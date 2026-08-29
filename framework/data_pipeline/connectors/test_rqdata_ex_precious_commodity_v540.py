from __future__ import annotations

import copy

import pandas as pd
import pytest

from rqdata_ex_precious_commodity_v540 import (
    EXCLUDED_V540,
    UNDERLYINGS_V540,
    export_raw_contract_ledger_v540,
    validate_raw_contract_ledger_v540,
)


class FakeFutures:
    def get_dominant(self, root, **kwargs):
        assert kwargs["rule"] == 0
        assert kwargs["rank"] == 1
        return pd.Series(
            [f"{root}2401", f"{root}2401"],
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )

    def get_contract_multiplier(self, root, **kwargs):
        index = pd.MultiIndex.from_tuples(
            [(root, pd.Timestamp("2024-01-02")), (root, pd.Timestamp("2024-01-03"))],
            names=["underlying_symbol", "date"],
        )
        return pd.DataFrame(
            {"exchange": ["TEST", "TEST"], "contract_multiplier": [10.0, 10.0]},
            index=index,
        )


class FakeRQ:
    futures = FakeFutures()

    def get_price(self, contracts, **kwargs):
        assert kwargs["adjust_type"] == "none"
        assert set(kwargs["fields"]) == {
            "settlement", "prev_settlement", "open_interest", "volume"
        }
        index = pd.MultiIndex.from_tuples(
            [
                (contracts[0], pd.Timestamp("2024-01-02")),
                (contracts[0], pd.Timestamp("2024-01-03")),
            ],
            names=["order_book_id", "date"],
        )
        return pd.DataFrame(
            {
                "settlement": [100.0, 101.0],
                "prev_settlement": [99.0, 100.0],
                "open_interest": [1000.0, 1100.0],
                "volume": [500.0, 600.0],
            },
            index=index,
        )


def test_export_is_fixed_ex_precious_and_cannot_be_deployed():
    payload = export_raw_contract_ledger_v540(
        FakeRQ(), "2024-01-01", "2024-01-31"
    )
    validate_raw_contract_ledger_v540(payload)
    assert tuple(payload["underlying_universe"]) == UNDERLYINGS_V540
    assert set(EXCLUDED_V540).isdisjoint(payload["underlying_universe"])
    assert payload["gold_weight"] == 0.0
    assert payload["precious_metals_weight"] == 0.0
    assert payload["continuous_adjusted_price_used_for_PnL"] is False
    assert payload["governance"]["construction_allowed"] is False
    assert payload["governance"]["deployment_allowed"] is False
    assert len(payload["query_ledger"]) == len(UNDERLYINGS_V540) * 3


def test_universe_cannot_be_changed_or_contaminated():
    with pytest.raises(ValueError, match="frozen_ex_PM"):
        export_raw_contract_ledger_v540(
            FakeRQ(), "2024-01-01", "2024-01-31", underlyings=["AU"]
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("gold_weight", .01),
        ("precious_metals_weight", .01),
        ("continuous_adjusted_price_used_for_PnL", True),
    ],
)
def test_contamination_or_adjusted_PnL_is_rejected(path, value):
    payload = export_raw_contract_ledger_v540(
        FakeRQ(), "2024-01-01", "2024-01-31"
    )
    changed = copy.deepcopy(payload)
    changed[path] = value
    with pytest.raises(ValueError):
        validate_raw_contract_ledger_v540(changed)


def test_raw_ledger_hash_detects_any_change():
    payload = export_raw_contract_ledger_v540(
        FakeRQ(), "2024-01-01", "2024-01-31"
    )
    changed = copy.deepcopy(payload)
    changed["raw_blocks"][0]["real_contract_daily"][0]["settlement"] += 1.0
    with pytest.raises(ValueError, match="content_hash"):
        validate_raw_contract_ledger_v540(changed)
