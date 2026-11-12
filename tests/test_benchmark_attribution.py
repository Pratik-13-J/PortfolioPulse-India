"""Phase 6 framework: Active Share, Brinson-Fachler, Carino linking, data gate.

Textbook-style 3-sector example (single period):
  w_p = [0.5, 0.3, 0.2], w_b = [0.4, 0.4, 0.2]
  R_p,s = [0.10, 0.05, -0.02], R_b,s = [0.08, 0.06, 0.00]
  R_p = 0.061, R_b = 0.056, active = 0.005
  allocation  = [0.1*0.024, -0.1*0.004, 0]        = [0.0024, -0.0004, 0]    sum 0.0020
  selection   = [0.4*0.02, 0.4*-0.01, 0.2*-0.02]  = [0.008, -0.004, -0.004] sum 0.0000
  interaction = [0.1*0.02, -0.1*-0.01, 0]         = [0.002, 0.001, 0]       sum 0.0030
All weights and returns below are fixtures, not market data.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from portfoliopulse.benchmark_attribution import (
    AttributionGateError, active_share, attribution_gate, brinson_fachler, carino_link, gated_benchmark_weights,
    sector_aggregate,
)
from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.reference_data import import_benchmark_constituents, save_security_classifications

SECTORS = ["Financials", "Energy", "IT"]
PORTFOLIO = pd.DataFrame({"weight": [0.5, 0.3, 0.2], "return": [0.10, 0.05, -0.02]}, index=SECTORS)
BENCHMARK = pd.DataFrame({"weight": [0.4, 0.4, 0.2], "return": [0.08, 0.06, 0.00]}, index=SECTORS)


class ActiveShareTests(unittest.TestCase):
    def test_union_of_names(self) -> None:
        # 1/2 * (|0.5-0.3| + |0.5-0| + |0-0.7|) = 0.7
        self.assertAlmostEqual(active_share({"A": 0.5, "B": 0.5}, {"A": 0.3, "C": 0.7}), 0.7, places=15)
        self.assertEqual(active_share({"A": 1.0}, {"A": 1.0}), 0.0)
        self.assertAlmostEqual(active_share({"A": 1.0}, {"B": 1.0}), 1.0, places=15)

    def test_invalid_weights_rejected(self) -> None:
        with self.assertRaises(ValueError):
            active_share({"A": 0.6}, {"A": 1.0})
        with self.assertRaises(ValueError):
            active_share({"A": 1.2, "B": -0.2}, {"A": 1.0})


class BrinsonTests(unittest.TestCase):
    def test_three_sector_textbook_example(self) -> None:
        result = brinson_fachler(PORTFOLIO, BENCHMARK)
        self.assertAlmostEqual(result.portfolio_return, 0.061, places=15)
        self.assertAlmostEqual(result.benchmark_return, 0.056, places=15)
        np.testing.assert_allclose(result.effects.loc[SECTORS, "allocation"], [0.0024, -0.0004, 0.0], atol=1e-15)
        np.testing.assert_allclose(result.effects.loc[SECTORS, "selection"], [0.008, -0.004, -0.004], atol=1e-15)
        np.testing.assert_allclose(result.effects.loc[SECTORS, "interaction"], [0.002, 0.001, 0.0], atol=1e-15)
        self.assertAlmostEqual(result.effects["total"].sum(), result.active_return, places=15)
        self.assertAlmostEqual(result.active_return, 0.005, places=15)

    def test_unheld_and_off_benchmark_sector_conventions(self) -> None:
        portfolio = pd.DataFrame({"weight": [0.6, 0.4], "return": [0.10, 0.20]}, index=["Financials", "Gold"])
        result = brinson_fachler(portfolio, BENCHMARK)
        effects = result.effects
        # Energy/IT not held: no selection or interaction, allocation = -w_b * (R_b,s - R_b).
        self.assertAlmostEqual(effects.at["Energy", "selection"], 0.0)
        self.assertAlmostEqual(effects.at["Energy", "allocation"], -0.4 * (0.06 - 0.056), places=15)
        # Gold is off-benchmark: R_b,s := R_b so its whole effect is interaction.
        self.assertAlmostEqual(effects.at["Gold", "allocation"], 0.0, places=15)
        self.assertAlmostEqual(effects.at["Gold", "interaction"], 0.4 * (0.20 - 0.056), places=15)
        self.assertAlmostEqual(effects["total"].sum(), result.active_return, places=14)

    def test_sector_aggregation(self) -> None:
        weights = pd.Series({"HDFC": 0.3, "ICICI": 0.2, "RIL": 0.5})
        returns = pd.Series({"HDFC": 0.10, "ICICI": 0.05, "RIL": -0.01})
        sectors = {"HDFC": "Financials", "ICICI": "Financials", "RIL": "Energy"}
        frame = sector_aggregate(weights, returns, sectors)
        self.assertAlmostEqual(frame.at["Financials", "weight"], 0.5)
        self.assertAlmostEqual(frame.at["Financials", "return"], (0.03 + 0.01) / 0.5, places=15)
        with self.assertRaises(ValueError):
            sector_aggregate(weights, returns, {"HDFC": "Financials"})


class CarinoTests(unittest.TestCase):
    def test_two_period_linking_reconciles_to_cumulative_active_return(self) -> None:
        first = brinson_fachler(PORTFOLIO, BENCHMARK)
        second = brinson_fachler(
            PORTFOLIO.assign(weight=[0.4, 0.4, 0.2], **{"return": [-0.03, 0.02, 0.04]}),
            BENCHMARK.assign(**{"return": [-0.01, 0.01, 0.03]}),
        )
        linked = carino_link([first, second])
        expected_active = (1.061 * (1 + second.portfolio_return)) - (1.056 * (1 + second.benchmark_return))
        self.assertAlmostEqual(linked.effects["total"].sum(), expected_active, places=14)
        self.assertLess(linked.reconciliation_error, 1e-12)
        # A naive sum of period effects does not reconcile to the geometric active return.
        naive = first.effects["total"].sum() + second.effects["total"].sum()
        self.assertGreater(abs(naive - expected_active), 1e-6)

    def test_single_period_link_is_identity(self) -> None:
        result = brinson_fachler(PORTFOLIO, BENCHMARK)
        linked = carino_link([result])
        pd.testing.assert_frame_equal(linked.effects, result.effects, rtol=0, atol=1e-15)


def _write_constituents(directory: Path, text: str, weight_basis: str) -> Path:
    path = directory / f"constituents_{weight_basis}.csv"
    path.write_bytes(text.encode("utf-8"))
    sidecar = {
        "source_name": "Fixture constituent file (not market data)",
        "source_url": "https://example.org/fixture",
        "retrieved_at_utc": "2026-11-12T10:00:00+00:00",
        "provenance_label": "test_fixture",
        "verification_status": "simulated",
        "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "benchmark_symbol": "FIXTURE INDEX", "as_of_date": "2026-10-31", "weight_basis": weight_basis,
    }
    path.with_suffix(".meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return path


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        self.dir = Path(self._temp.name)
        self.db = self.dir / "gate.db"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_gate_refuses_missing_dataset_and_names_only_file(self) -> None:
        self.assertFalse(attribution_gate(None, db_path=self.db).enabled)
        path = _write_constituents(self.dir, "symbol,industry\nX,Financial Services\nY,IT\n", "none")
        ref = import_benchmark_constituents(path, db_path=self.db).reference_dataset_id
        status = attribution_gate(ref, db_path=self.db)
        self.assertFalse(status.enabled)
        self.assertTrue(any("weight_basis 'none'" in reason for reason in status.reasons))
        with self.assertRaises(AttributionGateError):
            gated_benchmark_weights(ref, db_path=self.db)

    def test_gate_requires_classifications_then_opens_with_label(self) -> None:
        path = _write_constituents(self.dir, "symbol,industry,weight\nX,Financial Services,0.6\nY,IT,0.4\n",
                                   "estimated")
        ref = import_benchmark_constituents(path, db_path=self.db).reference_dataset_id
        status = attribution_gate(ref, db_path=self.db)
        self.assertFalse(status.enabled)
        self.assertIn("classification", " ".join(status.reasons))
        save_security_classifications({"X": ("Financial Services", "Financials"), "Y": ("IT", "IT")},
                                      taxonomy="nse_industry", effective_date="2026-10-31",
                                      source_label="fixture", reference_dataset_id=ref, db_path=self.db)
        weights, status = gated_benchmark_weights(ref, db_path=self.db)
        self.assertTrue(status.enabled)
        self.assertEqual(status.label, "estimated")
        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertAlmostEqual(active_share({"X": 1.0}, weights), 0.4, places=15)
        self.assertFalse(attribution_gate(ref, required_symbols=["Z"], db_path=self.db).enabled)


if __name__ == "__main__":
    unittest.main()
