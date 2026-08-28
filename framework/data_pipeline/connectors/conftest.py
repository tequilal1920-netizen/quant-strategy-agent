"""Keep connector tests importable from both repo root and this directory."""

from __future__ import annotations

import sys
from pathlib import Path


CONNECTOR_ROOT = Path(__file__).resolve().parent
if str(CONNECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(CONNECTOR_ROOT))
