"""Validate one real NSE candle dataset and load it into the local SQLite store.

This command is deliberately offline. It accepts either the independently
labelled source CSV from Phase 2 or an existing, integrity-checked Angel One
candle cache. It does not authenticate, download data, or modify the demo
portfolio.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from portfoliopulse.angelone.cache import CandleCacheError, load_configured_candles
from portfoliopulse.angelone.instrument_master import InstrumentMasterError, load_config
from portfoliopulse.config import DB_PATH, PROJECT_ROOT, REAL_PORTFOLIO_NAME, TRADING_DAYS_PER_YEAR
from portfoliopulse.data_quality import DataQualityError, validate_daily_candles
from portfoliopulse.database import (
    get_dataset_info,
    import_candle_dataset,
    initialize,
    save_analysis_metadata,
    upsert_configured_portfolio,
)
from portfoliopulse.sourced_csv import SourcedCsvError, import_sourced_csv


CONFIG_PATH = PROJECT_ROOT / "config" / "instruments.json"
ANGEL_CACHE_PATH = PROJECT_ROOT / "data" / "processed" / "angel_candles.csv"
REPORT_DIR = PROJECT_ROOT / "data" / "processed"


@dataclass(frozen=True)
class IngestionResult:
    portfolio_id: int
    dataset_id: int
    analysis_id: int
    dataset_info: dict[str, Any]
    quality_summary: dict[str, Any]
    report_path: Path


def _angel_metadata(saved: dict[str, Any]) -> dict[str, Any]:
    """Adapt the cache's exact identity without claiming a new live fetch."""
    return {
        "source_name": saved["source"],
        "source_url": "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData",
        "retrieved_at_utc": saved["retrieved_at_utc_last"],
        "price_basis": saved["price_basis"],
        "provenance_kind": "angel_one_smartapi",
        "provenance_status": "broker_response_validated",
        "input_csv_sha256": saved["csv_sha256"],
        "start": saved["start"],
        "end_exclusive": saved["end_exclusive"],
    }


def ingest_phase3(
    source_kind: str,
    start: str,
    end_exclusive: str,
    *,
    input_csv: Path | None = None,
    input_metadata: Path | None = None,
    db_path: Path = DB_PATH,
    report_dir: Path = REPORT_DIR,
    processed_dir: Path | None = None,
) -> IngestionResult:
    """Import a validated dataset; never combine different source identities."""
    configured = load_config(CONFIG_PATH)
    symbols = [entry.symbol for entry in configured]
    if source_kind == "sourced":
        if input_csv is None:
            raise ValueError("--input is required for a sourced CSV import.")
        loaded = import_sourced_csv(
            Path(input_csv), configured, start, end_exclusive,
            metadata_path=input_metadata, output_dir=processed_dir,
        )
        candles = loaded.candles
        metadata = loaded.metadata
    elif source_kind == "angel":
        if input_metadata is not None:
            raise ValueError("--metadata is only used with --source sourced.")
        cached = load_configured_candles(
            Path(input_csv) if input_csv is not None else ANGEL_CACHE_PATH,
            configured, start, end_exclusive,
        )
        candles = cached.candles
        metadata = _angel_metadata(cached.metadata)
    else:
        raise ValueError("--source must be sourced or angel.")

    quality = validate_daily_candles(
        candles, symbols, start=start, end_exclusive=end_exclusive,
        expected_price_basis=metadata["price_basis"],
    )
    if quality.source != metadata["source_name"]:
        raise DataQualityError("Candle source does not match the dataset provenance.")
    summary = quality.to_summary()
    summary["input_rows_excluded_before_quality_check"] = int(
        metadata.get("input_rows_excluded_outside_window", 0)
    )
    dataset_metadata = {
        **metadata,
        "portfolio_model": "buy_and_hold_fixed_quantities",
        "quality_summary": summary,
    }

    initialize(db_path)
    portfolio_id = upsert_configured_portfolio(
        configured, portfolio_name=REAL_PORTFOLIO_NAME, path=db_path,
    )
    dataset_id = import_candle_dataset(
        quality.candles, metadata=dataset_metadata,
        portfolio_name=REAL_PORTFOLIO_NAME, path=db_path,
    )
    analysis_id = save_analysis_metadata(
        dataset_id, portfolio_name=REAL_PORTFOLIO_NAME,
        window_start=quality.aligned_prices.index[0].strftime("%Y-%m-%d"),
        window_end=quality.aligned_prices.index[-1].strftime("%Y-%m-%d"),
        settings={
            "stage": "phase3_input_validation",
            "portfolio_model": "buy_and_hold_fixed_quantities",
            "benchmark_symbol": next(entry.symbol for entry in configured if entry.is_benchmark),
            "return_basis": "close_to_close_price_return",
            "price_basis": quality.price_basis,
            "annualization_observations": TRADING_DAYS_PER_YEAR,
            "missing_date_policy": "exclude_without_fill",
        },
        quality=summary,
        path=db_path,
    )
    info = get_dataset_info(dataset_id, path=db_path)
    report_path = Path(report_dir) / f"phase3_dataset_{dataset_id}_{info['dataset_key'][:12]}_quality.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "portfolio_name": REAL_PORTFOLIO_NAME,
        "portfolio_id": portfolio_id,
        "dataset_id": dataset_id,
        "analysis_id": analysis_id,
        "source_name": metadata["source_name"],
        "provenance_kind": metadata["provenance_kind"],
        "provenance_status": metadata.get("provenance_status"),
        "price_basis": metadata["price_basis"],
        "input_csv_sha256": metadata.get("input_csv_sha256"),
        "quality": summary,
    }
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, report_path)
    return IngestionResult(portfolio_id, dataset_id, analysis_id, info, summary, report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and store Phase 2 NSE candles in SQLite.")
    parser.add_argument("--source", required=True, choices=("sourced", "angel"))
    parser.add_argument("--input", type=Path, help="Source CSV, or Angel candle cache (default: local Angel cache)")
    parser.add_argument("--metadata", type=Path, help="Provenance sidecar for --source sourced")
    parser.add_argument("--start", required=True, help="Inclusive YYYY-MM-DD date")
    parser.add_argument("--end", required=True, help="Exclusive YYYY-MM-DD date")
    parser.add_argument("--database", type=Path, default=DB_PATH, help="SQLite file (default: local project database)")
    args = parser.parse_args()
    try:
        result = ingest_phase3(
            args.source, args.start, args.end,
            input_csv=args.input, input_metadata=args.metadata, db_path=args.database,
        )
    except (InstrumentMasterError, SourcedCsvError, CandleCacheError, DataQualityError,
            ValueError, KeyError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Phase 3 import failed: {exc}\n")
    quality = result.quality_summary
    print(f"Portfolio: {REAL_PORTFOLIO_NAME}; dataset id: {result.dataset_id}; validation run: {result.analysis_id}")
    print(f"Source: {quality['source']}; price basis: {quality['price_basis']}")
    print(
        f"Candles: {quality['valid_candle_rows']}; union dates: {quality['union_dates']}; "
        f"common price dates: {quality['common_price_dates']}; "
        f"valid daily returns: {quality['valid_return_dates']}"
    )
    print(
        f"Excluded price dates: {quality['excluded_price_date_count']}; "
        f"excluded return dates: {quality['excluded_return_date_count']}; "
        f"input rows outside window: {quality['input_rows_excluded_before_quality_check']}"
    )
    print(f"Full quality report: {result.report_path}")


if __name__ == "__main__":
    main()
