# Phase 7 — calculation and data verification

## Audit result

The Phase 4/5 calculations were reviewed against the stated buy-and-hold model and are covered by hand-checkable tests. Holdings retain fixed share quantities, portfolio market values use each date's close, and portfolio returns use adjacent complete price dates. NIFTY 50 comparisons use only dates where the portfolio and benchmark return observations are both available. Missing closes are not converted to zero or forward-filled.

The documented return convention is endpoint annualization, `(last complete portfolio value / first complete portfolio value) ** (252 / observed union intervals) - 1`. Volatility and Sharpe use sample daily standard deviation (`ddof=1`) annualized by `sqrt(252)`; Sharpe converts the annual effective risk-free assumption to an effective daily rate. Drawdown is measured from the observed wealth path. Historical one-day VaR and expected shortfall use the worst `ceil((1-confidence) * N)` observed returns (nearest-rank tail). Risk contributions use the last matched date's market weights and sample covariance. These are price-return calculations; unadjusted OHLCV does not establish dividend-adjusted total returns or resolve corporate actions.

Phase 5 scenario attribution applies hypothetical one-period shocks to current holding values. Holding INR contributions reconcile to total scenario P&L. This is a sensitivity calculation, not a forecast or a historically calibrated loss estimate.

SQLite writes use parameterized statements; database write helpers use transaction contexts, and scenario persistence validates attribution and dataset/analysis lineage before saving. Dataset validation rejects duplicate symbol/date observations, malformed OHLC, and invalid values while retaining gaps as missing. The tests exercise these boundaries and the calculation outputs.

## Automated verification

The Phase 7 suite includes hand-calculated fixed-quantity portfolio returns, annualized sample volatility and Sharpe, drawdown, historical VaR/ES, scenario reconciliation, invalid and Boolean inputs, missing observations, duplicate observations, clipped date windows, benchmark alignment, source close comparison and NSE CSV parsing.

Run the complete suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite verifies Python analytics and source-comparison outputs. The later [final audit](FINAL_AUDIT.md) separately records browser checks and a limited ten-date independent NIFTY 50 comparison.

## NSE close comparison status

**Final-audit update (16 October 2026):** Ten NIFTY 50 price-index closes displayed on the official NSE Indices page for 1–15 October were compared with the saved Yahoo provider series. All ten were within 0.01 index points; the largest difference was 0.00078125 points. The [reference CSV](reference/nifty50_nse_indices_2026-10-01_2026-10-15.csv) transcribes the displayed table and is not an original export. The remaining 678 provider dates and individual stock closes have not been independently checked. See [final audit](FINAL_AUDIT.md).

NSE's official [Historical Data for NIFTY 50](https://www.nseindia.com/all-reports) and [Historical Index Data](https://www.niftyindices.com/reports/historical-data) pages provide the reference paths. Select the NIFTY 50 **price index** close series. The NSE All Reports page also exposes a separate Total Returns Index section; do not compare that series to a price-index close. The comparison script does not fetch NSE data and cannot certify a downloaded file's provenance. Save the original report and record its source URL and retrieval time.

### Manual checklist

1. Select the same calendar window as the saved dataset and export historical **NIFTY 50 index Open/High/Low/Close** data from the official NSE/NSE Indices page. Choose the price index, not NIFTY 50 TRI.
2. Preserve the original CSV unchanged. Record the official report URL, retrieval date/time, selected index, requested date range, and file SHA-256. Check that the exported close column is actually the index close.
3. Compare daily dates as calendar dates. Do not add holidays, fill missing dates, or infer a close. Explain provider-only/reference-only dates against the exchange calendar and dataset window.
4. Decide the permitted points tolerance before reviewing differences. The script default is 0.01 index points; use another tolerance only when the source precision or known convention justifies it, and record the reason.
5. Review the full outer-join output, then inspect at least the first, middle and last overlapping sessions, plus selected high-volatility dates. Check every mismatch and missing observation against the original export, not a copied summary.
6. Report overlap, exact matches, within-tolerance matches, differences, and dates present in only one series. A match within a chosen tolerance is not proof that either source is authoritative.
7. Keep the raw export, checksum, comparison CSV, JSON summary and reviewer notes together. Do not describe sources as agreeing until this check has actually been run and reviewed.

To run the comparator after obtaining and reviewing an official NSE export:

```powershell
.\.venv\Scripts\python.exe -m scripts.compare_nse_index --dataset-id 1 --nse-csv data\incoming\nse_nifty50.csv --reference-source-url "https://www.nseindia.com/all-reports" --tolerance-points 0.01
```

The command writes a full-outer-join detail CSV and a JSON summary under `data/processed/`, including the provider metadata, supplied reference URL and reference-file checksum. It exits nonzero if it finds dates outside the chosen tolerance or provider dates missing against the supplied reference; review the generated output and investigate before interpreting that result.
