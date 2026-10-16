"""Phase 4 saved-dataset integration and missing-benchmark drawdown checks."""

from __future__ import annotations

import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from portfoliopulse.angelone.instrument_master import load_config
from portfoliopulse.config import PROJECT_ROOT, REAL_PORTFOLIO_NAME
from portfoliopulse.database import (
    import_candle_dataset, initialize, session, upsert_configured_portfolio,
)
from portfoliopulse.portfolio_analysis import analyze_saved_dataset, compute_phase4_analysis
from scripts.analyze_phase4 import save_phase4_report


CONFIG = load_config(PROJECT_ROOT / "config" / "instruments.json")
DATES = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07")
RETRIEVED = "2026-09-08T08:00:00+00:00"


def _saved_fixture() -> tuple[pd.DataFrame, dict]:
    rows = []
    factors = (1.0, 1.02, 0.98, 1.01, 1.03)
    for day, factor in zip(DATES, factors):
        for position, item in enumerate(CONFIG):
            close = (100.0 + 10 * position) * factor
            rows.append({
                "symbol": item.symbol, "trading_symbol": item.trading_symbol,
                "exchange": item.exchange, "date": day,
                "open": close, "high": close, "low": close, "close": close,
                "volume": 0 if item.is_benchmark else 1000,
                "source": "Hand-checkable NSE fixture", "price_basis": "unadjusted_ohlcv",
                "provenance_kind": "user_supplied_csv", "interval": "ONE_DAY",
                "retrieved_at_utc": RETRIEVED,
            })
    metadata = {
        "source_name": "Hand-checkable NSE fixture",
        "source_url": "https://example.org/source",
        "provenance_kind": "user_supplied_csv",
        "provenance_status": "user_declared_unverified",
        "retrieved_at_utc": RETRIEVED,
        "price_basis": "unadjusted_ohlcv", "interval": "ONE_DAY",
        "start": DATES[0], "end_exclusive": "2026-09-08",
    }
    return pd.DataFrame(rows), metadata


def _hidden_loss_fixture() -> tuple[dict, pd.DataFrame]:
    days = ("2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")
    values = (100, 100, 50, 100, 100, 100)
    candles = []
    for day, close in zip(days, values):
        for symbol, price in (("A", close), ("NIFTY 50", 100)):
            if symbol == "NIFTY 50" and day == "2026-01-05":
                continue
            candles.append({
                "symbol": symbol, "date": day,
                "open": price, "high": price, "low": price, "close": price,
                "volume": 0 if symbol == "NIFTY 50" else 1000,
                "source": "Constant-risk fixture", "price_basis": "unadjusted_ohlcv",
                "interval": "ONE_DAY", "retrieved_at_utc": RETRIEVED,
            })
    info = {
        "dataset_id": 7, "portfolio_name": REAL_PORTFOLIO_NAME,
        "source_name": "Constant-risk fixture",
        "provenance_status": "user_declared_unverified",
        "price_basis": "unadjusted_ohlcv",
        "window_start": days[0], "window_end_exclusive": "2026-01-09",
        "portfolio_snapshot": [
            {"symbol": "A", "quantity": 1.0, "is_benchmark": 0},
            {"symbol": "NIFTY 50", "quantity": 0.0, "is_benchmark": 1},
        ],
    }
    return info, pd.DataFrame(candles)


class Phase4AnalysisTests(unittest.TestCase):
    def test_benchmark_gap_does_not_hide_observed_portfolio_drawdown(self) -> None:
        info, candles = _hidden_loss_fixture()
        result = compute_phase4_analysis(7, info, candles)
        self.assertEqual(len(result.returns.daily_comparison_returns), 3)
        self.assertEqual(result.metrics["portfolio_annualized_volatility"], 0.0)
        self.assertEqual(result.metrics["portfolio_max_drawdown"], -0.5)
        self.assertEqual(result.metrics["portfolio_matched_max_drawdown"], 0.0)
        self.assertEqual(result.returns.cumulative_portfolio_return.loc["2026-01-05"], -0.5)
        self.assertIsNone(result.metrics["portfolio_sharpe"])
        self.assertIsNone(result.metrics["portfolio_benchmark_correlation"])
        self.assertIsNone(result.risk_contributions)
        self.assertIn("asset_volatility_contributions", result.undefined_metrics)

    def test_missing_holding_does_not_hide_observed_benchmark_drawdown(self) -> None:
        info, candles = _hidden_loss_fixture()
        swapped = candles.copy()
        swapped["symbol"] = swapped["symbol"].replace({"A": "NIFTY 50", "NIFTY 50": "A"})
        swapped["volume"] = swapped["symbol"].map({"A": 1000, "NIFTY 50": 0})
        result = compute_phase4_analysis(7, info, swapped)
        self.assertEqual(result.metrics["benchmark_max_drawdown"], -0.5)
        self.assertEqual(result.metrics["benchmark_matched_max_drawdown"], 0.0)

    def test_risk_weights_stop_at_last_shared_price_date(self) -> None:
        candles, metadata = _saved_fixture()
        candles = candles.loc[~((candles["symbol"] == "NIFTY 50") &
                                (candles["date"] == DATES[-1]))].copy()
        final_reliance = (candles["symbol"] == "RELIANCE") & (candles["date"] == DATES[-1])
        candles.loc[final_reliance, ["open", "high", "low", "close"]] *= 2
        info = {
            "dataset_id": 8, "portfolio_name": REAL_PORTFOLIO_NAME,
            "source_name": metadata["source_name"],
            "provenance_status": metadata["provenance_status"],
            "price_basis": metadata["price_basis"],
            "window_start": metadata["start"],
            "window_end_exclusive": metadata["end_exclusive"],
            "portfolio_snapshot": [
                {"symbol": item.symbol, "quantity": item.quantity,
                 "is_benchmark": int(item.is_benchmark)} for item in CONFIG
            ],
        }
        result = compute_phase4_analysis(8, info, candles)
        self.assertEqual(result.risk_weight_date, DATES[-2])
        self.assertEqual(result.returns.market_weights.index[-1], pd.Timestamp(DATES[-1]))
        self.assertNotEqual(
            result.returns.market_weights.loc[DATES[-1], "RELIANCE"],
            result.returns.market_weights.loc[DATES[-2], "RELIANCE"],
        )
        self.assertAlmostEqual(
            result.risk_contributions.loc["RELIANCE", "weight"],
            result.returns.market_weights.loc[DATES[-2], "RELIANCE"],
        )

    def test_saved_analysis_uses_immutable_quantities_and_writes_finite_sql_results(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp, TemporaryDirectory(dir=processed) as output:
            db_path = Path(temp) / "phase4.db"
            initialize(db_path)
            upsert_configured_portfolio(CONFIG, path=db_path)
            candles, metadata = _saved_fixture()
            dataset_id = import_candle_dataset(candles, metadata=metadata, path=db_path)
            first = analyze_saved_dataset(dataset_id, annual_risk_free_rate=0.06, db_path=db_path)
            self.assertEqual(len(first.returns.daily_comparison_returns), 4)
            self.assertAlmostEqual(first.metrics["portfolio_cumulative_price_return"], 0.03)
            self.assertAlmostEqual(first.metrics["benchmark_cumulative_price_return"], 0.03)
            self.assertAlmostEqual(
                first.risk_contributions["component_annual_volatility"].sum(),
                first.metrics["current_weight_proxy_annualized_volatility"],
            )
            changed = tuple(replace(item, quantity=item.quantity + 100) if item.symbol == "RELIANCE"
                            else item for item in CONFIG)
            upsert_configured_portfolio(changed, path=db_path)
            repeated = analyze_saved_dataset(dataset_id, annual_risk_free_rate=0.06, db_path=db_path)
            self.assertEqual(first.analysis_id, repeated.analysis_id)
            self.assertEqual(first.metrics, repeated.metrics)
            report_path = save_phase4_report(repeated, Path(output))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["analysis_id"], first.analysis_id)
            self.assertEqual(report["quality"]["valid_return_dates"], 4)
            self.assertEqual(report["price_basis"], "unadjusted_ohlcv")
            with session(db_path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_metadata").fetchone()[0], 1)
                self.assertGreater(con.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0], 15)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(len(con.execute("PRAGMA foreign_key_check").fetchall()), 0)

    def test_invalid_parameters_fail_before_saving_a_run(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db_path = Path(temp) / "phase4.db"
            initialize(db_path)
            upsert_configured_portfolio(CONFIG, path=db_path)
            candles, metadata = _saved_fixture()
            dataset_id = import_candle_dataset(candles, metadata=metadata, path=db_path)
            with self.assertRaises(ValueError):
                analyze_saved_dataset(dataset_id, annual_risk_free_rate=-1, db_path=db_path)
            with self.assertRaises(ValueError):
                analyze_saved_dataset(dataset_id, confidence=1, db_path=db_path)
            with session(db_path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_metadata").fetchone()[0], 0)

    def test_failed_result_insert_rolls_back_analysis_metadata(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db_path = Path(temp) / "phase4.db"
            initialize(db_path)
            upsert_configured_portfolio(CONFIG, path=db_path)
            candles, metadata = _saved_fixture()
            dataset_id = import_candle_dataset(candles, metadata=metadata, path=db_path)
            with session(db_path) as con:
                con.execute(
                    """CREATE TRIGGER fail_phase4_result BEFORE INSERT ON analysis_results
                       BEGIN SELECT RAISE(ABORT, 'simulated result write error'); END"""
                )
            with self.assertRaises(sqlite3.IntegrityError):
                analyze_saved_dataset(dataset_id, db_path=db_path)
            with session(db_path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_metadata").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
