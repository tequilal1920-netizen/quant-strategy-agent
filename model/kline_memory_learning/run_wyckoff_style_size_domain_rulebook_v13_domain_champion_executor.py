"""V13 domain champion executor for Wyckoff style x size memory.

This runner keeps the V9/V11 domain-shared memory framework, then selects one
pre-declared execution profile for each style x size domain using all stocks in
that domain.  It is full-sample by request, but avoids single-stock fitting by
using only six broad profiles and one domain-level objective.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning import run_wyckoff_industry_style_memory_batch as base  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_batch as v1  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v6_markup_executor_batch as v6  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v9_compact_patterns as v9  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v10_trend_memory_executor as v10  # noqa: E402
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


OUTPUT_SUBDIR = "风格市值12域统一规则库Wyckoff_V13域内冠军执行器"
DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
START_OFFSET = 21


PROFILES: List[Dict[str, Any]] = [
    {
        "profile_id": "robust_markup_20_60_150_break40_wide",
        "name": "稳健主升-MA20/60/150-40日突破-宽止损",
        "fast": 20,
        "mid": 60,
        "slow": 150,
        "break_window": 40,
        "trail": 0.34,
        "trail2": 0.42,
        "exit_r20": 0.07,
        "major_exit": 0.10,
        "break_r20": 0.02,
        "full_r20": 0.03,
        "entry_r20": 0.07,
        "cooldown": 20,
        "memory_weight": 0.28,
    },
    {
        "profile_id": "balanced_20_40_120_break40_medium",
        "name": "均衡波段-MA20/40/120-40日突破-中止损",
        "fast": 20,
        "mid": 40,
        "slow": 120,
        "break_window": 40,
        "trail": 0.22,
        "trail2": 0.30,
        "exit_r20": 0.07,
        "major_exit": 0.10,
        "break_r20": 0.02,
        "full_r20": 0.03,
        "entry_r20": 0.07,
        "cooldown": 20,
        "memory_weight": 0.30,
    },
    {
        "profile_id": "offensive_10_40_120_break20",
        "name": "进攻突破-MA10/40/120-20日突破",
        "fast": 10,
        "mid": 40,
        "slow": 120,
        "break_window": 20,
        "trail": 0.28,
        "trail2": 0.36,
        "exit_r20": 0.08,
        "major_exit": 0.12,
        "break_r20": 0.015,
        "full_r20": 0.025,
        "entry_r20": 0.055,
        "cooldown": 16,
        "memory_weight": 0.24,
    },
    {
        "profile_id": "long_hold_20_40_120_break120",
        "name": "长趋势持有-MA20/40/120-120日突破",
        "fast": 20,
        "mid": 40,
        "slow": 120,
        "break_window": 120,
        "trail": 0.34,
        "trail2": 0.44,
        "exit_r20": 0.09,
        "major_exit": 0.13,
        "break_r20": 0.02,
        "full_r20": 0.03,
        "entry_r20": 0.07,
        "cooldown": 24,
        "memory_weight": 0.22,
    },
    {
        "profile_id": "defensive_20_60_120_break20",
        "name": "防守趋势-MA20/60/120-20日突破",
        "fast": 20,
        "mid": 60,
        "slow": 120,
        "break_window": 20,
        "trail": 0.22,
        "trail2": 0.30,
        "exit_r20": 0.06,
        "major_exit": 0.09,
        "break_r20": 0.02,
        "full_r20": 0.035,
        "entry_r20": 0.07,
        "cooldown": 20,
        "memory_weight": 0.34,
    },
    {
        "profile_id": "ultra_low_freq_20_60_150_break60",
        "name": "低频主升-MA20/60/150-60日突破",
        "fast": 20,
        "mid": 60,
        "slow": 150,
        "break_window": 60,
        "trail": 0.34,
        "trail2": 0.44,
        "exit_r20": 0.08,
        "major_exit": 0.12,
        "break_r20": 0.025,
        "full_r20": 0.035,
        "entry_r20": 0.075,
        "cooldown": 28,
        "memory_weight": 0.22,
    },
]


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _slice_after_month(series: StockSeries) -> StockSeries:
    start = min(START_OFFSET, max(0, len(series.dates) - 2))
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


def _load_stock_from_group(code: str, group: pd.DataFrame) -> StockSeries | None:
    rows = group.sort_values("trade_date")
    if len(rows) < 90:
        return None
    dates: List[str] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    vols: List[float] = []
    amounts: List[float] = []
    name = str(rows["stock_name"].dropna().iloc[-1]) if rows["stock_name"].notna().any() else code
    for row in rows.itertuples(index=False):
        raw_close = float(getattr(row, "close") or 0.0)
        qfq_close = float(getattr(row, "qfq_close") or raw_close)
        if raw_close <= 0 or qfq_close <= 0:
            continue
        factor = qfq_close / raw_close
        open_ = float(getattr(row, "open") or raw_close) * factor
        high = float(getattr(row, "high") or raw_close) * factor
        low = float(getattr(row, "low") or raw_close) * factor
        close = qfq_close
        dates.append(str(getattr(row, "trade_date")))
        opens.append(open_)
        highs.append(max(high, open_, close))
        lows.append(min(low, open_, close))
        closes.append(close)
        vols.append(float(getattr(row, "vol") or 0.0))
        amounts.append(float(getattr(row, "amount") or 0.0))
    if len(dates) < 90:
        return None
    return StockSeries(code=code, name=name, dates=dates, open=np.asarray(opens), high=np.asarray(highs), low=np.asarray(lows), close=np.asarray(closes), volume=np.asarray(vols), amount=np.asarray(amounts))


def _profile_replay(
    series: StockSeries,
    profile: Dict[str, Any],
    daily_raw: np.ndarray | None = None,
    domain_positions: np.ndarray | None = None,
    cost_rate: float = DEFAULT_COST_RATE,
) -> Dict[str, Any]:
    close = pd.Series(series.close.astype(float))
    n = len(close)
    nav = np.ones(n, dtype=float)
    positions = np.zeros(n, dtype=float)
    scores = np.full(n, 50.0, dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    if n < 3:
        return {"strategy_nav": nav, "positions": positions, "scores": scores, "buy_indices": [], "sell_indices": [], "current_position": 0.0, "current_score": 50.0, "profile": profile}

    fast = int(profile["fast"])
    mid = int(profile["mid"])
    slow = int(profile["slow"])
    break_window = int(profile["break_window"])
    ma_fast = close.rolling(fast, min_periods=max(3, fast // 3)).mean()
    ma_mid = close.rolling(mid, min_periods=max(8, mid // 3)).mean()
    ma_slow = close.rolling(slow, min_periods=max(20, slow // 3)).mean()
    high_break = close.rolling(break_window, min_periods=max(10, break_window // 3)).max()
    high20 = close.rolling(20, min_periods=6).max()
    ret5 = close.pct_change(5, fill_method=None).fillna(0.0)
    ret10 = close.pct_change(10, fill_method=None).fillna(0.0)
    ret20 = close.pct_change(20, fill_method=None).fillna(0.0)
    ret60 = close.pct_change(60, fill_method=None).fillna(0.0)
    memory = np.tanh(np.asarray(daily_raw if daily_raw is not None else np.zeros(n), dtype=float) * 1.25)
    domain_positions = np.asarray(domain_positions if domain_positions is not None else np.zeros(n), dtype=float).clip(0.0, 1.0)

    current = 0.0
    last_change = -10_000
    peak = float(close.iloc[0])
    risk_days = 0
    for idx in range(n - 1):
        price = float(close.iloc[idx])
        if not math.isfinite(price) or price <= 0:
            nav[idx + 1] = nav[idx]
            continue
        mf = ma_fast.iloc[idx]
        mm = ma_mid.iloc[idx]
        ms = ma_slow.iloc[idx]
        hb = high_break.iloc[idx]
        r5 = float(ret5.iloc[idx])
        r10 = float(ret10.iloc[idx])
        r20 = float(ret20.iloc[idx])
        r60 = float(ret60.iloc[idx])
        mem = float(memory[idx]) if idx < len(memory) and math.isfinite(float(memory[idx])) else 0.0
        above_fast = pd.notna(mf) and price > float(mf)
        above_mid = pd.notna(mm) and price > float(mm)
        above_slow = pd.notna(ms) and price > float(ms)
        ma_bull = above_mid and pd.notna(mf) and pd.notna(mm) and float(mf) > float(mm) and r20 > -0.04
        slow_bull = above_slow and r60 > 0.0
        breakout = pd.notna(hb) and price >= float(hb) * 0.995 and r20 > float(profile["break_r20"])
        impulse = (r20 > 0.09 and above_fast) or (r5 > 0.055 and pd.notna(high20.iloc[idx]) and price >= float(high20.iloc[idx]) * 0.985)

        target = 0.0
        if breakout or impulse or ((ma_bull or slow_bull) and r20 > float(profile["full_r20"])):
            target = 1.0
        elif ma_bull or slow_bull:
            target = 0.75
        elif above_slow or (above_mid and r20 > -0.04):
            target = 0.50
        elif above_fast and r20 > -0.02:
            target = 0.25

        weight = float(profile.get("memory_weight", 0.25))
        if mem > 0.58 and target >= 0.25:
            target = max(target, 0.75 if weight >= 0.22 else target)
        if mem > 0.78 and (above_fast or above_mid):
            target = max(target, 1.0)
        if domain_positions[idx] >= 0.75 and target >= 0.25 and mem > -0.55:
            target = max(target, 0.75)
        if idx < 60 and above_fast and r10 > 0.0:
            target = max(target, 0.50)

        if current > 0:
            peak = max(peak, price)
        else:
            peak = price
        trail = price / max(peak, 1e-9) - 1.0
        damage = (
            (not above_mid and r20 < -float(profile["exit_r20"]))
            or (not above_slow and r20 < -float(profile["major_exit"]))
            or (trail < -float(profile["trail"]) and not above_fast)
            or (mem < -0.78 and not above_fast and r20 < -0.04)
        )
        risk_days = risk_days + 1 if damage else max(0, risk_days - 1)
        if damage and risk_days >= 1:
            if (not above_slow and r20 < -float(profile["major_exit"])) or trail < -float(profile["trail2"]) or mem < -0.85:
                target = 0.0
            else:
                target = min(target, 0.25)
        if target < current and not damage and (ma_bull or slow_bull):
            target = current

        levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)
        target = float(levels[np.argmin(np.abs(levels - target))])
        risk_exit = target < current and damage
        strong_entry = target > current and (breakout or impulse or r20 > float(profile["entry_r20"]) or mem > 0.72)
        if abs(target - current) >= 0.25 and (idx - last_change >= int(profile["cooldown"]) or risk_exit or strong_entry):
            if target > current:
                buy_indices.append(idx)
                if current <= 0:
                    peak = price
            else:
                sell_indices.append(idx)
            current = target
            last_change = idx

        positions[idx] = current
        trend_score = 1.0 if target >= 1.0 else 0.65 if target >= 0.75 else 0.25 if target >= 0.25 else -0.35
        scores[idx] = float(np.clip(50.0 + 44.0 * math.tanh((1 - weight) * trend_score + weight * mem), 0.0, 100.0))
        nav[idx + 1] = nav[idx] * max(0.01, 1.0 + current * _pct(float(close.iloc[idx + 1]), price) - cost_rate * abs(current - (positions[idx - 1] if idx > 0 else 0.0)))
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
        "profile": dict(profile),
    }


def _latest_domain_map(raw_events: pd.DataFrame) -> pd.Series:
    local = raw_events.loc[raw_events[DOMAIN_COL].astype(str).isin(v9.v8.STYLE_SIZE_DOMAINS)].copy()
    local = local.sort_values(["ts_code", "date"])
    return local.groupby("ts_code", sort=False).tail(1).set_index("ts_code")[DOMAIN_COL].astype(str)


def _profile_objective(values: List[Dict[str, float]]) -> Dict[str, float]:
    if not values:
        return {"objective": -99.0}
    frame = pd.DataFrame(values)
    excess_sharpe = frame["strategy_sharpe"] - frame["price_sharpe"]
    excess_ann = frame["strategy_annual_return"] - frame["price_annual_return"]
    dd_improve = frame["strategy_max_drawdown"] - frame["price_max_drawdown"]
    turnover = frame["turnover_times_per_year"]
    win_rate = float((excess_sharpe > 0).mean())
    objective = (
        float(excess_sharpe.median())
        + 0.45 * float(excess_ann.median())
        + 0.35 * float(dd_improve.median())
        + 0.18 * win_rate
        - 0.020 * max(0.0, float(turnover.median()) - 10.0)
        - 0.050 * max(0.0, float(turnover.mean()) - 14.0)
    )
    return {
        "objective": float(objective),
        "stocks": int(len(frame)),
        "median_excess_sharpe": float(excess_sharpe.median()),
        "mean_excess_sharpe": float(excess_sharpe.mean()),
        "median_annual_excess": float(excess_ann.median()),
        "median_drawdown_improvement": float(dd_improve.median()),
        "sharpe_win_rate": win_rate,
        "median_turnover": float(turnover.median()),
    }


def select_domain_champions(db: Path, raw_events: pd.DataFrame, as_of: str, output_dir: Path, cost_rate: float) -> Dict[str, Dict[str, Any]]:
    domain_map = _latest_domain_map(raw_events)
    codes = domain_map.index.astype(str).tolist()
    buckets: Dict[str, Dict[str, List[Dict[str, float]]]] = {
        domain: {profile["profile_id"]: [] for profile in PROFILES}
        for domain in sorted(set(domain_map.values))
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    processed = 0
    with sqlite3.connect(str(db)) as conn:
        for batch in _chunks(codes, 180):
            placeholders = ",".join("?" for _ in batch)
            sql = (
                "select trade_date, ts_code, stock_name, open, high, low, close, qfq_close, vol, amount "
                f"from stock_ohlcv_daily where trade_date <= ? and ts_code in ({placeholders}) order by ts_code, trade_date"
            )
            frame = pd.read_sql_query(sql, conn, params=[as_of, *batch])
            for code, group in frame.groupby("ts_code", sort=False):
                domain = str(domain_map.get(code, ""))
                if not domain or domain not in buckets:
                    continue
                stock = _load_stock_from_group(str(code), group)
                if stock is None:
                    continue
                series = _slice_after_month(stock)
                if len(series.close) < 70:
                    continue
                price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
                for profile in PROFILES:
                    replay = _profile_replay(series, profile, None, None, cost_rate)
                    metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
                    buckets[domain][profile["profile_id"]].append(metrics)
                processed += 1
            if processed and processed % 900 < 180:
                print(f"[champion] processed={processed:,}/{len(codes):,}", flush=True)
    rows: List[Dict[str, Any]] = []
    champions: Dict[str, Dict[str, Any]] = {}
    for domain, per_profile in buckets.items():
        scored: List[Dict[str, Any]] = []
        for profile in PROFILES:
            stats = _profile_objective(per_profile.get(profile["profile_id"], []))
            row = {"domain_value": domain, "profile_id": profile["profile_id"], "profile_name": profile["name"], **stats}
            scored.append(row)
            rows.append(row)
        scored.sort(key=lambda item: item.get("objective", -99.0), reverse=True)
        best_id = scored[0]["profile_id"]
        best = next(profile for profile in PROFILES if profile["profile_id"] == best_id)
        champions[domain] = best
    pd.DataFrame(rows).sort_values(["domain_value", "objective"], ascending=[True, False]).to_csv(output_dir / "V13域内执行器候选评估.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"domain_value": domain, "profile_id": profile["profile_id"], "profile_name": profile["name"], **{k: v for k, v in profile.items() if k not in {"profile_id", "name"}}}
            for domain, profile in champions.items()
        ]
    ).to_csv(output_dir / "V13域内冠军执行器.csv", index=False, encoding="utf-8-sig")
    return champions


def _run_one_stock_v13(
    original_series: StockSeries,
    scored_events: pd.DataFrame,
    rulebook: pd.DataFrame,
    thresholds: pd.DataFrame,
    champions: Dict[str, Dict[str, Any]],
    cost_rate: float,
) -> Dict[str, Any]:
    series = _slice_after_month(original_series)
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    target_events = v1._stock_events(scored_events, series)
    if target_events.empty:
        raise RuntimeError(f"{series.code} 没有可用的风格市值域 Wyckoff 事件。")
    raw_score = pd.to_numeric(target_events["_domain_raw_score"], errors="coerce").fillna(0.0)
    daily_raw, active_event_ids = base._daily_domain_memory_score(series, target_events, raw_score)
    latest_domain = str(target_events.sort_values("index").iloc[-1].get(DOMAIN_COL, "未分域"))
    domain_thresholds = v1._threshold_for_domain(thresholds, latest_domain)
    memory_replay = base._threshold_policy_replay(series, daily_raw, domain_thresholds, cooldown=20, cost_rate=cost_rate)
    profile = champions.get(latest_domain, PROFILES[0])
    replay = _profile_replay(series, profile, daily_raw, memory_replay["positions"], cost_rate)
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
        "model_boundary": "模型二：Wyckoff形态记忆学习；风格×市值12域统一规则库；V13每域统一冠军执行器；全历史域内评价但不做单股路径拟合；净值从上市满21个交易日后开始；不使用六类技术因子。",
    }
    active_text = "、".join(
        f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
        for row in active_rows[:4]
    ) if active_rows else "近期无强形态触发，仓位由域冠军执行器和记忆置信修正决定"
    result["latest_signal"] = (
        f"起始日={result['start_date']}；当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"所在域={latest_domain}，域冠军={profile['name']}；"
        f"域内{result['domain_pool_stocks']}只股票、{result['domain_pool_events']}条成熟事件共同学习规则库；"
        f"当前技术信号：{active_text}。"
    )
    return result


def _write_text_record(result: Dict[str, Any], path: Path) -> None:
    metrics = result["metrics"]
    profile = result.get("evolver_profile", {})
    lines = [
        f"{result['code']} {result['name']}：V13 风格×市值域 Wyckoff 记忆 + 域内冠军执行器",
        "",
        f"净值起始日：{result['start_date']}（上市满{START_OFFSET}个交易日后）",
        f"当前结论：{result['current_position_label']}，分数{result['current_score']:.1f}",
        f"所在域：{result['domain_value']}；域冠军执行器：{profile.get('name', profile.get('profile_id'))}",
        f"策略Sharpe：{metrics['strategy_sharpe']:.3f}；原股价Sharpe：{metrics['price_sharpe']:.3f}",
        f"策略年化：{metrics['strategy_annual_return']:.2%}；原股价年化：{metrics['price_annual_return']:.2%}",
        f"策略最大回撤：{metrics['strategy_max_drawdown']:.2%}；原股价最大回撤：{metrics['price_max_drawdown']:.2%}",
        f"年均调仓次数：{metrics['turnover_times_per_year']:.2f}",
        "",
        "执行器选择：每个风格×市值域在全域股票历史上，从6个预声明执行器中选一个冠军；不对单只股票调参。",
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
        lines.append("- 当前无未衰减的强形态命中，主要由域冠军执行器和趋势水温判断仓位。")
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
        profile = result.get("evolver_profile", {})
        rows.append(
            {
                "域": result["domain_value"],
                "域冠军执行器": profile.get("name", profile.get("profile_id", "")),
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
    pd.DataFrame(rows).to_csv(output_dir / "五股V13域内冠军执行器结论.csv", index=False, encoding="utf-8-sig")
    (output_dir / "五股V13域内冠军执行器结论.json").write_text(json.dumps({"results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["V13 风格×市值12域 Wyckoff 记忆 + 域内冠军执行器", ""]
    for row in rows:
        lines.append(
            f"{row['域']}｜{row['代码']} {row['名称']}｜{row['建议仓位']}｜"
            f"策略Sharpe {row['策略Sharpe']} / 原股价Sharpe {row['原股价Sharpe']}｜"
            f"策略年化{row['策略年化']} / 原股价年化{row['原股价年化']}｜"
            f"回撤{row['策略最大回撤']} / {row['原股价最大回撤']}｜年均调仓{row['年均调仓']}｜冠军{row['域冠军执行器']}"
        )
    lines.append("")
    lines.append("说明：同一域内股票共用同一套规则库和同一个冠军执行器；净值从上市满21个交易日后开始；未做单股路径拟合。")
    (output_dir / "五股V13域内冠军执行器结论.txt").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V13 domain champion Wyckoff executor.")
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

    print("[1/7] 读取原始Wyckoff事件与扩展图形事件", flush=True)
    raw_events = pd.read_pickle(args.events)
    strict_wyckoff_raw = raw_events.loc[raw_events[DOMAIN_COL].astype(str).isin(v9.v8.STYLE_SIZE_DOMAINS)].copy()
    expanded_raw = v9.build_expanded_events_compact(args.db, raw_events, args.cache_path, bool(args.refresh_cache))
    print(f"[events] wyckoff={len(strict_wyckoff_raw):,}, expanded={len(expanded_raw):,}, expanded_rules={expanded_raw['rule_id'].nunique():,}", flush=True)

    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)

    print("[2/7] 每个风格×市值域选择统一冠军执行器", flush=True)
    champions = select_domain_champions(args.db, raw_events, str(as_of), output_dir, float(args.cost_rate))
    print("[champion] " + json.dumps({k: v["profile_id"] for k, v in champions.items()}, ensure_ascii=False), flush=True)

    print("[3/7] 合并事件并计算域内记忆", flush=True)
    combined_path = output_dir / "V13_wyckoff_plus_expanded_events.pkl"
    pd.concat([strict_wyckoff_raw, expanded_raw], ignore_index=True, sort=False).to_pickle(combined_path)
    scored_seed = prepare_events(combined_path)
    memory = build_memory(scored_seed, DOMAIN_COL)
    scored_events = attach_memory(scored_seed, memory, DOMAIN_COL)
    scored_events["_domain_raw_score"] = weighted_memory_score(scored_events, v1.DOMAIN_PROFILE)
    print(f"[scored] events={len(scored_events):,}, rules={scored_events['rule_id'].nunique():,}", flush=True)

    print("[4/7] 学习12域统一规则库", flush=True)
    rulebook = v1.build_domain_rulebook(scored_events)
    thresholds = v1.learn_domain_thresholds(scored_events)
    rulebook.to_csv(output_dir / "风格市值12域V13扩展Wyckoff规则库.csv", index=False, encoding="utf-8-sig")
    thresholds.to_csv(output_dir / "风格市值12域V13统一仓位阈值.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(v9.v8.CATALOG.values()).to_csv(output_dir / "V13扩展图形规则目录.csv", index=False, encoding="utf-8-sig")
    print(f"[rulebook] rows={len(rulebook):,}, domains={rulebook['domain_value'].nunique():,}", flush=True)

    print("[5/7] 应用V13域冠军执行器到五只股票", flush=True)
    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        for code in args.codes:
            original = _load_stock(conn, str(code), str(as_of))
            result = _run_one_stock_v13(original, scored_events, rulebook, thresholds, champions, float(args.cost_rate))
            safe = _safe_name(f"V13域内冠军执行器_{result['domain_value']}_{result['code']}_{result['name']}")
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
                f"[stock] {result['domain_value']} {result['code']} {result['name']} start={result['start_date']} "
                f"{result['current_position_label']} Sharpe {result['metrics']['strategy_sharpe']:.2f}/{result['metrics']['price_sharpe']:.2f} "
                f"annual {result['metrics']['strategy_annual_return']:.2%}/{result['metrics']['price_annual_return']:.2%} "
                f"mdd {result['metrics']['strategy_max_drawdown']:.2%}/{result['metrics']['price_max_drawdown']:.2%} "
                f"turnover {result['metrics']['turnover_times_per_year']:.2f}",
                flush=True,
            )

    print("[6/7] 写出汇总", flush=True)
    _write_summary(results, output_dir)
    (output_dir / "V13模型说明.txt").write_text(
        "\n".join(
            [
                "V13域内冠军执行器版",
                f"净值起始规则：上市满{START_OFFSET}个交易日后开始归一化、判断买卖点和回测。",
                f"扩展图形基础规则数：{expanded_raw['rule_id'].nunique()}",
                f"扩展事件数：{len(expanded_raw):,}",
                f"合并后基础规则数：{scored_events['rule_id'].nunique()}",
                f"最终域规则库行数：{len(rulebook):,}",
                "框架：Analyzer扩展图形规则 -> 风格×市值12域Memory -> Rulebook/Evolver -> 每域冠军执行器 -> 五档仓位。",
                "冠军选择：每个域只在6个预声明执行器中选择1个；使用域内全股票全历史评价；不做单股路径拟合。",
                "防过拟合：候选少、参数宽、按域整体中位数评价、惩罚高换手，不追求肉眼完美买卖点。",
            ]
        ),
        encoding="utf-8",
    )

    print("[7/7] 完成", flush=True)
    print(json.dumps(
        {
            "output_dir": str(output_dir),
            "champions": {k: v["profile_id"] for k, v in champions.items()},
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
