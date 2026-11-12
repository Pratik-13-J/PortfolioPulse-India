"""Read-only cross-module consistency audit of the stored engine runs for one dataset.

Opens the database with SQLite mode=ro and recomputes reconciliation identities
independently from the stored values (plain arithmetic), plus one fresh
in-memory Phase 4 computation. Prints PASS/FAIL per check; exits 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

from portfoliopulse.config import DB_PATH
from portfoliopulse.database import get_candle_dataset, get_dataset_info
from portfoliopulse.portfolio_analysis import compute_phase4_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", type=int, default=1)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    args = parser.parse_args()
    con = sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True)
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        if not ok:
            failures.append(name)

    def latest(engine: str):
        row = con.execute("""SELECT analysis_id, settings_json FROM analysis_metadata WHERE dataset_id=? AND
                             json_extract(settings_json,'$.engine_version')=? ORDER BY analysis_id DESC LIMIT 1""",
                          (args.dataset_id, engine)).fetchone()
        if row is None:
            return None, {}, {}
        metrics = {n: v for n, v in con.execute("SELECT metric_name, value FROM analysis_results WHERE analysis_id=?",
                                                (row[0],))}
        return row[0], json.loads(row[1]), metrics

    risk_id, risk_settings, risk = latest("phase3_risk_v1")
    stress_id, stress_settings, stress = latest("phase4_stress_v1")
    opt_id, opt_settings, opt = latest("phase5_opt_v1")
    print(f"Dataset {args.dataset_id}: risk run {risk_id}, stress run {stress_id}, optimization run {opt_id}")

    info = get_dataset_info(args.dataset_id, path=args.database, read_only=True)
    candles = get_candle_dataset(args.dataset_id, path=args.database, read_only=True)
    phase4 = compute_phase4_analysis(args.dataset_id, info, candles)
    as_of = phase4.risk_weight_date
    closes = candles[candles["date"] == as_of].set_index("symbol")["close"]
    quantities = {r["symbol"]: float(r["quantity"]) for r in info["portfolio_snapshot"] if not r["is_benchmark"]}
    value = math.fsum(quantities[s] * float(closes[s]) for s in quantities)

    # Cross-module identity: dates, value, annualization.
    for label, settings, key in (("risk", risk_settings, "risk_weight_date"), ("stress", stress_settings, "as_of_date"),
                                 ("optimization", opt_settings, "as_of_date")):
        check(f"{label} as-of date = Phase 4 risk-weight date", settings.get(key) == as_of, f"{settings.get(key)} vs {as_of}")
    for label, metrics, key in (("risk", risk, "risk_portfolio_value_inr"), ("stress", stress, "stress_portfolio_value_inr"),
                                ("optimization", opt, "opt_portfolio_value_inr")):
        check(f"{label} portfolio value = sum(q x close) on {as_of}", math.isclose(metrics.get(key, -1), value, rel_tol=1e-12),
              f"{metrics.get(key)} vs {value}")
    check("risk annualization = 252", risk_settings.get("annualization_observations") == 252)
    check("risk N = Phase 4 valid returns", risk.get("risk_valid_return_observations") ==
          len(phase4.returns.daily_comparison_returns))
    check("historical 95% VaR (risk run) = Phase 4 portfolio_var_1d (fresh)",
          risk.get("var_1d:historical:c95") == phase4.metrics["portfolio_var_1d"])
    check("optimization rf = 6% assumption", opt_settings.get("annual_risk_free_rate") == 0.06)

    # VaR / ES conventions and INR conversion.
    for name, var in risk.items():
        if name.startswith("var_1d:") and name.count(":") == 2:
            suffix = name[len("var_1d:"):]
            es = risk.get(f"es_1d:{suffix}")
            inr = risk.get(f"var_1d_inr:{suffix}")
            if inr is not None and not math.isclose(inr, var * value, rel_tol=1e-12):
                check(f"VaR INR = VaR x value ({suffix})", False)
            monotone = not suffix.startswith("cornish_fisher") or json.loads(con.execute(
                "SELECT quality_json FROM analysis_metadata WHERE analysis_id=?", (risk_id,)).fetchone()[0]
            )["cornish_fisher_tail_monotone"].get(f"portfolio:{suffix.split(':')[1]}", False)
            if es is not None and monotone and es < var - 1e-15:
                check(f"ES >= VaR ({suffix})", False, f"ES {es} < VaR {var}")
    check("VaR INR conversions and ES >= VaR for every stored method", not any(f.startswith(("VaR INR", "ES >="))
                                                                              for f in failures))
    for estimator in ("sample", "ledoit_wolf", "ewma"):
        for level in ("c95", "c99"):
            parts = [v for n, v in risk.items() if n.startswith(f"component_var_1d:delta_normal_{estimator}:{level}:")]
            check(f"component VaR sums to total ({estimator} {level})",
                  math.isclose(math.fsum(parts), risk[f"var_1d:delta_normal_{estimator}:{level}"], rel_tol=1e-12))
    for level in ("c95", "c99"):
        parts = [v for n, v in risk.items() if n.startswith(f"component_es_1d:monte_carlo_bootstrap:{level}:")]
        check(f"bootstrap component ES sums to MC ES ({level})",
              math.isclose(math.fsum(parts), risk[f"es_1d:monte_carlo_bootstrap:{level}"], rel_tol=1e-12))

    # Kupiec recomputed from stored counts with plain math.
    for method in ("historical", "normal"):
        for level, p in (("c95", 0.05), ("c99", 0.01)):
            n = int(risk[f"backtest_observations:{method}:{level}"])
            x = int(risk[f"backtest_exceptions:{method}:{level}"])
            rate = x / n
            ll0 = (n - x) * math.log(1 - p) + (x * math.log(p) if x else 0.0)
            ll1 = ((n - x) * math.log(1 - rate) if x < n else 0.0) + (x * math.log(rate) if x else 0.0)
            check(f"Kupiec LR recomputed ({method} {level}: {x}/{n})",
                  math.isclose(-2 * (ll0 - ll1), risk[f"backtest_kupiec_lr:{method}:{level}"], rel_tol=1e-9, abs_tol=1e-12))

    # Stress: holding contributions reconcile; percent = INR / value.
    rows = con.execute("SELECT scenario_run_id, impact_inr, impact_pct, portfolio_value_inr FROM scenario_runs "
                       "WHERE analysis_id=?", (stress_id,)).fetchall()
    ok = True
    for run_id, impact, pct, run_value in rows:
        total = math.fsum(r[0] for r in con.execute(
            "SELECT impact_inr FROM scenario_contributions WHERE scenario_run_id=?", (run_id,)))
        ok &= math.isclose(total, impact, rel_tol=1e-12, abs_tol=1e-9) and math.isclose(pct, impact / run_value, rel_tol=1e-12)
        ok &= math.isclose(run_value, value, rel_tol=1e-12)
    check(f"stress contributions reconcile for all {len(rows)} scenarios", ok and bool(rows))

    # Optimization: weights, bounds, sector cap, allocation budget identity, walk-forward window.
    constraints = opt_settings["constraints"]
    ok = True
    for run_id, strategy, status, metrics_json in con.execute(
            "SELECT optimization_run_id, strategy, status, metrics_json FROM optimization_runs WHERE analysis_id=?", (opt_id,)):
        weights = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT symbol, target_weight, sector FROM optimization_weights WHERE optimization_run_id=?", (run_id,))}
        if not weights:
            ok &= status in ("undefined", "infeasible")
            continue
        ok &= math.isclose(math.fsum(w for w, _ in weights.values()), 1.0, abs_tol=1e-9)
        if status in ("optimal", "constrained"):
            ok &= all(constraints["min_weight"] - 1e-9 <= w <= constraints["max_weight"] + 1e-9 for w, _ in weights.values())
            for sector, cap in constraints["sector_max"].items():
                ok &= math.fsum(w for w, sec in weights.values() if sec == sector) <= cap + 1e-9
        m = json.loads(metrics_json)
        ok &= math.isclose(m["new_money_invested_inr"] + m["new_money_transaction_cost_inr"] + m["new_money_residual_cash_inr"],
                           m["new_money_budget_inr"], rel_tol=1e-12) and m["new_money_residual_cash_inr"] >= 0
    check("optimization weights sum to 1, constraints hold, allocation budget reconciles", ok)
    estimation = opt_settings["walk_forward"]["estimation_observations"]
    dates = [r[0] for r in con.execute(
        """SELECT DISTINCT b.observation_date FROM optimization_backtests b JOIN optimization_runs r
           USING(optimization_run_id) WHERE r.analysis_id=? ORDER BY 1""", (opt_id,))]
    returns_index = [d.strftime("%Y-%m-%d") for d in phase4.returns.asset_daily_returns.index]
    check("walk-forward starts after the estimation window (no look-ahead)",
          bool(dates) and dates[0] == returns_index[estimation] and len(dates) == len(returns_index) - estimation,
          f"first OOS {dates[0] if dates else None}, returns[{estimation}] {returns_index[estimation]}")

    dupes = con.execute("""SELECT json_extract(settings_json,'$.engine_version') e, COUNT(*) FROM analysis_metadata
                           WHERE dataset_id=? AND e IN ('phase3_risk_v1','phase4_stress_v1','phase5_opt_v1')
                           GROUP BY e HAVING COUNT(*) > 1""", (args.dataset_id,)).fetchall()
    check("no duplicate Conv 3-5 engine runs", not dupes, str(dupes))
    check("integrity_check ok", con.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    check("no foreign-key violations", not con.execute("PRAGMA foreign_key_check").fetchall())
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
