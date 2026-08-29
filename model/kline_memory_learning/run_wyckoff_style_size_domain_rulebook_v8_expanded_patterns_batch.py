"""V8 expanded-pattern Wyckoff domain memory model.

This version fixes the main downgrade in the prior experiments: the Analyzer
no longer emits only a small set of Wyckoff parent structures.  It keeps the
same domain-shared memory/evolution frame, but augments Wyckoff events with a
large technical-pattern event library generated from daily OHLCV:

- moving-average structure and crosses;
- price breakouts/breakdowns;
- pullback/support and reclaim patterns;
- volume-price confirmation/divergence;
- volatility contraction/expansion;
- candle body/shadow/gap patterns;
- momentum acceleration/exhaustion and range position.

The rulebook is still learned at the style x size domain level.  The five sample
stocks do not receive stock-specific path fitting.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning import run_wyckoff_industry_style_memory_batch as base  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_batch as v1  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v5_trend_executor_batch as v5  # noqa: E402
from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v7_hybrid_executor_batch as v7  # noqa: E402
from model.kline_memory_learning.run_wyckoff_domain_evolver_optimization import (  # noqa: E402
    EVENT_PATH,
    attach_memory,
    build_memory,
    prepare_events,
    weighted_memory_score,
)
from model.kline_memory_learning.run_wyckoff_memory_batch import (  # noqa: E402
    DEFAULT_COST_RATE,
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    THEORY,
    _load_stock,
    _safe_name,
)
from model.kline_memory_learning.run_wyckoff_style_size_memory_batch import (  # noqa: E402
    DOMAIN_COL,
    _plot_style_size_chart,
)


OUTPUT_SUBDIR = "风格市值12域统一规则库Wyckoff_V8扩展图形规则"
DEFAULT_CODES = ["300308.SZ", "688256.SH", "300750.SZ", "601138.SH", "000725.SZ"]
STYLE_SIZE_DOMAINS = v7.STYLE_SIZE_DOMAINS
HORIZON = 20
MIN_START = "20120101"

DOMAIN_COLUMNS = [
    "domain_global",
    "domain_industry",
    "domain_size3",
    "domain_style4",
    "domain_style_size12",
    "domain_industry_size",
    "domain_industry_style",
    "domain_board",
    "domain_liquidity3",
    "domain_behavior_ds",
]

CATALOG: Dict[str, Dict[str, Any]] = {}


def _register(rule_id: str, name: str, direction: int, family: str, description: str) -> None:
    CATALOG[rule_id] = {
        "rule_id": rule_id,
        "name_cn": name,
        "direction": int(direction),
        "family": family,
        "description": description,
    }


def _init_catalog() -> None:
    if CATALOG:
        return
    windows = [5, 10, 20, 40, 60, 120, 250]
    pairs = [(5, 20), (10, 20), (20, 60), (20, 120), (60, 120), (120, 250)]
    breakout_windows = [10, 20, 40, 60, 120, 250]
    volume_tags = [("DRY", "缩量"), ("NORMAL", "常量"), ("VOLUME", "放量")]
    for w in windows:
        _register(f"TECH_PRICE_RECLAIM_MA{w}_BULL", f"价格重新站上MA{w}", 1, "均线位置", f"收盘价上穿MA{w}")
        _register(f"TECH_PRICE_LOSE_MA{w}_BEAR", f"价格跌破MA{w}", -1, "均线位置", f"收盘价下穿MA{w}")
        _register(f"TECH_MA{w}_SUPPORT_BULL", f"MA{w}回踩支撑", 1, "回踩支撑", f"上升趋势中回踩MA{w}后收回")
        _register(f"TECH_MA{w}_RESIST_BEAR", f"MA{w}反抽受阻", -1, "反抽压力", f"下行趋势中反抽MA{w}失败")
    for a, b in pairs:
        _register(f"TECH_GOLDEN_CROSS_MA{a}_{b}_BULL", f"MA{a}上穿MA{b}", 1, "均线交叉", f"短均线上穿长均线MA{b}")
        _register(f"TECH_DEATH_CROSS_MA{a}_{b}_BEAR", f"MA{a}下穿MA{b}", -1, "均线交叉", f"短均线下穿长均线MA{b}")
    for a, b, c in [(5, 20, 60), (10, 20, 60), (20, 60, 120), (60, 120, 250)]:
        _register(f"TECH_MA_STACK_{a}_{b}_{c}_BULL", f"MA{a}/{b}/{c}多头排列", 1, "均线结构", "均线多头排列且价格在短均线上方")
        _register(f"TECH_MA_STACK_{a}_{b}_{c}_BEAR", f"MA{a}/{b}/{c}空头排列", -1, "均线结构", "均线空头排列且价格在短均线下方")
    for w in breakout_windows:
        for tag, tag_cn in volume_tags:
            _register(f"TECH_BREAKOUT_HIGH{w}_{tag}_BULL", f"{tag_cn}突破{w}日新高", 1, "突破", f"收盘价突破{w}日高点")
            _register(f"TECH_BREAKDOWN_LOW{w}_{tag}_BEAR", f"{tag_cn}跌破{w}日新低", -1, "跌破", f"收盘价跌破{w}日低点")
            _register(f"TECH_FALSE_BREAKOUT_HIGH{w}_{tag}_BEAR", f"{tag_cn}{w}日假突破回落", -1, "假突破", f"盘中新高但收盘回落至区间内")
            _register(f"TECH_FALSE_BREAKDOWN_LOW{w}_{tag}_BULL", f"{tag_cn}{w}日假跌破收回", 1, "假跌破", f"盘中新低但收盘收回区间内")
    for w in [20, 40, 60, 120]:
        _register(f"TECH_VOL_CONTRACT_BREAKOUT_{w}_BULL", f"{w}日波动收缩后向上突破", 1, "波动收缩", "低波动整理后向上突破")
        _register(f"TECH_VOL_CONTRACT_BREAKDOWN_{w}_BEAR", f"{w}日波动收缩后向下跌破", -1, "波动收缩", "低波动整理后向下跌破")
        _register(f"TECH_RANGE_UPPER_REJECT_{w}_BEAR", f"{w}日区间上沿受阻", -1, "区间位置", "触及区间上沿但收盘回落")
        _register(f"TECH_RANGE_LOWER_RECLAIM_{w}_BULL", f"{w}日区间下沿收回", 1, "区间位置", "触及区间下沿后收回")
    for h in [5, 10, 20, 40, 60, 120]:
        for level, label in [(0.03, "温和"), (0.08, "强势"), (0.16, "极强")]:
            key = int(level * 100)
            _register(f"TECH_MOMENTUM_UP_{h}_{key}_BULL", f"{h}日{label}动量上行", 1, "动量", f"{h}日收益超过{level:.0%}")
            _register(f"TECH_MOMENTUM_DOWN_{h}_{key}_BEAR", f"{h}日{label}动量下行", -1, "动量", f"{h}日收益低于-{level:.0%}")
        _register(f"TECH_EXHAUST_UP_{h}_BEAR", f"{h}日急涨衰竭", -1, "动量衰竭", "短期急涨后放量长上影")
        _register(f"TECH_PANIC_DOWN_{h}_BULL", f"{h}日恐慌下跌修复", 1, "恐慌修复", "短期急跌后长下影收回")
    candle_rules = [
        ("HAMMER", "锤头线", 1, "蜡烛形态"),
        ("SHOOTING_STAR", "射击之星", -1, "蜡烛形态"),
        ("BULL_ENGULF", "阳包阴", 1, "蜡烛形态"),
        ("BEAR_ENGULF", "阴包阳", -1, "蜡烛形态"),
        ("BIG_WHITE", "长阳实体", 1, "实体K线"),
        ("BIG_BLACK", "长阴实体", -1, "实体K线"),
        ("LOWER_SHADOW_REVERSAL", "长下影反转", 1, "影线"),
        ("UPPER_SHADOW_REVERSAL", "长上影回落", -1, "影线"),
        ("INSIDE_UP", "孕线后向上突破", 1, "组合K线"),
        ("INSIDE_DOWN", "孕线后向下跌破", -1, "组合K线"),
        ("OUTSIDE_UP", "外包阳线", 1, "组合K线"),
        ("OUTSIDE_DOWN", "外包阴线", -1, "组合K线"),
    ]
    for key, name, direction, family in candle_rules:
        for ctx, ctx_cn in [("LOW", "低位"), ("MID", "中位"), ("HIGH", "高位")]:
            _register(f"TECH_CANDLE_{key}_{ctx}_{'BULL' if direction > 0 else 'BEAR'}", f"{ctx_cn}{name}", direction, family, name)
    for gap, label in [(0.015, "小"), (0.035, "中"), (0.065, "大")]:
        key = int(gap * 1000)
        _register(f"TECH_GAP_UP_CONT_{key}_BULL", f"{label}幅跳空高开延续", 1, "缺口", "跳空高开且收强")
        _register(f"TECH_GAP_UP_FAIL_{key}_BEAR", f"{label}幅跳空高开回落", -1, "缺口", "跳空高开但收弱")
        _register(f"TECH_GAP_DOWN_REV_{key}_BULL", f"{label}幅跳空低开修复", 1, "缺口", "跳空低开后收回")
        _register(f"TECH_GAP_DOWN_CONT_{key}_BEAR", f"{label}幅跳空低开延续", -1, "缺口", "跳空低开且收弱")
    for w in [20, 60, 120]:
        _register(f"TECH_VOLUME_PRICE_UP_{w}_BULL", f"{w}日量价齐升", 1, "量价", "价格上行且成交额放大")
        _register(f"TECH_VOLUME_PRICE_DOWN_{w}_BEAR", f"{w}日量价齐跌", -1, "量价", "价格下行且成交额放大")
        _register(f"TECH_SHRINK_PULLBACK_{w}_BULL", f"{w}日缩量回调", 1, "量价", "上升趋势中缩量回撤")
        _register(f"TECH_DISTRIBUTION_VOLUME_{w}_BEAR", f"{w}日放量滞涨派发", -1, "量价", "高位放量但收益走弱")
    for rule_id, meta in list(CATALOG.items()):
        THEORY.setdefault(rule_id, {"name_cn": meta["name_cn"], "direction": meta["direction"]})
        base.THEORY.setdefault(rule_id, {"name_cn": meta["name_cn"], "direction": meta["direction"]})
        v1.THEORY.setdefault(rule_id, {"name_cn": meta["name_cn"], "direction": meta["direction"]})


def _volume_tag(amount_ratio: pd.Series) -> Dict[str, pd.Series]:
    return {
        "DRY": amount_ratio <= 0.80,
        "NORMAL": (amount_ratio > 0.80) & (amount_ratio < 1.30),
        "VOLUME": amount_ratio >= 1.30,
    }


def _event_rows_for_stock(frame: pd.DataFrame, domain_row: pd.Series) -> List[Dict[str, Any]]:
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    close = pd.to_numeric(frame["qfq_close"], errors="coerce").replace(0.0, np.nan)
    raw_close = pd.to_numeric(frame["close"], errors="coerce").replace(0.0, np.nan)
    open_ = pd.to_numeric(frame["open"], errors="coerce").replace(0.0, np.nan)
    high = pd.to_numeric(frame["high"], errors="coerce").replace(0.0, np.nan)
    low = pd.to_numeric(frame["low"], errors="coerce").replace(0.0, np.nan)
    amount = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    dates = frame["trade_date"].astype(str)
    n = len(frame)
    if n < 280:
        return []

    ret = {w: close.pct_change(w) for w in [1, 2, 3, 5, 10, 20, 40, 60, 120, 250]}
    ma = {w: close.rolling(w, min_periods=max(3, w // 3)).mean() for w in [5, 10, 20, 40, 60, 120, 250]}
    high_roll = {w: close.rolling(w, min_periods=max(5, w // 3)).max().shift(1) for w in [10, 20, 40, 60, 120, 250]}
    low_roll = {w: close.rolling(w, min_periods=max(5, w // 3)).min().shift(1) for w in [10, 20, 40, 60, 120, 250]}
    range_high = {w: close.rolling(w, min_periods=max(5, w // 3)).max() for w in [20, 40, 60, 120]}
    range_low = {w: close.rolling(w, min_periods=max(5, w // 3)).min() for w in [20, 40, 60, 120]}
    vol20 = close.pct_change().rolling(20, min_periods=8).std()
    amount20 = amount.rolling(20, min_periods=8).mean()
    amount_ratio = (amount / amount20.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    range_pct = ((high - low) / raw_close.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    range20 = range_pct.rolling(20, min_periods=8).mean().replace(0.0, np.nan)
    range_ratio = (range_pct / range20).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    body = (raw_close - open_) / raw_close.replace(0.0, np.nan)
    body_abs = body.abs()
    upper_shadow = (high - np.maximum(open_, raw_close)) / raw_close.replace(0.0, np.nan)
    lower_shadow = (np.minimum(open_, raw_close) - low) / raw_close.replace(0.0, np.nan)
    close_pos_20 = ((close - close.rolling(20, min_periods=8).min()) / (close.rolling(20, min_periods=8).max() - close.rolling(20, min_periods=8).min()).replace(0.0, np.nan)).fillna(0.5)
    gap = (open_ / raw_close.shift(1).replace(0.0, np.nan) - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    forward = close.shift(-HORIZON) / close - 1.0

    rows: List[Dict[str, Any]] = []
    base_payload = {
        "ts_code": str(domain_row.name),
        "stock_name": str(domain_row.get("stock_name", "")),
        "industry_name": str(domain_row.get("domain_industry", "")),
        "board": str(domain_row.get("domain_board", "")),
        "size": str(domain_row.get("size", "")),
        "style": str(domain_row.get("style", "")),
        "cell": str(domain_row.get("cell", "")),
    }
    for col in DOMAIN_COLUMNS:
        base_payload[col] = str(domain_row.get(col, "未分域"))
    for numeric_col in ("total_mv", "circ_mv", "amount20"):
        base_payload[numeric_col] = float(domain_row.get(numeric_col, np.nan)) if pd.notna(domain_row.get(numeric_col, np.nan)) else np.nan

    def add(rule_id: str, cond: pd.Series, direction: int, strength_source: pd.Series | float, frequency: str = "20D", persistent: int = 0) -> None:
        valid = cond.fillna(False) & close.notna() & forward.notna()
        if persistent > 0:
            periodic = pd.Series(np.arange(n) % persistent == 0, index=frame.index)
            trigger = valid & ((valid & ~valid.shift(1).fillna(False)) | periodic)
        else:
            trigger = valid & ~valid.shift(1).fillna(False)
        idxs = np.flatnonzero(trigger.to_numpy())
        if len(idxs) == 0:
            return
        if isinstance(strength_source, pd.Series):
            strength_values = strength_source.reindex(frame.index).fillna(0.5).clip(0.05, 1.0)
        else:
            strength_values = pd.Series(float(strength_source), index=frame.index)
        for idx in idxs:
            if idx < 120 or idx + HORIZON >= n:
                continue
            fwd = float(forward.iloc[idx])
            if not math.isfinite(fwd):
                continue
            payload = dict(base_payload)
            payload.update(
                {
                    "date": str(dates.iloc[idx]),
                    "future_date": str(dates.iloc[idx + HORIZON]),
                    "split": "full",
                    "future_split": "full",
                    "rule_id": rule_id,
                    "frequency": frequency,
                    "direction": int(direction),
                    "strength": float(strength_values.iloc[idx]),
                    "forward_return": fwd,
                    "signed_return": float(direction) * fwd,
                    "ret20": float(ret[20].iloc[idx]) if pd.notna(ret[20].iloc[idx]) else 0.0,
                    "ret60": float(ret[60].iloc[idx]) if pd.notna(ret[60].iloc[idx]) else 0.0,
                    "vol20": float(vol20.iloc[idx]) if pd.notna(vol20.iloc[idx]) else 0.0,
                    "range20": float(range20.iloc[idx]) if pd.notna(range20.iloc[idx]) else 0.0,
                    "turnover": np.nan,
                    "pe_ttm": np.nan,
                    "pb": np.nan,
                    "ps_ttm": np.nan,
                    "dv_ttm": np.nan,
                    "volume_ratio": float(amount_ratio.iloc[idx]),
                    "range_ratio": float(range_ratio.iloc[idx]),
                    "amount_ratio": float(amount_ratio.iloc[idx]),
                    "close_position": float(close_pos_20.iloc[idx]),
                }
            )
            rows.append(payload)

    for w in [5, 10, 20, 40, 60, 120, 250]:
        add(f"TECH_PRICE_RECLAIM_MA{w}_BULL", (close > ma[w]) & (close.shift(1) <= ma[w].shift(1)), 1, (close / ma[w] - 1).abs(), "20D")
        add(f"TECH_PRICE_LOSE_MA{w}_BEAR", (close < ma[w]) & (close.shift(1) >= ma[w].shift(1)), -1, (close / ma[w] - 1).abs(), "20D")
        add(f"TECH_MA{w}_SUPPORT_BULL", (close > ma[w]) & (low <= ma[w] * 1.015) & (ret[60] > 0.03) & (body > -0.015), 1, 0.62, "20D")
        add(f"TECH_MA{w}_RESIST_BEAR", (close < ma[w]) & (high >= ma[w] * 0.985) & (ret[60] < -0.03) & (body < 0.015), -1, 0.62, "20D")
    for a, b in [(5, 20), (10, 20), (20, 60), (20, 120), (60, 120), (120, 250)]:
        add(f"TECH_GOLDEN_CROSS_MA{a}_{b}_BULL", (ma[a] > ma[b]) & (ma[a].shift(1) <= ma[b].shift(1)), 1, 0.72, "20D")
        add(f"TECH_DEATH_CROSS_MA{a}_{b}_BEAR", (ma[a] < ma[b]) & (ma[a].shift(1) >= ma[b].shift(1)), -1, 0.72, "20D")
    for a, b, c in [(5, 20, 60), (10, 20, 60), (20, 60, 120), (60, 120, 250)]:
        add(f"TECH_MA_STACK_{a}_{b}_{c}_BULL", (close > ma[a]) & (ma[a] > ma[b]) & (ma[b] > ma[c]), 1, 0.70, "60D", persistent=20)
        add(f"TECH_MA_STACK_{a}_{b}_{c}_BEAR", (close < ma[a]) & (ma[a] < ma[b]) & (ma[b] < ma[c]), -1, 0.70, "60D", persistent=20)
    vol_tags = _volume_tag(amount_ratio)
    for w in [10, 20, 40, 60, 120, 250]:
        for tag, tag_cond in vol_tags.items():
            add(f"TECH_BREAKOUT_HIGH{w}_{tag}_BULL", (close > high_roll[w] * 1.003) & tag_cond, 1, 0.66, "20D")
            add(f"TECH_BREAKDOWN_LOW{w}_{tag}_BEAR", (close < low_roll[w] * 0.997) & tag_cond, -1, 0.66, "20D")
            add(f"TECH_FALSE_BREAKOUT_HIGH{w}_{tag}_BEAR", (high > high_roll[w] * 1.006) & (close < high_roll[w]) & tag_cond & (upper_shadow > body_abs), -1, 0.68, "20D")
            add(f"TECH_FALSE_BREAKDOWN_LOW{w}_{tag}_BULL", (low < low_roll[w] * 0.994) & (close > low_roll[w]) & tag_cond & (lower_shadow > body_abs), 1, 0.68, "20D")
    for w in [20, 40, 60, 120]:
        width = (range_high[w] - range_low[w]) / close.replace(0.0, np.nan)
        low_width = width < width.rolling(120, min_periods=40).quantile(0.30)
        add(f"TECH_VOL_CONTRACT_BREAKOUT_{w}_BULL", low_width & (close > range_high[w].shift(1) * 1.003), 1, 0.74, "20D")
        add(f"TECH_VOL_CONTRACT_BREAKDOWN_{w}_BEAR", low_width & (close < range_low[w].shift(1) * 0.997), -1, 0.74, "20D")
        pos = ((close - range_low[w]) / (range_high[w] - range_low[w]).replace(0.0, np.nan)).fillna(0.5)
        add(f"TECH_RANGE_UPPER_REJECT_{w}_BEAR", (pos > 0.86) & (upper_shadow > 1.3 * body_abs) & (body < 0.01), -1, 0.64, "20D")
        add(f"TECH_RANGE_LOWER_RECLAIM_{w}_BULL", (pos < 0.18) & (lower_shadow > 1.3 * body_abs) & (body > -0.01), 1, 0.64, "20D")
    for h in [5, 10, 20, 40, 60, 120]:
        for level in [0.03, 0.08, 0.16]:
            key = int(level * 100)
            add(f"TECH_MOMENTUM_UP_{h}_{key}_BULL", ret[h] > level, 1, ret[h].abs(), "20D", persistent=20 if h >= 20 else 0)
            add(f"TECH_MOMENTUM_DOWN_{h}_{key}_BEAR", ret[h] < -level, -1, ret[h].abs(), "20D", persistent=20 if h >= 20 else 0)
        add(f"TECH_EXHAUST_UP_{h}_BEAR", (ret[h] > 0.15) & (upper_shadow > 0.025) & (amount_ratio > 1.25), -1, 0.76, "20D")
        add(f"TECH_PANIC_DOWN_{h}_BULL", (ret[h] < -0.15) & (lower_shadow > 0.025) & (close > low * 1.04), 1, 0.76, "20D")
    context = {
        "LOW": close_pos_20 < 0.30,
        "MID": (close_pos_20 >= 0.30) & (close_pos_20 <= 0.70),
        "HIGH": close_pos_20 > 0.70,
    }
    bull_engulf = (body > 0.018) & (body.shift(1) < -0.010) & (raw_close > open_.shift(1)) & (open_ < raw_close.shift(1))
    bear_engulf = (body < -0.018) & (body.shift(1) > 0.010) & (raw_close < open_.shift(1)) & (open_ > raw_close.shift(1))
    inside = (high < high.shift(1)) & (low > low.shift(1))
    outside = (high > high.shift(1)) & (low < low.shift(1))
    for ctx, ctx_cond in context.items():
        add(f"TECH_CANDLE_HAMMER_{ctx}_BULL", ctx_cond & (lower_shadow > 2.0 * body_abs) & (upper_shadow < 0.012) & (body > -0.006), 1, 0.65, "20D")
        add(f"TECH_CANDLE_SHOOTING_STAR_{ctx}_BEAR", ctx_cond & (upper_shadow > 2.0 * body_abs) & (lower_shadow < 0.012) & (body < 0.006), -1, 0.65, "20D")
        add(f"TECH_CANDLE_BULL_ENGULF_{ctx}_BULL", ctx_cond & bull_engulf, 1, 0.70, "20D")
        add(f"TECH_CANDLE_BEAR_ENGULF_{ctx}_BEAR", ctx_cond & bear_engulf, -1, 0.70, "20D")
        add(f"TECH_CANDLE_BIG_WHITE_{ctx}_BULL", ctx_cond & (body > 0.045) & (range_ratio > 1.05), 1, 0.68, "20D")
        add(f"TECH_CANDLE_BIG_BLACK_{ctx}_BEAR", ctx_cond & (body < -0.045) & (range_ratio > 1.05), -1, 0.68, "20D")
        add(f"TECH_CANDLE_LOWER_SHADOW_REVERSAL_{ctx}_BULL", ctx_cond & (lower_shadow > 0.035) & (close > open_), 1, 0.66, "20D")
        add(f"TECH_CANDLE_UPPER_SHADOW_REVERSAL_{ctx}_BEAR", ctx_cond & (upper_shadow > 0.035) & (close < open_), -1, 0.66, "20D")
        add(f"TECH_CANDLE_INSIDE_UP_{ctx}_BULL", ctx_cond & inside.shift(1).fillna(False) & (close > high.shift(1)), 1, 0.60, "20D")
        add(f"TECH_CANDLE_INSIDE_DOWN_{ctx}_BEAR", ctx_cond & inside.shift(1).fillna(False) & (close < low.shift(1)), -1, 0.60, "20D")
        add(f"TECH_CANDLE_OUTSIDE_UP_{ctx}_BULL", ctx_cond & outside & (body > 0.015), 1, 0.62, "20D")
        add(f"TECH_CANDLE_OUTSIDE_DOWN_{ctx}_BEAR", ctx_cond & outside & (body < -0.015), -1, 0.62, "20D")
    for gap_level in [0.015, 0.035, 0.065]:
        key = int(gap_level * 1000)
        add(f"TECH_GAP_UP_CONT_{key}_BULL", (gap > gap_level) & (body > 0.010) & (close > ma[20]), 1, 0.70, "20D")
        add(f"TECH_GAP_UP_FAIL_{key}_BEAR", (gap > gap_level) & (body < -0.010) & (upper_shadow > body_abs), -1, 0.70, "20D")
        add(f"TECH_GAP_DOWN_REV_{key}_BULL", (gap < -gap_level) & (body > 0.010) & (lower_shadow > body_abs), 1, 0.70, "20D")
        add(f"TECH_GAP_DOWN_CONT_{key}_BEAR", (gap < -gap_level) & (body < -0.010) & (close < ma[20]), -1, 0.70, "20D")
    for w in [20, 60, 120]:
        add(f"TECH_VOLUME_PRICE_UP_{w}_BULL", (ret[w] > 0.05) & (amount_ratio > 1.25) & (close > ma[min(w, 60)]), 1, 0.68, "20D", persistent=20)
        add(f"TECH_VOLUME_PRICE_DOWN_{w}_BEAR", (ret[w] < -0.05) & (amount_ratio > 1.25) & (close < ma[min(w, 60)]), -1, 0.68, "20D", persistent=20)
        add(f"TECH_SHRINK_PULLBACK_{w}_BULL", (ret[w] > 0.05) & (ret[10] < 0.0) & (amount_ratio < 0.85) & (close > ma[120]), 1, 0.62, "20D")
        add(f"TECH_DISTRIBUTION_VOLUME_{w}_BEAR", (ret[w] > 0.08) & (ret[10] < 0.02) & (amount_ratio > 1.50) & (upper_shadow > lower_shadow), -1, 0.66, "20D")
    return rows


def _latest_domain_map(raw_events: pd.DataFrame) -> pd.DataFrame:
    local = raw_events.loc[raw_events[DOMAIN_COL].astype(str).isin(STYLE_SIZE_DOMAINS)].copy()
    local = local.sort_values(["ts_code", "date"])
    latest = local.groupby("ts_code", sort=False).tail(1).set_index("ts_code")
    return latest


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def build_expanded_events(db: Path, raw_events: pd.DataFrame, cache_path: Path, refresh: bool = False) -> pd.DataFrame:
    _init_catalog()
    if cache_path.exists() and not refresh:
        return pd.read_pickle(cache_path)
    domain_map = _latest_domain_map(raw_events)
    codes = domain_map.index.astype(str).tolist()
    rows: List[Dict[str, Any]] = []
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        for batch_idx, batch in enumerate(_chunks(codes, 180), start=1):
            placeholders = ",".join("?" for _ in batch)
            sql = (
                "select trade_date, ts_code, stock_name, open, high, low, close, qfq_close, pre_close, pct_chg, vol, amount "
                f"from stock_ohlcv_daily where trade_date >= ? and ts_code in ({placeholders}) "
                "order by ts_code, trade_date"
            )
            frame = pd.read_sql_query(sql, conn, params=[MIN_START, *batch])
            if frame.empty:
                continue
            for code, group in frame.groupby("ts_code", sort=False):
                if code not in domain_map.index:
                    continue
                rows.extend(_event_rows_for_stock(group, domain_map.loc[code]))
            if batch_idx % 5 == 0:
                print(f"[expanded] batch {batch_idx}, events={len(rows):,}", flush=True)
    expanded = pd.DataFrame(rows)
    expanded.to_pickle(cache_path)
    pd.DataFrame(CATALOG.values()).to_csv(cache_path.with_suffix(".catalog.csv"), index=False, encoding="utf-8-sig")
    return expanded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V8 expanded-pattern Wyckoff domain memory model.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=EVENT_PATH)
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / OUTPUT_SUBDIR)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_OUTPUT_DIR / "expanded_pattern_events_v8.pkl")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _init_catalog()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/6] 读取原始Wyckoff事件", flush=True)
    raw_events = pd.read_pickle(args.events)
    strict_wyckoff_raw = raw_events.loc[raw_events[DOMAIN_COL].astype(str).isin(STYLE_SIZE_DOMAINS)].copy()
    print(f"[wyckoff] strict={len(strict_wyckoff_raw):,}, stocks={strict_wyckoff_raw['ts_code'].nunique():,}", flush=True)

    print("[2/6] 生成/读取扩展图形技术事件", flush=True)
    expanded_raw = build_expanded_events(args.db, raw_events, args.cache_path, bool(args.refresh_cache))
    print(f"[expanded] events={len(expanded_raw):,}, rules={expanded_raw['rule_id'].nunique():,}, stocks={expanded_raw['ts_code'].nunique():,}", flush=True)

    print("[3/6] 合并Wyckoff事件与扩展技术事件并计算上下文", flush=True)
    combined_raw = pd.concat([strict_wyckoff_raw, expanded_raw], ignore_index=True, sort=False)
    combined_path = output_dir / "V8_wyckoff_plus_expanded_events.pkl"
    combined_raw.to_pickle(combined_path)
    scored_seed = prepare_events(combined_path)
    print(f"[combined] events={len(scored_seed):,}, base_rules={scored_seed['rule_id'].nunique():,}", flush=True)

    print("[4/6] 学习12域扩展多规则库", flush=True)
    memory = build_memory(scored_seed, DOMAIN_COL)
    scored_events = attach_memory(scored_seed, memory, DOMAIN_COL)
    scored_events["_domain_raw_score"] = weighted_memory_score(scored_events, v1.DOMAIN_PROFILE)
    rulebook = v1.build_domain_rulebook(scored_events)
    thresholds = v1.learn_domain_thresholds(scored_events)
    rulebook.to_csv(output_dir / "风格市值12域V8扩展Wyckoff规则库.csv", index=False, encoding="utf-8-sig")
    thresholds.to_csv(output_dir / "风格市值12域V8统一仓位阈值.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(CATALOG.values()).to_csv(output_dir / "V8扩展图形规则目录.csv", index=False, encoding="utf-8-sig")
    print(f"[rulebook] rows={len(rulebook):,}, domains={rulebook['domain_value'].nunique():,}, base_rules={rulebook['rule_id'].nunique():,}", flush=True)

    print("[5/6] 应用V7混合执行器到五只样本股票", flush=True)
    results: List[Dict[str, Any]] = []
    with sqlite3.connect(str(args.db)) as conn:
        as_of = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0] if args.as_of == "latest" else str(args.as_of)
        for code in args.codes:
            series = _load_stock(conn, str(code), as_of)
            result = v7._run_one_stock(series, scored_events, rulebook, thresholds, float(args.cost_rate))
            result["evolver_profile"]["mode"] = "style_size_domain_rulebook_v8_expanded_patterns_hybrid_executor"
            result["evolver_profile"]["expanded_pattern_rules"] = int(expanded_raw["rule_id"].nunique())
            result["model_boundary"] = "模型二：Wyckoff形态记忆学习；扩展几百条图形技术规则；风格×市值12域统一规则库；全市场兜底记忆；同域全股票共用规则；不使用六类技术因子；不做单股路径拟合。"
            safe = _safe_name(f"V8扩展图形规则_{result['domain_value']}_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值相对强度.png"
            json_path = output_dir / f"{safe}.json"
            txt_path = output_dir / f"{safe}_学习记录.txt"
            result["chart_path"] = str(chart_path)
            result["json_path"] = str(json_path)
            result["txt_path"] = str(txt_path)
            _plot_style_size_chart(result, chart_path)
            json_path.write_text(json.dumps(v1._json_light(result), ensure_ascii=False, indent=2), encoding="utf-8")
            v5._write_text_record(result, txt_path)
            results.append(result)
            print(
                f"[stock] {result['domain_value']} {result['code']} {result['name']} "
                f"{result['current_position_label']} Sharpe {result['metrics']['strategy_sharpe']:.2f}/"
                f"{result['metrics']['price_sharpe']:.2f} annual {result['metrics']['strategy_annual_return']:.2%}/"
                f"{result['metrics']['price_annual_return']:.2%}",
                flush=True,
            )

    print("[6/6] 写出汇总", flush=True)
    v5._write_summary(results, output_dir)
    (output_dir / "V8模型说明.txt").write_text(
        "\n".join(
            [
                "V8扩展图形规则版",
                f"扩展图形基础规则数：{expanded_raw['rule_id'].nunique()}",
                f"扩展事件数：{len(expanded_raw):,}",
                f"合并后规则数：{scored_events['rule_id'].nunique()}",
                f"最终域规则库行数：{len(rulebook):,}",
                "框架：Analyzer扩展图形规则 -> 域内Memory -> Rulebook/Evolver -> V7混合执行器 -> 五档仓位。",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "output_dir": str(output_dir),
            "expanded_base_rules": int(expanded_raw["rule_id"].nunique()),
            "combined_base_rules": int(scored_events["rule_id"].nunique()),
            "rulebook_rows": int(len(rulebook)),
            "domain_count": int(rulebook["domain_value"].nunique()),
            "results": [
                {
                    "code": item["code"],
                    "name": item["name"],
                    "domain": item["domain_value"],
                    "position": item["current_position_label"],
                    "score": round(float(item["current_score"]), 1),
                    "strategy_sharpe": round(float(item["metrics"]["strategy_sharpe"]), 3),
                    "price_sharpe": round(float(item["metrics"]["price_sharpe"]), 3),
                    "strategy_annual": round(float(item["metrics"]["strategy_annual_return"]), 4),
                    "price_annual": round(float(item["metrics"]["price_annual_return"]), 4),
                    "chart_path": item["chart_path"],
                    "txt_path": item["txt_path"],
                }
                for item in results
            ],
        },
        ensure_ascii=False,
        indent=2,
    ), flush=True)


if __name__ == "__main__":
    main()
