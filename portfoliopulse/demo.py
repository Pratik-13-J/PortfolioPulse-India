"""Clearly fictional Indian-market portfolio and reproducible demo prices."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DemoInstrument:
    symbol: str
    name: str
    asset_class: str
    sector: str
    quantity: float
    target_weight: float
    starting_price_inr: float
    market_beta: float
    daily_drift: float
    daily_idiosyncratic_volatility: float


DEMO_INSTRUMENTS = (
    DemoInstrument("DEMO_ENERGY", "Sample Indian energy stock", "Equity", "Energy", 60, .18, 2800, 1.10, .00025, .012),
    DemoInstrument("DEMO_BANK", "Sample Indian bank stock", "Equity", "Banking", 120, .18, 1500, 1.20, .00025, .013),
    DemoInstrument("DEMO_IT", "Sample Indian IT stock", "Equity", "Information Technology", 85, .14, 1600, .95, .00030, .011),
    DemoInstrument("DEMO_HEALTH", "Sample Indian healthcare stock", "Equity", "Healthcare", 85, .10, 1200, .75, .00020, .009),
    DemoInstrument("DEMO_FMCG", "Sample Indian consumer stock", "Equity", "Consumer Staples", 40, .10, 2500, .60, .00018, .007),
    DemoInstrument("DEMO_AUTO", "Sample Indian auto stock", "Equity", "Automobiles", 125, .10, 800, 1.15, .00023, .012),
    DemoInstrument("DEMO_GOLD", "Sample gold ETF", "Gold", "Gold", 1667, .10, 60, .05, .00018, .006),
    DemoInstrument("DEMO_GILT", "Sample government bond ETF", "Debt", "Government Debt", 1000, .10, 100, -.05, .00012, .002),
    DemoInstrument("DEMO_NIFTY50", "Sample Nifty 50-style benchmark", "Benchmark", "Broad Market", 0, 0, 20000, 1.0, .00022, .002),
)

DEMO_SOURCE = "synthetic_india_demo_v1"
DEMO_START = "2024-01-02"
DEMO_END = "2026-09-30"
DEMO_SEED = 20261016


def generate_demo_prices() -> pd.DataFrame:
    """Build fictional INR prices on weekdays; dates are not an NSE calendar."""
    dates = pd.bdate_range(DEMO_START, DEMO_END)
    rng = np.random.default_rng(DEMO_SEED)
    market = rng.normal(0.0002, 0.008, len(dates))
    frames: list[pd.DataFrame] = []
    for instrument in DEMO_INSTRUMENTS:
        daily_log_return = (
            instrument.daily_drift
            + instrument.market_beta * market
            + rng.normal(0, instrument.daily_idiosyncratic_volatility, len(dates))
        )
        prices = instrument.starting_price_inr * np.exp(np.cumsum(daily_log_return))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": instrument.symbol,
                    "date": dates.strftime("%Y-%m-%d"),
                    "close_value": prices,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)
