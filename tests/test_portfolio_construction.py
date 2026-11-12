"""Costs, integer allocation, portfolio evaluation and walk-forward (simulated test data)."""

import math
import unittest

import numpy as np
import pandas as pd

from portfoliopulse.optimization_backtest import walk_forward
from portfoliopulse.portfolio_allocation import CostModel, integer_allocation, rebalance_allocation, turnover
from portfoliopulse.portfolio_evaluation import evaluate
from portfoliopulse.risk_metrics import historical_var_es, maximum_drawdown
from tests.test_optimization import SECTORS, SYMBOLS, cov4

INDIAN = {
    "gst_rate": 0.18, "brokerage_per_order_inr": 0.0,
    "components": [
        {"name": "brokerage", "buy_bps": 0.0, "sell_bps": 0.0, "gst_applicable": True},
        {"name": "stt", "buy_bps": 10.0, "sell_bps": 10.0, "gst_applicable": False},
        {"name": "stamp", "buy_bps": 1.5, "sell_bps": 0.0, "gst_applicable": False},
        {"name": "exchange", "buy_bps": 0.297, "sell_bps": 0.297, "gst_applicable": True},
        {"name": "sebi", "buy_bps": 0.01, "sell_bps": 0.01, "gst_applicable": True},
    ],
}
ZERO = {"components": [{"name": "none", "buy_bps": 0.0, "sell_bps": 0.0}], "gst_rate": 0.0}


class CostTests(unittest.TestCase):
    def test_side_rates_hand_calculation(self):
        model = CostModel.from_config(INDIAN)
        # buy: (10 + 1.5 + 0.297 + 0.01) bps + 18% GST on (0.297 + 0.01) bps = 11.807 + 0.05526 bps
        self.assertAlmostEqual(model.rate("buy"), 11.86226e-4, places=12)
        self.assertAlmostEqual(model.rate("sell"), 10.36226e-4, places=12)
        costs = model.trade_costs(pd.Series({"A": 100_000.0, "B": -50_000.0, "C": 0.0}))
        self.assertAlmostEqual(costs.at["A", "cost_inr"], 118.6226, places=8)  # 100,000 x 11.86226 bp
        self.assertAlmostEqual(costs.at["B", "cost_inr"], 51.8113, places=8)  # 50,000 x 10.36226 bp
        self.assertEqual(costs.at["C", "cost_inr"], 0.0)
        fraction = model.turnover_cost_fraction(pd.Series({"A": 0.10, "B": -0.10}))
        self.assertAlmostEqual(fraction, 0.10 * 11.86226e-4 + 0.10 * 10.36226e-4, places=14)

    def test_turnover_definitions(self):
        result = turnover(pd.Series({"A": 0.6, "B": 0.4}), pd.Series({"A": 0.5, "B": 0.5}))
        self.assertAlmostEqual(result["gross"], 0.2, places=15)
        self.assertAlmostEqual(result["one_way"], 0.1, places=15)


class IntegerAllocationTests(unittest.TestCase):
    def test_hand_example_without_costs(self):
        # 50/50 of INR 1,000 at prices 100 and 300: floors 5 and 1 shares (INR 800).
        # Another B share (300) does not fit; another A share would overshoot A's
        # target, so INR 200 stays as residual cash.
        alloc = integer_allocation(pd.Series({"A": 0.5, "B": 0.5}), pd.Series({"A": 100.0, "B": 300.0}),
                                   1000, CostModel.from_config(ZERO))
        self.assertEqual(alloc.shares.to_dict(), {"A": 5, "B": 1})
        self.assertAlmostEqual(alloc.residual_cash_inr, 200.0, places=10)
        self.assertAlmostEqual(alloc.invested_inr + alloc.transaction_cost_inr + alloc.residual_cash_inr, 1000.0)

    def test_budget_never_exceeded_and_deterministic(self):
        model = CostModel.from_config({**INDIAN, "brokerage_per_order_inr": 20.0})
        weights = pd.Series({"A": 0.3, "B": 0.25, "C": 0.25, "D": 0.2})
        prices = pd.Series({"A": 1389.4, "B": 726.85, "C": 1110.0, "D": 263.9})
        first = integer_allocation(weights, prices, 1_000_000, model)
        second = integer_allocation(weights, prices, 1_000_000, model)
        self.assertEqual(first.shares.to_dict(), second.shares.to_dict())
        spent = float((first.shares * prices).sum())
        fees = spent * model.rate("buy") + 20 * 1.18 * int((first.shares > 0).sum())
        self.assertAlmostEqual(first.transaction_cost_inr, fees, places=6)
        self.assertGreaterEqual(first.residual_cash_inr, 0)
        self.assertLessEqual(spent + fees, 1_000_000 + 1e-6)
        # Each holding is within one share of target, plus the cost that the budget must fund.
        gaps = (first.shares * prices - weights * 1_000_000).abs()
        self.assertTrue((gaps <= prices + first.transaction_cost_inr).all())
        self.assertAlmostEqual(first.invested_inr + first.transaction_cost_inr + first.residual_cash_inr, 1_000_000)

    def test_rebalance_is_self_financed(self):
        model = CostModel.from_config(INDIAN)
        prices = pd.Series({"A": 100.0, "B": 250.0, "C": 80.0})
        current = pd.Series({"A": 100, "B": 10, "C": 50})  # INR 10,000 + 2,500 + 4,000
        alloc = rebalance_allocation(pd.Series({"A": 0.3, "B": 0.4, "C": 0.3}), prices, current, model)
        self.assertAlmostEqual(alloc.budget_inr, 16_500.0)
        self.assertGreaterEqual(alloc.residual_cash_inr, -1e-9)
        trades = alloc.trades
        expected_cost = sum(abs(v) * (model.rate("buy") if v > 0 else model.rate("sell"))
                            for v in trades["trade_value_inr"])
        self.assertAlmostEqual(alloc.transaction_cost_inr, expected_cost, places=8)
        self.assertEqual(current.to_dict()["A"], 100)  # input holdings untouched


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(21)
        self.assets = pd.DataFrame(rng.multivariate_normal([3e-4, 2e-4, 1e-4, 2e-4], cov4().to_numpy() / 252, 400),
                                   columns=SYMBOLS, index=pd.bdate_range("2024-01-01", periods=400))
        self.bench = self.assets.mean(axis=1).rename("NIFTY 50")

    def test_metrics_and_reconciliation(self):
        w = pd.Series([0.25, 0.25, 0.25, 0.25], index=SYMBOLS)
        mu = self.assets.mean() * 252
        cov = self.assets.cov() * 252
        out = evaluate(w, asset_returns=self.assets, benchmark_returns=self.bench, mu_annual=mu, cov_annual=cov,
                       annual_risk_free_rate=0.06, sectors=SECTORS, current_weights=w,
                       cost_model=CostModel.from_config(INDIAN))
        m = out["metrics"]
        self.assertAlmostEqual(m["ex_ante_expected_return"], float(mu.mean()), places=14)
        self.assertAlmostEqual(m["ex_ante_volatility"], math.sqrt(w @ cov @ w), places=14)
        series = self.assets @ w
        self.assertEqual(m["var_1d:historical:c99"], historical_var_es(series, confidence=0.99)[0])
        self.assertEqual(m["in_sample_max_drawdown"], maximum_drawdown(series))
        self.assertAlmostEqual(m["in_sample_beta"], 1.0, places=10)  # benchmark is this exact mix
        self.assertAlmostEqual(m["hhi"], 0.25)
        self.assertEqual(m["turnover_gross_vs_current"], 0.0)
        for key, value in out["reconciliation"].items():
            self.assertAlmostEqual(value, 1.0 if key == "weights_sum" else 0.0, places=12, msg=key)
        self.assertAlmostEqual(out["sectors"]["Banking"], 0.5)


class WalkForwardTests(unittest.TestCase):
    def test_hand_example_drift_and_costs(self):
        dates = pd.bdate_range("2025-01-01", periods=6)
        assets = pd.DataFrame({"A": [0.0, 0.0, 0.10, 0.0, -0.10, 0.0], "B": [0.0, 0.0, 0.0, 0.10, 0.0, 0.0]},
                              index=dates)
        bench = assets.mean(axis=1)
        equal = lambda window: pd.Series(0.5, index=window.columns)
        model = CostModel.from_config({"components": [{"name": "flat", "buy_bps": 10.0, "sell_bps": 10.0}],
                                       "gst_rate": 0.0})
        result = walk_forward(assets, bench, bench, {"equal": equal}, initial_weights=pd.Series({"A": 1.0, "B": 0.0}),
                              cost_model=model, estimation=2, holding=2)
        # t=2: trade 100/0 -> 50/50 (turnover 1.0, cost 0.001); r = 0.5 * 10% = 5%.
        # t=3: drifted weights A 0.55/1.05, B 0.5/1.05; r = 0.5/1.05 * 10%.
        # t=4: rebalance from drift (A 0.55/1.1, B 0.55/1.1 -> exactly 50/50, no trade); r = -5%.
        self.assertAlmostEqual(result.gross["equal"].iloc[0], 0.05, places=15)
        self.assertAlmostEqual(result.net["equal"].iloc[0], 0.999 * 1.05 - 1, places=15)
        self.assertAlmostEqual(result.gross["equal"].iloc[1], 0.5 / 1.05 * 0.10, places=15)
        self.assertAlmostEqual(result.turnover["equal"].iloc[2], 0.0, places=14)
        self.assertAlmostEqual(result.gross["equal"].iloc[2], -0.05, places=15)
        self.assertEqual(result.rebalance_dates, [f"{dates[2]:%Y-%m-%d}", f"{dates[4]:%Y-%m-%d}"])

    def test_no_look_ahead(self):
        rng = np.random.default_rng(5)
        assets = pd.DataFrame(rng.normal(0, 0.01, (120, 2)), columns=["A", "B"],
                              index=pd.bdate_range("2024-01-01", periods=120))
        inverse_vol = lambda window: (1 / window.std()) / (1 / window.std()).sum()
        base = walk_forward(assets, assets.mean(axis=1), assets.mean(axis=1), {"iv": inverse_vol},
                            initial_weights=pd.Series({"A": 0.5, "B": 0.5}),
                            cost_model=CostModel.from_config(ZERO), estimation=60, holding=20)
        shocked = assets.copy()
        shocked.iloc[60:] *= 5  # change only data on/after the first out-of-sample day
        other = walk_forward(shocked, shocked.mean(axis=1), shocked.mean(axis=1), {"iv": inverse_vol},
                             initial_weights=pd.Series({"A": 0.5, "B": 0.5}),
                             cost_model=CostModel.from_config(ZERO), estimation=60, holding=20)
        first = base.weights["iv"].iloc[0]
        np.testing.assert_array_equal(first.to_numpy(), other.weights["iv"].iloc[0].to_numpy())
        self.assertFalse(np.allclose(base.weights["iv"].iloc[1], other.weights["iv"].iloc[1]))

    def test_insufficient_data_is_reported(self):
        assets = pd.DataFrame(np.zeros((10, 2)), columns=["A", "B"], index=pd.bdate_range("2025-01-01", periods=10))
        result = walk_forward(assets, assets["A"], assets["A"], {}, initial_weights=pd.Series({"A": 1.0, "B": 0.0}),
                              cost_model=CostModel.from_config(ZERO))
        self.assertEqual(result.status, "insufficient_data")
        self.assertIn("10 valid returns", result.reason)


if __name__ == "__main__":
    unittest.main()
