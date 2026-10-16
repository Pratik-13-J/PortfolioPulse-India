"""Phase 4 orchestration for one explicitly selected, source-labelled dataset.

The calculations below use saved dataset holdings, never today's mutable
portfolio settings. No UI or external market-data access occurs here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import numpy as np

from portfoliopulse.config import DB_PATH, REAL_PORTFOLIO_NAME, TRADING_DAYS_PER_YEAR
from portfoliopulse.data_quality import CandleQualityReport, DataQualityError, validate_daily_candles
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, save_analysis_run,
)
from portfoliopulse.portfolio_returns import BuyAndHoldReturns, build_buy_and_hold_returns
from portfoliopulse.risk_metrics import (
    annualized_volatility, asset_correlations, historical_var_es, sharpe_ratio,
    volatility_contributions,
)


@dataclass(frozen=True)
class Phase4Analysis:
    dataset_id: int
    analysis_id: int | None
    portfolio_name: str
    source_name: str
    source_url: str | None
    retrieved_at_utc: str
    provenance_status: str
    price_basis: str
    dataset_key: str
    input_csv_sha256: str | None
    benchmark_symbol: str
    risk_weight_date: str
    annual_risk_free_rate: float
    confidence: float
    quality: CandleQualityReport
    returns: BuyAndHoldReturns
    metrics: dict[str, float | None]
    metric_units: dict[str, str]
    correlations: pd.DataFrame
    risk_contributions: pd.DataFrame | None
    undefined_metrics: dict[str, str]


def _benchmark_and_quantities(snapshot: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, float]]:
    if not snapshot:
        raise ValueError("The dataset has no saved holding snapshot.")
    benchmarks = [row for row in snapshot if row["is_benchmark"] == 1]
    if len(benchmarks) != 1:
        raise ValueError("The dataset needs exactly one saved benchmark.")
    benchmark = str(benchmarks[0]["symbol"])
    quantities = {str(row["symbol"]): float(row["quantity"]) for row in snapshot if row["is_benchmark"] == 0}
    if not quantities or benchmark in quantities or len(quantities) + 1 != len(snapshot):
        raise ValueError("The saved holding snapshot is incomplete or duplicated.")
    return benchmark, quantities


def _observed_max_drawdown(cumulative_price_return: pd.Series) -> float:
    """Drawdown from actual complete-date levels, including the initial peak."""
    observed = cumulative_price_return.dropna()
    if len(observed) < 2:
        raise ValueError("At least two observed price levels are required for drawdown.")
    wealth = observed + 1.0
    if (wealth <= 0).any():
        raise ValueError("Observed wealth levels must be positive.")
    return float((wealth / wealth.cummax() - 1.0).min())


def compute_phase4_analysis(
    dataset_id: int,
    dataset_info: Mapping[str, Any],
    candles: pd.DataFrame,
    *,
    annual_risk_free_rate: float = 0.0,
    confidence: float = 0.95,
) -> Phase4Analysis:
    """Compute comparable portfolio and NIFTY price analytics without DB writes."""
    if dataset_info.get("portfolio_name") != REAL_PORTFOLIO_NAME:
        raise ValueError("Phase 4 requires the saved real NSE portfolio, not the fictional demo.")
    if dataset_id != dataset_info.get("dataset_id"):
        raise ValueError("The requested dataset ID does not match the saved candle metadata.")
    benchmark, quantities = _benchmark_and_quantities(dataset_info["portfolio_snapshot"])
    symbols = [*quantities, benchmark]
    quality = validate_daily_candles(
        candles, symbols,
        start=dataset_info["window_start"],
        end_exclusive=dataset_info["window_end_exclusive"],
        expected_price_basis=dataset_info["price_basis"],
    )
    if quality.source != dataset_info["source_name"]:
        raise DataQualityError("Stored candle source does not match the dataset label.")
    grid = quality.candles.pivot(index="date", columns="symbol", values="close")
    returns = build_buy_and_hold_returns(grid, quantities, benchmark)
    if not returns.daily_comparison_returns.index.equals(quality.daily_returns.index):
        raise DataQualityError("The portfolio return calendar disagrees with Phase 3 quality validation.")

    portfolio_daily = returns.daily_comparison_returns["portfolio"]
    benchmark_daily = returns.daily_comparison_returns[benchmark]
    portfolio_var, portfolio_es = historical_var_es(portfolio_daily, confidence=confidence)
    benchmark_var, benchmark_es = historical_var_es(benchmark_daily, confidence=confidence)
    if isinstance(annual_risk_free_rate, (bool, np.bool_)):
        raise ValueError("The annual INR risk-free assumption must be numeric.")
    try:
        rf = float(annual_risk_free_rate)
    except (TypeError, ValueError) as exc:
        raise ValueError("The annual INR risk-free assumption must be numeric.") from exc
    if not math.isfinite(rf) or rf <= -1:
        raise ValueError("The annual INR risk-free assumption must be finite and above -100%.")
    portfolio_sharpe = sharpe_ratio(portfolio_daily, annual_risk_free_rate=rf)
    benchmark_sharpe = sharpe_ratio(benchmark_daily, annual_risk_free_rate=rf)

    indexed = returns.cumulative_comparison_returns
    common_levels = indexed.dropna(how="any")
    # Attribute risk using the last date in the same price-comparison window.
    # A later holding quote without a NIFTY quote must not move this as-of date.
    latest_weights = returns.market_weights.loc[common_levels.index[-1]]
    assets_and_comparison = pd.concat([
        returns.asset_daily_returns,
        benchmark_daily.rename(benchmark),
        portfolio_daily.rename("portfolio"),
    ], axis=1)
    correlations = asset_correlations(assets_and_comparison)
    portfolio_benchmark_corr = correlations.at["portfolio", benchmark]
    undefined: dict[str, str] = {}
    if portfolio_sharpe is None:
        undefined["portfolio_sharpe"] = "The portfolio daily return sample has zero volatility."
    if benchmark_sharpe is None:
        undefined["benchmark_sharpe"] = "The benchmark daily return sample has zero volatility."
    if pd.isna(portfolio_benchmark_corr):
        undefined["portfolio_benchmark_correlation"] = "At least one return series is constant."

    try:
        contributions = volatility_contributions(returns.asset_daily_returns, latest_weights)
        proxy_volatility: float | None = float(contributions["component_annual_volatility"].sum())
    except ValueError as exc:
        if "volatility is zero" not in str(exc):
            raise
        contributions = None
        proxy_volatility = None
        undefined["current_weight_proxy_annualized_volatility"] = "Current-weight proxy volatility is zero."
        undefined["asset_volatility_contributions"] = "Marginal and component shares are undefined at zero proxy volatility."

    metrics: dict[str, float | None] = {
        "portfolio_first_value_inr": float(returns.portfolio_value_inr.loc[common_levels.index[0]]),
        "portfolio_last_value_inr": float(returns.portfolio_value_inr.loc[common_levels.index[-1]]),
        "portfolio_cumulative_price_return": float(common_levels.iloc[-1]["portfolio"]),
        "portfolio_annualized_price_return": returns.endpoint_annualized_portfolio_return,
        "portfolio_annualized_volatility": annualized_volatility(portfolio_daily),
        "portfolio_sharpe": portfolio_sharpe,
        "portfolio_max_drawdown": _observed_max_drawdown(returns.cumulative_portfolio_return),
        "portfolio_matched_max_drawdown": _observed_max_drawdown(indexed["portfolio"]),
        "portfolio_var_1d": portfolio_var,
        "portfolio_expected_shortfall_1d": portfolio_es,
        "benchmark_cumulative_price_return": float(common_levels.iloc[-1][benchmark]),
        "benchmark_annualized_price_return": returns.endpoint_annualized_benchmark_return,
        "benchmark_annualized_volatility": annualized_volatility(benchmark_daily),
        "benchmark_sharpe": benchmark_sharpe,
        "benchmark_max_drawdown": _observed_max_drawdown(
            returns.price_grid[benchmark].div(
                returns.price_grid.at[common_levels.index[0], benchmark]
            ).sub(1.0).loc[common_levels.index[0]:common_levels.index[-1]]
        ),
        "benchmark_matched_max_drawdown": _observed_max_drawdown(indexed[benchmark]),
        "benchmark_var_1d": benchmark_var,
        "benchmark_expected_shortfall_1d": benchmark_es,
        "portfolio_benchmark_correlation": (
            None if pd.isna(portfolio_benchmark_corr) else float(portfolio_benchmark_corr)
        ),
        "current_weight_proxy_annualized_volatility": proxy_volatility,
    }
    units = {
        name: ("INR" if name.endswith("_inr") else "unitless" if name.endswith("_sharpe")
               or name.endswith("_correlation") else "annual_ratio" if "annualized" in name
               else "daily_ratio" if name.endswith("_1d") else "ratio")
        for name in metrics
    }
    return Phase4Analysis(
        dataset_id=dataset_id, analysis_id=None,
        portfolio_name=dataset_info["portfolio_name"],
        source_name=dataset_info["source_name"],
        source_url=dataset_info.get("source_url"),
        retrieved_at_utc=dataset_info.get("retrieved_at_utc", ""),
        provenance_status=dataset_info["provenance_status"],
        price_basis=dataset_info["price_basis"],
        dataset_key=dataset_info.get("dataset_key", ""),
        input_csv_sha256=dataset_info.get("input_csv_sha256"),
        benchmark_symbol=benchmark,
        risk_weight_date=common_levels.index[-1].strftime("%Y-%m-%d"),
        annual_risk_free_rate=rf, confidence=float(confidence),
        quality=quality, returns=returns,
        metrics=metrics, metric_units=units,
        correlations=correlations, risk_contributions=contributions,
        undefined_metrics=undefined,
    )


def analyze_saved_dataset(
    dataset_id: int, *,
    annual_risk_free_rate: float = 0.0,
    confidence: float = 0.95,
    db_path: Path = DB_PATH,
) -> Phase4Analysis:
    """Read one dataset, calculate analytics, then save finite scalar results."""
    info = get_dataset_info(dataset_id, path=db_path)
    candles = get_candle_dataset(dataset_id, path=db_path)
    result = compute_phase4_analysis(
        dataset_id, info, candles,
        annual_risk_free_rate=annual_risk_free_rate, confidence=confidence,
    )
    common_dates = result.returns.cumulative_comparison_returns.dropna(how="any").index
    settings = {
        "stage": "phase4_portfolio_analytics",
        "engine_version": "phase4_v1",
        "portfolio_model": "buy_and_hold_fixed_quantities",
        "benchmark_symbol": result.benchmark_symbol,
        "return_basis": "close_to_close_price_return",
        "price_basis": result.price_basis,
        "annual_risk_free_rate_assumption": result.annual_risk_free_rate,
        "confidence": result.confidence,
        "annualization_observations": TRADING_DAYS_PER_YEAR,
        "annualized_return_method": "endpoint_price_ratio_power_252_over_observed_union_intervals",
        "volatility_method": "sample_daily_std_ddof_1_times_sqrt_252",
        "sharpe_method": "mean_daily_excess_over_sample_daily_std_times_sqrt_252",
        "var_es_method": "nearest_rank_worst_ceil_tail_count",
        "risk_contribution_method": "last_matched_date_weight_sample_covariance_proxy",
        "daily_missing_policy": "exclude_missing_current_or_previous_observed_quote",
    }
    quality = result.quality.to_summary()
    quality["endpoint_observed_union_intervals"] = result.returns.observed_union_intervals
    quality["undefined_metrics"] = result.undefined_metrics
    scalar_results = {
        name: (float(value), result.metric_units[name])
        for name, value in result.metrics.items() if value is not None
    }
    if result.risk_contributions is not None:
        for symbol, row in result.risk_contributions.iterrows():
            scalar_results[f"weight:{symbol}"] = (float(row["weight"]), "ratio")
            scalar_results[f"marginal_volatility:{symbol}"] = (
                float(row["marginal_annual_volatility"]), "annual_ratio",
            )
            scalar_results[f"component_volatility:{symbol}"] = (
                float(row["component_annual_volatility"]), "annual_ratio",
            )
            scalar_results[f"volatility_share:{symbol}"] = (
                float(row["percent_contribution"]), "ratio",
            )
    analysis_id = save_analysis_run(
        dataset_id, portfolio_name=result.portfolio_name,
        window_start=common_dates[0].strftime("%Y-%m-%d"),
        window_end=common_dates[-1].strftime("%Y-%m-%d"),
        settings=settings, quality=quality, metrics=scalar_results, path=db_path,
    )
    return replace(result, analysis_id=analysis_id)
