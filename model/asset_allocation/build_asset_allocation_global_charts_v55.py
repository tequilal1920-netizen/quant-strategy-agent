"""Build global performance charts for the latest asset-allocation models.

Reporting only: this script writes PNG/CSV/JSON artifacts and never deploys.
Chinese labels are encoded with unicode escapes so the source stays ASCII-safe
under Windows PowerShell.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.font_manager import FontProperties

from allocation_math_v5 import estimate_statistical_covariance_v5, solve_erc_v5
from backtest_asset_allocation_v541_long import (
    LINEAR_COST_BPS_V541,
    QUADRATIC_COST_V541,
    _drift,
)
from backtest_asset_allocation_v554_long import _simulate_v554, candidate_grid_v554


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_ROOT / "output" / "model_improvement"
OUTPUT_DIR = PROJECT_ROOT / "output" / "asset_allocation_global_charts_v55"

PANEL_PATH = INPUT_DIR / "asset_allocation_panel_v553.json"
V554_PATH = INPUT_DIR / "asset_allocation_v554_long_research.json"

EXPECTED_PANEL_HASH = "815E7181B166EDF859FE59BF56040260564D0CE1E58B3D844DA2B9C6A276439C"
EXPECTED_V554_HASH = "1EFEFB9D98F18B4E6D4CB8B0051B897BED341B1E399B8D478577AB7200D0F376"

ASSET_ORDER = ("equity", "bond", "gold", "commodity")
DISPLAY_BENCHMARK = np.array([0.25, 0.25, 0.25, 0.25], dtype=float)
POLICY_BENCHMARK = np.array([0.60, 0.15, 0.10, 0.15], dtype=float)
ALL_WEATHER_WEIGHTS = np.array([0.15, 0.60, 0.10, 0.15], dtype=float)

ORANGE = "#FFC000"
GREY = "#BFBFBF"
RED = "#C00000"
BLACK = "#000000"
AXIS_GREY = "#D9D9D9"

CN_YEAR = "\u5e74\u5ea6"
CN_STRATEGY_RETURN = "\u7b56\u7565\u6536\u76ca"
CN_EQUAL = "\u56db\u8d44\u4ea7\u7b49\u6743"
CN_EXCESS = "\u8d85\u989d\u6536\u76ca"
CN_DRAWDOWN = "\u6700\u5927\u56de\u64a4"
CN_INTERVAL_ANNUAL = "\u533a\u95f4\u5e74\u5316"
CN_RELATIVE_STRENGTH = "\u76f8\u5bf9\u5f3a\u5ea6\uff08\u53f3\u8f74\uff09"
CN_RISK_PARITY = "\u98ce\u9669\u5e73\u4ef7"
CN_ALL_WEATHER = "\u5168\u5929\u5019"
CN_MACRO_FACTOR = "\u5b8f\u89c2\u56e0\u5b50"
CN_ANNUAL_TABLE = "\u5e74\u5ea6\u6307\u6807"
CN_NAV_RELATIVE = "\u51c0\u503c\u4e0e\u76f8\u5bf9\u5f3a\u5f31"
CN_MONTHLY_RETURNS = "\u5168\u90e8\u7b56\u7565\u6708\u5ea6\u6536\u76ca_\u7b49\u6743\u5c55\u793a\u57fa\u51c6.csv"
CN_NAV_CSV = "\u5168\u90e8\u7b56\u7565\u51c0\u503c_\u76f8\u5bf9\u5f3a\u5f31.csv"
CN_AUDIT = "\u7ed8\u56fe\u5ba1\u8ba1.json"


def _font(path: str, size: float) -> FontProperties:
    font_path = Path(path)
    if font_path.exists():
        return FontProperties(fname=str(font_path), size=size)
    return FontProperties(size=size)


KAI_SMALL = _font(r"C:\Windows\Fonts\simkai.ttf", 13)
KAI_LEGEND = _font(r"C:\Windows\Fonts\simkai.ttf", 16)
HEI_HEADER = _font(r"C:\Windows\Fonts\simhei.ttf", 18)
ARIAL = _font(r"C:\Windows\Fonts\arial.ttf", 18)


def _canonical_hash(value: Any) -> str:
    blob = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_inputs(panel: Mapping[str, Any], v554: Mapping[str, Any]) -> None:
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER:
        raise ValueError("asset_order_mismatch")
    if panel.get("content_sha256") != EXPECTED_PANEL_HASH:
        raise ValueError("panel_hash_not_latest_v553")
    if v554.get("content_sha256") != EXPECTED_V554_HASH:
        raise ValueError("v554_hash_not_latest_long_research")
    if v554.get("selection_uses_test") is not False:
        raise ValueError("v554_selection_must_not_use_test")
    selected = v554.get("selected_ids_pretest") or {}
    if selected.get("absolute_no_benchmark") != "V554-ABS-02":
        raise ValueError("v554_bl_abs_champion_changed")


def _month_to_date(month: str) -> pd.Timestamp:
    return pd.Period(str(month), freq="M").to_timestamp("M")


def _period_before(month: str) -> pd.Timestamp:
    return (pd.Period(str(month), freq="M") - 1).to_timestamp("M")


def _cost(change: np.ndarray) -> float:
    linear = np.asarray(LINEAR_COST_BPS_V541, dtype=float) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541, dtype=float)
    return float(linear @ np.abs(change) + 0.5 * quadratic @ (change**2))


def _simulate_fixed_weights(
    months: Sequence[str],
    returns: np.ndarray,
    target_weights: np.ndarray,
    *,
    name: str,
    start_signal_index: int = 35,
) -> list[dict[str, Any]]:
    target = np.asarray(target_weights, dtype=float)
    if target.shape != (4,) or not np.all(np.isfinite(target)):
        raise ValueError(f"{name}_target_invalid")
    if abs(float(target.sum()) - 1.0) > 1.0e-10:
        raise ValueError(f"{name}_target_must_sum_to_one")
    previous = target.copy()
    rows: list[dict[str, Any]] = []
    for signal_index in range(start_signal_index, len(returns) - 1):
        realized = returns[signal_index + 1]
        realized_month = str(months[signal_index + 1])
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "month": realized_month,
                "net_return": float(target @ realized) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
            }
        )
        previous = _drift(target, realized)
    return rows


def _simulate_rolling_erc(
    months: Sequence[str],
    returns: np.ndarray,
    *,
    start_signal_index: int = 35,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous: np.ndarray | None = None
    for signal_index in range(start_signal_index, len(returns) - 1):
        window = returns[signal_index - 35 : signal_index + 1]
        covariance, _ = estimate_statistical_covariance_v5(
            window,
            half_life=24,
            diagonal_shrinkage=0.35,
        )
        erc = solve_erc_v5(covariance)
        if erc.status != "optimal":
            raise RuntimeError(f"erc_failed:{months[signal_index]}")
        target = np.asarray(erc.weights, dtype=float)
        if previous is None:
            previous = target.copy()
        realized = returns[signal_index + 1]
        realized_month = str(months[signal_index + 1])
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "month": realized_month,
                "net_return": float(target @ realized) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
                "erc_maximum_budget_error": float(np.max(np.abs(erc.budget_error))),
            }
        )
        previous = _drift(target, realized)
    return rows


def _v554_abs02_rows(panel: Mapping[str, Any]) -> list[dict[str, Any]]:
    spec = next(item for item in candidate_grid_v554() if item["id"] == "V554-ABS-02")
    result = _simulate_v554(panel, spec, allow_test=True)
    rows: list[dict[str, Any]] = []
    for row in result["returns"]:
        rows.append(
            {
                "month": str(row["month"]),
                "net_return": float(row["net_return"]),
                "turnover": float(row["turnover"]),
                "cost": float(row["cost"]),
            }
        )
    return rows


def _align_rows(
    strategy_rows: Sequence[Mapping[str, Any]],
    benchmark_rows: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    strategy = {str(row["month"]): float(row["net_return"]) for row in strategy_rows}
    benchmark = {str(row["month"]): float(row["net_return"]) for row in benchmark_rows}
    months = sorted(set(strategy) & set(benchmark))
    if not months:
        raise ValueError("no_common_chart_months")
    frame = pd.DataFrame(
        {
            "month": months,
            "date": [_month_to_date(month) for month in months],
            "strategy_return": [strategy[month] for month in months],
            "benchmark_return": [benchmark[month] for month in months],
        }
    )
    frame["excess_return"] = (1.0 + frame["strategy_return"]) / (1.0 + frame["benchmark_return"]) - 1.0
    return frame


def _nav_series(returns: Sequence[float], months: Sequence[str]) -> pd.Series:
    values = [1.0]
    for item in returns:
        values.append(values[-1] * (1.0 + float(item)))
    index = [_period_before(str(months[0]))] + [_month_to_date(str(month)) for month in months]
    return pd.Series(values, index=pd.DatetimeIndex(index))


def _max_drawdown_from_nav(nav: pd.Series) -> float:
    drawdown = nav / nav.cummax() - 1.0
    return float(drawdown.min())


def _period_return(returns: pd.Series) -> float:
    return float(np.prod(1.0 + returns.to_numpy(dtype=float)) - 1.0) if not returns.empty else 0.0


def _annualized_return(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float(np.prod(1.0 + returns.to_numpy(dtype=float)) ** (12.0 / len(returns)) - 1.0)


def _pct(value: float) -> str:
    number = 0.0 if abs(float(value)) < 0.0005 else float(value)
    return f"{number * 100:.1f}%"


def _annual_table(frame: pd.DataFrame) -> list[list[str]]:
    months = [str(item) for item in frame["month"]]
    strategy = pd.Series(frame["strategy_return"].to_numpy(dtype=float), index=pd.Index(months))
    benchmark = pd.Series(frame["benchmark_return"].to_numpy(dtype=float), index=pd.Index(months))
    rows: list[list[str]] = []
    for year in sorted({month[:4] for month in months}):
        label = f"{year}YTD" if year == str(pd.Timestamp.today().year) else year
        year_months = [month for month in months if month.startswith(year)]
        s = strategy.loc[year_months]
        b = benchmark.loc[year_months]
        year_nav = _nav_series(s.tolist(), year_months)
        rows.append(
            [
                label,
                _pct(_period_return(s)),
                _pct(_period_return(b)),
                _pct((1.0 + _period_return(s)) / (1.0 + _period_return(b)) - 1.0),
                _pct(_max_drawdown_from_nav(year_nav)),
            ]
        )
    full_nav = _nav_series(strategy.tolist(), months)
    rows.append(
        [
            CN_INTERVAL_ANNUAL,
            _pct(_annualized_return(strategy)),
            _pct(_annualized_return(benchmark)),
            _pct((1.0 + _annualized_return(strategy)) / (1.0 + _annualized_return(benchmark)) - 1.0),
            _pct(_max_drawdown_from_nav(full_nav)),
        ]
    )
    return rows


def _render_table(rows: Sequence[Sequence[str]], output: Path) -> None:
    headers = [CN_YEAR, CN_STRATEGY_RETURN, CN_EQUAL, CN_EXCESS, CN_DRAWDOWN]
    dpi = 180
    fig_width = 1266 / dpi
    fig_height = 717 / dpi
    fig, axis = plt.subplots(figsize=(fig_width, fig_height), dpi=dpi)
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
        cell_text = text.get_text()
        text.set_fontproperties(HEI_HEADER if row_index == 0 or any(ord(char) > 127 for char in cell_text) else ARIAL)
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


def _render_nav_chart(frame: pd.DataFrame, strategy_label: str, output: Path) -> pd.DataFrame:
    months = [str(item) for item in frame["month"]]
    strategy_nav = _nav_series(frame["strategy_return"].tolist(), months)
    benchmark_nav = _nav_series(frame["benchmark_return"].tolist(), months)
    relative_strength = strategy_nav / benchmark_nav

    dpi = 180
    fig, axis = plt.subplots(figsize=(1778 / dpi, 1197 / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    axis.set_facecolor("white")
    axis.plot(
        benchmark_nav.index,
        benchmark_nav.values,
        color=ORANGE,
        linewidth=2.25,
        solid_capstyle="round",
        label=CN_EQUAL,
    )
    axis.plot(
        strategy_nav.index,
        strategy_nav.values,
        color=GREY,
        linewidth=2.35,
        solid_capstyle="round",
        label=strategy_label,
    )
    right = axis.twinx()
    right.plot(
        relative_strength.index,
        relative_strength.values,
        color=RED,
        linewidth=2.55,
        solid_capstyle="round",
        label=CN_RELATIVE_STRENGTH,
    )

    left_values = np.r_[strategy_nav.values, benchmark_nav.values]
    left_low, left_high, left_ticks = _nice_limits(left_values, step=0.2 if np.ptp(left_values) <= 2.0 else 0.5)
    right_low, right_high, right_ticks = _nice_limits(
        relative_strength.values,
        step=0.1 if np.ptp(relative_strength.values) <= 0.8 else 0.5,
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
    for label in axis.get_xticklabels() + axis.get_yticklabels() + right.get_yticklabels():
        label.set_fontproperties(ARIAL)

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

    return pd.DataFrame(
        {
            "date": strategy_nav.index.strftime("%Y-%m-%d"),
            "strategy_nav": strategy_nav.values,
            "equal_weight_nav": benchmark_nav.values,
            "relative_strength": relative_strength.values,
        }
    )


def _strategy_summary(frame: pd.DataFrame) -> dict[str, Any]:
    months = [str(item) for item in frame["month"]]
    strategy = pd.Series(frame["strategy_return"].to_numpy(dtype=float), index=pd.Index(months))
    benchmark = pd.Series(frame["benchmark_return"].to_numpy(dtype=float), index=pd.Index(months))
    active = strategy - benchmark
    volatility = float(strategy.std(ddof=1) * math.sqrt(12.0))
    benchmark_volatility = float(benchmark.std(ddof=1) * math.sqrt(12.0))
    tracking = float(active.std(ddof=1) * math.sqrt(12.0))
    return {
        "months": len(frame),
        "start_month": months[0],
        "end_month": months[-1],
        "annual_return": _annualized_return(strategy),
        "benchmark_annual_return": _annualized_return(benchmark),
        "annual_excess_return": (1.0 + _annualized_return(strategy))
        / (1.0 + _annualized_return(benchmark))
        - 1.0,
        "annual_volatility": volatility,
        "benchmark_annual_volatility": benchmark_volatility,
        "sharpe": float(strategy.mean() * 12.0 / volatility) if volatility > 1.0e-12 else None,
        "benchmark_sharpe": float(benchmark.mean() * 12.0 / benchmark_volatility)
        if benchmark_volatility > 1.0e-12
        else None,
        "information_ratio": float(active.mean() * 12.0 / tracking) if tracking > 1.0e-12 else None,
        "max_drawdown": _max_drawdown_from_nav(_nav_series(strategy.tolist(), months)),
    }


def _write_monthly_csv(strategy_frames: Mapping[str, pd.DataFrame], output: Path) -> None:
    months = next(iter(strategy_frames.values()))["month"].tolist()
    table: dict[str, list[Any]] = {"month": months}
    for key, frame in strategy_frames.items():
        table[f"{key}_return"] = frame["strategy_return"].tolist()
        table[f"{key}_equal_weight_return"] = frame["benchmark_return"].tolist()
    pd.DataFrame(table).to_csv(output, index=False, encoding="utf-8-sig")


def build() -> dict[str, Any]:
    panel = _read_json(PANEL_PATH)
    v554 = _read_json(V554_PATH)
    _validate_inputs(panel, v554)

    months = [str(item) for item in panel["months"]]
    returns = np.asarray(panel["returns"], dtype=float)
    equal_rows = _simulate_fixed_weights(months, returns, DISPLAY_BENCHMARK, name="equal_weight")

    strategy_builders: list[tuple[str, str, str, Callable[[], list[dict[str, Any]]], str]] = [
        (
            "01_BL",
            "BL",
            "BL",
            lambda: _v554_abs02_rows(panel),
            "V554-ABS-02: latest v554 no-benchmark BL plus strict ERC-anchor research champion; not production-promoted.",
        ),
        (
            "02_" + CN_RISK_PARITY,
            CN_RISK_PARITY,
            CN_RISK_PARITY,
            lambda: _simulate_rolling_erc(months, returns),
            "Rolling 36-month statistical covariance plus strict ERC; reporting-only.",
        ),
        (
            "03_" + CN_ALL_WEATHER,
            CN_ALL_WEATHER,
            CN_ALL_WEATHER,
            lambda: _simulate_fixed_weights(months, returns, ALL_WEATHER_WEIGHTS, name="all_weather"),
            "Fixed all-weather sleeve E/B/G/C=15/60/10/15, monthly rebalanced with the same costs.",
        ),
        (
            "04_" + CN_MACRO_FACTOR,
            CN_MACRO_FACTOR,
            CN_MACRO_FACTOR,
            lambda: _simulate_fixed_weights(months, returns, POLICY_BENCHMARK, name="macro_factor_gate_off"),
            "Macro release-vintage PIT admission is zero; macro-alpha is truth-gated off, so the chart shows the 60/15/10/15 policy path without fabricated macro contribution.",
        ),
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    strategy_frames: dict[str, pd.DataFrame] = {}
    nav_frames: dict[str, pd.DataFrame] = {}
    audit: dict[str, Any] = {
        "schema_version": "asset-allocation-global-performance-charts-v55/1.0",
        "created_by": "build_asset_allocation_global_charts_v55.py",
        "input_files": {
            "panel": str(PANEL_PATH),
            "panel_content_sha256": panel["content_sha256"],
            "v554": str(V554_PATH),
            "v554_content_sha256": v554["content_sha256"],
        },
        "asset_order_internal": list(ASSET_ORDER),
        "display_benchmark": {
            "name": CN_EQUAL,
            "weights_internal_equity_bond_gold_commodity": DISPLAY_BENCHMARK.tolist(),
            "optimizer_input": False,
            "active_return_reference": False,
        },
        "policy_benchmark_internal_equity_bond_gold_commodity": POLICY_BENCHMARK.tolist(),
        "strategy_definitions": {},
        "outputs": {},
    }

    for prefix, key, label, builder, description in strategy_builders:
        frame = _align_rows(builder(), equal_rows)
        strategy_frames[key] = frame
        table_rows = _annual_table(frame)
        table_path = OUTPUT_DIR / f"{prefix}_{CN_ANNUAL_TABLE}.png"
        nav_path = OUTPUT_DIR / f"{prefix}_{CN_NAV_RELATIVE}.png"
        nav_csv = _render_nav_chart(frame, label, nav_path)
        _render_table(table_rows, table_path)
        nav_frames[key] = nav_csv
        audit["strategy_definitions"][key] = description
        audit["outputs"][key] = {
            "annual_table_png": str(table_path),
            "nav_relative_strength_png": str(nav_path),
            "summary": _strategy_summary(frame),
            "annual_table_rows": table_rows,
        }

    _write_monthly_csv(strategy_frames, OUTPUT_DIR / CN_MONTHLY_RETURNS)
    with (OUTPUT_DIR / CN_NAV_CSV).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["strategy", "date", "strategy_nav", "equal_weight_nav", "relative_strength"])
        for key, frame in nav_frames.items():
            for row in frame.itertuples(index=False):
                writer.writerow([key, row.date, row.strategy_nav, row.equal_weight_nav, row.relative_strength])

    audit["output_content_sha256"] = _canonical_hash(
        {key: value for key, value in audit.items() if key != "output_content_sha256"}
    )
    audit_path = OUTPUT_DIR / CN_AUDIT
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return audit


def main() -> None:
    audit = build()
    print(json.dumps({"status": "ok", "output_dir": str(OUTPUT_DIR), "outputs": audit["outputs"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
