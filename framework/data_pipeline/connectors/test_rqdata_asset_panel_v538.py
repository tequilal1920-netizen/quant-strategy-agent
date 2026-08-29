from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rqdata_asset_panel_v538 as connector


class FakeFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def reset_index(self) -> "FakeFrame":
        return self

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return list(self._rows)


class FakeRQData:
    def __init__(self) -> None:
        self.initialized = False
        self.price_calls: list[str] = []

    def init(self) -> None:
        self.initialized = True

    def instruments(self, code: str) -> SimpleNamespace:
        return SimpleNamespace(
            order_book_id=code,
            symbol={
                "H00300.INDX": "300收益",
                "H11006.XSHG": "中证国债指数",
                "AU9999.SGEX": "黄金9999",
            }[code],
            type="INDX" if code != "AU9999.SGEX" else "Spot",
            listed_date="2005-04-08",
            de_listed_date="0000-00-00",
            currency="CNY",
            exchange="XSHG",
            underlying_symbol=None,
            contract_multiplier=10 if code == "AU9999.SGEX" else None,
            base_date="2004-12-31",
            base_point=1000,
        )

    def get_price(self, code: str, **kwargs: object) -> FakeFrame:
        assert kwargs["frequency"] == "1d"
        assert kwargs["fields"] == ["close"]
        assert kwargs["adjust_type"] == "none"
        self.price_calls.append(code)
        offset = {"H00300.INDX": 0.0, "H11006.XSHG": 1000.0, "AU9999.SGEX": 2000.0}[code]
        return FakeFrame(
            [
                {"date": "2013-01-04", "close": 100.0 + offset},
                {"date": "2013-01-31", "close": 101.0 + offset},
                {"date": "2013-02-01", "close": 102.0 + offset},
                {"date": "2013-02-28", "close": 103.0 + offset},
            ]
        )


def audited_commodity() -> dict[str, object]:
    rows = [
        {"date": "2013-01-31", "level": 501.0},
        {"date": "2013-02-28", "level": 503.0},
    ]
    return {
        "schema_version": connector.COMMODITY_INPUT_SCHEMA,
        "asset_class": "commodity_ex_precious_metals",
        "series_id": "AUDITED-COMMODITY-EX-PM-TR",
        "provider": "independent-governed-pipeline",
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


def test_fixed_authoritative_codes_and_semantics() -> None:
    assert tuple(item.code for item in connector.RQDATA_RESEARCH_SERIES_V538) == (
        "H00300.INDX",
        "H11006.XSHG",
        "AU9999.SGEX",
    )
    bond = connector.RQDATA_RESEARCH_SERIES_V538[1]
    assert "not H01006" in bond.governance_note
    assert "not ChinaBond CBA00601" in bond.governance_note


def test_missing_commodity_fails_before_rqdata_initialization() -> None:
    fake = FakeRQData()
    with pytest.raises(connector.MissingAuditedCommoditySeries):
        connector.collect_rqdata_asset_panel_v538(
            "2013-01-01",
            "2013-02-28",
            audited_commodity=None,
            rqdatac_module=fake,
        )
    assert fake.initialized is False
    assert fake.price_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gold_weight", 0.01),
        ("precious_metals_weight", 0.01),
        ("gold_excluded", False),
        ("precious_metals_excluded", False),
        ("back_adjusted_continuous_prices_used_for_pnl", True),
    ],
)
def test_contaminated_or_back_adjusted_commodity_is_rejected(field: str, value: object) -> None:
    payload = audited_commodity()
    payload["methodology"][field] = value  # type: ignore[index]
    with pytest.raises(connector.CommodityAuditError):
        connector.validate_audited_commodity_input(payload)


def test_four_asset_panel_uses_last_real_observation_and_only_three_rq_codes() -> None:
    fake = FakeRQData()
    result = connector.collect_rqdata_asset_panel_v538(
        "2013-01-01",
        "2013-02-28",
        audited_commodity=audited_commodity(),
        rqdatac_module=fake,
        retrieved_at="2026-08-13T12:30:00Z",
    )
    assert fake.initialized is True
    assert fake.price_calls == ["H00300.INDX", "H11006.XSHG", "AU9999.SGEX"]
    assert len(result["panel"]) == 2
    january = result["panel"][0]
    assert january["month_end"] == "2013-01-31"
    assert january["equity_total_return_level"] == 101.0
    assert january["government_bond_reinvestment_level"] == 1101.0
    assert january["rmb_gold_spot_level"] == 2101.0
    assert january["commodity_ex_precious_metals_total_return_level"] == 501.0
    assert result["panel_policy"]["commodity_direct_rqdata_substitution_allowed"] is False
    assert result["panel_policy"]["broad_commodity_with_precious_metals_allowed"] is False
    assert len(result["panel_content_sha256"]) == 64
    for source in result["source_series"].values():
        assert len(source["query_sha256"]) == 64
        assert source["governance_grade"] == "D2_candidate_not_D3"


def test_query_hash_is_stable_and_commodity_content_hash_is_enforced() -> None:
    assert connector._canonical_hash({"a": 1, "b": 2}) == connector._canonical_hash(
        {"b": 2, "a": 1}
    )
    payload = audited_commodity()
    payload["rows"][0]["level"] = 999.0  # type: ignore[index]
    with pytest.raises(connector.CommodityAuditError, match="content_sha256_mismatch"):
        connector.validate_audited_commodity_input(payload)


def test_month_gap_fails_closed() -> None:
    payload = audited_commodity()
    payload["rows"] = payload["rows"][:1]  # type: ignore[index]
    payload["content_sha256"] = connector._canonical_hash(payload["rows"])
    fake = FakeRQData()
    with pytest.raises(connector.CoverageError):
        connector.collect_rqdata_asset_panel_v538(
            "2013-01-01",
            "2013-02-28",
            audited_commodity=payload,
            rqdatac_module=fake,
        )
    assert fake.initialized is False


def test_atomic_output_is_strict_json_and_contains_no_credential_values(tmp_path: Path) -> None:
    fake = FakeRQData()
    result = connector.collect_rqdata_asset_panel_v538(
        "2013-01-01",
        "2013-02-28",
        audited_commodity=audited_commodity(),
        rqdatac_module=fake,
    )
    destination = tmp_path / "asset-panel.json"
    connector._atomic_json(result, destination)
    decoded = json.loads(destination.read_text(encoding="utf-8"))
    assert decoded["governance"]["credentials_collected"] is False
    assert decoded["governance"]["credentials_stored"] is False
    assert "NaN" not in destination.read_text(encoding="utf-8")


def test_cli_requires_audited_commodity_input() -> None:
    with pytest.raises(SystemExit):
        connector.parse_args(["--output", "panel.json"])
