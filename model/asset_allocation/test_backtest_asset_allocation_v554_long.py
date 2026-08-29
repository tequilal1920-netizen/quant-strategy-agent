from __future__ import annotations

import copy

import numpy as np
import pytest

from backtest_asset_allocation_v554_long import (
    GRID_HASH_V554,
    PRETEST_YEARS_V554,
    TEST_START_V554,
    _canonical_hash,
    _pretest_panel,
    _validate_panel,
    build_research_v554,
    candidate_grid_v554,
    select_pretest_v554,
)


def panel() -> dict:
    rng = np.random.default_rng(5450)
    months = []
    year, month = 2015, 1
    for _ in range(90):
        months.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    returns = rng.normal([.007, .003, .005, .004], [.04, .012, .035, .04], size=(90, 4))
    payload = {
        "schema_version": "asset-allocation-panel-v553-T2-signal-self-financing-act360-d2-research/1.0",
        "asset_order": ["equity", "bond", "gold", "commodity"],
        "months": months,
        "returns": returns.tolist(),
        "levels": (100 * np.cumprod(1 + returns, axis=0)).tolist(),
        "data_quality": {"status": "D2_research_not_D3", "blocking_items": ["second_source"]},
        "source_lineage": {
            "provider": "synthetic_test_only",
            "source_content_sha256": "E0E7001141EED0C8D1A46E58F47C875ADBC628BF62B491773C5A8BBF71D4F731",
            "trading_parameters_content_sha256": "7D103E6EFB4923C34BA95DAD4B1A1E7F767CB41E88C697E6768938C4CA33436C",
            "commodity_builder": "commodity_self_financing_v553",
            "collateral_source_method": "get_interbank_offered_rate.Shibor_ON_fallback",
            "collateral_day_count": "ACT/360",
        },
        "deployment_allowed": False,
    }
    payload["content_sha256"] = _canonical_hash(payload)
    return payload


def row(mode: str, *, good: bool = True) -> dict:
    relative = {
        "months": 24,
        "annual_return": .08,
        "annual_excess_return": .01 if good else -.01,
        "information_ratio": .6 if good else -.2,
        "sharpe": 1.1,
        "sharpe_improvement": .1 if good else -.1,
        "max_active_drawdown": -.01,
        "max_drawdown": -.08,
        "average_turnover": .02,
    }
    absolute = {
        **relative,
        "annual_return": .06 if good else -.01,
        "sharpe": .8 if good else -.1,
        "max_drawdown": -.08 if good else -.20,
        "annual_excess_return": -.20,
        "information_ratio": -.4,
        "sharpe_improvement": -.2,
    }
    metrics = relative if mode == "benchmark_relative" else absolute
    annual = {**metrics, "months": 12}
    return {
        "spec": {"id": f"test-{mode}", "mode": mode},
        "metrics": {"train": dict(metrics), "validation": dict(metrics)},
        "pretest_calendar_years": {year: dict(annual) for year in PRETEST_YEARS_V554},
    }


def test_grid_is_exactly_eight_and_hash_locked():
    grid = candidate_grid_v554()
    assert [item["id"] for item in grid] == [
        "V554-REL-01", "V554-REL-02", "V554-REL-03", "V554-REL-04",
        "V554-ABS-01", "V554-ABS-02", "V554-ABS-03", "V554-ABS-04",
    ]
    assert _canonical_hash(grid) == GRID_HASH_V554


def test_panel_calendar_and_hash_are_fail_closed():
    payload = panel()
    _validate_panel(payload, allow_test=True)
    changed = copy.deepcopy(payload)
    changed["months"][1] = "201503"
    changed["content_sha256"] = _canonical_hash({key: value for key, value in changed.items() if key != "content_sha256"})
    with pytest.raises(ValueError, match="months_not_unique_contiguous"):
        _validate_panel(changed, allow_test=True)


def test_panel_lineage_rejects_wrong_day_count_or_upstream_hash():
    for key, value in (("collateral_day_count", "ACT/365"), ("source_content_sha256", "0" * 64)):
        changed = panel()
        changed["source_lineage"][key] = value
        changed["content_sha256"] = _canonical_hash({name: item for name, item in changed.items() if name != "content_sha256"})
        with pytest.raises(ValueError, match="source_lineage_invalid"):
            _validate_panel(changed, allow_test=True)


def test_pretest_simulator_never_receives_test_months():
    with pytest.raises(ValueError, match="received_test_month"):
        _validate_panel(panel(), allow_test=False)
    pretest = _pretest_panel(panel())
    months, _ = _validate_panel(pretest, allow_test=False)
    assert max(months) < TEST_START_V554


def test_selector_rejects_wrong_years_or_test_metrics():
    candidate = row("benchmark_relative")
    changed = copy.deepcopy(candidate)
    changed["pretest_calendar_years"]["2022"] = changed["pretest_calendar_years"].pop("2021")
    with pytest.raises(ValueError, match="calendar_boundary"):
        select_pretest_v554([changed], "benchmark_relative")
    changed = copy.deepcopy(candidate)
    changed["metrics"]["test"] = changed["metrics"]["validation"]
    with pytest.raises(ValueError, match="non_pretest_metrics"):
        select_pretest_v554([changed], "benchmark_relative")


def test_absolute_selection_uses_own_sharpe_not_policy_excess():
    candidate = row("absolute_no_benchmark", good=True)
    selected, board = select_pretest_v554([candidate], "absolute_no_benchmark")
    assert selected == "test-absolute_no_benchmark"
    assert board[0]["eligible"] is True
    assert board[0]["validation"]["annual_excess_return"] < 0.0


def test_relative_selection_still_requires_positive_active_evidence():
    selected, board = select_pretest_v554([row("benchmark_relative", good=False)], "benchmark_relative")
    assert selected is None
    assert board[0]["eligible"] is False


def test_test_counterfactual_cannot_change_pretest_selected_or_board():
    original = panel()
    base_result = build_research_v554(original)
    changed = copy.deepcopy(original)
    for index, month in enumerate(changed["months"]):
        if month >= TEST_START_V554:
            changed["returns"][index] = [.5, -.5, .4, -.4]
            changed["levels"][index] = [200.0, 50.0, 180.0, 60.0]
    changed["content_sha256"] = _canonical_hash({key: value for key, value in changed.items() if key != "content_sha256"})
    counter = build_research_v554(changed)
    assert base_result["selected_ids_pretest"] == counter["selected_ids_pretest"]
    assert base_result["selection_boards"] == counter["selection_boards"]
    assert base_result["selection_uses_test"] is False


def test_result_is_byte_reproducible_and_equal_weight_absent():
    first = build_research_v554(panel())
    second = build_research_v554(panel())
    assert first == second
    assert _canonical_hash({key: value for key, value in first.items() if key != "content_sha256"}) == first["content_sha256"]
    assert first["equal_weight_role"] == "absent_from_optimizer_selection_active_metrics_and_current_target"


def test_pretest_panel_physically_prunes_nested_test_ledger_and_nav():
    payload = panel()
    payload["commodity"] = {
        "daily_ledger": [
            {"date": "2021-12-31", "nav": 1.0},
            {"date": "2022-01-04", "nav": 9.0},
        ],
        "monthly_nav": {"202112": 1.0, "202201": 9.0},
    }
    payload["content_sha256"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )
    stripped = _pretest_panel(payload)
    assert [row["date"] for row in stripped["commodity"]["daily_ledger"]] == ["2021-12-31"]
    assert stripped["commodity"]["monthly_nav"] == {"202112": 1.0}