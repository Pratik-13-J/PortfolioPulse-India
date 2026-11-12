"""Roadmap Phase 5: build, compare and store illustrative portfolios for one dataset.

Writes ``optimization_dataset_<id>_<run>_report.json`` plus comparison,
weights, frontier and walk-forward CSVs (structured for Conversation 6).
Original holdings are read only.
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
from portfoliopulse.optimization import OptimizationError
from portfoliopulse.optimization_analysis import (
    DEFAULT_CONFIG_PATH, ENGINE_VERSION, OptimizationAnalysis, analyze_saved_dataset_optimization,
)
from portfoliopulse.portfolio_returns import PortfolioReturnError
from portfoliopulse.risk_analysis import COMPARISON_LABEL
from portfoliopulse.stress_analysis import jsonable

DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "processed"


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def save_optimization_report(result: OptimizationAnalysis, directory: Path = DEFAULT_REPORT_DIR) -> dict[str, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    run = "unsaved" if result.analysis_id is None else f"run_{result.analysis_id}"
    stem = f"optimization_dataset_{result.dataset_id}_{run}"
    paths = {name: directory / f"{stem}_{name}.csv" for name in ("comparison", "weights", "frontier", "walk_forward")}
    paths["report"] = directory / f"{stem}_report.json"
    weights = pd.concat({name: ev["holdings"] for name, ev in result.evaluations.items()},
                        names=["strategy", "symbol"]).reset_index()
    allocation_rows = []
    for name, alloc in result.allocations.items():
        table = alloc["new_money"].table()
        table["strategy"] = name
        allocation_rows.append(table.reset_index(names="symbol"))
    _write(paths["comparison"], result.comparison.to_csv(index_label="strategy"))
    _write(paths["weights"], weights.to_csv(index=False))
    _write(paths["frontier"], result.frontier.to_csv(index=False))
    wf = result.walk_forward
    _write(paths["walk_forward"], "" if wf.net is None else wf.net.to_csv(index_label="date"))
    report = {
        "dataset_id": result.dataset_id, "analysis_id": result.analysis_id, "run_ids": result.run_ids,
        "engine_version": ENGINE_VERSION, "comparison_label": COMPARISON_LABEL,
        "label": "Illustrative, in-sample portfolio construction; not investment advice or a recommendation.",
        "source_name": result.source_name, "verification_status": result.verification_status,
        "price_basis": result.price_basis, "as_of_date": result.as_of_date,
        "valid_return_observations": result.observations, "portfolio_value_inr": result.portfolio_value_inr,
        "investment_amount_inr": result.investment_inr,
        "expected_returns": {m: {"annual": e.annual.to_dict(), "standard_error": e.standard_error.to_dict(),
                                 "shrinkage_intensity": e.shrinkage_intensity}
                             for m, e in result.expected_returns.items()},
        "covariance_diagnostics": result.covariance_diagnostics,
        "strategies": {name: {"status": r.status, "message": r.message, "diagnostics": r.diagnostics,
                              "feasibility": r.feasibility,
                              "weights": None if r.weights is None else r.weights.to_dict()}
                       for name, r in result.results.items()},
        "comparison": result.comparison.reset_index(names="strategy").to_dict(orient="records"),
        "integer_allocations": pd.concat(allocation_rows).to_dict(orient="records") if allocation_rows else [],
        "allocation_summary": {name: {kind: {"budget_inr": a.budget_inr, "invested_inr": a.invested_inr,
                                             "transaction_cost_inr": a.transaction_cost_inr,
                                             "residual_cash_inr": a.residual_cash_inr}
                                      for kind, a in alloc.items()} for name, alloc in result.allocations.items()},
        "frontier": result.frontier.to_dict(orient="records"),
        "walk_forward": {"status": wf.status, "reason": wf.reason, "rebalance_dates": wf.rebalance_dates,
                         "failures": wf.failures,
                         "summary": None if wf.summary is None else wf.summary.to_dict(orient="index")},
        "tradeoffs": result.tradeoffs, "settings": result.settings,
        "limitations": [
            "Unverified Yahoo price data with an unconfirmed price basis (dataset 1); price returns, not total returns.",
            "Expected returns are noisy in-sample estimates; optimized weights are illustrative, not forecasts.",
            "Five-stock, four-sector universe: no broad-market diversification claim.",
            "Transaction-cost rates are assumptions to verify; taxes on gains are not modelled.",
            "Walk-forward uses a fixed universe chosen today (survivorship/selection bias).",
        ],
        "local_outputs": {k: str(v) for k, v in paths.items()},
    }
    _write(paths["report"], json.dumps(jsonable(report), indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Illustrative portfolio optimization for one saved dataset.")
    parser.add_argument("--dataset-id", required=True, type=int)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Optimization config JSON")
    parser.add_argument("--investment-inr", type=float, default=None, help="Cash amount for integer allocation")
    parser.add_argument("--no-save", action="store_true", help="Compute and report without a DB write")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    try:
        result = analyze_saved_dataset_optimization(args.dataset_id, db_path=args.database, config_path=args.config,
                                                    investment_inr=args.investment_inr, save=not args.no_save)
        paths = save_optimization_report(result, args.report_dir)
    except (ValueError, OptimizationError, DataQualityError, PortfolioReturnError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Optimization failed: {exc}\n")
    print(f"Dataset {result.dataset_id}; analysis run: {result.analysis_id}; engine {ENGINE_VERSION}")
    print(f"Source: {result.source_name}; verification: {result.verification_status}; price basis: "
          f"{result.price_basis}. Illustrative and in-sample; not a recommendation.")
    for name, row in result.comparison.iterrows():
        if pd.isna(row.get("ex_ante_volatility")):
            print(f"  {name:30s} {row['status']:11s} {row['message'][:90]}")
            continue
        print(f"  {name:30s} {row['status']:11s} E[r] {row['ex_ante_expected_return']:7.2%}  vol "
              f"{row['ex_ante_volatility']:6.2%}  eff.N {row['effective_holdings']:.2f}  "
              f"turnover {row['turnover_gross_vs_current']:6.1%}")
    print(f"Walk-forward: {result.walk_forward.status}; {result.walk_forward.reason or ''}")
    for note in result.tradeoffs:
        print(f"  - {note}")
    print(f"Report: {paths['report']}")


if __name__ == "__main__":
    main()
