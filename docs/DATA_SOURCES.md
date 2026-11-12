# Data Sources, Ingestion and Verification

## What data is in the repository

| Item | Location | What it is |
|---|---|---|
| Synthetic demo prices | `data/demo/prices.csv` | Fictional prices for the legacy demo path and tests; never shown as real market data |
| NIFTY 50 reference sample | `docs/reference/nifty50_nse_indices_2026-10-01_2026-10-15.csv` | Ten NIFTY 50 closes hand-transcribed from the NSE Indices website (see the spot check below) |
| Market data and database | `data/portfoliopulse.db`, `data/incoming/` | **Not published** (git-ignored). Each user loads their own lawfully obtained data |

## Datasets used for the results in this repository

These datasets live only in the local database.

| Dataset | Source | Window | Status |
|---|---|---|---|
| 1 | Yahoo Finance via yfinance 1.7.0, imported as a source-labelled CSV | 2024-01-02 → 2026-10-15; 4,133 candles; 686 valid daily returns | `user_declared_unverified`. Labelled `unadjusted_ohlcv`, but the price evidence suggests split/bonus-adjusted prices (see [DATA_DEPENDENCIES.md](DATA_DEPENDENCIES.md)) |
| 2 | Angel One SmartAPI live pull | 2026-10-29 → 2026-11-12; 66 candles; 10 valid returns | `verified_live`, unadjusted. Too short for risk statistics or optimization |

NIFTY 50 is missing on 2026-01-15 in dataset 1, so that date and the returns on 15 and 16 January are excluded rather than filled.

## Loading data

### Route 1: source-labelled CSV

The CSV needs the columns `symbol, trading_symbol, exchange, date, open, high, low, close, volume`. Symbols must match `config/instruments.json` (for example `RELIANCE, RELIANCE-EQ, NSE` and `NIFTY 50, Nifty 50, NSE`), and dates use `YYYY-MM-DD`.

Next to the CSV, place a sidecar named `<csv name>.meta.json` containing:

```json
{
  "source_name": "Yahoo Finance via yfinance 1.7.0",
  "source_url": "https://finance.yahoo.com/",
  "retrieved_at_utc": "2026-10-16T05:18:22+00:00",
  "price_basis": "unadjusted_ohlcv"
}
```

- `price_basis` is one of `unadjusted_ohlcv`, `provider_adjusted_ohlcv` or `split_adjusted_ohlcv`.
- An optional `csv_sha256` is checked against the file.

Then run:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source sourced --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
```

`--start` is inclusive and `--end` exclusive. `--metadata` points to a sidecar stored elsewhere, and `--database` selects another SQLite file.

### Route 2: Angel One SmartAPI

1. Copy `.env.example` to `.env` and fill in your own credentials. Never commit or share this file.
2. Download the candles into the local cache: `.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2026-10-29 --end 2026-11-13`
3. Load the cache into the database: `.\.venv\Scripts\python.exe -m scripts.ingest_phase3 --source angel --start 2026-10-29 --end 2026-11-13`

Requests are spaced 60 seconds apart. If the API returns HTTP 403, run `scripts.diagnose_angelone`, which is offline by default and prints no secrets.

## Validation rules

Every import goes through `data_quality.validate_daily_candles`, which checks:
- positive, finite OHLC prices; low ≤ open/close ≤ high; non-negative volume;
- a unique symbol/date key, a valid calendar date, and a single declared price basis;
- exact instrument identities against the configuration.

Prices are aligned on the observed dates only. A daily return is kept only when every instrument has both the current and the previous close. **Nothing is forward-filled.** The code does not build an NSE holiday calendar, and it does not independently verify a provider's adjustment claims.

Each dataset is stored once. Identical re-imports return the same dataset ID, while a changed source, basis or content gets a new ID. Holding quantities are snapshotted with the dataset.

## NIFTY 50 spot check (limited independent verification)

On 16 October 2026, ten NIFTY 50 closing values for 1–15 October 2026 were transcribed from the [NSE Indices historical data page](https://www.niftyindices.com/reports/historical-data). They were then compared with dataset 1:

```powershell
.\.venv\Scripts\python.exe -m scripts.compare_nse_index --dataset-id 1 --nse-csv docs\reference\nifty50_nse_indices_2026-10-01_2026-10-15.csv --reference-source-url https://www.niftyindices.com/reports/historical-data
```

**Result:** all 10 dates matched within 0.01 index points, with a maximum difference of 0.00078 points.

This verifies only those ten index closes. It is not a complete NSE export, and it doesn't validate any individual stock prices or the other dates.
