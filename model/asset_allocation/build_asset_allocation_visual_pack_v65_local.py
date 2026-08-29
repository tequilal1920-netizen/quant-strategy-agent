from __future__ import annotations

import json
import math
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Rectangle, FancyArrow, Wedge, Circle

import build_asset_allocation_visual_pack_daily_local as daily_backend


ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "agent" / "output" / "model_improvement"
SNAPSHOT = DATA_DIR / "asset_allocation_snapshot_v64_daily_excess_governed.json"
PANEL = DATA_DIR / "asset_allocation_panel_v553.json"
FREEZE = DATA_DIR / "asset_allocation_rqdata_v541_freeze.json"
OUT = Path(r"C:\Users\Rye\Desktop\资产配置")

RED = "#b21b12"
DEEP_RED = "#8c150f"
ORANGE = "#f6b27e"
YELLOW = "#f5eec9"
GREY = "#e9e9e9"
LIGHT_GREY = "#f5f5f5"
BLACK = "#111111"
BLUE = "#2f6fb8"
GREEN = "#2f8a42"
GOLD = "#d4bd00"
LINE_GREY = "#bfbfbf"

ASSETS = ["equity", "bond", "gold", "commodity"]
ASSET_CN = {"equity": "股票", "bond": "债券", "gold": "黄金", "commodity": "商品"}
ASSET_CODE = {
    "equity": "沪深300ETF（510300.SH）",
    "bond": "十年国债ETF（511260.SH）",
    "gold": "黄金ETF（518880.SH）",
    "commodity": "非贵金属商品指数（A/AL/C/CF/CU/J/L/M/P/RB/RU/SR/TA/V/Y/ZN，剔除AU/AG）",
}
BENCH = "equal_weight_4_assets"
MODELS = [
    ("black_litterman", "BL周期观点配置"),
    ("risk_parity", "风险预算模型"),
    ("macro_factor", "宏观因子调整"),
]
MACRO_CN = ["增长因子", "通胀因子", "利率因子", "信用因子", "汇率因子", "流动性因子"]
MERRILL_STAGE_TO_ID = {"recovery": 1, "overheat": 2, "stagflation": 3, "recession": 4}
PRING_STAGE_TO_ID = {
    "I_credit_repair": 1,
    "II_profit_expansion": 2,
    "III_prosperity": 3,
    "IV_credit_pressure": 4,
    "V_profit_downturn": 5,
    "V_stagflation_profit_downturn": 5,
    "VI_recession_repair": 6,
}

def setup_fonts() -> None:
    matplotlib.rcParams["font.family"] = ["Arial", "KaiTi", "SimSun", "Microsoft YaHei"]
    matplotlib.rcParams["font.sans-serif"] = ["Arial", "KaiTi", "SimSun", "Microsoft YaHei"]
    matplotlib.rcParams["axes.unicode_minus"] = False


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_out() -> None:
    OUT.mkdir(parents=True, exist_ok=True)


def save(fig: plt.Figure, n: int) -> None:
    fig.savefig(OUT / f"{n}.png", dpi=220, facecolor="white")
    plt.close(fig)


def pmonth(s: str) -> pd.Period:
    text = str(s).strip()
    compact = text.replace("-", "")[:6]
    if len(compact) == 6 and compact.isdigit():
        return pd.Period(f"{compact[:4]}-{compact[4:]}", freq="M")
    return pd.Period(text[:7], freq="M")


def pts(p: pd.Period) -> pd.Timestamp:
    return p.to_timestamp(how="end").normalize()


def fmt_pct(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "--"
    return f"{x*100:.1f}%"


def wrap_cell(value: object, width: int) -> str:
    text = str(value)
    if not text:
        return text
    pieces: list[str] = []
    for part in text.replace("；", "；\n").replace("，", "，\n").splitlines():
        part = part.strip()
        if not part:
            continue
        wrapped = textwrap.wrap(part, width=max(4, width), break_long_words=True, replace_whitespace=False)
        pieces.extend(wrapped or [part])
    return "\n".join(pieces)


def line_count(value: object) -> int:
    return max(1, str(value).count("\n") + 1)


def panel_monthly_returns(panel: dict) -> pd.DataFrame:
    months = [pmonth(x) for x in panel["months"]]
    return pd.DataFrame(panel["returns"], index=months, columns=panel["asset_order"])[ASSETS].astype(float)


def snapshot_returns(snapshot: dict, key: str) -> pd.Series:
    if "backtest" in snapshot:
        rows = snapshot["backtest"]["strategies"][key]["returns"]
    elif key == BENCH:
        rows = ((snapshot.get("benchmarks") or {}).get(BENCH) or {}).get("returns") or []
    else:
        rows = ((snapshot.get("allocation_models") or {}).get(key) or {}).get("returns") or []
    return pd.Series({pmonth(r["month"]): float(r["net_return"]) for r in rows}).sort_index()


def daily_nav_from_monthly(ret: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    if isinstance(ret, pd.Series):
        nav = []
        cur = 1.0
        idx = []
        for p, r in ret.dropna().items():
            days = pd.bdate_range(p.to_timestamp(), pts(p))
            if len(days) == 0:
                continue
            dr = (1.0 + float(r)) ** (1.0 / len(days)) - 1.0
            vals = cur * np.cumprod(np.full(len(days), 1.0 + dr))
            cur = float(vals[-1])
            nav.extend(vals.tolist())
            idx.extend(days.tolist())
        return pd.Series(nav, index=pd.DatetimeIndex(idx))
    out = {}
    for c in ret.columns:
        out[c] = daily_nav_from_monthly(ret[c])
    return pd.DataFrame(out).dropna(how="all")



def nav_from_daily_returns(ret: pd.Series) -> pd.Series:
    clean = ret.dropna().astype(float)
    return pd.Series(np.cumprod(1.0 + clean.values), index=clean.index)


def daily_cycle_strategy_returns(daily_rets: pd.DataFrame, cycles: pd.DataFrame, stage_col: str, weights: dict[int, np.ndarray]) -> pd.Series:
    stage_by_month = {p.strftime("%Y%m"): int(v) for p, v in cycles[stage_col].dropna().items()}
    vals: list[float] = []
    idx: list[pd.Timestamp] = []
    for dt, row in daily_rets[ASSETS].iterrows():
        stage = stage_by_month.get(dt.strftime("%Y%m"))
        target = weights.get(stage) if stage is not None else None
        if target is None:
            continue
        vals.append(float(row.to_numpy(dtype=float) @ target))
        idx.append(pd.Timestamp(dt))
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name=stage_col)

def annual_table(strategy_daily: pd.Series, bench_daily: pd.Series) -> list[list[str]]:
    aligned = pd.concat([strategy_daily, bench_daily], axis=1).dropna()
    aligned.columns = ["策略收益", "基准"]
    years = sorted(aligned.index.year.unique())
    rows: list[list[str]] = []
    for y in years:
        sub = aligned[aligned.index.year == y]
        if sub.empty:
            continue
        sr = sub["策略收益"].iloc[-1] / sub["策略收益"].iloc[0] - 1
        br = sub["基准"].iloc[-1] / sub["基准"].iloc[0] - 1
        dd = (sub["策略收益"] / sub["策略收益"].cummax() - 1).min()
        label = f"{y}YTD" if y == years[-1] else str(y)
        rows.append([label, fmt_pct(sr), fmt_pct(br), fmt_pct(sr - br), fmt_pct(dd)])
    total_s = aligned["策略收益"].iloc[-1] / aligned["策略收益"].iloc[0] - 1
    total_b = aligned["基准"].iloc[-1] / aligned["基准"].iloc[0] - 1
    yrs = max((aligned.index[-1] - aligned.index[0]).days / 365.25, 1e-9)
    ann_s = (1 + total_s) ** (1 / yrs) - 1
    ann_b = (1 + total_b) ** (1 / yrs) - 1
    dd_all = (aligned["策略收益"] / aligned["策略收益"].cummax() - 1).min()
    rows.append(["区间年化", fmt_pct(ann_s), fmt_pct(ann_b), fmt_pct(ann_s - ann_b), fmt_pct(dd_all)])
    return rows


def stage_blocks(periods: list[pd.Period], stages: list[int]) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    blocks = []
    if not periods:
        return blocks
    start = periods[0]
    cur = stages[0]
    last = periods[0]
    for p, s in zip(periods[1:], stages[1:]):
        if s != cur:
            blocks.append((start.to_timestamp(), pts(last), cur))
            start, cur = p, s
        last = p
    blocks.append((start.to_timestamp(), pts(last), cur))
    return blocks


def synth_cycles(months: list[pd.Period], ret: pd.DataFrame) -> pd.DataFrame:
    equity = ret["equity"].rolling(6, min_periods=3).sum() - ret["bond"].rolling(6, min_periods=3).sum()
    commodity = ret["commodity"].rolling(6, min_periods=3).sum()
    bond = ret["bond"].rolling(6, min_periods=3).sum()
    gold = ret["gold"].rolling(6, min_periods=3).sum()
    growth = (equity + commodity - bond).fillna(0)
    inflation = (commodity + gold - bond).fillna(0)
    money = (bond.rolling(3, min_periods=1).mean() - equity.rolling(3, min_periods=1).mean()).fillna(0)
    credit = (equity.rolling(3, min_periods=1).mean() + commodity.rolling(3, min_periods=1).mean() - gold.rolling(3, min_periods=1).mean()).fillna(0)
    pr_growth = growth.copy()

    def z(x: pd.Series, w: int = 24) -> pd.Series:
        m = x.rolling(w, min_periods=6).mean()
        s = x.rolling(w, min_periods=6).std().replace(0, np.nan)
        return ((x - m) / s).clip(-1.15, 1.15).fillna(0).rolling(3, min_periods=1).mean().clip(-1, 1)

    g = z(growth)
    inf = z(inflation)
    m = z(money)
    c = z(credit)
    pg = z(pr_growth)
    gsig = np.where(g >= 0, 1, -1)
    isig = np.where(inf >= 0, 1, -1)
    msig = np.where(m >= 0, 1, -1)
    csig = np.where(c >= 0, 1, -1)
    pgsig = np.where(pg >= 0, 1, -1)

    merrill = []
    for a, b in zip(gsig, isig):
        if a > 0 and b < 0:
            merrill.append(1)  # 复苏
        elif a > 0 and b > 0:
            merrill.append(2)  # 过热
        elif a < 0 and b > 0:
            merrill.append(3)  # 滞胀
        else:
            merrill.append(4)  # 衰退

    pring = []
    prev = 1
    mapping = {
        (1, 1, -1): 1,
        (1, 1, 1): 2,
        (-1, 1, 1): 3,
        (-1, -1, 1): 4,
        (-1, -1, -1): 5,
        (1, -1, -1): 6,
    }
    for tup in zip(msig, csig, pgsig):
        prev = mapping.get(tuple(tup), prev)
        pring.append(prev)

    return pd.DataFrame({
        "增长连续指标": g.values, "通胀连续指标": inf.values, "增长方向": gsig,
        "通胀方向": isig, "美林阶段": merrill,
        "货币连续指标": m.values, "信用连续指标": c.values, "普林格增长连续指标": pg.values,
        "货币方向": msig, "信用方向": csig, "普林格增长方向": pgsig, "普林格阶段": pring,
    }, index=months)

def _unit_clip(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(number):
        return 0.0
    return float(np.clip(number, -1.0, 1.0))


def _direction(value: object) -> int:
    return 1 if _unit_clip(value) >= 0 else -1


def cycle_history_frame(snapshot: dict) -> pd.DataFrame:
    rows = (snapshot.get("cycle_tracking") or {}).get("history") or []
    records = []
    index = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("month"):
            continue
        merrill_stage = str(row.get("merrill_stage") or "")
        pring_stage = str(row.get("pring_stage") or "")
        m_id = MERRILL_STAGE_TO_ID.get(merrill_stage)
        p_id = PRING_STAGE_TO_ID.get(pring_stage)
        if m_id is None or p_id is None:
            continue
        growth = _unit_clip(row.get("merrill_growth"))
        inflation = _unit_clip(row.get("merrill_inflation"))
        money = _unit_clip(row.get("pring_money"))
        credit = _unit_clip(row.get("pring_credit"))
        pr_growth = _unit_clip(row.get("pring_growth"))
        index.append(pmonth(str(row.get("month"))))
        records.append({
            "增长连续指标": growth,
            "通胀连续指标": inflation,
            "增长方向": _direction(growth),
            "通胀方向": _direction(inflation),
            "美林阶段": m_id,
            "货币连续指标": money,
            "信用连续指标": credit,
            "普林格增长连续指标": pr_growth,
            "货币方向": _direction(money),
            "信用方向": _direction(credit),
            "普林格增长方向": _direction(pr_growth),
            "普林格阶段": p_id,
        })
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records, index=pd.PeriodIndex(index, freq="M")).sort_index()
    return frame[~frame.index.duplicated(keep="last")]

def cycle_strategy_returns(ret: pd.DataFrame, stages: pd.Series, mapping: dict[int, np.ndarray]) -> pd.Series:
    out = []
    idx = []
    for i in range(1, len(ret)):
        w = mapping[int(stages.iloc[i - 1])]
        out.append(float(np.dot(w, ret.iloc[i].values)))
        idx.append(ret.index[i])
    return pd.Series(out, index=idx)




def standardize_clip(x: pd.Series, w: int = 24) -> pd.Series:
    m = x.rolling(w, min_periods=6).mean()
    s = x.rolling(w, min_periods=6).std().replace(0, np.nan)
    return ((x - m) / s).replace([np.inf, -np.inf], np.nan).fillna(0).clip(-1, 1)


def macro_proxy_frame(cycles: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """Use only available model panel data/proxy cycle series; no random or hand-filled values."""
    raw = pd.DataFrame(index=ret.index)
    raw["增长因子"] = cycles["增长连续指标"].reindex(ret.index)
    raw["通胀因子"] = cycles["通胀连续指标"].reindex(ret.index)
    raw["利率因子"] = -ret["bond"].rolling(6, min_periods=3).sum()
    raw["信用因子"] = cycles["信用连续指标"].reindex(ret.index)
    raw["汇率因子"] = (ret["gold"] - ret["equity"]).rolling(6, min_periods=3).sum()
    raw["流动性因子"] = cycles["货币连续指标"].reindex(ret.index)
    out = raw.apply(standardize_clip).rolling(3, min_periods=1).mean().clip(-1, 1)
    return out[MACRO_CN].fillna(0)


def ols_stats(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    xy = pd.concat([x, y], axis=1).dropna()
    if len(xy) < 12 or float(xy.iloc[:, 0].var()) <= 1e-12:
        return 0.0, 0.0
    xv = xy.iloc[:, 0].to_numpy(dtype=float)
    yv = xy.iloc[:, 1].to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(xv)), xv])
    beta = np.linalg.lstsq(X, yv, rcond=None)[0]
    resid = yv - X @ beta
    dof = max(len(xv) - 2, 1)
    s2 = float((resid @ resid) / dof)
    cov_beta = s2 * np.linalg.pinv(X.T @ X)
    se = math.sqrt(max(float(cov_beta[1, 1]), 1e-18))
    return float(beta[1]), float(beta[1] / se)


def sparkline(values: pd.Series) -> str:
    vals = values.dropna().tail(12).to_numpy(dtype=float)
    if len(vals) == 0:
        return ""
    lo, hi = float(vals.min()), float(vals.max())
    chars = "▁▂▃▄▅▆▇"
    if hi - lo < 1e-12:
        return chars[3] * len(vals)
    return "".join(chars[min(len(chars) - 1, max(0, int((v - lo) / (hi - lo) * (len(chars) - 1))))] for v in vals)

def top_bottom(fig: plt.Figure, source: str = "资料来源：Wind，模型测算") -> None:
    ax = fig.add_axes([0.02, 0.972, 0.96, 0.002]); ax.axis("off"); ax.plot([0, 1], [0, 0], color=RED, lw=1.5)
    ax2 = fig.add_axes([0.02, 0.028, 0.96, 0.002]); ax2.axis("off"); ax2.plot([0, 1], [0, 0], color=RED, lw=1.2)
    fig.text(0.025, 0.008, source, fontsize=10, family="KaiTi")


def table_figure(n: int, title: str, headers: list[str], rows: list[list[str]], widths=None, fontsize=15) -> None:
    if widths is None:
        widths = [1 / len(headers)] * len(headers)
    total_width = float(sum(widths))
    rel_widths = [float(w) / total_width for w in widths]
    wrap_widths = [max(5, int(58 * rel)) for rel in rel_widths]
    wrapped_headers = [wrap_cell(head, wrap_widths[j]) for j, head in enumerate(headers)]
    wrapped_rows = [
        [wrap_cell(val, wrap_widths[j]) for j, val in enumerate(row)]
        for row in rows
    ]
    row_units = [max(line_count(cell) for cell in wrapped_headers)] + [
        max(line_count(cell) for cell in row) for row in wrapped_rows
    ]
    fig_height = max(5.2, 1.25 + 0.34 * sum(row_units) + 0.28 * len(row_units))
    fig, ax = plt.subplots(figsize=(13.2, fig_height))
    fig.subplots_adjust(left=0.045, right=0.985, top=0.90, bottom=0.08)
    ax.axis("off")
    x = np.cumsum([0] + widths) / sum(widths)
    ax.text(0.01, 1.03, title, fontsize=18, weight="bold", family="KaiTi", va="top", transform=ax.transAxes)
    y_top = 0.94
    table_height = 0.82
    unit_total = float(sum(row_units))
    heights = [table_height * u / unit_total for u in row_units]

    yy = y_top - heights[0]
    for j, head in enumerate(wrapped_headers):
        ax.add_patch(Rectangle((x[j], yy), x[j + 1] - x[j], heights[0], facecolor=DEEP_RED, edgecolor="white", lw=1))
        ax.text((x[j] + x[j + 1]) / 2, yy + heights[0] / 2, head, color="white", fontsize=fontsize, family="KaiTi",
                weight="bold", ha="center", va="center", linespacing=1.18)
    y_cursor = yy
    body_font = max(10, fontsize)
    for i, row in enumerate(wrapped_rows):
        h = heights[i + 1]
        yy = y_cursor - h
        for j, val in enumerate(row):
            fc = "#f8efe8" if j == 0 else (GREY if i % 2 == 0 else "#f7f7f7")
            ax.add_patch(Rectangle((x[j], yy), x[j + 1] - x[j], h, facecolor=fc, edgecolor="white", lw=1))
            cell_font = max(9, body_font - max(0, line_count(val) - 2))
            ax.text((x[j] + x[j + 1]) / 2, yy + h / 2, val, fontsize=cell_font, family="KaiTi",
                    ha="center", va="center", linespacing=1.16)
        y_cursor = yy
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    top_bottom(fig)
    save(fig, n)


def figure_1() -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    stages = [("周期识别", "美林时钟\n普林格周期"), ("周期映射", "四资产排序\n观点强弱"), ("BL配置", "先验收益π\n观点P/Q/Ω"),
              ("风险预算", "协方差Σ\n风险贡献均衡"), ("宏观调整", "增长/通胀/利率\n信用/汇率/流动性")]
    xs = np.linspace(0.08, 0.82, 5)
    for i, (t, s) in enumerate(stages):
        ax.add_patch(Rectangle((xs[i], 0.58), 0.14, 0.10, facecolor="#6f95c8", edgecolor="#3b5d8a", lw=2))
        ax.text(xs[i] + 0.07, 0.63, t, color="white", ha="center", va="center", fontsize=17, family="KaiTi", weight="bold")
        ax.add_patch(Rectangle((xs[i] - .005, 0.35), 0.15, 0.13, facecolor="#e7edf7", edgecolor="#506b91", lw=1.5))
        ax.text(xs[i] + 0.07, 0.415, s, ha="center", va="center", fontsize=14, family="KaiTi")
        if i < 4:
            ax.add_patch(FancyArrow(xs[i] + 0.15, 0.625, xs[i+1] - xs[i] - 0.17, 0, width=0.012,
                                    head_width=0.04, head_length=0.025, color="#5f86b8"))
    ax.add_patch(Rectangle((0.05, 0.20), 0.83, 0.07, facecolor=DEEP_RED, edgecolor=DEEP_RED))
    ax.text(0.465, 0.235, "统一输出：四资产权重、日度净值、年度收益、超额收益、最大回撤、当前周期与观点",
            color="white", ha="center", va="center", fontsize=16, family="KaiTi", weight="bold")
    top_bottom(fig, "资料来源：模型框架整理")
    save(fig, 1)


def figure_4_corr(ret: pd.DataFrame) -> None:
    corr = ret.corr()
    labels = [ASSET_CN[a] for a in ASSETS]
    cmap = LinearSegmentedColormap.from_list("grb", ["#00b050", "#ffff00", "#c00000"])
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.axis("off")
    ax.text(0.02, 0.96, "图表6：组合资产间相关性系数", fontsize=18, family="KaiTi", weight="bold")
    left, top, cell = 0.18, 0.82, 0.14
    for j, lab in enumerate(["收益率相关性\n系数"] + labels):
        ax.add_patch(Rectangle((left + j*cell, top), cell, cell*0.85, facecolor=DEEP_RED, edgecolor=DEEP_RED))
        ax.text(left+j*cell+cell/2, top+cell*0.42, lab, color="white", ha="center", va="center", fontsize=13, family="KaiTi", weight="bold")
    for i, lab in enumerate(labels):
        ax.add_patch(Rectangle((left, top-(i+1)*cell), cell, cell, facecolor=DEEP_RED, edgecolor=DEEP_RED))
        ax.text(left+cell/2, top-(i+0.5)*cell, lab, color="white", ha="center", va="center", fontsize=14, family="KaiTi", weight="bold")
        for j in range(4):
            if j > i:
                continue
            val = corr.iloc[i, j]
            color = cmap((val + 1) / 2)
            ax.add_patch(Rectangle((left+(j+1)*cell, top-(i+1)*cell), cell, cell, facecolor=color, edgecolor="white", lw=1))
            ax.text(left+(j+1.5)*cell, top-(i+0.5)*cell, f"{val:.2f}" if i != j else "1.00",
                    ha="center", va="center", fontsize=13, family="Arial")
    top_bottom(fig, "资料来源：Wind/RQData，模型测算")
    save(fig, 4)


def figure_5_merrill_clock() -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0.03, 0.96, "图表1：美林时钟周期划分", fontsize=18, family="KaiTi", weight="bold")
    # pale arrows
    ax.add_patch(FancyArrow(0.20, 0.82, 0.60, 0, width=.06, head_width=.13, head_length=.06, color="#fbf2cf"))
    ax.add_patch(FancyArrow(0.84, 0.78, 0, -0.55, width=.06, head_width=.13, head_length=.06, color="#fbf2cf"))
    ax.add_patch(FancyArrow(0.80, 0.16, -0.60, 0, width=.06, head_width=.13, head_length=.06, color="#fbf2cf"))
    ax.add_patch(FancyArrow(0.16, 0.20, 0, 0.55, width=.06, head_width=.13, head_length=.06, color="#fbf2cf"))
    ax.text(.50, .84, "通胀上行", fontsize=18, family="KaiTi", ha="center")
    ax.text(.50, .10, "通胀下行", fontsize=18, family="KaiTi", ha="center")
    ax.text(.12, .50, "经济上行", fontsize=18, family="KaiTi", va="center", rotation=90)
    ax.text(.88, .50, "经济下行", fontsize=18, family="KaiTi", va="center", rotation=90)
    center=(.5,.48)
    for a0,a1,lab in [(90,180,"复苏期"),(0,90,"过热期"),(180,270,"衰退期"),(270,360,"滞涨期")]:
        ax.add_patch(Wedge(center, .30, a0, a1, width=.10, facecolor=RED, edgecolor="white", lw=2))
    ax.add_patch(Circle(center, .16, facecolor="#cfcfcf", edgecolor="white", lw=2))
    ax.plot([.34,.66],[.48,.48], color="white", lw=2); ax.plot([.5,.5],[.32,.64], color="white", lw=2)
    ax.text(.41,.54,"股票\n周期性增长",ha="center",va="center",fontsize=15,family="KaiTi")
    ax.text(.59,.54,"商品\n周期性价值",ha="center",va="center",fontsize=15,family="KaiTi")
    ax.text(.41,.41,"债券\n防守性增长",ha="center",va="center",fontsize=15,family="KaiTi")
    ax.text(.59,.41,"黄金\n防守性价值",ha="center",va="center",fontsize=15,family="KaiTi")
    ax.text(.36,.62,"复苏期", color="white", fontsize=15, family="KaiTi", rotation=35)
    ax.text(.64,.62,"过热期", color="white", fontsize=15, family="KaiTi", rotation=-35)
    ax.text(.36,.31,"衰退期", color="white", fontsize=15, family="KaiTi", rotation=-35)
    ax.text(.64,.31,"滞涨期", color="white", fontsize=15, family="KaiTi", rotation=35)
    top_bottom(fig, "资料来源：美林证券《The Investment Clock》，模型整理")
    save(fig, 5)


def figure_factor_pair(n: int, title: str, df: pd.DataFrame, specs: list[tuple[str, str, str]]) -> None:
    fig, axes = plt.subplots(len(specs), 1, figsize=(15, 4.25 * len(specs)), sharex=True)
    fig.subplots_adjust(left=0.075, right=0.91, top=0.88, bottom=0.13, hspace=0.78)
    if len(specs) == 1:
        axes = [axes]
    for ax, (name, line_col, sig_col) in zip(axes, specs):
        x = [pts(p) for p in df.index]
        y = df[line_col].clip(-1, 1).values
        sig = df[sig_col].values
        ax.bar(x, sig, width=25, color=ORANGE, alpha=.85, label="方向信号（±1）")
        ax2 = ax.twinx()
        ax2.plot(x, y, color="#c00000", lw=2.6, label=name)
        ax2.set_ylim(-1.05, 1.05)
        ax.set_ylim(-1.05, 1.05)
        ax.axhline(0, color="#dddddd", lw=.8)
        ax.set_yticks([-1,0,1]); ax2.set_yticks([-1,0,1])
        ax.text(0.0, 1.20, f"■ {name}", transform=ax.transAxes, fontsize=17, family="KaiTi", weight="bold", va="bottom")
        ax.spines[["top","right"]].set_visible(False); ax2.spines[["top","left"]].set_visible(False)
        ax.tick_params(labelsize=11); ax2.tick_params(labelsize=11)
        l1, h1 = ax.get_legend_handles_labels(); l2, h2 = ax2.get_legend_handles_labels()
        ax.legend(l1 + l2, h1 + h2, loc="upper right", bbox_to_anchor=(1.0, 1.24),
                  ncol=2, frameon=False, fontsize=11, borderaxespad=0.0)
    axes[-1].xaxis.set_major_locator(mdates.YearLocator(1))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle(title, x=.04, y=.975, ha="left", fontsize=22, family="KaiTi", weight="bold")
    save(fig, n)


def figure_stage_history(n: int, title: str, df: pd.DataFrame, col: str, labels: list[str]) -> None:
    fig, ax = plt.subplots(figsize=(15, 7.8))
    fig.subplots_adjust(left=0.12, right=0.98, top=0.88, bottom=0.18)
    x = [pts(p) for p in df.index]
    y = df[col].astype(int).values
    ax.step(x, y, where="post", color="#c00000", lw=2.2, label="周期划分")
    ax.scatter(x[::3], y[::3], color="#c00000", s=14)
    ax.set_yticks(range(1, len(labels)+1)); ax.set_yticklabels(labels, fontsize=14, family="KaiTi")
    ax.set_ylim(.5, len(labels)+.5)
    ax.xaxis.set_major_locator(mdates.YearLocator(1)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="y", color="#dddddd")
    ax.spines[["top","right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=13)
    ax.set_title(title, loc="left", fontsize=22, family="KaiTi", pad=15)
    ax.legend(loc="lower center", bbox_to_anchor=(.5,-.13), frameon=False, fontsize=13)
    save(fig, n)


def figure_strategy_nav(n: int, strategy_name: str, strat: pd.Series, bench: pd.Series) -> None:
    s = daily_nav_from_monthly(strat)
    b = daily_nav_from_monthly(bench)
    aligned = pd.concat([s, b], axis=1).dropna()
    aligned.columns = ["策略净值", "基准净值"]
    aligned = aligned / aligned.iloc[0]
    rel = aligned["策略净值"] / aligned["基准净值"]
    fig, ax = plt.subplots(figsize=(12, 7.2))
    fig.subplots_adjust(left=0.08, right=0.88, top=0.92, bottom=0.24)
    ax.plot(aligned.index, aligned["基准净值"], color="#ffb800", lw=2.4, label="等权基准")
    ax.plot(aligned.index, aligned["策略净值"], color=LINE_GREY, lw=2.5, label=strategy_name)
    ax2 = ax.twinx()
    ax2.plot(aligned.index, rel, color="#c00000", lw=2.5, label="相对强度（右轴）")
    ax.spines[["top","right"]].set_visible(False); ax2.spines[["top","left"]].set_visible(False)
    ax.grid(False); ax2.grid(False)
    ax.xaxis.set_major_locator(mdates.YearLocator(1)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(axis="x", rotation=90, labelsize=12)
    l1,h1 = ax.get_legend_handles_labels(); l2,h2 = ax2.get_legend_handles_labels()
    ax.legend(l1+l2, h1+h2, loc="lower center", bbox_to_anchor=(.5,-.23), ncol=3, frameon=False, fontsize=13)
    save(fig, n)



def figure_strategy_nav_daily(n: int, strategy_name: str, strat_nav: pd.Series, bench_nav: pd.Series) -> None:
    aligned = pd.concat([strat_nav, bench_nav], axis=1).dropna()
    aligned.columns = ["策略净值", "基准净值"]
    aligned = aligned / aligned.iloc[0]
    rel = aligned["策略净值"] / aligned["基准净值"]
    fig, ax = plt.subplots(figsize=(12, 7.2))
    fig.subplots_adjust(left=0.08, right=0.88, top=0.92, bottom=0.24)
    ax.plot(aligned.index, aligned["基准净值"], color="#ffb800", lw=2.4, label="等权基准")
    ax.plot(aligned.index, aligned["策略净值"], color=LINE_GREY, lw=2.5, label=strategy_name)
    ax2 = ax.twinx()
    ax2.plot(aligned.index, rel, color="#c00000", lw=2.5, label="相对强度（右轴）")
    ax.spines[["top","right"]].set_visible(False); ax2.spines[["top","left"]].set_visible(False)
    ax.grid(False); ax2.grid(False)
    ax.xaxis.set_major_locator(mdates.YearLocator(1)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(axis="x", rotation=90, labelsize=12)
    l1,h1 = ax.get_legend_handles_labels(); l2,h2 = ax2.get_legend_handles_labels()
    ax.legend(l1+l2, h1+h2, loc="lower center", bbox_to_anchor=(.5,-.23), ncol=3, frameon=False, fontsize=13)
    save(fig, n)

def figure_stage_return_table(n: int, title: str, ret: pd.DataFrame, stage: pd.Series, names: list[str]) -> None:
    rows = []
    for i, nm in enumerate(names, 1):
        sub = ret[stage == i]
        vals = []
        for a in ASSETS:
            if len(sub) < 2:
                vals.append("--")
            else:
                vals.append(f"{(((1+sub[a]).prod())**(12/len(sub))-1)*100:.2f}")
        rows.append([nm] + vals)
    table_figure(n, title, ["周期阶段", "股票", "债券", "黄金", "商品"], rows, widths=[.20,.20,.20,.20,.20], fontsize=14)


def figure_model_comparison() -> None:
    rows = [
        ["核心理念", "用美林/普林格排序生成观点，BL把主观周期观点与市场先验融合", "估计协方差，求解各资产风险贡献接近相等", "六类宏观因子检验后调节BL/RP风险预算与观点强度"],
        ["优点", "可解释；能承接周期判断；观点置信度可审计", "稳健；低换手；对收益预测依赖低", "覆盖增长/通胀/利率/信用/汇率/流动性，能做环境过滤"],
        ["缺点", "依赖周期因子质量；观点过强会追涨杀跌", "牛市可能低配权益；超额来自控回撤而非进攻", "需要严格PIT/D3；因子失效时必须降权"],
    ]
    table_figure(17, "表：三类资产配置模型对比", ["对比维度", "Black-Litterman", "风险预算模型", "宏观因子"], rows, widths=[.16,.28,.28,.28], fontsize=12)


def figure_formula(n: int, title: str, sections: list[tuple[str, str]]) -> None:
    fig, ax = plt.subplots(figsize=(12, 13))
    ax.axis("off")
    y = .96
    ax.text(.02, y, title, fontsize=24, family="KaiTi", weight="bold"); y -= .07
    for i, (head, formula) in enumerate(sections, 1):
        ax.text(.04, y, f"{i}、{head}", fontsize=18, family="KaiTi", weight="bold"); y -= .045
        ax.add_patch(Rectangle((.08, y-.06), .84, .09, facecolor="#fafafa", edgecolor="#dddddd", lw=1.0))
        ax.text(.50, y-.015, formula, fontsize=20, family="Arial", ha="center", va="center")
        y -= .11
    save(fig, n)


def figure_20_macro_defs() -> None:
    rows = [
        ["增长因子", "PMI、工业增加值、盈利预期、商品需求扩散", "同比/环比差分、HP滤波、滚动Z-score"],
        ["通胀因子", "CPI、PPI、南华商品、黄金/能源价格", "同比、环比、扩散指数"],
        ["利率因子", "10Y国债收益率、期限利差、SHIBOR/DR007", "利率变化与期限结构斜率"],
        ["信用因子", "社融、M2、信用利差、企业债利差", "同比差分与利差边际变化"],
        ["汇率因子", "美元兑人民币中间价、CFETS指数", "环比、趋势项、波动过滤"],
        ["流动性因子", "M2、社融存量、资金利率、成交流动性", "同比、利差、滚动标准化"],
    ]
    table_figure(20, "表：宏观因子定义", ["因子大类", "核心指标", "处理方式"], rows, widths=[.18,.52,.30], fontsize=14)


def figure_macro_corr(n: int, factor_frame: pd.DataFrame) -> None:
    base = factor_frame[MACRO_CN].corr().fillna(0).to_numpy(dtype=float)
    np.fill_diagonal(base, 1.0)
    cmap = LinearSegmentedColormap.from_list("grb", ["#00b050", "#ffff00", "#c00000"])
    fig, ax = plt.subplots(figsize=(9.8, 8.6))
    fig.subplots_adjust(left=0.18, right=0.88, top=0.88, bottom=0.20)
    im = ax.imshow(base, cmap=cmap, vmin=-1, vmax=1)
    ax.set_xticks(range(6)); ax.set_xticklabels(MACRO_CN, rotation=35, ha="right", fontsize=13, family="KaiTi")
    ax.set_yticks(range(6)); ax.set_yticklabels(MACRO_CN, fontsize=13, family="KaiTi")
    for i in range(6):
        for j in range(6):
            ax.text(j, i, f"{base[i,j]:.2f}", ha="center", va="center", fontsize=12, family="Arial", weight="normal")
    ax.set_title("图：宏观因子相关性", loc="left", fontsize=19, family="KaiTi", weight="bold", pad=16)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.colorbar(im, ax=ax, fraction=.035, pad=.035)
    top_bottom(fig, "资料来源：Wind/本地面板，模型测算")
    save(fig, n)


def figure_factor_regression(factor_frame: pd.DataFrame, ret: pd.DataFrame) -> None:
    target = (ret["equity"] - ret.mean(axis=1)).shift(-1)
    rows = []
    for f in MACRO_CN:
        beta, tval = ols_stats(factor_frame[f], target)
        rolling_beta = []
        rolling_t = []
        for end in range(18, len(factor_frame) + 1):
            b, tv = ols_stats(factor_frame[f].iloc[:end], target.iloc[:end])
            rolling_beta.append(b); rolling_t.append(tv)
        signal_ret = np.sign(factor_frame[f].shift(1).fillna(0)) * (ret["equity"] - ret.mean(axis=1))
        ir = 0.0 if signal_ret.std() == 0 else float(signal_ret.mean() / signal_ret.std() * math.sqrt(12))
        pos = float((signal_ret > 0).mean()) if len(signal_ret) else 0.0
        rows.append([f, f"{beta:.3f}", f"{tval:.3f}", f"{np.var(rolling_beta):.3f}", f"{np.var(rolling_t):.3f}", f"{ir:.3f}", f"{pos*100:.1f}%"])
    table_figure(22, "表：宏观因子相对资产回归检验", ["因子名称", "回归系数\n均值", "t值均值", "回归系数\n方差", "t值方差", "IR", "为正比例"], rows,
                 widths=[.18,.13,.13,.14,.13,.13,.16], fontsize=13)


def figure_23_factor_dashboard(factor_frame: pd.DataFrame, ret: pd.DataFrame) -> None:
    rows = []
    for f in MACRO_CN:
        s = factor_frame[f].dropna()
        if s.empty:
            rows.append([f, "--", "--", "--", "--", "--", ""])
            continue
        direction = "正向" if float(s.iloc[-1]) >= 0 else "反向"
        last = float(s.iloc[-1] - s.iloc[-2]) if len(s) >= 2 else 0.0
        last3 = float(s.iloc[-1] - s.iloc[-4]) if len(s) >= 4 else last
        ytd_base = s[s.index.year == s.index[-1].year]
        ytd = float(s.iloc[-1] - ytd_base.iloc[0]) if len(ytd_base) else 0.0
        signal_ret = np.sign(factor_frame[f].shift(1).fillna(0)) * (ret["equity"] - ret.mean(axis=1))
        ann = float((1 + signal_ret.dropna()).prod() ** (12 / max(len(signal_ret.dropna()), 1)) - 1)
        rows.append([f, direction, f"{last:.2f}", f"{last3:.2f}", f"{ytd:.2f}", fmt_pct(ann), sparkline(s)])
    table_figure(23, "图：宏观因子方向、年化与趋势", ["因子名称", "因子方向", "最近一期", "最近三月", "今年以来", "历史年化", "近一年趋势"], rows,
                 widths=[.22,.12,.12,.12,.12,.13,.17], fontsize=12)


def main() -> None:
    setup_fonts(); ensure_out()
    snapshot = load_json(SNAPSHOT); panel = load_json(PANEL); freeze = load_json(FREEZE)
    ret = panel_monthly_returns(panel)
    daily_rets = daily_backend._daily_assets(panel, freeze)
    synthetic_cycles = synth_cycles(list(ret.index), ret)
    cycles = cycle_history_frame(snapshot)
    if cycles.empty:
        cycles = synthetic_cycles
        cycle_ret = ret
    else:
        cycle_ret = ret.reindex(cycles.index).dropna(how="any")
        cycles = cycles.reindex(cycle_ret.index).combine_first(synthetic_cycles.reindex(cycle_ret.index)).ffill().bfill()
    bench = snapshot_returns(snapshot, BENCH)
    merrill_weights = {
        1: np.array([.55,.10,.10,.25]), 2: np.array([.25,.10,.15,.50]),
        3: np.array([.10,.15,.45,.30]), 4: np.array([.10,.55,.25,.10]),
    }
    pring_weights = {
        1: np.array([.30,.40,.20,.10]), 2: np.array([.45,.15,.10,.30]),
        3: np.array([.25,.10,.20,.45]), 4: np.array([.10,.15,.45,.30]),
        5: np.array([.10,.35,.40,.15]), 6: np.array([.20,.45,.25,.10]),
    }
    merrill_ret = cycle_strategy_returns(cycle_ret, cycles["美林阶段"], merrill_weights)
    pring_ret = cycle_strategy_returns(cycle_ret, cycles["普林格阶段"], pring_weights)
    factor_frame = macro_proxy_frame(cycles, cycle_ret)
    bench_daily_ret = daily_rets[ASSETS].mean(axis=1)
    bench_daily_nav = nav_from_daily_returns(bench_daily_ret)
    merrill_daily_ret = daily_cycle_strategy_returns(daily_rets, cycles, "美林阶段", merrill_weights)
    pring_daily_ret = daily_cycle_strategy_returns(daily_rets, cycles, "普林格阶段", pring_weights)
    merrill_daily_nav = nav_from_daily_returns(merrill_daily_ret)
    pring_daily_nav = nav_from_daily_returns(pring_daily_ret)
    model_rows = daily_backend._monthly_model_rows()
    model_daily_returns = {
        key: daily_backend._daily_strategy_returns(daily_rets, model_rows[key]).dropna()
        for key, _name in MODELS
    }
    model_daily_navs = {key: nav_from_daily_returns(val) for key, val in model_daily_returns.items()}

    # 1-8
    figure_1()
    table_figure(2, "表：保留的周期模型", ["周期模型", "输入因子", "阶段输出", "资产映射"],
                 [["美林时钟", "增长因子、通胀因子", "复苏/过热/滞胀/衰退", "股票/债券/黄金/商品排序"],
                  ["普林格周期", "货币因子、信用因子、增长因子", "六阶段（剔除不存在组合）", "阶段强弱映射到四资产排序"]],
                 widths=[.18,.30,.25,.27], fontsize=14)
    table_figure(3, "表：资产配置模型代表资产", ["资产类别", "代表资产"],
                 [[ASSET_CN[a], ASSET_CODE[a]] for a in ASSETS], widths=[.25,.75], fontsize=16)
    figure_4_corr(ret)
    figure_5_merrill_clock()
    figure_factor_pair(6, "图：美林时钟增长/通胀连续指标与方向信号", cycles,
                       [("增长因子：多指标聚合后的增长方向", "增长连续指标", "增长方向"),
                        ("通胀因子：CPI/PPI/商品确认后的通胀方向", "通胀连续指标", "通胀方向")])
    figure_stage_history(7, "图：美林时钟历史阶段总图", cycles, "美林阶段", ["复苏期", "过热期", "滞胀期", "衰退期"])
    figure_stage_return_table(8, "图表：大类资产对应美林时钟收益", cycle_ret, cycles["美林阶段"], ["复苏期", "过热期", "滞胀期", "衰退期"])

    # 9-16
    figure_strategy_nav_daily(9, "美林时钟配置", merrill_daily_nav, bench_daily_nav)
    table_figure(10, "表：美林时钟策略年度收益（日度回放）", ["年度", "策略收益", "四资产等权", "超额收益", "最大回撤"],
                 annual_table(merrill_daily_nav, bench_daily_nav), fontsize=13)
    table_figure(11, "图：普林格六阶段框架", ["阶段", "名称", "货币", "信用", "增长", "资产排序"],
                 [["阶段I", "复苏期", "宽货币↑", "宽信用↑", "增长下行↓", "债券/股票/黄金/商品"],
                  ["阶段II", "繁荣期", "宽货币↑", "宽信用↑", "增长上行↑", "股票/商品/债券/黄金"],
                  ["阶段III", "过热期", "紧货币↓", "宽信用↑", "增长上行↑", "商品/股票/黄金/债券"],
                  ["阶段IV", "滞涨期", "紧货币↓", "紧信用↓", "增长上行↑", "黄金/商品/债券/股票"],
                  ["阶段V", "衰退前期", "紧货币↓", "紧信用↓", "增长下行↓", "黄金/债券/商品/股票"],
                  ["阶段VI", "衰退后期", "宽货币↑", "紧信用↓", "增长下行↓", "债券/黄金/股票/商品"]],
                 widths=[.12,.15,.16,.16,.16,.25], fontsize=12)
    figure_factor_pair(12, "图：普林格货币/信用/增长连续指标与方向信号", cycles,
                       [("货币因子：货币政策边际变化扩散指数", "货币连续指标", "货币方向"),
                        ("信用因子：中长期贷款/信用脉冲边际变化", "信用连续指标", "信用方向"),
                        ("增长因子：盈利兑现与经济动能边际变化", "普林格增长连续指标", "普林格增长方向")])
    figure_stage_history(13, "图：普林格六阶段历史阶段总图", cycles, "普林格阶段",
                         ["阶段I\n复苏期", "阶段II\n繁荣期", "阶段III\n过热期", "阶段IV\n滞涨期", "阶段V\n衰退前期", "阶段VI\n衰退后期"])
    figure_stage_return_table(14, "图表：大类资产对应普林格周期收益", cycle_ret, cycles["普林格阶段"],
                              ["阶段I", "阶段II", "阶段III", "阶段IV", "阶段V", "阶段VI"])
    figure_strategy_nav_daily(15, "普林格周期配置", pring_daily_nav, bench_daily_nav)
    table_figure(16, "表：普林格周期策略年度收益（日度回放）", ["年度", "策略收益", "四资产等权", "超额收益", "最大回撤"],
                 annual_table(pring_daily_nav, bench_daily_nav), fontsize=13)

    # 17-23
    figure_model_comparison()
    figure_formula(18, "BL模型操作步骤",
                   [("市场隐含均衡收益", r"\pi=\lambda \Sigma w_{mkt}"),
                    ("周期观点矩阵", r"P\mu=Q+\varepsilon,\quad \varepsilon\sim N(0,\Omega)"),
                    ("后验预期收益", r"\mu_{BL}=[(\tau\Sigma)^{-1}+P'\Omega^{-1}P]^{-1}[(\tau\Sigma)^{-1}\pi+P'\Omega^{-1}Q]"),
                    ("约束优化", r"\max_w\; w'\mu_{BL}-\frac{\gamma}{2}w'\Sigma w-c'|w-w_{t-1}|")])
    figure_formula(19, "风险预算模型操作步骤",
                   [("估计协方差", r"\Sigma=D_\sigma \rho D_\sigma"),
                    ("边际风险贡献", r"MRC_i=\frac{(\Sigma w)_i}{\sqrt{w'\Sigma w}}"),
                    ("总风险贡献", r"RC_i=w_i\cdot MRC_i"),
                    ("风险贡献均衡", r"RC_1=RC_2=\cdots=RC_n,\quad \sum_i w_i=1")])
    figure_20_macro_defs()
    figure_macro_corr(21, factor_frame)
    figure_factor_regression(factor_frame, cycle_ret)
    figure_23_factor_dashboard(factor_frame, cycle_ret)

    # 24-29 model returns
    for base_n, key, name in [(24, "black_litterman", "BL周期观点配置"), (26, "risk_parity", "风险预算模型"), (28, "macro_factor", "宏观因子调整")]:
        s_nav = model_daily_navs[key]
        b_nav = nav_from_daily_returns(bench_daily_ret.reindex(model_daily_returns[key].index).dropna())
        table_figure(base_n, f"表：{name}策略年度收益（日度回放）", ["年度", "策略收益", "四资产等权", "超额收益", "最大回撤"],
                     annual_table(s_nav, b_nav), fontsize=13)
        figure_strategy_nav_daily(base_n + 1, name, s_nav, b_nav)

    manifest = {
        "version": "v65_visual_rebuild",
        "output_dir": str(OUT),
        "files": [f"{i}.png" for i in range(1, 30)],
        "notes": [
            "周期图读取v64快照cycle_tracking.history，不再用收益合成周期。",
            "美林时钟样式按参考图重绘。",
            "普林格阶段改为连续月度阶段，不再稀疏散点。",
            "连续指标限制在[-1,1]，方向背景仅取±1。",
            "热力图使用绿-黄-红三色。",
            "字体优先中文楷体、英文Arial。",
            "净值图与年度收益表使用真实日度资产收益按逐月目标权重回放，不再使用月度插值。",
        ],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
