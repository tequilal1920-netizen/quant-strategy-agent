"""Physically test-isolated long-sample v5.4.2 allocation research.

Frozen before reading the real result:
* 2013-2017: 60-month model warm-up only;
* 2018-2019: out-of-sample development/train score;
* 2020-2021: validation score;
* 2022 onward: retrospective reporter only, constructed after selection;
* 4 relative and 4 absolute candidates, no grid expansion;
* at least 3 of 4 pretest calendar years must have positive excess and IR;
* aggregate train and validation must have positive excess, IR and Sharpe delta.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v541_stack import (
    ASSET_ORDER_V541,
    POLICY_WEIGHTS_V541,
    allocate_absolute_v541,
    allocate_relative_v541,
)
from backtest_asset_allocation_v541_long import (
    LINEAR_COST_BPS_V541,
    LOOKBACK_V541,
    NO_D3_CYCLES_V541,
    QUADRATIC_COST_V541,
    _drift,
    metrics_v541,
)


TRAIN_END_V542 = "201912"
VALIDATION_END_V542 = "202112"
TEST_START_V542 = "202201"


def candidate_grid_v542() -> tuple[dict[str, Any], ...]:
    rows = []
    for index, (view_scale, uncertainty) in enumerate(
        ((.0025, .025), (.0025, .075), (.0040, .025), (.0040, .075)), 1
    ):
        parameters = StackParametersV53(
            market_view_scale_monthly=view_scale,
            uncertainty_penalty=uncertainty,
            active_l2_penalty=.01,
            macro_blend_weight=0.0,
        )
        rows.append({"id": f"V542-REL-{index:02d}", "mode": "benchmark_relative", "parameters": asdict(parameters)})
    for index, (view_scale, anchor_penalty) in enumerate(
        ((.0025, .50), (.0025, 1.50), (.0040, .50), (.0040, 1.50)), 1
    ):
        parameters = StackParametersV53(
            market_view_scale_monthly=view_scale,
            uncertainty_penalty=.075,
            absolute_anchor_penalty=anchor_penalty,
            macro_blend_weight=0.0,
        )
        rows.append({"id": f"V542-ABS-{index:02d}", "mode": "absolute_no_benchmark", "parameters": asdict(parameters)})
    return tuple(rows)


def _sample_v542(month: str) -> str:
    if month <= TRAIN_END_V542:
        return "train"
    if month <= VALIDATION_END_V542:
        return "validation"
    return "test"


def _validate_panel(panel: Mapping[str, Any], *, allow_test: bool) -> tuple[list[str], np.ndarray]:
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER_V541:
        raise ValueError("v542_panel_asset_order_invalid")
    months = [str(item) for item in panel["months"]]
    returns = np.asarray(panel["returns"], dtype=float)
    if returns.shape != (len(months), 4) or not np.all(np.isfinite(returns)):
        raise ValueError("v542_panel_returns_invalid")
    if any(months[index] >= months[index + 1] for index in range(len(months) - 1)):
        raise ValueError("v542_panel_months_invalid")
    if not allow_test and any(month >= TEST_START_V542 for month in months):
        raise ValueError("v542_selector_simulator_received_test_month")
    return months, returns


def _simulate_v542(
    panel: Mapping[str, Any], spec: Mapping[str, Any], *, allow_test: bool
) -> dict[str, Any]:
    months, returns = _validate_panel(panel, allow_test=allow_test)
    parameters = StackParametersV53(**dict(spec["parameters"]))
    mode = str(spec["mode"])
    previous = POLICY_WEIGHTS_V541.copy() if mode == "benchmark_relative" else np.asarray([.15, .60, .10, .15])
    benchmark_drifted = POLICY_WEIGHTS_V541.copy()
    linear = np.asarray(LINEAR_COST_BPS_V541) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541)
    macro_levels = np.zeros((LOOKBACK_V541, 4))
    macro_admission = np.zeros((LOOKBACK_V541, 4), dtype=bool)
    rows = []
    weights = []
    maximum_kkt = 0.0
    last_diagnostics = None
    for signal_index in range(LOOKBACK_V541 - 1, len(returns) - 1):
        left = signal_index - LOOKBACK_V541 + 1
        window = returns[left : signal_index + 1]
        month_window = months[left : signal_index + 1]
        arguments = (
            window, macro_levels, macro_admission, month_window,
            NO_D3_CYCLES_V541, previous, parameters,
        )
        if mode == "benchmark_relative":
            diagnostics = allocate_relative_v541(
                *arguments,
                transaction_cost_bps=LINEAR_COST_BPS_V541,
                quadratic_cost=QUADRATIC_COST_V541,
            )
        else:
            diagnostics = allocate_absolute_v541(
                *arguments,
                transaction_cost_bps=LINEAR_COST_BPS_V541,
                quadratic_cost=QUADRATIC_COST_V541,
            )
        target = np.asarray(diagnostics["weights"], dtype=float)
        optimizer = diagnostics["optimizer"]
        if optimizer["status"] != "optimal" or optimizer["solver"]["fallback_used"]:
            raise RuntimeError("v542_monthly_solver_failed")
        kkt = float(optimizer["solver"]["maximum_kkt_residual"])
        if kkt > 1.0e-7:
            raise RuntimeError("v542_monthly_kkt_failed")
        maximum_kkt = max(maximum_kkt, kkt)
        realized = returns[signal_index + 1]
        realized_month = months[signal_index + 1]
        change = target - previous
        cost = float(linear @ np.abs(change) + .5 * quadratic @ (change**2))
        benchmark_change = POLICY_WEIGHTS_V541 - benchmark_drifted
        benchmark_cost = float(linear @ np.abs(benchmark_change) + .5 * quadratic @ (benchmark_change**2))
        rows.append(
            {
                "month": realized_month,
                "sample": _sample_v542(realized_month),
                "net_return": float(target @ realized) - cost,
                "benchmark_return": float(POLICY_WEIGHTS_V541 @ realized) - benchmark_cost,
                "turnover": .5 * float(np.abs(change).sum()),
                "cost": cost,
                "benchmark_cost": benchmark_cost,
                "maximum_kkt_residual": kkt,
            }
        )
        weights.append({"signal_month": months[signal_index], "realized_month": realized_month, **{asset: float(target[index]) for index, asset in enumerate(ASSET_ORDER_V541)}})
        previous = _drift(target, realized)
        benchmark_drifted = _drift(POLICY_WEIGHTS_V541, realized)
        last_diagnostics = diagnostics
    metrics = {
        sample: metrics_v541([row for row in rows if row["sample"] == sample])
        for sample in ("train", "validation", "test")
    }
    pretest_years = sorted({row["month"][:4] for row in rows if row["sample"] != "test"})
    return {
        "spec": dict(spec),
        "metrics": metrics,
        "pretest_calendar_years": {year: metrics_v541([row for row in rows if row["sample"] != "test" and row["month"].startswith(year)]) for year in pretest_years},
        "returns": rows,
        "weights": weights,
        "last_diagnostics": last_diagnostics,
        "maximum_monthly_kkt_residual": maximum_kkt,
    }


def _pretest_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    months = [str(item) for item in panel["months"]]
    keep = [index for index, month in enumerate(months) if month < TEST_START_V542]
    return {
        "asset_order": list(panel["asset_order"]),
        "months": [months[index] for index in keep],
        "returns": [panel["returns"][index] for index in keep],
    }


def _number(row: Mapping[str, Any], key: str, default: float = -99.0) -> float:
    value = row.get(key)
    return default if value is None else float(value)


def _selection_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "spec": dict(result["spec"]),
        "metrics": {"train": dict(result["metrics"]["train"]), "validation": dict(result["metrics"]["validation"])},
        "pretest_calendar_years": dict(result["pretest_calendar_years"]),
    }


def select_pretest_v542(candidates: Sequence[Mapping[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    if any("test" in (candidate.get("metrics") or {}) for candidate in candidates):
        raise ValueError("v542_selector_received_test")
    board = []
    for candidate in candidates:
        train, validation = candidate["metrics"]["train"], candidate["metrics"]["validation"]
        yearly = [row for row in candidate["pretest_calendar_years"].values() if int(row.get("months") or 0) >= 6]
        positive_years = sum(
            _number(row, "annual_excess_return", -1.0) > 0.0
            and _number(row, "information_ratio") > 0.0
            for row in yearly
        )
        aggregate_gate = all(
            _number(row, "annual_excess_return", -1.0) > 0.0
            and _number(row, "information_ratio") > 0.0
            and _number(row, "sharpe_improvement", -1.0) >= 0.0
            and _number(row, "max_active_drawdown", -1.0) >= -.02
            for row in (train, validation)
        )
        eligible = bool(aggregate_gate and len(yearly) == 4 and positive_years >= 3)
        score = (
            min(_number(train, "information_ratio"), _number(validation, "information_ratio"))
            + .25 * min(_number(train, "sharpe_improvement"), _number(validation, "sharpe_improvement"))
            + 10.0 * min(_number(train, "annual_excess_return"), _number(validation, "annual_excess_return"))
            - .25 * abs(_number(train, "information_ratio") - _number(validation, "information_ratio"))
            - .50 * _number(validation, "average_turnover", 0.0)
        )
        board.append({
            "id": candidate["spec"]["id"], "mode": candidate["spec"]["mode"],
            "eligible": eligible, "score": score, "positive_pretest_calendar_years": positive_years,
            "required_positive_calendar_years": 3, "train": train, "validation": validation,
        })
    board.sort(key=lambda row: (-row["score"], row["id"]))
    return next((row["id"] for row in board if row["eligible"]), None), board


def _test_report(panel: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    try:
        full = _simulate_v542(panel, spec, allow_test=True)
    except Exception as error:
        return {"status": "reporter_failed_closed", "error_code": type(error).__name__, "selection_affected": False}
    return {
        "status": "retrospective_report_only_not_pristine",
        "metrics": full["metrics"]["test"],
        "returns": [row for row in full["returns"] if row["sample"] == "test"],
        "selection_affected": False,
    }


def build_research_v542(panel: Mapping[str, Any]) -> dict[str, Any]:
    pretest = _pretest_panel(panel)
    pretest_results = [_simulate_v542(pretest, spec, allow_test=False) for spec in candidate_grid_v542()]
    selected = {}
    boards = {}
    reports = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        mode_results = [row for row in pretest_results if row["spec"]["mode"] == mode]
        selected[mode], boards[mode] = select_pretest_v542([_selection_payload(row) for row in mode_results])
        if selected[mode] is None:
            reports[mode] = None
        else:
            spec = next(row for row in candidate_grid_v542() if row["id"] == selected[mode])
            reports[mode] = _test_report(panel, spec)
    return {
        "schema_version": "asset-allocation-v542-long-physically-test-isolated/1.0",
        "status": "research_only_pending_statistical_and_data_promotion_gates",
        "deployment_allowed": False,
        "asset_order": list(ASSET_ORDER_V541),
        "policy_benchmark_internal": POLICY_WEIGHTS_V541.tolist(),
        "equal_weight_role": "absent_from_optimizer_selection_and_active_metrics",
        "protocol": {
            "lookback_months": LOOKBACK_V541,
            "warmup": "2013-2017",
            "train": "2018-2019",
            "validation": "2020-2021",
            "test": "2022 onward retrospective_report_only_not_pristine",
        },
        "candidate_grid": list(candidate_grid_v542()),
        "selected_ids_pretest": selected,
        "selection_boards": boards,
        "test_reports_revealed_after_selection": reports,
        "pretest_results": pretest_results,
        "selector_input_contains_test": False,
        "selection_uses_test": False,
        "production_admitted_cycles": [],
        "macro_blend_effective": 0.0,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    panel = json.loads(Path(args.panel).read_text(encoding="utf-8"))
    result = build_research_v542(panel)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False),encoding="utf-8")
    print(json.dumps({"status":result["status"],"selected_ids_pretest":result["selected_ids_pretest"]},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
