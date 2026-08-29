from __future__ import annotations

import inspect

import numpy as np
import pytest

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v537_stack import (
    POLICY_WEIGHTS_V537,
    allocate_absolute_v537,
    covariance_truth_gated_v537,
    policy_risk_diagnostic_v537,
    strict_erc_anchor_v537,
)


def _returns(months=36):
    index = np.arange(months, dtype=float)
    return np.column_stack(
        [
            .004 + .01 * np.sin(index / 4),
            .002 + .002 * np.cos(index / 7),
            .003 + .008 * np.sin(index / 5 + .5),
            .003 + .012 * np.cos(index / 6 + .2),
        ]
    )


def test_unadmitted_macro_cell_cannot_affect_covariance():
    returns = _returns(24)
    macro = np.arange(96, dtype=float).reshape(24, 4) / 100.0
    admission = np.ones((24, 4), dtype=bool)
    admission[0, 0] = False
    first, gate = covariance_truth_gated_v537(
        returns, macro, admission, StackParametersV53()
    )
    changed = macro.copy()
    changed[0, 0] = 999999.0
    second, second_gate = covariance_truth_gated_v537(
        returns, changed, admission, StackParametersV53()
    )
    assert gate["gate_passed"] is False
    assert second_gate["effective_macro_blend_weight"] == 0.0
    assert np.array_equal(first.covariance, second.covariance)


def test_fully_admitted_macro_can_enter_only_after_complete_row_gate():
    returns = _returns(36)
    macro = np.column_stack(
        [
            returns[:, 0] - returns[:, 1],
            returns[:, 2] - returns[:, 1],
            returns[:, 3] - returns[:, 1],
            returns[:, 0] - returns[:, 3],
        ]
    )
    covariance, gate = covariance_truth_gated_v537(
        returns, macro, np.ones_like(macro, dtype=bool), StackParametersV53()
    )
    assert gate["gate_passed"] is True
    assert covariance.macro_blend_weight > 0.0


def test_policy_risk_is_diagnostic_not_fictitious_budget():
    diagnostic = policy_risk_diagnostic_v537(np.eye(4))
    assert diagnostic["role"].endswith("not_risk_budget_optimization")
    assert diagnostic["capital_weights"] == POLICY_WEIGHTS_V537.tolist()
    assert diagnostic["projection_or_absolute_value_applied"] is False


def test_strict_erc_anchor_has_tight_budget_and_kkt_gate():
    weights, evidence = strict_erc_anchor_v537(np.diag([.04, .01, .0225, .0324]))
    assert np.isclose(weights.sum(), 1.0)
    assert evidence["gate"]["passed"] is True
    assert evidence["gate"]["maximum_budget_error"] <= 1e-5
    assert evidence["gate"]["kkt_residual"] <= 1e-7


def test_absolute_model_signature_has_no_policy_or_benchmark_argument():
    parameters = inspect.signature(allocate_absolute_v537).parameters
    assert "policy" not in parameters
    assert "benchmark" not in parameters
    assert "benchmark_weights" not in parameters


def test_malformed_admission_matrix_fails_closed():
    with pytest.raises(ValueError):
        covariance_truth_gated_v537(
            _returns(24), np.zeros((24,4)), np.zeros((24,3), dtype=bool), StackParametersV53()
        )
