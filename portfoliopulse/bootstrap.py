"""Initialize the local Indian demo portfolio from its checked-in CSV."""

from pathlib import Path

from portfoliopulse.config import DB_PATH, DEMO_CSV_PATH
from portfoliopulse.data import PriceDataError, load_with_csv_fallback
from portfoliopulse.database import initialize, load_observations, seed_instruments, session
from portfoliopulse.demo import DEMO_INSTRUMENTS


def ensure_demo_data(path: Path = DB_PATH, csv_path: Path = DEMO_CSV_PATH) -> int:
    """Seed only a database with no observations for the fictional demo universe."""
    initialize(path)
    seed_instruments(path)
    symbols = [item.symbol for item in DEMO_INSTRUMENTS]
    with session(path) as con:
        rows = con.execute(
            """SELECT i.symbol, COUNT(o.observation_date) AS observations
               FROM instruments i LEFT JOIN market_observations o USING(instrument_id)
               WHERE i.symbol IN ({}) GROUP BY i.symbol""".format(",".join("?" for _ in symbols)),
            symbols,
        ).fetchall()
    counts = {row["symbol"]: row["observations"] for row in rows}
    if all(counts.get(symbol, 0) > 0 for symbol in symbols):
        return 0
    if any(counts.values()):
        raise PriceDataError("The demo price database is incomplete; review it before reseeding.")
    aligned = load_with_csv_fallback(None, csv_path, symbols, fallback_source="synthetic_india_demo_v1")
    return load_observations(aligned.observations, source=aligned.source, path=path)
