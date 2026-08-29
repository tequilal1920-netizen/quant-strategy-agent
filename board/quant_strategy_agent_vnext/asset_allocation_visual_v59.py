"""Graph-first visual adapter for v5.9 equal-anchor no-gold model zoo."""

from __future__ import annotations

from typing import Any

import asset_allocation_visual_v58 as _base


MODEL_ORDER = ("active_rotation", "black_litterman", "risk_parity", "all_weather", "macro_factor")
COLORS = {
    "active_rotation": "#c00000",
    "black_litterman": "#7030a0",
    "risk_parity": "#7f7f7f",
    "all_weather": "#ffc000",
    "macro_factor": "#5b2c83",
    "equal_weight_3_assets": "#98a2b3",
    "equal_anchor_1_3_1_3_1_3": "#163d7a",
}


def _retitle(payload: dict[str, Any]) -> dict[str, Any]:
    visuals = payload
    visuals["history"]["title"] = "三资产等权锚模型全局收益复盘：主动轮动 + BL + 风险平价 + 全天候 + 宏观因子"
    visuals["history"]["chart"]["title"] = "净值曲线：五个模型与三资产等权基准"
    visuals["diagnostics"]["title"] = "三资产配置模型：等权锚主动轮动、BL、风险平价、全天候、宏观因子"
    visuals["diagnostics"]["chart"]["title"] = "最新权重：五个模型独立输出（权益 / 国债 / 商品；黄金已删除）"
    visuals["strategy"]["title"] = "最终策略：等权锚主动轮动优先看超额，风险平价保留为Sharpe/回撤冠军"
    visuals["strategy"]["chart"]["title"] = "推荐观察：主动轮动为超额研究冠军；风险平价为Sharpe诊断冠军；宏观因子保留周期一致性"
    return visuals


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
