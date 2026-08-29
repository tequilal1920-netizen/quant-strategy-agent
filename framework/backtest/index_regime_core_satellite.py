"""Causal Bayesian core-satellite index-enhancement research engine.

The engine keeps the benchmark fully invested and applies only a bounded
active overlay.  Factor weights are learned from information coefficients
whose holding periods have already ended.  The sealed test interval is never
used by this module to choose a factor, risk budget, or model configuration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from framework.backtest.index_active_risk_optimizer import add_causal_risk_features


SLOW_FACTORS = (
    "quality_value_low_crowding_v8",
    "fundamental_quality_v4",
    "domain_quality_neutral_v9",
    "domain_value_neutral_v9",
    "factor_domain_agent_v9",
)

FAST_FACTORS = (
    "domain_money_neutral_v9",
    "domain_technical_neutral_v9",
    "trend_quality_v4",
    "kline_context_agent_v8",
    "kline_executable_skill_v11",
)

STYLE_COLUMNS = (
    ("规模", "total_mv", True),
    ("估值", "pb", True),
    ("盈利", "roe", False),
    ("中期动量", "mom60", False),
    ("长期动量", "mom120", False),
    ("拥挤度", "turnover_rate", True),
)


@dataclass(frozen=True)
class BayesianAlphaConfig:
    horizons: tuple[int, ...] = (6, 12, 24, 48)
    horizon_weights: tuple[float, ...] = (0.35, 0.30, 0.22, 0.13)
    minimum_history: int = 12
    prior_strength: float = 8.0
    covariance_lookback: int = 36
    covariance_shrinkage: float = 0.55
    factor_transition_penalty: float = 2.5


@dataclass(frozen=True)
class CoreSatelliteConfig:
    target_tracking_error: float = 0.035
    max_active_weight: float = 0.006
    max_total_weight: float = 0.05
    max_industry_deviation: float = 0.012
    turnover_penalty: float = 5.0
    residual_ridge: float = 1.0e-4
    volatility_lookback: int = 36
    volatility_min_periods: int = 12
    volatility_floor: float = 0.10
    volatility_cap: float = 0.80
    risk_half_life: float = 12.0
    covariance_shrinkage: float = 0.55
    newey_west_lags: int = 1


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _logistic(value: float) -> float:
    clipped = float(np.clip(value, -30.0, 30.0))
    return 1.0 / (1.0 + math.exp(-clipped))


def _weighted_mean(values: np.ndarray, half_life: float) -> tuple[float, float, float]:
    if not len(values):
        return 0.0, 0.0, 0.0
    ages = np.arange(len(values) - 1, -1, -1, dtype=float)
    weights = np.power(0.5, ages / max(half_life, 1.0))
    weights /= max(float(weights.sum()), 1.0e-12)
    mean = float(weights @ values)
    variance = float(weights @ np.square(values - mean))
    effective_n = float(1.0 / max(float(np.square(weights).sum()), 1.0e-12))
    return mean, variance, effective_n


def _posterior_summary(
    history: np.ndarray,
    config: BayesianAlphaConfig,
) -> dict[str, float]:
    finite = np.clip(history[np.isfinite(history)], -0.30, 0.30)
    if len(finite) < config.minimum_history:
        return {
            "posterior_mean": 0.0,
            "posterior_se": 1.0,
            "positive_ratio": 0.5,
            "evidence_z": 0.0,
        }
    horizon_means: list[float] = []
    horizon_variances: list[float] = []
    horizon_effective_n: list[float] = []
    used_weights: list[float] = []
    for horizon, horizon_weight in zip(config.horizons, config.horizon_weights):
        sample = finite[-min(horizon, len(finite)) :]
        mean, variance, effective_n = _weighted_mean(
            sample, max(float(horizon) / 3.0, 2.0)
        )
        horizon_means.append(mean)
        horizon_variances.append(max(variance, 1.0e-5))
        horizon_effective_n.append(effective_n)
        used_weights.append(float(horizon_weight))
    blend = np.asarray(used_weights, dtype=float)
    blend /= max(float(blend.sum()), 1.0e-12)
    sample_mean = float(blend @ np.asarray(horizon_means, dtype=float))
    sample_variance = float(blend @ np.asarray(horizon_variances, dtype=float))
    effective_n = float(blend @ np.asarray(horizon_effective_n, dtype=float))
    shrinkage = effective_n / (effective_n + max(config.prior_strength, 1.0))
    posterior_mean = shrinkage * sample_mean
    posterior_se = math.sqrt(
        sample_variance / max(effective_n + config.prior_strength, 1.0)
    )
    recent = finite[-min(24, len(finite)) :]
    positive_ratio = float(np.mean(recent > 0.0)) if len(recent) else 0.5
    evidence_z = posterior_mean / max(posterior_se, 0.005)
    return {
        "posterior_mean": posterior_mean,
        "posterior_se": posterior_se,
        "positive_ratio": positive_ratio,
        "evidence_z": evidence_z,
    }


def _decorrelated_positive_weights(
    histories: dict[str, np.ndarray],
    summaries: dict[str, dict[str, float]],
    config: BayesianAlphaConfig,
) -> dict[str, float]:
    names = [
        name
        for name, summary in summaries.items()
        if summary["posterior_mean"] > 0.0 and len(histories[name]) >= config.minimum_history
    ]
    if not names:
        return {}
    length = min(config.covariance_lookback, min(len(histories[name]) for name in names))
    matrix = np.column_stack([histories[name][-length:] for name in names]).astype(float)
    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    if matrix.shape[0] > 1:
        covariance = np.cov(matrix, rowvar=False, ddof=1)
    else:
        covariance = np.eye(len(names), dtype=float)
    covariance = np.atleast_2d(np.asarray(covariance, dtype=float))
    diagonal = np.diag(np.maximum(np.diag(covariance), 1.0e-5))
    shrink = float(np.clip(config.covariance_shrinkage, 0.0, 1.0))
    covariance = (1.0 - shrink) * covariance + shrink * diagonal
    covariance += np.eye(len(names), dtype=float) * 1.0e-5
    posterior = np.asarray([summaries[name]["posterior_mean"] for name in names])
    stability = np.asarray(
        [0.25 + 0.75 * summaries[name]["positive_ratio"] for name in names]
    )
    try:
        raw = np.linalg.solve(covariance, posterior) * stability
    except np.linalg.LinAlgError:
        raw = posterior / np.maximum(np.diag(covariance), 1.0e-5) * stability
    raw = np.maximum(np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0), 0.0)
    if float(raw.sum()) <= 0.0:
        return {}
    raw /= float(raw.sum())
    return {name: float(weight) for name, weight in zip(names, raw)}


def _regime_family_budget(frame: pd.DataFrame) -> tuple[float, float, dict[str, float]]:
    trend = _safe_float(pd.to_numeric(frame.get("mom120"), errors="coerce").median())
    medium = pd.to_numeric(frame.get("mom60"), errors="coerce")
    breadth = _safe_float((medium > 0.0).mean(), 0.5)
    fast_share = 0.25 + 0.50 * _logistic(5.0 * trend + 3.0 * (breadth - 0.5))
    return 1.0 - fast_share, fast_share, {
        "中长期趋势": trend,
        "上涨广度": breadth,
        "稳健因子预算": 1.0 - fast_share,
        "快速因子预算": fast_share,
    }


def add_bayesian_regime_alpha(
    panel: pd.DataFrame,
    config: BayesianAlphaConfig | None = None,
    *,
    score_column: str = "bayesian_regime_alpha_v15",
    confidence_column: str = "bayesian_active_confidence_v15",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Create a multi-horizon posterior alpha using matured labels only."""
    config = config or BayesianAlphaConfig()
    enriched = panel.copy()
    factors = [name for name in (*SLOW_FACTORS, *FAST_FACTORS) if name in enriched]
    grouped = [
        (str(date), np.asarray(index, dtype=int))
        for date, index in enriched.groupby("trade_date", sort=True).groups.items()
    ]
    monthly_ic: dict[str, list[float]] = {name: [] for name in factors}
    for _, index in grouped:
        frame = enriched.loc[index]
        label_rank = frame["label_next_ret"].rank()
        for factor in factors:
            value = frame[factor].rank().corr(label_rank)
            monthly_ic[factor].append(float(value) if pd.notna(value) else 0.0)

    score = pd.Series(0.5, index=enriched.index, dtype=float)
    confidence = pd.Series(0.0, index=enriched.index, dtype=float)
    previous_factor_weights: dict[str, float] = {}
    monthly_diagnostics: list[dict[str, Any]] = []
    for position, (date, index) in enumerate(grouped):
        frame = enriched.loc[index]
        histories = {
            factor: np.asarray(monthly_ic[factor][:position], dtype=float)
            for factor in factors
        }
        summaries = {
            factor: _posterior_summary(history, config)
            for factor, history in histories.items()
        }
        raw_weights = _decorrelated_positive_weights(histories, summaries, config)
        slow_budget, fast_budget, regime = _regime_family_budget(frame)
        family_adjusted: dict[str, float] = {}
        for family, family_budget in ((SLOW_FACTORS, slow_budget), (FAST_FACTORS, fast_budget)):
            available = {name: raw_weights.get(name, 0.0) for name in family}
            total = sum(available.values())
            if total > 0.0:
                for name, value in available.items():
                    if value > 0.0:
                        family_adjusted[name] = family_budget * value / total
        if family_adjusted:
            current_total = sum(family_adjusted.values())
            family_adjusted = {
                name: value / current_total for name, value in family_adjusted.items()
            }
            transition = max(config.factor_transition_penalty, 0.0)
            names = set(family_adjusted) | set(previous_factor_weights)
            smoothed = {
                name: (
                    family_adjusted.get(name, 0.0)
                    + transition * previous_factor_weights.get(name, 0.0)
                )
                / (1.0 + transition)
                for name in names
            }
            total = sum(smoothed.values())
            factor_weights = {
                name: value / total for name, value in smoothed.items() if value > 0.0
            }
        else:
            factor_weights = {}

        if factor_weights:
            centered = pd.Series(0.0, index=index, dtype=float)
            evidence = 0.0
            for factor, weight in factor_weights.items():
                centered += weight * (frame[factor].rank(pct=True).fillna(0.5) - 0.5)
                evidence += weight * max(summaries[factor]["evidence_z"], 0.0)
            score.loc[index] = centered.rank(pct=True).fillna(0.5)
            active_confidence = 1.0 - math.exp(-max(evidence, 0.0) / 2.0)
            confidence.loc[index] = float(np.clip(active_confidence, 0.0, 1.0))
            previous_factor_weights = factor_weights
        else:
            active_confidence = 0.0

        monthly_diagnostics.append(
            {
                "trade_date": date,
                "factor_weights": factor_weights,
                "active_confidence": active_confidence,
                "regime": regime,
                "posterior": summaries,
                "uses_current_label": False,
            }
        )

    enriched[score_column] = score.fillna(0.5)
    enriched[confidence_column] = confidence.fillna(0.0)
    return enriched, {
        "model": "multi_horizon_empirical_bayes_factor_allocator",
        "score_column": score_column,
        "confidence_column": confidence_column,
        "factors": factors,
        "config": config.__dict__,
        "causal": True,
        "selection_uses_test": False,
        "monthly_diagnostics": monthly_diagnostics,
        "latest_factor_weights": (
            monthly_diagnostics[-1]["factor_weights"] if monthly_diagnostics else {}
        ),
        "latest_confidence": (
            monthly_diagnostics[-1]["active_confidence"] if monthly_diagnostics else 0.0
        ),
    }


def _rank_exposure(values: pd.Series, reverse: bool = False) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    return (
        numeric.rank(pct=True, ascending=not reverse).fillna(0.5).to_numpy(dtype=float)
        - 0.5
    )


def _style_design(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    columns: list[np.ndarray] = []
    labels: list[str] = []
    for label, column, reverse in STYLE_COLUMNS:
        if column in frame:
            columns.append(_rank_exposure(frame[column], reverse=reverse))
            labels.append(label)
    if not columns:
        return np.empty((len(frame), 0), dtype=float), []
    return np.column_stack(columns), labels


def _bounded_projection(
    preferred: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    target: float,
) -> np.ndarray:
    preferred = np.asarray(preferred, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.maximum(np.asarray(upper, dtype=float), lower)
    target = float(np.clip(target, lower.sum(), upper.sum()))
    if not len(preferred):
        return preferred
    low = float(np.min(preferred - upper)) - 1.0
    high = float(np.max(preferred - lower)) + 1.0
    for _ in range(80):
        level = 0.5 * (low + high)
        projected = np.clip(preferred - level, lower, upper)
        if float(projected.sum()) > target:
            low = level
        else:
            high = level
    return np.clip(preferred - 0.5 * (low + high), lower, upper)


def _risk_state(
    matured_returns: list[dict[str, float]],
    codes: list[str],
    annual_volatility: np.ndarray,
    config: CoreSatelliteConfig,
) -> dict[str, Any]:
    history = matured_returns[-config.volatility_lookback :]
    if len(history) < config.volatility_min_periods:
        return {
            "method": "年化对角回退",
            "matrix": np.empty((0, len(codes))),
            "weights": np.empty(0),
            "diagonal_variance": np.maximum(annual_volatility, 1.0e-4) ** 2,
            "observations": len(history),
            "causal": True,
        }
    matrix = pd.DataFrame(history).reindex(columns=codes).fillna(0.0).to_numpy(dtype=float)
    matrix -= matrix.mean(axis=1, keepdims=True)
    ages = np.arange(len(matrix) - 1, -1, -1, dtype=float)
    weights = np.power(0.5, ages / max(config.risk_half_life, 1.0))
    weights /= max(float(weights.sum()), 1.0e-12)
    return {
        "method": "EWMA低秩主动风险",
        "matrix": matrix,
        "weights": weights,
        "diagonal_variance": np.maximum(annual_volatility, 1.0e-4) ** 2,
        "observations": len(history),
        "causal": True,
    }


def _tracking_error(
    active: np.ndarray,
    risk_state: dict[str, Any],
    config: CoreSatelliteConfig,
) -> float:
    active = np.asarray(active, dtype=float)
    diagonal_variance = np.asarray(risk_state["diagonal_variance"], dtype=float)
    diagonal_risk = float(np.sum(np.square(active) * diagonal_variance))
    matrix = np.asarray(risk_state["matrix"], dtype=float)
    weights = np.asarray(risk_state["weights"], dtype=float)
    historical_risk = diagonal_risk
    if len(matrix):
        active_returns = matrix @ active
        mean = float(weights @ active_returns)
        centered = active_returns - mean
        historical_risk = 12.0 * float(weights @ np.square(centered))
        if config.newey_west_lags >= 1 and len(centered) > 1:
            lag_covariance = float(
                np.sum(weights[1:] * centered[1:] * centered[:-1])
            )
            historical_risk += 12.0 * lag_covariance
    shrink = float(np.clip(config.covariance_shrinkage, 0.0, 1.0))
    variance = shrink * diagonal_risk + (1.0 - shrink) * max(historical_risk, 0.0)
    return math.sqrt(max(variance, 0.0))


def optimize_core_satellite_weights(
    frame: pd.DataFrame,
    score_column: str,
    confidence_column: str,
    previous_weights: dict[str, float] | None = None,
    config: CoreSatelliteConfig | None = None,
    risk_state: dict[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    config = config or CoreSatelliteConfig()
    g = (
        frame.dropna(subset=[score_column, "index_weight", "ts_code"])
        .drop_duplicates(subset=["ts_code"], keep="last")
        .copy()
    )
    if g.empty:
        return {}, {"status": "empty"}
    index_weight = pd.to_numeric(g["index_weight"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if float(index_weight.sum()) <= 0.0:
        index_weight = np.ones(len(g), dtype=float)
    base = index_weight / float(index_weight.sum())
    annual_volatility = pd.to_numeric(
        g.get("active_risk_volatility", pd.Series(index=g.index, dtype=float)),
        errors="coerce",
    ).to_numpy(dtype=float)
    valid = annual_volatility[np.isfinite(annual_volatility) & (annual_volatility > 0.0)]
    fallback = float(np.median(valid)) if len(valid) else 0.25
    annual_volatility = np.clip(
        np.nan_to_num(
            annual_volatility,
            nan=fallback,
            posinf=config.volatility_cap,
            neginf=config.volatility_floor,
        ),
        config.volatility_floor,
        config.volatility_cap,
    )
    if risk_state is None:
        risk_state = {
            "method": "年化对角回退",
            "matrix": np.empty((0, len(g))),
            "weights": np.empty(0),
            "diagonal_variance": annual_volatility**2,
            "observations": 0,
            "causal": True,
        }

    alpha = g[score_column].rank(pct=True).fillna(0.5).to_numpy(dtype=float) - 0.5
    design, style_labels = _style_design(g)
    inverse_variance = 1.0 / np.maximum(annual_volatility**2, 1.0e-6)
    regression_weight = np.maximum(base, 1.0e-8) * inverse_variance
    if design.shape[1]:
        gram = design.T @ (design * regression_weight[:, None])
        beta = np.linalg.pinv(
            gram + np.eye(gram.shape[0]) * config.residual_ridge,
            hermitian=True,
        ) @ (design.T @ (regression_weight * alpha))
        alpha = alpha - design @ beta
    median = float(np.median(alpha))
    mad = float(np.median(np.abs(alpha - median)))
    robust_alpha = np.tanh((alpha - median) / max(2.5 * 1.4826 * mad, 1.0e-4))
    direction = inverse_variance * robust_alpha
    direction -= base * float(direction.sum())
    confidence = float(
        np.clip(
            pd.to_numeric(g[confidence_column], errors="coerce").fillna(0.0).median(),
            0.0,
            1.0,
        )
    )
    target_te = config.target_tracking_error * confidence
    pre_scale_te = _tracking_error(direction, risk_state, config)
    if pre_scale_te > 1.0e-12:
        direction *= target_te / pre_scale_te
    direction = np.clip(direction, -base, config.max_active_weight)
    raw_target = base + direction

    codes = g["ts_code"].astype(str).tolist()
    previous_weights = previous_weights or {}
    previous = np.asarray([_safe_float(previous_weights.get(code)) for code in codes])
    if float(previous.sum()) <= 0.0:
        previous = base.copy()
    else:
        previous /= float(previous.sum())
    preferred = (raw_target + config.turnover_penalty * previous) / (
        1.0 + config.turnover_penalty
    )
    lower = np.maximum(0.0, base - config.max_active_weight)
    upper = np.minimum(
        config.max_total_weight,
        np.maximum(base + config.max_active_weight, base * 3.0),
    )
    upper = np.maximum(upper, lower)

    industries = g["industry_name"].fillna("未分类").astype(str).to_numpy()
    unique_industries = list(dict.fromkeys(industries.tolist()))
    preferred_industry = np.asarray(
        [float(preferred[industries == industry].sum()) for industry in unique_industries]
    )
    base_industry = np.asarray(
        [float(base[industries == industry].sum()) for industry in unique_industries]
    )
    industry_lower = np.maximum(0.0, base_industry - config.max_industry_deviation)
    industry_upper = np.minimum(1.0, base_industry + config.max_industry_deviation)
    industry_targets = _bounded_projection(
        preferred_industry,
        industry_lower,
        industry_upper,
        1.0,
    )
    weights = np.zeros(len(g), dtype=float)
    for industry, target in zip(unique_industries, industry_targets):
        mask = industries == industry
        weights[mask] = _bounded_projection(
            preferred[mask], lower[mask], upper[mask], float(target)
        )
    weights = np.maximum(weights, 0.0)
    weights /= max(float(weights.sum()), 1.0e-12)
    active = weights - base
    estimated_te = _tracking_error(active, risk_state, config)
    if estimated_te > target_te > 0.0:
        active *= target_te / estimated_te
        weights = base + active
        weights /= max(float(weights.sum()), 1.0e-12)
        active = weights - base
        estimated_te = _tracking_error(active, risk_state, config)
    if target_te <= 1.0e-12:
        weights = base.copy()
        active = np.zeros_like(base)
        estimated_te = 0.0

    industry_deviations = {
        industry: float(active[industries == industry].sum())
        for industry in unique_industries
    }
    style_exposures = (
        {label: float(value) for label, value in zip(style_labels, design.T @ active)}
        if design.shape[1]
        else {}
    )
    turnover = 0.5 * float(np.abs(weights - previous).sum())
    return dict(zip(codes, weights.astype(float))), {
        "status": "ready",
        "benchmark_core_weight": 1.0 - 0.5 * float(np.abs(active).sum()),
        "active_share": 0.5 * float(np.abs(active).sum()),
        "alpha_confidence": confidence,
        "target_tracking_error": target_te,
        "estimated_tracking_error": estimated_te,
        "pre_scale_tracking_error": pre_scale_te,
        "one_way_turnover": turnover,
        "max_active_weight": float(np.max(np.abs(active))),
        "industry_deviations": industry_deviations,
        "max_industry_deviation": max(
            (abs(value) for value in industry_deviations.values()), default=0.0
        ),
        "style_exposures": style_exposures,
        "max_style_exposure": max(
            (abs(value) for value in style_exposures.values()), default=0.0
        ),
        "risk_model": {
            "method": risk_state["method"],
            "observations": risk_state["observations"],
            "causal": risk_state["causal"],
        },
        "fully_invested": True,
        "absolute_market_timing": False,
        "return_label_used_after_weight_freeze": True,
    }


def backtest_core_satellite(
    panel: pd.DataFrame,
    score_column: str,
    confidence_column: str,
    *,
    cost_rate: float,
    config: CoreSatelliteConfig | None = None,
    safe_float: Callable[[Any], float] = _safe_float,
) -> tuple[list[float], list[float], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    config = config or CoreSatelliteConfig()
    enriched = add_causal_risk_features(
        panel,
        lookback=config.volatility_lookback,
        min_periods=config.volatility_min_periods,
    )
    returns: list[float] = []
    benchmark_returns: list[float] = []
    nav_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    monthly_evidence: list[dict[str, Any]] = []
    previous_weights: dict[str, float] = {}
    matured_returns: list[dict[str, float]] = []
    nav = 1.0
    benchmark_nav = 1.0
    for date, raw in enriched.groupby("trade_date", sort=True):
        frame = (
            raw.dropna(subset=[score_column, confidence_column, "label_next_ret"])
            .drop_duplicates(subset=["ts_code"], keep="last")
            .copy()
        )
        if frame.empty:
            continue
        index_weight = pd.to_numeric(frame["index_weight"], errors="coerce").fillna(0.0)
        if float(index_weight.sum()) <= 0.0:
            index_weight = pd.Series(1.0, index=frame.index)
        base = index_weight / float(index_weight.sum())
        codes = frame["ts_code"].astype(str).tolist()
        volatility = pd.to_numeric(
            frame["active_risk_volatility"], errors="coerce"
        ).fillna(config.volatility_floor).clip(
            config.volatility_floor, config.volatility_cap
        ).to_numpy(dtype=float)
        risk_state = _risk_state(matured_returns, codes, volatility, config)
        weights, evidence = optimize_core_satellite_weights(
            frame,
            score_column,
            confidence_column,
            previous_weights,
            config,
            risk_state,
        )
        if not weights:
            continue
        realized = {
            str(code): safe_float(value)
            for code, value in zip(frame["ts_code"], frame["label_next_ret"])
        }
        gross_return = sum(
            weight * realized.get(code, 0.0) for code, weight in weights.items()
        )
        benchmark_return = float(
            sum(
                float(weight) * realized.get(str(code), 0.0)
                for code, weight in zip(frame["ts_code"], base)
            )
        )
        all_codes = set(weights) | set(previous_weights)
        two_way_turnover = sum(
            abs(weights.get(code, 0.0) - previous_weights.get(code, 0.0))
            for code in all_codes
        )
        transaction_cost = two_way_turnover * cost_rate
        net_return = gross_return - transaction_cost
        nav *= 1.0 + net_return
        benchmark_nav *= 1.0 + benchmark_return
        excess_return = net_return - benchmark_return
        returns.append(net_return)
        benchmark_returns.append(benchmark_return)
        nav_rows.append(
            {
                "trade_date": str(date),
                "nav": nav,
                "benchmark_nav": benchmark_nav,
                "relative_nav": nav / benchmark_nav if benchmark_nav else 1.0,
                "period_return": net_return,
                "gross_return": gross_return,
                "benchmark_return": benchmark_return,
                "excess_return": excess_return,
                "two_way_turnover": two_way_turnover,
                "transaction_cost": transaction_cost,
            }
        )
        evidence = {
            **evidence,
            "trade_date": str(date),
            "two_way_turnover": two_way_turnover,
            "transaction_cost": transaction_cost,
            "gross_return": gross_return,
            "benchmark_return": benchmark_return,
            "net_return": net_return,
            "excess_return": excess_return,
        }
        monthly_evidence.append(evidence)
        meta = frame.assign(_code=frame["ts_code"].astype(str)).set_index("_code")
        for rank_no, (code, weight) in enumerate(
            sorted(weights.items(), key=lambda item: item[1], reverse=True), 1
        ):
            row = meta.loc[code]
            signal_rows.append(
                {
                    "trade_date": str(date),
                    "ts_code": code,
                    "industry_name": str(row["industry_name"]),
                    "score": float(row[score_column]),
                    "rank_no": rank_no,
                    "target_weight": float(weight),
                }
            )
        matured_returns.append(realized)
        previous_weights = weights

    return returns, benchmark_returns, nav_rows, signal_rows, {
        "model": "bayesian_regime_core_satellite_index_enhancement",
        "score_column": score_column,
        "confidence_column": confidence_column,
        "research_status": "post_test_diagnostic_candidate",
        "promotion_eligible": False,
        "selection_uses_test": False,
        "config": config.__dict__,
        "periods": len(nav_rows),
        "average_tracking_error": float(
            np.mean([row["estimated_tracking_error"] for row in monthly_evidence])
        )
        if monthly_evidence
        else 0.0,
        "average_one_way_turnover": float(
            np.mean([row["one_way_turnover"] for row in monthly_evidence])
        )
        if monthly_evidence
        else 0.0,
        "average_active_share": float(
            np.mean([row["active_share"] for row in monthly_evidence])
        )
        if monthly_evidence
        else 0.0,
        "average_alpha_confidence": float(
            np.mean([row["alpha_confidence"] for row in monthly_evidence])
        )
        if monthly_evidence
        else 0.0,
        "max_industry_deviation": max(
            (row["max_industry_deviation"] for row in monthly_evidence), default=0.0
        ),
        "max_style_exposure": max(
            (row["max_style_exposure"] for row in monthly_evidence), default=0.0
        ),
        "monthly_evidence": monthly_evidence,
    }
