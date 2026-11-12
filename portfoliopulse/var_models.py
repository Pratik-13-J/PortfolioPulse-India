"""One-day Value-at-Risk and Expected Shortfall models (roadmap Phase 3).

Pure functions only: no database, network or UI access. Every loss figure is
a one-day loss expressed as a nonnegative-is-loss decimal fraction of the
portfolio value (multiply by the portfolio value for INR). Returns are close-
to-close *price* returns; nothing here infers dividends or corporate actions.

Conventions (alpha = 1 - confidence, z = Phi^-1(alpha) < 0):

* Historical: reuses ``risk_metrics.historical_var_es`` (nearest rank,
  k = max(1, ceil(alpha * N)) worst observations). Not re-implemented here.
* Parametric normal: VaR = -(mu + z * sigma),
  ES = -mu + sigma * phi(z) / alpha, with mu the sample mean and sigma the
  sample standard deviation (ddof=1).
* Cornish-Fisher: z_cf = z + (z^2 - 1) S / 6 + (z^3 - 3z) K / 24
  - (2z^3 - 5z) S^2 / 36, with S the moment skewness and K the moment excess
  kurtosis (population moments, ``bias=True`` in SciPy terms).
  VaR = -(mu + z_cf * sigma). ES averages the Cornish-Fisher quantile over the
  tail, which has the closed form
  ES = -mu + sigma * phi(z) / alpha * [1 + z S / 6 + (z^2 - 1) K / 24
  + (1 - 2 z^2) S^2 / 36]. The expansion is only a valid quantile function
  where z_cf is increasing in z; ``tail_monotone`` reports this on [-6, z].
* Monte Carlo: seeded multivariate-normal (sample mean and sample covariance)
  or iid bootstrap of historical dates (rows) on *asset* returns, revalued at
  fixed quantities: portfolio return = sum_i V_i R_i / sum_i V_i, with V_i the
  current INR holding values. The simulated portfolio returns are summarised
  with the same nearest-rank estimator as historical VaR/ES. Standard errors
  are batch means: the paths are split into B equal batches, VaR/ES are
  estimated per batch and SE = std(batch estimates, ddof=1) / sqrt(B).
* Delta-normal component VaR/ES (Euler allocation) for a weight vector w,
  mean vector m and covariance Sigma: sigma_p = sqrt(w' Sigma w),
  component VaR_i = w_i * (-m_i - z (Sigma w)_i / sigma_p),
  component ES_i = w_i * (-m_i + phi(z) / alpha * (Sigma w)_i / sigma_p).
  Components sum exactly to the total delta-normal VaR/ES.

Horizon is one trading day. No square-root-of-time scaling is applied here;
scaling to h days by sqrt(h) would assume iid returns and is an assumption
the caller must label explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import norm

from portfoliopulse.risk_metrics import _asset_returns, _finite_rate, _returns, historical_var_es

DEFAULT_CONFIDENCES = (0.95, 0.99)
MONTE_CARLO_METHODS = ("multivariate_normal", "bootstrap")


@dataclass(frozen=True)
class VaREstimate:
    """One-day VaR/ES as nonnegative-is-loss decimal fractions of value."""

    method: str
    confidence: float
    var: float
    es: float
    observations: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonteCarloEstimate(VaREstimate):
    var_standard_error: float = math.nan
    es_standard_error: float = math.nan
    seed: int = 0
    paths: int = 0
    batches: int = 0
    component_es: pd.Series | None = None


def _confidence(value: float) -> float:
    level = _finite_rate(value, "confidence")
    if not 0 < level < 1:
        raise ValueError("confidence must lie strictly between zero and one.")
    return level


def historical_tail_count(observations: int, confidence: float) -> int:
    """k = max(1, ceil((1 - c) * N)), matching ``historical_var_es``."""
    level = _confidence(confidence)
    if observations < 1:
        raise ValueError("At least one observation is required.")
    return max(1, int(np.ceil((1.0 - level) * observations - 1e-12)))


def historical(daily_returns: pd.Series, *, confidence: float = 0.95) -> VaREstimate:
    """Historical VaR/ES via the existing Phase 4 estimator (not re-implemented)."""
    level = _confidence(confidence)
    var, es = historical_var_es(daily_returns, confidence=level)
    n = len(daily_returns)
    return VaREstimate("historical", level, var, es, n, {"tail_count": historical_tail_count(n, level)})


def age_weighted_historical(
    daily_returns: pd.Series, *, confidence: float = 0.95, decay: float = 0.98,
) -> VaREstimate:
    """Age-weighted historical simulation (Boudoukh, Richardson & Whitelaw 1998).

    The observation of age a (0 = most recent) gets weight
    w = decay^a (1 - decay) / (1 - decay^N); decay = 1 gives equal weights 1/N.
    Returns are sorted from worst; VaR is minus the first return whose
    cumulative weight reaches alpha = 1 - c; ES is minus the weight-averaged
    tail up to alpha (the VaR observation enters with its partial weight).
    Losses are floored at zero, as in ``historical_var_es``. A diagnostic
    alternative only: it is not used by the stored risk-engine runs.
    """
    level = _confidence(confidence)
    lam = _finite_rate(decay, "decay")
    if not 0 < lam <= 1:
        raise ValueError("decay must lie in (0, 1].")
    returns = _returns(daily_returns, minimum=2)
    values = returns.to_numpy(dtype=float)
    n = len(values)
    ages = np.arange(n - 1, -1, -1, dtype=float)
    weights = np.full(n, 1.0 / n) if lam == 1 else lam ** ages * (1 - lam) / (1 - lam ** n)
    order = np.argsort(values, kind="mergesort")
    sorted_returns, sorted_weights = values[order], weights[order]
    alpha = 1.0 - level
    cumulative = np.cumsum(sorted_weights)
    index = int(np.searchsorted(cumulative, alpha - 1e-12))
    before = cumulative[index - 1] if index > 0 else 0.0
    tail = float(sorted_returns[:index] @ sorted_weights[:index] + sorted_returns[index] * (alpha - before))
    return VaREstimate("age_weighted_historical", level, max(0.0, -float(sorted_returns[index])),
                       max(0.0, -tail / alpha), n, {"decay": lam})


def historical_tail_observations(daily_returns: pd.Series, *, confidence: float = 0.95) -> pd.DataFrame:
    """The k worst observed daily returns (worst first; ties by earlier date)."""
    returns = _returns(daily_returns, minimum=2)
    k = historical_tail_count(len(returns), confidence)
    order = np.argsort(returns.to_numpy(), kind="mergesort")[:k]
    tail = returns.iloc[order]
    return pd.DataFrame({
        "rank": np.arange(1, k + 1),
        "date": tail.index,
        "daily_return": tail.to_numpy(),
        "loss": -tail.to_numpy(),
    })


@dataclass(frozen=True)
class SampleMoments:
    observations: int
    mean: float
    std: float
    skewness: float
    excess_kurtosis: float


def sample_moments(daily_returns: pd.Series) -> SampleMoments:
    """Mean, sample std (ddof=1), moment skewness and moment excess kurtosis."""
    returns = _returns(daily_returns, minimum=3)
    values = returns.to_numpy(dtype=float)
    shifted = values - values[0]  # exact zero dispersion for constant streams
    centered = shifted - shifted.mean()
    n = len(values)
    m2 = float(np.mean(centered ** 2))
    std = float(math.sqrt(float(centered @ centered) / (n - 1)))
    if m2 == 0:
        raise ValueError("Daily returns are constant; higher moments are undefined.")
    skew = float(np.mean(centered ** 3) / m2 ** 1.5)
    kurt = float(np.mean(centered ** 4) / m2 ** 2 - 3.0)
    return SampleMoments(n, float(values.mean()), std, skew, kurt)


def normal_var_es(mean: float, std: float, *, confidence: float = 0.95) -> tuple[float, float]:
    """Closed-form normal one-day (VaR, ES) for return mean and std."""
    level = _confidence(confidence)
    mu, sigma = _finite_rate(mean, "mean"), _finite_rate(std, "std")
    if sigma < 0:
        raise ValueError("std must be nonnegative.")
    alpha = 1.0 - level
    z = float(norm.ppf(alpha))
    return -(mu + z * sigma), -mu + sigma * float(norm.pdf(z)) / alpha


def parametric_normal(daily_returns: pd.Series, *, confidence: float = 0.95) -> VaREstimate:
    """Normal VaR/ES from the sample mean and sample std (ddof=1)."""
    level = _confidence(confidence)
    returns = _returns(daily_returns, minimum=2)
    values = returns.to_numpy(dtype=float)
    centered = (values - values[0]) - (values - values[0]).mean()
    std = float(math.sqrt(float(centered @ centered) / (len(values) - 1)))
    mu = float(values.mean())
    var, es = normal_var_es(mu, std, confidence=level)
    return VaREstimate("normal", level, var, es, len(values), {
        "mean": mu, "std": std, "z": float(norm.ppf(1.0 - level)),
    })


def cornish_fisher_z(z: float | np.ndarray, skewness: float, excess_kurtosis: float) -> float | np.ndarray:
    s, k = skewness, excess_kurtosis
    return z + (z ** 2 - 1) * s / 6 + (z ** 3 - 3 * z) * k / 24 - (2 * z ** 3 - 5 * z) * s ** 2 / 36


def cornish_fisher(daily_returns: pd.Series, *, confidence: float = 0.95) -> VaREstimate:
    """Cornish-Fisher (modified) VaR and tail-averaged Cornish-Fisher ES."""
    level = _confidence(confidence)
    moments = sample_moments(daily_returns)
    alpha = 1.0 - level
    z = float(norm.ppf(alpha))
    s, k = moments.skewness, moments.excess_kurtosis
    z_cf = float(cornish_fisher_z(z, s, k))
    var = -(moments.mean + z_cf * moments.std)
    bracket = 1 + z * s / 6 + (z ** 2 - 1) * k / 24 + (1 - 2 * z ** 2) * s ** 2 / 36
    es = -moments.mean + moments.std * float(norm.pdf(z)) / alpha * bracket
    grid = np.linspace(-6.0, z, 2001)
    derivative = 1 + grid * s / 3 + (3 * grid ** 2 - 3) * k / 24 - (6 * grid ** 2 - 5) * s ** 2 / 36
    return VaREstimate("cornish_fisher", level, float(var), float(es), moments.observations, {
        "mean": moments.mean, "std": moments.std, "skewness": s, "excess_kurtosis": k,
        "z": z, "z_cornish_fisher": z_cf, "tail_monotone": bool(np.all(derivative > 0)),
    })


def _holding_values(holding_values_inr: Mapping[str, float] | pd.Series, columns: pd.Index) -> np.ndarray:
    values = pd.Series(holding_values_inr, dtype=float)
    if set(values.index) != set(columns) or len(values) != len(columns):
        raise ValueError("Holding values must cover exactly the asset return columns.")
    array = values.reindex(columns).to_numpy(dtype=float)
    if not np.isfinite(array).all() or np.any(array < 0) or array.sum() <= 0:
        raise ValueError("Holding values must be finite, nonnegative and sum to a positive value.")
    return array


def _covariance_factor(covariance: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.cholesky(covariance)
    except np.linalg.LinAlgError:
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        if eigenvalues.min() < -1e-12 * max(1.0, abs(eigenvalues.max())):
            raise ValueError("The covariance matrix is not positive semidefinite.") from None
        return eigenvectors * np.sqrt(np.clip(eigenvalues, 0.0, None))


def sample_mean_and_covariance(asset_returns: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Sample mean vector and daily sample covariance (ddof=1)."""
    clean = _asset_returns(asset_returns)
    values = clean.to_numpy(dtype=float)
    centered = values - values[0]
    centered -= centered.mean(axis=0)
    covariance = (centered.T @ centered) / (len(clean) - 1)
    return (pd.Series(values.mean(axis=0), index=clean.columns),
            pd.DataFrame(covariance, index=clean.columns, columns=clean.columns))


def simulate_portfolio_returns(
    asset_returns: pd.DataFrame,
    holding_values_inr: Mapping[str, float] | pd.Series,
    *,
    method: str,
    paths: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (portfolio_returns[paths], holding_contributions[paths, assets]).

    Contribution_i = V_i R_i / sum(V): each simulated portfolio return is the
    revaluation of fixed quantities after one simulated day.
    """
    if method not in MONTE_CARLO_METHODS:
        raise ValueError(f"Monte Carlo method must be one of {MONTE_CARLO_METHODS}.")
    if isinstance(paths, bool) or not isinstance(paths, (int, np.integer)) or paths < 2:
        raise ValueError("paths must be an integer of at least 2.")
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError("seed must be a nonnegative integer.")
    clean = _asset_returns(asset_returns)
    values = _holding_values(holding_values_inr, clean.columns)
    rng = np.random.default_rng(int(seed))
    if method == "bootstrap":
        rows = rng.integers(0, len(clean), size=int(paths))
        simulated = clean.to_numpy(dtype=float)[rows]
    else:
        mean, covariance = sample_mean_and_covariance(clean)
        factor = _covariance_factor(covariance.to_numpy())
        draws = rng.standard_normal((int(paths), clean.shape[1]))
        simulated = mean.to_numpy() + draws @ factor.T
    contributions = simulated * (values / values.sum())
    return contributions.sum(axis=1), contributions


def monte_carlo(
    asset_returns: pd.DataFrame,
    holding_values_inr: Mapping[str, float] | pd.Series,
    *,
    method: str = "multivariate_normal",
    confidences: Sequence[float] = DEFAULT_CONFIDENCES,
    paths: int = 100_000,
    seed: int = 20261112,
    batches: int = 50,
) -> dict[float, MonteCarloEstimate]:
    """Seeded Monte Carlo VaR/ES with batch-means standard errors.

    Component ES is the negative mean holding contribution over the same k
    worst paths used for ES, so the components sum to the ES total.
    """
    if isinstance(batches, bool) or not isinstance(batches, (int, np.integer)) or batches < 2:
        raise ValueError("batches must be an integer of at least 2.")
    if paths % batches:
        raise ValueError("paths must be a multiple of batches.")
    levels = [_confidence(level) for level in confidences]
    portfolio, contributions = simulate_portfolio_returns(
        asset_returns, holding_values_inr, method=method, paths=paths, seed=seed,
    )
    if (portfolio < -1).any():
        raise ValueError("A simulated portfolio return fell below -100%.")
    columns = asset_returns.columns
    values = _holding_values(holding_values_inr, columns)
    # R_i < -1 (a negative simulated price) <=> contribution_i < -w_i.
    negative_price_paths = int((contributions < -(values / values.sum())).any(axis=1).sum())
    simulated = pd.Series(portfolio)
    batch_series = [pd.Series(chunk) for chunk in np.split(portfolio, batches)]
    order = np.argsort(portfolio, kind="mergesort")
    output: dict[float, MonteCarloEstimate] = {}
    for level in levels:
        var, es = historical_var_es(simulated, confidence=level)
        batch = np.array([historical_var_es(chunk, confidence=level) for chunk in batch_series])
        se = batch.std(axis=0, ddof=1) / math.sqrt(batches)
        k = historical_tail_count(paths, level)
        component = pd.Series(-contributions[order[:k]].mean(axis=0), index=columns, name="component_es")
        output[level] = MonteCarloEstimate(
            method=f"monte_carlo_{method}", confidence=level, var=var, es=es,
            observations=len(asset_returns), details={
                "tail_count": k, "negative_price_paths": negative_price_paths,
                "standard_error_method": "batch_means",
            },
            var_standard_error=float(se[0]), es_standard_error=float(se[1]),
            seed=int(seed), paths=int(paths), batches=int(batches), component_es=component,
        )
    return output


def _weights(weights: Sequence[float] | pd.Series, columns: pd.Index) -> np.ndarray:
    if isinstance(weights, pd.Series):
        if not weights.index.equals(columns):
            raise ValueError("Weight labels and order must match the covariance columns.")
    array = np.asarray(list(weights), dtype=float)
    if array.ndim != 1 or len(array) != len(columns) or not np.isfinite(array).all():
        raise ValueError("weights must be one finite value per asset.")
    if not np.isclose(array.sum(), 1.0, rtol=0, atol=1e-8):
        raise ValueError("weights must sum to one.")
    return array


def delta_normal_components(
    mean: pd.Series,
    covariance: pd.DataFrame,
    weights: Sequence[float] | pd.Series,
    *,
    confidence: float = 0.95,
    portfolio_value_inr: float | None = None,
) -> tuple[VaREstimate, pd.DataFrame]:
    """Delta-normal VaR/ES and their Euler component allocations."""
    level = _confidence(confidence)
    if not isinstance(covariance, pd.DataFrame) or not covariance.index.equals(covariance.columns):
        raise ValueError("covariance must be a square DataFrame with matching labels.")
    if not isinstance(mean, pd.Series) or not mean.index.equals(covariance.columns):
        raise ValueError("mean labels and order must match the covariance columns.")
    w = _weights(weights, covariance.columns)
    sigma = covariance.to_numpy(dtype=float)
    mu = mean.to_numpy(dtype=float)
    if not np.allclose(sigma, sigma.T, rtol=0, atol=1e-15) or not np.isfinite(sigma).all():
        raise ValueError("covariance must be finite and symmetric.")
    sigma_w = sigma @ w
    variance = float(w @ sigma_w)
    if variance <= 0:
        raise ValueError("Portfolio variance is zero; component VaR is undefined.")
    sigma_p = math.sqrt(variance)
    alpha = 1.0 - level
    z = float(norm.ppf(alpha))
    marginal_var = -mu - z * sigma_w / sigma_p
    marginal_es = -mu + float(norm.pdf(z)) / alpha * sigma_w / sigma_p
    total_var, total_es = normal_var_es(float(w @ mu), sigma_p, confidence=level)
    frame = pd.DataFrame({
        "weight": w,
        "marginal_var": marginal_var,
        "component_var": w * marginal_var,
        "marginal_es": marginal_es,
        "component_es": w * marginal_es,
    }, index=covariance.columns)
    frame["var_share"] = frame["component_var"] / total_var if total_var != 0 else np.nan
    if portfolio_value_inr is not None:
        value = _finite_rate(portfolio_value_inr, "portfolio_value_inr")
        frame["component_var_inr"] = frame["component_var"] * value
        frame["component_es_inr"] = frame["component_es"] * value
    estimate = VaREstimate("delta_normal", level, total_var, total_es, 0, {
        "portfolio_mean": float(w @ mu), "portfolio_std": sigma_p, "z": z,
    })
    return estimate, frame
