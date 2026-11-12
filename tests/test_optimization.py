"""Optimization and expected returns: closed forms, KKT, constraints, determinism.

Return series here are simulated, test-only data.
"""

import math
import unittest

import numpy as np
import pandas as pd

from portfoliopulse import expected_returns as er
from portfoliopulse import optimization as opt
from portfoliopulse.risk_metrics import annualized_return, volatility_contributions

SYMBOLS = ["A", "B", "C", "D"]
SECTORS = {"A": "Banking", "B": "Banking", "C": "IT", "D": "Energy"}


def cov4():
    vols = np.array([0.20, 0.25, 0.30, 0.18])
    corr = np.array([[1.0, 0.3, 0.2, 0.3], [0.3, 1.0, 0.25, 0.2], [0.2, 0.25, 1.0, 0.1], [0.3, 0.2, 0.1, 1.0]])
    return pd.DataFrame(np.outer(vols, vols) * corr, index=SYMBOLS, columns=SYMBOLS)


def problem(**kwargs):
    current = kwargs.pop("current", None)
    return opt.build_problem(SYMBOLS, SECTORS, opt.Constraints(**kwargs), current)


class CovarianceValidationTests(unittest.TestCase):
    def test_accepts_psd_and_reports_eigenvalues(self):
        info = opt.validate_covariance(cov4())
        self.assertTrue(info["positive_semidefinite"])
        np.testing.assert_allclose(info["min_eigenvalue"], np.linalg.eigvalsh(cov4().to_numpy()).min())

    def test_rejects_asymmetric_non_psd_and_nan(self):
        bad = cov4().copy()
        bad.iloc[0, 1] += 0.01
        with self.assertRaises(opt.OptimizationError):
            opt.validate_covariance(bad)
        not_psd = pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=["X", "Y"], columns=["X", "Y"])  # eig -1, 3
        with self.assertRaises(opt.OptimizationError):
            opt.validate_covariance(not_psd)
        nan = cov4().copy()
        nan.iloc[2, 2] = np.nan
        with self.assertRaises(opt.OptimizationError):
            opt.validate_covariance(nan)


class MinVarianceTests(unittest.TestCase):
    def test_two_asset_closed_form(self):
        # w1 = (s2^2 - s12) / (s1^2 + s2^2 - 2 s12) = (0.09 - 0.006) / (0.13 - 0.012) = 0.711864...
        cov = pd.DataFrame([[0.04, 0.006], [0.006, 0.09]], index=["X", "Y"], columns=["X", "Y"])
        p = opt.build_problem(["X", "Y"], {"X": "S1", "Y": "S2"}, opt.Constraints())
        result = opt.min_variance(cov, p)
        self.assertEqual(result.status, "optimal")
        self.assertAlmostEqual(result.weights["X"], 0.084 / 0.118, places=8)

    def test_interior_solution_matches_inverse_formula_and_kkt(self):
        cov = cov4()
        inv = np.linalg.inv(cov.to_numpy())
        expected = inv @ np.ones(4) / (np.ones(4) @ inv @ np.ones(4))
        self.assertTrue((expected > 0).all())
        result = opt.min_variance(cov, problem())
        np.testing.assert_allclose(result.weights.to_numpy(), expected, atol=1e-7)
        self.assertLess(result.diagnostics["kkt_free_gradient_spread"], 1e-7)
        self.assertLess(result.feasibility["max_violation"], 1e-9)

    def test_binding_bounds_sector_cap_and_turnover(self):
        cov = cov4()
        capped = opt.min_variance(cov, problem(max_weight=0.30, min_weight=0.05, sector_max={"Banking": 0.40}))
        w = capped.weights
        self.assertLessEqual(w.max(), 0.30 + 1e-9)
        self.assertGreaterEqual(w.min(), 0.05 - 1e-9)
        self.assertLessEqual(w["A"] + w["B"], 0.40 + 1e-9)
        self.assertAlmostEqual(w.sum(), 1.0, places=12)
        current = pd.Series({"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1})
        limited = opt.min_variance(cov, problem(max_turnover=0.10, current=current))
        self.assertLessEqual(float((limited.weights - current).abs().sum()), 0.10 + 1e-7)
        free = opt.min_variance(cov, problem())
        self.assertGreater(float((free.weights - current).abs().sum()), 0.10)  # so the limit binds
        self.assertGreater(float(limited.objective), float(free.objective))

    def test_infeasible_constraints_are_detected(self):
        with self.assertRaises(opt.OptimizationError):
            problem(min_weight=0.30)  # 4 x 30% > 100%
        with self.assertRaises(opt.OptimizationError):
            problem(min_weight=0.25, sector_max={"Banking": 0.40})
        with self.assertRaises(opt.OptimizationError):
            problem(sector_max={"Pharma": 0.2})

    def test_determinism(self):
        first = opt.min_variance(cov4(), problem(max_weight=0.35))
        second = opt.min_variance(cov4(), problem(max_weight=0.35))
        self.assertEqual([x.hex() for x in first.weights], [x.hex() for x in second.weights])


class MaxSharpeTests(unittest.TestCase):
    def test_matches_unconstrained_tangency(self):
        cov = cov4()
        mu = pd.Series({"A": 0.12, "B": 0.14, "C": 0.16, "D": 0.10})
        rf = 0.04
        raw = np.linalg.solve(cov.to_numpy(), (mu - rf).to_numpy())
        tangency = raw / raw.sum()
        self.assertTrue((tangency > 0).all())
        result = opt.max_sharpe(mu, cov, problem(), annual_risk_free_rate=rf)
        np.testing.assert_allclose(result.weights.to_numpy(), tangency, atol=1e-6)
        frontier = opt.efficient_frontier(mu, cov, problem(), points=30, annual_risk_free_rate=rf)
        self.assertGreaterEqual(result.diagnostics["sharpe"] + 1e-9, frontier["sharpe"].max())

    def test_undefined_when_no_portfolio_beats_risk_free(self):
        mu = pd.Series({"A": 0.02, "B": 0.03, "C": -0.01, "D": 0.01})
        result = opt.max_sharpe(mu, cov4(), problem(), annual_risk_free_rate=0.06)
        self.assertEqual(result.status, "undefined")
        self.assertIsNone(result.weights)
        self.assertAlmostEqual(result.diagnostics["max_feasible_expected_return"], 0.03, places=8)


class RiskParityTests(unittest.TestCase):
    def test_equal_contributions_to_1e6(self):
        cov = cov4()
        result = opt.risk_parity(cov, problem())
        self.assertEqual(result.status, "optimal")
        shares = opt.risk_contributions(result.weights, cov)["risk_share"]
        np.testing.assert_allclose(shares.to_numpy(), 0.25, atol=1e-6)

    def test_two_asset_inverse_volatility(self):
        # With two assets ERC gives w1 = (1/s1) / (1/s1 + 1/s2) for any correlation.
        cov = pd.DataFrame([[0.04, 0.012], [0.012, 0.09]], index=["X", "Y"], columns=["X", "Y"])
        w = opt.erc_log_barrier(cov)
        self.assertAlmostEqual(w["X"], (1 / 0.2) / (1 / 0.2 + 1 / 0.3), places=7)

    def test_constrained_fallback_reports_dispersion(self):
        result = opt.risk_parity(cov4(), problem(max_weight=0.26))
        self.assertIn(result.status, ("optimal", "constrained"))
        self.assertLessEqual(result.weights.max(), 0.26 + 1e-9)
        if result.status == "constrained":
            self.assertGreater(result.diagnostics["max_risk_share_deviation"], 0)


class FrontierAndContributionTests(unittest.TestCase):
    def test_frontier_properties(self):
        cov = cov4()
        mu = pd.Series({"A": 0.12, "B": 0.14, "C": 0.16, "D": 0.10})
        frontier = opt.efficient_frontier(mu, cov, problem(max_weight=0.5), points=25)
        self.assertEqual(len(frontier), 25)
        self.assertTrue((frontier["status"] == "optimal").all())
        self.assertTrue(frontier["volatility"].is_monotonic_increasing)
        np.testing.assert_allclose(frontier["expected_return"], frontier["target_return"], atol=1e-7)
        weights = frontier.filter(like="w:")
        np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-9)
        self.assertLessEqual(weights.to_numpy().max(), 0.5 + 1e-9)

    def test_risk_contributions_reconcile_and_match_existing_function(self):
        rng = np.random.default_rng(3)
        returns = pd.DataFrame(rng.multivariate_normal(np.zeros(4), cov4().to_numpy() / 252, 500),
                               columns=SYMBOLS, index=pd.bdate_range("2024-01-01", periods=500))
        w = pd.Series([0.1, 0.2, 0.3, 0.4], index=SYMBOLS)
        annual = returns.cov(ddof=1) * 252
        rc = opt.risk_contributions(w, annual)
        self.assertAlmostEqual(rc["component_volatility"].sum(), math.sqrt(w @ annual @ w), places=14)
        existing = volatility_contributions(returns, w)
        np.testing.assert_allclose(rc["component_volatility"], existing["component_annual_volatility"], rtol=1e-10)


class ExpectedReturnTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(12)
        self.returns = pd.DataFrame(rng.normal([4e-4, 1e-4, -2e-4], 0.012, (400, 3)), columns=["X", "Y", "Z"],
                                    index=pd.bdate_range("2024-01-01", periods=400))

    def test_arithmetic_and_cagr(self):
        arith = er.estimate(self.returns, "arithmetic")
        np.testing.assert_allclose(arith.annual, self.returns.mean() * 252, rtol=1e-14)
        cagr = er.estimate(self.returns, "cagr")
        self.assertEqual(cagr.annual["X"], annualized_return(self.returns["X"]))
        # AM-GM: geometric growth never exceeds compounding the arithmetic mean daily return.
        self.assertTrue((cagr.annual <= (1 + self.returns.mean()) ** 252 - 1).all())

    def test_bayes_stein_hand_formula(self):
        result = er.estimate(self.returns, "bayes_stein")
        n, p = self.returns.shape
        s = self.returns.cov().to_numpy() * (n - 1) / (n - p - 2)
        inv = np.linalg.inv(s)
        m = self.returns.mean().to_numpy()
        m0 = np.ones(p) @ inv @ m / (np.ones(p) @ inv @ np.ones(p))
        lam = (p + 2) / ((m - m0) @ inv @ (m - m0))
        delta = lam / (n + lam)
        np.testing.assert_allclose(result.annual, ((1 - delta) * m + delta * m0) * 252, rtol=1e-12)
        self.assertTrue(0 < result.shrinkage_intensity < 1)
        spread = lambda s: s.max() - s.min()
        self.assertLess(spread(result.annual), spread(er.estimate(self.returns).annual))


if __name__ == "__main__":
    unittest.main()
