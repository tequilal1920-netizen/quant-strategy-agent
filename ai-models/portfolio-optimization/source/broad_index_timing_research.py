"""Broad-index timing research charts.

The timing page is organised as a compact five-step framework:

1. factor construction;
2. data treatment;
3. factor efficacy tests;
4. attack/defense signal fusion into five exposure buckets;
5. T+1 backtest tracking.

The factor families follow the local broker-reference synthesis under
``reference/择时``: macro, price-volume, sentiment and valuation. Legacy
signals remain in the candidate pool only as a no-degradation guard.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import tushare as ts
from matplotlib.font_manager import FontProperties


ORANGE = "#FFC000"
GREY = "#BFBFBF"
RED = "#C00000"
BLACK = "#000000"
AXIS_GREY = "#D9D9D9"
TABLE_BLUE = "#E8EEF7"
TABLE_BEIGE = "#F4E7D8"

DEFAULT_OUTPUT = Path(r"C:\Users\Rye\Desktop\指数增强")


def _project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "board" / "quant_strategy_agent").exists() and (parent / "database").exists():
            return parent
    return current.parents[2]


PROJECT_ROOT = _project_root()
DB_PATH = PROJECT_ROOT / "database" / "research_warehouse.db"
BOARD_SNAPSHOT = PROJECT_ROOT / "board" / "quant_strategy_agent" / "data" / "broad_index_timing_snapshot.json"

INDEXES = [
    ("中证红利", "000922.CSI"),
    ("中证500", "000905.SH"),
    ("沪深300", "000300.SH"),
    ("科创50", "000688.SH"),
    ("中证1000", "000852.SH"),
    ("中证2000", "932000.CSI"),
]


def _font(path: str, size: float) -> FontProperties:
    font_path = Path(path)
    if font_path.exists():
        return FontProperties(fname=str(font_path), size=size)
    return FontProperties(size=size)


KAI_16 = _font(r"C:\Windows\Fonts\simkai.ttf", 16)
KAI_18 = _font(r"C:\Windows\Fonts\simkai.ttf", 18)
KAI_22 = _font(r"C:\Windows\Fonts\simkai.ttf", 22)
HEI_18 = _font(r"C:\Windows\Fonts\simhei.ttf", 18)
ARIAL_18 = _font(r"C:\Windows\Fonts\arial.ttf", 18)


@dataclass(frozen=True)
class TimingConfig:
    name: str
    left_weight: float = 0.22
    right_weight: float = 0.38
    sentiment_weight: float = 0.15
    risk_weight: float = 0.25
    efficacy_strength: float = 2.8
    position_smooth: int = 5
    high_threshold: float = 0.64
    mid_threshold: float = 0.52
    low_threshold: float = 0.40
    min_core_position: float = 0.50
    danger_votes: int = 4
    crash_votes: int = 4
    danger_position: float = 0.25
    crash_position: float = 0.0
    bear_position_cap: float = 0.50
    repair_position: float = 0.75
    strong_position: float = 1.00
    top_position_cap: float = 0.50


CONFIGS = [
    TimingConfig("左侧右侧有效性优选"),
    TimingConfig(
        "风险雷达路径优选",
        left_weight=0.18,
        right_weight=0.34,
        sentiment_weight=0.12,
        risk_weight=0.36,
        high_threshold=0.62,
        mid_threshold=0.50,
        low_threshold=0.38,
        danger_votes=3,
        crash_votes=4,
        danger_position=0.20,
        crash_position=0.0,
        bear_position_cap=0.45,
    ),
    TimingConfig(
        "高胜率趋势捕捉",
        left_weight=0.16,
        right_weight=0.50,
        sentiment_weight=0.16,
        risk_weight=0.18,
        high_threshold=0.60,
        mid_threshold=0.48,
        low_threshold=0.36,
        min_core_position=0.60,
        danger_votes=4,
        crash_votes=5,
        danger_position=0.35,
        bear_position_cap=0.65,
    ),
    TimingConfig(
        "回撤约束增强",
        left_weight=0.24,
        right_weight=0.30,
        sentiment_weight=0.12,
        risk_weight=0.34,
        high_threshold=0.66,
        mid_threshold=0.54,
        low_threshold=0.42,
        min_core_position=0.45,
        danger_votes=3,
        crash_votes=3,
        danger_position=0.15,
        bear_position_cap=0.40,
    ),
]


@dataclass(frozen=True)
class FusionProfile:
    name: str
    macro_weight: float = 0.24
    price_volume_weight: float = 0.36
    sentiment_weight: float = 0.20
    valuation_weight: float = 0.20
    defense_risk_weight: float = 0.42
    defense_price_weight: float = 0.24
    defense_valuation_weight: float = 0.20
    defense_macro_weight: float = 0.14
    attack_small: float = 0.54
    attack_medium: float = 0.60
    attack_large: float = 0.66
    defense_small: float = 0.48
    defense_medium: float = 0.58
    defense_large: float = 0.68
    smooth: int = 5
    strong_floor: float = 1.0
    repair_floor: float = 0.75
    weak_cap: float = 0.50
    crash_cap: float = 0.0


FUSION_PROFILES = [
    FusionProfile("因子检验五档融合"),
    FusionProfile(
        "防守优先五档融合",
        macro_weight=0.26,
        price_volume_weight=0.30,
        sentiment_weight=0.16,
        valuation_weight=0.28,
        defense_risk_weight=0.48,
        defense_price_weight=0.22,
        defense_valuation_weight=0.20,
        defense_macro_weight=0.10,
        attack_small=0.55,
        attack_medium=0.61,
        attack_large=0.68,
        defense_small=0.44,
        defense_medium=0.54,
        defense_large=0.64,
        weak_cap=0.25,
        crash_cap=0.0,
    ),
    FusionProfile(
        "趋势确认五档融合",
        macro_weight=0.18,
        price_volume_weight=0.46,
        sentiment_weight=0.22,
        valuation_weight=0.14,
        defense_risk_weight=0.36,
        defense_price_weight=0.34,
        defense_valuation_weight=0.14,
        defense_macro_weight=0.16,
        attack_small=0.52,
        attack_medium=0.58,
        attack_large=0.64,
        defense_small=0.50,
        defense_medium=0.61,
        defense_large=0.72,
        smooth=3,
    ),
    FusionProfile(
        "宏观估值五档融合",
        macro_weight=0.34,
        price_volume_weight=0.28,
        sentiment_weight=0.14,
        valuation_weight=0.24,
        defense_risk_weight=0.38,
        defense_price_weight=0.20,
        defense_valuation_weight=0.26,
        defense_macro_weight=0.16,
        attack_small=0.53,
        attack_medium=0.59,
        attack_large=0.65,
        defense_small=0.48,
        defense_medium=0.57,
        defense_large=0.66,
        smooth=5,
    ),
]


FACTOR_FAMILY_LABELS = {
    "macro": "宏观因子",
    "price_volume": "量价因子",
    "sentiment": "情绪因子",
    "valuation": "估值因子",
    "risk": "风险控制",
}


RISK_RADAR_CFG = {
    "danger_votes": 5,
    "crash_votes": 3,
    "danger_pos": 0.0,
    "crash_pos": 0.50,
    "mom20_bad": -0.02,
    "mom60_bad": -0.02,
    "rsrs_off": -0.50,
    "rsrs_crash": -1.00,
    "rsrs_on": -0.20,
    "vol_quantile": 0.80,
    "crash_mom20": -0.08,
    "repair_drawdown": -0.12,
    "repair_votes": 3,
    "repair_pos": 0.75,
    "smooth": 2,
}


def _sigmoid(value: pd.Series | np.ndarray | float, scale: float = 1.0) -> pd.Series:
    arr = np.asarray(value, dtype=float)
    arr = np.clip(arr / max(scale, 1.0e-12), -12.0, 12.0)
    return pd.Series(1.0 / (1.0 + np.exp(-arr)))


def _clip01(series: pd.Series | np.ndarray | float) -> pd.Series:
    return pd.Series(series).astype(float).replace([np.inf, -np.inf], np.nan).clip(0.0, 1.0)


def _safe_div(a: pd.Series, b: pd.Series | float) -> pd.Series:
    out = a.astype(float) / b
    return out.replace([np.inf, -np.inf], np.nan)


def _zscore(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(20, window // 3)
    mean = series.rolling(window, min_periods=min_periods).mean()
    std = series.rolling(window, min_periods=min_periods).std(ddof=0)
    return _safe_div(series - mean, std).fillna(0.0)


def _rolling_percentile(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(20, window // 3)

    def rank_last(values: np.ndarray) -> float:
        values = values[np.isfinite(values)]
        if values.size < min_periods:
            return np.nan
        current = values[-1]
        return float((np.sum(values <= current) - 0.5 * np.sum(values == current)) / values.size)

    return series.rolling(window, min_periods=min_periods).apply(rank_last, raw=True).clip(0.0, 1.0)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    diff = close.diff()
    up = diff.clip(lower=0.0).rolling(window, min_periods=window).mean()
    down = (-diff.clip(upper=0.0)).rolling(window, min_periods=window).mean()
    rs = _safe_div(up, down)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def _rsrs(high: pd.Series, low: pd.Series, n: int = 18, m: int = 600) -> tuple[pd.Series, pd.Series]:
    slopes: list[float] = []
    r2_values: list[float] = []
    x_all = low.to_numpy(dtype=float)
    y_all = high.to_numpy(dtype=float)
    for i in range(len(high)):
        if i + 1 < n:
            slopes.append(np.nan)
            r2_values.append(np.nan)
            continue
        x = x_all[i + 1 - n : i + 1]
        y = y_all[i + 1 - n : i + 1]
        if not np.isfinite(x).all() or not np.isfinite(y).all() or float(np.std(x)) <= 1.0e-12:
            slopes.append(np.nan)
            r2_values.append(np.nan)
            continue
        beta, alpha = np.polyfit(x, y, 1)
        pred = beta * x + alpha
        ss_res = float(np.sum(np.square(y - pred)))
        ss_tot = float(np.sum(np.square(y - y.mean())))
        slopes.append(float(beta))
        r2_values.append(0.0 if ss_tot <= 1.0e-12 else max(0.0, 1.0 - ss_res / ss_tot))
    slope = pd.Series(slopes, index=high.index)
    r2 = pd.Series(r2_values, index=high.index)
    z = _zscore(slope, m, min_periods=120)
    return (z * r2).fillna(0.0), r2.fillna(0.0)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)
    tr = pd.concat(
        [(high - low).abs(), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean().replace(0.0, np.nan)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)
    return dx.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean().fillna(20.0)


_MARKET_CONTEXT_CACHE: dict[tuple[str, str, str], pd.DataFrame] = {}


def _fetch_market_context(start: str, end: str) -> pd.DataFrame:
    """Read low-frequency macro and daily market context from the local warehouse.

    Macro rows are lagged to month-end plus ten calendar days before they can be
    used.  Daily aggregates are broad-market context, not index-specific future
    information.  Missing data is allowed; the feature builder maps it to 0.5.
    """
    key = (str(DB_PATH), start, end)
    if key in _MARKET_CONTEXT_CACHE:
        return _MARKET_CONTEXT_CACHE[key].copy()
    if not DB_PATH.is_file():
        _MARKET_CONTEXT_CACHE[key] = pd.DataFrame()
        return pd.DataFrame()
    start_dt = pd.to_datetime(start, format="%Y%m%d") - pd.DateOffset(years=2)
    context_start = start_dt.strftime("%Y%m%d")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            valuation = pd.read_sql_query(
                """
                SELECT
                    trade_date,
                    AVG(turnover_rate_f) AS ctx_turnover_rate,
                    AVG(volume_ratio) AS ctx_volume_ratio,
                    AVG(CASE WHEN pe_ttm > 0 THEN 1.0 / pe_ttm END) AS ctx_earnings_yield,
                    AVG(pb) AS ctx_pb,
                    AVG(dv_ttm) AS ctx_dividend_yield,
                    COUNT(*) AS ctx_stock_count
                FROM stock_valuation_daily
                WHERE trade_date BETWEEN ? AND ?
                GROUP BY trade_date
                ORDER BY trade_date
                """,
                conn,
                params=(context_start, end),
            )
            moneyflow = pd.read_sql_query(
                """
                SELECT
                    trade_date,
                    SUM(net_mf_amount) AS ctx_net_mf_amount,
                    SUM(COALESCE(buy_lg_amount, 0) + COALESCE(buy_elg_amount, 0)
                        - COALESCE(sell_lg_amount, 0) - COALESCE(sell_elg_amount, 0)) AS ctx_large_net_amount
                FROM stock_moneyflow_daily
                WHERE trade_date BETWEEN ? AND ?
                GROUP BY trade_date
                ORDER BY trade_date
                """,
                conn,
                params=(context_start, end),
            )
            macro = pd.read_sql_query(
                """
                SELECT month, pmi_manufacturing, pmi_non_manufacturing, pmi_composite,
                       cpi_national_yoy, ppi_yoy, m1_yoy, m2_yoy, sf_inc_month, sf_stock_endval
                FROM macro_monthly
                ORDER BY month
                """,
                conn,
            )
    except Exception:
        _MARKET_CONTEXT_CACHE[key] = pd.DataFrame()
        return pd.DataFrame()
    if valuation.empty:
        _MARKET_CONTEXT_CACHE[key] = pd.DataFrame()
        return pd.DataFrame()
    daily = valuation.merge(moneyflow, on="trade_date", how="left") if not moneyflow.empty else valuation
    daily["date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    numeric_cols = [c for c in daily.columns if c not in {"trade_date", "date"}]
    for column in numeric_cols:
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily["ctx_turnover_pct252"] = _rolling_percentile(daily["ctx_turnover_rate"], 252, 80).fillna(0.5)
    daily["ctx_turnover_z120"] = _zscore(np.log(daily["ctx_turnover_rate"].replace(0.0, np.nan)), 120, 40)
    daily["ctx_volume_ratio_pct252"] = _rolling_percentile(daily["ctx_volume_ratio"], 252, 80).fillna(0.5)
    daily["ctx_moneyflow_z120"] = _zscore(daily.get("ctx_net_mf_amount", pd.Series(index=daily.index, dtype=float)).fillna(0.0), 120, 40)
    daily["ctx_large_moneyflow_z120"] = _zscore(daily.get("ctx_large_net_amount", pd.Series(index=daily.index, dtype=float)).fillna(0.0), 120, 40)
    daily["ctx_market_value_pct756"] = _rolling_percentile(daily["ctx_earnings_yield"], 756, 180).fillna(0.5)
    daily["ctx_market_pb_guard756"] = 1.0 - _rolling_percentile(daily["ctx_pb"], 756, 180).fillna(0.5)
    if not macro.empty:
        macro = macro.copy()
        macro["date"] = pd.to_datetime(macro["month"].astype(str) + "01", format="%Y%m%d") + pd.offsets.MonthEnd(1) + pd.Timedelta(days=10)
        for column in macro.columns:
            if column not in {"month", "date"}:
                macro[column] = pd.to_numeric(macro[column], errors="coerce")
        daily = pd.merge_asof(
            daily.sort_values("date"),
            macro.drop(columns=["month"]).sort_values("date"),
            on="date",
            direction="backward",
        )
    daily = daily[daily["trade_date"].between(start, end)].reset_index(drop=True)
    _MARKET_CONTEXT_CACHE[key] = daily
    return daily.copy()


def _fetch_index_daily(pro: Any, code: str, start: str, end: str) -> pd.DataFrame:
    raw = pro.index_daily(ts_code=code, start_date=start, end_date=end)
    if raw.empty:
        raise RuntimeError(f"index_daily_empty:{code}")
    raw = raw.sort_values("trade_date").reset_index(drop=True)
    raw["date"] = pd.to_datetime(raw["trade_date"], format="%Y%m%d")
    for column in ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]:
        if column in raw:
            raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw["ret"] = raw["close"].pct_change().fillna(0.0)
    return raw


def _fetch_daily_basic(pro: Any, code: str, start: str, end: str) -> pd.DataFrame:
    try:
        basic = pro.index_dailybasic(ts_code=code, start_date=start, end_date=end)
    except Exception:
        return pd.DataFrame()
    if basic.empty:
        return pd.DataFrame()
    basic = basic.sort_values("trade_date").reset_index(drop=True)
    for column in basic.columns:
        if column not in {"ts_code", "trade_date"}:
            basic[column] = pd.to_numeric(basic[column], errors="coerce")
    return basic


def _prepare_features(
    raw: pd.DataFrame,
    basic: pd.DataFrame | None = None,
    market_context: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    frame = raw.copy()
    if basic is not None and not basic.empty:
        frame = frame.merge(basic.drop(columns=["ts_code"], errors="ignore"), on="trade_date", how="left")
    if market_context is not None and not market_context.empty:
        ctx = market_context.drop(columns=["date"], errors="ignore").copy()
        frame = frame.merge(ctx, on="trade_date", how="left")
        ctx_cols = [c for c in ctx.columns if c != "trade_date"]
        for column in ctx_cols:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").ffill()

    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    amount = frame["amount"].astype(float)
    ret = frame["ret"].astype(float)

    def _col(name: str, default: float = np.nan) -> pd.Series:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
        return pd.Series(float(default), index=frame.index, dtype=float)

    for n in [5, 10, 20, 40, 60, 120, 240]:
        frame[f"ma{n}"] = close.rolling(n, min_periods=max(5, n // 3)).mean()
        frame[f"mom{n}"] = close / close.shift(n) - 1.0
    frame["vol20"] = ret.rolling(20, min_periods=10).std(ddof=0) * math.sqrt(252.0)
    frame["vol60"] = ret.rolling(60, min_periods=20).std(ddof=0) * math.sqrt(252.0)
    frame["vol120"] = ret.rolling(120, min_periods=40).std(ddof=0) * math.sqrt(252.0)
    frame["drawdown60"] = close / close.rolling(60, min_periods=20).max() - 1.0
    frame["drawdown120"] = close / close.rolling(120, min_periods=40).max() - 1.0
    frame["amount_pct252"] = _rolling_percentile(amount, 252, 80).fillna(0.5)
    frame["amount_z120"] = _zscore(np.log(amount.replace(0.0, np.nan)), 120, 40)
    frame["up_share20"] = (ret > 0.0).rolling(20, min_periods=10).mean().fillna(0.5)
    frame["range_pct"] = ((high - low) / close).replace([np.inf, -np.inf], np.nan)
    frame["range_pct120"] = _rolling_percentile(frame["range_pct"], 120, 40).fillna(0.5)
    frame["rsi14"] = _rsi(close, 14)
    frame["rsrs_z"], frame["rsrs_r2"] = _rsrs(high, low)
    frame["adx14"] = _adx(high, low, close, 14)

    features: dict[str, list[str]] = {"macro": [], "price_volume": [], "sentiment": [], "valuation": [], "risk": []}

    # 量价因子：把原左侧修复/反转和右侧趋势合并到同一个价格-成交体系。
    frame["f_trend20"] = _sigmoid(frame["mom20"].fillna(0.0), 0.035).values
    frame["f_trend60"] = _sigmoid(frame["mom60"].fillna(0.0), 0.075).values
    frame["f_trend120"] = _sigmoid(frame["mom120"].fillna(0.0), 0.13).values
    frame["f_ma_distance"] = _sigmoid((frame["ma60"] / frame["ma120"] - 1.0).fillna(0.0), 0.035).values
    frame["f_rsrs"] = _sigmoid(frame["rsrs_z"].fillna(0.0), 0.85).values
    frame["f_amount_trend"] = _clip01(0.55 * frame["amount_pct252"] + 0.45 * frame["up_share20"]).values
    frame["f_repair"] = _clip01(
        0.45 * _sigmoid(-frame["drawdown60"].fillna(0.0), 0.08)
        + 0.35 * _sigmoid(frame["mom10"].fillna(0.0), 0.025)
        + 0.20 * _sigmoid(frame["rsrs_z"].fillna(0.0).diff(5).fillna(0.0), 0.35)
    ).values
    frame["f_low_vol"] = (1.0 - _rolling_percentile(frame["vol20"], 252, 80).fillna(0.5)).clip(0.0, 1.0).values
    frame["f_oversold_reversal"] = _clip01(
        0.60 * (1.0 - frame["rsi14"].clip(0.0, 100.0) / 100.0)
        + 0.40 * _sigmoid(frame["mom5"].fillna(0.0), 0.018)
    ).values
    trend_direction = _clip01(
        0.50
        + 0.28 * np.tanh(frame["mom20"].fillna(0.0) / 0.055)
        + 0.22 * np.tanh(((frame["ma20"] / frame["ma60"] - 1.0).fillna(0.0)) / 0.030)
    )
    adx_strength = ((frame["adx14"].fillna(20.0) - 15.0) / 25.0).clip(0.0, 1.0)
    frame["f_adx_trend"] = _clip01(0.50 * (1.0 - adx_strength) + trend_direction * adx_strength).values
    std20 = close.rolling(20, min_periods=10).std(ddof=0).replace(0.0, np.nan)
    boll_z = ((close - frame["ma20"]) / (2.0 * std20)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    frame["f_bollinger_path"] = _clip01(0.50 + 0.35 * np.tanh(boll_z) + 0.15 * np.tanh(frame["mom5"].fillna(0.0) / 0.020)).values
    roll_min = close.rolling(252, min_periods=80).min()
    roll_max = close.rolling(252, min_periods=80).max()
    tr_degree = ((close - roll_min) / (roll_max - roll_min).replace(0.0, np.nan)).clip(0.0, 1.0).fillna(0.5)
    frame["tr_degree"] = tr_degree
    frame["f_tr_location"] = _clip01(
        np.where(
            (tr_degree < 0.20) & (frame["mom5"].fillna(0.0) > 0.0),
            0.85,
            np.where(
                (tr_degree > 0.86) & (frame["mom5"].fillna(0.0) < 0.0),
                0.20,
                0.50 + 0.45 * np.tanh(frame["mom20"].fillna(0.0) / 0.075),
            ),
        )
    ).values
    features["price_volume"] = [
        "f_trend20",
        "f_trend60",
        "f_trend120",
        "f_ma_distance",
        "f_rsrs",
        "f_amount_trend",
        "f_repair",
        "f_low_vol",
        "f_oversold_reversal",
        "f_adx_trend",
        "f_bollinger_path",
        "f_tr_location",
    ]

    # 估值因子：指数自身估值优先，缺失时使用全市场估值上下文做温和补充。
    pe_col = "pe_ttm" if "pe_ttm" in frame.columns else "pe" if "pe" in frame.columns else None
    pb_col = "pb" if "pb" in frame.columns else None
    if pe_col:
        pe = pd.to_numeric(frame[pe_col], errors="coerce")
        earnings_yield = (1.0 / pe.where(pe > 0.0)).replace([np.inf, -np.inf], np.nan)
        frame["f_pe_value"] = _rolling_percentile(earnings_yield, 756, 180).fillna(_col("ctx_market_value_pct756", 0.5)).fillna(0.5)
        frame["f_valuation_extreme_guard"] = (1.0 - _rolling_percentile(pe, 756, 180).fillna(0.5)).clip(0.0, 1.0)
    else:
        frame["f_pe_value"] = _col("ctx_market_value_pct756", 0.5).fillna(0.5)
        frame["f_valuation_extreme_guard"] = _col("ctx_market_pb_guard756", 0.5).fillna(0.5)
    if pb_col:
        pb = pd.to_numeric(frame[pb_col], errors="coerce")
        frame["f_pb_value"] = (1.0 - _rolling_percentile(pb, 756, 180).fillna(0.5)).clip(0.0, 1.0)
    else:
        frame["f_pb_value"] = _col("ctx_market_pb_guard756", 0.5).fillna(0.5)
    dividend_source = None
    for dy_col in ("dv_ratio", "dv_ttm", "ctx_dividend_yield"):
        if dy_col in frame.columns:
            dividend_source = pd.to_numeric(frame[dy_col], errors="coerce")
            break
    frame["f_dividend_value"] = _rolling_percentile(dividend_source, 756, 180).fillna(0.5) if dividend_source is not None else 0.5
    frame["f_erp_proxy"] = _clip01(0.65 * frame["f_pe_value"] + 0.35 * _col("ctx_market_value_pct756", 0.5).fillna(0.5)).values
    frame["f_value_repair"] = _clip01(0.58 * frame["f_erp_proxy"] + 0.42 * _sigmoid(-frame["drawdown120"].fillna(0.0), 0.16)).values
    features["valuation"] = ["f_pe_value", "f_pb_value", "f_dividend_value", "f_erp_proxy", "f_valuation_extreme_guard", "f_value_repair"]

    # 宏观因子：月度数据按可得性滞后后映射到日频，输出增长、流动性、信用、通胀四类方向。
    pmi = _col("pmi_composite", np.nan).combine_first(_col("pmi_manufacturing", 50.0)).ffill()
    m1 = _col("m1_yoy", np.nan).ffill()
    m2 = _col("m2_yoy", np.nan).ffill()
    sf_stock = _col("sf_stock_endval", np.nan).ffill()
    sf_inc = _col("sf_inc_month", np.nan).ffill()
    cpi = _col("cpi_national_yoy", np.nan).ffill()
    ppi = _col("ppi_yoy", np.nan).ffill()
    frame["macro_m1m2_gap"] = m1 - m2
    frame["f_macro_growth"] = _clip01(0.50 + 0.24 * _zscore(pmi.diff(63), 756, 180) + 0.16 * _zscore(pmi - 50.0, 756, 180)).fillna(0.5).values
    frame["f_macro_liquidity"] = _clip01(0.50 + 0.22 * _zscore(frame["macro_m1m2_gap"], 756, 180) + 0.14 * _zscore(m2.diff(63), 756, 180)).fillna(0.5).values
    frame["f_macro_credit"] = _clip01(
        0.50
        + 0.22 * _zscore(sf_stock.pct_change(63, fill_method=None), 756, 180)
        + 0.12 * _zscore(sf_inc.pct_change(252, fill_method=None), 756, 180)
    ).fillna(0.5).values
    inflation_pressure = pd.concat([cpi, ppi], axis=1).mean(axis=1)
    frame["f_macro_inflation_relief"] = (1.0 - _rolling_percentile(inflation_pressure, 756, 180).fillna(0.5)).clip(0.0, 1.0).values
    frame["f_macro_policy_mix"] = _clip01(
        0.32 * frame["f_macro_growth"]
        + 0.32 * frame["f_macro_liquidity"]
        + 0.24 * frame["f_macro_credit"]
        + 0.12 * frame["f_macro_inflation_relief"]
    ).values
    features["macro"] = ["f_macro_growth", "f_macro_liquidity", "f_macro_credit", "f_macro_inflation_relief", "f_macro_policy_mix"]

    # 情绪因子：弱情绪只做左侧修复，强情绪更多跟随，尾部风险交给估值和量价风控。
    low_sentiment_repair = ((frame["amount_pct252"] <= 0.15) & (frame["mom5"].fillna(0.0) > 0.0)).astype(float)
    strong_sentiment_follow = ((frame["amount_pct252"] >= 0.60) & (frame["mom20"].fillna(0.0) > -0.02)).astype(float)
    frame["f_sentiment_v"] = _clip01(0.50 * low_sentiment_repair + 0.50 * strong_sentiment_follow + 0.25 * frame["up_share20"]).values
    frame["f_sentiment_flow_proxy"] = _clip01(0.45 * frame["amount_pct252"] + 0.55 * _sigmoid(frame["mom20"].fillna(0.0), 0.045)).values
    ctx_turnover = _col("ctx_turnover_pct252", 0.5).fillna(0.5)
    ctx_flow = _col("ctx_moneyflow_z120", 0.0).fillna(0.0)
    ctx_large_flow = _col("ctx_large_moneyflow_z120", 0.0).fillna(0.0)
    frame["f_market_turnover_follow"] = _clip01(0.45 * ctx_turnover + 0.30 * frame["up_share20"] + 0.25 * _sigmoid(frame["mom20"].fillna(0.0), 0.050)).values
    frame["f_moneyflow_confirm"] = _clip01(0.50 * _sigmoid(ctx_flow, 1.2) + 0.50 * _sigmoid(ctx_large_flow, 1.2)).values
    frame["f_single_side_sentiment"] = _clip01(
        np.where(
            (ctx_turnover < 0.22) & (frame["mom5"].fillna(0.0) > 0.0),
            0.85,
            np.where(
                ctx_turnover >= 0.60,
                0.72 + 0.18 * _sigmoid(frame["mom20"].fillna(0.0), 0.055),
                np.where((ctx_turnover < 0.40) & (frame["mom20"].fillna(0.0) < 0.0), 0.28, 0.50),
            ),
        )
    ).values
    features["sentiment"] = [
        "f_sentiment_v",
        "f_sentiment_flow_proxy",
        "f_market_turnover_follow",
        "f_moneyflow_confirm",
        "f_single_side_sentiment",
    ]

    risk_votes = pd.DataFrame(index=frame.index)
    risk_votes["below_ma60"] = (close < frame["ma60"]).astype(float)
    risk_votes["below_ma120"] = (close < frame["ma120"]).astype(float)
    risk_votes["ma60_below_ma120"] = (frame["ma60"] < frame["ma120"]).astype(float)
    risk_votes["mom20_bad"] = (frame["mom20"] < -0.035).astype(float)
    risk_votes["mom60_bad"] = (frame["mom60"] < -0.055).astype(float)
    risk_votes["rsrs_bad"] = (frame["rsrs_z"] < -0.55).astype(float)
    risk_votes["vol_bad"] = (frame["vol20"] > frame["vol20"].rolling(252, min_periods=80).quantile(0.80)).astype(float)
    risk_votes["drawdown_bad"] = (frame["drawdown60"] < -0.08).astype(float)
    frame["risk_votes"] = risk_votes.sum(axis=1)
    enhanced_risk_votes = frame["risk_votes"] + (frame["f_valuation_extreme_guard"] < 0.18).astype(float) + (frame["f_macro_policy_mix"] < 0.34).astype(float)
    frame["risk_votes_enhanced"] = enhanced_risk_votes
    frame["f_risk_guard"] = (1.0 - frame["risk_votes"] / risk_votes.shape[1]).clip(0.0, 1.0)
    frame["f_macro_valuation_guard"] = (1.0 - frame["risk_votes_enhanced"] / (risk_votes.shape[1] + 2)).clip(0.0, 1.0)
    frame["f_crash_guard"] = _clip01(
        1.0
        - 0.35 * (frame["mom20"].fillna(0.0) < -0.08).astype(float)
        - 0.25 * (frame["rsrs_z"].fillna(0.0) < -1.00).astype(float)
        - 0.25 * (frame["range_pct120"] > 0.85).astype(float)
        - 0.15 * (close < frame["ma120"]).astype(float)
    ).values
    frame["f_top_guard"] = _clip01(
        1.0
        - 0.50 * ((frame["amount_pct252"] > 0.92) & (frame["mom20"] < 0.0)).astype(float)
        - 0.25 * ((frame["rsi14"] > 75.0) & (frame["mom5"] < 0.0)).astype(float)
        - 0.25 * ((frame["ma20"] < frame["ma60"]) & (frame["mom20"] < 0.0)).astype(float)
    ).values
    frame["f_drawdown_guard"] = _clip01(1.0 + frame["drawdown60"].fillna(0.0) / 0.18).values
    frame["f_volatility_guard"] = (1.0 - _rolling_percentile(frame["vol20"], 252, 80).fillna(0.5)).clip(0.0, 1.0).values
    features["risk"] = ["f_risk_guard", "f_crash_guard", "f_top_guard", "f_drawdown_guard", "f_volatility_guard", "f_macro_valuation_guard"]

    # Compatibility aliases preserve the old left/right candidate inputs exactly enough for the no-degradation guard.
    features["left"] = list(dict.fromkeys(["f_repair", "f_low_vol", "f_oversold_reversal", "f_pe_value", "f_pb_value", "f_dividend_value"]))
    features["right"] = list(dict.fromkeys(["f_trend20", "f_trend60", "f_trend120", "f_ma_distance", "f_rsrs", "f_amount_trend"]))
    return frame, features


def _effective_group_score(
    frame: pd.DataFrame,
    columns: Iterable[str],
    *,
    cfg: TimingConfig,
    forward_days: int = 20,
    lookback: int = 756,
) -> pd.Series:
    columns = [column for column in columns if column in frame.columns]
    if not columns:
        return pd.Series(np.full(len(frame), 0.5), index=frame.index)
    future = frame["close"].shift(-forward_days) / frame["close"] - 1.0
    scores = []
    weights = []
    for column in columns:
        signal = pd.to_numeric(frame[column], errors="coerce").astype(float).clip(0.0, 1.0)
        centered = signal - 0.5
        ic = signal.rolling(lookback, min_periods=252).corr(future).shift(forward_days)
        edge = (centered * future).rolling(lookback, min_periods=252).mean().shift(forward_days)
        stable_ic = ic.rolling(63, min_periods=20).mean()
        stable_edge = edge.rolling(63, min_periods=20).mean()
        sign = np.where((stable_ic.fillna(0.0) + 8.0 * stable_edge.fillna(0.0)) >= 0.0, 1.0, -1.0)
        signed_signal = pd.Series(np.where(sign >= 0.0, signal, 1.0 - signal), index=frame.index)
        ic_strength = stable_ic.abs().fillna(0.0) * cfg.efficacy_strength
        edge_scale = future.rolling(lookback, min_periods=252).std(ddof=0).shift(forward_days).replace(0.0, np.nan)
        edge_strength = (stable_edge.abs() / edge_scale).replace([np.inf, -np.inf], np.nan).fillna(0.0) * 2.0
        weight = (0.65 * ic_strength + 0.35 * edge_strength).clip(0.0, 1.0)
        prior = pd.Series(np.where(frame.index.to_series() < 320, 1.0, 0.12), index=frame.index)
        weight = np.maximum(weight, prior)
        scores.append(signed_signal.clip(0.0, 1.0))
        weights.append(pd.Series(weight, index=frame.index))
    score_frame = pd.concat(scores, axis=1)
    weight_frame = pd.concat(weights, axis=1)
    denom = weight_frame.sum(axis=1).replace(0.0, np.nan)
    score = (score_frame * weight_frame).sum(axis=1) / denom
    return score.fillna(score_frame.mean(axis=1)).fillna(0.5).clip(0.0, 1.0)


def _factor_test_summary(frame: pd.DataFrame, groups: dict[str, list[str]], forward_days: int = 20) -> dict[str, Any]:
    close = frame["close"].astype(float)
    future_by_horizon = {
        horizon: (close.shift(-horizon) / close - 1.0).replace([np.inf, -np.inf], np.nan)
        for horizon in (5, 20, 60)
    }
    if "date" in frame.columns:
        date_source = frame["date"]
    elif "trade_date" in frame.columns:
        date_source = frame["trade_date"]
    else:
        date_source = pd.Series(frame.index, index=frame.index)
    date_axis = pd.to_datetime(date_source, errors="coerce").dt.strftime("%Y-%m-%d")
    if date_axis.isna().all():
        date_axis = pd.Series([str(item) for item in frame.index], index=frame.index)

    def _series_payload(signal: pd.Series, rolling_ic: pd.Series) -> dict[str, Any]:
        path = signal.replace([np.inf, -np.inf], np.nan).clip(0.0, 1.0)
        pit_ric = rolling_ic.replace([np.inf, -np.inf], np.nan).shift(forward_days)
        cumulative = pit_ric.fillna(0.0).cumsum()
        payload = pd.DataFrame(
            {
                "date": date_axis,
                "signal": path,
                "rolling_ic": pit_ric,
                "cumulative_ic": cumulative,
            },
            index=frame.index,
        ).dropna(subset=["date"])
        if len(payload) > 756:
            payload = payload.tail(756)
        return {
            "dates": payload["date"].astype(str).tolist(),
            "signal": pd.to_numeric(payload["signal"], errors="coerce").round(6).where(payload["signal"].notna(), None).tolist(),
            "rolling_ic": pd.to_numeric(payload["rolling_ic"], errors="coerce").round(6).where(payload["rolling_ic"].notna(), None).tolist(),
            "cumulative_ic": pd.to_numeric(payload["cumulative_ic"], errors="coerce").round(6).where(payload["cumulative_ic"].notna(), None).tolist(),
        }

    rows: list[dict[str, Any]] = []
    for family in ("macro", "price_volume", "sentiment", "valuation"):
        for column in groups.get(family, []):
            if column not in frame.columns:
                continue
            signal = pd.to_numeric(frame[column], errors="coerce").astype(float).clip(0.0, 1.0)
            future = future_by_horizon[forward_days]
            valid = signal.notna() & future.notna()
            n = int(valid.sum())
            if n < 120 or float(signal[valid].std(ddof=0)) <= 1.0e-12:
                continue
            sig = signal[valid]
            fut = future[valid]
            ic20 = float(np.corrcoef(sig, fut)[0, 1]) if n >= 3 else 0.0
            if not math.isfinite(ic20):
                ic20 = 0.0
            signed = sig if ic20 >= 0.0 else 1.0 - sig
            centered = signed - 0.5
            edge = centered * fut
            edge_std = float(edge.std(ddof=1)) if n > 1 else 0.0
            t_value = 0.0 if edge_std <= 1.0e-12 else float(edge.mean() / edge_std * math.sqrt(n))
            rolling_ic = signal.rolling(126, min_periods=60).corr(future)
            ric = rolling_ic.replace([np.inf, -np.inf], np.nan).dropna()
            icir = 0.0 if len(ric) < 20 or float(ric.std(ddof=0)) <= 1.0e-12 else float(ric.mean() / ric.std(ddof=0))
            horizon_ics: dict[str, float] = {}
            for horizon, horizon_future in future_by_horizon.items():
                mask = signal.notna() & horizon_future.notna()
                if int(mask.sum()) < 120 or float(signal[mask].std(ddof=0)) <= 1.0e-12:
                    horizon_ics[str(horizon)] = 0.0
                    continue
                val = float(np.corrcoef(signal[mask], horizon_future[mask])[0, 1])
                horizon_ics[str(horizon)] = 0.0 if not math.isfinite(val) else val
            q_hi = float(signed.quantile(0.70))
            q_lo = float(signed.quantile(0.30))
            high_mask = signed >= q_hi
            low_mask = signed <= q_lo
            spread = float(fut[high_mask].mean() - fut[low_mask].mean()) if bool(high_mask.any() and low_mask.any()) else 0.0
            fut_scale = float(fut.std(ddof=0))
            decay = 0.0 if abs(horizon_ics["5"]) <= 1.0e-12 else abs(horizon_ics["60"]) / max(abs(horizon_ics["5"]), 1.0e-12)
            spread_strength = 0.0 if fut_scale <= 1.0e-12 else max(0.0, spread / fut_scale)
            quality = (
                0.42 * min(1.0, abs(icir) / 1.2)
                + 0.26 * min(1.0, abs(t_value) / 3.0)
                + 0.22 * min(1.0, spread_strength)
                + 0.10 * min(1.0, decay)
            )
            rows.append(
                {
                    "family": family,
                    "family_label": FACTOR_FAMILY_LABELS.get(family, family),
                    "factor": column,
                    "direction": "正向" if ic20 >= 0.0 else "反向",
                    "sample": n,
                    "ic": round(ic20, 6),
                    "icir": round(icir, 6),
                    "t_value": round(t_value, 6),
                    "ic_decay": round(decay, 6),
                    "long_short_return": round(spread, 6),
                    "quality": round(float(quality), 6),
                    "admitted": bool(quality >= 0.18 and (abs(t_value) >= 0.75 or spread > 0.0)),
                    "horizon_ic": {k: round(v, 6) for k, v in horizon_ics.items()},
                    "series": _series_payload(signal, rolling_ic),
                }
            )
    family_rows: list[dict[str, Any]] = []
    for family in ("macro", "price_volume", "sentiment", "valuation"):
        scoped = [row for row in rows if row["family"] == family]
        admitted = [row for row in scoped if row["admitted"]]
        family_rows.append(
            {
                "family": family,
                "family_label": FACTOR_FAMILY_LABELS.get(family, family),
                "factor_count": len(scoped),
                "admitted_count": len(admitted),
                "avg_quality": round(float(np.mean([row["quality"] for row in scoped])) if scoped else 0.0, 6),
                "avg_icir": round(float(np.mean([row["icir"] for row in scoped])) if scoped else 0.0, 6),
                "avg_long_short": round(float(np.mean([row["long_short_return"] for row in scoped])) if scoped else 0.0, 6),
            }
        )
    rows.sort(key=lambda item: (item["admitted"], item["quality"], abs(item["t_value"])), reverse=True)
    return {
        "forward_days": forward_days,
        "families": family_rows,
        "top_factors": rows[:16],
        "factor_count": len(rows),
        "admitted_factor_count": sum(1 for row in rows if row["admitted"]),
    }


def _score_level(score: pd.Series, small: float, medium: float, large: float) -> pd.Series:
    values = pd.to_numeric(score, errors="coerce").fillna(0.5).astype(float)
    level = pd.Series(0, index=values.index, dtype=int)
    level = level.mask(values >= small, 1)
    level = level.mask(values >= medium, 2)
    level = level.mask(values >= large, 3)
    return level.astype(int)


def _to_five_bucket(position: pd.Series | np.ndarray | float) -> pd.Series:
    return (pd.Series(position).astype(float).clip(0.0, 1.0) * 4.0).round().div(4.0).clip(0.0, 1.0)


def _four_dimension_fusion_candidate(features: pd.DataFrame, groups: dict[str, list[str]], profile: FusionProfile) -> pd.DataFrame:
    out = features.copy()
    cfg = TimingConfig(name=profile.name, efficacy_strength=3.2, position_smooth=profile.smooth)
    out["macro_score"] = _effective_group_score(out, groups.get("macro", []), cfg=cfg)
    out["price_volume_score"] = _effective_group_score(out, groups.get("price_volume", []), cfg=cfg)
    out["sentiment_score"] = _effective_group_score(out, groups.get("sentiment", []), cfg=cfg)
    out["valuation_score"] = _effective_group_score(out, groups.get("valuation", []), cfg=cfg)
    out["risk_score"] = _effective_group_score(out, groups.get("risk", []), cfg=cfg)
    total = profile.macro_weight + profile.price_volume_weight + profile.sentiment_weight + profile.valuation_weight
    out["attack_score"] = (
        profile.macro_weight * out["macro_score"]
        + profile.price_volume_weight * out["price_volume_score"]
        + profile.sentiment_weight * out["sentiment_score"]
        + profile.valuation_weight * out["valuation_score"]
    ) / total
    out["defense_score"] = (
        profile.defense_risk_weight * (1.0 - out["risk_score"])
        + profile.defense_price_weight * (1.0 - out["price_volume_score"])
        + profile.defense_valuation_weight * (1.0 - out["valuation_score"])
        + profile.defense_macro_weight * (1.0 - out["macro_score"])
    ).clip(0.0, 1.0)
    attack_level = _score_level(out["attack_score"], profile.attack_small, profile.attack_medium, profile.attack_large)
    defense_level = _score_level(out["defense_score"], profile.defense_small, profile.defense_medium, profile.defense_large)
    raw = 0.50 + 0.25 * (attack_level.astype(float) - defense_level.astype(float))
    position = _to_five_bucket(raw)

    close = out["close"].astype(float)
    strong = (
        (attack_level >= 2)
        & (defense_level <= 1)
        & (close > out["ma20"])
        & (out["ma20"] > out["ma60"])
        & (out["mom20"] > 0.0)
        & (out["rsrs_z"] > -0.30)
    ).fillna(False)
    repair = (
        (out["drawdown60"] < -0.08)
        & (out["mom10"] > 0.0)
        & (out["valuation_score"] > 0.52)
        & (out.get("risk_votes_enhanced", out["risk_votes"]) <= 6)
    ).fillna(False)
    weak = ((defense_level >= 2) & (attack_level <= 1)).fillna(False)
    crash = (
        ((defense_level >= 3) & ((out["mom20"] < -0.055) | (close < out["ma120"])))
        | (out.get("risk_votes_enhanced", out["risk_votes"]) >= 8)
        | ((out["rsrs_z"] < -1.10) & (out["mom20"] < -0.035))
    ).fillna(False)
    top_break = (
        (out["f_valuation_extreme_guard"] < 0.14)
        & (out["price_volume_score"] < 0.46)
        & (out["mom20"] < 0.0)
    ).fillna(False)
    position = position.mask(strong, np.maximum(position, profile.strong_floor))
    position = position.mask(repair, np.maximum(position, profile.repair_floor))
    position = position.mask(weak, np.minimum(position, profile.weak_cap))
    position = position.mask(top_break, np.minimum(position, 0.50))
    position = position.mask(crash, np.minimum(position, profile.crash_cap))
    position = _to_five_bucket(position)

    smooth = max(1, int(profile.smooth))
    out["raw_position"] = position.values
    out["position"] = position.rolling(smooth, min_periods=1).mean().clip(0.0, 1.0)
    out["bucket_position"] = out["raw_position"]
    out["attack_level"] = attack_level
    out["defense_level"] = defense_level
    out["left_score"] = (0.50 * out["valuation_score"] + 0.50 * out["macro_score"]).clip(0.0, 1.0)
    out["right_score"] = (0.55 * out["price_volume_score"] + 0.45 * out["sentiment_score"]).clip(0.0, 1.0)
    out["composite_score"] = out["attack_score"].clip(0.0, 1.0)
    out["model_name"] = profile.name
    out.attrs["min_position"] = 0.0
    out.attrs["max_position"] = 1.0
    out.attrs["borrow_annual"] = 0.035
    return out


def _guided_five_bucket_candidate(
    features: pd.DataFrame,
    groups: dict[str, list[str]],
    base: pd.DataFrame,
    *,
    name: str = "因子检验五档融合",
    profile: FusionProfile = FUSION_PROFILES[0],
    base_weight: float = 0.75,
    smooth: int = 3,
) -> pd.DataFrame:
    fusion = _four_dimension_fusion_candidate(features, groups, profile)
    out = features.copy()
    for column in [
        "macro_score",
        "price_volume_score",
        "sentiment_score",
        "valuation_score",
        "risk_score",
        "attack_score",
        "defense_score",
        "attack_level",
        "defense_level",
    ]:
        out[column] = fusion[column]
    base_position = pd.to_numeric(base["position"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
    fused_position = base_weight * base_position + (1.0 - base_weight) * pd.to_numeric(fusion["bucket_position"], errors="coerce").fillna(0.5)
    position = _to_five_bucket(fused_position)
    strong = (
        (out["attack_level"] >= 2)
        & (out["defense_level"] <= 1)
        & (out["mom20"] > 0.0)
        & (out["close"] > out["ma20"])
    ).fillna(False)
    protection = (
        (out["defense_level"] >= 3)
        | ((out["risk_votes"] >= 6) & (out["mom20"] < 0.0))
        | ((out["rsrs_z"] < -1.05) & (out["mom20"] < -0.04))
    ).fillna(False)
    repair = (
        (out["drawdown60"] < -0.10)
        & (out["mom10"] > 0.0)
        & (out["attack_level"] >= 1)
        & (out["defense_level"] <= 2)
    ).fillna(False)
    position = position.mask(strong, np.maximum(position, 1.0))
    position = position.mask(repair, np.maximum(position, 0.75))
    position = position.mask(protection, np.minimum(position, 0.25))
    position = _to_five_bucket(position)
    out["raw_position"] = position.values
    out["bucket_position"] = position.values
    out["position"] = position.rolling(max(1, int(smooth)), min_periods=1).mean().clip(0.0, 1.0)
    out["left_score"] = (0.50 * out["valuation_score"] + 0.50 * out["macro_score"]).clip(0.0, 1.0)
    out["right_score"] = (0.55 * out["price_volume_score"] + 0.45 * out["sentiment_score"]).clip(0.0, 1.0)
    out["composite_score"] = out["attack_score"].clip(0.0, 1.0)
    out["model_name"] = name
    out.attrs["min_position"] = 0.0
    out.attrs["max_position"] = 1.0
    out.attrs["borrow_annual"] = 0.035
    return out


def _build_position(frame: pd.DataFrame, features: dict[str, list[str]], cfg: TimingConfig) -> pd.DataFrame:
    out = frame.copy()
    out["left_score"] = _effective_group_score(out, features["left"], cfg=cfg)
    out["right_score"] = _effective_group_score(out, features["right"], cfg=cfg)
    out["sentiment_score"] = _effective_group_score(out, features["sentiment"], cfg=cfg)
    out["risk_score"] = _effective_group_score(out, features["risk"], cfg=cfg)
    total = cfg.left_weight + cfg.right_weight + cfg.sentiment_weight + cfg.risk_weight
    out["composite_score"] = (
        cfg.left_weight * out["left_score"]
        + cfg.right_weight * out["right_score"]
        + cfg.sentiment_weight * out["sentiment_score"]
        + cfg.risk_weight * out["risk_score"]
    ) / total

    position = pd.Series(cfg.min_core_position, index=out.index, dtype=float)
    position = position.mask(out["composite_score"] >= cfg.high_threshold, 1.00)
    position = position.mask((out["composite_score"] >= cfg.mid_threshold) & (out["composite_score"] < cfg.high_threshold), 0.75)
    position = position.mask((out["composite_score"] >= cfg.low_threshold) & (out["composite_score"] < cfg.mid_threshold), 0.50)
    position = position.mask(out["composite_score"] < cfg.low_threshold, 0.25)

    close = out["close"]
    strong_trend = (
        (close > out["ma20"])
        & (out["ma20"] > out["ma60"])
        & (out["ma60"] > out["ma120"])
        & (out["mom20"] > 0.0)
        & (out["rsrs_z"] > -0.20)
    )
    repair = (
        (out["drawdown60"] < -0.08)
        & (out["mom5"] > 0.0)
        & (out["mom10"] > 0.0)
        & (out["rsrs_z"].diff(5) > 0.0)
    )
    bear = (
        (close < out["ma120"])
        & (out["ma60"] < out["ma120"])
        & (out["mom60"] < -0.03)
    )
    top = (
        (out["amount_pct252"] > 0.92)
        & (out["mom20"] < 0.0)
        & ((out["right_score"] < 0.48) | (out["risk_score"] < 0.45))
    )
    crash = (
        (out["mom20"] < -0.08).astype(int)
        + (close < out["ma60"]).astype(int)
        + (out["rsrs_z"] < -1.0).astype(int)
        + (out["range_pct120"] > 0.85).astype(int)
        + (out["risk_votes"] >= cfg.crash_votes).astype(int)
    )

    position = position.mask(strong_trend, np.maximum(position, cfg.strong_position))
    position = position.mask(repair, np.maximum(position, cfg.repair_position))
    position = position.mask(bear, np.minimum(position, cfg.bear_position_cap))
    position = position.mask(top, np.minimum(position, cfg.top_position_cap))
    position = position.mask(out["risk_votes"] >= cfg.danger_votes, np.minimum(position, cfg.danger_position))
    position = position.mask(crash >= cfg.crash_votes, np.minimum(position, cfg.crash_position))

    smooth = max(1, int(cfg.position_smooth))
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(smooth, min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = cfg.name
    return out


def _max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def _annual_return(returns: pd.Series) -> float:
    values = returns.dropna()
    if values.empty:
        return 0.0
    return float(np.prod(1.0 + values) ** (252.0 / len(values)) - 1.0)


def _sharpe(returns: pd.Series) -> float:
    values = returns.dropna()
    vol = float(values.std(ddof=0) * math.sqrt(252.0))
    if vol <= 1.0e-12:
        return 0.0
    return float(values.mean() * 252.0 / vol)


def _backtest(signal_frame: pd.DataFrame, *, cash_annual: float = 0.018, cost_bps: float = 1.0) -> pd.DataFrame:
    out = signal_frame.copy()
    min_position = float(signal_frame.attrs.get("min_position", 0.0))
    max_position = float(signal_frame.attrs.get("max_position", 1.0))
    borrow_annual = float(signal_frame.attrs.get("borrow_annual", 0.035))
    pos = out["position"].shift(1).fillna(0.5).clip(min_position, max_position)
    cash_daily = (1.0 + cash_annual) ** (1.0 / 252.0) - 1.0
    borrow_daily = (1.0 + borrow_annual) ** (1.0 / 252.0) - 1.0
    turnover_cost = pos.diff().abs().fillna(0.0) * cost_bps / 10000.0
    funding = pd.Series(
        np.where(pos <= 1.0, (1.0 - pos) * cash_daily, (1.0 - pos) * borrow_daily),
        index=out.index,
    )
    out["strategy_return"] = pos * out["ret"].fillna(0.0) + funding - turnover_cost
    out["benchmark_return"] = out["ret"].fillna(0.0)
    out["strategy_nav"] = (1.0 + out["strategy_return"]).cumprod()
    out["benchmark_nav"] = (1.0 + out["benchmark_return"]).cumprod()
    out["relative_strength"] = out["strategy_nav"] / out["benchmark_nav"]
    out["applied_position"] = pos
    return out


def _monthly_consistency_metrics(bt: pd.DataFrame) -> dict[str, float]:
    frame = bt.dropna(subset=["strategy_return", "benchmark_return"]).copy()
    if frame.empty:
        return {
            "monthly_excess_win_rate": 0.0,
            "monthly_positive_rate": 0.0,
            "down_month_protection_rate": 0.0,
            "up_month_capture_rate": 0.0,
        }
    frame["year_month"] = frame["date"].dt.to_period("M")
    monthly_rows: list[tuple[float, float]] = []
    for _month, scoped in frame.groupby("year_month"):
        if len(scoped) < 5:
            continue
        strategy_return = float(np.prod(1.0 + scoped["strategy_return"]) - 1.0)
        benchmark_return = float(np.prod(1.0 + scoped["benchmark_return"]) - 1.0)
        monthly_rows.append((strategy_return, benchmark_return))
    if not monthly_rows:
        return {
            "monthly_excess_win_rate": 0.0,
            "monthly_positive_rate": 0.0,
            "down_month_protection_rate": 0.0,
            "up_month_capture_rate": 0.0,
        }
    values = np.asarray(monthly_rows, dtype=float)
    strategy = values[:, 0]
    benchmark = values[:, 1]
    down_mask = benchmark < 0.0
    up_mask = benchmark > 0.0
    return {
        "monthly_excess_win_rate": float(np.mean(strategy > benchmark)),
        "monthly_positive_rate": float(np.mean(strategy > 0.0)),
        "down_month_protection_rate": float(np.mean(strategy[down_mask] > benchmark[down_mask])) if bool(np.any(down_mask)) else 0.0,
        "up_month_capture_rate": float(np.mean(strategy[up_mask] > 0.0)) if bool(np.any(up_mask)) else 0.0,
    }


def _metrics(bt: pd.DataFrame) -> dict[str, float]:
    sr = bt["strategy_return"]
    br = bt["benchmark_return"]
    strategy_ann = _annual_return(sr)
    benchmark_ann = _annual_return(br)
    years = sorted(set(bt["date"].dt.year))
    win = 0
    positive_years = 0
    count = 0
    for year in years:
        part = bt[bt["date"].dt.year == year]
        if len(part) < 20:
            continue
        s = float(np.prod(1.0 + part["strategy_return"]) - 1.0)
        b = float(np.prod(1.0 + part["benchmark_return"]) - 1.0)
        win += int(s > b)
        positive_years += int(s > 0.0)
        count += 1
    payload = {
        "strategy_ann": strategy_ann,
        "benchmark_ann": benchmark_ann,
        "excess_ann": strategy_ann - benchmark_ann,
        "strategy_sharpe": _sharpe(sr),
        "benchmark_sharpe": _sharpe(br),
        "strategy_mdd": _max_drawdown(bt["strategy_nav"]),
        "benchmark_mdd": _max_drawdown(bt["benchmark_nav"]),
        "avg_position": float(bt["applied_position"].mean()),
        "annual_excess_win_rate": 0.0 if count == 0 else win / count,
        "annual_positive_rate": 0.0 if count == 0 else positive_years / count,
    }
    payload.update(_monthly_consistency_metrics(bt))
    return payload


def _selection_score(metrics: dict[str, float]) -> float:
    excess = metrics["excess_ann"]
    mdd_improve = metrics["strategy_mdd"] - metrics["benchmark_mdd"]
    return (
        metrics["strategy_sharpe"]
        + 3.6 * max(excess, -0.05)
        + 1.5 * mdd_improve
        + 0.80 * metrics.get("monthly_excess_win_rate", 0.0)
        + 0.45 * metrics.get("annual_excess_win_rate", 0.0)
        + 0.30 * metrics.get("down_month_protection_rate", 0.0)
        + 0.20 * metrics.get("up_month_capture_rate", 0.0)
        - 0.20 * max(0.0, 0.45 - metrics["avg_position"])
    )


def _mean_score(frame: pd.DataFrame, columns: Iterable[str]) -> pd.Series:
    usable = [column for column in columns if column in frame.columns]
    if not usable:
        return pd.Series(np.full(len(frame), 0.5), index=frame.index)
    return frame[usable].astype(float).mean(axis=1).fillna(0.5).clip(0.0, 1.0)


def _legacy_left_right_candidate(features: pd.DataFrame, groups: dict[str, list[str]]) -> pd.DataFrame:
    out = features.copy()
    out["left_score"] = _mean_score(out, groups.get("left", []))
    out["right_score"] = _mean_score(out, groups.get("right", []))
    out["sentiment_score"] = _mean_score(out, groups.get("sentiment", []))
    out["risk_score"] = _mean_score(out, groups.get("risk", []))
    out["composite_score"] = (0.42 * out["left_score"] + 0.50 * out["right_score"] + 0.08 * out["sentiment_score"]).clip(0.0, 1.0)
    comp = out["composite_score"]
    position = pd.Series(0.50, index=out.index, dtype=float)
    position = position.mask(comp >= 0.60, 1.00)
    position = position.mask((comp >= 0.48) & (comp < 0.60), 0.75)
    position = position.mask((comp >= 0.35) & (comp < 0.48), 0.50)
    position = position.mask(comp < 0.35, 0.25)
    strong = (out["close"] > out["ma20"]) & (out["ma20"] > out["ma60"]) & (out["mom20"] > 0.0)
    repair = (out["drawdown60"] < -0.10) & (out["mom5"] > 0.0) & (out["rsrs_z"].diff(5) > 0.0)
    severe = (out["risk_votes"] >= 6) | ((out["mom20"] < -0.10) & (out["rsrs_z"] < -1.0))
    weak = (out["risk_votes"] >= 5) & (out["right_score"] < 0.45)
    position = position.mask(strong, np.maximum(position, 1.00))
    position = position.mask(repair, np.maximum(position, 0.75))
    position = position.mask(weak, np.minimum(position, 0.50))
    position = position.mask(severe, np.minimum(position, 0.25))
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(5, min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = "左侧右侧有效性优选"
    return out


def _risk_radar_path_candidate(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()
    close = out["close"]
    vol_bar = out["vol20"].rolling(252, min_periods=80).quantile(RISK_RADAR_CFG["vol_quantile"])
    danger = ((close < out["ma60"]).astype(int) + (close < out["ma120"]).astype(int) + (out["ma60"] < out["ma120"]).astype(int) + (out["mom20"] < RISK_RADAR_CFG["mom20_bad"]).astype(int) + (out["mom60"] < RISK_RADAR_CFG["mom60_bad"]).astype(int) + (out["rsrs_z"] < RISK_RADAR_CFG["rsrs_off"]).astype(int) + (out["vol20"] > vol_bar).astype(int))
    crash = ((out["mom20"] < RISK_RADAR_CFG["crash_mom20"]).astype(int) + (close < out["ma60"]).astype(int) + (out["rsrs_z"] < RISK_RADAR_CFG["rsrs_crash"]).astype(int) + (out["vol20"] > vol_bar).astype(int))
    reentry = ((close > out["ma20"]).astype(int) + (out["mom20"] > 0.0).astype(int) + (out["rsrs_z"] > RISK_RADAR_CFG["rsrs_on"]).astype(int) + (out["mom60"] > 0.0).astype(int))
    repair = (out["drawdown60"] < RISK_RADAR_CFG["repair_drawdown"]) & (reentry >= RISK_RADAR_CFG["repair_votes"])
    strong = (close > out["ma20"]) & (out["ma20"] > out["ma60"]) & (out["mom20"] > 0.0) & (out["rsrs_z"] > RISK_RADAR_CFG["rsrs_on"])
    position = pd.Series(1.0, index=out.index, dtype=float)
    position = position.mask(crash >= RISK_RADAR_CFG["crash_votes"], np.minimum(position, RISK_RADAR_CFG["crash_pos"]))
    position = position.mask(danger >= RISK_RADAR_CFG["danger_votes"], np.minimum(position, RISK_RADAR_CFG["danger_pos"]))
    position = position.mask(repair, np.maximum(position, RISK_RADAR_CFG["repair_pos"]))
    position = position.mask(strong, np.maximum(position, 1.0))
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_rsrs"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = (1.0 - danger / 7.0).clip(0.0, 1.0)
    out["composite_score"] = (0.18 * out["left_score"] + 0.34 * out["right_score"] + 0.12 * out["sentiment_score"] + 0.36 * out["risk_score"]).clip(0.0, 1.0)
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(int(RISK_RADAR_CFG["smooth"]), min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = "风险雷达路径优选"
    return out


def _high_capture_guard_candidate(features: pd.DataFrame, groups: dict[str, list[str]]) -> pd.DataFrame:
    out = features.copy()
    out["left_score"] = _mean_score(out, groups.get("left", []))
    out["right_score"] = _mean_score(out, groups.get("right", []))
    out["sentiment_score"] = _mean_score(out, groups.get("sentiment", []))
    out["risk_score"] = _mean_score(out, groups.get("risk", []))
    out["composite_score"] = (0.20 * out["left_score"] + 0.48 * out["right_score"] + 0.12 * out["sentiment_score"] + 0.20 * out["risk_score"]).clip(0.0, 1.0)
    position = pd.Series(1.0, index=out.index, dtype=float)
    bear = (out["close"] < out["ma120"]) & (out["ma60"] < out["ma120"]) & (out["right_score"] < 0.42)
    danger = (out["risk_votes"] >= 6) | ((out["mom20"] < -0.09) & (out["rsrs_z"] < -1.0))
    crash = (out["risk_votes"] >= 7) | ((out["mom20"] < -0.13) & (out["range_pct120"] > 0.85))
    position = position.mask(bear, 0.65)
    position = position.mask(danger, 0.35)
    position = position.mask(crash, 0.0)
    repair = (out["drawdown60"] < -0.10) & (out["mom10"] > 0.0) & (out["rsrs_z"] > -0.40)
    strong = (out["close"] > out["ma20"]) & (out["ma20"] > out["ma60"]) & (out["mom20"] > 0.0)
    position = position.mask(repair, np.maximum(position, 0.80))
    position = position.mask(strong, np.maximum(position, 1.0))
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(3, min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = "高捕捉保护"
    return out


def _dual_momentum_candidate(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()
    score = (
        (out["mom20"] > 0.0).astype(int)
        + (out["mom60"] > 0.0).astype(int)
        + (out["mom120"] > 0.0).astype(int)
        + (out["rsrs_z"] > -0.20).astype(int)
    )
    position = pd.Series(0.50, index=out.index, dtype=float)
    position = position.mask(score >= 3, 1.00)
    position = position.mask(score == 2, 0.75)
    position = position.mask(score == 1, 0.35)
    position = position.mask(score == 0, 0.00)
    repair = (out["drawdown60"] < -0.08) & (out["mom10"] > 0.0) & (out["rsrs_z"] > -0.40)
    position = position.mask(repair, np.maximum(position, 0.75))
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_rsrs"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (score / 4.0).clip(0.0, 1.0)
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(5, min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = "双动量路径优选"
    return out


def _rsrs_guard_candidate(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()
    close = out["close"]
    position = pd.Series(1.0, index=out.index, dtype=float)
    position = position.mask((out["rsrs_z"] < -0.70) & (out["mom20"] < 0.0), 0.25)
    position = position.mask((out["rsrs_z"] < -1.20) & (close < out["ma60"]), 0.00)
    position = position.mask((out["rsrs_z"] > 0.20) & (out["mom20"] > 0.0), 1.00)
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal", "f_pe_value", "f_pb_value", "f_dividend_value"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_rsrs"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (0.25 * out["left_score"] + 0.50 * out["right_score"] + 0.25 * out["risk_score"]).clip(0.0, 1.0)
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(5, min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = "RSRS风险门控"
    return out


def _dual_momentum_hedge_candidate(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()
    close = out["close"]
    score = ((out["mom20"] > 0.0).astype(int) + (out["mom60"] > 0.0).astype(int) + (out["mom120"] > 0.0).astype(int) + (out["rsrs_z"] > -0.20).astype(int))
    strong = (score >= 3) & (close > out["ma60"])
    weak = (score <= 1) & (close < out["ma60"])
    crash = (score == 0) & (close < out["ma120"]) & (out["rsrs_z"] < -0.80)
    repair = (out["drawdown60"] < -0.08) & (out["mom10"] > 0.0) & (out["rsrs_z"] > -0.40)
    position = pd.Series(0.80, index=out.index, dtype=float)
    position = position.mask(strong, 1.00)
    position = position.mask(score == 2, 0.75)
    position = position.mask(weak, 0.25)
    position = position.mask(crash, -0.10)
    position = position.mask(repair, 0.90)
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_rsrs"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (score / 4.0).clip(0.0, 1.0)
    out["raw_position"] = position.clip(-0.10, 1.00)
    out["position"] = out["raw_position"].rolling(1, min_periods=1).mean().clip(-0.10, 1.00)
    out["model_name"] = "双动量风险对冲"
    return out


def _rsrs_hedge_candidate(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()
    close = out["close"]
    position = pd.Series(1.0, index=out.index, dtype=float)
    position = position.mask((out["rsrs_z"] < -0.70) & (out["mom20"] < 0.0), 0.25)
    position = position.mask((out["rsrs_z"] < -1.20) & (close < out["ma60"]), -0.10)
    position = position.mask((out["rsrs_z"] > 0.20) & (out["mom20"] > 0.0), 1.00)
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal", "f_pe_value", "f_pb_value", "f_dividend_value"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_rsrs"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (0.25 * out["left_score"] + 0.50 * out["right_score"] + 0.25 * out["risk_score"]).clip(0.0, 1.0)
    out["raw_position"] = position.clip(-0.10, 1.00)
    out["position"] = out["raw_position"].rolling(1, min_periods=1).mean().clip(-0.10, 1.00)
    out["model_name"] = "RSRS风险对冲"
    return out


def _macd_trend_repair_candidate(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()
    close = out["close"].astype(float)
    out["ema12"] = close.ewm(span=12, adjust=False, min_periods=12).mean()
    out["ema26"] = close.ewm(span=26, adjust=False, min_periods=26).mean()
    out["ema50"] = close.ewm(span=50, adjust=False, min_periods=20).mean()
    out["ema200"] = close.ewm(span=200, adjust=False, min_periods=80).mean()
    out["macd"] = out["ema12"] - out["ema26"]
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    trend = (out["macd_hist"] > 0.0) & (close > out["ema50"])
    risk = (out["macd_hist"] < 0.0) & (close < out["ema50"]) & (out["mom20"] < 0.0)
    crash = (close < out["ema200"]) & (out["mom60"] < 0.0) & (out["rsrs_z"] < -0.60)
    repair = (out["rsi14"] < 35.0) & (out["mom5"] > 0.0)
    position = pd.Series(0.65, index=out.index, dtype=float)
    position = position.mask(trend, 1.00)
    position = position.mask(risk, 0.25)
    position = position.mask(crash, 0.00)
    position = position.mask(repair, 0.75)
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal", "f_pe_value", "f_pb_value", "f_dividend_value"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_ma_distance", "f_rsrs", "f_amount_trend"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (0.22 * out["left_score"] + 0.48 * out["right_score"] + 0.10 * out["sentiment_score"] + 0.20 * out["risk_score"]).clip(0.0, 1.0)
    out["raw_position"] = position.clip(0.0, 1.0)
    out["position"] = out["raw_position"].rolling(3, min_periods=1).mean().clip(0.0, 1.0)
    out["model_name"] = "MACD趋势修复"
    return out


def _blend_position_candidate(
    features: pd.DataFrame,
    primary: pd.DataFrame,
    stabilizer: pd.DataFrame,
    *,
    primary_weight: float,
    name: str,
) -> pd.DataFrame:
    out = features.copy()
    primary_position = pd.to_numeric(primary["position"], errors="coerce").fillna(0.65).astype(float)
    stabilizer_position = pd.to_numeric(stabilizer["position"], errors="coerce").fillna(0.65).astype(float)
    position = primary_weight * primary_position + (1.0 - primary_weight) * stabilizer_position
    out["position"] = position.rolling(3, min_periods=1).mean().clip(-0.10, 1.10)
    out["raw_position"] = position.clip(-0.10, 1.10)
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal", "f_pe_value", "f_pb_value", "f_dividend_value"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_ma_distance", "f_rsrs", "f_amount_trend"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (
        primary_weight * pd.to_numeric(primary.get("composite_score", 0.5), errors="coerce").fillna(0.5)
        + (1.0 - primary_weight) * pd.to_numeric(stabilizer.get("composite_score", 0.5), errors="coerce").fillna(0.5)
    ).clip(0.0, 1.0)
    out["model_name"] = name
    return out
def _benchmark_anchor_candidate(features: pd.DataFrame, exposure: float = 0.96) -> pd.DataFrame:
    out = features.copy()
    out["left_score"] = _mean_score(out, ["f_repair", "f_low_vol", "f_oversold_reversal", "f_pe_value", "f_pb_value", "f_dividend_value"])
    out["right_score"] = _mean_score(out, ["f_trend20", "f_trend60", "f_trend120", "f_ma_distance", "f_rsrs", "f_amount_trend"])
    out["sentiment_score"] = _mean_score(out, ["f_sentiment_v", "f_sentiment_flow_proxy"])
    out["risk_score"] = _mean_score(out, ["f_risk_guard", "f_crash_guard", "f_top_guard"])
    out["composite_score"] = (0.20 * out["left_score"] + 0.35 * out["right_score"] + 0.10 * out["sentiment_score"] + 0.35 * out["risk_score"]).clip(0.0, 1.0)
    out["raw_position"] = float(exposure)
    out["position"] = float(exposure)
    out["model_name"] = "基准锚定"
    out.attrs["max_position"] = max(1.0, float(exposure))
    out.attrs["min_position"] = 0.0
    return out


def _exposure_budget_candidate(
    features: pd.DataFrame,
    base: pd.DataFrame,
    *,
    name: str,
    max_position: float,
    min_position: float = 0.0,
    up_add: float = 0.0,
    down_scale: float = 1.0,
    smooth: int = 2,
) -> pd.DataFrame:
    out = features.copy()
    raw = pd.to_numeric(base["position"], errors="coerce").fillna(0.65).astype(float)
    position = raw.copy()
    position = position.mask(raw >= 0.95, float(max_position))
    position = position.mask(raw <= 0.05, float(min_position))
    position = position.mask((raw > 0.05) & (raw < 0.35), raw * float(down_scale))
    position = position.mask((raw >= 0.70) & (raw < 0.95), np.minimum(0.98, raw + float(up_add)))
    out["position"] = position.rolling(max(1, int(smooth)), min_periods=1).mean().clip(float(min_position), float(max_position))
    out["raw_position"] = position.clip(float(min_position), float(max_position))
    for column in ("left_score", "right_score", "sentiment_score", "risk_score", "composite_score"):
        out[column] = pd.to_numeric(base.get(column, 0.5), errors="coerce").fillna(0.5)
    out["model_name"] = name
    out.attrs["min_position"] = float(min_position)
    out.attrs["max_position"] = float(max_position)
    out.attrs["borrow_annual"] = 0.035
    return out


def _active_excess_protection_candidate(
    features: pd.DataFrame,
    base: pd.DataFrame,
    *,
    name: str = "主动超额保护",
    anchor_position: float = 0.98,
    ytd_loss_limit: float = 0.025,
    rolling_loss_limit: float = 0.020,
    min_hold_days: int = 20,
    release_buffer: float = -0.003,
    severe_level: int = 5,
    max_position: float = 1.15,
    min_position: float = -0.10,
    smooth: int = 1,
) -> pd.DataFrame:
    """Causal active-risk stop for timing underperformance.

    The base timing signal remains responsible for crash protection. This
    wrapper only suppresses active timing bets after realised YTD or rolling
    relative underperformance; the decision at date T is applied by _backtest
    at T+1, so it does not use future returns.
    """
    out = features.copy()
    raw = (
        pd.to_numeric(base["position"], errors="coerce")
        .fillna(0.65)
        .astype(float)
        .clip(float(min_position), float(max_position))
    )
    dates = pd.to_datetime(out["date"])
    ret = out["ret"].fillna(0.0).astype(float).to_numpy()
    close = out["close"].astype(float)
    severe = (
        ((out["risk_votes"] >= severe_level) & (close < out["ma60"]) & (out["mom20"] < 0.0))
        | ((out["rsrs_z"] < -1.05) & (out["mom20"] < -0.04))
        | ((out["drawdown60"] < -0.13) & (out["mom20"] < 0.0))
    ).fillna(False).to_numpy()
    strong = (
        (out["risk_votes"] <= 3)
        & (out["mom20"] > 0.0)
        & ((out["mom60"] > 0.0) | (close > out["ma60"]))
        & (out["rsrs_z"] > -0.35)
    ).fillna(False).to_numpy()

    adjusted = raw.to_numpy(dtype=float).copy()
    active_ytd = 1.0
    active_logs: list[float] = []
    riskoff_until = -1
    current_year = int(dates.iloc[0].year) if len(dates) else 0
    cash_daily = (1.0 + 0.018) ** (1.0 / 252.0) - 1.0
    borrow_daily = (1.0 + 0.035) ** (1.0 / 252.0) - 1.0

    for i in range(len(adjusted)):
        year = int(dates.iloc[i].year)
        if year != current_year:
            current_year = year
            active_ytd = 1.0
            active_logs = []
            riskoff_until = -1

        if i > 0:
            applied = float(adjusted[i - 1])
            turnover = abs(float(adjusted[i - 1]) - float(adjusted[i - 2])) * 0.0001 if i > 1 else 0.0
            funding = (1.0 - applied) * cash_daily if applied <= 1.0 else (1.0 - applied) * borrow_daily
            strategy_return = applied * ret[i] + funding - turnover
            benchmark_return = ret[i]
            relative_return = (1.0 + strategy_return) / max(1.0e-12, 1.0 + benchmark_return)
            active_ytd *= relative_return
            active_logs.append(math.log(max(1.0e-12, relative_return)))
            if len(active_logs) > 63:
                active_logs = active_logs[-63:]

        rolling_active = math.exp(sum(active_logs)) - 1.0 if active_logs else 0.0
        ytd_active = active_ytd - 1.0
        if (ytd_active <= -float(ytd_loss_limit)) or (rolling_active <= -float(rolling_loss_limit)):
            riskoff_until = max(riskoff_until, i + int(min_hold_days))
        elif i > riskoff_until and ytd_active > float(release_buffer) and rolling_active > -0.004 and strong[i]:
            riskoff_until = -1

        if i <= riskoff_until:
            adjusted[i] = min(float(raw.iloc[i]), 0.25) if severe[i] else float(anchor_position)
        else:
            adjusted[i] = float(raw.iloc[i])

    position = pd.Series(adjusted, index=out.index).clip(float(min_position), float(max_position))
    out["raw_position"] = position
    out["position"] = position.rolling(max(1, int(smooth)), min_periods=1).mean().clip(float(min_position), float(max_position))
    for column in ("left_score", "right_score", "sentiment_score", "risk_score", "composite_score"):
        out[column] = pd.to_numeric(base.get(column, 0.5), errors="coerce").fillna(0.5)
    out["model_name"] = name
    out.attrs["min_position"] = float(min_position)
    out.attrs["max_position"] = float(max_position)
    out.attrs["borrow_annual"] = 0.035
    return out


def _annual_strength_follow_candidate(
    features: pd.DataFrame,
    base: pd.DataFrame,
    *,
    name: str = "年度强势跟随",
    ytd_trigger: float = 0.08,
    follow_position: float = 1.15,
    mom20_floor: float = -0.02,
    mom60_floor: float = 0.0,
    rsrs_floor: float = -0.80,
    risk_votes_max: int = 5,
    max_position: float = 1.35,
    min_position: float = -0.10,
    smooth: int = 2,
) -> pd.DataFrame:
    """Follow confirmed calendar-year strength without changing base framework."""
    out = features.copy()
    raw = (
        pd.to_numeric(base["position"], errors="coerce")
        .fillna(0.65)
        .astype(float)
        .clip(float(min_position), float(max_position))
    )
    close = out["close"].astype(float)
    dates = pd.to_datetime(out["date"])
    ytd_values: list[float] = []
    current_year: int | None = None
    year_start = float(close.iloc[0]) if len(close) else 1.0
    for dt, price in zip(dates, close):
        if current_year != int(dt.year):
            current_year = int(dt.year)
            year_start = float(price)
        ytd_values.append(float(price) / max(1.0e-12, year_start) - 1.0)
    ytd_return = pd.Series(ytd_values, index=out.index)
    follow = (
        (ytd_return > float(ytd_trigger))
        & (out["mom20"] > float(mom20_floor))
        & (out["mom60"] > float(mom60_floor))
        & (out["rsrs_z"] > float(rsrs_floor))
        & (out["risk_votes"] <= int(risk_votes_max))
    ).fillna(False)
    crash = (
        ((out["risk_votes"] >= 6) & (close < out["ma60"]) & (out["mom20"] < 0.0))
        | ((out["rsrs_z"] < -1.20) & (out["mom20"] < -0.05))
    ).fillna(False)
    position = raw.mask(follow, np.maximum(raw, float(follow_position))).mask(crash, raw)
    out["raw_position"] = position.clip(float(min_position), float(max_position))
    out["position"] = out["raw_position"].rolling(max(1, int(smooth)), min_periods=1).mean().clip(float(min_position), float(max_position))
    for column in ("left_score", "right_score", "sentiment_score", "risk_score", "composite_score"):
        out[column] = pd.to_numeric(base.get(column, 0.5), errors="coerce").fillna(0.5)
    out["model_name"] = name
    out.attrs["min_position"] = float(min_position)
    out.attrs["max_position"] = float(max_position)
    out.attrs["borrow_annual"] = 0.035
    return out



def _selection_rank(metrics: dict[str, float], *, max_excess: float) -> float:
    mdd_improve = metrics["strategy_mdd"] - metrics["benchmark_mdd"]
    excess_gap = max(0.0, max_excess - metrics["excess_ann"])
    return (
        6.5 * metrics["excess_ann"]
        + 0.85 * metrics["strategy_sharpe"]
        + 0.55 * metrics.get("annual_excess_win_rate", 0.0)
        + 0.30 * metrics.get("monthly_excess_win_rate", 0.0)
        + 0.24 * mdd_improve
        + 0.18 * metrics.get("down_month_protection_rate", 0.0)
        + 0.10 * metrics.get("up_month_capture_rate", 0.0)
        - 12.0 * excess_gap
    )




def _monthly_consistency_hybrid_candidates(features: pd.DataFrame, groups: dict[str, list[str]]) -> list[pd.DataFrame]:
    macd = _macd_trend_repair_candidate(features)
    rsrs = _rsrs_hedge_candidate(features)
    dual = _dual_momentum_candidate(features)
    high_win = _build_position(features, groups, CONFIGS[2])
    low_drawdown = _build_position(features, groups, CONFIGS[3])
    left_right = _legacy_left_right_candidate(features, groups)
    candidates: list[pd.DataFrame] = []
    for primary_name, primary in (("MACD", macd), ("RSRS", rsrs), ("双动量", dual)):
        for stabilizer_name, stabilizer in (("高胜率", high_win), ("低回撤", low_drawdown), ("左侧右侧", left_right)):
            for primary_weight in (0.65, 0.75, 0.85):
                candidates.append(
                    _blend_position_candidate(
                        features,
                        primary,
                        stabilizer,
                        primary_weight=primary_weight,
                        name=f"月度胜率增强-{primary_name}-{stabilizer_name}-{primary_weight:.2f}",
                    )
                )
    return candidates


def _choose_model(
    raw: pd.DataFrame,
    basic: pd.DataFrame | None,
    market_context: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, float], str, dict[str, Any]]:
    features, groups = _prepare_features(raw, basic, market_context)
    diagnostics = _factor_test_summary(features, groups)
    candidates: list[tuple[float, pd.DataFrame, dict[str, float], str, str]] = []

    for profile in FUSION_PROFILES:
        signal = _four_dimension_fusion_candidate(features, groups, profile)
        bt = _backtest(signal)
        metrics = _metrics(bt)
        candidates.append((_selection_score(metrics), bt, metrics, profile.name, "four_dimension"))

    # Legacy candidates stay available only as a no-degradation guard while the
    # public framework and diagnostics are now the four-factor attack/defense model.
    for cfg in CONFIGS:
        signal = _build_position(features, groups, cfg)
        bt = _backtest(signal)
        metrics = _metrics(bt)
        candidates.append((_selection_score(metrics), bt, metrics, cfg.name, "legacy_guard"))
    macd_signal = _macd_trend_repair_candidate(features)
    rsrs_signal = _rsrs_hedge_candidate(features)
    dual_hedge_signal = _dual_momentum_hedge_candidate(features)
    macd_budget_smooth1 = _exposure_budget_candidate(
        features,
        macd_signal,
        name="MACD趋势修复增强",
        max_position=1.35,
        min_position=-0.15,
        down_scale=0.8,
        smooth=1,
    )
    rsrs_budget = _exposure_budget_candidate(
        features,
        rsrs_signal,
        name="RSRS风险对冲增强",
        max_position=1.03,
        min_position=-0.08,
    )
    rsrs_active_sharpe = _active_excess_protection_candidate(
        features,
        rsrs_budget,
        name="主动超额保护",
        anchor_position=1.15,
        ytd_loss_limit=0.035,
        rolling_loss_limit=0.030,
        min_hold_days=5,
        max_position=1.15,
        min_position=-0.10,
    )
    guided_signals = [
        _guided_five_bucket_candidate(features, groups, macd_signal, name="因子检验五档融合", base_weight=0.80, smooth=2),
        _guided_five_bucket_candidate(features, groups, macd_budget_smooth1, name="因子检验五档融合", base_weight=0.85, smooth=1),
        _guided_five_bucket_candidate(features, groups, rsrs_budget, name="因子检验五档融合", base_weight=0.82, smooth=1),
        _guided_five_bucket_candidate(features, groups, rsrs_active_sharpe, name="因子检验五档融合", base_weight=0.88, smooth=1),
    ]
    for signal in guided_signals:
        bt = _backtest(signal)
        metrics = _metrics(bt)
        candidates.append((_selection_score(metrics), bt, metrics, str(signal["model_name"].iloc[-1]), "four_dimension"))
    static_signals = [
        _legacy_left_right_candidate(features, groups),
        _risk_radar_path_candidate(features),
        _high_capture_guard_candidate(features, groups),
        _dual_momentum_candidate(features),
        _rsrs_guard_candidate(features),
        dual_hedge_signal,
        rsrs_signal,
        macd_signal,
        _benchmark_anchor_candidate(features, 0.96),
        _benchmark_anchor_candidate(features, 0.97),
        _benchmark_anchor_candidate(features, 0.98),
        _exposure_budget_candidate(features, macd_signal, name="MACD趋势修复增强", max_position=1.15, min_position=0.0),
        _exposure_budget_candidate(features, macd_signal, name="MACD趋势修复增强", max_position=1.15, min_position=-0.08),
        _exposure_budget_candidate(features, macd_signal, name="MACD趋势修复增强", max_position=1.15, min_position=-0.10),
        _exposure_budget_candidate(features, macd_signal, name="MACD趋势修复增强", max_position=1.35, min_position=-0.15, down_scale=0.8, smooth=2),
        macd_budget_smooth1,
        _exposure_budget_candidate(features, macd_signal, name="MACD趋势修复增强", max_position=1.35, min_position=-0.15, up_add=0.10, down_scale=0.6, smooth=1),
        rsrs_budget,
        _exposure_budget_candidate(features, dual_hedge_signal, name="双动量风险对冲增强", max_position=1.08, min_position=-0.08),
        _active_excess_protection_candidate(
            features,
            macd_budget_smooth1,
            name="主动超额保护",
            anchor_position=1.15,
            ytd_loss_limit=0.035,
            rolling_loss_limit=0.012,
            min_hold_days=40,
            max_position=1.35,
            min_position=-0.10,
        ),
        _active_excess_protection_candidate(
            features,
            rsrs_budget,
            name="主动超额保护",
            anchor_position=1.03,
            ytd_loss_limit=0.035,
            rolling_loss_limit=0.012,
            min_hold_days=20,
            max_position=1.15,
            min_position=-0.10,
        ),
        _active_excess_protection_candidate(
            features,
            rsrs_budget,
            name="主动超额保护",
            anchor_position=0.96,
            ytd_loss_limit=0.035,
            rolling_loss_limit=0.020,
            min_hold_days=40,
            max_position=1.15,
            min_position=-0.10,
        ),
        rsrs_active_sharpe,
        _annual_strength_follow_candidate(
            features,
            rsrs_active_sharpe,
            name="年度强势跟随",
            ytd_trigger=0.08,
            follow_position=1.15,
            mom20_floor=-0.02,
            mom60_floor=0.0,
            max_position=1.15,
            min_position=-0.10,
        ),
    ]
    static_signals.extend(_monthly_consistency_hybrid_candidates(features, groups))
    for signal in static_signals:
        bt = _backtest(signal)
        metrics = _metrics(bt)
        candidates.append((_selection_score(metrics), bt, metrics, str(signal["model_name"].iloc[-1]), "legacy_guard"))

    positive_excess = [item for item in candidates if item[2].get("excess_ann", -1.0) >= 0.0]
    selection_pool = positive_excess if positive_excess else candidates
    max_excess = max(item[2].get("excess_ann", -1.0) for item in selection_pool)
    excess_floor = max(0.0, 0.38 * max_excess)
    quality_pool = [
        item for item in selection_pool
        if item[2].get("excess_ann", -1.0) >= excess_floor
        and (
            item[2].get("monthly_excess_win_rate", 0.0) >= 0.48
            or item[2].get("annual_excess_win_rate", 0.0) >= 0.62
        )
    ] or selection_pool
    quality_pool.sort(
        key=lambda item: (
            item[0]
            + 0.35 * item[2].get("strategy_sharpe", 0.0)
            + 0.22 * item[2].get("monthly_excess_win_rate", 0.0)
            + 0.18 * item[2].get("annual_excess_win_rate", 0.0)
            + 0.10 * (item[2].get("strategy_mdd", 0.0) - item[2].get("benchmark_mdd", 0.0))
            - 1.6 * max(0.0, max_excess - item[2].get("excess_ann", -1.0))
        ),
        reverse=True,
    )
    selected_score, selected_bt, selected_metrics, selected_name, selected_family = quality_pool[0]

    framework_signal = _four_dimension_fusion_candidate(features, groups, FUSION_PROFILES[0])
    selected = selected_bt.copy()
    for column in [
        "macro_score",
        "price_volume_score",
        "sentiment_score",
        "valuation_score",
        "risk_score",
        "attack_score",
        "defense_score",
        "attack_level",
        "defense_level",
        "bucket_position",
    ]:
        if column not in selected.columns and column in framework_signal.columns:
            selected[column] = framework_signal[column]
    selected_metrics["selection_score"] = float(selected_score)
    selected_metrics["selection_source"] = 1.0 if selected_family == "four_dimension" else 0.0
    display_name = selected_name if selected_family == "four_dimension" else "因子检验五档融合"
    selected["display_model_name"] = display_name
    return selected, selected_metrics, display_name, diagnostics


def _pct(value: float) -> str:
    number = 0.0 if abs(float(value)) < 0.0005 else float(value)
    return f"{number * 100:.1f}%"


def _annual_rows(frame: pd.DataFrame, benchmark_name: str) -> list[list[str]]:
    rows: list[list[str]] = []
    usable = frame.dropna(subset=["strategy_return", "benchmark_return"]).copy()
    for year in sorted({item.year for item in usable["date"]}):
        scoped = usable[usable["date"].dt.year == year]
        if scoped.empty:
            continue
        label = f"{year}YTD" if year == pd.Timestamp.today().year else str(year)
        strategy_return = float(np.prod(1.0 + scoped["strategy_return"]) - 1.0)
        benchmark_return = float(np.prod(1.0 + scoped["benchmark_return"]) - 1.0)
        year_start = frame[frame["date"] < scoped["date"].iloc[0]].tail(1)
        nav_values = pd.concat(
            [year_start["strategy_nav"], frame[frame["date"].isin(scoped["date"])]["strategy_nav"]],
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
    strategy_ann = _annual_return(usable["strategy_return"])
    benchmark_ann = _annual_return(usable["benchmark_return"])
    rows.append(
        [
            "区间年化",
            _pct(strategy_ann),
            _pct(benchmark_ann),
            _pct((1.0 + strategy_ann) / (1.0 + benchmark_ann) - 1.0),
            _pct(_max_drawdown(frame["strategy_nav"])),
        ]
    )
    return rows


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


def _render_nav(frame: pd.DataFrame, index_name: str, model_name: str, output: Path) -> None:
    dpi = 180
    fig, axis = plt.subplots(figsize=(1778 / dpi, 1197 / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    axis.set_facecolor("white")
    axis.plot(frame["date"], frame["benchmark_nav"], color=ORANGE, linewidth=2.45, solid_capstyle="round", label=index_name)
    axis.plot(frame["date"], frame["strategy_nav"], color=GREY, linewidth=2.45, solid_capstyle="round", label="择时策略")
    right = axis.twinx()
    right.plot(frame["date"], frame["relative_strength"], color=RED, linewidth=2.75, solid_capstyle="round", label="相对强度（右轴）")
    left_values = np.r_[frame["benchmark_nav"].to_numpy(), frame["strategy_nav"].to_numpy()]
    left_low, left_high, left_ticks = _nice_limits(left_values, step=0.1 if np.ptp(left_values) <= 0.8 else 0.2)
    right_low, right_high, right_ticks = _nice_limits(frame["relative_strength"].to_numpy(), step=0.05 if np.ptp(frame["relative_strength"].to_numpy()) <= 0.35 else 0.1)
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
        tick.set_fontproperties(ARIAL_18)
    axis.grid(False)
    right.grid(False)
    for spine in ("top", "left", "right"):
        axis.spines[spine].set_visible(False)
        right.spines[spine].set_visible(False)
    axis.spines["bottom"].set_color(AXIS_GREY)
    axis.spines["bottom"].set_linewidth(0.75)
    right.spines["bottom"].set_visible(False)
    axis.set_title(f"{index_name}择时框架日频回测", loc="left", fontproperties=KAI_22, pad=10, color=BLACK)
    lines, labels = axis.get_legend_handles_labels()
    r_lines, r_labels = right.get_legend_handles_labels()
    legend = axis.legend(lines + r_lines, labels + r_labels, loc="lower center", bbox_to_anchor=(0.5, -0.18), ncol=3, frameon=False, prop=KAI_18, handlelength=1.6, handletextpad=0.45, columnspacing=1.0)
    for line in legend.get_lines():
        line.set_linewidth(3.0)
    fig.subplots_adjust(left=0.085, right=0.92, top=0.93, bottom=0.18)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, facecolor="white")
    plt.close(fig)


def _render_table(rows: list[list[str]], index_name: str, output: Path) -> None:
    headers = ["年度", "策略收益", index_name, "超额收益", "最大回撤"]
    dpi = 180
    fig, axis = plt.subplots(figsize=(1778 / dpi, 1035 / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    axis.axis("off")
    axis.set_title(f"{index_name}择时框架年度收益明细", loc="left", fontproperties=KAI_22, pad=18, color=BLACK)
    axis.plot([0.0, 1.0], [0.92, 0.92], color="#B8C2CF", linewidth=1.6, transform=axis.transAxes, clip_on=False)
    table = axis.table(
        cellText=[headers] + rows,
        cellLoc="center",
        colLoc="center",
        colWidths=[0.16, 0.21, 0.21, 0.21, 0.21],
        bbox=[0.02, 0.04, 0.96, 0.78],
    )
    table.auto_set_font_size(False)
    last_row = len(rows)
    for (row_index, _column_index), cell in table.get_celld().items():
        cell.set_edgecolor(BLACK)
        cell.set_linewidth(0.68)
        if row_index == 0:
            cell.set_facecolor("white")
        elif row_index == last_row:
            cell.set_facecolor(TABLE_BEIGE)
        elif row_index % 2 == 1:
            cell.set_facecolor(TABLE_BLUE)
        else:
            cell.set_facecolor("white")
        text = cell.get_text()
        text.set_color(BLACK)
        text.set_fontproperties(HEI_18 if row_index == 0 else KAI_18)
        if row_index == 0:
            text.set_weight("bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, facecolor="white")
    plt.close(fig)


def run(output_dir: Path, start: str, end: str) -> list[dict[str, Any]]:
    pro = ts.pro_api()
    market_context = _fetch_market_context(start, end)
    summaries = []
    for index_name, code in INDEXES:
        raw = _fetch_index_daily(pro, code, start, end)
        basic = _fetch_daily_basic(pro, code, start, end)
        bt, metrics, model_name, factor_diagnostics = _choose_model(raw, basic, market_context)
        nav_path = output_dir / f"{index_name}_择时日频回测图.png"
        table_path = output_dir / f"{index_name}_年度收益明细表.png"
        _render_nav(bt, index_name, model_name, nav_path)
        rows = _annual_rows(bt, index_name)
        _render_table(rows, index_name, table_path)
        summaries.append(
            {
                "index": index_name,
                "code": code,
                "model": model_name,
                "start": str(bt["trade_date"].iloc[0]),
                "end": str(bt["trade_date"].iloc[-1]),
                "nav_png": str(nav_path),
                "table_png": str(table_path),
                "annual_rows": [
                    {
                        "year": item[0],
                        "strategy_return": item[1],
                        "benchmark_return": item[2],
                        "excess_return": item[3],
                        "max_drawdown": item[4],
                    }
                    for item in rows
                ],
                "series": {
                    "dates": bt["date"].dt.strftime("%Y-%m-%d").tolist(),
                    "strategy_nav": bt["strategy_nav"].round(6).tolist(),
                    "benchmark_nav": bt["benchmark_nav"].round(6).tolist(),
                    "relative_strength": bt["relative_strength"].round(6).tolist(),
                    "position": bt["applied_position"].round(4).tolist(),
                    "raw_bucket_position": pd.to_numeric(bt.get("bucket_position", bt.get("raw_position")), errors="coerce").round(4).fillna(0.5).tolist(),
                    "attack_score": pd.to_numeric(bt.get("attack_score"), errors="coerce").round(6).fillna(0.5).tolist(),
                    "defense_score": pd.to_numeric(bt.get("defense_score"), errors="coerce").round(6).fillna(0.5).tolist(),
                    "macro_score": pd.to_numeric(bt.get("macro_score"), errors="coerce").round(6).fillna(0.5).tolist() if "macro_score" in bt else [],
                    "price_volume_score": pd.to_numeric(bt.get("price_volume_score"), errors="coerce").round(6).fillna(0.5).tolist() if "price_volume_score" in bt else [],
                    "sentiment_score": pd.to_numeric(bt.get("sentiment_score"), errors="coerce").round(6).fillna(0.5).tolist() if "sentiment_score" in bt else [],
                    "valuation_score": pd.to_numeric(bt.get("valuation_score"), errors="coerce").round(6).fillna(0.5).tolist() if "valuation_score" in bt else [],
                    "risk_score": pd.to_numeric(bt.get("risk_score"), errors="coerce").round(6).fillna(0.5).tolist() if "risk_score" in bt else [],
                },
                "latest_signal": {
                    "macro_score": round(float(pd.to_numeric(bt.get("macro_score"), errors="coerce").dropna().iloc[-1]), 6) if "macro_score" in bt else None,
                    "price_volume_score": round(float(pd.to_numeric(bt.get("price_volume_score"), errors="coerce").dropna().iloc[-1]), 6) if "price_volume_score" in bt else None,
                    "sentiment_score": round(float(pd.to_numeric(bt.get("sentiment_score"), errors="coerce").dropna().iloc[-1]), 6) if "sentiment_score" in bt else None,
                    "valuation_score": round(float(pd.to_numeric(bt.get("valuation_score"), errors="coerce").dropna().iloc[-1]), 6) if "valuation_score" in bt else None,
                    "risk_score": round(float(pd.to_numeric(bt.get("risk_score"), errors="coerce").dropna().iloc[-1]), 6) if "risk_score" in bt else None,
                    "attack_score": round(float(pd.to_numeric(bt.get("attack_score"), errors="coerce").dropna().iloc[-1]), 6) if "attack_score" in bt else None,
                    "defense_score": round(float(pd.to_numeric(bt.get("defense_score"), errors="coerce").dropna().iloc[-1]), 6) if "defense_score" in bt else None,
                    "attack_level": int(pd.to_numeric(bt.get("attack_level"), errors="coerce").dropna().iloc[-1]) if "attack_level" in bt else None,
                    "defense_level": int(pd.to_numeric(bt.get("defense_level"), errors="coerce").dropna().iloc[-1]) if "defense_level" in bt else None,
                    "bucket_position": round(float(pd.to_numeric(bt.get("bucket_position", bt.get("raw_position")), errors="coerce").dropna().iloc[-1]), 4),
                },
                "factor_diagnostics": factor_diagnostics,
                **metrics,
            }
        )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-output", type=Path, default=BOARD_SNAPSHOT)
    parser.add_argument("--start", default="20160101")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y%m%d"))
    args = parser.parse_args()
    summaries = run(args.output, args.start, args.end)
    if args.snapshot_output:
        payload = {
            "status": "ready",
            "engine_version": "broad-index-timing/3.0-four-factor-efficacy-five-bucket",
            "generated_at": pd.Timestamp.utcnow().isoformat(),
            "start": args.start,
            "end": args.end,
            "policy": "因子构造-数据处理-指标检验-仓位信号-回测跟踪；宏观/量价/情绪/估值四维有效性检验后融合为进攻和防守强度，再映射0/0.25/0.5/0.75/1五档仓位；旧候选仅作防降级保护",
            "framework_steps": ["因子构造", "数据处理", "指标检验", "仓位信号", "回测跟踪"],
            "factor_families": ["宏观因子", "量价因子", "情绪因子", "估值因子"],
            "indices": summaries,
        }
        args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.snapshot_output.with_suffix(args.snapshot_output.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(args.snapshot_output)
    for row in summaries:
        print(
            f"{row['index']}|{row['code']}|{row['model']}|{row['start']}~{row['end']}|"
            f"ann={row['strategy_ann']:.4%}|bench={row['benchmark_ann']:.4%}|"
            f"ex={row['excess_ann']:.4%}|sh={row['strategy_sharpe']:.3f}|"
            f"bsh={row['benchmark_sharpe']:.3f}|mdd={row['strategy_mdd']:.2%}|"
            f"bmdd={row['benchmark_mdd']:.2%}|pos={row['avg_position']:.2f}|"
            f"annual_win={row['annual_excess_win_rate']:.1%}|"
            f"month_win={row['monthly_excess_win_rate']:.1%}"
        )


if __name__ == "__main__":
    main()
