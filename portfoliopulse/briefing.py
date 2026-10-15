"""Deterministic narrative based on computed portfolio values."""

import pandas as pd


def briefing(metrics: dict[str, float], weights: pd.Series, hhi: float, impact: float, portfolio_value: float) -> str:
    largest = weights.idxmax()
    worst = min(metrics["max_drawdown"], 0.0)
    loss_pct = impact / portfolio_value if portfolio_value else 0.0
    notes = [f"The portfolio's largest current holding is {largest} at {weights[largest]:.1%} of current value."]
    notes.append(f"Over the selected window, the portfolio returned {metrics['total_return']:.1%}; annualized volatility was {metrics['annual_volatility']:.1%} and the deepest drawdown was {worst:.1%}.")
    notes.append(f"Historical 95% one-day VaR is {metrics['var']:.2%} of portfolio value; this is a past-data estimate, not a maximum-loss guarantee.")
    notes.append(f"The configured shock scenario implies an estimated change of ₹{impact:,.0f} ({loss_pct:.1%}) at current holdings. HHI is {hhi:.3f}; higher values indicate more concentrated weights.")
    return "\n\n".join(notes)
