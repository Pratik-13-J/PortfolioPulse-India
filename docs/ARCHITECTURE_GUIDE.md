# Architecture Guide

PortfolioPulse is a layered Python application. Pure calculation modules know nothing about databases or the UI, orchestration modules connect them to SQLite, command-line scripts are the only writers, and the Streamlit dashboard only reads.

```text
                ┌──────────────────────── INGESTION ────────────────────────┐
 Angel One API ─┤ angelone/client.py, instrument_master.py, cache.py        │
 Sourced CSV  ──┤ sourced_csv.py (SHA-256 sidecar)                          │
 NSE ref files ─┤ reference_data.py, nse_corporate_actions.py               │
                └───────────────┬────────────────────────────────────────────┘
                                ▼  data_quality.validate_daily_candles (no fills)
                ┌──────────────── PERSISTENCE (SQLite, schema v4) ───────────┐
                │ database.py, schema.sql + migrations.py (v1–v4, backup-first)│
                │ candle_datasets · daily_candles · dataset_holdings          │
                │ analysis_metadata/results · scenario_* · optimization_*     │
                │ reference_datasets · corporate_actions · provenance_events  │
                └───────────────┬────────────────────────────────────────────┘
                                ▼ read candles + immutable holding snapshot
 ┌──────────── PURE CALCULATIONS (no I/O) ─────────────┐   ┌──── ORCHESTRATION (CLI) ────┐
 │ portfolio_returns · risk_metrics · performance_metrics│◄─┤ portfolio_analysis (Phase 4) │
 │ var_models · var_backtests · covariance_estimators    │◄─┤ risk_analysis   phase3_risk_v1 │
 │ drawdowns · factor_sensitivities · stress_scenarios   │◄─┤ stress_analysis phase4_stress_v1│
 │ historical_stress · stress_impact · attribution        │◄─┤ optimization_analysis         │
 │ expected_returns · optimization · portfolio_allocation │   │   phase5_opt_v1               │
 │ portfolio_evaluation · optimization_backtest           │   │ price_basis_versioning        │
 │ benchmark_attribution (gated)                          │   └──────────────┬───────────────┘
 └────────────────────────────────────────────────────────┘                  ▼ save_* (transactions)
                                                              SQLite  ──mode=ro──►  app.py
                                                                                    ui/stored_results.py
                                                                                    ui/charts.py · reporting.py
```

## 1. Data ingestion

- **Angel One SmartAPI** (`angelone/`): resolves NSE symbols against the instrument master, authenticates from `.env`, requests daily candles in compliant windows with retries and spacing, and normalises timestamps. Responses are cached for offline replay (`cache.py`).
- **Sourced CSV** (`sourced_csv.py`, `scripts/import_sourced_csv.py`, `scripts/ingest_phase3.py`): any lawfully obtained OHLCV file plus a sidecar declaring source, retrieval time, price basis and SHA-256.
- **Reference files** (`reference_data.py`): corporate actions and benchmark constituents. Each is stored as a `reference_datasets` row with its declared source and checksum. `nse_corporate_actions.py` converts NSE's own CSV export into that format.

## 2. Validation and provenance

- `data_quality.validate_daily_candles` checks positive prices, OHLC relationships, duplicate symbol/date rows, date coverage and missingness, and builds the valid-return calendar. Missing prices are **never** forward-filled. A return that needs a missing observation is excluded and reported.
- `corporate_action_jump_warnings` flags suspicious jumps, warning only.
- Every dataset carries `source_name`, `price_basis` and `verification_status`. The status follows a ladder: `verified_live`, `verified_reference_sample`, `user_declared_unverified`, `fallback_unverified`, `simulated`. Label changes are recorded as `dataset_provenance_events`.

## 3. SQLite schema and versioning

- `schema.sql` is the frozen baseline. `migrations.run_migrations` (called from `database.initialize`) applies numbered, idempotent, transactional migrations and takes a backup first:
  - v1: provenance and reference tables;
  - v2: verification status and the split-adjusted basis;
  - v3: benchmark constituents and classifications;
  - v4: optimization tables.
- **Datasets are immutable.** `import_candle_dataset` derives a dataset key from content plus metadata. Re-importing identical data returns the same ID, while a different source or basis gets a new ID. The holding snapshot (`dataset_holdings`) is frozen with the dataset, so later config edits never rewrite history.
- **Versioning for corrections:** `price_basis_versioning.build_adjusted_version` creates a *new* dataset (for example, adjusted for splits and bonuses) with a lineage event, and never edits the base dataset.

## 4. Calculation layer

The pure functions take pandas objects and return numbers or frames. Each layer:
- **Returns:** `portfolio_returns.build_buy_and_hold_returns` turns closes and fixed quantities into values, weights, daily returns and the comparison calendar. It is the single source of every return series.
- **Performance and benchmark:** `risk_metrics` (volatility, Sharpe, drawdown, historical VaR/ES, volatility contributions) and `performance_metrics` (TE, IR, beta, capture, Sortino, rolling, Carino contributions).
- **Risk engine:** `var_models` (normal, Cornish-Fisher, Monte Carlo, delta-normal components, age-weighted HS), `var_backtests` (rolling forecasts, Kupiec, Christoffersen), `covariance_estimators` (sample, Ledoit-Wolf, EWMA) and `drawdowns`.
- **Stress engine:** `stress_scenarios` (shock vectors with direct and propagated components), `historical_stress` (event replay), `factor_sensitivities` (OLS betas, rate/FX) and `stress_impact` (P&L, sector impact, risk integration).
- **Optimization engine:** `expected_returns`, `optimization` (SLSQP, ERC, frontier), `portfolio_allocation` (costs, integer shares), `portfolio_evaluation` and `optimization_backtest` (walk-forward).
- **Attribution engine:** `benchmark_attribution` (Brinson-Fachler, Carino, Active Share, `attribution_gate`) and `attribution` (scenario P&L reconciliation, allocation alerts).

## 5. Orchestration

Each engine has one `analyze_saved_dataset_*` function. It works in four steps:
1. Read the dataset.
2. Recompute the Phase 4 analysis in memory.
3. Run the engine.
4. Persist the results with `save_analysis_run`, plus `save_dataset_scenario` or `save_optimization_runs`.

The settings JSON records `engine_version`, the method strings, the seeds and the source/verification labels. Identical inputs map to the same `analysis_id`, which makes runs idempotent; scenario and optimization rows are reused. Command-line entry points: `scripts/analyze_risk.py`, `analyze_stress.py`, `analyze_optimization.py` and `corporate_actions.py`.

## 6. Dashboard and reports

- `app.py` is **read-only**: every connection uses SQLite `mode=ro`, there are no migrations, and the in-memory Phase 4 analysis is never saved.
- `ui/stored_results.py` loads the latest stored run per engine (`latest_run`) and reshapes it (`risk_view`, `stress_view`, `optimization_view`) without recomputing anything.
- `ui/charts.py` builds the Plotly figures. `ui/attribution_demo.py` provides the labelled synthetic Brinson example.
- `reporting.py` builds a CSV zip and a self-contained HTML report, each headed by a provenance block.

## 7. End-to-end data flow example

1. A CSV is imported, validated and stored as dataset 1.
2. `scripts.analyze_risk --dataset-id 1` builds buy-and-hold returns, then VaR/ES and backtests. The result is stored as analysis 9 (`phase3_risk_v1`).
3. The dashboard opens dataset 1, and `latest_run(1, "risk")` finds analysis 9.
4. `risk_view` pivots the stored metric keys (`var_1d:historical:c99`, …) into a table.
5. `charts.var_methods` draws the chart, and Reports exports the same values with their provenance.
