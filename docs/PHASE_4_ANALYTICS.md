# Phase 4 verification — Indian equity portfolio analytics

This records the Phase 4 state. The later [final audit](FINAL_AUDIT.md) contains the current browser and limited NSE spot-check results.

## Result and scope

Phase 4 calculates and stores risk measures for the **saved, real NSE equity dataset**. It reads the immutable share quantities saved with that dataset, not the current editable portfolio configuration. The model is buy and hold: quantities remain fixed, while INR market values and weights drift. NIFTY 50 is a separate price-index benchmark. Phase 6 now presents these tested results in the Streamlit analyst dashboard; session-edited target weights do not rewrite the Phase 4 snapshot.

The verified local dataset contains 4,133 daily OHLCV rows from Yahoo Finance via yfinance 1.7.0, labelled `unadjusted_ohlcv` and `user_declared_unverified`. It has 689 observed union dates, 688 shared price dates and **686 valid paired one-day return dates**. The missing NIFTY 50 close on 15 January 2026 excludes one-day returns on 15 and 16 January. The first observed date has no prior return. No quote or return is filled. Live Angel One historical candles and independent NSE price checks remain pending from the earlier data gates.

## Calculation conventions

Let `q_i` be a holding's fixed shares and `P_i,t` its observed close in INR. Portfolio value is `V_t = sum(q_i × P_i,t)`, and actual weight is `w_i,t = q_i × P_i,t / V_t`. For a valid adjacent observed-date pair, the portfolio daily price return is `V_t / V_(t−1) − 1`; the NIFTY return is calculated on the **same pair**. Lagged weights times asset returns sum to that portfolio return. Any pair touching a missing holding or benchmark close is excluded from the paired daily sample.

| Measure | Exact convention |
| --- | --- |
| Cumulative price return | Observed current value / first complete shared-date value − 1. The portfolio-only path retains observed values on a benchmark-missing date; the paired chart has a visible gap. |
| Annualized price return | `(last complete value / first complete value) ** (252 / observed union-date intervals between endpoints) − 1`; the same denominator is used for NIFTY. This endpoint measure can include a price move across a missing intermediate quote without inventing a one-day return. |
| Annualized volatility | Sample standard deviation (`ddof=1`) of the valid paired daily returns × `sqrt(252)`. |
| Sharpe ratio | `(mean valid daily return − effective daily INR risk-free assumption) / sample daily standard deviation × sqrt(252)`, with daily risk-free rate `(1 + annual_rate) ** (1/252) − 1`. A zero-volatility Sharpe is undefined (`null`), never reported as zero. |
| Maximum drawdown | Minimum observed value / running observed peak − 1, including the initial peak. Standalone portfolio and NIFTY drawdowns use all their available observed price levels between common endpoints. Separate **matched-date** drawdowns use only shared price dates for comparison. |
| Historical 95% one-day VaR and expected shortfall | Sort the valid one-day returns ascending; select `k = ceil((1 − 0.95) × N)` worst observations. VaR is the nonnegative loss magnitude of the kth worst return; ES is the nonnegative loss magnitude of their mean. With 686 returns, `k = 35`. This empirical tail is coarse for short samples and is not a future-loss limit. |
| Correlation | Pearson sample correlation on the same valid return dates for holdings, portfolio and NIFTY. Constant-series correlations are undefined and written as `null` in JSON; they are omitted from finite SQL results. |
| Marginal and component volatility | Use the **last shared portfolio/NIFTY price-date weights** and annualized sample asset-return covariance `Σ`. The current-weight proxy volatility is `sqrt(wᵀΣw)`; marginal contribution is `(Σw)_i / proxy_vol`, and component contribution is `w_i × marginal_i`. Components sum to the proxy volatility. This snapshot estimate can differ from realized buy-and-hold volatility because weights drifted. |

All headline performance numbers are **price returns**, not dividend-aware total returns. Unadjusted closes may be affected by dividends, splits or other corporate actions. The observed union is not an independently verified NSE trading calendar, and a missing intermediate quote may hide an intraday or daily trough. The annual INR risk-free rate is an analyst assumption; the example 6% below is illustrative, not a fetched RBI rate.

## Local verification result

For dataset ID 1, 2 January 2024 to 15 October 2026, using an **illustrative 6% effective annual INR risk-free rate** and 95% historical confidence:

| Metric | Portfolio | NIFTY 50 |
| --- | ---: | ---: |
| Endpoint cumulative price return | −12.72% | +6.15% |
| Endpoint annualized price return | −4.86% | +2.21% |
| Annualized daily-return volatility | 13.75% | 13.59% |
| Sharpe ratio | −0.743 | −0.203 |
| Maximum observed drawdown | −29.51% | −15.77% |
| Historical 95% one-day VaR | 1.36% | 1.37% |
| Historical 95% one-day expected shortfall | 1.91% | 1.93% |

Portfolio/NIFTY correlation on the paired daily sample is about **0.892**. The last matched-date weight covariance proxy volatility is about **13.88%**, and the five holding component contributions reconcile to that proxy. Its difference from the realized portfolio volatility of 13.75% is expected for drifting weights. These numbers describe the local, source-labelled input and chosen assumptions; they are not independently checked NSE returns or investment advice.

## Run and outputs

From the repository folder after the Phase 3 import:

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_phase4 --dataset-id 1 --annual-rf 0.06 --confidence 0.95
```

`--dataset-id` explicitly selects a saved source and price basis. `--annual-rf` is an effective annual decimal rate; its default is `0`. The command reads local SQLite data, calculates the metrics, and saves the assumptions, quality summary and finite scalar results in **one SQLite transaction**. It also writes an ignored local JSON report and CSVs for paired daily returns, cumulative observed values, correlation and holding risk contributions under `data/processed/`. Repeating an unchanged run reuses its analysis ID. The returned report path identifies the exact run.

Hand-calculated tests cover drifting quantities, return-contribution reconciliation, compounding, 252-day geometric annualization, sample volatility, daily risk-free conversion, drawdown, nearest-rank VaR/ES, correlation, and covariance contributions. Regression cases prove that a missing benchmark quote cannot hide an observed portfolio drawdown, a missing holding quote cannot hide an observed NIFTY drawdown, and a trailing benchmark gap cannot move the contribution weight date beyond the analysis window. Zero volatility, insufficient history and invalid assumptions produce explicit undefined values or errors rather than infinities. The stored-dataset test also changes the current holding configuration and confirms that the analysis still uses its original saved snapshot. A simulated SQL result-write failure rolls back its run metadata.

The final local check passed **77 tests**, Python compilation, dependency checks and SQLite integrity/foreign-key checks. The 4,133-candle dataset remained dataset ID 1; repeating the final 6%/95% analysis returned run ID 4 with **40 finite scalar SQL results**. The JSON report records the price basis, source, assumptions, excluded dates, as-of weight date, undefined-value reasons and exact local output paths. Market input and reports remain ignored by Git, so a fresh checkout needs its own legally obtained source file.
