from __future__ import annotations

import copy

import numpy as np
import pytest

from asset_allocation_v541_stack import ASSET_ORDER_V541
from backtest_asset_allocation_v542_long import (
    TEST_START_V542,
    _simulate_v542,
    build_research_v542,
    candidate_grid_v542,
    select_pretest_v542,
)


def panel() -> dict:
    rng = np.random.default_rng(5420)
    months=[]; year=2013; month=1
    for _ in range(132):
        months.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13: year,month=year+1,1
    returns=rng.normal([.007,.003,.004,.005],[.04,.012,.035,.045],size=(132,4))
    return {"asset_order":list(ASSET_ORDER_V541),"months":months,"returns":returns.tolist()}


def test_grid_is_frozen_eight_candidates():
    assert len(candidate_grid_v542()) == 8


def test_pretest_simulator_physically_rejects_test_months():
    with pytest.raises(ValueError,match="received_test_month"):
        _simulate_v542(panel(),candidate_grid_v542()[0],allow_test=False)


def test_selector_rejects_test_field():
    with pytest.raises(ValueError,match="received_test"):
        select_pretest_v542([{"spec":{"id":"x","mode":"benchmark_relative"},"metrics":{"train":{},"validation":{},"test":{}},"pretest_calendar_years":{}}])


def test_test_counterfactual_cannot_change_board_or_selected_id():
    original=panel(); base=build_research_v542(original)
    changed=copy.deepcopy(original)
    for index,month in enumerate(changed["months"]):
        if month>=TEST_START_V542:
            changed["returns"][index]=[.5,-.5,.4,-.4]
    counter=build_research_v542(changed)
    assert base["selected_ids_pretest"] == counter["selected_ids_pretest"]
    assert base["selection_boards"] == counter["selection_boards"]
    assert base["selector_input_contains_test"] is False
    assert base["selection_uses_test"] is False


def test_equal_weight_is_absent_from_contract():
    result=build_research_v542(panel())
    assert result["equal_weight_role"] == "absent_from_optimizer_selection_and_active_metrics"
