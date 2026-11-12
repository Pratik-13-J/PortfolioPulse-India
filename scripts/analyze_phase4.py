"""Run and save Phase 4 analytics for one existing SQLite candle dataset."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from portfoliopulse.config import DB_PATH, PROJECT_ROOT
from portfoliopulse.data_quality import DataQualityError
from portfoliopulse.portfolio_analysis import ENGINE_VERSION, Phase4Analysis, analyze_saved_dataset
from portfoliopulse.portfolio_returns import PortfolioReturnError


DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "processed"


def _optional_float(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _atomic_csv(frame: pd.DataFrame, path: Path, *, index_label: str = "date") -> None:
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index_label=index_label)
    os.replace(temporary, path)


def save_phase4_report(result: Phase4Analysis, directory: Path = DEFAULT_REPORT_DIR) -> Path:
    """Write local, source-labelled numbers and plot-ready series for review."""
    if result.analysis_id is None:
        raise ValueError("Save the analysis in SQLite before writing its report.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"phase4_dataset_{result.dataset_id}_run_{result.analysis_id}"
    daily_path = directory / f"{stem}_daily_returns.csv"
    cumulative_path = directory / f"{stem}_cumulative_returns.csv"
    correlation_path = directory / f"{stem}_correlations.csv"
    contribution_path = directory / f"{stem}_risk_contributions.csv"
    report_path = directory / f"{stem}_report.json"

    _atomic_csv(result.returns.daily_comparison_returns, daily_path)
    cumulative = result.returns.cumulative_comparison_returns.copy()
    cumulative["portfolio_observed"] = result.returns.cumulative_portfolio_return
    _atomic_csv(cumulative, cumulative_path)
    _atomic_csv(result.correlations, correlation_path, index_label="symbol")
    if result.risk_contributions is not None:
        _atomic_csv(result.risk_contributions, contribution_path, index_label="symbol")

    weights = result.returns.market_weights.loc[pd.Timestamp(result.risk_weight_date)]
    report = {
        "dataset_id": result.dataset_id,
        "analysis_id": result.analysis_id,
        "portfolio_name": result.portfolio_name,
        "source_name": result.source_name,
        "source_url": result.source_url,
        "retrieved_at_utc": result.retrieved_at_utc,
        "provenance_status": result.provenance_status,
        "price_basis": result.price_basis,
        "dataset_key": result.dataset_key,
        "input_csv_sha256": result.input_csv_sha256,
        "return_basis": "close_to_close_price_return",
        "portfolio_model": "buy_and_hold_fixed_quantities",
        "engine_version": ENGINE_VERSION,
        "benchmark_symbol": result.benchmark_symbol,
        "annual_risk_free_rate_assumption": result.annual_risk_free_rate,
        "confidence": result.confidence,
        "metrics": result.metrics,
        "metric_units": result.metric_units,
        "quality": result.quality.to_summary(),
        "observed_union_intervals_for_endpoint_annualization": result.returns.observed_union_intervals,
        "undefined_metrics": result.undefined_metrics,
        "performance": None if result.performance is None else {
            "comparison_label": "price return vs NIFTY 50 price index (not TRI)",
            "observations": result.performance.observations,
            "metrics": result.performance.metrics,
            "metric_units": result.performance.units,
            "undefined_metrics": result.performance.undefined,
            "period_returns": result.performance.periods,
            "contribution_reconciliation_error": result.performance.contributions.reconciliation_error,
        },
        "corporate_action_jump_warnings": (
            [] if result.jump_warnings is None else result.jump_warnings.to_dict(orient="records")
        ),
        "risk_weight_date": result.risk_weight_date,
        "risk_market_weights": {symbol: float(weight) for symbol, weight in weights.items()},
        "risk_contributions": (
            None if result.risk_contributions is None else {
                symbol: {name: float(value) for name, value in row.items()}
                for symbol, row in result.risk_contributions.iterrows()
            }
        ),
        "correlations": {
            symbol: {peer: _optional_float(value) for peer, value in row.items()}
            for symbol, row in result.correlations.iterrows()
        },
        "methodology": {
            "annualized_return": "first_to_last_complete_price_ratio ** (252 / observed_union_intervals) - 1",
            "daily_risk_sample": "only adjacent observed dates with every holding and benchmark close",
            "annualized_volatility": "sample_std_of_valid_daily_returns_ddof_1 * sqrt(252)",
            "sharpe": "mean_daily_excess_return / sample_daily_std * sqrt(252)",
            "risk_free_daily": "(1 + annual_risk_free_rate_assumption) ** (1 / 252) - 1",
            "maximum_drawdown": "standalone observed portfolio/benchmark paths use their own available dates; matched variants use shared price dates",
            "historical_var_es": "nearest_rank_worst_ceil((1-confidence)*N)_one_day_returns",
            "risk_contributions": "last_matched_date_market_weights * sample_asset_covariance_marginal_volatility",
            "limitation": "Unadjusted OHLCV gives price returns; missing dates and unverified corporate actions limit interpretation.",
        },
        "local_outputs": {
            "daily_returns_csv": str(daily_path),
            "cumulative_returns_csv": str(cumulative_path),
            "correlations_csv": str(correlation_path),
            "risk_contributions_csv": str(contribution_path) if result.risk_contributions is not None else None,
        },
    }
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, report_path)
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze an existing Indian equity candle dataset.")
    parser.add_argument("--dataset-id", required=True, type=int, help="Explicit Phase 3 SQLite dataset ID")
    parser.add_argument("--annual-rf", type=float, default=0.0,
                        help="Assumed effective annual INR risk-free rate (decimal; default 0)")
    parser.add_argument("--confidence", type=float, default=0.95,
                        help="One-day historical VaR/ES confidence (default 0.95)")
    parser.add_argument("--database", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR,
                        help="Local directory for the review report and time series")
    args = parser.parse_args()
    try:
        result = analyze_saved_dataset(
            args.dataset_id, annual_risk_free_rate=args.annual_rf,
            confidence=args.confidence, db_path=args.database,
        )
        report_path = save_phase4_report(result, args.report_dir)
    except (ValueError, DataQualityError, PortfolioReturnError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Phase 4 analysis failed: {exc}\n")
    metrics = result.metrics
    print(f"Portfolio: {result.portfolio_name}; dataset: {result.dataset_id}; analysis run: {result.analysis_id}")
    print(f"Source: {result.source_name}; price basis: {result.price_basis}; model: buy and hold")
    print(
        f"Matched price dates: {result.quality.common_price_dates_count}; "
        f"valid daily-return dates: {result.quality.valid_return_dates_count}; "
        f"excluded return dates: {len(result.quality.excluded_return_dates)}"
    )
    print(
        f"Portfolio endpoint price return: {metrics['portfolio_cumulative_price_return']:.2%}; "
        f"annualized: {metrics['portfolio_annualized_price_return']:.2%}; "
        f"annualized volatility: {metrics['portfolio_annualized_volatility']:.2%}"
    )
    print(
        f"{result.benchmark_symbol} endpoint price return: {metrics['benchmark_cumulative_price_return']:.2%}; "
        f"annualized: {metrics['benchmark_annualized_price_return']:.2%}"
    )
    print(
        f"Historical {result.confidence:.0%} one-day portfolio VaR: {metrics['portfolio_var_1d']:.2%}; "
        f"expected shortfall: {metrics['portfolio_expected_shortfall_1d']:.2%}"
    )
    print(f"Full report: {report_path}")


if __name__ == "__main__":
    main()
