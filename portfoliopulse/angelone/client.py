"""Authenticated Angel One daily candles, with explicit source and price basis.

`fetch_daily_candles` accepts a SmartAPI-like client so its financial data path
can be checked without credentials or network access. Dates are Indian calendar
dates: start is inclusive and end is exclusive.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import math
import os
import re
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

import pandas as pd

from portfoliopulse.angelone.instrument_master import ResolvedInstrument


SOURCE = "angel_one_smartapi"
PRICE_BASIS = "unadjusted_ohlcv"
INTERVAL = "ONE_DAY"
IST = "Asia/Kolkata"
MAX_DAYS_PER_REQUEST = 2_000
CANDLE_COLUMNS = (
    "symbol", "trading_symbol", "token", "exchange", "timestamp_ist",
    "date", "open", "high", "low", "close", "volume", "source",
    "retrieved_at_utc", "price_basis", "interval",
)
_ANGEL_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,9})?)?(?:Z|\+00:00|\+05:30)?\Z"
)


class AngelOneDataError(ValueError):
    """The Angel One session or candle response cannot be used safely."""


class CandleClient(Protocol):
    def getCandleData(self, historicDataParams: dict[str, str]) -> Any: ...


def _suppress_sdk_logs() -> None:
    """The upstream SDK may log auth headers and request bodies on errors."""
    logging.getLogger("SmartApi.smartConnect").disabled = True
    logging.getLogger("logzero").disabled = True
    try:
        import logzero
    except ImportError:
        return
    logzero.logger.disabled = True


def authenticate_from_environment() -> CandleClient:
    """Create a SmartAPI session from local environment variables.

    `.env` is loaded if python-dotenv is installed. No credential or session
    response is returned, logged, embedded in errors, or saved by this module.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass
    names = (
        "ANGELONE_API_KEY", "ANGELONE_CLIENT_ID", "ANGELONE_PASSWORD",
        "ANGELONE_TOTP_SECRET",
    )
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise AngelOneDataError(f"Set the missing Angel One environment variables: {', '.join(missing)}.")
    _suppress_sdk_logs()
    try:
        from SmartApi import SmartConnect
        import pyotp
    except ImportError:
        raise AngelOneDataError("Install the Angel One SDK and pyotp before authenticating.") from None
    try:
        client = SmartConnect(api_key=os.environ["ANGELONE_API_KEY"])
        one_time_password = pyotp.TOTP(os.environ["ANGELONE_TOTP_SECRET"]).now()
        response = client.generateSession(
            os.environ["ANGELONE_CLIENT_ID"],
            os.environ["ANGELONE_PASSWORD"],
            one_time_password,
        )
        if not isinstance(response, Mapping) or response.get("status") is not True:
            raise AngelOneDataError("Angel One authentication was rejected.")
        return client
    except Exception:
        raise AngelOneDataError("Angel One authentication failed; check local credentials and account access.") from None


def _parse_date(value: str, label: str) -> date:
    if not isinstance(value, str):
        raise AngelOneDataError(f"{label} must be an ISO date (YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise AngelOneDataError(f"{label} must be an ISO date (YYYY-MM-DD).") from None
    if parsed.isoformat() != value:
        raise AngelOneDataError(f"{label} must be an ISO date (YYYY-MM-DD).")
    return parsed


def _request_windows(first: date, stop: date):
    cursor = first
    while cursor < stop:
        next_cursor = min(cursor + timedelta(days=MAX_DAYS_PER_REQUEST), stop)
        yield cursor, next_cursor
        cursor = next_cursor


def _transient_code(value: object) -> bool:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return False
    return code in {408, 429} or 500 <= code <= 599


def _transient_exception(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    if _transient_code(getattr(exc, "status_code", None)) or _transient_code(getattr(exc, "code", None)):
        return True
    response = getattr(exc, "response", None)
    if _transient_code(getattr(response, "status_code", None)):
        return True
    if type(exc).__name__ in {
        "ConnectTimeout", "ReadTimeout", "ConnectionError", "ProxyError", "ChunkedEncodingError",
    }:
        return True
    message = str(exc).casefold()
    return any(word in message for word in (
        "rate limit", "too many requests", "exceeding access rate", "temporarily unavailable",
    ))


def _transient_response(response: Mapping[str, Any]) -> bool:
    if _transient_code(response.get("errorcode")) or _transient_code(response.get("code")):
        return True
    if str(response.get("errorcode")) in {"AB1021", "AB1004"}:
        return True
    message = response.get("message", "")
    if not isinstance(message, str):
        return False
    lowered = message.casefold()
    return any(word in lowered for word in (
        "rate limit", "too many requests", "exceeding access rate", "temporarily unavailable",
    ))


def _request_candles(
    client: CandleClient,
    params: dict[str, str],
    *,
    max_attempts: int,
    retry_wait_seconds: float,
    sleeper: Callable[[float], None],
) -> list[Any]:
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.getCandleData(dict(params))
        except Exception as exc:
            if attempt < max_attempts and _transient_exception(exc):
                sleeper(retry_wait_seconds * (2 ** (attempt - 1)))
                continue
            raise AngelOneDataError("Angel One daily candle request failed.") from None
        if not isinstance(response, Mapping):
            raise AngelOneDataError("Angel One returned an invalid daily candle response.")
        if response.get("status") is not True:
            if attempt < max_attempts and _transient_response(response):
                sleeper(retry_wait_seconds * (2 ** (attempt - 1)))
                continue
            raise AngelOneDataError("Angel One rejected a daily candle request.")
        candles = response.get("data")
        if candles is None:
            return []
        if not isinstance(candles, list):
            raise AngelOneDataError("Angel One returned invalid daily candle data.")
        return candles
    raise AngelOneDataError("Angel One daily candle retry limit was reached.")


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise AngelOneDataError(f"Daily candle {field} must be numeric.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AngelOneDataError(f"Daily candle {field} must be numeric.") from None
    if not math.isfinite(number) or number <= 0:
        raise AngelOneDataError(f"Daily candle {field} must be finite and positive.")
    return number


def _volume(value: object) -> float:
    if isinstance(value, bool):
        raise AngelOneDataError("Daily candle volume must be numeric.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AngelOneDataError("Daily candle volume must be numeric.") from None
    if not math.isfinite(number) or number < 0:
        raise AngelOneDataError("Daily candle volume must be finite and non-negative.")
    return number


def _parse_candle(raw: Any, first: date, stop: date) -> dict[str, Any]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 6:
        raise AngelOneDataError("Each daily candle must contain timestamp and five OHLCV values.")
    if not isinstance(raw[0], str) or _ANGEL_TIMESTAMP.fullmatch(raw[0]) is None:
        raise AngelOneDataError("Daily candle timestamp must use an unambiguous ISO-style date and time.")
    try:
        timestamp = pd.Timestamp(raw[0])
        if pd.isna(timestamp):
            raise ValueError("missing timestamp")
        timestamp = timestamp.tz_localize(IST) if timestamp.tzinfo is None else timestamp.tz_convert(IST)
    except (TypeError, ValueError, OverflowError):
        raise AngelOneDataError("Daily candle timestamp is invalid.") from None
    local_date = timestamp.date()
    if not first <= local_date < stop:
        raise AngelOneDataError("Daily candle timestamp is outside its requested window.")
    open_price = _positive_number(raw[1], "open")
    high_price = _positive_number(raw[2], "high")
    low_price = _positive_number(raw[3], "low")
    close_price = _positive_number(raw[4], "close")
    volume = _volume(raw[5])
    if high_price < max(open_price, close_price, low_price) or low_price > min(open_price, close_price, high_price):
        raise AngelOneDataError("Daily candle OHLC relationships are inconsistent.")
    return {
        "timestamp_ist": timestamp,
        "date": pd.Timestamp(local_date),
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
    }


def fetch_daily_candles(
    client: CandleClient,
    instruments: Sequence[ResolvedInstrument],
    start: str,
    end: str,
    *,
    max_attempts: int = 3,
    retry_wait_seconds: float = 1.0,
    request_delay_seconds: float = 0.5,
    sleeper: Callable[[float], None] = time.sleep,
    raw_batches: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Fetch validated, unadjusted daily NSE OHLCV for resolved instruments.

    The end date is exclusive. Each request covers at most 2,000 calendar days.
    Distinct requests are spaced by half a second by default. Missing market
    sessions are left missing; zero index volume is valid.
    """
    first, stop = _parse_date(start, "start"), _parse_date(end, "end")
    if first >= stop:
        raise AngelOneDataError("start must be before the exclusive end date.")
    if not instruments or len({item.symbol for item in instruments}) != len(instruments):
        raise AngelOneDataError("Provide a non-empty list of unique resolved instruments.")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise AngelOneDataError("max_attempts must be a positive integer.")
    if isinstance(retry_wait_seconds, bool) or not isinstance(retry_wait_seconds, (int, float)) or not math.isfinite(retry_wait_seconds) or retry_wait_seconds < 0:
        raise AngelOneDataError("retry_wait_seconds must be finite and non-negative.")
    if isinstance(request_delay_seconds, bool) or not isinstance(request_delay_seconds, (int, float)) or not math.isfinite(request_delay_seconds) or request_delay_seconds < 0:
        raise AngelOneDataError("request_delay_seconds must be finite and non-negative.")
    if raw_batches is not None and not isinstance(raw_batches, list):
        raise AngelOneDataError("raw_batches must be a list when supplied.")
    _suppress_sdk_logs()
    rows: list[dict[str, Any]] = []
    captured_batches: list[dict[str, Any]] = []
    request_count = 0
    for instrument in instruments:
        if instrument.exchange != "NSE" or not isinstance(instrument.token, str) or not instrument.token.isdecimal() or int(instrument.token) <= 0:
            raise AngelOneDataError("Each instrument needs a validated NSE token.")
        before = len(rows)
        for chunk_first, chunk_stop in _request_windows(first, stop):
            if request_count:
                sleeper(request_delay_seconds)
            request_count += 1
            params = {
                "exchange": instrument.exchange,
                "symboltoken": instrument.token,
                "interval": INTERVAL,
                "fromdate": f"{chunk_first.isoformat()} 00:00",
                "todate": f"{(chunk_stop - timedelta(days=1)).isoformat()} 23:59",
            }
            candles = _request_candles(
                client, params, max_attempts=max_attempts,
                retry_wait_seconds=retry_wait_seconds, sleeper=sleeper,
            )
            parsed = [_parse_candle(raw, chunk_first, chunk_stop) for raw in candles]
            for item in parsed:
                rows.append({
                    "symbol": instrument.symbol,
                    "trading_symbol": instrument.trading_symbol,
                    "token": instrument.token,
                    "exchange": instrument.exchange,
                    **item,
                })
            captured_batches.append({
                "source": SOURCE,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": instrument.symbol,
                "trading_symbol": instrument.trading_symbol,
                "token": instrument.token,
                "exchange": instrument.exchange,
                "interval": INTERVAL,
                "fromdate": params["fromdate"],
                "todate": params["todate"],
                "data": [list(raw) for raw in candles],
            })
        if len(rows) == before:
            raise AngelOneDataError(f"No daily candles were returned for {instrument.symbol}.")
    frame = pd.DataFrame(rows)
    if frame.duplicated(["symbol", "date"]).any():
        raise AngelOneDataError("Duplicate symbol/date daily candles were returned.")
    frame["timestamp_ist"] = frame["timestamp_ist"].astype("datetime64[ns, Asia/Kolkata]")
    frame["date"] = frame["date"].astype("datetime64[ns]")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    frame["source"] = SOURCE
    frame["retrieved_at_utc"] = retrieved_at
    frame["price_basis"] = PRICE_BASIS
    frame["interval"] = INTERVAL
    result = frame.loc[:, list(CANDLE_COLUMNS)].sort_values(["date", "symbol"]).reset_index(drop=True)
    if raw_batches is not None:
        raw_batches.extend(captured_batches)
    return result
