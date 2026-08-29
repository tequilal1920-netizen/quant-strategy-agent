from __future__ import annotations

import inspect

import numpy as np
import pytest

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v541_stack import (
    POLICY_WEIGHTS_V541,
    allocate_absolute_v541,
    allocate_relative_v541,
)


RNG = np.random.default_rng(541)
RETURNS = RNG.normal(0.003, [.04, .012, .035, .045], size=(61, 4))
LEVELS = np.cumsum(RNG.normal(0, 1, size=(61, 4)), axis=0)
ADMISSION = np.zeros((61, 4), dtype=bool)
MONTHS = tuple(
    f"{2019 + (index // 12):04d}{index % 12 + 1:02d}" for index in range(61)
)
NO_D3 = {"cycles": {}}


def test_entrypoint_only_accepts_levels_and_transforms_internally():
    result = allocate_relative_v541(
        RETURNS, LEVELS, ADMISSION, MONTHS, NO_D3,
        POLICY_WEIGHTS_V541, StackParametersV53(),
    )
    assert result["optimizer"]["status"] == "optimal"
    assert result["macro_truth_gate"]["input_role"].startswith("levels_only")
    assert result["macro_truth_gate"]["calendar_contiguity_verified"] is True


@pytest.mark.parametrize(
    "months",
    [
        MONTHS[:20] + (MONTHS[21],) + MONTHS[21:],
        MONTHS[:20] + (MONTHS[19],) + MONTHS[21:],
    ],
)
def test_missing_or_duplicate_month_is_rejected(months):
    with pytest.raises(ValueError, match="months_must_be"):
        allocate_relative_v541(
            RETURNS, LEVELS, ADMISSION, months, NO_D3,
            POLICY_WEIGHTS_V541, StackParametersV53(),
        )


def test_unadmitted_previous_level_cannot_affect_result():
    base = allocate_relative_v541(
        RETURNS, LEVELS, ADMISSION, MONTHS, NO_D3,
        POLICY_WEIGHTS_V541, StackParametersV53(),
    )
    changed = LEVELS.copy()
    changed[30, 2] += 100000.0
    counterfactual = allocate_relative_v541(
        RETURNS, changed, ADMISSION, MONTHS, NO_D3,
        POLICY_WEIGHTS_V541, StackParametersV53(),
    )
    assert np.allclose(base["weights"], counterfactual["weights"], atol=1e-13)


def test_absolute_end_to_end_and_signature_are_benchmark_free():
    result = allocate_absolute_v541(
        RETURNS, LEVELS, ADMISSION, MONTHS, NO_D3,
        [.15, .60, .10, .15], StackParametersV53(),
    )
    assert result["optimizer"]["status"] == "optimal"
    assert result["policy_benchmark_used_in_model"] is False
    parameters = inspect.signature(allocate_absolute_v541).parameters
    assert "benchmark" not in parameters
    assert "benchmark_weights" not in parameters
