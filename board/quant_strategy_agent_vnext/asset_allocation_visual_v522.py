"""Schema-5.2.2-only evidence blocks for the existing four-panel UI."""
from __future__ import annotations

import math
from typing import Any


CYCLE_LABELS = {
    "pring": "普林格周期",
    "kitchin": "基钦周期",
    "juglar": "朱格拉周期",
    "merrill": "美林时钟",
    "kondratieff": "康波周期",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _date(value: Any) -> Any:
    text = str(value or "")
    return f"{text[:4]}-{text[4:6]}" if text.isdigit() and len(text) == 6 else value


def _table(columns: list[tuple[str, str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "columns": [
            {"key": key, "label": label, "format": value_format}
            for key, label, value_format in columns
        ],
        "rows": rows,
    }


def _trace(
    name: str,
    x: list[Any],
    y: list[Any],
    *,
    kind: str = "scatter",
    color: str | None = None,
    text: list[Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": kind,
        "mode": "lines" if kind == "scatter" else "",
        "name": name,
        "x": x,
        "y": y,
        "axis": "y",
    }
    if color:
        result["color"] = color
    if text is not None:
        result["text"] = text
    return result


def _metric_rows(metrics: Any) -> list[dict[str, Any]]:
    if not isinstance(metrics, dict):
        return []
    rows: list[dict[str, Any]] = []
    for split in ("train", "validation", "test"):
        item = metrics.get(split)
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "split": split,
                "annual_return": _number(item.get("annual_return")),
                "annual_excess_return": _number(item.get("annual_excess_return")),
                "sharpe": _number(item.get("sharpe")),
                "information_ratio": _number(item.get("information_ratio")),
                "max_drawdown": _number(item.get("max_drawdown")),
            }
        )
    return rows


def _probability_text(probabilities: Any) -> str:
    if not isinstance(probabilities, dict) or not probabilities:
        return "--"
    ordered = sorted(
        probabilities.items(), key=lambda item: _number(item[1]), reverse=True
    )
    return "；".join(
        f"{name} {_number(value) * 100:.1f}%" for name, value in ordered
    )


def _weight_text(data: dict[str, Any], weights: Any) -> str:
    if not isinstance(weights, dict):
        return "--"
    labels = data.get("asset_labels") or {}
    return " / ".join(
        f"{labels.get(asset, asset)}{_number(weights.get(asset)) * 100:.1f}%"
        for asset in data.get("asset_order") or []
    )


def _allocation_metadata(allocation: Any) -> dict[str, Any]:
    if not isinstance(allocation, dict):
        return {}
    metadata = allocation.get("metadata")
    return metadata if isinstance(metadata, dict) else allocation


def _cycle_mapping(data: dict[str, Any], cycle: str) -> str:
    audit = (
        ((data.get("cycle_factor_availability") or {}).get("cycles") or {}).get(
            cycle
        )
        or {}
    )
    if cycle == "kondratieff" and not bool(audit.get("eligible_for_views")):
        return "未准入，门禁强制零贡献"
    relative = ((data.get("allocations") or {}).get("benchmark_relative") or {})
    views = _allocation_metadata(relative).get("cycle_views") or {}
    labels = views.get("view_labels") or []
    values = (views.get("cycle_contributions") or {}).get(cycle) or []
    if not labels or len(labels) != len(values):
        return "无可审计映射"
    return "；".join(
        f"{label}={_number(value) * 100:+.2f}%"
        for label, value in zip(labels, values)
    )


def _cycle_rows(
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current = ((data.get("allocations") or {}).get("current_cycle") or {})
    cycles = current.get("cycles") or {}
    availability = data.get("cycle_factor_availability") or {}
    audits = availability.get("cycles") or {}
    admitted = set(availability.get("admitted_cycles") or [])
    production_admitted = set(
        availability.get("production_admitted_cycles") or []
    )
    registry = data.get("cycle_factor_registry") or []
    if not isinstance(registry, list):
        registry = []
    factor_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    shared_source = str(current.get("source") or "--")

    for cycle, cycle_label in CYCLE_LABELS.items():
        state = cycles.get(cycle) or {}
        audit = audits.get(cycle) or {}
        evidence = state.get("factor_evidence") or {}
        observed = audit.get("observed_fields") or evidence.get("observed_fields") or {}
        eligible = bool(
            audit.get("eligible_for_views", state.get("eligible_for_views"))
        )
        shadow_eligible = bool(
            audit.get(
                "eligible_for_shadow_views",
                state.get("eligible_for_shadow_views", eligible),
            )
        )
        production_eligible = bool(
            audit.get(
                "eligible_for_production_views",
                state.get("eligible_for_production_views", False),
            )
        )
        enters_shadow = cycle in admitted and eligible and shadow_eligible
        enters_production = (
            cycle in production_admitted and eligible and production_eligible
        )
        view_scope = str(
            audit.get("view_scope")
            or state.get("view_scope")
            or (
                "production"
                if enters_production
                else "shadow_only"
                if enters_shadow
                else "not_admitted"
            )
        )
        probabilities = state.get("probabilities") or {}
        confidence = _number(state.get("confidence"))
        stage = str(state.get("state_name") or state.get("state") or "--")
        duration = state.get("duration_model") or audit.get("duration_model") or {}
        duration_text = (
            f"{_number(duration.get('minimum_months')):.0f}/"
            f"{_number(duration.get('expected_months')):.0f}/"
            f"{_number(duration.get('maximum_months')):.0f}月"
            if duration
            else "未参数化"
        )
        method = str(
            state.get("method")
            or duration.get("method")
            or evidence.get("admission_reason")
            or audit.get("admission_reason")
            or "--"
        )
        data_status = str(
            audit.get("data_status") or state.get("data_status") or "--"
        )
        source = str(evidence.get("source") or shared_source)
        if cycle == "kondratieff" and not eligible and not observed:
            source = "--（无可用独立长周期样本）"
        mapping = _cycle_mapping(data, cycle)
        if enters_production:
            allocation_scope = "生产配置"
        elif enters_shadow:
            allocation_scope = (
                "仅影子研究（非D3/非生产）"
                if "not_D3" in data_status or data_status.startswith("D2_")
                else "仅影子研究（非生产）"
            )
        else:
            allocation_scope = "否（零贡献）"
        summary_rows.append(
            {
                "cycle": cycle_label,
                "current_stage": stage,
                "stage_probability": max(
                    (_number(value) for value in probabilities.values()),
                    default=confidence,
                ),
                "probability_distribution": _probability_text(probabilities),
                "confidence": confidence,
                "judgment_method": method,
                "duration": duration_text,
                "data_status": data_status,
                "view_scope": view_scope,
                "asset_mapping": mapping,
                "enters_shadow_allocation": enters_shadow,
                "enters_production_allocation": enters_production,
                "enters_allocation": allocation_scope,
            }
        )

        specifications = [
            row for row in registry if str(row.get("cycle") or "") == cycle
        ]
        if cycle == "pring" and not specifications:
            specifications = [
                {
                    "pillar": "三市场牛熊",
                    "factor_key": f"{asset}_bull_probability",
                    "economic_role": f"{asset}总收益的风险调整多期限牛熊概率",
                    "frequency": "monthly",
                    "required_for_admission": True,
                    "observed": asset in (state.get("market_probabilities") or {}),
                    "field": (
                        (
                            ((data.get("asset_proxies") or {}).get(asset) or {}).get(
                                "execution_code"
                            )
                            or "--"
                        )
                        + "（D2执行代理）"
                    ),
                }
                for asset in ("bond", "equity", "commodity")
            ]
        if cycle == "kondratieff" and not specifications:
            specifications = [
                {
                    "pillar": "独立长周期样本",
                    "factor_key": "independent_40_60_year_cycles",
                    "economic_role": "多个独立完整40—60年样本；不足时禁止参数化与资产映射",
                    "frequency": "structural",
                    "required_for_admission": True,
                    "observed": False,
                }
            ]
        for specification in specifications:
            factor_key = str(specification.get("factor_key") or "--")
            observed_field = observed.get(factor_key)
            observed_flag = bool(
                specification.get("observed", observed_field not in (None, ""))
            )
            if observed_flag and enters_production:
                factor_state = "生产准入"
            elif observed_flag and enters_shadow:
                factor_state = (
                    "D2影子准入（非D3/非生产）"
                    if "not_D3" in data_status or data_status.startswith("D2_")
                    else "影子准入（非生产）"
                )
            elif observed_flag:
                factor_state = "已观测但整周期未准入"
            else:
                factor_state = "缺失/未验证"
            factor_rows.append(
                {
                    "cycle": cycle_label,
                    "pillar": specification.get("pillar") or "--",
                    "factor": factor_key,
                    "field": observed_field
                    or specification.get("field")
                    or ",".join(specification.get("accepted_fields") or [])
                    or "--",
                    "frequency": specification.get("frequency") or "--",
                    "source": source,
                    "required": "必需"
                    if specification.get("required_for_admission")
                    else "辅助",
                    "pit_status": factor_state,
                    "data_status": data_status,
                    "view_scope": view_scope,
                    "economic_role": specification.get("economic_role") or "--",
                    "stage": stage,
                    "asset_mapping": mapping,
                    "enters_shadow_allocation": enters_shadow,
                    "enters_production_allocation": enters_production,
                    "enters_allocation": allocation_scope,
                }
            )
    return factor_rows, summary_rows


def _model_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    allocations = data.get("allocations") or {}
    relative = allocations.get("benchmark_relative") or {}
    relative_meta = _allocation_metadata(relative)
    risk_parity = allocations.get("risk_parity") or {}
    risk_budget = allocations.get("macro_risk_budget") or {}
    rp_meta = risk_parity.get("metadata") or {}
    rb_meta = risk_budget.get("metadata") or {}
    relative_rb = relative_meta.get("risk_budget") or {}
    bl = relative_meta.get("black_litterman") or {}
    optimizer = relative_meta.get("optimizer") or {}
    macro = (
        ((data.get("macro_factor_risk_audit") or {}).get("by_model_version") or {}).get(
            "benchmark_relative"
        )
        or {}
    )
    macro_factors = ", ".join(macro.get("factor_names") or []) or "--"
    macro_exposure = macro.get("factor_exposure") or {}
    objective_terms = optimizer.get("objective_terms") or {}
    slacks = optimizer.get("constraint_slack") or {}
    rows: list[dict[str, Any]] = []

    def add(model: str, status: str, values: list[tuple[str, str]]) -> None:
        rows.extend(
            {"model": model, "step": step, "evidence": evidence, "status": status}
            for step, evidence in values
        )

    add(
        "宏观因子风险模型",
        str(macro.get("status") or "--"),
        [
            ("输入", f"四资产月收益＋{macro_factors}；PIT准入决定宏观混合权重"),
            (
                "计算",
                str(
                    macro.get("formula")
                    or "Σ=ρ(BFB′+D)+(1−ρ)Σstat；Euler分解因子风险"
                ),
            ),
            (
                "约束",
                f"PIT未通过时ρ强制为0；当前ρ={_number(macro.get('macro_blend_weight')):.3f}",
            ),
            (
                "输出",
                "；".join(
                    f"{key}={_number(value):+.3f}"
                    for key, value in macro_exposure.items()
                )
                or "无有效因子暴露",
            ),
            (
                "最终作用",
                str(
                    macro.get("production_interpretation")
                    or "形成风险解释与协方差混合；不绕过PIT门"
                ),
            ),
        ],
    )
    add(
        "严格风险平价（ERC）",
        str(rp_meta.get("status") or "--"),
        [
            ("输入", "冻结协方差矩阵＋四资产等风险预算b=(25%,25%,25%,25%)"),
            (
                "计算",
                "求解 wᵢ(Σw)ᵢ=bᵢ·w′Σw；Newton对数障碍法严格校准Euler风险贡献",
            ),
            ("约束", "w≥0，Σw=1；协方差先做PSD投影；不得用等资本权重替代ERC"),
            ("输出", _weight_text(data, risk_parity.get("weights"))),
            (
                "最终作用",
                "提供等风险基线；最大风险预算误差="
                f"{_number((rp_meta.get('diagnostics') or {}).get('maximum_budget_error')):.3g}",
            ),
        ],
    )
    add(
        "约束风险预算",
        str(rb_meta.get("status") or relative_rb.get("status") or "--"),
        [
            ("输入", "战略等风险基线＋已准入周期概率＋冻结协方差＋上一期权重"),
            ("计算", "Richard–Roncalli约束对数障碍：RCᵢ/ΣRC≈目标风险预算bᵢ"),
            (
                "约束",
                "多空边界、预算和为1、换手上限及激活约束="
                + ",".join(rb_meta.get("active_constraints") or []),
            ),
            ("输出", _weight_text(data, risk_budget.get("weights"))),
            (
                "最终作用",
                "作为BL均衡先验与最终优化锚；边界激活时保留预算误差和影子价格",
            ),
        ],
    )
    add(
        "稳健Black–Litterman",
        str((bl.get("diagnostics") or {}).get("status") or "--"),
        [
            (
                "输入",
                "风险预算先验π=δΣw、联合周期观点P/q与完整非对角Ω；"
                f"观点数={len(bl.get('q') or [])}",
            ),
            (
                "计算",
                "μBL=[(τΣ)⁻¹+P′Ω⁻¹P]⁻¹[(τΣ)⁻¹π+P′Ω⁻¹q]；保留观点误差相关性",
            ),
            (
                "约束",
                "60/15/10/15政策基准仅进入高低配版；主动份额、跟踪误差、换手和资产边界同时约束",
            ),
            (
                "输出",
                "后验月收益="
                + ",".join(
                    f"{_number(value):+.4f}" for value in bl.get("posterior_mean") or []
                ),
            ),
            (
                "最终作用",
                "后验均值进入成本感知优化；"
                f"预期收益项={_number(objective_terms.get('expected_return')):.6f}，"
                f"最大违约={_number(slacks.get('max_violation')):.2g}",
            ),
        ],
    )
    return rows


def _history_traces(data: dict[str, Any]) -> list[dict[str, Any]]:
    history = data.get("cycle_history") or []
    traces: list[dict[str, Any]] = []
    if not isinstance(history, list):
        return traces
    for cycle, label in CYCLE_LABELS.items():
        x: list[Any] = []
        y: list[float] = []
        text: list[str] = []
        for row in history:
            state = ((row.get("cycles") or {}).get(cycle) or {})
            probabilities = state.get("probabilities") or {}
            confidence = max(
                (_number(value) for value in probabilities.values()),
                default=_number(state.get("confidence")),
            )
            if row.get("month") in (None, ""):
                continue
            x.append(_date(row.get("month")))
            y.append(confidence)
            text.append(str(state.get("state_name") or state.get("state") or "--"))
        if x:
            traces.append(_trace(label, x, y, text=text))
    return traces


def _strategy_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    allocations = data.get("allocations") or {}
    strategies = (data.get("backtest") or {}).get("strategies") or {}
    rows: list[dict[str, Any]] = []
    for key, label, role in [
        ("strategic_benchmark", "60/15/10/15战略基准", "优化与主动偏离锚"),
        ("benchmark_relative", "基准高低配版", "已按夏普目标授权服务"),
        (
            "absolute_no_benchmark",
            "无基准版",
            "不把政策权重放入模型；仅作绝对研究对照",
        ),
    ]:
        weights = (allocations.get(key) or {}).get("weights") or {}
        rows.append(
            {
                "kind": "当前权重",
                "model_version": key,
                "portfolio": label,
                "split": "current",
                "equity": weights.get("equity"),
                "bond": weights.get("bond"),
                "gold": weights.get("gold"),
                "commodity": weights.get("commodity"),
                "annual_return": None,
                "sharpe": None,
                "max_drawdown": None,
                "annual_excess_return": None,
                "information_ratio": None,
                "role": role,
            }
        )
    for key, label, role in [
        (
            "strategic_benchmark",
            "60/15/10/15战略基准",
            "政策锚绩效；不作为主净值图基准线",
        ),
        (
            "benchmark_relative",
            "基准高低配版",
            "主动收益始终相对60/15/10/15战略基准",
        ),
        (
            "absolute_no_benchmark",
            "无基准版",
            "超额/IR仅为事后政策基准比较，不进入优化",
        ),
    ]:
        for metric in _metric_rows((strategies.get(key) or {}).get("metrics")):
            rows.append(
                {
                    "kind": "分段绩效",
                    "model_version": key,
                    "portfolio": label,
                    "split": metric.get("split"),
                    "equity": None,
                    "bond": None,
                    "gold": None,
                    "commodity": None,
                    "annual_return": metric.get("annual_return"),
                    "sharpe": metric.get("sharpe"),
                    "max_drawdown": metric.get("max_drawdown"),
                    "annual_excess_return": metric.get("annual_excess_return"),
                    "information_ratio": metric.get("information_ratio"),
                    "role": role,
                }
            )
    decision_sets = data.get("asset_decisions") or {}
    strength_summaries = data.get("current_strength_summary") or {}
    labels = data.get("asset_labels") or {}
    for model_version, portfolio_label in (
        ("benchmark_relative", "基准高低配版"),
        ("absolute_no_benchmark", "无基准版"),
    ):
        decisions = decision_sets.get(model_version) or {}
        strength_rows = (
            (
                _allocation_metadata(allocations.get(model_version) or {}).get(
                    "asset_strength"
                )
                or {}
            ).get("rows")
            or {}
        )
        summary = strength_summaries.get(model_version) or {}
        strongest = [
            labels.get(asset, asset)
            for asset in summary.get("strongest_assets") or []
        ]
        weakest = [
            labels.get(asset, asset)
            for asset in summary.get("weakest_assets") or []
        ]
        tie_policy = (
            f"{portfolio_label}：最强="
            + ",".join(strongest)
            + "；最弱="
            + ",".join(weakest)
            + "；相同综合分在1e-12内共享名次；强弱解释输入，不按名次机械分配权重"
        )
        for asset in data.get("asset_order") or []:
            decision = {
                **(strength_rows.get(asset) or {}),
                **(decisions.get(asset) or {}),
            }
            signals = []
            for signal in decision.get("input_signals") or []:
                if isinstance(signal, dict):
                    name = (
                        signal.get("name")
                        or signal.get("label")
                        or signal.get("id")
                    )
                    value = signal.get("value")
                    signals.append(
                        f"{name}={_number(value):+.4f}"
                        if name
                        else f"{_number(value):+.4f}"
                    )
                elif signal not in (None, ""):
                    signals.append(str(signal))
            rows.append(
                {
                    "kind": "资产强弱",
                    "model_version": model_version,
                    "portfolio": portfolio_label,
                    "split": "current",
                    "asset": labels.get(asset, asset),
                    "strength_rank": decision.get("strength_rank"),
                    "strength_label": decision.get("strength_label_cn"),
                    "composite_strength": decision.get("composite_strength"),
                    "active_weight": decision.get("active_weight"),
                    "decision_summary": decision.get("decision_summary_cn") or "--",
                    "input_signals": "；".join(signals) or "--",
                    "tie_assets": ",".join(
                        labels.get(item, item)
                        for item in decision.get("strength_tied_assets") or []
                    )
                    or "--",
                    "equity": None,
                    "bond": None,
                    "gold": None,
                    "commodity": None,
                    "annual_return": None,
                    "sharpe": None,
                    "max_drawdown": None,
                    "annual_excess_return": None,
                    "information_ratio": None,
                    "role": tie_policy,
                }
            )
    return rows


def build(
    data: dict[str, Any],
    metrics: list[dict[str, Any]] | None = None,
    page: str = "strategy",
) -> dict[str, Any]:
    """Return the existing four visual blocks without introducing new CSS."""
    del metrics, page
    factor_rows, cycle_rows = _cycle_rows(data)
    allocations = data.get("allocations") or {}
    assets = data.get("asset_order") or []
    labels = data.get("asset_labels") or {}

    risk_sources = [
        (
            "严格ERC（独立诊断）",
            "risk_parity",
            allocations.get("risk_parity") or {},
            "diagnostic_only",
            "独立等风险诊断，不直接改变推荐权重",
        ),
        (
            "相对版约束风险预算",
            "macro_risk_budget",
            allocations.get("macro_risk_budget") or {},
            "benchmark_relative",
            "相对版政策基准与风险预算双锚中的风险预算腿",
        ),
        (
            "无基准版BL后成本优化组合",
            "robust_bl",
            allocations.get("robust_bl") or {},
            "absolute_no_benchmark",
            "无基准版风险预算先验经BL与成本优化后的组合",
        ),
        (
            "相对版BL后成本优化组合",
            "benchmark_relative",
            allocations.get("benchmark_relative") or {},
            "benchmark_relative",
            "政策基准先验经BL、风险预算双锚与成本优化后的组合",
        ),
    ]
    model_traces: list[dict[str, Any]] = []
    for name, source_key, model, expected_version, diagnostic_role in risk_sources:
        metadata = _allocation_metadata(model)
        model_version = str(
            metadata.get("model_version")
            or (metadata.get("model_spec") or {}).get("model_version")
            or expected_version
        )
        trace = _trace(
            name,
            [labels.get(asset, asset) for asset in assets],
            [
                _number((model.get("risk_contribution") or {}).get(asset))
                for asset in assets
            ],
            kind="bar",
        )
        trace.update(
            {
                "source_allocation_key": source_key,
                "model_version": model_version,
                "diagnostic_role": diagnostic_role,
            }
        )
        model_traces.append(trace)

    strategies = (data.get("backtest") or {}).get("strategies") or {}
    nav_traces: list[dict[str, Any]] = []
    for key, label, color in [
        ("benchmark_relative", "基准高低配版", "#163d7a"),
        ("absolute_no_benchmark", "无基准版", "#a61b1b"),
        ("equal_weight_25", "四资产等权（仅净值展示）", "#98a2b3"),
    ]:
        nav_rows = (strategies.get(key) or {}).get("nav") or []
        x: list[Any] = []
        y: list[float] = []
        for row in nav_rows:
            value = row.get("nav")
            if row.get("month") in (None, ""):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                x.append(_date(row.get("month")))
                y.append(number)
        if y:
            nav_traces.append(_trace(label, x, y, color=color))

    deployment = data.get("deployment_decision") or {}
    service_note = (
        "基准高低配版已按用户明确夏普目标授权服务；稳健性、D3/PIT与未来独立样本"
        "观察项单列，不改写已实现绩效。"
    )
    return {
        "descriptive": {
            "title": "五周期因子、PIT状态与资产映射",
            "note": (
                "逐因子保留真实字段、来源、缺失和准入状态；未通过PIT/样本门的周期"
                "保持零配置贡献，不以代理值补齐。"
            ),
            "table": _table(
                [
                    ("cycle", "周期", "text"),
                    ("pillar", "支柱", "text"),
                    ("factor", "因子", "text"),
                    ("field", "真实字段/候选字段", "text"),
                    ("frequency", "频率", "text"),
                    ("source", "实际来源", "text"),
                    ("required", "准入要求", "text"),
                    ("pit_status", "因子/PIT状态", "status"),
                    ("data_status", "周期数据状态", "status"),
                    ("view_scope", "准入范围", "status"),
                    ("enters_shadow_allocation", "影子研究", "status"),
                    ("enters_production_allocation", "生产配置", "status"),
                    ("economic_role", "经济含义", "text"),
                    ("stage", "当前阶段", "text"),
                    ("asset_mapping", "资产映射", "text"),
                    ("enters_allocation", "进入配置", "status"),
                ],
                factor_rows,
            ),
            "chart": {
                "title": "五周期当前阶段置信度",
                "x_title": "周期",
                "y_title": "最高阶段概率",
                "traces": [
                    _trace(
                        "当前阶段置信度",
                        [row["cycle"] for row in cycle_rows],
                        [row["stage_probability"] for row in cycle_rows],
                        kind="bar",
                        text=[row["current_stage"] for row in cycle_rows],
                    )
                ],
            },
        },
        "history": {
            "title": "五周期阶段概率、判定方法与历史复盘",
            "note": (
                "除康波外，阶段概率来自冻结显式久期滤波；康波因缺少多个独立完整长周期样本，"
                "仅按等概率作研究展示，不参数化、不映射、不进入配置。图中逐月展示每个周期"
                "最高阶段概率，表中完整列出当前概率分布和影子/生产准入状态。"
            ),
            "table": _table(
                [
                    ("cycle", "周期", "text"),
                    ("current_stage", "当前阶段", "text"),
                    ("stage_probability", "最高概率", "percentile"),
                    ("probability_distribution", "完整阶段概率", "text"),
                    ("confidence", "置信度", "percentile"),
                    ("judgment_method", "划分/判断方法", "text"),
                    ("duration", "最小/期望/最大久期", "text"),
                    ("data_status", "数据状态", "status"),
                    ("view_scope", "准入范围", "status"),
                    ("enters_shadow_allocation", "影子研究", "status"),
                    ("enters_production_allocation", "生产配置", "status"),
                    ("asset_mapping", "资产映射", "text"),
                    ("enters_allocation", "进入配置", "status"),
                ],
                cycle_rows,
            ),
            "chart": {
                "title": "五周期最高阶段概率时序",
                "x_title": "月份",
                "y_title": "阶段概率",
                "traces": _history_traces(data),
            },
        },
        "diagnostics": {
            "title": "四类资产配置模型：输入—计算—约束—输出—作用",
            "note": (
                "宏观因子模型、严格ERC、约束风险预算和完整Ω的Black–Litterman"
                "均按快照逐步展开；表述只引用冻结字段。"
            ),
            "table": _table(
                [
                    ("model", "模型", "text"),
                    ("step", "步骤", "text"),
                    ("evidence", "公式/约束/证据", "text"),
                    ("status", "求解/数据状态", "status"),
                ],
                _model_rows(data),
            ),
            "chart": {
                "title": "ERC、相对版风险预算及两版BL后组合风险贡献",
                "x_title": "资产",
                "y_title": "风险贡献",
                "traces": model_traces,
            },
        },
        "strategy": {
            "title": "策略权重、三段效果与展示基准",
            "note": (
                service_note
                + " 等权25/25/25/25只进入本净值图；优化、权重表、主动收益和IR"
                "仍以60/15/10/15战略基准为准。"
            ),
            "table": _table(
                [
                    ("kind", "证据类型", "text"),
                    ("model_version", "模型版本", "text"),
                    ("portfolio", "组合", "text"),
                    ("split", "阶段", "text"),
                    ("asset", "资产", "text"),
                    ("strength_rank", "强弱名次", "number"),
                    ("strength_label", "强弱标签", "status"),
                    ("composite_strength", "综合强度", "signed"),
                    ("active_weight", "高低配百分点", "signed_percent"),
                    ("decision_summary", "配置原因", "text"),
                    ("input_signals", "真实输入信号", "text"),
                    ("tie_assets", "并列资产", "text"),
                    ("equity", "权益", "percentile"),
                    ("bond", "国债", "percentile"),
                    ("gold", "黄金", "percentile"),
                    ("commodity", "商品", "percentile"),
                    ("annual_return", "年化收益", "signed_percent"),
                    ("sharpe", "夏普", "signed"),
                    ("max_drawdown", "最大回撤", "signed_percent"),
                    (
                        "annual_excess_return",
                        "对60/15/10/15战略基准超额",
                        "signed_percent",
                    ),
                    ("information_ratio", "IR", "signed"),
                    ("role", "口径/最终作用", "text"),
                ],
                _strategy_rows(data),
            ),
            "chart": {
                "title": "高低配版、无基准版与四资产等权净值",
                "x_title": "月份",
                "y_title": "成本后净值",
                "traces": nav_traces,
            },
            "service_authorization": {
                "status": deployment.get("status"),
                "executed_mode": deployment.get("executed_mode"),
                "authorization_basis": deployment.get("authorization_basis"),
            },
        },
    }
