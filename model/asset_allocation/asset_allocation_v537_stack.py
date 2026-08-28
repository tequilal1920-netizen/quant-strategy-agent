"""Truth-gated v5.3.7 allocation stack.

This version closes three v5.3.6 audit gaps:

* macro observations enter the factor covariance only on rows where every
  required factor is individually PIT-admitted;
* the benchmark-relative model uses the 60/15/10/15 policy only as the true
  capital and BL equilibrium anchor, not as a fictitious risk-budget target;
* the absolute model accepts an ERC anchor only when both numerical and budget
  errors pass explicit gates; otherwise it fails closed.

No five-cycle view is admitted unless the upstream row explicitly carries a
production D3 flag.  The current warehouse therefore produces market-only BL
views and a zero macro blend, which is a feature rather than a silent fallback.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v536_stack as previous
import asset_allocation_v53_stack as primitives
from allocation_math_v5 import (
    black_litterman_posterior_v5,
    fit_macro_factor_covariance_v5,
    portfolio_risk_contribution_v5,
    reverse_equilibrium_returns_v5,
    solve_erc_v5,
)
from convex_optimizer_v537 import optimize_absolute_v537, optimize_relative_v537


ASSET_ORDER_V537 = previous.ASSET_ORDER_V536
POLICY_WEIGHTS_V537 = previous.POLICY_WEIGHTS_V536.copy()


def covariance_truth_gated_v537(
    returns: np.ndarray,
    macro: np.ndarray,
    macro_admission: np.ndarray,
    parameters: primitives.StackParametersV53,
) -> tuple[Any, dict[str, Any]]:
    returns = np.asarray(returns, dtype=float)
    macro = np.asarray(macro, dtype=float)
    admission = np.asarray(macro_admission, dtype=bool)
    if returns.ndim != 2 or returns.shape[1] != 4 or not np.all(np.isfinite(returns)):
        raise ValueError("v537_returns_invalid")
    if macro.shape != (len(returns), 4) or admission.shape != macro.shape:
        raise ValueError("v537_macro_admission_requires_aligned_month_by_factor_matrix")
    if not np.all(np.isfinite(macro)):
        raise ValueError("v537_macro_values_nonfinite")
    coverage = np.mean(admission, axis=0) if len(admission) else np.zeros(4)
    complete_rows = np.all(admission, axis=1)
    complete_row_count = int(complete_rows.sum())
    all_columns_pass = bool(np.all(coverage >= parameters.macro_pit_required_fraction))
    required = max(24, macro.shape[1] + 3)
    gate_passed = all_columns_pass and complete_row_count >= required
    effective = float(parameters.macro_blend_weight) if gate_passed else 0.0
    if gate_passed:
        fit_returns = returns[complete_rows]
        fit_macro = macro[complete_rows]
    else:
        # The statistical covariance still uses every causally available asset
        # return; macro values are replaced by a constant neutral matrix and
        # rho is exactly zero, so an unadmitted cell cannot affect the result.
        fit_returns = returns
        fit_macro = np.zeros((len(returns), 4), dtype=float)
    covariance = fit_macro_factor_covariance_v5(
        fit_returns,
        fit_macro,
        macro_blend_weight=effective,
        factor_names=("growth", "inflation", "credit", "liquidity"),
        ridge_penalty=parameters.ridge_penalty,
        statistical_half_life=parameters.statistical_half_life,
        factor_half_life=parameters.factor_half_life,
        diagonal_shrinkage=parameters.diagonal_shrinkage,
        min_observations=min(required, len(fit_returns)),
    )
    return covariance, {
        "requested_macro_blend_weight": float(parameters.macro_blend_weight),
        "effective_macro_blend_weight": effective,
        "pit_coverage_by_factor": coverage.tolist(),
        "complete_PIT_rows": complete_row_count,
        "required_complete_PIT_rows": required,
        "all_macro_columns_pass": all_columns_pass,
        "gate_passed": gate_passed,
        "unadmitted_cells_can_affect_covariance": False,
    }


def _production_cycles(cycle_row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(name)
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_production_views"))
        and str(payload.get("view_scope")) == "production"
        and str(payload.get("data_status", "")).startswith("D3_")
    )


def policy_risk_diagnostic_v537(covariance: np.ndarray) -> dict[str, Any]:
    volatility, marginal, relative = portfolio_risk_contribution_v5(
        covariance, POLICY_WEIGHTS_V537
    )
    return {
        "role": "policy_capital_anchor_risk_diagnostic_not_risk_budget_optimization",
        "capital_weights": POLICY_WEIGHTS_V537.tolist(),
        "portfolio_volatility": float(volatility),
        "marginal_risk_contribution": np.asarray(marginal).tolist(),
        "actual_euler_relative_risk_contribution": np.asarray(relative).tolist(),
        "negative_relative_risk_contribution_present": bool(np.any(np.asarray(relative) < 0.0)),
        "projection_or_absolute_value_applied": False,
    }


def strict_erc_anchor_v537(covariance: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    result = solve_erc_v5(covariance)
    maximum_budget_error = float(np.max(np.abs(result.budget_error)))
    gate = {
        "status": result.status,
        "maximum_budget_error": maximum_budget_error,
        "maximum_budget_error_limit": 1.0e-5,
        "kkt_residual": float(result.kkt_residual),
        "kkt_residual_limit": 1.0e-7,
        "passed": bool(
            result.status == "optimal"
            and maximum_budget_error <= 1.0e-5
            and float(result.kkt_residual) <= 1.0e-7
        ),
        "method": "strict_long_only_unconstrained_ERC_then_optimizer_bounds",
        "cycle_signal_used_in_anchor": False,
    }
    if not gate["passed"]:
        raise RuntimeError("v537_strict_erc_anchor_failed_truth_gate")
    return np.asarray(result.weights, dtype=float), {"result": result.to_dict(), "gate": gate}


def _views(
    covariance: np.ndarray,
    prior: np.ndarray,
    history: np.ndarray,
    current_cycle: Mapping[str, Any],
    fitted_cycle: Mapping[str, Any],
    parameters: primitives.StackParametersV53,
) -> tuple[Any, dict[str, Any]]:
    production = _production_cycles(current_cycle)
    if production:
        return previous._views_v536(
            covariance, prior, history, current_cycle, fitted_cycle, parameters
        )
    market = previous.causal_market_view_v536(
        history,
        covariance,
        prior,
        tau=parameters.tau,
        view_scale_monthly=parameters.view_scale_monthly,
    )
    return market, {
        "method": "market_only_no_production_cycle_views",
        "production_cycles": [],
        "shadow_cycles_contribution": 0.0,
        "cycle_data_truth_gate": "no_D3_production_cycle_currently_admitted",
    }


def allocate_relative_v537(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admission_matrix: np.ndarray,
    current_cycle: Mapping[str, Any],
    fitted_cycle: Mapping[str, Any],
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
    covariance, macro_gate = covariance_truth_gated_v537(
        return_history, macro_history, macro_admission_matrix, parameters
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, POLICY_WEIGHTS_V537, parameters.risk_aversion
    )
    views, view_policy = _views(
        covariance.covariance,
        prior,
        np.asarray(return_history),
        current_cycle,
        fitted_cycle,
        parameters,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        POLICY_WEIGHTS_V537,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_relative_v537(
        posterior.posterior_mean - prior,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        POLICY_WEIGHTS_V537,
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
    return {
        "mode": "benchmark_relative",
        "weights": optimizer.get("weights"),
        "policy_benchmark": POLICY_WEIGHTS_V537.tolist(),
        "macro_truth_gate": macro_gate,
        "policy_risk_diagnostic": policy_risk_diagnostic_v537(covariance.covariance),
        "black_litterman": posterior.to_dict(),
        "view_consensus": view_policy,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
    }


def allocate_absolute_v537(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admission_matrix: np.ndarray,
    current_cycle: Mapping[str, Any],
    fitted_cycle: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: primitives.StackParametersV53,
    *,
    lower_bounds: Sequence[float] = (0.10, 0.10, 0.05, 0.05),
    upper_bounds: Sequence[float] = (0.60, 0.75, 0.30, 0.40),
    max_one_way_turnover: float = 0.10,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
) -> dict[str, Any]:
    covariance, macro_gate = covariance_truth_gated_v537(
        return_history, macro_history, macro_admission_matrix, parameters
    )
    anchor, anchor_evidence = strict_erc_anchor_v537(covariance.covariance)
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, anchor, parameters.risk_aversion
    )
    views, view_policy = _views(
        covariance.covariance,
        prior,
        np.asarray(return_history),
        current_cycle,
        fitted_cycle,
        parameters,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        anchor,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_absolute_v537(
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
    return {
        "mode": "absolute_no_benchmark",
        "weights": optimizer.get("weights"),
        "policy_benchmark_used_in_model": False,
        "macro_truth_gate": macro_gate,
        "risk_budget": anchor_evidence,
        "black_litterman": posterior.to_dict(),
        "view_consensus": view_policy,
        "optimizer": optimizer,
        "post_solve_scaling_applied": False,
    }


__all__ = [
    "ASSET_ORDER_V537",
    "POLICY_WEIGHTS_V537",
    "allocate_absolute_v537",
    "allocate_relative_v537",
    "covariance_truth_gated_v537",
    "policy_risk_diagnostic_v537",
    "strict_erc_anchor_v537",
]
