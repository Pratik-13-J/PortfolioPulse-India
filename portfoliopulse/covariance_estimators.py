"""Daily asset-return covariance estimators: sample, Ledoit-Wolf and EWMA.

All matrices are *daily*; multiply by 252 to annualize.

* Sample: unbiased (ddof=1) sample covariance.
* Ledoit-Wolf (2004) shrinkage toward a scaled identity m I, on demeaned
  returns x_t with the 1/N maximum-likelihood normalisation used in the paper
  (S = X'X / N, ||A||^2 = tr(A A') / p):
    m = tr(S) / p,  d^2 = ||S - m I||^2,
    b_bar^2 = (1 / N^2) sum_t ||x_t x_t' - S||^2,  b^2 = min(b_bar^2, d^2),
    delta = b^2 / d^2,  Sigma_LW = delta m I + (1 - delta) S.
* EWMA (RiskMetrics, lambda = 0.94), zero-mean, as of the last date:
    Sigma = sum_j w_j r_(T-j) r_(T-j)',  w_j = (1 - lambda) lambda^j / (1 - lambda^N)
  for j = 0..N-1 (most recent first); the finite-sample weights sum to one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from portfoliopulse.risk_metrics import _asset_returns, _finite_rate
from portfoliopulse.var_models import sample_mean_and_covariance

ESTIMATORS = ("sample", "ledoit_wolf", "ewma")
DEFAULT_EWMA_LAMBDA = 0.94


def sample_covariance(asset_returns: pd.DataFrame) -> pd.DataFrame:
    return sample_mean_and_covariance(asset_returns)[1]


def ledoit_wolf_covariance(asset_returns: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Return (shrunk daily covariance, shrinkage intensity delta in [0, 1])."""
    clean = _asset_returns(asset_returns)
    x = clean.to_numpy(dtype=float)
    x = x - x.mean(axis=0)
    n, p = x.shape
    s = x.T @ x / n
    m = float(np.trace(s) / p)
    target = m * np.eye(p)
    d2 = float(np.sum((s - target) ** 2) / p)
    # sum_t ||x_t x_t' - S||^2 = sum_t ||x_t||^4 - N ||S||^2 (both Frobenius, unnormalised).
    row_norms = np.sum(x ** 2, axis=1)
    b_bar2 = float((np.sum(row_norms ** 2) - n * np.sum(s ** 2)) / p / n ** 2)
    shrinkage = 0.0 if d2 == 0 else min(max(b_bar2, 0.0), d2) / d2
    shrunk = shrinkage * target + (1.0 - shrinkage) * s
    return pd.DataFrame(shrunk, index=clean.columns, columns=clean.columns), float(shrinkage)


def ewma_covariance(asset_returns: pd.DataFrame, *, decay: float = DEFAULT_EWMA_LAMBDA) -> pd.DataFrame:
    """Zero-mean RiskMetrics EWMA covariance with normalised finite weights."""
    lam = _finite_rate(decay, "decay")
    if not 0 < lam < 1:
        raise ValueError("EWMA decay must lie strictly between zero and one.")
    clean = _asset_returns(asset_returns)
    x = clean.to_numpy(dtype=float)
    n = len(x)
    ages = np.arange(n - 1, -1, -1)  # last row has age 0
    weights = (1.0 - lam) * lam ** ages / (1.0 - lam ** n)
    covariance = (x * weights[:, None]).T @ x
    return pd.DataFrame(covariance, index=clean.columns, columns=clean.columns)


def covariance_estimates(
    asset_returns: pd.DataFrame, *, decay: float = DEFAULT_EWMA_LAMBDA,
) -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
    """All three daily estimators plus their parameters (shrinkage, decay)."""
    lw, shrinkage = ledoit_wolf_covariance(asset_returns)
    return (
        {"sample": sample_covariance(asset_returns), "ledoit_wolf": lw,
         "ewma": ewma_covariance(asset_returns, decay=decay)},
        {"ledoit_wolf_shrinkage": shrinkage, "ewma_lambda": float(decay)},
    )
