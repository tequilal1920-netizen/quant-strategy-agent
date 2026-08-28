"""Build reference-style performance charts for the latest CSI500 enhancement run.

The script is reporting-only.  It reads the most recent AUDITED optimizer run
from the local state database and writes one NAV/relative-strength chart plus
one annual table for each active CSI500 enhancement strategy.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.font_manager import FontProperties


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DB = PROJECT_ROOT / "database" / "optimizer_state.db"
OUTPUT_DIR = PROJECT_ROOT / "output" / "index_enhancement_global_charts_r34"

ORANGE = "#FFC000"
GREY = "#BFBFBF"
RED = "#C00000"
BLACK = "#000000"
AXIS_GREY = "#D9D9D9"

CN_YEAR = "\u5e74\u5ea6"
CN_STRATEGY_RETURN = "\u7b56\u7565\u6536\u76ca"
CN_BENCHMARK = "\u4e2d\u8bc1500"
CN_EXCESS = "\u8d85\u989d\u6536\u76ca"
CN_DRAWDOWN = "\u6700\u5927\u56de\u64a4"
CN_INTERVAL_ANNUAL = "\u533a\u95f4\u5e74\u5316"
CN_RELATIVE_STRENGTH = "\u76f8\u5bf9\u5f3a\u5ea6\uff08\u53f3\u8f74\uff09"
CN_NAV_RELATIVE = "\u51c0\u503c\u4e0e\u76f8\u5bf9\u5f3a\u5f31"
CN_ANNUAL_TABLE = "\u5e74\u5ea6\u6307\u6807"
CN_FACTOR_DIRECT = "\u56e0\u5b50\u76f4\u6295"
CN_SAME_SUPPORT = "\u540c\u6301\u4ed3\u5f97\u5206\u6743\u91cd"
CN_CONSTRAINED = "\u7ea6\u675f\u4f18\u5316\u5668"

STRATEGIES = (
    ("01", "direct_score_top50", CN_FACTOR_DIRECT),
    ("02", "same_support_score_weighted", CN_SAME_SUPPORT),
    ("03", "constrained_optimizer", CN_CONSTRAINED),
)


def _font(path: str, size: float) -> FontProperties:
    font_path = Path(path)
    if font_path.exists():
        return FontProperties(fname=str(font_path), size=size)
    return FontProperties(size=size)


KAI_LEGEND = _font(r"C:\Windows\Fonts\simkai.ttf", 16)
HEI_HEADER = _font(r"C:\Windows\Fonts\simhei.ttf", 18)
ARIAL = _font(r"C:\Windows\Fonts\arial.ttf", 18)


def _read_latest_result() -> tuple[dict[str, Any], dict[str, Any]]:
    if not STATE_DB.is_file():
        raise FileNotFoundError(f"optimizer_state_missing:{STATE_DB}")
    with sqlite3.connect(str(STATE_DB)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            select run_id, run_name, created_at, updated_at, result_json
            from optimizer_run
            where status='AUDITED' and result_json is not null
            order by created_at desc
            limit 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("audited_optimizer_run_not_found")
    result = json.loads(str(row["result_json"]))
    metadata = {
        "run_id": row["run_id"],
        "run_name": row["run_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    _validate_result(result)
    return metadata, result


def _validate_result(result: Mapping[str, Any]) -> None:
    if result.get("fallback_used") is not False:
        raise RuntimeError("optimizer_result_uses_fallback")
    strategies = result.get("strategies")
    if not isinstance(strategies, Mapping):
        raise RuntimeError("optimizer_strategies_missing")
    required = {"benchmark", *(item[1] for item in STRATEGIES)}
    missing = sorted(required - set(strategies))
    if missing:
        raise RuntimeError("strategy_nav_missing:" + ",".join(missing))
    universe = (
        result.get("metrics", {}).get("universe")
        if isinstance(result.get("metrics"), Mapping) else None
    )
    del universe


def _date(value: str) -> pd.Timestamp:
    text = "".join(character for character in str(value) if character.isdigit())[:8]
    if len(text) != 8:
        raise ValueError(f"invalid_date:{value}")
    return pd.to_datetime(text, format="%Y%m%d")


def _nav_frame(result: Mapping[str, Any], strategy_key: str) -> pd.DataFrame:
    strategies = result["strategies"]
    benchmark_rows = strategies["benchmark"]["nav"]
    strategy_rows = strategies[strategy_key]["nav"]
    benchmark = {
        str(row["date"]): float(row["nav"])
        for row in benchmark_rows
        if row.get("nav") is not None
    }
    strategy = {
        str(row["date"]): float(row["nav"])
        for row in strategy_rows
        if row.get("nav") is not None
    }
    dates = sorted(set(benchmark) & set(strategy))
    if len(dates) < 12:
        raise RuntimeError(f"insufficient_common_nav:{strategy_key}")
    frame = pd.DataFrame(
        {
            "date_text": dates,
            "date": [_date(item) for item in dates],
            "strategy_nav": [strategy[item] for item in dates],
            "benchmark_nav": [benchmark[item] for item in dates],
        }
    )
    for column in ("strategy_nav", "benchmark_nav"):
        first = float(frame[column].iloc[0])
        if not math.isfinite(first) or first <= 0.0:
            raise RuntimeError(f"invalid_initial_nav:{strategy_key}:{column}")
        frame[column] = frame[column] / first
    frame["relative_strength"] = frame["strategy_nav"] / frame["benchmark_nav"]
    frame["strategy_return"] = frame["strategy_nav"].pct_change()
    frame["benchmark_return"] = frame["benchmark_nav"].pct_change()
    return frame


def _period_return(values: pd.Series) -> float:
    values = values.dropna()
    if values.empty:
        return 0.0
    return float(np.prod(1.0 + values.to_numpy(dtype=float)) - 1.0)


def _annualized_return(values: pd.Series) -> float:
    values = values.dropna()
    if values.empty:
        return 0.0
    return float(np.prod(1.0 + values.to_numpy(dtype=float)) ** (12.0 / len(values)) - 1.0)


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    drawdown = nav / nav.cummax() - 1.0
    return float(drawdown.min())


def _pct(value: float) -> str:
    number = 0.0 if abs(float(value)) < 0.0005 else float(value)
    return f"{number * 100:.1f}%"


def _annual_rows(frame: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    usable = frame.dropna(subset=["strategy_return", "benchmark_return"]).copy()
    for year in sorted({item.year for item in usable["date"]}):
        scoped = usable[usable["date"].dt.year == year].copy()
        if scoped.empty:
            continue
        label = f"{year}YTD" if year == pd.Timestamp.today().year else str(year)
        strategy_return = _period_return(scoped["strategy_return"])
        benchmark_return = _period_return(scoped["benchmark_return"])
        year_start = frame[frame["date"] < scoped["date"].iloc[0]].tail(1)
        nav_values = pd.concat(
            [
                year_start["strategy_nav"],
                frame[frame["date"].isin(scoped["date"])]["strategy_nav"],
            ],
            ignore_index=True,
        )
        rows.append(
            [
                label,
                _pct(strategy_return),
                _pct(benchmark_return),
                _pct((1.0 + strategy_return) / (1.0 + benchmark_return) - 1.0),
                _pct(_max_drawdown(nav_values)),
            ]
        )
    strategy_ann = _annualized_return(usable["strategy_return"])
    benchmark_ann = _annualized_return(usable["benchmark_return"])
    rows.append(
        [
            CN_INTERVAL_ANNUAL,
            _pct(strategy_ann),
            _pct(benchmark_ann),
            _pct((1.0 + strategy_ann) / (1.0 + benchmark_ann) - 1.0),
            _pct(_max_drawdown(frame["strategy_nav"])),
        ]
    )
    return rows


def _render_table(rows: Sequence[Sequence[str]], output: Path) -> None:
    headers = [CN_YEAR, CN_STRATEGY_RETURN, CN_BENCHMARK, CN_EXCESS, CN_DRAWDOWN]
    dpi = 180
    fig, axis = plt.subplots(figsize=(1266 / dpi, 717 / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    axis.axis("off")
    table = axis.table(
        cellText=[headers] + [list(row) for row in rows],
        cellLoc="center",
        colLoc="center",
        colWidths=[0.15, 0.2125, 0.2125, 0.2125, 0.2125],
        bbox=[0.0, 0.0, 1.0, 1.0],
    )
    table.auto_set_font_size(False)
    for (row_index, _column_index), cell in table.get_celld().items():
        cell.set_facecolor("white")
        cell.set_edgecolor(BLACK)
        cell.set_linewidth(0.72)
        text = cell.get_text()
        text.set_color(BLACK)
        content = text.get_text()
        text.set_fontproperties(
            HEI_HEADER if row_index == 0 or any(ord(char) > 127 for char in content) else ARIAL
        )
        if row_index == 0:
            text.set_weight("bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, facecolor="white")
    plt.close(fig)


def _nice_limits(values: np.ndarray, *, step: float) -> tuple[float, float, np.ndarray]:
    minimum = float(np.nanmin(values))
    maximum = float(np.nanmax(values))
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise ValueError("axis_values_nonfinite")
    if abs(maximum - minimum) < step:
        centre = (maximum + minimum) / 2.0
        minimum, maximum = centre - step, centre + step
    padding = (maximum - minimum) * 0.06
    lower = math.floor((minimum - padding) / step) * step
    upper = math.ceil((maximum + padding) / step) * step
    ticks = np.arange(lower, upper + step * 0.5, step)
    return lower, upper, ticks


def _render_nav(frame: pd.DataFrame, label: str, output: Path) -> None:
    dpi = 180
    fig, axis = plt.subplots(figsize=(1778 / dpi, 1197 / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    axis.set_facecolor("white")
    axis.plot(
        frame["date"],
        frame["benchmark_nav"],
        color=ORANGE,
        linewidth=2.25,
        solid_capstyle="round",
        label=CN_BENCHMARK,
    )
    axis.plot(
        frame["date"],
        frame["strategy_nav"],
        color=GREY,
        linewidth=2.35,
        solid_capstyle="round",
        label=label,
    )
    right = axis.twinx()
    right.plot(
        frame["date"],
        frame["relative_strength"],
        color=RED,
        linewidth=2.55,
        solid_capstyle="round",
        label=CN_RELATIVE_STRENGTH,
    )

    left_values = np.r_[frame["benchmark_nav"].to_numpy(), frame["strategy_nav"].to_numpy()]
    left_low, left_high, left_ticks = _nice_limits(
        left_values,
        step=0.1 if np.ptp(left_values) <= 0.8 else 0.2,
    )
    right_low, right_high, right_ticks = _nice_limits(
        frame["relative_strength"].to_numpy(),
        step=0.05 if np.ptp(frame["relative_strength"].to_numpy()) <= 0.35 else 0.1,
    )
    axis.set_ylim(left_low, left_high)
    axis.set_yticks(left_ticks)
    right.set_ylim(right_low, right_high)
    right.set_yticks(right_ticks)

    axis.xaxis.set_major_locator(mdates.YearLocator())
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axis.tick_params(axis="x", labelrotation=90, labelsize=18, colors=BLACK, length=5, width=0.75)
    axis.tick_params(axis="y", labelsize=18, colors=BLACK, length=0)
    right.tick_params(axis="y", labelsize=18, colors=BLACK, length=0)
    for tick in axis.get_xticklabels() + axis.get_yticklabels() + right.get_yticklabels():
        tick.set_fontproperties(ARIAL)

    axis.grid(False)
    right.grid(False)
    for spine in ("top", "left", "right"):
        axis.spines[spine].set_visible(False)
        right.spines[spine].set_visible(False)
    axis.spines["bottom"].set_color(AXIS_GREY)
    axis.spines["bottom"].set_linewidth(0.75)
    right.spines["bottom"].set_visible(False)

    lines, labels = axis.get_legend_handles_labels()
    right_lines, right_labels = right.get_legend_handles_labels()
    legend = axis.legend(
        lines + right_lines,
        labels + right_labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=3,
        frameon=False,
        prop=KAI_LEGEND,
        handlelength=1.5,
        handletextpad=0.45,
        columnspacing=1.0,
    )
    for line in legend.get_lines():
        line.set_linewidth(2.8)
    fig.subplots_adjust(left=0.085, right=0.92, top=0.98, bottom=0.18)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, facecolor="white")
    plt.close(fig)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def build() -> dict[str, Any]:
    metadata, result = _read_latest_result()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Any] = {}
    for prefix, key, label in STRATEGIES:
        frame = _nav_frame(result, key)
        nav_path = OUTPUT_DIR / f"{prefix}_{label}_{CN_NAV_RELATIVE}.png"
        table_path = OUTPUT_DIR / f"{prefix}_{label}_{CN_ANNUAL_TABLE}.png"
        _render_nav(frame, label, nav_path)
        annual_rows = _annual_rows(frame)
        _render_table(annual_rows, table_path)
        outputs[key] = {
            "label": label,
            "nav_relative_strength_png": str(nav_path),
            "annual_table_png": str(table_path),
            "nav_rows": int(len(frame)),
            "start": str(frame["date_text"].iloc[0]),
            "end": str(frame["date_text"].iloc[-1]),
            "annual_rows": annual_rows,
            "sha256": {
                "nav_relative_strength_png": _file_hash(nav_path),
                "annual_table_png": _file_hash(table_path),
            },
        }
    return {
        "status": "ok",
        "source": metadata,
        "output_dir": str(OUTPUT_DIR),
        "outputs": outputs,
    }


def main() -> None:
    print(json.dumps(build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
