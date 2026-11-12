"""Phase 3 advanced risk engine orchestration (engine ``phase3_risk_v1``).

Builds on an in-memory Phase 4 analysis of one saved dataset and persists a
*separate* analysis run (its own ``analysis_metadata`` row and result keys),
so existing Phase 4 / ``phase8_perf_v1`` rows, keys and the 20-key
``Phase4Analysis.metrics`` are untouched. No schema change is needed.

Two sample bases are kept apart and labelled:

* ``realized_portfolio``: the realized buy-and-hold (drifting-weight) daily
  portfolio price returns; used by historical, normal and Cornish-Fisher VaR,
  the backtests and drawdowns (the same series as Phase 4 historical VaR).
* ``current_weight``: asset returns revalued at the fixed quantities held on
  the risk-weight date; used by Monte Carlo, delta-normal and component VaR/ES.

All VaR/ES figures are one-day losses (decimal fraction of portfolio value,
INR equivalents at the risk-weight-date value), "price return vs NIFTY 50
price index (not TRI)". In-sample, not forecasts or guaranteed loss limits.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH, TRADING_DAYS_PER_YEAR
from portfoliopulse.covariance_estimators import DEFAULT_EWMA_LAMBDA, covariance_estimates
from portfoliopulse.database import get_candle_dataset, get_dataset_info, save_analysis_run
from portfoliopulse.drawdowns import drawdown_summary
from portfoliopulse.portfolio_analysis import Phase4Analysis, compute_phase4_analysis
from portfoliopulse.var_backtests import FORECAST_METHODS, backtest_var, rolling_var_forecasts
from portfoliopulse.var_models import (
    DEFAULT_CONFIDENCES, MONTE_CARLO_METHODS, VaREstimate, cornish_fisher,
    delta_normal_components, historical, historical_tail_observations, monte_carlo,
    parametric_normal, sample_mean_and_covariance,
)

ENGINE_VERSION = "phase3_risk_v1"
COMPARISON_LABEL = "price return vs NIFTY 50 price index (not TRI)"
MIN_TAIL_SAMPLE = 250
DEFAULT_MC_PATHS = 100_000
DEFAULT_MC_SEED = 20261112
DEFAULT_MC_BATCHES = 50
DEFAULT_BACKTEST_WINDOW = 250


@dataclass(frozen=True)
class RiskAnalysis:
    dataset_id: int
    analysis_id: int | None
    source_name: str
    verification_status: str
    provenance_status: str
    price_basis: str
    benchmark_symbol: str
    risk_weight_date: str
    observations: int
    portfolio_value_inr: float
    sample_adequate_for_tails: bool
    var_table: pd.DataFrame
    tail_observations: dict[str, pd.DataFrame]
    backtests: dict[str, dict[str, Any]]
    drawdowns: dict[str, dict[str, Any]]
    covariances: dict[str, pd.DataFrame]
    covariance_parameters: dict[str, float]
    components: dict[str, pd.DataFrame]
    undefined: dict[str, str]
    settings: dict[str, Any]
    quality: dict[str, Any]
    metrics: dict[str, tuple[float, str]]


def level_tag(level: float) -> str:
    return f"c{level * 100:g}"


def _row(series: str, basis: str, estimate: VaREstimate, value_inr: float, **extra: Any) -> dict[str, Any]:
    return {
        "series": series, "sample_basis": basis, "method": estimate.method,
        "confidence": estimate.confidence, "var_1d": estimate.var, "es_1d": estimate.es,
        "var_1d_inr": estimate.var * value_inr if series == "portfolio" else np.nan,
        "es_1d_inr": estimate.es * value_inr if series == "portfolio" else np.nan,
        "var_standard_error": extra.get("var_se", np.nan), "es_standard_error": extra.get("es_se", np.nan),
        "observations": extra.get("observations", estimate.observations),
    }


def compute_risk_analysis(
    phase4: Phase4Analysis,
    *,
    verification_status: str,
    confidences: Sequence[float] = DEFAULT_CONFIDENCES,
    mc_paths: int = DEFAULT_MC_PATHS,
    mc_seed: int = DEFAULT_MC_SEED,
    mc_batches: int = DEFAULT_MC_BATCHES,
    backtest_window: int = DEFAULT_BACKTEST_WINDOW,
    ewma_lambda: float = DEFAULT_EWMA_LAMBDA,
) -> RiskAnalysis:
    """Compute every Phase 3 risk output for one Phase 4 analysis (no DB I/O)."""
    returns = phase4.returns
    benchmark = phase4.benchmark_symbol
    portfolio = returns.daily_comparison_returns["portfolio"]
    benchmark_daily = returns.daily_comparison_returns[benchmark]
    assets = returns.asset_daily_returns
    if not assets.index.equals(portfolio.index):
        raise ValueError("Asset and portfolio daily returns must share the same valid dates.")
    as_of = pd.Timestamp(phase4.risk_weight_date)
    holding_values = returns.holding_values_inr.loc[as_of, list(assets.columns)].astype(float)
    if holding_values.isna().any():
        raise ValueError("Every holding needs a value on the risk-weight date.")
    value_inr = float(holding_values.sum())
    weights = (holding_values / value_inr).rename("weight")
    n = len(portfolio)
    levels = [float(level) for level in confidences]
    undefined: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    tails: dict[str, pd.DataFrame] = {}
    cf_flags: dict[str, bool] = {}

    for series_name, series in (("portfolio", portfolio), ("benchmark", benchmark_daily)):
        for level in levels:
            tag = level_tag(level)
            rows.append(_row(series_name, "realized_portfolio" if series_name == "portfolio" else "benchmark_index",
                             historical(series, confidence=level), value_inr))
            tails[f"{series_name}:{tag}"] = historical_tail_observations(series, confidence=level)
            for model in (parametric_normal, cornish_fisher):
                try:
                    estimate = model(series, confidence=level)
                except ValueError as exc:
                    undefined[f"{series_name}:{model.__name__}:{tag}"] = str(exc)
                    continue
                rows.append(_row(series_name, "realized_portfolio" if series_name == "portfolio"
                                 else "benchmark_index", estimate, value_inr))
                if model is cornish_fisher:
                    cf_flags[f"{series_name}:{tag}"] = bool(estimate.details["tail_monotone"])
                    rows[-1]["skewness"] = estimate.details["skewness"]
                    rows[-1]["excess_kurtosis"] = estimate.details["excess_kurtosis"]

    components: dict[str, pd.DataFrame] = {}
    mc_details: dict[str, Any] = {}
    for method in MONTE_CARLO_METHODS:
        results = monte_carlo(assets, holding_values, method=method, confidences=levels,
                              paths=mc_paths, seed=mc_seed, batches=mc_batches)
        for level, estimate in results.items():
            rows.append(_row("portfolio", "current_weight", estimate, value_inr,
                             var_se=estimate.var_standard_error, es_se=estimate.es_standard_error))
            mc_details[f"{estimate.method}:{level_tag(level)}"] = {
                **estimate.details, "seed": estimate.seed, "paths": estimate.paths, "batches": estimate.batches,
            }
            if method == "bootstrap":
                components[f"monte_carlo_bootstrap:{level_tag(level)}"] = pd.DataFrame({
                    "weight": weights, "component_es": estimate.component_es,
                    "component_es_inr": estimate.component_es * value_inr,
                })

    covariances, cov_params = covariance_estimates(assets, decay=ewma_lambda)
    sample_mean, _ = sample_mean_and_covariance(assets)
    means = {"sample": sample_mean, "ledoit_wolf": sample_mean, "ewma": sample_mean * 0.0}
    for estimator, matrix in covariances.items():
        for level in levels:
            try:
                total, frame = delta_normal_components(
                    means[estimator], matrix, weights, confidence=level, portfolio_value_inr=value_inr)
            except ValueError as exc:
                undefined[f"delta_normal_{estimator}:{level_tag(level)}"] = str(exc)
                continue
            rows.append(_row("portfolio", "current_weight",
                             replace(total, method=f"delta_normal_{estimator}", observations=n), value_inr))
            components[f"delta_normal_{estimator}:{level_tag(level)}"] = frame

    backtests: dict[str, dict[str, Any]] = {}
    for method in FORECAST_METHODS:
        for level in levels:
            key = f"{method}:{level_tag(level)}"
            if n <= backtest_window:
                undefined[f"backtest:{key}"] = (
                    f"Needs more than {backtest_window} valid returns for a rolling window; got {n}.")
                continue
            frame = rolling_var_forecasts(portfolio, window=backtest_window, confidence=level, method=method)
            backtests[key] = backtest_var(frame, confidence=level)

    drawdowns = {
        "portfolio": drawdown_summary(returns.cumulative_portfolio_return),
        "benchmark_matched": drawdown_summary(returns.cumulative_comparison_returns[benchmark]),
    }
    weight_vector = weights.to_numpy()
    annual_vol = {
        name: float(np.sqrt(weight_vector @ matrix.to_numpy() @ weight_vector * TRADING_DAYS_PER_YEAR))
        for name, matrix in covariances.items()
    }
    var_table = pd.DataFrame(rows)
    adequate = n >= MIN_TAIL_SAMPLE
    settings = {
        "stage": "phase3_advanced_risk_engine",
        "engine_version": ENGINE_VERSION,
        "comparison_label": COMPARISON_LABEL,
        "source_name": phase4.source_name,
        "verification_status": verification_status,
        "provenance_status": phase4.provenance_status,
        "price_basis": phase4.price_basis,
        "portfolio_model": "buy_and_hold_fixed_quantities",
        "return_basis": "close_to_close_price_return",
        "benchmark_symbol": benchmark,
        "horizon_trading_days": 1,
        "multi_day_scaling": "not applied; sqrt(h) scaling would assume iid returns",
        "confidences": levels,
        "risk_weight_date": phase4.risk_weight_date,
        "sample_bases": {
            "realized_portfolio": "realized buy-and-hold daily portfolio price returns (drifting weights)",
            "current_weight": "asset daily returns revalued at fixed quantities held on risk_weight_date",
        },
        "methods": {
            "historical": "risk_metrics.historical_var_es nearest rank k=max(1,ceil((1-c)N))",
            "normal": "VaR=-(mu+z*sigma); ES=-mu+sigma*phi(z)/(1-c); sample mean, std ddof=1",
            "cornish_fisher": "moment skewness/excess kurtosis; ES = tail average of CF quantile (closed form)",
            "monte_carlo_multivariate_normal": "seeded MVN with sample mean and ddof=1 covariance of asset returns",
            "monte_carlo_bootstrap": "seeded iid bootstrap of historical asset-return dates",
            "monte_carlo_estimator": "nearest-rank VaR/ES of simulated revalued portfolio returns",
            "monte_carlo_standard_error": "batch means: std(batch estimates, ddof=1)/sqrt(batches)",
            "delta_normal": "current-weight normal VaR/ES with Euler component allocation",
            "component_es_bootstrap": "negative mean holding contribution over the k worst bootstrap paths",
            "covariance_sample": "ddof=1",
            "covariance_ledoit_wolf": "Ledoit-Wolf 2004 shrinkage to scaled identity, 1/N normalisation, sample mean",
            "covariance_ewma": "RiskMetrics zero-mean EWMA, normalised finite weights, zero mean in delta-normal",
            "backtest_forecast": "one-day-ahead VaR from the trailing window only; exception if r_t < -VaR_t",
            "kupiec": "LR_pof ~ chi2(1)", "christoffersen": "LR_ind ~ chi2(1); LR_cc = LR_pof + LR_ind ~ chi2(2)",
            "drawdown_duration": "observed sessions and calendar days; first observed level is the starting peak",
        },
        "monte_carlo": {"seed": int(mc_seed), "paths": int(mc_paths), "batches": int(mc_batches)},
        "backtest_window_observations": int(backtest_window),
        "ewma_lambda": float(ewma_lambda),
        "minimum_tail_sample": MIN_TAIL_SAMPLE,
        "annualization_observations": TRADING_DAYS_PER_YEAR,
    }
    quality = {
        "valid_return_observations": n,
        "first_return_date": portfolio.index[0].strftime("%Y-%m-%d"),
        "last_return_date": portfolio.index[-1].strftime("%Y-%m-%d"),
        "sample_adequate_for_tail_statistics": adequate,
        "tail_statistics_reliability": (
            "in_sample_historical" if adequate else "insufficient_sample_smoke_test_only"),
        "tail_observations": {
            key: [{"rank": int(r["rank"]), "date": r["date"].strftime("%Y-%m-%d"),
                   "daily_return": float(r["daily_return"])} for _, r in frame.iterrows()]
            for key, frame in tails.items()
        },
        "cornish_fisher_tail_monotone": cf_flags,
        "monte_carlo": mc_details,
        "backtests": {key: {k: v for k, v in value.items() if k in (
            "first_forecast_date", "last_forecast_date", "exception_dates", "transitions")}
            for key, value in backtests.items()},
        "drawdowns": drawdowns,
        "undefined_metrics": undefined,
        "excluded_return_dates": list(returns.excluded_return_dates),
    }
    metrics = _scalar_metrics(var_table, backtests, drawdowns, components, annual_vol, cov_params,
                              value_inr, n)
    return RiskAnalysis(
        dataset_id=phase4.dataset_id, analysis_id=None, source_name=phase4.source_name,
        verification_status=verification_status, provenance_status=phase4.provenance_status,
        price_basis=phase4.price_basis, benchmark_symbol=benchmark,
        risk_weight_date=phase4.risk_weight_date, observations=n, portfolio_value_inr=value_inr,
        sample_adequate_for_tails=adequate, var_table=var_table, tail_observations=tails,
        backtests=backtests, drawdowns=drawdowns, covariances=covariances,
        covariance_parameters=cov_params, components=components, undefined=undefined,
        settings=settings, quality=quality, metrics=metrics,
    )


def _scalar_metrics(
    var_table: pd.DataFrame, backtests: dict[str, dict[str, Any]], drawdowns: dict[str, dict[str, Any]],
    components: dict[str, pd.DataFrame], annual_vol: dict[str, float], cov_params: dict[str, float],
    value_inr: float, observations: int,
) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {
        "risk_valid_return_observations": (float(observations), "count"),
        "risk_portfolio_value_inr": (value_inr, "INR"),
        "ledoit_wolf_shrinkage": (cov_params["ledoit_wolf_shrinkage"], "ratio"),
        "ewma_lambda": (cov_params["ewma_lambda"], "ratio"),
    }
    for row in var_table.to_dict(orient="records"):
        prefix = "" if row["series"] == "portfolio" else "benchmark_"
        suffix = f"{row['method']}:{level_tag(row['confidence'])}"
        out[f"{prefix}var_1d:{suffix}"] = (row["var_1d"], "daily_ratio")
        out[f"{prefix}es_1d:{suffix}"] = (row["es_1d"], "daily_ratio")
        if row["series"] == "portfolio":
            out[f"var_1d_inr:{suffix}"] = (row["var_1d_inr"], "INR")
            out[f"es_1d_inr:{suffix}"] = (row["es_1d_inr"], "INR")
        if np.isfinite(row["var_standard_error"]):
            out[f"var_1d_standard_error:{suffix}"] = (row["var_standard_error"], "daily_ratio")
            out[f"es_1d_standard_error:{suffix}"] = (row["es_standard_error"], "daily_ratio")
        if row["method"] == "cornish_fisher" and row["confidence"] == var_table["confidence"].iloc[0]:
            out[f"{prefix}daily_skewness"] = (row["skewness"], "unitless")
            out[f"{prefix}daily_excess_kurtosis"] = (row["excess_kurtosis"], "unitless")
    for key, result in backtests.items():
        for name, unit in (("observations", "count"), ("exceptions", "count"), ("expected_exceptions", "count"),
                           ("kupiec_lr", "unitless"), ("kupiec_p_value", "probability"),
                           ("christoffersen_lr", "unitless"), ("christoffersen_p_value", "probability"),
                           ("conditional_coverage_lr", "unitless"),
                           ("conditional_coverage_p_value", "probability")):
            out[f"backtest_{name}:{key}"] = (float(result[name]), unit)
    for label, summary in drawdowns.items():
        out[f"drawdown_max:{label}"] = (summary["max_drawdown"], "ratio")
        out[f"drawdown_current:{label}"] = (summary["current_drawdown"], "ratio")
        out[f"drawdown_episodes:{label}"] = (float(summary["episodes"]), "count")
        for episode in ("max_drawdown_episode", "longest_drawdown_episode"):
            if episode not in summary:
                continue
            item = summary[episode]
            short = "max" if episode.startswith("max") else "longest"
            for name in ("observations_to_trough", "duration_observations", "recovery_observations"):
                out[f"drawdown_{short}_{name}:{label}"] = (float(item[name]), "sessions")
            for name in ("calendar_days_to_trough", "duration_calendar_days", "recovery_calendar_days"):
                out[f"drawdown_{short}_{name}:{label}"] = (float(item[name]), "calendar_days")
            out[f"drawdown_{short}_recovered:{label}"] = (float(item["recovered"]), "boolean")
    for name, value in annual_vol.items():
        out[f"current_weight_annualized_volatility:{name}"] = (value, "annual_ratio")
    for key, frame in components.items():
        for symbol, row in frame.iterrows():
            if "component_var" in frame:
                out[f"component_var_1d:{key}:{symbol}"] = (float(row["component_var"]), "daily_ratio")
                out[f"component_var_1d_inr:{key}:{symbol}"] = (float(row["component_var_inr"]), "INR")
            out[f"component_es_1d:{key}:{symbol}"] = (float(row["component_es"]), "daily_ratio")
            out[f"component_es_1d_inr:{key}:{symbol}"] = (float(row["component_es_inr"]), "INR")
    return {name: (float(value), unit) for name, (value, unit) in out.items() if np.isfinite(value)}


def analyze_saved_dataset_risk(
    dataset_id: int,
    *,
    db_path: Path = DB_PATH,
    save: bool = True,
    **options: Any,
) -> RiskAnalysis:
    """Read one saved dataset, compute Phase 3 risk, and save a separate run."""
    info = get_dataset_info(dataset_id, path=db_path)
    candles = get_candle_dataset(dataset_id, path=db_path)
    # VaR/ES do not use the risk-free rate; Phase 4 is computed in memory only.
    phase4 = compute_phase4_analysis(dataset_id, info, candles)
    result = compute_risk_analysis(phase4, verification_status=str(info["verification_status"]), **options)
    if not save:
        return result
    common = phase4.returns.cumulative_comparison_returns.dropna(how="any").index
    analysis_id = save_analysis_run(
        dataset_id, portfolio_name=phase4.portfolio_name,
        window_start=common[0].strftime("%Y-%m-%d"), window_end=common[-1].strftime("%Y-%m-%d"),
        settings=result.settings, quality=result.quality, metrics=result.metrics, path=db_path,
    )
    return replace(result, analysis_id=analysis_id)
