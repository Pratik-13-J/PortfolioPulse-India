"""Compare saved provider NIFTY 50 closes with a user-supplied NSE CSV."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from portfoliopulse.config import DB_PATH, PROJECT_ROOT
from portfoliopulse.database import get_candle_dataset, get_dataset_info
from portfoliopulse.source_verification import compare_daily_closes, parse_nse_index_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", type=int, required=True)
    parser.add_argument("--nse-csv", type=Path, required=True, help="Locally saved NSE Historical Index Data CSV")
    parser.add_argument("--reference-source-url", required=True, help="Official NSE page or report URL used to obtain the CSV")
    parser.add_argument("--tolerance-points", type=float, default=0.01)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "processed")
    parser.add_argument("--database", type=Path, default=DB_PATH)
    args = parser.parse_args()

    if not args.reference_source_url.startswith("https://"):
        parser.error("--reference-source-url must be an https URL.")
    info = get_dataset_info(args.dataset_id, path=args.database)
    frame = get_candle_dataset(args.dataset_id, path=args.database)
    benchmark_symbols = {str(row["symbol"]) for row in info["portfolio_snapshot"] if row["is_benchmark"]}
    if len(benchmark_symbols) != 1:
        raise ValueError("Selected dataset must have exactly one benchmark in its saved portfolio snapshot.")
    benchmark = next(iter(benchmark_symbols))
    provider_rows = frame.loc[frame["symbol"].eq(benchmark), ["date", "close"]]
    if provider_rows.empty:
        raise ValueError(f"Selected dataset has no observations for benchmark {benchmark}.")
    provider = provider_rows.set_index("date")["close"]
    reference = parse_nse_index_csv(args.nse_csv)
    comparison, summary = compare_daily_closes(provider, reference, tolerance_points=args.tolerance_points)
    if summary["overlap_count"] == 0:
        raise ValueError("Provider and reference data have no overlapping dates; no comparison was made.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"nifty_close_comparison_dataset_{args.dataset_id}"
    csv_path = args.output_dir / f"{stem}.csv"
    json_path = args.output_dir / f"{stem}_summary.json"
    comparison.to_csv(csv_path, index_label="date", date_format="%Y-%m-%d")
    nse_bytes = args.nse_csv.read_bytes()
    summary_doc = {
        "dataset_id": args.dataset_id,
        "provider_source_name": info["source_name"],
        "provider_source_url": info["source_url"],
        "provider_price_basis": info["price_basis"],
        "benchmark_symbol": benchmark,
        "reference_source_url": args.reference_source_url,
        "reference_file": str(args.nse_csv.resolve()),
        "reference_file_sha256": hashlib.sha256(nse_bytes).hexdigest(),
        "comparison_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "comparison_window": {
            "start": comparison.index.min().date().isoformat(),
            "end": comparison.index.max().date().isoformat(),
        },
        "summary": summary,
        "interpretation": "Mechanical comparison only; differences and missing dates require source/calendar review.",
        "detail_csv": str(csv_path.resolve()),
    }
    json_path.write_text(json.dumps(summary_doc, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Detail: {csv_path}\nSummary: {json_path}")
    return 1 if summary["mismatch_count"] or summary["provider_missing_on_reference_dates"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
