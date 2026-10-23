# Phase 5 verification — Indian equity stress tests

This records the Phase 5 state. Phase 6 dashboard interaction and a limited NSE spot check were verified in the later [final audit](FINAL_AUDIT.md).

## Scope and interpretation

Phase 5 runs hypothetical one-period shocks against an explicitly selected Phase 3 dataset. It uses the immutable fixed-quantity holding snapshot and the latest price date shared with NIFTY 50 from Phase 4. Share quantities stay fixed, so the valuation weights reflect buy-and-hold drift. Scenario results are illustrations; they are not forecasts, investment recommendations, or historically calibrated predictions. A shock applies directly to the latest observed holding value, without estimating a price path.

## Configurable scenarios

| Scenario type | Default behavior | Configurable inputs |
| --- | --- | --- |
| `broad_market` | Every holding receives −10%. | `broad_shock` |
| `banking_stress` | `Banking` holdings receive −20%; all other holdings receive −5%. | `stressed_shock`, `residual_shock`; sector overrides and individual overrides |
| `it_correction` | `Information Technology` holdings receive −20%; all other holdings receive −5%. | `stressed_shock`, `residual_shock`; sector overrides and individual overrides |
| `individual` | Only explicitly named holdings move; unspecified holdings receive 0%. | One or more symbol shocks |
| `sector` | Only explicitly named sectors move; unspecified holdings receive 0%. | One or more sector shocks, optional individual overrides |

Shock values are decimal returns (for example, `-0.15` means −15%). Finite shocks down to −100% are accepted; values below −100% are rejected. Positive hypothetical shocks are allowed. Preset sectors match the sector labels stored in the selected dataset; an individual symbol override takes precedence over a sector override. No position is silently added or removed.

For holding `i`, value is `V_i = quantity_i × latest_close_i` and scenario contribution is `C_i = V_i × shock_i`. Total estimated INR impact is `sum(C_i)`, and portfolio impact is `sum(C_i) / sum(V_i)`. The reported holding `portfolio_impact_pct` is `C_i / sum(V_i)`; these percentages sum to the total portfolio impact percentage, even when positive and negative shocks net to zero. Losses are negative impacts. The engine validates these relationships before writing results.

Allocation drift is the actual as-of market weight minus the dataset's saved target weight. The default alert threshold is an absolute 5 percentage points. Concentration defaults to an alert when an individual holding exceeds 40% or portfolio Herfindahl–Hirschman index `sum(weight²)` exceeds 0.25. Thresholds are configurable. Drift thresholds are strict: equality does not trigger an alert.

## SQLite lineage

`scenario_runs` stores the dataset ID, Phase 4 analysis ID, as-of date, scenario type, hypothetical marker, portfolio value, total INR/percentage impact, input settings and alert results. `scenario_contributions` stores each holding's as-of value, applied shock, INR impact and portfolio-return contribution. A scenario and its holding rows are written in one transaction, and the database verifies dataset/analysis portfolio lineage. Existing fictional-demo scenario records remain separate.

## Run

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_phase5 --dataset-id 1 --type broad_market
.\.venv\Scripts\python.exe -m scripts.analyze_phase5 --dataset-id 1 --type banking_stress --stressed-shock -0.25 --residual-shock -0.04
.\.venv\Scripts\python.exe -m scripts.analyze_phase5 --dataset-id 1 --type individual --individual INFY=-0.22 --individual HDFCBANK=-0.12
.\.venv\Scripts\python.exe -m scripts.analyze_phase5 --dataset-id 1 --type sector --sector Banking=-0.20 --sector Energy=-0.12
```

The local verification used saved dataset 1, sourced from Yahoo Finance via yfinance and labelled `unadjusted_ohlcv` / `user_declared_unverified`. Its Phase 4 matched valuation date was 15 October 2026, portfolio value INR 119,198.75. A default broad-market shock produced an estimated impact of INR −11,919.87 (−10%). The five holding contributions sum to that amount; the fractional INR values may differ by a paisa when displayed rounded. Current-weight HHI was 0.212, below the default 0.25 alert threshold, and ICICIBANK exceeded its target weight by 9.14 percentage points. These figures describe the local input and assumptions only.

The six focused tests check preset shocks and overrides, invalid targets, manually verifiable INR and percentage reconciliation, mixed shocks that net to zero, drift and concentration thresholds. Phase 6 will connect the tested outputs to the dashboard. Angel One's live historical endpoint and independent NSE checks remain pending from earlier phases; the scenario math does not resolve those source limitations.
