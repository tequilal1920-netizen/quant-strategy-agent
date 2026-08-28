"""Fail-closed convex optimizers with explicit KKT certificates.

The v5.3.7 research solver keeps benchmark-relative and absolute objectives
separate, models all L1 terms with explicit auxiliary variables, and reports
primal feasibility, dual feasibility, stationarity, complementarity and the
solver duality gap for the exact weights returned to the caller.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import cvxpy as cp
import numpy as np


_TOLERANCE = 1.0e-7


def _vector(values: Sequence[float], name: str, size: int | None = None) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or (size is not None and len(result) != size):
        raise ValueError(f"v537_{name}_shape_invalid")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"v537_{name}_nonfinite")
    return result


def _matrix(values: Sequence[Sequence[float]], name: str, size: int) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.shape != (size, size) or not np.all(np.isfinite(result)):
        raise ValueError(f"v537_{name}_invalid")
    result = 0.5 * (result + result.T)
    eigenvalues, eigenvectors = np.linalg.eigh(result)
    floor = max(float(np.max(np.abs(eigenvalues))) * 1.0e-10, 1.0e-12)
    return (eigenvectors * np.maximum(eigenvalues, floor)) @ eigenvectors.T


def _finite_nonnegative(values: Mapping[str, float]) -> None:
    for name, value in values.items():
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError(f"v537_{name}_must_be_finite_nonnegative")


def _bound_checks(lower: np.ndarray, upper: np.ndarray) -> None:
    if np.any(lower < 0.0) or np.any(lower > upper):
        raise ValueError("v537_bounds_invalid")
    if float(lower.sum()) > 1.0 + 1.0e-12 or float(upper.sum()) < 1.0 - 1.0e-12:
        raise ValueError("v537_bounds_infeasible")


def _constraint_audit(
    weights: np.ndarray,
    previous: np.ndarray,
    covariance: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    benchmark: np.ndarray | None,
    max_active_share: float | None,
    max_tracking_error: float | None,
    max_turnover: float,
) -> dict[str, Any]:
    change = weights - previous
    turnover = 0.5 * float(np.abs(change).sum())
    violations = {
        "sum": abs(float(weights.sum()) - 1.0),
        "lower": float(np.max(np.maximum(lower - weights, 0.0))),
        "upper": float(np.max(np.maximum(weights - upper, 0.0))),
        "turnover": max(0.0, turnover - float(max_turnover)),
    }
    report: dict[str, Any] = {
        "sum_weights": float(weights.sum()),
        "one_way_turnover": turnover,
        "turnover_slack": float(max_turnover) - turnover,
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
    report["max_violation"] = max(violations.values(), default=0.0)
    return report


def _dual_array(constraint: cp.Constraint, size: int | None = None) -> np.ndarray:
    if constraint.dual_value is None:
        raise RuntimeError("v537_missing_dual_certificate")
    value = np.asarray(constraint.dual_value, dtype=float)
    if size is not None and value.size != size:
        raise RuntimeError("v537_dual_shape_invalid")
    if not np.all(np.isfinite(value)):
        raise RuntimeError("v537_nonfinite_dual_certificate")
    return value.reshape(-1) if size is not None else value


def _solver_gap(problem: cp.Problem) -> float | None:
    extra = getattr(problem.solver_stats, "extra_stats", None)
    if extra is None:
        return None
    candidate = getattr(extra, "gap_abs", None)
    if candidate is None and isinstance(extra, Mapping):
        candidate = extra.get("gap_abs")
    if candidate is None:
        return None
    candidate = float(candidate)
    return candidate if math.isfinite(candidate) else None


def _kkt_certificate(
    problem: cp.Problem,
    *,
    gradient: np.ndarray,
    equality: cp.Constraint,
    lower_constraint: cp.Constraint,
    upper_constraint: cp.Constraint,
    lower_slack: np.ndarray,
    upper_slack: np.ndarray,
    scalar_inequalities: Sequence[tuple[str, cp.Constraint, float, np.ndarray]],
    auxiliary_stationarity: Sequence[tuple[str, np.ndarray]],
    primal_max_violation: float,
) -> dict[str, Any]:
    size = len(gradient)
    equality_dual = float(_dual_array(equality))
    lower_dual = _dual_array(lower_constraint, size)
    upper_dual = _dual_array(upper_constraint, size)
    stationarity = gradient + equality_dual - lower_dual + upper_dual
    complementarity = [
        float(np.max(np.abs(lower_dual * lower_slack))),
        float(np.max(np.abs(upper_dual * upper_slack))),
    ]
    dual_feasibility = [
        float(np.max(np.maximum(-lower_dual, 0.0))),
        float(np.max(np.maximum(-upper_dual, 0.0))),
    ]
    dual_values: dict[str, Any] = {
        "sum": equality_dual,
        "lower": lower_dual.tolist(),
        "upper": upper_dual.tolist(),
    }
    for name, constraint, slack, derivative in scalar_inequalities:
        dual = float(_dual_array(constraint))
        dual_values[name] = dual
        stationarity = stationarity + dual * derivative
        complementarity.append(abs(dual * float(slack)))
        dual_feasibility.append(max(-dual, 0.0))
    auxiliary_residuals = {
        name: float(np.max(np.abs(residual))) for name, residual in auxiliary_stationarity
    }
    maximum_stationarity = max(
        float(np.max(np.abs(stationarity))),
        max(auxiliary_residuals.values(), default=0.0),
    )
    maximum_complementarity = max(complementarity, default=0.0)
    maximum_dual_violation = max(dual_feasibility, default=0.0)
    gap = _solver_gap(problem)
    maximum_kkt = max(
        float(primal_max_violation),
        maximum_stationarity,
        maximum_complementarity,
        maximum_dual_violation,
        0.0 if gap is None else abs(gap),
    )
    stats = problem.solver_stats
    return {
        "solver": "CLARABEL",
        "status": str(problem.status),
        "iterations": int(getattr(stats, "num_iters", 0) or 0),
        "solve_time_seconds": float(getattr(stats, "solve_time", 0.0) or 0.0),
        "dual_values": dual_values,
        "maximum_primal_violation": float(primal_max_violation),
        "maximum_stationarity_residual": maximum_stationarity,
        "maximum_complementarity_residual": maximum_complementarity,
        "maximum_dual_feasibility_violation": maximum_dual_violation,
        "absolute_duality_gap": gap,
        "maximum_kkt_residual": maximum_kkt,
        "auxiliary_stationarity_residuals": auxiliary_residuals,
        "hard_constraints_relaxed": False,
        "fallback_used": False,
        "selection_uses_test": False,
    }


def optimize_relative_v537(
    active_expected_return: Sequence[float],
    covariance: Sequence[Sequence[float]],
    posterior_mean_covariance: Sequence[Sequence[float]],
    benchmark_weights: Sequence[float],
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
    active_l2_penalty: float,
) -> dict[str, Any]:
    benchmark = _vector(benchmark_weights, "benchmark")
    size = len(benchmark)
    expected = _vector(active_expected_return, "active_expected_return", size)
    previous = _vector(previous_weights, "previous_weights", size)
    lower = _vector(lower_bounds, "lower_bounds", size)
    upper = _vector(upper_bounds, "upper_bounds", size)
    linear = _vector(linear_cost, "linear_cost", size)
    quadratic = _vector(quadratic_cost, "quadratic_cost", size)
    sigma = _matrix(covariance, "covariance", size)
    mean_covariance = _matrix(posterior_mean_covariance, "posterior_mean_covariance", size)
    _finite_nonnegative(
        {
            "max_active_share": max_active_share,
            "max_annual_tracking_error": max_annual_tracking_error,
            "max_one_way_turnover": max_one_way_turnover,
            "active_risk_aversion": active_risk_aversion,
            "uncertainty_penalty": uncertainty_penalty,
            "active_l2_penalty": active_l2_penalty,
        }
    )
    if np.any(linear < 0.0) or np.any(quadratic < 0.0):
        raise ValueError("v537_relative_costs_negative")
    if abs(float(benchmark.sum()) - 1.0) > 1.0e-8 or abs(float(previous.sum()) - 1.0) > 1.0e-8:
        raise ValueError("v537_relative_weight_vectors_must_sum_to_one")
    _bound_checks(lower, upper)

    weights = cp.Variable(size, name="weights")
    active_abs = cp.Variable(size, nonneg=True, name="active_abs")
    change_abs = cp.Variable(size, nonneg=True, name="change_abs")
    active = weights - benchmark
    change = weights - previous
    equality = cp.sum(weights) == 1.0
    lower_constraint = weights >= lower
    upper_constraint = weights <= upper
    active_positive = active <= active_abs
    active_negative = -active <= active_abs
    change_positive = change <= change_abs
    change_negative = -change <= change_abs
    active_share_constraint = 0.5 * cp.sum(active_abs) <= max_active_share
    tracking_constraint = (
        12.0 * cp.quad_form(active, sigma) <= max_annual_tracking_error**2
    )
    turnover_constraint = 0.5 * cp.sum(change_abs) <= max_one_way_turnover
    objective = cp.Minimize(
        -expected @ active
        + 0.5 * active_risk_aversion * cp.quad_form(active, sigma)
        + uncertainty_penalty * cp.quad_form(active, mean_covariance)
        + active_l2_penalty * cp.sum_squares(active)
        + linear @ change_abs
        + 0.5 * cp.sum(cp.multiply(quadratic, cp.square(change)))
    )
    constraints = [
        equality,
        lower_constraint,
        upper_constraint,
        active_positive,
        active_negative,
        change_positive,
        change_negative,
        active_share_constraint,
        tracking_constraint,
        turnover_constraint,
    ]
    problem = cp.Problem(objective, constraints)
    if not problem.is_dcp():
        raise RuntimeError("v537_relative_problem_not_dcp")
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status != cp.OPTIMAL or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {"solver": "CLARABEL", "status": str(problem.status), "fallback_used": False},
        }
    solution = np.asarray(weights.value, dtype=float)
    audit = _constraint_audit(
        solution,
        previous,
        sigma,
        lower,
        upper,
        benchmark=benchmark,
        max_active_share=max_active_share,
        max_tracking_error=max_annual_tracking_error,
        max_turnover=max_one_way_turnover,
    )
    if audit["max_violation"] > _TOLERANCE:
        raise RuntimeError("v537_relative_solution_failed_primal_audit")
    current_active = solution - benchmark
    current_change = solution - previous
    absolute_change = np.asarray(change_abs.value, dtype=float)
    absolute_active = np.asarray(active_abs.value, dtype=float)
    raw_cost = float(linear @ absolute_change + 0.5 * quadratic @ (current_change**2))
    gradient = (
        -expected
        + active_risk_aversion * (sigma @ current_active)
        + 2.0 * uncertainty_penalty * (mean_covariance @ current_active)
        + 2.0 * active_l2_penalty * current_active
        + quadratic * current_change
    )
    active_pos_dual = _dual_array(active_positive, size)
    active_neg_dual = _dual_array(active_negative, size)
    change_pos_dual = _dual_array(change_positive, size)
    change_neg_dual = _dual_array(change_negative, size)
    gradient = gradient + active_pos_dual - active_neg_dual + change_pos_dual - change_neg_dual
    active_share_dual = float(_dual_array(active_share_constraint))
    turnover_dual = float(_dual_array(turnover_constraint))
    auxiliary = [
        (
            "active_abs",
            linear * 0.0 - active_pos_dual - active_neg_dual + 0.5 * active_share_dual,
        ),
        (
            "change_abs",
            linear - change_pos_dual - change_neg_dual + 0.5 * turnover_dual,
        ),
    ]
    scalar = [
        (
            "active_share",
            active_share_constraint,
            max_active_share - 0.5 * float(absolute_active.sum()),
            np.zeros(size),
        ),
        (
            "annual_tracking_error_squared",
            tracking_constraint,
            max_annual_tracking_error**2 - 12.0 * float(current_active @ sigma @ current_active),
            24.0 * (sigma @ current_active),
        ),
        (
            "turnover",
            turnover_constraint,
            max_one_way_turnover - 0.5 * float(absolute_change.sum()),
            np.zeros(size),
        ),
    ]
    certificate = _kkt_certificate(
        problem,
        gradient=gradient,
        equality=equality,
        lower_constraint=lower_constraint,
        upper_constraint=upper_constraint,
        lower_slack=solution - lower,
        upper_slack=upper - solution,
        scalar_inequalities=scalar,
        auxiliary_stationarity=auxiliary,
        primal_max_violation=float(audit["max_violation"]),
    )
    if certificate["maximum_kkt_residual"] > 5.0e-6:
        raise RuntimeError("v537_relative_solution_failed_kkt_audit")
    return {
        "status": "optimal",
        "weights": solution.tolist(),
        "active_weights": current_active.tolist(),
        "objective_terms": {
            "active_expected_return": float(expected @ current_active),
            "active_risk_penalty": 0.5 * active_risk_aversion * float(current_active @ sigma @ current_active),
            "posterior_uncertainty_penalty": uncertainty_penalty * float(current_active @ mean_covariance @ current_active),
            "active_l2_penalty": active_l2_penalty * float(current_active @ current_active),
            "raw_expected_transaction_cost": raw_cost,
            "penalized_transaction_cost": raw_cost,
            "minimization_objective": float(problem.value),
        },
        "constraints": audit,
        "solver": certificate,
    }


def optimize_absolute_v537(
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
    anchor = _vector(risk_budget_anchor, "absolute_anchor")
    size = len(anchor)
    expected = _vector(expected_return, "absolute_expected_return", size)
    previous = _vector(previous_weights, "absolute_previous_weights", size)
    lower = _vector(lower_bounds, "absolute_lower_bounds", size)
    upper = _vector(upper_bounds, "absolute_upper_bounds", size)
    linear = _vector(linear_cost, "absolute_linear_cost", size)
    quadratic = _vector(quadratic_cost, "absolute_quadratic_cost", size)
    sigma = _matrix(covariance, "absolute_covariance", size)
    mean_covariance = _matrix(posterior_mean_covariance, "absolute_mean_covariance", size)
    _finite_nonnegative(
        {
            "max_one_way_turnover": max_one_way_turnover,
            "risk_aversion": risk_aversion,
            "uncertainty_penalty": uncertainty_penalty,
            "anchor_penalty": anchor_penalty,
        }
    )
    if np.any(linear < 0.0) or np.any(quadratic < 0.0):
        raise ValueError("v537_absolute_costs_negative")
    if abs(float(anchor.sum()) - 1.0) > 1.0e-8 or abs(float(previous.sum()) - 1.0) > 1.0e-8:
        raise ValueError("v537_absolute_weight_vectors_must_sum_to_one")
    _bound_checks(lower, upper)

    weights = cp.Variable(size, name="weights")
    change_abs = cp.Variable(size, nonneg=True, name="change_abs")
    change = weights - previous
    distance = weights - anchor
    equality = cp.sum(weights) == 1.0
    lower_constraint = weights >= lower
    upper_constraint = weights <= upper
    change_positive = change <= change_abs
    change_negative = -change <= change_abs
    turnover_constraint = 0.5 * cp.sum(change_abs) <= max_one_way_turnover
    objective = cp.Minimize(
        -expected @ weights
        + 0.5 * risk_aversion * cp.quad_form(weights, sigma)
        + uncertainty_penalty * cp.quad_form(weights, mean_covariance)
        + anchor_penalty * cp.quad_form(distance, sigma)
        + linear @ change_abs
        + 0.5 * cp.sum(cp.multiply(quadratic, cp.square(change)))
    )
    constraints = [
        equality,
        lower_constraint,
        upper_constraint,
        change_positive,
        change_negative,
        turnover_constraint,
    ]
    problem = cp.Problem(objective, constraints)
    if not problem.is_dcp():
        raise RuntimeError("v537_absolute_problem_not_dcp")
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status != cp.OPTIMAL or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {"solver": "CLARABEL", "status": str(problem.status), "fallback_used": False},
        }
    solution = np.asarray(weights.value, dtype=float)
    audit = _constraint_audit(
        solution,
        previous,
        sigma,
        lower,
        upper,
        benchmark=None,
        max_active_share=None,
        max_tracking_error=None,
        max_turnover=max_one_way_turnover,
    )
    if audit["max_violation"] > _TOLERANCE:
        raise RuntimeError("v537_absolute_solution_failed_primal_audit")
    current_change = solution - previous
    absolute_change = np.asarray(change_abs.value, dtype=float)
    current_distance = solution - anchor
    raw_cost = float(linear @ absolute_change + 0.5 * quadratic @ (current_change**2))
    gradient = (
        -expected
        + risk_aversion * (sigma @ solution)
        + 2.0 * uncertainty_penalty * (mean_covariance @ solution)
        + 2.0 * anchor_penalty * (sigma @ current_distance)
        + quadratic * current_change
    )
    change_pos_dual = _dual_array(change_positive, size)
    change_neg_dual = _dual_array(change_negative, size)
    gradient = gradient + change_pos_dual - change_neg_dual
    turnover_dual = float(_dual_array(turnover_constraint))
    auxiliary = [
        (
            "change_abs",
            linear - change_pos_dual - change_neg_dual + 0.5 * turnover_dual,
        )
    ]
    scalar = [
        (
            "turnover",
            turnover_constraint,
            max_one_way_turnover - 0.5 * float(absolute_change.sum()),
            np.zeros(size),
        )
    ]
    certificate = _kkt_certificate(
        problem,
        gradient=gradient,
        equality=equality,
        lower_constraint=lower_constraint,
        upper_constraint=upper_constraint,
        lower_slack=solution - lower,
        upper_slack=upper - solution,
        scalar_inequalities=scalar,
        auxiliary_stationarity=auxiliary,
        primal_max_violation=float(audit["max_violation"]),
    )
    if certificate["maximum_kkt_residual"] > 5.0e-6:
        raise RuntimeError("v537_absolute_solution_failed_kkt_audit")
    return {
        "status": "optimal",
        "weights": solution.tolist(),
        "objective_terms": {
            "expected_return": float(expected @ solution),
            "risk_penalty": 0.5 * risk_aversion * float(solution @ sigma @ solution),
            "posterior_uncertainty_penalty": uncertainty_penalty * float(solution @ mean_covariance @ solution),
            "anchor_penalty": anchor_penalty * float(current_distance @ sigma @ current_distance),
            "raw_expected_transaction_cost": raw_cost,
            "penalized_transaction_cost": raw_cost,
            "minimization_objective": float(problem.value),
        },
        "constraints": audit,
        "solver": certificate,
    }


__all__ = ["optimize_relative_v537", "optimize_absolute_v537"]
