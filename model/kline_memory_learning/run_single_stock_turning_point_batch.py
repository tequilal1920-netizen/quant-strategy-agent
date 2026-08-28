"""Batch full-history single-stock K-line memory research.

Each stock is fitted independently by the sparse turning-point teacher from
``run_single_stock_turning_point_research``.  Outputs are broker-report style
PNG charts plus Chinese JSON/TXT learning records.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning.run_single_stock_turning_point_research import (
    DEFAULT_COST_RATE,
    DEFAULT_OUTPUT_DIR,
    POSITION_LEVELS,
    StrategyResult,
    _write_records_txt,
    fit_teacher,
)


DEFAULT_OHLCV_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_ohlcv_runtime.npz"
DEFAULT_BASE_RUNTIME = ROOT / "output" / "kline_memory_learning" / "cross_sectional_factor_runtime.npz"
PALETTE = {
    "red": "#C00000",
    "yellow": "#FFC000",
    "blue": "#2F75B5",
    "gray": "#808080",
    "green": "#00B050",
    "grid": "#BFBFBF",
    "black": "#000000",
}


def _safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", value).strip("_")[:80]


def _load_names(base_runtime_path: Path) -> Dict[str, str]:
    with np.load(base_runtime_path, allow_pickle=False) as data:
        raw = str(data["names_json"][0]) if "names_json" in data.files else "{}"
    try:
        return {str(key): str(value) for key, value in json.loads(raw).items()}
    except Exception:
        return {}


def _load_ohlcv(ohlcv_cache_path: Path) -> Dict[str, np.ndarray]:
    with np.load(ohlcv_cache_path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def _eligible_indices(
    ohlcv: Dict[str, np.ndarray],
    names: Dict[str, str],
    min_history: int,
) -> List[int]:
    codes = ohlcv["codes"].astype(str)
    close = ohlcv["close"].astype(float)
    amount = ohlcv.get("amount", np.full_like(close, np.nan)).astype(float)
    eligible: List[int] = []
    for column, code in enumerate(codes):
        name = names.get(str(code), str(code))
        if "ST" in name.upper() or "退" in name:
            continue
        series = close[:, column]
        valid = np.isfinite(series) & (series > 0)
        if int(valid.sum()) < min_history:
            continue
        if not bool(valid[-1]):
            continue
        recent_amount = amount[-20:, column]
        if np.isfinite(recent_amount).any() and float(np.nanmean(recent_amount)) <= 0:
            continue
        eligible.append(column)
    return eligible


def _select_codes(
    ohlcv: Dict[str, np.ndarray],
    names: Dict[str, str],
    random_count: int,
    seed: int,
    include_codes: Sequence[str],
    min_history: int,
) -> List[int]:
    codes = ohlcv["codes"].astype(str)
    code_to_index = {str(code): index for index, code in enumerate(codes)}
    selected: List[int] = []
    for code in include_codes:
        if code in code_to_index and code_to_index[code] not in selected:
            selected.append(code_to_index[code])
    eligible = [
        index
        for index in _eligible_indices(ohlcv, names, min_history)
        if index not in selected
    ]
    rng = np.random.default_rng(seed)
    if random_count > 0:
        if len(eligible) < random_count:
            raise RuntimeError(f"可随机抽取股票不足：需要 {random_count}，实际 {len(eligible)}。")
        selected.extend(rng.choice(np.asarray(eligible), size=random_count, replace=False).astype(int).tolist())
    return selected


def _extract_stock_series(
    ohlcv: Dict[str, np.ndarray],
    column: int,
) -> Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]:
    dates_all = ohlcv["dates"].astype(str)
    close = ohlcv["close"][:, column].astype(float)
    high = ohlcv["high"][:, column].astype(float)
    volume = ohlcv["volume"][:, column].astype(float)
    valid = np.isfinite(close) & (close > 0) & np.isfinite(high) & (high > 0)
    dates = dates_all[valid].astype(str).tolist()
    close = close[valid]
    high = np.maximum(high[valid], close)
    volume = np.nan_to_num(volume[valid], nan=0.0, posinf=0.0, neginf=0.0)
    return dates, close, high, volume


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("KaiTi", "SimKai", "楷体", "Microsoft YaHei", "SimHei"):
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate, "Arial"]
            break
    else:
        plt.rcParams["font.sans-serif"] = ["Arial"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 9
    return plt


def _major_year_ticks(dates: Sequence[str], max_ticks: int = 8) -> Tuple[np.ndarray, List[str]]:
    years: Dict[str, int] = {}
    for index, date in enumerate(dates):
        year = str(date)[:4]
        years.setdefault(year, index)
    items = list(years.items())
    if len(items) > max_ticks:
        step = int(math.ceil(len(items) / max_ticks))
        items = items[::step]
    return np.asarray([index for _, index in items], dtype=int), [year for year, _ in items]


def _write_broker_style_chart(
    output_path: Path,
    result: StrategyResult,
    code: str,
    name: str,
) -> None:
    plt = _setup_matplotlib()
    import matplotlib.patheffects as pe

    x = np.arange(len(result.dates))
    fig, ax = plt.subplots(figsize=(8.4, 5.15), dpi=180)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    stock_effects = [pe.Stroke(linewidth=3.6, foreground="white"), pe.Normal()]
    ax.plot(
        x,
        result.price_nav,
        color=PALETTE["yellow"],
        lw=2.2,
        label=f"{name}原股价净值",
        path_effects=stock_effects,
        zorder=3,
    )
    ax.plot(
        x,
        result.strategy_nav,
        color=PALETTE["red"],
        lw=2.0,
        label="LLM技术学习策略净值",
        zorder=4,
    )
    if result.buy_indices:
        ax.scatter(
            result.buy_indices,
            result.price_nav[result.buy_indices],
            marker="^",
            s=36,
            color=PALETTE["green"],
            edgecolor="white",
            linewidth=0.45,
            label="买入/加仓",
            zorder=5,
        )
    if result.sell_indices:
        ax.scatter(
            result.sell_indices,
            result.price_nav[result.sell_indices],
            marker="v",
            s=36,
            color=PALETTE["red"],
            edgecolor="white",
            linewidth=0.45,
            label="卖出/减仓",
            zorder=5,
        )

    ax.set_yscale("log")
    tick_indices, tick_labels = _major_year_ticks(result.dates)
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, rotation=90)
    ax.set_ylabel("净值（对数轴）")
    ax.grid(True, axis="y", color=PALETTE["grid"], lw=0.55, alpha=0.42)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(PALETTE["black"])
        ax.spines[spine].set_linewidth(0.8)
    ax.tick_params(axis="both", colors=PALETTE["black"], labelsize=8.5)

    title = f"{name}（{code}）LLM技术学习策略 vs 原股价净值"
    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color="#111827", pad=10)
    subtitle = (
        f"全历史单股独立学习；策略年化 {result.metrics['annual_return']:.1%}，"
        f"Sharpe {result.metrics['sharpe']:.2f}，最大回撤 {result.metrics['max_drawdown']:.1%}，"
        f"当前仓位 {result.metrics['current_position']:.0%}"
    )
    ax.text(0.0, 1.015, subtitle, transform=ax.transAxes, fontsize=8.8, color="#44546A")
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=4,
        frameon=False,
        fontsize=8.5,
        handlelength=2.4,
    )

    last_x = len(result.dates) - 1
    ax.annotate(
        f"策略 {result.strategy_nav[-1]:.2f}x",
        xy=(last_x, result.strategy_nav[-1]),
        xytext=(-80, 16),
        textcoords="offset points",
        arrowprops={"arrowstyle": "-", "color": PALETTE["red"], "lw": 0.7},
        bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": PALETTE["red"], "lw": 0.7},
        fontsize=8,
        color=PALETTE["red"],
    )
    ax.annotate(
        f"原股价 {result.price_nav[-1]:.2f}x",
        xy=(last_x, result.price_nav[-1]),
        xytext=(-82, -24),
        textcoords="offset points",
        arrowprops={"arrowstyle": "-", "color": "#9A7600", "lw": 0.7},
        bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#9A7600", "lw": 0.7},
        fontsize=8,
        color="#9A7600",
    )
    footer = (
        f"区间：{result.dates[0]}至{result.dates[-1]}；成本：单边{DEFAULT_COST_RATE:.1%}；"
        "策略为全历史回溯学习研究模式。"
    )
    fig.text(0.07, 0.035, footer, fontsize=8.2, color="#58677C")
    fig.tight_layout(rect=(0.03, 0.09, 0.98, 0.95))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, facecolor="white")
    plt.close(fig)


def _write_stock_outputs(
    output_dir: Path,
    result: StrategyResult,
    code: str,
    name: str,
) -> Dict[str, object]:
    stem = _safe_name(f"LLM技术学习_{code}_{name}")
    chart_path = output_dir / f"{stem}.png"
    json_path = output_dir / f"{stem}.json"
    txt_path = output_dir / f"{stem}.txt"
    _write_broker_style_chart(chart_path, result, code, name)
    _write_records_txt(txt_path, result, code, name)
    payload = {
        "version": "single-stock-turning-point-teacher-batch/1.0",
        "code": code,
        "name": name,
        "mode": "full_history_research_individual_memory",
        "disclaimer": "每只股票单独全历史学习，属于回溯研究模式，不等同严格样本外生产验证。",
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
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _summary_row(payload: Dict[str, object]) -> Dict[str, object]:
    metrics = payload["metrics"]
    current = payload["current_advice"]
    return {
        "代码": payload["code"],
        "名称": payload["name"],
        "最新日期": current["date"],
        "当前动作": current["action"],
        "目标仓位": f"{float(current['target_position']):.0%}",
        "策略年化": f"{float(metrics['annual_return']):.2%}",
        "Sharpe": f"{float(metrics['sharpe']):.2f}",
        "最大回撤": f"{float(metrics['max_drawdown']):.2%}",
        "策略净值": f"{float(payload['strategy_nav_final']):.2f}x",
        "原股价净值": f"{float(payload['price_nav_final']):.2f}x",
        "信号频率": f"{float(metrics['signals_per_year']):.2f}次/年",
        "图表": payload["artifacts"]["chart"],
    }


def run_batch(
    ohlcv_cache_path: Path = DEFAULT_OHLCV_CACHE,
    base_runtime_path: Path = DEFAULT_BASE_RUNTIME,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    random_count: int = 5,
    seed: int = 20260822,
    include_codes: Sequence[str] = (),
    min_history: int = 900,
) -> Dict[str, object]:
    names = _load_names(base_runtime_path)
    ohlcv = _load_ohlcv(ohlcv_cache_path)
    codes = ohlcv["codes"].astype(str)
    selected_indices = _select_codes(ohlcv, names, random_count, seed, include_codes, min_history)
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads: List[Dict[str, object]] = []
    for column in selected_indices:
        code = str(codes[column])
        name = names.get(code, code)
        dates, close, high, volume = _extract_stock_series(ohlcv, column)
        result = fit_teacher(dates, close, high, volume, DEFAULT_COST_RATE)
        payloads.append(_write_stock_outputs(output_dir, result, code, name))

    summary_rows = [_summary_row(payload) for payload in payloads]
    summary_json = {
        "version": "single-stock-turning-point-teacher-batch/1.0",
        "mode": "full_history_research_individual_memory",
        "seed": seed,
        "random_count": random_count,
        "include_codes": list(include_codes),
        "stock_count": len(payloads),
        "rows": summary_rows,
    }
    json_path = output_dir / "随机五股LLM技术学习持仓结论.json"
    csv_path = output_dir / "随机五股LLM技术学习持仓结论.csv"
    txt_path = output_dir / "随机五股LLM技术学习持仓结论.txt"
    json_path.write_text(json.dumps(summary_json, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)
    lines = ["随机五股 LLM 技术学习当前持仓结论", ""]
    for row in summary_rows:
        lines.append(
            f"{row['代码']} {row['名称']}：{row['当前动作']}，目标仓位{row['目标仓位']}；"
            f"策略年化{row['策略年化']}，Sharpe {row['Sharpe']}，最大回撤{row['最大回撤']}。"
        )
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary_json["artifacts"] = {"json": str(json_path), "csv": str(csv_path), "txt": str(txt_path)}
    return summary_json


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run individual full-history K-line memory research for random stocks.")
    parser.add_argument("--ohlcv-cache", type=Path, default=DEFAULT_OHLCV_CACHE)
    parser.add_argument("--base-runtime", type=Path, default=DEFAULT_BASE_RUNTIME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--random-count", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260822)
    parser.add_argument("--include-code", action="append", default=[])
    parser.add_argument("--min-history", type=int, default=900)
    args = parser.parse_args(argv)
    result = run_batch(
        ohlcv_cache_path=args.ohlcv_cache,
        base_runtime_path=args.base_runtime,
        output_dir=args.output_dir,
        random_count=args.random_count,
        seed=args.seed,
        include_codes=args.include_code,
        min_history=args.min_history,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
