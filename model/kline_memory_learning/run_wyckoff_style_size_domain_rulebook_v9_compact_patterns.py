"""V9 compact expanded-pattern runner for domain-shared Wyckoff memory.

The original cached Wyckoff event store contains only ten parent structures.
This runner keeps the same style x size domain memory framework, but creates a
larger low-frequency technical-pattern event pool from full OHLCV history.
Events are capped by observable trigger strength per stock to prevent a noisy
high-frequency library from overwhelming the memory and Evolver layers.
"""

from __future__ import annotations

import math
import sqlite3
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("future.no_silent_downcasting", True)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning import run_wyckoff_style_size_domain_rulebook_v8_expanded_patterns_batch as v8  # noqa: E402


MAX_EVENTS_PER_STOCK = 620
MAX_EVENTS_PER_DATE = 4
CHUNK_SIZE = 70
HORIZON = v8.HORIZON


def _to_series(value: pd.Series | float, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.reindex(index).replace([np.inf, -np.inf], np.nan).fillna(0.5).clip(0.05, 1.0)
    return pd.Series(float(value), index=index)


def _compact(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(rows) <= MAX_EVENTS_PER_STOCK:
        return rows
    frame = pd.DataFrame(rows)
    frame = frame.sort_values(["date", "strength"], ascending=[True, False])
    frame = frame.groupby("date", sort=False).head(MAX_EVENTS_PER_DATE)
    if len(frame) <= MAX_EVENTS_PER_STOCK:
        return frame.to_dict("records")
    frame["_rank_strength"] = frame["strength"].rank(method="first", ascending=False)
    frame["_rank_time"] = np.linspace(0.0, 1.0, len(frame))
    frame["_keep_score"] = 0.78 * frame["_rank_strength"] / max(len(frame), 1) + 0.22 * frame["_rank_time"]
    kept = frame.nsmallest(MAX_EVENTS_PER_STOCK, "_keep_score").sort_values("date")
    return kept.drop(columns=["_rank_strength", "_rank_time", "_keep_score"]).to_dict("records")


def _event_rows_for_stock_compact(frame: pd.DataFrame, domain_row: pd.Series) -> List[Dict[str, Any]]:
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    idx = frame.index
    close = pd.to_numeric(frame["qfq_close"], errors="coerce").replace(0.0, np.nan)
    raw_close = pd.to_numeric(frame["close"], errors="coerce").replace(0.0, np.nan)
    open_ = pd.to_numeric(frame["open"], errors="coerce").replace(0.0, np.nan)
    high = pd.to_numeric(frame["high"], errors="coerce").replace(0.0, np.nan)
    low = pd.to_numeric(frame["low"], errors="coerce").replace(0.0, np.nan)
    amount = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    dates = frame["trade_date"].astype(str)
    n = len(frame)
    if n < 300:
        return []

    ret = {w: close.pct_change(w, fill_method=None) for w in [1, 2, 3, 5, 10, 20, 40, 60, 120, 250]}
    ma = {w: close.rolling(w, min_periods=max(3, w // 3)).mean() for w in [5, 10, 20, 40, 60, 120, 250]}
    high_roll = {w: close.rolling(w, min_periods=max(5, w // 3)).max().shift(1) for w in [10, 20, 40, 60, 120, 250]}
    low_roll = {w: close.rolling(w, min_periods=max(5, w // 3)).min().shift(1) for w in [10, 20, 40, 60, 120, 250]}
    range_high = {w: close.rolling(w, min_periods=max(5, w // 3)).max() for w in [20, 40, 60, 120]}
    range_low = {w: close.rolling(w, min_periods=max(5, w // 3)).min() for w in [20, 40, 60, 120]}
    amount20 = amount.rolling(20, min_periods=8).mean()
    amount_ratio = (amount / amount20.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    range_pct = ((high - low) / raw_close.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    range20 = range_pct.rolling(20, min_periods=8).mean().replace(0.0, np.nan)
    range_ratio = (range_pct / range20).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    body = (raw_close - open_) / raw_close.replace(0.0, np.nan)
    body_abs = body.abs()
    upper_shadow = (high - np.maximum(open_, raw_close)) / raw_close.replace(0.0, np.nan)
    lower_shadow = (np.minimum(open_, raw_close) - low) / raw_close.replace(0.0, np.nan)
    close_min20 = close.rolling(20, min_periods=8).min()
    close_max20 = close.rolling(20, min_periods=8).max()
    close_position = ((close - close_min20) / (close_max20 - close_min20).replace(0.0, np.nan)).fillna(0.5)
    vol20 = close.pct_change(fill_method=None).rolling(20, min_periods=8).std()
    gap = (open_ / raw_close.shift(1).replace(0.0, np.nan) - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    forward = close.shift(-HORIZON) / close - 1.0
    weekly_anchor = pd.Series(np.arange(n) % 5 == 0, index=idx)
    monthly_anchor = pd.Series(np.arange(n) % 20 == 0, index=idx)

    payload_base = {
        "ts_code": str(domain_row.name),
        "stock_name": str(domain_row.get("stock_name", "")),
        "industry_name": str(domain_row.get("domain_industry", "")),
        "board": str(domain_row.get("domain_board", "")),
        "size": str(domain_row.get("size", "")),
        "style": str(domain_row.get("style", "")),
        "cell": str(domain_row.get("cell", "")),
    }
    for col in v8.DOMAIN_COLUMNS:
        payload_base[col] = str(domain_row.get(col, "未分域"))
    for col in ("total_mv", "circ_mv", "amount20"):
        val = domain_row.get(col, np.nan)
        payload_base[col] = float(val) if pd.notna(val) else np.nan

    rows: List[Dict[str, Any]] = []
    specs: List[Tuple[str, pd.Series, int, pd.Series | float, pd.Series]] = []

    def spec(rule_id: str, cond: pd.Series, direction: int, strength: pd.Series | float, anchor: pd.Series = weekly_anchor) -> None:
        specs.append((rule_id, cond, direction, strength, anchor))

    for w in [5, 10, 20, 40, 60, 120, 250]:
        spec(f"TECH_PRICE_RECLAIM_MA{w}_BULL", (close > ma[w]) & (close.shift(1) <= ma[w].shift(1)), 1, (close / ma[w] - 1).abs(), weekly_anchor)
        spec(f"TECH_PRICE_LOSE_MA{w}_BEAR", (close < ma[w]) & (close.shift(1) >= ma[w].shift(1)), -1, (close / ma[w] - 1).abs(), weekly_anchor)
        spec(f"TECH_MA{w}_SUPPORT_BULL", (close > ma[w]) & (low <= ma[w] * 1.015) & (ret[60] > 0.03) & (body > -0.015), 1, 0.62, weekly_anchor)
        spec(f"TECH_MA{w}_RESIST_BEAR", (close < ma[w]) & (high >= ma[w] * 0.985) & (ret[60] < -0.03) & (body < 0.015), -1, 0.62, weekly_anchor)
    for a, b in [(5, 20), (10, 20), (20, 60), (20, 120), (60, 120), (120, 250)]:
        spec(f"TECH_GOLDEN_CROSS_MA{a}_{b}_BULL", (ma[a] > ma[b]) & (ma[a].shift(1) <= ma[b].shift(1)), 1, 0.72, weekly_anchor)
        spec(f"TECH_DEATH_CROSS_MA{a}_{b}_BEAR", (ma[a] < ma[b]) & (ma[a].shift(1) >= ma[b].shift(1)), -1, 0.72, weekly_anchor)
    for a, b, c in [(5, 20, 60), (10, 20, 60), (20, 60, 120), (60, 120, 250)]:
        spec(f"TECH_MA_STACK_{a}_{b}_{c}_BULL", (close > ma[a]) & (ma[a] > ma[b]) & (ma[b] > ma[c]), 1, 0.70, monthly_anchor)
        spec(f"TECH_MA_STACK_{a}_{b}_{c}_BEAR", (close < ma[a]) & (ma[a] < ma[b]) & (ma[b] < ma[c]), -1, 0.70, monthly_anchor)

    vol_tags = {
        "DRY": amount_ratio <= 0.80,
        "NORMAL": (amount_ratio > 0.80) & (amount_ratio < 1.30),
        "VOLUME": amount_ratio >= 1.30,
    }
    for w in [10, 20, 40, 60, 120, 250]:
        for tag, tag_cond in vol_tags.items():
            spec(f"TECH_BREAKOUT_HIGH{w}_{tag}_BULL", (close > high_roll[w] * 1.003) & tag_cond, 1, 0.66, weekly_anchor)
            spec(f"TECH_BREAKDOWN_LOW{w}_{tag}_BEAR", (close < low_roll[w] * 0.997) & tag_cond, -1, 0.66, weekly_anchor)
            spec(f"TECH_FALSE_BREAKOUT_HIGH{w}_{tag}_BEAR", (high > high_roll[w] * 1.006) & (close < high_roll[w]) & tag_cond & (upper_shadow > body_abs), -1, 0.68, weekly_anchor)
            spec(f"TECH_FALSE_BREAKDOWN_LOW{w}_{tag}_BULL", (low < low_roll[w] * 0.994) & (close > low_roll[w]) & tag_cond & (lower_shadow > body_abs), 1, 0.68, weekly_anchor)
    for w in [20, 40, 60, 120]:
        width = (range_high[w] - range_low[w]) / close.replace(0.0, np.nan)
        low_width = width < width.rolling(120, min_periods=40).quantile(0.30)
        pos = ((close - range_low[w]) / (range_high[w] - range_low[w]).replace(0.0, np.nan)).fillna(0.5)
        spec(f"TECH_VOL_CONTRACT_BREAKOUT_{w}_BULL", low_width & (close > range_high[w].shift(1) * 1.003), 1, 0.74, weekly_anchor)
        spec(f"TECH_VOL_CONTRACT_BREAKDOWN_{w}_BEAR", low_width & (close < range_low[w].shift(1) * 0.997), -1, 0.74, weekly_anchor)
        spec(f"TECH_RANGE_UPPER_REJECT_{w}_BEAR", (pos > 0.86) & (upper_shadow > 1.3 * body_abs) & (body < 0.01), -1, 0.64, weekly_anchor)
        spec(f"TECH_RANGE_LOWER_RECLAIM_{w}_BULL", (pos < 0.18) & (lower_shadow > 1.3 * body_abs) & (body > -0.01), 1, 0.64, weekly_anchor)
    for h in [5, 10, 20, 40, 60, 120]:
        for level in [0.03, 0.08, 0.16]:
            key = int(level * 100)
            spec(f"TECH_MOMENTUM_UP_{h}_{key}_BULL", ret[h] > level, 1, ret[h].abs(), weekly_anchor)
            spec(f"TECH_MOMENTUM_DOWN_{h}_{key}_BEAR", ret[h] < -level, -1, ret[h].abs(), weekly_anchor)
        spec(f"TECH_EXHAUST_UP_{h}_BEAR", (ret[h] > 0.15) & (upper_shadow > 0.025) & (amount_ratio > 1.25), -1, 0.76, weekly_anchor)
        spec(f"TECH_PANIC_DOWN_{h}_BULL", (ret[h] < -0.15) & (lower_shadow > 0.025) & (close > low * 1.04), 1, 0.76, weekly_anchor)

    contexts = {
        "LOW": close_position < 0.30,
        "MID": (close_position >= 0.30) & (close_position <= 0.70),
        "HIGH": close_position > 0.70,
    }
    bull_engulf = (body > 0.018) & (body.shift(1) < -0.010) & (raw_close > open_.shift(1)) & (open_ < raw_close.shift(1))
    bear_engulf = (body < -0.018) & (body.shift(1) > 0.010) & (raw_close < open_.shift(1)) & (open_ > raw_close.shift(1))
    inside = (high < high.shift(1)) & (low > low.shift(1))
    outside = (high > high.shift(1)) & (low < low.shift(1))
    for ctx_name, ctx_cond in contexts.items():
        spec(f"TECH_CANDLE_HAMMER_{ctx_name}_BULL", ctx_cond & (lower_shadow > 2.0 * body_abs) & (upper_shadow < 0.012) & (body > -0.006), 1, 0.65, weekly_anchor)
        spec(f"TECH_CANDLE_SHOOTING_STAR_{ctx_name}_BEAR", ctx_cond & (upper_shadow > 2.0 * body_abs) & (lower_shadow < 0.012) & (body < 0.006), -1, 0.65, weekly_anchor)
        spec(f"TECH_CANDLE_BULL_ENGULF_{ctx_name}_BULL", ctx_cond & bull_engulf, 1, 0.70, weekly_anchor)
        spec(f"TECH_CANDLE_BEAR_ENGULF_{ctx_name}_BEAR", ctx_cond & bear_engulf, -1, 0.70, weekly_anchor)
        spec(f"TECH_CANDLE_BIG_WHITE_{ctx_name}_BULL", ctx_cond & (body > 0.045) & (range_ratio > 1.05), 1, 0.68, weekly_anchor)
        spec(f"TECH_CANDLE_BIG_BLACK_{ctx_name}_BEAR", ctx_cond & (body < -0.045) & (range_ratio > 1.05), -1, 0.68, weekly_anchor)
        spec(f"TECH_CANDLE_LOWER_SHADOW_REVERSAL_{ctx_name}_BULL", ctx_cond & (lower_shadow > 0.035) & (close > open_), 1, 0.66, weekly_anchor)
        spec(f"TECH_CANDLE_UPPER_SHADOW_REVERSAL_{ctx_name}_BEAR", ctx_cond & (upper_shadow > 0.035) & (close < open_), -1, 0.66, weekly_anchor)
        spec(f"TECH_CANDLE_INSIDE_UP_{ctx_name}_BULL", ctx_cond & inside.shift(1, fill_value=False) & (close > high.shift(1)), 1, 0.60, weekly_anchor)
        spec(f"TECH_CANDLE_INSIDE_DOWN_{ctx_name}_BEAR", ctx_cond & inside.shift(1, fill_value=False) & (close < low.shift(1)), -1, 0.60, weekly_anchor)
        spec(f"TECH_CANDLE_OUTSIDE_UP_{ctx_name}_BULL", ctx_cond & outside & (body > 0.015), 1, 0.62, weekly_anchor)
        spec(f"TECH_CANDLE_OUTSIDE_DOWN_{ctx_name}_BEAR", ctx_cond & outside & (body < -0.015), -1, 0.62, weekly_anchor)
    for gap_level in [0.015, 0.035, 0.065]:
        key = int(gap_level * 1000)
        spec(f"TECH_GAP_UP_CONT_{key}_BULL", (gap > gap_level) & (body > 0.010) & (close > ma[20]), 1, 0.70, weekly_anchor)
        spec(f"TECH_GAP_UP_FAIL_{key}_BEAR", (gap > gap_level) & (body < -0.010) & (upper_shadow > body_abs), -1, 0.70, weekly_anchor)
        spec(f"TECH_GAP_DOWN_REV_{key}_BULL", (gap < -gap_level) & (body > 0.010) & (lower_shadow > body_abs), 1, 0.70, weekly_anchor)
        spec(f"TECH_GAP_DOWN_CONT_{key}_BEAR", (gap < -gap_level) & (body < -0.010) & (close < ma[20]), -1, 0.70, weekly_anchor)
    for w in [20, 60, 120]:
        spec(f"TECH_VOLUME_PRICE_UP_{w}_BULL", (ret[w] > 0.05) & (amount_ratio > 1.25) & (close > ma[min(w, 60)]), 1, 0.68, monthly_anchor)
        spec(f"TECH_VOLUME_PRICE_DOWN_{w}_BEAR", (ret[w] < -0.05) & (amount_ratio > 1.25) & (close < ma[min(w, 60)]), -1, 0.68, monthly_anchor)
        spec(f"TECH_SHRINK_PULLBACK_{w}_BULL", (ret[w] > 0.05) & (ret[10] < 0.0) & (amount_ratio < 0.85) & (close > ma[120]), 1, 0.62, weekly_anchor)
        spec(f"TECH_DISTRIBUTION_VOLUME_{w}_BEAR", (ret[w] > 0.08) & (ret[10] < 0.02) & (amount_ratio > 1.50) & (upper_shadow > lower_shadow), -1, 0.66, weekly_anchor)

    valid_base = close.notna() & forward.notna()
    for rule_id, cond, direction, strength_source, anchor in specs:
        trigger = cond.fillna(False) & anchor & valid_base
        locs = np.flatnonzero(trigger.to_numpy())
        if len(locs) == 0:
            continue
        strength_values = _to_series(strength_source, idx)
        for i in locs:
            if i < 120 or i + HORIZON >= n:
                continue
            fwd = float(forward.iloc[i])
            if not math.isfinite(fwd):
                continue
            payload = dict(payload_base)
            payload.update(
                {
                    "date": str(dates.iloc[i]),
                    "future_date": str(dates.iloc[i + HORIZON]),
                    "split": "full",
                    "future_split": "full",
                    "rule_id": rule_id,
                    "frequency": "20D",
                    "direction": int(direction),
                    "strength": float(strength_values.iloc[i]),
                    "forward_return": fwd,
                    "signed_return": float(direction) * fwd,
                    "ret20": float(ret[20].iloc[i]) if pd.notna(ret[20].iloc[i]) else 0.0,
                    "ret60": float(ret[60].iloc[i]) if pd.notna(ret[60].iloc[i]) else 0.0,
                    "vol20": float(vol20.iloc[i]) if pd.notna(vol20.iloc[i]) else 0.0,
                    "range20": float(range20.iloc[i]) if pd.notna(range20.iloc[i]) else 0.0,
                    "turnover": np.nan,
                    "pe_ttm": np.nan,
                    "pb": np.nan,
                    "ps_ttm": np.nan,
                    "dv_ttm": np.nan,
                    "volume_ratio": float(amount_ratio.iloc[i]),
                    "range_ratio": float(range_ratio.iloc[i]),
                    "amount_ratio": float(amount_ratio.iloc[i]),
                    "close_position": float(close_position.iloc[i]),
                }
            )
            rows.append(payload)
    return _compact(rows)


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def build_expanded_events_compact(db: Path, raw_events: pd.DataFrame, cache_path: Path, refresh: bool = False) -> pd.DataFrame:
    v8._init_catalog()
    if cache_path.exists() and not refresh:
        return pd.read_pickle(cache_path)
    domain_map = v8._latest_domain_map(raw_events)
    codes = domain_map.index.astype(str).tolist()
    rows: List[Dict[str, Any]] = []
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        for batch_idx, batch in enumerate(_chunks(codes, CHUNK_SIZE), start=1):
            placeholders = ",".join("?" for _ in batch)
            sql = (
                "select trade_date, ts_code, stock_name, open, high, low, close, qfq_close, pre_close, pct_chg, vol, amount "
                f"from stock_ohlcv_daily where trade_date >= ? and ts_code in ({placeholders}) "
                "order by ts_code, trade_date"
            )
            frame = pd.read_sql_query(sql, conn, params=[v8.MIN_START, *batch])
            for code, group in frame.groupby("ts_code", sort=False):
                if code in domain_map.index:
                    rows.extend(_event_rows_for_stock_compact(group, domain_map.loc[code]))
            print(f"[expanded-compact] batch={batch_idx}, stocks={min(batch_idx * CHUNK_SIZE, len(codes))}/{len(codes)}, events={len(rows):,}", flush=True)
    expanded = pd.DataFrame(rows)
    expanded.to_pickle(cache_path)
    pd.DataFrame(v8.CATALOG.values()).to_csv(cache_path.with_suffix(".catalog.csv"), index=False, encoding="utf-8-sig")
    return expanded


def main() -> None:
    v8.build_expanded_events = build_expanded_events_compact
    v8.main()


if __name__ == "__main__":
    main()
