from __future__ import annotations

import inspect

import numpy as np
import pytest

from convex_optimizer_v541 import optimize_absolute_v541, optimize_relative_v541


SIGMA = np.asarray(
    [[.020, .001, .002, .001], [.001, .004, 0, 0], [.002, 0, .015, .003], [.001, 0, .003, .018]]
)
MEAN_COV = np.eye(4) * .001
POLICY = [.60, .15, .10, .15]


def _relative(covariance=SIGMA):
    return optimize_relative_v541(
        [.002, -.001, -.0005, .001], covariance, MEAN_COV, POLICY, POLICY,
        lower_bounds=[.10, .05, .05, .05], upper_bounds=[.75, .40, .30, .40],
        max_active_share=.10, max_annual_tracking_error=.08,
        max_one_way_turnover=.08, linear_cost=[.0005, .0002, .0005, .0006],
        quadratic_cost=[.001, .0005, .0015, .002], active_risk_aversion=3.0,
        uncertainty_penalty=1.0, active_l2_penalty=.05,
    )


def test_symmetric_input_passes_and_preserves_complete_kkt():
    result = _relative()
    assert result["status"] == "optimal"
    assert result["solver"]["maximum_kkt_residual"] <= 1e-7
    assert result["input_symmetry_gate"]["covariance"]["passed"] is True


def test_large_antisymmetric_component_is_rejected():
    contaminated = SIGMA.copy()
    contaminated[0, 1] += 100.0
    contaminated[1, 0] -= 100.0
    with pytest.raises(ValueError, match="asymmetry_gate"):
        _relative(contaminated)


def test_absolute_api_still_has_no_benchmark_input():
    parameters = inspect.signature(optimize_absolute_v541).parameters
    assert "benchmark" not in parameters
    assert "benchmark_weights" not in parameters
