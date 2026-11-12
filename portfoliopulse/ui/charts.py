"""Plotly figure builders for stored engine outputs (presentation only).

Colour follows the dataviz reference palette: categorical hues in a fixed
order by entity (never by rank), a blue <-> red diverging scale with a gray
midpoint for signed impacts, one y-axis per chart, 2px lines.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
DIVERGING = [[0.0, "#d03b3b"], [0.5, "#f0efec"], [1.0, "#2a78d6"]]  # loss red, gain blue
STRATEGY_ORDER = ["existing", "equal_weight", "min_variance", "max_sharpe", "risk_parity",
                  "min_variance_turnover_limited", "max_sharpe_rf_0"]
CATEGORY_ORDER = ["hypothetical", "historical_replay", "historical_model_propagated"]


def _color(key: str, order: list[str]) -> str:
    index = order.index(key) if key in order else len(order) + sorted([key]).index(key)
    return CATEGORICAL[index % len(CATEGORICAL)]


def _layout(fig: go.Figure, title: str, *, y_title: str = "", x_title: str = "", percent_y: bool = False,
            percent_x: bool = False) -> go.Figure:
    fig.update_layout(title=title, template="plotly_white", hovermode="closest", legend_title_text="",
                      margin=dict(l=10, r=10, t=50, b=10), xaxis_title=x_title, yaxis_title=y_title)
    if percent_y:
        fig.update_yaxes(tickformat=".1%")
    if percent_x:
        fig.update_xaxes(tickformat=".1%")
    return fig


def var_methods(var_table: pd.DataFrame, confidence: str, *, series: str = "portfolio") -> go.Figure:
    """VaR and ES by method at one confidence, with Monte Carlo SE bars."""
    rows = var_table[(var_table["series"] == series) & (var_table["confidence"] == confidence)].copy()
    rows = rows.sort_values("var")
    fig = go.Figure()
    for i, (measure, label) in enumerate((("var", "VaR"), ("es", "Expected shortfall"))):
        error = rows.get(f"{measure}_standard_error")
        fig.add_bar(y=rows["method"], x=rows[measure], name=label, orientation="h", marker_color=CATEGORICAL[i],
                    error_x=None if error is None else dict(type="data", array=error.fillna(0) * 1.96, visible=True),
                    hovertemplate="%{y}: %{x:.3%}<extra>" + label + "</extra>")
    fig.update_layout(barmode="group", bargap=0.25)
    return _layout(fig, f"One-day {label_conf(confidence)} VaR and ES by method ({series})",
                   x_title="Loss as share of portfolio value", percent_x=True)


def label_conf(tag: str) -> str:
    return f"{tag[1:]}%" if tag.startswith("c") else tag


def stress_comparison(comparison: pd.DataFrame) -> go.Figure:
    rows = comparison.sort_values("impact_pct", ascending=False)
    fig = go.Figure()
    for category in [c for c in CATEGORY_ORDER if c in set(rows["category"])]:
        part = rows[rows["category"] == category]
        fig.add_bar(y=part["scenario_name"], x=part["impact_pct"], orientation="h", name=category.replace("_", " "),
                    marker_color=_color(category, CATEGORY_ORDER), customdata=part["impact_inr"],
                    hovertemplate="%{y}<br>%{x:.2%} · INR %{customdata:,.0f}<extra></extra>")
    fig.update_layout(height=max(380, 28 * len(rows)), yaxis={"categoryorder": "array",
                                                              "categoryarray": list(rows["scenario_name"])})
    return _layout(fig, "Stressed portfolio P&L by scenario (hypothetical revaluation)",
                   x_title="Portfolio impact", percent_x=True)


def stress_heatmap(contributions: pd.DataFrame, names: pd.Series) -> go.Figure:
    grid = contributions.pivot_table(index="scenario_id", columns="symbol", values="portfolio_impact_pct")
    grid = grid.reindex(names.index.intersection(grid.index))
    bound = float(grid.abs().max().max()) or 1.0
    fig = go.Figure(go.Heatmap(z=grid.to_numpy(), x=list(grid.columns), y=[names[i] for i in grid.index],
                               zmin=-bound, zmax=bound, colorscale=DIVERGING, xgap=2, ygap=2,
                               text=[[f"{v:.2%}" for v in row] for row in grid.to_numpy()],
                               texttemplate="%{text}", hovertemplate="%{y}<br>%{x}: %{z:.2%}<extra></extra>",
                               colorbar=dict(tickformat=".1%", title="Impact")))
    fig.update_layout(height=max(380, 28 * len(grid)))
    return _layout(fig, "Holding contribution to stressed P&L (share of portfolio value)")


def stress_waterfall(contributions: pd.DataFrame, title: str) -> go.Figure:
    rows = contributions.sort_values("impact_inr")
    fig = go.Figure(go.Waterfall(
        x=[*rows["symbol"], "Total"], y=[*rows["impact_inr"], 0.0],
        measure=["relative"] * len(rows) + ["total"],
        decreasing={"marker": {"color": "#d03b3b"}}, increasing={"marker": {"color": "#2a78d6"}},
        totals={"marker": {"color": "#52514e"}}, connector={"line": {"color": "#c3c2b7", "width": 1}},
        hovertemplate="%{x}: INR %{y:,.0f}<extra></extra>"))
    return _layout(fig, title, y_title="INR impact")


def frontier(frontier_points: pd.DataFrame, strategies: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not frontier_points.empty:
        fig.add_scatter(x=frontier_points["volatility"], y=frontier_points["return"], mode="lines",
                        name="Efficient frontier", line=dict(color="#52514e", width=2),
                        hovertemplate="vol %{x:.2%} · E[r] %{y:.2%}<extra>frontier</extra>")
    shown = strategies.dropna(subset=["ex_ante_volatility"])
    for row in shown.itertuples():
        # Markers cluster near the minimum-variance point, so identity comes from the
        # legend and hover rather than colliding direct labels.
        fig.add_scatter(x=[row.ex_ante_volatility], y=[row.ex_ante_expected_return], mode="markers",
                        name=row.strategy, marker=dict(size=11, color=_color(row.strategy, STRATEGY_ORDER),
                                    line=dict(color="#fcfcfb", width=2)),
                        hovertemplate=f"{row.strategy}<br>vol %{{x:.2%}} · E[r] %{{y:.2%}}<extra></extra>")
    return _layout(fig, "Efficient frontier (ex-ante, in-sample, illustrative)", x_title="Annual volatility",
                   y_title="Expected annual return", percent_x=True, percent_y=True)


def walk_forward(net: pd.DataFrame) -> go.Figure:
    growth = (1 + net.fillna(0)).cumprod() * 100
    fig = go.Figure()
    for column in [c for c in STRATEGY_ORDER if c in growth.columns] + \
            [c for c in growth.columns if c not in STRATEGY_ORDER]:
        fig.add_scatter(x=growth.index, y=growth[column], mode="lines", name=column,
                        line=dict(width=2, color=_color(column, STRATEGY_ORDER)))
    fig.update_layout(hovermode="x unified")
    return _layout(fig, "Walk-forward out-of-sample growth of INR 100 (net of costs)", y_title="Value (INR)")


def strategy_weights(weights: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for strategy in [s for s in STRATEGY_ORDER if s in set(weights["strategy"])]:
        part = weights[weights["strategy"] == strategy]
        fig.add_bar(x=part["symbol"], y=part["target_weight"], name=strategy,
                    marker_color=_color(strategy, STRATEGY_ORDER),
                    hovertemplate="%{x}: %{y:.1%}<extra>" + strategy + "</extra>")
    fig.update_layout(barmode="group", bargap=0.2, bargroupgap=0.05)
    return _layout(fig, "Theoretical weights by strategy", y_title="Weight", percent_y=True)


def rolling_line(frame: pd.DataFrame, column: str, title: str, *, percent: bool) -> go.Figure:
    fig = go.Figure(go.Scatter(x=frame.index, y=frame[column], mode="lines", line=dict(width=2, color=CATEGORICAL[0]),
                               hovertemplate="%{x|%Y-%m-%d}: %{y:" + (".2%" if percent else ".2f") + "}<extra></extra>"))
    fig.update_layout(hovermode="x unified", showlegend=False)
    return _layout(fig, title, percent_y=percent)
