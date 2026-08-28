"""Graph-first visual adapter for the v5.8 no-gold asset-allocation snapshot."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


MODEL_ORDER = ("black_litterman", "risk_parity", "all_weather", "macro_factor")
SPLIT_ORDER = ("train", "validation", "test_report_only", "full")
SPLIT_LABELS = {
    "train": "训练期",
    "validation": "验证期",
    "test_report_only": "报告期",
    "full": "全区间",
}
COLORS = {
    "black_litterman": "#c00000",
    "risk_parity": "#7f7f7f",
    "all_weather": "#ffc000",
    "macro_factor": "#7030a0",
    "equal_weight_3_assets": "#98a2b3",
    "equal_anchor_1_3_1_3_1_3": "#163d7a",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _asset_order(data: Mapping[str, Any]) -> list[str]:
    order = [str(item) for item in data.get("asset_order") or []]
    if order != ["equity", "bond", "commodity"]:
        raise ValueError("asset_allocation_v58_visual_requires_three_assets_no_gold")
    return order


def _asset_labels(data: Mapping[str, Any]) -> list[str]:
    labels = data.get("asset_labels") or {}
    return [str(labels.get(asset) or asset) for asset in _asset_order(data)]


def _trace(
    name: str,
    x: Sequence[Any],
    y: Sequence[Any],
    *,
    color: str | None = None,
    kind: str = "scatter",
    axis: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "x": list(x), "y": [_num(item) for item in y], "type": kind}
    if color:
        row["color"] = color
    if axis:
        row["axis"] = axis
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
    return {
        "columns": [{"key": key, "label": label, "format": fmt} for key, label, fmt in columns],
        "rows": [dict(row) for row in rows],
    }


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
    steps = ["数据/PIT", "周期", "协方差", "观点/预算", "优化器", "选模", "生产"]
    y_labels = []
    z = []
    text = []
    for key, model in _model_items(data):
        y_labels.append(str(model.get("name") or key))
        if key == "black_litterman":
            z.append([0.5, 0.3, 1.0, 1.0, 1.0, 0.8, 0.0])
            text.append(["D2", "周期影子", "稳健Σ", "两条BL相对观点", "成本/TE约束", "T/V", "未晋级"])
        elif key == "risk_parity":
            z.append([0.5, 0.0, 1.0, 1.0, 1.0, 0.5, 0.0])
            text.append(["D2", "不使用", "稳健Σ", "三资产ERC", "成本执行", "诊断", "未晋级"])
        elif key == "all_weather":
            z.append([0.5, 0.0, 0.5, 0.5, 0.8, 0.5, 0.0])
            text.append(["D2", "不使用", "固定规则", "防守袖套", "同成本", "基线", "未晋级"])
        else:
            z.append([0.5, 0.7, 0.5, 0.7, 0.8, 0.5, 0.0])
            text.append(["D2", "普林格五", "场景Σ", "商品占优", "同成本", "影子", "未晋级"])
    return {
        "title": "四类模型流程完整度：BL、风险平价、全天候、宏观因子（三资产无黄金）",
        "heatmap": {"x": steps, "y": y_labels, "z": z, "text": text, "zmin": 0, "zmax": 1},
    }


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    del metrics, page
    assets = _asset_order(data)
    asset_names = _asset_labels(data)
    cycle = data.get("cycle_tracking") or {}
    cycles = cycle.get("cycles") or []
    models = dict(_model_items(data))
    benchmarks = data.get("benchmarks") or {}
    equal = benchmarks.get("equal_weight_3_assets") or {}
    policy = benchmarks.get("equal_anchor_1_3_1_3_1_3") or equal

    cycle_names = [str(row.get("cycle")) for row in cycles]
    cycle_probs = [_num(row.get("display_probability")) for row in cycles]
    bias_z = [[_num((row.get("asset_bias") or {}).get(asset)) for asset in assets] for row in cycles]
    bias_text = [[f"{value:+.0%}" for value in row] for row in bias_z]

    factor_rows = _factor_rows(data)
    factor_y = [f"{row.get('cycle')}|{row.get('pillar')}" for row in factor_rows]
    factor_z = []
    factor_text = []
    for row in factor_rows:
        view_scope = str(row.get("view_scope") or "")
        data_status = str(row.get("data_status") or "")
        enters = str(row.get("enters_allocation") or "")
        shadow = 1 if "影子" in view_scope else 0
        production = 1 if "生产" in enters and "不" not in enters else 0
        pit = 0 if "缺" in data_status or "待补" in data_status else 0.5
        factor_z.append([pit, shadow, production])
        factor_text.append([data_status, view_scope, enters])

    nav_traces = [_nav_trace("三资产等权（仅展示）", equal.get("nav") or [], color=COLORS["equal_weight_3_assets"])]
    for key, model in _model_items(data):
        nav_traces.append(_nav_trace(str(model.get("name") or key), model.get("nav") or [], color=COLORS[key]))

    descriptive = {
        "title": "五周期跟踪：三资产无黄金，当前普林格=第五阶段滞涨",
        "display": "charts_only",
        "table": _table(
            [
                ("cycle", "周期", "text"),
                ("pillar", "支柱", "text"),
                ("factor", "因子", "text"),
                ("source", "数据源", "text"),
                ("data_status", "D3/PIT状态", "status"),
                ("current_stage", "当前阶段", "text"),
                ("enters_allocation", "进入配置", "status"),
            ],
            factor_rows,
        ),
        "chart": {
            "title": "五周期当前阶段概率/置信显示（普林格第五阶段：滞涨）",
            "x_title": "周期",
            "y_title": "概率/置信显示",
            "traces": [_trace("当前阶段显示概率", cycle_names, cycle_probs, color="#c00000", kind="bar")],
        },
        "secondary_charts": [
            {
                "title": "周期因子D3/PIT准入热力图（当前生产列均未准入）",
                "heatmap": {
                    "x": ["PIT可用", "影子研究", "生产入权重"],
                    "y": factor_y,
                    "z": factor_z,
                    "text": factor_text,
                    "zmin": 0,
                    "zmax": 1,
                },
            },
            {
                "title": "当前周期到资产映射：滞涨方向=商品占优，黄金已删除",
                "heatmap": {"x": asset_names, "y": cycle_names, "z": bias_z, "text": bias_text, "zmin": -0.20, "zmax": 0.20, "zmid": 0},
            },
        ],
    }

    history = {
        "title": "三资产无黄金收益复盘：所有曲线使用同一v553面板的权益/国债/商品三列",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("full_annual_return", "全区间年化", "percent"),
                ("full_sharpe", "全区间夏普", "number"),
                ("full_excess_vs_equal", "相对三资产等权年化超额", "percent"),
                ("validation_sharpe", "验证夏普", "number"),
                ("governance", "治理口径", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {"title": "净值曲线：BL、风险平价、全天候、宏观因子与三资产等权展示基准", "traces": nav_traces},
        "secondary_charts": [
            {
                "title": "训练/验证/报告/全区间夏普",
                "x_title": "模型",
                "y_title": "Sharpe",
                "traces": [
                    _trace(
                        SPLIT_LABELS[split],
                        [models[key].get("name") for key in MODEL_ORDER],
                        [_metric(models[key], split, "sharpe") for key in MODEL_ORDER],
                        kind="bar",
                    )
                    for split in SPLIT_ORDER
                ],
            },
            {
                "title": "相对三资产等权展示基准的年化超额（等权仅作图中对照）",
                "x_title": "模型",
                "y_title": "年化超额",
                "traces": [
                    _trace(
                        SPLIT_LABELS[split],
                        [models[key].get("name") for key in MODEL_ORDER],
                        [_metric(models[key], split, "annual_excess_return") for key in MODEL_ORDER],
                        kind="bar",
                    )
                    for split in SPLIT_ORDER
                ],
            },
        ],
    }

    weight_columns = [("model", "模型", "text"), ("role", "角色", "text")]
    weight_columns.extend((asset, label, "percent") for asset, label in zip(assets, asset_names))
    weight_columns.append(("governance", "治理", "text"))
    diagnostics = {
        "title": "三资产配置模型：BL、风险平价、全天候、宏观因子",
        "display": "charts_only",
        "table": _table(weight_columns, _strategy_rows(data)),
        "chart": {
            "title": "最新权重：四个模型独立输出（权益/国债/商品，黄金已删除）",
            "x_title": "资产",
            "y_title": "权重",
            "traces": [
                _trace(str(model.get("name") or key), asset_names, _weights(data, model), color=COLORS[key], kind="bar")
                for key, model in _model_items(data)
            ],
        },
        "secondary_charts": [
            {
                "title": "相对三资产等权锚1/3/1/3/1/3的高低配",
                "x_title": "资产",
                "y_title": "主动权重",
                "traces": [
                    _trace(
                        str(model.get("name") or key),
                        asset_names,
                        [_num((model.get("active_vs_policy") or {}).get(asset)) for asset in assets],
                        color=COLORS[key],
                        kind="bar",
                    )
                    for key, model in _model_items(data)
                ],
            },
            _workflow_chart(data),
            {
                "title": "收益-风险散点：越靠左上越优",
                "x_title": "最大回撤",
                "y_title": "全区间夏普",
                "traces": [
                    {
                        "name": str(model.get("name") or key),
                        "x": [_metric(model, "full", "max_drawdown")],
                        "y": [_metric(model, "full", "sharpe")],
                        "type": "scatter",
                        "mode": "markers+text",
                        "text": [str(model.get("name") or key)],
                        "color": COLORS[key],
                        "marker_size": 11,
                    }
                    for key, model in _model_items(data)
                ],
            },
        ],
    }

    recommended_key = str((data.get("recommended") or {}).get("primary_model") or "risk_parity")
    recommended_model = models.get(recommended_key) or {}
    macro_model = models.get("macro_factor") or {}
    strategy = {
        "title": "最终策略：等权锚主动超额优先，兼看Sharpe与滞涨周期一致性（三资产无黄金）",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("full_annual_return", "年化收益", "percent"),
                ("full_sharpe", "Sharpe", "number"),
                ("full_excess_vs_equal", "相对三资产等权超额", "percent"),
                ("governance", "状态", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {
            "title": "推荐观察：BL为等权锚正超额推荐；风险平价为Sharpe/回撤对照；宏观因子为滞涨一致方案",
            "traces": [
                _nav_trace("三资产等权展示基准", equal.get("nav") or [], color=COLORS["equal_weight_3_assets"]),
                _nav_trace(str(recommended_model.get("name") or "风险平价"), recommended_model.get("nav") or [], color=COLORS.get(recommended_key, "#7f7f7f")),
                _nav_trace(str(macro_model.get("name") or "宏观因子"), macro_model.get("nav") or [], color=COLORS["macro_factor"]),
            ],
        },
        "secondary_charts": [
            {
                "title": "当前最终权重对照：等权锚BL推荐 vs 滞涨周期一致方案",
                "x_title": "资产",
                "y_title": "权重",
                "traces": [
                    _trace(str(recommended_model.get("name") or "等权锚推荐"), asset_names, _weights(data, recommended_model), color=COLORS.get(recommended_key, "#7f7f7f"), kind="bar"),
                    _trace("宏观因子（普林格五）", asset_names, _weights(data, macro_model), color=COLORS["macro_factor"], kind="bar"),
                    _trace("等权锚1/3/1/3/1/3", asset_names, _weights(data, policy), color=COLORS["equal_anchor_1_3_1_3_1_3"], kind="bar"),
                ],
            },
            {
                "title": "报告期回撤：不用于选模，只用于风险展示",
                "x_title": "月份",
                "y_title": "回撤",
                "traces": [
                    _trace(str(model.get("name") or key), [row.get("month") for row in model.get("nav") or []], _drawdown_trace(model.get("nav") or []), color=COLORS[key])
                    for key, model in _model_items(data)
                ],
            },
            {
                "title": "当前周期资产强弱：普林格第五阶段滞涨，黄金已删除",
                "x_title": "资产",
                "y_title": "强弱倾向",
                "traces": [_trace("滞涨映射强弱", asset_names, [-0.15, -0.05, 0.20], color="#c00000", kind="bar")],
            },
        ],
    }
    return {"descriptive": descriptive, "history": history, "diagnostics": diagnostics, "strategy": strategy}
