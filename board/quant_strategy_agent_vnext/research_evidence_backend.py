"""Compact research-evidence payloads for the isolated vNext dashboard.

The endpoint built from this module never executes a model during an HTTP
request. It reads the same frozen, quality-gated snapshots as the existing
pages and exposes only the fields needed for descriptive, historical,
diagnostic and strategy-evidence views.
"""
from __future__ import annotations

import json
import os
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
from index_regime_visual_backend import index_regime_visuals
from liquidity_state_visual_backend import liquidity_state_visuals
from kline_multiscale_visual_backend import kline_multiscale_visuals


APP_ROOT = Path(__file__).resolve().parent
DATA_ROOT = APP_ROOT / "data"
FACTOR_RESULT = DATA_ROOT / "factor_strategy_inverse_vol_v32_20260726.json"
INDEX_SHADOW = DATA_ROOT / "index_active_risk_diagnostics.json"
INDEX_REGIME = DATA_ROOT / "index_regime_core_satellite_diagnostics.json"
KLINE_AUDIT = DATA_ROOT / "kline_cross_sectional_audit.json"
KLINE_MULTISCALE = DATA_ROOT / "kline_multiscale_expert_challenger.json"
LIQUIDITY_STATE = DATA_ROOT / "liquidity_state_challenger.json"


def _allocation_snapshot_path() -> Path:
    return Path(
        os.environ.get(
            "ASSET_ALLOCATION_SNAPSHOT",
            str(DATA_ROOT / "asset_allocation_snapshot.json"),
        )
    ).resolve()

FACTOR_MODEL_LABELS = {
    "incumbent_ols": "基准线性模型",
    "ols": "线性回归",
    "domain_ridge": "分域岭回归",
    "adaptive_icir_12m_neutral": "自适应ICIR中性组合",
    "incumbent_ols_adaptive_icir_rank_ensemble": "线性与ICIR秩集成",
    "lasso": "稀疏线性回归",
    "cs_ridge_neutral": "截面中性岭回归",
    "cs_elastic_neutral": "截面中性弹性网",
    "deep_mlp": "深层感知机",
}
FACTOR_POLICY_LABELS = {
    "full_exposure": "全量暴露",
    "robust_volatility_budget_rank_buffer": "分位缓冲与波动预算",
    "robust_fast_slow_volatility_budget": "快慢波动稳健预算",
    "continuous_rank_volatility_budget": "连续排序与波动预算",
    "continuous_rank_inverse_volatility_budget": "连续排序与逆波动配置",
    "continuous_rank_cost_aware_volatility_budget": "连续排序、成本感知与波动预算",
    "continuous_rank_adaptive_cost_aware_volatility_budget": "连续排序、自适应成本与波动预算",
    "continuous_rank_reliability_adjusted_volatility_budget": "连续排序、可靠性调仓与波动预算",
}
FACTOR_FEATURE_LABELS = {
    "ret_1": "1日收益",
    "ret_5": "5日收益",
    "ret_20": "20日动量",
    "ret_60": "60日动量",
    "vol_20": "20日波动",
    "down_vol_20": "20日下行波动",
    "price_pos_60": "60日价格分位",
    "volume_z_20": "20日成交量异常",
    "amihud_20": "20日非流动性",
    "turnover": "换手率",
    "volume_ratio": "量比",
    "value_ep": "盈利收益率",
    "value_bp": "账面市值比",
    "value_sp": "销售市值比",
    "dividend": "股息率",
    "log_mv": "对数市值",
    "moneyflow": "主力资金",
    "large_flow": "大单资金",
    "extreme_flow": "极端资金流",
    "range_1": "日内振幅",
    "gap_1": "隔夜跳空",
    "quality_roe": "净资产收益率",
    "quality_roa": "总资产收益率",
    "quality_gross_margin": "销售毛利率",
    "quality_asset_turn": "总资产周转率",
    "quality_low_leverage": "低杠杆",
    "growth_revenue": "营收增长",
    "growth_operating_profit": "营业利润增长",
    "growth_net_profit": "净利润增长",
}

_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}

REPORT_REFERENCES = {
    "asset": [
        {
            "broker": "国金证券",
            "title": "基于宏观因子风险预算的股债资产配置策略",
            "date": "2024-08-06",
            "url": "https://pdf.dfcfw.com/pdf/H301_AP202408061639154222_1.pdf",
        },
        {
            "broker": "招商银行研究院",
            "title": "大类资产配置方法体系和模型构建",
            "date": "2024-03-29",
            "url": "https://pdf.dfcfw.com/pdf/H301_AP202404031629713302_1.pdf",
        },
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


def _factor_candidate_label(candidate_id: Any) -> str:
    raw = str(candidate_id or "")
    model, separator, policy = raw.partition("::")
    model_label = FACTOR_MODEL_LABELS.get(model, model)
    if not separator:
        return model_label
    policy_label = FACTOR_POLICY_LABELS.get(policy, policy)
    return f"{model_label} · {policy_label}"


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
            {"id": "descriptive", "label": "数据描述"},
            {"id": "history", "label": "历史复盘与最新跟踪"},
            {"id": "diagnostics", "label": "模型拟合与诊断"},
            {"id": "strategy", "label": "策略回测与归因"},
        ],
    }


def _allocation_v522(data: dict[str, Any], page: str) -> dict[str, Any]:
    backtest = data.get("backtest") or {}
    strategies = backtest.get("strategies") or {}
    relative = strategies.get("benchmark_relative") or {}
    absolute = strategies.get("absolute_no_benchmark") or {}
    rows = _metric_rows(relative.get("metrics") or {})
    for row in rows:
        row["model"] = "基准高低配版"
    active = [
        {
            "split": row["split"],
            "annual_excess_return": row["annual_excess_return"],
            "information_ratio": row["information_ratio"],
        }
        for row in rows
    ]
    absolute_rows = _metric_rows(absolute.get("metrics") or {})
    for row in absolute_rows:
        row["model"] = "无基准版"

    allocations = data.get("allocations") or {}
    current = allocations.get("current_cycle") or {}
    availability = data.get("cycle_factor_availability") or {}
    availability_cycles = availability.get("cycles") or {}
    cycle_labels = {
        "pring": "普林格周期",
        "kitchin": "基钦周期",
        "juglar": "朱格拉周期",
        "merrill": "美林时钟",
        "kondratieff": "康波周期",
    }
    cycle_status = []
    current_cycles = current.get("cycles") or {}
    admitted = set(availability.get("admitted_cycles") or [])
    for cycle, label in cycle_labels.items():
        state = current_cycles.get(cycle) or {}
        audit = availability_cycles.get(cycle) or {}
        cycle_status.append(
            {
                "cycle": cycle,
                "label": label,
                "stage": state.get("state_name") or state.get("state"),
                "probabilities": state.get("probabilities") or {},
                "confidence": _finite(state.get("confidence")),
                "data_status": audit.get("data_status") or state.get("data_status"),
                "judgment_method": state.get("method")
                or (state.get("duration_model") or {}).get("method"),
                "enters_allocation": cycle in admitted
                and bool(
                    audit.get(
                        "eligible_for_views", state.get("eligible_for_views")
                    )
                ),
            }
        )

    deployment = data.get("deployment_decision") or {}
    quality = data.get("quality") or {}
    benchmark = data.get("benchmark") or {}
    payload = _base(
        "asset",
        str(deployment.get("status") or "user_approved_sharpe_mandate"),
        data.get("data_as_of"),
    )
    payload.update(
        {
            "champion": {
                "name": "基准高低配版",
                "executed_mode": "benchmark_relative",
                "weights": (allocations.get("benchmark_relative") or {}).get(
                    "weights"
                )
                or {},
                "authorization_basis": deployment.get("authorization_basis"),
            },
            "descriptive": {
                "current_cycle": current,
                "cycle_status": cycle_status,
                "factor_count": len(data.get("cycle_factor_registry") or []) + 4,
                "admitted_cycles": sorted(admitted),
            },
            "metrics": rows,
            "active_metrics": active,
            "absolute_metrics": absolute_rows,
            "robustness": {
                "cost_consistency_audit": data.get("cost_consistency_audit") or {},
                "statistical_evidence_gate": quality.get(
                    "statistical_evidence_gate"
                )
                or {},
                "statistical_evidence_by_version": quality.get(
                    "statistical_evidence_by_version"
                )
                or {},
            },
            "governance": {
                "service_authorization": {
                    "status": deployment.get("status"),
                    "deployable_dynamic_model": deployment.get(
                        "deployable_dynamic_model"
                    ),
                    "executed_mode": deployment.get("executed_mode"),
                    "authorization_basis": deployment.get("authorization_basis"),
                },
                "promotion_gate": quality.get("promotion_gate") or {},
                "statistical_evidence_gate": quality.get(
                    "statistical_evidence_gate"
                )
                or {},
                "statistical_evidence_by_version": quality.get(
                    "statistical_evidence_by_version"
                )
                or {},
                "test_policy": (data.get("methodology") or {}).get("test_policy")
                or "retrospective_report_only",
                "policy_benchmark": {
                    "id": benchmark.get("id"),
                    "weights": benchmark.get("weights") or {},
                    "role": "optimizer_and_active_return_anchor",
                },
                "display_benchmark": {
                    "id": "equal_weight_25",
                    "weights": {
                        "equity": 0.25,
                        "bond": 0.25,
                        "gold": 0.25,
                        "commodity": 0.25,
                    },
                    "role": "nav_display_only_not_optimizer_input",
                },
            },
            "cycle_status": cycle_status,
            "model_chain": [
                "宏观因子风险模型",
                "严格风险平价（ERC）",
                "约束风险预算",
                "稳健Black–Litterman",
            ],
            "architecture_comparison": [
                {
                    "model": "基准高低配版",
                    "metrics": relative.get("metrics") or {},
                    "role": "authorized_service",
                },
                {
                    "model": "无基准版",
                    "metrics": absolute.get("metrics") or {},
                    "role": "benchmark_free_research_comparator",
                },
            ],
            "macro_factor_risk_audit": data.get("macro_factor_risk_audit") or {},
            "references": REPORT_REFERENCES["asset"],
        }
    )
    payload["visuals"] = allocation_visuals(data, rows, page=page)
    return payload


def _allocation_v56_metric_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    asset_order = [str(item) for item in (data.get("asset_order") or [])]
    for key, model in ((data.get("allocation_models") or {}).items()):
        full = (model.get("metrics") or {}).get("full") or {}
        validation = (model.get("metrics") or {}).get("validation") or {}
        current = model.get("current_weights") or {}
        row = {
            "model": model.get("name") or key,
            "role": model.get("role") or "",
            "annual_return": _finite(full.get("annual_return")),
            "sharpe": _finite(full.get("sharpe")),
            "annual_excess_return": _finite(full.get("annual_excess_return")),
            "information_ratio": _finite(full.get("information_ratio")),
            "validation_sharpe": _finite(validation.get("sharpe")),
        }
        for asset in asset_order:
            row[asset] = _finite(current.get(asset))
        rows.append(row)
    return rows


def _allocation(page: str = "strategy") -> dict[str, Any]:
    data = _load(_allocation_snapshot_path())
    if str(data.get("schema_version") or "") == "6.3.0":
        from asset_allocation_visual_v63 import build as build_v63_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "资产配置 v6.3：真实因子链路 × 美林/普林格 × BL/风险平价/宏观因子",
            "summary": (data.get("cycle_tracking") or {}).get("current_summary")
            or "四资产资产配置：真实D2因子筛选进入美林、普林格、BL和宏观因子调控；D3/PIT生产门禁保持fail-closed。",
            "tables": [],
            "metrics": _allocation_v56_metric_rows(data),
            "governance": data.get("governance") or {},
            "references": data.get("references") or [],
            "visuals": build_v63_visuals(data, metrics=_allocation_v56_metric_rows(data), page=page),
            "source": "asset_allocation_snapshot.json",
        }
    if str(data.get("schema_version") or "") == "6.2.0":
        from asset_allocation_visual_v62 import build as build_v62_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "\u8d44\u4ea7\u914d\u7f6e v6.2\uff1a\u7f8e\u6797/\u666e\u6797\u683c \u00d7 BL/\u98ce\u9669\u5e73\u4ef7/\u5b8f\u89c2\u56e0\u5b50 \u00d7 D3/PIT\u95e8\u7981",
            "summary": (data.get("cycle_tracking") or {}).get("current_summary")
            or "\u56db\u8d44\u4ea7\u8d44\u4ea7\u914d\u7f6e\uff1a\u4e24\u5468\u671f\u4e09\u6a21\u578b\u4fdd\u6301v6.1\u7ed3\u8bba\uff0c\u65b0\u589eWind/iFinD/RQ D3/PIT\u5b8f\u89c2\u56e0\u5b50\u6ce8\u518c\u4e0e\u771f\u5b9e\u6027\u95e8\u7981\u3002",
            "tables": [],
            "metrics": _allocation_v56_metric_rows(data),
            "visuals": build_v62_visuals(data, metrics=_allocation_v56_metric_rows(data), page=page),
            "source": "asset_allocation_snapshot.json",
        }
    if str(data.get("schema_version") or "") == "6.1.0":
        from asset_allocation_visual_v61 import build as build_v61_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "资产配置 v6.1：美林/普林格 × BL/风险平价/宏观因子",
            "summary": (data.get("cycle_tracking") or {}).get("current_summary")
            or "四资产资产配置：美林增长通胀、普林格货币信用增长、周期观点BL、风险平价与宏观因子调整。",
            "tables": [],
            "metrics": _allocation_v56_metric_rows(data),
            "visuals": build_v61_visuals(data, metrics=_allocation_v56_metric_rows(data), page=page),
            "source": "asset_allocation_snapshot.json",
        }
    if str(data.get("schema_version") or "") == "6.0.0":
        from asset_allocation_visual_v60 import build as build_v60_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "Asset allocation v6.0: Merrill/Pring x BL/RP/Macro",
            "summary": (
                (data.get("cycle_tracking") or {}).get("current_summary")
                or "Asset allocation v6.0 keeps only Merrill clock, Pring cycle and three allocation models."
            ),
            "as_of": (data.get("data_as_of") or {}).get("market_month") or data.get("generated_at"),
            "layers": ["descriptive", "history", "diagnostics", "strategy"],
            "metrics": _allocation_v56_metric_rows(data),
            "governance": data.get("governance") or {},
            "references": data.get("references") or [],
            "visuals": build_v60_visuals(data, page=page),
        }
    if str(data.get("schema_version") or "") == "5.9.0":
        from asset_allocation_visual_v59 import build as build_v59_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "???????????????v5.9?",
            "summary": (
                (data.get("cycle_tracking") or {}).get("current_summary")
                or "????v5.9????????????????+BL+????+???+?????"
            ),
            "as_of": (data.get("data_as_of") or {}).get("market_month") or data.get("generated_at"),
            "layers": ["descriptive", "history", "diagnostics", "strategy"],
            "visuals": build_v59_visuals(data, page=page),
        }
    if str(data.get("schema_version") or "") == "5.8.0":
        from asset_allocation_visual_v58 import build as build_v58_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "\u8d44\u4ea7\u914d\u7f6e\uff08\u4e09\u8d44\u4ea7\u7b49\u6743\u951a\u00b7\u65e0\u9ec4\u91d1\uff09",
            "summary": (
                (data.get("cycle_tracking") or {}).get("current_summary")
                or "\u8d44\u4ea7\u914d\u7f6ev5.8\u4e09\u8d44\u4ea7\u7b49\u6743\u951a\u65e0\u9ec4\u91d1\u7814\u7a76\u5feb\u7167\u3002"
            ),
            "metrics": _allocation_v56_metric_rows(data),
            "governance": data.get("governance") or {},
            "references": data.get("references") or [],
            "visuals": build_v58_visuals(data, page=page),
        }
    if str(data.get("schema_version") or "") == "5.7.0":
        from asset_allocation_visual_v57 import build as build_v57_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "\u8d44\u4ea7\u914d\u7f6e\uff08\u4e09\u8d44\u4ea7\u65e0\u9ec4\u91d1\uff09",
            "summary": (
                (data.get("cycle_tracking") or {}).get("current_summary")
                or "\u8d44\u4ea7\u914d\u7f6ev5.7\u4e09\u8d44\u4ea7\u65e0\u9ec4\u91d1\u7814\u7a76\u5feb\u7167\u3002"
            ),
            "metrics": _allocation_v56_metric_rows(data),
            "governance": data.get("governance") or {},
            "references": data.get("references") or [],
            "visuals": build_v57_visuals(data, page=page),
        }
    if str(data.get("schema_version") or "") == "5.6.0":
        from asset_allocation_visual_v56 import build as build_v56_visuals

        return {
            "schema_version": "research-evidence/1.0",
            "route": f"allocation:{page}",
            "title": "?????????-?????",
            "summary": (
                (data.get("cycle_tracking") or {}).get("current_summary")
                or "????v5.6??????????????????????"
            ),
            "metrics": _allocation_v56_metric_rows(data),
            "governance": data.get("governance") or {},
            "references": data.get("references") or [],
            "visuals": build_v56_visuals(data, page=page),
        }
    if str(data.get("schema_version") or "") == "5.2.2":
        return _allocation_v522(data, page)
    audit = (data.get("backtest") or {}).get("selection_audit") or {}
    current = (data.get("allocations") or {}).get("current_cycle") or {}
    rows = _metric_rows(
        {
            "train": audit.get("train_metrics") or {},
            "validation": audit.get("validation_metrics") or {},
            "test": audit.get("test_metrics_report_only") or {},
        }
    )
    for row in rows:
        row["model"] = "战略偏好"
    active = _metric_rows(
        {
            "train": audit.get("train_active_metrics") or {},
            "validation": audit.get("validation_active_metrics") or {},
            "test": audit.get("test_active_metrics_report_only") or {},
        }
    )
    active_by_split = {row["split"]: row for row in active}
    selected_strategy = ((data.get("backtest") or {}).get("strategies") or {}).get("recommended") or {}
    cash_by_split = selected_strategy.get("cash_hurdle_metrics_by_split") or {}
    for row in rows:
        active_row = active_by_split.get(row["split"]) or {}
        row["annual_excess_return"] = _finite(
            active_row.get("annual_excess_return")
        )
        row["information_ratio"] = _finite(
            active_row.get("information_ratio")
        )
        row["cash_excess_sharpe"] = _finite(
            (cash_by_split.get(row["split"]) or {}).get("cash_excess_sharpe")
        )
    objective_champions = (data.get("backtest") or {}).get("objective_champions") or {}
    stable_key = str((objective_champions.get("stable_absolute") or {}).get("strategy") or "")
    stable_strategy = ((data.get("backtest") or {}).get("strategies") or {}).get(stable_key) or {}
    stable_rows = _metric_rows(stable_strategy.get("metrics_by_split") or {})
    stable_active = {
        row["split"]: row
        for row in _metric_rows(stable_strategy.get("active_metrics_by_split") or {})
    }
    stable_cash = stable_strategy.get("cash_hurdle_metrics_by_split") or {}
    for row in stable_rows:
        row["model"] = "稳健绝对"
        active_row = stable_active.get(row["split"]) or {}
        row["annual_excess_return"] = _finite(active_row.get("annual_excess_return"))
        row["information_ratio"] = _finite(active_row.get("information_ratio"))
        row["cash_excess_sharpe"] = _finite(
            (stable_cash.get(row["split"]) or {}).get("cash_excess_sharpe")
        )
    if stable_key and stable_key != "recommended":
        rows.extend(stable_rows)

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
    selected_spec = dict(audit.get("selected_spec") or {})
    selected_spec.pop("id", None)
    payload = _base("asset", status, data.get("data_as_of"))
    payload.update(
        {
            "champion": selected_spec,
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
                "architecture_policy": (data.get("backtest") or {}).get("architecture_policy") or {},
                "objective_champions": objective_champions,
            },
            "architecture_comparison": (data.get("backtest") or {}).get("architecture_comparison") or [],
            "macro_factor_risk_audit": (data.get("allocations") or {}).get("macro_factor_risk_audit") or {},
            "references": REPORT_REFERENCES["asset"],
        }
    )
    payload["visuals"] = allocation_visuals(data, rows, page=page)
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


def _liquidity_state(page: str) -> dict[str, Any]:
    data = _load(DATA_ROOT / "liquidity_snapshot.json")
    model = _load(LIQUIDITY_STATE)
    page_data = (data.get("pages") or {}).get(page) or {}
    charts = page_data.get("charts") or []
    sources = data.get("source_registry") or {}
    selected = model.get("selected") or {}
    evaluation = selected.get("evaluation") or {}
    config = evaluation.get("config") or {}
    split_rows = evaluation.get("split_metrics") or {}
    selected_label = str(evaluation.get("label") or "\u8d44\u91d1\u72b6\u6001\u98ce\u9669\u9884\u7b97")
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
    payload = _base(
        "liquidity",
        "\u7814\u7a76\u8bca\u65ad",
        model.get("data_as_of") or page_data.get("as_of"),
    )
    payload.update(
        {
            "mechanism": {
                "nodes": [
                    "\u7cbe\u786e\u8d44\u91d1\u5e8f\u5217\u4e0e\u53d1\u5e03\u65e5",
                    "\u6d41\u91cf\u3001\u5b58\u91cf\u4e0e\u4f9b\u7ed9\u5206\u7c7b",
                    "\u53d1\u5e03\u6ede\u540e\u4e0e\u77ed\u4e2d\u671f\u7a33\u5065\u521b\u65b0",
                    "\u8d44\u91d1\u7c7b\u5185\u540e\u9a8c\u6536\u7f29",
                    "\u8d44\u91d1\u7c7b\u95f4\u6eda\u52a8\u540e\u9a8c",
                    "\u6ce2\u52a8\u98ce\u9669\u9884\u7b97\u4e0e\u4e0b\u5468\u6267\u884c",
                ],
                "formula": "z=(x-\u6eda\u52a8\u4e2d\u4f4d\u6570)/(1.4826\u00d7MAD)\uff1bw_t\u7531\u5df2\u6210\u719f\u672a\u6765\u6536\u76ca\u6807\u7b7e\u7684\u5206\u6bb5\u79e9\u76f8\u5173\u540e\u9a8c\u5f62\u6210\uff1b\u6d4b\u8bd5\u671f\u53ea\u62a5\u544a\u3002",
            },
            "champion": {
                "model": selected_label,
                "target_horizon_weeks": config.get("target_horizon_weeks"),
                "lookback_weeks": config.get("lookback_weeks"),
                "refit_weeks": config.get("refit_weeks"),
                "target_volatility": config.get("target_volatility"),
                "cost_bps": config.get("cost_bps"),
            },
            "descriptive": {
                "chart_count": len(charts),
                "source_count": len(source_ids),
                "quality_counts": dict(quality_counts),
                "exact_model_input_count": (model.get("data_audit") or {}).get("exact_model_input_count"),
                "effective_model_input_count": len(
                    {
                        row.get("series_id")
                        for row in (model.get("data_audit") or {}).get("feature_registry") or []
                        if int(row.get("observations") or 0) > 0
                    }
                ),
                "excluded_contract_count": (model.get("data_audit") or {}).get("contract_excluded_count"),
                "candidate_count": len(model.get("candidate_evaluations") or []),
            },
            "metrics": [
                {"split": split, **row}
                for split, row in split_rows.items()
                if isinstance(row, dict)
            ],
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
                "status": "research_diagnostic",
                "production_snapshot_status": (data.get("quality") or {}).get("status"),
                "exact_series_only": True,
                "database_read_only": True,
                "selection_uses": ["train", "valid"],
                "selection_excludes": ["test"],
                "selection_uses_test": False,
                "promotion_eligible": False,
                "test_policy": "\u5c01\u5b58\u6d4b\u8bd5\u53ea\u62a5\u544a\uff0c\u4e0d\u53c2\u4e0e\u5019\u9009\u6392\u5e8f",
                "validation_sharpe": ((split_rows.get("valid") or {}).get("sharpe")),
                "test_sharpe_report_only": ((split_rows.get("test") or {}).get("sharpe")),
                "validation_information_ratio": ((split_rows.get("valid") or {}).get("information_ratio")),
                "missing_contract_series": (model.get("data_audit") or {}).get("contract_excluded"),
            },
            "references": model.get("research_basis") or [],
            "root_cause_audit": model.get("root_cause_audit") or [],
        }
    )
    payload["visuals"] = liquidity_state_visuals(data, page, model)
    return payload


def _rotation(page: str = "industry") -> dict[str, Any]:
    data = _load(DATA_ROOT / "rotation_snapshot.json")
    frequencies = (data.get("industry") or {}).get("frequencies") or {}
    style = (
        ((data.get("style") or {}).get("frequencies") or {}).get("quarterly")
        or {}
    )
    series = []
    model_labels = {"monthly": "月频行业轮动", "weekly": "周频行业轮动"}
    for name in ("monthly", "weekly"):
        model = frequencies.get(name) or {}
        promotion = model.get("promotion_gate") or {}
        series.append(
            {
                "model": model_labels[name],
                "candidate": model.get("selected_candidate_label") or model.get("selected_candidate"),
                "research_candidate": model.get("research_selected_candidate_label") or model.get("research_selected_candidate"),
                "gate": promotion.get("status") or (model.get("gate") or {}).get("status"),
                "metrics": _metric_rows(model.get("metrics") or {}),
            }
        )
    series.append(
        {
            "model": "季度风格轮动",
            "candidate": style.get("selected_candidate"),
            "research_candidate": style.get("selected_candidate"),
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
                "warning": (
                    "景气加速度确认候选仅作诊断。其训练与验证证据单独展示，"
                    "生产冠军不因已观察报告期的改善而替换。"
                ),
                "monthly_promotion_gate": (frequencies.get("monthly") or {}).get("promotion_gate") or {},
                "weekly_promotion_gate": (frequencies.get("weekly") or {}).get("promotion_gate") or {},
            },
        }
    )
    payload["visuals"] = rotation_visuals(data, series, page=page)
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
            "candidate": _factor_candidate_label(candidate_id),
            "candidate_id": candidate_id,
            "model": FACTOR_MODEL_LABELS.get(
                candidate.get("model"), candidate.get("model")
            ),
            "policy": FACTOR_POLICY_LABELS.get(
                candidate.get("execution_policy"),
                candidate.get("execution_policy"),
            ),
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
    selected_model = selection.get("selected_model")
    selected_policy = selection.get("selected_execution_policy")
    selected_candidate = f"{selected_model}::{selected_policy}"
    payload = _base("factor", "current_champion_with_shadow", data.get("created_at"))
    payload.update(
        {
            "champion": {
                "model": FACTOR_MODEL_LABELS.get(selected_model, selected_model),
                "policy": FACTOR_POLICY_LABELS.get(selected_policy, selected_policy),
                "candidate": _factor_candidate_label(selected_candidate),
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
    visual_data = dict(data)
    visual_data["features"] = [
        FACTOR_FEATURE_LABELS.get(feature, feature)
        for feature in (data.get("features") or [])
    ]
    payload["visuals"] = factor_visuals(
        visual_data, metrics, compact_candidates
    )
    return payload


def _legacy_index() -> dict[str, Any]:
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


def _index() -> dict[str, Any]:
    data = _load(DATA_ROOT / "index_enhancement_snapshot.json")
    audit = (data.get("champion_audit") or {}).get("CSI800_ENH") or {}
    regime = _load(INDEX_REGIME) if INDEX_REGIME.exists() else {}
    selected = regime.get("selected") or {}
    summary = selected.get("summary") or {}
    split_metrics = summary.get("split_metrics") or {}
    selected_model = regime.get("selected_candidate") or audit.get("champion")
    selected_label = summary.get("mandate") or selected_model
    payload = _base(
        "index",
        "diagnostic_only" if regime else "conditional_champion",
        regime.get("data_as_of") or data.get("data_as_of"),
    )
    payload.update(
        {
            "champion": {
                "name": selected_label,
                "family": "\u4e2d\u8bc1800\u8d1d\u53f6\u65af\u6838\u5fc3\u536b\u661f",
                "status": "\u540e\u9a8c\u8bca\u65ad\u5019\u9009" if regime else audit.get("status"),
                "candidate_count": len(regime.get("candidate_evaluations") or [])
                if regime
                else audit.get("candidate_count"),
            },
            "mechanism": {
                "nodes": [
                    "\u4fe1\u53f7\u65e5\u6307\u6570\u6743\u91cd\u6838\u5fc3",
                    "\u5df2\u6210\u719f\u6536\u76ca\u8ba1\u7b97\u591a\u671f\u9650RankIC",
                    "\u7ecf\u9a8c\u8d1d\u53f6\u65af\u56e0\u5b50\u540e\u9a8c",
                    "\u5feb\u6162\u56e0\u5b50\u72b6\u6001\u9884\u7b97",
                    "\u98ce\u683c\u6b8b\u5dee\u4e0e\u884c\u4e1a\u8f6f\u7ea6\u675f",
                    "\u8ddf\u8e2a\u8bef\u5dee\u548c\u6362\u624b\u8054\u5408\u63a7\u5236",
                ],
                "formula": "\u76ee\u6807 = \u4e3b\u52a8\u6536\u76ca - \u98ce\u9669\u60e9\u7f5a - \u6362\u624b\u6210\u672c\uff1b\u7ec4\u5408\u6743\u91cd = \u57fa\u51c6\u6743\u91cd + \u6709\u9650\u4e3b\u52a8\u504f\u79bb\u3002",
            },
            "metrics": _metric_rows(split_metrics)
            if split_metrics
            else _metric_rows(audit.get("splits") or {}),
            "diagnostic_candidate": {
                "model": selected_label,
                "model_id": selected_model,
                "mandate": summary.get("mandate"),
                "promotion_eligible": False,
                "selection_uses_test": False,
                "average_tracking_error": summary.get("average_tracking_error"),
                "average_one_way_turnover": summary.get("average_one_way_turnover"),
                "average_active_share": summary.get("average_active_share"),
                "average_alpha_confidence": summary.get("average_alpha_confidence"),
            },
            "governance": {
                "selection_uses_test": False,
                "test_policy": "\u5c01\u5b58\u6d4b\u8bd5\u53ea\u62a5\u544a\uff0c\u4e0d\u53c2\u4e0e\u5019\u9009\u6392\u5e8f\u6216\u53c2\u6570\u9009\u62e9",
                "promotion_eligible": False,
                "warning": "\u6d4b\u8bd5\u671fIR\u4ecd\u672a\u7a33\u5b9a\u4e3a\u6b63\uff0c\u5f53\u524d\u7ed3\u679c\u53ea\u7528\u4e8e\u8bca\u65ad\u548c\u4e0b\u4e00\u8f6e\u524d\u77bb\u9a8c\u8bc1\u3002",
            },
            "references": regime.get("research_basis") or [],
        }
    )
    payload["visuals"] = (
        index_regime_visuals(regime)
        if regime
        else index_visuals(data, audit, _metric_rows(audit.get("splits") or {}))
    )
    return payload

def _portfolio() -> dict[str, Any]:
    data = _load(DATA_ROOT / "portfolio_optimization_snapshot.json")
    selected = ((data.get("backtest") or {}).get("strategies") or {}).get(
        "selected"
    ) or {}
    optimization = data.get("optimization") or {}
    promotion = (data.get("backtest") or {}).get("promotion_gate") or {}
    payload = _base(
        "portfolio",
        promotion.get("status") or "post_test_diagnostic_candidate",
        data.get("data_as_of"),
    )
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
    model = _load(KLINE_MULTISCALE) if KLINE_MULTISCALE.exists() else {}
    if model:
        selected = model.get("selected") or {}
        deployment = model.get("deployment_selected") or {}
        result = ((model.get("results") or {}).get(selected.get("universe")) or {})
        candidate = ((result.get("candidates") or {}).get(selected.get("candidate")) or {})
        latest = result.get("latest") or []
        pure = model.get("pure_technical_model") or {}
        pure_selected = pure.get("selected") or {}
        pure_guard = pure.get("release_guard") or {}
        payload = _base("kline", "研究诊断", str(model.get("created_at") or "").split("T")[0])
        payload.update(
            {
                "champion": {
                    "model": deployment.get("candidate") or "暂无可部署策略",
                    "policy": "训练与验证双段闸门",
                },
                "mechanism": {
                    "nodes": [
                        "点时点日K与周K",
                        "五类形态专家",
                        "行业与市值中性",
                        "扩展窗口截面排序",
                        "成本后分位组合",
                        "封存测试发布闸门",
                    ],
                    "formula": "sᵢₜ=Rank[f(日线趋势,放量突破,趋势回撤,缩量蓄势,周线形态,市场状态)]；t日收盘形成信号，下一交易日开盘执行。",
                },
                "descriptive": {
                    "candidate_count": len(result.get("candidates") or {}),
                    "factor_count": len(result.get("expert_names") or []),
                },
                "governance": {
                    "status": "observe_only",
                    "reason": "研究多空诊断通过训练与验证，但无可执行候选通过双段闸门。",
                    "selection_uses_test": False,
                    "accepted_by_train_validation": bool(selected.get("accepted_by_train_validation")),
                    "release_approved": bool(deployment.get("release_approved")),
                    "deployment_candidate": deployment.get("candidate"),
                    "research_candidate": selected.get("candidate"),
                    "execution_mode": None,
                    "pure_technical_status": pure.get("status"),
                    "pure_technical_candidate": pure_selected.get("candidate"),
                    "pure_technical_universe": pure_selected.get("universe"),
                    "pure_technical_release_approved": bool(pure_guard.get("release_approved")),
                    "pure_technical_selection_uses_test": False,
                    "dual_model_note": "model_1_pure_technical_signal_stack; model_2_llm_memory_multiscale; sealed_test_report_only",
                },
                "references": model.get("research_basis") or REPORT_REFERENCES["kline"],
            }
        )
        payload["visuals"] = kline_multiscale_visuals(model)
        return payload
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


def _without_mechanism(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("mechanism", None)
    return payload


def build(route: str) -> dict[str, Any]:
    route = str(route or "").strip()
    prefix, _, page = route.partition(":")
    if prefix == "allocation":
        payload = _allocation(page or "home")
    if prefix == "liquidity":
        payload = _liquidity_state(page or "home")
    if prefix == "rotation":
        payload = _rotation(page or "industry")
    if prefix == "factorlab":
        payload = _index() if page == "strategy" else _factor()
    if prefix == "technical":
        payload = _kline()
    if prefix == "portfolio":
        payload = _portfolio()
    if prefix not in {"allocation", "liquidity", "rotation", "factorlab", "technical", "portfolio"}:
        payload = _base("data", "not_applicable", None)
    return _without_mechanism(payload)
