"""Read-only loaders for persisted Phase 3/4/5 engine runs (no recomputation).

Connections are opened with SQLite ``mode=ro`` so the dashboard cannot write
through these helpers. A database without a given engine's run (or an older
schema without the optimization tables) yields ``None`` and the UI shows how
to create it with the CLI.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

ENGINES = {"risk": "phase3_risk_v1", "stress": "phase4_stress_v1", "optimization": "phase5_opt_v1"}
CLI = {
    "risk": "scripts.analyze_risk",
    "stress": "scripts.analyze_stress",
    "optimization": "scripts.analyze_optimization",
}


@dataclass(frozen=True)
class StoredRun:
    analysis_id: int
    dataset_id: int
    engine_version: str
    created_at_utc: str
    window_start: str
    window_end: str
    settings: dict[str, Any]
    quality: dict[str, Any]
    metrics: pd.DataFrame  # columns: metric_name, value, unit

    def value(self, name: str) -> float | None:
        match = self.metrics.loc[self.metrics["metric_name"] == name, "value"]
        return None if match.empty else float(match.iloc[0])


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_run(dataset_id: int, kind: str, *, db_path: Path) -> StoredRun | None:
    """Most recent stored run of one engine for a dataset, or None."""
    engine = ENGINES[kind]
    if not Path(db_path).exists():
        return None
    with closing(_connect(db_path)) as con:
        row = con.execute(
            """SELECT analysis_id, dataset_id, created_at_utc, window_start, window_end, settings_json, quality_json
               FROM analysis_metadata WHERE dataset_id=? AND json_extract(settings_json, '$.engine_version')=?
               ORDER BY analysis_id DESC LIMIT 1""", (dataset_id, engine),
        ).fetchone()
        if row is None:
            return None
        metrics = pd.read_sql_query(
            "SELECT metric_name, value, unit FROM analysis_results WHERE analysis_id=? ORDER BY metric_name",
            con, params=(row["analysis_id"],))
    return StoredRun(int(row["analysis_id"]), int(row["dataset_id"]), engine, str(row["created_at_utc"]),
                     str(row["window_start"]), str(row["window_end"]), json.loads(row["settings_json"]),
                     json.loads(row["quality_json"]), metrics)


def default_dataset_index(dataset_ids: list[int], *, db_path: Path) -> int:
    """Index of the first listed dataset (most recent first) with any stored engine run, else 0."""
    for index, dataset_id in enumerate(dataset_ids):
        if any(latest_run(dataset_id, kind, db_path=db_path) is not None for kind in ENGINES):
            return index
    return 0


def _split(name: str) -> list[str]:
    return name.split(":")


_MEASURES = {"var_1d": "var", "es_1d": "es", "benchmark_var_1d": "var", "benchmark_es_1d": "es",
             "var_1d_inr": "var_inr", "es_1d_inr": "es_inr",
             "var_1d_standard_error": "var_standard_error", "es_1d_standard_error": "es_standard_error"}


def risk_view(run: StoredRun) -> dict[str, Any]:
    """VaR/ES table, tails, backtests, drawdowns and components from a risk run."""
    rows = []
    for item in run.metrics.itertuples():
        parts = _split(item.metric_name)
        if len(parts) == 3 and parts[0] in _MEASURES:
            series = "benchmark" if parts[0].startswith("benchmark") else "portfolio"
            measure = _MEASURES[parts[0]]
            rows.append({"series": series, "method": parts[1], "confidence": parts[2], "measure": measure,
                         "value": item.value})
    long = pd.DataFrame(rows)
    table = (long.pivot_table(index=["series", "method", "confidence"], columns="measure", values="value")
             .reset_index() if not long.empty else long)
    backtests = []
    for item in run.metrics.itertuples():
        parts = _split(item.metric_name)
        if parts[0].startswith("backtest_") and len(parts) == 3:
            backtests.append({"method": parts[1], "confidence": parts[2], "stat": parts[0][len("backtest_"):],
                              "value": item.value})
    backtest_table = (pd.DataFrame(backtests).pivot_table(index=["method", "confidence"], columns="stat",
                                                          values="value").reset_index()
                      if backtests else pd.DataFrame())
    components = []
    for item in run.metrics.itertuples():
        parts = _split(item.metric_name)
        if parts[0] in ("component_var_1d", "component_es_1d") and len(parts) == 4:
            components.append({"measure": parts[0], "estimator": parts[1], "confidence": parts[2],
                               "symbol": parts[3], "value": item.value})
    return {
        "var_table": table, "tails": run.quality.get("tail_observations", {}),
        "backtests": backtest_table, "backtest_dates": run.quality.get("backtests", {}),
        "drawdowns": run.quality.get("drawdowns", {}), "components": pd.DataFrame(components),
        "undefined": run.quality.get("undefined_metrics", {}),
        "observations": run.quality.get("valid_return_observations"),
        "reliability": run.quality.get("tail_statistics_reliability"),
    }


def stress_view(run: StoredRun, *, db_path: Path) -> dict[str, Any]:
    """Scenario comparison and holding contributions linked to a stress run."""
    with closing(_connect(db_path)) as con:
        runs = pd.read_sql_query(
            """SELECT scenario_run_id, scenario_name, scenario_type, portfolio_value_inr, impact_inr, impact_pct,
                      settings_json, alerts_json FROM scenario_runs WHERE analysis_id=? ORDER BY scenario_run_id""",
            con, params=(run.analysis_id,))
        contributions = pd.read_sql_query(
            """SELECT c.scenario_run_id, c.symbol, c.holding_value_inr, c.shock, c.impact_inr, c.portfolio_impact_pct
               FROM scenario_contributions c JOIN scenario_runs r USING(scenario_run_id)
               WHERE r.analysis_id=? ORDER BY c.scenario_run_id, c.symbol""", con, params=(run.analysis_id,))
    settings = runs.pop("settings_json").map(json.loads)
    alerts = runs.pop("alerts_json").map(json.loads)
    runs["scenario_id"] = settings.map(lambda s: s.get("scenario_id"))
    runs["category"] = settings.map(lambda s: s.get("category"))
    runs["benchmark_move"] = settings.map(lambda s: s.get("benchmark_move"))
    runs["worst_holding"] = settings.map(lambda s: (s.get("worst_holdings") or [None])[0])
    refs = alerts.map(lambda a: a.get("risk_integration", {}))
    runs["loss_to_historical_var_99"] = refs.map(
        lambda r: r.get("var_es_references", {}).get("historical:c99", {}).get("loss_to_var"))
    runs["loss_to_historical_es_99"] = refs.map(
        lambda r: r.get("var_es_references", {}).get("historical:c99", {}).get("loss_to_es"))
    runs["loss_to_max_drawdown"] = refs.map(lambda r: r.get("loss_to_max_drawdown"))
    basis = {row.scenario_run_id: s.get("shock_basis", {}) for row, s in zip(runs.itertuples(), settings)}
    contributions["shock_basis"] = [basis.get(r, {}).get(s) for r, s in
                                    zip(contributions["scenario_run_id"], contributions["symbol"])]
    names = runs.set_index("scenario_run_id")["scenario_id"]
    contributions["scenario_id"] = contributions["scenario_run_id"].map(names)
    sectors = {row.scenario_id: s.get("sector_impact", {}) for row, s in zip(runs.itertuples(), settings)}
    return {"comparison": runs, "contributions": contributions, "sector_impact": sectors,
            "unavailable": run.quality.get("unavailable_scenarios", {}), "events": run.quality.get("events", []),
            "warnings": run.quality.get("warnings", [])}


def optimization_view(run: StoredRun, *, db_path: Path) -> dict[str, Any] | None:
    """Strategy comparison, weights, frontier and walk-forward series."""
    with closing(_connect(db_path)) as con:
        if not _has_table(con, "optimization_runs"):
            return None
        runs = pd.read_sql_query(
            """SELECT optimization_run_id, strategy, status, diagnostics_json, metrics_json FROM optimization_runs
               WHERE analysis_id=? ORDER BY optimization_run_id""", con, params=(run.analysis_id,))
        weights = pd.read_sql_query(
            """SELECT r.strategy, w.symbol, w.sector, w.target_weight, w.current_weight, w.integer_shares,
                      w.allocated_value_inr, w.allocated_weight FROM optimization_weights w
               JOIN optimization_runs r USING(optimization_run_id) WHERE r.analysis_id=?
               ORDER BY r.optimization_run_id, w.symbol""", con, params=(run.analysis_id,))
        backtests = pd.read_sql_query(
            """SELECT r.strategy, b.observation_date, b.net_return, b.gross_return, b.turnover, b.cost_fraction
               FROM optimization_backtests b JOIN optimization_runs r USING(optimization_run_id)
               WHERE r.analysis_id=? ORDER BY b.observation_date""", con, params=(run.analysis_id,))
    metrics = pd.DataFrame([json.loads(m) for m in runs["metrics_json"]])
    metrics.insert(0, "strategy", runs["strategy"].to_numpy())
    metrics["status"] = runs["status"].to_numpy()
    metrics["message"] = [json.loads(d).get("message") for d in runs["diagnostics_json"]]
    frontier = []
    for item in run.metrics.itertuples():
        parts = _split(item.metric_name)
        if parts[0] in ("opt_frontier_return", "opt_frontier_volatility"):
            frontier.append({"point": int(parts[1]), "field": parts[0].split("_")[-1], "value": item.value})
    frontier_df = (pd.DataFrame(frontier).pivot_table(index="point", columns="field", values="value").reset_index()
                   if frontier else pd.DataFrame())
    net = (backtests.pivot_table(index="observation_date", columns="strategy", values="net_return")
           if not backtests.empty else pd.DataFrame())
    if not net.empty:
        net.index = pd.to_datetime(net.index)
    return {"comparison": metrics, "weights": weights, "frontier": frontier_df, "walk_forward_net": net,
            "walk_forward": run.quality.get("walk_forward", {}), "tradeoffs": run.quality.get("tradeoffs", []),
            "statuses": run.quality.get("statuses", {})}
