# Handoff — Conversation 2 (2026-11-12)

Scope: roadmap Phase 1 (foundation and data correctness), Phase 2 (relative performance) and the Phase 6 framework (gated benchmark attribution). Branch: `conv2-foundation-performance-attribution`, one commit per phase. Nothing has been pushed.

| Phase | Commit | Summary |
|---|---|---|
| 1 | `a4b17a1` | Versioned migrations, provenance ledger, warn-only jump check, reference importers, Angel 403 diagnostic, provider protocol |
| 2 | `aeb251a` | `performance_metrics.py`, integrated under `engine_version = phase8_perf_v1` |
| 6 (framework) | `b4a46c6` | `benchmark_attribution.py`: Active Share, Brinson-Fachler, Carino linking, data gate |
| docs | `e216e55` | README, this handoff, `CLAUDE.md` handoff section |
| live validation | (addendum commit) | Angel One live diagnostic + 15-day live pull -> dataset 2; docs, roadmap, Conv 3 kickoff (sections 6-7) |

## 1. Verification results (actually run)

- **Baseline before any change:** HEAD `9e67316` exported to `.tmp/baseline` gave **91 passed, 57 subtests passed**. This re-confirms the audit figure on this machine.
- **Final suite:** `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` gave **146 passed, 71 subtests passed**. No existing test was removed or edited.
- **Environment:** `.venv` did not exist, so it was created from the pinned `requirements-dev.txt` (Python 3.12.10). `pip check` was clean and no pins changed.
- **DB backup:** `data/backups/portfoliopulse_pre_conv2_20261112T154115Z.db` was taken with the SQLite backup API; `integrity_check` = ok. The migration runner also took `portfoliopulse_pre_migration_20261112T155214534843Z.db`. `data/backups/` is now git-ignored.
- **Migration on a copy first:** I migrated a copy (`.tmp/migrated.db`) to version 3 and then re-ran it to confirm it was idempotent.
  - Integrity check: `ok`.
  - `foreign_key_check`: empty.
  - Dataset 1 Phase-4 metrics were recomputed (rf 0.06, 95%), along with the risk contributions. Both match the baseline code's output **bit for bit**: all 20 metrics plus the contributions were compared as hex floats.
  - All 236 stored `analysis_results` rows and the 10 `scenario_contributions` rows are byte-identical.
- **Real DB:** `data/portfoliopulse.db` was then migrated 0 → 3.
  - The same comparison came out identical, both straight after the migration and after the full test suite.
  - Row counts are unchanged: 1 dataset, 4,133 candles, 7 analyses, 236 results, 2 scenario runs, 6 holdings snapshot rows.
  - No new analysis run was written to the real DB in this conversation.
- **Angel One:** the offline diagnostic was run first; the user then ran the live diagnostic locally, and the controlled live pull followed. Both are described in §6.

## 2. What changed

### Phase 1
- `portfoliopulse/migrations.py` (new) adds a `schema_version` table and numbered migrations. Each migration runs in its own transaction and FK-checks before commit. Migrations are idempotent, and a backup is taken first whenever `candle_datasets` has rows.
  - **v1:** `reference_datasets`, `corporate_actions`, `dataset_provenance_events`.
  - **v2:** a 12-step rebuild of `candle_datasets` that widens `price_basis` to allow `split_adjusted_ohlcv` and adds `verification_status`. The ladder is `verified_live`, `verified_reference_sample`, `user_declared_unverified`, `fallback_unverified`, `simulated`.
    - The backfill is conservative: only an Angel One `broker_response_validated` dataset can become `verified_live`.
    - The backfill is recorded as `migration_backfill` events. Dataset 1 is `user_declared_unverified`.
  - **v3:** `benchmark_constituents` and `security_classifications`, which belong to Phase 6. They were landed with v1/v2 because they share the runner.
  - `schema.sql` is frozen as the version-0 baseline and now carries a header comment saying so.
  - The migration refuses an unexpected column layout and rolls back cleanly (tested).
- `database.py` changes:
  - `initialize()` now calls `run_migrations()`.
  - Imports set `verification_status` via `derive_verification_status`.
  - New helpers: `list_provenance_events`, `record_provenance_note`, `relabel_price_basis()` and `set_verification_status()`.
    - `relabel_price_basis()` must cite an imported `corporate_actions` reference dataset plus evidence. It never rewrites candles, and the original declaration stays in `metadata_json`.
    - `set_verification_status()` reserves `verified_live` for Angel One.
- `data_quality.py` adds `corporate_action_jump_warnings()`, a warn-only check for |r| > 35% (configurable) with matching to nearby ex-dates. It also adds `unexplained_action_dates()`, which lists "quiet" split/bonus ex-dates as evidence of provider adjustment. `PRICE_BASES` includes `split_adjusted_ohlcv` (also in `sourced_csv.py`), and `app.py` has a display label for it.
- `reference_data.py` (new) imports CSVs that come with a JSON sidecar. The sidecar must declare `source_name`, an HTTPS `source_url`, a UTC `retrieved_at_utc`, `provenance_label` and `file_sha256`. The digest is recomputed on import, and `verified_live` cannot be self-declared.
  - Corporate actions: splits and bonuses need ratios.
  - Benchmark constituents: weights must sum to 1 ± 1e-4, or be absent with `weight_basis = 'none'`. Weights are never filled.
  - Dated security classifications: history is never overwritten.
- `angelone/diagnostics.py` and `scripts/diagnose_angelone.py` (new) produce the sanitized §3 checklist report. It is built from a whitelist only: booleans, status codes, hosts, routes, header names, `ABxxxx` codes and fixed labels.
  - It never reads credential values; the presence check uses key membership only.
  - Live mode authenticates through the existing `authenticate_from_environment()`.
- `providers/base.py` (new) defines `DailyCandleProvider`, `ProvenanceRecord` and thin adapters over the existing Angel One, sourced-CSV and Yahoo modules. `require_explicit_provider()` never substitutes one provider for another.
- `tests/conftest.py` creates `.tmp`.

### Phase 2
- `performance_metrics.py` (new, pure). Formulas are in the module docstring.
  - TE = std(a, ddof=1)·√252.
  - IR = mean(a)·252/TE.
  - β and downside β (days with r_b < 0): sample covariance over sample variance.
  - Up/down capture: geometric mean ratios.
  - Sortino as specified in the roadmap.
  - Rolling 63/126/252-observation TE, β and returns.
  - Period returns (1m/3m/6m/ytd/1y): anchored on the last common observed date on or before the calendar offset.
  - Security contributions: (H_t − H_{t−1})/V_{t−1}, Carino-linked.
  - Undefined values are returned as `None`, with the reason recorded.
- `portfolio_analysis.py`:
  - `Phase4Analysis.metrics` keeps exactly the original 20 keys and values.
  - Phase 2 results are in the new `performance` field, with jump warnings in `jump_warnings`.
  - `analyze_saved_dataset` persists the new keys under `engine_version = phase8_perf_v1`, with method labels and `comparison_label = "price return vs NIFTY 50 price index (not TRI)"`.
  - It raises if a new key would collide with an existing one.
  - The 7 earlier analysis rows are untouched; re-running on dataset 1 creates a new analysis ID because the settings changed.
- `scripts/analyze_phase4.py`: the report includes `engine_version`, a `performance` block and jump warnings. CLI arguments are unchanged.
- **New metric keys:**
  - Relative: `portfolio_tracking_error_annualized`, `portfolio_active_return_annualized`, `portfolio_information_ratio`, `portfolio_beta`, `portfolio_downside_beta`, `portfolio_up_capture_ratio`, `portfolio_down_capture_ratio`.
  - Sortino: `portfolio_sortino`, `benchmark_sortino`.
  - Rolling: `rolling_{63,126,252}d_{tracking_error,beta}_latest`.
  - Period returns: `{portfolio,benchmark}_period_return_{1m,3m,6m,ytd,1y}`.
  - Contributions: `contribution_carino:<SYM>`, `contribution_arithmetic:<SYM>`, `contribution_total_compounded_return`.
- **Dataset 1 (read-only computation, rf 6%):**
  - TE 6.36%, IR −1.17, β 0.902, downside β 0.822.
  - Up capture 0.873, down capture 0.967.
  - Sortino −1.02 (benchmark −0.28).
  - Carino contributions sum to −12.718%, equal to the portfolio cumulative price return; the reconciliation error is 2.8e-17.
  - These figures are price returns versus the NIFTY 50 price index, on unverified Yahoo data.

### Phase 6 framework
- `benchmark_attribution.py` (new):
  - `active_share`, `sector_aggregate`, single-period `brinson_fachler` and `carino_link`. Brinson-Fachler has explicit conventions for unheld and off-benchmark sectors, and both Brinson-Fachler and `carino_link` reconcile to 1e-12.
  - `attribution_gate`, which also backs `gated_benchmark_weights`. The gate refuses: no dataset, `weight_basis 'none'`, any missing weight, a weight sum outside 1 ± 1e-4, or names without an NSE-industry classification. `estimated` weights stay labelled.
- These functions are tested on fixtures only (textbook 3-sector example, two-period linking). **No UI or CLI exposure, and nothing has been run on real data.**
- Security-level attribution within sectors is **not** implemented yet.

## 3. Angel One HTTP 403: offline findings (superseded by the live results in §6)

These come from `python -m scripts.diagnose_angelone`. They are hypotheses, not a confirmed root cause:

1. **SDK hosts look current.** smartapi-python 1.5.5 uses `apiconnect.angelone.in` and `smartapi.angelone.in`, and the historical route is `/rest/secure/angelbroking/historical/v1/getCandleData`.
2. **The SDK sends a placeholder public IP.** `requestHeaders()` reads the class-level IP attributes. Because the project blocks the SDK's ipify lookup, the SDK falls back to its hard-coded placeholder public IP and a loopback local IP. The constructor overrides `clientPublicIP`/`clientLocalIP` are **ignored** (`constructor_ip_override_honoured: false`). If the account or app now needs a registered static IP, this alone could explain a 403 on the very first request. The SmartAPI portal settings should be checked.
3. **The cached instrument master is from the legacy host.** It was fetched from `margincalculator.angelbroking.com` (`MASTER_URL` in `instrument_master.py`, retrieved 2026-10-16). All 6 configured tokens still resolve and are numeric. The URL was not changed, because that is a behaviour change for the user to approve after checking the current documentation.
4. **No credentials are in the process environment, and there is no `.env` in this checkout.**

**User action:** run `python -m scripts.diagnose_angelone --live --idle-seconds 65 --attempts 2 --spacing 60 --output .tmp\angel_diag.json` locally and review the JSON (it contains no secrets). The interpretation field distinguishes three cases:
- non-historical endpoints pass but the first candle call after idle returns 403 → entitlement, app type or static IP;
- everything fails → session or key;
- candles succeed → attempt a ≤ 15-day live pull.

## 4. Open issues and next steps

1. **The dataset 1 price basis is unconfirmed.**
   - The jump scan found no move above 35% (worst: ITC −9.7%).
   - With an NSE corporate-action file this is evidence the series is split/bonus-adjusted, but it is not proof yet.
   - **User action:** download the NSE corporate actions for the 5 stocks and write a sidecar with its SHA-256.
   - Then run `reference_data.import_corporate_actions()` and `data_quality.unexplained_action_dates()`.
   - If bonus ex-dates are quiet, call `database.relabel_price_basis(1, "split_adjusted_ohlcv", reference_dataset_id=..., evidence=...)`.
2. **Phase 6 data:** a NIFTY 50 constituent file with official, or explicitly labelled estimated, weights plus NSE industry classifications. The decision is still open (roadmap §10.2).
3. **Angel One:** the roadmap §3 exit criterion is **met** (live pull stored as dataset 2; see §6). A long-window pull has not been re-tested.
4. **Not done in Conv 2:**
   - security-level attribution within sectors;
   - `attribution_runs` / `attribution_effects` tables;
   - a price-basis classifier and a cross-provider diff report (roadmap §4, layer 2);
   - dashboard display of the Phase 2 metrics (planned for Conv 6).
5. **Next conversation (Conv 3):** the advanced risk engine. SciPy has not been added yet; it still needs pinning, `pip check` and a full test run.

## 5. Files

- **New:** `portfoliopulse/{migrations,reference_data,performance_metrics,benchmark_attribution}.py`, `portfoliopulse/angelone/diagnostics.py`, `portfoliopulse/providers/{__init__,base}.py`, `scripts/diagnose_angelone.py`, `tests/{conftest,legacy_fixtures,test_migrations,test_corporate_action_check,test_angelone_diagnostics,test_providers_reference_data,test_performance_metrics,test_benchmark_attribution}.py`, `docs/HANDOFF_CONV2.md`.
- **Modified:** `portfoliopulse/{database,data_quality,sourced_csv,portfolio_analysis}.py`, `portfoliopulse/schema.sql` (comment only), `app.py` (one label), `scripts/analyze_phase4.py`, `README.md`, `.gitignore`, `CLAUDE.md` (handoff appended).
- **Dependencies:** none added or changed.


## 6. Addendum (2026-11-12): Angel One live validation

### 6.1 Live diagnostic (run by the user locally, reviewed in `.tmp/angel_diag.json`)
- The report contains no credential, token or IP values (whitelisted fields only).
- Every call returned **HTTP 200** from `apiconnect.angelone.in` with a successful response:
  - login and profile;
  - `getRMS` and `getLtpData`;
  - two `getCandleData` calls, the first after 65 s idle (about 67 s after login) and the second 60 s later.
- The session had its access token and feed token attached. No `ABxxxx` error codes were returned.
- **No 403 was reproduced, so its exact cause cannot be identified from this evidence.**
- **Ruled out for the current account/app:**
  - missing historical-data entitlement;
  - an inactive API key;
  - a session or token defect;
  - wrong hosts or routes;
  - static-IP enforcement. The SDK's hard-coded placeholder public IP was sent and accepted.
- **Not separated:** how the failing 2026-10-16 refresh differed from these successful runs.
  1. Window length: about 1,018 days per instrument then, versus 10–15 days now.
  2. Pacing: 10 s between requests then, versus 60 s now.
  3. Account or server-side state may have changed since October.

### 6.2 Controlled live pull → dataset 2
- **Procedure:**
  - The user authorized the pull. A DB backup was taken first: `data/backups/portfoliopulse_pre_angel_live_pull_20261112T163100238273Z.db`.
  - The window was start 2026-10-29 inclusive to 2026-11-13 exclusive: 6 instruments, 1 request each, spaced 60 s apart (306 s in total). The pull used the existing `fetch_daily_candles` with `request_delay_seconds=60`.
  - A fresh instrument master was downloaded into a **new** file, `data/raw/instrument_master_live_20261112.json`; the existing cache was untouched. It came from `margincalculator.angelbroking.com`, which still serves. All 6 tokens equal the 2026-10-16 cache.
  - The candles were saved to a **new** cache file, `data/processed/angel_candles_live_2026-10-29_2026-11-13_exclusive.csv` (SHA-256 `a1731cf41958069216e956aa0dc99924d225e71952403bcd45a2eaa25c500e16`), with raw batches in `data/raw/angelone/candles_live_2026-10-29_2026-11-13_exclusive.json`. All of these paths are git-ignored.
  - The import used the existing tested path: `python -m scripts.ingest_phase3 --source angel --input <cache> --start 2026-10-29 --end 2026-11-13`.
- **Result:**
  - **Dataset ID 2:** `angel_one_smartapi`, `provenance_status = broker_response_validated`, `verification_status = verified_live`, `price_basis = unadjusted_ohlcv`.
  - **Phase 3 validation run:** analysis ID 8.
  - **Quality report:** `data/processed/phase3_dataset_2_28b5ae75de59_quality.json`.
- **Validation (actually run):**
  - 66 candles, 11 per symbol, 11 union dates, all common, giving 10 valid daily returns. The only excluded return date is the first date, which has no previous observation.
  - 0 duplicates, 0 nulls, 0 non-positive prices, 0 OHLC violations.
  - 6 distinct numeric broker tokens; every row has an IST timestamp (`00:00+05:30` daily stamps) and a UTC retrieval time.
  - NIFTY 50 volume is 0 on every day, which is expected for an index. Stock volumes are positive.
  - No |r| > 35%. The largest move is **RELIANCE +11.6% on 2026-11-02** (1,164.9 → 1,300.0); it has not been checked externally.
  - **Calendar:** there is a **Sunday 2026-11-08** session with real volume, consistent with a special (Muhurat-style) session, and **Tuesday 2026-11-10 is absent**, consistent with a holiday. Neither is checked against an official NSE holiday calendar.
- **Dataset 1 is unchanged:**
  - its 20 Phase-4 metrics and risk contributions are bit-identical (hex compare);
  - all 236 earlier `analysis_results` rows are preserved;
  - the scenario rows are identical;
  - `integrity_check` = ok and `foreign_key_check` is empty.
- **Phase 4 on dataset 2** (read-only, not persisted): cumulative price return is +11.26% for the portfolio versus +10.30% for NIFTY 50.
  - With only 10 returns, downside beta, every rolling window and every period return are undefined, and correctly reported as such.
  - **This dataset is too short for risk statistics.**
- **Tests after the import:** 146 passed, 71 subtests passed. The data-quality, Angel One and migration subset gave 50 passed, 37 subtests.

### 6.3 Is Angel One suitable as the primary historical provider?
**Conditionally yes.** Live access to authenticated daily candles now works, the data passed every integrity check, and provenance is complete (`verified_live`). Before it can replace the Yahoo fallback for analysis, these restrictions and limitations remain:
1. **Long-window access is unproven.** The original 403 occurred on about 1,018-day requests at 10 s pacing. The full 2024-01-02 → 2026-11-13 window is about 1,046 days, under the 2,000-day cap: 6 requests at 60 s spacing. That needs one controlled test.
2. **The data is unadjusted.** Angel candles are raw prices. Any history spanning a split or bonus ex-date will show the jump, and the project never adjusts it silently. Long-history analytics need NSE corporate-action data (and `split_adjusted_ohlcv` handling) or must stay explicitly unadjusted.
3. **There is no cross-provider check yet.** Dataset 2 does not overlap dataset 1 (Yahoo, which ends 2026-10-15) or the NSE reference sample (2026-10-01 → 10-15).
4. **Price index only.** Angel serves the NIFTY 50 price index. There is no TRI and no dividends.
5. **The SDK's placeholder IP header is still sent.** It is accepted today, but would break if Angel enforces a registered static IP. SDK 1.5.5 ignores constructor overrides, so a fix needs an SDK workaround or upgrade. That needs a compatibility review; do not upgrade casually.
6. **The instrument master still comes from the legacy `angelbroking.com` URL.** It works today, but the current-docs host should be confirmed before changing `MASTER_URL`.
7. **Pacing:** keep at least 60 s spacing and at most one request per instrument and window until the long-window test passes. Rate limits are not documented in the project.

### 6.4 Recommended next data step (needs explicit user approval; not run)
Pull the 2024-01-02 → 2026-10-16 window from Angel One at 60 s spacing as a new dataset, then compare it with dataset 1 (a cross-provider diff, never a merge) and with the NSE reference sample. This one step would:
- test whether long windows trigger the 403;
- give the first Angel-versus-Yahoo and Angel-versus-NSE comparison;
- show whether Yahoo dataset 1 is split/bonus-adjusted. Raw Angel closes should step down at bonus ex-dates where Yahoo's do not.

## 7. Conversation 3 kickoff: Advanced Risk Engine (roadmap Phase 3)

**Read first:** `CLAUDE.md` (including §13), this file, and roadmap §5 Phase 3 and §9.

**Scope (Phase 3 only; no stress-testing, optimization or dashboard work):**
- Historical VaR/ES (already present: reuse `risk_metrics.historical_var_es`) plus parametric normal, Cornish-Fisher and Monte Carlo VaR at 95% and 99%, with ES/CVaR for each.
- Kupiec POF and Christoffersen independence backtests.
- Drawdown duration and recovery time.
- Sample, Ledoit-Wolf and EWMA (λ = 0.94) covariance.
- Component VaR/ES that reconciles to the total.
- New modules per roadmap §4: `var_models.py` and `risk_analysis.py`. Each records `engine_version` and `settings_json`.

**Prerequisite:** SciPy (`norm`, `chi2`).
- Choose a version compatible with numpy 2.5.4 / pandas 3.0.6 / Python 3.12.
- Pin it in `requirements.txt`, then run `pip check` and the full suite before writing any risk code.
- Commit the pin separately.

**Data to use:**
- **Dataset 1** (Yahoo, 686 valid returns, `user_declared_unverified`, basis unconfirmed) is the only sample long enough. Label every output with its source and verification status. 99% historical uses k = 7 tail observations; show this.
- **Dataset 2** (Angel, `verified_live`) has 10 returns: use it only for smoke tests, never for tail statistics.
- If the user approves §6.4 first, the long Angel dataset becomes the preferred input. Never mix providers within one return series.

**Invariants to preserve:**
- The baseline is **146 tests / 71 subtests**; no existing test may be removed.
- Phase 4 metric keys and `phase8_perf_v1` keys stay unchanged. New keys go under a new engine version.
- `Phase4Analysis.metrics` keeps exactly 20 keys.
- Schema changes are additive numbered migrations (`migrations.py`, currently v3), with a backup first.
- Dataset 1 and dataset 2 records must stay bit-identical. Verify with a hex-float metric dump like the one used in Conv 2.
- Labels: "price return vs NIFTY 50 price index"; 1-day horizon; √h scaling is shown only as an assumption; Monte Carlo reports seed, path count and standard error.

**Acceptance (roadmap Phase 3):**
- Normal VaR matches the closed form.
- Monte Carlo converges to parametric within the standard-error bound at a fixed seed.
- Kupiec matches a hand-worked χ² example.
- Component VaR sums to the total.
- Every formula is checked against numpy/scipy independently.
- Commit per milestone and append a dated handoff to `CLAUDE.md` and `docs/HANDOFF_CONV3.md`.

**Open user decisions carried forward:**
- §6.4 long Angel pull (approval);
- NSE corporate-action file (to confirm the dataset 1 basis);
- NIFTY 50 constituent-weights source (Phase 6);
- static-IP/SDK handling if Angel tightens enforcement.
