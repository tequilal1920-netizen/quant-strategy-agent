"""Causal multi-scale K-line expert portfolio utilities.

The module deliberately separates representation, causal expert gating, portfolio
construction and sealed-test reporting.  All signals are formed with information
available at the signal close.  Expert weights at a signal date only use feedback
whose holding period has already matured.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import bottleneck as bn
except ImportError:  # pragma: no cover - pandas fallback is exercised in minimal envs.
    bn = None


TRADING_DAYS = 252
WEEKS_PER_YEAR = 52


def _finite(array: np.ndarray) -> np.ndarray:
    return np.asarray(array, dtype=np.float64)


def _safe_divide(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    output = np.full(np.broadcast_shapes(np.shape(left), np.shape(right)), np.nan, dtype=np.float64)
    np.divide(left, right, out=output, where=np.isfinite(right) & (np.abs(right) > 1e-12))
    return output


def _move_mean(values: np.ndarray, window: int, minimum: Optional[int] = None) -> np.ndarray:
    minimum = int(minimum or max(2, window // 2))
    if bn is not None:
        return bn.move_mean(_finite(values), window=window, min_count=minimum, axis=0)
    return pd.DataFrame(values).rolling(window, min_periods=minimum).mean().to_numpy()


def _move_std(values: np.ndarray, window: int, minimum: Optional[int] = None) -> np.ndarray:
    minimum = int(minimum or max(3, window // 2))
    if bn is not None:
        return bn.move_std(_finite(values), window=window, min_count=minimum, ddof=1, axis=0)
    return pd.DataFrame(values).rolling(window, min_periods=minimum).std().to_numpy()


def _move_max(values: np.ndarray, window: int, minimum: Optional[int] = None) -> np.ndarray:
    minimum = int(minimum or max(2, window // 2))
    if bn is not None:
        return bn.move_max(_finite(values), window=window, min_count=minimum, axis=0)
    return pd.DataFrame(values).rolling(window, min_periods=minimum).max().to_numpy()


def _move_min(values: np.ndarray, window: int, minimum: Optional[int] = None) -> np.ndarray:
    minimum = int(minimum or max(2, window // 2))
    if bn is not None:
        return bn.move_min(_finite(values), window=window, min_count=minimum, axis=0)
    return pd.DataFrame(values).rolling(window, min_periods=minimum).min().to_numpy()


def row_rank(values: np.ndarray, eligible: Optional[np.ndarray] = None) -> np.ndarray:
    """Cross-sectional percentile rank with missing and eligibility awareness."""

    frame = pd.DataFrame(_finite(values))
    if eligible is not None:
        frame = frame.where(np.asarray(eligible, dtype=bool))
    return frame.rank(axis=1, pct=True, method="average").to_numpy(dtype=np.float32)


def _combine_ranked(parts: Sequence[Tuple[np.ndarray, float]], eligible: np.ndarray) -> np.ndarray:
    total = np.zeros_like(parts[0][0], dtype=np.float64)
    weight_sum = np.zeros_like(parts[0][0], dtype=np.float64)
    for values, weight in parts:
        ranked = row_rank(values, eligible)
        valid = np.isfinite(ranked)
        total[valid] += float(weight) * ranked[valid]
        weight_sum[valid] += abs(float(weight))
    result = _safe_divide(total, weight_sum)
    result[~eligible] = np.nan
    return row_rank(result, eligible)


def _lag_return(close: np.ndarray, lag: int) -> np.ndarray:
    result = np.full_like(close, np.nan, dtype=np.float64)
    result[lag:] = _safe_divide(close[lag:], close[:-lag]) - 1.0
    return result


def build_multiscale_experts(
    close: np.ndarray,
    open_price: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    signal_indices: Sequence[int],
    eligible_daily: np.ndarray,
) -> Dict[str, np.ndarray]:
    """Build interpretable daily/weekly K-line experts from point-in-time OHLCV."""

    close = _finite(close)
    open_price = _finite(open_price)
    high = _finite(high)
    low = _finite(low)
    volume = _finite(volume)
    amount = _finite(amount)
    signal_indices = np.asarray(signal_indices, dtype=np.int32)
    eligible = np.asarray(eligible_daily[signal_indices], dtype=bool)

    returns = _lag_return(close, 1)
    mom5, mom20, mom60, mom120 = (_lag_return(close, lag) for lag in (5, 20, 60, 120))
    vol20 = _move_std(returns, 20, 12) * sqrt(TRADING_DAYS)
    vol60 = _move_std(returns, 60, 36) * sqrt(TRADING_DAYS)
    path20 = _move_mean(np.abs(returns), 20, 12) * 20.0
    path60 = _move_mean(np.abs(returns), 60, 36) * 60.0
    efficiency20 = _safe_divide(np.abs(mom20), path20)
    efficiency60 = _safe_divide(np.abs(mom60), path60)
    high20, high60 = _move_max(high, 20, 12), _move_max(high, 60, 36)
    low20 = _move_min(low, 20, 12)
    range20 = _safe_divide(high20 - low20, close)
    breakout20 = _safe_divide(close, high20) - 1.0
    breakout60 = _safe_divide(close, high60) - 1.0
    candle_range = np.maximum(high - low, np.abs(close) * 1e-6)
    body = _safe_divide(close - open_price, candle_range)
    close_position = _safe_divide(close - low, candle_range)
    lower_wick = _safe_divide(np.minimum(open_price, close) - low, candle_range)
    upper_wick = _safe_divide(high - np.maximum(open_price, close), candle_range)
    volume5 = _move_mean(volume, 5, 3)
    volume20 = _move_mean(volume, 20, 12)
    amount20 = _move_mean(amount, 20, 12)
    volume_confirmation = np.log1p(np.maximum(_safe_divide(volume5, volume20), 0.0))
    amount_stability = -_safe_divide(_move_std(amount, 20, 12), np.abs(amount20))

    def at_signal(values: np.ndarray) -> np.ndarray:
        return values[signal_indices]

    daily_trend = _combine_ranked(
        [
            (at_signal(mom20), 0.22),
            (at_signal(mom60), 0.30),
            (at_signal(mom120), 0.20),
            (at_signal(efficiency20), 0.14),
            (at_signal(efficiency60), 0.14),
            (at_signal(vol20), -0.12),
        ],
        eligible,
    )
    breakout = _combine_ranked(
        [
            (at_signal(breakout20), 0.24),
            (at_signal(breakout60), 0.24),
            (at_signal(close_position), 0.16),
            (at_signal(body), 0.10),
            (at_signal(volume_confirmation), 0.16),
            (at_signal(range20), -0.10),
        ],
        eligible,
    )
    trend_pullback = _combine_ranked(
        [
            (at_signal(mom60), 0.28),
            (at_signal(mom120), 0.24),
            (at_signal(mom5), -0.18),
            (at_signal(lower_wick), 0.12),
            (at_signal(upper_wick), -0.06),
            (at_signal(volume_confirmation), -0.06),
            (at_signal(vol20), -0.06),
        ],
        eligible,
    )
    compression = _combine_ranked(
        [
            (at_signal(mom60), 0.25),
            (at_signal(mom20), 0.16),
            (at_signal(_safe_divide(vol20, vol60)), -0.22),
            (at_signal(range20), -0.16),
            (at_signal(amount_stability), 0.11),
            (at_signal(close_position), 0.10),
        ],
        eligible,
    )

    weekly_open = _move_mean(open_price, 5, 3) * np.nan
    weekly_open[4:] = open_price[:-4]
    weekly_high = _move_max(high, 5, 3)
    weekly_low = _move_min(low, 5, 3)
    weekly_range = np.maximum(weekly_high - weekly_low, np.abs(close) * 1e-6)
    weekly_body = _safe_divide(close - weekly_open, weekly_range)
    weekly_position = _safe_divide(close - weekly_low, weekly_range)
    weekly_volume = _move_mean(volume, 5, 3) * 5.0
    weekly_volume_base = _move_mean(weekly_volume, 20, 12)
    weekly_volume_ratio = np.log1p(np.maximum(_safe_divide(weekly_volume, weekly_volume_base), 0.0))
    weekly_expert = _combine_ranked(
        [
            (at_signal(mom5), 0.12),
            (at_signal(mom20), 0.24),
            (at_signal(mom60), 0.28),
            (at_signal(weekly_body), 0.10),
            (at_signal(weekly_position), 0.12),
            (at_signal(weekly_volume_ratio), 0.08),
            (at_signal(vol60), -0.06),
        ],
        eligible,
    )
    return {
        "日线趋势": daily_trend,
        "放量突破": breakout,
        "趋势回撤": trend_pullback,
        "缩量蓄势": compression,
        "周线形态": weekly_expert,
    }


def rank_ic(score: np.ndarray, realized: np.ndarray, eligible: np.ndarray) -> float:
    mask = np.asarray(eligible, dtype=bool) & np.isfinite(score) & np.isfinite(realized)
    if int(mask.sum()) < 30:
        return 0.0
    left = pd.Series(score[mask]).rank().to_numpy()
    right = pd.Series(realized[mask]).rank().to_numpy()
    value = np.corrcoef(left, right)[0, 1]
    return float(value) if np.isfinite(value) else 0.0


def causal_expert_mixture(
    experts: Mapping[str, np.ndarray],
    feedback_returns: np.ndarray,
    eligible: np.ndarray,
    states: Optional[Sequence[int]] = None,
    half_life: float = 26.0,
    prior_strength: float = 13.0,
    signed_direction: bool = False,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Combine experts with an online, state-aware posterior using matured feedback."""

    names = list(experts)
    panels = np.stack([np.asarray(experts[name], dtype=np.float64) for name in names], axis=1)
    periods, expert_count, assets = panels.shape
    feedback_returns = np.asarray(feedback_returns, dtype=np.float64)
    eligible = np.asarray(eligible, dtype=bool)
    states = np.zeros(periods, dtype=np.int8) if states is None else np.asarray(states, dtype=np.int8)
    decay = float(np.exp(np.log(0.5) / max(float(half_life), 1.0)))
    global_sum = np.zeros(expert_count)
    global_weight = np.zeros(expert_count)
    state_sum = np.zeros((3, expert_count))
    state_weight = np.zeros((3, expert_count))
    mixture = np.full((periods, assets), np.nan, dtype=np.float32)
    weights = np.zeros((periods, expert_count), dtype=np.float32)
    feedback_ic = np.full((periods, expert_count), np.nan, dtype=np.float32)

    for index in range(periods):
        if index > 0:
            global_sum *= decay
            global_weight *= decay
            state_sum *= decay
            state_weight *= decay
            previous_state = int(np.clip(states[index - 1], 0, 2))
            for expert in range(expert_count):
                value = rank_ic(
                    panels[index - 1, expert], feedback_returns[index - 1], eligible[index - 1]
                )
                feedback_ic[index - 1, expert] = value
                global_sum[expert] += value
                global_weight[expert] += 1.0
                state_sum[previous_state, expert] += value
                state_weight[previous_state, expert] += 1.0

        current_state = int(np.clip(states[index], 0, 2))
        global_mean = _safe_divide(global_sum, global_weight + prior_strength)
        local_mean = _safe_divide(
            state_sum[current_state] + prior_strength * global_mean,
            state_weight[current_state] + prior_strength,
        )
        evidence = 0.40 * global_mean + 0.60 * local_mean
        evidence = np.nan_to_num(evidence, nan=0.0)
        dispersion = max(float(np.std(evidence)), 0.01)
        reliability = np.abs(evidence) if signed_direction else evidence
        logits = np.clip(reliability / dispersion, -4.0, 4.0)
        posterior = np.exp(logits - np.max(logits))
        posterior /= max(float(posterior.sum()), 1e-12)
        directions = np.where(evidence < 0.0, -1.0, 1.0) if signed_direction else np.ones(expert_count)
        weights[index] = posterior * directions
        valid = np.isfinite(panels[index])
        directed_panel = (
            0.5 + directions[:, None] * (panels[index] - 0.5)
            if signed_direction else panels[index]
        )
        numerator = np.nansum(directed_panel * posterior[:, None], axis=0)
        denominator = np.sum(valid * posterior[:, None], axis=0)
        row = _safe_divide(numerator, denominator)
        row[~eligible[index]] = np.nan
        mixture[index] = row_rank(row[None, :], eligible[index][None, :])[0]
    return mixture, weights, feedback_ic


def residual_blend(
    original: np.ndarray,
    technical: np.ndarray,
    eligible: np.ndarray,
    technical_weight: float = 0.65,
) -> np.ndarray:
    """Add only the cross-sectional technical component not spanned by the old score."""

    original = row_rank(original, eligible)
    technical = row_rank(technical, eligible)
    output = np.full_like(technical, np.nan, dtype=np.float32)
    for index in range(len(output)):
        mask = eligible[index] & np.isfinite(original[index]) & np.isfinite(technical[index])
        if int(mask.sum()) < 30:
            continue
        x = original[index, mask].astype(float)
        y = technical[index, mask].astype(float)
        design = np.column_stack([np.ones(len(x)), x])
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - design @ beta
        residual_rank = pd.Series(residual).rank(pct=True).to_numpy()
        combined = (1.0 - technical_weight) * x + technical_weight * residual_rank
        output[index, mask] = pd.Series(combined).rank(pct=True).to_numpy(dtype=np.float32)
    return output


def market_state_features(close: np.ndarray, signal_indices: Sequence[int], eligible_daily: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return continuous causal state features and three descriptive states."""

    close = _finite(close)
    signal_indices = np.asarray(signal_indices, dtype=np.int32)
    daily_returns = _lag_return(close, 1)
    mom20 = _lag_return(close, 20)
    mom60 = _lag_return(close, 60)
    market_return = np.nanmean(np.where(eligible_daily, daily_returns, np.nan), axis=1)
    market_mom20 = _move_mean(market_return[:, None], 20, 12)[:, 0] * 20.0
    market_mom60 = _move_mean(market_return[:, None], 60, 36)[:, 0] * 60.0
    market_vol20 = _move_std(market_return[:, None], 20, 12)[:, 0] * sqrt(TRADING_DAYS)
    breadth20 = np.nanmean(np.where(eligible_daily, mom20 > 0.0, np.nan), axis=1)
    breadth60 = np.nanmean(np.where(eligible_daily, mom60 > 0.0, np.nan), axis=1)
    raw = np.column_stack([
        market_mom20[signal_indices], market_mom60[signal_indices],
        breadth20[signal_indices] - 0.5, breadth60[signal_indices] - 0.5,
        -market_vol20[signal_indices],
    ])
    frame = pd.DataFrame(raw)
    expanding_mean = frame.expanding(min_periods=13).mean().shift(1)
    expanding_std = frame.expanding(min_periods=13).std().shift(1).replace(0.0, np.nan)
    standardized = ((frame - expanding_mean) / expanding_std).clip(-4.0, 4.0).fillna(0.0).to_numpy()
    trend_axis = standardized[:, 1] + standardized[:, 3]
    risk_axis = -standardized[:, 4]
    states = np.ones(len(raw), dtype=np.int8)
    states[(trend_axis > 0.35) & (risk_axis < 1.25)] = 2
    states[(trend_axis < -0.35) | (risk_axis > 1.25)] = 0
    return standardized.astype(np.float32), states


def online_market_exposure(
    state_features: np.ndarray,
    matured_market_returns: np.ndarray,
    realized_volatility: np.ndarray,
    minimum_history: int = 26,
    ridge: float = 8.0,
    volatility_target: float = 0.14,
) -> np.ndarray:
    """Causal expanding ridge forecast mapped to a volatility-budgeted exposure."""

    features = np.asarray(state_features, dtype=np.float64)
    returns = np.asarray(matured_market_returns, dtype=np.float64)
    volatility = np.asarray(realized_volatility, dtype=np.float64)
    output = np.ones(len(features), dtype=np.float32)
    for index in range(len(features)):
        history = np.arange(index)
        history = history[np.isfinite(returns[:index])]
        vol_budget = min(1.0, volatility_target / max(float(volatility[index]), 0.06))
        if len(history) < minimum_history:
            output[index] = vol_budget
            continue
        x = features[history]
        y = returns[history]
        design = np.column_stack([np.ones(len(x)), x])
        penalty = np.eye(design.shape[1]) * ridge
        penalty[0, 0] = 0.0
        beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        prediction = float(np.r_[1.0, features[index]] @ beta)
        scale = max(float(np.std(y[-52:], ddof=1)), 0.015)
        directional = float(1.0 / (1.0 + np.exp(-np.clip(prediction / scale, -5.0, 5.0))))
        output[index] = np.clip((0.20 + 0.80 * directional) * vol_budget, 0.0, 1.0)
    return output


@dataclass(frozen=True)
class BacktestConfig:
    selection_count: int = 100
    selection_fraction: float = 0.10
    buffer_multiple: float = 1.5
    maximum_weight: float = 0.02
    cost_rate: float = 0.0015
    minimum_assets: int = 50
    inverse_risk_weighting: bool = True


def _capped_normalize(raw: np.ndarray, cap: float) -> np.ndarray:
    raw = np.maximum(np.nan_to_num(np.asarray(raw, dtype=float), nan=0.0), 0.0)
    if len(raw) == 0 or float(raw.sum()) <= 0.0:
        return np.zeros_like(raw)
    if len(raw) * cap < 1.0 - 1e-12:
        return raw / float(raw.sum())
    weights = np.zeros_like(raw)
    active = np.ones(len(raw), dtype=bool)
    remaining = 1.0
    while active.any() and remaining > 1e-12:
        active_raw = raw[active]
        proposed = active_raw / max(float(active_raw.sum()), 1e-12) * remaining
        over = proposed > cap + 1e-12
        active_positions = np.flatnonzero(active)
        if not over.any():
            weights[active_positions] = proposed
            break
        capped_positions = active_positions[over]
        weights[capped_positions] = cap
        active[capped_positions] = False
        remaining = 1.0 - float(weights.sum())
    return weights


def _max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    nav = np.cumprod(1.0 + np.nan_to_num(returns, nan=0.0))
    peaks = np.maximum.accumulate(nav)
    return float(np.min(nav / np.maximum(peaks, 1e-12) - 1.0))


def _metrics(rows: Sequence[Mapping[str, float]]) -> Dict[str, float]:
    rows = [
        row for row in rows
        if np.isfinite(row["strategy_return"]) and np.isfinite(row["benchmark_return"])
    ]
    if not rows:
        return {
            "periods": 0, "annual_return": 0.0, "excess_annual_return": 0.0,
            "sharpe": 0.0, "excess_sharpe": 0.0, "max_drawdown": 0.0,
            "turnover": 0.0, "rank_ic": 0.0, "win_rate": 0.0,
        }
    strategy = np.asarray([row["strategy_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    excess = strategy - benchmark
    annual = float(np.prod(1.0 + strategy) ** (WEEKS_PER_YEAR / len(strategy)) - 1.0)
    benchmark_annual = float(np.prod(1.0 + benchmark) ** (WEEKS_PER_YEAR / len(benchmark)) - 1.0)
    std = float(np.std(strategy, ddof=1)) if len(strategy) > 1 else 0.0
    excess_std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
    return {
        "periods": int(len(rows)),
        "annual_return": annual,
        "benchmark_annual_return": benchmark_annual,
        "excess_annual_return": annual - benchmark_annual,
        "sharpe": float(np.mean(strategy) / std * sqrt(WEEKS_PER_YEAR)) if std > 1e-12 else 0.0,
        "excess_sharpe": float(np.mean(excess) / excess_std * sqrt(WEEKS_PER_YEAR)) if excess_std > 1e-12 else 0.0,
        "max_drawdown": _max_drawdown(strategy),
        "turnover": float(np.mean([row["turnover"] for row in rows])),
        "rank_ic": float(np.mean([row["rank_ic"] for row in rows])),
        "win_rate": float(np.mean(excess > 0.0)),
    }


def backtest_weekly_scores(
    dates: Sequence[str],
    scores: np.ndarray,
    eligible: np.ndarray,
    entry_prices: np.ndarray,
    signal_close: np.ndarray,
    risk: np.ndarray,
    split_labels: Sequence[str],
    exposure: Optional[np.ndarray] = None,
    config: BacktestConfig = BacktestConfig(),
) -> Dict[str, object]:
    """Backtest weekly cross-sectional scores with buffering and explicit cash."""

    dates = [str(value) for value in dates]
    scores = np.asarray(scores, dtype=float)
    eligible = np.asarray(eligible, dtype=bool)
    entry_prices = np.asarray(entry_prices, dtype=float)
    signal_close = np.asarray(signal_close, dtype=float)
    risk = np.asarray(risk, dtype=float)
    split_labels = list(split_labels)
    exposure = np.ones(len(scores), dtype=float) if exposure is None else np.asarray(exposure, dtype=float)
    previous_weights: Dict[int, float] = {}
    rows: List[Dict[str, object]] = []

    for index in range(len(scores) - 1):
        if split_labels[index] != split_labels[index + 1]:
            continue
        signal_mask = eligible[index] & np.isfinite(scores[index]) & np.isfinite(risk[index])
        candidates = np.flatnonzero(signal_mask)
        if len(candidates) < config.minimum_assets:
            continue
        order = candidates[np.argsort(scores[index, candidates])[::-1]]
        target = max(20, int(round(len(order) * config.selection_fraction)))
        if config.selection_count > 0:
            target = min(config.selection_count, target)
        buffer_size = min(len(order), max(target, int(round(target * config.buffer_multiple))))
        buffer = set(order[:buffer_size])
        retained = [asset for asset in previous_weights if asset in buffer]
        chosen = retained[:target]
        chosen_set = set(chosen)
        for asset in order:
            if asset in chosen_set:
                continue
            chosen.append(int(asset))
            chosen_set.add(int(asset))
            if len(chosen) >= target:
                break
        inverse_risk = 1.0 / np.clip(risk[index, chosen], 0.08, 0.80)
        strength = np.clip(scores[index, chosen] - 0.50, 0.02, 0.50)
        raw = (
            inverse_risk * (0.75 + 0.25 * strength / max(float(np.max(strength)), 1e-12))
            if config.inverse_risk_weighting else np.ones(len(chosen), dtype=float)
        )
        weights = _capped_normalize(raw, config.maximum_weight)
        scale = float(np.clip(exposure[index], 0.0, 1.0))
        current_weights = {
            int(asset): float(weight * scale)
            for asset, weight in zip(chosen, weights)
            if np.isfinite(entry_prices[index, asset]) and entry_prices[index, asset] > 0.0
        }
        assets = set(previous_weights) | set(current_weights)
        turnover = 0.5 * sum(abs(current_weights.get(asset, 0.0) - previous_weights.get(asset, 0.0)) for asset in assets)
        exit_prices = np.where(
            np.isfinite(entry_prices[index + 1]) & (entry_prices[index + 1] > 0.0),
            entry_prices[index + 1], signal_close[index + 1],
        )
        realized = _safe_divide(exit_prices, entry_prices[index]) - 1.0
        gross = float(sum(weight * realized[asset] for asset, weight in current_weights.items()))
        benchmark_mask = signal_mask & np.isfinite(realized)
        benchmark_return = float(np.nanmean(realized[benchmark_mask]))
        strategy_return = gross - config.cost_rate * turnover
        row_ic = rank_ic(scores[index], realized, signal_mask & np.isfinite(realized))
        rows.append({
            "signal_date": dates[index], "exit_date": dates[index + 1],
            "split": split_labels[index], "strategy_return": strategy_return,
            "benchmark_return": benchmark_return, "gross_return": gross,
            "turnover": float(turnover), "exposure": scale, "rank_ic": row_ic,
            "holding_count": int(len(current_weights)),
        })
        previous_weights = current_weights

    metrics = {
        split: _metrics([row for row in rows if row["split"] == split])
        for split in ("train", "valid", "test")
    }
    metrics["full"] = _metrics(rows)
    nav, benchmark_nav = 1.0, 1.0
    curve: List[List[object]] = []
    for row in rows:
        nav *= 1.0 + float(row["strategy_return"])
        benchmark_nav *= 1.0 + float(row["benchmark_return"])
        curve.append([
            row["exit_date"], round(nav, 6), round(benchmark_nav, 6),
            round(nav / max(benchmark_nav, 1e-12), 6), row["split"],
            round(float(row["exposure"]), 4), round(float(row["turnover"]), 4),
        ])
    return {"metrics": metrics, "periods": rows, "curve": curve}


def combine_long_short_backtests(
    long_result: Mapping[str, object],
    bottom_result: Mapping[str, object],
    cost_rate: float,
) -> Dict[str, object]:
    """Combine two long books into a costed paper long-short factor diagnostic."""

    bottom_rows = {
        (row["signal_date"], row["exit_date"], row["split"]): row
        for row in bottom_result["periods"]
    }
    rows: List[Dict[str, object]] = []
    for long_row in long_result["periods"]:
        key = (long_row["signal_date"], long_row["exit_date"], long_row["split"])
        bottom_row = bottom_rows.get(key)
        if bottom_row is None:
            continue
        turnover = float(long_row["turnover"]) + float(bottom_row["turnover"])
        spread = (
            float(long_row["gross_return"])
            - float(bottom_row["gross_return"])
            - float(cost_rate) * turnover
        )
        rows.append({
            "signal_date": long_row["signal_date"],
            "exit_date": long_row["exit_date"],
            "split": long_row["split"],
            "strategy_return": spread,
            "benchmark_return": 0.0,
            "gross_return": float(long_row["gross_return"]) - float(bottom_row["gross_return"]),
            "turnover": turnover,
            "exposure": 0.0,
            "rank_ic": float(long_row["rank_ic"]),
            "holding_count": int(long_row["holding_count"]) + int(bottom_row["holding_count"]),
        })
    metrics = {
        split: _metrics([row for row in rows if row["split"] == split])
        for split in ("train", "valid", "test")
    }
    metrics["full"] = _metrics(rows)
    nav = 1.0
    curve: List[List[object]] = []
    for row in rows:
        nav *= 1.0 + float(row["strategy_return"])
        curve.append([row["exit_date"], round(nav, 6), 1.0, round(nav, 6), row["split"], 0.0, round(float(row["turnover"]), 4)])
    return {"metrics": metrics, "periods": rows, "curve": curve, "execution_mode": "paper_long_short_alpha"}


def selection_score(metrics: Mapping[str, Mapping[str, float]]) -> Dict[str, object]:
    """Train/validation-only gate; test metrics are intentionally never referenced."""

    train, valid = metrics["train"], metrics["valid"]
    gates = {
        "训练绝对夏普为正": train["sharpe"] > 0.0,
        "验证绝对夏普为正": valid["sharpe"] > 0.0,
        "训练超额为正": train["excess_annual_return"] > 0.0,
        "验证超额为正": valid["excess_annual_return"] > 0.0,
        "训练排序有效": train["rank_ic"] > 0.0,
        "验证排序有效": valid["rank_ic"] > 0.0,
        "验证样本充分": valid["periods"] >= 26,
    }
    score = (
        min(float(train["excess_sharpe"]), float(valid["excess_sharpe"]))
        + 0.35 * min(float(train["sharpe"]), float(valid["sharpe"]))
        + 2.0 * min(float(train["rank_ic"]), float(valid["rank_ic"]))
        - 0.10 * max(float(train["turnover"]), float(valid["turnover"]))
    )
    return {"score": float(score), "passed": bool(all(gates.values())), "gates": gates, "test_used": False}


def choose_champion(candidates: Mapping[str, Mapping[str, object]]) -> Dict[str, object]:
    """Choose from predeclared candidates without consulting sealed-test outcomes."""

    ranked = sorted(
        (
            (name, selection_score(candidate["metrics"]))
            for name, candidate in candidates.items()
        ),
        key=lambda item: item[1]["score"],
        reverse=True,
    )
    accepted = [item for item in ranked if item[1]["passed"]]
    selected_name, selected = (accepted[0] if accepted else ranked[0])
    return {
        "name": selected_name,
        "accepted": bool(selected["passed"]),
        "status": "validated_champion" if selected["passed"] else "observe_only_no_validated_strategy",
        "selection": selected,
        "ranking": [{"name": name, **score} for name, score in ranked],
        "test_used_for_selection": False,
    }
