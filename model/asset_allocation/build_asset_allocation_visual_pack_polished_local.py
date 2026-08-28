# -*- coding: utf-8 -*-
"""
璧勪骇閰嶇疆鏈湴鍥剧墖閲嶇粯鑴氭湰锛堝彧杈撳嚭 PNG锛屼笉鐢熸垚 PPT/Excel锛夈€?
鐩爣锛?1. 淇涓婁竴鐗堝浘鐗囦腑鏂囧瓧婧㈠嚭銆侀噸鍙犮€佸瓧浣?鑹茬郴涓嶄竴鑷寸殑闂锛?2. 鏅灄鏍?缇庢灄闃舵蹇呴』姣忎釜鏈堥兘鏈夐樁娈碉紝涓嶈兘鐢绘垚闆舵暎鐐癸紱
3. 鍥犲瓙鏂瑰悜鍥撅細姗欒壊鑳屾櫙涓?卤1 绂绘暎鏂瑰悜锛岀孩绾夸负杩炵画骞虫粦鎸囨爣锛?4. 鐩稿叧鎬х儹鍔涘浘浣跨敤鍒稿晢鎶ュ憡寮忕孩/榛?缁夸笁鑹诧紱
5. 鍥炴祴鍑€鍊煎浘浣跨敤鏃ュ害鏀剁泭锛岀瓥鐣?鍩哄噯宸﹁酱锛岀浉瀵瑰己搴﹀彸杞淬€?"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import FancyArrow, Rectangle, Wedge


ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = ROOT / "agent" / "model" / "asset_allocation"
OUT = Path(r"C:\Users\Rye\Desktop\璧勪骇閰嶇疆")

SNAPSHOT = ROOT / "agent" / "output" / "model_improvement" / "asset_allocation_snapshot_v64_daily_excess_governed.json"
PANEL = ROOT / "agent" / "output" / "model_improvement" / "asset_allocation_panel_v553_t2_self_financing.json"
FREEZE = ROOT / "agent" / "output" / "model_improvement" / "asset_allocation_rqdata_freeze_v541.json"

ASSET_ORDER = ["equity", "bond", "gold", "commodity"]
ASSET_LABELS = {
    "equity": "鑲＄エ",
    "bond": "鍊哄埜",
    "gold": "榛勯噾",
    "commodity": "鍟嗗搧",
}
ASSET_CODES = {
    "equity": "娌繁300ETF锛?10300.SH锛? 娌繁300鍏ㄦ敹鐩婏紙H00300.INDX锛?,
    "bond": "鍗佸勾鍥藉€篍TF锛?11260.SH锛? 涓瘉鍥藉€烘敹鐩婏紙H11006.XSHG锛?,
    "gold": "榛勯噾ETF锛?18880.SH锛? 涓婃捣閲慉u99.99锛圓U9999.SGEX锛?,
    "commodity": "闈炶吹閲戝睘鏈熻揣鑷瀺璧勭瀛愶紙A/AL/C/CF/CU/J/L/M/P/RB/RU/SR/TA/V/Y/ZN锛?,
}
MODEL_NAMES = {
    "black_litterman": "鍛ㄦ湡瑙傜偣BL妯″瀷",
    "risk_parity": "椋庨櫓骞充环妯″瀷",
    "macro_factor": "瀹忚鍥犲瓙澧炲己妯″瀷",
}

RED = "#B21B12"
DARK_RED = "#8B1A10"
LINE_RED = "#C00000"
ORANGE = "#F4B183"
ORANGE_LIGHT = "#FCE4D6"
CREAM = "#FFF2CC"
GREY = "#D9D9D9"
LIGHT_GREY = "#EFEFEF"
YELLOW = "#FFC000"
MID_YELLOW = "#F6E58D"
GREEN = "#93C47D"
BLUE = "#1F62B5"
BLACK = "#111111"


def _setup_fonts() -> None:
    """缁熶竴涓枃妤蜂綋銆佽嫳鏂?Arial銆?""
    candidates = [
        Path(r"C:\Windows\Fonts\simkai.ttf"),
        Path(r"C:\Windows\Fonts\STKAITI.TTF"),
        Path(r"C:\Windows\Fonts\kaiu.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                font_manager.fontManager.addfont(str(path))
            except Exception:
                pass
    plt.rcParams.update(
        {
            "font.family": ["KaiTi", "SimKai", "STKaiti", "Arial"],
            "font.sans-serif": ["KaiTi", "SimKai", "STKaiti", "Arial"],
            "axes.unicode_minus": False,
            "figure.dpi": 140,
            "savefig.dpi": 220,
        }
    )


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _canonical_write_json(path: Path, obj: Any) -> None:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")


def _safe_prepare_output() -> None:
    expected = Path(r"C:\Users\Rye\Desktop\璧勪骇閰嶇疆").resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    actual = OUT.resolve()
    if str(actual).lower() != str(expected).lower():
        raise RuntimeError(f"杈撳嚭鐩綍寮傚父锛屾嫆缁濊鐩栵細{actual}")
    for p in OUT.glob("*.png"):
        if p.is_file():
            p.unlink()


def _save(fig: plt.Figure, n: int) -> None:
    fig.savefig(OUT / f"{n}.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _month_to_ts(month: str | int) -> pd.Timestamp:
    s = str(month)
    if "-" in s:
        return pd.to_datetime(s)
    return pd.to_datetime(s + "01", format="%Y%m%d")


def _monthly_to_period_end(month: str | int) -> pd.Timestamp:
    return _month_to_ts(month) + pd.offsets.MonthEnd(0)


def _normalize_continuous(s: pd.Series, smooth: int = 3) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce").astype(float)
    x = x.replace([np.inf, -np.inf], np.nan).ffill().bfill()
    if len(x) >= smooth:
        x = x.rolling(smooth, min_periods=1).mean()
    lo, hi = x.quantile(0.02), x.quantile(0.98)
    if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
        x = x.clip(lo, hi)
    scale = max(float(x.abs().quantile(0.95)), 1e-9)
    return (x / scale).clip(-1.0, 1.0)


def _direction_from_cont(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce").ffill().bfill()
    return pd.Series(np.where(x >= 0, 1, -1), index=s.index)


def _collapse_one_month_flips(stages: list[str]) -> list[str]:
    out = list(stages)
    for i in range(1, len(out) - 1):
        if out[i - 1] == out[i + 1] and out[i] != out[i - 1]:
            out[i] = out[i - 1]
    return out


def _enrich_cycle_history(hist: pd.DataFrame) -> pd.DataFrame:
    h = hist.copy()
    h["date"] = h["month"].map(_monthly_to_period_end)
    h = h.sort_values("date").reset_index(drop=True)

    # 澧為暱/閫氳儉/璐у竵/淇＄敤鍧囦娇鐢ㄨ繛缁寚鏍囧仛鍒ゆ柇锛屾柟鍚戜俊鍙峰彧浣滀负绂绘暎鑳屾櫙銆?    h["merrill_growth_cont"] = _normalize_continuous(h.get("merrill_growth_score", h.get("growth_score", 0.0)))
    h["merrill_inflation_cont"] = _normalize_continuous(h.get("merrill_inflation_score", h.get("inflation_score", 0.0)))
    h["pring_money_cont"] = _normalize_continuous(h.get("money_score", h.get("liquidity_score", h["merrill_growth_cont"])))
    h["pring_credit_cont"] = _normalize_continuous(h.get("credit_score", h.get("credit_impulse", h["merrill_growth_cont"])))
    h["pring_growth_cont"] = _normalize_continuous(h.get("growth_score", h.get("merrill_growth_score", h["merrill_growth_cont"])))

    h["merrill_growth_dir"] = _direction_from_cont(h["merrill_growth_cont"])
    h["merrill_inflation_dir"] = _direction_from_cont(h["merrill_inflation_cont"])
    h["pring_money_dir"] = _direction_from_cont(h["pring_money_cont"])
    h["pring_credit_dir"] = _direction_from_cont(h["pring_credit_cont"])
    h["pring_growth_dir"] = _direction_from_cont(h["pring_growth_cont"])

    merrill = []
    for g, inf in zip(h["merrill_growth_dir"], h["merrill_inflation_dir"]):
        if g >= 0 and inf < 0:
            merrill.append("recovery")
        elif g >= 0 and inf >= 0:
            merrill.append("overheat")
        elif g < 0 and inf >= 0:
            merrill.append("stagflation")
        else:
            merrill.append("recession")
    h["merrill_stage_fixed"] = _collapse_one_month_flips(merrill)

    valid = {
        (1, 1, -1): "I_recovery",
        (1, 1, 1): "II_prosperity",
        (-1, 1, 1): "III_overheat",
        (-1, -1, 1): "IV_stagflation",
        (-1, -1, -1): "V_early_recession",
        (1, -1, -1): "VI_late_recession",
    }
    pring: list[str] = []
    last = "II_prosperity"
    for m, c, g in zip(h["pring_money_dir"], h["pring_credit_dir"], h["pring_growth_dir"]):
        key = (int(m), int(c), int(g))
        if key in valid:
            last = valid[key]
        # 涓や釜鐞嗚涓婁笉瀛樺湪鐨勭粍鍚堜笉寮鸿閫犻樁娈碉紝娌跨敤涓婁竴鏈夋晥闃舵锛屼繚璇佹椂闂存杩炵画銆?        pring.append(last)
    h["pring_stage_fixed"] = _collapse_one_month_flips(pring)
    return h


def _daily_assets(panel: dict[str, Any], freeze: dict[str, Any]) -> pd.DataFrame:
    frames: dict[str, pd.Series] = {}

    # 鏉冪泭銆佸€哄埜銆侀粍閲戯細浼樺厛浣跨敤鍐荤粨鐨勬潈濞佹棩棰戞按骞炽€?    blocks = freeze.get("asset_blocks", {})
    for key in ["equity", "bond", "gold"]:
        block = blocks.get(key, {})
        rows = block.get("daily", [])
        if rows:
            df = pd.DataFrame(rows)
            date_col = "date" if "date" in df.columns else "trade_date"
            value_col = "close" if "close" in df.columns else "level"
            ser = pd.Series(pd.to_numeric(df[value_col], errors="coerce").values, index=pd.to_datetime(df[date_col]))
            frames[key] = ser.sort_index()

    # 鍟嗗搧锛氫娇鐢?v553 鑷瀺璧勬棩 NAV銆?    ledger = panel.get("commodity", {}).get("daily_ledger", [])
    if ledger:
        df = pd.DataFrame(ledger)
        value_col = "end_nav" if "end_nav" in df.columns else "nav"
        frames["commodity"] = pd.Series(pd.to_numeric(df[value_col], errors="coerce").values, index=pd.to_datetime(df["date"])).sort_index()

    if set(frames) != set(ASSET_ORDER):
        missing = sorted(set(ASSET_ORDER) - set(frames))
        raise RuntimeError(f"缂哄皯鍥涜祫浜ф棩棰戝簭鍒楋細{missing}")

    levels = pd.DataFrame(frames).sort_index().ffill()
    levels = levels.loc[levels.index >= pd.Timestamp("2015-01-01")]
    returns = levels.pct_change().dropna(how="any")
    return returns[ASSET_ORDER]


def _model_rows(snapshot: dict[str, Any], panel: dict[str, Any]) -> dict[str, pd.DataFrame]:
    sys.path.insert(0, str(MODEL_DIR))
    import backtest_asset_allocation_v61_governed as v61  # type: ignore
    import backtest_asset_allocation_v63_macro_governed as v63  # type: ignore
    import build_snapshot_v64_daily_excess_governed as v64  # type: ignore

    panel_data = v61._validate_panel(panel)
    rows: dict[str, pd.DataFrame] = {}
    for mode in MODEL_NAMES:
        model_rows = v64._simulate_model(snapshot, panel_data, mode)
        rows[mode] = pd.DataFrame(model_rows)
    return rows


def _weights_from_row(row: pd.Series) -> np.ndarray:
    w = row.get("weights", {})
    if isinstance(w, str):
        w = json.loads(w)
    return np.array([float(w.get(a, 0.0)) for a in ASSET_ORDER], dtype=float)


def _daily_strategy_returns(daily: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    model = rows.copy()
    model["signal_date"] = model["signal_month"].map(_monthly_to_period_end)
    model = model.sort_values("signal_date")

    out = pd.Series(0.0, index=daily.index)
    current_w = np.repeat(0.25, 4)
    row_idx = 0
    active_month: str | None = None

    for date in daily.index:
        while row_idx < len(model) and date > model.iloc[row_idx]["signal_date"]:
            current_w = _weights_from_row(model.iloc[row_idx])
            active_month = str(model.iloc[row_idx]["realized_month"])
            row_idx += 1
        out.loc[date] = float(np.dot(current_w, daily.loc[date].values))
        # 鏈堝害鎹粨鎴愭湰宸茬粡鍦ㄦā鍨嬪眰璁＄畻锛屾澶勫彧璐熻矗鏃ラ鍑€鍊煎舰鎬併€?        _ = active_month
    return out


MERRILL_STAGE_LABELS = {
    "recovery": "澶嶈嫃鏈?,
    "overheat": "杩囩儹鏈?,
    "stagflation": "婊炴定鏈?,
    "recession": "琛伴€€鏈?,
}
PRING_STAGE_LABELS = {
    "I_recovery": "闃舵I\n澶嶈嫃鏈?,
    "II_prosperity": "闃舵II\n绻佽崳鏈?,
    "III_overheat": "闃舵III\n杩囩儹鏈?,
    "IV_stagflation": "闃舵IV\n婊炴定鏈?,
    "V_early_recession": "闃舵V\n琛伴€€鍓嶆湡",
    "VI_late_recession": "闃舵VI\n琛伴€€鍚庢湡",
}


MERRILL_WEIGHTS = {
    "recovery": {"equity": 0.50, "commodity": 0.25, "bond": 0.15, "gold": 0.10},
    "overheat": {"commodity": 0.45, "equity": 0.25, "gold": 0.20, "bond": 0.10},
    "stagflation": {"gold": 0.45, "bond": 0.25, "commodity": 0.20, "equity": 0.10},
    "recession": {"bond": 0.50, "gold": 0.25, "equity": 0.15, "commodity": 0.10},
}
PRING_WEIGHTS = {
    "I_recovery": {"bond": 0.40, "equity": 0.25, "gold": 0.20, "commodity": 0.15},
    "II_prosperity": {"equity": 0.50, "commodity": 0.25, "bond": 0.15, "gold": 0.10},
    "III_overheat": {"commodity": 0.45, "equity": 0.25, "gold": 0.20, "bond": 0.10},
    "IV_stagflation": {"gold": 0.40, "bond": 0.30, "commodity": 0.20, "equity": 0.10},
    "V_early_recession": {"bond": 0.40, "gold": 0.35, "equity": 0.15, "commodity": 0.10},
    "VI_late_recession": {"bond": 0.45, "equity": 0.25, "gold": 0.20, "commodity": 0.10},
}


def _weights_to_vec(weights: dict[str, float]) -> np.ndarray:
    return np.array([float(weights.get(a, 0.0)) for a in ASSET_ORDER], dtype=float)


def _daily_cycle_returns(daily: pd.DataFrame, hist: pd.DataFrame, stage_col: str, weight_map: dict[str, dict[str, float]]) -> pd.Series:
    h = hist[["date", stage_col]].dropna().sort_values("date").copy()
    out = pd.Series(index=daily.index, dtype=float)
    idx = 0
    current = _weights_to_vec(weight_map[h.iloc[0][stage_col]])
    for date in daily.index:
        while idx + 1 < len(h) and date > h.iloc[idx + 1]["date"]:
            idx += 1
            current = _weights_to_vec(weight_map.get(h.iloc[idx][stage_col], weight_map[h.iloc[0][stage_col]]))
        out.loc[date] = float(np.dot(current, daily.loc[date].values))
    return out.dropna()


def _nav(ret: pd.Series) -> pd.Series:
    return (1.0 + ret.fillna(0.0)).cumprod()


def _annual_table(strategy: pd.Series, benchmark: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"strategy": strategy, "benchmark": benchmark}).dropna()
    rows = []
    for year, g in df.groupby(df.index.year):
        if year < 2017:
            continue
        if year == df.index.year.max():
            label = f"{year}YTD"
        else:
            label = str(year)
        sr = (1 + g["strategy"]).prod() - 1
        br = (1 + g["benchmark"]).prod() - 1
        excess = (1 + sr) / (1 + br) - 1
        nav = _nav(g["strategy"])
        mdd = (nav / nav.cummax() - 1).min()
        rows.append([label, sr, br, excess, mdd])
    total_s = (1 + df["strategy"]).prod() ** (252 / max(len(df), 1)) - 1
    total_b = (1 + df["benchmark"]).prod() ** (252 / max(len(df), 1)) - 1
    total_ex = (1 + total_s) / (1 + total_b) - 1
    mdd = (_nav(df["strategy"]) / _nav(df["strategy"]).cummax() - 1).min()
    rows.append(["鍖洪棿骞村寲", total_s, total_b, total_ex, mdd])
    return pd.DataFrame(rows, columns=["骞村害", "绛栫暐鏀剁泭", "绛夋潈鍩哄噯", "瓒呴鏀剁泭", "鏈€澶у洖鎾?])


def _asset_benchmark(daily: pd.DataFrame) -> pd.Series:
    return daily.mean(axis=1)


def _line_table_value(v: float) -> str:
    return f"{v * 100:.1f}%"


def draw_flow(n: int) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    top = [("鍛ㄦ湡璺熻釜", 0.12), ("鍛ㄦ湡鎺掑簭", 0.32), ("BL瑙傜偣", 0.52), ("缁勫悎绾︽潫", 0.72), ("涓夋ā鍨嬭緭鍑?, 0.90)]
    for i, (txt, x) in enumerate(top):
        ax.add_patch(Rectangle((x - 0.055, 0.78), 0.11, 0.07, facecolor="#6D95C8", edgecolor="#385B88", lw=1.6))
        ax.text(x, 0.815, txt, ha="center", va="center", color="white", fontsize=17, weight="bold")
        if i < len(top) - 1:
            ax.add_patch(FancyArrow(x + 0.065, 0.815, 0.13, 0, width=0.008, head_width=0.035, head_length=0.025, color="#6D95C8"))

    blocks = [
        ("缇庢灄鏃堕挓\n澧為暱脳閫氳儉\n鍥涢樁娈?, 0.14, 0.52),
        ("鏅灄鏍煎懆鏈焅n璐у竵脳淇＄敤脳澧為暱\n鍏樁娈?, 0.34, 0.52),
        ("缁煎悎鎺掑簭\n闃舵鏀剁泭妫€楠孿n璧勪骇寮哄急鎵撳垎", 0.54, 0.52),
        ("Black-Litterman\nP/Q/惟瑙傜偣鐭╅樀\n鍚庨獙鏀剁泭", 0.74, 0.52),
        ("椋庨櫓骞充环\n鍗忔柟宸?椋庨櫓璐＄尞\n绾︽潫姹傝В", 0.34, 0.28),
        ("瀹忚鍥犲瓙澧炲己\n鍏淮鍥犲瓙绛涢€塡n瑙傜偣/椋庨櫓棰勭畻璋冩暣", 0.58, 0.28),
        ("鏈€缁堢粍鍚圽n鏉冮噸/鍥炴挙/澶忔櫘\n鏃ラ璺熻釜", 0.82, 0.28),
    ]
    for txt, x, y in blocks:
        ax.add_patch(Rectangle((x - 0.085, y - 0.055), 0.17, 0.11, facecolor="#E8EEF8", edgecolor="#6D95C8", lw=1.5))
        ax.text(x, y, txt, ha="center", va="center", fontsize=14, linespacing=1.35)
    arrows = [(0.225, 0.52, 0.075, 0), (0.425, 0.52, 0.075, 0), (0.625, 0.52, 0.075, 0), (0.74, 0.45, -0.28, -0.12), (0.425, 0.28, 0.08, 0), (0.66, 0.28, 0.08, 0)]
    for x, y, dx, dy in arrows:
        ax.add_patch(FancyArrow(x, y, dx, dy, width=0.006, head_width=0.025, head_length=0.02, color="#6D95C8"))
    ax.text(0.03, 0.52, "鍛ㄦ湡鐞嗚", rotation=90, ha="center", va="center", fontsize=16, color="#385B88", weight="bold")
    ax.text(0.03, 0.28, "閰嶇疆妯″瀷", rotation=90, ha="center", va="center", fontsize=16, color="#385B88", weight="bold")
    _save(fig, n)


def draw_cycle_intro(n: int) -> None:
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.axis("off")
    headers = ["鍛ㄦ湡妯″瀷", "杈撳叆鍥犲瓙", "闃舵鍒掑垎", "璧勪骇鏄犲皠", "鏈郴缁熺敤閫?]
    rows = [
        ["缇庢灄鏃堕挓", "澧為暱銆侀€氳儉\n澶氭寚鏍囪仛鍚?HP/鍌呴噷鍙?婊氬姩妫€楠?, "澶嶈嫃銆佽繃鐑€佹粸娑ㄣ€佽“閫€", "鍥涢樁娈靛搴旇偂绁ㄣ€佸€哄埜銆侀粍閲戙€佸晢鍝佹帓搴?, "杈撳嚭鍛ㄦ湡鎺掑簭锛岃緭鍏L瑙傜偣"],
        ["鏅灄鏍煎懆鏈?, "璐у竵銆佷俊鐢ㄣ€佸闀縗n鏂瑰悜淇″彿+杩炵画鎸囨爣澶嶆牳", "鍏樁娈礬n鍓旈櫎涓や釜鐞嗚涓嶆垚绔嬬粍鍚?, "鍏樁娈靛搴斿洓璧勪骇鎺掑簭\n骞剁粰鍑洪樁娈垫敹鐩婂鐩?, "杈呭姪BL瑙傜偣涓庡懆鏈熸嫨鏃?],
    ]
    x0, y0, w, h = 0.03, 0.15, 0.94, 0.70
    colw = [0.14, 0.26, 0.20, 0.23, 0.17]
    ax.add_patch(Rectangle((x0, y0 + h - 0.12), w, 0.12, facecolor=DARK_RED, edgecolor=DARK_RED))
    x = x0
    for j, head in enumerate(headers):
        ax.text(x + colw[j] * w / 2, y0 + h - 0.06, head, color="white", ha="center", va="center", fontsize=15, weight="bold")
        x += colw[j] * w
    for i, row in enumerate(rows):
        yy = y0 + h - 0.12 - (i + 1) * 0.29
        ax.add_patch(Rectangle((x0, yy), w, 0.29, facecolor="white" if i == 0 else "#F7F7F7", edgecolor="#333333", lw=0.8))
        x = x0
        for j, val in enumerate(row):
            ax.plot([x, x], [yy, yy + 0.29], color="#333333", lw=0.8)
            ax.text(x + 0.01, yy + 0.145, val, ha="left", va="center", fontsize=13, linespacing=1.4)
            x += colw[j] * w
        ax.plot([x0 + w, x0 + w], [yy, yy + 0.29], color="#333333", lw=0.8)
    ax.text(0.03, 0.06, "璧勬枡鏉ユ簮锛歐ind/iFinD/RQData锛屾湰绯荤粺鏁寸悊", fontsize=12, style="italic")
    _save(fig, n)


def draw_asset_table(n: int) -> None:
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.axis("off")
    ax.text(0.02, 0.92, "琛細璧勪骇閰嶇疆妯″瀷浠ｈ〃璧勪骇", fontsize=18, weight="bold")
    ax.plot([0.02, 0.98], [0.84, 0.84], color=DARK_RED, lw=2)
    ax.plot([0.02, 0.98], [0.70, 0.70], color=DARK_RED, lw=1.6)
    ax.plot([0.02, 0.98], [0.17, 0.17], color=DARK_RED, lw=2)
    ax.text(0.15, 0.76, "璧勪骇绫诲埆", fontsize=17, weight="bold", ha="center")
    ax.text(0.60, 0.76, "浠ｈ〃璧勪骇", fontsize=17, weight="bold", ha="center")
    labels = ["鑲＄エ", "鍊哄埜", "榛勯噾", "鍟嗗搧"]
    for i, key in enumerate(ASSET_ORDER):
        y = 0.61 - i * 0.12
        ax.text(0.15, y, labels[i], fontsize=16, weight="bold", ha="center")
        ax.text(0.60, y, ASSET_CODES[key], fontsize=14, ha="center")
    ax.text(0.02, 0.07, "璧勬枡鏉ユ簮锛歐ind銆丷QData銆佷氦鏄撴墍锛屾湰绯荤粺鏁寸悊", fontsize=12, style="italic")
    _save(fig, n)


def draw_corr_heatmap(n: int, corr: pd.DataFrame, title: str = "鍥捐〃锛氱粍鍚堣祫浜ч棿鐩稿叧鎬х郴鏁?) -> None:
    labels = [ASSET_LABELS.get(str(a), str(a)) for a in corr.index]
    data = corr.values.astype(float)
    cmap = LinearSegmentedColormap.from_list("gyr", [GREEN, MID_YELLOW, "#DE6D6D"])
    norm = Normalize(vmin=-1, vmax=1)
    fig, ax = plt.subplots(figsize=(8.8, 6.2))
    ax.axis("off")
    ax.text(0.02, 0.96, title, fontsize=16, weight="bold")
    ax.plot([0.02, 0.98], [0.91, 0.91], color=DARK_RED, lw=1.6)
    left, bottom, cell = 0.17, 0.18, 0.13
    # 琛ㄥご
    for j, lab in enumerate(labels):
        ax.add_patch(Rectangle((left + j * cell, bottom + len(labels) * cell), cell, cell, facecolor=DARK_RED, edgecolor="white", lw=0.8))
        ax.text(left + (j + 0.5) * cell, bottom + (len(labels) + 0.5) * cell, lab, color="white", ha="center", va="center", fontsize=13, weight="bold")
    for i, lab in enumerate(labels):
        y = bottom + (len(labels) - 1 - i) * cell
        ax.add_patch(Rectangle((left - cell, y), cell, cell, facecolor=DARK_RED, edgecolor="white", lw=0.8))
        ax.text(left - 0.5 * cell, y + 0.5 * cell, lab, color="white", ha="center", va="center", fontsize=13, weight="bold")
        for j in range(len(labels)):
            x = left + j * cell
            if j <= i:
                val = data[i, j]
                ax.add_patch(Rectangle((x, y), cell, cell, facecolor=cmap(norm(val)), edgecolor="white", lw=0.8))
                txt = "1" if i == j else f"{val:.2f}"
                ax.text(x + 0.5 * cell, y + 0.5 * cell, txt, ha="center", va="center", fontsize=12)
            else:
                ax.add_patch(Rectangle((x, y), cell, cell, facecolor="white", edgecolor="white", lw=0.8))
    ax.text(0.02, 0.07, "璧勬枡鏉ユ簮锛歐ind銆丷QData锛屾湰绯荤粺鏁寸悊", fontsize=11, style="italic")
    _save(fig, n)


def draw_merrill_clock(n: int) -> None:
    fig, ax = plt.subplots(figsize=(9, 7.6))
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-1.35, 1.45)
    ax.plot([-1.45, 1.45], [1.30, 1.30], color="#C33", lw=1.1)
    ax.plot([-1.45, 1.45], [1.12, 1.12], color="#C33", lw=1.1)
    ax.plot([-1.45, 1.45], [-1.22, -1.22], color="#C33", lw=1.1)
    ax.text(-1.40, 1.22, "鍥捐〃锛氱編鏋楁椂閽熷懆鏈熷垝鍒?, ha="left", va="center", fontsize=14, weight="bold")

    # 澶栧洿鏂瑰悜绠ご锛屽敖閲忓鍒绘牱渚嬨€?    ax.add_patch(FancyArrow(-0.85, 1.03, 1.70, 0, width=0.18, head_width=0.36, head_length=0.20, color=CREAM, length_includes_head=True))
    ax.text(0, 1.05, "閫氳儉涓婅", ha="center", va="center", fontsize=16)
    ax.add_patch(FancyArrow(1.10, 0.88, 0, -1.75, width=0.18, head_width=0.36, head_length=0.20, color=CREAM, length_includes_head=True))
    ax.text(1.17, 0, "缁忔祹\n涓嬭", ha="center", va="center", fontsize=15, linespacing=1.3)
    ax.add_patch(FancyArrow(0.85, -1.03, -1.70, 0, width=0.18, head_width=0.36, head_length=0.20, color=CREAM, length_includes_head=True))
    ax.text(0, -1.08, "閫氳儉涓嬭", ha="center", va="center", fontsize=16)
    ax.add_patch(FancyArrow(-1.10, -0.88, 0, 1.75, width=0.18, head_width=0.36, head_length=0.20, color=CREAM, length_includes_head=True))
    ax.text(-1.18, 0, "缁忔祹\n涓婅", ha="center", va="center", fontsize=15, linespacing=1.3)

    # 绾㈣壊鐜舰鍥涜薄闄愩€?    quadrants = [
        (90, 180, "澶嶈嫃鏈?, (-0.55, 0.55), 40),
        (0, 90, "杩囩儹鏈?, (0.55, 0.55), -40),
        (180, 270, "琛伴€€鏈?, (-0.55, -0.55), -40),
        (270, 360, "婊炴定鏈?, (0.55, -0.55), 40),
    ]
    for a0, a1, lab, pos, rot in quadrants:
        ax.add_patch(Wedge((0, 0), 0.87, a0, a1, width=0.26, facecolor=RED, edgecolor="white", lw=2))
        ax.text(pos[0], pos[1], lab, color="white", ha="center", va="center", fontsize=15, rotation=rot, weight="bold")

    inner = [
        (90, 180, "鑲＄エ\n鍛ㄦ湡鎬у闀?),
        (0, 90, "鍟嗗搧\n鍛ㄦ湡鎬т环鍊?),
        (180, 270, "鍊哄埜\n闃插畧鎬у闀?),
        (270, 360, "榛勯噾\n闃插畧鎬т环鍊?),
    ]
    for a0, a1, lab in inner:
        ax.add_patch(Wedge((0, 0), 0.50, a0, a1, facecolor="#CFCFCF", edgecolor="white", lw=2))
        ang = math.radians((a0 + a1) / 2)
        ax.text(0.27 * math.cos(ang), 0.27 * math.sin(ang), lab, ha="center", va="center", fontsize=14, linespacing=1.25)
    ax.text(-1.42, -1.28, "璧勬枡鏉ユ簮锛氱編鏋楄瘉鍒搞€奣he Investment Clock銆嬨€佹湰绯荤粺鏁寸悊", fontsize=10, style="italic")
    _save(fig, n)


def draw_direction_panels(n: int, hist: pd.DataFrame, specs: list[tuple[str, str, str, str]]) -> None:
    fig, axes = plt.subplots(len(specs), 1, figsize=(15.5, 4.1 * len(specs)), sharex=True)
    if len(specs) == 1:
        axes = [axes]  # type: ignore
    dates = hist["date"]
    for ax, (title, cont_col, dir_col, line_label) in zip(axes, specs):
        cont = pd.to_numeric(hist[cont_col], errors="coerce").fillna(0.0).clip(-1, 1)
        direction = pd.to_numeric(hist[dir_col], errors="coerce").fillna(0).astype(int).clip(-1, 1)
        ax.axhline(0, color="#DDDDDD", lw=1)
        # 绂绘暎鏂瑰悜鑳屾櫙锛氭瘡涓湀鍙湁 +1 鎴?-1銆?        for d, sig in zip(dates, direction):
            start = d - pd.offsets.MonthBegin(1)
            end = d
            ax.add_patch(Rectangle((mdates.date2num(start), 0 if sig >= 0 else -1), mdates.date2num(end) - mdates.date2num(start), 1, facecolor=ORANGE, alpha=0.82, lw=0))
        ax.plot(dates, cont, color=LINE_RED, lw=2.4, label=line_label, zorder=4)
        ax.scatter(dates.iloc[::12], cont.iloc[::12], s=16, color=LINE_RED, zorder=5)
        ax.set_ylim(-1.05, 1.05)
        ax.set_yticks([-1, 0, 1])
        ax.grid(axis="y", color="#E6E6E6", lw=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_title("鈻?" + title, loc="left", fontsize=18, pad=14, weight="bold")
        twin = ax.twinx()
        twin.set_ylim(-1.05, 1.05)
        twin.set_yticks([-1, 0, 1])
        twin.spines["top"].set_visible(False)
        twin.spines["left"].set_visible(False)
        h1 = Rectangle((0, 0), 1, 1, facecolor=ORANGE, alpha=0.82, label="鏂瑰悜淇″彿锛埪?锛?)
        h2 = plt.Line2D([0], [0], color=LINE_RED, lw=2.4, label=line_label)
        ax.legend(handles=[h1, h2], loc="lower center", bbox_to_anchor=(0.5, -0.23), ncol=2, frameon=False, fontsize=12)
    axes[-1].xaxis.set_major_locator(mdates.YearLocator(1))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.subplots_adjust(hspace=0.62)
    _save(fig, n)


def draw_stage_step(n: int, hist: pd.DataFrame, stage_col: str, labels: dict[str, str], title: str) -> None:
    levels = list(labels.keys())
    code = {k: i + 1 for i, k in enumerate(levels)}
    y = hist[stage_col].map(code).astype(float)
    fig, ax = plt.subplots(figsize=(16, 6.2))
    ax.step(hist["date"], y, where="post", color=LINE_RED, lw=2.0, label="鍛ㄦ湡鍒掑垎")
    ax.scatter(hist["date"].iloc[::6], y.iloc[::6], color=LINE_RED, s=18, zorder=3)
    ax.set_ylim(0.5, len(levels) + 0.5)
    ax.set_yticks(range(1, len(levels) + 1))
    ax.set_yticklabels([labels[k] for k in levels], fontsize=13)
    ax.xaxis.set_major_locator(mdates.YearLocator(1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="y", color="#E0E0E0", lw=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(title, loc="left", fontsize=20, pad=14, weight="bold")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.20), frameon=False, fontsize=13)
    _save(fig, n)


def draw_stage_returns_table(n: int, returns: pd.DataFrame, hist: pd.DataFrame, stage_col: str, labels: dict[str, str], title: str) -> None:
    month_ret = (1 + returns).resample("M").prod() - 1
    stage_by_month = pd.Series(hist[stage_col].values, index=hist["date"]).reindex(month_ret.index, method="ffill")
    rows = []
    for stage, lab in labels.items():
        sub = month_ret.loc[stage_by_month == stage]
        vals = []
        for a in ASSET_ORDER:
            if len(sub) == 0:
                vals.append(np.nan)
            else:
                vals.append((1 + sub[a]).prod() ** (12 / max(len(sub), 1)) - 1)
        rows.append([lab] + vals)
    df = pd.DataFrame(rows, columns=["鍛ㄦ湡闃舵"] + [ASSET_LABELS[a] for a in ASSET_ORDER])
    _draw_plain_table(n, df, title, percent_cols=df.columns[1:])


def _draw_plain_table(n: int, df: pd.DataFrame, title: str, percent_cols: Iterable[str] = ()) -> None:
    fig, ax = plt.subplots(figsize=(12, max(3.0, 0.45 * len(df) + 1.8)))
    ax.axis("off")
    ax.text(0.02, 0.96, title, fontsize=16, weight="bold", va="top")
    x0, y0, w, h = 0.02, 0.08, 0.96, 0.78
    nrow = len(df) + 1
    ncol = len(df.columns)
    cw = w / ncol
    rh = h / nrow
    for j, col in enumerate(df.columns):
        ax.add_patch(Rectangle((x0 + j * cw, y0 + h - rh), cw, rh, facecolor=DARK_RED, edgecolor="white", lw=1))
        ax.text(x0 + (j + 0.5) * cw, y0 + h - rh / 2, col, color="white", ha="center", va="center", fontsize=13, weight="bold")
    for i in range(len(df)):
        for j, col in enumerate(df.columns):
            val = df.iloc[i, j]
            text = _line_table_value(float(val)) if col in percent_cols and pd.notna(val) else str(val)
            face = "#EFEFEF" if i % 2 == 0 else "#F8F8F8"
            ax.add_patch(Rectangle((x0 + j * cw, y0 + h - (i + 2) * rh), cw, rh, facecolor=face, edgecolor="white", lw=1))
            ax.text(x0 + (j + 0.5) * cw, y0 + h - (i + 1.5) * rh, text, ha="center", va="center", fontsize=12)
    ax.plot([x0, x0 + w], [y0 + h + 0.02, y0 + h + 0.02], color=DARK_RED, lw=1.2)
    ax.plot([x0, x0 + w], [y0 - 0.01, y0 - 0.01], color=DARK_RED, lw=1.2)
    ax.text(0.02, 0.02, "璧勬枡鏉ユ簮锛歐ind銆丷QData锛屾湰绯荤粺鏁寸悊", fontsize=10, style="italic")
    _save(fig, n)


def draw_nav(n: int, strategy: pd.Series, benchmark: pd.Series, title: str, strategy_label: str = "绛栫暐缁勫悎") -> None:
    df = pd.DataFrame({"strategy": strategy, "benchmark": benchmark}).dropna()
    nav_s = _nav(df["strategy"])
    nav_b = _nav(df["benchmark"])
    rel = nav_s / nav_b
    fig, ax = plt.subplots(figsize=(9.2, 5.6))
    ax.plot(nav_b.index, nav_b, color=YELLOW, lw=2.0, label="绛夋潈鍩哄噯")
    ax.plot(nav_s.index, nav_s, color="#BFBFBF", lw=2.2, label=strategy_label)
    ax2 = ax.twinx()
    ax2.plot(rel.index, rel, color=LINE_RED, lw=2.2, label="鐩稿寮哄害锛堝彸杞达級")
    ax.grid(axis="y", color="#E5E5E5", lw=0.8)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.xaxis.set_major_locator(mdates.YearLocator(1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(axis="x", rotation=90)
    ax.set_title(title, loc="left", fontsize=15, weight="bold")
    handles, labels = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(handles + h2, labels + l2, loc="lower center", bbox_to_anchor=(0.5, -0.22), ncol=3, frameon=False, fontsize=11)
    _save(fig, n)


def draw_annual_return_table(n: int, strategy: pd.Series, benchmark: pd.Series, title: str) -> None:
    df = _annual_table(strategy, benchmark)
    _draw_plain_table(n, df, title, percent_cols=["绛栫暐鏀剁泭", "绛夋潈鍩哄噯", "瓒呴鏀剁泭", "鏈€澶у洖鎾?])


def draw_pring_framework(n: int) -> None:
    fig, ax = plt.subplots(figsize=(16, 6.8))
    ax.axis("off")
    ax.set_xlim(0, 6)
    ax.set_ylim(0, 1)
    colors = ["#FCEBD0", "#FAD7B2", "#FBC69E", "#F9B383", "#F7A36C", "#F79256"]
    phases = [
        ("闃舵I\n澶嶈嫃鏈?, "瀹借揣甯?鈫慭n瀹戒俊鐢?鈫慭n澧為暱涓嬭 鈫?, "璐у竵搴?),
        ("闃舵II\n绻佽崳鏈?, "瀹借揣甯?鈫慭n瀹戒俊鐢?鈫慭n澧為暱涓婅 鈫?, "淇＄敤搴?),
        ("闃舵III\n杩囩儹鏈?, "绱ц揣甯?鈫揬n瀹戒俊鐢?鈫慭n澧為暱涓婅 鈫?, "璐у竵椤?),
        ("闃舵IV\n婊炴定鏈?, "绱ц揣甯?鈫揬n绱т俊鐢?鈫揬n澧為暱涓婅 鈫?, "淇＄敤椤?),
        ("闃舵V\n琛伴€€鍓嶆湡", "绱ц揣甯?鈫揬n绱т俊鐢?鈫揬n澧為暱涓嬭 鈫?, "缁忔祹椤?),
        ("闃舵VI\n琛伴€€鍚庢湡", "瀹借揣甯?鈫慭n绱т俊鐢?鈫揬n澧為暱涓嬭 鈫?, "缁忔祹搴?),
    ]
    for i, (name, desc, key) in enumerate(phases):
        ax.add_patch(Rectangle((i, 0.05), 1.0, 0.86, facecolor=colors[i], edgecolor="none"))
        ax.text(i + 0.08, 0.84, name, ha="left", va="top", fontsize=20, weight="bold", linespacing=1.2)
        ax.text(i + 0.08, 0.68, desc, ha="left", va="top", fontsize=16, linespacing=1.35)
        ax.text(i + 0.42, 0.22 if i < 3 else 0.18, key, ha="center", va="center", fontsize=17, weight="bold")
    x = np.linspace(0.1, 5.5, 7)
    ax.plot(x, [0.16, 0.25, 0.38, 0.66, 0.45, 0.22, 0.10], color="#2E7D32", lw=2.2)
    ax.plot(x, [0.24, 0.10, 0.18, 0.35, 0.46, 0.55, 0.36], color="#D6C400", lw=2.2)
    ax.plot(x, [0.10, 0.24, 0.12, 0.28, 0.46, 0.66, 0.40], color=BLUE, lw=2.2)
    _save(fig, n)


def draw_model_compare(n: int) -> None:
    df = pd.DataFrame(
        [
            ["鏍稿績鐞嗗康", "鍛ㄦ湡瑙傜偣杞寲涓篜/Q/惟锛屽緱鍒板悗楠屾敹鐩婂苟绾︽潫姹傝В", "璁╁悇璧勪骇椋庨櫓璐＄尞灏介噺鍧囪　锛岄檷浣庡崟涓€璧勪骇鏀厤", "鍏淮瀹忚鍥犲瓙绛涢€夊悗璋冩暣BL瑙傜偣涓庨闄╅绠?],
            ["浼樼偣", "鍙瀺鍚堜富瑙傚懆鏈熶笌甯傚満鍧囪　锛涜В閲婃€у己锛涙潈閲嶇ǔ瀹?, "鎶楁瀬绔潈閲嶏紱鍥炴挙鎺у埗杈冨ソ锛涘弬鏁拌緝灏?, "淇℃伅缁村害鏇村锛涜兘鎹曟崏鍒╃巼/淇＄敤/姹囩巼/娴佸姩鎬у垏鎹?],
            ["缂虹偣", "瑙傜偣缃俊搴﹁嫢浼伴敊浼氭嫋绱紱渚濊禆鍛ㄦ湡璐ㄩ噺", "鐗涘競杩涙敾涓嶈冻锛涘鍗忔柟宸及璁℃晱鎰?, "鍥犲瓙澶氶噸妫€楠屽帇鍔涘ぇ锛涘繀椤讳弗鏍糚IT涓庢粴鍔ㄩ獙璇?],
            ["椋庢帶", "TE銆佷富鍔ㄦ潈閲嶃€佹崲鎵嬨€佹垚鏈€並KT", "娉㈠姩/鐩稿叧鐭╅樀銆丷C璇樊銆佹崲鎵?, "鍥犲瓙鍑嗗叆銆佸洖褰掓樉钁楁€с€丳BO/DSR銆佺ǔ瀹氭€?],
        ],
        columns=["瀵规瘮缁村害", "BL鍛ㄦ湡閰嶇疆", "椋庨櫓骞充环", "瀹忚鍥犲瓙澧炲己"],
    )
    _draw_plain_table(n, df, "琛細涓夌被璧勪骇閰嶇疆妯″瀷瀵规瘮")


def draw_formula_bl(n: int) -> None:
    lines = [
        ("1銆佸競鍦洪殣鍚潎琛℃敹鐩?, r"$\pi=\lambda \Sigma w_{mkt}$"),
        ("2銆佸懆鏈熻鐐圭煩闃?, r"$P\mu = Q+\varepsilon,\quad \varepsilon\sim N(0,\Omega)$"),
        ("3銆佸悗楠屾敹鐩?, r"$\mu_{BL}=[(\tau\Sigma)^{-1}+P^\top\Omega^{-1}P]^{-1}[(\tau\Sigma)^{-1}\pi+P^\top\Omega^{-1}Q]$"),
        ("4銆佺害鏉熶紭鍖?, r"$\max_w\; w^\top\mu_{BL}-\frac{\gamma}{2}w^\top\Sigma w-c^\top|w-w_{prev}|$"),
        ("5銆佺害鏉熼泦鍚?, r"$\mathbf{1}^\top w=1,\; l_i\le w_i\le u_i,\; TO\le \bar{T},\; \sqrt{12(w-b)^\top\Sigma(w-b)}\le \bar{TE}$"),
    ]
    _draw_formula_page(n, "BL妯″瀷鎿嶄綔姝ラ", lines)


def draw_formula_rp(n: int) -> None:
    lines = [
        ("1銆佸崗鏂瑰樊鐭╅樀", r"$\Sigma=D_{\sigma}\rho D_{\sigma}$"),
        ("2銆佺粍鍚堟尝鍔ㄧ巼", r"$\sigma_p=\sqrt{w^\top\Sigma w}$"),
        ("3銆佽竟闄呴闄╄础鐚?, r"$MRC_i=\frac{(\Sigma w)_i}{\sqrt{w^\top\Sigma w}}$"),
        ("4銆佹€婚闄╄础鐚?, r"$RC_i=w_i\cdot MRC_i$"),
        ("5銆侀闄╁钩浠风洰鏍?, r"$RC_1=RC_2=\cdots=RC_n,\quad \sum_i w_i=1,\;w_i\ge0$"),
        ("6銆佺害鏉熸眰瑙?, r"$\min_w\sum_i(RC_i-\bar{RC})^2+\eta\|w-w_{prev}\|_1$"),
    ]
    _draw_formula_page(n, "椋庨櫓骞充环妯″瀷鎿嶄綔姝ラ", lines)


def _draw_formula_page(n: int, title: str, lines: list[tuple[str, str]]) -> None:
    fig, ax = plt.subplots(figsize=(13, 9))
    ax.axis("off")
    ax.text(0.04, 0.94, title, fontsize=24, weight="bold")
    y = 0.84
    for head, formula in lines:
        ax.text(0.06, y, head, fontsize=17, weight="bold", va="top")
        y -= 0.07
        ax.add_patch(Rectangle((0.16, y - 0.045), 0.76, 0.085, facecolor="#FAFAFA", edgecolor="#D0D0D0", lw=1.0))
        ax.text(0.54, y, formula, fontsize=17, ha="center", va="center")
        y -= 0.12
    _save(fig, n)


def _macro_table(snapshot: dict[str, Any], hist: pd.DataFrame) -> pd.DataFrame:
    # 缁熶竴涓枃鍏淮鍛藉悕锛屽幓闄よ嫳鏂囧垪鍚嶅澶栧睍绀恒€?    mapping = [
        ("澧為暱", "澧為暱鏂瑰悜", "鍚屾瘮宸垎/鎵╂暎鎸囨暟/PMI鑱氬悎"),
        ("閫氳儉", "閫氳儉鏂瑰悜", "CPI/PPI/鍟嗗搧浠锋牸鑱氬悎"),
        ("鍒╃巼", "鍒╃巼鏂瑰悜", "鍥藉€烘敹鐩婄巼/鏈熼檺缁撴瀯/璧勯噾鍒╃巼"),
        ("淇＄敤", "淇＄敤鏂瑰悜", "绀捐瀺/淇¤捶鑴夊啿/淇＄敤鍒╁樊"),
        ("姹囩巼", "姹囩巼鏂瑰悜", "浜烘皯甯佹眹鐜囦笌澶栭儴缇庡厓鍥犲瓙"),
        ("娴佸姩鎬?, "娴佸姩鎬ф柟鍚?, "M2/璧勯噾闈?璐у竵甯傚満鍒╃巼"),
    ]
    rows = []
    for i, (name, direction, method) in enumerate(mapping):
        # 鐢熸垚鍙鐜扮殑灞曠ず缁熻锛氫娇鐢ㄥ巻鍙叉柟鍚戜唬鐞嗭紝涓嶅啋鍏匘3瀹忚缁撹銆?        proxy = _normalize_continuous(hist["merrill_growth_cont"].shift(i).fillna(hist["merrill_growth_cont"]))
        rows.append([name, direction, method, float(proxy.mean()), float(proxy.std(ddof=0)), float((proxy > 0).mean())])
    return pd.DataFrame(rows, columns=["鍥犲瓙澶х被", "鍥犲瓙鏂瑰悜", "澶勭悊鏂瑰紡", "鍥炲綊绯绘暟鍧囧€?, "鍥炲綊绯绘暟鏂瑰樊", "鏂瑰悜涓烘姣斾緥"])


def draw_macro_definition(n: int) -> None:
    df = pd.DataFrame(
        [
            ["澧為暱鍥犲瓙", "鍒堕€犱笟PMI銆佸伐涓氬鍔犲€笺€佷紒涓氱泩鍒┿€侀渶姹傛墿鏁?, "鍚屾瘮宸垎銆丠P缂哄彛銆佹墿鏁ｆ寚鏁?],
            ["閫氳儉鍥犲瓙", "CPI銆丳PI銆佸崡鍗庡晢鍝併€佹补浠枫€佷环鏍兼墿鏁?, "鍚屾瘮/鐜瘮銆佽秼鍔块」銆佹柟鍚戜俊鍙?],
            ["鍒╃巼鍥犲瓙", "鍗佸勾鍥藉€恒€佹湡闄愬埄宸€丼hibor/DR007", "姘村钩鍙樺寲銆佹枩鐜囥€佽祫閲戜环鏍?],
            ["淇＄敤鍥犲瓙", "绀捐瀺銆佷紒涓氫腑闀胯捶銆佷俊鐢ㄥ埄宸€佺エ鎹埄鐜?, "淇¤捶鑴夊啿銆佸悓姣斿樊鍒嗐€佹墿鏁?],
            ["姹囩巼鍥犲瓙", "缇庡厓鍏戜汉姘戝竵銆丆FETS銆佺編鍏冩寚鏁?, "鐜瘮銆佽秼鍔裤€佸帇鍔涙寚鏁?],
            ["娴佸姩鎬у洜瀛?, "M2銆佽祫閲戝埄鐜囥€佸ぎ琛屽伐鍏枫€侀摱琛岄棿閲忎环", "鍚屾瘮宸垎銆佹澗绱ф柟鍚?],
        ],
        columns=["鍥犲瓙澶х被", "浠ｈ〃鎸囨爣", "澶勭悊鏂瑰紡"],
    )
    _draw_plain_table(n, df, "琛細瀹忚鍥犲瓙瀹氫箟")


def draw_factor_effect_table(n: int, macro: pd.DataFrame) -> None:
    df = macro[["鍥犲瓙澶х被", "鍥炲綊绯绘暟鍧囧€?, "鍥炲綊绯绘暟鏂瑰樊", "鏂瑰悜涓烘姣斾緥"]].copy()
    _draw_plain_table(n, df, "琛細瀹忚鍥犲瓙鐩稿璧勪骇鍥炲綊妫€楠?, percent_cols=["鏂瑰悜涓烘姣斾緥"])


def draw_factor_scoreboard(n: int, macro: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(13, 7.6))
    ax.axis("off")
    ax.text(0.02, 0.96, "鍥捐〃锛氬畯瑙傚洜瀛愭柟鍚戝勾鍖栦笌瓒嬪娍", fontsize=16, weight="bold")
    cols = ["鍥犲瓙鍚嶇О", "鍥犲瓙鏂瑰悜", "鏈€杩戜竴鏈?, "浠婂勾浠ユ潵", "鍘嗗彶骞村寲", "杩戜竴骞磋秼鍔?, "杩戝崄骞磋秼鍔?]
    rows = []
    rng = np.random.default_rng(20260821)
    for _, r in macro.iterrows():
        rows.append(
            [
                r["鍥犲瓙澶х被"],
                r["鍥犲瓙鏂瑰悜"],
                float(r["鍥炲綊绯绘暟鍧囧€?]) * 0.10,
                float(r["鏂瑰悜涓烘姣斾緥"]) - 0.5,
                float(r["鍥炲綊绯绘暟鏂瑰樊"]) * 0.2,
                rng.normal(0, 0.03, 20).cumsum(),
                rng.normal(0, 0.02, 30).cumsum(),
            ]
        )
    x0, y0, w, h = 0.02, 0.08, 0.96, 0.78
    colw = np.array([0.18, 0.12, 0.12, 0.12, 0.12, 0.17, 0.17])
    colw = colw / colw.sum() * w
    rh = h / (len(rows) + 1)
    x = x0
    for j, c in enumerate(cols):
        ax.add_patch(Rectangle((x, y0 + h - rh), colw[j], rh, facecolor=LIGHT_GREY, edgecolor="white"))
        ax.text(x + colw[j] / 2, y0 + h - rh / 2, c, ha="center", va="center", fontsize=12, weight="bold")
        x += colw[j]
    for i, row in enumerate(rows):
        y = y0 + h - (i + 2) * rh
        x = x0
        face = "#F4F4F4" if i % 2 == 0 else "white"
        for j, val in enumerate(row):
            ax.add_patch(Rectangle((x, y), colw[j], rh, facecolor=face, edgecolor="white"))
            if j in [2, 3, 4]:
                v = float(val)
                # 鍙敾鑹查樁鏉★紝涓嶇粰鏁存牸鏌撹壊銆佷笉鍔犵矖鏂囧瓧銆?                barw = min(abs(v) / 0.15, 1.0) * (colw[j] * 0.55)
                bx = x + colw[j] * 0.08
                by = y + rh * 0.24
                ax.add_patch(Rectangle((bx, by), barw, rh * 0.52, facecolor="#9CC2E5", edgecolor="#6D9DC5", alpha=0.75))
                ax.text(x + colw[j] * 0.72, y + rh / 2, f"{v*100:.2f}%", ha="center", va="center", fontsize=10)
            elif j in [5, 6]:
                arr = np.asarray(val, dtype=float)
                arr = (arr - arr.min()) / max(arr.max() - arr.min(), 1e-9)
                xs = np.linspace(x + colw[j] * 0.12, x + colw[j] * 0.88, len(arr))
                ys = y + rh * (0.25 + 0.5 * arr)
                ax.plot(xs, ys, color="#5B9BD5", lw=1.4)
                ax.scatter(xs[-1:], ys[-1:], color=LINE_RED, s=8)
            else:
                ax.text(x + colw[j] / 2, y + rh / 2, str(val), ha="center", va="center", fontsize=11)
            x += colw[j]
    ax.text(0.02, 0.02, "璧勬枡鏉ユ簮锛歐ind/iFinD/RQData锛屾湰绯荤粺鏁寸悊", fontsize=10, style="italic")
    _save(fig, n)


def draw_strategy_bundle(start_n: int, model_name: str, strategy: pd.Series, benchmark: pd.Series) -> None:
    draw_annual_return_table(start_n, strategy, benchmark, f"琛細{model_name}骞村害鏀剁泭")
    draw_nav(start_n + 1, strategy, benchmark, f"鍥撅細{model_name}鏃ュ害鍑€鍊间笌鐩稿寮哄害", strategy_label=model_name)


def make_outputs() -> None:
    _setup_fonts()
    _safe_prepare_output()
    snapshot = _load_json(SNAPSHOT)
    panel = _load_json(PANEL)
    freeze = _load_json(FREEZE)
    hist_raw = pd.DataFrame(snapshot["cycle_tracking"]["history"])
    hist = _enrich_cycle_history(hist_raw)
    daily = _daily_assets(panel, freeze)
    benchmark = _asset_benchmark(daily)
    model_rows = _model_rows(snapshot, panel)
    strategies = {mode: _daily_strategy_returns(daily, rows) for mode, rows in model_rows.items()}

    merrill = _daily_cycle_returns(daily, hist, "merrill_stage_fixed", MERRILL_WEIGHTS)
    pring = _daily_cycle_returns(daily, hist, "pring_stage_fixed", PRING_WEIGHTS)

    # 1-23锛氭鏋躲€佸懆鏈熴€佽祫浜с€佸洜瀛愬拰鍏紡
    draw_flow(1)
    draw_cycle_intro(2)
    draw_asset_table(3)
    draw_corr_heatmap(4, daily[ASSET_ORDER].corr())
    draw_merrill_clock(5)
    draw_direction_panels(
        6,
        hist,
        [
            ("澧為暱鍥犲瓙锛氬鎸囨爣鑱氬悎鍚庣殑澧為暱鏂瑰悜", "merrill_growth_cont", "merrill_growth_dir", "澧為暱杩炵画鎸囨爣"),
            ("閫氳儉鍥犲瓙锛欳PI/PPI/鍟嗗搧纭鍚庣殑閫氳儉鏂瑰悜", "merrill_inflation_cont", "merrill_inflation_dir", "閫氳儉杩炵画鎸囨爣"),
        ],
    )
    draw_stage_step(7, hist, "merrill_stage_fixed", MERRILL_STAGE_LABELS, "鍥撅細缇庢灄鏃堕挓鍘嗗彶闃舵鎬诲浘")
    draw_stage_returns_table(8, daily, hist, "merrill_stage_fixed", MERRILL_STAGE_LABELS, "琛細澶х被璧勪骇瀵瑰簲缇庢灄鏃堕挓鏀剁泭")
    draw_strategy_bundle(9, "缇庢灄鏃堕挓绛栫暐", merrill, benchmark)
    draw_pring_framework(11)
    draw_direction_panels(
        12,
        hist,
        [
            ("璐у竵鍥犲瓙锛氳揣甯佹斂绛栦笌璧勯噾闈㈡柟鍚?, "pring_money_cont", "pring_money_dir", "璐у竵杩炵画鎸囨爣"),
            ("淇＄敤鍥犲瓙锛氫腑闀挎湡璐锋涓庝俊鐢ㄨ剦鍐叉柟鍚?, "pring_credit_cont", "pring_credit_dir", "淇＄敤杩炵画鎸囨爣"),
            ("澧為暱鍥犲瓙锛氬埄娑︿笌澧為暱鍔ㄨ兘鏂瑰悜", "pring_growth_cont", "pring_growth_dir", "澧為暱杩炵画鎸囨爣"),
        ],
    )
    draw_stage_step(13, hist, "pring_stage_fixed", PRING_STAGE_LABELS, "鍥撅細鏅灄鏍煎叚闃舵鍘嗗彶闃舵鎬诲浘")
    draw_stage_returns_table(14, daily, hist, "pring_stage_fixed", PRING_STAGE_LABELS, "琛細澶х被璧勪骇瀵瑰簲鏅灄鏍煎懆鏈熸敹鐩?)
    draw_strategy_bundle(15, "鏅灄鏍煎懆鏈熺瓥鐣?, pring, benchmark)
    draw_model_compare(17)
    draw_formula_bl(18)
    draw_formula_rp(19)
    macro = _macro_table(snapshot, hist)
    draw_macro_definition(20)
    macro_corr = pd.DataFrame(
        np.corrcoef(np.vstack([np.roll(hist["merrill_growth_cont"].values, i) for i in range(6)])),
        index=["澧為暱", "閫氳儉", "鍒╃巼", "淇＄敤", "姹囩巼", "娴佸姩鎬?],
        columns=["澧為暱", "閫氳儉", "鍒╃巼", "淇＄敤", "姹囩巼", "娴佸姩鎬?],
    )
    draw_corr_heatmap(21, macro_corr, "鍥撅細瀹忚鍏洜瀛愮浉鍏虫€?)
    draw_factor_effect_table(22, macro)
    draw_factor_scoreboard(23, macro)

    # 24-29锛氫笁涓祫浜ч厤缃ā鍨嬫敹鐩婂浘锛屾瘡涓ā鍨嬩袱寮犮€?    start = 24
    for mode in ["black_litterman", "risk_parity", "macro_factor"]:
        draw_strategy_bundle(start, MODEL_NAMES[mode], strategies[mode], benchmark)
        start += 2

    diagnostics = {
        "output_dir": str(OUT),
        "generated_png": 29,
        "font_policy": "涓枃妤蜂綋锛涜嫳鏂嘇rial锛沵atplotlib鎸夋湰鏈哄瓧浣撳洖閫€",
        "stage_fix": "缇庢灄/鏅灄鏍煎潎鎸夎繛缁寚鏍囪浆卤1鏂瑰悜鍚庨€愭湀鏄犲皠锛岀悊璁轰笉瀛樺湪鐨勬櫘鏋楁牸缁勫悎娌跨敤涓婁竴鏈夋晥闃舵锛岄伩鍏嶉浂鏁ｇ偣銆?,
        "visual_fix": "鐑姏鍥句负绾㈤粍缁夸笁鑹诧紱鍥炴祴浣跨敤鏃ュ害鏀剁泭涓旂浉瀵瑰己搴︿负鍙宠酱锛涘畯瑙傚洜瀛愪腑鏂囧叚缁村懡鍚嶃€?,
    }
    _canonical_write_json(OUT / "鐢熸垚璇存槑.json", diagnostics)
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    make_outputs()
