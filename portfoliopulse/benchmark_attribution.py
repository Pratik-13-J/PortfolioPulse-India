"""Benchmark-relative attribution framework (Phase 6), gated on real data.

The formulas are implemented and tested on hand-calculable fixtures. Any use
against stored benchmark data must pass :func:`attribution_gate`, which
refuses constituent files without valid weights (the free NSE Indices list
has names and industries only). Weights are never estimated or filled here;
an ``estimated`` file supplied by the user is labelled as such.

Formulas (sector s, period t; weights each sum to 1):

* Active Share = 1/2 * sum_i |w_p,i - w_b,i| over the union of names
* Brinson-Fachler:
    allocation_s  = (w_p,s - w_b,s) * (R_b,s - R_b)
    selection_s   = w_b,s * (R_p,s - R_b,s)
    interaction_s = (w_p,s - w_b,s) * (R_p,s - R_b,s)
  and sum_s (allocation + selection + interaction) = R_p - R_b.
  Conventions: a sector the portfolio does not hold uses R_p,s := R_b,s
  (no selection or interaction); an off-benchmark sector uses R_b,s := R_b
  (no allocation; its whole effect is interaction).
* Carino linking: k_t = [ln(1+R_p,t) - ln(1+R_b,t)] / (R_p,t - R_b,t)
  (or 1/(1+R_p,t) when equal), K the same on cumulative returns; linked
  effect = sum_t (k_t / K) * effect_t, which sums to the cumulative active
  return prod(1+R_p,t) - prod(1+R_b,t).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH
from portfoliopulse.database import session

WEIGHT_TOLERANCE = 1e-4
EFFECTS = ("allocation", "selection", "interaction")


class AttributionGateError(ValueError):
    """Valid benchmark constituent data is not available; attribution is refused."""


def _weights(weights: Mapping[str, float] | pd.Series, label: str) -> pd.Series:
    series = pd.Series(weights, dtype=float)
    if series.empty or series.index.has_duplicates:
        raise ValueError(f"{label} weights must be a non-empty mapping with unique names.")
    if not np.isfinite(series.to_numpy()).all() or (series < 0).any():
        raise ValueError(f"{label} weights must be finite and long-only.")
    if abs(float(series.sum()) - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"{label} weights must sum to 1 ± {WEIGHT_TOLERANCE}.")
    return series


def active_share(portfolio: Mapping[str, float] | pd.Series, benchmark: Mapping[str, float] | pd.Series) -> float:
    p = _weights(portfolio, "Portfolio")
    b = _weights(benchmark, "Benchmark")
    names = p.index.union(b.index)
    return float(0.5 * (p.reindex(names, fill_value=0.0) - b.reindex(names, fill_value=0.0)).abs().sum())


def sector_aggregate(weights: pd.Series, returns: pd.Series, sectors: Mapping[str, str]) -> pd.DataFrame:
    """Security weights/returns -> sector weight and weighted sector return."""
    w = _weights(weights, "Security")
    r = pd.Series(returns, dtype=float)
    missing = set(w.index) - set(r.index)
    if missing:
        raise ValueError(f"Missing returns for: {', '.join(sorted(missing))}.")
    unmapped = set(w.index) - set(sectors)
    if unmapped:
        raise ValueError(f"Missing sector classification for: {', '.join(sorted(unmapped))}.")
    frame = pd.DataFrame({"weight": w, "return": r.reindex(w.index), "sector": [sectors[s] for s in w.index]})
    if not np.isfinite(frame["return"].to_numpy()).all():
        raise ValueError("Security returns must be finite.")
    grouped = frame.assign(product=frame["weight"] * frame["return"]).groupby("sector")[["weight", "product"]].sum()
    grouped["return"] = np.where(grouped["weight"] > 0, grouped["product"] / grouped["weight"].where(grouped["weight"] > 0), np.nan)
    return grouped[["weight", "return"]]


@dataclass(frozen=True)
class BrinsonResult:
    effects: pd.DataFrame        # index sector, columns allocation/selection/interaction/total
    portfolio_return: float
    benchmark_return: float

    @property
    def active_return(self) -> float:
        return self.portfolio_return - self.benchmark_return


def brinson_fachler(portfolio: pd.DataFrame, benchmark: pd.DataFrame) -> BrinsonResult:
    """Single-period sector attribution.

    ``portfolio`` and ``benchmark`` are indexed by sector with columns
    ``weight`` and ``return`` (as from :func:`sector_aggregate`). A sector
    absent from one side is treated with the conventions in the module doc.
    """
    for label, frame in (("Portfolio", portfolio), ("Benchmark", benchmark)):
        if not {"weight", "return"} <= set(frame.columns):
            raise ValueError(f"{label} needs 'weight' and 'return' columns.")
        _weights(frame["weight"], label)
    sectors = portfolio.index.union(benchmark.index)
    wp = portfolio["weight"].reindex(sectors, fill_value=0.0).astype(float)
    wb = benchmark["weight"].reindex(sectors, fill_value=0.0).astype(float)
    rb_total = float((benchmark["weight"] * benchmark["return"]).sum())
    rp_total = float((portfolio["weight"] * portfolio["return"].where(portfolio["weight"] > 0, 0.0)).sum())
    rb = benchmark["return"].reindex(sectors).astype(float)
    rb = rb.where(wb > 0, rb_total)  # off-benchmark sector
    rp = portfolio["return"].reindex(sectors).astype(float)
    rp = rp.where(wp > 0, rb)        # sector not held
    if not (np.isfinite(rp.to_numpy()).all() and np.isfinite(rb.to_numpy()).all()):
        raise ValueError("Every held sector needs a finite return.")
    effects = pd.DataFrame({
        "allocation": (wp - wb) * (rb - rb_total),
        "selection": wb * (rp - rb),
        "interaction": (wp - wb) * (rp - rb),
    }, index=sectors)
    effects["total"] = effects[list(EFFECTS)].sum(axis=1)
    gap = abs(float(effects["total"].sum()) - (rp_total - rb_total))
    if gap > 1e-12:
        raise ValueError("Brinson-Fachler effects do not reconcile to the active return.")
    return BrinsonResult(effects, rp_total, rb_total)


def _carino_k(rp: float, rb: float) -> float:
    if rp <= -1 or rb <= -1:
        raise ValueError("Carino linking needs returns above -100%.")
    if math.isclose(rp, rb, rel_tol=0.0, abs_tol=1e-15):
        return 1.0 / (1.0 + rp)
    return (math.log1p(rp) - math.log1p(rb)) / (rp - rb)


@dataclass(frozen=True)
class LinkedAttribution:
    effects: pd.DataFrame        # linked effects by sector
    cumulative_portfolio_return: float
    cumulative_benchmark_return: float
    reconciliation_error: float


def carino_link(periods: Sequence[BrinsonResult]) -> LinkedAttribution:
    """Link single-period effects so they sum to cumulative active return."""
    if not periods:
        raise ValueError("Provide at least one attribution period.")
    rp_cum = float(np.prod([1 + item.portfolio_return for item in periods]) - 1)
    rb_cum = float(np.prod([1 + item.benchmark_return for item in periods]) - 1)
    big_k = _carino_k(rp_cum, rb_cum)
    linked: pd.DataFrame | None = None
    for item in periods:
        scaled = item.effects[list(EFFECTS)] * (_carino_k(item.portfolio_return, item.benchmark_return) / big_k)
        linked = scaled if linked is None else linked.add(scaled, fill_value=0.0)
    assert linked is not None
    linked["total"] = linked[list(EFFECTS)].sum(axis=1)
    error = abs(float(linked["total"].sum()) - (rp_cum - rb_cum))
    if error > 1e-12:
        raise ValueError("Carino-linked effects do not reconcile to the cumulative active return.")
    return LinkedAttribution(linked, rp_cum, rb_cum, error)


@dataclass(frozen=True)
class GateStatus:
    enabled: bool
    reasons: tuple[str, ...]
    weight_basis: str | None
    label: str | None
    as_of_date: str | None


def attribution_gate(
    reference_dataset_id: int | None, *, required_symbols: Sequence[str] = (), db_path: Path = DB_PATH,
) -> GateStatus:
    """Decide whether stored constituent data can support attribution.

    Enabled only when the constituent file has ``official`` or ``estimated``
    weights for every row, the weights sum to 1 ± 1e-4, and each benchmark
    name has an NSE-industry classification effective on the as-of date.
    """
    if reference_dataset_id is None:
        return GateStatus(False, ("No benchmark constituent dataset has been imported.",), None, None, None)
    reasons: list[str] = []
    with session(db_path) as con:
        reference = con.execute(
            "SELECT kind FROM reference_datasets WHERE reference_dataset_id=?", (reference_dataset_id,),
        ).fetchone()
        if reference is None or reference["kind"] != "index_constituents":
            return GateStatus(False, ("The reference dataset is not a constituent file.",), None, None, None)
        rows = con.execute(
            "SELECT symbol, weight, weight_basis, as_of_date FROM benchmark_constituents WHERE reference_dataset_id=?",
            (reference_dataset_id,),
        ).fetchall()
        if not rows:
            return GateStatus(False, ("The constituent dataset has no rows.",), None, None, None)
        as_of = str(rows[0]["as_of_date"])
        classified = {row[0] for row in con.execute(
            "SELECT DISTINCT symbol FROM security_classifications WHERE taxonomy='nse_industry' AND effective_date<=?",
            (as_of,),
        )}
    bases = {row["weight_basis"] for row in rows}
    basis = bases.pop() if len(bases) == 1 else None
    if basis is None:
        reasons.append("Constituent rows disagree on weight basis.")
    elif basis == "none":
        reasons.append("Constituent weights are unavailable (weight_basis 'none'); names and industries only.")
    weights = [row["weight"] for row in rows]
    if any(weight is None for weight in weights):
        reasons.append("At least one constituent weight is missing; weights are never filled.")
    elif abs(math.fsum(weights) - 1.0) > WEIGHT_TOLERANCE:
        reasons.append("Constituent weights do not sum to 1 ± 1e-4.")
    symbols = {row["symbol"] for row in rows}
    unclassified = sorted((symbols | set(required_symbols)) - classified)
    if unclassified:
        reasons.append(f"Missing NSE-industry classification for: {', '.join(unclassified[:10])}"
                       + (" …" if len(unclassified) > 10 else "") + ".")
    label = None if basis not in ("official", "estimated") else basis
    return GateStatus(not reasons, tuple(reasons), basis, label, as_of)


def gated_benchmark_weights(reference_dataset_id: int | None, *, db_path: Path = DB_PATH) -> tuple[pd.Series, GateStatus]:
    """Return stored benchmark weights only if the gate passes."""
    status = attribution_gate(reference_dataset_id, db_path=db_path)
    if not status.enabled:
        raise AttributionGateError("; ".join(status.reasons))
    with session(db_path) as con:
        rows = con.execute(
            "SELECT symbol, weight FROM benchmark_constituents WHERE reference_dataset_id=? ORDER BY symbol",
            (reference_dataset_id,),
        ).fetchall()
    return pd.Series({row["symbol"]: float(row["weight"]) for row in rows}), status
