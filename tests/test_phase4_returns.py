"""Small, hand-checkable buy-and-hold and benchmark return examples."""

from __future__ import annotations

import unittest

import pandas as pd

from portfoliopulse.portfolio_returns import (
    PortfolioReturnError, annualize_endpoint_price_return, build_buy_and_hold_returns,
)


def grid_with_gap() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "A": [100, 110, 121, 133.1, 146.41],
            "B": [100, 90, 99, 108.9, 119.79],
            "NIFTY 50": [100, 105, float("nan"), 115, 120],
        },
        index=pd.to_datetime([
            "2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
        ]),
    )


class BuyAndHoldReturnTests(unittest.TestCase):
    def test_fixed_quantities_make_weights_drift_and_daily_contributions_reconcile(self) -> None:
        prices = grid_with_gap().iloc[:2].copy()
        prices.loc[pd.Timestamp("2026-01-05"), :] = [121, 99, 115]
        series = build_buy_and_hold_returns(prices, {"A": 2, "B": 1}, "NIFTY 50")

        self.assertEqual(series.portfolio_value_inr.tolist(), [300, 310, 341])
        self.assertAlmostEqual(series.market_weights.loc["2026-01-02", "A"], 220 / 310)
        self.assertAlmostEqual(series.market_weights.loc["2026-01-05", "A"], 242 / 341)
        self.assertAlmostEqual(series.daily_comparison_returns.loc["2026-01-02", "portfolio"], 310 / 300 - 1)
        self.assertAlmostEqual(series.daily_comparison_returns.loc["2026-01-05", "portfolio"], 0.1)
        self.assertAlmostEqual(series.daily_comparison_returns.loc["2026-01-05", "NIFTY 50"], 115 / 105 - 1)
        self.assertAlmostEqual(series.daily_return_contributions.loc["2026-01-02", "A"], (2 / 3) * 0.1)
        self.assertAlmostEqual(series.daily_return_contributions.loc["2026-01-02", "B"], (1 / 3) * -0.1)
        pd.testing.assert_series_equal(
            series.daily_return_contributions.sum(axis=1),
            series.daily_comparison_returns["portfolio"],
            check_names=False,
        )
        self.assertAlmostEqual(series.cumulative_comparison_returns.iloc[-1]["portfolio"], 341 / 300 - 1)
        self.assertAlmostEqual(series.endpoint_annualized_portfolio_return, (341 / 300) ** (252 / 2) - 1)
        self.assertEqual(series.observed_union_intervals, 2)

    def test_missing_benchmark_never_creates_a_bridged_daily_return(self) -> None:
        series = build_buy_and_hold_returns(grid_with_gap(), {"A": 2, "B": 1}, "NIFTY 50")
        self.assertEqual(
            series.daily_comparison_returns.index.strftime("%Y-%m-%d").tolist(),
            ["2026-01-02", "2026-01-07"],
        )
        self.assertEqual(
            series.excluded_return_dates,
            ("2026-01-01", "2026-01-05", "2026-01-06"),
        )
        self.assertTrue(series.cumulative_comparison_returns.loc["2026-01-05"].isna().all())
        self.assertAlmostEqual(series.cumulative_portfolio_return.loc["2026-01-05"], 341 / 300 - 1)
        self.assertAlmostEqual(series.market_weights.loc["2026-01-05", "A"], 242 / 341)
        self.assertAlmostEqual(
            series.cumulative_comparison_returns.loc["2026-01-06", "portfolio"],
            375.1 / 300 - 1,
        )
        self.assertAlmostEqual(
            series.endpoint_annualized_benchmark_return,
            (120 / 100) ** (252 / 4) - 1,
        )
        self.assertEqual(series.observed_union_intervals, 4)

    def test_invalid_inputs_and_insufficient_history_are_explicit(self) -> None:
        good = grid_with_gap()
        with self.assertRaisesRegex(PortfolioReturnError, "positive"):
            build_buy_and_hold_returns(good, {"A": 0, "B": 1}, "NIFTY 50")
        bad = good.copy()
        bad.iloc[0, 0] = -1
        with self.assertRaisesRegex(PortfolioReturnError, "positive"):
            build_buy_and_hold_returns(bad, {"A": 2, "B": 1}, "NIFTY 50")
        with self.assertRaisesRegex(PortfolioReturnError, "unique"):
            build_buy_and_hold_returns(pd.concat([good, good.iloc[[0]]]), {"A": 2, "B": 1}, "NIFTY 50")
        with self.assertRaisesRegex(PortfolioReturnError, "two valid"):
            build_buy_and_hold_returns(good.iloc[:2], {"A": 2, "B": 1}, "NIFTY 50")
        with self.assertRaises(PortfolioReturnError):
            annualize_endpoint_price_return(100, 110, 0)


if __name__ == "__main__":
    unittest.main()
