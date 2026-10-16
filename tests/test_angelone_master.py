"""API-independent tests for Angel One scrip-master resolution."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from portfoliopulse.angelone.instrument_master import (
    MASTER_URL,
    InstrumentMasterError,
    fetch_master,
    load_cached_master,
    load_config,
    resolve,
)
from portfoliopulse.config import PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "config" / "instruments.json"


def mock_master(entries):
    # These numeric values are test fixtures, never actual Angel One tokens.
    return [
        {"symbol": entry.trading_symbol, "exch_seg": "NSE", "token": str(index + 100001)}
        for index, entry in enumerate(entries)
    ]


class InstrumentMasterTests(unittest.TestCase):
    def test_project_config_contains_explicit_nse_symbols_without_tokens(self) -> None:
        entries = load_config(CONFIG_PATH)
        self.assertEqual(len(entries), 6)
        self.assertEqual([entry.symbol for entry in entries],
                         ["NIFTY 50", "RELIANCE", "HDFCBANK", "INFY", "ICICIBANK", "ITC"])
        self.assertTrue(all(entry.exchange == "NSE" for entry in entries))
        self.assertTrue(all(not hasattr(entry, "token") for entry in entries))
        self.assertAlmostEqual(sum(entry.target_weight for entry in entries), 1.0)

    def test_exact_resolution_preserves_config_and_master_token(self) -> None:
        entries = load_config(CONFIG_PATH)
        resolved = resolve(entries, mock_master(entries))
        self.assertEqual([item.token for item in resolved], [str(n) for n in range(100001, 100007)])
        self.assertEqual(resolved[0].trading_symbol, "Nifty 50")
        self.assertTrue(resolved[0].is_benchmark)
        self.assertEqual(resolved[1].quantity, 20)
        self.assertEqual(resolved[1].sector, "Energy")

    def test_missing_wrong_exchange_ambiguous_and_invalid_tokens_fail(self) -> None:
        entries = load_config(CONFIG_PATH)
        valid = mock_master(entries)
        cases = [
            valid[:-1],
            [{**valid[0], "exch_seg": "BSE"}, *valid[1:]],
            [*valid, valid[0]],
            [{**valid[0], "token": ""}, *valid[1:]],
        ]
        for master in cases:
            with self.subTest(master=master), self.assertRaises(InstrumentMasterError):
                resolve(entries, master)

    def test_config_rejects_invented_or_embedded_token(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            path = Path(directory) / "instruments.json"
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config["holdings"][0]["token"] = "123"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(InstrumentMasterError, "tokens"):
                load_config(path)

    def test_mock_fetch_cache_and_checksum(self) -> None:
        entries = load_config(CONFIG_PATH)
        payload = json.dumps(mock_master(entries)).encode("utf-8")
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            cache = Path(directory) / "instrument_master.json"

            def getter(url):
                self.assertEqual(url, MASTER_URL)
                return payload

            live = fetch_master(cache, getter=getter)
            self.assertFalse(live.from_cache)
            self.assertEqual(len(live.entries), 6)
            self.assertTrue(cache.is_file())

            def unavailable(_url):
                raise ConnectionError("offline")

            cached = fetch_master(cache, getter=unavailable)
            self.assertTrue(cached.from_cache)
            self.assertEqual(cached.retrieved_at_utc, live.retrieved_at_utc)
            self.assertEqual(resolve(entries, cached.entries), resolve(entries, live.entries))

            cache.write_bytes(payload + b" ")
            with self.assertRaisesRegex(InstrumentMasterError, "checksum"):
                fetch_master(cache, getter=unavailable)

    def test_failed_fetch_without_snapshot_fails_explicitly(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            with self.assertRaisesRegex(InstrumentMasterError, "no valid local snapshot"):
                fetch_master(Path(directory) / "missing.json", getter=lambda _url: b"not-json")

    def test_explicit_offline_master_load_never_uses_network(self) -> None:
        entries = load_config(CONFIG_PATH)
        payload = json.dumps(mock_master(entries)).encode("utf-8")
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            cache = Path(directory) / "instrument_master.json"
            fetch_master(cache, getter=lambda _url: payload)
            with patch(
                "portfoliopulse.angelone.instrument_master._download_master",
                side_effect=AssertionError("network called"),
            ):
                snapshot = load_cached_master(cache)
            self.assertTrue(snapshot.from_cache)
            self.assertEqual(len(resolve(entries, snapshot.entries)), 6)


if __name__ == "__main__":
    unittest.main()
