"""Hypothetical shock calculations; assumptions will be validated in Phase 5."""

import pandas as pd


def scenario_impact(
    latest_values: pd.Series, shocks_by_class: dict[str, float], classes: pd.Series
) -> tuple[float, pd.Series]:
    shocks = classes.reindex(latest_values.index).map(shocks_by_class).fillna(0.0)
    contributions = latest_values * shocks
    return float(contributions.sum()), contributions.sort_values()
