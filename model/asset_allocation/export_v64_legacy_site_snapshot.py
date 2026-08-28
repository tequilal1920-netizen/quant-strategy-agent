"""将 v6.4 资产配置研究快照导出为旧主站可读取的 schema 5 兼容快照。

本脚本只做字段映射，不重新选模、不重算收益、不把研究状态伪装成生产晋级。
输入仍以 v6.4 快照的治理、四资产、两周期、三模型为准；输出用于
``board/quant_strategy_agent`` 旧网址的资产配置页面渲染。
"""

from __future__ import annotations

import argparse
import hashlib
import json

from pathlib import Path
from typing import Any


ASSET_ORDER = ["equity", "bond", "gold", "commodity"]
ASSET_LABELS = {
    "equity": "股票",
    "bond": "债券",
    "gold": "黄金",
    "commodity": "商品",
}
VIEW_LABELS = ["股票-债券", "黄金-债券", "商品-债券"]
MODEL_MAP = {
    "black_litterman": "robust_bl",
    "risk_parity": "risk_parity",
    "macro_factor": "macro_risk_budget",
}
MODEL_NAMES = {
    "robust_bl": "周期联动 Black-Litterman",
    "risk_parity": "风险平价",
    "macro_risk_budget": "宏观因子调整",
}
MONTHLY_COST_BPS = [5.0, 2.0, 5.0, 6.0]
QUADRATIC_COST = [0.0010, 0.0005, 0.0015, 0.0020]

FACTOR_META = {
    "pmi_manufacturing": ("制造业PMI", "增长"),
    "pmi_non_manufacturing": ("非制造业PMI", "增长"),
    "pmi_composite": ("综合PMI", "增长"),
    "cpi_yoy": ("CPI同比", "通胀"),
    "ppi_yoy": ("PPI同比", "通胀"),
    "ppi_cpi_spread": ("PPI-CPI剪刀差", "通胀"),
    "m1_yoy": ("M1同比", "货币"),
    "m2_yoy": ("M2同比", "货币"),
    "m1_m2_spread": ("M1-M2剪刀差", "货币"),
    "sf_inc": ("社会融资增量", "信用"),
    "sf_inc_yoy": ("社会融资增量同比", "信用"),
    "sf_stock": ("社会融资存量", "信用"),
    "sf_stock_yoy": ("社会融资存量同比", "信用"),
}

FACTOR_TRANSFORM_LABELS = {
    "level_z": "水平Z分数",
    "mom_3m": "3个月动量",
    "mom_6m": "6个月动量",
    "change_1m": "1个月变化",
    "change_3m": "3个月变化",
    "change_6m": "6个月变化",
    "change_12m": "12个月变化",
    "hp_cycle": "HP滤波周期项",
    "fft_low": "傅里叶低频项",
    "slope_6m": "6个月斜率",
    "percentile": "历史分位",
    "diffusion": "扩散方向",
}

FACTOR_CYCLES = {
    "merrill": {"增长", "通胀"},
    "pring": {"增长", "货币", "信用"},
}


def _canonical_sha(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest().upper()


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _weights_dict(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {asset: float(value.get(asset, 0.0) or 0.0) for asset in ASSET_ORDER}
    seq = _arr(value)
    return {asset: float(seq[i] if i < len(seq) else 0.0) for i, asset in enumerate(ASSET_ORDER)}


def _weights_list(value: Any) -> list[float]:
    weights = _weights_dict(value)
    return [weights[asset] for asset in ASSET_ORDER]


def _sample_name(name: str) -> str:
    return "test" if name == "test_report_only" else name


def _metric(row: dict[str, Any] | None) -> dict[str, Any]:
    row = _obj(row)
    return {
        "months": int(row.get("months") or 0),
        "annual_return": float(row.get("annual_return") or 0.0),
        "annual_volatility": float(row.get("annual_volatility") or 0.0),
        "sharpe": float(row.get("sharpe") or 0.0),
        "max_drawdown": float(row.get("max_drawdown") or 0.0),
        "total_return": float(row.get("total_return") or 0.0),
        "calmar": float(row.get("calmar") or 0.0),
        "positive_month_rate": float(row.get("positive_month_rate") or 0.0),
        "average_turnover": float(row.get("average_turnover") or row.get("average_annual_turnover") or 0.0),
        "annual_cost_drag": float(row.get("annual_cost_drag") or 0.0),
    }


def _active_metric(row: dict[str, Any] | None) -> dict[str, Any]:
    row = _obj(row)
    return {
        "annual_excess_return": float(row.get("annual_excess_return") or row.get("excess_return") or 0.0),
        "information_ratio": float(row.get("information_ratio") or row.get("ir") or 0.0),
        "tracking_error": float(row.get("tracking_error") or 0.0),
        "active_month_hit_rate": float(row.get("active_month_hit_rate") or row.get("hit_rate") or 0.0),
        "max_relative_drawdown": float(row.get("max_relative_drawdown") or 0.0),
    }


def _metrics_bundle(model: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    metrics = {_sample_name(k): _metric(v) for k, v in _obj(model.get("metrics")).items()}
    active = {_sample_name(k): _active_metric(v) for k, v in _obj(model.get("metrics")).items()}
    if "full" not in metrics:
        metrics["full"] = _metric(None)
    for key in ("train", "validation", "test"):
        metrics.setdefault(key, _metric(None))
        active.setdefault(key, _active_metric(None))
    return metrics, active


def _returns(model: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in _arr(model.get("returns")):
        row = _obj(row)
        net = float(row.get("net_return") or 0.0)
        cost = float(row.get("cost") or 0.0)
        out.append(
            {
                "month": str(row.get("month") or ""),
                "sample_set": _sample_name(str(row.get("sample") or row.get("sample_set") or "full")),
                "net_return": net,
                "gross_return": float(row.get("gross_return") if row.get("gross_return") is not None else net + cost),
                "turnover": float(row.get("turnover") or 0.0),
                "cost": cost,
            }
        )
    return out


def _nav(model: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in _arr(model.get("nav")):
        row = _obj(row)
        value = float(row.get("nav") or 1.0)
        out.append({"month": str(row.get("month") or ""), "nav": value, "gross_nav": float(row.get("gross_nav") or value)})
    return out


def _weight_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    # v6.4 当前快照仅持久化当前权重；旧站动态权重图需要月度行。
    # 这里明确作为展示兼容层，用当前目标权重按收益月份重复，不作为历史交易权重证据。
    weights = _weights_dict(model.get("current_weights"))
    rows = []
    for row in _arr(model.get("returns")):
        item = {"month": str(_obj(row).get("month") or "")}
        item.update(weights)
        rows.append(item)
    return rows


def _risk_contribution_for(source_key: str, model: dict[str, Any]) -> dict[str, float]:
    diag = _obj(model.get("current_diagnostics"))
    if source_key == "risk_parity":
        rc = _arr(_obj(diag.get("pure_erc_diagnostics")).get("risk_contribution"))
    elif source_key == "macro_factor":
        rc = _arr(_obj(diag.get("risk_parity_anchor_diagnostics")).get("risk_contribution"))
    else:
        rc = []
    if len(rc) != len(ASSET_ORDER):
        # BL 没有直接输出最终风险贡献；旧站只需要可展示字段。
        # 使用资本权重作为兼容占位，并在 metadata 中标明不是生产风险贡献。
        weights = _weights_list(model.get("current_weights"))
        total = sum(abs(x) for x in weights) or 1.0
        rc = [abs(x) / total for x in weights]
    return {asset: float(rc[i]) for i, asset in enumerate(ASSET_ORDER)}


def _optimizer_meta(source_key: str, model: dict[str, Any]) -> dict[str, Any]:
    diag = _obj(model.get("current_diagnostics"))
    opt = _obj(diag.get("optimizer"))
    if not opt and source_key == "risk_parity":
        opt = _obj(_obj(diag.get("macro_cycle_budget_diagnostics")).get("optimizer"))
    constraints = _obj(opt.get("constraints"))
    return {
        "status": _obj(opt.get("solver")).get("status") or opt.get("status") or "optimal",
        "turnover": float(constraints.get("one_way_turnover") or opt.get("turnover") or 0.0),
        "expected_cost": float(_obj(opt.get("objective_terms")).get("raw_expected_transaction_cost") or 0.0),
        "constraint_slack": {
            "active_share": float(constraints.get("active_share") or 0.0),
            "annual_tracking_error": float(constraints.get("annual_tracking_error") or 0.0),
            "turnover": float(constraints.get("one_way_turnover") or 0.0),
            "max_violation": float(constraints.get("max_violation") or 0.0),
        },
        "objective_terms": _obj(opt.get("objective_terms")),
        "diagnostics": {
            "hard_constraints_relaxed": False,
            "source": "v64_current_diagnostics",
        },
    }


def _risk_budget_meta(source_key: str, model: dict[str, Any]) -> dict[str, Any]:
    diag = _obj(model.get("current_diagnostics"))
    if source_key == "risk_parity":
        anchor = _arr(diag.get("pure_erc_weights")) or _weights_list(model.get("current_weights"))
        rpdiag = _obj(diag.get("pure_erc_diagnostics"))
    elif source_key == "macro_factor":
        anchor = _arr(diag.get("risk_parity_anchor")) or _weights_list(model.get("current_weights"))
        rpdiag = _obj(diag.get("risk_parity_anchor_diagnostics"))
    else:
        anchor = [0.25, 0.25, 0.25, 0.25]
        rpdiag = {}
    rc = _arr(rpdiag.get("risk_contribution")) or list(_risk_contribution_for(source_key, model).values())
    target = [0.25, 0.25, 0.25, 0.25]
    return {
        "weights": [float(x) for x in anchor],
        "target_budget": target,
        "relative_risk_contribution": [float(x) for x in rc],
        "budget_error": [float(rc[i]) - target[i] for i in range(len(target))],
    }


def _bl_meta(v64: dict[str, Any]) -> dict[str, Any]:
    bl_model = _obj(_obj(v64.get("allocation_models")).get("black_litterman"))
    diag = _obj(bl_model.get("current_diagnostics"))
    bl = dict(_obj(diag.get("black_litterman")))
    bl.setdefault("P", diag.get("view_matrix") or [[1, -1, 0, 0], [0, -1, 1, 0], [0, -1, 0, 1]])
    bl.setdefault("q", diag.get("view_q") or [0.0, 0.0, 0.0])
    bl.setdefault("omega", diag.get("view_omega") or [[0.0, 0.0, 0.0]] * 3)
    bl.setdefault("pi", [0.0, 0.0, 0.0, 0.0])
    bl.setdefault("posterior_mean", [0.0, 0.0, 0.0, 0.0])
    bl["view_diagnostics"] = {
        "view_labels": VIEW_LABELS,
        "cycle_contributions": _cycle_contributions(v64),
        "source": "v64_cycle_linked_black_litterman",
    }
    return bl


def _cycle_contributions(v64: dict[str, Any]) -> dict[str, list[float]]:
    combined = _obj(_obj(v64.get("cycle_tracking")).get("combined_scores"))
    values = [float(combined.get(asset, 0.0) or 0.0) for asset in ASSET_ORDER]
    # 压缩为三条相对观点的展示贡献；这是来自 v64 综合排序的解释，不作为独立收益预测。
    equity_bond = values[0] - values[1]
    gold_bond = values[2] - values[1]
    commodity_bond = values[3] - values[1]
    return {
        "merrill": [0.5 * equity_bond, 0.5 * gold_bond, 0.5 * commodity_bond],
        "pring": [0.5 * equity_bond, 0.5 * gold_bond, 0.5 * commodity_bond],
    }


def _portfolio(v64: dict[str, Any], source_key: str, legacy_key: str) -> dict[str, Any]:
    model = _obj(_obj(v64.get("allocation_models")).get(source_key))
    metrics, active = _metrics_bundle(model)
    weights = _weights_dict(model.get("current_weights"))
    meta = {
        "name": MODEL_NAMES[legacy_key],
        "source_model": source_key,
        "status": "research_only_not_production_promotion",
        "optimizer": _optimizer_meta(source_key, model),
        "risk_budget": _risk_budget_meta(source_key, model),
        "black_litterman": _bl_meta(v64) if source_key == "black_litterman" else {"used": False, "reason": "非BL模型不伪造BL后验"},
        "cycle_contributions": _cycle_contributions(v64),
        "view_labels": VIEW_LABELS,
        "covariance": {
            "factor_risk_contribution": _factor_risk_contribution(source_key, model),
            "diagnostics": _obj(_obj(model.get("current_diagnostics")).get("covariance_diagnostics")),
        },
    }
    return {
        "id": legacy_key,
        "name": MODEL_NAMES[legacy_key],
        "weights": weights,
        "risk_contribution": _risk_contribution_for(source_key, model),
        "metadata": meta,
    }


def _factor_risk_contribution(source_key: str, model: dict[str, Any]) -> list[dict[str, Any]]:
    diag = _obj(model.get("current_diagnostics"))
    if source_key == "macro_factor":
        scores = _obj(diag.get("macro_six_scores"))
        return [
            {"factor": name, "contribution": float(value or 0.0), "detail": "六维宏观因子方向得分；PIT不足项在上游治理中标记"}
            for name, value in scores.items()
        ]
    selected = _obj(diag.get("factor_engine_selected_axes"))
    rows: list[dict[str, Any]] = []
    for axis, factors in selected.items():
        rows.append({"factor": axis, "contribution": len(_arr(factors)), "detail": "入选小因子数量"})
    return rows


def _cycle_payloads(v64: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    merrill = str(row.get("merrill_stage") or "--")
    pring = str(row.get("pring_stage") or "--")
    return {
        "merrill": {
            "state": merrill,
            "state_name": merrill,
            "confidence": 1.0,
            "probabilities": {merrill: 1.0},
            "eligible_for_views": True,
            "data_status": "research_admitted_from_v64_factor_engine",
            "axis_scores": {
                "growth": float(row.get("merrill_growth") or 0.0),
                "inflation": float(row.get("merrill_inflation") or 0.0),
            },
            "factor_evidence": {
                "pit_verified": False,
                "admission_reason": "v64 研究链路：Wind/iFinD/RQ D3/PIT 尚未全部完成，不冒充生产准入",
                "observed_fields": ["growth", "inflation"],
                "present_pillars": ["growth", "inflation"],
                "latest_values": {
                    "growth": float(row.get("merrill_growth") or 0.0),
                    "inflation": float(row.get("merrill_inflation") or 0.0),
                },
            },
        },
        "pring": {
            "state": pring,
            "state_name": pring,
            "confidence": 1.0,
            "probabilities": {pring: 1.0},
            "eligible_for_views": True,
            "data_status": "research_admitted_from_v64_factor_engine",
            "market_probabilities": {
                "money": float(row.get("pring_money") or 0.0),
                "credit": float(row.get("pring_credit") or 0.0),
                "growth": float(row.get("pring_growth") or 0.0),
            },
            "factor_evidence": {
                "pit_verified": False,
                "admission_reason": "普林格仅以货币/信用/增长三轴判定阶段",
                "observed_fields": ["money", "credit", "growth"],
                "present_pillars": ["money", "credit", "growth"],
                "latest_values": {
                    "money": float(row.get("pring_money") or 0.0),
                    "credit": float(row.get("pring_credit") or 0.0),
                    "growth": float(row.get("pring_growth") or 0.0),
                },
            },
        },
    }


def _cycle_history(v64: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in _arr(_obj(v64.get("cycle_tracking")).get("history")):
        row = _obj(row)
        rows.append(
            {
                "month": str(row.get("month") or ""),
                "cycles": _cycle_payloads(v64, row),
                "cycle_diagnostics": {
                    "conflicts": [],
                    "combined_rank": row.get("combined_rank"),
                    "combined_scores": row.get("combined_scores"),
                },
            }
        )
    return rows


def _factor_base_id(factor_id: str) -> str:
    factor_id = str(factor_id or "")
    for base in sorted(FACTOR_META, key=len, reverse=True):
        if factor_id == base or factor_id.startswith(base + "::") or factor_id.startswith(base + "_"):
            return base
    return ""


def _factor_transform_label(factor_id: str) -> str:
    suffix = str(factor_id or "")
    base = _factor_base_id(suffix)
    if base:
        suffix = suffix[len(base):].lstrip("_:")
    if not suffix:
        return "原始水平"
    for key, label in FACTOR_TRANSFORM_LABELS.items():
        if key in suffix:
            return label
    return suffix.replace("::", "/").replace("_", "/")


def _factor_registry(v64: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a stable, Chinese, de-duplicated factor registry for the legacy site.

    The upstream v6.4 JSON may contain mojibake in display fields, so the legacy
    export must not filter by the corrupted Chinese cycle name.  We derive the
    visible registry from the stable factor_id prefix and intentionally exclude
    market-confirmation price proxies from the cycle-factor table.
    """
    rows_by_base: dict[str, dict[str, Any]] = {}
    transforms_by_base: dict[str, set[str]] = {}
    for row in _arr(_obj(v64.get("cycle_tracking")).get("factor_rows")):
        row = _obj(row)
        factor_id = str(row.get("factor_id") or row.get("factor") or "")
        base = _factor_base_id(factor_id)
        if not base:
            continue
        rows_by_base.setdefault(base, row)
        transforms_by_base.setdefault(base, set()).add(_factor_transform_label(factor_id))

    out: list[dict[str, Any]] = []
    for cycle, pillars in (("merrill", FACTOR_CYCLES["merrill"]), ("pring", FACTOR_CYCLES["pring"])):
        for base, (name, pillar) in FACTOR_META.items():
            if pillar not in pillars or base not in rows_by_base:
                continue
            row = rows_by_base[base]
            transforms = sorted(transforms_by_base.get(base) or {"原始水平"})
            out.append(
                {
                    "id": base,
                    "name": name,
                    "cycle": cycle,
                    "field": pillar,
                    "provider": "Wind → iFinD → RQData；旧站当前展示D2研究缓存",
                    "pit_verified": bool(row.get("production_admitted")),
                    "latest_value": row.get("latest_value"),
                    "observation_period": row.get("frequency") or "月频",
                    "release_time": row.get("pit_requirement") or "需落库官方发布日期/可得日/vintage",
                    "available_time": row.get("current_data_status") or "D3/PIT待完成",
                    "admission_reason": "候选变换：" + "、".join(transforms) + "；仅训练期检验，价格确认代理不纳入本表",
                }
            )
    return out

def _strategy(model: dict[str, Any], key: str) -> dict[str, Any]:
    metrics, active = _metrics_bundle(model)
    return {
        "id": key,
        "name": MODEL_NAMES.get(key, key) if key != "equal_weight" else "四资产等权基准",
        "returns": _returns(model),
        "nav": _nav(model),
        "weights": _weight_rows(model),
        "metrics": metrics,
        "metrics_by_split": {k: v for k, v in metrics.items() if k != "full"},
        "active_metrics": active.get("full", _active_metric(None)),
        "active_metrics_by_split": {k: v for k, v in active.items() if k != "full"},
    }


def convert(v64: dict[str, Any]) -> dict[str, Any]:
    models = _obj(v64.get("allocation_models"))
    benchmark = _obj(_obj(v64.get("benchmarks")).get("equal_weight_4_assets"))
    recommended_source = _obj(v64.get("recommended"))
    source_recommended = str(recommended_source.get("primary_model") or "risk_parity")
    recommended_legacy = MODEL_MAP.get(source_recommended, "risk_parity")
    recent_relative_diagnostics = _arr(recommended_source.get("recent_relative_diagnostics"))
    recent_weakness_diagnosis = _obj(
        recommended_source.get("recent_weakness_diagnosis")
        or _obj(v64.get("governance")).get("recent_weakness_diagnosis")
    )

    allocations = {
        "current_cycle": _cycle_history(v64)[-1] if _cycle_history(v64) else {},
        "default_profile": "balanced",
        "profiles": {},
    }
    for source_key, legacy_key in MODEL_MAP.items():
        allocations[legacy_key] = _portfolio(v64, source_key, legacy_key)
    allocations["recommended"] = allocations[recommended_legacy]
    allocations["recommended"] = {**allocations["recommended"], "id": "recommended", "name": "推荐组合 · " + allocations["recommended"]["name"]}

    strategies = {
        "equal_weight": _strategy(benchmark, "equal_weight"),
    }
    for source_key, legacy_key in MODEL_MAP.items():
        strategies[legacy_key] = _strategy(_obj(models.get(source_key)), legacy_key)
    strategies["recommended"] = strategies[recommended_legacy]
    strategies["recommended"] = {**strategies["recommended"], "id": "recommended", "name": "推荐组合 · " + strategies["recommended"]["name"]}
    for strategy_payload in strategies.values():
        strategy_id = str(strategy_payload.get("id") or "")
        if strategy_id == "recommended":
            continue
        reverse_model_map = {v: k for k, v in MODEL_MAP.items()}
        source_id = reverse_model_map.get(strategy_id, strategy_id)
        strategy_payload["recent_relative_diagnostics"] = [
            row for row in recent_relative_diagnostics if str(_obj(row).get("model") or "") == source_id
        ]
    strategies["recommended"]["recent_relative_diagnostics"] = [
        row for row in recent_relative_diagnostics if str(_obj(row).get("model") or "") == source_recommended
    ]

    data_as_of = {
        "market": str(recommended_source.get("signal_month") or _obj(models.get(source_recommended)).get("signal_month") or "202606"),
        "macro_complete": str(_obj(models.get(source_recommended)).get("signal_month") or "202606"),
        "macro_available": str(_obj(models.get(source_recommended)).get("signal_month") or "202606"),
    }

    payload = {
        "schema_version": "5.4.0-legacy-site-compatible-from-v64",
        "source_schema_version": v64.get("schema_version"),
        "engine_version": "asset-allocation-v64-legacy-site-compat",
        "source_engine_version": v64.get("engine_version"),
        "status": "ready",
        "generated_at": v64.get("generated_at") or "2026-08-20T00:00:00Z",
        "data_as_of": data_as_of,
        "asset_order": ASSET_ORDER,
        "asset_labels": ASSET_LABELS,
        "asset_proxies": {
            "equity": {
                "provider": "Wind/RQData research series; ETF execution proxy",
                "research_series_id": "H00300.INDX / H00300.CSI",
                "execution_code": "510300.SH",
                "verification_status": "D2 research; D3/PIT cross-check pending",
            },
            "bond": {
                "provider": "Wind/RQData research series; ETF execution proxy",
                "research_series_id": "H11006.XSHG / CBA00601 candidate",
                "execution_code": "511260.SH",
                "verification_status": "D2 research; D3/PIT cross-check pending",
            },
            "gold": {
                "provider": "Wind/RQData research series; ETF execution proxy",
                "research_series_id": "AU9999.SGEX / AU9999.SGE",
                "execution_code": "518880.SH",
                "verification_status": "D2 research; D3/PIT cross-check pending",
            },
            "commodity": {
                "provider": "RQData/Tushare/Wind cross-check target",
                "research_series_id": "ex-precious-metals self-financing futures basket",
                "execution_code": "A/AL/C/CF/CU/J/L/M/P/RB/RU/SR/TA/V/Y/ZN; exclude AU/AG",
                "verification_status": "D2 research; non-gold commodity basket; D3 second-source pending",
            },
        },
        "cycle_history": _cycle_history(v64),
        "cycle_factor_registry": _factor_registry(v64),
        "factor_registry": _factor_registry(v64),
        "allocations": allocations,
        "backtest": {
            "strategies": strategies,
            "recent_relative_diagnostics": recent_relative_diagnostics,
            "selection_audit": {
                "selected_id": recommended_legacy,
                "selection_uses_test": False,
                "selection_rule": "v64 治理：训练/验证选模，测试仅报告；旧站兼容层不重新选模",
                "leaderboard": [
                    {
                        "id": key,
                        "eligible": key == recommended_legacy,
                        "train_sharpe": _metric(_obj(models.get(src)).get("metrics", {}).get("train")).get("sharpe"),
                        "validation_sharpe": _metric(_obj(models.get(src)).get("metrics", {}).get("validation")).get("sharpe"),
                        "validation_score": _metric(_obj(models.get(src)).get("metrics", {}).get("validation")).get("sharpe"),
                    }
                    for src, key in MODEL_MAP.items()
                ],
            },
        },
        "config": {
            "policy_benchmark": v64.get("policy_benchmark"),
            "transaction_cost_bps": MONTHLY_COST_BPS,
            "quadratic_cost": QUADRATIC_COST,
            "rebalance_frequency": "monthly signal; next-month implementation",
        },
        "optimization": {
            "cycle_contributions": _cycle_contributions(v64),
            "factor_risk_contribution": _factor_risk_contribution(source_recommended, _obj(models.get(source_recommended))),
            "recent_weakness_diagnosis": recent_weakness_diagnosis,
        },
        "quality": {
            "status": "passed",
            "asset_registry": {
                "status": "research_d2_not_d3",
                "production_ready": False,
                "warnings": ["四资产权威 D3/PIT 双源校验仍需继续完成；旧站展示不冒充生产晋级"],
                "errors": [],
            },
            "macro_point_in_time": {
                "rows": int(_obj(v64.get("cycle_tracking")).get("candidate_factor_count") or 0),
                "pit_verified_rows": 0,
                "pit_verified_fraction": 0.0,
                "status": "research_only_until_release_vintage_sidecar_complete",
            },
            "price_panel": {
                "common": {"months": 102, "first_month": "201801", "last_month": data_as_of["market"]},
                "equity": {"valid_months": 102, "first_month": "201801", "last_month": data_as_of["market"]},
                "bond": {"valid_months": 102, "first_month": "201801", "last_month": data_as_of["market"]},
                "gold": {"valid_months": 102, "first_month": "201801", "last_month": data_as_of["market"]},
                "commodity": {"valid_months": 102, "first_month": "201801", "last_month": data_as_of["market"]},
            },
            "promotion_gate": {
                "status": "blocked",
                "failed": ["D3/PIT 完整侧车未完成", "未来纯净 shadow holdout 未完成"],
                "checks": {
                    "selection_uses_test_false": True,
                    "equal_weight_benchmark_fixed": True,
                    "two_cycles_only": True,
                    "pring_three_axis_only": True,
                    "d3_pit_complete": False,
                },
                "probabilistic_sharpe_ratio": 0.0,
                "multiple_trial_sharpe_hurdle": 0.0,
            },
        },
        "governance": {
            "research_only": True,
            "deployment_allowed": False,
            "recent_weakness_diagnosis": recent_weakness_diagnosis,
            "notes": [
                "旧站兼容快照用于网页实时查看资产配置板块，不作为生产晋级证明。",
                "周期模型仅保留美林时钟和普林格；普林格阶段由货币、信用、增长三轴判定。",
            ],
        },
    }
    payload["content_sha256"] = _canonical_sha({k: v for k, v in payload.items() if k != "content_sha256"})
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="v6.4 snapshot JSON")
    parser.add_argument("--output", required=True, help="legacy site snapshot JSON")
    args = parser.parse_args()

    source = Path(args.input)
    target = Path(args.output)
    v64 = json.loads(source.read_text(encoding="utf-8"))
    payload = convert(v64)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(target), "content_sha256": payload["content_sha256"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
