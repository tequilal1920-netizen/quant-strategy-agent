"""Atomically apply v5.1.1 governance hardening to a generated shadow JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from snapshot_governance_v51 import harden_shadow_snapshot_v51


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input).resolve()
    destination = Path(args.output).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    hardened = harden_shadow_snapshot_v51(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(hardened, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(destination)
    print(
        json.dumps(
            {
                "status": hardened["status"],
                "promotion": hardened["quality"]["promotion_gate"]["status"],
                "model_hash": hardened["model_hash"],
                "output": str(destination),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
