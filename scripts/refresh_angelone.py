"""Refresh the configured NSE daily candles or reload an exact Angel One cache."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from portfoliopulse.angelone.cache import (
    CandleCacheError,
    cache_identity,
    load_configured_candles,
    save_candles,
)
from portfoliopulse.angelone.instrument_master import (
    InstrumentMasterError,
    fetch_master,
    load_config,
    resolve,
)
from portfoliopulse.config import PROJECT_ROOT


MASTER_PATH = PROJECT_ROOT / "data" / "raw" / "instrument_master.json"
CANDLE_PATH = PROJECT_ROOT / "data" / "processed" / "angel_candles.csv"
RAW_CANDLE_DIR = PROJECT_ROOT / "data" / "raw" / "angelone"


def _save_raw_candle_batches(batches: list[dict], start: str, end: str) -> Path:
    """Preserve candle request/data only; never write an authentication response."""
    RAW_CANDLE_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_CANDLE_DIR / f"candles_{start}_{end}_exclusive.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(batches, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def refresh(start: str, end: str, *, offline: bool = False) -> tuple[int, bool, bool]:
    """Return (saved rows, used CSV fallback, used stored token mapping)."""
    configured = load_config(PROJECT_ROOT / "config" / "instruments.json")
    if offline:
        cached = load_configured_candles(CANDLE_PATH, configured, start, end)
        return len(cached.candles), True, True

    snapshot = fetch_master(MASTER_PATH)
    # A stored master is useful for exact replay, but its tokens may have
    # changed. Never request fresh candles with a stale token mapping.
    if snapshot.from_cache:
        cached = load_configured_candles(CANDLE_PATH, configured, start, end)
        return len(cached.candles), True, True
    instruments = resolve(configured, snapshot.entries)
    identity = cache_identity(instruments, start, end)

    # Keep the SDK and local credentials out of the offline path entirely.
    from dotenv import load_dotenv
    from portfoliopulse.angelone.client import AngelOneDataError, authenticate_from_environment, fetch_daily_candles

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    raw_batches: list[dict] = []
    try:
        client = authenticate_from_environment()
        candles = fetch_daily_candles(client, instruments, start, end, raw_batches=raw_batches)
        save_candles(candles, CANDLE_PATH, identity)
    except (AngelOneDataError, CandleCacheError, OSError) as fetch_error:
        try:
            cached = load_configured_candles(CANDLE_PATH, configured, start, end)
        except CandleCacheError as cache_error:
            raise RuntimeError(
                f"Angel One refresh unavailable ({fetch_error}); exact local CSV fallback unavailable ({cache_error})."
            ) from fetch_error
        return len(cached.candles), True, True
    try:
        _save_raw_candle_batches(raw_batches, start, end)
    except OSError:
        raise RuntimeError("Fresh Angel One candles were saved, but the raw candle batches could not be preserved.") from None
    return len(candles), False, False


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh source-labelled Angel One NSE daily OHLCV candles.")
    parser.add_argument("--start", required=True, help="Inclusive date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Exclusive date, YYYY-MM-DD")
    parser.add_argument("--offline", action="store_true", help="Replay only the exact candle CSV and its recorded token mapping")
    args = parser.parse_args()
    try:
        rows, fallback, stored_mapping = refresh(args.start, args.end, offline=args.offline)
    except (InstrumentMasterError, CandleCacheError, RuntimeError) as exc:
        parser.exit(1, f"Data refresh failed: {exc}\n")
    print(f"Source: angel_one_smartapi; unadjusted ONE_DAY OHLCV; candle rows: {rows}")
    print(f"CSV fallback: {fallback}; stored token mapping used: {stored_mapping}")
    print(f"Local candle CSV: {CANDLE_PATH}")


if __name__ == "__main__":
    main()
