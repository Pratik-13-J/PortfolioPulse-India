"""Roadmap Phase 4 stress-testing orchestration (engine ``phase4_stress_v1``).

Reuses, without re-implementing: the in-memory Phase 4 buy-and-hold analysis
(valuation, returns, drawdown), the Phase 3 risk engine (VaR/ES table),
``attribution.scenario_attribution`` (INR reconciliation), the existing
direct presets (``scenarios.scenario_shocks``), covariance estimators and
delta-normal components.

Persistence uses the existing schema only: one ``analysis_metadata`` run under
the new engine version (scenario-comparison scalars, betas, sensitivities),
plus one ``scenario_runs`` row and its ``scenario_contributions`` per
available scenario, linked to that run, with new ``scenario_type`` values.
Every scenario row is a hypothetical revaluation of the current fixed
quantities (``hypothetical = 1``), including historical replays.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH
from portfoliopulse.covariance_estimators import covariance_estimates
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, list_scenario_runs, save_analysis_run, save_dataset_scenario,
)
from portfoliopulse.factor_sensitivities import MACRO_FACTORS, load_macro_factor_csv, macro_sensitivities, market_betas
from portfoliopulse.historical_stress import EventOutcome, data_identified_events, replay_event
from portfoliopulse.portfolio_analysis import Phase4Analysis, compute_phase4_analysis
from portfoliopulse.risk_analysis import COMPARISON_LABEL, RiskAnalysis, compute_risk_analysis
from portfoliopulse.source_verification import parse_nse_index_csv
from portfoliopulse.stress_impact import StressImpact, risk_integration, stress_impact
from portfoliopulse.stress_scenarios import (
    DEFAULT_LIBRARY_PATH, ShockVector, assumed_sensitivities, beta_vector, covariance_vector, direct_vector,
    load_library, macro_vector, normalize_holdings,
)
from portfoliopulse.var_models import sample_mean_and_covariance

ENGINE_VERSION = "phase4_stress_v1"


@dataclass(frozen=True)
class StressAnalysis:
    dataset_id: int
    analysis_id: int | None
    scenario_run_ids: dict[str, int]
    source_name: str
    verification_status: str
    provenance_status: str
    price_basis: str
    benchmark_symbol: str
    as_of_date: str
    portfolio_value_inr: float
    library_version: str
    library_sha256: str
    holdings: pd.DataFrame
    betas: pd.DataFrame
    sensitivity_basis: str
    sensitivities: pd.DataFrame
    vectors: dict[str, ShockVector]
    impacts: dict[str, StressImpact]
    integration: dict[str, dict[str, Any]]
    events: list[EventOutcome]
    unavailable: dict[str, str]
    warnings: list[str]
    comparison: pd.DataFrame
    settings: dict[str, Any]
    quality: dict[str, Any]
    metrics: dict[str, tuple[float, str]]


def jsonable(value: Any) -> Any:
    """Recursively convert pandas/numpy scalars for strict JSON (NaN -> None)."""
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def _build_hypothetical(
    spec: Mapping[str, Any], holdings: pd.DataFrame, betas: pd.Series, covariance: pd.DataFrame,
    benchmark: str, sensitivities: pd.DataFrame, sensitivity_basis: str, estimator: str,
) -> ShockVector:
    kind = spec["kind"]
    if kind == "direct":
        return direct_vector(spec, holdings)
    if kind == "beta_market":
        return beta_vector(spec, holdings, betas)
    if kind == "combined":
        return beta_vector(spec, holdings, betas, scenario_type="combined_market_sector")
    if kind == "covariance_individual":
        return covariance_vector(spec, holdings, covariance, benchmark=benchmark, estimator=estimator)
    if kind == "macro":
        return macro_vector(spec, holdings, sensitivities, basis=sensitivity_basis)
    raise ValueError(f"Unknown scenario kind {kind!r}.")


def compute_stress_analysis(
    phase4: Phase4Analysis,
    risk: RiskAnalysis,
    snapshot: Sequence[Mapping[str, Any]],
    *,
    verification_status: str,
    library: Mapping[str, Any],
    library_sha256: str,
    benchmark_reference: pd.Series | None = None,
    reference_label: str | None = None,
    macro_factors: pd.DataFrame | None = None,
    macro_label: Mapping[str, Any] | None = None,
    covariance_estimator: str = "sample",
) -> StressAnalysis:
    """All scenarios, impacts and risk integration for one dataset (no DB I/O)."""
    returns = phase4.returns
    benchmark = phase4.benchmark_symbol
    holdings, warnings = normalize_holdings(snapshot)
    symbols = list(holdings.index)
    assets = returns.asset_daily_returns.loc[:, symbols]
    bench_daily = returns.daily_comparison_returns[benchmark]
    as_of = pd.Timestamp(phase4.risk_weight_date)
    values = returns.holding_values_inr.loc[as_of, symbols].astype(float)
    if values.isna().any() or not values.sum() > 0:
        raise ValueError("Every holding needs a positive value on the risk-weight date.")
    targets = {str(r["symbol"]): float(r["target_weight"]) for r in snapshot if not r.get("is_benchmark")}

    betas = market_betas(assets, bench_daily)
    if not risk.sample_adequate_for_tails:
        warnings.append(f"Betas, covariances and VaR references use only {len(bench_daily)} valid returns; "
                        "propagated and risk-integration outputs are smoke-test quality only.")
    joint = pd.concat([assets, bench_daily.rename(benchmark)], axis=1)
    estimates, _ = covariance_estimates(joint)
    if covariance_estimator not in estimates:
        raise ValueError(f"covariance_estimator must be one of {sorted(estimates)}.")
    joint_cov = estimates[covariance_estimator]
    if macro_factors is not None:
        long = macro_sensitivities(assets, bench_daily, macro_factors)
        sensitivities = long[long["factor"].isin(MACRO_FACTORS)].pivot(
            index="symbol", columns="factor", values="sensitivity").reindex(symbols)
        sensitivity_basis = "estimated"
        sensitivity_detail = long
    else:
        sensitivities, sens_warnings = assumed_sensitivities(library, holdings)
        warnings.extend(sens_warnings)
        sensitivity_basis = "assumed"
        sensitivity_detail = None

    vectors: dict[str, ShockVector] = {}
    unavailable: dict[str, str] = {}
    for spec in library["hypothetical_scenarios"]:
        try:
            vectors[spec["scenario_id"]] = _build_hypothetical(
                spec, holdings, betas["beta"], joint_cov, benchmark, sensitivities, sensitivity_basis,
                covariance_estimator)
        except ValueError as exc:
            unavailable[spec["scenario_id"]] = str(exc)

    events = list(library["historical_events"])
    identified = library.get("data_identified_events", {})
    for event in data_identified_events(returns.price_grid, bench_daily, benchmark):
        key = "worst_benchmark_drawdown" if "drawdown" in event["event_id"] else "worst_benchmark_day"
        if not identified.get(key, False):
            continue
        duplicate = next((e["event_id"] for e in events if (e["start"], e["end"]) == (event["start"], event["end"])),
                         None)
        if duplicate is not None:
            warnings.append(f"{event['event_id']} has the same window as {duplicate}; not repeated.")
            continue
        events.append(event)
    outcomes = []
    for event in events:
        outcome = replay_event(event, returns.price_grid, holdings, benchmark, betas["beta"],
                               benchmark_reference=benchmark_reference, reference_label=reference_label)
        outcomes.append(outcome)
        if outcome.vector is not None:
            vectors[outcome.event_id] = outcome.vector
        else:
            unavailable[outcome.event_id] = outcome.reason or "unavailable"

    mean, sample_cov = sample_mean_and_covariance(assets)
    max_dd = float(phase4.metrics["portfolio_max_drawdown"])
    weights = values / values.sum()
    portfolio_beta = float(weights @ betas["beta"])
    impacts, integration, rows = {}, {}, []
    for scenario_id, vector in vectors.items():
        impact = stress_impact(values, holdings["sector"], vector)
        impacts[scenario_id] = impact
        integration[scenario_id] = risk_integration(
            impact, risk.var_table, max_drawdown=max_dd, asset_returns=assets, mean=mean,
            covariance=sample_cov, target_weights=targets)
        hist99 = integration[scenario_id]["var_es_references"].get("historical:c99", {})
        worst = impact.worst_holdings[0]
        rows.append({
            "scenario_id": scenario_id, "name": vector.name, "category": vector.category,
            "scenario_type": vector.scenario_type,
            "benchmark_move": vector.benchmark_move,
            "beta_implied_portfolio_move": (None if vector.benchmark_move is None
                                            else portfolio_beta * vector.benchmark_move),
            "impact_inr": impact.impact_inr, "impact_pct": impact.impact_pct,
            "worst_holding": worst, "worst_holding_impact_inr": float(impact.holdings.at[worst, "impact_inr"]),
            "loss_to_historical_var_99": hist99.get("loss_to_var"),
            "loss_to_historical_es_99": hist99.get("loss_to_es"),
            "exceeds_historical_es_99": hist99.get("exceeds_es"),
            "loss_to_max_drawdown": integration[scenario_id]["loss_to_max_drawdown"],
            "shock_bases": "; ".join(sorted(set(vector.basis))),
        })
    comparison = pd.DataFrame(rows).set_index("scenario_id")

    metrics: dict[str, tuple[float, str]] = {
        "stress_portfolio_value_inr": (float(values.sum()), "INR"),
        "stress_current_weight_beta": (portfolio_beta, "unitless"),
        "stress_scenarios_available": (float(len(vectors)), "count"),
        "stress_scenarios_unavailable": (float(len(unavailable)), "count"),
    }
    for symbol, row in betas.iterrows():
        metrics[f"stress_beta:{symbol}"] = (float(row["beta"]), "unitless")
        metrics[f"stress_beta_standard_error:{symbol}"] = (float(row["beta_standard_error"]), "unitless")
        metrics[f"stress_beta_r_squared:{symbol}"] = (float(row["r_squared"]), "ratio")
    for symbol, row in sensitivities.iterrows():
        for factor, value in row.items():
            metrics[f"stress_macro_sensitivity:{sensitivity_basis}:{factor}:{symbol}"] = (float(value), "unitless")
    for scenario_id, impact in impacts.items():
        metrics[f"stress_impact_inr:{scenario_id}"] = (impact.impact_inr, "INR")
        metrics[f"stress_impact_pct:{scenario_id}"] = (impact.impact_pct, "ratio")
        move = vectors[scenario_id].benchmark_move
        if move is not None:
            metrics[f"stress_benchmark_move:{scenario_id}"] = (float(move), "ratio")
        for sector, row in impact.sectors.iterrows():
            metrics[f"stress_sector_impact_inr:{scenario_id}:{sector}"] = (float(row["impact_inr"]), "INR")
        for name in ("loss_to_historical_var_99", "loss_to_historical_es_99", "loss_to_max_drawdown"):
            value = comparison.at[scenario_id, name]
            if value is not None and np.isfinite(value):
                metrics[f"stress_{name}:{scenario_id}"] = (float(value), "ratio")
    metrics = {k: v for k, v in metrics.items() if np.isfinite(v[0])}

    settings = {
        "stage": "roadmap_phase4_stress_testing",
        "engine_version": ENGINE_VERSION,
        "comparison_label": COMPARISON_LABEL,
        "interpretation": "hypothetical one-period revaluations of current fixed quantities; not forecasts",
        "source_name": phase4.source_name, "verification_status": verification_status,
        "provenance_status": phase4.provenance_status, "price_basis": phase4.price_basis,
        "benchmark_symbol": benchmark, "as_of_date": phase4.risk_weight_date,
        "scenario_library_version": library["library_version"], "scenario_library_sha256": library_sha256,
        "beta_method": "single-factor OLS of daily asset returns on daily NIFTY 50 returns, valid dates only",
        "covariance_propagation_estimator": covariance_estimator,
        "macro_sensitivity_basis": sensitivity_basis,
        "macro_sensitivity_source": (jsonable(dict(macro_label)) if macro_label else
                                     library["assumed_macro_sensitivities"]["basis"]),
        "benchmark_reference": reference_label,
        "risk_engine": {"engine_version": risk.settings["engine_version"], "monte_carlo": risk.settings["monte_carlo"]},
        "pnl_method": "impact_i = V_i * shock_i via attribution.scenario_attribution; V_i at as_of_date",
        "shock_components": "direct | beta_propagated | covariance_propagated | factor | historical_observed",
    }
    quality = jsonable({
        "warnings": warnings,
        "unavailable_scenarios": unavailable,
        "events": [o.summary() for o in outcomes],
        "valid_return_observations": int(len(bench_daily)),
        "sector_labels": holdings["sector"].to_dict(),
        "macro_sensitivity_detail": (None if sensitivity_detail is None
                                     else sensitivity_detail.to_dict(orient="records")),
    })
    return StressAnalysis(
        dataset_id=phase4.dataset_id, analysis_id=None, scenario_run_ids={},
        source_name=phase4.source_name, verification_status=verification_status,
        provenance_status=phase4.provenance_status, price_basis=phase4.price_basis,
        benchmark_symbol=benchmark, as_of_date=phase4.risk_weight_date, portfolio_value_inr=float(values.sum()),
        library_version=library["library_version"], library_sha256=library_sha256, holdings=holdings,
        betas=betas, sensitivity_basis=sensitivity_basis, sensitivities=sensitivities, vectors=vectors,
        impacts=impacts, integration=integration, events=outcomes, unavailable=unavailable, warnings=warnings,
        comparison=comparison, settings=settings, quality=quality, metrics=metrics,
    )


def _scenario_settings(result: StressAnalysis, scenario_id: str) -> dict[str, Any]:
    vector = result.vectors[scenario_id]
    return jsonable({
        "engine_version": ENGINE_VERSION, "scenario_id": scenario_id, "category": vector.category,
        "interpretation": "hypothetical revaluation of current fixed quantities; not a forecast",
        "comparison_label": COMPARISON_LABEL, "source_name": result.source_name,
        "verification_status": result.verification_status, "price_basis": result.price_basis,
        "benchmark_move": vector.benchmark_move, "assumptions": vector.assumptions,
        "shock_basis": vector.basis.to_dict(),
        "shock_components": {s: row.to_dict() for s, row in vector.components.iterrows()},
        "sector_impact": result.impacts[scenario_id].sectors.to_dict(orient="index"),
        "worst_holdings": result.impacts[scenario_id].worst_holdings,
        "warnings": list(vector.warnings),
        "scenario_library_sha256": result.library_sha256,
    })


def analyze_saved_dataset_stress(
    dataset_id: int,
    *,
    db_path: Path = DB_PATH,
    library_path: Path = DEFAULT_LIBRARY_PATH,
    nse_reference_csv: Path | None = None,
    macro_factor_csv: Path | None = None,
    covariance_estimator: str = "sample",
    save: bool = True,
    **risk_options: Any,
) -> StressAnalysis:
    """Read one dataset, run every scenario, and optionally persist the results."""
    info = get_dataset_info(dataset_id, path=db_path)
    candles = get_candle_dataset(dataset_id, path=db_path)
    phase4 = compute_phase4_analysis(dataset_id, info, candles)
    status = str(info["verification_status"])
    risk = compute_risk_analysis(phase4, verification_status=status, **risk_options)
    library, sha = load_library(library_path)
    reference = label = None
    if nse_reference_csv is not None:
        reference = parse_nse_index_csv(nse_reference_csv)
        digest = hashlib.sha256(Path(nse_reference_csv).read_bytes()).hexdigest()
        label = f"NSE Indices NIFTY 50 CSV {Path(nse_reference_csv).name} sha256={digest}"
    factors = factor_label = None
    if macro_factor_csv is not None:
        factors, factor_label = load_macro_factor_csv(macro_factor_csv)
    result = compute_stress_analysis(
        phase4, risk, info["portfolio_snapshot"], verification_status=status, library=library,
        library_sha256=sha, benchmark_reference=reference, reference_label=label,
        macro_factors=factors, macro_label=factor_label, covariance_estimator=covariance_estimator,
    )
    if not save:
        return result
    common = phase4.returns.cumulative_comparison_returns.dropna(how="any").index
    analysis_id = save_analysis_run(
        dataset_id, portfolio_name=phase4.portfolio_name,
        window_start=common[0].strftime("%Y-%m-%d"), window_end=common[-1].strftime("%Y-%m-%d"),
        settings=result.settings, quality=result.quality, metrics=result.metrics, path=db_path,
    )
    existing = list_scenario_runs(analysis_id, path=db_path)
    names = [result.vectors[s].name for s in result.vectors]
    if existing:
        if [row["scenario_name"] for row in existing] != names:
            raise ValueError("Stored scenario runs for this analysis do not match the current scenario set.")
        run_ids = {s: int(row["scenario_run_id"]) for s, row in zip(result.vectors, existing)}
    else:
        run_ids = {}
        for scenario_id, vector in result.vectors.items():
            impact = result.impacts[scenario_id]
            contributions = impact.holdings[["holding_value_inr", "shock", "impact_inr", "portfolio_impact_pct"]]
            run_ids[scenario_id] = save_dataset_scenario(
                dataset_id=dataset_id, analysis_id=analysis_id, as_of_date=result.as_of_date,
                name=vector.name, scenario_type=vector.scenario_type,
                portfolio_value_inr=impact.portfolio_value_inr, impact_inr=impact.impact_inr,
                impact_pct=impact.impact_pct, settings=_scenario_settings(result, scenario_id),
                alerts=jsonable({"risk_integration": result.integration[scenario_id]}),
                contributions=contributions, path=db_path,
            )
    return replace(result, analysis_id=analysis_id, scenario_run_ids=run_ids)
