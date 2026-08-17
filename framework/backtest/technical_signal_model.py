"""Broker-style pure technical signal stack.

This module is intentionally separate from the LLM/K-line memory agent.  It
turns local OHLCV-only indicators into auditable signal families, supports a
single-stock timing path, and exposes cross-sectional scores that can be tested
with the existing weekly portfolio backtester.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Dict, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from framework.backtest.kline_multiscale_expert import (
    TRADING_DAYS,
    WEEKS_PER_YEAR,
    _combine_ranked,
    _lag_return,
    _move_max,
    _move_mean,
    _move_min,
    _move_std,
    _safe_divide,
    rank_ic,
    row_rank,
)


TECHNICAL_MODEL_VERSION = "technical-signal-stack/1.0-broker-style"

DEFAULT_FAMILY_WEIGHTS = {
    "趋势动量": 0.22,
    "突破确认": 0.18,
    "回撤反转": 0.16,
    "量价确认": 0.16,
    "波动质量": 0.16,
    "防守择时": 0.12,
}


FACTOR_FRAMEWORK = [
    {
        "family": "趋势动量",
        "direction": "顺势做多",
        "indicators": ["20/60/120日动量", "均线排列", "路径效率", "低波动惩罚"],
        "failure_modes": ["横盘震荡", "高位放量滞涨", "趋势末端波动急升"],
    },
    {
        "family": "突破确认",
        "direction": "创新高且量价确认",
        "indicators": ["20/60日唐奇安突破", "收盘位置", "实体强度", "量比和额比"],
        "failure_modes": ["假突破", "一字板不可成交", "无量突破"],
    },
    {
        "family": "回撤反转",
        "direction": "中期上行中的短期回撤修复",
        "indicators": ["中期动量", "短期跌幅", "RSI回落", "下影线", "收盘修复"],
        "failure_modes": ["下跌中继", "基本面冲击", "流动性枯竭"],
    },
    {
        "family": "量价确认",
        "direction": "资金参与和价格同步",
        "indicators": ["成交量均线比", "成交额均线比", "OBV型净量", "成交额稳定性"],
        "failure_modes": ["脉冲式对倒", "公告日异常量", "高换手衰减"],
    },
    {
        "family": "波动质量",
        "direction": "低下行波动和高趋势效率优先",
        "indicators": ["20/60日波动", "下行波动", "60日回撤", "ATR区间", "效率比"],
        "failure_modes": ["低波动陷阱", "即将扩波", "停牌导致的虚假平稳"],
    },
    {
        "family": "防守择时",
        "direction": "市场状态和个股风险预算",
        "indicators": ["短期均线保护", "跳空风险", "回撤深度", "量价背离", "波动预算"],
        "failure_modes": ["V型反转踏空", "系统性行情切换", "连续涨跌停无法执行"],
    },
]


def _finite(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def _at(values: np.ndarray, signal_indices: Sequence[int]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)[np.asarray(signal_indices, dtype=np.int32)]



def _bounded(values: np.ndarray, window: int, minimum: int) -> tuple[int, int]:
    length = int(np.asarray(values).shape[0])
    bounded_window = max(1, min(int(window), length))
    bounded_minimum = max(1, min(int(minimum), bounded_window))
    return bounded_window, bounded_minimum


def _mean(values: np.ndarray, window: int, minimum: int) -> np.ndarray:
    bounded_window, bounded_minimum = _bounded(values, window, minimum)
    return _move_mean(values, bounded_window, bounded_minimum)


def _std(values: np.ndarray, window: int, minimum: int) -> np.ndarray:
    bounded_window, bounded_minimum = _bounded(values, window, minimum)
    return _move_std(values, bounded_window, bounded_minimum)


def _max(values: np.ndarray, window: int, minimum: int) -> np.ndarray:
    bounded_window, bounded_minimum = _bounded(values, window, minimum)
    return _move_max(values, bounded_window, bounded_minimum)


def _min(values: np.ndarray, window: int, minimum: int) -> np.ndarray:
    bounded_window, bounded_minimum = _bounded(values, window, minimum)
    return _move_min(values, bounded_window, bounded_minimum)

def _rsi(close: np.ndarray, window: int = 14) -> np.ndarray:
    returns = _lag_return(close, 1)
    gains = np.where(returns > 0.0, returns, 0.0)
    losses = np.where(returns < 0.0, -returns, 0.0)
    avg_gain = _mean(gains, window, max(5, window // 2))
    avg_loss = _mean(losses, window, max(5, window // 2))
    return _safe_divide(avg_gain, avg_gain + avg_loss)


def build_technical_signal_families(
    close: np.ndarray,
    open_price: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    signal_indices: Sequence[int],
    eligible_daily: np.ndarray,
) -> Dict[str, np.ndarray]:
    """Build six interpretable pure-technical signal families.

    Every family is computed with information available at the signal close.
    Returned values are cross-sectional percentile ranks on each signal date.
    """

    close = _finite(close)
    open_price = _finite(open_price)
    high = _finite(high)
    low = _finite(low)
    volume = _finite(volume)
    amount = _finite(amount)
    signal_indices = np.asarray(signal_indices, dtype=np.int32)
    eligible = np.asarray(eligible_daily[signal_indices], dtype=bool)

    returns = _lag_return(close, 1)
    mom5, mom10, mom20, mom60, mom120 = (_lag_return(close, lag) for lag in (5, 10, 20, 60, 120))
    ma5 = _mean(close, 5, 3)
    ma10 = _mean(close, 10, 6)
    ma20 = _mean(close, 20, 12)
    ma60 = _mean(close, 60, 36)
    ma120 = _mean(close, 120, 72)
    ma_stack = (
        0.35 * (_safe_divide(ma5, ma20) - 1.0)
        + 0.35 * (_safe_divide(ma20, ma60) - 1.0)
        + 0.30 * (_safe_divide(ma60, ma120) - 1.0)
    )
    path20 = _mean(np.abs(returns), 20, 12) * 20.0
    path60 = _mean(np.abs(returns), 60, 36) * 60.0
    efficiency20 = _safe_divide(np.abs(mom20), path20)
    efficiency60 = _safe_divide(np.abs(mom60), path60)

    high20 = _max(high, 20, 12)
    high60 = _max(high, 60, 36)
    high120 = _max(high, 120, 72)
    low20 = _min(low, 20, 12)
    low60 = _min(low, 60, 36)
    breakout20 = _safe_divide(close, high20) - 1.0
    breakout60 = _safe_divide(close, high60) - 1.0
    breakout120 = _safe_divide(close, high120) - 1.0

    candle_range = np.maximum(high - low, np.abs(close) * 1e-6)
    body = _safe_divide(close - open_price, candle_range)
    close_position = _safe_divide(close - low, candle_range)
    lower_wick = _safe_divide(np.minimum(open_price, close) - low, candle_range)
    upper_wick = _safe_divide(high - np.maximum(open_price, close), candle_range)
    prev_close = np.vstack([np.full((1, close.shape[1]), np.nan), close[:-1]])
    gap = _safe_divide(open_price, prev_close) - 1.0

    volume5 = _mean(volume, 5, 3)
    volume20 = _mean(volume, 20, 12)
    volume60 = _mean(volume, 60, 36)
    amount5 = _mean(amount, 5, 3)
    amount20 = _mean(amount, 20, 12)
    amount60 = _mean(amount, 60, 36)
    volume_ratio = np.log1p(np.maximum(_safe_divide(volume5, volume20), 0.0))
    amount_ratio = np.log1p(np.maximum(_safe_divide(amount5, amount20), 0.0))
    amount_stability = -_safe_divide(_std(amount, 20, 12), np.abs(amount20))
    signed_volume = np.sign(np.nan_to_num(returns, nan=0.0)) * np.log1p(np.maximum(volume, 0.0))
    obv20 = _mean(signed_volume, 20, 12)
    obv60 = _mean(signed_volume, 60, 36)

    vol20 = _std(returns, 20, 12) * sqrt(TRADING_DAYS)
    vol60 = _std(returns, 60, 36) * sqrt(TRADING_DAYS)
    downside20 = _std(np.minimum(returns, 0.0), 20, 12) * sqrt(TRADING_DAYS)
    range20 = _safe_divide(_mean(high - low, 20, 12), close)
    range60 = _safe_divide(_mean(high - low, 60, 36), close)
    drawdown60 = _safe_divide(close, _max(close, 60, 36)) - 1.0
    drawdown120 = _safe_divide(close, _max(close, 120, 72)) - 1.0
    rsi14 = _rsi(close, 14)
    ma_guard = _safe_divide(close, ma20) - 1.0
    volume_drift = _safe_divide(volume20, volume60) - 1.0
    amount_drift = _safe_divide(amount20, amount60) - 1.0

    families = {
        "趋势动量": _combine_ranked(
            [
                (_at(mom20, signal_indices), 0.20),
                (_at(mom60, signal_indices), 0.25),
                (_at(mom120, signal_indices), 0.18),
                (_at(ma_stack, signal_indices), 0.17),
                (_at(efficiency60, signal_indices), 0.12),
                (_at(vol20, signal_indices), -0.08),
            ],
            eligible,
        ),
        "突破确认": _combine_ranked(
            [
                (_at(breakout20, signal_indices), 0.20),
                (_at(breakout60, signal_indices), 0.22),
                (_at(breakout120, signal_indices), 0.12),
                (_at(close_position, signal_indices), 0.13),
                (_at(body, signal_indices), 0.10),
                (_at(volume_ratio, signal_indices), 0.13),
                (_at(amount_ratio, signal_indices), 0.10),
            ],
            eligible,
        ),
        "回撤反转": _combine_ranked(
            [
                (_at(mom60, signal_indices), 0.22),
                (_at(mom120, signal_indices), 0.16),
                (_at(mom5, signal_indices), -0.17),
                (_at(mom10, signal_indices), -0.08),
                (_at(rsi14, signal_indices), -0.12),
                (_at(lower_wick, signal_indices), 0.11),
                (_at(close_position, signal_indices), 0.10),
                (_at(upper_wick, signal_indices), -0.04),
            ],
            eligible,
        ),
        "量价确认": _combine_ranked(
            [
                (_at(volume_ratio, signal_indices), 0.16),
                (_at(amount_ratio, signal_indices), 0.16),
                (_at(obv20, signal_indices), 0.18),
                (_at(obv60, signal_indices), 0.12),
                (_at(volume_drift, signal_indices), 0.10),
                (_at(amount_drift, signal_indices), 0.10),
                (_at(close_position, signal_indices), 0.10),
                (_at(amount_stability, signal_indices), 0.08),
            ],
            eligible,
        ),
        "波动质量": _combine_ranked(
            [
                (_at(vol20, signal_indices), -0.20),
                (_at(_safe_divide(vol20, vol60), signal_indices), -0.12),
                (_at(downside20, signal_indices), -0.18),
                (_at(drawdown60, signal_indices), 0.16),
                (_at(drawdown120, signal_indices), 0.10),
                (_at(efficiency20, signal_indices), 0.12),
                (_at(efficiency60, signal_indices), 0.12),
                (_at(range20, signal_indices), -0.10),
            ],
            eligible,
        ),
        "防守择时": _combine_ranked(
            [
                (_at(ma_guard, signal_indices), 0.16),
                (_at(mom20, signal_indices), 0.14),
                (_at(drawdown60, signal_indices), 0.16),
                (_at(downside20, signal_indices), -0.15),
                (_at(range60, signal_indices), -0.12),
                (_at(gap, signal_indices), -0.08),
                (_at(_safe_divide(volume_ratio, np.maximum(np.abs(mom5), 1e-5)), signal_indices), -0.08),
                (_at(amount_stability, signal_indices), 0.11),
            ],
            eligible,
        ),
    }
    return {name: row_rank(values, eligible).astype(np.float32) for name, values in families.items()}


def technical_framework_payload() -> list[dict[str, Any]]:
    return [dict(item) for item in FACTOR_FRAMEWORK]


def _normalize_weights(weights: Mapping[str, float]) -> Dict[str, float]:
    clean = {name: max(float(value), 0.0) for name, value in weights.items()}
    total = sum(clean.values())
    if total <= 1e-12:
        clean = dict(DEFAULT_FAMILY_WEIGHTS)
        total = sum(clean.values())
    return {name: value / total for name, value in clean.items()}


def learn_family_weights_train_only(
    families: Mapping[str, np.ndarray],
    forward_returns: np.ndarray,
    eligible: np.ndarray,
    split_labels: Sequence[str],
    prior_weights: Mapping[str, float] | None = None,
    prior_strength: float = 0.60,
) -> dict[str, Any]:
    """Estimate family weights with train labels only.

    Validation and test labels are deliberately ignored here.  Validation is
    used later by the strategy gate, not by the signal formula.
    """

    prior = _normalize_weights(prior_weights or DEFAULT_FAMILY_WEIGHTS)
    forward_returns = np.asarray(forward_returns, dtype=np.float64)
    eligible = np.asarray(eligible, dtype=bool)
    train_indices = [index for index, split in enumerate(split_labels) if split == "train"]
    raw_scores: Dict[str, float] = {}
    diagnostics = []
    for name, values in families.items():
        ics = [
            rank_ic(values[index], forward_returns[index], eligible[index])
            for index in train_indices
            if np.isfinite(forward_returns[index]).any()
        ]
        clean = np.asarray([value for value in ics if np.isfinite(value)], dtype=float)
        mean_ic = float(np.mean(clean)) if len(clean) else 0.0
        weak_ic = float(np.percentile(clean, 25)) if len(clean) else 0.0
        stability = float(np.mean(clean > 0.0)) if len(clean) else 0.0
        score = max(0.0, 0.65 * mean_ic + 0.35 * weak_ic) * (0.5 + 0.5 * stability)
        raw_scores[name] = score
        diagnostics.append(
            {
                "family": name,
                "train_rank_ic_mean": mean_ic,
                "train_rank_ic_q25": weak_ic,
                "train_positive_ratio": stability,
                "raw_score": score,
            }
        )
    score_weights = _normalize_weights(raw_scores)
    learned = {
        name: prior_strength * prior.get(name, 0.0) + (1.0 - prior_strength) * score_weights.get(name, 0.0)
        for name in families
    }
    weights = _normalize_weights(learned)
    return {
        "version": TECHNICAL_MODEL_VERSION,
        "weights": weights,
        "diagnostics": diagnostics,
        "prior_weights": prior,
        "prior_strength": float(prior_strength),
        "train_labels_used_for_fit": True,
        "validation_labels_used_for_fit": False,
        "test_labels_used_for_fit": False,
    }


def combine_signal_families(
    families: Mapping[str, np.ndarray],
    eligible: np.ndarray,
    weights: Mapping[str, float] | None = None,
) -> np.ndarray:
    weights = _normalize_weights(weights or DEFAULT_FAMILY_WEIGHTS)
    arrays = [np.asarray(value, dtype=np.float64) for value in families.values()]
    if not arrays:
        return np.empty((0, 0), dtype=np.float32)
    output = np.zeros_like(arrays[0], dtype=np.float64)
    weight_sum = np.zeros_like(arrays[0], dtype=np.float64)
    eligible = np.asarray(eligible, dtype=bool)
    for name, values in families.items():
        weight = float(weights.get(name, 0.0))
        valid = np.isfinite(values)
        output[valid] += weight * values[valid]
        weight_sum[valid] += abs(weight)
    combined = _safe_divide(output, weight_sum)
    combined[~eligible] = np.nan
    return row_rank(combined, eligible).astype(np.float32)


def technical_family_diagnostics(
    families: Mapping[str, np.ndarray],
    forward_returns: np.ndarray,
    eligible: np.ndarray,
    split_labels: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    forward_returns = np.asarray(forward_returns, dtype=np.float64)
    eligible = np.asarray(eligible, dtype=bool)
    for name, values in families.items():
        row: dict[str, Any] = {"family": name}
        for split in ("train", "valid", "test"):
            indices = [index for index, label in enumerate(split_labels) if label == split]
            ics = [
                rank_ic(values[index], forward_returns[index], eligible[index])
                for index in indices
            ]
            clean = np.asarray([value for value in ics if np.isfinite(value)], dtype=float)
            row[f"{split}_rank_ic"] = float(np.mean(clean)) if len(clean) else 0.0
            row[f"{split}_positive_ratio"] = float(np.mean(clean > 0.0)) if len(clean) else 0.0
            row[f"{split}_periods"] = int(len(clean))
        rows.append(row)
    return rows


@dataclass(frozen=True)
class SingleAssetTimingConfig:
    entry_quantiles: Tuple[float, ...] = (0.55, 0.60, 0.65, 0.70)
    exit_quantiles: Tuple[float, ...] = (0.35, 0.40, 0.45, 0.50)
    cost_rate: float = 0.0015
    annualization: int = TRADING_DAYS


def _timing_rows(
    score: np.ndarray,
    realized_returns: np.ndarray,
    split_labels: Sequence[str],
    entry_threshold: float,
    exit_threshold: float,
    cost_rate: float,
) -> list[dict[str, Any]]:
    position = 0.0
    rows: list[dict[str, Any]] = []
    previous_split = None
    for index, split in enumerate(split_labels):
        if previous_split is not None and split != previous_split:
            position = 0.0
        value = float(score[index]) if np.isfinite(score[index]) else np.nan
        target = position
        if np.isfinite(value):
            if position <= 0.0 and value >= entry_threshold:
                target = 1.0
            elif position > 0.0 and value <= exit_threshold:
                target = 0.0
        turnover = abs(target - position)
        realized = float(realized_returns[index]) if np.isfinite(realized_returns[index]) else 0.0
        rows.append(
            {
                "index": index,
                "split": split,
                "position": target,
                "strategy_return": target * realized - cost_rate * turnover,
                "benchmark_return": realized,
                "turnover": turnover,
            }
        )
        position = target
        previous_split = split
    return rows


def _path_metrics(rows: Sequence[Mapping[str, Any]], annualization: int) -> dict[str, float]:
    if not rows:
        return {
            "periods": 0,
            "annual_return": 0.0,
            "benchmark_annual_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "turnover": 0.0,
            "exposure": 0.0,
        }
    strategy = np.asarray([row["strategy_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    nav = np.cumprod(1.0 + np.nan_to_num(strategy, nan=0.0))
    peaks = np.maximum.accumulate(nav)
    std = float(np.std(strategy, ddof=1)) if len(strategy) > 1 else 0.0
    return {
        "periods": int(len(rows)),
        "annual_return": float(np.prod(1.0 + strategy) ** (annualization / len(strategy)) - 1.0),
        "benchmark_annual_return": float(np.prod(1.0 + benchmark) ** (annualization / len(benchmark)) - 1.0),
        "sharpe": float(np.mean(strategy) / std * sqrt(annualization)) if std > 1e-12 else 0.0,
        "max_drawdown": float(np.min(nav / np.maximum(peaks, 1e-12) - 1.0)),
        "turnover": float(np.mean([row["turnover"] for row in rows])),
        "exposure": float(np.mean([row["position"] for row in rows])),
    }


def calibrate_single_asset_timing(
    score: Sequence[float],
    realized_returns: Sequence[float],
    split_labels: Sequence[str],
    config: SingleAssetTimingConfig = SingleAssetTimingConfig(),
) -> dict[str, Any]:
    """Choose single-stock thresholds on train only and report all splits."""

    score = np.asarray(score, dtype=np.float64)
    realized = np.asarray(realized_returns, dtype=np.float64)
    split_labels = list(split_labels)
    train_score = score[[index for index, split in enumerate(split_labels) if split == "train"]]
    train_score = train_score[np.isfinite(train_score)]
    if len(train_score) < 20:
        raise ValueError("single-asset timing requires at least 20 finite train scores")
    candidates = []
    for entry_q in config.entry_quantiles:
        for exit_q in config.exit_quantiles:
            if exit_q >= entry_q:
                continue
            entry = float(np.quantile(train_score, entry_q))
            exit_ = float(np.quantile(train_score, exit_q))
            rows = _timing_rows(score, realized, split_labels, entry, exit_, config.cost_rate)
            train_rows = [row for row in rows if row["split"] == "train"]
            metrics = _path_metrics(train_rows, config.annualization)
            objective = (
                metrics["sharpe"]
                + 0.25 * (metrics["annual_return"] - metrics["benchmark_annual_return"])
                - 0.10 * metrics["turnover"]
                + 0.20 * metrics["max_drawdown"]
            )
            candidates.append((objective, entry, exit_, entry_q, exit_q, metrics))
    if not candidates:
        raise ValueError("no valid entry/exit threshold candidates")
    _, entry, exit_, entry_q, exit_q, train_metrics = max(candidates, key=lambda item: item[0])
    rows = _timing_rows(score, realized, split_labels, entry, exit_, config.cost_rate)
    metrics = {
        split: _path_metrics([row for row in rows if row["split"] == split], config.annualization)
        for split in ("train", "valid", "test")
    }
    metrics["full"] = _path_metrics(rows, config.annualization)
    return {
        "version": TECHNICAL_MODEL_VERSION,
        "entry_threshold": entry,
        "exit_threshold": exit_,
        "entry_quantile": entry_q,
        "exit_quantile": exit_q,
        "train_metrics_used_for_fit": train_metrics,
        "metrics": metrics,
        "rows": rows,
        "train_labels_used_for_fit": True,
        "validation_labels_used_for_fit": False,
        "test_labels_used_for_fit": False,
    }


def split_label_alias(split: str) -> str:
    return "validation" if split == "valid" else str(split)


def compact_weekly_metrics(metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        split_label_alias(split): {
            key: float(value) if isinstance(value, (int, float, np.floating)) else value
            for key, value in values.items()
            if key in {
                "periods",
                "annual_return",
                "benchmark_annual_return",
                "excess_annual_return",
                "sharpe",
                "excess_sharpe",
                "max_drawdown",
                "turnover",
                "rank_ic",
                "win_rate",
            }
        }
        for split, values in metrics.items()
    }


