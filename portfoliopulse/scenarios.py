"""Configurable hypothetical one-period shocks for Indian equity holdings."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


def _shock(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be numeric, not Boolean, and no smaller than -100%.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric, not Boolean, and no smaller than -100%.") from exc
    if not math.isfinite(result) or result < -1.0:
        raise ValueError(f"{label} must be a finite number no smaller than -100%.")
    return result


def scenario_shocks(
    holdings: Sequence[Mapping[str, object]],
    scenario_type: str,
    *,
    broad_shock: float = -0.10,
    stressed_shock: float = -0.20,
    residual_shock: float = -0.05,
    individual_shocks: Mapping[str, float] | None = None,
    sector_shocks: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Return one shock per holding; presets are illustrative and configurable.

    ``broad_market`` shocks every holding by ``broad_shock``. ``banking_stress``
    applies ``stressed_shock`` to Banking sector names and ``residual_shock``
    to others. ``it_correction`` applies the same sector rule to Information
    Technology. Custom individual/sector scenarios default unspecified names
    to zero. User-supplied sector shocks take precedence over defaults.
    """
    if not holdings:
        raise ValueError("At least one holding is required.")
    symbols = [str(row["symbol"]) for row in holdings]
    if len(set(symbols)) != len(symbols) or any(not s.strip() for s in symbols):
        raise ValueError("Holding symbols must be unique and non-empty.")
    broad = _shock(broad_shock, "broad_shock")
    stressed = _shock(stressed_shock, "stressed_shock")
    residual = _shock(residual_shock, "residual_shock")
    custom = dict(individual_shocks or {})
    sectors = dict(sector_shocks or {})
    unknown_symbols = set(custom) - set(symbols)
    if unknown_symbols:
        raise ValueError(f"Unknown holding shock targets: {', '.join(sorted(unknown_symbols))}.")
    unknown_sectors = set(sectors) - {str(row["sector"]) for row in holdings}
    if unknown_sectors:
        raise ValueError(f"Unknown sector shock targets: {', '.join(sorted(unknown_sectors))}.")
    custom = {s: _shock(v, f"shock for {s}") for s, v in custom.items()}
    sectors = {s: _shock(v, f"shock for sector {s}") for s, v in sectors.items()}

    if scenario_type == "broad_market":
        result = {symbol: broad for symbol in symbols}
    elif scenario_type in {"banking_stress", "it_correction"}:
        target = "Banking" if scenario_type == "banking_stress" else "Information Technology"
        result = {
            str(row["symbol"]): (stressed if str(row["sector"]).casefold() == target.casefold() else residual)
            for row in holdings
        }
    elif scenario_type in {"individual", "sector"}:
        result = {symbol: 0.0 for symbol in symbols}
    else:
        raise ValueError("scenario_type must be broad_market, banking_stress, it_correction, individual, or sector.")

    for row in holdings:
        symbol, sector = str(row["symbol"]), str(row["sector"])
        if sector in sectors:
            result[symbol] = sectors[sector]
        if symbol in custom:
            result[symbol] = custom[symbol]
    if scenario_type == "individual" and not custom:
        raise ValueError("An individual scenario requires at least one individual shock.")
    if scenario_type == "sector" and not sectors:
        raise ValueError("A sector scenario requires at least one sector shock.")
    return result


def scenario_impact(
    latest_values: pd.Series, shocks_by_class: dict[str, float], classes: pd.Series
) -> tuple[float, pd.Series]:
    """Backward-compatible fictional-dashboard scenario helper."""
    shocks = classes.reindex(latest_values.index).map(shocks_by_class).fillna(0.0)
    contributions = latest_values * shocks
    return float(contributions.sum()), contributions.sort_values()
