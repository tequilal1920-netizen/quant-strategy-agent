from __future__ import annotations

import inspect

import pytest

from commodity_self_financing_v553 import (
    _validated_shibor_collateral_rows_v553,
    construct_panel_v553,
)


def test_T_minus_1_settlement_uses_T_minus_2_information():
    source = inspect.getsource(construct_panel_v553)
    assert "dominant_effective_date = previous_date" in source
    assert "calendar[calendar_index - 2]" in source
    assert "values[:-1]" in source
    assert '"information_cutoff_date": information_cutoff_date' in source


def test_collateral_is_known_at_execution_and_cost_paid_before_accrual():
    source = inspect.getsource(construct_panel_v553)
    assert "collateral_rows[collateral_index][0] <= previous_date" in source
    assert "futures_return + (1.0 - total_cost) * collateral_return - total_cost" in source


def test_monthly_positions_drift_and_no_constant_daily_target_return():
    source = inspect.getsource(construct_panel_v553)
    assert "start_root_exposure[root] * root_returns[root]" in source
    assert "target[root] * root_returns[root]" not in source
    assert "previous_exposure = end_exposure" in source

def test_shibor_overnight_is_strictly_act_360_and_source_gated():
    source = inspect.getsource(_validated_shibor_collateral_rows_v553) + inspect.getsource(construct_panel_v553)
    assert 'collateral_method != "get_interbank_offered_rate.Shibor_ON_fallback"' in source
    assert "day_count / collateral_day_count_denominator" in source
    assert "return rows, collateral_method, 360.0" in source
    assert "day_count / 365" not in source

def test_shibor_rows_reject_dr001_both_missing_and_duplicate_dates():
    valid = {"method": "get_interbank_offered_rate.Shibor_ON_fallback", "daily": [{"date": "2026-01-02", "ON": 1.5}]}
    rows, method, denominator = _validated_shibor_collateral_rows_v553(valid)
    assert rows == [("2026-01-02", 1.5)]
    assert method == valid["method"] and denominator == 360.0
    bad_rows = [
        [{"date": "2026-01-02", "DR001": 1.5}],
        [{"date": "2026-01-02", "ON": 1.5, "DR001": 1.4}],
        [{"date": "2026-01-02"}],
    ]
    for daily in bad_rows:
        with pytest.raises(ValueError, match="row_schema_invalid"):
            _validated_shibor_collateral_rows_v553({**valid, "daily": daily})
    with pytest.raises(ValueError, match="dates_not_unique"):
        _validated_shibor_collateral_rows_v553({**valid, "daily": valid["daily"] * 2})