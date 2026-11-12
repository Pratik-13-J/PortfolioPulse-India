"""VaR backtests: rolling out-of-sample forecasts, Kupiec POF, Christoffersen.

Pure functions. A forecast for day t uses only the ``window`` returns
strictly before t; an exception (violation) is a realized loss strictly
greater than the forecast VaR, i.e. r_t < -VaR_t.

Kupiec proportion-of-failures, with n days, x exceptions, p = 1 - c:
  LR_pof = -2 ln[(1-p)^(n-x) p^x] + 2 ln[(1-x/n)^(n-x) (x/n)^x]  ~ chi2(1)
Christoffersen independence, with n_ij transitions from state i to j:
  pi_0 = n01/(n00+n01), pi_1 = n11/(n10+n11), pi = (n01+n11)/(n-1)
  LR_ind = -2 ln[(1-pi)^(n00+n10) pi^(n01+n11)]
           + 2 ln[(1-pi_0)^n00 pi_0^n01 (1-pi_1)^n10 pi_1^n11]   ~ chi2(1)
Conditional coverage LR_cc = LR_pof + LR_ind ~ chi2(2). Terms of the form
0 * ln 0 are taken as zero; a state with no outgoing transitions contributes
nothing to the likelihood. Small samples make these asymptotic chi-square
p-values approximate.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy.special import xlogy
from scipy.stats import chi2

from portfoliopulse.risk_metrics import _returns
from portfoliopulse.var_models import _confidence, historical, parametric_normal

FORECAST_METHODS = ("historical", "normal")


def rolling_var_forecasts(
    daily_returns: pd.Series, *, window: int = 250, confidence: float = 0.99, method: str = "historical",
) -> pd.DataFrame:
    """One-day-ahead VaR forecasts from a trailing window and their outcomes."""
    if method not in FORECAST_METHODS:
        raise ValueError(f"Backtest method must be one of {FORECAST_METHODS}.")
    if isinstance(window, bool) or not isinstance(window, (int, np.integer)) or window < 2:
        raise ValueError("window must be an integer of at least 2.")
    level = _confidence(confidence)
    returns = _returns(daily_returns, minimum=2)
    if len(returns) <= window:
        raise ValueError(f"Backtesting needs more than {window} observations; got {len(returns)}.")
    model = historical if method == "historical" else parametric_normal
    forecasts = [model(returns.iloc[t - window:t], confidence=level).var for t in range(window, len(returns))]
    realized = returns.iloc[window:]
    frame = pd.DataFrame({"realized_return": realized.to_numpy(), "var_forecast": forecasts}, index=realized.index)
    frame["exception"] = frame["realized_return"] < -frame["var_forecast"]
    return frame


def _indicator(exceptions: Sequence[bool] | pd.Series) -> np.ndarray:
    values = list(exceptions)
    if not all(isinstance(item, (bool, np.bool_)) for item in values):
        raise ValueError("Exceptions must be Boolean indicators.")
    return np.asarray(values, dtype=bool)


def kupiec_pof(exceptions: int, observations: int, *, confidence: float) -> dict[str, float | int]:
    """Kupiec (1995) unconditional coverage likelihood-ratio test."""
    level = _confidence(confidence)
    for value, label in ((exceptions, "exceptions"), (observations, "observations")):
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"{label} must be an integer.")
    n, x = int(observations), int(exceptions)
    if n < 1 or not 0 <= x <= n:
        raise ValueError("Need observations >= 1 and 0 <= exceptions <= observations.")
    p = 1.0 - level
    rate = x / n
    null = xlogy(n - x, 1 - p) + xlogy(x, p)
    alternative = xlogy(n - x, 1 - rate) + xlogy(x, rate)
    lr = max(0.0, float(-2.0 * (null - alternative)))
    return {
        "observations": n, "exceptions": x, "expected_exceptions": n * p,
        "exception_rate": rate, "lr": lr, "p_value": float(chi2.sf(lr, 1)),
    }


def christoffersen_independence(exceptions: Sequence[bool] | pd.Series) -> dict[str, float | int]:
    """Christoffersen (1998) first-order Markov independence test."""
    hits = _indicator(exceptions)
    if len(hits) < 2:
        raise ValueError("At least two exception indicators are required.")
    previous, current = hits[:-1], hits[1:]
    n00 = int(np.sum(~previous & ~current)); n01 = int(np.sum(~previous & current))
    n10 = int(np.sum(previous & ~current)); n11 = int(np.sum(previous & current))
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    pi = (n01 + n11) / (len(hits) - 1)
    null = xlogy(n00 + n10, 1 - pi) + xlogy(n01 + n11, pi)
    alternative = xlogy(n00, 1 - pi0) + xlogy(n01, pi0) + xlogy(n10, 1 - pi1) + xlogy(n11, pi1)
    lr = max(0.0, float(-2.0 * (null - alternative)))
    return {
        "n00": n00, "n01": n01, "n10": n10, "n11": n11,
        "pi0": pi0, "pi1": pi1, "pi": pi, "lr": lr, "p_value": float(chi2.sf(lr, 1)),
    }


def backtest_var(forecasts: pd.DataFrame, *, confidence: float) -> dict[str, Any]:
    """Kupiec, Christoffersen and conditional coverage for a forecast frame."""
    hits = _indicator(forecasts["exception"])
    pof = kupiec_pof(int(hits.sum()), len(hits), confidence=confidence)
    independence = christoffersen_independence(hits)
    cc = pof["lr"] + independence["lr"]
    return {
        "observations": pof["observations"], "exceptions": pof["exceptions"],
        "expected_exceptions": pof["expected_exceptions"], "exception_rate": pof["exception_rate"],
        "kupiec_lr": pof["lr"], "kupiec_p_value": pof["p_value"],
        "christoffersen_lr": independence["lr"], "christoffersen_p_value": independence["p_value"],
        "conditional_coverage_lr": cc, "conditional_coverage_p_value": float(chi2.sf(cc, 2)),
        "transitions": {key: independence[key] for key in ("n00", "n01", "n10", "n11")},
        "first_forecast_date": forecasts.index[0].strftime("%Y-%m-%d"),
        "last_forecast_date": forecasts.index[-1].strftime("%Y-%m-%d"),
        "exception_dates": [day.strftime("%Y-%m-%d") for day in forecasts.index[hits]],
    }
