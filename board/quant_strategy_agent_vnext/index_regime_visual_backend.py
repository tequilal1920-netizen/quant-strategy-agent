"""Information-dense visual payload for the index-enhancement research page."""

from __future__ import annotations

from typing import Any

from research_visual_backend import _date_label, _num, _sample, _table, _trace


FACTOR_LABELS = {
    "quality_value_low_crowding_v8": "质量价值低拥挤",
    "fundamental_quality_v4": "基本面质量",
    "domain_quality_neutral_v9": "行业内质量",
    "domain_value_neutral_v9": "行业内价值",
    "factor_domain_agent_v9": "跨域综合",
    "domain_money_neutral_v9": "行业内资金",
    "domain_technical_neutral_v9": "行业内技术",
    "trend_quality_v4": "趋势质量",
    "kline_context_agent_v8": "K线情境",
    "kline_executable_skill_v11": "K线执行",
}


def _split_label(value: str) -> str:
    return {
        "train": "训练期",
        "valid": "验证期",
        "validation": "验证期",
        "test": "封存测试",
        "full": "全样本",
    }.get(value, value)


def _factor_rows(selected: dict[str, Any]) -> list[dict[str, Any]]:
    alpha = selected.get("alpha_diagnostics") or {}
    months = alpha.get("monthly_diagnostics") or []
    latest = months[-1] if months else {}
    weights = latest.get("factor_weights") or alpha.get("latest_factor_weights") or {}
    posterior = latest.get("posterior") or {}
    rows: list[dict[str, Any]] = []
    for factor, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
        detail = posterior.get(factor) or {}
        trend = [
            _num((row.get("factor_weights") or {}).get(factor))
            for row in months[-24:]
        ]
        rows.append(
            {
                "factor": FACTOR_LABELS.get(factor, factor),
                "family": "稳健" if factor in {
                    "quality_value_low_crowding_v8",
                    "fundamental_quality_v4",
                    "domain_quality_neutral_v9",
                    "domain_value_neutral_v9",
                    "factor_domain_agent_v9",
                } else "快速",
                "weight": _num(weight),
                "posterior_ic": _num(detail.get("posterior_mean")),
                "evidence_z": _num(detail.get("evidence_z")),
                "positive_ratio": _num(detail.get("positive_ratio")),
                "trend": trend,
            }
        )
    return rows


def index_regime_visuals(payload: dict[str, Any]) -> dict[str, Any]:
    selected = payload.get("selected") or {}
    summary = selected.get("summary") or {}
    split_metrics = summary.get("split_metrics") or {}
    metrics = [
        {"split": _split_label(name), **(values or {})}
        for name, values in split_metrics.items()
        if name in {"train", "valid", "test", "full"}
    ]
    factor_rows = _factor_rows(selected)
    nav = selected.get("nav") or []
    dates = [_date_label(row.get("trade_date")) for row in nav]
    monthly = (selected.get("portfolio_evidence") or {}).get("monthly_evidence") or []
    monthly_tail = [
        {
            "date": _date_label(row.get("trade_date")),
            "confidence": _num(row.get("alpha_confidence")),
            "active_share": _num(row.get("active_share")),
            "tracking_error": _num(row.get("estimated_tracking_error")),
            "turnover": _num(row.get("one_way_turnover")),
            "excess": _num(row.get("excess_return")),
        }
        for row in monthly
    ]
    yearly = [
        {"year": row.get("year"), **row}
        for row in selected.get("yearly_metrics") or []
    ]
    costs = selected.get("cost_sensitivity") or []
    return {
        "descriptive": {
            "title": "多期限因子后验与当前权重",
            "note": "权重仅使用持有期已结束的历史 RankIC，证据减弱时主动预算回归基准。",
            "table": _table(
                [
                    ("factor", "因子", "text"),
                    ("family", "类型", "text"),
                    ("weight", "当前权重", "percent"),
                    ("posterior_ic", "后验IC", "signed"),
                    ("evidence_z", "证据强度", "signed"),
                    ("positive_ratio", "正IC率", "percent"),
                    ("trend", "近24期权重", "sparkline"),
                ],
                factor_rows,
            ),
            "chart": {
                "title": "后验有效性与配置权重",
                "x_title": "后验 RankIC",
                "y_title": "当前权重",
                "traces": [
                    _trace(
                        "因子",
                        [row["posterior_ic"] for row in factor_rows],
                        [row["weight"] for row in factor_rows],
                        kind="scatter",
                        mode="markers+text",
                        text=[row["factor"] for row in factor_rows],
                        color="#c00000",
                    )
                ],
            },
        },
        "history": {
            "title": "基准锚定净值与相对净值",
            "note": "组合始终满仓复制中证800，主动卫星不参与绝对仓位择时。",
            "table": _table(
                [
                    ("split", "样本", "text"),
                    ("annual_return", "年化收益", "percent"),
                    ("excess_annual_return", "年化超额", "signed_percent"),
                    ("sharpe", "夏普", "signed"),
                    ("information_ratio", "IR", "signed"),
                    ("tracking_error", "跟踪误差", "percent"),
                    ("relative_max_drawdown", "相对回撤", "signed_percent"),
                ],
                metrics,
            ),
            "chart": {
                "title": "策略、基准与相对净值",
                "x_title": "日期",
                "y_title": "累计净值",
                "y2_title": "相对净值",
                "traces": [
                    _trace("增强组合", _sample(dates, 220), _sample([row.get("nav") for row in nav], 220), color="#c00000"),
                    _trace("中证800基准", _sample(dates, 220), _sample([row.get("benchmark_nav") for row in nav], 220), color="#808080"),
                    _trace("相对净值", _sample(dates, 220), _sample([row.get("relative_nav") for row in nav], 220), axis="y2", color="#2f75b5"),
                ],
            },
        },
        "diagnostics": {
            "title": "主动预算、风险与换手诊断",
            "note": "置信度驱动主动预算，跟踪误差、主动份额与换手同步核验。",
            "table": _table(
                [
                    ("date", "日期", "text"),
                    ("confidence", "置信度", "percent"),
                    ("active_share", "主动份额", "percent"),
                    ("tracking_error", "预计跟踪误差", "percent"),
                    ("turnover", "单边换手", "percent"),
                    ("excess", "当期超额", "signed_percent"),
                ],
                monthly_tail[-11:],
            ),
            "chart": {
                "title": "置信度与主动风险预算",
                "x_title": "日期",
                "y_title": "置信度/主动份额/跟踪误差",
                "y2_title": "单边换手",
                "traces": [
                    _trace("Alpha置信度", _sample([row["date"] for row in monthly_tail], 220), _sample([row["confidence"] for row in monthly_tail], 220), color="#c00000"),
                    _trace("主动份额", _sample([row["date"] for row in monthly_tail], 220), _sample([row["active_share"] for row in monthly_tail], 220), color="#ffc000"),
                    _trace("预计跟踪误差", _sample([row["date"] for row in monthly_tail], 220), _sample([row["tracking_error"] for row in monthly_tail], 220), color="#2f75b5"),
                    _trace("单边换手", _sample([row["date"] for row in monthly_tail], 220), _sample([row["turnover"] for row in monthly_tail], 220), axis="y2", color="#808080"),
                ],
            },
        },
        "strategy": {
            "title": "年度主动收益与成本承压",
            "note": "封存测试只报告，不参与模型排序；成本按双边实际换手逐月扣除。",
            "table": _table(
                [
                    ("year", "年度", "text"),
                    ("annual_return", "策略收益", "percent"),
                    ("excess_annual_return", "主动收益", "signed_percent"),
                    ("information_ratio", "IR", "signed"),
                    ("active_win_rate", "主动胜率", "percent"),
                    ("relative_max_drawdown", "相对回撤", "signed_percent"),
                ],
                yearly[-11:],
            ),
            "chart": {
                "title": "交易成本上升后的主动收益与IR",
                "x_title": "单边成本(bp)",
                "y_title": "年化主动收益",
                "y2_title": "信息比率",
                "traces": [
                    _trace("年化主动收益", [row.get("cost_bps") for row in costs], [row.get("excess_annual_return") for row in costs], color="#c00000"),
                    _trace("信息比率", [row.get("cost_bps") for row in costs], [row.get("information_ratio") for row in costs], axis="y2", color="#2f75b5"),
                ],
            },
        },
    }
