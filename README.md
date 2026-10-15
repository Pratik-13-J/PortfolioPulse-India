# PortfolioPulse

India-first portfolio risk monitor built with Python, SQLite, Streamlit, and Plotly.

**Status:** Phases 0 and 1 and the Phase 2 offline demo path are complete. Phase 2 uses fictional India-themed instruments because the project currently needs a dummy portfolio. Live market-data retrieval is deferred. Financial calculations and dashboard behavior still need the later gates in [PROJECT_PLAN.md](PROJECT_PLAN.md). See the [Phase 2 data record](docs/PHASE_2_DATA.md).

This offline-first MVP demonstrates portfolio return and risk analytics, data persistence, allocation review, and what-if scenarios. The included prices are **synthetic demo data**, not market observations. No trades, forecasts, or personalized investment advice are produced.

## Windows PowerShell setup

Run these commands from this project folder with Python 3.12 installed:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.seed_demo
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The temporary-directory settings keep Python package setup inside the project on restricted Windows machines. To open the project in VS Code, run `code .` from the same folder if the VS Code command is installed.

The local database path is `data/portfoliopulse.db`. The checked-in offline fallback is `data/demo/prices.csv`. Running the seed command again is safe: it leaves an already populated demo portfolio alone. An older Phase 1 synthetic portfolio may remain in a local database, but the dashboard selects the new India Demo Portfolio.

To check the starter database without running the dashboard:

```powershell
.\.venv\Scripts\python.exe -c "from portfoliopulse.database import connect; c=connect(); print(c.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0]); c.close()"
```

## What it shows

- INR-denominated portfolio value and weights from user-editable quantities.
- Cumulative return, annualized return and volatility, Sharpe ratio, maximum drawdown, historical VaR/expected shortfall, and rolling volatility.
- Benchmark comparison against the included NIFTY 50 TRI demo series.
- Allocation drift, concentration (HHI), correlation, and configurable shock scenarios with loss contribution.
- A deterministic analyst-style briefing whose statements come from the calculated results.

## Data and methodology

The bundled series are generated deterministically for interface and workflow demonstration only. The portfolio has six fictional Indian equity stocks (energy, banking, IT, healthcare, consumer staples, automobiles), a fictional gold ETF, a fictional government bond ETF, and a separate Nifty-style benchmark. The `DEMO_` prefix keeps them visibly distinct from listed securities. The CSV covers weekdays from 2 January 2024 through 30 September 2026; these are **not** actual Indian exchange sessions, and prices are **not** corporate-action-adjusted or total-return observations.

The data path validates symbol, ISO date, finite positive price, duplicates and common-date coverage before import. It does not fill missing prices. The dashboard blocks risk calculation for a selected window with missing instrument or benchmark observations. To import another CSV, use `symbol,date,close_value` in the dashboard; symbols must match the fictional demo universe. This import updates observations in SQLite and records the uploaded filename as source. A provider for real Indian prices is not yet connected. For real observations later, confirm data rights, adjustment method, source and as-of date first. Nifty Indices and AMFI are candidate source sites; see the project plan.

Run focused Phase 2 checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_data_pipeline.py' -v
```

Returns are computed from daily portfolio market values using fixed unit holdings (no automatic rebalancing). Annualization assumes 252 trading observations. Historical VaR is the positive loss threshold at the selected confidence level; expected shortfall is the average loss in the tail beyond that threshold. These measures depend on the selected historical window and do not predict future losses. The Sharpe ratio uses a configurable annual risk-free assumption, defaulting to 0% for this illustrative dataset.

## SQL design

SQLite uses normalized `instruments`, `portfolios`, `holdings`, `market_observations`, `risk_runs`, `risk_metrics`, and scenario tables. Instrument rows include asset class and sector. Imports use parameterized statements, foreign keys, uniqueness constraints, and idempotent upserts. Connections close after every database operation. Analytics are calculated in Python; SQL joins holdings to instrument metadata, persists each run and its method metadata, and retrieves portfolio/scenario histories.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full scope, the confirmed buy-and-hold model and the phase gates.
