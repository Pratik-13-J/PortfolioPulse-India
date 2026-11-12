"""Sanitized Angel One SmartAPI HTTP 403 diagnostic (roadmap §3 checklist).

Output is built from a whitelist only: booleans, HTTP status codes, host names,
route paths, header *names*, Angel error codes and fixed classification
labels. It never contains credential values, session/feed tokens, IP or MAC
values, request bodies or raw response text. This module does not read the
credential environment variables itself; live checks authenticate through
``client.authenticate_from_environment`` exactly like the refresh script.

Live checks are only run when the user invokes the CLI with ``--live`` on
their own machine. Mocked tests exercise the same code without network.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import metadata as importlib_metadata
from typing import Any, Callable, Iterator, Mapping, Sequence
from unittest.mock import patch
from urllib.parse import urlsplit

CREDENTIAL_ENV_NAMES = ("ANGELONE_API_KEY", "ANGELONE_CLIENT_ID", "ANGELONE_PASSWORD", "ANGELONE_TOTP_SECRET")
CURRENT_DOC_HOST_SUFFIX = "angelone.in"
LEGACY_HOST_SUFFIX = "angelbroking.com"
SDK_PLACEHOLDER_PUBLIC_IP = "106.193.147.98"  # compared only; never printed
_TEST_NET_IP = "203.0.113.7"  # RFC 5737 documentation address used to probe overrides
_ERROR_CODE = re.compile(r"\bAB\d{4}\b")
_RATE_WORDS = ("exceeding access rate", "rate limit", "too many requests")


def classify_outcome(status_code: int | None, error_text: str | None = None,
                     errorcode: str | None = None, exception_name: str | None = None) -> str:
    """Map one call outcome to a fixed, non-sensitive label."""
    text = (error_text or "").casefold()
    rate = any(word in text for word in _RATE_WORDS) or errorcode == "AB1021"
    if status_code == 403:
        if rate:
            return "http_403_access_rate"
        if "token" in text or exception_name == "TokenException":
            return "http_403_token_or_session"
        return "http_403_forbidden_other"
    if status_code == 429 or rate:
        return "rate_limited"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if exception_name is not None:
        return "exception_" + re.sub(r"[^A-Za-z0-9_]", "", exception_name)[:40]
    if errorcode:
        return "api_error"
    if status_code == 200:
        return "ok"
    return "unknown"


def _safe_errorcode(value: Any) -> str | None:
    if value is None:
        return None
    match = _ERROR_CODE.search(str(value))
    return match.group(0) if match else None


@dataclass
class HttpObservation:
    method: str
    host: str
    path: str
    status_code: int | None
    content_type: str | None
    request_header_names: list[str]
    response_header_names: list[str]
    authorization_header_present: bool

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RequestRecorder:
    """Wraps ``requests.request`` to capture metadata only, never values."""

    observations: list[HttpObservation] = field(default_factory=list)

    @contextmanager
    def install(self) -> Iterator["RequestRecorder"]:
        import requests

        original = requests.request

        def wrapper(method, url, *args, **kwargs):
            headers = dict(kwargs.get("headers") or {})
            parts = urlsplit(str(url))
            try:
                response = original(method, url, *args, **kwargs)
            except Exception:
                self.observations.append(HttpObservation(
                    str(method).upper(), parts.hostname or "", parts.path, None, None,
                    sorted(headers), [], "Authorization" in headers,
                ))
                raise
            self.observations.append(HttpObservation(
                str(method).upper(), parts.hostname or "", parts.path, int(response.status_code),
                (response.headers.get("Content-Type") or "").split(";")[0] or None,
                sorted(headers), sorted(response.headers.keys()), "Authorization" in headers,
            ))
            return response

        with patch.object(requests, "request", wrapper):
            yield self


def _import_sdk():
    """Import SmartConnect with its third-party public-IP lookup blocked."""
    import requests

    from portfoliopulse.angelone.client import _suppress_sdk_logs

    _suppress_sdk_logs()
    with patch.object(requests, "get", side_effect=OSError("SDK IP lookup disabled")):
        from SmartApi import SmartConnect
    return SmartConnect


def sdk_report(smart_connect_cls: Any | None = None) -> dict[str, Any]:
    """Checklist items 1 and 4: SDK version, hosts, routes and header behaviour."""
    try:
        cls = smart_connect_cls or _import_sdk()
    except ImportError:
        return {"sdk_installed": False}
    try:
        version = importlib_metadata.version("smartapi-python")
    except importlib_metadata.PackageNotFoundError:
        version = None
    root_host = urlsplit(getattr(cls, "_rootUrl", "")).hostname or ""
    login_host = urlsplit(getattr(cls, "_login_url", "")).hostname or ""
    routes = getattr(cls, "_routes", {})
    probe = cls(api_key="diagnostic-placeholder", clientPublicIP=_TEST_NET_IP, clientLocalIP=_TEST_NET_IP)
    headers = probe.requestHeaders()
    public_value = headers.get("X-ClientPublicIP")
    local_value = headers.get("X-ClientLocalIP")
    return {
        "sdk_installed": True,
        "sdk_version": version,
        "root_host": root_host,
        "root_host_matches_current_docs": root_host.endswith(CURRENT_DOC_HOST_SUFFIX),
        "login_host": login_host,
        "historical_route": routes.get("api.candle.data"),
        "profile_route": routes.get("api.user.profile"),
        "ltp_route": routes.get("api.ltp.data"),
        "request_header_names": sorted(headers),
        "public_ip_header_kind": (
            "sdk_hardcoded_placeholder" if public_value == SDK_PLACEHOLDER_PUBLIC_IP
            else "constructor_override" if public_value == _TEST_NET_IP
            else "missing" if not public_value else "other_value"
        ),
        "local_ip_header_is_loopback": str(local_value).strip().startswith("127."),
        "constructor_ip_override_honoured": public_value == _TEST_NET_IP and local_value == _TEST_NET_IP,
    }


def environment_report(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Whether each credential *name* is set in the process environment.

    Only key membership is tested; no value is read, compared or returned.
    ``.env`` is not parsed here (live mode loads it via the client module).
    """
    env = os.environ if environ is None else environ
    return {name: name in env for name in CREDENTIAL_ENV_NAMES}


def master_report(master_meta: Mapping[str, Any] | None, resolved: Sequence[Any] | None,
                  resolve_error: str | None = None) -> dict[str, Any]:
    """Checklist item 6: master host recorded in the cache and token resolution."""
    host = urlsplit(str((master_meta or {}).get("source_url", ""))).hostname or None
    return {
        "cached_master_present": master_meta is not None,
        "cached_master_host": host,
        "cached_master_host_is_legacy": bool(host and host.endswith(LEGACY_HOST_SUFFIX)),
        "cached_master_retrieved_at_utc": (master_meta or {}).get("retrieved_at_utc"),
        "resolution_error": resolve_error,
        "instruments": [
            {"symbol": item.symbol, "exchange": item.exchange, "is_benchmark": bool(item.is_benchmark),
             "token_resolved": bool(item.token), "token_is_numeric": str(item.token).isdecimal()}
            for item in (resolved or ())
        ],
    }


def _call(name: str, func: Callable[[], Any], recorder: RequestRecorder) -> dict[str, Any]:
    before = len(recorder.observations)
    started = time.monotonic()
    status_ok: bool | None = None
    errorcode: str | None = None
    exception_name: str | None = None
    error_text: str | None = None
    try:
        response = func()
        if isinstance(response, Mapping):
            status_ok = response.get("status") is True
            errorcode = _safe_errorcode(response.get("errorcode"))
            if not status_ok:
                error_text = str(response.get("message", ""))
    except Exception as exc:  # classified, never echoed
        exception_name = type(exc).__name__
        error_text = str(exc)
    http = [item.as_dict() for item in recorder.observations[before:]]
    status_code = http[-1]["status_code"] if http else None
    label = classify_outcome(status_code, error_text, errorcode, exception_name)
    if label == "unknown" and status_ok:
        label = "ok"
    elif label == "ok" and status_ok is False:
        label = "api_error"
    return {
        "check": name,
        "outcome": label,
        "response_status_true": status_ok,
        "errorcode": errorcode or _safe_errorcode(error_text),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "http": http,
    }


def live_checks(
    client: Any,
    recorder: RequestRecorder,
    *,
    instrument: Any,
    login_monotonic: float,
    idle_seconds: float = 65.0,
    candle_attempts: int = 1,
    spacing_seconds: float = 60.0,
    window_days: int = 10,
    today: date | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Checklist items 2, 5 and 7 against an authenticated client.

    The caller authenticates *inside* ``recorder.install()`` so the login call
    is observed too. ``instrument`` is a resolved equity used for LTP and
    candles. The window is at most 15 days.
    """
    if not 1 <= window_days <= 15:
        raise ValueError("window_days must be between 1 and 15.")
    if candle_attempts < 1:
        raise ValueError("candle_attempts must be positive.")
    end = today or date.today()
    start = end - timedelta(days=window_days)
    session = {
        "access_token_attached": bool(getattr(client, "access_token", None)),
        "feed_token_present": bool(getattr(client, "feed_token", None)),
    }
    results = [
        _call("rms_limit_non_historical", lambda: client.rmsLimit(), recorder),
        _call("ltp_non_historical", lambda: client.ltpData(
            instrument.exchange, instrument.trading_symbol, instrument.token), recorder),
    ]
    sleeper(idle_seconds)
    params = {"exchange": instrument.exchange, "symboltoken": instrument.token, "interval": "ONE_DAY",
              "fromdate": f"{start.isoformat()} 09:15", "todate": f"{end.isoformat()} 15:30"}
    for attempt in range(candle_attempts):
        if attempt:
            sleeper(spacing_seconds)
        result = _call(f"historical_candles_attempt_{attempt + 1}", lambda: client.getCandleData(dict(params)), recorder)
        result["seconds_since_login"] = round(time.monotonic() - login_monotonic, 1)
        results.append(result)
    candle_outcomes = [item["outcome"] for item in results if item["check"].startswith("historical")]
    non_historical_ok = all(item["outcome"] == "ok" for item in results[:2])
    return {
        "session": session,
        "idle_seconds_before_first_candle_call": idle_seconds,
        "calls": results,
        "interpretation": interpret(non_historical_ok, candle_outcomes),
    }


def interpret(non_historical_ok: bool, candle_outcomes: Sequence[str]) -> str:
    """Fixed-text hint for the user; a hypothesis, not a proven root cause."""
    if candle_outcomes and all(outcome == "ok" for outcome in candle_outcomes):
        return "Historical candles succeeded in this run; a small live pull can be attempted."
    first = candle_outcomes[0] if candle_outcomes else "none"
    if non_historical_ok and first.startswith("http_403"):
        return ("Non-historical endpoints passed but the first historical call after idle returned 403: "
                "likely an endpoint entitlement, app-type or registered static-IP restriction rather than "
                "request rate. Check the SmartAPI portal (app permissions, API key status, static IP).")
    if not non_historical_ok:
        return "Non-historical endpoints also failed: check session, API key status and account access first."
    return "Inconclusive; compare outcomes across repeated runs and portal settings."


def assert_sanitized(report_text: str, forbidden: Sequence[str]) -> None:
    """Raise if any forbidden literal appears in the serialized report."""
    for value in forbidden:
        if value and value in report_text:
            raise AssertionError("A sensitive value leaked into the diagnostic report.")
