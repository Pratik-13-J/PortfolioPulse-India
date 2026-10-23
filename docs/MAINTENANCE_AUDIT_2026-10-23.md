# Maintenance audit — 23 October 2026

## Scope and verification

This audit covers the project files in this repository, its local SQLite dataset, the Python dependency set, the automated tests, and a local Streamlit run. The project was copied from `D:\Prev_Projects\Project-Russell_Investment-Portfolio_Risk_Monitor` because the configured workspace path did not exist. The original repository was not changed. Its existing uncommitted changes were preserved in this copy. The local `.env` and virtual environment were not copied; the ignored market-data files and database were copied for offline verification.

- Python 3.12.10; `pip check` found no broken installed requirements.
- `pytest -q -p no:cacheprovider`: **91 passed, 57 subtests passed** after creating the ignored `.tmp` directory required by the documented setup. A test run without that directory failed in tests that explicitly use it as a temporary-file parent.
- SQLite `PRAGMA integrity_check`: `ok` for the copied database.
- Streamlit served HTTP 200 at `/` and `/_stcore/health` on local loopback. Streamlit's app test runner rendered the saved dataset with **zero exceptions**, five dashboard tabs, and 23 metrics.

## Findings and actions

| Area | Finding | Action |
|---|---|---|
| Scenario validation | An individual shock keyed by a sector, or a sector shock keyed by a holding symbol, passed the combined target check and was then silently ignored. | Fixed with separate holding and sector checks. Added regression cases. |
| File duplication | No exact duplicate source or documentation files were found, apart from the two empty package `__init__.py` files. | No deletion needed. |
| Legacy code | `portfoliopulse/analytics.py` is not imported by the app, scripts, or tests and overlaps the newer `risk_metrics.py`. `scenario_impact` in `scenarios.py` also has no internal callers. | Left in place because external consumers may import these public helpers; remove in a deliberate compatibility cleanup. |
| Dependencies | The direct application imports account for the listed main libraries. `websocket-client` has no direct project import, but the installed Angel One SDK imports `websocket` in its WebSocket modules. | Kept the explicit pin. No dependency was removed on usage guesses. |
| Tests and setup | Several tests require `.tmp` to exist; the README's installation steps create it. | Kept the documented setup. The test command alone is not a fresh-checkout setup command. |
| Documentation | Setup, data provenance, methodology, limitations, and phase notes are present. README screenshots are still placeholders. | Capture reviewed, sanitized screenshots before public presentation. |
| Structure | `app.py` (390 lines) and `database.py` (748 lines) are large, but the calculation modules are separate and tested. | Split them only with focused tests for the extracted behavior; no broad refactor was needed for the verified fix. |
| GitHub | This local Git repository has no configured remote. The copied working tree includes substantial pre-existing uncommitted work. | No push or pull request is possible until a remote is configured. Keep this audit's changes separate from the pre-existing work when committing. |

## Remaining external checks

The Angel One historical-candle endpoint is still not verified live after the previously recorded HTTP 403 response. The saved Yahoo Finance fallback is labelled as user-declared and unverified. The existing ten-date NIFTY 50 check is only a spot check, not full-series validation. See [Final audit](FINAL_AUDIT.md) for the source and method limits.
