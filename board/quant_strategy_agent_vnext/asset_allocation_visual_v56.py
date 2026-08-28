"""Graph-first visual adapter for asset allocation v5.6."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


ASSET_ORDER = ("equity", "bond", "gold", "commodity")
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
    "equal_weight_25": "#98a2b3",
    "policy_60_15_10_15": "#163d7a",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _trace(name: str, x: Sequence[Any], y: Sequence[Any], *, color: str | None = None, kind: str = "scatter", axis: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "x": list(x), "y": [_num(item) for item in y], "type": kind}
    if color:
        row["color"] = color
    if axis:
        row["axis"] = axis
    return row


def _nav_trace(name: str, rows: Sequence[Mapping[str, Any]], *, color: str) -> dict[str, Any]:
    return _trace(name, [row.get("month") for row in rows], [row.get("nav") for row in rows], color=color)


def _weights(model: Mapping[str, Any]) -> list[float]:
    current = model.get("current_weights") or {}
    return [_num(current.get(asset)) for asset in ASSET_ORDER]


def _asset_labels(data: Mapping[str, Any]) -> list[str]:
    labels = data.get("asset_labels") or {}
    return [str(labels.get(asset) or asset) for asset in ASSET_ORDER]


def _model_items(data: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    models = data.get("allocation_models") or {}
    keys = ["black_litterman", "risk_parity", "all_weather", "macro_factor"]
    return [(key, models.get(key) or {}) for key in keys]


def _metric(model: Mapping[str, Any], split: str, key: str) -> float:
    return _num(((model.get("metrics") or {}).get(split) or {}).get(key))


def _table(columns: Sequence[tuple[str, str, str]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "columns": [{"key": key, "label": label, "format": fmt} for key, label, fmt in columns],
        "rows": [dict(row) for row in rows],
    }


def _cycle_rows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(((data.get("cycle_tracking") or {}).get("factor_rows") or []))


def _strategy_rows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, model in _model_items(data):
        metrics = model.get("metrics") or {}
        current = model.get("current_weights") or {}
        rows.append(
            {
                "model": model.get("name") or key,
                "role": model.get("role") or "",
                "governance": model.get("governance") or "",
                "full_sharpe": _num((metrics.get("full") or {}).get("sharpe")),
                "full_annual_return": _num((metrics.get("full") or {}).get("annual_return")),
                "full_excess_vs_equal": _num((metrics.get("full") or {}).get("annual_excess_return")),
                "validation_sharpe": _num((metrics.get("validation") or {}).get("sharpe")),
                "equity": _num(current.get("equity")),
                "bond": _num(current.get("bond")),
                "gold": _num(current.get("gold")),
                "commodity": _num(current.get("commodity")),
            }
        )
    return rows


def _workflow_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    steps = ["数据/PIT", "周期", "协方差", "观点/预算", "优化器", "选模", "生产"]
    model_labels = []
    z = []
    text = []
    for key, model in _model_items(data):
        model_labels.append(str(model.get("name") or key))
        if key == "black_litterman":
            row = [0.5, 0.2, 1.0, 1.0, 1.0, 0.8, 0.0]
            txt = ["D2", "周期不生产", "稳健Σ", "BL+Omega", "成本约束", "T/V", "未晋级"]
        elif key == "risk_parity":
            row = [0.5, 0.0, 1.0, 1.0, 1.0, 0.5, 0.0]
            txt = ["D2", "不使用", "稳健Σ", "严格ERC", "成本执行", "诊断", "未晋级"]
        elif key == "all_weather":
            row = [0.5, 0.0, 0.5, 0.5, 0.8, 0.5, 0.0]
            txt = ["D2", "不使用", "固定规则", "防守袖套", "同成本", "基线", "未晋级"]
        else:
            row = [0.5, 0.7, 0.5, 0.7, 0.8, 0.5, 0.0]
            txt = ["D2", "普林格五", "场景Σ", "滞涨映射", "同成本", "影子", "未晋级"]
        z.append(row)
        text.append(txt)
    return {
        "title": "四类模型流程完整度：BL、风险平价、全天候、宏观因子",
        "heatmap": {"x": steps, "y": model_labels, "z": z, "text": text, "zmin": 0, "zmax": 1},
    }


def _drawdown_trace(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    peak = 1.0
    out = []
    for row in rows:
        nav = _num(row.get("nav"), 1.0)
        peak = max(peak, nav)
        out.append(nav / peak - 1.0)
    return out


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    del metrics, page
    asset_names = _asset_labels(data)
    cycle = data.get("cycle_tracking") or {}
    cycles = cycle.get("cycles") or []
    models = dict(_model_items(data))
    benchmarks = data.get("benchmarks") or {}
    equal = benchmarks.get("equal_weight_25") or {}
    policy = benchmarks.get("policy_60_15_10_15") or {}

    cycle_names = [str(row.get("cycle")) for row in cycles]
    cycle_probs = [_num(row.get("display_probability")) for row in cycles]
    bias_z = [[_num((row.get("asset_bias") or {}).get(asset)) for asset in ASSET_ORDER] for row in cycles]
    bias_text = [[f"{value:+.0%}" for value in row] for row in bias_z]

    factor_rows = _cycle_rows(data)
    factor_y = [f"{row.get('cycle')}|{row.get('pillar')}" for row in factor_rows]
    factor_z = []
    factor_text = []
    for row in factor_rows:
        shadow = 1 if "影子" in str(row.get("view_scope")) else 0
        production = 1 if "生产" in str(row.get("enters_allocation")) else 0
        pit = 0 if "缺" in str(row.get("data_status")) or "待" in str(row.get("data_status")) else 0.5
        factor_z.append([pit, shadow, production])
        factor_text.append([str(row.get("data_status")), str(row.get("view_scope")), str(row.get("enters_allocation"))])

    nav_chart_traces = [
        _nav_trace("四资产等权（仅展示）", equal.get("nav") or [], color=COLORS["equal_weight_25"]),
    ]
    for key, model in _model_items(data):
        nav_chart_traces.append(_nav_trace(str(model.get("name") or key), model.get("nav") or [], color=COLORS[key]))

    descriptive = {
        "title": "五周期跟踪：当前普林格=第五阶段滞涨",
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
            "title": "五周期当前阶段概率/置信显示（普林格按第五阶段滞涨校准）",
            "x_title": "周期",
            "y_title": "概率/置信显示",
            "traces": [_trace("当前阶段显示概率", cycle_names, cycle_probs, color="#c00000", kind="bar")],
        },
        "secondary_charts": [
            {
                "title": "周期因子D3/PIT准入热力图（生产列当前均未准入）",
                "heatmap": {"x": ["PIT可用", "影子研究", "生产入权重"], "y": factor_y, "z": factor_z, "text": factor_text, "zmin": 0, "zmax": 1},
            },
            {
                "title": "当前周期到资产映射：滞涨方向=商品/黄金占优",
                "heatmap": {"x": asset_names, "y": cycle_names, "z": bias_z, "text": bias_text, "zmin": -0.15, "zmax": 0.15, "zmid": 0},
            },
        ],
    }

    history = {
        "title": "四模型收益复盘：所有曲线同一v553四资产面板",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("full_annual_return", "全区间年化", "percent"),
                ("full_sharpe", "全区间夏普", "number"),
                ("full_excess_vs_equal", "相对等权年化超额", "percent"),
                ("validation_sharpe", "验证夏普", "number"),
                ("governance", "治理口径", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {"title": "净值曲线：BL、风险平价、全天候、宏观因子与等权展示基准", "traces": nav_chart_traces},
        "secondary_charts": [
            {
                "title": "训练/验证/报告/全区间夏普",
                "x_title": "模型",
                "y_title": "Sharpe",
                "traces": [
                    _trace(SPLIT_LABELS[split], [models[key].get("name") for key in models], [_metric(models[key], split, "sharpe") for key in models], kind="bar")
                    for split in SPLIT_ORDER
                ],
            },
            {
                "title": "相对等权展示基准的年化超额（等权仅作图中对照）",
                "x_title": "模型",
                "y_title": "年化超额",
                "traces": [
                    _trace(SPLIT_LABELS[split], [models[key].get("name") for key in models], [_metric(models[key], split, "annual_excess_return") for key in models], kind="bar")
                    for split in SPLIT_ORDER
                ],
            },
        ],
    }

    diagnostics = {
        "title": "四类资产配置模型：BL、风险平价、全天候、宏观因子",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("role", "角色", "text"),
                ("equity", "权益", "percent"),
                ("bond", "国债", "percent"),
                ("gold", "黄金", "percent"),
                ("commodity", "商品", "percent"),
                ("governance", "治理", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {
            "title": "最新权重：四个模型独立输出（内部顺序权益/国债/黄金/商品）",
            "x_title": "资产",
            "y_title": "权重",
            "traces": [
                _trace(str(model.get("name") or key), asset_names, _weights(model), color=COLORS[key], kind="bar")
                for key, model in _model_items(data)
            ],
        },
        "secondary_charts": [
            {
                "title": "相对政策基准60/15/10/15的高低配",
                "x_title": "资产",
                "y_title": "主动权重",
                "traces": [
                    _trace(str(model.get("name") or key), asset_names, [_num((model.get("active_vs_policy") or {}).get(asset)) for asset in ASSET_ORDER], color=COLORS[key], kind="bar")
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

    recommended = str((data.get("recommended") or {}).get("primary_model") or "risk_parity")
    recommended_model = models.get(recommended) or {}
    macro_model = models.get("macro_factor") or {}
    strategy = {
        "title": "最终策略：Sharpe冠军与滞涨周期一致性双口径展示",
        "display": "charts_only",
        "table": _table(
            [
                ("model", "模型", "text"),
                ("full_annual_return", "年化收益", "percent"),
                ("full_sharpe", "Sharpe", "number"),
                ("full_excess_vs_equal", "相对等权超额", "percent"),
                ("governance", "状态", "text"),
            ],
            _strategy_rows(data),
        ),
        "chart": {
            "title": "推荐观察：风险平价为Sharpe冠军；宏观因子为普林格第五阶段滞涨一致方案",
            "traces": [
                _nav_trace("等权展示基准", equal.get("nav") or [], color=COLORS["equal_weight_25"]),
                _nav_trace(str(recommended_model.get("name") or "风险平价"), recommended_model.get("nav") or [], color=COLORS.get(recommended, "#7f7f7f")),
                _nav_trace(str(macro_model.get("name") or "宏观因子"), macro_model.get("nav") or [], color=COLORS["macro_factor"]),
            ],
        },
        "secondary_charts": [
            {
                "title": "当前最终权重对照：Sharpe冠军 vs 滞涨周期一致方案",
                "x_title": "资产",
                "y_title": "权重",
                "traces": [
                    _trace(str(recommended_model.get("name") or "Sharpe冠军"), asset_names, _weights(recommended_model), color=COLORS.get(recommended, "#7f7f7f"), kind="bar"),
                    _trace("宏观因子（普林格五）", asset_names, _weights(macro_model), color=COLORS["macro_factor"], kind="bar"),
                    _trace("政策基准60/15/10/15", asset_names, _weights(policy), color=COLORS["policy_60_15_10_15"], kind="bar"),
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
                "title": "当前周期资产强弱：普林格第五阶段滞涨",
                "x_title": "资产",
                "y_title": "强弱倾向",
                "traces": [_trace("滞涨映射强弱", asset_names, [-0.15, -0.05, 0.10, 0.10], color="#c00000", kind="bar")],
            },
        ],
    }
    return {"descriptive": descriptive, "history": history, "diagnostics": diagnostics, "strategy": strategy}
