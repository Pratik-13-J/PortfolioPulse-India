"""Constrained long-only portfolio optimization with SciPy (pure functions).

Inputs are *annual* expected returns mu and an *annual* covariance Sigma
(validated symmetric, finite and positive semidefinite). Weights are fully
invested (sum w = 1); residual cash only arises later, in integer-share
allocation. Objectives:

* minimum variance: min w' Sigma w
* maximum Sharpe:   max (mu' w - rf) / sqrt(w' Sigma w); undefined (reported,
  not forced) when no feasible portfolio has mu' w > rf
* risk parity (ERC): unconstrained long-only ERC via the log-barrier problem
  min 1/2 y' Sigma y - (1/n) sum ln y_i, w = y / sum y (Spinu 2013); if that
  violates the configured constraints, a constrained fallback minimises
  sum_i (RC_i - 1/n)^2 with RC_i = w_i (Sigma w)_i / w' Sigma w, and the
  achieved dispersion is reported
* efficient frontier: min variance subject to mu' w = target for targets
  between the minimum-variance return and the maximum feasible return

Constraints: per-asset bounds (min/max weight, the max doubling as the
single-name concentration cap), sector minimum/maximum, and turnover
sum |w - w_current| <= T, linearised with auxiliary variables
t_i >= |w_i - w_current,i|. Every result is re-checked for feasibility; a
solver "success" that violates a constraint is not reported as optimal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

FEASIBILITY_TOLERANCE = 1e-7


class OptimizationError(ValueError):
    """Raised for invalid inputs or infeasible constraint sets."""


@dataclass(frozen=True)
class Constraints:
    min_weight: float | Mapping[str, float] = 0.0
    max_weight: float | Mapping[str, float] = 1.0
    sector_max: Mapping[str, float] = field(default_factory=dict)
    sector_min: Mapping[str, float] = field(default_factory=dict)
    max_turnover: float | None = None
    long_only: bool = True
    fully_invested: bool = True

    @classmethod
    def from_config(cls, config: Mapping[str, Any], **overrides: Any) -> "Constraints":
        values = {k: config[k] for k in ("min_weight", "max_weight", "sector_max", "sector_min",
                                         "max_turnover", "long_only", "fully_invested") if k in config}
        values.update(overrides)
        return cls(**values)

    def describe(self) -> dict[str, Any]:
        return {"min_weight": self.min_weight, "max_weight": self.max_weight,
                "sector_max": dict(self.sector_max), "sector_min": dict(self.sector_min),
                "max_turnover": self.max_turnover, "long_only": self.long_only,
                "fully_invested": self.fully_invested}


@dataclass(frozen=True)
class OptimizationResult:
    strategy: str
    status: str  # optimal | constrained | infeasible | undefined | failed | reference
    weights: pd.Series | None
    message: str
    objective: float | None = None
    iterations: int = 0
    feasibility: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Problem:
    symbols: list[str]
    sectors: pd.Series
    lower: np.ndarray
    upper: np.ndarray
    constraints: Constraints
    current: np.ndarray | None


def validate_covariance(covariance: pd.DataFrame, *, tolerance: float = 1e-12) -> dict[str, Any]:
    """Require a finite, symmetric, positive semidefinite square matrix."""
    if not isinstance(covariance, pd.DataFrame) or not covariance.index.equals(covariance.columns):
        raise OptimizationError("Covariance must be a square DataFrame with matching labels.")
    sigma = covariance.to_numpy(dtype=float)
    if not np.isfinite(sigma).all():
        raise OptimizationError("Covariance contains non-finite values.")
    asymmetry = float(np.max(np.abs(sigma - sigma.T)))
    if asymmetry > tolerance * max(1.0, float(np.max(np.abs(sigma)))):
        raise OptimizationError("Covariance is not symmetric.")
    eigenvalues = np.linalg.eigvalsh((sigma + sigma.T) / 2)
    scale = max(1.0, float(abs(eigenvalues).max()))
    if eigenvalues.min() < -tolerance * scale:
        raise OptimizationError(f"Covariance is not positive semidefinite (min eigenvalue {eigenvalues.min():.3e}).")
    return {"symmetric": True, "positive_semidefinite": True, "min_eigenvalue": float(eigenvalues.min()),
            "max_eigenvalue": float(eigenvalues.max()),
            "condition_number": float(eigenvalues.max() / eigenvalues.min()) if eigenvalues.min() > 0 else math.inf,
            "max_asymmetry": asymmetry}


def _per_asset(value: float | Mapping[str, float], symbols: Sequence[str], label: str) -> np.ndarray:
    if isinstance(value, Mapping):
        unknown = set(value) - set(symbols)
        if unknown:
            raise OptimizationError(f"Unknown {label} symbols: {', '.join(sorted(unknown))}.")
        default = value.get("*")
        out = [value.get(s, default) for s in symbols]
        if any(v is None for v in out):
            raise OptimizationError(f"{label} must cover every symbol or give a '*' default.")
        return np.array(out, dtype=float)
    return np.full(len(symbols), float(value))


def build_problem(
    symbols: Sequence[str], sectors: Mapping[str, str] | pd.Series, constraints: Constraints,
    current_weights: Mapping[str, float] | pd.Series | None = None,
) -> Problem:
    """Validate bounds and a necessary-condition feasibility check."""
    symbols = list(symbols)
    if not constraints.fully_invested:
        raise OptimizationError("Only fully invested optimization is supported; cash appears in allocation.")
    sector = pd.Series(sectors).reindex(symbols)
    if sector.isna().any():
        raise OptimizationError(f"Missing sector for {', '.join(sector.index[sector.isna()])}.")
    lower = _per_asset(constraints.min_weight, symbols, "min_weight")
    upper = _per_asset(constraints.max_weight, symbols, "max_weight")
    if constraints.long_only and (lower < 0).any():
        raise OptimizationError("Long-only optimization needs non-negative minimum weights.")
    if constraints.long_only:
        lower = np.maximum(lower, 0.0)
    if (lower > upper).any() or (upper > 1).any():
        raise OptimizationError("Each weight bound needs min <= max <= 1.")
    if lower.sum() > 1 + 1e-12 or upper.sum() < 1 - 1e-12:
        raise OptimizationError(f"Infeasible: sum of minimum weights {lower.sum():.4f} and maximum weights "
                                f"{upper.sum():.4f} cannot reach a fully invested portfolio.")
    for name, cap in constraints.sector_max.items():
        members = (sector == name).to_numpy()
        if not members.any():
            raise OptimizationError(f"Sector cap for unknown sector {name!r}.")
        if lower[members].sum() > cap + 1e-12:
            raise OptimizationError(f"Infeasible: minimum weights in {name} exceed its {cap:.0%} cap.")
    for name, floor in constraints.sector_min.items():
        members = (sector == name).to_numpy()
        if not members.any():
            raise OptimizationError(f"Sector floor for unknown sector {name!r}.")
        if upper[members].sum() < floor - 1e-12:
            raise OptimizationError(f"Infeasible: maximum weights in {name} cannot reach its {floor:.0%} floor.")
    current = None
    if current_weights is not None:
        current = pd.Series(current_weights, dtype=float).reindex(symbols).to_numpy()
        if np.isnan(current).any() or not math.isclose(current.sum(), 1.0, abs_tol=1e-8):
            raise OptimizationError("Current weights must cover every symbol and sum to one.")
    if constraints.max_turnover is not None:
        if current is None:
            raise OptimizationError("A turnover limit needs the current weights.")
        if constraints.max_turnover < 0:
            raise OptimizationError("max_turnover must be non-negative.")
    return Problem(symbols, sector, lower, upper, constraints, current)


def check_feasibility(weights: np.ndarray | pd.Series, problem: Problem) -> dict[str, float]:
    """Largest violation of each constraint family (0 means satisfied)."""
    w = np.asarray(weights, dtype=float)
    out = {
        "budget": abs(float(w.sum()) - 1.0),
        "lower_bound": float(max(0.0, (problem.lower - w).max())),
        "upper_bound": float(max(0.0, (w - problem.upper).max())),
        "sector_max": 0.0, "sector_min": 0.0, "turnover": 0.0,
    }
    sectors = problem.sectors.to_numpy()
    for name, cap in problem.constraints.sector_max.items():
        out["sector_max"] = max(out["sector_max"], float(w[sectors == name].sum() - cap))
    for name, floor in problem.constraints.sector_min.items():
        out["sector_min"] = max(out["sector_min"], float(floor - w[sectors == name].sum()))
    if problem.constraints.max_turnover is not None:
        out["turnover"] = max(0.0, float(np.abs(w - problem.current).sum() - problem.constraints.max_turnover))
    out["sector_max"] = max(0.0, out["sector_max"])
    out["sector_min"] = max(0.0, out["sector_min"])
    out["max_violation"] = max(out.values())
    return out


def _scipy_constraints(problem: Problem, n: int, extra: Sequence[dict]) -> list[dict]:
    sectors = problem.sectors.to_numpy()
    cons: list[dict] = [{"type": "eq", "fun": lambda x: x[:n].sum() - 1.0,
                         "jac": lambda x: np.r_[np.ones(n), np.zeros(len(x) - n)]}]
    for name, cap in problem.constraints.sector_max.items():
        mask = (sectors == name).astype(float)
        cons.append({"type": "ineq", "fun": lambda x, m=mask, c=cap: c - m @ x[:n],
                     "jac": lambda x, m=mask: np.r_[-m, np.zeros(len(x) - n)]})
    for name, floor in problem.constraints.sector_min.items():
        mask = (sectors == name).astype(float)
        cons.append({"type": "ineq", "fun": lambda x, m=mask, f=floor: m @ x[:n] - f,
                     "jac": lambda x, m=mask: np.r_[m, np.zeros(len(x) - n)]})
    if problem.constraints.max_turnover is not None:
        w0, cap = problem.current, problem.constraints.max_turnover
        eye = np.eye(n)
        cons += [
            {"type": "ineq", "fun": lambda x: x[n:] - (x[:n] - w0), "jac": lambda x: np.hstack([-eye, eye])},
            {"type": "ineq", "fun": lambda x: x[n:] + (x[:n] - w0), "jac": lambda x: np.hstack([eye, eye])},
            {"type": "ineq", "fun": lambda x: cap - x[n:].sum(), "jac": lambda x: np.r_[np.zeros(n), -np.ones(n)]},
        ]
    return cons + [dict(item) for item in extra]


def _starts(problem: Problem) -> list[np.ndarray]:
    n = len(problem.symbols)
    starts = [np.full(n, 1.0 / n), (problem.lower + problem.upper) / 2]
    if problem.current is not None:
        starts.insert(0, problem.current.copy())
    projected = []
    for start in starts:
        w = np.clip(start, problem.lower, problem.upper)
        projected.append(w / w.sum())
    return projected


def solve(
    strategy: str, problem: Problem, objective: Callable[[np.ndarray], float],
    gradient: Callable[[np.ndarray], np.ndarray] | None = None, *, extra: Sequence[dict] = (),
) -> OptimizationResult:
    """SLSQP from several feasible-ish starts; keep the best feasible solution."""
    n = len(problem.symbols)
    turnover = problem.constraints.max_turnover is not None
    bounds = list(zip(problem.lower, problem.upper)) + ([(0.0, 2.0)] * n if turnover else [])

    def f(x):
        return objective(x[:n])

    def g(x):
        return np.r_[gradient(x[:n]), np.zeros(len(x) - n)]

    best: OptimizationResult | None = None
    messages = []
    for start in _starts(problem):
        x0 = np.r_[start, np.abs(start - problem.current)] if turnover else start
        res = minimize(f, x0, jac=g if gradient is not None else None, method="SLSQP", bounds=bounds,
                       constraints=_scipy_constraints(problem, n, extra),
                       options={"ftol": 1e-14, "maxiter": 1000})
        weights = np.clip(res.x[:n], problem.lower, problem.upper)
        feasibility = check_feasibility(weights, problem)
        for item in extra:
            if item["type"] == "eq":
                feasibility["extra_equality"] = abs(float(item["fun"](np.r_[weights, res.x[n:]])))
                feasibility["max_violation"] = max(feasibility["max_violation"], feasibility["extra_equality"])
        messages.append(str(res.message))
        ok = bool(res.success) and feasibility["max_violation"] <= FEASIBILITY_TOLERANCE
        if not ok:
            continue
        candidate = OptimizationResult(
            strategy, "optimal", pd.Series(weights, index=problem.symbols, name="weight"), str(res.message),
            float(objective(weights)), int(res.nit), feasibility)
        if best is None or candidate.objective < best.objective - 1e-15:
            best = candidate
    if best is None:
        return OptimizationResult(strategy, "infeasible", None,
                                  "No start converged to a feasible solution: " + "; ".join(sorted(set(messages))))
    return best


def portfolio_volatility(weights: np.ndarray, sigma: np.ndarray) -> float:
    return float(math.sqrt(max(0.0, float(weights @ sigma @ weights))))


def risk_contributions(weights: pd.Series, covariance: pd.DataFrame) -> pd.DataFrame:
    """Euler volatility contributions; components sum to sigma_p."""
    w = weights.reindex(covariance.columns).to_numpy(dtype=float)
    sigma = covariance.to_numpy(dtype=float)
    vol = portfolio_volatility(w, sigma)
    if vol == 0:
        raise OptimizationError("Portfolio volatility is zero; risk contributions are undefined.")
    marginal = sigma @ w / vol
    component = w * marginal
    return pd.DataFrame({"weight": w, "marginal_volatility": marginal, "component_volatility": component,
                         "risk_share": component / vol}, index=covariance.columns)


def min_variance(covariance: pd.DataFrame, problem: Problem, *, strategy: str = "min_variance") -> OptimizationResult:
    sigma = covariance.reindex(index=problem.symbols, columns=problem.symbols).to_numpy(dtype=float)
    result = solve(strategy, problem, lambda w: float(w @ sigma @ w), lambda w: 2 * sigma @ w)
    return _with_kkt(result, sigma, problem)


def _with_kkt(result: OptimizationResult, sigma: np.ndarray, problem: Problem) -> OptimizationResult:
    """KKT stationarity on free (non-binding) assets: 2 (Sigma w)_i equal across them."""
    if result.weights is None or problem.constraints.sector_max or problem.constraints.sector_min \
            or problem.constraints.max_turnover is not None:
        return result
    w = result.weights.to_numpy()
    free = (w > problem.lower + 1e-6) & (w < problem.upper - 1e-6)
    grad = 2 * sigma @ w
    spread = float(grad[free].max() - grad[free].min()) if free.sum() >= 2 else 0.0
    return OptimizationResult(**{**result.__dict__, "diagnostics": {**result.diagnostics,
                                                                     "kkt_free_gradient_spread": spread,
                                                                     "free_assets": int(free.sum())}})


def max_return(mu: pd.Series, problem: Problem) -> OptimizationResult:
    m = mu.reindex(problem.symbols).to_numpy(dtype=float)
    return solve("max_return", problem, lambda w: -float(m @ w), lambda w: -m)


def max_sharpe(mu: pd.Series, covariance: pd.DataFrame, problem: Problem, *, annual_risk_free_rate: float,
               strategy: str = "max_sharpe") -> OptimizationResult:
    m = mu.reindex(problem.symbols).to_numpy(dtype=float)
    sigma = covariance.reindex(index=problem.symbols, columns=problem.symbols).to_numpy(dtype=float)
    rf = float(annual_risk_free_rate)
    best_return = max_return(mu, problem)
    if best_return.weights is None:
        return OptimizationResult(strategy, "infeasible", None, best_return.message)
    top = float(m @ best_return.weights.to_numpy())
    if top <= rf:
        return OptimizationResult(
            strategy, "undefined", None,
            f"No feasible portfolio has expected return above the risk-free rate ({top:.4%} <= {rf:.4%}); "
            "maximum Sharpe is undefined rather than forced to a maximum-volatility corner.",
            diagnostics={"max_feasible_expected_return": top, "annual_risk_free_rate": rf})

    def neg_sharpe(w):
        return -(float(m @ w) - rf) / portfolio_volatility(w, sigma)

    def grad(w):
        vol = portfolio_volatility(w, sigma)
        excess = float(m @ w) - rf
        return -(m / vol - excess * (sigma @ w) / vol ** 3)

    result = solve(strategy, problem, neg_sharpe, grad)
    if result.weights is not None:
        result = OptimizationResult(**{**result.__dict__, "objective": -result.objective,
                                       "diagnostics": {"sharpe": -neg_sharpe(result.weights.to_numpy()),
                                                       "annual_risk_free_rate": rf,
                                                       "max_feasible_expected_return": top}})
    return result


def erc_log_barrier(covariance: pd.DataFrame) -> pd.Series:
    """Unconstrained long-only equal-risk-contribution weights (log-barrier)."""
    sigma = covariance.to_numpy(dtype=float)
    n = sigma.shape[0]
    budget = np.full(n, 1.0 / n)
    y0 = 1.0 / np.sqrt(np.diag(sigma))
    res = minimize(lambda y: 0.5 * y @ sigma @ y - budget @ np.log(y), y0 / y0.sum(),
                   jac=lambda y: sigma @ y - budget / y, method="L-BFGS-B",
                   bounds=[(1e-12, None)] * n, options={"ftol": 1e-16, "gtol": 1e-14, "maxiter": 10_000})
    if not res.success:
        raise OptimizationError(f"ERC log-barrier did not converge: {res.message}")
    return pd.Series(res.x / res.x.sum(), index=covariance.columns, name="weight")


def risk_parity(covariance: pd.DataFrame, problem: Problem, *, strategy: str = "risk_parity") -> OptimizationResult:
    cov = covariance.reindex(index=problem.symbols, columns=problem.symbols)
    sigma = cov.to_numpy(dtype=float)
    n = len(problem.symbols)
    exact = erc_log_barrier(cov)
    feasibility = check_feasibility(exact.to_numpy(), problem)

    def dispersion(w: np.ndarray) -> float:
        shares = w * (sigma @ w) / float(w @ sigma @ w)
        return float(np.max(np.abs(shares - 1.0 / n)))

    if feasibility["max_violation"] <= FEASIBILITY_TOLERANCE:
        return OptimizationResult(strategy, "optimal", exact, "log-barrier ERC satisfies all constraints",
                                  dispersion(exact.to_numpy()), 0, feasibility,
                                  {"method": "log_barrier", "max_risk_share_deviation": dispersion(exact.to_numpy())})

    def objective(w):
        shares = w * (sigma @ w) / float(w @ sigma @ w)
        return float(((shares - 1.0 / n) ** 2).sum())

    result = solve(strategy, problem, objective)
    if result.weights is None:
        return result
    return OptimizationResult(
        strategy, "constrained", result.weights,
        "Exact ERC violates the constraints; closest constrained risk-parity solution reported.",
        result.objective, result.iterations, result.feasibility,
        {"method": "constrained_least_squares_risk_shares",
         "max_risk_share_deviation": dispersion(result.weights.to_numpy()),
         "unconstrained_erc_weights": exact.to_dict(),
         "unconstrained_erc_violations": feasibility})


def efficient_frontier(mu: pd.Series, covariance: pd.DataFrame, problem: Problem, *, points: int = 25,
                       annual_risk_free_rate: float = 0.0) -> pd.DataFrame:
    """Min-variance portfolios at evenly spaced target returns (>= 2 points)."""
    if points < 2:
        raise OptimizationError("The frontier needs at least two points.")
    m = mu.reindex(problem.symbols).to_numpy(dtype=float)
    sigma = covariance.reindex(index=problem.symbols, columns=problem.symbols).to_numpy(dtype=float)
    low = min_variance(covariance, problem)
    high = max_return(mu, problem)
    if low.weights is None or high.weights is None:
        raise OptimizationError("The constraint set is infeasible; no frontier exists.")
    r_low, r_high = float(m @ low.weights.to_numpy()), float(m @ high.weights.to_numpy())
    rows = []
    for k, target in enumerate(np.linspace(r_low, r_high, points)):
        if k == 0:
            result = low
        else:
            n = len(m)
            result = solve(f"frontier_{k}", problem, lambda w: float(w @ sigma @ w), lambda w: 2 * sigma @ w,
                           extra=[{"type": "eq", "fun": lambda x, t=target: float(m @ x[:n]) - t,
                                   "jac": lambda x: np.r_[m, np.zeros(len(x) - n)]}])
        row: dict[str, Any] = {"point": k, "target_return": float(target), "status": result.status}
        if result.weights is not None:
            w = result.weights.to_numpy()
            vol = portfolio_volatility(w, sigma)
            row.update({"expected_return": float(m @ w), "volatility": vol,
                        "sharpe": (float(m @ w) - annual_risk_free_rate) / vol if vol > 0 else np.nan,
                        **{f"w:{s}": float(x) for s, x in zip(problem.symbols, w)}})
        rows.append(row)
    return pd.DataFrame(rows)
