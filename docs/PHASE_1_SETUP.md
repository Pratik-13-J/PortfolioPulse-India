# Phase 1 setup record — 16 October 2026

Historical setup record under the earlier dependency list. See [the revised plan audit](REVISED_PLAN_AUDIT.md) for current phase status and Angel One setup additions.

## Scope completed

- Continued in the existing `Project-Russell_Investment-Portfolio_Risk_Monitor` workspace rather than creating a nested `PortfolioPulse` folder.
- Initialized a local Git repository and saved the reviewed Phase 0 files as commit `a9069ab` before changing setup files. There is no remote or publication step.
- Created `.venv` using Python 3.12.10 and installed the four direct dependencies declared in `requirements.txt`.
- Kept data, SQLite, analytics, scenarios, briefing and Streamlit UI in separate modules, with shared settings in `portfoliopulse/config.py`.
- Added exact Windows PowerShell setup and run commands to the README.

## Verification

- Virtual-environment imports succeeded for pandas, NumPy, Plotly and Streamlit.
- `python -m scripts.seed_demo` completed and created one portfolio, five **synthetic** instruments and 4,890 observations in SQLite.
- Running `app.py` directly completed without an exception, although Streamlit correctly warned that this is bare mode without an interactive session.
- A temporary local Streamlit server started on `127.0.0.1:8517`, and its `/_stcore/health` endpoint returned HTTP 200. The server was stopped after the check.
- The Streamlit `AppTest` harness stalled in this environment and was stopped. Phase 6 and Phase 7 still need an interactive dashboard walkthrough and financial validation.

## Setup notes

The first virtual-environment attempt failed because Python wrote temporary `pip` files outside the permitted workspace. Setting `TEMP` and `TMP` to `.tmp` inside the project resolved that error. The first dependency install also failed under network restrictions; the approved network retry installed the packages successfully.

The installed Streamlit version reports that the starter app's `use_container_width` argument is deprecated. This does not prevent startup and is tracked for the dashboard phase.

## Gate result

**Phase 1 passed for project setup and local server startup.** It does not certify that the current synthetic-data dashboard calculations are correct. The next work item is Phase 2: select and validate Indian market-data sources and a labeled offline fallback.
