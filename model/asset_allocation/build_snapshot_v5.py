"""Build an auditable v5 shadow snapshot from local read-only data.

The default command does not call paid or public APIs.  It writes either a
research-only model snapshot or an explicit blocked manifest.  Production mode
requires a D3 registry and the statistical promotion gate; it cannot be forced.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asset_allocation_engine import load_macro_from_sqlite
from asset_allocation_v5 import ResearchConfigV5, build_snapshot_v5, research_shadow_config_v5
from asset_data_v5 import (
    ASSET_ORDER_V5,
    default_asset_registry_v5,
    load_asset_registry_v5,
    load_local_shadow_prices_v5,
    validate_asset_registry_v5,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Path to research_warehouse.db")
    parser.add_argument("--output", required=True, help="Destination JSON; never use the live v4 filename for shadow runs")
    parser.add_argument("--registry", default=None, help="Optional audited v5 asset registry JSON")
    parser.add_argument("--train-end", default=None, help="Frozen YYYYMM training boundary")
    parser.add_argument("--validation-end", default=None, help="Frozen YYYYMM validation boundary")
    parser.add_argument("--production", action="store_true", help="Require D3 data and all promotion gates")
    return parser.parse_args()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(destination)


def _blocked_payload(reason: str, lineage: dict[str, Any], registry_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "5.0",
        "engine_version": "asset-allocation-research-v5.0-shadow",
        "generated_at": _now(),
        "status": "blocked",
        "asset_order": list(ASSET_ORDER_V5),
        "reason": reason,
        "quality": {
            "status": "blocked",
            "asset_registry": registry_audit,
            "local_shadow_lineage": lineage,
        },
        "policy": "blocked manifests may be inspected but cannot replace a live allocation snapshot",
    }


def _month_span(first_month: str, last_month: str) -> int:
    if len(first_month) != 6 or len(last_month) != 6:
        return 0
    first = int(first_month[:4]) * 12 + int(first_month[4:]) - 1
    last = int(last_month[:4]) * 12 + int(last_month[4:]) - 1
    return max(last - first + 1, 0)


def main() -> int:
    args = parse_args()
    database = Path(args.database).resolve()
    output = Path(args.output).resolve()
    if database == output:
        raise ValueError("output_must_not_overwrite_database")
    registry = load_asset_registry_v5(args.registry) if args.registry else default_asset_registry_v5()
    registry_audit = validate_asset_registry_v5(registry, require_production=bool(args.production))
    if args.production and registry_audit["status"] != "passed":
        payload = _blocked_payload("v5_production_asset_registry_failed", {}, registry_audit)
        _atomic_json(payload, output)
        sys.stdout.write(json.dumps({"status": "blocked", "output": str(output), "reason": payload["reason"]}, ensure_ascii=False, indent=2) + "\n")
        return 2

    macro_rows = load_macro_from_sqlite(database)
    prices, lineage = load_local_shadow_prices_v5(database)
    coverage = lineage.get("coverage") or {}
    first_month = max(str((coverage.get(asset) or {}).get("first") or "")[:6] for asset in ASSET_ORDER_V5)
    last_month = min(str((coverage.get(asset) or {}).get("last") or "")[:6] for asset in ASSET_ORDER_V5)
    common_span = _month_span(first_month, last_month)
    if common_span < 24:
        snapshot = _blocked_payload(
            f"local_shadow_four_asset_common_history_too_short:{common_span}:first={first_month}:last={last_month}",
            lineage,
            registry_audit,
        )
        _atomic_json(snapshot, output)
        sys.stdout.write(json.dumps({"status": "blocked", "output": str(output), "reason": snapshot["reason"]}, ensure_ascii=False, indent=2) + "\n")
        return 2
    config = research_shadow_config_v5(first_month, last_month)
    if args.train_end or args.validation_end or args.production:
        config = ResearchConfigV5(
            **{
                **config.__dict__,
                "train_end": args.train_end or config.train_end,
                "validation_end": args.validation_end or config.validation_end,
                "production_mode": bool(args.production),
            }
        )
    try:
        snapshot = build_snapshot_v5(macro_rows, prices, registry=registry, config=config)
        snapshot["quality"]["local_shadow_lineage"] = lineage
    except (ValueError, RuntimeError) as error:
        snapshot = _blocked_payload(str(error), lineage, registry_audit)
    _atomic_json(snapshot, output)
    summary = {
        "status": snapshot["status"],
        "output": str(output),
        "generated_at": snapshot["generated_at"],
        "reason": snapshot.get("reason"),
        "promotion": ((snapshot.get("quality") or {}).get("promotion_gate") or {}).get("status"),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if args.production:
        return 0 if snapshot["status"] == "ready" else 2
    return 0 if snapshot["status"] == "research_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
