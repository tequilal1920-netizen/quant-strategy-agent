"""Export latest industry/style rotation figures to the desktop review folder."""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parent


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "database").exists() and (candidate / "board").exists():
            return candidate
    return start.parents[1]


PROJECT_ROOT = _find_project_root(ROOT)
DATABASE = PROJECT_ROOT / "database" / "research_warehouse.db"
BOARD_ROOT = PROJECT_ROOT / "board" / "quant_strategy_agent"
VNEXT_DATA = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data"
STATIC_FIGURES = BOARD_ROOT / "static" / "rotation_figures"
DESKTOP_DIR = Path(r"C:\Users\Rye\Desktop\行业轮动")
SIGNAL_CUTOFF = "20260730"

HEADER_BLUE = "#1F3D7A"
ROW_BLUE = "#E8EEF7"
LIGHT_ORANGE = "#F7E9DB"
LINE_GREY = "#B7C3D0"


def _set_font() -> None:
    candidates = ["KaiTi", "STKaiti", "Kaiti SC", "SimKai", "FangSong", "SimSun", "Microsoft YaHei", "Arial Unicode MS"]
    available = {font.name for font in plt.matplotlib.font_manager.fontManager.ttflist}
    selected = next((name for name in candidates if name in available), "Microsoft YaHei")
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = [selected, "Arial"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["savefig.facecolor"] = "white"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_static_figures() -> list[Path]:
    mapping = {
        "industry_monthly_annual_table.png": "01_行业轮动_C39_收益表.png",
        "industry_monthly_daily_nav.png": "01_行业轮动_C39_净值相对强度.png",
        "style12_annual_table.png": "02_12类风格轮动_收益表.png",
        "style12_daily_nav.png": "02_12类风格轮动_净值相对强度.png",
        "size3_annual_table.png": "03_市值轮动_收益表.png",
        "size3_daily_nav.png": "03_市值轮动_净值相对强度.png",
        "style4_annual_table.png": "04_四类风格轮动_收益表.png",
        "style4_daily_nav.png": "04_四类风格轮动_净值相对强度.png",
    }
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for source_name, target_name in mapping.items():
        source = STATIC_FIGURES / source_name
        target = DESKTOP_DIR / target_name
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, target)
        outputs.append(target)
    return outputs


def _industry_scores() -> dict[str, dict[str, float]]:
    connection = sqlite3.connect(DATABASE)
    frame = pd.read_sql_query(
        """
        SELECT rebalance_date, industry_name, score
        FROM v3_industry_signal
        WHERE run_id = 'v3_strict_integrated_20260706'
          AND universe = 'CSI800_ENH'
          AND rebalance_date >= '20260101'
          AND rebalance_date <= ?
        ORDER BY rebalance_date, score DESC
        """,
        connection,
        params=(SIGNAL_CUTOFF,),
    )
    connection.close()
    if frame.empty:
        return {}
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    output: dict[str, dict[str, float]] = {}
    for date, group in frame.groupby("rebalance_date", sort=True):
        output[str(date)] = {
            str(row.industry_name): float(row.score)
            for row in group.dropna(subset=["score"]).sort_values("score", ascending=False).itertuples()
        }
    return output


def _style_scores(strategy: dict[str, Any]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for holding in strategy.get("holdings", []):
        signal = str(holding.get("signal_date", "")).replace("-", "")
        if not signal.startswith("2026") or signal > SIGNAL_CUTOFF:
            continue
        score = holding.get("score", {})
        if not isinstance(score, dict):
            continue
        parsed: dict[str, float] = {}
        for name, value in score.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                parsed[str(name)] = number
        output[signal] = parsed
    return output


def _date_label(value: str) -> str:
    ts = pd.Timestamp(value)
    return f"{ts.month}月信号\n{ts.strftime('%m-%d')}"


def _rank_rows(scores_by_date: dict[str, dict[str, float]], top_n: int = 5) -> list[list[str]]:
    rows: list[list[str]] = []
    for date in sorted(scores_by_date):
        items = sorted(scores_by_date[date].items(), key=lambda item: item[1], reverse=True)
        if not items:
            continue
        top = [name for name, _ in items[:top_n]]
        bottom = [name for name, _ in items[-top_n:]][::-1]
        top += [""] * (top_n - len(top))
        bottom += [""] * (top_n - len(bottom))
        rows.append([_date_label(date), *top[:top_n], *bottom[:top_n]])
    return rows


def _plot_top_bottom(title: str, scores_by_date: dict[str, dict[str, float]], path: Path) -> None:
    _set_font()
    rows = _rank_rows(scores_by_date, 5)
    if not rows:
        raise ValueError(f"empty_scores_for_{path.name}")
    columns = ["信号日期", "Top1", "Top2", "Top3", "Top4", "Top5", "Bottom1", "Bottom2", "Bottom3", "Bottom4", "Bottom5"]
    fig_height = max(4.2, 0.48 * (len(rows) + 2))
    fig, ax = plt.subplots(figsize=(14.8, fig_height), dpi=180)
    ax.axis("off")
    ax.text(0.0, 1.03, title, transform=ax.transAxes, ha="left", va="bottom", fontsize=15, fontweight="bold", color="#000000")
    ax.plot([0.0, 1.0], [1.0, 1.0], transform=ax.transAxes, color=LINE_GREY, lw=1.3, clip_on=False)
    table = ax.table(cellText=rows, colLabels=columns, cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10.8)
    table.scale(1.0, 1.45)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#FFFFFF")
        cell.set_linewidth(0.8)
        cell.get_text().set_color("#000000")
        if row == 0:
            cell.set_facecolor("#FFFFFF")
            cell.get_text().set_weight("bold")
        elif row == len(rows):
            cell.set_facecolor(LIGHT_ORANGE)
        elif row % 2 == 0:
            cell.set_facecolor("#FFFFFF")
        else:
            cell.set_facecolor(ROW_BLUE if column > 5 else "#FFFFFF")
        if column == 0 and row > 0:
            cell.get_text().set_weight("bold")
    for column in range(len(columns)):
        for row in range(len(rows) + 1):
            table[(row, column)].set_width(0.084 if column else 0.10)
    fig.tight_layout(pad=0.25)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def export() -> dict[str, Any]:
    copied = _copy_static_figures()
    style = _read_json(VNEXT_DATA / "style_six_dimension_monthly.json")
    top_bottom_jobs = [
        ("01_行业轮动_C39_2026月度TopBottom.png", "行业轮动模型2026年以来月度得分Top5与Bottom5", _industry_scores()),
        ("02_12类风格轮动_2026月度TopBottom.png", "12类风格轮动模型2026年以来月度得分Top5与Bottom5", _style_scores(style["strategies"]["style12"])),
        ("03_市值轮动_2026月度TopBottom.png", "市值轮动模型2026年以来月度得分Top/Bottom", _style_scores(style["strategies"]["size3"])),
        ("04_四类风格轮动_2026月度TopBottom.png", "四类风格轮动模型2026年以来月度得分Top/Bottom", _style_scores(style["strategies"]["style4"])),
    ]
    generated: list[Path] = []
    for filename, title, scores in top_bottom_jobs:
        target = DESKTOP_DIR / filename
        _plot_top_bottom(title, scores, target)
        generated.append(target)
    return {
        "desktop_dir": str(DESKTOP_DIR),
        "copied": [str(path) for path in copied],
        "generated": [str(path) for path in generated],
    }


def main() -> int:
    print(json.dumps(export(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
