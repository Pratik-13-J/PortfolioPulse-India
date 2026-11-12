"""Run roadmap Phase 4 stress tests for one saved dataset and write structured outputs.

Outputs (for the Conversation 6 dashboard) in ``--report-dir``:
``stress_dataset_<id>_<run>_report.json`` plus CSVs for the scenario
comparison matrix, holding-level impacts and sector-level impacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd

from portfoliopulse.config import DB_PATH, PROJECT_ROOT
from portfoliopulse.data_quality import DataQualityError
from portfoliopulse.portfolio_returns import PortfolioReturnError
from portfoliopulse.reference_data import ReferenceDataError
from portfoliopulse.risk_analysis import COMPARISON_LABEL, DEFAULT_MC_BATCHES, DEFAULT_MC_PATHS, DEFAULT_MC_SEED
from portfoliopulse.stress_analysis import ENGINE_VERSION, StressAnalysis, analyze_saved_dataset_stress, jsonable
from portfoliopulse.stress_scenarios import DEFAULT_LIBRARY_PATH

DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "processed"


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def save_stress_report(result: StressAnalysis, directory: Path = DEFAULT_REPORT_DIR) -> dict[str, Path]:
    """Write the JSON report and three tidy CSV tables; return their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    run = "unsaved" if result.analysis_id is None else f"run_{result.analysis_id}"
    stem = directory / f"stress_dataset_{result.dataset_id}_{run}"
    holdings = pd.concat({sid: impact.holdings for sid, impact in result.impacts.items()},
                         names=["scenario_id", "symbol"]).reset_index()
    sectors = pd.concat({sid: impact.sectors for sid, impact in result.impacts.items()},
                        names=["scenario_id", "sector"]).reset_index()
    paths = {
        "comparison_csv": Path(f"{stem}_comparison.csv"),
        "holdings_csv": Path(f"{stem}_holdings.csv"),
        "sectors_csv": Path(f"{stem}_sectors.csv"),
        "report_json": Path(f"{stem}_report.json"),
    }
    _atomic_text(paths["comparison_csv"], result.comparison.to_csv())
    _atomic_text(paths["holdings_csv"], holdings.to_csv(index=False))
    _atomic_text(paths["sectors_csv"], sectors.to_csv(index=False))
    report = {
        "dataset_id": result.dataset_id, "analysis_id": result.analysis_id,
        "scenario_run_ids": result.scenario_run_ids, "engine_version": ENGINE_VERSION,
        "comparison_label": COMPARISON_LABEL,
        "interpretation": "Hypothetical one-period revaluations of current fixed quantities; not forecasts.",
        "source_name": result.source_name, "verification_status": result.verification_status,
        "provenance_status": result.provenance_status, "price_basis": result.price_basis,
        "benchmark_symbol": result.benchmark_symbol, "as_of_date": result.as_of_date,
        "portfolio_value_inr": result.portfolio_value_inr,
        "scenario_library": {"version": result.library_version, "sha256": result.library_sha256},
        "betas": result.betas.to_dict(orient="index"),
        "macro_sensitivity_basis": result.sensitivity_basis,
        "macro_sensitivities": result.sensitivities.to_dict(orient="index"),
        "comparison": result.comparison.reset_index().to_dict(orient="records"),
        "scenarios": {
            sid: {
                "name": vector.name, "category": vector.category, "scenario_type": vector.scenario_type,
                "benchmark_move": vector.benchmark_move, "assumptions": vector.assumptions,
                "warnings": list(vector.warnings),
                "impact_inr": result.impacts[sid].impact_inr, "impact_pct": result.impacts[sid].impact_pct,
                "worst_holdings": result.impacts[sid].worst_holdings,
                "holdings": result.impacts[sid].holdings.reset_index(names="symbol").to_dict(orient="records"),
                "sectors": result.impacts[sid].sectors.reset_index(names="sector").to_dict(orient="records"),
                "risk_integration": result.integration[sid],
            } for sid, vector in result.vectors.items()
        },
        "events": [outcome.summary() for outcome in result.events],
        "unavailable_scenarios": result.unavailable,
        "warnings": result.warnings,
        "settings": result.settings,
        "limitations": [
            "Price returns vs NIFTY 50 price index (not TRI); dividends and corporate actions are not adjusted.",
            "Betas, covariances and estimated sensitivities are in-sample estimates from the selected dataset.",
            "Assumed macro sensitivities are illustrative configuration, not estimates.",
            "Historical replays apply past observed moves to today's holdings; they do not reconstruct past P&L.",
        ],
        "local_outputs": {name: str(path) for name, path in paths.items()},
    }
    _atomic_text(paths["report_json"],
                 json.dumps(jsonable(report), indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Roadmap Phase 4 stress tests for one saved dataset.")
    parser.add_argument("--dataset-id", required=True, type=int, help="Explicit SQLite dataset ID")
    parser.add_argument("--database", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY_PATH, help="Scenario library JSON")
    parser.add_argument("--nse-reference-csv", type=Path, default=None,
                        help="Optional NSE Indices NIFTY 50 history CSV for events the dataset does not cover")
    parser.add_argument("--macro-factor-csv", type=Path, default=None,
                        help="Optional source-labelled CSV (date, india_10y_yield_pct, usdinr) with a .meta.json sidecar")
    parser.add_argument("--covariance", choices=("sample", "ledoit_wolf", "ewma"), default="sample",
                        help="Estimator for covariance propagation (default sample)")
    parser.add_argument("--mc-paths", type=int, default=DEFAULT_MC_PATHS)
    parser.add_argument("--mc-seed", type=int, default=DEFAULT_MC_SEED)
    parser.add_argument("--mc-batches", type=int, default=DEFAULT_MC_BATCHES)
    parser.add_argument("--no-save", action="store_true", help="Compute and report without a DB write")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    try:
        result = analyze_saved_dataset_stress(
            args.dataset_id, db_path=args.database, library_path=args.library,
            nse_reference_csv=args.nse_reference_csv, macro_factor_csv=args.macro_factor_csv,
            covariance_estimator=args.covariance, save=not args.no_save,
            mc_paths=args.mc_paths, mc_seed=args.mc_seed, mc_batches=args.mc_batches,
        )
        paths = save_stress_report(result, args.report_dir)
    except (ValueError, DataQualityError, PortfolioReturnError, ReferenceDataError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Stress analysis failed: {exc}\n")
    print(f"Dataset {result.dataset_id}; analysis run: {result.analysis_id}; engine {ENGINE_VERSION}")
    print(f"Source: {result.source_name}; verification: {result.verification_status}; "
          f"price basis: {result.price_basis}; {COMPARISON_LABEL}")
    print(f"As of {result.as_of_date}; portfolio value INR {result.portfolio_value_inr:,.2f}; "
          f"macro sensitivities: {result.sensitivity_basis}. Hypothetical revaluations, not forecasts.")
    for sid, row in result.comparison.iterrows():
        print(f"  {sid:38s} {row['category']:22s} {row['impact_pct']:8.2%}  INR {row['impact_inr']:>11,.0f}  "
              f"worst {row['worst_holding']}")
    for sid, reason in result.unavailable.items():
        print(f"  UNAVAILABLE {sid}: {reason}")
    print(f"Report: {paths['report_json']}")


if __name__ == "__main__":
    main()
