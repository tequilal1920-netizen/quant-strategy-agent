"""Frozen long-sample, test-blind v5.4.1 allocation backtest.

Protocol is fixed before results are read:
* common monthly data start: 2013-02 or later;
* 60-month causal lookback;
* train through 2017-12;
* validation 2018-01 through 2021-12;
* 2022-01 onward retrospective report-only, never visible to selector;
* compact 4 relative + 4 absolute candidate grid;
* relative optimization anchor is 60/15/10/15; absolute API is benchmark-free;
* equal weight is not present anywhere in this module.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
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


TRAIN_END_V541 = "201712"
VALIDATION_END_V541 = "202112"
LOOKBACK_V541 = 60
NO_D3_CYCLES_V541: dict[str, Any] = {"cycles": {}}
LINEAR_COST_BPS_V541 = (5.0, 2.0, 5.0, 6.0)
QUADRATIC_COST_V541 = (0.0010, 0.0005, 0.0015, 0.0020)


def candidate_grid_v541() -> tuple[dict[str, Any], ...]:
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
        rows.append(
            {"id": f"V541-REL-{index:02d}", "mode": "benchmark_relative", "parameters": asdict(parameters)}
        )
    for index, (view_scale, anchor_penalty) in enumerate(
        ((.0025, .50), (.0025, 1.50), (.0040, .50), (.0040, 1.50)), 1
    ):
        parameters = StackParametersV53(
            market_view_scale_monthly=view_scale,
            uncertainty_penalty=.075,
            absolute_anchor_penalty=anchor_penalty,
            macro_blend_weight=0.0,
        )
        rows.append(
            {"id": f"V541-ABS-{index:02d}", "mode": "absolute_no_benchmark", "parameters": asdict(parameters)}
        )
    return tuple(rows)


def _drift(weights: np.ndarray, realized: np.ndarray) -> np.ndarray:
    gross = weights * (1.0 + realized)
    return gross / float(gross.sum())


def _sample(month: str) -> str:
    if month <= TRAIN_END_V541:
        return "train"
    if month <= VALIDATION_END_V541:
        return "validation"
    return "test"


def _drawdown(returns: np.ndarray) -> float:
    nav = np.r_[1.0, np.cumprod(1.0 + returns)]
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def metrics_v541(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"months": 0, "risk_free_rate": 0.0}
    strategy = np.asarray([row["net_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    active = strategy - benchmark
    relative = (1.0 + strategy) / (1.0 + benchmark) - 1.0
    n = len(rows)
    volatility = float(strategy.std(ddof=1) * np.sqrt(12.0)) if n > 1 else 0.0
    benchmark_vol = float(benchmark.std(ddof=1) * np.sqrt(12.0)) if n > 1 else 0.0
    tracking = float(active.std(ddof=1) * np.sqrt(12.0)) if n > 1 else 0.0
    sharpe = float(strategy.mean() * 12.0 / volatility) if volatility > 1e-12 else None
    benchmark_sharpe = float(benchmark.mean() * 12.0 / benchmark_vol) if benchmark_vol > 1e-12 else None
    return {
        "months": n,
        "risk_free_rate": 0.0,
        "annual_return": float(np.prod(1.0 + strategy) ** (12.0 / n) - 1.0),
        "benchmark_annual_return": float(np.prod(1.0 + benchmark) ** (12.0 / n) - 1.0),
        "annual_excess_return": float(np.prod(1.0 + relative) ** (12.0 / n) - 1.0),
        "annual_volatility": volatility,
        "benchmark_annual_volatility": benchmark_vol,
        "sharpe": sharpe,
        "benchmark_sharpe": benchmark_sharpe,
        "sharpe_improvement": None if sharpe is None or benchmark_sharpe is None else sharpe - benchmark_sharpe,
        "tracking_error": tracking,
        "information_ratio": float(active.mean() * 12.0 / tracking) if tracking > 1e-12 else None,
        "max_drawdown": _drawdown(strategy),
        "benchmark_max_drawdown": _drawdown(benchmark),
        "max_active_drawdown": _drawdown(relative),
        "average_turnover": float(np.mean([row["turnover"] for row in rows])),
        "annual_cost_drag": float(np.mean([row["cost"] for row in rows]) * 12.0),
        "benchmark_annual_cost_drag": float(np.mean([row["benchmark_cost"] for row in rows]) * 12.0),
    }


def _validate_panel(panel: Mapping[str, Any]) -> tuple[list[str], np.ndarray]:
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER_V541:
        raise ValueError("v541_long_panel_asset_order_invalid")
    months = [str(item) for item in panel["months"]]
    returns = np.asarray(panel["returns"], dtype=float)
    if returns.shape != (len(months), 4) or not np.all(np.isfinite(returns)):
        raise ValueError("v541_long_panel_returns_invalid")
    if any(months[index] >= months[index + 1] for index in range(len(months) - 1)):
        raise ValueError("v541_long_panel_months_invalid")
    if len(months) < LOOKBACK_V541 + 2:
        raise ValueError("v541_long_panel_insufficient")
    return months, returns


def simulate_v541(panel: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    months, returns = _validate_panel(panel)
    parameters = StackParametersV53(**dict(spec["parameters"]))
    mode = str(spec["mode"])
    previous = POLICY_WEIGHTS_V541.copy() if mode == "benchmark_relative" else np.asarray([.15, .60, .10, .15])
    benchmark_drifted = POLICY_WEIGHTS_V541.copy()
    linear = np.asarray(LINEAR_COST_BPS_V541) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541)
    macro_levels = np.zeros((LOOKBACK_V541, 4))
    macro_admission = np.zeros((LOOKBACK_V541, 4), dtype=bool)
    rows = []
    weight_rows = []
    max_kkt = 0.0
    last_target = None
    last_diagnostics = None
    for signal_index in range(LOOKBACK_V541 - 1, len(returns) - 1):
        left = signal_index - LOOKBACK_V541 + 1
        window = returns[left : signal_index + 1]
        month_window = months[left : signal_index + 1]
        if mode == "benchmark_relative":
            diagnostics = allocate_relative_v541(
                window, macro_levels, macro_admission, month_window,
                NO_D3_CYCLES_V541, previous, parameters,
                transaction_cost_bps=LINEAR_COST_BPS_V541,
                quadratic_cost=QUADRATIC_COST_V541,
            )
        else:
            diagnostics = allocate_absolute_v541(
                window, macro_levels, macro_admission, month_window,
                NO_D3_CYCLES_V541, previous, parameters,
                transaction_cost_bps=LINEAR_COST_BPS_V541,
                quadratic_cost=QUADRATIC_COST_V541,
            )
        target = np.asarray(diagnostics["weights"], dtype=float)
        optimizer = diagnostics["optimizer"]
        if optimizer["status"] != "optimal" or optimizer["solver"]["fallback_used"]:
            raise RuntimeError("v541_long_monthly_solver_failed")
        kkt = float(optimizer["solver"]["maximum_kkt_residual"])
        if kkt > 1e-7:
            raise RuntimeError("v541_long_monthly_kkt_failed")
        max_kkt = max(max_kkt, kkt)
        realized = returns[signal_index + 1]
        realized_month = months[signal_index + 1]
        change = target - previous
        cost = float(linear @ np.abs(change) + 0.5 * quadratic @ (change**2))
        benchmark_change = POLICY_WEIGHTS_V541 - benchmark_drifted
        benchmark_cost = float(linear @ np.abs(benchmark_change) + 0.5 * quadratic @ (benchmark_change**2))
        rows.append(
            {
                "month": realized_month,
                "sample": _sample(realized_month),
                "gross_return": float(target @ realized),
                "net_return": float(target @ realized) - cost,
                "benchmark_return": float(POLICY_WEIGHTS_V541 @ realized) - benchmark_cost,
                "turnover": .5 * float(np.abs(change).sum()),
                "cost": cost,
                "benchmark_cost": benchmark_cost,
                "maximum_kkt_residual": kkt,
            }
        )
        weight_rows.append(
            {"signal_month": months[signal_index], "realized_month": realized_month, **{asset: float(target[index]) for index, asset in enumerate(ASSET_ORDER_V541)}}
        )
        previous = _drift(target, realized)
        benchmark_drifted = _drift(POLICY_WEIGHTS_V541, realized)
        last_target, last_diagnostics = target, diagnostics
    split_metrics = {sample: metrics_v541([row for row in rows if row["sample"] == sample]) for sample in ("train", "validation", "test")}
    pretest_years = sorted({row["month"][:4] for row in rows if row["sample"] != "test"})
    return {
        "spec": dict(spec),
        "metrics": split_metrics,
        "pretest_calendar_years": {year: metrics_v541([row for row in rows if row["sample"] != "test" and row["month"].startswith(year)]) for year in pretest_years},
        "returns": rows,
        "weights": weight_rows,
        "last_target": None if last_target is None else last_target.tolist(),
        "last_diagnostics": last_diagnostics,
        "maximum_monthly_kkt_residual": max_kkt,
        "selection_uses_test": False,
    }


def _number(row: Mapping[str, Any], key: str, default: float = -99.0) -> float:
    value = row.get(key)
    return default if value is None else float(value)


def selection_payload_v541(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "spec": dict(result["spec"]),
        "metrics": {"train": dict(result["metrics"]["train"]), "validation": dict(result["metrics"]["validation"])},
        "pretest_calendar_years": dict(result["pretest_calendar_years"]),
    }


def select_pretest_v541(candidates: Sequence[Mapping[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    if any("test" in (candidate.get("metrics") or {}) for candidate in candidates):
        raise ValueError("v541_long_selector_received_test")
    board = []
    for candidate in candidates:
        train = candidate["metrics"]["train"]
        validation = candidate["metrics"]["validation"]
        yearly = [row for row in candidate["pretest_calendar_years"].values() if int(row.get("months") or 0) >= 6]
        gate_rows = [train, validation, *yearly]
        eligible = all(
            _number(row, "annual_excess_return", -1.0) > 0.0
            and _number(row, "information_ratio") > 0.0
            and _number(row, "sharpe_improvement", -1.0) >= 0.0
            and _number(row, "max_active_drawdown", -1.0) >= -.02
            for row in gate_rows
        )
        score = (
            min(_number(train, "information_ratio"), _number(validation, "information_ratio"))
            + .25 * min(_number(train, "sharpe_improvement"), _number(validation, "sharpe_improvement"))
            + 10.0 * min(_number(train, "annual_excess_return"), _number(validation, "annual_excess_return"))
            - .25 * abs(_number(train, "information_ratio") - _number(validation, "information_ratio"))
            - .50 * _number(validation, "average_turnover", 0.0)
        )
        board.append({"id": candidate["spec"]["id"], "mode": candidate["spec"]["mode"], "eligible": eligible, "score": score, "train": train, "validation": validation})
    board.sort(key=lambda row: (-row["score"], row["id"]))
    return next((row["id"] for row in board if row["eligible"]), None), board


def build_research_v541(panel: Mapping[str, Any]) -> dict[str, Any]:
    results = [simulate_v541(panel, spec) for spec in candidate_grid_v541()]
    selected = {}
    boards = {}
    reports = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        mode_results = [row for row in results if row["spec"]["mode"] == mode]
        selected[mode], boards[mode] = select_pretest_v541([selection_payload_v541(row) for row in mode_results])
        reports[mode] = next((row for row in mode_results if row["spec"]["id"] == selected[mode]), None)
    return {
        "schema_version": "asset-allocation-v541-long-research/1.0",
        "status": "research_only_pending_promotion_gates",
        "deployment_allowed": False,
        "asset_order": list(ASSET_ORDER_V541),
        "policy_benchmark_internal": POLICY_WEIGHTS_V541.tolist(),
        "equal_weight_role": "absent_from_optimizer_and_selection",
        "protocol": {"lookback_months": LOOKBACK_V541, "train_end": TRAIN_END_V541, "validation_end": VALIDATION_END_V541, "test_role": "retrospective_report_only_not_used_for_selection"},
        "candidate_grid": list(candidate_grid_v541()),
        "selected_ids_pretest": selected,
        "selection_boards": boards,
        "selected_reports": reports,
        "all_results": results,
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
    result = build_research_v541(panel)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": result["status"], "selected_ids_pretest": result["selected_ids_pretest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
