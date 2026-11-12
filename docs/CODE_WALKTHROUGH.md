# Code Walkthrough

**Design rules shared by all of these functions:**
- **Pure calculation functions** validate their inputs, never touch the database, and return plain numbers or DataFrames.
- **Orchestrators** read a dataset, call the calculations, and persist the results with an `engine_version`.
- **Undefined results** come back as `None` or with a status string. They are never forced to a number.

## Call graph (risk engine example)

```text
scripts/analyze_risk.py main()
 └─ risk_analysis.analyze_saved_dataset_risk(dataset_id)
     ├─ database.get_dataset_info / get_candle_dataset
     ├─ portfolio_analysis.compute_phase4_analysis
     │    ├─ data_quality.validate_daily_candles
     │    └─ portfolio_returns.build_buy_and_hold_returns
     ├─ risk_analysis.compute_risk_analysis
     │    ├─ var_models.historical → risk_metrics.historical_var_es
     │    ├─ var_models.parametric_normal / cornish_fisher / monte_carlo
     │    ├─ covariance_estimators.covariance_estimates → var_models.delta_normal_components
     │    ├─ var_backtests.rolling_var_forecasts → backtest_var (kupiec_pof, christoffersen_independence)
     │    └─ drawdowns.drawdown_summary
     └─ database.save_analysis_run  (dedups identical settings, so reruns are idempotent)
app.py → ui.stored_results.latest_run → risk_view → ui.charts.var_methods
```

The stress and optimization engines follow the same shape:
- `stress_analysis.analyze_saved_dataset_stress` calls `stress_scenarios`, `historical_stress` and `stress_impact`, then `save_dataset_scenario`.
- `optimization_analysis.analyze_saved_dataset_optimization` calls `optimization`, `portfolio_evaluation`, `portfolio_allocation` and `optimization_backtest`, then `save_optimization_runs`.

## Key functions

| Function | What it does | Why this approach | Input validation | How to modify safely |
|---|---|---|---|---|
| `portfolio_returns.build_buy_and_hold_returns` | Builds values, weights and returns from closes and fixed quantities; aligns with the benchmark | Fixed quantities match a real buy-and-hold holder, and weights drift naturally | Positive closes; known symbols; one benchmark; no bridged gaps | Every engine depends on it: change only with the Phase 4 tests and the hex-dump comparison |
| `risk_metrics.historical_var_es` | Nearest-rank VaR/ES | Deterministic with ties; no interpolation assumption | `_returns`: sorted unique dates, finite, ≥ −100% | Reused by Monte Carlo and backtests; changing it alters stored run 9 |
| `performance_metrics.relative_performance` | TE, IR, beta, capture, Sortino, rolling, period returns, Carino contributions | One place for benchmark-relative metrics, with reasons for anything undefined | `_paired`: identical date indexes | New keys go under a new `engine_version` |
| `benchmark_attribution.brinson_fachler` / `carino_link` | Sector effects and their multi-period linking | Brinson-Fachler keeps allocation relative to the benchmark total; Carino makes effects add up across periods | Weights sum to 1 ± 1e-4, are long-only, have finite returns; a reconciliation check raises an error | Use only through `attribution_gate`; never feed it estimated weights unlabelled |
| `var_models.monte_carlo` | Seeded MVN or bootstrap simulation, revalued at fixed quantities, with batch-means SE | Reproducible, and reports its own error | Paths must be a multiple of batches; seed ≥ 0; holding values cover every asset | Keep the seed and paths in the settings |
| `var_models.delta_normal_components` | Euler component VaR/ES | Components add up to the total exactly | Square symmetric covariance; weights sum to 1 | Don't mix with volatility contributions |
| `var_backtests.rolling_var_forecasts` | One-day-ahead forecasts from the trailing window only | Prevents look-ahead | Window size; enough history | `FORECAST_METHODS` feeds stored runs; new estimators go in `DIAGNOSTIC_METHODS` |
| `covariance_estimators.ledoit_wolf_covariance` | Shrinkage toward a scaled identity | Optimizers amplify covariance noise | `_asset_returns` validation | Tested against a loop transcription of the paper |
| `stress_scenarios.make_vector` / `ShockVector` | Shocks split into direct, beta, covariance, factor and historical components | Every shocked number can explain its source | Finite; a total shock below −100% is rejected | Add new shock types as new components |
| `historical_stress.replay_event` | Applies observed event-window returns to today's holdings | Real observations only; reports unavailable events | Anchors must cover both ends of the window | Never supply index moves from memory |
| `optimization.solve` | SLSQP from several starting points, keeping the best **feasible** solution | SLSQP can claim success while breaching a constraint; this re-checks | `build_problem` rejects impossible bounds up front | New constraints go in `_scipy_constraints` and `check_feasibility` together |
| `optimization.max_sharpe` | Tangency portfolio | Returns `undefined` if no feasible E[r] exceeds rf | — | — |
| `portfolio_allocation.integer_allocation` | Whole shares within the budget | Adds a share only if it reduces the gap to target, so no overweight dumping | Positive prices; weights sum to 1 | The budget identity is tested |
| `optimization_backtest.walk_forward` | Rebalancing rules evaluated out of sample, with costs | A realistic, no-look-ahead check | Enough observations, otherwise `insufficient_data` | The rules receive only the window slice |
| `database.save_analysis_run` | Atomic metadata and results, deduplicated | Idempotent reruns | Finite values, non-empty units | Schema changes go only through a new migration |
| `ui.stored_results.latest_run` | Read-only retrieval of the newest run per engine | The dashboard never recomputes or writes | `mode=ro` connection | Add new views here, not in `app.py` |

## Safe-modification checklist

1. Add a test with a hand-calculated expected value first.
2. Keep stored-run inputs unchanged. Otherwise reruns create new runs, which is expected but must be deliberate.
3. Run `pytest` and `python -m scripts.audit_consistency --dataset-id 1`.
4. For schema changes, add a new numbered migration in `migrations.py`, tested on a copy, with a backup first.
