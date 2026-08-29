"""V25 low-frequency domain-shared full-history teacher evolver.

This version is stricter than V24:
1. The style x size 12-domain membership is read from the expanded
   Wyckoff/technical-shape event pool.
2. Each domain learns exactly one low-frequency Teacher-Evolve profile from
   every stock in that domain.
3. The learned domain profile is then applied unchanged to target stocks.

The user explicitly requested no train/test split and full-history optimization.
Accordingly, historical bars with mature 10/20/60/120-day forward outcomes are
allowed to train the teacher tape.  The most recent 120 bars, whose forward
outcomes are not mature, use a causal trend fallback.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    _annual_stats,
    _load_stock,
    _metrics,
    _pct,
    _position_label,
    _safe_name,
)
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v9_compact_patterns import MAX_EVENTS_PER_STOCK  # noqa: E402
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import DOMAIN_COL, _plot_style_size_chart  # noqa: E402
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v13_domain_champion_executor import _slice_after_month  # noqa: E402


DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
DEFAULT_OUTPUT_DIR = Path("agent/output/kline_memory_learning/v25_domain_teacher_evolver_lowfreq")
DEFAULT_CACHE = Path("agent/output/kline_memory_learning/expanded_pattern_events_v9_compact.pkl")
TARGET_DOMAINS = ["大盘成长", "大盘均衡", "大盘价值"]


@dataclass
class StockTape:
    code: str
    name: str
    dates: List[str]
    close: np.ndarray
    price_nav: np.ndarray
    f10: np.ndarray
    f20: np.ndarray
    f60: np.ndarray
    f120: np.ndarray
    best: np.ndarray
    mn20: np.ndarray
    mn60: np.ndarray
    fallback: np.ndarray


BASE_PARAMS: Dict[str, Dict[str, float | int]] = {
    "大盘成长": dict(
        s10=0.06, s20=0.12, s60=0.28, s120=0.45, sbest=0.25,
        mn20_offset=0.070, mn20_scale=0.10, mn60_offset=0.140, mn60_scale=0.18,
        full=2.10, heavy=1.20, half=0.30, light=-0.50,
    ),
    "大盘均衡": dict(
        s10=0.04, s20=0.08, s60=0.18, s120=0.30, sbest=0.18,
        mn20_offset=0.045, mn20_scale=0.08, mn60_offset=0.090, mn60_scale=0.14,
        full=1.90, heavy=1.00, half=0.20, light=-0.60,
    ),
    "大盘价值": dict(
        s10=0.035, s20=0.07, s60=0.15, s120=0.25, sbest=0.15,
        mn20_offset=0.045, mn20_scale=0.07, mn60_offset=0.090, mn60_scale=0.12,
        full=1.60, heavy=0.76, half=0.02, light=-0.74,
    ),
}


def _future_min(close: np.ndarray, horizon: int) -> np.ndarray:
    series = pd.Series(close)
    values = [series.shift(-offset) / series - 1.0 for offset in range(1, horizon + 1)]
    return pd.concat(values, axis=1).min(axis=1).to_numpy(float)


def _causal_fallback(close_values: np.ndarray) -> np.ndarray:
    close = pd.Series(np.asarray(close_values, dtype=float))
    ma20 = close.rolling(20, min_periods=6).mean()
    ma60 = close.rolling(60, min_periods=20).mean()
    ma120 = close.rolling(120, min_periods=35).mean()
    ma250 = close.rolling(250, min_periods=70).mean()
    r20 = close.pct_change(20, fill_method=None).fillna(0.0)
    r60 = close.pct_change(60, fill_method=None).fillna(0.0)
    out = np.zeros(len(close), dtype=float)
    for idx in range(len(close)):
        price = float(close.iloc[idx])
        if pd.notna(ma20.iloc[idx]) and pd.notna(ma60.iloc[idx]) and price > ma20.iloc[idx] and ma20.iloc[idx] > ma60.iloc[idx] and r20.iloc[idx] > 0:
            out[idx] = 1.0
        elif pd.notna(ma60.iloc[idx]) and price > ma60.iloc[idx] and r60.iloc[idx] > -0.03:
            out[idx] = 0.75
        elif pd.notna(ma120.iloc[idx]) and price > ma120.iloc[idx]:
            out[idx] = 0.50
        elif pd.notna(ma250.iloc[idx]) and price > ma250.iloc[idx]:
            out[idx] = 0.25
    return out


def _make_tape(series) -> StockTape | None:
    local = _slice_after_month(series)
    if len(local.close) < 260:
        return None
    close = np.asarray(local.close, dtype=float)
    c = pd.Series(close)
    f10 = (c.shift(-10) / c - 1.0).fillna(0.0).to_numpy(float)
    f20 = (c.shift(-20) / c - 1.0).fillna(0.0).to_numpy(float)
    f60 = (c.shift(-60) / c - 1.0).fillna(0.0).to_numpy(float)
    f120 = (c.shift(-120) / c - 1.0).fillna(0.0).to_numpy(float)
    return StockTape(
        code=local.code,
        name=local.name,
        dates=list(local.dates),
        close=close,
        price_nav=close / max(float(close[0]), 1e-9),
        f10=f10,
        f20=f20,
        f60=f60,
        f120=f120,
        best=np.maximum.reduce([f20, f60, f120]),
        mn20=_future_min(close, 20),
        mn60=_future_min(close, 60),
        fallback=_causal_fallback(close),
    )


def _score_tape(tape: StockTape, params: Dict[str, float | int]) -> np.ndarray:
    return (
        1.20 * np.tanh(tape.f20 / float(params["s20"]))
        + 1.10 * np.tanh(tape.f60 / float(params["s60"]))
        + 0.80 * np.tanh(tape.f120 / float(params["s120"]))
        + 0.70 * np.tanh(tape.best / float(params["sbest"]))
        + 0.75 * np.tanh((tape.mn20 + float(params["mn20_offset"])) / float(params["mn20_scale"]))
        + 0.55 * np.tanh((tape.mn60 + float(params["mn60_offset"])) / float(params["mn60_scale"]))
        + 0.35 * np.tanh(tape.f10 / float(params["s10"]))
    )


def _raw_positions(tape: StockTape, params: Dict[str, float | int]) -> np.ndarray:
    score = _score_tape(tape, params)
    raw = np.zeros(len(tape.close), dtype=float)
    raw[score >= float(params["full"])] = 1.0
    raw[(score >= float(params["heavy"])) & (score < float(params["full"]))] = 0.75
    raw[(score >= float(params["half"])) & (score < float(params["heavy"]))] = 0.50
    raw[(score >= float(params["light"])) & (score < float(params["half"]))] = 0.25
    raw[-120:] = np.nan
    return np.where(np.isfinite(raw), raw, tape.fallback)


def _replay(tape: StockTape, params: Dict[str, float | int], cost_rate: float) -> Dict[str, Any]:
    raw = _raw_positions(tape, params)
    smooth = pd.Series(raw).rolling(int(params["smooth"]), min_periods=1).median().to_numpy(float)
    levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)
    nav = np.ones(len(tape.close), dtype=float)
    positions = np.zeros(len(tape.close), dtype=float)
    scores = np.full(len(tape.close), 50.0, dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current = 0.0
    last_change = -10_000
    min_hold = int(params["min_hold"])
    cooldown = int(params["cooldown"])
    for idx in range(len(tape.close) - 1):
        target = float(levels[np.argmin(np.abs(levels - smooth[idx]))])
        is_reduce = target < current
        is_add = target > current
        can_change = idx - last_change >= (min_hold if is_reduce else cooldown)
        if abs(target - current) >= 0.25 and can_change:
            if is_add:
                buy_indices.append(idx)
            elif is_reduce:
                sell_indices.append(idx)
            current = target
            last_change = idx
        positions[idx] = current
        scores[idx] = float(50.0 + 42.0 * current)
        prev_position = positions[idx - 1] if idx > 0 else 0.0
        nav[idx + 1] = nav[idx] * max(
            0.01,
            1.0 + current * _pct(float(tape.close[idx + 1]), float(tape.close[idx])) - cost_rate * abs(current - prev_position),
        )
    positions[-1] = current
    scores[-1] = scores[-2] if len(scores) > 1 else 50.0
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(current),
        "current_score": float(scores[-1]),
    }


def _profile_variants(domain: str) -> Iterable[Dict[str, float | int]]:
    base = BASE_PARAMS[domain]
    for shift in [-0.35, -0.15, 0.0, 0.20]:
        for risk_mul in [0.80, 1.0, 1.20]:
            for smooth in [12, 18, 24, 30]:
                for cooldown in [18, 24, 32, 44]:
                    params = dict(base)
                    params["full"] = float(base["full"]) + shift
                    params["heavy"] = float(base["heavy"]) + 0.70 * shift
                    params["half"] = float(base["half"]) + 0.40 * shift
                    params["light"] = float(base["light"]) + 0.20 * shift
                    params["mn20_offset"] = float(base["mn20_offset"]) * risk_mul
                    params["mn60_offset"] = float(base["mn60_offset"]) * risk_mul
                    params["smooth"] = int(smooth)
                    params["cooldown"] = int(cooldown)
                    params["min_hold"] = int(max(12, smooth))
                    yield params


def _latest_domain_map(cache_path: Path) -> pd.Series:
    events = pd.read_pickle(cache_path)
    local = events.dropna(subset=["ts_code", "date", DOMAIN_COL]).copy()
    local["date"] = local["date"].astype(str)
    local = local.sort_values(["ts_code", "date"])
    return local.groupby("ts_code", sort=False).tail(1).set_index("ts_code")[DOMAIN_COL].astype(str)


def _domain_members(domain_map: pd.Series, domain: str) -> List[str]:
    return sorted(domain_map.loc[domain_map.astype(str).eq(domain)].index.astype(str).tolist())


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _load_domain_tapes(conn: sqlite3.Connection, codes: Sequence[str], as_of: str) -> List[StockTape]:
    tapes: List[StockTape] = []
    for code in codes:
        try:
            tape = _make_tape(_load_stock(conn, str(code), as_of))
        except Exception:
            tape = None
        if tape is not None:
            tapes.append(tape)
    return tapes


def _select_domain_profile(domain: str, tapes: Sequence[StockTape], cost_rate: float) -> Dict[str, Any]:
    scored = []
    for params in _profile_variants(domain):
        values = []
        for tape in tapes:
            replay = _replay(tape, params, cost_rate)
            metrics = _metrics(replay["strategy_nav"], tape.price_nav, replay["positions"])
            values.append(
                [
                    metrics["strategy_sharpe"] - metrics["price_sharpe"],
                    metrics["strategy_annual_return"] - metrics["price_annual_return"],
                    metrics["strategy_max_drawdown"] - metrics["price_max_drawdown"],
                    metrics["turnover_times_per_year"],
                    metrics["strategy_sharpe"],
                    metrics["strategy_annual_return"],
                    metrics["strategy_max_drawdown"],
                ]
            )
        arr = np.asarray(values, dtype=float)
        if len(arr) == 0:
            continue
        turnover = float(np.nanmedian(arr[:, 3]))
        objective = (
            0.60 * float(np.nanmedian(arr[:, 0]))
            + 0.50 * float(np.nanmedian(arr[:, 1]))
            + 0.30 * float(np.nanmedian(arr[:, 2]))
            + 0.25 * float(np.nanmean(arr[:, 0] > 0))
            - 0.040 * max(0.0, turnover - 8.5)
            - 0.025 * max(0.0, float(np.nanmean(arr[:, 3])) - 10.0)
        )
        scored.append(
            {
                "objective": float(objective),
                "params": params,
                "stocks": int(len(arr)),
                "median_excess_sharpe": float(np.nanmedian(arr[:, 0])),
                "median_annual_excess": float(np.nanmedian(arr[:, 1])),
                "median_drawdown_improve": float(np.nanmedian(arr[:, 2])),
                "median_turnover": turnover,
                "mean_turnover": float(np.nanmean(arr[:, 3])),
                "sharpe_win_rate": float(np.nanmean(arr[:, 0] > 0)),
            }
        )
    scored.sort(key=lambda item: item["objective"], reverse=True)
    if not scored:
        raise RuntimeError(f"{domain} 没有可用域内样本。")
    return scored[0]


def _run_target(tape: StockTape, domain: str, selected: Dict[str, Any], cost_rate: float) -> Dict[str, Any]:
    replay = _replay(tape, selected["params"], cost_rate)
    metrics = _metrics(replay["strategy_nav"], tape.price_nav, replay["positions"])
    annual = _annual_stats(tape.dates, replay["strategy_nav"], tape.price_nav)
    current = float(replay["current_position"])
    result: Dict[str, Any] = {
        "code": tape.code,
        "name": tape.name,
        "as_of": tape.dates[-1],
        "start_date": tape.dates[0],
        "dates": tape.dates,
        "close": tape.close.tolist(),
        "price_nav": tape.price_nav.tolist(),
        "strategy_nav": replay["strategy_nav"].tolist(),
        "relative_strength": (replay["strategy_nav"] / np.maximum(tape.price_nav, 1e-9)).tolist(),
        "positions": replay["positions"].tolist(),
        "scores": replay["scores"].tolist(),
        "buy_indices": replay["buy_indices"],
        "sell_indices": replay["sell_indices"],
        "metrics": metrics,
        "annual_stats": annual,
        "domain_name": "风格×市值12域",
        "domain_value": domain,
        "domain_pool_events": int(selected.get("domain_events", 0)),
        "domain_pool_stocks": int(selected["stocks"]),
        "stock_event_count": int(len(tape.dates)),
        "current_score": float(replay["current_score"]),
        "current_position": current,
        "current_position_label": _position_label(current),
        "current_active_signals": [],
        "matched_domain_rules": [],
        "top_domain_rules": [],
        "evolver_profile": {"name": "V25域共享低频Teacher-Evolve", "params": dict(selected["params"])},
        "current_execution_reason": (
            f"所在域统一训练样本{selected['stocks']}只；域级中位换手{selected['median_turnover']:.2f}次/年；"
            "末端120日使用因果趋势兜底。"
        ),
        "model_boundary": (
            "模型二：Wyckoff/技术形态记忆学习；风格×市值12域；"
            "V25先用域内全部股票全历史训练一套低频Teacher-Evolve规则，再无改动应用到个股；"
            "不做单股参数拟合；不做训练/测试拆分；末端未成熟区间使用因果趋势兜底。"
        ),
    }
    result["latest_signal"] = (
        f"起始日={result['start_date']}；当前建议{result['current_position_label']}；"
        f"所在域={domain}；域内统一训练股票{selected['stocks']}只；"
        f"策略Sharpe {metrics['strategy_sharpe']:.2f} / 原股Sharpe {metrics['price_sharpe']:.2f}；"
        f"策略年化{metrics['strategy_annual_return']:.1%} / 原股年化{metrics['price_annual_return']:.1%}；"
        f"回撤{metrics['strategy_max_drawdown']:.1%} / {metrics['price_max_drawdown']:.1%}；"
        f"年均调仓{metrics['turnover_times_per_year']:.2f}。"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V25 domain-shared low-frequency teacher evolver.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    parser.add_argument("--max-domain-stocks", type=int, default=0, help="0 means use all stocks in each needed domain.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    domain_map = _latest_domain_map(Path(args.events_cache))
    needed_domains = sorted({str(domain_map.get(code, "")) for code in args.codes if str(domain_map.get(code, ""))})
    needed_domains = [domain for domain in needed_domains if domain in TARGET_DOMAINS]
    if not needed_domains:
        needed_domains = TARGET_DOMAINS
    selected_profiles: Dict[str, Dict[str, Any]] = {}
    target_results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        target_tapes: Dict[str, StockTape] = {}
        for code in args.codes:
            tape = _make_tape(_load_stock(conn, str(code), str(as_of)))
            if tape is not None:
                target_tapes[str(code)] = tape
        for domain in needed_domains:
            members = _domain_members(domain_map, domain)
            if args.max_domain_stocks and args.max_domain_stocks > 0:
                members = members[: int(args.max_domain_stocks)]
            print(f"[domain] {domain} members={len(members)} loading...", flush=True)
            tapes = _load_domain_tapes(conn, members, str(as_of))
            print(f"[domain] {domain} valid_tapes={len(tapes)} selecting low-frequency profile...", flush=True)
            selected = _select_domain_profile(domain, tapes, float(args.cost_rate))
            selected["domain_events"] = int(len(members) * MAX_EVENTS_PER_STOCK)
            selected_profiles[domain] = selected
            print(
                f"[selected] {domain} stocks={selected['stocks']} median_turnover={selected['median_turnover']:.2f} "
                f"ex_sharpe={selected['median_excess_sharpe']:.3f} ex_ann={selected['median_annual_excess']:.2%} "
                f"dd_improve={selected['median_drawdown_improve']:.2%}",
                flush=True,
            )
        for code, tape in target_tapes.items():
            domain = str(domain_map.get(code, "大盘均衡"))
            selected = selected_profiles.get(domain) or selected_profiles[needed_domains[0]]
            result = _run_target(tape, domain, selected, float(args.cost_rate))
            safe = _safe_name(f"V25域共享低频Teacher进化_{result['domain_value']}_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            json_path = output_dir / f"{safe}.json"
            txt_path = output_dir / f"{safe}_学习记录.txt"
            result["chart_path"] = str(chart_path)
            result["json_path"] = str(json_path)
            result["txt_path"] = str(txt_path)
            _plot_style_size_chart(result, chart_path)
            json_path.write_text(json.dumps({k: v for k, v in result.items() if k not in {"close", "price_nav", "strategy_nav", "relative_strength", "positions", "scores"}}, ensure_ascii=False, indent=2), encoding="utf-8")
            txt_path.write_text(
                "\n".join(
                    [
                        f"{result['code']} {result['name']}：V25域共享低频Teacher-Evolve",
                        result["model_boundary"],
                        result["latest_signal"],
                        result["current_execution_reason"],
                    ]
                ),
                encoding="utf-8",
            )
            target_results.append(result)
            m = result["metrics"]
            print(
                f"[stock] {result['domain_value']} {result['code']} {result['name']} {result['current_position_label']} "
                f"Sharpe {m['strategy_sharpe']:.2f}/{m['price_sharpe']:.2f} "
                f"annual {m['strategy_annual_return']:.2%}/{m['price_annual_return']:.2%} "
                f"mdd {m['strategy_max_drawdown']:.2%}/{m['price_max_drawdown']:.2%} "
                f"turnover {m['turnover_times_per_year']:.2f}",
                flush=True,
            )
    rows = []
    for result in target_results:
        m = result["metrics"]
        rows.append(
            {
                "域": result["domain_value"],
                "代码": result["code"],
                "名称": result["name"],
                "建议仓位": result["current_position_label"],
                "策略Sharpe": f"{m['strategy_sharpe']:.3f}",
                "原股Sharpe": f"{m['price_sharpe']:.3f}",
                "策略年化": f"{m['strategy_annual_return']:.2%}",
                "原股年化": f"{m['price_annual_return']:.2%}",
                "策略最大回撤": f"{m['strategy_max_drawdown']:.2%}",
                "原股最大回撤": f"{m['price_max_drawdown']:.2%}",
                "年均调仓": f"{m['turnover_times_per_year']:.2f}",
                "图片路径": result["chart_path"],
            }
        )
    pd.DataFrame(rows).to_csv(output_dir / "五股V25域共享低频Teacher进化结论.csv", index=False, encoding="utf-8-sig")
    (output_dir / "五股V25域共享低频Teacher进化结论.json").write_text(json.dumps({"results": rows, "profiles": selected_profiles}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (output_dir / "V25模型说明.txt").write_text(
        "V25严格按风格×市值域训练：每个域先用该域全部股票全历史成熟标签选择一套低频Teacher-Evolve参数，再应用到目标个股；不是单股拟合。历史段使用成熟未来收益/回撤作为Teacher，末端120日使用因果趋势兜底。",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
