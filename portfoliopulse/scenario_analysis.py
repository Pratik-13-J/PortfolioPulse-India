"""Phase 5 source-linked stress test orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

from portfoliopulse.config import DB_PATH, REAL_PORTFOLIO_NAME
from portfoliopulse.attribution import allocation_alerts, scenario_attribution
from portfoliopulse.database import get_dataset_info, initialize, save_dataset_scenario
from portfoliopulse.portfolio_analysis import Phase4Analysis, analyze_saved_dataset
from portfoliopulse.scenarios import scenario_shocks


@dataclass(frozen=True)
class Phase5Result:
    dataset_id: int
    analysis_id: int
    scenario_run_id: int
    scenario_name: str
    scenario_type: str
    as_of_date: str
    portfolio_value_inr: float
    impact_inr: float
    impact_pct: float
    hypothetical: bool
    contributions: pd.DataFrame
    alerts: dict[str, object]
    shock_settings: dict[str, object]


def run_scenario(
    dataset_id: int, scenario_type: str, *, name: str | None = None,
    broad_shock: float = -0.10, stressed_shock: float = -0.20, residual_shock: float = -0.05,
    individual_shocks: Mapping[str, float] | None = None,
    sector_shocks: Mapping[str, float] | None = None,
    drift_threshold: float = 0.05, single_name_limit: float = 0.40, hhi_limit: float = 0.25,
    db_path: Path = DB_PATH,
) -> Phase5Result:
    """Analyze an explicit saved dataset and persist hypothetical stress results."""
    initialize(db_path)
    info = get_dataset_info(dataset_id, path=db_path)
    if info.get("portfolio_name") != REAL_PORTFOLIO_NAME:
        raise ValueError("Phase 5 requires the saved Indian NSE portfolio dataset, not the fictional demo.")
    analysis: Phase4Analysis = analyze_saved_dataset(dataset_id, db_path=db_path)
    as_of = pd.Timestamp(analysis.risk_weight_date)
    snapshot = [row for row in info["portfolio_snapshot"] if not row["is_benchmark"]]
    symbols = [str(row["symbol"]) for row in snapshot]
    values_series = analysis.returns.holding_values_inr.loc[as_of, symbols]
    actual_series = analysis.returns.market_weights.loc[as_of, symbols]
    total_value = float(values_series.sum())
    if not total_value > 0:
        raise ValueError("The Phase 4 valuation date has no positive portfolio value.")
    shocks = scenario_shocks(
        snapshot, scenario_type, broad_shock=broad_shock, stressed_shock=stressed_shock,
        residual_shock=residual_shock, individual_shocks=individual_shocks,
        sector_shocks=sector_shocks,
    )
    contributions, impact, impact_pct = scenario_attribution(
        {s: float(values_series[s]) for s in symbols}, shocks,
    )
    targets = {str(row["symbol"]): float(row["target_weight"]) for row in snapshot}
    alerts = allocation_alerts(
        {s: float(actual_series[s]) for s in symbols}, targets,
        drift_threshold=drift_threshold, single_name_limit=single_name_limit, hhi_limit=hhi_limit,
    )
    scenario_name = name or scenario_type.replace("_", " ").title()
    settings: dict[str, object] = {
        "engine_version": "phase5_v1",
        "interpretation": "hypothetical_one_period_shock_not_forecast_or_historically_calibrated_prediction",
        "portfolio_model": "buy_and_hold_fixed_quantities_with_drifting_weights",
        "price_basis": analysis.price_basis,
        "shock_by_symbol": shocks,
        "default_parameters": {
            "broad_shock": broad_shock, "stressed_shock": stressed_shock,
            "residual_shock": residual_shock,
            "individual_shocks": dict(individual_shocks or {}),
            "sector_shocks": dict(sector_shocks or {}),
            "unspecified_custom_holdings": 0.0,
        },
    }
    run_id = save_dataset_scenario(
        dataset_id=dataset_id, analysis_id=int(analysis.analysis_id), as_of_date=analysis.risk_weight_date,
        name=scenario_name, scenario_type=scenario_type, portfolio_value_inr=total_value,
        impact_inr=impact, impact_pct=impact_pct, settings=settings, alerts=alerts,
        contributions=contributions, path=db_path,
    )
    return Phase5Result(
        dataset_id=dataset_id, analysis_id=int(analysis.analysis_id), scenario_run_id=run_id,
        scenario_name=scenario_name, scenario_type=scenario_type, as_of_date=analysis.risk_weight_date,
        portfolio_value_inr=total_value, impact_inr=impact, impact_pct=impact_pct,
        hypothetical=True, contributions=contributions, alerts=alerts, shock_settings=settings,
    )
