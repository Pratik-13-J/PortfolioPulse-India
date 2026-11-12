# Financial Methodology Audit — PortfolioPulse India v1.0 (2026-11-13)

**Scope.** Every implemented performance, attribution, risk, VaR/CVaR, backtesting, stress and optimization methodology.

**Method.** Each item was validated in three ways:
1. Source inspection against its documented formula.
2. **Independent** hand-calculated fixtures: expected values come from plain arithmetic, never from the production function. Fixtures live in `tests/test_financial_audit.py` and the phase test files.
3. Read-only reconciliation of the **stored real-data runs** (dataset 1: runs 9/10/11) with `scripts/audit_consistency.py`, which reported **29/29 PASS**.

**Statuses:**
- **Verified:** independently checked.
- **Defect found:** the implementation was wrong.
- **Not implemented:** the methodology is absent.
- **Data gated:** implemented, but real-data validation needs data the project lacks.

**Result:** no mathematical defect was found in any implemented methodology. One integration defect was fixed: the dashboard's default dataset (see §9). Two conventions are documented as limitations rather than changed, because changing them would alter stored results.

## 1. Portfolio performance

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Daily buy-and-hold return | `portfolio_returns.build_buy_and_hold_returns` | Verified | Hand fixture: V = 400 → 416 → 406 → 422 gives r_t = V_t/V_{t-1} − 1; lagged-weight identity 0.5·10% + 0.5·(−2%) | — | No action | Valid only on dates where every holding and the benchmark are observed; gaps are excluded, not bridged |
| Cumulative / TWR | same; `risk_metrics.cumulative_returns` | Verified | ∏(1+r_t) − 1 = 422/400 − 1 | — | No action | No external cash flows, so TWR = MWR |
| Money-weighted return (IRR) | — | Not implemented | — | Not applicable: fixed quantities, no flows | No action | Would only matter with cash flows |
| Arithmetic vs geometric annual return | `risk_metrics.annualized_return`, `expected_returns.estimate` | Verified | Hand: ∏(1+r)^(252/N) − 1; arithmetic = mean × 252; AM-GM bound test | — | No action | 252 trading days |
| Endpoint CAGR | `portfolio_returns.annualize_endpoint_price_return` | Verified | (V_end/V_start)^(252/intervals) − 1, Phase 4 tests | — | No action | Counts observed intervals, not calendar time |
| Volatility, Sharpe | `risk_metrics` | Verified | Hand: sample sd (ddof=1) × √252; Sharpe uses daily rf = 1.06^(1/252) − 1 | — | No action | rf 6% is illustrative; the Phase 4 CLI default is 0% (configurable) |
| Sortino / downside deviation | `performance_metrics.sortino_ratio` | Verified | Hand: mean 0.005, DD = √(0.02²/4) = 0.01 → 0.005·252/(0.01·√252) | My first fixture used the wrong mean; the production value was right | Test corrected | DD over all N observations (documented) |
| Maximum drawdown, duration, recovery | `risk_metrics.maximum_drawdown`, `drawdowns.py` | Verified | Hand: 1 → 0.9 → 0.99 → 0.792 = −20.8% (initial wealth counted); episode durations hand-built | — | No action | Durations count dataset sessions, not a validated NSE calendar |
| Rolling returns, period returns | `performance_metrics.rolling_relative_metrics`, `period_returns` | Verified | Window-of-two and anchor-date fixtures | — | No action | Windows count valid observations |
| Security contribution, Carino | `performance_metrics.security_contributions` | Verified | Hand Carino fixture; reconciles to compounded return within 1e-12 | — | No action | — |

## 2. Benchmark analytics

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Active return, TE, IR | `performance_metrics` | Verified | Hand: active = [.005, −.01, .01, .005], sample var 7.5e-5, TE = √var·√252, IR = mean·252/TE | — | No action | Arithmetic active return |
| Beta, downside beta | same | Verified | Hand sample cov/var; downside uses b < 0 days; SciPy `linregress` matches the OLS beta in the stress module | — | No action | Needs ≥ 2 down days, else `None` |
| Up/down capture | `capture_ratios` | Verified | Hand: (√(1.01·1.03) − 1)/(√(1.005·1.02) − 1), etc. | — | No action | Geometric definition; `None` with no down days |
| Rolling beta / TE | `rolling_relative_metrics` | Verified | Existing fixture | — | No action | — |
| Treynor ratio | — | Not implemented | — | Not part of the approved roadmap | No action | — |
| Benchmark definition | all modules | Verified | `audit_consistency`: same dates, same NIFTY 50 price-index series | — | No action | **Price index, not TRI** |

## 3. Brinson attribution

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Brinson-Fachler A/S/I | `benchmark_attribution.brinson_fachler` | Verified (formula) / **Data gated** (real data) | Hand 3-sector example: R_b = .059, R_p = .068; A, S, I per sector computed by hand; A + S + I = .009 | — | No action | No NIFTY 50 constituent weights exist, so `attribution_gate` refuses. Nothing is computed on real data; weights are never fabricated |
| Missing-sector conventions | same | Verified | Sector not held: R_p,s := R_b,s; off-benchmark sector: R_b,s := R_b (whole effect is interaction, e.g. 0.3·(0.12 − 0.04)); zero-weight position contributes 0 | — | No action | Documented in the module |
| Brinson-Hood-Beebower | — | Not implemented | Identity tested: total BF allocation = total BHB allocation (Σ(w_p − w_b) = 0), so only the sector split differs | BF chosen in the roadmap | No action | — |
| Carino multi-period linking | `carino_link` | Verified | Hand k-factor recomputation; linked total = ∏(1+R_p) − ∏(1+R_b) | — | No action | — |
| Active Share | `active_share` | Verified | Hand: ½(.1 + .1 + .2) = .2 | — | No action | Data gated on real weights |

## 4. Risk measurement and VaR/CVaR

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Historical VaR/ES | `risk_metrics.historical_var_es` (reused by `var_models`) | Verified | Hand: N = 20, 95% → k = 1 (0.05, 0.05); 90% → k = 2 (VaR 0.03, ES 0.04); stored 95% VaR equals a fresh Phase 4 run | — | No action | Nearest rank k = ⌈(1−c)N⌉; at 99% on N = 686, k = 7 |
| Parametric normal VaR/ES | `var_models.parametric_normal` | Verified | Closed form vs hand constants and `scipy.stats.norm` | **Convention:** the historical estimator floors at 0, but the parametric one is unfloored (it can be negative when the quantile is a gain) | Documented, not changed (changing it would alter stored results) | On dataset 1 every VaR is positive |
| Cornish-Fisher VaR/ES | `var_models.cornish_fisher` | Verified | Hand z_cf; ES vs numerical quadrature; moments vs SciPy | ES < VaR can occur when the expansion is non-monotone (dataset 2) | Flagged via `tail_monotone` | Small samples are unreliable |
| Monte Carlo VaR/ES | `var_models.monte_carlo` | Verified | MVN within 3 SE of the closed form; fixed seed reproduces bit-identically; fixed-quantity revaluation recomputed by hand | — | No action | Batch-means SE collapses for tiny bootstrap samples; 1-day horizon, no √h scaling |
| INR conversion | `risk_analysis` | Verified | `audit_consistency`: VaR_INR = VaR × ₹119,198.75 for every stored method | — | No action | Value at the as-of date |
| ES ≥ VaR, sign convention | all | Verified | Property checks on fixtures and on all stored methods | — | No action | Losses are positive numbers |
| Covariance estimators | `covariance_estimators` | Verified | Sample vs `numpy.cov`; Ledoit-Wolf vs a loop transcription of the paper; EWMA hand example; PSD checked (κ 4.3–8.1 on dataset 1) | — | No action | LW uses 1/N normalisation |
| Correlation | `risk_metrics.asset_correlations` | Verified | Existing fixture (constant series → NaN) | — | No action | — |

## 5. Backtesting

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Rolling one-day-ahead forecasts | `var_backtests.rolling_var_forecasts` | Verified | Only the trailing window is used (perturbation test) | — | No action | 250-day window, 436 forecasts on dataset 1 |
| Kupiec POF | `kupiec_pof` | Verified | Hand: n = 250, x = 5, p = .01 → LR 1.95681; stored LRs recomputed exactly | Historical 95% **fails** at 5% (33 exceptions vs 21.8 expected, p = 0.022); others pass | Reported as a failure | Asymptotic χ²; few 99% exceptions |
| Christoffersen independence and conditional coverage | `christoffersen_independence`, `backtest_var` | Verified | Hand transition counts; LR 2.231438 | — | No action | Insufficient samples are reported as undefined (e.g. dataset 2) |

## 6. Risk decomposition

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Volatility RC = w_i(Σw)_i/σ_p | `optimization.risk_contributions`, `risk_metrics.volatility_contributions` | Verified | Hand: Σ = [[.04, .01], [.01, .09]], w = (.6, .4) → Σw = (.028, .042), σ_p = √.0336; ΣRC = σ_p | — | No action | Current-weight proxy ≠ realised drifting-weight volatility (labelled) |
| Component VaR/ES (Euler) | `var_models.delta_normal_components` | Verified | Hand 2-asset example; finite-difference gradient; stored components sum to totals for 3 estimators × 2 levels | Kept separate from volatility contributions | No action | Delta-normal |
| MC component ES | `monte_carlo` | Verified | Stored components sum to the MC ES | — | No action | — |
| Sector risk contribution / diversification benefit | — | Not implemented | — | Derivable by summing components; not in the roadmap | No action | — |

## 7. Stress testing

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Direct, beta, covariance and factor shocks; component labels | `stress_scenarios` | Verified | Hand β·m plus overlays; conditional mean Σ_US Σ_SS⁻¹ s; covariance conditioning on NIFTY equals OLS β | — | No action | In-sample betas |
| Historical replay | `historical_stress.replay_event` | Verified | Hand anchors, nearest-earlier closes, beta fallback for a missing close, partial windows refused | COVID-2020 and 2022 reported **unavailable** (data starts 2024) | No action | Event windows are analyst-defined and unverified |
| Rate / USD-INR sensitivity | `factor_sensitivities`, `macro_vector` | Verified (OLS on simulated data) / **Data gated** | Recovers known simulated sensitivities within SE; `np.linalg.lstsq` cross-check | No real factor series | Assumed values labelled `illustrative_assumption_not_estimated` | Needs RBI/FBIL series |
| P&L, sector impact, loss share, post-stress risk | `stress_impact`, `attribution.scenario_attribution` | Verified | Hand INR P&L; all 16 stored scenarios reconcile; P&L recomputed from candles in a test | — | No action | One-period stress vs one-day VaR is a scale comparison only |

## 8. Optimization

| Methodology | Implementation | Status | Validation | Findings | Action | Limitations |
|---|---|---|---|---|---|---|
| Minimum variance | `optimization.min_variance` | Verified | 2-asset closed form; Σ⁻¹1/1′Σ⁻¹1; KKT gradient spread | — | No action | — |
| Maximum Sharpe | `max_sharpe` | Verified | Matches the analytic tangency; ≥ every frontier Sharpe | **Undefined** at rf 6% on dataset 1 (best feasible E[r] 1.98%) | Reported, not forced | rf 0% sensitivity provided |
| Risk parity (ERC) | `erc_log_barrier`, `risk_parity` | Verified | Equal risk shares to 1e-6; 2-asset inverse volatility | Exact ERC breaches the 40% Banking cap on dataset 1, so the constrained solution is used (max share deviation 0.25 pp) | Reported | — |
| Efficient frontier | `efficient_frontier` | Verified | Monotone volatility, targets met, weights within bounds | — | No action | Ex-ante, in-sample |
| Constraints (long-only, bounds, sector, turnover, fully invested) | `build_problem`, `check_feasibility` | Verified | Unit tests; stored weights sum to 1 within 5–35%, Banking ≤ 40% (`audit_consistency`); infeasible turnover proven by hand | — | No action | — |
| Expected returns | `expected_returns` | Verified | Arithmetic / CAGR / Bayes-Stein hand formula | SE 12–16% per year | Labelled | In-sample |
| Costs, integer allocation | `portfolio_allocation` | Verified | Hand buy rate 11.86226 bp; budget identity invested + cost + cash = budget for every stored strategy | — | No action | Cost rates are assumptions |
| Walk-forward | `optimization_backtest.walk_forward` | Verified | Hand drift/cost example; no look-ahead (perturbation); stored first OOS date = return #252 | — | No action | Fixed universe (survivorship); about 1.7 years |
| "Diversification" wording | dashboard / reports | Verified | Trade-off notes state 5 stocks / 4 sectors with no broad-market claim | — | No action | — |

## 9. Cross-module consistency, dashboard and reports

| Check | Status | Evidence |
|---|---|---|
| As-of date, portfolio value, N and annualization agree across Phase 4 / risk / stress / optimization | Verified | `audit_consistency`: 2026-10-15, ₹119,198.75, N = 686, 252 |
| Return frequency, currency and price basis | Verified | Daily, INR, the same dataset 1 series; labels carried in `settings_json` |
| Risk-free rates | Verified with note | 6% (optimization, dashboard default) is illustrative; the Phase 4 CLI default is 0%; risk and stress do not use rf |
| Dashboard shows the stored engine outputs | Verified | Loaders reproduce engine values exactly (tests); live check of runs 9/10/11 |
| Dashboard default dataset | **Defect found (integration)** | It opened on dataset 2 (no stored runs), leaving the engine tabs empty |
| | **Fixed** | `ui.stored_results.default_dataset_index` now prefers the most recent dataset with stored runs; regression test added |
| No dashboard writes, no duplicate runs | Verified | AppTest against an OS read-only DB file (SHA-256 and mtime unchanged); no duplicate Conv 3–5 runs |
| Reports | Verified | HTML escaping, provenance header and CSV zip tests; manual export from a clean venv |
