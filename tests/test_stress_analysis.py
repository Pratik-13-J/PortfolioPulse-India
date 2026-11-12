"""Roadmap Phase 4 stress orchestration on a controlled synthetic saved dataset."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, import_candle_dataset, initialize, session, upsert_configured_portfolio,
)
from portfoliopulse.portfolio_analysis import analyze_saved_dataset, compute_phase4_analysis
from portfoliopulse.risk_analysis import compute_risk_analysis
from portfoliopulse.stress_analysis import ENGINE_VERSION, analyze_saved_dataset_stress, compute_stress_analysis
from portfoliopulse.stress_scenarios import UNCLASSIFIED, load_library
from scripts.analyze_stress import save_stress_report
from tests.test_phase3_risk_analysis import CONFIG, _synthetic

FAST = {"mc_paths": 10_000, "mc_batches": 20}


def _library(temp: Path) -> Path:
    """The shipped library plus two events inside the synthetic window."""
    library, _ = load_library()
    library["historical_events"] = library["historical_events"] + [
        {"event_id": "synthetic_window", "name": "Synthetic window", "start": "2025-03-03", "end": "2025-03-31",
         "window_basis": "test-only window on simulated data"},
    ]
    path = temp / "library.json"
    path.write_text(json.dumps(library), encoding="utf-8")
    return path


class StressAnalysisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PROJECT_ROOT / ".tmp").mkdir(exist_ok=True)
        cls._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        temp = Path(cls._temp.name)
        cls.db = temp / "stress.db"
        initialize(cls.db)
        upsert_configured_portfolio(CONFIG, path=cls.db)
        candles, metadata = _synthetic(320)
        cls.candles = candles
        cls.dataset_id = import_candle_dataset(candles, metadata=metadata, path=cls.db)
        cls.phase4 = analyze_saved_dataset(cls.dataset_id, db_path=cls.db)
        with session(cls.db) as con:
            cls.before = [tuple(r) for r in con.execute(
                "SELECT * FROM analysis_results ORDER BY analysis_id, metric_name")]
        cls.library_path = _library(temp)
        cls.result = analyze_saved_dataset_stress(cls.dataset_id, db_path=cls.db, library_path=cls.library_path,
                                                  **FAST)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_persisted_runs_link_to_new_engine_and_leave_old_rows(self):
        r = self.result
        self.assertEqual(len(r.scenario_run_ids), len(r.vectors))
        with session(self.db) as con:
            settings = json.loads(con.execute("SELECT settings_json FROM analysis_metadata WHERE analysis_id=?",
                                              (r.analysis_id,)).fetchone()[0])
            runs = con.execute("SELECT scenario_run_id, analysis_id, scenario_type, hypothetical, impact_inr "
                               "FROM scenario_runs ORDER BY scenario_run_id").fetchall()
            old = [tuple(x) for x in con.execute(
                "SELECT * FROM analysis_results WHERE analysis_id<>? ORDER BY analysis_id, metric_name",
                (r.analysis_id,))]
            sums = dict(con.execute("SELECT scenario_run_id, SUM(impact_inr) FROM scenario_contributions "
                                    "GROUP BY scenario_run_id").fetchall())
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(settings["engine_version"], ENGINE_VERSION)
        self.assertEqual(settings["verification_status"], "user_declared_unverified")
        self.assertEqual(len(settings["scenario_library_sha256"]), 64)
        self.assertEqual(old, self.before)
        self.assertTrue(all(run[1] == r.analysis_id and run[3] == 1 for run in runs))
        for run_id, _, _, _, impact in runs:
            self.assertAlmostEqual(sums[run_id], impact, places=7)
        self.assertIn("historical_replay", {run[2] for run in runs})
        self.assertIn("beta_market", {run[2] for run in runs})

    def test_pnl_recomputed_independently_from_candles(self):
        as_of = self.result.as_of_date
        closes = self.candles[self.candles["date"] == as_of].set_index("symbol")["close"]
        quantities = {item.symbol: item.quantity for item in CONFIG if not item.is_benchmark}
        for scenario_id, vector in self.result.vectors.items():
            expected = math.fsum(quantities[s] * closes[s] * vector.shocks[s] for s in quantities)
            self.assertAlmostEqual(self.result.impacts[scenario_id].impact_inr, expected, places=6, msg=scenario_id)
        value = math.fsum(quantities[s] * closes[s] for s in quantities)
        self.assertAlmostEqual(self.result.portfolio_value_inr, value, places=6)

    def test_historical_coverage_and_labels(self):
        statuses = {o.event_id: o.status for o in self.result.events}
        self.assertEqual(statuses["covid_crash_2020"], "unavailable")
        self.assertEqual(statuses["inflation_rate_shock_2022"], "unavailable")
        self.assertEqual(statuses["synthetic_window"], "replayed")
        self.assertIn("covid_crash_2020", self.result.unavailable)
        replay = self.result.vectors["synthetic_window"]
        prices = self.phase4.returns.price_grid
        self.assertAlmostEqual(replay.shocks["INFY"],
                               prices.at[pd.Timestamp("2025-03-31"), "INFY"] / prices.at[pd.Timestamp("2025-03-03"), "INFY"] - 1,
                               places=14)
        self.assertEqual(set(replay.basis), {"historical_observed"})
        self.assertEqual(set(self.result.vectors["nifty_minus_10_beta"].basis), {"beta_propagated"})
        self.assertEqual(set(self.result.vectors["broad_minus_10_direct"].basis), {"direct"})

    def test_risk_integration_and_comparison_matrix(self):
        row = self.result.comparison.loc["nifty_minus_20_beta"]
        var99 = self.result.integration["nifty_minus_20_beta"]["var_es_references"]["historical:c99"]["var_1d"]
        self.assertAlmostEqual(row["loss_to_historical_var_99"], -row["impact_pct"] / var99, places=12)
        betas = self.result.betas["beta"]
        weights = self.result.impacts["nifty_minus_20_beta"].holdings["weight"]
        self.assertAlmostEqual(row["impact_pct"], float((weights * betas * -0.2).sum()), places=14)
        self.assertAlmostEqual(row["beta_implied_portfolio_move"], row["impact_pct"], places=14)
        self.assertIn("stress_impact_inr:nifty_minus_20_beta", self.result.metrics)
        self.assertTrue(all(math.isfinite(v) for v, _ in self.result.metrics.values()))

    def test_rerun_is_idempotent(self):
        again = analyze_saved_dataset_stress(self.dataset_id, db_path=self.db, library_path=self.library_path, **FAST)
        self.assertEqual(again.analysis_id, self.result.analysis_id)
        self.assertEqual(again.scenario_run_ids, self.result.scenario_run_ids)
        with session(self.db) as con:
            count = con.execute("SELECT COUNT(*) FROM scenario_runs").fetchone()[0]
        self.assertEqual(count, len(self.result.vectors))

    def test_structured_report(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as output:
            paths = save_stress_report(self.result, Path(output))
            report = json.loads(paths["report_json"].read_text(encoding="utf-8"))
            holdings = pd.read_csv(paths["holdings_csv"])
        self.assertEqual(report["engine_version"], ENGINE_VERSION)
        self.assertIn("not forecasts", report["interpretation"])
        self.assertEqual(len(holdings), 5 * len(self.result.vectors))
        self.assertIn("covid_crash_2020", report["unavailable_scenarios"])

    def test_incomplete_sector_classification_and_reference_and_macro(self):
        info = get_dataset_info(self.dataset_id, path=self.db)
        phase4 = compute_phase4_analysis(self.dataset_id, info, get_candle_dataset(self.dataset_id, path=self.db))
        risk = compute_risk_analysis(phase4, verification_status="user_declared_unverified", **FAST)
        snapshot = [dict(row, sector="" if row["symbol"] == "ITC" else row["sector"])
                    for row in info["portfolio_snapshot"]]
        library, sha = load_library()
        reference = pd.Series([100.0, 70.0], index=pd.to_datetime(["2020-01-14", "2020-03-23"]))
        factors = pd.DataFrame({"rate_change_pp": phase4.returns.asset_daily_returns["INFY"] * 3.0,
                                "usdinr_change": phase4.returns.asset_daily_returns["ITC"] * -0.5})
        result = compute_stress_analysis(
            phase4, risk, snapshot, verification_status="user_declared_unverified", library=library,
            library_sha256=sha, benchmark_reference=reference, reference_label="test-only reference",
            macro_factors=factors, macro_label={"source_name": "test-only simulated factors"})
        self.assertEqual(result.holdings.at["ITC", "sector"], UNCLASSIFIED)
        self.assertTrue(any("ITC" in w for w in result.warnings))
        self.assertEqual(result.impacts["banking_minus_15_direct"].holdings.at["ITC", "shock"], 0.0)
        covid = next(o for o in result.events if o.event_id == "covid_crash_2020")
        self.assertEqual(covid.status, "model_propagated")
        self.assertAlmostEqual(result.vectors["covid_crash_2020"].shocks["RELIANCE"],
                               result.betas.at["RELIANCE", "beta"] * -0.30, places=14)
        self.assertEqual(result.sensitivity_basis, "estimated")
        self.assertEqual(result.vectors["rate_plus_100bp"].scenario_type, "macro_estimated")


if __name__ == "__main__":
    unittest.main()
