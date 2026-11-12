# CLAUDE.md — PortfolioPulse India

This file is the working guide for Claude Code and other coding agents. Read it before modifying the repository. Treat the source code, tests, `README.md`, `PROJECT_PLAN.md`, and latest audit documents as the authoritative project materials. If this file conflicts with executable code or a newer audit, inspect the code and tests, explain the discrepancy, and update this guide when appropriate.

## 1. Project purpose and scope

**PortfolioPulse India** is an educational Indian-equity portfolio analytics and risk-monitoring application built with Python, pandas, NumPy, SQLite, Streamlit, and Plotly.

It analyses a configurable buy-and-hold portfolio of NSE-listed shares in INR, compares price performance with the NIFTY 50 **price index**, calculates portfolio risk statistics, and evaluates transparent hypothetical one-period stress scenarios. The dashboard presents analysis and data-quality context.

This is **not** an execution/trading system, investment-advice product, return-prediction model, or production portfolio-management platform. Do not describe it as such or add those capabilities without an explicit change in scope.

### Configured example portfolio

The example universe is configured in `config/instruments.json`:

- RELIANCE
- HDFCBANK
- INFY
- ICICIBANK
- ITC
- NIFTY 50 as the separate benchmark

Quantities and target weights are illustrative configuration, not recommendations. The model is **buy and hold**: share quantities remain fixed and market weights drift as prices change. Target weights are reference values for allocation-drift monitoring; editing them must not silently change historical holdings or returns.

## 2. Current implementation status and critical caveats

Use these status statements when developing, documenting, or presenting the project:

1. Portfolio analytics, scenario attribution, SQLite persistence, and Streamlit/Plotly dashboard are implemented.
2. The recorded local verification reported **91 pytest tests and 57 subtests passing**. These are historical audit results, not proof that a new checkout or later code change currently passes. Run the tests before making a fresh claim.
3. Angel One SmartAPI adapter, instrument-master resolution, candle normalization, retries, and offline-cache workflow are implemented and mock-tested. The 2026-10-16 refresh received HTTP 403 access-rate responses (cause not isolated). On 2026-11-12 a live diagnostic and a controlled 15-day pull succeeded: **dataset 2** (`angel_one_smartapi`, `verified_live`, `unadjusted_ohlcv`, 2026-10-29 to 2026-11-12, 66 candles). Only this short window is verified live; long-window Angel access is untested. See `docs/HANDOFF_CONV2.md` §6.
4. The saved local analysis dataset is a separate Yahoo Finance OHLCV CSV imported through the source-labelled fallback. It is marked `unadjusted_ohlcv` and user-declared/unverified. It is **not Angel One data**.
5. Only a limited ten-date NIFTY 50 spot check was compared with displayed official NSE Indices values. The audit reported all ten within 0.01 index points, with maximum absolute difference 0.00078125. This does not validate the complete history, individual stocks, or every date.
6. Returns are price returns, not dividend-aware total returns. The benchmark is NIFTY 50 price index, not TRI. Corporate actions and dividends are not independently adjusted in the unadjusted fallback.
7. The illustrative annual INR risk-free rate default is 6%; it is a user assumption, not a live RBI feed.
8. Missing prices are not forward-filled or replaced with zero. Returns requiring missing observations are excluded and data-quality limitations should remain visible.
9. Scenario outputs are hypothetical one-period sensitivities, not forecasts or predictions.
10. The application does not synchronize broker holdings, place trades, forecast returns, provide LLM investment advice, or implement live multi-asset coverage.

Never remove source labels, adjustment-basis labels, validation warnings, or limitations merely to make dashboard output look more complete.

## 3. Technology and environment

- Python: verified local environment uses Python 3.12.
- Dependencies are pinned in `requirements.txt` and `requirements-dev.txt`.
- Main libraries: NumPy, pandas, Plotly, Streamlit, yfinance, smartapi-python, pyotp, python-dotenv, logzero, websocket-client, pytest.
- Database: SQLite through Python's built-in `sqlite3`; no separate database server is required.
- Broker: Angel One SmartAPI.

Do not casually upgrade pinned dependencies. Check compatibility, update pins intentionally, run the full test suite, and document any behavior changes.

## 4. Architecture and module responsibilities

Keep business logic out of the Streamlit presentation layer. Prefer small, testable functions and preserve the existing separation of concerns.

### Repository root

- `app.py` — Streamlit UI, dashboard controls, tabs, charts, formatting, and presentation. It calls the tested analytics/database modules; it should not become the home of financial calculation logic.
- `config/instruments.json` — configured instruments, example quantities/weights, and benchmark identity. Contains no broker tokens or secrets.
- `requirements.txt` — runtime dependencies.
- `requirements-dev.txt` — development/test dependencies, including pytest.
- `.env.example` — names of required environment variables only; never put real credentials here.
- `.gitignore` — excludes secrets, local environments, caches, temporary files, and local data as configured.
- `PROJECT_PLAN.md` — phased project scope and acceptance criteria.
- `README.md` — setup, usage, architecture, assumptions, and public-facing project description.

### `portfoliopulse/`

- `config.py` — central project paths and configuration constants, including database path and portfolio name.
- `real_portfolio.py` — real/example portfolio definitions used by the current analytics workflow.
- `data.py` — source-independent data normalization/alignment and price-data helpers.
- `data_quality.py` — OHLCV validation, duplicate/date checks, price relationships, coverage, and missingness checks.
- `sourced_csv.py` — import of separately sourced CSV market data with provenance and checksum validation.
- `source_verification.py` — comparison of daily closes and parsing/validation of NSE index reference CSVs.
- `market_data.py` — earlier Yahoo Finance research/download/cache path; keep distinct from the Angel One path and clearly label its data source.
- `database.py` — SQLite connection, initialization, parameterized queries, transactions, dataset/holding persistence, and analysis/scenario persistence.
- `schema.sql` — SQLite table, index, key, and constraint definitions. Update schema and database initialization/migration behavior together when changing persistence.
- `portfolio_returns.py` — pure calculations for fixed-quantity portfolio valuation, daily buy-and-hold returns, benchmark alignment, and annualized endpoint price returns.
- `risk_metrics.py` — risk calculations: cumulative/annualized returns, volatility, Sharpe, drawdown, historical VaR/ES, correlations, covariance, and volatility contributions.
- `portfolio_analysis.py` — orchestration for loading a saved dataset, computing portfolio/benchmark analytics, creating metadata, and persisting results. Keep it independent of Streamlit.
- `var_models.py` (Conv 3) — parametric normal, Cornish-Fisher, seeded Monte Carlo (MVN/bootstrap) VaR/ES and delta-normal Euler component VaR/ES; historical reuses `risk_metrics.historical_var_es`.
- `var_backtests.py` (Conv 3) — rolling one-day-ahead VaR forecasts, Kupiec POF, Christoffersen independence, conditional coverage.
- `covariance_estimators.py` (Conv 3) — sample, Ledoit-Wolf and EWMA (λ = 0.94) daily covariance.
- `drawdowns.py` (Conv 3) — drawdown episodes, duration and recovery time.
- `risk_analysis.py` (Conv 3) — Phase 3 risk orchestration; persists a separate run under `engine_version = phase3_risk_v1` (CLI `scripts/analyze_risk.py`).
- `factor_sensitivities.py` (Conv 4) — OLS market betas and rate/USD-INR sensitivities with SE/R²; source-labelled factor CSV loader.
- `stress_scenarios.py` (Conv 4) — `ShockVector` with separate direct / beta_propagated / covariance_propagated / factor / historical_observed components; scenario library loader (`config/stress_scenarios.json`).
- `historical_stress.py` (Conv 4) — event-window replay from observed closes; model-propagated fallback from an NSE reference series; otherwise unavailable.
- `stress_impact.py` (Conv 4) — stressed P&L (via `scenario_attribution`), sector impact, loss shares, integration with VaR/ES, drawdown and risk contributions.
- `stress_analysis.py` (Conv 4) — stress orchestration under `engine_version = phase4_stress_v1` (CLI `scripts/analyze_stress.py`).
- `expected_returns.py` (Conv 5) — arithmetic, CAGR and Bayes-Stein annual expected returns with standard errors.
- `optimization.py` (Conv 5) — covariance PSD validation, configurable constraints, SLSQP min-variance / max-Sharpe (undefined when no feasible return beats rf), log-barrier ERC with constrained fallback, efficient frontier.
- `portfolio_allocation.py` (Conv 5) — Indian delivery cost model, turnover, integer-share allocation and self-financed rebalance.
- `portfolio_evaluation.py` (Conv 5) — ex-ante and labelled in-sample metrics for any weight vector, reusing `risk_metrics` / `performance_metrics` / `var_models`.
- `optimization_backtest.py` (Conv 5) — walk-forward validation without look-ahead, net of costs.
- `optimization_analysis.py` (Conv 5) — orchestration under `engine_version = phase5_opt_v1`; writes only the migration-4 `optimization_*` tables (CLI `scripts/analyze_optimization.py`).
- `ui/stored_results.py`, `ui/charts.py` (Conv 6) — read-only (`mode=ro`) loaders for stored engine runs and Plotly chart builders; no financial logic.
- `reporting.py` (Conv 6) — CSV zip and self-contained HTML reports with a provenance header.
- `scenarios.py` — definitions/generation of broad-market, banking, IT/sector, and individual-holding shocks.
- `attribution.py` — holding-level scenario impact attribution, reconciliation, allocation drift, and concentration alerts.
- `scenario_analysis.py` — orchestration of dataset-linked scenario calculations and persistence.
- `briefing.py` — deterministic, rule-based analyst briefing derived from calculated metrics; it must not invent facts or imply predictive certainty.
- `analytics.py` — earlier/legacy demo analytics. It overlaps with the newer real-portfolio analytics path; inspect references before changing or deleting it.
- `demo.py` and `bootstrap.py` — synthetic/legacy demo data and initialization. Never mix fictional demo values with real-market performance or label them as real.
- `angelone/instrument_master.py` — retrieves/parses instrument master and resolves configured exchange/symbol identities to broker tokens; includes cache/checksum-related handling.
- `angelone/client.py` — SmartAPI authentication/client behavior, historical candle requests, date-window handling, retries, timestamp normalization, and candle validation.
- `angelone/cache.py` — local broker-response cache and metadata/integrity checks for offline replay.

### `scripts/`

Use the scripts as explicit command-line entry points rather than duplicating workflow logic:

- `refresh_angelone.py` — retrieve daily Angel One candles.
- `import_sourced_csv.py` — validate/import source-labelled CSV input and provenance.
- `ingest_phase3.py` — ingest validated source data into SQLite.
- `analyze_phase4.py` — run portfolio/benchmark analytics for a saved dataset.
- `analyze_phase5.py` — run hypothetical stress analysis for a saved dataset.
- `compare_nse_index.py` — compare saved NIFTY 50 observations with a supplied NSE reference CSV.
- `refresh_market_data.py` — earlier Yahoo Finance refresh path.
- `generate_demo_csv.py` and `seed_demo.py` — legacy/demo data utilities.

### `tests/`

Keep all tests. They cover configuration, data processing, Yahoo data, Angel One instrument/client/cache/refresh behavior, sourced CSV, data quality, Phase 3 ingestion/database, Phase 4 returns/risk/analysis, Phase 5 scenarios, and Phase 7 audit/source checks. Tests use deterministic fixtures and mocked external APIs where applicable; passing mocks does not establish live provider access.

### `docs/` and `data/`

- `docs/` contains methodology, phase records, audits, NSE reference data, and interview material. Read the relevant documents when changing the corresponding area; the latest final/maintenance audits are especially important.
- `data/` contains local datasets, metadata, raw instrument-master material, demo files, and the SQLite database. Treat these as data, not source code. Do not load huge files or dump the database into the model context unless a task specifically requires it.

## 5. Data-source hierarchy and provenance rules

Keep data paths and claims separate:

1. **Angel One SmartAPI** — primary broker source. Live historical retrieval is verified only for dataset 2 (15-day window, 2026-11-12); earlier long-window requests got HTTP 403. Keep at least 60 s request spacing. Do not claim a successful live refresh based only on mocks or cached files.
2. **Sourced CSV fallback** — separate offline/import path. Every imported dataset must retain its provider/source declaration, date window, retrieval information, file checksum, and adjustment basis in the expected sidecar/metadata. A user-declared source is not independently verified merely because it imports successfully.
3. **Yahoo Finance research data** — separate fallback/research path. The currently described local fallback is unadjusted OHLCV and user-declared/unverified. Do not relabel it as Angel One or verified NSE data.
4. **Synthetic demo data** — fictional only. Keep isolated from real portfolio analytics and dashboard outputs.
5. **NSE reference CSV** — a small manually transcribed reference sample for a limited NIFTY 50 spot check, not a complete original NSE export and not validation of all securities/dates.

Data integrity requirements:

- Preserve source/provider and adjustment-basis metadata end-to-end.
- Validate positive prices, OHLC relationships, duplicate symbol/date rows, date coverage, missingness, and instrument identity.
- Do not silently forward-fill missing prices, substitute zero, bridge missing intervals with fabricated returns, or mix adjusted and unadjusted price series.
- Do not assume an exchange calendar is complete unless it has been independently established.
- Do not overwrite immutable historical dataset/holding snapshots when current configuration changes.
- Respect provider/exchange licensing and redistribution terms.

## 6. Financial methodology and conventions

The existing documented conventions are part of the project contract. If changing one, update implementation, tests, UI labels, README, and methodology documentation together.

### Portfolio valuation and daily returns

For fixed quantity `q_i` and observed close `P_{i,t}`:

`V_t = sum_i(q_i * P_{i,t})`

`r_t = V_t / V_(t-1) - 1`

For matched daily stock returns, lagged portfolio weights times stock returns should reconcile to the fixed-quantity portfolio return. Keep benchmark comparisons on common valid dates and disclose excluded observations.

### Annualization and volatility

- Trading-day convention: 252 days per year.
- Annualized endpoint price return: `(V_end / V_start) ** (252 / observed_union_intervals) - 1`, using the first and last observed complete values and observed date intervals. Disclose sample/calendar limitations.
- Daily volatility: sample standard deviation (`ddof=1`) of valid paired daily returns.
- Annualized volatility: daily sample standard deviation multiplied by `sqrt(252)`.

### Sharpe ratio

Convert the annual effective INR risk-free assumption to an effective daily rate:

`daily_rf = (1 + annual_rf) ** (1 / 252) - 1`

`Sharpe = mean(daily_returns - daily_rf) / sample_std(daily_returns) * sqrt(252)`

Sharpe is undefined when volatility is zero or the sample is insufficient. The 6% default is illustrative and manually configurable, not a live RBI rate.

### Drawdown

Calculate observed wealth relative to its running observed peak, including starting wealth of 1. Preserve the distinction between standalone portfolio drawdown and matched-period comparison labels.

### Historical VaR and expected shortfall

At confidence `c`, with `N` valid daily returns:

`k = max(1, ceil((1 - c) * N))`

Sort returns from worst to best. Historical VaR is the nonnegative kth-worst daily loss; expected shortfall is the nonnegative average of the worst `k` losses. These are historical sample statistics, not forecasts or guaranteed loss limits. Clearly label confidence, horizon, and sample size where appropriate.

### Correlation and volatility contribution

- Pearson correlation is calculated on complete aligned daily return rows; constant series may produce undefined values.
- Annual sample covariance matrix: `Sigma`.
- Current-weight volatility proxy: `sigma_p = sqrt(w.T @ Sigma @ w)`.
- Marginal contribution: `(Sigma @ w)_i / sigma_p`.
- Component contribution: `w_i * marginal_i`; components should reconcile to the proxy volatility.
- This is a current-weight covariance snapshot/proxy and can differ from realized volatility of the drifting-weight buy-and-hold portfolio. Do not conflate the two.

### Stress scenarios and attribution

Scenarios are hypothetical one-period shocks. For holding `i`:

`impact_i_INR = quantity_i * current_price_i * shock_i`

Portfolio impact is the sum of holding impacts; portfolio percentage impact is total impact divided by portfolio value. Holding contributions must reconcile to the scenario total. Reject invalid shock inputs and clearly label assumptions as hypothetical, not forecasts.

### Return/benchmark convention

The project uses unadjusted close-based **price returns** for the described local fallback and NIFTY 50 **price index**, not TRI. Dividends/corporate actions are not independently adjusted. Never describe these as total returns.

## 7. SQLite data model

The schema currently defines these tables (some belong to the legacy demo path):

- `instruments` — instrument master.
- `portfolios` — portfolio definitions.
- `holdings` — legacy/current portfolio holding records.
- `market_observations` — legacy demo observations.
- `risk_runs`, `risk_metrics` — legacy risk-run results.
- `scenarios` — legacy scenario definitions.
- `scenario_runs`, `scenario_contributions` — dataset-linked stress run and holding-level impacts.
- `instrument_listings` — exchange/listing identities.
- `portfolio_benchmarks` — benchmark mapping.
- `candle_datasets` — source-specific dataset identity and provenance.
- `dataset_holdings` — immutable holdings snapshot associated with a dataset.
- `daily_candles` — daily OHLCV observations linked to a dataset.
- `analysis_metadata` — analysis configuration, selected window, quality/context metadata.
- `analysis_results` — persisted computed metrics.
- `optimization_runs`, `optimization_weights`, `optimization_backtests` (migration 4) — illustrative optimized allocations and walk-forward series, stored separately from holdings.

Use parameterized SQL for all values. Preserve foreign keys, unique constraints, checks, and transaction/rollback behavior. Do not make assumptions about a dataset ID (for example, ID `1`) being portable across databases. Inspect available dataset metadata first. When changing schema, update `schema.sql`, initialization/migration logic, database functions, and tests together. Avoid destructive migrations; explain backup and migration steps.

## 8. Setup and common commands (Windows PowerShell)

Run commands from the repository root. Use the project virtual environment when present.

### Create environment and install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

Runtime-only installation:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Initialize database

```powershell
.\.venv\Scripts\python.exe -c "from portfoliopulse.database import initialize; initialize()"
```

The dashboard also initializes the schema on startup. An empty database should show ingestion guidance, not fabricated/demo performance.

### Configure Angel One (only for live attempts)

```powershell
Copy-Item .env.example .env
notepad .env
```

Expected variable names:

- `ANGELONE_API_KEY`
- `ANGELONE_CLIENT_ID`
- `ANGELONE_PASSWORD`
- `ANGELONE_TOTP_SECRET`

Credentials must be entered locally by the user. Never request them in chat, print them, add them to source code, commit `.env`, or include them in logs/screenshots. Offline CSV ingestion and tests should not require broker credentials.

### Data refresh / import / analysis

Angel One refresh (start inclusive, end exclusive):

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2024-01-02 --end 2026-10-16
```

Import a lawfully obtained, source-labelled CSV (the CSV must have the expected provenance sidecar; consult `docs/PHASE_3_DATA_SQL.md` for exact format):

```powershell
.\.venv\Scripts\python.exe -m scripts.import_sourced_csv --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
```

Run Phase 4 analytics (replace dataset ID with one actually present in the local database):

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_phase4 --dataset-id 1 --annual-rf 0.06 --confidence 0.95
```

Run Phase 5 broad-market scenario (check `--help` for supported scenario options before using another type):

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_phase5 --dataset-id 1 --type broad_market
```

Compare NIFTY 50 with the limited reference sample:

```powershell
.\.venv\Scripts\python.exe -m scripts.compare_nse_index --dataset-id 1 --nse-csv docs\reference\nifty50_nse_indices_2026-10-01_2026-10-15.csv --reference-source-url https://www.niftyindices.com/reports/historical-data --output-dir .tmp\nse-audit
```

### Run tests and dashboard

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Use `--help` on scripts if arguments or behavior are unclear. Do not assume that a command succeeded until its exit status/output has been checked.

## 9. Development rules for Claude

Follow these rules for every task:

1. **Inspect before editing.** Read the relevant implementation, callers, tests, schema, and documentation before proposing or changing behavior. Use targeted file reads; do not dump the entire database, instrument master, or large CSVs into context.
2. **Establish the baseline.** Check Git status and run the relevant tests before making changes when practical. Do not overwrite uncommitted user work. Ask before destructive operations or broad refactors.
3. **Make minimal, scoped changes.** Prefer the smallest coherent change. Do not rebuild the application, rename modules, reorganize the architecture, or add dependencies without a clear requirement and explanation.
4. **Preserve public behavior.** Avoid breaking existing CLI arguments, data formats, database records, UI behavior, and documented workflows unless the task explicitly requires a change. If behavior changes, explain migration/compatibility impact.
5. **Keep calculations testable.** Put financial/business logic in pure or focused modules, not in `app.py`. Add or update unit tests with hand-calculable fixtures for financial formulas and edge cases.
6. **Test the right layers.** Run targeted tests during iteration and the full test suite before concluding. For changes involving data persistence, include database tests; for external APIs, use mocks and clearly distinguish mocked verification from live verification.
7. **Protect financial correctness.** Preserve units (INR, decimal return, percentage points), sign conventions, date alignment, sample-size behavior, missing-data rules, annualization assumptions, and adjustment basis. Avoid silent coercion or rounding that changes results.
8. **Preserve provenance.** Never fabricate source, retrieval time, exchange validation, data freshness, or successful API access. Keep metadata and adjustment labels attached to datasets and displayed outputs.
9. **Protect secrets and private data.** Never print, request, commit, or expose API keys, passwords, TOTP secrets, tokens, session responses, account identifiers, or private portfolio data. Do not read `.env` unless the user explicitly asks for a safe local configuration check; never reveal its values.
10. **Handle data safely.** Back up before database migrations or data rewrites. Do not delete user data, raw files, reference files, or the SQLite database without explicit approval. Avoid changing large datasets as part of code-only tasks.
11. **Avoid unsupported claims.** Do not claim a live API integration, official data verification, complete exchange calendar, total-return calculation, production readiness, or predictive ability unless independently demonstrated.
12. **Update documentation.** If a change affects setup, architecture, formulas, assumptions, CLI, schema, UI, or limitations, update the relevant README/docs and this file where necessary.
13. **Report clearly.** At completion, summarize files changed, behavior changed, commands/tests run and their actual results, unresolved issues, and any manual steps. Distinguish verified facts from assumptions.
14. **No unrelated feature creep.** Do not add forecasting, LLM advice, trading, broker holdings sync, deployment infrastructure, or multi-asset coverage unless explicitly requested.

## 10. Suggested workflow for a development task

1. Restate the requested outcome and identify the affected modules.
2. Inspect the current code, relevant tests, and latest audit notes.
3. Check repository status and preserve any user changes.
4. Write a short implementation plan for non-trivial work.
5. Implement a focused change with tests.
6. Run targeted tests, then the full suite where feasible.
7. Review the diff for accidental changes, secrets, generated files, and data modifications.
8. Update docs and this guide if behavior or assumptions changed.
9. Report exact verification results and any limitations. Never imply tests or live checks were run when they were not.

## 11. Best documents to consult by task

- Overall project scope and acceptance criteria: `PROJECT_PLAN.md`.
- User setup and commands: `README.md`.
- Current completed work, formula checks, known limitations, and handoff: `docs/FINAL_AUDIT.md`.
- Cleanup and maintenance findings: `docs/MAINTENANCE_AUDIT_2026-10-23.md`.
- Data ingestion, schema, CSV headers, sidecar/provenance: `docs/PHASE_3_DATA_SQL.md`.
- Portfolio-return and risk methodology: `docs/PHASE_4_ANALYTICS.md`.
- Scenario definitions and attribution: `docs/PHASE_5_STRESS_TESTS.md`.
- Dashboard behavior: `docs/PHASE_6_DASHBOARD.md`.
- Verification methodology: `docs/PHASE_7_VERIFICATION.md`.
- Resume/interview content (only when specifically working on career material): `docs/PORTFOLIOPULSE_CV_INTERVIEW.md`.

## 12. Definition of done

A development task is complete only when:

- The requested behavior is implemented in the correct layer.
- Existing functionality and data integrity are preserved.
- Relevant automated tests pass, or failures are clearly reported with evidence.
- Financial outputs retain correct methodology, units, labels, and assumptions.
- Source provenance and known limitations remain transparent.
- Documentation is updated where needed.
- No secrets, generated artifacts, or unrelated data changes are introduced.
- The final handoff states exactly what changed and what was actually verified.

## 13. Handoff log

### 2026-11-12 — Conversation 2 (Phases 1, 2, 6-framework)

Full details: `docs/HANDOFF_CONV2.md`. Branch `conv2-foundation-performance-attribution`; commits `a4b17a1` (Phase 1), `aeb251a` (Phase 2), `b4a46c6` (Phase 6 framework), plus a docs commit. Not pushed.

- **Tests (actually run):** baseline HEAD 91 passed / 57 subtests; final **146 passed / 71 subtests**. `.venv` was created from pinned `requirements-dev.txt`; no dependency changes.
- **Schema:** now versioned. `schema.sql` is the frozen v0 baseline; `portfoliopulse/migrations.py` holds v1–v3 and runs from `database.initialize()` (backup-first, transactional, idempotent). New tables: `schema_version`, `reference_datasets`, `corporate_actions`, `dataset_provenance_events`, `benchmark_constituents`, `security_classifications`. `candle_datasets` gained `verification_status` and allows `split_adjusted_ohlcv`. Add future schema changes as a new numbered migration, never by editing `schema.sql`.
- **Real DB:** backed up to `data/backups/` (git-ignored), migrated 0 → 3 after testing on a copy; dataset 1 Phase-4 metrics, all 236 stored results and scenario contributions bit-identical; integrity/FK checks clean.
- **Phase 2:** `performance_metrics.py`; new keys stored under `engine_version = phase8_perf_v1`; `Phase4Analysis.metrics` still has exactly the original 20 keys; new results in `.performance`. Label everything "price return vs NIFTY 50 price index (not TRI)".
- **Phase 6:** `benchmark_attribution.py` (Active Share, Brinson-Fachler, Carino) is fixture-tested and **gated** by `attribution_gate()`; no valid constituent weights exist, so nothing is computed on real data. Never fabricate weights.
- **Angel One 403:** only the offline sanitized diagnostic (`scripts/diagnose_angelone.py`) was run. Leads: SDK sends its hard-coded placeholder public IP (constructor IP overrides ignored); cached master from legacy `angelbroking.com` host. Live checks are a user action; still unverified.
- **Open:** dataset 1 still labelled `unadjusted_ohlcv` (jump scan consistent with split/bonus adjustment but no NSE corporate-action file imported — relabel only via `relabel_price_basis` with that evidence); constituent weights decision; security-level attribution; dashboard display of Phase 2 metrics. Next: Conversation 3 (advanced risk engine, SciPy pin).
- Status statement §2 item 2 (91 tests) is superseded by the counts above.

### 2026-11-12 — Conversation 2 addendum (Angel One live validation)

- User ran the live diagnostic locally: all calls HTTP 200 (login, profile, RMS, LTP, two candle calls). The original 403 was not reproduced, so its exact cause is unidentified (candidates: ~1,018-day windows, 10 s pacing, or since-changed account state). Static-IP enforcement, entitlement, key, session, host and route problems are ruled out for now.
- With user authorization: DB backup → 15-day live pull (6 instruments, 60 s spacing) → new cache files → `ingest_phase3 --source angel` → **dataset ID 2** (`verified_live`, `unadjusted_ohlcv`, 66 candles, 11 sessions, 10 valid returns). Integrity clean. The Sunday 2026-11-08 session and missing 2026-11-10 are unverified against an NSE calendar; RELIANCE +11.6% on 2026-11-02 is unverified.
- Dataset 1 untouched and bit-identical. Tests: 146 passed / 71 subtests.
- Angel One is **conditionally suitable** as primary: long-window access, corporate-action handling for unadjusted history, and cross-provider comparison are still open (`docs/HANDOFF_CONV2.md` §6.3). Next data step awaiting approval: long Angel pull 2024-01-02 → 2026-10-16 compared with dataset 1 and the NSE sample (§6.4).
- **Conversation 3 kickoff** (advanced risk engine only; SciPy pin first) is in `docs/HANDOFF_CONV2.md` §7.

### 2026-11-12 — Conversation 3 (roadmap Phase 3: Advanced Risk Engine)

Full details: `docs/HANDOFF_CONV3.md`. Branch `conv3-risk-engine` (from `63b25ed`); commits `6607bed` (SciPy pin), `f647be7` (var_models), `0b31722` (backtests), `16de268` (drawdowns + covariance), `7da37d9` (risk_analysis + CLI), plus a docs commit. Not pushed.

- **Dependency:** `scipy==1.18.1` pinned in `requirements.txt`; no other package changed; `pip check` clean.
- **Tests (actually run):** baseline 146 passed / 71 subtests; final **192 passed / 71 subtests**. No existing test removed or edited.
- **Engine:** new outputs only under `engine_version = phase3_risk_v1`, as a separate `analysis_metadata` run; method strings, MC seed/paths/batches and provenance labels live in `settings_json`, and the k tail observations live in `quality_json`. No schema change (still v3). Phase 4 / `phase8_perf_v1` keys and the 20-key `Phase4Analysis.metrics` are unchanged.
- **Real DB:** backup `data/backups/portfoliopulse_pre_conv3_risk_20261112T165943068479Z.db`, trial on a copy first, then **run 9** for dataset 1 (305 results). All pre-existing rows are bit-identical (hex-float fingerprint), the Phase-4/perf hex dump is identical, and integrity/FK checks are clean. Dataset 2 was smoke-tested with `--no-save` only.
- **Dataset 1 (unverified Yahoo, N = 686):**
  - 99% historical VaR 2.127% with k = 7;
  - normal 2.032%, Cornish-Fisher 2.678%;
  - MVN MC 2.034% (SE 0.011%), bootstrap 2.191%;
  - max drawdown −29.5%, unrecovered.
  - The historical 95% Kupiec test rejects at 5% (33 exceptions vs 21.8 expected).
  - These are in-sample statistics, not forecasts.
- **Open:** dataset 1 basis is unconfirmed (an NSE corporate-action file is needed before its tail/drawdown dates are treated as real events); dashboard display of Phase 3 outputs is not done. Next: Conversation 4 = roadmap Phase 4 stress testing (kickoff in `docs/HANDOFF_CONV3.md` §6).
- Status statement §2 item 2 test counts are superseded by the figures above.

### 2026-11-12 — Conversation 4 (roadmap Phase 4: Portfolio Stress Testing)

Full details: `docs/HANDOFF_CONV4.md`. Branch `conv4-stress-testing` (from `4892ec1`, the unmerged `conv3-risk-engine` tip); commits `24a1c30` (pure modules + unit tests), `1cb0bb0` (orchestration + CLI), plus a docs commit. Not pushed.

- **Tests (actually run):** baseline 192 / 71 subtests; final **219 passed / 71 subtests**. No existing test removed or edited. No new dependency.
- **Design:** shock components keep direct and beta-/covariance-/factor-propagated moves apart, and every holding carries a `shock_basis`. P&L reuses `attribution.scenario_attribution`; direct presets reuse `scenarios.scenario_shocks`; VaR/ES come from `risk_analysis.compute_risk_analysis`.
- **Schema:** unchanged (v3). Results are one `analysis_metadata` run (`phase4_stress_v1`) plus existing-schema `scenario_runs`/`scenario_contributions` rows with new `scenario_type` values. Reruns are idempotent.
- **Real DB:**
  - backup `data/backups/portfoliopulse_pre_conv4_stress_20261112T171648238607Z.db`, trial on a copy first;
  - **analysis run 10** and **scenario runs 3–18**;
  - all prior rows, datasets and holdings bit-identical; integrity/FK clean.
- **Data limits:**
  - COVID-2020 and 2022 events are **unavailable**, because dataset 1 starts 2024-01-02. Never fill index moves from memory; an NSE Indices CSV via `--nse-reference-csv` makes them `model_propagated`.
  - Rate/FX scenarios use **illustrative assumed** sector sensitivities from config unless a source-labelled factor CSV is supplied.
- **Dataset 1 headline:** NIFTY −10% beta-propagated = −9.18% (₹−10,945), about 4.3× historical 99% one-day VaR. The worst observed NIFTY drawdown replay gives −13.46%.
- **Next:** Conversation 5 = roadmap Phase 5 optimization (kickoff in `docs/HANDOFF_CONV4.md` §6).

### 2026-11-12 — Conversation 5 (roadmap Phase 5: Portfolio Construction & Optimization)

Full details: `docs/HANDOFF_CONV5.md`. Branch `conv5-optimization` (from `9f0e115`, the unmerged conv4 tip); commits `e882e9d`, `02a7226`, `373e8d0`, plus a docs commit. Not pushed.

- **Tests (actually run):** baseline 219 / 71; final **250 passed / 71 subtests**. No test removed or edited; no new dependency.
- **Schema v4 (additive):** `optimization_runs`, `optimization_weights`, `optimization_backtests`. Optimized weights are never written to `dataset_holdings`, `holdings` or `config/instruments.json`. Results are stored under `engine_version = phase5_opt_v1`.
- **Real DB:**
  - backup `data/backups/portfoliopulse_pre_conv5_optimization_20261112T173719020304Z.db`, plus the migration auto-backup; trial on a copy first;
  - **analysis run 11** with 7 strategy runs and 2,170 walk-forward rows; rerun idempotent;
  - all prior tables, schema v1–v3 rows and the Phase-4 hex dump are bit-identical; integrity/FK clean.
- **Dataset 1 (unverified Yahoo, N = 686):**
  - **max-Sharpe is undefined** at rf 6%, because the best feasible in-sample E[r] is 1.98%; a labelled rf 0% sensitivity is reported instead;
  - min-variance lowers ex-ante volatility only from 13.69% to 13.47%;
  - exact ERC breaches the 40% Banking cap, so a constrained solution is used;
  - out of sample (2025-01 to 2026-10), every strategy returned about −12% to −16% a year net, vs NIFTY 50 −1.1%.
  - Mean standard errors of about 12–16% a year dominate. All results are illustrative and in-sample, not recommendations.
- **Dataset 2** is refused (`InsufficientDataError`, below 252 returns).
- **Next:** Conversation 6 = dashboard/reporting (kickoff in `docs/HANDOFF_CONV5.md` §7).

### 2026-11-12 — Conversation 6 (roadmap Phase 7: Dashboard & Reporting)

Full details: `docs/HANDOFF_CONV6.md`. Branch `conv6-dashboard` (from `c4ca993`, the unmerged conv5 tip); commit `4e037f5` plus a docs/fix commit. Not pushed.

- **Tests (actually run):** baseline 250 / 71; final **256 passed / 71 subtests**. Streamlit `AppTest` renders all 11 tabs with zero exceptions on a fixture DB, an empty DB, and a dataset without runs. A manual browser check against a DB copy found and fixed one label collision.
- **Design:** the new tabs only read stored `phase3_risk_v1` / `phase4_stress_v1` / `phase5_opt_v1` runs through `mode=ro` connections. The existing five tabs are unchanged. `PORTFOLIOPULSE_DB` overrides the DB path; **tests must hold it across AppTest reruns** (see `tests/test_dashboard.py::_pointed_at`).
- **DB:** all prior rows are bit-identical. **Analysis 12** (dataset 2, `phase8_perf_v1`, rf 0.06 / 95%) was added to the real DB by an early test rerun that fell back to the default path. It is the same row the dashboard creates when a user opens dataset 2, the user decided to keep it.
- **Open:** the existing Performance path still writes Phase 4 runs on load (pre-existing behaviour; making it read-only needs the user's decision); the branch stack conv2→conv6 awaits review and merge; the data decisions carried forward are unchanged.
