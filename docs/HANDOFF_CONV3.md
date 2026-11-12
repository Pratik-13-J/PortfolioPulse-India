# Handoff — Conversation 3 (2026-11-12): Advanced Risk Engine (roadmap Phase 3)

Scope: roadmap Phase 3 only. No stress testing (Phase 4), optimization (Phase 5) or dashboard work was done. Branch `conv3-risk-engine`, created from `63b25ed` (tip of `conv2-foundation-performance-attribution`). One commit per milestone; nothing pushed or merged.

| Milestone | Commit | Summary |
|---|---|---|
| 1 SciPy pin | `6607bed` | `scipy==1.18.1` added to `requirements.txt` |
| 2 VaR models | `f647be7` | `var_models.py`: normal, Cornish-Fisher, Monte Carlo (MVN + bootstrap), delta-normal component VaR/ES |
| 3 Backtests | `0b31722` | `var_backtests.py`: rolling forecasts, Kupiec POF, Christoffersen independence, conditional coverage |
| 4 Drawdowns, covariance | `16de268` | `drawdowns.py`, `covariance_estimators.py` (sample, Ledoit-Wolf, EWMA λ = 0.94) |
| 5 Orchestration | `7da37d9` | `risk_analysis.py` (engine `phase3_risk_v1`), `scripts/analyze_risk.py` |
| docs | (this commit) | README, this file, `CLAUDE.md` §13 |

## 1. Verification results (actually run)

- **Baseline:** 146 passed / 71 subtests on `63b25ed` with SciPy installed, before any risk code.
- **Per milestone:** 146 → 163 → 173 → 184 → 192 passed, 71 subtests each time. No existing test was removed or edited.
- **Final suite:** `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` → **192 passed, 71 subtests passed**.
- **Dependency:** `scipy==1.18.1` (cp312 win_amd64 wheel). Installing it changed no other package; numpy 2.5.4 and pandas 3.0.6 unchanged; `pip check` clean. `requirements-dev.txt` inherits it via `-r requirements.txt`.
- **DB bit-identity:**
  - Before any change: a hex-float, ordered, per-table SHA-256 fingerprint of all 22 tables (TOTAL `7cd54b12…c0fd`) and an in-memory hex dump of dataset 1 and 2 Phase-4 metrics (20 keys each), `phase8_perf_v1` scalars and risk contributions (105 lines, SHA-256 `30682ce6…3fc9`).
  - Trial on a copy (`.tmp/conv3/trial.db`): wrote run 9; every pre-existing row (analysis IDs ≤ 8 and all other tables) fingerprinted identical; integrity `ok`; `foreign_key_check` empty.
  - Real DB: backup `data/backups/portfoliopulse_pre_conv3_risk_20261112T165943068479Z.db` (`migrations.backup_database`), then `scripts.analyze_risk --dataset-id 1` → **analysis run 9**, 305 result rows. Fingerprint of all pre-existing rows identical to the baseline; the Phase-4/perf hex dump identical; integrity `ok`; FK check empty. Datasets 1 and 2 (candles, holdings, provenance) unchanged.
- **Dataset 2:** smoke-tested with `--no-save` only (no DB write).
- **Schema:** unchanged (still v3). The new run is a separate `analysis_metadata` row, so no migration was needed.

## 2. Methodology (one-day horizon; decimal loss fraction of portfolio value; INR at the risk-weight-date value)

Labels: "price return vs NIFTY 50 price index (not TRI)"; in-sample statistics, not forecasts or loss limits; no √h scaling applied (it would assume iid returns).

Two sample bases are kept apart and named in every row (`sample_basis`):
- `realized_portfolio`: the realized buy-and-hold (drifting-weight) daily portfolio returns, the same series Phase 4 uses. Used by historical, normal, Cornish-Fisher, backtests and drawdowns. NIFTY 50 gets the same three univariate methods (`benchmark_index`).
- `current_weight`: asset daily returns revalued at the fixed quantities held on the risk-weight date. Used by Monte Carlo, delta-normal and component VaR/ES.

With α = 1 − c and z = Φ⁻¹(α):
- **Historical:** reuses `risk_metrics.historical_var_es` (k = max(1, ⌈αN⌉)). Not re-implemented.
- **Normal:** VaR = −(μ + zσ), ES = −μ + σφ(z)/α, sample mean, σ with ddof = 1.
- **Cornish-Fisher:** z_cf = z + (z²−1)S/6 + (z³−3z)K/24 − (2z³−5z)S²/36, with moment skewness S and excess kurtosis K. VaR = −(μ + z_cf σ). ES is the tail average of the CF quantile, in closed form: −μ + σφ(z)/α·[1 + zS/6 + (z²−1)K/24 + (1−2z²)S²/36] (checked against numerical quadrature). `tail_monotone` flags whether the expansion is increasing on [−6, z].
- **Monte Carlo:** seeded `numpy.random.default_rng`; MVN uses the sample mean and covariance (Cholesky), bootstrap draws historical dates iid (keeps cross-sectional dependence, drops serial dependence, cannot exceed the worst history). The portfolio return is Σ V_i R_i / Σ V_i (fixed quantities). VaR/ES use the same nearest-rank estimator. SE = batch means (std of B batch estimates / √B). Seed, paths, batches and the negative-price path count are recorded.
- **Delta-normal and components (Euler):** σ_p = √(w′Σw); component VaR_i = w_i(−m_i − z(Σw)_i/σ_p); component ES_i = w_i(−m_i + φ(z)/α·(Σw)_i/σ_p). These sum exactly to the totals. Bootstrap component ES is the negative mean holding contribution over the same k worst paths, so it sums to the MC ES.
- **Covariance:** sample (ddof = 1); Ledoit-Wolf 2004 shrinkage toward a scaled identity (1/N normalisation, checked against a loop transcription of the paper); zero-mean RiskMetrics EWMA λ = 0.94 with normalised finite weights. Delta-normal uses the sample mean with sample and LW, and zero mean with EWMA.
- **Backtests:** each one-day-ahead historical or normal VaR uses only the trailing 250 returns; an exception is r_t < −VaR_t. Kupiec LR_pof ~ χ²(1); Christoffersen LR_ind ~ χ²(1); LR_cc = sum ~ χ²(2), with 0·ln 0 = 0. The p-values are asymptotic.
- **Drawdowns:** episodes run from peak → trough → first return to ≥ peak. The output gives sessions and calendar days to trough, to recovery, and from trough to recovery. The first observed level is the starting peak, so depth matches Phase 4's `_observed_max_drawdown` exactly. Sessions are dataset dates, not validated against an NSE calendar. Unrecovered episodes run to the last date and are flagged.

**Persistence:** a new `analysis_metadata` row with `settings_json.engine_version = "phase3_risk_v1"`, `stage = phase3_advanced_risk_engine`, method strings, MC seed/paths/batches, backtest window, λ, and the source / verification status / provenance status / price basis. `quality_json` holds N, the k tail observations at each confidence for the portfolio and NIFTY 50, CF monotonicity, MC details, exception dates and transitions, drawdown episodes and undefined reasons. Result keys use a colon namespace, for example `var_1d:normal:c99`, `es_1d_inr:monte_carlo_bootstrap:c95`, `var_1d_standard_error:monte_carlo_multivariate_normal:c99`, `benchmark_var_1d:cornish_fisher:c99`, `backtest_kupiec_p_value:historical:c99`, `drawdown_max_duration_observations:portfolio`, `component_var_1d:delta_normal_ledoit_wolf:c99:INFY`, `current_weight_annualized_volatility:ewma`. A test asserts that none of these keys overlap the Phase 4 / `phase8_perf_v1` keys.

## 3. Dataset 1 results (run 9)

Source: Yahoo Finance via yfinance 1.7.0; `user_declared_unverified`; `unadjusted_ohlcv` (basis unconfirmed). N = 686 valid returns, 2024-01-02 → 2026-10-15. Risk-weight date 2026-10-15; portfolio value ₹119,198.75.

| Method (portfolio) | 95% VaR | 95% ES | 99% VaR | 99% ES |
|---|---|---|---|---|
| Historical (realized) | 1.359% | 1.915% | 2.127% | 2.868% |
| Normal (realized) | 1.442% | 1.804% | 2.032% | 2.325% |
| Cornish-Fisher (realized; S = 0.002, K = 3.20) | 1.385% | 2.209% | 2.678% | 3.681% |
| MC MVN, 100k paths, seed 20261112 (SE) | 1.445% (0.005%) | 1.807% | 2.034% (0.011%) | 2.335% |
| MC bootstrap (SE) | 1.367% (0.008%) | 1.939% | 2.191% (0.014%) | 2.945% |
| Delta-normal sample | 1.442% | 1.808% | 2.038% | 2.334% |
| Delta-normal Ledoit-Wolf (δ = 0.055) | 1.422% | 1.783% | 2.010% | 2.302% |
| Delta-normal EWMA λ = 0.94 | 1.813% | 2.274% | 2.565% | 2.938% |

- **99% historical tail (k = 7):** 2024-06-04 −5.018%, 2026-03-19 −3.045%, 2025-04-07 −2.923%, 2024-08-05 −2.488%, 2026-03-27 −2.253%, 2026-04-24 −2.222%, 2025-04-01 −2.127%.
- **Backtests (436 forecasts, 250-day window):**
  - historical 95%: 33 exceptions vs 21.8 expected (Kupiec p 0.022, rejects at 5%; Christoffersen p 0.340);
  - historical 99%: 5 vs 4.4 (p 0.763 / 0.733);
  - normal 95%: 24 vs 21.8 (p 0.634 / 0.178);
  - normal 99%: 7 vs 4.4 (p 0.243 / 0.632).
- **Drawdown:** max −29.51%, peaking 2024-09-26 with the trough on 2026-10-08. It is **unrecovered**: 508 sessions / 749 calendar days underwater at the end of the sample. The current drawdown is −26.07%, and there are 14 episodes.
- **Current-weight annualized volatility:** sample 13.88%, LW 13.69%, EWMA 17.50%.
- **Context:** the EWMA figures are well above sample because recent volatility is elevated. Expect this to shift with the window.

Dataset 2 (Angel One, `verified_live`, N = 10) runs end to end. It is flagged `insufficient_sample_smoke_test_only` and its backtests are recorded as undefined; it was not persisted.

## 4. Open issues

1. **Dataset 1 is unverified and its price basis is unconfirmed.** The −29.5% unrecovered drawdown and the tail dates depend on it. An NSE corporate-action file is still needed: an unadjusted split or bonus would create false tail losses.
2. **MC standard errors are batch-means estimates.** Bootstrap SEs collapse to ~0 when the sample is tiny (dataset 2), because every batch picks the same historical value. This is reported as-is.
3. **Cornish-Fisher reliability on short samples.** On dataset 2, CF ES < CF VaR at 99%, which is a sign the expansion is unreliable for N = 10. The monotonicity flag is stored. On dataset 1 all flags are `True`.
4. **Backtests are in-sample over one 686-day history** with asymptotic χ² p-values and few 99% exceptions. They are indicative only.
5. Carried forward (user decisions): the long Angel pull (HANDOFF_CONV2 §6.4), the NSE corporate-action file, and the NIFTY 50 constituent-weights source.
6. **Not done (out of scope):** dashboard display of Phase 3 outputs; multi-day horizons; GARCH/filtered historical simulation.

## 5. Files

- **New:**
  - `portfoliopulse/var_models.py`, `portfoliopulse/var_backtests.py`, `portfoliopulse/drawdowns.py`, `portfoliopulse/covariance_estimators.py`, `portfoliopulse/risk_analysis.py`
  - `scripts/analyze_risk.py`
  - `tests/test_phase3_var_models.py` (17), `tests/test_phase3_backtests.py` (10), `tests/test_phase3_drawdowns_covariance.py` (11), `tests/test_phase3_risk_analysis.py` (8)
  - this file
- **Changed:** `requirements.txt` (SciPy pin), `README.md`, `CLAUDE.md`.
- **Unchanged:** every existing module, CLI argument, metric key, and the schema.

## 6. Conversation 4 kickoff: Stress Testing and Scenarios (roadmap Phase 4)

**Read first:**
- `CLAUDE.md` §13;
- this file;
- roadmap §5 Phase 4 and §9;
- `docs/PHASE_5_STRESS_TESTS.md`, which covers the existing `scenarios.py` / `attribution.py` / `scenario_analysis.py`.

**Scope:** roadmap Phase 4 only. No optimization (Phase 5) and no dashboard work unless approved.

**Reuse, don't duplicate:**
- `scenario_attribution` (holding impacts reconcile to the total);
- `var_models.delta_normal_components` and `covariance_estimators` for any covariance-based or historical-episode stress;
- `risk_analysis` for labelled persistence patterns.

**Invariants:**
- 192 tests / 71 subtests baseline; no test removed;
- Phase 4, `phase8_perf_v1` and `phase3_risk_v1` keys unchanged; new outputs go under a new `engine_version`;
- `Phase4Analysis.metrics` keeps exactly 20 keys;
- schema changes only as a new numbered migration (currently v3), backup first;
- verify all pre-existing rows with the hex-float fingerprint. The scripts live in `.tmp/conv3/`; recreate them if `.tmp` was cleaned. Restrict to analysis IDs ≤ 9.

**Data:**
- dataset 1 is the only long sample (label it `user_declared_unverified`);
- dataset 2 is for smoke tests only;
- never mix providers in one series.

**Labels:** scenarios are hypothetical one-period shocks, not forecasts.

**Pending user decisions:**
- the long Angel pull;
- the NSE corporate-action file (needed before treating dataset 1 tail and drawdown dates as real events);
- the constituent weights source;
- any RBI/FBIL macro series for macro stress (roadmap §10.4).
