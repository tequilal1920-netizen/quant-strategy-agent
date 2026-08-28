"""Dual-policy four-asset allocation research engine (v5.2 shadow).

The engine exposes two versions from one frozen information set:

* ``benchmark_relative`` starts from the investment-policy benchmark
  60% equity / 15% government bond / 15% ex-gold commodity / 10% gold,
  then implements finite over/underweights with active-share, tracking-error,
  turnover and transaction-cost controls.
* ``absolute_no_benchmark`` uses the endogenous cycle risk-budget anchor and
  never reads the policy benchmark in its prior, objective or constraints.

In the internal asset order ``(equity, bond, gold, commodity)`` the benchmark
is therefore ``(0.60, 0.15, 0.10, 0.15)``.  This distinction is validated at
runtime so gold and commodity cannot be silently transposed.

Candidate selection uses causal expanding training estimates and validation
metrics only.  The historical test window is report-only and, because v5.2
was designed after earlier v5.1 results were observed, is explicitly labelled
retrospective rather than a pristine new holdout.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v5 as _base
from allocation_math_v5 import (
    RiskBudgetResultV5,
    black_litterman_posterior_v5,
    fit_macro_factor_covariance_v5,
    optimize_allocation_v5,
    portfolio_risk_contribution_v5,
    reverse_equilibrium_returns_v5,
    solve_constrained_risk_budget_v5,
    solve_erc_v5,
)
from asset_allocation_v51 import _current_factor_availability, _serialise_view_bundle
from asset_data_v5 import (
    ASSET_LABELS_V5,
    ASSET_ORDER_V5,
    AssetSeriesSpecV5,
    default_asset_registry_v5,
    validate_asset_registry_v5,
)
from cycle_factor_registry_v5 import (
    serialise_cycle_factor_registry_v5,
    validate_cycle_factor_registry_v5,
)
from cycle_macro_models_v5 import (
    FACTOR_SCHEMA_VERSION_V5,
    build_macro_cycle_probabilities_v5,
    build_pring_market_probabilities_v5,
    merge_cycle_history_v5,
)
from cycle_views_v5 import fit_cycle_view_model_v5, forecast_cycle_views_v5
from snapshot_factor_risk_v51 import macro_factor_risk_decomposition_v51
from snapshot_governance_v51 import harden_shadow_snapshot_v51
from snapshot_truth_gate_v51 import apply_truth_gate_v51


ENGINE_VERSION_V52 = "asset-allocation-research-v5.2-dual-policy-shadow"
MODEL_FORMULA_V52 = (
    "D3/PIT admission -> explicit-duration cycles -> full-Omega BL -> "
    "(A) 60/15/15/10 policy-relative implementation or "
    "(B) benchmark-free constrained risk budget -> costs -> validation-only selection"
)
STRATEGIC_BENCHMARK_ID_V52 = "strategic_60_15_15_10"
POLICY_BENCHMARK_WEIGHTS_V52 = (0.60, 0.15, 0.10, 0.15)
POLICY_ACTIVE_BANDS_V52 = (0.10, 0.05, 0.03, 0.05)
MODEL_VERSIONS_V52 = ("benchmark_relative", "absolute_no_benchmark")


@dataclass(frozen=True)
class ResearchConfigV52(_base.ResearchConfigV5):
    """Predeclared v5.2 policy; none of these fields are test-tuned."""

    max_one_way_turnover: float = 0.12
    policy_benchmark_weights: tuple[float, float, float, float] = POLICY_BENCHMARK_WEIGHTS_V52
    policy_active_bands: tuple[float, float, float, float] = POLICY_ACTIVE_BANDS_V52
    policy_max_active_share: float = 0.10
    policy_max_annual_tracking_error: float = 0.04
    policy_max_one_way_turnover: float = 0.08
    policy_anchor_penalty: float = 2.00
    policy_risk_anchor_penalty: float = 0.75
    authorized_recommended_mode: str = "benchmark_relative"
    risk_free_rate_annual: float = 0.0
    total_declared_trials: int = 16

    def validate(self) -> None:
        super().validate()
        if tuple(ASSET_ORDER_V5) != ("equity", "bond", "gold", "commodity"):
            raise ValueError("v52_internal_asset_order_changed")
        benchmark = np.asarray(self.policy_benchmark_weights, dtype=float)
        bands = np.asarray(self.policy_active_bands, dtype=float)
        if benchmark.shape != (4,) or bands.shape != (4,):
            raise ValueError("v52_policy_vectors_must_have_four_assets")
        if not np.all(np.isfinite(benchmark)) or np.any(benchmark <= 0.0):
            raise ValueError("v52_policy_benchmark_invalid")
        if abs(float(benchmark.sum()) - 1.0) > 1.0e-12:
            raise ValueError("v52_policy_benchmark_must_sum_to_one")
        if not np.allclose(benchmark, POLICY_BENCHMARK_WEIGHTS_V52, atol=1.0e-12):
            raise ValueError("v52_policy_benchmark_must_be_60_15_10_15_internal_order")
        if np.any(bands <= 0.0) or np.any(benchmark - bands < 0.0):
            raise ValueError("v52_policy_active_bands_invalid")
        if not 0.0 < self.policy_max_active_share <= 0.50:
            raise ValueError("v52_policy_active_share_cap_invalid")
        if not 0.0 < self.policy_max_annual_tracking_error <= 0.20:
            raise ValueError("v52_policy_tracking_error_cap_invalid")
        if not 0.0 <= self.policy_max_one_way_turnover <= 1.0:
            raise ValueError("v52_policy_turnover_cap_invalid")
        if min(self.policy_anchor_penalty, self.policy_risk_anchor_penalty) < 0.0:
            raise ValueError("v52_policy_anchor_penalties_invalid")
        if self.authorized_recommended_mode not in MODEL_VERSIONS_V52:
            raise ValueError("v52_authorized_recommended_mode_invalid")
        if self.total_declared_trials < 16:
            raise ValueError("v52_total_declared_trials_must_count_both_families")


def research_shadow_config_v52(first_month: str, last_month: str) -> ResearchConfigV52:
    base = _base.research_shadow_config_v5(first_month, last_month)
    return ResearchConfigV52(**asdict(base))


def candidate_grid_v52(mode: str | None = None) -> list[dict[str, Any]]:
    """Two fixed eight-candidate families; test data never creates candidates."""

    modes = MODEL_VERSIONS_V52 if mode is None else (mode,)
    if any(item not in MODEL_VERSIONS_V52 for item in modes):
        raise ValueError("v52_unknown_model_version")
    output: list[dict[str, Any]] = []
    for family in modes:
        prefix = "REL" if family == "benchmark_relative" else "ABS"
        for index, raw in enumerate(_base.candidate_grid_v5(), 1):
            spec = dict(raw)
            spec.update({"id": f"V52-{prefix}-{index:02d}", "model_version": family})
            output.append(spec)
    return output


def _performance_v52(rows: Sequence[Mapping[str, Any]], risk_free_rate: float = 0.0) -> dict[str, Any]:
    values = np.asarray([float(row["net_return"]) for row in rows], dtype=float)
    if len(values) == 0:
        return {
            "months": 0,
            "annual_return": None,
            "annualized_mean_return": None,
            "annual_volatility": None,
            "sharpe": None,
            "return_to_volatility": None,
            "sortino": None,
            "monthly_cvar_95": None,
            "max_drawdown": None,
            "average_turnover": None,
        }
    total = float(np.prod(1.0 + values))
    annual_return = total ** (12.0 / len(values)) - 1.0
    annualized_mean = float(values.mean() * 12.0)
    volatility = float(values.std(ddof=1) * math.sqrt(12.0)) if len(values) > 1 else 0.0
    sharpe = (annualized_mean - risk_free_rate) / volatility if volatility > 1.0e-12 else None
    return_to_volatility = annual_return / volatility if volatility > 1.0e-12 else None
    downside = np.minimum(values - risk_free_rate / 12.0, 0.0)
    downside_deviation = float(math.sqrt(np.mean(downside * downside)) * math.sqrt(12.0))
    sortino = (annualized_mean - risk_free_rate) / downside_deviation if downside_deviation > 1.0e-12 else None
    count = max(1, int(math.ceil(0.05 * len(values))))
    cvar = float(np.mean(np.sort(values)[:count]))
    nav = np.cumprod(1.0 + values)
    peak = np.maximum.accumulate(nav)
    drawdown = float(np.min(nav / peak - 1.0))
    standardized = (values - values.mean()) / max(float(values.std(ddof=0)), 1.0e-12)
    return {
        "months": len(values),
        "annual_return": annual_return,
        "annualized_mean_return": annualized_mean,
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "sharpe_definition": "12*mean(monthly_net_return)/annualized_sample_volatility; annual risk-free rate explicitly configured",
        "risk_free_rate_annual": risk_free_rate,
        "return_to_volatility": return_to_volatility,
        "sortino": sortino,
        "monthly_cvar_95": cvar,
        "max_drawdown": drawdown,
        "average_turnover": float(np.mean([float(row["turnover"]) for row in rows])),
        "annual_cost_drag": float(np.mean([float(row["cost"]) for row in rows]) * 12.0),
        "skewness": float(np.mean(standardized ** 3)),
        "excess_kurtosis": float(np.mean(standardized ** 4) - 3.0),
    }


def _relative_metrics_v52(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "annual_excess_return": None,
            "tracking_error": None,
            "information_ratio": None,
            "max_active_drawdown": None,
            "active_hit_rate": None,
        }
    strategy = np.asarray([float(row["net_return"]) for row in rows], dtype=float)
    benchmark = np.asarray([float(row["benchmark_return"]) for row in rows], dtype=float)
    active = strategy - benchmark
    relative_nav = np.cumprod((1.0 + strategy) / np.maximum(1.0 + benchmark, 1.0e-12))
    annual_excess = float(relative_nav[-1] ** (12.0 / len(rows)) - 1.0)
    tracking_error = float(active.std(ddof=1) * math.sqrt(12.0)) if len(active) > 1 else 0.0
    information_ratio = float(active.mean() * 12.0 / tracking_error) if tracking_error > 1.0e-12 else None
    peak = np.maximum.accumulate(relative_nav)
    return {
        "annual_excess_return": annual_excess,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "max_active_drawdown": float(np.min(relative_nav / peak - 1.0)),
        "active_hit_rate": float(np.mean(active > 0.0)),
    }


def _nav_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nav = 1.0
    output: list[dict[str, Any]] = []
    for row in rows:
        nav *= 1.0 + float(row["net_return"])
        output.append(
            {
                "month": row["month"],
                "nav": nav,
                "net_return": row["net_return"],
                "sample_set": row["sample_set"],
            }
        )
    return output


def strategic_benchmark_backtest_v52(
    months: Sequence[str], returns: np.ndarray, config: ResearchConfigV52
) -> dict[str, Any]:
    """Monthly rebalanced policy benchmark under the same cost convention."""

    target = np.asarray(config.policy_benchmark_weights, dtype=float)
    previous = target.copy()
    linear_cost = np.asarray(config.transaction_cost_bps, dtype=float) / 10000.0
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    for index in range(config.lookback_months, len(returns)):
        month = months[index]
        turnover = 0.5 * float(np.abs(target - previous).sum())
        cost = float(linear_cost @ np.abs(target - previous))
        gross = float(target @ returns[index])
        rows.append(
            {
                "month": month,
                "sample_set": _base._sample(month, config),
                "gross_return": gross,
                "net_return": gross - cost,
                "turnover": turnover,
                "cost": cost,
            }
        )
        weights.append(
            {
                "month": month,
                **{asset: float(target[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
            }
        )
        previous = _base._drift(target, returns[index])
    metrics = {
        sample: _performance_v52(
            [row for row in rows if row["sample_set"] == sample], config.risk_free_rate_annual
        )
        for sample in ("train", "validation", "test")
    }
    return {
        "id": STRATEGIC_BENCHMARK_ID_V52,
        "role": "primary_policy_benchmark_not_equal_weight",
        "weights": weights,
        "current_weights": target.tolist(),
        "metrics": metrics,
        "returns": rows,
        "nav": _nav_rows(rows),
    }


def _anchor_result_v52(
    covariance: np.ndarray, weights: np.ndarray, role: str
) -> RiskBudgetResultV5:
    _, _, relative = portfolio_risk_contribution_v5(covariance, weights)
    return RiskBudgetResultV5(
        weights=np.asarray(weights, dtype=float),
        target_budget=np.asarray(relative, dtype=float),
        relative_risk_contribution=np.asarray(relative, dtype=float),
        budget_error=np.zeros(4),
        kkt_residual=0.0,
        active_constraints=(),
        shadow_prices={},
        status="fixed_anchor",
        diagnostics={"role": role, "method": "declared_capital_weight_anchor"},
    )


def _rank_score_v52(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    scores = np.zeros(len(values), dtype=float)
    if len(values) == 1:
        return scores
    for rank, index in enumerate(order):
        scores[index] = -1.0 + 2.0 * rank / (len(values) - 1.0)
    return scores


def asset_strength_v52(
    return_history: np.ndarray,
    covariance: np.ndarray,
    posterior: Any,
    weights: np.ndarray,
    mode: str,
    benchmark: np.ndarray | None,
) -> dict[str, Any]:
    """Causal four-asset strength explanation; it is not double-counted in weights."""

    history = np.asarray(return_history, dtype=float)
    annual_vol = np.sqrt(np.maximum(np.diag(covariance), 1.0e-12) * 12.0)
    horizon_weights = {3: 0.20, 6: 0.35, 12: 0.45}
    horizon_scores: dict[int, np.ndarray] = {}
    trend = np.zeros(4)
    for horizon, blend in horizon_weights.items():
        window = history[-horizon:]
        cumulative = np.prod(1.0 + window, axis=0) - 1.0
        denominator = np.maximum(annual_vol * math.sqrt(horizon / 12.0), 1.0e-8)
        horizon_scores[horizon] = cumulative / denominator
        trend += blend * horizon_scores[horizon]
    predictive = np.asarray(posterior.predictive_covariance, dtype=float)
    posterior_vol = np.sqrt(np.maximum(np.diag(predictive), 1.0e-12) * 12.0)
    posterior_annual = np.asarray(posterior.posterior_mean, dtype=float) * 12.0
    bl_information = posterior_annual / np.maximum(posterior_vol, 1.0e-8)
    trend_rank = _rank_score_v52(trend)
    bl_rank = _rank_score_v52(bl_information)
    composite = 0.50 * trend_rank + 0.50 * bl_rank
    descending = list(np.argsort(-composite, kind="mergesort"))
    labels = ("最强", "偏强", "偏弱", "最弱")
    label_by_index = {index: labels[rank] for rank, index in enumerate(descending)}
    _, _, risk_contribution = portfolio_risk_contribution_v5(covariance, weights)
    cycle_view = np.asarray(posterior.P, dtype=float).T @ (
        np.asarray(posterior.q, dtype=float) - np.asarray(posterior.P, dtype=float) @ np.asarray(posterior.pi, dtype=float)
    )
    rows: dict[str, Any] = {}
    for position, asset in enumerate(ASSET_ORDER_V5):
        active = None if benchmark is None else float(weights[position] - benchmark[position])
        if active is None:
            stance = "绝对配置（无基准）"
        elif active > 0.005:
            stance = "高配"
        elif active < -0.005:
            stance = "低配"
        else:
            stance = "中性"
        agreement = 1.0 - abs(float(trend_rank[position] - bl_rank[position])) / 2.0
        probability = 1.0 / (1.0 + math.exp(-2.0 * float(composite[position])))
        rows[asset] = {
            "asset": asset,
            "asset_label": ASSET_LABELS_V5[asset],
            "strength_rank": descending.index(position) + 1,
            "strength_label_cn": label_by_index[position],
            "composite_strength": float(composite[position]),
            "signal_probability": probability,
            "signal_agreement_confidence": max(0.0, min(1.0, agreement)),
            "trend_score_3m": float(horizon_scores[3][position]),
            "trend_score_6m": float(horizon_scores[6][position]),
            "trend_score_12m": float(horizon_scores[12][position]),
            "risk_adjusted_trend": float(trend[position]),
            "bl_expected_return_monthly": float(posterior.posterior_mean[position]),
            "expected_return_annual": float(posterior_annual[position]),
            "bl_expected_volatility_annual": float(posterior_vol[position]),
            "bl_information_ratio": float(bl_information[position]),
            "cycle_view_return_contribution_monthly": float(cycle_view[position]),
            "capital_weight": float(weights[position]),
            "benchmark_weight": None if benchmark is None else float(benchmark[position]),
            "active_weight": active,
            "allocation_stance_cn": stance,
            "risk_contribution": float(risk_contribution[position]),
            "input_signals": [
                {"name": "3个月风险调整趋势", "value": float(horizon_scores[3][position]), "direction": "higher_is_stronger"},
                {"name": "6个月风险调整趋势", "value": float(horizon_scores[6][position]), "direction": "higher_is_stronger"},
                {"name": "12个月风险调整趋势", "value": float(horizon_scores[12][position]), "direction": "higher_is_stronger"},
                {"name": "BL后验年化预期收益", "value": float(posterior_annual[position]), "direction": "higher_is_stronger"},
                {"name": "预测年化波动", "value": float(posterior_vol[position]), "direction": "lower_is_safer_not_automatically_stronger"},
                {"name": "周期相对观点月度贡献", "value": float(cycle_view[position]), "direction": "signed"},
            ],
            "model_role": mode,
            "policy": "strength explains the frozen BL/trend inputs; it is not added again to the optimizer",
        }
    return {
        "method": "equal blend of cross-sectional ranks of 3/6/12m risk-adjusted trend and BL posterior information ratio",
        "horizon_weights": {str(key): value for key, value in horizon_weights.items()},
        "strongest_asset": ASSET_ORDER_V5[descending[0]],
        "weakest_asset": ASSET_ORDER_V5[descending[-1]],
        "rows": rows,
    }


def _policy_scaled_weights_v52(
    raw: np.ndarray,
    benchmark: np.ndarray,
    previous: np.ndarray,
    covariance: np.ndarray,
    config: ResearchConfigV52,
) -> tuple[np.ndarray, dict[str, Any]]:
    active = raw - benchmark
    raw_active_share = 0.5 * float(np.abs(active).sum())
    raw_tracking_error = math.sqrt(max(12.0 * float(active @ covariance @ active), 0.0))
    share_scale = 1.0 if raw_active_share <= config.policy_max_active_share else config.policy_max_active_share / raw_active_share
    tracking_scale = 1.0 if raw_tracking_error <= config.policy_max_annual_tracking_error else config.policy_max_annual_tracking_error / raw_tracking_error
    scale = min(1.0, share_scale, tracking_scale)
    final = benchmark + scale * active
    active_final = final - benchmark
    active_share = 0.5 * float(np.abs(active_final).sum())
    tracking_error = math.sqrt(max(12.0 * float(active_final @ covariance @ active_final), 0.0))
    turnover = 0.5 * float(np.abs(final - previous).sum())
    lower = benchmark - np.asarray(config.policy_active_bands, dtype=float)
    upper = benchmark + np.asarray(config.policy_active_bands, dtype=float)
    violations = {
        "weight_sum": abs(float(final.sum()) - 1.0),
        "lower_bound": float(np.max(np.maximum(lower - final, 0.0))),
        "upper_bound": float(np.max(np.maximum(final - upper, 0.0))),
        "active_share": max(active_share - config.policy_max_active_share, 0.0),
        "tracking_error": max(tracking_error - config.policy_max_annual_tracking_error, 0.0),
        "turnover": max(turnover - config.policy_max_one_way_turnover, 0.0),
    }
    maximum = max(violations.values())
    if maximum > 1.0e-7:
        raise RuntimeError(f"v52_policy_constraints_infeasible_after_scaling:{maximum:.3e}")
    return final, {
        "raw_active_share": raw_active_share,
        "raw_annual_tracking_error": raw_tracking_error,
        "homothetic_scale": scale,
        "active_share": active_share,
        "annual_tracking_error": tracking_error,
        "turnover": turnover,
        "active_weight": active_final.tolist(),
        "active_bands": list(config.policy_active_bands),
        "max_active_share": config.policy_max_active_share,
        "max_annual_tracking_error": config.policy_max_annual_tracking_error,
        "max_one_way_turnover": config.policy_max_one_way_turnover,
        "violations": violations,
        "max_violation": maximum,
        "method": "convex homothetic scaling toward the declared policy benchmark",
    }


def _allocate_at_v52(
    return_history: np.ndarray,
    macro_history: np.ndarray,
    macro_admitted: np.ndarray,
    cycle_row: Mapping[str, Any],
    view_model: Mapping[str, Any],
    previous: np.ndarray,
    spec: Mapping[str, Any],
    config: ResearchConfigV52,
) -> tuple[np.ndarray, dict[str, Any]]:
    mode = str(spec["model_version"])
    effective_macro_weight = (
        float(spec["macro_blend_weight"])
        if float(np.mean(macro_admitted)) >= config.macro_pit_required_fraction
        else 0.0
    )
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
    target_budget, budget_policy = _base.cycle_risk_budget_v5(cycle_row)
    risk_anchor = solve_constrained_risk_budget_v5(
        covariance.covariance,
        target_budget,
        config.lower_bounds,
        config.upper_bounds,
    )
    policy_benchmark = np.asarray(config.policy_benchmark_weights, dtype=float)
    if mode == "benchmark_relative":
        prior_weights = policy_benchmark
        policy_penalty = config.policy_anchor_penalty
        risk_penalty = config.policy_risk_anchor_penalty
        combined_weights = (
            policy_penalty * policy_benchmark + risk_penalty * risk_anchor.weights
        ) / max(policy_penalty + risk_penalty, 1.0e-12)
        optimizer_anchor = _anchor_result_v52(
            covariance.covariance, combined_weights, "policy_plus_risk_budget_dual_anchor"
        )
        lower = policy_benchmark - np.asarray(config.policy_active_bands, dtype=float)
        upper = policy_benchmark + np.asarray(config.policy_active_bands, dtype=float)
        maximum_turnover = config.policy_max_one_way_turnover
        anchor_penalty = policy_penalty + risk_penalty
    elif mode == "absolute_no_benchmark":
        prior_weights = risk_anchor.weights
        optimizer_anchor = risk_anchor
        lower = np.asarray(config.lower_bounds, dtype=float)
        upper = np.asarray(config.upper_bounds, dtype=float)
        maximum_turnover = config.max_one_way_turnover
        anchor_penalty = float(spec["anchor_penalty"])
    else:
        raise ValueError("v52_unknown_allocation_mode")
    prior = reverse_equilibrium_returns_v5(
        covariance.covariance, prior_weights, float(spec["risk_aversion"])
    )
    views = forecast_cycle_views_v5(view_model, prior, cycle_row)
    posterior = black_litterman_posterior_v5(
        covariance.covariance,
        prior_weights,
        delta=float(spec["risk_aversion"]),
        tau=float(spec["tau"]),
        views=views,
    )
    constraints: dict[str, Any] = {
        "lower_bounds": lower,
        "upper_bounds": upper,
        "max_turnover": maximum_turnover,
        "annualization": 12.0,
    }
    if config.max_annual_volatility is not None:
        constraints["max_annual_volatility"] = config.max_annual_volatility
    result = optimize_allocation_v5(
        posterior,
        optimizer_anchor,
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
            "anchor_penalty": anchor_penalty,
            "max_iterations": 1000,
            "solver_tolerance": 1.0e-10,
        },
    )
    if result.status == "infeasible" or not np.all(np.isfinite(result.weights)):
        raise RuntimeError(f"v52_{mode}_optimizer_infeasible")
    optimizer = result.to_dict()
    final = np.asarray(result.weights, dtype=float)
    policy_constraints = None
    if mode == "benchmark_relative":
        final, policy_constraints = _policy_scaled_weights_v52(
            final, policy_benchmark, previous, covariance.covariance, config
        )
        change = final - previous
        linear = np.asarray(config.transaction_cost_bps, dtype=float) / 10000.0
        quadratic = np.asarray(config.quadratic_cost, dtype=float)
        optimizer["pre_policy_scale_weights"] = optimizer["weights"]
        optimizer["weights"] = final.tolist()
        optimizer["turnover"] = 0.5 * float(np.abs(change).sum())
        optimizer["expected_cost"] = float(
            linear @ np.abs(change) + 0.5 * quadratic @ (change * change)
        )
        optimizer["status"] = result.status + "_policy_scaled"
        optimizer["policy_constraints"] = policy_constraints
        optimizer["constraint_slack"]["policy_active_share_slack"] = (
            config.policy_max_active_share - policy_constraints["active_share"]
        )
        optimizer["constraint_slack"]["policy_tracking_error_slack"] = (
            config.policy_max_annual_tracking_error
            - policy_constraints["annual_tracking_error"]
        )
        optimizer["constraint_slack"]["max_violation"] = max(
            float(optimizer["constraint_slack"].get("max_violation") or 0.0),
            float(policy_constraints["max_violation"]),
        )
    strength = asset_strength_v52(
        return_history,
        covariance.covariance,
        posterior,
        final,
        mode,
        policy_benchmark if mode == "benchmark_relative" else None,
    )
    diagnostics = {
        "model_version": mode,
        "policy_benchmark_used_in_model": mode == "benchmark_relative",
        "policy_benchmark": policy_benchmark.tolist() if mode == "benchmark_relative" else None,
        "policy_constraint_audit": policy_constraints,
        "covariance": covariance.to_dict(),
        "risk_budget": risk_anchor.to_dict(),
        "risk_budget_policy": budget_policy,
        "black_litterman": posterior.to_dict(),
        "cycle_views": _serialise_view_bundle(views),
        "optimizer": optimizer,
        "asset_strength": strength,
        "macro_blend_requested": float(spec["macro_blend_weight"]),
        "macro_blend_effective": effective_macro_weight,
    }
    return final, diagnostics


def _initial_absolute_weights_v52(
    returns: np.ndarray, config: ResearchConfigV52
) -> np.ndarray:
    covariance = np.cov(np.asarray(returns[: config.lookback_months], dtype=float), rowvar=False, ddof=1)
    return solve_constrained_risk_budget_v5(
        covariance,
        np.full(4, 0.25),
        config.lower_bounds,
        config.upper_bounds,
    ).weights


def _view_models_by_signal_v52(
    returns: np.ndarray,
    cycles: Sequence[Mapping[str, Any]],
    months: Sequence[str],
    config: ResearchConfigV52,
) -> tuple[dict[int, Mapping[str, Any]], Mapping[str, Any]]:
    train_mask = [month <= config.train_end for month in months]
    frozen = fit_cycle_view_model_v5(
        returns,
        cycles,
        train_mask=train_mask,
        minimum_train=config.minimum_cycle_train,
    )
    output: dict[int, Mapping[str, Any]] = {}
    for signal_index in range(config.lookback_months - 1, len(returns)):
        if months[signal_index] <= config.train_end:
            prefix_returns = returns[: signal_index + 1]
            prefix_cycles = cycles[: signal_index + 1]
            output[signal_index] = fit_cycle_view_model_v5(
                prefix_returns,
                prefix_cycles,
                train_mask=[True] * len(prefix_returns),
                minimum_train=config.minimum_cycle_train,
            )
        else:
            output[signal_index] = frozen
    return output, frozen


def _attach_policy_comparison_v52(
    result: dict[str, Any], benchmark: Mapping[str, Any], config: ResearchConfigV52
) -> dict[str, Any]:
    benchmark_by_month = {
        str(row["month"]): float(row["net_return"])
        for row in benchmark.get("returns") or []
    }
    rows = result["returns"]
    for row in rows:
        value = benchmark_by_month.get(str(row["month"]))
        if value is None:
            raise ValueError("v52_strategy_and_policy_benchmark_misaligned")
        row["benchmark_return"] = value
        row["active_return"] = float(row["net_return"]) - value
    for sample in ("train", "validation", "test"):
        sample_rows = [row for row in rows if row["sample_set"] == sample]
        result["metrics"][sample].update(_relative_metrics_v52(sample_rows))
    return result


def _simulate_candidate_v52(
    months: Sequence[str],
    returns: np.ndarray,
    cycles: Sequence[Mapping[str, Any]],
    macro_innovations: np.ndarray,
    macro_admitted: np.ndarray,
    view_models: Mapping[int, Mapping[str, Any]],
    spec: Mapping[str, Any],
    config: ResearchConfigV52,
    benchmark: Mapping[str, Any],
) -> dict[str, Any]:
    mode = str(spec["model_version"])
    previous = (
        np.asarray(config.policy_benchmark_weights, dtype=float)
        if mode == "benchmark_relative"
        else _initial_absolute_weights_v52(returns, config)
    )
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    strength_history: list[dict[str, Any]] = []
    current_diagnostics: dict[str, Any] = {}
    start = config.lookback_months - 1
    linear_cost = np.asarray(config.transaction_cost_bps, dtype=float) / 10000.0
    for signal_index in range(start, len(returns)):
        left = signal_index - config.lookback_months + 1
        target, diagnostics = _allocate_at_v52(
            returns[left : signal_index + 1],
            macro_innovations[left : signal_index + 1],
            macro_admitted[left : signal_index + 1],
            cycles[signal_index],
            view_models[signal_index],
            previous,
            spec,
            config,
        )
        turnover = 0.5 * float(np.abs(target - previous).sum())
        cost = float(linear_cost @ np.abs(target - previous))
        weights.append(
            {
                "month": months[signal_index],
                "signal_month": months[signal_index],
                **{asset: float(target[position]) for position, asset in enumerate(ASSET_ORDER_V5)},
            }
        )
        strength_history.append(
            {
                "month": months[signal_index],
                "strongest_asset": diagnostics["asset_strength"]["strongest_asset"],
                "weakest_asset": diagnostics["asset_strength"]["weakest_asset"],
                "rows": copy.deepcopy(diagnostics["asset_strength"]["rows"]),
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
                    "sample_set": _base._sample(month, config),
                    "gross_return": gross,
                    "net_return": gross - cost,
                    "turnover": turnover,
                    "cost": cost,
                }
            )
            previous = _base._drift(target, realized)
        else:
            previous = target
    metrics = {
        sample: _performance_v52(
            [row for row in rows if row["sample_set"] == sample],
            config.risk_free_rate_annual,
        )
        for sample in ("train", "validation", "test")
    }
    result = {
        "spec": dict(spec),
        "metrics": metrics,
        "returns": rows,
        "nav": _nav_rows(rows),
        "weights": weights,
        "strength_history": strength_history,
        "current_weights": previous,
        "current_diagnostics": current_diagnostics,
    }
    return _attach_policy_comparison_v52(result, benchmark, config)


def _number(payload: Mapping[str, Any], key: str, default: float = -99.0) -> float:
    value = payload.get(key)
    return default if value is None else float(value)


def _select_candidate_v52(
    results: Sequence[Mapping[str, Any]], mode: str, config: ResearchConfigV52
) -> tuple[dict[str, Any], dict[str, Any]]:
    leaderboard: list[dict[str, Any]] = []
    for result in results:
        train = result["metrics"]["train"]
        validation = result["metrics"]["validation"]
        train_sharpe = _number(train, "sharpe")
        validation_sharpe = _number(validation, "sharpe")
        if mode == "benchmark_relative":
            train_ir = _number(train, "information_ratio")
            validation_ir = _number(validation, "information_ratio")
            eligible = (
                int(train.get("months") or 0) >= config.minimum_train_returns
                and int(validation.get("months") or 0) >= config.minimum_validation_returns
                and _number(train, "annual_excess_return", -1.0) > 0.0
                and _number(validation, "annual_excess_return", -1.0) > 0.0
                and validation_ir > 0.0
            )
            score = (
                0.65 * min(train_ir, validation_ir)
                + 0.35 * min(train_sharpe, validation_sharpe)
                - 0.20 * abs(train_ir - validation_ir)
                - 0.50 * _number(validation, "average_turnover", 0.0)
            )
        else:
            train_ir = validation_ir = None
            eligible = (
                int(train.get("months") or 0) >= config.minimum_train_returns
                and int(validation.get("months") or 0) >= config.minimum_validation_returns
                and _number(train, "annual_return", -1.0) > 0.0
                and validation_sharpe > 0.0
            )
            score = (
                min(train_sharpe, validation_sharpe)
                - 0.20 * abs(train_sharpe - validation_sharpe)
                - 0.50 * _number(validation, "average_turnover", 0.0)
            )
        leaderboard.append(
            {
                "id": result["spec"]["id"],
                "model_version": mode,
                "eligible": bool(eligible),
                "train_sharpe": train_sharpe,
                "validation_sharpe": validation_sharpe,
                "train_information_ratio": train_ir,
                "validation_information_ratio": validation_ir,
                "train_annual_excess_return": train.get("annual_excess_return"),
                "validation_annual_excess_return": validation.get("annual_excess_return"),
                "validation_score": score,
            }
        )
    eligible_ids = {row["id"] for row in leaderboard if row["eligible"]}
    pool = [item for item in results if item["spec"]["id"] in eligible_ids] or list(results)
    selected = max(
        pool,
        key=lambda item: next(
            row["validation_score"] for row in leaderboard if row["id"] == item["spec"]["id"]
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
        "fallback_research_only": not bool(eligible_ids),
        "selection_rule": (
            "training eligibility plus conservative validation information ratio and Sharpe; test never ranks candidates"
            if mode == "benchmark_relative"
            else "training eligibility plus conservative validation absolute Sharpe; policy benchmark never enters this family"
        ),
    }


def _promotion_gate_v52(
    selected: Mapping[str, Any],
    selection: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    registry_audit: Mapping[str, Any],
    macro_audit: Mapping[str, Any],
    mode: str,
    config: ResearchConfigV52,
) -> dict[str, Any]:
    validation = dict(selected["metrics"]["validation"])
    test = dict(selected["metrics"]["test"])
    benchmark_test = dict(benchmark["metrics"]["test"])
    n = max(int(validation.get("months") or 1), 1)
    hurdle = max(
        0.0,
        NormalDist().inv_cdf(1.0 - 1.0 / max(config.total_declared_trials, 2))
        / math.sqrt(n)
        * math.sqrt(12.0),
    )
    psr = _base._psr(test, hurdle)
    checks: dict[str, bool] = {
        "asset_registry_d3": bool(registry_audit.get("production_ready")),
        "macro_pit_coverage": float(macro_audit.get("pit_verified_fraction") or 0.0)
        >= config.macro_pit_required_fraction,
        "validation_sample": int(validation.get("months") or 0)
        >= config.minimum_validation_returns,
        "sealed_test_sample": int(test.get("months") or 0) >= config.minimum_test_returns,
        "validation_family_eligible": int(selection.get("eligible_count") or 0) > 0,
        "test_sharpe_threshold": test.get("sharpe") is not None
        and float(test["sharpe"]) >= config.promotion_min_test_sharpe,
        "probabilistic_sharpe": psr is not None and psr >= config.promotion_min_psr,
    }
    if mode == "benchmark_relative":
        checks.update(
            {
                "validation_excess_positive": _number(validation, "annual_excess_return", -1.0) > 0.0,
                "test_excess_positive_report_only": _number(test, "annual_excess_return", -1.0) > 0.0,
                "test_information_ratio_positive_report_only": _number(test, "information_ratio", -99.0) > 0.0,
            }
        )
    return {
        "model_version": mode,
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
        "probabilistic_sharpe_ratio": psr,
        "multiple_trial_sharpe_hurdle": hurdle,
        "declared_trials": config.total_declared_trials,
        "policy_benchmark_test_sharpe": benchmark_test.get("sharpe"),
        "test_is_report_only": True,
        "test_period_pristine_for_v52": False,
        "policy": "failed validation, data or statistical gates cannot be overridden by a high retrospective Sharpe",
    }


def _allocation_payload_v52(selected: Mapping[str, Any]) -> dict[str, Any]:
    weights = np.asarray(selected["current_weights"], dtype=float)
    covariance = np.asarray(
        selected["current_diagnostics"]["covariance"]["covariance"], dtype=float
    )
    _, _, risk = portfolio_risk_contribution_v5(covariance, weights)
    return {
        "weights": {
            asset: float(weights[position]) for position, asset in enumerate(ASSET_ORDER_V5)
        },
        "risk_contribution": {
            asset: float(risk[position]) for position, asset in enumerate(ASSET_ORDER_V5)
        },
        "metadata": copy.deepcopy(selected["current_diagnostics"]),
    }


def _factor_risk_all_versions_v52(payload: dict[str, Any]) -> None:
    audits: dict[str, Any] = {}
    for key in ("strategic_benchmark", "benchmark_relative", "absolute_no_benchmark", "recommended"):
        allocation = (payload.get("allocations") or {}).get(key) or {}
        metadata = allocation.get("metadata") or {}
        covariance = metadata.get("covariance") or {}
        if not covariance:
            continue
        audit = macro_factor_risk_decomposition_v51(
            allocation.get("weights") or {}, payload["asset_order"], covariance
        )
        metadata["factor_risk_contribution"] = copy.deepcopy(audit["rows"])
        metadata["macro_factor_risk_decomposition"] = copy.deepcopy(audit)
        allocation["metadata"] = metadata
        payload["allocations"][key] = allocation
        audits[key] = audit
    payload["macro_factor_risk_audit"] = {
        "version": "5.2",
        "by_model_version": audits,
        "weights_changed": False,
        "backtest_values_changed": False,
    }


def _rehash_v52(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("model_hash", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def build_snapshot_v52(
    macro_rows: Sequence[Mapping[str, Any]],
    price_series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    registry: Mapping[str, AssetSeriesSpecV5] | None = None,
    generated_at: str | None = None,
    config: ResearchConfigV52 | None = None,
) -> dict[str, Any]:
    config = config or ResearchConfigV52()
    config.validate()
    validate_cycle_factor_registry_v5()
    registry = dict(registry or default_asset_registry_v5())
    registry_audit = validate_asset_registry_v5(
        registry, require_production=config.production_mode
    )
    if config.production_mode and registry_audit["status"] != "passed":
        raise ValueError(
            "v52_production_data_gate_failed:" + ",".join(registry_audit["errors"])
        )
    price_months, price_levels, price_audit = _base.monthly_prices_v5(price_series)
    return_months = price_months[1:]
    returns = price_levels[1:] / price_levels[:-1] - 1.0
    safe_macro, macro_audit = _base._pit_safe_macro_rows(macro_rows)
    macro_probabilities = build_macro_cycle_probabilities_v5(
        safe_macro, train_end=config.train_end
    )
    pring = build_pring_market_probabilities_v5(
        return_months, returns, train_end=config.train_end
    )
    cycles = merge_cycle_history_v5(return_months, pring, macro_probabilities)
    if len(cycles) != len(returns):
        raise ValueError("v52_cycle_and_return_history_misaligned")
    innovations, macro_admitted = _base._macro_innovations(cycles)
    view_models, frozen_view_model = _view_models_by_signal_v52(
        returns, cycles, return_months, config
    )
    benchmark = strategic_benchmark_backtest_v52(return_months, returns, config)
    family_results: dict[str, list[dict[str, Any]]] = {}
    selected: dict[str, dict[str, Any]] = {}
    selections: dict[str, dict[str, Any]] = {}
    promotions: dict[str, dict[str, Any]] = {}
    for mode in MODEL_VERSIONS_V52:
        family_results[mode] = [
            _simulate_candidate_v52(
                return_months,
                returns,
                cycles,
                innovations,
                macro_admitted,
                view_models,
                spec,
                config,
                benchmark,
            )
            for spec in candidate_grid_v52(mode)
        ]
        selected[mode], selections[mode] = _select_candidate_v52(
            family_results[mode], mode, config
        )
        promotions[mode] = _promotion_gate_v52(
            selected[mode],
            selections[mode],
            benchmark,
            registry_audit,
            macro_audit,
            mode,
            config,
        )
    relative_allocation = _allocation_payload_v52(selected["benchmark_relative"])
    absolute_allocation = _allocation_payload_v52(selected["absolute_no_benchmark"])
    recommended_key = config.authorized_recommended_mode
    recommended = copy.deepcopy(
        relative_allocation
        if recommended_key == "benchmark_relative"
        else absolute_allocation
    )
    current_covariance = np.asarray(
        recommended["metadata"]["covariance"]["covariance"], dtype=float
    )
    benchmark_vector = np.asarray(config.policy_benchmark_weights, dtype=float)
    _, _, benchmark_risk = portfolio_risk_contribution_v5(
        current_covariance, benchmark_vector
    )
    benchmark_allocation = {
        "weights": {
            asset: float(benchmark_vector[position])
            for position, asset in enumerate(ASSET_ORDER_V5)
        },
        "risk_contribution": {
            asset: float(benchmark_risk[position])
            for position, asset in enumerate(ASSET_ORDER_V5)
        },
        "metadata": {
            "role": "primary_policy_benchmark_not_equal_weight",
            "covariance": copy.deepcopy(recommended["metadata"]["covariance"]),
            "rebalance": "monthly target reset with the same per-asset transaction-cost convention",
        },
    }
    erc = solve_erc_v5(current_covariance)
    risk_budget_payload = recommended["metadata"]["risk_budget"]
    risk_budget_weights = np.asarray(risk_budget_payload["weights"], dtype=float)
    cycle_factor_registry = serialise_cycle_factor_registry_v5()
    payload: dict[str, Any] = {
        "schema_version": "5.2",
        "engine_version": ENGINE_VERSION_V52,
        "generated_at": generated_at or _base._utc_now(),
        "status": "research_only",
        "asset_order": list(ASSET_ORDER_V5),
        "asset_labels": dict(ASSET_LABELS_V5),
        "asset_proxies": {
            asset: asdict(registry[asset]) for asset in ASSET_ORDER_V5
        },
        "benchmark": {
            "id": STRATEGIC_BENCHMARK_ID_V52,
            "name": "60%权益＋15%国债＋15%非黄金商品＋10%黄金",
            "display_order": ["equity", "bond", "commodity", "gold"],
            "internal_asset_order": list(ASSET_ORDER_V5),
            "weights": benchmark_allocation["weights"],
            "equal_weight_is_primary_benchmark": False,
            "rebalance": "monthly",
            "costs": "same asset-level one-way cost convention as both model versions",
        },
        "data_as_of": {
            "market": price_months[-1],
            "macro_available": safe_macro[-1]["month"] if safe_macro else None,
            "macro_complete": safe_macro[-1]["month"] if safe_macro else None,
        },
        "methodology": {
            "formula": MODEL_FORMULA_V52,
            "benchmark_relative": (
                "BL equilibrium prior starts from the declared policy benchmark; the optimizer uses a separate policy/risk-budget dual anchor, then enforces active bands, active share, policy tracking error and turnover"
            ),
            "absolute_no_benchmark": (
                "BL equilibrium prior, objective anchor and constraints use only endogenous risk budgets; changing policy weights cannot change this version"
            ),
            "asset_strength": (
                "four-asset causal 3/6/12m risk-adjusted trend plus BL posterior information-ratio rank; explanation only, no double counting"
            ),
            "training": (
                "training weights use expanding prefix-fitted cycle views; validation and retrospective test use the model frozen at train_end"
            ),
            "selection": "each eight-candidate family is selected on training/validation only; total multiplicity counts all 16 declared trials",
            "test_policy": "historical test is report-only and not pristine after the v5.2 redesign; future paper trading is required for promotion",
            "risk_parity": "strict ERC Newton solution",
            "risk_budget": "Richard-Roncalli constrained risk budget",
            "black_litterman": "joint three-view posterior with full non-diagonal Omega",
            "kondratieff_policy": "display only; zero risk-budget and BL-view contribution",
            "sharpe_policy": "standard arithmetic zero-risk-free Sharpe is reported; no guaranteed return or Sharpe",
        },
        "config": asdict(config),
        "cycle_history": cycles,
        "cycle_view_model": {
            key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in frozen_view_model.items()
            if key != "feature_slices"
        },
        "cycle_factor_registry": cycle_factor_registry,
        "allocations": {
            "current_cycle": cycles[-1],
            "as_of": price_months[-1],
            "strategic_benchmark": benchmark_allocation,
            "benchmark_relative": relative_allocation,
            "absolute_no_benchmark": absolute_allocation,
            "recommended": recommended,
            "recommended_mode": recommended_key,
            "risk_parity": {
                "weights": {
                    asset: float(erc.weights[position])
                    for position, asset in enumerate(ASSET_ORDER_V5)
                },
                "risk_contribution": {
                    asset: float(erc.relative_risk_contribution[position])
                    for position, asset in enumerate(ASSET_ORDER_V5)
                },
                "metadata": erc.to_dict(),
            },
            "macro_risk_budget": {
                "weights": {
                    asset: float(risk_budget_weights[position])
                    for position, asset in enumerate(ASSET_ORDER_V5)
                },
                "risk_contribution": {
                    asset: float(risk_budget_payload["relative_risk_contribution"][position])
                    for position, asset in enumerate(ASSET_ORDER_V5)
                },
                "metadata": copy.deepcopy(risk_budget_payload),
            },
            "robust_bl": copy.deepcopy(absolute_allocation),
        },
        "asset_decisions": {
            "benchmark_relative": copy.deepcopy(
                relative_allocation["metadata"]["asset_strength"]["rows"]
            ),
            "absolute_no_benchmark": copy.deepcopy(
                absolute_allocation["metadata"]["asset_strength"]["rows"]
            ),
        },
        "current_strength_summary": {
            "benchmark_relative": {
                "strongest_asset": relative_allocation["metadata"]["asset_strength"]["strongest_asset"],
                "weakest_asset": relative_allocation["metadata"]["asset_strength"]["weakest_asset"],
            },
            "absolute_no_benchmark": {
                "strongest_asset": absolute_allocation["metadata"]["asset_strength"]["strongest_asset"],
                "weakest_asset": absolute_allocation["metadata"]["asset_strength"]["weakest_asset"],
            },
        },
        "backtest": {
            "sample_splits": {
                "train": {"end": config.train_end, "role": "expanding causal training eligibility"},
                "validation": {"end": config.validation_end, "role": "family-specific candidate selection"},
                "test": {
                    "start": _base._next_month(config.validation_end),
                    "role": "retrospective report only; not pristine after v5.2 redesign",
                },
            },
            "strategies": {
                "strategic_benchmark": benchmark,
                "benchmark_relative": {
                    key: selected["benchmark_relative"][key]
                    for key in ("metrics", "nav", "weights", "returns", "strength_history")
                },
                "absolute_no_benchmark": {
                    key: selected["absolute_no_benchmark"][key]
                    for key in ("metrics", "nav", "weights", "returns", "strength_history")
                },
                "recommended": {
                    key: selected[recommended_key][key]
                    for key in ("metrics", "nav", "weights", "returns", "strength_history")
                },
            },
            "selection_audit": {
                "benchmark_relative": selections["benchmark_relative"],
                "absolute_no_benchmark": selections["absolute_no_benchmark"],
                "recommended_mode": recommended_key,
                "recommended_mode_rule": "explicit investment-policy authorization; never selected from test performance",
                "selection_uses_test": False,
                "total_declared_trials": config.total_declared_trials,
            },
            "comparison_policy": {
                "primary_benchmark": STRATEGIC_BENCHMARK_ID_V52,
                "benchmark_relative_active_return": "benchmark_relative minus strategic_benchmark",
                "absolute_version_policy_benchmark_role": "report_only_not_model_input",
                "equal_weight_role": "not_primary_and_not_used_for_selection",
            },
        },
        "optimization": copy.deepcopy(recommended["metadata"]),
        "quality": {
            "status": "research_only",
            "asset_registry": registry_audit,
            "price_panel": price_audit,
            "macro_point_in_time": macro_audit,
            "promotion_by_version": promotions,
            "promotion_gate": copy.deepcopy(promotions[recommended_key]),
        },
        "limitations": [
            "Local execution proxies are not yet a D3 verified total-return registry.",
            "Macro rows without verified available_time and vintage cannot enter the macro covariance or BL views.",
            "The ex-gold commodity ETF basket has a shorter history and its internal rebalancing cost requires separate production validation.",
            "The v5.2 historical test window was already observable during redesign and is not a pristine new sealed test.",
            "A high historical Sharpe, information ratio or excess return is not guaranteed to persist.",
        ],
    }
    availability = _current_factor_availability(payload)
    payload["cycle_factor_availability"] = availability
    payload["quality"]["cycle_factor_admission"] = {
        "status": "passed" if availability["admitted_cycles"] else "blocked",
        **availability,
    }
    payload = harden_shadow_snapshot_v51(payload)
    payload = apply_truth_gate_v51(payload)
    _factor_risk_all_versions_v52(payload)
    payload["schema_version"] = "5.2"
    payload["engine_version"] = ENGINE_VERSION_V52
    payload["v52_governance"] = {
        "policy_benchmark_order_verified": True,
        "absolute_model_reads_policy_benchmark": False,
        "two_families_selected_independently": True,
        "recommended_mode_uses_test": False,
        "historical_test_pristine": False,
        "future_paper_holdout_required": True,
    }
    return _rehash_v52(payload)


__all__ = [
    "ENGINE_VERSION_V52",
    "MODEL_FORMULA_V52",
    "MODEL_VERSIONS_V52",
    "POLICY_ACTIVE_BANDS_V52",
    "POLICY_BENCHMARK_WEIGHTS_V52",
    "ResearchConfigV52",
    "STRATEGIC_BENCHMARK_ID_V52",
    "asset_strength_v52",
    "build_snapshot_v52",
    "candidate_grid_v52",
    "research_shadow_config_v52",
    "strategic_benchmark_backtest_v52",
]
