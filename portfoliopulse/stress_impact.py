"""Stressed P&L, contributions, sector impact and risk integration.

Holding P&L reuses ``attribution.scenario_attribution``:
impact_i = V_i * shock_i, total = sum_i impact_i, pct = total / sum_i V_i.
Additional outputs:

* loss_share_i = impact_i / total (shares of the total stressed P&L; they sum
  to one whenever total != 0; a holding that gains in a loss scenario has a
  negative share);
* sector impact = sum of holding impacts per stored sector label;
* stressed value V_i' = V_i (1 + shock_i) and weight w_i' = V_i' / sum V';
* worst holdings ranked by INR impact (most negative first).

Risk integration compares the stressed loss L = -pct with one-day VaR/ES from
the Phase 3 engine (``risk_analysis``) and with the observed maximum drawdown,
and recomputes delta-normal component VaR (``var_models``) and current-weight
volatility contributions (``risk_metrics``) at the stressed weights. A one-
period stress is not a one-day statistic: the ratios show scale only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from portfoliopulse.attribution import allocation_alerts, scenario_attribution
from portfoliopulse.risk_metrics import volatility_contributions
from portfoliopulse.stress_scenarios import ShockVector
from portfoliopulse.var_models import delta_normal_components

RISK_REFERENCE_METHODS = ("historical", "normal", "cornish_fisher", "monte_carlo_bootstrap", "delta_normal_sample")


@dataclass(frozen=True)
class StressImpact:
    scenario_id: str
    portfolio_value_inr: float
    impact_inr: float
    impact_pct: float
    holdings: pd.DataFrame
    sectors: pd.DataFrame
    worst_holdings: list[str]


def stress_impact(holding_values_inr: pd.Series, sectors: pd.Series, vector: ShockVector) -> StressImpact:
    """INR impact per holding and sector for one shock vector."""
    shocks = vector.shocks.reindex(holding_values_inr.index)
    if shocks.isna().any():
        raise ValueError(f"{vector.scenario_id}: shocks must cover every holding.")
    frame, total, pct = scenario_attribution(holding_values_inr.to_dict(), shocks.to_dict())
    frame = frame.reindex(holding_values_inr.index)
    frame["sector"] = sectors.reindex(frame.index)
    frame["shock_basis"] = vector.basis.reindex(frame.index)
    for name in vector.components.columns:
        frame[f"{name}_component"] = vector.components[name].reindex(frame.index)
    frame["loss_share"] = frame["impact_inr"] / total if total != 0 else np.nan
    frame["stressed_value_inr"] = frame["holding_value_inr"] * (1.0 + frame["shock"])
    stressed_total = float(frame["stressed_value_inr"].sum())
    frame["weight"] = frame["holding_value_inr"] / frame["holding_value_inr"].sum()
    frame["stressed_weight"] = frame["stressed_value_inr"] / stressed_total if stressed_total > 0 else np.nan
    frame["impact_rank"] = frame["impact_inr"].rank(method="first").astype(int)
    sector = frame.groupby("sector", sort=True).agg(
        holding_value_inr=("holding_value_inr", "sum"), impact_inr=("impact_inr", "sum"),
        holdings=("impact_inr", "size"),
    )
    sector["sector_return"] = sector["impact_inr"] / sector["holding_value_inr"]
    sector["portfolio_impact_pct"] = sector["impact_inr"] / frame["holding_value_inr"].sum()
    sector["loss_share"] = sector["impact_inr"] / total if total != 0 else np.nan
    worst = list(frame.sort_values(["impact_inr"], kind="mergesort").index[:3])
    return StressImpact(vector.scenario_id, float(frame["holding_value_inr"].sum()), float(total), float(pct),
                        frame, sector, worst)


def risk_integration(
    impact: StressImpact,
    var_table: pd.DataFrame,
    *,
    max_drawdown: float,
    asset_returns: pd.DataFrame,
    mean: pd.Series,
    covariance: pd.DataFrame,
    target_weights: dict[str, float],
    confidence: float = 0.99,
) -> dict[str, Any]:
    """Relate a stressed loss to VaR/ES, drawdown and risk contributions."""
    loss = -impact.impact_pct
    references: dict[str, Any] = {}
    portfolio_rows = var_table[(var_table["series"] == "portfolio")]
    for method in RISK_REFERENCE_METHODS:
        for level in sorted(portfolio_rows["confidence"].unique()):
            row = portfolio_rows[(portfolio_rows["method"] == method) & (portfolio_rows["confidence"] == level)]
            if row.empty:
                continue
            var, es = float(row["var_1d"].iloc[0]), float(row["es_1d"].iloc[0])
            references[f"{method}:c{level * 100:g}"] = {
                "var_1d": var, "es_1d": es,
                "loss_to_var": loss / var if var > 0 else None,
                "loss_to_es": loss / es if es > 0 else None,
                "exceeds_var": bool(loss > var), "exceeds_es": bool(loss > es),
            }
    weights = impact.holdings["weight"]
    stressed = impact.holdings["stressed_weight"]
    out: dict[str, Any] = {
        "stressed_loss_pct": loss,
        "var_es_references": references,
        "max_drawdown": max_drawdown,
        "loss_to_max_drawdown": loss / abs(max_drawdown) if max_drawdown < 0 else None,
        "exceeds_max_drawdown": bool(loss > abs(max_drawdown)),
        "interpretation": "one-period stress loss vs one-day in-sample VaR/ES and observed drawdown (scale only)",
    }
    if stressed.notna().all() and (stressed >= 0).all():
        cols = list(covariance.columns)
        before_total, before = delta_normal_components(mean, covariance, weights.reindex(cols), confidence=confidence)
        after_total, after = delta_normal_components(mean, covariance, stressed.reindex(cols), confidence=confidence)
        vol_before = volatility_contributions(asset_returns, weights.reindex(asset_returns.columns))
        vol_after = volatility_contributions(asset_returns, stressed.reindex(asset_returns.columns))
        out["post_stress_risk"] = {
            "confidence": confidence,
            "delta_normal_var_1d_before": before_total.var, "delta_normal_var_1d_after": after_total.var,
            "component_var_share_before": before["var_share"].to_dict(),
            "component_var_share_after": after["var_share"].to_dict(),
            "proxy_annual_volatility_before": float(vol_before["component_annual_volatility"].sum()),
            "proxy_annual_volatility_after": float(vol_after["component_annual_volatility"].sum()),
            "volatility_share_after": vol_after["percent_contribution"].to_dict(),
            "method": "same sample mean/covariance, weights re-marked to stressed values",
        }
        out["post_stress_allocation"] = allocation_alerts(stressed.to_dict(), target_weights)
    return out
