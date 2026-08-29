from __future__ import annotations

from snapshot_governance_v51 import harden_shadow_snapshot_v51


def _payload(registry_ready: bool, macro_ready: bool) -> dict:
    cycle = {
        "cycles": {
            "pring": {"eligible_for_views": True, "data_status": "D3_upstream_total_return_registry"},
            "kitchin": {"eligible_for_views": macro_ready},
            "juglar": {"eligible_for_views": macro_ready},
            "merrill": {"eligible_for_views": macro_ready},
        }
    }
    availability = {
        "cycles": {
            "pring": {"eligible_for_views": True},
            "kitchin": {"eligible_for_views": macro_ready},
            "juglar": {"eligible_for_views": macro_ready},
            "merrill": {"eligible_for_views": macro_ready},
        }
    }
    return {
        "status": "research_only",
        "config": {"production_mode": False},
        "cycle_history": [cycle],
        "allocations": {"current_cycle": cycle},
        "cycle_factor_availability": availability,
        "quality": {
            "asset_registry": {"production_ready": registry_ready},
            "cycle_factor_admission": {},
            "promotion_gate": {"checks": {"sealed_test_sample": True}},
        },
        "limitations": [],
        "weights": {"equity": 0.25},
    }


def test_execution_proxy_pring_is_shadow_only_and_blocks_promotion() -> None:
    hardened = harden_shadow_snapshot_v51(_payload(False, False))
    pring = hardened["allocations"]["current_cycle"]["cycles"]["pring"]
    assert pring["eligible_for_shadow_views"] is True
    assert pring["eligible_for_production_views"] is False
    assert pring["data_status"] == "D2_execution_proxy_shadow_only_not_D3"
    assert hardened["quality"]["cycle_factor_admission"]["status"] == "blocked"
    assert hardened["quality"]["promotion_gate"]["status"] == "blocked"
    assert hardened["governance_correction"]["weights_changed"] is False


def test_d3_and_all_macro_cycles_can_pass_added_checks() -> None:
    hardened = harden_shadow_snapshot_v51(_payload(True, True))
    checks = hardened["quality"]["promotion_gate"]["checks"]
    assert checks["cycle_factor_completeness"] is True
    assert checks["cycle_input_d3"] is True
    assert hardened["quality"]["cycle_factor_admission"]["status"] == "passed"
