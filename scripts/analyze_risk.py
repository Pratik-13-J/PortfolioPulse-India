"""Run and save the Phase 3 advanced risk engine for one saved candle dataset."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH, PROJECT_ROOT
from portfoliopulse.data_quality import DataQualityError
from portfoliopulse.portfolio_returns import PortfolioReturnError
from portfoliopulse.risk_analysis import (
    COMPARISON_LABEL, DEFAULT_BACKTEST_WINDOW, DEFAULT_MC_BATCHES, DEFAULT_MC_PATHS, DEFAULT_MC_SEED,
    ENGINE_VERSION, RiskAnalysis, analyze_saved_dataset_risk,
)

DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "processed"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def save_risk_report(result: RiskAnalysis, directory: Path = DEFAULT_REPORT_DIR) -> Path:
    """Write a local, provenance-labelled JSON report of one risk run."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    run = "unsaved" if result.analysis_id is None else f"run_{result.analysis_id}"
    path = directory / f"phase3_risk_dataset_{result.dataset_id}_{run}_report.json"
    report = {
        "dataset_id": result.dataset_id, "analysis_id": result.analysis_id,
        "engine_version": ENGINE_VERSION, "comparison_label": COMPARISON_LABEL,
        "source_name": result.source_name, "verification_status": result.verification_status,
        "provenance_status": result.provenance_status, "price_basis": result.price_basis,
        "benchmark_symbol": result.benchmark_symbol, "risk_weight_date": result.risk_weight_date,
        "valid_return_observations": result.observations,
        "portfolio_value_inr": result.portfolio_value_inr,
        "tail_statistics_reliability": result.quality["tail_statistics_reliability"],
        "horizon": "1 trading day; no sqrt(h) scaling applied",
        "var_es": result.var_table.to_dict(orient="records"),
        "tail_observations": result.quality["tail_observations"],
        "backtests": result.backtests,
        "drawdowns": result.drawdowns,
        "covariance_parameters": result.covariance_parameters,
        "components": {key: frame.reset_index(names="symbol").to_dict(orient="records")
                       for key, frame in result.components.items()},
        "undefined_metrics": result.undefined,
        "settings": result.settings,
        "limitations": [
            "In-sample historical statistics, not forecasts or guaranteed loss limits.",
            "Price returns vs NIFTY 50 price index (not TRI); dividends/corporate actions not adjusted.",
            "Observed dates are not validated against an NSE trading calendar.",
        ],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(_clean(report), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 advanced risk engine for one saved dataset.")
    parser.add_argument("--dataset-id", required=True, type=int, help="Explicit SQLite dataset ID")
    parser.add_argument("--database", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument("--mc-paths", type=int, default=DEFAULT_MC_PATHS, help="Monte Carlo paths")
    parser.add_argument("--mc-seed", type=int, default=DEFAULT_MC_SEED, help="Monte Carlo seed")
    parser.add_argument("--mc-batches", type=int, default=DEFAULT_MC_BATCHES, help="Batches for standard errors")
    parser.add_argument("--backtest-window", type=int, default=DEFAULT_BACKTEST_WINDOW,
                        help="Trailing observations per rolling VaR forecast")
    parser.add_argument("--no-save", action="store_true", help="Compute and report without a DB write")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Report directory")
    args = parser.parse_args()
    try:
        result = analyze_saved_dataset_risk(
            args.dataset_id, db_path=args.database, save=not args.no_save,
            mc_paths=args.mc_paths, mc_seed=args.mc_seed, mc_batches=args.mc_batches,
            backtest_window=args.backtest_window,
        )
        report = save_risk_report(result, args.report_dir)
    except (ValueError, DataQualityError, PortfolioReturnError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Phase 3 risk analysis failed: {exc}\n")
    print(f"Dataset {result.dataset_id}; analysis run: {result.analysis_id}; engine {ENGINE_VERSION}")
    print(f"Source: {result.source_name}; verification: {result.verification_status}; "
          f"price basis: {result.price_basis}; {COMPARISON_LABEL}")
    print(f"Valid daily returns N = {result.observations}; "
          f"tail reliability: {result.quality['tail_statistics_reliability']}; horizon 1 day")
    table = result.var_table[result.var_table["series"] == "portfolio"]
    for row in table.itertuples():
        se = "" if np.isnan(row.var_standard_error) else f" (SE {row.var_standard_error:.4%})"
        print(f"  {row.method:34s} {row.confidence:.0%}: VaR {row.var_1d:.3%}{se}, ES {row.es_1d:.3%}")
    tail = result.tail_observations.get("portfolio:c99")
    if tail is not None:
        print(f"99% historical tail: k = {len(tail)} worst observations")
        for row in tail.itertuples():
            print(f"  {row.rank}. {row.date:%Y-%m-%d} {row.daily_return:.3%}")
    for key, test in result.backtests.items():
        print(f"Backtest {key}: {test['exceptions']} exceptions / {test['observations']} "
              f"(expected {test['expected_exceptions']:.1f}); Kupiec p {test['kupiec_p_value']:.3f}; "
              f"Christoffersen p {test['christoffersen_p_value']:.3f}")
    print(f"Full report: {report}")


if __name__ == "__main__":
    main()
