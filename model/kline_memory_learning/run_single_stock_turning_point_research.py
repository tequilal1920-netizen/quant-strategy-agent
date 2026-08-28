"""Full-history sparse turning-point teacher for single-stock K-line learning.

This research helper is intentionally separated from the strict
train/validation/test single-stock analyzer.  It implements the user-requested
full-history learning mode: learn sparse historical turning points, distill
them into five position levels, and export a Chinese learning record plus a
diagnostic chart.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = (
    ROOT
    / "output"
    / "kline_memory_learning"
    / "single_stock_600737_20260821_sparse"
    / "learned_kline_result.json"
)
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\Rye\Desktop\技术分析")
DEFAULT_COST_RATE = 0.001
TRADING_DAYS = 252.0
POSITION_LEVELS = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)


@dataclass(frozen=True)
class PivotConfig:
    threshold: float
    min_gap: int
    ramp_days: int
    rebalance_days: int
    stop_drawdown: float
    allow_trend_override: bool


@dataclass
class StrategyResult:
    config: PivotConfig
    dates: List[str]
    price_nav: np.ndarray
    strategy_nav: np.ndarray
    position: np.ndarray
    buy_indices: List[int]
    sell_indices: List[int]
    metrics: Dict[str, float]
    learning_records: List[Dict[str, object]]
    current_advice: Dict[str, object]


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except Exception:
        return default
    if not math.isfinite(result):
        return default
    return result


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    output = np.full(len(values), np.nan, dtype=float)
    if len(values) < window:
        return output
    cumsum = np.cumsum(np.r_[0.0, values])
    output[window - 1 :] = (cumsum[window:] - cumsum[:-window]) / float(window)
    return output


def _rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    output = np.full(len(values), np.nan, dtype=float)
    for index in range(window - 1, len(values)):
        output[index] = float(np.max(values[index - window + 1 : index + 1]))
    return output


def _max_drawdown(nav: np.ndarray) -> float:
    if len(nav) == 0:
        return 0.0
    peak = np.maximum.accumulate(nav)
    return float(np.min(nav / np.maximum(peak, 1e-12) - 1.0))


def _annual_return(nav: np.ndarray) -> float:
    if len(nav) < 2 or nav[-1] <= 0:
        return 0.0
    years = len(nav) / TRADING_DAYS
    return float(nav[-1] ** (1.0 / max(years, 1e-9)) - 1.0)


def _sharpe(daily_return: np.ndarray) -> float:
    if len(daily_return) == 0:
        return 0.0
    volatility = float(np.std(daily_return, ddof=1)) * math.sqrt(TRADING_DAYS)
    if volatility <= 1e-12:
        return 0.0
    return float(np.mean(daily_return) * TRADING_DAYS / volatility)


def _metrics(nav: np.ndarray, daily_return: np.ndarray, position: np.ndarray) -> Dict[str, float]:
    turnover = float(np.sum(np.abs(np.diff(np.r_[0.0, position]))))
    years = len(nav) / TRADING_DAYS
    change_count = float(np.sum(np.abs(np.diff(np.r_[0.0, position])) > 1e-9))
    return {
        "annual_return": _annual_return(nav),
        "sharpe": _sharpe(daily_return),
        "max_drawdown": _max_drawdown(nav),
        "total_return": float(nav[-1] - 1.0) if len(nav) else 0.0,
        "turnover_per_year": turnover / max(years, 1e-9),
        "signal_count": change_count,
        "signals_per_year": change_count / max(years, 1e-9),
        "avg_position": float(np.mean(position)) if len(position) else 0.0,
        "current_position": float(position[-1]) if len(position) else 0.0,
    }


def _parse_daily(input_path: Path) -> Tuple[Dict[str, object], List[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    daily = ((payload.get("chart_data") or {}).get("daily") or [])
    if len(daily) < 260:
        raise ValueError("日线样本不足，无法构建全历史拐点教师。")
    dates: List[str] = []
    open_price: List[float] = []
    high: List[float] = []
    low: List[float] = []
    close: List[float] = []
    volume: List[float] = []
    for row in daily:
        dates.append(str(row[0]))
        open_price.append(_safe_float(row[1]))
        high.append(_safe_float(row[2], _safe_float(row[5], _safe_float(row[4]))))
        low.append(_safe_float(row[3], _safe_float(row[5], _safe_float(row[4]))))
        close.append(_safe_float(row[5], _safe_float(row[4])))
        volume.append(_safe_float(row[6]))
    return (
        payload,
        dates,
        np.asarray(open_price, dtype=float),
        np.asarray(high, dtype=float),
        np.asarray(low, dtype=float),
        np.asarray(close, dtype=float),
        np.asarray(volume, dtype=float),
    )


def _zigzag_pivots(close: np.ndarray, threshold: float, min_gap: int) -> List[Tuple[str, int]]:
    pivots: List[Tuple[str, int]] = []
    direction = 0
    high_value = low_value = float(close[0])
    high_index = low_index = 0
    for index in range(1, len(close)):
        price = float(close[index])
        if direction >= 0 and price > high_value:
            high_value = price
            high_index = index
        if direction <= 0 and price < low_value:
            low_value = price
            low_index = index
        if direction == 0:
            if price >= low_value * (1.0 + threshold) and index - low_index >= min_gap:
                pivots.append(("buy", low_index))
                direction = 1
                high_value = price
                high_index = index
            elif price <= high_value * (1.0 - threshold) and index - high_index >= min_gap:
                pivots.append(("sell", high_index))
                direction = -1
                low_value = price
                low_index = index
        elif direction == 1:
            if price <= high_value * (1.0 - threshold) and index - high_index >= min_gap:
                pivots.append(("sell", high_index))
                direction = -1
                low_value = price
                low_index = index
        else:
            if price >= low_value * (1.0 + threshold) and index - low_index >= min_gap:
                pivots.append(("buy", low_index))
                direction = 1
                high_value = price
                high_index = index
    filtered: List[Tuple[str, int]] = []
    state = 0
    last_index = -10_000
    for action, index in sorted(pivots, key=lambda item: item[1]):
        if index - last_index < min_gap:
            continue
        if action == "buy" and state == 0:
            filtered.append((action, index))
            state = 1
            last_index = index
        elif action == "sell" and state == 1:
            filtered.append((action, index))
            state = 0
            last_index = index
    return filtered


def _distill_position(
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    pivots: Sequence[Tuple[str, int]],
    config: PivotConfig,
) -> np.ndarray:
    n = len(close)
    base_position = np.zeros(n, dtype=float)
    state = 0
    last_buy_index: Optional[int] = None
    segment_high = 0.0
    buy_indices = {index for action, index in pivots if action == "buy"}
    sell_indices = {index for action, index in pivots if action == "sell"}
    ma20 = _rolling_mean(close, 20)
    ma60 = _rolling_mean(close, 60)
    ma120 = _rolling_mean(close, 120)
    vol20 = _rolling_mean(volume, 20)
    high120 = _rolling_max(high, 120)
    for index in range(n):
        if index in buy_indices:
            state = 1
            last_buy_index = index
            segment_high = close[index]
        if index in sell_indices:
            state = 0
            last_buy_index = None
            segment_high = 0.0
        if state == 0 or last_buy_index is None:
            base_position[index] = 0.0
            continue
        segment_high = max(segment_high, close[index])
        age = index - last_buy_index
        gain_from_entry = close[index] / max(close[last_buy_index], 1e-12) - 1.0
        drawdown_from_segment_high = close[index] / max(segment_high, 1e-12) - 1.0
        trend_ok = (
            np.isfinite(ma20[index])
            and np.isfinite(ma60[index])
            and close[index] >= ma20[index]
            and ma20[index] >= ma60[index] * 0.985
        )
        major_trend_ok = (
            trend_ok
            and np.isfinite(ma120[index])
            and ma60[index] >= ma120[index] * 0.985
        )
        breakout_ok = (
            index >= 120
            and np.isfinite(high120[index])
            and close[index] >= 0.94 * high120[index]
        )
        volume_ok = (
            np.isfinite(vol20[index])
            and vol20[index] > 0
            and volume[index] >= 0.85 * vol20[index]
        )
        if age < config.ramp_days:
            position = 0.50
        elif major_trend_ok and (gain_from_entry >= 0.18 or breakout_ok) and volume_ok:
            position = 1.00
        elif trend_ok and (gain_from_entry >= 0.08 or breakout_ok):
            position = 0.75
        else:
            position = 0.50
        if drawdown_from_segment_high <= config.stop_drawdown:
            position = min(position, 0.25)
        elif drawdown_from_segment_high <= config.stop_drawdown * 0.65:
            position = min(position, 0.50)
        base_position[index] = position
    if config.allow_trend_override:
        latest = n - 1
        latest_trend = (
            np.isfinite(ma20[latest])
            and np.isfinite(ma60[latest])
            and np.isfinite(ma120[latest])
            and close[latest] >= ma20[latest] >= ma60[latest] >= ma120[latest] * 0.98
        )
        latest_momentum = (
            latest >= 60
            and close[latest] / max(close[latest - 60], 1e-12) - 1.0 >= 0.12
        )
        latest_breakout = (
            latest >= 120
            and np.isfinite(high120[latest])
            and close[latest] >= 0.92 * high120[latest]
        )
        if latest_trend and (latest_momentum or latest_breakout):
            base_position[latest] = max(base_position[latest], 1.0)
    quantized = np.asarray([POSITION_LEVELS[np.argmin(np.abs(POSITION_LEVELS - x))] for x in base_position])
    smoothed = np.zeros(n, dtype=float)
    current = 0.0
    last_change = -10_000
    event_indices = buy_indices | sell_indices | {n - 1}
    for index in range(n):
        desired = float(quantized[index])
        forced_event = index in event_indices
        emergency_cut = desired <= 0.25 and current >= 0.75
        enough_gap = index - last_change >= config.rebalance_days
        if forced_event or emergency_cut or (enough_gap and abs(desired - current) >= 0.25):
            current = desired
            last_change = index
        smoothed[index] = current
    return smoothed


def _backtest(close: np.ndarray, position: np.ndarray, cost_rate: float) -> Tuple[np.ndarray, np.ndarray]:
    close_return = np.r_[0.0, close[1:] / np.maximum(close[:-1], 1e-12) - 1.0]
    turnover = np.r_[0.0, np.abs(np.diff(position))]
    daily_return = np.r_[0.0, position[:-1] * close_return[1:]] - turnover * cost_rate
    nav = np.cumprod(np.maximum(0.01, 1.0 + daily_return))
    return nav, daily_return


def _factor_snapshot(index: int, close: np.ndarray, high: np.ndarray, volume: np.ndarray) -> Dict[str, float]:
    ma20 = _rolling_mean(close, 20)
    ma60 = _rolling_mean(close, 60)
    ma120 = _rolling_mean(close, 120)
    vol20 = _rolling_mean(volume, 20)
    high120 = _rolling_max(high, 120)
    ret20 = close[index] / max(close[max(0, index - 20)], 1e-12) - 1.0 if index >= 20 else 0.0
    ret60 = close[index] / max(close[max(0, index - 60)], 1e-12) - 1.0 if index >= 60 else 0.0
    drawdown60 = close[index] / max(np.max(close[max(0, index - 59) : index + 1]), 1e-12) - 1.0
    return {
        "ma20": _safe_float(ma20[index], float("nan")),
        "ma60": _safe_float(ma60[index], float("nan")),
        "ma120": _safe_float(ma120[index], float("nan")),
        "ret20": float(ret20),
        "ret60": float(ret60),
        "drawdown60": float(drawdown60),
        "volume_ratio20": float(volume[index] / max(vol20[index], 1e-12)) if np.isfinite(vol20[index]) else 1.0,
        "distance_high120": float(close[index] / max(high120[index], 1e-12) - 1.0) if np.isfinite(high120[index]) else 0.0,
    }


def _record_reason(action: str, factors: Dict[str, float], current_position: float) -> str:
    if action == "buy":
        return (
            "趋势拐点确认：前期回撤结束后价格重新抬升；"
            f"20日收益{factors['ret20']:.1%}、60日收益{factors['ret60']:.1%}，"
            f"距离120日高点{factors['distance_high120']:.1%}，五档仓位升至{current_position:.0%}。"
        )
    return (
        "趋势衰竭/回撤确认：阶段高点后进入下跌确认区，"
        f"60日回撤{factors['drawdown60']:.1%}，五档仓位降至{current_position:.0%}。"
    )


def _build_records(
    dates: List[str],
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    position: np.ndarray,
    pivots: Sequence[Tuple[str, int]],
) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    for action, index in pivots:
        factors = _factor_snapshot(index, close, high, volume)
        records.append(
            {
                "date": dates[index],
                "action": "买入/加仓" if action == "buy" else "卖出/减仓",
                "price": round(float(close[index]), 4),
                "position_after": round(float(position[index]), 4),
                "technical_factors": {key: round(float(value), 6) for key, value in factors.items()},
                "logic": _record_reason(action, factors, float(position[index])),
            }
        )
    return records


def _current_advice(
    dates: List[str],
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    position: np.ndarray,
) -> Dict[str, object]:
    index = len(close) - 1
    factors = _factor_snapshot(index, close, high, volume)
    target = float(position[index])
    if target >= 0.875:
        action = "强势持有/满仓"
        logic = "主升趋势仍在，价格位于中长期均线上方且中期收益为正，模型要求继续参与上涨段。"
    elif target >= 0.625:
        action = "持有/偏高仓"
        logic = "趋势仍偏强但量价或突破确认不足，保留较高仓位并等待再确认。"
    elif target >= 0.375:
        action = "观察/中性仓"
        logic = "趋势边际转弱，保留部分仓位但不追高。"
    elif target >= 0.125:
        action = "低仓防守"
        logic = "主升信号未完全破坏但已有回撤压力，仅保留观察仓。"
    else:
        action = "空仓/等待"
        logic = "趋势段结束或防守信号占优，等待下一次拐点确认。"
    fail_conditions = [
        "收盘价连续跌破20日均线且20日均线走平/下弯",
        "从60日高点回撤超过12%-16%且无法快速收复",
        "放量下跌后反弹量能不足，60日收益转负",
    ]
    return {
        "date": dates[index],
        "close": round(float(close[index]), 4),
        "target_position": target,
        "action": action,
        "logic": logic,
        "technical_factors": {key: round(float(value), 6) for key, value in factors.items()},
        "failure_conditions": fail_conditions,
    }


def _evaluate_config(
    dates: List[str],
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    config: PivotConfig,
    cost_rate: float,
) -> Optional[StrategyResult]:
    pivots = _zigzag_pivots(close, config.threshold, config.min_gap)
    if len(pivots) < 4:
        return None
    position = _distill_position(close, high, volume, pivots, config)
    nav, daily_return = _backtest(close, position, cost_rate)
    price_nav = close / max(close[0], 1e-12)
    metrics = _metrics(nav, daily_return, position)
    buy_indices = [index for action, index in pivots if action == "buy"]
    sell_indices = [index for action, index in pivots if action == "sell"]
    records = _build_records(dates, close, high, volume, position, pivots)
    advice = _current_advice(dates, close, high, volume, position)
    return StrategyResult(
        config=config,
        dates=dates,
        price_nav=price_nav,
        strategy_nav=nav,
        position=position,
        buy_indices=buy_indices,
        sell_indices=sell_indices,
        metrics=metrics,
        learning_records=records,
        current_advice=advice,
    )


def _score_result(result: StrategyResult) -> float:
    m = result.metrics
    signal_penalty = max(0.0, m["signals_per_year"] - 3.0) * 0.25
    drawdown_penalty = max(0.0, abs(m["max_drawdown"]) - 0.35) * 1.20
    current_bonus = 0.20 if m["current_position"] >= 0.75 else -0.15
    return (
        1.4 * m["sharpe"]
        + 1.8 * min(m["annual_return"], 0.60)
        + 0.4 * min(m["total_return"], 20.0) / 20.0
        - signal_penalty
        - drawdown_penalty
        + current_bonus
    )


def fit_teacher(
    dates: List[str],
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    cost_rate: float = DEFAULT_COST_RATE,
) -> StrategyResult:
    configs: List[PivotConfig] = []
    for threshold in (0.18, 0.20, 0.22, 0.26, 0.30):
        for min_gap in (20, 30, 40, 60, 80, 100):
            for ramp_days in (10, 15):
                for rebalance_days in (60,):
                    for stop_drawdown in (-0.12, -0.16, -0.20):
                        configs.append(
                            PivotConfig(
                                threshold=threshold,
                                min_gap=min_gap,
                                ramp_days=ramp_days,
                                rebalance_days=rebalance_days,
                                stop_drawdown=stop_drawdown,
                                allow_trend_override=True,
                            )
                        )
    results = [
        result
        for config in configs
        for result in [_evaluate_config(dates, close, high, volume, config, cost_rate)]
        if result is not None
    ]
    if not results:
        raise RuntimeError("没有可用的拐点教师候选。")
    feasible = [
        result
        for result in results
        if result.metrics["signal_count"] >= 4
        and result.metrics["signals_per_year"] <= 5.0
        and result.metrics["annual_return"] > 0.12
        and result.metrics["sharpe"] > 0.60
    ]
    pool = feasible or results
    return max(pool, key=_score_result)


def _write_chart(
    output_path: Path,
    result: StrategyResult,
    code: str,
    name: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font_candidates = ["KaiTi", "SimKai", "Microsoft YaHei", "SimHei"]
    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in font_candidates:
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate, "Arial"]
            break
    plt.rcParams["axes.unicode_minus"] = False

    x = np.arange(len(result.dates))
    fig, ax = plt.subplots(figsize=(14.2, 7.2), dpi=180)
    ax.plot(x, result.strategy_nav, color="#08796f", lw=2.0, label="LLM拐点学习五档策略净值")
    ax.plot(x, result.price_nav, color="#304058", lw=1.4, label=f"{name or code} 原股价净值")
    if result.buy_indices:
        ax.scatter(
            result.buy_indices,
            result.price_nav[result.buy_indices],
            marker="^",
            s=52,
            color="#15934f",
            edgecolor="white",
            linewidth=0.5,
            zorder=4,
            label="买入/加仓",
        )
    if result.sell_indices:
        ax.scatter(
            result.sell_indices,
            result.price_nav[result.sell_indices],
            marker="v",
            s=52,
            color="#d52828",
            edgecolor="white",
            linewidth=0.5,
            zorder=4,
            label="卖出/减仓",
        )
    tick_count = 10
    tick_indices = np.linspace(0, len(result.dates) - 1, tick_count, dtype=int)
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([result.dates[index][:4] for index in tick_indices], rotation=0)
    ax.grid(True, color="#e7edf5", lw=0.7)
    ax.set_axisbelow(True)
    ax.set_ylabel("净值")
    title = f"{name or code} LLM技术学习五档策略 vs 原股价净值"
    ax.set_title(title, loc="left", fontsize=16, fontweight="bold", pad=12)
    subtitle = (
        f"全历史拐点教师；阈值{result.config.threshold:.0%} / 最小间隔{result.config.min_gap}日；"
        f"策略年化{result.metrics['annual_return']:.1%}，Sharpe {result.metrics['sharpe']:.2f}，"
        f"最大回撤{result.metrics['max_drawdown']:.1%}，信号{result.metrics['signals_per_year']:.1f}次/年"
    )
    ax.text(0.0, 1.015, subtitle, transform=ax.transAxes, fontsize=10.5, color="#44546a")
    legend = ax.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#cfd8e3", fontsize=10)
    legend.get_frame().set_alpha(0.92)
    last_x = len(result.dates) - 1
    ax.annotate(
        f"策略 {result.strategy_nav[-1]:.2f}x",
        xy=(last_x, result.strategy_nav[-1]),
        xytext=(-92, 18),
        textcoords="offset points",
        arrowprops={"arrowstyle": "-", "color": "#08796f", "lw": 0.8},
        bbox={"boxstyle": "round,pad=0.32", "fc": "white", "ec": "#08796f", "lw": 0.8},
        color="#08796f",
        fontsize=9.5,
    )
    ax.annotate(
        f"原股价 {result.price_nav[-1]:.2f}x",
        xy=(last_x, result.price_nav[-1]),
        xytext=(-94, -30),
        textcoords="offset points",
        arrowprops={"arrowstyle": "-", "color": "#304058", "lw": 0.8},
        bbox={"boxstyle": "round,pad=0.32", "fc": "white", "ec": "#304058", "lw": 0.8},
        color="#304058",
        fontsize=9.5,
    )
    footer = (
        f"回测区间：{result.dates[0]} 至 {result.dates[-1]}；交易成本：单边{DEFAULT_COST_RATE:.1%}；"
        "说明：该图为用户指定的全历史学习研究模式，买卖点教师用于记忆蒸馏，不等同严格样本外生产验证。"
    )
    fig.text(0.06, 0.035, footer, fontsize=9.5, color="#58677c")
    fig.tight_layout(rect=(0.0, 0.055, 1.0, 0.96))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)


def _write_records_txt(output_path: Path, result: StrategyResult, code: str, name: str) -> None:
    lines: List[str] = []
    lines.append(f"{name or code} LLM技术学习五档策略记录")
    lines.append("")
    lines.append("一、模型定位")
    lines.append("全历史稀疏拐点教师 -> 六类技术因子状态解释 -> 五档仓位蒸馏 -> 当前配置建议。")
    lines.append("该模式专门服务于“抓主升、躲主跌、交易不要太频繁”的研究诉求，不按严格样本外生产验证表述。")
    lines.append("")
    lines.append("二、核心参数与效果")
    lines.append(
        f"拐点阈值：{result.config.threshold:.0%}；最小信号间隔：{result.config.min_gap}个交易日；"
        f"仓位档位：0/25/50/75/100；单边成本：{DEFAULT_COST_RATE:.1%}。"
    )
    lines.append(
        f"策略年化：{result.metrics['annual_return']:.2%}；Sharpe：{result.metrics['sharpe']:.2f}；"
        f"最大回撤：{result.metrics['max_drawdown']:.2%}；累计收益：{result.metrics['total_return']:.2%}；"
        f"信号频率：{result.metrics['signals_per_year']:.2f}次/年。"
    )
    lines.append(
        f"原股价累计净值：{result.price_nav[-1]:.2f}x；策略累计净值：{result.strategy_nav[-1]:.2f}x。"
    )
    lines.append("")
    lines.append("三、当前配置建议")
    advice = result.current_advice
    lines.append(
        f"{advice['date']} 收盘 {advice['close']}：{advice['action']}，目标仓位 {advice['target_position']:.0%}。"
    )
    lines.append(str(advice["logic"]))
    lines.append("失效条件：" + "；".join(str(item) for item in advice["failure_conditions"]))
    lines.append("")
    lines.append("四、历史买卖点学习记录")
    for record in result.learning_records:
        lines.append(
            f"{record['date']} {record['action']}，价格 {record['price']}，"
            f"仓位 {float(record['position_after']):.0%}：{record['logic']}"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    input_path: Path = DEFAULT_INPUT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    code: str = "600737.SH",
    name: str = "中粮糖业",
) -> Dict[str, object]:
    payload, dates, open_price, high, low, close, volume = _parse_daily(input_path)
    del payload, open_price, low
    result = fit_teacher(dates, close, high, volume, DEFAULT_COST_RATE)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{name}LLM技术学习全历史拐点增强版"
    chart_path = output_dir / f"{stem}.png"
    json_path = output_dir / f"{stem}.json"
    txt_path = output_dir / f"{stem}.txt"
    _write_chart(chart_path, result, code, name)
    _write_records_txt(txt_path, result, code, name)
    payload_out: Dict[str, object] = {
        "version": "single-stock-turning-point-teacher/1.0",
        "code": code,
        "name": name,
        "mode": "full_history_research_sparse_turning_point_teacher",
        "disclaimer": "全历史学习研究模式，不等同严格样本外生产验证。",
        "config": {
            "threshold": result.config.threshold,
            "min_gap": result.config.min_gap,
            "ramp_days": result.config.ramp_days,
            "rebalance_days": result.config.rebalance_days,
            "stop_drawdown": result.config.stop_drawdown,
            "position_levels": POSITION_LEVELS.tolist(),
            "cost_rate": DEFAULT_COST_RATE,
        },
        "metrics": result.metrics,
        "price_nav_final": float(result.price_nav[-1]),
        "strategy_nav_final": float(result.strategy_nav[-1]),
        "current_advice": result.current_advice,
        "learning_records": result.learning_records,
        "artifacts": {
            "chart": str(chart_path),
            "json": str(json_path),
            "txt": str(txt_path),
        },
    }
    json_path.write_text(json.dumps(payload_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload_out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run full-history sparse turning-point K-line research.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="learned_kline_result.json path")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="output folder")
    parser.add_argument("--code", default="600737.SH")
    parser.add_argument("--name", default="中粮糖业")
    args = parser.parse_args(argv)
    result = run(args.input, args.output_dir, args.code, args.name)
    print(json.dumps({
        "chart": result["artifacts"]["chart"],
        "json": result["artifacts"]["json"],
        "txt": result["artifacts"]["txt"],
        "metrics": result["metrics"],
        "current_advice": result["current_advice"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
