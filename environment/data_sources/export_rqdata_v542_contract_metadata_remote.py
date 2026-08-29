"""Bounded, credential-free RQData contract metadata export for v5.4.2.

The source freeze determines the complete contract allow-list.  This exporter
only reads instrument metadata for that fixed list; it does not discover or
scan any additional instruments.  The resulting file is still research-only
until a second-source check promotes the full commodity ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import rqdatac


SOURCE_HASH = "E0E7001141EED0C8D1A46E58F47C875ADBC628BF62B491773C5A8BBF71D4F731"


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _finite_positive(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"v542_contract_metadata_invalid:{label}")
    return number


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if source.get("content_sha256") != SOURCE_HASH:
        raise RuntimeError("v542_source_freeze_hash_mismatch")
    contracts = sorted(
        {
            row["contract"]
            for rows in source["commodity_raw"]["dominant"].values()
            for row in rows
        }
    )
    rqdatac.init()
    instruments = rqdatac.instruments(contracts, market="cn")
    if not isinstance(instruments, (list, tuple)):
        instruments = [instruments]
    rows = []
    for instrument in instruments:
        tick = instrument.tick_size() if callable(getattr(instrument, "tick_size", None)) else getattr(instrument, "tick_size")
        rows.append(
            {
                "order_book_id": str(instrument.order_book_id),
                "underlying_symbol": str(instrument.underlying_symbol).upper(),
                "exchange": str(instrument.exchange),
                "listed_date": str(instrument.listed_date)[:10],
                "de_listed_date": str(instrument.de_listed_date)[:10],
                "contract_multiplier_latest": _finite_positive(
                    instrument.contract_multiplier, "contract_multiplier"
                ),
                "tick_size": _finite_positive(tick, "tick_size"),
            }
        )
    rows.sort(key=lambda row: row["order_book_id"])
    if [row["order_book_id"] for row in rows] != contracts:
        raise RuntimeError("v542_contract_metadata_coverage_mismatch")
    payload = {
        "schema_version": "rqdata-futures-contract-metadata-v542/1.0",
        "provider": "RQData",
        "source_content_sha256": SOURCE_HASH,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "contract_count": len(rows),
        "rows": rows,
        "query": {
            "api": "instruments",
            "scope": "fixed_contract_allowlist_from_source_freeze",
            "fields": [
                "order_book_id",
                "underlying_symbol",
                "exchange",
                "listed_date",
                "de_listed_date",
                "contract_multiplier",
                "tick_size",
            ],
        },
        "credentials_in_output": False,
        "deployment_allowed": False,
    }
    payload["content_sha256"] = _hash(payload)
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    print(
        json.dumps(
            {
                "status": "ok",
                "contract_count": len(rows),
                "content_sha256": payload["content_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
