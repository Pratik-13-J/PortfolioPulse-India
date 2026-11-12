# Handoff — Conversation 2 (2026-11-12)

Scope: roadmap Phase 1 (foundation and data correctness), Phase 2 (relative performance) and the Phase 6 framework (gated benchmark attribution). Branch: `conv2-foundation-performance-attribution`, one commit per phase. Nothing has been pushed.

| Phase | Commit | Summary |
|---|---|---|
| 1 | `a4b17a1` | Versioned migrations, provenance ledger, warn-only jump check, reference importers, Angel 403 diagnostic, provider protocol |
| 2 | `aeb251a` | `performance_metrics.py`, integrated under `engine_version = phase8_perf_v1` |
| 6 (framework) | `b4a46c6` | `benchmark_attribution.py`: Active Share, Brinson-Fachler, Carino linking, data gate |
| docs | (this commit) | README, this handoff, `CLAUDE.md` handoff section |

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
- **Angel One:** only the **offline** diagnostic was run. **No live check was run**, because credentials stay with the user.

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

## 3. Angel One HTTP 403: offline findings (live checks pending)

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
3. **Angel live diagnostic** (above). The exit criteria (a small live pull or a documented external blocker) are **not yet met**.
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
