"""Integrity-checked local CSV cache for Angel One daily OHLCV candles."""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.angelone.instrument_master import ConfiguredInstrument, ResolvedInstrument


SOURCE = "angel_one_smartapi"
PRICE_BASIS = "unadjusted_ohlcv"
INTERVAL = "ONE_DAY"
COLUMNS = (
    "symbol", "trading_symbol", "token", "exchange", "timestamp_ist", "date",
    "open", "high", "low", "close", "volume", "source", "retrieved_at_utc",
    "price_basis", "interval",
)


class CandleCacheError(ValueError):
    """A candle cache cannot be used without changing its declared meaning."""


@dataclass(frozen=True)
class CacheIdentity:
    # Each entry is (logical symbol, Angel trading symbol, token, exchange).
    instruments: tuple[tuple[str, str, str, str], ...]
    start: str
    end_exclusive: str
    source: str = SOURCE
    price_basis: str = PRICE_BASIS
    interval: str = INTERVAL


@dataclass(frozen=True)
class CachedCandles:
    candles: pd.DataFrame
    metadata: dict[str, Any]


def _iso_day(value: str) -> pd.Timestamp:
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise CandleCacheError("Cache window dates must use YYYY-MM-DD.") from exc
    if day.strftime("%Y-%m-%d") != value or day.tz is not None:
        raise CandleCacheError("Cache window dates must use YYYY-MM-DD.")
    return day


def _validate_identity(identity: CacheIdentity) -> None:
    if not isinstance(identity, CacheIdentity):
        raise CandleCacheError("Expected a CacheIdentity for Angel One candles.")
    if identity.source != SOURCE or identity.price_basis != PRICE_BASIS or identity.interval != INTERVAL:
        raise CandleCacheError("Only Angel One unadjusted ONE_DAY OHLCV candles may use this cache.")
    if _iso_day(identity.start) >= _iso_day(identity.end_exclusive):
        raise CandleCacheError("Cache window start must precede its exclusive end.")
    if not identity.instruments or len(set(identity.instruments)) != len(identity.instruments):
        raise CandleCacheError("Cache identity needs unique configured instruments.")
    logical_symbols = [row[0] for row in identity.instruments]
    trading_symbols = [row[1] for row in identity.instruments]
    if len(set(logical_symbols)) != len(logical_symbols) or len(set(trading_symbols)) != len(trading_symbols):
        raise CandleCacheError("Cache instruments must have unique logical and trading symbols.")
    for instrument in identity.instruments:
        if len(instrument) != 4 or any(not isinstance(value, str) or not value for value in instrument):
            raise CandleCacheError("Cache instrument identity is incomplete.")
        if instrument[3] != "NSE" or not instrument[2].isdecimal() or int(instrument[2]) <= 0:
            raise CandleCacheError("Cache identity requires valid Angel One NSE tokens.")


def cache_identity(instruments: Sequence[ResolvedInstrument], start: str, end: str) -> CacheIdentity:
    """Build an exact identity; `start` is inclusive and `end` exclusive."""
    rows = tuple(sorted(
        (item.symbol, item.trading_symbol, item.token, item.exchange) for item in instruments
    ))
    identity = CacheIdentity(rows, start, end)
    _validate_identity(identity)
    return identity


def _identity_payload(identity: CacheIdentity) -> dict[str, Any]:
    return {
        "source": identity.source,
        "price_basis": identity.price_basis,
        "interval": identity.interval,
        "start": identity.start,
        "end_exclusive": identity.end_exclusive,
        "instruments": [
            {"symbol": symbol, "trading_symbol": trading_symbol, "token": token, "exchange": exchange}
            for symbol, trading_symbol, token, exchange in identity.instruments
        ],
    }


def _normalize_candles(frame: pd.DataFrame, identity: CacheIdentity) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise CandleCacheError("No daily candles are available to cache.")
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise CandleCacheError(f"Missing candle columns: {', '.join(sorted(missing))}.")
    clean = frame.loc[:, list(COLUMNS)].copy()
    for column in ("symbol", "trading_symbol", "token", "exchange", "source", "retrieved_at_utc", "price_basis", "interval"):
        if clean[column].isna().any():
            raise CandleCacheError(f"Candle column {column} has missing values.")
        clean[column] = clean[column].astype(str)
    observed_instruments = set(
        zip(clean["symbol"], clean["trading_symbol"], clean["token"], clean["exchange"])
    )
    if observed_instruments != set(identity.instruments):
        raise CandleCacheError("Candle symbols, trading symbols, tokens or exchanges do not match the cache request.")
    if set(clean["source"]) != {SOURCE} or set(clean["price_basis"]) != {PRICE_BASIS} or set(clean["interval"]) != {INTERVAL}:
        raise CandleCacheError("Candle source, price basis or interval is inconsistent.")
    try:
        timestamps = pd.to_datetime(clean["timestamp_ist"], errors="raise")
        dates = pd.to_datetime(clean["date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise CandleCacheError("Candle timestamps or dates are invalid.") from exc
    if timestamps.dt.tz is None:
        raise CandleCacheError("Candle timestamps must have an Asia/Kolkata timezone offset.")
    timestamps = timestamps.dt.tz_convert("Asia/Kolkata").astype("datetime64[ns, Asia/Kolkata]")
    if dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise CandleCacheError("Candle dates must be naive normalized Indian calendar dates.")
    dates = dates.astype("datetime64[ns]")
    if not timestamps.dt.tz_localize(None).dt.normalize().eq(dates).all():
        raise CandleCacheError("Each candle date must equal its Asia/Kolkata timestamp date.")
    if not dates.ge(_iso_day(identity.start)).all() or not dates.lt(_iso_day(identity.end_exclusive)).all():
        raise CandleCacheError("Candle dates fall outside the requested inclusive/exclusive window.")
    clean["timestamp_ist"] = timestamps
    clean["date"] = dates
    if clean.duplicated(["symbol", "date"]).any():
        raise CandleCacheError("Duplicate symbol/date candles are not allowed.")
    for value in clean["retrieved_at_utc"]:
        try:
            retrieved = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CandleCacheError("A candle retrieval timestamp is invalid.") from exc
        if retrieved.utcoffset() is None or retrieved.utcoffset().total_seconds() != 0:
            raise CandleCacheError("Candle retrieval timestamps must be in UTC.")
    for column in ("open", "high", "low", "close", "volume"):
        try:
            clean[column] = pd.to_numeric(clean[column], errors="raise").astype(float)
        except (TypeError, ValueError) as exc:
            raise CandleCacheError(f"Candle {column} contains a non-numeric value.") from exc
        if not np.isfinite(clean[column].to_numpy()).all():
            raise CandleCacheError(f"Candle {column} contains a non-finite value.")
    if (clean[["open", "high", "low", "close"]] <= 0).any().any() or (clean["volume"] < 0).any():
        raise CandleCacheError("Candle prices must be positive and volume non-negative.")
    if ((clean["low"] > clean[["open", "close"]].min(axis=1)) |
        (clean["high"] < clean[["open", "close"]].max(axis=1)) |
        (clean["high"] < clean["low"])).any():
        raise CandleCacheError("Candle OHLC relationships are invalid.")
    return clean.sort_values(["date", "symbol"]).reset_index(drop=True)


def _metadata_path(cache_csv: Path) -> Path:
    return cache_csv.with_name(cache_csv.name + ".meta.json")


def save_candles(frame: pd.DataFrame, cache_csv: Path, identity: CacheIdentity) -> dict[str, Any]:
    """Validate and save Angel One candles with exact request identity and hash."""
    _validate_identity(identity)
    clean = _normalize_candles(frame, identity)
    serializable = clean.copy()
    serializable["timestamp_ist"] = serializable["timestamp_ist"].map(lambda value: value.isoformat())
    serializable["date"] = serializable["date"].dt.strftime("%Y-%m-%d")
    payload = serializable.to_csv(index=False).encode("utf-8")
    retrieved_times = pd.to_datetime(clean["retrieved_at_utc"], utc=True)
    metadata = {
        **_identity_payload(identity),
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": len(clean),
        "retrieved_at_utc_first": retrieved_times.min().isoformat(),
        "retrieved_at_utc_last": retrieved_times.max().isoformat(),
        "csv_sha256": hashlib.sha256(payload).hexdigest(),
    }
    cache_csv = Path(cache_csv)
    cache_csv.parent.mkdir(parents=True, exist_ok=True)
    csv_temp = cache_csv.with_name(cache_csv.name + ".tmp")
    meta_path = _metadata_path(cache_csv)
    meta_temp = meta_path.with_name(meta_path.name + ".tmp")
    csv_temp.write_bytes(payload)
    meta_temp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(csv_temp, cache_csv)
    os.replace(meta_temp, meta_path)
    return metadata


def load_candles(cache_csv: Path, expected: CacheIdentity) -> CachedCandles:
    """Load only a hash-valid Angel One cache for an identical request."""
    _validate_identity(expected)
    cache_csv = Path(cache_csv)
    try:
        payload = cache_csv.read_bytes()
        metadata = json.loads(_metadata_path(cache_csv).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandleCacheError("No readable Angel One candle CSV and metadata are available.") from exc
    if not isinstance(metadata, dict) or any(metadata.get(key) != value for key, value in _identity_payload(expected).items()):
        raise CandleCacheError("Cached candles do not match the requested instruments, window or price basis.")
    if hashlib.sha256(payload).hexdigest() != metadata.get("csv_sha256"):
        raise CandleCacheError("Cached candle CSV failed its SHA-256 integrity check.")
    try:
        frame = pd.read_csv(io.BytesIO(payload), dtype={"symbol": str, "trading_symbol": str, "token": str, "exchange": str})
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise CandleCacheError("Cached candle CSV is not readable.") from exc
    clean = _normalize_candles(frame, expected)
    if len(clean) != metadata.get("row_count"):
        raise CandleCacheError("Cached candle row count does not match metadata.")
    return CachedCandles(clean, metadata)


def load_configured_candles(
    cache_csv: Path, configured: Sequence[ConfiguredInstrument], start: str, end: str
) -> CachedCandles:
    """Replay a previously validated Angel CSV using its recorded token mapping.

    A current master may assign different tokens. Offline replay checks the
    configured names/exchange and the saved CSV integrity without using an old
    token to request new market data.
    """
    try:
        metadata = json.loads(_metadata_path(Path(cache_csv)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandleCacheError("No readable Angel One candle CSV metadata is available.") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("instruments"), list):
        raise CandleCacheError("Cached candle instrument metadata is invalid.")
    recorded = metadata["instruments"]
    if any(not isinstance(item, dict) for item in recorded):
        raise CandleCacheError("Cached candle instrument metadata is invalid.")
    expected_names = sorted((item.symbol, item.trading_symbol, item.exchange) for item in configured)
    try:
        recorded_names = sorted((item["symbol"], item["trading_symbol"], item["exchange"]) for item in recorded)
        identity_rows = tuple(sorted(
            (item["symbol"], item["trading_symbol"], item["token"], item["exchange"])
            for item in recorded
        ))
    except (KeyError, TypeError, ValueError) as exc:
        raise CandleCacheError("Cached candle instrument metadata is invalid.") from exc
    if recorded_names != expected_names:
        raise CandleCacheError("Cached candles do not match configured instruments.")
    identity = CacheIdentity(identity_rows, start, end)
    return load_candles(cache_csv, identity)
