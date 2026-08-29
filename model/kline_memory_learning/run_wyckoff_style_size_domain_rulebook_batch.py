"""Style-size domain unified Wyckoff memory rulebook.

This runner is the formal "domain shared memory" version of the Wyckoff
technical learning model:

1. Split all A-share stocks into style x size 12 domains.
2. For each domain, use all matured historical Wyckoff events from every stock
   in that domain to learn one shared rulebook.
3. Apply the same domain rulebook and the same domain thresholds to every
   stock in that domain.

It deliberately does not use the six-family technical factor stack and does
not optimize a separate path for each individual stock.
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
from model.kline_memory_learning.run_wyckoff_domain_evolver_optimization import (  # noqa: E402
    EVENT_PATH,
    attach_memory,
    build_memory,
    evaluate_scored,
    finite,
    prepare_events,
    threshold_grid,
    weighted_memory_score,
)
from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    THEORY,
    StockSeries,
    _annual_stats,
    _load_stock,
    _metrics,
    _position_label,
    _safe_name,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import (  # noqa: E402
    DOMAIN_COL,
    DOMAIN_NAME,
    _plot_style_size_chart,
)


OUTPUT_SUBDIR = "风格市值12域统一规则库Wyckoff"
DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]

DOMAIN_PROFILE: Dict[str, Any] = {
    "name": "风格×市值12域统一Wyckoff规则库",
    "domain_col": DOMAIN_COL,
    "min_exact": 8,
    "min_memory": 18,
    "n_cap": 320,
    "return_scale": 0.052,
    "signed_scale": 0.044,
    "tail_guard": 0.075,
    "w_exact": 1.10,
    "w_stage": 0.80,
    "w_base": 0.54,
    "w_global_stage": 0.25,
    "w_global_base": 0.14,
    "a_memory": 0.74,
    "a_hit": 0.32,
    "a_signed": 0.18,
    "a_trend": 0.72,
    "a_market": 0.0,
    "a_rule": 0.44,
    "a_freq": 0.16,
    "a_quality": 0.18,
    "a_strength": 0.14,
    "a_tail": 0.82,
    "fallback_mean": 0.0,
}


def _assign_positions(score: pd.Series, thresholds: Sequence[float]) -> pd.Series:
    t0, t25, t50, t75 = [float(x) for x in thresholds]
    values = np.select(
        [score <= t0, score <= t25, score <= t50, score <= t75],
        [0.0, 0.25, 0.50, 0.75],
        default=1.0,
    )
    return pd.Series(values, index=score.index)


def _position_from_edge(edge: float) -> float:
    if edge <= -0.28:
        return 0.0
    if edge <= -0.08:
        return 0.25
    if edge <= 0.08:
        return 0.50
    if edge <= 0.26:
        return 0.75
    return 1.0


def _edge_score(row: pd.Series) -> float:
    hit = finite(row.get("hit_rate"), 0.50)
    avg = finite(row.get("avg_forward_return"), 0.0)
    signed = finite(row.get("avg_signed_return"), 0.0)
    tail = finite(row.get("tail10_forward_return"), 0.0)
    support = min(1.0, math.log1p(max(1.0, finite(row.get("sample_count"), 1.0))) / math.log1p(260.0))
    raw = (
        0.42 * math.tanh(avg / 0.055)
        + 0.30 * ((hit - 0.5) * 2.0)
        + 0.22 * math.tanh(signed / 0.050)
        + 0.14 * min(0.0, tail + 0.075)
    )
    return float(raw * (0.35 + 0.65 * support))


def build_domain_rulebook(scored_events: pd.DataFrame) -> pd.DataFrame:
    local = scored_events.copy()
    local["domain_value"] = local[DOMAIN_COL].astype(str)
    group_cols = ["domain_value", "rule_id", "frequency", "stage", "confirmation"]
    rulebook = local.groupby(group_cols, sort=False).agg(
        sample_count=("forward_return", "size"),
        stock_count=("ts_code", "nunique"),
        avg_forward_return=("forward_return", "mean"),
        median_forward_return=("forward_return", "median"),
        hit_rate=("forward_return", lambda item: float((pd.to_numeric(item, errors="coerce") > 0).mean())),
        avg_signed_return=("signed_return", "mean"),
        tail10_forward_return=("forward_return", lambda item: float(np.nanquantile(pd.to_numeric(item, errors="coerce"), 0.10))),
        memory_raw_score=("_domain_raw_score", "mean"),
        avg_strength=("strength", "mean"),
    ).reset_index()
    rulebook = rulebook.loc[rulebook["sample_count"].ge(6)].copy()
    rulebook["rule_name"] = rulebook["rule_id"].map(lambda x: THEORY.get(str(x), {}).get("name_cn", str(x)))
    rulebook["edge_score"] = rulebook.apply(_edge_score, axis=1)
    rulebook["rule_position"] = rulebook["edge_score"].map(_position_from_edge)
    rulebook["rule_position_label"] = rulebook["rule_position"].map(_position_label)
    rulebook["direction_label"] = np.select(
        [rulebook["edge_score"] >= 0.18, rulebook["edge_score"] <= -0.18],
        ["偏多/加仓", "偏空/减仓"],
        default="中性/观察",
    )
    rulebook = rulebook.sort_values(
        ["domain_value", "edge_score", "sample_count"],
        ascending=[True, False, False],
    ).reset_index(drop=True)
    return rulebook


def learn_domain_thresholds(scored_events: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for domain, local in scored_events.groupby(DOMAIN_COL, sort=False):
        local = local.copy()
        score = pd.to_numeric(local["_domain_raw_score"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        best: Dict[str, Any] | None = None
        candidates = threshold_grid(score)
        candidates.extend([
            (-0.52, -0.24, -0.02, 0.18),
            (-0.42, -0.18, 0.04, 0.24),
            (-0.32, -0.10, 0.10, 0.30),
            (-0.24, -0.04, 0.16, 0.36),
            (-0.16, 0.04, 0.22, 0.44),
        ])
        seen: set[tuple[float, float, float, float]] = set()
        for thresholds in candidates:
            key = tuple(round(float(x), 6) for x in thresholds)
            if key in seen:
                continue
            seen.add(key)
            position = _assign_positions(score, thresholds)
            metric = evaluate_scored(local, position, DEFAULT_COST_RATE * 10000.0)
            avg_pos = finite(metric.get("avg_position"), 0.0)
            low_share = finite(metric.get("low_position_share"), 0.0)
            full_share = finite(metric.get("full_position_share"), 0.0)
            objective = (
                finite(metric.get("sharpe"), -9.0)
                + 0.55 * finite(metric.get("annual_return"), -1.0)
                + 0.85 * finite(metric.get("annual_excess"), -1.0)
                + 0.22 * (finite(metric.get("hit_rate"), 0.0) - 0.5)
                + 0.06 * full_share
                - 0.20 * max(0.0, low_share - 0.50)
                - 0.26 * max(0.0, 0.36 - avg_pos)
            )
            if best is None or objective > best["objective"]:
                best = {
                    "domain_value": str(domain),
                    "thresholds": [float(x) for x in thresholds],
                    "objective": float(objective),
                    **metric,
                }
        if best is not None:
            rows.append(best)
    return pd.DataFrame(rows)


def _threshold_for_domain(threshold_table: pd.DataFrame, domain_value: str) -> List[float]:
    row = threshold_table.loc[threshold_table["domain_value"].astype(str).eq(str(domain_value))]
    if row.empty:
        row = threshold_table.sort_values(["events"], ascending=False).head(1)
    if row.empty:
        return [-0.32, -0.10, 0.10, 0.30]
    value = row.iloc[0]["thresholds"]
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = list(value)
    return [float(x) for x in parsed]


def _top_rules(rulebook: pd.DataFrame, domain_value: str, limit: int = 18) -> List[Dict[str, Any]]:
    local = rulebook.loc[rulebook["domain_value"].astype(str).eq(str(domain_value))].copy()
    if local.empty:
        return []
    local["_rank"] = local["edge_score"].abs()
    local = local.sort_values(["_rank", "sample_count"], ascending=[False, False]).head(limit)
    out = []
    for row in local.itertuples(index=False):
        out.append(
            {
                "rule_name": str(row.rule_name),
                "frequency": str(row.frequency),
                "stage": str(row.stage),
                "confirmation": str(row.confirmation),
                "sample_count": int(row.sample_count),
                "stock_count": int(row.stock_count),
                "hit_rate": round(float(row.hit_rate), 4),
                "avg_forward_return": round(float(row.avg_forward_return), 6),
                "tail10_forward_return": round(float(row.tail10_forward_return), 6),
                "edge_score": round(float(row.edge_score), 6),
                "position": str(row.rule_position_label),
                "direction": str(row.direction_label),
            }
        )
    return out


def _matched_rules_for_active(rulebook: pd.DataFrame, domain_value: str, active_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    for active in active_rows:
        local = rulebook.loc[
            rulebook["domain_value"].astype(str).eq(str(domain_value))
            & rulebook["rule_id"].astype(str).eq(str(active.get("rule_id", "")))
            & rulebook["frequency"].astype(str).eq(str(active.get("frequency", "")))
            & rulebook["stage"].astype(str).eq(str(active.get("stage", "")))
            & rulebook["confirmation"].astype(str).eq(str(active.get("confirmation", "")))
        ]
        if local.empty:
            continue
        row = local.iloc[0]
        matched.append(
            {
                "date": str(active.get("date", "")),
                "rule_name": str(row["rule_name"]),
                "frequency": str(row["frequency"]),
                "stage": str(row["stage"]),
                "confirmation": str(row["confirmation"]),
                "sample_count": int(row["sample_count"]),
                "stock_count": int(row["stock_count"]),
                "hit_rate": round(float(row["hit_rate"]), 4),
                "edge_score": round(float(row["edge_score"]), 6),
                "position": str(row["rule_position_label"]),
                "direction": str(row["direction_label"]),
            }
        )
    return matched


def _stock_events(scored: pd.DataFrame, series: StockSeries) -> pd.DataFrame:
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


def _run_one_stock(
    series: StockSeries,
    scored_events: pd.DataFrame,
    rulebook: pd.DataFrame,
    threshold_table: pd.DataFrame,
    cost_rate: float,
) -> Dict[str, Any]:
    price_nav = series.close.astype(float) / max(float(series.close[0]), 1e-9)
    target_events = _stock_events(scored_events, series)
    if target_events.empty:
        raise RuntimeError(f"{series.code} 没有可用的风格×市值域 Wyckoff 事件。")
    raw_score = pd.to_numeric(target_events["_domain_raw_score"], errors="coerce").fillna(0.0)
    daily_raw, active_event_ids = base._daily_domain_memory_score(series, target_events, raw_score)
    latest_idx = len(series.dates) - 1
    latest_domain = str(target_events.sort_values("index").iloc[-1].get(DOMAIN_COL, "未分域"))
    thresholds = _threshold_for_domain(threshold_table, latest_domain)
    replay = base._threshold_policy_replay(series, daily_raw, thresholds, cooldown=22, cost_rate=cost_rate)
    metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
    annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
    current_position = float(replay["current_position"])
    current_score = 78.0 if current_position >= 1.0 else 66.0 if current_position >= 0.75 else 55.0 if current_position >= 0.50 else 46.0 if current_position >= 0.25 else 38.0
    active_rows = base._active_rows_for_date(target_events, raw_score, active_event_ids, latest_idx)
    domain_slice = scored_events.loc[scored_events[DOMAIN_COL].astype(str).eq(latest_domain)]
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
        "matched_domain_rules": _matched_rules_for_active(rulebook, latest_domain, active_rows),
        "top_domain_rules": _top_rules(rulebook, latest_domain, limit=18),
        "domain_thresholds": thresholds,
        "evolver_profile": {
            "mode": "style_size_domain_unified_rulebook",
            "domain_col": DOMAIN_COL,
            "cooldown": 22,
            "thresholds": thresholds,
            "rule_source": "all_matured_events_in_same_style_size_domain",
            "stock_specific_path_optimization": False,
        },
        "model_boundary": "模型二：Wyckoff形态记忆学习；风格×市值12域统一规则库；同域全股票共用规则；不使用六类技术因子。",
    }
    if result["current_active_signals"]:
        active_text = "、".join(
            f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
            for row in result["current_active_signals"][:4]
        )
    else:
        active_text = "近期无强触发形态，仓位由同域统一规则库和趋势尾部状态共同决定"
    result["latest_signal"] = (
        f"当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"所在域={latest_domain}，域内{result['domain_pool_stocks']}只股票、"
        f"{result['domain_pool_events']}条成熟Wyckoff事件共同学习出统一规则库；"
        f"当前技术信号：{active_text}。"
    )
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


def _write_text_record(result: Dict[str, Any], path: Path) -> None:
    lines = [
        f"{result['code']} {result['name']}｜风格×市值域统一Wyckoff规则库学习记录",
        "",
        f"当前结论：{result['current_position_label']}，分数{result['current_score']:.1f}",
        f"所属域：{result['domain_value']}；域内股票数：{result['domain_pool_stocks']}；域内成熟事件数：{result['domain_pool_events']}",
        f"策略Sharpe：{result['metrics']['strategy_sharpe']:.3f}；原股价Sharpe：{result['metrics']['price_sharpe']:.3f}",
        f"策略年化：{result['metrics']['strategy_annual_return']:.2%}；原股价年化：{result['metrics']['price_annual_return']:.2%}",
        f"策略最大回撤：{result['metrics']['strategy_max_drawdown']:.2%}；原股价最大回撤：{result['metrics']['price_max_drawdown']:.2%}",
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
        lines.append("- 当前没有未衰减的精确形态命中，仓位由同域规则库的尾部趋势状态决定。")
    lines.extend(["", "该域代表性规则（按边际强度排序）："])
    for rule in result["top_domain_rules"][:18]:
        lines.append(
            f"- {rule['frequency']} {rule['rule_name']}｜{rule['stage']}｜{rule['confirmation']}："
            f"{rule['direction']}，建议{rule['position']}，样本{rule['sample_count']}，覆盖{rule['stock_count']}只，"
            f"命中率{rule['hit_rate']:.1%}，20日均值{rule['avg_forward_return']:.2%}，10%尾部{rule['tail10_forward_return']:.2%}"
        )
    lines.extend([
        "",
        "模型边界：本版本先按风格×市值12域学习统一规则库，再应用到同域个股；没有给该个股单独调参，也没有调用六类技术因子。",
    ])
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
                "数据截止": result["as_of"],
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
    pd.DataFrame(rows).to_csv(output_dir / "五股统一域规则库结论.csv", index=False, encoding="utf-8-sig")
    (output_dir / "五股统一域规则库结论.json").write_text(json.dumps({"results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["风格×市值12域统一Wyckoff规则库五股结论", ""]
    for row in rows:
        lines.append(
            f"{row['域']}｜{row['代码']} {row['名称']}：{row['建议仓位']}，"
            f"策略Sharpe {row['策略Sharpe']} / 原股价Sharpe {row['原股价Sharpe']}，"
            f"策略年化{row['策略年化']} / 原股价年化{row['原股价年化']}。"
        )
    lines.extend(["", "说明：同一域内股票共用同一套规则库和阈值；没有单股路径拟合。"])
    (output_dir / "五股统一域规则库结论.txt").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run style-size unified Wyckoff domain rulebook.")
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

    print("[1/5] 读取全历史成熟Wyckoff事件与风格×市值12域", flush=True)
    all_events = prepare_events(args.events)
    print(f"[events] {len(all_events):,} events / {all_events['ts_code'].nunique():,} stocks", flush=True)

    print("[2/5] 构建域内共享记忆层并计算事件分数", flush=True)
    memory = build_memory(all_events, DOMAIN_COL)
    scored_events = attach_memory(all_events, memory, DOMAIN_COL)
    scored_events["_domain_raw_score"] = weighted_memory_score(scored_events, DOMAIN_PROFILE)

    print("[3/5] 学习每个风格×市值域的统一多规则库和统一仓位阈值", flush=True)
    rulebook = build_domain_rulebook(scored_events)
    thresholds = learn_domain_thresholds(scored_events)
    rulebook.to_csv(output_dir / "风格市值12域统一Wyckoff规则库.csv", index=False, encoding="utf-8-sig")
    thresholds.to_csv(output_dir / "风格市值12域统一仓位阈值.csv", index=False, encoding="utf-8-sig")
    print(f"[rulebook] {len(rulebook):,} rules / {rulebook['domain_value'].nunique():,} domains", flush=True)

    print("[4/5] 将同域统一规则应用到五只样本股票", flush=True)
    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for code in args.codes:
            series = _load_stock(conn, str(code), as_of)
            result = _run_one_stock(series, scored_events, rulebook, thresholds, float(args.cost_rate))
            safe = _safe_name(f"风格市值统一规则库_{result['domain_value']}_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            json_path = output_dir / f"{safe}.json"
            txt_path = output_dir / f"{safe}_学习记录.txt"
            _plot_style_size_chart(result, chart_path)
            json_path.write_text(json.dumps(_json_light(result), ensure_ascii=False, indent=2), encoding="utf-8")
            _write_text_record(result, txt_path)
            result["chart_path"] = str(chart_path)
            result["json_path"] = str(json_path)
            result["txt_path"] = str(txt_path)
            results.append(result)
            print(
                f"[stock] {result['domain_value']} {result['code']} {result['name']} "
                f"{result['current_position_label']} Sharpe {result['metrics']['strategy_sharpe']:.2f}/"
                f"{result['metrics']['price_sharpe']:.2f}",
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
