"""Pre-registered 24-candidate v5.3.5 complete-stack research.

Eighteen relative candidates calibrate only three economically explicit
quantities on train/validation: market-view scale, BL mean-uncertainty penalty
and risk-budget anchoring.  Six absolute candidates retain the earlier complete
stack grid.  The selector receives no test fields.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

import backtest_asset_allocation_v53_stack as base
import asset_allocation_v533_stack as stack
from asset_allocation_v534_stack import truth_gated_risk_budget_v534
from asset_allocation_v53_stack import POLICY_WEIGHTS_V53, StackParametersV53


SCHEMA = "asset-allocation-v5.3.5-stack-backtest/1"


def candidate_grid_v535() -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    identifier = 0
    for view_scale in (0.0025, 0.0040):
        for uncertainty_penalty in (0.025, 0.075, 0.150):
            for risk_budget_penalty in (0.10, 0.40, 0.80):
                identifier += 1
                parameters = StackParametersV53(
                    market_view_scale_monthly=view_scale,
                    uncertainty_penalty=uncertainty_penalty,
                    active_l2_penalty=0.01,
                    macro_blend_weight=0.25,
                )
                output.append(
                    {
                        "id": f"V535-REL-{identifier:02d}",
                        "model_version": "benchmark_relative",
                        "parameters": asdict(parameters),
                        "risk_budget_anchor_penalty": risk_budget_penalty,
                    }
                )
    for spec in base.candidate_grid_v53_stack():
        if spec["model_version"] == "absolute_no_benchmark":
            cloned = dict(spec)
            cloned["id"] = cloned["id"].replace("V53S-", "V535-")
            output.append(cloned)
    if len(output) != 24 or len({row["id"] for row in output}) != 24:
        raise RuntimeError("v535_candidate_grid_must_have_24_unique_candidates")
    return tuple(output)


def _json(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def simulate_v535(
    info: Mapping[str, Any], spec: Mapping[str, Any], protocol: base.BacktestProtocolV53
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
    linear_costs = np.asarray(protocol.transaction_cost_bps) / 10000.0
    quadratic_costs = np.asarray(protocol.quadratic_cost)
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    for signal_index in range(protocol.lookback_months - 1, len(returns) - 1):
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
        if mode == "benchmark_relative":
            kwargs["risk_budget_anchor_penalty"] = float(
                spec["risk_budget_anchor_penalty"]
            )
            target, diagnostics = stack.allocate_relative_v533(*common, **kwargs)
        else:
            target, diagnostics = stack.allocate_absolute_v533(*common, **kwargs)
        realized = returns[signal_index + 1]
        month = months[signal_index + 1]
        change = target - previous
        cost = float(linear_costs @ np.abs(change) + 0.5 * quadratic_costs @ (change * change))
        benchmark_change = POLICY_WEIGHTS_V53 - benchmark_drifted
        benchmark_cost = float(
            linear_costs @ np.abs(benchmark_change)
            + 0.5 * quadratic_costs @ (benchmark_change * benchmark_change)
        )
        rows.append(
            {
                "month": month,
                "sample": base._sample(month, protocol),
                "gross_return": float(target @ realized),
                "net_return": float(target @ realized) - cost,
                "benchmark_gross_return": float(POLICY_WEIGHTS_V53 @ realized),
                "benchmark_return": float(POLICY_WEIGHTS_V53 @ realized) - benchmark_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": cost,
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
    return {
        "spec": dict(spec),
        "metrics": {
            sample: base.metrics([row for row in rows if row["sample"] == sample])
            for sample in ("train", "validation", "test")
        },
        "pretest_calendar_years": {
            year: base.metrics([row for row in rows if row["month"].startswith(year)])
            for year in ("2022", "2023", "2024")
        },
        "returns": rows,
        "weights": weights,
        "current_weights": previous.tolist(),
        "current_diagnostics": diagnostics,
    }


def build_research_v535(database: Path, protocol: base.BacktestProtocolV53) -> dict[str, Any]:
    protocol.validate()
    information = base.prepare_information_set(database, protocol)
    original = stack._truth_gated_risk_budget
    stack._truth_gated_risk_budget = truth_gated_risk_budget_v534
    try:
        results = [simulate_v535(information, spec, protocol) for spec in candidate_grid_v535()]
    finally:
        stack._truth_gated_risk_budget = original
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
    return _json(
        {
            "schema_version": SCHEMA,
            "status": "research_only",
            "asset_order": list(base.ASSETS),
            "policy_benchmark_internal": POLICY_WEIGHTS_V53.tolist(),
            "protocol": asdict(protocol),
            "candidate_count": 24,
            "candidate_grid": list(candidate_grid_v535()),
            "selected_ids_pretest": selected,
            "selection_leaderboards_without_test": leaderboards,
            "selected_reports_with_retrospective_test_attached_after_freeze": reports,
            "results": results,
            "data": {
                "price_audit": information["price_audit"],
                "macro_audit": information["macro_audit"],
                "lineage": information["lineage"],
            },
            "governance": {
                "selection_uses_test": False,
                "selector_input_contains_test": False,
                "test_role": "retrospective_report_only_not_pristine",
                "production_admitted_cycles": [],
                "D2_shadow_cycles": ["pring"],
                "macro_pit_production_admitted": False,
                "macro_blend_effective": 0.0,
                "relative_risk_budget_integrated_in_objective": True,
                "relative_active_optimization_single_stage": True,
                "absolute_policy_benchmark_model_input": False,
                "negative_risk_contribution_projection_applied": False,
                "research_runner_uses_scoped_truth_gate": True,
                "production_builder_must_use_explicit_functions": True,
                "deployment_allowed": False,
                "statistical_promotion_status": "blocked_pending_24m_future_pristine_holdout",
            },
        }
    )


def main() -> int:
    arguments = base.parse_args()
    payload = build_research_v535(Path(arguments.database), base.BacktestProtocolV53())
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
