"""Full-history style-size Wyckoff memory charts.

Domain design:
    all A-share stocks -> 12 style-size boxes
    every box uses all mature historical Wyckoff/K-line events from all stocks
    inside that box as the memory pool.  The same full-history memory rules are
    then replayed on selected stocks in the box.

This runner is for the user's full-history research request.  It does not use
the six-family pure technical factor stack.
"""

from __future__ import annotations

import argparse
import csv
import json
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
    build_memory,
    finite,
    prepare_events,
)
from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    _date_label,
    _load_stock,
    _major_year_ticks,
    _position_label,
    _safe_name,
    _setup_matplotlib,
)


DOMAIN_COL = "domain_style_size12"
DOMAIN_NAME = "风格×市值12种"
OUTPUT_SUBDIR = "风格市值12域记忆曲线"
STYLE_SIZE_ORDER = (
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
)


PALETTE = {
    "red": "#C00000",
    "yellow": "#FFC000",
    "blue": "#1F3F78",
    "light_blue": "#D9E2F3",
    "gray": "#BFBFBF",
    "dark_gray": "#595959",
    "green": "#00A651",
    "black": "#000000",
}


def _configure_base_domain() -> None:
    base.DOMAIN_COL = DOMAIN_COL
    base.DOMAIN_NAME = DOMAIN_NAME
    base.FULL_HISTORY_PROFILE["domain_col"] = DOMAIN_COL
    base.FULL_HISTORY_PROFILE["name"] = "风格×市值12域全历史记忆Evolver"
    base._latest_signal_text = _latest_signal_text  # type: ignore[attr-defined]


def _latest_signal_text(result: Dict[str, Any]) -> str:
    active_rows = result.get("current_active_signals", [])
    if active_rows:
        signal = "、".join(
            f"{row['frequency']} {row['rule_name']}({row['stage']}/{row['confirmation']})"
            for row in active_rows[:3]
        )
    else:
        signal = "近期无未衰减强触发形态，仓位主要由风格×市值域内记忆和尾部趋势共同决定"
    return (
        f"当前分数{result['current_score']:.1f}，建议{result['current_position_label']}；"
        f"风格×市值记忆池：{result['domain_value']}，"
        f"{result['domain_pool_stocks']}只股票、{result['domain_pool_events']}条成熟形态；"
        f"当前技术信号：{signal}。"
    )


def _latest_event_rows(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    frame["stock_name"] = frame["stock_name"].fillna("").astype(str)
    frame = frame.loc[~frame["stock_name"].str.upper().str.contains("ST", regex=False)].copy()
    frame = frame.loc[frame[DOMAIN_COL].notna() & ~frame[DOMAIN_COL].astype(str).str.contains("未分", regex=False)].copy()
    frame["date"] = frame["date"].astype(str)
    frame = frame.sort_values(["ts_code", "date"])
    latest = frame.groupby("ts_code", sort=False).tail(1).copy()
    for column in ("circ_mv", "total_mv", "amount20"):
        latest[column] = pd.to_numeric(latest[column], errors="coerce")
    latest["liquidity_rank"] = latest.groupby(DOMAIN_COL)["amount20"].rank(pct=True)
    latest["size_rank"] = latest.groupby(DOMAIN_COL)["circ_mv"].rank(pct=True)
    latest["fame_score"] = latest["size_rank"].fillna(0.0) * 0.62 + latest["liquidity_rank"].fillna(0.0) * 0.38
    return latest


def select_representative_codes(events: pd.DataFrame, samples_per_domain: int) -> tuple[list[str], pd.DataFrame]:
    latest = _latest_event_rows(events)
    rows: list[dict[str, Any]] = []
    codes: list[str] = []
    domains = [cell for cell in STYLE_SIZE_ORDER if cell in set(latest[DOMAIN_COL].astype(str))]
    for domain in domains:
        local = latest.loc[latest[DOMAIN_COL].astype(str).eq(domain)].copy()
        local = local.sort_values(["fame_score", "circ_mv", "amount20", "ts_code"], ascending=[False, False, False, True])
        chosen = local.head(max(1, int(samples_per_domain))).copy()
        for row in chosen.itertuples(index=False):
            code = str(row.ts_code)
            codes.append(code)
            rows.append(
                {
                    "域": domain,
                    "代码": code,
                    "名称": str(row.stock_name),
                    "最新形态日": str(row.date),
                    "流通市值": finite(row.circ_mv),
                    "20日成交额": finite(row.amount20),
                    "代表性分数": finite(row.fame_score),
                }
            )
    return codes, pd.DataFrame(rows)


def domain_pool_summary(events: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    local_events = events.loc[events[DOMAIN_COL].astype(str).isin(STYLE_SIZE_ORDER)].copy()
    summary = local_events.groupby(DOMAIN_COL, sort=False).agg(
        域内成熟形态数=("forward_return", "size"),
        域内股票数=("ts_code", "nunique"),
        平均20日后收益=("forward_return", "mean"),
        方向命中率=("signed_return", lambda item: float((pd.to_numeric(item, errors="coerce") > 0).mean())),
    ).reset_index().rename(columns={DOMAIN_COL: "域"})
    if not selected.empty:
        names = selected.groupby("域", sort=False).apply(lambda item: "、".join(f"{r.代码} {r.名称}" for r in item.itertuples(index=False))).rename("样本股票").reset_index()
        summary = summary.merge(names, on="域", how="left")
    order_map = {name: idx for idx, name in enumerate(STYLE_SIZE_ORDER)}
    summary["_order"] = summary["域"].map(order_map).fillna(999).astype(int)
    return summary.sort_values(["_order", "域"]).drop(columns="_order")


def _plot_style_size_chart(result: Dict[str, Any], output_path: Path) -> None:
    plt = _setup_matplotlib()
    x = np.arange(len(result["dates"]))
    price_nav = np.asarray(result["price_nav"], dtype=float)
    strategy_nav = np.asarray(result["strategy_nav"], dtype=float)
    relative = np.asarray(result["relative_strength"], dtype=float)
    buy = np.asarray(result["buy_indices"], dtype=int)
    sell = np.asarray(result["sell_indices"], dtype=int)

    fig, ax = plt.subplots(figsize=(8.6, 5.2), dpi=180)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.grid(False)
    ax.plot(x, price_nav, color=PALETTE["yellow"], lw=2.0, label="原股价净值", zorder=2)
    ax.plot(x, strategy_nav, color=PALETTE["gray"], lw=2.35, label="风格×市值记忆策略净值", zorder=3)
    if len(buy):
        ax.scatter(buy, price_nav[buy], marker="^", s=34, color=PALETTE["green"], edgecolor="white", linewidth=0.45, label="买入/加仓", zorder=5)
    if len(sell):
        ax.scatter(sell, price_nav[sell], marker="v", s=34, color=PALETTE["red"], edgecolor="white", linewidth=0.45, label="卖出/减仓", zorder=5)

    ax2 = ax.twinx()
    ax2.plot(x, relative, color=PALETTE["red"], lw=1.85, label="相对强度（右轴）", zorder=4)
    ax.set_title(
        f"{result['domain_value']}｜{result['code']} {result['name']} 记忆学习净值与买卖点",
        loc="left",
        fontsize=12,
        fontweight="bold",
        color=PALETTE["black"],
        pad=8,
    )
    metrics = result["metrics"]
    ax.text(
        0.01,
        0.98,
        (
            f"截止{_date_label(result['as_of'])}；当前{result['current_position_label']}；"
            f"策略Sharpe {metrics['strategy_sharpe']:.2f} / 原股价Sharpe {metrics['price_sharpe']:.2f}；"
            f"策略年化{metrics['strategy_annual_return']:.1%} / 原股价年化{metrics['price_annual_return']:.1%}"
        ),
        transform=ax.transAxes,
        fontsize=8.4,
        color=PALETTE["dark_gray"],
        va="top",
    )
    ticks, labels = _major_year_ticks(result["dates"], max_ticks=8)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=0)
    ax.set_yscale("log")
    ax2.set_yscale("log")
    ax.set_ylabel("净值（对数轴）", fontsize=9)
    ax2.set_ylabel("相对强度（对数轴）", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax2.spines["right"].set_linewidth(0.8)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="lower center", bbox_to_anchor=(0.5, -0.20), frameon=False, ncol=5, fontsize=8.2)
    fig.tight_layout(rect=[0.0, 0.03, 1.0, 1.0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _write_outputs(results: Sequence[Dict[str, Any]], selected: pd.DataFrame, pool_summary: pd.DataFrame, output_dir: Path) -> None:
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
                "图片路径": result["chart_path"],
            }
        )
    pd.DataFrame(rows).to_csv(output_dir / "风格市值12域样本曲线评分.csv", index=False, encoding="utf-8-sig")
    selected.to_csv(output_dir / "风格市值12域样本股票.csv", index=False, encoding="utf-8-sig")
    pool_summary.to_csv(output_dir / "风格市值12域记忆池汇总.csv", index=False, encoding="utf-8-sig")
    (output_dir / "风格市值12域样本曲线评分.json").write_text(
        json.dumps({"results": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["风格×市值12域 Wyckoff/K线记忆学习样本曲线", ""]
    for row in rows:
        lines.append(
            f"{row['域']}｜{row['代码']} {row['名称']}：{row['建议仓位']}，"
            f"策略Sharpe {row['策略Sharpe']} / 原股价Sharpe {row['原股价Sharpe']}。"
        )
    lines.extend(["", "说明：不划分训练/测试，全部成熟历史样本进入对应风格×市值域记忆池，是全历史回溯研究。"])
    (output_dir / "风格市值12域样本曲线说明.txt").write_text("\n".join(lines), encoding="utf-8")


def _make_contact_sheet(image_paths: Sequence[Path], output_path: Path, columns: int = 4, thumb_width: int = 560) -> None:
    from PIL import Image, ImageDraw, ImageFont

    images = []
    for path in image_paths:
        image = Image.open(path).convert("RGB")
        scale = thumb_width / image.width
        size = (thumb_width, max(1, int(image.height * scale)))
        images.append((path, image.resize(size)))
    if not images:
        return
    rows = int(np.ceil(len(images) / columns))
    label_h = 34
    pad = 18
    thumb_h = max(image.height for _, image in images)
    canvas = Image.new("RGB", (columns * thumb_width + (columns + 1) * pad, rows * (thumb_h + label_h) + (rows + 1) * pad), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("msyh.ttc", 18)
    except Exception:
        font = ImageFont.load_default()
    for idx, (path, image) in enumerate(images):
        r, c = divmod(idx, columns)
        x = pad + c * (thumb_width + pad)
        y = pad + r * (thumb_h + label_h + pad)
        draw.text((x, y), path.stem.replace("风格市值记忆Evolver_", ""), fill=(30, 30, 30), font=font)
        canvas.paste(image, (x, y + label_h))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run style-size domain Wyckoff memory curves.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=EVENT_PATH)
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument("--samples-per-domain", type=int, default=2)
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / OUTPUT_SUBDIR)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _configure_base_domain()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/5] 读取全历史成熟Wyckoff事件与风格×市值12域", flush=True)
    all_events = prepare_events(args.events)
    print(f"[events] {len(all_events):,} events / {all_events['ts_code'].nunique():,} stocks", flush=True)

    selected = pd.DataFrame()
    if args.codes:
        codes = [str(code) for code in args.codes]
    else:
        print("[2/5] 每个风格×市值域挑选代表性高流动性样本股", flush=True)
        codes, selected = select_representative_codes(all_events, int(args.samples_per_domain))
        print(f"[selected] {len(codes)} stocks from {selected['域'].nunique() if not selected.empty else 0} domains", flush=True)

    print("[3/5] 构建风格×市值域内全股票历史记忆池", flush=True)
    memory = build_memory(all_events, DOMAIN_COL)
    pool_summary = domain_pool_summary(all_events, selected)

    results: List[Dict[str, Any]] = []
    chart_paths: List[Path] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        print("[4/5] 对样本股应用所在风格×市值域的同一套记忆规则并画图", flush=True)
        for code in codes:
            series = _load_stock(conn, str(code), as_of)
            result = base._run_one_stock(series, all_events, memory, float(args.cost_rate))
            result["model_boundary"] = "模型二：Wyckoff形态记忆学习；风格×市值12域记忆池；全历史回溯研究；不使用六类技术因子。"
            safe = _safe_name(f"风格市值记忆Evolver_{result['domain_value']}_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            _plot_style_size_chart(result, chart_path)
            result["chart_path"] = str(chart_path)
            chart_paths.append(chart_path)
            results.append(result)
            print(
                f"[stock] {result['domain_value']} {result['code']} {result['name']} "
                f"{result['current_position_label']} Sharpe {result['metrics']['strategy_sharpe']:.2f}/"
                f"{result['metrics']['price_sharpe']:.2f}",
                flush=True,
            )

    print("[5/5] 写出汇总与总览图", flush=True)
    _write_outputs(results, selected, pool_summary, output_dir)
    contact_path = output_dir / "风格市值12域样本曲线总览.png"
    _make_contact_sheet(chart_paths, contact_path)
    print(json.dumps({
        "output_dir": str(output_dir),
        "contact_sheet": str(contact_path),
        "stock_count": len(results),
        "domain_count": int(pool_summary["域"].nunique()),
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
