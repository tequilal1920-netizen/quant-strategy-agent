"""Direct active-weight optimizer for the governed four-asset v5.3 research.

The relative model optimises the active vector ``a = w - b`` directly around
the declared policy benchmark ``b``.  This avoids the v5.2 pattern of solving an
absolute portfolio first and only then shrinking it toward policy.  The module
does not fetch data or choose parameters and can therefore be tested with
synthetic inputs without exposing the retrospective holdout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize


_EPS = 1.0e-12


@dataclass(frozen=True)
class ActiveOptimizerResultV53:
    weights: np.ndarray
    active_weights: np.ndarray
    status: str
    objective_terms: Mapping[str, float]
    constraints: Mapping[str, Any]
    turnover: float
    expected_cost: float
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def value(item: Any) -> Any:
            if isinstance(item, np.ndarray):
                return item.tolist()
            if isinstance(item, (np.floating, np.integer)):
                return item.item()
            if isinstance(item, Mapping):
                return {str(key): value(inner) for key, inner in item.items()}
            if isinstance(item, (list, tuple)):
                return [value(inner) for inner in item]
            return item

        return value(
            {
                "weights": self.weights,
                "active_weights": self.active_weights,
                "status": self.status,
                "objective_terms": self.objective_terms,
                "constraints": self.constraints,
                "turnover": self.turnover,
                "expected_cost": self.expected_cost,
                "diagnostics": self.diagnostics,
            }
        )


def _vector(values: Sequence[float] | np.ndarray, name: str, size: int | None = None) -> np.ndarray:
    output = np.asarray(values, dtype=float)
    if output.ndim != 1 or (size is not None and output.size != size):
        raise ValueError(f"{name}_shape_invalid")
    if not np.all(np.isfinite(output)):
        raise ValueError(f"{name}_nonfinite")
    return output


def _covariance(values: Sequence[Sequence[float]] | np.ndarray, size: int) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.shape != (size, size) or not np.all(np.isfinite(matrix)):
        raise ValueError("active_covariance_invalid")
    matrix = (matrix + matrix.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    floor = max(float(np.median(np.maximum(np.diag(matrix), 0.0))) * 1.0e-10, _EPS)
    return eigenvectors @ np.diag(np.maximum(eigenvalues, floor)) @ eigenvectors.T


def _audit(
    weights: np.ndarray,
    benchmark: np.ndarray,
    previous: np.ndarray,
    covariance: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    max_active_share: float,
    max_tracking_error: float,
    max_turnover: float,
) -> dict[str, Any]:
    active = weights - benchmark
    active_share = 0.5 * float(np.abs(active).sum())
    turnover = 0.5 * float(np.abs(weights - previous).sum())
    tracking_error = math.sqrt(max(12.0 * float(active @ covariance @ active), 0.0))
    violations = {
        "sum": abs(float(weights.sum()) - 1.0),
        "lower": float(np.max(np.maximum(lower - weights, 0.0))),
        "upper": float(np.max(np.maximum(weights - upper, 0.0))),
        "active_share": max(active_share - max_active_share, 0.0),
        "annual_tracking_error": max(tracking_error - max_tracking_error, 0.0),
        "turnover": max(turnover - max_turnover, 0.0),
    }
    return {
        "active_share": active_share,
        "annual_tracking_error": tracking_error,
        "turnover": turnover,
        "active_share_slack": max_active_share - active_share,
        "tracking_error_slack": max_tracking_error - tracking_error,
        "turnover_slack": max_turnover - turnover,
        "lower_slack": weights - lower,
        "upper_slack": upper - weights,
        "violations": violations,
        "max_violation": max(violations.values()),
    }


def optimize_policy_relative_v53(
    active_expected_return: Sequence[float] | np.ndarray,
    covariance: Sequence[Sequence[float]] | np.ndarray,
    benchmark_weights: Sequence[float] | np.ndarray,
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
    active_l2_penalty: float,
    cost_multiplier: float = 1.0,
    l1_smoothing: float = 1.0e-8,
    max_iterations: int = 1500,
    solver_tolerance: float = 1.0e-11,
) -> ActiveOptimizerResultV53:
    """Maximise net active utility subject to policy-relative hard constraints.

    The minimised objective is

    ``-alpha' a + gamma/2 a'Sigma a + lambda a'a + costs(w-w_drift)``.
    """

    benchmark = _vector(benchmark_weights, "benchmark")
    size = benchmark.size
    expected = _vector(active_expected_return, "active_expected_return", size)
    previous = _vector(previous_weights, "previous_weights", size)
    lower = _vector(lower_bounds, "lower_bounds", size)
    upper = _vector(upper_bounds, "upper_bounds", size)
    linear = _vector(linear_cost, "linear_cost", size)
    quadratic = _vector(quadratic_cost, "quadratic_cost", size)
    matrix = _covariance(covariance, size)
    if abs(float(benchmark.sum()) - 1.0) > 1.0e-10 or abs(float(previous.sum()) - 1.0) > 1.0e-8:
        raise ValueError("active_benchmark_and_previous_must_sum_to_one")
    if np.any(benchmark < lower) or np.any(benchmark > upper):
        raise ValueError("active_benchmark_outside_bounds")
    if np.any(lower < 0.0) or np.any(lower > upper) or lower.sum() > 1.0 or upper.sum() < 1.0:
        raise ValueError("active_bounds_infeasible")
    if min(max_active_share, max_annual_tracking_error) <= 0.0:
        raise ValueError("active_caps_must_be_positive")
    if not 0.0 <= max_one_way_turnover <= 1.0:
        raise ValueError("active_turnover_cap_invalid")
    if min(active_risk_aversion, active_l2_penalty, cost_multiplier) < 0.0:
        raise ValueError("active_penalties_must_be_nonnegative")
    if np.any(linear < 0.0) or np.any(quadratic < 0.0) or l1_smoothing <= 0.0:
        raise ValueError("active_costs_invalid")

    def terms(weights: np.ndarray) -> dict[str, float]:
        active = weights - benchmark
        change = weights - previous
        smooth_abs = np.sqrt(change * change + l1_smoothing * l1_smoothing) - l1_smoothing
        return {
            "active_expected_return": float(expected @ active),
            "active_risk_penalty": 0.5
            * active_risk_aversion
            * float(active @ matrix @ active),
            "active_l2_penalty": active_l2_penalty * float(active @ active),
            "transaction_cost": cost_multiplier
            * float(linear @ smooth_abs + 0.5 * quadratic @ (change * change)),
        }

    def objective(weights: np.ndarray) -> float:
        payload = terms(weights)
        return (
            -payload["active_expected_return"]
            + payload["active_risk_penalty"]
            + payload["active_l2_penalty"]
            + payload["transaction_cost"]
        )

    def gradient(weights: np.ndarray) -> np.ndarray:
        active = weights - benchmark
        change = weights - previous
        return (
            -expected
            + active_risk_aversion * (matrix @ active)
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
        {
            "type": "ineq",
            "fun": lambda w: max_active_share - 0.5 * float(np.abs(w - benchmark).sum()),
        },
        {
            "type": "ineq",
            "fun": lambda w: tracking_variance_cap
            - 12.0 * float((w - benchmark) @ matrix @ (w - benchmark)),
            "jac": lambda w: -24.0 * (matrix @ (w - benchmark)),
        },
        {
            "type": "ineq",
            "fun": lambda w: max_one_way_turnover - 0.5 * float(np.abs(w - previous).sum()),
        },
    ]
    seeds: list[np.ndarray] = []
    for seed in (previous, benchmark, 0.5 * (previous + benchmark)):
        clipped = np.minimum(np.maximum(seed, lower), upper)
        clipped += (1.0 - clipped.sum()) / size
        clipped = np.minimum(np.maximum(clipped, lower), upper)
        clipped = benchmark + 0.999 * (clipped - benchmark)
        if not any(np.allclose(clipped, old) for old in seeds):
            seeds.append(clipped)

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
        candidate_objective = float(objective(weights))
        attempts.append(
            {
                "success": bool(result.success),
                "message": str(result.message),
                "iterations": int(getattr(result, "nit", 0) or 0),
                "objective": candidate_objective,
                "max_violation": float(audit["max_violation"]),
            }
        )
        if bool(result.success) and float(audit["max_violation"]) <= 1.0e-7:
            if best is None or candidate_objective < best[0]:
                best = (candidate_objective, weights, result, audit)

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
    payload = terms(weights)
    payload["minimization_objective"] = objective_value
    exact_change = weights - previous
    exact_cost = float(linear @ np.abs(exact_change) + 0.5 * quadratic @ (exact_change * exact_change))
    multipliers = getattr(result, "multipliers", None) if result is not None else None
    return ActiveOptimizerResultV53(
        weights=weights,
        active_weights=weights - benchmark,
        status=status,
        objective_terms=payload,
        constraints=audit,
        turnover=0.5 * float(np.abs(exact_change).sum()),
        expected_cost=exact_cost,
        diagnostics={
            "solver": "SCIPY_SLSQP",
            "attempts": attempts,
            "fallback_level": fallback_level,
            "hard_constraints_relaxed": False,
            "selection_uses_test": False,
            "multipliers": None if multipliers is None else np.ravel(multipliers),
        },
    )


__all__ = ["ActiveOptimizerResultV53", "optimize_policy_relative_v53"]
