# PortfolioPulse India

Indian equity portfolio risk analytics and stress testing with Python, SQLite, Streamlit and Plotly.

## Project status

The [revised project plan](PROJECT_PLAN.md) makes **Angel One SmartAPI daily candles the primary market data source**, with NSE reports used for selected independent checks. The initial portfolio is an Indian equity example compared with NIFTY 50. Gold, government debt and mutual funds are possible extensions once compatible data and methods are established; the current market-data workflow is not a complete institutional multi-asset portfolio.

| Gate | Current result |
| --- | --- |
| Phase 0 — review and structure | Passed: the Python package layout and imports were checked, and open financial issues were recorded. |
| Phase 1 — environment and configuration | Verified after the revised setup and configuration repairs. The app and package can start locally without broker credentials. |
| Phase 2 — Angel One data ingestion | **Open.** Public instrument-master validation, mocked candle ingestion, exact CSV replay and independent sourced-CSV import checks pass. An authenticated candle download and real sourced-CSV inspection remain pending. The earlier fictional CSV and yfinance checks do not satisfy this revised gate. |

The existing dashboard still displays a fictional, INR-denominated demo portfolio. Its financial metrics, SQL workflows, scenarios and UI have not passed the later phase gates. See the [revised-plan audit](docs/REVISED_PLAN_AUDIT.md) for the precise Phase 0–2 findings.

## Windows PowerShell setup

Run from the repository folder with Python 3.12:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.seed_demo
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The temporary-directory settings keep package installation inside the repository on restricted Windows machines. To open the folder in VS Code, use `code .` if its command is installed.

The starter dashboard and bundled fictional CSV need no API credentials. The local SQLite database is `data/portfoliopulse.db`; it is excluded from Git. The seed command is idempotent for an already populated demo portfolio.

### Angel One credentials and daily data

For an authenticated Angel One refresh, copy the example file to a local `.env`, then fill the four named values in that local file:

```powershell
Copy-Item .env.example .env
notepad .env
```

The variable names are `ANGELONE_API_KEY`, `ANGELONE_CLIENT_ID`, `ANGELONE_PASSWORD` and `ANGELONE_TOTP_SECRET`. The refresh command loads the project `.env` locally; `.env` is ignored by Git. Keep credentials out of code, screenshots, issues and chat messages. The Angel One refresh checks current instrument-master mappings; no stock token should be assumed from an older example.

Run the daily-candle refresh with an inclusive start and exclusive end date:

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2024-01-02 --end 2026-10-16
```

Once a successful download creates a matching local candle CSV and metadata, replay that exact date range without network access:

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_angelone --start 2024-01-02 --end 2026-10-16 --offline
```

Offline mode needs the saved candle CSV and its source, token, date-window and checksum metadata. It does not request a current instrument master. The fictional demo CSV is not substituted for listed securities. The authenticated refresh cannot be considered verified until valid credentials are supplied and its returned candles and saved output are inspected.

### Independently sourced CSV when broker access is unavailable

Place a real, legally obtained daily OHLCV file at `data/incoming/prices.csv` and a provenance sidecar at `data/incoming/prices.meta.json`. Both stay local and are ignored by Git. This path uses the same six configured NSE instruments but is kept separate from the Angel One cache. The CSV header must be exactly:

```text
symbol,trading_symbol,exchange,date,open,high,low,close,volume
```

Use the logical and trading symbols in [config/instruments.json](config/instruments.json), actual `YYYY-MM-DD` observation dates and actual prices. The sidecar must record the true source and price convention:

```json
{
  "source_name": "actual provider or report name",
  "source_url": "https://example.com/actual-report-page",
  "retrieved_at_utc": "2026-10-16T09:00:00+00:00",
  "price_basis": "unadjusted_ohlcv"
}
```

Replace the example URL and retrieval time with the real values; use `provider_adjusted_ohlcv` only if the provider actually adjusted every OHLC field. Import the exact window with:

```powershell
.\.venv\Scripts\python.exe -m scripts.import_sourced_csv --input data\incoming\prices.csv --start 2024-01-02 --end 2026-10-16
```

The importer checks instruments, OHLCV, dates, duplicates, source metadata and missing observations, then saves aligned closes, daily returns, coverage and provenance under ignored `data/processed/`. Its source claim is labelled **user declared and unverified** until independently checked; imported data is never labelled Angel One. It does not turn price observations into dividend-aware total returns.

## Historical local demo and optional research path

The checked-in `data/demo/prices.csv` contains **synthetic** weekday prices for eight fictional holdings across Indian equity sectors, gold and government debt, plus a fictional Nifty-style benchmark. `DEMO_` symbols are not listed securities. Weekdays are not a verified NSE holiday calendar, and the series contain no dividends, coupons or corporate-action adjustments. The existing dashboard reads this portfolio so that the interface can be demonstrated offline.

The previously built yfinance pipeline is a separate optional research path. It downloads auto-adjusted daily values for listed Indian examples and a `^NSEI` **price-index** benchmark, then keeps a local cache under ignored `data/market/`. This path passed its own historical-data checks but is neither Angel One ingestion nor NSE validation. To run it:

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_market_data --start 2024-01-02 --end 2026-10-16
.\.venv\Scripts\python.exe -m scripts.refresh_market_data --start 2024-01-02 --end 2026-10-16 --offline
```

These dates are an example; the start is inclusive and the end is exclusive. Offline reload requires a prior successful download with the same symbols, dates and adjustment basis. The listed sample is not yet connected to the demo dashboard or SQLite portfolio. See the [fictional-data record](docs/PHASE_2_DATA.md) and [yfinance verification record](docs/PHASE_2_MARKET.md) for what those historical checks actually established.

## Revised workflow and financial boundaries

The Phase 2 Angel One workflow is intended to validate current symbol/token/exchange mappings, retrieve daily OHLCV, record source and retrieval metadata, handle failures and rate limits, and permit local CSV fallback. Its stock and index candles are **price observations**, not automatically dividend- and corporate-action-adjusted total returns. A zero index volume is not automatically a failed quote. Missing prices must be reported rather than filled or fabricated. The Phase 2 gate requires mock-response checks and an authenticated retrieval before sign-off.

Later phases will validate SQLite portfolio setup, buy-and-hold accounting, risk metrics, holding-level stress contributions, and the analyst dashboard. The confirmed portfolio model uses fixed quantities so market weights drift; target weights are a reference for allocation alerts. Any switch to a fixed-weight, periodically rebalanced model must be stated explicitly and its calculations retested.

This is an educational analysis tool, not investment advice, a forecast or a trading system. Reported historical losses and hypothetical shocks depend on the chosen data, window and methodology. Market-data licensing and exchange terms need review before any public redistribution.
