"""Age-weighted historical simulation (diagnostic VaR estimator): hand-worked checks."""

import unittest

import numpy as np
import pandas as pd

from portfoliopulse.risk_metrics import historical_var_es
from portfoliopulse.var_backtests import FORECAST_METHODS, rolling_var_forecasts
from portfoliopulse.var_models import age_weighted_historical


def s(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values), freq="D"), dtype=float)


class AgeWeightedTests(unittest.TestCase):
    def test_hand_example_decay_half(self):
        # Returns oldest -> newest [-0.03, 0.01, -0.05, 0.02]; ages 3, 2, 1, 0; decay 0.5:
        # w = 0.5^age * 0.5 / (1 - 0.5^4) -> [1/15, 2/15, 4/15, 8/15].
        # Sorted worst first: -0.05 (4/15), -0.03 (1/15), 0.01 (2/15), 0.02 (8/15).
        returns = s([-0.03, 0.01, -0.05, 0.02])
        at95 = age_weighted_historical(returns, confidence=0.95, decay=0.5)  # alpha 0.05 <= 4/15
        self.assertAlmostEqual(at95.var, 0.05, places=15)
        self.assertAlmostEqual(at95.es, 0.05, places=15)
        at70 = age_weighted_historical(returns, confidence=0.70, decay=0.5)  # alpha 0.30: 4/15 < 0.30 <= 5/15
        self.assertAlmostEqual(at70.var, 0.03, places=15)
        # ES = (4/15 * 0.05 + (0.30 - 4/15) * 0.03) / 0.30 = 0.0477777...
        self.assertAlmostEqual(at70.es, (4 / 15 * 0.05 + (0.30 - 4 / 15) * 0.03) / 0.30, places=14)

    def test_equal_weights_reproduce_nearest_rank(self):
        rng = np.random.default_rng(3)
        returns = s(rng.normal(0, 0.012, 300))
        for level in (0.95, 0.99):
            var, es = historical_var_es(returns, confidence=level)
            flat = age_weighted_historical(returns, confidence=level, decay=1.0)
            self.assertAlmostEqual(flat.var, var, places=15)
            self.assertAlmostEqual(flat.es, es, places=14)

    def test_recent_losses_dominate_with_fast_decay(self):
        calm, stressed = [0.001] * 240, [-0.03, -0.025, -0.02, 0.0, -0.028] * 2
        recent = age_weighted_historical(s(calm + stressed), confidence=0.95, decay=0.94)
        equal = age_weighted_historical(s(calm + stressed), confidence=0.95, decay=1.0)
        self.assertGreater(recent.var, equal.var)  # reacts to the recent volatile block

    def test_invalid_decay(self):
        for bad in (0.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                age_weighted_historical(s([0.01, -0.02, 0.0]), decay=bad)


class DiagnosticBacktestTests(unittest.TestCase):
    def test_age_weighted_forecasts_use_only_trailing_window(self):
        rng = np.random.default_rng(9)
        returns = s(rng.normal(0, 0.01, 120))
        frame = rolling_var_forecasts(returns, window=60, confidence=0.95, method="age_weighted", decay=0.97)
        expected = age_weighted_historical(returns.iloc[5:65], confidence=0.95, decay=0.97).var
        self.assertEqual(frame["var_forecast"].iloc[5], expected)
        self.assertEqual(FORECAST_METHODS, ("historical", "normal"))  # stored engine methods unchanged


if __name__ == "__main__":
    unittest.main()
