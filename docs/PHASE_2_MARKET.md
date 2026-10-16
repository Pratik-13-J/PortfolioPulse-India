# Phase 2 historical market-data verification — 16 October 2026

Historical yfinance gate only. It does not verify the revised Angel One Phase 2 gate; see [the revised plan audit](REVISED_PLAN_AUDIT.md).

## Audit of the original Phase 2 sign-off

The earlier sign-off covered the **fictional offline demo only**. It did not meet the subsequently supplied P2 definition requiring yfinance adjusted historical downloads and fallback after a download failure. That claim has been corrected in the README and project plan.

## Separate real sample portfolio

`portfoliopulse/real_portfolio.py` defines six NSE equity examples across energy/conglomerate, banking, IT, healthcare, consumer staples and automobiles, plus `GOLDBEES.NS` (gold) and `LTGILTBEES.NS` (government debt). Its sample unit quantities and target weights are illustrative. `^NSEI` is a separate **Nifty 50 price index** benchmark, not a total-return index. The Streamlit dashboard still uses `DEMO_` prices; the listed portfolio is ready for Phase 3 SQL and Phase 6 UI integration.

## Download and fallback contract

- Run `pip install -r requirements.txt` in the project virtual environment. `yfinance==1.7.0` is pinned.
- Run `.\.venv\Scripts\python.exe -m scripts.refresh_market_data --start 2024-01-02 --end 2026-10-16`. Start is inclusive; end is exclusive. The arguments are configurable.
- The downloader explicitly requests `auto_adjust=True`, daily observations, no repair, and missing rows retained. It extracts `Close` from either yfinance MultiIndex orientation or a single-ticker flat frame, then validates dates, numeric finite positive prices, duplicate dates and every requested symbol.
- Missing quotes remain missing. Dates use observed market sessions, not a generated weekday calendar. Prices align on common dates, and returns are calculated on the original union date grid before incomplete rows are removed. No zero filling, forward filling or return bridging is applied.
- Successful downloads save `data/market/adjusted_prices.csv`, its JSON metadata and SHA-256 checksum, `aligned_prices.csv`, `daily_returns.csv` and `coverage.csv`. `data/market/` is ignored by Git because downloaded market data should remain local.
- If download or validation fails, only a checksum-valid cache with the **same tickers, exact date range and adjustment basis** is loaded. Run the same command with `--offline` to skip the network explicitly. Without a matching cache, the command fails clearly. The checked-in fictional CSV is never substituted for listed securities.

## Verification result

The live run on 16 October 2026 for 2 January 2024 through 15 October 2026 downloaded all nine series. It found 689 observed union dates, 682 fully aligned price dates, 675 valid daily-return dates and seven dates with at least one missing quote. Gold had two missing observations, the gilt ETF five, and the benchmark one; these were not fabricated. The exact-window `--offline` run reloaded the cache and reproduced those counts. Twelve automated data tests pass, covering flat/MultiIndex columns, a simulated market holiday, missing observations, duplicate dates, invalid prices, failed symbols, cache mismatch/tampering, offline reload and the earlier synthetic path. `pip check` reports no broken requirements.

The fetched series are Yahoo Finance research data, not an exchange-certified feed. Auto-adjusted close handles the adjustment applied by yfinance; provider corrections can change historical values. The Nifty benchmark is a price index, so it does not include the total-return convention needed for a rigorous dividend-aware benchmark comparison. The ETF series reflect their own distribution/adjustment data and should be checked against fund disclosures before investment reporting.

## Primary references

- [yfinance download API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) documents `auto_adjust`, MultiIndex output, missing-row handling and exclusive end dates.
- [yfinance PyPI release](https://pypi.org/project/yfinance/) provides the pinned release and the project's Yahoo data-use disclaimer.
- [NSE Gold BeES listing](https://www.nseindia.com/get-quote/equity/GOLDBEES/Nippon-India-Gold-Bees) and [NSE Long Term Gilt listing](https://www.nseindia.com/get-quote/equity/LTGILTBEES/Nippon-India-ETF-Nifty-8-13-yr-G-Sec-Long-Term-Gilt) identify the two ETF examples.
- [Yahoo Finance Nifty 50 page](https://finance.yahoo.com/quote/%5ENSEI/) identifies `^NSEI` as a price index quote in INR.

## Gate

**P2 passes for the specified local historical-data pipeline.** A fresh clone can run the fictional dashboard offline; a real-price fallback becomes available only after one successful local download for the same requested range. Phase 3 will connect the separate listed portfolio to SQLite. Phase 4 will verify buy-and-hold metrics, and Phase 6 will decide how to present source/coverage in the dashboard.
