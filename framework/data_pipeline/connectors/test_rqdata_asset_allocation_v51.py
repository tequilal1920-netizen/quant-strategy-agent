from __future__ import annotations

import json
from pathlib import Path

import rqdata_asset_allocation_v51 as connector


def test_whitelist_contains_exact_official_names_and_no_secrets() -> None:
    assert connector.RQ_MACRO_FACTORS_V51["pmi_manufacturing"] == "制造业采购经理指数PMI_当月"
    assert connector.RQ_MACRO_FACTORS_V51["dr007"].endswith("DR007:日")
    assert connector.RQ_ASSETS_V51["gold_research_crosscheck"] == "AU9999.SGEX"
    module = Path(connector.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("refresh token", "access token", "password=", "pwd=", "license key"):
        assert forbidden not in module


def test_query_hash_is_order_stable() -> None:
    left = connector._query_hash("probe", {"a": 1, "b": 2})
    right = connector._query_hash("probe", {"b": 2, "a": 1})
    assert left == right


def test_atomic_json_does_not_emit_nan(tmp_path: Path) -> None:
    target = tmp_path / "probe.json"
    connector._atomic_json({"ok": True, "value": None}, target)
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True, "value": None}
