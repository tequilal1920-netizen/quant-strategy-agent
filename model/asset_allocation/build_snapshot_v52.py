"""Build the governed v5.2 dual-policy allocation shadow snapshot.

This command reads the local research warehouse only.  It never downloads
paid data, writes credentials, promotes production or overwrites the v4 live
snapshot.  Production mode remains subject to the unchanged D3/PIT and
statistical gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from asset_allocation_engine import load_macro_from_sqlite
from asset_allocation_v52 import (
    ENGINE_VERSION_V52,
    ResearchConfigV52,
    build_snapshot_v52,
    research_shadow_config_v52,
)
from asset_data_authoritative_v51 import load_local_authoritative_execution_prices_v51
from asset_data_v5 import (
    ASSET_ORDER_V5,
    default_asset_registry_v5,
    load_asset_registry_v5,
    validate_asset_registry_v5,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--registry", default=None)
    parser.add_argument("--pit-macro-json", default=None)
    parser.add_argument("--train-end", default=None)
    parser.add_argument("--validation-end", default=None)
    parser.add_argument("--production", action="store_true")
    return parser.parse_args()


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _rehash(payload: dict[str, Any]) -> None:
    payload.pop("model_hash", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(destination)


def _load_macro(
    database: Path, pit_json: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not pit_json:
        rows = [dict(row) for row in load_macro_from_sqlite(database)]
        return rows, {
            "source": "local_research_warehouse_macro",
            "pit_connector_file": None,
            "warning": "rows_without_verified_availability_and_vintage_are_display_only",
        }
    source = Path(pit_json).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("pit_macro_json_must_contain_row_objects")
    return [dict(row) for row in rows], {
        "source": "connector_pit_json",
        "pit_connector_file": str(source),
        "connector_manifest": payload.get("manifest") if isinstance(payload, dict) else None,
    }


def _month_span(first_month: str, last_month: str) -> int:
    if len(first_month) != 6 or len(last_month) != 6:
        return 0
    first = int(first_month[:4]) * 12 + int(first_month[4:]) - 1
    last = int(last_month[:4]) * 12 + int(last_month[4:]) - 1
    return max(last - first + 1, 0)


def _blocked(
    reason: str, lineage: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    payload = {
        "schema_version": "5.2",
        "engine_version": ENGINE_VERSION_V52,
        "generated_at": _now(),
        "status": "blocked",
        "asset_order": list(ASSET_ORDER_V5),
        "reason": reason,
        "quality": {
            "status": "blocked",
            "asset_registry": dict(registry),
            "local_execution_lineage": dict(lineage),
        },
        "policy": "blocked research artifacts cannot replace the live allocation snapshot",
    }
    _rehash(payload)
    return payload


def main() -> int:
    args = parse_args()
    database = Path(args.database).resolve()
    output = Path(args.output).resolve()
    if database == output:
        raise ValueError("output_must_not_overwrite_database")
    registry = (
        load_asset_registry_v5(args.registry)
        if args.registry
        else default_asset_registry_v5()
    )
    registry_audit = validate_asset_registry_v5(
        registry, require_production=bool(args.production)
    )
    if args.production and registry_audit["status"] != "passed":
        snapshot = _blocked("v52_production_asset_registry_failed", {}, registry_audit)
        _atomic_json(snapshot, output)
        return 2
    prices, lineage = load_local_authoritative_execution_prices_v51(database)
    coverage = lineage.get("coverage") or {}
    first_month = max(
        str((coverage.get(asset) or {}).get("first") or "")[:6]
        for asset in ASSET_ORDER_V5
    )
    last_month = min(
        str((coverage.get(asset) or {}).get("last") or "")[:6]
        for asset in ASSET_ORDER_V5
    )
    span = _month_span(first_month, last_month)
    if span < 24:
        snapshot = _blocked(
            f"local_execution_common_history_too_short:{span}:first={first_month}:last={last_month}",
            lineage,
            registry_audit,
        )
        _atomic_json(snapshot, output)
        return 2
    macro_rows, macro_lineage = _load_macro(database, args.pit_macro_json)
    config = research_shadow_config_v52(first_month, last_month)
    if args.train_end or args.validation_end or args.production:
        config = ResearchConfigV52(
            **{
                **config.__dict__,
                "train_end": args.train_end or config.train_end,
                "validation_end": args.validation_end or config.validation_end,
                "production_mode": bool(args.production),
            }
        )
    try:
        snapshot = build_snapshot_v52(
            macro_rows, prices, registry=registry, config=config
        )
        snapshot["quality"]["local_execution_lineage"] = lineage
        snapshot["quality"]["macro_connector_lineage"] = macro_lineage
        _rehash(snapshot)
    except (ValueError, RuntimeError) as error:
        snapshot = _blocked(str(error), lineage, registry_audit)
        snapshot["quality"]["macro_connector_lineage"] = macro_lineage
        _rehash(snapshot)
    _atomic_json(snapshot, output)
    summary = {
        "status": snapshot["status"],
        "output": str(output),
        "generated_at": snapshot["generated_at"],
        "reason": snapshot.get("reason"),
        "recommended_mode": ((snapshot.get("allocations") or {}).get("recommended_mode")),
        "promotion": ((snapshot.get("quality") or {}).get("promotion_gate") or {}).get("status"),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if args.production:
        return 0 if snapshot["status"] == "ready" else 2
    return 0 if snapshot["status"] == "research_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
