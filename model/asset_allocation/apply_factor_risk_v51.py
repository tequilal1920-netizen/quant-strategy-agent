"""Attach the v5.1.3 factor-risk audit to an existing governed snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from snapshot_factor_risk_v51 import attach_factor_risk_audit_v51


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    source = json.loads(arguments.input.read_text(encoding="utf-8"))
    result = attach_factor_risk_audit_v51(source)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = arguments.output.with_suffix(arguments.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(arguments.output)


if __name__ == "__main__":
    main()
