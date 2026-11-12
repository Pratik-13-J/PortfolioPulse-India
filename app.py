"""Streamlit analyst dashboard for source-labelled PortfolioPulse datasets."""

from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from portfoliopulse.attribution import allocation_alerts, scenario_attribution
from portfoliopulse.briefing import portfolio_analyst_briefing
from portfoliopulse.config import DB_PATH, REAL_PORTFOLIO_NAME
from portfoliopulse.database import get_candle_dataset, get_dataset_info, list_candle_datasets
from portfoliopulse.portfolio_analysis import Phase4Analysis, compute_phase4_analysis
from portfoliopulse.scenarios import scenario_shocks
from portfoliopulse.benchmark_attribution import attribution_gate
from portfoliopulse.reporting import csv_bundle, html_report, provenance_rows
from portfoliopulse.risk_analysis import COMPARISON_LABEL
from portfoliopulse.ui import charts
from portfoliopulse.ui.stored_results import CLI, ENGINES, latest_run, optimization_view, risk_view, stress_view


st.set_page_config(page_title="PortfolioPulse India | Risk Monitor", page_icon="📈", layout="wide")

# PORTFOLIOPULSE_DB lets tests and reviewers point the dashboard at a copy of the database.
DATABASE = Path(os.environ.get("PORTFOLIOPULSE_DB", str(DB_PATH)))
# The dashboard is read-only: every database access uses SQLite mode=ro, nothing is
# initialized or migrated here, and analytics are computed in memory without saving.
# Use the scripts/ command-line tools to ingest data or persist analysis runs.


@st.cache_data(show_spinner="Loading and validating the saved market dataset…")
def _load_analysis(dataset_id: int, annual_rf: float, confidence: float, database_path: str) -> Phase4Analysis:
    path = Path(database_path)
    return compute_phase4_analysis(
        dataset_id,
        get_dataset_info(dataset_id, path=path, read_only=True),
        get_candle_dataset(dataset_id, path=path, read_only=True),
        annual_risk_free_rate=annual_rf,
        confidence=confidence,
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
    datasets = [] if not DATABASE.exists() else [
        item for item in list_candle_datasets(DATABASE, read_only=True)
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
    analysis = _load_analysis(dataset_id, float(annual_rf), float(confidence), str(DATABASE))
except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
    st.error(f"This dataset could not be analyzed: {exc}")
    st.stop()

try:
    dataset_info = get_dataset_info(dataset_id, path=DATABASE, read_only=True)
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
    f"valuation date {analysis.risk_weight_date} · computed in memory (read-only dashboard; not saved)"
)

metrics = analysis.metrics
kpi_columns = st.columns(5)
kpi_columns[0].metric("Annualized price return", _format_pct(metrics["portfolio_annualized_price_return"]))
kpi_columns[1].metric("Annualized volatility", _format_pct(metrics["portfolio_annualized_volatility"]))
kpi_columns[2].metric("Sharpe ratio", _format_ratio(metrics["portfolio_sharpe"]))
kpi_columns[3].metric("Maximum drawdown", _format_pct(metrics["portfolio_max_drawdown"]))
kpi_columns[4].metric(f"Historical {confidence:.0%} 1-day VaR", _format_pct(metrics["portfolio_var_1d"]))

(performance_tab, benchmark_tab, allocation_tab, risk_tab, var_tab, scenario_tab, stress_library_tab,
 optimization_tab, attribution_tab, data_tab, reports_tab) = st.tabs([
    "Performance", "Benchmark comparison", "Portfolio & targets", "Risk & diversification", "VaR & backtests",
    "Stress tests", "Stress library", "Optimization", "Attribution", "Data & methodology", "Reports",
])

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
        "split_adjusted_ohlcv": "Split/bonus-adjusted OHLCV · not dividend-adjusted; price returns only",
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


# --- Conversation 6: read-only views of stored Phase 3/4/5 engine runs ------------------------
# These tabs only display persisted results; they never recompute or write financial results.

stored_runs = {kind: latest_run(dataset_id, kind, db_path=DATABASE) for kind in ENGINES}


def _missing_run(kind: str) -> None:
    st.info(f"No stored `{ENGINES[kind]}` run for dataset {dataset_id}. Create one from the command line:")
    st.code(f".\\.venv\\Scripts\\python.exe -m {CLI[kind]} --dataset-id {dataset_id}", language="powershell")


def _run_caption(run) -> None:
    settings = run.settings
    st.caption(
        f"Stored run {run.analysis_id} · {run.engine_version} · created {run.created_at_utc} UTC · "
        f"source {settings.get('source_name')} · verification **{settings.get('verification_status')}** · "
        f"price basis {settings.get('price_basis')} · {settings.get('comparison_label', COMPARISON_LABEL)}"
    )


def _pct_table(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    shown = frame.copy()
    for column in columns:
        if column in shown:
            shown[column] = shown[column].map(_format_pct)
    return shown


with benchmark_tab:
    st.subheader("Portfolio vs NIFTY 50 (price return vs NIFTY 50 price index, not TRI)")
    performance = analysis.performance
    if performance is None:
        st.info("Relative-performance metrics are unavailable for this dataset.")
    else:
        names = {
            "portfolio_tracking_error_annualized": "Tracking error (annual)",
            "portfolio_active_return_annualized": "Active return (annual, arithmetic)",
            "portfolio_information_ratio": "Information ratio", "portfolio_beta": "Beta",
            "portfolio_downside_beta": "Downside beta", "portfolio_up_capture_ratio": "Up capture",
            "portfolio_down_capture_ratio": "Down capture", "portfolio_sortino": "Portfolio Sortino",
            "benchmark_sortino": "NIFTY 50 Sortino",
        }
        cols = st.columns(3)
        for i, (key, label) in enumerate(names.items()):
            value = performance.metrics.get(key)
            text = _format_pct(value) if performance.units.get(key) == "annual_ratio" else _format_ratio(value)
            cols[i % 3].metric(label, text)
        if performance.undefined:
            st.caption("Undefined: " + "; ".join(f"{k}: {v}" for k, v in performance.undefined.items()))
        windows = [w for w in sorted(performance.rolling) if not performance.rolling[w].empty]
        if windows:
            window = st.radio("Rolling window (valid observations)", windows, horizontal=True,
                              index=len(windows) - 1)
            left, right = st.columns(2)
            left.plotly_chart(charts.rolling_line(performance.rolling[window], "tracking_error",
                                                  f"Rolling {window}-day tracking error", percent=True),
                              width="stretch")
            right.plotly_chart(charts.rolling_line(performance.rolling[window], "beta",
                                                   f"Rolling {window}-day beta", percent=False), width="stretch")
        else:
            st.info("Not enough valid returns for any rolling window.")
        periods = pd.DataFrame([{"period": k, **(v or {"note": "not enough history"})}
                                for k, v in performance.periods.items()])
        st.dataframe(periods.astype(str), hide_index=True, width="stretch")
        st.caption(f"{performance.observations:,} matched valid daily returns. Price returns only; NIFTY 50 is the "
                   "price index, not the total-return index.")

with var_tab:
    run = stored_runs["risk"]
    if run is None:
        _missing_run("risk")
    else:
        _run_caption(run)
        view = risk_view(run)
        if view["reliability"] != "in_sample_historical":
            st.warning(f"Only {view['observations']} valid returns: tail statistics are smoke-test quality only.")
        level = st.radio("Confidence", ["c95", "c99"], index=1, horizontal=True, format_func=charts.label_conf)
        st.plotly_chart(charts.var_methods(view["var_table"], level), width="stretch")
        st.caption("One-day losses as a share of portfolio value; Monte Carlo error bars are ±1.96 batch-means "
                   "standard errors. In-sample statistics, not forecasts or loss limits; no square-root-of-time scaling.")
        table = view["var_table"][view["var_table"]["confidence"] == level]
        st.dataframe(_pct_table(table, ["var", "es", "var_standard_error", "es_standard_error"]),
                     hide_index=True, width="stretch")
        tail = pd.DataFrame(view["tails"].get(f"portfolio:{level}", []))
        st.subheader(f"Historical tail: k = {len(tail)} worst of {view['observations']} valid returns")
        st.dataframe(_pct_table(tail, ["daily_return"]), hide_index=True, width="stretch")
        st.subheader("Backtests (rolling one-day-ahead forecasts)")
        if view["backtests"].empty:
            st.info("Backtests undefined: " + "; ".join(v for k, v in view["undefined"].items() if "backtest" in k))
        else:
            st.dataframe(view["backtests"].round(4), hide_index=True, width="stretch")
            st.caption("Kupiec POF and Christoffersen independence chi-square p-values; small p-values reject correct "
                       "coverage or independence. Asymptotic tests on one short history: indicative only.")
        st.subheader("Drawdown duration and recovery")
        dd = view["drawdowns"].get("portfolio", {})
        episode = dd.get("max_drawdown_episode", {})
        cols = st.columns(4)
        cols[0].metric("Maximum drawdown", _format_pct(dd.get("max_drawdown")))
        cols[1].metric("Peak to trough (sessions)", str(episode.get("observations_to_trough", "n/a")))
        cols[2].metric("Peak to recovery (sessions)", str(episode.get("duration_observations", "n/a")),
                       delta=None if episode.get("recovered", True) else "not recovered", delta_color="off")
        cols[3].metric("Current drawdown", _format_pct(dd.get("current_drawdown")))
        st.caption(f"Peak {episode.get('peak_date')} · trough {episode.get('trough_date')} · recovery "
                   f"{episode.get('recovery_date') or 'not yet'}. Sessions are dataset dates, not a validated NSE "
                   "calendar.")
        components = view["components"]
        if not components.empty:
            estimator = st.selectbox("Component VaR/ES allocation", sorted(components["estimator"].unique()))
            part = components[(components["estimator"] == estimator) & (components["confidence"] == level)]
            if part.empty:
                st.info("No components stored for this estimator and confidence.")
            else:
                pivot = part.pivot_table(index="symbol", columns="measure", values="value").reset_index()
                st.dataframe(_pct_table(pivot, ["component_var_1d", "component_es_1d"]), hide_index=True,
                             width="stretch")
                st.caption("Euler components sum to the method's total VaR/ES.")

with stress_library_tab:
    run = stored_runs["stress"]
    if run is None:
        _missing_run("stress")
    else:
        _run_caption(run)
        st.warning("Hypothetical one-period revaluations of the current fixed quantities. Not forecasts.")
        view = stress_view(run, db_path=DATABASE)
        comparison = view["comparison"]
        if comparison.empty:
            st.info("This stress run has no available scenarios.")
        else:
            st.plotly_chart(charts.stress_comparison(comparison), width="stretch")
            names = comparison.set_index("scenario_id")["scenario_name"]
            st.plotly_chart(charts.stress_heatmap(view["contributions"], names), width="stretch")
            shown = comparison[["scenario_name", "category", "scenario_type", "benchmark_move", "impact_inr",
                                "impact_pct", "worst_holding", "loss_to_historical_var_99",
                                "loss_to_historical_es_99", "loss_to_max_drawdown"]]
            st.dataframe(_pct_table(shown, ["benchmark_move", "impact_pct"]).round(2), hide_index=True,
                         width="stretch")
            st.caption("Loss multiples compare a one-period stress with one-day 99% historical VaR/ES and the "
                       "observed maximum drawdown: scale only.")
            choice = st.selectbox("Scenario detail", list(names.index), format_func=lambda key: names[key])
            detail = view["contributions"][view["contributions"]["scenario_id"] == choice]
            st.plotly_chart(charts.stress_waterfall(detail, f"{names[choice]}: holding P&L (INR)"), width="stretch")
            st.dataframe(_pct_table(detail[["symbol", "holding_value_inr", "shock", "shock_basis", "impact_inr",
                                            "portfolio_impact_pct"]], ["shock", "portfolio_impact_pct"]),
                         hide_index=True, width="stretch")
            st.caption("Shock basis: direct = stated shock; beta_propagated / covariance_propagated = model "
                       "estimates from in-sample betas or covariance; factor = assumed or estimated rate/FX "
                       "sensitivity; historical_observed = the holding's own observed event-window return.")
            sector = pd.DataFrame(view["sector_impact"].get(choice, {})).T
            if not sector.empty:
                st.dataframe(sector, width="stretch")
        if view["unavailable"]:
            st.subheader("Unavailable scenarios")
            for key, reason in view["unavailable"].items():
                st.error(f"{key}: {reason}")
        for warning in view["warnings"]:
            st.caption(f"Warning: {warning}")

with optimization_tab:
    run = stored_runs["optimization"]
    view = None if run is None else optimization_view(run, db_path=DATABASE)
    if view is None:
        _missing_run("optimization")
    else:
        _run_caption(run)
        st.warning("Illustrative, in-sample portfolio construction on a five-stock universe. Not a recommendation; "
                   "the stored holdings are never changed.")
        comparison = view["comparison"]
        for row in comparison.itertuples():
            if row.status in ("undefined", "infeasible", "failed"):
                st.error(f"{row.strategy}: {row.status}. {row.message}")
            elif row.status == "constrained":
                st.info(f"{row.strategy}: {row.message}")
        st.plotly_chart(charts.frontier(view["frontier"], comparison), width="stretch")
        columns = ["strategy", "status", "ex_ante_expected_return", "ex_ante_volatility", "ex_ante_sharpe",
                   "in_sample_sortino", "in_sample_beta", "in_sample_max_drawdown", "var_1d:historical:c99",
                   "es_1d:historical:c99", "effective_holdings", "turnover_gross_vs_current",
                   "oos_net_annualized_return", "oos_net_max_drawdown"]
        ratios = ("strategy", "status", "ex_ante_sharpe", "in_sample_sortino", "in_sample_beta", "effective_holdings")
        present = [c for c in columns if c in comparison]
        st.dataframe(_pct_table(comparison[present], [c for c in present if c not in ratios]).round(3),
                     hide_index=True, width="stretch")
        if not view["weights"].empty:
            st.plotly_chart(charts.strategy_weights(view["weights"]), width="stretch")
            strategy = st.selectbox("Integer-share allocation", list(view["weights"]["strategy"].unique()))
            alloc = view["weights"][view["weights"]["strategy"] == strategy]
            st.dataframe(_pct_table(alloc.drop(columns="strategy"),
                                    ["target_weight", "current_weight", "allocated_weight"]),
                         hide_index=True, width="stretch")
            row = comparison.set_index("strategy").loc[strategy]
            st.caption(
                f"Theoretical weights vs whole shares for INR {run.settings.get('investment_amount_inr', 0):,.0f}: "
                f"residual cash INR {row.get('new_money_residual_cash_inr', float('nan')):,.2f}; transaction costs "
                f"INR {row.get('new_money_transaction_cost_inr', float('nan')):,.2f} (illustrative cost assumptions).")
        if not view["walk_forward_net"].empty:
            st.plotly_chart(charts.walk_forward(view["walk_forward_net"]), width="stretch")
            failures = {k: len(v) for k, v in view["walk_forward"].get("failures", {}).items() if v}
            if failures:
                st.caption("Rebalances where a rule was undefined (prior weights held): "
                           + ", ".join(f"{k} {v}" for k, v in failures.items()))
        else:
            st.info(f"Walk-forward not available: {view['walk_forward'].get('reason')}")
        st.subheader("Trade-offs")
        for note in view["tradeoffs"]:
            st.markdown(f"- {note}")

with attribution_tab:
    st.subheader("Benchmark attribution (Active Share, Brinson-Fachler)")
    gate = attribution_gate(None, db_path=DATABASE)
    st.warning("Gated: " + " ".join(gate.reasons) + " No attribution is calculated or shown without valid NIFTY 50 "
               "constituent weights; weights are never fabricated.")

with reports_tab:
    st.subheader("Export")
    st.caption("Reports are built only from stored results and start with a provenance header.")
    header = provenance_rows({**dataset_info, "dataset_id": dataset_id}, stored_runs)
    if st.button("Prepare CSV and HTML report", width="stretch"):
        tables: dict[str, pd.DataFrame] = {}
        sections: list[tuple[str, object]] = []
        if stored_runs["risk"] is not None:
            rv = risk_view(stored_runs["risk"])
            tables["var_es"] = rv["var_table"]
            tables["backtests"] = rv["backtests"]
            sections += [("VaR and ES (99%)", charts.var_methods(rv["var_table"], "c99")),
                         ("VaR/ES table", rv["var_table"])]
        if stored_runs["stress"] is not None:
            sv = stress_view(stored_runs["stress"], db_path=DATABASE)
            tables["stress_comparison"] = sv["comparison"]
            tables["stress_contributions"] = sv["contributions"]
            if not sv["comparison"].empty:
                sections.append(("Stress scenarios", charts.stress_comparison(sv["comparison"])))
            sections.append(("Unavailable scenarios",
                             [f"{k}: {v}" for k, v in sv["unavailable"].items()] or ["none"]))
        if stored_runs["optimization"] is not None:
            ov = optimization_view(stored_runs["optimization"], db_path=DATABASE)
            if ov is not None:
                tables["optimization_comparison"] = ov["comparison"]
                tables["optimization_weights"] = ov["weights"]
                sections += [("Efficient frontier", charts.frontier(ov["frontier"], ov["comparison"])),
                             ("Trade-offs", ov["tradeoffs"])]
        st.session_state["report_zip"] = csv_bundle(tables, header)
        st.session_state["report_html"] = html_report(f"PortfolioPulse India · dataset {dataset_id}", header, sections)
    if "report_zip" in st.session_state:
        st.download_button("Download CSV bundle (.zip)", st.session_state["report_zip"],
                           file_name=f"portfoliopulse_dataset_{dataset_id}.zip", mime="application/zip")
        st.download_button("Download HTML report", st.session_state["report_html"],
                           file_name=f"portfoliopulse_dataset_{dataset_id}.html", mime="text/html")
    st.dataframe(pd.DataFrame(header, columns=["Field", "Value"]), hide_index=True, width="stretch")
