"""V6 style-size Wyckoff rulebook with markup-priority executor.

V6 keeps the domain-shared Wyckoff memory framework and changes only the
execution policy.  Compared with V5, the executor is more return-seeking:
when price is above MA120 and MA20 confirms a markup trend, it keeps high
exposure unless the domain memory or drawdown gate gives a clear warning.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning import run_wyckoff_industry_style_memory_batch as base  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_batch as v1  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v5_trend_executor_batch as v5  # noqa: E402
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
    _context_features,
    _load_stock,
    _metrics,
    _pct,
    _position_label,
    _safe_name,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import (  # noqa: E402
    DOMAIN_COL,
    DOMAIN_NAME,
    _plot_style_size_chart,
)


OUTPUT_SUBDIR = "风格市值12域统一规则库Wyckoff_V6主升段执行器"
DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
STYLE_SIZE_DOMAINS = v5.STYLE_SIZE_DOMAINS


def _markup_replay(series: StockSeries, daily_raw: np.ndarray, cost_rate: float) -> Dict[str, Any]:
    close = series.close.astype(float)
    n = len(close)
    features = _context_features(series)
    ma20 = features["ma20"]
    ma60 = features["ma60"]
    ma120 = features["ma120"]
    ret20 = features["ret20"]
    ret60 = features["ret60"]
    high60 = pd.Series(close).rolling(60, min_periods=20).max().to_numpy(dtype=float)
    high120 = pd.Series(close).rolling(120, min_periods=40).max().to_numpy(dtype=float)

    nav = np.ones(n, dtype=float)
    positions = np.zeros(n, dtype=float)
    scores = np.full(n, 50.0, dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current = 0.0
    last_change = -10000
    peak = 0.0
    cooldown = 18

    for idx in range(120, n - 1):
        values = (close[idx], ma20[idx], ma60[idx], ma120[idx], ret20[idx], ret60[idx])
        if not all(np.isfinite(x) for x in values):
            continue
        memory = math.tanh(float(daily_raw[idx]) * 1.45)
        dd60 = close[idx] / max(high60[idx], 1e-9) - 1.0 if np.isfinite(high60[idx]) else 0.0
        dd120 = close[idx] / max(high120[idx], 1e-9) - 1.0 if np.isfinite(high120[idx]) else 0.0

        above120 = close[idx] > ma120[idx]
        above60 = close[idx] > ma60[idx]
        ma_markup = ma20[idx] > ma120[idx]
        strong_markup = above120 and above60 and ma20[idx] > ma60[idx] and ret20[idx] > -0.02 and ret60[idx] > 0.015
        acceleration = above120 and ma20[idx] > ma60[idx] and ret20[idx] > 0.04
        recovery = close[idx] > ma60[idx] and ret20[idx] > 0.025 and memory > -0.25
        breakdown = close[idx] < ma120[idx] and ma20[idx] < ma60[idx] and ret20[idx] < -0.02
        sharp_drawdown = dd60 < -0.17 and close[idx] < ma20[idx]
        deep_break = dd120 < -0.25 and close[idx] < ma60[idx]

        if strong_markup:
            target = 1.0
        elif above120 and ma_markup:
            target = 0.75
        elif above120 or recovery:
            target = 0.50
        elif memory > 0.38 and ret20[idx] > -0.04:
            target = 0.25
        else:
            target = 0.0

        if acceleration and memory > -0.55:
            target = max(target, 1.0)
        elif memory > 0.55 and not breakdown:
            target = max(target, 0.75)
        if memory < -0.65 and close[idx] < ma20[idx]:
            target = min(target, 0.25)
        if breakdown or sharp_drawdown:
            target = min(target, 0.25)
        if deep_break or (ret20[idx] < -0.16 and close[idx] < ma60[idx]):
            target = 0.0

        if current > 0:
            peak = max(peak, float(close[idx]))
            trail = close[idx] / max(peak, 1e-9) - 1.0
            if trail < -0.18 and close[idx] < ma20[idx]:
                target = min(target, 0.25)
            if trail < -0.26 and close[idx] < ma60[idx]:
                target = 0.0

        raw_score = 0.55 * (2.0 * target - 1.0) + 0.45 * memory
        scores[idx] = float(np.clip(50.0 + 42.0 * math.tanh(raw_score), 0.0, 100.0))
        risk_exit = target < current and (breakdown or sharp_drawdown or deep_break or memory < -0.65)
        strong_entry = target > current and (strong_markup or acceleration or memory > 0.65)
        can_change = idx - last_change >= cooldown or risk_exit or strong_entry
        if abs(target - current) >= 0.25 and can_change:
            if target > current:
                buy_indices.append(idx)
                if current <= 0:
                    peak = float(close[idx])
            else:
                sell_indices.append(idx)
                if target <= 0:
                    peak = 0.0
            current = float(target)
            last_change = idx

        positions[idx] = current
        turnover_cost = cost_rate * abs(current - (positions[idx - 1] if idx > 0 else 0.0))
        nav[idx + 1] = nav[idx] * max(0.01, 1.0 + current * _pct(close[idx + 1], close[idx]) - turnover_cost)
    positions[-1] = current
    if n > 1:
        scores[-1] = scores[-2]
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(current),
        "current_score": float(scores[-1]),
        "profile": {
            "mode": "style_size_domain_rulebook_v6_markup_executor",
            "cooldown": cooldown,
            "position_levels": [0.0, 0.25, 0.50, 0.75, 1.0],
            "stock_specific_path_optimization": False,
        },
    }


def _run_one_stock(series: StockSeries, scored_events: pd.DataFrame, rulebook: pd.DataFrame, cost_rate: float) -> Dict[str, Any]:
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    target_events = v1._stock_events(scored_events, series)
    if target_events.empty:
        raise RuntimeError(f"{series.code} 没有可用的风格×市值域 Wyckoff 事件。")
    raw_score = pd.to_numeric(target_events["_domain_raw_score"], errors="coerce").fillna(0.0)
    daily_raw, active_event_ids = base._daily_domain_memory_score(series, target_events, raw_score)
    replay = _markup_replay(series, daily_raw, cost_rate)
    metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
    annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
    latest_idx = len(series.dates) - 1
    latest_domain = str(target_events.sort_values("index").iloc[-1].get(DOMAIN_COL, "未分域"))
    domain_slice = scored_events.loc[scored_events[DOMAIN_COL].astype(str).eq(latest_domain)]
    active_rows = base._active_rows_for_date(target_events, raw_score, active_event_ids, latest_idx)
    current_position = float(replay["current_position"])
    current_score = 78.0 if current_position >= 1.0 else 66.0 if current_position >= 0.75 else 55.0 if current_position >= 0.50 else 46.0 if current_position >= 0.25 else 38.0
    result: Dict[str, Any] = {
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
        "matched_domain_rules": v1._matched_rules_for_active(rulebook, latest_domain, active_rows),
        "top_domain_rules": v1._top_rules(rulebook, latest_domain, limit=18),
        "evolver_profile": replay["profile"],
        "model_boundary": "模型二：Wyckoff形态记忆学习；风格×市值12域统一规则库；全市场兜底记忆；主升段趋势执行器；同域全股票共用规则；不使用六类技术因子；不做单股路径拟合。",
    }
    active_text = "、".join(
        f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
        for row in active_rows[:4]
    ) if active_rows else "近期无强触发形态，仓位由MA20/MA120主升段、回撤闸门和同域记忆共同决定"
    result["latest_signal"] = (
        f"当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"所在域={latest_domain}，域内{result['domain_pool_stocks']}只股票、"
        f"{result['domain_pool_events']}条成熟Wyckoff事件共同学习规则库；"
        f"当前技术信号：{active_text}。"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V6 markup-priority Wyckoff domain executor.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=EVENT_PATH)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / OUTPUT_SUBDIR)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/5] 读取全历史成熟Wyckoff事件", flush=True)
    full_events = prepare_events(args.events)
    strict_events = full_events.loc[full_events[DOMAIN_COL].astype(str).isin(STYLE_SIZE_DOMAINS)].copy()
    print(f"[events] strict={len(strict_events):,} / full={len(full_events):,} events", flush=True)

    print("[2/5] 构建12域规则记忆 + 全市场兜底记忆", flush=True)
    memory = build_memory(full_events, DOMAIN_COL)
    scored_events = attach_memory(strict_events, memory, DOMAIN_COL)
    scored_events["_domain_raw_score"] = weighted_memory_score(scored_events, v1.DOMAIN_PROFILE)

    print("[3/5] 学习12域统一多规则库", flush=True)
    rulebook = v1.build_domain_rulebook(scored_events)
    rulebook.to_csv(output_dir / "风格市值12域统一Wyckoff规则库.csv", index=False, encoding="utf-8-sig")
    print(f"[rulebook] {len(rulebook):,} rules / {rulebook['domain_value'].nunique():,} domains", flush=True)

    print("[4/5] 应用V6主升段执行器到五只样本股票", flush=True)
    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for code in args.codes:
            series = _load_stock(conn, str(code), as_of)
            result = _run_one_stock(series, scored_events, rulebook, float(args.cost_rate))
            safe = _safe_name(f"V6风格市值主升段执行器_{result['domain_value']}_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            json_path = output_dir / f"{safe}.json"
            txt_path = output_dir / f"{safe}_学习记录.txt"
            result["chart_path"] = str(chart_path)
            result["json_path"] = str(json_path)
            result["txt_path"] = str(txt_path)
            _plot_style_size_chart(result, chart_path)
            json_path.write_text(json.dumps(v1._json_light(result), ensure_ascii=False, indent=2), encoding="utf-8")
            v5._write_text_record(result, txt_path)
            results.append(result)
            print(
                f"[stock] {result['domain_value']} {result['code']} {result['name']} "
                f"{result['current_position_label']} Sharpe {result['metrics']['strategy_sharpe']:.2f}/"
                f"{result['metrics']['price_sharpe']:.2f} annual {result['metrics']['strategy_annual_return']:.2%}/"
                f"{result['metrics']['price_annual_return']:.2%}",
                flush=True,
            )

    print("[5/5] 写出汇总", flush=True)
    v5._write_summary(results, output_dir)
    print(json.dumps(
        {
            "output_dir": str(output_dir),
            "rule_count": int(len(rulebook)),
            "domain_count": int(rulebook["domain_value"].nunique()),
            "results": [
                {
                    "code": item["code"],
                    "name": item["name"],
                    "domain": item["domain_value"],
                    "position": item["current_position_label"],
                    "score": round(float(item["current_score"]), 1),
                    "strategy_sharpe": round(float(item["metrics"]["strategy_sharpe"]), 3),
                    "price_sharpe": round(float(item["metrics"]["price_sharpe"]), 3),
                    "strategy_annual": round(float(item["metrics"]["strategy_annual_return"]), 4),
                    "price_annual": round(float(item["metrics"]["price_annual_return"]), 4),
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
