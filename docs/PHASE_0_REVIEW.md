# Phase 0 review — 16 October 2026

Historical review under the earlier project scope. See [the revised plan audit](REVISED_PLAN_AUDIT.md) for current phase status.

## Confirmed scope

- Audience: Indian retail-investor perspective with clear analyst-quality calculations for the Russell Investments Asset Allocation Intern application.
- Currency and universe: INR and Indian-listed assets. Specific real instruments and benchmark files will be selected in Phase 2 after source and adjustment checks.
- Portfolio accounting: buy and hold. Holdings quantities remain constant during the selected window, while current weights change with market values. Target weights provide a drift reference.
- Product boundary: historical risk analysis and hypothetical stress tests. The rules-based briefing describes calculated facts; no trade execution or personalized recommendation.

## Inherited workspace findings

The folder contained a Streamlit prototype, synthetic data generator, one combined analytics module, SQLite code and a long plan. It had no Git repository, local virtual environment, tests, or generated data/database. Python 3.12.10 is available on the host. There is no evidence that this prototype has been run successfully or that its financial output has been validated.

The most important gaps for later phases are:

| Finding | Next phase |
| --- | --- |
| `NIFTYETF`, `BANKETF`, `GOLDETF`, `DEBTETF` and `NIFTY50` are synthetic labels and synthetic price paths, not verified NSE instruments or Nifty TRI data. Generated weekdays also include Indian market holidays. | P2 |
| CSV import accepts only the seeded symbols; it does not yet provide a real-data download, source-specific adjustment handling, or a validated offline fallback for real data. | P2 |
| Schema/repository need a documented portfolio identity and data lineage. The app currently saves a risk run using portfolio ID `1`. | P3 |
| Starter metrics need formula review. The briefing says 95% VaR even when 99% is selected, a zero-volatility Sharpe is shown as zero, and the annual-return convention differs from the pasted plan. | P4/P6 |
| Volatility contribution is absent. Benchmark beta and rolling volatility are calculated inside the UI rather than the analytics module. | P4/P6 |
| The existing scenario controls shock asset classes, while the new brief requires per-asset shocks. | P5 |
| Input edge cases, SQL persistence, charts and scenario reconciliation have no automated or manual validation record. | P2–P7 |
| README describes features in the present tense despite the unverified state. Setup, screenshots and interview materials need a checked handoff. | P1/P8 |

These are tracked gaps, not claims that the current app is ready for an investor.

## Phase 0 changes

- Kept the Streamlit entry point at the root and organized source under the `portfoliopulse` Python package.
- Separated data generation, SQL storage, analytics, scenarios and briefing into named modules, and moved the schema beside the repository code.
- Added one configuration module for project paths, portfolio name, benchmark symbol and trading-day convention.
- Created `data`, `tests` and `docs` locations and updated imports and documentation paths.
- Replaced the old two-day summary with explicit phase gates in `PROJECT_PLAN.md`.

## Phase 0 gate

**Passed:** Python imported the package modules; the schema path resolved; `compileall` found no syntax errors in the app, package or seed script; a file listing showed the new layout and no old `src` package. These are structure checks only. The dashboard was not launched, and financial results remain unverified.

Phase 1 begins with a reproducible environment and setup check. The hard-coded risk-run portfolio ID and other findings above remain tracked for their assigned phases.
