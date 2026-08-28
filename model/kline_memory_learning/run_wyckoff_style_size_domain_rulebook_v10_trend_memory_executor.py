"""V10 trend-memory executor for style x size Wyckoff domain rules.

V10 keeps the V9 domain-shared memory/rulebook framework and changes only the
execution layer:

- NAV and signal judgment start after 21 trading days from listing.
- Domain Wyckoff memory decides the quality of current patterns.
- A causal trend state keeps high exposure during markup/uptrend periods.
- Distribution, breakdown, and trailing-stop gates cut exposure after trend
  damage, with hysteresis to avoid excessive trading.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning import run_wyckoff_industry_style_memory_batch as base  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_batch as v1  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v9_compact_patterns as v9  # noqa: E402
from model.kline_memory_learning.run_wyckoff_domain_evolver_optimization import (  # noqa: E402
    EVENT_PATH,
    attach_memory,
    build_memory,
    prepare_events,
    weighted_memory_score,
)
from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    StockSeries,
    _annual_stats,
    _load_stock,
    _metrics,
    _pct,
    _position_label,
    _safe_name,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import (  # noqa: E402
    DOMAIN_COL,
    _plot_style_size_chart,
)


OUTPUT_SUBDIR = "风格市值12域统一规则库Wyckoff_V10趋势记忆执行器"
DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
START_AFTER_TRADING_DAYS = 21


def _slice_after_ipo_month(series: StockSeries, offset: int = START_AFTER_TRADING_DAYS) -> StockSeries:
    start = min(max(int(offset), 0), max(len(series.dates) - 2, 0))
    return StockSeries(
        code=series.code,
        name=series.name,
        dates=list(series.dates[start:]),
        open=np.asarray(series.open[start:], dtype=float),
        high=np.asarray(series.high[start:], dtype=float),
        low=np.asarray(series.low[start:], dtype=float),
        close=np.asarray(series.close[start:], dtype=float),
        volume=np.asarray(series.volume[start:], dtype=float),
        amount=np.asarray(series.amount[start:], dtype=float),
    )


def _roll(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    return series.rolling(window, min_periods=min_periods or max(3, window // 3)).mean()


def _ret(close: pd.Series, window: int) -> pd.Series:
    return close.pct_change(window, fill_method=None).replace([np.inf, -np.inf], np.nan)


def _five_level(value: float) -> float:
    levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)
    return float(levels[np.argmin(np.abs(levels - float(value)))])


def _trend_memory_replay(
    series: StockSeries,
    daily_raw: np.ndarray,
    domain_positions: np.ndarray,
    cost_rate: float,
) -> Dict[str, Any]:
    close = pd.Series(series.close.astype(float))
    n = len(close)
    nav = np.ones(n, dtype=float)
    positions = np.zeros(n, dtype=float)
    scores = np.full(n, 50.0, dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    if n < 3:
        return {
            "strategy_nav": nav,
            "positions": positions,
            "scores": scores,
            "buy_indices": buy_indices,
            "sell_indices": sell_indices,
            "current_position": 0.0,
            "current_score": 50.0,
            "profile": {},
        }

    ma10 = _roll(close, 10)
    ma20 = _roll(close, 20)
    ma60 = _roll(close, 60)
    ma120 = _roll(close, 120)
    ma250 = _roll(close, 250)
    ret5 = _ret(close, 5)
    ret10 = _ret(close, 10)
    ret20 = _ret(close, 20)
    ret60 = _ret(close, 60)
    high20 = close.rolling(20, min_periods=5).max()
    high60 = close.rolling(60, min_periods=15).max()
    high120 = close.rolling(120, min_periods=30).max()
    low20 = close.rolling(20, min_periods=5).min()
    low60 = close.rolling(60, min_periods=15).min()
    amount = pd.Series(series.amount.astype(float))
    amount20 = amount.rolling(20, min_periods=5).mean().replace(0.0, np.nan)
    amount_ratio = (amount / amount20).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    memory = np.tanh(np.asarray(daily_raw, dtype=float) * 1.50)
    domain_positions = np.asarray(domain_positions, dtype=float).clip(0.0, 1.0)

    current = 0.0
    last_change = -10_000
    entry_peak = 0.0
    cooldown = 12
    risk_confirm = 0
    recovery_confirm = 0

    for idx in range(0, n - 1):
        price = float(close.iloc[idx])
        if not math.isfinite(price) or price <= 0:
            nav[idx + 1] = nav[idx]
            continue

        m10 = float(ma10.iloc[idx]) if pd.notna(ma10.iloc[idx]) else np.nan
        m20 = float(ma20.iloc[idx]) if pd.notna(ma20.iloc[idx]) else np.nan
        m60 = float(ma60.iloc[idx]) if pd.notna(ma60.iloc[idx]) else np.nan
        m120 = float(ma120.iloc[idx]) if pd.notna(ma120.iloc[idx]) else np.nan
        m250 = float(ma250.iloc[idx]) if pd.notna(ma250.iloc[idx]) else np.nan
        r5 = float(ret5.iloc[idx]) if pd.notna(ret5.iloc[idx]) else 0.0
        r10 = float(ret10.iloc[idx]) if pd.notna(ret10.iloc[idx]) else 0.0
        r20 = float(ret20.iloc[idx]) if pd.notna(ret20.iloc[idx]) else 0.0
        r60 = float(ret60.iloc[idx]) if pd.notna(ret60.iloc[idx]) else 0.0
        h20 = float(high20.iloc[idx]) if pd.notna(high20.iloc[idx]) else price
        h60 = float(high60.iloc[idx]) if pd.notna(high60.iloc[idx]) else price
        h120 = float(high120.iloc[idx]) if pd.notna(high120.iloc[idx]) else price
        l20 = float(low20.iloc[idx]) if pd.notna(low20.iloc[idx]) else price
        l60 = float(low60.iloc[idx]) if pd.notna(low60.iloc[idx]) else price
        vol_boost = float(amount_ratio.iloc[idx]) >= 1.15
        mem = float(memory[idx]) if idx < len(memory) and math.isfinite(float(memory[idx])) else 0.0
        domain_pos = float(domain_positions[idx]) if idx < len(domain_positions) else 0.0

        above20 = math.isfinite(m20) and price >= m20
        above60 = math.isfinite(m60) and price >= m60
        above120 = math.isfinite(m120) and price >= m120
        above250 = math.isfinite(m250) and price >= m250
        ma_bull = math.isfinite(m20) and math.isfinite(m60) and m20 >= m60
        ma_big_bull = ma_bull and math.isfinite(m120) and m60 >= m120
        ma_bear = math.isfinite(m20) and math.isfinite(m60) and m20 < m60

        early_breakout = price >= h20 * 0.995 and r10 > 0.035
        channel_breakout = price >= h60 * 0.995 and (r20 > 0.055 or vol_boost)
        major_breakout = price >= h120 * 0.995 and r20 > 0.035
        markup = (above60 and ma_bull and r20 > -0.025) or (above120 and r60 > 0.02)
        strong_markup = (above60 and ma_big_bull and r20 > 0.02) or channel_breakout or major_breakout
        acceleration = (r20 > 0.10 and above20) or (r5 > 0.055 and price >= h20 * 0.99)
        constructive_pullback = markup and price >= m60 * 0.965 and r10 < 0.015 and mem > -0.35
        early_recovery = above20 and r10 > 0.025 and mem > -0.45

        if strong_markup or acceleration:
            trend_target = 1.0
        elif markup or channel_breakout or constructive_pullback:
            trend_target = 0.75
        elif above120 or early_recovery or (above60 and mem > -0.20):
            trend_target = 0.50
        elif above20 or mem > 0.32:
            trend_target = 0.25
        else:
            trend_target = 0.0

        if idx < 45:
            if above20 and r10 > 0.0:
                trend_target = max(trend_target, 0.50)
            if early_breakout or r20 > 0.06:
                trend_target = max(trend_target, 0.75)

        target = max(domain_pos, trend_target)
        if mem > 0.62 and not ma_bear and r20 > -0.045:
            target = max(target, 0.75)
        if mem > 0.78 and (above20 or above60):
            target = max(target, 1.0)

        if current > 0:
            entry_peak = max(entry_peak, price)
        else:
            entry_peak = price
        trail = price / max(entry_peak, 1e-9) - 1.0

        first_damage = (price < m20 if math.isfinite(m20) else False) and (r10 < -0.035 or mem < -0.48)
        trend_break = (price < m60 if math.isfinite(m60) else False) and (r20 < -0.07 or ma_bear)
        major_break = (price < m120 if math.isfinite(m120) else False) and (r20 < -0.10 or mem < -0.60)
        distribution = mem < -0.68 and (not above20) and (r20 < -0.025 or price < l20 * 1.04)
        range_loss = price < l20 * 1.005 and r20 < -0.04
        deep_range_loss = price < l60 * 1.015 and r20 < -0.08
        trailing_damage = (trail < -0.13 and not above20) or (trail < -0.20 and not above60)

        risk_signal = first_damage or trend_break or major_break or distribution or trailing_damage or range_loss
        risk_confirm = risk_confirm + 1 if risk_signal else max(0, risk_confirm - 1)
        recovery_signal = (above20 and r5 > 0.015) or channel_breakout or mem > 0.55
        recovery_confirm = recovery_confirm + 1 if recovery_signal else max(0, recovery_confirm - 1)

        if distribution or major_break or deep_range_loss:
            target = min(target, 0.0)
        elif trend_break or (trailing_damage and risk_confirm >= 1):
            target = min(target, 0.25)
        elif first_damage and risk_confirm >= 2:
            target = min(target, 0.50)
        elif current >= 0.75 and constructive_pullback and recovery_confirm >= 1:
            target = max(target, 0.75)

        if strong_markup and mem > -0.55 and not major_break:
            target = max(target, 0.75)
        if acceleration and mem > -0.65 and not trend_break:
            target = max(target, 1.0)

        target = _five_level(target)
        risk_exit = target < current and (distribution or trend_break or major_break or trailing_damage or deep_range_loss)
        strong_entry = target > current and (strong_markup or acceleration or mem > 0.68 or channel_breakout)
        can_change = idx - last_change >= cooldown or risk_exit or strong_entry
        if abs(target - current) >= 0.25 and can_change:
            if target > current:
                buy_indices.append(idx)
                if current <= 0:
                    entry_peak = price
            else:
                sell_indices.append(idx)
            current = target
            last_change = idx

        positions[idx] = current
        blended_score = 0.42 * mem + 0.42 * (2.0 * trend_target - 1.0) + 0.16 * (2.0 * domain_pos - 1.0)
        scores[idx] = float(np.clip(50.0 + 44.0 * math.tanh(blended_score), 0.0, 100.0))
        daily_return = _pct(float(close.iloc[idx + 1]), price)
        prev = float(positions[idx - 1]) if idx > 0 else 0.0
        turnover_cost = cost_rate * abs(current - prev)
        nav[idx + 1] = nav[idx] * max(0.01, 1.0 + current * daily_return - turnover_cost)

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
            "mode": "style_size_domain_rulebook_v10_trend_memory_executor",
            "position_levels": [0.0, 0.25, 0.50, 0.75, 1.0],
            "start_after_trading_days": START_AFTER_TRADING_DAYS,
            "stock_specific_path_optimization": False,
            "rules": [
                "domain_memory_rulebook",
                "markup_trend_hold",
                "channel_breakout_full_position",
                "distribution_breakdown_risk_gate",
                "trailing_stop_after_entry_peak",
                "hysteresis_cooldown",
            ],
        },
    }


def _run_one_stock_v10(
    original_series: StockSeries,
    scored_events: pd.DataFrame,
    rulebook: pd.DataFrame,
    thresholds: pd.DataFrame,
    cost_rate: float,
) -> Dict[str, Any]:
    series = _slice_after_ipo_month(original_series)
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    target_events = v1._stock_events(scored_events, series)
    if target_events.empty:
        raise RuntimeError(f"{series.code} 没有可用的风格市值域 Wyckoff 事件。")
    raw_score = pd.to_numeric(target_events["_domain_raw_score"], errors="coerce").fillna(0.0)
    daily_raw, active_event_ids = base._daily_domain_memory_score(series, target_events, raw_score)
    latest_domain = str(target_events.sort_values("index").iloc[-1].get(DOMAIN_COL, "未分域"))
    domain_thresholds = v1._threshold_for_domain(thresholds, latest_domain)
    memory_replay = base._threshold_policy_replay(series, daily_raw, domain_thresholds, cooldown=18, cost_rate=cost_rate)
    replay = _trend_memory_replay(series, daily_raw, np.asarray(memory_replay["positions"], dtype=float), cost_rate)
    metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
    annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
    latest_idx = len(series.dates) - 1
    domain_slice = scored_events.loc[scored_events[DOMAIN_COL].astype(str).eq(latest_domain)]
    active_rows = base._active_rows_for_date(target_events, raw_score, active_event_ids, latest_idx)
    current_position = float(replay["current_position"])
    result: Dict[str, Any] = {
        "code": series.code,
        "name": series.name,
        "as_of": series.dates[-1],
        "start_date": series.dates[0],
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
        "domain_name": "风格×市值12域",
        "domain_value": latest_domain,
        "domain_pool_events": int(len(domain_slice)),
        "domain_pool_stocks": int(domain_slice["ts_code"].nunique()),
        "stock_event_count": int(len(target_events)),
        "current_score": float(replay["current_score"]),
        "current_position": current_position,
        "current_position_label": _position_label(current_position),
        "current_active_signals": active_rows,
        "matched_domain_rules": v1._matched_rules_for_active(rulebook, latest_domain, active_rows),
        "top_domain_rules": v1._top_rules(rulebook, latest_domain, limit=18),
        "evolver_profile": replay["profile"],
        "model_boundary": "模型二：Wyckoff形态记忆学习；风格×市值12域统一规则库；域内全股票共用规则；执行器只用因果趋势/破位/跟踪止损；净值从上市满21个交易日后开始；不做单股路径拟合；不使用六类技术因子。",
    }
    active_text = "、".join(
        f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
        for row in active_rows[:4]
    ) if active_rows else "近期无强形态触发，仓位由域记忆、趋势水温和破位风险门共同决定"
    result["latest_signal"] = (
        f"起始日={result['start_date']}；当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"所在域={latest_domain}，域内{result['domain_pool_stocks']}只股票、{result['domain_pool_events']}条成熟事件共同学习规则库；"
        f"当前技术信号：{active_text}。"
    )
    return result


def _write_text_record(result: Dict[str, Any], path: Path) -> None:
    metrics = result["metrics"]
    lines = [
        f"{result['code']} {result['name']}：V10 风格×市值域 Wyckoff 记忆 + 趋势记忆执行器",
        "",
        f"净值起始日：{result['start_date']}（上市满{START_AFTER_TRADING_DAYS}个交易日后）",
        f"当前结论：{result['current_position_label']}，分数{result['current_score']:.1f}",
        f"所在域：{result['domain_value']}；域内股票数：{result['domain_pool_stocks']}；域内事件数：{result['domain_pool_events']}",
        f"策略Sharpe：{metrics['strategy_sharpe']:.3f}；原股价Sharpe：{metrics['price_sharpe']:.3f}",
        f"策略年化：{metrics['strategy_annual_return']:.2%}；原股价年化：{metrics['price_annual_return']:.2%}",
        f"策略最大回撤：{metrics['strategy_max_drawdown']:.2%}；原股价最大回撤：{metrics['price_max_drawdown']:.2%}",
        f"年均调仓次数：{metrics['turnover_times_per_year']:.2f}",
        "",
        "执行逻辑：域内记忆规则负责形态质量，趋势水温负责主升段持有，派发/破位/入场后高点回撤负责降仓，12天冷却控制频率。",
        "",
        "当前命中规则：",
    ]
    if result["matched_domain_rules"]:
        for rule in result["matched_domain_rules"]:
            lines.append(
                f"- {rule['date']} {rule['frequency']} {rule['rule_name']}：{rule['stage']} / {rule['confirmation']}；"
                f"{rule['direction']}；{rule['position']}；样本{rule['sample_count']}；胜率{rule['hit_rate']:.1%}；edge={rule['edge_score']:.3f}"
            )
    else:
        lines.append("- 当前无未衰减的强形态命中，主要由趋势水温和风险门给出仓位。")
    lines.extend(["", "所在域最强规则："])
    for rule in result["top_domain_rules"][:18]:
        lines.append(
            f"- {rule['frequency']} {rule['rule_name']}：{rule['stage']} / {rule['confirmation']}；"
            f"{rule['direction']}；{rule['position']}；样本{rule['sample_count']}；股票{rule['stock_count']}只；"
            f"胜率{rule['hit_rate']:.1%}；20日均值{rule['avg_forward_return']:.2%}；10%尾部{rule['tail10_forward_return']:.2%}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_summary(results: Sequence[Dict[str, Any]], output_dir: Path) -> None:
    rows = []
    for result in results:
        metrics = result["metrics"]
        rows.append(
            {
                "域": result["domain_value"],
                "代码": result["code"],
                "名称": result["name"],
                "净值起始日": result["start_date"],
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
                "图片路径": result["chart_path"],
                "学习记录": result["txt_path"],
            }
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(output_dir / "五股V10趋势记忆执行器结论.csv", index=False, encoding="utf-8-sig")
    (output_dir / "五股V10趋势记忆执行器结论.json").write_text(
        json.dumps({"results": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["V10 风格×市值12域 Wyckoff 记忆 + 趋势记忆执行器", ""]
    for row in rows:
        lines.append(
            f"{row['域']}｜{row['代码']} {row['名称']}｜{row['建议仓位']}｜"
            f"策略Sharpe {row['策略Sharpe']} / 原股价Sharpe {row['原股价Sharpe']}｜"
            f"策略年化{row['策略年化']} / 原股价年化{row['原股价年化']}｜"
            f"最大回撤{row['策略最大回撤']} / {row['原股价最大回撤']}｜年均调仓{row['年均调仓']}"
        )
    lines.extend(["", "说明：同一域内股票共用同一套规则库；净值和买卖点从上市满21个交易日后开始；未做单股路径参数拟合。"])
    (output_dir / "五股V10趋势记忆执行器结论.txt").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V10 style-size Wyckoff trend-memory executor.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=EVENT_PATH)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / OUTPUT_SUBDIR)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_OUTPUT_DIR / "expanded_pattern_events_v9_compact.pkl")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/6] 读取原始Wyckoff事件与V9扩展图形事件", flush=True)
    raw_events = pd.read_pickle(args.events)
    strict_wyckoff_raw = raw_events.loc[raw_events[DOMAIN_COL].astype(str).isin(v9.v8.STYLE_SIZE_DOMAINS)].copy()
    expanded_raw = v9.build_expanded_events_compact(args.db, raw_events, args.cache_path, bool(args.refresh_cache))
    print(
        f"[events] wyckoff={len(strict_wyckoff_raw):,}, expanded={len(expanded_raw):,}, "
        f"expanded_rules={expanded_raw['rule_id'].nunique():,}",
        flush=True,
    )

    print("[2/6] 合并事件并计算记忆上下文", flush=True)
    combined_path = output_dir / "V10_wyckoff_plus_expanded_events.pkl"
    pd.concat([strict_wyckoff_raw, expanded_raw], ignore_index=True, sort=False).to_pickle(combined_path)
    scored_seed = prepare_events(combined_path)
    memory = build_memory(scored_seed, DOMAIN_COL)
    scored_events = attach_memory(scored_seed, memory, DOMAIN_COL)
    scored_events["_domain_raw_score"] = weighted_memory_score(scored_events, v1.DOMAIN_PROFILE)
    print(f"[scored] events={len(scored_events):,}, rules={scored_events['rule_id'].nunique():,}", flush=True)

    print("[3/6] 学习12域统一规则库与仓位阈值", flush=True)
    rulebook = v1.build_domain_rulebook(scored_events)
    thresholds = v1.learn_domain_thresholds(scored_events)
    rulebook.to_csv(output_dir / "风格市值12域V10扩展Wyckoff规则库.csv", index=False, encoding="utf-8-sig")
    thresholds.to_csv(output_dir / "风格市值12域V10统一仓位阈值.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(v9.v8.CATALOG.values()).to_csv(output_dir / "V10扩展图形规则目录.csv", index=False, encoding="utf-8-sig")
    print(f"[rulebook] rows={len(rulebook):,}, domains={rulebook['domain_value'].nunique():,}", flush=True)

    print("[4/6] 应用V10趋势记忆执行器到五只股票", flush=True)
    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for code in args.codes:
            original = _load_stock(conn, str(code), as_of)
            result = _run_one_stock_v10(original, scored_events, rulebook, thresholds, float(args.cost_rate))
            safe = _safe_name(f"V10趋势记忆执行器_{result['domain_value']}_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            json_path = output_dir / f"{safe}.json"
            txt_path = output_dir / f"{safe}_学习记录.txt"
            result["chart_path"] = str(chart_path)
            result["json_path"] = str(json_path)
            result["txt_path"] = str(txt_path)
            _plot_style_size_chart(result, chart_path)
            json_path.write_text(json.dumps(v1._json_light(result), ensure_ascii=False, indent=2), encoding="utf-8")
            _write_text_record(result, txt_path)
            results.append(result)
            print(
                f"[stock] {result['domain_value']} {result['code']} {result['name']} "
                f"start={result['start_date']} {result['current_position_label']} "
                f"Sharpe {result['metrics']['strategy_sharpe']:.2f}/{result['metrics']['price_sharpe']:.2f} "
                f"annual {result['metrics']['strategy_annual_return']:.2%}/{result['metrics']['price_annual_return']:.2%} "
                f"mdd {result['metrics']['strategy_max_drawdown']:.2%}/{result['metrics']['price_max_drawdown']:.2%}",
                flush=True,
            )

    print("[5/6] 写出汇总", flush=True)
    _write_summary(results, output_dir)
    (output_dir / "V10模型说明.txt").write_text(
        "\n".join(
            [
                "V10趋势记忆执行器版",
                f"净值起始规则：上市满{START_AFTER_TRADING_DAYS}个交易日后开始归一化、判断买卖点和回测。",
                f"扩展图形基础规则数：{expanded_raw['rule_id'].nunique()}",
                f"扩展事件数：{len(expanded_raw):,}",
                f"合并后基础规则数：{scored_events['rule_id'].nunique()}",
                f"最终域规则库行数：{len(rulebook):,}",
                "框架：Analyzer扩展图形规则 -> 风格×市值12域Memory -> Rulebook/Evolver -> 趋势记忆执行器 -> 五档仓位。",
                "边界：同一域内全股票共用规则；不做单股路径拟合；不使用六类技术因子模型。",
            ]
        ),
        encoding="utf-8",
    )
    print("[6/6] 完成", flush=True)
    print(json.dumps(
        {
            "output_dir": str(output_dir),
            "expanded_base_rules": int(expanded_raw["rule_id"].nunique()),
            "combined_base_rules": int(scored_events["rule_id"].nunique()),
            "rulebook_rows": int(len(rulebook)),
            "domain_count": int(rulebook["domain_value"].nunique()),
            "results": [
                {
                    "code": item["code"],
                    "name": item["name"],
                    "domain": item["domain_value"],
                    "start_date": item["start_date"],
                    "position": item["current_position_label"],
                    "score": round(float(item["current_score"]), 1),
                    "strategy_sharpe": round(float(item["metrics"]["strategy_sharpe"]), 3),
                    "price_sharpe": round(float(item["metrics"]["price_sharpe"]), 3),
                    "strategy_annual": round(float(item["metrics"]["strategy_annual_return"]), 4),
                    "price_annual": round(float(item["metrics"]["price_annual_return"]), 4),
                    "strategy_mdd": round(float(item["metrics"]["strategy_max_drawdown"]), 4),
                    "price_mdd": round(float(item["metrics"]["price_max_drawdown"]), 4),
                    "turnover": round(float(item["metrics"]["turnover_times_per_year"]), 2),
                    "chart_path": item["chart_path"],
                    "txt_path": item["txt_path"],
                }
                for item in results
            ],
        },
        ensure_ascii=False,
        indent=2,
    ), flush=True)


if __name__ == "__main__":
    main()
