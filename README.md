# PortfolioPulse India — Version 1.0

**Indian equity portfolio analytics and risk monitoring with Python, pandas, NumPy, SciPy, SQLite, Streamlit and Plotly.**

## 1. Project overview

PortfolioPulse India analyses a configurable **buy-and-hold** portfolio of NSE-listed shares in INR against the **NIFTY 50 price index**. It measures performance, benchmark-relative behaviour, risk (VaR/CVaR with backtests), hypothetical and historical stress scenarios, and illustrative portfolio construction. Every number is shown with its data source, verification status and price basis.

It is an **educational analytics project**. It is not investment advice, a trading system, a forecasting tool or a production portfolio-management platform.

The configured example universe is RELIANCE, HDFCBANK, INFY, ICICIBANK and ITC (four sectors), with NIFTY 50 as the benchmark ([config/instruments.json](config/instruments.json)). Quantities and target weights are illustrative. Share quantities stay fixed, and market weights drift with prices.

## 2. Key features

| Area | What is implemented |
|---|---|
| Data | Angel One SmartAPI adapter (live-verified for a 15-day window); source-labelled CSV import with SHA-256 sidecars; OHLCV quality validation (no forward-fill); immutable dataset/holding snapshots; versioned, backup-first schema migrations; provenance ledger with a verification-status ladder |
| Performance | Buy-and-hold valuation and daily returns, cumulative and endpoint-annualized price return, volatility, Sharpe, Sortino, maximum drawdown with duration/recovery, period returns, Carino-linked security contributions |
| Benchmark | Tracking error, information ratio, beta, downside beta, geometric up/down capture, rolling 63/126/252-day TE and beta |
| Attribution | Active Share and Brinson-Fachler with Carino linking, fixture-tested and **gated** until real NIFTY 50 constituent weights are imported |
| Risk (engine `phase3_risk_v1`) | Historical, normal, Cornish-Fisher and seeded Monte Carlo (multivariate-normal and bootstrap) one-day VaR/ES at 95%/99%; Kupiec and Christoffersen backtests; sample, Ledoit-Wolf and EWMA covariance; Euler component VaR/ES and volatility contributions |
| Stress (engine `phase4_stress_v1`) | Versioned scenario library: NIFTY beta-propagated shocks, direct stock/sector shocks, covariance-propagated shocks, combined market+sector, rate/USD-INR sensitivities (illustrative assumed values unless a sourced factor file is supplied), historical event replay from observed closes |
| Optimization (engine `phase5_opt_v1`) | Equal weight, minimum variance, maximum Sharpe, constrained risk parity (ERC), 25-point efficient frontier; weight, concentration, sector and turnover constraints; Indian delivery cost model; integer-share allocation; walk-forward out-of-sample test |
| Corporate actions | NSE Corporate Actions CSV converter, ex-date evidence classifier and dataset-version builder (awaiting the official NSE file) |
| Dashboard | Read-only Streamlit app with 11 tabs, Plotly charts, CSV/HTML report export |

## 3. System architecture

```text
config/ ──► portfoliopulse (pure calculations) ──► orchestration ──► SQLite (data/portfoliopulse.db)
                                                        ▲                       │ read-only (mode=ro)
scripts/ (CLI: ingest, analyze, stress, optimize) ──────┘                       ▼
                                                              app.py + portfoliopulse/ui (Streamlit)
```

1. **Ingestion and validation:** `angelone/`, `sourced_csv.py`, `data_quality.py`, `reference_data.py`, `nse_corporate_actions.py`.
2. **Persistence:** `database.py`, `schema.sql` (baseline) and `migrations.py` (v1–v4).
3. **Pure calculations:** `portfolio_returns.py`, `risk_metrics.py`, `performance_metrics.py`, `var_models.py`, `var_backtests.py`, `covariance_estimators.py`, `drawdowns.py`, `factor_sensitivities.py`, `stress_scenarios.py`, `historical_stress.py`, `stress_impact.py`, `expected_returns.py`, `optimization.py`, `portfolio_allocation.py`, `portfolio_evaluation.py`, `optimization_backtest.py`, `benchmark_attribution.py`, `attribution.py`, `scenarios.py`.
4. **Orchestration:** `portfolio_analysis.py`, `risk_analysis.py`, `stress_analysis.py`, `optimization_analysis.py`, `price_basis_versioning.py`. Each records an `engine_version` and its settings.
5. **Presentation:** `app.py`, `portfoliopulse/ui/` (stored-run loaders, charts), `reporting.py`.

Business logic lives in the tested modules; `app.py` only displays results. The scripts in `scripts/` are the only path that writes analysis results to the database.

## 4. Technology stack

- **Language:** Python 3.12 (verified 3.12.10 on Windows 11).
- **Libraries, pinned in `requirements.txt`:** numpy 2.5.4, pandas 3.0.6, scipy 1.18.1, streamlit 1.66.0, plotly 7.1.0, yfinance 1.7.0, smartapi-python 1.5.5, pyotp, python-dotenv, logzero and websocket-client. pytest 9.1.1 is in `requirements-dev.txt`.
- **Storage:** SQLite through the Python standard library; no database server is needed.

## 5. Project directory structure

```text
app.py                      Streamlit dashboard (read-only)
config/                     instruments.json, stress_scenarios.json, optimization.json
portfoliopulse/             analytics package (see §3); ui/ and angelone/ subpackages
scripts/                    command-line workflows (ingest, analyze_*, corporate_actions, audit_consistency, ...)
tests/                      unit, integration, financial-audit and dashboard (AppTest) tests
docs/                       methodology, phase records, handoffs, FINANCIAL_METHODOLOGY_AUDIT.md, HANDOFF_FINAL.md
data/                       local database, inputs, backups and reports (git-ignored, except a synthetic demo file)
requirements*.txt, .env.example, CLAUDE.md, PROJECT_PLAN.md
```

## 6. Database and data provenance

The SQLite database is `data/portfoliopulse.db`, schema version 4. Its main tables:
- `candle_datasets` with `daily_candles` and `dataset_holdings`: one immutable snapshot per dataset.
- `analysis_metadata` with `analysis_results`: one row per engine run, with settings and quality JSON.
- `scenario_runs` with `scenario_contributions`: stress results.
- `optimization_runs`, `optimization_weights` and `optimization_backtests`: illustrative allocations, never written to the holdings.
- `reference_datasets`, `corporate_actions` and `dataset_provenance_events`: reference data and the audit trail.

The local datasets:

| Dataset | Source | Status | Use |
|---|---|---|---|
| 1 | Yahoo Finance via yfinance 1.7.0 (CSV import), 2024-01-02 → 2026-10-15, 686 valid returns | `user_declared_unverified`; labelled `unadjusted_ohlcv` (see limitations) | Main analysis sample: stored runs 9 (risk), 10 (stress), 11 (optimization) |
| 2 | Angel One SmartAPI live pull, 2026-10-29 → 2026-11-12, 10 returns | `verified_live`, unadjusted | Smoke tests only; too short for tail statistics or optimization |

Provenance rules:
- Source, price basis and verification status travel with every result.
- Missing prices are never filled.
- Comparisons are **price return vs NIFTY 50 price index (not TRI)**.
- A ten-date NIFTY 50 spot check against NSE Indices matched within 0.01 index points ([docs/FINAL_AUDIT.md](docs/FINAL_AUDIT.md)).

## 7. Installation (Windows PowerShell)

Prerequisite: Python 3.12 (`python --version`). From the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

Create or upgrade the database schema. This is safe to repeat, and it takes a backup first when data exists:

```powershell
.\.venv\Scripts\python.exe -c "from portfoliopulse.database import initialize; initialize()"
```

Load market data. Use one of these two routes; the market data itself is not distributed with the repository.

```powershell
# Source-labelled CSV with a provenance sidecar (format: docs/PHASE_3_DATA_SQL.md)
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
```

```powershell
# Angel One SmartAPI (needs .env credentials; start inclusive, end exclusive)
.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2026-10-29 --end 2026-11-13
```

Then create the stored engine runs that the dashboard displays. Replace `1` with your dataset ID; each command reuses an identical existing run instead of duplicating it.

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_risk --dataset-id 1
.\.venv\Scripts\python.exe -m scripts.analyze_stress --dataset-id 1
.\.venv\Scripts\python.exe -m scripts.analyze_optimization --dataset-id 1
```

## 8. Configuration

- **`config/instruments.json`:** the universe, quantities, target weights and benchmark.
- **`config/optimization.json`:**
  - risk-free rate (6%, illustrative; not a live RBI rate);
  - expected-return method and covariance estimator;
  - constraints: 5–35% per stock, Banking ≤ 40%, an optional turnover limit;
  - transaction-cost components, which are assumptions to verify against your broker's contract note;
  - investment amount and walk-forward windows.
- **`config/stress_scenarios.json`:** the versioned scenario library, including the illustrative rate/FX sensitivities.
- **`.env`:** Angel One credentials only, needed for live refreshes. Copy `.env.example` to `.env` and fill it in locally; never commit or share it. Offline analysis and tests need no credentials.
- **`PORTFOLIOPULSE_DB`:** optional environment variable that points the dashboard at a different database file.

## 9. Local execution

There is one launch command, run from the repository folder:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Then open the URL it prints (normally http://localhost:8501). Stop the app with `Ctrl+C`. With the virtual environment activated (`.\.venv\Scripts\Activate.ps1`), `streamlit run app.py` is equivalent.

## 10. Dashboard overview

The dashboard is **read-only**. It opens SQLite with `mode=ro`, never initializes or migrates the schema, and computes the sidebar-driven analytics in memory without saving them.

By default it opens the most recent dataset that has stored engine runs. Tabs:

| Tab | Content |
|---|---|
| Performance | Growth of ₹100 vs NIFTY 50; drawdown |
| Benchmark comparison | TE, IR, beta, capture ratios, rolling TE/beta, period returns |
| Portfolio & targets | Holdings, session-only target weights, drift and concentration alerts |
| Risk & diversification | Correlation heatmap, volatility contributions |
| VaR & backtests | Stored VaR/ES by method, the k worst days, Kupiec/Christoffersen, drawdown duration, component VaR/ES |
| Stress tests | Interactive direct shocks (hypothetical) |
| Stress library | Stored scenario comparison, heatmap, waterfall, shock basis, unavailable events with reasons |
| Optimization | Status notices, frontier, strategy comparison, integer-share allocation, walk-forward, trade-offs |
| Attribution | Gated message (no constituent weights) |
| Data & methodology | Source, retrieval time, price basis, coverage, excluded dates |
| Reports | CSV zip and self-contained HTML report with a provenance header |

## 11. Testing

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m scripts.audit_consistency --dataset-id 1
```

- **Version 1.0 result (2026-11-13): 280 passed, 71 subtests passed.** The same result was reproduced in a clean virtual environment built from the pinned requirements.
- **What the suite covers:**
  - hand-calculated financial fixtures, including the independent audit in `tests/test_financial_audit.py`;
  - integration tests on temporary databases;
  - migrations;
  - Streamlit `AppTest` renders, run against an OS-level read-only database file.
- **`audit_consistency`** is a read-only cross-module check of the stored runs: dates, portfolio value, INR conversion, reconciliations, a Kupiec recomputation, constraints and the walk-forward window. It reported 29/29 PASS.

The full methodology audit is in [docs/FINANCIAL_METHODOLOGY_AUDIT.md](docs/FINANCIAL_METHODOLOGY_AUDIT.md).

### Troubleshooting

- **"No real Indian market dataset is stored yet":** no `data/portfoliopulse.db` exists, or it has no dataset. Run the initialization and import steps in §7.
- **Empty VaR, Stress library or Optimization tabs:** that dataset has no stored engine run. The tab shows the exact command to create one.
- **`Optimization failed: ... needs at least 252`:** the dataset is too short (e.g. dataset 2), so use dataset 1.
- **`pip` errors:** confirm Python 3.12 and a fresh `.venv`. Do not mix in globally installed packages.
- **Port already in use:** run `.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502`.
- **Angel One HTTP 403:** run `.\.venv\Scripts\python.exe -m scripts.diagnose_angelone` (offline and sanitized), and see [docs/HANDOFF_CONV2.md](docs/HANDOFF_CONV2.md).

## 12. Known limitations

- **Unverified main data.** Dataset 1 is Yahoo data and user-declared/unverified. Its `unadjusted_ohlcv` label is probably wrong: the evidence shows split/bonus adjustment and a possibly unadjusted ITC demerger. Confirming this needs the **official NSE Corporate Actions file**; the tooling is ready (`scripts.corporate_actions`). A corrected Dataset 3 will be created only from that file.
- **Price returns only.** NIFTY 50 is the price index, not the total-return index (TRI), and dividends are not included.
- **Attribution is gated.** Benchmark attribution stays off until NIFTY 50 constituent weights with NSE industry classifications are imported. Weights are never fabricated.
- **Rate and FX stress use illustrative sensitivities.** They are not estimated, because no RBI/FBIL series has been supplied. The COVID-2020 and 2022 events are unavailable: dataset 1 starts in 2024, and no index moves are filled from memory.
- **Short, single-regime sample.** VaR/ES, backtests, betas, covariances and optimized weights are in-sample statistics on about 2.7 years of data. Expected-return standard errors are about 12–16% a year. Max-Sharpe is undefined at the 6% risk-free assumption. Walk-forward covers about 1.7 years.
- **Narrow universe.** Five stocks in four sectors, selected today, means no broad diversification claim and survivorship/selection bias.
- **Limited Angel One verification.** Live access is verified for one 15-day window only; long-window access is untested.
- **Out of scope:** trading, broker holdings synchronization, forecasting, LLM advice, monitoring and cloud deployment.

## Sources

- [Angel One SmartAPI documentation](https://smartapi.angelone.in/docs)
- [Angel One SmartAPI Python SDK](https://github.com/angel-one/smartapi-python)
- [NSE corporate filings — corporate actions](https://www.nseindia.com/companies-listing/corporate-filings-actions)
- [NSE Indices historical data](https://www.niftyindices.com/reports/historical-data)
- [Streamlit: run your app](https://docs.streamlit.io/develop/concepts/architecture/run-your-app)

Further reading:
- [docs/HANDOFF_FINAL.md](docs/HANDOFF_FINAL.md): final handoff;
- [CLAUDE.md](CLAUDE.md): developer and agent guide;
- [docs/PORTFOLIOPULSE_CV_INTERVIEW.md](docs/PORTFOLIOPULSE_CV_INTERVIEW.md): CV and interview notes.
