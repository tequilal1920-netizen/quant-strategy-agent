"""Public credential-safe facade for the governed RQData v5.3.8 panel.

The implementation lives in the adjacent private core module.  This facade
adds a recursive input scrubber: credential-like field names are rejected
before any commodity payload can be validated, queried, hashed, or exported.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

import _rqdata_asset_panel_v538_core as _core
from _rqdata_asset_panel_v538_core import *  # noqa: F401,F403


_canonical_hash = _core._canonical_hash
_atomic_json = _core._atomic_json
_load_rqdata_safely = _core._load_rqdata_safely
_CORE_VALIDATE_COMMODITY = _core.validate_audited_commodity_input
_CORE_COLLECT_PANEL = _core.collect_rqdata_asset_panel_v538


_SENSITIVE_FIELD_RE = re.compile(
    r"(?:password|passwd|pwd|token|licen[cs]e|secret|credential|authorization|api[_-]?key|username)",
    re.IGNORECASE,
)


def _reject_sensitive_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SENSITIVE_FIELD_RE.search(str(key)):
                raise CommodityAuditError("credential_material_not_allowed")
            _reject_sensitive_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_fields(child)


def validate_audited_commodity_input(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    _reject_sensitive_fields(payload)
    return _CORE_VALIDATE_COMMODITY(payload)


def collect_rqdata_asset_panel_v538(
    start_date: str,
    end_date: str,
    *,
    audited_commodity: Mapping[str, Any] | None,
    rqdatac_module: Any | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    _reject_sensitive_fields(audited_commodity)
    return _CORE_COLLECT_PANEL(
        start_date,
        end_date,
        audited_commodity=audited_commodity,
        rqdatac_module=rqdatac_module,
        retrieved_at=retrieved_at,
    )
def main(argv: Any = None) -> int:
    original_validator = _core.validate_audited_commodity_input
    original_collector = _core.collect_rqdata_asset_panel_v538
    try:
        _core.validate_audited_commodity_input = validate_audited_commodity_input
        _core.collect_rqdata_asset_panel_v538 = collect_rqdata_asset_panel_v538
        return _core.main(argv)
    finally:
        _core.validate_audited_commodity_input = original_validator
        _core.collect_rqdata_asset_panel_v538 = original_collector


if __name__ == "__main__":
    raise SystemExit(main())
