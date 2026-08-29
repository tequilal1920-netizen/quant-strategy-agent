"""Numerical building blocks for the four-asset allocation research engine v5.

The module is deliberately independent from ``asset_allocation_engine`` so it
can be exercised in isolation while v4 remains the production implementation.
All returns and covariance matrices use one common period (normally monthly);
annualisation is applied only to explicit constraints and reporting.

The functions below do not fetch data, select factors, or tune parameters.  A
caller must freeze those choices on its training sample before invoking these
primitives.  In particular, ``macro_blend_weight``, risk budgets, Black-
Litterman parameters, costs, and constraints are explicit inputs rather than
backtest-selected defaults hidden inside the solvers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

try:  # SciPy is already a repository dependency; keep import failure explicit.
    from scipy.optimize import minimize
except ImportError:  # pragma: no cover - exercised only in a broken runtime.
    minimize = None  # type: ignore[assignment]


_EPSILON = 1.0e-12


def _json_value(value: Any) -> Any:
    """Convert numpy-rich diagnostics to snapshot-safe Python values."""

    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class CovarianceBundleV5:
    """Auditable decomposition of the covariance used by allocation solvers."""

    covariance: np.ndarray
    factor_loadings: np.ndarray
    factor_covariance: np.ndarray
    specific_covariance: np.ndarray
    statistical_covariance: np.ndarray
    macro_blend_weight: float
    factor_names: tuple[str, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "covariance": self.covariance.tolist(),
            "factor_loadings": self.factor_loadings.tolist(),
            "factor_covariance": self.factor_covariance.tolist(),
            "specific_covariance": self.specific_covariance.tolist(),
            "statistical_covariance": self.statistical_covariance.tolist(),
            "macro_blend_weight": float(self.macro_blend_weight),
            "factor_names": list(self.factor_names),
            "diagnostics": _json_value(self.diagnostics),
        }


@dataclass(frozen=True)
class RiskBudgetResultV5:
    """Weights and numerical evidence for ERC or constrained risk budgeting."""

    weights: np.ndarray
    target_budget: np.ndarray
    relative_risk_contribution: np.ndarray
    budget_error: np.ndarray
    kkt_residual: float
    active_constraints: tuple[str, ...]
    shadow_prices: dict[str, float]
    status: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": self.weights.tolist(),
            "target_budget": self.target_budget.tolist(),
            "relative_risk_contribution": self.relative_risk_contribution.tolist(),
            "budget_error": self.budget_error.tolist(),
            "kkt_residual": float(self.kkt_residual),
            "active_constraints": list(self.active_constraints),
            "shadow_prices": _json_value(self.shadow_prices),
            "status": self.status,
            "diagnostics": _json_value(self.diagnostics),
        }


@dataclass(frozen=True)
class BlackLittermanResultV5:
    """Complete Black-Litterman prior, views, and posterior state."""

    prior_weights: np.ndarray
    pi: np.ndarray
    delta: float
    tau: float
    P: np.ndarray
    q: np.ndarray
    omega: np.ndarray
    posterior_mean: np.ndarray
    posterior_mean_covariance: np.ndarray
    predictive_covariance: np.ndarray
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prior_weights": self.prior_weights.tolist(),
            "pi": self.pi.tolist(),
            "delta": float(self.delta),
            "tau": float(self.tau),
            "P": self.P.tolist(),
            "q": self.q.tolist(),
            "omega": self.omega.tolist(),
            "posterior_mean": self.posterior_mean.tolist(),
            "posterior_mean_covariance": self.posterior_mean_covariance.tolist(),
            "predictive_covariance": self.predictive_covariance.tolist(),
            "diagnostics": _json_value(self.diagnostics),
        }


@dataclass(frozen=True)
class OptimizerResultV5:
    """Final robust allocation and all solver/fallback diagnostics."""

    weights: np.ndarray
    status: str
    objective_terms: dict[str, float]
    constraint_slack: dict[str, Any]
    shadow_prices: dict[str, Any]
    turnover: float
    expected_cost: float
    fallback_level: int
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": self.weights.tolist(),
            "status": self.status,
            "objective_terms": _json_value(self.objective_terms),
            "constraint_slack": _json_value(self.constraint_slack),
            "shadow_prices": _json_value(self.shadow_prices),
            "turnover": float(self.turnover),
            "expected_cost": float(self.expected_cost),
            "fallback_level": int(self.fallback_level),
            "diagnostics": _json_value(self.diagnostics),
        }


def _finite_vector(values: Sequence[float] | np.ndarray, name: str) -> np.ndarray:
    vector = np.asarray(values, dtype=float)
    if vector.ndim != 1 or vector.size == 0:
        raise ValueError(f"{name}_must_be_nonempty_vector")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name}_must_be_finite")
    return vector


def _finite_matrix(values: Sequence[Sequence[float]] | np.ndarray, name: str) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError(f"{name}_must_be_nonempty_matrix")
    if not np.all(np.isfinite(matrix)):
        raise ValueError(f"{name}_must_be_finite")
    return matrix


def _square_matrix(values: Sequence[Sequence[float]] | np.ndarray, name: str) -> np.ndarray:
    matrix = _finite_matrix(values, name)
    if matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"{name}_must_be_square")
    return matrix


def nearest_positive_semidefinite_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    *,
    relative_floor: float = 1.0e-10,
) -> tuple[np.ndarray, dict[str, float]]:
    """Symmetrise and apply a scale-aware eigenvalue floor.

    The projection is intentionally reported.  Callers can reject a matrix
    whose repair norm is too large rather than silently accepting bad data.
    """

    matrix = _square_matrix(covariance, "covariance")
    symmetric = (matrix + matrix.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    diagonal_scale = max(float(np.median(np.maximum(np.diag(symmetric), 0.0))), _EPSILON)
    floor = max(float(relative_floor) * diagonal_scale, _EPSILON)
    repaired_values = np.maximum(eigenvalues, floor)
    repaired = (eigenvectors * repaired_values) @ eigenvectors.T
    repaired = (repaired + repaired.T) / 2.0
    norm = float(np.linalg.norm(repaired - symmetric, ord="fro"))
    base = max(float(np.linalg.norm(symmetric, ord="fro")), _EPSILON)
    return repaired, {
        "minimum_eigenvalue_before": float(eigenvalues.min()),
        "minimum_eigenvalue_after": float(repaired_values.min()),
        "relative_repair_norm": norm / base,
        "condition_number": float(repaired_values.max() / repaired_values.min()),
    }


def _exponential_weights(length: int, half_life: float | None) -> np.ndarray:
    if length <= 0:
        raise ValueError("weight_length_must_be_positive")
    if half_life is None:
        return np.full(length, 1.0 / length)
    if not math.isfinite(float(half_life)) or float(half_life) <= 0:
        raise ValueError("half_life_must_be_positive")
    age = np.arange(length - 1, -1, -1, dtype=float)
    weight = np.exp(-math.log(2.0) * age / float(half_life))
    return weight / weight.sum()


def _weighted_covariance(data: np.ndarray, weights: np.ndarray) -> np.ndarray:
    mean = np.average(data, axis=0, weights=weights)
    centered = data - mean
    covariance = (centered * weights[:, None]).T @ centered
    effective_denominator = 1.0 - float(weights @ weights)
    if effective_denominator <= 1.0e-10:
        raise ValueError("effective_covariance_sample_too_small")
    return covariance / effective_denominator


def estimate_statistical_covariance_v5(
    returns: Sequence[Sequence[float]] | np.ndarray,
    *,
    half_life: float | None = None,
    diagonal_shrinkage: float = 0.35,
    relative_eigenvalue_floor: float = 1.0e-10,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Estimate a causal weighted covariance with diagonal shrinkage."""

    data = _finite_matrix(returns, "returns")
    if data.shape[0] < 3:
        raise ValueError("insufficient_statistical_covariance_history")
    if not 0.0 <= float(diagonal_shrinkage) <= 1.0:
        raise ValueError("diagonal_shrinkage_must_be_between_zero_and_one")
    weights = _exponential_weights(len(data), half_life)
    raw = _weighted_covariance(data, weights)
    diagonal = np.diag(np.maximum(np.diag(raw), _EPSILON))
    shrunk = (1.0 - float(diagonal_shrinkage)) * raw + float(diagonal_shrinkage) * diagonal
    covariance, projection = nearest_positive_semidefinite_v5(
        shrunk, relative_floor=relative_eigenvalue_floor
    )
    effective_observations = 1.0 / float(weights @ weights)
    return covariance, {
        "observations": int(data.shape[0]),
        "assets": int(data.shape[1]),
        "half_life": None if half_life is None else float(half_life),
        "diagonal_shrinkage": float(diagonal_shrinkage),
        "effective_observations": effective_observations,
        "projection": projection,
    }


def fit_macro_factor_covariance_v5(
    asset_returns: Sequence[Sequence[float]] | np.ndarray,
    macro_innovations: Sequence[Sequence[float]] | np.ndarray,
    *,
    macro_blend_weight: float,
    factor_names: Sequence[str] | None = None,
    ridge_penalty: float = 0.15,
    statistical_half_life: float | None = None,
    factor_half_life: float | None = None,
    diagonal_shrinkage: float = 0.35,
    min_observations: int = 24,
    relative_eigenvalue_floor: float = 1.0e-10,
) -> CovarianceBundleV5:
    """Fit ``Sigma = rho (B F B' + D) + (1-rho) Sigma_stat``.

    ``macro_blend_weight`` is supplied by a training-only calibration or an
    approved policy.  This function intentionally never selects it from the
    same observations used to report portfolio performance.
    """

    returns = _finite_matrix(asset_returns, "asset_returns")
    factors = _finite_matrix(macro_innovations, "macro_innovations")
    if returns.shape[0] != factors.shape[0]:
        raise ValueError("asset_and_macro_observations_must_align")
    if not 0.0 <= float(macro_blend_weight) <= 1.0:
        raise ValueError("macro_blend_weight_must_be_between_zero_and_one")
    if ridge_penalty < 0 or not math.isfinite(float(ridge_penalty)):
        raise ValueError("ridge_penalty_must_be_nonnegative")
    names = tuple(str(name) for name in (factor_names or [f"factor_{i + 1}" for i in range(factors.shape[1])]))
    if len(names) != factors.shape[1] or len(set(names)) != len(names):
        raise ValueError("factor_names_must_be_unique_and_align")

    statistical, statistical_diagnostics = estimate_statistical_covariance_v5(
        returns,
        half_life=statistical_half_life,
        diagonal_shrinkage=diagonal_shrinkage,
        relative_eigenvalue_floor=relative_eigenvalue_floor,
    )
    asset_count, factor_count = returns.shape[1], factors.shape[1]
    required = max(int(min_observations), factor_count + 3)
    if len(returns) < required:
        zeros = np.zeros((asset_count, factor_count))
        return CovarianceBundleV5(
            covariance=statistical,
            factor_loadings=zeros,
            factor_covariance=np.zeros((factor_count, factor_count)),
            specific_covariance=statistical.copy(),
            statistical_covariance=statistical,
            macro_blend_weight=0.0,
            factor_names=names,
            diagnostics={
                "status": "fallback_statistical_covariance",
                "reason": "insufficient_macro_history",
                "observations": int(len(returns)),
                "required_observations": required,
                "statistical": statistical_diagnostics,
            },
        )

    centered_factors = factors - factors.mean(axis=0, keepdims=True)
    design = np.column_stack([np.ones(len(centered_factors)), centered_factors])
    penalty = np.diag([0.0] + [float(ridge_penalty)] * factor_count)
    system = design.T @ design + penalty
    try:
        coefficients = np.linalg.solve(system, design.T @ returns)
    except np.linalg.LinAlgError as error:
        zeros = np.zeros((asset_count, factor_count))
        return CovarianceBundleV5(
            covariance=statistical,
            factor_loadings=zeros,
            factor_covariance=np.zeros((factor_count, factor_count)),
            specific_covariance=statistical.copy(),
            statistical_covariance=statistical,
            macro_blend_weight=0.0,
            factor_names=names,
            diagnostics={
                "status": "fallback_statistical_covariance",
                "reason": f"factor_regression_singular:{type(error).__name__}",
                "observations": int(len(returns)),
                "statistical": statistical_diagnostics,
            },
        )

    loadings = coefficients[1:].T
    residuals = returns - design @ coefficients
    factor_covariance, factor_diagnostics = estimate_statistical_covariance_v5(
        centered_factors,
        half_life=factor_half_life,
        diagonal_shrinkage=diagonal_shrinkage,
        relative_eigenvalue_floor=relative_eigenvalue_floor,
    )
    residual_variance = np.var(residuals, axis=0, ddof=max(1, factor_count + 1))
    residual_variance = np.maximum(residual_variance, _EPSILON)
    specific = np.diag(residual_variance)
    macro_raw = loadings @ factor_covariance @ loadings.T + specific
    macro_covariance, macro_projection = nearest_positive_semidefinite_v5(
        macro_raw, relative_floor=relative_eigenvalue_floor
    )
    rho = float(macro_blend_weight)
    blended_raw = rho * macro_covariance + (1.0 - rho) * statistical
    blended, blended_projection = nearest_positive_semidefinite_v5(
        blended_raw, relative_floor=relative_eigenvalue_floor
    )
    systematic_variance = float(np.trace(loadings @ factor_covariance @ loadings.T))
    specific_variance = float(np.trace(specific))
    return CovarianceBundleV5(
        covariance=blended,
        factor_loadings=loadings,
        factor_covariance=factor_covariance,
        specific_covariance=specific,
        statistical_covariance=statistical,
        macro_blend_weight=rho,
        factor_names=names,
        diagnostics={
            "status": "ok",
            "observations": int(len(returns)),
            "factor_count": factor_count,
            "ridge_penalty": float(ridge_penalty),
            "system_condition_number": float(np.linalg.cond(system)),
            "systematic_trace_share": systematic_variance
            / max(systematic_variance + specific_variance, _EPSILON),
            "statistical": statistical_diagnostics,
            "factor": factor_diagnostics,
            "macro_projection": macro_projection,
            "blended_projection": blended_projection,
        },
    )


def portfolio_risk_contribution_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Return volatility, Euler risk contributions, and relative contributions."""

    matrix = _square_matrix(covariance, "covariance")
    vector = _finite_vector(weights, "weights")
    if matrix.shape[0] != vector.size:
        raise ValueError("weights_and_covariance_must_align")
    variance = float(vector @ matrix @ vector)
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError("portfolio_variance_must_be_positive")
    volatility = math.sqrt(variance)
    contribution = vector * (matrix @ vector) / volatility
    relative = contribution / volatility
    return volatility, contribution, relative


def _risk_budget_objective(x: np.ndarray, covariance: np.ndarray, budgets: np.ndarray) -> float:
    return 0.5 * float(x @ covariance @ x) - float(budgets @ np.log(x))


def _solve_unconstrained_risk_budget(
    covariance: np.ndarray,
    budgets: np.ndarray,
    tolerance: float,
    max_iterations: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Newton solve of ``0.5 x'Sigma x - b'log(x)`` before normalisation."""

    diagonal = np.maximum(np.diag(covariance), _EPSILON)
    x = np.sqrt(np.maximum(budgets, _EPSILON) / diagonal)
    x = np.maximum(x, 1.0e-8)
    objective = _risk_budget_objective(x, covariance, budgets)
    converged = False
    gradient_norm = math.inf
    for iteration in range(1, int(max_iterations) + 1):
        gradient = covariance @ x - budgets / x
        gradient_norm = float(np.max(np.abs(gradient)))
        if gradient_norm <= tolerance:
            converged = True
            break
        hessian = covariance + np.diag(budgets / (x * x))
        try:
            direction = -np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as error:
            raise RuntimeError("risk_budget_newton_singular") from error
        negative = direction < 0
        maximum_step = 1.0
        if np.any(negative):
            maximum_step = min(maximum_step, 0.99 * float(np.min(-x[negative] / direction[negative])))
        step = maximum_step
        directional_derivative = float(gradient @ direction)
        accepted = False
        while step >= 1.0e-12:
            candidate = x + step * direction
            if np.all(candidate > 0):
                candidate_objective = _risk_budget_objective(candidate, covariance, budgets)
                if candidate_objective <= objective + 1.0e-4 * step * directional_derivative:
                    x, objective, accepted = candidate, candidate_objective, True
                    break
            step *= 0.5
        if not accepted:
            raise RuntimeError("risk_budget_newton_line_search_failed")
    if not converged:
        gradient = covariance @ x - budgets / x
        gradient_norm = float(np.max(np.abs(gradient)))
        converged = gradient_norm <= max(tolerance * 10.0, 1.0e-9)
    if not converged:
        raise RuntimeError("risk_budget_newton_not_converged")
    weights = x / x.sum()
    return weights, {
        "iterations": iteration,
        "gradient_infinity_norm": gradient_norm,
        "barrier_objective": objective,
    }


def _validate_budgets(budgets: Sequence[float] | np.ndarray, size: int) -> np.ndarray:
    vector = _finite_vector(budgets, "budgets")
    if vector.size != size or np.any(vector <= 0):
        raise ValueError("budgets_must_be_strictly_positive_and_align")
    total = float(vector.sum())
    if total <= 0:
        raise ValueError("budget_sum_must_be_positive")
    return vector / total


def solve_erc_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    *,
    tolerance: float = 1.0e-10,
    max_iterations: int = 500,
) -> RiskBudgetResultV5:
    """Solve strict long-only equal risk contribution without post-clipping."""

    repaired, projection = nearest_positive_semidefinite_v5(covariance)
    size = repaired.shape[0]
    budgets = np.full(size, 1.0 / size)
    weights, solver = _solve_unconstrained_risk_budget(
        repaired, budgets, float(tolerance), int(max_iterations)
    )
    _, _, relative = portfolio_risk_contribution_v5(repaired, weights)
    error = relative - budgets
    maximum_error = float(np.max(np.abs(error)))
    status = "optimal" if maximum_error <= max(100.0 * tolerance, 1.0e-8) else "numerical_failure"
    return RiskBudgetResultV5(
        weights=weights,
        target_budget=budgets,
        relative_risk_contribution=relative,
        budget_error=error,
        kkt_residual=float(solver["gradient_infinity_norm"]),
        active_constraints=(),
        shadow_prices={},
        status=status,
        diagnostics={
            **solver,
            "maximum_budget_error": maximum_error,
            "covariance_projection": projection,
            "method": "newton_log_barrier_equal_risk_contribution",
        },
    )


def _constraint_arrays(
    size: int,
    lower_bounds: Sequence[float] | None,
    upper_bounds: Sequence[float] | None,
) -> tuple[np.ndarray, np.ndarray]:
    lower = np.zeros(size) if lower_bounds is None else _finite_vector(lower_bounds, "lower_bounds")
    upper = np.ones(size) if upper_bounds is None else _finite_vector(upper_bounds, "upper_bounds")
    if lower.size != size or upper.size != size:
        raise ValueError("bounds_must_align_with_assets")
    if np.any(lower < 0) or np.any(upper <= 0) or np.any(lower > upper):
        raise ValueError("invalid_weight_bounds")
    if float(lower.sum()) > 1.0 + 1.0e-12 or float(upper.sum()) < 1.0 - 1.0e-12:
        raise ValueError("weight_bounds_make_simplex_infeasible")
    return lower, upper


def _bounded_simplex_projection(values: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> np.ndarray:
    """Euclidean projection onto ``sum(w)=1, lower<=w<=upper``."""

    vector = np.asarray(values, dtype=float)
    if vector.shape != lower.shape:
        raise ValueError("projection_inputs_must_align")
    left = float(np.min(vector - upper)) - 1.0
    right = float(np.max(vector - lower)) + 1.0
    for _ in range(200):
        midpoint = (left + right) / 2.0
        candidate = np.clip(vector - midpoint, lower, upper)
        if candidate.sum() > 1.0:
            left = midpoint
        else:
            right = midpoint
    result = np.clip(vector - (left + right) / 2.0, lower, upper)
    if abs(float(result.sum()) - 1.0) > 1.0e-9:
        raise RuntimeError("bounded_simplex_projection_failed")
    return result


def solve_constrained_risk_budget_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    budgets: Sequence[float] | np.ndarray,
    lower_bounds: Sequence[float] | None = None,
    upper_bounds: Sequence[float] | None = None,
    *,
    previous_weights: Sequence[float] | np.ndarray | None = None,
    turnover_cap: float | None = None,
    tolerance: float = 1.0e-9,
    max_iterations: int = 600,
) -> RiskBudgetResultV5:
    """Solve the Richard--Roncalli constrained log-barrier formulation.

    The inner convex problem minimises ``sigma(x)-lambda*b'log(x)`` inside the
    supplied feasible set.  An outer bisection chooses ``lambda`` so that the
    raw solution sums to one.  No weight is clipped after solving.
    """

    if minimize is None:  # pragma: no cover
        raise RuntimeError("scipy_is_required_for_constrained_risk_budget")
    matrix, projection = nearest_positive_semidefinite_v5(covariance)
    size = matrix.shape[0]
    target = _validate_budgets(budgets, size)
    lower, upper = _constraint_arrays(size, lower_bounds, upper_bounds)
    previous = None
    if previous_weights is not None:
        previous = _finite_vector(previous_weights, "previous_weights")
        if previous.size != size or abs(float(previous.sum()) - 1.0) > 1.0e-8:
            raise ValueError("previous_weights_must_align_and_sum_to_one")
    if turnover_cap is not None:
        if previous is None:
            raise ValueError("turnover_cap_requires_previous_weights")
        if not math.isfinite(float(turnover_cap)) or float(turnover_cap) < 0:
            raise ValueError("turnover_cap_must_be_nonnegative")

    turnover_smoothing = 0.0
    if turnover_cap is not None:
        turnover_smoothing = min(
            1.0e-8,
            max(float(turnover_cap), 1.0e-12) / max(8.0 * size, 1.0),
        )

    def conservative_turnover_slack(x: np.ndarray) -> float:
        if turnover_cap is None or previous is None:
            return math.inf
        difference = x - previous
        smooth_l1 = float(
            np.sqrt(difference * difference + turnover_smoothing * turnover_smoothing).sum()
        )
        return float(turnover_cap) - 0.5 * smooth_l1

    def conservative_turnover_jacobian(x: np.ndarray) -> np.ndarray:
        if previous is None:
            return np.zeros_like(x)
        difference = x - previous
        denominator = np.sqrt(
            difference * difference + turnover_smoothing * turnover_smoothing
        )
        return -0.5 * difference / denominator

    unconstrained_bounds = np.all(lower <= 1.0e-14) and np.all(upper >= 1.0 - 1.0e-14)
    if unconstrained_bounds and turnover_cap is None:
        weights, solver = _solve_unconstrained_risk_budget(
            matrix, target, float(tolerance), int(max_iterations)
        )
        _, _, relative = portfolio_risk_contribution_v5(matrix, weights)
        error = relative - target
        return RiskBudgetResultV5(
            weights=weights,
            target_budget=target,
            relative_risk_contribution=relative,
            budget_error=error,
            kkt_residual=float(solver["gradient_infinity_norm"]),
            active_constraints=(),
            shadow_prices={},
            status="optimal",
            diagnostics={
                **solver,
                "maximum_budget_error": float(np.max(np.abs(error))),
                "covariance_projection": projection,
                "method": "newton_log_barrier_unconstrained_risk_budget",
            },
        )

    seed_base = previous if previous is not None else np.full(size, 1.0 / size)
    seed = _bounded_simplex_projection(seed_base, lower, upper)
    if turnover_cap is not None and 0.5 * float(np.abs(seed - previous).sum()) > float(turnover_cap) + 1.0e-9:
        feasibility = minimize(
            lambda w: float((w - seed) @ (w - seed)),
            seed,
            method="SLSQP",
            bounds=list(zip(lower, upper)),
            constraints=[
                {"type": "eq", "fun": lambda w: float(w.sum() - 1.0)},
                {
                    "type": "ineq",
                    "fun": conservative_turnover_slack,
                    "jac": conservative_turnover_jacobian,
                },
            ],
            options={"maxiter": max_iterations, "ftol": tolerance},
        )
        if not feasibility.success:
            raise ValueError("turnover_and_bounds_make_problem_infeasible")
        seed = np.asarray(feasibility.x, dtype=float)

    inner_seed = np.maximum(seed, np.maximum(lower, 1.0e-10))

    def inner(lambda_value: float, start: np.ndarray) -> tuple[np.ndarray, Any]:
        lam = float(lambda_value)

        def objective(x: np.ndarray) -> float:
            variance = max(float(x @ matrix @ x), _EPSILON)
            return math.sqrt(variance) - lam * float(target @ np.log(x))

        def gradient(x: np.ndarray) -> np.ndarray:
            sigma = math.sqrt(max(float(x @ matrix @ x), _EPSILON))
            return matrix @ x / sigma - lam * target / x

        constraints: list[dict[str, Any]] = []
        if turnover_cap is not None and previous is not None:
            constraints.append(
                {
                    "type": "ineq",
                    "fun": conservative_turnover_slack,
                    "jac": conservative_turnover_jacobian,
                }
            )
        result = minimize(
            objective,
            np.clip(start, np.maximum(lower, 1.0e-10), upper),
            jac=gradient,
            method="SLSQP",
            bounds=list(zip(np.maximum(lower, 1.0e-10), upper)),
            constraints=constraints,
            options={"maxiter": max_iterations, "ftol": min(tolerance, 1.0e-11), "disp": False},
        )
        if not result.success or not np.all(np.isfinite(result.x)):
            raise RuntimeError(f"constrained_risk_budget_inner_failed:{result.message}")
        return np.asarray(result.x, dtype=float), result

    low_lambda = 1.0e-12
    low_x, low_result = inner(low_lambda, inner_seed)
    low_value = float(low_x.sum() - 1.0)
    high_lambda = 1.0
    high_x, high_result = inner(high_lambda, low_x)
    high_value = float(high_x.sum() - 1.0)
    for _ in range(60):
        if low_value <= 0.0 <= high_value:
            break
        if low_value > 0.0:
            low_lambda *= 0.1
            low_x, low_result = inner(low_lambda, low_x)
            low_value = float(low_x.sum() - 1.0)
        else:
            high_lambda *= 10.0
            high_x, high_result = inner(high_lambda, high_x)
            high_value = float(high_x.sum() - 1.0)
    if not (low_value <= 0.0 <= high_value):
        raise RuntimeError("constrained_risk_budget_lambda_not_bracketed")

    solution, result = high_x, high_result
    lambda_value = high_lambda
    root_candidates = [(low_x, low_result), (high_x, high_result)]
    best_root_solution, best_root_result = min(
        root_candidates, key=lambda item: abs(float(item[0].sum()) - 1.0)
    )
    best_root_solution = best_root_solution.copy()
    best_root_error = abs(float(best_root_solution.sum()) - 1.0)
    bisection_iterations = 0
    for bisection_iterations in range(1, 101):
        lambda_value = math.sqrt(low_lambda * high_lambda)
        start = low_x if abs(low_value) < abs(high_value) else high_x
        solution, result = inner(lambda_value, start)
        value = float(solution.sum() - 1.0)
        if abs(value) < best_root_error:
            best_root_solution = solution.copy()
            best_root_result = result
            best_root_error = abs(value)
        if abs(value) <= tolerance:
            break
        if value < 0:
            low_lambda, low_x, low_value = lambda_value, solution, value
        else:
            high_lambda, high_x, high_value = lambda_value, solution, value

    # SLSQP's scalar root can stall a few ulps away from one when a box bound
    # becomes active: normalising that raw vector would then violate the bound.
    # Refine the best root with the simplex imposed inside the solver.  At the
    # true root the equality multiplier is zero, so this is a numerical KKT
    # refinement of the same Richard--Roncalli problem, not post-solve clipping.
    solution = best_root_solution
    result = best_root_result
    root_sum_error = best_root_error
    refinement_start = _bounded_simplex_projection(solution, lower, upper)
    if turnover_cap is not None and previous is not None:
        refinement_turnover = 0.5 * float(np.abs(refinement_start - previous).sum())
        if refinement_turnover > float(turnover_cap) + 1.0e-10:
            refinement_start = seed.copy()

    def refinement_objective(x: np.ndarray) -> float:
        variance = max(float(x @ matrix @ x), _EPSILON)
        return math.sqrt(variance) - lambda_value * float(target @ np.log(x))

    def refinement_gradient(x: np.ndarray) -> np.ndarray:
        sigma = math.sqrt(max(float(x @ matrix @ x), _EPSILON))
        return matrix @ x / sigma - lambda_value * target / x

    refinement_constraints: list[dict[str, Any]] = [
        {"type": "eq", "fun": lambda x: float(x.sum() - 1.0), "jac": lambda x: np.ones(size)}
    ]
    if turnover_cap is not None and previous is not None:
        refinement_constraints.append(
            {
                "type": "ineq",
                "fun": conservative_turnover_slack,
                "jac": conservative_turnover_jacobian,
            }
        )
    refinement = minimize(
        refinement_objective,
        refinement_start,
        jac=refinement_gradient,
        method="SLSQP",
        bounds=list(zip(np.maximum(lower, 1.0e-10), upper)),
        constraints=refinement_constraints,
        options={"maxiter": max_iterations, "ftol": min(tolerance, 1.0e-12), "disp": False},
    )
    if refinement.success and np.all(np.isfinite(refinement.x)):
        solution = np.asarray(refinement.x, dtype=float)
        result = refinement
    elif root_sum_error > max(tolerance * 10.0, 1.0e-7):
        raise RuntimeError(
            f"constrained_risk_budget_normalisation_not_converged:{refinement.message}"
        )
    sum_error = abs(float(solution.sum()) - 1.0)
    if sum_error > max(tolerance * 10.0, 1.0e-7):
        raise RuntimeError("constrained_risk_budget_normalisation_not_converged")
    weights = solution.copy()
    bound_violation = max(
        float(np.max(np.maximum(lower - weights, 0.0))),
        float(np.max(np.maximum(weights - upper, 0.0))),
    )
    turnover = 0.0 if previous is None else 0.5 * float(np.abs(weights - previous).sum())
    turnover_violation = 0.0 if turnover_cap is None else max(turnover - float(turnover_cap), 0.0)
    if max(bound_violation, turnover_violation) > 1.0e-7:
        raise RuntimeError("constrained_risk_budget_returned_infeasible_weights")
    _, _, relative = portfolio_risk_contribution_v5(matrix, weights)
    error = relative - target
    active: list[str] = []
    for index in range(size):
        if weights[index] - lower[index] <= 1.0e-6:
            active.append(f"lower_{index}")
        if upper[index] - weights[index] <= 1.0e-6:
            active.append(f"upper_{index}")
    if turnover_cap is not None and float(turnover_cap) - turnover <= 1.0e-6:
        active.append("turnover")
    sigma = math.sqrt(float(weights @ matrix @ weights))
    stationarity = matrix @ weights / sigma - lambda_value * target / weights
    free = [
        index
        for index in range(size)
        if f"lower_{index}" not in active and f"upper_{index}" not in active
    ]
    equality_multiplier = (
        -float(np.mean(stationarity[free])) if free else -float(np.median(stationarity))
    )
    adjusted_stationarity = stationarity + equality_multiplier
    stationarity_violation = []
    for index, gradient in enumerate(adjusted_stationarity):
        if f"lower_{index}" in active:
            stationarity_violation.append(max(-float(gradient), 0.0))
        elif f"upper_{index}" in active:
            stationarity_violation.append(max(float(gradient), 0.0))
        else:
            stationarity_violation.append(abs(float(gradient)))
    kkt = max(stationarity_violation + [bound_violation, turnover_violation, sum_error])
    maximum_budget_error = float(np.max(np.abs(error)))
    status = "optimal" if maximum_budget_error <= 1.0e-5 else "approximate_constrained"
    multipliers = getattr(result, "multipliers", None)
    shadow_prices: dict[str, float] = {}
    if multipliers is not None:
        shadow_prices = {f"solver_multiplier_{index}": float(value) for index, value in enumerate(np.ravel(multipliers))}
    return RiskBudgetResultV5(
        weights=weights,
        target_budget=target,
        relative_risk_contribution=relative,
        budget_error=error,
        kkt_residual=float(kkt),
        active_constraints=tuple(active),
        shadow_prices=shadow_prices,
        status=status,
        diagnostics={
            "method": "richard_roncalli_constrained_log_barrier_slsqp",
            "lambda": lambda_value,
            "lambda_bisection_iterations": bisection_iterations,
            "root_sum_error_before_simplex_refinement": root_sum_error,
            "simplex_refinement_success": bool(refinement.success),
            "simplex_equality_multiplier": equality_multiplier,
            "inner_iterations": int(getattr(result, "nit", 0) or 0),
            "sum_error": sum_error,
            "turnover": turnover,
            "maximum_budget_error": maximum_budget_error,
            "solver_message": str(result.message),
            "covariance_projection": projection,
        },
    )


def reverse_equilibrium_returns_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    prior_weights: Sequence[float] | np.ndarray,
    delta: float,
) -> np.ndarray:
    matrix = _square_matrix(covariance, "covariance")
    weights = _finite_vector(prior_weights, "prior_weights")
    if weights.size != matrix.shape[0] or abs(float(weights.sum()) - 1.0) > 1.0e-8:
        raise ValueError("prior_weights_must_align_and_sum_to_one")
    if not math.isfinite(float(delta)) or float(delta) <= 0:
        raise ValueError("delta_must_be_positive")
    return float(delta) * (matrix @ weights)


def idzorek_omega_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    P: Sequence[Sequence[float]] | np.ndarray,
    confidences: Sequence[float] | np.ndarray,
    tau: float,
    *,
    relative_floor: float = 1.0e-10,
) -> np.ndarray:
    """Independent-view Idzorek uncertainty; omit zero-confidence rows upstream."""

    matrix, _ = nearest_positive_semidefinite_v5(covariance, relative_floor=relative_floor)
    views = _finite_matrix(P, "P")
    confidence = _finite_vector(confidences, "confidences")
    if views.shape[1] != matrix.shape[0] or confidence.size != views.shape[0]:
        raise ValueError("idzorek_inputs_must_align")
    if not math.isfinite(float(tau)) or float(tau) <= 0:
        raise ValueError("tau_must_be_positive")
    if np.any(confidence <= 0) or np.any(confidence > 1):
        raise ValueError("idzorek_confidence_must_be_in_open_zero_one_interval")
    prior_view_variance = np.einsum("ij,jk,ik->i", views, float(tau) * matrix, views)
    floor = max(float(np.median(np.maximum(np.diag(matrix), 0.0))) * relative_floor, _EPSILON)
    uncertainty = (1.0 - confidence) / confidence * prior_view_variance
    return np.diag(np.maximum(uncertainty, floor))


def black_litterman_posterior_v5(
    covariance: Sequence[Sequence[float]] | np.ndarray,
    prior_weights: Sequence[float] | np.ndarray,
    *,
    delta: float,
    tau: float,
    views: Any | None = None,
) -> BlackLittermanResultV5:
    """Combine a risk-budget prior with a duck-typed cycle ``ViewBundleV5``.

    ``views`` may be any object exposing ``P``, ``q`` and ``omega``.  The
    function intentionally does not import ``cycle_views_v5`` and therefore
    cannot create a circular dependency.
    """

    matrix, projection = nearest_positive_semidefinite_v5(covariance)
    weights = _finite_vector(prior_weights, "prior_weights")
    if weights.size != matrix.shape[0] or abs(float(weights.sum()) - 1.0) > 1.0e-8:
        raise ValueError("prior_weights_must_align_and_sum_to_one")
    if np.any(weights < -1.0e-12):
        raise ValueError("prior_weights_must_be_long_only")
    if not math.isfinite(float(tau)) or float(tau) <= 0:
        raise ValueError("tau_must_be_positive")
    pi = reverse_equilibrium_returns_v5(matrix, weights, delta)
    prior_mean_covariance = float(tau) * matrix

    if views is None:
        P = np.zeros((0, matrix.shape[0]))
        q = np.zeros(0)
        omega = np.zeros((0, 0))
        posterior = pi.copy()
        mean_covariance = prior_mean_covariance.copy()
        innovation_condition = 1.0
        view_diagnostics: dict[str, Any] = {}
    else:
        try:
            P = np.asarray(views.P, dtype=float)
            q = np.asarray(views.q, dtype=float)
            omega = np.asarray(views.omega, dtype=float)
        except AttributeError as error:
            raise ValueError("views_must_expose_P_q_and_omega") from error
        if P.ndim != 2 or P.shape[1] != matrix.shape[0]:
            raise ValueError("P_must_have_one_column_per_asset")
        if q.ndim != 1 or q.size != P.shape[0]:
            raise ValueError("q_must_have_one_value_per_view")
        if omega.shape != (P.shape[0], P.shape[0]):
            raise ValueError("omega_must_be_square_per_view")
        if not np.all(np.isfinite(P)) or not np.all(np.isfinite(q)) or not np.all(np.isfinite(omega)):
            raise ValueError("view_inputs_must_be_finite")
        if P.shape[0] == 0:
            posterior = pi.copy()
            mean_covariance = prior_mean_covariance.copy()
            innovation_condition = 1.0
        else:
            if np.linalg.matrix_rank(P) < P.shape[0]:
                raise ValueError("view_rows_must_be_linearly_independent")
            omega, omega_projection = nearest_positive_semidefinite_v5(omega)
            innovation = P @ prior_mean_covariance @ P.T + omega
            innovation, innovation_projection = nearest_positive_semidefinite_v5(innovation)
            innovation_condition = float(np.linalg.cond(innovation))
            right = q - P @ pi
            solved_right = np.linalg.solve(innovation, right)
            posterior = pi + prior_mean_covariance @ P.T @ solved_right
            solved_covariance = np.linalg.solve(innovation, P @ prior_mean_covariance)
            mean_covariance = prior_mean_covariance - prior_mean_covariance @ P.T @ solved_covariance
            mean_covariance, mean_projection = nearest_positive_semidefinite_v5(mean_covariance)
            view_diagnostics = {
                "omega_projection": omega_projection,
                "innovation_projection": innovation_projection,
                "posterior_mean_projection": mean_projection,
            }
        raw_diagnostics = getattr(views, "diagnostics", {})
        view_diagnostics = {**view_diagnostics, "source": _json_value(raw_diagnostics)}

    predictive, predictive_projection = nearest_positive_semidefinite_v5(matrix + mean_covariance)
    return BlackLittermanResultV5(
        prior_weights=weights,
        pi=pi,
        delta=float(delta),
        tau=float(tau),
        P=P,
        q=q,
        omega=omega,
        posterior_mean=posterior,
        posterior_mean_covariance=mean_covariance,
        predictive_covariance=predictive,
        diagnostics={
            "status": "ok",
            "view_count": int(P.shape[0]),
            "innovation_condition_number": innovation_condition,
            "covariance_projection": projection,
            "predictive_projection": predictive_projection,
            "views": view_diagnostics,
        },
    )


def _aligned_cost_vector(costs: dict[str, Any], key: str, size: int) -> np.ndarray:
    raw = costs.get(key, np.zeros(size))
    if np.isscalar(raw):
        vector = np.full(size, float(raw))
    else:
        vector = _finite_vector(raw, key)
    if vector.size != size or np.any(vector < 0):
        raise ValueError(f"{key}_must_be_nonnegative_and_align")
    return vector


def _optimizer_constraints(
    weights: np.ndarray,
    previous: np.ndarray,
    anchor: np.ndarray,
    covariance: np.ndarray,
    loadings: np.ndarray,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    size = len(weights)
    lower, upper = _constraint_arrays(
        size, constraints.get("lower_bounds"), constraints.get("upper_bounds")
    )
    annualization = float(constraints.get("annualization", 12.0))
    if annualization <= 0:
        raise ValueError("annualization_must_be_positive")
    turnover = 0.5 * float(np.abs(weights - previous).sum())
    annual_variance = annualization * float(weights @ covariance @ weights)
    tracking_variance = annualization * float((weights - anchor) @ covariance @ (weights - anchor))
    report: dict[str, Any] = {
        "budget_sum_error": abs(float(weights.sum()) - 1.0),
        "lower_bound_slack": weights - lower,
        "upper_bound_slack": upper - weights,
        "turnover": turnover,
        "annual_volatility": math.sqrt(max(annual_variance, 0.0)),
        "annual_tracking_error": math.sqrt(max(tracking_variance, 0.0)),
    }
    violations = [
        report["budget_sum_error"],
        float(np.max(np.maximum(lower - weights, 0.0))),
        float(np.max(np.maximum(weights - upper, 0.0))),
    ]
    if "max_turnover" in constraints:
        report["turnover_slack"] = float(constraints["max_turnover"]) - turnover
        violations.append(max(-report["turnover_slack"], 0.0))
    if "max_annual_volatility" in constraints:
        report["volatility_slack"] = float(constraints["max_annual_volatility"]) - report["annual_volatility"]
        violations.append(max(-report["volatility_slack"], 0.0))
    if "max_annual_tracking_error" in constraints:
        report["tracking_error_slack"] = float(constraints["max_annual_tracking_error"]) - report["annual_tracking_error"]
        violations.append(max(-report["tracking_error_slack"], 0.0))
    exposure = loadings.T @ weights if loadings.size else np.zeros(0)
    report["factor_exposure"] = exposure
    if "factor_lower_bounds" in constraints or "factor_upper_bounds" in constraints:
        factor_lower = np.full(len(exposure), -np.inf) if "factor_lower_bounds" not in constraints else _finite_vector(constraints["factor_lower_bounds"], "factor_lower_bounds")
        factor_upper = np.full(len(exposure), np.inf) if "factor_upper_bounds" not in constraints else _finite_vector(constraints["factor_upper_bounds"], "factor_upper_bounds")
        if factor_lower.size != len(exposure) or factor_upper.size != len(exposure):
            raise ValueError("factor_bounds_must_align")
        report["factor_lower_slack"] = exposure - factor_lower
        report["factor_upper_slack"] = factor_upper - exposure
        violations.extend([
            float(np.max(np.maximum(factor_lower - exposure, 0.0), initial=0.0)),
            float(np.max(np.maximum(exposure - factor_upper, 0.0), initial=0.0)),
        ])
    if "stress_returns" in constraints:
        stress = _finite_matrix(constraints["stress_returns"], "stress_returns")
        if stress.shape[1] != size:
            raise ValueError("stress_returns_must_align")
        maximum_loss = constraints.get("max_stress_loss")
        if maximum_loss is None:
            raise ValueError("stress_returns_require_max_stress_loss")
        loss_limit = np.full(stress.shape[0], float(maximum_loss)) if np.isscalar(maximum_loss) else _finite_vector(maximum_loss, "max_stress_loss")
        if loss_limit.size != stress.shape[0] or np.any(loss_limit < 0):
            raise ValueError("max_stress_loss_must_align_and_be_nonnegative")
        scenario_return = stress @ weights
        stress_slack = loss_limit + scenario_return
        report["stress_return"] = scenario_return
        report["stress_slack"] = stress_slack
        violations.append(float(np.max(np.maximum(-stress_slack, 0.0), initial=0.0)))
    if "linear_inequality_matrix" in constraints:
        matrix = _finite_matrix(constraints["linear_inequality_matrix"], "linear_inequality_matrix")
        bound = _finite_vector(constraints.get("linear_inequality_upper", []), "linear_inequality_upper")
        if matrix.shape[1] != size or matrix.shape[0] != bound.size:
            raise ValueError("linear_inequality_inputs_must_align")
        slack = bound - matrix @ weights
        report["linear_inequality_slack"] = slack
        violations.append(float(np.max(np.maximum(-slack, 0.0), initial=0.0)))
    report["max_violation"] = max(violations)
    return report


def optimize_allocation_v5(
    posterior: BlackLittermanResultV5,
    risk_anchor: RiskBudgetResultV5,
    covariance_bundle: CovarianceBundleV5,
    previous_weights: Sequence[float] | np.ndarray,
    constraints: dict[str, Any],
    costs: dict[str, Any],
    robust_spec: dict[str, Any],
) -> OptimizerResultV5:
    """Solve the single robust allocation problem; never relax hard constraints."""

    if minimize is None:  # pragma: no cover
        raise RuntimeError("scipy_is_required_for_unified_optimizer")
    covariance, covariance_projection = nearest_positive_semidefinite_v5(covariance_bundle.covariance)
    mean_covariance, mean_projection = nearest_positive_semidefinite_v5(
        posterior.posterior_mean_covariance
    )
    expected = _finite_vector(posterior.posterior_mean, "posterior_mean")
    anchor = _finite_vector(risk_anchor.weights, "risk_anchor_weights")
    previous = _finite_vector(previous_weights, "previous_weights")
    size = covariance.shape[0]
    if expected.size != size or anchor.size != size or previous.size != size:
        raise ValueError("optimizer_vectors_must_align")
    if abs(float(anchor.sum()) - 1.0) > 1.0e-8 or abs(float(previous.sum()) - 1.0) > 1.0e-8:
        raise ValueError("anchor_and_previous_weights_must_sum_to_one")
    if covariance_bundle.factor_loadings.shape[0] != size:
        raise ValueError("factor_loadings_must_align_with_assets")
    lower, upper = _constraint_arrays(
        size, constraints.get("lower_bounds"), constraints.get("upper_bounds")
    )
    linear_cost = _aligned_cost_vector(costs, "linear", size)
    quadratic_cost = _aligned_cost_vector(costs, "quadratic", size)
    risk_aversion = float(robust_spec.get("risk_aversion", 1.0))
    uncertainty_penalty = float(robust_spec.get("uncertainty_penalty", 0.0))
    anchor_penalty = float(robust_spec.get("anchor_penalty", 0.0))
    if min(risk_aversion, uncertainty_penalty, anchor_penalty) < 0:
        raise ValueError("optimizer_penalties_must_be_nonnegative")
    smooth = float(robust_spec.get("l1_smoothing", 1.0e-8))
    if smooth <= 0:
        raise ValueError("l1_smoothing_must_be_positive")

    def objective(weights: np.ndarray) -> float:
        change = weights - previous
        variance = float(weights @ covariance @ weights)
        mean_uncertainty = math.sqrt(max(float(weights @ mean_covariance @ weights), 0.0))
        anchor_distance = float((weights - anchor) @ covariance @ (weights - anchor))
        smooth_absolute = np.sqrt(change * change + smooth * smooth) - smooth
        transaction = float(linear_cost @ smooth_absolute + 0.5 * quadratic_cost @ (change * change))
        return (
            0.5 * risk_aversion * variance
            - float(expected @ weights)
            + uncertainty_penalty * mean_uncertainty
            + anchor_penalty * anchor_distance
            + transaction
        )

    def gradient(weights: np.ndarray) -> np.ndarray:
        change = weights - previous
        mean_variance = max(float(weights @ mean_covariance @ weights), 0.0)
        uncertainty_gradient = np.zeros(size)
        if mean_variance > _EPSILON and uncertainty_penalty > 0:
            uncertainty_gradient = uncertainty_penalty * (mean_covariance @ weights) / math.sqrt(mean_variance)
        cost_gradient = linear_cost * change / np.sqrt(change * change + smooth * smooth) + quadratic_cost * change
        return (
            risk_aversion * (covariance @ weights)
            - expected
            + uncertainty_gradient
            + 2.0 * anchor_penalty * (covariance @ (weights - anchor))
            + cost_gradient
        )

    annualization = float(constraints.get("annualization", 12.0))
    scipy_constraints: list[dict[str, Any]] = [
        {"type": "eq", "fun": lambda w: float(w.sum() - 1.0), "jac": lambda w: np.ones(size)}
    ]
    if "max_turnover" in constraints:
        maximum = float(constraints["max_turnover"])
        scipy_constraints.append(
            {"type": "ineq", "fun": lambda w, cap=maximum: cap - 0.5 * float(np.abs(w - previous).sum())}
        )
    if "max_annual_volatility" in constraints:
        maximum_variance = float(constraints["max_annual_volatility"]) ** 2
        scipy_constraints.append(
            {
                "type": "ineq",
                "fun": lambda w, cap=maximum_variance: cap - annualization * float(w @ covariance @ w),
                "jac": lambda w: -2.0 * annualization * (covariance @ w),
            }
        )
    if "max_annual_tracking_error" in constraints:
        maximum_tracking = float(constraints["max_annual_tracking_error"]) ** 2
        scipy_constraints.append(
            {
                "type": "ineq",
                "fun": lambda w, cap=maximum_tracking: cap - annualization * float((w - anchor) @ covariance @ (w - anchor)),
                "jac": lambda w: -2.0 * annualization * (covariance @ (w - anchor)),
            }
        )
    loadings = np.asarray(covariance_bundle.factor_loadings, dtype=float)
    if "factor_lower_bounds" in constraints:
        bounds = _finite_vector(constraints["factor_lower_bounds"], "factor_lower_bounds")
        if bounds.size != loadings.shape[1]:
            raise ValueError("factor_lower_bounds_must_align")
        for index, bound in enumerate(bounds):
            scipy_constraints.append(
                {"type": "ineq", "fun": lambda w, i=index, b=float(bound): float(loadings[:, i] @ w - b)}
            )
    if "factor_upper_bounds" in constraints:
        bounds = _finite_vector(constraints["factor_upper_bounds"], "factor_upper_bounds")
        if bounds.size != loadings.shape[1]:
            raise ValueError("factor_upper_bounds_must_align")
        for index, bound in enumerate(bounds):
            scipy_constraints.append(
                {"type": "ineq", "fun": lambda w, i=index, b=float(bound): float(b - loadings[:, i] @ w)}
            )
    if "stress_returns" in constraints:
        stress = _finite_matrix(constraints["stress_returns"], "stress_returns")
        if stress.shape[1] != size:
            raise ValueError("stress_returns_must_align")
        maximum_loss = constraints.get("max_stress_loss")
        if maximum_loss is None:
            raise ValueError("stress_returns_require_max_stress_loss")
        loss = np.full(stress.shape[0], float(maximum_loss)) if np.isscalar(maximum_loss) else _finite_vector(maximum_loss, "max_stress_loss")
        if loss.size != stress.shape[0]:
            raise ValueError("max_stress_loss_must_align")
        for index in range(stress.shape[0]):
            scipy_constraints.append(
                {"type": "ineq", "fun": lambda w, i=index: float(loss[i] + stress[i] @ w)}
            )
    if "linear_inequality_matrix" in constraints:
        linear_matrix = _finite_matrix(constraints["linear_inequality_matrix"], "linear_inequality_matrix")
        linear_upper = _finite_vector(constraints.get("linear_inequality_upper", []), "linear_inequality_upper")
        if linear_matrix.shape != (linear_upper.size, size):
            raise ValueError("linear_inequality_inputs_must_align")
        for index in range(linear_matrix.shape[0]):
            scipy_constraints.append(
                {"type": "ineq", "fun": lambda w, i=index: float(linear_upper[i] - linear_matrix[i] @ w)}
            )

    seeds: list[np.ndarray] = []
    for raw in (previous, anchor, np.full(size, 1.0 / size)):
        projected = _bounded_simplex_projection(raw, lower, upper)
        if not any(np.allclose(projected, existing) for existing in seeds):
            seeds.append(projected)
    best_result: Any | None = None
    best_weights: np.ndarray | None = None
    best_objective = math.inf
    attempts: list[dict[str, Any]] = []
    for seed in seeds:
        result = minimize(
            objective,
            seed,
            jac=gradient,
            method="SLSQP",
            bounds=list(zip(lower, upper)),
            constraints=scipy_constraints,
            options={
                "maxiter": int(robust_spec.get("max_iterations", 1500)),
                "ftol": float(robust_spec.get("solver_tolerance", 1.0e-11)),
                "disp": False,
            },
        )
        candidate = np.asarray(result.x, dtype=float)
        audit = _optimizer_constraints(candidate, previous, anchor, covariance, loadings, constraints)
        attempts.append(
            {
                "success": bool(result.success),
                "message": str(result.message),
                "iterations": int(getattr(result, "nit", 0) or 0),
                "objective": float(objective(candidate)),
                "max_violation": float(audit["max_violation"]),
            }
        )
        if result.success and float(audit["max_violation"]) <= 1.0e-7 and objective(candidate) < best_objective:
            best_result, best_weights, best_objective = result, candidate, objective(candidate)

    fallback_level = 0
    status = "optimal"
    if best_weights is None:
        for level, (label, candidate) in enumerate((("risk_anchor", anchor), ("previous_weights", previous)), 1):
            audit = _optimizer_constraints(candidate, previous, anchor, covariance, loadings, constraints)
            if float(audit["max_violation"]) <= 1.0e-7:
                best_weights = candidate.copy()
                best_objective = objective(best_weights)
                fallback_level = level
                status = f"fallback_{label}"
                break
    if best_weights is None:
        return OptimizerResultV5(
            weights=np.full(size, np.nan),
            status="infeasible",
            objective_terms={},
            constraint_slack={"max_violation": math.inf},
            shadow_prices={},
            turnover=math.nan,
            expected_cost=math.nan,
            fallback_level=3,
            diagnostics={
                "attempts": attempts,
                "reason": "hard_constraints_infeasible_or_solver_failed",
                "covariance_projection": covariance_projection,
                "mean_covariance_projection": mean_projection,
            },
        )

    change = best_weights - previous
    exact_cost = float(linear_cost @ np.abs(change) + 0.5 * quadratic_cost @ (change * change))
    mean_uncertainty = math.sqrt(max(float(best_weights @ mean_covariance @ best_weights), 0.0))
    objective_terms = {
        "expected_return": float(expected @ best_weights),
        "risk_penalty": 0.5 * risk_aversion * float(best_weights @ covariance @ best_weights),
        "mean_uncertainty_penalty": uncertainty_penalty * mean_uncertainty,
        "anchor_penalty": anchor_penalty * float((best_weights - anchor) @ covariance @ (best_weights - anchor)),
        "transaction_cost": exact_cost,
        "minimization_objective": best_objective,
    }
    constraint_audit = _optimizer_constraints(
        best_weights, previous, anchor, covariance, loadings, constraints
    )
    multipliers = getattr(best_result, "multipliers", None) if best_result is not None else None
    shadow_prices: dict[str, Any] = {}
    if multipliers is not None:
        shadow_prices = {f"solver_multiplier_{index}": float(value) for index, value in enumerate(np.ravel(multipliers))}
    return OptimizerResultV5(
        weights=best_weights,
        status=status,
        objective_terms=objective_terms,
        constraint_slack=constraint_audit,
        shadow_prices=shadow_prices,
        turnover=0.5 * float(np.abs(change).sum()),
        expected_cost=exact_cost,
        fallback_level=fallback_level,
        diagnostics={
            "solver": "SCIPY_SLSQP",
            "attempts": attempts,
            "covariance_projection": covariance_projection,
            "mean_covariance_projection": mean_projection,
            "hard_constraints_relaxed": False,
        },
    )


__all__ = [
    "BlackLittermanResultV5",
    "CovarianceBundleV5",
    "OptimizerResultV5",
    "RiskBudgetResultV5",
    "black_litterman_posterior_v5",
    "estimate_statistical_covariance_v5",
    "fit_macro_factor_covariance_v5",
    "idzorek_omega_v5",
    "nearest_positive_semidefinite_v5",
    "optimize_allocation_v5",
    "portfolio_risk_contribution_v5",
    "reverse_equilibrium_returns_v5",
    "solve_constrained_risk_budget_v5",
    "solve_erc_v5",
]
