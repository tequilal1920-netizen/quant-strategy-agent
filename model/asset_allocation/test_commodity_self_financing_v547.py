from __future__ import annotations

import inspect

import pytest

from commodity_self_financing_v547 import _execution_price


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

from commodity_self_financing_v547 import construct_panel_v547


def _minimal_drift_step(start, root_return, collateral_return, cost):
    portfolio_return = sum(start[key] * root_return[key] for key in start) + collateral_return - cost
    end = {key: start[key] * (1.0 + root_return[key]) / (1.0 + portfolio_return) for key in start}
    return portfolio_return, end


def test_between_rebalances_exposure_drifts_without_hidden_rebalancing():
    start = {"A": .50, "C": .50}
    portfolio_return, end = _minimal_drift_step(start, {"A": .10, "C": 0.0}, 0.0, 0.0)
    assert portfolio_return == pytest.approx(.05)
    assert end["A"] == pytest.approx(.50 * 1.10 / 1.05)
    assert end["C"] == pytest.approx(.50 / 1.05)
    assert end["A"] > start["A"]


def test_collateral_contract_is_previous_date_not_interval_end():
    source = inspect.getsource(construct_panel_v547)
    assert "collateral_rows[collateral_index][0] <= previous_date" in source
    assert "collateral_rows[collateral_index][0] < when" not in source


def test_no_daily_constant_weight_return_formula_remains():
    source = inspect.getsource(construct_panel_v547)
    assert "start_root_exposure[root] * root_returns[root]" in source
    assert "target[root] * root_returns[root]" not in source
    assert "previous_exposure = end_exposure" in source