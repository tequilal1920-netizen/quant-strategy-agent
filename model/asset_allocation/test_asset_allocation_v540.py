from __future__ import annotations

import inspect

import numpy as np
import pytest

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v540_stack import (
    POLICY_WEIGHTS_V540,
    allocate_absolute_v540,
    allocate_relative_v540,
    covariance_truth_gated_v540,
    macro_innovations_truth_gated_v540,
    policy_risk_diagnostic_v540,
    strict_erc_prior_anchor_v540,
)


RNG = np.random.default_rng(20260813)
RETURNS = RNG.normal(0.004, [0.04, 0.012, 0.035, 0.045], size=(60, 4))
MACRO = RNG.normal(0.0, 1.0, size=(60, 4))
CURRENT_NO_D3 = {
    "cycles": {
        "pring": {
            "eligible_for_production_views": False,
            "view_scope": "shadow_only",
            "data_status": "research_execution_proxy_not_D3",
        }
    }
}


def test_level_to_innovation_admission_requires_both_vintages():
    levels = np.arange(24.0).reshape(6, 4)
    admission = np.ones_like(levels, dtype=bool)
    admission[2, 1] = False
    innovations, transformed = macro_innovations_truth_gated_v540(levels, admission)
    assert innovations.shape == (5, 4)
    assert transformed[1, 1] == np.bool_(False)
    assert transformed[2, 1] == np.bool_(False)
    with pytest.raises(ValueError):
        macro_innovations_truth_gated_v540(levels, admission.astype(str))


def test_unadmitted_macro_cell_is_invariant_but_same_asset_return_is_not():
    admission = np.ones((60, 4), dtype=bool)
    admission[-12, 0] = False
    parameters = StackParametersV53(macro_blend_weight=.25)
    base, gate = covariance_truth_gated_v540(RETURNS, MACRO, admission, parameters)
    changed_macro = MACRO.copy()
    changed_macro[-12, 0] += 10000.0
    counterfactual, _ = covariance_truth_gated_v540(
        RETURNS, changed_macro, admission, parameters
    )
    assert np.allclose(base.covariance, counterfactual.covariance, atol=1e-14)
    changed_return = RETURNS.copy()
    changed_return[-12, 0] += .25
    return_counterfactual, _ = covariance_truth_gated_v540(
        changed_return, MACRO, admission, parameters
    )
    assert not np.allclose(base.covariance, return_counterfactual.covariance)
    assert gate["effective_macro_blend_weight"] == 0.0
    assert gate["statistical_leg_uses_all_asset_returns"] is True


def test_recent_contiguous_complete_PIT_suffix_activates_macro_leg():
    admission = np.ones((60, 4), dtype=bool)
    parameters = StackParametersV53(macro_blend_weight=.25)
    bundle, gate = covariance_truth_gated_v540(RETURNS, MACRO, admission, parameters)
    assert gate["gate_passed"] is True
    assert gate["effective_macro_blend_weight"] == .25
    assert bundle.macro_blend_weight == .25


def test_policy_risk_diagnostic_labels_marginal_and_euler_separately():
    covariance = np.cov(RETURNS.T)
    diagnostic = policy_risk_diagnostic_v540(covariance)
    assert diagnostic["role"].endswith("not_risk_budget")
    assert "marginal_risk_contribution" in diagnostic
    assert "euler_risk_contribution" in diagnostic
    assert diagnostic["projection_or_absolute_value_applied"] is False


def test_strict_erc_rejects_degenerate_covariance_and_passes_regular_case():
    with pytest.raises(RuntimeError, match="covariance_truth_gate"):
        strict_erc_prior_anchor_v540(np.zeros((4, 4)), [.1, .1, .05, .05], [.6, .75, .3, .4])
    anchor, evidence = strict_erc_prior_anchor_v540(
        np.cov(RETURNS.T), [.1, .1, .05, .05], [.6, .75, .3, .4]
    )
    assert np.isclose(anchor.sum(), 1.0)
    assert evidence["gate"]["maximum_budget_error"] <= 1e-8
    assert evidence["gate"]["final_optimizer_portfolio_is_claimed_ERC"] is False


def test_end_to_end_relative_and_absolute_are_optimal_and_truth_gated():
    parameters = StackParametersV53(macro_blend_weight=0.0)
    admission = np.zeros((60, 4), dtype=bool)
    relative = allocate_relative_v540(
        RETURNS,
        MACRO,
        admission,
        CURRENT_NO_D3,
        POLICY_WEIGHTS_V540,
        parameters,
    )
    absolute = allocate_absolute_v540(
        RETURNS,
        MACRO,
        admission,
        CURRENT_NO_D3,
        [.15, .60, .10, .15],
        parameters,
    )
    assert relative["optimizer"]["status"] == "optimal"
    assert absolute["optimizer"]["status"] == "optimal"
    assert relative["optimizer"]["solver"]["maximum_kkt_residual"] <= 1e-7
    assert absolute["optimizer"]["solver"]["maximum_kkt_residual"] <= 1e-7
    assert relative["view_consensus"]["production_cycles"] == []
    assert absolute["policy_benchmark_used_in_model"] is False


def test_absolute_api_has_no_benchmark_or_fitted_cycle_input():
    parameters = inspect.signature(allocate_absolute_v540).parameters
    assert "benchmark" not in parameters
    assert "benchmark_weights" not in parameters
    assert "fitted_cycle" not in parameters


def test_any_current_D3_cycle_fails_until_full_history_pipeline_is_certified():
    current = {
        "cycles": {
            "pring": {
                "eligible_for_production_views": True,
                "view_scope": "production",
                "data_status": "D3_verified",
            }
        }
    }
    with pytest.raises(RuntimeError, match="history_pipeline"):
        allocate_relative_v540(
            RETURNS,
            MACRO,
            np.zeros((60, 4), dtype=bool),
            current,
            POLICY_WEIGHTS_V540,
            StackParametersV53(),
        )
