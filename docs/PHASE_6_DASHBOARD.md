# Phase 6 — PortfolioPulse India analyst dashboard

This records the Phase 6 state. The browser interaction checks were completed in the later [final audit](FINAL_AUDIT.md).

## What the dashboard shows

`app.py` reads an explicitly saved Indian portfolio dataset and calls the tested Phase 4 and Phase 5 analytics. It does not mix fictional demo prices into the real portfolio view.

- Annualized price return, annualized volatility, Sharpe ratio, maximum drawdown and historical one-day VaR KPIs.
- Portfolio and NIFTY 50 growth indexed to INR 100 on their first complete shared date, plus a matching-date drawdown chart.
- Fixed quantities and current market values from the dataset snapshot, with editable target weights in the current Streamlit session.
- Current-versus-target allocation, allocation drift alerts, single-name and HHI concentration alerts.
- Asset/NIFTY return correlation heatmap and holding-level contribution to current-weight proxy volatility.
- Broad-market, banking, IT, individual-stock and custom sector one-period shock controls, with INR contribution attribution.
- Data source, retrieval time in IST, declared price-adjustment basis, candle count, shared price/return observation counts, excluded dates and provenance status.
- A deterministic, rule-based briefing assembled from the selected dataset's computed metrics.

Target weights are session-only dashboard inputs. They do not change the immutable target weights stored with the dataset or alter its fixed share quantities. If edited targets do not sum to 100%, drift alerts identify that state; the dashboard temporarily references the saved target weights for the displayed drift check, while actual-weight concentration and scenario calculations remain available.

## Run

From the repository folder in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The app selects the most recently imported real Indian dataset by default. Use the sidebar to choose a different dataset, set the effective annual INR risk-free assumption, choose the historical VaR confidence, and set alert thresholds. Press **Reload saved data** after importing or refreshing local data while the dashboard is open.

## Calculation and data limits shown in the UI

The dashboard displays close-to-close price returns. It does not describe unadjusted close-price changes as total returns. Provider adjustment claims are displayed as declared metadata and are not independently verified. Missing observations are not filled; the comparison and daily risk samples use dates with complete holding and NIFTY observations. The endpoint annualized return uses the Phase 4 observed-interval method; annualized volatility and Sharpe use valid paired daily returns with 252 annualization observations. Historical VaR is a past-sample estimate, not a loss guarantee. Stress scenarios are hypothetical one-period shocks, not forecasts or calibrated predictions.

The risk-contribution chart is the Phase 4 last-matched-date weight/covariance proxy, which can differ from realized buy-and-hold volatility because holding weights drift. Correlations are Pearson correlations on aligned return observations. The current dashboard does not connect to live broker accounts or fetch market data; use the existing Phase 2/3 ingestion workflow to update the local database.

## Verification record

Phase 5 re-verification before dashboard integration passed all 83 repository tests. The saved Phase 5 run's INR and percentage contributions reconcile across five holdings; SQLite integrity returned `ok` and the foreign-key check returned no rows. Phase 6 passed Python compilation. The local HTTP health probe was blocked by this environment's socket permissions, and the in-process Streamlit runner did not complete within its execution window, so browser-level interaction remains to be checked in the user's local session.
