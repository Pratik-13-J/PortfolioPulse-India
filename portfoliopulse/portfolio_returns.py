"""Buy-and-hold INR price returns on an observed Indian-market date grid.

The input closes must already be source-validated. This module never fills a
missing close, assumes rebalancing, or interprets unadjusted prices as total
returns. The benchmark is a comparison series, not a portfolio holding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from portfoliopulse.config import TRADING_DAYS_PER_YEAR


class PortfolioReturnError(ValueError):
    """The price grid cannot support a faithful buy-and-hold comparison."""


@dataclass(frozen=True)
class BuyAndHoldReturns:
    price_grid: pd.DataFrame
    holding_values_inr: pd.DataFrame
    portfolio_value_inr: pd.Series
    market_weights: pd.DataFrame
    asset_daily_returns: pd.DataFrame
    daily_return_contributions: pd.DataFrame
    daily_comparison_returns: pd.DataFrame
    cumulative_portfolio_return: pd.Series
    cumulative_comparison_returns: pd.DataFrame
    endpoint_annualized_portfolio_return: float
    endpoint_annualized_benchmark_return: float
    observed_union_intervals: int
    excluded_return_dates: tuple[str, ...]


def annualize_endpoint_price_return(initial: float, final: float, observed_intervals: int) -> float:
    """Geometric endpoint price return with 252 observed sessions per year.

    The interval count is from the observed union calendar between the two
    complete endpoints. It includes dates where an intermediate benchmark
    quote is missing; no missing *daily* return is calculated from endpoints.
    """
    if (
        isinstance(observed_intervals, bool) or not isinstance(observed_intervals, int)
        or observed_intervals < 1 or not math.isfinite(initial)
        or not math.isfinite(final) or initial <= 0 or final <= 0
    ):
        raise PortfolioReturnError("Endpoint values must be positive and observed intervals positive.")
    return float((final / initial) ** (TRADING_DAYS_PER_YEAR / observed_intervals) - 1.0)


def build_buy_and_hold_returns(
    observed_closes: pd.DataFrame,
    quantities: Mapping[str, float],
    benchmark_symbol: str,
) -> BuyAndHoldReturns:
    """Calculate drifting-weight returns and same-date benchmark comparisons.

    `observed_closes` has one row per observed date and may contain missing
    quotes. A one-day return is kept only if every holding and the benchmark
    have closes on that date and its immediately preceding observed date.
    Cumulative levels use actual portfolio/benchmark prices from the first
    complete shared date and retain NaN at an incomplete date. Their endpoint
    annualization includes all observed union intervals between endpoints;
    daily risk samples exclude any transition touching a missing quote.
    """
    if not isinstance(observed_closes, pd.DataFrame) or observed_closes.empty:
        raise PortfolioReturnError("Provide a non-empty observed close-price grid.")
    if observed_closes.columns.has_duplicates or observed_closes.index.has_duplicates:
        raise PortfolioReturnError("Price symbols and observed dates must be unique.")
    if not isinstance(observed_closes.index, pd.DatetimeIndex):
        raise PortfolioReturnError("Observed dates must be a DatetimeIndex.")
    if (observed_closes.index.tz is not None or observed_closes.index.hasnans
            or not observed_closes.index.equals(observed_closes.index.normalize())):
        raise PortfolioReturnError("Observed dates must be timezone-free calendar dates.")
    if not isinstance(quantities, Mapping) or not quantities:
        raise PortfolioReturnError("Provide positive fixed quantities for the holdings.")
    if not isinstance(benchmark_symbol, str) or not benchmark_symbol.strip():
        raise PortfolioReturnError("Provide a named benchmark symbol.")
    symbols = list(quantities)
    if benchmark_symbol in quantities or "portfolio" in quantities or benchmark_symbol == "portfolio":
        raise PortfolioReturnError("The benchmark and portfolio label cannot be holdings.")
    if any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols):
        raise PortfolioReturnError("Holding symbols must be non-empty strings.")
    if not set([*symbols, benchmark_symbol]).issubset(observed_closes.columns):
        raise PortfolioReturnError("The price grid needs every holding and the benchmark.")
    clean_quantities: dict[str, float] = {}
    for symbol, quantity in quantities.items():
        if isinstance(quantity, (bool, np.bool_)):
            raise PortfolioReturnError("Holding quantities must be positive finite numbers.")
        try:
            number = float(quantity)
        except (TypeError, ValueError) as exc:
            raise PortfolioReturnError("Holding quantities must be positive finite numbers.") from exc
        if not math.isfinite(number) or number <= 0:
            raise PortfolioReturnError("Holding quantities must be positive finite numbers.")
        clean_quantities[symbol] = number

    grid = observed_closes.loc[:, [*symbols, benchmark_symbol]].sort_index().copy()
    for symbol in grid.columns:
        if grid[symbol].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise PortfolioReturnError("Closes must be numeric prices, not Boolean values.")
        try:
            values = pd.to_numeric(grid[symbol], errors="raise").to_numpy(dtype=float, na_value=np.nan)
        except (TypeError, ValueError) as exc:
            raise PortfolioReturnError(f"{symbol} closes must be numeric.") from exc
        if np.isinf(values).any() or (values[~np.isnan(values)] <= 0).any():
            raise PortfolioReturnError(f"{symbol} closes must be positive and finite when observed.")
        grid[symbol] = values
    if grid.isna().all(axis=1).any():
        raise PortfolioReturnError("An observed date cannot have every configured quote missing.")

    holding_values = grid[symbols].mul(pd.Series(clean_quantities), axis=1)
    portfolio_value = holding_values.sum(axis=1, min_count=len(symbols)).rename("portfolio_value_inr")
    complete_prices = grid.notna().all(axis=1)
    complete_dates = grid.index[complete_prices]
    if len(complete_dates) < 2:
        raise PortfolioReturnError("At least two shared portfolio and benchmark price dates are required.")

    grid_returns = grid.pct_change(fill_method=None)
    valid_returns = grid_returns.notna().all(axis=1)
    daily_dates = grid.index[valid_returns]
    if len(daily_dates) < 2:
        raise PortfolioReturnError("At least two valid shared one-day returns are required for Phase 4 risk.")
    asset_returns = grid_returns.loc[daily_dates, symbols]
    portfolio_returns_grid = portfolio_value.pct_change(fill_method=None)
    benchmark_returns_grid = grid_returns[benchmark_symbol]
    daily_comparison = pd.DataFrame({
        "portfolio": portfolio_returns_grid.loc[daily_dates],
        benchmark_symbol: benchmark_returns_grid.loc[daily_dates],
    }, index=daily_dates)

    # Lagged market-value weights ensure contributions reflect fixed quantities
    # and reconcile to the actual portfolio return at each valid transition.
    all_weights = holding_values.div(portfolio_value, axis=0)
    contributions = all_weights.shift(1).mul(grid_returns[symbols]).loc[daily_dates]
    if not np.allclose(
        contributions.sum(axis=1).to_numpy(), daily_comparison["portfolio"].to_numpy(),
        rtol=1e-10, atol=1e-12,
    ):
        raise PortfolioReturnError("Holding return contributions do not reconcile to portfolio returns.")
    # Portfolio allocation exists whenever every holding has a close, even if
    # the independent benchmark is missing on that date.
    weights = all_weights.dropna(how="any").copy()

    first, last = complete_dates[0], complete_dates[-1]
    observed_intervals = len(grid.loc[first:last]) - 1
    first_value = float(portfolio_value.loc[first])
    last_value = float(portfolio_value.loc[last])
    first_benchmark = float(grid.at[first, benchmark_symbol])
    last_benchmark = float(grid.at[last, benchmark_symbol])
    cumulative_portfolio = portfolio_value.loc[first:last].div(first_value).sub(1.0).rename(
        "portfolio_cumulative_price_return"
    )
    cumulative = pd.DataFrame({
        "portfolio": portfolio_value.where(complete_prices).div(first_value).sub(1.0),
        benchmark_symbol: grid[benchmark_symbol].where(complete_prices).div(first_benchmark).sub(1.0),
    }, index=grid.index)
    return BuyAndHoldReturns(
        price_grid=grid,
        holding_values_inr=holding_values,
        portfolio_value_inr=portfolio_value,
        market_weights=weights,
        asset_daily_returns=asset_returns,
        daily_return_contributions=contributions,
        daily_comparison_returns=daily_comparison,
        cumulative_portfolio_return=cumulative_portfolio,
        cumulative_comparison_returns=cumulative,
        endpoint_annualized_portfolio_return=annualize_endpoint_price_return(
            first_value, last_value, observed_intervals,
        ),
        endpoint_annualized_benchmark_return=annualize_endpoint_price_return(
            first_benchmark, last_benchmark, observed_intervals,
        ),
        observed_union_intervals=observed_intervals,
        excluded_return_dates=tuple(day.strftime("%Y-%m-%d") for day in grid.index[~valid_returns]),
    )
