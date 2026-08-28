"""Build v6.2 D3/PIT-governed four-asset allocation snapshot.

v6.2 deliberately preserves the approved v6.1 model effect and conclusion:
weights, NAVs, metrics and recommended model are copied from v6.1 and verified
by tests.  The upgrade adds a much wider macro factor catalogue plus a strict
Wind/iFinD/RQ D3/PIT admission layer.  New factors are visible in the page and
machine-readable snapshot, but they do not enter Merrill, Pring, BL or macro
factor weights until the required release-vintage evidence is present.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

from asset_allocation_d3_pit_registry_v62 import (  # noqa: E402
    build_d3_pit_governance,
    build_macro_factor_registry,
    registry_factor_rows,
)
from build_snapshot_v61_four_asset_cycle_bl_rp_macro import (  # noqa: E402
    AUDIT_OUTPUT as AUDIT_OUTPUT_V61,
    DEFAULT_OUTPUT,
    build_snapshot as build_snapshot_v61,
)


SCHEMA_V62 = "6.2.0"
ENGINE_V62 = "asset-allocation-v62-d3-pit-governed-effect-frozen"
VERSION_V62 = "2026.08.16-asset-allocation-d3-pit-governed-vnext-r37.1"
AUDIT_OUTPUT = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_snapshot_v62_d3_pit_governed.json"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _model_effect_signature(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "asset_order": snapshot.get("asset_order"),
        "policy_benchmark": snapshot.get("policy_benchmark"),
        "allocation_models": snapshot.get("allocation_models"),
        "benchmarks": snapshot.get("benchmarks"),
        "recommended": snapshot.get("recommended"),
    }


def augment_snapshot_v62(base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if base is None:
        base = build_snapshot_v61()
    if str(base.get("schema_version") or "") != "6.1.0":
        raise ValueError("v62_requires_v61_base_snapshot")
    previous_hash = str(base.get("content_sha256") or "")
    registry = build_macro_factor_registry()
    governance = build_d3_pit_governance(registry)
    snapshot = copy.deepcopy(dict(base))
    model_effect = _model_effect_signature(base)
    effect_hash = _hash(model_effect)

    cycle_tracking = copy.deepcopy(snapshot.get("cycle_tracking") or {})
    base_factor_rows = list(cycle_tracking.get("factor_rows") or [])
    registry_rows = registry_factor_rows(registry)
    cycle_tracking["factor_rows"] = base_factor_rows + registry_rows
    cycle_tracking["candidate_factor_count"] = len(cycle_tracking["factor_rows"])
    cycle_tracking["production_admitted_cycles"] = []
    cycle_tracking["research_admitted_cycles"] = ["美林时钟", "普林格周期"]
    cycle_tracking["truth_boundary"] = (
        "v6.2已注册全量宏观小因子和Wind/iFinD/RQ D3/PIT门禁；"
        "未完成release-vintage与跨源hash闭环的因子不得进入当前权重，模型结论冻结自v6.1。"
    )
    cycle_tracking["current_summary"] = (
        "四资产两周期三模型：美林增长/通胀、普林格货币/信用/增长/市场确认，"
        "并已接入87个宏观小因子D3/PIT注册表；未验证因子只展示不改权重。"
    )

    snapshot["schema_version"] = SCHEMA_V62
    snapshot["engine_version"] = ENGINE_V62
    snapshot["app_version"] = VERSION_V62
    snapshot["generated_at"] = "2026-08-16"
    snapshot["cycle_tracking"] = cycle_tracking
    snapshot["macro_factor_catalog_v62"] = registry
    snapshot["d3_pit_governance"] = governance
    snapshot["data_quality"] = {
        **(snapshot.get("data_quality") or {}),
        "status": "D2_research_with_v62_D3_PIT_truth_gate",
        "production_ready": False,
        "factor_catalogue_total": registry["macro_factor_count"],
        "production_admitted_macro_factor_count": 0,
        "model_effect_frozen_from_schema": "6.1.0",
        "model_effect_frozen_from_content_sha256": previous_hash,
        "source_priority": "Wind优先，其次iFinD，再次RQData；新增因子未完成D3/PIT前不改变权重。",
        "blocking_items": [
            "87个宏观小因子已注册，但缺Wind/iFinD/RQ provider_series_id + release_time/available_time + vintage/revision + query_hash + cross_provider_hash闭环",
            "四资产收益面板仍为v553 D2 research；Wind/iFinD/RQ D3二源月度hash尚未写入当前快照",
            "为满足效果稳定，本次不让未验证新增因子进入求解器",
        ],
    }
    snapshot["governance"] = {
        **(snapshot.get("governance") or {}),
        "status": "research_service_visible_with_d3_pit_fail_closed_gate",
        "selection_uses_test": False,
        "deployment_allowed": False,
        "model_effect_frozen": True,
        "model_effect_signature_sha256": effect_hash,
        "d3_pit_production_admitted": False,
        "truth_boundary": (
            "v6.2新增数据维度和D3/PIT实时更新合约，但所有未验证因子均fail-closed；"
            "网页结论、推荐模型和收益指标保持v6.1。"
        ),
    }
    snapshot["model_effect_freeze_v62"] = {
        "base_schema_version": "6.1.0",
        "base_content_sha256": previous_hash,
        "effect_signature_sha256": effect_hash,
        "preserved_fields": [
            "asset_order",
            "policy_benchmark",
            "allocation_models",
            "benchmarks",
            "recommended",
        ],
        "reason": "用户要求补齐D3/PIT数据治理，同时模型效果和结论尽可能不变。",
    }
    snapshot["references"] = list(snapshot.get("references") or []) + [
        {
            "name": "v6.2 D3/PIT宏观小因子注册表",
            "path": "board/public_dashboard/data/indicator_catalog.seed.json",
            "usage": "87个宏观小因子映射到增长/通胀/利率/信用/汇率/流动性六类，作为Wind/iFinD/RQ实时更新和D3/PIT准入清单。",
        }
    ]
    snapshot.pop("content_sha256", None)
    snapshot["content_sha256"] = _hash(snapshot)
    return snapshot


def build_snapshot() -> dict[str, Any]:
    return augment_snapshot_v62()


def write_snapshot(output: Path) -> dict[str, Any]:
    snapshot = build_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(output)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    snapshot = write_snapshot(Path(args.output))
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": snapshot["schema_version"],
                "content_sha256": snapshot["content_sha256"],
                "base_content_sha256": snapshot["model_effect_freeze_v62"]["base_content_sha256"],
                "model_effect_signature_sha256": snapshot["model_effect_freeze_v62"]["effect_signature_sha256"],
                "recommended": snapshot["recommended"],
                "metrics": {k: v["metrics"]["full"] for k, v in snapshot["allocation_models"].items()},
                "factor_count": snapshot["cycle_tracking"]["candidate_factor_count"],
                "macro_factor_catalogue_total": snapshot["macro_factor_catalog_v62"]["macro_factor_count"],
                "production_admitted_macro_factor_count": snapshot["d3_pit_governance"]["production_admitted_factor_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
