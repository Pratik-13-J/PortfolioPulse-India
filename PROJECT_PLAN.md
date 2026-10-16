# PortfolioPulse India — revised two-day project plan

## Purpose and current status

PortfolioPulse India is an Indian equity portfolio risk analytics and stress-testing project for an Asset Allocation Intern application. It will compare a user-defined INR equity portfolio with the NIFTY 50, explain which holdings drive risk, show allocation drift, and estimate the impact of transparent hypothetical market shocks.

This plan replaces the earlier multi-asset and yfinance-primary plan. Angel One SmartAPI daily historical candles are the primary data source. NSE reports are an independent reference for selected dates and closes. A source-labelled CSV is the operational fallback. The existing fictional demo and yfinance prototype remain separate development aids; neither is evidence that the Angel One pipeline works.

Phase 0 reviewed the original scaffold and Phase 1 setup has been verified locally. Phase 2 meets this plan's sourced-CSV contingency: a real, source-labelled Indian six-instrument dataset was imported and checked for the 2024-01-02 to 2026-10-16 window. On 16 October 2026, live Angel One login succeeded, but the historical-candle endpoint returned HTTP 403 access-rate denials, so the Angel-specific live check remains pending. Phase 3 data quality and SQLite storage have been verified with that local dataset; [the Phase 3 record](docs/PHASE_3_DATA_SQL.md) has the counts and limits. Phase 4 buy-and-hold price analytics are implemented and verified on the same saved dataset; [its methodology record](docs/PHASE_4_ANALYTICS.md) explains the formulas and limits. Phases 5–8 remain open. The phase audit is recorded in docs/REVISED_PLAN_AUDIT.md.

## Investment questions

1. How has the Indian equity portfolio performed relative to the NIFTY 50 on matching dates?
2. Which holdings and sectors contribute most to risk and concentration?
3. What is the estimated one-period impact of a broad correction, banking sell-off, IT correction, or user-specified stock shock?
4. How far have current weights drifted from target allocations?

This is historical risk monitoring and scenario analysis. It does not trade, recommend securities, forecast returns, or connect to a broker account for portfolio holdings.

## Portfolio and benchmark conventions

The first release is an Indian equity portfolio. The suggested universe is NIFTY 50 as benchmark and RELIANCE, HDFCBANK, INFY, ICICIBANK, and ITC as stock holdings. The instrument configuration identifies their intended NSE trading symbols; current Angel One tokens must be resolved against the current instrument master. No remembered token, including a NIFTY token, is trusted without that check. If a security cannot be retrieved reliably, substitute another liquid NSE listing and record the reason.

The user confirmed a **buy-and-hold model with fixed quantities**. Quantities remain fixed through the analysis window; market values and actual weights drift. Target weights are references for drift alerts, not a daily rebalancing rule. This decision overrides the attachment's fixed-weight P4 wording. Compare portfolio and benchmark only on valid shared dates.

Angel One daily OHLCV candles are **price data**, not automatically dividend- or corporate-action-adjusted total-return data. The NIFTY 50 daily close is a price-index benchmark unless an explicitly labelled total-return series is supplied. Do not call a price-return comparison a total-return comparison. A gilt or gold series may be added only after compatible return conventions and data rights are established; the initial MVP is not described as a full multi-asset platform.

## Data-source strategy

- **Primary — Angel One SmartAPI:** authenticated daily candles for the configured NSE stocks and NIFTY 50. Read credentials only from local environment variables. The official Historical API documents ONE_DAY requests of up to 2,000 days and returns timestamp, open, high, low, close, and volume. Split longer ranges into compliant windows.
- **Instrument validation:** fetch or load the current Angel One instrument master and resolve exact exchange/trading-symbol matches. Reject missing or ambiguous matches instead of using a guessed token.
- **Reference — NSE:** manually download official historical index values or market reports where practical and compare selected dates and closes. Record the source and differences; do not claim agreement until checked.
- **Fallback — local CSV:** on authentication or network failure, load a previously validated, source-labelled file for the same instruments, date range, and price basis. A fictional CSV cannot stand in for live market history. Fail clearly when no suitable cache exists.
- **Optional context:** RBI rates and government-securities data can explain the interest-rate setting. AMFI NAV history and fund analytics are later extensions. The existing yfinance research path remains optional and is not the primary gate.
- **Rights:** keep downloaded market data local until applicable API and exchange terms for redistribution are confirmed.

Store each observation's source, retrieval time, instrument symbol/token/exchange, timestamp, OHLCV fields, and adjustment status. Preserve raw responses locally where practical. Normalize candle timestamps to Asia/Kolkata, retain genuine missing observations, and never fabricate market sessions, zero prices, dividends, or returns.

## Architecture in the current repository

The existing portfoliopulse package can meet the proposed separation without a disruptive rename to src. Keep modules small and isolate the broker adapter from analytics and Streamlit:

- config/instruments.json — intended NSE benchmark and holdings; no tokens or credentials.
- portfoliopulse/angelone/ — instrument-master resolution, authenticated historical client, candle normalization, retry handling, and CSV fallback.
- portfoliopulse/sourced_csv.py — independent, source-labelled NSE OHLCV import and date alignment when broker access is unavailable.
- portfoliopulse/data.py — source-independent date alignment and price validation.
- portfoliopulse/database.py and schema.sql — parameterized SQLite access, transactions, instruments, portfolios, daily observations, provenance, and saved analytics.
- portfoliopulse/analytics.py — earlier fictional-dashboard calculations.
- portfoliopulse/portfolio_returns.py and risk_metrics.py — pure real-portfolio buy-and-hold return and risk calculations.
- portfoliopulse/portfolio_analysis.py — dataset-linked calculation and SQL result orchestration, separate from Streamlit.
- portfoliopulse/scenarios.py — hypothetical shocks and attribution.
- portfoliopulse/briefing.py — deterministic explanation based on computed numbers.
- app.py — Streamlit controls and presentation only.
- scripts/ — explicit setup, data refresh, and verification commands.
- tests/ — API-independent ingestion tests and hand-checkable finance/SQL checks.
- data/raw/ and data/processed/ — local ignored broker responses and derived files.

Local secrets belong in an ignored .env file or process environment. Commit only .env.example with variable names. Never print an API key, client code, PIN/password, TOTP secret, JWT, or full authentication response.

## Phase gates

| Gate | Time target | Work | Acceptance check |
| --- | ---: | --- | --- |
| **P0 — Review** | completed | Audit the inherited project, record accounting/data decisions and known defects. | A traceable review exists and the current repository remains importable. |
| **P1 — Setup** | 30 min | Verify Windows venv, pinned dependencies, module layout, starter Streamlit app, .env.example, ignored secrets, and config/instruments.json. | Clean setup imports; the starter app launches; config has no tokens or credentials; ignored local files stay out of Git. |
| **P2 — Angel One ingestion** | 75 min | Resolve current tokens, authenticate from environment, fetch daily OHLCV in compliant date windows, normalize IST timestamps, retry transient failures, save raw/source metadata, and use a validated CSV fallback. | Mock-response tests pass; a live authenticated run retrieves NIFTY 50 and at least three stocks and saves a labelled local dataset, or the live gate is reported pending with a sourced CSV fallback. |
| **P3 — Data quality and SQLite** | 45 min | Validate positive prices, duplicate symbol/date rows, OHLC relationships, missingness, calendar overlap and adjustment consistency; persist instruments, candles, portfolios and analysis metadata. | Parameterized SQL, transactions, constraints and repeat imports work; invalid batches roll back; excluded dates are reported. |
| **P4 — Portfolio analytics** | 90 min | Calculate buy-and-hold daily returns, cumulative/annualized return, volatility, Sharpe with a configurable INR risk-free rate, drawdown, historical one-day VaR/ES, correlations, and holding-level volatility contribution. | Hand-calculated fixtures match; 252-day convention, price-return limitation, zero volatility and insufficient-history behavior are documented. |
| **P5 — Indian stress tests** | 60 min | Add broad-market, banking, IT and holding-specific one-period shocks, loss attribution, target drift and concentration alerts. | Contributions reconcile to total INR and percentage impact; invalid shocks are rejected; scenarios are labelled hypothetical. |
| **P6 — Analyst dashboard** | 120 min | Connect tested modules to Streamlit and Plotly; show INR KPIs, NIFTY comparison, allocation, drawdown, correlation, risk contribution, stress controls, source/freshness/adjustment panel and grounded briefing. | Displayed numbers agree with engine output; missing-data and API errors have clear feedback. |
| **P7 — Financial/data verification** | 60 min | Audit formulas, calendars, adjustments, SQL, scenarios and UI; compare selected Angel NIFTY closes with NSE reference files. | Automated checks and manual walkthrough pass; source comparison records actual observed differences. |
| **P8 — Handoff** | 45 min | Finish README, source notes, screenshots, truthful CV bullets and interview explanation. | A new reader can install, run, verify and explain the delivered features and limits. |

Day 1 covers P1–P4. Day 2 covers P5–P8. If SmartAPI authentication is delayed, proceed with a clearly sourced CSV dataset for analytics and return to the live P2 gate when access is configured. Do not mark authenticated ingestion complete from a mock alone.

## Definition of done

- At least NIFTY 50 and three configured stocks have valid daily observations saved locally with source, retrieval and adjustment metadata.
- SQL stores verified inputs and reproducible analysis settings; no result silently mixes adjusted and unadjusted series.
- The buy-and-hold portfolio and NIFTY comparison use common valid dates, with excluded-date counts visible.
- Risk formulas and scenarios pass small manually checked examples, and scenario contributions sum to total impact.
- The dashboard reports assumptions, source age, limitations and validation errors without fabricated performance or commentary.
- The README and CV describe only implemented and verified features.

## Sources and further extensions

- [Angel One Historical API](https://smartapi.angelbroking.com/docs/Historical) — daily candle request and response contract.
- [Angel One Instruments documentation](https://smartapi.angelbroking.com/docs) — current instrument-master endpoint and token fields.
- [Angel One Python SDK](https://github.com/angel-one/smartapi-python) — authenticated client reference.
- [NSE market reports](https://www.nseindia.com/all-reports) — independent market-reference files.
- [RBI Database on Indian Economy](https://data.rbi.org.in/) — optional macro context.
- [AMFI NAV downloads](https://www.amfiindia.com/net-asset-value/nav-download) — later fund extension.

PostgreSQL deployment, scheduled refreshes, bond/fund total-return models, broker portfolio sync, LLM wording assistance and advanced risk backtesting can follow only after the local equity workflow is correct.
