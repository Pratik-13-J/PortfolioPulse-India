# PortfolioPulse India

**An Indian equity portfolio risk monitor built with Python, pandas, NumPy, SQLite, Streamlit and Plotly.**

PortfolioPulse analyses a configurable buy-and-hold portfolio of NSE-listed shares in INR, compares its price performance with the NIFTY 50 price index, and explores transparent hypothetical stress scenarios. It is an educational analytics project for an asset-allocation internship portfolio; it is not investment advice or a trading system.

## Project status

| Area | Status |
|---|---|
| Portfolio analytics, scenario attribution and dashboard | Implemented; the portfolio uses fixed share quantities and weights drift with prices. |
| Automated verification | 146 pytest tests and 71 subtests pass locally (2026-11-12, Conversation 2). The tests use deterministic/mock data and hand-checkable examples. |
| Angel One SmartAPI | Historical-candle adapter, current instrument-master resolution, normalization, retries and offline-cache workflow are implemented and mock-tested. Live authentication succeeded, but historical requests were denied with HTTP 403 access-rate responses; a live Angel One candle dataset has not been verified. |
| Dataset currently used for local analysis | A separate Yahoo Finance OHLCV CSV imported through the source-labelled fallback, marked `unadjusted_ohlcv` and user-declared/unverified. It is not Angel One data. |
| Schema and provenance (Conv 2) | SQLite schema is versioned (`schema_version`, currently 3) with backup-first migrations. Datasets carry a `verification_status` ladder and an append-only provenance-event ledger. `split_adjusted_ohlcv` is now a supported basis; dataset 1 is still labelled `unadjusted_ohlcv` because no NSE corporate-action file has been imported to justify a relabel. |
| Relative performance (Conv 2) | Tracking error, information ratio, beta, downside beta, up/down capture, Sortino, rolling windows, period returns and Carino-linked security contributions, stored under `engine_version = phase8_perf_v1`. |
| Benchmark attribution (Conv 2) | Active Share and Brinson-Fachler with Carino linking are implemented and fixture-tested only. They are **gated**: no valid NIFTY 50 constituent weights have been imported, so no attribution is calculated or shown. |
| NSE independent validation | Ten displayed official NIFTY 50 price-index closes were compared with the saved provider series within 0.01 index points. The remaining dates and individual stocks are unverified. |

The local analysis record and detailed formulas are in [Phase 3 data and SQL](docs/PHASE_3_DATA_SQL.md), [Phase 4 analytics](docs/PHASE_4_ANALYTICS.md), [Phase 5 stress testing](docs/PHASE_5_STRESS_TESTS.md), [Phase 6 dashboard](docs/PHASE_6_DASHBOARD.md), and [Phase 7 verification](docs/PHASE_7_VERIFICATION.md). The latest [maintenance audit](docs/MAINTENANCE_AUDIT_2026-10-23.md) records the project check and remaining cleanup. The phased scope is in [PROJECT_PLAN.md](PROJECT_PLAN.md).

## What it does

- Ingests daily Angel One SmartAPI OHLCV candles for configured NSE instruments, resolving symbols against the current instrument master instead of assuming broker tokens.
- Offers a separate, provenance-labelled CSV import path for offline/fallback analysis. Provider, date window, retrieval, file checksum and adjustment basis are recorded with each dataset.
- Validates candles, duplicate symbol/date observations, price relationships, date coverage and missingness; missing prices are not forward-filled or replaced with zero.
- Stores source-specific datasets and immutable portfolio snapshots in SQLite, so a later holding-configuration change does not rewrite old analyses.
- Calculates buy-and-hold returns, annualized return and volatility, Sharpe ratio, maximum drawdown, historical one-day VaR and expected shortfall, correlations and volatility contributions.
- Models configurable broad-market, banking, IT, sector and individual-stock shocks, with holding-level INR and portfolio-percentage attribution that reconciles to the scenario total.
- Displays results and data-quality context in a Streamlit/Plotly dashboard, with editable session-only target weights and a rule-based briefing from computed metrics.

## Example portfolio and conventions

The configured example uses RELIANCE, HDFCBANK, INFY, ICICIBANK and ITC, with NIFTY 50 as a separate benchmark. Quantities and target weights are examples in [config/instruments.json](config/instruments.json); they are not a recommendation. The selected portfolio model is **buy and hold**: share quantities stay fixed, current market weights drift, and target weights are only a reference for allocation-drift alerts.

The default INR annual risk-free assumption is configurable in the dashboard/analysis command. The example `6%` value is illustrative and is not fetched from RBI. Analytics use 252 trading days. Annualized return uses the observed first-to-last price ratio raised to `252 / observed date intervals`; daily volatility and Sharpe use sample standard deviation (`ddof=1`) annualized by `sqrt(252)`. Sharpe converts the annual effective risk-free assumption to an effective daily rate. Historical VaR/ES use the nearest-rank worst `ceil((1-confidence) × N)` daily returns.

Scenario outputs are **hypothetical one-period sensitivities**, not predictions or historically calibrated forecasts. Portfolio and NIFTY daily comparisons use matching valid dates. Missing observations remain visible and returns touching missing prices are excluded from the paired sample.

### Price adjustment and benchmark

The configured benchmark is **NIFTY 50 price index**, not NIFTY 50 TRI. The currently used local fallback is provider OHLCV labelled `unadjusted_ohlcv`; price returns from those closes are not dividend-aware total returns. The pipeline records the adjustment basis and does not infer one. Corporate actions, dividend treatment, data coverage and provider differences can affect historical results. Do not compare price-index returns with a total-return benchmark as if the conventions matched.

## Architecture

```text
app.py                         Streamlit controls, charts and presentation
portfoliopulse/
  angelone/                    SmartAPI auth, instrument lookup, candle normalization/cache
  data_quality.py              OHLCV and date-coverage validation
  sourced_csv.py               independent source-labelled CSV import
  database.py + schema.sql     parameterized SQLite access and schema
  portfolio_returns.py         fixed-quantity price returns and alignment
  risk_metrics.py              risk formulas and volatility contributions
  portfolio_analysis.py        dataset-linked analysis orchestration and persistence
  performance_metrics.py       TE, IR, beta, capture, Sortino, rolling, period returns, contributions
  benchmark_attribution.py     gated Active Share, Brinson-Fachler and Carino linking
  migrations.py                versioned, idempotent, backup-first schema migrations
  reference_data.py            SHA-256-sidecar importers: corporate actions, constituents
  providers/                   provider protocol over the Angel One, CSV and Yahoo modules
  scenarios.py + attribution.py hypothetical shock generation and reconciliation
  briefing.py                  deterministic text from computed metrics
scripts/                       refresh, import, analysis and source-comparison commands
tests/                          deterministic unit and integration checks
docs/                           methodology, phase verification and interview notes
data/                           local database, source data and reports (ignored by Git)
```

SQLite separates older demo observations from source-specific `candle_datasets`. Each saved dataset has its own `daily_candles`, `dataset_holdings` snapshot and source metadata. `analysis_metadata` and `analysis_results` retain selected windows, settings, quality details and results; `scenario_runs` and `scenario_contributions` preserve hypothetical shock settings and holding attribution. Foreign keys, unique keys and checks constrain identities, dates and values; database writes use transactions.

## Screenshots

Add reviewed screenshots here before publishing the repository. Use sanitized data only; never include credentials, account identifiers or private portfolio values.

- **Dashboard overview:** `[screenshot placeholder — save as docs/screenshots/dashboard-overview.png]`
- **Performance and risk charts:** `[screenshot placeholder — save as docs/screenshots/performance-risk.png]`
- **Stress test and holding attribution:** `[screenshot placeholder — save as docs/screenshots/stress-test.png]`

## Installation and run (Windows PowerShell)

Requires Python 3.12 for the verified local setup. From the repository root:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

To run the dashboard after importing a real dataset:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

If no real dataset exists, the dashboard explains that ingestion is required; it does not present the fictional demo as real market performance. Streamlit's [official run instructions](https://docs.streamlit.io/develop/concepts/architecture/run-your-app) describe this local command.

### Configure Angel One SmartAPI

Credentials are read locally from environment variables. Create an ignored `.env` from the example, then enter credentials on your computer:

```powershell
Copy-Item .env.example .env
notepad .env
```

The expected names are `ANGELONE_API_KEY`, `ANGELONE_CLIENT_ID`, `ANGELONE_PASSWORD` and `ANGELONE_TOTP_SECRET`. Never commit `.env` or expose these values in logs or screenshots. Angel One's official [SmartAPI documentation](https://smartapi.angelone.in/docs) describes historical candle requests, instrument tokens and intervals. This project requests daily (`ONE_DAY`) OHLCV and records the source and price basis.

Refresh an inclusive start / exclusive end window:

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2024-01-02 --end 2026-10-16
```

Import a legally obtained, source-labelled fallback CSV with a provenance sidecar:

```powershell
.\.venv\Scripts\python.exe -m scripts.import_sourced_csv --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
```

See [Phase 2/3 data instructions](docs/PHASE_3_DATA_SQL.md) for the exact header, sidecar fields, offline replay and provenance rules. Downloaded market data is kept local; review provider/exchange terms before redistributing it.

### Calculate and validate

Run Phase 4 analytics for a saved dataset ID. The 6% annual INR risk-free assumption below is illustrative; use the value appropriate for your analysis and disclose it.

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_phase4 --dataset-id 1 --annual-rf 0.06 --confidence 0.95
```

Relative-performance metrics are saved with every Phase 4 run (same command). All comparisons are **price return vs NIFTY 50 price index**, not TRI.

Diagnose the Angel One HTTP 403 without exposing secrets (offline by default; `--live` authenticates from your local `.env` and prints only status codes, hosts, header names, error codes and fixed labels):

```powershell
.\.venv\Scripts\python.exe -m scripts.diagnose_angelone
.\.venv\Scripts\python.exe -m scripts.diagnose_angelone --live --idle-seconds 65 --attempts 2 --spacing 60
```

Run all automated tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

The current local run (2026-11-12) completed with **146 tests passing and 71 passing subtests**. API tests use mocks; live Angel One historical access remains unverified.

An independent **ten-date spot check** of NIFTY 50 price-index closes for 1–15 October 2026 was completed against values displayed on [NSE Indices historical data](https://www.niftyindices.com/reports/historical-data). All ten provider values were within 0.01 index points; the largest absolute difference was 0.00078125 points. The [reference CSV](docs/reference/nifty50_nse_indices_2026-10-01_2026-10-15.csv) is a transcription of the displayed official table, not an original NSE export. See [Final audit](docs/FINAL_AUDIT.md) for the comparison command and limitations. For a broader comparison, obtain an official NIFTY 50 price-index CSV and follow the checklist in [Phase 7 verification](docs/PHASE_7_VERIFICATION.md).

## Limitations

- The live Angel One historical-candle gate remains pending after an HTTP 403 access-rate denial. Mock tests validate request handling, not live candle delivery.
- The current local analysis source is Yahoo Finance OHLCV, labelled unadjusted and user-declared/unverified. The ten-date NSE NIFTY 50 spot check supports only those selected index closes; it does not validate the remaining dates or stock prices.
- Dataset 1 shows no daily move above 35% across the window, which is consistent with split/bonus adjustment, but this has not been checked against NSE corporate-action ex-dates; its `unadjusted_ohlcv` label is unconfirmed.
- Benchmark attribution stays disabled until a constituent file with valid weights (official or explicitly labelled estimated) and NSE industry classifications is imported.
- Unadjusted close data may omit dividends and corporate-action effects; it does not support a total-return claim.
- The observed union dates are not an independently verified exchange calendar. Missing quotes can omit a daily loss; the pipeline reports gaps instead of fabricating values.
- Historical VaR/ES depend on the selected sample and are not loss guarantees. Stress shocks are hypothetical and depend on analyst-selected settings.
- This release models an Indian **equity** portfolio. The fictional demo includes other asset classes but is isolated and is not evidence of live commodity or debt analytics.
- No automated deployment, broker holdings synchronization, trading, forecasts or LLM-generated investment advice are implemented.

## Sources

- [Angel One SmartAPI official documentation](https://smartapi.angelone.in/docs)
- [Angel One SmartAPI Python SDK](https://github.com/angel-one/smartapi-python)
- [NSE All Reports](https://www.nseindia.com/all-reports)
- [NSE Indices historical data](https://www.niftyindices.com/reports/historical-data)
- [Streamlit: run your app](https://docs.streamlit.io/develop/concepts/architecture/run-your-app)

## CV and interview handoff

See [CV bullets and interview preparation](docs/PORTFOLIOPULSE_CV_INTERVIEW.md) for three evidence-based resume bullets and ten project questions with concise answers.
