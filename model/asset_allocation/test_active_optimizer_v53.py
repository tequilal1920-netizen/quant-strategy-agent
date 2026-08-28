from __future__ import annotations

import numpy as np

from active_optimizer_v53 import optimize_policy_relative_v53


POLICY = np.asarray([0.60, 0.15, 0.10, 0.15])


def _solve(alpha: np.ndarray):
    covariance = np.asarray(
        [
            [0.0030, -0.0002, 0.0001, 0.0004],
            [-0.0002, 0.0005, 0.0001, -0.0001],
            [0.0001, 0.0001, 0.0012, 0.0002],
            [0.0004, -0.0001, 0.0002, 0.0024],
        ]
    )
    return optimize_policy_relative_v53(
        alpha,
        covariance,
        POLICY,
        POLICY,
        lower_bounds=POLICY - np.asarray([0.10, 0.05, 0.03, 0.05]),
        upper_bounds=POLICY + np.asarray([0.10, 0.05, 0.03, 0.05]),
        max_active_share=0.10,
        max_annual_tracking_error=0.04,
        max_one_way_turnover=0.08,
        linear_cost=np.asarray([5.0, 2.0, 5.0, 6.0]) / 10000.0,
        quadratic_cost=np.asarray([0.0010, 0.0005, 0.0015, 0.0020]),
        active_risk_aversion=4.0,
        active_l2_penalty=0.02,
    )


def test_zero_active_alpha_returns_policy() -> None:
    result = _solve(np.zeros(4))
    assert result.status == "optimal"
    assert np.allclose(result.weights, POLICY, atol=1.0e-8)
    assert np.allclose(result.active_weights, 0.0, atol=1.0e-8)
    assert result.constraints["max_violation"] <= 1.0e-7


def test_positive_equity_relative_view_produces_zero_sum_active_tilt() -> None:
    result = _solve(np.asarray([0.010, -0.004, -0.002, -0.004]))
    assert result.status == "optimal"
    assert result.active_weights[0] > 0.0
    assert abs(float(result.active_weights.sum())) <= 1.0e-9
    assert result.constraints["active_share"] <= 0.10 + 1.0e-8
    assert result.constraints["annual_tracking_error"] <= 0.04 + 1.0e-8
    assert result.constraints["turnover"] <= 0.08 + 1.0e-8
    assert result.constraints["max_violation"] <= 1.0e-7


def test_optimizer_reports_multiple_solver_attempts_and_never_relaxes() -> None:
    result = _solve(np.asarray([-0.002, 0.006, -0.001, -0.003]))
    assert result.diagnostics["hard_constraints_relaxed"] is False
    assert result.diagnostics["selection_uses_test"] is False
    assert len(result.diagnostics["attempts"]) >= 1
    assert all("max_violation" in row for row in result.diagnostics["attempts"])
