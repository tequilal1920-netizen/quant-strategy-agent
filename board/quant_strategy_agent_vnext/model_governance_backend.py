"""Read-only train/validation/sealed-test evidence contract for the dashboard."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1] if APP_DIR.name == "quant_strategy_agent" else APP_DIR
EVIDENCE_ROOT = PROJECT_ROOT / "output" / "model_improvement"
RELEASE = "2026.08.17-technical-dual-model-governed-r38.0"


def _asset_snapshot_path() -> Path:
    return Path(
        os.environ.get(
            "ASSET_ALLOCATION_SNAPSHOT",
            str(APP_DIR / "data" / "asset_allocation_snapshot.json"),
        )
    ).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _metric(source: dict[str, Any] | None) -> dict[str, Any]:
    source = source or {}
    result = {
        "sharpe": _number(source.get("sharpe")),
        "information_ratio": _number(
            source.get("information_ratio", source.get("excess_sharpe"))
        ),
        "annual_return": _number(source.get("annual_return")),
        "annual_excess_return": _number(
            source.get(
                "annual_excess_return",
                source.get("annual_excess", source.get("excess_annual_return")),
            )
        ),
        "max_drawdown": _number(source.get("max_drawdown")),
        "turnover": _number(source.get("annual_turnover", source.get("turnover"))),
        "observations": source.get("observations", source.get("months")),
    }
    return {key: value for key, value in result.items() if value is not None}


def _base_models() -> dict[str, dict[str, Any]]:
    return {
        "data_dashboard": {
            "name": "数据看板",
            "engine": "multi-source-point-in-time-dashboard",
            "champion": "日度研究快照",
            "gate": "tracking",
            "metric_kind": "data_quality",
            "quality": {"status": "passed", "note": "展示层按来源和可得日追踪，不以夏普评价"},
            "loss_attribution": "该板块承担信息集和数据可得性审计，不直接形成可交易收益。",
            "next_action": "继续保留来源、可得日、缺失率和修订痕迹，禁止把事后修订值回填历史。",
        },
        "asset_allocation": {
            "name": "资产配置",
            "engine": "asset-allocation-research-v4.5-empirical-view-uncertainty",
            "champion": "B06 equity_guarded_posterior",
            "gate": "conditional",
            "metric_kind": "strategy",
            "splits": {
                "train": {"sharpe": 1.070510, "annual_return": 0.068963, "information_ratio": 0.343933, "max_drawdown": -0.059792},
                "validation": {"sharpe": 0.022975, "annual_return": 0.000255, "information_ratio": 0.383413, "max_drawdown": -0.029535},
                "test": {"sharpe": 1.522463, "annual_return": 0.117842, "information_ratio": 0.717041, "max_drawdown": -0.063172},
            },
            "test_policy": "report_only_after_train_validation_selection",
            "loss_attribution": "验证期绝对收益接近零，主动信息比率仍为正。模型更接近稳定主动配置器，尚不能按测试期夏普直接晋级。",
            "next_action": "保留周期后验、相对强弱和协方差收缩主链；新增方案只在训练期声明并由验证期筛选，DSR未过前维持条件候选。",
            "robustness": {"dsr_passed": False, "pbo": 0.30},
        },
        "liquidity_tracking": {
            "name": "资金面跟踪",
            "engine": "liquidity-dashboard-v2",
            "champion": "七类资金PIT追踪",
            "gate": "tracking",
            "metric_kind": "data_quality",
            "quality": {"status": "passed", "checks": "78/78", "charts": 37},
            "loss_attribution": "该板块用于解释资金状态和权益关联，不应把追踪质量转换为虚构夏普。",
            "next_action": "坚持真实资金口径、发布日期和代理变量分层，金额序列缺失时保持缺失。",
        },
        "industry_rotation": {
            "name": "行业风格轮动",
            "engine": "industry-rotation/4.7-common-window-report-momentum",
            "champion": "C6_direct_month_smooth",
            "gate": "review",
            "metric_kind": "strategy",
            "splits": {
                "train": {"sharpe": -1.609143, "information_ratio": 0.863655, "annual_return": -0.218345, "max_drawdown": -0.290187},
                "validation": {"sharpe": 1.112870, "information_ratio": 0.616433, "annual_return": 0.216970, "max_drawdown": -0.177857},
                "test": {"sharpe": 0.119620, "information_ratio": 0.337019, "annual_return": 0.004263, "max_drawdown": -0.364398},
            },
            "test_policy": "train_direction_validation_selection_test_report_only",
            "loss_attribution": "2022年后绝对收益和超额收益同步衰减，月频仍有弱正超额，周频转负。主要问题是行业状态迁移和高频信号寿命缩短，成本不是唯一解释。",
            "next_action": "冻结现有冠军为观察基线，后续只接受跨状态可靠性、截面覆盖和稳定性同时改善的训练期候选。",
        },
        "factor_laboratory": {
            "name": "因子实验室",
            "engine": "factor-lab/3.2-inverse-volatility-rank-execution",
            "champion": "adaptive_icir_12m_neutral::continuous_rank_volatility_budget",
            "gate": "research_candidate",
            "metric_kind": "strategy",
            "splits": {
                "train": {"sharpe": 0.463768, "annual_return": 0.042948, "max_drawdown": -0.127024},
                "validation": {"sharpe": 1.125880},
                "test": {"sharpe": 2.466000, "annual_return": 0.274500, "max_drawdown": -0.028152},
            },
            "test_policy": "sealed_report_only",
            "loss_attribution": "训练期风险调整收益偏低，验证期显著改善；封存测试表现较强，但换手预算仍未通过，不能据此继续调参。",
            "next_action": "保留因果ICIR权重、正交中性化和波动预算执行。下一轮从交易路径和信号稳定性降低换手，不改变封存测试。",
            "robustness": {"gates_passed": 9, "gates_total": 10, "turnover_gate_passed": False},
        },
        "kline_memory": {
            "name": "K线学习",
            "engine": "kline-memory/9.2-dual-momentum-volatility-budget",
            "champion": "验证失败观察保护",
            "gate": "observe_only",
            "metric_kind": "strategy",
            "splits": {
                "train": {"sharpe": 0.0, "annual_return": 0.0},
                "validation": {"sharpe": 0.0, "annual_return": 0.0},
                "test": {"sharpe": 0.0, "annual_return": 0.0},
            },
            "test_policy": "sealed_report_only",
            "loss_attribution": "当前候选没有有效训练和验证持仓路径。零波动来自空仓保护，不构成模型改善，也不应计为低风险冠军。",
            "next_action": "先建立跨股票、跨形态、跨市场状态的独立验证组合。单股规则只有在训练和验证均形成有效路径后才能进入封存测试。",
        },
        "portfolio_optimization": {
            "name": "组合优化",
            "engine": "portfolio-optimizer/2.4-solver-audit",
            "champion": "C188 risk_adjusted_trend + EWMA",
            "gate": "research_candidate",
            "metric_kind": "strategy",
            "splits": {
                "train": {"sharpe": 1.402566, "annual_return": 0.079115, "information_ratio": -0.160580},
                "validation": {"sharpe": 0.002987, "annual_return": -0.001111, "information_ratio": 0.596941},
                "test": {"sharpe": 2.438049, "annual_return": 0.122241, "information_ratio": -0.133000, "max_drawdown": -0.019422},
            },
            "test_policy": "train_shortlist_validation_select_test_report_only",
            "loss_attribution": "测试期绝对夏普较高但主动信息比率为负。宽基和行业权益合计拖累约16.7个百分点，商品和债券现金贡献约12.5个百分点，成本仅为次要损耗。",
            "next_action": "保留可行域、协方差和交易成本主链。后续候选须在训练期显式优化主动风险预算，并由验证期同时约束绝对夏普和信息比率。",
            "robustness": {"pbo_passed": True, "dsr_passed": False},
        },
        "index_enhancement": {
            "name": "指数增强",
            "engine": "index-enhancement/1.1-split-champion-audit",
            "champion": "待训练验证重新筛选",
            "gate": "review",
            "metric_kind": "strategy",
            "loss_attribution": "旧版页面以全样本正式回测聚合模型，未形成训练期初筛、验证期选模和测试期只报告的独立冠军契约。",
            "next_action": "新后端按时间切分逐模型计算夏普、信息比率、回撤和换手，只用训练与验证选择冠军，测试结果在冻结后展示。",
        },
        "llm_factor_agent": {
            "name": "LLM因子挖掘",
            "engine": "llm-hypothesis-to-dsl-governance",
            "champion": "研究代理",
            "gate": "research",
            "metric_kind": "research_pipeline",
            "loss_attribution": "LLM负责提出可执行假设，不拥有独立可晋级收益。任何表达式都必须经过PIT、泄漏、冗余、IC衰减和组合层验证。",
            "next_action": "将自然语言假设固定为DSL表达式和数据依赖清单，候选预算和淘汰轨迹全量留痕，通过因子实验室统一晋级。",
        },
    }


def _update_asset_v522(model: dict[str, Any], data: dict[str, Any]) -> None:
    strategies = (data.get("backtest") or {}).get("strategies") or {}
    relative = strategies.get("benchmark_relative") or {}
    allocations = data.get("allocations") or {}
    quality = data.get("quality") or {}
    deployment = data.get("deployment_decision") or {}
    availability = data.get("cycle_factor_availability") or {}
    current_cycles = (
        ((allocations.get("current_cycle") or {}).get("cycles") or {})
    )
    cycle_labels = {
        "pring": "普林格周期",
        "kitchin": "基钦周期",
        "juglar": "朱格拉周期",
        "merrill": "美林时钟",
        "kondratieff": "康波周期",
    }
    admitted = set(availability.get("admitted_cycles") or [])
    availability_cycles = availability.get("cycles") or {}

    model["engine"] = data.get("engine_version") or model["engine"]
    model["champion"] = "benchmark_relative 基准高低配版"
    model["gate"] = deployment.get("status") or "user_approved_sharpe_mandate"
    model["metric_kind"] = "strategy"
    model["splits"] = {
        split: _metric(metrics)
        for split, metrics in (relative.get("metrics") or {}).items()
        if split in {"train", "validation", "test"}
    }
    model["test_policy"] = (
        (data.get("methodology") or {}).get("test_policy")
        or "retrospective_report_only_not_service_authorization_basis"
    )
    model["cycle_status"] = [
        {
            "cycle": cycle,
            "label": label,
            "stage": (current_cycles.get(cycle) or {}).get("state_name")
            or (current_cycles.get(cycle) or {}).get("state"),
            "confidence": _number(
                (current_cycles.get(cycle) or {}).get("confidence")
            ),
            "data_status": (availability_cycles.get(cycle) or {}).get(
                "data_status"
            )
            or (current_cycles.get(cycle) or {}).get("data_status"),
            "enters_allocation": cycle in admitted
            and bool(
                (availability_cycles.get(cycle) or {}).get(
                    "eligible_for_views",
                    (current_cycles.get(cycle) or {}).get("eligible_for_views"),
                )
            ),
        }
        for cycle, label in cycle_labels.items()
    ]
    model["model_chain"] = [
        {
            "model": "宏观因子风险模型",
            "steps": ["输入", "计算", "约束", "输出", "最终作用"],
            "status": (
                (
                    (data.get("macro_factor_risk_audit") or {}).get(
                        "by_model_version"
                    )
                    or {}
                ).get("benchmark_relative")
                or {}
            ).get("status"),
        },
        {
            "model": "严格风险平价（ERC）",
            "steps": ["输入", "计算", "约束", "输出", "最终作用"],
            "status": (
                ((allocations.get("risk_parity") or {}).get("metadata") or {})
            ).get("status"),
        },
        {
            "model": "约束风险预算",
            "steps": ["输入", "计算", "约束", "输出", "最终作用"],
            "status": (
                ((allocations.get("macro_risk_budget") or {}).get("metadata") or {})
            ).get("status"),
        },
        {
            "model": "稳健Black–Litterman",
            "steps": ["输入", "计算", "约束", "输出", "最终作用"],
            "status": (
                (
                    (
                        (allocations.get("benchmark_relative") or {}).get(
                            "metadata"
                        )
                        or {}
                    ).get("black_litterman")
                    or {}
                ).get("diagnostics")
                or {}
            ).get("status"),
        },
    ]
    model["robustness"] = {
        "quality_status": quality.get("status"),
        "service_authorization": {
            "status": deployment.get("status"),
            "deployable_dynamic_model": deployment.get(
                "deployable_dynamic_model"
            ),
            "executed_mode": deployment.get("executed_mode"),
            "authorization_basis": deployment.get("authorization_basis"),
        },
        "sharpe_authorization_gate": quality.get("promotion_gate") or {},
        "statistical_evidence_gate": quality.get("statistical_evidence_gate")
        or {},
        "statistical_evidence_by_version": quality.get(
            "statistical_evidence_by_version"
        )
        or {},
        "policy_benchmark": (data.get("benchmark") or {}).get("weights") or {},
        "nav_display_only_benchmark": {
            "id": "equal_weight_25",
            "weights": {
                "equity": 0.25,
                "bond": 0.25,
                "gold": 0.25,
                "commodity": 0.25,
            },
            "optimizer_input": False,
            "active_return_anchor": False,
        },
        "cost_consistency_audit": data.get("cost_consistency_audit") or {},
    }
    validation = model["splits"].get("validation") or {}
    test = model["splits"].get("test") or {}
    model["loss_attribution"] = (
        "基准高低配版已按用户明确夏普目标授权服务；"
        f"验证期夏普{_number(validation.get('sharpe')) or 0:.2f}，"
        f"回顾测试夏普{_number(test.get('sharpe')) or 0:.2f}。"
        "统计稳健性、D3/PIT和未来独立样本作为单独观察项保留，不改写历史结果。"
    )
    model["next_action"] = (
        "保持60/15/10/15为优化及主动收益锚，等权25/25/25/25只作净值展示；"
        "逐月保留五周期因子可得性、BL完整Omega、风险预算KKT、约束和成本审计。"
    )


def _update_asset(model: dict[str, Any]) -> None:
    data = _read_json(_asset_snapshot_path())
    if str(data.get("schema_version") or "") == "5.2.2":
        _update_asset_v522(model, data)
        return
    if not data:
        data = _read_json(EVIDENCE_ROOT / "asset_empirical_uncertainty_v45_20260726.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    backtest = data.get("backtest") or {}
    audit = backtest.get("selection_audit") or {}
    selected = audit.get("selected_spec") or data.get("selected_spec") or {}
    model["champion"] = f"{selected.get('id', 'B06')} {selected.get('family', 'posterior')}"
    recommended = ((backtest.get("strategies") or {}).get("recommended") or {})
    absolute = recommended.get("metrics_by_split") or data.get("metrics_by_split") or {}
    active = recommended.get("active_metrics_by_split") or data.get("active_metrics_by_split") or {}
    model["splits"] = {}
    for split in ("train", "validation", "test"):
        metric = _metric(absolute.get(split))
        active_metric = _metric(active.get(split))
        for key in ("information_ratio", "annual_excess_return"):
            if key in active_metric:
                metric[key] = active_metric[key]
        model["splits"][split] = metric
    objectives = backtest.get("objective_champions") or {}
    stable = objectives.get("stable_absolute") or {}
    if stable:
        model["champion"] += f"；稳健绝对 {stable.get('model') or stable.get('strategy')}"
    model["robustness"] = {
        **(model.get("robustness") or {}),
        "pbo": audit.get("pbo_cscv"),
        "objective_champions": objectives,
        "quality_status": (data.get("quality") or {}).get("status"),
    }
    model["loss_attribution"] = (
        "战略偏好目标的验证期绝对收益接近零，但主动信息比率仍为正；"
        f"稳健绝对目标由训练与验证夏普下界选择{stable.get('model') or '既有架构'}，"
        f"两段夏普为{_number(stable.get('train_sharpe')) or 0:.2f}和"
        f"{_number(stable.get('validation_sharpe')) or 0:.2f}。测试期仅作报告。"
    )
    model["next_action"] = (
        "战略偏好和稳健绝对两条目标分别治理。未来候选必须在训练期声明，"
        "由验证期门禁筛选；已观察测试结果不得参与切换。"
    )


def _update_liquidity(model: dict[str, Any]) -> None:
    data = _read_json(APP_DIR / "data" / "liquidity_state_challenger.json")
    if not data:
        return
    selected = data.get("selected") or {}
    evaluation = selected.get("evaluation") or {}
    audit = data.get("data_audit") or {}
    split_source = evaluation.get("split_metrics") or {}
    model["engine"] = data.get("engine_version") or model["engine"]
    model["champion"] = data.get("selected_candidate") or model["champion"]
    model["gate"] = "research_diagnostic"
    model["metric_kind"] = "strategy_and_data_quality"
    model["splits"] = {
        ("validation" if split == "valid" else split): _metric(metrics)
        for split, metrics in split_source.items()
        if split in {"train", "valid", "validation", "test"}
    }
    registry = audit.get("feature_registry") or []
    effective_series = {
        row.get("series_id")
        for row in registry
        if int(row.get("observations") or 0) > 0
    }
    snapshot = _read_json(APP_DIR / "data" / "liquidity_snapshot.json")
    quality = snapshot.get("quality") or {}
    dsr = selected.get("deflated_sharpe_train_validation") or {}
    model["test_policy"] = "train_validation_selection_test_report_only"
    model["robustness"] = {
        "selection_uses_test": bool(evaluation.get("selection_uses_test", False)),
        "promotion_eligible": bool(evaluation.get("promotion_eligible", False)),
        "research_status": evaluation.get("research_status"),
        "candidate_count": len(data.get("candidate_evaluations") or []),
        "exact_database_series": int(audit.get("exact_model_input_count") or 0),
        "effective_training_series": len(effective_series),
        "excluded_contracts": int(audit.get("contract_excluded_count") or 0),
        "production_snapshot_used_for_model": bool(
            audit.get("production_snapshot_used_for_model", False)
        ),
        "read_only": bool(audit.get("read_only", False)),
        "tracking_quality_status": quality.get("status"),
        "tracking_checks": f"{quality.get('checks_passed', 0)}/{quality.get('checks_total', 0)}",
        "tracking_charts": int(quality.get("chart_count") or 0),
        "deflated_sharpe_confidence": _number(dsr.get("confidence")),
    }
    train = model["splits"].get("train") or {}
    validation = model["splits"].get("validation") or {}
    test = model["splits"].get("test") or {}
    model["loss_attribution"] = (
        f"资金面监测质量为{model['robustness']['tracking_checks']}，但不等同于收益模型有效。"
        f"精确数据库存在{model['robustness']['exact_database_series']}条候选序列，"
        f"其中{model['robustness']['effective_training_series']}条形成可用周频历史。"
        f"训练、验证和封存测试夏普分别为{_number(train.get('sharpe')) or 0:.2f}、"
        f"{_number(validation.get('sharpe')) or 0:.2f}和{_number(test.get('sharpe')) or 0:.2f}。"
        "月末执行与真实货币ETF收益已降低收益口径偏差；验证期绝对收益仍为负。"
    )
    model["next_action"] = (
        "保持只读精确序列、发布滞后、两层滚动后验和月末执行主链。"
        "当前候选只作诊断，不因已观察测试期表现晋级；补齐Wind EDB和EPFR精确序列后，"
        "再用预声明的未来影子样本检验跨资金类别稳定性。"
    )


def _update_portfolio(model: dict[str, Any]) -> None:
    data = _read_json(APP_DIR / "data" / "portfolio_optimization_snapshot.json")
    if not data:
        data = _read_json(EVIDENCE_ROOT / "portfolio_attribution_final_20260726.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    selected = ((data.get("optimization") or {}).get("selected_spec") or {})
    model["champion"] = f"{selected.get('candidate_id', 'C188')} {selected.get('expected_return_method', 'risk_adjusted_trend')}"
    backtest = data.get("backtest") or {}
    selected_strategy = ((backtest.get("strategies") or {}).get("selected") or {})
    split_source = selected_strategy.get("metrics") or (
        (backtest.get("return_loss_attribution") or {}).get("splits") or {}
    )
    model["splits"] = {
        split: _metric(metrics)
        for split, metrics in split_source.items()
        if split in {"train", "validation", "test"}
    }
    promotion = backtest.get("promotion_gate") or {}
    model["gate"] = promotion.get("status") or model["gate"]
    model["robustness"] = {
        "pbo_passed": bool(promotion.get("pbo_passed")),
        "dsr_passed": bool(promotion.get("dsr_passed")),
        "quality_status": (data.get("quality") or {}).get("status"),
        "solver_benchmark": (data.get("optimization") or {}).get("solver_benchmark") or [],
    }

    train = model["splits"].get("train") or {}
    validation = model["splits"].get("validation") or {}
    test = model["splits"].get("test") or {}
    model["loss_attribution"] = (
        f"\u73b0\u91d1\u7b49\u4ef7\u7269\u4e0e\u4e45\u671f\u503a\u5238\u5df2\u5206\u5c42\u3002"
        f"\u8bad\u7ec3\u590f\u666e{_number(train.get('sharpe')) or 0:.2f}\uff0c"
        f"\u9a8c\u8bc1\u590f\u666e{_number(validation.get('sharpe')) or 0:.2f}\uff0c"
        f"\u5c01\u5b58\u6d4b\u8bd5\u590f\u666e{_number(test.get('sharpe')) or 0:.2f}\u3002"
        f"\u6d4b\u8bd5\u671f\u5e74\u5316\u8d85\u989d{(_number(test.get('annual_excess_return')) or 0) * 100:.2f}%\uff0c"
        "\u7ec4\u5408\u5b9a\u4f4d\u4e3a\u4f4e\u6ce2\u7edd\u5bf9\u6536\u76ca\u5019\u9009\uff0c\u4e0d\u7b49\u540c\u4e8e\u6307\u6570\u589e\u5f3a\u3002"
    )
    model["next_action"] = (
        "\u4fdd\u6301\u540e\u9a8c\u8bca\u65ad\u72b6\u6001\uff0c\u8865\u9f50\u65f6\u70b9\u5316\u57fa\u91d1\u5168\u96c6\u4e0e\u603b\u56de\u62a5\u590d\u6743\uff0c"
        "\u65b0\u589e12\u4e2a\u6708\u5f71\u5b50\u6837\u672c\u540e\u91cd\u65b0\u6821\u9a8c\u53bb\u504f\u590f\u666e\u3002"
    )


def _update_rotation(model: dict[str, Any]) -> None:
    data = _read_json(EVIDENCE_ROOT / "industry_report_momentum_v47_20260726.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    frequencies = ((data.get("industry") or {}).get("frequencies") or {})
    monthly, weekly = frequencies.get("monthly") or {}, frequencies.get("weekly") or {}
    model["champion"] = monthly.get("selected_candidate") or model["champion"]
    model["splits"] = {
        split: _metric(metrics)
        for split, metrics in (monthly.get("metrics") or {}).items()
        if split in {"train", "validation", "test"}
    }
    model["weekly_test"] = _metric((weekly.get("metrics") or {}).get("test"))


def _update_factor(model: dict[str, Any]) -> None:
    data = _read_json(EVIDENCE_ROOT / "factor_strategy_inverse_vol_v32_20260726.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    model["champion"] = (data.get("selection") or {}).get("best_validation_candidate") or model["champion"]
    model["splits"] = {
        ("validation" if split == "valid" else split): _metric(metrics)
        for split, metrics in (data.get("metrics") or {}).items()
        if split in {"train", "valid", "validation", "test"}
    }


def _update_kline(model: dict[str, Any]) -> None:
    multiscale = _read_json(APP_DIR / "data" / "kline_multiscale_expert_challenger.json")
    if multiscale:
        selected = multiscale.get("selected") or {}
        deployment = multiscale.get("deployment_selected") or {}
        result = ((multiscale.get("results") or {}).get(selected.get("universe")) or {})
        candidate = ((result.get("candidates") or {}).get(selected.get("candidate")) or {})
        metrics = candidate.get("metrics") or {}
        train = metrics.get("train") or {}
        valid = metrics.get("valid") or {}
        test = metrics.get("test") or {}

        pure = multiscale.get("pure_technical_model") or {}
        pure_selected = pure.get("selected") or {}
        pure_result = ((pure.get("results") or {}).get(pure_selected.get("universe")) or {})
        pure_candidate = ((pure_result.get("candidates") or {}).get(pure_selected.get("candidate")) or {})
        pure_metrics = pure_candidate.get("metrics") or {}
        pure_train = pure_metrics.get("train") or {}
        pure_valid = pure_metrics.get("valid") or {}
        pure_test = pure_metrics.get("test") or {}
        pure_guard = pure.get("release_guard") or {}
        pure_gates = pure_guard.get("gates") or {}
        pure_framework = pure.get("framework") or {}
        if isinstance(pure_framework, dict):
            pure_family_count = len(pure_framework.get("families") or [])
        elif isinstance(pure_framework, list):
            pure_family_count = len(pure_framework)
        else:
            pure_family_count = 0

        pure_version = pure.get("version") or "technical-signal-stack/1.0-broker-style"
        llm_version = multiscale.get("version") or model["engine"]
        model["engine"] = f"{pure_version} + {llm_version}"
        model["champion"] = deployment.get("candidate") or "\u6682\u65e0\u53ef\u90e8\u7f72\u7b56\u7565"
        model["gate"] = "research_diagnostic"
        model["metric_kind"] = "strategy"
        model["splits"] = {
            "train": _metric(train),
            "validation": _metric(valid),
            "test": _metric(test),
        }
        model["test_policy"] = "train_validation_select_sealed_test_release_only"
        model["robustness"] = {
            **(model.get("robustness") or {}),
            "technical_dual_model_summary": {
                "model_1": "\u7eaf\u6280\u672f\u4fe1\u53f7\u6808",
                "model_2": "LLM\u8bb0\u5fc6\u591a\u5468\u671f",
                "production_status": "\u672a\u53d1\u5e03\u4ea4\u6613\u7b56\u7565\uff1b\u4ec5\u7814\u7a76\u8bca\u65ad",
                "selection_uses_test": False,
            },
            "pure_technical_release_guard": {
                "status": pure.get("status"),
                "version": pure_version,
                "selection_uses_test": False,
                "accepted_by_train_validation": bool(pure_selected.get("accepted_by_train_validation")),
                "release_approved": bool(pure_guard.get("release_approved")),
                "deployment_candidate": (pure.get("deployment_selected") or {}).get("candidate"),
                "research_candidate": pure_selected.get("candidate"),
                "universe": pure_selected.get("universe"),
                "gates": pure_gates,
                "sealed_test": _metric(pure_test),
                "framework_family_count": pure_family_count,
            },
            "multiscale_release_guard": {
                "selection_uses_test": False,
                "accepted_by_train_validation": bool(selected.get("accepted_by_train_validation")),
                "release_approved": bool(deployment.get("release_approved")),
                "deployment_candidate": deployment.get("candidate"),
                "research_candidate": selected.get("candidate"),
                "execution_mode": None,
                "cost_rate_per_turnover": ((multiscale.get("integrity") or {}).get("cost_rate_per_turnover")),
                "signal_uses_close_or_earlier": bool(
                    (multiscale.get("integrity") or {}).get("signal_uses_close_or_earlier")
                ),
                "execution_is_next_trade_open": bool(
                    (multiscale.get("integrity") or {}).get("execution_is_next_trade_open")
                ),
            },
        }
        model["loss_attribution"] = (
            f"model_2 train Sharpe={_number(train.get('sharpe')) or 0:.2f}, "
            f"valid Sharpe={_number(valid.get('sharpe')) or 0:.2f}, "
            f"sealed test Sharpe={_number(test.get('sharpe')) or 0:.2f}; "
            f"model_1 pure technical train Sharpe={_number(pure_train.get('sharpe')) or 0:.2f}, "
            f"valid Sharpe={_number(pure_valid.get('sharpe')) or 0:.2f}, "
            f"sealed test Sharpe={_number(pure_test.get('sharpe')) or 0:.2f}. "
            "Both models are selected by train/validation only and remain research diagnostics because the sealed test release gates fail."
        )
        model["next_action"] = (
            "Keep factor-family diagnostics, LLM memory diagnostics, cost-aware backtests, and release gates. "
            "Only pre-declared factors or future samples may enter the next cycle; sealed-test retuning is prohibited."
        )
        return
    data = _read_json(EVIDENCE_ROOT / "kline_release_guard_000001" / "learned_kline_result.json")
    if data:
        model["engine"] = f"kline-memory/{data.get('version', '9.2')}"
        guard = data.get("no_degradation_guard") or {}
        model["champion"] = guard.get("selected_signal_chain") or model["champion"]
        model["gate"] = "observe_only" if guard.get("observe_only", True) else "research_candidate"
        model["splits"] = {
            ("validation" if split == "valid" else split): _metric(metrics)
            for split, metrics in (data.get("backtest_metrics") or {}).items()
            if split in {"train", "valid", "test"}
        }

    audit = _read_json(APP_DIR / "data" / "kline_cross_sectional_audit.json")
    if not audit:
        return
    candidates = audit.get("candidates") or []
    best_rejected = candidates[0] if candidates else {}
    failed_checks = [
        name
        for name, passed in (best_rejected.get("checks") or {}).items()
        if not passed
    ]
    model["engine"] = f"kline-memory/{audit.get('source_version', 'cross-sectional-factor-study/1.3')}"
    model["gate"] = "observe_only"
    model["test_policy"] = audit.get("selection_policy") or "train_validation_only_test_report_only"
    model["robustness"] = {
        **(model.get("robustness") or {}),
        "cross_sectional_audit": {
            "status": audit.get("status"),
            "candidate_count": audit.get("candidate_count", len(candidates)),
            "eligible_count": audit.get("eligible_count", 0),
            "selection_uses_test": bool(audit.get("selection_uses_test", False)),
            "best_rejected": {
                "universe": best_rejected.get("universe"),
                "frequency": best_rejected.get("frequency"),
                "score": best_rejected.get("score"),
                "failed_checks": failed_checks,
                "train": best_rejected.get("train") or {},
                "validation": best_rejected.get("validation") or {},
                "test_report_only": best_rejected.get("test_report_only") or {},
            },
        },
    }
    model["loss_attribution"] = (
        f"Audited {audit.get('candidate_count', len(candidates))} cross-stock and frequency candidates; "
        f"{audit.get('eligible_count', 0)} passed train and validation simultaneously. "
        "Positive RankIC has not converted stably into cost-adjusted portfolio returns."
    )
    model["next_action"] = (
        "Keep single-stock pattern chains and cross-sectional candidates in observe-only mode. "
        "Only candidates positive in both train and validation can enter a one-shot sealed test."
    )

def _update_index(model: dict[str, Any]) -> None:
    data = _read_json(APP_DIR / "data" / "index_enhancement_snapshot.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    audit = ((data.get("champion_audit") or {}).get("CSI800_ENH") or {})
    if not audit.get("champion"):
        return
    model["champion"] = str(audit["champion"])
    model["splits"] = {
        split: _metric(metrics)
        for split, metrics in (audit.get("splits") or {}).items()
        if split in {"train", "validation", "test"}
    }
    test = model["splits"].get("test") or {}
    test_sharpe = _number(test.get("sharpe"))
    test_ir = _number(test.get("information_ratio"))
    model["gate"] = "review" if test_sharpe is None or test_ir is None or test_sharpe <= 0 or test_ir <= 0 else "conditional"
    model["loss_attribution"] = (
        "冠军仅由训练和验证期的夏普、信息比率及回撤稳健排序选出。"
        f"首次封存测试夏普{test_sharpe:.2f}、信息比率{test_ir:.2f}，显示2023年后发生显著状态失效。"
    )
    shadow = data.get("shadow_challenger_audit") or {}
    selected_shadow = str(shadow.get("selected_shadow") or "")
    shadow_candidates = shadow.get("candidates") or []
    selected_evidence = next(
        (
            candidate
            for candidate in shadow_candidates
            if candidate.get("model") == selected_shadow
        ),
        {},
    )
    shadow_splits = selected_evidence.get("splits") or {}
    shadow_validation = _metric(shadow_splits.get("validation"))
    shadow_test = _metric(shadow_splits.get("test"))
    model["robustness"] = {
        **(model.get("robustness") or {}),
        "post_test_shadow": {
            "model": selected_shadow,
            "promotion_eligible": False,
            "selection_uses_test": False,
            "validation": shadow_validation,
            "test_diagnostic": shadow_test,
        },
    }
    if selected_shadow:
        model["loss_attribution"] += (
            f" 新增影子模型{selected_shadow}在验证期IR为"
            f"{_number(shadow_validation.get('information_ratio')) or 0:.2f}，"
            f"已观测期诊断IR为{_number(shadow_test.get('information_ratio')) or 0:.2f}。"
            "行业和风格约束修复后仍未恢复Alpha，收益损失已定位到底层信号迁移。"
        )
    model["next_action"] = "保持封存测试不变，下一轮重新声明状态条件化Alpha和风险预算候选；中证2000在建立训练、验证分段前禁止选模。"



    regime = _read_json(
        APP_DIR / "data" / "index_regime_core_satellite_diagnostics.json"
    )
    if regime:
        selected_model = str(regime.get("selected_candidate") or "")
        selected = regime.get("selected") or {}
        summary = selected.get("summary") or {}
        split_metrics = summary.get("split_metrics") or {}
        validation = _metric(split_metrics.get("valid"))
        test_diagnostic = _metric(split_metrics.get("test"))
        model["engine"] = regime.get("engine_version") or model["engine"]
        model["robustness"] = {
            **(model.get("robustness") or {}),
            "post_test_shadow": {
                "model": selected_model,
                "mandate": summary.get("mandate"),
                "promotion_eligible": False,
                "selection_uses_test": False,
                "candidate_count": len(regime.get("candidate_evaluations") or []),
                "validation": validation,
                "test_diagnostic": test_diagnostic,
                "average_tracking_error": summary.get("average_tracking_error"),
                "average_one_way_turnover": summary.get("average_one_way_turnover"),
            },
        }
        model["loss_attribution"] = (
            "\u65e7\u51a0\u519b\u5c06\u6210\u5206\u80a1\u7b49\u6743\u5747\u503c\u5f53\u4f5c\u6307\u6570\u57fa\u51c6\uff0c\u4e14\u4ee5\u5c11\u91cf\u80a1\u7968\u7684\u7edd\u5bf9\u6536\u76ca\u6a21\u578b\u627f\u62c5\u6307\u6570\u589e\u5f3a\u804c\u80fd\u3002"
            "2024\u81f32026\u5e74\u7684\u4e3b\u8981\u635f\u5931\u6765\u81ea\u57fa\u51c6\u8d1d\u5854\u672a\u8ddf\u4e0a\u4e0e\u957f\u7a97\u53e3\u63a9\u76d6\u8fd1\u671f\u56e0\u5b50\u8870\u51cf\u3002"
            f"\u6838\u5fc3\u536b\u661f\u5019\u9009{selected_model}\u9a8c\u8bc1\u671fIR\u4e3a"
            f"{_number(validation.get('information_ratio')) or 0:.2f}\uff0c"
            f"\u5c01\u5b58\u6d4b\u8bd5IR\u4e3a{_number(test_diagnostic.get('information_ratio')) or 0:.2f}\u3002"
        )
        model["next_action"] = (
            "\u4fdd\u7559\u5168\u4ed3\u57fa\u51c6\u6838\u5fc3\u548c\u8d1d\u53f6\u65af\u4e3b\u52a8\u9884\u7b97\uff0c\u7ee7\u7eed\u4ee5\u65b0\u589e\u524d\u77bb\u6570\u636e\u76d1\u6d4b2025\u5e74\u540e\u8d44\u91d1\u56e0\u5b50\u548c\u4ef7\u503c\u56e0\u5b50\u7684\u7a33\u5b9a\u6027\uff1b"
            "\u5c01\u5b58\u6d4b\u8bd5\u4e0d\u7528\u4e8e\u518d\u9009\u6a21\u6216\u8c03\u53c2\u3002"
        )


def build_model_governance() -> dict[str, Any]:
    models = copy.deepcopy(_base_models())
    _update_asset(models["asset_allocation"])
    _update_liquidity(models["liquidity_tracking"])
    _update_portfolio(models["portfolio_optimization"])
    _update_rotation(models["industry_rotation"])
    _update_factor(models["factor_laboratory"])
    _update_kline(models["kline_memory"])
    _update_index(models["index_enhancement"])
    gate_counts: dict[str, int] = {}
    for model in models.values():
        gate = str(model.get("gate") or "unknown")
        gate_counts[gate] = gate_counts.get(gate, 0) + 1
    target_met = [
        key
        for key, model in models.items()
        if _number(((model.get("splits") or {}).get("test") or {}).get("sharpe")) is not None
        and float(((model.get("splits") or {}).get("test") or {}).get("sharpe")) >= 1.5
    ]
    return {
        "status": "ok",
        "schema_version": "model-governance/1.0",
        "release": RELEASE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "policy": {
            "selection": "train shortlist → validation select → sealed test report only",
            "target": "Sharpe 1.5 is an aspiration, not a promotion override",
            "prohibited": "test-set retuning, fabricated returns, zero-position success labels",
        },
        "summary": {
            "model_count": len(models),
            "gate_counts": gate_counts,
            "report_only_test_sharpe_at_least_1_5": target_met,
        },
        "models": models,
        "research_basis": [
            {"name": "国泰海通：大类资产配置展望", "url": "https://www.htsec.com/jfimg/colimg/upload/20230105/33841672893185135.pdf", "applied_to": "周期状态、利率与流动性扩展"},
            {"name": "\u56fd\u6cf0\u6d77\u901a\uff1a\u98ce\u63a7\u6a21\u578b\u8fd8\u6709\u5fc5\u8981\u5417", "url": "https://www.htsec.com/jfimg/colimg/upload/20230821/1692578595183004217.pdf", "applied_to": "\u6301\u4ed3\u6570\u3001\u8ddf\u8e2a\u8bef\u5dee\u4e0e\u6362\u624b\u7387\u7684\u8054\u5408\u8bc4\u4f30"},
            {"name": "\u534e\u6cf0\u671f\u8d27\uff1a\u5546\u54c1\u7b56\u7565\u6307\u6570\u7684\u6709\u6548\u524d\u6cbf", "url": "https://htfc.com/wz_upload/png_upload/20220322/1647908605004d4f3a2.pdf", "applied_to": "\u91cd\u91c7\u6837\u3001\u6709\u6548\u524d\u6cbf\u8fde\u7eed\u6027\u4e0e\u53c2\u6570\u654f\u611f\u6027"},
            {"name": "国泰海通：多因子模型因子暴露与交易成本", "url": "https://www.htsec.com/jfimg/colimg/upload/20220815/3051660530146420.pdf", "applied_to": "因子中性化、暴露和换手归因"},
            {"name": "国泰海通：多模态图神经网络选股", "url": "https://www.htsec.com/jfimg/colimg/upload/20240515/1715749972757048871.pdf", "applied_to": "截面关系与状态表征挑战模型"},
            {"name": "国泰海通：市场状态下的因子配置", "url": "https://www.htsec.com/jfimg/colimg/upload/20240710/1720588848500078657.pdf", "applied_to": "状态迁移和因子衰减诊断"},
            {"name": "东北证券：A股风险模型研究", "url": "https://www.nesc.cn/timerfiles/upload/report/2024/01/29/15813745.pdf", "applied_to": "风险暴露、协方差和主动风险预算"},
        ],
    }
