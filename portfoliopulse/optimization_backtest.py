"""Walk-forward (out-of-sample) validation of allocation rules.

Rebalance dates are positions t = E, E + H, E + 2H, ... in the valid-return
calendar (E = estimation window, H = holding period). At each rebalance the
rule sees only returns at positions [t - E, t) - strictly before the first
day it will be held - so there is no look-ahead. Between rebalances weights
drift with prices (buy-and-hold within the holding period):
r_t = w_(t-1)' R_t and w_t = w_(t-1) * (1 + R_t) / (1 + r_t).
A rebalance trades from the drifted weights to the new target; its cost
fraction c (from ``CostModel``) is charged on that day: net_t = (1 - c)(1 + r_t) - 1.

Every strategy starts from the existing portfolio's actual market weights on
the day before the first out-of-sample date, so the first trade's cost is
counted. The existing portfolio itself is the realized buy-and-hold return
series (no trades, no costs).

Limits: one fixed five-stock universe chosen today (survivorship / selection
bias cannot be removed), about 1.7 years of out-of-sample data, unverified
price data. The evaluation is indicative, not statistically conclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from portfoliopulse.portfolio_allocation import CostModel
from portfoliopulse.risk_metrics import annualized_return, annualized_volatility, maximum_drawdown, sharpe_ratio

Rule = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class WalkForwardResult:
    status: str  # completed | insufficient_data
    reason: str | None
    estimation_observations: int
    holding_observations: int
    rebalance_dates: list[str] = field(default_factory=list)
    gross: pd.DataFrame | None = None
    net: pd.DataFrame | None = None
    costs: pd.DataFrame | None = None
    turnover: pd.DataFrame | None = None
    weights: dict[str, pd.DataFrame] = field(default_factory=dict)
    failures: dict[str, list[str]] = field(default_factory=dict)
    summary: pd.DataFrame | None = None


def walk_forward(
    asset_returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    existing_returns: pd.Series,
    rules: Mapping[str, Rule],
    *,
    initial_weights: pd.Series,
    cost_model: CostModel,
    estimation: int = 252,
    holding: int = 21,
    annual_risk_free_rate: float = 0.0,
) -> WalkForwardResult:
    n = len(asset_returns)
    if estimation < 2 or holding < 1:
        raise ValueError("estimation must be >= 2 and holding >= 1.")
    if n < estimation + 2 * holding:
        return WalkForwardResult("insufficient_data",
                                 f"{n} valid returns; walk-forward needs at least {estimation + 2 * holding} "
                                 f"(estimation {estimation} + two holding periods of {holding}).",
                                 estimation, holding)
    if not (asset_returns.index.equals(benchmark_returns.index) and asset_returns.index.equals(existing_returns.index)):
        raise ValueError("Asset, benchmark and existing returns must share the same valid dates.")
    values = asset_returns.to_numpy(dtype=float)
    dates = asset_returns.index[estimation:]
    rebalances = list(range(estimation, n, holding))
    columns = list(asset_returns.columns)
    gross, net, costs, turns, weights, failures = {}, {}, {}, {}, {}, {}
    for name, rule in rules.items():
        w = initial_weights.reindex(columns).to_numpy(dtype=float)
        g, nt, c, tv, history, failed = [], [], [], [], {}, []
        for t in range(estimation, n):
            cost = turn = 0.0
            if t in rebalances:
                window = asset_returns.iloc[t - estimation:t]
                try:
                    target = rule(window).reindex(columns).to_numpy(dtype=float)
                    if np.isnan(target).any() or not np.isclose(target.sum(), 1.0, atol=1e-6):
                        raise ValueError("rule returned invalid weights")
                except ValueError as exc:
                    failed.append(f"{asset_returns.index[t]:%Y-%m-%d}: {exc}")
                    target = w
                delta = pd.Series(target - w, index=columns)
                turn = float(np.abs(delta).sum())
                cost = cost_model.turnover_cost_fraction(delta)
                w = target
                history[asset_returns.index[t]] = target
            r = float(w @ values[t])
            g.append(r)
            nt.append((1 - cost) * (1 + r) - 1)
            c.append(cost)
            tv.append(turn)
            w = w * (1 + values[t]) / (1 + r)
        gross[name], net[name], costs[name], turns[name] = g, nt, c, tv
        weights[name] = pd.DataFrame.from_dict(history, orient="index", columns=columns)
        failures[name] = failed
    existing = existing_returns.iloc[estimation:].to_numpy()
    gross["existing"] = net["existing"] = list(existing)
    costs["existing"] = turns["existing"] = [0.0] * len(dates)
    gross_df, net_df = pd.DataFrame(gross, index=dates), pd.DataFrame(net, index=dates)
    bench = benchmark_returns.iloc[estimation:]
    rows = {}
    for name in net_df.columns:
        series = net_df[name]
        rows[name] = {
            "net_annualized_return": annualized_return(series),
            "gross_annualized_return": annualized_return(gross_df[name]),
            "net_annualized_volatility": annualized_volatility(series),
            "net_sharpe": sharpe_ratio(series, annual_risk_free_rate=annual_risk_free_rate),
            "net_max_drawdown": maximum_drawdown(series),
            "total_cost_fraction": float(np.sum(costs[name])),
            "average_rebalance_turnover": float(np.sum(turns[name]) / max(1, len(rebalances)))
            if name != "existing" else 0.0,
            "rebalances": len(rebalances) if name != "existing" else 0,
            "failed_rebalances": len(failures.get(name, [])),
        }
    rows["NIFTY 50 (price index)"] = {
        "net_annualized_return": annualized_return(bench), "gross_annualized_return": annualized_return(bench),
        "net_annualized_volatility": annualized_volatility(bench),
        "net_sharpe": sharpe_ratio(bench, annual_risk_free_rate=annual_risk_free_rate),
        "net_max_drawdown": maximum_drawdown(bench), "total_cost_fraction": 0.0,
        "average_rebalance_turnover": 0.0, "rebalances": 0, "failed_rebalances": 0,
    }
    return WalkForwardResult(
        "completed", None, estimation, holding,
        [f"{asset_returns.index[t]:%Y-%m-%d}" for t in rebalances], gross_df, net_df,
        pd.DataFrame(costs, index=dates), pd.DataFrame(turns, index=dates), weights, failures,
        pd.DataFrame.from_dict(rows, orient="index"),
    )
