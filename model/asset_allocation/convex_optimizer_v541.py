"""Symmetry-gated wrappers around the audited v5.3.9 convex solvers."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from convex_optimizer_v539 import optimize_absolute_v539, optimize_relative_v539


SYMMETRY_RELATIVE_LIMIT_V541 = 1.0e-10


def _symmetry_gate_v541(
    values: Sequence[Sequence[float]], name: str
) -> dict[str, float]:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.all(np.isfinite(matrix)):
        raise ValueError(f"v541_{name}_invalid")
    symmetric = 0.5 * (matrix + matrix.T)
    scale = max(float(np.linalg.norm(symmetric, ord="fro")), 1.0e-15)
    asymmetry = float(np.linalg.norm(matrix - matrix.T, ord="fro"))
    relative = asymmetry / scale
    if relative > SYMMETRY_RELATIVE_LIMIT_V541:
        raise ValueError(f"v541_{name}_asymmetry_gate_failed")
    return {
        "asymmetry_frobenius_norm": asymmetry,
        "relative_asymmetry": relative,
        "relative_asymmetry_limit": SYMMETRY_RELATIVE_LIMIT_V541,
        "passed": True,
    }


def optimize_relative_v541(
    active_expected_return: Sequence[float],
    covariance: Sequence[Sequence[float]],
    posterior_mean_covariance: Sequence[Sequence[float]],
    benchmark_weights: Sequence[float],
    previous_weights: Sequence[float],
    **kwargs: Any,
) -> dict[str, Any]:
    covariance_gate = _symmetry_gate_v541(covariance, "covariance")
    posterior_gate = _symmetry_gate_v541(
        posterior_mean_covariance, "posterior_mean_covariance"
    )
    result = optimize_relative_v539(
        active_expected_return,
        covariance,
        posterior_mean_covariance,
        benchmark_weights,
        previous_weights,
        **kwargs,
    )
    result["input_symmetry_gate"] = {
        "covariance": covariance_gate,
        "posterior_mean_covariance": posterior_gate,
    }
    return result


def optimize_absolute_v541(
    expected_return: Sequence[float],
    covariance: Sequence[Sequence[float]],
    posterior_mean_covariance: Sequence[Sequence[float]],
    risk_budget_anchor: Sequence[float],
    previous_weights: Sequence[float],
    **kwargs: Any,
) -> dict[str, Any]:
    covariance_gate = _symmetry_gate_v541(covariance, "covariance")
    posterior_gate = _symmetry_gate_v541(
        posterior_mean_covariance, "posterior_mean_covariance"
    )
    result = optimize_absolute_v539(
        expected_return,
        covariance,
        posterior_mean_covariance,
        risk_budget_anchor,
        previous_weights,
        **kwargs,
    )
    result["input_symmetry_gate"] = {
        "covariance": covariance_gate,
        "posterior_mean_covariance": posterior_gate,
    }
    return result


__all__ = [
    "SYMMETRY_RELATIVE_LIMIT_V541",
    "optimize_absolute_v541",
    "optimize_relative_v541",
]
