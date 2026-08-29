"""Low-quota D3/PIT probe contract for asset-allocation factors.

This file is a safe runner skeleton: it never stores credentials, never prints
tokens/passwords, and caps each requested series probe to a tiny sample.  It is
used to produce evidence for the v6.2 registry before any factor can be
promoted from "catalogued" to "production admitted".

Real provider clients should be wired through environment variables or local
credential stores only.  Do not paste secrets into this file or generated JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_d3_pit_probe_v62.json"
MAX_ROWS_PER_SERIES = 10
SECRET_KEYS = ("token", "password", "passwd", "pwd", "secret", "license", "refresh", "access")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in SECRET_KEYS):
                return True
            if _contains_secret_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if any(secret in str(key).lower() for secret in SECRET_KEYS):
            raise ValueError(f"secret_field_forbidden:{key}")
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = value
        else:
            clean[str(key)] = str(value)
    return clean


def build_probe_evidence(provider: str, series_id: str, rows: list[Mapping[str, Any]], query: Mapping[str, Any]) -> dict[str, Any]:
    if len(rows) > MAX_ROWS_PER_SERIES:
        raise ValueError("probe_row_limit_exceeded")
    payload_rows = [_safe_row(row) for row in rows]
    query_clean = _safe_row(query)
    payload = {
        "schema_version": "asset-allocation-d3-pit-probe/6.2",
        "provider": provider,
        "series_id": series_id,
        "retrieved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "row_count": len(payload_rows),
        "max_rows_per_series": MAX_ROWS_PER_SERIES,
        "query_hash": _hash(query_clean),
        "source_payload_hash": _hash(payload_rows),
        "rows": payload_rows,
        "admission_ready": False,
        "missing_for_d3_pit": [
            "release_time",
            "available_time",
            "vintage_or_revision_id",
            "cross_provider_hash",
            "transformation_hash",
        ],
    }
    if _contains_secret_key(payload):
        raise ValueError("probe_payload_contains_secret")
    payload["content_sha256"] = _hash(payload)
    return payload


def offline_contract(provider: str, series_id: str) -> dict[str, Any]:
    return build_probe_evidence(
        provider=provider,
        series_id=series_id,
        rows=[],
        query={
            "mode": "offline_contract_only",
            "provider": provider,
            "series_id": series_id,
            "row_limit": MAX_ROWS_PER_SERIES,
            "credential_source": "environment_or_local_keyring_only",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=os.environ.get("ASSET_D3_PROVIDER", "Wind"))
    parser.add_argument("--series-id", default=os.environ.get("ASSET_D3_SERIES_ID", "UNSET"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    payload = offline_contract(str(args.provider), str(args.series_id))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output), "content_sha256": payload["content_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
