from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from portfoliopulse.angelone.instrument_master import ConfiguredInstrument
from portfoliopulse.config import DB_PATH, PORTFOLIO_NAME, REAL_PORTFOLIO_NAME, SCHEMA_PATH
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
        candle_columns = {row["name"] for row in con.execute("PRAGMA table_info(daily_candles)")}
        if "retrieved_at_utc" not in candle_columns:
            con.execute("ALTER TABLE daily_candles ADD COLUMN retrieved_at_utc TEXT")
            con.execute(
                """UPDATE daily_candles SET retrieved_at_utc=(
                       SELECT retrieved_at_utc FROM candle_datasets d
                       WHERE d.dataset_id=daily_candles.dataset_id)
                   WHERE retrieved_at_utc IS NULL"""
            )


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


def save_dataset_scenario(
    *, dataset_id: int, analysis_id: int, as_of_date: str, name: str, scenario_type: str,
    portfolio_value_inr: float, impact_inr: float, impact_pct: float,
    settings: Mapping[str, Any], alerts: Mapping[str, Any], contributions: pd.DataFrame,
    path: Path = DB_PATH,
) -> int:
    """Atomically persist a hypothetical scenario and per-holding attribution."""
    if not math.isclose(float(contributions["impact_inr"].sum()), float(impact_inr), rel_tol=1e-10, abs_tol=1e-7):
        raise ValueError("Scenario contributions must reconcile before saving.")
    settings_json = json.dumps(settings, ensure_ascii=False, allow_nan=False, sort_keys=True)
    alerts_json = json.dumps(alerts, ensure_ascii=False, allow_nan=False, sort_keys=True)
    rows = []
    for symbol, row in contributions.iterrows():
        rows.append((str(symbol), float(row["holding_value_inr"]), float(row["shock"]),
                     float(row["impact_inr"]), float(row["portfolio_impact_pct"])))
    with session(path) as con:
        lineage = con.execute(
            """SELECT d.portfolio_id AS dataset_portfolio_id, a.dataset_id, a.portfolio_id AS analysis_portfolio_id FROM candle_datasets d
               JOIN analysis_metadata a ON a.analysis_id=? WHERE d.dataset_id=?""",
            (analysis_id, dataset_id),
        ).fetchone()
        if (lineage is None or int(lineage["dataset_id"]) != dataset_id
                or int(lineage["dataset_portfolio_id"]) != int(lineage["analysis_portfolio_id"])):
            raise ValueError("Scenario dataset and Phase 4 analysis lineage do not match.")
        cursor = con.execute(
            """INSERT INTO scenario_runs(dataset_id,analysis_id,as_of_date,scenario_name,scenario_type,
                   hypothetical,portfolio_value_inr,impact_inr,impact_pct,settings_json,alerts_json)
               VALUES(?,?,?,?,?,1,?,?,?,?,?)""",
            (dataset_id, analysis_id, as_of_date, name, scenario_type, float(portfolio_value_inr),
             float(impact_inr), float(impact_pct), settings_json, alerts_json),
        )
        run_id = int(cursor.lastrowid)
        con.executemany(
            """INSERT INTO scenario_contributions(scenario_run_id,symbol,holding_value_inr,shock,impact_inr,portfolio_impact_pct)
               VALUES(?,?,?,?,?,?)""", [(run_id, *row) for row in rows],
        )
        return run_id


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


_CANDLE_COLUMNS = ("symbol", "trading_symbol", "exchange", "date", "open", "high", "low", "close", "volume")
_PRICE_BASES = frozenset({"unadjusted_ohlcv", "provider_adjusted_ohlcv"})
_SAFE_METADATA_KEYS = (
    "source_name", "source", "source_url", "provenance_kind", "provenance_status",
    "retrieved_at_utc", "price_basis", "interval", "start", "end_exclusive",
    "input_csv_sha256", "valid_candle_rows", "aligned_price_dates",
    "valid_daily_return_dates", "incomplete_union_dates",
    "input_rows_excluded_outside_window", "processed_at_utc", "output_sha256",
    "instruments", "quality", "quality_summary", "portfolio_model",
)


def _iso_day(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    return parsed


def _utc_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("retrieved_at_utc must be a UTC ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("retrieved_at_utc must be a UTC ISO timestamp.") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("retrieved_at_utc must be a UTC ISO timestamp.")
    return parsed.isoformat()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upsert_configured_portfolio(
    entries: Sequence[ConfiguredInstrument], *,
    portfolio_name: str = REAL_PORTFOLIO_NAME, path: Path = DB_PATH,
) -> int:
    """Persist the NSE buy-and-hold quantities and benchmark from config.

    A listing already tied to a different logical symbol is rejected. Existing
    market datasets cannot be relabelled by changing a listing in place.
    """
    items = tuple(entries)
    if not isinstance(portfolio_name, str) or not portfolio_name.strip() or portfolio_name == PORTFOLIO_NAME:
        raise ValueError("Provide a distinct non-empty name for the real NSE portfolio.")
    if len(items) < 2 or len({item.symbol for item in items}) != len(items):
        raise ValueError("Configured portfolio needs unique symbols, a benchmark and holdings.")
    if len({(item.exchange, item.trading_symbol) for item in items}) != len(items):
        raise ValueError("Configured NSE trading symbols must be unique.")
    benchmarks = [item for item in items if item.is_benchmark]
    holdings = [item for item in items if not item.is_benchmark]
    if len(benchmarks) != 1 or not holdings or any(item.exchange != "NSE" for item in items):
        raise ValueError("Configured portfolio needs one NSE benchmark and NSE holdings.")
    if any(item.asset_class != ("Benchmark" if item.is_benchmark else "Equity") for item in items):
        raise ValueError("Configured asset classes must match benchmark and equity holdings.")
    if any(not item.symbol or not item.trading_symbol or not item.sector for item in items):
        raise ValueError("Configured symbols, trading symbols and sectors cannot be blank.")
    for item in items:
        if not math.isfinite(item.quantity) or not math.isfinite(item.target_weight):
            raise ValueError("Configured quantities and target weights must be finite.")
        if item.is_benchmark and (item.quantity != 0 or item.target_weight != 0):
            raise ValueError("The benchmark is not an owned holding.")
        if not item.is_benchmark and (item.quantity <= 0 or not 0 <= item.target_weight <= 1):
            raise ValueError("Holding quantities must be positive and target weights within [0, 1].")
    if not math.isclose(sum(item.target_weight for item in holdings), 1.0, abs_tol=1e-9):
        raise ValueError("Holding target weights must sum to 1.0.")

    initialize(path)
    with session(path) as con:
        con.execute("INSERT INTO portfolios(name, base_currency) VALUES(?, 'INR') ON CONFLICT(name) DO NOTHING", (portfolio_name,))
        portfolio_id = int(con.execute("SELECT portfolio_id FROM portfolios WHERE name=?", (portfolio_name,)).fetchone()[0])
        for item in items:
            existing = con.execute(
                """SELECT i.instrument_id, i.asset_class, l.trading_symbol, l.exchange
                   FROM instruments i LEFT JOIN instrument_listings l USING(instrument_id)
                   WHERE i.symbol=?""", (item.symbol,),
            ).fetchone()
            if existing and (existing["asset_class"] != item.asset_class or
                             (existing["trading_symbol"] is not None and
                              (existing["trading_symbol"], existing["exchange"]) !=
                              (item.trading_symbol, item.exchange))):
                raise ValueError(f"Existing instrument identity conflicts with {item.symbol}.")
            con.execute(
                """INSERT INTO instruments(symbol,name,asset_class,sector,currency,data_source)
                   VALUES(?,?,?,?, 'INR', 'configured_nse')
                   ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector""",
                (item.symbol, item.symbol, item.asset_class, item.sector),
            )
            instrument_id = int(con.execute("SELECT instrument_id FROM instruments WHERE symbol=?", (item.symbol,)).fetchone()[0])
            con.execute(
                """INSERT INTO instrument_listings(instrument_id,trading_symbol,exchange)
                   VALUES(?,?,?) ON CONFLICT(instrument_id) DO NOTHING""",
                (instrument_id, item.trading_symbol, item.exchange),
            )
            if item.is_benchmark:
                con.execute(
                    """INSERT INTO portfolio_benchmarks(portfolio_id,instrument_id) VALUES(?,?)
                       ON CONFLICT(portfolio_id) DO UPDATE SET instrument_id=excluded.instrument_id""",
                    (portfolio_id, instrument_id),
                )
            else:
                con.execute(
                    """INSERT INTO holdings(portfolio_id,instrument_id,quantity,target_weight)
                       VALUES(?,?,?,?) ON CONFLICT(portfolio_id,instrument_id)
                       DO UPDATE SET quantity=excluded.quantity,target_weight=excluded.target_weight""",
                    (portfolio_id, instrument_id, item.quantity, item.target_weight),
                )
        wanted = [item.symbol for item in holdings]
        con.execute(
            """DELETE FROM holdings WHERE portfolio_id=? AND instrument_id NOT IN
               (SELECT instrument_id FROM instruments WHERE symbol IN (""" + ",".join("?" for _ in wanted) + "))",
            (portfolio_id, *wanted),
        )
    return portfolio_id


def _validated_candles(frame: pd.DataFrame, metadata: Mapping[str, Any]) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty or not set(_CANDLE_COLUMNS).issubset(frame.columns):
        raise ValueError("Daily candles need symbol, trading_symbol, exchange, date and OHLCV columns.")
    clean = frame.copy()
    for column in ("symbol", "trading_symbol", "exchange"):
        if clean[column].isna().any():
            raise ValueError(f"Candle {column} cannot be missing.")
        clean[column] = clean[column].astype(str)
        if clean[column].str.strip().ne(clean[column]).any() or clean[column].eq("").any():
            raise ValueError(f"Candle {column} must be a non-empty exact identifier.")
    if set(clean["exchange"]) != {"NSE"}:
        raise ValueError("Only NSE candles match the configured portfolio.")
    if pd.api.types.is_datetime64_any_dtype(clean["date"]):
        if isinstance(clean["date"].dtype, pd.DatetimeTZDtype):
            raise ValueError("Candle dates must be timezone-free Indian calendar days.")
        dates = pd.to_datetime(clean["date"], errors="coerce")
        if dates.isna().any() or not dates.eq(dates.dt.normalize()).all():
            raise ValueError("Candle dates must be valid Indian calendar days.")
    else:
        strings = clean["date"].astype(str)
        dates = pd.to_datetime(strings, format="%Y-%m-%d", errors="coerce")
        if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").eq(strings).all():
            raise ValueError("Candle dates must use YYYY-MM-DD.")
    clean["date"] = dates.dt.strftime("%Y-%m-%d")
    if clean.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date candles are not allowed.")
    if not clean["date"].between(metadata["start"], metadata["end_exclusive"], inclusive="left").all():
        raise ValueError("Candle dates must stay inside the dataset window.")
    for column in ("open", "high", "low", "close", "volume"):
        try:
            clean[column] = pd.to_numeric(clean[column], errors="raise").astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Candle {column} must be numeric.") from exc
        if not np.isfinite(clean[column].to_numpy()).all():
            raise ValueError(f"Candle {column} must be finite.")
    if (clean[["open", "high", "low", "close"]] <= 0).any().any() or (clean["volume"] < 0).any():
        raise ValueError("Candle prices must be positive and volume non-negative.")
    if ((clean["low"] > clean[["open", "close"]].min(axis=1)) |
        (clean["high"] < clean[["open", "close"]].max(axis=1)) |
        (clean["high"] < clean["low"])).any():
        raise ValueError("Candle OHLC relationships are invalid.")
    for frame_key, metadata_key in (("source", "source_name"), ("price_basis", "price_basis"),
                                    ("interval", "interval"), ("provenance_kind", "provenance_kind")):
        if frame_key in clean and (clean[frame_key].isna().any() or set(clean[frame_key].astype(str)) != {metadata[metadata_key]}):
            raise ValueError(f"Candle {frame_key} disagrees with dataset provenance.")
    angel_source = metadata["provenance_kind"] == "angel_one_smartapi"
    if "retrieved_at_utc" not in clean:
        if angel_source:
            raise ValueError("Angel One candles need per-candle retrieval timestamps.")
        clean["retrieved_at_utc"] = metadata["retrieved_at_utc"]
    if clean["retrieved_at_utc"].isna().any():
        raise ValueError("Candle retrieval timestamps cannot be missing.")
    clean["retrieved_at_utc"] = clean["retrieved_at_utc"].map(_utc_timestamp)
    observed_times = set(clean["retrieved_at_utc"])
    if (angel_source and max(observed_times) > metadata["retrieved_at_utc"]) or (
        not angel_source and observed_times != {metadata["retrieved_at_utc"]}
    ):
        raise ValueError("Candle retrieval timestamps disagree with dataset provenance.")
    if "source_url" in clean and (clean["source_url"].isna().any() or
                                  set(clean["source_url"].astype(str)) != {metadata.get("source_url")}):
        raise ValueError("Candle source URLs disagree with dataset provenance.")
    if angel_source:
        if "token" not in clean or "timestamp_ist" not in clean:
            raise ValueError("Angel One candles need token and IST timestamp columns.")
    clean["token"] = clean["token"] if "token" in clean else None
    clean["timestamp_ist"] = clean["timestamp_ist"] if "timestamp_ist" in clean else None
    if angel_source and clean["token"].isna().any():
        raise ValueError("Angel One broker tokens cannot be missing.")
    if clean["token"].notna().any():
        tokens = clean["token"].astype(str)
        if clean["token"].isna().any() or not tokens.str.fullmatch(r"[1-9][0-9]*").all():
            raise ValueError("Broker tokens must be positive decimal identifiers.")
        if clean.assign(token=tokens).groupby("symbol")["token"].nunique().gt(1).any():
            raise ValueError("Each symbol needs one broker token per dataset.")
        clean["token"] = tokens
    if angel_source and clean["timestamp_ist"].isna().any():
        raise ValueError("Angel One IST timestamps cannot be missing.")
    if clean["timestamp_ist"].notna().any():
        if clean["timestamp_ist"].isna().any():
            raise ValueError("IST timestamps must be present for every candle or none.")
        normalized: list[str] = []
        for value, candle_date in zip(clean["timestamp_ist"], clean["date"]):
            try:
                timestamp = pd.Timestamp(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("Candle IST timestamp is invalid.") from exc
            if timestamp.tzinfo is None or str(timestamp.tz_convert("Asia/Kolkata").date()) != candle_date:
                raise ValueError("Candle IST timestamp must match its Indian calendar date.")
            normalized.append(timestamp.tz_convert("Asia/Kolkata").isoformat())
        clean["timestamp_ist"] = normalized
    return clean.sort_values(["date", "symbol"]).reset_index(drop=True)


def _dataset_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("Dataset metadata must be a mapping.")
    safe = {key: metadata[key] for key in _SAFE_METADATA_KEYS if key in metadata}
    source_name = safe.get("source_name", safe.get("source"))
    for label, value in (("source_name", source_name), ("provenance_kind", safe.get("provenance_kind")),
                         ("price_basis", safe.get("price_basis"))):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Dataset metadata needs {label}.")
    if safe["price_basis"] not in _PRICE_BASES:
        raise ValueError("Dataset price_basis is unsupported.")
    if safe.get("interval", "ONE_DAY") != "ONE_DAY":
        raise ValueError("Dataset interval must be ONE_DAY.")
    if "source_url" in safe:
        if not isinstance(safe["source_url"], str):
            raise ValueError("Dataset source_url must be a credential-free HTTPS URL.")
        url = urlsplit(safe["source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Dataset source_url must be a credential-free HTTPS URL.")
    first, stop = _iso_day(safe.get("start"), "start"), _iso_day(safe.get("end_exclusive"), "end_exclusive")
    if first >= stop:
        raise ValueError("Dataset start must precede its exclusive end date.")
    safe["source_name"] = source_name.strip()
    safe.pop("source", None)
    safe["retrieved_at_utc"] = _utc_timestamp(safe.get("retrieved_at_utc"))
    safe["interval"] = "ONE_DAY"
    safe.setdefault("provenance_status", "recorded_unverified")
    if not isinstance(safe["provenance_status"], str) or not safe["provenance_status"].strip():
        raise ValueError("Dataset provenance_status must be non-empty.")
    if "input_csv_sha256" in safe and (not isinstance(safe["input_csv_sha256"], str) or
                                       len(safe["input_csv_sha256"]) != 64 or
                                       any(char not in "0123456789abcdef" for char in safe["input_csv_sha256"])):
        raise ValueError("input_csv_sha256 must be a lowercase SHA-256 digest.")
    for key in ("valid_candle_rows", "aligned_price_dates", "valid_daily_return_dates", "incomplete_union_dates",
                "input_rows_excluded_outside_window"):
        if key in safe and (isinstance(safe[key], bool) or not isinstance(safe[key], int) or safe[key] < 0):
            raise ValueError(f"Dataset {key} must be a non-negative integer.")
    try:
        json.dumps(safe, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Dataset metadata must be JSON-serializable.") from exc
    return safe


def import_candle_dataset(
    candles: pd.DataFrame, *, metadata: Mapping[str, Any],
    portfolio_name: str = REAL_PORTFOLIO_NAME, path: Path = DB_PATH,
) -> int:
    """Validate and atomically import one source-labelled NSE daily dataset.

    Reimporting identical content and metadata returns its existing ID. A
    different source, adjustment basis or content receives a separate ID.
    No observations are forward-filled or converted to returns here.
    """
    safe = _dataset_metadata(metadata)
    clean = _validated_candles(candles, safe)
    if "valid_candle_rows" in safe and safe["valid_candle_rows"] != len(clean):
        raise ValueError("Declared valid candle count does not match the imported rows.")
    rows = [
        (row.symbol, row.trading_symbol, row.exchange, row.date,
         float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume),
         row.token, row.timestamp_ist, row.retrieved_at_utc)
        for row in clean.loc[:, [*_CANDLE_COLUMNS, "token", "timestamp_ist", "retrieved_at_utc"]].itertuples(index=False)
    ]
    candle_hash = _sha256_json(rows)
    old_candle_hash = _sha256_json([row[:-1] for row in rows])
    identity_keys = (
        "source_name", "source_url", "provenance_kind", "provenance_status",
        "retrieved_at_utc", "price_basis", "interval", "start", "end_exclusive",
        "input_csv_sha256", "instruments",
    )
    stable_metadata = {name: safe[name] for name in identity_keys if name in safe}
    legacy_key = _sha256_json({"portfolio": portfolio_name, "metadata": stable_metadata, "candles_sha256": old_candle_hash})

    # `initialize` is safe for a new database and idempotent for the demo DB.
    initialize(path)
    with session(path) as con:
        con.execute("BEGIN IMMEDIATE")
        portfolio = con.execute("SELECT portfolio_id FROM portfolios WHERE name=?", (portfolio_name,)).fetchone()
        if portfolio is None:
            raise ValueError("Configured NSE portfolio has not been saved to SQLite.")
        portfolio_id = int(portfolio[0])
        expected_rows = con.execute(
            """SELECT i.instrument_id, i.symbol, l.trading_symbol, l.exchange,
                      i.asset_class, i.sector, COALESCE(h.quantity, 0) AS quantity,
                      COALESCE(h.target_weight, 0) AS target_weight,
                      CASE WHEN b.instrument_id IS NULL THEN 0 ELSE 1 END AS is_benchmark
               FROM instruments i
               JOIN instrument_listings l USING(instrument_id)
               LEFT JOIN holdings h ON h.instrument_id=i.instrument_id AND h.portfolio_id=?
               LEFT JOIN portfolio_benchmarks b ON b.instrument_id=i.instrument_id AND b.portfolio_id=?
               WHERE h.instrument_id IS NOT NULL OR b.instrument_id IS NOT NULL""",
            (portfolio_id, portfolio_id),
        ).fetchall()
        expected = {(row["symbol"], row["trading_symbol"], row["exchange"]): row["instrument_id"] for row in expected_rows}
        observed = set(zip(clean["symbol"], clean["trading_symbol"], clean["exchange"]))
        if not expected or observed != set(expected):
            raise ValueError("Candles must exactly match the configured holdings and benchmark listings.")
        snapshot = sorted((
            row["symbol"], row["trading_symbol"], row["exchange"], row["asset_class"],
            row["sector"], float(row["quantity"]), float(row["target_weight"]), int(row["is_benchmark"]),
        ) for row in expected_rows)
        key = _sha256_json({"portfolio": portfolio_name, "metadata": stable_metadata,
                            "candles_sha256": candle_hash, "portfolio_snapshot": snapshot})
        known = con.execute("SELECT dataset_id, candle_count, candles_sha256 FROM candle_datasets WHERE dataset_key=?", (key,)).fetchone()
        if known is None:
            former_snapshot_key = _sha256_json({"portfolio": portfolio_name, "metadata": stable_metadata,
                                                "candles_sha256": old_candle_hash,
                                                "portfolio_snapshot": snapshot})
            legacy = con.execute(
                "SELECT dataset_id, candle_count, candles_sha256 FROM candle_datasets WHERE dataset_key IN (?,?)",
                (legacy_key, former_snapshot_key),
            ).fetchone()
            if legacy is not None:
                previous = con.execute(
                    """SELECT symbol,trading_symbol,exchange,asset_class,sector,quantity,target_weight,is_benchmark
                       FROM dataset_holdings WHERE dataset_id=? ORDER BY symbol""", (legacy["dataset_id"],),
                ).fetchall()
                recorded_snapshot = [tuple(row) for row in previous]
                if not previous or recorded_snapshot == snapshot:
                    known = legacy
                    if legacy["candles_sha256"] != old_candle_hash:
                        raise ValueError("Existing dataset identity disagrees with candle content.")
                    con.execute("UPDATE candle_datasets SET dataset_key=?,candles_sha256=? WHERE dataset_id=?",
                                (key, candle_hash, legacy["dataset_id"]))
                    con.executemany(
                        """UPDATE daily_candles SET retrieved_at_utc=?
                           WHERE dataset_id=? AND instrument_id=? AND observation_date=?""",
                        [(row[-1], legacy["dataset_id"], expected[(row[0], row[1], row[2])], row[3]) for row in rows],
                    )
        if known:
            if known["candle_count"] != len(rows) or known["candles_sha256"] not in {old_candle_hash, candle_hash}:
                raise ValueError("Existing dataset identity disagrees with candle content.")
            count = con.execute("SELECT COUNT(*) FROM dataset_holdings WHERE dataset_id=?", (known["dataset_id"],)).fetchone()[0]
            if count == 0:
                con.executemany(
                    """INSERT INTO dataset_holdings(dataset_id,symbol,trading_symbol,exchange,
                       asset_class,sector,quantity,target_weight,is_benchmark) VALUES(?,?,?,?,?,?,?,?,?)""",
                    [(known["dataset_id"], *item) for item in snapshot],
                )
            elif count != len(snapshot):
                raise ValueError("Existing dataset has an incomplete portfolio snapshot.")
            return int(known["dataset_id"])
        cursor = con.execute(
            """INSERT INTO candle_datasets(
                portfolio_id,dataset_key,candles_sha256,source_name,source_url,
                provenance_kind,provenance_status,retrieved_at_utc,price_basis,interval,
                window_start,window_end_exclusive,input_csv_sha256,candle_count,
                aligned_price_dates,valid_daily_return_dates,incomplete_union_dates,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (portfolio_id, key, candle_hash, safe["source_name"], safe.get("source_url"),
             safe["provenance_kind"], safe["provenance_status"], safe["retrieved_at_utc"],
             safe["price_basis"], safe["interval"], safe["start"], safe["end_exclusive"],
             safe.get("input_csv_sha256"), len(rows), safe.get("aligned_price_dates"),
             safe.get("valid_daily_return_dates"), safe.get("incomplete_union_dates"),
             json.dumps(safe, sort_keys=True, allow_nan=False)),
        )
        dataset_id = int(cursor.lastrowid)
        con.executemany(
            """INSERT INTO dataset_holdings(dataset_id,symbol,trading_symbol,exchange,
               asset_class,sector,quantity,target_weight,is_benchmark) VALUES(?,?,?,?,?,?,?,?,?)""",
            [(dataset_id, *item) for item in snapshot],
        )
        con.executemany(
            """INSERT INTO daily_candles(
                dataset_id,instrument_id,observation_date,open_price,high_price,low_price,
                close_price,volume,broker_token,timestamp_ist,retrieved_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            [(dataset_id, expected[(row[0], row[1], row[2])], *row[3:]) for row in rows],
        )
    return dataset_id


def get_dataset_info(dataset_id: int, *, path: Path = DB_PATH) -> dict[str, Any]:
    """Read the explicit source, price basis and quality lineage for a dataset."""
    with session(path) as con:
        row = con.execute(
            """SELECT d.*, p.name AS portfolio_name FROM candle_datasets d
               JOIN portfolios p USING(portfolio_id) WHERE d.dataset_id=?""", (dataset_id,),
        ).fetchone()
        snapshot = con.execute(
            """SELECT symbol,trading_symbol,exchange,asset_class,sector,quantity,target_weight,is_benchmark
               FROM dataset_holdings WHERE dataset_id=? ORDER BY symbol""", (dataset_id,),
        ).fetchall()
    if row is None:
        raise ValueError(f"Unknown candle dataset ID: {dataset_id}.")
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json"))
    result["portfolio_snapshot"] = [dict(item) for item in snapshot]
    return result


def list_candle_datasets(path: Path = DB_PATH) -> list[dict[str, Any]]:
    """List stored real-market datasets for explicit dashboard selection."""
    with session(path) as con:
        rows = con.execute(
            """SELECT d.dataset_id, p.name AS portfolio_name, d.source_name,
                      d.price_basis, d.window_start, d.window_end_exclusive,
                      d.candle_count, d.imported_at_utc
               FROM candle_datasets d JOIN portfolios p USING(portfolio_id)
               ORDER BY d.imported_at_utc DESC, d.dataset_id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_candle_dataset(dataset_id: int, *, path: Path = DB_PATH) -> pd.DataFrame:
    """Return one dataset only; callers must select its ID explicitly."""
    with session(path) as con:
        frame = pd.read_sql_query(
            """SELECT i.symbol, l.trading_symbol, l.exchange, c.observation_date AS date,
                      c.open_price AS open, c.high_price AS high, c.low_price AS low,
                      c.close_price AS close, c.volume, c.broker_token AS token,
                      c.timestamp_ist, d.source_name AS source, d.price_basis,
                      d.provenance_kind, c.retrieved_at_utc, d.interval
               FROM daily_candles c JOIN candle_datasets d USING(dataset_id)
               JOIN instruments i USING(instrument_id)
               JOIN instrument_listings l USING(instrument_id)
               WHERE c.dataset_id=? ORDER BY c.observation_date, i.symbol""",
            con, params=(dataset_id,),
        )
    if frame.empty:
        raise ValueError(f"Unknown or empty candle dataset ID: {dataset_id}.")
    frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d")
    return frame


def _serialized_analysis_inputs(
    window_start: str, window_end: str,
    settings: Mapping[str, Any], quality: Mapping[str, Any],
) -> tuple[str, str]:
    start, end = _iso_day(window_start, "window_start"), _iso_day(window_end, "window_end")
    if start > end:
        raise ValueError("Analysis window_start must not exceed window_end.")
    if not isinstance(settings, Mapping) or not isinstance(quality, Mapping):
        raise ValueError("Analysis settings and quality must be mappings.")
    try:
        settings_json = json.dumps(dict(settings), sort_keys=True, allow_nan=False)
        quality_json = json.dumps(dict(quality), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Analysis metadata must be JSON-serializable and finite.") from exc
    return settings_json, quality_json


def _insert_analysis_metadata(
    con: sqlite3.Connection, dataset_id: int, portfolio_name: str,
    window_start: str, window_end: str, settings_json: str, quality_json: str,
) -> int:
    dataset = con.execute(
        """SELECT d.portfolio_id, d.window_start, d.window_end_exclusive
           FROM candle_datasets d JOIN portfolios p USING(portfolio_id)
           WHERE d.dataset_id=? AND p.name=?""", (dataset_id, portfolio_name),
    ).fetchone()
    if dataset is None:
        raise ValueError("Analysis dataset does not belong to the named portfolio.")
    if window_start < dataset["window_start"] or window_end >= dataset["window_end_exclusive"]:
        raise ValueError("Analysis window must be inside the chosen dataset window.")
    con.execute(
        """INSERT INTO analysis_metadata(dataset_id,portfolio_id,window_start,window_end,settings_json,quality_json)
           VALUES(?,?,?,?,?,?) ON CONFLICT(dataset_id,portfolio_id,window_start,window_end,settings_json,quality_json)
           DO NOTHING""",
        (dataset_id, dataset["portfolio_id"], window_start, window_end, settings_json, quality_json),
    )
    row = con.execute(
        """SELECT analysis_id FROM analysis_metadata WHERE dataset_id=? AND portfolio_id=?
           AND window_start=? AND window_end=? AND settings_json=? AND quality_json=?""",
        (dataset_id, dataset["portfolio_id"], window_start, window_end, settings_json, quality_json),
    ).fetchone()
    return int(row[0])


def save_analysis_metadata(
    dataset_id: int, *, portfolio_name: str = REAL_PORTFOLIO_NAME,
    window_start: str, window_end: str, settings: Mapping[str, Any],
    quality: Mapping[str, Any], path: Path = DB_PATH,
) -> int:
    """Record analysis settings and exclusions against a chosen dataset."""
    settings_json, quality_json = _serialized_analysis_inputs(window_start, window_end, settings, quality)
    with session(path) as con:
        return _insert_analysis_metadata(
            con, dataset_id, portfolio_name, window_start, window_end, settings_json, quality_json,
        )


def _validated_analysis_metrics(metrics: Mapping[str, tuple[float, str]]) -> list[tuple[str, float, str]]:
    if not isinstance(metrics, Mapping) or not metrics:
        raise ValueError("Analysis metrics must be a non-empty name-to-(value,unit) mapping.")
    rows: list[tuple[str, float, str]] = []
    for name, metric in metrics.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(metric, (tuple, list)) or len(metric) != 2:
            raise ValueError("Each analysis metric needs a name, finite value and unit.")
        value, unit = metric
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Analysis metric values must be finite numbers.") from exc
        if not math.isfinite(number) or not isinstance(unit, str) or not unit.strip():
            raise ValueError("Analysis metric values must be finite and units non-empty.")
        rows.append((name, number, unit))
    return rows


def _replace_analysis_results(
    con: sqlite3.Connection, analysis_id: int, rows: list[tuple[str, float, str]],
) -> None:
    con.execute("DELETE FROM analysis_results WHERE analysis_id=?", (analysis_id,))
    con.executemany(
        "INSERT INTO analysis_results(analysis_id,metric_name,value,unit) VALUES(?,?,?,?)",
        [(analysis_id, name, number, unit) for name, number, unit in rows],
    )


def save_analysis_results(
    analysis_id: int, metrics: Mapping[str, tuple[float, str]], *, path: Path = DB_PATH,
) -> None:
    """Atomically replace scalar results for one recorded analysis run."""
    rows = _validated_analysis_metrics(metrics)
    with session(path) as con:
        if con.execute("SELECT 1 FROM analysis_metadata WHERE analysis_id=?", (analysis_id,)).fetchone() is None:
            raise ValueError("Analysis run does not exist.")
        _replace_analysis_results(con, analysis_id, rows)


def save_analysis_run(
    dataset_id: int, *, portfolio_name: str = REAL_PORTFOLIO_NAME,
    window_start: str, window_end: str, settings: Mapping[str, Any],
    quality: Mapping[str, Any], metrics: Mapping[str, tuple[float, str]],
    path: Path = DB_PATH,
) -> int:
    """Save metadata and finite scalar metrics in one SQLite transaction.

    A failed result insert rolls back a newly inserted metadata row, too.
    Repeating identical inputs returns the same run ID and replaces its
    result rows atomically.
    """
    settings_json, quality_json = _serialized_analysis_inputs(window_start, window_end, settings, quality)
    rows = _validated_analysis_metrics(metrics)
    with session(path) as con:
        analysis_id = _insert_analysis_metadata(
            con, dataset_id, portfolio_name, window_start, window_end, settings_json, quality_json,
        )
        _replace_analysis_results(con, analysis_id, rows)
        return analysis_id
