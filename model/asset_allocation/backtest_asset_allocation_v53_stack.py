"""Test-blind backtest harness for the complete v5.3 allocation stack.

This is a research reporter, not a deployment builder.  It composes the
five-cycle state layer, macro/statistical covariance, constrained risk budget,
Black--Litterman and both optimisers from explicit functions.  Selector inputs
physically exclude retrospective test rows; test reporting is attached only
after a pre-test candidate id is frozen.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v5 as base
from asset_allocation_v53_stack import (
    POLICY_WEIGHTS_V53,
    StackParametersV53,
    allocate_absolute_v53,
    allocate_relative_v53,
)
from asset_data_authoritative_v51 import load_local_authoritative_execution_prices_v51
from cycle_macro_models_v5 import (
    build_macro_cycle_probabilities_v5,
    build_pring_market_probabilities_v5,
    merge_cycle_history_v5,
)
from cycle_view_training_v53 import fit_frozen_cycle_view_model_v53


SCHEMA = "asset-allocation-v5.3-stack-backtest/1"
ASSETS = ("equity", "bond", "gold", "commodity")


@dataclass(frozen=True)
class BacktestProtocolV53:
    train_end: str = "202312"
    validation_end: str = "202412"
    lookback_months: int = 24
    minimum_cycle_train: int = 18
    transaction_cost_bps: tuple[float, float, float, float] = (5.0, 2.0, 5.0, 6.0)
    quadratic_cost: tuple[float, float, float, float] = (0.0010, 0.0005, 0.0015, 0.0020)
    selection_uses_test: bool = False

    def validate(self) -> None:
        if self.selection_uses_test:
            raise ValueError("v53_stack_test_selection_forbidden")
        if self.lookback_months < 18:
            raise ValueError("v53_stack_lookback_too_short")
        if not self.train_end < self.validation_end:
            raise ValueError("v53_stack_splits_invalid")


def candidate_grid_v53_stack() -> tuple[dict[str, Any], ...]:
    """Twelve predeclared candidates: six relative and six absolute."""

    candidates: list[dict[str, Any]] = []
    for mode, prefix in (("benchmark_relative", "REL"), ("absolute_no_benchmark", "ABS")):
        identifier = 0
        for view_scale in (0.0015, 0.0025, 0.0040):
            for active_or_anchor in (0.01, 0.03):
                identifier += 1
                parameters = StackParametersV53(
                    market_view_scale_monthly=view_scale,
                    active_l2_penalty=active_or_anchor if mode == "benchmark_relative" else 0.02,
                    absolute_anchor_penalty=active_or_anchor * 50.0 if mode == "absolute_no_benchmark" else 1.25,
                    macro_blend_weight=0.25,
                )
                candidates.append(
                    {
                        "id": f"V53S-{prefix}-{identifier:02d}",
                        "model_version": mode,
                        "parameters": asdict(parameters),
                    }
                )
    return tuple(candidates)


def _macro_rows_read_only(database: Path) -> list[dict[str, Any]]:
    import sqlite3

    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute("select * from macro_monthly order by month")]
    finally:
        connection.close()
    # No row in the current warehouse carries release/vintage metadata.  Keep
    # the numerical values visible for research diagnostics but fail PIT closed.
    for row in rows:
        row["_pit_verified"] = False
    return rows


def _sample(month: str, protocol: BacktestProtocolV53) -> str:
    if month <= protocol.train_end:
        return "train"
    if month <= protocol.validation_end:
        return "validation"
    return "test"


def _drift(weights: np.ndarray, realized: np.ndarray) -> np.ndarray:
    values = weights * (1.0 + realized)
    return values / max(float(values.sum()), 1.0e-12)


def _drawdown(values: np.ndarray) -> float:
    nav = np.cumprod(1.0 + values)
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"months": 0}
    strategy = np.asarray([row["net_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    active = strategy - benchmark
    relative_nav = np.cumprod((1.0 + strategy) / np.maximum(1.0 + benchmark, 1.0e-12))
    count = len(rows)
    strategy_vol = float(strategy.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    benchmark_vol = float(benchmark.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    tracking_error = float(active.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    strategy_sharpe = float(strategy.mean() * 12.0 / strategy_vol) if strategy_vol > 1.0e-12 else None
    benchmark_sharpe = float(benchmark.mean() * 12.0 / benchmark_vol) if benchmark_vol > 1.0e-12 else None
    return {
        "months": count,
        "annual_return": float(np.prod(1.0 + strategy) ** (12.0 / count) - 1.0),
        "benchmark_annual_return": float(np.prod(1.0 + benchmark) ** (12.0 / count) - 1.0),
        "annual_excess_return": float(relative_nav[-1] ** (12.0 / count) - 1.0),
        "annual_volatility": strategy_vol,
        "benchmark_annual_volatility": benchmark_vol,
        "sharpe": strategy_sharpe,
        "benchmark_sharpe": benchmark_sharpe,
        "sharpe_improvement": None if strategy_sharpe is None or benchmark_sharpe is None else strategy_sharpe - benchmark_sharpe,
        "tracking_error": tracking_error,
        "information_ratio": float(active.mean() * 12.0 / tracking_error) if tracking_error > 1.0e-12 else None,
        "max_drawdown": _drawdown(strategy),
        "benchmark_max_drawdown": _drawdown(benchmark),
        "max_active_drawdown": float(np.min(relative_nav / np.maximum.accumulate(relative_nav) - 1.0)),
        "positive_active_month_rate": float(np.mean(active > 0.0)),
        "average_turnover": float(np.mean([row["turnover"] for row in rows])),
        "annual_cost_drag": float(np.mean([row["cost"] for row in rows]) * 12.0),
        "benchmark_annual_cost_drag": float(np.mean([row["benchmark_cost"] for row in rows]) * 12.0),
    }


def prepare_information_set(
    database: Path, protocol: BacktestProtocolV53
) -> dict[str, Any]:
    panel, lineage = load_local_authoritative_execution_prices_v51(database)
    price_months, prices, price_audit = base.monthly_prices_v5(panel)
    months = price_months[1:]
    returns = prices[1:] / prices[:-1] - 1.0
    macro_rows = _macro_rows_read_only(database)
    safe_macro, macro_audit = base._pit_safe_macro_rows(macro_rows)
    macro_cycles = build_macro_cycle_probabilities_v5(safe_macro, train_end=protocol.train_end)
    pring = build_pring_market_probabilities_v5(months, returns, train_end=protocol.train_end)
    cycles = merge_cycle_history_v5(months, pring, macro_cycles)
    innovations, macro_admitted = base._macro_innovations(cycles)
    frozen_views = fit_frozen_cycle_view_model_v53(
        returns,
        cycles,
        months,
        train_end=protocol.train_end,
        minimum_train=protocol.minimum_cycle_train,
    )
    return {
        "months": months,
        "returns": returns,
        "cycles": cycles,
        "macro_innovations": innovations,
        "macro_admitted": macro_admitted,
        "frozen_views": frozen_views,
        "price_audit": price_audit,
        "macro_audit": macro_audit,
        "lineage": lineage,
    }


def simulate(
    info: Mapping[str, Any],
    spec: Mapping[str, Any],
    protocol: BacktestProtocolV53,
) -> dict[str, Any]:
    months = info["months"]
    returns = np.asarray(info["returns"], dtype=float)
    cycles = info["cycles"]
    macro = np.asarray(info["macro_innovations"], dtype=float)
    admitted = np.asarray(info["macro_admitted"], dtype=bool)
    parameters = StackParametersV53(**dict(spec["parameters"]))
    mode = str(spec["model_version"])
    previous = POLICY_WEIGHTS_V53.copy() if mode == "benchmark_relative" else np.asarray([0.25, 0.35, 0.15, 0.25])
    benchmark_drifted = POLICY_WEIGHTS_V53.copy()
    costs = np.asarray(protocol.transaction_cost_bps) / 10000.0
    quadratic = np.asarray(protocol.quadratic_cost)
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    current_diagnostics: dict[str, Any] = {}
    start = protocol.lookback_months - 1
    for signal_index in range(start, len(returns) - 1):
        left = signal_index - protocol.lookback_months + 1
        if mode == "benchmark_relative":
            target, diagnostics = allocate_relative_v53(
                returns[left : signal_index + 1],
                macro[left : signal_index + 1],
                admitted[left : signal_index + 1],
                cycles[signal_index],
                info["frozen_views"],
                previous,
                parameters,
                transaction_cost_bps=protocol.transaction_cost_bps,
                quadratic_cost=protocol.quadratic_cost,
            )
        else:
            target, diagnostics = allocate_absolute_v53(
                returns[left : signal_index + 1],
                macro[left : signal_index + 1],
                admitted[left : signal_index + 1],
                cycles[signal_index],
                info["frozen_views"],
                previous,
                parameters,
                transaction_cost_bps=protocol.transaction_cost_bps,
                quadratic_cost=protocol.quadratic_cost,
            )
        realized = returns[signal_index + 1]
        month = months[signal_index + 1]
        change = target - previous
        model_cost = float(costs @ np.abs(change) + 0.5 * quadratic @ (change * change))
        benchmark_change = POLICY_WEIGHTS_V53 - benchmark_drifted
        benchmark_cost = float(costs @ np.abs(benchmark_change) + 0.5 * quadratic @ (benchmark_change * benchmark_change))
        rows.append(
            {
                "month": month,
                "sample": _sample(month, protocol),
                "gross_return": float(target @ realized),
                "net_return": float(target @ realized) - model_cost,
                "benchmark_gross_return": float(POLICY_WEIGHTS_V53 @ realized),
                "benchmark_return": float(POLICY_WEIGHTS_V53 @ realized) - benchmark_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": model_cost,
                "benchmark_cost": benchmark_cost,
            }
        )
        weights.append(
            {
                "signal_month": months[signal_index],
                "realized_month": month,
                **{asset: float(target[index]) for index, asset in enumerate(ASSETS)},
            }
        )
        previous = _drift(target, realized)
        benchmark_drifted = _drift(POLICY_WEIGHTS_V53, realized)
        current_diagnostics = diagnostics
    split_metrics = {
        sample: metrics([row for row in rows if row["sample"] == sample])
        for sample in ("train", "validation", "test")
    }
    pretest_years = {
        year: metrics([row for row in rows if row["month"].startswith(year)])
        for year in ("2022", "2023", "2024")
    }
    return {
        "spec": dict(spec),
        "metrics": split_metrics,
        "pretest_calendar_years": pretest_years,
        "returns": rows,
        "weights": weights,
        "current_weights": previous.tolist(),
        "current_diagnostics": current_diagnostics,
    }


def _number(payload: Mapping[str, Any], key: str, default: float = -99.0) -> float:
    value = payload.get(key)
    return default if value is None else float(value)


def selection_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Physically remove every test row/metric before selection."""

    return {
        "spec": dict(result["spec"]),
        "metrics": {
            "train": dict(result["metrics"]["train"]),
            "validation": dict(result["metrics"]["validation"]),
        },
        "pretest_calendar_years": dict(result["pretest_calendar_years"]),
        "pretest_returns": [dict(row) for row in result["returns"] if row["sample"] != "test"],
    }


def select_pretest(
    candidates: Sequence[Mapping[str, Any]], mode: str
) -> tuple[str | None, list[dict[str, Any]]]:
    if any("test" in (candidate.get("metrics") or {}) for candidate in candidates):
        raise ValueError("v53_selector_received_test_metrics")
    leaderboard: list[dict[str, Any]] = []
    for candidate in candidates:
        train = candidate["metrics"]["train"]
        validation = candidate["metrics"]["validation"]
        if mode == "benchmark_relative":
            yearly = [row for row in candidate["pretest_calendar_years"].values() if int(row.get("months") or 0) >= 6]
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
        else:
            eligible = (
                int(train.get("months") or 0) >= 12
                and int(validation.get("months") or 0) >= 12
                and _number(train, "sharpe") > 0.0
                and _number(validation, "sharpe") > 0.0
            )
            score = (
                min(_number(train, "sharpe"), _number(validation, "sharpe"))
                - 0.20 * abs(_number(train, "sharpe") - _number(validation, "sharpe"))
                - 0.50 * _number(validation, "average_turnover", 0.0)
            )
        leaderboard.append(
            {
                "id": candidate["spec"]["id"],
                "eligible": bool(eligible),
                "score": score,
                "train": train,
                "validation": validation,
            }
        )
    leaderboard.sort(key=lambda row: (-row["score"], row["id"]))
    selected = next((row["id"] for row in leaderboard if row["eligible"]), None)
    return selected, leaderboard


def build_research(database: Path, protocol: BacktestProtocolV53) -> dict[str, Any]:
    protocol.validate()
    info = prepare_information_set(database, protocol)
    results = [simulate(info, spec, protocol) for spec in candidate_grid_v53_stack()]
    selected: dict[str, str | None] = {}
    leaderboards: dict[str, list[dict[str, Any]]] = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        family = [selection_payload(row) for row in results if row["spec"]["model_version"] == mode]
        selected[mode], leaderboards[mode] = select_pretest(family, mode)
    final_reports = {
        mode: next((row for row in results if row["spec"]["id"] == selected_id), None)
        for mode, selected_id in selected.items()
    }
    return {
        "schema_version": SCHEMA,
        "status": "research_only",
        "asset_order": list(ASSETS),
        "policy_benchmark_internal": POLICY_WEIGHTS_V53.tolist(),
        "protocol": asdict(protocol),
        "candidate_grid": list(candidate_grid_v53_stack()),
        "selected_ids_pretest": selected,
        "selection_leaderboards_without_test": leaderboards,
        "selected_reports_with_retrospective_test_attached_after_freeze": final_reports,
        "results": results,
        "data": {
            "price_audit": info["price_audit"],
            "macro_audit": info["macro_audit"],
            "lineage": info["lineage"],
        },
        "governance": {
            "selection_uses_test": False,
            "selector_input_contains_test": False,
            "test_role": "retrospective_report_only_not_pristine",
            "macro_pit_production_admitted": False,
            "macro_blend_effective": 0.0,
            "deployment_allowed": False,
            "statistical_promotion_status": "blocked_pending_24m_future_pristine_holdout",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    payload = build_research(Path(arguments.database), BacktestProtocolV53())
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    summary = {
        "status": payload["status"],
        "selected_ids_pretest": payload["selected_ids_pretest"],
        "selected_metrics": {
            mode: None if report is None else report["metrics"]
            for mode, report in payload["selected_reports_with_retrospective_test_attached_after_freeze"].items()
        },
        "output": str(output.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
