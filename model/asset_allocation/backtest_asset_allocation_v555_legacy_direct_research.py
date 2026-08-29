"""Governed research-only evaluation of the single frozen B06 Direct challenger.

This runner is deliberately incapable of promoting or deploying the challenger.
It first validates the complete v553 panel and its nested lineage/content hash,
then physically removes every 2022+ panel, commodity-ledger and monthly-NAV row
before producing the train/validation selector object.  The already-observed
2022+ period is opened only by a report-only path after the single candidate has
been fixed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import cvxpy
import numpy as np

# Direct CLI execution places only model/asset_allocation on sys.path, while
# the frozen signal imports framework.backtest. Resolve the repository root
# from this file rather than relying on a caller-specific PYTHONPATH.
PROJECT_ROOT_V555 = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_V555) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_V555))

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v541_stack import ASSET_ORDER_V541, POLICY_WEIGHTS_V541
from asset_allocation_v549_direct_stack import allocate_relative_legacy_direct_v549
from backtest_asset_allocation_v541_long import (
    LINEAR_COST_BPS_V541,
    NO_D3_CYCLES_V541,
    QUADRATIC_COST_V541,
    _drift,
    metrics_v541,
)
from backtest_asset_allocation_v551_long import select_pretest_v551
from legacy_b06_direct_v549 import SPEC_SHA256_V549, frozen_spec_v549


SCHEMA_V555 = "asset-allocation-v555-legacy-direct-research-only/1.0"
SELECTOR_SCHEMA_V555 = "asset-allocation-v555-single-candidate-pretest-selector/1.0"
PANEL_SCHEMA_V555 = "asset-allocation-panel-v553-T2-signal-self-financing-act360-d2-research/1.0"
CHALLENGER_ID_V555 = "V555-LEGACY-B06-DIRECT-RESEARCH-01"
GOVERNANCE_LABEL_V555 = "legacy_transfer_challenger_not_blind_champion"
LOOKBACK_V555 = 36
TRAIN_MONTHS_V555 = tuple(f"{year:04d}{month:02d}" for year in (2018, 2019) for month in range(1, 13))
VALIDATION_MONTHS_V555 = tuple(f"{year:04d}{month:02d}" for year in (2020, 2021) for month in range(1, 13))
PRETEST_MONTHS_V555 = TRAIN_MONTHS_V555 + VALIDATION_MONTHS_V555
PRETEST_YEARS_V555 = ("2018", "2019", "2020", "2021")
TEST_START_V555 = "202201"
EXPECTED_DIRECT_SERIES_V555 = {
    "equity": "H00300.INDX",
    "bond": "H11006.XSHG",
    "gold": "AU9999.SGEX",
}
EXPECTED_COMMODITY_ROOTS_V555 = (
    "A", "AL", "C", "CF", "CU", "J", "L", "M", "P", "RB", "RU", "SR", "TA", "V", "Y", "ZN"
)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _is_sha256(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and all(character in "0123456789ABCDEF" for character in text)


def _candidate_parameters_v555() -> StackParametersV53:
    return StackParametersV53(
        statistical_half_life=24.0,
        factor_half_life=30.0,
        diagonal_shrinkage=.35,
        macro_blend_weight=0.0,
        macro_pit_required_fraction=.90,
        ridge_penalty=.20,
        risk_aversion=4.0,
        tau=.05,
        uncertainty_penalty=0.0,
        absolute_anchor_penalty=1.25,
        active_risk_aversion=4.0,
        active_l2_penalty=.01,
        market_view_scale_monthly=.0025,
        cycle_view_weight=0.0,
        market_view_weight=0.0,
    )


def candidate_spec_v555() -> dict[str, Any]:
    return {
        "id": CHALLENGER_ID_V555,
        "mode": "benchmark_relative",
        "governance_label": GOVERNANCE_LABEL_V555,
        "lookback_months": LOOKBACK_V555,
        "signal_spec": frozen_spec_v549(),
        "signal_spec_sha256": SPEC_SHA256_V549,
        "stack_parameters": asdict(_candidate_parameters_v555()),
        "optimizer_contract": {
            "lower_bounds": [.10, .05, .05, .05],
            "upper_bounds": [.75, .40, .30, .40],
            "max_active_share": .10,
            "max_annual_tracking_error": .08,
            "max_one_way_turnover": .08,
            "linear_cost_bps": list(LINEAR_COST_BPS_V541),
            "quadratic_cost": list(QUADRATIC_COST_V541),
            "post_solve_scaling": False,
        },
        "inference_contract": {
            "path": "direct_active_alpha",
            "other_inference": "mutually_exclusive",
            "posterior_uncertainty_penalty": 0.0,
            "macro_contribution": 0.0,
            "production_cycle_contribution": 0.0,
        },
        "candidate_count": 1,
    }


SPEC_SHA256_V555 = _canonical_hash(candidate_spec_v555())


def _month_number(value: str) -> int:
    text = str(value)
    if len(text) != 6 or not text.isdigit():
        raise ValueError("v555_month_must_be_YYYYMM")
    year, month = int(text[:4]), int(text[4:])
    if year < 1900 or not 1 <= month <= 12:
        raise ValueError("v555_month_must_be_YYYYMM")
    return year * 12 + month - 1


def _validate_lineage_v555(panel: Mapping[str, Any]) -> None:
    lineage = panel.get("source_lineage") or {}
    required = {
        "provider", "source_content_sha256", "trading_parameters_content_sha256", "direct_series",
        "commodity_builder", "collateral_source_method", "collateral_day_count",
    }
    if not required.issubset(lineage) or lineage.get("provider") != "RQData":
        raise ValueError("v555_panel_lineage_provider_or_fields_invalid")
    if not _is_sha256(lineage.get("source_content_sha256")) or not _is_sha256(lineage.get("trading_parameters_content_sha256")):
        raise ValueError("v555_panel_lineage_source_hash_invalid")
    if lineage.get("commodity_builder") != "commodity_self_financing_v553":
        raise ValueError("v555_panel_lineage_commodity_builder_invalid")
    if lineage.get("collateral_source_method") != "get_interbank_offered_rate.Shibor_ON_fallback":
        raise ValueError("v555_panel_lineage_collateral_source_invalid")
    if lineage.get("collateral_day_count") != "ACT/360":
        raise ValueError("v555_panel_lineage_day_count_invalid")
    direct = lineage.get("direct_series") or {}
    if set(direct) != set(EXPECTED_DIRECT_SERIES_V555):
        raise ValueError("v555_panel_lineage_direct_series_invalid")
    for asset, code in EXPECTED_DIRECT_SERIES_V555.items():
        row = direct.get(asset) or {}
        if row.get("code") != code or not _is_sha256(row.get("daily_sha256")):
            raise ValueError("v555_panel_lineage_direct_series_invalid")


def _validate_commodity_v555(panel: Mapping[str, Any], months: Sequence[str], returns: np.ndarray) -> None:
    commodity = panel.get("commodity") or {}
    collateral = commodity.get("collateral") or {}
    if collateral.get("day_count") != "ACT/360" or collateral.get("source_method") != "get_interbank_offered_rate.Shibor_ON_fallback":
        raise ValueError("v555_commodity_collateral_contract_invalid")
    if commodity.get("continuous_adjusted_price_used_for_PnL") is not False:
        raise ValueError("v555_commodity_continuous_price_pnl_forbidden")
    if float(commodity.get("precious_metals_weight", float("nan"))) != 0.0 or float(commodity.get("gold_weight", float("nan"))) != 0.0:
        raise ValueError("v555_commodity_precious_metal_overlap_invalid")
    if set(commodity.get("excluded_underlyings") or ()) != {"AU", "AG"}:
        raise ValueError("v555_commodity_precious_metal_exclusion_invalid")
    if tuple(commodity.get("underlyings") or ()) != EXPECTED_COMMODITY_ROOTS_V555:
        raise ValueError("v555_commodity_root_universe_invalid")
    accounting = commodity.get("position_accounting") or {}
    if accounting.get("dominant_and_volatility_information_cutoff") != "T_minus_2" or accounting.get("execution_price") != "T_minus_1_settlement":
        raise ValueError("v555_commodity_information_execution_contract_invalid")
    if accounting.get("implicit_daily_rebalancing") is not False:
        raise ValueError("v555_commodity_daily_rebalancing_forbidden")
    ledger = commodity.get("daily_ledger")
    if not isinstance(ledger, list) or not ledger:
        raise ValueError("v555_commodity_daily_ledger_missing")
    dates = [str(row.get("date", "")) for row in ledger]
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("v555_commodity_daily_ledger_dates_invalid")
    if dates[0][:7].replace("-", "") > str(panel.get("level_base_month")) or dates[-1][:7].replace("-", "") != months[-1]:
        raise ValueError("v555_commodity_daily_ledger_coverage_invalid")
    for row in ledger:
        date = str(row.get("date", ""))
        execution = str(row.get("execution_date", ""))
        cutoff = str(row.get("information_cutoff_date", ""))
        mapping = str(row.get("dominant_mapping_effective_date", ""))
        if not (len(date) == len(execution) == len(cutoff) == len(mapping) == 10 and cutoff < execution < date and mapping == execution):
            raise ValueError("v555_commodity_daily_ledger_PIT_invalid")
        for key in ("return", "collateral_return", "commission_cost", "half_tick_slippage_cost", "traded_notional", "nav"):
            value = float(row.get(key, float("nan")))
            if not math.isfinite(value) or (key in {"commission_cost", "half_tick_slippage_cost", "traded_notional", "nav"} and value < 0.0):
                raise ValueError("v555_commodity_daily_ledger_numeric_invalid")
        for trade in row.get("trades") or []:
            if not (
                str(trade.get("information_cutoff_date", "")) == cutoff
                and str(trade.get("execution_date", "")) == execution
                and str(trade.get("effective_date", "")) == date
                and str(trade.get("dominant_mapping_effective_date", "")) == mapping
            ):
                raise ValueError("v555_commodity_trade_PIT_invalid")
    monthly_nav = commodity.get("monthly_nav")
    if not isinstance(monthly_nav, dict) or not set(months).issubset(monthly_nav):
        raise ValueError("v555_commodity_monthly_nav_missing")
    base_month = str(panel.get("level_base_month", ""))
    if base_month not in monthly_nav or _month_number(months[0]) - _month_number(base_month) != 1:
        raise ValueError("v555_commodity_monthly_nav_base_invalid")
    nav_months = sorted(str(month) for month in monthly_nav)
    nav_numbers = [_month_number(month) for month in nav_months]
    if any(right - left != 1 for left, right in zip(nav_numbers, nav_numbers[1:])):
        raise ValueError("v555_commodity_monthly_nav_calendar_invalid")
    nav = {str(month): float(value) for month, value in monthly_nav.items()}
    if any(not math.isfinite(value) or value <= 0.0 for value in nav.values()):
        raise ValueError("v555_commodity_monthly_nav_numeric_invalid")
    previous_month = base_month
    for index, month in enumerate(months):
        actual = nav[month] / nav[previous_month] - 1.0
        if abs(actual - float(returns[index, 3])) > 1.0e-12:
            raise ValueError("v555_commodity_monthly_nav_return_identity_invalid")
        previous_month = month


def _validate_panel(panel: Mapping[str, Any], *, allow_test: bool) -> tuple[list[str], np.ndarray]:
    if not isinstance(panel, Mapping) or panel.get("schema_version") != PANEL_SCHEMA_V555:
        raise ValueError("v555_panel_schema_invalid")
    body = dict(panel)
    stored_hash = str(body.pop("content_sha256", ""))
    if not _is_sha256(stored_hash) or stored_hash != _canonical_hash(body):
        raise ValueError("v555_panel_content_hash_mismatch")
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER_V541:
        raise ValueError("v555_panel_asset_order_invalid")
    _validate_lineage_v555(panel)
    quality = panel.get("data_quality") or {}
    if panel.get("deployment_allowed") is not False or quality.get("status") != "D2_research_not_D3":
        raise ValueError("v555_panel_governance_boundary_invalid")
    months = [str(item) for item in panel.get("months") or []]
    numbers = [_month_number(item) for item in months]
    if len(set(numbers)) != len(numbers) or any(right - left != 1 for left, right in zip(numbers, numbers[1:])):
        raise ValueError("v555_panel_months_not_unique_contiguous")
    returns = np.asarray(panel.get("returns"), dtype=float)
    levels = np.asarray(panel.get("levels"), dtype=float)
    if returns.shape != (len(months), 4) or not np.all(np.isfinite(returns)) or np.any(returns <= -1.0):
        raise ValueError("v555_panel_returns_invalid")
    if levels.shape != returns.shape or not np.all(np.isfinite(levels)) or np.any(levels <= 0.0):
        raise ValueError("v555_panel_levels_invalid")
    if len(levels) > 1 and not np.allclose(levels[1:] / levels[:-1] - 1.0, returns[1:], rtol=0.0, atol=1.0e-12):
        raise ValueError("v555_panel_level_return_identity_invalid")
    if not set(PRETEST_MONTHS_V555).issubset(months) or len(months) < LOOKBACK_V555 + len(PRETEST_MONTHS_V555):
        raise ValueError("v555_panel_required_protocol_months_missing")
    if not allow_test and any(month >= TEST_START_V555 for month in months):
        raise ValueError("v555_selector_simulator_received_test_month")
    _validate_commodity_v555(panel, months, returns)
    return months, returns


def _strip_runtime_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _strip_runtime_fields(item) for key, item in value.items() if key != "solve_time_seconds"}
    if isinstance(value, (list, tuple)):
        return [_strip_runtime_fields(item) for item in value]
    if isinstance(value, np.ndarray):
        return _strip_runtime_fields(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("v555_nonfinite_output")
    return value


def _pretest_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    # This second full validation makes this helper safe even when called outside build_v555.
    months, _ = _validate_panel(panel, allow_test=True)
    keep = [index for index, month in enumerate(months) if month < TEST_START_V555]
    output = {
        key: deepcopy(value)
        for key, value in panel.items()
        if key not in {"months", "returns", "levels", "content_sha256", "commodity"}
    }
    commodity = deepcopy(panel["commodity"])
    commodity["daily_ledger"] = [
        row for row in commodity["daily_ledger"]
        if str(row.get("date", ""))[:7].replace("-", "") < TEST_START_V555
    ]
    commodity["monthly_nav"] = {
        str(month): value for month, value in commodity["monthly_nav"].items()
        if str(month) < TEST_START_V555
    }
    output["commodity"] = commodity
    output["months"] = [months[index] for index in keep]
    output["returns"] = [panel["returns"][index] for index in keep]
    output["levels"] = [panel["levels"][index] for index in keep]
    output["content_sha256"] = _canonical_hash(output)
    _validate_panel(output, allow_test=False)
    return output


def _sample(month: str) -> str:
    if month in TRAIN_MONTHS_V555:
        return "train"
    if month in VALIDATION_MONTHS_V555:
        return "validation"
    if month >= TEST_START_V555:
        return "test"
    return "warmup"


def _allocate_v555(window: np.ndarray, month_window: Sequence[str], previous: np.ndarray) -> dict[str, Any]:
    macro_levels = np.zeros((LOOKBACK_V555, 4), dtype=float)
    macro_admission = np.zeros((LOOKBACK_V555, 4), dtype=bool)
    return allocate_relative_legacy_direct_v549(
        window,
        macro_levels,
        macro_admission,
        month_window,
        NO_D3_CYCLES_V541,
        previous,
        _candidate_parameters_v555(),
        lower_bounds=(.10, .05, .05, .05),
        upper_bounds=(.75, .40, .30, .40),
        max_active_share=.10,
        max_annual_tracking_error=.08,
        max_one_way_turnover=.08,
        transaction_cost_bps=LINEAR_COST_BPS_V541,
        quadratic_cost=QUADRATIC_COST_V541,
    )


def _simulate_v555(panel: Mapping[str, Any], *, allow_test: bool) -> dict[str, Any]:
    months, returns = _validate_panel(panel, allow_test=allow_test)
    previous = POLICY_WEIGHTS_V541.copy()
    benchmark_drifted = POLICY_WEIGHTS_V541.copy()
    linear = np.asarray(LINEAR_COST_BPS_V541, dtype=float) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541, dtype=float)
    rows: list[dict[str, Any]] = []
    weight_rows: list[dict[str, Any]] = []
    maximum_kkt = 0.0
    for signal_index in range(LOOKBACK_V555 - 1, len(returns) - 1):
        left = signal_index - LOOKBACK_V555 + 1
        diagnostics = _allocate_v555(returns[left : signal_index + 1], months[left : signal_index + 1], previous)
        target = np.asarray(diagnostics["weights"], dtype=float)
        optimizer = diagnostics["optimizer"]
        if optimizer.get("status") != "optimal" or optimizer["solver"].get("fallback_used") is not False:
            raise RuntimeError("v555_monthly_solver_failed")
        kkt = float(optimizer["solver"].get("maximum_kkt_residual", float("nan")))
        if not math.isfinite(kkt) or kkt > 1.0e-7:
            raise RuntimeError("v555_monthly_kkt_failed")
        maximum_kkt = max(maximum_kkt, kkt)
        realized = returns[signal_index + 1]
        realized_month = months[signal_index + 1]
        change = target - previous
        cost = float(linear @ np.abs(change) + .5 * quadratic @ (change**2))
        benchmark_change = POLICY_WEIGHTS_V541 - benchmark_drifted
        benchmark_cost = float(linear @ np.abs(benchmark_change) + .5 * quadratic @ (benchmark_change**2))
        rows.append({
            "signal_month": months[signal_index],
            "month": realized_month,
            "sample": _sample(realized_month),
            "net_return": float(target @ realized) - cost,
            "benchmark_return": float(POLICY_WEIGHTS_V541 @ realized) - benchmark_cost,
            "turnover": .5 * float(np.abs(change).sum()),
            "cost": cost,
            "benchmark_cost": benchmark_cost,
            "maximum_kkt_residual": kkt,
        })
        weight_rows.append({
            "signal_month": months[signal_index],
            "realized_month": realized_month,
            "optimized_weights": {asset: float(target[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
            "signal_target_weights": {
                asset: float(diagnostics["signal_target_weights"][index]) for index, asset in enumerate(ASSET_ORDER_V541)
            },
        })
        previous = _drift(target, realized)
        benchmark_drifted = _drift(POLICY_WEIGHTS_V541, realized)
    metric_samples = ("train", "validation", "test") if allow_test else ("train", "validation")
    return {
        "spec_id": CHALLENGER_ID_V555,
        "spec_sha256": SPEC_SHA256_V555,
        "metrics": {sample: metrics_v541([row for row in rows if row["sample"] == sample]) for sample in metric_samples},
        "pretest_calendar_years": {
            year: metrics_v541([row for row in rows if row["month"].startswith(year)]) for year in PRETEST_YEARS_V555
        },
        "returns": rows,
        "weights": weight_rows,
        "end_drifted_weights": previous.tolist(),
        "maximum_monthly_kkt_residual": maximum_kkt,
        "causal_execution": "signal_at_month_t_close_targets_return_in_month_t_plus_1",
        "cost_convention": "strategy_and_policy_benchmark_both_rebalance_from_drifted_holdings_with_same_cost_vectors",
    }


def _selector_object_v555(pretest_result: Mapping[str, Any]) -> dict[str, Any]:
    candidate = candidate_spec_v555()
    if _canonical_hash(candidate) != SPEC_SHA256_V555:
        raise RuntimeError("v555_candidate_spec_hash_drift")
    result = {
        "schema_version": SELECTOR_SCHEMA_V555,
        "candidate_count": 1,
        "candidate_spec": candidate,
        "candidate_spec_sha256": SPEC_SHA256_V555,
        "metrics": {
            "train": deepcopy(pretest_result["metrics"]["train"]),
            "validation": deepcopy(pretest_result["metrics"]["validation"]),
        },
        "pretest_calendar_years": deepcopy(pretest_result["pretest_calendar_years"]),
    }
    if set(result["metrics"]) != {"train", "validation"} or tuple(sorted(result["pretest_calendar_years"])) != PRETEST_YEARS_V555:
        raise RuntimeError("v555_selector_object_boundary_invalid")
    return result


def select_pretest_v555(selector: Mapping[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    if selector.get("schema_version") != SELECTOR_SCHEMA_V555 or selector.get("candidate_count") != 1:
        raise ValueError("v555_selector_schema_or_count_invalid")
    if selector.get("candidate_spec") != candidate_spec_v555() or selector.get("candidate_spec_sha256") != SPEC_SHA256_V555:
        raise ValueError("v555_selector_candidate_spec_not_frozen")
    payload = {
        "spec": {"id": CHALLENGER_ID_V555, "mode": "benchmark_relative"},
        "metrics": deepcopy(selector["metrics"]),
        "pretest_calendar_years": deepcopy(selector["pretest_calendar_years"]),
    }
    selected, board = select_pretest_v551([payload], "benchmark_relative")
    return selected == CHALLENGER_ID_V555, board


def _current_target_v555(panel: Mapping[str, Any], full_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    months, returns = _validate_panel(panel, allow_test=True)
    full = dict(full_result) if full_result is not None else _simulate_v555(panel, allow_test=True)
    previous = np.asarray(full["end_drifted_weights"], dtype=float)
    diagnostics = _allocate_v555(returns[-LOOKBACK_V555:], months[-LOOKBACK_V555:], previous)
    target = np.asarray(diagnostics["weights"], dtype=float)
    signal_target = np.asarray(diagnostics["signal_target_weights"], dtype=float)
    strength = np.asarray(diagnostics["raw_signal_strength"], dtype=float)
    order = np.argsort(-strength, kind="mergesort")
    change = target - previous
    linear = np.asarray(LINEAR_COST_BPS_V541, dtype=float) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541, dtype=float)
    return {
        "as_of_month_end": months[-1],
        "target_for_next_realized_month": True,
        "previous_drifted_weights": {asset: float(previous[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
        "optimized_weights": {asset: float(target[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
        "signal_target_weights": {asset: float(signal_target[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
        "raw_signal_strength": {asset: float(strength[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
        "strength_order_strong_to_weak": [ASSET_ORDER_V541[index] for index in order],
        "estimated_one_way_turnover": .5 * float(np.abs(change).sum()),
        "estimated_implementation_cost": float(linear @ np.abs(change) + .5 * quadratic @ (change**2)),
        "status": "research_reporting_target_not_production_authorized",
        "diagnostics": _strip_runtime_fields(diagnostics),
    }


def _test_report_v555(panel: Mapping[str, Any]) -> dict[str, Any]:
    try:
        full = _simulate_v555(panel, allow_test=True)
        target = _current_target_v555(panel, full)
    except Exception as error:
        return {
            "status": "retrospective_reporter_failed_closed",
            "error_code": type(error).__name__,
            "selection_affected": False,
        }
    return {
        "status": "retrospective_report_only_not_pristine",
        "metrics": full["metrics"]["test"],
        "returns": [row for row in full["returns"] if row["sample"] == "test"],
        "weights": [row for row in full["weights"] if row["realized_month"] >= TEST_START_V555],
        "current_target": target,
        "selection_affected": False,
    }


def build_research_v555(panel: Mapping[str, Any]) -> dict[str, Any]:
    # Full schema/hash/order/lineage/PIT validation necessarily precedes physical test pruning.
    _validate_panel(panel, allow_test=True)
    pretest_panel = _pretest_panel(panel)
    pretest_result = _simulate_v555(pretest_panel, allow_test=False)
    selector = _selector_object_v555(pretest_result)
    passes_pretest_gate, board = select_pretest_v555(selector)
    report = _test_report_v555(panel)
    output: dict[str, Any] = {
        "schema_version": SCHEMA_V555,
        "status": "research_only",
        "governance_label": GOVERNANCE_LABEL_V555,
        "candidate_count": 1,
        "candidate_spec": candidate_spec_v555(),
        "candidate_spec_sha256": SPEC_SHA256_V555,
        "panel_content_sha256": panel["content_sha256"],
        "source_lineage": deepcopy(panel["source_lineage"]),
        "asset_order": list(ASSET_ORDER_V541),
        "policy_benchmark_internal": POLICY_WEIGHTS_V541.tolist(),
        "protocol": {
            "lookback_months": LOOKBACK_V555,
            "train": "2018-01 through 2019-12",
            "validation": "2020-01 through 2021-12",
            "test": "2022-01 onward retrospective_report_only_not_pristine",
            "execution": "signal_month_t_to_realized_month_t_plus_1",
            "current_target": "latest_month_end_signal_for_next_realized_month",
        },
        "selector": selector,
        "selection_board": board,
        "passes_pretest_gate_reporter_only": passes_pretest_gate,
        "pretest_result": pretest_result,
        "test_report_revealed_after_candidate_fixed": report,
        "selection_uses_test": False,
        "production_admitted_cycles": [],
        "macro_blend_effective": 0.0,
        "other_inference_used": False,
        "deployment_allowed": False,
        "promotion_allowed": False,
        "promotion_blockers": [
            "validation_period_already_observed_by_prior_model_generations",
            "test_period_not_pristine",
            "D3_Wind_primary_and_second_source_crosscheck_not_complete",
            "future_pristine_shadow_holdout_not_complete",
        ],
        "data_quality": {
            "panel_status": (panel.get("data_quality") or {}).get("status"),
            "production_ready": False,
            "blocking_items": list((panel.get("data_quality") or {}).get("blocking_items") or []),
        },
        "runtime_versions": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "cvxpy": cvxpy.__version__,
        },
    }
    output = _strip_runtime_fields(output)
    output["content_sha256"] = _canonical_hash(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    panel = json.loads(Path(args.panel).read_text(encoding="utf-8"))
    result = build_research_v555(panel)
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    temporary.replace(target)
    print(json.dumps({
        "status": result["status"],
        "governance_label": result["governance_label"],
        "candidate_spec_sha256": result["candidate_spec_sha256"],
        "content_sha256": result["content_sha256"],
        "deployment_allowed": result["deployment_allowed"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHALLENGER_ID_V555", "GOVERNANCE_LABEL_V555", "PANEL_SCHEMA_V555", "SPEC_SHA256_V555",
    "TEST_START_V555", "build_research_v555", "candidate_spec_v555", "select_pretest_v555",
]
