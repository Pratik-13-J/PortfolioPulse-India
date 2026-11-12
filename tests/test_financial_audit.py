"""Version 1.0 financial methodology audit: independent hand-calculated checks.

Every expected value below is computed with plain Python arithmetic on small
hand-made fixtures (or literal numbers worked out by hand), never by calling
the production function under test.
"""

from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from portfoliopulse.benchmark_attribution import active_share, brinson_fachler, carino_link
from portfoliopulse.optimization import risk_contributions
from portfoliopulse.performance_metrics import (
    beta, capture_ratios, downside_beta, information_ratio, sortino_ratio, tracking_error,
)
from portfoliopulse.portfolio_returns import build_buy_and_hold_returns
from portfoliopulse.risk_metrics import (
    annualized_return, annualized_volatility, historical_var_es, maximum_drawdown, sharpe_ratio,
)
from portfoliopulse.var_models import cornish_fisher, parametric_normal

DATES = pd.date_range("2025-01-01", periods=4, freq="D")
P = [0.01, -0.02, 0.03, 0.00]
B = [0.005, -0.01, 0.02, -0.005]


def mean(xs):
    return sum(xs) / len(xs)


def sample_cov(xs, ys):
    mx, my = mean(xs), mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


def s(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values), freq="D"), dtype=float)


class PerformanceAuditTests(unittest.TestCase):
    def test_tracking_error_ir_beta_by_hand(self):
        active = [p - b for p, b in zip(P, B)]  # [0.005, -0.01, 0.01, 0.005]
        te = math.sqrt(sample_cov(active, active)) * math.sqrt(252)  # sample var = 2.25e-4 / 3 = 7.5e-5
        self.assertAlmostEqual(sample_cov(active, active), 7.5e-5, places=15)
        self.assertAlmostEqual(tracking_error(s(P), s(B)), te, places=14)
        self.assertAlmostEqual(information_ratio(s(P), s(B)), mean(active) * 252 / te, places=12)
        self.assertAlmostEqual(beta(s(P), s(B)), sample_cov(P, B) / sample_cov(B, B), places=13)
        down = [i for i, b in enumerate(B) if b < 0]  # days 1 and 3
        dp, db = [P[i] for i in down], [B[i] for i in down]
        self.assertAlmostEqual(downside_beta(s(P), s(B)), sample_cov(dp, db) / sample_cov(db, db), places=13)

    def test_capture_ratios_geometric_by_hand(self):
        up, down = capture_ratios(s(P), s(B))
        # Up days 0, 2: sqrt(1.01 * 1.03) - 1 over sqrt(1.005 * 1.02) - 1
        self.assertAlmostEqual(up, (math.sqrt(1.01 * 1.03) - 1) / (math.sqrt(1.005 * 1.02) - 1), places=12)
        # Down days 1, 3: sqrt(0.98 * 1.00) - 1 over sqrt(0.99 * 0.995) - 1
        self.assertAlmostEqual(down, (math.sqrt(0.98) - 1) / (math.sqrt(0.99 * 0.995) - 1), places=12)
        self.assertEqual(capture_ratios(s([0.01, 0.02]), s([0.01, 0.02]))[1], None)  # no down days

    def test_sortino_and_sharpe_by_hand(self):
        # rf = 0: mean(P) = 0.02 / 4 = 0.005; downside deviation over all N = sqrt(0.02^2 / 4) = 0.01
        self.assertAlmostEqual(sortino_ratio(s(P)), 0.005 * 252 / (0.01 * math.sqrt(252)), places=12)
        rf_daily = 1.06 ** (1 / 252) - 1
        sd = math.sqrt(sample_cov(P, P))
        self.assertAlmostEqual(sharpe_ratio(s(P), annual_risk_free_rate=0.06),
                               (mean(P) - rf_daily) / sd * math.sqrt(252), places=12)
        self.assertAlmostEqual(annualized_volatility(s(P)), sd * math.sqrt(252), places=14)
        growth = 1.01 * 0.98 * 1.03 * 1.00
        self.assertAlmostEqual(annualized_return(s(P)), growth ** (252 / 4) - 1, places=10)
        self.assertIsNone(sortino_ratio(s([0.01, 0.02, 0.0])))  # no downside: undefined, not infinite

    def test_drawdown_includes_initial_wealth(self):
        # Wealth 1 -> 0.9 -> 0.99 -> 0.792: peak 1, trough 0.792 -> -20.8%
        self.assertAlmostEqual(maximum_drawdown(s([-0.10, 0.10, -0.20])), 0.792 - 1, places=14)

    def test_buy_and_hold_twr_equals_value_ratio(self):
        prices = pd.DataFrame({"A": [100.0, 110.0, 99.0, 105.0], "B": [50.0, 49.0, 52.0, 53.0],
                               "NIFTY 50": [1000.0, 1010.0, 990.0, 1005.0]}, index=DATES)
        result = build_buy_and_hold_returns(prices, {"A": 2.0, "B": 4.0}, "NIFTY 50")
        values = [2 * a + 4 * b for a, b in zip(prices["A"], prices["B"])]  # 400, 416, 406, 422
        daily = result.daily_comparison_returns["portfolio"].tolist()
        self.assertEqual(len(daily), 3)
        for got, (v0, v1) in zip(daily, zip(values, values[1:])):
            self.assertAlmostEqual(got, v1 / v0 - 1, places=15)
        # With no external cash flows, time-weighted (linked) return = V_end / V_start - 1 (= MWR).
        self.assertAlmostEqual(float(np.prod([1 + r for r in daily]) - 1), 422 / 400 - 1, places=14)
        # Lagged weights: 0.5 x A return + 0.5 x B return on day 1 (weights 200/400, 200/400).
        self.assertAlmostEqual(daily[0], 0.5 * 0.10 + 0.5 * (49 / 50 - 1), places=15)


class BrinsonAuditTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = pd.DataFrame({"weight": [0.6, 0.4], "return": [0.10, 0.02]}, index=["A", "B"])
        self.benchmark = pd.DataFrame({"weight": [0.5, 0.3, 0.2], "return": [0.08, 0.03, 0.05]},
                                      index=["A", "B", "C"])

    def test_brinson_fachler_hand_example(self):
        # R_b = 0.5*0.08 + 0.3*0.03 + 0.2*0.05 = 0.059; R_p = 0.6*0.10 + 0.4*0.02 = 0.068
        result = brinson_fachler(self.portfolio, self.benchmark)
        self.assertAlmostEqual(result.benchmark_return, 0.059, places=15)
        self.assertAlmostEqual(result.portfolio_return, 0.068, places=15)
        e = result.effects
        expected = {  # (allocation, selection, interaction) by hand
            "A": (0.1 * (0.08 - 0.059), 0.5 * (0.10 - 0.08), 0.1 * (0.10 - 0.08)),     # 0.0021, 0.01, 0.002
            "B": (0.1 * (0.03 - 0.059), 0.3 * (0.02 - 0.03), 0.1 * (0.02 - 0.03)),     # -0.0029, -0.003, -0.001
            "C": (-0.2 * (0.05 - 0.059), 0.0, 0.0),                                     # 0.0018 (sector not held)
        }
        for sector, (a, sel, i) in expected.items():
            self.assertAlmostEqual(e.at[sector, "allocation"], a, places=15)
            self.assertAlmostEqual(e.at[sector, "selection"], sel, places=15)
            self.assertAlmostEqual(e.at[sector, "interaction"], i, places=15)
        self.assertAlmostEqual(float(e["total"].sum()), 0.068 - 0.059, places=15)  # A + S + I = R_p - R_b

    def test_bf_and_bhb_allocation_totals_coincide(self):
        # BHB allocation_s = (w_p - w_b) r_b,s. Because sum(w_p - w_b) = 0, the totals equal BF's;
        # only the sector-level split differs (BHB itself is not implemented; BF is used).
        result = brinson_fachler(self.portfolio, self.benchmark)
        bhb_total = 0.1 * 0.08 + 0.1 * 0.03 + (-0.2) * 0.05  # = 0.001
        self.assertAlmostEqual(float(result.effects["allocation"].sum()), bhb_total, places=15)

    def test_off_benchmark_sector_and_zero_weight(self):
        portfolio = pd.DataFrame({"weight": [0.7, 0.3, 0.0], "return": [0.05, 0.12, 0.40]}, index=["A", "D", "Z"])
        benchmark = pd.DataFrame({"weight": [1.0], "return": [0.04]}, index=["A"])
        e = brinson_fachler(portfolio, benchmark).effects
        # D is off-benchmark: R_b,D := R_b = 0.04 -> allocation 0, interaction 0.3 * (0.12 - 0.04) = 0.024
        self.assertAlmostEqual(e.at["D", "allocation"], 0.0, places=15)
        self.assertAlmostEqual(e.at["D", "interaction"], 0.024, places=15)
        # A zero-weight position contributes nothing.
        self.assertAlmostEqual(float(e.loc["Z"].abs().sum()), 0.0, places=15)
        self.assertAlmostEqual(float(e["total"].sum()), (0.7 * 0.05 + 0.3 * 0.12) - 0.04, places=15)

    def test_carino_two_periods_by_hand(self):
        first = brinson_fachler(self.portfolio, self.benchmark)
        second = brinson_fachler(self.benchmark.drop(index="C").assign(weight=[0.5, 0.5]),
                                 self.benchmark)
        linked = carino_link([first, second])
        rp = (1 + first.portfolio_return) * (1 + second.portfolio_return) - 1
        rb = (1 + first.benchmark_return) * (1 + second.benchmark_return) - 1
        self.assertAlmostEqual(float(linked.effects["total"].sum()), rp - rb, places=14)
        k = lambda a, b: (math.log1p(a) - math.log1p(b)) / (a - b)
        expected_a = (first.effects.at["A", "total"] * k(first.portfolio_return, first.benchmark_return)
                      + second.effects.at["A", "total"] * k(second.portfolio_return, second.benchmark_return)) / k(rp, rb)
        self.assertAlmostEqual(linked.effects.at["A", "total"], expected_a, places=14)

    def test_active_share_by_hand(self):
        # names A, B, C: |0.6-0.5| + |0.4-0.3| + |0-0.2| = 0.4 -> 0.2
        self.assertAlmostEqual(active_share({"A": 0.6, "B": 0.4}, {"A": 0.5, "B": 0.3, "C": 0.2}), 0.2, places=15)


class RiskAuditTests(unittest.TestCase):
    def test_volatility_risk_contributions_by_hand(self):
        cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.09]], index=["X", "Y"], columns=["X", "Y"])
        rc = risk_contributions(pd.Series({"X": 0.6, "Y": 0.4}), cov)
        # Sigma w = [0.028, 0.042]; sigma_p^2 = 0.6*0.028 + 0.4*0.042 = 0.0336
        sigma_p = math.sqrt(0.0336)
        self.assertAlmostEqual(rc.at["X", "marginal_volatility"], 0.028 / sigma_p, places=15)
        self.assertAlmostEqual(rc.at["X", "component_volatility"], 0.6 * 0.028 / sigma_p, places=15)
        self.assertAlmostEqual(rc.at["Y", "component_volatility"], 0.4 * 0.042 / sigma_p, places=15)
        self.assertAlmostEqual(float(rc["component_volatility"].sum()), sigma_p, places=15)  # Euler
        self.assertAlmostEqual(float(rc["risk_share"].sum()), 1.0, places=15)

    def test_historical_var_es_nearest_rank_by_hand(self):
        returns = s([-0.05, -0.03, -0.02, 0.01, 0.02] + [0.001 * i for i in range(15)])  # N = 20
        self.assertEqual(historical_var_es(returns, confidence=0.95), (0.05, 0.05))  # k = ceil(1.0) = 1
        var90, es90 = historical_var_es(returns, confidence=0.90)  # k = 2: worst two -0.05, -0.03
        self.assertAlmostEqual(var90, 0.03, places=15)
        self.assertAlmostEqual(es90, 0.04, places=15)
        self.assertGreaterEqual(es90, var90)

    def test_sign_conventions_are_documented(self):
        gains = s([0.01, 0.02, 0.015, 0.012, 0.018])
        # Historical VaR/ES are floored at zero (no loss in the tail of an all-gain sample) ...
        self.assertEqual(historical_var_es(gains, confidence=0.95), (0.0, 0.0))
        # ... while the parametric closed form is not floored: a negative value means the
        # 5% quantile is still a gain (-(mu + z sigma) < 0). Documented in the audit report.
        mu, sd = mean(gains.tolist()), math.sqrt(sample_cov(gains.tolist(), gains.tolist()))
        normal = parametric_normal(gains, confidence=0.95)
        self.assertAlmostEqual(normal.var, -(mu - 1.6448536269514729 * sd), places=12)
        self.assertLess(normal.var, 0)

    def test_es_not_below_var_for_parametric_models(self):
        rng = np.random.default_rng(101)
        returns = s(list(rng.standard_t(6, 500) * 0.01))
        for level in (0.95, 0.99):
            n = parametric_normal(returns, confidence=level)
            self.assertGreater(n.es, n.var)
            cf = cornish_fisher(returns, confidence=level)
            if cf.details["tail_monotone"]:
                self.assertGreaterEqual(cf.es, cf.var)


if __name__ == "__main__":
    unittest.main()
