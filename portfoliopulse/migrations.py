"""Versioned, idempotent, additive SQLite migrations.

``schema.sql`` is the frozen *baseline* (version 0). Every later change lives
here as a numbered migration recorded in ``schema_version``. Each migration:

* runs inside one transaction and is recorded in the same transaction;
* is idempotent: it inspects the live schema and skips work already present;
* never deletes or rewrites market observations, analyses or scenario runs.

Before any pending migration touches a database that already holds a candle
dataset, :func:`run_migrations` takes a timestamped SQLite backup.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PRICE_BASES = ("unadjusted_ohlcv", "provider_adjusted_ohlcv", "split_adjusted_ohlcv")
VERIFICATION_STATUSES = (
    "verified_live",
    "verified_reference_sample",
    "user_declared_unverified",
    "fallback_unverified",
    "simulated",
)
REFERENCE_KINDS = (
    "corporate_actions", "index_constituents", "index_tri", "index_reference_close", "macro_series",
)
CORPORATE_ACTION_TYPES = ("split", "bonus", "dividend", "rights", "merger", "demerger", "other")
PROVENANCE_EVENT_TYPES = (
    "migration_backfill", "price_basis_relabel", "verification_status_change",
    "corporate_action_review", "note",
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


class MigrationError(RuntimeError):
    """A migration could not be applied without risking stored data."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _table_sql(con: sqlite3.Connection, table: str) -> str | None:
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return None if row is None else str(row[0])


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")]


# --- Migration 1: provenance and reference-data tables ---------------------

_V1_SQL = f"""
CREATE TABLE IF NOT EXISTS reference_datasets (
    reference_dataset_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ({_sql_list(REFERENCE_KINDS)})),
    source_name TEXT NOT NULL CHECK(length(trim(source_name)) > 0),
    source_url TEXT,
    provenance_label TEXT NOT NULL CHECK(length(trim(provenance_label)) > 0),
    verification_status TEXT NOT NULL CHECK(verification_status IN ({_sql_list(VERIFICATION_STATUSES)})),
    file_sha256 TEXT NOT NULL CHECK(length(file_sha256) = 64),
    as_of_date TEXT,
    retrieved_at_utc TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK(row_count >= 0),
    metadata_json TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, file_sha256)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    reference_dataset_id INTEGER NOT NULL REFERENCES reference_datasets(reference_dataset_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK(length(trim(symbol)) > 0),
    ex_date TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK(action_type IN ({_sql_list(CORPORATE_ACTION_TYPES)})),
    ratio_new REAL CHECK(ratio_new IS NULL OR ratio_new > 0),
    ratio_old REAL CHECK(ratio_old IS NULL OR ratio_old > 0),
    cash_amount_inr REAL CHECK(cash_amount_inr IS NULL OR cash_amount_inr >= 0),
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(reference_dataset_id, symbol, ex_date, action_type)
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_symbol_date ON corporate_actions(symbol, ex_date);

CREATE TABLE IF NOT EXISTS dataset_provenance_events (
    event_id INTEGER PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES candle_datasets(dataset_id),
    event_type TEXT NOT NULL CHECK(event_type IN ({_sql_list(PROVENANCE_EVENT_TYPES)})),
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    reference_dataset_id INTEGER REFERENCES reference_datasets(reference_dataset_id),
    evidence_json TEXT NOT NULL DEFAULT '{{}}',
    recorded_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_provenance_events_dataset ON dataset_provenance_events(dataset_id, event_id);
"""


def _migration_1(con: sqlite3.Connection) -> None:
    for statement in _V1_SQL.split(";"):
        if statement.strip():
            con.execute(statement)


# --- Migration 2: widen price_basis and add verification_status ------------

_CANDLE_DATASETS_V2 = f"""
CREATE TABLE candle_datasets__v2 (
    dataset_id INTEGER PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    dataset_key TEXT NOT NULL UNIQUE,
    candles_sha256 TEXT NOT NULL,
    source_name TEXT NOT NULL CHECK(length(trim(source_name)) > 0),
    source_url TEXT,
    provenance_kind TEXT NOT NULL CHECK(length(trim(provenance_kind)) > 0),
    provenance_status TEXT NOT NULL CHECK(length(trim(provenance_status)) > 0),
    retrieved_at_utc TEXT NOT NULL,
    price_basis TEXT NOT NULL CHECK(price_basis IN ({_sql_list(PRICE_BASES)})),
    interval TEXT NOT NULL CHECK(interval = 'ONE_DAY'),
    window_start TEXT NOT NULL,
    window_end_exclusive TEXT NOT NULL CHECK(window_end_exclusive > window_start),
    input_csv_sha256 TEXT,
    candle_count INTEGER NOT NULL CHECK(candle_count > 0),
    aligned_price_dates INTEGER CHECK(aligned_price_dates >= 0),
    valid_daily_return_dates INTEGER CHECK(valid_daily_return_dates >= 0),
    incomplete_union_dates INTEGER CHECK(incomplete_union_dates >= 0),
    metadata_json TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verification_status TEXT NOT NULL DEFAULT 'user_declared_unverified'
        CHECK(verification_status IN ({_sql_list(VERIFICATION_STATUSES)}))
)
"""

_BASELINE_CANDLE_COLUMNS = (
    "dataset_id", "portfolio_id", "dataset_key", "candles_sha256", "source_name", "source_url",
    "provenance_kind", "provenance_status", "retrieved_at_utc", "price_basis", "interval",
    "window_start", "window_end_exclusive", "input_csv_sha256", "candle_count",
    "aligned_price_dates", "valid_daily_return_dates", "incomplete_union_dates",
    "metadata_json", "imported_at_utc",
)


def derive_verification_status(provenance_kind: str, provenance_status: str) -> str:
    """Conservative mapping; never upgrades a dataset beyond its recorded evidence.

    Only a validated Angel One broker response counts as ``verified_live``.
    Anything unrecognised is treated as ``user_declared_unverified``.
    """
    if provenance_status in VERIFICATION_STATUSES:
        if provenance_status == "verified_live" and provenance_kind != "angel_one_smartapi":
            return "user_declared_unverified"
        return provenance_status
    if provenance_kind == "angel_one_smartapi" and provenance_status == "broker_response_validated":
        return "verified_live"
    if provenance_kind == "simulated":
        return "simulated"
    return "user_declared_unverified"


def _migration_2(con: sqlite3.Connection) -> None:
    current = _table_sql(con, "candle_datasets")
    if current is None:
        raise MigrationError("candle_datasets is missing; apply the baseline schema first.")
    columns = _columns(con, "candle_datasets")
    if "split_adjusted_ohlcv" in current and "verification_status" in columns:
        return
    if tuple(columns) != _BASELINE_CANDLE_COLUMNS:
        raise MigrationError("candle_datasets has an unexpected column layout; refusing to rebuild it.")
    before = con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0]
    con.execute(_CANDLE_DATASETS_V2)
    column_list = ",".join(_BASELINE_CANDLE_COLUMNS)
    con.execute(f"INSERT INTO candle_datasets__v2({column_list}) SELECT {column_list} FROM candle_datasets")
    rows = con.execute("SELECT dataset_id, provenance_kind, provenance_status FROM candle_datasets__v2").fetchall()
    for dataset_id, kind, status in rows:
        derived = derive_verification_status(str(kind), str(status))
        con.execute("UPDATE candle_datasets__v2 SET verification_status=? WHERE dataset_id=?", (derived, dataset_id))
    con.execute("DROP TABLE candle_datasets")
    con.execute("ALTER TABLE candle_datasets__v2 RENAME TO candle_datasets")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_candle_datasets_portfolio ON candle_datasets(portfolio_id, imported_at_utc)"
    )
    after = con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0]
    if after != before:
        raise MigrationError("candle_datasets row count changed during the rebuild.")
    if con.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("Foreign-key check failed after rebuilding candle_datasets.")
    if _table_sql(con, "dataset_provenance_events") is None:
        raise MigrationError("Migration 1 must precede migration 2.")
    con.executemany(
        """INSERT INTO dataset_provenance_events(dataset_id,event_type,field_name,old_value,new_value,evidence_json)
           VALUES(?, 'migration_backfill', 'verification_status', NULL, ?, ?)""",
        [(dataset_id, derive_verification_status(str(kind), str(status)),
          '{"rule": "derive_verification_status", "migration": 2}') for dataset_id, kind, status in rows],
    )


# --- Migration 3: benchmark constituents and security classifications ------

_V3_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_constituents (
    reference_dataset_id INTEGER NOT NULL REFERENCES reference_datasets(reference_dataset_id) ON DELETE CASCADE,
    benchmark_symbol TEXT NOT NULL CHECK(length(trim(benchmark_symbol)) > 0),
    as_of_date TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK(length(trim(symbol)) > 0),
    company_name TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL CHECK(length(trim(industry)) > 0),
    weight REAL CHECK(weight IS NULL OR (weight >= 0 AND weight <= 1)),
    weight_basis TEXT NOT NULL CHECK(weight_basis IN ('official', 'estimated', 'none')),
    PRIMARY KEY(reference_dataset_id, symbol)
);

CREATE TABLE IF NOT EXISTS security_classifications (
    classification_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL CHECK(length(trim(symbol)) > 0),
    taxonomy TEXT NOT NULL CHECK(taxonomy IN ('nse_industry', 'project_sector')),
    classification TEXT NOT NULL CHECK(length(trim(classification)) > 0),
    common_sector TEXT NOT NULL CHECK(length(trim(common_sector)) > 0),
    effective_date TEXT NOT NULL,
    reference_dataset_id INTEGER REFERENCES reference_datasets(reference_dataset_id),
    source_label TEXT NOT NULL CHECK(length(trim(source_label)) > 0),
    UNIQUE(symbol, taxonomy, effective_date)
);
"""


def _migration_3(con: sqlite3.Connection) -> None:
    for statement in _V3_SQL.split(";"):
        if statement.strip():
            con.execute(statement)


# --- Migration 4: optimization results (separate from holdings) ------------
# Optimized allocations are illustrative and live only here; they never touch
# dataset_holdings, holdings or config/instruments.json.

_V4_SQL = """
CREATE TABLE IF NOT EXISTS optimization_runs (
    optimization_run_id INTEGER PRIMARY KEY,
    analysis_id INTEGER NOT NULL REFERENCES analysis_metadata(analysis_id),
    dataset_id INTEGER NOT NULL REFERENCES candle_datasets(dataset_id),
    strategy TEXT NOT NULL CHECK(length(trim(strategy)) > 0),
    status TEXT NOT NULL CHECK(status IN ('optimal', 'constrained', 'infeasible', 'undefined', 'failed', 'reference')),
    illustrative INTEGER NOT NULL DEFAULT 1 CHECK(illustrative = 1),
    settings_json TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(analysis_id, strategy)
);

CREATE TABLE IF NOT EXISTS optimization_weights (
    optimization_run_id INTEGER NOT NULL REFERENCES optimization_runs(optimization_run_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK(length(trim(symbol)) > 0),
    sector TEXT NOT NULL,
    target_weight REAL NOT NULL CHECK(target_weight >= -1e-9 AND target_weight <= 1.000000001),
    current_weight REAL NOT NULL,
    integer_shares INTEGER CHECK(integer_shares IS NULL OR integer_shares >= 0),
    allocated_value_inr REAL CHECK(allocated_value_inr IS NULL OR allocated_value_inr >= 0),
    allocated_weight REAL,
    PRIMARY KEY(optimization_run_id, symbol)
);

CREATE TABLE IF NOT EXISTS optimization_backtests (
    optimization_run_id INTEGER NOT NULL REFERENCES optimization_runs(optimization_run_id) ON DELETE CASCADE,
    observation_date TEXT NOT NULL,
    gross_return REAL NOT NULL,
    net_return REAL NOT NULL,
    turnover REAL NOT NULL DEFAULT 0 CHECK(turnover >= 0),
    cost_fraction REAL NOT NULL DEFAULT 0 CHECK(cost_fraction >= 0),
    PRIMARY KEY(optimization_run_id, observation_date)
);
"""


def _migration_4(con: sqlite3.Connection) -> None:
    for statement in _V4_SQL.split(";"):
        if statement.strip():
            con.execute(statement)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "provenance_reference_tables", _migration_1),
    Migration(2, "candle_datasets_split_basis_and_verification_status", _migration_2),
    Migration(3, "benchmark_constituents_and_classifications", _migration_3),
    Migration(4, "optimization_runs_weights_backtests", _migration_4),
)
LATEST_VERSION = MIGRATIONS[-1].version


def _ensure_version_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS schema_version (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               applied_at_utc TEXT NOT NULL
           )"""
    )


def applied_versions(con: sqlite3.Connection) -> set[int]:
    if _table_sql(con, "schema_version") is None:
        return set()
    return {int(row[0]) for row in con.execute("SELECT version FROM schema_version")}


def pending_migrations(path: Path) -> list[Migration]:
    if not Path(path).exists():
        return list(MIGRATIONS)
    with closing(sqlite3.connect(path)) as con:
        done = applied_versions(con)
    return [item for item in MIGRATIONS if item.version not in done]


def backup_database(path: Path, backup_dir: Path | None = None, *, label: str = "pre_migration") -> Path:
    """Copy a live SQLite database with the online backup API."""
    source = Path(path)
    target_dir = Path(backup_dir) if backup_dir is not None else source.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_dir / f"{source.stem}_{label}_{stamp}.db"
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(target)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("The pre-migration backup failed its integrity check.")
    return target


def _holds_market_data(path: Path) -> bool:
    if not Path(path).exists():
        return False
    with closing(sqlite3.connect(path)) as con:
        if _table_sql(con, "candle_datasets") is None:
            return False
        return con.execute("SELECT EXISTS(SELECT 1 FROM candle_datasets)").fetchone()[0] == 1


def run_migrations(path: Path, *, backup_dir: Path | None = None) -> list[int]:
    """Apply pending migrations in order; return the versions applied now.

    Foreign keys are disabled for the duration (SQLite's documented table
    rebuild procedure) and re-checked inside each transaction before commit.
    """
    pending = pending_migrations(path)
    if not pending:
        return []
    if _holds_market_data(path):
        backup_database(path, backup_dir)
    applied: list[int] = []
    with closing(sqlite3.connect(path, isolation_level=None)) as con:
        con.execute("PRAGMA foreign_keys = OFF")
        try:
            _ensure_version_table(con)
            for migration in pending:
                if migration.version in applied_versions(con):
                    continue  # applied concurrently by another connection
                con.execute("BEGIN IMMEDIATE")
                try:
                    migration.apply(con)
                    if con.execute("PRAGMA foreign_key_check").fetchall():
                        raise MigrationError(f"Migration {migration.version} broke foreign-key integrity.")
                    con.execute(
                        "INSERT INTO schema_version(version,name,applied_at_utc) VALUES(?,?,?)",
                        (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
                    )
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
                applied.append(migration.version)
        finally:
            con.execute("PRAGMA foreign_keys = ON")
    return applied


def schema_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as con:
        done = applied_versions(con)
    return max(done, default=0)
