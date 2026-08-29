"""Complete v5.3 allocation stack primitives for governed research.

This module composes the tested v5 numerical building blocks without changing
the deployed v5.2.2 snapshot.  Both model versions consume one frozen
information set:

* the relative version starts from the 60/15/10/15 internal policy vector,
  converts deterministic causal strength into the same three relative views as
  the cycle model, combines them with Black--Litterman, and solves native active
  utility with hard TE/active-share/turnover constraints;
* the absolute version uses only the constrained cycle risk-budget anchor as
  its BL prior and calls the existing robust cost-aware absolute optimiser.

Unavailable or non-PIT macro factors remain zero-contribution.  The functions
never fetch data, never inspect a holdout and never authorise deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from active_optimizer_v53 import ActiveOptimizerResultV53, optimize_policy_relative_v53
from allocation_math_v5 import (
    BlackLittermanResultV5,
    CovarianceBundleV5,
    OptimizerResultV5,
    RiskBudgetResultV5,
    black_litterman_posterior_v5,
    fit_macro_factor_covariance_v5,
    optimize_allocation_v5,
    reverse_equilibrium_returns_v5,
    solve_constrained_risk_budget_v5,
)
from asset_allocation_v5 import cycle_risk_budget_v5
from cycle_views_v5 import P_VIEWS_V5, ViewBundleV5, forecast_cycle_views_v5


ASSET_ORDER_V53 = ("equity", "bond", "gold", "commodity")
POLICY_WEIGHTS_V53 = np.asarray([0.60, 0.15, 0.10, 0.15])


@dataclass(frozen=True)
class StackParametersV53:
    statistical_half_life: float = 24.0
    factor_half_life: float = 30.0
    diagonal_shrinkage: float = 0.35
    macro_blend_weight: float = 0.0
    macro_pit_required_fraction: float = 0.90
    ridge_penalty: float = 0.20
    risk_aversion: float = 4.0
    tau: float = 0.05
    uncertainty_penalty: float = 0.40
    absolute_anchor_penalty: float = 1.25
    active_risk_aversion: float = 4.0
    active_l2_penalty: float = 0.02
    market_view_scale_monthly: float = 0.0025
    cycle_view_weight: float = 0.50
    market_view_weight: float = 0.50


def _rank_score(values: np.ndarray) -> np.ndarray:
    order = np.argsort(np.asarray(values, dtype=float), kind="mergesort")
    output = np.empty(len(order), dtype=float)
    for rank, index in enumerate(order):
        output[index] = 0.0 if len(order) == 1 else -1.0 + 2.0 * rank / (len(order) - 1.0)
    return output


def causal_market_strength_v53(return_history: np.ndarray) -> dict[str, Any]:
    """B06/B12-style deterministic causal strength, rewritten for four assets."""

    history = np.asarray(return_history, dtype=float)
    if history.ndim != 2 or history.shape[1] != 4 or len(history) < 12:
        raise ValueError("v53_market_strength_requires_12x4_history")
    annual_volatility = np.maximum(
        np.std(history[-12:], axis=0, ddof=1) * np.sqrt(12.0), 0.02
    )
    score = np.zeros(4)
    by_horizon: dict[str, list[float]] = {}
    for horizon, coefficient in ((3, 0.20), (6, 0.35), (12, 0.45)):
        compound = np.prod(1.0 + history[-horizon:], axis=0) - 1.0
        adjusted = compound / (annual_volatility * np.sqrt(horizon / 12.0))
        score += coefficient * adjusted
        by_horizon[str(horizon)] = adjusted.tolist()
    ranked = _rank_score(score)
    ranked -= float(ranked.mean())
    return {
        "raw_risk_adjusted_strength": score,
        "cross_sectional_rank_score": ranked,
        "annual_volatility": annual_volatility,
        "by_horizon": by_horizon,
        "horizon_weights": {"3": 0.20, "6": 0.35, "12": 0.45},
        "selection_uses_test": False,
    }


def market_view_bundle_v53(
    covariance: np.ndarray,
    prior_return: np.ndarray,
    return_history: np.ndarray,
    *,
    tau: float,
    view_scale_monthly: float,
) -> ViewBundleV5:
    """Create three auditable relative views from causal market strength."""

    strength = causal_market_strength_v53(return_history)
    ranked = np.asarray(strength["cross_sectional_rank_score"], dtype=float)
    raw_asset_tilt = view_scale_monthly * ranked
    prior = np.asarray(prior_return, dtype=float)
    q = P_VIEWS_V5 @ prior + P_VIEWS_V5 @ raw_asset_tilt
    view_covariance = P_VIEWS_V5 @ (float(tau) * covariance) @ P_VIEWS_V5.T
    diagonal = np.diag(np.maximum(np.diag(view_covariance), 1.0e-10))
    omega = 0.25 * view_covariance + 0.75 * diagonal
    eigenvalues, eigenvectors = np.linalg.eigh((omega + omega.T) / 2.0)
    omega = eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0e-10)) @ eigenvectors.T
    return ViewBundleV5(
        P=P_VIEWS_V5.copy(),
        q=q,
        omega=omega,
        cycle_contributions={
            "causal_market_strength": P_VIEWS_V5 @ raw_asset_tilt,
            "kondratieff": np.zeros(3),
        },
        forecast_error_covariance=omega.copy(),
        diagnostics={
            "status": "causal_market_view",
            "source": "3_6_12m_risk_adjusted_strength",
            "strength": strength,
            "test_or_validation_used": False,
        },
    )


def combine_view_bundles_v53(
    cycle: ViewBundleV5,
    market: ViewBundleV5,
    *,
    cycle_weight: float,
    market_weight: float,
) -> ViewBundleV5:
    if not np.allclose(cycle.P, market.P, atol=1.0e-12):
        raise ValueError("v53_view_matrices_must_match")
    if min(cycle_weight, market_weight) < 0.0 or cycle_weight + market_weight <= 0.0:
        raise ValueError("v53_view_weights_invalid")
    total = cycle_weight + market_weight
    left, right = cycle_weight / total, market_weight / total
    q = left * np.asarray(cycle.q) + right * np.asarray(market.q)
    omega = (
        left * left * np.asarray(cycle.omega)
        + right * right * np.asarray(market.omega)
    )
    diagonal = np.diag(np.maximum(np.diag(omega), 1.0e-10))
    omega = 0.50 * omega + 0.50 * diagonal
    eigenvalues, eigenvectors = np.linalg.eigh((omega + omega.T) / 2.0)
    omega = eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0e-10)) @ eigenvectors.T
    contributions = {
        f"cycle:{key}": left * np.asarray(value, dtype=float)
        for key, value in cycle.cycle_contributions.items()
    }
    contributions.update(
        {
            f"market:{key}": right * np.asarray(value, dtype=float)
            for key, value in market.cycle_contributions.items()
        }
    )
    return ViewBundleV5(
        P=cycle.P.copy(),
        q=q,
        omega=omega,
        cycle_contributions=contributions,
        forecast_error_covariance=omega.copy(),
        diagnostics={
            "status": "robust_cycle_market_view_consensus",
            "cycle_weight": left,
            "market_weight": right,
            "cycle_diagnostics": cycle.diagnostics,
            "market_diagnostics": market.diagnostics,
            "selection_uses_test": False,
        },
    )


def covariance_and_risk_budget_v53(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    parameters: StackParametersV53,
    *,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
) -> tuple[CovarianceBundleV5, RiskBudgetResultV5, dict[str, Any]]:
    admitted_fraction = float(np.mean(np.asarray(macro_admitted, dtype=bool)))
    effective_macro_weight = (
        parameters.macro_blend_weight
        if admitted_fraction >= parameters.macro_pit_required_fraction
        else 0.0
    )
    covariance = fit_macro_factor_covariance_v5(
        return_history,
        macro_history,
        macro_blend_weight=effective_macro_weight,
        factor_names=("growth", "inflation", "credit", "liquidity"),
        ridge_penalty=parameters.ridge_penalty,
        statistical_half_life=parameters.statistical_half_life,
        factor_half_life=parameters.factor_half_life,
        diagonal_shrinkage=parameters.diagonal_shrinkage,
        min_observations=min(24, len(return_history)),
    )
    target_budget, budget_policy = cycle_risk_budget_v5(cycle_row)
    risk_budget = solve_constrained_risk_budget_v5(
        covariance.covariance,
        target_budget,
        lower_bounds,
        upper_bounds,
    )
    return covariance, risk_budget, {
        "requested_macro_blend_weight": parameters.macro_blend_weight,
        "effective_macro_blend_weight": effective_macro_weight,
        "macro_admitted_fraction": admitted_fraction,
        "risk_budget_policy": budget_policy,
    }


def allocate_relative_v53(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: StackParametersV53,
    *,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
    active_bands: Sequence[float] = (0.10, 0.05, 0.03, 0.05),
    max_active_share: float = 0.10,
    max_tracking_error: float = 0.04,
    max_turnover: float = 0.08,
) -> tuple[np.ndarray, dict[str, Any]]:
    bands = np.asarray(active_bands, dtype=float)
    covariance, risk_budget, shared = covariance_and_risk_budget_v53(
        return_history,
        macro_history,
        macro_admitted,
        cycle_row,
        parameters,
        lower_bounds=POLICY_WEIGHTS_V53 - bands,
        upper_bounds=POLICY_WEIGHTS_V53 + bands,
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, POLICY_WEIGHTS_V53, parameters.risk_aversion
    )
    cycle_views = forecast_cycle_views_v5(fitted_cycle_view_model, prior, cycle_row)
    market_views = market_view_bundle_v53(
        covariance.covariance,
        prior,
        return_history,
        tau=parameters.tau,
        view_scale_monthly=parameters.market_view_scale_monthly,
    )
    views = combine_view_bundles_v53(
        cycle_views,
        market_views,
        cycle_weight=parameters.cycle_view_weight,
        market_weight=parameters.market_view_weight,
    )
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        POLICY_WEIGHTS_V53,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    active_expected_return = posterior.posterior_mean - posterior.pi
    result: ActiveOptimizerResultV53 = optimize_policy_relative_v53(
        active_expected_return,
        covariance.covariance,
        POLICY_WEIGHTS_V53,
        previous_weights,
        lower_bounds=POLICY_WEIGHTS_V53 - bands,
        upper_bounds=POLICY_WEIGHTS_V53 + bands,
        max_active_share=max_active_share,
        max_annual_tracking_error=max_tracking_error,
        max_one_way_turnover=max_turnover,
        linear_cost=np.asarray(transaction_cost_bps) / 10000.0,
        quadratic_cost=quadratic_cost,
        active_risk_aversion=parameters.active_risk_aversion,
        active_l2_penalty=parameters.active_l2_penalty,
    )
    if result.status == "infeasible":
        raise RuntimeError("v53_relative_optimizer_infeasible")
    return result.weights, {
        "model_version": "benchmark_relative",
        "policy_benchmark": POLICY_WEIGHTS_V53.tolist(),
        "covariance": covariance.to_dict(),
        "risk_budget": risk_budget.to_dict(),
        "black_litterman": posterior.to_dict(),
        "view_consensus": {
            "P": views.P.tolist(),
            "q": views.q.tolist(),
            "omega": views.omega.tolist(),
            "diagnostics": views.diagnostics,
        },
        "optimizer": result.to_dict(),
        "shared": shared,
        "selection_uses_test": False,
    }


def allocate_absolute_v53(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: StackParametersV53,
    *,
    lower_bounds: Sequence[float] = (0.10, 0.15, 0.05, 0.05),
    upper_bounds: Sequence[float] = (0.60, 0.75, 0.35, 0.40),
    max_turnover: float = 0.12,
    transaction_cost_bps: Sequence[float] = (5.0, 2.0, 5.0, 6.0),
    quadratic_cost: Sequence[float] = (0.0010, 0.0005, 0.0015, 0.0020),
) -> tuple[np.ndarray, dict[str, Any]]:
    covariance, risk_budget, shared = covariance_and_risk_budget_v53(
        return_history,
        macro_history,
        macro_admitted,
        cycle_row,
        parameters,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
    )
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, risk_budget.weights, parameters.risk_aversion
    )
    cycle_views = forecast_cycle_views_v5(fitted_cycle_view_model, prior, cycle_row)
    market_views = market_view_bundle_v53(
        covariance.covariance,
        prior,
        return_history,
        tau=parameters.tau,
        view_scale_monthly=parameters.market_view_scale_monthly,
    )
    views = combine_view_bundles_v53(
        cycle_views,
        market_views,
        cycle_weight=parameters.cycle_view_weight,
        market_weight=parameters.market_view_weight,
    )
    posterior: BlackLittermanResultV5 = black_litterman_posterior_v5(
        covariance.covariance,
        risk_budget.weights,
        delta=parameters.risk_aversion,
        tau=parameters.tau,
        views=views,
    )
    result: OptimizerResultV5 = optimize_allocation_v5(
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
        raise RuntimeError("v53_absolute_optimizer_infeasible")
    return result.weights, {
        "model_version": "absolute_no_benchmark",
        "policy_benchmark_used_in_model": False,
        "covariance": covariance.to_dict(),
        "risk_budget": risk_budget.to_dict(),
        "black_litterman": posterior.to_dict(),
        "view_consensus": {
            "P": views.P.tolist(),
            "q": views.q.tolist(),
            "omega": views.omega.tolist(),
            "diagnostics": views.diagnostics,
        },
        "optimizer": result.to_dict(),
        "shared": shared,
        "selection_uses_test": False,
    }


__all__ = [
    "ASSET_ORDER_V53",
    "POLICY_WEIGHTS_V53",
    "StackParametersV53",
    "allocate_absolute_v53",
    "allocate_relative_v53",
    "causal_market_strength_v53",
    "combine_view_bundles_v53",
    "covariance_and_risk_budget_v53",
    "market_view_bundle_v53",
]
