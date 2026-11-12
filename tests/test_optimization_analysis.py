"""Roadmap Phase 5 orchestration on a controlled synthetic saved dataset."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import import_candle_dataset, initialize, session, upsert_configured_portfolio
from portfoliopulse.migrations import LATEST_VERSION, schema_version
from portfoliopulse.optimization_analysis import (
    ENGINE_VERSION, InsufficientDataError, analyze_saved_dataset_optimization, load_config,
)
from scripts.analyze_optimization import save_optimization_report
from tests.test_phase3_risk_analysis import CONFIG, _synthetic


def _config(temp: Path, **changes) -> Path:
    config, _ = load_config()
    config.update(changes)
    path = temp / "optimization.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class OptimizationAnalysisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PROJECT_ROOT / ".tmp").mkdir(exist_ok=True)
        cls._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        temp = Path(cls._temp.name)
        cls.db = temp / "opt.db"
        initialize(cls.db)
        upsert_configured_portfolio(CONFIG, path=cls.db)
        candles, metadata = _synthetic(320)
        cls.dataset_id = import_candle_dataset(candles, metadata=metadata, path=cls.db)
        with session(cls.db) as con:
            cls.holdings_before = [tuple(r) for r in con.execute("SELECT * FROM dataset_holdings ORDER BY 1, 2")]
        # Shorter walk-forward window so the 319-return synthetic sample is out-of-sample testable.
        cls.config_path = _config(temp, minimum_observations=200, investment_amount_inr=500000,
                                  walk_forward={"estimation_observations": 200, "holding_observations": 21},
                                  annual_risk_free_rate=0.0)
        cls.result = analyze_saved_dataset_optimization(cls.dataset_id, db_path=cls.db, config_path=cls.config_path)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_schema_v4_runs_and_holdings_untouched(self):
        self.assertEqual(schema_version(self.db), LATEST_VERSION)
        self.assertEqual(LATEST_VERSION, 4)
        with session(self.db) as con:
            holdings = [tuple(r) for r in con.execute("SELECT * FROM dataset_holdings ORDER BY 1, 2")]
            runs = con.execute("SELECT strategy, status, illustrative FROM optimization_runs").fetchall()
            settings = json.loads(con.execute("SELECT settings_json FROM analysis_metadata WHERE analysis_id=?",
                                              (self.result.analysis_id,)).fetchone()[0])
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(holdings, self.holdings_before)
        self.assertEqual({r[0] for r in runs}, set(self.result.results))
        self.assertTrue(all(r[2] == 1 for r in runs))
        self.assertEqual(settings["engine_version"], ENGINE_VERSION)
        self.assertEqual(settings["verification_status"], "user_declared_unverified")

    def test_weights_feasible_and_sum_to_one(self):
        constraints = self.result.settings["constraints"]
        for name, res in self.result.results.items():
            if res.weights is None or name in ("existing", "equal_weight"):
                continue
            w = res.weights
            self.assertAlmostEqual(float(w.sum()), 1.0, places=9, msg=name)
            self.assertGreaterEqual(float(w.min()), constraints["min_weight"] - 1e-9)
            self.assertLessEqual(float(w.max()), constraints["max_weight"] + 1e-9)
            self.assertLessEqual(float(w[["HDFCBANK", "ICICIBANK"]].sum()), 0.40 + 1e-9)
        # Existing synthetic weights put ITC above the 35% cap, so any feasible portfolio
        # needs gross turnover >= 2 x the excess; with a 20% limit it must be infeasible.
        current = self.result.results["existing"].weights
        excess = float((current - constraints["max_weight"]).clip(lower=0).sum())
        self.assertGreater(2 * excess, 0.20)
        limited = self.result.results["min_variance_turnover_limited"]
        self.assertEqual(limited.status, "infeasible")
        self.assertIsNone(limited.weights)

    def test_risk_reconciles_and_allocation_within_budget(self):
        for name, ev in self.result.evaluations.items():
            for key, value in ev["reconciliation"].items():
                self.assertAlmostEqual(value, 1.0 if key == "weights_sum" else 0.0, places=10, msg=(name, key))
        for name, alloc in self.result.allocations.items():
            cash = alloc["new_money"]
            self.assertGreaterEqual(cash.residual_cash_inr, 0)
            self.assertAlmostEqual(cash.invested_inr + cash.transaction_cost_inr + cash.residual_cash_inr, 500000,
                                   places=6)
            self.assertGreaterEqual(alloc["rebalance_existing"].residual_cash_inr, -1e-9)

    def test_walk_forward_and_persisted_backtests(self):
        wf = self.result.walk_forward
        self.assertEqual(wf.status, "completed")
        self.assertEqual(len(wf.net), 319 - 200)
        np.testing.assert_allclose(wf.net["existing"].to_numpy(),
                                   self.result.walk_forward.gross["existing"].to_numpy())
        with session(self.db) as con:
            count = con.execute("""SELECT COUNT(*) FROM optimization_backtests b JOIN optimization_runs r
                                   USING(optimization_run_id) WHERE r.strategy='min_variance'""").fetchone()[0]
        self.assertEqual(count, 119)

    def test_rerun_is_idempotent_and_report(self):
        again = analyze_saved_dataset_optimization(self.dataset_id, db_path=self.db, config_path=self.config_path)
        self.assertEqual(again.analysis_id, self.result.analysis_id)
        self.assertEqual(again.run_ids, self.result.run_ids)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as output:
            paths = save_optimization_report(again, Path(output))
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            frontier = pd.read_csv(paths["frontier"])
        self.assertIn("not investment advice", report["label"])
        self.assertEqual(len(frontier), 25)
        self.assertTrue(report["tradeoffs"][0].startswith("Universe: 5 stocks in 4 sectors"))


class InsufficientDataTests(unittest.TestCase):
    def test_short_dataset_is_refused_and_writes_nothing(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "short.db"
            initialize(db)
            upsert_configured_portfolio(CONFIG, path=db)
            candles, metadata = _synthetic(12)
            dataset_id = import_candle_dataset(candles, metadata=metadata, path=db)
            with self.assertRaises(InsufficientDataError) as ctx:
                analyze_saved_dataset_optimization(dataset_id, db_path=db)
            self.assertIn("11 valid daily returns", str(ctx.exception))
            with session(db) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_metadata").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()


class ConfigHashTests(unittest.TestCase):
    def test_config_and_library_hashes_ignore_line_endings(self):
        import hashlib
        from portfoliopulse.stress_scenarios import load_library
        for loader, name in ((load_config, "optimization.json"), (load_library, "stress_scenarios.json")):
            source = (PROJECT_ROOT / "config" / name).read_bytes().replace(b"\r\n", b"\n")
            with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
                lf, crlf = Path(temp) / "lf.json", Path(temp) / "crlf.json"
                lf.write_bytes(source)
                crlf.write_bytes(source.replace(b"\n", b"\r\n"))
                self.assertEqual(loader(lf)[1], loader(crlf)[1], name)
                self.assertEqual(loader(lf)[1], hashlib.sha256(source).hexdigest(), name)
