"""Adjusted Yahoo price retrieval with verified local CSV fallback."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from portfoliopulse.data import AlignedPrices, PriceDataError, align_prices, load_price_csv, normalize_observations

Download = Callable[..., pd.DataFrame]
PRICE_BASIS = "yfinance_auto_adjust_close"


def _date_range(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        first = pd.Timestamp(start)
        stop = pd.Timestamp(end)
    except (TypeError, ValueError) as exc:
        raise PriceDataError("Start and end must be ISO dates.") from exc
    if first.strftime("%Y-%m-%d") != start or stop.strftime("%Y-%m-%d") != end or first >= stop:
        raise PriceDataError("Use ISO dates with start before the exclusive end date.")
    return first, stop


def _adjusted_close(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        raise PriceDataError("The market download returned no prices.")
    if isinstance(raw.columns, pd.MultiIndex):
        if raw.columns.nlevels != 2:
            raise PriceDataError("Unexpected market download column structure.")
        close_levels = [level for level in range(2) if "Close" in raw.columns.get_level_values(level)]
        if len(close_levels) != 1:
            raise PriceDataError("Adjusted Close column was not found in the market download.")
        close = raw.xs("Close", level=close_levels[0], axis=1)
    elif len(symbols) == 1 and "Close" in raw.columns:
        close = raw[["Close"]].rename(columns={"Close": symbols[0]})
    else:
        raise PriceDataError("Unexpected market download columns for the requested symbols.")
    if close.columns.has_duplicates or close.index.has_duplicates:
        raise PriceDataError("Duplicate downloaded symbol or date columns are not allowed.")
    missing = set(symbols) - set(close.columns)
    if missing:
        raise PriceDataError(f"Market download omitted: {', '.join(sorted(missing))}")
    close = close.reindex(columns=symbols).copy()
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(close.index, errors="raise"))
    except (TypeError, ValueError) as exc:
        raise PriceDataError("Downloaded dates are invalid.") from exc
    if dates.tz is not None:
        dates = dates.tz_convert("Asia/Kolkata").tz_localize(None)
    dates = dates.normalize()
    if dates.has_duplicates:
        raise PriceDataError("The market download contains duplicate Indian calendar dates.")
    close.index = dates
    for symbol in symbols:
        try:
            values = pd.to_numeric(close[symbol], errors="raise").to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            raise PriceDataError(f"Non-numeric downloaded price for {symbol}.") from exc
        present = ~np.isnan(values)
        if not present.any():
            raise PriceDataError(f"No adjusted prices downloaded for {symbol}.")
        if (~np.isfinite(values[present])).any() or (values[present] <= 0).any():
            raise PriceDataError(f"Invalid adjusted prices downloaded for {symbol}.")
        close[symbol] = values
    return close.sort_index()


def download_adjusted_observations(
    symbols: list[str], start: str, end: str, *, downloader: Download | None = None
) -> pd.DataFrame:
    """Download daily auto-adjusted Close values; end is exclusive."""
    if not symbols or len(symbols) != len(set(symbols)):
        raise PriceDataError("Provide a non-empty list of unique symbols.")
    first, stop = _date_range(start, end)
    if downloader is None:
        import yfinance as yf

        downloader = yf.download
    raw = downloader(
        tickers=symbols, start=start, end=end, interval="1d",
        auto_adjust=True, repair=False, keepna=True, ignore_tz=True,
        group_by="column", multi_level_index=True, threads=False,
        progress=False, timeout=15,
    )
    close = _adjusted_close(raw, symbols)
    close = close.loc[(close.index >= first) & (close.index < stop)]
    if close.empty:
        raise PriceDataError("The market download has no dates in the requested window.")
    parts = [
        pd.DataFrame({"symbol": symbol, "date": close.index[close[symbol].notna()],
                      "close_value": close.loc[close[symbol].notna(), symbol].to_numpy()})
        for symbol in symbols
    ]
    return normalize_observations(pd.concat(parts, ignore_index=True))


def _metadata_path(cache_csv: Path) -> Path:
    return cache_csv.with_suffix(cache_csv.suffix + ".json")


def _save_cache(observations: pd.DataFrame, cache_csv: Path, symbols: list[str], start: str, end: str) -> None:
    cache_csv.parent.mkdir(parents=True, exist_ok=True)
    payload = observations.assign(date=observations["date"].dt.strftime("%Y-%m-%d")).to_csv(index=False).encode("utf-8")
    metadata = {
        "source": "yfinance", "price_basis": PRICE_BASIS, "symbols": symbols,
        "start": start, "end_exclusive": end,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "csv_sha256": hashlib.sha256(payload).hexdigest(),
    }
    csv_temp = cache_csv.with_suffix(cache_csv.suffix + ".tmp")
    meta_path = _metadata_path(cache_csv)
    meta_temp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    csv_temp.write_bytes(payload)
    meta_temp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(csv_temp, cache_csv)
    os.replace(meta_temp, meta_path)


def _load_cache(cache_csv: Path, symbols: list[str], start: str, end: str) -> pd.DataFrame:
    meta_path = _metadata_path(cache_csv)
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        payload = cache_csv.read_bytes()
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise PriceDataError("No readable adjusted-price CSV cache and metadata are available.") from exc
    if not isinstance(metadata, dict):
        raise PriceDataError("The adjusted-price cache metadata is invalid.")
    expected = {"source": "yfinance", "price_basis": PRICE_BASIS, "symbols": symbols,
                "start": start, "end_exclusive": end}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise PriceDataError("Cached prices do not match the requested symbols, window or adjustment basis.")
    if hashlib.sha256(payload).hexdigest() != metadata.get("csv_sha256"):
        raise PriceDataError("The adjusted-price CSV cache failed its integrity check.")
    return load_price_csv(cache_csv)


def load_cached_market_prices(symbols: list[str], start: str, end: str, cache_csv: Path) -> AlignedPrices:
    """Load a previously downloaded, exactly matching adjusted-price CSV."""
    _date_range(start, end)
    observations = _load_cache(cache_csv, symbols, start, end)
    return align_prices(observations, symbols, source="cached_" + PRICE_BASIS,
                        used_fallback=True, start=start,
                        end=(pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))


def refresh_market_prices(
    symbols: list[str], start: str, end: str, cache_csv: Path,
    *, downloader: Download | None = None,
) -> AlignedPrices:
    """Use validated adjusted prices online, or the matching local cache on failure."""
    _date_range(start, end)
    try:
        observations = download_adjusted_observations(symbols, start, end, downloader=downloader)
        result = align_prices(observations, symbols, source=PRICE_BASIS, start=start,
                              end=(pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    except Exception as download_error:
        try:
            return load_cached_market_prices(symbols, start, end, cache_csv)
        except (PriceDataError, OSError) as cache_error:
            raise PriceDataError(
                f"Adjusted-price download failed ({download_error}); CSV fallback unavailable ({cache_error})."
            ) from download_error
    _save_cache(result.observations, cache_csv, symbols, start, end)
    return result
