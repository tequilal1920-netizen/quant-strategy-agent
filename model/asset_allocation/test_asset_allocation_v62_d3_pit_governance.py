from __future__ import annotations

import json

import pytest

from asset_allocation_d3_pit_registry_v62 import REQUIRED_D3_PIT_FIELDS, build_macro_factor_registry
from build_snapshot_v61_four_asset_cycle_bl_rp_macro import build_snapshot as build_v61
from build_snapshot_v62_d3_pit_governed_four_asset_cycle_bl_rp_macro import build_snapshot as build_v62
from probe_asset_allocation_d3_pit_v62 import build_probe_evidence


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_v62_preserves_v61_model_effect_exactly():
    v61 = build_v61()
    v62 = build_v62()
    for key in ("asset_order", "policy_benchmark", "allocation_models", "benchmarks", "recommended"):
        assert _canonical(v62[key]) == _canonical(v61[key])
    assert v62["model_effect_freeze_v62"]["base_content_sha256"] == v61["content_sha256"]
    assert v62["governance"]["model_effect_frozen"] is True
    assert v62["governance"]["deployment_allowed"] is False


def test_v62_adds_macro_catalogue_without_admitting_unverified_factors():
    v61 = build_v61()
    v62 = build_v62()
    registry = v62["macro_factor_catalog_v62"]
    assert registry["macro_factor_count"] == 87
    assert registry["production_admitted_factor_count"] == 0
    assert registry["current_weight_factor_count"] == 0
    assert v62["d3_pit_governance"]["current_weight_factor_count_from_new_catalog"] == 0
    assert v62["cycle_tracking"]["candidate_factor_count"] == v61["cycle_tracking"]["candidate_factor_count"] + 87
    added = [
        row
        for row in v62["cycle_tracking"]["factor_rows"]
        if row.get("cycle") == "D3/PIT宏观因子库"
    ]
    assert len(added) == 87
    assert all(row["enters_current_weight"] == "no_pending_d3_pit_no_effect_change" for row in added)
    assert all("Wind" in row["source_priority"] and "iFinD" in row["source_priority"] for row in added)


def test_macro_registry_covers_six_categories_and_required_fields():
    registry = build_macro_factor_registry()
    assert set(registry["by_category"]) == {"growth", "inflation", "interest_rate", "credit", "fx", "liquidity"}
    assert all(registry["by_category"][key] > 0 for key in registry["by_category"])
    assert registry["required_d3_pit_fields"] == list(REQUIRED_D3_PIT_FIELDS)
    for row in registry["rows"]:
        assert row["production_admitted"] is False
        assert row["enters_current_weight"] is False
        assert row["d3_pit_required_fields"] == list(REQUIRED_D3_PIT_FIELDS)


def test_probe_evidence_is_secret_safe_and_row_limited():
    payload = build_probe_evidence(
        "Wind",
        "EDB_SAMPLE",
        [{"date": "2026-06-30", "value": 1.23}],
        {"sql": "SELECT TOP 1 TRADE_DT, VALUE FROM wande.dbo.SAMPLE"},
    )
    assert payload["row_count"] == 1
    assert payload["admission_ready"] is False
    assert "query_hash" in payload and "source_payload_hash" in payload
    with pytest.raises(ValueError):
        build_probe_evidence("Wind", "BAD", [{"token": "do-not-store"}], {"sql": "SELECT 1"})
    with pytest.raises(ValueError):
        build_probe_evidence("Wind", "BAD", [{} for _ in range(11)], {"sql": "SELECT 1"})
