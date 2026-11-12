"""Annual expected-return estimates for optimization (all in-sample, noisy).

* ``arithmetic``: mean daily return x 252 (consistent with mean-variance math).
* ``cagr``: per-asset geometric annual rate via ``risk_metrics.annualized_return``
  ((prod(1 + r))^(252/N) - 1); lower than arithmetic by roughly sigma^2 / 2.
* ``bayes_stein`` (Jorion 1986): shrink the daily sample mean vector m toward
  the minimum-variance portfolio mean m0 = 1' S^-1 m / 1' S^-1 1, with
  S = sample covariance x (N - 1) / (N - p - 2),
  lambda = (p + 2) / ((m - m0 1)' S^-1 (m - m0 1)), delta = lambda / (N + lambda),
  m_BS = (1 - delta) m + delta m0 1, then x 252.

Historical means over ~2.7 years of unverified price data carry very large
estimation error (standard error ~ sigma / sqrt(years)); they describe the
sample and are not forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from portfoliopulse.config import TRADING_DAYS_PER_YEAR
from portfoliopulse.risk_metrics import _asset_returns, annualized_return

METHODS = ("arithmetic", "cagr", "bayes_stein")


@dataclass(frozen=True)
class ExpectedReturns:
    method: str
    annual: pd.Series
    standard_error: pd.Series
    observations: int
    shrinkage_intensity: float | None = None
    shrinkage_target_annual: float | None = None


def estimate(asset_returns: pd.DataFrame, method: str = "arithmetic") -> ExpectedReturns:
    if method not in METHODS:
        raise ValueError(f"Expected-return method must be one of {METHODS}.")
    clean = _asset_returns(asset_returns)
    n, p = clean.shape
    days = TRADING_DAYS_PER_YEAR
    daily_mean = clean.mean()
    # Standard error of the annualized arithmetic mean: sd_daily * 252 / sqrt(N).
    se = clean.std(ddof=1) * days / np.sqrt(n)
    if method == "arithmetic":
        return ExpectedReturns(method, daily_mean * days, se, n)
    if method == "cagr":
        return ExpectedReturns(method, pd.Series({c: annualized_return(clean[c]) for c in clean}), se, n)
    if n <= p + 2:
        raise ValueError("Bayes-Stein needs more observations than assets + 2.")
    sample = clean.cov(ddof=1).to_numpy() * (n - 1) / (n - p - 2)
    inv = np.linalg.inv(sample)
    ones = np.ones(p)
    m = daily_mean.to_numpy()
    m0 = float(ones @ inv @ m / (ones @ inv @ ones))
    gap = m - m0
    quad = float(gap @ inv @ gap)
    lam = np.inf if quad == 0 else (p + 2) / quad
    delta = 1.0 if not np.isfinite(lam) else lam / (n + lam)
    shrunk = (1 - delta) * m + delta * m0
    return ExpectedReturns(method, pd.Series(shrunk * days, index=clean.columns), se, n,
                           shrinkage_intensity=float(delta), shrinkage_target_annual=m0 * days)
