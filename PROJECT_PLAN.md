# PortfolioPulse — India-first Portfolio Risk Monitor

## Status and working method

**Phase 0 complete:** existing scaffold reviewed, organized and import-checked on 16 October 2026. **Phases 1–8 are not signed off.** The presence of a module or screen in the starter code does not count as financial or runtime verification. We will finish each phase, check its gate, fix findings, and then move on.

**Confirmed portfolio model:** INR-denominated Indian assets, buy-and-hold holdings. Quantities stay fixed over the analysis window, so market weights drift; scenario impacts use the latest observed weights. Target weights are a comparison for drift alerts. The benchmark remains outside the portfolio.

## Project description

PortfolioPulse is an explainable portfolio risk dashboard for an Indian retail investor, built with the discipline of an investment analyst workflow. A user records holdings and target allocations; the application imports historical prices or NAVs, checks the data, calculates performance and risk in Python, stores both source data and reproducible results in SQL, and explains the main exposures and hypothetical losses in plain language.

It answers three questions: **How has the portfolio behaved? Where is its risk concentrated? What could happen under a specified market shock?** It is a monitoring and learning tool, not an investment adviser, trade recommender, or broker connection.

## Intended user and design choice

The primary user is an Indian retail investor who wants one INR-denominated view of Indian equities, mutual funds or ETFs, debt exposure, and gold. The interface should be approachable; the calculation and data layers should be rigorous enough to discuss in an investment analytics interview. This makes the project locally relevant while demonstrating Python, SQL, financial reasoning, data quality, reproducibility, and communication for a Russell Investments application.

Use Nifty benchmarks and Indian instrument examples. Do not imply that a Nifty index is a complete representation of a diversified portfolio or that the demo is production investment advice.

## What the MVP will implement

1. **Portfolio setup:** edit quantities and target weights for the seeded India-themed ETF proxy portfolio. The model is INR-denominated. Adding arbitrary instruments and fund schemes is a follow-on; the current CSV import accepts the seeded symbols.
2. **Historical data workflow:** import a CSV with `symbol,date,close_value`; generate deterministic synthetic sample series so the demo works offline. Show source and observation coverage, and align returns on common dates. Keep the repository interface replaceable for later licensed feeds.
3. **Data quality controls:** parse dates and numeric fields; reject duplicate instrument/date rows and invalid prices; show coverage and align on common dates; do not silently forward-fill or mix incompatible calendars.
4. **Performance and risk engine:** calculate daily portfolio returns, cumulative return, annualized return and volatility, Sharpe ratio with a disclosed configurable risk-free assumption, maximum drawdown, 95% or 99% historical VaR and expected shortfall, 63-observation rolling volatility, benchmark beta, and correlation. Show the selected window and method in the interface.
5. **Exposure and alerts:** show actual versus target instrument allocation, drift, concentration (including HHI), and correlation. Users can adjust drift and concentration alert thresholds; alerts are explainable and are not personalized recommendations.
6. **Scenario analysis:** let users apply transparent shocks by asset class (for example, equity −10%, debt −3%, gold +5%). Report estimated INR and percentage portfolio impact, with contribution by holding. Label results as illustrations, not forecasts.
7. **Analyst briefing:** generate a short deterministic Python summary grounded in the calculated values: performance context, top exposures, scenario result, and data caveats. No LLM key or API is required for the MVP.
8. **Dashboard and export:** use Streamlit and Plotly for a portfolio overview, performance/drawdown, allocation, correlation, risk metrics, scenario controls, and a downloadable CSV/briefing.

## Python and SQL architecture

Keep the Streamlit UI thin and keep finance logic out of page callbacks:

```text
app.py                         # Streamlit entry point; UI concerns only
portfoliopulse/
  config.py                    # paths and shared assumptions
  data.py                      # current synthetic data; live/CSV pipeline in P2
  database.py                  # SQLite repository; strengthened in P3
  schema.sql                   # normalized database tables
  analytics.py                 # portfolio and risk calculations; validated in P4
  scenarios.py                 # shock analysis; extended in P5
  briefing.py                  # deterministic explanation; reviewed in P6
scripts/seed_demo.py           # local demo database initialization
data/                          # local database and future CSV cache
tests/                         # meaningful calculation and SQL checks in P2–P7
docs/                          # phase reviews and methodology notes
```

Use **SQLite for the two-day local MVP**, with foreign keys enabled, explicit transactions, parameterized queries, uniqueness constraints, and idempotent `INSERT ... ON CONFLICT` imports. This shows real SQL use while keeping setup simple. Keep repository methods portable so PostgreSQL can replace SQLite if the project grows.

Core relational tables:

- `instruments(instrument_id, symbol, name, asset_class, currency, data_source)`
- `portfolios(portfolio_id, name, base_currency, created_at)`
- `holdings(portfolio_id, instrument_id, quantity, target_weight)`
- `market_observations(instrument_id, observation_date, close_value, source, imported_at)` with a unique instrument/date key
- `risk_runs(run_id, portfolio_id, window_start, window_end, method, confidence, annual_risk_free_rate, created_at)`
- `risk_metrics(run_id, metric_name, value, unit)`
- `scenarios(scenario_id, portfolio_id, name, as_of_date, impact_inr, impact_pct, shocks_json, contributions_json, created_at)`

SQL is used for validated observation upserts, holdings/instrument joins, coverage retrieval, and persistent risk/scenario snapshots. Add indexes for instrument/date and portfolio/date access as the project grows. Store calculation method, confidence level, window, and source metadata so a result can be reproduced and audited.

Python should own the transparent financial calculations. Align return series on common dates before portfolio aggregation; state whether weights are fixed or rebalanced; use adjusted prices/total-return series when available; and avoid look-ahead by using only observations through the selected as-of date. Use vectorized pandas/NumPy calculations, small pure functions, explicit annualization conventions, and typed result objects. Never present a single VaR number without its confidence level, horizon, historical window, and limitations.

## India-specific data and product decisions

- Start with **Nifty 50 TRI or another suitable Nifty total-return series** as a benchmark, plus sample Indian equity/ETF prices and mutual-fund NAV observations. Make benchmark selection explicit.
- Prefer user-supplied or official downloadable historical data for the demonstrator. Nifty Indices offers historical and total-return index data; AMFI offers NAV and NAV-history access. NSE lists EOD and historical market-data products and has a data-use policy, so confirm terms before automated, redistributed, or commercial use.
- Keep provider code replaceable. Yahoo Finance may be useful for a quick research prototype, but it is not the app's authoritative source and its availability/coverage must not be assumed.
- Handle Indian market holidays, different NAV/price calendars, symbol changes, corporate actions, stale observations, and INR display. Never compare raw price levels across securities.
- Keep user holdings local in the MVP; do not collect credentials or connect to a broker.

## Phase gates for the two-day build

| Phase | Work | Gate before proceeding |
| --- | --- | --- |
| **0 — Review and structure** | Audit the inherited scaffold, align the scope to Indian assets, organize files and record risks. | File layout and imports are coherent; open issues and decisions are recorded. |
| **1 — Environment and configuration** | Create a reproducible Python environment, finalize dependencies and Windows setup instructions. | Fresh setup imports the package and starts the application without missing modules. |
| **2 — Indian market data** | Select real Indian ETFs/benchmark, confirm usable historical sources and terms, ingest adjusted/total-return series where available, and retain a labeled offline CSV fallback. | Dates, source, adjustments, duplicates, missingness and overlap are checked; no fabricated points enter the real-data path. |
| **3 — SQL and portfolio setup** | Review the schema, portfolio identity, holdings/targets, idempotent imports and saved analysis results. | Transactions, constraints, date-filtered retrieval and invalid-weight handling work on a fresh database. |
| **4 — Portfolio analytics** | Validate buy-and-hold valuation, returns, annualization, Sharpe, drawdown, historical VaR/ES, correlations and volatility contribution. | Small hand-calculated examples match Python output; methods and edge cases are documented. |
| **5 — Scenarios and alerts** | Add shocks per asset, contribution reconciliation, allocation drift and concentration thresholds. | Scenario components sum to total impact and invalid inputs are rejected. |
| **6 — Dashboard and briefing** | Connect the tested modules to Streamlit/Plotly; add date range, holdings, benchmark, risks, scenarios, data freshness and a grounded briefing. | Displayed figures agree with engine output and controls show clear feedback. |
| **7 — Financial validation** | Review the full path from raw data to charts, SQL persistence, look-ahead and edge cases. | Essential automated checks and a manual dashboard walkthrough pass. |
| **8 — Interview handoff** | Finalize README, methodology, screenshots, source notes, truthful CV bullets and interview questions. | A fresh reader can run and explain the project and its limits. |

We will treat existing starter code as material to review during its phase, not as proof that the phase is complete. If time is tight, correctness of P2–P7 takes priority over additional charts or an LLM integration.

## Definition of done

- A fresh local setup can launch the app and load the included sample portfolio without API keys.
- A user can import data, see validation feedback, create or edit a portfolio, and retrieve persisted inputs/results after restart.
- Metrics are traceable to a date window, method, confidence/horizon, and source; scenario totals reconcile to the displayed contributions.
- Missing or unsuitable data is surfaced clearly rather than converted into a confident-looking metric.
- The briefing only describes results already computed by the Python engine; it does not invent forecasts or prescribe trades.
- README and interface state the educational purpose, data limitations, and no-advice boundary.

## Deliberately out of scope for the MVP

Live brokerage integrations, streaming market data, automated trading, personalized buy/sell recommendations, portfolio optimization, complex ML forecasts, paid LLM integration, multi-user authentication, and a production cloud deployment. These add data rights, security, model-risk, and delivery work without improving the two-day interview demonstration.

## Follow-on roadmap

1. Add a PostgreSQL deployment and scheduled, source-licensed data refresh with monitoring.
2. Add sector/factor exposure and duration/credit-risk measures where holdings data supports them.
3. Add historical crisis-window replay, rolling VaR/expected-shortfall backtesting, and documented model validation.
4. Optionally add an LLM explanation layer that receives only a validated metrics packet, cites those values, and remains subject to human review. Keep deterministic mode as the fallback.

## Positioning after implementation

**PortfolioPulse — India-first Portfolio Risk Monitor** | Python, SQL, pandas, NumPy, Streamlit, Plotly

Describe the delivered workflow and verified metrics only. Do not claim live data, AI integration, or performance improvements unless those are implemented and demonstrated.

## Research references

- [Russell Investments internship program](https://russellinvestments.com/content/ri/uk/en-gb/about-us/careers/internships.html) describes interns contributing to real projects and developing analytical/presentation skills.
- [Russell Investments regulatory brochure](https://russellinvestments.com/content/dam/ri/files/us/en/legal/adv/RIIS-ADV-PART-2A-BROCHURE.pdf) describes AI-assisted research and portfolio-risk use cases including exposure analysis and stress testing, with human validation and no autonomous trading decisions.
- [Nifty Indices historical data](https://www.niftyindices.com/reports/historical-data) includes historical index values and total-return index values.
- [AMFI NAV and NAV history](https://www.amfiindia.com/net-asset-value) provides Indian mutual-fund NAV access.
- [NSE data usage policy](https://www.nseindia.com/static/market-data/nse-data-policy) governs use and sharing of NSE market data; confirm applicable terms before automating or redistributing it.
- [SEBI Investor Riskometer explainer](https://investor.sebi.gov.in/hindi/riskometer.html) is a useful India-specific example of presenting risk in a way retail investors can understand.
- Public GitHub examples such as [market-risk-dashboard](https://github.com/TR-3N/market-risk-dashboard) and [skfolio portfolio analytics](https://github.com/JDS-265/skfolio-portfolio-analytics) illustrate modular risk dashboards and scenario workflows. They are design references, not dependencies or copied source.
