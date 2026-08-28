"""Explicit, causal and truth-gated asset allocation stack v5.3.6.

This module is deliberately independent of the historical v5.3 research
modules.  It contains no monkey-patch hook and consumes only information
available at the supplied signal month.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v53_stack as primitives
from allocation_math_v5 import (
    RiskBudgetResultV5,
    black_litterman_posterior_v5,
    fit_macro_factor_covariance_v5,
    portfolio_risk_contribution_v5,
    reverse_equilibrium_returns_v5,
    solve_constrained_risk_budget_v5,
)
from convex_optimizer_v536 import optimize_absolute_v536, optimize_relative_v536
from cycle_views_v5 import P_VIEWS_V5, ViewBundleV5
from cycle_views_v536 import forecast_cycle_views_v536


ASSET_ORDER_V536 = ("equity", "bond", "gold", "commodity")
POLICY_WEIGHTS_V536 = np.asarray([0.60, 0.15, 0.10, 0.15], dtype=float)


def average_rank_score_v536(values: Sequence[float]) -> np.ndarray:
    """Cross-sectional [-1,1] score with average ties and permutation symmetry."""

    vector = np.asarray(values, dtype=float)
    if vector.ndim != 1 or not np.all(np.isfinite(vector)):
        raise ValueError("v536_rank_values_invalid")
    if len(vector) <= 1 or np.allclose(vector, vector[0], rtol=0.0, atol=1.0e-14):
        return np.zeros(len(vector), dtype=float)
    order = np.argsort(vector, kind="mergesort")
    ranks = np.empty(len(vector), dtype=float)
    cursor = 0
    while cursor < len(vector):
        end = cursor + 1
        while end < len(vector) and abs(vector[order[end]] - vector[order[cursor]]) <= 1.0e-14:
            end += 1
        average = 0.5 * (cursor + end - 1)
        ranks[order[cursor:end]] = average
        cursor = end
    scaled = -1.0 + 2.0 * ranks / (len(vector) - 1.0)
    return scaled - float(np.mean(scaled))


def causal_market_view_v536(
    return_history: np.ndarray,
    covariance: np.ndarray,
    prior: np.ndarray,
    *,
    tau: float,
    view_scale_monthly: float,
) -> ViewBundleV5:
    history = np.asarray(return_history, dtype=float)
    if history.ndim != 2 or history.shape[1] != 4 or len(history) < 12:
        raise ValueError("v536_market_view_requires_12x4_history")
    volatility = np.maximum(np.std(history[-12:], axis=0, ddof=1) * np.sqrt(12.0), 0.02)
    aggregate = np.zeros(4)
    by_horizon: dict[str, list[float]] = {}
    for horizon, weight in ((3, 0.20), (6, 0.35), (12, 0.45)):
        compound = np.prod(1.0 + history[-horizon:], axis=0) - 1.0
        adjusted = compound / (volatility * np.sqrt(horizon / 12.0))
        aggregate += weight * adjusted
        by_horizon[str(horizon)] = adjusted.tolist()
    rank_score = average_rank_score_v536(aggregate)
    asset_alpha = float(view_scale_monthly) * rank_score
    q = P_VIEWS_V5 @ np.asarray(prior, dtype=float) + P_VIEWS_V5 @ asset_alpha
    raw_omega = P_VIEWS_V5 @ (float(tau) * np.asarray(covariance)) @ P_VIEWS_V5.T
    diagonal = np.diag(np.maximum(np.diag(raw_omega), 1.0e-8))
    # This is a conservative model-risk floor, not a learned confidence claim.
    omega = raw_omega + diagonal
    return ViewBundleV5(
        P=P_VIEWS_V5.copy(),
        q=q,
        omega=omega,
        cycle_contributions={
            "causal_market_strength": P_VIEWS_V5 @ asset_alpha,
            "kondratieff": np.zeros(3),
        },
        forecast_error_covariance=omega.copy(),
        diagnostics={
            "status": "causal_market_view",
            "source": "3_6_12m_risk_adjusted_strength_average_tie_rank",
            "raw_risk_adjusted_strength": aggregate.tolist(),
            "cross_sectional_rank_score": rank_score.tolist(),
            "annual_volatility": volatility.tolist(),
            "by_horizon": by_horizon,
            "omega_policy": "tau_P_sigma_PT_plus_diagonal_model_risk_floor",
            "selection_uses_test": False,
        },
    )


def _production_cycles(cycle_row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(name)
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_production_views"))
    )


def _covariance_v536(
    returns: np.ndarray,
    macro: np.ndarray,
    macro_admission: np.ndarray,
    parameters: primitives.StackParametersV53,
) -> tuple[Any, dict[str, Any]]:
    admitted = np.asarray(macro_admission, dtype=bool)
    if admitted.ndim != 2 or admitted.shape[1] != 4 or admitted.shape[0] != len(macro):
        raise ValueError("v536_macro_admission_requires_month_by_factor_matrix")
    coverage = np.mean(admitted, axis=0) if len(admitted) else np.zeros(4)
    all_columns_pass = bool(np.all(coverage >= parameters.macro_pit_required_fraction))
    effective = parameters.macro_blend_weight if all_columns_pass else 0.0
    covariance = fit_macro_factor_covariance_v5(
        returns,
        macro,
        macro_blend_weight=effective,
        factor_names=("growth", "inflation", "credit", "liquidity"),
        ridge_penalty=parameters.ridge_penalty,
        statistical_half_life=parameters.statistical_half_life,
        factor_half_life=parameters.factor_half_life,
        diagonal_shrinkage=parameters.diagonal_shrinkage,
        min_observations=min(24, len(returns)),
    )
    return covariance, {
        "requested_macro_blend_weight": parameters.macro_blend_weight,
        "effective_macro_blend_weight": effective,
        "pit_coverage_by_factor": coverage.tolist(),
        "all_macro_columns_pass": all_columns_pass,
    }


def _risk_anchor_v536(
    covariance: np.ndarray,
    cycle_row: Mapping[str, Any],
    *,
    mode: str,
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[RiskBudgetResultV5, dict[str, Any]]:
    production = _production_cycles(cycle_row)
    shadow = [
        name
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_views")) and name not in production
    ]
    if mode == "benchmark_relative":
        _, _, actual = portfolio_risk_contribution_v5(covariance, POLICY_WEIGHTS_V536)
        result = RiskBudgetResultV5(
            weights=POLICY_WEIGHTS_V536.copy(),
            target_budget=np.asarray(actual, dtype=float),
            relative_risk_contribution=np.asarray(actual, dtype=float),
            budget_error=np.zeros(4),
            kkt_residual=0.0,
            active_constraints=(),
            shadow_prices={},
            status="fixed_policy_risk_anchor",
            diagnostics={"negative_risk_contribution_projection_applied": False},
        )
        return result, {
            "source": "fixed_policy_capital_anchor_actual_euler_RC_disclosed",
            "actual_euler_risk_contribution": np.asarray(actual).tolist(),
            "production_cycles_not_double_counted_in_risk_anchor": list(production),
            "shadow_cycles_excluded": shadow,
            "negative_risk_contribution_projection_applied": False,
        }
    if mode == "absolute_no_benchmark":
        target = np.full(4, 0.25)
        result = solve_constrained_risk_budget_v5(covariance, target, lower, upper)
        return result, {
            "source": "state_neutral_strict_ERC_anchor",
            "target_budget": target.tolist(),
            "cycle_signals_not_double_counted_in_risk_anchor": list(production),
            "shadow_cycles_excluded": shadow,
            "policy_benchmark_used_in_model": False,
        }
    raise ValueError("v536_unknown_mode")


def _views_v536(
    covariance: np.ndarray,
    prior: np.ndarray,
    history: np.ndarray,
    current_cycle: Mapping[str, Any],
    fitted_cycle: Mapping[str, Any],
    parameters: primitives.StackParametersV53,
) -> tuple[ViewBundleV5, dict[str, Any]]:
    cycle = forecast_cycle_views_v536(fitted_cycle, current_cycle)
    market = causal_market_view_v536(
        history,
        covariance,
        prior,
        tau=parameters.tau,
        view_scale_monthly=parameters.market_view_scale_monthly,
    )
    if not cycle["emits_view"]:
        return market, {
            "policy": "market_only_because_no_D3_cycle_view",
            "cycle_status": cycle["status"],
            "cycle_weight_effective": 0.0,
            "market_weight_effective": 1.0,
        }
    # Conservative correlated forecast pool.  The between-model disagreement
    # term avoids any false confidence gain from treating related signals as
    # independent.
    total = parameters.cycle_view_weight + parameters.market_view_weight
    left = parameters.cycle_view_weight / total
    right = parameters.market_view_weight / total
    cycle_q = np.asarray(cycle["q"], dtype=float)
    market_q = np.asarray(market.q, dtype=float)
    disagreement = np.outer(cycle_q - market_q, cycle_q - market_q)
    q = left * cycle_q + right * market_q
    omega = (
        left * np.asarray(cycle["omega"], dtype=float)
        + right * np.asarray(market.omega, dtype=float)
        + left * right * disagreement
    )
    bundle = ViewBundleV5(
        P=P_VIEWS_V5.copy(),
        q=q,
        omega=omega,
        cycle_contributions={
            **{f"cycle:{key}": left * np.asarray(value) for key, value in cycle["cycle_contributions"].items()},
            **{f"market:{key}": right * np.asarray(value) for key, value in market.cycle_contributions.items()},
        },
        forecast_error_covariance=omega.copy(),
        diagnostics={
            "status": "conservative_correlated_view_pool",
            "cycle_weight": left,
            "market_weight": right,
            "disagreement_penalty_included": True,
            "selection_uses_test": False,
        },
    )
    return bundle, {
        "policy": "linear_covariance_pool_plus_between_view_disagreement",
        "cycle_status": cycle["status"],
        "cycle_weight_effective": left,
        "market_weight_effective": right,
    }


def allocate_relative_v536(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admission_matrix: np.ndarray,
    cycle_row: Mapping[str, Any],
    fitted_cycle_view: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: primitives.StackParametersV53,
    *,
    risk_budget_anchor_penalty: float,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
    active_bands: Sequence[float] = (0.10, 0.05, 0.03, 0.05),
    max_active_share: float = 0.10,
    max_tracking_error: float = 0.04,
    max_turnover: float = 0.08,
) -> tuple[np.ndarray, dict[str, Any]]:
    bands = np.asarray(active_bands, dtype=float)
    lower, upper = POLICY_WEIGHTS_V536 - bands, POLICY_WEIGHTS_V536 + bands
    covariance, macro_gate = _covariance_v536(
        np.asarray(return_history), np.asarray(macro_history), np.asarray(macro_admission_matrix), parameters
    )
    risk_anchor, risk_policy = _risk_anchor_v536(
        covariance.covariance, cycle_row, mode="benchmark_relative", lower=lower, upper=upper
    )
    prior = reverse_equilibrium_returns_v5(covariance.covariance, POLICY_WEIGHTS_V536, parameters.risk_aversion)
    views, view_policy = _views_v536(
        covariance.covariance, prior, np.asarray(return_history), cycle_row, fitted_cycle_view, parameters
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        POLICY_WEIGHTS_V536,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_relative_v536(
        posterior.posterior_mean - posterior.pi,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        POLICY_WEIGHTS_V536,
        risk_anchor.weights,
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
    if optimizer["status"] != "optimal":
        raise RuntimeError(f"v536_relative_optimizer_not_optimal:{optimizer['status']}")
    return np.asarray(optimizer["weights"], dtype=float), {
        "model_version": "benchmark_relative_v536",
        "policy_benchmark": POLICY_WEIGHTS_V536.tolist(),
        "covariance": covariance.to_dict(),
        "macro_truth_gate": macro_gate,
        "risk_budget": risk_anchor.to_dict(),
        "risk_budget_truth_gate": risk_policy,
        "black_litterman": posterior.to_dict(),
        "view_policy": view_policy,
        "view_consensus": {
            "P": views.P.tolist(), "q": views.q.tolist(), "omega": views.omega.tolist(),
            "diagnostics": views.diagnostics,
        },
        "optimizer": optimizer,
        "selection_uses_test": False,
    }


def allocate_absolute_v536(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admission_matrix: np.ndarray,
    cycle_row: Mapping[str, Any],
    fitted_cycle_view: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: primitives.StackParametersV53,
    *,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
    lower_bounds: Sequence[float] = (0.10, 0.15, 0.05, 0.05),
    upper_bounds: Sequence[float] = (0.60, 0.75, 0.35, 0.40),
    max_turnover: float = 0.12,
) -> tuple[np.ndarray, dict[str, Any]]:
    lower, upper = np.asarray(lower_bounds, dtype=float), np.asarray(upper_bounds, dtype=float)
    covariance, macro_gate = _covariance_v536(
        np.asarray(return_history), np.asarray(macro_history), np.asarray(macro_admission_matrix), parameters
    )
    risk_anchor, risk_policy = _risk_anchor_v536(
        covariance.covariance, cycle_row, mode="absolute_no_benchmark", lower=lower, upper=upper
    )
    prior = reverse_equilibrium_returns_v5(covariance.covariance, risk_anchor.weights, parameters.risk_aversion)
    views, view_policy = _views_v536(
        covariance.covariance, prior, np.asarray(return_history), cycle_row, fitted_cycle_view, parameters
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        risk_anchor.weights,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    optimizer = optimize_absolute_v536(
        posterior.posterior_mean,
        covariance.covariance,
        posterior.posterior_mean_covariance,
        risk_anchor.weights,
        previous_weights,
        lower_bounds=lower,
        upper_bounds=upper,
        max_one_way_turnover=max_turnover,
        linear_cost=np.asarray(transaction_cost_bps) / 10000.0,
        quadratic_cost=quadratic_cost,
        risk_aversion=parameters.risk_aversion,
        uncertainty_penalty=parameters.uncertainty_penalty,
        anchor_penalty=parameters.absolute_anchor_penalty,
    )
    if optimizer["status"] != "optimal":
        raise RuntimeError(f"v536_absolute_optimizer_not_optimal:{optimizer['status']}")
    return np.asarray(optimizer["weights"], dtype=float), {
        "model_version": "absolute_no_benchmark_v536",
        "policy_benchmark_used_in_model": False,
        "covariance": covariance.to_dict(),
        "macro_truth_gate": macro_gate,
        "risk_budget": risk_anchor.to_dict(),
        "risk_budget_truth_gate": risk_policy,
        "black_litterman": posterior.to_dict(),
        "view_policy": view_policy,
        "view_consensus": {
            "P": views.P.tolist(), "q": views.q.tolist(), "omega": views.omega.tolist(),
            "diagnostics": views.diagnostics,
        },
        "optimizer": optimizer,
        "selection_uses_test": False,
    }


__all__ = [
    "ASSET_ORDER_V536",
    "POLICY_WEIGHTS_V536",
    "allocate_absolute_v536",
    "allocate_relative_v536",
    "average_rank_score_v536",
    "causal_market_view_v536",
]
