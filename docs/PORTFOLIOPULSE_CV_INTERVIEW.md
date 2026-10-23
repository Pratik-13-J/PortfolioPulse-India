# PortfolioPulse India — CV and interview notes

## CV bullets

- Built a Python/pandas/NumPy analytics engine for an INR-denominated, fixed-quantity Indian equity portfolio, calculating price returns, annualized risk, Sharpe ratio, drawdown, historical VaR/expected shortfall and holding-level volatility contributions against NIFTY 50.
- Implemented an Angel One SmartAPI daily-candle ingestion workflow with current instrument-master resolution, IST normalization, retries, data validation and source-checked offline caching; mocked integration tests pass, while live historical candle access remains pending after provider HTTP 403 responses.
- Developed a SQLite-backed Streamlit/Plotly risk dashboard with data-quality and provenance reporting, configurable hypothetical market/sector/stock shocks, reconciled holding-level INR attribution and deterministic metric-based briefing; 91 automated tests pass locally.

For a shorter resume, omit details before the semicolon in bullet two only if you have no room; retain the live-access status in an interview or project link. The completed NSE check covers ten selected NIFTY 50 closes only.

## Interview questions and sample answers

### 1. What problem does PortfolioPulse solve?

It gives an Indian retail-investor or analyst a transparent view of an NSE equity portfolio's historical price risk relative to NIFTY 50. It calculates returns and risk, shows how holdings contribute, and estimates the effect of user-selected hypothetical shocks. It is an analytics prototype, not a recommendation or trading product.

### 2. Why did you use buy-and-hold quantities instead of constant portfolio weights?

The selected model keeps share quantities fixed through the observation window. Each holding is marked to its observed close, so actual INR market values and weights drift naturally. This models a portfolio that is not rebalanced every day. Target weights are kept separately as reference points for allocation-drift alerts.

### 3. How does the Angel One data path work?

It loads credentials from local environment variables, resolves configured NSE symbols against the current instrument master, requests daily candles in bounded date windows, normalizes timestamps to India time, validates observations, and saves source-specific data with retrieval and price-basis metadata. It has retries and an offline cache. The code and mocked behavior are tested, but the historical endpoint returned HTTP 403 in the local live check, so live candle delivery is not verified yet.

### 4. Why is the currently analyzed data not called Angel One data?

The local analysis dataset was imported from a Yahoo Finance OHLCV CSV through a separate source-labelled fallback. Its stored price basis is `unadjusted_ohlcv`, and the provider claim remains user-declared/unverified pending independent checking. Keeping the sources in separate datasets prevents a fallback from being mislabeled as broker data.

### 5. What is stored in SQLite?

The database stores instruments and NSE listings, portfolios, source-specific candle datasets, daily OHLCV candles, and an immutable holding snapshot per dataset. Analyses store their dataset, date window, settings, quality summary and finite results. Scenario runs store their settings and per-holding contributions. Foreign keys and uniqueness constraints protect relationships, and writes run in transactions.

### 6. How do you calculate annualized return, volatility and Sharpe?

Annualized return uses the first-to-last complete value ratio raised to `252 / observed date intervals`, minus one. Annualized volatility uses sample standard deviation of valid daily returns multiplied by `sqrt(252)`. Sharpe subtracts an effective daily rate converted from the configurable annual INR risk-free assumption, divides by daily sample standard deviation and annualizes by `sqrt(252)`. A zero-volatility Sharpe is undefined rather than forced to zero.

### 7. How do you handle missing prices and mismatched dates?

I do not replace missing prices with zero or forward-fill them. A one-day return needs valid adjacent observations. Portfolio-versus-NIFTY return comparisons use paired dates where both series are valid, and the data-quality output reports gaps and excluded observations. Standalone observed portfolio drawdown can still include an observed portfolio loss on a date when the benchmark is missing.

### 8. How should I interpret VaR, expected shortfall and stress results?

Historical VaR and expected shortfall summarize a tail of past one-day returns in the selected sample; they are not future-loss guarantees. Stress tests apply configured hypothetical one-period shocks to current holding values. Holding INR impacts sum to total scenario impact, but the shocks are not forecasts or historically calibrated predictions.

### 9. How do you treat the benchmark, dividends and risk-free rate?

The comparison benchmark is the NIFTY 50 price index, not NIFTY 50 TRI. The currently used fallback closes are unadjusted OHLCV and do not represent dividend-aware total returns. The annual INR risk-free rate is an explicit analyst setting, not automatically pulled from RBI; the example 6% is illustrative. I would only compare matched conventions and disclose the selected assumption.

### 10. What has and has not been verified, and what would you do next?

The analytics and scenario cases have deterministic unit tests, including hand-checkable examples; the current full suite has 91 passing tests. SQLite integrity and dependency checks passed locally. Mock tests do not prove live broker access. Ten NIFTY 50 closes displayed on the official NSE Indices history page were compared with the saved provider series and all fell within 0.01 index points. Next I would obtain a broader official price-index export, preserve its provenance and checksum, investigate missing dates and mismatches, and separately validate stock prices.

## Short project walkthrough

> PortfolioPulse India is a Python and SQLite risk monitor for a buy-and-hold NSE equity portfolio. I separated market ingestion, data quality, analytics and scenario attribution from the Streamlit dashboard. The analytics compare the portfolio with the NIFTY 50 price index, calculate conventional historical risk measures, and reconcile hypothetical shocks to holding-level INR contributions. The app records data provenance and missing observations. The current fallback dataset is unadjusted Yahoo Finance OHLCV. Ten official NIFTY 50 closes passed a spot check; live Angel One historical access and broader source validation remain pending.
