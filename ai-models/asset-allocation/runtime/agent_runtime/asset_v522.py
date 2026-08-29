from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


ASSET_ORDER = ("equity", "bond", "gold", "commodity")
CYCLE_ORDER = ("kondratieff", "juglar", "kitchin", "merrill", "pring")
PROFILE_ALIASES = {
    "\u7a33\u5065": "conservative",
    "\u5e73\u8861": "balanced",
    "\u6743\u76ca\u4f18\u5148": "equity_preferred",
    "conservative": "conservative",
    "balanced": "balanced",
    "equity_preferred": "equity_preferred",
    "recommended": "balanced",
    "benchmark_relative": "equity_preferred",
    "absolute_no_benchmark": "conservative",
}
PROFILE_TARGETS = {
    "balanced": "recommended",
    "equity_preferred": "benchmark_relative",
    "conservative": "absolute_no_benchmark",
}
METRIC_FIELDS = (
    "status",
    "start",
    "end",
    "observations",
    "annual_return",
    "benchmark_annual_return",
    "annual_excess",
    "sharpe",
    "excess_sharpe",
    "information_ratio",
    "max_drawdown",
    "annual_turnover",
    "turnover",
    "annual_volatility",
    "annual_excess_return",
    "average_turnover",
    "calmar",
    "months",
    "positive_month_rate",
    "total_return",
)


def _compact_metrics(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: value.get(key) for key in METRIC_FIELDS if key in value}


def _cycle_summary(current: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = current.get("cycles") or {}
    states: dict[str, Any] = {"month": current.get("month")}
    probabilities: dict[str, Any] = {}
    for name in CYCLE_ORDER:
        row = rows.get(name) or {}
        states[name] = {
            "state": row.get("state_name") or row.get("state"),
            "confidence": row.get("confidence"),
        }
        probabilities[name] = row.get("probabilities")
    return states, probabilities


def _cycle_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    availability = payload.get("cycle_factor_availability") or {}
    rows = availability.get("cycles") or {}
    cycles: dict[str, Any] = {}
    for name in CYCLE_ORDER:
        row = rows.get(name) or {}
        cycles[name] = {
            "state": row.get("state"),
            "confidence": row.get("confidence"),
            "admitted": row.get("eligible_for_views"),
            "data_status": row.get("data_status"),
            "required_pillars": row.get("required_pillars"),
            "present_pillars": row.get("present_pillars"),
            "missing_pillars": row.get("missing_pillars"),
            "missing_required_factors": row.get("missing_required_factors"),
            "observed_fields": row.get("observed_fields"),
            "admission_reason": row.get("admission_reason"),
            "duration_model": row.get("duration_model"),
        }
    return {
        "factor_schema_version": availability.get("factor_schema_version"),
        "cycles": cycles,
        "admitted_cycles": availability.get("admitted_cycles"),
        "conflicts": availability.get("conflicts"),
    }


def _policy_contract(payload: dict[str, Any]) -> dict[str, Any]:
    benchmark = payload.get("benchmark") or {}
    weights = benchmark.get("weights") or {}
    backtest = payload.get("backtest") or {}
    strategies = backtest.get("strategies") or {}
    compact_display = (
        (backtest.get("display_benchmarks") or {}).get("equal_weight_25") or {}
    )
    strategy_display = strategies.get("equal_weight_25") or {}
    display = compact_display or strategy_display
    internal_order = (
        benchmark.get("internal_asset_order")
        or payload.get("asset_order")
        or list(ASSET_ORDER)
    )
    display_weights = display.get("weights")
    if not isinstance(display_weights, dict):
        current_weights = strategy_display.get("current_weights")
        if (
            isinstance(current_weights, (list, tuple))
            and len(current_weights) == len(internal_order)
        ):
            display_weights = {
                str(asset): float(current_weights[index])
                for index, asset in enumerate(internal_order)
            }
        else:
            display_weights = None
    return {
        "policy_anchor": {
            "id": benchmark.get("id"),
            "internal_asset_order": internal_order,
            "weights": weights,
            "weights_in_internal_order": [weights.get(asset) for asset in ASSET_ORDER],
            "role": "optimizer_anchor_and_active_return_reference",
        },
        "main_nav_display_benchmark": {
            "id": display.get("id") or ("equal_weight_25" if display else None),
            "weights": display_weights,
            "role": display.get("role"),
            "optimizer_input": display.get("optimizer_input"),
            "active_return_reference": display.get("active_return_reference"),
        },
        "separation_rule": (
            "The 60/15/10/15 internal policy anchor drives optimization, "
            "active weights and active-return metrics. The 25/25/25/25 series "
            "is only the comparison line on the main NAV chart."
        ),
    }


def _governance(payload: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get("quality") or {}
    decision = payload.get("deployment_decision") or {}
    contract = payload.get("model_contract") or {}
    selection_uses_test = contract.get("selection_uses_test")
    if selection_uses_test is None:
        selection_uses_test = decision.get("uses_retrospective_test")
    return {
        "service_authorization": {
            "deployment_decision": decision,
            "authorization_gate": quality.get("promotion_gate"),
        },
        "statistical_evidence": {
            "gate": quality.get("statistical_evidence_gate"),
            "by_version": quality.get("statistical_evidence_by_version"),
        },
        "selection_uses_retrospective_test": selection_uses_test,
        "service_status": payload.get("status"),
        "quality_status": quality.get("status"),
    }


def _strategy_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    strategies = ((payload.get("backtest") or {}).get("strategies") or {})
    result: dict[str, Any] = {}
    for name, row in strategies.items():
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics") or {}
        if any(key in metrics for key in METRIC_FIELDS):
            result[name] = _compact_metrics(metrics)
            continue
        if isinstance(metrics, dict):
            result[name] = {
                split: _compact_metrics(value)
                for split, value in metrics.items()
                if isinstance(value, dict)
            }
    return result


def _allocation(
    params: dict[str, Any],
    allocations: dict[str, Any],
    param: Callable[..., Any],
    error_type: type[RuntimeError],
) -> tuple[str, str, dict[str, Any]]:
    raw = str(param(params, "profile", "\u753b\u50cf", default="balanced"))
    profile = PROFILE_ALIASES.get(raw, raw)
    target = PROFILE_TARGETS.get(profile)
    allocation = allocations.get(target) if target else None
    if not isinstance(allocation, dict):
        raise error_type(
            f"Unknown asset profile: {raw}; choose conservative, balanced, "
            "or equity_preferred"
        )
    return profile, target, allocation


def query_asset_v522(
    operation: str,
    params: dict[str, Any],
    payload: dict[str, Any],
    path: Path,
    *,
    response: Callable[[str, str, dict[str, Any], Path, Any], dict[str, Any]],
    param: Callable[..., Any],
    error_type: type[RuntimeError],
) -> dict[str, Any]:
    allocations = payload.get("allocations") or {}
    current = allocations.get("current_cycle") or {}
    profile, allocation_key, allocation = _allocation(
        params, allocations, param, error_type
    )
    cycle, probabilities = _cycle_summary(current)
    policy = _policy_contract(payload)
    governance = _governance(payload)
    evidence = _cycle_evidence(payload)

    if operation == "cycle":
        result: dict[str, Any] = {
            "cycle": cycle,
            "state_probabilities": probabilities,
            "five_cycle_admission": evidence,
            "policy_and_display_benchmark": policy,
            "service_authorization_and_statistical_evidence": governance,
        }
        if payload.get("model_evidence_catalog") is not None:
            result["model_evidence_catalog"] = payload.get("model_evidence_catalog")
    elif operation == "backtest":
        backtest = payload.get("backtest") or {}
        result = {
            "backtest": {
                "metrics": _strategy_metrics(payload),
                "sample_splits": backtest.get("sample_splits"),
                "selection_audit": backtest.get("selection_audit"),
                "comparison_policy": backtest.get("comparison_policy"),
            },
            "policy_and_display_benchmark": policy,
            "service_authorization_and_statistical_evidence": governance,
        }
    elif operation == "current":
        result = {
            "cycle": cycle,
            "profile": profile,
            "allocation_key": allocation_key,
            "weights": allocation.get("weights"),
            "risk_contribution": allocation.get("risk_contribution"),
            "turnover": (allocation.get("metadata") or {}).get(
                "current_rebalance_turnover"
            ),
            "policy_and_display_benchmark": policy,
            "five_cycle_admission": evidence,
            "service_authorization_and_statistical_evidence": governance,
        }
        if payload.get("model_evidence_catalog") is not None:
            result["model_evidence_catalog"] = payload.get("model_evidence_catalog")
    else:
        raise error_type(
            "Asset-allocation operation must be current, cycle, or backtest"
        )
    return response("asset-allocation", operation, payload, path, result)
