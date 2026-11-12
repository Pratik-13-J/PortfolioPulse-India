"""OLS factor sensitivities (market beta, rate and USD/INR) with standard errors.

Pure functions. For y (n) and regressors X (n x k, with an intercept column
when requested): b = (X'X)^-1 X'y, residuals e = y - X b,
s^2 = e'e / (n - k), SE(b) = sqrt(diag(s^2 (X'X)^-1)), R^2 = 1 - e'e / SST.
Rows with any missing value are excluded (never filled) and counted.

The single-factor market beta equals cov(r_i, r_m) / var(r_m) (ddof=1), the
same estimator as ``performance_metrics.beta``. Sensitivities are in-sample
estimates; they describe the sample, not a guaranteed future response.

Macro factor convention (when a source-labelled factor CSV is supplied):
``rate_change_pp`` = daily change in the 10-year G-sec yield in percentage
points (+1.0 = +100 bp); ``usdinr_change`` = daily simple change of USD/INR
(+0.01 = INR depreciates 1%). A rate sensitivity is therefore the return per
+100 bp and an FX sensitivity the return per +100% USD/INR change (use
``usdinr_change`` moves as decimals, e.g. +0.05 for +5%).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from portfoliopulse.reference_data import ReferenceDataError, read_sidecar

MACRO_FACTORS = ("rate_change_pp", "usdinr_change")


@dataclass(frozen=True)
class OLSResult:
    coefficients: pd.Series
    standard_errors: pd.Series
    r_squared: float
    observations: int
    excluded_rows: int
    residual_std: float


def ols(y: pd.Series, x: pd.DataFrame, *, intercept: bool = True) -> OLSResult:
    """Ordinary least squares on rows where y and every regressor are present."""
    if not isinstance(y, pd.Series) or not isinstance(x, pd.DataFrame):
        raise TypeError("y must be a Series and x a DataFrame.")
    joined = pd.concat([y.rename("__y__"), x], axis=1, join="outer")
    clean = joined.dropna(how="any")
    excluded = len(joined) - len(clean)
    regressors = clean.drop(columns="__y__").astype(float)
    if intercept:
        regressors.insert(0, "alpha", 1.0)
    n, k = regressors.shape
    if n <= k:
        raise ValueError(f"OLS needs more observations ({n}) than coefficients ({k}).")
    design = regressors.to_numpy()
    target = clean["__y__"].to_numpy(dtype=float)
    if not np.isfinite(design).all() or not np.isfinite(target).all():
        raise ValueError("OLS inputs must be finite.")
    if np.linalg.matrix_rank(design) < k:
        raise ValueError("OLS regressors are collinear or constant; sensitivities are undefined.")
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    residuals = target - design @ coef
    rss = float(residuals @ residuals)
    s2 = rss / (n - k)
    se = np.sqrt(np.diag(s2 * np.linalg.inv(design.T @ design)))
    centered = target - target.mean()
    sst = float(centered @ centered)
    r2 = float("nan") if sst == 0 else 1.0 - rss / sst
    return OLSResult(pd.Series(coef, index=regressors.columns), pd.Series(se, index=regressors.columns),
                     r2, int(n), int(excluded), float(np.sqrt(s2)))


def market_betas(asset_returns: pd.DataFrame, benchmark_returns: pd.Series) -> pd.DataFrame:
    """Single-factor OLS beta of each asset on the benchmark, with SE and R^2."""
    rows = {}
    for symbol in asset_returns.columns:
        fit = ols(asset_returns[symbol], benchmark_returns.rename("market").to_frame())
        rows[symbol] = {
            "beta": float(fit.coefficients["market"]), "beta_standard_error": float(fit.standard_errors["market"]),
            "alpha_daily": float(fit.coefficients["alpha"]), "r_squared": fit.r_squared,
            "observations": fit.observations, "residual_std_daily": fit.residual_std,
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def macro_sensitivities(
    asset_returns: pd.DataFrame, benchmark_returns: pd.Series, factors: pd.DataFrame,
) -> pd.DataFrame:
    """OLS of r_i on (r_m, rate_change_pp, usdinr_change); long table of results."""
    missing = set(MACRO_FACTORS) - set(factors.columns)
    if missing:
        raise ValueError(f"Macro factors are missing: {', '.join(sorted(missing))}.")
    regressors = pd.concat([benchmark_returns.rename("market"), factors.loc[:, list(MACRO_FACTORS)]], axis=1)
    rows = []
    for symbol in asset_returns.columns:
        fit = ols(asset_returns[symbol], regressors)
        for factor in ("market", *MACRO_FACTORS):
            rows.append({
                "symbol": symbol, "factor": factor, "sensitivity": float(fit.coefficients[factor]),
                "standard_error": float(fit.standard_errors[factor]), "r_squared": fit.r_squared,
                "observations": fit.observations, "excluded_rows": fit.excluded_rows,
            })
    return pd.DataFrame(rows)


def load_macro_factor_csv(csv_path: Path, sidecar_path: Path | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a source-labelled daily factor file: date, india_10y_yield_pct, usdinr.

    The JSON sidecar must declare source, HTTPS URL, retrieval time, label and
    the file SHA-256 (``reference_data.read_sidecar``). Levels are converted to
    daily changes between consecutive *observed* rows; gaps are not filled.
    """
    payload, sidecar = read_sidecar(Path(csv_path), sidecar_path)
    try:
        frame = pd.read_csv(io.BytesIO(payload))
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise ReferenceDataError("The macro factor CSV cannot be parsed.") from exc
    required = {"date", "india_10y_yield_pct", "usdinr"}
    if required - set(frame.columns):
        raise ReferenceDataError(f"The macro factor CSV needs columns {sorted(required)}.")
    dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    yields = pd.to_numeric(frame["india_10y_yield_pct"], errors="coerce")
    fx = pd.to_numeric(frame["usdinr"], errors="coerce")
    if dates.isna().any() or yields.isna().any() or fx.isna().any() or (fx <= 0).any():
        raise ReferenceDataError("The macro factor CSV has invalid dates, yields or USD/INR levels.")
    levels = pd.DataFrame({"yield": yields.to_numpy(), "usdinr": fx.to_numpy()}, index=pd.DatetimeIndex(dates))
    if levels.index.has_duplicates or not levels.index.is_monotonic_increasing:
        raise ReferenceDataError("Macro factor dates must be unique and increasing.")
    changes = pd.DataFrame({
        "rate_change_pp": levels["yield"].diff(),
        "usdinr_change": levels["usdinr"].pct_change(fill_method=None),
    }).iloc[1:]
    return changes, sidecar
