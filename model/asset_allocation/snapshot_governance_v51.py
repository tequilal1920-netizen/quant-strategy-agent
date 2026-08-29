"""Final governance hardening for generated allocation v5.1 shadow snapshots.

The Pring filter can be researched with the local execution proxy panel, but
that panel is not a verified Wind total-return registry.  This finaliser makes
the shadow/production distinction explicit and adds cycle-completeness checks
to the promotion gate without changing any already realised backtest result.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


def _harden_pring(payload: dict[str, Any], registry_d3: bool) -> None:
    rows = list(payload.get("cycle_history") or [])
    current = ((payload.get("allocations") or {}).get("current_cycle") or {})
    candidates = rows + ([current] if current else [])
    for row in candidates:
        pring = ((row.get("cycles") or {}).get("pring") or {})
        if not pring:
            continue
        pring["eligible_for_shadow_views"] = bool(pring.get("eligible_for_views"))
        pring["eligible_for_production_views"] = bool(
            pring.get("eligible_for_views") and registry_d3
        )
        pring["data_status"] = (
            "D3_verified_total_return_registry"
            if registry_d3
            else "D2_execution_proxy_shadow_only_not_D3"
        )
        pring["view_scope"] = "production" if registry_d3 else "shadow_only"


def harden_shadow_snapshot_v51(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(source))
    quality = payload.setdefault("quality", {})
    registry = quality.get("asset_registry") or {}
    registry_d3 = bool(registry.get("production_ready"))
    _harden_pring(payload, registry_d3)

    availability = payload.get("cycle_factor_availability") or {}
    cycles = availability.get("cycles") or {}
    macro_names = ("kitchin", "juglar", "merrill")
    admitted_macro = [
        name for name in macro_names if bool((cycles.get(name) or {}).get("eligible_for_views"))
    ]
    complete_macro = len(admitted_macro) == len(macro_names)
    factor_gate = dict(quality.get("cycle_factor_admission") or {})
    factor_gate.update(
        {
            "status": "passed" if complete_macro and registry_d3 else "blocked",
            "macro_cycles_required": list(macro_names),
            "macro_cycles_admitted": admitted_macro,
            "macro_cycle_completeness": complete_macro,
            "pring_shadow_allowed": bool(
                (cycles.get("pring") or {}).get("eligible_for_views")
            ),
            "pring_production_allowed": bool(
                (cycles.get("pring") or {}).get("eligible_for_views") and registry_d3
            ),
            "policy": (
                "execution-proxy Pring signals may be evaluated in shadow; production "
                "requires D3 research total-return series plus all governed macro pillars"
            ),
        }
    )
    quality["cycle_factor_admission"] = factor_gate

    promotion = quality.get("promotion_gate") or {}
    checks = dict(promotion.get("checks") or {})
    checks["cycle_factor_completeness"] = complete_macro
    checks["cycle_input_d3"] = registry_d3
    promotion["checks"] = checks
    promotion["failed"] = [name for name, passed in checks.items() if not passed]
    promotion["status"] = "passed" if all(checks.values()) else "blocked"
    quality["promotion_gate"] = promotion

    payload["status"] = (
        "ready"
        if payload.get("config", {}).get("production_mode")
        and promotion["status"] == "passed"
        else "research_only"
    )
    payload["governance_correction"] = {
        "version": "5.1.1",
        "reason": "execution ETF/commodity proxies cannot be labelled D3 research total-return inputs",
        "backtest_values_changed": False,
        "weights_changed": False,
        "production_gate_tightened": True,
    }
    limitations = list(payload.get("limitations") or [])
    statement = (
        "Pring currently uses the local equity/bond/ex-gold-commodity execution proxy panel "
        "for shadow research only; it is not a D3 production view input."
    )
    if statement not in limitations:
        limitations.append(statement)
    payload["limitations"] = limitations
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


__all__ = ["harden_shadow_snapshot_v51"]
