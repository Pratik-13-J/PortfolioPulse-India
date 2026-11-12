"""Synthetic, clearly labelled Brinson-Fachler demonstration (not portfolio data).

Real attribution needs NIFTY 50 constituent weights, which are not available,
so the dashboard keeps it gated. This module only feeds the *illustrative*
example with hand-picked synthetic sector weights and returns through the
same tested functions (``brinson_fachler``, ``carino_link``,
``active_share``). Nothing here reads or writes the database.
"""

from __future__ import annotations

import pandas as pd

from portfoliopulse.benchmark_attribution import active_share, brinson_fachler, carino_link

DEMO_LABEL = "ILLUSTRATIVE ONLY: synthetic sector weights and returns, not the PortfolioPulse portfolio or NIFTY 50"

# Period 1 and period 2 sector weights/returns (synthetic numbers chosen for readability).
_PORTFOLIO = [
    pd.DataFrame({"weight": [0.45, 0.30, 0.25], "return": [0.060, 0.020, 0.010]}, index=["Financials", "IT", "Energy"]),
    pd.DataFrame({"weight": [0.40, 0.35, 0.25], "return": [-0.020, 0.030, 0.015]}, index=["Financials", "IT", "Energy"]),
]
_BENCHMARK = [
    pd.DataFrame({"weight": [0.35, 0.15, 0.20, 0.30], "return": [0.050, 0.030, 0.000, 0.020]},
                 index=["Financials", "IT", "Energy", "Consumer"]),
    pd.DataFrame({"weight": [0.35, 0.15, 0.20, 0.30], "return": [-0.010, 0.020, 0.010, 0.005]},
                 index=["Financials", "IT", "Energy", "Consumer"]),
]


def demo() -> dict[str, object]:
    """Single-period effects (period 1), two-period Carino-linked effects, Active Share."""
    periods = [brinson_fachler(p, b) for p, b in zip(_PORTFOLIO, _BENCHMARK)]
    linked = carino_link(periods)
    first = periods[0]
    return {
        "label": DEMO_LABEL,
        "portfolio_sectors": _PORTFOLIO[0],
        "benchmark_sectors": _BENCHMARK[0],
        "single_period": first.effects,
        "portfolio_return": first.portfolio_return,
        "benchmark_return": first.benchmark_return,
        "active_return": first.active_return,
        "linked": linked.effects,
        "linked_active_return": linked.cumulative_portfolio_return - linked.cumulative_benchmark_return,
        "active_share": active_share(_PORTFOLIO[0]["weight"], _BENCHMARK[0]["weight"]),
    }
