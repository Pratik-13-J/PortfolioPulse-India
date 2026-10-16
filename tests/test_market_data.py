"""Phase 2 checks for adjusted downloads and trustworthy fallback."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from portfoliopulse.config import PROJECT_ROOT
from portfoliopulse.data import PriceDataError
from portfoliopulse.market_data import download_adjusted_observations, load_cached_market_prices, refresh_market_prices
from portfoliopulse.real_portfolio import REAL_INSTRUMENTS


DATES = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"])
SYMBOLS = ["AAA.NS", "BBB.NS"]


def market_frame(*, ticker_first: bool = False) -> pd.DataFrame:
    columns = pd.MultiIndex.from_product([["Close", "Open"], SYMBOLS], names=["Price", "Ticker"])
    rows = [
        [100, 200, 99, 199],
        [110, 220, 109, 219],
        [120, np.nan, 119, np.nan],
        [130, 260, 129, 259],
    ]
    frame = pd.DataFrame(rows, index=DATES, columns=columns)
    return frame.swaplevel(0, 1, axis=1) if ticker_first else frame


class MarketDataTests(unittest.TestCase):
    def test_multiindex_adjusted_close_and_missing_return_gap(self) -> None:
        for ticker_first in (False, True):
            def downloader(**kwargs):
                self.assertTrue(kwargs["auto_adjust"])
                self.assertTrue(kwargs["keepna"])
                self.assertFalse(kwargs["repair"])
                self.assertEqual(kwargs["interval"], "1d")
                return market_frame(ticker_first=ticker_first)

            with self.subTest(ticker_first=ticker_first), TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
                result = refresh_market_prices(SYMBOLS, "2026-01-01", "2026-01-07",
                                               Path(directory) / "prices.csv", downloader=downloader)
                self.assertFalse(result.used_fallback)
                self.assertEqual(result.dropped_price_dates, 1)
                self.assertEqual(result.prices.index.strftime("%Y-%m-%d").tolist(),
                                 ["2026-01-01", "2026-01-02", "2026-01-06"])
                self.assertEqual(result.daily_returns.index.strftime("%Y-%m-%d").tolist(), ["2026-01-02"])
                self.assertAlmostEqual(float(result.daily_returns.loc["2026-01-02", "AAA.NS"]), 0.1)

    def test_single_ticker_flat_columns_and_market_holiday(self) -> None:
        frame = pd.DataFrame({"Close": [100, 105], "Open": [99, 104]},
                             index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
        result = download_adjusted_observations(["AAA.NS"], "2026-01-01", "2026-01-06",
                                                downloader=lambda **_: frame)
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").tolist(),
                         ["2026-01-02", "2026-01-05"])
        self.assertEqual(result["close_value"].tolist(), [100, 105])

    def test_duplicate_dates_invalid_prices_and_failed_ticker_are_rejected(self) -> None:
        bad_frames = [
            market_frame().set_axis(pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-05", "2026-01-06"])),
            market_frame().copy(),
            market_frame().copy(),
        ]
        bad_frames[1].loc[DATES[0], ("Close", "AAA.NS")] = 0
        bad_frames[2].loc[:, ("Close", "BBB.NS")] = np.nan
        for frame in bad_frames:
            with self.subTest(frame=frame), self.assertRaises(PriceDataError):
                download_adjusted_observations(SYMBOLS, "2026-01-01", "2026-01-07",
                                               downloader=lambda **_: frame)

    def test_failure_uses_only_matching_untampered_cache(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            cache = Path(directory) / "prices.csv"
            first = refresh_market_prices(SYMBOLS, "2026-01-01", "2026-01-07", cache,
                                          downloader=lambda **_: market_frame())

            def unavailable(**_):
                raise ConnectionError("offline")

            cached = refresh_market_prices(SYMBOLS, "2026-01-01", "2026-01-07", cache,
                                           downloader=unavailable)
            self.assertTrue(cached.used_fallback)
            pd.testing.assert_frame_equal(first.prices, cached.prices, check_freq=False)
            offline = load_cached_market_prices(SYMBOLS, "2026-01-01", "2026-01-07", cache)
            self.assertTrue(offline.used_fallback)
            pd.testing.assert_frame_equal(first.daily_returns, offline.daily_returns, check_freq=False)
            with self.assertRaisesRegex(PriceDataError, "do not match"):
                refresh_market_prices(SYMBOLS, "2026-01-01", "2026-01-08", cache,
                                      downloader=unavailable)
            cache.write_text(cache.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(PriceDataError, "integrity"):
                refresh_market_prices(SYMBOLS, "2026-01-01", "2026-01-07", cache,
                                      downloader=unavailable)

    def test_failed_download_without_cache_is_explicit(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT / ".tmp") as directory:
            with self.assertRaisesRegex(PriceDataError, "fallback unavailable"):
                refresh_market_prices(SYMBOLS, "2026-01-01", "2026-01-07",
                                      Path(directory) / "prices.csv",
                                      downloader=lambda **_: pd.DataFrame())

    def test_real_sample_portfolio_is_separate_from_demo(self) -> None:
        holdings = [item for item in REAL_INSTRUMENTS if item.asset_class != "Benchmark"]
        self.assertEqual(len(holdings), 8)
        self.assertAlmostEqual(sum(item.target_weight for item in holdings), 1.0)
        self.assertTrue(all(item.quantity > 0 and not item.symbol.startswith("DEMO_") for item in holdings))


if __name__ == "__main__":
    unittest.main()
