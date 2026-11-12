"""Phase 3 risk-engine orchestration on a synthetic saved dataset."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from portfoliopulse.angelone.instrument_master import load_config
from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import import_candle_dataset, initialize, session, upsert_configured_portfolio
from portfoliopulse.portfolio_analysis import ENGINE_VERSION as PHASE4_ENGINE, analyze_saved_dataset
from portfoliopulse.risk_analysis import ENGINE_VERSION, analyze_saved_dataset_risk
from scripts.analyze_risk import save_risk_report

CONFIG = load_config(PROJECT_ROOT / "config" / "instruments.json")
RETRIEVED = "2026-09-08T08:00:00+00:00"


def _synthetic(days: int) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(99)
    dates = pd.bdate_range("2025-01-01", periods=days)
    market = rng.normal(0.0003, 0.01, days)
    rows = []
    for position, item in enumerate(CONFIG):
        idio = rng.normal(0, 0.008, days) if not item.is_benchmark else np.zeros(days)
        path = (100.0 + 10 * position) * np.cumprod(1 + market + idio)
        for day, close in zip(dates, path):
            close = round(float(close), 4)
            rows.append({
                "symbol": item.symbol, "trading_symbol": item.trading_symbol, "exchange": item.exchange,
                "date": day.strftime("%Y-%m-%d"), "open": close, "high": close, "low": close, "close": close,
                "volume": 0 if item.is_benchmark else 1000, "source": "Synthetic risk fixture",
                "price_basis": "unadjusted_ohlcv", "provenance_kind": "user_supplied_csv",
                "interval": "ONE_DAY", "retrieved_at_utc": RETRIEVED,
            })
    metadata = {
        "source_name": "Synthetic risk fixture", "source_url": "https://example.org/synthetic",
        "provenance_kind": "user_supplied_csv", "provenance_status": "user_declared_unverified",
        "retrieved_at_utc": RETRIEVED, "price_basis": "unadjusted_ohlcv", "interval": "ONE_DAY",
        "start": dates[0].strftime("%Y-%m-%d"),
        "end_exclusive": (dates[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    return pd.DataFrame(rows), metadata


class RiskAnalysisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PROJECT_ROOT / ".tmp").mkdir(exist_ok=True)
        cls._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        cls.db = Path(cls._temp.name) / "phase3.db"
        initialize(cls.db)
        upsert_configured_portfolio(CONFIG, path=cls.db)
        candles, metadata = _synthetic(320)
        cls.dataset_id = import_candle_dataset(candles, metadata=metadata, path=cls.db)
        cls.phase4 = analyze_saved_dataset(cls.dataset_id, annual_risk_free_rate=0.06, db_path=cls.db)
        with session(cls.db) as con:
            cls.phase4_rows = con.execute(
                "SELECT metric_name, value, unit FROM analysis_results WHERE analysis_id=? ORDER BY metric_name",
                (cls.phase4.analysis_id,)).fetchall()
        cls.risk = analyze_saved_dataset_risk(cls.dataset_id, db_path=cls.db, mc_paths=50_000, mc_batches=50)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def _row(self, method, level, series="portfolio"):
        table = self.risk.var_table
        match = table[(table["method"] == method) & (table["confidence"] == level) & (table["series"] == series)]
        self.assertEqual(len(match), 1)
        return match.iloc[0]

    def test_separate_run_leaves_phase4_rows_and_keys_unchanged(self):
        self.assertNotEqual(self.risk.analysis_id, self.phase4.analysis_id)
        self.assertEqual(len(self.phase4.metrics), 20)
        with session(self.db) as con:
            after = con.execute(
                "SELECT metric_name, value, unit FROM analysis_results WHERE analysis_id=? ORDER BY metric_name",
                (self.phase4.analysis_id,)).fetchall()
            settings = json.loads(con.execute("SELECT settings_json FROM analysis_metadata WHERE analysis_id=?",
                                              (self.risk.analysis_id,)).fetchone()[0])
            names = {r[0] for r in con.execute("SELECT metric_name FROM analysis_results WHERE analysis_id=?",
                                               (self.risk.analysis_id,))}
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual([tuple(r) for r in after], [tuple(r) for r in self.phase4_rows])
        self.assertEqual(settings["engine_version"], ENGINE_VERSION)
        self.assertNotEqual(ENGINE_VERSION, PHASE4_ENGINE)
        self.assertEqual(settings["verification_status"], "user_declared_unverified")
        self.assertEqual(settings["source_name"], "Synthetic risk fixture")
        self.assertIn("not TRI", settings["comparison_label"])
        self.assertEqual(settings["monte_carlo"], {"seed": 20261112, "paths": 50_000, "batches": 50})
        self.assertFalse(names & {r[0] for r in self.phase4_rows})
        self.assertEqual(names, set(self.risk.metrics))

    def test_rerun_is_idempotent(self):
        again = analyze_saved_dataset_risk(self.dataset_id, db_path=self.db, mc_paths=50_000, mc_batches=50)
        self.assertEqual(again.analysis_id, self.risk.analysis_id)
        self.assertEqual(again.metrics, self.risk.metrics)

    def test_historical_matches_phase4_and_tail_rows(self):
        n = self.risk.observations
        self.assertEqual(n, 319)
        self.assertEqual(self._row("historical", 0.95)["var_1d"], self.phase4.metrics["portfolio_var_1d"])
        self.assertEqual(self._row("historical", 0.95)["es_1d"], self.phase4.metrics["portfolio_expected_shortfall_1d"])
        self.assertEqual(len(self.risk.tail_observations["portfolio:c99"]), math.ceil(0.01 * n))
        self.assertEqual(len(self.risk.quality["tail_observations"]["portfolio:c99"]), 4)
        self.assertTrue(self.risk.sample_adequate_for_tails)

    def test_monte_carlo_within_standard_error_of_delta_normal(self):
        for level in (0.95, 0.99):
            mc = self._row("monte_carlo_multivariate_normal", level)
            closed = self._row("delta_normal_sample", level)
            self.assertLess(abs(mc["var_1d"] - closed["var_1d"]), 3 * mc["var_standard_error"])
            self.assertLess(abs(mc["es_1d"] - closed["es_1d"]), 3 * mc["es_standard_error"])

    def test_components_reconcile_to_totals(self):
        for estimator in ("sample", "ledoit_wolf", "ewma"):
            for level in (0.95, 0.99):
                frame = self.risk.components[f"delta_normal_{estimator}:c{level * 100:g}"]
                total = self._row(f"delta_normal_{estimator}", level)
                self.assertAlmostEqual(frame["component_var"].sum(), total["var_1d"], places=14)
                self.assertAlmostEqual(frame["component_es"].sum(), total["es_1d"], places=14)
                self.assertAlmostEqual(frame["component_var_inr"].sum(), total["var_1d_inr"], places=7)
        for level in (0.95, 0.99):
            frame = self.risk.components[f"monte_carlo_bootstrap:c{level * 100:g}"]
            self.assertAlmostEqual(frame["component_es"].sum(), self._row("monte_carlo_bootstrap", level)["es_1d"],
                                   places=14)
            self.assertAlmostEqual(frame["weight"].sum(), 1.0, places=14)

    def test_backtests_drawdowns_and_metrics(self):
        result = self.risk.backtests["historical:c99"]
        self.assertEqual(result["observations"], 319 - 250)
        self.assertEqual(len(result["exception_dates"]), result["exceptions"])
        self.assertEqual(self.risk.drawdowns["portfolio"]["max_drawdown"],
                         self.phase4.metrics["portfolio_max_drawdown"])
        self.assertEqual(self.risk.drawdowns["benchmark_matched"]["max_drawdown"],
                         self.phase4.metrics["benchmark_matched_max_drawdown"])
        self.assertTrue(all(math.isfinite(value) for value, _ in self.risk.metrics.values()))
        self.assertIn("var_1d:cornish_fisher:c99", self.risk.metrics)
        self.assertIn("benchmark_es_1d:normal:c95", self.risk.metrics)
        self.assertIn("backtest_kupiec_p_value:normal:c99", self.risk.metrics)
        self.assertIn("drawdown_max_recovery_observations:portfolio", self.risk.metrics)
        self.assertEqual(self.risk.metrics["var_1d:normal:c99"][1], "daily_ratio")

    def test_report_is_labelled(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as output:
            path = save_risk_report(self.risk, Path(output))
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["engine_version"], ENGINE_VERSION)
        self.assertEqual(report["verification_status"], "user_declared_unverified")
        self.assertEqual(report["valid_return_observations"], 319)
        self.assertEqual(len(report["tail_observations"]["portfolio:c99"]), 4)


class SmallSampleTests(unittest.TestCase):
    def test_short_sample_flags_tails_and_skips_backtests(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "small.db"
            initialize(db)
            upsert_configured_portfolio(CONFIG, path=db)
            candles, metadata = _synthetic(12)
            dataset_id = import_candle_dataset(candles, metadata=metadata, path=db)
            risk = analyze_saved_dataset_risk(dataset_id, db_path=db, save=False, mc_paths=10_000, mc_batches=20)
            with session(db) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_metadata").fetchone()[0], 0)
        self.assertFalse(risk.sample_adequate_for_tails)
        self.assertEqual(risk.quality["tail_statistics_reliability"], "insufficient_sample_smoke_test_only")
        self.assertEqual(risk.backtests, {})
        self.assertIn("backtest:historical:c99", risk.undefined)


if __name__ == "__main__":
    unittest.main()
