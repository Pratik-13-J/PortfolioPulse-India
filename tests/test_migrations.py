"""Conversation 2 / Phase 1: versioned, idempotent, backup-first migrations."""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, initialize, list_provenance_events, record_provenance_note,
    relabel_price_basis, session, set_verification_status,
)
from portfoliopulse.migrations import (
    LATEST_VERSION, MigrationError, derive_verification_status, pending_migrations, run_migrations,
    schema_version,
)
from portfoliopulse.portfolio_analysis import compute_phase4_analysis
from tests.legacy_fixtures import build_legacy_db

TABLES_V3 = {"schema_version", "reference_datasets", "corporate_actions", "dataset_provenance_events",
             "benchmark_constituents", "security_classifications"}


def _dump(path: Path, table: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as con:
        return sorted(con.execute(f"SELECT * FROM {table}").fetchall())


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as con:
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _insert_corporate_action_reference(db: Path, reference_id: int = 5) -> None:
    with session(db) as con:
        con.execute(
            """INSERT INTO reference_datasets(reference_dataset_id,kind,source_name,source_url,provenance_label,
                   verification_status,file_sha256,retrieved_at_utc,row_count,metadata_json)
               VALUES(?,'corporate_actions','NSE corporate actions','https://www.nseindia.com/x',
                   'nse_corporate_actions','user_declared_unverified',?,'2026-01-08T00:00:00+00:00',1,'{}')""",
            (reference_id, "a" * 64),
        )


class FreshDatabaseTests(unittest.TestCase):
    def test_fresh_database_reaches_latest_version_without_backup_and_is_idempotent(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "fresh.db"
            initialize(db)
            self.assertEqual(schema_version(db), LATEST_VERSION)
            self.assertTrue(TABLES_V3 <= _tables(db))
            self.assertFalse((Path(temp) / "backups").exists())
            before = _dump(db, "schema_version")
            initialize(db)
            self.assertEqual(_dump(db, "schema_version"), before)
            self.assertEqual(pending_migrations(db), [])
            self.assertEqual(run_migrations(db), [])


class LegacyDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        self.dir = Path(self._temp.name)
        self.db = self.dir / "legacy.db"
        build_legacy_db(self.db)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_migration_backs_up_preserves_rows_and_passes_integrity(self) -> None:
        preserved = {table: _dump(self.db, table) for table in (
            "daily_candles", "dataset_holdings", "analysis_metadata", "analysis_results", "scenario_runs")}
        legacy_datasets = _dump(self.db, "candle_datasets")
        self.assertEqual(schema_version(self.db), 0)
        initialize(self.db)
        backups = list((self.dir / "backups").glob("legacy_pre_migration_*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(_dump(backups[0], "candle_datasets"), legacy_datasets)
        for table, rows in preserved.items():
            self.assertEqual(_dump(self.db, table), rows, table)
        migrated = _dump(self.db, "candle_datasets")
        self.assertEqual([row[:-1] for row in migrated], legacy_datasets)
        self.assertEqual(migrated[0][-1], "user_declared_unverified")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            indexes = con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='candle_datasets'").fetchall()
            self.assertIn(("idx_candle_datasets_portfolio",), indexes)
        events = list_provenance_events(1, path=self.db)
        self.assertEqual([event["event_type"] for event in events], ["migration_backfill"])
        initialize(self.db)  # nothing pending: no extra backup or event
        self.assertEqual(len(list((self.dir / "backups").glob("*.db"))), 1)
        self.assertEqual(len(list_provenance_events(1, path=self.db)), 1)

    def test_phase4_metrics_are_bit_identical_after_migration(self) -> None:
        def metrics():
            info = get_dataset_info(1, path=self.db)
            result = compute_phase4_analysis(1, info, get_candle_dataset(1, path=self.db),
                                             annual_risk_free_rate=0.06, confidence=0.95)
            return result.metrics, result.risk_contributions.to_dict()

        before = metrics()
        initialize(self.db)
        self.assertEqual(before, metrics())  # exact float equality

    def test_widened_checks_accept_split_basis_and_reject_unknown_values(self) -> None:
        initialize(self.db)
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE candle_datasets SET price_basis='split_adjusted_ohlcv' WHERE dataset_id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE candle_datasets SET price_basis='total_return' WHERE dataset_id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE candle_datasets SET verification_status='trusted' WHERE dataset_id=1")
            con.rollback()

    def test_cascade_foreign_keys_still_reference_rebuilt_table(self) -> None:
        initialize(self.db)
        with session(self.db) as con:
            con.execute("DELETE FROM scenario_runs")
            con.execute("DELETE FROM analysis_metadata")
            con.execute("DELETE FROM dataset_provenance_events")
            con.execute("DELETE FROM candle_datasets WHERE dataset_id=1")
        self.assertEqual(_dump(self.db, "daily_candles"), [])
        self.assertEqual(_dump(self.db, "dataset_holdings"), [])

    def test_restricting_foreign_key_still_blocks_orphaning_analyses(self) -> None:
        initialize(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            with session(self.db) as con:
                con.execute("DELETE FROM candle_datasets WHERE dataset_id=1")
        self.assertEqual(len(_dump(self.db, "candle_datasets")), 1)

    def test_unexpected_layout_rolls_back_and_leaves_version_unrecorded(self) -> None:
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("ALTER TABLE candle_datasets ADD COLUMN surprise TEXT")
            con.commit()
        with self.assertRaises(MigrationError):
            run_migrations(self.db)
        with closing(sqlite3.connect(self.db)) as con:
            versions = {row[0] for row in con.execute("SELECT version FROM schema_version")}
            self.assertEqual(versions, {1})
            ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='candle_datasets'").fetchone()[0]
            self.assertNotIn("split_adjusted_ohlcv", ddl)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0], 1)
            self.assertIsNone(con.execute(
                "SELECT name FROM sqlite_master WHERE name='candle_datasets__v2'").fetchone())

    def test_relabel_requires_corporate_action_reference_and_keeps_candles(self) -> None:
        initialize(self.db)
        candles_before = _dump(self.db, "daily_candles")
        with self.assertRaises(ValueError):
            relabel_price_basis(1, "split_adjusted_ohlcv", reference_dataset_id=99,
                                evidence={"note": "x"}, path=self.db)
        _insert_corporate_action_reference(self.db)
        with self.assertRaises(ValueError):
            relabel_price_basis(1, "split_adjusted_ohlcv", reference_dataset_id=5, evidence={}, path=self.db)
        relabel_price_basis(1, "split_adjusted_ohlcv", reference_dataset_id=5,
                            evidence={"quiet_bonus_ex_dates": ["2025-08-26"]}, path=self.db)
        info = get_dataset_info(1, path=self.db)
        self.assertEqual(info["price_basis"], "split_adjusted_ohlcv")
        self.assertEqual(info["metadata"]["price_basis"], "unadjusted_ohlcv")  # original declaration kept
        self.assertEqual(_dump(self.db, "daily_candles"), candles_before)
        event = list_provenance_events(1, path=self.db)[-1]
        self.assertEqual((event["old_value"], event["new_value"], event["reference_dataset_id"]),
                         ("unadjusted_ohlcv", "split_adjusted_ohlcv", 5))
        # Relabelled candles still analyse, with the new basis carried through.
        result = compute_phase4_analysis(1, info, get_candle_dataset(1, path=self.db))
        self.assertEqual(result.price_basis, "split_adjusted_ohlcv")
        record_provenance_note(1, "Reviewed jump scan", path=self.db)
        self.assertEqual(list_provenance_events(1, path=self.db)[-1]["event_type"], "note")

    def test_verified_live_is_reserved_for_angel_one(self) -> None:
        initialize(self.db)
        with self.assertRaises(ValueError):
            set_verification_status(1, "verified_live", evidence={"claim": "x"}, path=self.db)
        set_verification_status(1, "fallback_unverified", evidence={"reason": "Yahoo export"}, path=self.db)
        self.assertEqual(get_dataset_info(1, path=self.db)["verification_status"], "fallback_unverified")


class DerivationTests(unittest.TestCase):
    def test_conservative_ladder_mapping(self) -> None:
        cases = {
            ("user_supplied_csv", "user_declared_unverified"): "user_declared_unverified",
            ("user_supplied_csv", "recorded_unverified"): "user_declared_unverified",
            ("user_supplied_csv", "verified_live"): "user_declared_unverified",
            ("angel_one_smartapi", "broker_response_validated"): "verified_live",
            ("angel_one_smartapi", "recorded_unverified"): "user_declared_unverified",
            ("simulated", "anything"): "simulated",
            ("user_supplied_csv", "fallback_unverified"): "fallback_unverified",
        }
        for (kind, status), expected in cases.items():
            with self.subTest(kind=kind, status=status):
                self.assertEqual(derive_verification_status(kind, status), expected)


if __name__ == "__main__":
    unittest.main()
