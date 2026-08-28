from __future__ import annotations

import copy
import hashlib
import json

import pytest

from commodity_self_financing_v543 import (
    EXCLUDED_V543,
    OUTPUT_SCHEMA_V543,
    UNDERLYINGS_V543,
    _canonical_hash,
    _commission_cost,
    _tick_grid_by_root,
    validate_inputs_v543,
)


def _seal(payload: dict) -> dict:
    payload["content_sha256"] = _canonical_hash(payload)
    return payload


def source() -> dict:
    dominant = {
        root: [{"trade_date": "2013-01-02", "contract": f"{root}1305"}]
        for root in UNDERLYINGS_V543
    }
    return _seal(
        {
            "schema_version": "asset-allocation-rqdata-v541-freeze/1.0",
            "credentials_in_output": False,
            "commodity_raw": {
                "underlyings": list(UNDERLYINGS_V543),
                "excluded": list(EXCLUDED_V543),
                "gold_weight": 0.0,
                "precious_metals_weight": 0.0,
                "continuous_adjusted_price_used_for_PnL": False,
                "dominant_rule": "rule0_OI_1.1x_T_minus_1_effective_next_day",
                "dominant": dominant,
            },
        }
    )


def trading(src: dict) -> dict:
    return _seal(
        {
            "schema_version": "rqdata-futures-trading-parameters-v541/1.0",
            "source_content_sha256": src["content_sha256"],
            "rows": [],
        }
    )


def test_input_hashes_and_lineage_are_fail_closed():
    src = source()
    fees = trading(src)
    validate_inputs_v543(
        src,
        fees,
        expected_source_hash=src["content_sha256"],
        expected_trading_hash=fees["content_sha256"],
    )
    changed = copy.deepcopy(src)
    changed["commodity_raw"]["gold_weight"] = 0.01
    with pytest.raises(ValueError, match="content_hash"):
        validate_inputs_v543(
            changed,
            fees,
            expected_source_hash=src["content_sha256"],
            expected_trading_hash=fees["content_sha256"],
        )


def test_precious_metals_or_continuous_price_cannot_enter():
    src = source()
    src["commodity_raw"]["underlyings"][0] = "AU"
    src = _seal({key: value for key, value in src.items() if key != "content_sha256"})
    fees = trading(src)
    with pytest.raises(ValueError, match="universe"):
        validate_inputs_v543(
            src,
            fees,
            expected_source_hash=src["content_sha256"],
            expected_trading_hash=fees["content_sha256"],
        )


def test_tick_grid_uses_only_preregistered_calibration_window():
    rows = []
    for index in range(25):
        rows.append(
            {
                "order_book_id": "A1305",
                "date": f"2013-01-{index + 1:02d}",
                "prev_settlement": 100.0,
                "settlement": 100.0 + (index % 3 + 1),
            }
        )
    contract_to_root = {"A1305": "A"}
    with pytest.raises(ValueError, match="tick_calibration_insufficient"):
        _tick_grid_by_root(rows, contract_to_root, "2013-03-31")


def test_commission_formulas_use_money_or_real_contract_count():
    by_money = {
        "commission_type": "by_money",
        "open_commission": 0.0001,
        "close_commission": 0.0002,
    }
    by_volume = {
        "commission_type": "by_volume",
        "open_commission": 2.0,
        "close_commission": 3.0,
    }
    assert _commission_cost(0.2, "open", by_money, 100.0, 10.0) == pytest.approx(0.00002)
    assert _commission_cost(0.2, "close", by_volume, 100.0, 10.0) == pytest.approx(0.0006)


def test_output_contract_never_allows_self_promotion():
    assert OUTPUT_SCHEMA_V543.endswith("d2-research/1.0")
    assert set(EXCLUDED_V543) == {"AU", "AG"}
