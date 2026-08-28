"""Strict v5.3.9 convex solvers with complete high-level KKT certificates.

The benchmark-relative problem is solved directly in active-weight space and
is never post-scaled.  The absolute problem deliberately has no benchmark
argument.  Every scalar and vector inequality (including the L1 epigraphs)
participates in primal, dual, stationarity and complementarity checks.  A
pinned CLARABEL canonical residual/gap certificate is persisted as an
independent, redundant check.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import clarabel
import cvxpy as cp
import numpy as np


KKT_LIMIT_V539 = 1.0e-7
CVXPY_VERSION_V539 = "1.7.5"
CLARABEL_VERSION_V539 = "0.11.1"


def _vector(values: Sequence[float], name: str, size: int | None = None) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or (size is not None and len(result) != size):
        raise ValueError(f"v539_{name}_shape_invalid")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"v539_{name}_nonfinite")
    return result


def _psd_matrix(
    values: Sequence[Sequence[float]], name: str, size: int
) -> tuple[np.ndarray, dict[str, float]]:
    raw = np.asarray(values, dtype=float)
    if raw.shape != (size, size) or not np.all(np.isfinite(raw)):
        raise ValueError(f"v539_{name}_invalid")
    symmetric = 0.5 * (raw + raw.T)
    asymmetry = float(np.linalg.norm(raw - raw.T, ord="fro"))
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    scale = max(float(np.max(np.abs(eigenvalues))), 1.0e-12)
    if float(eigenvalues.min()) < -1.0e-10 * scale:
        raise ValueError(f"v539_{name}_materially_non_psd")
    floor = max(scale * 1.0e-12, 1.0e-15)
    projected = (eigenvectors * np.maximum(eigenvalues, floor)) @ eigenvectors.T
    repair = float(np.linalg.norm(projected - symmetric, ord="fro"))
    denominator = max(float(np.linalg.norm(symmetric, ord="fro")), 1.0e-15)
    relative_repair = repair / denominator
    condition = float(np.linalg.cond(projected))
    if not math.isfinite(condition) or condition > 1.0e12 or relative_repair > 1.0e-6:
        raise ValueError(f"v539_{name}_psd_repair_or_condition_failed")
    return projected, {
        "minimum_eigenvalue_before": float(eigenvalues.min()),
        "minimum_eigenvalue_after": float(np.linalg.eigvalsh(projected).min()),
        "relative_psd_repair_norm": relative_repair,
        "condition_number": condition,
        "asymmetry_frobenius_norm": asymmetry,
    }


def _finite_nonnegative(values: Mapping[str, float]) -> None:
    for name, value in values.items():
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError(f"v539_{name}_must_be_finite_nonnegative")


def _bounds(lower: np.ndarray, upper: np.ndarray) -> None:
    if np.any(lower < 0.0) or np.any(lower > upper):
        raise ValueError("v539_bounds_invalid")
    if float(lower.sum()) > 1.0 + 1.0e-12 or float(upper.sum()) < 1.0 - 1.0e-12:
        raise ValueError("v539_bounds_infeasible")


def _dual_vector(constraint: cp.Constraint, size: int) -> np.ndarray:
    if constraint.dual_value is None:
        raise RuntimeError("v539_missing_dual")
    result = np.asarray(constraint.dual_value, dtype=float).reshape(-1)
    if result.size != size or not np.all(np.isfinite(result)):
        raise RuntimeError("v539_vector_dual_invalid")
    return result


def _dual_scalar(constraint: cp.Constraint) -> float:
    if constraint.dual_value is None:
        raise RuntimeError("v539_missing_dual")
    result = np.asarray(constraint.dual_value, dtype=float).reshape(-1)
    if result.size != 1 or not np.isfinite(result[0]):
        raise RuntimeError("v539_scalar_dual_invalid")
    return float(result[0].item())


def _canonical_certificate(problem: cp.Problem) -> dict[str, Any]:
    if cp.__version__ != CVXPY_VERSION_V539 or clarabel.__version__ != CLARABEL_VERSION_V539:
        raise RuntimeError("v539_unpinned_cvxpy_or_clarabel_version")
    solver = getattr(problem, "_solver_cache", {}).get("CLARABEL")
    if solver is None or not hasattr(solver, "get_info"):
        raise RuntimeError("v539_canonical_certificate_unavailable")
    info = solver.get_info()
    values = {
        "canonical_primal_residual": float(info.res_primal),
        "canonical_dual_residual": float(info.res_dual),
        "absolute_duality_gap": float(info.gap_abs),
        "relative_duality_gap": float(info.gap_rel),
    }
    if str(info.status) != "Solved" or not all(np.isfinite(list(values.values()))):
        raise RuntimeError("v539_canonical_certificate_invalid")
    if max(abs(value) for value in values.values()) > KKT_LIMIT_V539:
        raise RuntimeError("v539_canonical_certificate_failed")
    return {
        **values,
        "solver_status": str(info.status),
        "cvxpy_version": cp.__version__,
        "clarabel_version": clarabel.__version__,
        "version_lock_passed": True,
    }


def _constraint_entry(name: str, dual: np.ndarray | float, slack: np.ndarray | float) -> dict[str, Any]:
    dual_array = np.asarray(dual, dtype=float)
    slack_array = np.asarray(slack, dtype=float)
    if dual_array.shape != slack_array.shape:
        if dual_array.size == 1 and slack_array.size == 1:
            dual_array = dual_array.reshape(1)
            slack_array = slack_array.reshape(1)
        else:
            raise RuntimeError("v539_kkt_entry_shape_invalid")
    return {
        "name": name,
        "dual": dual_array,
        "slack": slack_array,
        "primal_violation": float(np.max(np.maximum(-slack_array, 0.0))),
        "dual_violation": float(np.max(np.maximum(-dual_array, 0.0))),
        "complementarity": float(np.max(np.abs(dual_array * slack_array))),
    }


def _assemble_certificate(
    problem: cp.Problem,
    *,
    equality_residual: float,
    equality_dual: float,
    stationarity_weights: np.ndarray,
    stationarity_auxiliary: Mapping[str, np.ndarray],
    inequalities: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    primal = max(
        abs(float(equality_residual)),
        max((entry["primal_violation"] for entry in inequalities), default=0.0),
    )
    dual = max((entry["dual_violation"] for entry in inequalities), default=0.0)
    complementarity = max(
        (entry["complementarity"] for entry in inequalities), default=0.0
    )
    auxiliary_residuals = {
        name: float(np.max(np.abs(np.asarray(residual, dtype=float))))
        for name, residual in stationarity_auxiliary.items()
    }
    stationarity = max(
        float(np.max(np.abs(stationarity_weights))),
        max(auxiliary_residuals.values(), default=0.0),
    )
    canonical = _canonical_certificate(problem)
    maximum = max(
        primal,
        dual,
        complementarity,
        stationarity,
        canonical["canonical_primal_residual"],
        canonical["canonical_dual_residual"],
        canonical["absolute_duality_gap"],
    )
    certificate = {
        "solver": "CLARABEL",
        "status": str(problem.status),
        "iterations": int(getattr(problem.solver_stats, "num_iters", 0) or 0),
        "solve_time_seconds": float(
            getattr(problem.solver_stats, "solve_time", 0.0) or 0.0
        ),
        "equality_dual": equality_dual,
        "dual_values": {
            entry["name"]: np.asarray(entry["dual"]).tolist()
            for entry in inequalities
        },
        "maximum_primal_violation": primal,
        "maximum_dual_feasibility_violation": dual,
        "maximum_stationarity_residual": stationarity,
        "maximum_complementarity_residual": complementarity,
        "auxiliary_stationarity_residuals": auxiliary_residuals,
        "canonical_solver_certificate": canonical,
        "absolute_duality_gap": canonical["absolute_duality_gap"],
        "duality_gap_available": True,
        "maximum_kkt_residual": maximum,
        "certificate_scope": (
            "all_high_level_equalities+all_vector_and_scalar_inequalities+"
            "all_epigraph_variables+canonical_primal_dual_gap"
        ),
        "hard_constraints_relaxed": False,
        "fallback_used": False,
        "selection_uses_test": False,
    }
    if maximum > KKT_LIMIT_V539:
        raise RuntimeError("v539_complete_kkt_certificate_failed")
    return certificate


def _audit(
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
        "turnover": max(turnover - max_turnover, 0.0),
    }
    report: dict[str, Any] = {
        "sum_weights": float(weights.sum()),
        "one_way_turnover": turnover,
        "turnover_slack": max_turnover - turnover,
    }
    if benchmark is not None:
        active = weights - benchmark
        active_share = 0.5 * float(np.abs(active).sum())
        tracking = math.sqrt(max(12.0 * float(active @ covariance @ active), 0.0))
        violations["active_share"] = max(active_share - float(max_active_share), 0.0)
        violations["annual_tracking_error"] = max(
            tracking - float(max_tracking_error), 0.0
        )
        report.update(
            {
                "active_share": active_share,
                "annual_tracking_error": tracking,
                "active_share_slack": float(max_active_share) - active_share,
                "tracking_error_slack": float(max_tracking_error) - tracking,
            }
        )
    report["violations"] = violations
    report["max_violation"] = max(violations.values(), default=0.0)
    return report


def optimize_relative_v539(
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
    sigma, sigma_diagnostics = _psd_matrix(covariance, "covariance", size)
    mean_covariance, mean_covariance_diagnostics = _psd_matrix(
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
        raise ValueError("v539_relative_costs_negative")
    if abs(float(benchmark.sum()) - 1.0) > 1.0e-8 or abs(float(previous.sum()) - 1.0) > 1.0e-8:
        raise ValueError("v539_relative_weight_vectors_must_sum_to_one")
    _bounds(lower, upper)

    weights = cp.Variable(size, name="weights")
    active_abs = cp.Variable(size, name="active_abs")
    change_abs = cp.Variable(size, name="change_abs")
    active = weights - benchmark
    change = weights - previous
    equality = cp.sum(weights) == 1.0
    lower_c = weights >= lower
    upper_c = weights <= upper
    active_pos_c = active <= active_abs
    active_neg_c = -active <= active_abs
    active_nonnegative_c = active_abs >= 0.0
    change_pos_c = change <= change_abs
    change_neg_c = -change <= change_abs
    change_nonnegative_c = change_abs >= 0.0
    active_share_c = 0.5 * cp.sum(active_abs) <= max_active_share
    tracking_c = 12.0 * cp.quad_form(active, sigma) <= max_annual_tracking_error**2
    turnover_c = 0.5 * cp.sum(change_abs) <= max_one_way_turnover
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
        lower_c,
        upper_c,
        active_pos_c,
        active_neg_c,
        active_nonnegative_c,
        change_pos_c,
        change_neg_c,
        change_nonnegative_c,
        active_share_c,
        tracking_c,
        turnover_c,
    ]
    problem = cp.Problem(objective, constraints)
    if not problem.is_dcp():
        raise RuntimeError("v539_relative_problem_not_dcp")
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status != cp.OPTIMAL or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {"status": str(problem.status), "fallback_used": False},
        }
    w = np.asarray(weights.value, dtype=float)
    u = np.asarray(active_abs.value, dtype=float)
    v = np.asarray(change_abs.value, dtype=float)
    a = w - benchmark
    d = w - previous
    audit = _audit(
        w,
        previous,
        sigma,
        lower,
        upper,
        benchmark=benchmark,
        max_active_share=max_active_share,
        max_tracking_error=max_annual_tracking_error,
        max_turnover=max_one_way_turnover,
    )
    dual = {
        "lower": _dual_vector(lower_c, size),
        "upper": _dual_vector(upper_c, size),
        "active_positive": _dual_vector(active_pos_c, size),
        "active_negative": _dual_vector(active_neg_c, size),
        "active_abs_nonnegative": _dual_vector(active_nonnegative_c, size),
        "change_positive": _dual_vector(change_pos_c, size),
        "change_negative": _dual_vector(change_neg_c, size),
        "change_abs_nonnegative": _dual_vector(change_nonnegative_c, size),
        "active_share": _dual_scalar(active_share_c),
        "tracking_error_squared": _dual_scalar(tracking_c),
        "turnover": _dual_scalar(turnover_c),
    }
    equality_dual = _dual_scalar(equality)
    gradient = (
        -expected
        + active_risk_aversion * (sigma @ a)
        + 2.0 * uncertainty_penalty * (mean_covariance @ a)
        + 2.0 * active_l2_penalty * a
        + quadratic * d
    )
    stationarity_w = (
        gradient
        + equality_dual
        - dual["lower"]
        + dual["upper"]
        + dual["active_positive"]
        - dual["active_negative"]
        + dual["change_positive"]
        - dual["change_negative"]
        + dual["tracking_error_squared"] * 24.0 * (sigma @ a)
    )
    stationarity_aux = {
        "active_abs": -dual["active_positive"]
        - dual["active_negative"]
        - dual["active_abs_nonnegative"]
        + 0.5 * dual["active_share"],
        "change_abs": linear
        - dual["change_positive"]
        - dual["change_negative"]
        - dual["change_abs_nonnegative"]
        + 0.5 * dual["turnover"],
    }
    inequalities = [
        _constraint_entry("lower", dual["lower"], w - lower),
        _constraint_entry("upper", dual["upper"], upper - w),
        _constraint_entry("active_positive", dual["active_positive"], u - a),
        _constraint_entry("active_negative", dual["active_negative"], u + a),
        _constraint_entry(
            "active_abs_nonnegative", dual["active_abs_nonnegative"], u
        ),
        _constraint_entry("change_positive", dual["change_positive"], v - d),
        _constraint_entry("change_negative", dual["change_negative"], v + d),
        _constraint_entry(
            "change_abs_nonnegative", dual["change_abs_nonnegative"], v
        ),
        _constraint_entry(
            "active_share",
            dual["active_share"],
            max_active_share - 0.5 * float(u.sum()),
        ),
        _constraint_entry(
            "tracking_error_squared",
            dual["tracking_error_squared"],
            max_annual_tracking_error**2 - 12.0 * float(a @ sigma @ a),
        ),
        _constraint_entry(
            "turnover",
            dual["turnover"],
            max_one_way_turnover - 0.5 * float(v.sum()),
        ),
    ]
    certificate = _assemble_certificate(
        problem,
        equality_residual=float(w.sum()) - 1.0,
        equality_dual=equality_dual,
        stationarity_weights=stationarity_w,
        stationarity_auxiliary=stationarity_aux,
        inequalities=inequalities,
    )
    raw_cost = float(linear @ v + 0.5 * quadratic @ (d**2))
    return {
        "status": "optimal",
        "weights": w.tolist(),
        "active_weights": a.tolist(),
        "objective_terms": {
            "active_expected_return": float(expected @ a),
            "active_risk_penalty": 0.5 * active_risk_aversion * float(a @ sigma @ a),
            "posterior_uncertainty_penalty": uncertainty_penalty
            * float(a @ mean_covariance @ a),
            "active_l2_penalty": active_l2_penalty * float(a @ a),
            "raw_expected_transaction_cost": raw_cost,
            "penalized_transaction_cost": raw_cost,
            "minimization_objective": float(problem.value),
        },
        "constraints": audit,
        "solver": certificate,
        "matrix_diagnostics": {
            "covariance": sigma_diagnostics,
            "posterior_mean_covariance": mean_covariance_diagnostics,
        },
    }


def optimize_absolute_v539(
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
    sigma, sigma_diagnostics = _psd_matrix(covariance, "absolute_covariance", size)
    mean_covariance, mean_covariance_diagnostics = _psd_matrix(
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
        raise ValueError("v539_absolute_costs_negative")
    if abs(float(anchor.sum()) - 1.0) > 1.0e-8 or abs(float(previous.sum()) - 1.0) > 1.0e-8:
        raise ValueError("v539_absolute_weight_vectors_must_sum_to_one")
    _bounds(lower, upper)

    weights = cp.Variable(size, name="weights")
    change_abs = cp.Variable(size, name="change_abs")
    change = weights - previous
    distance = weights - anchor
    equality = cp.sum(weights) == 1.0
    lower_c = weights >= lower
    upper_c = weights <= upper
    change_pos_c = change <= change_abs
    change_neg_c = -change <= change_abs
    change_nonnegative_c = change_abs >= 0.0
    turnover_c = 0.5 * cp.sum(change_abs) <= max_one_way_turnover
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
            lower_c,
            upper_c,
            change_pos_c,
            change_neg_c,
            change_nonnegative_c,
            turnover_c,
        ],
    )
    if not problem.is_dcp():
        raise RuntimeError("v539_absolute_problem_not_dcp")
    problem.solve(solver="CLARABEL", verbose=False)
    if problem.status != cp.OPTIMAL or weights.value is None:
        return {
            "status": "infeasible_or_solver_failed",
            "weights": None,
            "solver": {"status": str(problem.status), "fallback_used": False},
        }
    w = np.asarray(weights.value, dtype=float)
    v = np.asarray(change_abs.value, dtype=float)
    d = w - previous
    distance_value = w - anchor
    audit = _audit(
        w,
        previous,
        sigma,
        lower,
        upper,
        benchmark=None,
        max_active_share=None,
        max_tracking_error=None,
        max_turnover=max_one_way_turnover,
    )
    dual = {
        "lower": _dual_vector(lower_c, size),
        "upper": _dual_vector(upper_c, size),
        "change_positive": _dual_vector(change_pos_c, size),
        "change_negative": _dual_vector(change_neg_c, size),
        "change_abs_nonnegative": _dual_vector(change_nonnegative_c, size),
        "turnover": _dual_scalar(turnover_c),
    }
    equality_dual = _dual_scalar(equality)
    gradient = (
        -expected
        + risk_aversion * (sigma @ w)
        + 2.0 * uncertainty_penalty * (mean_covariance @ w)
        + 2.0 * anchor_penalty * (sigma @ distance_value)
        + quadratic * d
    )
    stationarity_w = (
        gradient
        + equality_dual
        - dual["lower"]
        + dual["upper"]
        + dual["change_positive"]
        - dual["change_negative"]
    )
    stationarity_aux = {
        "change_abs": linear
        - dual["change_positive"]
        - dual["change_negative"]
        - dual["change_abs_nonnegative"]
        + 0.5 * dual["turnover"]
    }
    inequalities = [
        _constraint_entry("lower", dual["lower"], w - lower),
        _constraint_entry("upper", dual["upper"], upper - w),
        _constraint_entry("change_positive", dual["change_positive"], v - d),
        _constraint_entry("change_negative", dual["change_negative"], v + d),
        _constraint_entry(
            "change_abs_nonnegative", dual["change_abs_nonnegative"], v
        ),
        _constraint_entry(
            "turnover",
            dual["turnover"],
            max_one_way_turnover - 0.5 * float(v.sum()),
        ),
    ]
    certificate = _assemble_certificate(
        problem,
        equality_residual=float(w.sum()) - 1.0,
        equality_dual=equality_dual,
        stationarity_weights=stationarity_w,
        stationarity_auxiliary=stationarity_aux,
        inequalities=inequalities,
    )
    raw_cost = float(linear @ v + 0.5 * quadratic @ (d**2))
    return {
        "status": "optimal",
        "weights": w.tolist(),
        "objective_terms": {
            "expected_return": float(expected @ w),
            "risk_penalty": 0.5 * risk_aversion * float(w @ sigma @ w),
            "posterior_uncertainty_penalty": uncertainty_penalty
            * float(w @ mean_covariance @ w),
            "anchor_penalty": anchor_penalty
            * float(distance_value @ sigma @ distance_value),
            "raw_expected_transaction_cost": raw_cost,
            "penalized_transaction_cost": raw_cost,
            "minimization_objective": float(problem.value),
        },
        "constraints": audit,
        "solver": certificate,
        "matrix_diagnostics": {
            "covariance": sigma_diagnostics,
            "posterior_mean_covariance": mean_covariance_diagnostics,
        },
    }


__all__ = [
    "CLARABEL_VERSION_V539",
    "CVXPY_VERSION_V539",
    "KKT_LIMIT_V539",
    "optimize_absolute_v539",
    "optimize_relative_v539",
]
