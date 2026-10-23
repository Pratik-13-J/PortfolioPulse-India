"""Compare one provider's daily NIFTY 50 closes with a supplied reference CSV."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd


def _clean_close_series(series: pd.Series, label: str) -> pd.Series:
    if not isinstance(series, pd.Series) or not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError(f"{label} closes must be a Series indexed by calendar dates.")
    if series.empty or series.index.has_duplicates or series.index.hasnans:
        raise ValueError(f"{label} dates must be non-empty, unique and present.")
    if series.index.tz is not None or not series.index.equals(series.index.normalize()):
        raise ValueError(f"{label} dates must be timezone-free calendar dates.")
    if series.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError(f"{label} closes must be numeric, not Boolean.")
    try:
        values = pd.to_numeric(series, errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} closes must be numeric.") from exc
    if not np.isfinite(values.to_numpy()).all() or (values <= 0).any():
        raise ValueError(f"{label} closes must be positive and finite.")
    return values.sort_index().rename(label)


def compare_daily_closes(
    provider_closes: pd.Series,
    reference_closes: pd.Series,
    *,
    tolerance_points: float = 0.01,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Full-outer-join calendar dates and classify close-price differences."""
    if isinstance(tolerance_points, (bool, np.bool_)):
        raise ValueError("tolerance_points must be finite and non-negative.")
    tolerance = float(tolerance_points)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance_points must be finite and non-negative.")
    provider = _clean_close_series(provider_closes, "provider")
    reference = _clean_close_series(reference_closes, "reference")
    joined = pd.concat([provider, reference], axis=1, join="outer").sort_index()
    joined["difference_points"] = joined["provider"] - joined["reference"]
    joined["absolute_difference_points"] = joined["difference_points"].abs()
    joined["relative_difference_pct"] = 100.0 * joined["difference_points"] / joined["reference"]
    joined["status"] = np.select(
        [
            joined["provider"].isna() & joined["reference"].notna(),
            joined["reference"].isna() & joined["provider"].notna(),
            joined["absolute_difference_points"].le(tolerance),
        ],
        ["provider_missing", "reference_missing", "within_tolerance"],
        default="difference_exceeds_tolerance",
    )
    overlap = joined.dropna(subset=["provider", "reference"])
    exact = int(overlap["difference_points"].eq(0).sum())
    within = int(overlap["absolute_difference_points"].le(tolerance).sum())
    mismatches = int(overlap["absolute_difference_points"].gt(tolerance).sum())
    summary: dict[str, object] = {
        "provider_observation_count": int(provider.notna().sum()),
        "reference_observation_count": int(reference.notna().sum()),
        "overlap_count": int(len(overlap)),
        "exact_match_count": exact,
        "within_tolerance_count": within,
        "mismatch_count": mismatches,
        "provider_missing_on_reference_dates": int(joined["status"].eq("provider_missing").sum()),
        "reference_missing_on_provider_dates": int(joined["status"].eq("reference_missing").sum()),
        "tolerance_points": tolerance,
        "maximum_absolute_difference_points": (
            float(overlap["absolute_difference_points"].max()) if len(overlap) else None
        ),
    }
    return joined, summary


def parse_nse_index_csv(path: str | Path) -> pd.Series:
    """Read an NSE Historical Index Data CSV with Date and Close columns."""
    frame = pd.read_csv(path, encoding="utf-8-sig")
    normalized = {
        str(column).strip().casefold().replace(" ", "").replace("_", ""): column
        for column in frame.columns
    }
    date_key = next((normalized[key] for key in ("date", "indexdate", "tradingdate") if key in normalized), None)
    close_key = next((normalized[key] for key in ("close", "closeindex", "indexclose", "closeprice") if key in normalized), None)
    if date_key is None or close_key is None:
        raise ValueError("NSE reference CSV must have Date and Close columns.")
    index_name_key = normalized.get("indexname")
    if index_name_key is not None:
        names = frame[index_name_key].astype(str).str.strip().str.upper()
        if not names.eq("NIFTY 50").all():
            raise ValueError("NSE reference CSV must contain only NIFTY 50 price-index rows.")
    dates = pd.to_datetime(frame[date_key], dayfirst=True, errors="coerce")
    close_text = frame[close_key].astype(str).str.replace(",", "", regex=False).str.strip()
    closes = pd.to_numeric(close_text, errors="coerce")
    if dates.isna().any() or closes.isna().any():
        raise ValueError("NSE reference CSV has an invalid date or close value.")
    index = pd.DatetimeIndex(dates.dt.normalize())
    result = pd.Series(closes.to_numpy(dtype=float), index=index, name="reference")
    return _clean_close_series(result, "reference")
