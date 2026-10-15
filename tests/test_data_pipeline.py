"""Phase 2 checks for the offline Indian demo data path."""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from portfoliopulse.bootstrap import ensure_demo_data
from portfoliopulse.config import DEMO_CSV_PATH, PROJECT_ROOT
from portfoliopulse.data import PriceDataError, align_prices, load_price_csv, load_with_csv_fallback
from portfoliopulse.database import get_holdings, load_observations, session
from portfoliopulse.demo import DEMO_INSTRUMENTS, DEMO_SOURCE


class PricePipelineTests(unittest.TestCase):
    def test_checked_in_demo_has_all_instruments_and_common_dates(self) -> None:
        symbols = [item.symbol for item in DEMO_INSTRUMENTS]
        aligned = load_with_csv_fallback(None, DEMO_CSV_PATH, symbols, fallback_source=DEMO_SOURCE)
        self.assertTrue(aligned.used_fallback)
        self.assertEqual(aligned.source, DEMO_SOURCE)
        self.assertEqual(set(aligned.observations["symbol"]), set(symbols))
        self.assertEqual(aligned.dropped_price_dates, 0)
        self.assertEqual(len(aligned.prices), 717)
        self.assertEqual(len(aligned.daily_returns), 716)
        self.assertEqual(set(aligned.coverage["missing_on_union_dates"]), {0})

    def test_rejects_invalid_duplicate_or_missing_fields(self) -> None:
        bad_files = [
            "symbol,date,close_value\nA,2026-01-01,0\n",
            "symbol,date,close_value\nA,2026-01-01,inf\n",
            "symbol,date,close_value\nA,wrong,100\n",
            "symbol,date,close_value\n,2026-01-01,100\n",
            "symbol,date,close_value\nA,2026-01-01,100\nA,2026-01-01,101\n",
            "symbol,date\nA,2026-01-01\n",
        ]
        for csv_text in bad_files:
            with self.subTest(csv_text=csv_text), self.assertRaises(PriceDataError):
                load_price_csv(StringIO(csv_text))

    def test_gap_is_not_forward_filled_or_bridged_in_returns(self) -> None:
        csv_text = (
            "symbol,date,close_value\n"
            "A,2026-01-01,100\nB,2026-01-01,100\n"
            "A,2026-01-02,110\nB,2026-01-02,110\n"
            "A,2026-01-03,120\n"
            "A,2026-01-04,130\nB,2026-01-04,130\n"
        )
        aligned = align_prices(load_price_csv(StringIO(csv_text)), ["A", "B"], source="test")
        self.assertEqual(aligned.dropped_price_dates, 1)
        self.assertEqual(len(aligned.prices), 3)
        self.assertEqual(aligned.daily_returns.index.strftime("%Y-%m-%d").tolist(), ["2026-01-02"])
        self.assertEqual(int(aligned.coverage.set_index("symbol").loc["B", "missing_on_union_dates"]), 1)

    def test_fallback_only_for_absent_primary(self) -> None:
        symbols = [item.symbol for item in DEMO_INSTRUMENTS]
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            primary = Path(directory) / "prices.csv"
            fallback = load_with_csv_fallback(primary, DEMO_CSV_PATH, symbols, fallback_source=DEMO_SOURCE)
            self.assertTrue(fallback.used_fallback)
            primary.write_text("symbol,date,close_value\nDEMO_ENERGY,2026-01-01,not-a-price\n", encoding="utf-8")
            with self.assertRaises(PriceDataError):
                load_with_csv_fallback(primary, DEMO_CSV_PATH, symbols, fallback_source=DEMO_SOURCE)


class DatabaseSeedTests(unittest.TestCase):
    def test_seed_is_idempotent_and_keeps_sector_metadata(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            db_path = Path(directory) / "portfolio.db"
            count = ensure_demo_data(db_path)
            self.assertEqual(count, 717 * len(DEMO_INSTRUMENTS))
            self.assertEqual(ensure_demo_data(db_path), 0)
            holdings = get_holdings(db_path)
            self.assertEqual(len(holdings), 8)
            self.assertAlmostEqual(float(holdings["target_weight"].sum()), 1.0)
            self.assertEqual(len(set(holdings["sector"])), 8)
            with session(db_path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0], count)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM market_observations WHERE source=?", (DEMO_SOURCE,)).fetchone()[0], count)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM instruments WHERE asset_class='Benchmark'").fetchone()[0], 1)

    def test_invalid_import_does_not_change_existing_prices(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            db_path = Path(directory) / "portfolio.db"
            ensure_demo_data(db_path)
            invalid = pd.DataFrame({"symbol": ["DEMO_ENERGY"], "date": ["2026-09-30"], "close_value": [float("inf")]})
            with self.assertRaises(PriceDataError):
                load_observations(invalid, source="uploaded:test.csv", path=db_path)
            with session(db_path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0], 717 * len(DEMO_INSTRUMENTS))


if __name__ == "__main__":
    unittest.main()
