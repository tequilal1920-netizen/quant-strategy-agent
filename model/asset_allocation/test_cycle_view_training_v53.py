from __future__ import annotations

import copy

import numpy as np

from cycle_view_training_v53 import (
    fit_frozen_cycle_view_model_v53,
    target_month_train_mask_v53,
)


def _row(index: int) -> dict:
    states = {str(number): 0.02 for number in range(1, 7)}
    states[str(index % 6 + 1)] = 0.90
    total = sum(states.values())
    states = {key: value / total for key, value in states.items()}
    return {
        "month": f"{2020 + index // 12:04d}{index % 12 + 1:02d}",
        "cycles": {
            "pring": {"probabilities": states, "eligible_for_views": True},
            "kitchin": {"probabilities": {}, "eligible_for_views": False},
            "juglar": {"probabilities": {}, "eligible_for_views": False},
            "merrill": {"probabilities": {}, "eligible_for_views": False},
            "kondratieff": {"probabilities": {}, "eligible_for_views": False},
        },
    }


def test_target_month_mask_rejects_first_validation_label() -> None:
    months = tuple(f"{2020 + index // 12:04d}{index % 12 + 1:02d}" for index in range(48))
    mask = target_month_train_mask_v53(months, "202212")
    pairs = [(months[index], months[index + 1], mask[index]) for index in range(len(months) - 1)]
    assert next(value for feature, target, value in pairs if target == "202301") is False
    assert next(value for feature, target, value in pairs if target == "202212") is True


def test_first_validation_return_counterfactual_cannot_change_frozen_fit() -> None:
    rng = np.random.default_rng(20260813)
    months = tuple(f"{2020 + index // 12:04d}{index % 12 + 1:02d}" for index in range(48))
    cycles = [_row(index) for index in range(48)]
    returns = rng.normal(0.003, [0.04, 0.015, 0.025, 0.035], size=(48, 4))
    original = fit_frozen_cycle_view_model_v53(
        returns,
        cycles,
        months,
        train_end="202212",
        minimum_train=18,
    )
    changed_returns = returns.copy()
    changed_returns[months.index("202301")] += np.asarray([0.50, -0.40, 0.30, -0.20])
    counterfactual = fit_frozen_cycle_view_model_v53(
        changed_returns,
        copy.deepcopy(cycles),
        months,
        train_end="202212",
        minimum_train=18,
    )
    for key in ("feature_mean", "intercept", "coefficient", "omega"):
        np.testing.assert_allclose(original[key], counterfactual[key], atol=1.0e-12)
    assert original["selected_ridge"] == counterfactual["selected_ridge"]
    assert original["last_admitted_target_month"] == "202212"
    assert original["first_rejected_target_month"] == "202301"
