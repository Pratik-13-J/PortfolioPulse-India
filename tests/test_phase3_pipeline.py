"""End-to-end offline check for the Phase 3 validation and SQL boundary."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from portfoliopulse.angelone.instrument_master import load_config
from portfoliopulse.bootstrap import ensure_demo_data
from portfoliopulse.config import PORTFOLIO_NAME, PROJECT_ROOT, REAL_PORTFOLIO_NAME
from portfoliopulse.database import get_candle_dataset, get_dataset_info, session
from portfoliopulse.sourced_csv import SourcedCsvError
from scripts.ingest_phase3 import ingest_phase3


CONFIG = load_config(PROJECT_ROOT / "config" / "instruments.json")
DATES = ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07")


def _input_csv(directory: Path, *, invalid: bool = False) -> Path:
    rows = []
    for entry in CONFIG:
        for index, day in enumerate(DATES):
            if entry.symbol == "ITC" and day == "2026-01-06":
                continue
            close = 100.0 + index * 5
            rows.append({
                "symbol": entry.symbol,
                "trading_symbol": entry.trading_symbol,
                "exchange": entry.exchange,
                "date": day,
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 0 if entry.is_benchmark else 1000,
            })
    if invalid:
        rows[-1]["close"] = -1
    path = directory / "fixture_candles.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    path.with_suffix(".meta.json").write_text(json.dumps({
        "source_name": "Test-only Indian market fixture",
        "source_url": "https://example.org/market-data",
        "retrieved_at_utc": "2026-10-16T08:00:00+00:00",
        "price_basis": "unadjusted_ohlcv",
    }), encoding="utf-8")
    return path


class Phase3PipelineTests(unittest.TestCase):
    def test_real_portfolio_import_is_idempotent_and_keeps_demo_isolated(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as input_dir, TemporaryDirectory(dir=processed) as output_dir:
            db_path = Path(input_dir) / "portfolio.db"
            demo_rows = ensure_demo_data(db_path)
            input_path = _input_csv(Path(input_dir))
            options = {
                "input_csv": input_path,
                "db_path": db_path,
                "report_dir": Path(output_dir),
                "processed_dir": Path(output_dir),
            }
            first = ingest_phase3("sourced", "2026-01-02", "2026-01-08", **options)
            repeated = ingest_phase3("sourced", "2026-01-02", "2026-01-08", **options)
            self.assertEqual((first.dataset_id, first.analysis_id),
                             (repeated.dataset_id, repeated.analysis_id))
            self.assertEqual(first.quality_summary["valid_candle_rows"], 23)
            self.assertEqual(first.quality_summary["common_price_dates"], 3)
            self.assertEqual(first.quality_summary["valid_return_dates"], 1)
            self.assertEqual(first.quality_summary["excluded_price_dates"][0]["date"], "2026-01-06")
            self.assertEqual(first.quality_summary["excluded_return_date_count"], 3)
            self.assertEqual(len(get_candle_dataset(first.dataset_id, path=db_path)), 23)
            stored = get_dataset_info(first.dataset_id, path=db_path)
            self.assertEqual(stored["source_name"], "Test-only Indian market fixture")
            self.assertEqual(stored["price_basis"], "unadjusted_ohlcv")
            self.assertEqual(stored["metadata"]["quality_summary"]["common_price_dates"], 3)
            snapshot = {row["symbol"]: row for row in stored["portfolio_snapshot"]}
            self.assertEqual(set(snapshot), {entry.symbol for entry in CONFIG})
            self.assertEqual(snapshot["RELIANCE"]["quantity"], 20.0)
            self.assertEqual(snapshot["NIFTY 50"]["is_benchmark"], 1)
            self.assertEqual(
                set(get_candle_dataset(first.dataset_id, path=db_path)["retrieved_at_utc"]),
                {"2026-10-16T08:00:00+00:00"},
            )
            self.assertTrue(first.report_path.is_file())
            with session(db_path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_candles").fetchone()[0], 23)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_metadata").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0], demo_rows)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM portfolios WHERE name=?", (PORTFOLIO_NAME,)).fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM portfolios WHERE name=?", (REAL_PORTFOLIO_NAME,)).fetchone()[0], 1)

    def test_invalid_source_file_never_creates_a_dataset(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as input_dir, TemporaryDirectory(dir=processed) as output_dir:
            db_path = Path(input_dir) / "portfolio.db"
            input_path = _input_csv(Path(input_dir), invalid=True)
            with self.assertRaises(SourcedCsvError):
                ingest_phase3(
                    "sourced", "2026-01-02", "2026-01-08",
                    input_csv=input_path, db_path=db_path,
                    report_dir=Path(output_dir), processed_dir=Path(output_dir),
                )
            self.assertFalse(db_path.exists())
            self.assertEqual(list(Path(output_dir).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
