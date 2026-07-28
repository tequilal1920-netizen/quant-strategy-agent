from __future__ import annotations

import json
import sys
from pathlib import Path


app_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(app_root))

import research_evidence_backend as backend  # noqa: E402


routes = [
    "allocation:strategy",
    "liquidity:retail",
    "rotation:industry",
    "factorlab:dashboard",
    "factorlab:strategy",
    "technical:learning",
    "portfolio:solve",
]
rows = []
for route in routes:
    payload = backend.build(route)
    rows.append(
        {
            "route": route,
            "status": payload.get("status"),
            "layers": len(payload.get("layers") or []),
            "mechanism_nodes": len((payload.get("mechanism") or {}).get("nodes") or []),
            "visual_blocks": sorted((payload.get("visuals") or {}).keys()),
            "bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        }
    )

print(json.dumps({"status": "ok", "routes": rows}, ensure_ascii=False))
