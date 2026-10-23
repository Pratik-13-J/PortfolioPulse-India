"""Streamlit analyst dashboard for source-labelled PortfolioPulse datasets."""

from __future__ import annotations

import sqlite3
import math
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from portfoliopulse.attribution import allocation_alerts, scenario_attribution
from portfoliopulse.briefing import portfolio_analyst_briefing
from portfoliopulse.config import DB_PATH, REAL_PORTFOLIO_NAME
from portfoliopulse.database import get_dataset_info, initialize, list_candle_datasets
from portfoliopulse.portfolio_analysis import Phase4Analysis, analyze_saved_dataset
from portfoliopulse.scenarios import scenario_shocks


st.set_page_config(page_title="PortfolioPulse India | Risk Monitor", page_icon="📈", layout="wide")


@st.cache_data(show_spinner="Loading and validating the saved market dataset…")
def _load_analysis(dataset_id: int, annual_rf: float, confidence: float, database_path: str) -> Phase4Analysis:
    return analyze_saved_dataset(
        dataset_id,
        annual_risk_free_rate=annual_rf,
        confidence=confidence,
        db_path=Path(database_path),
    )


def _format_pct(value: float | None, places: int = 2) -> str:
    return "Not available" if value is None or pd.isna(value) else f"{value:.{places}%}"


def _format_ratio(value: float | None) -> str:
    return "Not available" if value is None or pd.isna(value) else f"{value:.2f}"


def _drawdown_chart(analysis: Phase4Analysis) -> pd.DataFrame:
    matched = analysis.returns.cumulative_comparison_returns.dropna(how="any")
    wealth = matched.add(1.0)
    return wealth.div(wealth.cummax()).sub(1.0).rename(
        columns={"portfolio": "Portfolio", analysis.benchmark_symbol: "NIFTY 50"}
    )


def _dataset_label(item: dict[str, object]) -> str:
    return (
        f"Dataset {item['dataset_id']} · {item['source_name']} · "
        f"{item['window_start']} to {item['window_end_exclusive']} (exclusive)"
    )


try:
    initialize(DB_PATH)
    datasets = [
        item for item in list_candle_datasets(DB_PATH)
        if item["portfolio_name"] == REAL_PORTFOLIO_NAME
    ]
except Exception as exc:
    st.title("PortfolioPulse India")
    st.error(f"Could not open the local market-data database: {exc}")
    st.stop()

st.title("PortfolioPulse India")
st.caption("Portfolio risk monitor · INR · Buy-and-hold quantities · NIFTY 50 comparison")

if not datasets:
    st.info("No real Indian market dataset is stored yet. Complete Phase 2/3 ingestion, then restart or reload this page.")
    st.code(".\\.venv\\Scripts\\python.exe -m scripts.ingest_phase3 --help", language="powershell")
    st.caption("The fictional demonstration portfolio is kept separate and is not shown as real market performance here.")
    st.stop()

with st.sidebar:
    st.header("Analysis settings")
    selected_dataset = st.selectbox(
        "Saved Indian dataset", datasets, format_func=_dataset_label,
        index=0, key="selected_dataset",
    )
    dataset_id = int(selected_dataset["dataset_id"])
    annual_rf = st.number_input(
        "Annual INR risk-free assumption (%)", min_value=-99.0, max_value=100.0,
        value=6.0, step=0.25, help="Effective annual assumption used for Sharpe ratio; not fetched from RBI.",
    ) / 100.0
    confidence = st.selectbox("Historical one-day VaR confidence", [0.95, 0.99], index=0, format_func=lambda x: f"{x:.0%}")
    drift_threshold = st.slider("Weight drift alert threshold (percentage points)", 1, 20, 5) / 100.0
    single_name_limit = st.slider("Single holding concentration threshold", 10, 100, 40) / 100.0
    hhi_limit = st.slider("HHI concentration threshold", 0.05, 1.0, 0.25, step=0.05)
    if st.button("Reload saved data", width="stretch"):
        _load_analysis.clear()
        st.rerun()

try:
    analysis = _load_analysis(dataset_id, float(annual_rf), float(confidence), str(DB_PATH))
except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
    st.error(f"This dataset could not be analyzed: {exc}")
    st.stop()

try:
    dataset_info = get_dataset_info(dataset_id, path=DB_PATH)
    snapshot = [row for row in dataset_info["portfolio_snapshot"] if not row["is_benchmark"]]
except (ValueError, OSError, sqlite3.Error) as exc:
    st.error(f"Could not load the saved holding snapshot: {exc}")
    st.stop()

holding_symbols = [str(row["symbol"]) for row in snapshot]
as_of = pd.Timestamp(analysis.risk_weight_date)
holding_values = analysis.returns.holding_values_inr.loc[as_of, holding_symbols]
actual_weights = analysis.returns.market_weights.loc[as_of, holding_symbols].astype(float)
portfolio_value = float(holding_values.sum())
target_defaults = {str(row["symbol"]): float(row["target_weight"]) for row in snapshot}

st.caption(
    f"{analysis.portfolio_name} · {analysis.quality.start} to "
    f"{(analysis.quality.aligned_prices.index[-1]).strftime('%Y-%m-%d')} · "
    f"valuation date {analysis.risk_weight_date} · analysis {analysis.analysis_id}"
)

metrics = analysis.metrics
kpi_columns = st.columns(5)
kpi_columns[0].metric("Annualized price return", _format_pct(metrics["portfolio_annualized_price_return"]))
kpi_columns[1].metric("Annualized volatility", _format_pct(metrics["portfolio_annualized_volatility"]))
kpi_columns[2].metric("Sharpe ratio", _format_ratio(metrics["portfolio_sharpe"]))
kpi_columns[3].metric("Maximum drawdown", _format_pct(metrics["portfolio_max_drawdown"]))
kpi_columns[4].metric(f"Historical {confidence:.0%} 1-day VaR", _format_pct(metrics["portfolio_var_1d"]))

performance_tab, allocation_tab, risk_tab, scenario_tab, data_tab = st.tabs(
    ["Performance", "Portfolio & targets", "Risk & diversification", "Stress tests", "Data & methodology"]
)

with performance_tab:
    matched = analysis.returns.cumulative_comparison_returns.dropna(how="any")
    if matched.empty:
        st.warning("There are no dates shared by every holding and NIFTY 50 in this dataset.")
    else:
        indexed = matched.add(1.0).mul(100.0).rename(
            columns={"portfolio": "Portfolio", analysis.benchmark_symbol: "NIFTY 50"}
        )
        indexed.index.name = "Date"
        chart = px.line(
            indexed, x=indexed.index, y=indexed.columns,
            labels={"value": "Growth of INR 100", "variable": "Series", "x": "Date"},
            title="Portfolio and NIFTY 50 · shared observed dates",
        )
        chart.update_layout(hovermode="x unified", legend_title_text="")
        st.plotly_chart(chart, width="stretch")
        st.caption("Both series start at INR 100 on the first complete shared date. Gaps and missing observations are not filled.")

    left, right = st.columns(2)
    with left:
        st.metric("Portfolio cumulative price return", _format_pct(metrics["portfolio_cumulative_price_return"]))
        st.metric("NIFTY 50 cumulative price return", _format_pct(metrics["benchmark_cumulative_price_return"]))
    with right:
        st.metric("NIFTY 50 annualized price return", _format_pct(metrics["benchmark_annualized_price_return"]))
        st.metric("NIFTY 50 annualized volatility", _format_pct(metrics["benchmark_annualized_volatility"]))

    drawdowns = _drawdown_chart(analysis)
    drawdown_plot = px.line(
        drawdowns, x=drawdowns.index, y=drawdowns.columns,
        labels={"value": "Drawdown", "variable": "Series", "x": "Date"},
        title="Observed drawdown on shared dates",
    )
    drawdown_plot.update_yaxes(tickformat=".0%")
    drawdown_plot.update_layout(hovermode="x unified", legend_title_text="")
    st.plotly_chart(drawdown_plot, width="stretch")

with allocation_tab:
    st.subheader("Holdings and target allocation")
    st.caption("Fixed quantities are taken from the saved dataset. Edited targets apply to this dashboard session only; they do not change the stored portfolio snapshot.")
    holdings_table = pd.DataFrame({
        "Symbol": holding_symbols,
        "Sector": [str(row["sector"]) for row in snapshot],
        "Quantity (fixed)": [float(row["quantity"]) for row in snapshot],
        "Market value (INR)": [float(holding_values[symbol]) for symbol in holding_symbols],
        "Current weight (%)": [float(actual_weights[symbol]) * 100 for symbol in holding_symbols],
        "Target weight (%)": [target_defaults[symbol] * 100 for symbol in holding_symbols],
    })
    edited = st.data_editor(
        holdings_table,
        hide_index=True,
        width="stretch",
        key=f"target_weights_{dataset_id}",
        disabled=["Symbol", "Sector", "Quantity (fixed)", "Market value (INR)", "Current weight (%)"],
        column_config={
            "Market value (INR)": st.column_config.NumberColumn(format="INR %.2f"),
            "Current weight (%)": st.column_config.NumberColumn(format="%.2f%%"),
            "Target weight (%)": st.column_config.NumberColumn(min_value=0, max_value=100, step=0.5, format="%.1f%%"),
        },
    )
    edited_values = pd.to_numeric(edited["Target weight (%)"], errors="coerce")
    entries_valid = bool(edited_values.notna().all() and edited_values.between(0, 100).all())
    edited_targets = (
        {str(symbol): float(value) / 100.0 for symbol, value in zip(edited["Symbol"], edited_values)}
        if entries_valid else {}
    )
    target_total = sum(edited_targets.values()) if entries_valid else float("nan")
    targets_valid = entries_valid and math.isclose(target_total, 1.0, rel_tol=0, abs_tol=1e-8)
    total_col, value_col = st.columns(2)
    total_col.metric("Target weights total", f"{target_total:.1%}" if entries_valid else "Invalid", delta="Valid" if targets_valid else "Check targets")
    value_col.metric("Current portfolio value", f"INR {portfolio_value:,.2f}")
    if not targets_valid:
        st.error("Enter numeric target weights from 0% to 100% that sum to exactly 100%.")
        target_weights = target_defaults
    else:
        target_weights = edited_targets

    try:
        concentration_result = allocation_alerts(
            {symbol: float(actual_weights[symbol]) for symbol in holding_symbols},
            target_weights if targets_valid else target_defaults,
            drift_threshold=drift_threshold,
            single_name_limit=single_name_limit,
            hhi_limit=hhi_limit,
        )
        alert_result = concentration_result
    except ValueError as exc:
        st.error(f"Allocation checks unavailable: {exc}")
        alert_result = None

    allocation_frame = pd.DataFrame({
        "Symbol": holding_symbols,
        "Current weight": [float(actual_weights[symbol]) for symbol in holding_symbols],
        "Target weight": [target_weights[symbol] for symbol in holding_symbols],
    }).melt(id_vars="Symbol", var_name="Allocation", value_name="Weight")
    allocation_plot = px.bar(
        allocation_frame, x="Symbol", y="Weight", color="Allocation", barmode="group",
        title="Current market weights and target weights",
    )
    allocation_plot.update_yaxes(tickformat=".0%")
    st.plotly_chart(allocation_plot, width="stretch")
    if alert_result is not None:
        drift_rows = [
            {"Symbol": symbol, "Current": item["actual_weight"], "Target": item["target_weight"], "Drift": item["drift"]}
            for symbol, item in alert_result["drifts"].items()
        ]
        drift_alerts = [row for row in drift_rows if abs(row["Drift"]) > drift_threshold]
        if not targets_valid:
            st.caption("Drift alerts below use the saved target weights until the edited weights total 100%.")
        elif drift_alerts:
            st.warning("Weight drift alerts: " + ", ".join(f"{row['Symbol']} {row['Drift']:+.1%}" for row in drift_alerts))
        else:
            st.success("No holding exceeds the selected weight-drift threshold.")

with risk_tab:
    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Asset correlation")
        corr_symbols = [*holding_symbols, analysis.benchmark_symbol]
        corr = analysis.correlations.reindex(index=corr_symbols, columns=corr_symbols)
        heatmap = px.imshow(
            corr, zmin=-1, zmax=1, color_continuous_scale="RdBu",
            text_auto=".2f", aspect="auto", title="Daily price-return correlation",
        )
        st.plotly_chart(heatmap, width="stretch")
        st.caption(f"Pearson correlation on {analysis.quality.valid_return_dates_count:,} complete paired daily returns. Constant series may show as blank.")
    with right:
        st.subheader("Asset risk contribution")
        contribution = analysis.risk_contributions
        if contribution is None:
            st.info("Risk contributions are undefined for a zero-volatility portfolio or insufficient return history.")
        else:
            risk_chart_data = contribution.reset_index(names="Symbol")
            risk_chart = px.bar(
                risk_chart_data, x="Symbol", y="percent_contribution",
                hover_data={"component_annual_volatility": ":.2%", "weight": ":.2%"},
                labels={"percent_contribution": "Share of current-weight proxy volatility"},
                title="Component contribution to current-weight proxy volatility",
            )
            risk_chart.update_yaxes(tickformat=".0%")
            st.plotly_chart(risk_chart, width="stretch")
            st.caption("These contributions use the last matched-date weights and historical asset covariance. Shares sum to 100%; this proxy can differ from realized buy-and-hold volatility.")
            st.dataframe(
                contribution.rename(columns={
                    "weight": "Current weight", "marginal_annual_volatility": "Marginal annual volatility",
                    "component_annual_volatility": "Component annual volatility", "percent_contribution": "Volatility share",
                }).style.format({
                    "Current weight": "{:.2%}", "Marginal annual volatility": "{:.2%}",
                    "Component annual volatility": "{:.2%}", "Volatility share": "{:.2%}",
                }),
                width="stretch",
            )
    if alert_result is not None:
        conc = alert_result["concentration"]
        st.metric("Portfolio HHI", f"{conc['hhi']:.3f}", delta="Alert" if conc["hhi_alert"] else "Within selected threshold", delta_color="inverse")
        if conc["single_name_alerts"]:
            st.warning("Single-name concentration alerts: " + ", ".join(conc["single_name_alerts"]))

with scenario_tab:
    st.subheader("Hypothetical one-period stress")
    st.warning("Illustrative shocks only. These are not forecasts, investment recommendations or historically calibrated predictions.")
    scenario_type = st.selectbox(
        "Scenario", ["broad_market", "banking_stress", "it_correction", "individual", "sector"],
        format_func=lambda item: {
            "broad_market": "Broad Indian market correction",
            "banking_stress": "Banking stress",
            "it_correction": "IT sector correction",
            "individual": "Individual stock shock",
            "sector": "Custom sector shock",
        }[item],
    )
    controls = st.columns(3)
    shock_kwargs: dict[str, object] = {}
    if scenario_type == "broad_market":
        shock_kwargs["broad_shock"] = controls[0].slider("Broad market move", -60, 20, -10, format="%d%%") / 100.0
    elif scenario_type in {"banking_stress", "it_correction"}:
        shock_kwargs["stressed_shock"] = controls[0].slider("Stressed sector move", -60, 20, -20, format="%d%%") / 100.0
        shock_kwargs["residual_shock"] = controls[1].slider("Other holdings move", -60, 20, -5, format="%d%%") / 100.0
    elif scenario_type == "individual":
        symbol = controls[0].selectbox("Holding", holding_symbols)
        shock_kwargs["individual_shocks"] = {symbol: controls[1].slider("Holding move", -100, 50, -20, format="%d%%") / 100.0}
    else:
        sectors = sorted({str(row["sector"]) for row in snapshot})
        sector = controls[0].selectbox("Sector", sectors)
        shock_kwargs["sector_shocks"] = {sector: controls[1].slider("Sector move", -100, 50, -20, format="%d%%") / 100.0}

    try:
        shocks = scenario_shocks(snapshot, scenario_type, **shock_kwargs)
        contributions, scenario_impact_inr, scenario_impact_pct = scenario_attribution(
            {symbol: float(holding_values[symbol]) for symbol in holding_symbols}, shocks,
        )
        scenario_columns = st.columns(3)
        scenario_columns[0].metric("Estimated portfolio impact", f"INR {scenario_impact_inr:,.2f}", delta=f"{scenario_impact_pct:.2%}")
        scenario_columns[1].metric("Portfolio value before shock", f"INR {portfolio_value:,.2f}")
        scenario_columns[2].metric("Valuation date", analysis.risk_weight_date)
        impact_chart = px.bar(
            contributions.reset_index(names="Symbol"), x="Symbol", y="impact_inr",
            color="impact_inr", color_continuous_scale="RdYlGn",
            labels={"impact_inr": "Estimated contribution (INR)"},
            title="Holding contribution to the scenario impact",
        )
        impact_chart.update_layout(coloraxis_showscale=False)
        st.plotly_chart(impact_chart, width="stretch")
        st.dataframe(
            contributions.rename(columns={
                "holding_value_inr": "Current value (INR)", "shock": "Shock",
                "impact_inr": "Impact (INR)", "portfolio_impact_pct": "Portfolio impact contribution",
            }).style.format({
                "Current value (INR)": "INR {:,.2f}", "Shock": "{:.2%}",
                "Impact (INR)": "INR {:,.2f}", "Portfolio impact contribution": "{:.2%}",
            }),
            width="stretch",
        )
        if alert_result is not None:
            briefing_text = portfolio_analyst_briefing(
                metrics, actual_weights, float(alert_result["concentration"]["hhi"]), scenario_impact_inr,
            )
            st.subheader("Rule-based analyst briefing")
            st.write(briefing_text)
    except ValueError as exc:
        st.error(f"Scenario could not be calculated: {exc}")

with data_tab:
    st.subheader("Source and data quality")
    try:
        retrieved_ist = pd.Timestamp(analysis.retrieved_at_utc).tz_convert("Asia/Kolkata")
        retrieved_label = retrieved_ist.strftime("%d %b %Y, %H:%M IST")
    except (TypeError, ValueError):
        retrieved_label = "Unavailable"
    adjustment = {
        "unadjusted_ohlcv": "Unadjusted OHLCV · price returns only",
        "provider_adjusted_ohlcv": "Provider-adjusted OHLCV · provider basis; not independently verified",
    }.get(analysis.price_basis, analysis.price_basis)
    info_cols = st.columns(4)
    info_cols[0].metric("Data source", analysis.source_name)
    info_cols[1].metric("Last retrieval", retrieved_label)
    info_cols[2].metric("Price basis", adjustment)
    info_cols[3].metric("Candle observations", f"{len(analysis.quality.candles):,}")
    quality_cols = st.columns(4)
    quality_cols[0].metric("Shared price dates", f"{analysis.quality.common_price_dates_count:,}")
    quality_cols[1].metric("Valid paired daily returns", f"{analysis.quality.valid_return_dates_count:,}")
    quality_cols[2].metric("Excluded price dates", f"{len(analysis.quality.excluded_price_dates):,}")
    quality_cols[3].metric("Excluded return dates", f"{len(analysis.quality.excluded_return_dates):,}")
    st.caption(
        f"Source status: {analysis.provenance_status}. Window: {dataset_info['window_start']} through "
        f"{dataset_info['window_end_exclusive']} (exclusive). Retrieved timestamps are displayed in India Standard Time."
    )
    st.dataframe(analysis.quality.coverage, hide_index=True, width="stretch")
    st.subheader("Method and limits")
    st.markdown(
        """
        - Buy and hold means fixed share quantities; market weights drift with prices.
        - Portfolio and NIFTY returns are compared on matching observed dates. Missing observations are not filled.
        - Headline performance uses close-to-close **price returns**. Unadjusted closes do not include dividends and may reflect corporate actions.
        - Annualization uses 252 trading observations. The annual risk-free input is an analyst assumption.
        - Historical VaR is a past-sample estimate, not a maximum-loss guarantee. Stress scenarios are hypothetical one-period shocks.
        """
    )
