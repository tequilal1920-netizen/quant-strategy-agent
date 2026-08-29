"""Graph-first visual adapter for v6.1 four-asset cycle allocation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


MODEL_ORDER = ("black_litterman", "risk_parity", "macro_factor")
SPLIT_ORDER = ("train", "validation", "test_report_only", "full")
SPLIT_LABELS = {"train": "训练期", "validation": "验证期", "test_report_only": "报告期", "full": "全区间"}
COLORS = {
    "black_litterman": "#c00000",
    "risk_parity": "#7f7f7f",
    "macro_factor": "#7030a0",
    "equal_weight_4_assets": "#98a2b3",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _asset_order(data: Mapping[str, Any]) -> list[str]:
    order = [str(item) for item in data.get("asset_order") or []]
    if order != ["equity", "bond", "gold", "commodity"]:
        raise ValueError("asset_allocation_v61_visual_requires_four_assets")
    return order


def _asset_labels(data: Mapping[str, Any]) -> list[str]:
    labels = data.get("asset_labels") or {}
    return [str(labels.get(asset) or asset) for asset in _asset_order(data)]


def _trace(name: str, x: Sequence[Any], y: Sequence[Any], *, color: str | None = None, kind: str = "scatter") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "x": list(x), "y": [_num(item) for item in y], "type": kind}
    if color:
        row["color"] = color
    return row


def _nav_trace(name: str, rows: Sequence[Mapping[str, Any]], *, color: str) -> dict[str, Any]:
    return _trace(name, [row.get("month") for row in rows], [row.get("nav") for row in rows], color=color)


def _model_items(data: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    models = data.get("allocation_models") or {}
    return [(key, models.get(key) or {}) for key in MODEL_ORDER]


def _weights(data: Mapping[str, Any], model: Mapping[str, Any]) -> list[float]:
    current = model.get("current_weights") or {}
    return [_num(current.get(asset)) for asset in _asset_order(data)]


def _metric(model: Mapping[str, Any], split: str, key: str) -> float:
    return _num(((model.get("metrics") or {}).get(split) or {}).get(key))


def _table(columns: Sequence[tuple[str, str, str]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"columns": [{"key": k, "label": l, "format": f} for k, l, f in columns], "rows": [dict(r) for r in rows]}


def _factor_rows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(((data.get("cycle_tracking") or {}).get("factor_rows") or []))


def _strategy_rows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, model in _model_items(data):
        metrics = model.get("metrics") or {}
        current = model.get("current_weights") or {}
        row = {
            "model": model.get("name") or key,
            "role": model.get("role") or "",
            "governance": model.get("governance") or "",
            "full_sharpe": _num((metrics.get("full") or {}).get("sharpe")),
            "full_annual_return": _num((metrics.get("full") or {}).get("annual_return")),
            "full_excess_vs_equal": _num((metrics.get("full") or {}).get("annual_excess_return")),
            "validation_sharpe": _num((metrics.get("validation") or {}).get("sharpe")),
        }
        for asset in _asset_order(data):
            row[asset] = _num(current.get(asset))
        rows.append(row)
    return rows


def _drawdown_trace(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    peak = 1.0
    out = []
    for row in rows:
        nav = _num(row.get("nav"), 1.0)
        peak = max(peak, nav)
        out.append(nav / peak - 1.0)
    return out


def _workflow_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    steps = ["数据", "周期", "协方差", "观点/预算", "优化器", "回测", "生产"]
    y_labels = []
    z = []
    text = []
    for key, model in _model_items(data):
        y_labels.append(str(model.get("name") or key))
        if key == "black_litterman":
            z.append([0.6, 0.9, 1.0, 1.0, 1.0, 0.9, 0.0])
            text.append(["D2面板+宏观库", "美林+普林格", "稳健Σ", "P/Q/Omega", "TE/换手/成本", "T/V/报告", "D3/PIT未闭环"])
        elif key == "risk_parity":
            z.append([0.6, 0.0, 1.0, 1.0, 1.0, 0.8, 0.0])
            text.append(["D2面板", "不使用周期", "稳健Σ", "ERC预算", "同成本", "诊断", "研究服务"])
        else:
            z.append([0.6, 0.8, 1.0, 0.9, 1.0, 0.9, 0.0])
            text.append(["D2宏观+市场", "两周期", "稳健Σ", "六因子alpha+RP锚", "约束优化", "T/V/报告", "D3/PIT未闭环"])
    return {"title": "三模型流程完整度：BL / 风险平价 / 宏观因子调整", "heatmap": {"x": steps, "y": y_labels, "z": z, "text": text, "zmin": 0, "zmax": 1}}


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    del metrics, page
    assets = _asset_order(data)
    asset_names = _asset_labels(data)
    cycle = data.get("cycle_tracking") or {}
    cycles = cycle.get("cycles") or []
    models = dict(_model_items(data))
    benchmark = (data.get("benchmarks") or {}).get("equal_weight_4_assets") or {}

    cycle_names = [str(row.get("cycle")) for row in cycles]
    cycle_probs = [_num(row.get("display_probability")) for row in cycles]
    bias_z = [[_num((row.get("asset_bias") or {}).get(asset)) for asset in assets] for row in cycles]
    bias_text = [[f"{value:+.1%}" for value in row] for row in bias_z]
    factor_rows = _factor_rows(data)
    factor_y = [f"{row.get('cycle')}|{row.get('pillar')}|{row.get('factor')}" for row in factor_rows]
    factor_z = []
    factor_text = []
    for row in factor_rows:
        status = str(row.get("current_data_status") or "")
        enters = str(row.get("enters_current_weight") or "")
        d2 = 1 if "D2已计算" in status else 0
        prod = 1 if "production" in enters else 0
        factor_z.append([d2, 1 if "yes" in enters else 0, prod])
        factor_text.append([status, enters, row.get("pit_requirement") or ""])

    nav_traces = [_nav_trace("四资产等权25%基准", benchmark.get("nav") or [], color=COLORS["equal_weight_4_assets"])]
    for key, model in _model_items(data):
        nav_traces.append(_nav_trace(str(model.get("name") or key), model.get("nav") or [], color=COLORS[key]))

    descriptive = {
        "title": "周期跟踪：美林时钟 + 普林格周期（四资产）",
        "display": "charts_only",
        "table": _table(
            [
                ("cycle", "模型", "text"),
                ("pillar", "因子大类", "text"),
                ("factor", "因子", "text"),
                ("source_priority", "数据优先级", "text"),
                ("current_data_status", "当前数据状态", "status"),
                ("processing", "处理方法", "text"),
                ("enters_current_weight", "是否入当前权重", "status"),
            ],
            factor_rows,
        ),
        "chart": {"title": "当前周期置信度：美林四阶段 + 普林格六阶段", "traces": [_trace("当前置信度", cycle_names, cycle_probs, color="#c00000", kind="bar")]},
        "secondary_charts": [
            {"title": "周期到四资产映射：股票/债券/黄金/商品", "heatmap": {"x": asset_names, "y": cycle_names, "z": bias_z, "text": bias_text, "zmin": -1, "zmax": 1, "zmid": 0}},
            {"title": "因子准入热力图：D2可计算 / 入研究权重 / 生产D3", "heatmap": {"x": ["D2可计算", "当前研究权重", "生产D3"], "y": factor_y, "z": factor_z, "text": factor_text, "zmin": 0, "zmax": 1}},
        ],
    }

    history = {
        "title": "收益复盘：三模型与四资产等权基准",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("full_annual_return", "全区间年化", "percent"),
                ("full_sharpe", "全区间夏普", "number"),
                ("full_excess_vs_equal", "相对等权超额", "percent"),
                ("validation_sharpe", "验证夏普", "number"),
                ("governance", "治理口径", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {"title": "净值曲线：BL / 风险平价 / 宏观因子调整 / 四资产等权", "traces": nav_traces},
        "secondary_charts": [
            {"title": "训练/验证/报告/全区间夏普", "traces": [_trace(SPLIT_LABELS[s], [models[k].get("name") for k in MODEL_ORDER], [_metric(models[k], s, "sharpe") for k in MODEL_ORDER], kind="bar") for s in SPLIT_ORDER]},
            {"title": "相对四资产等权的年化超额", "traces": [_trace(SPLIT_LABELS[s], [models[k].get("name") for k in MODEL_ORDER], [_metric(models[k], s, "annual_excess_return") for k in MODEL_ORDER], kind="bar") for s in SPLIT_ORDER]},
        ],
    }

    weight_cols = [("model", "模型", "text"), ("role", "角色", "text")]
    weight_cols.extend((asset, label, "percent") for asset, label in zip(assets, asset_names))
    weight_cols.append(("governance", "治理", "text"))
    diagnostics = {
        "title": "资产配置模型：周期BL、风险平价、宏观因子调整",
        "display": "charts_only",
        "table": _table(weight_cols, _strategy_rows(data)),
        "chart": {"title": "当前四资产权重", "traces": [_trace(str(m.get("name") or k), asset_names, _weights(data, m), color=COLORS[k], kind="bar") for k, m in _model_items(data)]},
        "secondary_charts": [
            {"title": "相对25%等权基准的高低配", "traces": [_trace(str(m.get("name") or k), asset_names, [_num((m.get("active_vs_policy") or {}).get(a)) for a in assets], color=COLORS[k], kind="bar") for k, m in _model_items(data)]},
            _workflow_chart(data),
            {"title": "收益-回撤-夏普诊断", "traces": [{"name": str(m.get("name") or k), "x": [_metric(m, "full", "max_drawdown")], "y": [_metric(m, "full", "sharpe")], "type": "scatter", "mode": "markers+text", "text": [str(m.get("name") or k)], "color": COLORS[k], "marker_size": 11} for k, m in _model_items(data)]},
        ],
    }

    recommended_key = str((data.get("recommended") or {}).get("primary_model") or "macro_factor")
    recommended = models.get(recommended_key) or {}
    strategy = {
        "title": "最终推荐：以正超额和夏普综合排序，当前推荐宏观因子调整",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("full_annual_return", "年化收益", "percent"),
                ("full_sharpe", "Sharpe", "number"),
                ("full_excess_vs_equal", "相对四资产等权超额", "percent"),
                ("governance", "状态", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {"title": "推荐模型 vs 四资产等权基准", "traces": [_nav_trace("四资产等权25%基准", benchmark.get("nav") or [], color=COLORS["equal_weight_4_assets"]), _nav_trace(str(recommended.get("name") or recommended_key), recommended.get("nav") or [], color=COLORS.get(recommended_key, "#7030a0"))]},
        "secondary_charts": [
            {"title": "推荐模型当前权重", "traces": [_trace(str(recommended.get("name") or recommended_key), asset_names, _weights(data, recommended), color=COLORS.get(recommended_key, "#7030a0"), kind="bar"), _trace("四资产等权25%", asset_names, [0.25, 0.25, 0.25, 0.25], color=COLORS["equal_weight_4_assets"], kind="bar")]},
            {"title": "报告期回撤：只报告，不用于调参", "traces": [_trace(str(m.get("name") or k), [row.get("month") for row in m.get("nav") or []], _drawdown_trace(m.get("nav") or []), color=COLORS[k]) for k, m in _model_items(data)]},
            {"title": "当前两周期综合资产排序", "traces": [_trace("综合得分", asset_names, [_num((cycle.get("combined_scores") or {}).get(a)) for a in assets], color="#c00000", kind="bar")]},
        ],
    }
    return {"descriptive": descriptive, "history": history, "diagnostics": diagnostics, "strategy": strategy}
