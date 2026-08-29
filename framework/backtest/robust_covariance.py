"""Point-in-time covariance estimators shared by allocation optimizers.

The estimator follows the production sequence used by multi-factor risk
models: exponentially weighted observations, Newey-West serial-correlation
adjustment, shrinkage toward a diagonal target, volatility-regime scaling and
an explicit positive-semidefinite projection.  Every input observation must be
available before the portfolio decision timestamp.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RobustCovarianceDiagnostics:
    observations: int
    assets: int
    half_life: float
    newey_west_lags: int
    diagonal_shrinkage: float
    regime_variance_multiplier: float
    minimum_eigenvalue_before_projection: float
    minimum_eigenvalue_after_projection: float
    condition_number: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def nearest_positive_semidefinite(
    covariance: np.ndarray,
    *,
    relative_floor: float = 1.0e-7,
) -> np.ndarray:
    """Return a symmetric PSD matrix with a scale-aware eigenvalue floor."""

    matrix = np.asarray(covariance, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("covariance_must_be_square")
    matrix = np.nan_to_num((matrix + matrix.T) / 2.0)
    values, vectors = np.linalg.eigh(matrix)
    scale = max(float(np.median(np.maximum(np.diag(matrix), 0.0))), 1.0e-12)
    floor = max(scale * float(relative_floor), 1.0e-12)
    projected = (vectors * np.maximum(values, floor)) @ vectors.T
    return (projected + projected.T) / 2.0


def _exponential_weights(length: int, half_life: float) -> np.ndarray:
    if length <= 0:
        return np.empty(0, dtype=float)
    ages = np.arange(length - 1, -1, -1, dtype=float)
    weights = np.exp(-math.log(2.0) * ages / max(float(half_life), 1.0))
    return weights / max(float(weights.sum()), 1.0e-12)


def _weighted_covariance(data: np.ndarray, weights: np.ndarray) -> np.ndarray:
    mean = np.average(data, axis=0, weights=weights)
    centered = data - mean
    covariance = (centered * weights[:, None]).T @ centered
    effective = 1.0 - float(np.sum(weights * weights))
    if effective > 1.0e-8:
        covariance /= effective
    return covariance


def _newey_west_covariance(
    data: np.ndarray,
    weights: np.ndarray,
    lags: int,
) -> np.ndarray:
    mean = np.average(data, axis=0, weights=weights)
    centered = data - mean
    covariance = _weighted_covariance(data, weights)
    maximum_lag = min(max(int(lags), 0), max(len(data) - 2, 0))
    for lag in range(1, maximum_lag + 1):
        lag_weights = weights[lag:].copy()
        lag_weights /= max(float(lag_weights.sum()), 1.0e-12)
        forward = centered[lag:]
        backward = centered[:-lag]
        autocovariance = (forward * lag_weights[:, None]).T @ backward
        kernel = 1.0 - lag / (maximum_lag + 1.0)
        covariance += kernel * (autocovariance + autocovariance.T)
    return covariance


def robust_covariance(
    history: np.ndarray,
    *,
    annualization: float,
    half_life: float,
    newey_west_lags: int,
    diagonal_shrinkage: float,
    regime_lookback: int,
    regime_half_life: float,
    relative_eigenvalue_floor: float = 1.0e-7,
    return_diagnostics: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict[str, Any]]:
    """Estimate a causal, regime-aware covariance matrix.

    Missing values are replaced by each asset's historical median inside the
    supplied window.  The volatility-regime multiplier is a single portfolio-
    level variance ratio, so correlations are not distorted by a collection of
    ex-post asset-specific scaling rules.
    """

    data = np.asarray(history, dtype=float)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if data.ndim != 2 or data.shape[0] < 3 or data.shape[1] < 1:
        raise ValueError("insufficient_covariance_history")
    medians = np.nanmedian(data, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    missing = ~np.isfinite(data)
    if missing.any():
        data = data.copy()
        data[missing] = np.take(medians, np.where(missing)[1])

    weights = _exponential_weights(len(data), half_life)
    raw = _newey_west_covariance(data, weights, newey_west_lags)
    raw = (raw + raw.T) / 2.0
    diagonal = np.diag(np.maximum(np.diag(raw), 1.0e-12))
    shrinkage = float(np.clip(diagonal_shrinkage, 0.0, 1.0))
    shrunk = (1.0 - shrinkage) * raw + shrinkage * diagonal

    recent_length = min(max(int(regime_lookback), 3), len(data))
    recent = data[-recent_length:]
    recent_weights = _exponential_weights(len(recent), regime_half_life)
    recent_covariance = _weighted_covariance(recent, recent_weights)
    base_variance = np.maximum(np.diag(shrunk), 1.0e-12)
    recent_variance = np.maximum(np.diag(recent_covariance), 1.0e-12)
    finite_ratios = recent_variance / base_variance
    finite_ratios = finite_ratios[np.isfinite(finite_ratios)]
    regime_multiplier = float(np.median(finite_ratios)) if len(finite_ratios) else 1.0
    # The bounds are numerical safeguards against a nearly constant or broken
    # price history; they are not selected by backtest performance.
    regime_multiplier = float(np.clip(regime_multiplier, 0.25, 4.0))
    regime_adjusted = shrunk * regime_multiplier

    minimum_before = float(np.linalg.eigvalsh((regime_adjusted + regime_adjusted.T) / 2.0).min())
    covariance = nearest_positive_semidefinite(
        regime_adjusted,
        relative_floor=relative_eigenvalue_floor,
    ) * float(annualization)
    eigenvalues = np.linalg.eigvalsh(covariance)
    minimum_after = float(eigenvalues.min())
    condition_number = float(eigenvalues.max() / max(minimum_after, 1.0e-12))
    diagnostics = RobustCovarianceDiagnostics(
        observations=int(data.shape[0]),
        assets=int(data.shape[1]),
        half_life=float(half_life),
        newey_west_lags=int(newey_west_lags),
        diagonal_shrinkage=shrinkage,
        regime_variance_multiplier=regime_multiplier,
        minimum_eigenvalue_before_projection=minimum_before * float(annualization),
        minimum_eigenvalue_after_projection=minimum_after,
        condition_number=condition_number,
    ).to_dict()
    if return_diagnostics:
        return covariance, diagnostics
    return covariance
