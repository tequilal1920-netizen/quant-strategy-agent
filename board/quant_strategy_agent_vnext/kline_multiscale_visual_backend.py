"""Dense five-panel visuals for the causal multi-scale K-line study."""

from __future__ import annotations

import math
from typing import Any


PALETTE = ["#c00000", "#ffc000", "#2f75b5", "#808080", "#ed7d31", "#7030a0"]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sample(rows: list[Any], limit: int = 180) -> list[Any]:
    if len(rows) <= limit:
        return rows
    positions = sorted(
        {
            round(position * (len(rows) - 1) / max(limit - 1, 1))
            for position in range(limit)
        }
    )
    return [rows[position] for position in positions]


def _date(value: Any) -> str:
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _trace(
    name: str,
    x: list[Any],
    y: list[Any],
    *,
    kind: str = "scatter",
    axis: str = "y",
    color: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": kind,
        "mode": "lines" if kind != "bar" else None,
        "name": name,
        "x": x,
        "y": y,
        "axis": axis,
    }
    if color:
        result["color"] = color
    return result


def _table(columns: list[tuple[str, str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "columns": [
            {"key": key, "label": label, "format": value_format}
            for key, label, value_format in columns
        ],
        "rows": rows,
    }


def _selected(model: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = model.get("selected") or {}
    result = ((model.get("results") or {}).get(selected.get("universe")) or {})
    candidate = ((result.get("candidates") or {}).get(selected.get("candidate")) or {})
    return result, candidate


def _latest_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for rank, item in enumerate((result.get("latest") or [])[:11], start=1):
        rows.append(
            {
                "name": item.get("名称") or item.get("代码"),
                "rank": rank,
                "score": _num(item.get("综合形态")),
                "daily": _num(item.get("日线趋势")),
                "breakout": _num(item.get("放量突破")),
                "pullback": _num(item.get("趋势回撤")),
                "quiet": _num(item.get("缩量蓄势")),
                "weekly": _num(item.get("周线形态")),
                "trend": [
                    _num(item.get("日线趋势")),
                    _num(item.get("放量突破")),
                    _num(item.get("趋势回撤")),
                    _num(item.get("缩量蓄势")),
                    _num(item.get("周线形态")),
                ],
            }
        )
    return rows


def _expert_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    names = result.get("expert_names") or []
    posterior = result.get("posterior_weights") or []
    rank_ic = result.get("expert_rank_ic") or []
    rows = []
    for position, name in enumerate(names, start=1):
        weights = [_num(row[position]) for row in posterior if len(row) > position]
        recent_ic = [
            _num(row[position])
            for row in rank_ic[-26:]
            if len(row) > position and row[position] is not None
        ]
        rows.append(
            {
                "name": name,
                "weight": weights[-1] if weights else 0.0,
                "rank_ic": sum(recent_ic) / len(recent_ic) if recent_ic else 0.0,
                "positive_ratio": (
                    sum(value > 0 for value in recent_ic) / len(recent_ic)
                    if recent_ic else 0.0
                ),
                "trend": weights[-36:],
            }
        )
    return rows


def _split_rows(candidate: dict[str, Any], model_label: str = "LLM记忆多周期") -> list[dict[str, Any]]:
    metrics = candidate.get("metrics") or {}
    labels = {
        "train": "训练",
        "valid": "验证",
        "test": "封存测试",
        "full": "全样本诊断",
    }
    rows = []
    for key in ("train", "valid", "test", "full"):
        item = metrics.get(key) or {}
        rows.append(
            {
                "model": model_label,
                "split": labels[key],
                "periods": int(item.get("periods") or 0),
                "annual_return": _num(item.get("annual_return")),
                "sharpe": _num(item.get("sharpe")),
                "max_drawdown": _num(item.get("max_drawdown")),
                "turnover": _num(item.get("turnover")),
                "rank_ic": _num(item.get("rank_ic")),
                "win_rate": _num(item.get("win_rate")),
            }
        )
    return rows


def _split_axis(row: dict[str, Any]) -> str:
    return f"{row.get('model', '模型')}·{row.get('split', '')}"


def _curve_rows(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    peak = 0.0
    for item in candidate.get("curve") or []:
        if len(item) < 7:
            continue
        nav = _num(item[1], 1.0)
        peak = max(peak, nav)
        rows.append(
            {
                "date": str(item[0]),
                "nav": nav,
                "relative": _num(item[3], nav),
                "drawdown": nav / peak - 1.0 if peak > 0 else 0.0,
                "turnover": _num(item[6]),
            }
        )
    return _sample(rows, 180)


def _importance_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    values = (model.get("supervised_ranker") or {}).get("feature_importance") or []
    total = sum(_num(item.get("split_count")) for item in values) or 1.0
    rows = []
    for item in values[:11]:
        feature = str(item.get("feature") or "")
        layer = feature.split("_", 1)[0] if "_" in feature else "形态"
        count = int(_num(item.get("split_count")))
        rows.append(
            {
                "factor": feature.replace("_", "·"),
                "layer": layer,
                "split_count": count,
                "share": count / total,
            }
        )
    return rows


def kline_multiscale_visuals(model: dict[str, Any]) -> dict[str, Any]:
    result, candidate = _selected(model)
    release_approved = bool(
        (model.get("deployment_selected") or {}).get("release_approved")
    )
    latest = _latest_rows(result)
    experts = _expert_rows(result)
    split_rows = _split_rows(candidate, "模型二：LLM记忆多周期")
    pure = model.get("pure_technical_model") or {}
    pure_selected = pure.get("selected") or {}
    pure_result = ((pure.get("results") or {}).get(pure_selected.get("universe")) or {})
    pure_candidate = ((pure_result.get("candidates") or {}).get(pure_selected.get("candidate")) or {})
    if pure_candidate:
        split_rows.extend(_split_rows(pure_candidate, "模型一：纯技术信号栈"))
    curve = _curve_rows(candidate) if release_approved else []
    importance = _importance_rows(model)
    names = result.get("expert_names") or []
    posterior = _sample(result.get("posterior_weights") or [], 180)
    states = _sample(result.get("state_history") or [], 180)
    state_by_date = {str(row[0]): _num(row[2]) for row in states if len(row) >= 3}

    latest_names = [row["name"] for row in latest[:10]]
    descriptive_traces = [
        _trace(
            "综合领先",
            latest_names,
            [len(latest[:10]) - row["rank"] + 1 for row in latest[:10]],
            kind="bar",
            axis="y2",
            color=PALETTE[0],
        )
    ]
    for position, (key, label) in enumerate(
        (("daily", "日线趋势"), ("breakout", "放量突破"), ("pullback", "趋势回撤"), ("quiet", "缩量蓄势"), ("weekly", "周线形态")),
        start=1,
    ):
        descriptive_traces.append(
            _trace(label, latest_names, [row[key] for row in latest[:10]], color=PALETTE[position % len(PALETTE)])
        )

    history_traces = []
    for position, name in enumerate(names, start=1):
        history_traces.append(
            _trace(
                name,
                [_date(row[0]) for row in posterior],
                [_num(row[position]) if len(row) > position else 0.0 for row in posterior],
                color=PALETTE[(position - 1) % len(PALETTE)],
            )
        )
    history_traces.append(
        _trace(
            "风险预算",
            [_date(row[0]) for row in posterior],
            [state_by_date.get(str(row[0]), 0.0) for row in posterior],
            axis="y2",
            color=PALETTE[3],
        )
    )

    return {
        "descriptive": {
            "title": "最新多周期技术截面",
            "note": "全A可交易股票按行业与市值处理后的当前形态排序。",
            "table": _table(
                [
                    ("name", "标的", "text"),
                    ("rank", "排名", "integer"),
                    ("daily", "日线", "percentile"),
                    ("breakout", "突破", "percentile"),
                    ("pullback", "回撤", "percentile"),
                    ("weekly", "周线", "percentile"),
                    ("trend", "五维形态", "sparkline"),
                ],
                latest,
            ),
            "chart": {
                "title": "当前领先标的五维形态",
                "x_title": "标的",
                "y_title": "截面分位",
                "traces": descriptive_traces,
            },
                "y2_title": "综合领先顺序",
        },
        "history": {
            "title": "专家权重与状态迁移",
            "note": "权重仅使用已经成熟的历史反馈，当前信号不读取未来收益。",
            "table": _table(
                [
                    ("name", "形态专家", "text"),
                    ("weight", "当前权重", "signed"),
                    ("rank_ic", "近26周信息系数", "signed"),
                    ("positive_ratio", "正向占比", "percentile"),
                    ("trend", "权重轨迹", "sparkline"),
                ],
                experts,
            ),
            "chart": {
                "title": "因果后验权重与风险预算",
                "x_title": "日期",
                "y_title": "专家权重",
                "y2_title": "风险预算",
                "traces": history_traces,
            },
        },
        "diagnostics": {
            "title": "分样本模型诊断",
            "note": "训练验证用于选择，封存测试只用于发布闸门。",
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("split", "样本", "text"),
                    ("periods", "周数", "integer"),
                    ("annual_return", "年化收益", "signed_percent"),
                    ("sharpe", "夏普", "signed"),
                    ("max_drawdown", "最大回撤", "signed_percent"),
                    ("turnover", "单周换手", "signed"),
                    ("rank_ic", "信息系数", "signed"),
                    ("win_rate", "胜率", "percentile"),
                ],
                split_rows,
            ),
            "chart": {
                "title": "训练、验证与封存测试稳定性",
                "x_title": "样本",
                "y_title": "夏普",
                "y2_title": "信息系数",
                "traces": [
                    _trace("夏普", [_split_axis(row) for row in split_rows], [row["sharpe"] for row in split_rows], kind="bar", color=PALETTE[0]),
                    _trace("信息系数", [_split_axis(row) for row in split_rows], [row["rank_ic"] for row in split_rows], axis="y2", color=PALETTE[2]),
                ],
            },
        },
        "strategy": {
            "title": "成本后净值与特征归因" if release_approved else "研究形态贡献归因",
            "note": (
                "成本后结果已通过发布闸门。"
                if release_approved
                else "暂无可部署策略；仅展示研究模型的特征贡献。"
            ),
            "table": _table(
                [
                    ("factor", "特征", "text"),
                    ("layer", "层级", "text"),
                    ("split_count", "分裂次数", "integer"),
                    ("share", "重要度占比", "percentile"),
                ],
                importance,
            ),
            "chart": {
                "title": "多空形态检验净值、回撤与换手" if release_approved else "形态贡献分裂占比",
                "x_title": "日期" if release_approved else "特征",
                "y_title": "累计净值" if release_approved else "重要度占比",
                "y2_title": "回撤与换手" if release_approved else "",
                "traces": (
                    [
                        _trace("成本后净值", [_date(row["date"]) for row in curve], [row["nav"] for row in curve], color=PALETTE[0]),
                        _trace("相对净值", [_date(row["date"]) for row in curve], [row["relative"] for row in curve], color=PALETTE[1]),
                        _trace("回撤", [_date(row["date"]) for row in curve], [row["drawdown"] for row in curve], axis="y2", color=PALETTE[2]),
                        _trace("单周换手", [_date(row["date"]) for row in curve], [row["turnover"] for row in curve], axis="y2", color=PALETTE[3]),
                    ]
                    if release_approved
                    else [
                        _trace(
                            "分裂占比",
                            [row["factor"] for row in importance],
                            [row["share"] for row in importance],
                            kind="bar",
                            color=PALETTE[0],
                        )
                    ]
                ),
            },
        },
    }


