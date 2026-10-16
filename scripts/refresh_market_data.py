"""Download real Indian sample prices or use an exact matching local CSV cache."""

from __future__ import annotations

import argparse
from datetime import date

from portfoliopulse.config import MARKET_CSV_PATH
from portfoliopulse.market_data import load_cached_market_prices, refresh_market_prices
from portfoliopulse.real_portfolio import REAL_SYMBOLS


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh adjusted Indian sample prices.")
    parser.add_argument("--start", default="2024-01-02", help="Inclusive YYYY-MM-DD date")
    parser.add_argument("--end", default=date.today().isoformat(), help="Exclusive YYYY-MM-DD date")
    parser.add_argument("--offline", action="store_true", help="Use the previously downloaded matching CSV without network access")
    args = parser.parse_args()
    if args.offline:
        result = load_cached_market_prices(REAL_SYMBOLS, args.start, args.end, MARKET_CSV_PATH)
    else:
        result = refresh_market_prices(REAL_SYMBOLS, args.start, args.end, MARKET_CSV_PATH)
    output = MARKET_CSV_PATH.parent
    result.prices.to_csv(output / "aligned_prices.csv", index_label="date")
    result.daily_returns.to_csv(output / "daily_returns.csv", index_label="date")
    result.coverage.to_csv(output / "coverage.csv", index=False)
    print(f"Source: {result.source}; CSV fallback: {result.used_fallback}")
    print(f"Assets: {len(REAL_SYMBOLS)}; aligned prices: {len(result.prices)} dates; "
          f"daily returns: {len(result.daily_returns)} dates; incomplete dates: {result.dropped_price_dates}")
    print(result.coverage.to_string(index=False))
    print(f"Files saved under: {output}")


if __name__ == "__main__":
    main()
