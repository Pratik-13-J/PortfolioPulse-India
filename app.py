from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from portfoliopulse.analytics import concentration, portfolio_values, risk_metrics
from portfoliopulse.bootstrap import ensure_demo_data
from portfoliopulse.briefing import briefing
from portfoliopulse.config import BENCHMARK_SYMBOL, DB_PATH, TRADING_DAYS_PER_YEAR
from portfoliopulse.data import PriceDataError, align_prices, load_price_csv
from portfoliopulse.database import get_holdings, get_prices, load_observations, save_holdings, save_run, save_scenario
from portfoliopulse.scenarios import scenario_impact

st.set_page_config(page_title="PortfolioPulse", page_icon="📊", layout="wide")
ensure_demo_data()

st.title("PortfolioPulse")
st.caption("India-first portfolio risk monitor · Python analytics · SQL-backed · Illustrative demo data")
st.warning("The bundled price paths are synthetic and for demonstration only. This tool is educational; it does not provide investment advice or trade recommendations.")

holdings = get_holdings()
symbols = holdings["symbol"].tolist()
all_prices = get_prices(symbols + [BENCHMARK_SYMBOL])
if all_prices.empty:
    st.error("No observations are available. Check the local database or import file.")
    st.stop()

with st.sidebar:
    st.header("Portfolio inputs")
    uploaded = st.file_uploader("Import observations (CSV)", type=["csv"], help="Required columns: symbol, date, close_value. Symbols must exist in the demo instrument list.")
    if uploaded is not None and st.button("Import CSV observations"):
        try:
            imported = load_price_csv(uploaded)
            rows_added = load_observations(imported, source=f"uploaded:{uploaded.name}")
            st.success(f"Imported or updated {rows_added:,} observations.")
            st.rerun()
        except (ValueError, KeyError) as exc:
            st.error(f"Import not applied: {exc}")
    st.caption("Adjust units held. Current weights are calculated from the latest available values.")
    quantities: dict[str, float] = {}
    target_weights_input: dict[str, float] = {}
    for row in holdings.itertuples(index=False):
        quantities[row.symbol] = st.number_input(f"{row.symbol} units", min_value=0.0, value=float(row.quantity), step=1.0, key=f"qty_{row.symbol}")
        target_weights_input[row.symbol] = st.number_input(f"{row.symbol} target weight (%)", min_value=0, max_value=100, value=int(round(row.target_weight * 100)), step=5, key=f"target_{row.symbol}") / 100
    st.caption(f"Target total: {sum(target_weights_input.values()):.0%} (must equal 100% to save)")
    if st.button("Save holdings", use_container_width=True):
        try:
            save_holdings(quantities, target_weights_input)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    drift_threshold = st.slider("Drift alert threshold (percentage points)", 1, 20, 5) / 100
    concentration_threshold = st.slider("Concentration alert threshold", 20, 100, 40) / 100

    st.divider()
    min_date = all_prices["date"].min().date()
    max_date = all_prices["date"].max().date()
    selected = st.date_input("Analysis window", value=(max(min_date, date(max_date.year - 2, max_date.month, min(max_date.day, 28))), max_date), min_value=min_date, max_value=max_date)
    confidence = st.selectbox("Historical VaR confidence", [0.95, 0.99], format_func=lambda x: f"{x:.0%}")
    annual_rf = st.number_input("Annual risk-free assumption (%)", min_value=0.0, max_value=20.0, value=0.0, step=0.25) / 100

    st.divider()
    st.subheader("Illustrative shock")
    equity_shock = st.slider("Equity move", -40, 20, -10, format="%d%%") / 100
    debt_shock = st.slider("Debt move", -20, 10, -3, format="%d%%") / 100
    gold_shock = st.slider("Gold move", -20, 20, 5, format="%d%%") / 100

if isinstance(selected, (tuple, list)) and len(selected) == 2:
    start_date, end_date = selected
else:
    start_date, end_date = min_date, max_date
if start_date >= end_date:
    st.error("Choose a start date before the end date.")
    st.stop()

price_window = get_prices(symbols + [BENCHMARK_SYMBOL], start_date.isoformat(), end_date.isoformat())
holdings = get_holdings()
try:
    aligned_window = align_prices(price_window, symbols + [BENCHMARK_SYMBOL], source="local_database")
    if aligned_window.dropped_price_dates:
        st.error("This window has missing instrument or benchmark prices. Choose a complete window or import the missing observations before calculating risk.")
        st.dataframe(aligned_window.coverage, hide_index=True)
        st.stop()
    value_matrix, portfolio_value = portfolio_values(price_window[price_window["symbol"].isin(symbols)], holdings)
    returns, metrics = risk_metrics(portfolio_value, confidence=confidence, annual_rf=annual_rf)
except (PriceDataError, ValueError, KeyError) as exc:
    st.error(str(exc))
    st.stop()

latest_values = value_matrix.iloc[-1]
latest_total = float(latest_values.sum())
weights = latest_values / latest_total
target_weights = holdings.set_index("symbol")["target_weight"].reindex(weights.index).fillna(0)
allocation = pd.DataFrame({"symbol": weights.index, "Current weight": weights.values, "Target weight": target_weights.values})
classes = holdings.set_index("symbol")["asset_class"]
hhi = concentration(weights)
shock_by_class = {"Equity": equity_shock, "Debt": debt_shock, "Gold": gold_shock}
impact, contributions = scenario_impact(latest_values, shock_by_class, classes)
benchmark_series = price_window[price_window["symbol"] == BENCHMARK_SYMBOL].set_index("date")["close_value"]
benchmark_returns = benchmark_series.pct_change().dropna()
beta_dates = returns.index.intersection(benchmark_returns.index)
benchmark_beta = float(returns.reindex(beta_dates).cov(benchmark_returns.reindex(beta_dates)) / benchmark_returns.reindex(beta_dates).var()) if len(beta_dates) > 1 and benchmark_returns.reindex(beta_dates).var() else float("nan")
drift_alerts = allocation.loc[(allocation["Current weight"] - allocation["Target weight"]).abs() >= drift_threshold]
concentration_alerts = allocation.loc[allocation["Current weight"] >= concentration_threshold]

cards = st.columns(5)
cards[0].metric("Portfolio value", f"₹{latest_total:,.0f}")
cards[1].metric("Selected-window return", f"{metrics['total_return']:.1%}")
cards[2].metric("Annualized volatility", f"{metrics['annual_volatility']:.1%}")
cards[3].metric(f"Historical {confidence:.0%} 1-day VaR", f"{metrics['var']:.2%}")
cards[4].metric("Maximum drawdown", f"{metrics['max_drawdown']:.1%}")

overview, exposures, scenarios, methodology = st.tabs(["Overview", "Exposure & diversification", "Scenarios & briefing", "Methodology & data"])
with overview:
    if not drift_alerts.empty:
        st.warning(f"Allocation drift alert ({drift_threshold:.0%} threshold): " + ", ".join(f"{r.symbol} {r['Current weight']-r['Target weight']:+.1%}" for _, r in drift_alerts.iterrows()))
    if not concentration_alerts.empty:
        st.warning(f"Concentration alert ({concentration_threshold:.0%} threshold): " + ", ".join(f"{r.symbol} {r['Current weight']:.1%}" for _, r in concentration_alerts.iterrows()))
    left, right = st.columns([1.6, 1])
    with left:
        indexed = (portfolio_value / portfolio_value.iloc[0] * 100).rename("Portfolio")
        benchmark = price_window[price_window["symbol"] == BENCHMARK_SYMBOL].set_index("date")["close_value"]
        common_dates = indexed.index.intersection(benchmark.index)
        chart = pd.DataFrame({"Portfolio": indexed.reindex(common_dates), "NIFTY 50 demo benchmark": benchmark.reindex(common_dates) / benchmark.reindex(common_dates).iloc[0] * 100})
        st.plotly_chart(px.line(chart, labels={"value": "Growth of ₹100 (index)", "date": "Date", "variable": "Series"}, title="Portfolio and benchmark path"), use_container_width=True)
        export = pd.DataFrame({"portfolio_value_inr": portfolio_value, "daily_return": portfolio_value.pct_change()})
        st.download_button("Download portfolio history (CSV)", export.to_csv(index_label="date"), file_name="portfolio_history.csv", mime="text/csv")
    with right:
        drawdown = portfolio_value / portfolio_value.cummax() - 1
        st.plotly_chart(px.area(x=drawdown.index, y=drawdown.values, labels={"x": "Date", "y": "Drawdown"}, title="Portfolio drawdown"), use_container_width=True)
        st.metric("Annualized return", f"{metrics['annual_return']:.1%}")
        st.metric("Sharpe ratio", f"{metrics['sharpe']:.2f}")
        st.metric("Beta vs Nifty 50 demo benchmark", f"{benchmark_beta:.2f}" if pd.notna(benchmark_beta) else "N/A")
        st.metric("Historical expected shortfall", f"{metrics['expected_shortfall']:.2%}")
    rolling_vol = returns.rolling(63).std() * (TRADING_DAYS_PER_YEAR ** 0.5)
    st.plotly_chart(px.line(x=rolling_vol.index, y=rolling_vol.values, labels={"x": "Date", "y": "Annualized volatility"}, title="Rolling 63-observation volatility"), use_container_width=True)

with exposures:
    st.subheader("Indian demo holdings")
    st.dataframe(holdings[["symbol", "name", "sector", "asset_class", "quantity", "target_weight"]], use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(px.bar(allocation.melt(id_vars="symbol", var_name="Allocation", value_name="Weight"), x="symbol", y="Weight", color="Allocation", barmode="group", title="Current vs target allocation", text_auto=".1%"), use_container_width=True)
    with c2:
        st.plotly_chart(px.pie(allocation, values="Current weight", names="symbol", hole=.45, title="Current portfolio weights"), use_container_width=True)
    st.metric("Herfindahl concentration index (HHI)", f"{hhi:.3f}", help="Sum of squared instrument weights; a higher value indicates more concentration.")
    drift = allocation.assign(Drift=allocation["Current weight"] - allocation["Target weight"])
    st.dataframe(drift.style.format({"Current weight": "{:.1%}", "Target weight": "{:.1%}", "Drift": "{:+.1%}"}), use_container_width=True, hide_index=True)
    asset_returns = value_matrix.pct_change().dropna()
    st.plotly_chart(px.imshow(asset_returns.corr(), text_auto=".2f", color_continuous_scale="RdBu", zmin=-1, zmax=1, title="Historical return correlation"), use_container_width=True)

with scenarios:
    st.subheader("What-if impact at current holdings")
    st.caption("Shocks are user-defined illustrations applied to current market values; they are not forecasts.")
    st.metric("Estimated scenario change", f"₹{impact:,.0f}", delta=f"{impact / latest_total:.1%} of portfolio", delta_color="inverse")
    loss_table = contributions.rename("Estimated impact (₹)").to_frame().sort_values("Estimated impact (₹)")
    st.dataframe(loss_table.style.format({"Estimated impact (₹)": "₹{:,.0f}"}), use_container_width=True)
    if st.button("Save scenario snapshot"):
        save_scenario("Custom asset-class shock", str(portfolio_value.index[-1].date()), impact, impact / latest_total, shock_by_class, contributions.to_dict())
        st.success("Scenario saved to the local SQL database.")
    st.subheader("Analyst briefing")
    briefing_text = briefing(metrics, weights, hhi, impact, latest_total)
    st.markdown(briefing_text)
    st.download_button("Download briefing", briefing_text, file_name="portfoliopulse_briefing.txt")
    if st.button("Save risk snapshot"):
        run_id = save_run(1, str(returns.index[0].date()), str(returns.index[-1].date()), confidence, annual_rf, metrics)
        st.success(f"Risk snapshot saved to SQLite (run {run_id}).")

with methodology:
    coverage = all_prices.groupby("symbol").agg(observations=("date", "count"), first_date=("date", "min"), latest_date=("date", "max"), source=("source", "last")).reset_index()
    st.subheader("Observation coverage")
    st.dataframe(coverage, use_container_width=True, hide_index=True)
    st.markdown("""
    **Data status:** synthetic demo series loaded from a bundled CSV. They are not real Nifty, stock, ETF, or fund observations. The weekday date grid is not an NSE trading calendar.

    **Portfolio construction:** fixed unit holdings valued at each date's close/NAV. The portfolio is not automatically rebalanced. Dates are aligned across selected holdings using common observations.

    **Annualization:** 252 trading observations per year. Volatility is sample standard deviation of daily returns multiplied by √252. Sharpe uses the annual risk-free assumption shown in the sidebar.

    **Historical VaR / expected shortfall:** empirical daily-return quantile and mean of returns in the loss tail. These depend on the sample and do not bound future losses.

    **Real-data path:** import appropriately sourced observations. Nifty Indices historical/total-return data and AMFI NAV history are candidate India-specific inputs. NSE market-data access has usage terms; confirm rights before automation or redistribution. Keep source and as-of metadata with every import.
    """)
    st.code("symbol,date,close_value\nDEMO_ENERGY,2026-09-29,254.21\n", language="csv")
    st.caption(f"Local database: {DB_PATH.name} · Last observation: {all_prices['date'].max().date()} · Base currency: INR")
