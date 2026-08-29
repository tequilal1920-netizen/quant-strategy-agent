"""Strictly causal research backtest for the v5.3.6 allocation stack.

This reporter fixes historical training replay, drawdown, current-target and
serialization defects.  It is intentionally research-only because the
available validation is 12 months and all five macro-cycle inputs lack D3/PIT
metadata in the current warehouse.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import backtest_asset_allocation_v53_stack as legacy_data
from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v536_stack import (
    ASSET_ORDER_V536,
    POLICY_WEIGHTS_V536,
    allocate_absolute_v536,
    allocate_relative_v536,
)
from cycle_views_v536 import fit_cycle_views_expanding_v536


SCHEMA = "asset-allocation-v5.3.6-causal-backtest/1"


def _json(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return _json(value.tolist())
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("v536_nonfinite_json_value")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _drawdown(returns: np.ndarray) -> float:
    nav = np.r_[1.0, np.cumprod(1.0 + np.asarray(returns, dtype=float))]
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"months": 0, "risk_free_rate": 0.0}
    strategy = np.asarray([row["net_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    active = strategy - benchmark
    relative_returns = (1.0 + strategy) / np.maximum(1.0 + benchmark, 1.0e-12) - 1.0
    count = len(rows)
    strategy_vol = float(strategy.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    benchmark_vol = float(benchmark.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    tracking_error = float(active.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    strategy_sharpe = float(strategy.mean() * 12.0 / strategy_vol) if strategy_vol > 1.0e-12 else None
    benchmark_sharpe = float(benchmark.mean() * 12.0 / benchmark_vol) if benchmark_vol > 1.0e-12 else None
    return {
        "months": count,
        "risk_free_rate": 0.0,
        "annual_return": float(np.prod(1.0 + strategy) ** (12.0 / count) - 1.0),
        "benchmark_annual_return": float(np.prod(1.0 + benchmark) ** (12.0 / count) - 1.0),
        "annual_excess_return": float(np.prod(1.0 + relative_returns) ** (12.0 / count) - 1.0),
        "annual_volatility": strategy_vol,
        "benchmark_annual_volatility": benchmark_vol,
        "sharpe": strategy_sharpe,
        "benchmark_sharpe": benchmark_sharpe,
        "sharpe_improvement": None if strategy_sharpe is None or benchmark_sharpe is None else strategy_sharpe - benchmark_sharpe,
        "tracking_error": tracking_error,
        "information_ratio": float(active.mean() * 12.0 / tracking_error) if tracking_error > 1.0e-12 else None,
        "max_drawdown": _drawdown(strategy),
        "benchmark_max_drawdown": _drawdown(benchmark),
        "max_active_drawdown": _drawdown(relative_returns),
        "positive_active_month_rate": float(np.mean(active > 0.0)),
        "average_turnover": float(np.mean([row["turnover"] for row in rows])),
        "annual_cost_drag": float(np.mean([row["cost"] for row in rows]) * 12.0),
        "benchmark_annual_cost_drag": float(np.mean([row["benchmark_cost"] for row in rows]) * 12.0),
    }


def candidate_grid_v536() -> tuple[dict[str, Any], ...]:
    """Small preregistered family; old observed reports must not expand it."""

    output: list[dict[str, Any]] = []
    identifier = 0
    for view_scale in (0.0025, 0.0040):
        for uncertainty in (0.025, 0.075):
            for risk_anchor in (0.40, 0.80):
                identifier += 1
                parameters = StackParametersV53(
                    market_view_scale_monthly=view_scale,
                    uncertainty_penalty=uncertainty,
                    active_l2_penalty=0.01,
                    macro_blend_weight=0.25,
                )
                output.append(
                    {
                        "id": f"V536-REL-{identifier:02d}",
                        "model_version": "benchmark_relative",
                        "parameters": asdict(parameters),
                        "risk_budget_anchor_penalty": risk_anchor,
                    }
                )
    for view_scale in (0.0025, 0.0040):
        for anchor in (0.50, 1.50):
            identifier += 1
            parameters = StackParametersV53(
                market_view_scale_monthly=view_scale,
                uncertainty_penalty=0.075,
                absolute_anchor_penalty=anchor,
                macro_blend_weight=0.25,
            )
            output.append(
                {
                    "id": f"V536-ABS-{identifier - 8:02d}",
                    "model_version": "absolute_no_benchmark",
                    "parameters": asdict(parameters),
                }
            )
    if len(output) != 12:
        raise RuntimeError("v536_grid_size_changed")
    return tuple(output)


def prepare_information_set(database: Path, protocol: legacy_data.BacktestProtocolV53) -> dict[str, Any]:
    info = legacy_data.prepare_information_set(database, protocol)
    # Current warehouse has zero row-level PIT-admitted macro fields.  The
    # matrix shape is explicit so future partial admission cannot unlock all
    # factors via one aggregate boolean.
    info["macro_admission_matrix"] = np.zeros((len(info["months"]), 4), dtype=bool)
    for row in info["cycles"]:
        for payload in (row.get("cycles") or {}).values():
            payload["eligible_for_production_views"] = False
    return info


def _fitted_cycle_for_signal(
    info: Mapping[str, Any], signal_index: int, protocol: legacy_data.BacktestProtocolV53
) -> dict[str, Any]:
    # Training is expanding.  Validation/test may use all labels through the
    # fixed training cutoff, but never validation/test labels.
    train_index = max(index for index, month in enumerate(info["months"]) if month <= protocol.train_end)
    fit_index = min(signal_index, train_index)
    return fit_cycle_views_expanding_v536(
        np.asarray(info["returns"], dtype=float),
        info["cycles"],
        info["months"],
        signal_index=fit_index,
        production_cycles=(),
        minimum_train=protocol.minimum_cycle_train,
    )


def simulate_v536(
    info: Mapping[str, Any], spec: Mapping[str, Any], protocol: legacy_data.BacktestProtocolV53
) -> dict[str, Any]:
    months = info["months"]
    returns = np.asarray(info["returns"], dtype=float)
    macro = np.asarray(info["macro_innovations"], dtype=float)
    admission = np.asarray(info["macro_admission_matrix"], dtype=bool)
    parameters = StackParametersV53(**dict(spec["parameters"]))
    mode = str(spec["model_version"])
    previous = POLICY_WEIGHTS_V536.copy() if mode == "benchmark_relative" else np.asarray([0.25, 0.35, 0.15, 0.25])
    benchmark_drifted = POLICY_WEIGHTS_V536.copy()
    linear = np.asarray(protocol.transaction_cost_bps) / 10000.0
    quadratic = np.asarray(protocol.quadratic_cost)
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    last_target: np.ndarray | None = None
    last_diagnostics: dict[str, Any] = {}
    for signal_index in range(protocol.lookback_months - 1, len(returns) - 1):
        left = signal_index - protocol.lookback_months + 1
        fitted = _fitted_cycle_for_signal(info, signal_index, protocol)
        common = (
            returns[left : signal_index + 1],
            macro[left : signal_index + 1],
            admission[left : signal_index + 1],
            info["cycles"][signal_index],
            fitted,
            previous,
            parameters,
        )
        if mode == "benchmark_relative":
            target, diagnostics = allocate_relative_v536(
                *common,
                risk_budget_anchor_penalty=float(spec["risk_budget_anchor_penalty"]),
                transaction_cost_bps=protocol.transaction_cost_bps,
                quadratic_cost=protocol.quadratic_cost,
            )
        else:
            target, diagnostics = allocate_absolute_v536(
                *common,
                transaction_cost_bps=protocol.transaction_cost_bps,
                quadratic_cost=protocol.quadratic_cost,
            )
        optimizer = diagnostics["optimizer"]
        if optimizer["status"] != "optimal" or optimizer["solver"].get("fallback_used"):
            raise RuntimeError("v536_monthly_solver_release_gate_failed")
        realized = returns[signal_index + 1]
        month = months[signal_index + 1]
        change = target - previous
        cost = float(linear @ np.abs(change) + 0.5 * quadratic @ (change**2))
        benchmark_change = POLICY_WEIGHTS_V536 - benchmark_drifted
        benchmark_cost = float(linear @ np.abs(benchmark_change) + 0.5 * quadratic @ (benchmark_change**2))
        rows.append(
            {
                "month": month,
                "sample": legacy_data._sample(month, protocol),
                "gross_return": float(target @ realized),
                "net_return": float(target @ realized) - cost,
                "benchmark_gross_return": float(POLICY_WEIGHTS_V536 @ realized),
                "benchmark_return": float(POLICY_WEIGHTS_V536 @ realized) - benchmark_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": cost,
                "benchmark_cost": benchmark_cost,
                "solver_status": optimizer["status"],
                "fallback_used": bool(optimizer["solver"].get("fallback_used")),
                "max_violation": float(optimizer["constraints"]["max_violation"]),
                "maximum_complementarity_residual": float(optimizer["solver"]["maximum_complementarity_residual"]),
            }
        )
        weights.append(
            {
                "signal_month": months[signal_index],
                "realized_month": month,
                **{asset: float(target[index]) for index, asset in enumerate(ASSET_ORDER_V536)},
            }
        )
        last_target = target.copy()
        last_diagnostics = diagnostics
        previous = legacy_data._drift(target, realized)
        benchmark_drifted = legacy_data._drift(POLICY_WEIGHTS_V536, realized)

    # Latest known month is a signal month; solve once more without attaching
    # an unavailable future return.
    latest_signal = len(returns) - 1
    left = latest_signal - protocol.lookback_months + 1
    latest_fitted = _fitted_cycle_for_signal(info, latest_signal, protocol)
    current_common = (
        returns[left : latest_signal + 1],
        macro[left : latest_signal + 1],
        admission[left : latest_signal + 1],
        info["cycles"][latest_signal],
        latest_fitted,
        previous,
        parameters,
    )
    if mode == "benchmark_relative":
        current_target, current_diagnostics = allocate_relative_v536(
            *current_common,
            risk_budget_anchor_penalty=float(spec["risk_budget_anchor_penalty"]),
            transaction_cost_bps=protocol.transaction_cost_bps,
            quadratic_cost=protocol.quadratic_cost,
        )
    else:
        current_target, current_diagnostics = allocate_absolute_v536(
            *current_common,
            transaction_cost_bps=protocol.transaction_cost_bps,
            quadratic_cost=protocol.quadratic_cost,
        )
    split_metrics = {
        sample: metrics([row for row in rows if row["sample"] == sample])
        for sample in ("train", "validation", "test")
    }
    pretest_years = sorted({row["month"][:4] for row in rows if row["sample"] != "test"})
    return _json(
        {
            "spec": dict(spec),
            "metrics": split_metrics,
            "pretest_calendar_years": {
                year: metrics([row for row in rows if row["month"].startswith(year) and row["sample"] != "test"])
                for year in pretest_years
            },
            "returns": rows,
            "weights": weights,
            "last_rebalance_target": None if last_target is None else last_target.tolist(),
            "end_drifted_holdings": previous.tolist(),
            "current_signal_month": months[latest_signal],
            "current_signal_target": current_target.tolist(),
            "last_rebalance_diagnostics": last_diagnostics,
            "current_signal_diagnostics": current_diagnostics,
        }
    )


def selection_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "spec": dict(result["spec"]),
        "metrics": {"train": dict(result["metrics"]["train"]), "validation": dict(result["metrics"]["validation"])},
        "pretest_calendar_years": dict(result["pretest_calendar_years"]),
    }


def _number(row: Mapping[str, Any], key: str, default: float = -99.0) -> float:
    value = row.get(key)
    return default if value is None else float(value)


def select_pretest_v536(
    candidates: Sequence[Mapping[str, Any]], mode: str
) -> tuple[str | None, list[dict[str, Any]]]:
    if any("test" in (candidate.get("metrics") or {}) for candidate in candidates):
        raise ValueError("v536_selector_received_test")
    board: list[dict[str, Any]] = []
    for candidate in candidates:
        train, validation = candidate["metrics"]["train"], candidate["metrics"]["validation"]
        yearly = [row for row in candidate["pretest_calendar_years"].values() if int(row.get("months") or 0) >= 6]
        # User's latest mandate requires both versions to beat the policy
        # benchmark as well as improve Sharpe.  This is a release gate even for
        # the otherwise benchmark-independent absolute optimiser.
        eligible = all(
            _number(row, "annual_excess_return", -1.0) > 0.0
            and _number(row, "information_ratio") > 0.0
            and _number(row, "sharpe_improvement", -1.0) >= 0.0
            and _number(row, "max_active_drawdown", -1.0) >= -0.02
            for row in [train, validation, *yearly]
        )
        score = (
            min(_number(train, "information_ratio"), _number(validation, "information_ratio"))
            + 0.25 * min(_number(train, "sharpe_improvement"), _number(validation, "sharpe_improvement"))
            + 10.0 * min(_number(train, "annual_excess_return"), _number(validation, "annual_excess_return"))
            - 0.25 * abs(_number(train, "information_ratio") - _number(validation, "information_ratio"))
            - 0.50 * _number(validation, "average_turnover", 0.0)
        )
        board.append({"id": candidate["spec"]["id"], "mode": mode, "eligible": bool(eligible), "score": score, "train": train, "validation": validation})
    board.sort(key=lambda row: (-row["score"], row["id"]))
    return next((row["id"] for row in board if row["eligible"]), None), board


def build_research_v536(database: Path, protocol: legacy_data.BacktestProtocolV53) -> dict[str, Any]:
    protocol.validate()
    info = prepare_information_set(database, protocol)
    results = [simulate_v536(info, spec, protocol) for spec in candidate_grid_v536()]
    selected: dict[str, str | None] = {}
    boards: dict[str, list[dict[str, Any]]] = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        payloads = [selection_payload(row) for row in results if row["spec"]["model_version"] == mode]
        selected[mode], boards[mode] = select_pretest_v536(payloads, mode)
    reports = {
        mode: next((row for row in results if row["spec"]["id"] == identifier), None)
        for mode, identifier in selected.items()
    }
    return _json(
        {
            "schema_version": SCHEMA,
            "status": "invalid_for_promotion_until_future_pristine_holdout",
            "asset_order": list(ASSET_ORDER_V536),
            "policy_benchmark_internal": POLICY_WEIGHTS_V536.tolist(),
            "protocol": asdict(protocol),
            "candidate_grid": list(candidate_grid_v536()),
            "selected_ids_pretest": selected,
            "selection_leaderboards_without_test": boards,
            "selected_reports_with_retrospective_test_attached_after_freeze": reports,
            "results": results,
            "data": {"price_audit": info["price_audit"], "macro_audit": info["macro_audit"], "lineage": info["lineage"]},
            "governance": {
                "selection_uses_test": False,
                "selector_input_contains_test": False,
                "test_role": "retrospective_report_only_not_pristine",
                "training_view_fit": "expanding_target_month_safe",
                "production_admitted_cycles": [],
                "macro_PIT_admission_by_factor": [False, False, False, False],
                "macro_blend_effective": 0.0,
                "monkey_patch_used": False,
                "relative_direct_active_optimizer": True,
                "absolute_policy_benchmark_model_input": False,
                "equal_weight_optimizer_input": False,
                "deployment_allowed": False,
                "promotion_blockers": [
                    "zero_D3_cycle_factors",
                    "validation_only_12_months",
                    "retrospective_test_not_pristine",
                    "future_24m_shadow_holdout_required",
                ],
            },
        }
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    payload = build_research_v536(Path(arguments.database), legacy_data.BacktestProtocolV53())
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "selected_ids_pretest": payload["selected_ids_pretest"], "output": str(output.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
