"""Source mirror for v6.4 asset-allocation snapshot builder.

The formal AI entry keeps a lightweight wrapper so the model code remains
single-sourced under ``model/asset_allocation``.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))

from build_snapshot_v64_daily_excess_governed import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    raise SystemExit(main())
