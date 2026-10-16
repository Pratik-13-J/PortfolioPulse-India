"""Checks for independently sourced, user-supplied Indian daily OHLCV CSVs."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from portfoliopulse.angelone.instrument_master import load_config
from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.sourced_csv import SourcedCsvError, import_sourced_csv


CONFIG = load_config(PROJECT_ROOT / "config" / "instruments.json")
DATES = ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07")


def sample_candles(*, gap: bool = False) -> pd.DataFrame:
    rows = []
    for item in CONFIG:
        for index, day in enumerate(DATES):
            if gap and item.symbol == "ITC" and day == "2026-01-06":
                continue
            close = 100 + index * 5
            rows.append({
                "symbol": item.symbol,
                "trading_symbol": item.trading_symbol,
                "exchange": item.exchange,
                "date": day,
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 0 if item.is_benchmark else 1000,
            })
    return pd.DataFrame(rows)


def write_input(directory: Path, frame: pd.DataFrame, metadata: dict | None = None) -> Path:
    csv_path = directory / "supplied_prices.csv"
    csv_path.write_text(frame.to_csv(index=False), encoding="utf-8")
    sidecar = metadata if metadata is not None else {
        "source_name": "NSE historical daily report supplied by user",
        "source_url": "https://www.nseindia.com/all-reports",
        "retrieved_at_utc": "2026-10-16T09:00:00+00:00",
        "price_basis": "unadjusted_ohlcv",
    }
    csv_path.with_suffix(".meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return csv_path


class SourcedCsvTests(unittest.TestCase):
    def test_valid_source_label_alignment_missingness_and_processed_output(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as input_dir, TemporaryDirectory(dir=processed) as output_dir:
            csv_path = write_input(Path(input_dir), sample_candles(gap=True))
            result = import_sourced_csv(csv_path, CONFIG, "2026-01-02", "2026-01-08", output_dir=Path(output_dir))
            self.assertEqual(result.metadata["provenance_kind"], "user_supplied_csv")
            self.assertEqual(result.metadata["provenance_status"], "user_declared_unverified")
            self.assertEqual(result.metadata["source_name"], "NSE historical daily report supplied by user")
            self.assertEqual(result.metadata["price_basis"], "unadjusted_ohlcv")
            self.assertEqual(result.metadata["valid_candle_rows"], 23)
            self.assertEqual(result.metadata["incomplete_union_dates"], 1)
            self.assertEqual(len(result.aligned.prices), 3)
            self.assertEqual(result.aligned.daily_returns.index.strftime("%Y-%m-%d").tolist(), ["2026-01-05"])
            self.assertEqual(int(result.aligned.coverage.set_index("symbol").loc["ITC", "missing_on_union_dates"]), 1)
            self.assertEqual(set(result.candles["source"]), {"NSE historical daily report supplied by user"})
            self.assertTrue(result.output_csv.is_file())
            self.assertTrue(result.metadata_path.is_file())
            self.assertTrue(result.aligned_prices_csv.is_file())
            self.assertTrue(result.daily_returns_csv.is_file())
            self.assertTrue(result.coverage_csv.is_file())
            self.assertTrue(result.output_csv.resolve().is_relative_to(processed.resolve()))
            digest = hashlib.sha256(result.output_csv.read_bytes()).hexdigest()
            self.assertEqual(result.metadata["output_sha256"][result.output_csv.name], digest)

    def test_rejects_bad_provenance_or_checksum_without_output(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        bad_metadata = [
            {"source_name": "NSE", "source_url": "https://www.nseindia.com/all-reports",
             "retrieved_at_utc": "2026-10-16T09:00:00+00:00"},
            {"source_name": "NSE", "source_url": "https://user:secret@nseindia.com/report",
             "retrieved_at_utc": "2026-10-16T09:00:00+00:00", "price_basis": "unadjusted_ohlcv"},
            {"source_name": "NSE", "source_url": "https://www.nseindia.com/report?access_token=secret",
             "retrieved_at_utc": "2026-10-16T09:00:00+00:00", "price_basis": "unadjusted_ohlcv"},
            {"source_name": "NSE", "source_url": "https://www.nseindia.com/report#private",
             "retrieved_at_utc": "2026-10-16T09:00:00+00:00", "price_basis": "unadjusted_ohlcv"},
            {"source_name": "NSE", "source_url": "https://www.nseindia.com/all-reports",
             "retrieved_at_utc": "2026-10-16T09:00:00+05:30", "price_basis": "unadjusted_ohlcv"},
            {"source_name": "NSE", "source_url": "https://www.nseindia.com/all-reports",
             "retrieved_at_utc": "2026-10-16T09:00:00+00:00", "price_basis": "unadjusted_ohlcv",
             "csv_sha256": "0" * 64},
        ]
        for supplied in bad_metadata:
            with self.subTest(supplied=supplied), TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as input_dir, TemporaryDirectory(dir=processed) as output_dir:
                csv_path = write_input(Path(input_dir), sample_candles(), supplied)
                with self.assertRaises(SourcedCsvError):
                    import_sourced_csv(csv_path, CONFIG, "2026-01-02", "2026-01-08", output_dir=Path(output_dir))
                self.assertEqual(list(Path(output_dir).iterdir()), [])

    def test_rejects_duplicate_bad_ohlcv_date_and_wrong_universe(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        good = sample_candles()
        cases = []
        cases.append(pd.concat([good, good.iloc[[0]]], ignore_index=True))
        high_low = good.copy()
        high_low.loc[0, "high"] = 0
        cases.append(high_low)
        negative_volume = good.copy()
        negative_volume.loc[0, "volume"] = -1
        cases.append(negative_volume)
        bad_date = good.copy()
        bad_date.loc[0, "date"] = "2026-02-30"
        cases.append(bad_date)
        wrong_symbol = good.copy()
        wrong_symbol.loc[0, "symbol"] = "DEMO_ENERGY"
        cases.append(wrong_symbol)
        missing_asset = good.loc[good["symbol"] != "ITC"].copy()
        cases.append(missing_asset)
        for bad in cases:
            with self.subTest(rows=len(bad)), TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as input_dir, TemporaryDirectory(dir=processed) as output_dir:
                csv_path = write_input(Path(input_dir), bad)
                with self.assertRaises(SourcedCsvError):
                    import_sourced_csv(csv_path, CONFIG, "2026-01-02", "2026-01-08", output_dir=Path(output_dir))
                self.assertEqual(list(Path(output_dir).iterdir()), [])

    def test_window_excludes_outside_rows_and_output_path_is_restricted(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as input_dir, TemporaryDirectory(dir=processed) as output_dir:
            csv_path = write_input(Path(input_dir), sample_candles())
            result = import_sourced_csv(csv_path, CONFIG, "2026-01-05", "2026-01-08", output_dir=Path(output_dir))
            self.assertEqual(result.metadata["input_rows_excluded_outside_window"], len(CONFIG))
            self.assertEqual(len(result.candles), 3 * len(CONFIG))
            with self.assertRaisesRegex(SourcedCsvError, "data/processed"):
                import_sourced_csv(csv_path, CONFIG, "2026-01-05", "2026-01-08", output_dir=Path(input_dir))


if __name__ == "__main__":
    unittest.main()
