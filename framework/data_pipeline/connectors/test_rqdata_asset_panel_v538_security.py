from __future__ import annotations

import inspect
from pathlib import Path

import rqdata_asset_panel_v538 as connector


def _commodity_payload() -> dict[str, object]:
    rows = [{"date": "2013-01-31", "level": 100.0}]
    return {
        "schema_version": connector.COMMODITY_INPUT_SCHEMA,
        "asset_class": "commodity_ex_precious_metals",
        "series_id": "AUDITED-EX-PM-TR",
        "provider": "governed-series-provider",
        "retrieved_at": "2026-08-13T12:00:00Z",
        "query_sha256": "a" * 64,
        "content_sha256": connector._canonical_hash(rows),
        "methodology": {
            "version": "1.0.0",
            "construction_type": "t_minus_1_real_contract_self_financing",
            "return_semantics": "fully_collateralized_total_return",
            "gold_weight": 0.0,
            "precious_metals_weight": 0.0,
            "gold_excluded": True,
            "precious_metals_excluded": True,
            "t_minus_1_information_only": True,
            "fully_collateralized": True,
            "back_adjusted_continuous_prices_used_for_pnl": False,
        },
        "audit": {"status": "D3", "evidence_sha256": "b" * 64},
        "rows": rows,
    }


def test_connector_has_no_environment_or_credential_parameters() -> None:
    module_text = Path(connector.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in module_text
    assert "getenv(" not in module_text
    signature = inspect.signature(connector.collect_rqdata_asset_panel_v538)
    lowered = " ".join(signature.parameters).lower()
    for forbidden in ("token", "password", "license", "secret", "credential", "username"):
        assert forbidden not in lowered


def test_unrecognized_top_level_fields_are_not_copied_to_output() -> None:
    payload = _commodity_payload()
    payload["opaque_runtime_material"] = "must-not-be-emitted"
    normalized = connector.validate_audited_commodity_input(payload)
    assert "opaque_runtime_material" not in normalized
    assert "must-not-be-emitted" not in repr(normalized)


def test_cli_error_payload_never_contains_provider_exception_text(capsys: object) -> None:
    class BrokenRQData:
        def init(self) -> None:
            raise RuntimeError("sensitive provider runtime detail")

    try:
        connector._load_rqdata_safely(BrokenRQData())
    except connector.ProviderAccessError as exc:
        assert str(exc) == "rqdata_provider_access_failed"
    else:
        raise AssertionError("provider initialization failure was not fail-closed")
