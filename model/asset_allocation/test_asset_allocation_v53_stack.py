from __future__ import annotations

import copy

import numpy as np

from asset_allocation_v53_stack import (
    POLICY_WEIGHTS_V53,
    StackParametersV53,
    allocate_absolute_v53,
    allocate_relative_v53,
    causal_market_strength_v53,
)
from cycle_view_training_v53 import fit_frozen_cycle_view_model_v53


def _row(index: int) -> dict:
    pring = {str(number): 0.02 for number in range(1, 7)}
    pring[str(index % 6 + 1)] = 0.90
    total = sum(pring.values())
    pring = {key: value / total for key, value in pring.items()}
    return {
        "month": f"{2019 + index // 12:04d}{index % 12 + 1:02d}",
        "growth_score": 0.0,
        "inflation_score": 0.0,
        "credit_score": 0.0,
        "liquidity_score": 0.0,
        "cycles": {
            "pring": {"probabilities": pring, "eligible_for_views": True},
            "kitchin": {"probabilities": {}, "eligible_for_views": False},
            "juglar": {"probabilities": {}, "eligible_for_views": False},
            "merrill": {"probabilities": {}, "eligible_for_views": False},
            "kondratieff": {"probabilities": {}, "eligible_for_views": False},
        },
    }


def _inputs():
    rng = np.random.default_rng(20260813)
    returns = rng.normal(0.003, [0.040, 0.012, 0.025, 0.035], size=(60, 4))
    months = tuple(f"{2019 + index // 12:04d}{index % 12 + 1:02d}" for index in range(60))
    cycles = [_row(index) for index in range(60)]
    model = fit_frozen_cycle_view_model_v53(
        returns,
        cycles,
        months,
        train_end="202212",
        minimum_train=24,
    )
    macro = np.zeros((36, 4))
    admitted = np.zeros(36, dtype=bool)
    return returns[-36:], macro, admitted, cycles[-1], model


def test_market_strength_uses_explicit_three_horizons() -> None:
    returns, *_ = _inputs()
    output = causal_market_strength_v53(returns)
    assert output["horizon_weights"] == {"3": 0.20, "6": 0.35, "12": 0.45}
    assert abs(float(np.sum(output["cross_sectional_rank_score"]))) <= 1.0e-12
    assert output["selection_uses_test"] is False


def test_relative_stack_has_complete_bl_risk_budget_and_active_optimizer() -> None:
    returns, macro, admitted, cycle, model = _inputs()
    weights, diagnostics = allocate_relative_v53(
        returns,
        macro,
        admitted,
        cycle,
        model,
        POLICY_WEIGHTS_V53,
        StackParametersV53(),
    )
    assert abs(float(weights.sum()) - 1.0) <= 1.0e-9
    assert diagnostics["model_version"] == "benchmark_relative"
    assert diagnostics["shared"]["effective_macro_blend_weight"] == 0.0
    assert diagnostics["black_litterman"]["P"]
    assert diagnostics["risk_budget"]["weights"]
    assert diagnostics["optimizer"]["constraints"]["max_violation"] <= 1.0e-7
    assert diagnostics["selection_uses_test"] is False


def test_absolute_stack_does_not_read_policy_benchmark() -> None:
    returns, macro, admitted, cycle, model = _inputs()
    weights, diagnostics = allocate_absolute_v53(
        returns,
        macro,
        admitted,
        cycle,
        model,
        [0.25, 0.35, 0.15, 0.25],
        StackParametersV53(),
    )
    assert abs(float(weights.sum()) - 1.0) <= 1.0e-9
    assert diagnostics["model_version"] == "absolute_no_benchmark"
    assert diagnostics["policy_benchmark_used_in_model"] is False
    assert "policy_benchmark" not in diagnostics
    assert diagnostics["optimizer"]["constraint_slack"]["max_violation"] <= 1.0e-7


def test_validation_return_counterfactual_cannot_change_stack_frozen_view_model() -> None:
    rng = np.random.default_rng(19)
    returns = rng.normal(0.003, [0.04, 0.012, 0.025, 0.035], size=(60, 4))
    months = tuple(f"{2019 + index // 12:04d}{index % 12 + 1:02d}" for index in range(60))
    cycles = [_row(index) for index in range(60)]
    original = fit_frozen_cycle_view_model_v53(
        returns, cycles, months, train_end="202212", minimum_train=24
    )
    changed = returns.copy()
    changed[months.index("202301")] += np.asarray([0.8, -0.6, 0.4, -0.3])
    counterfactual = fit_frozen_cycle_view_model_v53(
        changed, copy.deepcopy(cycles), months, train_end="202212", minimum_train=24
    )
    for key in ("feature_mean", "intercept", "coefficient", "omega"):
        np.testing.assert_allclose(original[key], counterfactual[key], atol=1.0e-12)
