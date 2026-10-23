"""Deterministic narrative based on computed portfolio values."""

import pandas as pd
from collections.abc import Mapping


def briefing(metrics: dict[str, float], weights: pd.Series, hhi: float, impact: float, portfolio_value: float) -> str:
    largest = weights.idxmax()
    worst = min(metrics["max_drawdown"], 0.0)
    loss_pct = impact / portfolio_value if portfolio_value else 0.0
    notes = [f"The portfolio's largest current holding is {largest} at {weights[largest]:.1%} of current value."]
    notes.append(f"Over the selected window, the portfolio returned {metrics['total_return']:.1%}; annualized volatility was {metrics['annual_volatility']:.1%} and the deepest drawdown was {worst:.1%}.")
    notes.append(f"Historical 95% one-day VaR is {metrics['var']:.2%} of portfolio value; this is a past-data estimate, not a maximum-loss guarantee.")
    notes.append(f"The configured shock scenario implies an estimated change of ₹{impact:,.0f} ({loss_pct:.1%}) at current holdings. HHI is {hhi:.3f}; higher values indicate more concentrated weights.")
    return "\n\n".join(notes)


def portfolio_analyst_briefing(
    metrics: Mapping[str, float | None], actual_weights: pd.Series,
    hhi: float, scenario_impact_inr: float,
) -> str:
    """Create a short rule-based readout using only computed portfolio figures."""
    if actual_weights.empty or not actual_weights.map(pd.notna).all():
        raise ValueError("Computed portfolio weights are required for the briefing.")
    largest_symbol = str(actual_weights.idxmax())
    largest_weight = float(actual_weights.max())
    annual_return = metrics.get("portfolio_annualized_price_return")
    benchmark_return = metrics.get("benchmark_annualized_price_return")
    volatility = metrics.get("portfolio_annualized_volatility")
    sharpe = metrics.get("portfolio_sharpe")
    drawdown = metrics.get("portfolio_max_drawdown")
    var_1d = metrics.get("portfolio_var_1d")
    notes = [
        f"Largest holding by current market value: {largest_symbol} at {largest_weight:.1%}.",
        (
            f"Annualized price return was {annual_return:.1%} versus {benchmark_return:.1%} for NIFTY 50."
            if annual_return is not None and benchmark_return is not None
            else "Annualized portfolio or NIFTY 50 return is unavailable for this dataset."
        ),
        (
            f"Annualized volatility was {volatility:.1%}; maximum observed drawdown was {drawdown:.1%}."
            if volatility is not None and drawdown is not None
            else "Volatility or maximum drawdown is unavailable for this dataset."
        ),
        (
            f"Historical one-day VaR was {var_1d:.2%}; the Sharpe ratio was {sharpe:.2f}."
            if var_1d is not None and sharpe is not None
            else f"Historical one-day VaR was {var_1d:.2%}; the Sharpe ratio is undefined because daily returns had zero volatility."
            if var_1d is not None
            else "Historical one-day VaR or Sharpe ratio is unavailable for this dataset."
        ),
        f"The selected hypothetical scenario estimates an INR {scenario_impact_inr:,.2f} one-period change. This is not a forecast.",
    ]
    concentration_note = (
        f"Holding concentration HHI is {hhi:.3f} (higher values indicate more concentration)."
    )
    notes.append(concentration_note)
    return "\n\n".join(notes)
