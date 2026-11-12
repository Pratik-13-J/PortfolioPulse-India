"""Relative performance analytics on matched daily *price* returns (Phase 2).

All comparisons are "price return vs NIFTY 50 price index" until a total-
return index exists. Inputs are already-validated daily return streams on
the same valid dates (``BuyAndHoldReturns.daily_comparison_returns``); this
module never fills gaps. Undefined statistics return ``None`` and the
orchestrator records why.

Formulas (r_p portfolio, r_b benchmark, a = r_p - r_b, N observations):

* TE   = std(a, ddof=1) * sqrt(252)
* IR   = mean(a) * 252 / TE                           (None when TE == 0)
* beta = cov(r_p, r_b, ddof=1) / var(r_b, ddof=1)     (None when var == 0)
* downside beta: the same on days with r_b < 0         (needs >= 2 such days)
* up/down capture: geometric mean of r_p over geometric mean of r_b on days
  with r_b > 0 (up) or r_b < 0 (down); days with r_b == 0 are in neither
* Sortino = mean(r - rf_d) * 252 / (sqrt(mean(min(r - rf_d, 0)^2)) * sqrt(252))
  where rf_d = (1 + rf_annual)^(1/252) - 1 and the mean of squares uses all N
* contribution_i,t = (H_i,t - H_i,t-1) / V_t-1 = w_i,t-1 * r_i,t, which sums
  to r_p,t; linked over time with Carino factors so the linked
  contributions sum exactly to the compounded portfolio return.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from portfoliopulse.config import TRADING_DAYS_PER_YEAR
from portfoliopulse.risk_metrics import _finite_rate, _returns

ROLLING_WINDOWS = (63, 126, 252)
PERIODS = ("1m", "3m", "6m", "ytd", "1y")
_PERIOD_OFFSETS = {"1m": pd.DateOffset(months=1), "3m": pd.DateOffset(months=3),
                   "6m": pd.DateOffset(months=6), "1y": pd.DateOffset(years=1)}


def _paired(portfolio: pd.Series, benchmark: pd.Series, *, minimum: int = 2) -> tuple[np.ndarray, np.ndarray]:
    p = _returns(portfolio, minimum=minimum)
    b = _returns(benchmark, minimum=minimum)
    if not p.index.equals(b.index):
        raise ValueError("Portfolio and benchmark returns must share exactly the same dates.")
    return p.to_numpy(dtype=float), b.to_numpy(dtype=float)


def _centered(values: np.ndarray) -> np.ndarray:
    # Shift by the first value before demeaning (as risk_metrics does) so an
    # exactly constant stream yields exactly zero dispersion.
    shifted = values - values[0]
    return shifted - shifted.mean()


def _sample_cov(x: np.ndarray, y: np.ndarray) -> float:
    return float((_centered(x) @ _centered(y)) / (len(x) - 1))


def tracking_error(portfolio: pd.Series, benchmark: pd.Series, *,
                   trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    p, b = _paired(portfolio, benchmark)
    active = p - b
    return float(math.sqrt(_sample_cov(active, active)) * math.sqrt(trading_days_per_year))


def annualized_active_return(portfolio: pd.Series, benchmark: pd.Series, *,
                             trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Arithmetic mean daily active return times 252 (the IR numerator)."""
    p, b = _paired(portfolio, benchmark)
    return float((p - b).mean() * trading_days_per_year)


def information_ratio(portfolio: pd.Series, benchmark: pd.Series, *,
                      trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float | None:
    te = tracking_error(portfolio, benchmark, trading_days_per_year=trading_days_per_year)
    if te == 0:
        return None
    return annualized_active_return(portfolio, benchmark, trading_days_per_year=trading_days_per_year) / te


def beta(portfolio: pd.Series, benchmark: pd.Series) -> float | None:
    p, b = _paired(portfolio, benchmark)
    variance = _sample_cov(b, b)
    return None if variance == 0 else _sample_cov(p, b) / variance


def downside_beta(portfolio: pd.Series, benchmark: pd.Series) -> float | None:
    p, b = _paired(portfolio, benchmark)
    mask = b < 0
    if mask.sum() < 2:
        return None
    variance = _sample_cov(b[mask], b[mask])
    return None if variance == 0 else _sample_cov(p[mask], b[mask]) / variance


def _geometric_mean(values: np.ndarray) -> float:
    return float(np.expm1(np.log1p(values).mean()))


def capture_ratios(portfolio: pd.Series, benchmark: pd.Series) -> tuple[float | None, float | None]:
    """Geometric up-market and down-market capture ratios (decimal, 1.0 = 100%)."""
    p, b = _paired(portfolio, benchmark, minimum=1)
    if (p == -1).any() or (b == -1).any():
        raise ValueError("Capture ratios are undefined when a daily return is -100%.")
    results: list[float | None] = []
    for mask in (b > 0, b < 0):
        if not mask.any():
            results.append(None)
            continue
        denominator = _geometric_mean(b[mask])
        results.append(None if denominator == 0 else _geometric_mean(p[mask]) / denominator)
    return results[0], results[1]


def sortino_ratio(daily_returns: pd.Series, *, annual_risk_free_rate: float = 0.0,
                  trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float | None:
    returns = _returns(daily_returns, minimum=2).to_numpy(dtype=float)
    rf = _finite_rate(annual_risk_free_rate, "annual_risk_free_rate")
    if rf <= -1:
        raise ValueError("annual_risk_free_rate must be greater than -100%.")
    daily_rf = float(np.expm1(np.log1p(rf) / trading_days_per_year))
    excess = returns - daily_rf
    downside = math.sqrt(float(np.mean(np.minimum(excess, 0.0) ** 2)))
    if downside == 0:
        return None
    return float(excess.mean() * trading_days_per_year / (downside * math.sqrt(trading_days_per_year)))


def rolling_relative_metrics(portfolio: pd.Series, benchmark: pd.Series, window: int, *,
                             trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> pd.DataFrame:
    """Rolling TE, beta and compounded returns over ``window`` valid observations.

    Windows count *valid paired return observations*, not calendar sessions;
    an excluded date shortens no window but is simply absent from it.
    """
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError("window must be an integer of at least 2 observations.")
    _paired(portfolio, benchmark)
    p, b = portfolio.astype(float), benchmark.astype(float)
    active = p - b
    frame = pd.DataFrame({
        "tracking_error": active.rolling(window).std(ddof=1) * math.sqrt(trading_days_per_year),
        "beta": p.rolling(window).cov(b, ddof=1) / b.rolling(window).var(ddof=1),
        "portfolio_return": np.expm1(np.log1p(p).rolling(window).sum()),
        "benchmark_return": np.expm1(np.log1p(b).rolling(window).sum()),
    }, index=portfolio.index)
    return frame.dropna(how="all")


def period_returns(levels: pd.DataFrame, *, periods: Sequence[str] = PERIODS) -> dict[str, dict[str, object] | None]:
    """Point-to-point returns ending at the last complete common date.

    ``levels`` has one row per complete common date (no NaN) and one column
    per series (e.g. portfolio INR value and benchmark index level). The
    anchor is the last observed date on or before (as_of - offset); YTD
    anchors on the last observed date on or before 31 December of the prior
    year. A period longer than the history returns ``None``.
    """
    if not isinstance(levels, pd.DataFrame) or len(levels) < 2 or levels.isna().any().any():
        raise ValueError("Provide at least two complete level rows without missing values.")
    if not isinstance(levels.index, pd.DatetimeIndex) or not levels.index.is_monotonic_increasing:
        raise ValueError("Levels need an increasing DatetimeIndex.")
    if (levels <= 0).any().any():
        raise ValueError("Levels must be positive.")
    as_of = levels.index[-1]
    result: dict[str, dict[str, object] | None] = {}
    for period in periods:
        if period == "ytd":
            target = pd.Timestamp(year=as_of.year - 1, month=12, day=31)
        elif period in _PERIOD_OFFSETS:
            target = as_of - _PERIOD_OFFSETS[period]
        else:
            raise ValueError(f"Unsupported period {period!r}.")
        eligible = levels.index[levels.index <= target]
        if len(eligible) == 0:
            result[period] = None
            continue
        anchor = eligible[-1]
        result[period] = {
            "anchor_date": anchor.strftime("%Y-%m-%d"),
            "as_of_date": as_of.strftime("%Y-%m-%d"),
            **{str(column): float(levels.at[as_of, column] / levels.at[anchor, column] - 1.0)
               for column in levels.columns},
        }
    return result


def carino_factor(value: float) -> float:
    """ln(1 + r) / r, with the limit 1 at r = 0."""
    if value <= -1:
        raise ValueError("Carino linking needs returns above -100%.")
    return 1.0 if value == 0 else math.log1p(value) / value


@dataclass(frozen=True)
class SecurityContribution:
    daily: pd.DataFrame          # c_i,t per valid transition
    portfolio_daily: pd.Series   # sum_i c_i,t = r_p,t
    summary: pd.DataFrame        # arithmetic_sum and carino_linked per symbol
    total_return: float          # prod(1 + r_p,t) - 1
    reconciliation_error: float  # |sum_i carino_i - total_return|
    endpoint_return: float       # V_last / V_first - 1 over the supplied rows
    excluded_transitions: tuple[str, ...]


def security_contributions(holding_values_inr: pd.DataFrame) -> SecurityContribution:
    """Per-holding INR value-change contributions and Carino-linked totals.

    A transition is used only when every holding has a value on the date and
    on the preceding row. Linked contributions reconcile to the compounded
    return of those transitions; with no gaps that equals the endpoint return.
    """
    if not isinstance(holding_values_inr, pd.DataFrame) or holding_values_inr.shape[0] < 2:
        raise ValueError("Provide at least two dated rows of holding values.")
    if holding_values_inr.shape[1] < 1 or holding_values_inr.columns.has_duplicates:
        raise ValueError("Holding columns must be unique and non-empty.")
    values = holding_values_inr.astype(float).sort_index()
    finite = values.to_numpy()[~np.isnan(values.to_numpy())]
    if not np.isfinite(finite).all() or (finite < 0).any():
        raise ValueError("Holding values must be finite and non-negative.")
    total_value = values.sum(axis=1, min_count=values.shape[1])
    complete = values.notna().all(axis=1)
    valid = complete & complete.shift(1, fill_value=False)
    if not valid.any():
        raise ValueError("No complete consecutive holding-value transitions are available.")
    previous_total = total_value.shift(1)
    daily = values.diff().div(previous_total, axis=0).loc[valid]
    portfolio_daily = daily.sum(axis=1).rename("portfolio")
    total = float(np.expm1(np.log1p(portfolio_daily.to_numpy()).sum()))
    scale = carino_factor(total)
    weights = portfolio_daily.map(carino_factor) / scale
    linked = daily.mul(weights, axis=0).sum(axis=0)
    summary = pd.DataFrame({"arithmetic_sum": daily.sum(axis=0), "carino_linked": linked})
    complete_dates = values.index[complete]
    endpoint = float(total_value.loc[complete_dates[-1]] / total_value.loc[complete_dates[0]] - 1.0)
    return SecurityContribution(
        daily=daily, portfolio_daily=portfolio_daily, summary=summary, total_return=total,
        reconciliation_error=abs(float(linked.sum()) - total), endpoint_return=endpoint,
        excluded_transitions=tuple(day.strftime("%Y-%m-%d") for day in values.index[1:] if not valid[day]),
    )


@dataclass(frozen=True)
class RelativePerformance:
    metrics: dict[str, float | None]
    units: dict[str, str]
    undefined: dict[str, str]
    rolling: dict[int, pd.DataFrame]
    periods: dict[str, dict[str, object] | None]
    contributions: SecurityContribution
    observations: int


def relative_performance(
    portfolio: pd.Series,
    benchmark: pd.Series,
    *,
    levels: pd.DataFrame,
    holding_values_inr: pd.DataFrame,
    annual_risk_free_rate: float = 0.0,
    rolling_windows: Sequence[int] = ROLLING_WINDOWS,
) -> RelativePerformance:
    """Compute every Phase 2 statistic and explain each undefined one.

    ``levels`` columns must be ``portfolio`` and ``benchmark``.
    """
    if list(levels.columns) != ["portfolio", "benchmark"]:
        raise ValueError("levels must have columns ['portfolio', 'benchmark'].")
    metrics: dict[str, float | None] = {}
    units: dict[str, str] = {}
    undefined: dict[str, str] = {}

    def put(name: str, value: float | None, unit: str, reason: str) -> None:
        metrics[name] = value
        units[name] = unit
        if value is None:
            undefined[name] = reason

    te = tracking_error(portfolio, benchmark)
    put("portfolio_tracking_error_annualized", te, "annual_ratio", "")
    put("portfolio_active_return_annualized", annualized_active_return(portfolio, benchmark), "annual_ratio", "")
    put("portfolio_information_ratio", information_ratio(portfolio, benchmark), "unitless",
        "Tracking error is zero: daily active returns are constant.")
    put("portfolio_beta", beta(portfolio, benchmark), "unitless", "Benchmark daily returns have zero variance.")
    put("portfolio_downside_beta", downside_beta(portfolio, benchmark), "unitless",
        "Fewer than two benchmark down days, or zero down-day benchmark variance.")
    up, down = capture_ratios(portfolio, benchmark)
    put("portfolio_up_capture_ratio", up, "unitless", "No benchmark up days in the sample.")
    put("portfolio_down_capture_ratio", down, "unitless", "No benchmark down days in the sample.")
    put("portfolio_sortino", sortino_ratio(portfolio, annual_risk_free_rate=annual_risk_free_rate), "unitless",
        "No daily return fell below the daily risk-free rate (zero downside deviation).")
    put("benchmark_sortino", sortino_ratio(benchmark, annual_risk_free_rate=annual_risk_free_rate), "unitless",
        "No daily return fell below the daily risk-free rate (zero downside deviation).")

    rolling: dict[int, pd.DataFrame] = {}
    for window in rolling_windows:
        if len(portfolio) < window:
            for stat in ("tracking_error", "beta"):
                put(f"rolling_{window}d_{stat}_latest", None, "annual_ratio" if stat == "tracking_error" else "unitless",
                    f"Fewer than {window} valid paired daily returns.")
            continue
        frame = rolling_relative_metrics(portfolio, benchmark, window)
        rolling[window] = frame
        latest = frame.iloc[-1]
        put(f"rolling_{window}d_tracking_error_latest", float(latest["tracking_error"]), "annual_ratio", "")
        beta_latest = latest["beta"]
        put(f"rolling_{window}d_beta_latest", None if not np.isfinite(beta_latest) else float(beta_latest),
            "unitless", "Benchmark variance is zero in the latest window.")

    periods = period_returns(levels)
    for period, values in periods.items():
        for side in ("portfolio", "benchmark"):
            put(f"{side}_period_return_{period}", None if values is None else float(values[side]), "ratio",
                f"History is shorter than the {period} period.")

    contributions = security_contributions(holding_values_inr)
    if contributions.reconciliation_error > 1e-12 * max(1.0, abs(contributions.total_return)):
        raise ValueError("Linked security contributions do not reconcile to the portfolio return.")
    for symbol, row in contributions.summary.iterrows():
        put(f"contribution_carino:{symbol}", float(row["carino_linked"]), "ratio", "")
        put(f"contribution_arithmetic:{symbol}", float(row["arithmetic_sum"]), "ratio", "")
    put("contribution_total_compounded_return", contributions.total_return, "ratio", "")
    return RelativePerformance(metrics, units, undefined, rolling, periods, contributions, len(portfolio))


def scalar_results(performance: RelativePerformance) -> Mapping[str, tuple[float, str]]:
    """Finite (value, unit) pairs ready for ``analysis_results``."""
    return {name: (float(value), performance.units[name])
            for name, value in performance.metrics.items() if value is not None}
