# Phase 2 data record — 16 October 2026

Historical fictional-demo gate only. The revised Angel One Phase 2 gate remains open; see [the revised plan audit](REVISED_PLAN_AUDIT.md).

## Scope and portfolio

The user requested a dummy INR portfolio for now. The checked-in `data/demo/prices.csv` contains 6,453 fictional observations for nine `DEMO_` instruments across 717 weekdays, from 2 January 2024 to 30 September 2026. Eight are holdings and one is a separate Nifty 50-style benchmark. The portfolio holds fictional energy, banking, IT, healthcare, consumer staples and auto stocks, plus gold and government-debt ETF proxies. Fixed unit quantities support the confirmed buy-and-hold model; target weights sum to 100%.

All prices are synthetic and reproducible from `portfoliopulse/demo.py` with random seed `20261016`. The weekday grid is not an NSE trading calendar. No instrument is a listed security, no price is a real exchange/NAV observation, and the series do not include corporate-action adjustment, dividends, coupons, taxes or trading costs. The source stored in SQLite is `synthetic_india_demo_v1`.

## Data path

1. `scripts.seed_demo` initializes or migrates the SQLite schema, adds fictional instrument/sector metadata and portfolio holdings, then loads the bundled CSV if no demo observations exist.
2. `data.py` requires `symbol,date,close_value`; rejects blank symbols, invalid dates, non-finite or non-positive prices and duplicate symbol/date rows; and checks each requested symbol's coverage.
3. The fallback function uses the bundled CSV only when a primary file is absent. A malformed primary file is reported instead of silently replaced. No live provider is connected yet; a dashboard upload is the explicit alternative input path for the demo symbols.
4. Date alignment uses common observed prices and computes daily returns on the original date grid before dropping missing rows. It never forward-fills or forms a return across a missing date. The dashboard blocks risk calculation when the selected window contains any incomplete instrument or benchmark date.
5. SQLite imports use a transaction, foreign keys, parameterized SQL and `(instrument_id, observation_date)` upserts. Repeat startup does not overwrite existing demo observations or user edits to holdings.

## Verification

Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_data_pipeline.py' -v` from the project root. Six checks cover the bundled CSV and source, invalid rows, duplicates, missing-date returns, fallback selection, idempotent SQL seeding and no-write behavior on invalid imports. The seed script was run twice against the local database; the first run loaded 6,453 observations and the second loaded zero. The local database has eight holdings, one benchmark, and all new observations labeled with the synthetic source. The app completed a bare Python run without an exception, and a temporary Streamlit server returned HTTP 200 from its local health endpoint. An interactive dashboard walkthrough remains for Phase 6 and Phase 7.

## Phase gate and remaining work

**The Phase 2 offline demo path passes.** It gives the project a reproducible Indian-perspective sample and a validated CSV fallback. The separate adjusted-price pipeline requested later is documented in [PHASE_2_MARKET.md](PHASE_2_MARKET.md). Phase 3 will review SQL identity, import semantics and saved snapshots; Phase 4 will verify the financial formulas. The existing dashboard charts and risk figures are still provisional until those phases pass.
