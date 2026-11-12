"""Phase 3 drawdown durations and covariance estimators."""

import unittest

import numpy as np
import pandas as pd

from portfoliopulse import covariance_estimators as ce
from portfoliopulse.drawdowns import drawdown_episodes, drawdown_summary
from portfoliopulse.portfolio_analysis import _observed_max_drawdown


def levels(wealth):
    return pd.Series(np.asarray(wealth) - 1.0, index=pd.date_range("2025-01-01", periods=len(wealth), freq="D"))


class DrawdownDurationTests(unittest.TestCase):
    def test_hand_example_episodes(self):
        # Wealth: 1, 1.1, 0.99, 1.045, 1.1, 1.2, 1.02, 1.14
        # Episode 1: peak 1.1 (pos 1) -> trough 0.99 (pos 2) -> recovered at 1.1 (pos 4).
        # Episode 2: peak 1.2 (pos 5) -> trough 1.02 (pos 6), unrecovered at the end.
        path = levels([1.0, 1.1, 0.99, 1.045, 1.1, 1.2, 1.02, 1.14])
        episodes = drawdown_episodes(path)
        self.assertEqual(len(episodes), 2)
        first, second = episodes.iloc[0], episodes.iloc[1]
        self.assertAlmostEqual(first["depth"], 0.99 / 1.1 - 1, places=15)
        self.assertTrue(first["recovered"])
        self.assertEqual((first["observations_to_trough"], first["duration_observations"],
                          first["recovery_observations"]), (1, 3, 2))
        self.assertEqual(first["recovery_date"], path.index[4])
        self.assertAlmostEqual(second["depth"], 1.02 / 1.2 - 1, places=15)  # -15%
        self.assertFalse(second["recovered"])
        self.assertEqual((second["duration_observations"], second["recovery_observations"]), (2, 1))

        summary = drawdown_summary(path)
        self.assertAlmostEqual(summary["max_drawdown"], -0.15, places=14)
        self.assertEqual(summary["max_drawdown_episode"]["peak_date"], "2025-01-06")
        self.assertIsNone(summary["max_drawdown_episode"]["recovery_date"])
        self.assertEqual(summary["longest_drawdown_episode"]["duration_observations"], 3)
        self.assertEqual(summary["longest_drawdown_episode"]["duration_calendar_days"], 3)
        self.assertTrue(summary["currently_underwater"])
        self.assertAlmostEqual(summary["current_drawdown"], 1.14 / 1.2 - 1, places=15)
        self.assertEqual(summary["current_underwater_observations"], 2)

    def test_first_observation_is_the_starting_peak(self):
        summary = drawdown_summary(levels([1.0, 0.9, 0.95, 1.0]))
        self.assertEqual(summary["max_drawdown_episode"]["peak_date"], "2025-01-01")
        self.assertEqual(summary["max_drawdown_episode"]["recovery_observations"], 2)
        self.assertFalse(summary["currently_underwater"])

    def test_monotone_path_has_no_episode(self):
        summary = drawdown_summary(levels([1.0, 1.01, 1.02]))
        self.assertEqual((summary["episodes"], summary["max_drawdown"]), (0, 0.0))

    def test_depth_matches_phase4_observed_max_drawdown(self):
        rng = np.random.default_rng(23)
        path = levels(np.cumprod(1 + rng.normal(0, 0.015, 400)) / (1 + 0.0))
        path.iloc[0] = 0.0
        path.iloc[[50, 51, 200]] = np.nan  # gaps are dropped, not filled
        self.assertEqual(drawdown_summary(path)["max_drawdown"], _observed_max_drawdown(path))

    def test_invalid_paths(self):
        with self.assertRaises(ValueError):
            drawdown_summary(levels([1.0]))
        with self.assertRaises(ValueError):
            drawdown_summary(levels([1.0, -0.5 + 1 - 1.0]))


def _ledoit_wolf_reference(x):
    """Direct loop transcription of Ledoit & Wolf (2004), Lemma 3.2-3.4."""
    x = x - x.mean(axis=0)
    n, p = x.shape
    s = sum(np.outer(row, row) for row in x) / n
    norm2 = lambda a: np.trace(a @ a.T) / p
    m = np.trace(s) / p
    d2 = norm2(s - m * np.eye(p))
    b_bar2 = sum(norm2(np.outer(row, row) - s) for row in x) / n ** 2
    delta = min(b_bar2, d2) / d2
    return delta * m * np.eye(p) + (1 - delta) * s, delta


class CovarianceEstimatorTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(31)
        cov = np.array([[1.0, 0.6, 0.1], [0.6, 2.0, 0.4], [0.1, 0.4, 0.5]]) * 1e-4
        self.assets = pd.DataFrame(rng.multivariate_normal([3e-4, 0, -1e-4], cov, 120),
                                   index=pd.date_range("2024-01-01", periods=120, freq="B"),
                                   columns=["A", "B", "C"])

    def test_sample_matches_numpy(self):
        np.testing.assert_allclose(ce.sample_covariance(self.assets).to_numpy(),
                                   np.cov(self.assets.to_numpy(), rowvar=False, ddof=1), rtol=1e-12)

    def test_ledoit_wolf_matches_reference_loop(self):
        shrunk, delta = ce.ledoit_wolf_covariance(self.assets)
        expected, expected_delta = _ledoit_wolf_reference(self.assets.to_numpy())
        self.assertAlmostEqual(delta, expected_delta, places=12)
        np.testing.assert_allclose(shrunk.to_numpy(), expected, rtol=1e-10, atol=1e-20)
        self.assertTrue(0 <= delta <= 1)
        self.assertGreater(np.linalg.eigvalsh(shrunk.to_numpy()).min(), 0)
        # Shrinkage preserves the average variance (trace).
        n = len(self.assets)
        self.assertAlmostEqual(np.trace(shrunk.to_numpy()),
                               np.trace(ce.sample_covariance(self.assets).to_numpy()) * (n - 1) / n, places=15)

    def test_ledoit_wolf_hand_example_without_sampling_noise(self):
        # Demeaned rows +-a with a = (0.01, 0.03): S = (a a' + a a') / 2 = a a'.
        # Every x_t x_t' equals S, so b_bar^2 = 0 and delta = 0: no shrinkage.
        frame = pd.DataFrame([[0.01, 0.03], [-0.01, -0.03]], columns=["X", "Y"],
                             index=pd.date_range("2025-01-01", periods=2))
        shrunk, delta = ce.ledoit_wolf_covariance(frame)
        self.assertAlmostEqual(delta, 0.0, places=12)
        np.testing.assert_allclose(shrunk.to_numpy(), [[1e-4, 3e-4], [3e-4, 9e-4]], rtol=1e-9)

    def test_ewma_hand_example(self):
        # lambda = 0.5, r = [0.01, -0.02, 0.03]: weights (oldest..newest) = [1, 2, 4] / 7
        # sigma^2 = (1e-4 * 1 + 4e-4 * 2 + 9e-4 * 4) / 7 = 45e-4 / 7
        frame = pd.DataFrame({"X": [0.01, -0.02, 0.03]}, index=pd.date_range("2025-01-01", periods=3))
        self.assertAlmostEqual(ce.ewma_covariance(frame, decay=0.5).iat[0, 0], 45e-4 / 7, places=17)

    def test_ewma_matches_explicit_weighted_sum(self):
        x = self.assets.to_numpy()
        lam, n = 0.94, len(x)
        expected = sum((1 - lam) * lam ** (n - 1 - t) * np.outer(x[t], x[t]) for t in range(n)) / (1 - lam ** n)
        np.testing.assert_allclose(ce.ewma_covariance(self.assets).to_numpy(), expected, rtol=1e-12)
        with self.assertRaises(ValueError):
            ce.ewma_covariance(self.assets, decay=1.0)

    def test_bundle(self):
        estimates, params = ce.covariance_estimates(self.assets)
        self.assertEqual(set(estimates), set(ce.ESTIMATORS))
        self.assertEqual(params["ewma_lambda"], 0.94)
        for matrix in estimates.values():
            self.assertTrue(matrix.index.equals(self.assets.columns))
            np.testing.assert_allclose(matrix.to_numpy(), matrix.to_numpy().T, rtol=0, atol=1e-18)


if __name__ == "__main__":
    unittest.main()
