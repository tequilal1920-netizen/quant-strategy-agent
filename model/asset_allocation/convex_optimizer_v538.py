"""Convex v5.3.8 solvers with explicit auxiliary-variable KKT checks.

This module supersedes the v5.3.7 research implementation without mutating it.
The L1 epigraph variables are ordinary variables with explicit non-negativity
constraints, so their dual feasibility, stationarity and complementarity are
part of the persisted certificate.  The benchmark-relative and absolute APIs
remain intentionally separate: the absolute solver has no benchmark input.
"""

from __future__ import annotations

from typing import Any, Sequence

import cvxpy as cp
import numpy as np

from .convex_optimizer_v537 import (
    _TOLERANCE,
    _bound_checks,
    _constraint_audit,
    _dual_array,
    _finite_nonnegative,
    _kkt_certificate,
    _matrix,
    _vector,
)


def _scalar_dual(constraint: cp.Constraint) -> float:
    """Return a finite scalar dual without NumPy scalar coercion warnings."""

    value = _dual_array(constraint)
    if np.asarray(value).size != 1:
        raise RuntimeError("v538_scalar_dual_shape_invalid")
    return float(np.asarray(value).reshape(-1)[0].item())


def _extend_auxiliary_certificate(
    certificate: dict[str, Any],
    entries: Sequence[tuple[str, np.ndarray, np.ndarray]],
) -> dict[str, Any]:
    """Add explicit epigraph non-negativity duals to a base certificate."""

    dual_violation = float(certificate["maximum_dual_feasibility_violation"])
    complementarity = float(certificate["maximum_complementarity_residual"])
    for name, dual, slack in entries:
        dual_array = np.asarray(dual, dtype=float)
        slack_array = np.asarray(slack, dtype=float)
        if dual_array.shape != slack_array.shape:
            raise RuntimeError("v538_auxiliary_certificate_shape_invalid")
        if not np.all(np.isfinite(dual_array)) or not np.all(np.isfinite(slack_array)):
            raise RuntimeError("v538_auxiliary_certificate_nonfinite")
        certificate["dual_values"][name] = dual_array.tolist()
        dual_violation = max(
            dual_violation,
            float(np.max(np.maximum(-dual_array, 0.0))),
        )
        complementarity = max(
            complementarity,
            float(np.max(np.abs(dual_array * slack_array))),
        )
    certificate["maximum_dual_feasibility_violation"] = dual_violation
    certificate["maximum_complementarity_residual"] = complementarity
    gap = certificate.get("absolute_duality_gap")
    certificate["duality_gap_available"] = gap is not None
    certificate["maximum_kkt_residual"] = max(
        float(certificate["maximum_primal_violation"]),
        float(certificate["maximum_stationarity_residual"]),
        complementarity,
        dual_violation,
        0.0 if gap is None else abs(float(gap)),
    )
    certificate["certificate_scope"] = (
        "primal_feasibility+dual_feasibility+stationarity+complementarity"
        "+solver_gap_when_available"
    )
    return certificate


def optimize_relative_v538(
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
    """Solve directly in benchmark-active space; never post-scale weights."""

    benchmark = _vector(benchmark_weights, "benchmark")
    size = len(benchmark)
    expected = _vector(active_expected_return, "active_expected_return", size)
    previous = _vector(previous_weights, "previous_weights", size)
    lower = _vector(lower_bounds, "lower_bounds", size)
    upper = _vector(upper_bounds, "upper_bounds", size)
    linear = _vector(linear_cost, "linear_cost", size)
    quadratic = _vector(quadratic_cost, "quadratic_cost", size)
    sigma = _matrix(covariance, "covariance", size)
    mean_covariance = _matrix(
        posterior_mean_covariance, "posterior_mean_covariance", size
    )
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
        raise ValueError("v538_relative_costs_negative")
    if (
        abs(float(benchmark.sum()) - 1.0) > 1.0e-8
        or abs(float(previous.sum()) - 1.0) > 1.0e-8
    ):
        raise ValueError("v538_relative_weight_vectors_must_sum_to_one")
    _bound_checks(lower, upper)

    weights = cp.Variable(size, name="weights")
    active_abs = cp.Variable(size, name="active_abs")
    change_abs = cp.Variable(size, name="change_abs")
    active = weights - benchmark
    change = weights - previous
    equality = cp.sum(weights) == 1.0
    lower_constraint = weights >= lower
    upper_constraint = weights <= upper
    active_positive = active <= active_abs
    active_negative = -active <= active_abs
    active_nonnegative = active_abs >= 0.0
    change_positive = change <= change_abs
    change_negative = -change <= change_abs
    change_nonnegative = change_abs >= 0.0
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
    problem = cp.Problem(
        objective,
        [
            equality,
            lower_constraint,
            upper_constraint,
            active_positive,
            active_negative,
            active_nonnegative,
            change_positive,
            change_negative,
            change_nonnegative,
            active_share_constraint,
            tracking_constraint,
            turnover_constraint,
        ],
    )
    if not problem.is_dcp():
        raise RuntimeError("v538_relative_problem_not_dcp")
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status != cp.OPTIMAL or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {
                "solver": "CLARABEL",
                "status": str(problem.status),
                "fallback_used": False,
            },
        }

    solution = np.asarray(weights.value, dtype=float)
    absolute_active = np.asarray(active_abs.value, dtype=float)
    absolute_change = np.asarray(change_abs.value, dtype=float)
    current_active = solution - benchmark
    current_change = solution - previous
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
        raise RuntimeError("v538_relative_solution_failed_primal_audit")

    active_pos_dual = _dual_array(active_positive, size)
    active_neg_dual = _dual_array(active_negative, size)
    active_nonnegative_dual = _dual_array(active_nonnegative, size)
    change_pos_dual = _dual_array(change_positive, size)
    change_neg_dual = _dual_array(change_negative, size)
    change_nonnegative_dual = _dual_array(change_nonnegative, size)
    active_share_dual = _scalar_dual(active_share_constraint)
    turnover_dual = _scalar_dual(turnover_constraint)
    gradient = (
        -expected
        + active_risk_aversion * (sigma @ current_active)
        + 2.0 * uncertainty_penalty * (mean_covariance @ current_active)
        + 2.0 * active_l2_penalty * current_active
        + quadratic * current_change
        + active_pos_dual
        - active_neg_dual
        + change_pos_dual
        - change_neg_dual
    )
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
            max_active_share * 0.0
            + max_annual_tracking_error**2
            - 12.0 * float(current_active @ sigma @ current_active),
            24.0 * (sigma @ current_active),
        ),
        (
            "turnover",
            turnover_constraint,
            max_one_way_turnover - 0.5 * float(absolute_change.sum()),
            np.zeros(size),
        ),
    ]
    auxiliary = [
        (
            "active_abs",
            -active_pos_dual
            - active_neg_dual
            - active_nonnegative_dual
            + 0.5 * active_share_dual,
        ),
        (
            "change_abs",
            linear
            - change_pos_dual
            - change_neg_dual
            - change_nonnegative_dual
            + 0.5 * turnover_dual,
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
    certificate = _extend_auxiliary_certificate(
        certificate,
        [
            ("active_abs_nonnegative", active_nonnegative_dual, absolute_active),
            ("change_abs_nonnegative", change_nonnegative_dual, absolute_change),
        ],
    )
    if certificate["maximum_kkt_residual"] > 5.0e-6:
        raise RuntimeError("v538_relative_solution_failed_kkt_audit")
    raw_cost = float(
        linear @ absolute_change + 0.5 * quadratic @ (current_change**2)
    )
    return {
        "status": "optimal",
        "weights": solution.tolist(),
        "active_weights": current_active.tolist(),
        "objective_terms": {
            "active_expected_return": float(expected @ current_active),
            "active_risk_penalty": 0.5
            * active_risk_aversion
            * float(current_active @ sigma @ current_active),
            "posterior_uncertainty_penalty": uncertainty_penalty
            * float(current_active @ mean_covariance @ current_active),
            "active_l2_penalty": active_l2_penalty
            * float(current_active @ current_active),
            "raw_expected_transaction_cost": raw_cost,
            "penalized_transaction_cost": raw_cost,
            "minimization_objective": float(problem.value),
        },
        "constraints": audit,
        "solver": certificate,
    }


def optimize_absolute_v538(
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
    """Solve the absolute portfolio without accepting a benchmark input."""

    anchor = _vector(risk_budget_anchor, "absolute_anchor")
    size = len(anchor)
    expected = _vector(expected_return, "absolute_expected_return", size)
    previous = _vector(previous_weights, "absolute_previous_weights", size)
    lower = _vector(lower_bounds, "absolute_lower_bounds", size)
    upper = _vector(upper_bounds, "absolute_upper_bounds", size)
    linear = _vector(linear_cost, "absolute_linear_cost", size)
    quadratic = _vector(quadratic_cost, "absolute_quadratic_cost", size)
    sigma = _matrix(covariance, "absolute_covariance", size)
    mean_covariance = _matrix(
        posterior_mean_covariance, "absolute_mean_covariance", size
    )
    _finite_nonnegative(
        {
            "max_one_way_turnover": max_one_way_turnover,
            "risk_aversion": risk_aversion,
            "uncertainty_penalty": uncertainty_penalty,
            "anchor_penalty": anchor_penalty,
        }
    )
    if np.any(linear < 0.0) or np.any(quadratic < 0.0):
        raise ValueError("v538_absolute_costs_negative")
    if (
        abs(float(anchor.sum()) - 1.0) > 1.0e-8
        or abs(float(previous.sum()) - 1.0) > 1.0e-8
    ):
        raise ValueError("v538_absolute_weight_vectors_must_sum_to_one")
    _bound_checks(lower, upper)

    weights = cp.Variable(size, name="weights")
    change_abs = cp.Variable(size, name="change_abs")
    change = weights - previous
    distance = weights - anchor
    equality = cp.sum(weights) == 1.0
    lower_constraint = weights >= lower
    upper_constraint = weights <= upper
    change_positive = change <= change_abs
    change_negative = -change <= change_abs
    change_nonnegative = change_abs >= 0.0
    turnover_constraint = 0.5 * cp.sum(change_abs) <= max_one_way_turnover
    objective = cp.Minimize(
        -expected @ weights
        + 0.5 * risk_aversion * cp.quad_form(weights, sigma)
        + uncertainty_penalty * cp.quad_form(weights, mean_covariance)
        + anchor_penalty * cp.quad_form(distance, sigma)
        + linear @ change_abs
        + 0.5 * cp.sum(cp.multiply(quadratic, cp.square(change)))
    )
    problem = cp.Problem(
        objective,
        [
            equality,
            lower_constraint,
            upper_constraint,
            change_positive,
            change_negative,
            change_nonnegative,
            turnover_constraint,
        ],
    )
    if not problem.is_dcp():
        raise RuntimeError("v538_absolute_problem_not_dcp")
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status != cp.OPTIMAL or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {
                "solver": "CLARABEL",
                "status": str(problem.status),
                "fallback_used": False,
            },
        }

    solution = np.asarray(weights.value, dtype=float)
    absolute_change = np.asarray(change_abs.value, dtype=float)
    current_change = solution - previous
    current_distance = solution - anchor
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
        raise RuntimeError("v538_absolute_solution_failed_primal_audit")

    change_pos_dual = _dual_array(change_positive, size)
    change_neg_dual = _dual_array(change_negative, size)
    change_nonnegative_dual = _dual_array(change_nonnegative, size)
    turnover_dual = _scalar_dual(turnover_constraint)
    gradient = (
        -expected
        + risk_aversion * (sigma @ solution)
        + 2.0 * uncertainty_penalty * (mean_covariance @ solution)
        + 2.0 * anchor_penalty * (sigma @ current_distance)
        + quadratic * current_change
        + change_pos_dual
        - change_neg_dual
    )
    certificate = _kkt_certificate(
        problem,
        gradient=gradient,
        equality=equality,
        lower_constraint=lower_constraint,
        upper_constraint=upper_constraint,
        lower_slack=solution - lower,
        upper_slack=upper - solution,
        scalar_inequalities=[
            (
                "turnover",
                turnover_constraint,
                max_one_way_turnover - 0.5 * float(absolute_change.sum()),
                np.zeros(size),
            )
        ],
        auxiliary_stationarity=[
            (
                "change_abs",
                linear
                - change_pos_dual
                - change_neg_dual
                - change_nonnegative_dual
                + 0.5 * turnover_dual,
            )
        ],
        primal_max_violation=float(audit["max_violation"]),
    )
    certificate = _extend_auxiliary_certificate(
        certificate,
        [("change_abs_nonnegative", change_nonnegative_dual, absolute_change)],
    )
    if certificate["maximum_kkt_residual"] > 5.0e-6:
        raise RuntimeError("v538_absolute_solution_failed_kkt_audit")
    raw_cost = float(
        linear @ absolute_change + 0.5 * quadratic @ (current_change**2)
    )
    return {
        "status": "optimal",
        "weights": solution.tolist(),
        "objective_terms": {
            "expected_return": float(expected @ solution),
            "risk_penalty": 0.5 * risk_aversion * float(solution @ sigma @ solution),
            "posterior_uncertainty_penalty": uncertainty_penalty
            * float(solution @ mean_covariance @ solution),
            "anchor_penalty": anchor_penalty
            * float(current_distance @ sigma @ current_distance),
            "raw_expected_transaction_cost": raw_cost,
            "penalized_transaction_cost": raw_cost,
            "minimization_objective": float(problem.value),
        },
        "constraints": audit,
        "solver": certificate,
    }


__all__ = ["optimize_relative_v538", "optimize_absolute_v538"]
