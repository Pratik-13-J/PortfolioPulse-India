"""Orchestration checks for the source-labelled Angel One refresh command."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from portfoliopulse.angelone.cache import cache_identity, load_candles, save_candles
from portfoliopulse.angelone.instrument_master import fetch_master, load_config, resolve
from portfoliopulse.config import PROJECT_ROOT
from scripts import refresh_angelone


START, END = "2026-01-01", "2026-01-06"


def sample_candles(instruments):
    rows = []
    for item in instruments:
        for day in ("2026-01-02", "2026-01-05"):
            rows.append({
                "symbol": item.symbol, "trading_symbol": item.trading_symbol,
                "token": item.token, "exchange": item.exchange,
                "timestamp_ist": pd.Timestamp(day + " 15:30", tz="Asia/Kolkata"),
                "date": pd.Timestamp(day), "open": 100.0, "high": 102.0,
                "low": 99.0, "close": 101.0,
                "volume": 0.0 if item.is_benchmark else 100.0,
                "source": "angel_one_smartapi",
                "retrieved_at_utc": "2026-10-16T00:00:00+00:00",
                "price_basis": "unadjusted_ohlcv", "interval": "ONE_DAY",
            })
    return pd.DataFrame(rows)


class AngelOneRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        root = Path(self.temporary.name)
        self.master_path = root / "raw" / "instrument_master.json"
        self.candle_path = root / "processed" / "angel_candles.csv"
        self.raw_dir = root / "raw" / "angelone"
        configured = load_config(PROJECT_ROOT / "config" / "instruments.json")
        master_rows = [
            {"symbol": item.trading_symbol, "exch_seg": "NSE", "token": str(100001 + index)}
            for index, item in enumerate(configured)
        ]
        self.snapshot = fetch_master(self.master_path, getter=lambda _url: json.dumps(master_rows).encode())
        self.instruments = resolve(configured, self.snapshot.entries)
        self.identity = cache_identity(self.instruments, START, END)
        self.candles = sample_candles(self.instruments)
        self.patches = [
            patch.object(refresh_angelone, "MASTER_PATH", self.master_path),
            patch.object(refresh_angelone, "CANDLE_PATH", self.candle_path),
            patch.object(refresh_angelone, "RAW_CANDLE_DIR", self.raw_dir),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self) -> None:
        for active in reversed(self.patches):
            active.stop()
        self.temporary.cleanup()

    def test_offline_uses_recorded_token_mapping_without_master_or_network(self) -> None:
        save_candles(self.candles, self.candle_path, self.identity)
        self.master_path.unlink()
        self.master_path.with_name(self.master_path.name + ".meta.json").unlink()
        with patch("portfoliopulse.angelone.instrument_master._download_master", side_effect=AssertionError("network used")):
            rows, used_fallback, cached_master = refresh_angelone.refresh(START, END, offline=True)
        self.assertEqual((rows, used_fallback, cached_master), (12, True, True))
        with self.assertRaisesRegex(ValueError, "match"):
            refresh_angelone.refresh(START, "2026-01-07", offline=True)

    def test_failed_authentication_uses_only_matching_saved_angel_candles(self) -> None:
        from portfoliopulse.angelone.client import AngelOneDataError

        save_candles(self.candles, self.candle_path, self.identity)
        with patch.object(refresh_angelone, "fetch_master", return_value=self.snapshot), \
             patch("portfoliopulse.angelone.client.authenticate_from_environment", side_effect=AngelOneDataError("credentials absent")):
            rows, used_fallback, cached_master = refresh_angelone.refresh(START, END)
        self.assertEqual((rows, used_fallback, cached_master), (12, True, True))
        self.assertFalse(self.raw_dir.exists())

    def test_stale_master_never_starts_a_new_candle_request(self) -> None:
        save_candles(self.candles, self.candle_path, self.identity)
        stale = replace(self.snapshot, from_cache=True)
        with patch.object(refresh_angelone, "fetch_master", return_value=stale), \
             patch("portfoliopulse.angelone.client.authenticate_from_environment", side_effect=AssertionError("used stale tokens")):
            rows, used_fallback, cached_master = refresh_angelone.refresh(START, END)
        self.assertEqual((rows, used_fallback, cached_master), (12, True, True))

    def test_changed_current_master_keeps_older_valid_candle_cache_available(self) -> None:
        from portfoliopulse.angelone.client import AngelOneDataError

        save_candles(self.candles, self.candle_path, self.identity)
        configured = load_config(PROJECT_ROOT / "config" / "instruments.json")
        new_master_rows = [
            {"symbol": item.trading_symbol, "exch_seg": "NSE", "token": str(200001 + index)}
            for index, item in enumerate(configured)
        ]
        new_snapshot = fetch_master(self.master_path, getter=lambda _url: json.dumps(new_master_rows).encode())
        with patch.object(refresh_angelone, "fetch_master", return_value=new_snapshot), \
             patch("portfoliopulse.angelone.client.authenticate_from_environment", side_effect=AngelOneDataError("credentials absent")):
            rows, used_fallback, stored_mapping = refresh_angelone.refresh(START, END)
        self.assertEqual((rows, used_fallback, stored_mapping), (12, True, True))
        self.assertEqual(len(load_candles(self.candle_path, self.identity).candles), 12)

    def test_mock_online_candles_save_csv_and_candle_only_raw_batches(self) -> None:
        def fake_fetch(_client, _instruments, _start, _end, *, raw_batches):
            raw_batches.append({"source": "angel_one_smartapi", "data": [["2026-01-02T15:30:00+05:30", 100, 102, 99, 101, 100]]})
            return self.candles

        with patch.object(refresh_angelone, "fetch_master", return_value=self.snapshot), \
             patch("portfoliopulse.angelone.client.authenticate_from_environment", return_value=object()), \
             patch("portfoliopulse.angelone.client.fetch_daily_candles", side_effect=fake_fetch):
            rows, used_fallback, cached_master = refresh_angelone.refresh(START, END)
        self.assertEqual((rows, used_fallback, cached_master), (12, False, False))
        self.assertEqual(len(load_candles(self.candle_path, self.identity).candles), 12)
        raw_files = list(self.raw_dir.glob("*.json"))
        self.assertEqual(len(raw_files), 1)
        self.assertEqual(json.loads(raw_files[0].read_text(encoding="utf-8"))[0]["source"], "angel_one_smartapi")

    def test_raw_write_failure_is_reported_without_false_fallback_label(self) -> None:
        with patch.object(refresh_angelone, "fetch_master", return_value=self.snapshot), \
             patch("portfoliopulse.angelone.client.authenticate_from_environment", return_value=object()), \
             patch("portfoliopulse.angelone.client.fetch_daily_candles", return_value=self.candles), \
             patch.object(refresh_angelone, "_save_raw_candle_batches", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "raw candle batches could not be preserved"):
                refresh_angelone.refresh(START, END)
        self.assertEqual(len(load_candles(self.candle_path, self.identity).candles), 12)


if __name__ == "__main__":
    unittest.main()
