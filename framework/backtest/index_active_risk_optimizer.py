"""Causal benchmark-relative optimizer for index-enhancement research.

The module converts an existing point-in-time cross-sectional alpha into a
tradable benchmark-relative portfolio.  It deliberately separates alpha
research from portfolio construction:

* the benchmark is replicated before any active view is applied;
* alpha is residualized against signal-date industry and style exposures;
* causal trailing volatility determines the active-risk budget;
* every industry weight is projected back to its benchmark weight;
* turnover enters the convex target as a quadratic transition penalty;
* the realized next-period return is only consumed after weights are frozen.

The implementation uses NumPy/Pandas only so the formal backtest and the
production snapshot do not depend on a commercial solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ActiveRiskConfig:
    target_tracking_error: float = 0.045
    max_active_weight: float = 0.008
    max_total_weight: float = 0.05
    turnover_penalty: float = 3.0
    residual_ridge: float = 1.0e-4
    volatility_lookback: int = 36
    volatility_min_periods: int = 12
    volatility_floor: float = 0.10
    volatility_cap: float = 0.80
    use_causal_alpha_reliability: bool = False
    reliability_lookback: int = 60
    reliability_prior_strength: float = 24.0


STYLE_COLUMNS = (
    ("size", "total_mv", True),
    ("value", "pb", True),
    ("profitability", "roe", False),
    ("momentum", "mom60", False),
    ("long_momentum", "mom120", False),
    ("crowding", "turnover_rate", True),
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _rank_exposure(values: pd.Series, reverse: bool = False) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    ranked = numeric.rank(pct=True, ascending=not reverse).fillna(0.5)
    return ranked.to_numpy(dtype=float) - 0.5


def add_causal_risk_features(
    panel: pd.DataFrame,
    *,
    lookback: int = 36,
    min_periods: int = 12,
) -> pd.DataFrame:
    """Attach volatility using only returns whose holding period has ended."""
    ordered = panel.sort_values(["ts_code", "trade_date"]).copy()
    prior_return = ordered.groupby("ts_code", sort=False)["label_next_ret"].shift(1)
    ordered["_prior_realized_return"] = prior_return
    ordered["active_risk_volatility"] = (
        ordered.groupby("ts_code", sort=False)["_prior_realized_return"]
        .transform(lambda values: values.rolling(lookback, min_periods=min_periods).std(ddof=1))
        .astype(float)
        * math.sqrt(12.0)
    )
    ordered = ordered.drop(columns=["_prior_realized_return"])
    return ordered.sort_index()


def _bounded_simplex_projection(
    preferred: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    target: float,
) -> np.ndarray:
    """Euclidean projection onto bounds with an exact sum constraint."""
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
    projected = np.clip(preferred - 0.5 * (low + high), lower, upper)
    gap = target - float(projected.sum())
    if abs(gap) > 1.0e-11:
        room = upper - projected if gap > 0 else projected - lower
        room_sum = float(room.sum())
        if room_sum > 0:
            projected = projected + math.copysign(1.0, gap) * room * min(1.0, abs(gap) / room_sum)
    return projected


def _design_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    industry = pd.get_dummies(
        frame["industry_name"].fillna("UNCLASSIFIED").astype(str),
        prefix="industry",
        dtype=float,
    )
    blocks = [industry.to_numpy(dtype=float)]
    names = list(industry.columns)
    for label, column, reverse in STYLE_COLUMNS:
        if column in frame:
            blocks.append(_rank_exposure(frame[column], reverse=reverse).reshape(-1, 1))
            names.append(label)
    return np.column_stack(blocks), names


def _residual_alpha(
    frame: pd.DataFrame,
    score_column: str,
    base: np.ndarray,
    annual_volatility: np.ndarray,
    ridge: float,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    alpha = frame[score_column].rank(pct=True).fillna(0.5).to_numpy(dtype=float) - 0.5
    design, exposure_names = _design_matrix(frame)
    inverse_variance = 1.0 / np.maximum(annual_volatility**2, 1.0e-6)
    regression_weight = np.maximum(base, 1.0e-8) * inverse_variance
    weighted_design = design * regression_weight[:, None]
    gram = design.T @ weighted_design
    penalty = np.eye(gram.shape[0], dtype=float) * ridge
    beta = np.linalg.pinv(gram + penalty, hermitian=True) @ (
        design.T @ (regression_weight * alpha)
    )
    residual = alpha - design @ beta
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    scale = max(1.4826 * mad, 1.0e-4)
    robust_residual = np.tanh((residual - median) / (2.5 * scale))
    active_direction = regression_weight * robust_residual
    return active_direction, design, exposure_names


def optimize_weights(
    frame: pd.DataFrame,
    score_column: str,
    previous_weights: dict[str, float] | None = None,
    config: ActiveRiskConfig | None = None,
    active_risk_multiplier: float = 1.0,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Freeze benchmark-relative target weights before next returns are read."""
    config = config or ActiveRiskConfig()
    g = (
        frame.dropna(subset=[score_column, "index_weight", "ts_code"])
        .drop_duplicates(subset=["ts_code"], keep="last")
        .copy()
    )
    if g.empty:
        return {}, {"status": "empty"}
    index_weight = pd.to_numeric(g["index_weight"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if float(index_weight.sum()) <= 0:
        index_weight = np.ones(len(g), dtype=float)
    base = index_weight / float(index_weight.sum())

    volatility = pd.to_numeric(
        g.get("active_risk_volatility", pd.Series(index=g.index, dtype=float)),
        errors="coerce",
    ).to_numpy(dtype=float)
    valid_volatility = volatility[np.isfinite(volatility) & (volatility > 0)]
    cross_sectional_fallback = (
        float(np.median(valid_volatility)) if len(valid_volatility) else 0.25
    )
    volatility = np.nan_to_num(
        volatility,
        nan=cross_sectional_fallback,
        posinf=config.volatility_cap,
        neginf=config.volatility_floor,
    )
    volatility = np.clip(volatility, config.volatility_floor, config.volatility_cap)

    direction, design, exposure_names = _residual_alpha(
        g,
        score_column,
        base,
        volatility,
        config.residual_ridge,
    )
    direction -= base * float(direction.sum())
    diagonal_te = math.sqrt(max(12.0 * float(np.sum((direction * volatility) ** 2)), 0.0))
    if diagonal_te > 1.0e-12:
        direction *= (
            config.target_tracking_error * float(np.clip(active_risk_multiplier, -1.0, 1.0))
        ) / diagonal_te
    active_lower = -base
    active_upper = np.full(len(g), config.max_active_weight, dtype=float)
    active = np.clip(direction, active_lower, active_upper)
    raw_target = base + active

    codes = g["ts_code"].astype(str).tolist()
    previous_weights = previous_weights or {}
    previous = np.asarray([_safe_float(previous_weights.get(code), 0.0) for code in codes])
    if float(previous.sum()) <= 0:
        previous = base.copy()
    else:
        previous /= float(previous.sum())
    preferred = (
        raw_target + config.turnover_penalty * previous
    ) / (1.0 + config.turnover_penalty)
    upper = np.minimum(
        config.max_total_weight,
        np.maximum(base + config.max_active_weight, base * 4.0),
    )
    upper = np.maximum(upper, base)
    weights = np.zeros(len(g), dtype=float)
    industries = g["industry_name"].fillna("UNCLASSIFIED").astype(str)
    for industry in industries.unique():
        mask = industries.to_numpy() == industry
        weights[mask] = _bounded_simplex_projection(
            preferred[mask],
            np.zeros(int(mask.sum()), dtype=float),
            upper[mask],
            float(base[mask].sum()),
        )
    weights = np.maximum(weights, 0.0)
    weights /= max(float(weights.sum()), 1.0e-12)

    active = weights - base
    style_start = max(0, design.shape[1] - (len(exposure_names) - industries.nunique()))
    style_exposure = design[:, style_start:].T @ active if style_start < design.shape[1] else np.array([])
    industry_deviation = [
        abs(float(active[industries.to_numpy() == industry].sum()))
        for industry in industries.unique()
    ]
    turnover = 0.5 * float(np.abs(weights - previous).sum())
    estimated_te = math.sqrt(max(12.0 * float(np.sum((active * volatility) ** 2)), 0.0))
    diagnostics = {
        "status": "ready",
        "estimated_tracking_error": estimated_te,
        "one_way_turnover": turnover,
        "active_share": 0.5 * float(np.abs(active).sum()),
        "max_active_weight": float(np.max(np.abs(active))),
        "max_industry_deviation": max(industry_deviation, default=0.0),
        "max_style_exposure": float(np.max(np.abs(style_exposure))) if len(style_exposure) else 0.0,
        "causal_alpha_reliability": float(active_risk_multiplier),
        "style_exposures": {
            name: float(value)
            for name, value in zip(exposure_names[-len(style_exposure) :], style_exposure)
        },
        "risk_history_is_causal": True,
        "return_label_used_after_weight_freeze": True,
    }
    return dict(zip(codes, weights.astype(float))), diagnostics


def _causal_alpha_reliability(
    realized_ic: list[float],
    config: ActiveRiskConfig,
) -> float:
    history = np.asarray(realized_ic[-config.reliability_lookback :], dtype=float)
    history = np.clip(history[np.isfinite(history)], -0.30, 0.30)
    if len(history) < 3:
        return 0.0
    mean_ic = float(np.mean(history))
    dispersion = float(np.std(history, ddof=1)) if len(history) > 1 else 0.0
    standard_error = dispersion / math.sqrt(len(history)) + 0.01
    posterior_shrinkage = len(history) / (
        len(history) + max(config.reliability_prior_strength, 1.0)
    )
    return float(np.tanh(posterior_shrinkage * mean_ic / standard_error))


def backtest_active_risk_optimizer(
    panel: pd.DataFrame,
    score_column: str,
    *,
    cost_rate: float,
    config: ActiveRiskConfig | None = None,
    safe_float: Callable[[Any], float] = _safe_float,
) -> tuple[list[float], list[float], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Run a monthly benchmark-relative simulation and retain solver evidence."""
    config = config or ActiveRiskConfig()
    enriched = add_causal_risk_features(
        panel,
        lookback=config.volatility_lookback,
        min_periods=config.volatility_min_periods,
    )
    returns: list[float] = []
    benchmark_returns: list[float] = []
    nav_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    previous_weights: dict[str, float] = {}
    realized_alpha_ic: list[float] = []
    nav = 1.0
    for date, raw in enriched.groupby("trade_date", sort=True):
        g = (
            raw.dropna(subset=[score_column, "label_next_ret"])
            .drop_duplicates(subset=["ts_code"], keep="last")
            .copy()
        )
        if g.empty:
            continue
        index_weight = pd.to_numeric(g["index_weight"], errors="coerce").fillna(0.0)
        if float(index_weight.sum()) <= 0:
            index_weight = pd.Series(1.0, index=g.index)
        base = index_weight / float(index_weight.sum())
        reliability = (
            _causal_alpha_reliability(realized_alpha_ic, config)
            if config.use_causal_alpha_reliability
            else 1.0
        )
        weights, evidence = optimize_weights(
            g, score_column, previous_weights, config, reliability
        )
        if not weights:
            continue
        realized = {
            str(code): safe_float(ret)
            for code, ret in zip(g["ts_code"], g["label_next_ret"])
        }
        gross_return = sum(weight * realized.get(code, 0.0) for code, weight in weights.items())
        benchmark_return = float(
            sum(float(weight) * realized.get(str(code), 0.0) for code, weight in zip(g["ts_code"], base))
        )
        all_codes = set(weights) | set(previous_weights)
        two_way_turnover = sum(
            abs(weights.get(code, 0.0) - previous_weights.get(code, 0.0))
            for code in all_codes
        )
        net_return = gross_return - two_way_turnover * cost_rate
        nav *= 1.0 + net_return
        returns.append(net_return)
        benchmark_returns.append(benchmark_return)
        nav_rows.append(
            {
                "trade_date": date,
                "nav": nav,
                "period_return": net_return,
                "benchmark_return": benchmark_return,
                "excess_return": net_return - benchmark_return,
            }
        )
        evidence = {
            **evidence,
            "trade_date": date,
            "cost_rate": cost_rate,
            "two_way_turnover": two_way_turnover,
            "transaction_cost": two_way_turnover * cost_rate,
        }
        diagnostics.append(evidence)
        meta = g.set_index(g["ts_code"].astype(str))
        ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
        for rank_no, (code, weight) in enumerate(ranked, 1):
            row = meta.loc[code]
            signal_rows.append(
                {
                    "trade_date": date,
                    "ts_code": code,
                    "industry_name": str(row["industry_name"]),
                    "score": float(row[score_column]),
                    "rank_no": rank_no,
                    "target_weight": float(weight),
                }
            )
        current_ic = g[score_column].rank().corr(g["label_next_ret"].rank())
        if pd.notna(current_ic):
            realized_alpha_ic.append(float(current_ic))
        previous_weights = weights
    summary = {
        "model": "benchmark_relative_active_risk_optimizer",
        "score_column": score_column,
        "selection_uses_test": False,
        "research_status": "post_test_diagnostic_challenger",
        "config": config.__dict__,
        "causal_reliability_history": realized_alpha_ic,
        "periods": len(diagnostics),
        "average_estimated_tracking_error": float(
            np.mean([row["estimated_tracking_error"] for row in diagnostics])
        )
        if diagnostics
        else 0.0,
        "average_one_way_turnover": float(
            np.mean([row["one_way_turnover"] for row in diagnostics])
        )
        if diagnostics
        else 0.0,
        "max_industry_deviation": max(
            (row["max_industry_deviation"] for row in diagnostics), default=0.0
        ),
        "max_style_exposure": max(
            (row["max_style_exposure"] for row in diagnostics), default=0.0
        ),
        "monthly_solver_evidence": diagnostics,
    }
    return returns, benchmark_returns, nav_rows, signal_rows, summary
