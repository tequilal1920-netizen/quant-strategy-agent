"""Truth-gated v5.3.2 wrappers around the complete v5.3 stack.

Only cycle views explicitly admitted for production may receive non-zero BL
consensus weight.  D2/shadow Pring remains fully visible in diagnostics but
cannot override the causal market view.  This wrapper avoids changing the
earlier research artifact and can be reviewed independently.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v53_stack as stack


def production_admitted_cycles_v532(cycle_row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(name)
        for name, payload in (cycle_row.get("cycles") or {}).items()
        if bool(payload.get("eligible_for_production_views"))
    )


def effective_parameters_v532(
    cycle_row: Mapping[str, Any], parameters: stack.StackParametersV53
) -> tuple[stack.StackParametersV53, dict[str, Any]]:
    admitted = production_admitted_cycles_v532(cycle_row)
    effective = parameters if admitted else replace(parameters, cycle_view_weight=0.0)
    return effective, {
        "production_admitted_cycles": list(admitted),
        "cycle_weight_requested": parameters.cycle_view_weight,
        "cycle_weight_effective": effective.cycle_view_weight,
        "cycle_gate": (
            "production_cycle_views_active"
            if admitted
            else "no_D3_cycle_views_market_signal_only"
        ),
    }


def allocate_relative_v532(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: stack.StackParametersV53,
    **kwargs: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    effective, gate = effective_parameters_v532(cycle_row, parameters)
    weights, diagnostics = stack.allocate_relative_v53(
        return_history,
        macro_history,
        macro_admitted,
        cycle_row,
        fitted_cycle_view_model,
        previous_weights,
        effective,
        **kwargs,
    )
    diagnostics["cycle_production_gate"] = gate
    return weights, diagnostics


def allocate_absolute_v532(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: Sequence[bool],
    cycle_row: Mapping[str, Any],
    fitted_cycle_view_model: Mapping[str, Any],
    previous_weights: Sequence[float],
    parameters: stack.StackParametersV53,
    **kwargs: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    effective, gate = effective_parameters_v532(cycle_row, parameters)
    weights, diagnostics = stack.allocate_absolute_v53(
        return_history,
        macro_history,
        macro_admitted,
        cycle_row,
        fitted_cycle_view_model,
        previous_weights,
        effective,
        **kwargs,
    )
    diagnostics["cycle_production_gate"] = gate
    return weights, diagnostics


__all__ = [
    "allocate_absolute_v532",
    "allocate_relative_v532",
    "effective_parameters_v532",
    "production_admitted_cycles_v532",
]
