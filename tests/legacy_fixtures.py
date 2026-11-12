"""Build a *baseline-schema* (version 0) SQLite database for migration tests.

Rows are inserted with plain SQL against ``schema.sql`` exactly as a database
created before ``migrations.py`` existed. No migration code runs here.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from portfoliopulse.config import REAL_PORTFOLIO_NAME, SCHEMA_PATH

DATES = ("2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07")
A_CLOSES = (100.0, 102.0, 99.0, 101.0, 104.0)
B_CLOSES = (50.0, 50.5, 51.0, 49.0, 50.0)
N_CLOSES = (1000.0, 1010.0, 995.0, 1005.0, 1020.0)
RETRIEVED = "2026-01-08T00:00:00+00:00"
INSTRUMENTS = (
    ("A", "A-EQ", "Equity", "Energy", 10.0, 0.5, 0),
    ("B", "B-EQ", "Equity", "Banking", 20.0, 0.5, 0),
    ("NIFTY 50", "Nifty 50", "Benchmark", "Broad Market", 0.0, 0.0, 1),
)


def build_legacy_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT INTO portfolios(portfolio_id,name) VALUES(1,?)", (REAL_PORTFOLIO_NAME,))
        for index, (symbol, trading, asset, sector, _q, _w, _b) in enumerate(INSTRUMENTS, start=1):
            con.execute(
                "INSERT INTO instruments(instrument_id,symbol,name,asset_class,sector,data_source) "
                "VALUES(?,?,?,?,?,'configured_nse')", (index, symbol, symbol, asset, sector),
            )
            con.execute("INSERT INTO instrument_listings VALUES(?,?,'NSE')", (index, trading))
        metadata = {"source_name": "Legacy fixture", "price_basis": "unadjusted_ohlcv",
                    "provenance_kind": "user_supplied_csv", "provenance_status": "user_declared_unverified"}
        con.execute(
            """INSERT INTO candle_datasets(dataset_id,portfolio_id,dataset_key,candles_sha256,source_name,
                   source_url,provenance_kind,provenance_status,retrieved_at_utc,price_basis,interval,
                   window_start,window_end_exclusive,input_csv_sha256,candle_count,metadata_json)
               VALUES(1,1,'legacy-key',?,'Legacy fixture','https://example.org/x','user_supplied_csv',
                   'user_declared_unverified',?,'unadjusted_ohlcv','ONE_DAY',?,'2026-01-08',NULL,15,?)""",
            ("b" * 64, RETRIEVED, DATES[0], json.dumps(metadata, sort_keys=True)),
        )
        for symbol, trading, asset, sector, quantity, weight, bench in INSTRUMENTS:
            con.execute("INSERT INTO dataset_holdings VALUES(1,?,?,'NSE',?,?,?,?,?)",
                        (symbol, trading, asset, sector, quantity, weight, bench))
        for day, a, b, n in zip(DATES, A_CLOSES, B_CLOSES, N_CLOSES):
            for instrument_id, close in ((1, a), (2, b), (3, n)):
                con.execute(
                    "INSERT INTO daily_candles VALUES(1,?,?,?,?,?,?,?,NULL,NULL,?)",
                    (instrument_id, day, close, close, close, close, 0 if instrument_id == 3 else 100, RETRIEVED),
                )
        con.execute(
            "INSERT INTO analysis_metadata(analysis_id,dataset_id,portfolio_id,window_start,window_end,"
            "settings_json,quality_json) VALUES(1,1,1,?,?,'{}','{}')", (DATES[0], DATES[-1]),
        )
        con.execute("INSERT INTO analysis_results VALUES(1,'portfolio_sharpe',1.25,'unitless')")
        con.execute(
            "INSERT INTO scenario_runs(scenario_run_id,dataset_id,analysis_id,as_of_date,scenario_name,"
            "scenario_type,portfolio_value_inr,impact_inr,impact_pct,settings_json,alerts_json) "
            "VALUES(1,1,1,?,'Broad -10%','broad_market',2040,-204,-0.1,'{}','{}')", (DATES[-1],),
        )
        con.commit()
