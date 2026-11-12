# VaR Backtest Diagnostics — why historical 95% VaR fails Kupiec

**Data:** dataset 1, Yahoo Finance, `user_declared_unverified`. The sample is 686 valid daily portfolio returns, and the out-of-sample test window runs **2025-01-09 → 2026-10-15** (436 days).

**Reproduce (read-only, no database writes):**

```powershell
.\.venv\Scripts\python.exe -m scripts.var_diagnostics --dataset-id 1
```

Stored risk run 9 is **not changed**. Its historical 95% backtest still shows a Kupiec failure, and this report explains it rather than hiding it.

## 1. What was checked first (no defect found)

| Check | Result |
|---|---|
| Exception definition | Exception if r_t < −VaR_t, with VaR_t forecast only from the 250 returns **before** t. Look-ahead is ruled out by a perturbation test (`tests/test_phase3_backtests.py`) |
| Expected count | n·(1−c) = 436 × 0.05 = 21.8 at 95%, and 4.36 at 99% |
| Estimator convention | Nearest rank k = ⌈(1−c)·250⌉, so k = 13 at 95% and k = 3 at 99% |
| Kupiec | LR = −2 ln[(1−p)^(n−x) p^x / ((1−x/n)^(n−x) (x/n)^x)], χ²(1). Hand-worked test (n = 250, x = 5 → 1.95681); the stored LRs recompute exactly (`scripts/audit_consistency.py`) |
| Christoffersen | First-order Markov LR, χ²(1); hand-worked transition-count test |
| Reproducibility | The diagnostic run reproduces the stored run: 33 exceptions, p = 0.0217 |

## 2. Estimator comparison (identical 436 test dates)

| Confidence | Estimator | Exceptions (expected) | Kupiec p | Christoffersen p | Cond. coverage p | Mean VaR |
|---|---|---|---|---|---|---|
| 95% | **Historical, 250-day (stored)** | **33** (21.8) | **0.022 ✗** | 0.340 | **0.046 ✗** | 1.27% |
| 95% | Historical, 125-day | 25 (21.8) | 0.492 | 0.056 | 0.127 | 1.35% |
| 95% | Normal, 250-day (stored) | 24 (21.8) | 0.634 | 0.178 | 0.360 | 1.40% |
| 95% | Age-weighted HS, λ = 0.99 | 23 (21.8) | 0.794 | 0.488 | 0.760 | 1.34% |
| 95% | Age-weighted HS, λ = 0.97 | 25 (21.8) | 0.492 | 0.684 | 0.727 | 1.39% |
| 99% | Historical, 250-day (stored) | 5 (4.36) | 0.763 | 0.733 | 0.902 | 2.10% |
| 99% | Historical, 125-day | 9 (4.36) | 0.051 | 0.169 | 0.058 | 1.91% |
| 99% | Normal, 250-day (stored) | 7 (4.36) | 0.243 | 0.632 | 0.451 | 1.97% |
| 99% | Age-weighted HS, λ = 0.99 | 4 (4.36) | 0.861 | 0.785 | 0.949 | 2.09% |
| 99% | Age-weighted HS, λ = 0.97 | 4 (4.36) | 0.861 | 0.785 | 0.949 | 1.95% |

✗ marks rejection at the 5% level. Age-weighted HS (Boudoukh-Richardson-Whitelaw) is `var_models.age_weighted_historical`. It is tested by hand (λ = 0.5 example), and at λ = 1 it reproduces the nearest-rank estimator exactly.

## 3. Why the stored 95% historical VaR fails

1. **Exceptions cluster by regime, not by day.** The quarterly expectation is about 3.2 exceptions (63 days × 5%), but the historical 95% exceptions are distributed very unevenly:

   | Quarter | 2025-Q1 | 2025-Q2 | 2025-Q3 | 2025-Q4 | 2026-Q1 | 2026-Q2 | 2026-Q3 | 2026-Q4 (partial) |
   |---|---|---|---|---|---|---|---|---|
   | Exceptions | 4 | 4 | 0 | 0 | **13** | 8 | 3 | 1 |

   Calm 2025 H2 had none, while the 2026 sell-off produced 21 in two quarters.
2. **The equal-weighted window reacts too slowly.** On exception days, the median ratio of recent 20-day volatility to estimation-window volatility is **1.02**, against **0.87** across all test days. The 250-day window still weights old calm days equally when volatility rises, so VaR is too low exactly when it matters.
3. **The Christoffersen test misses it.** It tests only whether an exception follows an exception (n11 = 4). Clustering over weeks or months is invisible to a first-order test. The conditional-coverage test rejects (p = 0.046) only because of the Kupiec part.
4. **At 99% the model passes**, with 5 exceptions against 4.36 expected. At that level the test has little power: a handful of exceptions cannot separate good models from bad ones.

## 4. Interpretation and decision

- The failure is a genuine **model** weakness (slow adaptation to volatility regimes), not a coding error. The stored results keep showing it.
- Estimators that weight recent data more are better calibrated on this sample: age-weighted HS, a shorter window, or a parametric model at 95%. Age-weighted HS at λ = 0.99 passes every test at both levels with little extra capital (mean VaR 1.34% vs 1.27%).
- **Not adopted as the default.** The comparison was chosen after seeing the results, on one 1.7-year sample of unverified data, with five candidates tested (a multiple-testing risk). A proper replacement would need a longer, verified history and a pre-registered comparison. A sensible next step would be filtered historical simulation with EWMA or GARCH volatility scaling; it was not built because one short sample cannot validate it.
- **Data caveat:** dataset 1's price basis is unconfirmed. An unadjusted corporate action inside the window would add false exceptions; the ITC demerger day, 2025-01-06, falls before the test window.
