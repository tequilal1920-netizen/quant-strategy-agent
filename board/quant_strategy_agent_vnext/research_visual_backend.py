"""Build compact, report-style visual evidence from frozen model snapshots."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable


DATA_ROOT = Path(__file__).resolve().parent / "data"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _sample(values: list[Any], limit: int = 180) -> list[Any]:
    if len(values) <= limit:
        return values
    indexes = {
        round(index * (len(values) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [values[index] for index in sorted(indexes)]


def _date_label(value: Any) -> Any:
    text = str(value or "")
    if text.isdigit() and len(text) == 6:
        return f"{text[:4]}-{text[4:6]}"
    if text.isdigit() and len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return value


def _paired(
    records: Iterable[dict[str, Any]],
    x_key: str,
    y_key: str,
    limit: int = 180,
) -> tuple[list[Any], list[float]]:
    pairs = []
    for row in records:
        value = row.get(y_key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number) or row.get(x_key) in (None, ""):
            continue
        pairs.append((_date_label(row.get(x_key)), number))
    pairs = _sample(pairs, limit)
    return [row[0] for row in pairs], [row[1] for row in pairs]


def _drawdown(values: Iterable[Any]) -> list[float | None]:
    peak: float | None = None
    result: list[float | None] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            result.append(None)
            continue
        if not math.isfinite(number) or number <= 0:
            result.append(None)
            continue
        peak = number if peak is None else max(peak, number)
        result.append(number / peak - 1.0)
    return result


def _trace(
    name: str,
    x: list[Any],
    y: list[Any],
    *,
    kind: str = "scatter",
    mode: str = "lines",
    axis: str = "y",
    color: str | None = None,
    text: list[Any] | None = None,
    dash: str | None = None,
    marker_size: list[float] | float | None = None,
    marker_color: list[Any] | str | None = None,
) -> dict[str, Any]:
    result = {
        "type": kind,
        "mode": mode,
        "name": name,
        "x": x,
        "y": y,
        "axis": axis,
    }
    if color:
        result["color"] = color
    if text is not None:
        result["text"] = text
    if dash:
        result["dash"] = dash
    if marker_size is not None:
        result["marker_size"] = marker_size
    if marker_color is not None:
        result["marker_color"] = marker_color
    return result


def _table(columns: list[tuple[str, str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "columns": [
            {"key": key, "label": label, "format": value_format}
            for key, label, value_format in columns
        ],
        "rows": rows,
    }


def _indicator(
    group: str,
    name: str,
    scope: str,
    dates: list[Any],
    values: list[Any],
    *,
    quality: str = "",
) -> dict[str, Any] | None:
    pairs = []
    for date, value in zip(dates, values):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            pairs.append((date, number))
    if not pairs:
        return None
    clean_values = [row[1] for row in pairs]
    latest = clean_values[-1]
    previous = clean_values[-2] if len(clean_values) > 1 else latest
    percentile = sum(value <= latest for value in clean_values) / len(clean_values)
    return {
        "group": group,
        "name": name,
        "scope": scope,
        "direction": latest - previous,
        "latest": latest,
        "change": latest - previous,
        "percentile": percentile,
        "quality": quality,
        "date": str(pairs[-1][0]),
        "trend": clean_values[-36:],
    }


INDICATOR_COLUMNS = [
    ("group", "分组", "text"),
    ("name", "指标", "text"),
    ("scope", "口径", "text"),
    ("direction", "方向", "arrow"),
    ("latest", "最新值", "number"),
    ("change", "较前值", "signed"),
    ("percentile", "历史分位", "percentile"),
    ("quality", "状态", "status"),
    ("trend", "趋势", "sparkline"),
]


def allocation_visuals(
    data: dict[str, Any],
    metrics: list[dict[str, Any]],
    page: str = "strategy",
) -> dict[str, Any]:
    if str(data.get("schema_version") or "") == "5.2.2":
        from asset_allocation_visual_v522 import build as build_v522_visuals

        return build_v522_visuals(data, metrics, page)
    monitor_rows: list[dict[str, Any]] = []
    used = set()
    role_labels = {"leading": "先行", "coincident": "同步", "lagging": "滞后"}
    roles = (data.get("factor_selection") or {}).get("roles") or {}
    series_map = data.get("factor_series") or {}
    for role in ("leading", "coincident", "lagging"):
        for item in roles.get(role) or []:
            factor_id = item.get("id")
            if factor_id in used:
                continue
            points = series_map.get(factor_id) or []
            row = _indicator(
                role_labels[role],
                str(item.get("name") or factor_id),
                "月频/PIT",
                [point.get("month") for point in points],
                [point.get("value") for point in points],
                quality=f"训练IC {_num(item.get('train_ic')):.2f}",
            )
            if row:
                monitor_rows.append(row)
                used.add(factor_id)

    strategies = (data.get("backtest") or {}).get("strategies") or {}
    selected = strategies.get("recommended") or {}
    nav_rows = selected.get("nav") or []
    dates, strategy_nav = _paired(nav_rows, "month", "nav")
    objective_champions = (data.get("backtest") or {}).get("objective_champions") or {}
    stable_key = str((objective_champions.get("stable_absolute") or {}).get("strategy") or "")
    stable_strategy = strategies.get(stable_key) or {}
    stable_rows = stable_strategy.get("nav") or []
    stable_dates, stable_nav = _paired(stable_rows, "month", "nav")
    stable_label = str((objective_champions.get("stable_absolute") or {}).get("model") or "稳健绝对")
    _, benchmark_nav = _paired(nav_rows, "month", "benchmark_nav")
    _, relative_nav = _paired(nav_rows, "month", "relative_nav")

    backtest = data.get("backtest") or {}
    audit = backtest.get("selection_audit") or {}
    candidate_rows = []
    architecture = backtest.get("architecture_comparison") or []
    if architecture:
        architecture = sorted(
            architecture,
            key=lambda row: (
                row.get("id") != "recommended",
                not bool(row.get("evidence_gate")),
                -_num(row.get("validation_excess")),
            ),
        )
        for row in architecture:
            candidate_rows.append(
                {
                    "candidate": row.get("model") or row.get("id"),
                    "family": "稳健绝对" if row.get("id") == stable_key else (row.get("status") or "架构对照"),
                    "train_sharpe": _num(row.get("train_sharpe")),
                    "validation_sharpe": _num(row.get("validation_sharpe")),
                    "train_cash_excess_sharpe": _num(row.get("train_cash_excess_sharpe")),
                    "validation_cash_excess_sharpe": _num(row.get("validation_cash_excess_sharpe")),
                    "validation_excess": _num(row.get("validation_excess")),
                    "turnover": _num(row.get("turnover")),
                    "eligible": bool(row.get("evidence_gate")),
                }
            )
    else:
        for row in audit.get("leaderboard") or []:
            candidate_rows.append(
                {
                    "candidate": row.get("id"),
                    "family": row.get("family"),
                    "train_sharpe": _num(row.get("train_sharpe")),
                    "validation_sharpe": _num(row.get("validation_sharpe")),
                    "train_cash_excess_sharpe": 0.0,
                    "validation_cash_excess_sharpe": 0.0,
                    "validation_excess": _num(row.get("validation_excess")),
                    "turnover": _num(row.get("turnover")),
                    "eligible": bool(row.get("validation_eligible")),
                }
            )

    recommended = (data.get("allocations") or {}).get("recommended") or {}
    stable_allocation = (data.get("allocations") or {}).get("stable_absolute") or {}
    weights = recommended.get("weights") or {}
    risk = recommended.get("risk_contribution") or {}
    labels = data.get("asset_labels") or {}
    allocation_rows = [
        {
            "type": "战略偏好",
            "item": labels.get(asset, asset),
            "weight": _num(weight),
            "risk_contribution": _num(risk.get(asset)),
            "exposure": None,
        }
        for asset, weight in weights.items()
    ]
    macro_audit = (data.get("allocations") or {}).get("macro_factor_risk_audit") or {}
    stable_weights = stable_allocation.get("weights") or {}
    stable_risk = stable_allocation.get("risk_contribution") or {}
    allocation_rows.extend(
        {
            "type": "稳健绝对",
            "item": labels.get(asset, asset),
            "weight": _num(weight),
            "risk_contribution": _num(stable_risk.get(asset)),
            "exposure": None,
        }
        for asset, weight in stable_weights.items()
    )
    allocation_rows.extend(
        {
            "type": "宏观因子",
            "item": row.get("factor"),
            "weight": None,
            "risk_contribution": _num(row.get("total_risk_share")),
            "exposure": _num(row.get("exposure")),
        }
        for row in macro_audit.get("factors") or []
    )
    costs = ((data.get("backtest") or {}).get("robustness") or {}).get("cost_sensitivity_test") or []
    return {
        "descriptive": {
            "title": "入模宏观指标监测矩阵",
            "note": "仅展示训练期入选因子。较前值与历史分位均由冻结时序计算。",
            "table": _table(INDICATOR_COLUMNS, monitor_rows),
            "chart": {
                "title": "最新标准值与历史分位",
                "x_title": "历史分位",
                "y_title": "标准值",
                "traces": [
                    _trace(
                        "入模因子",
                        [row["percentile"] for row in monitor_rows],
                        [row["latest"] for row in monitor_rows],
                        kind="scatter",
                        mode="markers",
                        text=[row["name"] for row in monitor_rows],
                    )
                ],
            },
        },
        "history": {
            "title": "冠军组合历史净值与相对净值",
            "note": "训练、验证、封存测试边界沿用模型快照，测试期只报告。",
            "table": _table(
                [
                    ("split", "样本", "text"),
                    ("model", "目标", "text"),
                    ("annual_return", "年化", "percent"),
                    ("annual_excess_return", "超额", "signed_percent"),
                    ("sharpe", "夏普", "number"),
                    ("cash_excess_sharpe", "现金超额夏普", "signed"),
                    ("information_ratio", "IR", "signed"),
                    ("max_drawdown", "回撤", "signed_percent"),
                ],
                metrics,
            ),
            "chart": {
                "title": "组合、基准与相对净值",
                "x_title": "月份",
                "y_title": "净值",
                "traces": [
                    _trace("冠军组合", dates, strategy_nav, color="#163d7a"),
                    *([_trace(stable_label, stable_dates, stable_nav, color="#a61b1b")] if stable_nav and stable_key != "recommended" else []),
                    _trace("等权基准", dates, benchmark_nav, color="#98a2b3"),
                    _trace("相对净值", dates, relative_nav, color="#c46a08"),
                ],
            },
        },
        "diagnostics": {
            "title": "模型架构训练—验证稳健性",
            "note": "横轴为验证期现金超额夏普，纵轴为绝对夏普；高现金仓位不能只凭绝对夏普晋级。",
            "table": _table(
                [
                    ("candidate", "模型架构", "text"),
                    ("family", "状态", "text"),
                    ("train_sharpe", "训练夏普", "number"),
                    ("validation_sharpe", "验证夏普", "signed"),
                    ("train_cash_excess_sharpe", "训练现金超额", "signed"),
                    ("validation_cash_excess_sharpe", "验证现金超额", "signed"),
                    ("validation_excess", "验证超额", "signed_percent"),
                    ("turnover", "年化换手", "number"),
                    ("eligible", "证据门禁", "status"),
                ],
                candidate_rows[:12],
            ),
            "chart": {
                "title": "验证期绝对与现金超额表现",
                "x_title": "现金超额夏普",
                "y_title": "验证期夏普",
                "traces": [
                    _trace(
                        "候选模型",
                        [row["validation_cash_excess_sharpe"] for row in candidate_rows],
                        [row["validation_sharpe"] for row in candidate_rows],
                        kind="scatter",
                        mode="markers",
                        text=[row["candidate"] for row in candidate_rows],
                    )
                ],
            },
        },
        "strategy": {
            "title": "当前权重、宏观风险贡献与成本衰减",
            "note": "资产权重与风险贡献来自当前冻结解；宏观因子行为当前只作风险解释，不改写权重。",
            "table": _table(
                [
                    ("type", "类型", "text"),
                    ("item", "资产或因子", "text"),
                    ("weight", "配置权重", "percentile"),
                    ("risk_contribution", "风险贡献", "percentile"),
                    ("exposure", "因子暴露", "signed"),
                ],
                allocation_rows,
            ),
            "chart": {
                "title": "交易成本敏感性",
                "x_title": "单边成本(bp)",
                "y_title": "年化收益",
                "y2_title": "夏普",
                "traces": [
                    _trace(
                        "年化收益",
                        [_num(row.get("transaction_cost_bps", row.get("cost_bps"))) for row in costs],
                        [_num(row.get("annual_return")) for row in costs],
                        color="#163d7a",
                    ),
                    _trace(
                        "夏普",
                        [_num(row.get("transaction_cost_bps", row.get("cost_bps"))) for row in costs],
                        [_num(row.get("sharpe")) for row in costs],
                        axis="y2",
                        color="#c46a08",
                    ),
                ],
            },
        },
    }


def liquidity_visuals(data: dict[str, Any], page: str) -> dict[str, Any]:
    page_data = (data.get("pages") or {}).get(page) or {}
    charts = page_data.get("charts") or []
    monitor_rows = []
    history_traces = []
    for chart in charts:
        traces = chart.get("traces") or []
        if not traces:
            continue
        trace = max(traces, key=lambda row: len(row.get("y") or []))
        row = _indicator(
            str(chart.get("frequency") or "跟踪"),
            str(chart.get("title") or trace.get("name")),
            str(trace.get("name") or ""),
            trace.get("x") or [],
            trace.get("y") or [],
            quality=str((chart.get("quality") or {}).get("status") or ""),
        )
        if row:
            monitor_rows.append(row)
        if len(history_traces) < 4:
            pairs = [
                (date, _num(value))
                for date, value in zip(trace.get("x") or [], trace.get("y") or [])
                if value is not None
            ]
            pairs = _sample(pairs, 160)
            values = [item[1] for item in pairs]
            mean = sum(values) / len(values) if values else 0.0
            variance = sum((value - mean) ** 2 for value in values) / max(len(values) - 1, 1)
            scale = math.sqrt(variance) or 1.0
            history_traces.append(
                _trace(
                    str(chart.get("title") or trace.get("name")),
                    [item[0] for item in pairs],
                    [(item[1] - mean) / scale for item in pairs],
                )
            )

    quality_rows = []
    for chart in charts:
        quality = chart.get("quality") or {}
        quality_rows.append(
            {
                "chart": chart.get("title"),
                "frequency": chart.get("frequency"),
                "observations": int(quality.get("common_observations") or 0),
                "largest_gap": _num(quality.get("largest_gap_days")),
                "missing": int(quality.get("missing_after_intersection") or 0),
                "status": quality.get("status"),
            }
        )
    source_registry = data.get("source_registry") or {}
    source_rows = [
        {
            "source": value.get("label") or key,
            "frequency": value.get("frequency"),
            "quality": value.get("quality"),
        }
        for key, value in source_registry.items()
    ]
    quality_counts: dict[str, int] = {}
    for row in source_rows:
        key = str(row.get("quality") or "unknown")
        quality_counts[key] = quality_counts.get(key, 0) + 1
    return {
        "descriptive": {
            "title": "资金指标监测矩阵",
            "note": "不同量纲通过历史分位并列观察，原始最新值仍保留在表内。",
            "table": _table(INDICATOR_COLUMNS, monitor_rows[:12]),
            "chart": {
                "title": "当前历史分位",
                "x_title": "指标",
                "y_title": "历史分位",
                "traces": [
                    _trace(
                        "历史分位",
                        [row["name"] for row in monitor_rows[:12]],
                        [row["percentile"] for row in monitor_rows[:12]],
                        kind="bar",
                    )
                ],
            },
        },
        "history": {
            "title": "资金序列标准化历史对照",
            "note": "仅为解决不同量纲的可视化比较，标准化不改变页面中的原始数据。",
            "table": _table(
                [
                    ("chart", "图表", "text"),
                    ("frequency", "频率", "text"),
                    ("observations", "共同样本", "integer"),
                    ("status", "质量", "status"),
                ],
                quality_rows,
            ),
            "chart": {
                "title": "主要资金序列滚动标准分",
                "x_title": "日期",
                "y_title": "标准分",
                "traces": history_traces,
            },
        },
        "diagnostics": {
            "title": "图表级数据质量诊断",
            "note": "缺口、共同样本和时间间隔直接决定资金复盘可信度。",
            "table": _table(
                [
                    ("chart", "图表", "text"),
                    ("observations", "共同样本", "integer"),
                    ("largest_gap", "最大间隔(日)", "number"),
                    ("missing", "交集后缺失", "integer"),
                    ("status", "状态", "status"),
                ],
                quality_rows,
            ),
            "chart": {
                "title": "样本覆盖与最大间隔",
                "x_title": "图表",
                "y_title": "共同样本",
                "y2_title": "最大间隔(日)",
                "traces": [
                    _trace("共同样本", [row["chart"] for row in quality_rows], [row["observations"] for row in quality_rows], kind="bar"),
                    _trace("最大间隔", [row["chart"] for row in quality_rows], [row["largest_gap"] for row in quality_rows], axis="y2", color="#c46a08"),
                ],
            },
        },
        "strategy": {
            "title": "数据源覆盖与复盘边界",
            "note": "该模块是资金跟踪系统，不构造虚假的收益模型或夏普指标。",
            "table": _table(
                [
                    ("source", "数据源", "text"),
                    ("frequency", "频率", "text"),
                    ("quality", "质量", "status"),
                ],
                source_rows,
            ),
            "chart": {
                "title": "数据源质量分布",
                "x_title": "质量状态",
                "y_title": "数据源数",
                "traces": [
                    _trace("数据源", list(quality_counts), list(quality_counts.values()), kind="bar")
                ],
            },
        },
    }


def _rotation_industry_visuals_base(data: dict[str, Any], model_rows: list[dict[str, Any]]) -> dict[str, Any]:
    frequencies = (data.get("industry") or {}).get("frequencies") or {}
    monthly = frequencies.get("monthly") or {}
    monthly_six = monthly.get("six_dimension") or {}
    ranking = monthly_six.get("research_ranking") or []
    dimensions = [
        ("prosperity", "景气度"),
        ("fundamental", "基本面"),
        ("technical", "技术面"),
        ("valuation", "估值"),
        ("funds", "资金面"),
        ("anti_crowding", "低拥挤度"),
    ]

    def component_value(row: dict[str, Any], key: str) -> float | None:
        value = (row.get("components") or {}).get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def research_result(
        model: dict[str, Any],
        required_key: str,
    ) -> dict[str, Any]:
        six = model.get("six_dimension") or {}
        for candidate in (model.get("research_result"), six.get("research_result")):
            if isinstance(candidate, dict) and candidate.get(required_key):
                return candidate
        return {}

    dimension_rows = []
    for ranked in ranking:
        name = ranked.get("name")
        if not name:
            continue
        row = {
            "rank": ranked.get("rank"),
            "industry": name,
            "score": _num(ranked.get("score")),
        }
        row.update({key: component_value(ranked, key) for key, _ in dimensions})
        dimension_rows.append(row)

    history_traces = []
    candidate_rows = []
    for frequency in ("monthly", "weekly"):
        model = frequencies.get(frequency) or {}
        result = research_result(model, "nav")
        nav = result.get("nav") or []
        dates, strategy = _paired(nav, "date", "strategy", 220)
        _, benchmark = _paired(nav, "date", "benchmark", 220)
        frequency_label = "月频" if frequency == "monthly" else "周频"
        history_traces.extend(
            [
                _trace(f"{frequency_label}六维策略", dates, strategy),
                _trace(
                    f"{frequency_label}31行业等权基准",
                    dates,
                    benchmark,
                    color="#98a2b3",
                ),
            ]
        )
        for row in model.get("candidate_audit") or []:
            candidate_rows.append(
                {
                    "frequency": "月频" if frequency == "monthly" else "周频",
                    "candidate": row.get("candidate_label") or row.get("candidate"),
                    "train_absolute_sharpe": _num(row.get("train_sharpe")),
                    "validation_absolute_sharpe": _num(row.get("validation_sharpe")),
                    "train_excess_sharpe": _num(row.get("train_excess_sharpe")),
                    "validation_excess_sharpe": _num(row.get("validation_excess_sharpe")),
                    "report_excess_sharpe": _num((row.get("report_only_test") or {}).get("excess_sharpe")),
                    "objective": _num(row.get("objective"), -999.0),
                }
            )
    candidate_rows.sort(key=lambda row: row["objective"], reverse=True)

    holding_history = research_result(monthly, "holdings").get("holdings") or []
    if isinstance(holding_history, list):
        latest_holding = holding_history[-1] if holding_history else {}
    elif isinstance(holding_history, dict):
        latest_holding = holding_history
    else:
        latest_holding = {}
    holding_weights = latest_holding.get("weights") or {}
    holding_names = set(latest_holding.get("names") or []) | set(holding_weights)
    uniform_weight = _num(latest_holding.get("weight"))
    strategy_rows = [
        {
            "rank": row.get("rank"),
            "industry": row.get("name"),
            "score": _num(row.get("score")),
            "selected": "入选" if row.get("name") in holding_names else "未入选",
            "weight": _num(
                holding_weights.get(row.get("name")),
                uniform_weight if row.get("name") in holding_names else 0.0,
            ),
        }
        for row in ranking
        if row.get("name")
    ]
    return {
        "descriptive": {
            "title": "31行业六维评分矩阵",
            "note": "月频六维研究排名；景气度、基本面、技术面、估值、资金面与低拥挤度统一为截面分位。",
            "table": _table(
                [
                    ("rank", "排名", "integer"),
                    ("industry", "行业", "text"),
                    ("score", "综合分", "percentile"),
                    *[(key, label, "percentile") for key, label in dimensions],
                ],
                dimension_rows,
            ),
            "chart": {
                "title": "31行业六维条件色评分",
                "x_title": "维度",
                "y_title": "行业",
                "heatmap": {
                    "x": [label for _, label in dimensions],
                    "y": [row["industry"] for row in dimension_rows],
                    "z": [
                        [row[key] for key, _ in dimensions]
                        for row in dimension_rows
                    ],
                },
            },
        },
        "history": {
            "title": "月频与周频六维策略历史复盘",
            "note": "净值优先读取各频率六维研究结果；生产C6与研究挑战者仍在治理表中分别披露。",
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("candidate", "生产方案", "text"),
                    ("research_candidate", "研究方案", "text"),
                    ("gate", "门禁", "status"),
                ],
                model_rows,
            ),
            "chart": {
                "title": "六维行业轮动策略与31行业等权基准",
                "x_title": "日期",
                "y_title": "净值",
                "traces": history_traces,
            },
        },
        "diagnostics": {
            "title": "候选模型样本内外一致性",
            "note": "训练强但验证弱的候选在图中会落在右下区域，不因测试期偶然改善而晋级。",
            "table": _table(
                [
                    ("frequency", "频率", "text"),
                    ("candidate", "候选", "text"),
                    ("train_absolute_sharpe", "训练夏普", "signed"),
                    ("validation_absolute_sharpe", "验证夏普", "signed"),
                    ("train_excess_sharpe", "训练超额夏普", "signed"),
                    ("validation_excess_sharpe", "验证超额夏普", "signed"),
                    ("report_excess_sharpe", "报告超额夏普", "signed"),
                    ("objective", "稳健目标", "signed"),
                ],
                candidate_rows[:16],
            ),
            "chart": {
                "title": "训练与验证超额夏普",
                "x_title": "训练超额夏普",
                "y_title": "验证超额夏普",
                "traces": [
                    _trace("候选", [row["train_excess_sharpe"] for row in candidate_rows], [row["validation_excess_sharpe"] for row in candidate_rows], kind="scatter", mode="markers", text=[row["candidate"] for row in candidate_rows])
                ],
            },
        },
        "strategy": {
            "title": "六维研究排序与最新持仓",
            "note": "排序来自月频六维研究截面；入选状态与权重来自该研究结果最后一期实际持仓。",
            "table": _table(
                [
                    ("rank", "排名", "integer"),
                    ("industry", "行业", "text"),
                    ("score", "综合分", "percentile"),
                    ("selected", "入选", "status"),
                    ("weight", "权重", "percent"),
                ],
                strategy_rows,
            ),
            "chart": {
                "title": "六维得分与最新组合权重",
                "x_title": "行业",
                "y_title": "综合得分",
                "y2_title": "组合权重",
                "traces": [
                    _trace(
                        "六维综合分",
                        [row["industry"] for row in strategy_rows],
                        [row["score"] for row in strategy_rows],
                        kind="bar",
                    ),
                    _trace(
                        "最新持仓权重",
                        [row["industry"] for row in strategy_rows],
                        [row["weight"] for row in strategy_rows],
                        kind="bar",
                        axis="y2",
                        color="#ffc000",
                    ),
                ],
            },
        },
    }


def _latest_holding(model: dict[str, Any]) -> dict[str, Any]:
    holdings = model.get("holdings") or []
    if isinstance(holdings, list):
        return holdings[-1] if holdings else {}
    return holdings if isinstance(holdings, dict) else {}


def _rotation_industry_visuals(
    data: dict[str, Any],
    model_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Production champion is primary; six-dimension challengers remain shadow evidence."""
    visuals = _rotation_industry_visuals_base(data, model_rows)
    frequencies = (data.get("industry") or {}).get("frequencies") or {}
    for block in visuals.values():
        block["display"] = "charts_only"

    monthly = frequencies.get("monthly") or {}
    production_ranking = monthly.get("ranking") or []
    research_rows = (visuals["descriptive"].get("table") or {}).get("rows") or []
    research_score = {row.get("industry"): row.get("score") for row in research_rows}
    visuals["descriptive"]["secondary_charts"] = [
        {
            "title": "生产冠军与六维研究截面",
            "x_title": "行业",
            "y_title": "标准化得分",
            "traces": [
                _trace(
                    "C6生产冠军",
                    [row.get("name") for row in production_ranking],
                    [_num(row.get("score")) for row in production_ranking],
                    kind="bar",
                ),
                _trace(
                    "六维研究",
                    [row.get("name") for row in production_ranking],
                    [_num(research_score.get(row.get("name"))) for row in production_ranking],
                    color="#2f75b5",
                    mode="lines+markers",
                ),
            ],
        }
    ]

    production_traces = []
    production_drawdowns = []
    for frequency in ("monthly", "weekly"):
        model = frequencies.get(frequency) or {}
        label = "月频" if frequency == "monthly" else "周频"
        dates, strategy = _paired(model.get("nav") or [], "date", "strategy", 220)
        _, benchmark = _paired(model.get("nav") or [], "date", "benchmark", 220)
        production_traces.extend(
            [
                _trace(f"{label}C6生产冠军", dates, strategy),
                _trace(f"{label}行业等权", dates, benchmark, color="#808080", dash="dot"),
            ]
        )
        production_drawdowns.append(_trace(f"{label}C6回撤", dates, _drawdown(strategy)))
    research_history = visuals["history"].get("chart") or {}
    visuals["history"]["chart"] = {
        "title": "生产冠军与行业等权基准",
        "x_title": "日期",
        "y_title": "净值",
        "traces": production_traces,
    }
    visuals["history"]["secondary_charts"] = [
        {
            "title": "生产冠军历史回撤",
            "x_title": "日期",
            "y_title": "回撤",
            "traces": production_drawdowns,
        },
        {
            "title": "六维研究影子净值",
            "x_title": "日期",
            "y_title": "净值",
            "traces": research_history.get("traces") or [],
        },
    ]

    candidate_rows = (visuals["diagnostics"].get("table") or {}).get("rows") or []
    visuals["diagnostics"]["secondary_charts"] = [
        {
            "title": "验证期绝对与超额夏普",
            "x_title": "候选",
            "y_title": "夏普",
            "traces": [
                _trace(
                    "绝对夏普",
                    [row.get("candidate") for row in candidate_rows],
                    [row.get("validation_absolute_sharpe") for row in candidate_rows],
                    kind="bar",
                ),
                _trace(
                    "超额夏普",
                    [row.get("candidate") for row in candidate_rows],
                    [row.get("validation_excess_sharpe") for row in candidate_rows],
                    kind="bar",
                    color="#2f75b5",
                ),
            ],
        }
    ]

    latest = _latest_holding(monthly)
    weights = latest.get("weights") or {}
    production_rows = [
        {
            "rank": row.get("rank"),
            "industry": row.get("name"),
            "score": _num(row.get("score")),
            "selected": "入选" if row.get("name") in weights else "未入选",
            "weight": _num(weights.get(row.get("name"), row.get("weight"))),
        }
        for row in production_ranking
    ]
    visuals["strategy"]["title"] = "生产冠军排序与最新持仓"
    visuals["strategy"]["note"] = "主图只展示训练与验证定型的C6生产冠军；六维研究结果单列为影子证据。"
    visuals["strategy"]["table"]["rows"] = production_rows
    research_strategy = visuals["strategy"].get("chart") or {}
    visuals["strategy"]["chart"] = {
        "title": "C6生产得分与最新权重",
        "x_title": "行业",
        "y_title": "生产得分",
        "y2_title": "组合权重",
        "traces": [
            _trace("C6生产得分", [row["industry"] for row in production_rows], [row["score"] for row in production_rows], kind="bar"),
            _trace("生产持仓权重", [row["industry"] for row in production_rows], [row["weight"] for row in production_rows], axis="y2", color="#ffc000", mode="lines+markers"),
        ],
    }
    visuals["strategy"]["secondary_charts"] = [research_strategy]
    return visuals

def _rotation_style_visuals(data: dict[str, Any]) -> dict[str, Any]:
    style = data.get("style") or {}
    model = ((style.get("frequencies") or {}).get("quarterly") or {})
    cells = style.get("cells") or []
    ranking = model.get("ranking") or []
    nav = model.get("nav") or []
    candidates = model.get("candidate_audit") or []
    migration = style.get("migration") or {}
    latest = _latest_holding(model)
    dates, strategy = _paired(nav, "date", "strategy", 220)
    _, benchmark = _paired(nav, "date", "benchmark", 220)
    _, excess = _paired(nav, "date", "excess", 220)
    selected = set(latest.get("names") or [])
    style_rows = [
        {
            "style": row.get("name") or row.get("cell"),
            "score": _num(row.get("score")),
            "selected": "入选" if (row.get("name") or row.get("cell")) in selected else "未入选",
            "weight": _num(row.get("weight")),
            "stock_count": int(_num(row.get("stock_count"))),
            "cap_share": _num(row.get("cap_share")),
        }
        for row in ranking
    ]
    candidate_rows = [
        {
            "candidate": row.get("candidate"),
            "train_excess": _num(row.get("train_excess_sharpe")),
            "validation_excess": _num(row.get("validation_excess_sharpe")),
            "validation_return": _num(row.get("validation_annual_excess")),
            "turnover": _num(row.get("validation_annual_turnover")),
        }
        for row in candidates
    ]
    flows = (migration.get("flows") or [])[:12]
    blocks = {
        "descriptive": {
            "title": "十二风格箱估值、成长、红利与市值覆盖",
            "display": "charts_only",
            "table": _table(
                [
                    ("cell", "风格箱", "text"),
                    ("stock_count", "股票数", "integer"),
                    ("cap_share", "市值占比", "percent"),
                    ("value_score", "价值", "percentile"),
                    ("growth_score", "成长", "percentile"),
                    ("dividend_yield", "红利", "percentile"),
                ],
                cells,
            ),
            "chart": {
                "title": "十二风格箱条件色矩阵",
                "x_title": "维度",
                "y_title": "风格箱",
                "heatmap": {
                    "x": ["价值", "成长", "红利", "市值占比"],
                    "y": [row.get("cell") for row in cells],
                    "z": [
                        [
                            _num(row.get("value_score")),
                            _num(row.get("growth_score")),
                            _num(row.get("dividend_yield")),
                            _num(row.get("cap_share")),
                        ]
                        for row in cells
                    ],
                },
            },
            "secondary_charts": [
                {
                    "title": "股票数量与市值覆盖",
                    "x_title": "风格箱",
                    "y_title": "股票数",
                    "y2_title": "市值占比",
                    "traces": [
                        _trace("股票数", [row.get("cell") for row in cells], [_num(row.get("stock_count")) for row in cells], kind="bar"),
                        _trace("市值占比", [row.get("cell") for row in cells], [_num(row.get("cap_share")) for row in cells], axis="y2", color="#ffc000", mode="lines+markers"),
                    ],
                }
            ],
        },
        "history": {
            "title": "季度风格轮动历史净值与回撤",
            "display": "charts_only",
            "table": _table(
                [
                    ("date", "日期", "text"),
                    ("strategy", "策略", "number"),
                    ("benchmark", "基准", "number"),
                    ("excess", "超额净值", "number"),
                ],
                _sample(nav, 40),
            ),
            "chart": {
                "title": "风格轮动、基准与超额净值",
                "x_title": "日期",
                "y_title": "净值",
                "traces": [
                    _trace("风格轮动", dates, strategy),
                    _trace("风格等权", dates, benchmark, color="#808080", dash="dot"),
                    _trace("超额净值", dates, excess, color="#2f75b5"),
                ],
            },
            "secondary_charts": [
                {
                    "title": "策略与基准回撤",
                    "x_title": "日期",
                    "y_title": "回撤",
                    "traces": [
                        _trace("策略回撤", dates, _drawdown(strategy)),
                        _trace("基准回撤", dates, _drawdown(benchmark), color="#808080"),
                    ],
                }
            ],
        },
        "diagnostics": {
            "title": "风格候选训练验证一致性",
            "display": "charts_only",
            "table": _table(
                [
                    ("candidate", "候选", "text"),
                    ("train_excess", "训练超额夏普", "signed"),
                    ("validation_excess", "验证超额夏普", "signed"),
                    ("validation_return", "验证年化超额", "signed_percent"),
                    ("turnover", "验证换手", "number"),
                ],
                candidate_rows,
            ),
            "chart": {
                "title": "训练与验证超额夏普",
                "x_title": "训练超额夏普",
                "y_title": "验证超额夏普",
                "traces": [
                    _trace("候选", [row["train_excess"] for row in candidate_rows], [row["validation_excess"] for row in candidate_rows], kind="scatter", mode="markers", text=[row["candidate"] for row in candidate_rows])
                ],
            },
            "secondary_charts": [
                {
                    "title": "验证超额—换手代价",
                    "x_title": "年化换手",
                    "y_title": "验证年化超额",
                    "traces": [
                        _trace("候选", [row["turnover"] for row in candidate_rows], [row["validation_return"] for row in candidate_rows], kind="scatter", mode="markers", text=[row["candidate"] for row in candidate_rows])
                    ],
                }
            ],
        },
        "strategy": {
            "title": "最新风格排序、持仓与迁移",
            "display": "charts_only",
            "table": _table(
                [
                    ("style", "风格箱", "text"),
                    ("score", "综合分", "signed"),
                    ("selected", "入选", "status"),
                    ("weight", "权重", "percent"),
                    ("stock_count", "股票数", "integer"),
                    ("cap_share", "市值占比", "percent"),
                ],
                style_rows,
            ),
            "chart": {
                "title": "最新风格得分与配置权重",
                "x_title": "风格箱",
                "y_title": "综合得分",
                "y2_title": "配置权重",
                "traces": [
                    _trace("风格得分", [row["style"] for row in style_rows], [row["score"] for row in style_rows], kind="bar"),
                    _trace("配置权重", [row["style"] for row in style_rows], [row["weight"] for row in style_rows], axis="y2", color="#ffc000", mode="lines+markers"),
                ],
            },
            "secondary_charts": [
                {
                    "title": "主要风格迁移流",
                    "x_title": "迁移方向",
                    "y_title": "股票数",
                    "traces": [
                        _trace("迁移股票", [f'{row.get("from")}→{row.get("to")}' for row in flows], [_num(row.get("count")) for row in flows], kind="bar")
                    ],
                }
            ],
        },
    }
    return blocks

def _rotation_allocation_visuals(
    data: dict[str, Any],
    model_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    frequencies = (data.get("industry") or {}).get("frequencies") or {}
    style_model = (((data.get("style") or {}).get("frequencies") or {}).get("quarterly") or {})
    models = [
        ("月频行业", frequencies.get("monthly") or {}),
        ("周频行业", frequencies.get("weekly") or {}),
        ("季度风格", style_model),
    ]
    allocation_rows = []
    for label, model in models:
        latest = _latest_holding(model)
        weights = latest.get("weights") or {}
        names = latest.get("names") or []
        default_weight = _num(latest.get("weight"))
        ranking_score = {row.get("name"): _num(row.get("score")) for row in model.get("ranking") or []}
        for name in names:
            allocation_rows.append(
                {
                    "model": label,
                    "name": name,
                    "weight": _num(weights.get(name), default_weight),
                    "score": ranking_score.get(name, 0.0),
                }
            )

    nav_traces = []
    drawdown_traces = []
    candidate_rows = []
    turnover_traces = []
    for label, model in models:
        dates, strategy = _paired(model.get("nav") or [], "date", "strategy", 220)
        nav_traces.append(_trace(label, dates, strategy))
        drawdown_traces.append(_trace(label, dates, _drawdown(strategy)))
        holding_dates, turnover = _paired(model.get("holdings") or [], "signal_date", "turnover", 160)
        turnover_traces.append(_trace(label, holding_dates, turnover, mode="lines+markers"))
        for row in model.get("candidate_audit") or []:
            candidate_rows.append(
                {
                    "model": label,
                    "candidate": row.get("candidate_label") or row.get("candidate"),
                    "train_excess": _num(row.get("train_excess_sharpe")),
                    "validation_excess": _num(row.get("validation_excess_sharpe")),
                    "objective": _num(row.get("objective"), _num(row.get("selection_objective"))),
                    "turnover": _num(row.get("validation_turnover"), _num(row.get("validation_annual_turnover"))),
                }
            )

    table = _table(
        [
            ("model", "模型", "text"),
            ("name", "持仓", "text"),
            ("weight", "权重", "percent"),
            ("score", "得分", "signed"),
        ],
        allocation_rows,
    )
    visuals = {
        "descriptive": {
            "title": "行业与风格当前配置",
            "display": "charts_only",
            "table": table,
            "chart": {
                "title": "月频、周频与风格配置权重",
                "x_title": "持仓",
                "y_title": "权重",
                "traces": [
                    _trace(
                        label,
                        [row["name"] for row in allocation_rows if row["model"] == label],
                        [row["weight"] for row in allocation_rows if row["model"] == label],
                        kind="bar",
                    )
                    for label, _ in models
                ],
            },
            "secondary_charts": [
                {
                    "title": "当前入选标的信号强度",
                    "x_title": "持仓",
                    "y_title": "综合得分",
                    "traces": [
                        _trace(
                            label,
                            [row["name"] for row in allocation_rows if row["model"] == label],
                            [row["score"] for row in allocation_rows if row["model"] == label],
                            kind="bar",
                        )
                        for label, _ in models
                    ],
                }
            ],
        },
        "history": {
            "title": "三套轮动模型历史净值与回撤",
            "display": "charts_only",
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("candidate", "生产方案", "text"),
                    ("research_candidate", "研究方案", "text"),
                    ("gate", "门禁", "status"),
                ],
                model_rows,
            ),
            "chart": {
                "title": "行业与风格轮动净值",
                "x_title": "日期",
                "y_title": "净值",
                "traces": nav_traces,
            },
            "secondary_charts": [
                {
                    "title": "行业与风格轮动回撤",
                    "x_title": "日期",
                    "y_title": "回撤",
                    "traces": drawdown_traces,
                }
            ],
        },
        "diagnostics": {
            "title": "候选训练验证与实施代价",
            "display": "charts_only",
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("candidate", "候选", "text"),
                    ("train_excess", "训练超额夏普", "signed"),
                    ("validation_excess", "验证超额夏普", "signed"),
                    ("objective", "稳健目标", "signed"),
                    ("turnover", "验证换手", "number"),
                ],
                candidate_rows,
            ),
            "chart": {
                "title": "训练与验证超额夏普",
                "x_title": "训练超额夏普",
                "y_title": "验证超额夏普",
                "traces": [
                    _trace("候选", [row["train_excess"] for row in candidate_rows], [row["validation_excess"] for row in candidate_rows], kind="scatter", mode="markers", text=[f'{row["model"]}·{row["candidate"]}' for row in candidate_rows])
                ],
            },
            "secondary_charts": [
                {
                    "title": "验证目标—换手代价",
                    "x_title": "验证换手",
                    "y_title": "稳健目标",
                    "traces": [
                        _trace("候选", [row["turnover"] for row in candidate_rows], [row["objective"] for row in candidate_rows], kind="scatter", mode="markers", text=[f'{row["model"]}·{row["candidate"]}' for row in candidate_rows])
                    ],
                }
            ],
        },
        "strategy": {
            "title": "调仓节奏与最新配置",
            "display": "charts_only",
            "table": table,
            "chart": {
                "title": "历史单边换手",
                "x_title": "信号日",
                "y_title": "单边换手",
                "traces": turnover_traces,
            },
            "secondary_charts": [
                {
                    "title": "最新权重—信号得分",
                    "x_title": "配置权重",
                    "y_title": "信号得分",
                    "traces": [
                        _trace(
                            "当前持仓",
                            [row["weight"] for row in allocation_rows],
                            [row["score"] for row in allocation_rows],
                            kind="scatter",
                            mode="markers",
                            text=[f'{row["model"]}·{row["name"]}' for row in allocation_rows],
                            marker_size=[max(9.0, row["weight"] * 100.0) for row in allocation_rows],
                        )
                    ],
                }
            ],
        },
    }
    return visuals


def rotation_visuals(
    data: dict[str, Any],
    model_rows: list[dict[str, Any]],
    page: str = "industry",
) -> dict[str, Any]:
    if page == "style":
        return _rotation_style_visuals(data)
    if page in {"allocation", "home", "backtest"}:
        return _rotation_allocation_visuals(data, model_rows)
    return _rotation_industry_visuals(data, model_rows)

def factor_visuals(
    data: dict[str, Any],
    metrics: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    features = data.get("features") or []
    weights = ((data.get("selection") or {}).get("adaptive_icir") or {}).get("last_weights") or []
    factor_rows = [
        {
            "factor": feature,
            "weight": _num(weight),
            "direction": _num(weight),
            "absolute_weight": abs(_num(weight)),
        }
        for feature, weight in zip(features, weights)
    ]
    factor_rows.sort(key=lambda row: row["absolute_weight"], reverse=True)
    diagnostics = data.get("diagnostics") or {}
    rolling = diagnostics.get("rolling") or []
    dates, nav_net = _paired(rolling, "date", "nav_net", 220)
    _, nav_gross = _paired(rolling, "date", "nav_gross", 220)
    _, rolling_ic = _paired(rolling, "date", "rolling_rank_ic", 220)
    costs = diagnostics.get("cost_sensitivity") or []
    return {
        "descriptive": {
            "title": "动态因子权重截面",
            "note": "权重来自滞后滚动ICIR更新，正负号代表方向，绝对值代表当前影响强度。",
            "table": _table(
                [
                    ("factor", "因子", "text"),
                    ("direction", "方向", "arrow"),
                    ("weight", "当前权重", "signed"),
                    ("absolute_weight", "绝对权重", "percentile"),
                ],
                factor_rows[:15],
            ),
            "chart": {
                "title": "当前因子权重",
                "x_title": "因子",
                "y_title": "权重",
                "traces": [_trace("权重", [row["factor"] for row in factor_rows[:15]], [row["weight"] for row in factor_rows[:15]], kind="bar")],
            },
        },
        "history": {
            "title": "成本前后净值与滚动RankIC",
            "note": "净值使用真实执行策略，滚动RankIC只使用当时可见标签和历史窗口。",
            "table": _table(
                [
                    ("split", "样本", "text"),
                    ("rank_ic", "RankIC", "signed"),
                    ("icir", "ICIR", "signed"),
                    ("annual_return", "年化", "percent"),
                    ("sharpe", "夏普", "signed"),
                    ("turnover", "换手", "number"),
                ],
                (diagnostics.get("split_summary") or metrics),
            ),
            "chart": {
                "title": "净值与滚动RankIC",
                "x_title": "日期",
                "y_title": "净值",
                "y2_title": "滚动RankIC",
                "traces": [
                    _trace("成本后净值", dates, nav_net, color="#163d7a"),
                    _trace("成本前净值", dates, nav_gross, color="#98a2b3"),
                    _trace("滚动RankIC", dates, rolling_ic, axis="y2", color="#c46a08"),
                ],
            },
        },
        "diagnostics": {
            "title": "候选模型换手—夏普—RankIC诊断",
            "note": "横轴为验证换手，纵轴为验证夏普，悬停显示候选名称；表内同步保留验证RankIC。",
            "table": _table(
                [
                    ("candidate", "候选", "text"),
                    ("model", "模型", "text"),
                    ("validation_sharpe", "验证夏普", "signed"),
                    ("validation_rank_ic", "验证RankIC", "signed"),
                    ("validation_turnover", "验证换手", "number"),
                ],
                candidate_rows[:16],
            ),
            "chart": {
                "title": "验证期收益质量与执行代价",
                "x_title": "验证换手",
                "y_title": "验证夏普",
                "traces": [
                    _trace("候选", [row["validation_turnover"] for row in candidate_rows], [row["validation_sharpe"] for row in candidate_rows], kind="scatter", mode="markers", text=[row["candidate"] for row in candidate_rows])
                ],
                "vline": 0.65,
            },
        },
        "strategy": {
            "title": "交易成本衰减与可执行性",
            "note": "成本敏感性只检验已固定策略，不用测试期成本曲线反向选择模型。",
            "table": _table(
                [
                    ("cost_bps", "单边成本(bp)", "number"),
                    ("return", "年化", "percent"),
                    ("sharpe", "夏普", "signed"),
                    ("max_drawdown", "回撤", "signed_percent"),
                ],
                costs,
            ),
            "chart": {
                "title": "成本上升后的收益与夏普衰减",
                "x_title": "单边成本(bp)",
                "y_title": "年化收益",
                "y2_title": "夏普",
                "traces": [
                    _trace("年化收益", [_num(row.get("cost_bps")) for row in costs], [_num(row.get("return")) for row in costs], color="#163d7a"),
                    _trace("夏普", [_num(row.get("cost_bps")) for row in costs], [_num(row.get("sharpe")) for row in costs], axis="y2", color="#c46a08"),
                ],
            },
        },
    }


def index_visuals(data: dict[str, Any], audit: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    factor_tests = (data.get("factor_tests") or {}).get("CSI800_ENH") or []
    validation_rows = [
        {
            "factor": row.get("factor"),
            "split": row.get("split"),
            "rank_ic": _num(row.get("rank_ic")),
            "icir": _num(row.get("icir")),
            "spread": _num(row.get("group_spread")),
            "turnover": _num(row.get("turnover")),
            "passed": bool(row.get("pass")),
        }
        for row in factor_tests
        if str(row.get("split")).lower() in {"valid", "validation"}
    ]
    represented = {row["factor"] for row in validation_rows}
    training_rows = [
        {
            "factor": row.get("factor"),
            "split": row.get("split"),
            "rank_ic": _num(row.get("rank_ic")),
            "icir": _num(row.get("icir")),
            "spread": _num(row.get("group_spread")),
            "turnover": _num(row.get("turnover")),
            "passed": bool(row.get("pass")),
        }
        for row in factor_tests
        if str(row.get("split")).lower() == "train"
        and row.get("factor") not in represented
    ]
    training_rows.sort(key=lambda row: row["rank_ic"], reverse=True)
    validation_rows.extend(training_rows[: max(0, 15 - len(validation_rows))])
    if not validation_rows:
        validation_rows = [
            {
                "factor": row.get("factor"),
                "split": row.get("split"),
                "rank_ic": _num(row.get("rank_ic")),
                "icir": _num(row.get("icir")),
                "spread": _num(row.get("group_spread")),
                "turnover": _num(row.get("turnover")),
                "passed": bool(row.get("pass")),
            }
            for row in factor_tests[:20]
        ]
    validation_rows.sort(key=lambda row: row["rank_ic"], reverse=True)

    nav = (data.get("nav") or {}).get("CSI800_ENH") or {}
    champion_name = audit.get("champion")
    champion_series = next(
        (row for row in nav.get("series") or [] if row.get("model") == champion_name),
        (nav.get("series") or [{}])[0],
    )
    dates = champion_series.get("dates") or []
    strategy_nav = champion_series.get("nav") or []
    benchmark = nav.get("benchmark") or {}
    leaderboard = [
        {
            "model": row.get("model"),
            "status": row.get("status"),
            "annual_excess": _num(row.get("excess_annual_return")),
            "information_ratio": _num(row.get("information_ratio")),
            "sharpe": _num(row.get("sharpe")),
            "max_drawdown": _num(row.get("max_drawdown")),
        }
        for row in nav.get("leaderboard") or []
    ]
    leaderboard.sort(key=lambda row: row["information_ratio"], reverse=True)
    rolling = champion_series.get("rolling") or {}
    return {
        "descriptive": {
            "title": "验证期因子有效性矩阵",
            "note": "只把验证期因子检验用于比较，封存测试不参与因子筛选。",
            "table": _table(
                [
                    ("factor", "因子", "text"),
                    ("rank_ic", "RankIC", "signed"),
                    ("icir", "ICIR", "signed"),
                    ("spread", "分组收益差", "signed_percent"),
                    ("turnover", "换手", "number"),
                    ("passed", "门禁", "status"),
                ],
                validation_rows[:15],
            ),
            "chart": {
                "title": "验证RankIC与换手",
                "x_title": "换手",
                "y_title": "RankIC",
                "traces": [_trace("因子", [row["turnover"] for row in validation_rows], [row["rank_ic"] for row in validation_rows], kind="scatter", mode="markers", text=[row["factor"] for row in validation_rows])],
            },
        },
        "history": {
            "title": "指数增强冠军与基准历史净值",
            "note": "重复日期沿用原快照，页面不在前端重新计算或平滑。",
            "table": _table(
                [
                    ("split", "样本", "text"),
                    ("annual_return", "年化", "percent"),
                    ("annual_excess_return", "超额", "signed_percent"),
                    ("sharpe", "夏普", "signed"),
                    ("information_ratio", "IR", "signed"),
                    ("max_drawdown", "回撤", "signed_percent"),
                ],
                metrics,
            ),
            "chart": {
                "title": "冠军与基准净值",
                "x_title": "日期",
                "y_title": "净值",
                "traces": [
                    _trace("指数增强冠军", _sample(dates, 220), _sample(strategy_nav, 220), color="#163d7a"),
                    _trace("基准", _sample(benchmark.get("dates") or [], 220), _sample(benchmark.get("nav") or [], 220), color="#98a2b3"),
                ],
            },
        },
        "diagnostics": {
            "title": "滚动夏普、信息比率与跟踪误差",
            "note": "滚动主动收益恶化与跟踪误差扩张应同时观察，避免只看累计净值。",
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("status", "状态", "status"),
                    ("sharpe", "夏普", "signed"),
                    ("information_ratio", "IR", "signed"),
                    ("annual_excess", "超额", "signed_percent"),
                    ("max_drawdown", "回撤", "signed_percent"),
                ],
                leaderboard[:12],
            ),
            "chart": {
                "title": "滚动风险调整指标",
                "x_title": "日期",
                "y_title": "夏普/IR",
                "y2_title": "跟踪误差",
                "traces": [
                    _trace("滚动夏普", _sample(dates, 220), _sample(rolling.get("sharpe") or [], 220), color="#163d7a"),
                    _trace("滚动IR", _sample(dates, 220), _sample(rolling.get("information_ratio") or [], 220), color="#c46a08"),
                    _trace("跟踪误差", _sample(dates, 220), _sample(rolling.get("tracking_error") or [], 220), axis="y2", color="#98a2b3"),
                ],
            },
        },
        "strategy": {
            "title": "候选模型主动收益—IR截面",
            "note": "后验影子候选只进入诊断，不改变当前冠军。",
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("status", "状态", "status"),
                    ("annual_excess", "年化超额", "signed_percent"),
                    ("information_ratio", "IR", "signed"),
                    ("sharpe", "夏普", "signed"),
                ],
                leaderboard[:15],
            ),
            "chart": {
                "title": "候选主动收益与信息比率",
                "x_title": "年化超额",
                "y_title": "信息比率",
                "traces": [_trace("候选", [row["annual_excess"] for row in leaderboard], [row["information_ratio"] for row in leaderboard], kind="scatter", mode="markers", text=[row["model"] for row in leaderboard])],
            },
        },
    }


def _portfolio_visuals_base(data: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    weights = (data.get("home") or {}).get("current_weights") or []
    group_labels = {
        "bond_cash": "\u503a\u5238\u73b0\u91d1",
        "broad_equity": "\u5bbd\u57fa\u6743\u76ca",
        "commodity": "\u5546\u54c1",
        "overseas_equity": "\u6d77\u5916\u6743\u76ca",
        "sector_equity": "\u884c\u4e1a\u6743\u76ca",
    }
    role_labels = {
        "cash_equivalent": "\u73b0\u91d1\u7b49\u4ef7\u7269",
        "bond_duration": "\u4e45\u671f\u503a\u5238",
        "risk_asset": "\u98ce\u9669\u8d44\u4ea7",
    }

    weight_rows = [
        {
            "asset": f"{row.get('name') or row.get('code')}\u00b7{role_labels.get(row.get('risk_role'), row.get('risk_role') or '--')}",
            "group": group_labels.get(row.get("group"), row.get("group") or "--"),
            "weight": _num(row.get("weight")),
            "position_cap": _num(row.get("position_cap")),
            "expected_return": _num(row.get("expected_return")),
            "volatility": _num(row.get("annual_volatility")),
            "risk_contribution": _num(row.get("risk_contribution")),
        }
        for row in weights
    ]
    strategies = (data.get("backtest") or {}).get("strategies") or {}
    history_traces = []
    drawdown_traces = []
    for key in ("selected", "equal_weight", "inverse_volatility", "hrp"):
        strategy = strategies.get(key) or {}
        dates, nav = _paired(strategy.get("nav") or [], "date", "nav", 180)
        label = str(strategy.get("label") or key)
        history_traces.append(_trace(label, dates, nav))
        drawdown_traces.append(_trace(label, dates, _drawdown(nav)))
    optimization = data.get("optimization") or {}
    solver_rows = []
    for row in optimization.get("solver_benchmark") or []:
        residual = max(_num(row.get("max_constraint_violation")), 0.0)
        solver_rows.append(
            {
                **row,
                "actual_solver": row.get("actual_solver") or row.get("solver"),
                "accuracy_digits": -math.log10(max(residual, 1e-16)),
            }
        )
    frontier = optimization.get("efficient_frontier") or []
    attribution = ((data.get("backtest") or {}).get("return_loss_attribution") or {}).get("splits") or {}
    test_attr = attribution.get("test") or {}
    group_weights = test_attr.get("group_average_weights") or {}
    group_contribution = test_attr.get("group_gross_active_contribution") or {}
    attribution_rows = [
        {
            "group": group_labels.get(group, group),
            "average_weight": _num(group_weights.get(group)),
            "active_contribution": _num(group_contribution.get(group)),
        }
        for group in sorted(set(group_weights) | set(group_contribution))
    ]
    return {
        "descriptive": {
            "title": "当前权重与风险贡献矩阵",
            "note": "权重、预期收益、波动率和风险贡献来自同一次冻结求解。",
            "table": _table(
                [
                    ("asset", "标的", "text"),
                    ("group", "风险袖套", "text"),
                    ("weight", "权重", "percentile"),
                    ("expected_return", "预期收益", "signed_percent"),
                    ("position_cap", "\u4e0a\u9650", "percent"),
                    ("volatility", "波动率", "percent"),
                    ("risk_contribution", "风险贡献", "percentile"),
                ],
                weight_rows,
            ),
            "chart": {
                "title": "权重与风险贡献",
                "x_title": "标的",
                "y_title": "占比",
                "traces": [
                    _trace("权重", [row["asset"] for row in weight_rows], [row["weight"] for row in weight_rows], kind="bar"),
                    _trace("风险贡献", [row["asset"] for row in weight_rows], [row["risk_contribution"] for row in weight_rows], kind="bar"),
                    _trace("\u8d44\u4ea7\u4e0a\u9650", [row["asset"] for row in weight_rows], [row["position_cap"] for row in weight_rows], kind="scatter", mode="lines+markers"),
                ],
            },
        },
        "history": {
            "title": "优化组合与规则基线历史净值",
            "note": "所有策略使用相同资产池、调仓日和交易成本口径。",
            "table": _table(
                [
                    ("split", "样本", "text"),
                    ("annual_return", "年化", "percent"),
                    ("annual_excess_return", "超额", "signed_percent"),
                    ("sharpe", "夏普", "signed"),
                    ("information_ratio", "IR", "signed"),
                    ("max_drawdown", "回撤", "signed_percent"),
                ],
                metrics,
            ),
            "chart": {
                "title": "优化组合与基线净值",
                "x_title": "日期",
                "y_title": "净值",
                "traces": history_traces,
            },
        },
        "diagnostics": {
            "title": "求解器残差与有效前沿",
            "note": "求解成功必须同时满足状态和硬约束残差，等权可行种子不计为正常求解。",
            "table": _table(
                [
                    ("solver", "请求求解器", "text"),
                    ("actual_solver", "实际求解器", "text"),
                    ("status", "状态", "status"),
                    ("median_ms", "中位耗时(ms)", "number"),
                    ("iterations", "迭代", "integer"),
                    ("max_constraint_violation", "最大残差", "scientific"),
                ],
                solver_rows,
            ),
            "chart": {
                "title": "有效前沿",
                "x_title": "预期波动",
                "y_title": "预期收益",
                "traces": [_trace("有效前沿", [_num(row.get("volatility")) for row in frontier], [_num(row.get("expected_return")) for row in frontier], kind="scatter", mode="lines+markers", text=[row.get("solver") for row in frontier])],
            },
        },
        "strategy": {
            "title": "测试期风险袖套收益贡献",
            "note": "贡献以期初主动权重乘下期资产收益计算，实施残差和交易成本单独保留。",
            "table": _table(
                [
                    ("group", "风险袖套", "text"),
                    ("average_weight", "平均权重", "percentile"),
                    ("active_contribution", "主动贡献", "signed_percent"),
                ],
                attribution_rows,
            ),
            "chart": {
                "title": "风险袖套主动贡献",
                "x_title": "风险袖套",
                "y_title": "累计主动贡献",
                "traces": [_trace("主动贡献", [row["group"] for row in attribution_rows], [row["active_contribution"] for row in attribution_rows], kind="bar")],
            },
        },
    }


def portfolio_visuals(data: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Graph-first optimizer evidence built only from the frozen optimizer snapshot."""
    visuals = _portfolio_visuals_base(data, metrics)
    for block in visuals.values():
        block["display"] = "charts_only"

    weight_rows = (visuals["descriptive"].get("table") or {}).get("rows") or []
    asset_labels = [str(row.get("asset") or "标的") for row in weight_rows]
    matrix_raw = [
        [
            _num(row.get("weight")),
            _num(row.get("risk_contribution")),
            _num(row.get("position_cap")),
            _num(row.get("expected_return")),
            _num(row.get("volatility")),
        ]
        for row in weight_rows
    ]
    column_ranges = []
    for column in range(5):
        values = [row[column] for row in matrix_raw]
        lower = min(values, default=0.0)
        upper = max(values, default=1.0)
        column_ranges.append((lower, upper if upper > lower else lower + 1.0))
    matrix_scores = [
        [
            (value - column_ranges[column][0])
            / (column_ranges[column][1] - column_ranges[column][0])
            for column, value in enumerate(row)
        ]
        for row in matrix_raw
    ]
    visuals["descriptive"]["chart"] = {
        "title": "权重、风险贡献与仓位上限条件色矩阵",
        "heatmap": {
            "x": ["组合权重", "风险贡献", "仓位上限", "预期收益", "年化波动"],
            "y": asset_labels,
            "z": matrix_scores,
            "zmin": 0.0,
            "zmax": 1.0,
            "text": [
                [
                    f"{_num(row.get('weight')):.1%}",
                    f"{_num(row.get('risk_contribution')):.1%}",
                    f"{_num(row.get('position_cap')):.1%}",
                    f"{_num(row.get('expected_return')):.1%}",
                    f"{_num(row.get('volatility')):.1%}",
                ]
                for row in weight_rows
            ],
        },
    }
    visuals["descriptive"]["secondary_charts"] = [
        {
            "title": "预期收益—波动—当前权重",
            "x_title": "年化波动",
            "y_title": "预期收益",
            "traces": [
                _trace(
                    "标的",
                    [row.get("volatility") for row in weight_rows],
                    [row.get("expected_return") for row in weight_rows],
                    kind="scatter",
                    mode="markers+text",
                    text=asset_labels,
                    marker_size=[max(9.0, _num(row.get("weight")) * 110.0) for row in weight_rows],
                    marker_color=[row.get("risk_contribution") for row in weight_rows],
                )
            ],
        }
    ]

    history = visuals["history"].get("chart") or {}
    split_labels = {"train": "训练期", "validation": "验证期", "valid": "验证期", "test": "测试期"}
    metric_rows = [row for row in metrics if row.get("split") in split_labels]
    visuals["history"]["secondary_charts"] = [
        {
            "title": "同口径历史回撤",
            "x_title": "日期",
            "y_title": "回撤",
            "traces": [
                _trace(
                    str(trace.get("name") or "策略"),
                    list(trace.get("x") or []),
                    _drawdown(trace.get("y") or []),
                    color=trace.get("color"),
                )
                for trace in history.get("traces") or []
            ],
        },
        {
            "title": "训练、验证、测试表现",
            "x_title": "样本",
            "y_title": "夏普与信息比率",
            "y2_title": "年化收益与最大回撤",
            "traces": [
                _trace("夏普", [split_labels[row["split"]] for row in metric_rows], [_num(row.get("sharpe")) for row in metric_rows], kind="bar"),
                _trace("信息比率", [split_labels[row["split"]] for row in metric_rows], [_num(row.get("information_ratio")) for row in metric_rows], kind="bar", color="#808080"),
                _trace("年化收益", [split_labels[row["split"]] for row in metric_rows], [_num(row.get("annual_return")) for row in metric_rows], axis="y2", color="#ffc000", mode="lines+markers"),
                _trace("最大回撤", [split_labels[row["split"]] for row in metric_rows], [_num(row.get("max_drawdown")) for row in metric_rows], axis="y2", color="#2f75b5", mode="lines+markers"),
            ],
        },
    ]

    optimization = data.get("optimization") or {}
    solver_rows = (visuals["diagnostics"].get("table") or {}).get("rows") or []
    constraint_rows = ((optimization.get("constraint_slack") or {}).get("rows") or [])
    frontier = optimization.get("efficient_frontier") or []
    selected_spec = optimization.get("selected_spec") or {}
    selected_risk_aversion = _num(selected_spec.get("risk_aversion"), float("nan"))
    selected_frontier = min(
        frontier,
        key=lambda row: abs(_num(row.get("risk_aversion")) - selected_risk_aversion),
        default={},
    )
    frontier_chart = visuals["diagnostics"].get("chart") or {}
    frontier_chart["title"] = "有效前沿与当前求解落点"
    if selected_frontier:
        frontier_chart.setdefault("traces", []).append(
            _trace(
                "当前求解",
                [_num(selected_frontier.get("volatility"))],
                [_num(selected_frontier.get("expected_return"))],
                kind="scatter",
                mode="markers+text",
                text=[f"C{str(selected_spec.get('candidate_id') or '').lstrip('C')}"],
                color="#b42318",
                marker_size=15,
            )
        )
    visuals["diagnostics"]["chart"] = frontier_chart
    solver_names = [str(row.get("actual_solver") or row.get("solver") or "求解器") for row in solver_rows]
    visuals["diagnostics"]["secondary_charts"] = [
        {
            "title": "求解速度、迭代与约束精度",
            "x_title": "求解器",
            "y_title": "中位耗时(ms)",
            "y2_title": "迭代与有效位数",
            "traces": [
                _trace("中位耗时", solver_names, [_num(row.get("median_ms")) for row in solver_rows], kind="bar"),
                _trace("迭代次数", solver_names, [_num(row.get("iterations")) for row in solver_rows], axis="y2", color="#ffc000", mode="lines+markers"),
                _trace("约束有效位数", solver_names, [_num(row.get("accuracy_digits")) for row in solver_rows], axis="y2", color="#168a47", mode="lines+markers"),
            ],
        },
        {
            "title": "约束边界紧度",
            "x_title": "约束",
            "y_title": "可用松弛",
            "log_y": True,
            "traces": [
                _trace(
                    "松弛",
                    [row.get("constraint") for row in constraint_rows],
                    [max(abs(_num(row.get("slack"))), 1e-12) for row in constraint_rows],
                    kind="bar",
                )
            ],
        },
    ]

    backtest = data.get("backtest") or {}
    cost_rows = backtest.get("cost_sensitivity_test") or []
    stress_rows = backtest.get("stress_scenarios") or []
    visuals["strategy"]["secondary_charts"] = [
        {
            "title": "交易成本冲击",
            "x_title": "单边成本(bp)",
            "y_title": "夏普",
            "y2_title": "年化收益与成本拖累",
            "traces": [
                _trace("夏普", [_num(row.get("cost_bps")) for row in cost_rows], [_num(row.get("sharpe")) for row in cost_rows], color="#b42318", mode="lines+markers"),
                _trace("年化收益", [_num(row.get("cost_bps")) for row in cost_rows], [_num(row.get("annual_return")) for row in cost_rows], axis="y2", color="#ffc000", mode="lines+markers"),
                _trace("成本拖累", [_num(row.get("cost_bps")) for row in cost_rows], [_num(row.get("cost_drag")) for row in cost_rows], axis="y2", color="#2f75b5", mode="lines+markers"),
            ],
        },
        {
            "title": "历史压力情景",
            "x_title": "情景",
            "y_title": "区间收益与最大回撤",
            "traces": [
                _trace("组合收益", [row.get("scenario") for row in stress_rows], [_num(row.get("return")) for row in stress_rows], kind="bar"),
                _trace("基准收益", [row.get("scenario") for row in stress_rows], [_num(row.get("benchmark_return")) for row in stress_rows], kind="bar", color="#808080"),
                _trace("最大回撤", [row.get("scenario") for row in stress_rows], [_num(row.get("max_drawdown")) for row in stress_rows], color="#2f75b5", mode="lines+markers"),
            ],
        },
    ]
    return visuals

def kline_visuals(audit: dict[str, Any]) -> dict[str, Any]:
    candidates = audit.get("candidates") or []
    candidate_rows = []
    for row in candidates:
        train = row.get("train") or {}
        validation = row.get("validation") or {}
        test = row.get("test_report_only") or {}
        candidate_rows.append(
            {
                "candidate": f"{row.get('universe')} / {row.get('frequency')}",
                "grade": row.get("grade"),
                "score": _num(row.get("score")),
                "train_sharpe": _num(train.get("sharpe")),
                "validation_sharpe": _num(validation.get("sharpe")),
                "test_sharpe": _num(test.get("sharpe")),
                "validation_rank_ic": _num(validation.get("rank_ic")),
                "validation_turnover": _num(validation.get("turnover")),
                "passed": bool(row.get("passed")),
                "trend": [_num(train.get("sharpe")), _num(validation.get("sharpe")), _num(test.get("sharpe"))],
            }
        )
    candidate_rows.sort(key=lambda row: row["score"], reverse=True)
    check_names = sorted(
        {
            check
            for row in candidates
            for check in (row.get("checks") or {})
        }
    )
    heat_z = [
        [1 if (row.get("checks") or {}).get(check) else 0 for check in check_names]
        for row in candidates
    ]
    return {
        "descriptive": {
            "title": "跨股票候选监测矩阵",
            "note": "当前没有可晋级模型。表中保留训练、验证和封存测试差异以暴露失稳。",
            "table": _table(
                [
                    ("candidate", "股票池/频率", "text"),
                    ("grade", "等级", "status"),
                    ("score", "完整性分", "number"),
                    ("train_sharpe", "训练夏普", "signed"),
                    ("validation_sharpe", "验证夏普", "signed"),
                    ("test_sharpe", "测试夏普", "signed"),
                    ("trend", "三段表现", "sparkline"),
                ],
                candidate_rows,
            ),
            "chart": {
                "title": "训练与验证夏普",
                "x_title": "训练夏普",
                "y_title": "验证夏普",
                "traces": [_trace("候选", [row["train_sharpe"] for row in candidate_rows], [row["validation_sharpe"] for row in candidate_rows], kind="scatter", mode="markers", text=[row["candidate"] for row in candidate_rows])],
            },
        },
        "history": {
            "title": "训练、验证、封存测试分段对照",
            "note": "三段并列是诊断，不是连续净值替代；测试期不参与候选方向和公式选择。",
            "table": _table(
                [
                    ("candidate", "股票池/频率", "text"),
                    ("train_sharpe", "训练", "signed"),
                    ("validation_sharpe", "验证", "signed"),
                    ("test_sharpe", "封存测试", "signed"),
                ],
                candidate_rows,
            ),
            "chart": {
                "title": "候选三段夏普",
                "x_title": "候选",
                "y_title": "夏普",
                "traces": [
                    _trace("训练", [row["candidate"] for row in candidate_rows], [row["train_sharpe"] for row in candidate_rows], kind="bar"),
                    _trace("验证", [row["candidate"] for row in candidate_rows], [row["validation_sharpe"] for row in candidate_rows], kind="bar"),
                    _trace("封存测试", [row["candidate"] for row in candidate_rows], [row["test_sharpe"] for row in candidate_rows], kind="bar"),
                ],
            },
        },
        "diagnostics": {
            "title": "跨股票晋级检查矩阵",
            "note": "每格来自审计快照的布尔检查。任一核心样本门禁失败都不能晋级。",
            "table": _table(
                [
                    ("candidate", "股票池/频率", "text"),
                    ("grade", "等级", "status"),
                    ("passed", "最终晋级", "status"),
                    ("validation_rank_ic", "验证RankIC", "signed"),
                    ("validation_turnover", "验证换手", "number"),
                ],
                candidate_rows,
            ),
            "chart": {
                "title": "候选检查热力图",
                "x_title": "检查项",
                "y_title": "候选",
                "heatmap": {
                    "x": check_names,
                    "y": [f"{row.get('universe')}/{row.get('frequency')}" for row in candidates],
                    "z": heat_z,
                },
            },
        },
        "strategy": {
            "title": "候选收益质量与执行代价",
            "note": "验证RankIC与换手同时进入诊断，当前合格候选数为零。",
            "table": _table(
                [
                    ("candidate", "股票池/频率", "text"),
                    ("score", "完整性分", "number"),
                    ("validation_rank_ic", "验证RankIC", "signed"),
                    ("validation_turnover", "验证换手", "number"),
                    ("test_sharpe", "测试夏普", "signed"),
                    ("passed", "晋级", "status"),
                ],
                candidate_rows,
            ),
            "chart": {
                "title": "验证RankIC与换手",
                "x_title": "验证换手",
                "y_title": "验证RankIC",
                "traces": [_trace("候选", [row["validation_turnover"] for row in candidate_rows], [row["validation_rank_ic"] for row in candidate_rows], kind="scatter", mode="markers", text=[row["candidate"] for row in candidate_rows])],
            },
        },
    }
