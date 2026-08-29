"""V27 domain-shared Wyckoff memory teacher with low-frequency execution.

Compared with V26, this restores the original Predict/Critic/Reflect/Evolve
spirit more directly:
- Reflect: learn a domain rulebook from all K-line/Wyckoff event memories.
- Critic/Teacher: on the full in-sample history requested by the user, learn one
  domain-level teacher profile that rewards large forward upside and penalizes
  forward drawdown.
- Evolve: select one low-frequency five-level executor per style-size domain.
- Predict: apply the selected domain rulebook/profile unchanged to each stock.

This is an in-sample research/evolution run, not an out-of-sample performance
claim.  The executable current tail uses causal trend fallback because future
outcomes are not mature there.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
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
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v25_domain_teacher_evolver_lowfreq import (  # noqa: E402
    StockTape,
    _make_tape,
    _score_tape,
)
from model.kline_memory_learning.run_wyckoff_style_size_domain_rulebook_v26_event_memory_evolver import (  # noqa: E402
    DEFAULT_CACHE,
    DEFAULT_CODES,
    _date_key,
    _latest_domain_map,
    _learn_rulebook,
    _load_events,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import DOMAIN_COL, _plot_style_size_chart  # noqa: E402


DEFAULT_OUTPUT_DIR = Path("agent/output/kline_memory_learning/v27_shared_teacher_lowfreq")


def _domain_members(events: pd.DataFrame, domain: str) -> List[str]:
    return sorted(events.loc[events[DOMAIN_COL].eq(domain), "ts_code"].dropna().astype(str).unique().tolist())


def _event_pressure_for_tape(tape: StockTape, stock_events: pd.DataFrame, rulebook: pd.DataFrame) -> np.ndarray:
    date_keys = [_date_key(d) for d in tape.dates]
    pressure = pd.Series(0.0, index=pd.Index(date_keys, name="date_key"))
    if stock_events.empty or rulebook.empty:
        return pressure.to_numpy(float)
    weights = rulebook.set_index("rule_id")["weight"]
    ev = stock_events[stock_events["rule_id"].isin(weights.index)].copy()
    if ev.empty:
        return pressure.to_numpy(float)
    ev["date_key"] = ev["date"].map(_date_key) if "date" in ev.columns else ev["date_key"].map(_date_key)
    ev["weighted"] = ev["rule_id"].map(weights).astype(float) * ev["strength"].astype(float)
    daily = ev.groupby("date_key")["weighted"].sum()
    return pressure.add(daily, fill_value=0.0).reindex(date_keys).fillna(0.0).to_numpy(float)


def _domain_base_from_tape(domain: str) -> Dict[str, float]:
    # Avoid hard-coding garbled Chinese domain labels by using broad defaults.
    if "成长" in domain or "ɳ" in domain:
        return dict(s10=0.055, s20=0.105, s60=0.24, s120=0.38, sbest=0.22, mn20_offset=0.060, mn20_scale=0.10, mn60_offset=0.125, mn60_scale=0.17, full=2.00, heavy=1.10, half=0.25, light=-0.55)
    if "价值" in domain or "ֵ" in domain:
        return dict(s10=0.035, s20=0.070, s60=0.15, s120=0.25, sbest=0.15, mn20_offset=0.045, mn20_scale=0.07, mn60_offset=0.090, mn60_scale=0.12, full=1.55, heavy=0.72, half=0.02, light=-0.75)
    return dict(s10=0.040, s20=0.085, s60=0.18, s120=0.30, sbest=0.18, mn20_offset=0.050, mn20_scale=0.08, mn60_offset=0.095, mn60_scale=0.14, full=1.80, heavy=0.92, half=0.12, light=-0.68)


def _profile_variants(domain: str, pressure_scale: float) -> Iterable[Dict[str, float | int]]:
    base = _domain_base_from_tape(domain)
    for shift in [-0.10, 0.10]:
        for smooth in [30, 45]:
            for min_hold in [50, 75]:
                params: Dict[str, float | int] = dict(base)
                params["full"] = float(base["full"]) + shift
                params["heavy"] = float(base["heavy"]) + 0.65 * shift
                params["half"] = float(base["half"]) + 0.35 * shift
                params["light"] = float(base["light"]) + 0.20 * shift
                params["smooth"] = int(smooth)
                params["min_hold"] = int(min_hold)
                params["cooldown"] = int(max(24, min_hold * 0.45))
                params["confirm"] = 3
                params["event_weight"] = 0.15
                params["tail_event_weight"] = 0.35
                params["pressure_scale"] = max(float(pressure_scale), 0.10)
                yield params


def _raw_target(tape: StockTape, event_pressure: np.ndarray, params: Dict[str, float | int]) -> np.ndarray:
    levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.0], dtype=float)
    pressure = pd.Series(event_pressure).ewm(span=18, adjust=False).mean().to_numpy(float)
    pressure_norm = np.tanh(pressure / max(float(params["pressure_scale"]), 1e-6))
    teacher_score = _score_tape(tape, params) + float(params["event_weight"]) * pressure_norm
    target = np.zeros(len(tape.close), dtype=float)
    target[teacher_score >= float(params["light"])] = 0.25
    target[teacher_score >= float(params["half"])] = 0.50
    target[teacher_score >= float(params["heavy"])] = 0.75
    target[teacher_score >= float(params["full"])] = 1.0
    tail_score = (2.0 * tape.fallback - 1.0) + float(params["tail_event_weight"]) * pressure_norm
    tail_target = np.zeros(len(tape.close), dtype=float)
    tail_target[tail_score >= -0.20] = 0.25
    tail_target[tail_score >= 0.10] = 0.50
    tail_target[tail_score >= 0.45] = 0.75
    tail_target[tail_score >= 0.75] = 1.0
    tail = min(120, len(target))
    target[-tail:] = tail_target[-tail:]
    return levels[np.argmin(np.abs(target[:, None] - levels[None, :]), axis=1)]


def _replay(tape: StockTape, event_pressure: np.ndarray, params: Dict[str, float | int], cost_rate: float) -> Dict[str, Any]:
    raw = _raw_target(tape, event_pressure, params)
    smooth = pd.Series(raw).rolling(int(params["smooth"]), min_periods=1).median().to_numpy(float)
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
        target = float(smooth[idx])
        if idx >= confirm - 1:
            target = float(np.median(smooth[idx - confirm + 1 : idx + 1]))
        is_add = target > current
        is_reduce = target < current
        wait = cooldown if is_add else min_hold
        fast_risk = is_reduce and target <= 0.25 and current >= 0.75
        if abs(target - current) >= 0.25 and (idx - last_change >= wait or fast_risk):
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


def _select_domain_profile(domain: str, tapes: Sequence[StockTape], pressures: Dict[str, np.ndarray], cost_rate: float) -> Dict[str, Any]:
    all_p = np.concatenate([np.asarray(pressures[t.code], dtype=float) for t in tapes if t.code in pressures])
    pressure_scale = float(np.nanpercentile(np.abs(all_p), 85)) if len(all_p) else 1.0
    scored: List[Dict[str, Any]] = []
    for params in _profile_variants(domain, pressure_scale):
        rows: List[List[float]] = []
        for tape in tapes:
            replay = _replay(tape, pressures[tape.code], params, cost_rate)
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
        med_turnover = float(np.nanmedian(arr[:, 3]))
        objective = (
            0.58 * float(np.nanmedian(arr[:, 0]))
            + 0.42 * float(np.nanmedian(arr[:, 1]))
            + 0.22 * float(np.nanmedian(arr[:, 2]))
            + 0.24 * float(np.nanmean(arr[:, 0] > 0.0))
            + 0.16 * float(np.nanmean(arr[:, 1] > 0.0))
            - 0.060 * max(0.0, med_turnover - 7.0)
            - 0.020 * max(0.0, 2.5 - med_turnover)
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
                "mean_turnover": float(np.nanmean(arr[:, 3])),
                "sharpe_win_rate": float(np.nanmean(arr[:, 0] > 0.0)),
            }
        )
    scored.sort(key=lambda x: x["objective"], reverse=True)
    return scored[0]


def _target_result(
    tape: StockTape,
    event_pressure: np.ndarray,
    domain: str,
    rulebook: pd.DataFrame,
    selected: Dict[str, Any],
    cost_rate: float,
    output_dir: Path,
) -> Dict[str, Any]:
    replay = _replay(tape, event_pressure, selected["params"], cost_rate)
    metrics = _metrics(replay["strategy_nav"], tape.price_nav, replay["positions"])
    annual = _annual_stats(tape.dates, replay["strategy_nav"], tape.price_nav)
    top_rules = rulebook.head(24).to_dict("records")
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
        "domain_pool_events": int(rulebook["n_events"].sum()) if not rulebook.empty else 0,
        "stock_event_count": int(np.count_nonzero(np.abs(event_pressure) > 0.0)),
        "current_score": float(replay["current_score"]),
        "current_position": float(replay["current_position"]),
        "current_position_label": _position_label(float(replay["current_position"])),
        "current_active_signals": [],
        "matched_domain_rules": top_rules[:10],
        "top_domain_rules": top_rules,
        "evolver_profile": {"name": "V27_shared_domain_teacher_lowfreq", "params": dict(selected["params"])},
        "model_boundary": "one shared style-size domain rulebook/profile; no per-stock parameter fitting",
    }
    result["latest_signal"] = (
        f"{tape.code} {tape.name}: domain={domain}, position={result['current_position_label']}, "
        f"Sharpe {metrics['strategy_sharpe']:.2f}/{metrics['price_sharpe']:.2f}, "
        f"annual {metrics['strategy_annual_return']:.1%}/{metrics['price_annual_return']:.1%}, "
        f"mdd {metrics['strategy_max_drawdown']:.1%}/{metrics['price_max_drawdown']:.1%}, "
        f"turnover {metrics['turnover_times_per_year']:.2f}"
    )
    safe = _safe_name(f"V27_shared_teacher_lowfreq_{domain}_{tape.code}_{tape.name}")
    chart_path = output_dir / f"{safe}_trade_nav_relative.png"
    result["chart_path"] = str(chart_path)
    _plot_style_size_chart(result, chart_path)
    light = {k: v for k, v in result.items() if k not in {"close", "price_nav", "strategy_nav", "relative_strength", "positions", "scores"}}
    (output_dir / f"{safe}.json").write_text(json.dumps(light, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    notes = [result["latest_signal"], result["model_boundary"], "top domain memory rules:"]
    for row in top_rules:
        notes.append(
            f"- {row['rule_id']} weight={row['weight']:.4f} hit={row['hit_rate']:.2%} "
            f"edge={row['mean_signed_return']:.3%} breadth={int(row['n_stocks'])}"
        )
    (output_dir / f"{safe}_memory_notes.txt").write_text("\n".join(notes), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V27 shared domain teacher low-frequency memory model.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    parser.add_argument("--max-domain-stocks", type=int, default=0, help="0 means all stocks in each needed domain.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _load_events(Path(args.events_cache))
    domain_map = _latest_domain_map(events)
    target_codes = [str(c) for c in args.codes]
    needed_domains = sorted({str(domain_map.get(code, "")) for code in target_codes if str(domain_map.get(code, ""))})
    results: List[Dict[str, Any]] = []
    profiles: Dict[str, Any] = {}
    rulebooks: Dict[str, pd.DataFrame] = {}
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for domain in needed_domains:
            domain_events = events[events[DOMAIN_COL].eq(domain)].copy()
            members = _domain_members(events, domain)
            if args.max_domain_stocks and args.max_domain_stocks > 0:
                members = members[: int(args.max_domain_stocks)]
                domain_events = domain_events[domain_events["ts_code"].isin(members)].copy()
            print(f"[domain] {domain} members={len(members)} events={len(domain_events)} learning memory rulebook...", flush=True)
            rulebook = _learn_rulebook(domain_events, max_rules=120)
            rulebooks[domain] = rulebook
            tapes: List[StockTape] = []
            pressures: Dict[str, np.ndarray] = {}
            for code in members:
                try:
                    tape = _make_tape(_load_stock(conn, code, str(as_of)))
                except Exception:
                    tape = None
                if tape is None:
                    continue
                stock_events = domain_events[domain_events["ts_code"].eq(code)]
                pressure = _event_pressure_for_tape(tape, stock_events, rulebook)
                tapes.append(tape)
                pressures[tape.code] = pressure
            print(f"[domain] {domain} valid_tapes={len(tapes)} rules={len(rulebook)} selecting shared teacher executor...", flush=True)
            selected = _select_domain_profile(domain, tapes, pressures, float(args.cost_rate))
            profiles[domain] = selected
            rulebook.to_csv(output_dir / f"V27_rulebook_{_safe_name(domain)}.csv", index=False, encoding="utf-8-sig")
            print(
                f"[selected] {domain} stocks={selected['stocks']} rules={len(rulebook)} "
                f"ex_sharpe={selected['median_excess_sharpe']:.3f} ex_ann={selected['median_annual_excess']:.2%} "
                f"dd={selected['median_drawdown_improve']:.2%} turnover={selected['median_turnover']:.2f}",
                flush=True,
            )
        for code in target_codes:
            domain = str(domain_map.get(code, ""))
            if domain not in profiles:
                print(f"[skip] {code} no profile", flush=True)
                continue
            tape = _make_tape(_load_stock(conn, code, str(as_of)))
            if tape is None:
                print(f"[skip] {code} insufficient history", flush=True)
                continue
            stock_events = events[(events["ts_code"].eq(code)) & (events[DOMAIN_COL].eq(domain))]
            pressure = _event_pressure_for_tape(tape, stock_events, rulebooks[domain])
            result = _target_result(tape, pressure, domain, rulebooks[domain], profiles[domain], float(args.cost_rate), output_dir)
            results.append(result)
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
    for result in results:
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
    pd.DataFrame(rows).to_csv(output_dir / "V27_five_stock_summary.csv", index=False, encoding="utf-8-sig")
    (output_dir / "V27_five_stock_summary.json").write_text(
        json.dumps({"results": rows, "profiles": profiles}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[done] output_dir={output_dir}", flush=True)


if __name__ == "__main__":
    main()
