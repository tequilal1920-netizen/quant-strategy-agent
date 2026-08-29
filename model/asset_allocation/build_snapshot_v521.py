"""Build the validation-governed v5.2.1 dual-policy shadow snapshot."""

from __future__ import annotations

import json
import sys

from asset_allocation_v521 import apply_validation_governance_v521
import build_snapshot_v52 as raw_builder


def main() -> int:
    args = raw_builder.parse_args()
    original = raw_builder.build_snapshot_v52

    def governed(*build_args, **build_kwargs):
        return apply_validation_governance_v521(
            original(*build_args, **build_kwargs)
        )

    raw_builder.build_snapshot_v52 = governed
    try:
        return raw_builder.main()
    finally:
        raw_builder.build_snapshot_v52 = original


if __name__ == "__main__":
    raise SystemExit(main())
