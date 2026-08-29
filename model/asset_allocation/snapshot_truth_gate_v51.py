"""Truthful display gate for non-admitted macro-cycle states in v5.1.

An HSMM always has a latent most-likely state, even when every emission is
missing.  Such a prior-driven state must not be presented as a researched
current macro conclusion.  This post-build gate retains the latent state and
probabilities for audit, but replaces the display state with an explicit
"data not admitted" label whenever required PIT factors failed admission.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


MACRO_CYCLES_V51 = ("kitchin", "juglar", "merrill")
BLOCKED_DISPLAY_STATE_V51 = "数据不足（未准入）"


def _gate_cycle_row(row: dict[str, Any]) -> int:
    changed = 0
    cycles = row.get("cycles") or {}
    for name in MACRO_CYCLES_V51:
        cycle = cycles.get(name) or {}
        if cycle and not bool(cycle.get("eligible_for_views")):
            latent = cycle.get("latent_state", cycle.get("state"))
            cycle["latent_state"] = latent
            cycle["state"] = BLOCKED_DISPLAY_STATE_V51
            cycle["display_policy"] = (
                "latent probabilities are retained for diagnostics; no current-cycle "
                "conclusion is reported until required factors and PIT metadata pass"
            )
            row[f"{name}_state"] = BLOCKED_DISPLAY_STATE_V51
            changed += 1
    return changed


def apply_truth_gate_v51(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(source))
    changed = 0
    for row in payload.get("cycle_history") or []:
        changed += _gate_cycle_row(row)
    current = ((payload.get("allocations") or {}).get("current_cycle") or {})
    if current:
        changed += _gate_cycle_row(current)

    for container in (
        payload.get("cycle_factor_availability") or {},
        ((payload.get("quality") or {}).get("cycle_factor_admission") or {}),
    ):
        cycles = container.get("cycles") or {}
        for name in MACRO_CYCLES_V51:
            cycle = cycles.get(name) or {}
            if cycle and not bool(cycle.get("eligible_for_views")):
                cycle["latent_state"] = cycle.get("latent_state", cycle.get("state"))
                cycle["state"] = BLOCKED_DISPLAY_STATE_V51

    payload["truth_gate"] = {
        "version": "5.1.2",
        "blocked_display_state": BLOCKED_DISPLAY_STATE_V51,
        "macro_cycle_payloads_gated": changed,
        "probabilities_preserved": True,
        "weights_changed": False,
        "backtest_values_changed": False,
    }
    payload.pop("model_hash", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


__all__ = [
    "BLOCKED_DISPLAY_STATE_V51",
    "MACRO_CYCLES_V51",
    "apply_truth_gate_v51",
]
