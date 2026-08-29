"""Build the user-approved v5.2.2 dual-policy allocation snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import asset_allocation_v522 as corrected
import build_snapshot_v52 as raw_builder


def main() -> int:
    original_blocked = raw_builder._blocked
    original_atomic_json = raw_builder._atomic_json

    def corrected_blocked(reason, lineage, registry):
        payload = original_blocked(reason, lineage, registry)
        payload["schema_version"] = corrected.SCHEMA_VERSION_V522
        payload["engine_version"] = corrected.ENGINE_VERSION_V522
        payload["policy"] = (
            "blocked v5.2.2 artifacts cannot replace the authorized allocation snapshot"
        )
        raw_builder._rehash(payload)
        return payload
    def corrected_atomic_json(payload, destination):
        public_payload = corrected.sanitize_public_snapshot_v522(payload)
        if public_payload.get("status") == "ready":
            corrected.assert_approved_relative_snapshot_v522(public_payload)
            decision = public_payload.get("deployment_decision") or {}
            expected = {
                "status": "user_approved_sharpe_mandate",
                "deployable_dynamic_model": True,
                "executed_mode": "benchmark_relative",
                "authorization_basis": corrected.AUTHORIZATION_BASIS_V522,
            }
            if any(decision.get(key) != value for key, value in expected.items()):
                raise AssertionError("v522_deployment_decision_contract_changed")
            quality = public_payload.get("quality") or {}
            service = quality.get("service_contract_gate") or {}
            if quality.get("status") != "passed" or service.get("status") != "passed":
                raise AssertionError("v522_service_contract_not_passed")
        original_atomic_json(public_payload, destination)


    originals = {
        "build_snapshot_v52": raw_builder.build_snapshot_v52,
        "research_shadow_config_v52": raw_builder.research_shadow_config_v52,
        "ResearchConfigV52": raw_builder.ResearchConfigV52,
        "_blocked": raw_builder._blocked,
        "_atomic_json": raw_builder._atomic_json,
    }
    raw_builder.build_snapshot_v52 = corrected.build_snapshot_v522
    raw_builder.research_shadow_config_v52 = corrected.research_shadow_config_v522
    raw_builder.ResearchConfigV52 = corrected.ResearchConfigV522
    raw_builder._blocked = corrected_blocked
    raw_builder._atomic_json = corrected_atomic_json
    try:
        result = raw_builder.main()
        args = raw_builder.parse_args()
        output = Path(args.output).resolve()
        if output.exists():
            payload = json.loads(output.read_text(encoding="utf-8"))
            if payload.get("status") == "ready":
                quality = payload.get("quality") or {}
                service = quality.get("service_contract_gate") or {}
                if (
                    quality.get("status") == "passed"
                    and service.get("status") == "passed"
                ):
                    corrected.assert_approved_relative_snapshot_v522(payload)
                    return 0
                return 2
        return result
    finally:
        for name, original in originals.items():
            setattr(raw_builder, name, original)


if __name__ == "__main__":
    raise SystemExit(main())
