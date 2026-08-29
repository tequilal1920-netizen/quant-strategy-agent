"""Atomically apply the v5.1.2 truthful macro-cycle display gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from snapshot_truth_gate_v51 import apply_truth_gate_v51


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input).resolve()
    destination = Path(args.output).resolve()
    payload = apply_truth_gate_v51(json.loads(source.read_text(encoding="utf-8")))
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(destination)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "gated": payload["truth_gate"]["macro_cycle_payloads_gated"],
                "model_hash": payload["model_hash"],
                "output": str(destination),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
