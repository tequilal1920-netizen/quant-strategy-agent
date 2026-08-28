from __future__ import annotations

import inspect

from commodity_self_financing_v550 import construct_panel_v550


def test_T_minus_1_settlement_uses_T_minus_2_information():
    source = inspect.getsource(construct_panel_v550)
    assert "dominant_effective_date = previous_date" in source
    assert "calendar[calendar_index - 2]" in source
    assert "values[:-1]" in source
    assert '"information_cutoff_date": information_cutoff_date' in source


def test_collateral_is_known_at_execution_and_cost_paid_before_accrual():
    source = inspect.getsource(construct_panel_v550)
    assert "collateral_rows[collateral_index][0] <= previous_date" in source
    assert "futures_return + (1.0 - total_cost) * collateral_return - total_cost" in source


def test_monthly_positions_drift_and_no_constant_daily_target_return():
    source = inspect.getsource(construct_panel_v550)
    assert "start_root_exposure[root] * root_returns[root]" in source
    assert "target[root] * root_returns[root]" not in source
    assert "previous_exposure = end_exposure" in source