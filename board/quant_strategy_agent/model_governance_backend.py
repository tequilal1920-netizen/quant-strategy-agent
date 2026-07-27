"""Read-only train/validation/sealed-test evidence contract for the dashboard."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1] if APP_DIR.name == "quant_strategy_agent" else APP_DIR
EVIDENCE_ROOT = PROJECT_ROOT / "output" / "model_improvement"
RELEASE = "2026.07.27-scoped-controls-ai-resilience-r21.1"


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
            source.get("annual_excess_return", source.get("annual_excess"))
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
            "engine": "portfolio-optimizer/2.3-return-loss-attribution",
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


def _update_asset(model: dict[str, Any]) -> None:
    data = _read_json(EVIDENCE_ROOT / "asset_empirical_uncertainty_v45_20260726.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    selected = data.get("selected_spec") or {}
    model["champion"] = f"{selected.get('id', 'B06')} {selected.get('family', 'posterior')}"
    absolute, active = data.get("metrics_by_split") or {}, data.get("active_metrics_by_split") or {}
    model["splits"] = {}
    for split in ("train", "validation", "test"):
        metric = _metric(absolute.get(split))
        active_metric = _metric(active.get(split))
        for key in ("information_ratio", "annual_excess_return"):
            if key in active_metric:
                metric[key] = active_metric[key]
        model["splits"][split] = metric


def _update_portfolio(model: dict[str, Any]) -> None:
    data = _read_json(EVIDENCE_ROOT / "portfolio_attribution_final_20260726.json")
    if not data:
        return
    model["engine"] = data.get("engine_version") or model["engine"]
    selected = ((data.get("optimization") or {}).get("selected_spec") or {})
    model["champion"] = f"{selected.get('candidate_id', 'C188')} {selected.get('expected_return_method', 'risk_adjusted_trend')}"
    attribution = ((data.get("backtest") or {}).get("return_loss_attribution") or {})
    model["splits"] = {
        split: _metric(metrics)
        for split, metrics in (attribution.get("splits") or {}).items()
        if split in {"train", "validation", "test"}
    }


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
        f"跨股票池与频率共审计{audit.get('candidate_count', len(candidates))}组候选，"
        f"训练和验证同时通过的策略为{audit.get('eligible_count', 0)}组。"
        "分数最高的中证2000月频候选验证期夏普1.83，但训练期夏普为-0.44，"
        "封存测试年化超额为-8.15%。正RankIC未能稳定转化为扣除交易路径后的收益，"
        "主要损失来自高换手和因子收益向组合收益的映射不稳定。"
    )
    model["next_action"] = (
        "保持单股形态链和横截面候选仅观察。下一轮在训练期重建形态标签、"
        "分市场状态评估信号寿命，并用连续权重和成交成本预算形成候选；"
        "仅允许训练与验证均为正的策略进入一次性封存测试。"
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


def build_model_governance() -> dict[str, Any]:
    models = copy.deepcopy(_base_models())
    _update_asset(models["asset_allocation"])
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
            {"name": "国泰海通：多因子模型因子暴露与交易成本", "url": "https://www.htsec.com/jfimg/colimg/upload/20220815/3051660530146420.pdf", "applied_to": "因子中性化、暴露和换手归因"},
            {"name": "国泰海通：多模态图神经网络选股", "url": "https://www.htsec.com/jfimg/colimg/upload/20240515/1715749972757048871.pdf", "applied_to": "截面关系与状态表征挑战模型"},
            {"name": "国泰海通：市场状态下的因子配置", "url": "https://www.htsec.com/jfimg/colimg/upload/20240710/1720588848500078657.pdf", "applied_to": "状态迁移和因子衰减诊断"},
            {"name": "东北证券：A股风险模型研究", "url": "https://www.nesc.cn/timerfiles/upload/report/2024/01/29/15813745.pdf", "applied_to": "风险暴露、协方差和主动风险预算"},
        ],
    }
