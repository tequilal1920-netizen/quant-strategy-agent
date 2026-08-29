"""Native Direct active optimiser integration for the single B06 transfer challenger."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v540_stack as truth
import asset_allocation_v53_stack as primitives
from asset_allocation_v541_stack import POLICY_WEIGHTS_V541, _matrix_symmetry_gate_v541, _prepare_macro_v541, _validate_months
from convex_optimizer_v541 import optimize_relative_v541
from legacy_b06_direct_v556 import direct_active_alpha_v556, legacy_b06_target_v556


def allocate_relative_legacy_direct_v556(
    return_history: np.ndarray,
    macro_levels: np.ndarray,
    macro_level_admission: np.ndarray,
    months: Sequence[str],
    current_cycle: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: primitives.StackParametersV53,
    *,
    lower_bounds: Sequence[float] = (.10, .05, .05, .05),
    upper_bounds: Sequence[float] = (.75, .40, .30, .40),
    max_active_share: float = .10,
    max_annual_tracking_error: float = .08,
    max_one_way_turnover: float = .08,
    transaction_cost_bps: Sequence[float] = (5., 2., 5., 6.),
    quadratic_cost: Sequence[float] = (.0010, .0005, .0015, .0020),
) -> dict[str, Any]:
    raw_returns = np.asarray(return_history, dtype=float)
    if raw_returns.ndim != 2 or raw_returns.shape != (36, 4) or not np.all(np.isfinite(raw_returns)):
        raise ValueError("v556_direct_stack_requires_exact_36x4_window")
    validated_months = _validate_months(months, 36)
    if truth._production_cycles(current_cycle):
        raise RuntimeError("v556_production_cycle_history_pipeline_not_certified")
    # Macro entrypoint remains closed and separately truth-gated.  It drops one
    # row for level differencing only in the statistical covariance path.  The
    # fixed B06 signal receives the complete validated 36-month price window.
    returns, macro, admission, transformed_months = _prepare_macro_v541(
        raw_returns, macro_levels, macro_level_admission, validated_months
    )
    covariance_bundle, macro_gate = truth.covariance_truth_gated_v540(
        returns, macro, admission, parameters
    )
    covariance = covariance_bundle.covariance
    symmetry = _matrix_symmetry_gate_v541(covariance, "covariance")
    signal_target, signal = legacy_b06_target_v556(
        raw_returns, POLICY_WEIGHTS_V541, lower_bounds, upper_bounds
    )
    if abs(float(parameters.active_risk_aversion) - float(parameters.risk_aversion)) > 1e-12:
        raise ValueError("v556_direct_delta_must_equal_optimizer_risk_aversion")
    alpha = direct_active_alpha_v556(
        covariance, signal_target, POLICY_WEIGHTS_V541, parameters.active_risk_aversion
    )
    optimizer = optimize_relative_v541(
        alpha,
        covariance,
        covariance,  # interface compatibility only; objective coefficient is exactly zero
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
        uncertainty_penalty=0.0,
        active_l2_penalty=parameters.active_l2_penalty,
    )
    if optimizer.get("status") != "optimal" or optimizer.get("weights") is None:
        raise RuntimeError("v556_direct_optimizer_not_optimal")
    residual = np.max(np.abs(alpha - parameters.active_risk_aversion * covariance @ (signal_target - POLICY_WEIGHTS_V541)))
    if residual > 1e-12:
        raise AssertionError("v556_direct_alpha_identity_failed")
    return {
        "mode": "benchmark_relative",
        "challenger_family": "legacy_transfer_challenger_not_blind_champion",
        "signal_path": "direct_active_alpha",
        "weights": optimizer["weights"],
        "optimized_weights": optimizer["weights"],
        "signal_target_weights": signal_target.tolist(),
        "raw_signal_strength": signal["raw_tactical_score"],
        "policy_benchmark": POLICY_WEIGHTS_V541.tolist(),
        "direct_active_alpha": alpha.tolist(),
        "direct_alpha_formula_max_residual": float(residual),
        "legacy_signal_diagnostics": signal,
        "other_inference": {"used": False, "reason": "mutually_exclusive_with_direct_active_alpha"},
        "posterior_mean_covariance_role": "unused_interface_compatibility",
        "posterior_uncertainty_penalty": 0.0,
        "macro_truth_gate": {**macro_gate, "transformed_month_start": transformed_months[0], "transformed_month_end": transformed_months[-1]},
        "production_cycles": [],
        "input_covariance_symmetry_gate": symmetry,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
        "signal_window_start": validated_months[0],
        "signal_window_end": validated_months[-1],
        "future_outcome_used": False,
        "selection_uses_test": False,
    }


__all__ = ["allocate_relative_legacy_direct_v556"]