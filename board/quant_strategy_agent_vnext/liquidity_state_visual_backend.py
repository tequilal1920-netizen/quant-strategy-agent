"""Dense five-panel evidence for the exact-series liquidity-state model."""

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


def _indicator_rows(snapshot: dict[str, Any], page: str) -> list[dict[str, Any]]:
    charts = (((snapshot.get("pages") or {}).get(page) or {}).get("charts") or [])
    rows: list[dict[str, Any]] = []
    for chart in charts:
        traces = chart.get("traces") or []
        if not traces:
            continue
        trace = max(traces, key=lambda item: len(item.get("y") or []))
        pairs = [
            (date, _num(value))
            for date, value in zip(trace.get("x") or [], trace.get("y") or [])
            if value is not None
        ]
        if not pairs:
            continue
        values = [value for _, value in pairs]
        latest = values[-1]
        previous = values[-2] if len(values) > 1 else latest
        percentile = sum(value <= latest for value in values) / len(values)
        rows.append(
            {
                "name": chart.get("title") or trace.get("name"),
                "frequency": chart.get("frequency") or "截面",
                "latest": latest,
                "change": latest - previous,
                "percentile": percentile,
                "date": str(pairs[-1][0]),
                "trend": values[-36:],
            }
        )
    return rows


def _split_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    selected = model.get("selected") or {}
    metrics = ((selected.get("evaluation") or {}).get("split_metrics") or {})
    labels = {
        "train": "训练",
        "valid": "验证",
        "test": "测试只报告",
        "full": "全样本诊断",
    }
    rows = []
    for key in ("train", "valid", "test", "full"):
        row = metrics.get(key) or {}
        rows.append(
            {
                "split": labels[key],
                "periods": int(row.get("periods") or 0),
                "annual_return": _num(row.get("annual_return")),
                "sharpe": _num(row.get("sharpe")),
                "information_ratio": _num(row.get("information_ratio")),
                "max_drawdown": _num(row.get("max_drawdown")),
                "average_exposure": _num(row.get("average_exposure")),
            }
        )
    return rows


def liquidity_state_visuals(
    snapshot: dict[str, Any],
    page: str,
    model: dict[str, Any],
) -> dict[str, Any]:
    selected = model.get("selected") or {}
    indicator_rows = _indicator_rows(snapshot, page)
    nav = _sample(selected.get("nav") or [], 180)
    group_rows = []
    for row in selected.get("latest_group_state") or []:
        group_rows.append(
            {
                "group": row.get("group"),
                "state": _num(row.get("state")),
                "posterior_weight": _num(row.get("posterior_weight")),
                "contribution": _num(row.get("contribution")),
                "confidence": _num(row.get("direction_confidence")),
            }
        )
    conditional = selected.get("conditional_returns_train_validation") or []
    split_rows = _split_rows(model)
    latest_exposure = _num((nav[-1] if nav else {}).get("equity_exposure"))

    return {
        "descriptive": {
            "title": "真实资金截面",
            "note": "当前二级页面的原始指标保持原口径，分位数只用于横向阅读。",
            "table": _table(
                [
                    ("name", "指标", "text"),
                    ("frequency", "频率", "text"),
                    ("latest", "最新值", "number"),
                    ("change", "边际变化", "signed"),
                    ("percentile", "历史分位", "percentile"),
                    ("trend", "走势", "sparkline"),
                ],
                indicator_rows[:10],
            ),
            "chart": {
                "title": "当前历史分位",
                "x_title": "资金指标",
                "y_title": "历史分位",
                "traces": [
                    _trace(
                        "历史分位",
                        [row["name"] for row in indicator_rows],
                        [row["percentile"] for row in indicator_rows],
                        kind="bar",
                        color=PALETTE[0],
                    )
                ],
            },
        },
        "history": {
            "title": "资金状态与权益预算",
            "note": "周频资金后验在月末执行，剩余仓位配置银华日利。",
            "table": _table(
                [
                    ("group", "资金类别", "text"),
                    ("state", "当前状态", "signed"),
                    ("posterior_weight", "后验权重", "signed"),
                    ("contribution", "状态贡献", "signed"),
                    ("confidence", "方向置信", "percentile"),
                ],
                group_rows,
            ),
            "chart": {
                "title": "滚动资金状态与权益仓位",
                "x_title": "日期",
                "y_title": "资金状态",
                "y2_title": "权益仓位",
                "traces": [
                    _trace("资金状态", [row.get("date") for row in nav], [row.get("signal") for row in nav], color=PALETTE[0]),
                    _trace("权益仓位", [row.get("date") for row in nav], [row.get("equity_exposure") for row in nav], axis="y2", color=PALETTE[2]),
                ],
            },
        },
        "diagnostics": {
            "title": "分样本诊断与状态单调性",
            "note": "候选排序只用训练与验证，测试期不参与模型选择。",
            "table": _table(
                [
                    ("split", "样本", "text"),
                    ("periods", "期数", "integer"),
                    ("annual_return", "年化收益", "percentile"),
                    ("sharpe", "夏普", "signed"),
                    ("information_ratio", "信息比率", "signed"),
                    ("max_drawdown", "最大回撤", "signed"),
                    ("average_exposure", "平均仓位", "percentile"),
                ],
                split_rows,
            ),
            "chart": {
                "title": "训练验证资金状态五分位",
                "x_title": "状态分位",
                "y_title": "下一周平均收益",
                "y2_title": "平均权益仓位",
                "traces": [
                    _trace("下一期平均收益", [f"Q{row.get('bucket')}" for row in conditional], [row.get("average_next_period_return", row.get("average_next_week_return")) for row in conditional], kind="bar", color=PALETTE[0]),
                    _trace("平均权益仓位", [f"Q{row.get('bucket')}" for row in conditional], [row.get("average_exposure") for row in conditional], axis="y2", color=PALETTE[2]),
                ],
            },
        },
        "strategy": {
            "title": "策略净值与资金归因",
            "note": "剩余仓位按银华日利累计分红总收益计量；测试期只报告。",
            "table": _table(
                [
                    ("group", "资金类别", "text"),
                    ("contribution", "当前贡献", "signed"),
                    ("state", "状态", "signed"),
                    ("posterior_weight", "权重", "signed"),
                    ("confidence", "置信", "percentile"),
                ],
                group_rows,
            ),
            "chart": {
                "title": f"净值、回撤与当前权益仓位 {latest_exposure:.1%}",
                "x_title": "日期",
                "y_title": "累计净值",
                "y2_title": "策略回撤",
                "traces": [
                    _trace("资金策略", [row.get("date") for row in nav], [row.get("strategy_nav") for row in nav], color=PALETTE[0]),
                    _trace("上证综指", [row.get("date") for row in nav], [row.get("benchmark_nav") for row in nav], color=PALETTE[1]),
                    _trace("策略回撤", [row.get("date") for row in nav], [row.get("drawdown") for row in nav], axis="y2", color=PALETTE[3]),
                ],
            },
        },
    }
