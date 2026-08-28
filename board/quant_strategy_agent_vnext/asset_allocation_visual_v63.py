"""Graph-first visual adapter for v6.3 real-chain asset allocation."""

from __future__ import annotations

from typing import Any, Mapping

from asset_allocation_visual_v61 import COLORS, MODEL_ORDER, _metric, _num, _trace, build as build_v61


def _axis_selection_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    engine = ((data.get("cycle_tracking") or {}).get("factor_engine") or {})
    selected = engine.get("selected_by_axis") or {}
    x = list(selected.keys())
    y = [len(selected.get(axis) or []) for axis in x]
    return {"title": "八大轴训练窗筛选入模因子数", "traces": [_trace("入模因子数", x, y, color="#c00000", kind="bar")]}


def _truth_gate_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    quality = data.get("data_quality") or {}
    d2 = float(quality.get("actual_computed_factor_count") or 0)
    selected = float(quality.get("selected_research_factor_count") or 0)
    prod = float(quality.get("production_admitted_macro_factor_count") or 0)
    return {
        "title": "数据真实性门：实算D2 / 研究入模 / 生产D3",
        "traces": [
            _trace("因子数量", ["D2实算候选", "训练窗入模", "生产D3/PIT"], [d2, selected, prod], color="#c00000", kind="bar")
        ],
    }


def _pretest_gate_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    models = data.get("allocation_models") or {}
    x = [str((models.get(key) or {}).get("name") or key) for key in MODEL_ORDER]
    return {
        "title": "训练/验证门禁：Sharpe、超额、IR只用2021年前选模",
        "traces": [
            _trace("训练Sharpe", x, [_metric(models.get(k) or {}, "train", "sharpe") for k in MODEL_ORDER], color="#7f7f7f", kind="bar"),
            _trace("验证Sharpe", x, [_metric(models.get(k) or {}, "validation", "sharpe") for k in MODEL_ORDER], color="#c00000", kind="bar"),
            _trace("验证超额", x, [_metric(models.get(k) or {}, "validation", "annual_excess_return") for k in MODEL_ORDER], color="#7030a0", kind="bar"),
        ],
    }


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    visuals = build_v61(data, metrics=metrics, page=page)
    descriptive = visuals.get("descriptive") or {}
    descriptive.setdefault("secondary_charts", [])
    descriptive["secondary_charts"].append(_axis_selection_chart(data))
    descriptive["secondary_charts"].append(_truth_gate_chart(data))
    diagnostics = visuals.get("diagnostics") or {}
    diagnostics.setdefault("secondary_charts", [])
    diagnostics["secondary_charts"].insert(0, _pretest_gate_chart(data))
    strategy = visuals.get("strategy") or {}
    recommended_key = str((data.get("recommended") or {}).get("primary_model") or "macro_factor")
    recommended = (data.get("allocation_models") or {}).get(recommended_key) or {}
    strategy["title"] = f"最终推荐：{recommended.get('name') or recommended_key}（训练/验证门禁通过，报告期只展示）"
    note = (
        "v6.3已经形成D2真实因子→周期阶段→资产映射→BL/宏观调控→回测闭环；"
        "但Wind/iFinD/RQ release-vintage与跨源hash未闭环，生产D3仍为0。"
    )
    for block in (descriptive, diagnostics, strategy):
        block["note"] = note if not block.get("note") else f"{block.get('note')}；{note}"
    return visuals
