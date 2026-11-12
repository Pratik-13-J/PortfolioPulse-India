"""NSE corporate-action parsing, ex-date evidence and adjusted dataset versions.

All CSV rows and prices here are hand-made test fixtures, not NSE data.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, import_candle_dataset, initialize, upsert_configured_portfolio,
)
from portfoliopulse.nse_corporate_actions import convert_nse_csv, parse_purpose, write_project_files
from portfoliopulse.price_basis_versioning import (
    EventDecision, apply_adjustments, build_adjusted_version, classify_events, validate_version,
)
from portfoliopulse.reference_data import ReferenceDataError, import_corporate_actions
from tests.test_phase3_risk_analysis import CONFIG, _synthetic

NSE_FIXTURE = (
    "SYMBOL,COMPANY NAME,SERIES,PURPOSE,FACE VALUE,EX-DATE,RECORD DATE\n"
    "RELIANCE,Test Co A,EQ,Bonus 1:1,10,05-Feb-2025,05-Feb-2025\n"
    "ITC,Test Co B,EQ,Demerger,1,13-Feb-2025,13-Feb-2025\n"
    "ITC,Test Co B,EQ,Interim Dividend - Rs 6.50 Per Share,1,20-Feb-2025,20-Feb-2025\n"
    "ITC,Test Co B,EQ,Special Dividend - Rs 1 Per Share,1,20-Feb-2025,20-Feb-2025\n"
    "INFY,Test Co C,EQ,Annual General Meeting/Dividend - Rs 20 Per Share,5,27-Feb-2025,27-Feb-2025\n"
    "INFY,Test Co C,EQ,Annual General Meeting,5,-,-\n"
    "HDFCBANK,Test Co D,EQ,Dividend - Rs 19.50 Per Share,1,15-Jun-2023,15-Jun-2023\n"
    "ICICIBANK,Test Co E,N1,Interest Payment,100,10-Feb-2025,10-Feb-2025\n"
    "OTHERCO,Test Co F,EQ,Bonus 1:1,10,05-Feb-2025,05-Feb-2025\n"
)
SYMBOLS = ["HDFCBANK", "ICICIBANK", "INFY", "ITC", "RELIANCE"]


class PurposeParsingTests(unittest.TestCase):
    def test_ratios_amounts_and_review(self):
        self.assertEqual(parse_purpose("Bonus 1:1")[0], {"action_type": "bonus", "ratio_new": 2.0, "ratio_old": 1.0,
                                                         "description": "Bonus 1:1"})
        bonus = parse_purpose("BONUS 3:2")[0]
        self.assertEqual((bonus["ratio_new"], bonus["ratio_old"]), (5.0, 2.0))  # 3 new per 2 held: 2 -> 5 shares
        split = parse_purpose("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share")[0]
        self.assertEqual((split["action_type"], split["ratio_new"], split["ratio_old"]), ("split", 10.0, 1.0))
        self.assertEqual(parse_purpose("Interim Dividend - Rs 8 Per Share")[0]["cash_amount_inr"], 8.0)
        self.assertEqual([a["action_type"] for a in parse_purpose("Annual General Meeting/Dividend - Rs 10 Per Share")],
                         ["dividend"])
        self.assertEqual(parse_purpose("Annual General Meeting"), [])
        self.assertEqual(parse_purpose("Demerger")[0]["action_type"], "demerger")
        self.assertIsNone(parse_purpose("Buyback"))
        self.assertIsNone(parse_purpose("Bonus"))  # no ratio: never guessed
        self.assertIsNone(parse_purpose("Face Value Split From Rs 1 To Rs 10"))  # consolidation, not a split


class ConversionTests(unittest.TestCase):
    def test_convert_filters_merges_and_routes_review(self):
        result = convert_nse_csv(NSE_FIXTURE.encode(), symbols=SYMBOLS, window=("2024-01-01", "2026-10-16"))
        actions = result.actions.set_index(["symbol", "ex_date", "action_type"])
        self.assertEqual(actions.loc[("RELIANCE", "2025-02-05", "bonus"), ["ratio_new", "ratio_old"]].tolist(), [2.0, 1.0])
        self.assertAlmostEqual(actions.loc[("ITC", "2025-02-20", "dividend"), "cash_amount_inr"], 7.5)  # 6.50 + 1
        self.assertIn(("ITC", "2025-02-13", "demerger"), actions.index)
        self.assertIn(("INFY", "2025-02-27", "dividend"), actions.index)
        self.assertNotIn("HDFCBANK", set(result.actions["symbol"]))  # 2023 ex-date is outside the window
        self.assertNotIn("OTHERCO", set(result.actions["symbol"]))
        self.assertTrue(result.review.empty)
        self.assertIn("meeting notice", result.ignored["reason"].tolist())  # undated AGM notice ignored
        self.assertIn("series N1", result.ignored["reason"].tolist())
        self.assertEqual(result.raw_rows, 9)
        self.assertEqual(len(result.raw_sha256), 64)

    def test_write_refuses_pending_review_then_imports(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            buyback = "ITC,Test Co B,EQ,Buyback,1,10-Mar-2025,10-Mar-2025" + chr(10)
            pending = convert_nse_csv((NSE_FIXTURE + buyback).encode(),
                                      symbols=SYMBOLS, window=("2024-01-01", "2026-10-16"))
            self.assertEqual(pending.review["purpose"].tolist(), ["Buyback"])
            with self.assertRaises(ReferenceDataError):
                write_project_files(pending, Path(temp) / "ca.csv", source_url="https://www.nseindia.com/test",
                                    retrieved_at_utc="2026-11-13T00:00:00+00:00", raw_file_name="raw.csv")
            ready = convert_nse_csv(NSE_FIXTURE.encode(), symbols=SYMBOLS, window=("2024-01-01", "2026-10-16"))
            csv_path, meta = write_project_files(ready, Path(temp) / "ca.csv",
                                                 source_url="https://www.nseindia.com/test",
                                                 retrieved_at_utc="2026-11-13T00:00:00+00:00", raw_file_name="raw.csv")
            sidecar = json.loads(meta.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["raw_file_sha256"], ready.raw_sha256)
            db = Path(temp) / "ref.db"
            imported = import_corporate_actions(csv_path, db_path=db)
            self.assertEqual(imported.row_count, len(ready.actions))


def _grid(unadjusted: bool) -> pd.DataFrame:
    dates = pd.bdate_range("2025-02-03", periods=5)  # ex-date 2025-02-05 is the third row
    stock = [100.0, 101.0, 51.0, 52.0, 51.5] if unadjusted else [50.0, 50.5, 51.0, 52.0, 51.5]
    return pd.DataFrame({"RELIANCE": stock, "NIFTY 50": [1000.0, 1010.0, 1020.0, 1030.0, 1025.0]}, index=dates)


class ClassificationTests(unittest.TestCase):
    ACTIONS = pd.DataFrame([
        {"symbol": "RELIANCE", "ex_date": "2025-02-05", "action_type": "bonus", "ratio_new": 2.0, "ratio_old": 1.0},
        {"symbol": "RELIANCE", "ex_date": "2025-02-06", "action_type": "dividend", "ratio_new": None, "ratio_old": None,
         "cash_amount_inr": 0.5},
        {"symbol": "RELIANCE", "ex_date": "2025-02-04", "action_type": "demerger", "ratio_new": None, "ratio_old": None},
        {"symbol": "RELIANCE", "ex_date": "2026-01-01", "action_type": "bonus", "ratio_new": 2.0, "ratio_old": 1.0},
    ])

    def test_unadjusted_and_adjusted_bonus(self):
        # Unadjusted: 101 -> 51 is -49.5%; expected 0.5 x (1 + 0.0099) - 1 = -49.5%; NIFTY +0.99%.
        raw = classify_events(_grid(True), "NIFTY 50", self.ACTIONS).set_index(["ex_date", "action_type"])
        self.assertEqual(raw.loc[("2025-02-05", "bonus"), "classification"], "unadjusted")
        self.assertAlmostEqual(raw.loc[("2025-02-05", "bonus"), "stock_return"], 51 / 101 - 1, places=14)
        adjusted = classify_events(_grid(False), "NIFTY 50", self.ACTIONS).set_index(["ex_date", "action_type"])
        self.assertEqual(adjusted.loc[("2025-02-05", "bonus"), "classification"], "already_adjusted")
        self.assertEqual(adjusted.loc[("2025-02-04", "demerger"), "classification"], "requires_value_source")
        self.assertEqual(adjusted.loc[("2025-02-06", "dividend"), "classification"], "price_return_not_adjusted")
        self.assertAlmostEqual(adjusted.loc[("2025-02-06", "dividend"), "dividend_yield_on_previous_close"], 0.5 / 51)
        self.assertEqual(adjusted.loc[("2026-01-01", "bonus"), "classification"], "outside_dataset_window")

    def test_apply_and_validate(self):
        candles = _grid(True).reset_index(names="date").melt(id_vars="date", var_name="symbol", value_name="close")
        candles = candles.assign(open=candles["close"], high=candles["close"], low=candles["close"], volume=100.0)
        decision = EventDecision("RELIANCE", "2025-02-05", "bonus", "adjust", 0.5)
        version = apply_adjustments(candles, [decision])
        rel = version[version["symbol"] == "RELIANCE"].set_index("date")["close"]
        self.assertEqual(rel.tolist(), [50.0, 50.5, 51.0, 52.0, 51.5])
        self.assertEqual(version.loc[(version["symbol"] == "RELIANCE") & (version["date"] < "2025-02-05"),
                                     "volume"].tolist(), [200.0, 200.0])
        self.assertEqual(validate_version(candles, version, [decision])["rows_scaled"], 2)
        with self.assertRaises(ValueError):
            validate_version(candles, version, [])  # undeclared scaling is detected


class VersionBuildTests(unittest.TestCase):
    def setUp(self):
        (PROJECT_ROOT / ".tmp").mkdir(exist_ok=True)
        self._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        self.temp = Path(self._temp.name)
        self.db = self.temp / "version.db"
        initialize(self.db)
        upsert_configured_portfolio(CONFIG, path=self.db)
        candles, metadata = _synthetic(40)
        ex = sorted(candles["date"].unique())[20]
        self.ex = ex
        unadjusted = candles["symbol"].eq("RELIANCE") & candles["date"].lt(ex)
        for column in ("open", "high", "low", "close"):
            candles.loc[unadjusted, column] = candles.loc[unadjusted, column] * 2  # synthetic 1:1 bonus gap
        self.base_id = import_candle_dataset(candles, metadata=metadata, path=self.db)
        csv = self.temp / "ca.csv"
        csv.write_text("symbol,ex_date,action_type,ratio_new,ratio_old,cash_amount_inr,description\n"
                       f"RELIANCE,{ex},bonus,2,1,,test fixture bonus\n"
                       f"ITC,{ex},demerger,,,,test fixture demerger\n", encoding="utf-8")
        import hashlib
        csv.with_suffix(".meta.json").write_text(json.dumps({
            "source_name": "test fixture", "source_url": "https://example.org/ca", "provenance_label": "test",
            "retrieved_at_utc": "2026-11-13T00:00:00+00:00",
            "file_sha256": hashlib.sha256(csv.read_bytes()).hexdigest()}), encoding="utf-8")
        self.reference_id = import_corporate_actions(csv, db_path=self.db).reference_dataset_id

    def tearDown(self):
        self._temp.cleanup()

    def _base_state(self):
        with closing(sqlite3.connect(self.db)) as con:
            return (con.execute("SELECT * FROM candle_datasets WHERE dataset_id=?", (self.base_id,)).fetchall(),
                    con.execute("SELECT * FROM daily_candles WHERE dataset_id=? ORDER BY 2,3", (self.base_id,)).fetchall())

    def test_builds_new_version_and_leaves_base_untouched(self):
        before = self._base_state()
        grid = get_candle_dataset(self.base_id, path=self.db).pivot(index="date", columns="symbol", values="close")
        evidence = classify_events(grid, "NIFTY 50", pd.DataFrame([
            {"symbol": "RELIANCE", "ex_date": self.ex, "action_type": "bonus", "ratio_new": 2.0, "ratio_old": 1.0}]))
        self.assertEqual(evidence["classification"].iloc[0], "unadjusted")
        decisions = [EventDecision("RELIANCE", self.ex, "bonus", "adjust", 0.5),
                     EventDecision("ITC", self.ex, "demerger", "leave_unadjusted")]
        new_id = build_adjusted_version(self.base_id, self.reference_id, decisions, db_path=self.db)
        self.assertNotEqual(new_id, self.base_id)
        self.assertEqual(self._base_state(), before)
        info = get_dataset_info(new_id, path=self.db)
        self.assertEqual(info["price_basis"], "split_adjusted_ohlcv")
        self.assertEqual(info["verification_status"], get_dataset_info(self.base_id, path=self.db)["verification_status"])
        base = get_candle_dataset(self.base_id, path=self.db)
        new = get_candle_dataset(new_id, path=self.db)
        pre = (base["symbol"] == "RELIANCE") & (base["date"] < pd.Timestamp(self.ex))
        np.testing.assert_allclose(new.loc[pre.to_numpy(), "close"].to_numpy(), base.loc[pre, "close"].to_numpy() * 0.5)
        np.testing.assert_array_equal(new.loc[~pre.to_numpy(), "close"].to_numpy(), base.loc[~pre, "close"].to_numpy())
        with closing(sqlite3.connect(self.db)) as con:
            events = con.execute("SELECT event_type, old_value, new_value, reference_dataset_id, evidence_json "
                                 "FROM dataset_provenance_events WHERE dataset_id=?", (new_id,)).fetchall()
        lineage = [e for e in events if e[0] == "corporate_action_review"]
        self.assertEqual(len(lineage), 1)
        self.assertEqual(json.loads(lineage[0][4])["derived_from_dataset_id"], self.base_id)
        again = build_adjusted_version(self.base_id, self.reference_id, decisions, db_path=self.db)
        self.assertEqual(again, new_id)
        with closing(sqlite3.connect(self.db)) as con:
            count = con.execute("SELECT COUNT(*) FROM dataset_provenance_events WHERE dataset_id=? AND "
                                "event_type='corporate_action_review'", (new_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_refuses_incomplete_or_wrong_decisions(self):
        before = self._base_state()
        for decisions in (
            [],  # bonus left undecided
            [EventDecision("RELIANCE", self.ex, "bonus", "adjust", 0.25)],  # wrong factor
            [EventDecision("RELIANCE", self.ex, "bonus", "adjust", 0.5),
             EventDecision("ITC", self.ex, "demerger", "adjust", 0.9)],  # demerger cannot be adjusted here
            [EventDecision("RELIANCE", self.ex, "bonus", "adjust", 0.5),
             EventDecision("INFY", "2025-01-01", "bonus", "adjust", 0.5)],  # no such reference action
        ):
            with self.assertRaises(ValueError):
                build_adjusted_version(self.base_id, self.reference_id, decisions, db_path=self.db)
        self.assertEqual(self._base_state(), before)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM candle_datasets").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
