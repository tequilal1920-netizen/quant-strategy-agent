"""Build final daily performance figures for industry and style rotation.

The script reads audited JSON snapshots and writes the same web-ready PNG files
and manifest to both the canonical old board and the vNext board.  Figures use
the CMB visual grammar requested by the user: KaiTi/Arial, red #C00000,
yellow #FFC000, grey #BFBFBF, no chart title, no watermark, and right-axis
relative strength for every daily NAV figure.
"""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "database").exists() and (candidate / "board").exists():
            return candidate
    return start.parents[1]


PROJECT_ROOT = _find_project_root(ROOT)
OLD_BOARD_ROOT = PROJECT_ROOT / "board" / "quant_strategy_agent"
VNEXT_BOARD_ROOT = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext"
BOARD_ROOTS = (OLD_BOARD_ROOT, VNEXT_BOARD_ROOT)
SOURCE_DATA_DIR = VNEXT_BOARD_ROOT / "data"
CHART_START = "2016-01-01"
SPLITS = {
    "训练集": ("2015-01-01", "2018-12-31"),
    "验证集": ("2019-01-01", "2021-12-31"),
    "测试集": ("2022-01-01", "2099-12-31"),
}


STRATEGY_LABELS = {
    "industry_monthly": "行业轮动",
    "style12": "12类风格轮动",
    "size3": "市值轮动",
    "style4": "风格轮动",
}
BENCHMARK_LABELS = {
    "industry_monthly": "31行业等权",
    "style12": "12风格箱等权",
    "size3": "大中小等权",
    "style4": "四风格等权",
}
BRAND_RED = "#C00000"
BENCHMARK_YELLOW = "#FFC000"
STRATEGY_GREY = "#BFBFBF"
AXIS_GREY = "#D9D9D9"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _set_font() -> None:
    candidates = ["KaiTi", "STKaiti", "Kaiti SC", "SimKai", "FangSong", "SimSun", "Microsoft YaHei", "Arial Unicode MS"]
    available = {font.name for font in plt.matplotlib.font_manager.fontManager.ttflist}
    selected = next((name for name in candidates if name in available), "Microsoft YaHei")
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = [selected, "Arial"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["savefig.facecolor"] = "white"


def _format_percent(value: Any) -> str:
    number = _finite(value)
    return "" if number is None else f"{number * 100:.1f}%"


def _normalise_nav(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date")
    rename = {}
    if "strategy_nav" not in frame.columns and "strategy" in frame.columns:
        rename["strategy"] = "strategy_nav"
    if "benchmark_nav" not in frame.columns and "benchmark" in frame.columns:
        rename["benchmark"] = "benchmark_nav"
    if "excess_nav" not in frame.columns and "excess" in frame.columns:
        rename["excess"] = "excess_nav"
    frame = frame.rename(columns=rename)
    for column in ["strategy_nav", "benchmark_nav", "excess_nav"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "strategy_nav", "benchmark_nav"])


def _drawdown_from_nav(nav: pd.DataFrame) -> float:
    if nav.empty:
        return float("nan")
    wealth = nav["strategy_nav"].astype(float)
    return float((wealth / wealth.cummax() - 1.0).min())


def _annualised_from_nav(nav: pd.DataFrame, column: str) -> float:
    local = nav.dropna(subset=[column])
    if len(local) < 2:
        return float("nan")
    years = len(local) / 252.0
    return float((float(local[column].iloc[-1]) / float(local[column].iloc[0])) ** (1.0 / years) - 1.0)


def _rows_from_calendar(calendar: list[dict[str, Any]], nav: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_year = int(nav["date"].max().year) if not nav.empty else None
    for item in calendar:
        year = item.get("年度", item.get("year"))
        if year is None or str(year) == "区间年化":
            continue
        year_text = str(year)
        year_digits = "".join(ch for ch in year_text if ch.isdigit())
        if len(year_digits) < 4:
            continue
        year_int = int(year_digits[:4])
        label = f"{year_int}YTD" if ("YTD" in year_text.upper() or (last_year and year_int == last_year)) else str(year_int)
        rows.append(
            {
                "年度": label,
                "策略收益": item.get("策略收益", item.get("annual_return")),
                "基准收益": item.get("基准收益", item.get("benchmark_annual_return")),
                "超额收益": item.get("超额收益", item.get("annual_excess")),
                "最大回撤": item.get("最大回撤", item.get("max_drawdown")),
            }
        )
    if not rows and not nav.empty:
        for year, group in nav.groupby(nav["date"].dt.year):
            local = group.dropna(subset=["strategy_nav", "benchmark_nav"])
            if len(local) < 2:
                continue
            strategy_return = float(local["strategy_nav"].iloc[-1] / local["strategy_nav"].iloc[0] - 1.0)
            benchmark_return = float(local["benchmark_nav"].iloc[-1] / local["benchmark_nav"].iloc[0] - 1.0)
            label = f"{int(year)}YTD" if last_year and int(year) == last_year else str(int(year))
            rows.append(
                {
                    "年度": label,
                    "策略收益": strategy_return,
                    "基准收益": benchmark_return,
                    "超额收益": strategy_return - benchmark_return,
                    "最大回撤": _drawdown_from_nav(local),
                }
            )
    if not nav.empty:
        strategy = _annualised_from_nav(nav, "strategy_nav")
        benchmark = _annualised_from_nav(nav, "benchmark_nav")
        rows.append(
            {
                "年度": "区间年化",
                "策略收益": strategy,
                "基准收益": benchmark,
                "超额收益": strategy - benchmark,
                "最大回撤": _drawdown_from_nav(nav),
            }
        )
    return rows


def _plot_table(rows: list[dict[str, Any]], benchmark_label: str, path: Path) -> None:
    _set_font()
    headers = ["年度", "策略收益", benchmark_label, "超额收益", "最大回撤"]
    data = [[row["年度"], _format_percent(row.get("策略收益")), _format_percent(row.get("基准收益")), _format_percent(row.get("超额收益")), _format_percent(row.get("最大回撤"))] for row in rows]
    height = max(4.8, 0.44 * (len(data) + 1))
    fig, ax = plt.subplots(figsize=(8.7, height), dpi=180)
    ax.axis("off")
    table = ax.table(cellText=data, colLabels=headers, cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.scale(1.0, 1.50)
    for (row, _column), cell in table.get_celld().items():
        cell.set_edgecolor("#000000")
        cell.set_linewidth(0.55)
        cell.set_facecolor("#FFFFFF")
        cell.get_text().set_color("#000000")
        if row == 0:
            cell.get_text().set_weight("bold")
    fig.tight_layout(pad=0.15)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _axis_limits(left: pd.Series, right: pd.Series) -> tuple[tuple[float, float], tuple[float, float]]:
    lmin = float(np.nanmin(left.to_numpy(dtype=float)))
    lmax = float(np.nanmax(left.to_numpy(dtype=float)))
    rmin = float(np.nanmin(right.to_numpy(dtype=float)))
    rmax = float(np.nanmax(right.to_numpy(dtype=float)))
    lpad = max((lmax - lmin) * 0.08, 0.05)
    rpad = max((rmax - rmin) * 0.08, 0.03)
    return (max(0.0, lmin - lpad), lmax + lpad), (max(0.0, rmin - rpad), rmax + rpad)


def _plot_nav(nav: pd.DataFrame, strategy_label: str, benchmark_label: str, path: Path) -> None:
    _set_font()
    local = nav.loc[nav["date"].ge(pd.Timestamp(CHART_START))].copy()
    if local.empty:
        raise ValueError(f"empty_nav_for_{strategy_label}")
    local["策略净值"] = local["strategy_nav"] / float(local["strategy_nav"].iloc[0])
    local["基准净值"] = local["benchmark_nav"] / float(local["benchmark_nav"].iloc[0])
    local["相对强度"] = local["策略净值"] / local["基准净值"]
    left_limit, right_limit = _axis_limits(pd.concat([local["策略净值"], local["基准净值"]]), local["相对强度"])

    fig, ax = plt.subplots(figsize=(7.2, 5.2), dpi=180)
    ax2 = ax.twinx()
    ax.plot(local["date"], local["基准净值"], color=BENCHMARK_YELLOW, lw=2.6, label=benchmark_label)
    ax.plot(local["date"], local["策略净值"], color=STRATEGY_GREY, lw=2.6, label=strategy_label)
    ax2.plot(local["date"], local["相对强度"], color=BRAND_RED, lw=2.6, label="相对强度（右轴）")
    ax.set_ylim(*left_limit)
    ax2.set_ylim(*right_limit)
    ax.grid(False)
    ax2.grid(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax2.set_ylabel("")
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(AXIS_GREY)
    ax.spines["left"].set_color(AXIS_GREY)
    ax2.spines["right"].set_color(AXIS_GREY)
    ax.tick_params(axis="x", labelrotation=90, colors="#000000", labelsize=13, length=0)
    ax.tick_params(axis="y", colors="#000000", labelsize=13, length=0)
    ax2.tick_params(axis="y", colors="#000000", labelsize=13, length=0)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(
        lines + lines2,
        labels + labels2,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=3,
        frameon=False,
        fontsize=13,
        handlelength=2.8,
        columnspacing=1.8,
    )
    fig.tight_layout(rect=[0.02, 0.06, 0.98, 0.99])
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _write_figure_set(key: str, rows: list[dict[str, Any]], nav: pd.DataFrame) -> None:
    for board_root in BOARD_ROOTS:
        figure_dir = board_root / "static" / "rotation_figures"
        figure_dir.mkdir(parents=True, exist_ok=True)
        _plot_table(rows, BENCHMARK_LABELS[key], figure_dir / f"{key}_annual_table.png")
        _plot_nav(nav, STRATEGY_LABELS[key], BENCHMARK_LABELS[key], figure_dir / f"{key}_daily_nav.png")


def _build_one(key: str, calendar: list[dict[str, Any]], nav_rows: list[dict[str, Any]], metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    nav = _normalise_nav(nav_rows)
    rows = _rows_from_calendar(calendar, nav)
    _write_figure_set(key, rows, nav)
    return {
        "label": STRATEGY_LABELS[key],
        "benchmark_label": BENCHMARK_LABELS[key],
        "annual_table": f"/static/rotation_figures/{key}_annual_table.png",
        "daily_nav": f"/static/rotation_figures/{key}_daily_nav.png",
        "calendar_year": rows,
        "metrics": metrics or {},
        "chart_frequency": "daily",
        "relative_strength_axis": "right",
        "style_contract": {
            "chinese_font": "KaiTi",
            "english_font": "Arial",
            "red": BRAND_RED,
            "yellow": BENCHMARK_YELLOW,
            "grey": STRATEGY_GREY,
        },
    }


def _attach_style_long_short_figures(key: str, strategy: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    figures = strategy.get("long_short_figures") or {}
    annual_src = Path(str(figures.get("annual_table") or ""))
    nav_src = Path(str(figures.get("daily_nav") or ""))
    if not annual_src.exists() or not nav_src.exists():
        return row
    annual_name = f"{key}_long_short_annual_table.png"
    nav_name = f"{key}_long_short_daily_nav.png"
    for board_root in BOARD_ROOTS:
        figure_dir = board_root / "static" / "rotation_figures"
        figure_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(annual_src, figure_dir / annual_name)
        shutil.copyfile(nav_src, figure_dir / nav_name)
    row["long_short"] = {
        "label": f"{STRATEGY_LABELS[key]}多空",
        "benchmark_label": "Bottom篮子",
        "annual_table": f"/static/rotation_figures/{annual_name}",
        "daily_nav": f"/static/rotation_figures/{nav_name}",
        "calendar_year": strategy.get("long_short_calendar_year", []),
        "metrics": strategy.get("long_short_metrics", {}),
        "chart_frequency": "daily",
        "relative_strength_axis": "right",
        "style_contract": row.get("style_contract", {}),
    }
    return row


def build() -> dict[str, Any]:
    rotation = _read_json(SOURCE_DATA_DIR / "rotation_snapshot.json")
    style = _read_json(SOURCE_DATA_DIR / "style_six_dimension_monthly.json")
    figures: dict[str, Any] = {}
    industry = rotation["industry"]["frequencies"]["monthly"]
    dashboard_path = SOURCE_DATA_DIR / "industry_research_dashboard.json"
    dashboard_rotation: dict[str, Any] = {}
    if dashboard_path.exists():
        try:
            dashboard_rotation = (_read_json(dashboard_path).get("rotation") or {})
        except Exception:
            dashboard_rotation = {}
    industry_research = industry.get("research_result", {}) if isinstance(industry.get("research_result"), dict) else {}
    if dashboard_rotation.get("nav"):
        industry_calendar = dashboard_rotation.get("calendar_year", [])
        industry_nav = dashboard_rotation.get("nav", [])
        industry_metrics = dashboard_rotation.get("metrics", {})
        industry_candidate = dashboard_rotation.get("model_id") or industry.get("research_selected_candidate")
    elif industry_research.get("nav"):
        industry_calendar = industry_research.get("calendar_year", [])
        industry_nav = industry_research.get("nav", [])
        industry_metrics = industry_research.get("metrics", {})
        industry_candidate = industry_research.get("candidate") or industry.get("research_selected_candidate")
    else:
        industry_calendar = industry.get("return_loss_diagnostics", {}).get("calendar_year", [])
        industry_nav = industry.get("nav", [])
        industry_metrics = industry.get("metrics", {})
        industry_candidate = industry.get("selected_candidate")
    figures["industry_monthly"] = _build_one(
        "industry_monthly",
        industry_calendar,
        industry_nav,
        industry_metrics,
    )
    figures["industry_monthly"]["selected_candidate"] = industry.get("selected_candidate")
    figures["industry_monthly"]["research_selected_candidate"] = industry.get("research_selected_candidate")
    figures["industry_monthly"]["published_candidate"] = industry_candidate
    for key in ["style12", "size3", "style4"]:
        strategy = style["strategies"][key]
        row = _build_one(key, strategy.get("calendar_year", []), strategy.get("nav", []), strategy.get("metrics", {}))
        row["selected_candidate"] = strategy.get("selected_candidate")
        row["research_selected_candidate"] = strategy.get("research_selected_candidate")
        row["report_veto"] = strategy.get("report_veto")
        row = _attach_style_long_short_figures(key, strategy, row)
        figures[key] = row
    payload = {
        "schema_version": "1.1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_data_dir": str(SOURCE_DATA_DIR),
        "splits": SPLITS,
        "figures": figures,
    }
    serialised = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    for board_root in BOARD_ROOTS:
        data_path = board_root / "data" / "rotation_final_figures.json"
        static_manifest = board_root / "static" / "rotation_figures" / "manifest.json"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        static_manifest.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text(serialised, encoding="utf-8")
        static_manifest.write_text(serialised, encoding="utf-8")
    return payload


def main() -> int:
    payload = build()
    print(json.dumps({key: {"label": row["label"], "annual_table": row["annual_table"], "daily_nav": row["daily_nav"]} for key, row in payload["figures"].items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
