"""Canonical broker-report rows for the schema-5.2.2 formal visual tables."""
from __future__ import annotations

from typing import Any, Mapping


ELIGIBLE_SCOPES = ("exact_method", "cross_cycle_framework")
REFERENCE_LIMIT = 5
FALLBACK_TEXT = "暂无已核验专属研报"

CYCLE_NAMES = {
    "pring": "普林格周期",
    "kitchin": "基钦周期",
    "juglar": "朱格拉周期",
    "merrill": "美林时钟",
    "kondratieff": "康波周期",
}
ALLOCATION_MODEL_NAMES = {
    "macro_factor_model": "宏观因子风险模型",
    "risk_parity": "严格风险平价（ERC）",
    "risk_budget": "约束风险预算",
    "black_litterman": "稳健Black–Litterman",
}


def _text(reference: Mapping[str, Any], *keys: str, default: str = "--") -> str:
    for key in keys:
        value = reference.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _eligible_references(model: Any) -> list[Mapping[str, Any]]:
    if not isinstance(model, Mapping):
        return []
    references = model.get("authoritative_references")
    if not isinstance(references, list):
        return []
    selected = [
        reference
        for reference in references
        if isinstance(reference, Mapping)
        and reference.get("verification_status") == "inspected"
        and reference.get("scope") in ELIGIBLE_SCOPES
    ]
    # Exact-method evidence is always shown before a cross-cycle framework.
    selected.sort(
        key=lambda reference: ELIGIBLE_SCOPES.index(str(reference.get("scope")))
    )
    return selected[:REFERENCE_LIMIT]


def _catalog(snapshot: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    catalog = snapshot.get("model_evidence_catalog")
    if not isinstance(catalog, Mapping):
        return {}
    models = catalog.get(family)
    return models if isinstance(models, Mapping) else {}


def cycle_reference_rows_v522(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    models = _catalog(snapshot, "cycle_models")
    rows: list[dict[str, Any]] = []
    for model_id, model_name in CYCLE_NAMES.items():
        references = _eligible_references(models.get(model_id))
        if not references:
            rows.append(
                {
                    "row_type": "reference_fallback",
                    "reference_model_id": model_id,
                    "cycle": model_name,
                    "pillar": "权威研报证据",
                    "factor": FALLBACK_TEXT,
                    "field": "--",
                    "frequency": "研报",
                    "source": "--",
                    "required": "研究依据",
                    "pit_status": "not_available",
                    "data_status": "not_available",
                    "economic_role": "仅展示已逐篇核验的方法证据",
                    "stage": "--",
                    "asset_mapping": "--",
                    "enters_allocation": "不直接进入配置",
                    "verification_status": "not_available",
                    "reference_scope": "none",
                }
            )
            continue
        for reference in references:
            title = _text(reference, "title", "report_title", "name")
            broker = _text(reference, "broker", "institution", "publisher")
            date = _text(
                reference,
                "report_date",
                "date",
                "published_at",
                "publish_date",
            )
            cataloged_at = _text(reference, "cataloged_at", default="")
            matched_section = _text(reference, "matched_section", default="")
            url = _text(reference, "url", "source_url", "path")
            scope = str(reference["scope"])
            evidence_scope = scope
            if matched_section:
                evidence_scope += f"；对应章节={matched_section}"
            if cataloged_at:
                evidence_scope += f"；目录收录={cataloged_at}"
            rows.append(
                {
                    "row_type": "authoritative_reference",
                    "reference_model_id": model_id,
                    "cycle": model_name,
                    "pillar": "权威研报证据",
                    "factor": title,
                    "field": date,
                    "frequency": "研报",
                    "source": broker,
                    "required": "研究依据",
                    "pit_status": "inspected",
                    "data_status": "inspected",
                    "economic_role": evidence_scope,
                    "stage": "--",
                    "asset_mapping": url,
                    "enters_allocation": "不直接进入配置",
                    "verification_status": "inspected",
                    "reference_scope": scope,
                    "reference_title": title,
                    "reference_broker": broker,
                    "reference_date": date,
                    "reference_report_date": date,
                    "reference_cataloged_at": cataloged_at or None,
                    "reference_matched_section": matched_section or None,
                    "reference_url": url,
                }
            )
    return rows


def allocation_reference_rows_v522(
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    models = _catalog(snapshot, "allocation_models")
    rows: list[dict[str, Any]] = []
    for model_id, model_name in ALLOCATION_MODEL_NAMES.items():
        references = _eligible_references(models.get(model_id))
        if not references:
            rows.append(
                {
                    "row_type": "reference_fallback",
                    "reference_model_id": model_id,
                    "model": model_name,
                    "step": "权威研报",
                    "evidence": FALLBACK_TEXT,
                    "status": "not_available",
                    "verification_status": "not_available",
                    "reference_scope": "none",
                }
            )
            continue
        for reference in references:
            title = _text(reference, "title", "report_title", "name")
            broker = _text(reference, "broker", "institution", "publisher")
            date = _text(
                reference,
                "report_date",
                "date",
                "published_at",
                "publish_date",
            )
            cataloged_at = _text(reference, "cataloged_at", default="")
            matched_section = _text(reference, "matched_section", default="")
            url = _text(reference, "url", "source_url", "path")
            scope = str(reference["scope"])
            metadata = []
            if matched_section:
                metadata.append(f"对应章节={matched_section}")
            if cataloged_at:
                metadata.append(f"目录收录={cataloged_at}")
            metadata_text = "｜" + "｜".join(metadata) if metadata else ""
            rows.append(
                {
                    "row_type": "authoritative_reference",
                    "reference_model_id": model_id,
                    "model": model_name,
                    "step": "权威研报",
                    "evidence": (
                        f"{broker}｜{title}｜{date}｜scope={scope}{metadata_text}｜{url}"
                    ),
                    "status": "inspected",
                    "verification_status": "inspected",
                    "reference_scope": scope,
                    "reference_title": title,
                    "reference_broker": broker,
                    "reference_date": date,
                    "reference_report_date": date,
                    "reference_cataloged_at": cataloged_at or None,
                    "reference_matched_section": matched_section or None,
                    "reference_url": url,
                }
            )
    return rows
