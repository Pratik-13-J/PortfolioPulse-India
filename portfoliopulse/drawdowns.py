"""Drawdown episodes, duration and recovery time on observed wealth levels.

Input is an observed cumulative price-return path (``1 + value`` is wealth),
such as ``BuyAndHoldReturns.cumulative_portfolio_return``. The first observed
level is the starting peak, matching the Phase 4 ``_observed_max_drawdown``
convention. Missing levels are dropped, never filled.

For each episode (wealth first falls below its running peak until it first
returns to at least that peak):

* depth = trough wealth / peak wealth - 1 (<= 0)
* observations_to_trough = sessions from peak to trough
* duration_observations = sessions from peak to recovery (peak-to-recovery)
* recovery_observations = sessions from trough to recovery (recovery time)
* calendar-day equivalents use date differences

"Sessions" count the dataset's observed dates, which are not independently
validated against an NSE trading calendar. An unrecovered episode has no
recovery date; its durations run to the last observation and are flagged.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

EPISODE_COLUMNS = [
    "peak_date", "trough_date", "recovery_date", "depth", "recovered",
    "observations_to_trough", "duration_observations", "recovery_observations",
    "calendar_days_to_trough", "duration_calendar_days", "recovery_calendar_days",
]


def _wealth(cumulative_return: pd.Series) -> pd.Series:
    if not isinstance(cumulative_return, pd.Series):
        raise TypeError("cumulative_return must be a pandas Series.")
    observed = cumulative_return.dropna()
    if len(observed) < 2:
        raise ValueError("At least two observed levels are required for drawdown durations.")
    if observed.index.has_duplicates or not observed.index.is_monotonic_increasing:
        raise ValueError("Observed dates must be unique and increasing.")
    wealth = observed.astype(float) + 1.0
    if not np.isfinite(wealth.to_numpy()).all() or (wealth <= 0).any():
        raise ValueError("Observed wealth levels must be finite and positive.")
    return wealth


def drawdown_episodes(cumulative_return: pd.Series) -> pd.DataFrame:
    """One row per drawdown episode, in chronological order."""
    wealth = _wealth(cumulative_return)
    dates, values = wealth.index, wealth.to_numpy()
    rows: list[dict[str, Any]] = []
    peak_pos, trough_pos, in_drawdown = 0, 0, False

    def close(recovery_pos: int | None) -> None:
        end = recovery_pos if recovery_pos is not None else len(values) - 1
        rows.append({
            "peak_date": dates[peak_pos], "trough_date": dates[trough_pos],
            "recovery_date": dates[recovery_pos] if recovery_pos is not None else pd.NaT,
            "depth": float(values[trough_pos] / values[peak_pos] - 1.0),
            "recovered": recovery_pos is not None,
            "observations_to_trough": trough_pos - peak_pos,
            "duration_observations": end - peak_pos,
            "recovery_observations": end - trough_pos,
            "calendar_days_to_trough": int((dates[trough_pos] - dates[peak_pos]).days),
            "duration_calendar_days": int((dates[end] - dates[peak_pos]).days),
            "recovery_calendar_days": int((dates[end] - dates[trough_pos]).days),
        })

    for pos in range(1, len(values)):
        if in_drawdown:
            if values[pos] >= values[peak_pos]:
                close(pos)
                in_drawdown, peak_pos = False, pos
            elif values[pos] < values[trough_pos]:
                trough_pos = pos
        elif values[pos] < values[peak_pos]:
            in_drawdown, trough_pos = True, pos
        else:
            peak_pos = pos
    if in_drawdown:
        close(None)
    return pd.DataFrame(rows, columns=EPISODE_COLUMNS)


def drawdown_summary(cumulative_return: pd.Series) -> dict[str, Any]:
    """Maximum-drawdown episode, longest episode and current underwater state."""
    wealth = _wealth(cumulative_return)
    episodes = drawdown_episodes(cumulative_return)
    summary: dict[str, Any] = {
        "observations": int(len(wealth)), "episodes": int(len(episodes)),
        "first_date": wealth.index[0].strftime("%Y-%m-%d"),
        "last_date": wealth.index[-1].strftime("%Y-%m-%d"),
    }
    if episodes.empty:
        summary.update({"max_drawdown": 0.0, "currently_underwater": False, "current_drawdown": 0.0})
        return summary

    def describe(row: pd.Series) -> dict[str, Any]:
        return {
            "peak_date": row["peak_date"].strftime("%Y-%m-%d"),
            "trough_date": row["trough_date"].strftime("%Y-%m-%d"),
            "recovery_date": None if not row["recovered"] else row["recovery_date"].strftime("%Y-%m-%d"),
            "depth": float(row["depth"]), "recovered": bool(row["recovered"]),
            **{name: int(row[name]) for name in EPISODE_COLUMNS[5:]},
        }

    deepest = episodes.loc[episodes["depth"].idxmin()]
    longest = episodes.loc[episodes["duration_observations"].idxmax()]
    last = episodes.iloc[-1]
    underwater = not bool(last["recovered"])
    summary.update({
        "max_drawdown": float(deepest["depth"]),
        "max_drawdown_episode": describe(deepest),
        "longest_drawdown_episode": describe(longest),
        "currently_underwater": underwater,
        "current_drawdown": float(wealth.iloc[-1] / wealth.cummax().iloc[-1] - 1.0),
        "current_underwater_observations": int(last["duration_observations"]) if underwater else 0,
    })
    return summary
