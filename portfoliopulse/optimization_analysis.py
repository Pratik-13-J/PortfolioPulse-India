"""Roadmap Phase 5 portfolio construction orchestration (engine ``phase5_opt_v1``).

Reuses the in-memory Phase 4 buy-and-hold analysis for valuation and returns,
``covariance_estimators`` for Sigma, the Conversation 3 risk building blocks
(through ``portfolio_evaluation``) and the stress module's sector handling.
Persists one ``analysis_metadata`` run plus migration-4 tables
``optimization_runs`` / ``optimization_weights`` / ``optimization_backtests``.
The original holdings (``dataset_holdings``, ``holdings``,
``config/instruments.json``) are read only, never written.

Everything is illustrative and in-sample unless it comes from the walk-forward
out-of-sample evaluation; dataset provenance labels travel with every output.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from portfoliopulse.config import DB_PATH, PROJECT_ROOT, TRADING_DAYS_PER_YEAR
from portfoliopulse.covariance_estimators import covariance_estimates
from portfoliopulse.database import (
    get_candle_dataset, get_dataset_info, initialize, list_optimization_runs, save_analysis_run,
    save_optimization_runs,
)
from portfoliopulse.expected_returns import ExpectedReturns, estimate
from portfoliopulse.optimization import (
    Constraints, OptimizationResult, build_problem, check_feasibility, efficient_frontier, max_sharpe,
    min_variance, risk_parity, validate_covariance,
)
from portfoliopulse.optimization_backtest import WalkForwardResult, walk_forward
from portfoliopulse.portfolio_allocation import CostModel, integer_allocation, rebalance_allocation
from portfoliopulse.portfolio_analysis import Phase4Analysis, compute_phase4_analysis
from portfoliopulse.portfolio_evaluation import evaluate
from portfoliopulse.risk_analysis import COMPARISON_LABEL
from portfoliopulse.stress_analysis import jsonable
from portfoliopulse.stress_scenarios import CRLF, LF, normalize_holdings

ENGINE_VERSION = "phase5_opt_v1"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "optimization.json"
CORE_STRATEGIES = ("existing", "equal_weight", "min_variance", "max_sharpe", "risk_parity")


class InsufficientDataError(ValueError):
    """The selected dataset is too short for the configured optimization."""


@dataclass(frozen=True)
class OptimizationAnalysis:
    dataset_id: int
    analysis_id: int | None
    run_ids: dict[str, int]
    source_name: str
    verification_status: str
    price_basis: str
    as_of_date: str
    observations: int
    portfolio_value_inr: float
    investment_inr: float
    expected_returns: dict[str, ExpectedReturns]
    covariance_diagnostics: dict[str, Any]
    results: dict[str, OptimizationResult]
    evaluations: dict[str, dict[str, Any]]
    allocations: dict[str, dict[str, Any]]
    frontier: pd.DataFrame
    walk_forward: WalkForwardResult
    comparison: pd.DataFrame
    tradeoffs: list[str]
    settings: dict[str, Any]
    quality: dict[str, Any]
    metrics: dict[str, tuple[float, str]]


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> tuple[dict[str, Any], str]:
    payload = Path(path).read_bytes()
    config = json.loads(payload.decode("utf-8"))
    for key in ("annual_risk_free_rate", "expected_return_method", "covariance_estimator", "constraints",
                "transaction_costs", "walk_forward", "minimum_observations"):
        if key not in config:
            raise ValueError(f"Optimization config is missing {key}.")
    # Hash content with line endings normalised to LF (Git's stored form), so a CRLF
    # checkout (core.autocrlf) does not change the recorded config identity.
    return config, hashlib.sha256(payload.replace(CRLF, LF)).hexdigest()


def _rules(constraints: Constraints, sectors: pd.Series, symbols: list[str], method: str, estimator: str,
           rf: float) -> dict[str, Any]:
    """Allocation rules for walk-forward: each sees only its estimation window."""
    base = replace(constraints, max_turnover=None)
    problem = build_problem(symbols, sectors, base)

    def cov_of(window: pd.DataFrame) -> pd.DataFrame:
        return covariance_estimates(window)[0][estimator] * TRADING_DAYS_PER_YEAR

    def require(result: OptimizationResult) -> pd.Series:
        if result.weights is None:
            raise ValueError(f"{result.strategy} {result.status}: {result.message[:120]}")
        return result.weights

    return {
        "equal_weight": lambda window: pd.Series(1.0 / len(symbols), index=symbols),
        "min_variance": lambda window: require(min_variance(cov_of(window), problem)),
        "max_sharpe": lambda window: require(max_sharpe(estimate(window, method).annual, cov_of(window), problem,
                                                        annual_risk_free_rate=rf)),
        "risk_parity": lambda window: require(risk_parity(cov_of(window), problem)),
    }


def _tradeoffs(comparison: pd.DataFrame, n_assets: int, n_sectors: int) -> list[str]:
    """Rule-based, number-grounded notes; no forecasts or recommendations."""
    notes = [f"Universe: {n_assets} stocks in {n_sectors} sectors. Concentration measures describe this small "
             "universe only and do not show broad-market diversification."]
    if "existing" not in comparison.index:
        return notes
    base = comparison.loc["existing"]
    for name in ("equal_weight", "min_variance", "max_sharpe", "risk_parity"):
        if name not in comparison.index:
            continue
        row = comparison.loc[name]
        if pd.isna(row.get("ex_ante_volatility")):
            notes.append(f"{name}: no portfolio ({row['status']}); see diagnostics.")
            continue
        notes.append(
            f"{name}: ex-ante volatility {row['ex_ante_volatility']:.2%} vs existing {base['ex_ante_volatility']:.2%}; "
            f"expected return {row['ex_ante_expected_return']:.2%} vs {base['ex_ante_expected_return']:.2%}; "
            f"effective holdings {row['effective_holdings']:.2f} vs {base['effective_holdings']:.2f}; "
            f"turnover from existing {row['turnover_gross_vs_current']:.1%}"
            + (f"; out-of-sample net return {row['oos_net_annualized_return']:.2%} vs existing "
               f"{base['oos_net_annualized_return']:.2%}" if pd.notna(row.get("oos_net_annualized_return"))
               and pd.notna(base.get("oos_net_annualized_return")) else "") + ".")
    notes.append("Expected returns are noisy in-sample means; lower-volatility portfolios rely only on the "
                 "covariance estimate and are usually more robust than return-seeking ones.")
    return notes


def compute_optimization(
    phase4: Phase4Analysis,
    snapshot: list[Mapping[str, Any]],
    *,
    verification_status: str,
    config: Mapping[str, Any],
    config_sha256: str,
    investment_inr: float | None = None,
) -> OptimizationAnalysis:
    returns = phase4.returns
    benchmark = phase4.benchmark_symbol
    holdings, warnings = normalize_holdings(snapshot)
    symbols = list(holdings.index)
    sectors = holdings["sector"]
    assets = returns.asset_daily_returns.loc[:, symbols]
    bench = returns.daily_comparison_returns[benchmark]
    n = len(assets)
    if n < int(config["minimum_observations"]):
        raise InsufficientDataError(
            f"{n} valid daily returns; optimization needs at least {config['minimum_observations']} "
            "(config minimum_observations). Use a longer dataset.")
    rf = float(config["annual_risk_free_rate"])
    method = config["expected_return_method"]
    estimator = config["covariance_estimator"]
    as_of = pd.Timestamp(phase4.risk_weight_date)
    prices = returns.price_grid.loc[as_of, symbols].astype(float)
    current_shares = holdings["quantity"]
    values = returns.holding_values_inr.loc[as_of, symbols].astype(float)
    current = values / values.sum()
    investment = float(investment_inr if investment_inr is not None else config["investment_amount_inr"])
    cost_model = CostModel.from_config(config["transaction_costs"])

    mus = {m: estimate(assets, m) for m in dict.fromkeys([method, *config.get("expected_return_methods_reported", [])])}
    mu = mus[method].annual
    estimates, cov_params = covariance_estimates(assets)
    cov_diag = {name: validate_covariance(matrix * TRADING_DAYS_PER_YEAR) for name, matrix in estimates.items()}
    cov_diag["ledoit_wolf_shrinkage"] = cov_params["ledoit_wolf_shrinkage"]
    cov = estimates[estimator] * TRADING_DAYS_PER_YEAR

    constraints = Constraints.from_config(config["constraints"])
    problem = build_problem(symbols, sectors, constraints, current)
    equal = pd.Series(1.0 / len(symbols), index=symbols, name="weight")
    results: dict[str, OptimizationResult] = {
        "existing": OptimizationResult("existing", "reference", current.rename("weight"),
                                       "Existing fixed-quantity holdings at the as-of date (read only).",
                                       feasibility=check_feasibility(current.to_numpy(), problem)),
        "equal_weight": OptimizationResult("equal_weight", "reference", equal, "1/N benchmark rule.",
                                           feasibility=check_feasibility(equal.to_numpy(), problem)),
        "min_variance": min_variance(cov, problem),
        "max_sharpe": max_sharpe(mu, cov, problem, annual_risk_free_rate=rf),
        "risk_parity": risk_parity(cov, problem),
    }
    for variant in config.get("strategy_variants", []):
        overrides = {k: v for k, v in variant.items() if k in Constraints.__dataclass_fields__}
        variant_problem = build_problem(symbols, sectors, replace(constraints, **overrides), current)
        if variant["base"] == "min_variance":
            results[variant["strategy"]] = min_variance(cov, variant_problem, strategy=variant["strategy"])
        elif variant["base"] == "risk_parity":
            results[variant["strategy"]] = risk_parity(cov, variant_problem, strategy=variant["strategy"])
        else:
            raise ValueError(f"Unsupported variant base {variant['base']!r}.")
    for rate in config.get("risk_free_sensitivity_rates", []):
        name = f"max_sharpe_rf_{float(rate):g}"
        results[name] = max_sharpe(mu, cov, problem, annual_risk_free_rate=float(rate), strategy=name)

    frontier = efficient_frontier(mu, cov, problem, points=int(config.get("frontier_points", 25)),
                                  annual_risk_free_rate=rf)
    wf_config = config["walk_forward"]
    initial = returns.market_weights.loc[assets.index[int(wf_config["estimation_observations"]) - 1], symbols]
    wf = walk_forward(assets, bench, returns.daily_comparison_returns["portfolio"],
                      _rules(constraints, sectors, symbols, method, estimator, rf),
                      initial_weights=initial / initial.sum(), cost_model=cost_model,
                      estimation=int(wf_config["estimation_observations"]),
                      holding=int(wf_config["holding_observations"]), annual_risk_free_rate=rf)

    evaluations, allocations, rows = {}, {}, {}
    for name, result in results.items():
        row: dict[str, Any] = {"status": result.status, "message": result.message}
        if result.weights is not None:
            ev = evaluate(result.weights, asset_returns=assets, benchmark_returns=bench, mu_annual=mu,
                          cov_annual=cov, annual_risk_free_rate=rf, sectors=sectors, current_weights=current,
                          cost_model=cost_model)
            evaluations[name] = ev
            new_money = integer_allocation(result.weights, prices, investment, cost_model)
            rebalance = rebalance_allocation(result.weights, prices, current_shares, cost_model)
            allocations[name] = {"new_money": new_money, "rebalance_existing": rebalance}
            row.update(ev["metrics"])
            row.update({f"sector_weight:{s}": v for s, v in ev["sectors"].items()})
            row.update({"new_money_residual_cash_inr": new_money.residual_cash_inr,
                        "new_money_transaction_cost_inr": new_money.transaction_cost_inr,
                        "rebalance_transaction_cost_inr": rebalance.transaction_cost_inr,
                        "rebalance_residual_cash_inr": rebalance.residual_cash_inr,
                        "max_constraint_violation": result.feasibility.get("max_violation")})
        wf_key = name if wf.summary is not None and name in wf.summary.index else None
        if wf_key is not None:
            row.update({f"oos_{k}": v for k, v in wf.summary.loc[wf_key].items()})
        rows[name] = row
    comparison = pd.DataFrame.from_dict(rows, orient="index")
    tradeoffs = _tradeoffs(comparison, len(symbols), sectors.nunique())

    metrics: dict[str, tuple[float, str]] = {"opt_valid_return_observations": (float(n), "count"),
                                             "opt_portfolio_value_inr": (float(values.sum()), "INR"),
                                             "opt_investment_amount_inr": (investment, "INR")}
    numeric = ("ex_ante_expected_return", "ex_ante_volatility", "ex_ante_sharpe", "in_sample_annualized_return",
               "in_sample_annualized_volatility", "in_sample_sharpe", "in_sample_sortino", "in_sample_beta",
               "in_sample_max_drawdown", "var_1d:historical:c99", "es_1d:historical:c99", "hhi",
               "effective_holdings", "max_weight", "turnover_gross_vs_current", "rebalance_cost_fraction",
               "oos_net_annualized_return", "oos_net_annualized_volatility", "oos_net_sharpe", "oos_net_max_drawdown",
               "oos_total_cost_fraction")
    for name, row in rows.items():
        for key in numeric:
            value = row.get(key)
            if value is not None and np.isfinite(value):
                metrics[f"opt_{key}:{name}"] = (float(value), "ratio")
    for _, point in frontier.iterrows():
        if point["status"] == "optimal":
            metrics[f"opt_frontier_return:{int(point['point'])}"] = (float(point["expected_return"]), "annual_ratio")
            metrics[f"opt_frontier_volatility:{int(point['point'])}"] = (float(point["volatility"]), "annual_ratio")
    for m, est in mus.items():
        for symbol, value in est.annual.items():
            metrics[f"opt_expected_return:{m}:{symbol}"] = (float(value), "annual_ratio")
    settings = jsonable({
        "stage": "roadmap_phase5_portfolio_construction", "engine_version": ENGINE_VERSION,
        "interpretation": config["interpretation"], "comparison_label": COMPARISON_LABEL,
        "source_name": phase4.source_name, "verification_status": verification_status,
        "provenance_status": phase4.provenance_status, "price_basis": phase4.price_basis,
        "as_of_date": phase4.risk_weight_date, "config_version": config.get("config_version"),
        "config_sha256": config_sha256, "annual_risk_free_rate": rf,
        "risk_free_rate_note": config.get("risk_free_rate_note"),
        "expected_return_method": method, "covariance_estimator": estimator,
        "constraints": constraints.describe(), "strategy_variants": config.get("strategy_variants", []),
        "transaction_cost_basis": cost_model.basis, "investment_amount_inr": investment,
        "walk_forward": wf_config, "optimizer": "scipy.optimize SLSQP; ERC log-barrier via L-BFGS-B",
        "in_sample_label": "illustrative, in-sample (constant-mix daily rebalanced, gross of costs)",
        "survivorship_note": "Fixed five-stock universe chosen today; survivorship/selection bias not removable.",
    })
    quality = jsonable({
        "warnings": warnings, "valid_return_observations": n,
        "covariance_diagnostics": cov_diag,
        "expected_return_shrinkage": {m: e.shrinkage_intensity for m, e in mus.items()},
        "expected_return_standard_error": mus[method].standard_error.to_dict(),
        "statuses": {name: {"status": r.status, "message": r.message, "diagnostics": r.diagnostics,
                            "feasibility": r.feasibility} for name, r in results.items()},
        "walk_forward": {"status": wf.status, "reason": wf.reason, "rebalance_dates": wf.rebalance_dates,
                         "failures": wf.failures},
        "reconciliation": {name: ev["reconciliation"] for name, ev in evaluations.items()},
        "tradeoffs": tradeoffs,
    })
    return OptimizationAnalysis(
        dataset_id=phase4.dataset_id, analysis_id=None, run_ids={}, source_name=phase4.source_name,
        verification_status=verification_status, price_basis=phase4.price_basis, as_of_date=phase4.risk_weight_date,
        observations=n, portfolio_value_inr=float(values.sum()), investment_inr=investment, expected_returns=mus,
        covariance_diagnostics=cov_diag, results=results, evaluations=evaluations, allocations=allocations,
        frontier=frontier, walk_forward=wf, comparison=comparison, tradeoffs=tradeoffs, settings=settings,
        quality=quality, metrics={k: v for k, v in metrics.items() if np.isfinite(v[0])},
    )


def _run_rows(result: OptimizationAnalysis) -> list[dict[str, Any]]:
    runs = []
    wf = result.walk_forward
    for name, opt in result.results.items():
        weights = []
        if opt.weights is not None:
            holdings = result.evaluations[name]["holdings"]
            alloc = result.allocations[name]["new_money"]
            for symbol, row in holdings.iterrows():
                weights.append({"symbol": symbol, "sector": row["sector"], "target_weight": float(row["weight"]),
                                "current_weight": float(row["current_weight"]),
                                "integer_shares": int(alloc.shares[symbol]),
                                "allocated_value_inr": float(alloc.shares[symbol] * alloc.prices[symbol]),
                                "allocated_weight": float(alloc.achieved_weights[symbol])})
        backtest = []
        if wf.net is not None and name in wf.net.columns:
            for day in wf.net.index:
                backtest.append({"observation_date": f"{day:%Y-%m-%d}", "gross_return": wf.gross.at[day, name],
                                 "net_return": wf.net.at[day, name], "turnover": wf.turnover.at[day, name],
                                 "cost_fraction": wf.costs.at[day, name]})
        metrics = {k: v for k, v in result.comparison.loc[name].items() if k != "message"}
        if name in result.allocations:
            new_money = result.allocations[name]["new_money"]
            metrics.update({"new_money_invested_inr": new_money.invested_inr,
                            "new_money_budget_inr": new_money.budget_inr})
        runs.append({
            "strategy": name, "status": opt.status,
            "settings": jsonable({"engine_version": ENGINE_VERSION, "label": "illustrative, not a recommendation",
                                  "source_name": result.source_name,
                                  "verification_status": result.verification_status,
                                  "config_sha256": result.settings["config_sha256"]}),
            "diagnostics": jsonable({"message": opt.message, "objective": opt.objective,
                                     "iterations": opt.iterations, "feasibility": opt.feasibility,
                                     **opt.diagnostics}),
            "metrics": jsonable(metrics), "weights": weights, "backtest": backtest,
        })
    return runs


def analyze_saved_dataset_optimization(
    dataset_id: int, *, db_path: Path = DB_PATH, config_path: Path = DEFAULT_CONFIG_PATH,
    investment_inr: float | None = None, save: bool = True,
) -> OptimizationAnalysis:
    """Read one dataset, build and compare portfolios, optionally persist."""
    if save:
        initialize(db_path)  # applies migration 4 (backup first) when pending
    info = get_dataset_info(dataset_id, path=db_path)
    phase4 = compute_phase4_analysis(dataset_id, info, get_candle_dataset(dataset_id, path=db_path))
    config, sha = load_config(config_path)
    result = compute_optimization(phase4, info["portfolio_snapshot"], verification_status=str(info["verification_status"]),
                                  config=config, config_sha256=sha, investment_inr=investment_inr)
    if not save:
        return result
    common = phase4.returns.cumulative_comparison_returns.dropna(how="any").index
    analysis_id = save_analysis_run(
        dataset_id, portfolio_name=phase4.portfolio_name, window_start=f"{common[0]:%Y-%m-%d}",
        window_end=f"{common[-1]:%Y-%m-%d}", settings=result.settings, quality=result.quality,
        metrics=result.metrics, path=db_path,
    )
    existing = list_optimization_runs(analysis_id, path=db_path)
    if existing:
        if [r["strategy"] for r in existing] != list(result.results):
            raise ValueError("Stored optimization runs for this analysis do not match the current strategy set.")
        ids = {r["strategy"]: int(r["optimization_run_id"]) for r in existing}
    else:
        ids = save_optimization_runs(dataset_id=dataset_id, analysis_id=analysis_id, runs=_run_rows(result),
                                     path=db_path)
    return replace(result, analysis_id=analysis_id, run_ids=ids)
