"""CSV price ingestion and explicit date alignment for Indian portfolio data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np
import pandas as pd


class PriceDataError(ValueError):
    """The supplied price file cannot be used without changing its meaning."""


@dataclass(frozen=True)
class AlignedPrices:
    observations: pd.DataFrame
    prices: pd.DataFrame
    daily_returns: pd.DataFrame
    coverage: pd.DataFrame
    dropped_price_dates: int
    source: str
    used_fallback: bool


def normalize_observations(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate tidy symbol/date/price observations without filling gaps."""
    required = {"symbol", "date", "close_value"}
    missing_columns = required - set(frame.columns)
    if missing_columns:
        raise PriceDataError(f"Missing columns: {', '.join(sorted(missing_columns))}")
    if frame.empty:
        raise PriceDataError("The price file has no observations.")

    clean = frame.loc[:, ["symbol", "date", "close_value"]].copy()
    clean["symbol"] = clean["symbol"].astype("string").str.strip()
    clean["date"] = pd.to_datetime(clean["date"], format="%Y-%m-%d", errors="coerce")
    clean["close_value"] = pd.to_numeric(clean["close_value"], errors="coerce")
    invalid_symbol = clean["symbol"].isna() | clean["symbol"].eq("")
    invalid_price = clean["close_value"].isna() | ~np.isfinite(clean["close_value"].to_numpy(dtype=float)) | clean["close_value"].le(0)
    if invalid_symbol.any() or clean["date"].isna().any() or invalid_price.any():
        raise PriceDataError("Each row needs a symbol, an ISO date, and a finite positive price.")
    if clean.duplicated(["symbol", "date"]).any():
        raise PriceDataError("Duplicate symbol/date observations are not allowed.")
    return clean.sort_values(["date", "symbol"]).reset_index(drop=True)


def load_price_csv(source: str | Path | IO[bytes]) -> pd.DataFrame:
    """Read a CSV and return validated observations in long format."""
    try:
        frame = pd.read_csv(source, dtype={"symbol": "string", "date": "string"})
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise PriceDataError(f"Could not read the price CSV: {exc}") from exc
    return normalize_observations(frame)


def align_prices(
    observations: pd.DataFrame,
    symbols: list[str],
    *,
    source: str,
    used_fallback: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> AlignedPrices:
    """Align all requested symbols; compute returns before dropping gaps."""
    if not symbols or len(symbols) != len(set(symbols)):
        raise PriceDataError("Provide a non-empty list of unique symbols.")
    clean = normalize_observations(observations)
    clean = clean.loc[clean["symbol"].isin(symbols)].copy()
    if start:
        clean = clean.loc[clean["date"] >= pd.Timestamp(start)]
    if end:
        clean = clean.loc[clean["date"] <= pd.Timestamp(end)]
    absent = set(symbols) - set(clean["symbol"])
    if absent:
        raise PriceDataError(f"No observations for: {', '.join(sorted(absent))}")
    wide = clean.pivot(index="date", columns="symbol", values="close_value").sort_index().reindex(columns=symbols)
    coverage = pd.DataFrame(
        {
            "symbol": symbols,
            "observations": wide.notna().sum().reindex(symbols).to_numpy(),
            "missing_on_union_dates": wide.isna().sum().reindex(symbols).to_numpy(),
        }
    )
    aligned = wide.dropna(how="any")
    if len(aligned) < 2:
        raise PriceDataError("At least two common price dates are required.")
    # Returns are formed on the original date grid. This avoids bridging a missing observation.
    daily_returns = wide.pct_change(fill_method=None).dropna(how="any")
    if daily_returns.empty:
        raise PriceDataError("No consecutive common return observations are available.")
    return AlignedPrices(
        observations=clean,
        prices=aligned,
        daily_returns=daily_returns,
        coverage=coverage,
        dropped_price_dates=len(wide) - len(aligned),
        source=source,
        used_fallback=used_fallback,
    )


def load_with_csv_fallback(
    primary_csv: Path | None,
    fallback_csv: Path,
    symbols: list[str],
    *,
    fallback_source: str,
) -> AlignedPrices:
    """Use the checked local fallback only when the primary file is absent."""
    use_fallback = primary_csv is None or not primary_csv.is_file()
    chosen = fallback_csv if use_fallback else primary_csv
    observations = load_price_csv(chosen)
    label = fallback_source if use_fallback else f"csv:{chosen.name}"
    return align_prices(observations, symbols, source=label, used_fallback=use_fallback)
