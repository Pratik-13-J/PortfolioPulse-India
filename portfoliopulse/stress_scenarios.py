"""Stress shock vectors that keep direct and propagated components apart.

A ``ShockVector`` holds, per holding, the decimal one-period price shock split
into components, so outputs can always say *why* a holding moved:

* ``direct`` - a shock the scenario states for that holding or its sector
  (built with the existing ``scenarios.scenario_shocks`` for direct presets);
* ``beta_propagated`` - beta_i * market_move, with beta_i an in-sample OLS
  estimate from the selected dataset (``factor_sensitivities.market_betas``);
* ``covariance_propagated`` - the conditional mean of unshocked holdings given
  the directly shocked ones: E[r_U | r_S = s] = Sigma_US Sigma_SS^-1 s
  (zero-mean, from ``covariance_estimators``);
* ``factor`` - rate / USD/INR sensitivities times assumed factor moves
  (``assumed`` from configuration, or ``estimated`` by OLS from a sourced file);
* ``historical_observed`` - a return observed over an event window in the
  dataset's own closes.

Components add (shock_i = sum of components). A combined shock below -100%
is rejected. All scenarios are hypothetical revaluations of current holdings.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.scenarios import scenario_shocks

COMPONENTS = ("direct", "beta_propagated", "covariance_propagated", "factor", "historical_observed")
UNCLASSIFIED = "Unclassified"
HYPOTHETICAL_KINDS = ("beta_market", "direct", "covariance_individual", "combined", "macro")
CRLF, LF = bytes([13, 10]), bytes([10])  # line-ending normalisation for content hashes
DEFAULT_LIBRARY_PATH = Path(__file__).resolve().parents[1] / "config" / "stress_scenarios.json"


@dataclass(frozen=True)
class ShockVector:
    scenario_id: str
    name: str
    scenario_type: str
    category: str
    components: pd.DataFrame
    benchmark_move: float | None = None
    assumptions: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def shocks(self) -> pd.Series:
        return self.components.sum(axis=1).rename("shock")

    @property
    def basis(self) -> pd.Series:
        labels = {
            symbol: "+".join(name for name in COMPONENTS if row[name] != 0) or "unshocked"
            for symbol, row in self.components.iterrows()
        }
        return pd.Series(labels, name="shock_basis")


def make_vector(
    scenario_id: str, name: str, scenario_type: str, category: str,
    components: Mapping[str, Mapping[str, float]] | pd.DataFrame, symbols: Sequence[str], **extra: Any,
) -> ShockVector:
    frame = pd.DataFrame(components).reindex(index=list(symbols), columns=list(COMPONENTS)).fillna(0.0)
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{scenario_id}: shock components must be finite.")
    total = values.sum(axis=1)
    if (total < -1).any():
        bad = [s for s, t in zip(symbols, total) if t < -1]
        raise ValueError(f"{scenario_id}: combined shock below -100% for {', '.join(bad)}.")
    return ShockVector(scenario_id, name, scenario_type, category, frame.astype(float), **extra)


def normalize_holdings(snapshot: Sequence[Mapping[str, Any]]) -> tuple[pd.DataFrame, list[str]]:
    """Holdings (non-benchmark rows) with blank sectors mapped to Unclassified."""
    rows, warnings = [], []
    for row in snapshot:
        if row.get("is_benchmark"):
            continue
        sector = row.get("sector")
        if sector is None or (isinstance(sector, float) and math.isnan(sector)) or not str(sector).strip():
            warnings.append(f"{row['symbol']} has no sector classification; treated as {UNCLASSIFIED}.")
            sector = UNCLASSIFIED
        rows.append({"symbol": str(row["symbol"]), "sector": str(sector), "quantity": float(row["quantity"])})
    if not rows:
        raise ValueError("The holding snapshot has no non-benchmark holdings.")
    frame = pd.DataFrame(rows).set_index("symbol")
    if frame.index.has_duplicates:
        raise ValueError("Holding symbols must be unique.")
    return frame, warnings


def _move(value: Any, label: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or number < -1:
        raise ValueError(f"{label} must be finite and no smaller than -100%.")
    return number


def direct_vector(spec: Mapping[str, Any], holdings: pd.DataFrame) -> ShockVector:
    """Direct presets via the existing ``scenarios.scenario_shocks`` rules."""
    rows = [{"symbol": s, "sector": r["sector"]} for s, r in holdings.iterrows()]
    kwargs = {key: spec[key] for key in ("broad_shock", "stressed_shock", "residual_shock",
                                         "individual_shocks", "sector_shocks") if key in spec}
    shocks = scenario_shocks(rows, spec["scenario_type"], **kwargs)
    return make_vector(spec["scenario_id"], spec["name"], f"direct_{spec['scenario_type']}", "hypothetical",
                       {"direct": shocks}, holdings.index,
                       assumptions={"rule": "scenarios.scenario_shocks", **kwargs})


def beta_vector(
    spec: Mapping[str, Any], holdings: pd.DataFrame, betas: pd.Series, *, scenario_type: str = "beta_market",
) -> ShockVector:
    """shock_i = beta_i * m, plus optional sector / individual direct overlays."""
    market = _move(spec["market_move"], "market_move")
    beta = betas.reindex(holdings.index)
    if beta.isna().any():
        raise ValueError(f"Missing beta for {', '.join(beta.index[beta.isna()])}.")
    direct = pd.Series(0.0, index=holdings.index)
    warnings: list[str] = []
    sectors = dict(spec.get("sector_shocks", {}))
    unknown = set(sectors) - set(holdings["sector"])
    if unknown:
        raise ValueError(f"Unknown sector shock targets: {', '.join(sorted(unknown))}.")
    for sector, shock in sectors.items():
        direct[holdings["sector"] == sector] += _move(shock, f"shock for sector {sector}")
    if sectors and (holdings["sector"] == UNCLASSIFIED).any():
        warnings.append("Unclassified holdings receive no sector overlay.")
    for symbol, shock in dict(spec.get("individual_shocks", {})).items():
        if symbol not in direct.index:
            raise ValueError(f"Unknown holding shock target: {symbol}.")
        direct[symbol] += _move(shock, f"shock for {symbol}")
    return make_vector(
        spec["scenario_id"], spec["name"], scenario_type, "hypothetical",
        {"beta_propagated": beta * market, "direct": direct}, holdings.index,
        benchmark_move=market, warnings=tuple(warnings),
        assumptions={"market_move": market, "sector_overlays": sectors,
                     "individual_overlays": dict(spec.get("individual_shocks", {})),
                     "propagation": "beta_i * market_move; in-sample OLS beta; overlays add"},
    )


def conditional_moves(covariance: pd.DataFrame, conditioning: Mapping[str, float]) -> pd.Series:
    """E[r_U | r_S = s] = Sigma_US Sigma_SS^-1 s for every unconditioned column."""
    names = list(conditioning)
    if not names or set(names) - set(covariance.columns):
        raise ValueError("Conditioning names must be non-empty columns of the covariance matrix.")
    others = [c for c in covariance.columns if c not in names]
    sigma_ss = covariance.loc[names, names].to_numpy(dtype=float)
    if np.linalg.matrix_rank(sigma_ss) < len(names):
        raise ValueError("The conditioning covariance block is singular.")
    s = np.array([float(conditioning[n]) for n in names])
    moves = covariance.loc[others, names].to_numpy(dtype=float) @ np.linalg.solve(sigma_ss, s)
    return pd.Series(moves, index=others)


def covariance_vector(
    spec: Mapping[str, Any], holdings: pd.DataFrame, covariance: pd.DataFrame, *,
    benchmark: str | None = None, estimator: str = "sample",
) -> ShockVector:
    """Direct shocks on named holdings; covariance-propagated moves elsewhere."""
    direct = {s: _move(v, f"shock for {s}") for s, v in dict(spec["individual_shocks"]).items()}
    if set(direct) - set(holdings.index):
        raise ValueError(f"Unknown holding shock targets: {', '.join(sorted(set(direct) - set(holdings.index)))}.")
    moves = conditional_moves(covariance, direct)
    propagated = moves.reindex(holdings.index).fillna(0.0)
    propagated[list(direct)] = 0.0
    implied = float(moves[benchmark]) if benchmark is not None and benchmark in moves.index else None
    return make_vector(
        spec["scenario_id"], spec["name"], "covariance_individual", "hypothetical",
        {"direct": pd.Series(direct), "covariance_propagated": propagated}, holdings.index,
        benchmark_move=implied,
        assumptions={"individual_shocks": direct, "covariance_estimator": estimator,
                     "propagation": "zero-mean conditional expectation Sigma_US Sigma_SS^-1 s"},
    )


def assumed_sensitivities(library: Mapping[str, Any], holdings: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Per-holding macro sensitivities from the configured sector assumptions."""
    config = library["assumed_macro_sensitivities"]
    by_sector, fallback = config["by_sector"], config["unclassified"]
    rows, warnings = {}, []
    for symbol, row in holdings.iterrows():
        values = by_sector.get(row["sector"])
        if values is None:
            warnings.append(f"No assumed macro sensitivity for {symbol} ({row['sector']}); using the unclassified "
                            "fallback.")
            values = fallback
        rows[symbol] = {k: float(v) for k, v in values.items()}
    return pd.DataFrame.from_dict(rows, orient="index"), warnings


def macro_vector(
    spec: Mapping[str, Any], holdings: pd.DataFrame, sensitivities: pd.DataFrame, *, basis: str,
) -> ShockVector:
    """factor component_i = sum_f sensitivity_i,f * move_f (market held at zero)."""
    if basis not in ("assumed", "estimated"):
        raise ValueError("basis must be 'assumed' or 'estimated'.")
    moves = {f: float(v) for f, v in dict(spec["factor_moves"]).items()}
    if set(moves) - set(sensitivities.columns):
        raise ValueError(f"Unknown macro factors: {', '.join(sorted(set(moves) - set(sensitivities.columns)))}.")
    sens = sensitivities.reindex(holdings.index)
    if sens[list(moves)].isna().any().any():
        raise ValueError("Every holding needs a sensitivity for each shocked factor.")
    factor = sum(sens[f] * m for f, m in moves.items())
    return make_vector(
        spec["scenario_id"], spec["name"], f"macro_{basis}", "hypothetical", {"factor": factor}, holdings.index,
        assumptions={"factor_moves": moves, "sensitivity_basis": basis,
                     "sensitivities": {s: {f: float(sens.at[s, f]) for f in moves} for s in sens.index},
                     "market_component": "held at zero (partial effect)"},
    )


def load_library(path: Path = DEFAULT_LIBRARY_PATH) -> tuple[dict[str, Any], str]:
    """Load and validate the versioned scenario library; return (library, LF-normalised sha256)."""
    payload = Path(path).read_bytes()
    library = json.loads(payload.decode("utf-8"))
    for key in ("library_version", "historical_events", "hypothetical_scenarios", "assumed_macro_sensitivities"):
        if key not in library:
            raise ValueError(f"Scenario library is missing {key}.")
    ids = [item["event_id"] for item in library["historical_events"]] + [
        item["scenario_id"] for item in library["hypothetical_scenarios"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Scenario and event IDs must be unique.")
    for event in library["historical_events"]:
        start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
        if not start < end:
            raise ValueError(f"{event['event_id']}: start must precede end.")
    for spec in library["hypothetical_scenarios"]:
        if spec.get("kind") not in HYPOTHETICAL_KINDS:
            raise ValueError(f"{spec.get('scenario_id')}: unknown kind {spec.get('kind')!r}.")
    # Hash content with line endings normalised to LF (Git's stored form), so a CRLF
    # checkout (core.autocrlf) does not change the recorded library identity.
    return library, hashlib.sha256(payload.replace(CRLF, LF)).hexdigest()
