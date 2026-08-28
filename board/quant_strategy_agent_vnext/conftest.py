"""Scoped pytest fixture for rendering a research-only allocation snapshot.

The production loader correctly refuses non-ready snapshots.  The dedicated
shadow transport test replaces only that loader with the already validated
v5.1 JSON so the unchanged page/API transport can be exercised.  A separate
test asserts that the real production loader continues to return 503.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def inject_v51_shadow_for_transport_only(request, monkeypatch):
    test_class = request.cls
    if test_class is None or test_class.__name__ != "AssetAllocationV51ShadowTransportTest":
        yield
        return
    module = test_class.module
    shadow = Path(module.ALLOCATION_SNAPSHOT_PATH)
    payload = json.loads(shadow.read_text(encoding="utf-8"))
    monkeypatch.setattr(module, "load_allocation_snapshot", lambda: payload)
    yield
