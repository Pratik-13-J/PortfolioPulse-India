"""Phase 3 VaR/ES models: hand-calculable fixtures plus independent SciPy checks."""

import math
import unittest

import numpy as np
import pandas as pd
from scipy import integrate, stats

from portfoliopulse import var_models as vm
from portfoliopulse.risk_metrics import historical_var_es

Z95 = -1.6448536269514729  # Phi^-1(0.05), from standard normal tables
PHI_Z95 = 0.10313564037537128  # phi(Z95) = exp(-Z95^2 / 2) / sqrt(2 pi)


def series(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values), freq="D"), dtype=float)


class NormalVaRTests(unittest.TestCase):
    def test_closed_form_hand_example(self):
        # mean 0.002; squared deviations sum 1.48e-3; sample variance 3.7e-4.
        returns = series([0.01, -0.02, 0.03, -0.01, 0.0])
        estimate = vm.parametric_normal(returns, confidence=0.95)
        sigma = math.sqrt(3.7e-4)
        self.assertAlmostEqual(estimate.details["mean"], 0.002, places=15)
        self.assertAlmostEqual(estimate.details["std"], sigma, places=15)
        self.assertAlmostEqual(estimate.var, -0.002 - Z95 * sigma, places=14)
        self.assertAlmostEqual(estimate.es, -0.002 + sigma * PHI_Z95 / 0.05, places=14)
        self.assertEqual(estimate.observations, 5)

    def test_matches_scipy_normal_distribution(self):
        rng = np.random.default_rng(7)
        returns = series(rng.normal(0.0004, 0.012, 300))
        mu, sigma = returns.mean(), returns.std(ddof=1)
        for level in (0.95, 0.99):
            estimate = vm.parametric_normal(returns, confidence=level)
            dist = stats.norm(mu, sigma)
            self.assertAlmostEqual(estimate.var, -dist.ppf(1 - level), places=13)
            es_scipy = -dist.expect(lambda x: x, ub=dist.ppf(1 - level), conditional=True)
            self.assertAlmostEqual(estimate.es, es_scipy, places=10)

    def test_rejects_bad_confidence(self):
        for bad in (0, 1, 1.5, float("nan"), True):
            with self.assertRaises(ValueError):
                vm.parametric_normal(series([0.01, -0.01, 0.02]), confidence=bad)


class CornishFisherTests(unittest.TestCase):
    def test_moments_match_scipy_biased_estimators(self):
        returns = series([0.012, -0.031, 0.004, 0.019, -0.007, 0.001, -0.052, 0.023])
        moments = vm.sample_moments(returns)
        self.assertAlmostEqual(moments.skewness, stats.skew(returns, bias=True), places=12)
        self.assertAlmostEqual(moments.excess_kurtosis, stats.kurtosis(returns, fisher=True, bias=True), places=12)
        self.assertAlmostEqual(moments.std, returns.std(ddof=1), places=15)

    def test_symmetric_hand_moments(self):
        # [-2,-1,0,1,2] %: m2 = 2e-4, m4 = 6.8e-8, so K = 1.7 - 3 = -1.3, S = 0.
        moments = vm.sample_moments(series([-0.02, -0.01, 0.0, 0.01, 0.02]))
        self.assertAlmostEqual(moments.skewness, 0.0, places=12)
        self.assertAlmostEqual(moments.excess_kurtosis, -1.3, places=12)

    def test_z_expansion_hand_value_and_normal_limit(self):
        self.assertEqual(vm.cornish_fisher_z(Z95, 0.0, 0.0), Z95)
        z, s, k = Z95, -0.5, 3.0
        expected = (z + (z * z - 1) * s / 6 + (z ** 3 - 3 * z) * k / 24 - (2 * z ** 3 - 5 * z) * s * s / 36)
        # z^2 = 2.705544, z^3 = -4.450223:
        # -1.644854 - 0.142129 + 0.060542 + 0.004696 = -1.721744 (hand arithmetic).
        self.assertAlmostEqual(vm.cornish_fisher_z(z, s, k), expected, places=15)
        self.assertAlmostEqual(expected, -1.721744, places=6)

    def test_es_equals_numerical_tail_average_of_cf_quantile(self):
        rng = np.random.default_rng(11)
        returns = series(rng.standard_t(5, 500) * 0.01)
        for level in (0.95, 0.99):
            est = vm.cornish_fisher(returns, confidence=level)
            mu, sd = est.details["mean"], est.details["std"]
            s, k = est.details["skewness"], est.details["excess_kurtosis"]
            alpha = 1 - level
            integral, _ = integrate.quad(
                lambda u: mu + sd * vm.cornish_fisher_z(stats.norm.ppf(u), s, k), 0, alpha, limit=200,
            )
            self.assertAlmostEqual(est.es, -integral / alpha, places=8)
            self.assertAlmostEqual(est.var, -(mu + sd * vm.cornish_fisher_z(stats.norm.ppf(alpha), s, k)), places=14)
            self.assertIn("tail_monotone", est.details)

    def test_constant_returns_are_undefined(self):
        with self.assertRaises(ValueError):
            vm.cornish_fisher(series([0.01] * 6))


class HistoricalReuseTests(unittest.TestCase):
    def test_wraps_existing_estimator_and_tail_counts(self):
        rng = np.random.default_rng(3)
        returns = series(rng.normal(0, 0.01, 686))
        estimate = vm.historical(returns, confidence=0.99)
        self.assertEqual((estimate.var, estimate.es), historical_var_es(returns, confidence=0.99))
        self.assertEqual(estimate.details["tail_count"], 7)  # ceil(6.86)
        self.assertEqual(vm.historical_tail_count(686, 0.95), 35)  # ceil(34.3)
        self.assertEqual(vm.historical_tail_count(20, 0.95), 1)
        tail = vm.historical_tail_observations(returns, confidence=0.99)
        self.assertEqual(len(tail), 7)
        self.assertAlmostEqual(tail["loss"].iloc[-1], estimate.var, places=15)
        self.assertAlmostEqual(tail["loss"].mean(), estimate.es, places=15)
        self.assertTrue(tail["daily_return"].is_monotonic_increasing)


class MonteCarloTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        dates = pd.date_range("2024-01-01", periods=400, freq="B")
        cov = np.array([[1.0, 0.4, 0.2], [0.4, 1.5, 0.3], [0.2, 0.3, 0.8]]) * 1e-4
        self.assets = pd.DataFrame(rng.multivariate_normal([4e-4, 2e-4, 1e-4], cov, 400),
                                   index=dates, columns=["A", "B", "C"])
        self.values = pd.Series({"A": 50_000.0, "B": 30_000.0, "C": 20_000.0})
        self.weights = self.values / self.values.sum()

    def test_multivariate_normal_converges_to_closed_form_within_se(self):
        mean, cov = vm.sample_mean_and_covariance(self.assets)
        results = vm.monte_carlo(self.assets, self.values, method="multivariate_normal",
                                 paths=200_000, seed=20261112, batches=50)
        for level, mc in results.items():
            closed, _ = vm.delta_normal_components(mean, cov, self.weights, confidence=level)
            self.assertLess(abs(mc.var - closed.var), 3 * mc.var_standard_error, level)
            self.assertLess(abs(mc.es - closed.es), 3 * mc.es_standard_error, level)
            self.assertGreater(mc.var_standard_error, 0)
            self.assertEqual((mc.seed, mc.paths, mc.batches), (20261112, 200_000, 50))
            self.assertEqual(mc.details["negative_price_paths"], 0)

    def test_seed_reproducibility_and_component_es_reconciles(self):
        first = vm.monte_carlo(self.assets, self.values, method="bootstrap", paths=10_000, seed=5, batches=20)
        second = vm.monte_carlo(self.assets, self.values, method="bootstrap", paths=10_000, seed=5, batches=20)
        other = vm.monte_carlo(self.assets, self.values, method="bootstrap", paths=10_000, seed=6, batches=20)
        for level in (0.95, 0.99):
            self.assertEqual(first[level].var.hex(), second[level].var.hex())
            # Bootstrap quantiles are historical values, so VaR can coincide across
            # seeds; the tail average (ES) still differs.
            self.assertNotEqual(first[level].es, other[level].es)
            self.assertAlmostEqual(first[level].component_es.sum(), first[level].es, places=14)
            self.assertEqual(first[level].details["tail_count"], math.ceil((1 - level) * 10_000 - 1e-12))

    def test_bootstrap_revalues_fixed_quantities_from_historical_rows(self):
        portfolio, contributions = vm.simulate_portfolio_returns(
            self.assets, self.values, method="bootstrap", paths=50, seed=9)
        rows = np.random.default_rng(9).integers(0, len(self.assets), size=50)
        drawn = self.assets.to_numpy()[rows]
        # Fixed quantities: new value = sum V_i (1 + R_i); return = sum V_i R_i / sum V.
        expected = (drawn * self.values.to_numpy()).sum(axis=1) / self.values.sum()
        np.testing.assert_allclose(portfolio, expected, rtol=0, atol=1e-16)
        np.testing.assert_allclose(contributions.sum(axis=1), portfolio, rtol=0, atol=1e-16)
        self.assertTrue(np.isin(drawn[:, 0], self.assets["A"].to_numpy()).all())

    def test_bootstrap_tail_cannot_exceed_worst_history(self):
        mc = vm.monte_carlo(self.assets, self.values, method="bootstrap", paths=20_000, seed=1, batches=20)
        realized = self.assets.to_numpy() @ self.weights.to_numpy()
        self.assertLessEqual(mc[0.99].var, -realized.min() + 1e-15)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            vm.monte_carlo(self.assets, self.values, method="garch")
        with self.assertRaises(ValueError):
            vm.monte_carlo(self.assets, self.values, paths=1001, batches=50)
        with self.assertRaises(ValueError):
            vm.monte_carlo(self.assets, self.values.drop("C"))
        with self.assertRaises(ValueError):
            vm.monte_carlo(self.assets, self.values, seed=-1)


class DeltaNormalComponentTests(unittest.TestCase):
    def test_two_asset_hand_example(self):
        cov = pd.DataFrame([[4e-4, 1e-4], [1e-4, 9e-4]], index=["X", "Y"], columns=["X", "Y"])
        mean = pd.Series([0.0, 0.0], index=["X", "Y"])
        total, parts = vm.delta_normal_components(mean, cov, [0.5, 0.5], confidence=0.95,
                                                  portfolio_value_inr=1_000_000)
        sigma_p = math.sqrt(0.25 * (4e-4 + 9e-4 + 2e-4))  # = sqrt(3.75e-4)
        self.assertAlmostEqual(total.var, -Z95 * sigma_p, places=14)
        self.assertAlmostEqual(parts.at["X", "component_var"], 0.5 * -Z95 * 2.5e-4 / sigma_p, places=14)
        self.assertAlmostEqual(parts.at["Y", "component_var"], 0.5 * -Z95 * 5e-4 / sigma_p, places=14)
        self.assertAlmostEqual(parts["component_var"].sum(), total.var, places=15)
        self.assertAlmostEqual(parts["component_es"].sum(), total.es, places=15)
        self.assertAlmostEqual(parts["var_share"].sum(), 1.0, places=14)
        self.assertAlmostEqual(parts["component_var_inr"].sum(), total.var * 1_000_000, places=8)

    def test_components_match_numerical_euler_gradient(self):
        rng = np.random.default_rng(5)
        assets = pd.DataFrame(rng.normal(2e-4, 0.01, (300, 4)), columns=list("ABCD"))
        assets.index = pd.date_range("2024-01-01", periods=300, freq="B")
        mean, cov = vm.sample_mean_and_covariance(assets)
        w = np.array([0.1, 0.2, 0.3, 0.4])

        def var_of(weights):
            # Scale-homogeneous VaR (no sum-to-one constraint) for the Euler check.
            mu_p = weights @ mean.to_numpy()
            sd_p = math.sqrt(weights @ cov.to_numpy() @ weights)
            return -(mu_p + stats.norm.ppf(0.01) * sd_p)

        _, parts = vm.delta_normal_components(mean, cov, w, confidence=0.99)
        h = 1e-7
        for i, name in enumerate(cov.columns):
            bump = np.zeros(4); bump[i] = h
            gradient = (var_of(w + bump) - var_of(w - bump)) / (2 * h)
            self.assertAlmostEqual(parts.at[name, "component_var"], w[i] * gradient, places=8)
        np.testing.assert_allclose(cov.to_numpy(), np.cov(assets.to_numpy(), rowvar=False, ddof=1), rtol=1e-12)

    def test_zero_variance_is_undefined(self):
        cov = pd.DataFrame(np.zeros((2, 2)), index=["X", "Y"], columns=["X", "Y"])
        with self.assertRaises(ValueError):
            vm.delta_normal_components(pd.Series([0.0, 0.0], index=["X", "Y"]), cov, [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
