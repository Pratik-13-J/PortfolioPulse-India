"""Roadmap Phase 4 stress units: hand fixtures and independent numpy/scipy checks.

Synthetic return series below are simulated, test-only data.
"""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from scipy import stats

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.factor_sensitivities import load_macro_factor_csv, macro_sensitivities, market_betas, ols
from portfoliopulse.historical_stress import data_identified_events, replay_event
from portfoliopulse.performance_metrics import beta as performance_beta
from portfoliopulse.scenarios import scenario_shocks
from portfoliopulse.stress_impact import risk_integration, stress_impact
from portfoliopulse import stress_scenarios as ss

DATES = pd.bdate_range("2025-01-01", periods=300)


def holdings_frame(sectors=("Banking", "Information Technology"), quantities=(10.0, 20.0)):
    return pd.DataFrame({"sector": list(sectors), "quantity": list(quantities)}, index=["A", "B"])


class FactorSensitivityTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.market = pd.Series(rng.normal(0, 0.01, 300), index=DATES)
        self.assets = pd.DataFrame({
            "A": 1.3 * self.market + rng.normal(0, 0.005, 300),
            "B": 0.6 * self.market + rng.normal(0, 0.008, 300),
        }, index=DATES)

    def test_exact_line_hand_example(self):
        x = pd.DataFrame({"f": [0.0, 1.0, 2.0, 3.0]})
        fit = ols(pd.Series([1.0, 3.0, 5.0, 7.0]), x)  # y = 1 + 2x exactly
        self.assertAlmostEqual(fit.coefficients["alpha"], 1.0, places=12)
        self.assertAlmostEqual(fit.coefficients["f"], 2.0, places=12)
        self.assertAlmostEqual(fit.r_squared, 1.0, places=12)

    def test_market_beta_matches_existing_estimator_and_scipy(self):
        betas = market_betas(self.assets, self.market)
        for symbol in ("A", "B"):
            self.assertAlmostEqual(betas.at[symbol, "beta"], performance_beta(self.assets[symbol], self.market), places=12)
            reg = stats.linregress(self.market, self.assets[symbol])
            self.assertAlmostEqual(betas.at[symbol, "beta_standard_error"], reg.stderr, places=12)
            self.assertAlmostEqual(betas.at[symbol, "r_squared"], reg.rvalue ** 2, places=12)
        # Simulated known betas are recovered within 3 standard errors.
        self.assertLess(abs(betas.at["A", "beta"] - 1.3), 3 * betas.at["A", "beta_standard_error"])
        self.assertLess(abs(betas.at["B", "beta"] - 0.6), 3 * betas.at["B", "beta_standard_error"])

    def test_missing_rows_are_excluded_not_filled(self):
        y = self.assets["A"].copy()
        y.iloc[[3, 7]] = np.nan
        fit = ols(y, self.market.rename("m").to_frame())
        self.assertEqual((fit.observations, fit.excluded_rows), (298, 2))

    def test_macro_regression_recovers_simulated_sensitivities(self):
        rng = np.random.default_rng(2)
        factors = pd.DataFrame({"rate_change_pp": rng.normal(0, 0.05, 300),
                                "usdinr_change": rng.normal(0, 0.003, 300)}, index=DATES)
        assets = pd.DataFrame({"A": 1.0 * self.market - 0.04 * factors["rate_change_pp"]
                               + 0.4 * factors["usdinr_change"] + rng.normal(0, 0.002, 300)}, index=DATES)
        long = macro_sensitivities(assets, self.market, factors).set_index("factor")
        for factor, truth in (("market", 1.0), ("rate_change_pp", -0.04), ("usdinr_change", 0.4)):
            self.assertLess(abs(long.at[factor, "sensitivity"] - truth), 4 * long.at[factor, "standard_error"])
        design = np.column_stack([np.ones(300), self.market, factors])
        coef = np.linalg.lstsq(design, assets["A"].to_numpy(), rcond=None)[0]
        self.assertAlmostEqual(long.at["usdinr_change", "sensitivity"], coef[3], places=10)

    def test_collinear_regressors_are_rejected(self):
        with self.assertRaises(ValueError):
            ols(self.assets["A"], pd.DataFrame({"m": self.market, "m2": 2 * self.market}))

    def test_macro_factor_csv_with_sidecar(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            path = Path(temp) / "macro.csv"
            path.write_text("date,india_10y_yield_pct,usdinr\n2025-01-01,7.00,85.0\n"
                            "2025-01-02,7.10,85.85\n2025-01-03,7.05,85.85\n", encoding="utf-8")
            sidecar = {"source_name": "Test fixture", "source_url": "https://example.org/macro",
                       "retrieved_at_utc": "2025-01-04T00:00:00+00:00", "provenance_label": "test_fixture",
                       "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            path.with_suffix(".meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
            changes, meta = load_macro_factor_csv(path)
        self.assertEqual(len(changes), 2)
        self.assertAlmostEqual(changes["rate_change_pp"].iloc[0], 0.10, places=12)
        self.assertAlmostEqual(changes["usdinr_change"].iloc[0], 0.01, places=12)  # 85 -> 85.85
        self.assertEqual(meta["verification_status"], "user_declared_unverified")


class ShockVectorTests(unittest.TestCase):
    def test_beta_propagation_hand_example_with_sector_overlay(self):
        holdings = holdings_frame()
        betas = pd.Series({"A": 1.2, "B": 0.5})
        vector = ss.beta_vector({"scenario_id": "x", "name": "x", "market_move": -0.10,
                                 "sector_shocks": {"Banking": -0.05}}, holdings, betas)
        self.assertAlmostEqual(vector.shocks["A"], -0.12 - 0.05, places=15)
        self.assertAlmostEqual(vector.shocks["B"], -0.05, places=15)
        self.assertEqual(vector.basis.to_dict(), {"A": "direct+beta_propagated", "B": "beta_propagated"})
        self.assertEqual(vector.benchmark_move, -0.10)
        with self.assertRaises(ValueError):
            ss.beta_vector({"scenario_id": "x", "name": "x", "market_move": -0.9}, holdings, pd.Series({"A": 1.2, "B": 1}))
        with self.assertRaises(ValueError):
            ss.beta_vector({"scenario_id": "x", "name": "x", "market_move": -0.1, "sector_shocks": {"Pharma": -0.1}},
                           holdings, betas)

    def test_conditional_moves_hand_example_and_beta_equivalence(self):
        cov = pd.DataFrame([[4e-4, 2e-4], [2e-4, 9e-4]], index=["A", "B"], columns=["A", "B"])
        self.assertAlmostEqual(ss.conditional_moves(cov, {"A": -0.10})["B"], -0.05, places=15)  # 2/4 * -0.1
        rng = np.random.default_rng(4)
        market = pd.Series(rng.normal(0, 0.01, 300), index=DATES)
        assets = pd.DataFrame({"A": 1.1 * market + rng.normal(0, 0.006, 300)}, index=DATES)
        joint = pd.concat([assets, market.rename("NIFTY 50")], axis=1)
        moves = ss.conditional_moves(joint.cov(ddof=1), {"NIFTY 50": -0.1})
        self.assertAlmostEqual(moves["A"], market_betas(assets, market).at["A", "beta"] * -0.1, places=12)

    def test_covariance_vector_labels_direct_and_propagated(self):
        holdings = holdings_frame()
        cov = pd.DataFrame([[4e-4, 2e-4, 1e-4], [2e-4, 9e-4, 3e-4], [1e-4, 3e-4, 2e-4]],
                           index=["A", "B", "NIFTY 50"], columns=["A", "B", "NIFTY 50"])
        vector = ss.covariance_vector({"scenario_id": "c", "name": "c", "individual_shocks": {"A": -0.2}},
                                      holdings, cov, benchmark="NIFTY 50")
        self.assertEqual(vector.components.at["A", "direct"], -0.2)
        self.assertAlmostEqual(vector.components.at["B", "covariance_propagated"], -0.1, places=15)
        self.assertAlmostEqual(vector.benchmark_move, -0.05, places=15)
        self.assertEqual(vector.basis["B"], "covariance_propagated")

    def test_direct_vector_reuses_existing_presets(self):
        holdings = holdings_frame()
        vector = ss.direct_vector({"scenario_id": "d", "name": "d", "scenario_type": "sector",
                                   "sector_shocks": {"Banking": -0.15}}, holdings)
        expected = scenario_shocks([{"symbol": "A", "sector": "Banking"},
                                    {"symbol": "B", "sector": "Information Technology"}],
                                   "sector", sector_shocks={"Banking": -0.15})
        self.assertEqual(vector.shocks.to_dict(), expected)
        self.assertEqual(vector.basis.to_dict(), {"A": "direct", "B": "unshocked"})

    def test_missing_sector_is_unclassified_with_warning(self):
        frame, warnings = ss.normalize_holdings([
            {"symbol": "A", "sector": "Banking", "quantity": 1, "is_benchmark": 0},
            {"symbol": "B", "sector": "", "quantity": 2, "is_benchmark": 0},
            {"symbol": "C", "sector": None, "quantity": 3, "is_benchmark": 0},
            {"symbol": "NIFTY 50", "sector": "Broad Market", "quantity": 0, "is_benchmark": 1},
        ])
        self.assertEqual(frame["sector"].to_dict(), {"A": "Banking", "B": ss.UNCLASSIFIED, "C": ss.UNCLASSIFIED})
        self.assertEqual(len(warnings), 2)
        library, _ = ss.load_library()
        sens, sens_warnings = ss.assumed_sensitivities(library, frame)
        self.assertEqual(sens.at["B", "rate_change_pp"], 0.0)
        self.assertEqual(len(sens_warnings), 2)

    def test_macro_vector_hand_example(self):
        holdings = holdings_frame()
        sens = pd.DataFrame({"rate_change_pp": [-0.04, -0.01], "usdinr_change": [-0.10, 0.40]}, index=["A", "B"])
        vector = ss.macro_vector({"scenario_id": "m", "name": "m",
                                  "factor_moves": {"rate_change_pp": 1.0, "usdinr_change": 0.05}},
                                 holdings, sens, basis="assumed")
        self.assertAlmostEqual(vector.shocks["A"], -0.04 - 0.005, places=15)
        self.assertAlmostEqual(vector.shocks["B"], -0.01 + 0.02, places=15)
        self.assertEqual(vector.scenario_type, "macro_assumed")

    def test_library_validation(self):
        library, digest = ss.load_library()
        self.assertEqual(len(digest), 64)
        self.assertIn("covid_crash_2020", [e["event_id"] for e in library["historical_events"]])
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            broken = dict(library)
            broken["hypothetical_scenarios"] = library["hypothetical_scenarios"] + [library["hypothetical_scenarios"][0]]
            path = Path(temp) / "lib.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(ValueError):
                ss.load_library(path)


class ImpactTests(unittest.TestCase):
    def test_hand_pnl_shares_sectors_and_weights(self):
        # Values A = 10 x 100 = 1000, B = 20 x 150 = 3000; shocks -10% and +5%.
        values = pd.Series({"A": 10 * 100.0, "B": 20 * 150.0})
        vector = ss.make_vector("h", "h", "t", "hypothetical", {"direct": {"A": -0.10, "B": 0.05}}, ["A", "B"])
        impact = stress_impact(values, pd.Series({"A": "Banking", "B": "IT"}), vector)
        self.assertAlmostEqual(impact.holdings.at["A", "impact_inr"], -100.0, places=10)
        self.assertAlmostEqual(impact.holdings.at["B", "impact_inr"], 150.0, places=10)
        self.assertAlmostEqual(impact.impact_inr, 50.0, places=10)
        self.assertAlmostEqual(impact.impact_pct, 50 / 4000, places=15)
        self.assertAlmostEqual(impact.holdings["loss_share"].sum(), 1.0, places=14)
        self.assertAlmostEqual(impact.holdings.at["A", "loss_share"], -2.0, places=14)
        self.assertAlmostEqual(impact.holdings.at["A", "stressed_weight"], 900 / 4050, places=15)
        self.assertEqual(impact.worst_holdings[0], "A")
        self.assertAlmostEqual(impact.sectors.at["Banking", "sector_return"], -0.10, places=15)
        self.assertAlmostEqual(impact.sectors["portfolio_impact_pct"].sum(), impact.impact_pct, places=15)

    def test_risk_integration_ratios(self):
        rng = np.random.default_rng(8)
        assets = pd.DataFrame(rng.normal(0, 0.01, (300, 2)), index=DATES, columns=["A", "B"])
        values = pd.Series({"A": 1000.0, "B": 3000.0})
        vector = ss.make_vector("h", "h", "t", "hypothetical", {"direct": {"A": -0.2, "B": -0.1}}, ["A", "B"])
        impact = stress_impact(values, pd.Series({"A": "X", "B": "Y"}), vector)
        table = pd.DataFrame([{"series": "portfolio", "method": "historical", "confidence": 0.99,
                               "var_1d": 0.025, "es_1d": 0.03}])
        result = risk_integration(impact, table, max_drawdown=-0.25, asset_returns=assets,
                                  mean=assets.mean(), covariance=assets.cov(), target_weights={"A": 0.5, "B": 0.5})
        loss = (200 + 300) / 4000
        ref = result["var_es_references"]["historical:c99"]
        self.assertAlmostEqual(ref["loss_to_var"], loss / 0.025, places=12)
        self.assertTrue(ref["exceeds_es"])
        self.assertAlmostEqual(result["loss_to_max_drawdown"], loss / 0.25, places=12)
        post = result["post_stress_risk"]
        self.assertAlmostEqual(sum(post["component_var_share_after"].values()), 1.0, places=12)
        self.assertIn("drifts", result["post_stress_allocation"])


class HistoricalReplayTests(unittest.TestCase):
    def setUp(self):
        dates = pd.to_datetime(["2025-03-03", "2025-03-04", "2025-03-05", "2025-03-07", "2025-03-10"])
        self.grid = pd.DataFrame({
            "A": [100.0, 95.0, 90.0, 92.0, 99.0],
            "B": [50.0, 49.0, np.nan, 47.0, 48.0],
            "NIFTY 50": [1000.0, 980.0, 950.0, 960.0, 990.0],
        }, index=dates)
        self.holdings = holdings_frame()
        self.betas = pd.Series({"A": 1.2, "B": 0.5})

    def event(self, start, end):
        return {"event_id": "e", "name": "e", "start": start, "end": end, "window_basis": "test"}

    def test_replay_observed_returns_and_nearest_earlier_anchor(self):
        out = replay_event(self.event("2025-03-03", "2025-03-09"), self.grid, self.holdings, "NIFTY 50", self.betas)
        self.assertEqual(out.status, "replayed")
        self.assertEqual(out.anchors, {"start": "2025-03-03", "end": "2025-03-07"})  # 03-09 not observed
        self.assertAlmostEqual(out.vector.shocks["A"], 92 / 100 - 1, places=15)
        self.assertAlmostEqual(out.vector.shocks["B"], 47 / 50 - 1, places=15)
        self.assertAlmostEqual(out.benchmark_move, 960 / 1000 - 1, places=15)
        self.assertTrue(out.warnings)
        # Path uses only dates with every holding observed (03-05 skipped, not filled):
        # 2000 -> 1930 -> 1860, so the in-window drawdown is 1860 / 2000 - 1 = -7%.
        self.assertAlmostEqual(out.path_max_drawdown, -0.07, places=14)

    def test_missing_anchor_close_is_beta_propagated_not_filled(self):
        out = replay_event(self.event("2025-03-04", "2025-03-05"), self.grid, self.holdings, "NIFTY 50", self.betas)
        self.assertEqual(out.vector.basis.to_dict(), {"A": "historical_observed", "B": "beta_propagated"})
        self.assertAlmostEqual(out.vector.shocks["B"], 0.5 * (950 / 980 - 1), places=15)

    def test_complete_path_drawdown(self):
        out = replay_event(self.event("2025-03-07", "2025-03-10"), self.grid, self.holdings, "NIFTY 50", self.betas)
        self.assertEqual(out.path_max_drawdown, 0.0)

    def test_uncovered_window_is_unavailable_or_model_propagated(self):
        covid = self.event("2020-01-14", "2020-03-23")
        out = replay_event(covid, self.grid, self.holdings, "NIFTY 50", self.betas)
        self.assertEqual(out.status, "unavailable")
        self.assertIn("begins 2025-03-03", out.reason)
        partial = replay_event(self.event("2025-03-05", "2025-03-20"), self.grid, self.holdings, "NIFTY 50", self.betas)
        self.assertEqual(partial.status, "unavailable")
        reference = pd.Series([12000.0, 9000.0], index=pd.to_datetime(["2020-01-14", "2020-03-23"]))
        model = replay_event(covid, self.grid, self.holdings, "NIFTY 50", self.betas,
                             benchmark_reference=reference, reference_label="test reference")
        self.assertEqual(model.status, "model_propagated")
        self.assertAlmostEqual(model.vector.shocks["A"], 1.2 * -0.25, places=15)
        self.assertEqual(model.vector.category, "historical_model_propagated")

    def test_data_identified_events(self):
        returns = self.grid["NIFTY 50"].pct_change().dropna()
        events = data_identified_events(self.grid, returns, "NIFTY 50")
        self.assertEqual(events[0]["start"], "2025-03-03")
        self.assertEqual(events[0]["end"], "2025-03-05")
        self.assertEqual(events[1]["end"], "2025-03-05")  # -3.06% is the worst day


if __name__ == "__main__":
    unittest.main()
