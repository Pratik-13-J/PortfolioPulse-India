# Revised plan audit — 16 October 2026

This document preserves phase-by-phase findings. The later [final audit](FINAL_AUDIT.md) supersedes its earlier browser and independent-source verification status.

## Scope change

The newly supplied **PortfolioPulse India: Revised 2-Day Project Plan** supersedes the former yfinance-first Phase 2 gate. Its initial deliverable is an Indian **equity** portfolio compared with NIFTY 50, with Angel One SmartAPI daily historical candles as the primary feed and selected NSE reports as an independent reference. The plan names RELIANCE, HDFCBANK, INFY, ICICIBANK and ITC as suggested holdings; their current exchange tokens must be checked against the Angel One instrument master. The attachment's “multi-asset” heading is broader than its explicit initial scope. Gold, government debt and mutual-fund extensions should be described as later work until their data and return conventions are supported.

The supplied plan starts at P1, while this repository already has a P0 review. We retain P0 as the review/structure gate and use P1–P8 for the new execution steps. The former demo and yfinance checks remain useful, but their old P2 sign-off does not transfer to the new Angel One acceptance criteria.

## Phase 0 — review and structure

**Result: passed.** The inherited prototype was separated into data, database, analytics, scenarios and briefing modules within `portfoliopulse/`; imports and schema paths were checked and financial gaps were recorded in [PHASE_0_REVIEW.md](PHASE_0_REVIEW.md). The proposed `src/` tree in the attachment is an example of module boundaries, not a functional requirement to rename a working package. P0 does not certify market data or finance calculations.

## Phase 1 — environment and configuration

**Result: verified after revised setup repairs.** The project uses a local Python 3.12.10 environment, pinned direct dependencies, a Windows PowerShell setup path, an ignored local `.env` with a tracked `.env.example`, instrument configuration, and a starter Streamlit app. Two setup/configuration checks passed; `pip check` found no broken installed requirements; core package imports succeeded; the local Streamlit health endpoint returned HTTP 200; and a bare `app.py` run exited successfully with only expected Streamlit context/deprecation warnings. The current configuration names NIFTY 50 and five NSE equities without storing tokens or credentials. The original setup record is [PHASE_1_SETUP.md](PHASE_1_SETUP.md). The app can run on the fictional demo without broker authentication. Phase 1 confirms startup and imports, not correctness of portfolio analytics or a live Angel One session.

## Phase 2 — Angel One daily-candle ingestion

**Result: sourced-CSV contingency accepted; Angel live-candle check pending.** [PHASE_2_DATA.md](PHASE_2_DATA.md) established only the reproducible fictional CSV path. [PHASE_2_MARKET.md](PHASE_2_MARKET.md) established a separate yfinance adjusted-close pipeline and exact-match offline cache. Those historical records do not verify Angel One candles or NSE reference prices. The revised plan permits a clearly reported live blocker with a real sourced-CSV fallback; that fallback is now verified below.

A fresh public Angel One instrument-master fetch on 16 October 2026 returned 153,128 entries with a valid stored checksum. NIFTY 50 and all five configured stocks resolved to six distinct current NSE tokens. The user configured the four `ANGELONE_*` values in the ignored local `.env`; only presence, never values, was checked. A status-only live check showed Angel One login HTTP 200 with `status: true`. Thus authentication is verified, but no claim of live Angel One prices is made.

**Implemented and checked locally:** The new adapter resolves current instrument tokens, requests bounded daily candles, normalizes timestamps to Indian time, validates OHLCV and duplicate dates, retries transient failures, and records source, retrieval time and unadjusted price basis. It preserves candle-only raw batches locally after a successful refresh. A checksum-checked Angel CSV can be replayed offline using its recorded token mapping; a stale instrument master is never used to request fresh candles. An independently sourced CSV importer accepts real NSE OHLCV with explicit source provenance and saves aligned closes, returns and coverage under `data/processed/`. It never relabels that source as Angel One. API-independent tests cover these paths, including missing dates, changed token mappings, cache tampering and credential-safe errors.

Live short-window requests for both NIFTY 50 and RELIANCE returned HTTP 403 plain-text access-rate denials. An end-to-end refresh for 1–15 October 2026, with ten seconds between distinct requests and 15/30-second rate-limit backoff, ended with the same denial. The downloader reported that no exact Angel CSV fallback was available. No `angel_candles.csv` or raw candle batches were saved, so OHLCV coverage, actual offline replay and NSE close agreement cannot yet be checked. The installed SmartAPI SDK attempted an unnecessary third-party public-IP lookup during import; the project adapter now suppresses that lookup before authenticated requests.

**Real-data contingency:** Yahoo Finance daily Open, High, Low, Close and Volume were retrieved through yfinance 1.7.0 with `auto_adjust=False` for NIFTY 50 and the five configured equities, 2 January 2024 through 15 October 2026. The ignored input and sidecar are `data/incoming/yahoo_nse_2024-01-02_2026-10-16.csv` and its same-stem `.meta.json`; the importer produced source-labelled outputs under `data/processed/`. Input and output SHA-256 hashes, OHLCV relationships, dates, duplicate keys, coverage and close-to-close percentage returns were checked. The result has 4,133 candles, 688 shared price dates and 686 valid return dates. The provider lacks a NIFTY 50 row on 15 January 2026, leaving one incomplete union date and excluding returns at the gap edges. The import is labelled `user_declared_unverified` for source provenance and `unadjusted_ohlcv` for price basis. It is not Angel One data, adjusted total return data or an independent NSE cross-check.

The final local check ran `python -m unittest discover -s tests -v`: **44 tests passed**. `pip check` found no broken installed requirements, and `git diff --check` found no whitespace errors. The additional test covers access-rate retries and sanitized errors. These checks do not substitute for returned live candles or a comparison with NSE reference prices.

The new P2 gate requires:

1. Credentials loaded from local environment or ignored `.env`, with no secret values in logs or committed files.
2. Current instrument-master validation of symbol, token and exchange for NIFTY 50 and configured NSE equities. Example tokens are not accepted without a current check.
3. Daily OHLCV request normalization to `Asia/Kolkata`, with symbol, token, exchange, source, retrieval time and price-adjustment status recorded. Index zero volume remains a valid possibility.
4. Bounded requests consistent with the [official Historical API](https://smartapi.angelbroking.com/docs/Historical)'s 2,000-day maximum for `ONE_DAY` candles, plus explicit authentication, network, API and rate-limit error handling and retries.
5. Preservation of raw candle responses where practical, a validated independent CSV import and a matching prior Angel CSV fallback when an authenticated download fails. Missing prices are never fabricated.
6. An API-independent mock-response test, a successful authenticated retrieval or clearly documented external blocker, and inspection of saved output. Selected Angel One-versus-NSE close checks remain the P7 verification step; agreement cannot be claimed in advance.

Angel One unadjusted candle closes must not be silently mixed with yfinance auto-adjusted values or called total returns. Store adjustment basis with data and disclose the distinction in later calculations and charts. API and exchange data-use terms require review before public redistribution.

## Phase 3 — data quality and SQLite

**Result: verified with the local sourced-CSV contingency.** The source-independent validator checks positive OHLC prices, non-negative volume, duplicate symbol/date keys, OHLC consistency, UTC retrieval stamps, source and declared adjustment basis. It aligns on observed dates only and reports missing observations and the return dates excluded around a gap. The SQLite layer stores the configured INR portfolio, NIFTY 50 benchmark, immutable holding snapshot, provider-specific OHLCV dataset, per-candle retrieval time, validation settings and future scalar result slots. Repeat imports are idempotent and an invalid SQL batch rolls back. The existing fictional dashboard tables remain intact. Full details and limitations are in [PHASE_3_DATA_SQL.md](PHASE_3_DATA_SQL.md).

## Phase 4 — portfolio analytics

**Result: verified on the saved Indian equity dataset and small hand-calculated fixtures.** The real analysis reads the immutable fixed quantities attached to the chosen SQLite dataset, calculates drifting market weights and buy-and-hold portfolio value, and compares with NIFTY 50 on valid matching daily returns. It calculates endpoint annualized price performance, sample volatility, Sharpe with an explicitly chosen annual INR risk-free assumption, observed maximum drawdown, nearest-rank historical one-day VaR/ES, correlation and latest-weight covariance risk contributions. A regression check preserves a portfolio drawdown occurring on a benchmark-missing date. Results, assumptions and finite scalar values are saved with the dataset lineage. The existing Streamlit dashboard is still on the separate fictional portfolio. See [PHASE_4_ANALYTICS.md](PHASE_4_ANALYTICS.md) for formulas, sample numbers and limitations.

## Phase 5 and Phase 6

**Phase 5: implemented and verified.** Configurable broad-market, banking, IT, individual-stock and sector shocks produce holding-level INR contributions with percentage reconciliation. Target drift and single-name/HHI concentration alerts use the saved dataset and Phase 4 valuation date. Scenario runs and contributions are stored with dataset/analysis lineage. The recheck passed 83 repository tests; the saved local scenario reconciles across five holdings and SQLite integrity/foreign-key checks passed. See [PHASE_5_STRESS_TESTS.md](PHASE_5_STRESS_TESTS.md).

**Phase 6: implemented.** The Streamlit app now presents the real saved dataset, matching-date NIFTY comparison, KPI cards, editable session-only target weights, allocation and risk-contribution charts, correlation and drawdown charts, interactive stress previews, source/quality details and computed rule-based briefing. It handles a database with no imported real dataset without showing fictional prices as market performance. The code compiles and chart inputs were exercised; browser-level interaction remains to be confirmed locally because this environment blocked the HTTP health probe and its AppTest runner did not complete. See [PHASE_6_DASHBOARD.md](PHASE_6_DASHBOARD.md).

## Remaining gates and decisions

## Phase 7 and Phase 8 — verification and handoff

**Phase 7 automated checks: complete.** The audit added hand-calculated portfolio and risk cases, scenario reconciliation, invalid inputs, missing/duplicate observations, mismatched windows, source-close comparison tests, and a local NSE CSV parser. The full suite passes 91 tests; Python compilation, `pip check`, SQLite `integrity_check` and `foreign_key_check` pass. The source-comparison utility and checklist are in [PHASE_7_VERIFICATION.md](PHASE_7_VERIFICATION.md).

**Independent NSE comparison: limited spot check completed in the final audit.** Ten official displayed NIFTY 50 price-index closes matched the saved provider series within 0.01 index points. The reference is a transcription, not an original NSE export; the rest of the sample and the stock prices remain unverified. See [FINAL_AUDIT.md](FINAL_AUDIT.md).

**Phase 8: prepared.** The project README documents the use case, implemented workflow, calculations, schema, setup, limitations, source links and screenshot placeholders. Evidence-based CV bullets and ten sample interview questions and answers are in [PORTFOLIOPULSE_CV_INTERVIEW.md](PORTFOLIOPULSE_CV_INTERVIEW.md). Screenshots still need to be captured and added before a public repository handoff.

The user reconfirmed **buy and hold** for Phase 4: fixed quantities with drifting market weights. This resolves the new prompt's “fixed weights” wording; the implemented formula and tests follow the confirmed buy-and-hold convention.
