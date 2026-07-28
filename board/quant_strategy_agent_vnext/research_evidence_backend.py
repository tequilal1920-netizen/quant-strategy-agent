"""Compact research-evidence payloads for the isolated vNext dashboard.

The endpoint built from this module never executes a model during an HTTP
request.  It reads the same frozen, quality-gated snapshots as the existing
pages and exposes only the fields needed for mechanism, descriptive,
historical, diagnostic and strategy-evidence views.
"""
from __future__ import annotations

import json
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from research_visual_backend import (
    allocation_visuals,
    factor_visuals,
    index_visuals,
    kline_visuals,
    liquidity_visuals,
    portfolio_visuals,
    rotation_visuals,
)


APP_ROOT = Path(__file__).resolve().parent
DATA_ROOT = APP_ROOT / "data"
FACTOR_RESULT = DATA_ROOT / "factor_strategy_inverse_vol_v32_20260726.json"
INDEX_SHADOW = DATA_ROOT / "index_active_risk_diagnostics.json"
KLINE_AUDIT = DATA_ROOT / "kline_cross_sectional_audit.json"

_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}

REPORT_REFERENCES = {
    "asset": [
        {
            "broker": "国信证券",
            "title": "AI视角驱动的Black-Litterman资产配置",
            "date": "2026-01-12",
            "url": "https://pdf.dfcfw.com/pdf/H3_AP202601121816952139_1.pdf",
        },
        {
            "broker": "中银证券",
            "title": "BL宏观量化策略模型主动配置展望",
            "date": "2025-03",
            "url": "https://pdf.dfcfw.com/pdf/H3_AP202503261647689552_1.pdf",
        },
    ],
    "factor": [
        {
            "broker": "东吴证券",
            "title": "AI重塑量化：基于大语言模型驱动的因子改进与情绪ALPHA挖掘",
            "date": "2026-01-10",
            "url": "https://cloud.gildata.com/queryservice/research/attachment/821383980303.pdf",
        },
    ],
    "kline": [
        {
            "broker": "东吴证券",
            "title": "绝对收益视角下的技术形态专家模型",
            "date": "2026-03-24",
            "url": "https://cloud.gildata.com/queryservice/research/attachment/827696785997.pdf",
        },
    ],
}


def _load(path: Path) -> dict[str, Any]:
    stat = path.stat()
    key = str(path)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == stat.st_mtime_ns:
            return cached[1]
        payload = json.loads(path.read_text(encoding="utf-8"))
        _CACHE[key] = (stat.st_mtime_ns, payload)
        return payload


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _metric_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    aliases = {"valid": "validation"}
    rows = []
    for raw_name in ("train", "validation", "valid", "test"):
        if raw_name not in metrics:
            continue
        name = aliases.get(raw_name, raw_name)
        if any(row["split"] == name for row in rows):
            continue
        item = metrics.get(raw_name) or {}
        rows.append(
            {
                "split": name,
                "annual_return": _finite(item.get("annual_return")),
                "annual_excess_return": _finite(
                    item.get("annual_excess_return", item.get("annual_excess"))
                ),
                "sharpe": _finite(item.get("sharpe")),
                "information_ratio": _finite(
                    item.get("information_ratio", item.get("excess_sharpe"))
                ),
                "max_drawdown": _finite(item.get("max_drawdown")),
                "turnover": _finite(
                    item.get("turnover", item.get("annual_turnover"))
                ),
                "observations": int(
                    item.get("observations", item.get("months", 0)) or 0
                ),
            }
        )
    return rows


def _base(module: str, status: str, as_of: Any) -> dict[str, Any]:
    return {
        "schema_version": "research-evidence/1.0",
        "module": module,
        "status": status,
        "as_of": as_of,
        "layers": [
            {"id": "mechanism", "label": "原理与机理"},
            {"id": "descriptive", "label": "数据描述"},
            {"id": "history", "label": "历史复盘与最新跟踪"},
            {"id": "diagnostics", "label": "模型拟合与诊断"},
            {"id": "strategy", "label": "策略回测与归因"},
        ],
    }


def _allocation() -> dict[str, Any]:
    data = _load(DATA_ROOT / "asset_allocation_snapshot.json")
    audit = (data.get("backtest") or {}).get("selection_audit") or {}
    current = (data.get("allocations") or {}).get("current_cycle") or {}
    rows = _metric_rows(
        {
            "train": audit.get("train_metrics") or {},
            "validation": audit.get("validation_metrics") or {},
            "test": audit.get("test_metrics_report_only") or {},
        }
    )
    active = _metric_rows(
        {
            "train": audit.get("train_active_metrics") or {},
            "validation": audit.get("validation_active_metrics") or {},
            "test": audit.get("test_active_metrics_report_only") or {},
        }
    )
    active_by_split = {row["split"]: row for row in active}
    for row in rows:
        active_row = active_by_split.get(row["split"]) or {}
        row["annual_excess_return"] = _finite(
            active_row.get("annual_excess_return")
        )
        row["information_ratio"] = _finite(
            active_row.get("information_ratio")
        )

    robustness = (data.get("backtest") or {}).get("robustness") or {}
    cost_sensitivity = []
    for item in robustness.get("cost_sensitivity_test") or []:
        normalized = dict(item)
        normalized["cost_bps"] = _finite(
            item.get("cost_bps", item.get("transaction_cost_bps"))
        )
        cost_sensitivity.append(normalized)
    robustness = {**robustness, "cost_sensitivity_test": cost_sensitivity}

    gate_status = str((audit.get("promotion_gate") or {}).get("status") or "")
    status = "conditional_champion" if gate_status == "conditional" else "current_champion"
    payload = _base("asset", status, data.get("data_as_of"))
    payload.update(
        {
            "champion": audit.get("selected_spec") or {},
            "mechanism": {
                "nodes": [
                    "PIT宏观因子",
                    "普林格/基钦/朱格拉/美林状态",
                    "资产收益观点与不确定性",
                    "EWMA/Ledoit-Wolf风险",
                    "约束优化与换手控制",
                    "当前资产权重",
                ],
                "formula": "w*=argmax μᵀw−λwᵀΣw−κ‖w−w₋₁‖，μ由周期状态与相对强弱形成，测试集不参与选模。",
            },
            "descriptive": {
                "current_cycle": current,
                "factor_count": len(data.get("factor_registry") or []),
                "candidate_count": int(audit.get("trial_count") or 0),
                "validation_eligible_count": int(
                    audit.get("validation_eligible_count") or 0
                ),
            },
            "metrics": rows,
            "active_metrics": active,
            "robustness": robustness,
            "governance": {
                "promotion_gate": audit.get("promotion_gate") or {},
                "pbo": audit.get("pbo_cscv"),
                "dsr": audit.get("deflated_sharpe_probability"),
                "test_policy": "report_only",
            },
            "references": REPORT_REFERENCES["asset"],
        }
    )
    payload["visuals"] = allocation_visuals(data, rows)
    return payload


def _liquidity(page: str) -> dict[str, Any]:
    data = _load(DATA_ROOT / "liquidity_snapshot.json")
    page_data = (data.get("pages") or {}).get(page) or {}
    charts = page_data.get("charts") or []
    sources = data.get("source_registry") or {}
    source_ids = sorted(
        {
            trace.get("source_id")
            for chart in charts
            for trace in chart.get("traces") or []
            if trace.get("source_id")
        }
    )
    quality_counts = Counter(
        str((sources.get(source_id) or {}).get("quality") or "unknown")
        for source_id in source_ids
    )
    payload = _base("liquidity", "tracking_not_return_model", page_data.get("as_of"))
    payload.update(
        {
            "mechanism": {
                "nodes": [
                    "原始资金账户/份额/融资数据",
                    "公布时滞与频率对齐",
                    "净流量与存量变化",
                    "滚动标准化与拥挤度",
                    "权益行情关联复盘",
                    "最新资金状态",
                ],
                "formula": "FlowShockₜ=(Flowₜ−rolling median)/rolling MAD；只做时点可得的关联复盘，不把同步相关性写成因果收益。",
            },
            "descriptive": {
                "chart_count": len(charts),
                "source_count": len(source_ids),
                "quality_counts": dict(quality_counts),
                "sources": [
                    {
                        "id": source_id,
                        "label": (sources.get(source_id) or {}).get("label"),
                        "frequency": (sources.get(source_id) or {}).get("frequency"),
                        "quality": (sources.get(source_id) or {}).get("quality"),
                    }
                    for source_id in source_ids
                ],
            },
            "chart_inventory": [
                {
                    "id": chart.get("id"),
                    "title": chart.get("title"),
                    "frequency": chart.get("frequency"),
                    "reference": chart.get("reference"),
                    "quality": chart.get("quality"),
                }
                for chart in charts
            ],
            "governance": {
                "status": (data.get("quality") or {}).get("status"),
                "model_metric_policy": "no_sharpe_for_tracking_pages",
            },
        }
    )
    payload["visuals"] = liquidity_visuals(data, page)
    return payload


def _rotation() -> dict[str, Any]:
    data = _load(DATA_ROOT / "rotation_snapshot.json")
    frequencies = (data.get("industry") or {}).get("frequencies") or {}
    style = (
        ((data.get("style") or {}).get("frequencies") or {}).get("quarterly")
        or {}
    )
    series = []
    for name in ("monthly", "weekly"):
        model = frequencies.get(name) or {}
        series.append(
            {
                "model": name,
                "candidate": model.get("selected_candidate"),
                "gate": (model.get("gate") or {}).get("status"),
                "metrics": _metric_rows(model.get("metrics") or {}),
            }
        )
    series.append(
        {
            "model": "quarterly_style",
            "candidate": style.get("selected_candidate"),
            "gate": (style.get("gate") or {}).get("status"),
            "metrics": _metric_rows(style.get("metrics") or {}),
        }
    )
    payload = _base("rotation", "mixed_governance", data.get("as_of"))
    payload.update(
        {
            "mechanism": {
                "nodes": [
                    "行业专属高频指标",
                    "PIT景气方向与价格确认",
                    "截面标准化与去相关",
                    "月频/周频独立组合",
                    "换手与交易成本",
                    "Top行业及风格权重",
                ],
                "formula": "Scoreᵢ,ₜ=Σₖωₖ·z(PIT indicatorᵢ,ₖ,ₜ)+price confirmation；验证集定型，2022年后只报告。",
            },
            "models": series,
            "descriptive": {
                "industry_count": (
                    (data.get("high_frequency") or {}).get("summary") or {}
                ).get("industry_count"),
                "field_count": (
                    (data.get("high_frequency") or {}).get("summary") or {}
                ).get("field_count"),
                "live_ratio": (
                    (data.get("high_frequency") or {}).get("summary") or {}
                ).get("live_ratio"),
                "style_stability": (
                    ((data.get("style") or {}).get("migration") or {}).get(
                        "stability_rate"
                    )
                ),
            },
            "governance": {
                "test_policy": (data.get("method") or {}).get("test_policy"),
                "warning": "月频和周频测试表现偏弱，当前只保留真实结果，不以测试期反向改参。",
            },
        }
    )
    payload["visuals"] = rotation_visuals(data, series)
    return payload


def _factor() -> dict[str, Any]:
    data = _load(FACTOR_RESULT)
    selection = data.get("selection") or {}
    candidates = (
        ((data.get("models") or {}).get("execution_candidates"))
        or data.get("execution_candidates")
        or {}
    )
    compact_candidates = []
    shadow = None
    for candidate_id, candidate in candidates.items():
        valid = candidate.get("valid") or {}
        train = candidate.get("train") or {}
        row = {
            "candidate": candidate_id,
            "model": candidate.get("model"),
            "policy": candidate.get("execution_policy"),
            "train_sharpe": _finite(train.get("sharpe")),
            "validation_sharpe": _finite(valid.get("sharpe")),
            "validation_rank_ic": _finite(valid.get("rank_ic")),
            "validation_turnover": _finite(valid.get("turnover")),
        }
        compact_candidates.append(row)
        feasible = (
            row["validation_turnover"] <= 0.65
            and row["validation_rank_ic"] > 0
            and _finite(train.get("rank_ic")) > 0
        )
        if feasible and (
            shadow is None
            or row["validation_sharpe"] > shadow["validation_sharpe"]
        ):
            shadow = row
    compact_candidates.sort(
        key=lambda row: row["validation_sharpe"], reverse=True
    )
    metrics = _metric_rows(data.get("metrics") or {})
    adaptive = selection.get("adaptive_icir") or {}
    weights = adaptive.get("last_weights") or []
    payload = _base("factor", "current_champion_with_shadow", data.get("created_at"))
    payload.update(
        {
            "champion": {
                "model": selection.get("selected_model"),
                "policy": selection.get("selected_execution_policy"),
                "candidate": selection.get("best_validation_candidate"),
            },
            "mechanism": {
                "nodes": [
                    "PIT行情/估值/资金特征",
                    "截面正交与中性化",
                    "滚动ICIR/线性/非线性模型",
                    "验证集模型与执行联合选择",
                    "成本感知连续权重",
                    "Alpha组合与晋级门禁",
                ],
                "formula": "αₜ=Σⱼwⱼ,ₜ·z(fⱼ,ₜ)，wⱼ,ₜ仅由滞后非重叠ICIR更新；组合层显式扣除换手成本。",
            },
            "descriptive": {
                "factor_count": len(weights),
                "candidate_count": len(compact_candidates),
                "active_factor_count": adaptive.get("mean_active_factor_count"),
            },
            "metrics": metrics,
            "candidate_diagnostics": compact_candidates[:24],
            "shadow_challenger": {
                **(shadow or {}),
                "promotion_eligible": False,
                "reason": "该候选的封存测试期已经被观察，仅保留为前瞻影子执行方案。",
            },
            "governance": {
                "test_policy": selection.get("test_usage"),
                "gates": data.get("gates") or [],
            },
            "references": REPORT_REFERENCES["factor"],
        }
    )
    payload["visuals"] = factor_visuals(data, metrics, compact_candidates)
    return payload


def _index() -> dict[str, Any]:
    data = _load(DATA_ROOT / "index_enhancement_snapshot.json")
    audit = (data.get("champion_audit") or {}).get("CSI800_ENH") or {}
    shadow = _load(INDEX_SHADOW) if INDEX_SHADOW.exists() else {}
    payload = _base("index", "conditional_champion", data.get("data_as_of"))
    payload.update(
        {
            "champion": {
                "name": audit.get("champion"),
                "status": audit.get("status"),
                "candidate_count": audit.get("candidate_count"),
            },
            "mechanism": {
                "nodes": [
                    "基准成分与PIT因子",
                    "滚动IC可靠度",
                    "行业风格风险暴露",
                    "主动风险优化",
                    "成本与换手约束",
                    "基准权重+主动权重",
                ],
                "formula": "min ½ΔwᵀΣΔw−καᵀΔw+η‖Δw−Δw₋₁‖；行业中性、风格边界与跟踪误差同步校验。",
            },
            "metrics": _metric_rows(audit.get("splits") or {}),
            "shadow": {
                "model": shadow.get("model"),
                "status": shadow.get("research_status"),
                "promotion_eligible": shadow.get("promotion_eligible"),
                "reason": shadow.get("reason"),
                "metrics": _metric_rows(
                    {
                        str(row.get("split")): row
                        for row in shadow.get("split_metrics") or []
                    }
                ),
            },
            "governance": {
                "selection_uses_test": audit.get("selection_uses_test"),
                "test_policy": audit.get("test_policy"),
                "warning": "冠军测试期超额为负；后验主动风险模型只能作为诊断候选，不能晋级。",
            },
        }
    )
    payload["visuals"] = index_visuals(
        data,
        audit,
        _metric_rows(audit.get("splits") or {}),
    )
    return payload


def _portfolio() -> dict[str, Any]:
    data = _load(DATA_ROOT / "portfolio_optimization_snapshot.json")
    selected = ((data.get("backtest") or {}).get("strategies") or {}).get(
        "selected"
    ) or {}
    optimization = data.get("optimization") or {}
    payload = _base("portfolio", "current_champion", data.get("data_as_of"))
    payload.update(
        {
            "champion": (data.get("home") or {}).get("selected_candidate") or {},
            "solver": (data.get("home") or {}).get("selected_solver") or {},
            "mechanism": {
                "nodes": [
                    "资产池与流动性",
                    "收益观点/Black-Litterman",
                    "收缩协方差与尾部风险",
                    "目标函数与约束集",
                    "CVXPY/SCIPY求解与残差复核",
                    "权重/交易/风险归因",
                ],
                "formula": "max μᵀw−λwᵀΣw−κ₂‖w−w₋₁‖²−κ₁‖w−w₋₁‖₁，预算、分组、仓位和换手均为硬约束。",
            },
            "metrics": _metric_rows(selected.get("metrics") or {}),
            "solver_benchmark": optimization.get("solver_benchmark") or [],
            "constraint_slack": optimization.get("constraint_slack") or {},
            "efficient_frontier": optimization.get("efficient_frontier") or [],
            "cost_sensitivity": (data.get("backtest") or {}).get(
                "cost_sensitivity_test"
            )
            or [],
            "return_loss_attribution": (data.get("backtest") or {}).get(
                "return_loss_attribution"
            )
            or {},
            "governance": {
                "promotion_gate": (data.get("backtest") or {}).get(
                    "promotion_gate"
                )
                or {},
                "test_policy": (data.get("method") or {}).get("test_policy"),
            },
        }
    )
    payload["visuals"] = portfolio_visuals(
        data,
        _metric_rows(selected.get("metrics") or {}),
    )
    return payload


def _kline() -> dict[str, Any]:
    audit = _load(KLINE_AUDIT) if KLINE_AUDIT.exists() else {}
    payload = _base("kline", "observe_only", audit.get("version"))
    payload.update(
        {
            "mechanism": {
                "nodes": [
                    "日K/周K与量价序列",
                    "共享/独立GRU形态编码",
                    "股票/指数多层推理",
                    "选股与择时信号",
                    "跨股票分组及市场状态验证",
                    "交易成本与多重检验门禁",
                ],
                "formula": "hₜ=GRU(xₜ,hₜ₋₁)，日K与周K分别编码后融合；标签、特征和交易执行必须按时间顺序错开。",
            },
            "descriptive": {
                "required_validation": [
                    "多股票训练/验证/测试样本",
                    "purge与embargo",
                    "牛熊及波动状态分层",
                    "个股合成与指数直接推理对照",
                    "成本、换手、DSR与PBO",
                ]
            },
            "governance": {
                "status": "observe_only",
                "reason": "当前单股任务缺少可晋级的跨股票训练和验证路径，不展示虚构的冠军夏普。",
            },
            "references": REPORT_REFERENCES["kline"],
        }
    )
    payload["visuals"] = kline_visuals(audit)
    return payload


def build(route: str) -> dict[str, Any]:
    route = str(route or "").strip()
    prefix, _, page = route.partition(":")
    if prefix == "allocation":
        return _allocation()
    if prefix == "liquidity":
        return _liquidity(page or "home")
    if prefix == "rotation":
        return _rotation()
    if prefix == "factorlab":
        return _index() if page == "strategy" else _factor()
    if prefix == "technical":
        return _kline()
    if prefix == "portfolio":
        return _portfolio()
    return _base("data", "not_applicable", None)
