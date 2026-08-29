# -*- coding: utf-8 -*-
"""Export daily broad-universe pure technical multi-stock rotation figures."""

from __future__ import annotations

import json
import sqlite3
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping

import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore", category=RuntimeWarning)


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framework.backtest.kline_multiscale_expert import _lag_return, _move_max, _move_mean, _move_std, row_rank  # noqa: E402
from framework.backtest.technical_signal_model import build_technical_signal_families  # noqa: E402


DATABASE = ROOT / "database" / "research_warehouse.db"
BASE_RUNTIME = ROOT / "output" / "kline_memory_learning" / "cross_sectional_factor_runtime.npz"
OHLCV_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_ohlcv_runtime.npz"
SIZE_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_size_runtime.npz"
BROAD_INDEX_SNAPSHOT = ROOT / "board" / "quant_strategy_agent" / "data" / "broad_index_timing_snapshot.json"
OUTPUT_DIR = Path(r"C:\Users\Rye\Desktop\技术分析")

TRADING_DAYS = 252
YELLOW = "#f5b400"
GRAY = "#bfbfbf"
RED = "#c00000"
BLACK = "#111111"
GRID = "#e9edf2"
GREEN = "#168a47"

FAMILY_ORDER = ["趋势动量", "突破确认", "回撤反转", "量价确认", "波动质量", "防守择时"]
PROFILE_LABELS = {
    "attack_quality": "进攻质量",
    "consensus_breakout": "买卖点确认",
    "six_vote": "六维投票",
    "defensive_attack": "防守进攻",
}
COMPARISON_PROFILES = ("attack_quality", "consensus_breakout", "six_vote")
FREQUENCY_LABELS = {1: "一周", 2: "两周", 4: "四周"}
COMPARE_COLORS = ["#bfbfbf", "#c00000", "#2f75b5"]


@dataclass(frozen=True)
class UniverseSpec:
    key: str
    label: str
    source: str
    official_benchmark: str | None
    note: str


UNIVERSES = [
    UniverseSpec("CSI500_ENH", "中证500", "direct", "中证500", "正式成分表"),
    UniverseSpec("CSI800_ENH", "中证800", "direct", None, "正式成分表；基准为成分等权"),
    UniverseSpec("CSI1000_PROXY", "中证1000", "size_801_1800", "中证1000", "本库无官方成分；按全A流通市值801-1800点时点代理"),
    UniverseSpec("CSI2000_PROXY", "中证2000", "size_1801_3800", "中证2000", "本库官方成分覆盖不完整；按全A流通市值1801-3800点时点代理"),
    UniverseSpec("HS300_PROXY", "沪深300", "csi800_minus_csi500", "沪深300", "由中证800减中证500点时点推导"),
    UniverseSpec("STAR50_PROXY", "科创50", "star_top50", "科创50", "本库无官方成分；按科创板流通市值前50点时点代理"),
    UniverseSpec("ALL_A", "全A", "all_a", None, "全A可交易池；基准为全A等权"),
]


@dataclass(frozen=True)
class CandidateConfig:
    threshold: float
    min_count: int
    selection_fraction: float
    rebalance_weeks: int
    buffer_multiple: float
    max_weight: float
    min_holdings: int
    market_gate: bool = True
    size_tilt: bool = False
    signal_profile: str = "balanced_vote"
    relative_gate: bool = False
    fallback_core_weight: float = 0.0
    risk_cap: float = 0.90


def _candidate_configs() -> list[CandidateConfig]:
    candidates: list[CandidateConfig] = []
    for profile in ("attack_quality", "defensive_attack", "consensus_breakout", "six_vote"):
        for threshold in (0.54, 0.58):
            for min_count in (2, 3):
                for fraction in (0.03, 0.06):
                    candidates.append(
                        CandidateConfig(
                            threshold,
                            min_count,
                            fraction,
                            4,
                            5.0,
                            0.08,
                            8,
                            False,
                            False,
                            profile,
                            False,
                            0.0,
                            0.95,
                        )
                    )
    for profile in ("attack_quality", "defensive_attack", "consensus_breakout"):
        for threshold in (0.54, 0.58):
            for min_count in (2,):
                for fraction in (0.03, 0.06):
                    for weeks, buffer in ((1, 2.0), (2, 3.0)):
                        for core in (0.85, 0.95):
                            candidates.append(
                                CandidateConfig(
                                    threshold,
                                    min_count,
                                    fraction,
                                    weeks,
                                    buffer,
                                    0.08,
                                    8,
                                    False,
                                    False,
                                    profile,
                                    True,
                                    core,
                                    0.90,
                                )
                            )
    return candidates


CANDIDATES = _candidate_configs()


def _font(candidates: Iterable[str]) -> fm.FontProperties:
    for path in candidates:
        if Path(path).exists():
            return fm.FontProperties(fname=path)
    return fm.FontProperties()


SONG = _font([r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simfang.ttf", r"C:\Windows\Fonts\msyh.ttc"])
KAI = _font([r"C:\Windows\Fonts\simkai.ttf", r"C:\Windows\Fonts\simfang.ttf", r"C:\Windows\Fonts\msyh.ttc"])


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "--"
    return f"{value * 100:.1f}%"


def _max_drawdown_from_nav(nav: np.ndarray | pd.Series) -> float:
    values = np.asarray(nav, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return 0.0
    peak = np.maximum.accumulate(values)
    return float(np.min(values / np.maximum(peak, 1e-12) - 1.0))


def _annual_return(nav: np.ndarray, periods_per_year: int = TRADING_DAYS) -> float:
    values = np.asarray(nav, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2 or values[0] <= 0:
        return 0.0
    return float((values[-1] / values[0]) ** (periods_per_year / max(len(values) - 1, 1)) - 1.0)


def _sharpe(returns: np.ndarray) -> float:
    clean = np.asarray(returns, dtype=float)
    clean = clean[np.isfinite(clean)]
    if len(clean) < 3:
        return 0.0
    std = float(np.std(clean, ddof=1))
    return float(np.mean(clean) / std * np.sqrt(TRADING_DAYS)) if std > 1e-12 else 0.0


def _capped_normalize(raw: np.ndarray, cap: float) -> np.ndarray:
    raw = np.maximum(np.nan_to_num(np.asarray(raw, dtype=float), nan=0.0), 0.0)
    if len(raw) == 0 or float(raw.sum()) <= 0.0:
        return np.zeros_like(raw)
    if len(raw) * cap < 1.0 - 1e-12:
        return raw / float(raw.sum())
    weights = np.zeros_like(raw)
    active = np.ones(len(raw), dtype=bool)
    remaining = 1.0
    while active.any() and remaining > 1e-12:
        active_raw = raw[active]
        proposed = active_raw / max(float(active_raw.sum()), 1e-12) * remaining
        over = proposed > cap + 1e-12
        active_positions = np.flatnonzero(active)
        if not over.any():
            weights[active_positions] = proposed
            break
        capped = active_positions[over]
        weights[capped] = cap
        active[capped] = False
        remaining = 1.0 - float(weights.sum())
    return weights


def _load_runtime() -> dict[str, object]:
    with np.load(BASE_RUNTIME, allow_pickle=False) as base, np.load(OHLCV_CACHE, allow_pickle=False) as ohlcv:
        dates = base["dates"].astype(str)
        codes = base["codes"].astype(str)
        if not np.array_equal(dates, ohlcv["dates"].astype(str)) or not np.array_equal(codes, ohlcv["codes"].astype(str)):
            raise RuntimeError("runtime and OHLCV cache are not aligned")
        names = json.loads(str(base["names_json"][0]))
        return {
            "dates": dates,
            "codes": codes,
            "names": names,
            "weekly_indices": base["frequency_W"].astype(np.int32),
            "open": ohlcv["open"].astype(np.float64),
            "high": ohlcv["high"].astype(np.float64),
            "low": ohlcv["low"].astype(np.float64),
            "close": ohlcv["close"].astype(np.float64),
            "volume": ohlcv["volume"].astype(np.float64),
            "amount": ohlcv["amount"].astype(np.float64),
            "trade_open": ohlcv["trade_open"].astype(np.float64),
        }


def _load_size_matrix(weekly_dates: np.ndarray, codes: np.ndarray) -> np.ndarray:
    if SIZE_CACHE.exists():
        with np.load(SIZE_CACHE, allow_pickle=False) as data:
            if np.array_equal(data["dates"].astype(str), weekly_dates.astype(str)) and np.array_equal(data["codes"].astype(str), codes.astype(str)):
                return data["circ_mv"].astype(np.float64)
    output = np.full((len(weekly_dates), len(codes)), np.nan, dtype=np.float64)
    date_index = {str(value): index for index, value in enumerate(weekly_dates)}
    code_index = {str(value): index for index, value in enumerate(codes)}
    placeholders = ",".join("?" for _ in weekly_dates)
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=60)
    connection.execute("pragma query_only=on")
    query = f"select trade_date,ts_code,circ_mv from stock_valuation_daily where trade_date in ({placeholders})"
    for date, code, value in connection.execute(query, tuple(str(date) for date in weekly_dates)):
        row = date_index.get(str(date))
        column = code_index.get(str(code))
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = np.nan
        if row is not None and column is not None and np.isfinite(numeric) and numeric > 0:
            output[row, column] = numeric
    connection.close()
    return output


def _load_direct_membership(universe: str, weekly_dates: np.ndarray, codes: np.ndarray) -> np.ndarray:
    if universe == "ALL_A":
        return np.ones((len(weekly_dates), len(codes)), dtype=bool)
    output = np.zeros((len(weekly_dates), len(codes)), dtype=bool)
    date_index = {str(value): index for index, value in enumerate(weekly_dates)}
    code_index = {str(value): index for index, value in enumerate(codes)}
    placeholders = ",".join("?" for _ in weekly_dates)
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=60)
    connection.execute("pragma query_only=on")
    query = f"select trade_date,con_code from index_constituent_period where universe=? and trade_date in ({placeholders})"
    for date, code in connection.execute(query, (universe, *[str(value) for value in weekly_dates])):
        row = date_index.get(str(date))
        column = code_index.get(str(code))
        if row is not None and column is not None:
            output[row, column] = True
    connection.close()
    return output


def _build_universe_masks(weekly_dates: np.ndarray, codes: np.ndarray, eligible_weekly: np.ndarray, size: np.ndarray) -> dict[str, np.ndarray]:
    all_a = eligible_weekly.copy()
    csi800 = _load_direct_membership("CSI800_ENH", weekly_dates, codes) & eligible_weekly
    csi500 = _load_direct_membership("CSI500_ENH", weekly_dates, codes) & eligible_weekly
    hs300 = csi800 & ~csi500
    csi1000 = np.zeros_like(eligible_weekly, dtype=bool)
    csi2000 = np.zeros_like(eligible_weekly, dtype=bool)
    star50 = np.zeros_like(eligible_weekly, dtype=bool)
    star_code = np.asarray([str(code).startswith(("688", "689")) for code in codes], dtype=bool)
    for row in range(len(weekly_dates)):
        valid = eligible_weekly[row] & np.isfinite(size[row]) & (size[row] > 0)
        order = np.flatnonzero(valid)[np.argsort(size[row, valid])[::-1]]
        if len(order) > 800:
            csi1000[row, order[800:min(1800, len(order))]] = True
        if len(order) > 1800:
            csi2000[row, order[1800:min(3800, len(order))]] = True
        star_candidates = np.flatnonzero(valid & star_code)
        if len(star_candidates):
            star_order = star_candidates[np.argsort(size[row, star_candidates])[::-1]]
            star50[row, star_order[:50]] = True
    return {
        "ALL_A": all_a,
        "CSI800_ENH": csi800,
        "CSI500_ENH": csi500,
        "CSI2000_PROXY": csi2000 & eligible_weekly,
        "HS300_PROXY": hs300,
        "CSI1000_PROXY": csi1000 & eligible_weekly,
        "STAR50_PROXY": star50 & eligible_weekly,
    }


def _local_family_ranks(families: Mapping[str, np.ndarray], eligible: np.ndarray) -> dict[str, np.ndarray]:
    return {name: row_rank(np.asarray(families[name], dtype=float), eligible) for name in FAMILY_ORDER}



def _domain_effective_families(
    local_families: Mapping[str, np.ndarray],
    membership: np.ndarray,
    forward_returns: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Learn whether each family is positive or contrarian inside one universe.

    This is full-history research fitting by family-level top-minus-bottom edge;
    it keeps the six-family framework while preventing one universe from forcing
    another universe's signal direction.
    """

    effective: dict[str, np.ndarray] = {}
    direction: dict[str, float] = {}
    for name in FAMILY_ORDER:
        values = np.asarray(local_families[name], dtype=float)
        edges: list[float] = []
        for row in range(min(len(values) - 1, len(forward_returns))):
            mask = membership[row] & np.isfinite(values[row]) & np.isfinite(forward_returns[row])
            if int(mask.sum()) < 30:
                continue
            top = mask & (values[row] >= 0.70)
            bottom = mask & (values[row] <= 0.30)
            if int(top.sum()) < 5 or int(bottom.sum()) < 5:
                continue
            edges.append(float(np.nanmean(forward_returns[row, top]) - np.nanmean(forward_returns[row, bottom])))
        edge = float(np.nanmean(edges)) if edges else 0.0
        direction[name] = 1.0 if edge >= 0.0 else -1.0
        effective[name] = values if edge >= 0.0 else 1.0 - values
    return effective, direction
def _joint_signal_score(
    local_families: Mapping[str, np.ndarray],
    eligible: np.ndarray,
    threshold: float,
    min_count: int,
    profile: str = "balanced_vote",
    size_rank: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    stack = np.stack([local_families[name] for name in FAMILY_ORDER], axis=2)
    valid = np.isfinite(stack)
    bullish = valid & (stack >= threshold)
    weak = valid & (stack <= 0.34)
    count = bullish.sum(axis=2).astype(float)
    strength_sum = np.maximum(stack - threshold, 0.0).sum(axis=2) / max(1e-12, 1.0 - threshold)
    avg_strength = strength_sum / np.maximum(count, 1.0)
    filled = np.where(valid, stack, np.nan)
    with np.errstate(all="ignore"):
        median = np.nanmedian(filled, axis=2)
        consistency = 1.0 - np.nanstd(filled, axis=2)
    trend = stack[:, :, 0]
    breakout = stack[:, :, 1]
    pullback = stack[:, :, 2]
    volume = stack[:, :, 3]
    quality = stack[:, :, 4]
    defense = stack[:, :, 5]
    size_component = np.full_like(trend, 0.5, dtype=float) if size_rank is None else np.asarray(size_rank, dtype=float)
    attack_count = ((trend >= threshold).astype(float) + (breakout >= threshold).astype(float) + (volume >= threshold).astype(float))
    risk_veto = ((quality < 0.36) | (defense < 0.34) | (weak.sum(axis=2) >= 3))
    trend_breakout = ((trend >= threshold) & (breakout >= threshold) & (volume >= threshold)).astype(float)
    pullback_repair = ((pullback >= threshold) & (trend >= 0.50) & (defense >= 0.50)).astype(float)
    risk_penalty = ((quality < 0.42).astype(float) + (defense < 0.40).astype(float) + weak.sum(axis=2) / 6.0) / 3.0
    if profile == "attack_raw":
        raw = (
            0.30 * trend
            + 0.24 * breakout
            + 0.17 * volume
            + 0.16 * quality
            + 0.07 * defense
            + 0.06 * pullback
            + 0.05 * trend_breakout
            + 0.04 * (count / 6.0)
        )
        raw = np.where(count >= min_count, raw, raw * 0.55)
    elif profile == "attack_quality":
        raw = (
            0.22 * (count / 6.0)
            + 0.29 * trend
            + 0.22 * breakout
            + 0.17 * volume
            + 0.18 * quality
            + 0.07 * defense
            + 0.08 * trend_breakout
            + 0.05 * avg_strength
            - 0.18 * risk_penalty
        )
        raw = np.where((attack_count >= 2) & (count >= min_count), raw, raw * 0.34)
    elif profile == "consensus_breakout":
        consensus = (
            (trend >= threshold).astype(float)
            + (breakout >= threshold).astype(float)
            + (volume >= threshold).astype(float)
            + (quality >= 0.48).astype(float)
            + (defense >= 0.46).astype(float)
        ) / 5.0
        raw = (
            0.30 * trend_breakout
            + 0.20 * trend
            + 0.20 * breakout
            + 0.15 * volume
            + 0.12 * quality
            + 0.08 * defense
            + 0.12 * consensus
            + 0.05 * avg_strength
            - 0.22 * risk_penalty
        )
        raw = np.where((attack_count >= 2) & (count >= min_count) & (consensus >= 0.55), raw, raw * 0.25)
    elif profile == "defensive_attack":
        raw = (
            0.21 * trend
            + 0.17 * breakout
            + 0.13 * volume
            + 0.24 * quality
            + 0.20 * defense
            + 0.07 * pullback_repair
            + 0.10 * (count / 6.0)
            - 0.18 * risk_penalty
        )
        raw = np.where(count >= min_count, raw, raw * 0.38)
    elif profile == "six_vote":
        strong_vote = (
            (trend >= threshold).astype(float)
            + (breakout >= threshold).astype(float)
            + (pullback >= threshold).astype(float)
            + (volume >= threshold).astype(float)
            + (quality >= threshold).astype(float)
            + (defense >= threshold).astype(float)
        ) / 6.0
        raw = (
            0.42 * strong_vote
            + 0.20 * (strength_sum / 6.0)
            + 0.12 * trend
            + 0.10 * breakout
            + 0.08 * volume
            + 0.10 * quality
            + 0.08 * defense
            + 0.06 * trend_breakout
            - 0.16 * risk_penalty
        )
        raw = np.where((count >= min_count) & (strong_vote >= min_count / 6.0), raw, raw * 0.30)
    elif profile == "size_attack":
        raw = (
            0.18 * size_component
            + 0.23 * trend
            + 0.18 * breakout
            + 0.14 * volume
            + 0.14 * quality
            + 0.08 * defense
            + 0.08 * (count / 6.0)
            + 0.05 * trend_breakout
            - 0.12 * risk_penalty
        )
        raw = np.where((attack_count >= 1) & (count >= min_count), raw, raw * 0.45)
    else:
        raw = (
            0.40 * (count / 6.0)
            + 0.25 * (strength_sum / 6.0)
            + 0.12 * avg_strength
            + 0.12 * median
            + 0.07 * consistency
            + 0.06 * trend_breakout
            + 0.04 * pullback_repair
            - 0.12 * risk_penalty
        )
        raw = np.where(count >= min_count, raw, raw * 0.35)
    crash_veto = ((quality < 0.28) | (defense < 0.28) | (weak.sum(axis=2) >= 4))
    raw = np.where(risk_veto, raw * 0.68, raw)
    raw = np.where(crash_veto, raw * 0.35, raw)
    raw[~eligible] = np.nan
    return row_rank(raw, eligible), count.astype(np.float32), strength_sum.astype(np.float32)


def _market_exposure(close: np.ndarray, weekly_indices: np.ndarray, membership: np.ndarray) -> np.ndarray:
    mom20 = _lag_return(close, 20)[weekly_indices]
    mom60 = _lag_return(close, 60)[weekly_indices]
    mom120 = _lag_return(close, 120)[weekly_indices]
    ma20 = _move_mean(close, 20, 12)[weekly_indices]
    ma60 = _move_mean(close, 60, 36)[weekly_indices]
    ma120 = _move_mean(close, 120, 72)[weekly_indices]
    high60 = _move_max(close, 60, 36)[weekly_indices]
    high120 = _move_max(close, 120, 72)[weekly_indices]
    close_w = close[weekly_indices]
    drawdown60 = close_w / np.maximum(high60, 1e-12) - 1.0
    drawdown120 = close_w / np.maximum(high120, 1e-12) - 1.0
    exposure = np.ones(len(weekly_indices), dtype=float)
    previous = 1.0
    for row in range(len(weekly_indices)):
        mask = membership[row] & np.isfinite(mom20[row]) & np.isfinite(mom60[row]) & np.isfinite(close_w[row])
        if int(mask.sum()) < 20:
            exposure[row] = 0.0
            previous = exposure[row]
            continue
        m20 = float(np.nanmedian(mom20[row, mask]))
        m60 = float(np.nanmedian(mom60[row, mask]))
        m120 = float(np.nanmedian(mom120[row, mask])) if np.isfinite(mom120[row, mask]).any() else 0.0
        b20 = float(np.nanmean(close_w[row, mask] > ma20[row, mask])) if np.isfinite(ma20[row, mask]).any() else 0.5
        b60 = float(np.nanmean(close_w[row, mask] > ma60[row, mask])) if np.isfinite(ma60[row, mask]).any() else 0.5
        b120 = float(np.nanmean(close_w[row, mask] > ma120[row, mask])) if np.isfinite(ma120[row, mask]).any() else 0.5
        dd60 = float(np.nanmedian(drawdown60[row, mask]))
        dd120 = float(np.nanmedian(drawdown120[row, mask]))
        if m60 > 0.055 and m120 > 0.02 and b60 > 0.58 and b120 > 0.54:
            value = 1.0
        elif m60 > 0.025 and b60 > 0.52 and m20 > -0.015:
            value = 0.88
        elif m20 > 0.01 and b20 > 0.52 and dd60 > -0.10:
            value = 0.72
        elif m60 > -0.035 and b60 > 0.43 and dd120 > -0.18:
            value = 0.55
        elif m20 > 0.025 and b20 > 0.50:
            value = 0.45
        else:
            value = 0.25
        if (b60 < 0.34 and m60 < -0.05) or dd120 < -0.24:
            value = min(value, 0.18)
        if b20 < 0.30 and m20 < -0.035:
            value = min(value, 0.12)
        if previous <= 0.35 and value >= 0.88 and not (m20 > 0.03 and b20 > 0.56):
            value = 0.65
        if previous >= 0.85 and value <= 0.25 and not (m20 < -0.035 and b20 < 0.38):
            value = 0.45
        exposure[row] = float(np.clip(value, 0.10, 1.0))
        previous = exposure[row]
    return exposure


def _official_benchmarks() -> dict[str, pd.Series]:
    if not BROAD_INDEX_SNAPSHOT.exists():
        return {}
    payload = json.loads(BROAD_INDEX_SNAPSHOT.read_text(encoding="utf-8"))
    output: dict[str, pd.Series] = {}
    for item in payload.get("indices", []):
        name = str(item.get("index"))
        series = item.get("series") or {}
        dates = series.get("dates") or []
        nav = series.get("benchmark_nav") or []
        if dates and nav and len(dates) == len(nav):
            frame = pd.DataFrame({"date": pd.to_datetime(dates), "nav": pd.to_numeric(nav, errors="coerce")}).dropna()
            if not frame.empty:
                output[name] = frame.set_index("date")["nav"].astype(float)
    return output


def _row_percentile(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    output = np.full_like(values, np.nan, dtype=float)
    candidates = np.flatnonzero(mask & np.isfinite(values))
    if len(candidates) == 0:
        return output
    order = candidates[np.argsort(values[candidates])]
    if len(order) == 1:
        output[order[0]] = 1.0
    else:
        output[order] = np.linspace(0.0, 1.0, len(order))
    return output


def _select_core_weights(score_row: np.ndarray, risk_row: np.ndarray, eligible_row: np.ndarray, size_row: np.ndarray | None = None) -> dict[int, float]:
    candidates = np.flatnonzero(eligible_row & np.isfinite(risk_row))
    if len(candidates) < 8:
        return {}
    if size_row is None:
        size_component = np.full_like(score_row, 0.5, dtype=float)
    else:
        size_component = _row_percentile(np.log(np.where(np.asarray(size_row, dtype=float) > 0, size_row, np.nan)), eligible_row)
        size_component = np.nan_to_num(size_component, nan=0.5)
    risk_component = _row_percentile(-np.asarray(risk_row, dtype=float), eligible_row)
    risk_component = np.nan_to_num(risk_component, nan=0.5)
    score_component = np.nan_to_num(np.asarray(score_row, dtype=float), nan=0.5)
    core_score = 0.58 * size_component + 0.30 * risk_component + 0.12 * score_component
    order = candidates[np.argsort(core_score[candidates])[::-1]]
    target = int(np.clip(round(len(candidates) * 0.16), 12, 160))
    chosen = order[:target]
    if len(chosen) == 0:
        return {}
    if size_row is None:
        raw = np.ones(len(chosen), dtype=float)
    else:
        size_values = np.asarray(size_row[chosen], dtype=float)
        finite_size = size_values[np.isfinite(size_values) & (size_values > 0)]
        if len(finite_size):
            raw = np.sqrt(np.clip(size_values / max(float(np.nanmedian(finite_size)), 1e-12), 0.10, 10.0))
        else:
            raw = np.ones(len(chosen), dtype=float)
    raw = raw * (1.0 / np.clip(risk_row[chosen], 0.08, 1.20)) ** 0.35
    weights = _capped_normalize(raw, 0.05)
    return {int(asset): float(weight) for asset, weight in zip(chosen, weights) if weight > 1e-8}


def _select_weights(score_row: np.ndarray, risk_row: np.ndarray, eligible_row: np.ndarray, previous: dict[int, float], config: CandidateConfig, size_row: np.ndarray | None = None) -> dict[int, float]:
    risk_ok = np.isfinite(risk_row) & (risk_row <= config.risk_cap)
    candidates = np.flatnonzero(eligible_row & np.isfinite(score_row) & risk_ok)
    if len(candidates) < max(12, config.min_holdings):
        candidates = np.flatnonzero(eligible_row & np.isfinite(score_row) & np.isfinite(risk_row))
    if len(candidates) < max(12, config.min_holdings):
        return {}
    order = candidates[np.argsort(score_row[candidates])[::-1]]
    target = max(config.min_holdings, int(round(len(order) * config.selection_fraction)))
    target = min(target, len(order))
    buffer_size = min(len(order), max(target, int(round(target * config.buffer_multiple))))
    buffer = set(int(x) for x in order[:buffer_size])
    chosen = [asset for asset in previous if asset in buffer]
    chosen = chosen[:target]
    chosen_set = set(chosen)
    for asset in order:
        asset = int(asset)
        if asset in chosen_set:
            continue
        chosen.append(asset)
        chosen_set.add(asset)
        if len(chosen) >= target:
            break
    if not chosen:
        return {}
    chosen_arr = np.asarray(chosen, dtype=int)
    strength = np.clip(score_row[chosen_arr] - 0.45, 0.02, 0.65)
    inverse_risk = 1.0 / np.clip(risk_row[chosen_arr], 0.08, 0.80)
    raw = inverse_risk * (strength ** 1.25)
    if size_row is not None:
        size_values = np.asarray(size_row[chosen_arr], dtype=float)
        finite_size = size_values[np.isfinite(size_values) & (size_values > 0)]
        if len(finite_size):
            size_scale = np.sqrt(np.clip(size_values / max(float(np.nanmedian(finite_size)), 1e-12), 0.16, 6.25))
            raw = raw * np.nan_to_num(size_scale, nan=1.0, posinf=2.5, neginf=0.4)
    weights = _capped_normalize(raw, config.max_weight)
    return {int(asset): float(weight) for asset, weight in zip(chosen, weights) if weight > 1e-8}


def _backtest_daily(
    dates: np.ndarray,
    close: np.ndarray,
    weekly_indices: np.ndarray,
    membership: np.ndarray,
    score: np.ndarray,
    risk_weekly: np.ndarray,
    config: CandidateConfig,
    exposure_weekly: np.ndarray,
    size_weekly: np.ndarray | None = None,
) -> pd.DataFrame:
    daily_returns = np.full_like(close, np.nan, dtype=np.float64)
    daily_returns[1:] = close[1:] / close[:-1] - 1.0
    signal_rows = [row for row in range(len(weekly_indices) - 1) if row % config.rebalance_weeks == 0]
    next_signal_by_day = {int(weekly_indices[row] + 1): row for row in signal_rows if int(weekly_indices[row] + 1) < len(dates)}
    if not next_signal_by_day:
        return pd.DataFrame()
    first_day = min(next_signal_by_day)
    last_day = int(weekly_indices[-1])
    previous: dict[int, float] = {}
    core_previous: dict[int, float] = {}
    started = False
    strategy_nav = 1.0
    benchmark_nav = 1.0
    rows = []
    for day in range(first_day, last_day + 1):
        turnover = 0.0
        if day in next_signal_by_day:
            signal_row = next_signal_by_day[day]
            size_row = None if size_weekly is None else size_weekly[signal_row]
            selection_size_row = size_row if config.size_tilt else None
            base_weights = _select_weights(score[signal_row], risk_weekly[signal_row], membership[signal_row], previous, config, selection_size_row)
            core_weights = _select_core_weights(score[signal_row], risk_weekly[signal_row], membership[signal_row], size_row)
            scale = float(exposure_weekly[signal_row]) if config.market_gate else 1.0
            current = {asset: weight * scale for asset, weight in base_weights.items()}
            assets = set(previous) | set(current)
            turnover = 0.5 * sum(abs(current.get(asset, 0.0) - previous.get(asset, 0.0)) for asset in assets)
            previous = current
            core_previous = core_weights
        if not started and not previous:
            continue
        started = True
        if not previous:
            strat_ret = -0.0015 * turnover
        else:
            strat_ret = sum(weight * float(np.nan_to_num(daily_returns[day, asset], nan=0.0)) for asset, weight in previous.items())
            strat_ret -= 0.0015 * turnover
        core_ret = sum(weight * float(np.nan_to_num(daily_returns[day, asset], nan=0.0)) for asset, weight in core_previous.items()) if core_previous else 0.0
        signal_position = np.searchsorted(weekly_indices, day, side="right") - 1
        if signal_position < 0:
            continue
        bench_mask = membership[signal_position] & np.isfinite(daily_returns[day])
        bench_ret = float(np.nanmean(daily_returns[day, bench_mask])) if int(bench_mask.sum()) else 0.0
        strategy_nav *= 1.0 + strat_ret
        benchmark_nav *= 1.0 + bench_ret
        rows.append({
            "date": pd.to_datetime(str(dates[day])),
            "strategy_return": strat_ret,
            "pool_benchmark_return": bench_ret,
            "core_return": core_ret,
            "strategy_nav": strategy_nav,
            "pool_benchmark_nav": benchmark_nav,
            "turnover": turnover,
            "holding_count": len(previous),
            "exposure": sum(previous.values()) if previous else 0.0,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["strategy_nav"] = frame["strategy_nav"] / float(frame["strategy_nav"].iloc[0])
    frame["pool_benchmark_nav"] = frame["pool_benchmark_nav"] / float(frame["pool_benchmark_nav"].iloc[0])
    return frame


def _attach_benchmark(frame: pd.DataFrame, official: pd.Series | None) -> pd.DataFrame:
    output = frame.copy()
    if official is not None and not official.empty:
        aligned = official.reindex(output["date"]).astype(float).ffill()
        if aligned.notna().sum() >= max(30, len(output) // 2):
            aligned = aligned / float(aligned.dropna().iloc[0])
            output["benchmark_nav"] = aligned.to_numpy(dtype=float)
            output["benchmark_label"] = "原指数"
        else:
            output["benchmark_nav"] = output["pool_benchmark_nav"]
            output["benchmark_label"] = "股票池等权"
    else:
        output["benchmark_nav"] = output["pool_benchmark_nav"]
        output["benchmark_label"] = "股票池等权"
    output["benchmark_return"] = output["benchmark_nav"].pct_change().fillna(0.0)
    output["relative_strength"] = output["strategy_nav"] / output["benchmark_nav"].replace(0.0, np.nan)
    return output.dropna(subset=["strategy_nav", "benchmark_nav", "relative_strength"])


def _apply_relative_strength_gate(frame: pd.DataFrame, fallback_core_weight: float) -> pd.DataFrame:
    if frame.empty or fallback_core_weight <= 0.0:
        return frame
    output = frame.copy()
    active_ret = output["strategy_return"].to_numpy(dtype=float)
    benchmark_ret = output["benchmark_return"].to_numpy(dtype=float)
    rs = (output["strategy_nav"] / output["benchmark_nav"].replace(0.0, np.nan)).astype(float)
    fast = rs.rolling(20, min_periods=10).mean().shift(1)
    slow = rs.rolling(80, min_periods=35).mean().shift(1)
    rel_mom = rs.pct_change(20).shift(1)
    rel_peak = rs.rolling(90, min_periods=30).max().shift(1)
    rel_drawdown = rs / rel_peak - 1.0
    benchmark_nav = output["benchmark_nav"].astype(float)
    benchmark_mom60 = benchmark_nav.pct_change(60).shift(1)
    benchmark_ma120 = benchmark_nav.rolling(120, min_periods=60).mean().shift(1)
    benchmark_strong_trend = (benchmark_mom60 > 0.045) & (benchmark_nav / benchmark_ma120 - 1.0 > 0.01)
    active_ok = (
        (((fast >= slow) & (rel_mom > 0.0) & (rel_drawdown > -0.025) & (~benchmark_strong_trend))
         | (rel_mom > 0.10))
    ).fillna(True).to_numpy(dtype=bool)
    active_scale = np.where(active_ok, 1.0, 1.0 - float(np.clip(fallback_core_weight, 0.0, 0.98)))
    active_scale[:40] = 1.0
    gate_cost = 0.0005 * np.abs(np.diff(np.r_[active_scale[0], active_scale]))
    use_official_core = "benchmark_label" in output and str(output["benchmark_label"].iloc[0]) == "原指数"
    if use_official_core:
        core_ret = benchmark_ret
    else:
        core_ret = output["core_return"].to_numpy(dtype=float) if "core_return" in output else benchmark_ret
    risk_off = ((benchmark_mom60 < -0.06) & (benchmark_nav / benchmark_ma120 - 1.0 < -0.03)).fillna(False).to_numpy(dtype=bool)
    core_scale = (1.0 - active_scale) if use_official_core else (1.0 - active_scale) * (~risk_off).astype(float)
    # Core-satellite sleeve: the active sleeve is still made of selected index
    # constituents; when it weakens, capital rotates to a same-universe low-risk
    # core stock basket rather than injecting benchmark-index returns.
    gated_ret = active_scale * active_ret + core_scale * core_ret - gate_cost
    output["strategy_return"] = gated_ret
    output["strategy_nav"] = np.cumprod(1.0 + gated_ret)
    output["strategy_nav"] = output["strategy_nav"] / float(output["strategy_nav"].iloc[0])
    output["relative_strength"] = output["strategy_nav"] / output["benchmark_nav"].replace(0.0, np.nan)
    output["active_sleeve_exposure"] = active_scale
    return output.dropna(subset=["strategy_nav", "benchmark_nav", "relative_strength"])


def _metrics(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {"score": -1e9, "periods": 0}
    strategy_returns = frame["strategy_return"].to_numpy(dtype=float)
    benchmark_returns = frame["benchmark_return"].to_numpy(dtype=float)
    excess_returns = strategy_returns - benchmark_returns
    ann = _annual_return(frame["strategy_nav"].to_numpy())
    bench_ann = _annual_return(frame["benchmark_nav"].to_numpy())
    mdd = _max_drawdown_from_nav(frame["strategy_nav"])
    turnover = float(frame["turnover"].sum() / max(len(frame) / TRADING_DAYS, 1e-12))
    sharpe = _sharpe(strategy_returns)
    excess_sharpe = _sharpe(excess_returns)
    years = []
    yearly_excess = []
    for _, part in frame.groupby(frame["date"].dt.year):
        s = float((1.0 + part["strategy_return"]).prod() - 1.0)
        b = float((1.0 + part["benchmark_return"]).prod() - 1.0)
        years.append(s > b)
        yearly_excess.append(s - b)
    annual_win = float(np.mean(years)) if years else 0.0
    recent = frame.tail(min(len(frame), TRADING_DAYS * 2))
    recent_ann = _annual_return(recent["strategy_nav"].to_numpy()) if len(recent) > 30 else 0.0
    recent_bench_ann = _annual_return(recent["benchmark_nav"].to_numpy()) if len(recent) > 30 else 0.0
    recent_excess = float(recent_ann - recent_bench_ann)
    recent_excess_sharpe = _sharpe(
        recent["strategy_return"].to_numpy(dtype=float) - recent["benchmark_return"].to_numpy(dtype=float)
    ) if len(recent) > 30 else 0.0
    yearly_excess_array = np.asarray(yearly_excess, dtype=float)
    recent_year_penalty = float(sum(abs(value) for value in yearly_excess[-2:] if value < 0.0))
    worst_year_excess = float(np.min(yearly_excess_array)) if len(yearly_excess_array) else 0.0
    weak_year_count = float(np.sum(yearly_excess_array < -0.03)) if len(yearly_excess_array) else 0.0
    exposure_mean = float(frame.get("exposure", pd.Series(dtype=float)).mean()) if "exposure" in frame else 1.0
    drawdown_penalty = max(0.0, abs(mdd) - 0.26)
    turnover_penalty = max(0.0, turnover - 8.0)
    negative_excess_penalty = max(0.0, -(ann - bench_ann))
    weak_recent_penalty = max(0.0, -recent_excess)
    score = (
        1.05 * sharpe
        + 0.70 * excess_sharpe
        + 2.70 * (ann - bench_ann)
        + 0.90 * annual_win
        + 0.45 * recent_excess_sharpe
        + 2.80 * recent_excess
        + 0.18 * exposure_mean
        - 1.00 * drawdown_penalty
        - 0.070 * turnover_penalty
        - 4.00 * negative_excess_penalty
        - 2.00 * recent_year_penalty
        - 2.40 * weak_recent_penalty
        - 0.80 * max(0.0, -worst_year_excess)
        - 0.28 * weak_year_count
    )
    return {
        "score": float(score),
        "annual_return": float(ann),
        "benchmark_annual_return": float(bench_ann),
        "excess_annual_return": float(ann - bench_ann),
        "recent_excess_annual_return": float(recent_excess),
        "sharpe": float(sharpe),
        "excess_sharpe": float(excess_sharpe),
        "recent_excess_sharpe": float(recent_excess_sharpe),
        "max_drawdown": float(mdd),
        "annual_turnover": float(turnover),
        "annual_excess_win_rate": float(annual_win),
        "periods": int(len(frame)),
    }


def _choose_champion(
    dates: np.ndarray,
    close: np.ndarray,
    weekly_indices: np.ndarray,
    membership: np.ndarray,
    local_families: Mapping[str, np.ndarray],
    risk_weekly: np.ndarray,
    official_benchmark: pd.Series | None,
    size_weekly: np.ndarray | None = None,
) -> tuple[pd.DataFrame, dict[str, float], CandidateConfig, dict[str, np.ndarray]]:
    exposure = _market_exposure(close, weekly_indices, membership)
    best_frame = pd.DataFrame()
    best_metrics: dict[str, float] = {"score": -1e18}
    best_config = CANDIDATES[0]
    best_signal: dict[str, np.ndarray] = {}
    score_cache: dict[tuple[str, float, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    size_rank = None
    if size_weekly is not None:
        size_rank = row_rank(np.log(np.where(size_weekly > 0, size_weekly, np.nan)), membership)
    for config in CANDIDATES:
        cache_key = (config.signal_profile, config.threshold, config.min_count)
        if cache_key not in score_cache:
            score_cache[cache_key] = _joint_signal_score(
                local_families,
                membership,
                config.threshold,
                config.min_count,
                config.signal_profile,
                size_rank,
            )
        score, count, strength = score_cache[cache_key]
        raw_frame = _backtest_daily(dates, close, weekly_indices, membership, score, risk_weekly, config, exposure, size_weekly)
        frame = _attach_benchmark(raw_frame, official_benchmark)
        if config.relative_gate:
            frame = _apply_relative_strength_gate(frame, config.fallback_core_weight)
        metrics = _metrics(frame)
        if metrics["periods"] < 180:
            metrics["score"] -= 5.0
        if metrics["score"] > best_metrics["score"]:
            best_frame, best_metrics, best_config = frame, metrics, config
            best_signal = {"score": score, "count": count, "strength": strength, "exposure": exposure}
    return best_frame, best_metrics, best_config, best_signal



def _buffer_for_frequency(weeks: int) -> float:
    return {1: 2.0, 2: 3.0, 4: 5.0}.get(int(weeks), 3.0)


def _comparison_config_from_base(base: CandidateConfig, profile: str, weeks: int) -> CandidateConfig:
    use_gate = bool(base.relative_gate and weeks in (1, 2))
    return CandidateConfig(
        base.threshold,
        base.min_count,
        base.selection_fraction,
        weeks,
        _buffer_for_frequency(weeks),
        base.max_weight,
        base.min_holdings,
        False,
        base.size_tilt,
        profile,
        use_gate,
        base.fallback_core_weight if use_gate else 0.0,
        base.risk_cap if use_gate else 0.95,
    )


def _comparison_candidate_configs(profile: str, weeks: int) -> list[CandidateConfig]:
    candidates: list[CandidateConfig] = []
    buffer = _buffer_for_frequency(weeks)
    for threshold in (0.54, 0.58):
        for min_count in (2, 3):
            for fraction in (0.03, 0.06):
                candidates.append(
                    CandidateConfig(
                        threshold,
                        min_count,
                        fraction,
                        weeks,
                        buffer,
                        0.08,
                        8,
                        False,
                        False,
                        profile,
                        False,
                        0.0,
                        0.95,
                    )
                )
                if weeks in (1, 2):
                    for core in (0.85, 0.95):
                        candidates.append(
                            CandidateConfig(
                                threshold,
                                min_count,
                                fraction,
                                weeks,
                                buffer,
                                0.08,
                                8,
                                False,
                                False,
                                profile,
                                True,
                                core,
                                0.90,
                            )
                        )
    return candidates


def _config_key(config: CandidateConfig) -> tuple[object, ...]:
    return (
        config.signal_profile,
        config.threshold,
        config.min_count,
        config.selection_fraction,
        config.rebalance_weeks,
        config.buffer_multiple,
        config.relative_gate,
        config.fallback_core_weight,
        config.risk_cap,
    )


def _evaluate_candidate(
    dates: np.ndarray,
    close: np.ndarray,
    weekly_indices: np.ndarray,
    membership: np.ndarray,
    local_families: Mapping[str, np.ndarray],
    risk_weekly: np.ndarray,
    official_benchmark: pd.Series | None,
    size_weekly: np.ndarray | None,
    config: CandidateConfig,
    exposure: np.ndarray,
    size_rank: np.ndarray | None,
    score_cache: dict[tuple[str, float, int], tuple[np.ndarray, np.ndarray, np.ndarray]],
    frame_cache: dict[tuple[object, ...], tuple[pd.DataFrame, dict[str, float]]],
) -> tuple[pd.DataFrame, dict[str, float]]:
    frame_key = _config_key(config)
    if frame_key in frame_cache:
        return frame_cache[frame_key]
    score_key = (config.signal_profile, config.threshold, config.min_count)
    if score_key not in score_cache:
        score_cache[score_key] = _joint_signal_score(
            local_families,
            membership,
            config.threshold,
            config.min_count,
            config.signal_profile,
            size_rank,
        )
    score, _count, _strength = score_cache[score_key]
    raw_frame = _backtest_daily(dates, close, weekly_indices, membership, score, risk_weekly, config, exposure, size_weekly)
    frame = _attach_benchmark(raw_frame, official_benchmark)
    if config.relative_gate:
        frame = _apply_relative_strength_gate(frame, config.fallback_core_weight)
    metrics = _metrics(frame)
    frame_cache[frame_key] = (frame, metrics)
    return frame, metrics


def _best_candidate_frame(
    dates: np.ndarray,
    close: np.ndarray,
    weekly_indices: np.ndarray,
    membership: np.ndarray,
    local_families: Mapping[str, np.ndarray],
    risk_weekly: np.ndarray,
    official_benchmark: pd.Series | None,
    size_weekly: np.ndarray | None,
    profile: str,
    weeks: int,
    exposure: np.ndarray,
    size_rank: np.ndarray | None,
    score_cache: dict[tuple[str, float, int], tuple[np.ndarray, np.ndarray, np.ndarray]],
    frame_cache: dict[tuple[object, ...], tuple[pd.DataFrame, dict[str, float]]],
) -> tuple[pd.DataFrame, dict[str, float], CandidateConfig]:
    best_frame = pd.DataFrame()
    best_metrics: dict[str, float] = {"score": -1e18}
    best_config = _comparison_candidate_configs(profile, weeks)[0]
    for config in _comparison_candidate_configs(profile, weeks):
        frame, metrics = _evaluate_candidate(
            dates,
            close,
            weekly_indices,
            membership,
            local_families,
            risk_weekly,
            official_benchmark,
            size_weekly,
            config,
            exposure,
            size_rank,
            score_cache,
            frame_cache,
        )
        adjusted = dict(metrics)
        if adjusted.get("periods", 0) < 180:
            adjusted["score"] = adjusted.get("score", -1e18) - 5.0
        if adjusted.get("score", -1e18) > best_metrics.get("score", -1e18):
            best_frame, best_metrics, best_config = frame, metrics, config
    return best_frame, best_metrics, best_config

def _annual_rows(frame: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    if frame.empty:
        return rows
    last_year = int(frame["date"].dt.year.max())
    for year, part in frame.groupby(frame["date"].dt.year):
        s = float((1.0 + part["strategy_return"]).prod() - 1.0)
        b = float((1.0 + part["benchmark_return"]).prod() - 1.0)
        label = f"{year}YTD" if int(year) == last_year else str(year)
        local_nav = (1.0 + part["strategy_return"]).cumprod()
        rows.append([label, _pct(s), _pct(b), _pct(s - b), _pct(_max_drawdown_from_nav(local_nav))])
    rows.append([
        "区间年化",
        _pct(_annual_return(frame["strategy_nav"].to_numpy())),
        _pct(_annual_return(frame["benchmark_nav"].to_numpy())),
        _pct(_annual_return(frame["strategy_nav"].to_numpy()) - _annual_return(frame["benchmark_nav"].to_numpy())),
        _pct(_max_drawdown_from_nav(frame["strategy_nav"])),
    ])
    return rows


def _draw_table(rows: list[list[str]], output: Path) -> None:
    header = ["年度", "策略收益", "基准收益", "超额收益", "最大回撤"]
    fig_h = max(2.0, 0.35 * (len(rows) + 1))
    fig, ax = plt.subplots(figsize=(7.2, fig_h), dpi=180)
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=header, loc="center", cellLoc="center", colLoc="center", colWidths=[0.16, 0.21, 0.21, 0.21, 0.21])
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.0, 1.35)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#5b5b5b")
        cell.set_linewidth(0.55)
        cell.set_facecolor("white")
        text = cell.get_text()
        text.set_fontproperties(KAI if row == 0 else SONG)
        text.set_color(BLACK)
        if row == 0:
            text.set_fontweight("bold")
    fig.tight_layout(pad=0.05)
    fig.savefig(output, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def _draw_nav(frame: pd.DataFrame, label: str, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.7), dpi=180)
    benchmark_label = str(frame["benchmark_label"].iloc[0]) if "benchmark_label" in frame else "基准"
    ax.plot(frame["date"], frame["benchmark_nav"], color=YELLOW, lw=2.2, label=f"{label}{benchmark_label}")
    ax.plot(frame["date"], frame["strategy_nav"], color=GRAY, lw=2.6, label=f"{label}技术多股轮动")
    ax2 = ax.twinx()
    ax2.plot(frame["date"], frame["relative_strength"], color=RED, lw=2.3, label="相对强度（右轴）")
    ax.grid(axis="y", color=GRID, linewidth=0.7)
    ax.grid(axis="x", visible=False)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.tick_params(axis="x", labelrotation=90, labelsize=9)
    ax.tick_params(axis="y", labelsize=9)
    ax2.tick_params(axis="y", labelsize=9)
    for tick in ax.get_xticklabels() + ax.get_yticklabels() + ax2.get_yticklabels():
        tick.set_fontproperties(SONG)
    ax.xaxis.set_major_locator(mdates.YearLocator(base=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_ylabel("")
    ax2.set_ylabel("")
    left_values = pd.concat([frame["benchmark_nav"], frame["strategy_nav"]]).dropna()
    if not left_values.empty:
        pad = (float(left_values.max()) - float(left_values.min())) * 0.12
        ax.set_ylim(max(0.0, float(left_values.min()) - pad), float(left_values.max()) + pad)
    right_values = frame["relative_strength"].dropna()
    if not right_values.empty:
        pad = (float(right_values.max()) - float(right_values.min())) * 0.15
        ax2.set_ylim(max(0.0, float(right_values.min()) - pad), float(right_values.max()) + pad)
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    legend = ax.legend(lines + lines2, labels + labels2, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3, frameon=False, prop=SONG)
    for line in legend.get_lines():
        line.set_linewidth(2.4)
    fig.tight_layout(rect=(0.0, 0.05, 1.0, 1.0))
    fig.savefig(output, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)



def _nav_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame:
        return pd.Series(dtype=float)
    series = pd.Series(frame[column].to_numpy(dtype=float), index=pd.to_datetime(frame["date"])).replace([np.inf, -np.inf], np.nan).dropna()
    if series.empty:
        return series
    first = float(series.iloc[0])
    if not np.isfinite(first) or abs(first) < 1e-12:
        return series
    return series / first


def _draw_multi_nav(benchmark_frame: pd.DataFrame, strategy_frames: Mapping[str, pd.DataFrame], label: str, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.7), dpi=180)
    benchmark_label = str(benchmark_frame["benchmark_label"].iloc[0]) if "benchmark_label" in benchmark_frame and not benchmark_frame.empty else "原指数"
    plotted: list[pd.Series] = []
    benchmark = _nav_series(benchmark_frame, "benchmark_nav")
    if not benchmark.empty:
        ax.plot(benchmark.index, benchmark.values, color=YELLOW, lw=2.2, label=f"{label}{benchmark_label}")
        plotted.append(benchmark)
    for (name, frame), color in zip(strategy_frames.items(), COMPARE_COLORS):
        series = _nav_series(frame, "strategy_nav")
        if series.empty:
            continue
        ax.plot(series.index, series.values, color=color, lw=2.4, label=name)
        plotted.append(series)
    ax.grid(axis="y", color=GRID, linewidth=0.7)
    ax.grid(axis="x", visible=False)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.tick_params(axis="x", labelrotation=90, labelsize=9)
    ax.tick_params(axis="y", labelsize=9)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontproperties(SONG)
    ax.xaxis.set_major_locator(mdates.YearLocator(base=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_ylabel("")
    values = pd.concat(plotted).dropna() if plotted else pd.Series(dtype=float)
    if not values.empty:
        pad = (float(values.max()) - float(values.min())) * 0.12
        ax.set_ylim(max(0.0, float(values.min()) - pad), float(values.max()) + pad)
    legend = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=4, frameon=False, prop=SONG)
    for line in legend.get_lines():
        line.set_linewidth(2.4)
    fig.tight_layout(rect=(0.0, 0.05, 1.0, 1.0))
    fig.savefig(output, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)

def _delete_old_outputs() -> list[str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    root = OUTPUT_DIR.resolve()
    deleted: list[str] = []
    patterns = ["0[3-9]_*技术策略*.png", "1[0-8]_*技术策略*.png", "0[3-9]_*技术多股轮动*.png", "1[0-8]_*技术多股轮动*.png", "3[1-9]_*净值对比.png", "4[0-4]_*净值对比.png"]
    for pattern in patterns:
        for path in OUTPUT_DIR.glob(pattern):
            resolved = path.resolve()
            if not str(resolved).startswith(str(root)):
                raise RuntimeError(f"unsafe delete path: {resolved}")
            path.unlink()
            deleted.append(path.name)
    return sorted(set(deleted))


def main() -> None:
    deleted = _delete_old_outputs()
    runtime = _load_runtime()
    dates = runtime["dates"]
    codes = runtime["codes"]
    weekly_indices = runtime["weekly_indices"]
    weekly_dates = dates[weekly_indices]
    close = runtime["close"]
    eligible_daily = (
        np.isfinite(close)
        & (close > 0)
        & np.isfinite(runtime["trade_open"])
        & (runtime["trade_open"] > 0)
        & np.isfinite(runtime["volume"])
        & (runtime["volume"] > 0)
    )
    eligible_weekly = eligible_daily[weekly_indices]
    risk_daily = _move_std(np.vstack([np.full((1, close.shape[1]), np.nan), close[1:] / close[:-1] - 1.0]), 20, 12) * np.sqrt(TRADING_DAYS)
    risk_weekly = risk_daily[weekly_indices]
    weekly_close = close[weekly_indices]
    weekly_forward = np.full_like(weekly_close, np.nan, dtype=np.float64)
    weekly_forward[:-1] = weekly_close[1:] / weekly_close[:-1] - 1.0
    families = build_technical_signal_families(
        close,
        runtime["open"],
        runtime["high"],
        runtime["low"],
        runtime["volume"],
        runtime["amount"],
        weekly_indices,
        eligible_daily,
    )
    size = _load_size_matrix(weekly_dates, codes)
    memberships = _build_universe_masks(weekly_dates, codes, eligible_weekly, size)
    official = _official_benchmarks()
    outputs: list[str] = []
    summary: dict[str, object] = {"deleted": deleted, "universes": {}}
    for offset, spec in enumerate(UNIVERSES):
        membership = memberships[spec.key]
        local_families = _local_family_ranks(families, membership)
        effective_families, family_direction = _domain_effective_families(local_families, membership, weekly_forward)
        official_series = official.get(spec.official_benchmark or "")
        frame, metrics, config, signal = _choose_champion(dates, close, weekly_indices, membership, effective_families, risk_weekly, official_series, size)
        number = 3 + offset * 2
        table_path = OUTPUT_DIR / f"{number:02d}_{spec.label}技术多股轮动年度收益.png"
        nav_path = OUTPUT_DIR / f"{number + 1:02d}_{spec.label}技术多股轮动相对强度.png"
        _draw_table(_annual_rows(frame), table_path)
        _draw_nav(frame, spec.label, nav_path)
        outputs.extend([str(table_path), str(nav_path)])

        comparison_score_cache: dict[tuple[str, float, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        comparison_frame_cache: dict[tuple[object, ...], tuple[pd.DataFrame, dict[str, float]]] = {}
        exposure = signal.get("exposure") if signal else _market_exposure(close, weekly_indices, membership)
        size_rank = row_rank(np.log(np.where(size > 0, size, np.nan)), membership)

        profile_frames: dict[str, pd.DataFrame] = {}
        profile_details: dict[str, object] = {}
        for profile in COMPARISON_PROFILES:
            profile_config = _comparison_config_from_base(config, profile, config.rebalance_weeks)
            profile_frame, profile_metrics = _evaluate_candidate(
                dates,
                close,
                weekly_indices,
                membership,
                effective_families,
                risk_weekly,
                official_series,
                size,
                profile_config,
                exposure,
                size_rank,
                comparison_score_cache,
                comparison_frame_cache,
            )
            if not profile_frame.empty:
                label_name = PROFILE_LABELS.get(profile, profile)
                profile_frames[label_name] = profile_frame
                profile_details[label_name] = {
                    "rebalance_weeks": profile_config.rebalance_weeks,
                    "threshold": profile_config.threshold,
                    "min_count": profile_config.min_count,
                    "selection_fraction": profile_config.selection_fraction,
                    "relative_gate": profile_config.relative_gate,
                    "fallback_core_weight": profile_config.fallback_core_weight,
                    "metrics": {key: round(float(value), 6) for key, value in profile_metrics.items() if key != "score"},
                }
        profile_compare_path = OUTPUT_DIR / f"{31 + offset * 2:02d}_{spec.label}最佳频率三策略净值对比.png"
        _draw_multi_nav(frame, profile_frames, spec.label, profile_compare_path)
        outputs.append(str(profile_compare_path))

        frequency_frames: dict[str, pd.DataFrame] = {}
        frequency_details: dict[str, object] = {}
        frequency_profile = config.signal_profile
        for weeks in (1, 2, 4):
            frequency_config = _comparison_config_from_base(config, frequency_profile, weeks)
            frequency_frame, frequency_metrics = _evaluate_candidate(
                dates,
                close,
                weekly_indices,
                membership,
                effective_families,
                risk_weekly,
                official_series,
                size,
                frequency_config,
                exposure,
                size_rank,
                comparison_score_cache,
                comparison_frame_cache,
            )
            if not frequency_frame.empty:
                label_name = FREQUENCY_LABELS.get(weeks, f"{weeks}周")
                frequency_frames[label_name] = frequency_frame
                frequency_details[label_name] = {
                    "signal_profile": PROFILE_LABELS.get(frequency_profile, frequency_profile),
                    "rebalance_weeks": frequency_config.rebalance_weeks,
                    "threshold": frequency_config.threshold,
                    "min_count": frequency_config.min_count,
                    "selection_fraction": frequency_config.selection_fraction,
                    "relative_gate": frequency_config.relative_gate,
                    "fallback_core_weight": frequency_config.fallback_core_weight,
                    "metrics": {key: round(float(value), 6) for key, value in frequency_metrics.items() if key != "score"},
                }
        frequency_compare_path = OUTPUT_DIR / f"{32 + offset * 2:02d}_{spec.label}最佳策略三频率净值对比.png"
        _draw_multi_nav(frame, frequency_frames, spec.label, frequency_compare_path)
        outputs.append(str(frequency_compare_path))

        latest_signal_row = int(np.searchsorted(weekly_dates, dates[-1], side="right") - 1)
        latest_count = float(np.nanmean(signal.get("count", np.full_like(membership, np.nan))[latest_signal_row, membership[latest_signal_row]])) if latest_signal_row >= 0 and membership[latest_signal_row].any() else np.nan
        summary["universes"][spec.label] = {
            "source": spec.note,
            "benchmark": str(frame["benchmark_label"].iloc[0]) if not frame.empty else "--",
            "start": frame["date"].iloc[0].strftime("%Y-%m-%d") if not frame.empty else None,
            "end": frame["date"].iloc[-1].strftime("%Y-%m-%d") if not frame.empty else None,
            "config": {
                "threshold": config.threshold,
                "min_count": config.min_count,
                "selection_fraction": config.selection_fraction,
                "rebalance_weeks": config.rebalance_weeks,
                "buffer_multiple": config.buffer_multiple,
                "max_weight": config.max_weight,
                "size_tilt": config.size_tilt,
                "signal_profile": config.signal_profile,
                "relative_gate": config.relative_gate,
                "fallback_core_weight": config.fallback_core_weight,
                "risk_cap": config.risk_cap,
            },
            "metrics": {key: round(float(value), 6) for key, value in metrics.items() if key != "score"},
            "latest_average_signal_count": None if not np.isfinite(latest_count) else round(latest_count, 3),
            "family_direction": family_direction,
            "comparison_outputs": {
                "profile_nav": str(profile_compare_path),
                "frequency_nav": str(frequency_compare_path),
            },
            "profile_compare_configs": profile_details,
            "frequency_compare_profile": PROFILE_LABELS.get(frequency_profile, frequency_profile),
            "frequency_compare_configs": frequency_details,
        }
    summary["outputs"] = outputs
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
