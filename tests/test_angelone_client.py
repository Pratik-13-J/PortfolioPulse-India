"""API-independent checks for Angel One daily candle ingestion."""

from __future__ import annotations

from datetime import date, timedelta
import io
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

import pandas as pd

from portfoliopulse.angelone.cache import cache_identity, load_candles, save_candles
from portfoliopulse.angelone.client import (
    AngelOneDataError,
    CANDLE_COLUMNS,
    PRICE_BASIS,
    SOURCE,
    authenticate_from_environment,
    fetch_daily_candles,
)
from portfoliopulse.angelone.instrument_master import ResolvedInstrument
from portfoliopulse.config import PROJECT_ROOT


def instrument(symbol: str = "RELIANCE", token: str = "100001", *, benchmark: bool = False) -> ResolvedInstrument:
    return ResolvedInstrument(
        symbol=symbol,
        trading_symbol="Nifty 50" if benchmark else f"{symbol}-EQ",
        exchange="NSE",
        token=token,
        asset_class="Benchmark" if benchmark else "Equity",
        sector="Broad Market" if benchmark else "Energy",
        quantity=0 if benchmark else 20,
        target_weight=0 if benchmark else 1,
        is_benchmark=benchmark,
    )


def candle(day: str, *, volume: float = 1000) -> list[object]:
    return [f"{day}T09:15:00+05:30", 100, 106, 99, 105, volume]


class FakeClient:
    def __init__(self, responses: list[object]):
        self.responses = iter(responses)
        self.calls: list[dict[str, str]] = []

    def getCandleData(self, params: dict[str, str]):
        self.calls.append(params.copy())
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class AngelOneClientTests(unittest.TestCase):
    def test_normalized_candles_keep_holiday_gap_zero_index_volume_and_metadata(self) -> None:
        stock = instrument()
        benchmark = instrument("NIFTY 50", "100002", benchmark=True)
        client = FakeClient([
            {"status": True, "data": [candle("2026-01-02"), candle("2026-01-05")]},
            {"status": True, "data": [candle("2026-01-02", volume=0), candle("2026-01-05", volume=0)]},
        ])
        raw_batches = []
        spacing = []
        result = fetch_daily_candles(client, [stock, benchmark], "2026-01-01", "2026-01-06",
                                     raw_batches=raw_batches, sleeper=spacing.append)
        self.assertEqual(result.columns.tolist(), list(CANDLE_COLUMNS))
        self.assertEqual(len(result), 4)
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").unique().tolist(),
                         ["2026-01-02", "2026-01-05"])
        self.assertEqual(str(result["timestamp_ist"].dt.tz), "Asia/Kolkata")
        self.assertEqual(set(result.loc[result["symbol"] == "NIFTY 50", "volume"]), {0.0})
        self.assertEqual(set(result["source"]), {SOURCE})
        self.assertEqual(set(result["price_basis"]), {PRICE_BASIS})
        self.assertEqual(set(result["interval"]), {"ONE_DAY"})
        self.assertEqual(client.calls[0], {
            "exchange": "NSE", "symboltoken": "100001", "interval": "ONE_DAY",
            "fromdate": "2026-01-01 00:00", "todate": "2026-01-05 23:59",
        })
        self.assertEqual(len(raw_batches), 2)
        self.assertEqual(spacing, [10.0])
        self.assertEqual(raw_batches[0]["data"], [candle("2026-01-02"), candle("2026-01-05")])
        self.assertFalse(any(key in raw_batches[0] for key in ("headers", "authentication", "jwtToken", "message")))
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            identity = cache_identity([stock, benchmark], "2026-01-01", "2026-01-06")
            path = Path(directory) / "candles.csv"
            save_candles(result, path, identity)
            cached = load_candles(path, identity).candles
            pd.testing.assert_frame_equal(result, cached, check_freq=False)

    def test_requests_are_chunked_at_two_thousand_calendar_days(self) -> None:
        first = date(2020, 1, 1)
        stop = first + timedelta(days=2001)

        class ChunkClient:
            def __init__(self):
                self.calls = []

            def getCandleData(self, params):
                self.calls.append(params.copy())
                return {"status": True, "data": [candle(params["fromdate"][:10])]}

        client = ChunkClient()
        result = fetch_daily_candles(client, [instrument()], first.isoformat(), stop.isoformat(),
                                     request_delay_seconds=0)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["fromdate"], "2020-01-01 00:00")
        self.assertEqual(client.calls[0]["todate"], f"{(first + timedelta(days=1999)).isoformat()} 23:59")
        self.assertEqual(client.calls[1]["fromdate"], f"{(first + timedelta(days=2000)).isoformat()} 00:00")
        self.assertEqual(client.calls[1]["todate"], f"{(stop - timedelta(days=1)).isoformat()} 23:59")
        self.assertEqual(len(result), 2)

    def test_transient_failures_retry_without_exposing_exception_text(self) -> None:
        waits = []
        client = FakeClient([TimeoutError("SECRET IN ERROR"), {"status": True, "data": [candle("2026-01-02")]}])
        result = fetch_daily_candles(
            client, [instrument()], "2026-01-01", "2026-01-06",
            max_attempts=2, retry_wait_seconds=0.25, sleeper=waits.append,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(waits, [0.25])
        rejected = FakeClient([{"status": False, "errorcode": "AB1021", "message": "Too many requests"},
                               {"status": True, "data": [candle("2026-01-02")]}])
        fetch_daily_candles(rejected, [instrument()], "2026-01-01", "2026-01-06",
                            max_attempts=2, retry_wait_seconds=0, sleeper=waits.append)
        self.assertEqual(len(rejected.calls), 2)
        self.assertEqual(waits[-1], 15.0)
        failed = FakeClient([RuntimeError("SECRET IN ERROR")])
        with self.assertRaises(AngelOneDataError) as context:
            fetch_daily_candles(failed, [instrument()], "2026-01-01", "2026-01-06",
                                retry_wait_seconds=0, sleeper=waits.append)
        self.assertNotIn("SECRET", str(context.exception))
        self.assertEqual(len(failed.calls), 1)

    def test_sdk_access_rate_exception_uses_long_retry_and_sanitized_error(self) -> None:
        class DataException(Exception):
            code = 502

        message = "Access denied because of exceeding access rate; SECRET SESSION DATA"
        waits: list[float] = []
        client = FakeClient([
            DataException(message),
            {"status": True, "data": [candle("2026-01-02")]},
        ])
        result = fetch_daily_candles(
            client, [instrument()], "2026-01-01", "2026-01-06",
            max_attempts=2, retry_wait_seconds=0.25, sleeper=waits.append,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(waits, [15.0])

        blocked = FakeClient([DataException(message), DataException(message)])
        waits.clear()
        with self.assertRaises(AngelOneDataError) as context:
            fetch_daily_candles(
                blocked, [instrument()], "2026-01-01", "2026-01-06",
                max_attempts=2, retry_wait_seconds=0.25, sleeper=waits.append,
            )
        self.assertEqual(waits, [15.0])
        self.assertIn("access-rate limit", str(context.exception))
        self.assertNotIn("SECRET", str(context.exception))

    def test_invalid_duplicate_and_out_of_window_candles_are_rejected(self) -> None:
        invalid = [
            [candle("2026-01-02"), candle("2026-01-02")],
            [["bad timestamp", 100, 106, 99, 105, 1000]],
            [["01/02/2026 09:15", 100, 106, 99, 105, 1000]],
            [["2026/01/02 09:15", 100, 106, 99, 105, 1000]],
            [candle("2026-01-06")],
            [["2026-01-02T09:15:00+05:30", 100, 104, 99, 105, 1000]],
            [["2026-01-02T09:15:00+05:30", 100, 106, 99, 105, -1]],
            [["2026-01-02T09:15:00+05:30", 100, 106, 99, 0, 1000]],
        ]
        for data in invalid:
            raw_batches = []
            with self.subTest(data=data), self.assertRaises(AngelOneDataError):
                fetch_daily_candles(FakeClient([{"status": True, "data": data}]),
                                    [instrument()], "2026-01-01", "2026-01-06",
                                    raw_batches=raw_batches)
            self.assertEqual(raw_batches, [])

    def test_utc_and_naive_iso_timestamps_map_to_ist_calendar_dates(self) -> None:
        raw = [
            ["2026-01-02T03:45:00Z", 100, 106, 99, 105, 1000],
            ["2026-01-05 09:15", 101, 107, 100, 106, 1000],
        ]
        result = fetch_daily_candles(FakeClient([{"status": True, "data": raw}]),
                                     [instrument()], "2026-01-01", "2026-01-06")
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").tolist(),
                         ["2026-01-02", "2026-01-05"])
        self.assertEqual(result["timestamp_ist"].dt.strftime("%H:%M").tolist(),
                         ["09:15", "09:15"])

    def test_failed_symbol_and_rejected_response_are_explicit_and_sanitized(self) -> None:
        for response in (
            {"status": True, "data": []},
            {"status": False, "message": "SECRET SESSION DATA", "errorcode": "AB1004"},
            {"status": True, "data": "invalid"},
        ):
            with self.subTest(response=response), self.assertRaises(AngelOneDataError) as context:
                fetch_daily_candles(FakeClient([response]), [instrument()],
                                    "2026-01-01", "2026-01-06")
            self.assertNotIn("SECRET", str(context.exception))

    def test_sdk_error_logger_is_suppressed_during_candle_request(self) -> None:
        import logzero

        sdk_logger = logzero.logger
        previous_disabled = sdk_logger.disabled
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        sdk_logger.addHandler(handler)

        class LoggingClient:
            def getCandleData(self, _params):
                sdk_logger.error("SECRET AUTH HEADER")
                return {"status": True, "data": [candle("2026-01-02")]}

        try:
            fetch_daily_candles(LoggingClient(), [instrument()], "2026-01-01", "2026-01-06")
            self.assertEqual(output.getvalue(), "")
        finally:
            sdk_logger.removeHandler(handler)
            sdk_logger.disabled = previous_disabled

    def test_authentication_uses_environment_without_returning_session_response(self) -> None:
        import logzero

        smartapi = ModuleType("SmartApi")
        pyotp = ModuleType("pyotp")
        dotenv = ModuleType("dotenv")
        dotenv.load_dotenv = lambda **_: None
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        sdk_loggers = (logzero.logger, logging.getLogger("SmartApi.smartConnect"))
        previous_disabled = [logger.disabled for logger in sdk_loggers]
        for logger in sdk_loggers:
            logger.disabled = False
            logger.addHandler(handler)

        class FakeSmartConnect:
            def __init__(self, *, api_key):
                for logger in sdk_loggers:
                    logger.error("SECRET AUTH HEADER")
                self.api_key = api_key

            def generateSession(self, client_id, password, one_time_password):
                self.seen = (client_id, password, one_time_password)
                return {"status": True, "data": {"jwtToken": "SECRET JWT"}}

        class FakeTOTP:
            def __init__(self, secret):
                self.secret = secret

            def now(self):
                return "123456"

        smartapi.SmartConnect = FakeSmartConnect
        pyotp.TOTP = FakeTOTP
        env = {
            "ANGELONE_API_KEY": "test-api-key",
            "ANGELONE_CLIENT_ID": "test-client",
            "ANGELONE_PASSWORD": "test-password",
            "ANGELONE_TOTP_SECRET": "test-totp-secret",
        }
        try:
            with patch.dict(sys.modules, {"SmartApi": smartapi, "pyotp": pyotp, "dotenv": dotenv}), patch.dict(os.environ, env, clear=True):
                client = authenticate_from_environment()
            self.assertEqual(output.getvalue(), "")
        finally:
            for logger, disabled in zip(sdk_loggers, previous_disabled):
                logger.removeHandler(handler)
                logger.disabled = disabled
        self.assertIsInstance(client, FakeSmartConnect)
        self.assertEqual(client.seen, ("test-client", "test-password", "123456"))


if __name__ == "__main__":
    unittest.main()
