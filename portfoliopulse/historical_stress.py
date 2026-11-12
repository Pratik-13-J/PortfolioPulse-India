"""Historical stress events replayed on the current fixed-quantity holdings.

An event is an analyst-defined window [start, end]. Anchors are the last
observed benchmark closes on or before ``start`` and ``end``; the dataset must
contain an observation on or before ``start`` and at least one on or after
``end``, so a partially covered window is never treated as complete. Nothing
is forward-filled:

* ``replayed`` - each holding's shock is its own observed close-to-close
  return between the anchors (component ``historical_observed``). A holding
  missing a close at either anchor instead gets beta_i * benchmark move
  (``beta_propagated``) with a warning.
* ``model_propagated`` - the dataset does not cover the window but a
  source-labelled NIFTY 50 reference series does: every holding gets
  beta_i * index move. Betas come from the dataset sample, not the event.
* ``unavailable`` - no covering data; the reason is reported. No index move is
  ever filled in from memory or defaults.

``path_max_drawdown`` revalues the current quantities on the window's dates
where every holding has a close (dates with a gap are skipped, not filled).

Replays apply past moves to *today's* quantities and values: they are
hypothetical revaluations, not a reconstruction of past portfolio P&L.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

from portfoliopulse.drawdowns import drawdown_episodes, drawdown_summary
from portfoliopulse.stress_scenarios import ShockVector, make_vector


@dataclass(frozen=True)
class EventOutcome:
    event_id: str
    name: str
    start: str
    end: str
    window_basis: str
    status: str
    reason: str | None = None
    vector: ShockVector | None = None
    anchors: dict[str, str] = field(default_factory=dict)
    benchmark_move: float | None = None
    path_max_drawdown: float | None = None
    observations_in_window: int = 0
    warnings: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "name": self.name, "start": self.start, "end": self.end,
            "window_basis": self.window_basis, "status": self.status, "reason": self.reason,
            "anchors": self.anchors, "benchmark_move": self.benchmark_move,
            "path_max_drawdown": self.path_max_drawdown, "observations_in_window": self.observations_in_window,
            "warnings": list(self.warnings),
        }


def _anchors(index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp] | str:
    if len(index) == 0:
        return "no observations"
    if index[0] > start:
        return f"series begins {index[0]:%Y-%m-%d}, after the event start {start:%Y-%m-%d}"
    if index[-1] < end:
        return f"series ends {index[-1]:%Y-%m-%d}, before the event end {end:%Y-%m-%d}"
    first = index[index <= start][-1]
    last = index[index <= end][-1]
    if last <= first:
        return "no observation inside the event window"
    return first, last


def replay_event(
    event: Mapping[str, Any],
    price_grid: pd.DataFrame,
    holdings: pd.DataFrame,
    benchmark: str,
    betas: pd.Series,
    *,
    benchmark_reference: pd.Series | None = None,
    reference_label: str | None = None,
) -> EventOutcome:
    """Replay one event window; see the module docstring for the rules."""
    start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
    base = dict(event_id=event["event_id"], name=event["name"], start=event["start"], end=event["end"],
                window_basis=event.get("window_basis", ""))
    symbols = list(holdings.index)
    bench = price_grid[benchmark].dropna()
    anchors = _anchors(bench.index, start, end)
    if isinstance(anchors, str):
        if benchmark_reference is None:
            return EventOutcome(**base, status="unavailable",
                                reason=f"Dataset benchmark {anchors}; no reference index series supplied.")
        ref = benchmark_reference.dropna()
        ref_anchors = _anchors(ref.index, start, end)
        if isinstance(ref_anchors, str):
            return EventOutcome(**base, status="unavailable",
                                reason=f"Dataset benchmark {anchors}; reference series {ref_anchors}.")
        a, b = ref_anchors
        move = float(ref[b] / ref[a] - 1.0)
        warnings = ("Model-propagated: holding moves are beta_i * reference index move; betas are estimated on "
                    "the dataset sample, not on the event window.",)
        vector = make_vector(
            event["event_id"], event["name"], "historical_model_propagated", "historical_model_propagated",
            {"beta_propagated": betas.reindex(symbols) * move}, symbols, benchmark_move=move, warnings=warnings,
            assumptions={"benchmark_reference": reference_label, "propagation": "beta_i * index move"},
        )
        return EventOutcome(**base, status="model_propagated", vector=vector, benchmark_move=move,
                            anchors={"start": f"{a:%Y-%m-%d}", "end": f"{b:%Y-%m-%d}"},
                            observations_in_window=int(((ref.index > a) & (ref.index <= b)).sum()),
                            warnings=warnings)

    a, b = anchors
    move = float(bench[b] / bench[a] - 1.0)
    warnings: list[str] = []
    if a != start or b != end:
        warnings.append(f"Nearest earlier observed closes used: {a:%Y-%m-%d} and {b:%Y-%m-%d}.")
    observed, propagated = {}, {}
    for symbol in symbols:
        p0 = price_grid.at[a, symbol] if a in price_grid.index else float("nan")
        p1 = price_grid.at[b, symbol] if b in price_grid.index else float("nan")
        if pd.notna(p0) and pd.notna(p1):
            observed[symbol] = float(p1 / p0 - 1.0)
        else:
            if pd.isna(betas.get(symbol)):
                return EventOutcome(**base, status="unavailable",
                                    reason=f"{symbol} lacks closes at the anchors and has no beta.")
            propagated[symbol] = float(betas[symbol]) * move
            warnings.append(f"{symbol} has no close at an anchor date; beta-propagated from the benchmark move.")
    window = price_grid.loc[a:b, symbols].dropna(how="any")
    path_dd = None
    if not propagated and len(window) >= 2 and window.index[0] == a and window.index[-1] == b:
        value = (window * holdings["quantity"]).sum(axis=1)
        path_dd = float(drawdown_summary(value / value.iloc[0] - 1.0)["max_drawdown"])
    vector = make_vector(
        event["event_id"], event["name"], "historical_replay", "historical_replay",
        {"historical_observed": observed, "beta_propagated": propagated}, symbols,
        benchmark_move=move, warnings=tuple(warnings),
        assumptions={"anchors": [f"{a:%Y-%m-%d}", f"{b:%Y-%m-%d}"],
                     "interpretation": "observed event-window returns applied to current holdings"},
    )
    return EventOutcome(**base, status="replayed", vector=vector, benchmark_move=move,
                        anchors={"start": f"{a:%Y-%m-%d}", "end": f"{b:%Y-%m-%d}"}, path_max_drawdown=path_dd,
                        observations_in_window=int(((bench.index > a) & (bench.index <= b)).sum()),
                        warnings=tuple(warnings))


def data_identified_events(
    price_grid: pd.DataFrame, benchmark_daily_returns: pd.Series, benchmark: str,
) -> list[dict[str, Any]]:
    """Worst observed benchmark drawdown episode and worst single valid day."""
    events: list[dict[str, Any]] = []
    bench = price_grid[benchmark].dropna()
    episodes = drawdown_episodes(bench / bench.iloc[0] - 1.0)
    if not episodes.empty:
        worst = episodes.loc[episodes["depth"].idxmin()]
        events.append({
            "event_id": "worst_benchmark_drawdown_in_dataset",
            "name": f"Worst observed NIFTY 50 drawdown ({worst['peak_date']:%Y-%m-%d} to {worst['trough_date']:%Y-%m-%d})",
            "start": f"{worst['peak_date']:%Y-%m-%d}", "end": f"{worst['trough_date']:%Y-%m-%d}",
            "window_basis": "Data-identified: deepest peak-to-trough episode of the dataset's own benchmark closes.",
        })
    if len(benchmark_daily_returns):
        day = benchmark_daily_returns.idxmin()
        previous = bench.index[bench.index < day]
        if len(previous):
            events.append({
                "event_id": "worst_benchmark_day_in_dataset",
                "name": f"Worst valid NIFTY 50 daily return ({day:%Y-%m-%d})",
                "start": f"{previous[-1]:%Y-%m-%d}", "end": f"{day:%Y-%m-%d}",
                "window_basis": "Data-identified: most negative valid daily benchmark return in the dataset.",
            })
    return events
