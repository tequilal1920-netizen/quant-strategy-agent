from __future__ import annotations

import copy
import json
from datetime import date, timedelta

import numpy as np
import pytest

import backtest_asset_allocation_v555_legacy_direct_research as v555
from backtest_asset_allocation_v541_long import LINEAR_COST_BPS_V541, QUADRATIC_COST_V541, _drift


POLICY = np.asarray([.60, .15, .10, .15], dtype=float)
FIXED_TARGET = np.asarray([.58, .17, .10, .15], dtype=float)
SIGNAL_TARGET = np.asarray([.55, .20, .11, .14], dtype=float)


def _months(count: int = 90) -> list[str]:
    output = []
    year, month = 2015, 1
    for _ in range(count):
        output.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return output


def _previous_month(month: str) -> str:
    number = int(month[:4]) * 12 + int(month[4:]) - 2
    return f"{number // 12:04d}{number % 12 + 1:02d}"


def panel() -> dict:
    months = _months()
    rng = np.random.default_rng(5550)
    time = np.arange(len(months), dtype=float)
    deterministic = np.column_stack([
        .007 + .015 * np.sin(time / 4.0),
        .003 + .004 * np.cos(time / 5.0),
        .004 + .012 * np.sin(time / 6.0 + .4),
        .005 + .020 * np.cos(time / 4.5 + .2),
    ])
    returns = deterministic + rng.normal(0.0, [.008, .002, .006, .010], size=(len(months), 4))
    levels = np.empty_like(returns)
    levels[0] = [4000.0, 160.0, 250.0, 1.0]
    for index in range(1, len(months)):
        levels[index] = levels[index - 1] * (1.0 + returns[index])
    base_month = _previous_month(months[0])
    monthly_nav = {base_month: 1.0}
    commodity_nav = 1.0
    for index, month in enumerate(months):
        commodity_nav *= 1.0 + float(returns[index, 3])
        monthly_nav[month] = commodity_nav
    ledger = []
    for ledger_month in [base_month, *months]:
        year, month = int(ledger_month[:4]), int(ledger_month[4:])
        effective = date(year, month, 20)
        execution = effective - timedelta(days=1)
        cutoff = effective - timedelta(days=2)
        ledger.append({
            "date": effective.isoformat(),
            "execution_date": execution.isoformat(),
            "information_cutoff_date": cutoff.isoformat(),
            "dominant_mapping_effective_date": execution.isoformat(),
            "return": 0.0,
            "collateral_return": 0.0001,
            "commission_cost": 0.0,
            "half_tick_slippage_cost": 0.0,
            "traded_notional": 0.0,
            "nav": monthly_nav[ledger_month],
            "trades": [],
        })
    direct = {
        "equity": {"code": "H00300.INDX", "daily_sha256": "1" * 64},
        "bond": {"code": "H11006.XSHG", "daily_sha256": "2" * 64},
        "gold": {"code": "AU9999.SGEX", "daily_sha256": "3" * 64},
    }
    payload = {
        "schema_version": v555.PANEL_SCHEMA_V555,
        "asset_order": ["equity", "bond", "gold", "commodity"],
        "months": months,
        "returns": returns.tolist(),
        "levels": levels.tolist(),
        "level_base_month": base_month,
        "data_quality": {"status": "D2_research_not_D3", "blocking_items": ["D3_second_source"]},
        "source_lineage": {
            "provider": "RQData",
            "source_content_sha256": "A" * 64,
            "trading_parameters_content_sha256": "B" * 64,
            "direct_series": direct,
            "commodity_builder": "commodity_self_financing_v553",
            "collateral_source_method": "get_interbank_offered_rate.Shibor_ON_fallback",
            "collateral_day_count": "ACT/360",
        },
        "commodity": {
            "collateral": {
                "day_count": "ACT/360",
                "source_method": "get_interbank_offered_rate.Shibor_ON_fallback",
            },
            "continuous_adjusted_price_used_for_PnL": False,
            "precious_metals_weight": 0.0,
            "gold_weight": 0.0,
            "excluded_underlyings": ["AU", "AG"],
            "underlyings": list(v555.EXPECTED_COMMODITY_ROOTS_V555),
            "position_accounting": {
                "dominant_and_volatility_information_cutoff": "T_minus_2",
                "execution_price": "T_minus_1_settlement",
                "implicit_daily_rebalancing": False,
            },
            "daily_ledger": ledger,
            "monthly_nav": monthly_nav,
        },
        "deployment_allowed": False,
    }
    payload["content_sha256"] = v555._canonical_hash(payload)
    return payload


def _rehash(payload: dict) -> None:
    payload["content_sha256"] = v555._canonical_hash({key: value for key, value in payload.items() if key != "content_sha256"})


def _fake_allocate(window, month_window, previous):
    assert np.asarray(window).shape == (36, 4)
    assert len(month_window) == 36
    return {
        "weights": FIXED_TARGET.tolist(),
        "signal_target_weights": SIGNAL_TARGET.tolist(),
        "raw_signal_strength": [.8, .5, .4, .2],
        "signal_path": "direct_active_alpha",
        "other_inference": {"used": False},
        "posterior_uncertainty_penalty": 0.0,
        "production_cycles": [],
        "macro_truth_gate": {"effective_weight": 0.0},
        "optimizer": {
            "status": "optimal",
            "weights": FIXED_TARGET.tolist(),
            "solver": {"fallback_used": False, "maximum_kkt_residual": 1.0e-10, "solve_time_seconds": 99.0},
        },
    }


@pytest.fixture(autouse=True)
def fast_fixed_direct_stack(monkeypatch):
    monkeypatch.setattr(v555, "_allocate_v555", _fake_allocate)


def test_complete_hash_is_verified_before_any_test_pruning():
    payload = panel()
    test_row = next(row for row in payload["commodity"]["daily_ledger"] if row["date"] >= "2022-01-01")
    test_row["nav"] = 999.0
    with pytest.raises(ValueError, match="content_hash_mismatch"):
        v555.build_research_v555(payload)


def test_corrected_schema_lineage_act360_and_panel_identities_are_fail_closed():
    payload = panel()
    v555._validate_panel(payload, allow_test=True)
    old_schema = copy.deepcopy(payload)
    old_schema["schema_version"] = "asset-allocation-panel-v550-T2-signal-self-financing-d2-research/1.0"
    _rehash(old_schema)
    with pytest.raises(ValueError, match="schema_invalid"):
        v555._validate_panel(old_schema, allow_test=True)
    wrong_day_count = copy.deepcopy(payload)
    wrong_day_count["source_lineage"]["collateral_day_count"] = "ACT/365"
    wrong_day_count["commodity"]["collateral"]["day_count"] = "ACT/365"
    _rehash(wrong_day_count)
    with pytest.raises(ValueError, match="day_count_invalid"):
        v555._validate_panel(wrong_day_count, allow_test=True)
    wrong_level = copy.deepcopy(payload)
    wrong_level["levels"][10][0] *= 1.001
    _rehash(wrong_level)
    with pytest.raises(ValueError, match="level_return_identity"):
        v555._validate_panel(wrong_level, allow_test=True)


def test_pretest_pruning_is_physical_for_months_ledger_and_monthly_nav():
    stripped = v555._pretest_panel(panel())
    assert max(stripped["months"]) == "202112"
    assert max(row["date"][:7].replace("-", "") for row in stripped["commodity"]["daily_ledger"]) == "202112"
    assert max(stripped["commodity"]["monthly_nav"]) == "202112"
    v555._validate_panel(stripped, allow_test=False)


def test_selector_object_contains_only_train_validation_and_four_pretest_years():
    payload = panel()
    pretest = v555._simulate_v555(v555._pretest_panel(payload), allow_test=False)
    selector = v555._selector_object_v555(pretest)
    assert set(selector["metrics"]) == {"train", "validation"}
    assert set(selector["pretest_calendar_years"]) == {"2018", "2019", "2020", "2021"}
    assert selector["candidate_count"] == 1
    assert "test" not in selector["metrics"]


def test_single_candidate_spec_and_nested_signal_hash_are_locked():
    spec = v555.candidate_spec_v555()
    assert spec["candidate_count"] == 1
    assert spec["mode"] == "benchmark_relative"
    assert spec["signal_spec_sha256"] == v555.SPEC_SHA256_V549
    assert v555._canonical_hash(spec) == v555.SPEC_SHA256_V555
    assert spec["inference_contract"] == {
        "path": "direct_active_alpha",
        "other_inference": "mutually_exclusive",
        "posterior_uncertainty_penalty": 0.0,
        "macro_contribution": 0.0,
        "production_cycle_contribution": 0.0,
    }
    result = v555._simulate_v555(v555._pretest_panel(panel()), allow_test=False)
    selector = v555._selector_object_v555(result)
    selector["candidate_spec"]["optimizer_contract"]["max_active_share"] = .20
    with pytest.raises(ValueError, match="not_frozen"):
        v555.select_pretest_v555(selector)


def test_test_counterfactual_cannot_change_pretest_result_selector_or_board():
    original = panel()
    base = v555.build_research_v555(original)
    changed = copy.deepcopy(original)
    first_level = np.asarray(changed["levels"][0], dtype=float)
    for index, month in enumerate(changed["months"]):
        if month >= v555.TEST_START_V555:
            changed["returns"][index][:3] = [.25, -.08, .15]
    recomputed = np.empty((len(changed["months"]), 4), dtype=float)
    recomputed[0] = first_level
    for index in range(1, len(changed["months"])):
        recomputed[index] = recomputed[index - 1] * (1.0 + np.asarray(changed["returns"][index], dtype=float))
    changed["levels"] = recomputed.tolist()
    _rehash(changed)
    counter = v555.build_research_v555(changed)
    assert base["selector"] == counter["selector"]
    assert base["selection_board"] == counter["selection_board"]
    assert base["pretest_result"] == counter["pretest_result"]
    assert base["selection_uses_test"] is counter["selection_uses_test"] is False
    assert base["test_report_revealed_after_candidate_fixed"] != counter["test_report_revealed_after_candidate_fixed"]


def test_signal_t_realizes_t_plus_one_costs_use_drift_and_current_target_is_next_month_end_target():
    payload = panel()
    simulation = v555._simulate_v555(payload, allow_test=True)
    first, second = simulation["returns"][:2]
    assert first["signal_month"] == "201712" and first["month"] == "201801"
    linear = np.asarray(LINEAR_COST_BPS_V541) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541)
    initial_change = FIXED_TARGET - POLICY
    expected_first_cost = float(linear @ np.abs(initial_change) + .5 * quadratic @ (initial_change**2))
    assert first["cost"] == pytest.approx(expected_first_cost)
    assert first["benchmark_cost"] == pytest.approx(0.0)
    months = payload["months"]
    realized_first = np.asarray(payload["returns"][months.index("201801")], dtype=float)
    drifted = _drift(FIXED_TARGET, realized_first)
    second_change = FIXED_TARGET - drifted
    expected_second_cost = float(linear @ np.abs(second_change) + .5 * quadratic @ (second_change**2))
    assert second["cost"] == pytest.approx(expected_second_cost)
    current = v555._current_target_v555(payload, simulation)
    assert current["as_of_month_end"] == payload["months"][-1]
    assert current["target_for_next_realized_month"] is True
    assert list(current["optimized_weights"].values()) == pytest.approx(FIXED_TARGET)
    assert list(current["signal_target_weights"].values()) == pytest.approx(SIGNAL_TARGET)
    assert current["strength_order_strong_to_weak"] == ["equity", "bond", "gold", "commodity"]


def test_calendar_pit_and_commodity_monthly_return_contracts_fail_closed():
    duplicate = panel()
    duplicate["months"][1] = duplicate["months"][0]
    _rehash(duplicate)
    with pytest.raises(ValueError, match="months_not_unique_contiguous"):
        v555._validate_panel(duplicate, allow_test=True)
    pit = panel()
    pit["commodity"]["daily_ledger"][5]["information_cutoff_date"] = pit["commodity"]["daily_ledger"][5]["execution_date"]
    _rehash(pit)
    with pytest.raises(ValueError, match="daily_ledger_PIT_invalid"):
        v555._validate_panel(pit, allow_test=True)
    nav = panel()
    nav["commodity"]["monthly_nav"]["201801"] *= 1.01
    _rehash(nav)
    with pytest.raises(ValueError, match="monthly_nav_return_identity"):
        v555._validate_panel(nav, allow_test=True)


def test_reporter_exception_fails_closed_and_never_changes_governance(monkeypatch):
    payload = panel()
    original = v555._simulate_v555

    def reporter_fails(candidate_panel, *, allow_test):
        if allow_test:
            raise RuntimeError("sealed reporter failure")
        return original(candidate_panel, allow_test=allow_test)

    monkeypatch.setattr(v555, "_simulate_v555", reporter_fails)
    output = v555.build_research_v555(payload)
    assert output["test_report_revealed_after_candidate_fixed"] == {
        "status": "retrospective_reporter_failed_closed",
        "error_code": "RuntimeError",
        "selection_affected": False,
    }
    assert output["governance_label"] == "legacy_transfer_challenger_not_blind_champion"
    assert output["status"] == "research_only"
    assert output["deployment_allowed"] is False
    assert output["promotion_allowed"] is False


def test_output_is_byte_reproducible_finite_and_permanently_not_deployable():
    first = v555.build_research_v555(panel())
    second = v555.build_research_v555(panel())
    assert first == second
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert encoded == json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert v555._canonical_hash({key: value for key, value in first.items() if key != "content_sha256"}) == first["content_sha256"]
    assert first["candidate_count"] == 1
    assert first["selection_uses_test"] is False
    assert first["production_admitted_cycles"] == []
    assert first["macro_blend_effective"] == 0.0
    assert first["other_inference_used"] is False
    assert first["deployment_allowed"] is False
    assert first["promotion_allowed"] is False
