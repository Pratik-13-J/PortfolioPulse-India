# PortfolioPulse India

**Portfolio analytics and risk management for an Indian equity portfolio, built with Python, pandas, SciPy, SQLite and Streamlit.**

![tests](https://github.com/Pratik-13-J/PortfolioPulse-India/actions/workflows/tests.yml/badge.svg)

PortfolioPulse analyses a buy-and-hold portfolio of five NSE stocks against the **NIFTY 50 price index**. It covers four areas: performance and benchmark analytics, VaR/CVaR with statistical backtests, stress testing, and constrained portfolio construction. All of it is shown in a read-only dashboard, and every number carries its data source and verification status.

> Educational project (v1.1). Not investment advice, a trading system or a forecasting tool.

## 1. Objective and business problem

A portfolio analyst has to answer four questions:
1. How did the portfolio do against its benchmark, and why?
2. How much could it lose on a bad day, and is that risk model reliable?
3. What happens in a market, sector, rate or currency shock?
4. Would a different allocation have been better, after constraints and trading costs?

PortfolioPulse answers each with transparent, tested methods. It also refuses to fabricate an answer when the data does not support one: missing benchmark weights, an undefined maximum-Sharpe portfolio, or unavailable historical events.

Example universe: RELIANCE, HDFCBANK, INFY, ICICIBANK, ITC (four sectors), benchmark NIFTY 50 ([config/instruments.json](config/instruments.json)). Quantities are illustrative.

## 2. Key capabilities

| Area | Capabilities |
|---|---|
| Data | Angel One SmartAPI and source-labelled CSV ingestion; OHLCV validation (no forward-fill); immutable dataset snapshots; versioned migrations; provenance ledger; NSE corporate-action tooling |
| Performance | Buy-and-hold returns, CAGR, volatility, Sharpe, Sortino, drawdown with duration and recovery, Carino-linked security contributions |
| Benchmark | Active return, tracking error, information ratio, beta, downside beta, up/down capture, rolling TE and beta |
| Risk | Historical, normal, Cornish-Fisher and Monte Carlo VaR/CVaR; Kupiec and Christoffersen backtests; sample, Ledoit-Wolf and EWMA covariance; component VaR; risk contributions |
| Stress | Beta-, covariance- and factor-propagated shocks; sector and stock shocks; historical event replay; P&L by holding and sector |
| Optimization | Min-variance, max-Sharpe, risk parity (ERC), efficient frontier; bounds, sector and turnover constraints; Indian trading costs; integer-share allocation; walk-forward test |
| Attribution | Brinson-Fachler, Carino linking, Active Share. Gated until real constituent weights exist; a labelled synthetic demo is shown |
| App | Read-only Streamlit dashboard (11 tabs), CSV and HTML reports with a provenance header, GitHub Actions CI |

## 3. Architecture

```text
Angel One API / sourced CSV / NSE reference files
        │  validate (no fills) + provenance
        ▼
SQLite (schema v4): datasets · candles · holdings snapshots · engine runs · provenance events
        ▲ write (scripts/ only)                     │ read-only (mode=ro)
        │                                           ▼
Orchestrators ── pure calculation modules ──   app.py + ui/ + reporting.py
(risk · stress · optimization)                 (Streamlit + Plotly)
```

There are three rules:
- Calculation modules are pure: no I/O.
- Command-line scripts are the only writers, and each stored run records an `engine_version` and its settings.
- The dashboard never writes.

Details: [docs/ARCHITECTURE_GUIDE.md](docs/ARCHITECTURE_GUIDE.md) · [docs/CODE_WALKTHROUGH.md](docs/CODE_WALKTHROUGH.md).

## 4. Technology stack

- **Runtime:** Python 3.12.
- **Libraries, pinned in [requirements.txt](requirements.txt):** numpy 2.5.4, pandas 3.0.6, scipy 1.18.1 (optimization, statistics), streamlit 1.66, plotly 7.1, smartapi-python, yfinance.
- **Storage:** SQLite (standard library).
- **Testing:** pytest, Streamlit AppTest, and GitHub Actions on Windows.

## 5. Financial methodologies

| Topic | Implementation (one line) |
|---|---|
| Returns | V_t = Σ qᵢPᵢ,t; r_t = V_t/V_{t−1} − 1 on dates where every series is observed; 252 trading days |
| Sharpe / Sortino | Sample sd (ddof = 1) × √252; effective daily rf from an illustrative 6% annual rate |
| Historical VaR/ES | Nearest rank: k = ⌈(1−c)N⌉ worst days; ES = mean of the k worst |
| Parametric / Cornish-Fisher | −(μ + zσ); a skew/kurtosis-adjusted z; closed-form ES |
| Monte Carlo | Seeded MVN or bootstrap of asset returns, revalued at fixed quantities, with batch-means standard errors |
| Backtests | Rolling one-day-ahead forecasts (no look-ahead); Kupiec POF; Christoffersen independence |
| Risk contribution | Euler: RCᵢ = wᵢ(Σw)ᵢ/σ_p, summing to σ_p; component VaR sums to VaR |
| Brinson-Fachler | A = (w_p − w_b)(R_b,s − R_b), S = w_b(R_p,s − R_b,s), I = (w_p − w_b)(R_p,s − R_b,s); Carino-linked |
| Optimization | SLSQP with every result re-checked for feasibility; Ledoit-Wolf covariance; log-barrier ERC |

Every formula has a definition, worked example and limitations in [docs/FINANCIAL_CONCEPTS_GUIDE.md](docs/FINANCIAL_CONCEPTS_GUIDE.md). An independent audit is in [docs/FINANCIAL_METHODOLOGY_AUDIT.md](docs/FINANCIAL_METHODOLOGY_AUDIT.md).

## 6. Dashboard screenshots

All screenshots are real output on dataset 1 (Yahoo data, user-declared and unverified).

| | |
|---|---|
| ![Performance](docs/images/01_overview_performance.jpg) **Performance:** growth of ₹100 vs NIFTY 50 | ![Benchmark](docs/images/02_benchmark_comparison.jpg) **Benchmark comparison:** TE, IR, beta, capture |
| ![Risk](docs/images/03_risk_diversification.jpg) **Risk:** correlations and volatility contributions | ![VaR](docs/images/04_var_cvar_methods.jpg) **VaR/CVaR** by method (99%) |
| ![Tail](docs/images/05_var_tail_and_backtests.jpg) **The k = 7 worst days and backtests** | ![Stress](docs/images/06_stress_library.jpg) **Stress library** with labelled assumed sensitivities |
| ![Optimization](docs/images/07_optimization_frontier.jpg) **Optimization:** frontier and status notices | ![Gated](docs/images/08_attribution_gated.jpg) **Attribution:** gated on real data |
| ![Brinson demo](docs/images/09_brinson_illustrative_demo.jpg) **Brinson-Fachler:** *synthetic* illustrative example | ![Reports](docs/images/10_reports_export.jpg) **Reports** with provenance header |

## 7. Example results (dataset 1, as of 2026-10-15, portfolio value ₹119,199)

| Metric | Portfolio | NIFTY 50 |
|---|---|---|
| Annualized price return | −4.86% | +2.21% |
| Annualized volatility | 13.75% | 13.59% |
| Sharpe (rf 6%) | −0.74 | −0.20 |
| Beta / tracking error / information ratio | 0.90 / 6.36% / −1.17 | — |
| Maximum drawdown | −29.5% (unrecovered) | −15.8% |

### VaR and stress examples

| One-day 99% VaR | Value |
|---|---|
| Historical (k = 7 of 686 days) | 2.13% (₹2,535) |
| Normal | 2.03% |
| Cornish-Fisher (excess kurtosis 3.2) | 2.68% |
| Monte Carlo MVN (100k paths, SE 0.011%) | 2.03% |

**Backtest:** historical 95% VaR **fails** the Kupiec test, with 33 exceptions against 21.8 expected (p = 0.022).
- The exceptions cluster in the 2026-Q1 volatility regime, which an equal-weighted 250-day window adapts to too slowly.
- An age-weighted historical simulation passes on the same dates.
- It is not adopted as the default, because it was chosen after seeing the results on one sample.

The full analysis is in [docs/VAR_BACKTEST_DIAGNOSTICS.md](docs/VAR_BACKTEST_DIAGNOSTICS.md).

| Stress scenario (hypothetical) | Portfolio impact |
|---|---|
| NIFTY 50 −20%, beta-propagated | −18.4% |
| NIFTY −10% plus Banking −10% | −13.6% |
| Replay of the worst observed NIFTY drawdown (Sep 2024 – Mar 2025) | −13.5% |
| ICICIBANK −20%, alone vs with correlated moves | −5.8% vs −10.5% |
| 10Y yield +100 bp (illustrative assumed sensitivities) | −2.6% |

**Optimization:** maximum Sharpe is **undefined** at rf 6%, because no feasible portfolio's expected return exceeds 6%. Min-variance lowers ex-ante volatility only from 13.69% to 13.47%.

## 8. Brinson attribution

Brinson-Fachler splits active return R_p − R_b into three effects per sector:
- **allocation:** over- or under-weighting sectors that beat the benchmark;
- **selection:** picking better stocks within a sector;
- **interaction:** the cross term.

Carino linking makes multi-period effects add up to the compounded active return.

The formulas are verified on hand-worked examples. Real attribution needs **NIFTY 50 constituent weights**, which the project doesn't have, so the dashboard keeps it **gated** and shows only a **clearly labelled synthetic example**. Benchmark weights are never fabricated.

## 9. Database and data provenance

| Dataset | Source | Status | Used for |
|---|---|---|---|
| 1 | Yahoo Finance (CSV import), 2024-01-02 → 2026-10-15, 686 returns | `user_declared_unverified`; labelled `unadjusted_ohlcv`, but the evidence suggests split/bonus-adjusted prices (pending the NSE file) | Stored runs 9 (risk), 10 (stress), 11 (optimization) |
| 2 | Angel One SmartAPI live pull, 2026-10-29 → 2026-11-12 | `verified_live` | Smoke tests (10 returns) |

**Database rules:**
- **No market data in Git.** The SQLite database (`data/portfoliopulse.db`), its backups and all market data are git-ignored.
- **Never overwritten.** Datasets and stored runs are immutable; corrections create new dataset versions.
- **Price returns only.** All comparisons are price returns, not total returns.

External data still needed (constituent weights, NSE corporate actions, RBI/FBIL rates, USD/INR, the ITC demerger valuation): [docs/DATA_DEPENDENCIES.md](docs/DATA_DEPENDENCIES.md).

## 10. Installation (Windows)

Install Python 3.12, then:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

The database is created on first ingestion. Load data with either route; market data is not distributed with the repository.

```powershell
# Source-labelled CSV with provenance sidecar (format: docs/PHASE_3_DATA_SQL.md)
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
# or Angel One (copy .env.example to .env and fill it in locally; never commit it)
.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2026-10-29 --end 2026-11-13
```

Create the stored engine runs that the dashboard displays. Identical reruns reuse the existing run.

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_risk --dataset-id 1
.\.venv\Scripts\python.exe -m scripts.analyze_stress --dataset-id 1
.\.venv\Scripts\python.exe -m scripts.analyze_optimization --dataset-id 1
```

Configuration lives in three files, plus one optional setting:
- `config/optimization.json`: constraints, risk-free rate, cost assumptions;
- `config/stress_scenarios.json`: the scenario library and illustrative rate/FX sensitivities;
- `config/instruments.json`: the universe;
- optional `PORTFOLIOPULSE_DB`: points the app at a different database file.

## 11. Launch

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Then open http://localhost:8501. The app is read-only and opens the most recent dataset that has stored runs. A guided 5–7 minute tour is in [docs/PROJECT_DEMO.md](docs/PROJECT_DEMO.md).

## 12. Testing

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m scripts.audit_consistency --dataset-id 1
.\.venv\Scripts\python.exe -m scripts.var_diagnostics --dataset-id 1
```

- **v1.1 result:** **286 passed, 71 subtests passed**, both locally and on a clean checkout without a database. CI runs the same suite on every push and pull request ([.github/workflows/tests.yml](.github/workflows/tests.yml)).
- **What the tests cover:** hand-calculated financial fixtures, database integration on temporary databases, and Streamlit AppTest against a read-only database file.
- **The two audit scripts are read-only:** `audit_consistency` (29 checks) and `var_diagnostics`.

**Troubleshooting:**
- **"No real Indian market dataset":** run the ingestion step.
- **Empty engine tabs:** run the `analyze_*` commands.
- **"needs at least 252":** use dataset 1.
- **Port busy:** add `--server.port 8502`.
- **Angel One HTTP 403:** run `python -m scripts.diagnose_angelone`.

## 13. Known limitations and data dependencies

- **Unverified main data.** Dataset 1 is unverified Yahoo data with an unconfirmed price basis. The NSE corporate-action workflow is ready, but no official file has been supplied.
- **Price returns only.** The benchmark is the NIFTY 50 price index, not the total-return index (TRI).
- **Short, narrow sample.** About 2.7 years, one regime, five stocks: the results are in-sample and noisy, with no broad-diversification claim, and they carry survivorship bias.
- **Assumptions and gaps.**
  - Rate and FX sensitivities are illustrative assumptions.
  - The COVID-2020 and 2022 scenarios are unavailable, since the data starts in 2024.
  - Brinson attribution is gated.
  - Cost rates are assumptions to verify.
- **Limited Angel One verification.** Live access is verified for one 15-day window only.

More documentation:
- [HANDOFF_FINAL.md](docs/HANDOFF_FINAL.md): final handoff;
- [DATA_DEPENDENCIES.md](docs/DATA_DEPENDENCIES.md): external data still needed;
- [CLAUDE.md](CLAUDE.md): development guide;
- [PORTFOLIOPULSE_CV_INTERVIEW.md](docs/PORTFOLIOPULSE_CV_INTERVIEW.md): CV and interview notes.
