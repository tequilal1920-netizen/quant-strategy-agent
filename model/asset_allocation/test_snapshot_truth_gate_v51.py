from __future__ import annotations

from snapshot_truth_gate_v51 import BLOCKED_DISPLAY_STATE_V51, apply_truth_gate_v51


def test_non_admitted_state_is_never_reported_as_current_conclusion() -> None:
    row = {
        "kitchin_state": "主动补库",
        "juglar_state": "繁荣早期",
        "merrill_state": "过热",
        "cycles": {
            "kitchin": {"state": "主动补库", "eligible_for_views": False, "probabilities": {"主动补库": 0.25}},
            "juglar": {"state": "繁荣早期", "eligible_for_views": False, "probabilities": {"繁荣早期": 0.30}},
            "merrill": {"state": "过热", "eligible_for_views": False, "probabilities": {"过热": 0.37}},
        },
    }
    source = {
        "cycle_history": [row],
        "allocations": {"current_cycle": row},
        "cycle_factor_availability": {"cycles": row["cycles"]},
        "quality": {"cycle_factor_admission": {"cycles": row["cycles"]}},
    }
    gated = apply_truth_gate_v51(source)
    for name in ("kitchin", "juglar", "merrill"):
        cycle = gated["allocations"]["current_cycle"]["cycles"][name]
        assert cycle["state"] == BLOCKED_DISPLAY_STATE_V51
        assert cycle["latent_state"]
        assert cycle["probabilities"]
    assert gated["truth_gate"]["weights_changed"] is False


def test_admitted_cycle_keeps_its_reported_state() -> None:
    row = {"cycles": {"merrill": {"state": "复苏", "eligible_for_views": True}}}
    gated = apply_truth_gate_v51({"cycle_history": [row], "allocations": {"current_cycle": row}})
    assert gated["allocations"]["current_cycle"]["cycles"]["merrill"]["state"] == "复苏"
