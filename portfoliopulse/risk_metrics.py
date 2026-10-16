"""Pure risk calculations for a dated stream of daily *price* returns.

The caller decides how portfolio returns are constructed (for example, fixed
quantities or daily rebalancing) and aligns them with a benchmark. These
functions do not infer dividends or corporate actions from unadjusted closes.
All rates and loss figures are decimal fractions, not percentages or INR.
"""

from __future__ import annotations

from collections.abc import Sequence
from numbers import Real

import numpy as np
import pandas as pd

from portfoliopulse.config import TRADING_DAYS_PER_YEAR


def _trading_days(value: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 1:
        raise ValueError("trading_days_per_year must be a positive integer.")
    return int(value)


def _finite_rate(value: float, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    return float(value)


def _returns(returns: pd.Series, *, minimum: int = 1) -> pd.Series:
    if not isinstance(returns, pd.Series):
        raise TypeError("daily_returns must be a pandas Series.")
    if len(returns) < minimum:
        raise ValueError(f"At least {minimum} daily return observation(s) are required.")
    if returns.index.has_duplicates or not returns.index.is_monotonic_increasing or returns.index.hasnans:
        raise ValueError("Daily return dates must be unique, nonmissing, and increasing.")
    if returns.isna().any() or returns.map(lambda item: isinstance(item, (bool, np.bool_))).any():
        raise ValueError("Daily returns must be finite numeric values without missing observations.")
    try:
        clean = pd.to_numeric(returns, errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Daily returns must be finite numeric values.") from exc
    if not np.isfinite(clean.to_numpy()).all():
        raise ValueError("Daily returns must be finite numeric values.")
    if (clean < -1).any():
        raise ValueError("A simple daily return cannot be below -100%.")
    return clean


def _asset_returns(asset_returns: pd.DataFrame, *, minimum: int = 2) -> pd.DataFrame:
    if not isinstance(asset_returns, pd.DataFrame):
        raise TypeError("asset_returns must be a pandas DataFrame.")
    if asset_returns.shape[0] < minimum or asset_returns.shape[1] < 1:
        raise ValueError(f"At least {minimum} dates and one asset are required.")
    if asset_returns.columns.has_duplicates:
        raise ValueError("Asset return columns must be unique.")
    if asset_returns.index.has_duplicates or not asset_returns.index.is_monotonic_increasing or asset_returns.index.hasnans:
        raise ValueError("Asset return dates must be unique, nonmissing, and increasing.")
    if asset_returns.isna().any().any():
        raise ValueError("Asset returns cannot contain missing observations.")
    if asset_returns.map(lambda item: isinstance(item, (bool, np.bool_))).any().any():
        raise ValueError("Asset returns must be numeric, not Boolean.")
    try:
        clean = asset_returns.apply(pd.to_numeric, errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Asset returns must be finite numeric values.") from exc
    if not np.isfinite(clean.to_numpy()).all():
        raise ValueError("Asset returns must be finite numeric values.")
    if (clean < -1).any().any():
        raise ValueError("A simple daily return cannot be below -100%.")
    return clean


def _sample_daily_volatility(returns: pd.Series) -> float:
    # Center around the first observation before subtracting the mean. This
    # keeps exactly constant decimal streams at exactly zero despite the
    # floating-point summation in a direct std([0.1, 0.1, 0.1]).
    values = returns.to_numpy(dtype=float)
    return float((values - values[0]).std(ddof=1))


def cumulative_returns(daily_returns: pd.Series) -> pd.Series:
    """Compound daily returns; first value includes the first day's return."""
    returns = _returns(daily_returns)
    return ((1.0 + returns).cumprod() - 1.0).rename("cumulative_return")


def annualized_return(
    daily_returns: pd.Series, *, trading_days_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """Geometric annual rate: (product(1 + r_t)) ** (252 / N) - 1.

    N counts valid daily return intervals, not calendar days. The trading-day
    constant is configurable for hand checks but defaults to 252. If a
    portfolio instead reports first-to-last price growth over its full window,
    that endpoint statistic can differ when return intervals were excluded.
    """
    returns = _returns(daily_returns)
    days = _trading_days(trading_days_per_year)
    if (returns == -1).any():
        return -1.0
    return float(np.expm1(np.log1p(returns.to_numpy()).sum() * days / len(returns)))


def annualized_volatility(
    daily_returns: pd.Series, *, trading_days_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """Sample daily standard deviation (ddof=1), multiplied by sqrt(252)."""
    returns = _returns(daily_returns, minimum=2)
    days = _trading_days(trading_days_per_year)
    return float(_sample_daily_volatility(returns) * np.sqrt(days))


def sharpe_ratio(
    daily_returns: pd.Series,
    *,
    annual_risk_free_rate: float = 0.0,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Annualized mean daily excess return divided by sample daily volatility.

    The annual INR risk-free rate is an effective annual decimal rate and is
    converted to an effective daily rate, (1 + annual_rate) ** (1/252) - 1.
    A zero-volatility return stream has no defined Sharpe ratio (``None``).
    """
    returns = _returns(daily_returns, minimum=2)
    days = _trading_days(trading_days_per_year)
    annual_rf = _finite_rate(annual_risk_free_rate, "annual_risk_free_rate")
    if annual_rf <= -1:
        raise ValueError("annual_risk_free_rate must be greater than -100%.")
    daily_volatility = _sample_daily_volatility(returns)
    if daily_volatility == 0:
        return None
    daily_rf = float(np.expm1(np.log1p(annual_rf) / days))
    return float((returns.mean() - daily_rf) / daily_volatility * np.sqrt(days))


def drawdown_series(daily_returns: pd.Series) -> pd.Series:
    """Wealth divided by its running peak, with initial wealth 1 included."""
    returns = _returns(daily_returns)
    wealth = (1.0 + returns).cumprod()
    peak = wealth.cummax().clip(lower=1.0)
    return (wealth / peak - 1.0).rename("drawdown")


def maximum_drawdown(daily_returns: pd.Series) -> float:
    """Most negative drawdown, in [-1, 0]; zero means no drawdown."""
    return float(drawdown_series(daily_returns).min())


def historical_var_es(
    daily_returns: pd.Series, *, confidence: float = 0.95
) -> tuple[float, float]:
    """Return empirical one-day (VaR, ES) as nonnegative loss magnitudes.

    With N observations, k = ceil((1 - confidence) * N), at least one. VaR is
    the loss of the kth worst daily return; ES averages the worst k returns.
    A fixed observation count handles ties deterministically. Positive-only
    tails are floored at zero. Small samples give coarse tail estimates.
    """
    returns = _returns(daily_returns, minimum=2)
    level = _finite_rate(confidence, "confidence")
    if not 0 < level < 1:
        raise ValueError("confidence must lie strictly between zero and one.")
    ordered = np.sort(returns.to_numpy())
    # Avoid ceil(1.0000000000000009) becoming two at 95% of 20 observations.
    tail_count = max(1, int(np.ceil((1.0 - level) * len(ordered) - 1e-12)))
    return max(0.0, -float(ordered[tail_count - 1])), max(0.0, -float(ordered[:tail_count].mean()))


def asset_correlations(asset_returns: pd.DataFrame) -> pd.DataFrame:
    """Pairwise sample correlations on fully aligned dates; constants are NaN."""
    clean = _asset_returns(asset_returns)
    return clean.corr(min_periods=2)


def volatility_contributions(
    asset_returns: pd.DataFrame,
    weights: Sequence[float] | pd.Series,
    *,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> pd.DataFrame:
    """Marginal and component annualized volatility for one weight snapshot.

    For annualized sample covariance S and portfolio volatility s:
    marginal_i = (S w)_i / s, component_i = w_i * marginal_i. Components sum
    to s, and percent contributions sum to one. The same-date weight vector
    must be long-only, sum to one, and follow ``asset_returns`` column order.
    Zero proxy volatility raises ``ValueError`` because marginal/component
    shares are undefined.
    This current-weight covariance proxy does not equal realized volatility
    of a buy-and-hold portfolio whose weights drifted through the window.
    """
    clean = _asset_returns(asset_returns)
    days = _trading_days(trading_days_per_year)
    if isinstance(weights, pd.Series) and not weights.index.equals(clean.columns):
        raise ValueError("Weight labels and order must match asset return columns.")
    if isinstance(weights, (str, bytes)):
        raise ValueError("weights must be one long-only weight per asset.")
    try:
        weight_list = list(weights)
    except TypeError as exc:
        raise ValueError("weights must be one long-only weight per asset.") from exc
    if any(isinstance(weight, (bool, np.bool_)) for weight in weight_list):
        raise ValueError("weights must be numeric, not Boolean.")
    try:
        weight_array = np.asarray(weight_list, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("weights must be finite numeric values.") from exc
    if weight_array.ndim != 1 or len(weight_array) != clean.shape[1]:
        raise ValueError("weights must be one long-only weight per asset.")
    if not np.isfinite(weight_array).all() or np.any(weight_array < 0):
        raise ValueError("weights must be finite and long-only.")
    if not np.isclose(weight_array.sum(), 1.0, rtol=0, atol=1e-8):
        raise ValueError("Long-only weights must sum to one.")
    values = clean.to_numpy(dtype=float)
    centered = values - values[0]
    centered -= centered.mean(axis=0)
    covariance = (centered.T @ centered) * (days / (len(clean) - 1))
    covariance_weighted = covariance @ weight_array
    variance = float(weight_array @ covariance_weighted)
    if variance < -1e-12:
        raise ValueError("The asset-return covariance produced a negative portfolio variance.")
    portfolio_volatility = float(np.sqrt(max(0.0, variance)))
    if portfolio_volatility == 0:
        raise ValueError("Portfolio volatility is zero; risk contributions are undefined.")
    marginal = covariance_weighted / portfolio_volatility
    component = weight_array * marginal
    percent = component / portfolio_volatility
    return pd.DataFrame({
        "weight": weight_array,
        "marginal_annual_volatility": marginal,
        "component_annual_volatility": component,
        "percent_contribution": percent,
    }, index=clean.columns)
