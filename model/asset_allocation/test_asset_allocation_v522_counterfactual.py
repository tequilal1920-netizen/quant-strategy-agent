from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np

import asset_allocation_v521 as policy_governance
import asset_allocation_v52 as raw
import asset_allocation_v522 as engine
import verify_asset_allocation_v522 as verifier
from test_asset_allocation_v52 import _one_spec, _synthetic_panel


CANONICAL_SNAPSHOT_V522 = (
    Path(__file__).resolve().parents[2]
    / "board"
    / "quant_strategy_agent_vnext"
    / "data"
    / "asset_allocation_snapshot_v52_shadow.json"
)


def _relaxed_validation(self) -> None:
    """Keep general controls while allowing a policy counterfactual in this test."""

    raw._base.ResearchConfigV5.validate(self)
    weights = np.asarray(self.policy_benchmark_weights, dtype=float)
    assert weights.shape == (4,)
    assert np.all(weights > 0.0)
    assert abs(float(weights.sum()) - 1.0) < 1.0e-12


def _normalise_display_governance(snapshot):
    candidate = copy.deepcopy(snapshot)
    candidate["benchmark"]["weights"] = {
        "equity": 0.60,
        "bond": 0.15,
        "gold": 0.10,
        "commodity": 0.15,
    }
    return policy_governance.apply_validation_governance_v521(candidate)


def test_absolute_model_is_invariant_to_policy_benchmark_counterfactual() -> None:
    panel, macro = _synthetic_panel(72)
    common = dict(
        train_end="202012",
        validation_end="202112",
        lookback_months=18,
        minimum_cycle_train=18,
        minimum_train_returns=12,
        minimum_validation_returns=6,
        minimum_test_returns=6,
        production_mode=False,
    )
    declared = engine.ResearchConfigV522(**common)
    counterfactual = engine.ResearchConfigV522(
        **common,
        policy_benchmark_weights=(0.50, 0.20, 0.10, 0.20),
    )
    with patch.object(
        engine.ResearchConfigV522, "validate", _relaxed_validation
    ), patch.object(
        engine,
        "apply_validation_governance_v521",
        side_effect=_normalise_display_governance,
    ), patch.object(
        raw,
        "candidate_grid_v52",
        side_effect=lambda mode=None: _one_spec(str(mode)),
    ):
        first = engine.build_snapshot_v522(
            macro, panel, config=declared, generated_at="2026-08-11T00:00:00Z"
        )
        second = engine.build_snapshot_v522(
            macro, panel, config=counterfactual, generated_at="2026-08-11T00:00:00Z"
        )
    first_allocation = first["allocations"]["absolute_no_benchmark"]
    second_allocation = second["allocations"]["absolute_no_benchmark"]
    np.testing.assert_allclose(
        list(first_allocation["weights"].values()),
        list(second_allocation["weights"].values()),
        atol=1.0e-12,
    )
    first_rows = first["backtest"]["strategies"]["absolute_no_benchmark"]["returns"]
    second_rows = second["backtest"]["strategies"]["absolute_no_benchmark"]["returns"]
    np.testing.assert_allclose(
        [row["net_return"] for row in first_rows],
        [row["net_return"] for row in second_rows],
        atol=1.0e-12,
    )
    assert all(
        row["benchmark_weight"] is None
        for row in second["asset_decisions"]["absolute_no_benchmark"].values()
    )

def test_future_asof_can_update_selected_model_and_dynamic_weights() -> None:
    future_weights = {
        "equity": 0.48,
        "bond": 0.22,
        "gold": 0.10,
        "commodity": 0.20,
    }
    snapshot = {
        "data_as_of": {
            "market": "202607",
            "macro_available": "202607",
            "macro_complete": "202607",
        },
        "config": {
            "train_end": "202312",
            "validation_end": "202412",
        },
        "benchmark": {
            "weights": {
                "equity": 0.60,
                "bond": 0.15,
                "gold": 0.10,
                "commodity": 0.15,
            }
        },
        "allocations": {
            "benchmark_relative": {"weights": future_weights},
            "recommended": {"weights": copy.deepcopy(future_weights)},
        },
        "backtest": {
            "selection_audit": {
                "benchmark_relative": {
                    "selected_id": "V52-REL-FUTURE-VALIDATED",
                    "selection_uses_test": False,
                }
            }
        },
    }
    audit = engine.approved_relative_weight_freeze_audit_v522(snapshot)
    assert audit["applicable"] is False
    assert audit["status"] == "passed"
    assert set(audit["checks"]) == {
        "strategic_anchor_is_60_15_10_15_internal_order"
    }
    assert (
        engine.assert_approved_relative_snapshot_v522(snapshot)["status"]
        == "passed"
    )


def _current_verifier_contract_v522():
    payload = json.loads(CANONICAL_SNAPSHOT_V522.read_text(encoding="utf-8"))
    return (
        payload["quality"]["statistical_evidence_by_version"][
            "benchmark_relative"
        ],
        payload["cycle_factor_availability"],
        payload["allocations"]["current_cycle"]["cycles"],
        payload["model_evidence_catalog"]["cycle_models"],
    )


def test_canonical_statistical_warnings_are_exact_but_future_may_improve() -> None:
    statistical, _, _, _ = _current_verifier_contract_v522()
    assert verifier._statistical_evidence_errors_v522(
        statistical, canonical_release=True
    ) == []

    improved = copy.deepcopy(statistical)
    improved["checks"] = {
        name: True for name in improved["checks"]
    }
    improved["failed"] = []
    improved["status"] = "passed"
    assert "canonical_statistical_warning_evidence" in (
        verifier._statistical_evidence_errors_v522(
            improved, canonical_release=True
        )
    )
    assert verifier._statistical_evidence_errors_v522(
        improved, canonical_release=False
    ) == []

    inconsistent = copy.deepcopy(improved)
    inconsistent["failed"] = ["future_pristine_paper_holdout"]
    assert "statistical_failed_check_mismatch" in (
        verifier._statistical_evidence_errors_v522(
            inconsistent, canonical_release=False
        )
    )


def test_canonical_cycle_contract_is_exact_pring_d2_shadow_only() -> None:
    _, availability, current_cycles, cycle_models = (
        _current_verifier_contract_v522()
    )
    assert verifier._cycle_availability_errors_v522(
        availability,
        current_cycles,
        cycle_models,
        canonical_release=True,
    ) == []


def test_broker_reference_identity_fails_closed_after_rehash() -> None:
    canonical = json.loads(CANONICAL_SNAPSHOT_V522.read_text(encoding="utf-8"))
    assert verifier.verify(canonical)["status"] == "passed"

    mutations = [
        (
            "H3_AP202601121816952139_1.pdf",
            "title",
            "AI视角驱动的Black-Litterman资产配置",
            "catalog_reference_identity:allocation:black_litterman:1",
        ),
        (
            "738530789217.pdf",
            "institution",
            "国泰海通证券",
            "catalog_reference_issuer:allocation:risk_parity:0",
        ),
        (
            "815736789040.pdf",
            "date",
            "2025-11-06",
            "catalog_reference_date:cycle:kondratieff:1",
        ),
    ]
    for url_suffix, field, bad_value, expected_error in mutations:
        payload = copy.deepcopy(canonical)
        changed = False
        for family in ("cycle_models", "allocation_models"):
            for model in payload["model_evidence_catalog"][family].values():
                for reference in model["authoritative_references"]:
                    if str(reference.get("url") or "").endswith(url_suffix):
                        reference[field] = bad_value
                        changed = True
        assert changed
        engine._rehash(payload)
        result = verifier.verify(payload)
        assert result["model_hash_verified"] is True
        assert expected_error in result["errors"]


def _future_verified_juglar_contract_v522():
    _, availability, current_cycles, cycle_models = (
        _current_verifier_contract_v522()
    )
    availability = copy.deepcopy(availability)
    current_cycles = copy.deepcopy(current_cycles)
    cycle_models = copy.deepcopy(cycle_models)
    data_status = "D3_verified_vendor_pit"

    availability["admitted_cycles"] = ["pring", "juglar"]
    availability["production_admitted_cycles"] = ["juglar"]
    availability["cycles"]["juglar"].update(
        {
            "data_status": data_status,
            "eligible_for_views": True,
            "eligible_for_shadow_views": True,
            "eligible_for_production_views": True,
            "view_scope": "production",
        }
    )
    current_cycles["juglar"].update(
        {
            "data_status": data_status,
            "eligible_for_views": True,
            "eligible_for_shadow_views": True,
            "eligible_for_production_views": True,
            "view_scope": "production",
        }
    )
    juglar_model = cycle_models["juglar"]
    juglar_model["status"] = "admitted"
    juglar_model["inputs"].update(
        {
            "data_status": data_status,
            "authoritative_source_verification": "verified",
            "verified_vendor_series_ids": [
                "WIND:JUGLAR_CAPACITY_PIT",
                "WIND:JUGLAR_CREDIT_PIT",
            ],
        }
    )
    juglar_model["outputs"]["current_state_payload"] = copy.deepcopy(
        current_cycles["juglar"]
    )
    juglar_model["effects"].update(
        {
            "eligible_for_views": True,
            "eligible_for_production_views": True,
            "current_bl_view_contribution": [0.01, 0.0, -0.01],
            "current_contribution_is_zero": False,
        }
    )
    return availability, current_cycles, cycle_models


def test_future_verified_cycle_admission_is_allowed_when_internally_consistent() -> None:
    availability, current_cycles, cycle_models = (
        _future_verified_juglar_contract_v522()
    )
    assert verifier._cycle_availability_errors_v522(
        availability,
        current_cycles,
        cycle_models,
        canonical_release=False,
    ) == []


def test_future_cycle_inconsistencies_fail_closed() -> None:
    availability, current_cycles, cycle_models = (
        _future_verified_juglar_contract_v522()
    )

    bad_flags = copy.deepcopy(cycle_models)
    bad_flags["juglar"]["effects"]["eligible_for_views"] = False
    assert "cycle_catalog_effect_mismatch:juglar" in (
        verifier._cycle_availability_errors_v522(
            availability,
            current_cycles,
            bad_flags,
            canonical_release=False,
        )
    )

    bad_source = copy.deepcopy(cycle_models)
    bad_source["juglar"]["inputs"].update(
        {
            "authoritative_source_verification": "not_verified",
            "verified_vendor_series_ids": [],
        }
    )
    assert "production_cycle_source_not_verified:juglar" in (
        verifier._cycle_availability_errors_v522(
            availability,
            current_cycles,
            bad_source,
            canonical_release=False,
        )
    )

    bad_contribution = copy.deepcopy(cycle_models)
    bad_contribution["kitchin"]["effects"].update(
        {
            "current_bl_view_contribution": [0.01, 0.0, 0.0],
            "current_contribution_is_zero": False,
        }
    )
    assert "nonadmitted_cycle_nonzero_contribution:kitchin" in (
        verifier._cycle_availability_errors_v522(
            availability,
            current_cycles,
            bad_contribution,
            canonical_release=False,
        )
    )

    bad_admitted_list = copy.deepcopy(availability)
    bad_admitted_list["admitted_cycles"] = ["pring"]
    assert "cycle_admitted_set_mismatch" in (
        verifier._cycle_availability_errors_v522(
            bad_admitted_list,
            current_cycles,
            cycle_models,
            canonical_release=False,
        )
    )

    bad_data_status = copy.deepcopy(availability)
    bad_data_status["cycles"]["juglar"]["data_status"] = "stale_status"
    assert "cycle_current_contract_mismatch:juglar" in (
        verifier._cycle_availability_errors_v522(
            bad_data_status,
            current_cycles,
            cycle_models,
            canonical_release=False,
        )
    )
