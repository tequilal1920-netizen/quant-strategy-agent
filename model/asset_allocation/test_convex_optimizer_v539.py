from __future__ import annotations

import inspect
import math

import numpy as np
import pytest

import convex_optimizer_v539 as module
from convex_optimizer_v539 import optimize_absolute_v539, optimize_relative_v539


SIGMA = np.asarray(
    [
        [.020, .001, .002, .001],
        [.001, .004, .000, .000],
        [.002, .000, .015, .003],
        [.001, .000, .003, .018],
    ]
)
MEAN_COVARIANCE = np.eye(4) * .001
POLICY = np.asarray([.60, .15, .10, .15])
COST = np.asarray([.0005, .0002, .0005, .0006])
QUADRATIC = np.asarray([.0010, .0005, .0015, .0020])


def _relative(**overrides):
    arguments = dict(
        active_expected_return=[.0020, -.0010, -.0005, .0010],
        covariance=SIGMA,
        posterior_mean_covariance=MEAN_COVARIANCE,
        benchmark_weights=POLICY,
        previous_weights=POLICY,
        lower_bounds=[.10, .05, .05, .05],
        upper_bounds=[.75, .40, .30, .40],
        max_active_share=.10,
        max_annual_tracking_error=.08,
        max_one_way_turnover=.08,
        linear_cost=COST,
        quadratic_cost=QUADRATIC,
        active_risk_aversion=3.0,
        uncertainty_penalty=1.0,
        active_l2_penalty=.05,
    )
    arguments.update(overrides)
    return optimize_relative_v539(**arguments)


def _absolute(**overrides):
    arguments = dict(
        expected_return=[.0020, .0010, .0005, .0015],
        covariance=SIGMA,
        posterior_mean_covariance=MEAN_COVARIANCE,
        risk_budget_anchor=[.15, .60, .10, .15],
        previous_weights=[.15, .60, .10, .15],
        lower_bounds=[.10, .10, .05, .05],
        upper_bounds=[.60, .75, .30, .40],
        max_one_way_turnover=.10,
        linear_cost=COST,
        quadratic_cost=QUADRATIC,
        risk_aversion=3.0,
        uncertainty_penalty=1.0,
        anchor_penalty=.40,
    )
    arguments.update(overrides)
    return optimize_absolute_v539(**arguments)


def _assert_full(result):
    assert result["status"] == "optimal"
    assert result["constraints"]["max_violation"] <= 1e-7
    solver = result["solver"]
    assert solver["maximum_kkt_residual"] <= 1e-7
    assert solver["maximum_stationarity_residual"] <= 1e-7
    assert solver["maximum_dual_feasibility_violation"] <= 1e-7
    assert solver["maximum_complementarity_residual"] <= 1e-7
    assert solver["duality_gap_available"] is True
    assert solver["canonical_solver_certificate"]["version_lock_passed"] is True
    assert solver["fallback_used"] is False


def test_relative_nonzero_and_zero_active_have_complete_certificates():
    _assert_full(_relative())
    zero = _relative(active_expected_return=[0.0, 0.0, 0.0, 0.0])
    _assert_full(zero)
    assert np.allclose(zero["weights"], POLICY, atol=2e-6)
    for name in (
        "active_positive",
        "active_negative",
        "active_abs_nonnegative",
        "change_positive",
        "change_negative",
        "change_abs_nonnegative",
    ):
        assert name in zero["solver"]["dual_values"]


def test_absolute_nonzero_and_zero_change_have_complete_certificates():
    _assert_full(_absolute())
    _assert_full(_absolute(expected_return=[0.0, 0.0, 0.0, 0.0]))


def test_absolute_api_has_no_benchmark_parameter():
    parameters = inspect.signature(optimize_absolute_v539).parameters
    assert "benchmark" not in parameters
    assert "benchmark_weights" not in parameters


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nonfinite_caps_fail_closed(bad):
    with pytest.raises(ValueError):
        _relative(max_annual_tracking_error=bad)


def test_negative_costs_fail_closed():
    with pytest.raises(ValueError):
        _relative(linear_cost=[-.1, 0.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        _absolute(linear_cost=[-.1, 0.0, 0.0, 0.0])


def test_missing_or_unpinned_canonical_certificate_fails_closed(monkeypatch):
    monkeypatch.setattr(module, "CVXPY_VERSION_V539", "impossible")
    with pytest.raises(RuntimeError, match="unpinned"):
        _relative()


def test_material_psd_repair_is_rejected():
    bad = SIGMA.copy()
    bad[0, 0] = -1.0
    with pytest.raises(ValueError, match="materially_non_psd"):
        _relative(covariance=bad)
