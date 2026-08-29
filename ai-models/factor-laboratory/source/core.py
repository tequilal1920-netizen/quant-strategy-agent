"""Production-oriented numerical factor laboratory worker.

The worker is deliberately isolated from Flask. It reads an immutable SQLite
research warehouse, trains on chronological train/validation partitions, opens
the test partition once, and writes one auditable JSON result. It never reads
credentials or calls external data providers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_metrics import (  # noqa: E402
    annualized_sharpe,
    annualized_volatility,
    automatic_hac_lag,
    effective_observations,
    hac_information_ratio,
)


ENGINE_VERSION = "factor-lab/3.6.6-domain-timing-conservative-residual-guard"
FEATURES = [
    "ret_1", "ret_5", "ret_20", "ret_60", "vol_20", "down_vol_20",
    "price_pos_60", "volume_z_20", "amihud_20", "turnover", "volume_ratio",
    "value_ep", "value_bp", "value_sp", "dividend", "log_mv",
    "moneyflow", "large_flow", "extreme_flow", "range_1", "gap_1",
    "quality_roe", "quality_roa", "quality_gross_margin",
    "quality_asset_turn", "quality_low_leverage",
    "growth_revenue", "growth_operating_profit", "growth_net_profit",
]
LEGACY_FEATURES = FEATURES[:21]
DOMAINS = {
    "price": ["ret_1", "ret_5", "ret_20", "ret_60", "price_pos_60", "range_1", "gap_1"],
    "risk": ["vol_20", "down_vol_20"],
    "liquidity": ["volume_z_20", "amihud_20", "turnover", "volume_ratio"],
    "valuation": ["value_ep", "value_bp", "value_sp", "dividend", "log_mv"],
    "flow": ["moneyflow", "large_flow", "extreme_flow"],
    "quality": ["quality_roe", "quality_roa", "quality_gross_margin", "quality_asset_turn", "quality_low_leverage"],
    "growth": ["growth_revenue", "growth_operating_profit", "growth_net_profit"],
}
_TRADING_DATE_POSITION: dict[str, int] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def progress(path: Path | None, stage: str, pct: float, message: str, **extra: Any) -> None:
    if not path:
        return
    bounded_pct = max(0.0, min(1.0, float(pct)))
    payload = {"stage": stage, "progress": round(bounded_pct, 4), "message": message, "updated_at": now_iso()}
    payload.update(extra)
    atomic_json(path, payload)


def rankdata(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    if len(values) > 1:
        ranks /= len(values) - 1
    return ranks


def safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 8:
        return 0.0
    aa, bb = a[mask], b[mask]
    if np.std(aa) < 1e-12 or np.std(bb) < 1e-12:
        return 0.0
    return finite(np.corrcoef(aa, bb)[0, 1])


def max_drawdown(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    nav = np.cumprod(1 + np.nan_to_num(returns, nan=0.0))
    peak = np.maximum.accumulate(nav)
    return finite(np.min(nav / np.maximum(peak, 1e-12) - 1))


def _weighted_volatility(values: list[float], span: int) -> float:
    """Exponentially weighted volatility without using the current return."""

    if len(values) < 2:
        return 0.0
    sample = np.asarray(values[-max(2, span):], dtype=float)
    decay = 2.0 / (max(2, span) + 1.0)
    weights = np.power(1.0 - decay, np.arange(len(sample) - 1, -1, -1))
    weights /= max(float(weights.sum()), 1e-12)
    mean = float(np.sum(weights * sample))
    variance = float(np.sum(weights * np.square(sample - mean)))
    return math.sqrt(max(variance, 0.0))


def causal_volatility_scale(
    prior_unscaled_returns: list[float],
    periods_per_year: float,
    previous_scale: float,
    policy: dict[str, Any] | None,
) -> float:
    """Return a causal, long-only exposure multiplier for a factor book.

    The scale is based exclusively on returns that were observable before the
    current rebalance.  A fast and a slow estimate are combined by taking the
    larger risk estimate, then exposure is restored more slowly than it is
    reduced.  This prevents a single quiet window from creating hidden
    leverage and keeps the overlay auditable.
    """

    if not policy:
        return 1.0
    minimum_history = int(policy.get("minimum_history", 8))
    if len(prior_unscaled_returns) < minimum_history:
        return 1.0
    fast_window = int(policy.get("fast_window", 8))
    slow_window = int(policy.get("slow_window", 24))
    annualizer = math.sqrt(max(periods_per_year, 1.0))
    fast = _weighted_volatility(prior_unscaled_returns, fast_window) * annualizer
    slow = _weighted_volatility(prior_unscaled_returns, slow_window) * annualizer
    forecast = max(fast, slow, 1e-8)
    target = max(float(policy.get("target_volatility", 0.18)), 1e-6)
    floor = min(max(float(policy.get("minimum_scale", 0.25)), 0.0), 1.0)
    raw_scale = min(1.0, max(floor, target / forecast))
    if raw_scale < previous_scale:
        adjustment = float(policy.get("risk_reduction_speed", 0.70))
    else:
        adjustment = float(policy.get("risk_restoration_speed", 0.20))
    adjustment = min(max(adjustment, 0.0), 1.0)
    return finite(previous_scale + adjustment * (raw_scale - previous_scale), 1.0)


def _sleeve_turnover(current: dict[str, float], previous: dict[str, float]) -> float:
    """One-way turnover for one non-negative sleeve with changing notional."""

    names = set(current) | set(previous)
    l1_change = sum(abs(current.get(name, 0.0) - previous.get(name, 0.0)) for name in names)
    notional_change = abs(sum(current.values()) - sum(previous.values()))
    return finite(0.5 * (l1_change + notional_change))


def estimate_rank_return_slope(
    frame: pd.DataFrame,
    score_col: str,
    target_col: str,
    horizon: int,
) -> float:
    """Estimate a robust return-per-rank slope from the training split only."""

    slopes: list[float] = []
    eligible_date_index = 0
    for date, group in frame.groupby("trade_date", sort=True):
        group = group[["ts_code", score_col, target_col]].dropna()
        if len(group) < 30:
            continue
        absolute_position = _TRADING_DATE_POSITION.get(
            str(date), eligible_date_index
        )
        eligible_date_index += 1
        if absolute_position % max(1, horizon) != 0:
            continue
        ranks = (
            group[score_col].rank(pct=True, method="average").to_numpy(float)
            - 0.5
        )
        realized = group[target_col].to_numpy(float)
        denominator = float(np.dot(ranks, ranks))
        if denominator <= 1e-12:
            continue
        slopes.append(finite(float(np.dot(ranks, realized) / denominator)))
    if not slopes:
        return 0.0
    return max(0.0, finite(float(np.median(slopes))))


def _proximal_l1_around_previous(
    desired: np.ndarray,
    previous: np.ndarray,
    threshold: float,
) -> np.ndarray:
    delta = desired - previous
    return previous + np.sign(delta) * np.maximum(
        np.abs(delta) - max(0.0, threshold), 0.0
    )


def cost_aware_sleeve_weights(
    desired: dict[str, float],
    previous: dict[str, float],
    *,
    cost_bps: float,
    rank_return_slope: float,
    raw_rank_sum: float,
) -> dict[str, float]:
    """Solve one convex turnover-regularized sleeve allocation.

    The objective is a quadratic tracking loss around the frictionless
    rank portfolio plus the exact linear transaction-cost rate times L1
    trading.  Curvature is calibrated so the zero-cost optimum equals the
    rank portfolio.  No test-period parameter or no-trade threshold enters
    the solver.
    """

    if not desired:
        return {}
    if not previous or cost_bps <= 0.0 or rank_return_slope <= 1e-12:
        return dict(desired)
    names = sorted(set(desired) | set(previous))
    target = np.asarray([desired.get(name, 0.0) for name in names], dtype=float)
    prior = np.asarray([previous.get(name, 0.0) for name in names], dtype=float)
    target_notional = max(0.0, float(target.sum()))
    if target_notional <= 1e-12:
        return {}
    curvature = (
        rank_return_slope
        * max(float(raw_rank_sum), 1e-12)
        / target_notional
    )
    if curvature <= 1e-12:
        return dict(desired)
    # Constant-notional one-way sleeve turnover is 0.5 * L1.
    threshold = (0.5 * max(0.0, cost_bps) / 10000.0) / curvature

    def weights_at_shift(shift: float) -> np.ndarray:
        values = _proximal_l1_around_previous(
            target - shift, prior, threshold
        )
        return np.maximum(values, 0.0)

    bound = max(
        1.0,
        target_notional,
        float(np.max(np.abs(target))) if len(target) else 0.0,
        float(np.max(np.abs(prior))) if len(prior) else 0.0,
    )
    lower, upper = -2.0 * bound, 2.0 * bound
    while float(weights_at_shift(lower).sum()) < target_notional:
        lower *= 2.0
    while float(weights_at_shift(upper).sum()) > target_notional:
        upper *= 2.0
    for _ in range(80):
        midpoint = 0.5 * (lower + upper)
        if float(weights_at_shift(midpoint).sum()) > target_notional:
            lower = midpoint
        else:
            upper = midpoint
    optimized = weights_at_shift(0.5 * (lower + upper))
    total = float(optimized.sum())
    if total > 1e-12:
        optimized *= target_notional / total
    return {
        name: finite(float(value))
        for name, value in zip(names, optimized)
        if value > 1e-12
    }


def causal_rank_innovation_gain(
    current_signal: dict[str, float],
    previous_signal: dict[str, float],
) -> float:
    """Kalman-style update gain from observable cross-sectional innovation."""

    overlap = sorted(set(current_signal) & set(previous_signal))
    if len(overlap) < 10:
        return 1.0
    current = np.asarray([current_signal[name] for name in overlap], dtype=float)
    previous = np.asarray([previous_signal[name] for name in overlap], dtype=float)
    current -= float(np.mean(current))
    previous -= float(np.mean(previous))
    signal_variance = float(np.var(current, ddof=1))
    innovation_variance = float(np.var(current - previous, ddof=1))
    denominator = signal_variance + innovation_variance
    if denominator <= 1.0e-12:
        return 1.0
    return finite(float(np.clip(signal_variance / denominator, 0.0, 1.0)), 1.0)


def reliability_blended_sleeve(
    desired: dict[str, float],
    previous: dict[str, float],
    gain: float,
) -> dict[str, float]:
    """Partially move from prior holdings to the new rank target."""

    if not desired or not previous:
        return dict(desired)
    target_notional = float(sum(desired.values()))
    names = sorted(set(desired) | set(previous))
    update_gain = float(np.clip(gain, 0.0, 1.0))
    blended = np.maximum(np.asarray([
        previous.get(name, 0.0)
        + update_gain * (desired.get(name, 0.0) - previous.get(name, 0.0))
        for name in names
    ], dtype=float), 0.0)
    total = float(blended.sum())
    if total <= 1.0e-12:
        return dict(desired)
    blended *= target_notional / total
    return {name: finite(float(value)) for name, value in zip(names, blended) if value > 1.0e-12}




def backtest_cross_section(
    frame: pd.DataFrame,
    score_col: str,
    target_col: str,
    cost_bps: float,
    horizon: int,
    risk_budget: dict[str, Any] | None = None,
    selection_buffer: float = 0.0,
    portfolio_construction: str = "top_decile",
    transaction_cost_optimized: bool = False,
    rank_return_slope: float = 0.0,
    adaptive_rank_return_slope: bool = False,
    asset_risk_weighted: bool = False,
    risk_col: str | None = None,
    causal_signal_reliability: bool = False,
) -> dict[str, Any]:
    daily: list[dict[str, Any]] = []
    previous_long: dict[str, float] = {}
    previous_short: dict[str, float] = {}
    prior_unscaled_returns: list[float] = []
    pending_unscaled_returns: list[tuple[int, float]] = []
    prior_rank_return_slopes: list[float] = []
    pending_rank_return_slopes: list[tuple[int, float]] = []
    previous_scale = 1.0
    previous_effective_rank_slope = finite(rank_return_slope)
    previous_signal_rank: dict[str, float] = {}
    previous_signal_gain = 1.0
    eligible_date_index = 0
    for date, group in frame.groupby("trade_date", sort=True):
        columns = ["ts_code", score_col, target_col]
        if asset_risk_weighted and risk_col:
            columns.append(risk_col)
        group = group[columns].dropna(subset=["ts_code", score_col, target_col])
        if len(group) < 30:
            continue
        group = group.sort_values(score_col)
        n = max(3, len(group) // 10)
        ic = safe_corr(rankdata(group[score_col].to_numpy()), rankdata(group[target_col].to_numpy()))
        absolute_position = _TRADING_DATE_POSITION.get(str(date), eligible_date_index)
        matured = [
            value
            for maturity, value in pending_unscaled_returns
            if maturity <= absolute_position
        ]
        prior_unscaled_returns.extend(matured)
        pending_unscaled_returns = [
            item for item in pending_unscaled_returns if item[0] > absolute_position
        ]
        matured_slopes = [
            value
            for maturity, value in pending_rank_return_slopes
            if maturity <= absolute_position
        ]
        prior_rank_return_slopes.extend(matured_slopes)
        pending_rank_return_slopes = [
            item for item in pending_rank_return_slopes
            if item[0] > absolute_position
        ]
        is_rebalance = absolute_position % max(1, horizon) == 0
        eligible_date_index += 1
        gross = net = turnover = 0.0
        if is_rebalance:
            ordered = list(group.ts_code.astype(str))
            indexed = group.set_index(group.ts_code.astype(str))
            if portfolio_construction == "continuous_rank":
                centered_rank = (
                    group.set_index(group.ts_code.astype(str))[score_col]
                    .rank(pct=True, method="average")
                    .sub(0.5)
                )
                long_raw = centered_rank.clip(lower=0.0)
                short_raw = centered_rank.clip(upper=0.0).abs()
                if asset_risk_weighted and risk_col:
                    observed_risk = pd.to_numeric(
                        indexed.loc[centered_rank.index, risk_col],
                        errors="coerce",
                    ).abs()
                    positive_risk = observed_risk[
                        np.isfinite(observed_risk) & (observed_risk > 1e-12)
                    ]
                    risk_fallback = (
                        float(positive_risk.median())
                        if not positive_risk.empty else 1.0
                    )
                    observed_risk = observed_risk.where(
                        np.isfinite(observed_risk)
                        & (observed_risk > 1e-12),
                        risk_fallback,
                    )
                    long_raw = long_raw.div(observed_risk)
                    short_raw = short_raw.div(observed_risk)
                long_base = (
                    long_raw / max(float(long_raw.sum()), 1e-12)
                )
                short_base = (
                    short_raw / max(float(short_raw.sum()), 1e-12)
                )
                long_base = long_base[long_base > 0.0]
                short_base = short_base[short_base > 0.0]
                rank_values = centered_rank.to_numpy(float)
                realized_values = indexed.loc[
                    centered_rank.index, target_col
                ].to_numpy(float)
                rank_denominator = float(np.dot(rank_values, rank_values))
                realized_rank_slope = (
                    finite(float(np.dot(rank_values, realized_values) / rank_denominator))
                    if rank_denominator > 1e-12 else 0.0
                )
            else:
                realized_rank_slope = 0.0
                buffer_count = max(
                    0, int(round(n * max(0.0, selection_buffer)))
                )
                short_pool = set(
                    ordered[: min(len(ordered), n + buffer_count)]
                )
                long_pool = set(
                    ordered[max(0, len(ordered) - n - buffer_count):]
                )
                short_names = [
                    name for name in previous_short if name in short_pool
                ]
                short_names.extend(
                    name for name in ordered if name not in short_names
                )
                short_names = short_names[:n]
                long_names = [
                    name for name in previous_long if name in long_pool
                ]
                long_names.extend(
                    name for name in reversed(ordered)
                    if name not in long_names
                )
                long_names = long_names[:n]
                short = set(short_names)
                long = set(long_names)
                long_base = pd.Series(1.0 / len(long), index=list(long))
                short_base = pd.Series(1.0 / len(short), index=list(short))
                base_gross = finite(
                    indexed.loc[list(long), target_col].mean()
                    - indexed.loc[list(short), target_col].mean()
                )
            periods = 252.0 / max(1, horizon)
            risk_scale = causal_volatility_scale(
                prior_unscaled_returns, periods, previous_scale, risk_budget
            )
            desired_long = {
                str(name): risk_scale * float(value)
                for name, value in long_base.items()
            }
            desired_short = {
                str(name): risk_scale * float(value)
                for name, value in short_base.items()
            }
            current_signal_rank = (
                {str(name): float(value) for name, value in centered_rank.items()}
                if portfolio_construction == "continuous_rank" else {}
            )
            signal_reliability_gain = 1.0
            if (
                transaction_cost_optimized
                and portfolio_construction == "continuous_rank"
            ):
                if adaptive_rank_return_slope:
                    slope_history = [
                        finite(rank_return_slope),
                        *prior_rank_return_slopes,
                    ]
                    effective_rank_return_slope = max(
                        0.0, finite(float(np.median(slope_history)))
                    )
                else:
                    effective_rank_return_slope = finite(rank_return_slope)
                available_names = set(indexed.index.astype(str))
                tradable_previous_long = {
                    name: value for name, value in previous_long.items()
                    if name in available_names
                }
                tradable_previous_short = {
                    name: value for name, value in previous_short.items()
                    if name in available_names
                }
                long_weights = cost_aware_sleeve_weights(
                    desired_long,
                    tradable_previous_long,
                    cost_bps=cost_bps,
                    rank_return_slope=effective_rank_return_slope,
                    raw_rank_sum=float(long_raw.sum()),
                )
                short_weights = cost_aware_sleeve_weights(
                    desired_short,
                    tradable_previous_short,
                    cost_bps=cost_bps,
                    rank_return_slope=effective_rank_return_slope,
                    raw_rank_sum=float(short_raw.sum()),
                )
                for name in set(long_weights) & set(short_weights):
                    offset = min(long_weights[name], short_weights[name])
                    long_weights[name] -= offset
                    short_weights[name] -= offset
                long_weights = {
                    name: value for name, value in long_weights.items()
                    if value > 1e-12
                }
                short_weights = {
                    name: value for name, value in short_weights.items()
                    if value > 1e-12
                }
            elif (
                causal_signal_reliability
                and portfolio_construction == "continuous_rank"
            ):
                effective_rank_return_slope = finite(rank_return_slope)
                signal_reliability_gain = causal_rank_innovation_gain(
                    current_signal_rank, previous_signal_rank
                )
                available_names = set(indexed.index.astype(str))
                tradable_previous_long = {
                    name: value for name, value in previous_long.items()
                    if name in available_names
                }
                tradable_previous_short = {
                    name: value for name, value in previous_short.items()
                    if name in available_names
                }
                long_weights = reliability_blended_sleeve(
                    desired_long, tradable_previous_long, signal_reliability_gain
                )
                short_weights = reliability_blended_sleeve(
                    desired_short, tradable_previous_short, signal_reliability_gain
                )
            else:
                effective_rank_return_slope = finite(rank_return_slope)
                long_weights = desired_long
                short_weights = desired_short
            if current_signal_rank:
                previous_signal_rank = current_signal_rank
            previous_signal_gain = signal_reliability_gain
            turnover = (
                _sleeve_turnover(long_weights, previous_long)
                + _sleeve_turnover(short_weights, previous_short)
            )
            gross = finite(
                sum(
                    value * finite(indexed.at[name, target_col])
                    for name, value in long_weights.items()
                )
                - sum(
                    value * finite(indexed.at[name, target_col])
                    for name, value in short_weights.items()
                )
            )
            actual_scale = min(
                sum(long_weights.values()), sum(short_weights.values())
            )
            base_gross = (
                finite(gross / actual_scale) if actual_scale > 1e-12 else 0.0
            )
            net = gross - turnover * cost_bps / 10000.0
            previous_long, previous_short = long_weights, short_weights
            previous_scale = risk_scale
            pending_unscaled_returns.append(
                (absolute_position + max(1, horizon) + 1, base_gross)
            )
            if portfolio_construction == "continuous_rank":
                pending_rank_return_slopes.append(
                    (
                        absolute_position + max(1, horizon) + 1,
                        realized_rank_slope,
                    )
                )
            previous_effective_rank_slope = effective_rank_return_slope
        else:
            base_gross = 0.0
            actual_scale = min(
                sum(previous_long.values()), sum(previous_short.values())
            )
            risk_scale = actual_scale
            effective_rank_return_slope = previous_effective_rank_slope
            signal_reliability_gain = previous_signal_gain
        current_long_weights = previous_long
        current_short_weights = previous_short
        daily.append({
            "date": str(date),
            "gross": gross,
            "base_gross": base_gross,
            "net": net,
            "turnover": turnover,
            "rank_ic": ic,
            "is_rebalance": is_rebalance,
            "risk_scale": actual_scale,
            "cost_aware_rank_slope": effective_rank_return_slope,
            "signal_reliability_gain": signal_reliability_gain,
            "long_effective_names": finite(
                sum(current_long_weights.values()) ** 2
                / sum(
                    value * value for value in current_long_weights.values()
                )
            ) if current_long_weights else 0.0,
            "short_effective_names": finite(
                sum(current_short_weights.values()) ** 2
                / sum(
                    value * value for value in current_short_weights.values()
                )
            ) if current_short_weights else 0.0,
        })
    if not daily:
        return {"rank_ic": 0.0, "icir": 0.0, "hit_rate": 0.0, "annual_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "turnover": 0.0, "series": []}
    ic = np.array([x["rank_ic"] for x in daily], dtype=float)
    performance_rows = [x for x in daily if x["is_rebalance"]]
    returns = np.array([x["net"] for x in performance_rows], dtype=float)
    periods = 252 / max(1, horizon)
    annual = finite(np.prod(1 + returns) ** (periods / max(len(returns), 1)) - 1) if len(returns) else 0.0
    vol = annualized_volatility(returns, periods)
    sharpe = annualized_sharpe(returns, periods)
    ic_std = np.std(ic, ddof=1) if len(ic) > 1 else 0.0
    hac_lag = automatic_hac_lag(len(ic), minimum_lag=max(0, horizon - 1))
    return {
        "rank_ic": finite(np.mean(ic)),
        "icir": finite(
            hac_information_ratio(
                ic,
                252,
                max_lag=hac_lag,
                minimum_lag=max(0, horizon - 1),
            )
        ),
        "icir_naive": finite(np.mean(ic) / ic_std * math.sqrt(252)) if ic_std > 1e-12 else 0.0,
        "ic_hac_lag": hac_lag,
        "ic_effective_observations": finite(
            effective_observations(
                ic,
                max_lag=hac_lag,
                minimum_lag=max(0, horizon - 1),
            )
        ),
        "hit_rate": finite(np.mean(ic > 0)),
        "annual_return": annual,
        "annual_volatility": vol,
        "sharpe": finite(sharpe),
        "max_drawdown": max_drawdown(returns),
        "turnover": finite(np.mean([x["turnover"] for x in performance_rows])),
        "average_risk_scale": finite(
            np.mean([x["risk_scale"] for x in performance_rows]), 1.0
        ),
        "observations": len(returns),
        "signal_observations": len(daily),
        "rebalance_every_n_trading_days": max(1, horizon),
        "turnover_convention": (
            "sum of one-way long and short sleeve turnover; initial full-risk funding=2.0; "
            "risk-budget notional changes are included"
        ),
        "risk_budget": dict(risk_budget or {"name": "full_exposure"}),
        "selection_buffer": finite(selection_buffer),
        "portfolio_construction": portfolio_construction,
        "transaction_cost_optimized": bool(transaction_cost_optimized),
        "adaptive_rank_return_slope": bool(adaptive_rank_return_slope),
        "causal_signal_reliability": bool(causal_signal_reliability),
        "rank_return_slope": finite(rank_return_slope),
        "average_cost_aware_rank_slope": finite(np.mean([
            x["cost_aware_rank_slope"]
            for x in performance_rows
        ])),
        "average_signal_reliability_gain": finite(np.mean([
            x["signal_reliability_gain"]
            for x in performance_rows
        ]), 1.0),
        "risk_return_observation_lag": max(1, horizon) + 1,
        "average_gross_exposure": finite(
            2.0 * np.mean([x["risk_scale"] for x in performance_rows])
        ),
        "asset_risk_weighted": bool(asset_risk_weighted),
        "average_long_effective_names": finite(np.mean([
            x["long_effective_names"] for x in performance_rows
        ])),
        "average_short_effective_names": finite(np.mean([
            x["short_effective_names"] for x in performance_rows
        ])),
        "series": daily,
    }


def deflated_sharpe_proxy(sharpe: float, observations: int, trials: int) -> dict[str, float]:
    if observations < 3:
        return {"dsr_confidence": 0.0, "pbo_proxy": 1.0}
    # Conservative Gaussian multiple-testing approximation; explicitly labeled proxy.
    expected_max = math.sqrt(max(0.0, 2 * math.log(max(1, trials))))
    z = (sharpe * math.sqrt(max(observations - 1, 1)) - expected_max) / math.sqrt(max(1e-9, 1 + 0.5 * sharpe * sharpe))
    confidence = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return {"dsr_confidence": finite(confidence), "pbo_proxy": finite(1 - confidence)}


@dataclass
class Panel:
    frame: pd.DataFrame
    dates: list[str]
    assets: list[str]
    features: np.ndarray
    targets: np.ndarray
    valid: np.ndarray
    feature_names: list[str]
    horizons: list[int]
    split: dict[str, tuple[int, int]]
    source: dict[str, Any]


def read_panel(config: dict[str, Any], progress_path: Path | None = None) -> Panel:
    db_path = Path(config["database_path"])
    if not db_path.exists():
        raise FileNotFoundError(f"research warehouse unavailable: {db_path}")
    max_assets = int(config.get("max_assets", 160))
    max_months = int(config.get("max_months", 60))
    sequence = int(config.get("sequence_length", 120))
    horizons = sorted({int(x) for x in config.get("horizons", [5, 10, 20]) if 1 <= int(x) <= 60})
    if not horizons:
        horizons = [5, 10, 20]
    required_dates_limit = int(config.get("required_dates_limit", 1600))
    required_dates_limit = min(max(required_dates_limit, 260), 5000)
    required_dates = min(required_dates_limit, max(260, max_months * 22 + sequence + max(horizons) + 30))
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    progress(progress_path, "data", 0.04, "读取交易日与流动性股票池")
    dates_desc = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM stock_ohlcv_daily ORDER BY trade_date DESC LIMIT ?", (required_dates,)
    )]
    if len(dates_desc) < sequence + max(horizons) + 60:
        raise RuntimeError("insufficient chronological market data")
    start_date, end_date = dates_desc[-1], dates_desc[0]
    chronological_dates = sorted(dates_desc)
    split_valid_start_cutoff = str(config.get("split_valid_start_date") or "").replace("-", "")
    provisional_train_end = int(len(chronological_dates) * float(config.get("split_train_ratio", 0.60)))
    if split_valid_start_cutoff:
        for index, trade_date in enumerate(chronological_dates):
            if str(trade_date) >= split_valid_start_cutoff:
                provisional_train_end = index
                break
    embargo = max(horizons) + 1
    universe_index = max(0, min(len(chronological_dates) - 1, provisional_train_end - embargo - 1))
    universe_end = chronological_dates[universe_index]
    universe_start = chronological_dates[max(0, universe_index - 59)]
    liquidity_assets = [r[0] for r in conn.execute(
        "SELECT ts_code FROM stock_ohlcv_daily WHERE trade_date BETWEEN ? AND ? AND amount IS NOT NULL "
        "GROUP BY ts_code HAVING COUNT(*)>=30 ORDER BY AVG(amount) DESC LIMIT ?",
        (universe_start, universe_end, max_assets),
    )]
    assets = liquidity_assets
    asset_universe_policy = "liquidity universe frozen at purged train end; validation and test observations excluded"
    asset_universe_fallback = ""
    factor_mode = str(config.get("factor_universe_mode") or "").strip().lower()
    if factor_mode in {"screened_full", "warehouse_screened", "full", "unified", "screened"} or bool(config.get("use_unified_factor_panel")):
        try:
            factor_asset_limit = min(800, max(max_assets * 4, max_assets))
            factor_assets = [r[0] for r in conn.execute(
                "SELECT ts_code FROM factor_value_daily WHERE trade_date BETWEEN ? AND ? AND factor_value IS NOT NULL "
                "GROUP BY ts_code HAVING COUNT(*)>=? ORDER BY COUNT(*) DESC LIMIT ?",
                (
                    start_date,
                    universe_end,
                    int(config.get("factor_asset_min_values", 20)),
                    factor_asset_limit,
                ),
            )]
            if factor_assets:
                factor_placeholders = ",".join("?" for _ in factor_assets)
                scoped_assets = [r[0] for r in conn.execute(
                    f"SELECT ts_code FROM stock_ohlcv_daily WHERE trade_date BETWEEN ? AND ? AND amount IS NOT NULL "
                    f"AND ts_code IN ({factor_placeholders}) "
                    "GROUP BY ts_code HAVING COUNT(*)>=30 ORDER BY AVG(amount) DESC LIMIT ?",
                    (universe_start, universe_end, *factor_assets, max_assets),
                )]
                if len(scoped_assets) >= 30:
                    assets = scoped_assets
                    asset_universe_policy = "liquidity universe selected inside materialized factor coverage before validation/test; no test observations used"
                else:
                    asset_universe_fallback = f"factor_covered_asset_count_below_minimum: {len(scoped_assets)}"
        except sqlite3.Error as exc:
            asset_universe_fallback = f"factor_asset_prefilter_failed: {exc}"
    if len(assets) < 30:
        raise RuntimeError("insufficient liquid assets")
    placeholders = ",".join("?" for _ in assets)
    sql = f"""
        SELECT o.trade_date,o.ts_code,o.open,o.high,o.low,o.close,
               COALESCE(o.qfq_close,o.close) AS qfq_close,o.pre_close,o.pct_chg,o.vol,o.amount,
               v.pe_ttm,v.pb,v.ps_ttm,v.dv_ttm,v.total_mv,v.circ_mv,v.turnover_rate,v.volume_ratio,
               m.net_mf_amount,m.buy_lg_amount,m.sell_lg_amount,m.buy_elg_amount,m.sell_elg_amount
        FROM stock_ohlcv_daily o
        LEFT JOIN stock_valuation_daily v ON v.trade_date=o.trade_date AND v.ts_code=o.ts_code
        LEFT JOIN stock_moneyflow_daily m ON m.trade_date=o.trade_date AND m.ts_code=o.ts_code
        WHERE o.trade_date>=? AND o.ts_code IN ({placeholders})
        ORDER BY o.trade_date,o.ts_code
    """
    progress(progress_path, "data", 0.09, "读取行情、估值与资金流点时面板")
    frame = pd.read_sql_query(sql, conn, params=[start_date, *assets])
    financial = pd.read_sql_query(
        f"""
        SELECT ts_code,visible_date,roe,roa,gross_margin,debt_to_assets,
               assets_turn,op_yoy,tr_yoy,netprofit_yoy
        FROM financial_report_visible
        WHERE visible_date<=? AND ts_code IN ({placeholders})
        ORDER BY ts_code,visible_date
        """,
        conn,
        params=[end_date, *assets],
    )
    industry_periods = pd.read_sql_query(
        f"""
        SELECT ts_code,start_date,end_date,industry_name
        FROM sw_l1_industry_daily
        WHERE ts_code IN ({placeholders})
        ORDER BY ts_code,start_date
        """,
        conn,
        params=assets,
    )
    conn.close()
    if frame.empty:
        raise RuntimeError("empty market panel")
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame = frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    financial_columns = [
        "roe", "roa", "gross_margin", "debt_to_assets", "assets_turn",
        "op_yoy", "tr_yoy", "netprofit_yoy",
    ]
    enriched: list[pd.DataFrame] = []
    for ts_code, stock in frame.groupby("ts_code", sort=False):
        stock["_date_key"] = pd.to_numeric(stock["trade_date"], errors="raise").astype(np.int64)
        stock = stock.sort_values("trade_date").copy()
        visible = financial.loc[financial.ts_code == ts_code].drop(columns=["ts_code"]).copy()
        if not visible.empty:
            visible["visible_date"] = visible["visible_date"].astype(str)
            visible = visible.sort_values("visible_date").drop_duplicates("visible_date", keep="last")
            visible["_date_key"] = pd.to_numeric(visible["visible_date"], errors="raise").astype(np.int64)
            stock = pd.merge_asof(
                stock, visible, on="_date_key",
                direction="backward", allow_exact_matches=True,
            )
        else:
            for column in financial_columns:
                stock[column] = np.nan
            stock["visible_date"] = None
        periods = industry_periods.loc[industry_periods.ts_code == ts_code].drop(columns=["ts_code"]).copy()
        if not periods.empty:
            periods["start_date"] = periods["start_date"].astype(str)
            periods["end_date"] = periods["end_date"].fillna("99991231").astype(str)
            periods = periods.sort_values("start_date").drop_duplicates("start_date", keep="last")
            periods["_date_key"] = pd.to_numeric(periods["start_date"], errors="raise").astype(np.int64)
            stock = pd.merge_asof(
                stock, periods, on="_date_key",
                direction="backward", allow_exact_matches=True,
            )
            stock["industry_name"] = stock["industry_name"].where(
                stock["trade_date"] <= stock["end_date"]
            )
        else:
            stock["industry_name"] = "未知"
            stock["start_date"] = None
            stock["end_date"] = None
        stock = stock.drop(columns=["_date_key"])
        enriched.append(stock)
    frame = pd.concat(enriched, ignore_index=True).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = frame.groupby("ts_code", sort=False)
    price = frame["qfq_close"].where(frame["qfq_close"] > 0)
    frame["ret_1"] = g["qfq_close"].pct_change(fill_method=None)
    for h in [5, 20, 60]:
        frame[f"ret_{h}"] = g["qfq_close"].pct_change(h, fill_method=None)
    frame["vol_20"] = g["ret_1"].rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
    negative = frame["ret_1"].clip(upper=0)
    frame["down_vol_20"] = negative.groupby(frame["ts_code"]).rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
    low60 = g["qfq_close"].rolling(60, min_periods=30).min().reset_index(level=0, drop=True)
    high60 = g["qfq_close"].rolling(60, min_periods=30).max().reset_index(level=0, drop=True)
    frame["price_pos_60"] = (price - low60) / (high60 - low60).replace(0, np.nan) - 0.5
    log_vol = np.log1p(frame["vol"].clip(lower=0))
    vol_mean = log_vol.groupby(frame["ts_code"]).rolling(20, min_periods=12).mean().reset_index(level=0, drop=True)
    vol_std = log_vol.groupby(frame["ts_code"]).rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
    frame["volume_z_20"] = (log_vol - vol_mean) / vol_std.replace(0, np.nan)
    illiq = frame["ret_1"].abs() / frame["amount"].replace(0, np.nan)
    frame["amihud_20"] = np.log1p(illiq.groupby(frame["ts_code"]).rolling(20, min_periods=12).mean().reset_index(level=0, drop=True) * 1e8)
    frame["turnover"] = frame["turnover_rate"] / 100.0
    frame["value_ep"] = np.where(frame["pe_ttm"] > 0, 1 / frame["pe_ttm"], np.nan)
    frame["value_bp"] = np.where(frame["pb"] > 0, 1 / frame["pb"], np.nan)
    frame["value_sp"] = np.where(frame["ps_ttm"] > 0, 1 / frame["ps_ttm"], np.nan)
    frame["dividend"] = frame["dv_ttm"] / 100.0
    frame["log_mv"] = np.log(frame["circ_mv"].where(frame["circ_mv"] > 0))
    denominator = frame["amount"].abs().replace(0, np.nan)
    frame["moneyflow"] = frame["net_mf_amount"] / denominator
    frame["large_flow"] = (frame["buy_lg_amount"] - frame["sell_lg_amount"]) / denominator
    frame["extreme_flow"] = (frame["buy_elg_amount"] - frame["sell_elg_amount"]) / denominator
    frame["range_1"] = (frame["high"] - frame["low"]) / frame["pre_close"].replace(0, np.nan)
    frame["gap_1"] = frame["open"] / frame["pre_close"].replace(0, np.nan) - 1
    frame["quality_roe"] = pd.to_numeric(frame["roe"], errors="coerce")
    frame["quality_roa"] = pd.to_numeric(frame["roa"], errors="coerce")
    frame["quality_gross_margin"] = pd.to_numeric(frame["gross_margin"], errors="coerce")
    frame["quality_asset_turn"] = pd.to_numeric(frame["assets_turn"], errors="coerce")
    frame["quality_low_leverage"] = -pd.to_numeric(frame["debt_to_assets"], errors="coerce")
    frame["growth_revenue"] = pd.to_numeric(frame["tr_yoy"], errors="coerce")
    frame["growth_operating_profit"] = pd.to_numeric(frame["op_yoy"], errors="coerce")
    frame["growth_net_profit"] = pd.to_numeric(frame["netprofit_yoy"], errors="coerce")
    entry_price = g["qfq_close"].shift(-1)
    for h in horizons:
        # Signals use the T close and are executed after one full trading-day
        # lag. The target therefore starts at T+1 close and ends at T+h+1.
        frame[f"target_{h}"] = g["qfq_close"].shift(-(h + 1)) / entry_price - 1
        # Cross-sectional market residualization changes neither the long-short
        # spread nor the rank IC and keeps the learning target numerically stable.
        market = frame.groupby("trade_date")[f"target_{h}"].transform("mean")
        frame[f"target_{h}"] = (frame[f"target_{h}"] - market).clip(-0.6, 0.6)
    history_count = g.cumcount() + 1
    liquidity_20 = g["amount"].rolling(20, min_periods=12).median().reset_index(level=0, drop=True)
    minimum_history = max(60, min(sequence, 252))
    frame["model_eligible"] = (
        (history_count >= minimum_history)
        & (liquidity_20 > 0)
        & price.notna()
        & (price > 0)
    )
    progress(progress_path, "data", 0.15, "构造因果特征、缺失掩码与多周期残差标签")
    dates = sorted(frame.trade_date.unique().tolist())
    assets = sorted(frame.ts_code.unique().tolist())
    global _TRADING_DATE_POSITION
    _TRADING_DATE_POSITION = {str(date): index for index, date in enumerate(dates)}
    date_index = {d: i for i, d in enumerate(dates)}
    asset_index = {a: i for i, a in enumerate(assets)}
    def split_boundary(value: Any, default_index: int) -> int:
        cutoff = str(value or "").replace("-", "")
        if not cutoff:
            return default_index
        for index, trade_date in enumerate(dates):
            if str(trade_date) >= cutoff:
                return index
        return default_index

    split_train_ratio = float(config.get("split_train_ratio", 0.60))
    split_valid_ratio = float(config.get("split_valid_ratio", 0.80))
    split_train_ratio = min(max(split_train_ratio, 0.30), 0.80)
    split_valid_ratio = min(max(split_valid_ratio, split_train_ratio + 0.05), 0.95)
    split_train = int(len(dates) * split_train_ratio)
    split_valid = int(len(dates) * split_valid_ratio)
    embargo = max(horizons) + 1
    if config.get("split_test_start_date"):
        valid_start = split_boundary(config.get("split_valid_start_date"), split_train)
        test_start = split_boundary(config.get("split_test_start_date"), split_valid)
        valid_start = min(max(valid_start, sequence + embargo + 1), len(dates) - max(horizons) - embargo - 2)
        test_start = min(max(test_start, valid_start + embargo + 1), len(dates) - max(horizons) - 1)
        split = {
            "train": (sequence, max(sequence + 1, valid_start - embargo)),
            "valid": (valid_start, max(valid_start + 1, test_start - embargo)),
            "test": (test_start, len(dates) - max(horizons) - 1),
        }
    else:
        split = {
            "train": (sequence, max(sequence + 1, split_train - embargo)),
            "valid": (split_train, max(split_train + 1, split_valid - embargo)),
            "test": (split_valid, len(dates) - max(horizons) - 1),
        }
    feature_names = list(FEATURES)
    factor_universe_report: dict[str, Any] = {
        "mode": "core_29",
        "enabled": False,
        "feature_count": len(feature_names),
        "test_usage": "not_applicable",
    }
    try:
        try:
            from unified_factor_panel import extend_with_screened_factors
        except ImportError:
            from model.factor_laboratory.unified_factor_panel import extend_with_screened_factors
        progress(
            progress_path,
            "data",
            0.17,
            "merge_materialized_factors_and_screen_on_train_valid",
        )
        frame, feature_names, factor_universe_report = extend_with_screened_factors(
            frame,
            database_path=db_path,
            base_features=list(FEATURES),
            target_col=f"target_{horizons[0]}",
            date_order=dates,
            assets=assets,
            split=split,
            config=config,
        )
    except Exception as exc:
        factor_universe_report = {
            "mode": str(config.get("factor_universe_mode") or "core_29"),
            "enabled": bool(config.get("use_unified_factor_panel") or config.get("factor_universe_mode")),
            "feature_count": len(feature_names),
            "message": f"factor_universe_extension_failed: {exc}",
            "fallback": "core_29",
            "test_usage": "excluded_from_factor_screening",
        }
    feature_array = np.full((len(dates), len(assets), len(feature_names)), np.nan, dtype=np.float32)
    target_array = np.full((len(dates), len(assets), len(horizons)), np.nan, dtype=np.float32)
    eligibility_array = np.zeros((len(dates), len(assets)), dtype=bool)
    di = frame.trade_date.map(date_index).to_numpy()
    ai = frame.ts_code.map(asset_index).to_numpy()
    feature_array[di, ai] = frame[feature_names].to_numpy(dtype=np.float32)
    target_array[di, ai] = frame[[f"target_{h}" for h in horizons]].to_numpy(dtype=np.float32)
    eligibility_array[di, ai] = frame["model_eligible"].to_numpy(dtype=bool)
    minimum_feature_count = max(8, int(math.ceil(min(len(feature_names), len(LEGACY_FEATURES)) * 0.60)))
    valid = (
        np.isfinite(target_array).all(axis=2)
        & (np.isfinite(feature_array).sum(axis=2) >= minimum_feature_count)
        & eligibility_array
    )
    return Panel(
        frame=frame,
        dates=dates,
        assets=assets,
        features=feature_array,
        targets=target_array,
        valid=valid,
        feature_names=feature_names,
        horizons=horizons,
        split=split,
        source={
            "database": str(db_path), "start_date": start_date, "end_date": end_date,
            "rows": int(len(frame)), "dates": len(dates), "assets": len(assets),
            "feature_count": len(feature_names),
            "watermark": max(dates), "point_in_time": True,
            "factor_universe": factor_universe_report,
            "universe_formation_date": universe_end,
            "universe_liquidity_window": [universe_start, universe_end],
            "universe_policy": asset_universe_policy,
            "universe_fallback": asset_universe_fallback,
            "execution_lag_trading_days": 1,
            "minimum_feature_count": minimum_feature_count,
            "minimum_listing_history": minimum_history,
            "financial_point_in_time_policy": "latest financial_report_visible.visible_date not after signal date",
            "financial_feature_coverage": finite(frame[financial_columns].notna().mean().mean()),
            "industry_mapping_coverage": finite(frame["industry_name"].notna().mean()),
        },
    )


def normalise_panel(panel: Panel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    start, end = panel.split["train"]
    train = panel.features[start:end]
    median = np.nanmedian(train.reshape(-1, train.shape[-1]), axis=0)
    q25 = np.nanpercentile(train.reshape(-1, train.shape[-1]), 25, axis=0)
    q75 = np.nanpercentile(train.reshape(-1, train.shape[-1]), 75, axis=0)
    scale = np.where(q75 - q25 > 1e-6, q75 - q25, 1.0)
    values = np.nan_to_num((panel.features - median) / scale, nan=0.0, posinf=8.0, neginf=-8.0)
    values = np.clip(values, -8, 8).astype(np.float32)
    missing = (~np.isfinite(panel.features)).astype(np.float32)
    return values, missing, np.concatenate([median, scale])


def cross_sectional_rank_features(panel: Panel) -> np.ndarray:
    """Rank every feature inside each date without using future observations."""

    ranked = np.zeros_like(panel.features, dtype=np.float32)
    for date_index in range(panel.features.shape[0]):
        frame = pd.DataFrame(panel.features[date_index])
        values = frame.rank(axis=0, pct=True, method="average").sub(0.5)
        ranked[date_index] = values.fillna(0.0).to_numpy(dtype=np.float32)
    return ranked


def neutralize_cross_sectional_scores(frame: pd.DataFrame) -> np.ndarray:
    """Remove same-date industry means and linear size exposure from scores."""

    work = frame.copy()
    work["score"] = pd.to_numeric(work["score"], errors="coerce").fillna(0.0)
    work["log_mv"] = pd.to_numeric(work.get("log_mv"), errors="coerce")
    work["industry_name"] = work.get("industry_name", "未知").fillna("未知").astype(str)
    output = pd.Series(0.0, index=work.index, dtype=float)
    for _, group in work.groupby("trade_date", sort=False):
        score = group["score"].astype(float)
        size = group["log_mv"].astype(float)
        size_median = float(size.median()) if size.notna().any() else 0.0
        size_filled = size.fillna(size_median)
        size_scale = max(float(size_filled.std(ddof=0)), 1e-12)
        size_z = ((size_filled - size_median) / size_scale).to_numpy(dtype=float)
        industry = pd.get_dummies(
            group["industry_name"], prefix="industry", drop_first=True, dtype=float
        )
        design_parts = [np.ones((len(group), 1)), size_z[:, None]]
        if not industry.empty:
            design_parts.append(industry.to_numpy(dtype=float))
        design = np.column_stack(design_parts)
        beta = np.linalg.lstsq(design, score.to_numpy(dtype=float), rcond=None)[0]
        residual = pd.Series(score.to_numpy(dtype=float) - design @ beta, index=group.index)
        output.loc[group.index] = residual
    return output.to_numpy(dtype=float)


def residualize_cross_sectional_scores_against_anchor(
    frame: pd.DataFrame,
    anchor_col: str = "anchor_score",
) -> np.ndarray:
    """Keep only same-date overlay information not explained by the anchor."""

    work = frame.copy()
    work["score"] = pd.to_numeric(work["score"], errors="coerce").fillna(0.0)
    anchor_series = work[anchor_col] if anchor_col in work.columns else pd.Series(0.0, index=work.index)
    work[anchor_col] = pd.to_numeric(anchor_series, errors="coerce")
    size_series = work["log_mv"] if "log_mv" in work.columns else pd.Series(0.0, index=work.index)
    work["log_mv"] = pd.to_numeric(size_series, errors="coerce")
    industry_series = work["industry_name"] if "industry_name" in work.columns else pd.Series("unknown", index=work.index)
    work["industry_name"] = industry_series.fillna("unknown").astype(str)
    output = pd.Series(0.0, index=work.index, dtype=float)
    for _, group in work.groupby("trade_date", sort=False):
        score = group["score"].astype(float)
        anchor = group[anchor_col].astype(float)
        anchor_median = float(anchor.median()) if anchor.notna().any() else 0.0
        anchor_filled = anchor.fillna(anchor_median)
        anchor_scale = max(float(anchor_filled.std(ddof=0)), 1e-12)
        anchor_z = ((anchor_filled - anchor_median) / anchor_scale).to_numpy(dtype=float)
        size = group["log_mv"].astype(float)
        size_median = float(size.median()) if size.notna().any() else 0.0
        size_filled = size.fillna(size_median)
        size_scale = max(float(size_filled.std(ddof=0)), 1e-12)
        size_z = ((size_filled - size_median) / size_scale).to_numpy(dtype=float)
        industry = pd.get_dummies(
            group["industry_name"], prefix="industry", drop_first=True, dtype=float
        )
        design_parts = [np.ones((len(group), 1)), anchor_z[:, None], size_z[:, None]]
        if not industry.empty:
            design_parts.append(industry.to_numpy(dtype=float))
        design = np.column_stack(design_parts)
        beta = np.linalg.lstsq(design, score.to_numpy(dtype=float), rcond=None)[0]
        residual = pd.Series(score.to_numpy(dtype=float) - design @ beta, index=group.index)
        output.loc[group.index] = residual
    return output.to_numpy(dtype=float)


def _rank_targets_by_date(values: np.ndarray, dates: np.ndarray) -> np.ndarray:
    work = pd.DataFrame({"date": dates, "value": values})
    return (
        work.groupby("date", sort=False)["value"]
        .rank(pct=True, method="average")
        .sub(0.5)
        .to_numpy(dtype=float)
    )


def cross_sectional_rank_ensemble(
    scores: list[np.ndarray],
    dates: np.ndarray,
) -> np.ndarray:
    """Scale-compatible fixed ensemble; weights are predeclared and test-free."""
    if not scores:
        raise ValueError("rank_ensemble_requires_scores")
    lengths = {len(values) for values in scores}
    if len(lengths) != 1 or lengths != {len(dates)}:
        raise ValueError("rank_ensemble_shape_mismatch")
    ranked = [_rank_targets_by_date(values, dates) for values in scores]
    return np.nanmean(np.column_stack(ranked), axis=1)


def split_frame(panel: Panel, split_name: str, scores: np.ndarray, horizon_index: int = 0) -> pd.DataFrame:
    start, end = panel.split[split_name]
    rows = []
    for local_i, date_i in enumerate(range(start, end)):
        mask = panel.valid[date_i] & np.isfinite(scores[local_i])
        for asset_i in np.where(mask)[0]:
            rows.append((panel.dates[date_i], panel.assets[asset_i], finite(scores[local_i, asset_i]), finite(panel.targets[date_i, asset_i, horizon_index])))
    return pd.DataFrame(rows, columns=["trade_date", "ts_code", "score", "target"])


def torch_modules():
    try:
        import torch
        from torch import nn
        return torch, nn
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyTorch is required by the Factor Laboratory worker") from exc


def run_lstm(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    torch, nn = torch_modules()
    torch.set_num_threads(max(1, int(config.get("cpu_threads", 4))))
    device = torch.device("cuda" if torch.cuda.is_available() and config.get("allow_cuda", True) else "cpu")
    recurrent_cell = str(config.get("recurrent_cell") or config.get("engine") or "lstm").lower()
    if recurrent_cell not in {"lstm", "gru"}:
        recurrent_cell = "lstm"
    values, missing, scaler = normalise_panel(panel)
    sequence = int(config.get("sequence_length", 120))
    domain_indices = [[panel.feature_names.index(x) for x in names if x in panel.feature_names] for names in DOMAINS.values()]
    domain_indices = [x for x in domain_indices if x]

    class DateDataset(torch.utils.data.Dataset):
        def __init__(self, split_name: str):
            self.start, self.end = panel.split[split_name]
            self.indices = list(range(max(sequence, self.start), self.end))

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, index):
            date_i = self.indices[index]
            x = values[date_i - sequence:date_i].transpose(1, 0, 2)
            m = missing[date_i - sequence:date_i].transpose(1, 0, 2)
            y = panel.targets[date_i]
            mask = panel.valid[date_i]
            exposure_cols = [panel.feature_names.index("log_mv"), panel.feature_names.index("vol_20"), panel.feature_names.index("ret_20")]
            exposures = values[date_i][:, exposure_cols]
            return torch.from_numpy(x), torch.from_numpy(m), torch.from_numpy(y), torch.from_numpy(mask), torch.from_numpy(exposures)

    class CausalConv(nn.Module):
        def __init__(self, dim, kernels=(3, 5, 9), dilations=(1, 2, 4)):
            super().__init__()
            self.blocks = nn.ModuleList()
            for kernel, dilation in zip(kernels, dilations):
                pad = (kernel - 1) * dilation
                self.blocks.append(nn.Sequential(
                    nn.ConstantPad1d((pad, 0), 0.0),
                    nn.Conv1d(dim, dim, kernel, dilation=dilation, groups=dim),
                    nn.Conv1d(dim, dim, 1), nn.GELU(), nn.GroupNorm(1, dim),
                ))
            self.gate = nn.Linear(dim * len(self.blocks), dim)

        def forward(self, x):
            z = x.transpose(1, 2)
            parts = [block(z)[..., :z.shape[-1]].transpose(1, 2) for block in self.blocks]
            merged = torch.cat(parts, dim=-1)
            return x + torch.tanh(self.gate(merged))

    class RoutedLSTM(nn.Module):
        def __init__(self, hp: dict[str, Any]):
            super().__init__()
            dim = int(hp["hidden_dim"])
            self.domain_proj = nn.ModuleList([nn.Sequential(nn.Linear(len(idx) * 2, dim), nn.GELU(), nn.LayerNorm(dim)) for idx in domain_indices])
            self.router = nn.Sequential(nn.Linear(len(panel.feature_names) * 2, dim), nn.GELU(), nn.Linear(dim, len(domain_indices)))
            self.conv = CausalConv(dim)
            recurrent_layers = int(hp.get("recurrent_layers", hp.get("lstm_layers", 2)))
            if str(hp.get("recurrent_cell", "lstm")).lower() == "gru":
                self.recurrent = nn.GRU(dim, dim, num_layers=recurrent_layers, batch_first=True, dropout=float(hp["dropout"]))
                self.to_dim = nn.Identity()
            else:
                projection = max(16, dim // 2)
                self.recurrent = nn.LSTM(dim, dim, num_layers=recurrent_layers, batch_first=True, dropout=float(hp["dropout"]), proj_size=projection)
                self.to_dim = nn.Linear(projection, dim)
            layer = nn.TransformerEncoderLayer(dim, int(hp["heads"]), dim * 4, float(hp["dropout"]), batch_first=True, norm_first=True, activation="gelu")
            self.temporal = nn.TransformerEncoder(layer, num_layers=int(hp["attention_layers"]), enable_nested_tensor=False)
            self.regime_gate = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, int(hp["experts"])))
            self.experts = nn.ModuleList([nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Dropout(float(hp["dropout"])), nn.Linear(dim, len(panel.horizons) * 3)) for _ in range(int(hp["experts"]))])
            self.skip = nn.Linear(len(panel.feature_names), len(panel.horizons))
            self.norm = nn.LayerNorm(dim)

        def forward(self, x, m):
            current = torch.cat([x[:, -1], m[:, -1]], dim=-1)
            weights = torch.softmax(self.router(current), dim=-1)
            routed = []
            for proj, idx in zip(self.domain_proj, domain_indices):
                routed.append(proj(torch.cat([x[..., idx], m[..., idx]], dim=-1)))
            h = sum(weights[:, i, None, None] * routed[i] for i in range(len(routed)))
            h = self.conv(h)
            h, _ = self.recurrent(h)
            h = self.to_dim(h)
            length = h.shape[1]
            mask = torch.triu(torch.ones(length, length, device=h.device, dtype=torch.bool), diagonal=1)
            h = self.temporal(h, mask=mask)
            z = self.norm(h[:, -1])
            gate = torch.softmax(self.regime_gate(z), dim=-1)
            expert = torch.stack([head(z) for head in self.experts], dim=1)
            output = (gate.unsqueeze(-1) * expert).sum(dim=1).reshape(-1, len(panel.horizons), 3)
            mu = output[..., 0] + 0.15 * self.skip(x[:, -1])
            log_sigma = output[..., 1].clamp(-5, 2)
            quantile_width = torch.nn.functional.softplus(output[..., 2])
            return mu, log_sigma, quantile_width, gate, weights

    def rank_loss(pred, target):
        pred = pred - pred.mean()
        target = target - target.mean()
        corr = (pred * target).mean() / (pred.std(unbiased=False) * target.std(unbiased=False) + 1e-6)
        return 1 - corr

    def augment_training_batch(x_batch, m_batch, hp: dict[str, Any]):
        """Train-only anti-overfit perturbations; validation/test stay untouched."""
        min_fraction = float(hp.get("min_sequence_fraction", 1.0))
        if 0.2 <= min_fraction < 1.0 and x_batch.shape[1] > 20:
            min_keep = max(20, int(x_batch.shape[1] * min_fraction))
            keep = random.randint(min_keep, x_batch.shape[1])
            x_batch = x_batch[:, -keep:]
            m_batch = m_batch[:, -keep:]
        feature_dropout = max(0.0, min(0.8, float(hp.get("feature_dropout", 0.0))))
        if feature_dropout > 0:
            keep_mask = (torch.rand(x_batch.shape[0], 1, x_batch.shape[2], device=x_batch.device) > feature_dropout).float()
            x_batch = x_batch * keep_mask
            m_batch = torch.clamp(m_batch + (1.0 - keep_mask), 0.0, 1.0)
        input_noise = max(0.0, float(hp.get("input_noise", 0.0)))
        if input_noise > 0:
            x_batch = x_batch + torch.randn_like(x_batch) * input_noise
        return x_batch, m_batch

    def robust_validation_score(valid_scores: list[float], train_scores: list[float], hp: dict[str, Any]):
        scores = np.asarray([finite(x) for x in valid_scores if np.isfinite(x)], dtype=float)
        if scores.size == 0:
            return -1.0, {"valid_rank_ic": -1.0, "valid_weakest_fold_ic": -1.0, "valid_positive_ratio": 0.0}
        mean_ic = finite(scores.mean())
        std_ic = finite(scores.std())
        positive_ratio = finite((scores > 0).mean())
        fold_count = max(1, min(int(hp.get("selection_folds", 4)), scores.size))
        fold_means = [finite(chunk.mean()) for chunk in np.array_split(scores, fold_count) if chunk.size]
        weakest_fold = finite(min(fold_means)) if fold_means else mean_ic
        fold_gap = 0.0
        if len(fold_means) >= 2:
            midpoint = max(1, len(fold_means) // 2)
            fold_gap = finite(abs(np.mean(fold_means[:midpoint]) - np.mean(fold_means[midpoint:])))
        train_mean = finite(np.mean(train_scores)) if train_scores else mean_ic
        train_valid_gap = max(0.0, train_mean - mean_ic)
        score = (
            0.45 * mean_ic
            + 0.35 * weakest_fold
            + 0.10 * positive_ratio
            - float(hp.get("validation_std_penalty", 0.20)) * std_ic
            - float(hp.get("validation_fold_gap_penalty", 0.50)) * fold_gap
            - float(hp.get("train_valid_gap_penalty", 0.60)) * train_valid_gap
        )
        return finite(score), {
            "valid_rank_ic": mean_ic,
            "valid_rank_ic_std": std_ic,
            "valid_weakest_fold_ic": weakest_fold,
            "valid_positive_ratio": positive_ratio,
            "valid_fold_gap": fold_gap,
            "train_rank_ic_mean": train_mean,
            "train_valid_gap": finite(train_valid_gap),
        }

    def fit_one(hp: dict[str, Any], seed: int, epochs: int, trial_label: str):
        random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
        model = RoutedLSTM(hp).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=float(hp["learning_rate"]), weight_decay=float(hp["weight_decay"]), betas=(0.9, 0.95))
        train_ds, valid_ds = DateDataset("train"), DateDataset("valid")
        best_state, best_score, history = None, -1e9, []
        no_improve = 0
        patience = max(1, int(hp.get("patience", 4)))
        min_delta = float(hp.get("min_delta", 5e-4))
        prev_score = None
        for epoch in range(max(1, epochs)):
            model.train(); losses = []; train_scores = []
            for x, m, y, mask, exposures in torch.utils.data.DataLoader(train_ds, batch_size=1, shuffle=False):
                x, m, y, mask, exposures = x[0].to(device), m[0].to(device), y[0].to(device), mask[0].to(device), exposures[0].to(device)
                if mask.sum() < 30: continue
                train_x, train_m = augment_training_batch(x[mask], m[mask], hp)
                mu, log_sigma, width, gate, router = model(train_x, train_m)
                yy = y[mask]
                huber = torch.nn.functional.smooth_l1_loss(mu, yy)
                nll = (0.5 * torch.exp(-2 * log_sigma) * (yy - mu).pow(2) + log_sigma).mean()
                ranking = torch.stack([rank_loss(mu[:, i], yy[:, i]) for i in range(len(panel.horizons))]).mean()
                sign = torch.nn.functional.binary_cross_entropy_with_logits(mu * 12, (yy > 0).float())
                exposure_penalty = torch.stack([torch.abs(torch.corrcoef(torch.stack([mu[:, 0], exposures[mask, j]]))[0, 1]) for j in range(exposures.shape[1])]).nanmean()
                turnover = torch.tensor(0.0, device=device)
                current_score = mu[:, 0]
                if prev_score is not None and len(prev_score) == len(current_score):
                    turnover = (current_score - prev_score).abs().mean()
                prev_score = current_score.detach()
                balance = (gate.mean(0) * torch.log(gate.mean(0) + 1e-8)).sum() + (router.mean(0) * torch.log(router.mean(0) + 1e-8)).sum()
                loss = .34 * ranking + .24 * huber + .14 * nll + .08 * sign + .08 * turnover + .08 * exposure_penalty + .04 * balance
                train_rank_ic = safe_corr(rankdata(mu[:, 0].detach().cpu().numpy()), rankdata(yy[:, 0].detach().cpu().numpy()))
                if np.isfinite(train_rank_ic):
                    train_scores.append(train_rank_ic)
                optimizer.zero_grad(set_to_none=True); loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), float(hp["grad_clip"]))
                optimizer.step(); losses.append(finite(loss.item()))
            valid_scores = []
            model.eval()
            with torch.no_grad():
                for x, m, y, mask, _ in torch.utils.data.DataLoader(valid_ds, batch_size=1, shuffle=False):
                    x, m, y, mask = x[0].to(device), m[0].to(device), y[0].numpy(), mask[0].numpy().astype(bool)
                    if mask.sum() < 30: continue
                    mu = model(x[mask], m[mask])[0].cpu().numpy()
                    score = safe_corr(rankdata(mu[:, 0]), rankdata(y[mask, 0]))
                    valid_scores.append(score)
            selection_score, validation_detail = robust_validation_score(valid_scores, train_scores, hp)
            history.append({"epoch": epoch + 1, "train_loss": finite(np.mean(losses)), "valid_selection_score": selection_score, **validation_detail})
            if selection_score > best_score + min_delta:
                best_score = selection_score
                no_improve = 0
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            else:
                no_improve += 1
                if no_improve >= patience:
                    history[-1]["early_stop"] = True
                    history[-1]["patience"] = patience
                    break
        if best_state: model.load_state_dict(best_state)
        return model, best_score, history

    search = config.get("search", {})
    rng = random.Random(int(config.get("seed", 20260720)))
    n_trials = max(1, int(search.get("trials", 3)))
    trial_epochs = max(1, int(search.get("trial_epochs", 2)))
    base_hp = {
        "hidden_dim": int(config.get("hidden_dim", 128)),
        "recurrent_layers": int(config.get("gru_layers", config.get("lstm_layers", 2))),
        "attention_layers": int(config.get("attention_layers", 2)), "heads": int(config.get("heads", 8)),
        "experts": int(config.get("experts", 4)), "dropout": float(config.get("dropout", .20)),
        "learning_rate": float(config.get("learning_rate", 3e-4)), "weight_decay": float(config.get("weight_decay", 1e-4)),
        "grad_clip": float(config.get("grad_clip", 1.0)), "recurrent_cell": recurrent_cell,
        "patience": int(config.get("patience", 4)), "min_delta": float(config.get("min_delta", 5e-4)),
        "feature_dropout": float(config.get("feature_dropout", .08)),
        "input_noise": float(config.get("input_noise", .015)),
        "min_sequence_fraction": float(config.get("min_sequence_fraction", .78)),
        "selection_folds": int(config.get("selection_folds", 4)),
        "validation_std_penalty": float(config.get("validation_std_penalty", .20)),
        "validation_fold_gap_penalty": float(config.get("validation_fold_gap_penalty", .50)),
        "train_valid_gap_penalty": float(config.get("train_valid_gap_penalty", .60)),
    }
    candidates = []
    for trial in range(n_trials):
        hp = dict(base_hp)
        if trial:
            hp.update({
                "hidden_dim": rng.choice([64, 96, 128, 160]), "recurrent_layers": rng.choice([2, 3]),
                "attention_layers": rng.choice([1, 2, 3]), "heads": rng.choice([4, 8]),
                "experts": rng.choice([3, 4, 6]), "dropout": rng.choice([.12, .18, .24, .30]),
                "learning_rate": 10 ** rng.uniform(-4.1, -3.1), "weight_decay": 10 ** rng.uniform(-5.5, -3.3),
                "feature_dropout": rng.choice([.04, .08, .12, .16]),
                "input_noise": rng.choice([.005, .010, .015, .020]),
                "min_sequence_fraction": rng.choice([.72, .78, .84, .90]),
                "train_valid_gap_penalty": rng.choice([.45, .60, .80]),
            })
            if hp["hidden_dim"] % hp["heads"]: hp["heads"] = 4
        progress(progress_path, f"{recurrent_cell}_search", .20 + .18 * trial / n_trials, f"嵌套净化搜索 {trial + 1}/{n_trials}", trial=trial + 1)
        model, score, history = fit_one(hp, int(config.get("seed", 20260720)) + trial, trial_epochs, f"trial-{trial}")
        best_epoch = max(history, key=lambda item: item.get("valid_selection_score", -1e9)) if history else {}
        candidates.append({"hp": hp, "valid_selection_score": score, "valid_rank_ic": best_epoch.get("valid_rank_ic", score), "history": history, "best_epoch": best_epoch})
        del model
    candidates.sort(key=lambda x: x["valid_selection_score"], reverse=True)
    best_hp = candidates[0]["hp"]
    seeds = [int(config.get("seed", 20260720)) + i * 97 for i in range(max(1, int(config.get("ensemble_seeds", 3))))]
    final_epochs = max(1, int(config.get("epochs", 8)))
    split_predictions: dict[str, list[np.ndarray]] = {"train": [], "valid": [], "test": []}
    histories = []
    for index, seed in enumerate(seeds):
        progress(progress_path, f"{recurrent_cell}_ensemble", .40 + .32 * index / len(seeds), f"训练深度集成 seed {index + 1}/{len(seeds)}", seed=seed)
        model, _, history = fit_one(best_hp, seed, final_epochs, f"seed-{seed}")
        histories.append({"seed": seed, "history": history})
        model.eval()
        with torch.no_grad():
            for split_name in split_predictions:
                preds = []
                for x, m, y, mask, _ in torch.utils.data.DataLoader(DateDataset(split_name), batch_size=1, shuffle=False):
                    x, m = x[0].to(device), m[0].to(device)
                    preds.append(model(x, m)[0].cpu().numpy())
                split_predictions[split_name].append(np.stack(preds) if preds else np.empty((0, len(panel.assets), len(panel.horizons))))
        del model
    metrics, predictions = {}, {}
    for split_name, arrays in split_predictions.items():
        if not arrays: continue
        mean = np.mean(np.stack(arrays), axis=0)
        std = np.std(np.stack(arrays), axis=0)
        # Dataset can start after split start because of the lookback guard.
        start, end = panel.split[split_name]
        effective_start = max(sequence, start)
        original = panel.split[split_name]
        panel.split[split_name] = (effective_start, effective_start + len(mean))
        sf = split_frame(panel, split_name, mean[..., 0], 0)
        panel.split[split_name] = original
        bt = backtest_cross_section(sf, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])
        metrics[split_name] = bt
        predictions[split_name] = {"mean_uncertainty": finite(np.mean(std[..., 0])), "dates": [x["date"] for x in bt["series"]], "rank_ic": [x["rank_ic"] for x in bt["series"]], "net": [x["net"] for x in bt["series"]]}
    trials = n_trials + len(seeds)
    metrics["test"].update(deflated_sharpe_proxy(metrics["test"]["sharpe"], metrics["test"]["observations"], trials))
    gates = gate_results(metrics, trials)
    recurrent_label = "GRU" if recurrent_cell == "gru" else "LSTM"
    recurrent_component = "gated_recurrent_unit" if recurrent_cell == "gru" else "projected_lstm"
    return {
        "engine": recurrent_cell, "engine_version": ENGINE_VERSION, "device": str(device),
        "architecture": {
            "name": f"PIT-Masked Causal Mixture Residual {recurrent_label}",
            "components": ["domain_variable_router", "multi_scale_causal_depthwise_conv", recurrent_component, "causal_transformer_attention", "regime_mixture_of_experts", "multi_horizon_gaussian_quantile_heads", "train_only_time_feature_perturbation", "weak_fold_ic_early_stopping"],
            "best_hyperparameters": best_hp, "parameter_count_policy": "recorded_at_runtime",
        },
        "search": {"method": "purged_successive_halving", "candidates": candidates, "seeds": seeds, "trial_count": trials},
        "loss": {"rank": .34, "huber": .24, "heteroscedastic_nll": .14, "sign": .08, "turnover": .08, "exposure": .08, "router_balance": .04, "selection": "mean_valid_ic + weakest_fold_ic + positive_ratio - volatility - train_valid_gap"},
        "metrics": metrics, "predictions": predictions, "gates": gates, "training_history": histories,
        "scaler_hash": hashlib.sha256(np.asarray(scaler).tobytes()).hexdigest(),
    }



def run_gru(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    gru_config = dict(config)
    gru_config["engine"] = "gru"
    gru_config["recurrent_cell"] = "gru"
    # GRU is kept as a lighter, faster medium-horizon challenger instead of a
    # parameter-identical LSTM clone. Explicit user/runtime values still win
    # through the backend preset, while direct worker invocations get a sane
    # short-path profile.
    gru_config.setdefault("sequence_length", 120)
    gru_config.setdefault("hidden_dim", 96)
    gru_config.setdefault("gru_layers", 2)
    gru_config.setdefault("attention_layers", 2)
    gru_config.setdefault("heads", 4)
    gru_config.setdefault("experts", 4)
    gru_config.setdefault("dropout", 0.22)
    gru_config.setdefault("patience", 5)
    result = run_lstm(panel, gru_config, progress_path)
    result.setdefault("model_board", {})
    result["model_board"].update({
        "模型定位": "GRU 中短周期路径因子挖掘器",
        "核心区别": "更新门/重置门替代 LSTM 的输入门/遗忘门/输出门，参数更少，更适合中短期量价和资金路径",
        "正式状态": "已拥有独立 engine 输出；晋级仍需训练/验证选择与测试一次性报告通过",
    })
    return result


UNARY_TOKENS = [
    "NEG", "ABS", "SLOG", "CS_RANK",
    "TS_Z20", "TS_Z60", "DELTA5", "DELTA20", "DECAY10", "DECAY20",
]
BINARY_TOKENS = ["ADD", "SUB", "MUL", "DIV"]


def cross_rank(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    return values.groupby(frame["trade_date"]).rank(pct=True) - 0.5


def time_op(frame: pd.DataFrame, values: pd.Series, op: str) -> pd.Series:
    g = values.groupby(frame["ts_code"])
    if op in {"TS_Z20", "TS_Z60"}:
        window = 20 if op == "TS_Z20" else 60
        min_periods = 12 if window == 20 else 36
        mean = g.rolling(window, min_periods=min_periods).mean().reset_index(level=0, drop=True)
        std = g.rolling(window, min_periods=min_periods).std().reset_index(level=0, drop=True)
        return (values - mean) / std.replace(0, np.nan)
    if op == "DELTA5": return values - g.shift(5)
    if op == "DELTA20": return values - g.shift(20)
    if op == "DECAY10": return g.rolling(10, min_periods=6).mean().reset_index(level=0, drop=True)
    if op == "DECAY20": return g.rolling(20, min_periods=12).mean().reset_index(level=0, drop=True)
    raise ValueError(op)


def evaluate_postfix(frame: pd.DataFrame, tokens: list[str]) -> pd.Series:
    stack: list[pd.Series] = []
    for token in tokens:
        if token in FEATURES:
            stack.append(frame[token].astype(float))
        elif token in UNARY_TOKENS:
            if not stack: raise ValueError("unary stack underflow")
            x = stack.pop()
            if token == "NEG": y = -x
            elif token == "ABS": y = x.abs()
            elif token == "SLOG": y = np.sign(x) * np.log1p(x.abs())
            elif token == "CS_RANK": y = cross_rank(frame, x)
            else: y = time_op(frame, x, token)
            stack.append(y.replace([np.inf, -np.inf], np.nan))
        elif token in BINARY_TOKENS:
            if len(stack) < 2: raise ValueError("binary stack underflow")
            b, a = stack.pop(), stack.pop()
            if token == "ADD": y = a + b
            elif token == "SUB": y = a - b
            elif token == "MUL": y = a.clip(-8, 8) * b.clip(-8, 8)
            else: y = a / b.where(b.abs() > 1e-6)
            stack.append(y.replace([np.inf, -np.inf], np.nan))
        else:
            raise ValueError(f"unknown token {token}")
    if len(stack) != 1: raise ValueError("formula stack is not singular")
    return stack[0]


def formula_complexity(tokens: list[str]) -> float:
    return len(tokens) + 1.5 * sum(x in UNARY_TOKENS for x in tokens) + 2.0 * sum(x in BINARY_TOKENS for x in tokens)


def formula_ic_stability(raw_ic: pd.Series) -> dict[str, float]:
    values = pd.to_numeric(raw_ic, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return {
            "fold_count": 0.0,
            "weakest_fold_rank_ic": 0.0,
            "median_fold_rank_ic": 0.0,
            "ic_positive_ratio": 0.0,
            "ic_stability_gap": 1.0,
        }
    fold_count = max(1, min(4, len(values)))
    folds = np.array_split(values.to_numpy(float), fold_count)
    fold_means = [finite(np.mean(fold)) for fold in folds if len(fold)]
    weakest = min(fold_means) if fold_means else 0.0
    median = float(np.median(fold_means)) if fold_means else 0.0
    gap = max(fold_means) - min(fold_means) if len(fold_means) > 1 else 0.0
    return {
        "fold_count": float(len(fold_means)),
        "weakest_fold_rank_ic": finite(weakest),
        "median_fold_rank_ic": finite(median),
        "ic_positive_ratio": finite(float((values > 0).mean())),
        "ic_stability_gap": finite(gap),
    }


def formula_reward(frame: pd.DataFrame, tokens: list[str], target: str, cost_bps: float, fidelity: float = 1.0) -> tuple[float, dict[str, Any]]:
    try:
        values = evaluate_postfix(frame, tokens)
    except Exception:
        return -1.0, {"invalid": True}
    work = frame[["trade_date", "ts_code", target, "ret_20", "value_bp", "log_mv"]].copy()
    work["score"] = values
    if fidelity < 1:
        dates = sorted(work.trade_date.unique())
        keep = set(dates[:: max(1, int(1 / max(.05, fidelity)))])
        work = work[work.trade_date.isin(keep)]
    work = work.dropna(subset=["score", target])
    coverage = len(work) / max(1, len(frame))
    if coverage < .55:
        return -.8 + .2 * coverage, {"coverage": coverage, "invalid": True}
    raw_ic = work.groupby("trade_date").apply(lambda g: safe_corr(rankdata(g.score.to_numpy()), rankdata(g[target].to_numpy())), include_groups=False)
    stability = formula_ic_stability(raw_ic)
    dates = sorted(work.trade_date.unique())
    cut = dates[len(dates) // 2] if dates else ""
    early = finite(raw_ic[raw_ic.index <= cut].mean()) if len(raw_ic) else 0.0
    late = finite(raw_ic[raw_ic.index > cut].mean()) if len(raw_ic) else 0.0
    # Residual contribution against two broad existing factor dimensions.
    sample = work.dropna(subset=["ret_20", "value_bp", "log_mv"]).copy()
    if len(sample) > 100:
        x = sample[["ret_20", "value_bp", "log_mv"]].to_numpy(float)
        x = np.column_stack([np.ones(len(x)), np.nan_to_num(x)])
        beta = np.linalg.lstsq(x, sample.score.to_numpy(float), rcond=1e-6)[0]
        sample["residual"] = sample.score.to_numpy(float) - x @ beta
        residual_ic = sample.groupby("trade_date").apply(lambda g: safe_corr(rankdata(g.residual.to_numpy()), rankdata(g[target].to_numpy())), include_groups=False).mean()
    else:
        residual_ic = 0.0
    bt = backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost_bps, 5)
    turnover = bt["turnover"]
    redundancy = max(abs(safe_corr(work.score.to_numpy(), work.ret_20.to_numpy())), abs(safe_corr(work.score.to_numpy(), work.value_bp.to_numpy())))
    weakest = min(early, late)
    drawdown_excess = max(0.0, abs(min(0.0, finite(bt.get("max_drawdown")))) - 0.18)
    reward = (
        7.5 * weakest
        + 2.5 * stability["weakest_fold_rank_ic"]
        + 4.5 * finite(residual_ic)
        + .18 * bt["sharpe"]
        + .32 * stability["ic_positive_ratio"]
        - .20 * turnover
        - .25 * redundancy
        - .40 * stability["ic_stability_gap"]
        - .75 * drawdown_excess
        - .010 * formula_complexity(tokens)
    )
    detail = {
        "early_rank_ic": early, "late_rank_ic": late,
        "residual_rank_ic": finite(residual_ic), "coverage": coverage,
        "turnover": turnover, "max_correlation": redundancy,
        "valid_sharpe": bt["sharpe"], "max_drawdown": bt.get("max_drawdown"),
        "reward": finite(reward), "invalid": False,
        **stability,
    }
    return finite(reward), detail


def run_rl_transformer(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    torch, nn = torch_modules()
    device = torch.device("cuda" if torch.cuda.is_available() and config.get("allow_cuda", True) else "cpu")
    seed = int(config.get("seed", 20260720)); random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    vocab = ["PAD", "BOS", "STOP", *FEATURES, *UNARY_TOKENS, *BINARY_TOKENS]
    token_id = {x: i for i, x in enumerate(vocab)}
    operands = set(FEATURES); unary = set(UNARY_TOKENS); binary = set(BINARY_TOKENS)
    max_steps = int(config.get("max_formula_tokens", 14))
    d_model = int(config.get("d_model", 128)); heads = int(config.get("heads", 8)); layers = int(config.get("layers", 4))

    class ActorCritic(nn.Module):
        def __init__(self):
            super().__init__()
            self.token = nn.Embedding(len(vocab), d_model)
            self.position = nn.Embedding(max_steps + 2, d_model)
            layer = nn.TransformerEncoderLayer(d_model, heads, d_model * 4, float(config.get("dropout", .15)), batch_first=True, norm_first=True, activation="gelu")
            self.backbone = nn.TransformerEncoder(layer, num_layers=layers, enable_nested_tensor=False)
            self.policy = nn.Linear(d_model, len(vocab)); self.value = nn.Linear(d_model, 1)

        def forward(self, tokens):
            pos = torch.arange(tokens.shape[1], device=tokens.device)[None]
            x = self.token(tokens) + self.position(pos)
            length = tokens.shape[1]
            mask = torch.triu(torch.ones(length, length, device=tokens.device, dtype=torch.bool), diagonal=1)
            h = self.backbone(x, mask=mask)[:, -1]
            return self.policy(h), self.value(h).squeeze(-1)

    model = ActorCritic().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.get("learning_rate", 2e-4)), weight_decay=float(config.get("weight_decay", 1e-4)))
    all_frame = panel.frame[["trade_date", "ts_code", *FEATURES, f"target_{panel.horizons[0]}"]].copy()
    train_start, train_end = panel.split["train"]; valid_start, valid_end = panel.split["valid"]; test_start, test_end = panel.split["test"]
    train_dates = set(panel.dates[train_start:train_end]); valid_dates = set(panel.dates[valid_start:valid_end]); test_dates = set(panel.dates[test_start:test_end])
    train_frame = all_frame[all_frame.trade_date.isin(train_dates)].reset_index(drop=True)
    valid_frame = all_frame[all_frame.trade_date.isin(valid_dates)].reset_index(drop=True)
    test_frame = all_frame[all_frame.trade_date.isin(test_dates)].reset_index(drop=True)
    target = f"target_{panel.horizons[0]}"
    reward_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    archive: dict[str, dict[str, Any]] = {}

    def legal_mask(stack_depth: int, step: int) -> np.ndarray:
        mask = np.zeros(len(vocab), dtype=bool)
        for token in FEATURES: mask[token_id[token]] = True
        if stack_depth >= 1:
            for token in UNARY_TOKENS: mask[token_id[token]] = True
            mask[token_id["STOP"]] = True
        if stack_depth >= 2:
            for token in BINARY_TOKENS: mask[token_id[token]] = True
        if step >= max_steps - 1:
            mask[:] = False
            if stack_depth == 1: mask[token_id["STOP"]] = True
            elif stack_depth >= 2:
                for token in BINARY_TOKENS: mask[token_id[token]] = True
            else:
                for token in FEATURES: mask[token_id[token]] = True
        return mask

    def sample_episode(fidelity: float):
        ids = [token_id["BOS"]]; formula: list[str] = []; stack = 0; transitions = []
        for step in range(max_steps):
            state = torch.tensor([ids], dtype=torch.long, device=device)
            logits, value = model(state)
            legal = legal_mask(stack, step)
            masked = logits[0].masked_fill(~torch.tensor(legal, device=device), -1e9)
            dist = torch.distributions.Categorical(logits=masked)
            action = dist.sample(); action_id = int(action.item()); token = vocab[action_id]
            transitions.append({"state": list(ids), "action": action_id, "old_logp": finite(dist.log_prob(action).item()), "value": finite(value.item()), "legal": legal.tolist()})
            ids.append(action_id)
            if token == "STOP": break
            formula.append(token)
            if token in operands: stack += 1
            elif token in binary: stack -= 1
        if stack != 1: reward, detail = -1.0, {"invalid": True, "reason": "non_singular_stack"}
        else:
            key = " ".join(formula)
            if key not in reward_cache:
                train_reward, train_detail = formula_reward(train_frame, formula, target, float(config.get("cost_bps", 15)), fidelity)
                valid_reward, valid_detail = formula_reward(valid_frame, formula, target, float(config.get("cost_bps", 15)), fidelity)
                reward = min(train_reward, valid_reward) + .35 * valid_reward
                detail = {"train": train_detail, "valid": valid_detail, "reward": finite(reward)}
                reward_cache[key] = (reward, detail)
            reward, detail = reward_cache[key]
            complexity_bucket = min(4, len(formula) // 4)
            domain_bucket = next((name for name, cols in DOMAINS.items() if any(x in cols for x in formula)), "mixed")
            cell = f"{complexity_bucket}:{domain_bucket}"
            current = archive.get(cell)
            if current is None or reward > current["reward"]:
                archive[cell] = {"formula": formula, "reward": reward, "detail": detail}
        return transitions, formula, finite(reward), detail

    episodes = max(8, int(config.get("episodes", 160)))
    rollout = max(4, int(config.get("rollout_batch", 32)))
    ppo_epochs = max(1, int(config.get("ppo_epochs", 3)))
    gamma = float(config.get("gamma", .99)); clip = float(config.get("ppo_clip", .2)); entropy_coef = float(config.get("entropy", .01)); value_coef = float(config.get("value_coef", .5))
    training_curve = []
    episode_count = 0
    while episode_count < episodes:
        batch = []
        fidelity = .35 if episode_count < episodes * .55 else 1.0
        rewards = []
        for _ in range(min(rollout, episodes - episode_count)):
            transitions, formula, reward, detail = sample_episode(fidelity)
            returns = []
            running = reward
            for _step in reversed(transitions):
                returns.append(running); running *= gamma
            returns.reverse()
            for transition, ret in zip(transitions, returns):
                transition["return"] = ret; batch.append(transition)
            rewards.append(reward); episode_count += 1
        for _ in range(ppo_epochs):
            random.shuffle(batch)
            for transition in batch:
                state = torch.tensor([transition["state"]], dtype=torch.long, device=device)
                logits, value = model(state)
                legal = torch.tensor(transition["legal"], dtype=torch.bool, device=device)
                masked = logits[0].masked_fill(~legal, -1e9)
                dist = torch.distributions.Categorical(logits=masked)
                action = torch.tensor(transition["action"], device=device)
                logp = dist.log_prob(action)
                advantage = torch.tensor(transition["return"] - transition["value"], dtype=torch.float32, device=device)
                ratio = torch.exp(logp - transition["old_logp"])
                policy_loss = -torch.min(ratio * advantage, torch.clamp(ratio, 1 - clip, 1 + clip) * advantage)
                value_loss = (value[0] - transition["return"]) ** 2
                loss = policy_loss + value_coef * value_loss - entropy_coef * dist.entropy()
                optimizer.zero_grad(set_to_none=True); loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), .5); optimizer.step()
        training_curve.append({"episodes": episode_count, "mean_reward": finite(np.mean(rewards)), "best_reward": max((x["reward"] for x in archive.values()), default=-1.0), "unique_formulas": len(reward_cache), "archive_cells": len(archive), "fidelity": fidelity})
        progress(progress_path, "rl_ppo", .20 + .57 * episode_count / episodes, f"PPO+GAE 公式搜索 {episode_count}/{episodes}", unique_formulas=len(reward_cache), archive_cells=len(archive))
    ranked = sorted(archive.values(), key=lambda x: x["reward"], reverse=True)[: min(12, len(archive))]
    final_candidates = []
    for item in ranked:
        formula = item["formula"]
        train_reward, train_detail = formula_reward(train_frame, formula, target, float(config.get("cost_bps", 15)), 1.0)
        valid_reward, valid_detail = formula_reward(valid_frame, formula, target, float(config.get("cost_bps", 15)), 1.0)
        test_reward, test_detail = formula_reward(test_frame, formula, target, float(config.get("cost_bps", 15)), 1.0)
        score = evaluate_postfix(test_frame, formula)
        test_work = test_frame[["trade_date", "ts_code", target]].copy(); test_work["score"] = score
        bt = backtest_cross_section(test_work.rename(columns={target: "target"}), "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])
        final_candidates.append({"formula_postfix": formula, "formula": " ".join(formula), "train": train_detail, "valid": valid_detail, "test": test_detail, "test_backtest": bt, "selection_reward": finite(min(train_reward, valid_reward) + .35 * valid_reward), "test_report_only_reward": test_reward})
    trials = max(1, len(reward_cache))
    best = final_candidates[0] if final_candidates else {"test_backtest": {"sharpe": 0.0, "observations": 0}}
    best["test_backtest"].update(deflated_sharpe_proxy(best["test_backtest"].get("sharpe", 0.0), best["test_backtest"].get("observations", 0), trials))
    metrics = {"test": best["test_backtest"], "valid": {"rank_ic": finite((best.get("valid") or {}).get("late_rank_ic")), "sharpe": finite((best.get("valid") or {}).get("valid_sharpe"))}}
    return {
        "engine": "rl_transformer", "engine_version": ENGINE_VERSION, "device": str(device),
        "architecture": {"name": "Grammar-Constrained Synergistic Formula Transformer", "layers": layers, "d_model": d_model, "heads": heads, "vocabulary": len(vocab), "components": ["causal_transformer_actor", "critic_value_head", "postfix_ast_environment", "hard_type_stack_mask", "ppo_clipped_objective", "multi_fidelity_reward", "quality_diversity_archive"]},
        "search": {"episodes": episodes, "rollout_batch": rollout, "ppo_epochs": ppo_epochs, "unique_formulas": len(reward_cache), "archive_cells": len(archive), "trial_count": trials},
        "reward": {"weakest_fold_rank_ic": 8.0, "residual_rank_ic": 5.0, "net_sharpe": .20, "turnover": -.18, "redundancy": -.22, "complexity": -.008},
        "training_curve": training_curve, "candidates": final_candidates, "metrics": metrics,
        "gates": gate_results(metrics, trials), "test_used_for_search": False,
    }

def purged_expanding_oof_predictions(
    model: Any,
    features: np.ndarray,
    target: np.ndarray,
    dates: np.ndarray,
    transform: Any,
    horizon: int,
    max_samples: int,
    folds: int = 4,
    initial_fraction: float = 0.40,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Create purged chronological predictions for development evaluation."""

    from sklearn.base import clone

    x = np.asarray(features)
    y = np.asarray(target, dtype=float)
    date_strings = np.asarray(dates).astype(str)
    if not (len(x) == len(y) == len(date_strings)):
        raise ValueError("oof_input_length_mismatch")
    predictions = np.full(len(y), np.nan, dtype=float)
    unique_dates = np.unique(date_strings)
    date_positions = np.searchsorted(unique_dates, date_strings)
    horizon = max(1, int(horizon))
    folds = max(2, int(folds))
    initial_date_count = max(
        horizon + 2,
        int(math.ceil(len(unique_dates) * float(initial_fraction))),
    )
    diagnostics: dict[str, Any] = {
        "method": "purged_expanding_window_oof",
        "uses_in_sample_prediction": False,
        "purge_trading_dates": horizon,
        "requested_folds": folds,
        "initial_calibration_date_count": initial_date_count,
        "total_date_count": int(len(unique_dates)),
        "folds": [],
    }
    if initial_date_count >= len(unique_dates):
        diagnostics.update({
            "status": "insufficient_dates",
            "predicted_date_count": 0,
            "prediction_coverage": 0.0,
        })
        return predictions, diagnostics
    boundaries = np.unique(
        np.linspace(initial_date_count, len(unique_dates), folds + 1).astype(int)
    )
    for prediction_start, prediction_end in zip(boundaries[:-1], boundaries[1:]):
        training_end = int(prediction_start) - horizon
        train_mask = date_positions < training_end
        prediction_mask = (
            (date_positions >= int(prediction_start))
            & (date_positions < int(prediction_end))
        )
        fit_x = x[train_mask]
        fit_y = y[train_mask]
        if len(fit_x) == 0 or not np.any(prediction_mask):
            continue
        if len(fit_x) > max_samples:
            sample_index = np.linspace(
                0, len(fit_x) - 1, max_samples
            ).astype(int)
            fit_x, fit_y = fit_x[sample_index], fit_y[sample_index]
        fold_model = clone(model)
        fold_model.fit(transform(fit_x), fit_y)
        predictions[prediction_mask] = fold_model.predict(
            transform(x[prediction_mask])
        )
        diagnostics["folds"].append({
            "training_end": str(unique_dates[training_end - 1]),
            "prediction_start": str(unique_dates[prediction_start]),
            "prediction_end": str(unique_dates[prediction_end - 1]),
            "training_rows": int(len(fit_y)),
            "prediction_rows": int(np.sum(prediction_mask)),
        })
    predicted_date_count = int(
        len(np.unique(date_strings[np.isfinite(predictions)]))
    )
    diagnostics.update({
        "status": "ready" if predicted_date_count else "insufficient_dates",
        "predicted_date_count": predicted_date_count,
        "prediction_coverage": finite(
            predicted_date_count / max(1, len(unique_dates))
        ),
    })
    return predictions, diagnostics


def robust_development_score(candidate: dict[str, Any]) -> float:
    """Non-compensatory train-OOF/validation score; test is never inspected."""

    train = candidate.get("train") or {}
    valid = candidate.get("valid") or {}
    train_sharpe = finite(train.get("sharpe"))
    valid_sharpe = finite(valid.get("sharpe"))
    if (
        train_sharpe <= 0.0
        or valid_sharpe <= 0.0
        or finite(train.get("rank_ic")) <= 0.0
        or finite(valid.get("rank_ic")) <= 0.0
        or finite(valid.get("hit_rate")) <= 0.50
    ):
        return -math.inf
    return finite(
        2.0 * train_sharpe * valid_sharpe / (train_sharpe + valid_sharpe)
    )





def run_strategy(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    from sklearn.linear_model import ElasticNet, LinearRegression, Lasso, Ridge
    from sklearn.neural_network import MLPRegressor
    try:
        from adaptive_icir import causal_rolling_icir_scores
    except ImportError:
        from model.factor_laboratory.adaptive_icir import causal_rolling_icir_scores
    values, _, _ = normalise_panel(panel)
    ranked_values = cross_sectional_rank_features(panel)
    horizon_i = 0
    def flat(split_name, matrix, rank_target=False):
        start, end = panel.split[split_name]
        x = matrix[start:end].reshape(-1, len(panel.feature_names))
        y = panel.targets[start:end, :, horizon_i].reshape(-1)
        date = np.repeat(np.array(panel.dates[start:end]), len(panel.assets))
        asset = np.tile(np.array(panel.assets), end - start)
        mask = np.isfinite(y) & panel.valid[start:end].reshape(-1)
        target = _rank_targets_by_date(y[mask], date[mask]) if rank_target else y[mask]
        return x[mask], target, y[mask], date[mask], asset[mask]
    base_data = {split: flat(split, values) for split in ("train", "valid", "test")}
    rank_data = {split: flat(split, ranked_values, rank_target=True) for split in ("train", "valid", "test")}
    domain_timed_feature_indices = [
        index for index, name in enumerate(panel.feature_names)
        if str(name).startswith("factor_timing_") or str(name).startswith("factor_domain_")
    ]
    adaptive_ranked_values = ranked_values
    adaptive_feature_scope = "all_features"
    if domain_timed_feature_indices and bool(config.get("domain_timing_isolate_adaptive_base", True)):
        adaptive_base_indices = [
            index for index in range(len(panel.feature_names))
            if index not in set(domain_timed_feature_indices)
        ]
        if adaptive_base_indices:
            adaptive_ranked_values = ranked_values[:, :, adaptive_base_indices]
            adaptive_feature_scope = "base_without_domain_timed_features"
    adaptive_score_panel, adaptive_icir_report = causal_rolling_icir_scores(
        adaptive_ranked_values,
        panel.targets[:, :, horizon_i],
        panel.valid,
        panel.horizons[horizon_i],
        lookback_periods=48,
        min_periods=12,
    )
    adaptive_icir_report["feature_scope"] = adaptive_feature_scope
    adaptive_icir_report["domain_timed_feature_count_excluded_from_base"] = len(domain_timed_feature_indices) if adaptive_feature_scope == "base_without_domain_timed_features" else 0
    feature_index = {
        name: index for index, name in enumerate(panel.feature_names)
    }
    domain_indices = [
        [
            feature_index[name]
            for name in members
            if name in feature_index
        ]
        for members in DOMAINS.values()
    ]
    domain_indices = [indices for indices in domain_indices if indices]
    core_indices = [feature_index[name] for name in FEATURES if name in feature_index]
    legacy_indices = [feature_index[name] for name in LEGACY_FEATURES if name in feature_index]
    external_feature_count = max(0, len(panel.feature_names) - len(core_indices))

    def domain_projection(values_: np.ndarray) -> np.ndarray:
        return np.column_stack([
            np.nanmean(values_[:, indices], axis=1)
            for indices in domain_indices
        ])

    max_samples = int(config.get("max_training_samples", 240000))
    models = {
        "incumbent_ols": {"model": LinearRegression(), "data": base_data, "projection": False, "neutralize": False, "feature_indices": legacy_indices, "feature_scope": "legacy_21"},
        "ols": {"model": LinearRegression(), "data": base_data, "projection": False, "neutralize": False, "feature_indices": core_indices, "feature_scope": "core_29"},
        "lasso": {"model": Lasso(alpha=float(config.get("lasso_alpha", 2e-5)), max_iter=10000, selection="cyclic"), "data": base_data, "projection": False, "neutralize": False, "feature_scope": "screened_unified" if external_feature_count else "core_29"},
        "domain_ridge": {"model": Ridge(alpha=1.0), "data": base_data, "projection": True, "neutralize": False, "feature_scope": "economic_domain_core"},
        "cs_ridge_neutral": {"model": Ridge(alpha=10.0), "data": rank_data, "projection": False, "neutralize": True, "feature_scope": "screened_unified" if external_feature_count else "core_29"},
        "cs_elastic_neutral": {"model": ElasticNet(alpha=1e-4, l1_ratio=0.15, max_iter=10000), "data": rank_data, "projection": False, "neutralize": True, "feature_scope": "screened_unified" if external_feature_count else "core_29"},
        "deep_mlp": {"model": MLPRegressor(hidden_layer_sizes=(256, 128, 64), activation="relu", alpha=1e-4, batch_size=1024, learning_rate_init=3e-4, max_iter=int(config.get("epochs", 20)), early_stopping=True, validation_fraction=.15, n_iter_no_change=5, random_state=int(config.get("seed", 20260720))), "data": base_data, "projection": False, "neutralize": False, "feature_scope": "screened_unified" if external_feature_count else "core_29"},
    }
    if external_feature_count:
        models["screened_ols"] = {
            "model": LinearRegression(),
            "data": base_data,
            "projection": False,
            "neutralize": False,
            "feature_scope": "screened_unified",
        }
    result = {}
    prediction = {}
    execution_candidates = {}
    execution_policies = {
        "full_exposure": {
            "risk_budget": None,
            "selection_buffer": 0.0,
            "portfolio_construction": "top_decile",
        },
        "robust_fast_slow_volatility_budget": {
            "risk_budget": {
                "name": "robust_fast_slow_volatility_budget",
                "target_volatility": float(config.get("strategy_target_volatility", 0.18)),
            },
            "selection_buffer": 0.0,
            "portfolio_construction": "top_decile",
        },
        "robust_volatility_budget_rank_buffer": {
            "risk_budget": {
                "name": "robust_fast_slow_volatility_budget",
                "target_volatility": float(config.get("strategy_target_volatility", 0.18)),
            },
            "selection_buffer": 0.5,
            "portfolio_construction": "top_decile",
        },
        "continuous_rank_volatility_budget": {
            "risk_budget": {
                "name": "robust_fast_slow_volatility_budget",
                "target_volatility": float(config.get("strategy_target_volatility", 0.18)),
            },
            "selection_buffer": 0.0,
            "portfolio_construction": "continuous_rank",
        },
        "continuous_rank_reliability_adjusted_volatility_budget": {
            "risk_budget": {
                "name": "robust_fast_slow_volatility_budget",
                "target_volatility": float(config.get("strategy_target_volatility", 0.18)),
            },
            "selection_buffer": 0.0,
            "portfolio_construction": "continuous_rank",
            "causal_signal_reliability": True,
        },
    }
    cost_aware_policy_name = "continuous_rank_cost_aware_volatility_budget"
    cost = float(config.get("cost_bps", 15))
    horizon = panel.horizons[0]
    lookup = panel.frame[
        ["trade_date", "ts_code", "industry_name", "log_mv", "vol_20"]
    ].drop_duplicates(["trade_date", "ts_code"])
    risk_lookup = lookup[["trade_date", "ts_code", "vol_20"]]
    for i, (name, model_spec) in enumerate(models.items()):
        model_progress = .25 + .30 * i / max(1, len(models) - 1)
        progress(
            progress_path, "strategy", model_progress,
            f"训练时序样本外策略模型：{name}",
        )
        model = model_spec["model"]
        data = model_spec["data"]
        xtr, ytr, realized_train, dtr, atr = data["train"]
        xv, yv, realized_valid, dv, av = data["valid"]
        xt, yt, realized_test, dt, at = data["test"]
        fit_x, fit_y = xtr, ytr
        if len(fit_x) > max_samples:
            idx = np.linspace(0, len(fit_x) - 1, max_samples).astype(int)
            fit_x, fit_y = fit_x[idx], fit_y[idx]
        if model_spec.get("feature_indices") is not None:
            selected_indices = model_spec["feature_indices"]
            transform = lambda matrix, indices=selected_indices: matrix[:, indices]
        elif model_spec["projection"]:
            transform = domain_projection
        else:
            transform = lambda matrix: matrix
        train_oof_prediction, oof_diagnostics = (
            purged_expanding_oof_predictions(
                model, xtr, ytr, dtr, transform, horizon, max_samples,
                folds=int(config.get("development_oof_folds", 4)),
                initial_fraction=float(
                    config.get("development_initial_fraction", 0.40)
                ),
            )
        )
        model.fit(transform(fit_x), fit_y)
        prediction[name] = {
            "train": train_oof_prediction,
            "valid": model.predict(transform(xv)),
            "test": model.predict(transform(xt)),
        }
        if model_spec["neutralize"]:
            for split_name, dates, assets in (
                ("train", dtr, atr), ("valid", dv, av), ("test", dt, at)
            ):
                missing_score = ~np.isfinite(prediction[name][split_name])
                meta = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": prediction[name][split_name]})
                meta = meta.merge(lookup, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
                prediction[name][split_name] = neutralize_cross_sectional_scores(meta)
                prediction[name][split_name][missing_score] = np.nan
        result[name] = {
            "development_evaluation": oof_diagnostics,
            "feature_scope": model_spec.get("feature_scope", "screened_unified"),
            "input_feature_count": int(transform(fit_x[:1]).shape[1]) if len(fit_x) else 0,
        }
        split_inputs = (
            ("train", dtr, atr, realized_train),
            ("valid", dv, av, realized_valid),
            ("test", dt, at, realized_test),
        )
        for split_name, dates, assets, target in split_inputs:
            frame = pd.DataFrame({
                "trade_date": dates,
                "ts_code": assets,
                "score": prediction[name][split_name],
                "target": target,
            })
            result[name][split_name] = backtest_cross_section(frame, "score", "target", cost, horizon)
        for policy_name, policy in execution_policies.items():
            candidate_id = f"{name}::{policy_name}"
            execution_candidates[candidate_id] = {
                "model": name,
                "execution_policy": policy_name,
            }
            for split_name, dates, assets, target in split_inputs:
                frame = pd.DataFrame({
                    "trade_date": dates,
                    "ts_code": assets,
                    "score": prediction[name][split_name],
                    "target": target,
                })
                execution_policy = execution_policies[policy_name]
                execution_candidates[candidate_id][split_name] = backtest_cross_section(
                    frame,
                    "score",
                    "target",
                    cost,
                    horizon,
                    risk_budget=execution_policy["risk_budget"],
                    selection_buffer=execution_policy["selection_buffer"],
                    portfolio_construction=execution_policy["portfolio_construction"],
                    causal_signal_reliability=bool(execution_policy.get("causal_signal_reliability", False)),
                )
    adaptive_name = "adaptive_icir_12m_neutral"
    models[adaptive_name] = {"adaptive": True}
    result[adaptive_name] = {"adaptive_icir": adaptive_icir_report}
    prediction[adaptive_name] = {}
    adaptive_inputs = {}
    for split_name in ("train", "valid", "test"):
        start, end = panel.split[split_name]
        raw_target = panel.targets[start:end, :, horizon_i].reshape(-1)
        valid_mask = (
            np.isfinite(raw_target)
            & panel.valid[start:end].reshape(-1)
        )
        raw_score = adaptive_score_panel[start:end].reshape(-1)[valid_mask]
        _, _, realized, dates, assets = base_data[split_name]
        missing_score = ~np.isfinite(raw_score)
        meta = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": raw_score,
        })
        meta = meta.merge(
            lookup,
            how="left",
            on=["trade_date", "ts_code"],
            validate="many_to_one",
        )
        neutral_score = neutralize_cross_sectional_scores(meta)
        neutral_score[missing_score] = np.nan
        prediction[adaptive_name][split_name] = neutral_score
        adaptive_inputs[split_name] = (dates, assets, realized)
        frame = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": neutral_score,
            "target": realized,
        })
        result[adaptive_name][split_name] = backtest_cross_section(
            frame, "score", "target", cost, horizon
        )
    for policy_name, policy in execution_policies.items():
        candidate_id = f"{adaptive_name}::{policy_name}"
        execution_candidates[candidate_id] = {
            "model": adaptive_name,
            "execution_policy": policy_name,
        }
        for split_name, (dates, assets, realized) in adaptive_inputs.items():
            frame = pd.DataFrame({
                "trade_date": dates,
                "ts_code": assets,
                "score": prediction[adaptive_name][split_name],
                "target": realized,
            })
            execution_candidates[candidate_id][split_name] = (
                backtest_cross_section(
                    frame,
                    "score",
                    "target",
                    cost,
                    horizon,
                    risk_budget=policy["risk_budget"],
                    selection_buffer=policy["selection_buffer"],
                    portfolio_construction=policy["portfolio_construction"],
                    causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
                )
            )
    adaptive_train_dates, adaptive_train_assets, adaptive_train_realized = (
        adaptive_inputs["train"]
    )
    adaptive_train_frame = pd.DataFrame({
        "trade_date": adaptive_train_dates,
        "ts_code": adaptive_train_assets,
        "score": prediction[adaptive_name]["train"],
        "target": adaptive_train_realized,
    })
    adaptive_rank_return_slope = estimate_rank_return_slope(
        adaptive_train_frame, "score", "target", horizon
    )
    cost_aware_candidate_id = f"{adaptive_name}::{cost_aware_policy_name}"
    execution_candidates[cost_aware_candidate_id] = {
        "model": adaptive_name,
        "execution_policy": cost_aware_policy_name,
        "training_rank_return_slope": adaptive_rank_return_slope,
        "calibration_split": "train",
    }
    for split_name, (dates, assets, realized) in adaptive_inputs.items():
        frame = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": prediction[adaptive_name][split_name],
            "target": realized,
        })
        execution_candidates[cost_aware_candidate_id][split_name] = (
            backtest_cross_section(
                frame, "score", "target", cost, horizon,
                risk_budget=execution_policies[
                    "continuous_rank_volatility_budget"
                ]["risk_budget"],
                portfolio_construction="continuous_rank",
                transaction_cost_optimized=True,
                rank_return_slope=adaptive_rank_return_slope,
            )
        )
    adaptive_cost_policy_name = (
        "continuous_rank_adaptive_cost_aware_volatility_budget"
    )
    adaptive_cost_candidate_id = (
        f"{adaptive_name}::{adaptive_cost_policy_name}"
    )
    execution_candidates[adaptive_cost_candidate_id] = {
        "model": adaptive_name,
        "execution_policy": adaptive_cost_policy_name,
        "training_rank_return_slope": adaptive_rank_return_slope,
        "calibration_split": "train_then_causal_matured_updates",
    }
    for split_name, (dates, assets, realized) in adaptive_inputs.items():
        frame = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": prediction[adaptive_name][split_name],
            "target": realized,
        })
        execution_candidates[adaptive_cost_candidate_id][split_name] = (
            backtest_cross_section(
                frame, "score", "target", cost, horizon,
                risk_budget=execution_policies[
                    "continuous_rank_volatility_budget"
                ]["risk_budget"],
                portfolio_construction="continuous_rank",
                transaction_cost_optimized=True,
                rank_return_slope=adaptive_rank_return_slope,
                adaptive_rank_return_slope=True,
            )
        )
    inverse_vol_policy_name = (
        "continuous_rank_inverse_volatility_budget"
    )
    inverse_vol_candidate_id = f"{adaptive_name}::{inverse_vol_policy_name}"
    execution_candidates[inverse_vol_candidate_id] = {
        "model": adaptive_name,
        "execution_policy": inverse_vol_policy_name,
        "risk_input": "point_in_time_vol_20",
        "calibration_split": "none",
    }
    for split_name, (dates, assets, realized) in adaptive_inputs.items():
        frame = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": prediction[adaptive_name][split_name],
            "target": realized,
        }).merge(
            risk_lookup,
            how="left",
            on=["trade_date", "ts_code"],
            validate="many_to_one",
        )
        execution_candidates[inverse_vol_candidate_id][split_name] = (
            backtest_cross_section(
                frame, "score", "target", cost, horizon,
                risk_budget=execution_policies[
                    "continuous_rank_volatility_budget"
                ]["risk_budget"],
                portfolio_construction="continuous_rank",
                asset_risk_weighted=True,
                risk_col="vol_20",
            )
        )
    hybrid_name = "incumbent_ols_adaptive_icir_rank_ensemble"
    models[hybrid_name] = {
        "fixed_ensemble": True,
        "components": ["incumbent_ols", adaptive_name],
        "weights": [0.5, 0.5],
    }
    result[hybrid_name] = {
        "method": (
            "equal cross-sectional rank ensemble of the incumbent OLS "
            "and causal adaptive ICIR score, followed by industry-size "
            "neutralization"
        ),
        "weights": {"incumbent_ols": 0.5, adaptive_name: 0.5},
        "test_usage": "never_used_for_weights_or_candidate_construction",
    }
    prediction[hybrid_name] = {}
    for split_name, (dates, assets, realized) in adaptive_inputs.items():
        combined = cross_sectional_rank_ensemble(
            [
                prediction["incumbent_ols"][split_name],
                prediction[adaptive_name][split_name],
            ],
            dates,
        )
        meta = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": combined,
        })
        meta = meta.merge(
            lookup,
            how="left",
            on=["trade_date", "ts_code"],
            validate="many_to_one",
        )
        neutral_score = neutralize_cross_sectional_scores(meta)
        prediction[hybrid_name][split_name] = neutral_score
        frame = pd.DataFrame({
            "trade_date": dates,
            "ts_code": assets,
            "score": neutral_score,
            "target": realized,
        })
        result[hybrid_name][split_name] = backtest_cross_section(
            frame, "score", "target", cost, horizon
        )
    for policy_name, policy in execution_policies.items():
        candidate_id = f"{hybrid_name}::{policy_name}"
        execution_candidates[candidate_id] = {
            "model": hybrid_name,
            "execution_policy": policy_name,
        }
        for split_name, (dates, assets, realized) in adaptive_inputs.items():
            frame = pd.DataFrame({
                "trade_date": dates,
                "ts_code": assets,
                "score": prediction[hybrid_name][split_name],
                "target": realized,
            })
            execution_candidates[candidate_id][split_name] = backtest_cross_section(
                frame, "score", "target", cost, horizon,
                risk_budget=policy["risk_budget"],
                selection_buffer=policy["selection_buffer"],
                portfolio_construction=policy["portfolio_construction"],
                causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
            )
    if domain_timed_feature_indices and bool(config.get("enable_domain_timing_overlay_candidates", True)):
        overlay_name = "domain_timing_overlay"
        models[overlay_name] = {
            "domain_timing_overlay": True,
            "components": [panel.feature_names[index] for index in domain_timed_feature_indices],
            "method": "equal cross-sectional rank ensemble of accepted domain/timing composite features",
        }
        result[overlay_name] = {
            "method": "accepted domain/timing composite features are ranked, averaged, and industry-size neutralized",
            "component_features": [panel.feature_names[index] for index in domain_timed_feature_indices],
            "test_usage": "never_used_for_overlay_construction_or_candidate_selection",
        }
        prediction[overlay_name] = {}
        for split_name, (dates, assets, realized) in adaptive_inputs.items():
            start, end = panel.split[split_name]
            raw_target = panel.targets[start:end, :, horizon_i].reshape(-1)
            valid_mask = np.isfinite(raw_target) & panel.valid[start:end].reshape(-1)
            domain_matrix = ranked_values[start:end, :, :].reshape(-1, len(panel.feature_names))[valid_mask][:, domain_timed_feature_indices]
            with np.errstate(invalid="ignore"):
                overlay_score = np.nanmean(domain_matrix, axis=1)
            meta = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": overlay_score})
            meta = meta.merge(lookup, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
            neutral_score = neutralize_cross_sectional_scores(meta)
            prediction[overlay_name][split_name] = neutral_score
            frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": neutral_score, "target": realized})
            result[overlay_name][split_name] = backtest_cross_section(frame, "score", "target", cost, horizon)
        for policy_name, policy in execution_policies.items():
            candidate_id = f"{overlay_name}::{policy_name}"
            execution_candidates[candidate_id] = {"model": overlay_name, "execution_policy": policy_name}
            for split_name, (dates, assets, realized) in adaptive_inputs.items():
                frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": prediction[overlay_name][split_name], "target": realized})
                execution_candidates[candidate_id][split_name] = backtest_cross_section(
                    frame, "score", "target", cost, horizon,
                    risk_budget=policy["risk_budget"],
                    selection_buffer=policy["selection_buffer"],
                    portfolio_construction=policy["portfolio_construction"],
                    causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
                )
        if bool(config.get("enable_domain_timing_residual_overlay_candidates", True)):
            residual_overlay_name = "domain_timing_residual_overlay"
            models[residual_overlay_name] = {
                "domain_timing_residual_overlay": True,
                "components": [overlay_name, hybrid_name],
                "method": "accepted domain/timing overlay residualized cross-sectionally against the champion anchor, size, and industry",
            }
            result[residual_overlay_name] = {
                "method": "orthogonalized domain/timing residual overlay; keeps only incremental information unexplained by the champion anchor",
                "components": [overlay_name, hybrid_name],
                "component_features": [panel.feature_names[index] for index in domain_timed_feature_indices],
                "test_usage": "never_used_for_residualization_or_candidate_selection",
            }
            prediction[residual_overlay_name] = {}
            for split_name, (dates, assets, realized) in adaptive_inputs.items():
                meta = pd.DataFrame({
                    "trade_date": dates,
                    "ts_code": assets,
                    "score": prediction[overlay_name][split_name],
                    "anchor_score": prediction[hybrid_name][split_name],
                })
                meta = meta.merge(lookup, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
                residual_score = residualize_cross_sectional_scores_against_anchor(meta)
                prediction[residual_overlay_name][split_name] = residual_score
                frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": residual_score, "target": realized})
                result[residual_overlay_name][split_name] = backtest_cross_section(frame, "score", "target", cost, horizon)
            for policy_name, policy in execution_policies.items():
                candidate_id = f"{residual_overlay_name}::{policy_name}"
                execution_candidates[candidate_id] = {"model": residual_overlay_name, "execution_policy": policy_name}
                for split_name, (dates, assets, realized) in adaptive_inputs.items():
                    frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": prediction[residual_overlay_name][split_name], "target": realized})
                    execution_candidates[candidate_id][split_name] = backtest_cross_section(
                        frame, "score", "target", cost, horizon,
                        risk_budget=policy["risk_budget"],
                        selection_buffer=policy["selection_buffer"],
                        portfolio_construction=policy["portfolio_construction"],
                        causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
                    )
            for blend_name, overlay_weight in (
                ("domain_timing_anchor_residual_blend_98_02", 0.02),
                ("domain_timing_anchor_residual_blend_95_05", 0.05),
                ("domain_timing_anchor_residual_blend_90_10", 0.10),
                ("domain_timing_anchor_residual_blend_85_15", 0.15),
            ):
                base_weight = 1.0 - overlay_weight
                models[blend_name] = {
                    "fixed_ensemble": True,
                    "components": [hybrid_name, residual_overlay_name],
                    "weights": {hybrid_name: base_weight, residual_overlay_name: overlay_weight},
                    "method": "champion-anchored rank blend with orthogonal domain/timing residual overlay",
                }
                result[blend_name] = {
                    "method": "champion-anchored rank blend with domain/timing residual overlay, then industry-size neutralization",
                    "weights": {hybrid_name: base_weight, residual_overlay_name: overlay_weight},
                    "test_usage": "never_used_for_blend_weight_or_candidate_selection",
                }
                prediction[blend_name] = {}
                for split_name, (dates, assets, realized) in adaptive_inputs.items():
                    ranked_base = _rank_targets_by_date(prediction[hybrid_name][split_name], dates)
                    ranked_overlay = _rank_targets_by_date(prediction[residual_overlay_name][split_name], dates)
                    combined = base_weight * ranked_base + overlay_weight * ranked_overlay
                    meta = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": combined})
                    meta = meta.merge(lookup, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
                    neutral_score = neutralize_cross_sectional_scores(meta)
                    prediction[blend_name][split_name] = neutral_score
                    frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": neutral_score, "target": realized})
                    result[blend_name][split_name] = backtest_cross_section(frame, "score", "target", cost, horizon)
                for policy_name, policy in execution_policies.items():
                    candidate_id = f"{blend_name}::{policy_name}"
                    execution_candidates[candidate_id] = {"model": blend_name, "execution_policy": policy_name}
                    for split_name, (dates, assets, realized) in adaptive_inputs.items():
                        frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": prediction[blend_name][split_name], "target": realized})
                        execution_candidates[candidate_id][split_name] = backtest_cross_section(
                            frame, "score", "target", cost, horizon,
                            risk_budget=policy["risk_budget"],
                            selection_buffer=policy["selection_buffer"],
                            portfolio_construction=policy["portfolio_construction"],
                            causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
                        )
            confirm_name = "domain_timing_anchor_residual_confirmed_90_10"
            models[confirm_name] = {
                "fixed_ensemble": True,
                "components": [hybrid_name, residual_overlay_name],
                "method": "agreement-gated residual overlay; domain residual is added only when it confirms the anchor direction",
            }
            result[confirm_name] = {
                "method": "champion anchor plus 10% domain/timing residual only where residual and anchor ranks have the same sign",
                "residual_weight": 0.10,
                "test_usage": "never_used_for_confirmation_gate_or_candidate_selection",
            }
            prediction[confirm_name] = {}
            for split_name, (dates, assets, realized) in adaptive_inputs.items():
                ranked_base = _rank_targets_by_date(prediction[hybrid_name][split_name], dates)
                ranked_overlay = _rank_targets_by_date(prediction[residual_overlay_name][split_name], dates)
                confirmed_overlay = np.where((ranked_base * ranked_overlay) > 0.0, ranked_overlay, 0.0)
                combined = ranked_base + 0.10 * confirmed_overlay
                meta = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": combined})
                meta = meta.merge(lookup, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
                neutral_score = neutralize_cross_sectional_scores(meta)
                prediction[confirm_name][split_name] = neutral_score
                frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": neutral_score, "target": realized})
                result[confirm_name][split_name] = backtest_cross_section(frame, "score", "target", cost, horizon)
            for policy_name, policy in execution_policies.items():
                candidate_id = f"{confirm_name}::{policy_name}"
                execution_candidates[candidate_id] = {"model": confirm_name, "execution_policy": policy_name}
                for split_name, (dates, assets, realized) in adaptive_inputs.items():
                    frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": prediction[confirm_name][split_name], "target": realized})
                    execution_candidates[candidate_id][split_name] = backtest_cross_section(
                        frame, "score", "target", cost, horizon,
                        risk_budget=policy["risk_budget"],
                        selection_buffer=policy["selection_buffer"],
                        portfolio_construction=policy["portfolio_construction"],
                        causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
                    )
        for blend_name, overlay_weight in (
            ("domain_timing_anchor_blend_90_10", 0.10),
            ("domain_timing_anchor_blend_80_20", 0.20),
            ("domain_timing_anchor_blend_70_30", 0.30),
        ):
            base_weight = 1.0 - overlay_weight
            models[blend_name] = {
                "fixed_ensemble": True,
                "components": [hybrid_name, overlay_name],
                "weights": {hybrid_name: base_weight, overlay_name: overlay_weight},
                "method": "predeclared champion-anchored rank blend with accepted domain/timing overlay",
            }
            result[blend_name] = {
                "method": "predeclared champion-anchored rank blend with accepted domain/timing overlay, then industry-size neutralization",
                "weights": {hybrid_name: base_weight, overlay_name: overlay_weight},
                "test_usage": "never_used_for_blend_weight_or_candidate_selection",
            }
            prediction[blend_name] = {}
            for split_name, (dates, assets, realized) in adaptive_inputs.items():
                # Apply the predeclared blend in rank space, then neutralize.
                ranked_base = _rank_targets_by_date(prediction[hybrid_name][split_name], dates)
                ranked_overlay = _rank_targets_by_date(prediction[overlay_name][split_name], dates)
                combined = base_weight * ranked_base + overlay_weight * ranked_overlay
                meta = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": combined})
                meta = meta.merge(lookup, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
                neutral_score = neutralize_cross_sectional_scores(meta)
                prediction[blend_name][split_name] = neutral_score
                frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": neutral_score, "target": realized})
                result[blend_name][split_name] = backtest_cross_section(frame, "score", "target", cost, horizon)
            for policy_name, policy in execution_policies.items():
                candidate_id = f"{blend_name}::{policy_name}"
                execution_candidates[candidate_id] = {"model": blend_name, "execution_policy": policy_name}
                for split_name, (dates, assets, realized) in adaptive_inputs.items():
                    frame = pd.DataFrame({"trade_date": dates, "ts_code": assets, "score": prediction[blend_name][split_name], "target": realized})
                    execution_candidates[candidate_id][split_name] = backtest_cross_section(
                        frame, "score", "target", cost, horizon,
                        risk_budget=policy["risk_budget"],
                        selection_buffer=policy["selection_buffer"],
                        portfolio_construction=policy["portfolio_construction"],
                        causal_signal_reliability=bool(policy.get("causal_signal_reliability", False)),
                    )
    selection_turnover_budget = finite(float(config.get("selection_turnover_budget", 0.65)), 0.65)

    def train_valid_turnover_budget_pass(candidate: dict[str, Any]) -> bool:
        return (
            finite(candidate["train"].get("turnover"), 999.0) <= selection_turnover_budget
            and finite(candidate["valid"].get("turnover"), 999.0) <= selection_turnover_budget
        )

    best_validation_id = max(
        execution_candidates,
        key=lambda candidate_id: execution_candidates[candidate_id]["valid"]["sharpe"],
    )
    complexity_order = {
        "incumbent_ols": 0,
        "ols": 1,
        "domain_ridge": 2,
        "adaptive_icir_12m_neutral": 2.5,
        "incumbent_ols_adaptive_icir_rank_ensemble": 2.75,
        "domain_timing_anchor_residual_blend_98_02": 2.77,
        "domain_timing_anchor_residual_blend_95_05": 2.78,
        "domain_timing_anchor_residual_blend_90_10": 2.80,
        "domain_timing_anchor_residual_confirmed_90_10": 2.81,
        "domain_timing_anchor_residual_blend_85_15": 2.82,
        "domain_timing_anchor_blend_90_10": 2.84,
        "domain_timing_anchor_blend_80_20": 2.86,
        "domain_timing_anchor_blend_70_30": 2.88,
        "screened_ols": 2.8,
        "lasso": 3,
        "domain_timing_overlay": 3.2,
        "domain_timing_residual_overlay": 3.25,
        "cs_ridge_neutral": 4,
        "cs_elastic_neutral": 5,
        "deep_mlp": 6,
    }
    def candidate_complexity(candidate_id: str) -> tuple[float, str]:
        candidate = execution_candidates[candidate_id]
        policy_penalty = {
            "full_exposure": 0.0,
            "robust_fast_slow_volatility_budget": 0.25,
            "robust_volatility_budget_rank_buffer": 0.40,
            "continuous_rank_volatility_budget": 0.30,
            "continuous_rank_cost_aware_volatility_budget": 0.45,
            "continuous_rank_adaptive_cost_aware_volatility_budget": 0.50,
            "continuous_rank_inverse_volatility_budget": 0.40,
            "continuous_rank_reliability_adjusted_volatility_budget": 0.45,
        }.get(candidate["execution_policy"], 0.50)
        return complexity_order.get(candidate["model"], 9.0) + policy_penalty, candidate_id
    qualified = [
        candidate_id
        for candidate_id, candidate in execution_candidates.items()
        if math.isfinite(robust_development_score(candidate))
    ]
    turnover_qualified = [
        candidate_id for candidate_id in qualified
        if train_valid_turnover_budget_pass(execution_candidates[candidate_id])
    ]
    selection_pool = turnover_qualified or qualified
    if selection_pool:
        best_validation_id = max(
            selection_pool,
            key=lambda candidate_id: execution_candidates[candidate_id]["valid"]["sharpe"],
        )
        best_development_id = max(
            selection_pool,
            key=lambda candidate_id: robust_development_score(
                execution_candidates[candidate_id]
            ),
        )
    else:
        best_development_id = best_validation_id
    best_development_candidate = execution_candidates[best_development_id]
    best_development_score = robust_development_score(
        best_development_candidate
    )
    if not math.isfinite(best_development_score):
        best_development_score = 0.0
    best_observations = max(3, min(
        int(best_development_candidate["train"].get("observations") or 0),
        int(best_development_candidate["valid"].get("observations") or 0),
    ))
    one_standard_error = math.sqrt(
        max(1e-9, 1.0 + 0.5 * best_development_score ** 2)
        / best_observations
    )
    eligible = [
        candidate_id for candidate_id in selection_pool
        if robust_development_score(execution_candidates[candidate_id])
        >= best_development_score - one_standard_error
    ]
    prefer_best_development = bool(config.get("selection_prefer_best_development", True))
    anchor_id = str(config.get("selection_anchor_candidate_id") or "").strip()
    anchor_pool = set(eligible or selection_pool or [best_development_id])
    anchor_selected = False
    anchor_reasons: list[str] = []
    if bool(config.get("selection_prefer_anchor_when_eligible", False)) and anchor_id:
        if anchor_id not in execution_candidates:
            anchor_reasons.append("anchor_candidate_missing")
        elif anchor_id not in anchor_pool:
            anchor_reasons.append("anchor_not_within_one_standard_error_or_selection_pool")
        elif not math.isfinite(robust_development_score(execution_candidates[anchor_id])):
            anchor_reasons.append("anchor_failed_train_valid_positive_ic_gate")
        elif turnover_qualified and anchor_id not in turnover_qualified:
            anchor_reasons.append("anchor_failed_train_valid_turnover_budget")
        else:
            selected_id = anchor_id
            anchor_selected = True
    if not anchor_selected:
        if prefer_best_development:
            selected_id = best_development_id
        else:
            selected_id = min(eligible or [best_development_id], key=candidate_complexity)
    anchor_guard_applied = False
    anchor_guard_reasons: list[str] = []
    anchor_guard_improved_candidates: list[dict[str, Any]] = []
    if bool(config.get("selection_anchor_no_downgrade_guard", True)) and anchor_id:
        if anchor_id not in execution_candidates:
            anchor_guard_reasons.append("anchor_guard_anchor_missing")
        elif anchor_id not in selection_pool:
            anchor_guard_reasons.append("anchor_guard_anchor_outside_selection_pool")
        else:
            anchor_candidate = execution_candidates[anchor_id]
            anchor_score = robust_development_score(anchor_candidate)
            min_valid_sharpe_delta = finite(float(config.get("selection_anchor_min_valid_sharpe_delta", 0.02)), 0.02)
            min_development_delta = finite(float(config.get("selection_anchor_min_development_delta", 0.02)), 0.02)
            min_valid_rank_ic_delta = finite(float(config.get("selection_anchor_min_valid_rank_ic_delta", 0.0)), 0.0)
            min_valid_hit_buffer = finite(float(config.get("selection_anchor_min_valid_hit_buffer", -0.02)), -0.02)
            anchor_valid_sharpe = finite(anchor_candidate["valid"].get("sharpe"))
            anchor_valid_rank_ic = finite(anchor_candidate["valid"].get("rank_ic"))
            anchor_valid_hit = finite(anchor_candidate["valid"].get("hit_rate"))

            def passes_anchor_guard(candidate_id: str) -> bool:
                if candidate_id == anchor_id:
                    return True
                candidate = execution_candidates[candidate_id]
                candidate_score = robust_development_score(candidate)
                return (
                    math.isfinite(candidate_score)
                    and math.isfinite(anchor_score)
                    and candidate_score >= anchor_score + min_development_delta
                    and finite(candidate["valid"].get("sharpe")) >= anchor_valid_sharpe + min_valid_sharpe_delta
                    and finite(candidate["valid"].get("rank_ic")) >= anchor_valid_rank_ic + min_valid_rank_ic_delta
                    and finite(candidate["valid"].get("hit_rate")) >= anchor_valid_hit + min_valid_hit_buffer
                )

            anchor_guard_pool = [
                candidate_id for candidate_id in selection_pool
                if candidate_id != anchor_id and passes_anchor_guard(candidate_id)
            ]
            anchor_guard_improved_candidates = sorted(
                (
                    {
                        "candidate": candidate_id,
                        "development_score": robust_development_score(execution_candidates[candidate_id]),
                        "valid_sharpe": finite(execution_candidates[candidate_id]["valid"].get("sharpe")),
                        "valid_rank_ic": finite(execution_candidates[candidate_id]["valid"].get("rank_ic")),
                        "valid_hit_rate": finite(execution_candidates[candidate_id]["valid"].get("hit_rate")),
                    }
                    for candidate_id in anchor_guard_pool
                ),
                key=lambda row: (row["development_score"], row["valid_sharpe"], row["valid_rank_ic"]),
                reverse=True,
            )[:8]
            if anchor_guard_pool:
                guard_best_score = max(
                    robust_development_score(execution_candidates[candidate_id])
                    for candidate_id in anchor_guard_pool
                )
                guard_eligible = [
                    candidate_id for candidate_id in anchor_guard_pool
                    if robust_development_score(execution_candidates[candidate_id])
                    >= guard_best_score - one_standard_error
                ]
                if bool(config.get("selection_anchor_guard_prefer_simplest_within_one_se", True)):
                    selected_id = min(guard_eligible or anchor_guard_pool, key=candidate_complexity)
                    anchor_guard_reasons.append("selected_simplest_anchor_guard_candidate_within_one_standard_error")
                else:
                    selected_id = max(
                        anchor_guard_pool,
                        key=lambda candidate_id: robust_development_score(execution_candidates[candidate_id]),
                    )
                    anchor_guard_reasons.append("selected_best_anchor_guard_candidate")
                anchor_guard_reasons.append("selected_candidate_passed_anchor_train_valid_guard")
            elif selected_id != anchor_id:
                selected_id = anchor_id
                anchor_selected = True
                anchor_guard_applied = True
                anchor_guard_reasons.append("fallback_to_anchor_no_train_valid_dominant_candidate")
    selected_candidate = execution_candidates[selected_id]
    selected = selected_candidate["model"]
    selected_policy = selected_candidate["execution_policy"]
    positive_ic_candidates = sorted(
        (
            {
                "development_score": robust_development_score(candidate),
                "candidate": candidate_id,
                "train_sharpe": finite(candidate["train"].get("sharpe")),
                "valid_sharpe": finite(candidate["valid"].get("sharpe")),
                "train_rank_ic": finite(candidate["train"].get("rank_ic")),
                "valid_rank_ic": finite(candidate["valid"].get("rank_ic")),
                "valid_hit_rate": finite(candidate["valid"].get("hit_rate")),
            }
            for candidate_id, candidate in execution_candidates.items()
            if finite(candidate["train"].get("rank_ic")) > 0.0
            and finite(candidate["valid"].get("rank_ic")) > 0.0
            and math.isfinite(robust_development_score(candidate))
        ),
        key=lambda row: (row["development_score"], row["valid_rank_ic"]),
        reverse=True,
    )
    selection_checks = {
        "train_sharpe_positive": finite(selected_candidate["train"].get("sharpe")) > 0.0,
        "valid_sharpe_positive": finite(selected_candidate["valid"].get("sharpe")) > 0.0,
        "train_rank_ic_positive": finite(selected_candidate["train"].get("rank_ic")) > 0.0,
        "valid_rank_ic_positive": finite(selected_candidate["valid"].get("rank_ic")) > 0.0,
        "valid_ic_hit_rate_above_half": finite(selected_candidate["valid"].get("hit_rate")) > 0.50,
        "train_turnover_within_budget": finite(selected_candidate["train"].get("turnover"), 999.0) <= selection_turnover_budget,
        "valid_turnover_within_budget": finite(selected_candidate["valid"].get("turnover"), 999.0) <= selection_turnover_budget,
    }
    selection_quality = {
        "status": "passed" if all(selection_checks.values()) else "conditional",
        "checks": selection_checks,
        "selection_turnover_budget": selection_turnover_budget,
        "turnover_constrained_candidate_count": len(turnover_qualified),
        "turnover_constraint_fallback": not bool(turnover_qualified),
        "prefer_best_development": prefer_best_development,
        "one_standard_error_eligible_candidates": len(eligible),
        "anchor_candidate_id": anchor_id,
        "anchor_selected": anchor_selected,
        "anchor_reasons": anchor_reasons,
        "anchor_guard_applied": anchor_guard_applied,
        "anchor_guard_reasons": anchor_guard_reasons,
        "anchor_guard_improved_candidates": anchor_guard_improved_candidates,
        "positive_train_valid_ic_candidates": positive_ic_candidates[:5],
        "decision_basis": "train_and_validation_only",
        "test_usage": "report_only",
    }
    weights = {name: float(name == selected) for name in models}
    result["ensemble"] = {
        "weights": weights,
        "selection_rule": (
            "purged train-OOF and validation positive-IC harmonic stability, "
            "train-validation turnover-budget prefilter, then best robust "
            "development score; one-standard-error remains an audit reference"
        ),
        "best_validation_candidate": best_validation_id,
        "best_development_candidate": best_development_id,
        "best_development_score": best_development_score,
        "selected_model": selected,
        "selected_execution_policy": selected_policy,
        "one_standard_error": one_standard_error,
        "selection_quality": selection_quality,
        **{
            split_name: selected_candidate[split_name]
            for split_name in ("train", "valid", "test")
        },
    }
    result["execution_candidates"] = execution_candidates
    trials = len(execution_candidates)
    result["ensemble"]["test"].update(deflated_sharpe_proxy(result["ensemble"]["test"]["sharpe"], result["ensemble"]["test"]["observations"], trials))
    metrics = {
        split_name: result["ensemble"][split_name]
        for split_name in ("train", "valid", "test")
    }
    return {
        "engine": "strategy",
        "engine_version": ENGINE_VERSION,
        "models": result,
        "metrics": metrics,
        "gates": gate_results(metrics, trials),
        "selection": {
            "selected_model": selected,
            "selected_execution_policy": selected_policy,
            "best_validation_candidate": best_validation_id,
            "best_development_candidate": best_development_id,
            "best_development_score": best_development_score,
            "one_standard_error": one_standard_error,
            "selection_turnover_budget": selection_turnover_budget,
            "turnover_constrained_candidate_count": len(turnover_qualified),
            "turnover_constraint_fallback": not bool(turnover_qualified),
            "prefer_best_development": prefer_best_development,
            "one_standard_error_eligible_candidates": len(eligible),
            "anchor_candidate_id": anchor_id,
            "anchor_selected": anchor_selected,
            "anchor_reasons": anchor_reasons,
            "complexity_order": complexity_order,
            "adaptive_icir": adaptive_icir_report,
            "selection_quality": selection_quality,
            "test_usage": "report_only",
            "candidate_count": trials,
        },
        "test_used_for_selection": False,
    }


def run_joint_test(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    start, end = panel.split["test"]
    rows = []
    for fi, name in enumerate(panel.feature_names):
        score = panel.features[start:end, :, fi]
        sf = split_frame(panel, "test", score, 0)
        metric = backtest_cross_section(sf, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])
        rows.append({"factor": name, **{k: v for k, v in metric.items() if k != "series"}})
    rows.sort(key=lambda x: abs(x["rank_ic"]), reverse=True)
    matrix = panel.features[start:end].reshape(-1, len(panel.feature_names))
    corr = np.corrcoef(np.nan_to_num(matrix).T)
    return {"engine": "joint_test", "engine_version": ENGINE_VERSION, "factors": rows, "correlation": {"labels": panel.feature_names, "matrix": np.nan_to_num(corr).round(5).tolist()}, "metrics": {"test": rows[0] if rows else {}}, "gates": gate_results({"test": rows[0] if rows else {}}, len(rows))}


def gate_results(metrics: dict[str, Any], trials: int) -> list[dict[str, Any]]:
    valid = metrics.get("valid") or {}; test = metrics.get("test") or {}
    rules = [
        ("point_in_time", True, True, "点时与冻结切分"),
        ("coverage", finite(test.get("coverage", 1.0)), .80, "测试期覆盖率"),
        ("rank_ic", abs(finite(test.get("rank_ic"))), .03, "测试期绝对RankIC"),
        ("hit_rate", finite(test.get("hit_rate")), .53, "测试期IC命中率"),
        ("oos_decay", abs(finite(test.get("rank_ic"))) / max(abs(finite(valid.get("rank_ic"))), 1e-6), .75, "验证到测试衰减"),
        ("net_sharpe", finite(test.get("sharpe")), .50, "成本后测试Sharpe"),
        ("drawdown", finite(test.get("max_drawdown")), -.25, "最大回撤不劣于-25%"),
        ("turnover", finite(test.get("turnover")), .65, "换手预算"),
        ("dsr", finite(test.get("dsr_confidence")), .60, "多重试验修正DSR"),
        ("trial_ledger", trials, 1, "试验台账完整"),
    ]
    return [{"gate": key, "label": label, "observed": finite(value), "threshold": finite(threshold), "comparison": "le" if key == "turnover" else "ge", "passed": bool(value <= threshold if key == "turnover" else value >= threshold)} for key, value, threshold, label in rules]


def run(config: dict[str, Any], progress_path: Path | None = None) -> dict[str, Any]:
    started = time.time()
    progress(progress_path, "initializing", .01, "初始化研究任务", engine=config.get("engine"))
    panel = read_panel(config, progress_path)
    engine = str(config.get("engine", "lstm"))
    if engine == "lstm": payload = run_lstm(panel, config, progress_path)
    elif engine == "gru": payload = run_gru(panel, config, progress_path)
    elif engine == "rl_transformer": payload = run_rl_transformer(panel, config, progress_path)
    elif engine == "strategy": payload = run_strategy(panel, config, progress_path)
    elif engine == "joint_test": payload = run_joint_test(panel, config, progress_path)
    else: raise ValueError(f"unsupported engine: {engine}")
    payload.update({
        "status": "completed", "engine_version": ENGINE_VERSION, "created_at": now_iso(),
        "elapsed_seconds": round(time.time() - started, 3), "source": panel.source,
        "split": {k: {"start": panel.dates[v[0]], "end": panel.dates[max(v[0], v[1] - 1)], "start_index": v[0], "end_index": v[1]} for k, v in panel.split.items()},
        "horizons": panel.horizons, "features": panel.feature_names,
        "test_policy": "report_only_after_train_validation_selection",
    })
    progress(progress_path, "completed", 1.0, "研究任务完成", elapsed_seconds=payload["elapsed_seconds"])
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--progress")
    args = parser.parse_args()
    config_path, output_path = Path(args.config), Path(args.output)
    progress_path = Path(args.progress) if args.progress else None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        payload = run(config, progress_path)
        atomic_json(output_path, payload)
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = {"status": "failed", "engine_version": ENGINE_VERSION, "message": str(exc), "traceback": traceback.format_exc(limit=18), "created_at": now_iso()}
        atomic_json(output_path, failure)
        progress(progress_path, "failed", 1.0, str(exc))
        print(failure["traceback"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
