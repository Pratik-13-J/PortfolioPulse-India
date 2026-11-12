# Handoff — Conversation 6 (2026-11-12): Dashboard & Reporting (roadmap Phase 7)

Scope: presentation and reporting only. No new financial logic, no change to any engine, and no redesign of the existing tabs. Branch `conv6-dashboard`, created from `c4ca993` (tip of the unmerged `conv5-optimization`). Nothing has been pushed or merged.

| Commit | Summary |
|---|---|
| `4e037f5` | `portfoliopulse/ui/` (stored-result loaders, chart builders), `portfoliopulse/reporting.py`, new `app.py` tabs, `tests/test_dashboard.py` |
| (docs commit) | Frontier label fix, README, `docs/PHASE_6_DASHBOARD.md`, this file, `CLAUDE.md` |

## 1. Verification (actually run)

- **Tests:** baseline 250 / 71 → **256 passed, 71 subtests passed**. No existing test was removed or edited. The six new tests in `tests/test_dashboard.py` cover:
  - loaders reproduce engine outputs exactly: historical VaR, tail count, scenario contributions reconciling to totals, strategy list, 25 frontier points;
  - charts build;
  - HTML report escaping, provenance header and the CSV zip;
  - **Streamlit `AppTest`**:
    - renders all 11 tabs on a fixture DB with stored risk, stress and optimization runs, with zero exceptions;
    - the "Prepare report" click also raises no exceptions and selects the fixture dataset;
    - an empty DB shows the ingestion guidance;
    - a dataset without stored runs shows the CLI commands to create each run.
- **Manual browser check:** run at `127.0.0.1:8517` against a **copy** of the DB (`PORTFOLIOPULSE_DB=.tmp/conv3/preview6.db`) on dataset 1. These tabs rendered: VaR & backtests, Stress library, Optimization, Benchmark comparison and Reports, with both download buttons appearing. The server log had no errors.
  - One defect was found and fixed: frontier marker labels collided where four strategies cluster. Identity now comes from the legend and hover.
- **DB:**
  - All pre-existing rows (analysis IDs ≤ 10, scenario runs ≤ 18, every `optimization_*` row) and the Phase-4/perf hex dump are bit-identical. Integrity `ok`; FK check empty.
  - **One row was added to the real DB by mistake during test development: `analysis_metadata` 12** (dataset 2, `phase8_perf_v1`, rf 0.06, 95%, 59 results). An early version of the AppTest restored the DB environment variable before a button-click rerun, so that rerun opened the real DB. Opening dataset 2 in the dashboard runs the existing `analyze_saved_dataset`, and that inserted the row.
  - The row is the same one the dashboard creates whenever a user opens dataset 2 with default settings. No existing row changed. It has **not** been deleted, pending your decision.
  - The test now holds the override across reruns and asserts the fixture dataset is selected.

## 2. What was built

- **`portfoliopulse/ui/stored_results.py`:** read-only loaders using SQLite `mode=ro` connections.
  - `latest_run(dataset_id, kind)` returns the newest run for `risk`, `stress` or `optimization`. It returns None when absent or when the schema lacks the tables.
  - `risk_view`, `stress_view` and `optimization_view` reshape stored metrics, quality JSON and the scenario/optimization tables. They do no recomputation.
- **`portfoliopulse/ui/charts.py`** (Plotly; dataviz reference palette: fixed categorical order by entity, blue↔red diverging with a gray midpoint, single y-axis, 2px lines):
  - VaR/ES by method with ±1.96 SE Monte Carlo error bars;
  - stress scenario bars by category, holding × scenario impact heatmap, per-scenario waterfall;
  - efficient frontier with strategy markers;
  - walk-forward growth of ₹100;
  - strategy weights;
  - rolling TE and beta lines.
- **`portfoliopulse/reporting.py`:** CSV zip (`provenance.csv` plus tables) and a self-contained HTML report (Plotly JS embedded once).
  - Both carry a provenance header: dataset, source, verification and provenance status, price basis, window, run IDs, generation time.
  - Both carry the standing labels: not TRI; in-sample / hypothetical; illustrative, not advice.
  - All values are HTML-escaped.
- **`app.py`:**
  - The existing five tabs are unchanged. The new tabs are Benchmark comparison, VaR & backtests, Stress library, Optimization, Attribution (gated) and Reports.
  - New tabs only read stored runs. Each shows the run ID, engine, creation time, source, **verification status**, price basis and the not-TRI label.
  - They surface undefined and unavailable states with their reasons: the small-sample warning, undefined backtests, unavailable COVID/2022 events, undefined max-Sharpe, constrained ERC, walk-forward rule failures and the attribution gate.
  - When no stored run exists for an engine, the tab shows the exact CLI command to create it.
  - `PORTFOLIOPULSE_DB` overrides the DB path; the default is unchanged.

## 3. Known limitations

1. **Opening a dataset can write to the DB.** The dashboard's existing Performance path calls `analyze_saved_dataset`, which persists a Phase 4 run for any new settings combination. That behaviour predates Conv 6 and was kept. Making the dashboard fully read-only would change an existing workflow, so it needs your decision.
2. **Stored runs are shown as computed.** The sidebar risk-free rate and confidence do not re-run the Phase 3/4/5 engines; their settings are shown in the captions. Re-running them is a CLI action.
3. **Dataset 2 has no stored engine runs**, by design: it is too short. The tabs show how to create runs instead.
4. **HTML reports are large (~4–5 MB)** because Plotly JS is embedded for offline use. No PNG/kaleido export was added (no new dependency).
5. **Theme:** charts use Streamlit's own theme. The dark theme was checked visually, light only via defaults.

## 4. Files

- **New:** `portfoliopulse/ui/__init__.py`, `ui/stored_results.py`, `ui/charts.py`, `portfoliopulse/reporting.py`, `tests/test_dashboard.py`, this file.
- **Changed:** `app.py` (additive tabs, DB-path override), `README.md`, `docs/PHASE_6_DASHBOARD.md`, `CLAUDE.md`.
- **Unchanged:** every engine module, CLI, schema (v4) and stored run.

## 5. Status and next steps

The roadmap's numbered phases (1–7) are now implemented on stacked, unmerged branches:

`conv2-foundation-performance-attribution` → `conv3-risk-engine` → `conv4-stress-testing` → `conv5-optimization` → `conv6-dashboard`

Suggested next steps, in order:
1. **Review and merge the branch stack** into `main`. Each branch fast-forwards onto the next.
2. **Decide on analysis 12** (keep or remove) and whether the dashboard should become fully read-only.
3. **Data decisions carried forward:**
   - the NSE corporate-action file (confirms dataset 1's price basis);
   - the long Angel One pull (a verified long sample);
   - the NIFTY 50 constituent weights (ungates attribution);
   - the RBI/FBIL rate and FX series (replaces the assumed sensitivities);
   - the cost rates and constraints in `config/optimization.json`.
4. **Re-run the engines** on any new verified dataset with the CLIs; the dashboard picks up the latest run automatically.
