"""Numerically explicit v5.3.4 risk-anchor correction.

When no D3 cycle is admitted, a policy portfolio can contain non-positive Euler
risk contributions.  This module refuses to turn those values into a fake
strictly-positive risk budget.  Relative allocation therefore uses the fixed
policy capital vector as its disclosed risk anchor; absolute allocation uses a
strict equal-risk-contribution target.  Once D3 cycles exist, the standard
constrained cycle risk budget is used unchanged.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v533_stack as v533
from allocation_math_v5 import (
    RiskBudgetResultV5,
    portfolio_risk_contribution_v5,
    solve_constrained_risk_budget_v5,
)
from asset_allocation_v53_stack import POLICY_WEIGHTS_V53, cycle_risk_budget_v5


def truth_gated_risk_budget_v534(
    covariance: np.ndarray,
    cycle_row: Mapping[str, Any],
    *,
    mode: str,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
) -> tuple[RiskBudgetResultV5, dict[str, Any]]:
    admitted = v533._production_cycles(cycle_row)
    shadow = [
        name
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_views")) and name not in admitted
    ]
    if admitted:
        target, policy = cycle_risk_budget_v5(cycle_row)
        result = solve_constrained_risk_budget_v5(
            covariance, target, lower_bounds, upper_bounds
        )
        return result, {
            "source": "D3_production_cycle_probability_budget",
            "production_admitted_cycles": list(admitted),
            "shadow_cycles_excluded": shadow,
            "target_budget": np.asarray(target).tolist(),
            "policy": policy,
            "negative_risk_contribution_projection_applied": False,
        }
    if mode == "benchmark_relative":
        _, _, actual = portfolio_risk_contribution_v5(
            covariance, POLICY_WEIGHTS_V53
        )
        result = RiskBudgetResultV5(
            weights=POLICY_WEIGHTS_V53.copy(),
            target_budget=np.asarray(actual, dtype=float),
            relative_risk_contribution=np.asarray(actual, dtype=float),
            budget_error=np.zeros(4),
            kkt_residual=0.0,
            active_constraints=(),
            shadow_prices={},
            status="fixed_policy_risk_anchor_no_D3_cycle",
            diagnostics={
                "method": "declared_policy_capital_anchor_with_actual_euler_risk_disclosure",
                "negative_risk_contribution_projection_applied": False,
            },
        )
        return result, {
            "source": "fixed_policy_risk_anchor_no_D3_cycle",
            "production_admitted_cycles": [],
            "shadow_cycles_excluded": shadow,
            "actual_euler_risk_contribution": np.asarray(actual).tolist(),
            "negative_risk_contribution_projection_applied": False,
        }
    if mode == "absolute_no_benchmark":
        target = np.full(4, 0.25)
        result = solve_constrained_risk_budget_v5(
            covariance, target, lower_bounds, upper_bounds
        )
        return result, {
            "source": "strict_equal_risk_budget_no_D3_cycle",
            "production_admitted_cycles": [],
            "shadow_cycles_excluded": shadow,
            "target_budget": target.tolist(),
            "negative_risk_contribution_projection_applied": False,
        }
    raise ValueError("v534_unknown_mode")


def install_truth_gate_v534() -> Any:
    """Return original private hook after installing an explicit local wrapper.

    This helper exists only for the isolated research runner below.  Production
    builders must call v5.3.4 functions directly and must not monkey-patch.
    """

    original = v533._truth_gated_risk_budget
    v533._truth_gated_risk_budget = truth_gated_risk_budget_v534
    return original


__all__ = ["install_truth_gate_v534", "truth_gated_risk_budget_v534"]
