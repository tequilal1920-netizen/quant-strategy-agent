"""Broad-index timing research charts.

The script keeps the existing timing framework intact:

* left-side factors: valuation/reversal/repair;
* right-side factors: price-volume trend and RSRS confirmation;
* nonlinear sentiment and risk radar: high sentiment is not treated as a top
  unless fundamentals/trend/risk also deteriorate.

It is intentionally reporting-oriented: it fetches index daily data from
Tushare, builds causal T+1 positions, and writes the reference-style NAV chart
plus annual-return table for each configured broad index.
"""

from __future__ import annotations

import argparse
import json
import math
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
BOARD_SNAPSHOT = Path(__file__).resolve().parents[2] / "board" / "quant_strategy_agent" / "data" / "broad_index_timing_snapshot.json"

INDEXES = [
    ("中证红利", "000922.CSI"),
    ("中证500", "000905.SH"),
    ("沪深300", "000300.SH"),
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


def _prepare_features(raw: pd.DataFrame, basic: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    frame = raw.copy()
    if basic is not None and not basic.empty:
        frame = frame.merge(basic.drop(columns=["ts_code"], errors="ignore"), on="trade_date", how="left")
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    amount = frame["amount"].astype(float)
    ret = frame["ret"].astype(float)

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

    features: dict[str, list[str]] = {"left": [], "right": [], "sentiment": [], "risk": []}

    frame["f_trend20"] = _sigmoid(frame["mom20"].fillna(0.0), 0.035).values
    frame["f_trend60"] = _sigmoid(frame["mom60"].fillna(0.0), 0.075).values
    frame["f_trend120"] = _sigmoid(frame["mom120"].fillna(0.0), 0.13).values
    frame["f_ma_distance"] = _sigmoid((frame["ma60"] / frame["ma120"] - 1.0).fillna(0.0), 0.035).values
    frame["f_rsrs"] = _sigmoid(frame["rsrs_z"].fillna(0.0), 0.85).values
    frame["f_amount_trend"] = _clip01(0.55 * frame["amount_pct252"] + 0.45 * frame["up_share20"]).values
    features["right"] = [
        "f_trend20",
        "f_trend60",
        "f_trend120",
        "f_ma_distance",
        "f_rsrs",
        "f_amount_trend",
    ]

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
    features["left"] = ["f_repair", "f_low_vol", "f_oversold_reversal"]

    if "pe" in frame.columns:
        pe = pd.to_numeric(frame["pe"], errors="coerce")
        frame["f_pe_value"] = 1.0 - _rolling_percentile(pe, 756, 180).fillna(0.5)
        features["left"].append("f_pe_value")
    if "pb" in frame.columns:
        pb = pd.to_numeric(frame["pb"], errors="coerce")
        frame["f_pb_value"] = 1.0 - _rolling_percentile(pb, 756, 180).fillna(0.5)
        features["left"].append("f_pb_value")
    for dy_col in ("dv_ratio", "dv_ttm"):
        if dy_col in frame.columns:
            frame["f_dividend_value"] = _rolling_percentile(pd.to_numeric(frame[dy_col], errors="coerce"), 756, 180).fillna(0.5)
            features["left"].append("f_dividend_value")
            break

    low_sentiment_repair = ((frame["amount_pct252"] <= 0.15) & (frame["mom5"].fillna(0.0) > 0.0)).astype(float)
    strong_sentiment_follow = ((frame["amount_pct252"] >= 0.60) & (frame["mom20"].fillna(0.0) > -0.02)).astype(float)
    frame["f_sentiment_v"] = _clip01(0.50 * low_sentiment_repair + 0.50 * strong_sentiment_follow + 0.25 * frame["up_share20"]).values
    frame["f_sentiment_flow_proxy"] = _clip01(0.45 * frame["amount_pct252"] + 0.55 * _sigmoid(frame["mom20"].fillna(0.0), 0.045)).values
    features["sentiment"] = ["f_sentiment_v", "f_sentiment_flow_proxy"]

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
    frame["f_risk_guard"] = (1.0 - frame["risk_votes"] / risk_votes.shape[1]).clip(0.0, 1.0)
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
    features["risk"] = ["f_risk_guard", "f_crash_guard", "f_top_guard"]

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



def _selection_rank(metrics: dict[str, float], *, max_excess: float) -> float:
    mdd_improve = metrics["strategy_mdd"] - metrics["benchmark_mdd"]
    excess_gap = max(0.0, max_excess - metrics["excess_ann"])
    return (
        5.0 * metrics["excess_ann"]
        + 0.70 * metrics["strategy_sharpe"]
        + 0.55 * metrics.get("annual_excess_win_rate", 0.0)
        + 0.35 * metrics.get("monthly_excess_win_rate", 0.0)
        + 0.30 * mdd_improve
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


def _choose_model(raw: pd.DataFrame, basic: pd.DataFrame | None) -> tuple[pd.DataFrame, dict[str, float], str]:
    features, groups = _prepare_features(raw, basic)
    candidates: list[tuple[float, pd.DataFrame, dict[str, float], str]] = []
    for cfg in CONFIGS:
        signal = _build_position(features, groups, cfg)
        bt = _backtest(signal)
        metrics = _metrics(bt)
        candidates.append((_selection_score(metrics), bt, metrics, cfg.name))
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
    ]
    static_signals.extend(_monthly_consistency_hybrid_candidates(features, groups))
    for signal in static_signals:
        bt = _backtest(signal)
        metrics = _metrics(bt)
        candidates.append((_selection_score(metrics), bt, metrics, str(signal["model_name"].iloc[-1])))
    positive_excess = [item for item in candidates if item[2].get("excess_ann", -1.0) >= 0.0]
    selection_pool = positive_excess if positive_excess else candidates
    max_excess = max(item[2].get("excess_ann", -1.0) for item in selection_pool)
    tolerance = 0.003 if max_excess >= 0.010 else 0.001
    locked_pool = [
        item for item in selection_pool
        if item[2].get("excess_ann", -1.0) >= max_excess - tolerance
    ] or selection_pool
    locked_pool.sort(
        key=lambda item: _selection_rank(item[2], max_excess=max_excess),
        reverse=True,
    )
    return locked_pool[0][1], locked_pool[0][2], locked_pool[0][3]


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
    summaries = []
    for index_name, code in INDEXES:
        raw = _fetch_index_daily(pro, code, start, end)
        basic = _fetch_daily_basic(pro, code, start, end)
        bt, metrics, model_name = _choose_model(raw, basic)
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
                },
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
            "engine_version": "broad-index-timing/2.7-active-excess-consistency",
            "generated_at": pd.Timestamp.utcnow().isoformat(),
            "start": args.start,
            "end": args.end,
            "policy": "champion_locked_high_excess_first_signed_factor_efficacy_then_active_excess_protection_winrate_drawdown_tiebreak",
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



