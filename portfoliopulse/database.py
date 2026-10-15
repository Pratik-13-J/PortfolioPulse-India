from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from portfoliopulse.config import DB_PATH, PORTFOLIO_NAME, SCHEMA_PATH
from portfoliopulse.data import normalize_observations
from portfoliopulse.demo import DEMO_INSTRUMENTS, DEMO_SOURCE


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def session(path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a transaction and always close its connection."""
    with closing(connect(path)) as con:
        with con:
            yield con


def initialize(path: Path = DB_PATH) -> None:
    with session(path) as con:
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        columns = {row["name"] for row in con.execute("PRAGMA table_info(instruments)")}
        if "sector" not in columns:
            con.execute("ALTER TABLE instruments ADD COLUMN sector TEXT NOT NULL DEFAULT 'Unspecified'")


def load_observations(frame: pd.DataFrame, source: str, path: Path = DB_PATH) -> int:
    clean = normalize_observations(frame)
    clean["date"] = clean["date"].dt.strftime("%Y-%m-%d")
    with session(path) as con:
        symbols = clean["symbol"].unique().tolist()
        placeholders = ",".join("?" for _ in symbols)
        rows = con.execute(f"SELECT instrument_id, symbol FROM instruments WHERE symbol IN ({placeholders})", symbols).fetchall()
        ids = {row["symbol"]: row["instrument_id"] for row in rows}
        missing = set(symbols) - ids.keys()
        if missing:
            raise ValueError(f"Unknown symbols: {', '.join(sorted(missing))}")
        con.executemany(
            """INSERT INTO market_observations(instrument_id, observation_date, close_value, source)
               VALUES (?, ?, ?, ?) ON CONFLICT(instrument_id, observation_date)
               DO UPDATE SET close_value=excluded.close_value, source=excluded.source, imported_at=CURRENT_TIMESTAMP""",
            [(ids[r.symbol], r.date, float(r.close_value), source) for r in clean.itertuples(index=False)],
        )
    return len(clean)


def get_holdings(path: Path = DB_PATH) -> pd.DataFrame:
    with session(path) as con:
        return pd.read_sql_query(
            """SELECT i.symbol, i.name, i.asset_class, i.sector, h.quantity, h.target_weight
               FROM holdings h JOIN instruments i USING(instrument_id)
               JOIN portfolios p USING(portfolio_id) WHERE p.name = ? ORDER BY i.asset_class, i.symbol""",
            con, params=(PORTFOLIO_NAME,),
        )


def save_holdings(quantities: dict[str, float], target_weights: dict[str, float], path: Path = DB_PATH) -> None:
    if any(v < 0 for v in quantities.values()):
        raise ValueError("Quantities cannot be negative.")
    if any(v < 0 for v in target_weights.values()) or abs(sum(target_weights.values()) - 1.0) > 0.005:
        raise ValueError("Target weights must be non-negative and sum to 100%.")
    with session(path) as con:
        con.executemany(
            """UPDATE holdings SET quantity=?, target_weight=? WHERE instrument_id=(SELECT instrument_id FROM instruments WHERE symbol=?)
               AND portfolio_id=(SELECT portfolio_id FROM portfolios WHERE name=?)""",
            [(float(q), float(target_weights[symbol]), symbol, PORTFOLIO_NAME) for symbol, q in quantities.items()],
        )


def get_prices(symbols: list[str], start: str | None = None, end: str | None = None, path: Path = DB_PATH) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    clauses = [f"i.symbol IN ({','.join('?' for _ in symbols)})"]
    params: list[object] = list(symbols)
    if start:
        clauses.append("o.observation_date >= ?")
        params.append(start)
    if end:
        clauses.append("o.observation_date <= ?")
        params.append(end)
    with session(path) as con:
        frame = pd.read_sql_query(
            """SELECT i.symbol, i.asset_class, o.observation_date AS date, o.close_value, o.source
               FROM market_observations o JOIN instruments i USING(instrument_id)
               WHERE """ + " AND ".join(clauses) + " ORDER BY date, i.symbol",
            con, params=params,
        )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def save_run(portfolio_id: int, start: str, end: str, confidence: float, rf: float, metrics: dict[str, float], path: Path = DB_PATH) -> int:
    with session(path) as con:
        cursor = con.execute(
            "INSERT INTO risk_runs(portfolio_id, window_start, window_end, method, confidence, annual_risk_free_rate) VALUES (?, ?, ?, ?, ?, ?)",
            (portfolio_id, start, end, "historical_daily_returns", confidence, rf),
        )
        run_id = int(cursor.lastrowid)
        con.executemany("INSERT INTO risk_metrics(run_id, metric_name, value, unit) VALUES (?, ?, ?, ?)",
                        [(run_id, k, float(v), "ratio" if k in {"sharpe", "max_drawdown", "var", "expected_shortfall"} else "percent") for k, v in metrics.items()])
        return run_id


def save_scenario(name: str, as_of: str, impact: float, impact_pct: float, shocks: dict, contributions: dict, path: Path = DB_PATH) -> None:
    with session(path) as con:
        portfolio_id = con.execute("SELECT portfolio_id FROM portfolios WHERE name=?", (PORTFOLIO_NAME,)).fetchone()[0]
        con.execute("INSERT INTO scenarios(portfolio_id, name, as_of_date, impact_inr, impact_pct, shocks_json, contributions_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (portfolio_id, name, as_of, impact, impact_pct, json.dumps(shocks), json.dumps(contributions)))


def seed_instruments(path: Path = DB_PATH) -> None:
    rows = [(item.symbol, item.name, item.asset_class, item.sector, DEMO_SOURCE) for item in DEMO_INSTRUMENTS]
    with session(path) as con:
        con.executemany("INSERT OR IGNORE INTO instruments(symbol,name,asset_class,sector,data_source) VALUES(?,?,?,?,?)", rows)
        con.execute("INSERT OR IGNORE INTO portfolios(name) VALUES (?)", (PORTFOLIO_NAME,))
        p_id = con.execute("SELECT portfolio_id FROM portfolios WHERE name=?", (PORTFOLIO_NAME,)).fetchone()[0]
        quantities = [(item.symbol, item.quantity, item.target_weight) for item in DEMO_INSTRUMENTS if item.asset_class != "Benchmark"]
        con.executemany("""INSERT OR IGNORE INTO holdings(portfolio_id,instrument_id,quantity,target_weight)
                           VALUES (?,(SELECT instrument_id FROM instruments WHERE symbol=?),?,?)""",
                        [(p_id, *row) for row in quantities])
