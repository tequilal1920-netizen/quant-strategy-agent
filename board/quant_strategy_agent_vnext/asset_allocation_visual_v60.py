"""Graph-first visual adapter for v6.0 two-cycle / three-model allocation."""

from __future__ import annotations

from typing import Any

import asset_allocation_visual_v58 as _base


MODEL_ORDER = ("black_litterman", "risk_parity", "macro_factor")
COLORS = {
    "black_litterman": "#c00000",
    "risk_parity": "#7f7f7f",
    "macro_factor": "#7030a0",
    "equal_weight_3_assets": "#98a2b3",
    "equal_anchor_1_3_1_3_1_3": "#163d7a",
}


def _retitle(payload: dict[str, Any]) -> dict[str, Any]:
    payload["descriptive"]["title"] = "Two-cycle tracking: Merrill clock + Pring cycle only"
    payload["descriptive"]["chart"]["title"] = "Current cycle state: Merrill and Pring shadow diagnostics"
    payload["history"]["title"] = "Three-model return replay: BL+cycle, risk parity, macro factor"
    payload["history"]["chart"]["title"] = "NAV curves versus the 1/3 equal-weight benchmark"
    payload["diagnostics"]["title"] = "Current weights: three professional allocation models only"
    payload["diagnostics"]["chart"]["title"] = "Latest weights: equity / government bond / ex-gold commodity"
    payload["strategy"]["title"] = "Recommended research model: macro factor, with risk parity as Sharpe reference"
    payload["strategy"]["chart"]["title"] = "Recommended model versus equal-weight benchmark and cycle factor comparator"
    return payload


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    previous_order = _base.MODEL_ORDER
    previous_colors = dict(_base.COLORS)
    try:
        _base.MODEL_ORDER = MODEL_ORDER
        _base.COLORS.update(COLORS)
        return _retitle(_base.build(data, metrics=metrics, page=page))
    finally:
        _base.MODEL_ORDER = previous_order
        _base.COLORS.clear()
        _base.COLORS.update(previous_colors)
