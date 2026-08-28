"""V5 style-size Wyckoff domain rulebook with trend executor.

The learning frame is unchanged:
- all stocks are assigned to the 12 style x size domains;
- each domain learns one shared Wyckoff memory rulebook;
- stocks in the same domain use the same rulebook, no per-stock path fitting.

V5 improves only the execution layer.  The executor combines the domain
Wyckoff memory score with a low-frequency trend state, a drawdown gate, and a
hysteresis/cooldown rule to make five-level positions.  This is intended to
capture large markup trends while cutting exposure after clear markdowns.
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


OUTPUT_SUBDIR = "风格市值12域统一规则库Wyckoff_V5趋势执行器"
DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
STYLE_SIZE_DOMAINS = {
    "大盘成长",
    "大盘均衡",
    "大盘价值",
    "大盘红利",
    "中盘成长",
    "中盘均衡",
    "中盘价值",
    "中盘红利",
    "小盘成长",
    "小盘均衡",
    "小盘价值",
    "小盘红利",
}


def _rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    series = pd.Series(values)
    return series.rolling(window, min_periods=max(5, window // 5)).max().to_numpy(dtype=float)


def _rolling_vol(values: np.ndarray, window: int = 20) -> np.ndarray:
    returns = pd.Series(values).pct_change()
    return returns.rolling(window, min_periods=8).std().fillna(0.0).to_numpy(dtype=float)


def _trend_state(series: StockSeries, daily_raw: np.ndarray) -> Dict[str, np.ndarray]:
    close = series.close.astype(float)
    features = _context_features(series)
    ma20 = features["ma20"]
    ma60 = features["ma60"]
    ma120 = features["ma120"]
    ret20 = features["ret20"]
    ret60 = features["ret60"]
    ret120 = pd.Series(close).pct_change(120).fillna(0.0).to_numpy(dtype=float)
    high60 = _rolling_max(close, 60)
    high120 = _rolling_max(close, 120)
    vol20 = _rolling_vol(close, 20)

    score = np.zeros(len(close), dtype=float)
    strong_up = np.zeros(len(close), dtype=bool)
    risk_off = np.zeros(len(close), dtype=bool)
    breakout = np.zeros(len(close), dtype=bool)
    pullback_buy = np.zeros(len(close), dtype=bool)

    for idx in range(120, len(close)):
        values = (close[idx], ma20[idx], ma60[idx], ma120[idx], ret20[idx], ret60[idx], ret120[idx])
        if not all(np.isfinite(x) for x in values):
            continue
        above20 = close[idx] > ma20[idx]
        above60 = close[idx] > ma60[idx]
        above120 = close[idx] > ma120[idx]
        ma_bull = ma20[idx] > ma60[idx] > ma120[idx]
        ma_recover = ma20[idx] > ma120[idx] and close[idx] > ma60[idx]
        dd60 = close[idx] / max(high60[idx], 1e-9) - 1.0 if np.isfinite(high60[idx]) else 0.0
        dd120 = close[idx] / max(high120[idx], 1e-9) - 1.0 if np.isfinite(high120[idx]) else 0.0
        memory = math.tanh(float(daily_raw[idx]) * 1.55)

        trend = 0.0
        if ma_bull and above20 and ret20[idx] > 0.015 and ret60[idx] > 0.045:
            trend += 1.05
            strong_up[idx] = True
        elif ma_recover and above120 and ret60[idx] > 0.015:
            trend += 0.58
        elif above120 and ret20[idx] > 0.0:
            trend += 0.30
        if np.isfinite(high120[idx]) and close[idx] >= 0.985 * high120[idx] and ret60[idx] > 0.04:
            trend += 0.30
            breakout[idx] = True
        if above120 and ret60[idx] > 0.06 and -0.10 <= ret20[idx] < 0.00:
            trend += 0.22
            pullback_buy[idx] = True

        if close[idx] < ma120[idx] and ma20[idx] < ma60[idx]:
            trend -= 0.85
        elif close[idx] < ma60[idx] and ret20[idx] < -0.025:
            trend -= 0.45
        if dd60 < -0.16 and close[idx] < ma20[idx]:
            trend -= 0.46
        if dd120 < -0.24 and close[idx] < ma60[idx]:
            trend -= 0.55
        if ret20[idx] < -0.13 and close[idx] < ma60[idx]:
            trend -= 0.70
        if vol20[idx] > 0.045 and ret20[idx] < -0.06:
            trend -= 0.18

        risk_off[idx] = (
            (close[idx] < ma120[idx] and ma20[idx] < ma60[idx] and ret20[idx] < -0.015)
            or (dd60 < -0.18 and close[idx] < ma60[idx])
            or (ret20[idx] < -0.15 and close[idx] < ma20[idx])
        )
        score[idx] = 0.56 * trend + 0.44 * memory
    return {
        "score": score,
        "strong_up": strong_up,
        "risk_off": risk_off,
        "breakout": breakout,
        "pullback_buy": pullback_buy,
        "high120": high120,
    }


def _score_to_target(score: float) -> float:
    if score <= -0.70:
        return 0.0
    if score <= -0.22:
        return 0.25
    if score <= 0.22:
        return 0.50
    if score <= 0.62:
        return 0.75
    return 1.0


def _trend_executor_replay(series: StockSeries, daily_raw: np.ndarray, cost_rate: float) -> Dict[str, Any]:
    close = series.close.astype(float)
    n = len(close)
    state = _trend_state(series, daily_raw)
    score = state["score"]
    nav = np.ones(n, dtype=float)
    positions = np.zeros(n, dtype=float)
    scores_0_100 = np.asarray([float(np.clip(50.0 + 42.0 * math.tanh(x), 0.0, 100.0)) for x in score], dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current = 0.0
    last_change = -10000
    peak_since_entry = 0.0
    cooldown = 16

    for idx in range(120, n - 1):
        target = _score_to_target(float(score[idx]))
        if state["strong_up"][idx] or state["breakout"][idx]:
            target = max(target, 0.75)
        if state["pullback_buy"][idx] and daily_raw[idx] > -0.20:
            target = max(target, 0.50)
        if state["risk_off"][idx]:
            target = min(target, 0.25)

        if current > 0:
            peak_since_entry = max(peak_since_entry, float(close[idx]))
            drawdown_from_entry_peak = close[idx] / max(peak_since_entry, 1e-9) - 1.0
            if drawdown_from_entry_peak < -0.14 and score[idx] < 0.35:
                target = min(target, 0.25)
            if drawdown_from_entry_peak < -0.21 and score[idx] < 0.10:
                target = 0.0

        risk_exit = target < current and (state["risk_off"][idx] or score[idx] < -0.55)
        strong_entry = target > current and (state["strong_up"][idx] or score[idx] > 0.70)
        can_change = idx - last_change >= cooldown or risk_exit or strong_entry
        if abs(target - current) >= 0.25 and can_change:
            if target > current:
                buy_indices.append(idx)
                if current <= 0:
                    peak_since_entry = float(close[idx])
            else:
                sell_indices.append(idx)
                if target <= 0:
                    peak_since_entry = 0.0
            current = float(target)
            last_change = idx

        positions[idx] = current
        turnover_cost = cost_rate * abs(current - (positions[idx - 1] if idx > 0 else 0.0))
        nav[idx + 1] = nav[idx] * max(0.01, 1.0 + current * _pct(close[idx + 1], close[idx]) - turnover_cost)
    positions[-1] = current
    if n > 1:
        scores_0_100[-1] = scores_0_100[-2]
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores_0_100,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(current),
        "current_score": float(scores_0_100[-1]),
        "profile": {
            "mode": "style_size_domain_rulebook_v5_trend_executor",
            "cooldown": cooldown,
            "position_levels": [0.0, 0.25, 0.50, 0.75, 1.0],
            "stock_specific_path_optimization": False,
        },
    }


def _run_one_stock(
    series: StockSeries,
    scored_events: pd.DataFrame,
    rulebook: pd.DataFrame,
    cost_rate: float,
) -> Dict[str, Any]:
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    target_events = v1._stock_events(scored_events, series)
    if target_events.empty:
        raise RuntimeError(f"{series.code} 没有可用的风格×市值域 Wyckoff 事件。")
    raw_score = pd.to_numeric(target_events["_domain_raw_score"], errors="coerce").fillna(0.0)
    daily_raw, active_event_ids = base._daily_domain_memory_score(series, target_events, raw_score)
    replay = _trend_executor_replay(series, daily_raw, cost_rate)
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
        "model_boundary": "模型二：Wyckoff形态记忆学习；风格×市值12域统一规则库；全市场兜底记忆；趋势执行器；同域全股票共用规则；不使用六类技术因子；不做单股路径拟合。",
    }
    if active_rows:
        active_text = "、".join(
            f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
            for row in active_rows[:4]
        )
    else:
        active_text = "近期无强触发形态，仓位由同域规则库、MA20/MA60/MA120趋势与回撤闸门共同决定"
    result["latest_signal"] = (
        f"当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"所在域={latest_domain}，域内{result['domain_pool_stocks']}只股票、"
        f"{result['domain_pool_events']}条成熟Wyckoff事件共同学习规则库；"
        f"当前技术信号：{active_text}。"
    )
    return result


def _write_text_record(result: Dict[str, Any], path: Path) -> None:
    lines = [
        f"{result['code']} {result['name']}｜V5风格×市值域统一Wyckoff规则库+趋势执行器",
        "",
        f"当前结论：{result['current_position_label']}，分数{result['current_score']:.1f}",
        f"所属域：{result['domain_value']}；域内股票数：{result['domain_pool_stocks']}；域内成熟事件数：{result['domain_pool_events']}",
        f"策略Sharpe：{result['metrics']['strategy_sharpe']:.3f}；原股价Sharpe：{result['metrics']['price_sharpe']:.3f}",
        f"策略年化：{result['metrics']['strategy_annual_return']:.2%}；原股价年化：{result['metrics']['price_annual_return']:.2%}",
        f"策略最大回撤：{result['metrics']['strategy_max_drawdown']:.2%}；原股价最大回撤：{result['metrics']['price_max_drawdown']:.2%}",
        "",
        "执行器规则：同域Wyckoff记忆分数 + MA20/MA60/MA120趋势状态 + 60/120日滚动高点回撤闸门 + 16日冷却期，输出0/25/50/75/100五档仓位。",
        "",
        "当前命中的域规则：",
    ]
    if result["matched_domain_rules"]:
        for rule in result["matched_domain_rules"]:
            lines.append(
                f"- {rule['date']} {rule['frequency']} {rule['rule_name']}｜{rule['stage']}｜{rule['confirmation']}："
                f"{rule['direction']}，建议{rule['position']}，样本{rule['sample_count']}，命中率{rule['hit_rate']:.1%}，edge={rule['edge_score']:.3f}"
            )
    else:
        lines.append("- 当前没有未衰减的精确形态命中，仓位由同域规则库和趋势执行器决定。")
    lines.extend(["", "该域代表性规则（按边际强度排序）："])
    for rule in result["top_domain_rules"][:18]:
        lines.append(
            f"- {rule['frequency']} {rule['rule_name']}｜{rule['stage']}｜{rule['confirmation']}："
            f"{rule['direction']}，建议{rule['position']}，样本{rule['sample_count']}，覆盖{rule['stock_count']}只，"
            f"命中率{rule['hit_rate']:.1%}，20日均值{rule['avg_forward_return']:.2%}，10%尾部{rule['tail10_forward_return']:.2%}"
        )
    lines.extend(["", "模型边界：没有给该个股单独调参，没有单股路径拟合，没有调用六类技术因子。"])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_summary(results: List[Dict[str, Any]], output_dir: Path) -> None:
    rows = []
    for result in results:
        metrics = result["metrics"]
        rows.append(
            {
                "域": result["domain_value"],
                "代码": result["code"],
                "名称": result["name"],
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
    pd.DataFrame(rows).to_csv(output_dir / "五股V5趋势执行器结论.csv", index=False, encoding="utf-8-sig")
    (output_dir / "五股V5趋势执行器结论.json").write_text(json.dumps({"results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["V5 风格×市值12域统一Wyckoff规则库 + 趋势执行器 五股结论", ""]
    for row in rows:
        lines.append(
            f"{row['域']}｜{row['代码']} {row['名称']}：{row['建议仓位']}，"
            f"策略Sharpe {row['策略Sharpe']} / 原股价Sharpe {row['原股价Sharpe']}，"
            f"策略年化{row['策略年化']} / 原股价年化{row['原股价年化']}，"
            f"年均调仓{row['年均调仓']}。"
        )
    lines.append("")
    lines.append("说明：同域股票共用同一套Wyckoff域规则库；执行层使用统一趋势/回撤/冷却规则；没有单股路径拟合。")
    (output_dir / "五股V5趋势执行器结论.txt").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V5 style-size Wyckoff rulebook with trend executor.")
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

    print("[4/5] 应用V5趋势执行器到五只样本股票", flush=True)
    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for code in args.codes:
            series = _load_stock(conn, str(code), as_of)
            result = _run_one_stock(series, scored_events, rulebook, float(args.cost_rate))
            safe = _safe_name(f"V5风格市值趋势执行器_{result['domain_value']}_{result['code']}_{result['name']}")
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
                f"{result['current_position_label']} Sharpe {result['metrics']['strategy_sharpe']:.2f}/"
                f"{result['metrics']['price_sharpe']:.2f} annual {result['metrics']['strategy_annual_return']:.2%}/"
                f"{result['metrics']['price_annual_return']:.2%}",
                flush=True,
            )

    print("[5/5] 写出汇总", flush=True)
    _write_summary(results, output_dir)
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
