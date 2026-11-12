# 5–7 Minute Project Demonstration

**Before you start:**

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Dataset 1 opens by default. Keep [ARCHITECTURE_GUIDE.md](ARCHITECTURE_GUIDE.md) open in a second tab.

| Time | Show | Say (talking points) |
|---|---|---|
| 0:00–0:45 | README top section | "An asset manager needs to know how a portfolio performed against its benchmark, how much it could lose, what happens in a crisis, and whether a better allocation exists. Every number must be traceable to its source." |
| 0:45–1:30 | **Performance** tab: KPIs and growth chart | "Five NSE stocks, buy and hold, against the NIFTY 50 price index. The quantities are fixed, so the weights drift. The portfolio fell 4.9% a year while NIFTY rose 2.2%; the max drawdown is −29.5% and still unrecovered." Point to the caption: *computed in memory; dashboard read-only.* |
| 1:30–2:15 | **Benchmark comparison** | "Beta 0.90 and tracking error 6.4%. The information ratio is −1.17, so the active risk was not rewarded. Down-capture of 0.97 means it fell almost as much as the index on bad days." |
| 2:15–3:30 | **VaR & backtests** (99%) | "Five VaR methods side by side. Historical 99% one-day VaR is 2.13%, about ₹2,535, based on the 7 worst of 686 days, which are listed. Cornish-Fisher is higher (2.68%) because of fat tails, kurtosis 3.2. **The backtest:** historical 95% VaR fails Kupiec, with 33 exceptions against 21.8 expected. I investigated why: the exceptions cluster in 2026-Q1, when volatility jumped, and an equal-weighted 250-day window adapts too slowly. An age-weighted estimator passes, but I didn't adopt it, because it was chosen after the fact on one sample." ([VAR_BACKTEST_DIAGNOSTICS.md](VAR_BACKTEST_DIAGNOSTICS.md)) |
| 3:30–4:30 | **Stress library** | "Each holding's shock is tagged direct, beta-propagated, covariance-propagated, factor or historical. NIFTY −10% costs 9.2% through betas. A −20% shock to ICICIBANK alone costs 5.8%, but 10.5% once correlated names move with it. The rate/FX sensitivities are labelled as illustrative assumptions. COVID-2020 is shown as unavailable, because I never fill in index moves from memory." |
| 4:30–5:30 | **Optimization** | "Min-variance, max-Sharpe, risk parity and the frontier, under 5–35% stock caps and a 40% Banking cap. Max-Sharpe is reported as **undefined**, because no feasible portfolio beats the 6% risk-free rate. Forcing an answer would be meaningless. Walk-forward: no weighting rescued five stocks that lagged the index by about 12 points a year." |
| 5:30–6:15 | **Attribution** | "Brinson-Fachler is implemented and hand-verified, but real attribution needs NIFTY constituent weights, which I don't have. So it's gated, and this is a clearly labelled synthetic example: allocation + selection + interaction = active return." |
| 6:15–7:00 | ARCHITECTURE_GUIDE diagram, then **Reports** | "Pure calculation modules, orchestrators that store versioned runs in SQLite, and a read-only dashboard. 286 tests with hand-calculated fixtures, and CI on GitHub Actions. Every report starts with a provenance header." |

## One technical challenge to explain

**Making results reproducible and non-destructive.**
- **The problem:** re-running an engine could duplicate or overwrite results. Separately, Git's CRLF conversion silently changed a config file's hash, which would have created a duplicate optimization run.
- **The fix:**
  - runs are deduplicated on their exact settings, so identical inputs return the same `analysis_id`;
  - config hashes normalise line endings;
  - the dashboard opens SQLite read-only;
  - changes are verified with a hex-float fingerprint of every database row before and after.

## Likely questions

**Data and provenance**
- **Why not total return?** There is no NIFTY TRI or dividend data in the project, so everything is labelled price return.
- **Is the data verified?** Dataset 1 is user-declared Yahoo data. Its price basis is pending an NSE corporate-action check; that tooling is built, and any correction would create a new dataset version.

**Risk**
- **Why Ledoit-Wolf?** It shrinks the noisy sample covariance, which gives more stable optimizer weights.
- **Component VaR vs volatility contribution?** Both are Euler allocations, but of different risk measures.
- **How does Carino linking work?** It rescales each period's effect by k_t/K, so the effects add up to the compounded active return.

**Optimization**
- **Why can optimization be infeasible?** The constraints can contradict each other; the code checks for this up front and re-checks every result.
