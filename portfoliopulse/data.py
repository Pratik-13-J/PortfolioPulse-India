from __future__ import annotations

import numpy as np
import pandas as pd

from portfoliopulse.database import connect, initialize, load_observations, seed_instruments


def ensure_demo_data() -> None:
    initialize()
    seed_instruments()
    with connect() as con:
        count = con.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0]
    if count:
        return

    # Deterministic illustrative paths; these are not real market data.
    dates = pd.bdate_range("2023-01-02", "2026-09-30")
    rng = np.random.default_rng(20261015)
    common = rng.normal(0.00025, 0.009, len(dates))
    idiosyncratic = {
        "NIFTYETF": rng.normal(0.00032, 0.008, len(dates)),
        "BANKETF": rng.normal(0.00038, 0.013, len(dates)),
        "GOLDETF": rng.normal(0.00020, 0.006, len(dates)),
        "DEBTETF": rng.normal(0.00014, 0.0022, len(dates)),
        "NIFTY50": rng.normal(0.00030, 0.008, len(dates)),
    }
    frames = []
    bases = {"NIFTYETF": 220.0, "BANKETF": 480.0, "GOLDETF": 55.0, "DEBTETF": 105.0, "NIFTY50": 18000.0}
    betas = {"NIFTYETF": .85, "BANKETF": 1.25, "GOLDETF": .05, "DEBTETF": -.05, "NIFTY50": 1.0}
    for symbol, base in bases.items():
        daily = betas[symbol] * common + idiosyncratic[symbol]
        prices = base * np.exp(np.cumsum(daily))
        frames.append(pd.DataFrame({"symbol": symbol, "date": dates, "close_value": prices}))
    load_observations(pd.concat(frames, ignore_index=True), source="synthetic_demo")
