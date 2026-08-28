"""D3/PIT admission registry for asset-allocation v6.2.

This module is intentionally a governance layer, not a data imputation layer.
It registers the broad macro factor universe required by the two-cycle /
three-model framework, but only admits a factor into production when provider
lineage is complete:

* Wind / iFinD / RQData provider code;
* observation period;
* release time and available time;
* vintage or revision identifier;
* retrieval timestamp and query hash;
* source payload hash and independent cross-source check.

Anything short of that stays visible as a research/catalogued factor and is
blocked from changing the current model weights.  This preserves the approved
v6.1 model effect while making the missing D3/PIT work explicit and testable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDICATOR_CATALOG = PROJECT_ROOT / "board" / "public_dashboard" / "data" / "indicator_catalog.seed.json"

REQUIRED_D3_PIT_FIELDS = (
    "provider",
    "provider_series_id",
    "observation_period",
    "release_time",
    "available_time",
    "vintage_or_revision_id",
    "retrieved_at",
    "query_hash",
    "source_payload_hash",
    "cross_provider_hash",
    "transformation_hash",
    "admission_decision",
)

PROVIDER_PRIORITY = ("Wind", "iFinD", "RQData", "AKShare/local fallback")

MACRO_CATEGORY_ORDER = ("growth", "inflation", "interest_rate", "credit", "fx", "liquidity")
MACRO_CATEGORY_LABELS = {
    "growth": "增长",
    "inflation": "通胀",
    "interest_rate": "利率",
    "credit": "信用",
    "fx": "汇率",
    "liquidity": "流动性",
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _load_indicator_catalog(path: Path = INDICATOR_CATALOG) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [dict(row) for row in data.get("rows") or []]
    return [row for row in rows if str(row.get("module") or "") == "宏观"]


def _macro_category(row: Mapping[str, Any]) -> str:
    submodule = str(row.get("submodule") or "")
    metric = str(row.get("metric") or "")
    variable = str(row.get("variable") or "")
    text = f"{submodule}|{metric}|{variable}".lower()
    if "价格" in submodule or "通胀" in submodule or "cpi" in text or "ppi" in text:
        return "inflation"
    if "信用" in submodule or "财政" in submodule or "社融" in text or "贷款" in text or "credit" in text:
        return "credit"
    if (
        "利率" in text
        or "shibor" in text
        or "dr007" in text
        or "fr007" in text
        or "lpr" in text
        or "收益率" in metric
    ):
        return "interest_rate"
    if "外贸" in submodule or "储备" in submodule or "汇率" in text or "美元" in text or "fx" in text:
        return "fx"
    if "货币" in submodule or "流动性" in submodule or "m1" in text or "m2" in text or "成交" in metric:
        return "liquidity"
    return "growth"


def _cycle_usage(category: str) -> list[str]:
    usage: list[str] = []
    if category == "growth":
        usage.extend(["美林时钟:增长轴", "普林格周期:增长轴", "宏观因子模型:增长"])
    if category == "inflation":
        usage.extend(["美林时钟:通胀轴", "宏观因子模型:通胀"])
    if category == "interest_rate":
        usage.extend(["普林格周期:货币轴", "宏观因子模型:利率"])
    if category == "credit":
        usage.extend(["普林格周期:信用轴", "宏观因子模型:信用"])
    if category == "fx":
        usage.append("宏观因子模型:汇率")
    if category == "liquidity":
        usage.extend(["普林格周期:货币轴", "宏观因子模型:流动性"])
    return usage


def _row_to_registry(row: Mapping[str, Any]) -> dict[str, Any]:
    category = _macro_category(row)
    source = str(row.get("primary_source") or "")
    fallback = str(row.get("fallback_source") or "")
    provider_hint = "Wind/iFinD/RQ待核验"
    if "Wind" in source or "Wind" in fallback:
        provider_hint = "Wind待核验"
    elif "RQ" in source or "RQ" in fallback:
        provider_hint = "RQData待核验"
    elif "iFinD" in source or "iFinD" in fallback or "同花顺" in source or "同花顺" in fallback:
        provider_hint = "iFinD待核验"
    return {
        "id": str(row.get("id") or ""),
        "module": "宏观",
        "submodule": str(row.get("submodule") or ""),
        "metric": str(row.get("metric") or ""),
        "variable": str(row.get("variable") or ""),
        "category": category,
        "category_cn": MACRO_CATEGORY_LABELS[category],
        "frequency": str(row.get("frequency") or ""),
        "unit": str(row.get("unit") or ""),
        "meaning": str(row.get("meaning") or ""),
        "primary_source_catalog": source,
        "fallback_source_catalog": fallback,
        "api_field_catalog": str(row.get("api_field") or ""),
        "validation_grade_catalog": str(row.get("validation_grade") or ""),
        "test_evidence_catalog": str(row.get("test_evidence") or ""),
        "quality_rules_catalog": str(row.get("quality_rules") or ""),
        "provider_priority": list(PROVIDER_PRIORITY),
        "provider_hint": provider_hint,
        "d3_pit_required_fields": list(REQUIRED_D3_PIT_FIELDS),
        "d3_pit_status": "catalogued_pending_Wind_iFinD_RQ_release_vintage_crosscheck",
        "production_admitted": False,
        "research_display": True,
        "enters_current_weight": False,
        "cycle_usage": _cycle_usage(category),
        "blocking_reason": "缺 Wind/iFinD/RQ provider_series_id、release_time/available_time、vintage/revision、query_hash 与跨源hash闭环；按门禁不得改变当前权重。",
        "update_contract": {
            "preferred_order": list(PROVIDER_PRIORITY[:3]),
            "max_probe_rows_per_series": 10,
            "no_secret_persistence": True,
            "admission_requires_cross_provider_match": True,
        },
    }


def build_macro_factor_registry() -> dict[str, Any]:
    catalog_rows = [_row_to_registry(row) for row in _load_indicator_catalog()]
    by_category = {key: 0 for key in MACRO_CATEGORY_ORDER}
    for row in catalog_rows:
        by_category[str(row["category"])] += 1
    registry = {
        "schema_version": "asset-allocation-d3-pit-registry/6.2",
        "catalog_source": str(INDICATOR_CATALOG.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "catalog_source_sha256": _hash(json.loads(INDICATOR_CATALOG.read_text(encoding="utf-8"))),
        "provider_priority": list(PROVIDER_PRIORITY),
        "required_d3_pit_fields": list(REQUIRED_D3_PIT_FIELDS),
        "macro_factor_count": len(catalog_rows),
        "by_category": by_category,
        "production_admitted_factor_count": 0,
        "research_display_factor_count": len(catalog_rows),
        "current_weight_factor_count": 0,
        "admission_policy": (
            "只有同时具备 Wind/iFinD/RQ 权威代码、观测期、发布时间、可得时间、"
            "vintage/revision、retrieved_at、query_hash、source hash、跨源hash和变换hash的因子，"
            "才允许从display/research进入当前权重。"
        ),
        "model_effect_policy": "未完成D3/PIT闭环的新增宏观因子不得改变v6.1已批准权重、收益和推荐结论。",
        "rows": catalog_rows,
        "rows_sha256": _hash(catalog_rows),
    }
    registry["content_sha256"] = _hash(registry)
    return registry


def registry_factor_rows(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in registry.get("rows") or []:
        rows.append(
            {
                "cycle": "D3/PIT宏观因子库",
                "pillar": f"{row.get('category_cn')} / {row.get('submodule')}",
                "factor": row.get("metric"),
                "source_priority": "Wind -> iFinD -> RQData；AKShare/local仅作研究展示或补充核验",
                "current_data_status": "已注册，待Wind/iFinD/RQ D3/PIT验证",
                "pit_requirement": "provider_series_id + release_time + available_time + vintage/revision + query_hash + cross_provider_hash",
                "frequency": row.get("frequency"),
                "processing": "发布时点对齐、修订版本冻结、同比/环比/扩散、HP滤波、傅里叶低频、滚动zscore、IC/命中率/稳定性/缺失率筛选",
                "enters_current_weight": "no_pending_d3_pit_no_effect_change",
                "provider_hint": row.get("provider_hint"),
                "catalog_variable": row.get("variable"),
                "catalog_validation_grade": row.get("validation_grade_catalog"),
            }
        )
    return rows


def build_d3_pit_governance(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "asset-allocation-d3-pit-governance/6.2",
        "status": "fail_closed_research_visible",
        "production_ready": False,
        "factor_catalogue_total": int(registry.get("macro_factor_count") or 0),
        "production_admitted_factor_count": int(registry.get("production_admitted_factor_count") or 0),
        "current_weight_factor_count_from_new_catalog": int(registry.get("current_weight_factor_count") or 0),
        "required_fields": list(REQUIRED_D3_PIT_FIELDS),
        "provider_priority": list(PROVIDER_PRIORITY),
        "truth_gate": [
            "Wind/iFinD/RQ任一主源取数成功只是D2；必须有release_time/available_time/vintage/revision才可PIT。",
            "Wind与iFinD/RQ/官方源需要样本hash交叉核验；差异超阈值时不得准入。",
            "未准入因子只可进入网页证据和待办，不可进入美林/普林格/BL/宏观因子权重。",
            "本次v6.2保持v6.1收益、权重和推荐模型不漂移。",
        ],
        "probe_contract": {
            "wind": "低频TOP/N或指定日期窗口，只输出字段/行数/query_hash/source_hash，不保存账号口令。",
            "ifind": "额度保护；优先单因子元数据与最近小样本，禁止全量高频拉取。",
            "rqdata": "用于资产收益与可用宏观/指数交叉验证；无宏观release vintage时不得单独准入。",
        },
    }


__all__ = [
    "REQUIRED_D3_PIT_FIELDS",
    "PROVIDER_PRIORITY",
    "build_macro_factor_registry",
    "registry_factor_rows",
    "build_d3_pit_governance",
]
