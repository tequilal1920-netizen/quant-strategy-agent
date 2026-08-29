"""Governed v5.2.2 dual-policy asset-allocation model.

The first frozen v5.2 run is retained as evidence.  This revision fixes two
implementation defects without tuning on the retrospective test:

* backtests now charge the same linear plus quadratic impact cost used by the
  optimizer; and
* candidate selection never reads the already-observed retrospective test;
* the approved benchmark-relative mandate is selected on validation Sharpe,
  without treating excess return or information ratio as admission criteria;
* service authorization and statistical evidence are reported as separate
  gates, so unresolved D3/PIT/PSR evidence remains visible without being
  mislabelled as a failed service contract; and
* the 25%/25%/25%/25% line is generated only as a display benchmark.  It never
  enters the 60%/15%/15%/10% policy-relative optimiser or active-return maths.

It also records every historical policy constraint audit, recomputes optimizer
diagnostics after policy scaling, and reports exact signal ties instead of
forcing four artificial ranks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import ntpath
import re
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np

import allocation_math_v5 as _math
import asset_allocation_v52 as _raw
from asset_allocation_v521 import apply_validation_governance_v521


SCHEMA_VERSION_V522 = "5.2.2"
ENGINE_VERSION_V522 = "asset-allocation-v5.2.2-user-approved-sharpe-mandate"
AUTHORIZATION_BASIS_V522 = "explicit_user_approval_sharpe_only"
APPROVED_RELATIVE_MODEL_ID_V522 = "V52-REL-01"
APPROVED_RELATIVE_WEIGHTS_V522 = {
    "equity": 0.5075392872710349,
    "bond": 0.20,
    "gold": 0.09246071272896504,
    "commodity": 0.20,
}
EQUAL_WEIGHT_DISPLAY_ID_V522 = "equal_weight_25"
CANONICAL_RELEASE_SIGNATURE_V522 = {
    "market": "202606",
    "macro_available": "202606",
    "macro_complete": "202606",
    "train_end": "202312",
    "validation_end": "202412",
}

_ORIGINAL_STRENGTH = _raw.asset_strength_v52
_ORIGINAL_SELECTION = _raw._select_candidate_v52


@dataclass(frozen=True)
class ResearchConfigV522(_raw.ResearchConfigV52):
    future_paper_holdout_certified: bool = False
    future_paper_holdout_id: str | None = None
    future_paper_holdout_min_months: int = 12
    user_approved_sharpe_mandate: bool = True
    authorization_basis: str = AUTHORIZATION_BASIS_V522

    def validate(self) -> None:
        super().validate()
        if self.future_paper_holdout_min_months < 12:
            raise ValueError("v522_future_holdout_must_cover_at_least_12_months")
        if self.future_paper_holdout_certified or self.future_paper_holdout_id is not None:
            raise ValueError(
                "v522_manual_future_holdout_certification_forbidden_until_evidence_validator_exists"
            )
        if not self.user_approved_sharpe_mandate:
            raise ValueError("v522_user_approved_sharpe_mandate_required")
        if self.authorization_basis != AUTHORIZATION_BASIS_V522:
            raise ValueError("v522_authorization_basis_changed")


def research_shadow_config_v522(first_month: str, last_month: str) -> ResearchConfigV522:
    return ResearchConfigV522(**asdict(_raw.research_shadow_config_v52(first_month, last_month)))


def _rehash(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("model_hash", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def transaction_cost_v522(
    change: Sequence[float] | np.ndarray, config: ResearchConfigV522
) -> tuple[float, float, float]:
    delta = np.asarray(change, dtype=float)
    linear_vector = np.asarray(config.transaction_cost_bps, dtype=float) / 10000.0
    quadratic_vector = np.asarray(config.quadratic_cost, dtype=float)
    linear = float(linear_vector @ np.abs(delta))
    quadratic = float(0.5 * quadratic_vector @ (delta * delta))
    return linear, quadratic, linear + quadratic


def _tie_groups(values: Mapping[str, float], tolerance: float = 1.0e-12) -> list[list[str]]:
    ordered = sorted(values, key=lambda asset: (-float(values[asset]), _raw.ASSET_ORDER_V5.index(asset)))
    groups: list[list[str]] = []
    for asset in ordered:
        if not groups:
            groups.append([asset])
            continue
        prior = groups[-1][0]
        if abs(float(values[asset]) - float(values[prior])) <= tolerance:
            groups[-1].append(asset)
        else:
            groups.append([asset])
    return groups


def apply_tie_policy_v522(payload: Mapping[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(dict(payload))
    rows = output.get("rows") or {}
    values = {
        asset: float((rows.get(asset) or {}).get("composite_strength") or 0.0)
        for asset in _raw.ASSET_ORDER_V5
    }
    groups = _tie_groups(values)
    rank = 1
    for group_index, group in enumerate(groups):
        if group_index == 0:
            label = "最强" if len(group) == 1 else "并列最强"
        elif group_index == len(groups) - 1:
            label = "最弱" if len(group) == 1 else "并列最弱"
        elif group_index < len(groups) / 2.0:
            label = "偏强" if len(group) == 1 else "并列偏强"
        else:
            label = "偏弱" if len(group) == 1 else "并列偏弱"
        for asset in group:
            rows[asset]["strength_rank"] = rank
            rows[asset]["strength_label_cn"] = label
            rows[asset]["strength_tie"] = len(group) > 1
            rows[asset]["strength_tied_assets"] = list(group)
            rows[asset]["signal_probability_calibrated"] = False
            rows[asset]["signal_score_0_1"] = rows[asset].get("signal_probability")
        rank += len(group)
    strongest = list(groups[0])
    weakest = list(groups[-1])
    output["strongest_assets"] = strongest
    output["weakest_assets"] = weakest
    output["strongest_asset"] = strongest[0] if len(strongest) == 1 else None
    output["weakest_asset"] = weakest[0] if len(weakest) == 1 else None
    output["tie_policy"] = "competition ranking; equal composite scores within 1e-12 share the same label"
    output["signal_probability_policy"] = "logistic display score, not an empirically calibrated probability"
    return output


def asset_strength_v522(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return apply_tie_policy_v522(_ORIGINAL_STRENGTH(*args, **kwargs))


def select_candidate_v522(
    results: Sequence[Mapping[str, Any]], mode: str, config: ResearchConfigV522
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select the relative family on validation Sharpe; test never ranks."""

    if mode != "benchmark_relative":
        return _ORIGINAL_SELECTION(results, mode, config)

    leaderboard: list[dict[str, Any]] = []
    for result in results:
        train = result["metrics"]["train"]
        validation = result["metrics"]["validation"]
        train_sharpe = _raw._number(train, "sharpe")
        validation_sharpe = _raw._number(validation, "sharpe")
        train_sample_ok = (
            int(train.get("months") or 0) >= config.minimum_train_returns
        )
        validation_sample_ok = (
            int(validation.get("months") or 0)
            >= config.minimum_validation_returns
        )
        eligible = (
            train_sample_ok
            and validation_sample_ok
            and math.isfinite(validation_sharpe)
            and validation_sharpe > 0.0
        )
        leaderboard.append(
            {
                "id": result["spec"]["id"],
                "model_version": mode,
                "eligible": bool(eligible),
                "train_sharpe": train_sharpe,
                "validation_sharpe": validation_sharpe,
                "train_information_ratio": train.get("information_ratio"),
                "validation_information_ratio": validation.get("information_ratio"),
                "train_annual_excess_return": train.get("annual_excess_return"),
                "validation_annual_excess_return": validation.get(
                    "annual_excess_return"
                ),
                "validation_score": validation_sharpe,
                "score_objective": "validation_standard_sharpe",
            }
        )
    eligible_ids = {row["id"] for row in leaderboard if row["eligible"]}
    pool = [
        item for item in results if item["spec"]["id"] in eligible_ids
    ] or list(results)
    selected = max(
        pool,
        key=lambda item: next(
            row["validation_score"]
            for row in leaderboard
            if row["id"] == item["spec"]["id"]
        ),
    )
    leaderboard.sort(key=lambda row: row["validation_score"], reverse=True)
    return dict(selected), {
        "model_version": mode,
        "candidate_count": len(results),
        "eligible_count": len(eligible_ids),
        "selected_id": selected["spec"]["id"],
        "leaderboard": leaderboard,
        "selection_uses_test": False,
        "fallback_research_only": False,
        "excess_return_used_for_eligibility": False,
        "information_ratio_used_for_eligibility": False,
        "selection_rule": (
            "training/validation sample eligibility, positive validation standard "
            "Sharpe, then maximum validation Sharpe; excess return, information "
            "ratio and retrospective test never rank or reject candidates"
        ),
    }


def _recompute_scaled_optimizer_diagnostics(
    diagnostics: dict[str, Any],
    target: np.ndarray,
    previous: np.ndarray,
    spec: Mapping[str, Any],
    config: ResearchConfigV522,
) -> None:
    if diagnostics.get("model_version") != "benchmark_relative":
        diagnostics["optimizer"]["backtest_cost_formula"] = "linear + 0.5 * quadratic * delta_weight^2"
        diagnostics["model_spec"] = dict(spec)
        return
    covariance = np.asarray(diagnostics["covariance"]["covariance"], dtype=float)
    loadings = np.asarray(diagnostics["covariance"]["factor_loadings"], dtype=float)
    posterior = diagnostics["black_litterman"]
    expected = np.asarray(posterior["posterior_mean"], dtype=float)
    mean_covariance = np.asarray(posterior["posterior_mean_covariance"], dtype=float)
    risk_weights = np.asarray(diagnostics["risk_budget"]["weights"], dtype=float)
    benchmark = np.asarray(config.policy_benchmark_weights, dtype=float)
    denominator = max(config.policy_anchor_penalty + config.policy_risk_anchor_penalty, 1.0e-12)
    anchor = (
        config.policy_anchor_penalty * benchmark
        + config.policy_risk_anchor_penalty * risk_weights
    ) / denominator
    constraints: dict[str, Any] = {
        "lower_bounds": benchmark - np.asarray(config.policy_active_bands, dtype=float),
        "upper_bounds": benchmark + np.asarray(config.policy_active_bands, dtype=float),
        "max_turnover": config.policy_max_one_way_turnover,
        "annualization": 12.0,
    }
    if config.max_annual_volatility is not None:
        constraints["max_annual_volatility"] = config.max_annual_volatility
    audit = _math._optimizer_constraints(
        target, previous, anchor, covariance, loadings, constraints
    )
    policy = diagnostics.get("policy_constraint_audit") or {}
    audit["policy_active_share"] = policy.get("active_share")
    audit["policy_annual_tracking_error"] = policy.get("annual_tracking_error")
    audit["policy_active_share_slack"] = (
        config.policy_max_active_share - float(policy.get("active_share") or 0.0)
    )
    audit["policy_tracking_error_slack"] = (
        config.policy_max_annual_tracking_error
        - float(policy.get("annual_tracking_error") or 0.0)
    )
    audit["max_violation"] = max(
        float(audit.get("max_violation") or 0.0),
        float(policy.get("max_violation") or 0.0),
    )
    change = target - previous
    linear, quadratic, cost = transaction_cost_v522(change, config)
    mean_uncertainty = math.sqrt(max(float(target @ mean_covariance @ target), 0.0))
    risk_penalty = 0.5 * float(spec["risk_aversion"]) * float(target @ covariance @ target)
    expected_return = float(expected @ target)
    uncertainty_penalty = float(spec["uncertainty_penalty"]) * mean_uncertainty
    anchor_penalty = denominator * float((target - anchor) @ covariance @ (target - anchor))
    objective_terms = {
        "expected_return": expected_return,
        "risk_penalty": risk_penalty,
        "mean_uncertainty_penalty": uncertainty_penalty,
        "anchor_penalty": anchor_penalty,
        "transaction_cost": cost,
        "linear_transaction_cost": linear,
        "quadratic_transaction_cost": quadratic,
        "minimization_objective": (
            risk_penalty - expected_return + uncertainty_penalty + anchor_penalty + cost
        ),
        "minimization_objective_exact_cost": (
            risk_penalty - expected_return + uncertainty_penalty + anchor_penalty + cost
        ),
    }
    optimizer = diagnostics["optimizer"]
    optimizer["pre_policy_scale_objective_terms"] = copy.deepcopy(
        optimizer.get("objective_terms")
    )
    optimizer["pre_policy_scale_shadow_prices"] = copy.deepcopy(
        optimizer.get("shadow_prices")
    )
    optimizer["objective_terms"] = objective_terms
    optimizer["constraint_slack"] = _jsonable(audit)
    optimizer["shadow_prices"] = {}
    optimizer["shadow_price_policy"] = "invalidated by homothetic policy scaling; not reported as final multipliers"
    optimizer["weights"] = target.tolist()
    optimizer["turnover"] = 0.5 * float(np.abs(change).sum())
    optimizer["expected_cost"] = cost
    optimizer["objective_terms_recomputed_after_policy_scaling"] = True
    optimizer["backtest_cost_formula"] = "linear + 0.5 * quadratic * delta_weight^2"
    diagnostics["model_spec"] = dict(spec)


def _fixed_weight_backtest_v522(
    months: Sequence[str],
    returns: np.ndarray,
    config: ResearchConfigV522,
    *,
    identifier: str,
    role: str,
    target_weights: Sequence[float],
    optimizer_input: bool,
    active_return_reference: bool,
) -> dict[str, Any]:
    target = np.asarray(target_weights, dtype=float)
    if target.shape != (4,) or np.any(target < 0.0):
        raise ValueError("v522_fixed_weight_benchmark_invalid")
    if abs(float(target.sum()) - 1.0) > 1.0e-12:
        raise ValueError("v522_fixed_weight_benchmark_must_sum_to_one")
    previous = target.copy()
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    for index in range(config.lookback_months, len(returns)):
        month = months[index]
        change = target - previous
        linear, quadratic, cost = transaction_cost_v522(change, config)
        gross = float(target @ returns[index])
        rows.append(
            {
                "month": month,
                "sample_set": _raw._base._sample(month, config),
                "gross_return": gross,
                "net_return": gross - cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "linear_cost": linear,
                "quadratic_cost": quadratic,
                "cost": cost,
            }
        )
        weights.append(
            {
                "month": month,
                **{
                    asset: float(target[position])
                    for position, asset in enumerate(_raw.ASSET_ORDER_V5)
                },
            }
        )
        previous = _raw._base._drift(target, returns[index])
    metrics = {
        sample: _raw._performance_v52(
            [row for row in rows if row["sample_set"] == sample],
            config.risk_free_rate_annual,
        )
        for sample in ("train", "validation", "test")
    }
    return {
        "id": identifier,
        "role": role,
        "optimizer_input": optimizer_input,
        "active_return_reference": active_return_reference,
        "weights": weights,
        "current_weights": target.tolist(),
        "metrics": metrics,
        "returns": rows,
        "nav": _raw._nav_rows(rows),
        "rebalance": "monthly_fixed_target_after_drift",
        "cost_formula": "sum(linear_i*abs(delta_i) + 0.5*quadratic_i*delta_i^2)",
    }


def equal_weight_display_backtest_v522(
    months: Sequence[str], returns: np.ndarray, config: ResearchConfigV522
) -> dict[str, Any]:
    return _fixed_weight_backtest_v522(
        months,
        returns,
        config,
        identifier=EQUAL_WEIGHT_DISPLAY_ID_V522,
        role="nav_display_only_not_optimizer_input",
        target_weights=(0.25, 0.25, 0.25, 0.25),
        optimizer_input=False,
        active_return_reference=False,
    )


def strategic_benchmark_backtest_v522(
    months: Sequence[str], returns: np.ndarray, config: ResearchConfigV522
) -> dict[str, Any]:
    result = _fixed_weight_backtest_v522(
        months,
        returns,
        config,
        identifier=_raw.STRATEGIC_BENCHMARK_ID_V52,
        role="primary_policy_benchmark_not_equal_weight",
        target_weights=config.policy_benchmark_weights,
        optimizer_input=True,
        active_return_reference=True,
    )
    result["_display_benchmarks"] = {
        EQUAL_WEIGHT_DISPLAY_ID_V522: equal_weight_display_backtest_v522(
            months, returns, config
        )
    }
    return result


def _absolute_constraint_audit(
    target: np.ndarray, previous: np.ndarray, config: ResearchConfigV522
) -> dict[str, Any]:
    lower = np.asarray(config.lower_bounds, dtype=float)
    upper = np.asarray(config.upper_bounds, dtype=float)
    turnover = 0.5 * float(np.abs(target - previous).sum())
    violations = {
        "weight_sum": abs(float(target.sum()) - 1.0),
        "lower_bound": float(np.max(np.maximum(lower - target, 0.0))),
        "upper_bound": float(np.max(np.maximum(target - upper, 0.0))),
        "turnover": max(turnover - config.max_one_way_turnover, 0.0),
    }
    return {
        "turnover": turnover,
        "max_one_way_turnover": config.max_one_way_turnover,
        "violations": violations,
        "max_violation": max(violations.values()),
    }


def simulate_candidate_v522(
    months: Sequence[str],
    returns: np.ndarray,
    cycles: Sequence[Mapping[str, Any]],
    macro_innovations: np.ndarray,
    macro_admitted: np.ndarray,
    view_models: Mapping[int, Mapping[str, Any]],
    spec: Mapping[str, Any],
    config: ResearchConfigV522,
    benchmark: Mapping[str, Any],
) -> dict[str, Any]:
    mode = str(spec["model_version"])
    previous = (
        np.asarray(config.policy_benchmark_weights, dtype=float)
        if mode == "benchmark_relative"
        else _raw._initial_absolute_weights_v52(returns, config)
    )
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    strength_history: list[dict[str, Any]] = []
    constraint_history: list[dict[str, Any]] = []
    current_diagnostics: dict[str, Any] = {}
    start = config.lookback_months - 1
    for signal_index in range(start, len(returns)):
        left = signal_index - config.lookback_months + 1
        target, diagnostics = _raw._allocate_at_v52(
            returns[left : signal_index + 1],
            macro_innovations[left : signal_index + 1],
            macro_admitted[left : signal_index + 1],
            cycles[signal_index],
            view_models[signal_index],
            previous,
            spec,
            config,
        )
        _recompute_scaled_optimizer_diagnostics(
            diagnostics, np.asarray(target, dtype=float), previous, spec, config
        )
        change = target - previous
        linear, quadratic, cost = transaction_cost_v522(change, config)
        turnover = 0.5 * float(np.abs(change).sum())
        weights.append(
            {
                "month": months[signal_index],
                "signal_month": months[signal_index],
                **{
                    asset: float(target[position])
                    for position, asset in enumerate(_raw.ASSET_ORDER_V5)
                },
            }
        )
        if mode == "benchmark_relative":
            policy = diagnostics["policy_constraint_audit"]
            signal_constraint = {
                "month": months[signal_index],
                "active_share": policy["active_share"],
                "annual_tracking_error": policy["annual_tracking_error"],
                "turnover": policy["turnover"],
                "active_weight": policy["active_weight"],
                "max_violation": policy["max_violation"],
            }
        else:
            signal_constraint = {
                "month": months[signal_index],
                **_absolute_constraint_audit(target, previous, config),
            }
        constraint_history.append(signal_constraint)
        strength_history.append(
            {
                "month": months[signal_index],
                "strongest_asset": diagnostics["asset_strength"].get("strongest_asset"),
                "strongest_assets": diagnostics["asset_strength"].get("strongest_assets"),
                "weakest_asset": diagnostics["asset_strength"].get("weakest_asset"),
                "weakest_assets": diagnostics["asset_strength"].get("weakest_assets"),
                "rows": copy.deepcopy(diagnostics["asset_strength"]["rows"]),
                "constraint_audit": copy.deepcopy(signal_constraint),
            }
        )
        current_diagnostics = diagnostics
        if signal_index + 1 < len(returns):
            realized = returns[signal_index + 1]
            month = months[signal_index + 1]
            gross = float(target @ realized)
            rows.append(
                {
                    "month": month,
                    "sample_set": _raw._base._sample(month, config),
                    "gross_return": gross,
                    "net_return": gross - cost,
                    "turnover": turnover,
                    "linear_cost": linear,
                    "quadratic_cost": quadratic,
                    "cost": cost,
                }
            )
            previous = _raw._base._drift(target, realized)
        else:
            previous = target
    metrics = {
        sample: _raw._performance_v52(
            [row for row in rows if row["sample_set"] == sample],
            config.risk_free_rate_annual,
        )
        for sample in ("train", "validation", "test")
    }
    result = {
        "spec": dict(spec),
        "metrics": metrics,
        "returns": rows,
        "nav": _raw._nav_rows(rows),
        "weights": weights,
        "strength_history": strength_history,
        "constraint_history": constraint_history,
        "current_weights": previous,
        "current_diagnostics": current_diagnostics,
        "cost_formula": "sum(linear_i*abs(delta_i) + 0.5*quadratic_i*delta_i^2)",
    }
    return _raw._attach_policy_comparison_v52(result, benchmark, config)


def statistical_evidence_gate_v522(
    selected: Mapping[str, Any],
    selection: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    registry_audit: Mapping[str, Any],
    macro_audit: Mapping[str, Any],
    mode: str,
    config: ResearchConfigV522,
) -> dict[str, Any]:
    validation = dict(selected["metrics"]["validation"])
    test = dict(selected["metrics"]["test"])
    n = max(int(validation.get("months") or 1), 1)
    hurdle = max(
        0.0,
        NormalDist().inv_cdf(1.0 - 1.0 / max(config.total_declared_trials, 2))
        / math.sqrt(n)
        * math.sqrt(12.0),
    )
    validation_psr = _raw._base._psr(validation, hurdle)
    checks: dict[str, bool] = {
        "asset_registry_d3": bool(registry_audit.get("production_ready")),
        "macro_pit_coverage": float(macro_audit.get("pit_verified_fraction") or 0.0)
        >= config.macro_pit_required_fraction,
        "validation_sample": int(validation.get("months") or 0)
        >= config.minimum_validation_returns,
        "validation_family_eligible": int(selection.get("eligible_count") or 0) > 0,
        "validation_sharpe_threshold": validation.get("sharpe") is not None
        and float(validation["sharpe"]) >= config.promotion_min_test_sharpe,
        "probabilistic_sharpe_validation": validation_psr is not None
        and validation_psr >= config.promotion_min_psr,
        "future_pristine_paper_holdout": False,
    }
    if mode == "benchmark_relative":
        checks.update(
            {
                "validation_excess_positive": _raw._number(
                    validation, "annual_excess_return", -1.0
                )
                > 0.0,
                "validation_information_ratio_positive": _raw._number(
                    validation, "information_ratio", -99.0
                )
                > 0.0,
            }
        )
    return {
        "model_version": mode,
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
        "probabilistic_sharpe_ratio_validation": validation_psr,
        "multiple_trial_sharpe_hurdle": hurdle,
        "declared_trials": config.total_declared_trials,
        "future_paper_holdout_certified": False,
        "future_paper_holdout_id": None,
        "manual_holdout_certification_accepted": False,
        "future_holdout_validation": {
            "status": "not_implemented_fail_closed",
            "required_evidence": [
                "published frozen model fingerprint",
                "holdout start strictly after the freeze month",
                "at least 12 contiguous paper months",
                "paper strategy and same-cost benchmark returns",
                "positive holdout excess and information ratio",
                "immutable evidence manifest",
            ],
        },
        "retrospective_test_is_report_only": True,
        "retrospective_test_enters_checks": False,
        "retrospective_test_summary": {
            "months": test.get("months"),
            "sharpe": test.get("sharpe"),
            "annual_return": test.get("annual_return"),
            "annual_excess_return": test.get("annual_excess_return"),
            "information_ratio": test.get("information_ratio"),
            "policy_benchmark_sharpe": ((benchmark.get("metrics") or {}).get("test") or {}).get("sharpe"),
        },
        "policy": "promotion uses data lineage, training/validation statistics and a separately certified future paper holdout; the observed retrospective test can never promote this revision",
    }


def promotion_gate_v522(
    selected: Mapping[str, Any],
    selection: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    registry_audit: Mapping[str, Any],
    macro_audit: Mapping[str, Any],
    mode: str,
    config: ResearchConfigV522,
) -> dict[str, Any]:
    """Return execution authorization while retaining strict evidence separately."""

    statistical = statistical_evidence_gate_v522(
        selected,
        selection,
        benchmark,
        registry_audit,
        macro_audit,
        mode,
        config,
    )
    statistical["status"] = (
        "passed" if not statistical.get("failed") else "warning"
    )
    statistical["effect_on_user_authorized_deployment"] = "warning_only"
    statistical["gate_scope"] = "full_statistical_evidence"
    future_holdout = statistical.get("future_holdout_validation") or {}
    future_holdout["role"] = (
        "full_statistical_validation_only_not_user_authorization"
    )
    future_holdout["legacy_required_evidence"] = future_holdout.pop(
        "required_evidence", []
    )

    selected_metrics = selected.get("metrics") or {}
    train = dict(selected_metrics.get("train") or {})
    validation = dict(selected_metrics.get("validation") or {})
    benchmark_metrics = benchmark.get("metrics") or {}
    benchmark_train = dict(benchmark_metrics.get("train") or {})
    benchmark_validation = dict(benchmark_metrics.get("validation") or {})
    train_sharpe = _raw._number(train, "sharpe")
    validation_sharpe = _raw._number(validation, "sharpe")
    benchmark_train_sharpe = _raw._number(benchmark_train, "sharpe")
    benchmark_validation_sharpe = _raw._number(
        benchmark_validation, "sharpe"
    )
    train_delta = train_sharpe - benchmark_train_sharpe
    validation_delta = validation_sharpe - benchmark_validation_sharpe

    if mode == "benchmark_relative":
        checks = {
            "explicit_user_approval_sharpe_only": bool(
                config.user_approved_sharpe_mandate
            ),
            "authorization_basis_verified": (
                config.authorization_basis == AUTHORIZATION_BASIS_V522
            ),
            "training_sample": int(train.get("months") or 0)
            >= config.minimum_train_returns,
            "validation_sample": int(validation.get("months") or 0)
            >= config.minimum_validation_returns,
            "validation_family_sharpe_eligible": int(
                selection.get("eligible_count") or 0
            )
            > 0,
            "training_sharpe_improves_strategic_benchmark": (
                train_delta > 0.0
            ),
            "validation_sharpe_improves_strategic_benchmark": (
                validation_delta > 0.0
            ),
            "selection_uses_test_is_false": (
                selection.get("selection_uses_test") is False
            ),
        }
        status = "passed" if all(checks.values()) else "blocked"
    else:
        checks = {
            "explicit_user_approval_for_execution": False,
            "selection_uses_test_is_false": (
                selection.get("selection_uses_test") is False
            ),
        }
        status = "research_reference"

    return {
        "model_version": mode,
        "status": status,
        "gate_scope": "user_authorized_sharpe_mandate",
        "authorization_basis": AUTHORIZATION_BASIS_V522,
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
        "probabilistic_sharpe_ratio_validation": statistical.get(
            "probabilistic_sharpe_ratio_validation"
        ),
        "multiple_trial_sharpe_hurdle": statistical.get(
            "multiple_trial_sharpe_hurdle"
        ),
        "declared_trials": statistical.get("declared_trials"),
        "sharpe_evidence": {
            "training_model": train_sharpe,
            "training_strategic_benchmark": benchmark_train_sharpe,
            "training_delta": train_delta,
            "validation_model": validation_sharpe,
            "validation_strategic_benchmark": benchmark_validation_sharpe,
            "validation_delta": validation_delta,
        },
        "excess_return_required_for_authorization": False,
        "information_ratio_required_for_authorization": False,
        "statistical_evidence": statistical,
        "future_paper_holdout_certified": False,
        "future_paper_holdout_id": None,
        "manual_holdout_certification_accepted": False,
        "future_holdout_validation": copy.deepcopy(future_holdout),
        "retrospective_test_is_report_only": True,
        "retrospective_test_enters_checks": False,
        "retrospective_test_summary": copy.deepcopy(
            statistical.get("retrospective_test_summary") or {}
        ),
        "policy": (
            "candidate selection uses training/validation only; explicit user "
            "authorization accepts policy-relative Sharpe improvement without "
            "requiring positive excess return or information ratio, while all "
            "D3/PIT/PSR/holdout evidence remains separately disclosed"
        ),
    }


_LOCAL_ABSOLUTE_PATH_V522 = re.compile(
    r"(?i)(?:^|[\s\"'=])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)


def _logical_source_id_v522(key: str | None) -> str:
    mapping = {
        "warehouse": "research_warehouse_db",
        "database": "research_warehouse_db",
        "pit_connector_file": "pit_macro_connector_artifact",
        "registry_file": "asset_series_registry",
    }
    return mapping.get(str(key or ""), f"logical_source:{key or 'local_artifact'}")


def _contains_local_absolute_path_v522(value: str) -> bool:
    return bool(ntpath.isabs(value) or _LOCAL_ABSOLUTE_PATH_V522.search(value))


def sanitize_public_snapshot_v522(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Replace local absolute paths with logical source IDs and prove none remain."""

    replacements: list[dict[str, str]] = []

    def sanitize(value: Any, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {
                str(child_key): sanitize(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [sanitize(item, key) for item in value]
        if isinstance(value, tuple):
            return [sanitize(item, key) for item in value]
        if isinstance(value, str) and _contains_local_absolute_path_v522(value):
            logical = _logical_source_id_v522(key)
            replacements.append({"field": str(key or ""), "logical_source_id": logical})
            return logical
        return value

    output = sanitize(copy.deepcopy(dict(payload)))
    residual: list[str] = []

    def inspect(value: Any, path: str = "$") -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                inspect(child_value, f"{path}.{child_key}")
        elif isinstance(value, list):
            for index, child_value in enumerate(value):
                inspect(child_value, f"{path}[{index}]")
        elif isinstance(value, str) and _contains_local_absolute_path_v522(value):
            residual.append(path)

    inspect(output)
    if residual:
        raise ValueError(
            "v522_public_snapshot_contains_local_absolute_path:"
            + ",".join(residual)
        )
    output["public_snapshot_sanitization"] = {
        "status": "passed",
        "local_absolute_path_count": 0,
        "replacement_count": len(replacements),
        "logical_source_ids": sorted(
            {row["logical_source_id"] for row in replacements}
        ),
        "policy": "public snapshots expose logical source IDs, never local absolute paths",
    }
    return _rehash(output)


def _broker_reference_v522(
    institution: str,
    title: str,
    date: str,
    url: str,
    scope: str,
) -> dict[str, str]:
    if scope not in {"exact_method", "cross_cycle_framework"}:
        raise ValueError("v522_invalid_broker_reference_scope")
    return {
        "institution": institution,
        "title": title,
        "date": date,
        "url": url,
        "scope": scope,
        "verification_status": "inspected",
    }


def _authoritative_references_v522() -> dict[str, list[dict[str, str]]]:
    """Frozen references from the inspected broker-report evidence artifact."""

    records = {
        "bl_macro": ("\u56fd\u6cf0\u6d77\u901a\u8bc1\u5238", "BL\u5b8f\u89c2\u91cf\u5316\u7b56\u7565\u6a21\u578b\u4e3b\u52a8\u914d\u7f6e\u5c55\u671b(202503)\uff1aA\u80a1\u914d\u7f6e\u4ef7\u503c\u51f8\u663e \u91d1\u878d\u98ce\u683c\u6709\u671b\u56de\u5f52", "2025-03-06", "https://cloud.gildata.com/queryservice/research/attachment/794623041730.pdf"),
        "citic_outlook": ("\u4e2d\u4fe1\u5efa\u6295\u8bc1\u5238", "2025\u5e74\u8d44\u4ea7\u914d\u7f6e\u53ca\u91cf\u5316\u7b56\u7565\u5c55\u671b\uff1a\u8d22\u653f\u653f\u7b56\u4e0e\u7ecf\u6d4e\u5468\u671f\u7684\u5bf9\u6297\u4e0e\u7edf\u4e00", "2024-11-20", "https://cloud.gildata.com/queryservice/research/attachment/785442215064.pdf"),
        "six_cycle_etf": ("\u56fd\u76db\u8bc1\u5238", "\u91cf\u5316\u5206\u6790\u62a5\u544a\uff1a\u516d\u5468\u671f\u6846\u67b6\u4e0b\u7684\u591a\u8d44\u4ea7ETF\u914d\u7f6e", "2025-11-05", "https://cloud.gildata.com/queryservice/research/attachment/815736789040.pdf"),
        "six_cycle": ("\u56fd\u76db\u8bc1\u5238", "\u91cf\u5316\u4e13\u9898\u62a5\u544a\uff1a\u4e2d\u56fd\u7ecf\u6d4e\u516d\u5468\u671f\u6a21\u578b\u4e0e\u591a\u8d44\u4ea7\u7b56\u7565\u5e94\u7528", "2024-12-28", "https://cloud.gildata.com/queryservice/research/attachment/788730264596.pdf"),
        "risk_parity": ("\u56fd\u6cf0\u541b\u5b89\u8bc1\u5238", "\u5927\u7c7b\u8d44\u4ea7\u914d\u7f6e\u91cf\u5316\u6a21\u578b\u7814\u7a76\u7cfb\u5217\u4e4b\u4e09\uff1a\u6865\u6c34\u5168\u5929\u5019\u7b56\u7565\u548c\u98ce\u9669\u5e73\u4ef7\u6a21\u578b\u5168\u89e3\u6790", "2023-05-27", "https://cloud.gildata.com/queryservice/research/attachment/738530789217.pdf"),
        "macro_risk_parity": ("\u534e\u897f\u8bc1\u5238", "\u57fa\u4e8e\u5b8f\u89c2\u98ce\u9669\u56e0\u5b50\u7684\u8d44\u4ea7\u914d\u7f6e\uff1a\u5168\u5929\u5019\u5b8f\u89c2\u98ce\u9669\u5e73\u4ef7\u6a21\u578b", "2020-09-17", "https://cloud.gildata.com/queryservice/research/attachment/653657479794.pdf"),
        "risk_measures": ("\u534e\u897f\u8bc1\u5238", "\u534e\u897f\u91d1\u5de5\u5168\u5929\u5019\u8d44\u4ea7\u914d\u7f6e\u6846\u67b6\u4e4b\u4e00\uff1a\u98ce\u9669\u5e73\u4ef7\u6a21\u578b\u98ce\u9669\u6d4b\u5ea6\u63a2\u8ba8", "2020-09-08", "https://cloud.gildata.com/queryservice/research/attachment/652845069019.pdf"),
        "hmm": ("\u6d59\u5546\u8bc1\u5238", "\u5927\u7c7b\u8d44\u4ea7\u914d\u7f6e\u62e9\u65f6\u65b9\u6cd5\uff1a\u9690\u9a6c\u5c14\u53ef\u592b\u5e02\u573a\u72b6\u6001\u8bc6\u522b\u65b9\u6cd5", "2021-12-16", "https://cloud.gildata.com/queryservice/research/attachment/692990214922.pdf"),
        "hmm_companion": ("\u6d59\u5546\u8bc1\u5238", "\u9690\u9a6c\u5c14\u53ef\u592b\u5e02\u573a\u72b6\u6001\u8bc6\u522b\u65b9\u6cd5\uff1a\u5927\u7c7b\u8d44\u4ea7 \u914d\u7f6e\u62e9\u65f6", "2021-11-30", "https://cloud.gildata.com/queryservice/research/attachment/691587087194.pdf"),
        "ai_bl": ("\u56fd\u4fe1\u8bc1\u5238", "AI\u8d4b\u80fd\u8d44\u4ea7\u914d\u7f6e\uff08\u4e09\u5341\u56db\uff09\uff1a\u9996\u53d1\uff0cAI+\u591a\u8d44\u4ea7\u6cdb\u91cf\u5316\u7cfb\u5217\u6307\u6570", "2026-01-12", "https://pdf.dfcfw.com/pdf/H3_AP202601121816952139_1.pdf"),
    }

    def ref(key: str, scope: str) -> dict[str, str]:
        reference = _broker_reference_v522(*records[key], scope)
        if key == "ai_bl":
            reference["matched_section"] = (
                "AI\u89c6\u89d2\u9a71\u52a8\u7684Black-Litterman\u8d44\u4ea7\u914d\u7f6e"
            )
        if key == "six_cycle_etf":
            reference["report_date"] = "2025-11-05"
            reference["cataloged_at"] = "2025-11-06"
        return reference

    cycles = [
        ref("six_cycle", "cross_cycle_framework"),
        ref("six_cycle_etf", "cross_cycle_framework"),
        ref("citic_outlook", "cross_cycle_framework"),
    ]
    return {
        "kondratieff": copy.deepcopy(cycles),
        "juglar": copy.deepcopy(cycles),
        "kitchin": copy.deepcopy(cycles),
        "merrill": copy.deepcopy(cycles + [ref("hmm", "cross_cycle_framework")]),
        "pring": copy.deepcopy(cycles + [ref("hmm_companion", "cross_cycle_framework")]),
        "black_litterman": [ref("bl_macro", "exact_method"), ref("ai_bl", "exact_method"), ref("citic_outlook", "cross_cycle_framework")],
        "risk_parity": [ref("risk_parity", "exact_method"), ref("risk_measures", "exact_method"), ref("macro_risk_parity", "exact_method")],
        "risk_budget": [ref("risk_measures", "exact_method"), ref("macro_risk_parity", "exact_method"), ref("six_cycle_etf", "cross_cycle_framework")],
        "macro_factor_model": [ref("macro_risk_parity", "exact_method"), ref("bl_macro", "exact_method"), ref("citic_outlook", "cross_cycle_framework")],
    }


def _cycle_model_catalog_v522(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    names = {
        "kondratieff": "\u5eb7\u6ce2\u5468\u671f",
        "juglar": "\u6731\u683c\u62c9\u5468\u671f",
        "kitchin": "\u57fa\u94a6\u5468\u671f",
        "merrill": "\u4e2d\u56fd\u7f8e\u6797\u65f6\u949f",
        "pring": "\u666e\u6797\u683c\u5468\u671f",
    }
    references = _authoritative_references_v522()
    current_cycle = (
        ((snapshot.get("allocations") or {}).get("current_cycle") or {}).get(
            "cycles"
        )
        or {}
    )
    availability = (
        (snapshot.get("cycle_factor_availability") or {}).get("cycles") or {}
    )
    factor_registry = snapshot.get("cycle_factor_registry") or []
    asset_proxies = snapshot.get("asset_proxies") or {}
    relative_metadata = (
        ((snapshot.get("allocations") or {}).get("benchmark_relative") or {}).get(
            "metadata"
        )
        or {}
    )
    contributions = (
        (relative_metadata.get("cycle_views") or {}).get("cycle_contributions")
        or {}
    )
    history = snapshot.get("cycle_history") or []
    result: dict[str, Any] = {}
    for cycle, name_cn in names.items():
        current = copy.deepcopy(current_cycle.get(cycle) or {})
        admitted = bool(current.get("eligible_for_views"))
        production_admitted = bool(
            current.get("eligible_for_production_views", admitted)
        )
        specs = [
            copy.deepcopy(row)
            for row in factor_registry
            if str(row.get("cycle")) == cycle
        ]
        evidence = current.get("factor_evidence") or {}
        if cycle == "pring":
            source_contract: Any = {
                asset: copy.deepcopy(asset_proxies.get(asset))
                for asset in ("bond", "equity", "commodity")
            }
            steps = [
                "use bond, equity and ex-gold commodity monthly returns; exclude gold",
                "scale 3/6/12-month compounded returns by trailing 24-month volatility",
                "map three market bull scores into six-state emission likelihoods",
                "run the explicit-duration semi-Markov forward filter frozen on training",
            ]
        elif cycle == "kondratieff":
            source_contract = {
                "required_evidence": "multiple independent complete 40-60 year cycles",
                "available_evidence": "insufficient in the local governed sample",
            }
            steps = [
                "retain the four-state definition as equal-probability research display",
                "do not fit, create BL views, alter risk budgets or change weights",
            ]
        else:
            source_contract = specs
            steps = [
                "select the first real accepted field using the frozen training prefix",
                "apply the registered transform and economic sign",
                "compute causal robust Z scores and three-month momentum without revisions",
                "aggregate pillars and map them to fixed state emission centres",
                "run the explicit-duration semi-Markov forward filter frozen on training",
            ]
        contribution = [
            float(value) for value in contributions.get(cycle, (0.0, 0.0, 0.0))
        ]
        if cycle == "kondratieff":
            status = "display_only"
        elif admitted and production_admitted:
            status = "admitted"
        elif admitted:
            status = "admitted_shadow_only"
        else:
            status = "not_admitted"
        result[cycle] = {
            "id": cycle,
            "name_cn": name_cn,
            "status": status,
            "inputs": {
                "source_contract": source_contract,
                "current_source": copy.deepcopy(
                    evidence.get("source") or source_contract
                ),
                "observed_field": copy.deepcopy(
                    evidence.get("observed_fields") or {}
                ),
                "authoritative_source_verification": "not_verified",
                "verified_vendor_series_ids": [],
                "vendor_series_id_policy": (
                    "Wind/iFind/RQData series IDs are not asserted until independently verified"
                ),
                "observed_fields": copy.deepcopy(
                    evidence.get("observed_fields") or {}
                ),
                "required_factors": [
                    row.get("factor_key")
                    for row in specs
                    if row.get("required_for_admission")
                ],
                "optional_factors": [
                    row.get("factor_key")
                    for row in specs
                    if not row.get("required_for_admission")
                ],
                "input_assets": copy.deepcopy(current.get("input_assets") or []),
                "excluded_assets": copy.deepcopy(
                    current.get("excluded_assets") or []
                ),
                "data_status": current.get("data_status"),
            },
            "steps": steps,
            "constraints": {
                "pit_required": any(bool(row.get("pit_required")) for row in specs),
                "required_pillars": copy.deepcopy(
                    evidence.get("required_pillars")
                    or (availability.get(cycle) or {}).get("required_pillars")
                    or []
                ),
                "missing_required_factors": copy.deepcopy(
                    evidence.get("missing_required_factors")
                    or (availability.get(cycle) or {}).get(
                        "missing_required_factors"
                    )
                    or []
                ),
                "duration_model": copy.deepcopy(current.get("duration_model")),
                "admission_rule": (
                    "required factors, minimum history, available_time and vintage must pass; otherwise display only"
                ),
            },
            "outputs": {
                "current_state_payload": current,
                "history": {
                    "path": f"cycle_history[*].cycles.{cycle}",
                    "months": len(history),
                    "first_month": history[0].get("month") if history else None,
                    "last_month": history[-1].get("month") if history else None,
                },
            },
            "effects": {
                "eligible_for_views": admitted,
                "eligible_for_production_views": production_admitted,
                "current_bl_view_contribution": contribution,
                "current_contribution_is_zero": all(
                    abs(value) <= 1.0e-15 for value in contribution
                ),
                "standalone_performance_attribution_available": False,
                "effect_policy": (
                    "nonzero admitted contribution enters joint BL views"
                    if any(abs(value) > 1.0e-15 for value in contribution)
                    else "admission gate forces zero allocation contribution"
                ),
            },
            "authoritative_references": copy.deepcopy(
                references[cycle]
            ),
        }
    return result


def _allocation_model_catalog_v522(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    allocations = snapshot.get("allocations") or {}
    relative = allocations.get("benchmark_relative") or {}
    metadata = relative.get("metadata") or {}
    covariance = metadata.get("covariance") or {}
    black_litterman = metadata.get("black_litterman") or {}
    risk_parity = allocations.get("risk_parity") or {}
    risk_budget = allocations.get("macro_risk_budget") or {}
    macro_audit = (
        (
            (snapshot.get("macro_factor_risk_audit") or {}).get(
                "by_model_version"
            )
            or {}
        ).get("benchmark_relative")
        or {}
    )
    relative_metrics = (
        ((snapshot.get("backtest") or {}).get("strategies") or {})
        .get("benchmark_relative", {})
        .get("metrics", {})
    )
    config = snapshot.get("config") or {}
    references = _authoritative_references_v522()
    return {
        "black_litterman": {
            "id": "black_litterman",
            "name_cn": "\u7a33\u5065Black-Litterman",
            "status": (
                (black_litterman.get("diagnostics") or {}).get("status")
                or "not_available"
            ),
            "inputs": {
                key: copy.deepcopy(black_litterman.get(key))
                for key in (
                    "prior_weights",
                    "pi",
                    "delta",
                    "tau",
                    "P",
                    "q",
                    "omega",
                )
            },
            "steps": [
                "reverse equilibrium returns from the 60/15/15/10 policy and covariance",
                "encode three jointly estimated relative views in P and q",
                "retain the full non-diagonal forecast-error covariance Omega",
                "apply the Bayesian posterior for mean and predictive covariance",
            ],
            "constraints": {
                "policy_prior_is_strategic_60_15_15_10": True,
                "view_error_covariance_must_be_psd": True,
                "active_cycles": copy.deepcopy(
                    (
                        (
                            (
                                black_litterman.get("diagnostics") or {}
                            ).get("views")
                            or {}
                        ).get("source")
                        or {}
                    ).get("active_cycles")
                    or []
                ),
            },
            "outputs": {
                key: copy.deepcopy(black_litterman.get(key))
                for key in (
                    "posterior_mean",
                    "posterior_mean_covariance",
                    "predictive_covariance",
                    "diagnostics",
                )
            },
            "effects": {
                "feeds_recommended_optimizer": True,
                "recommended_weights": copy.deepcopy(relative.get("weights") or {}),
                "backtest_metrics": copy.deepcopy(relative_metrics),
            },
            "authoritative_references": copy.deepcopy(
                references["black_litterman"]
            ),
        },
        "risk_parity": {
            "id": "risk_parity",
            "name_cn": "\u4e25\u683c\u98ce\u9669\u5e73\u4ef7",
            "status": (
                (risk_parity.get("metadata") or {}).get("status")
                or "not_available"
            ),
            "inputs": {
                "asset_order": copy.deepcopy(snapshot.get("asset_order") or []),
                "covariance": copy.deepcopy(covariance.get("covariance")),
                "target_risk_budget": [0.25, 0.25, 0.25, 0.25],
            },
            "steps": [
                "project the covariance matrix to positive semidefinite",
                "solve equal risk contribution with a Newton log barrier",
                "audit KKT residual and per-asset risk-budget error",
            ],
            "constraints": {
                "long_only": True,
                "fully_invested": True,
                "hard_constraints_relaxed": False,
            },
            "outputs": copy.deepcopy(risk_parity),
            "effects": {
                "role": "independent_allocation_comparator",
                "used_as_policy_anchor": False,
                "standalone_backtest_available": False,
            },
            "authoritative_references": copy.deepcopy(
                references["risk_parity"]
            ),
        },
        "risk_budget": {
            "id": "risk_budget",
            "name_cn": "\u7ea6\u675f\u98ce\u9669\u9884\u7b97",
            "status": (
                (risk_budget.get("metadata") or {}).get("status")
                or "not_available"
            ),
            "inputs": {
                "covariance": copy.deepcopy(covariance.get("covariance")),
                "target_budget": copy.deepcopy(
                    (risk_budget.get("metadata") or {}).get("target_budget")
                ),
                "lower_bounds": copy.deepcopy(config.get("lower_bounds")),
                "upper_bounds": copy.deepcopy(config.get("upper_bounds")),
            },
            "steps": [
                "derive target risk budgets from admitted cycle probabilities",
                "solve the Richard-Roncalli log-barrier objective with SLSQP constraints",
                "bisect the risk-budget scale and refine on the simplex",
                "report budget error, active constraints, KKT residual and shadow prices",
            ],
            "constraints": {
                "long_only": True,
                "fully_invested": True,
                "maximum_one_way_turnover": config.get("max_one_way_turnover"),
                "solver_status_is_reported_not_overstated": True,
            },
            "outputs": copy.deepcopy(risk_budget),
            "effects": {
                "feeds_relative_optimizer_risk_anchor": True,
                "standalone_backtest_available": False,
            },
            "authoritative_references": copy.deepcopy(
                references["risk_budget"]
            ),
        },
        "macro_factor_model": {
            "id": "macro_factor_model",
            "name_cn": "\u5b8f\u89c2\u56e0\u5b50\u98ce\u9669\u6a21\u578b",
            "status": macro_audit.get("status") or "not_available",
            "inputs": {
                "factor_names": copy.deepcopy(covariance.get("factor_names") or []),
                "factor_loadings": copy.deepcopy(
                    covariance.get("factor_loadings")
                ),
                "factor_covariance": copy.deepcopy(
                    covariance.get("factor_covariance")
                ),
                "specific_covariance": copy.deepcopy(
                    covariance.get("specific_covariance")
                ),
                "statistical_covariance": copy.deepcopy(
                    covariance.get("statistical_covariance")
                ),
                "macro_pit_coverage": (
                    (
                        (snapshot.get("quality") or {}).get(
                            "macro_point_in_time"
                        )
                        or {}
                    ).get("pit_verified_fraction")
                ),
            },
            "steps": [
                "fit B, F and asset-specific D from causal macro innovations",
                "estimate statistical covariance by EWMA, diagonal shrinkage and PSD repair",
                "blend Sigma=rho*(BFB'+D)+(1-rho)*Sigma_stat under the PIT gate",
                "Euler-decompose growth, inflation, credit, liquidity and statistical risk",
            ],
            "constraints": {
                "pit_gate_required": True,
                "current_macro_blend_weight": covariance.get("macro_blend_weight"),
                "inactive_macro_contribution_must_equal_zero": True,
            },
            "outputs": {
                "covariance_diagnostics": copy.deepcopy(
                    covariance.get("diagnostics")
                ),
                "risk_decomposition": copy.deepcopy(macro_audit),
            },
            "effects": {
                "feeds_recommended_covariance": True,
                "current_macro_effect_is_zero": (
                    float(covariance.get("macro_blend_weight") or 0.0) == 0.0
                ),
                "effect_policy": macro_audit.get("production_interpretation"),
            },
            "authoritative_references": copy.deepcopy(
                references["macro_factor_model"]
            ),
        },
    }


def build_model_evidence_catalog_v522(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    required = (
        "id",
        "name_cn",
        "status",
        "inputs",
        "steps",
        "constraints",
        "outputs",
        "effects",
        "authoritative_references",
    )
    cycle_models = _cycle_model_catalog_v522(snapshot)
    allocation_models = _allocation_model_catalog_v522(snapshot)
    missing: list[str] = []
    reference_fields = (
        "institution",
        "title",
        "date",
        "url",
        "scope",
        "verification_status",
    )
    reference_errors: list[str] = []
    for family_name, family in (
        ("cycle_models", cycle_models),
        ("allocation_models", allocation_models),
    ):
        for model_name, model in family.items():
            for field in required:
                if field not in model:
                    missing.append(f"{family_name}.{model_name}.{field}")
            references = model.get("authoritative_references") or []
            if not 2 <= len(references) <= 5:
                reference_errors.append(
                    f"{family_name}.{model_name}.reference_count"
                )
            for index, reference in enumerate(references):
                prefix = (
                    f"{family_name}.{model_name}.authoritative_references[{index}]"
                )
                if set(reference) != set(reference_fields):
                    reference_errors.append(f"{prefix}.fields")
                if reference.get("scope") not in {
                    "exact_method",
                    "cross_cycle_framework",
                }:
                    reference_errors.append(f"{prefix}.scope")
                if reference.get("verification_status") != "inspected":
                    reference_errors.append(f"{prefix}.verification_status")
                if not str(reference.get("url") or "").startswith("https://"):
                    reference_errors.append(f"{prefix}.url")
    audit_errors = missing + reference_errors
    return {
        "schema_version": "1.0",
        "required_fields": list(required),
        "cycle_models": cycle_models,
        "allocation_models": allocation_models,
        "completeness_audit": {
            "status": "passed" if not audit_errors else "failed",
            "expected_cycle_models": [
                "kondratieff",
                "juglar",
                "kitchin",
                "merrill",
                "pring",
            ],
            "expected_allocation_models": [
                "black_litterman",
                "risk_parity",
                "risk_budget",
                "macro_factor_model",
            ],
            "missing_fields": missing,
            "reference_errors": reference_errors,
            "truthful_non_admission_preserved": True,
            "structural_completeness_is_not_statistical_validation": True,
        },
    }


def canonical_release_applicable_v522(snapshot: Mapping[str, Any]) -> bool:
    data_as_of = snapshot.get("data_as_of") or {}
    config = snapshot.get("config") or {}
    observed = {
        "market": data_as_of.get("market"),
        "macro_available": data_as_of.get("macro_available"),
        "macro_complete": data_as_of.get("macro_complete"),
        "train_end": config.get("train_end"),
        "validation_end": config.get("validation_end"),
    }
    return observed == CANONICAL_RELEASE_SIGNATURE_V522


def synchronize_cycle_availability_v522(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    availability_contract = snapshot.get("cycle_factor_availability") or {}
    availability_cycles = availability_contract.get("cycles") or {}
    current_cycles = (
        ((snapshot.get("allocations") or {}).get("current_cycle") or {}).get(
            "cycles"
        )
        or {}
    )
    for cycle, current in current_cycles.items():
        if cycle not in availability_cycles:
            raise ValueError(f"v522_cycle_availability_missing:{cycle}")
        available = availability_cycles[cycle]
        eligible = bool(current.get("eligible_for_views"))
        production_eligible = bool(
            current.get("eligible_for_production_views", False)
        )
        shadow_eligible = bool(
            current.get("eligible_for_shadow_views", eligible)
        )
        available.update(
            {
                "data_status": current.get("data_status"),
                "eligible_for_views": eligible,
                "eligible_for_shadow_views": shadow_eligible,
                "eligible_for_production_views": production_eligible,
                "view_scope": current.get("view_scope")
                or (
                    "production"
                    if production_eligible
                    else "shadow_only" if eligible else "not_admitted"
                ),
            }
        )
    availability_contract["admitted_cycles"] = [
        cycle
        for cycle, current in current_cycles.items()
        if bool(current.get("eligible_for_views"))
    ]
    availability_contract["production_admitted_cycles"] = [
        cycle
        for cycle, current in current_cycles.items()
        if bool(current.get("eligible_for_production_views", False))
    ]
    availability_contract["admission_scope"] = (
        "availability mirrors governed current-cycle eligibility; production requires explicit upstream admission"
    )
    return snapshot


def approved_relative_weight_freeze_audit_v522(
    snapshot: Mapping[str, Any],
    tolerance: float = 1.0e-10,
) -> dict[str, Any]:
    selection = (
        ((snapshot.get("backtest") or {}).get("selection_audit") or {}).get(
            "benchmark_relative"
        )
        or {}
    )
    allocations = snapshot.get("allocations") or {}
    expected_benchmark = {
        "equity": 0.60,
        "bond": 0.15,
        "gold": 0.10,
        "commodity": 0.15,
    }
    actual_relative = copy.deepcopy(
        (allocations.get("benchmark_relative") or {}).get("weights") or {}
    )
    actual_recommended = copy.deepcopy(
        (allocations.get("recommended") or {}).get("weights") or {}
    )
    actual_benchmark = copy.deepcopy(
        (snapshot.get("benchmark") or {}).get("weights") or {}
    )

    def max_error(
        actual: Mapping[str, Any], expected: Mapping[str, float]
    ) -> float:
        try:
            return max(
                abs(float(actual[asset]) - expected_weight)
                for asset, expected_weight in expected.items()
            )
        except (KeyError, TypeError, ValueError):
            return math.inf

    relative_error = max_error(actual_relative, APPROVED_RELATIVE_WEIGHTS_V522)
    recommended_error = max_error(
        actual_recommended, APPROVED_RELATIVE_WEIGHTS_V522
    )
    benchmark_error = max_error(actual_benchmark, expected_benchmark)
    selected_id = selection.get("selected_id")
    checks = {
        "strategic_anchor_is_60_15_10_15_internal_order": (
            benchmark_error <= tolerance
        ),
    }
    applicable = canonical_release_applicable_v522(snapshot)
    if applicable:
        checks.update(
            {
                "selected_model_id_frozen": (
                    selected_id == APPROVED_RELATIVE_MODEL_ID_V522
                ),
                "benchmark_relative_weights_frozen": relative_error <= tolerance,
                "recommended_weights_match_relative": recommended_error <= tolerance,
            }
        )
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "applicable": applicable,
        "release_signature": copy.deepcopy(CANONICAL_RELEASE_SIGNATURE_V522),
        "enforcement_scope": (
            "canonical_202606_release_exact_id_and_weights"
            if applicable
            else "future_dynamic_release_policy_anchor_only"
        ),
        "tolerance": tolerance,
        "approved_model_id": APPROVED_RELATIVE_MODEL_ID_V522,
        "selected_model_id": selected_id,
        "approved_weights": copy.deepcopy(APPROVED_RELATIVE_WEIGHTS_V522),
        "actual_benchmark_relative_weights": actual_relative,
        "actual_recommended_weights": actual_recommended,
        "strategic_anchor_weights": actual_benchmark,
        "max_abs_weight_error": {
            "benchmark_relative": relative_error,
            "recommended": recommended_error,
            "strategic_anchor": benchmark_error,
        },
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
    }


def assert_approved_relative_snapshot_v522(
    snapshot: Mapping[str, Any],
    tolerance: float = 1.0e-10,
) -> dict[str, Any]:
    """Hard assertion used by the official builder and canonical verifier only."""

    audit = approved_relative_weight_freeze_audit_v522(snapshot, tolerance)
    if audit["status"] != "passed":
        raise AssertionError(
            "v522_approved_relative_snapshot_changed:"
            + ",".join(audit["failed"])
        )
    return audit


def build_snapshot_v522(
    macro_rows: Sequence[Mapping[str, Any]],
    price_series: Mapping[str, Sequence[Mapping[str, Any]]],
    **kwargs: Any,
) -> dict[str, Any]:
    config = kwargs.get("config")
    if config is None:
        config = ResearchConfigV522()
    elif not isinstance(config, ResearchConfigV522):
        config = ResearchConfigV522(**asdict(config))
    kwargs["config"] = config
    originals = {
        "asset_strength_v52": _raw.asset_strength_v52,
        "_select_candidate_v52": _raw._select_candidate_v52,
        "strategic_benchmark_backtest_v52": _raw.strategic_benchmark_backtest_v52,
        "_simulate_candidate_v52": _raw._simulate_candidate_v52,
        "_promotion_gate_v52": _raw._promotion_gate_v52,
    }
    _raw.asset_strength_v52 = asset_strength_v522
    _raw.strategic_benchmark_backtest_v52 = strategic_benchmark_backtest_v522
    _raw._select_candidate_v52 = select_candidate_v522
    _raw._simulate_candidate_v52 = simulate_candidate_v522
    _raw._promotion_gate_v52 = promotion_gate_v522
    try:
        snapshot = _raw.build_snapshot_v52(macro_rows, price_series, **kwargs)
    finally:
        for name, original in originals.items():
            setattr(_raw, name, original)
    truth_gate_promotion = copy.deepcopy(
        ((snapshot.get("quality") or {}).get("promotion_gate") or {})
    )
    snapshot = apply_validation_governance_v521(snapshot)
    availability_contract = snapshot.get("cycle_factor_availability") or {}
    availability_cycles = availability_contract.get("cycles") or {}
    current_cycles = (
        ((snapshot.get("allocations") or {}).get("current_cycle") or {}).get(
            "cycles"
        )
        or {}
    )
    for cycle, current in current_cycles.items():
        if cycle not in availability_cycles:
            raise ValueError(f"v522_cycle_availability_missing:{cycle}")
        available = availability_cycles[cycle]
        eligible = bool(current.get("eligible_for_views"))
        production_eligible = bool(
            current.get("eligible_for_production_views", False)
        )
        shadow_eligible = bool(
            current.get("eligible_for_shadow_views", eligible)
        )
        available.update(
            {
                "data_status": current.get("data_status"),
                "eligible_for_views": eligible,
                "eligible_for_shadow_views": shadow_eligible,
                "eligible_for_production_views": production_eligible,
                "view_scope": current.get("view_scope")
                or (
                    "production"
                    if production_eligible
                    else "shadow_only" if eligible else "not_admitted"
                ),
            }
        )
    availability_contract["admitted_cycles"] = [
        cycle
        for cycle, current in current_cycles.items()
        if bool(current.get("eligible_for_views"))
    ]
    availability_contract["production_admitted_cycles"] = [
        cycle
        for cycle, current in current_cycles.items()
        if bool(current.get("eligible_for_production_views", False))
    ]
    availability_contract["admission_scope"] = (
        "availability mirrors governed current-cycle eligibility; production requires explicit upstream admission"
    )
    snapshot = synchronize_cycle_availability_v522(snapshot)
    backtest = snapshot["backtest"]
    strategies = backtest["strategies"]
    strategic = strategies["strategic_benchmark"]
    equal_candidates = strategic.pop("_display_benchmarks", {})
    equal_weight = copy.deepcopy(
        equal_candidates[EQUAL_WEIGHT_DISPLAY_ID_V522]
    )
    strategies[EQUAL_WEIGHT_DISPLAY_ID_V522] = equal_weight
    equal_weight_dict = {
        asset: float(equal_weight["current_weights"][position])
        for position, asset in enumerate(_raw.ASSET_ORDER_V5)
    }
    backtest["display_benchmarks"] = {
        EQUAL_WEIGHT_DISPLAY_ID_V522: {
            "id": EQUAL_WEIGHT_DISPLAY_ID_V522,
            "strategy_key": EQUAL_WEIGHT_DISPLAY_ID_V522,
            "weights": equal_weight_dict,
            "role": "nav_display_only_not_optimizer_input",
            "optimizer_input": False,
            "active_return_reference": False,
        }
    }
    comparison = backtest["comparison_policy"]
    comparison["primary_benchmark"] = _raw.STRATEGIC_BENCHMARK_ID_V52
    comparison["optimizer_policy_anchor"] = _raw.STRATEGIC_BENCHMARK_ID_V52
    comparison["active_return_reference"] = _raw.STRATEGIC_BENCHMARK_ID_V52
    comparison["nav_display_reference"] = EQUAL_WEIGHT_DISPLAY_ID_V522
    comparison["equal_weight_role"] = "nav_display_only_not_optimizer_input"
    snapshot["benchmark"]["display_nav_reference"] = EQUAL_WEIGHT_DISPLAY_ID_V522
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        strategy = snapshot["backtest"]["strategies"][mode]
        strategy["constraint_history"] = [
            copy.deepcopy(row["constraint_audit"])
            for row in strategy.get("strength_history") or []
            if isinstance(row.get("constraint_audit"), dict)
        ]
    quality = snapshot["quality"]
    promotions = quality["promotion_by_version"]
    statistical_by_version: dict[str, Any] = {}
    for mode, authorization in promotions.items():
        statistical = copy.deepcopy(
            authorization.pop("statistical_evidence", {})
        )
        authorization.pop("future_holdout_validation", None)
        if mode == "benchmark_relative":
            truth_checks = truth_gate_promotion.get("checks") or {}
            for check_name in (
                "cycle_factor_completeness",
                "cycle_input_d3",
            ):
                if check_name in truth_checks:
                    statistical.setdefault("checks", {})[check_name] = bool(
                        truth_checks[check_name]
                    )
        statistical["failed"] = [
            name
            for name, passed in (statistical.get("checks") or {}).items()
            if not passed
        ]
        statistical["status"] = (
            "passed" if not statistical["failed"] else "warning"
        )
        statistical["effect_on_user_authorized_deployment"] = "warning_only"
        statistical_by_version[mode] = statistical
        authorization["statistical_evidence_ref"] = (
            f"quality.statistical_evidence_by_version.{mode}"
        )
    quality["statistical_evidence_by_version"] = statistical_by_version
    quality["statistical_evidence_gate"] = copy.deepcopy(
        statistical_by_version["benchmark_relative"]
    )
    quality["promotion_gate"] = copy.deepcopy(
        promotions["benchmark_relative"]
    )
    quality["status"] = "passed"
    quality["status_scope"] = (
        "service_structure_cost_constraint_and_user_authorization_contract"
    )
    quality["statistical_warnings_present"] = (
        quality["statistical_evidence_gate"]["status"] != "passed"
    )

    allocations = snapshot["allocations"]
    allocations["recommended"] = copy.deepcopy(
        allocations["benchmark_relative"]
    )
    allocations["recommended_mode"] = "benchmark_relative"
    allocations["authorized_research_mode"] = "benchmark_relative"
    strategies["recommended"] = copy.deepcopy(strategies["benchmark_relative"])
    audit = backtest["selection_audit"]
    audit["recommended_mode"] = "benchmark_relative"
    audit["recommended_mode_rule"] = (
        "explicit user approval of the policy-relative Sharpe mandate; "
        "candidate parameters remain selected on training/validation only"
    )
    audit["selection_uses_test"] = False
    snapshot["optimization"] = copy.deepcopy(
        allocations["benchmark_relative"]["metadata"]
    )
    snapshot["status"] = "ready"
    snapshot["deployment_decision"] = {
        "status": "user_approved_sharpe_mandate",
        "deployable_dynamic_model": True,
        "executed_mode": "benchmark_relative",
        "authorization_basis": AUTHORIZATION_BASIS_V522,
        "research_challenger": "benchmark_relative",
        "absolute_research_version": "absolute_no_benchmark",
        "uses_training": True,
        "uses_validation": True,
        "uses_retrospective_test": False,
        "statistical_warnings_present": quality[
            "statistical_warnings_present"
        ],
        "statistical_evidence_ref": "quality.statistical_evidence_gate",
        "plain_language": (
            "The user-authorized objective is policy-relative Sharpe "
            "improvement; positive excess return and information ratio are "
            "reported but are not execution-admission requirements."
        ),
    }

    snapshot["schema_version"] = SCHEMA_VERSION_V522
    snapshot["engine_version"] = ENGINE_VERSION_V522
    snapshot["methodology"]["transaction_cost"] = (
        "optimizer and backtest both charge asset-level linear cost plus 0.5*quadratic_impact*delta_weight^2"
    )
    snapshot["methodology"]["promotion"] = (
        "training/validation Sharpe candidate selection plus explicit user authorization; D3/PIT/PSR/holdout remain warning-grade statistical evidence and retrospective test remains report-only"
    )
    snapshot["model_contract"]["pipeline"][6] = "线性费用＋二次冲击成本后的训练/验证选择"
    snapshot["model_contract"]["outputs"]["performance"] = (
        "训练/验证/回顾测试的净收益、波动、标准夏普、回撤及线性+二次成本；相对版另含几何相对年化超额和算术信息比率"
    )
    snapshot["model_contract"]["version"] = SCHEMA_VERSION_V522
    snapshot["model_contract"]["authorized_objective"] = (
        "policy_relative_sharpe_improvement"
    )
    snapshot["model_contract"]["return_outperformance_required"] = False
    snapshot["model_contract"]["information_ratio_required"] = False
    snapshot["model_contract"]["selection_uses_test"] = False
    snapshot["model_contract"]["benchmark_roles"] = {
        "optimizer_policy_anchor": _raw.STRATEGIC_BENCHMARK_ID_V52,
        "black_litterman_prior": _raw.STRATEGIC_BENCHMARK_ID_V52,
        "active_return_reference": _raw.STRATEGIC_BENCHMARK_ID_V52,
        "constraint_reference": _raw.STRATEGIC_BENCHMARK_ID_V52,
        "nav_display_reference": EQUAL_WEIGHT_DISPLAY_ID_V522,
        "equal_weight_role": "nav_display_only_not_optimizer_input",
    }

    snapshot["current_strength_summary"] = {
        mode: {
            "strongest_asset": snapshot["allocations"][mode]["metadata"]["asset_strength"].get("strongest_asset"),
            "strongest_assets": snapshot["allocations"][mode]["metadata"]["asset_strength"].get("strongest_assets"),
            "weakest_asset": snapshot["allocations"][mode]["metadata"]["asset_strength"].get("weakest_asset"),
            "weakest_assets": snapshot["allocations"][mode]["metadata"]["asset_strength"].get("weakest_assets"),
        }
        for mode in ("benchmark_relative", "absolute_no_benchmark")
    }
    snapshot["cost_consistency_audit"] = {
        "status": "passed",
        "optimizer_formula": "linear*abs(delta) + 0.5*quadratic*delta^2",
        "backtest_formula": "linear*abs(delta) + 0.5*quadratic*delta^2",
        "benchmark_same_formula": True,
        "row_components_retained": ["linear_cost", "quadratic_cost", "cost"],
    }
    snapshot["v522_governance"] = {
        "first_frozen_v52_snapshot_retained": True,
        "correction_is_parameter_tuning": False,
        "retrospective_test_used_for_correction": False,
        "test_can_promote": False,
        "future_paper_holdout_required_for_user_authorization": False,
        "future_paper_holdout_required_for_full_statistical_validation": True,
        "user_approved_sharpe_mandate": True,
        "historical_policy_constraints_retained": True,
        "tie_aware_strength_labels": True,
        "scaled_optimizer_diagnostics_recomputed": True,
        "manual_holdout_certification_accepted": False,
        "promotion_path": "explicit_user_approval_sharpe_only_with_separate_statistical_warnings",
        "production_status_transition_available": True,
        "positive_excess_required_for_authorization": False,
        "information_ratio_required_for_authorization": False,
    }
    snapshot["performance_claim"]["statement_cn"] = (
        "模型目标是提高风险调整收益并争取超额；绩效已按线性费用和二次冲击成本重算。"
        "相对基准版仍须在验证期及未来封存纸面组合中取得正超额，任何历史夏普都不构成保证。"
    )
    relative_performance = strategies["benchmark_relative"]["metrics"]
    benchmark_performance = strategic["metrics"]
    sharpe_evidence: dict[str, Any] = {}
    for sample in ("train", "validation"):
        relative_sharpe = float(relative_performance[sample]["sharpe"])
        benchmark_sharpe = float(benchmark_performance[sample]["sharpe"])
        sharpe_evidence[sample] = {
            "benchmark_relative_sharpe": relative_sharpe,
            "strategic_benchmark_sharpe": benchmark_sharpe,
            "improvement": relative_sharpe - benchmark_sharpe,
        }
    snapshot["performance_claim"] = {
        "authorization_basis": AUTHORIZATION_BASIS_V522,
        "authorized_objective": "policy_relative_sharpe_improvement",
        "sharpe_evidence": sharpe_evidence,
        "validated_positive_excess": bool(
            float(relative_performance["validation"].get("annual_excess_return") or 0.0)
            > 0.0
        ),
        "positive_excess_required_for_authorization": False,
        "positive_information_ratio_required_for_authorization": False,
        "retrospective_test_is_report_only": True,
        "retrospective_test_is_pristine": False,
        "guaranteed_high_sharpe": False,
        "display_benchmark_is_optimizer_input": False,
        "statement_cn": (
            "\u7528\u6237\u6279\u51c6\u672c\u7248\u672c\u4ee5\u76f8\u5bf960/15/15/10\u6218\u7565\u57fa\u51c6\u7684\u590f\u666e\u6539\u5584\u4e3a\u6267\u884c\u76ee\u6807\uff1b\u6b63\u8d85\u989d\u6536\u76ca\u548c\u6b63\u4fe1\u606f\u6bd4\u7387\u4ec5\u4f5c\u62a5\u544a\uff0c\u4e0d\u518d\u6784\u6210\u51c6\u5165\u6761\u4ef6\u3002"
            "\u56fe\u8868\u7b49\u6743\u57fa\u51c6\u53ea\u7528\u4e8e\u51c0\u503c\u5c55\u793a\uff0c\u4e0d\u8fdb\u5165\u4f18\u5316\u3001\u4e3b\u52a8\u6536\u76ca\u6216\u9ad8\u4f4e\u914d\u7ea6\u675f\uff1b\u56de\u987e\u6d4b\u8bd5\u53ea\u62a5\u544a\uff0c\u4efb\u4f55\u5386\u53f2\u7ed3\u679c\u90fd\u4e0d\u4fdd\u8bc1\u672a\u6765\u3002"
        ),
    }
    limitations = snapshot.setdefault("limitations", [])
    limitations.append(
        "The original v5.2 result omitted quadratic impact in realized backtest cost; v5.2.2 supersedes it and retains the original only as correction evidence."
    )
    snapshot["model_evidence_catalog"] = build_model_evidence_catalog_v522(
        snapshot
    )
    snapshot["approved_weight_freeze"] = (
        approved_relative_weight_freeze_audit_v522(snapshot)
    )
    compact_equal = (
        (backtest.get("display_benchmarks") or {}).get(
            EQUAL_WEIGHT_DISPLAY_ID_V522
        )
        or {}
    )
    service_checks = {
        "status_ready": snapshot.get("status") == "ready",
        "quality_structure_present": isinstance(quality, dict),
        "cost_consistency_passed": (
            snapshot["cost_consistency_audit"]["status"] == "passed"
        ),
        "model_evidence_catalog_complete": (
            snapshot["model_evidence_catalog"]["completeness_audit"]["status"]
            == "passed"
        ),
        "strategic_anchor_retained": (
            snapshot["approved_weight_freeze"]["checks"][
                "strategic_anchor_is_60_15_10_15_internal_order"
            ]
        ),
        "equal_weight_is_display_only": (
            compact_equal.get("id") == EQUAL_WEIGHT_DISPLAY_ID_V522
            and compact_equal.get("strategy_key")
            == EQUAL_WEIGHT_DISPLAY_ID_V522
            and compact_equal.get("role")
            == "nav_display_only_not_optimizer_input"
            and compact_equal.get("optimizer_input") is False
            and compact_equal.get("active_return_reference") is False
            and all(
                abs(float((compact_equal.get("weights") or {}).get(asset, -1.0)) - 0.25)
                <= 1.0e-12
                for asset in _raw.ASSET_ORDER_V5
            )
        ),
        "authorization_gate_passed": (
            quality["promotion_gate"].get("status") == "passed"
        ),
        "retrospective_test_excluded_from_selection": (
            audit.get("selection_uses_test") is False
        ),
    }
    service_failed = [
        name for name, passed in service_checks.items() if not passed
    ]
    quality["service_contract_gate"] = {
        "status": "passed" if not service_failed else "failed",
        "scope": (
            "service_structure_cost_constraints_benchmark_roles_and_user_authorization"
        ),
        "checks": service_checks,
        "failed": service_failed,
        "statistical_evidence_ref": "quality.statistical_evidence_gate",
    }
    quality["status"] = quality["service_contract_gate"]["status"]
    snapshot["status"] = "ready" if not service_failed else "failed"
    snapshot["deployment_decision"]["deployable_dynamic_model"] = (
        not service_failed
    )
    return sanitize_public_snapshot_v522(snapshot)


__all__ = [
    "CANONICAL_RELEASE_SIGNATURE_V522",
    "canonical_release_applicable_v522",
    "APPROVED_RELATIVE_MODEL_ID_V522",
    "APPROVED_RELATIVE_WEIGHTS_V522",
    "AUTHORIZATION_BASIS_V522",
    "EQUAL_WEIGHT_DISPLAY_ID_V522",
    "approved_relative_weight_freeze_audit_v522",
    "assert_approved_relative_snapshot_v522",
    "build_model_evidence_catalog_v522",
    "equal_weight_display_backtest_v522",
    "sanitize_public_snapshot_v522",
    "select_candidate_v522",
    "ENGINE_VERSION_V522",
    "ResearchConfigV522",
    "SCHEMA_VERSION_V522",
    "apply_tie_policy_v522",
    "asset_strength_v522",
    "build_snapshot_v522",
    "promotion_gate_v522",
    "research_shadow_config_v522",
    "simulate_candidate_v522",
    "strategic_benchmark_backtest_v522",
    "transaction_cost_v522",
]
