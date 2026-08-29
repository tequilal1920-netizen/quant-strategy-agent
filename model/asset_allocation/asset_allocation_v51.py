"""Governed v5.1 integration layer for the four-asset allocation challenger.

The v5.0 engine already contains the portfolio mathematics.  This module
connects it to the strict factor registry and explicit-duration cycle filters
without rewriting or weakening the frozen v5.0 implementation.  It also
serialises the joint BL views and per-cycle contributions needed by the web
evidence panel.

The integration temporarily replaces v5.0 module-level call sites while a
single build is running, then restores them in ``finally``.  A process lock
makes the operation deterministic and safe for the research CLI.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v5 as _base
import cycle_views_v5 as _legacy_views
from asset_data_v5 import AssetSeriesSpecV5
from cycle_factor_registry_v5 import (
    serialise_cycle_factor_registry_v5,
    validate_cycle_factor_registry_v5,
)
from cycle_macro_models_v5 import (
    FACTOR_SCHEMA_VERSION_V5,
    build_macro_cycle_probabilities_v5,
    build_pring_market_probabilities_v5,
    merge_cycle_history_v5,
)


ENGINE_VERSION_V51 = "asset-allocation-research-v5.1-governed-shadow"
MODEL_FORMULA_V51 = (
    "D3/PIT factor admission -> explicit-duration probabilistic cycles -> "
    "macro+statistical covariance -> constrained risk budget -> full-Omega "
    "Black-Litterman -> robust cost-aware optimizer"
)
VIEW_LABELS_V51 = ("equity-minus-bond", "commodity-minus-bond", "gold-minus-bond")
_BUILD_LOCK = threading.RLock()


def _serialise_view_bundle(bundle: Any) -> dict[str, Any]:
    return {
        "view_labels": list(VIEW_LABELS_V51),
        "P": np.asarray(bundle.P, dtype=float).tolist(),
        "q": np.asarray(bundle.q, dtype=float).tolist(),
        "omega": np.asarray(bundle.omega, dtype=float).tolist(),
        "forecast_error_covariance": np.asarray(
            bundle.forecast_error_covariance, dtype=float
        ).tolist(),
        "cycle_contributions": {
            str(cycle): np.asarray(values, dtype=float).tolist()
            for cycle, values in bundle.cycle_contributions.items()
        },
        "diagnostics": dict(bundle.diagnostics),
        "policy": (
            "all three relative views are estimated jointly on the frozen training "
            "sample; Omega preserves cross-view forecast-error covariance"
        ),
    }


def _current_factor_availability(payload: Mapping[str, Any]) -> dict[str, Any]:
    cycle = ((payload.get("allocations") or {}).get("current_cycle") or {})
    cycles = cycle.get("cycles") or {}
    rows: dict[str, Any] = {}
    for name in ("kitchin", "juglar", "merrill", "pring", "kondratieff"):
        item = cycles.get(name) or {}
        evidence = item.get("factor_evidence") or {}
        rows[name] = {
            "state": item.get("state") or item.get("state_name"),
            "confidence": item.get("confidence"),
            "eligible_for_views": bool(item.get("eligible_for_views")),
            "data_status": item.get("data_status"),
            "required_pillars": list(evidence.get("required_pillars") or []),
            "present_pillars": list(evidence.get("present_pillars") or []),
            "missing_pillars": list(evidence.get("missing_pillars") or []),
            "missing_required_factors": list(
                evidence.get("missing_required_factors") or []
            ),
            "observed_fields": dict(evidence.get("observed_fields") or {}),
            "admission_reason": evidence.get("admission_reason"),
            "duration_model": item.get("duration_model"),
        }
    return {
        "factor_schema_version": FACTOR_SCHEMA_VERSION_V5,
        "cycles": rows,
        "conflicts": list(
            ((cycle.get("cycle_diagnostics") or {}).get("conflicts") or [])
        ),
        "admitted_cycles": list(
            ((cycle.get("cycle_diagnostics") or {}).get("admitted_cycles") or [])
        ),
    }


def build_snapshot_v51(
    macro_rows: Sequence[Mapping[str, Any]],
    price_series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    registry: Mapping[str, AssetSeriesSpecV5] | None = None,
    generated_at: str | None = None,
    config: _base.ResearchConfigV5 | None = None,
) -> dict[str, Any]:
    """Build the governed v5.1 shadow snapshot and restore v5.0 call sites."""

    active_config = config or _base.ResearchConfigV5()
    active_config.validate()
    validate_cycle_factor_registry_v5()
    captured: dict[str, Any] = {}

    with _BUILD_LOCK:
        original = {
            "macro": _base.build_macro_cycle_probabilities_v5,
            "pring": _base.build_pring_market_probabilities_v5,
            "merge": _base.merge_cycle_history_v5,
            "forecast": _base.forecast_cycle_views_v5,
            "allocate": _base._allocate_at_v5,
        }

        def governed_macro(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
            return build_macro_cycle_probabilities_v5(
                rows, train_end=active_config.train_end
            )

        def captured_forecast(
            fitted: Mapping[str, Any],
            prior_return: Sequence[float],
            current_cycle: Mapping[str, Any],
        ) -> Any:
            bundle = _legacy_views.forecast_cycle_views_v5(
                fitted, prior_return, current_cycle
            )
            captured["last_view"] = _serialise_view_bundle(bundle)
            return bundle

        def captured_allocate(*args: Any, **kwargs: Any) -> tuple[np.ndarray, dict[str, Any]]:
            weights, diagnostics = original["allocate"](*args, **kwargs)
            enriched = dict(diagnostics)
            if "last_view" in captured:
                enriched["cycle_views"] = dict(captured["last_view"])
            return weights, enriched

        _base.build_macro_cycle_probabilities_v5 = governed_macro
        _base.build_pring_market_probabilities_v5 = build_pring_market_probabilities_v5
        _base.merge_cycle_history_v5 = merge_cycle_history_v5
        _base.forecast_cycle_views_v5 = captured_forecast
        _base._allocate_at_v5 = captured_allocate
        try:
            payload = _base.build_snapshot_v5(
                macro_rows,
                price_series,
                registry=registry,
                generated_at=generated_at,
                config=active_config,
            )
        finally:
            _base.build_macro_cycle_probabilities_v5 = original["macro"]
            _base.build_pring_market_probabilities_v5 = original["pring"]
            _base.merge_cycle_history_v5 = original["merge"]
            _base.forecast_cycle_views_v5 = original["forecast"]
            _base._allocate_at_v5 = original["allocate"]

    payload["schema_version"] = "5.1"
    payload["engine_version"] = ENGINE_VERSION_V51
    payload["methodology"]["formula"] = MODEL_FORMULA_V51
    payload["methodology"]["cycle_filter"] = (
        "causal robust standardisation plus explicit-duration HSMM; transition "
        "calibration freezes at train_end"
    )
    payload["methodology"]["factor_admission"] = (
        "required economic pillars, verified availability time and vintage; "
        "missing pillars disable the cycle's risk-budget and BL contribution"
    )
    payload["cycle_factor_registry"] = serialise_cycle_factor_registry_v5()
    availability = _current_factor_availability(payload)
    payload["cycle_factor_availability"] = availability
    payload["quality"]["cycle_factor_admission"] = {
        "status": "passed" if availability["admitted_cycles"] else "blocked",
        **availability,
    }
    payload["limitations"] = list(payload.get("limitations") or []) + [
        "A cycle with incomplete required pillars or unverified release/vintage metadata is displayed but contributes zero tactical view.",
        "The explicit-duration state model reduces one-month phase flips but cannot make incomplete macro data production-ready.",
    ]
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


ResearchConfigV51 = _base.ResearchConfigV5
research_shadow_config_v51 = _base.research_shadow_config_v5


__all__ = [
    "ENGINE_VERSION_V51",
    "MODEL_FORMULA_V51",
    "ResearchConfigV51",
    "VIEW_LABELS_V51",
    "build_snapshot_v51",
    "research_shadow_config_v51",
]
