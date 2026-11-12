"""Transaction costs, turnover and integer-share allocation (pure functions).

Cost model: each component has buy/sell basis points of traded value and a
GST flag; GST applies to GST-flagged components (brokerage, exchange and SEBI
fees) at ``gst_rate``. Per side, rate = sum(bps) / 1e4 + gst_rate *
sum(gst-flagged bps) / 1e4. A flat per-order brokerage (plus GST) is added for
each symbol traded. All values are configurable illustrative assumptions.

Turnover: gross = sum_i |w_target,i - w_current,i| (buys plus sells), one-way
= gross / 2.

Integer shares: theoretical weights are converted to whole shares within a
cash budget. Start with floor(w_i * budget / (P_i * (1 + buy_rate))), then add
one share at a time to the holding with the largest INR shortfall versus its
target, provided the share fits the remaining cash and reduces that holding's
absolute gap (shortfall >= half a share price); ties go by symbol, so the
result is deterministic. Leftover cash is not forced into overweight names. Residual cash = budget - share cost - transaction costs >= 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CostModel:
    components: tuple[dict[str, Any], ...]
    gst_rate: float = 0.18
    brokerage_per_order_inr: float = 0.0
    basis: str = "illustrative_assumption"

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "CostModel":
        components = tuple(dict(c) for c in config["components"])
        for item in components:
            for side in ("buy_bps", "sell_bps"):
                if not math.isfinite(float(item[side])) or float(item[side]) < 0:
                    raise ValueError(f"Cost component {item['name']} needs non-negative {side}.")
        return cls(components, float(config.get("gst_rate", 0.0)),
                   float(config.get("brokerage_per_order_inr", 0.0)), str(config.get("basis", "")))

    def rate(self, side: str) -> float:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'.")
        key = f"{side}_bps"
        total = sum(float(c[key]) for c in self.components) / 1e4
        gst = sum(float(c[key]) for c in self.components if c.get("gst_applicable")) / 1e4
        return total + self.gst_rate * gst

    @property
    def per_order_inr(self) -> float:
        return self.brokerage_per_order_inr * (1 + self.gst_rate)

    def trade_costs(self, trade_values_inr: pd.Series) -> pd.DataFrame:
        """Signed INR trade values (+ buy, - sell) -> per-symbol cost breakdown."""
        value = trade_values_inr.astype(float)
        side = np.where(value > 0, "buy", np.where(value < 0, "sell", "none"))
        rate = np.where(value > 0, self.rate("buy"), np.where(value < 0, self.rate("sell"), 0.0))
        fixed = np.where(value != 0, self.per_order_inr, 0.0)
        return pd.DataFrame({"trade_value_inr": value, "side": side, "rate": rate,
                             "cost_inr": value.abs() * rate + fixed}, index=value.index)

    def turnover_cost_fraction(self, delta_weights: pd.Series) -> float:
        """Cost as a fraction of portfolio value for weight changes (bps part only)."""
        buys = delta_weights.clip(lower=0).sum()
        sells = (-delta_weights.clip(upper=0)).sum()
        return float(buys * self.rate("buy") + sells * self.rate("sell"))


def turnover(target: pd.Series, current: pd.Series) -> dict[str, float]:
    delta = target.reindex(current.index).astype(float) - current.astype(float)
    if delta.isna().any():
        raise ValueError("Target and current weights must cover the same symbols.")
    gross = float(delta.abs().sum())
    return {"gross": gross, "one_way": gross / 2}


@dataclass(frozen=True)
class Allocation:
    shares: pd.Series
    prices: pd.Series
    target_weights: pd.Series
    budget_inr: float
    invested_inr: float
    transaction_cost_inr: float
    residual_cash_inr: float
    achieved_weights: pd.Series
    trades: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)

    def table(self) -> pd.DataFrame:
        frame = pd.DataFrame({
            "target_weight": self.target_weights, "price_inr": self.prices, "shares": self.shares,
            "value_inr": self.shares * self.prices, "achieved_weight_of_budget": self.achieved_weights,
        })
        frame["weight_gap"] = frame["achieved_weight_of_budget"] - frame["target_weight"]
        return frame


def _validated(weights: pd.Series, prices: pd.Series, budget: float) -> tuple[pd.Series, pd.Series]:
    w = weights.astype(float)
    p = prices.reindex(w.index).astype(float)
    if p.isna().any() or (p <= 0).any():
        raise ValueError("Every target symbol needs a positive price.")
    if (w < -1e-12).any() or not math.isclose(float(w.sum()), 1.0, abs_tol=1e-6):
        raise ValueError("Target weights must be non-negative and sum to one.")
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("Investment amount must be positive and finite.")
    return w.clip(lower=0), p


def integer_allocation(weights: pd.Series, prices: pd.Series, investment_inr: float,
                       cost_model: CostModel) -> Allocation:
    """Whole-share purchase of target weights from cash, never exceeding the budget."""
    w, p = _validated(weights, prices, float(investment_inr))
    rate = cost_model.rate("buy")
    fixed = cost_model.per_order_inr
    budget = float(investment_inr)
    shares = np.floor(w * budget / (p * (1 + rate))).astype(int)

    def spend(s: pd.Series) -> float:
        value = float((s * p).sum())
        return value * (1 + rate) + fixed * int((s > 0).sum())

    while spend(shares) > budget:  # per-order fees can push the floor over budget
        largest = (shares * p).idxmax()
        shares[largest] -= 1
    while True:
        cash = budget - spend(shares)
        shortfall = (w * budget - shares * p).sort_index()
        candidates = [s for s in shortfall.sort_values(ascending=False, kind="mergesort").index
                      if shortfall[s] >= p[s] / 2
                      and p[s] * (1 + rate) + (fixed if shares[s] == 0 else 0.0) <= cash + 1e-9]
        if not candidates:
            break
        shares[candidates[0]] += 1
    invested = float((shares * p).sum())
    cost = spend(shares) - invested
    return Allocation(shares.astype(int), p, w, budget, invested, cost, budget - invested - cost,
                      (shares * p) / budget,
                      notes=["Theoretical weights rounded to whole shares; residual cash is uninvested."])


def rebalance_allocation(weights: pd.Series, prices: pd.Series, current_shares: pd.Series,
                         cost_model: CostModel) -> Allocation:
    """Integer trades from the existing holdings to target weights, self-financed.

    The budget is the current holdings' value; buys and sells both pay costs.
    If costs make the result unaffordable, one share of the most overweight
    holding is removed until residual cash is non-negative.
    """
    current = current_shares.reindex(weights.index).fillna(0).astype(float)
    p = prices.reindex(weights.index).astype(float)
    value = float((current * p).sum())
    plan = integer_allocation(weights, p, value, cost_model)
    shares = plan.shares.copy()

    def costs(s: pd.Series) -> pd.DataFrame:
        return cost_model.trade_costs((s - current) * p)

    while True:
        trade = costs(shares)
        residual = value - float((shares * p).sum()) - float(trade["cost_inr"].sum())
        if residual >= -1e-9 or shares.sum() == 0:
            break
        over = ((shares * p) / value - weights).idxmax()
        shares[over] -= 1
    trade = costs(shares)
    trade["shares_delta"] = (shares - current).astype(int)
    invested = float((shares * p).sum())
    cost = float(trade["cost_inr"].sum())
    return Allocation(shares.astype(int), p, weights.astype(float), value, invested, cost,
                      value - invested - cost, (shares * p) / value, trades=trade,
                      notes=["Self-financed rebalance of existing holdings; existing records are not modified."])
