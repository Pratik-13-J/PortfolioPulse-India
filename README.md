# PortfolioPulse

India-first portfolio risk monitor built with Python, SQLite, Streamlit, and Plotly.

**Status:** Phase 0 review and structure are complete. This workspace contains a starter prototype whose calculations and dashboard have not yet passed Phases 1–8 in [PROJECT_PLAN.md](PROJECT_PLAN.md). See [the Phase 0 review](docs/PHASE_0_REVIEW.md) for tracked gaps.

This offline-first MVP demonstrates portfolio return and risk analytics, data persistence, allocation review, and what-if scenarios. The included prices are **synthetic demo data**, not market observations. No trades, forecasts, or personalized investment advice are produced.

## Starter launch commands (to be verified in Phase 1)

1. Create a Python environment and install `requirements.txt`.
2. Run `python -m scripts.seed_demo` once to create the local SQLite database and synthetic sample data.
3. Run `python -m streamlit run app.py`.

The local database path is `data/portfoliopulse.db`. The seed data is synthetic and labeled as such.

## What it shows

- INR-denominated portfolio value and weights from user-editable quantities.
- Cumulative return, annualized return and volatility, Sharpe ratio, maximum drawdown, historical VaR/expected shortfall, and rolling volatility.
- Benchmark comparison against the included NIFTY 50 TRI demo series.
- Allocation drift, concentration (HHI), correlation, and configurable shock scenarios with loss contribution.
- A deterministic analyst-style briefing whose statements come from the calculated results.

## Data and methodology

The bundled series are generated deterministically for interface and workflow demonstration only. To use real observations, import appropriately licensed/attributed CSV data through the app or `portfoliopulse/database.py` and preserve the source and as-of date. The import currently supports the seeded sample symbols. Nifty Indices publishes historical index and total-return data; AMFI provides NAV and NAV-history access. NSE market-data products are subject to data usage terms. Do not treat this demo's synthetic results as actual instrument performance.

Returns are computed from daily portfolio market values using fixed unit holdings (no automatic rebalancing). Annualization assumes 252 trading observations. Historical VaR is the positive loss threshold at the selected confidence level; expected shortfall is the average loss in the tail beyond that threshold. These measures depend on the selected historical window and do not predict future losses. The Sharpe ratio uses a configurable annual risk-free assumption, defaulting to 0% for this illustrative dataset.

## SQL design

SQLite uses normalized `instruments`, `portfolios`, `holdings`, `market_observations`, `risk_runs`, `risk_metrics`, and scenario tables. Imports use parameterized statements, foreign keys, uniqueness constraints, and idempotent upserts. Analytics are calculated in Python; SQL is used to validate/load observations, join holdings to instrument metadata, persist each run and its method metadata, and retrieve portfolio/scenario histories.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full scope, the confirmed buy-and-hold model and the phase gates.
