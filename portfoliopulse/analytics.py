from __future__ import annotations

import numpy as np
import pandas as pd

from portfoliopulse.config import TRADING_DAYS_PER_YEAR


def portfolio_values(prices: pd.DataFrame, holdings: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    active = holdings.loc[holdings["quantity"] > 0].copy()
    symbols = active["symbol"].tolist()
    wide = prices.pivot(index="date", columns="symbol", values="close_value").sort_index()
    aligned = wide[symbols].dropna(how="any")
    if len(aligned) < 2:
        raise ValueError("At least two shared trading dates are needed for the selected holdings.")
    values = aligned.mul(active.set_index("symbol")["quantity"], axis=1)
    total = values.sum(axis=1).rename("portfolio_value")
    return values, total


def risk_metrics(total_value: pd.Series, confidence: float = 0.95, annual_rf: float = 0.0) -> tuple[pd.Series, dict[str, float]]:
    returns = total_value.pct_change().dropna()
    if len(returns) < 2:
        raise ValueError("Not enough daily returns to calculate risk.")
    vol = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    annual_return = float((total_value.iloc[-1] / total_value.iloc[0]) ** (TRADING_DAYS_PER_YEAR / len(returns)) - 1)
    daily_rf = (1 + annual_rf) ** (1 / TRADING_DAYS_PER_YEAR) - 1
    sharpe = float(((returns.mean() - daily_rf) / returns.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)) if returns.std(ddof=1) else 0.0
    drawdown = total_value / total_value.cummax() - 1
    loss_cutoff = float(returns.quantile(1 - confidence))
    tail = returns[returns <= loss_cutoff]
    metrics = {
        "total_return": float(total_value.iloc[-1] / total_value.iloc[0] - 1),
        "annual_return": annual_return,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "var": max(0.0, -loss_cutoff),
        "expected_shortfall": max(0.0, -float(tail.mean())) if not tail.empty else max(0.0, -loss_cutoff),
    }
    return returns.rename("portfolio_return"), metrics


def concentration(weights: pd.Series) -> float:
    return float((weights.pow(2)).sum())
