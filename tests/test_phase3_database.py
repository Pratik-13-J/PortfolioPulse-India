"""Focused SQLite checks for source-labelled NSE candles and run lineage."""

from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from portfoliopulse.angelone.instrument_master import load_config
from portfoliopulse.config import PROJECT_ROOT, REAL_PORTFOLIO_NAME
from portfoliopulse.database import (
    _sha256_json,
    get_candle_dataset, get_dataset_info, import_candle_dataset, initialize,
    save_analysis_metadata, save_analysis_results, session, upsert_configured_portfolio,
)


ENTRIES = load_config(PROJECT_ROOT / "config" / "instruments.json")


def sample_candles(*, source: str = "sample NSE CSV") -> pd.DataFrame:
    rows = []
    for day, lift in (("2026-09-01", 0), ("2026-09-02", 1)):
        for position, item in enumerate(ENTRIES):
            base = 100.0 + position * 10 + lift
            rows.append({
                "symbol": item.symbol, "trading_symbol": item.trading_symbol,
                "exchange": item.exchange, "date": day, "open": base,
                "high": base + 2, "low": base - 2, "close": base + 1,
                "volume": 0 if item.is_benchmark else 1000 + position,
                "source": source, "price_basis": "unadjusted_ohlcv",
                "provenance_kind": "user_supplied_csv", "interval": "ONE_DAY",
                "retrieved_at_utc": "2026-09-03T00:00:00+00:00",
            })
    return pd.DataFrame(rows)


def sample_metadata(*, source: str = "sample NSE CSV") -> dict:
    return {
        "source_name": source, "source_url": "https://example.org/nse-candles",
        "provenance_kind": "user_supplied_csv",
        "provenance_status": "user_declared_unverified",
        "retrieved_at_utc": "2026-09-03T00:00:00+00:00",
        "price_basis": "unadjusted_ohlcv", "interval": "ONE_DAY",
        "start": "2026-09-01", "end_exclusive": "2026-09-03",
        "valid_candle_rows": 2 * len(ENTRIES),
        "aligned_price_dates": 2, "valid_daily_return_dates": 1,
        "incomplete_union_dates": 0,
        "processed_at_utc": "2026-09-03T01:00:00+00:00",
        "quality_summary": {"excluded_dates": 0, "generated_at_utc": "2026-09-03T01:00:00+00:00"},
        "portfolio_model": "buy_and_hold_fixed_quantities",
    }


class Phase3DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "phase3.db"
        initialize(self.path)
        self.portfolio_id = upsert_configured_portfolio(ENTRIES, path=self.path)

    def test_portfolio_and_listing_upsert_is_idempotent(self) -> None:
        self.assertEqual(upsert_configured_portfolio(ENTRIES, path=self.path), self.portfolio_id)
        with session(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM instrument_listings").fetchone()[0], len(ENTRIES))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM holdings WHERE portfolio_id=?", (self.portfolio_id,)).fetchone()[0], len(ENTRIES)-1)
            benchmark = con.execute(
                """SELECT i.symbol FROM portfolio_benchmarks b JOIN instruments i USING(instrument_id)
                   WHERE b.portfolio_id=?""", (self.portfolio_id,),
            ).fetchone()[0]
            self.assertEqual(benchmark, "NIFTY 50")
            self.assertAlmostEqual(con.execute("SELECT SUM(target_weight) FROM holdings WHERE portfolio_id=?", (self.portfolio_id,)).fetchone()[0], 1)

    def test_repeat_import_preserves_provenance_and_isolation(self) -> None:
        candles = sample_candles()
        metadata = sample_metadata()
        dataset_id = import_candle_dataset(candles, metadata=metadata, path=self.path)
        rerun = dict(metadata, processed_at_utc="2026-09-04T01:00:00+00:00",
                     quality_summary={"excluded_dates": 0, "generated_at_utc": "2026-09-04T01:00:00+00:00"})
        self.assertEqual(import_candle_dataset(candles, metadata=rerun, path=self.path), dataset_id)
        info = get_dataset_info(dataset_id, path=self.path)
        self.assertEqual(info["portfolio_name"], REAL_PORTFOLIO_NAME)
        self.assertEqual(info["source_name"], "sample NSE CSV")
        self.assertEqual(info["price_basis"], "unadjusted_ohlcv")
        self.assertEqual(info["provenance_status"], "user_declared_unverified")
        self.assertEqual(info["candle_count"], len(candles))
        self.assertEqual(info["metadata"]["portfolio_model"], "buy_and_hold_fixed_quantities")
        stored = get_candle_dataset(dataset_id, path=self.path)
        self.assertEqual(len(stored), len(candles))
        self.assertEqual(set(stored["source"]), {"sample NSE CSV"})
        self.assertEqual(set(stored["retrieved_at_utc"]), {"2026-09-03T00:00:00+00:00"})
        self.assertEqual(set(stored["volume"].loc[stored["symbol"] == "NIFTY 50"]), {0.0})
        other = sample_candles(source="other NSE CSV")
        other_id = import_candle_dataset(other, metadata=sample_metadata(source="other NSE CSV"), path=self.path)
        self.assertNotEqual(other_id, dataset_id)
        with session(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_candles").fetchone()[0], 2 * len(candles))

    def test_changed_portfolio_inputs_create_distinct_snapshot(self) -> None:
        candles, metadata = sample_candles(), sample_metadata()
        original_id = import_candle_dataset(candles, metadata=metadata, path=self.path)
        changed = tuple(
            replace(item, quantity=21, target_weight=0.25) if item.symbol == "RELIANCE" else
            replace(item, target_weight=0.15) if item.symbol == "HDFCBANK" else item
            for item in ENTRIES
        )
        upsert_configured_portfolio(changed, path=self.path)
        changed_id = import_candle_dataset(candles, metadata=metadata, path=self.path)
        self.assertNotEqual(changed_id, original_id)
        original = {item["symbol"]: item for item in get_dataset_info(original_id, path=self.path)["portfolio_snapshot"]}
        current = {item["symbol"]: item for item in get_dataset_info(changed_id, path=self.path)["portfolio_snapshot"]}
        self.assertEqual(original["RELIANCE"]["quantity"], 20.0)
        self.assertEqual(original["RELIANCE"]["target_weight"], 0.2)
        self.assertEqual(current["RELIANCE"]["quantity"], 21.0)
        self.assertEqual(current["RELIANCE"]["target_weight"], 0.25)
        self.assertEqual(original["NIFTY 50"]["is_benchmark"], 1)

    def test_legacy_dataset_backfills_snapshot_and_identity(self) -> None:
        candles, metadata = sample_candles(), sample_metadata()
        dataset_id = import_candle_dataset(candles, metadata=metadata, path=self.path)
        # Model the previous P3 schema's identity and absent snapshot. Its
        # daily_candles table had no per-candle retrieval field.
        old_rows = [
            (row.symbol, row.trading_symbol, row.exchange, row.date,
             float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume),
             None, None)
            for row in candles.itertuples(index=False)
        ]
        old_rows.sort(key=lambda row: (row[3], row[0]))
        old_hash = _sha256_json(old_rows)
        identity = {"source_name": metadata["source_name"], "source_url": metadata["source_url"],
                    "provenance_kind": metadata["provenance_kind"],
                    "provenance_status": metadata["provenance_status"],
                    "retrieved_at_utc": metadata["retrieved_at_utc"],
                    "price_basis": metadata["price_basis"], "interval": metadata["interval"],
                    "start": metadata["start"], "end_exclusive": metadata["end_exclusive"]}
        old_key = _sha256_json({"portfolio": REAL_PORTFOLIO_NAME, "metadata": identity,
                                "candles_sha256": old_hash})
        with session(self.path) as con:
            con.execute("DELETE FROM dataset_holdings WHERE dataset_id=?", (dataset_id,))
            con.execute("UPDATE candle_datasets SET dataset_key=?,candles_sha256=? WHERE dataset_id=?",
                        (old_key, old_hash, dataset_id))
        self.assertEqual(import_candle_dataset(candles, metadata=metadata, path=self.path), dataset_id)
        info = get_dataset_info(dataset_id, path=self.path)
        self.assertEqual(len(info["portfolio_snapshot"]), len(ENTRIES))
        self.assertEqual(set(get_candle_dataset(dataset_id, path=self.path)["retrieved_at_utc"]),
                         {metadata["retrieved_at_utc"]})

    def test_invalid_batches_do_not_mutate_database(self) -> None:
        good = sample_candles()
        bad_batches = []
        duplicate = pd.concat([good, good.iloc[[0]]], ignore_index=True)
        bad_batches.append(duplicate)
        inverted = good.copy()
        inverted.loc[0, "high"] = inverted.loc[0, "low"] - 1
        bad_batches.append(inverted)
        missing = good.loc[good["symbol"] != "ITC"].copy()
        bad_batches.append(missing)
        contaminated = good.copy()
        contaminated.loc[0, "source"] = "another source"
        bad_batches.append(contaminated)
        for bad in bad_batches:
            with self.subTest(kind=len(bad)), self.assertRaises(ValueError):
                import_candle_dataset(bad, metadata=sample_metadata(), path=self.path)
        with session(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_candles").fetchone()[0], 0)

    def test_sql_failure_rolls_back_dataset_and_partial_candles(self) -> None:
        with session(self.path) as con:
            con.execute(
                """CREATE TRIGGER fail_second_day BEFORE INSERT ON daily_candles
                   WHEN NEW.observation_date='2026-09-02'
                   BEGIN SELECT RAISE(ABORT, 'simulated storage error'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            import_candle_dataset(sample_candles(), metadata=sample_metadata(), path=self.path)
        with session(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_candles").fetchone()[0], 0)

    def test_angel_candles_keep_token_and_ist_timestamp(self) -> None:
        candles = sample_candles(source="angel_one_smartapi")
        candles["provenance_kind"] = "angel_one_smartapi"
        candles["retrieved_at_utc"] = "2026-09-02T12:00:00+00:00"
        candles["token"] = candles["symbol"].map({item.symbol: str(100 + index) for index, item in enumerate(ENTRIES)})
        candles["timestamp_ist"] = candles["date"].map(lambda day: f"{day}T15:30:00+05:30")
        metadata = sample_metadata(source="angel_one_smartapi")
        metadata["provenance_kind"] = "angel_one_smartapi"
        metadata["provenance_status"] = "broker_response_validated"
        dataset_id = import_candle_dataset(candles, metadata=metadata, path=self.path)
        stored = get_candle_dataset(dataset_id, path=self.path)
        self.assertEqual(set(stored["token"]), {str(100 + index) for index in range(len(ENTRIES))})
        self.assertTrue(stored["timestamp_ist"].str.endswith("+05:30").all())
        no_tokens = candles.drop(columns="token")
        with self.assertRaises(ValueError):
            import_candle_dataset(no_tokens, metadata=metadata, path=self.path)

    def test_analysis_settings_and_results_link_to_dataset(self) -> None:
        dataset_id = import_candle_dataset(sample_candles(), metadata=sample_metadata(), path=self.path)
        run_id = save_analysis_metadata(
            dataset_id, window_start="2026-09-01", window_end="2026-09-02",
            settings={"model": "buy_and_hold_fixed_quantities", "risk_free_rate": 0.06},
            quality={"excluded_dates": []}, path=self.path,
        )
        self.assertEqual(save_analysis_metadata(
            dataset_id, window_start="2026-09-01", window_end="2026-09-02",
            settings={"risk_free_rate": 0.06, "model": "buy_and_hold_fixed_quantities"},
            quality={"excluded_dates": []}, path=self.path,
        ), run_id)
        save_analysis_results(run_id, {"volatility": (0.15, "annual_ratio")}, path=self.path)
        with self.assertRaises(ValueError):
            save_analysis_results(run_id, {"volatility": (float("nan"), "annual_ratio")}, path=self.path)
        with session(self.path) as con:
            row = con.execute(
                """SELECT a.dataset_id, r.metric_name, r.value, r.unit
                   FROM analysis_metadata a JOIN analysis_results r USING(analysis_id)
                   WHERE a.analysis_id=?""", (run_id,),
            ).fetchone()
            self.assertEqual((row["dataset_id"], row["metric_name"], row["unit"]),
                             (dataset_id, "volatility", "annual_ratio"))
            self.assertAlmostEqual(row["value"], 0.15)


if __name__ == "__main__":
    unittest.main()
