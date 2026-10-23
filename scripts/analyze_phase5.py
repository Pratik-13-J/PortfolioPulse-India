"""Run one configurable Indian equity stress scenario against a saved dataset."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from portfoliopulse.config import DB_PATH
from portfoliopulse.scenario_analysis import run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a hypothetical one-period INR portfolio stress test.")
    parser.add_argument("--dataset-id", required=True, type=int)
    parser.add_argument("--type", required=True, choices=("broad_market", "banking_stress", "it_correction", "individual", "sector"))
    parser.add_argument("--name")
    parser.add_argument("--broad-shock", type=float, default=-0.10, help="Broad market shock as decimal; default -0.10")
    parser.add_argument("--stressed-shock", type=float, default=-0.20, help="Target sector shock; default -0.20")
    parser.add_argument("--residual-shock", type=float, default=-0.05, help="Other holdings in banking/IT presets; default -0.05")
    parser.add_argument("--individual", action="append", default=[], metavar="SYMBOL=SHOCK", help="Repeatable, e.g. INFY=-0.18")
    parser.add_argument("--sector", action="append", default=[], metavar="SECTOR=SHOCK", help="Repeatable, e.g. Banking=-0.20")
    parser.add_argument("--drift-threshold", type=float, default=0.05, help="Allocation drift alert threshold, default 5 percentage points")
    parser.add_argument("--single-name-limit", type=float, default=0.40)
    parser.add_argument("--hhi-limit", type=float, default=0.25)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    args = parser.parse_args()
    try:
        individual = _pairs(args.individual)
        sectors = _pairs(args.sector)
        result = run_scenario(
            args.dataset_id, args.type, name=args.name, broad_shock=args.broad_shock,
            stressed_shock=args.stressed_shock, residual_shock=args.residual_shock,
            individual_shocks=individual, sector_shocks=sectors,
            drift_threshold=args.drift_threshold, single_name_limit=args.single_name_limit,
            hhi_limit=args.hhi_limit, db_path=args.database,
        )
    except (ValueError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Phase 5 scenario failed: {exc}\n")
    print(f"Hypothetical scenario run {result.scenario_run_id}: {result.scenario_name}")
    print(f"Dataset {result.dataset_id}; Phase 4 analysis {result.analysis_id}; as of {result.as_of_date}")
    print(f"Portfolio value: INR {result.portfolio_value_inr:,.2f}")
    print(f"Estimated one-period impact: INR {result.impact_inr:,.2f} ({result.impact_pct:.2%})")
    print("Holding contributions (INR):")
    print(result.contributions[["shock", "impact_inr", "portfolio_impact_pct"]].to_string(float_format=lambda v: f"{v:,.4f}"))
    print("Alerts:")
    print(json.dumps(result.alerts, indent=2, ensure_ascii=False, allow_nan=False))
    print("These are hypothetical shocks, not forecasts or historically calibrated predictions.")


def _pairs(items: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected KEY=SHOCK, got {item!r}.")
        key, value = item.split("=", 1)
        if not key.strip() or key.strip() in result:
            raise ValueError("Scenario keys must be non-empty and unique.")
        result[key.strip()] = float(value)
    return result


if __name__ == "__main__":
    main()
