"""Provider protocol adapters and SHA-256-sidecar reference importers."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from portfoliopulse.angelone.instrument_master import load_config
from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.providers import (
    AngelOneProvider, DailyCandleProvider, ProvenanceRecord, SourcedCsvProvider, YahooResearchProvider,
    require_explicit_provider,
)
from portfoliopulse.reference_data import (
    ReferenceDataError, import_benchmark_constituents, import_corporate_actions, load_benchmark_constituents,
    load_corporate_actions, load_security_classifications, parse_constituents, save_security_classifications,
)
from tests.test_angelone_client import FakeClient, candle, instrument
from tests.test_sourced_csv import sample_candles, write_input

CONFIG = load_config(PROJECT_ROOT / "config" / "instruments.json")


def write_reference(directory: Path, name: str, csv_text: str, **sidecar) -> Path:
    path = directory / name
    path.write_bytes(csv_text.encode("utf-8"))
    meta = {
        "source_name": "NSE corporate actions (user download)",
        "source_url": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
        "retrieved_at_utc": "2026-11-12T10:00:00Z",
        "provenance_label": "nse_corporate_actions",
        "file_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
        **sidecar,
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


CA_CSV = (
    "symbol,ex_date,action_type,ratio_new,ratio_old,cash_amount_inr,description\n"
    "HDFCBANK,2025-08-26,bonus,2,1,,1:1 bonus\n"
    "RELIANCE,2024-10-28,bonus,2,1,,1:1 bonus\n"
    "ITC,2025-05-28,dividend,,,7.85,Final dividend\n"
)


class ProviderTests(unittest.TestCase):
    def test_angel_provider_wraps_fetch_and_labels_verified_live(self) -> None:
        client = FakeClient([{"status": True, "data": [candle("2026-01-02"), candle("2026-01-05")]}])
        provider = AngelOneProvider(client, [instrument()], request_delay_seconds=0, sleeper=lambda _: None)
        self.assertIsInstance(provider, DailyCandleProvider)
        result = provider.fetch("2026-01-01", "2026-01-06")
        self.assertEqual(len(result.candles), 2)
        self.assertEqual((result.provenance.provenance_kind, result.provenance.price_basis,
                          result.provenance.verification_status, result.provenance.tier),
                         ("angel_one_smartapi", "unadjusted_ohlcv", "verified_live", "primary"))
        self.assertEqual(len(result.raw), 1)

    def test_sourced_csv_provider_is_a_labelled_fallback(self) -> None:
        processed = PROJECT_ROOT / "data" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp, TemporaryDirectory(dir=processed) as out:
            csv_path = write_input(Path(temp), sample_candles())
            provider = SourcedCsvProvider(csv_path, CONFIG, output_dir=Path(out),
                                          verification_status="fallback_unverified")
            result = provider.fetch("2026-01-02", "2026-01-08")
        self.assertEqual(result.provenance.tier, "fallback")
        self.assertEqual(result.provenance.verification_status, "fallback_unverified")
        self.assertEqual(result.provenance.provenance_kind, "user_supplied_csv")
        with self.assertRaises(ValueError):
            SourcedCsvProvider(Path("x.csv"), CONFIG, verification_status="verified_live")

    def test_yahoo_provider_uses_injected_downloader(self) -> None:
        index = pd.to_datetime(["2026-01-02", "2026-01-05"])
        raw = pd.DataFrame({("Close", "RELIANCE.NS"): [100.0, 101.0]}, index=index)
        raw.columns = pd.MultiIndex.from_tuples(raw.columns)
        provider = YahooResearchProvider(["RELIANCE.NS"], downloader=lambda **_: raw)
        result = provider.fetch("2026-01-02", "2026-01-06")
        self.assertEqual(result.provenance.verification_status, "fallback_unverified")
        self.assertEqual(len(result.candles), 2)

    def test_no_silent_substitution_and_record_guards(self) -> None:
        provider = AngelOneProvider(FakeClient([]), [instrument()])
        with self.assertRaises(ValueError):
            require_explicit_provider("sourced_csv", [provider])
        self.assertIs(require_explicit_provider("angel_one_smartapi", [provider]), provider)
        with self.assertRaises(ValueError):
            ProvenanceRecord("x", "user_supplied_csv", "unadjusted_ohlcv", "verified_live", "x", "t")
        with self.assertRaises(ValueError):
            ProvenanceRecord("x", "user_supplied_csv", "total_return", "simulated", "x", "t")


class ReferenceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        self.dir = Path(self._temp.name)
        self.db = self.dir / "ref.db"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_corporate_actions_import_is_checksum_verified_and_idempotent(self) -> None:
        path = write_reference(self.dir, "ca.csv", CA_CSV)
        first = import_corporate_actions(path, db_path=self.db)
        again = import_corporate_actions(path, db_path=self.db)
        self.assertTrue(first.created)
        self.assertFalse(again.created)
        self.assertEqual(first.reference_dataset_id, again.reference_dataset_id)
        actions = load_corporate_actions(first.reference_dataset_id, db_path=self.db)
        self.assertEqual(len(actions), 3)
        hdfc = actions.loc[actions["symbol"].eq("HDFCBANK")].iloc[0]
        self.assertEqual((hdfc["ratio_new"], hdfc["ratio_old"]), (2.0, 1.0))

    def test_checksum_mismatch_and_bad_rows_are_rejected(self) -> None:
        path = write_reference(self.dir, "ca.csv", CA_CSV)
        path.write_bytes(CA_CSV.replace("7.85", "8.00").encode("utf-8"))
        with self.assertRaises(ReferenceDataError):
            import_corporate_actions(path, db_path=self.db)
        bad = write_reference(self.dir, "bad.csv", "symbol,ex_date,action_type\nINFY,2025-01-01,bonus\n")
        with self.assertRaises(ReferenceDataError):  # bonus without ratio
            import_corporate_actions(bad, db_path=self.db)
        live = write_reference(self.dir, "live.csv", CA_CSV, verification_status="verified_live")
        with self.assertRaises(ReferenceDataError):
            import_corporate_actions(live, db_path=self.db)

    def test_constituents_with_weights_must_sum_to_one(self) -> None:
        good = "symbol,company_name,industry,weight\nA,A Ltd,Financial Services,0.6\nB,B Ltd,Oil Gas,0.39995\n"
        parsed = parse_constituents(good.encode(), "official")
        self.assertAlmostEqual(parsed["weight"].sum(), 0.99995)
        with self.assertRaises(ReferenceDataError):
            parse_constituents(good.replace("0.39995", "0.3998").encode(), "official")
        with self.assertRaises(ReferenceDataError):
            parse_constituents("symbol,industry,weight\nA,X,0.5\nB,Y,\n".encode(), "estimated")
        with self.assertRaises(ReferenceDataError):
            parse_constituents(good.encode(), "none")

    def test_names_only_constituents_import_with_basis_none(self) -> None:
        text = "symbol,company_name,industry\nRELIANCE,Reliance Industries,Oil Gas & Consumable Fuels\n"
        path = write_reference(self.dir, "nifty.csv", text, benchmark_symbol="NIFTY 50", as_of_date="2026-10-31",
                               weight_basis="none", provenance_label="nse_indices_official",
                               source_url="https://www.niftyindices.com/indices/equity/broad-based-indices/nifty--50")
        result = import_benchmark_constituents(path, db_path=self.db)
        frame = load_benchmark_constituents(result.reference_dataset_id, db_path=self.db)
        self.assertTrue(frame["weight"].isna().all())
        self.assertEqual(frame.loc[0, "weight_basis"], "none")

    def test_security_classifications_keep_history(self) -> None:
        save_security_classifications({"RELIANCE": ("Energy", "Energy")}, taxonomy="project_sector",
                                      effective_date="2024-01-01", source_label="config/instruments.json",
                                      db_path=self.db)
        save_security_classifications({"RELIANCE": ("Oil Gas & Consumable Fuels", "Energy")},
                                      taxonomy="nse_industry", effective_date="2026-10-31",
                                      source_label="nse_indices_official", db_path=self.db)
        self.assertEqual(load_security_classifications("nse_industry", as_of_date="2026-01-01", db_path=self.db), {})
        self.assertEqual(load_security_classifications("nse_industry", as_of_date="2026-11-01", db_path=self.db),
                         {"RELIANCE": ("Oil Gas & Consumable Fuels", "Energy")})


if __name__ == "__main__":
    unittest.main()
