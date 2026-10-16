"""Validate an independently sourced NSE OHLCV CSV without broker access."""

from __future__ import annotations

import argparse
from pathlib import Path

from portfoliopulse.angelone.instrument_master import InstrumentMasterError, load_config
from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.sourced_csv import SourcedCsvError, import_sourced_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Import source-labelled Indian daily OHLCV candles.")
    parser.add_argument("--input", type=Path, required=True, help="CSV with configured NSE symbols and daily OHLCV")
    parser.add_argument("--metadata", type=Path, help="JSON source sidecar; defaults to <input stem>.meta.json")
    parser.add_argument("--start", required=True, help="Inclusive date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Exclusive date, YYYY-MM-DD")
    args = parser.parse_args()
    try:
        configured = load_config(PROJECT_ROOT / "config" / "instruments.json")
        result = import_sourced_csv(
            args.input, configured, args.start, args.end, metadata_path=args.metadata
        )
    except (InstrumentMasterError, SourcedCsvError) as exc:
        parser.exit(1, f"CSV import failed: {exc}\n")
    metadata = result.metadata
    print(f"Source: {metadata['source_name']} ({metadata['provenance_status']})")
    print(f"Price basis: {metadata['price_basis']}; candle rows: {metadata['valid_candle_rows']}")
    print(f"Aligned price dates: {metadata['aligned_price_dates']}; valid daily returns: {metadata['valid_daily_return_dates']}")
    print(f"Incomplete dates: {metadata['incomplete_union_dates']}; outside-window rows excluded: {metadata['input_rows_excluded_outside_window']}")
    print(f"Processed output: {result.output_csv.parent}")


if __name__ == "__main__":
    main()
