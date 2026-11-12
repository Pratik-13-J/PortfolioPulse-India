"""Risk/return profile of a weight vector, built from the existing engines.

Two clearly separated views:

* ex-ante (model): expected return mu' w, volatility sqrt(w' Sigma w) and
  Sharpe (mu' w - rf) / vol from the chosen mean / covariance estimates;
* in-sample historical (illustrative): a constant-mix (daily rebalanced,
  gross of costs) return series r_t = R_t w on the dataset's valid dates,
  summarised with ``risk_metrics`` (annualized return/volatility, Sharpe,
  maximum drawdown), ``performance_metrics`` (Sortino, beta vs NIFTY 50) and
  ``var_models`` (historical / normal / Cornish-Fisher VaR and ES; delta-
  normal component VaR/ES). Weights chosen on the same sample make these
  statistics optimistic: they are labelled in-sample.

Concentration: max weight, HHI = sum w^2, effective number of holdings 1/HHI,
sector allocation. On a five-stock, four-sector universe these describe
concentration only; they do not demonstrate broad-market diversification.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from portfoliopulse.optimization import risk_contributions
from portfoliopulse.performance_metrics import beta, sortino_ratio
from portfoliopulse.portfolio_allocation import CostModel, turnover
from portfoliopulse.risk_metrics import annualized_return, annualized_volatility, maximum_drawdown, sharpe_ratio
from portfoliopulse.var_models import (
    cornish_fisher, delta_normal_components, historical, parametric_normal, sample_mean_and_covariance,
)

CONFIDENCES = (0.95, 0.99)


def constant_mix_returns(asset_returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    w = weights.reindex(asset_returns.columns)
    if w.isna().any():
        raise ValueError("Weights must cover every asset return column.")
    return (asset_returns @ w).rename("portfolio")


def evaluate(
    weights: pd.Series,
    *,
    asset_returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    mu_annual: pd.Series,
    cov_annual: pd.DataFrame,
    annual_risk_free_rate: float,
    sectors: Mapping[str, str],
    current_weights: pd.Series,
    cost_model: CostModel,
) -> dict[str, Any]:
    w = weights.reindex(asset_returns.columns).astype(float)
    mu_p = float(mu_annual.reindex(w.index) @ w)
    vol = float(np.sqrt(w @ cov_annual.reindex(index=w.index, columns=w.index) @ w))
    rf = float(annual_risk_free_rate)
    series = constant_mix_returns(asset_returns, w)
    metrics: dict[str, Any] = {
        "ex_ante_expected_return": mu_p,
        "ex_ante_volatility": vol,
        "ex_ante_sharpe": (mu_p - rf) / vol if vol > 0 else None,
        "in_sample_annualized_return": annualized_return(series),
        "in_sample_annualized_volatility": annualized_volatility(series),
        "in_sample_sharpe": sharpe_ratio(series, annual_risk_free_rate=rf),
        "in_sample_sortino": sortino_ratio(series, annual_risk_free_rate=rf),
        "in_sample_beta": beta(series, benchmark_returns),
        "in_sample_max_drawdown": maximum_drawdown(series),
        "max_weight": float(w.max()),
        "hhi": float((w ** 2).sum()),
        "effective_holdings": float(1.0 / (w ** 2).sum()),
    }
    for level in CONFIDENCES:
        tag = f"c{level * 100:g}"
        for model in (historical, parametric_normal, cornish_fisher):
            estimate = model(series, confidence=level)
            metrics[f"var_1d:{estimate.method}:{tag}"] = estimate.var
            metrics[f"es_1d:{estimate.method}:{tag}"] = estimate.es
    mean_daily, cov_daily = sample_mean_and_covariance(asset_returns)
    total99, components99 = delta_normal_components(mean_daily, cov_daily, w, confidence=0.99)
    vol_rc = risk_contributions(w, cov_annual.reindex(index=w.index, columns=w.index))
    metrics["delta_normal_var_1d:c99"] = total99.var
    metrics["delta_normal_es_1d:c99"] = total99.es
    move = turnover(w, current_weights.reindex(w.index))
    cost = cost_model.turnover_cost_fraction(w - current_weights.reindex(w.index))
    metrics.update({"turnover_gross_vs_current": move["gross"], "turnover_one_way_vs_current": move["one_way"],
                    "rebalance_cost_fraction": cost,
                    "ex_ante_expected_return_net_first_year": mu_p - cost})
    sector = pd.Series(sectors).reindex(w.index)
    table = pd.DataFrame({
        "weight": w, "current_weight": current_weights.reindex(w.index), "sector": sector,
        "expected_return": mu_annual.reindex(w.index),
        "component_volatility": vol_rc["component_volatility"], "volatility_share": vol_rc["risk_share"],
        "component_var_1d_c99": components99["component_var"], "component_es_1d_c99": components99["component_es"],
    })
    reconciliation = {
        "weights_sum": float(w.sum()),
        "volatility_components_minus_total": float(vol_rc["component_volatility"].sum() - vol),
        "component_var_minus_total": float(components99["component_var"].sum() - total99.var),
        "component_es_minus_total": float(components99["component_es"].sum() - total99.es),
    }
    return {"metrics": metrics, "holdings": table,
            "sectors": table.groupby("sector")["weight"].sum().to_dict(),
            "reconciliation": reconciliation, "returns": series}
