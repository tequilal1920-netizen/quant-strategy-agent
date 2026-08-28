"""v5.4.6 relative allocation using the fixed legacy-B06 view mechanism.

This module changes only the relative view generator.  Covariance truth gates,
Black-Litterman mathematics, direct active constraints, transaction costs and
complete KKT certification remain the independently audited v5.4.1 path.
No cycle or macro data are admitted while D3/PIT evidence is absent.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v540_stack as truth
import asset_allocation_v53_stack as primitives
from allocation_math_v5 import black_litterman_posterior_v5, reverse_equilibrium_returns_v5
from asset_allocation_v541_stack import (
    POLICY_WEIGHTS_V541,
    _matrix_symmetry_gate_v541,
    _prepare_macro_v541,
)
from convex_optimizer_v541 import optimize_relative_v541
from legacy_b06_active_view_v546 import legacy_b06_view_bundle_v546


def allocate_relative_legacy_v546(
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
    if truth._production_cycles(current_cycle):
        raise RuntimeError("v546_production_cycle_history_pipeline_not_certified")
    returns, macro, admission, transformed_months = _prepare_macro_v541(
        return_history, macro_levels, macro_level_admission, months
    )
    covariance, macro_gate = truth.covariance_truth_gated_v540(
        returns, macro, admission, parameters
    )
    covariance_symmetry = _matrix_symmetry_gate_v541(covariance.covariance, "covariance")
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, POLICY_WEIGHTS_V541, parameters.risk_aversion
    )
    views = legacy_b06_view_bundle_v546(
        covariance.covariance,
        prior,
        returns,
        tau=parameters.tau,
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
        raise RuntimeError("v546_relative_optimizer_not_optimal")
    return {
        "mode": "benchmark_relative",
        "challenger_family": "legacy_B06_fixed_mechanism_transfer",
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
        "policy_risk_diagnostic": truth.policy_risk_diagnostic_v540(covariance.covariance),
        "black_litterman": posterior.to_dict(),
        "view_consensus": {
            "method": "legacy_B06_fixed_mechanism_transfer_market_only",
            "production_cycles": [],
            "shadow_cycles_contribution": 0.0,
            "diagnostics": views.diagnostics,
        },
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
        "selection_status": "post_first_v545_result_legacy_transfer_research_only",
    }


__all__ = ["allocate_relative_legacy_v546"]
