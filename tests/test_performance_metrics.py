"""Phase 2 relative performance metrics on hand-calculable fixtures.

Fixture (4 days): r_p = [0.01, -0.02, 0.03, 0.00], r_b = [0.02, -0.01, 0.01, -0.01]
  active a = [-0.01, -0.01, 0.02, 0.01]; mean(a) = 0.0025
  sum((a - mean)^2) = 6.75e-4 -> sample var 2.25e-4 -> std 0.015
  TE = 0.015 * sqrt(252); IR = 0.0025 * 252 / TE = sqrt(252) / 6
  mean p = 0.005, mean b = 0.0025; sum dev_p*dev_b = 6.5e-4; sum dev_b^2 = 6.75e-4
  beta = 6.5 / 6.75 = 26 / 27
  Sortino (rf = 0): mean 0.005; mean(min(r,0)^2) = 4e-4 / 4 = 1e-4 -> DD = 0.01
  Sortino = 0.005 * 252 / (0.01 * sqrt(252)) = 0.5 * sqrt(252)
"""

from __future__ import annotations

import json
import math
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import import_candle_dataset, initialize, upsert_configured_portfolio
from portfoliopulse.performance_metrics import (
    annualized_active_return, beta, capture_ratios, carino_factor, downside_beta, information_ratio,
    period_returns, relative_performance, rolling_relative_metrics, security_contributions, sortino_ratio,
    tracking_error,
)
from portfoliopulse.portfolio_analysis import ENGINE_VERSION, analyze_saved_dataset
from portfoliopulse.portfolio_returns import build_buy_and_hold_returns
from tests.test_phase4_analysis import CONFIG, _saved_fixture

DAYS = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"])
P = pd.Series([0.01, -0.02, 0.03, 0.00], index=DAYS)
B = pd.Series([0.02, -0.01, 0.01, -0.01], index=DAYS)
ROOT252 = math.sqrt(252)


class FormulaTests(unittest.TestCase):
    def test_tracking_error_and_information_ratio(self) -> None:
        self.assertAlmostEqual(tracking_error(P, B), 0.015 * ROOT252, places=14)
        self.assertAlmostEqual(annualized_active_return(P, B), 0.0025 * 252, places=14)
        self.assertAlmostEqual(information_ratio(P, B), ROOT252 / 6, places=12)
        active = (P - B).to_numpy()
        self.assertAlmostEqual(tracking_error(P, B), np.std(active, ddof=1) * ROOT252, places=14)

    def test_beta_matches_hand_value_and_numpy(self) -> None:
        self.assertAlmostEqual(beta(P, B), 26 / 27, places=14)
        cov = np.cov(P.to_numpy(), B.to_numpy(), ddof=1)
        self.assertAlmostEqual(beta(P, B), cov[0, 1] / cov[1, 1], places=14)

    def test_downside_beta(self) -> None:
        # Down days here have identical benchmark returns (-0.01, -0.01): undefined.
        self.assertIsNone(downside_beta(P, B))
        days = pd.to_datetime(["2026-02-02", "2026-02-03", "2026-02-04"])
        p = pd.Series([-0.02, -0.03, 0.05], index=days)
        b = pd.Series([-0.01, -0.03, 0.02], index=days)
        # Down days: p=[-0.02,-0.03], b=[-0.01,-0.03]; cov = 1e-4, var = 2e-4 -> 0.5.
        self.assertAlmostEqual(downside_beta(p, b), 0.5, places=14)

    def test_capture_ratios_are_geometric(self) -> None:
        up, down = capture_ratios(P, B)
        expected_up = (math.sqrt(1.01 * 1.03) - 1) / (math.sqrt(1.02 * 1.01) - 1)
        expected_down = (math.sqrt(0.98 * 1.00) - 1) / (math.sqrt(0.99 * 0.99) - 1)
        self.assertAlmostEqual(up, expected_up, places=12)
        self.assertAlmostEqual(down, expected_down, places=12)

    def test_sortino(self) -> None:
        self.assertAlmostEqual(sortino_ratio(P), 0.5 * ROOT252, places=12)
        rf = 0.06
        daily_rf = (1 + rf) ** (1 / 252) - 1
        excess = P.to_numpy() - daily_rf
        expected = excess.mean() * 252 / (math.sqrt(np.mean(np.minimum(excess, 0) ** 2)) * ROOT252)
        self.assertAlmostEqual(sortino_ratio(P, annual_risk_free_rate=rf), expected, places=12)

    def test_rolling_window_of_two(self) -> None:
        frame = rolling_relative_metrics(P, B, 2)
        last = frame.iloc[-1]
        self.assertAlmostEqual(last["tracking_error"], np.std([0.02, 0.01], ddof=1) * ROOT252, places=12)
        # Last two days: p=[0.03, 0], b=[0.01, -0.01]; cov = 3e-4, var = 2e-4 -> 1.5.
        self.assertAlmostEqual(last["beta"], 1.5, places=12)
        self.assertAlmostEqual(last["portfolio_return"], 0.03, places=14)
        self.assertAlmostEqual(last["benchmark_return"], 1.01 * 0.99 - 1, places=14)
        self.assertEqual(len(frame), 3)
        with self.assertRaises(ValueError):
            rolling_relative_metrics(P, B, 1)

    def test_undefined_cases_return_none(self) -> None:
        flat = pd.Series([0.01, 0.01, 0.01], index=DAYS[:3])
        self.assertEqual(tracking_error(flat, flat), 0.0)
        self.assertIsNone(information_ratio(flat, flat))
        self.assertIsNone(beta(P.iloc[:3], flat))
        self.assertEqual(capture_ratios(flat, flat), (1.0, None))
        self.assertIsNone(sortino_ratio(flat))
        with self.assertRaises(ValueError):
            tracking_error(P, B.iloc[:3])  # misaligned dates are refused, never re-aligned

    def test_period_returns_use_last_observed_anchor(self) -> None:
        index = pd.to_datetime(["2025-12-31", "2026-01-30", "2026-02-27", "2026-03-31"])
        levels = pd.DataFrame({"portfolio": [100.0, 110.0, 121.0, 133.1],
                               "benchmark": [200.0, 200.0, 210.0, 220.5]}, index=index)
        result = period_returns(levels)
        self.assertEqual(result["1m"]["anchor_date"], "2026-02-27")  # target 2026-02-28 is not observed
        self.assertAlmostEqual(result["1m"]["portfolio"], 0.1, places=12)
        self.assertAlmostEqual(result["1m"]["benchmark"], 0.05, places=12)
        self.assertAlmostEqual(result["3m"]["portfolio"], 0.331, places=12)
        self.assertEqual(result["ytd"]["anchor_date"], "2025-12-31")
        self.assertAlmostEqual(result["ytd"]["benchmark"], 0.1025, places=12)
        self.assertIsNone(result["6m"])
        self.assertIsNone(result["1y"])


class ContributionTests(unittest.TestCase):
    # H_A = [100, 110, 99], H_B = [100, 90, 108]; V = [200, 200, 207]
    # day 1: c = [+0.05, -0.05], r = 0; day 2: c = [-0.055, +0.09], r = 0.035
    VALUES = pd.DataFrame({"A": [100.0, 110.0, 99.0], "B": [100.0, 90.0, 108.0]},
                          index=pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04"]))

    def test_hand_calculated_carino_linking(self) -> None:
        result = security_contributions(self.VALUES)
        np.testing.assert_allclose(result.daily.to_numpy(), [[0.05, -0.05], [-0.055, 0.09]], rtol=0, atol=1e-15)
        self.assertAlmostEqual(result.total_return, 0.035, places=15)
        self.assertAlmostEqual(result.endpoint_return, 0.035, places=15)
        scale = 0.035 / math.log(1.035)  # k_1 / K with k_1 = 1 at r = 0; k_2 / K = 1
        self.assertAlmostEqual(result.summary.at["A", "carino_linked"], 0.05 * scale - 0.055, places=14)
        self.assertAlmostEqual(result.summary.at["B", "carino_linked"], -0.05 * scale + 0.09, places=14)
        self.assertAlmostEqual(result.summary.at["A", "arithmetic_sum"], -0.005, places=15)
        self.assertLess(result.reconciliation_error, 1e-12)
        self.assertEqual(carino_factor(0.0), 1.0)

    def test_matches_lagged_weight_times_return_from_buy_and_hold(self) -> None:
        prices = pd.DataFrame({"A": [10.0, 11.0, 9.9, 10.5], "B": [20.0, 18.0, 21.6, 21.0],
                               "BENCH": [100.0, 101.0, 99.0, 100.0]},
                              index=pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]))
        returns = build_buy_and_hold_returns(prices, {"A": 10.0, "B": 5.0}, "BENCH")
        result = security_contributions(returns.holding_values_inr)
        pd.testing.assert_frame_equal(result.daily, returns.daily_return_contributions, rtol=0, atol=1e-15)
        cumulative = returns.portfolio_value_inr.iloc[-1] / returns.portfolio_value_inr.iloc[0] - 1
        self.assertAlmostEqual(result.summary["carino_linked"].sum(), cumulative, places=14)

    def test_gap_transitions_are_excluded_and_disclosed(self) -> None:
        values = self.VALUES.copy()
        values.loc["2026-03-03", "B"] = np.nan
        with self.assertRaises(ValueError):
            security_contributions(values)  # no complete consecutive transition remains
        extended = pd.concat([values, pd.DataFrame({"A": [100.0], "B": [110.0]},
                                                   index=pd.to_datetime(["2026-03-05"]))])
        result = security_contributions(extended)
        self.assertEqual(result.excluded_transitions, ("2026-03-03", "2026-03-04"))
        self.assertEqual(len(result.daily), 1)


class IntegrationTests(unittest.TestCase):
    def test_relative_performance_reports_reasons_for_short_history(self) -> None:
        levels = pd.DataFrame({"portfolio": [100.0, 101.0, 99.0, 102.0, 102.0],
                               "benchmark": [10.0, 10.2, 10.1, 10.2, 10.1]},
                              index=pd.to_datetime(["2025-12-31", *DAYS.strftime("%Y-%m-%d")]))
        holdings = levels[["portfolio"]].rename(columns={"portfolio": "A"})
        result = relative_performance(P, B, levels=levels, holding_values_inr=holdings)
        self.assertIn("rolling_63d_tracking_error_latest", result.undefined)
        self.assertIsNone(result.metrics["portfolio_downside_beta"])
        self.assertAlmostEqual(result.metrics["portfolio_beta"], 26 / 27, places=14)
        self.assertAlmostEqual(result.metrics["contribution_carino:A"], 0.02, places=14)

    def test_saved_analysis_persists_new_keys_under_new_engine_version(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "phase2.db"
            initialize(db)
            upsert_configured_portfolio(CONFIG, path=db)
            candles, metadata = _saved_fixture()
            dataset_id = import_candle_dataset(candles, metadata=metadata, path=db)
            result = analyze_saved_dataset(dataset_id, annual_risk_free_rate=0.06, db_path=db)
            self.assertEqual(len(result.metrics), 20)  # Phase 4 keys unchanged
            perf = result.performance
            # Every price moves by the same factor, so active returns are zero.
            self.assertEqual(perf.metrics["portfolio_tracking_error_annualized"], 0.0)
            self.assertIsNone(perf.metrics["portfolio_information_ratio"])
            self.assertAlmostEqual(perf.metrics["portfolio_beta"], 1.0, places=12)
            self.assertAlmostEqual(perf.metrics["contribution_total_compounded_return"],
                                   result.metrics["portfolio_cumulative_price_return"], places=14)
            with closing(sqlite3.connect(db)) as con:
                settings = json.loads(con.execute("SELECT settings_json FROM analysis_metadata").fetchone()[0])
                quality = json.loads(con.execute("SELECT quality_json FROM analysis_metadata").fetchone()[0])
                names = {row[0] for row in con.execute("SELECT metric_name FROM analysis_results")}
            self.assertEqual(settings["engine_version"], ENGINE_VERSION)
            self.assertEqual(settings["comparison_label"], "price return vs NIFTY 50 price index (not TRI)")
            self.assertTrue({"portfolio_sharpe", "portfolio_var_1d", "portfolio_beta",
                             "contribution_carino:RELIANCE"} <= names)
            self.assertNotIn("portfolio_information_ratio", names)  # undefined -> not stored
            self.assertIn("portfolio_information_ratio", quality["undefined_metrics"])
            self.assertEqual(quality["corporate_action_jump_warnings"], [])


if __name__ == "__main__":
    unittest.main()
