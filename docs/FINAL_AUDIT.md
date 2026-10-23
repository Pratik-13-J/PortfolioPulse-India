# PortfolioPulse India — final project audit

Audit date: 16 October 2026. Repository: `D:\prjkts_to_do\Project-Russell_Investment-Portfolio_Risk_Monitor`.

## Executive status

**CONDITIONALLY READY** for an internship demonstration of an Indian equity risk monitor. The Python/SQLite/Streamlit application runs, the core finance tests pass, the offline source-labelled dataset produces a working dashboard, and ten NIFTY 50 closes passed an independent NSE Indices spot check. It is **not ready to present as a verified live Angel One feed or a fully exchange-validated backtest**. The broker historical endpoint previously returned HTTP 403, the stored Yahoo source is marked `user_declared_unverified`, and the NSE check covers only ten index dates, not individual stock prices or the full sample.

The selected model is fixed share quantities with drifting market weights. Editable dashboard *target* weights control drift and concentration context; they do not rebalance or change the saved holdings or historical performance. The dashboard selects a saved dataset window; it has no arbitrary date-range control. These are deliberate scope limits to state in a demonstration.

## Requirements-to-implementation matrix

| Intended feature | Implementation | Status and evidence |
|---|---|---|
| Daily Indian historical ingestion | `portfoliopulse/angelone/client.py`, `instrument_master.py`, `scripts/refresh_angelone.py` | **Partially implemented:** request normalization, bounded windows and mocked failure/retry tests pass; live historical candle delivery remains unverified after HTTP 403. |
| Offline market CSV fallback | `portfoliopulse/sourced_csv.py`, `scripts/import_sourced_csv.py` | **Implemented:** CSV and sidecar provenance/checksum validation pass tests without credentials. Dataset 1 uses this path. |
| Cleaning, coverage, adjustment labels | `portfoliopulse/data_quality.py`, `angelone/cache.py` | **Implemented:** duplicate, OHLC, date and missingness tests pass; source/price basis remain visible. Provider truth beyond the spot check is unverified. |
| Source and instrument tracking | `config/instruments.json`, `angelone/instrument_master.py`, `database.py` | **Implemented:** exact NSE symbol and token resolution is mock-tested; official [instrument master documentation](https://smartapi.angelone.in/docs/Instruments) was checked. A checksum-verified local master retrieved 16 October 2026 resolved all six configured NSE instruments exactly; this does not prove historical-candle access. |
| SQL storage and retrieval | `portfoliopulse/schema.sql`, `database.py` | **Implemented:** primary/foreign keys, unique constraints, transactions, snapshot lineage and idempotence tests pass. Existing DB integrity and foreign-key checks pass; fresh 16-table initialization passes. |
| Portfolio construction | `portfolio_returns.py`, `portfolio_analysis.py` | **Implemented as buy and hold:** fixed quantities, daily lagged market-value return contributions and drifting weights; hand-calculated tests pass. No daily-rebalanced portfolio mode. |
| Portfolio vs NIFTY 50 | `portfolio_returns.py`, `portfolio_analysis.py`, `app.py` | **Implemented:** aligned daily risk samples and shared-date growth chart. Ten selected benchmark closes independently checked; full-series source validation remains open. |
| Return, volatility, Sharpe, drawdown | `risk_metrics.py`, `portfolio_analysis.py` | **Implemented:** deterministic financial and missing-date tests pass. |
| Historical VaR and expected shortfall | `risk_metrics.py` | **Implemented:** 95%/99% dashboard control and nearest-rank tail tests; browser change from 95% to 99% changed displayed VaR from 1.36% to 2.13%. |
| Correlations and volatility attribution | `risk_metrics.py`, `portfolio_analysis.py` | **Implemented:** sample covariance proxy and component reconciliation tests pass; dashboard heatmap and risk chart displayed. |
| Market, banking, IT, sector and stock shocks | `scenarios.py`, `attribution.py`, `scenario_analysis.py` | **Implemented:** positive/negative, zero, mixed, invalid input and P&L reconciliation tests pass. Browser broad-shock changes updated INR impact and briefing. |
| Drift and concentration alerts | `attribution.py`, `app.py` | **Implemented:** target sum validation, HHI/single-name/drift tests. Browser 105% targets showed an error; restored 100% targets updated alerts. |
| Analyst dashboard and briefing | `app.py`, `briefing.py` | **Implemented:** launched in browser; performance, holdings, risk, stress and data-quality tabs inspected. Briefing refers to computed metrics. No free date-range or quantity editor. |
| Source-reference comparison | `source_verification.py`, `scripts/compare_nse_index.py` | **Partially verified:** ten official displayed NIFTY 50 closes compared; broader export and stock-price checks remain. |
| Documentation and reproducible setup | `README.md`, `requirements.txt`, `requirements-dev.txt`, `docs/` | **Implemented:** clean Python 3.12 environment installed, dependency check and test suite passed; a sourced data file is still required to populate a new real-market dashboard. |

## Executed verification

| Check | Result |
|---|---|
| Existing virtual environment, `python -m pytest -q -p no:cacheprovider` | **91 passed, 0 failed, 0 skipped; 57 subtests passed.** |
| Fresh Python 3.12 virtual environment, same pytest command | **91 passed, 0 failed, 0 skipped; 57 subtests passed.** |
| `python -m unittest discover -s tests -v` | **91 tests passed** before final changes; final verification used pytest. |
| `python -m compileall -q portfoliopulse scripts app.py` | Passed before final changes; rerun after edits in final checks. |
| Fresh environment `python -m pip check` | No broken requirements. |
| Fresh SQLite initialization | 16 tables created; `PRAGMA integrity_check` returned `ok`. |
| Existing SQLite checks | `integrity_check=ok`, zero foreign-key violations, zero duplicate candle keys, zero invalid OHLC rows. |
| Existing dataset | ID 1: 4,133 OHLCV rows, six instruments, 689 union dates, 688 complete shared price dates, 686 valid paired daily returns; window 2024-01-02 to 2026-10-16 exclusive. |
| Cached Angel One master | SHA-256 metadata validated; 153,128 entries; all six configured NSE instruments resolved exactly from the locally cached 16 October snapshot. No token values were printed. |
| Existing persistence | Seven analysis records and 236 scalar result rows observed after browser tests; latest analysis had 40 metrics. |
| Browser | Dashboard launched on localhost; five tabs inspected, risk-free and VaR controls, target-weight validation, and broad-stress input exercised. |
| Clean-environment browser startup | The freshly installed virtual environment launched Streamlit on a temporary port; the saved dataset, five tabs and KPI values rendered. Test servers were stopped after verification. |
| Live Angel One history | **Not executed successfully in this audit.** Previous HTTP 403 denial remains the known provider gate. Mock tests do not establish live access. |
| Full official NSE export and individual stock source comparisons | **Not executed.** Only the ten-date NIFTY 50 spot check below was performed. |
| Empty-dataset dashboard branch | A copied app with an empty temporary database launched on loopback and displayed the ingestion guidance without showing fictional returns. |
| Dedicated linter | **Not run.** Python compilation, imports, pytest and browser checks were executed. |

No automated tests were skipped. The unexecuted live/external checks above are separate verification activities, not undiscovered pytest cases.

## NSE NIFTY 50 spot check

On 16 October 2026, the [NSE Indices Historical Data page](https://www.niftyindices.com/reports/historical-data) was used in the browser: Equity → Broad Market Indices → NIFTY 50, 1–15 October 2026. The ten **close** values displayed in its table were transcribed into [`docs/reference/nifty50_nse_indices_2026-10-01_2026-10-15.csv`](reference/nifty50_nse_indices_2026-10-01_2026-10-15.csv). This file is a transcription of the visible table, **not an original NSE CSV export**. Compare it with the saved provider candles using:

```powershell
.\.venv\Scripts\python.exe -m scripts.compare_nse_index --dataset-id 1 --nse-csv docs\reference\nifty50_nse_indices_2026-10-01_2026-10-15.csv --reference-source-url https://www.niftyindices.com/reports/historical-data --output-dir .tmp\nse-audit
```

Observed result: 10 overlapping dates, 1 exact binary-floating-point match, **10 within 0.01 index points**, 0 mismatches, 0 provider-missing reference dates, maximum absolute difference **0.00078125 index points**. The provider has 678 additional dates outside this selected reference subset; those are **not validated** by this command. The dashboard's 2026-01-15 NIFTY gap was not resolved by the October spot check.

## Financial correctness

| Calculation | Convention and validation | Result |
|---|---|---|
| Daily buy-and-hold return | `V_t = Σ q_i P_it`; `r_t=V_t/V_(t-1)-1`; lagged weights times stock returns reconcile to this value. Hand example 2 shares A + 1 share B passes. | Pass |
| Cumulative and annualized price return | First-to-last *observed complete* value ratio minus one; annual rate `(V_end/V_start)^(252 / observed union intervals)-1`. Gaps are disclosed rather than bridged by an invented daily return. | Pass, with sample/calendar limitation |
| Volatility | Sample standard deviation of valid paired daily returns, `ddof=1`, times `sqrt(252)`. | Pass |
| Sharpe | `(mean daily return - ((1+annual INR RF)^(1/252)-1)) / sample daily std × sqrt(252)`; undefined at zero volatility. | Pass |
| Drawdown | Actual observed wealth level divided by running observed peak minus one, including starting wealth 1. Portfolio standalone and matched comparison labels are distinguished. | Pass |
| Historical VaR / ES | At confidence `c`, sort N returns, `k=max(1,ceil((1-c)N))`; VaR is nonnegative kth worst loss and ES is nonnegative average worst-k loss. | Pass; not a forecast |
| Correlation | Pearson on complete aligned daily price-return rows; constant series can be undefined. | Pass |
| Marginal/component volatility | Annual sample covariance `Σ`; proxy volatility `sqrt(wᵀΣw)`; marginal `(Σw)_i/σ`, component `w_i×marginal`. Components reconcile to proxy σ. | Pass; current-weight proxy differs from realized drifting-weight volatility |
| Scenario attribution | Per holding INR impact `q_i P_i × shock_i`; total is sum; portfolio shock return is `Σ w_i shock_i`. | Pass for broad, sector and individual cases |

All quoted performance is **price return**, not total return. The stored dataset is `unadjusted_ohlcv`; dividends and corporate actions are not independently adjusted. NIFTY 50 is the price index, not TRI. A 6% annual INR risk-free rate is an illustrative user setting, not an RBI feed. Missing quotes exclude affected paired daily returns; the observed union calendar is not independently certified as the complete exchange calendar. The available portfolio is five NSE equity holdings; the fictional multi-asset demo is isolated.

## Defects fixed during this audit

1. `source_verification.compare_daily_closes`: `relative_difference_pct` held a decimal ratio. Multiplied by 100 and added a numerical assertion. The column now reports percentage units correctly.
2. `source_verification.parse_nse_index_csv`: a CSV with an `Index Name` column could silently contain NIFTY 50 TRI or another index. It now rejects non-`NIFTY 50` rows. This does not certify a CSV that lacks index identity; source review remains necessary.
3. `scripts/compare_nse_index`: missing provider dates present in the reference now produce a nonzero command result, as price mismatches already did. Reference dates absent from a deliberately selected official subset remain informational.
4. `app.py`: invalid or empty edited target cells could raise on float conversion. Numeric/range/total validation now shows a clear error and uses saved targets until the edit is valid. Browser verification covered invalid and corrected totals.
5. `app.py`: deprecated Streamlit `use_container_width` calls were replaced with supported `width="stretch"` for the pinned Streamlit version.
6. `requirements-dev.txt` and README: pytest is now an explicit test dependency and the test command is reproducible. README/CV wording was updated to reflect the actual ten-date source check.
7. Local run command: the documented Streamlit startup now binds to `127.0.0.1` for local review. The unqualified test launch advertised network addresses, so the explicit loopback address is a safer default.

## Remaining risks

**Critical blockers for a live-broker or fully exchange-verified claim:** Angel One historical access is still denied/unverified; the full provider history and individual stock closes have no independent source comparison. These do not block an honestly labelled offline prototype demonstration.

**Important limitations:** unadjusted OHLCV and omitted dividends/corporate actions; one missing NIFTY date and affected daily returns; Yahoo fallback provenance remains user-declared; ten manually transcribed reference rows are a spot check; risk-free rate is a manual assumption; the current-weight covariance contribution is a snapshot proxy; targets cannot change historical buy-and-hold quantities; no dashboard arbitrary date-range selector. The cached master metadata records the older `margincalculator.angelbroking.com` host while current [SmartAPI documentation](https://smartapi.angelone.in/docs/Instruments) shows an `angelone.in` host; confirm the supported endpoint before relying on future refreshes.

**Minor improvements:** capture official NSE CSV exports and checksums for a wider period; add screenshots; run a dedicated linter; reduce legacy demo database helpers and the long dashboard function only when scope permits. **Outside scope:** trading, broker holdings synchronization, automated forecasts, LLM advice, production deployment, and live commodity/debt analytics.

Secret review: `.env` is ignored and not tracked in the current Git index, no `.env` commit path was found in available history, and a targeted tracked-file secret-pattern scan found no match. This is a limited local scan, not a guarantee that credentials never appeared elsewhere. No credential values were read or printed during this audit.

## Fresh Windows PowerShell setup and run

From the repository root with Python 3.12 installed:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env
```

The `.env` step is needed only for live Angel One attempts; **offline tests and sourced CSV ingestion do not need credentials**. Enter credentials locally if authorized; never paste them into a report. The dashboard creates an empty SQLite schema when launched, and the following command explicitly initializes it:

```powershell
.\.venv\Scripts\python.exe -c "from portfoliopulse.database import initialize; initialize()"
```

For a real dashboard, place a lawfully obtained, source-labelled OHLCV CSV and provenance sidecar under `data\incoming`; follow the exact fields and checksum rules in [`docs/PHASE_3_DATA_SQL.md`](PHASE_3_DATA_SQL.md). Then run:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Existing local dataset 1 can be used on this workstation without reimporting it. A new checkout does not include ignored provider candles or the local database. Without a real dataset, the app starts and explains that ingestion is needed; it will not present the fictional demo as real data.

## Presentation handoff

Architecture: source-specific Angel One/CSV adapters → strict data quality and provenance checks → transactional SQLite candles and immutable holding snapshots → pure buy-and-hold/risk/scenario functions → Streamlit/Plotly presentation and deterministic briefing. Separate modules make finance calculations testable without the UI.

Recommended screenshots: (1) five KPI cards and matched NIFTY growth, (2) source-quality panel showing the unadjusted and unverified labels, (3) target-weight validation and drift alert, (4) stress impact with holding attribution. Do not show `.env`, account IDs or broker sessions.

The [README](../README.md) now distinguishes implemented code, mocked API behavior, and the observed NSE spot check. Three CV bullets and ten interview questions with answers are maintained in [`docs/PORTFOLIOPULSE_CV_INTERVIEW.md`](PORTFOLIOPULSE_CV_INTERVIEW.md); its final question and project walkthrough were updated to match the audit evidence.
