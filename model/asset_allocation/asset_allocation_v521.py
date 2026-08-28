"""Validation-governed presentation layer for the v5.2 dual-policy model.

This module deliberately does not change candidate parameters, weights, returns,
or backtests.  It adds an execution fallback and an explicit input/output
contract after the first frozen v5.2 run showed that the benchmark-relative
family had no validation-eligible candidate.  The fallback rule reads training
and validation governance only; the retrospective test remains report-only.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from asset_allocation_v52 import ASSET_ORDER_V5, build_snapshot_v52


SCHEMA_VERSION_V521 = "5.2.1"
ENGINE_VERSION_V521 = "asset-allocation-research-v5.2.1-validation-governed-shadow"
STRATEGIC_HOLD_MODE_V521 = "strategic_benchmark_hold"


def _rehash(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("model_hash", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pp(value: Any) -> str:
    return f"{abs(_number(value)) * 100:.2f}"


def _pct(value: Any) -> str:
    return f"{_number(value) * 100:.2f}%"


def _relative_reason(row: Mapping[str, Any]) -> str:
    active = _number(row.get("active_weight"))
    rank = int(_number(row.get("strength_rank"), 99.0))
    stance = str(row.get("allocation_stance_cn") or "持平")
    label = str(row.get("strength_label_cn") or "未判定")
    vol = _pct(row.get("bl_expected_volatility_annual"))
    risk = _pct(row.get("risk_contribution"))
    if active < -1e-10 and rank <= 2:
        return (
            f"收益信号为“{label}”，但预测波动为{vol}、组合风险贡献为{risk}；"
            f"主动份额、跟踪误差和风险预算共同约束后，最终{stance}{_pp(active)}个百分点。"
            "强弱是预期收益信号排序，不等于资本权重排序。"
        )
    if active > 1e-10 and rank >= 3:
        return (
            f"收益信号为“{label}”，但预测波动仅{vol}且具有组合分散作用；"
            f"风险预算优化后最终{stance}{_pp(active)}个百分点。"
            "该配置来自完整协方差和边际风险，而非按强弱机械排序。"
        )
    if active > 1e-10:
        return (
            f"收益信号为“{label}”，BL预期收益与中长期趋势提供支持；"
            f"在风险及交易约束后最终{stance}{_pp(active)}个百分点，预测波动为{vol}。"
        )
    if active < -1e-10:
        return (
            f"收益信号为“{label}”，风险调整后吸引力不足；"
            f"在风险及交易约束后最终{stance}{_pp(active)}个百分点，预测波动为{vol}。"
        )
    return f"收益信号为“{label}”，约束优化后相对战略基准保持中性。"


def _absolute_reason(row: Mapping[str, Any]) -> str:
    label = str(row.get("strength_label_cn") or "未判定")
    weight = _pct(row.get("capital_weight"))
    vol = _pct(row.get("bl_expected_volatility_annual"))
    risk = _pct(row.get("risk_contribution"))
    return (
        f"无政策基准输入；该资产收益信号为“{label}”，最终权重{weight}、"
        f"预测波动{vol}、组合风险贡献{risk}。权重由BL后验收益、完整协方差、"
        "约束风险预算、上下限与换手成本联合决定，因此不按强弱名次机械排序。"
    )


def _decorate_decisions(payload: dict[str, Any]) -> None:
    decisions = payload.get("asset_decisions") or {}
    relative = decisions.get("benchmark_relative") or {}
    absolute = decisions.get("absolute_no_benchmark") or {}
    for asset in ASSET_ORDER_V5:
        if isinstance(relative.get(asset), dict):
            relative[asset]["decision_summary_cn"] = _relative_reason(relative[asset])
            relative[asset]["strength_is_not_weight_rank"] = True
            relative[asset]["allocation_driver_order"] = [
                "BL后验预期收益",
                "完整协方差与边际风险",
                "约束风险预算",
                "60/15/15/10主动边界",
                "跟踪误差、主动份额与换手成本",
            ]
        if isinstance(absolute.get(asset), dict):
            absolute[asset]["decision_summary_cn"] = _absolute_reason(absolute[asset])
            absolute[asset]["strength_is_not_weight_rank"] = True
            absolute[asset]["allocation_driver_order"] = [
                "BL后验预期收益",
                "完整协方差与边际风险",
                "内生风险预算",
                "资产上下限",
                "换手成本",
            ]


def _model_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    return {
        "version": SCHEMA_VERSION_V521,
        "asset_universe": ["权益", "国债", "黄金", "非黄金商品"],
        "internal_asset_order": list(ASSET_ORDER_V5),
        "input_frequency": "月频；仅使用当月决策时点可得信息",
        "inputs": {
            "market": {
                "fields": [
                    "四类资产月度收益",
                    "24个月滚动协方差",
                    "3/6/12个月风险调整趋势",
                    "资产级交易成本",
                ],
                "required_assets": list(ASSET_ORDER_V5),
            },
            "cycles": {
                "families": ["康波", "朱格拉", "基钦", "美林时钟", "普林格"],
                "admission_rule": "available_time、vintage及所需因子通过PIT门禁后才可进入BL观点；否则仅展示",
                "current_admission": ((payload.get("cycle_factor_availability") or {}).get("admitted_cycles") or []),
            },
            "benchmark_relative": {
                "policy_benchmark": {"equity": 0.60, "bond": 0.15, "gold": 0.10, "commodity": 0.15},
                "active_bands": dict(
                    zip(ASSET_ORDER_V5, config.get("policy_active_bands") or [0.10, 0.05, 0.03, 0.05])
                ),
                "max_active_share": config.get("policy_max_active_share"),
                "max_annual_tracking_error": config.get("policy_max_annual_tracking_error"),
                "max_one_way_turnover": config.get("policy_max_one_way_turnover"),
            },
            "absolute_no_benchmark": {
                "policy_benchmark_used": False,
                "prior_and_anchor": "内生约束风险预算",
                "constraints": "四资产上下限、换手、成本和风险预算；政策基准只用于事后报告比较",
            },
        },
        "pipeline": [
            "D3资产口径与PIT宏观门禁",
            "五类周期的显式持续期状态概率",
            "三条联合BL观点与非对角Omega",
            "Ledoit-Wolf/宏观因子协方差与尾部压力",
            "严格风险平价及约束风险预算",
            "相对基准或无基准双优化器",
            "交易成本后训练/验证选择",
            "回顾测试只报告，未来纸面组合再封存验证",
        ],
        "outputs": {
            "weights": "两版四资产当前权重；相对版同时输出基准权重、主动权重和高低配",
            "strength": "四资产最强/偏强/偏弱/最弱、概率、预期收益、预测波动及输入信号",
            "risk": "资本权重、风险贡献、主动份额、事前跟踪误差和换手",
            "performance": "训练/验证/回顾测试的收益、波动、标准夏普、回撤、成本；相对版另含超额和信息比率",
            "governance": "候选选择审计、PIT/D3/统计门禁、可执行或回退结论",
        },
        "selection_uses_test": False,
        "performance_guarantee": False,
    }


def apply_validation_governance_v521(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Add validation-only deployment governance without changing model results."""

    payload = copy.deepcopy(dict(snapshot))
    if list(payload.get("asset_order") or []) != list(ASSET_ORDER_V5):
        raise ValueError("v521_asset_order_mismatch")
    benchmark_weights = ((payload.get("benchmark") or {}).get("weights") or {})
    expected = {"equity": 0.60, "bond": 0.15, "gold": 0.10, "commodity": 0.15}
    if any(abs(_number(benchmark_weights.get(k)) - v) > 1e-12 for k, v in expected.items()):
        raise ValueError("v521_policy_benchmark_mismatch")

    backtest = payload.get("backtest") or {}
    audit = backtest.get("selection_audit") or {}
    relative_audit = audit.get("benchmark_relative") or {}
    promotion = (((payload.get("quality") or {}).get("promotion_by_version") or {}).get("benchmark_relative") or {})
    eligible_count = int(_number(relative_audit.get("eligible_count")))
    validation_eligible = eligible_count > 0
    promotion_passed = str(promotion.get("status") or "blocked") == "passed"
    dynamic_deployable = validation_eligible and promotion_passed

    allocations = payload.get("allocations") or {}
    if not all(key in allocations for key in ("strategic_benchmark", "benchmark_relative", "absolute_no_benchmark")):
        raise ValueError("v521_required_allocation_missing")
    original_recommended_mode = allocations.get("recommended_mode")
    original_optimization = copy.deepcopy(payload.get("optimization"))
    allocations["research_challenger"] = copy.deepcopy(allocations["benchmark_relative"])
    allocations["authorized_research_mode"] = "benchmark_relative"

    if not dynamic_deployable:
        allocations["recommended"] = copy.deepcopy(allocations["strategic_benchmark"])
        allocations["recommended_mode"] = STRATEGIC_HOLD_MODE_V521
        strategies = backtest.get("strategies") or {}
        strategies["research_challenger"] = copy.deepcopy(strategies.get("benchmark_relative"))
        strategies["recommended"] = copy.deepcopy(strategies.get("strategic_benchmark"))
        audit["recommended_mode"] = STRATEGIC_HOLD_MODE_V521
        audit["recommended_mode_rule"] = (
            "universal validation-only fallback: hold the declared strategic benchmark until the relative family has an eligible validation candidate and all promotion gates pass; test is never consulted"
        )
        payload["research_optimization"] = original_optimization
        payload["optimization"] = {
            "role": "not_applicable_while_holding_strategic_benchmark",
            "research_challenger": "benchmark_relative",
        }

    reason_codes: list[str] = []
    if not validation_eligible:
        reason_codes.append("no_validation_eligible_benchmark_relative_candidate")
    for item in promotion.get("failed") or []:
        code = str(item)
        if code not in reason_codes:
            reason_codes.append(code)
    payload["deployment_decision"] = {
        "status": "dynamic_model_deployable" if dynamic_deployable else "hold_strategic_benchmark",
        "deployable_dynamic_model": dynamic_deployable,
        "executed_mode": "benchmark_relative" if dynamic_deployable else STRATEGIC_HOLD_MODE_V521,
        "research_challenger": "benchmark_relative",
        "absolute_research_version": "absolute_no_benchmark",
        "reason_codes": reason_codes,
        "uses_training": True,
        "uses_validation": True,
        "uses_retrospective_test": False,
        "plain_language_cn": (
            "相对基准挑战模型尚未通过验证与数据/统计门禁，当前可执行建议保持60/15/15/10战略基准；"
            "两套动态权重继续作为研究输出，不得把回顾测试高夏普解释为已验证超额。"
            if not dynamic_deployable
            else "相对基准模型通过验证和全部门禁，可作为动态执行建议。"
        ),
    }
    payload["model_contract"] = _model_contract(payload)
    _decorate_decisions(payload)
    payload["performance_claim"] = {
        "validated_positive_excess": validation_eligible,
        "guaranteed_high_sharpe": False,
        "retrospective_test_is_pristine": False,
        "statement_cn": "模型目标是提高风险调整收益并争取超额，但本次相对基准版未验证出正超额，不能保证未来夏普或收益。",
    }
    payload["governance_correction_v521"] = {
        "applied": True,
        "trigger": "first frozen v5.2 relative family had zero validation-eligible candidates",
        "rule_reads_test": False,
        "candidate_parameters_changed": False,
        "dynamic_weights_changed": False,
        "backtest_returns_changed": False,
        "display_recommendation_changed": not dynamic_deployable,
        "original_recommended_mode": original_recommended_mode,
        "current_recommended_mode": allocations.get("recommended_mode"),
    }
    payload["schema_version"] = SCHEMA_VERSION_V521
    payload["engine_version"] = ENGINE_VERSION_V521
    return _rehash(payload)


def build_snapshot_v521(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return apply_validation_governance_v521(build_snapshot_v52(*args, **kwargs))


__all__ = [
    "ENGINE_VERSION_V521",
    "SCHEMA_VERSION_V521",
    "STRATEGIC_HOLD_MODE_V521",
    "apply_validation_governance_v521",
    "build_snapshot_v521",
]
