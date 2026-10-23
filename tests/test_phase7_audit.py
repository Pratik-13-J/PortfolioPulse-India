"""Phase 7 focused end-to-end checks using manually verifiable data."""

from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from portfoliopulse.attribution import allocation_alerts, scenario_attribution
from portfoliopulse.source_verification import compare_daily_closes, parse_nse_index_csv
from portfoliopulse.data_quality import DataQualityError, validate_daily_candles
from portfoliopulse.portfolio_returns import PortfolioReturnError, build_buy_and_hold_returns
from portfoliopulse.risk_metrics import (
    annualized_volatility, drawdown_series, historical_var_es, sharpe_ratio,
    volatility_contributions,
)
from portfoliopulse.scenarios import scenario_shocks


def _candles() -> pd.DataFrame:
    rows = []
    for day, a, index in (
        ("2026-01-02", 100.0, 100.0),
        ("2026-01-05", 110.0, 101.0),
        ("2026-01-06", 121.0, 102.0),
    ):
        for symbol, close in (("A", a), ("NIFTY 50", index)):
            rows.append({
                "symbol": symbol, "date": day, "open": close, "high": close,
                "low": close, "close": close, "volume": 1000,
                "source": "hand-checkable fixture", "retrieved_at_utc": "2026-01-07T00:00:00+00:00",
                "price_basis": "unadjusted_ohlcv", "interval": "ONE_DAY",
            })
    return pd.DataFrame(rows)


class Phase7FinancialAuditTests(unittest.TestCase):
    def test_known_fixed_quantity_portfolio_return_and_benchmark_alignment(self) -> None:
        dates = pd.date_range("2026-01-02", periods=3, freq="B")
        prices = pd.DataFrame({
            "A": [100.0, 110.0, 121.0],
            "B": [100.0, 90.0, 99.0],
            "NIFTY 50": [100.0, 101.0, 102.0],
        }, index=dates)
        result = build_buy_and_hold_returns(prices, {"A": 2, "B": 1}, "NIFTY 50")
        self.assertAlmostEqual(result.portfolio_value_inr.iloc[0], 300.0)
        self.assertAlmostEqual(result.daily_comparison_returns.iloc[0]["portfolio"], 310 / 300 - 1)
        self.assertAlmostEqual(result.daily_comparison_returns.iloc[1]["portfolio"], 341 / 310 - 1)
        self.assertAlmostEqual(result.daily_comparison_returns.iloc[0]["NIFTY 50"], 0.01)
        np.testing.assert_allclose(
            result.daily_return_contributions.sum(axis=1),
            result.daily_comparison_returns["portfolio"],
        )

    def test_volatility_sharpe_drawdown_var_and_expected_shortfall(self) -> None:
        dates = pd.date_range("2026-01-01", periods=4)
        returns = pd.Series([0.02, -0.01, 0.01, -0.03], index=dates)
        sample_std = float(returns.std(ddof=1))
        self.assertAlmostEqual(annualized_volatility(returns), sample_std * math.sqrt(252))
        self.assertAlmostEqual(sharpe_ratio(returns), returns.mean() / sample_std * math.sqrt(252))
        drawdowns = drawdown_series(pd.Series([0.10, -0.20, 0.05], index=dates[:3]))
        self.assertAlmostEqual(float(drawdowns.min()), -0.20)
        tail = pd.Series([-0.10, -0.08, -0.04, 0.0, 0.03, 0.05, 0.06, 0.08, 0.10, 0.12], index=pd.date_range("2026-01-01", periods=10))
        self.assertEqual(historical_var_es(tail, confidence=0.80), (0.08, 0.09))

    def test_scenario_attribution_reconciles_and_rejects_boolean_shocks(self) -> None:
        holdings = [{"symbol": "A", "sector": "Energy"}, {"symbol": "B", "sector": "Banking"}]
        shocks = scenario_shocks(holdings, "individual", individual_shocks={"A": -0.10})
        rows, impact, pct = scenario_attribution({"A": 1000.0, "B": 3000.0}, shocks)
        self.assertAlmostEqual(impact, -100.0)
        self.assertAlmostEqual(rows["impact_inr"].sum(), impact)
        self.assertAlmostEqual(rows["portfolio_impact_pct"].sum(), pct)
        with self.assertRaisesRegex(ValueError, "Boolean"):
            scenario_shocks(holdings, "individual", individual_shocks={"A": np.bool_(True)})
        with self.assertRaisesRegex(ValueError, "Boolean"):
            scenario_attribution({"A": 100.0}, {"A": np.bool_(True)})

    def test_invalid_weights_missing_dates_duplicates_and_window_mismatch(self) -> None:
        assets = pd.DataFrame({"A": [0.01, -0.01, 0.02], "B": [-0.01, 0.01, -0.02]})
        with self.assertRaisesRegex(ValueError, "sum to one"):
            volatility_contributions(assets, [0.2, 0.2])
        with self.assertRaisesRegex(ValueError, "long-only"):
            volatility_contributions(assets, [-0.1, 1.1])

        grid = pd.DataFrame(
            {"A": [100.0, 110.0, np.nan, 121.0, 133.1, 146.41],
             "NIFTY 50": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]),
        )
        aligned = build_buy_and_hold_returns(grid, {"A": 1.0}, "NIFTY 50")
        self.assertNotIn(pd.Timestamp("2026-01-07"), aligned.daily_comparison_returns.index)
        with self.assertRaisesRegex(PortfolioReturnError, "unique"):
            build_buy_and_hold_returns(pd.concat([grid, grid.iloc[[0]]]), {"A": 1.0}, "NIFTY 50")

        candles = _candles()
        duplicate = pd.concat([candles, candles.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(DataQualityError, "Duplicate symbol/date"):
            validate_daily_candles(duplicate, ["A", "NIFTY 50"])
        windowed = validate_daily_candles(
            candles, ["A", "NIFTY 50"], start="2026-01-05", end_exclusive="2026-01-07",
        )
        self.assertEqual(windowed.outside_window_rows, 2)
        self.assertEqual(windowed.aligned_prices.index.strftime("%Y-%m-%d").tolist(), ["2026-01-05", "2026-01-06"])

    def test_allocation_alerts_reject_boolean_weights(self) -> None:
        with self.assertRaisesRegex(ValueError, "Boolean"):
            allocation_alerts({"A": np.bool_(True)}, {"A": 1.0})

    def test_source_comparison_classifies_matches_differences_and_missing_dates(self) -> None:
        provider = pd.Series([100.0, 101.0, 102.0], index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]))
        reference = pd.Series([100.0, 101.005, 103.0], index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-07"]))
        detail, summary = compare_daily_closes(provider, reference, tolerance_points=0.01)
        self.assertEqual(summary["overlap_count"], 2)
        self.assertEqual(summary["exact_match_count"], 1)
        self.assertEqual(summary["within_tolerance_count"], 2)
        self.assertEqual(summary["reference_missing_on_provider_dates"], 1)
        self.assertEqual(summary["provider_missing_on_reference_dates"], 1)
        self.assertEqual(detail.loc[pd.Timestamp("2026-01-06"), "status"], "reference_missing")
        self.assertEqual(detail.loc[pd.Timestamp("2026-01-07"), "status"], "provider_missing")
        self.assertAlmostEqual(detail.loc[pd.Timestamp("2026-01-05"), "relative_difference_pct"], (101.0 / 101.005 - 1.0) * 100.0)

    def test_source_comparison_rejects_invalid_prices_duplicate_dates_and_tolerance(self) -> None:
        index = pd.to_datetime(["2026-01-02", "2026-01-02"])
        with self.assertRaisesRegex(ValueError, "unique"):
            compare_daily_closes(pd.Series([100.0, 101.0], index=index), pd.Series([100.0], index=index[:1]))
        with self.assertRaisesRegex(ValueError, "positive and finite"):
            compare_daily_closes(pd.Series([0.0], index=index[:1]), pd.Series([100.0], index=index[:1]))
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            compare_daily_closes(pd.Series([100.0], index=index[:1]), pd.Series([100.0], index=index[:1]), tolerance_points=np.nan)

    def test_nse_csv_parser_normalizes_report_fields_and_rejects_bad_rows(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path

        scratch = Path.cwd() / ".tmp"
        scratch.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=scratch) as directory:
            csv = Path(directory) / "nse.csv"
            # A quoted numeric field is needed because the decimal uses thousands separators.
            csv.write_text('Index Date,Close Price\n02-Jan-2026,"25,500.25"\n', encoding="utf-8")
            parsed = parse_nse_index_csv(csv)
            self.assertEqual(parsed.index[0], pd.Timestamp("2026-01-02"))
            self.assertEqual(float(parsed.iloc[0]), 25500.25)
            csv.write_text("Date,Close\nnot-a-date,100\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid date or close"):
                parse_nse_index_csv(csv)
            csv.write_text("Index Name,Index Date,Close Price\nNIFTY 50 TRI,02-Jan-2026,25500.25\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only NIFTY 50 price-index"):
                parse_nse_index_csv(csv)


if __name__ == "__main__":
    unittest.main()
