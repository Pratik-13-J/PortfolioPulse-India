# Phase 3 verification — data quality and SQLite

## Result

Phase 3 is complete for the locally saved, source-labelled Indian equity CSV. The import keeps the fictional dashboard portfolio separate from the real NSE portfolio. Angel One authenticated earlier, but its historical endpoint returned HTTP 403; this phase used the accepted Yahoo Finance CSV fallback and does not establish a live Angel One candle result or an independent NSE price match.

The initial real portfolio has fixed quantities of RELIANCE, HDFCBANK, INFY, ICICIBANK and ITC in INR, with NIFTY 50 as a benchmark. Its current weights will drift with prices; target weights are stored as references. The code does not calculate portfolio risk metrics yet. That is Phase 4.

## Data quality rules

`portfoliopulse/data_quality.py` checks each daily candle for a valid Indian calendar date, a unique symbol/date key, positive and finite OHLC prices, non-negative finite volume, consistent high/low relationships, an explicit source, a UTC retrieval timestamp, `ONE_DAY` interval and one declared price basis. It rejects a mixed adjustment basis. The sourced CSV importer also verifies its provenance sidecar and exact configured instrument identities before this check.

The validator aligns closes on the union of **observed** dates, then keeps a price date only if all six instruments are present. It calculates daily percentage returns on the original union grid with no fill. A return is kept only when every instrument has both the current and preceding observed close. It does not generate an NSE holiday calendar, infer an exchange session from a weekday, or independently verify a provider's corporate-action adjustment claim.

For the locally saved 2 January 2024 to 15 October 2026 observations (exclusive end: 16 October 2026):

| Check | Result |
| --- | ---: |
| Valid daily candles | 4,133 |
| Observed union dates | 689 |
| Shared price dates | 688 |
| Valid daily-return dates | 686 |
| Incomplete price dates | 1 |
| Excluded return dates | 3 |

NIFTY 50 is absent on **15 January 2026**. The price date is excluded, as are returns dated 15 and 16 January 2026. The first observed date, 2 January 2024, has no prior observation and therefore no daily return. No missing close or return was fabricated. The detailed, machine-readable quality report is saved locally under `data/processed/` and excluded from Git with the source data.

## SQLite storage and repeatability

`portfoliopulse/schema.sql` and `portfoliopulse/database.py` add source-isolated datasets to the existing SQLite demo schema:

| Stored item | Purpose |
| --- | --- |
| `instrument_listings`, `portfolio_benchmarks`, `holdings` | Exact NSE listing identities, benchmark and current portfolio setup |
| `candle_datasets`, `daily_candles` | Source, retrieval time, adjustment basis, window and daily OHLCV; every candle has its own retrieval time |
| `dataset_holdings` | Immutable snapshot of quantities, target weights, sector and benchmark identity used with that dataset |
| `analysis_metadata`, `analysis_results` | Dataset-linked settings, quality exclusions and a place for future scalar analytics results |

Queries use bound SQL parameters. The schema checks price and OHLC relationships, unique keys and foreign keys; imports use a transaction. A changed provider, price basis, candle content or holding snapshot gets a different dataset identity. An unchanged import returns the same dataset and validation-run IDs. Invalid input is rejected before the dataset write, and a simulated mid-insert SQL failure rolls back the dataset and partial candles. Existing fictional observations remain in their separate tables. Migration of a previously created Phase 3 SQLite database retained the original dataset ID and all 4,133 candles.

## Run on Windows PowerShell

From the repository folder, with the project environment installed and a real source CSV plus its same-stem `.meta.json` sidecar available locally:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\yahoo_nse_2024-01-02_2026-10-16.csv --start 2024-01-02 --end 2026-10-16
```

The command prints the dataset and validation-run IDs, quality counts and exact report path. It uses `data/portfoliopulse.db` by default; `--database` selects another SQLite file. When a matching, integrity-checked Angel One candle cache exists, `--source angel` reads that cache offline. This command does not fetch market data or access credentials.

The local verification import and repeat import both returned **dataset ID 1, validation-run ID 1** with 4,133 candles. The database passed SQLite integrity and foreign-key checks; the original demo observations remained present. The full test suite passed **60 tests**, including Phase 3 data-quality, duplicate/invalid data, idempotence, changed-quantity snapshots, rollback, migration, mock Angel metadata and end-to-end CSV cases. `pip check`, Python compilation and Git whitespace checks also passed.

The Yahoo observations are labelled `unadjusted_ohlcv` and `user_declared_unverified`. Their close-to-close changes are **price returns**, not dividend-aware total returns. A fresh checkout will need a legally obtained input CSV and sidecar because the local market data and database are ignored. Selected NSE reference-price comparisons remain for Phase 7.
