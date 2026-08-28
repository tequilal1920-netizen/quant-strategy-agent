from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = Path(__file__).with_name("catalog.json")


class QueryError(RuntimeError):
    """可向调用 Agent 直接展示的输入或数据错误。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QueryError(f"缺少数据文件：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise QueryError(f"数据文件不可读：{path}；{exc}") from exc
    if not isinstance(payload, dict):
        raise QueryError(f"数据文件顶层必须是对象：{path}")
    return payload


def catalog() -> dict[str, Any]:
    return _read_json(CATALOG_PATH)


def _snapshot_root() -> Path:
    configured = os.environ.get("QUANT_AGENT_SNAPSHOT_ROOT", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_dir():
            raise QueryError(f"QUANT_AGENT_SNAPSHOT_ROOT 不存在：{path}")
        return path
    candidates = [
        ROOT / "board" / "quant_strategy_agent_vnext" / "data",
        ROOT / "board" / "quant_strategy_agent" / "data",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    raise QueryError("没有找到模型快照目录，请设置 QUANT_AGENT_SNAPSHOT_ROOT")


def _snapshot(name: str) -> tuple[dict[str, Any], Path]:
    path = _snapshot_root() / name
    return _read_json(path), path


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compact_metrics(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    keep = (
        "status",
        "start",
        "end",
        "observations",
        "annual_return",
        "benchmark_annual_return",
        "annual_excess",
        "annual_excess_return",
        "sharpe",
        "excess_sharpe",
        "information_ratio",
        "max_drawdown",
        "annual_turnover",
        "turnover",
        "rank_ic",
        "icir",
        "hit_rate",
    )
    return {key: value.get(key) for key in keep if key in value}


def _response(
    skill: str,
    operation: str,
    payload: dict[str, Any],
    source: Path | str,
    result: Any,
) -> dict[str, Any]:
    return {
        "状态": "正常",
        "模型": skill,
        "动作": operation,
        "数据截止": payload.get("as_of")
        or payload.get("data_as_of")
        or payload.get("verified_at"),
        "生成时间": payload.get("generated_at")
        or payload.get("verified_at"),
        "数据来源": str(source),
        "结果": result,
    }


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in params and params[name] not in (None, ""):
            return params[name]
    return default


def _limit(params: dict[str, Any], default: int = 10, maximum: int = 50) -> int:
    raw = _param(params, "limit", "数量", default=default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise QueryError("数量必须是整数") from exc
    return max(1, min(value, maximum))

ASSET_V64_STAGE_CN = {
    "recovery": "复苏期",
    "overheat": "过热期",
    "stagflation": "滞胀期",
    "recession": "衰退期",
    "I_credit_repair": "阶段I 复苏期",
    "II_profit_expansion": "阶段II 繁荣期",
    "III_prosperity": "阶段III 过热期",
    "IV_credit_pressure": "阶段IV 滞涨期",
    "V_profit_downturn": "阶段V 衰退前期",
    "V_stagflation_profit_downturn": "阶段V 衰退前期",
    "VI_recession_repair": "阶段VI 衰退后期",
}


def _asset_v64_stage(value: Any) -> Any:
    return ASSET_V64_STAGE_CN.get(str(value), value)


def _asset_v64_labels(payload: dict[str, Any]) -> dict[str, str]:
    labels = payload.get("asset_labels") or {}
    fallback = {"equity": "股票", "bond": "债券", "gold": "黄金", "commodity": "商品"}
    return {key: str(labels.get(key) or fallback.get(key) or key) for key in _asset_v64_order(payload)}


def _asset_v64_order(payload: dict[str, Any]) -> list[str]:
    order = [str(item) for item in (payload.get("asset_order") or [])]
    return order if order else ["equity", "bond", "gold", "commodity"]


def _asset_v64_weights(payload: dict[str, Any], weights: Any) -> dict[str, Any]:
    labels = _asset_v64_labels(payload)
    order = _asset_v64_order(payload)
    if isinstance(weights, dict):
        return {labels[asset]: weights.get(asset) for asset in order}
    if isinstance(weights, list):
        return {labels[asset]: weights[index] if index < len(weights) else None for index, asset in enumerate(order)}
    return {}


def _asset_v64_model_key(params: dict[str, Any], payload: dict[str, Any]) -> str:
    rec = payload.get("recommended") or {}
    default = str(rec.get("primary_model") or "macro_factor")
    raw = str(_param(params, "model", "模型", "strategy", "策略", default=default)).strip()
    aliases = {
        "": default,
        "primary": default,
        "recommended": default,
        "主推": default,
        "推荐": default,
        "best": default,
        "收益冠军": str(rec.get("excess_champion_vs_equal_full_report_only") or default),
        "excess": str(rec.get("excess_champion_vs_equal_full_report_only") or default),
        "夏普冠军": str(rec.get("sharpe_champion_full_report_only") or default),
        "sharpe": str(rec.get("sharpe_champion_full_report_only") or default),
        "bl": "black_litterman",
        "black_litterman": "black_litterman",
        "black-litterman": "black_litterman",
        "周期观点bl": "black_litterman",
        "风险平价": "risk_parity",
        "风险预算": "risk_parity",
        "risk_parity": "risk_parity",
        "risk-parity": "risk_parity",
        "宏观因子": "macro_factor",
        "宏观": "macro_factor",
        "macro": "macro_factor",
        "macro_factor": "macro_factor",
        "macro-factor": "macro_factor",
    }
    key = aliases.get(raw, aliases.get(raw.lower(), raw))
    models = payload.get("allocation_models") or {}
    if key not in models:
        choices = "、".join(models.keys())
        raise QueryError(f"未知资产配置模型：{raw}；可选：主推、收益冠军、夏普冠军、black_litterman、risk_parity、macro_factor；当前模型集：{choices}")
    return key


def _asset_v64_cycle(payload: dict[str, Any]) -> dict[str, Any]:
    cycle = payload.get("cycle_tracking") or {}
    history = [row for row in (cycle.get("history") or []) if isinstance(row, dict)]
    latest = history[-1] if history else {}
    cycles = []
    for row in cycle.get("cycles") or []:
        if not isinstance(row, dict):
            continue
        cycles.append({
            "周期": row.get("cycle"),
            "维度": row.get("dimensions"),
            "当前阶段": _asset_v64_stage(row.get("current_stage")),
            "置信度": row.get("display_probability"),
            "研究准入": row.get("research_admitted"),
            "生产准入": row.get("production_admitted"),
        })
    return {
        "当前月份": latest.get("month"),
        "美林阶段": _asset_v64_stage(latest.get("merrill_stage")),
        "美林增长": latest.get("merrill_growth"),
        "美林通胀": latest.get("merrill_inflation"),
        "普林格阶段": _asset_v64_stage(latest.get("pring_stage")),
        "普林格货币": latest.get("pring_money"),
        "普林格信用": latest.get("pring_credit"),
        "普林格增长": latest.get("pring_growth"),
        "综合排序": (payload.get("recommended") or {}).get("current_cycle_rank") or cycle.get("combined_asset_ranking"),
        "周期模型": cycles,
        "历史样本数": len(history),
        "说明": cycle.get("current_summary"),
    }


def _asset_v64_metric_splits(model: dict[str, Any]) -> dict[str, Any]:
    metrics = model.get("metrics") or {}
    return {
        "训练": _compact_metrics(metrics.get("train")),
        "验证": _compact_metrics(metrics.get("validation")),
        "报告期仅展示": _compact_metrics(metrics.get("test_report_only")),
        "全区间": _compact_metrics(metrics.get("full")),
    }


def _asset_query_v64(operation: str, params: dict[str, Any], payload: dict[str, Any], path: Path) -> dict[str, Any]:
    models = payload.get("allocation_models") or {}
    rec = payload.get("recommended") or {}
    key = _asset_v64_model_key(params, payload) if operation in {"current", "backtest"} else str(rec.get("primary_model") or "macro_factor")
    model = models.get(key) or {}
    gate = rec.get("publication_gate") or {}
    if operation == "cycle":
        cycle = payload.get("cycle_tracking") or {}
        result = {
            "周期": _asset_v64_cycle(payload),
            "候选因子数": cycle.get("candidate_factor_count"),
            "入选研究因子数": cycle.get("selected_factor_count"),
            "研究准入周期": cycle.get("research_admitted_cycles"),
            "生产准入周期": cycle.get("production_admitted_cycles"),
            "真实性边界": cycle.get("truth_boundary"),
        }
    elif operation == "backtest":
        result = {
            "主推模型": rec.get("primary_model"),
            "收益冠军": rec.get("excess_champion_vs_equal_full_report_only"),
            "夏普冠军": rec.get("sharpe_champion_full_report_only"),
            "选择规则": rec.get("selection_rule"),
            "展示模型": key,
            "展示模型绩效": _asset_v64_metric_splits(model),
            "发布门禁": gate,
            "近年相对诊断": rec.get("recent_relative_diagnostics"),
            "近年弱项结论": rec.get("recent_weakness_diagnosis"),
        }
    elif operation == "current":
        result = {
            "快照版本": payload.get("schema_version"),
            "引擎版本": payload.get("engine_version"),
            "兼容说明": "v6.4不再使用稳健/平衡/权益优先画像；profile/画像参数仅保留兼容，默认返回主推模型。",
            "主推模型": rec.get("primary_model"),
            "收益冠军": rec.get("excess_champion_vs_equal_full_report_only"),
            "夏普冠军": rec.get("sharpe_champion_full_report_only"),
            "展示模型": key,
            "模型名称": model.get("name"),
            "模型说明": model.get("role"),
            "当前权重": _asset_v64_weights(payload, model.get("current_weights")),
            "当前周期": _asset_v64_cycle(payload),
            "绩效": _asset_v64_metric_splits(model),
            "发布门禁": gate.get(key),
            "选择规则": rec.get("selection_rule"),
            "治理": {
                "状态": (payload.get("governance") or {}).get("status"),
                "测试是否参与选择": (payload.get("governance") or {}).get("selection_uses_test"),
                "部署允许": (payload.get("governance") or {}).get("deployment_allowed"),
                "真实性边界": (payload.get("governance") or {}).get("truth_boundary"),
            },
        }
    else:
        raise QueryError("资产配置动作仅支持 current、cycle、backtest")
    return _response("asset-allocation", operation, payload, path, result)

def _asset_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("asset_allocation_snapshot.json")
    if str(payload.get("schema_version") or "").strip().startswith("6.") or payload.get("allocation_models"):
        return _asset_query_v64(operation, params, payload, path)
    allocations = payload.get("allocations") or {}
    current = allocations.get("current_cycle") or {}
    aliases = {
        "稳健": "conservative",
        "平衡": "balanced",
        "权益优先": "equity_preferred",
        "conservative": "conservative",
        "balanced": "balanced",
        "equity_preferred": "equity_preferred",
    }
    profile_raw = str(
        _param(
            params,
            "profile",
            "画像",
            default=allocations.get("default_profile") or "balanced",
        )
    )
    profile = aliases.get(profile_raw, profile_raw)
    profiles = allocations.get("profiles") or {}
    allocation = profiles.get(profile)
    if allocation is None and profile == allocations.get("default_profile"):
        allocation = allocations.get("recommended")
    if allocation is None:
        raise QueryError(f"未知资产画像：{profile_raw}；可选：稳健、平衡、权益优先")

    cycle = {
        "月份": current.get("month"),
        "普林格": current.get("pring_phase_name"),
        "普林格置信度": current.get("confidence"),
        "基钦": current.get("kitchin_state"),
        "朱格拉": current.get("juglar_state"),
        "康波": current.get("kondratieff_state"),
        "美林": current.get("merrill_state"),
        "增长得分": current.get("growth_score"),
        "通胀得分": current.get("inflation_score"),
        "流动性得分": current.get("liquidity_score"),
        "信用得分": current.get("credit_score"),
    }
    optimization = payload.get("optimization") or {}
    governance = {
        "入选方案": optimization.get("selected_spec"),
        "训练": _compact_metrics(optimization.get("train_metrics")),
        "验证": _compact_metrics(optimization.get("validation_metrics")),
        "测试仅报告": _compact_metrics(
            optimization.get("test_metrics_report_only")
        ),
        "多重试验修正": optimization.get("deflated_sharpe_probability"),
        "晋级门禁": optimization.get("promotion_gate"),
    }
    if operation == "cycle":
        result = {
            "周期": cycle,
            "状态概率": {
                "普林格": current.get("pring_probability"),
                "基钦": current.get("kitchin_probability"),
                "朱格拉": current.get("juglar_probability"),
                "康波": current.get("kondratieff_probability"),
                "美林": current.get("merrill_probability"),
            },
        }
    elif operation == "backtest":
        result = governance
    elif operation == "current":
        result = {
            "周期": cycle,
            "资产画像": profile,
            "资产权重": allocation.get("weights"),
            "风险贡献": allocation.get("risk_contribution"),
            "换手": (allocation.get("metadata") or {}).get(
                "current_rebalance_turnover"
            ),
            "治理": governance,
        }
    else:
        raise QueryError("资产配置动作仅支持 current、cycle、backtest")
    return _response("asset-allocation", operation, payload, path, result)


def _industry_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("rotation_snapshot.json")
    limit = _limit(params)
    frequency_raw = str(
        _param(params, "frequency", "频率", default="high_frequency")
    ).lower()
    aliases = {
        "高频": "high_frequency",
        "high": "high_frequency",
        "high_frequency": "high_frequency",
        "月频": "monthly",
        "月": "monthly",
        "monthly": "monthly",
        "周频": "weekly",
        "周": "weekly",
        "weekly": "weekly",
    }
    frequency = aliases.get(frequency_raw, frequency_raw)
    high_rows = (payload.get("high_frequency") or {}).get("industries") or []
    if operation == "ranking":
        if frequency == "high_frequency":
            rows = sorted(high_rows, key=lambda row: row.get("rank") if row.get("rank") is not None else 10_000)
            ranking = [
                {
                    "排名": row.get("rank"),
                    "行业": row.get("industry"),
                    "代码": row.get("code"),
                    "景气得分": row.get("score"),
                    "入选": row.get("selected"),
                    "实时指标数": row.get("live_indicators"),
                    "数据质量": row.get("data_quality"),
                }
                for row in rows[:limit]
            ]
            metrics = None
        elif frequency in {"monthly", "weekly"}:
            block = (
                ((payload.get("industry") or {}).get("frequencies") or {}).get(
                    frequency
                )
                or {}
            )
            ranking = [
                {
                    "排名": row.get("rank"),
                    "行业": row.get("name"),
                    "代码": row.get("code"),
                    "得分": row.get("score"),
                    "入选": row.get("selected"),
                    "权重": row.get("weight"),
                }
                for row in (block.get("ranking") or [])[:limit]
            ]
            metrics = {
                name: _compact_metrics(value)
                for name, value in (block.get("metrics") or {}).items()
            }
        else:
            raise QueryError("行业频率仅支持 高频、月频、周频")
        result = {"频率": frequency, "排名": ranking, "绩效": metrics}
    elif operation == "drivers":
        industry = str(_param(params, "industry", "行业", default="")).strip()
        if not industry:
            raise QueryError("drivers 动作必须提供 行业=行业名称")
        row = next(
            (
                item
                for item in high_rows
                if item.get("industry") == industry or item.get("code") == industry
            ),
            None,
        )
        if row is None:
            raise QueryError(f"未找到行业：{industry}")
        drivers = sorted(
            row.get("indicators") or [],
            key=lambda item: abs(_as_number(item.get("contribution")) or 0.0),
            reverse=True,
        )
        result = {
            "行业": row.get("industry"),
            "排名": row.get("rank"),
            "景气得分": row.get("score"),
            "驱动": [
                {
                    "指标": item.get("name"),
                    "频率": item.get("frequency"),
                    "单位": item.get("unit"),
                    "最新特征": item.get("latest_feature"),
                    "贡献": item.get("contribution"),
                    "方向": item.get("direction"),
                    "数据截止": item.get("last_available_date"),
                    "来源": item.get("source"),
                    "可用规则": item.get("availability_rule"),
                }
                for item in drivers[:limit]
            ],
        }
    elif operation == "style":
        style = payload.get("style") or {}
        cells = sorted(
            style.get("cells") or [],
            key=lambda item: _as_number(item.get("cap_share")) or 0.0,
            reverse=True,
        )
        result = {
            "信号日": style.get("latest_signal_date"),
            "执行日": style.get("latest_execution_date"),
            "频率": style.get("frequency"),
            "风格箱": cells[:limit],
            "迁移": style.get("migration"),
            "数据质量": style.get("data_quality"),
        }
    elif operation == "backtest":
        blocks = ((payload.get("industry") or {}).get("frequencies") or {})
        result = {
            name: {
                "入选方案": block.get("selected_candidate"),
                "研究挑战者": block.get("research_selected_candidate"),
                "绩效": {
                    split: _compact_metrics(metrics)
                    for split, metrics in (block.get("metrics") or {}).items()
                },
                "晋级门禁": block.get("promotion_gate"),
            }
            for name, block in blocks.items()
        }
    else:
        raise QueryError("行业景气度动作仅支持 ranking、drivers、style、backtest")
    return _response("industry-rotation", operation, payload, path, result)


def _latest_trace_point(trace: dict[str, Any]) -> dict[str, Any]:
    xs = trace.get("x") or []
    ys = trace.get("y") or []
    for index in range(min(len(xs), len(ys)) - 1, -1, -1):
        value = _as_number(ys[index])
        if value is not None:
            return {
                "名称": trace.get("name"),
                "日期": xs[index],
                "最新值": value,
                "来源编号": trace.get("source_id"),
            }
    return {
        "名称": trace.get("name"),
        "日期": None,
        "最新值": None,
        "来源编号": trace.get("source_id"),
    }


def _liquidity_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "标题": page.get("title"),
        "结论": page.get("conclusion"),
        "数据截止": page.get("as_of"),
        "图表": [
            {
                "图表": chart.get("title"),
                "频率": chart.get("frequency"),
                "参考": chart.get("reference"),
                "质量": chart.get("quality"),
                "最新值": [
                    _latest_trace_point(trace)
                    for trace in (chart.get("traces") or [])
                ],
            }
            for chart in (page.get("charts") or [])
        ],
    }


def _liquidity_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("liquidity_snapshot.json")
    pages = payload.get("pages") or {}
    aliases = {
        "主页": "home",
        "散户": "retail",
        "公募": "public",
        "ETF": "etf",
        "etf": "etf",
        "融资": "margin",
        "一级市场": "primary",
        "私募": "private",
        "外资": "foreign",
    }
    if operation == "overview":
        result = {
            "数据质量": payload.get("quality"),
            "页面": {
                name: {
                    "标题": page.get("title"),
                    "结论": page.get("conclusion"),
                    "数据截止": page.get("as_of"),
                    "图表数": len(page.get("charts") or []),
                }
                for name, page in pages.items()
            },
        }
    elif operation == "page":
        raw = str(_param(params, "page", "页面", default="home"))
        name = aliases.get(raw, raw)
        if name not in pages:
            raise QueryError(
                "未知资金页面；可选：主页、散户、公募、ETF、融资、一级市场、私募、外资"
            )
        result = _liquidity_page(pages[name])
    else:
        raise QueryError("资金面动作仅支持 overview、page")
    return _response("liquidity-tracking", operation, payload, path, result)


def _factor_manifest() -> tuple[dict[str, Any], Path]:
    configured = os.environ.get("QUANT_AGENT_FACTOR_MANIFEST", "").strip()
    path = (
        Path(configured).expanduser()
        if configured
        else ROOT / "model" / "factor_laboratory" / "champion_manifest.json"
    )
    return _read_json(path), path



def _factor_professional_framework() -> tuple[dict[str, Any], Path]:
    configured = os.environ.get("QUANT_AGENT_FACTOR_PROFESSIONAL_FRAMEWORK", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        ROOT / "model" / "factor_laboratory" / "professional_framework.json",
        ROOT.parent / "model" / "factor_laboratory" / "professional_framework.json",
        ROOT.parent / "source" / "professional_framework.json",
        ROOT.parent.parent / "source" / "professional_framework.json",
        ROOT.parent / "ai-models" / "factor-laboratory" / "source" / "professional_framework.json",
    ])
    for path in candidates:
        if path.is_file():
            return _read_json(path), path
    fallback = candidates[0] if candidates else ROOT / "model" / "factor_laboratory" / "professional_framework.json"
    return {
        "status": "unavailable",
        "version": "r35.9-professional-factor-lab-framework",
        "message": "professional_framework_artifact_unavailable",
    }, fallback


def _factor_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation == "champion":
        payload, path = _factor_manifest()
        high_sharpe = (
            (payload.get("enhanced_profiles") or {}).get("high_sharpe") or {}
        )
        result = {
            "冠军": payload.get("selected_candidate"),
            "选择依据": payload.get("selection_basis"),
            "测试用途": payload.get("test_usage"),
            "研究状态": payload.get("promotion_status"),
            "晋级结论": payload.get("promotion_decision"),
            "候选数": payload.get("candidate_count"),
            "三段绩效": payload.get("splits"),
            "门禁": payload.get("gates"),
            "候选归因": payload.get("candidate_diagnostics"),
        }
        if high_sharpe:
            result["高夏普增强档"] = {
                "候选": high_sharpe.get("selected_candidate"),
                "候选名称": high_sharpe.get("selected_candidate_label"),
                "选择依据": high_sharpe.get("selection_basis"),
                "测试用途": high_sharpe.get("test_usage"),
                "研究状态": high_sharpe.get("promotion_status"),
                "晋级结论": high_sharpe.get("promotion_decision"),
                "换手预算": high_sharpe.get("turnover_budget"),
                "三段绩效": high_sharpe.get("splits"),
                "门禁": high_sharpe.get("gates"),
                "门禁汇总": high_sharpe.get("gate_summary"),
                "默认状态": "已按授权切为默认，采用0.80增强档换手预算。" if high_sharpe.get("selected_candidate") == payload.get("selected_candidate") else "需明确授权放宽默认换手预算至0.80后才能替代0.65稳健冠军。",
            }
        return _response("factor-laboratory", operation, payload, path, result)

    payload, path = _snapshot("index_enhancement_snapshot.json")
    if operation == "index":
        universe = str(
            _param(params, "universe", "指数", default="CSI800_ENH")
        ).upper()
        audits = payload.get("champion_audit") or {}
        if universe not in audits:
            raise QueryError(f"未知指数增强标的池：{universe}")
        leaderboard = [
            row
            for row in (payload.get("leaderboard") or [])
            if row.get("universe") == universe
        ]
        result = {
            "标的池": universe,
            "冠军审计": audits.get(universe),
            "排行榜": leaderboard[: _limit(params)],
            "影子挑战者": payload.get("shadow_challenger_audit"),
            "治理": payload.get("governance"),
        }
    elif operation == "models":
        framework, _framework_path = _factor_professional_framework()
        result = {
            "模型": payload.get("models"),
            "SmartBeta": payload.get("smartbeta"),
            "风险层": payload.get("risk"),
            "求解": payload.get("solver"),
            "治理": payload.get("governance"),
            "professional_framework": framework,
        }
    else:
        raise QueryError("因子实验室动作仅支持 champion、index、models")
    return _response("factor-laboratory", operation, payload, path, result)


def _technical_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation == "status":
        payload, path = _snapshot("kline_cross_sectional_audit.json")
        result = {
            "治理状态": payload.get("status"),
            "版本": payload.get("version"),
            "选择规则": payload.get("selection_policy"),
            "测试是否参与选择": payload.get("selection_uses_test"),
            "入选候选": payload.get("selected_candidate"),
            "候选数": payload.get("candidate_count"),
            "合格数": payload.get("eligible_count"),
            "切分": payload.get("split"),
            "完整性": payload.get("integrity"),
        }
        return _response("technical-analysis", operation, payload, path, result)
    if operation == "patterns":
        query_text = str(
            _param(params, "query", "关键词", default="")
        ).strip().lower()
        root = (
            ROOT
            / "skill"
            / "technical-analysis"
            / "references"
            / "kline-patterns"
        )
        rows = []
        for path in sorted(root.glob("*.md")):
            if query_text and query_text not in path.stem.lower():
                continue
            rows.append(
                {
                    "形态": path.stem,
                    "文件": str(path.relative_to(ROOT)),
                }
            )
        payload = {"as_of": None, "generated_at": None}
        return _response(
            "technical-analysis",
            operation,
            payload,
            root,
            rows[: _limit(params, default=20, maximum=200)],
        )
    raise QueryError("技术分析动作仅支持 status、patterns")


def _portfolio_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("portfolio_optimization_snapshot.json")
    home = payload.get("home") or {}
    optimization = payload.get("optimization") or {}
    backtest = payload.get("backtest") or {}
    if operation == "current":
        minimum = _as_number(
            _param(params, "minimum_weight", "最小权重", default=0.0001)
        )
        if minimum is None or minimum < 0:
            raise QueryError("最小权重必须是不小于0的数字")
        weights = [
            row
            for row in (home.get("current_weights") or [])
            if (_as_number(row.get("weight")) or 0.0) >= minimum
        ]
        result = {
            "候选": home.get("selected_candidate"),
            "求解器": home.get("selected_solver"),
            "权重": weights,
            "晋级门禁": home.get("promotion_gate"),
        }
    elif operation == "solver":
        result = {
            "入选参数": optimization.get("selected_spec"),
            "求解器基准": optimization.get("solver_benchmark"),
            "约束余量": optimization.get("constraint_slack"),
            "风险模型": (payload.get("risk_constraints") or {}).get(
                "risk_models"
            ),
            "约束": (payload.get("risk_constraints") or {}).get("constraints"),
            "多重试验修正": optimization.get("deflated_sharpe_probability"),
            "PBO": optimization.get("pbo_cscv"),
        }
    elif operation == "backtest":
        result = {
            "策略": backtest.get("strategies"),
            "收益损失归因": backtest.get("return_loss_attribution"),
            "成本敏感性": backtest.get("cost_sensitivity_test"),
            "压力情景": backtest.get("stress_scenarios"),
            "晋级门禁": backtest.get("promotion_gate"),
        }
    else:
        raise QueryError("组合优化动作仅支持 current、solver、backtest")
    return _response("portfolio-optimization", operation, payload, path, result)


def _dashboard_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation in {"overview", "market"}:
        payload, path = _snapshot("global_market_snapshot.json")
        rows = payload.get("rows") or []
        region = str(_param(params, "region", "地区", default="")).strip()
        if region:
            rows = [row for row in rows if str(row.get("region")) == region]
        ranked = sorted(
            rows,
            key=lambda row: abs(_as_number(row.get("ret_1d")) or 0.0),
            reverse=True,
        )
        result = {
            "市场": ranked[: _limit(params)],
            "状态": payload.get("status"),
        }
        if operation == "overview":
            root = _snapshot_root()
            result["快照"] = [
                {
                    "文件": item.name,
                    "大小": item.stat().st_size,
                    "更新时间": item.stat().st_mtime,
                }
                for item in sorted(root.glob("*.json"))
            ]
        return _response("data-dashboard", operation, payload, path, result)
    if operation == "news":
        payload, path = _snapshot("sina_news_snapshot.json")
        rows = (
            payload.get("rows")
            or payload.get("news")
            or payload.get("items")
            or []
        )
        result = rows[: _limit(params, default=20)]
        return _response("data-dashboard", operation, payload, path, result)
    raise QueryError("数据看板动作仅支持 overview、market、news")


def _research_home_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation != "overview":
        raise QueryError("主页动作仅支持 overview")
    modules: dict[str, Any] = {}
    calls = [
        ("数据看板", "data-dashboard", "market", {"数量": 5}),
        ("资产配置", "asset-allocation", "current", params),
        ("资金面", "liquidity-tracking", "overview", {}),
        ("行业景气", "industry-rotation", "ranking", {"数量": 5}),
        ("因子", "factor-laboratory", "champion", {}),
        ("技术分析", "technical-analysis", "status", {}),
        ("组合优化", "portfolio-optimization", "current", {"最小权重": 0.001}),
    ]
    for title, skill, action, child_params in calls:
        try:
            modules[title] = query(skill, action, child_params)
        except QueryError as exc:
            modules[title] = {"状态": "受阻", "原因": str(exc)}
    payload = {"as_of": None, "generated_at": None}
    return _response(
        "research-home",
        operation,
        payload,
        "七个一级研究模块",
        modules,
    )


def query(
    skill: str, operation: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    params = dict(params or {})
    dispatch = {
        "research-home": _research_home_query,
        "data-dashboard": _dashboard_query,
        "asset-allocation": _asset_query,
        "liquidity-tracking": _liquidity_query,
        "industry-rotation": _industry_query,
        "factor-laboratory": _factor_query,
        "technical-analysis": _technical_query,
        "portfolio-optimization": _portfolio_query,
    }
    handler = dispatch.get(skill)
    if handler is None:
        raise QueryError(
            f"未知模型 Skill：{skill}；运行 python -m agent_runtime catalog 查看清单"
        )
    return handler(operation, params)
