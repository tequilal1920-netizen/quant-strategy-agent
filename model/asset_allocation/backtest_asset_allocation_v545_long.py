"""Governed long-sample v5.4.5 research with physical test isolation.

Protocol frozen before the first run on the corrected real panel:
* panel begins 2015-01 after all 16 commodity roots have dated fee coverage;
* 36-month causal lookback, so first realised research return is 2018-01;
* 2018-2019 train/development score; 2020-2021 validation score;
* 2022 onward is revealed only after a candidate is selected and is always
  labelled retrospective/not-pristine/report-only;
* four relative and four absolute candidates, with no post-result expansion;
* relative model targets positive active return/IR/Sharpe improvement;
* absolute model is selected only on its own net return, Sharpe and drawdown;
  comparison with 60/15/10/15 is reporter-only and not an optimizer input;
* equal weight is absent from all optimisation and selection objects.
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

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v541_stack import (
    ASSET_ORDER_V541,
    POLICY_WEIGHTS_V541,
    allocate_absolute_v541,
    allocate_relative_v541,
)
from backtest_asset_allocation_v541_long import (
    LINEAR_COST_BPS_V541,
    NO_D3_CYCLES_V541,
    QUADRATIC_COST_V541,
    _drift,
    metrics_v541,
)


SCHEMA_V545 = "asset-allocation-v545-long-physically-test-isolated/1.0"
PANEL_SCHEMA_V545 = "asset-allocation-panel-v544-d2-research/1.0"
LOOKBACK_V545 = 36
TRAIN_MONTHS_V545 = tuple(f"{year:04d}{month:02d}" for year in (2018, 2019) for month in range(1, 13))
VALIDATION_MONTHS_V545 = tuple(f"{year:04d}{month:02d}" for year in (2020, 2021) for month in range(1, 13))
PRETEST_MONTHS_V545 = TRAIN_MONTHS_V545 + VALIDATION_MONTHS_V545
PRETEST_YEARS_V545 = ("2018", "2019", "2020", "2021")
TEST_START_V545 = "202201"


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def candidate_grid_v545() -> tuple[dict[str, Any], ...]:
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
        rows.append({"id": f"V545-REL-{index:02d}", "mode": "benchmark_relative", "parameters": asdict(parameters)})
    for index, (view_scale, anchor_penalty) in enumerate(
        ((.0025, .50), (.0025, 1.50), (.0040, .50), (.0040, 1.50)), 1
    ):
        parameters = StackParametersV53(
            market_view_scale_monthly=view_scale,
            uncertainty_penalty=.075,
            absolute_anchor_penalty=anchor_penalty,
            macro_blend_weight=0.0,
        )
        rows.append({"id": f"V545-ABS-{index:02d}", "mode": "absolute_no_benchmark", "parameters": asdict(parameters)})
    return tuple(rows)


GRID_HASH_V545 = _canonical_hash(candidate_grid_v545())


def _month_number(value: str) -> int:
    text = str(value)
    if len(text) != 6 or not text.isdigit():
        raise ValueError("v545_month_must_be_YYYYMM")
    year, month = int(text[:4]), int(text[4:])
    if year < 1900 or month < 1 or month > 12:
        raise ValueError("v545_month_must_be_YYYYMM")
    return year * 12 + month - 1


def _validate_panel(panel: Mapping[str, Any], *, allow_test: bool) -> tuple[list[str], np.ndarray]:
    if panel.get("schema_version") != PANEL_SCHEMA_V545:
        raise ValueError("v545_panel_schema_invalid")
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER_V541:
        raise ValueError("v545_panel_asset_order_invalid")
    if panel.get("deployment_allowed") is not False or (panel.get("data_quality") or {}).get("status") != "D2_research_not_D3":
        raise ValueError("v545_panel_governance_boundary_invalid")
    body = dict(panel)
    stored_hash = str(body.pop("content_sha256", ""))
    if stored_hash != _canonical_hash(body):
        raise ValueError("v545_panel_content_hash_mismatch")
    months = [str(item) for item in panel["months"]]
    numbers = [_month_number(item) for item in months]
    if len(set(numbers)) != len(numbers) or any(right - left != 1 for left, right in zip(numbers, numbers[1:])):
        raise ValueError("v545_panel_months_not_unique_contiguous")
    returns = np.asarray(panel["returns"], dtype=float)
    if returns.shape != (len(months), 4) or not np.all(np.isfinite(returns)) or np.any(returns <= -1.0):
        raise ValueError("v545_panel_returns_invalid")
    if not set(PRETEST_MONTHS_V545).issubset(months) or len(months) < LOOKBACK_V545 + len(PRETEST_MONTHS_V545):
        raise ValueError("v545_panel_required_protocol_months_missing")
    if not allow_test and any(month >= TEST_START_V545 for month in months):
        raise ValueError("v545_selector_simulator_received_test_month")
    return months, returns


def _strip_runtime_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_runtime_fields(item)
            for key, item in value.items()
            if key not in {"solve_time_seconds"}
        }
    if isinstance(value, list):
        return [_strip_runtime_fields(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_strip_runtime_fields(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("v545_nonfinite_output")
        return value
    return value


def _pretest_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    months = [str(item) for item in panel["months"]]
    keep = [index for index, month in enumerate(months) if month < TEST_START_V545]
    output = {key: deepcopy(value) for key, value in panel.items() if key not in {"months", "returns", "levels", "content_sha256"}}
    output["months"] = [months[index] for index in keep]
    output["returns"] = [panel["returns"][index] for index in keep]
    if "levels" in panel:
        output["levels"] = [panel["levels"][index] for index in keep]
    output["content_sha256"] = _canonical_hash(output)
    return output


def _sample(month: str) -> str:
    if month in TRAIN_MONTHS_V545:
        return "train"
    if month in VALIDATION_MONTHS_V545:
        return "validation"
    if month >= TEST_START_V545:
        return "test"
    return "warmup"


def _allocate(
    mode: str,
    window: np.ndarray,
    month_window: Sequence[str],
    previous: np.ndarray,
    parameters: StackParametersV53,
) -> dict[str, Any]:
    macro_levels = np.zeros((LOOKBACK_V545, 4))
    macro_admission = np.zeros((LOOKBACK_V545, 4), dtype=bool)
    arguments = (
        window,
        macro_levels,
        macro_admission,
        month_window,
        NO_D3_CYCLES_V541,
        previous,
        parameters,
    )
    kwargs = {"transaction_cost_bps": LINEAR_COST_BPS_V541, "quadratic_cost": QUADRATIC_COST_V541}
    return allocate_relative_v541(*arguments, **kwargs) if mode == "benchmark_relative" else allocate_absolute_v541(*arguments, **kwargs)


def _simulate_v545(panel: Mapping[str, Any], spec: Mapping[str, Any], *, allow_test: bool) -> dict[str, Any]:
    months, returns = _validate_panel(panel, allow_test=allow_test)
    mode = str(spec["mode"])
    parameters = StackParametersV53(**dict(spec["parameters"]))
    previous = POLICY_WEIGHTS_V541.copy() if mode == "benchmark_relative" else np.asarray([.15, .60, .10, .15])
    benchmark_drifted = POLICY_WEIGHTS_V541.copy()
    linear = np.asarray(LINEAR_COST_BPS_V541) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541)
    rows = []
    weight_rows = []
    maximum_kkt = 0.0
    for signal_index in range(LOOKBACK_V545 - 1, len(returns) - 1):
        left = signal_index - LOOKBACK_V545 + 1
        diagnostics = _allocate(
            mode,
            returns[left : signal_index + 1],
            months[left : signal_index + 1],
            previous,
            parameters,
        )
        target = np.asarray(diagnostics["weights"], dtype=float)
        optimizer = diagnostics["optimizer"]
        if optimizer["status"] != "optimal" or optimizer["solver"]["fallback_used"]:
            raise RuntimeError("v545_monthly_solver_failed")
        kkt = float(optimizer["solver"]["maximum_kkt_residual"])
        if not math.isfinite(kkt) or kkt > 1.0e-7:
            raise RuntimeError("v545_monthly_kkt_failed")
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
                "sample": _sample(realized_month),
                "net_return": float(target @ realized) - cost,
                "benchmark_return": float(POLICY_WEIGHTS_V541 @ realized) - benchmark_cost,
                "turnover": .5 * float(np.abs(change).sum()),
                "cost": cost,
                "benchmark_cost": benchmark_cost,
                "maximum_kkt_residual": kkt,
            }
        )
        weight_rows.append(
            {
                "signal_month": months[signal_index],
                "realized_month": realized_month,
                **{asset: float(target[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
            }
        )
        previous = _drift(target, realized)
        benchmark_drifted = _drift(POLICY_WEIGHTS_V541, realized)
    return {
        "spec": dict(spec),
        "metrics": {
            sample: metrics_v541([row for row in rows if row["sample"] == sample])
            for sample in ("train", "validation", "test")
        },
        "pretest_calendar_years": {
            year: metrics_v541([row for row in rows if row["month"].startswith(year)])
            for year in PRETEST_YEARS_V545
        },
        "returns": rows,
        "weights": weight_rows,
        "end_drifted_weights": previous.tolist(),
        "maximum_monthly_kkt_residual": maximum_kkt,
    }


def _selection_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "spec": dict(result["spec"]),
        "metrics": {"train": dict(result["metrics"]["train"]), "validation": dict(result["metrics"]["validation"])},
        "pretest_calendar_years": dict(result["pretest_calendar_years"]),
    }


def _number(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key)
    if value is None:
        raise ValueError(f"v545_required_metric_missing:{key}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"v545_required_metric_nonfinite:{key}")
    return number


def select_pretest_v545(
    candidates: Sequence[Mapping[str, Any]], mode: str
) -> tuple[str | None, list[dict[str, Any]]]:
    if mode not in {"benchmark_relative", "absolute_no_benchmark"}:
        raise ValueError("v545_mode_invalid")
    board = []
    for candidate in candidates:
        if candidate["spec"]["mode"] != mode:
            raise ValueError("v545_selector_mode_mismatch")
        if set(candidate.get("metrics") or {}) != {"train", "validation"}:
            raise ValueError("v545_selector_received_non_pretest_metrics")
        years = candidate.get("pretest_calendar_years") or {}
        if tuple(sorted(years)) != PRETEST_YEARS_V545 or any(int(years[year].get("months") or 0) != 12 for year in PRETEST_YEARS_V545):
            raise ValueError("v545_selector_calendar_boundary_invalid")
        train, validation = candidate["metrics"]["train"], candidate["metrics"]["validation"]
        if int(train.get("months") or 0) != 24 or int(validation.get("months") or 0) != 24:
            raise ValueError("v545_selector_split_length_invalid")
        yearly = [years[year] for year in PRETEST_YEARS_V545]
        if mode == "benchmark_relative":
            positive_years = sum(
                _number(row, "annual_excess_return") > 0.0
                and _number(row, "information_ratio") > 0.0
                and _number(row, "sharpe_improvement") >= 0.0
                for row in yearly
            )
            aggregate = all(
                _number(row, "annual_excess_return") > 0.0
                and _number(row, "information_ratio") > 0.0
                and _number(row, "sharpe_improvement") >= 0.0
                and _number(row, "max_active_drawdown") >= -.02
                for row in (train, validation)
            )
            eligible = bool(aggregate and positive_years >= 3)
            score = (
                min(_number(train, "information_ratio"), _number(validation, "information_ratio"))
                + .25 * min(_number(train, "sharpe_improvement"), _number(validation, "sharpe_improvement"))
                + 10.0 * min(_number(train, "annual_excess_return"), _number(validation, "annual_excess_return"))
                - .25 * abs(_number(train, "information_ratio") - _number(validation, "information_ratio"))
                - .50 * _number(validation, "average_turnover")
            )
        else:
            positive_years = sum(
                _number(row, "annual_return") > 0.0
                and _number(row, "sharpe") > 0.0
                and _number(row, "max_drawdown") >= -.15
                for row in yearly
            )
            aggregate = all(
                _number(row, "annual_return") > 0.0
                and _number(row, "sharpe") >= .50
                and _number(row, "max_drawdown") >= -.15
                for row in (train, validation)
            )
            eligible = bool(aggregate and positive_years >= 3)
            score = (
                min(_number(train, "sharpe"), _number(validation, "sharpe"))
                + 2.0 * min(_number(train, "annual_return"), _number(validation, "annual_return"))
                + .50 * min(_number(train, "max_drawdown"), _number(validation, "max_drawdown"))
                - .20 * abs(_number(train, "sharpe") - _number(validation, "sharpe"))
                - .50 * _number(validation, "average_turnover")
            )
        board.append(
            {
                "id": candidate["spec"]["id"],
                "mode": mode,
                "eligible": eligible,
                "score": score,
                "positive_pretest_calendar_years": positive_years,
                "required_positive_calendar_years": 3,
                "train": train,
                "validation": validation,
            }
        )
    board.sort(key=lambda row: (-row["score"], row["id"]))
    return next((row["id"] for row in board if row["eligible"]), None), board


def _current_target(panel: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    months, returns = _validate_panel(panel, allow_test=True)
    full = _simulate_v545(panel, spec, allow_test=True)
    previous = np.asarray(full["end_drifted_weights"], dtype=float)
    left = len(returns) - LOOKBACK_V545
    diagnostics = _allocate(
        str(spec["mode"]),
        returns[left:],
        months[left:],
        previous,
        StackParametersV53(**dict(spec["parameters"])),
    )
    weights = np.asarray(diagnostics["weights"], dtype=float)
    signal = (diagnostics.get("black_litterman") or {})
    view_diagnostics = ((diagnostics.get("black_litterman") or {}).get("view_diagnostics") or {})
    if not view_diagnostics:
        view_diagnostics = ((diagnostics.get("view_consensus") or {}).get("diagnostics") or {})
    strength = view_diagnostics.get("raw_risk_adjusted_strength")
    rank = view_diagnostics.get("cross_sectional_rank_score")
    if strength is None or rank is None:
        from asset_allocation_v536_stack import causal_market_view_v536
        covariance = np.cov(returns[left:].T, ddof=1)
        regenerated = causal_market_view_v536(
            returns[left:], covariance, np.zeros(4), tau=.05,
            view_scale_monthly=float(spec["parameters"]["market_view_scale_monthly"]),
        )
        strength = regenerated.diagnostics["raw_risk_adjusted_strength"]
        rank = regenerated.diagnostics["cross_sectional_rank_score"]
    order = np.argsort(-np.asarray(strength, dtype=float))
    diagnostics = _strip_runtime_fields(diagnostics)
    return {
        "signal_month": months[-1],
        "weights": {asset: float(weights[index]) for index, asset in enumerate(ASSET_ORDER_V541)},
        "strength": {
            asset: {"raw": float(strength[index]), "rank_score": float(rank[index])}
            for index, asset in enumerate(ASSET_ORDER_V541)
        },
        "strength_order_strong_to_weak": [ASSET_ORDER_V541[index] for index in order],
        "diagnostics": diagnostics,
        "status": "research_reporting_target_not_production_authorized",
    }


def _test_report(panel: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    try:
        full = _simulate_v545(panel, spec, allow_test=True)
        target = _current_target(panel, spec)
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
        "current_target": target,
        "selection_affected": False,
    }


def build_research_v545(panel: Mapping[str, Any]) -> dict[str, Any]:
    pretest = _pretest_panel(panel)
    _validate_panel(pretest, allow_test=False)
    pretest_results = [_simulate_v545(pretest, spec, allow_test=False) for spec in candidate_grid_v545()]
    selected: dict[str, str | None] = {}
    boards = {}
    reports = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        mode_results = [row for row in pretest_results if row["spec"]["mode"] == mode]
        selected[mode], boards[mode] = select_pretest_v545(
            [_selection_payload(row) for row in mode_results], mode
        )
        if selected[mode] is None:
            reports[mode] = None
        else:
            spec = next(row for row in candidate_grid_v545() if row["id"] == selected[mode])
            reports[mode] = _test_report(panel, spec)
    output: dict[str, Any] = {
        "schema_version": SCHEMA_V545,
        "status": "research_only_pending_statistics_D3_and_future_pristine_holdout",
        "deployment_allowed": False,
        "asset_order": list(ASSET_ORDER_V541),
        "policy_benchmark_internal": POLICY_WEIGHTS_V541.tolist(),
        "equal_weight_role": "absent_from_optimizer_selection_active_metrics_and_current_target",
        "protocol": {
            "lookback_months": LOOKBACK_V545,
            "warmup": "2015-01 through 2017-12",
            "train": "2018-01 through 2019-12",
            "validation": "2020-01 through 2021-12",
            "test": "2022-01 onward retrospective_report_only_not_pristine",
            "relative_objective": "positive_net_active_return_IR_and_Sharpe_improvement",
            "absolute_objective": "own_net_return_Sharpe_and_drawdown; policy comparison reporter_only",
        },
        "candidate_grid": list(candidate_grid_v545()),
        "candidate_grid_sha256": GRID_HASH_V545,
        "panel_content_sha256": panel["content_sha256"],
        "source_lineage": deepcopy(panel.get("source_lineage")),
        "selected_ids_pretest": selected,
        "selection_boards": boards,
        "test_reports_revealed_after_selection": reports,
        "pretest_results": pretest_results,
        "selector_input_contains_test": False,
        "selection_uses_test": False,
        "production_admitted_cycles": [],
        "macro_blend_effective": 0.0,
        "runtime_versions": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "cvxpy": cvxpy.__version__,
        },
        "data_quality": {
            "panel_status": (panel.get("data_quality") or {}).get("status"),
            "production_ready": False,
            "blocking_items": list((panel.get("data_quality") or {}).get("blocking_items") or []),
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
    result = build_research_v545(panel)
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    print(
        json.dumps(
            {
                "status": result["status"],
                "selected_ids_pretest": result["selected_ids_pretest"],
                "content_sha256": result["content_sha256"],
                "deployment_allowed": result["deployment_allowed"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GRID_HASH_V545",
    "LOOKBACK_V545",
    "build_research_v545",
    "candidate_grid_v545",
    "select_pretest_v545",
]
