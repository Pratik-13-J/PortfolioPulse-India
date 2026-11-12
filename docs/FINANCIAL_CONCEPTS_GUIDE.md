# Financial Concepts Guide

**What this covers:** every financial metric in PortfolioPulse, as implemented.

**Format of each entry:** **definition · formula · code · example · interpretation · limits**.

**Conventions used throughout:**
- returns are daily close-to-close **price returns** in decimal form, compared with the NIFTY 50 **price index** (not TRI);
- 252 trading days a year;
- sample statistics use ddof = 1;
- losses are reported as positive numbers;
- the risk-free rate is an effective annual assumption (default 6%, illustrative), converted to a daily rate as rf_d = (1 + rf)^(1/252) − 1.

**Sources of the examples:** examples marked *(test)* come from hand-checked fixtures in `tests/`. Examples marked *(dataset 1)* are stored results on unverified Yahoo data.

**Not implemented, so not documented as available:** Treynor ratio, Brinson-Hood-Beebower, money-weighted IRR (no cash flows exist), sector-level risk contribution, filtered historical simulation.

---

## 1. Portfolio performance

### Daily return (buy and hold)
- **Definition:** the change in portfolio value from one day to the next, with share quantities held fixed.
- **Formula:** V_t = Σ q_i P_i,t; r_t = V_t / V_{t−1} − 1, which equals Σ w_i,t−1 r_i,t.
- **Code:** `portfolio_returns.build_buy_and_hold_returns(closes, quantities, benchmark)` → `.daily_comparison_returns`.
- **Example (test):** q = (2, 4), prices (100, 50) → (110, 49): V goes 400 → 416, so r = 4%, which equals 0.5 × 10% + 0.5 × (−2%).
- **Interpretation:** the return actually earned by holding the shares. Weights drift with prices.
- **Limits:** only dates where every holding and NIFTY have a close are used. Gaps are excluded, never bridged.

### Cumulative return and time-weighted return (TWR)
- **Definition and formula:** R = Π(1 + r_t) − 1. With no external cash flows, TWR = MWR = V_end / V_start − 1.
- **Code:** `risk_metrics.cumulative_returns`; `BuyAndHoldReturns.cumulative_comparison_returns`.
- **Example (test):** 400 → 422 gives +5.5%.
- **Limits:** the portfolio has no cash flows, so money-weighted IRR is not separately implemented.

### Annualized return (CAGR)
- **Formulas:**
  - geometric: (Π(1 + r_t))^(252/N) − 1;
  - endpoint version: (V_end / V_start)^(252 / observed intervals) − 1;
  - arithmetic, used for optimization: mean(r) × 252.
- **Code:** `risk_metrics.annualized_return`, `portfolio_returns.annualize_endpoint_price_return`, `expected_returns.estimate`.
- **Example (dataset 1):** portfolio −4.86% a year, against NIFTY +2.21% over the same window.
- **Limits:** about 2.7 years of data; the annualization is sensitive to the start and end dates.

### Annualized volatility
- **Formula:** σ = sd(r, ddof = 1) × √252.
- **Code:** `risk_metrics.annualized_volatility`.
- **Example (dataset 1):** 13.75%.
- **Interpretation:** the typical size of annual fluctuation.
- **Limits:** assumes i.i.d. scaling and ignores volatility clustering.

### Sharpe ratio
- **Formula:** (mean(r) − rf_d) / sd(r) × √252.
- **Code:** `risk_metrics.sharpe_ratio`.
- **Example (dataset 1, rf 6%):** −0.74.
- **Interpretation:** excess return per unit of total risk; negative here because returns were below the risk-free rate.
- **Limits:** returns `None` when volatility is zero; the rf is an assumption.

### Sortino ratio
- **Formula:** mean(r − rf_d) × 252 / (√(mean(min(r − rf_d, 0)²)) × √252). The downside deviation uses **all N** days.
- **Code:** `performance_metrics.sortino_ratio`.
- **Example (test):** r = (1%, −2%, 3%, 0%), rf = 0. The mean is 0.5% and the downside deviation is √(0.02²/4) = 1%, so Sortino = 0.005 × 252 / (0.01 × √252) = 7.94.
- **Interpretation:** penalises only downside volatility.
- **Limits:** returns `None` when there are no downside days.

### Maximum drawdown, duration and recovery
- **Formula:** DD_t = W_t / max_{s≤t} W_s − 1, with initial wealth 1 counted as a peak. Duration counts sessions from peak to recovery; recovery time counts sessions from trough to recovery.
- **Code:** `risk_metrics.maximum_drawdown`; `drawdowns.drawdown_episodes`, `drawdown_summary`.
- **Example (test):** wealth 1 → 0.9 → 0.99 → 0.792 gives −20.8%.
- **Example (dataset 1):** −29.5% from 2024-09-26, unrecovered after 508 sessions.
- **Limits:** "sessions" are dataset dates, not a validated NSE calendar.

---

## 2. Benchmark analytics

For these metrics, a = r_p − r_b is the daily active return. Portfolio and benchmark use identical valid dates.

| Metric | Formula | Code | Example *(test, p = (1%, −2%, 3%, 0%), b = (0.5%, −1%, 2%, −0.5%))* | Interpretation and limits |
|---|---|---|---|---|
| Active return | mean(a) × 252 | `annualized_active_return` | mean(a) = 0.25% → 63% a year | Arithmetic; *(dataset 1)* −7.46% |
| Tracking error | sd(a) × √252 | `tracking_error` | var(a) = 7.5e-5 → TE = 13.7% | How far the portfolio strays from the index; *(dataset 1)* 6.36% |
| Information ratio | active return / TE | `information_ratio` | 0.63 / 0.137 = 4.6 | Active return per unit of active risk; `None` if TE = 0 |
| Beta | cov(r_p, r_b) / var(r_b) | `beta` | equals the OLS slope, cross-checked with `scipy.stats.linregress` | Market sensitivity; *(dataset 1)* 0.90 |
| Downside beta | beta on days with r_b < 0 | `downside_beta` | days 2 and 4 only | Needs at least 2 down days |
| Up/down capture | geometric mean of r_p ÷ geometric mean of r_b, on up (down) days | `capture_ratios` | up = (√(1.01·1.03) − 1)/(√(1.005·1.02) − 1) | Above 1 on up days is good, below 1 on down days is good; *(dataset 1)* 0.87 / 0.97 |
| Rolling TE / beta | the same over 63/126/252 valid days | `rolling_relative_metrics` | — | Windows count valid observations |

---

## 3. Attribution

### Brinson-Fachler: allocation, selection, interaction
- **Formulas (for sector s):**
  - allocation A_s = (w_p,s − w_b,s)(R_b,s − R_b);
  - selection S_s = w_b,s(R_p,s − R_b,s);
  - interaction I_s = (w_p,s − w_b,s)(R_p,s − R_b,s);
  - and Σ(A + S + I) = R_p − R_b.
- **Code:** `benchmark_attribution.brinson_fachler(portfolio_sectors, benchmark_sectors)`.
- **Example (test):**
  - portfolio: A 60% at 10%, B 40% at 2%;
  - benchmark: A 50% at 8%, B 30% at 3%, C 20% at 5%;
  - so R_b = 5.9% and R_p = 6.8%;
  - sector A: allocation 0.1 × (8% − 5.9%) = 0.21%, selection 0.5 × 2% = 1.0%, interaction 0.1 × 2% = 0.2%;
  - sector C, not held: allocation only, (−0.2)(5% − 5.9%) = 0.18%;
  - total = 0.9% = R_p − R_b.
- **Interpretation:**
  - allocation rewards over-weighting sectors that beat the benchmark;
  - selection rewards picking better stocks within a sector;
  - interaction is the cross term.
- **Missing-sector conventions:** a sector the portfolio doesn't hold uses R_p,s := R_b,s, so it has no selection effect. An off-benchmark sector uses R_b,s := R_b, so its whole effect is interaction.
- **Limits:** **gated on real data.** No NIFTY 50 constituent weights exist, so the dashboard shows only a labelled synthetic example. Brinson-Hood-Beebower gives the same total allocation (because Σ(w_p − w_b) = 0) but a different sector split; it is not implemented.

### Carino linking
- **Formula:** k_t = [ln(1 + R_p,t) − ln(1 + R_b,t)] / (R_p,t − R_b,t), with K the same expression on cumulative returns. Each linked effect is Σ_t (k_t / K) × effect_t, and the linked effects sum to Π(1 + R_p) − Π(1 + R_b).
- **Code:** `benchmark_attribution.carino_link`; for security contributions, `performance_metrics.security_contributions`.
- **Interpretation:** single-period effects don't add up over time, because returns compound. Carino rescales each period so they do.

### Active Share
- **Formula:** ½ Σ |w_p,i − w_b,i| over the union of names.
- **Code:** `active_share`.
- **Example (test):** ½(0.1 + 0.1 + 0.2) = 20%.
- **Interpretation:** the share of the portfolio that differs from the index.

---

## 4. Risk

### Historical VaR and Expected Shortfall
- **Formula:** sort the N daily returns. With k = max(1, ⌈(1 − c)N⌉), VaR is minus the k-th worst return and ES is minus the mean of the k worst. Both are floored at 0.
- **Code:** `risk_metrics.historical_var_es`, wrapped by `var_models.historical`.
- **Example (test):** with N = 20, the 95% level gives k = 1 and VaR = ES = 5%; the 90% level gives k = 2, VaR 3%, ES 4%.
- **Example (dataset 1):** 99% VaR 2.13% with k = 7, ES 2.87%, which is ₹2,535 on ₹119,199.
- **Interpretation:** "on 99% of past days the loss was below 2.13%."
- **Limits:** assumes the past sample represents the future; it has only 7 tail points at 99%. The backtest failure is analysed in `VAR_BACKTEST_DIAGNOSTICS.md`.

### Parametric (normal) VaR and ES
- **Formula:** with z = Φ⁻¹(1 − c), VaR = −(μ + zσ) and ES = −μ + σφ(z)/(1 − c).
- **Code:** `var_models.parametric_normal`, `normal_var_es`.
- **Example (test):** μ = 0.2%, σ = √3.7e-4, giving 95% VaR = −0.002 + 1.6449σ = 2.96%.
- **Limits:** underestimates fat tails. This estimator is not floored, so it can be negative if the quantile is a gain.

### Cornish-Fisher VaR
- **Formula:** z_cf = z + (z² − 1)S/6 + (z³ − 3z)K/24 − (2z³ − 5z)S²/36, using moment skewness S and excess kurtosis K. VaR = −(μ + z_cf σ). ES is the tail average of the Cornish-Fisher quantile, in closed form.
- **Code:** `var_models.cornish_fisher`.
- **Example (dataset 1):** K = 3.2 lifts 99% VaR from 2.03% (normal) to 2.68%.
- **Limits:** the expansion is valid only while z_cf is monotone; `tail_monotone` flags this.

### Monte Carlo VaR and ES
- **Method:** a seeded simulation of asset returns, either multivariate normal (sample mean, Cholesky of the covariance) or a bootstrap of historical days. Each path is revalued at fixed quantities, as r_p = Σ V_i R_i / Σ V_i. The standard error comes from batch means.
- **Code:** `var_models.monte_carlo`.
- **Example (dataset 1):** 100,000 paths, seed 20261112; multivariate-normal 99% VaR 2.03% (SE 0.011%).
- **Limits:** the result depends on the model's assumptions; 1-day horizon only.

### Backtesting: Kupiec and Christoffersen
- **Kupiec:** LR = −2 ln[(1−p)^(n−x) p^x / ((1−x/n)^(n−x) (x/n)^x)] ~ χ²(1).
- **Christoffersen:** a first-order Markov independence test, LR ~ χ²(1); conditional coverage is the sum of the two, ~ χ²(2).
- **Code:** `var_backtests.rolling_var_forecasts`, `kupiec_pof`, `christoffersen_independence`, `backtest_var`.
- **Example (test):** n = 250, x = 5, p = 1% gives LR = 1.957, p = 0.162, so the model passes.
- **Example (dataset 1):** historical 95% VaR has 33 exceptions against 21.8 expected (p = 0.022), so it **fails**.
- **Interpretation:** too many exceptions means risk is underestimated. Clustered exceptions mean the model adapts too slowly.

### Covariance estimators
- **Formulas:**
  - sample: (1/(N−1)) Σ (x − x̄)(x − x̄)′;
  - Ledoit-Wolf: δ·m·I + (1 − δ)S, with δ estimated from the data;
  - EWMA: Σ_j (1 − λ)λ^j r r′ normalised, with λ = 0.94.
- **Code:** `covariance_estimators.sample_covariance`, `ledoit_wolf_covariance`, `ewma_covariance`.
- **Example (dataset 1):** δ = 0.055; current-weight volatility is 13.88% (sample), 13.69% (Ledoit-Wolf) and 17.50% (EWMA).
- **Why Ledoit-Wolf for optimization:** shrinkage reduces estimation noise, which optimizers otherwise amplify.

### Risk contributions (Euler)
- **Formula:** RC_i = w_i(Σw)_i / σ_p, which sums to σ_p.
- **Code:** `optimization.risk_contributions`, `risk_metrics.volatility_contributions`.
- **Example (test):** Σ = [[0.04, 0.01], [0.01, 0.09]], w = (0.6, 0.4). Then Σw = (0.028, 0.042), σ_p = √0.0336, and RC_1 = RC_2 = 0.0917, so the two assets contribute equal risk.
- **Component VaR:** w_i(−μ_i − z(Σw)_i/σ_p), which sums to the delta-normal VaR. Code: `var_models.delta_normal_components`. It is a **different** quantity from volatility contributions.

---

## 5. Stress testing

All stress outputs are hypothetical one-period revaluations of the current fixed quantities. The P&L is impact_i = V_i × shock_i (`attribution.scenario_attribution`, `stress_impact.stress_impact`), and it reconciles to the total.

| Shock | Formula | Code | Example *(dataset 1)* | Limits |
|---|---|---|---|---|
| Market (beta-propagated) | shock_i = β_i × m | `stress_scenarios.beta_vector` | NIFTY −10% gives −9.18% (portfolio β ≈ 0.92) | In-sample OLS betas |
| Direct stock or sector | a stated shock | `direct_vector` (calls `scenarios.scenario_shocks`) | Banking −15% gives −6.66% | Assumed sizes |
| Combined | β_i m + sector overlay | `beta_vector` | NIFTY −10% plus Banking −10% gives −13.62% | Effects add up |
| Covariance propagation | E[r_U \| r_S = s] = Σ_US Σ_SS⁻¹ s | `conditional_moves`, `covariance_vector` | ICICIBANK −20% gives −10.48% (versus −5.83% in isolation) | Assumes a linear, normal-like relationship |
| Interest rate / USD-INR | factor_i = Σ_f sens_i,f × move_f | `macro_vector`, `factor_sensitivities` | +100 bp gives −2.62% | **Assumed sensitivities** until RBI/FBIL data is supplied |
| Historical replay | each holding's own observed return between anchor closes | `historical_stress.replay_event` | 4 June 2024 election day gives −5.36% | COVID-2020 and 2022 unavailable: no data before 2024 |

---

## 6. Optimization

| Method | Objective and constraints | Code | Example *(dataset 1)* | Limits |
|---|---|---|---|---|
| Expected returns | arithmetic mean × 252; CAGR; Bayes-Stein shrinkage | `expected_returns.estimate` | standard errors of 12–16% a year | Very noisy |
| Minimum variance | min w′Σw, with Σw = 1 and bounds | `optimization.min_variance` (SLSQP) | volatility 13.47% | Uses only the covariance |
| Maximum Sharpe | max (μ′w − rf)/√(w′Σw) | `max_sharpe` | **Undefined** at rf 6%: the best feasible E[r] is 1.98% | Returns `undefined` instead of a forced corner |
| Risk parity (ERC) | equal RC_i; log-barrier ½y′Σy − (1/n)Σ ln y | `erc_log_barrier`, `risk_parity` | constrained: the Banking cap binds | Falls back to a constrained solution |
| Efficient frontier | min variance subject to μ′w = target, for 25 targets | `efficient_frontier` | from −3.48% at 13.47% volatility | Ex-ante, in-sample |
| Constraints | 5–35% per stock, Banking ≤ 40%, turnover Σ\|w − w₀\| ≤ T | `Constraints`, `build_problem`, `check_feasibility` | turnover-limited min-variance at 20% | Can be infeasible, which is detected and reported |
| Transaction costs | rate = Σbps + GST × Σ(GST-applicable bps) | `portfolio_allocation.CostModel` | buy 11.86 bp, sell 10.36 bp | Rates are assumptions |
| Integer shares | floor, then greedy steps that reduce the gap; leftover cash ≥ 0 | `integer_allocation`, `rebalance_allocation` | ₹10 lakh: ₹169 cash left, ₹1,185 in costs | — |
| Walk-forward | re-estimate on the trailing 252 days, hold 21 days, net of costs | `optimization_backtest.walk_forward` | min-variance −13.3% a year out of sample | About 1.7 years; survivorship bias |

**Why optimization can be infeasible:** the constraints can contradict each other. For example, Σ min > 100%, or the current weights breach a cap by more than half the turnover limit. `build_problem` checks the obvious cases, and every solver result is re-checked against all constraints.
