"""Import user-supplied, source-labelled Indian daily OHLCV without an API.

This is independent of the Angel One cache. Provenance is supplied by the user
and recorded as unverified; the importer never calls it a broker observation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from portfoliopulse.angelone.instrument_master import ConfiguredInstrument
from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.data import AlignedPrices, PriceDataError, align_prices


CSV_COLUMNS = (
    "symbol", "trading_symbol", "exchange", "date", "open", "high", "low", "close", "volume",
)
PRICE_BASES = {"unadjusted_ohlcv", "provider_adjusted_ohlcv", "split_adjusted_ohlcv"}
PROVENANCE_KIND = "user_supplied_csv"
PROCESSED_ROOT = PROJECT_ROOT / "data" / "processed"


class SourcedCsvError(ValueError):
    """A supplied CSV cannot be imported without changing its stated meaning."""


@dataclass(frozen=True)
class SourcedCsvResult:
    candles: pd.DataFrame
    aligned: AlignedPrices
    metadata: dict[str, Any]
    output_csv: Path
    metadata_path: Path
    aligned_prices_csv: Path
    daily_returns_csv: Path
    coverage_csv: Path


def _iso_day(value: str, label: str) -> date:
    if not isinstance(value, str):
        raise SourcedCsvError(f"{label} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SourcedCsvError(f"{label} must use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise SourcedCsvError(f"{label} must use YYYY-MM-DD.")
    return parsed


def _provenance(path: Path) -> dict[str, str]:
    try:
        supplied = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourcedCsvError(f"A readable provenance sidecar is required: {path}.") from exc
    if not isinstance(supplied, dict):
        raise SourcedCsvError("CSV provenance must be a JSON object.")
    required = ("source_name", "source_url", "retrieved_at_utc", "price_basis")
    if any(not isinstance(supplied.get(key), str) or not supplied[key].strip() for key in required):
        raise SourcedCsvError("CSV provenance requires source_name, source_url, retrieved_at_utc and price_basis.")
    source_name = supplied["source_name"].strip()
    source_url = supplied["source_url"].strip()
    url = urlsplit(source_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise SourcedCsvError("CSV source_url must be a credential-free HTTPS page URL without query or fragment.")
    try:
        retrieved = datetime.fromisoformat(supplied["retrieved_at_utc"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourcedCsvError("retrieved_at_utc must be an ISO UTC timestamp.") from exc
    if retrieved.utcoffset() is None or retrieved.utcoffset().total_seconds() != 0:
        raise SourcedCsvError("retrieved_at_utc must be an ISO UTC timestamp.")
    basis = supplied["price_basis"]
    if basis not in PRICE_BASES:
        raise SourcedCsvError(f"price_basis must be one of: {', '.join(sorted(PRICE_BASES))}.")
    result = {
        "source_name": source_name,
        "source_url": source_url,
        "retrieved_at_utc": retrieved.astimezone(timezone.utc).isoformat(),
        "price_basis": basis,
    }
    optional_hash = supplied.get("csv_sha256")
    if optional_hash is not None:
        if not isinstance(optional_hash, str) or len(optional_hash) != 64 or any(c not in "0123456789abcdef" for c in optional_hash):
            raise SourcedCsvError("Optional csv_sha256 must be a lowercase SHA-256 hex digest.")
        result["csv_sha256"] = optional_hash
    return result


def _read_candles(payload: bytes, configured: Sequence[ConfiguredInstrument], start: date, end: date) -> tuple[pd.DataFrame, int]:
    try:
        frame = pd.read_csv(io.BytesIO(payload), dtype={"symbol": str, "trading_symbol": str, "exchange": str, "date": str})
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise SourcedCsvError("The supplied daily OHLCV CSV cannot be read.") from exc
    if frame.empty or set(frame.columns) != set(CSV_COLUMNS) or frame.columns.has_duplicates:
        raise SourcedCsvError("CSV must contain exactly symbol,trading_symbol,exchange,date,open,high,low,close,volume.")
    clean = frame.loc[:, list(CSV_COLUMNS)].copy()
    if clean[["symbol", "trading_symbol", "exchange", "date"]].isna().any().any():
        raise SourcedCsvError("CSV identifiers and dates cannot be missing.")
    clean["symbol"] = clean["symbol"].astype(str)
    clean["trading_symbol"] = clean["trading_symbol"].astype(str)
    clean["exchange"] = clean["exchange"].astype(str)
    expected = {(item.symbol, item.trading_symbol, item.exchange) for item in configured}
    if not expected or len(expected) != len(configured):
        raise SourcedCsvError("Configured instruments must be non-empty and unique.")
    if any(exchange != "NSE" for _, _, exchange in expected):
        raise SourcedCsvError("This CSV importer requires configured NSE instruments.")
    observed = set(zip(clean["symbol"], clean["trading_symbol"], clean["exchange"]))
    if observed != expected:
        raise SourcedCsvError("CSV symbols, trading symbols and NSE exchanges must match the configured portfolio exactly.")
    date_strings = clean["date"].astype(str)
    parsed_dates = pd.to_datetime(date_strings, format="%Y-%m-%d", errors="coerce")
    if parsed_dates.isna().any() or not parsed_dates.dt.strftime("%Y-%m-%d").eq(date_strings).all():
        raise SourcedCsvError("CSV dates must use valid ISO YYYY-MM-DD values.")
    clean["date"] = parsed_dates.astype("datetime64[ns]")
    if clean.duplicated(["symbol", "date"]).any():
        raise SourcedCsvError("Duplicate symbol/date candles are not allowed.")
    for column in ("open", "high", "low", "close", "volume"):
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
        numbers = clean[column].to_numpy(dtype=float)
        if not np.isfinite(numbers).all():
            raise SourcedCsvError(f"CSV {column} must be finite and numeric.")
        clean[column] = numbers
    if (clean[["open", "high", "low", "close"]] <= 0).any().any() or (clean["volume"] < 0).any():
        raise SourcedCsvError("CSV OHLC prices must be positive and volume non-negative.")
    if ((clean["low"] > clean[["open", "close"]].min(axis=1)) |
        (clean["high"] < clean[["open", "close"]].max(axis=1)) |
        (clean["high"] < clean["low"])).any():
        raise SourcedCsvError("CSV OHLC relationships are inconsistent.")
    in_window = clean["date"].ge(pd.Timestamp(start)) & clean["date"].lt(pd.Timestamp(end))
    excluded = int((~in_window).sum())
    clean = clean.loc[in_window].copy()
    if set(clean["symbol"]) != {item.symbol for item in configured}:
        raise SourcedCsvError("Every configured instrument needs at least one candle inside the requested window.")
    return clean.sort_values(["date", "symbol"]).reset_index(drop=True), excluded


def _output_directory(path: Path | None) -> Path:
    root = PROCESSED_ROOT.resolve()
    chosen = Path(path).resolve() if path is not None else root
    if not chosen.is_relative_to(root):
        raise SourcedCsvError("Processed CSV output must stay under data/processed.")
    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def _write_bytes(path: Path, payload: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def import_sourced_csv(
    csv_path: Path,
    config_entries: Sequence[ConfiguredInstrument],
    start: str,
    end: str,
    *,
    metadata_path: Path | None = None,
    output_dir: Path | None = None,
) -> SourcedCsvResult:
    """Validate source-labelled NSE candles and save aligned results locally.

    `start` is inclusive, `end` exclusive. The sidecar defaults to
    `<csv_stem>.meta.json`. It declares source_name, source_url,
    retrieved_at_utc and price_basis. The source claim is recorded, not
    independently authenticated. No prices or holiday sessions are filled.
    """
    first, stop = _iso_day(start, "start"), _iso_day(end, "end")
    if first >= stop:
        raise SourcedCsvError("start must precede the exclusive end date.")
    csv_path = Path(csv_path)
    provenance = _provenance(Path(metadata_path) if metadata_path is not None else csv_path.with_suffix(".meta.json"))
    try:
        payload = csv_path.read_bytes()
    except OSError as exc:
        raise SourcedCsvError(f"Cannot read sourced candle CSV: {csv_path}.") from exc
    input_hash = hashlib.sha256(payload).hexdigest()
    if "csv_sha256" in provenance and provenance["csv_sha256"] != input_hash:
        raise SourcedCsvError("Supplied CSV does not match its declared SHA-256 checksum.")
    candles, excluded = _read_candles(payload, config_entries, first, stop)
    candles["source"] = provenance["source_name"]
    candles["source_url"] = provenance["source_url"]
    candles["retrieved_at_utc"] = provenance["retrieved_at_utc"]
    candles["price_basis"] = provenance["price_basis"]
    candles["provenance_kind"] = PROVENANCE_KIND
    candles["interval"] = "ONE_DAY"
    symbols = [item.symbol for item in config_entries]
    observations = candles.rename(columns={"close": "close_value"}).loc[:, ["symbol", "date", "close_value"]]
    try:
        aligned = align_prices(observations, symbols, source=provenance["source_name"])
    except PriceDataError as exc:
        raise SourcedCsvError(f"CSV does not provide enough common valid daily returns: {exc}") from exc
    output = _output_directory(output_dir)
    identity = {
        "input_csv_sha256": input_hash,
        "source_name": provenance["source_name"],
        "source_url": provenance["source_url"],
        "retrieved_at_utc": provenance["retrieved_at_utc"],
        "price_basis": provenance["price_basis"],
        "start": start,
        "end_exclusive": end,
        "instruments": sorted((item.symbol, item.trading_symbol, item.exchange) for item in config_entries),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    stem = f"sourced_{digest}"
    output_csv = output / f"{stem}_candles.csv"
    aligned_csv = output / f"{stem}_aligned_prices.csv"
    returns_csv = output / f"{stem}_daily_returns.csv"
    coverage_csv = output / f"{stem}_coverage.csv"
    metadata_output = output / f"{stem}.meta.json"
    serialized = candles.copy()
    serialized["date"] = serialized["date"].dt.strftime("%Y-%m-%d")
    outputs = {
        output_csv: serialized.to_csv(index=False).encode("utf-8"),
        aligned_csv: aligned.prices.to_csv(index_label="date").encode("utf-8"),
        returns_csv: aligned.daily_returns.to_csv(index_label="date").encode("utf-8"),
        coverage_csv: aligned.coverage.to_csv(index=False).encode("utf-8"),
    }
    metadata = {
        **identity,
        "provenance_kind": PROVENANCE_KIND,
        "provenance_status": "user_declared_unverified",
        "interval": "ONE_DAY",
        "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_rows_excluded_outside_window": excluded,
        "valid_candle_rows": len(candles),
        "aligned_price_dates": len(aligned.prices),
        "valid_daily_return_dates": len(aligned.daily_returns),
        "incomplete_union_dates": aligned.dropped_price_dates,
        "output_sha256": {path.name: hashlib.sha256(content).hexdigest() for path, content in outputs.items()},
    }
    for path, content in outputs.items():
        _write_bytes(path, content)
    _write_bytes(metadata_output, (json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
    return SourcedCsvResult(candles, aligned, metadata, output_csv, metadata_output,
                            aligned_csv, returns_csv, coverage_csv)
