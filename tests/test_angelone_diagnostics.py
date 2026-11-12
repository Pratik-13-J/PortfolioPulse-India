"""Mocked, offline checks for the sanitized Angel One 403 diagnostic."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from portfoliopulse.angelone.diagnostics import (
    RequestRecorder, assert_sanitized, classify_outcome, environment_report, interpret, live_checks,
    master_report, sdk_report,
)

SECRETS = ("API-KEY-SECRET-123", "CLIENT-ID-SECRET", "PASSWORD-SECRET", "TOTPSECRETBASE32",
           "eyJhbGciOiJIUzUxMiJ9.jwt-secret", "feed-token-secret", "198.51.100.23", "aa:bb:cc:dd:ee:ff")


class FakeResponse:
    def __init__(self, status: int, content_type: str = "application/json") -> None:
        self.status_code = status
        self.headers = {"Content-Type": content_type, "X-Request-Id": "req-should-not-matter"}


class DataException(Exception):
    pass


class FakeSdkClient:
    """Mimics SmartConnect: it calls requests.request and raises like the SDK."""

    def __init__(self, candle_status: int = 403) -> None:
        self.access_token = SECRETS[4]
        self.feed_token = SECRETS[5]
        self.candle_status = candle_status

    def _send(self, path: str, status: int):
        import requests

        headers = {"Authorization": "Bearer " + self.access_token, "X-ClientPublicIP": SECRETS[6],
                   "X-MACAddress": SECRETS[7], "X-PrivateKey": SECRETS[0]}
        requests.request("POST", "https://apiconnect.angelone.in" + path, headers=headers,
                         data=json.dumps({"password": SECRETS[2]}))
        return status

    def rmsLimit(self):
        self._send("/rest/secure/angelbroking/user/v1/getRMS", 200)
        return {"status": True, "data": {"net": "1"}}

    def ltpData(self, exchange, symbol, token):
        self._send("/rest/secure/angelbroking/order/v1/getLtpData", 200)
        return {"status": True, "data": {"ltp": 1}}

    def getCandleData(self, params):
        status = self._send("/rest/secure/angelbroking/historical/v1/getCandleData", self.candle_status)
        if status == 403:
            raise DataException(
                "Couldn't parse the JSON response received from the server: "
                f"b'Access denied because of exceeding access rate' token={SECRETS[4]}"
            )
        return {"status": True, "data": [["2026-11-02T09:15:00+05:30", 1, 2, 1, 2, 10]]}


def _fake_request(statuses: dict[str, int]):
    def request(method, url, *args, **kwargs):
        for suffix, status in statuses.items():
            if url.endswith(suffix):
                return FakeResponse(status, "text/plain" if status == 403 else "application/json")
        return FakeResponse(200)
    return request


INSTRUMENT = SimpleNamespace(symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ", token="2885",
                             is_benchmark=False)


class DiagnosticTests(unittest.TestCase):
    def _run(self, candle_status: int):
        import requests

        statuses = {"getCandleData": candle_status}
        with patch.object(requests, "request", _fake_request(statuses)):
            recorder = RequestRecorder()
            with recorder.install():
                client = FakeSdkClient(candle_status)
                return live_checks(client, recorder, instrument=INSTRUMENT, login_monotonic=time.monotonic(),
                                   idle_seconds=0, candle_attempts=2, spacing_seconds=0, window_days=10,
                                   sleeper=lambda _: None)

    def test_mocked_403_is_classified_and_report_is_sanitized(self) -> None:
        report = self._run(403)
        outcomes = [call["outcome"] for call in report["calls"]]
        self.assertEqual(outcomes, ["ok", "ok", "http_403_access_rate", "http_403_access_rate"])
        self.assertIn("entitlement", report["interpretation"])
        candle_http = report["calls"][2]["http"][0]
        self.assertEqual((candle_http["host"], candle_http["status_code"]), ("apiconnect.angelone.in", 403))
        self.assertIn("Authorization", candle_http["request_header_names"])
        self.assertTrue(candle_http["authorization_header_present"])
        self.assertEqual(report["session"], {"access_token_attached": True, "feed_token_present": True})
        text = json.dumps(report, default=str)
        assert_sanitized(text, SECRETS)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertNotIn("exceeding access rate", text.lower())  # raw provider text is not echoed

    def test_success_path(self) -> None:
        report = self._run(200)
        self.assertEqual(report["calls"][-1]["outcome"], "ok")
        self.assertIn("succeeded", report["interpretation"])

    def test_assert_sanitized_detects_leaks(self) -> None:
        with self.assertRaises(AssertionError):
            assert_sanitized('{"x": "PASSWORD-SECRET"}', SECRETS)

    def test_classification_table(self) -> None:
        cases = [
            ((403, "exceeding access rate"), "http_403_access_rate"),
            ((403, "Invalid Token", None, "TokenException"), "http_403_token_or_session"),
            ((403, "Forbidden"), "http_403_forbidden_other"),
            ((None, None, "AB1021"), "rate_limited"),
            ((502, ""), "server_error"),
            ((200, ""), "ok"),
            ((None, "boom", None, "ReadTimeout"), "exception_ReadTimeout"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(classify_outcome(*args), expected)

    def test_window_is_capped_at_fifteen_days(self) -> None:
        with self.assertRaises(ValueError):
            live_checks(FakeSdkClient(), RequestRecorder(), instrument=INSTRUMENT, login_monotonic=0.0,
                        window_days=16, sleeper=lambda _: None)

    def test_environment_report_uses_names_only(self) -> None:
        env = {"ANGELONE_API_KEY": SECRETS[0], "ANGELONE_PASSWORD": ""}
        report = environment_report(env)
        self.assertEqual(report["ANGELONE_API_KEY"], True)
        self.assertEqual(report["ANGELONE_CLIENT_ID"], False)
        assert_sanitized(json.dumps(report), SECRETS)

    def test_sdk_report_detects_placeholder_ip_without_printing_it(self) -> None:
        class FakeSmartConnect:
            _rootUrl = "https://apiconnect.angelone.in"
            _login_url = "https://smartapi.angelone.in/publisher-login"
            _routes = {"api.candle.data": "/rest/secure/angelbroking/historical/v1/getCandleData"}
            clientPublicIp = "106.193.147.98"
            clientLocalIp = "127.0.0.1"

            def __init__(self, **kwargs):
                pass

            def requestHeaders(self):
                return {"X-ClientPublicIP": self.clientPublicIp, "X-ClientLocalIP": self.clientLocalIp,
                        "X-PrivateKey": "diagnostic-placeholder"}

        report = sdk_report(FakeSmartConnect)
        self.assertTrue(report["root_host_matches_current_docs"])
        self.assertEqual(report["public_ip_header_kind"], "sdk_hardcoded_placeholder")
        self.assertFalse(report["constructor_ip_override_honoured"])
        text = json.dumps(report)
        self.assertNotIn("106.193.147.98", text)
        self.assertNotIn("127.0.0.1", text)

    def test_master_report_flags_legacy_host(self) -> None:
        report = master_report({"source_url": "https://margincalculator.angelbroking.com/x.json",
                                "retrieved_at_utc": "2026-10-16T05:12:53+00:00"}, [INSTRUMENT])
        self.assertTrue(report["cached_master_host_is_legacy"])
        self.assertEqual(report["instruments"][0]["token_is_numeric"], True)
        self.assertNotIn("2885", json.dumps(report))

    def test_interpretation_when_everything_fails(self) -> None:
        self.assertIn("session", interpret(False, ["http_403_access_rate"]))


if __name__ == "__main__":
    unittest.main()
