"""Hand-checkable Phase 3 daily-candle quality checks."""

from __future__ import annotations

import json
import unittest

import pandas as pd

from portfoliopulse.data_quality import DataQualityError, validate_daily_candles


def _candles() -> pd.DataFrame:
    rows = []
    prices = {
        "2026-01-01": {"NIFTY 50": 100.0, "ABC": 10.0},
        "2026-01-02": {"NIFTY 50": 110.0, "ABC": 11.0},
        "2026-01-05": {"NIFTY 50": 121.0},  # ABC is genuinely missing.
        "2026-01-06": {"NIFTY 50": 133.1, "ABC": 12.1},
        "2026-01-07": {"NIFTY 50": 146.41, "ABC": 13.31},
    }
    for day, values in prices.items():
        for symbol, close in values.items():
            rows.append({
                "symbol": symbol,
                "trading_symbol": "Nifty 50" if symbol == "NIFTY 50" else "ABC-EQ",
                "exchange": "NSE",
                "date": day,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 0 if symbol == "NIFTY 50" else 100,
                "source": "sourced_csv_test",
                "retrieved_at_utc": "2026-01-08T00:00:00+00:00",
                "price_basis": "unadjusted_ohlcv",
                "interval": "ONE_DAY",
            })
    return pd.DataFrame(rows)


class DailyCandleQualityTests(unittest.TestCase):
    def test_missing_session_is_reported_without_bridging_returns(self) -> None:
        report = validate_daily_candles(
            _candles(), ["NIFTY 50", "ABC"], start="2026-01-01", end_exclusive="2026-01-08",
            expected_price_basis="unadjusted_ohlcv",
        )
        self.assertEqual(report.union_dates_count, 5)
        self.assertEqual(report.common_price_dates_count, 4)
        self.assertEqual(report.valid_return_dates_count, 2)
        self.assertEqual(report.daily_returns.index.strftime("%Y-%m-%d").tolist(),
                         ["2026-01-02", "2026-01-07"])
        self.assertAlmostEqual(report.daily_returns.loc["2026-01-02", "ABC"], 0.1)
        self.assertAlmostEqual(report.daily_returns.loc["2026-01-07", "ABC"], 0.1)
        self.assertEqual(report.missing_dates_by_symbol["ABC"], ("2026-01-05",))
        self.assertEqual(report.excluded_price_dates["date"].tolist(), ["2026-01-05"])
        self.assertEqual(report.excluded_return_dates["date"].tolist(),
                         ["2026-01-01", "2026-01-05", "2026-01-06"])
        self.assertEqual(report.excluded_return_dates["reason"].tolist(), [
            "no_previous_observed_date", "missing_current_candle", "missing_previous_candle",
        ])
        self.assertEqual(report.to_summary()["coverage"], [
            {"symbol": "NIFTY 50", "observations": 5, "missing_on_union_dates": 0},
            {"symbol": "ABC", "observations": 4, "missing_on_union_dates": 1},
        ])
        self.assertIn('"excluded_return_date_count": 3', json.dumps(report.to_summary()))

    def test_window_excludes_rows_and_does_not_generate_holidays(self) -> None:
        report = validate_daily_candles(
            _candles(), ["NIFTY 50", "ABC"], start="2026-01-02", end_exclusive="2026-01-08"
        )
        self.assertEqual(report.outside_window_rows, 2)
        self.assertEqual(report.union_dates_count, 4)
        self.assertNotIn("2026-01-03", report.excluded_price_dates["date"].tolist())
        self.assertEqual(report.valid_return_dates_count, 1)
        with self.assertRaisesRegex(DataQualityError, "No consecutive shared return dates"):
            validate_daily_candles(_candles(), ["NIFTY 50", "ABC"],
                                   start="2026-01-02", end_exclusive="2026-01-07")

    def test_datetime_dates_from_both_importers(self) -> None:
        candles = _candles()
        candles["date"] = pd.to_datetime(candles["date"])
        candles["token"] = "1234"  # Angel-specific extra is retained.
        report = validate_daily_candles(candles, ["NIFTY 50", "ABC"])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(report.candles["date"]))
        self.assertIn("token", report.candles)

    def test_duplicate_and_invalid_ohlcv_rejected(self) -> None:
        base = _candles()
        cases = [
            (pd.concat([base, base.iloc[[0]]], ignore_index=True), "Duplicate symbol/date"),
            (base.assign(close=lambda frame: frame["close"].mask(frame.index == 0, 0)), "positive"),
            (base.assign(volume=lambda frame: frame["volume"].mask(frame.index == 0, -1)), "non-negative"),
            (base.assign(high=lambda frame: frame["high"].mask(frame.index == 0, 1)), "OHLC"),
            (base.assign(low=lambda frame: frame["low"].mask(frame.index == 0, float("inf"))), "finite"),
        ]
        for candles, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(DataQualityError, message):
                validate_daily_candles(candles, ["NIFTY 50", "ABC"])

    def test_price_basis_and_provenance_must_be_consistent(self) -> None:
        base = _candles()
        mixed = base.copy()
        mixed.loc[0, "price_basis"] = "provider_adjusted_ohlcv"
        with self.assertRaisesRegex(DataQualityError, "consistent price basis"):
            validate_daily_candles(mixed, ["NIFTY 50", "ABC"])
        with self.assertRaisesRegex(DataQualityError, "declared dataset metadata"):
            validate_daily_candles(base, ["NIFTY 50", "ABC"],
                                   expected_price_basis="provider_adjusted_ohlcv")
        mixed = base.copy()
        mixed.loc[0, "source"] = "another_provider"
        with self.assertRaisesRegex(DataQualityError, "consistent source"):
            validate_daily_candles(mixed, ["NIFTY 50", "ABC"])
        missing_time = base.copy()
        missing_time.loc[0, "retrieved_at_utc"] = "2026-01-08T05:30:00+05:30"
        with self.assertRaisesRegex(DataQualityError, "must be in UTC"):
            validate_daily_candles(missing_time, ["NIFTY 50", "ABC"])

    def test_missing_price_column_and_impossible_overlap(self) -> None:
        base = _candles()
        with self.assertRaisesRegex(DataQualityError, "Missing candle columns: close"):
            validate_daily_candles(base.drop(columns="close"), ["NIFTY 50", "ABC"])
        short = base[base["date"].isin(["2026-01-05", "2026-01-06"])].copy()
        with self.assertRaisesRegex(DataQualityError, "At least two shared price dates"):
            validate_daily_candles(short, ["NIFTY 50", "ABC"])


if __name__ == "__main__":
    unittest.main()
