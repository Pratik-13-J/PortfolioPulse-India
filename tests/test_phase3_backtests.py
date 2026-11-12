"""Phase 3 VaR backtests: hand-worked chi-square examples and SciPy checks."""

import math
import unittest

import numpy as np
import pandas as pd
from scipy import stats

from portfoliopulse import var_backtests as bt
from portfoliopulse.risk_metrics import historical_var_es


class KupiecTests(unittest.TestCase):
    def test_hand_worked_example(self):
        # n = 250, x = 5, p = 0.01 (99% VaR):
        # ln L0 = 245 ln 0.99 + 5 ln 0.01 = -2.462332 - 23.025851 = -25.488183
        # ln L1 = 245 ln 0.98 + 5 ln 0.02 = -4.949663 - 19.560115 = -24.509778
        # LR = -2 (-25.488183 + 24.509778) = 1.956810; chi2(1) p = 0.16186
        result = bt.kupiec_pof(5, 250, confidence=0.99)
        self.assertAlmostEqual(result["lr"], 1.956810, places=5)
        self.assertAlmostEqual(result["p_value"], 0.16186, places=4)
        self.assertAlmostEqual(result["expected_exceptions"], 2.5, places=12)
        self.assertAlmostEqual(result["p_value"], stats.chi2.sf(result["lr"], df=1), places=15)

    def test_exact_coverage_and_zero_exceptions(self):
        self.assertEqual(bt.kupiec_pof(5, 100, confidence=0.95)["lr"], 0.0)
        # x = 0: LR = -2 n ln(1 - p) = -2 * 250 * ln 0.99 = 5.025168
        self.assertAlmostEqual(bt.kupiec_pof(0, 250, confidence=0.99)["lr"], -500 * math.log(0.99), places=12)
        self.assertAlmostEqual(bt.kupiec_pof(0, 250, confidence=0.99)["lr"], 5.025168, places=5)

    def test_rejects_invalid_counts(self):
        for args in ((-1, 10), (11, 10), (1, 0), (True, 10)):
            with self.assertRaises(ValueError):
                bt.kupiec_pof(*args, confidence=0.99)


class ChristoffersenTests(unittest.TestCase):
    def test_equal_transition_probabilities_give_zero(self):
        # transitions: n00=4, n01=2, n10=2, n11=1 -> pi0 = pi1 = pi = 1/3
        hits = [False, False, True, True, False, False, False, True, False, False]
        result = bt.christoffersen_independence(hits)
        self.assertEqual((result["n00"], result["n01"], result["n10"], result["n11"]), (4, 2, 2, 1))
        self.assertAlmostEqual(result["lr"], 0.0, places=12)
        self.assertAlmostEqual(result["p_value"], 1.0, places=12)

    def test_clustered_hand_example(self):
        # n00=5, n01=1, n10=1, n11=2; pi0 = 1/6, pi1 = 2/3, pi = 1/3
        # ln L0 = 6 ln(2/3) + 3 ln(1/3) = -5.728628
        # ln L1 = 5 ln(5/6) + ln(1/6) + ln(1/3) + 2 ln(2/3) = -4.612909
        # LR = 2 * (5.728628 - 4.612909) = 2.231438
        hits = [False, False, False, True, True, True, False, False, False, False]
        result = bt.christoffersen_independence(hits)
        self.assertEqual((result["n00"], result["n01"], result["n10"], result["n11"]), (5, 1, 1, 2))
        self.assertAlmostEqual(result["lr"], 2.231438, places=5)
        lnl0 = 6 * math.log(2 / 3) + 3 * math.log(1 / 3)
        lnl1 = 5 * math.log(5 / 6) + math.log(1 / 6) + math.log(1 / 3) + 2 * math.log(2 / 3)
        self.assertAlmostEqual(result["lr"], -2 * (lnl0 - lnl1), places=12)

    def test_no_exceptions_is_defined(self):
        result = bt.christoffersen_independence([False] * 20)
        self.assertEqual(result["lr"], 0.0)

    def test_rejects_non_boolean(self):
        with self.assertRaises(ValueError):
            bt.christoffersen_independence([0, 1, 0])


class RollingForecastTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(17)
        self.returns = pd.Series(rng.normal(0, 0.01, 320),
                                 index=pd.date_range("2024-01-01", periods=320, freq="B"))

    def test_forecasts_use_only_prior_window(self):
        frame = bt.rolling_var_forecasts(self.returns, window=250, confidence=0.99, method="historical")
        self.assertEqual(len(frame), 70)
        self.assertEqual(frame.index[0], self.returns.index[250])
        expected = historical_var_es(self.returns.iloc[10:260], confidence=0.99)[0]
        self.assertEqual(frame["var_forecast"].iloc[10], expected)
        self.assertTrue((frame["exception"] == (frame["realized_return"] < -frame["var_forecast"])).all())

    def test_normal_forecast_and_summary(self):
        frame = bt.rolling_var_forecasts(self.returns, window=100, confidence=0.95, method="normal")
        window = self.returns.iloc[0:100]
        self.assertAlmostEqual(frame["var_forecast"].iloc[0],
                               -stats.norm(window.mean(), window.std(ddof=1)).ppf(0.05), places=13)
        summary = bt.backtest_var(frame, confidence=0.95)
        self.assertEqual(summary["observations"], 220)
        self.assertEqual(summary["exceptions"], int(frame["exception"].sum()))
        self.assertEqual(len(summary["exception_dates"]), summary["exceptions"])
        self.assertAlmostEqual(summary["conditional_coverage_lr"],
                               summary["kupiec_lr"] + summary["christoffersen_lr"], places=15)
        self.assertAlmostEqual(summary["conditional_coverage_p_value"],
                               stats.chi2.sf(summary["conditional_coverage_lr"], 2), places=15)

    def test_short_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            bt.rolling_var_forecasts(self.returns.iloc[:10], window=250)
        with self.assertRaises(ValueError):
            bt.rolling_var_forecasts(self.returns, method="garch")


if __name__ == "__main__":
    unittest.main()
