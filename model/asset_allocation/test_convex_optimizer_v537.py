from __future__ import annotations

import math

import numpy as np
import pytest

from convex_optimizer_v537 import optimize_absolute_v537, optimize_relative_v537


SIGMA = np.asarray([[.020,.001,.002,.001],[.001,.004,.000,.000],[.002,.000,.015,.003],[.001,.000,.003,.018]])
MEAN_COVARIANCE = np.eye(4) * .001
POLICY = np.asarray([.60,.15,.10,.15])
COST = np.asarray([.0005,.0002,.0005,.0006])
QUADRATIC = np.asarray([.0010,.0005,.0015,.0020])


def _relative(**overrides):
    arguments = dict(
        active_expected_return=[.0020,-.0010,-.0005,.0010],
        covariance=SIGMA,
        posterior_mean_covariance=MEAN_COVARIANCE,
        benchmark_weights=POLICY,
        previous_weights=POLICY,
        lower_bounds=[.10,.05,.05,.05],
        upper_bounds=[.75,.40,.30,.40],
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
    return optimize_relative_v537(**arguments)


def test_relative_solution_has_full_kkt_certificate():
    result = _relative()
    assert result["status"] == "optimal"
    assert result["constraints"]["max_violation"] <= 1e-7
    assert result["solver"]["maximum_kkt_residual"] <= 5e-6
    assert result["solver"]["maximum_stationarity_residual"] <= 5e-6
    assert result["solver"]["maximum_dual_feasibility_violation"] <= 5e-6
    assert result["solver"]["fallback_used"] is False


def test_zero_alpha_relative_returns_policy_without_post_scaling():
    result = _relative(active_expected_return=[0.0,0.0,0.0,0.0])
    assert result["status"] == "optimal"
    assert np.allclose(result["weights"], POLICY, atol=2e-6)
    assert np.allclose(result["active_weights"], np.zeros(4), atol=2e-6)


@pytest.mark.parametrize("bad", [math.nan,math.inf,-math.inf])
def test_nonfinite_caps_fail_closed(bad):
    with pytest.raises(ValueError):
        _relative(max_annual_tracking_error=bad)


def test_negative_costs_fail_closed_in_both_optimizers():
    with pytest.raises(ValueError):
        _relative(linear_cost=[-.1,0.0,0.0,0.0])
    with pytest.raises(ValueError):
        optimize_absolute_v537(
            [.0020,.0010,.0005,.0015],SIGMA,MEAN_COVARIANCE,
            [.15,.60,.10,.15],[.15,.60,.10,.15],
            lower_bounds=[.10,.10,.05,.05],upper_bounds=[.60,.75,.30,.40],
            max_one_way_turnover=.10,linear_cost=[-.1,0.0,0.0,0.0],
            quadratic_cost=QUADRATIC,risk_aversion=3.0,
            uncertainty_penalty=1.0,anchor_penalty=.40,
        )
