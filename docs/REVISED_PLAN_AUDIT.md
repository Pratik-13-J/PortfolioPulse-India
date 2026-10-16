# Revised plan audit — 16 October 2026

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

## Later gates and decisions

P3 reviews OHLCV validation, SQL constraints, portfolio configurations and stored result lineage. P4 validates the portfolio accounting and risk formulas on hand-calculated examples. P5–P8 cover Indian stress scenarios, the dashboard, end-to-end financial validation and interview handoff. Existing screens and functions are starter material until their gates pass.

The user previously chose **buy and hold**: fixed quantities with drifting market weights. The new attachment's P4 prompt says “fixed weights,” which may imply periodic rebalancing. The project should retain the confirmed buy-and-hold model unless the user explicitly changes that accounting decision; the final methodology must state the chosen convention and tests must match it.
