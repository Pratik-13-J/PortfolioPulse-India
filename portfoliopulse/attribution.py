"""Holding-level INR stress attribution and portfolio allocation alerts."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd


def scenario_attribution(
    holding_values_inr: Mapping[str, float], shocks: Mapping[str, float],
) -> tuple[pd.DataFrame, float, float]:
    """Calculate INR impacts; portfolio-level contribution shares reconcile."""
    if not holding_values_inr or set(holding_values_inr) != set(shocks):
        raise ValueError("Values and shocks must cover the same non-empty set of holdings.")
    values: dict[str, float] = {}
    clean_shocks: dict[str, float] = {}
    for symbol, raw in holding_values_inr.items():
        if isinstance(raw, (bool, np.bool_)):
            raise ValueError(f"{symbol} holding value must be numeric, not Boolean, and non-negative.")
        if isinstance(shocks[symbol], (bool, np.bool_)):
            raise ValueError(f"{symbol} shock must be numeric, not Boolean, and no smaller than -100%.")
        value = float(raw)
        shock = float(shocks[symbol])
        if not math.isfinite(value) or value < 0 or not math.isfinite(shock) or shock < -1:
            raise ValueError(f"Invalid value or shock for {symbol}.")
        values[symbol], clean_shocks[symbol] = value, shock
    total_value = sum(values.values())
    if not math.isfinite(total_value) or total_value <= 0:
        raise ValueError("Total portfolio value must be positive and finite.")
    impact = {symbol: values[symbol] * clean_shocks[symbol] for symbol in values}
    total_impact = math.fsum(impact.values())
    frame = pd.DataFrame({
        "holding_value_inr": pd.Series(values, dtype=float),
        "shock": pd.Series(clean_shocks, dtype=float),
        "impact_inr": pd.Series(impact, dtype=float),
        "portfolio_impact_pct": pd.Series(impact, dtype=float) / total_value,
    })
    if not math.isclose(float(frame["impact_inr"].sum()), total_impact, rel_tol=1e-12, abs_tol=1e-8):
        raise ArithmeticError("Holding INR contributions do not reconcile to scenario impact.")
    return frame.sort_index(), total_impact, total_impact / total_value


def allocation_alerts(
    actual_weights: Mapping[str, float], target_weights: Mapping[str, float], *,
    drift_threshold: float = 0.05, single_name_limit: float = 0.40,
    hhi_limit: float = 0.25,
) -> dict[str, object]:
    """Flag allocation drift in percentage points and name/HHI concentration."""
    if not actual_weights or set(actual_weights) != set(target_weights):
        raise ValueError("Actual and target weights must cover the same holdings.")
    thresholds = {"drift_threshold": drift_threshold, "single_name_limit": single_name_limit, "hhi_limit": hhi_limit}
    for name, value in thresholds.items():
        if isinstance(value, (bool, np.bool_)) or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
            raise ValueError(f"{name} must be finite and between 0 and 1.")
    if any(isinstance(w, (bool, np.bool_)) for w in [*actual_weights.values(), *target_weights.values()]):
        raise ValueError("Weights must be numeric, not Boolean.")
    actual = {s: float(w) for s, w in actual_weights.items()}
    target = {s: float(w) for s, w in target_weights.items()}
    for symbol in actual:
        if not math.isfinite(actual[symbol]) or not math.isfinite(target[symbol]) or actual[symbol] < 0 or target[symbol] < 0:
            raise ValueError("Weights must be finite and non-negative.")
    if not math.isclose(sum(actual.values()), 1.0, abs_tol=1e-8) or not math.isclose(sum(target.values()), 1.0, abs_tol=1e-8):
        raise ValueError("Actual and target weights must each sum to 100%.")
    drifts = {s: actual[s] - target[s] for s in actual}
    hhi = math.fsum(w * w for w in actual.values())
    return {
        "drift_threshold": float(drift_threshold),
        "single_name_limit": float(single_name_limit),
        "hhi_limit": float(hhi_limit),
        "drifts": {s: {"actual_weight": actual[s], "target_weight": target[s], "drift": drifts[s],
                       "alert": abs(drifts[s]) > drift_threshold} for s in sorted(actual)},
        "concentration": {
            "hhi": hhi,
            "effective_number_of_holdings": (1.0 / hhi if hhi else None),
            "single_name_alerts": [s for s in sorted(actual) if actual[s] > single_name_limit],
            "hhi_alert": hhi > hhi_limit,
        },
    }
