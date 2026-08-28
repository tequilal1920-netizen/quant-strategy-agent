"""Executable entrypoint for the truth-gated complete v5.3.2 research stack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

import backtest_asset_allocation_v53_stack as base
from asset_allocation_v532_stack import allocate_absolute_v532, allocate_relative_v532
from asset_allocation_v53_stack import POLICY_WEIGHTS_V53, StackParametersV53


SCHEMA = "asset-allocation-v5.3.2-stack-backtest/1"


def _json_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def simulate_v532(
    info: Mapping[str, Any],
    spec: Mapping[str, Any],
    protocol: base.BacktestProtocolV53,
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
        common = (
            returns[left : signal_index + 1],
            macro[left : signal_index + 1],
            admitted[left : signal_index + 1],
            cycles[signal_index],
            info["frozen_views"],
            previous,
            parameters,
        )
        kwargs = {
            "transaction_cost_bps": protocol.transaction_cost_bps,
            "quadratic_cost": protocol.quadratic_cost,
        }
        target, diagnostics = (
            allocate_relative_v532(*common, **kwargs)
            if mode == "benchmark_relative"
            else allocate_absolute_v532(*common, **kwargs)
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
                "sample": base._sample(month, protocol),
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
                **{asset: float(target[index]) for index, asset in enumerate(base.ASSETS)},
            }
        )
        previous = base._drift(target, realized)
        benchmark_drifted = base._drift(POLICY_WEIGHTS_V53, realized)
        current_diagnostics = diagnostics
    split_metrics = {
        sample: base.metrics([row for row in rows if row["sample"] == sample])
        for sample in ("train", "validation", "test")
    }
    return {
        "spec": dict(spec),
        "metrics": split_metrics,
        "pretest_calendar_years": {
            year: base.metrics([row for row in rows if row["month"].startswith(year)])
            for year in ("2022", "2023", "2024")
        },
        "returns": rows,
        "weights": weights,
        "current_weights": previous.tolist(),
        "current_diagnostics": current_diagnostics,
    }


def build_research_v532(
    database: Path, protocol: base.BacktestProtocolV53
) -> dict[str, Any]:
    protocol.validate()
    info = base.prepare_information_set(database, protocol)
    results = [simulate_v532(info, spec, protocol) for spec in base.candidate_grid_v53_stack()]
    selected: dict[str, str | None] = {}
    leaderboards: dict[str, list[dict[str, Any]]] = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        selector_rows = [
            base.selection_payload(row)
            for row in results
            if row["spec"]["model_version"] == mode
        ]
        selected[mode], leaderboards[mode] = base.select_pretest(selector_rows, mode)
    reports = {
        mode: next((row for row in results if row["spec"]["id"] == identifier), None)
        for mode, identifier in selected.items()
    }
    return _json_value(
        {
            "schema_version": SCHEMA,
            "status": "research_only",
            "asset_order": list(base.ASSETS),
            "policy_benchmark_internal": POLICY_WEIGHTS_V53.tolist(),
            "protocol": base.asdict(protocol),
            "candidate_grid": list(base.candidate_grid_v53_stack()),
            "selected_ids_pretest": selected,
            "selection_leaderboards_without_test": leaderboards,
            "selected_reports_with_retrospective_test_attached_after_freeze": reports,
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
                "production_admitted_cycles": [],
                "D2_shadow_cycles": ["pring"],
                "macro_pit_production_admitted": False,
                "macro_blend_effective": 0.0,
                "cycle_views_effective_in_production_weights": False,
                "deployment_allowed": False,
                "statistical_promotion_status": "blocked_pending_24m_future_pristine_holdout",
            },
        }
    )


def main() -> int:
    arguments = base.parse_args()
    payload = build_research_v532(Path(arguments.database), base.BacktestProtocolV53())
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_ids_pretest": payload["selected_ids_pretest"],
                "selected_metrics": {
                    mode: None if report is None else report["metrics"]
                    for mode, report in payload["selected_reports_with_retrospective_test_attached_after_freeze"].items()
                },
                "output": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
