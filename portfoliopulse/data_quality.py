"""Source-independent checks and calendar accounting for daily NSE candles.

This module validates the *declared* price basis. It cannot independently
verify a provider's corporate-action adjustments or invent missing sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = frozenset({
    "symbol", "date", "open", "high", "low", "close", "volume",
    "source", "retrieved_at_utc", "price_basis", "interval",
})
PRICE_BASES = frozenset({"unadjusted_ohlcv", "provider_adjusted_ohlcv"})
NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume")


class DataQualityError(ValueError):
    """The candle batch cannot be analyzed without changing its meaning."""


@dataclass(frozen=True)
class CandleQualityReport:
    candles: pd.DataFrame
    aligned_prices: pd.DataFrame
    daily_returns: pd.DataFrame
    coverage: pd.DataFrame
    excluded_price_dates: pd.DataFrame
    excluded_return_dates: pd.DataFrame
    missing_dates_by_symbol: dict[str, tuple[str, ...]]
    source: str
    price_basis: str
    start: str | None
    end_exclusive: str | None
    outside_window_rows: int
    union_dates_count: int

    @property
    def common_price_dates_count(self) -> int:
        return len(self.aligned_prices)

    @property
    def valid_return_dates_count(self) -> int:
        return len(self.daily_returns)

    def to_summary(self) -> dict[str, Any]:
        """JSON-ready audit details, including every excluded observed date."""
        return {
            "source": self.source,
            "price_basis": self.price_basis,
            "start": self.start,
            "end_exclusive": self.end_exclusive,
            "valid_candle_rows": len(self.candles),
            "outside_window_rows": self.outside_window_rows,
            "union_dates": self.union_dates_count,
            "common_price_dates": self.common_price_dates_count,
            "valid_return_dates": self.valid_return_dates_count,
            "excluded_price_date_count": len(self.excluded_price_dates),
            "excluded_return_date_count": len(self.excluded_return_dates),
            "coverage": self.coverage.to_dict(orient="records"),
            "missing_dates_by_symbol": {
                symbol: list(days) for symbol, days in self.missing_dates_by_symbol.items()
            },
            "excluded_price_dates": self.excluded_price_dates.to_dict(orient="records"),
            "excluded_return_dates": self.excluded_return_dates.to_dict(orient="records"),
        }


def _iso_day(value: str, label: str) -> pd.Timestamp:
    if not isinstance(value, str):
        raise DataQualityError(f"{label} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DataQualityError(f"{label} must use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise DataQualityError(f"{label} must use YYYY-MM-DD.")
    return pd.Timestamp(parsed)


def _candle_day(value: object) -> pd.Timestamp:
    if isinstance(value, str):
        return _iso_day(value, "Candle date")
    if isinstance(value, date):
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DataQualityError("Candle dates must be valid Indian calendar dates.") from exc
        if parsed.tzinfo is not None or parsed != parsed.normalize():
            raise DataQualityError("Candle dates must be naive Indian calendar dates without a time.")
        return parsed
    raise DataQualityError("Candle dates must use YYYY-MM-DD or naive calendar dates.")


def _utc_retrieval(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError("Every candle needs a UTC retrieval timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataQualityError("Candle retrieval timestamps must be ISO UTC timestamps.") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise DataQualityError("Candle retrieval timestamps must be in UTC.")


def _one_label(series: pd.Series, label: str) -> str:
    if series.isna().any() or not series.map(lambda item: isinstance(item, str) and bool(item.strip())).all():
        raise DataQualityError(f"Every candle needs a non-empty {label}.")
    labels = {item.strip() for item in series}
    if len(labels) != 1:
        raise DataQualityError(f"A candle batch must have one consistent {label}.")
    return next(iter(labels))


def _normalize_candles(candles: pd.DataFrame, symbols: Sequence[str]) -> tuple[pd.DataFrame, str, str]:
    if not isinstance(candles, pd.DataFrame) or candles.empty:
        raise DataQualityError("The daily candle batch is empty.")
    if candles.columns.has_duplicates:
        raise DataQualityError("Duplicate candle column names are not allowed.")
    missing = REQUIRED_COLUMNS - set(candles.columns)
    if missing:
        raise DataQualityError(f"Missing candle columns: {', '.join(sorted(missing))}.")
    if not symbols or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols):
        raise DataQualityError("Provide a non-empty list of named symbols.")
    if len(symbols) != len(set(symbols)):
        raise DataQualityError("Configured symbols must be unique.")
    clean = candles.copy()
    if clean["symbol"].isna().any() or not clean["symbol"].map(lambda item: isinstance(item, str) and bool(item.strip())).all():
        raise DataQualityError("Every candle needs a non-empty symbol.")
    clean["symbol"] = clean["symbol"].str.strip()
    unexpected = set(clean["symbol"]) - set(symbols)
    if unexpected:
        raise DataQualityError(f"Unexpected candle symbols: {', '.join(sorted(unexpected))}.")
    clean["date"] = pd.DatetimeIndex([_candle_day(value) for value in clean["date"]])
    if clean.duplicated(["symbol", "date"]).any():
        raise DataQualityError("Duplicate symbol/date candles are not allowed.")
    source = _one_label(clean["source"], "source")
    price_basis = _one_label(clean["price_basis"], "price basis")
    if price_basis not in PRICE_BASES:
        raise DataQualityError(f"Unsupported price basis: {price_basis}.")
    if _one_label(clean["interval"], "interval") != "ONE_DAY":
        raise DataQualityError("Only ONE_DAY candles can be analyzed as daily data.")
    clean["source"] = source
    clean["price_basis"] = price_basis
    clean["interval"] = "ONE_DAY"
    for value in clean["retrieved_at_utc"]:
        _utc_retrieval(value)
    for column in NUMERIC_COLUMNS:
        if clean[column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise DataQualityError(f"Candle {column} must be numeric, not Boolean.")
        try:
            values = pd.to_numeric(clean[column], errors="raise").to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            raise DataQualityError(f"Candle {column} must be numeric.") from exc
        if not np.isfinite(values).all():
            raise DataQualityError(f"Candle {column} must be finite.")
        invalid = (values < 0).any() if column == "volume" else (values <= 0).any()
        if invalid:
            requirement = "non-negative" if column == "volume" else "positive"
            raise DataQualityError(f"Candle {column} must be {requirement}.")
        clean[column] = values
    if (
        (clean["low"] > clean[["open", "close"]].min(axis=1))
        | (clean["high"] < clean[["open", "close"]].max(axis=1))
        | (clean["high"] < clean["low"])
    ).any():
        raise DataQualityError("Candle OHLC relationships are inconsistent.")
    if {"trading_symbol", "exchange"}.issubset(clean.columns):
        if clean[["trading_symbol", "exchange"]].isna().any().any():
            raise DataQualityError("Candle instrument identity is incomplete.")
        identities = clean.groupby("symbol")[["trading_symbol", "exchange"]].nunique(dropna=False)
        if (identities > 1).any().any():
            raise DataQualityError("A symbol maps to multiple trading symbols or exchanges.")
    return clean, source, price_basis


def validate_daily_candles(
    candles: pd.DataFrame,
    symbols: Sequence[str],
    *,
    start: str | None = None,
    end_exclusive: str | None = None,
    expected_price_basis: str | None = None,
) -> CandleQualityReport:
    """Validate a batch and report exactly which observed dates cannot be used.

    `start` is inclusive and `end_exclusive` is exclusive. The calendar is the
    union of actual observations, not generated weekdays. A return is valid
    only when all symbols have prices on this and the preceding union date.
    """
    if (start is None) != (end_exclusive is None):
        raise DataQualityError("Provide both start and end_exclusive, or neither.")
    first = _iso_day(start, "start") if start is not None else None
    stop = _iso_day(end_exclusive, "end_exclusive") if end_exclusive is not None else None
    if first is not None and first >= stop:
        raise DataQualityError("start must precede end_exclusive.")
    clean, source, price_basis = _normalize_candles(candles, symbols)
    if expected_price_basis is not None and price_basis != expected_price_basis:
        raise DataQualityError("Candle price basis does not match the declared dataset metadata.")
    outside_window_rows = 0
    if first is not None:
        in_window = clean["date"].ge(first) & clean["date"].lt(stop)
        outside_window_rows = int((~in_window).sum())
        clean = clean.loc[in_window].copy()
    absent = set(symbols) - set(clean["symbol"])
    if absent:
        raise DataQualityError(f"No in-window candles for: {', '.join(sorted(absent))}.")
    clean = clean.sort_values(["date", "symbol"]).reset_index(drop=True)
    wide = clean.pivot(index="date", columns="symbol", values="close").sort_index().reindex(columns=list(symbols))
    common_prices = wide.dropna(how="any")
    if len(common_prices) < 2:
        raise DataQualityError("At least two shared price dates are required.")
    # Compute on the original union grid before dropping incomplete dates.
    returns_grid = wide.pct_change(fill_method=None)
    daily_returns = returns_grid.dropna(how="any")
    if daily_returns.empty:
        raise DataQualityError("No consecutive shared return dates are available.")
    missing_dates_by_symbol: dict[str, tuple[str, ...]] = {
        symbol: tuple(day.strftime("%Y-%m-%d") for day in wide.index[wide[symbol].isna()])
        for symbol in symbols
    }
    coverage = pd.DataFrame({
        "symbol": list(symbols),
        "observations": [int(wide[symbol].notna().sum()) for symbol in symbols],
        "missing_on_union_dates": [len(missing_dates_by_symbol[symbol]) for symbol in symbols],
    })
    price_exclusions = [
        {
            "date": day.strftime("%Y-%m-%d"),
            "reason": "missing_candle",
            "missing_symbols": ",".join(symbol for symbol in symbols if pd.isna(wide.at[day, symbol])),
        }
        for day in wide.index if wide.loc[day].isna().any()
    ]
    excluded_price_dates = pd.DataFrame(price_exclusions, columns=["date", "reason", "missing_symbols"])
    return_exclusions = []
    for position, day in enumerate(wide.index):
        if not returns_grid.loc[day].isna().any():
            continue
        current_missing = [symbol for symbol in symbols if pd.isna(wide.at[day, symbol])]
        prior_missing = [] if position == 0 else [
            symbol for symbol in symbols if pd.isna(wide.at[wide.index[position - 1], symbol])
        ]
        reasons = []
        if position == 0:
            reasons.append("no_previous_observed_date")
        if current_missing:
            reasons.append("missing_current_candle")
        if prior_missing:
            reasons.append("missing_previous_candle")
        return_exclusions.append({
            "date": day.strftime("%Y-%m-%d"),
            "reason": ",".join(reasons),
            "missing_current_symbols": ",".join(current_missing),
            "missing_previous_symbols": ",".join(prior_missing),
        })
    excluded_return_dates = pd.DataFrame(return_exclusions, columns=[
        "date", "reason", "missing_current_symbols", "missing_previous_symbols",
    ])
    return CandleQualityReport(
        candles=clean,
        aligned_prices=common_prices,
        daily_returns=daily_returns,
        coverage=coverage,
        excluded_price_dates=excluded_price_dates,
        excluded_return_dates=excluded_return_dates,
        missing_dates_by_symbol=missing_dates_by_symbol,
        source=source,
        price_basis=price_basis,
        start=start,
        end_exclusive=end_exclusive,
        outside_window_rows=outside_window_rows,
        union_dates_count=len(wide),
    )
