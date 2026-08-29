from __future__ import annotations

import pytest

from commodity_self_financing_v544 import _execution_price


def test_new_contract_executes_at_current_prev_settlement():
    prices = {
        ("A2405", "2024-01-03"): {"prev_settlement": 100.0, "settlement": 101.0},
    }
    value = _execution_price(
        "A2405",
        "A",
        "2024-01-03",
        "2024-01-02",
        {"A": "A2405"},
        prices,
    )
    assert value == 100.0


def test_old_contract_executes_at_previous_day_settlement():
    prices = {
        ("A2401", "2024-01-02"): {"prev_settlement": 98.0, "settlement": 99.0},
    }
    value = _execution_price(
        "A2401",
        "A",
        "2024-01-03",
        "2024-01-02",
        {"A": "A2405"},
        prices,
    )
    assert value == 99.0


def test_missing_previous_day_execution_price_fails_closed():
    with pytest.raises(ValueError, match="old_contract_price_missing"):
        _execution_price(
            "A2401",
            "A",
            "2024-01-03",
            "2024-01-02",
            {"A": "A2405"},
            {},
        )
