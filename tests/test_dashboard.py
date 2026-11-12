"""Conversation 6 dashboard: read-only loaders, report export and Streamlit AppTest renders."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import unittest
import zipfile
from unittest import mock
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from streamlit.testing.v1 import AppTest

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.database import import_candle_dataset, initialize, upsert_configured_portfolio
from portfoliopulse.optimization_analysis import analyze_saved_dataset_optimization, load_config
from portfoliopulse.reporting import LABELS, csv_bundle, html_report, provenance_rows
from portfoliopulse.risk_analysis import analyze_saved_dataset_risk
from portfoliopulse.stress_analysis import analyze_saved_dataset_stress
from portfoliopulse.ui import charts
from portfoliopulse.ui.stored_results import latest_run, optimization_view, risk_view, stress_view
from tests.test_phase3_risk_analysis import CONFIG, _synthetic

APP = str(PROJECT_ROOT / "app.py")


def _fingerprint(db: Path) -> list[tuple]:
    with closing(sqlite3.connect(db)) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return [(t, con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables]


def _run_app(db: Path, *, timeout: int = 180) -> AppTest:
    """Render the app against ``db``. Callers must hold ``_pointed_at(db)`` across reruns too."""
    if os.environ.get("PORTFOLIOPULSE_DB") != str(db):
        raise AssertionError("PORTFOLIOPULSE_DB must point at the test database before rendering the app.")
    app = AppTest.from_file(APP, default_timeout=timeout)
    app.run()
    return app


def _pointed_at(db: Path):
    # Keep the override for every rerun (button clicks re-execute app.py) so the
    # real data/portfoliopulse.db is never opened by tests.
    return mock.patch.dict(os.environ, {"PORTFOLIOPULSE_DB": str(db)})


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PROJECT_ROOT / ".tmp").mkdir(exist_ok=True)
        cls._temp = TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")
        temp = Path(cls._temp.name)
        cls.db = temp / "dash.db"
        initialize(cls.db)
        upsert_configured_portfolio(CONFIG, path=cls.db)
        candles, metadata = _synthetic(320)
        cls.dataset_id = import_candle_dataset(candles, metadata=metadata, path=cls.db)
        cls.risk = analyze_saved_dataset_risk(cls.dataset_id, db_path=cls.db, mc_paths=10_000, mc_batches=20)
        cls.stress = analyze_saved_dataset_stress(cls.dataset_id, db_path=cls.db, mc_paths=10_000, mc_batches=20)
        config, _ = load_config()
        config.update(minimum_observations=200, walk_forward={"estimation_observations": 200,
                                                               "holding_observations": 21})
        cls.config_path = temp / "opt.json"
        cls.config_path.write_text(json.dumps(config), encoding="utf-8")
        cls.optimization = analyze_saved_dataset_optimization(cls.dataset_id, db_path=cls.db,
                                                              config_path=cls.config_path)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_loaders_match_engine_outputs(self):
        run = latest_run(self.dataset_id, "risk", db_path=self.db)
        self.assertEqual(run.analysis_id, self.risk.analysis_id)
        view = risk_view(run)
        row = view["var_table"][(view["var_table"]["series"] == "portfolio") &
                                (view["var_table"]["method"] == "historical") &
                                (view["var_table"]["confidence"] == "c99")].iloc[0]
        engine = self.risk.var_table[(self.risk.var_table["series"] == "portfolio") &
                                     (self.risk.var_table["method"] == "historical") &
                                     (self.risk.var_table["confidence"] == 0.99)].iloc[0]
        self.assertEqual(row["var"], engine["var_1d"])
        self.assertEqual(len(view["tails"]["portfolio:c99"]), 4)
        stress = stress_view(latest_run(self.dataset_id, "stress", db_path=self.db), db_path=self.db)
        self.assertEqual(len(stress["comparison"]), len(self.stress.vectors))
        sums = stress["contributions"].groupby("scenario_run_id")["impact_inr"].sum()
        for item in stress["comparison"].itertuples():
            self.assertAlmostEqual(sums[item.scenario_run_id], item.impact_inr, places=6)
        opt = optimization_view(latest_run(self.dataset_id, "optimization", db_path=self.db), db_path=self.db)
        self.assertEqual(list(opt["comparison"]["strategy"]), list(self.optimization.results))
        self.assertEqual(len(opt["frontier"]), 25)
        self.assertIsNone(latest_run(999, "risk", db_path=self.db))

    def test_charts_build(self):
        view = risk_view(latest_run(self.dataset_id, "risk", db_path=self.db))
        self.assertEqual(len(charts.var_methods(view["var_table"], "c99").data), 2)
        stress = stress_view(latest_run(self.dataset_id, "stress", db_path=self.db), db_path=self.db)
        names = stress["comparison"].set_index("scenario_id")["scenario_name"]
        heat = charts.stress_heatmap(stress["contributions"], names)
        self.assertEqual(heat.data[0].z.shape, (len(names), 5))

    def test_reports_carry_provenance_and_escape(self):
        runs = {"risk": latest_run(self.dataset_id, "risk", db_path=self.db), "stress": None}
        header = provenance_rows({"dataset_id": 1, "source_name": "<b>x</b>", "verification_status": "simulated",
                                  "price_basis": "unadjusted_ohlcv"}, runs)
        page = html_report("Test", header, [("Table", pd.DataFrame({"a": ["<script>"]})), ("Notes", ["n1"])])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("not TRI", page)
        self.assertIn("not stored", page)
        archive = zipfile.ZipFile(io.BytesIO(csv_bundle({"t": pd.DataFrame({"a": [1]}), "empty": pd.DataFrame()},
                                                        header)))
        self.assertEqual(archive.namelist(), ["provenance.csv", "t.csv"])
        self.assertIn(LABELS[0].split(";")[0], archive.read("provenance.csv").decode())

    def test_app_renders_every_tab_without_exceptions_and_without_new_engine_rows(self):
        before = _fingerprint(self.db)
        with _pointed_at(self.db):
            self._render_and_click(before)

    def _render_and_click(self, before):
        app = _run_app(self.db)
        self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
        self.assertEqual(len(app.tabs), 11)
        text = " ".join(str(m.value) for m in app.markdown) + " ".join(str(c.value) for c in app.caption)
        self.assertIn("phase3_risk_v1", text)
        self.assertIn("user_declared_unverified", text)
        warnings = " ".join(str(w.value) for w in app.warning)
        self.assertIn("Gated", warnings)
        self.assertIn("Not forecasts", warnings)
        after = dict(_fingerprint(self.db))
        for table, count in before:
            if table not in ("analysis_metadata", "analysis_results"):
                self.assertEqual(after[table], count, table)  # only the existing Phase 4 load may add a run
        prepare = next(b for b in app.button if b.label == "Prepare CSV and HTML report")
        prepare.click().run()
        self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
        self.assertIn("report_html", app.session_state)
        self.assertEqual(app.session_state["selected_dataset"]["dataset_id"], self.dataset_id)


class EmptyStateTests(unittest.TestCase):
    def test_empty_database_shows_guidance(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            with _pointed_at(Path(temp) / "empty.db"):
                app = _run_app(Path(temp) / "empty.db", timeout=60)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("No real Indian market dataset" in str(i.value) for i in app.info))

    def test_dataset_without_stored_runs_points_to_cli(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "plain.db"
            initialize(db)
            upsert_configured_portfolio(CONFIG, path=db)
            candles, metadata = _synthetic(30)
            import_candle_dataset(candles, metadata=metadata, path=db)
            with _pointed_at(db):
                app = _run_app(db, timeout=120)
            self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
            infos = " ".join(str(i.value) for i in app.info)
            for engine in ("phase3_risk_v1", "phase4_stress_v1", "phase5_opt_v1"):
                self.assertIn(engine, infos)


if __name__ == "__main__":
    unittest.main()
