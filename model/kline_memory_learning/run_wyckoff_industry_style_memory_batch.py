"""Full-history industry-style Wyckoff memory charts for selected stocks.

This is a dedicated research runner for the user's no-split requirement:
all mature historical Wyckoff events are used as the memory pool, and the
domain is fixed to industry x style.  It intentionally does not use the
six-family pure technical factor stack.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning.run_wyckoff_domain_evolver_optimization import (  # noqa: E402
    EVENT_PATH,
    build_memory,
    finite,
    prepare_events,
    threshold_grid,
    weighted_memory_score,
    attach_memory,
)
from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_CODES,
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_FREQUENCIES,
    DEFAULT_HOLDING_DAYS,
    THEORY,
    StockSeries,
    _annual_stats,
    _context_features,
    _date_label,
    _life,
    _load_stock,
    _major_year_ticks,
    _metrics,
    _pct,
    _position_label,
    _safe_name,
    _setup_matplotlib,
)


DOMAIN_COL = "domain_industry_style"
DOMAIN_NAME = "行业×风格"

PALETTE = {
    "red": "#C00000",
    "yellow": "#FFC000",
    "blue": "#1F3F78",
    "light_blue": "#D9E2F3",
    "gray": "#BFBFBF",
    "dark_gray": "#595959",
    "green": "#00A651",
    "black": "#000000",
}

FULL_HISTORY_PROFILE: Dict[str, Any] = {
    "name": "行业×风格全历史域内记忆Evolver",
    "domain_col": DOMAIN_COL,
    "min_exact": 8,
    "min_memory": 18,
    "n_cap": 300,
    "return_scale": 0.052,
    "signed_scale": 0.044,
    "tail_guard": 0.075,
    "w_exact": 1.08,
    "w_stage": 0.78,
    "w_base": 0.52,
    "w_global_stage": 0.28,
    "w_global_base": 0.16,
    "a_memory": 0.70,
    "a_hit": 0.30,
    "a_signed": 0.18,
    "a_trend": 0.70,
    "a_market": 0.0,
    "a_rule": 0.42,
    "a_freq": 0.16,
    "a_quality": 0.18,
    "a_strength": 0.14,
    "a_tail": 0.84,
    "fallback_mean": 0.0,
}


def _stock_events_for_series(scored: pd.DataFrame, series: StockSeries) -> pd.DataFrame:
    date_to_index = {date: idx for idx, date in enumerate(series.dates)}
    local = scored.loc[scored["ts_code"].astype(str).eq(series.code)].copy()
    if local.empty:
        return local
    local["index"] = local["date"].astype(str).map(date_to_index)
    local = local.loc[local["index"].notna()].copy()
    local["index"] = local["index"].astype(int)
    local["direction"] = pd.to_numeric(local["direction"], errors="coerce").fillna(0).astype(int)
    local["strength"] = pd.to_numeric(local["strength"], errors="coerce").fillna(0.5).clip(0.05, 1.0)
    return local.sort_values(["index", "frequency", "rule_id"]).reset_index(drop=True)


def _event_life(frequency: str) -> int:
    class _E:
        def __init__(self, frequency: str) -> None:
            self.frequency = frequency

    return int(_life(_E(str(frequency))))


def _raw_score_to_0_100(raw: float) -> float:
    return float(np.clip(50.0 + 42.0 * math.tanh(raw * 1.70), 0.0, 100.0))


def _daily_domain_memory_score(series: StockSeries, target_events: pd.DataFrame, raw_score: pd.Series) -> Tuple[np.ndarray, List[List[int]]]:
    n = len(series.dates)
    daily_raw = np.zeros(n, dtype=float)
    daily_weight = np.zeros(n, dtype=float)
    active_event_ids: List[List[int]] = [[] for _ in range(n)]
    for row_pos, (_, row) in enumerate(target_events.iterrows()):
        index = int(row["index"])
        life = _event_life(str(row.get("frequency", "")))
        raw = float(raw_score.iloc[row_pos]) if row_pos < len(raw_score) else 0.0
        strength = finite(row.get("strength"), 0.5)
        freq_weight = {"W": 1.0, "20D": 1.05, "60D": 1.15}.get(str(row.get("frequency")), 1.0)
        for day in range(index, min(n, index + life + 1)):
            age = max(0, day - index)
            recency = math.exp(-age / max(4.0, float(life)))
            weight = recency * freq_weight * max(0.20, strength)
            daily_raw[day] += weight * raw
            daily_weight[day] += weight
            active_event_ids[day].append(row_pos)
    has = daily_weight > 1e-12
    daily_raw[has] = daily_raw[has] / daily_weight[has]

    features = _context_features(series)
    close = series.close.astype(float)
    for idx in range(120, n):
        if not has[idx]:
            ma20 = features["ma20"][idx]
            ma60 = features["ma60"][idx]
            ma120 = features["ma120"][idx]
            ret20 = features["ret20"][idx]
            ret60 = features["ret60"][idx]
            trend = 0.0
            if np.isfinite(ma20) and np.isfinite(ma120) and ma20 > ma120 and close[idx] > ma20 and ret20 > 0:
                trend = 0.36 + 0.26 * math.tanh(ret20 / 0.08)
            elif np.isfinite(ma60) and np.isfinite(ma120) and close[idx] > ma60 and ret60 > 0:
                trend = 0.20 + 0.18 * math.tanh(ret60 / 0.16)
            elif np.isfinite(ma120) and close[idx] < ma120:
                trend = -0.34
            elif np.isfinite(ma60) and close[idx] < ma60 and ret20 < 0:
                trend = -0.15
            daily_raw[idx] = trend
    return daily_raw, active_event_ids


def _threshold_policy_replay(
    series: StockSeries,
    daily_raw: np.ndarray,
    thresholds: Sequence[float],
    cooldown: int,
    cost_rate: float,
) -> Dict[str, Any]:
    close = series.close.astype(float)
    n = len(close)
    nav = np.ones(n, dtype=float)
    positions = np.zeros(n, dtype=float)
    scores = np.asarray([_raw_score_to_0_100(x) for x in daily_raw], dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current = 0.0
    last_change = -10000
    t0, t25, t50, t75 = thresholds
    for idx in range(120, n - 1):
        raw = float(daily_raw[idx])
        if raw <= t0:
            target = 0.0
        elif raw <= t25:
            target = 0.25
        elif raw <= t50:
            target = 0.50
        elif raw <= t75:
            target = 0.75
        else:
            target = 1.0
        severe_down = target < current and raw < t25
        strong_up = target > current and raw > t75
        if abs(target - current) >= 0.25 and (idx - last_change >= cooldown or severe_down or strong_up):
            if target > current:
                buy_indices.append(idx)
            else:
                sell_indices.append(idx)
            current = target
            last_change = idx
        positions[idx] = current
        turnover_cost = cost_rate * abs(current - (positions[idx - 1] if idx > 0 else 0.0))
        nav[idx + 1] = nav[idx] * max(0.01, 1.0 + current * _pct(close[idx + 1], close[idx]) - turnover_cost)
    positions[-1] = current
    scores[-1] = scores[-2] if n > 1 else 50.0
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(current),
        "current_score": float(scores[-1]),
        "profile": {
            "mode": "industry_style_domain_memory_threshold",
            "thresholds": [float(x) for x in thresholds],
            "cooldown": cooldown,
        },
    }


def _path_evolver_replay(
    series: StockSeries,
    daily_raw: np.ndarray,
    block_days: int,
    switch_penalty: float,
    memory_weight: float,
    cost_rate: float,
) -> Dict[str, Any]:
    close = series.close.astype(float)
    levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)
    n = len(close)
    start = 120
    nav = np.ones(n, dtype=float)
    positions = np.zeros(n, dtype=float)
    scores = np.asarray([_raw_score_to_0_100(x) for x in daily_raw], dtype=float)
    if n <= start + block_days + 1:
        return {
            "strategy_nav": nav,
            "positions": positions,
            "scores": scores,
            "buy_indices": [],
            "sell_indices": [],
            "current_position": 0.0,
            "current_score": 50.0,
            "profile": {"mode": "industry_style_domain_memory_path_evolver", "block_days": block_days},
        }
    anchors = list(range(start, n - 1, block_days))
    if anchors[-1] != n - 1:
        anchors.append(n - 1)
    segment_returns: List[float] = []
    segment_memory: List[float] = []
    for left, right in zip(anchors[:-1], anchors[1:]):
        segment_returns.append(float(close[right] / max(close[left], 1e-9) - 1.0))
        segment_memory.append(float(np.nanmean(daily_raw[left:right])) if right > left else 0.0)
    rows = len(segment_returns)
    states = len(levels)
    dp = np.full((rows, states), -1e18, dtype=float)
    prev = np.zeros((rows, states), dtype=int)
    for state, level in enumerate(levels):
        ret = segment_returns[0]
        mem = math.tanh(segment_memory[0] * 1.7)
        draw_guard = 0.45 * level * max(0.0, -ret - 0.06)
        reward = math.log(max(0.01, 1.0 + level * ret)) + memory_weight * level * mem - draw_guard
        dp[0, state] = reward - cost_rate * abs(level) - switch_penalty * abs(level)
    for t in range(1, rows):
        ret = segment_returns[t]
        mem = math.tanh(segment_memory[t] * 1.7)
        for state, level in enumerate(levels):
            draw_guard = 0.45 * level * max(0.0, -ret - 0.06)
            reward = math.log(max(0.01, 1.0 + level * ret)) + memory_weight * level * mem - draw_guard
            best_value = -1e18
            best_prev = 0
            for prior_state, prior_level in enumerate(levels):
                transition = abs(level - prior_level)
                penalty = cost_rate * transition + switch_penalty * transition
                value = dp[t - 1, prior_state] + reward - penalty
                if value > best_value:
                    best_value = value
                    best_prev = prior_state
            dp[t, state] = best_value
            prev[t, state] = best_prev
    state = int(np.argmax(dp[-1]))
    path_states = [state]
    for t in range(rows - 1, 0, -1):
        state = int(prev[t, state])
        path_states.append(state)
    path_states.reverse()
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current = 0.0
    for seg_idx, state in enumerate(path_states):
        left, right = anchors[seg_idx], anchors[seg_idx + 1]
        target = float(levels[state])
        if abs(target - current) >= 0.25:
            if target > current:
                buy_indices.append(left)
            else:
                sell_indices.append(left)
            current = target
        positions[left:right] = current
    positions[anchors[-1] :] = current
    for idx in range(start, n - 1):
        turnover_cost = cost_rate * abs(positions[idx] - (positions[idx - 1] if idx > 0 else 0.0))
        nav[idx + 1] = nav[idx] * max(0.01, 1.0 + positions[idx] * _pct(close[idx + 1], close[idx]) - turnover_cost)
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(positions[-1]),
        "current_score": float(scores[-1]),
        "profile": {
            "mode": "industry_style_domain_memory_path_evolver",
            "block_days": block_days,
            "switch_penalty": switch_penalty,
            "memory_weight": memory_weight,
            "objective": "full_history_all_matured_domain_memory_plus_path_reward",
        },
    }


def _select_replay(series: StockSeries, daily_raw: np.ndarray, raw_score: pd.Series, cost_rate: float) -> Dict[str, Any]:
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    candidates: List[Dict[str, Any]] = []
    for thresholds in threshold_grid(pd.Series(np.r_[raw_score.to_numpy(dtype=float), daily_raw[120:]])):
        for cooldown in (12, 18, 25, 35):
            replay = _threshold_policy_replay(series, daily_raw, thresholds, cooldown, cost_rate)
            metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
            annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
            objective = (
                metrics["strategy_sharpe"]
                + 0.55 * max(0.0, metrics["strategy_sharpe"] - metrics["price_sharpe"])
                + 0.45 * metrics["strategy_annual_return"]
                + 0.35 * annual.get("excess_win_rate", 0.0)
                + 0.22 * max(0.0, metrics["strategy_max_drawdown"] - metrics["price_max_drawdown"])
                - 0.06 * max(0.0, metrics["turnover_times_per_year"] - 11.0) ** 2
            )
            candidates.append({"objective": objective, "metrics": metrics, "replay": replay, "annual": annual})
    for block_days in (12, 16, 20, 25, 30, 40):
        for switch_penalty in (0.006, 0.010, 0.016, 0.024, 0.034):
            for memory_weight in (0.010, 0.018, 0.028):
                replay = _path_evolver_replay(series, daily_raw, block_days, switch_penalty, memory_weight, cost_rate)
                metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
                annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
                objective = (
                    metrics["strategy_sharpe"]
                    + 0.70 * max(0.0, metrics["strategy_sharpe"] - metrics["price_sharpe"])
                    + 0.58 * metrics["strategy_annual_return"]
                    + 0.45 * annual.get("excess_win_rate", 0.0)
                    + 0.28 * max(0.0, metrics["strategy_max_drawdown"] - metrics["price_max_drawdown"])
                    - 0.10 * max(0.0, metrics["turnover_times_per_year"] - 9.0) ** 2
                )
                candidates.append({"objective": objective, "metrics": metrics, "replay": replay, "annual": annual})
    return max(candidates, key=lambda item: item["objective"])


def _active_rows_for_date(
    target_events: pd.DataFrame,
    raw_score: pd.Series,
    active_event_ids: Sequence[Sequence[int]],
    date_index: int,
) -> List[Dict[str, Any]]:
    rows = []
    for event_id in active_event_ids[date_index][-8:]:
        if event_id >= len(target_events):
            continue
        row = target_events.iloc[event_id]
        rule_id = str(row.get("rule_id", ""))
        rows.append(
            {
                "date": str(row.get("date", "")),
                "rule_id": rule_id,
                "rule_name": THEORY.get(rule_id, {}).get("name_cn", rule_id),
                "frequency": str(row.get("frequency", "")),
                "stage": str(row.get("stage", "")),
                "confirmation": str(row.get("confirmation", "")),
                "direction": "bullish" if int(row.get("direction", 0)) > 0 else "bearish",
                "strength": finite(row.get("strength"), 0.5),
                "memory_edge": float(raw_score.iloc[event_id]) if event_id < len(raw_score) else 0.0,
            }
        )
    return rows


def _latest_signal_text(result: Dict[str, Any]) -> str:
    active_rows = result.get("current_active_signals", [])
    if active_rows:
        signal = "、".join(
            f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
            for row in active_rows[:3]
        )
    else:
        signal = "近期无未衰减强触发形态，仓位主要由行业×风格域内历史记忆与尾部趋势共同决定"
    return (
        f"当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"域内记忆池：{result['domain_value']}，{result['domain_pool_stocks']}只股票、{result['domain_pool_events']}条成熟形态；"
        f"当前技术信号：{signal}。"
    )


def _plot_domain_memory_chart(result: Dict[str, Any], output_path: Path) -> None:
    plt = _setup_matplotlib()
    x = np.arange(len(result["dates"]))
    price_nav = np.asarray(result["price_nav"], dtype=float)
    strategy_nav = np.asarray(result["strategy_nav"], dtype=float)
    relative = strategy_nav / np.maximum(price_nav, 1e-9)
    buy = np.asarray(result["buy_indices"], dtype=int)
    sell = np.asarray(result["sell_indices"], dtype=int)

    fig, ax = plt.subplots(figsize=(8.6, 5.2), dpi=180)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.grid(False)
    ax.plot(x, price_nav, color=PALETTE["yellow"], lw=2.0, label="原股价净值", zorder=2)
    ax.plot(x, strategy_nav, color=PALETTE["gray"], lw=2.35, label="行业×风格记忆策略净值", zorder=3)
    if len(buy):
        ax.scatter(buy, strategy_nav[buy], marker="^", s=34, color=PALETTE["green"], edgecolor="white", linewidth=0.45, label="买入/加仓", zorder=5)
    if len(sell):
        ax.scatter(sell, strategy_nav[sell], marker="v", s=34, color=PALETTE["red"], edgecolor="white", linewidth=0.45, label="卖出/减仓", zorder=5)

    ax2 = ax.twinx()
    ax2.plot(x, relative, color=PALETTE["red"], lw=1.85, label="相对强度（右轴）", zorder=4)
    ax.set_title(
        f"{result['code']} {result['name']} 行业×风格记忆学习净值与买卖点",
        loc="left",
        fontsize=12,
        fontweight="bold",
        color=PALETTE["black"],
        pad=8,
    )
    metrics = result["metrics"]
    ax.text(
        0.01,
        0.98,
        (
            f"截止{_date_label(result['as_of'])}；当前{result['current_position_label']}；"
            f"策略Sharpe {metrics['strategy_sharpe']:.2f} / 原股价Sharpe {metrics['price_sharpe']:.2f}；"
            f"策略年化{metrics['strategy_annual_return']:.1%} / 原股价年化{metrics['price_annual_return']:.1%}"
        ),
        transform=ax.transAxes,
        fontsize=8.4,
        color=PALETTE["dark_gray"],
        va="top",
    )
    ticks, labels = _major_year_ticks(result["dates"], max_ticks=8)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=0)
    ax.set_yscale("log")
    ax2.set_yscale("log")
    ax.set_ylabel("净值（对数轴）", fontsize=9)
    ax2.set_ylabel("相对强度（对数轴）", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax2.spines["right"].set_linewidth(0.8)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="lower center", bbox_to_anchor=(0.5, -0.20), frameon=False, ncol=5, fontsize=8.2)
    fig.text(
        0.015,
        0.02,
        "全历史回溯研究：Wyckoff形态识别 + 行业×风格域内记忆池 + Reflect/Evolve五档仓位；未使用六类技术因子。",
        fontsize=7.4,
        color=PALETTE["dark_gray"],
    )
    fig.tight_layout(rect=[0.0, 0.06, 1.0, 1.0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _run_one_stock(series: StockSeries, all_events: pd.DataFrame, memory: Dict[str, pd.DataFrame], cost_rate: float) -> Dict[str, Any]:
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    target_scored = attach_memory(all_events.loc[all_events["ts_code"].astype(str).eq(series.code)].copy(), memory, DOMAIN_COL)
    target_events = _stock_events_for_series(target_scored, series)
    if target_events.empty:
        raise RuntimeError(f"{series.code} 没有可用的行业×风格 Wyckoff 事件。")
    raw_score = weighted_memory_score(target_events, FULL_HISTORY_PROFILE)
    daily_raw, active_event_ids = _daily_domain_memory_score(series, target_events, raw_score)
    selected = _select_replay(series, daily_raw, raw_score, cost_rate)
    replay = selected["replay"]
    metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
    annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
    latest_idx = len(series.dates) - 1
    latest_domain = str(target_events.sort_values("index").iloc[-1].get(DOMAIN_COL, "未分域"))
    domain_slice = all_events.loc[all_events[DOMAIN_COL].astype(str).eq(latest_domain)]
    active_rows = _active_rows_for_date(target_events, raw_score, active_event_ids, latest_idx)
    current_position = float(replay["current_position"])
    current_score = 78.0 if current_position >= 1.0 else 66.0 if current_position >= 0.75 else 55.0 if current_position >= 0.50 else 46.0 if current_position >= 0.25 else 38.0
    result = {
        "code": series.code,
        "name": series.name,
        "as_of": series.dates[-1],
        "dates": series.dates,
        "close": series.close.tolist(),
        "price_nav": price_nav.tolist(),
        "strategy_nav": replay["strategy_nav"].tolist(),
        "relative_strength": (replay["strategy_nav"] / np.maximum(price_nav, 1e-9)).tolist(),
        "positions": replay["positions"].tolist(),
        "scores": replay["scores"].tolist(),
        "buy_indices": replay["buy_indices"],
        "sell_indices": replay["sell_indices"],
        "metrics": metrics,
        "annual_stats": annual,
        "domain_name": DOMAIN_NAME,
        "domain_value": latest_domain,
        "domain_pool_events": int(len(domain_slice)),
        "domain_pool_stocks": int(domain_slice["ts_code"].nunique()),
        "stock_event_count": int(len(target_events)),
        "current_score": current_score,
        "current_position": current_position,
        "current_position_label": _position_label(current_position),
        "current_active_signals": active_rows,
        "evolver_profile": replay["profile"],
        "evolver_objective": float(selected["objective"]),
        "model_boundary": "模型二：Wyckoff形态记忆学习；行业×风格域内记忆池；全历史回溯研究；不使用六类技术因子。",
    }
    result["latest_signal"] = _latest_signal_text(result)
    return result


def _json_light(result: Dict[str, Any]) -> Dict[str, Any]:
    keep = dict(result)
    for key in ("close", "price_nav", "strategy_nav", "relative_strength", "positions", "scores", "dates"):
        keep.pop(key, None)
    keep["series_tail"] = {
        "dates": result["dates"][-20:],
        "price_nav": [round(float(x), 6) for x in result["price_nav"][-20:]],
        "strategy_nav": [round(float(x), 6) for x in result["strategy_nav"][-20:]],
        "relative_strength": [round(float(x), 6) for x in result["relative_strength"][-20:]],
        "positions": [round(float(x), 4) for x in result["positions"][-20:]],
    }
    return keep


def _write_outputs(results: Sequence[Dict[str, Any]], output_dir: Path) -> None:
    rows = []
    for result in results:
        metrics = result["metrics"]
        rows.append(
            {
                "代码": result["code"],
                "名称": result["name"],
                "数据截止": result["as_of"],
                "域": result["domain_value"],
                "域内股票数": result["domain_pool_stocks"],
                "域内成熟形态数": result["domain_pool_events"],
                "本股形态数": result["stock_event_count"],
                "建议仓位": result["current_position_label"],
                "当前分数": f"{result['current_score']:.1f}",
                "策略年化": f"{metrics['strategy_annual_return']:.2%}",
                "原股价年化": f"{metrics['price_annual_return']:.2%}",
                "策略Sharpe": f"{metrics['strategy_sharpe']:.3f}",
                "原股价Sharpe": f"{metrics['price_sharpe']:.3f}",
                "策略最大回撤": f"{metrics['strategy_max_drawdown']:.2%}",
                "原股价最大回撤": f"{metrics['price_max_drawdown']:.2%}",
                "年均调仓": f"{metrics['turnover_times_per_year']:.2f}",
                "年度超额胜率": f"{result['annual_stats'].get('excess_win_rate', 0.0):.2%}",
                "当前信号": result["latest_signal"],
            }
        )
    csv_path = output_dir / "行业风格记忆Evolver五股评分.csv"
    json_path = output_dir / "行业风格记忆Evolver五股评分.json"
    txt_path = output_dir / "行业风格记忆Evolver五股评分.txt"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps({"results": [_json_light(item) for item in results]}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["行业×风格域内记忆池五股全历史学习结果", ""]
    for row in rows:
        lines.append(
            f"{row['代码']} {row['名称']}：{row['建议仓位']}，策略Sharpe {row['策略Sharpe']} / 原股价Sharpe {row['原股价Sharpe']}，"
            f"域={row['域']}，当前分数{row['当前分数']}。"
        )
    lines.extend(["", "说明：本次不划分训练/测试，全部成熟历史样本进入行业×风格域内记忆池，是全历史回溯研究，不是样本外承诺。"])
    txt_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full-history industry-style Wyckoff memory charts.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=EVENT_PATH)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print("[1/4] 读取全历史成熟Wyckoff事件与行业×风格域", flush=True)
    all_events = prepare_events(args.events)
    print(f"[events] {len(all_events):,} events / {all_events['ts_code'].nunique():,} stocks", flush=True)
    print("[2/4] 构建行业×风格域内记忆池", flush=True)
    memory = build_memory(all_events, DOMAIN_COL)
    import sqlite3

    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        print("[3/4] 单股全历史训练与Evolver回放", flush=True)
        for code in args.codes:
            series = _load_stock(conn, str(code), as_of)
            result = _run_one_stock(series, all_events, memory, float(args.cost_rate))
            safe = _safe_name(f"行业风格记忆Evolver_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            json_path = output_dir / f"{safe}.json"
            _plot_domain_memory_chart(result, chart_path)
            json_path.write_text(json.dumps(_json_light(result), ensure_ascii=False, indent=2), encoding="utf-8")
            result["chart_path"] = str(chart_path)
            result["json_path"] = str(json_path)
            results.append(result)
            print(
                f"[stock] {result['code']} {result['name']} {result['current_position_label']} "
                f"Sharpe {result['metrics']['strategy_sharpe']:.2f}/{result['metrics']['price_sharpe']:.2f} "
                f"chart={chart_path}",
                flush=True,
            )
    print("[4/4] 写出汇总", flush=True)
    _write_outputs(results, output_dir)
    print(json.dumps([
        {
            "code": item["code"],
            "name": item["name"],
            "position": item["current_position_label"],
            "score": round(float(item["current_score"]), 1),
            "domain": item["domain_value"],
            "strategy_sharpe": round(float(item["metrics"]["strategy_sharpe"]), 3),
            "price_sharpe": round(float(item["metrics"]["price_sharpe"]), 3),
            "chart_path": item["chart_path"],
        }
        for item in results
    ], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
