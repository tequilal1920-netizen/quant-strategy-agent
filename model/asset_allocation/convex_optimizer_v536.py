"""Fail-closed convex optimisers for asset allocation v5.3.6."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import cvxpy as cp
import numpy as np


def _vector(value: Sequence[float], name: str, size: int | None = None) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.ndim != 1 or (size is not None and len(result) != size):
        raise ValueError(f"v536_{name}_shape_invalid")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"v536_{name}_nonfinite")
    return result


def _matrix(value: Sequence[Sequence[float]], name: str, size: int) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (size, size) or not np.all(np.isfinite(result)):
        raise ValueError(f"v536_{name}_invalid")
    result = (result + result.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(result)
    return eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0e-10)) @ eigenvectors.T


def _finite_scalars(payload: Mapping[str, float]) -> None:
    for name, value in payload.items():
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError(f"v536_{name}_invalid")


def _constraint_audit(
    weights: np.ndarray,
    benchmark: np.ndarray | None,
    previous: np.ndarray,
    covariance: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    max_active_share: float | None,
    max_tracking_error: float | None,
    max_turnover: float,
) -> dict[str, Any]:
    change = weights - previous
    violations: dict[str, float] = {
        "sum": abs(float(weights.sum()) - 1.0),
        "lower": float(np.max(np.maximum(lower - weights, 0.0))),
        "upper": float(np.max(np.maximum(weights - upper, 0.0))),
        "turnover": max(0.0, 0.5 * float(np.abs(change).sum()) - max_turnover),
    }
    report: dict[str, Any] = {
        "one_way_turnover": 0.5 * float(np.abs(change).sum()),
        "turnover_slack": max_turnover - 0.5 * float(np.abs(change).sum()),
        "lower_slack": (weights - lower).tolist(),
        "upper_slack": (upper - weights).tolist(),
    }
    if benchmark is not None:
        active = weights - benchmark
        active_share = 0.5 * float(np.abs(active).sum())
        tracking_error = math.sqrt(max(12.0 * float(active @ covariance @ active), 0.0))
        report.update(
            {
                "active_share": active_share,
                "annual_tracking_error": tracking_error,
                "active_share_slack": float(max_active_share) - active_share,
                "tracking_error_slack": float(max_tracking_error) - tracking_error,
            }
        )
        violations["active_share"] = max(0.0, active_share - float(max_active_share))
        violations["annual_tracking_error"] = max(
            0.0, tracking_error - float(max_tracking_error)
        )
    report["violations"] = violations
    report["max_violation"] = (
        math.inf
        if not all(math.isfinite(value) for value in violations.values())
        else max(violations.values(), default=0.0)
    )
    return report


def _solver_diagnostics(
    problem: cp.Problem,
    named_constraints: Sequence[tuple[str, cp.Constraint]],
    slacks: Mapping[str, float | Sequence[float]],
) -> dict[str, Any]:
    duals: dict[str, Any] = {}
    maximum_complementarity = 0.0
    for name, constraint in named_constraints:
        raw = constraint.dual_value
        if raw is None:
            duals[name] = None
            continue
        value = np.asarray(raw, dtype=float)
        duals[name] = value.tolist() if value.ndim else float(value)
        if name in slacks:
            slack = np.asarray(slacks[name], dtype=float)
            try:
                maximum_complementarity = max(
                    maximum_complementarity,
                    float(np.max(np.abs(value * slack))),
                )
            except ValueError:
                pass
    stats = problem.solver_stats
    return {
        "solver": "CLARABEL",
        "status": str(problem.status),
        "iterations": int(getattr(stats, "num_iters", 0) or 0),
        "solve_time_seconds": float(getattr(stats, "solve_time", 0.0) or 0.0),
        "dual_values": duals,
        "maximum_complementarity_residual": maximum_complementarity,
        "hard_constraints_relaxed": False,
        "fallback_used": False,
        "selection_uses_test": False,
    }


def optimize_relative_v536(
    active_expected_return: Sequence[float],
    covariance: Sequence[Sequence[float]],
    posterior_mean_covariance: Sequence[Sequence[float]],
    benchmark_weights: Sequence[float],
    risk_budget_anchor: Sequence[float],
    previous_weights: Sequence[float],
    *,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
    max_active_share: float,
    max_annual_tracking_error: float,
    max_one_way_turnover: float,
    linear_cost: Sequence[float],
    quadratic_cost: Sequence[float],
    active_risk_aversion: float,
    uncertainty_penalty: float,
    risk_budget_anchor_penalty: float,
    active_l2_penalty: float,
) -> dict[str, Any]:
    benchmark = _vector(benchmark_weights, "benchmark")
    size = len(benchmark)
    expected = _vector(active_expected_return, "active_expected_return", size)
    risk_anchor = _vector(risk_budget_anchor, "risk_budget_anchor", size)
    previous = _vector(previous_weights, "previous_weights", size)
    lower = _vector(lower_bounds, "lower_bounds", size)
    upper = _vector(upper_bounds, "upper_bounds", size)
    linear = _vector(linear_cost, "linear_cost", size)
    quadratic = _vector(quadratic_cost, "quadratic_cost", size)
    sigma = _matrix(covariance, "covariance", size)
    mean_covariance = _matrix(posterior_mean_covariance, "posterior_mean_covariance", size)
    _finite_scalars(
        {
            "max_active_share": max_active_share,
            "max_annual_tracking_error": max_annual_tracking_error,
            "max_one_way_turnover": max_one_way_turnover,
            "active_risk_aversion": active_risk_aversion,
            "uncertainty_penalty": uncertainty_penalty,
            "risk_budget_anchor_penalty": risk_budget_anchor_penalty,
            "active_l2_penalty": active_l2_penalty,
        }
    )
    if any(abs(float(row.sum()) - 1.0) > 1.0e-8 for row in (benchmark, risk_anchor, previous)):
        raise ValueError("v536_relative_weight_vectors_must_sum_to_one")
    if np.any(linear < 0.0) or np.any(quadratic < 0.0):
        raise ValueError("v536_relative_costs_negative")
    if np.any(lower < 0.0) or np.any(lower > upper) or lower.sum() > 1.0 or upper.sum() < 1.0:
        raise ValueError("v536_relative_bounds_infeasible")

    weights = cp.Variable(size)
    active = weights - benchmark
    change = weights - previous
    risk_distance = weights - risk_anchor
    objective = cp.Minimize(
        -expected @ active
        + 0.5 * active_risk_aversion * cp.quad_form(active, sigma)
        + uncertainty_penalty * cp.quad_form(active, mean_covariance)
        + risk_budget_anchor_penalty * cp.quad_form(risk_distance, sigma)
        + active_l2_penalty * cp.sum_squares(active)
        + linear @ cp.abs(change)
        + 0.5 * cp.sum(cp.multiply(quadratic, cp.square(change)))
    )
    constraints: list[tuple[str, cp.Constraint]] = [
        ("sum", cp.sum(weights) == 1.0),
        ("lower", weights >= lower),
        ("upper", weights <= upper),
        ("active_share", 0.5 * cp.norm1(active) <= max_active_share),
        (
            "annual_tracking_error_squared",
            12.0 * cp.quad_form(active, sigma) <= max_annual_tracking_error**2,
        ),
        ("turnover", 0.5 * cp.norm1(change) <= max_one_way_turnover),
    ]
    problem = cp.Problem(objective, [item for _, item in constraints])
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE} or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {"solver": "CLARABEL", "status": str(problem.status), "fallback_used": False},
        }
    solution = np.asarray(weights.value, dtype=float)
    audit = _constraint_audit(
        solution,
        benchmark,
        previous,
        sigma,
        lower,
        upper,
        max_active_share=max_active_share,
        max_tracking_error=max_annual_tracking_error,
        max_turnover=max_one_way_turnover,
    )
    if audit["max_violation"] > 1.0e-7:
        raise RuntimeError("v536_relative_solution_failed_primal_audit")
    current_active = solution - benchmark
    current_change = solution - previous
    distance = solution - risk_anchor
    raw_cost = float(linear @ np.abs(current_change) + 0.5 * quadratic @ (current_change**2))
    terms = {
        "active_expected_return": float(expected @ current_active),
        "active_risk_penalty": 0.5 * active_risk_aversion * float(current_active @ sigma @ current_active),
        "posterior_uncertainty_penalty": uncertainty_penalty * float(current_active @ mean_covariance @ current_active),
        "risk_budget_anchor_penalty": risk_budget_anchor_penalty * float(distance @ sigma @ distance),
        "active_l2_penalty": active_l2_penalty * float(current_active @ current_active),
        "raw_expected_transaction_cost": raw_cost,
        "penalized_transaction_cost": raw_cost,
        "minimization_objective": float(problem.value),
    }
    slacks = {
        "lower": solution - lower,
        "upper": upper - solution,
        "active_share": audit["active_share_slack"],
        "annual_tracking_error_squared": max_annual_tracking_error**2
        - 12.0 * float(current_active @ sigma @ current_active),
        "turnover": audit["turnover_slack"],
    }
    return {
        "status": "optimal" if problem.status == cp.OPTIMAL else "optimal_inaccurate",
        "weights": solution.tolist(),
        "active_weights": current_active.tolist(),
        "objective_terms": terms,
        "constraints": audit,
        "solver": _solver_diagnostics(problem, constraints, slacks),
    }


def optimize_absolute_v536(
    expected_return: Sequence[float],
    covariance: Sequence[Sequence[float]],
    posterior_mean_covariance: Sequence[Sequence[float]],
    risk_budget_anchor: Sequence[float],
    previous_weights: Sequence[float],
    *,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
    max_one_way_turnover: float,
    linear_cost: Sequence[float],
    quadratic_cost: Sequence[float],
    risk_aversion: float,
    uncertainty_penalty: float,
    anchor_penalty: float,
) -> dict[str, Any]:
    anchor = _vector(risk_budget_anchor, "absolute_risk_budget_anchor")
    size = len(anchor)
    expected = _vector(expected_return, "absolute_expected_return", size)
    previous = _vector(previous_weights, "absolute_previous_weights", size)
    lower = _vector(lower_bounds, "absolute_lower_bounds", size)
    upper = _vector(upper_bounds, "absolute_upper_bounds", size)
    linear = _vector(linear_cost, "absolute_linear_cost", size)
    quadratic = _vector(quadratic_cost, "absolute_quadratic_cost", size)
    sigma = _matrix(covariance, "absolute_covariance", size)
    mean_covariance = _matrix(posterior_mean_covariance, "absolute_posterior_mean_covariance", size)
    _finite_scalars(
        {
            "max_one_way_turnover": max_one_way_turnover,
            "risk_aversion": risk_aversion,
            "uncertainty_penalty": uncertainty_penalty,
            "anchor_penalty": anchor_penalty,
        }
    )
    if any(abs(float(row.sum()) - 1.0) > 1.0e-8 for row in (anchor, previous)):
        raise ValueError("v536_absolute_weight_vectors_must_sum_to_one")
    if np.any(lower < 0.0) or np.any(lower > upper) or lower.sum() > 1.0 or upper.sum() < 1.0:
        raise ValueError("v536_absolute_bounds_infeasible")
    weights = cp.Variable(size)
    change = weights - previous
    distance = weights - anchor
    objective = cp.Minimize(
        -expected @ weights
        + 0.5 * risk_aversion * cp.quad_form(weights, sigma)
        + uncertainty_penalty * cp.quad_form(weights, mean_covariance)
        + anchor_penalty * cp.quad_form(distance, sigma)
        + linear @ cp.abs(change)
        + 0.5 * cp.sum(cp.multiply(quadratic, cp.square(change)))
    )
    constraints: list[tuple[str, cp.Constraint]] = [
        ("sum", cp.sum(weights) == 1.0),
        ("lower", weights >= lower),
        ("upper", weights <= upper),
        ("turnover", 0.5 * cp.norm1(change) <= max_one_way_turnover),
    ]
    problem = cp.Problem(objective, [item for _, item in constraints])
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE} or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {"solver": "CLARABEL", "status": str(problem.status), "fallback_used": False},
        }
    solution = np.asarray(weights.value, dtype=float)
    audit = _constraint_audit(
        solution,
        None,
        previous,
        sigma,
        lower,
        upper,
        max_active_share=None,
        max_tracking_error=None,
        max_turnover=max_one_way_turnover,
    )
    if audit["max_violation"] > 1.0e-7:
        raise RuntimeError("v536_absolute_solution_failed_primal_audit")
    current_change = solution - previous
    distance = solution - anchor
    raw_cost = float(linear @ np.abs(current_change) + 0.5 * quadratic @ (current_change**2))
    terms = {
        "expected_return": float(expected @ solution),
        "risk_penalty": 0.5 * risk_aversion * float(solution @ sigma @ solution),
        "posterior_uncertainty_penalty": uncertainty_penalty * float(solution @ mean_covariance @ solution),
        "risk_budget_anchor_penalty": anchor_penalty * float(distance @ sigma @ distance),
        "raw_expected_transaction_cost": raw_cost,
        "penalized_transaction_cost": raw_cost,
        "minimization_objective": float(problem.value),
    }
    slacks = {
        "lower": solution - lower,
        "upper": upper - solution,
        "turnover": audit["turnover_slack"],
    }
    return {
        "status": "optimal" if problem.status == cp.OPTIMAL else "optimal_inaccurate",
        "weights": solution.tolist(),
        "objective_terms": terms,
        "constraints": audit,
        "solver": _solver_diagnostics(problem, constraints, slacks),
    }


__all__ = ["optimize_absolute_v536", "optimize_relative_v536"]
