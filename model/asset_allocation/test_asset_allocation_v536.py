from __future__ import annotations

import copy
import json

import numpy as np
import pytest

import asset_allocation_v536_stack as stack
from asset_allocation_v53_stack import StackParametersV53
from backtest_asset_allocation_v536_stack import _drawdown, _json, select_pretest_v536
from convex_optimizer_v536 import optimize_relative_v536
from cycle_views_v536 import fit_cycle_views_expanding_v536, forecast_cycle_views_v536


def _months(count: int = 48) -> tuple[str, ...]:
    return tuple(f"{2020 + index // 12:04d}{index % 12 + 1:02d}" for index in range(count))


def _cycles(months: tuple[str, ...], production: bool = False) -> list[dict]:
    rows = []
    for index, month in enumerate(months):
        pring = {str(value): 0.01 for value in range(1, 7)}
        pring[str(index % 6 + 1)] = 0.95
        total = sum(pring.values())
        rows.append(
            {
                "month": month,
                "cycles": {
                    "pring": {
                        "probabilities": {key: value / total for key, value in pring.items()},
                        "eligible_for_views": True,
                        "eligible_for_production_views": production,
                    },
                    "kitchin": {"probabilities": {}, "eligible_for_views": False, "eligible_for_production_views": False},
                    "juglar": {"probabilities": {}, "eligible_for_views": False, "eligible_for_production_views": False},
                    "merrill": {"probabilities": {}, "eligible_for_views": False, "eligible_for_production_views": False},
                    "kondratieff": {"probabilities": {}, "eligible_for_views": False, "eligible_for_production_views": False},
                },
            }
        )
    return rows


def test_average_rank_ties_are_symmetric_and_all_equal_is_zero() -> None:
    np.testing.assert_allclose(stack.average_rank_score_v536([1.0, 1.0, 1.0, 1.0]), 0.0)
    baseline = stack.average_rank_score_v536([1.0, 2.0, 2.0, 4.0])
    permutation = np.asarray([3, 1, 0, 2])
    permuted = stack.average_rank_score_v536(np.asarray([1.0, 2.0, 2.0, 4.0])[permutation])
    np.testing.assert_allclose(permuted, baseline[permutation])


def test_cycle_fit_uses_only_targets_through_signal_month() -> None:
    rng = np.random.default_rng(536)
    months = _months()
    returns = rng.normal(0.002, 0.02, size=(len(months), 4))
    cycles = _cycles(months, production=True)
    fitted = fit_cycle_views_expanding_v536(
        returns, cycles, months, signal_index=35, production_cycles=("pring",), minimum_train=18
    )
    changed = returns.copy()
    changed[36:] += np.asarray([0.8, -0.7, 0.5, -0.4])
    counterfactual = fit_cycle_views_expanding_v536(
        changed, copy.deepcopy(cycles), months, signal_index=35, production_cycles=("pring",), minimum_train=18
    )
    for key in ("feature_mean", "intercept", "coefficient", "omega"):
        np.testing.assert_allclose(fitted[key], counterfactual[key], atol=1.0e-12)


def test_cycle_q_is_absolute_relative_return_and_attribution_conserves() -> None:
    rng = np.random.default_rng(537)
    months = _months()
    returns = rng.normal(0.002, 0.02, size=(len(months), 4))
    cycles = _cycles(months, production=True)
    fitted = fit_cycle_views_expanding_v536(
        returns, cycles, months, signal_index=40, production_cycles=("pring",), minimum_train=18
    )
    forecast = forecast_cycle_views_v536(fitted, cycles[40])
    total = sum(np.asarray(value) for value in forecast["cycle_contributions"].values())
    np.testing.assert_allclose(forecast["q"], total, atol=1.0e-12)
    assert forecast["q_semantics"] == "absolute_relative_return_not_prior_plus_alpha"


def test_no_d3_cycle_emits_no_view() -> None:
    rng = np.random.default_rng(538)
    months = _months()
    returns = rng.normal(0.002, 0.02, size=(len(months), 4))
    fitted = fit_cycle_views_expanding_v536(
        returns, _cycles(months), months, signal_index=40, production_cycles=(), minimum_train=18
    )
    assert fitted["emits_view"] is False
    assert forecast_cycle_views_v536(fitted, _cycles(months)[40])["P"].shape == (0, 4)


def test_relative_optimizer_rejects_nonfinite_caps() -> None:
    covariance = np.eye(4) * 0.01
    kwargs = dict(
        active_expected_return=np.zeros(4),
        covariance=covariance,
        posterior_mean_covariance=np.eye(4) * 0.001,
        benchmark_weights=[0.6, 0.15, 0.1, 0.15],
        risk_budget_anchor=[0.6, 0.15, 0.1, 0.15],
        previous_weights=[0.6, 0.15, 0.1, 0.15],
        lower_bounds=[0.5, 0.1, 0.07, 0.1],
        upper_bounds=[0.7, 0.2, 0.13, 0.2],
        max_active_share=0.1,
        max_annual_tracking_error=float("nan"),
        max_one_way_turnover=0.08,
        linear_cost=[0.0005, 0.0002, 0.0005, 0.0006],
        quadratic_cost=[0.001, 0.0005, 0.0015, 0.002],
        active_risk_aversion=4.0,
        uncertainty_penalty=0.05,
        risk_budget_anchor_penalty=0.5,
        active_l2_penalty=0.01,
    )
    with pytest.raises(ValueError, match="max_annual_tracking_error_invalid"):
        optimize_relative_v536(**kwargs)


def test_zero_alpha_with_policy_anchor_returns_policy_without_fallback() -> None:
    result = optimize_relative_v536(
        np.zeros(4),
        np.eye(4) * 0.01,
        np.eye(4) * 0.001,
        [0.6, 0.15, 0.1, 0.15],
        [0.6, 0.15, 0.1, 0.15],
        [0.6, 0.15, 0.1, 0.15],
        lower_bounds=[0.5, 0.1, 0.07, 0.1],
        upper_bounds=[0.7, 0.2, 0.13, 0.2],
        max_active_share=0.1,
        max_annual_tracking_error=0.04,
        max_one_way_turnover=0.08,
        linear_cost=[0.0005, 0.0002, 0.0005, 0.0006],
        quadratic_cost=[0.001, 0.0005, 0.0015, 0.002],
        active_risk_aversion=4.0,
        uncertainty_penalty=0.05,
        risk_budget_anchor_penalty=0.5,
        active_l2_penalty=0.01,
    )
    assert result["status"] == "optimal"
    assert result["solver"]["fallback_used"] is False
    np.testing.assert_allclose(result["weights"], [0.6, 0.15, 0.1, 0.15], atol=1.0e-6)


def test_drawdown_includes_initial_nav() -> None:
    assert _drawdown(np.asarray([-0.05, 0.0])) == pytest.approx(-0.05)


def test_json_normalizer_handles_arrays_and_rejects_nonfinite() -> None:
    payload = _json({"x": np.asarray([1.0, 2.0])})
    json.dumps(payload, allow_nan=False)
    with pytest.raises(ValueError, match="nonfinite"):
        _json({"x": np.asarray([np.nan])})


def test_selector_rejects_test_and_requires_excess_for_both_modes() -> None:
    row = {
        "spec": {"id": "X"},
        "metrics": {"train": {}, "validation": {}, "test": {}},
        "pretest_calendar_years": {},
    }
    with pytest.raises(ValueError, match="selector_received_test"):
        select_pretest_v536([row], "absolute_no_benchmark")


def test_absolute_signature_contains_no_policy_benchmark_parameter() -> None:
    import inspect

    assert "policy" not in inspect.signature(stack.allocate_absolute_v536).parameters
    assert "benchmark" not in inspect.signature(stack.allocate_absolute_v536).parameters
