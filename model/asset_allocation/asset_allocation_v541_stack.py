"""End-to-end PIT and calendar enforced allocation stack v5.4.1."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v540_stack as base
import asset_allocation_v53_stack as primitives
from allocation_math_v5 import (
    black_litterman_posterior_v5,
    reverse_equilibrium_returns_v5,
)
from convex_optimizer_v541 import optimize_absolute_v541, optimize_relative_v541


ASSET_ORDER_V541 = base.ASSET_ORDER_V540
POLICY_WEIGHTS_V541 = base.POLICY_WEIGHTS_V540.copy()


def _month_number(value: str) -> int:
    text = str(value)
    if len(text) != 6 or not text.isdigit():
        raise ValueError("v541_month_must_be_YYYYMM")
    year, month = int(text[:4]), int(text[4:])
    date(year, month, 1)
    return year * 12 + month - 1


def _validate_months(months: Sequence[str], required_length: int) -> tuple[str, ...]:
    result = tuple(str(item) for item in months)
    if len(result) != required_length:
        raise ValueError("v541_months_must_align_with_levels")
    numbers = [_month_number(item) for item in result]
    if len(set(numbers)) != len(numbers):
        raise ValueError("v541_months_must_be_unique")
    if any(right - left != 1 for left, right in zip(numbers, numbers[1:])):
        raise ValueError("v541_months_must_be_strictly_contiguous")
    return result


def _prepare_macro_v541(
    return_history: np.ndarray,
    macro_levels: np.ndarray,
    macro_level_admission: np.ndarray,
    months: Sequence[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...]]:
    returns = np.asarray(return_history, dtype=float)
    levels = np.asarray(macro_levels, dtype=float)
    if returns.shape != levels.shape or returns.ndim != 2 or returns.shape[1] != 4:
        raise ValueError("v541_returns_and_macro_levels_must_align")
    validated_months = _validate_months(months, len(levels))
    innovations, transformed_admission = base.macro_innovations_truth_gated_v540(
        levels, macro_level_admission
    )
    return returns[1:], innovations, transformed_admission, validated_months[1:]


def _matrix_symmetry_gate_v541(values: np.ndarray, name: str) -> dict[str, float]:
    matrix = np.asarray(values, dtype=float)
    symmetric = 0.5 * (matrix + matrix.T)
    scale = max(float(np.linalg.norm(symmetric, ord="fro")), 1.0e-15)
    relative = float(np.linalg.norm(matrix - matrix.T, ord="fro")) / scale
    if relative > 1.0e-10:
        raise ValueError(f"v541_{name}_asymmetry_gate_failed")
    return {"relative_asymmetry": relative, "limit": 1.0e-10, "passed": True}


def allocate_relative_v541(
    return_history: np.ndarray,
    macro_levels: np.ndarray,
    macro_level_admission: np.ndarray,
    months: Sequence[str],
    current_cycle: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: primitives.StackParametersV53,
    *,
    lower_bounds: Sequence[float] = (0.10, 0.05, 0.05, 0.05),
    upper_bounds: Sequence[float] = (0.75, 0.40, 0.30, 0.40),
    max_active_share: float = 0.10,
    max_annual_tracking_error: float = 0.08,
    max_one_way_turnover: float = 0.08,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
) -> dict[str, Any]:
    returns, macro, admission, transformed_months = _prepare_macro_v541(
        return_history, macro_levels, macro_level_admission, months
    )
    covariance, macro_gate = base.covariance_truth_gated_v540(
        returns, macro, admission, parameters
    )
    covariance_symmetry = _matrix_symmetry_gate_v541(
        covariance.covariance, "covariance"
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, POLICY_WEIGHTS_V541, parameters.risk_aversion
    )
    views, view_policy = base._market_only_views(
        covariance.covariance, prior, returns, current_cycle, parameters
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        POLICY_WEIGHTS_V541,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_relative_v541(
        posterior.posterior_mean - prior,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        POLICY_WEIGHTS_V541,
        previous_weights,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        max_active_share=max_active_share,
        max_annual_tracking_error=max_annual_tracking_error,
        max_one_way_turnover=max_one_way_turnover,
        linear_cost=np.asarray(transaction_cost_bps) / 10000.0,
        quadratic_cost=quadratic_cost,
        active_risk_aversion=parameters.active_risk_aversion,
        uncertainty_penalty=parameters.uncertainty_penalty,
        active_l2_penalty=parameters.active_l2_penalty,
    )
    if optimizer.get("status") != "optimal" or optimizer.get("weights") is None:
        raise RuntimeError("v541_relative_optimizer_not_optimal")
    return {
        "mode": "benchmark_relative",
        "weights": optimizer["weights"],
        "policy_benchmark": POLICY_WEIGHTS_V541.tolist(),
        "macro_truth_gate": {
            **macro_gate,
            "input_role": "levels_only_transformed_inside_entrypoint",
            "transformed_month_start": transformed_months[0],
            "transformed_month_end": transformed_months[-1],
            "calendar_contiguity_verified": True,
        },
        "input_covariance_symmetry_gate": covariance_symmetry,
        "policy_risk_diagnostic": base.policy_risk_diagnostic_v540(
            covariance.covariance
        ),
        "black_litterman": posterior.to_dict(),
        "view_consensus": view_policy,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
    }


def allocate_absolute_v541(
    return_history: np.ndarray,
    macro_levels: np.ndarray,
    macro_level_admission: np.ndarray,
    months: Sequence[str],
    current_cycle: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: primitives.StackParametersV53,
    *,
    lower_bounds: Sequence[float] = (0.10, 0.10, 0.05, 0.05),
    upper_bounds: Sequence[float] = (0.60, 0.75, 0.30, 0.40),
    max_one_way_turnover: float = 0.10,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
) -> dict[str, Any]:
    returns, macro, admission, transformed_months = _prepare_macro_v541(
        return_history, macro_levels, macro_level_admission, months
    )
    covariance, macro_gate = base.covariance_truth_gated_v540(
        returns, macro, admission, parameters
    )
    covariance_symmetry = _matrix_symmetry_gate_v541(
        covariance.covariance, "covariance"
    )
    anchor, anchor_evidence = base.strict_erc_prior_anchor_v540(
        covariance.covariance, lower_bounds, upper_bounds
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, anchor, parameters.risk_aversion
    )
    views, view_policy = base._market_only_views(
        covariance.covariance, prior, returns, current_cycle, parameters
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        anchor,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_absolute_v541(
        posterior.posterior_mean,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        anchor,
        previous_weights,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        max_one_way_turnover=max_one_way_turnover,
        linear_cost=np.asarray(transaction_cost_bps) / 10000.0,
        quadratic_cost=quadratic_cost,
        risk_aversion=parameters.risk_aversion,
        uncertainty_penalty=parameters.uncertainty_penalty,
        anchor_penalty=parameters.absolute_anchor_penalty,
    )
    if optimizer.get("status") != "optimal" or optimizer.get("weights") is None:
        raise RuntimeError("v541_absolute_optimizer_not_optimal")
    return {
        "mode": "absolute_no_benchmark",
        "weights": optimizer["weights"],
        "policy_benchmark_used_in_model": False,
        "macro_truth_gate": {
            **macro_gate,
            "input_role": "levels_only_transformed_inside_entrypoint",
            "transformed_month_start": transformed_months[0],
            "transformed_month_end": transformed_months[-1],
            "calendar_contiguity_verified": True,
        },
        "input_covariance_symmetry_gate": covariance_symmetry,
        "risk_budget": anchor_evidence,
        "black_litterman": posterior.to_dict(),
        "view_consensus": view_policy,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
    }


__all__ = [
    "ASSET_ORDER_V541",
    "POLICY_WEIGHTS_V541",
    "allocate_absolute_v541",
    "allocate_relative_v541",
]
