"""Native active optimiser with BL uncertainty and risk-budget anchoring."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
from scipy.optimize import minimize

from active_optimizer_v53 import (
    ActiveOptimizerResultV53,
    _audit,
    _covariance,
    _vector,
)


def optimize_policy_relative_v533(
    active_expected_return: Sequence[float] | np.ndarray,
    covariance: Sequence[Sequence[float]] | np.ndarray,
    mean_uncertainty_covariance: Sequence[Sequence[float]] | np.ndarray,
    benchmark_weights: Sequence[float] | np.ndarray,
    risk_budget_weights: Sequence[float] | np.ndarray,
    previous_weights: Sequence[float] | np.ndarray,
    *,
    lower_bounds: Sequence[float] | np.ndarray,
    upper_bounds: Sequence[float] | np.ndarray,
    max_active_share: float,
    max_annual_tracking_error: float,
    max_one_way_turnover: float,
    linear_cost: Sequence[float] | np.ndarray,
    quadratic_cost: Sequence[float] | np.ndarray,
    active_risk_aversion: float,
    uncertainty_penalty: float,
    risk_budget_anchor_penalty: float,
    active_l2_penalty: float,
    cost_multiplier: float = 1.0,
    l1_smoothing: float = 1.0e-8,
    max_iterations: int = 1500,
    solver_tolerance: float = 1.0e-11,
) -> ActiveOptimizerResultV53:
    benchmark = _vector(benchmark_weights, "benchmark")
    size = benchmark.size
    expected = _vector(active_expected_return, "active_expected_return", size)
    risk_budget = _vector(risk_budget_weights, "risk_budget_weights", size)
    previous = _vector(previous_weights, "previous_weights", size)
    lower = _vector(lower_bounds, "lower_bounds", size)
    upper = _vector(upper_bounds, "upper_bounds", size)
    linear = _vector(linear_cost, "linear_cost", size)
    quadratic = _vector(quadratic_cost, "quadratic_cost", size)
    matrix = _covariance(covariance, size)
    uncertainty_matrix = _covariance(mean_uncertainty_covariance, size)
    if any(abs(float(row.sum()) - 1.0) > 1.0e-8 for row in (benchmark, risk_budget, previous)):
        raise ValueError("v533_weight_vectors_must_sum_to_one")
    if np.any(lower < 0.0) or np.any(lower > upper) or lower.sum() > 1.0 or upper.sum() < 1.0:
        raise ValueError("v533_bounds_infeasible")
    if np.any(benchmark < lower) or np.any(benchmark > upper):
        raise ValueError("v533_benchmark_outside_bounds")
    if min(
        active_risk_aversion,
        uncertainty_penalty,
        risk_budget_anchor_penalty,
        active_l2_penalty,
        cost_multiplier,
    ) < 0.0:
        raise ValueError("v533_penalties_must_be_nonnegative")
    if np.any(linear < 0.0) or np.any(quadratic < 0.0) or l1_smoothing <= 0.0:
        raise ValueError("v533_costs_invalid")

    risk_budget_active = risk_budget - benchmark

    def objective_terms(weights: np.ndarray) -> dict[str, float]:
        active = weights - benchmark
        change = weights - previous
        distance = active - risk_budget_active
        uncertainty_variance = max(float(active @ uncertainty_matrix @ active), 0.0)
        smooth_absolute = np.sqrt(change * change + l1_smoothing * l1_smoothing) - l1_smoothing
        return {
            "active_expected_return": float(expected @ active),
            "active_risk_penalty": 0.5 * active_risk_aversion * float(active @ matrix @ active),
            "mean_uncertainty_penalty": uncertainty_penalty
            * (math.sqrt(uncertainty_variance + l1_smoothing * l1_smoothing) - l1_smoothing),
            "risk_budget_anchor_penalty": risk_budget_anchor_penalty
            * float(distance @ matrix @ distance),
            "active_l2_penalty": active_l2_penalty * float(active @ active),
            "transaction_cost": cost_multiplier
            * float(linear @ smooth_absolute + 0.5 * quadratic @ (change * change)),
        }

    def objective(weights: np.ndarray) -> float:
        terms = objective_terms(weights)
        return (
            -terms["active_expected_return"]
            + terms["active_risk_penalty"]
            + terms["mean_uncertainty_penalty"]
            + terms["risk_budget_anchor_penalty"]
            + terms["active_l2_penalty"]
            + terms["transaction_cost"]
        )

    def gradient(weights: np.ndarray) -> np.ndarray:
        active = weights - benchmark
        distance = active - risk_budget_active
        change = weights - previous
        uncertainty_scale = math.sqrt(
            max(float(active @ uncertainty_matrix @ active), 0.0)
            + l1_smoothing * l1_smoothing
        )
        return (
            -expected
            + active_risk_aversion * (matrix @ active)
            + uncertainty_penalty * (uncertainty_matrix @ active) / uncertainty_scale
            + 2.0 * risk_budget_anchor_penalty * (matrix @ distance)
            + 2.0 * active_l2_penalty * active
            + cost_multiplier
            * (
                linear * change / np.sqrt(change * change + l1_smoothing * l1_smoothing)
                + quadratic * change
            )
        )

    tracking_variance_cap = max_annual_tracking_error * max_annual_tracking_error
    constraints = [
        {"type": "eq", "fun": lambda w: float(w.sum() - 1.0), "jac": lambda w: np.ones(size)},
        {"type": "ineq", "fun": lambda w: max_active_share - 0.5 * float(np.abs(w - benchmark).sum())},
        {
            "type": "ineq",
            "fun": lambda w: tracking_variance_cap
            - 12.0 * float((w - benchmark) @ matrix @ (w - benchmark)),
            "jac": lambda w: -24.0 * (matrix @ (w - benchmark)),
        },
        {"type": "ineq", "fun": lambda w: max_one_way_turnover - 0.5 * float(np.abs(w - previous).sum())},
    ]
    seeds: list[np.ndarray] = []
    for raw in (benchmark, previous, 0.5 * (benchmark + previous), risk_budget):
        active = np.minimum(np.maximum(raw, lower), upper) - benchmark
        share = 0.5 * float(np.abs(active).sum())
        if share > max_active_share:
            active *= max_active_share / share
        seed = benchmark + active
        seed += (1.0 - seed.sum()) / size
        if not any(np.allclose(seed, old) for old in seeds):
            seeds.append(seed)

    attempts: list[dict[str, Any]] = []
    best: tuple[float, np.ndarray, Any, dict[str, Any]] | None = None
    for seed in seeds:
        result = minimize(
            objective,
            seed,
            jac=gradient,
            method="SLSQP",
            bounds=list(zip(lower, upper)),
            constraints=constraints,
            options={"maxiter": max_iterations, "ftol": solver_tolerance, "disp": False},
        )
        weights = np.asarray(result.x, dtype=float)
        audit = _audit(
            weights,
            benchmark,
            previous,
            matrix,
            lower,
            upper,
            max_active_share,
            max_annual_tracking_error,
            max_one_way_turnover,
        )
        value = float(objective(weights))
        attempts.append(
            {
                "success": bool(result.success),
                "message": str(result.message),
                "iterations": int(getattr(result, "nit", 0) or 0),
                "objective": value,
                "max_violation": float(audit["max_violation"]),
            }
        )
        if bool(result.success) and float(audit["max_violation"]) <= 1.0e-7:
            if best is None or value < best[0]:
                best = (value, weights, result, audit)

    status = "optimal"
    fallback_level = 0
    if best is None:
        for level, candidate in enumerate((benchmark, previous), 1):
            audit = _audit(
                candidate,
                benchmark,
                previous,
                matrix,
                lower,
                upper,
                max_active_share,
                max_annual_tracking_error,
                max_one_way_turnover,
            )
            if float(audit["max_violation"]) <= 1.0e-7:
                best = (float(objective(candidate)), candidate.copy(), None, audit)
                status = "fallback_policy" if level == 1 else "fallback_previous"
                fallback_level = level
                break
    if best is None:
        return ActiveOptimizerResultV53(
            weights=np.full(size, np.nan),
            active_weights=np.full(size, np.nan),
            status="infeasible",
            objective_terms={},
            constraints={"max_violation": math.inf},
            turnover=math.nan,
            expected_cost=math.nan,
            diagnostics={"attempts": attempts, "hard_constraints_relaxed": False},
        )

    objective_value, weights, result, audit = best
    terms = objective_terms(weights)
    terms["minimization_objective"] = objective_value
    change = weights - previous
    exact_cost = float(linear @ np.abs(change) + 0.5 * quadratic @ (change * change))
    return ActiveOptimizerResultV53(
        weights=weights,
        active_weights=weights - benchmark,
        status=status,
        objective_terms=terms,
        constraints=audit,
        turnover=0.5 * float(np.abs(change).sum()),
        expected_cost=exact_cost,
        diagnostics={
            "solver": "SCIPY_SLSQP",
            "attempts": attempts,
            "fallback_level": fallback_level,
            "hard_constraints_relaxed": False,
            "selection_uses_test": False,
            "risk_budget_anchor_weights": risk_budget.tolist(),
        },
    )


__all__ = ["optimize_policy_relative_v533"]
