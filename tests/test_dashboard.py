"""Conversation 6 dashboard: read-only loaders, report export and Streamlit AppTest renders."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
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


def _file_state(db: Path) -> tuple[str, int]:
    return hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns


class _ReadOnlyFile:
    """Mark the DB file read-only at the OS level, so any write attempt fails loudly."""

    def __init__(self, db: Path):
        self.db = db

    def __enter__(self):
        os.chmod(self.db, stat.S_IREAD)
        return self

    def __exit__(self, *exc):
        os.chmod(self.db, stat.S_IREAD | stat.S_IWRITE)
        return False


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

    def test_app_is_read_only_and_renders_every_tab(self):
        before_rows, before_file = _fingerprint(self.db), _file_state(self.db)
        with _pointed_at(self.db), _ReadOnlyFile(self.db):
            self._render_and_interact()
        self.assertEqual(_fingerprint(self.db), before_rows)
        self.assertEqual(_file_state(self.db), before_file)  # bytes and mtime unchanged
        self.assertFalse(Path(f"{self.db}-journal").exists())
        self.assertFalse(Path(f"{self.db}-wal").exists())

    def _render_and_interact(self):
        app = _run_app(self.db)
        self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
        self.assertEqual(len(app.tabs), 11)
        text = " ".join(str(m.value) for m in app.markdown) + " ".join(str(c.value) for c in app.caption)
        self.assertIn("phase3_risk_v1", text)
        self.assertIn("user_declared_unverified", text)
        warnings = " ".join(str(w.value) for w in app.warning)
        self.assertIn("Gated", warnings)
        self.assertIn("Not forecasts", warnings)
        self.assertTrue(any("not saved" in str(c.value) for c in app.caption))
        # Changing settings used to persist a new Phase 4 run; it must now stay in memory.
        next(n for n in app.number_input if n.label.startswith("Annual INR risk-free")).set_value(5.0).run()
        next(b for b in app.selectbox if b.label == "Historical one-day VaR confidence").set_value(0.99).run()
        self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
        prepare = next(b for b in app.button if b.label == "Prepare CSV and HTML report")
        prepare.click().run()
        self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
        self.assertIn("report_html", app.session_state)
        self.assertEqual(app.session_state["selected_dataset"]["dataset_id"], self.dataset_id)


class EmptyStateTests(unittest.TestCase):
    def test_empty_database_shows_guidance(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            missing = Path(temp) / "empty.db"
            with _pointed_at(missing):
                app = _run_app(missing, timeout=60)
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(missing.exists())  # the read-only dashboard never creates a database
            self.assertTrue(any("No real Indian market dataset" in str(i.value) for i in app.info))

    def test_dataset_without_stored_runs_points_to_cli(self):
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "plain.db"
            initialize(db)
            upsert_configured_portfolio(CONFIG, path=db)
            candles, metadata = _synthetic(30)
            import_candle_dataset(candles, metadata=metadata, path=db)
            before = _file_state(db)
            with _pointed_at(db), _ReadOnlyFile(db):
                app = _run_app(db, timeout=120)
            self.assertEqual(_file_state(db), before)
            self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
            infos = " ".join(str(i.value) for i in app.info)
            for engine in ("phase3_risk_v1", "phase4_stress_v1", "phase5_opt_v1"):
                self.assertIn(engine, infos)


if __name__ == "__main__":
    unittest.main()


class ReadOnlyConnectionTests(unittest.TestCase):
    def test_read_only_connection_rejects_writes_and_never_creates_files(self):
        from portfoliopulse.database import connect, list_candle_datasets
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "ro.db"
            initialize(db)
            con = connect(db, read_only=True)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    con.execute("CREATE TABLE should_fail(x INTEGER)")
            finally:
                con.close()
            self.assertEqual(list_candle_datasets(db, read_only=True), [])
            missing = Path(temp) / "nested" / "missing.db"
            with self.assertRaises(FileNotFoundError):
                connect(missing, read_only=True)
            self.assertFalse(missing.parent.exists())


class DefaultDatasetTests(unittest.TestCase):
    def test_prefers_dataset_with_stored_runs(self):
        from portfoliopulse.ui.stored_results import default_dataset_index
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as temp:
            db = Path(temp) / "two.db"
            initialize(db)
            upsert_configured_portfolio(CONFIG, path=db)
            candles, metadata = _synthetic(260)
            older = import_candle_dataset(candles, metadata=metadata, path=db)
            analyze_saved_dataset_risk(older, db_path=db, mc_paths=10_000, mc_batches=20)
            short, short_meta = _synthetic(12)
            newer = import_candle_dataset(short, metadata=short_meta, path=db)  # no stored runs
            self.assertNotEqual(newer, older)
            ids = [newer, older]  # most recent first, as the dashboard lists them
            self.assertEqual(ids[default_dataset_index(ids, db_path=db)], older)
            self.assertEqual(default_dataset_index([999], db_path=db), 0)


class AttributionDemoTests(unittest.TestCase):
    def test_demo_is_labelled_and_reconciles(self):
        from portfoliopulse.ui.attribution_demo import DEMO_LABEL, demo
        result = demo()
        self.assertIn("ILLUSTRATIVE ONLY", DEMO_LABEL)
        # Hand: R_b = .35*.05 + .15*.03 + .20*0 + .30*.02 = 0.028; R_p = .45*.06 + .30*.02 + .25*.01 = 0.0355
        self.assertAlmostEqual(result["benchmark_return"], 0.028, places=15)
        self.assertAlmostEqual(result["portfolio_return"], 0.0355, places=15)
        self.assertAlmostEqual(float(result["single_period"]["total"].sum()), 0.0075, places=15)
        self.assertAlmostEqual(result["single_period"].at["Consumer", "allocation"], -0.30 * (0.02 - 0.028), places=15)
        self.assertAlmostEqual(float(result["linked"]["total"].sum()), result["linked_active_return"], places=14)
        self.assertAlmostEqual(result["active_share"], 0.5 * (0.10 + 0.15 + 0.05 + 0.30), places=15)
