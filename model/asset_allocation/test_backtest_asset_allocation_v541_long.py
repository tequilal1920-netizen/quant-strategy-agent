from __future__ import annotations

import copy
import inspect

import numpy as np
import pytest

from backtest_asset_allocation_v541_long import (
    ASSET_ORDER_V541,
    build_research_v541,
    candidate_grid_v541,
    select_pretest_v541,
)


def panel() -> dict:
    rng = np.random.default_rng(5410)
    months = []
    year, month = 2013, 1
    for _ in range(132):
        months.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    returns = rng.normal([.007, .003, .004, .005], [.04, .012, .035, .045], size=(132, 4))
    return {"asset_order": list(ASSET_ORDER_V541), "months": months, "returns": returns.tolist()}


def test_grid_is_frozen_compact_and_two_modes():
    grid = candidate_grid_v541()
    assert len(grid) == 8
    assert len([row for row in grid if row["mode"] == "benchmark_relative"]) == 4
    assert len([row for row in grid if row["mode"] == "absolute_no_benchmark"]) == 4


def test_selector_physically_rejects_test_payload():
    with pytest.raises(ValueError, match="received_test"):
        select_pretest_v541(
            [{"spec": {"id": "x", "mode": "benchmark_relative"}, "metrics": {"train": {}, "validation": {}, "test": {}}, "pretest_calendar_years": {}}]
        )


def test_test_return_perturbation_does_not_change_pretest_selection(monkeypatch):
    # A compact synthetic full run is intentionally expensive enough to test
    # the real selector, but candidate construction remains fixed.
    original = panel()
    base = build_research_v541(original)
    changed = copy.deepcopy(original)
    for index, month in enumerate(changed["months"]):
        if month >= "202201":
            changed["returns"][index] = [0.5, -0.5, 0.4, -0.4]
    counterfactual = build_research_v541(changed)
    assert base["selected_ids_pretest"] == counterfactual["selected_ids_pretest"]
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        assert base["selection_boards"][mode] == counterfactual["selection_boards"][mode]


def test_equal_weight_is_absent_from_source_contract():
    source = inspect.getsource(build_research_v541)
    assert "0.25, 0.25, 0.25, 0.25" not in source
    result = build_research_v541(panel())
    assert result["equal_weight_role"] == "absent_from_optimizer_and_selection"
    assert result["selection_uses_test"] is False
