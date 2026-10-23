from __future__ import annotations

import unittest

from portfoliopulse.attribution import allocation_alerts, scenario_attribution
from portfoliopulse.scenarios import scenario_shocks


HOLDINGS = [
    {"symbol": "HDFCBANK", "sector": "Banking"},
    {"symbol": "ICICIBANK", "sector": "Banking"},
    {"symbol": "INFY", "sector": "Information Technology"},
    {"symbol": "RELIANCE", "sector": "Energy"},
]


class ScenarioShockTests(unittest.TestCase):
    def test_presets_and_custom_overrides(self) -> None:
        self.assertEqual(set(scenario_shocks(HOLDINGS, "broad_market", broad_shock=-0.08).values()), {-0.08})
        bank = scenario_shocks(HOLDINGS, "banking_stress")
        self.assertEqual(bank["HDFCBANK"], -0.20)
        self.assertEqual(bank["RELIANCE"], -0.05)
        it = scenario_shocks(HOLDINGS, "it_correction", stressed_shock=-0.25)
        self.assertEqual(it["INFY"], -0.25)
        custom = scenario_shocks(HOLDINGS, "individual", individual_shocks={"INFY": -0.3})
        self.assertEqual(custom, {"HDFCBANK": 0.0, "ICICIBANK": 0.0, "INFY": -0.3, "RELIANCE": 0.0})
        sector = scenario_shocks(HOLDINGS, "sector", sector_shocks={"Banking": -0.15})
        self.assertEqual(sector["ICICIBANK"], -0.15)
        self.assertEqual(sector["INFY"], 0.0)

    def test_invalid_or_unknown_shocks_rejected(self) -> None:
        with self.assertRaises(ValueError):
            scenario_shocks(HOLDINGS, "broad_market", broad_shock=-1.01)
        with self.assertRaises(ValueError):
            scenario_shocks(HOLDINGS, "individual", individual_shocks={"UNKNOWN": -0.2})
        with self.assertRaisesRegex(ValueError, "holding shock targets"):
            scenario_shocks(HOLDINGS, "individual", individual_shocks={"Banking": -0.2})
        with self.assertRaisesRegex(ValueError, "sector shock targets"):
            scenario_shocks(HOLDINGS, "sector", sector_shocks={"INFY": -0.2})
        with self.assertRaises(ValueError):
            scenario_shocks(HOLDINGS, "individual")


class ScenarioAttributionTests(unittest.TestCase):
    def test_inr_and_portfolio_percent_reconcile(self) -> None:
        frame, total, pct = scenario_attribution(
            {"A": 1000, "B": 3000}, {"A": -0.10, "B": -0.02},
        )
        self.assertAlmostEqual(frame.at["A", "impact_inr"], -100)
        self.assertAlmostEqual(frame.at["B", "impact_inr"], -60)
        self.assertAlmostEqual(total, -160)
        self.assertAlmostEqual(frame["impact_inr"].sum(), total)
        self.assertAlmostEqual(frame["portfolio_impact_pct"].sum(), pct)
        self.assertAlmostEqual(pct, -0.04)

    def test_mixed_shocks_zero_total_still_have_defined_portfolio_contributions(self) -> None:
        frame, total, pct = scenario_attribution({"A": 100, "B": 100}, {"A": -0.1, "B": 0.1})
        self.assertEqual(total, 0)
        self.assertEqual(pct, 0)
        self.assertAlmostEqual(float(frame["portfolio_impact_pct"].sum()), 0)

    def test_invalid_values_and_mismatched_symbols_rejected(self) -> None:
        with self.assertRaises(ValueError):
            scenario_attribution({"A": 100}, {"B": -0.1})
        with self.assertRaises(ValueError):
            scenario_attribution({"A": 100}, {"A": -1.1})
        with self.assertRaises(ValueError):
            scenario_attribution({"A": 100}, {"A": True})

    def test_weight_drift_and_concentration_alerts(self) -> None:
        alerts = allocation_alerts(
            {"A": 0.60, "B": 0.25, "C": 0.15},
            {"A": 0.40, "B": 0.35, "C": 0.25},
        )
        self.assertTrue(alerts["drifts"]["A"]["alert"])
        self.assertIn("A", alerts["concentration"]["single_name_alerts"])
        self.assertTrue(alerts["concentration"]["hhi_alert"])
        self.assertAlmostEqual(alerts["concentration"]["hhi"], 0.445)


if __name__ == "__main__":
    unittest.main()
