"""v6.2 visual adapter: v6.1 model effect + D3/PIT truth gate."""

from __future__ import annotations

from typing import Any, Mapping

from asset_allocation_visual_v61 import build as build_v61


def _bar(name: str, x: list[str], y: list[float], color: str) -> dict[str, Any]:
    return {"name": name, "x": x, "y": y, "type": "bar", "color": color}


def _governance_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    gov = data.get("d3_pit_governance") or {}
    catalogue = float(gov.get("factor_catalogue_total") or 0)
    admitted = float(gov.get("production_admitted_factor_count") or 0)
    current = float(gov.get("current_weight_factor_count_from_new_catalog") or 0)
    pending = max(catalogue - admitted, 0.0)
    return {
        "title": "Wind/iFinD/RQ D3/PIT准入：注册很多，未验证不改权重",
        "traces": [
            _bar(
                "因子数量",
                ["宏观小因子目录", "生产D3/PIT准入", "进入当前权重", "待补D3/PIT"],
                [catalogue, admitted, current, pending],
                "#c00000",
            )
        ],
    }


def _category_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    registry = data.get("macro_factor_catalog_v62") or {}
    by_category = registry.get("by_category") or {}
    label_map = {
        "growth": "增长",
        "inflation": "通胀",
        "interest_rate": "利率",
        "credit": "信用",
        "fx": "汇率",
        "liquidity": "流动性",
    }
    keys = ["growth", "inflation", "interest_rate", "credit", "fx", "liquidity"]
    return {
        "title": "六大类宏观小因子目录：用于未来D3/PIT准入筛选",
        "traces": [_bar("已注册因子", [label_map[k] for k in keys], [float(by_category.get(k) or 0) for k in keys], "#7f7f7f")],
    }


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    visuals = build_v61(data, metrics=metrics, page=page)
    descriptive = visuals.get("descriptive") or {}
    descriptive["title"] = "周期跟踪：美林时钟 + 普林格周期 + D3/PIT宏观因子门禁"
    descriptive["note"] = (
        "v6.2补充87个宏观小因子目录和Wind/iFinD/RQ D3/PIT准入合约；"
        "未完成release-vintage与跨源hash核验前，新增因子只展示、不改变权重。"
    )
    descriptive.setdefault("secondary_charts", [])
    descriptive["secondary_charts"] = list(descriptive["secondary_charts"]) + [
        _governance_chart(data),
        _category_chart(data),
    ]

    diagnostics = visuals.get("diagnostics") or {}
    diagnostics["title"] = "资产配置模型：周期BL、风险平价、宏观因子调整（效果冻结+D3/PIT门禁）"
    diagnostics["note"] = (
        "三模型收益、权重与推荐结论冻结自v6.1；新增D3/PIT注册表是准入与实时更新层，不作为未验证alpha。"
    )

    strategy = visuals.get("strategy") or {}
    strategy["title"] = "最终推荐：保持v6.1最优结论，新增D3/PIT真实数据门禁"
    strategy["note"] = (
        "推荐模型、夏普、超额与当前权重尽量不变；待Wind/iFinD/RQ D3/PIT闭环后，才允许开启新因子再训练。"
    )
    return visuals
