"""Read-only VaR backtest diagnostics for one dataset (nothing is written to the database).

Compares rolling one-day VaR estimators on the same out-of-sample dates
(Kupiec, Christoffersen, conditional coverage) and examines exception
clustering: exceptions per calendar quarter, consecutive exceptions, and the
volatility regime at each exception (20-day realised volatility before the
day divided by the volatility of the estimation window).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH
from portfoliopulse.database import get_candle_dataset, get_dataset_info
from portfoliopulse.portfolio_analysis import compute_phase4_analysis
from portfoliopulse.var_backtests import backtest_var, rolling_var_forecasts

CANDIDATES = (  # (label, method, window, decay)
    ("historical_250 (stored engine)", "historical", 250, None),
    ("historical_125", "historical", 125, None),
    ("normal_250 (stored engine)", "normal", 250, None),
    ("age_weighted_250_decay0.99", "age_weighted", 250, 0.99),
    ("age_weighted_250_decay0.97", "age_weighted", 250, 0.97),
)


def diagnose(returns: pd.Series, *, start: int = 250, levels=(0.95, 0.99)) -> dict:
    results, clustering = [], {}
    test_dates = returns.index[start:]
    for level in levels:
        for label, method, window, decay in CANDIDATES:
            frame = rolling_var_forecasts(returns, window=window, confidence=level, method=method,
                                          **({"decay": decay} if decay is not None else {}))
            frame = frame.loc[frame.index.isin(test_dates)]  # identical evaluation dates for every estimator
            summary = backtest_var(frame, confidence=level)
            results.append({"confidence": level, "estimator": label, "observations": summary["observations"],
                            "exceptions": summary["exceptions"], "expected": round(summary["expected_exceptions"], 2),
                            "exception_rate": summary["exception_rate"], "kupiec_p": summary["kupiec_p_value"],
                            "christoffersen_p": summary["christoffersen_p_value"],
                            "conditional_coverage_p": summary["conditional_coverage_p_value"],
                            "consecutive_exceptions": summary["transitions"]["n11"],
                            "mean_var": float(frame["var_forecast"].mean())})
            if label.startswith("historical_250"):
                hits = frame.index[frame["exception"]]
                vol_ratio = []
                for day in hits:
                    position = returns.index.get_loc(day)
                    recent = returns.iloc[position - 20:position].std(ddof=1)
                    window_vol = returns.iloc[position - window:position].std(ddof=1)
                    vol_ratio.append(recent / window_vol)
                by_quarter = pd.Series(1, index=hits).groupby(hits.to_period("Q")).sum()
                clustering[f"c{level * 100:g}"] = {
                    "exception_dates": [f"{d:%Y-%m-%d}" for d in hits],
                    "exceptions_by_quarter": {str(k): int(v) for k, v in by_quarter.items()},
                    "median_recent_to_window_vol_ratio_at_exceptions": float(np.median(vol_ratio)) if vol_ratio else None,
                    "median_recent_to_window_vol_ratio_all_days": float(np.median([
                        returns.iloc[p - 20:p].std(ddof=1) / returns.iloc[p - window:p].std(ddof=1)
                        for p in range(start, len(returns))])),
                }
    return {"results": results, "clustering": clustering,
            "first_test_date": f"{test_dates[0]:%Y-%m-%d}", "last_test_date": f"{test_dates[-1]:%Y-%m-%d}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", type=int, default=1)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--json", type=Path, default=None, help="Optional path for the full JSON output")
    args = parser.parse_args()
    info = get_dataset_info(args.dataset_id, path=args.database, read_only=True)
    phase4 = compute_phase4_analysis(args.dataset_id, info,
                                     get_candle_dataset(args.dataset_id, path=args.database, read_only=True))
    returns = phase4.returns.daily_comparison_returns["portfolio"]
    report = diagnose(returns)
    print(f"Dataset {args.dataset_id} ({info['source_name']}, {info['verification_status']}); "
          f"{len(returns)} valid returns; test dates {report['first_test_date']} to {report['last_test_date']}")
    table = pd.DataFrame(report["results"])
    print(table.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(json.dumps(report["clustering"], indent=2))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
