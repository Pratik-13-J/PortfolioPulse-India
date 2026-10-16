"""Small, manually verifiable examples for Phase 4 risk conventions."""

from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from portfoliopulse.risk_metrics import (
    annualized_return,
    annualized_volatility,
    asset_correlations,
    cumulative_returns,
    drawdown_series,
    historical_var_es,
    maximum_drawdown,
    sharpe_ratio,
    volatility_contributions,
)


class PortfolioRiskMetricsTests(unittest.TestCase):
    def test_compounding_and_initial_wealth_drawdown(self) -> None:
        dates = pd.date_range("2026-01-01", periods=3)
        daily = pd.Series([-0.10, 0.10, 0.10], index=dates)
        np.testing.assert_allclose(cumulative_returns(daily), [-0.10, -0.01, 0.089])
        np.testing.assert_allclose(drawdown_series(daily), [-0.10, -0.01, 0.0])
        self.assertAlmostEqual(maximum_drawdown(daily), -0.10)

    def test_geometric_annual_return_uses_valid_interval_count(self) -> None:
        daily = pd.Series([0.10, -0.05, 0.0])
        total_growth = 1.10 * 0.95
        self.assertAlmostEqual(annualized_return(daily, trading_days_per_year=3), total_growth - 1)
        self.assertAlmostEqual(annualized_return(daily), total_growth ** (252 / 3) - 1)
        self.assertEqual(annualized_return(pd.Series([0.10, -1.0])), -1.0)

    def test_sample_volatility_and_effective_daily_risk_free_sharpe(self) -> None:
        daily = pd.Series([-0.10, 0.0, 0.10])
        self.assertAlmostEqual(annualized_volatility(daily), 0.10 * math.sqrt(252))
        positive = pd.Series([0.0, 0.02])
        daily_rf = 1.04 ** (1 / 252) - 1
        daily_sample_vol = 0.02 / math.sqrt(2)
        expected = (0.01 - daily_rf) / daily_sample_vol * math.sqrt(252)
        self.assertAlmostEqual(sharpe_ratio(positive, annual_risk_free_rate=0.04), expected)

    def test_zero_volatility_sharpe_undefined_and_short_history_rejected(self) -> None:
        flat = pd.Series([0.10, 0.10, 0.10])
        self.assertEqual(annualized_volatility(flat), 0.0)
        self.assertIsNone(sharpe_ratio(flat, annual_risk_free_rate=0.06))
        with self.assertRaisesRegex(ValueError, "At least 2"):
            annualized_volatility(pd.Series([0.01]))
        with self.assertRaisesRegex(ValueError, "At least 2"):
            sharpe_ratio(pd.Series([0.01]))
        with self.assertRaisesRegex(ValueError, "greater than -100%"):
            sharpe_ratio(pd.Series([0.01, 0.02]), annual_risk_free_rate=-1.0)

    def test_nearest_rank_var_es_and_ties(self) -> None:
        ten = pd.Series([-0.10, -0.08, -0.04, 0.0, 0.03, 0.05, 0.06, 0.08, 0.10, 0.12])
        self.assertEqual(historical_var_es(ten, confidence=0.80), (0.08, 0.09))
        self.assertEqual(historical_var_es(ten, confidence=0.95), (0.10, 0.10))
        twenty = pd.Series([-0.10, -0.05] + [0.0] * 18)
        self.assertEqual(historical_var_es(twenty, confidence=0.95), (0.10, 0.10))
        tied = pd.Series([-0.10, -0.10] + [0.0] * 8)
        self.assertEqual(historical_var_es(tied, confidence=0.80), (0.10, 0.10))
        self.assertEqual(historical_var_es(pd.Series([0.01, 0.02])), (0.0, 0.0))
        with self.assertRaisesRegex(ValueError, "strictly between"):
            historical_var_es(ten, confidence=1.0)

    def test_correlation_marks_constant_asset_undefined(self) -> None:
        assets = pd.DataFrame({
            "A": [-0.10, 0.0, 0.10],
            "B": [-0.20, 0.0, 0.20],
            "C": [0.05, 0.05, 0.05],
        })
        correlations = asset_correlations(assets)
        self.assertAlmostEqual(correlations.loc["A", "B"], 1.0)
        self.assertTrue(math.isnan(correlations.loc["A", "C"]))
        self.assertTrue(math.isnan(correlations.loc["C", "C"]))

    def test_marginal_component_risk_reconciles_to_snapshot_proxy(self) -> None:
        assets = pd.DataFrame({
            "A": [0.10, -0.10, 0.10, -0.10],
            "B": [0.20, -0.20, 0.20, -0.20],
        })
        weights = pd.Series([0.5, 0.5], index=["A", "B"])
        result = volatility_contributions(assets, weights)
        asset_a_vol = math.sqrt(0.04 / 3) * math.sqrt(252)
        asset_b_vol = math.sqrt(0.16 / 3) * math.sqrt(252)
        proxy_vol = annualized_volatility(assets.mul(weights, axis=1).sum(axis=1))
        self.assertAlmostEqual(result.loc["A", "marginal_annual_volatility"], asset_a_vol)
        self.assertAlmostEqual(result.loc["B", "marginal_annual_volatility"], asset_b_vol)
        self.assertAlmostEqual(result.loc["A", "component_annual_volatility"], 0.5 * asset_a_vol)
        self.assertAlmostEqual(result.loc["B", "component_annual_volatility"], 0.5 * asset_b_vol)
        self.assertAlmostEqual(result["component_annual_volatility"].sum(), proxy_vol)
        self.assertAlmostEqual(result.loc["A", "percent_contribution"], 1 / 3)
        self.assertAlmostEqual(result.loc["B", "percent_contribution"], 2 / 3)
        self.assertAlmostEqual(result["percent_contribution"].sum(), 1.0)

    def test_zero_proxy_volatility_and_invalid_inputs(self) -> None:
        cancelling = pd.DataFrame({"A": [0.10, -0.10, 0.10], "B": [-0.10, 0.10, -0.10]})
        with self.assertRaisesRegex(ValueError, "risk contributions are undefined"):
            volatility_contributions(cancelling, [0.5, 0.5])
        constant = pd.DataFrame({"A": [0.10, 0.10, 0.10], "B": [0.20, 0.0, -0.20]})
        with self.assertRaisesRegex(ValueError, "risk contributions are undefined"):
            volatility_contributions(constant, [1.0, 0.0])
        with self.assertRaisesRegex(ValueError, "Weight labels and order"):
            volatility_contributions(cancelling, pd.Series([0.5, 0.5], index=["B", "A"]))
        with self.assertRaisesRegex(ValueError, "sum to one"):
            volatility_contributions(cancelling, [0.2, 0.2])
        with self.assertRaisesRegex(ValueError, "long-only"):
            volatility_contributions(cancelling, [-0.1, 1.1])
        with self.assertRaisesRegex(ValueError, "not Boolean"):
            volatility_contributions(cancelling, [True, False])
        with self.assertRaisesRegex(ValueError, "missing observations"):
            asset_correlations(cancelling.assign(A=[0.1, np.nan, 0.1]))
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            cumulative_returns(pd.Series([0.01, np.inf]))
        with self.assertRaisesRegex(ValueError, "-100%"):
            maximum_drawdown(pd.Series([0.01, -1.1]))


if __name__ == "__main__":
    unittest.main()
