"""NSE corporate-action workflow for a saved dataset (convert -> import -> evidence -> version).

    convert         raw NSE CSV -> project CSV + sidecar (refuses while rows need review)   [read-only DB]
    import          project CSV -> reference_datasets / corporate_actions (backup first)   [writes reference tables]
    evidence        classify each action against a dataset's closes (JSON report)          [read-only]
    build-version   create a NEW split/bonus-adjusted dataset from decisions (backup first)
                    [writes a new dataset; the base dataset is never modified]

Nothing is downloaded; the NSE file must be supplied by the user.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

from portfoliopulse.config import DB_PATH
from portfoliopulse.database import get_candle_dataset, get_dataset_info, session
from portfoliopulse.migrations import backup_database
from portfoliopulse.nse_corporate_actions import convert_nse_csv, write_project_files
from portfoliopulse.price_basis_versioning import EventDecision, build_adjusted_version, classify_events
from portfoliopulse.reference_data import ReferenceDataError, import_corporate_actions
from portfoliopulse.stress_analysis import jsonable


def _convert(args: argparse.Namespace) -> None:
    info = get_dataset_info(args.dataset_id, path=args.database, read_only=True)
    symbols = sorted(r["symbol"] for r in info["portfolio_snapshot"] if not r["is_benchmark"])
    raw = Path(args.raw).read_bytes()
    result = convert_nse_csv(raw, symbols=symbols, window=(info["window_start"], info["window_end_exclusive"]))
    print(f"Raw rows {result.raw_rows}; actions {len(result.actions)}; review {len(result.review)}; "
          f"ignored {len(result.ignored)} (symbols {', '.join(symbols)}; window {info['window_start']} to "
          f"{info['window_end_exclusive']} exclusive)")
    print(result.actions.to_string(index=False) if not result.actions.empty else "No actions parsed.")
    if not result.review.empty:
        print("\nRows needing review (nothing written):")
        print(result.review.to_string(index=False))
        raise SystemExit(2)
    csv_path, meta = write_project_files(result, Path(args.out), source_url=args.source_url,
                                         retrieved_at_utc=args.retrieved_at, raw_file_name=Path(args.raw).name)
    print(f"Wrote {csv_path} and {meta}")


def _import(args: argparse.Namespace) -> None:
    print(f"Backup: {backup_database(args.database, label='pre_corporate_action_import')}")
    result = import_corporate_actions(Path(args.csv), db_path=args.database)
    print(f"reference_dataset_id {result.reference_dataset_id}; rows {result.row_count}; "
          f"{'created' if result.created else 'already imported'}; sha256 {result.file_sha256}")


def _evidence(args: argparse.Namespace) -> None:
    info = get_dataset_info(args.dataset_id, path=args.database, read_only=True)
    candles = get_candle_dataset(args.dataset_id, path=args.database, read_only=True)
    grid = candles.pivot(index="date", columns="symbol", values="close")
    benchmark = next(r["symbol"] for r in info["portfolio_snapshot"] if r["is_benchmark"])
    with session(args.database, read_only=True) as con:
        actions = pd.read_sql_query(
            "SELECT symbol, ex_date, action_type, ratio_new, ratio_old, cash_amount_inr FROM corporate_actions "
            "WHERE reference_dataset_id=? ORDER BY symbol, ex_date", con, params=(args.reference_id,))
    report = classify_events(grid, benchmark, actions, tolerance=args.tolerance)
    print(f"Dataset {args.dataset_id} ({info['source_name']}; declared basis {info['price_basis']}) vs "
          f"reference {args.reference_id}")
    shown = [c for c in ("symbol", "ex_date", "action_type", "price_factor", "stock_return", "benchmark_return",
                         "expected_if_unadjusted", "classification") if c in report]
    print(report[shown].to_string(index=False))
    if args.report:
        Path(args.report).write_text(json.dumps(jsonable(report.to_dict(orient="records")), indent=2) + "\n",
                                     encoding="utf-8")
        print(f"Report: {args.report}")


def _build(args: argparse.Namespace) -> None:
    rows = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    decisions = [EventDecision(r["symbol"], r["ex_date"], r["action_type"], r["decision"],
                               float(r.get("price_factor", 1.0)), dict(r.get("evidence", {}))) for r in rows]
    print(f"Backup: {backup_database(args.database, label='pre_dataset_version')}")
    new_id = build_adjusted_version(args.dataset_id, args.reference_id, decisions, db_path=args.database)
    print(f"New dataset version {new_id} (derived from dataset {args.dataset_id}; base dataset unchanged)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert")
    convert.add_argument("--dataset-id", type=int, required=True)
    convert.add_argument("--raw", required=True, help="NSE Corporate Actions CSV as downloaded")
    convert.add_argument("--out", required=True, help="Project-format CSV to write (sidecar alongside)")
    convert.add_argument("--source-url", required=True, help="HTTPS page the file was downloaded from")
    convert.add_argument("--retrieved-at", required=True, help="Download time, ISO 8601 with offset")
    imp = sub.add_parser("import")
    imp.add_argument("--csv", required=True)
    evidence = sub.add_parser("evidence")
    evidence.add_argument("--dataset-id", type=int, required=True)
    evidence.add_argument("--reference-id", type=int, required=True)
    evidence.add_argument("--tolerance", type=float, default=0.10)
    evidence.add_argument("--report")
    build = sub.add_parser("build-version")
    build.add_argument("--dataset-id", type=int, required=True)
    build.add_argument("--reference-id", type=int, required=True)
    build.add_argument("--decisions", required=True, help="JSON list of {symbol, ex_date, action_type, decision, "
                                                          "price_factor?}")
    args = parser.parse_args()
    handlers = {"convert": _convert, "import": _import, "evidence": _evidence, "build-version": _build}
    try:
        handlers[args.command](args)
    except (ValueError, ReferenceDataError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Corporate-action workflow failed: {exc}\n")


if __name__ == "__main__":
    main()
