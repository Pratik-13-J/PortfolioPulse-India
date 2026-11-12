# Handoff — Conversation 5 (2026-11-12): Portfolio Construction & Optimization (roadmap Phase 5)

Scope: roadmap Phase 5 only. No dashboard work, macro intelligence, monitoring or AI research. Branch `conv5-optimization`, created from `9f0e115` (tip of the unmerged `conv4-stress-testing`). Nothing has been pushed or merged.

| Commit | Summary |
|---|---|
| `e882e9d` | `config/optimization.json`, `expected_returns.py`, `optimization.py` + tests |
| `02a7226` | `portfolio_allocation.py`, `portfolio_evaluation.py`, `optimization_backtest.py` + tests |
| `373e8d0` | Migration v4, DB helpers, `optimization_analysis.py` (engine `phase5_opt_v1`), `scripts/analyze_optimization.py` + integration tests |
| (docs commit) | README, this file, `CLAUDE.md` |

## 1. Verification (actually run)

- **Tests:**
  - Baseline 219 / 71 subtests → 235 → 244 → **250 passed, 71 subtests passed** (final).
  - There are 31 new tests: `test_optimization.py` 16, `test_portfolio_construction.py` 9, `test_optimization_analysis.py` 6.
  - No existing test was removed or edited, and the existing migration tests pass with v4.
- **Dependencies:** none added. SciPy 1.18.1 (`optimize`) comes from the Conv 3 pin.
- **Schema v4 (additive):** `optimization_runs`, `optimization_weights`, `optimization_backtests`. `illustrative = 1` is enforced by CHECK. Holdings tables are never written.
- **DB:**
  - **Before:** fingerprint taken (analysis IDs ≤ 10, scenario runs ≤ 18). The run was then trialled on a copy, where v4 applied and all old tables were identical.
  - **Backups:** `data/backups/portfoliopulse_pre_conv5_optimization_20261112T173719020304Z.db`, plus the migration runner's automatic `portfoliopulse_pre_migration_20261112T173722155561Z.db`.
  - **Real run:** **analysis run 11** (189 results), 7 optimization runs, 30 weight rows, 2,170 walk-forward rows. A second run was idempotent.
  - **Preservation:**
    - every pre-existing table is bit-identical, including `dataset_holdings` and both datasets;
    - `schema_version` rows 1–3 are unchanged;
    - the Phase-4/perf hex dump is unchanged;
    - integrity `ok`, FK check empty.
- **Dataset 2** (10 returns) is refused with `InsufficientDataError` (below `minimum_observations = 252`), so nothing is written.

## 2. Methodology

**Expected returns** (`expected_returns.py`; all in-sample):
- `arithmetic`: mean × 252. This is the default for optimization.
- `cagr`: reuses `risk_metrics.annualized_return`.
- `bayes_stein`: Jorion 1986 shrinkage toward the minimum-variance portfolio mean, with S × (N−1)/(N−p−2).
- Each estimate has a standard error of sd × 252 / √N. On dataset 1 this is **11.8–16.2% per year**, as large as the means themselves. Bayes-Stein shrinkage intensity is 0.56.
- The risk-free rate is configurable (default 6%, illustrative and not a live rate). An rf sensitivity list is supported.

**Covariance:**
- Ledoit-Wolf by default (`covariance_estimators`, from Conv 3), annualized ×252.
- `validate_covariance` checks that the matrix is finite and symmetric and that its eigenvalues are ≥ −tol, and reports the condition number.
- Dataset 1 results: sample κ 4.7, LW κ 4.3 (shrinkage 0.055), EWMA κ 8.1. All three are PSD.

**Optimizers** (`optimization.py`, SciPy SLSQP from several starts; a solver "success" is accepted only if a re-check shows all constraint violations ≤ 1e-7):
- **Minimum variance:** min w′Σw. KKT stationarity on free assets is reported when only bounds apply. Verified against the 2-asset closed form and the Σ⁻¹1/1′Σ⁻¹1 interior solution.
- **Maximum Sharpe:** max (μ′w − rf)/σ. If the maximum feasible μ′w ≤ rf, the result is **undefined** and no maximum-volatility corner is forced. It matched the analytic tangency Σ⁻¹(μ − rf) normalised, and was at least the frontier's best Sharpe.
- **Risk parity (ERC):** log-barrier ½y′Σy − (1/n)Σln yᵢ via L-BFGS-B, then w = y/Σy. Risk shares are equal to 1e-6, and the 2-asset case equals inverse volatility. If exact ERC breaks a constraint, a constrained least-squares fallback on risk shares runs, reported with status `constrained` and its maximum share deviation.
- **Efficient frontier:** 25 points (configurable), from the minimum-variance return to the maximum feasible return, each with the equality μ′w = target.
- **Constraints** (all set in `config/optimization.json`):
  - minimum and maximum weight; the maximum is also the single-stock concentration cap;
  - sector maximum and minimum;
  - long-only and fully invested;
  - turnover Σ|w − w_current| ≤ T, linearised with auxiliary variables;
  - a necessary-condition infeasibility check runs before solving.

**Evaluation** (`portfolio_evaluation.py`; reuses `risk_metrics`, `performance_metrics` and `var_models` from Conv 3):
- **Ex-ante:** μ′w, √(w′Σw) and Sharpe.
- **In-sample:** a constant-mix (daily rebalanced, gross) return series, labelled "illustrative, in-sample". From it: annual return and volatility, Sharpe, Sortino, beta vs NIFTY 50, maximum drawdown, and historical / normal / Cornish-Fisher VaR and ES at 95% and 99%.
- **Risk decomposition:** delta-normal 99% component VaR/ES and Euler volatility contributions. Reconciliation is checked: the weights sum to 1, and components minus the total ≈ 0 (≤ 1e-10).
- **Concentration and trading:** HHI, effective N and sector weights; turnover and rebalance cost; first-year expected return net of cost.

**Costs and allocation** (`portfolio_allocation.py`):
- **Per-side rate** = Σbps/1e4 + GST × Σ(GST-flagged bps)/1e4, plus an optional per-order fee.
- **Default components** (illustrative; verify current rates): STT 10/10 bp, stamp duty 1.5 bp on buys, NSE transaction charge 0.297 bp, SEBI fee 0.01 bp, 18% GST, zero brokerage. That gives a buy rate of 11.862 bp and a sell rate of 10.362 bp.
- **Integer shares:** start from the floor, then add one share at a time where it reduces that holding's absolute gap and fits the cash. Residual cash is ≥ 0, the result is deterministic, and leftover cash is never forced into overweight names.
- **Self-financed rebalance:** trades from the existing shares are costed on both sides. The input holdings are not modified.

**Walk-forward** (`optimization_backtest.py`):
- 252-observation estimation, 21-observation hold, trailing window only (no look-ahead; tested by perturbing future returns).
- Weights drift between rebalances, and the cost is charged on the rebalance day.
- Every rule starts from the existing portfolio's actual weights.
- "Existing" is the realized buy-and-hold series. If a rule is undefined in a window (e.g. max-Sharpe), the prior weights are kept and the failure is counted.

## 3. Dataset 1 results (run 11)

Data and assumptions:
- Yahoo data, `user_declared_unverified`, `unadjusted_ohlcv` with the basis unconfirmed; N = 686.
- As of 2026-10-15, existing value ₹119,198.75.
- rf 6%, arithmetic μ, Ledoit-Wolf Σ.
- Constraints: 5–35% per stock, Banking ≤ 40%.
- Everything below is illustrative and not a recommendation.

| Strategy | Status | E[r] ex-ante | Vol ex-ante | In-sample return / max DD | Hist 99% VaR / ES | Eff. N | Banking | OOS net return / max DD |
|---|---|---|---|---|---|---|---|---|
| Existing | reference | −1.09% | 13.69% | −2.03% / −25.6% | 2.19% / 2.97% | 4.72 | 44.4% | −12.78% / −27.8% |
| Equal weight | reference | −3.35% | 13.66% | −4.22% / −28.8% | 2.17% / 2.94% | 5.00 | 40.0% | −11.66% / −28.2% |
| Min variance | optimal | −3.48% | 13.47% | −4.32% / −29.2% | 2.17% / 2.88% | 4.65 | 40.0% | −13.32% / −27.8% |
| Max Sharpe (rf 6%) | **undefined** | best feasible E[r] 1.98% ≤ 6% | | | | | | (12 of 21 windows undefined) −16.18% |
| Risk parity | constrained | −3.43% | 13.57% | −4.28% / −28.9% | 2.12% / 2.89% | 4.97 | 40.0% | −12.18% / −28.1% |
| Min var, turnover ≤ 20% | optimal | −3.22% | 13.48% | −4.06% / −28.8% | 2.15% / 2.87% | 4.73 | 40.0% | n/a |
| Max Sharpe, rf 0% (sensitivity) | optimal | 1.98% | 14.66% | 0.89% / −22.6% | 2.42% / 3.20% | 3.45 | 40.0% | n/a |
| NIFTY 50 price index (OOS) | | | | | | | | −1.14% / −15.7% |

**Out-of-sample window:** 2025-01-13 to 2026-10-15, with 21 rebalances.

**Other details:**
- **Risk parity:** the exact ERC weights put Banking at 40.3%, so the constrained solution has a maximum risk-share deviation of 0.25 percentage points.
- **Existing portfolio:** it is outside the configured constraints (Banking 44.4%), so every constrained strategy needs some turnover.
- **Integer allocation, ₹10 lakh, min-variance:** ₹998,646 invested, ₹1,184.62 costs, ₹169.14 cash left. Rebalancing the existing ₹1.19 lakh holdings to min-variance costs ₹32.10 and leaves ₹943.80 cash.

**Trade-offs:**
- On this sample, every optimized portfolio cuts ex-ante volatility only slightly (13.47–13.69%), because the five stocks have similar risk and correlation.
- Return-seeking choices depend entirely on noisy means.
- None of the strategies beat NIFTY 50 out of sample over 2025–26. The fixed five-stock portfolio underperformed the index by about 11.6 percentage points a year whichever weighting was used, which says more about stock selection than about weighting.
- Effective N ≤ 5 across all strategies; there is no broad-market diversification.

## 4. Limitations and open issues

1. **Data quality.** Dataset 1 is unverified Yahoo data with an unconfirmed price basis, and gives price returns only. An unadjusted split or bonus would distort μ, Σ and drawdowns. The NSE corporate-action file is still needed.
2. **Estimation error.** It dominates expected returns: the standard error is about 12–16% per year. Max-Sharpe is undefined at rf 6%. The rf 0% variant is a sensitivity, not a target.
3. **Fixed universe.** A five-stock, four-sector universe chosen today brings survivorship/selection bias, cannot show diversification beyond these names, and leaves sector constraints with little room.
4. **Short walk-forward.** It covers about 1.7 years, a single regime, with no significance testing. The max-Sharpe walk-forward is mostly "hold prior weights" because it is undefined in 12 of 21 windows.
5. **Costs.** The rates are assumptions to verify. Taxes on capital gains, market impact and bid-ask spread are not modelled.
6. **Not done (out of scope or deferred):** cost-aware objective (costs are evaluated, not optimized); cardinality constraints; robust/Black-Litterman returns; dashboard display (Conversation 6).
7. **Branches are stacked and unmerged:** conv5 → conv4 → conv3 → conv2. Carried-forward user decisions: the long Angel pull, the NSE corporate-action file, the constituent-weights source, the macro series, and confirmation of the cost rates and constraints.

## 5. Files

- **New:**
  - `config/optimization.json`
  - `portfoliopulse/expected_returns.py`, `optimization.py`, `portfolio_allocation.py`, `portfolio_evaluation.py`, `optimization_backtest.py`, `optimization_analysis.py`
  - `scripts/analyze_optimization.py`
  - `tests/test_optimization.py`, `tests/test_portfolio_construction.py`, `tests/test_optimization_analysis.py`
  - this file
- **Changed:**
  - `portfoliopulse/migrations.py` (migration 4, additive)
  - `portfoliopulse/database.py` (`save_optimization_runs`, `list_optimization_runs`)
  - `README.md`, `CLAUDE.md`
- **Unchanged:** every existing module's behaviour, CLI argument, metric key, and the holdings data and config.

## 6. Structured outputs for Conversation 6

`scripts/analyze_optimization.py` writes the following to `data/processed/`:
- `optimization_dataset_<id>_run_<n>_report.json`: strategies, comparison, allocations, frontier, walk-forward summary, trade-offs, settings, limitations;
- `_comparison.csv`;
- `_weights.csv` (strategy × symbol, with component risk);
- `_frontier.csv`;
- `_walk_forward.csv` (daily net returns per strategy).

In the database: `optimization_runs` (`metrics_json`, `diagnostics_json`), `optimization_weights` (target, current, integer shares) and `optimization_backtests` (daily gross/net/turnover/cost).

## 7. Conversation 6 kickoff: Dashboard & Reporting (roadmap Phase 7)

**Read first:**
- `CLAUDE.md` §13;
- `docs/HANDOFF_CONV3.md`, `HANDOFF_CONV4.md` and this file;
- roadmap §5 (dashboard pages) and §9;
- `docs/PHASE_6_DASHBOARD.md`;
- `app.py`.

**Scope:**
- Presentation only. Read the persisted runs: `phase3_risk_v1` (run 9), `phase4_stress_v1` (run 10 + scenario runs 3–18), `phase5_opt_v1` (run 11 + optimization tables).
- Use the JSON/CSV outputs; never recompute financial logic in `app.py`. Keep `app.py` thin, with builders in `portfoliopulse/ui/`.

**Must show:**
- source, verification status, price basis, N and the k tail observations;
- "illustrative / in-sample / hypothetical / not TRI" labels;
- undefined and unavailable states, with their reasons (e.g. max-Sharpe undefined, COVID unavailable).

**Invariants:**
- 250 tests / 71 subtests baseline; schema v4;
- no writes from the UI except existing workflows;
- all stored runs unchanged (fingerprint script: `.tmp/conv3/db_fingerprint_conv4.py <db> 11 18`, plus the `optimization_*` tables).
