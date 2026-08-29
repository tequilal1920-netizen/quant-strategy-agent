"""Causal, truth-gated four-asset allocation stack v5.4.0.

The statistical covariance always uses every causally available asset return.
Only a recent contiguous suffix of transformed macro observations whose input
levels are individually PIT-admitted may form the macro covariance leg.  The
relative model is anchored directly to 60/15/10/15 and the absolute model has
no benchmark input.  Production cycle views fail closed until their complete
historical D3/PIT pipeline is separately certified.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v536_stack as previous
import asset_allocation_v53_stack as primitives
from allocation_math_v5 import (
    CovarianceBundleV5,
    black_litterman_posterior_v5,
    estimate_statistical_covariance_v5,
    fit_macro_factor_covariance_v5,
    nearest_positive_semidefinite_v5,
    portfolio_risk_contribution_v5,
    reverse_equilibrium_returns_v5,
    solve_erc_v5,
)
from convex_optimizer_v539 import optimize_absolute_v539, optimize_relative_v539


ASSET_ORDER_V540 = ("equity", "bond", "gold", "commodity")
POLICY_WEIGHTS_V540 = np.asarray([0.60, 0.15, 0.10, 0.15])


def macro_innovations_truth_gated_v540(
    macro_levels: np.ndarray, level_admission: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Difference levels and require both vintages for each transformed cell."""

    levels = np.asarray(macro_levels, dtype=float)
    raw_admission = np.asarray(level_admission)
    if levels.ndim != 2 or levels.shape[1] != 4 or not np.all(np.isfinite(levels)):
        raise ValueError("v540_macro_levels_invalid")
    if raw_admission.shape != levels.shape or raw_admission.dtype != np.bool_:
        raise ValueError("v540_level_admission_must_be_boolean_and_aligned")
    if len(levels) < 2:
        raise ValueError("v540_macro_levels_insufficient")
    innovations = np.diff(levels, axis=0)
    transformed_admission = raw_admission[1:] & raw_admission[:-1]
    return innovations, transformed_admission


def _recent_complete_suffix(mask: np.ndarray) -> tuple[int, int]:
    start = len(mask)
    while start > 0 and bool(mask[start - 1]):
        start -= 1
    return start, len(mask) - start


def covariance_truth_gated_v540(
    returns: np.ndarray,
    macro_innovations: np.ndarray,
    transformed_macro_admission: np.ndarray,
    parameters: primitives.StackParametersV53,
) -> tuple[CovarianceBundleV5, dict[str, Any]]:
    """Blend full-history statistical and recent contiguous PIT macro legs."""

    asset_returns = np.asarray(returns, dtype=float)
    macro = np.asarray(macro_innovations, dtype=float)
    raw_admission = np.asarray(transformed_macro_admission)
    if (
        asset_returns.ndim != 2
        or asset_returns.shape[1] != 4
        or not np.all(np.isfinite(asset_returns))
    ):
        raise ValueError("v540_returns_invalid")
    if macro.shape != (len(asset_returns), 4) or not np.all(np.isfinite(macro)):
        raise ValueError("v540_macro_innovations_invalid")
    if raw_admission.shape != macro.shape or raw_admission.dtype != np.bool_:
        raise ValueError("v540_transformed_admission_must_be_boolean_and_aligned")
    requested = float(parameters.macro_blend_weight)
    if not 0.0 <= requested <= 1.0:
        raise ValueError("v540_macro_blend_weight_invalid")

    statistical, statistical_diagnostics = estimate_statistical_covariance_v5(
        asset_returns,
        half_life=parameters.statistical_half_life,
        diagonal_shrinkage=parameters.diagonal_shrinkage,
    )
    coverage = np.mean(raw_admission, axis=0) if len(raw_admission) else np.zeros(4)
    complete = np.all(raw_admission, axis=1)
    suffix_start, suffix_count = _recent_complete_suffix(complete)
    required = max(24, macro.shape[1] + 3)
    columns_pass = bool(np.all(coverage >= parameters.macro_pit_required_fraction))
    gate_passed = bool(requested > 0.0 and columns_pass and suffix_count >= required)

    factor_names = ("growth", "inflation", "credit", "liquidity")
    if gate_passed:
        macro_bundle = fit_macro_factor_covariance_v5(
            asset_returns[suffix_start:],
            macro[suffix_start:],
            macro_blend_weight=1.0,
            factor_names=factor_names,
            ridge_penalty=parameters.ridge_penalty,
            statistical_half_life=parameters.statistical_half_life,
            factor_half_life=parameters.factor_half_life,
            diagonal_shrinkage=parameters.diagonal_shrinkage,
            min_observations=required,
        )
        if macro_bundle.diagnostics.get("status") != "ok":
            raise RuntimeError("v540_macro_covariance_fit_failed_closed")
        blended_raw = requested * macro_bundle.covariance + (1.0 - requested) * statistical
        blended, blend_projection = nearest_positive_semidefinite_v5(blended_raw)
        bundle = CovarianceBundleV5(
            covariance=blended,
            factor_loadings=macro_bundle.factor_loadings,
            factor_covariance=macro_bundle.factor_covariance,
            specific_covariance=macro_bundle.specific_covariance,
            statistical_covariance=statistical,
            macro_blend_weight=requested,
            factor_names=factor_names,
            diagnostics={
                "status": "ok_separate_statistical_and_PIT_macro_legs",
                "statistical_all_returns": statistical_diagnostics,
                "macro_recent_contiguous_suffix": macro_bundle.diagnostics,
                "blended_projection": blend_projection,
            },
        )
        effective = requested
    else:
        zeros = np.zeros((4, 4))
        bundle = CovarianceBundleV5(
            covariance=statistical,
            factor_loadings=zeros,
            factor_covariance=zeros,
            specific_covariance=statistical.copy(),
            statistical_covariance=statistical,
            macro_blend_weight=0.0,
            factor_names=factor_names,
            diagnostics={
                "status": "statistical_only_macro_PIT_gate_closed",
                "statistical_all_returns": statistical_diagnostics,
            },
        )
        effective = 0.0
    return bundle, {
        "requested_macro_blend_weight": requested,
        "effective_macro_blend_weight": effective,
        "transformed_PIT_coverage_by_factor": coverage.tolist(),
        "recent_contiguous_complete_rows": suffix_count,
        "required_recent_contiguous_complete_rows": required,
        "all_macro_columns_pass": columns_pass,
        "gate_passed": gate_passed,
        "statistical_leg_uses_all_asset_returns": True,
        "macro_leg_uses_only_recent_contiguous_complete_PIT_rows": True,
        "unadmitted_macro_cells_can_affect_covariance": False,
    }


def _production_cycles(cycle_row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(name)
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_production_views"))
        and str(payload.get("view_scope")) == "production"
        and str(payload.get("data_status", "")).startswith("D3_")
    )


def _market_only_views(
    covariance: np.ndarray,
    prior: np.ndarray,
    return_history: np.ndarray,
    current_cycle: Mapping[str, Any],
    parameters: primitives.StackParametersV53,
) -> tuple[Any, dict[str, Any]]:
    production = _production_cycles(current_cycle)
    if production:
        raise RuntimeError("v540_production_cycle_history_pipeline_not_yet_certified")
    market = previous.causal_market_view_v536(
        return_history,
        covariance,
        prior,
        tau=parameters.tau,
        view_scale_monthly=parameters.market_view_scale_monthly,
    )
    return market, {
        "method": "market_only_until_full_history_D3_cycle_pipeline_is_certified",
        "production_cycles": [],
        "shadow_cycles_contribution": 0.0,
        "cycle_data_truth_gate": "closed",
    }


def policy_risk_diagnostic_v540(covariance: np.ndarray) -> dict[str, Any]:
    volatility, euler, relative = portfolio_risk_contribution_v5(
        covariance, POLICY_WEIGHTS_V540
    )
    marginal = covariance @ POLICY_WEIGHTS_V540 / volatility
    return {
        "role": "policy_capital_anchor_Euler_risk_diagnostic_not_risk_budget",
        "capital_weights": POLICY_WEIGHTS_V540.tolist(),
        "portfolio_volatility": float(volatility),
        "marginal_risk_contribution": marginal.tolist(),
        "euler_risk_contribution": np.asarray(euler).tolist(),
        "relative_euler_risk_contribution": np.asarray(relative).tolist(),
        "negative_relative_risk_contribution_present": bool(
            np.any(np.asarray(relative) < 0.0)
        ),
        "projection_or_absolute_value_applied": False,
    }


def strict_erc_prior_anchor_v540(
    covariance: np.ndarray,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
) -> tuple[np.ndarray, dict[str, Any]]:
    raw = np.asarray(covariance, dtype=float)
    if raw.shape != (4, 4) or not np.all(np.isfinite(raw)):
        raise ValueError("v540_erc_covariance_invalid")
    symmetric = 0.5 * (raw + raw.T)
    eigenvalues = np.linalg.eigvalsh(symmetric)
    scale = max(float(np.max(np.abs(eigenvalues))), 1.0e-15)
    repaired, projection = nearest_positive_semidefinite_v5(symmetric)
    condition = float(np.linalg.cond(repaired))
    covariance_gate = bool(
        float(eigenvalues.min()) >= -1.0e-10 * scale
        and projection["relative_repair_norm"] <= 1.0e-6
        and condition <= 1.0e8
        and scale > 1.0e-12
    )
    if not covariance_gate:
        raise RuntimeError("v540_erc_covariance_truth_gate_failed")
    result = solve_erc_v5(repaired)
    weights = np.asarray(result.weights, dtype=float)
    maximum_budget_error = float(np.max(np.abs(result.budget_error)))
    numerical_gate = bool(
        result.status == "optimal"
        and np.all(np.isfinite(weights))
        and float(weights.min()) > 0.0
        and abs(float(weights.sum()) - 1.0) <= 1.0e-10
        and maximum_budget_error <= 1.0e-8
        and float(result.kkt_residual) <= 1.0e-9
    )
    if not numerical_gate:
        raise RuntimeError("v540_strict_erc_prior_anchor_failed")
    lower = np.asarray(lower_bounds, dtype=float)
    upper = np.asarray(upper_bounds, dtype=float)
    feasible_under_bounds = bool(np.all(weights >= lower) and np.all(weights <= upper))
    return weights, {
        "role": "strict_ERC_prior_soft_anchor_not_final_portfolio_ERC",
        "result": result.to_dict(),
        "gate": {
            "passed": True,
            "maximum_budget_error": maximum_budget_error,
            "maximum_budget_error_limit": 1.0e-8,
            "kkt_residual": float(result.kkt_residual),
            "kkt_residual_limit": 1.0e-9,
            "covariance_projection": projection,
            "covariance_condition_number": condition,
            "covariance_condition_limit": 1.0e8,
            "anchor_feasible_under_optimizer_bounds": feasible_under_bounds,
            "final_optimizer_portfolio_is_claimed_ERC": False,
        },
    }


def allocate_relative_v540(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admission_matrix: np.ndarray,
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
    covariance, macro_gate = covariance_truth_gated_v540(
        return_history, macro_history, macro_admission_matrix, parameters
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, POLICY_WEIGHTS_V540, parameters.risk_aversion
    )
    views, view_policy = _market_only_views(
        covariance.covariance,
        prior,
        np.asarray(return_history),
        current_cycle,
        parameters,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        POLICY_WEIGHTS_V540,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_relative_v539(
        posterior.posterior_mean - prior,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        POLICY_WEIGHTS_V540,
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
        raise RuntimeError("v540_relative_optimizer_not_optimal")
    return {
        "mode": "benchmark_relative",
        "weights": optimizer["weights"],
        "policy_benchmark": POLICY_WEIGHTS_V540.tolist(),
        "macro_truth_gate": macro_gate,
        "policy_risk_diagnostic": policy_risk_diagnostic_v540(
            covariance.covariance
        ),
        "black_litterman": posterior.to_dict(),
        "view_consensus": view_policy,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
    }


def allocate_absolute_v540(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admission_matrix: np.ndarray,
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
    covariance, macro_gate = covariance_truth_gated_v540(
        return_history, macro_history, macro_admission_matrix, parameters
    )
    anchor, anchor_evidence = strict_erc_prior_anchor_v540(
        covariance.covariance, lower_bounds, upper_bounds
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, anchor, parameters.risk_aversion
    )
    views, view_policy = _market_only_views(
        covariance.covariance,
        prior,
        np.asarray(return_history),
        current_cycle,
        parameters,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        anchor,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_absolute_v539(
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
        raise RuntimeError("v540_absolute_optimizer_not_optimal")
    return {
        "mode": "absolute_no_benchmark",
        "weights": optimizer["weights"],
        "policy_benchmark_used_in_model": False,
        "macro_truth_gate": macro_gate,
        "risk_budget": anchor_evidence,
        "black_litterman": posterior.to_dict(),
        "view_consensus": view_policy,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
    }


__all__ = [
    "ASSET_ORDER_V540",
    "POLICY_WEIGHTS_V540",
    "allocate_absolute_v540",
    "allocate_relative_v540",
    "covariance_truth_gated_v540",
    "macro_innovations_truth_gated_v540",
    "policy_risk_diagnostic_v540",
    "strict_erc_prior_anchor_v540",
]
