"""V26 style-size domain shared Wyckoff/event-memory evolver.

This script keeps the user's requested framework:
- one style x size domain trains one shared rulebook from every stock in that
  domain;
- the rulebook is learned from the expanded K-line / Wyckoff event memory pool
  (rule_id, direction, strength, signed future outcome), not from per-stock
  parameter fitting;
- one low-frequency five-level executor is selected at the domain level and is
  applied unchanged to each target stock;
- net value and signals start after the first listed month.

The user explicitly asked for full-history in-sample research without a
train/test split.  Historical event labels are therefore used to evolve the
domain rulebook and executor.  The executable signal itself only uses same-day
event triggers plus causal price/volume context.
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
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v13_domain_champion_executor import _slice_after_month  # noqa: E402
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import DOMAIN_COL, _plot_style_size_chart  # noqa: E402


DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
DEFAULT_CACHE = Path("agent/output/kline_memory_learning/expanded_pattern_events_v9_compact.pkl")
DEFAULT_OUTPUT_DIR = Path("agent/output/kline_memory_learning/v26_event_memory_evolver")


@dataclass
class StockTape:
    code: str
    name: str
    dates: List[str]
    date_keys: List[str]
    close: np.ndarray
    price_nav: np.ndarray
    event_pressure: np.ndarray
    trend_context: np.ndarray
    risk_context: np.ndarray


def _date_key(value: Any) -> str:
    return str(value).replace("-", "").replace("/", "")[:8]


def _load_events(cache_path: Path) -> pd.DataFrame:
    events = pd.read_pickle(cache_path)
    required = ["ts_code", "date", DOMAIN_COL, "rule_id", "direction", "strength", "signed_return"]
    missing = [col for col in required if col not in events.columns]
    if missing:
        raise RuntimeError(f"events cache missing columns: {missing}")
    out = events[required + ["stock_name"]].copy() if "stock_name" in events.columns else events[required].copy()
    out = out.dropna(subset=["ts_code", "date", DOMAIN_COL, "rule_id", "direction", "signed_return"])
    out["ts_code"] = out["ts_code"].astype(str)
    out["date_key"] = out["date"].map(_date_key)
    out[DOMAIN_COL] = out[DOMAIN_COL].astype(str)
    out["rule_id"] = out["rule_id"].astype(str)
    out["direction"] = pd.to_numeric(out["direction"], errors="coerce").fillna(0.0).astype(float)
    out["strength"] = pd.to_numeric(out["strength"], errors="coerce").fillna(1.0).clip(0.1, 2.0)
    out["signed_return"] = pd.to_numeric(out["signed_return"], errors="coerce")
    out = out.dropna(subset=["signed_return"])
    return out


def _latest_domain_map(events: pd.DataFrame) -> pd.Series:
    local = events[["ts_code", "date_key", DOMAIN_COL]].dropna().sort_values(["ts_code", "date_key"])
    return local.groupby("ts_code", sort=False).tail(1).set_index("ts_code")[DOMAIN_COL].astype(str)


def _learn_rulebook(domain_events: pd.DataFrame, max_rules: int = 120) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    total_stocks = max(int(domain_events["ts_code"].nunique()), 1)
    for rule_id, g in domain_events.groupby("rule_id", sort=False):
        n = int(len(g))
        breadth = int(g["ts_code"].nunique())
        if n < 80 or breadth < max(10, int(total_stocks * 0.04)):
            continue
        signed = g["signed_return"].astype(float).clip(-0.35, 0.35).to_numpy(float)
        direction = float(np.sign(g["direction"].median()))
        if direction == 0.0:
            continue
        mean_edge = float(np.nanmean(signed))
        hit_rate = float(np.nanmean(signed > 0.0))
        std = float(np.nanstd(signed))
        n_eff = min(n, breadth * 12)
        t_stat = mean_edge / max(std, 1e-6) * math.sqrt(max(n_eff, 1))
        payoff = float(np.nanmean(signed[signed > 0.0])) / max(abs(float(np.nanmean(signed[signed < 0.0]))), 1e-6) if np.any(signed < 0.0) else 3.0
        if mean_edge <= 0.0004 or hit_rate <= 0.505 or t_stat <= 0.75:
            continue
        shrink = n_eff / (n_eff + 420.0)
        breadth_scale = math.sqrt(min(1.0, breadth / max(total_stocks * 0.40, 1.0)))
        quality = (
            1.15 * math.tanh(mean_edge * 22.0)
            + 0.90 * math.tanh((hit_rate - 0.50) * 5.0)
            + 0.35 * math.tanh((payoff - 1.0) * 0.8)
            + 0.25 * math.tanh(t_stat / 3.0)
        )
        weight = float(direction * shrink * breadth_scale * quality)
        rows.append(
            {
                "rule_id": str(rule_id),
                "direction": direction,
                "weight": weight,
                "n_events": n,
                "n_stocks": breadth,
                "mean_signed_return": mean_edge,
                "hit_rate": hit_rate,
                "t_stat": float(t_stat),
                "payoff": payoff,
            }
        )
    rulebook = pd.DataFrame(rows)
    if rulebook.empty:
        raise RuntimeError("domain rulebook is empty")
    rulebook["abs_weight"] = rulebook["weight"].abs()
    bullish = rulebook[rulebook["weight"] > 0].sort_values("abs_weight", ascending=False).head(max_rules // 2)
    bearish = rulebook[rulebook["weight"] < 0].sort_values("abs_weight", ascending=False).head(max_rules // 2)
    selected = pd.concat([bullish, bearish], ignore_index=True).sort_values("abs_weight", ascending=False)
    return selected.drop(columns=["abs_weight"]).reset_index(drop=True)


def _trend_context(close_values: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    close = pd.Series(np.asarray(close_values, dtype=float))
    ma10 = close.rolling(10, min_periods=5).mean()
    ma20 = close.rolling(20, min_periods=8).mean()
    ma60 = close.rolling(60, min_periods=20).mean()
    ma120 = close.rolling(120, min_periods=40).mean()
    ma250 = close.rolling(250, min_periods=80).mean()
    r10 = close.pct_change(10, fill_method=None).fillna(0.0)
    r20 = close.pct_change(20, fill_method=None).fillna(0.0)
    r60 = close.pct_change(60, fill_method=None).fillna(0.0)
    high20 = close.rolling(20, min_periods=5).max()
    high60 = close.rolling(60, min_periods=20).max()
    dd20 = close / high20 - 1.0
    dd60 = close / high60 - 1.0
    trend = np.zeros(len(close), dtype=float)
    risk = np.zeros(len(close), dtype=float)
    for i in range(len(close)):
        price = float(close.iloc[i])
        items: List[float] = []
        if pd.notna(ma20.iloc[i]) and ma20.iloc[i] > 0:
            items.append(math.tanh((price / ma20.iloc[i] - 1.0) * 10.0))
        if pd.notna(ma60.iloc[i]) and ma60.iloc[i] > 0:
            items.append(math.tanh((price / ma60.iloc[i] - 1.0) * 8.0))
        if pd.notna(ma120.iloc[i]) and ma120.iloc[i] > 0:
            items.append(math.tanh((price / ma120.iloc[i] - 1.0) * 6.0))
        if pd.notna(ma250.iloc[i]) and ma250.iloc[i] > 0:
            items.append(math.tanh((price / ma250.iloc[i] - 1.0) * 5.0))
        if pd.notna(ma10.iloc[i]) and pd.notna(ma20.iloc[i]) and pd.notna(ma60.iloc[i]):
            items.append(0.55 if ma10.iloc[i] > ma20.iloc[i] > ma60.iloc[i] else -0.35)
        items.extend([math.tanh(float(r10.iloc[i]) * 7.0), math.tanh(float(r20.iloc[i]) * 5.0), math.tanh(float(r60.iloc[i]) * 3.0)])
        trend[i] = float(np.nanmean(items)) if items else 0.0
        risk_items = [
            -math.tanh(abs(float(dd20.iloc[i])) * 8.0) if pd.notna(dd20.iloc[i]) and dd20.iloc[i] < -0.04 else 0.0,
            -math.tanh(abs(float(dd60.iloc[i])) * 6.0) if pd.notna(dd60.iloc[i]) and dd60.iloc[i] < -0.08 else 0.0,
        ]
        if pd.notna(ma20.iloc[i]) and price < ma20.iloc[i]:
            risk_items.append(-0.25)
        if pd.notna(ma60.iloc[i]) and price < ma60.iloc[i]:
            risk_items.append(-0.45)
        risk[i] = float(np.nansum(risk_items))
    return np.nan_to_num(trend, nan=0.0), np.nan_to_num(risk, nan=0.0)


def _make_stock_tape(series: Any, stock_events: pd.DataFrame, rulebook: pd.DataFrame) -> StockTape | None:
    local = _slice_after_month(series)
    if len(local.close) < 260:
        return None
    close = np.asarray(local.close, dtype=float)
    date_keys = [_date_key(d) for d in local.dates]
    rule_weights = rulebook.set_index("rule_id")["weight"]
    ev = stock_events[stock_events["rule_id"].isin(rule_weights.index)].copy()
    pressure = pd.Series(0.0, index=pd.Index(date_keys, name="date_key"))
    if not ev.empty:
        ev["weighted"] = ev["rule_id"].map(rule_weights).astype(float) * ev["strength"].astype(float)
        daily = ev.groupby("date_key")["weighted"].sum()
        pressure = pressure.add(daily, fill_value=0.0).reindex(date_keys).fillna(0.0)
    trend, risk = _trend_context(close)
    return StockTape(
        code=local.code,
        name=local.name,
        dates=list(local.dates),
        date_keys=date_keys,
        close=close,
        price_nav=close / max(float(close[0]), 1e-9),
        event_pressure=pressure.to_numpy(float),
        trend_context=trend,
        risk_context=risk,
    )


def _quantize(raw: np.ndarray, params: Dict[str, float | int]) -> np.ndarray:
    levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)
    target = np.zeros(len(raw), dtype=float)
    target[raw >= float(params["light"])] = 0.25
    target[raw >= float(params["half"])] = 0.50
    target[raw >= float(params["heavy"])] = 0.75
    target[raw >= float(params["full"])] = 1.0
    return levels[np.argmin(np.abs(target[:, None] - levels[None, :]), axis=1)]


def _execute(tape: StockTape, params: Dict[str, float | int], cost_rate: float) -> Dict[str, Any]:
    pressure = pd.Series(tape.event_pressure).ewm(span=int(params["memory_span"]), adjust=False).mean().to_numpy(float)
    scale = float(params.get("pressure_scale", 1.0))
    pressure_norm = np.tanh(pressure / max(scale, 1e-6))
    raw_score = (
        float(params["memory_weight"]) * pressure_norm
        + float(params["trend_weight"]) * tape.trend_context
        + float(params["risk_weight"]) * tape.risk_context
    )
    smooth = pd.Series(raw_score).rolling(int(params["smooth"]), min_periods=1).median().to_numpy(float)
    raw_target = _quantize(smooth, params)
    nav = np.ones(len(tape.close), dtype=float)
    positions = np.zeros(len(tape.close), dtype=float)
    scores = np.full(len(tape.close), 50.0, dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current = 0.0
    last_change = -10_000
    min_hold = int(params["min_hold"])
    cooldown = int(params["cooldown"])
    confirm = int(params["confirm"])
    for idx in range(len(tape.close) - 1):
        target = float(raw_target[idx])
        if idx >= confirm - 1:
            window = raw_target[idx - confirm + 1 : idx + 1]
            target = float(np.median(window))
        is_reduce = target < current
        is_add = target > current
        needed_gap = 0.25 if is_reduce else float(params["add_step"])
        wait = min_hold if is_reduce else cooldown
        can_change = idx - last_change >= wait
        if abs(target - current) >= needed_gap and can_change:
            if is_add:
                buy_indices.append(idx)
            elif is_reduce:
                sell_indices.append(idx)
            current = target
            last_change = idx
        positions[idx] = current
        scores[idx] = float(np.clip(50.0 + 35.0 * smooth[idx] + 12.0 * current, 0.0, 100.0))
        prev_position = positions[idx - 1] if idx > 0 else 0.0
        nav[idx + 1] = nav[idx] * max(
            0.01,
            1.0
            + current * _pct(float(tape.close[idx + 1]), float(tape.close[idx]))
            - cost_rate * abs(current - prev_position),
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
        "raw_score": raw_score,
    }


def _profile_variants(pressure_scale: float) -> Iterable[Dict[str, float | int]]:
    base: Dict[str, float | int] = {
        "pressure_scale": max(pressure_scale, 0.20),
        "trend_weight": 0.92,
        "risk_weight": 0.72,
    }
    for memory_weight in [0.65, 0.85]:
        for memory_span in [16, 28]:
            for smooth in [22, 34]:
                for min_hold in [35, 55]:
                    for threshold_shift in [-0.05, 0.10, 0.25]:
                        params = dict(base)
                        params.update(
                            {
                                "memory_weight": memory_weight,
                                "memory_span": memory_span,
                                "smooth": smooth,
                                "confirm": 3,
                                "cooldown": max(16, int(min_hold * 0.55)),
                                "min_hold": min_hold,
                                "add_step": 0.25,
                                "light": -0.24 + threshold_shift * 0.4,
                                "half": 0.06 + threshold_shift * 0.6,
                                "heavy": 0.36 + threshold_shift * 0.8,
                                "full": 0.66 + threshold_shift,
                            }
                        )
                        yield params


def _select_domain_executor(tapes: Sequence[StockTape], cost_rate: float) -> Dict[str, Any]:
    all_pressures = np.concatenate([np.asarray(t.event_pressure, dtype=float) for t in tapes if len(t.event_pressure)])
    scale = float(np.nanpercentile(np.abs(all_pressures), 85)) if len(all_pressures) else 1.0
    scored: List[Dict[str, Any]] = []
    for params in _profile_variants(scale):
        rows: List[List[float]] = []
        for tape in tapes:
            replay = _execute(tape, params, cost_rate)
            metrics = _metrics(replay["strategy_nav"], tape.price_nav, replay["positions"])
            rows.append(
                [
                    metrics["strategy_sharpe"] - metrics["price_sharpe"],
                    metrics["strategy_annual_return"] - metrics["price_annual_return"],
                    metrics["strategy_max_drawdown"] - metrics["price_max_drawdown"],
                    metrics["turnover_times_per_year"],
                    metrics["strategy_sharpe"],
                    metrics["strategy_annual_return"],
                ]
            )
        arr = np.asarray(rows, dtype=float)
        if len(arr) == 0:
            continue
        med_turnover = float(np.nanmedian(arr[:, 3]))
        mean_turnover = float(np.nanmean(arr[:, 3]))
        objective = (
            0.72 * float(np.nanmedian(arr[:, 0]))
            + 0.48 * float(np.nanmedian(arr[:, 1]))
            + 0.36 * float(np.nanmedian(arr[:, 2]))
            + 0.30 * float(np.nanmean(arr[:, 0] > 0.0))
            + 0.16 * float(np.nanmean(arr[:, 1] > 0.0))
            - 0.075 * max(0.0, med_turnover - 6.5)
            - 0.045 * max(0.0, mean_turnover - 7.5)
            - 0.020 * max(0.0, 2.0 - med_turnover)
        )
        scored.append(
            {
                "objective": float(objective),
                "params": params,
                "stocks": int(len(arr)),
                "median_excess_sharpe": float(np.nanmedian(arr[:, 0])),
                "median_annual_excess": float(np.nanmedian(arr[:, 1])),
                "median_drawdown_improve": float(np.nanmedian(arr[:, 2])),
                "median_turnover": med_turnover,
                "mean_turnover": mean_turnover,
                "sharpe_win_rate": float(np.nanmean(arr[:, 0] > 0.0)),
            }
        )
    scored.sort(key=lambda x: x["objective"], reverse=True)
    if not scored:
        raise RuntimeError("no executable domain profile")
    return scored[0]


def _build_target_result(
    tape: StockTape,
    domain: str,
    rulebook: pd.DataFrame,
    selected: Dict[str, Any],
    cost_rate: float,
    output_dir: Path,
) -> Dict[str, Any]:
    replay = _execute(tape, selected["params"], cost_rate)
    metrics = _metrics(replay["strategy_nav"], tape.price_nav, replay["positions"])
    annual = _annual_stats(tape.dates, replay["strategy_nav"], tape.price_nav)
    top_rules = rulebook.head(18).to_dict("records")
    active_pressure = float(tape.event_pressure[-1]) if len(tape.event_pressure) else 0.0
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
        "domain_name": "style_size_12",
        "domain_value": domain,
        "domain_pool_stocks": int(selected["stocks"]),
        "domain_pool_events": int(rulebook["n_events"].sum()),
        "stock_event_count": int(np.count_nonzero(np.abs(tape.event_pressure) > 0.0)),
        "current_score": float(replay["current_score"]),
        "current_position": float(replay["current_position"]),
        "current_position_label": _position_label(float(replay["current_position"])),
        "current_active_signals": [],
        "matched_domain_rules": top_rules[:8],
        "top_domain_rules": top_rules,
        "evolver_profile": {
            "name": "V26_domain_event_memory_low_frequency_executor",
            "params": dict(selected["params"]),
        },
    }
    result["latest_signal"] = (
        f"{tape.code} {tape.name}: domain={domain}, position={result['current_position_label']}, "
        f"score={result['current_score']:.1f}, active_pressure={active_pressure:.3f}, "
        f"strategy_sharpe={metrics['strategy_sharpe']:.2f}, price_sharpe={metrics['price_sharpe']:.2f}, "
        f"turnover={metrics['turnover_times_per_year']:.2f}/year"
    )
    result["model_boundary"] = (
        "V26 uses one shared style-size domain rulebook learned from all stocks in that domain. "
        "Each rule is a historical K-line/Wyckoff event rule_id with domain-level edge, hit rate, breadth and shrinkage. "
        "The target stock does not learn its own parameters; it only executes the shared domain rulebook."
    )
    safe = _safe_name(f"V26_domain_event_memory_{domain}_{tape.code}_{tape.name}")
    chart_path = output_dir / f"{safe}_trade_nav_relative.png"
    result["chart_path"] = str(chart_path)
    _plot_style_size_chart(result, chart_path)
    payload = {
        key: value
        for key, value in result.items()
        if key not in {"close", "price_nav", "strategy_nav", "relative_strength", "positions", "scores"}
    }
    (output_dir / f"{safe}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        result["latest_signal"],
        result["model_boundary"],
        "top learned domain rules:",
    ]
    for row in top_rules[:20]:
        lines.append(
            f"- {row['rule_id']} weight={row['weight']:.4f} hit={row['hit_rate']:.2%} "
            f"edge={row['mean_signed_return']:.3%} breadth={int(row['n_stocks'])}"
        )
    (output_dir / f"{safe}_memory_notes.txt").write_text("\n".join(lines), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V26 domain event-memory evolver.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    parser.add_argument("--max-domain-stocks", type=int, default=0, help="0 means all domain stocks.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _load_events(Path(args.events_cache))
    domain_map = _latest_domain_map(events)
    target_codes = [str(code) for code in args.codes]
    needed_domains = sorted({str(domain_map.get(code, "")) for code in target_codes if str(domain_map.get(code, ""))})
    if not needed_domains:
        raise RuntimeError("target stocks have no style-size domain in event cache")

    profiles: Dict[str, Dict[str, Any]] = {}
    rulebooks: Dict[str, pd.DataFrame] = {}
    target_results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for domain in needed_domains:
            domain_events = events[events[DOMAIN_COL].eq(domain)].copy()
            members = sorted(domain_events["ts_code"].dropna().astype(str).unique().tolist())
            if args.max_domain_stocks and args.max_domain_stocks > 0:
                members = members[: int(args.max_domain_stocks)]
                domain_events = domain_events[domain_events["ts_code"].isin(members)].copy()
            print(f"[domain] {domain} stocks={len(members)} events={len(domain_events)} learning rulebook...", flush=True)
            rulebook = _learn_rulebook(domain_events)
            rulebooks[domain] = rulebook
            tapes: List[StockTape] = []
            for code in members:
                try:
                    tape = _make_stock_tape(
                        _load_stock(conn, code, str(as_of)),
                        domain_events[domain_events["ts_code"].eq(code)],
                        rulebook,
                    )
                except Exception:
                    tape = None
                if tape is not None:
                    tapes.append(tape)
            print(f"[domain] {domain} valid_tapes={len(tapes)} selected_rules={len(rulebook)} selecting executor...", flush=True)
            selected = _select_domain_executor(tapes, float(args.cost_rate))
            profiles[domain] = selected
            rulebook.to_csv(output_dir / f"V26_rulebook_{_safe_name(domain)}.csv", index=False, encoding="utf-8-sig")
            print(
                f"[selected] {domain} stocks={selected['stocks']} rules={len(rulebook)} "
                f"ex_sharpe={selected['median_excess_sharpe']:.3f} ex_ann={selected['median_annual_excess']:.2%} "
                f"dd={selected['median_drawdown_improve']:.2%} turnover={selected['median_turnover']:.2f}",
                flush=True,
            )

        for code in target_codes:
            domain = str(domain_map.get(code, ""))
            if domain not in rulebooks:
                print(f"[skip] {code} no learned domain profile", flush=True)
                continue
            stock_events = events[(events["ts_code"].eq(code)) & (events[DOMAIN_COL].eq(domain))].copy()
            tape = _make_stock_tape(_load_stock(conn, code, str(as_of)), stock_events, rulebooks[domain])
            if tape is None:
                print(f"[skip] {code} insufficient stock history", flush=True)
                continue
            result = _build_target_result(
                tape=tape,
                domain=domain,
                rulebook=rulebooks[domain],
                selected=profiles[domain],
                cost_rate=float(args.cost_rate),
                output_dir=output_dir,
            )
            target_results.append(result)
            m = result["metrics"]
            print(
                f"[stock] {domain} {result['code']} {result['name']} {result['current_position_label']} "
                f"Sharpe {m['strategy_sharpe']:.2f}/{m['price_sharpe']:.2f} "
                f"annual {m['strategy_annual_return']:.2%}/{m['price_annual_return']:.2%} "
                f"mdd {m['strategy_max_drawdown']:.2%}/{m['price_max_drawdown']:.2%} "
                f"turnover {m['turnover_times_per_year']:.2f}",
                flush=True,
            )

    rows: List[Dict[str, Any]] = []
    for result in target_results:
        m = result["metrics"]
        rows.append(
            {
                "domain": result["domain_value"],
                "code": result["code"],
                "name": result["name"],
                "current_position": result["current_position_label"],
                "strategy_sharpe": round(float(m["strategy_sharpe"]), 4),
                "price_sharpe": round(float(m["price_sharpe"]), 4),
                "strategy_annual_return": round(float(m["strategy_annual_return"]), 6),
                "price_annual_return": round(float(m["price_annual_return"]), 6),
                "strategy_max_drawdown": round(float(m["strategy_max_drawdown"]), 6),
                "price_max_drawdown": round(float(m["price_max_drawdown"]), 6),
                "turnover_per_year": round(float(m["turnover_times_per_year"]), 4),
                "chart_path": result["chart_path"],
            }
        )
    pd.DataFrame(rows).to_csv(output_dir / "V26_five_stock_domain_event_memory_summary.csv", index=False, encoding="utf-8-sig")
    (output_dir / "V26_five_stock_domain_event_memory_summary.json").write_text(
        json.dumps({"results": rows, "profiles": profiles}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[done] output_dir={output_dir}", flush=True)


if __name__ == "__main__":
    main()
