"""Truth-gated, risk-budget-integrated complete v5.3.3 allocation stack."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v53_stack as base
from active_optimizer_v533 import optimize_policy_relative_v533
from allocation_math_v5 import (
    black_litterman_posterior_v5,
    optimize_allocation_v5,
    portfolio_risk_contribution_v5,
    reverse_equilibrium_returns_v5,
    solve_constrained_risk_budget_v5,
)


def _production_cycles(cycle_row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(name)
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_production_views"))
    )


def _truth_gated_risk_budget(
    covariance: np.ndarray,
    cycle_row: Mapping[str, Any],
    *,
    mode: str,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
) -> tuple[Any, dict[str, Any]]:
    admitted = _production_cycles(cycle_row)
    if admitted:
        target, policy = base.cycle_risk_budget_v5(cycle_row)
        source = "D3_production_cycle_probability_budget"
    elif mode == "benchmark_relative":
        _, _, target = portfolio_risk_contribution_v5(
            covariance, base.POLICY_WEIGHTS_V53
        )
        policy = {
            "components": [{"name": "policy_risk_contribution", "blend_weight": 1.0}],
            "kondratieff_weight": 0.0,
        }
        source = "policy_risk_contribution_no_D3_cycle"
    elif mode == "absolute_no_benchmark":
        target = np.full(4, 0.25)
        policy = {
            "components": [{"name": "equal_risk_budget", "blend_weight": 1.0}],
            "kondratieff_weight": 0.0,
        }
        source = "equal_risk_budget_no_D3_cycle"
    else:
        raise ValueError("v533_unknown_mode")
    result = solve_constrained_risk_budget_v5(
        covariance, target, lower_bounds, upper_bounds
    )
    return result, {
        "source": source,
        "production_admitted_cycles": list(admitted),
        "target_budget": np.asarray(target, dtype=float).tolist(),
        "policy": policy,
        "shadow_cycles_excluded": [
            name
            for name, payload in (cycle_row.get("cycles") or {}).items()
            if bool(payload.get("eligible_for_views")) and name not in admitted
        ],
    }


def _view_consensus(
    covariance: np.ndarray,
    prior: np.ndarray,
    return_history: np.ndarray,
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    parameters: base.StackParametersV53,
) -> tuple[Any, dict[str, Any]]:
    cycle_views = base.forecast_cycle_views_v5(
        fitted_cycle_view_model, prior, cycle_row
    )
    market_views = base.market_view_bundle_v53(
        covariance,
        prior,
        return_history,
        tau=parameters.tau,
        view_scale_monthly=parameters.market_view_scale_monthly,
    )
    admitted = _production_cycles(cycle_row)
    cycle_weight = parameters.cycle_view_weight if admitted else 0.0
    views = base.combine_view_bundles_v53(
        cycle_views,
        market_views,
        cycle_weight=cycle_weight,
        market_weight=parameters.market_view_weight,
    )
    return views, {
        "production_admitted_cycles": list(admitted),
        "cycle_weight_requested": parameters.cycle_view_weight,
        "cycle_weight_effective": cycle_weight,
        "market_weight_effective": parameters.market_view_weight,
        "cycle_gate": (
            "production_cycle_views_active"
            if admitted
            else "no_D3_cycle_views_market_signal_only"
        ),
    }


def allocate_relative_v533(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: base.StackParametersV53,
    *,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
    active_bands: Sequence[float] = (0.10, 0.05, 0.03, 0.05),
    max_active_share: float = 0.10,
    max_tracking_error: float = 0.04,
    max_turnover: float = 0.08,
    risk_budget_anchor_penalty: float = 0.75,
) -> tuple[np.ndarray, dict[str, Any]]:
    bands = np.asarray(active_bands, dtype=float)
    lower = base.POLICY_WEIGHTS_V53 - bands
    upper = base.POLICY_WEIGHTS_V53 + bands
    covariance, _, shared = base.covariance_and_risk_budget_v53(
        return_history,
        macro_history,
        macro_admitted,
        cycle_row,
        parameters,
        lower_bounds=lower,
        upper_bounds=upper,
    )
    risk_budget, risk_policy = _truth_gated_risk_budget(
        covariance.covariance,
        cycle_row,
        mode="benchmark_relative",
        lower_bounds=lower,
        upper_bounds=upper,
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance,
        base.POLICY_WEIGHTS_V53,
        parameters.risk_aversion,
    )
    views, view_gate = _view_consensus(
        covariance.covariance,
        prior,
        return_history,
        cycle_row,
        fitted_cycle_view_model,
        parameters,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        base.POLICY_WEIGHTS_V53,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    result = optimize_policy_relative_v533(
        posterior.posterior_mean - posterior.pi,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        base.POLICY_WEIGHTS_V53,
        risk_budget.weights,
        previous_weights,
        lower_bounds=lower,
        upper_bounds=upper,
        max_active_share=max_active_share,
        max_annual_tracking_error=max_tracking_error,
        max_one_way_turnover=max_turnover,
        linear_cost=np.asarray(transaction_cost_bps) / 10000.0,
        quadratic_cost=quadratic_cost,
        active_risk_aversion=parameters.active_risk_aversion,
        uncertainty_penalty=parameters.uncertainty_penalty,
        risk_budget_anchor_penalty=risk_budget_anchor_penalty,
        active_l2_penalty=parameters.active_l2_penalty,
    )
    if result.status == "infeasible":
        raise RuntimeError("v533_relative_optimizer_infeasible")
    return result.weights, {
        "model_version": "benchmark_relative",
        "policy_benchmark": base.POLICY_WEIGHTS_V53.tolist(),
        "covariance": covariance.to_dict(),
        "risk_budget": risk_budget.to_dict(),
        "risk_budget_truth_gate": risk_policy,
        "black_litterman": posterior.to_dict(),
        "view_consensus": {
            "P": views.P.tolist(),
            "q": views.q.tolist(),
            "omega": views.omega.tolist(),
            "diagnostics": views.diagnostics,
            "truth_gate": view_gate,
        },
        "optimizer": result.to_dict(),
        "shared": shared,
        "selection_uses_test": False,
    }


def allocate_absolute_v533(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: base.StackParametersV53,
    *,
    lower_bounds: Sequence[float] = (0.10, 0.15, 0.05, 0.05),
    upper_bounds: Sequence[float] = (0.60, 0.75, 0.35, 0.40),
    max_turnover: float = 0.12,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
) -> tuple[np.ndarray, dict[str, Any]]:
    covariance, _, shared = base.covariance_and_risk_budget_v53(
        return_history,
        macro_history,
        macro_admitted,
        cycle_row,
        parameters,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
    )
    risk_budget, risk_policy = _truth_gated_risk_budget(
        covariance.covariance,
        cycle_row,
        mode="absolute_no_benchmark",
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, risk_budget.weights, parameters.risk_aversion
    )
    views, view_gate = _view_consensus(
        covariance.covariance,
        prior,
        return_history,
        cycle_row,
        fitted_cycle_view_model,
        parameters,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        risk_budget.weights,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    result = optimize_allocation_v5(
        posterior,
        risk_budget,
        covariance,
        previous_weights,
        {
            "lower_bounds": lower_bounds,
            "upper_bounds": upper_bounds,
            "max_turnover": max_turnover,
            "annualization": 12.0,
        },
        {
            "linear": np.asarray(transaction_cost_bps) / 10000.0,
            "quadratic": quadratic_cost,
        },
        {
            "risk_aversion": parameters.risk_aversion,
            "uncertainty_penalty": parameters.uncertainty_penalty,
            "anchor_penalty": parameters.absolute_anchor_penalty,
            "max_iterations": 1500,
            "solver_tolerance": 1.0e-11,
        },
    )
    if result.status == "infeasible":
        raise RuntimeError("v533_absolute_optimizer_infeasible")
    return result.weights, {
        "model_version": "absolute_no_benchmark",
        "policy_benchmark_used_in_model": False,
        "covariance": covariance.to_dict(),
        "risk_budget": risk_budget.to_dict(),
        "risk_budget_truth_gate": risk_policy,
        "black_litterman": posterior.to_dict(),
        "view_consensus": {
            "P": views.P.tolist(),
            "q": views.q.tolist(),
            "omega": views.omega.tolist(),
            "diagnostics": views.diagnostics,
            "truth_gate": view_gate,
        },
        "optimizer": result.to_dict(),
        "shared": shared,
        "selection_uses_test": False,
    }


__all__ = ["allocate_absolute_v533", "allocate_relative_v533"]
