# Handoff — Conversation 4 (2026-11-12): Portfolio Stress Testing (roadmap Phase 4)

Scope: roadmap Phase 4 only. No optimization (Phase 5) and no dashboard work. Branch `conv4-stress-testing`, created from `4892ec1` (tip of `conv3-risk-engine`, which is itself unmerged). Nothing has been pushed or merged.

| Commit | Summary |
|---|---|
| `24a1c30` | Scenario library, OLS sensitivities, shock-vector propagation, historical event replay, impact and risk integration (pure modules plus unit tests) |
| `1cb0bb0` | `stress_analysis.py` orchestration (engine `phase4_stress_v1`), `scripts/analyze_stress.py`, integration tests, `database.list_scenario_runs` |
| (docs commit) | README, this file, `CLAUDE.md` |

"Phase 4" here is the **roadmap** phase. It is unrelated to the older `Phase4Analysis` (portfolio analytics), whose engine is `phase8_perf_v1`.

## 1. Verification (actually run)

- **Tests:** 192 passed / 71 subtests at the start. After the pure modules: 212 / 71. **Final: 219 passed, 71 subtests passed.** No existing test was removed or edited. There are 27 new tests: `tests/test_stress_units.py` (20) and `tests/test_stress_analysis.py` (7).
- **Dependencies:** none added; SciPy 1.18.1 is from Conv 3.
- **Schema:** unchanged (v3). New rows use the existing `analysis_metadata`/`analysis_results` and `scenario_runs`/`scenario_contributions` tables with new `scenario_type` values. `hypothetical = 1` holds for every row.
- **DB:**
  - Pre-change fingerprint: hex-float, ordered SHA-256 per table, covering analysis IDs ≤ 9 and scenario runs ≤ 2.
  - Trial on a copy, then backup `data/backups/portfoliopulse_pre_conv4_stress_20261112T171648238607Z.db`, then the real run.
  - Result: **analysis run 10** (182 results) and **scenario runs 3–18** (16 runs, 80 contribution rows).
  - Running it a second time reused run 10 and inserted no rows (idempotent).
  - All pre-existing rows are bit-identical. `candle_datasets`, `daily_candles`, `dataset_holdings` and `schema_version` hashes are unchanged. The dataset 1 and 2 Phase-4/perf hex dump is unchanged.
  - Integrity check `ok`; FK check empty.
- **Dataset 2:** smoke-tested with `--no-save` only. It now carries an explicit small-sample warning (N = 10).

## 2. Features and methodology

**Shock components.** A `ShockVector` stores each holding's shock as additive components, so direct and model-estimated moves are never mixed silently. Each holding's `shock_basis` is the `+`-joined list of its non-zero components, or `unshocked`. A total shock below −100% is rejected.

| Component | Meaning |
|---|---|
| `direct` | A stated holding or sector shock. Direct presets reuse `scenarios.scenario_shocks`. |
| `beta_propagated` | β_i × NIFTY 50 move. β is single-factor OLS on dataset daily returns. It equals `performance_metrics.beta`; SE and R² are reported. |
| `covariance_propagated` | E[r_U \| r_S = s] = Σ_US Σ_SS⁻¹ s (zero-mean). Uses the joint asset+NIFTY covariance from `covariance_estimators`: sample by default, Ledoit-Wolf or EWMA via `--covariance`. Conditioning on NIFTY alone reproduces OLS β exactly (tested). |
| `factor` | Rate / USD/INR sensitivity × factor move, with the market held at zero. The basis is `assumed` (illustrative sector values in config) or `estimated` (OLS on a source-labelled factor CSV). |
| `historical_observed` | A holding's own observed close-to-close return over an event window. |

**Scenario library.** `config/stress_scenarios.json` is `stress_library_v1`; its SHA-256 is stored with every run.

| Kind | Scenarios |
|---|---|
| Market shocks | NIFTY −10% and −20% (beta) |
| Direct broad | Every holding −10% |
| Single stock | ICICIBANK −20% (isolated, and covariance-propagated); INFY −20% (covariance) |
| Sector | Banking −15%, IT −15% |
| Combined | NIFTY −10% (beta) + Banking −10% overlay |
| Macro | 10Y yield +100 bp; USD/INR +5%; both together |

**Historical events** (`historical_stress.py`):
- **Anchors:** the last observed benchmark closes on or before the start and end dates. The data must cover both ends, so a partial window is never used.
- **Missing closes:** a holding missing a close at an anchor is beta-propagated with a warning. Nothing is filled.
- **Library windows:**
  - COVID-19 crash, 2020-01-14 → 2020-03-23;
  - 2022 inflation/rate shock, 2022-01-17 → 2022-06-17;
  - election result day, 2024-06-04;
  - 5 August 2024 risk-off;
  - 7 April 2025 tariff shock.
  - These dates are analyst-defined approximations and are labelled "verify against NSE Indices".
- **Data-identified events:** the dataset's deepest NIFTY drawdown episode and its worst valid NIFTY day. An event whose window duplicates a library window is skipped, with a warning.
- **Uncovered windows:** they are `unavailable` with a reason. If an NSE Indices NIFTY 50 CSV is supplied (`--nse-reference-csv`), the event becomes `model_propagated` (β × index move). No index move is ever filled in from memory.
- **Event path drawdown:** `path_max_drawdown` prices the current quantities at event-window prices, on dates where every holding is observed. Its weights are therefore event-time weights, so it differs slightly from `impact_pct`, which applies event returns to the as-of values.

**Impact** (`stress_impact.py`):
- impact_i = V_i × shock_i, computed with `attribution.scenario_attribution`, which reconciles to the total;
- percentage loss, sector impact (sum by stored sector), loss share = impact_i / total (sums to 1);
- worst holdings (top 3 by INR), stressed values and stressed weights.

**Risk integration** (scale comparisons only; a one-period stress is not a one-day statistic):
- loss / VaR and loss / ES for historical, normal, Cornish-Fisher, MC bootstrap and delta-normal at 95% and 99%, taken from the Phase 3 engine `compute_risk_analysis`, with exceedance flags;
- loss / |observed max drawdown|;
- delta-normal 99% component-VaR shares, and current-weight volatility contributions (`risk_metrics.volatility_contributions`), before and after re-marking to stressed weights;
- post-stress allocation drift and concentration (`attribution.allocation_alerts`);
- beta-implied portfolio move Σ w_i β_i × benchmark move.

**Incomplete sectors:** a blank or missing sector becomes `Unclassified`, with a warning. It receives no sector overlay and uses the unclassified macro fallback. A sector target that doesn't exist is rejected; for library scenarios it is recorded as unavailable.

**Persistence:**
- `analysis_metadata`, engine `phase4_stress_v1`:
  - `settings_json`: library version/SHA, β and covariance methods, macro basis, risk-engine reference, labels and provenance;
  - `quality_json`: warnings, unavailable scenarios with reasons, event anchors/status, sector labels.
- Result keys include `stress_impact_inr:<id>`, `stress_impact_pct:<id>`, `stress_benchmark_move:<id>`, `stress_sector_impact_inr:<id>:<sector>`, `stress_loss_to_historical_var_99:<id>`, `stress_loss_to_historical_es_99:<id>`, `stress_loss_to_max_drawdown:<id>`, `stress_beta[_standard_error|_r_squared]:<symbol>`, `stress_macro_sensitivity:<basis>:<factor>:<symbol>` and `stress_current_weight_beta`.
- Each scenario also gets a `scenario_runs` row:
  - `settings_json`: per-holding components and basis, assumptions, sector impact, worst holdings, warnings;
  - `alerts_json`: risk integration;
  - plus `scenario_contributions` rows.
- `scenario_type` values: `beta_market`, `direct_broad_market`, `direct_individual`, `direct_sector`, `covariance_individual`, `combined_market_sector`, `macro_assumed`, `macro_estimated`, `historical_replay`, `historical_model_propagated`.

**Structured outputs for Conversation 6** (`scripts/analyze_stress.py`; `--no-save` skips the DB write): `data/processed/stress_dataset_<id>_run_<n>_report.json`, plus `_comparison.csv` (the scenario matrix), `_holdings.csv` (long: scenario × symbol) and `_sectors.csv` (long: scenario × sector).

## 3. Dataset 1 results (run 10; as of 2026-10-15; ₹119,198.75)

Source: Yahoo, `user_declared_unverified`, `unadjusted_ohlcv`. Price return vs NIFTY 50 price index (not TRI). These are hypothetical revaluations, not forecasts.

**Betas** (SE): RELIANCE 1.101 (0.045), HDFCBANK 1.053 (0.043), ICICIBANK 0.921 (0.042), INFY 0.833 (0.068), ITC 0.661 (0.050).

| Scenario | Loss | INR | Loss / hist VaR99 | Loss / hist ES99 | Loss / max DD |
|---|---|---|---|---|---|
| NIFTY −10% (beta) | 9.18% | −10,945 | 4.32 | 3.20 | 0.31 |
| NIFTY −20% (beta) | 18.36% | −21,890 | 8.63 | 6.40 | 0.62 |
| Every holding −10% (direct) | 10.00% | −11,920 | 4.70 | 3.49 | 0.34 |
| ICICIBANK −20% isolated / covariance | 5.83% / 10.48% | −6,947 / −12,492 | 2.74 / 4.93 | | |
| Banking −15% / IT −15% (direct) | 6.66% / 2.79% | −7,936 / −3,330 | | | |
| NIFTY −10% + Banking −10% | 13.62% | −16,236 | 6.40 | 4.75 | 0.46 |
| Yield +100 bp (assumed) | 2.62% | −3,121 | | | |
| USD/INR +5% (assumed) | 0.09% | −113 | | | |
| Election day 2024-06-04 (replay) | 5.36% | −6,390 | 2.52 | 1.87 | |
| 5 Aug 2024 (replay) | 2.50% | −2,984 | | | |
| 7 Apr 2025 (replay) | 3.05% | −3,641 | | | |
| Worst NIFTY drawdown 2024-09-26 → 2025-03-04 (data-identified; NIFTY −15.77%) | 13.46% | −16,044 | 6.33 | 4.69 | 0.46 |
| COVID 2020 / 2022 shock | unavailable: dataset begins 2024-01-02; no reference supplied | | | | |

**Notes:**
- In the combined scenario, Banking is 64.1% of the loss.
- The −10% beta-propagated loss is less than the −10% direct loss because the current-weight beta is about 0.92.
- Delta-normal 99% VaR barely moves after re-marking (2.038% → 2.038%).

## 4. Limitations and open issues

1. **2020 and 2022 events cannot be replayed** from stored data. The fix is a user-supplied NIFTY 50 history CSV from NSE Indices (gives `model_propagated` results), or importing an earlier dataset (gives true replays).
2. **No rate or FX series exists.** The macro scenarios use **illustrative assumed** sector sensitivities that are not estimated from data; replace or confirm them in `config/stress_scenarios.json`, or supply a source-labelled factor CSV (`date, india_10y_yield_pct, usdinr` plus a `.meta.json` sidecar) to use OLS estimates. The RBI/FBIL series choice is still open (roadmap §10.4).
3. **All betas, covariances and propagation are in-sample.** They come from 686 unverified Yahoo returns with an unconfirmed price basis. Model-propagated events use betas from 2024–2026, not from the event window.
4. **Event dates and replays:**
   - The library event dates are approximate and unverified. The data-identified episodes depend on dataset 1's own closes.
   - Replays apply past moves to today's holdings; they do not reconstruct past P&L.
   - Dataset dates are not validated against an NSE trading calendar.
5. **No independent stress-scenario `scenario_definitions` or `macro_series` tables** (roadmap §5, Phase 4 DB line). The versioned library file plus its SHA-256 in `settings_json` replaces `scenario_definitions` for now. A `macro_series` table needs a real data source first.
6. **Branches are stacked:** `conv4-stress-testing` → `conv3-risk-engine` → `conv2-foundation-performance-attribution`, all unmerged. Carried-forward user decisions: the long Angel pull, the NSE corporate-action file, the constituent-weights source, and the macro series.

## 5. Files

- **New:**
  - `config/stress_scenarios.json`
  - `portfoliopulse/factor_sensitivities.py`, `stress_scenarios.py`, `historical_stress.py`, `stress_impact.py`, `stress_analysis.py`
  - `scripts/analyze_stress.py`
  - `tests/test_stress_units.py`, `tests/test_stress_analysis.py`
  - this file
- **Changed:**
  - `portfoliopulse/database.py` (new read-only `list_scenario_runs`; nothing else touched)
  - `README.md`, `CLAUDE.md`
- **Unchanged:** `scenarios.py`, `attribution.py`, `scenario_analysis.py`, `scripts/analyze_phase5.py`, every existing CLI argument, metric key and the schema.

## 6. Conversation 5 kickoff: Portfolio Construction & Optimization (roadmap Phase 5)

**Read first:**
- `CLAUDE.md` §13;
- this file;
- `docs/HANDOFF_CONV3.md`;
- roadmap §5 Phase 5 and §9.

**Scope:** Phase 5 only. No dashboard.

**Reuse, don't duplicate:**
- `covariance_estimators`, the sample mean, and `var_models.delta_normal_components` for risk contributions;
- `stress_analysis.compute_stress_analysis` to stress-test candidate portfolios;
- `attribution.allocation_alerts`.

**Invariants:**
- 219 tests / 71 subtests baseline; no test removed;
- Phase 4 analytics, `phase8_perf_v1`, `phase3_risk_v1` and `phase4_stress_v1` keys and rows unchanged; new outputs under a new engine version;
- `Phase4Analysis.metrics` keeps exactly 20 keys;
- schema changes only as a new numbered migration (currently v3), backup first;
- fingerprint all pre-existing rows. The scripts are in `.tmp/conv3/`; use `db_fingerprint_conv4.py <db> 10 18`.

**Data and labelling:**
- dataset 1 is the only long sample (label it unverified); dataset 2 is for smoke tests only;
- optimized weights are illustrative, in-sample and not recommendations;
- the existing quantities and holdings are never overwritten.

**Pending user decisions:**
- the SciPy `optimize` usage is covered by the existing pin, but confirm any new dependency (e.g. cvxpy) before adding it;
- the constraints set (long-only, position caps, sector caps);
- the decisions carried forward from earlier conversations.
