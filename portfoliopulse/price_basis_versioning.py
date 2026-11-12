"""Corporate-action evidence and adjusted dataset *versions* (base data untouched).

1. ``classify_events`` compares each imported corporate action with the base
   dataset's observed closes around its ex-date (read-only). For a split or
   bonus with price factor f = ratio_old / ratio_new, let r be the stock's
   close-to-close return from the last observed date before the ex-date to the
   first observed date on/after it, and b the benchmark's return over the same
   dates. Unadjusted data should show r ~ f (1 + b) - 1; adjusted data r ~ b.
   - ``already_adjusted``: |r - b| <= tol and |r - (f(1+b) - 1)| > tol
   - ``unadjusted``:       |r - (f(1+b) - 1)| <= tol
   - ``ambiguous``:        anything else (manual review)
   Demergers are ``requires_value_source`` (their factor needs a sourced value),
   dividends ``price_return_not_adjusted`` (information only), and rights /
   mergers / other ``manual_review``.

2. ``build_adjusted_version`` creates a NEW candle dataset from the base one:
   for each ``adjust`` decision, OHLC before the ex-date is multiplied by the
   reference factor and volume divided by it; ``already_adjusted`` events leave
   prices unchanged (the version then only carries the corrected basis label).
   It refuses unless every split/bonus in the window has a decision, every
   factor equals the reference ratio, and non-split events are left unadjusted
   under the ``split_adjusted_ohlcv`` label. The base dataset, its candles and
   its analysis runs are never written. Lineage is recorded as a
   ``corporate_action_review`` provenance event on the new dataset.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, import_candle_dataset, session,
)

SPLIT_TYPES = ("split", "bonus")
DEFAULT_TOLERANCE = 0.10
VERSION_METADATA_KEYS = ("source_name", "source_url", "provenance_kind", "provenance_status", "retrieved_at_utc",
                         "interval", "start", "end_exclusive", "input_csv_sha256", "instruments", "portfolio_model")


@dataclass(frozen=True)
class EventDecision:
    symbol: str
    ex_date: str
    action_type: str
    decision: str  # already_adjusted | adjust | leave_unadjusted
    price_factor: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)


def _price_factor(row: Mapping[str, Any]) -> float | None:
    if row["action_type"] not in SPLIT_TYPES:
        return None
    new, old = float(row["ratio_new"]), float(row["ratio_old"])
    if not (math.isfinite(new) and math.isfinite(old) and new > 0 and old > 0):
        raise ValueError(f"{row['symbol']} {row['ex_date']}: invalid split/bonus ratio.")
    return old / new


def classify_events(
    close_grid: pd.DataFrame, benchmark: str, actions: pd.DataFrame, *, tolerance: float = DEFAULT_TOLERANCE,
) -> pd.DataFrame:
    """One evidence row per corporate action (pure; no data is changed)."""
    rows = []
    for action in actions.to_dict(orient="records"):
        symbol, ex = action["symbol"], pd.Timestamp(action["ex_date"])
        base = {"symbol": symbol, "ex_date": action["ex_date"], "action_type": action["action_type"],
                "price_factor": _price_factor(action), "cash_amount_inr": action.get("cash_amount_inr")}
        if symbol not in close_grid:
            rows.append({**base, "classification": "symbol_not_in_dataset"})
            continue
        series = close_grid[symbol].dropna()
        before, after = series[series.index < ex], series[series.index >= ex]
        if before.empty or after.empty:
            rows.append({**base, "classification": "outside_dataset_window"})
            continue
        d0, d1 = before.index[-1], after.index[0]
        r = float(after.iloc[0] / before.iloc[-1] - 1)
        bench = close_grid[benchmark]
        b = (float(bench[d1] / bench[d0] - 1) if pd.notna(bench.get(d0)) and pd.notna(bench.get(d1)) else np.nan)
        evidence = {"previous_date": f"{d0:%Y-%m-%d}", "first_date_on_or_after": f"{d1:%Y-%m-%d}",
                    "stock_return": r, "benchmark_return": b,
                    "excess_return": r - b if np.isfinite(b) else np.nan}
        kind = action["action_type"]
        if kind in SPLIT_TYPES:
            f = base["price_factor"]
            if not np.isfinite(b):
                label = "ambiguous"
            else:
                unadjusted = f * (1 + b) - 1
                evidence["expected_if_unadjusted"] = unadjusted
                if abs(r - unadjusted) <= tolerance:
                    label = "unadjusted"
                elif abs(r - b) <= tolerance:
                    label = "already_adjusted"
                else:
                    label = "ambiguous"
        elif kind == "demerger":
            label = "requires_value_source"
        elif kind == "dividend":
            label = "price_return_not_adjusted"
            cash = action.get("cash_amount_inr")
            if cash is not None and pd.notna(cash):
                evidence["dividend_yield_on_previous_close"] = float(cash) / float(before.iloc[-1])
        else:
            label = "manual_review"
        rows.append({**base, **evidence, "classification": label})
    return pd.DataFrame(rows)


def apply_adjustments(candles: pd.DataFrame, decisions: Sequence[EventDecision]) -> pd.DataFrame:
    """Back-adjust OHLC (x factor) and volume (/ factor) before each 'adjust' ex-date."""
    out = candles.copy()
    dates = pd.to_datetime(out["date"])
    for decision in decisions:
        if decision.decision != "adjust":
            continue
        f = float(decision.price_factor)
        if not (math.isfinite(f) and 0 < f):
            raise ValueError("Adjustment factors must be positive and finite.")
        mask = (out["symbol"] == decision.symbol) & (dates < pd.Timestamp(decision.ex_date))
        out.loc[mask, ["open", "high", "low", "close"]] = out.loc[mask, ["open", "high", "low", "close"]] * f
        out.loc[mask, "volume"] = out.loc[mask, "volume"] / f
    return out


def validate_version(base: pd.DataFrame, version: pd.DataFrame, decisions: Sequence[EventDecision]) -> dict[str, Any]:
    """Same rows; untouched rows identical; adjusted rows scaled exactly by their factor."""
    key = ["symbol", "date"]
    left = base.set_index(key).sort_index()
    right = version.set_index(key).sort_index()
    if not left.index.equals(right.index):
        raise ValueError("The version must keep exactly the base dataset's symbol/date rows.")
    expected = np.ones(len(left))
    dates = pd.to_datetime(left.index.get_level_values("date"))
    symbols = left.index.get_level_values("symbol")
    for decision in decisions:
        if decision.decision == "adjust":
            expected[(symbols == decision.symbol) & (dates < pd.Timestamp(decision.ex_date))] *= decision.price_factor
    ratio = right["close"].to_numpy() / left["close"].to_numpy()
    worst = float(np.max(np.abs(ratio - expected)))
    if worst > 1e-12:
        raise ValueError(f"Adjusted closes deviate from the declared factors (max {worst:.3e}).")
    for column in ("open", "high", "low"):
        if np.max(np.abs(right[column].to_numpy() / left[column].to_numpy() - expected)) > 1e-12:
            raise ValueError(f"Adjusted {column} prices deviate from the declared factors.")
    return {"rows": int(len(left)), "rows_scaled": int((expected != 1).sum()), "max_factor_error": worst}


def build_adjusted_version(
    base_dataset_id: int,
    reference_dataset_id: int,
    decisions: Sequence[EventDecision],
    *,
    new_price_basis: str = "split_adjusted_ohlcv",
    db_path: Path = DB_PATH,
) -> int:
    """Create (or return, if identical) a new dataset version; base data is never written."""
    if new_price_basis != "split_adjusted_ohlcv":
        raise ValueError("Only split_adjusted_ohlcv versions are supported; a demerger-adjusted basis needs a new "
                         "schema migration first.")
    info = get_dataset_info(base_dataset_id, path=db_path, read_only=True)
    base = get_candle_dataset(base_dataset_id, path=db_path, read_only=True)
    with session(db_path, read_only=True) as con:
        kind = con.execute("SELECT kind FROM reference_datasets WHERE reference_dataset_id=?",
                           (reference_dataset_id,)).fetchone()
        if kind is None or kind[0] != "corporate_actions":
            raise ValueError("The reference dataset is not an imported corporate-action file.")
        actions = pd.read_sql_query(
            "SELECT symbol, ex_date, action_type, ratio_new, ratio_old FROM corporate_actions WHERE reference_dataset_id=?",
            con, params=(reference_dataset_id,))
        holdings = [tuple(r) for r in con.execute(
            """SELECT i.symbol, COALESCE(h.quantity, 0) FROM instruments i JOIN holdings h USING(instrument_id)
               JOIN portfolios p USING(portfolio_id) WHERE p.name=? ORDER BY i.symbol""", (info["portfolio_name"],))]
    snapshot = sorted((r["symbol"], float(r["quantity"])) for r in info["portfolio_snapshot"] if not r["is_benchmark"])
    if sorted((s, float(q)) for s, q in holdings) != snapshot:
        raise ValueError("Current configured holdings differ from the base dataset's snapshot; refusing to version.")
    window = actions[(actions["ex_date"] > info["window_start"]) & (actions["ex_date"] < info["window_end_exclusive"])]
    by_key = {(d.symbol, d.ex_date, d.action_type): d for d in decisions}
    for action in window.to_dict(orient="records"):
        key = (action["symbol"], action["ex_date"], action["action_type"])
        decision = by_key.get(key)
        if action["action_type"] in SPLIT_TYPES:
            if decision is None or decision.decision not in ("already_adjusted", "adjust"):
                raise ValueError(f"Split/bonus {key} needs an 'already_adjusted' or 'adjust' decision.")
            if decision.decision == "adjust" and not math.isclose(decision.price_factor, _price_factor(action),
                                                                  rel_tol=0, abs_tol=1e-15):
                raise ValueError(f"{key}: factor must equal ratio_old / ratio_new from the reference file.")
        elif decision is not None and decision.decision != "leave_unadjusted":
            raise ValueError(f"{key}: only splits/bonuses can be adjusted under {new_price_basis}.")
    unknown = set(by_key) - {(a["symbol"], a["ex_date"], a["action_type"]) for a in window.to_dict(orient="records")}
    if unknown:
        raise ValueError(f"Decisions without a matching reference action: {sorted(unknown)}.")
    version = apply_adjustments(base, decisions)
    validation = validate_version(base, version, decisions)
    version = version.assign(price_basis=new_price_basis, date=version["date"].dt.strftime("%Y-%m-%d"))
    metadata = {k: v for k, v in info["metadata"].items() if k in VERSION_METADATA_KEYS}
    metadata.update(price_basis=new_price_basis, start=info["window_start"], end_exclusive=info["window_end_exclusive"])
    new_id = import_candle_dataset(version.drop(columns=["token", "timestamp_ist"]), metadata=metadata,
                                   portfolio_name=info["portfolio_name"], path=db_path)
    if new_id == base_dataset_id:
        raise ValueError("The version resolved to the base dataset; nothing was created.")
    evidence = {
        "derived_from_dataset_id": base_dataset_id, "reference_dataset_id": reference_dataset_id,
        "new_price_basis": new_price_basis, "base_price_basis": info["price_basis"],
        "decisions": [d.__dict__ for d in decisions], "validation": validation,
    }
    payload = json.dumps(evidence, sort_keys=True, allow_nan=False, default=str)
    with session(db_path) as con:
        exists = con.execute(
            """SELECT 1 FROM dataset_provenance_events WHERE dataset_id=? AND event_type='corporate_action_review'
               AND evidence_json=?""", (new_id, payload)).fetchone()
        if exists is None:
            con.execute(
                """INSERT INTO dataset_provenance_events(dataset_id,event_type,field_name,old_value,new_value,
                       reference_dataset_id,evidence_json) VALUES(?, 'corporate_action_review', 'price_basis', ?, ?, ?, ?)""",
                (new_id, info["price_basis"], new_price_basis, reference_dataset_id, payload))
    return new_id
