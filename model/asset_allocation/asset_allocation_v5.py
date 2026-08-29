"""Governed four-asset allocation research engine (v5 shadow/challenger).

The investable universe is fixed to Chinese equity, Chinese government bonds,
RMB gold and an ex-gold commodity-futures sleeve.  The model is a causal chain:

1. point-in-time data admission and probabilistic cycle tracking;
2. statistical plus macro-factor covariance;
3. equal/constrained risk budgeting;
4. train-only cycle views in a full-uncertainty Black--Litterman posterior;
5. one robust optimizer with bounds, turnover and explicit costs;
6. train/validation selection and sealed test reporting.

This module never fetches data and never promotes itself.  A failed data or
statistical gate remains visible in the snapshot.  High historical Sharpe is
an evaluation outcome, never a hard-coded promise.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np

from allocation_math_v5 import (
    black_litterman_posterior_v5,
    fit_macro_factor_covariance_v5,
    optimize_allocation_v5,
    portfolio_risk_contribution_v5,
    reverse_equilibrium_returns_v5,
    solve_constrained_risk_budget_v5,
    solve_erc_v5,
)
from asset_data_v5 import (
    ASSET_LABELS_V5,
    ASSET_ORDER_V5,
    AssetSeriesSpecV5,
    default_asset_registry_v5,
    validate_asset_registry_v5,
)
from cycle_views_v5 import (
    build_macro_cycle_probabilities_v5,
    build_pring_market_probabilities_v5,
    fit_cycle_view_model_v5,
    forecast_cycle_views_v5,
    merge_cycle_history_v5,
)


ENGINE_VERSION_V5 = "asset-allocation-research-v5.0-shadow"
MODEL_FORMULA_V5 = (
    "D3/PIT data -> probabilistic cycles -> macro+statistical covariance -> "
    "constrained risk budget -> Black-Litterman -> robust cost-aware optimizer"
)


@dataclass(frozen=True)
class ResearchConfigV5:
    train_end: str = "202212"
    validation_end: str = "202412"
    lookback_months: int = 24
    minimum_cycle_train: int = 24
    transaction_cost_bps: tuple[float, float, float, float] = (8.0, 5.0, 12.0, 18.0)
    quadratic_cost: tuple[float, float, float, float] = (0.0010, 0.0005, 0.0015, 0.0020)
    lower_bounds: tuple[float, float, float, float] = (0.10, 0.15, 0.05, 0.05)
    upper_bounds: tuple[float, float, float, float] = (0.60, 0.75, 0.35, 0.40)
    max_one_way_turnover: float = 0.25
    max_annual_volatility: float | None = None
    macro_pit_required_fraction: float = 0.90
    minimum_train_returns: int = 18
    minimum_validation_returns: int = 12
    minimum_test_returns: int = 12
    promotion_min_test_sharpe: float = 0.50
    promotion_min_psr: float = 0.90
    production_mode: bool = False

    def validate(self) -> None:
        if not (self.train_end < self.validation_end):
            raise ValueError("train_end_must_precede_validation_end")
        if self.lookback_months < 12:
            raise ValueError("lookback_months_must_be_at_least_12")
        lower = np.asarray(self.lower_bounds, dtype=float)
        upper = np.asarray(self.upper_bounds, dtype=float)
        if lower.shape != (4,) or upper.shape != (4,) or np.any(lower < 0) or np.any(lower > upper):
            raise ValueError("invalid_four_asset_bounds")
        if lower.sum() > 1.0 or upper.sum() < 1.0:
            raise ValueError("four_asset_bounds_make_simplex_infeasible")
        if len(self.transaction_cost_bps) != 4 or len(self.quadratic_cost) != 4:
            raise ValueError("cost_vectors_must_have_four_assets")
        if not 0.0 <= self.max_one_way_turnover <= 1.0:
            raise ValueError("max_one_way_turnover_out_of_range")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _month(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:6]


def _next_month(month: str) -> str:
    ordinal = int(month[:4]) * 12 + int(month[4:])
    return f"{ordinal // 12:04d}{ordinal % 12 + 1:02d}"


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def monthly_prices_v5(
    price_series: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[str], np.ndarray, dict[str, Any]]:
    """Align month-end positive levels without filling across missing months."""

    per_asset: dict[str, dict[str, float]] = {}
    audit: dict[str, Any] = {}
    for asset in ASSET_ORDER_V5:
        rows = price_series.get(asset) or []
        values: dict[str, tuple[str, float]] = {}
        for row in rows:
            date = "".join(character for character in str(row.get("date") or row.get("trade_date") or "") if character.isdigit())[:8]
            close = _safe_float(row.get("close"))
            if len(date) < 6 or close is None or close <= 0:
                continue
            month = date[:6]
            if month not in values or date >= values[month][0]:
                values[month] = (date, close)
        per_asset[asset] = {month: item[1] for month, item in values.items()}
        audit[asset] = {
            "raw_rows": len(rows),
            "valid_months": len(values),
            "first_month": min(values) if values else None,
            "last_month": max(values) if values else None,
        }
    if any(not per_asset[asset] for asset in ASSET_ORDER_V5):
        missing = [asset for asset in ASSET_ORDER_V5 if not per_asset[asset]]
        raise ValueError("four_asset_price_series_missing:" + ",".join(missing))
    months = sorted(set.intersection(*(set(per_asset[asset]) for asset in ASSET_ORDER_V5)))
    if len(months) < 24:
        raise ValueError(f"four_asset_common_history_too_short:{len(months)}")
    matrix = np.asarray(
        [[per_asset[asset][month] for asset in ASSET_ORDER_V5] for month in months],
        dtype=float,
    )
    returns = matrix[1:] / matrix[:-1] - 1.0
    if not np.all(np.isfinite(returns)) or np.any(returns <= -1.0):
        raise ValueError("four_asset_return_matrix_invalid")
    audit["common"] = {"months": len(months), "first_month": months[0], "last_month": months[-1]}
    return months, matrix, audit


def _pit_safe_macro_rows(
    macro_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Move verified observations to first availability month; never back-date."""

    output: list[dict[str, Any]] = []
    verified = 0
    for raw in macro_rows:
        row = dict(raw)
        observation = _month(row.get("observation_period") or row.get("month"))
        available = _month(row.get("available_time") or row.get("release_time"))
        has_vintage = row.get("vintage") not in (None, "") or row.get("revision_field") not in (None, "")
        is_verified = bool(row.get("_pit_verified")) and len(observation) == 6 and len(available) == 6 and has_vintage
        if is_verified and available >= observation:
            row["observation_period"] = observation
            row["month"] = available
            row["_pit_verified"] = True
            verified += 1
        else:
            row["month"] = _month(row.get("month"))
            row["_pit_verified"] = False
        if len(str(row.get("month") or "")) == 6:
            output.append(row)
    output.sort(key=lambda row: str(row["month"]))
    fraction = verified / max(len(output), 1)
    return output, {
        "rows": len(output),
        "pit_verified_rows": verified,
        "pit_verified_fraction": fraction,
        "policy": "verified observations are timestamped at available_time, never observation_period",
    }


def _macro_innovations(cycle_history: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    levels = np.asarray(
        [
            [
                float(row.get("growth_score") or 0.0),
                float(row.get("inflation_score") or 0.0),
                float(row.get("credit_score") or 0.0),
                float(row.get("liquidity_score") or 0.0),
            ]
            for row in cycle_history
        ],
        dtype=float,
    )
    innovations = np.vstack([np.zeros((1, 4)), np.diff(levels, axis=0)])
    admitted = np.asarray(
        [
            any(bool((row.get("cycle_eligibility") or {}).get(name)) for name in ("kitchin", "juglar", "merrill"))
            for row in cycle_history
        ],
        dtype=bool,
    )
    innovations[~admitted] = 0.0
    return innovations, admitted


_MERRILL_BUDGETS = {
    "reflation": np.asarray([0.15, 0.40, 0.30, 0.15]),
    "recovery": np.asarray([0.38, 0.27, 0.18, 0.17]),
    "overheat": np.asarray([0.32, 0.15, 0.18, 0.35]),
    "stagflation": np.asarray([0.12, 0.18, 0.32, 0.38]),
}


def _probability_values(payload: Mapping[str, Any]) -> list[tuple[str, float]]:
    probabilities = payload.get("probabilities") or {}
    return [(str(state), max(float(value), 0.0)) for state, value in probabilities.items()]


def cycle_risk_budget_v5(cycle_row: Mapping[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """Probability-weighted policy prior; Kondratieff can never change weights."""

    base = np.full(4, 0.25)
    components: list[tuple[str, float, np.ndarray]] = [("strategic_equal_risk", 0.45, base)]
    cycles = cycle_row.get("cycles") or {}

    pring = cycles.get("pring") or {}
    if pring.get("eligible_for_views"):
        phase_budget = {
            "1": np.asarray([0.12, 0.53, 0.25, 0.10]),
            "2": np.asarray([0.33, 0.37, 0.20, 0.10]),
            "3": np.asarray([0.34, 0.25, 0.18, 0.23]),
            "4": np.asarray([0.35, 0.12, 0.15, 0.38]),
            "5": np.asarray([0.16, 0.14, 0.28, 0.42]),
            "6": np.asarray([0.10, 0.48, 0.32, 0.10]),
        }
        values = _probability_values(pring)
        vector = sum(probability * phase_budget.get(state, base) for state, probability in values)
        components.append(("pring", 0.30, vector / max(vector.sum(), 1.0e-12)))

    merrill = cycles.get("merrill") or {}
    if merrill.get("eligible_for_views"):
        mapped: list[tuple[float, np.ndarray]] = []
        for state, probability in _probability_values(merrill):
            lowered = state.lower()
            if "复苏" in state or "recovery" in lowered:
                key = "recovery"
            elif "过热" in state or "overheat" in lowered:
                key = "overheat"
            elif "滞涨" in state or "stag" in lowered:
                key = "stagflation"
            else:
                key = "reflation"
            mapped.append((probability, _MERRILL_BUDGETS[key]))
        vector = sum(probability * budget for probability, budget in mapped)
        components.append(("merrill", 0.15, vector / max(vector.sum(), 1.0e-12)))

    eligible_slow = [name for name in ("kitchin", "juglar") if bool((cycles.get(name) or {}).get("eligible_for_views"))]
    if eligible_slow:
        growth = float(cycle_row.get("growth_score") or 0.0)
        credit = float(cycle_row.get("credit_score") or 0.0)
        expansion = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, 0.6 * growth + 0.4 * credit))))
        vector = np.asarray([0.16 + 0.28 * expansion, 0.44 - 0.25 * expansion, 0.26 - 0.08 * expansion, 0.14 + 0.05 * expansion])
        components.append(("kitchin_juglar_confirmation", 0.10, vector / vector.sum()))

    total_weight = sum(weight for _, weight, _ in components)
    target = sum(weight * vector for _, weight, vector in components) / total_weight
    target = np.maximum(target, 1.0e-4)
    target /= target.sum()
    return target, {
        "components": [{"name": name, "blend_weight": weight / total_weight} for name, weight, _ in components],
        "kondratieff_weight": 0.0,
        "policy": "convex probability blend; learned BL views provide the empirical return mapping",
    }


def candidate_grid_v5() -> list[dict[str, Any]]:
    """Small predeclared grid; no candidate is created after seeing test data."""

    grid: list[dict[str, Any]] = []
    identifier = 0
    for half_life in (18.0, 30.0):
        for shrinkage in (0.25, 0.50):
            for macro_weight in (0.0, 0.25):
                identifier += 1
                grid.append(
                    {
                        "id": f"V5-{identifier:02d}",
                        "half_life": half_life,
                        "diagonal_shrinkage": shrinkage,
                        "macro_blend_weight": macro_weight,
                        "risk_aversion": 4.0,
                        "tau": 0.05,
                        "uncertainty_penalty": 0.40,
                        "anchor_penalty": 1.25,
                    }
                )
    return grid


def _sample(month: str, config: ResearchConfigV5) -> str:
    if month <= config.train_end:
        return "train"
    if month <= config.validation_end:
        return "validation"
    return "test"


def _drift(weights: np.ndarray, realized: np.ndarray) -> np.ndarray:
    value = weights * (1.0 + realized)
    return value / max(float(value.sum()), 1.0e-12)


def _performance(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = np.asarray([float(row["net_return"]) for row in rows], dtype=float)
    if len(values) == 0:
        return {"months": 0, "annual_return": None, "annual_volatility": None, "sharpe": None, "max_drawdown": None, "average_turnover": None}
    total = float(np.prod(1.0 + values))
    annual_return = total ** (12.0 / len(values)) - 1.0
    volatility = float(np.std(values, ddof=1) * math.sqrt(12.0)) if len(values) > 1 else 0.0
    sharpe = annual_return / volatility if volatility > 1.0e-12 else None
    nav = np.cumprod(1.0 + values)
    peak = np.maximum.accumulate(nav)
    maximum_drawdown = float(np.min(nav / peak - 1.0))
    return {
        "months": len(values),
        "annual_return": annual_return,
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": maximum_drawdown,
        "average_turnover": float(np.mean([float(row["turnover"]) for row in rows])),
        "annual_cost_drag": float(np.mean([float(row["cost"]) for row in rows]) * 12.0),
        "skewness": float(np.mean(((values - values.mean()) / max(values.std(ddof=0), 1.0e-12)) ** 3)),
        "excess_kurtosis": float(np.mean(((values - values.mean()) / max(values.std(ddof=0), 1.0e-12)) ** 4) - 3.0),
    }


def _psr(metrics: Mapping[str, Any], benchmark_sharpe: float) -> float | None:
    n = int(metrics.get("months") or 0)
    sharpe = metrics.get("sharpe")
    if n < 3 or sharpe is None:
        return None
    monthly_sharpe = float(sharpe) / math.sqrt(12.0)
    benchmark_monthly = float(benchmark_sharpe) / math.sqrt(12.0)
    skew = float(metrics.get("skewness") or 0.0)
    excess = float(metrics.get("excess_kurtosis") or 0.0)
    denominator = math.sqrt(max(1.0 - skew * monthly_sharpe + 0.25 * excess * monthly_sharpe * monthly_sharpe, 1.0e-12))
    statistic = (monthly_sharpe - benchmark_monthly) * math.sqrt(max(n - 1, 1)) / denominator
    return NormalDist().cdf(statistic)


def _allocate_at_v5(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: np.ndarray,
    cycle_row: Mapping[str, Any],
    view_model: Mapping[str, Any],
    previous: np.ndarray,
    spec: Mapping[str, Any],
    config: ResearchConfigV5,
) -> tuple[np.ndarray, dict[str, Any]]:
    effective_macro_weight = float(spec["macro_blend_weight"]) if float(np.mean(macro_admitted)) >= config.macro_pit_required_fraction else 0.0
    covariance = fit_macro_factor_covariance_v5(
        return_history,
        macro_history,
        macro_blend_weight=effective_macro_weight,
        factor_names=("growth", "inflation", "credit", "liquidity"),
        ridge_penalty=0.20,
        statistical_half_life=float(spec["half_life"]),
        factor_half_life=30.0,
        diagonal_shrinkage=float(spec["diagonal_shrinkage"]),
        min_observations=min(24, len(return_history)),
    )
    target_budget, budget_policy = cycle_risk_budget_v5(cycle_row)
    risk_anchor = solve_constrained_risk_budget_v5(
        covariance.covariance,
        target_budget,
        config.lower_bounds,
        config.upper_bounds,
    )
    prior = reverse_equilibrium_returns_v5(covariance.covariance, risk_anchor.weights, float(spec["risk_aversion"]))
    views = forecast_cycle_views_v5(view_model, prior, cycle_row)
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        risk_anchor.weights,
        delta=float(spec["risk_aversion"]),
        tau=float(spec["tau"]),
        views=views,
    )
    constraints: dict[str, Any] = {
        "lower_bounds": config.lower_bounds,
        "upper_bounds": config.upper_bounds,
        "max_turnover": config.max_one_way_turnover,
        "annualization": 12.0,
    }
    if config.max_annual_volatility is not None:
        constraints["max_annual_volatility"] = config.max_annual_volatility
    result = optimize_allocation_v5(
        posterior,
        risk_anchor,
        covariance,
        previous,
        constraints,
        {
            "linear": np.asarray(config.transaction_cost_bps, dtype=float) / 10000.0,
            "quadratic": config.quadratic_cost,
        },
        {
            "risk_aversion": float(spec["risk_aversion"]),
            "uncertainty_penalty": float(spec["uncertainty_penalty"]),
            "anchor_penalty": float(spec["anchor_penalty"]),
            "max_iterations": 1000,
            "solver_tolerance": 1.0e-10,
        },
    )
    if result.status == "infeasible" or not np.all(np.isfinite(result.weights)):
        raise RuntimeError("v5_unified_optimizer_infeasible")
    diagnostics = {
        "covariance": covariance.to_dict(),
        "risk_budget": risk_anchor.to_dict(),
        "risk_budget_policy": budget_policy,
        "black_litterman": posterior.to_dict(),
        "optimizer": result.to_dict(),
        "macro_blend_requested": float(spec["macro_blend_weight"]),
        "macro_blend_effective": effective_macro_weight,
    }
    return result.weights, diagnostics


def _simulate_candidate_v5(
    months: Sequence[str],
    returns: np.ndarray,
    cycles: Sequence[Mapping[str, Any]],
    macro_innovations: np.ndarray,
    macro_admitted: np.ndarray,
    view_model: Mapping[str, Any],
    spec: Mapping[str, Any],
    config: ResearchConfigV5,
) -> dict[str, Any]:
    previous = np.full(4, 0.25)
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    current_diagnostics: dict[str, Any] = {}
    start = config.lookback_months - 1
    for signal_index in range(start, len(returns)):
        left = signal_index - config.lookback_months + 1
        target, diagnostics = _allocate_at_v5(
            returns[left: signal_index + 1],
            macro_innovations[left: signal_index + 1],
            macro_admitted[left: signal_index + 1],
            cycles[signal_index],
            view_model,
            previous,
            spec,
            config,
        )
        turnover = 0.5 * float(np.abs(target - previous).sum())
        cost = float(np.asarray(config.transaction_cost_bps) / 10000.0 @ np.abs(target - previous))
        weights.append({"month": months[signal_index], "signal_month": months[signal_index], **{asset: float(target[position]) for position, asset in enumerate(ASSET_ORDER_V5)}})
        current_diagnostics = diagnostics
        if signal_index + 1 < len(returns):
            realized = returns[signal_index + 1]
            month = months[signal_index + 1]
            gross = float(target @ realized)
            rows.append(
                {
                    "month": month,
                    "sample_set": _sample(month, config),
                    "gross_return": gross,
                    "net_return": gross - cost,
                    "turnover": turnover,
                    "cost": cost,
                }
            )
            previous = _drift(target, realized)
        else:
            previous = target
    metrics = {sample: _performance([row for row in rows if row["sample_set"] == sample]) for sample in ("train", "validation", "test")}
    nav = 1.0
    nav_rows: list[dict[str, Any]] = []
    for row in rows:
        nav *= 1.0 + float(row["net_return"])
        nav_rows.append({"month": row["month"], "nav": nav, "net_return": row["net_return"], "sample_set": row["sample_set"]})
    return {"spec": dict(spec), "metrics": metrics, "returns": rows, "nav": nav_rows, "weights": weights, "current_weights": previous, "current_diagnostics": current_diagnostics}


def _equal_weight_benchmark_v5(months: Sequence[str], returns: np.ndarray, config: ResearchConfigV5) -> dict[str, Any]:
    target = np.full(4, 0.25)
    previous = target.copy()
    rows: list[dict[str, Any]] = []
    costs = np.asarray(config.transaction_cost_bps) / 10000.0
    for index in range(config.lookback_months, len(returns)):
        month = months[index]
        turnover = 0.5 * float(np.abs(target - previous).sum())
        cost = float(costs @ np.abs(target - previous))
        gross = float(target @ returns[index])
        rows.append({"month": month, "sample_set": _sample(month, config), "gross_return": gross, "net_return": gross - cost, "turnover": turnover, "cost": cost})
        previous = _drift(target, returns[index])
    return {"metrics": {sample: _performance([row for row in rows if row["sample_set"] == sample]) for sample in ("train", "validation", "test")}, "returns": rows}


def _select_candidate_v5(results: Sequence[Mapping[str, Any]], config: ResearchConfigV5) -> tuple[dict[str, Any], dict[str, Any]]:
    leaderboard: list[dict[str, Any]] = []
    for result in results:
        train = result["metrics"]["train"]
        validation = result["metrics"]["validation"]
        train_sharpe = float(train.get("sharpe") if train.get("sharpe") is not None else -99.0)
        validation_sharpe = float(validation.get("sharpe") if validation.get("sharpe") is not None else -99.0)
        eligible = int(train.get("months") or 0) >= config.minimum_train_returns and int(validation.get("months") or 0) >= config.minimum_validation_returns and float(train.get("annual_return") or -1.0) > 0.0
        score = min(train_sharpe, validation_sharpe) - 0.20 * abs(train_sharpe - validation_sharpe) - 0.50 * float(validation.get("average_turnover") or 0.0)
        leaderboard.append({"id": result["spec"]["id"], "eligible": eligible, "train_sharpe": train_sharpe, "validation_sharpe": validation_sharpe, "validation_score": score})
    eligible_ids = {row["id"] for row in leaderboard if row["eligible"]}
    pool = [result for result in results if result["spec"]["id"] in eligible_ids] or list(results)
    selected = max(pool, key=lambda result: next(row["validation_score"] for row in leaderboard if row["id"] == result["spec"]["id"]))
    leaderboard.sort(key=lambda row: row["validation_score"], reverse=True)
    return dict(selected), {
        "candidate_count": len(results),
        "eligible_count": len(eligible_ids),
        "leaderboard": leaderboard,
        "selected_id": selected["spec"]["id"],
        "selection_rule": "train eligibility, then conservative validation score; test never ranks candidates",
        "selection_uses_test": False,
    }


def _promotion_gate_v5(
    selected: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    registry_audit: Mapping[str, Any],
    macro_audit: Mapping[str, Any],
    config: ResearchConfigV5,
    candidate_count: int,
) -> dict[str, Any]:
    validation = dict(selected["metrics"]["validation"])
    test = dict(selected["metrics"]["test"])
    benchmark_test = dict(benchmark["metrics"]["test"])
    candidate_sharpes = []
    if validation.get("sharpe") is not None:
        candidate_sharpes.append(float(validation["sharpe"]))
    multiple_trial_hurdle = max(0.0, NormalDist().inv_cdf(1.0 - 1.0 / max(candidate_count, 2)) / math.sqrt(max(int(validation.get("months") or 1), 1)) * math.sqrt(12.0))
    psr = _psr(test, multiple_trial_hurdle)
    checks = {
        "asset_registry_d3": bool(registry_audit.get("production_ready")),
        "macro_pit_coverage": float(macro_audit.get("pit_verified_fraction") or 0.0) >= config.macro_pit_required_fraction,
        "validation_sample": int(validation.get("months") or 0) >= config.minimum_validation_returns,
        "sealed_test_sample": int(test.get("months") or 0) >= config.minimum_test_returns,
        "test_sharpe_threshold": test.get("sharpe") is not None and float(test["sharpe"]) >= config.promotion_min_test_sharpe,
        "test_beats_equal_weight": test.get("sharpe") is not None and benchmark_test.get("sharpe") is not None and float(test["sharpe"]) > float(benchmark_test["sharpe"]),
        "probabilistic_sharpe": psr is not None and psr >= config.promotion_min_psr,
    }
    return {
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
        "probabilistic_sharpe_ratio": psr,
        "multiple_trial_sharpe_hurdle": multiple_trial_hurdle,
        "test_is_report_only": True,
        "policy": "a failed gate cannot be overridden by high backtest Sharpe",
    }


def build_snapshot_v5(
    macro_rows: Sequence[Mapping[str, Any]],
    price_series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    registry: Mapping[str, AssetSeriesSpecV5] | None = None,
    generated_at: str | None = None,
    config: ResearchConfigV5 | None = None,
) -> dict[str, Any]:
    config = config or ResearchConfigV5()
    config.validate()
    registry = dict(registry or default_asset_registry_v5())
    registry_audit = validate_asset_registry_v5(registry, require_production=config.production_mode)
    if config.production_mode and registry_audit["status"] != "passed":
        raise ValueError("v5_production_data_gate_failed:" + ",".join(registry_audit["errors"]))

    price_months, price_levels, price_audit = monthly_prices_v5(price_series)
    return_months = price_months[1:]
    returns = price_levels[1:] / price_levels[:-1] - 1.0
    safe_macro, macro_audit = _pit_safe_macro_rows(macro_rows)
    macro_probabilities = build_macro_cycle_probabilities_v5(safe_macro)
    pring = build_pring_market_probabilities_v5(return_months, returns, train_end=config.train_end)
    cycles = merge_cycle_history_v5(return_months, pring, macro_probabilities)
    if len(cycles) != len(returns):
        raise ValueError("v5_cycle_and_return_history_misaligned")
    innovations, macro_admitted = _macro_innovations(cycles)
    train_mask = [month <= config.train_end for month in return_months]
    view_model = fit_cycle_view_model_v5(
        returns,
        cycles,
        train_mask=train_mask,
        minimum_train=config.minimum_cycle_train,
    )

    results = [
        _simulate_candidate_v5(return_months, returns, cycles, innovations, macro_admitted, view_model, spec, config)
        for spec in candidate_grid_v5()
    ]
    selected, selection_audit = _select_candidate_v5(results, config)
    benchmark = _equal_weight_benchmark_v5(return_months, returns, config)
    promotion = _promotion_gate_v5(selected, benchmark, registry_audit, macro_audit, config, len(results))
    current = np.asarray(selected["current_weights"], dtype=float)
    current_covariance = np.asarray(selected["current_diagnostics"]["covariance"]["covariance"], dtype=float)
    _, _, risk_contribution = portfolio_risk_contribution_v5(current_covariance, current)
    erc = solve_erc_v5(current_covariance)
    risk_budget_payload = selected["current_diagnostics"]["risk_budget"]
    risk_budget_weights = np.asarray(risk_budget_payload["weights"], dtype=float)
    status = "ready" if config.production_mode and promotion["status"] == "passed" else "research_only"
    payload = {
        "schema_version": "5.0",
        "engine_version": ENGINE_VERSION_V5,
        "generated_at": generated_at or _utc_now(),
        "status": status,
        "asset_order": list(ASSET_ORDER_V5),
        "asset_labels": dict(ASSET_LABELS_V5),
        "asset_proxies": {asset: asdict(registry[asset]) for asset in ASSET_ORDER_V5},
        "data_as_of": {
            "market": price_months[-1],
            "macro_available": safe_macro[-1]["month"] if safe_macro else None,
            "macro_complete": safe_macro[-1]["month"] if safe_macro else None,
        },
        "methodology": {
            "formula": MODEL_FORMULA_V5,
            "rebalance": "month-end information set forms next-month target; realized test returns never tune candidates",
            "assets": "equity, government bond, RMB gold, ex-gold commodity futures; no cash asset",
            "covariance": "causal EWMA shrinkage blended with macro factor BFB'+D only when PIT admission passes",
            "risk_parity": "strict ERC Newton solution",
            "risk_budget": "Richard-Roncalli constrained risk budget with probabilistic cycle budget prior",
            "black_litterman": "reverse equilibrium prior plus joint train-only cycle relative views and full forecast-error Omega",
            "optimizer": "single robust long-only cost-aware optimizer; hard constraints are never silently relaxed",
            "kondratieff_policy": "display only; zero risk-budget and BL-view contribution",
            "sharpe_policy": "validation selects; sealed test and probabilistic Sharpe only judge promotion; no guaranteed return",
        },
        "config": asdict(config),
        "cycle_history": cycles,
        "cycle_view_model": {
            key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in view_model.items()
            if key != "feature_slices"
        },
        "allocations": {
            "current_cycle": cycles[-1],
            "as_of": price_months[-1],
            "recommended": {
                "weights": {asset: float(current[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "risk_contribution": {asset: float(risk_contribution[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "metadata": selected["current_diagnostics"],
            },
            "risk_parity": {
                "weights": {asset: float(erc.weights[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "risk_contribution": {asset: float(erc.relative_risk_contribution[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "metadata": erc.to_dict(),
            },
            "macro_risk_budget": {
                "weights": {asset: float(risk_budget_weights[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "risk_contribution": {
                    asset: float(risk_budget_payload["relative_risk_contribution"][position])
                    for position, asset in enumerate(ASSET_ORDER_V5)
                },
                "metadata": risk_budget_payload,
            },
            "robust_bl": {
                "weights": {asset: float(current[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "risk_contribution": {asset: float(risk_contribution[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
                "metadata": {"role": "BL posterior plus unified robust optimizer", **selected["current_diagnostics"]},
            },
        },
        "backtest": {
            "sample_splits": {
                "train": {"end": config.train_end, "role": "candidate eligibility and view estimation"},
                "validation": {"end": config.validation_end, "role": "candidate selection"},
                "test": {"start": _next_month(config.validation_end), "role": "sealed report and promotion only"},
            },
            "strategies": {
                "recommended": {key: selected[key] for key in ("metrics", "nav", "weights", "returns")},
                "equal_weight": benchmark,
            },
            "selection_audit": selection_audit,
        },
        "optimization": selected["current_diagnostics"],
        "quality": {
            "status": "passed" if registry_audit.get("production_ready") and len(return_months) >= config.lookback_months + config.minimum_validation_returns else "research_only",
            "asset_registry": registry_audit,
            "price_panel": price_audit,
            "macro_point_in_time": macro_audit,
            "promotion_gate": promotion,
        },
        "limitations": [
            "Local three-ETF commodity basket is an execution proxy, not a verified long-history commodity total-return index.",
            "Macro rows without available_time and vintage are display-only and cannot enter risk or BL views.",
            "The currently observed report period cannot be reused to redesign or retune the selected candidate.",
            "Historical Sharpe and return do not guarantee future performance.",
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def research_shadow_config_v5(first_month: str, last_month: str) -> ResearchConfigV5:
    """Explicit engineering split for short proxy histories; never production."""

    start_year = int(first_month[:4])
    end_year = int(last_month[:4])
    span = max(end_year - start_year, 3)
    train_year = start_year + max(2, int(span * 0.55))
    validation_year = min(end_year - 1, train_year + max(1, int(span * 0.25)))
    return replace(
        ResearchConfigV5(),
        train_end=f"{train_year:04d}12",
        validation_end=f"{validation_year:04d}12",
        minimum_train_returns=12,
        minimum_validation_returns=6,
        minimum_test_returns=6,
        production_mode=False,
    )


__all__ = [
    "ENGINE_VERSION_V5",
    "MODEL_FORMULA_V5",
    "ResearchConfigV5",
    "build_snapshot_v5",
    "candidate_grid_v5",
    "cycle_risk_budget_v5",
    "monthly_prices_v5",
    "research_shadow_config_v5",
]
