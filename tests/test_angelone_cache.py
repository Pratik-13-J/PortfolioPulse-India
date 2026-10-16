"""Offline, mock-candle tests for Angel One's source-locked CSV cache."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from portfoliopulse.angelone.cache import (
    CandleCacheError,
    cache_identity,
    load_candles,
    save_candles,
)
from portfoliopulse.angelone.instrument_master import load_config, resolve
from portfoliopulse.config import PROJECT_ROOT


def fixture_identity():
    configured = load_config(PROJECT_ROOT / "config" / "instruments.json")[:2]
    master = [
        {"symbol": item.trading_symbol, "exch_seg": "NSE", "token": str(100001 + index)}
        for index, item in enumerate(configured)
    ]
    return cache_identity(resolve(configured, master), "2026-01-01", "2026-01-06")


def fixture_candles(identity):
    rows = []
    for symbol, trading_symbol, token, exchange in identity.instruments:
        for day, close in (("2026-01-02", 100.0), ("2026-01-05", 105.0)):
            rows.append({
                "symbol": symbol, "trading_symbol": trading_symbol, "token": token,
                "exchange": exchange,
                "timestamp_ist": pd.Timestamp(day + " 15:30:00", tz="Asia/Kolkata").as_unit("ns"),
                "date": pd.Timestamp(day).as_unit("ns"),
                "open": close - 1, "high": close + 2, "low": close - 2,
                "close": close, "volume": 0.0 if symbol == "NIFTY 50" else 1000.0,
                "source": "angel_one_smartapi",
                "retrieved_at_utc": "2026-10-16T10:00:00+00:00",
                "price_basis": "unadjusted_ohlcv", "interval": "ONE_DAY",
            })
    return pd.DataFrame(rows)


class CandleCacheTests(unittest.TestCase):
    def test_save_and_exact_offline_reload_preserve_candle_meaning(self) -> None:
        identity = fixture_identity()
        candles = fixture_candles(identity)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            path = Path(directory) / "angel_candles.csv"
            metadata = save_candles(candles, path, identity)
            loaded = load_candles(path, identity)
            self.assertEqual(metadata["source"], "angel_one_smartapi")
            self.assertEqual(metadata["price_basis"], "unadjusted_ohlcv")
            self.assertEqual(metadata["row_count"], 4)
            self.assertEqual(len(loaded.candles), 4)
            self.assertEqual(str(loaded.candles["timestamp_ist"].dt.tz), "Asia/Kolkata")
            self.assertTrue((loaded.candles["volume"] == 0).any())
            self.assertEqual(loaded.metadata["csv_sha256"], metadata["csv_sha256"])
            expected = candles.loc[:, loaded.candles.columns].sort_values(["date", "symbol"]).reset_index(drop=True)
            pd.testing.assert_frame_equal(loaded.candles, expected)

    def test_wrong_token_symbol_window_or_basis_rejected(self) -> None:
        identity = fixture_identity()
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            path = Path(directory) / "angel_candles.csv"
            save_candles(fixture_candles(identity), path, identity)
            first = identity.instruments[0]
            changed_token = (first[0], first[1], "999999", first[3])
            cases = [
                replace(identity, instruments=(changed_token, *identity.instruments[1:])),
                replace(identity, start="2025-12-31"),
                replace(identity, end_exclusive="2026-01-07"),
                replace(identity, price_basis="adjusted_close"),
                replace(identity, source="yfinance"),
            ]
            for expected in cases:
                with self.subTest(expected=expected), self.assertRaises(CandleCacheError):
                    load_candles(path, expected)

    def test_csv_tampering_and_missing_metadata_fail(self) -> None:
        identity = fixture_identity()
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            path = Path(directory) / "angel_candles.csv"
            save_candles(fixture_candles(identity), path, identity)
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(CandleCacheError, "SHA-256"):
                load_candles(path, identity)
            path.unlink()
            with self.assertRaisesRegex(CandleCacheError, "No readable"):
                load_candles(path, identity)

    def test_invalid_ohlcv_dates_duplicate_and_wrong_source_fail_before_write(self) -> None:
        identity = fixture_identity()
        valid = fixture_candles(identity)
        cases = []
        bad_price = valid.copy()
        bad_price.loc[0, "low"] = 200.0
        cases.append(bad_price)
        duplicate = pd.concat([valid, valid.iloc[[0]]], ignore_index=True)
        cases.append(duplicate)
        wrong_date = valid.copy()
        wrong_date.loc[0, "date"] = pd.Timestamp("2026-01-03")
        cases.append(wrong_date)
        out_of_range = valid.copy()
        out_of_range.loc[0, "date"] = pd.Timestamp("2026-01-06")
        out_of_range.loc[0, "timestamp_ist"] = pd.Timestamp("2026-01-06 15:30:00", tz="Asia/Kolkata")
        cases.append(out_of_range)
        wrong_source = valid.copy()
        wrong_source.loc[0, "source"] = "synthetic_india_demo_v1"
        cases.append(wrong_source)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            path = Path(directory) / "angel_candles.csv"
            for bad in cases:
                with self.subTest(bad=bad), self.assertRaises(CandleCacheError):
                    save_candles(bad, path, identity)
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
