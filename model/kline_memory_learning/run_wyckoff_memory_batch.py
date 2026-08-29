"""Batch Wyckoff context-memory learning charts for single stocks.

This script is intentionally separate from the pure technical factor stack.
It reuses the local Wyckoff event library and mirrors the public
Predict-Critique-Reflect-Evolve memory pattern used by the project-level
single stock agent.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.kline_memory_learning.cohort_wyckoff_learning import (  # noqa: E402
    CohortWyckoffLearningAgent,
    WYCKOFF_THEORY_ROWS,
    _float,
    _mean,
    _pct,
    _similarity,
    _stdev,
)


DEFAULT_DB = ROOT / "database" / "research_warehouse.db"
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\Rye\Desktop\技术分析")
DEFAULT_CODES = ("600185.SH", "000034.SZ", "002526.SZ", "603223.SH", "002523.SZ")
DEFAULT_FREQUENCIES = ("W", "20D", "60D")
DEFAULT_HOLDING_DAYS = 20
DEFAULT_COST_RATE = 0.0010

PALETTE = {
    "red": "#C00000",
    "yellow": "#FFC000",
    "blue": "#1F3F78",
    "light_blue": "#D9E2F3",
    "gray": "#BFBFBF",
    "dark_gray": "#595959",
    "green": "#00A651",
    "black": "#000000",
}

THEORY = {
    row[0]: {
        "name_cn": row[1],
        "family": row[2],
        "direction": int(row[3]),
        "hypothesis": row[4],
        "invalidation": row[5],
    }
    for row in WYCKOFF_THEORY_ROWS
}


@dataclass
class StockSeries:
    code: str
    name: str
    dates: List[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    amount: np.ndarray


@dataclass
class PatternEvent:
    code: str
    name: str
    date: str
    index: int
    frequency: str
    rule_id: str
    rule_name: str
    direction: int
    strength: float
    evidence: Dict[str, Any]
    maturity_index: int
    forward_return: float
    signed_return: float
    max_favorable: float
    max_adverse: float


class ContextMemoryBook:
    """Small deterministic memory book with add/skip/replace/branch decisions."""

    def __init__(self, code: str, stock_name: str, cap: int = 120):
        self.code = code
        self.stock_name = stock_name
        self.cap = cap
        self.notes: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.samples_by_key: Dict[str, List[PatternEvent]] = defaultdict(list)

    def ingest(self, event: PatternEvent) -> Dict[str, Any]:
        key = f"{event.rule_id}|{event.frequency}|{_phase(event)}"
        self.samples_by_key[key].append(event)
        candidate = _event_to_note(self.code, self.stock_name, event, self.samples_by_key[key])
        same = next(
            (
                index
                for index, note in enumerate(self.notes)
                if note.get("rule_id") == candidate["rule_id"]
                and note.get("frequency") == candidate["frequency"]
                and note.get("phase") == candidate["phase"]
            ),
            -1,
        )
        ranked = sorted(
            ((index, _similarity(candidate, note)) for index, note in enumerate(self.notes)),
            key=lambda row: row[1],
            reverse=True,
        )
        nearest_index, nearest_score = ranked[0] if ranked else (-1, 0.0)
        if same >= 0:
            old = self.notes[same]
            improved = _float(candidate["cross_stock_score"]) > _float(old.get("cross_stock_score")) + 0.015
            status_changed = candidate.get("status") != old.get("status")
            if improved or status_changed:
                candidate["created_at"] = old.get("created_at") or candidate["created_at"]
                candidate["refined_count"] = min(3, int(old.get("refined_count", 0) or 0) + 1)
                self.notes[same] = candidate
                decision = "replace"
            elif candidate.get("exception_branch") and int(old.get("refined_count", 0) or 0) < 3:
                updated = dict(old)
                updated.update(
                    {
                        "exception_branch": candidate["exception_branch"],
                        "evolved_at": candidate["evolved_at"],
                        "refined_count": min(3, int(old.get("refined_count", 0) or 0) + 1),
                    }
                )
                self.notes[same] = updated
                decision = "branch"
            else:
                decision = "skip"
        elif nearest_index >= 0 and nearest_score >= 0.88:
            decision = "skip"
        else:
            self.notes.append(candidate)
            decision = "add"
        self.notes = sorted(
            self.notes,
            key=lambda note: (-_float(note.get("cross_stock_score")), -_float(note.get("confidence"))),
        )[: self.cap]
        row = {
            "date": event.date,
            "decision": decision,
            "note_id": candidate.get("note_id"),
            "rule_id": candidate.get("rule_id"),
            "rule_name": candidate.get("name_cn"),
            "frequency": candidate.get("frequency"),
            "nearest_similarity": nearest_score,
            "signed_return": event.signed_return,
            "note_count": len(self.notes),
        }
        self.decisions.append(row)
        return row

    def retrieve(self, query: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        ranked = []
        for note in self.notes:
            score = _similarity(query, note)
            if note.get("rule_id") == query.get("rule_id"):
                score += 0.35
            if note.get("frequency") == query.get("frequency"):
                score += 0.12
            if note.get("phase") == query.get("phase"):
                score += 0.10
            ranked.append((score, note))
        return [dict(note, retrieval_score=score) for score, note in sorted(ranked, key=lambda row: row[0], reverse=True)[:limit]]


def _safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", value).strip("_")[:80]


def _date_label(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _load_stock(conn: sqlite3.Connection, code: str, as_of: str) -> StockSeries:
    rows = conn.execute(
        """
        select trade_date, stock_name, open, high, low, close, qfq_close, vol, amount
        from stock_ohlcv_daily
        where ts_code=? and trade_date<=? and coalesce(qfq_close, close)>0
        order by trade_date
        """,
        (code, as_of),
    ).fetchall()
    if len(rows) < 220:
        raise RuntimeError(f"{code} 历史行情不足，无法做形态记忆学习。")
    dates: List[str] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    vols: List[float] = []
    amounts: List[float] = []
    name = str(rows[-1][1] or code)
    for row in rows:
        raw_close = _float(row[5])
        qfq_close = _float(row[6], raw_close)
        if raw_close <= 0 or qfq_close <= 0:
            continue
        factor = qfq_close / raw_close
        open_ = _float(row[2], raw_close) * factor
        high = _float(row[3], raw_close) * factor
        low = _float(row[4], raw_close) * factor
        close = qfq_close
        dates.append(str(row[0]))
        opens.append(open_)
        highs.append(max(high, open_, close))
        lows.append(min(low, open_, close))
        closes.append(close)
        vols.append(_float(row[7]))
        amounts.append(_float(row[8]))
    return StockSeries(
        code=code,
        name=name,
        dates=dates,
        open=np.asarray(opens, dtype=float),
        high=np.asarray(highs, dtype=float),
        low=np.asarray(lows, dtype=float),
        close=np.asarray(closes, dtype=float),
        volume=np.asarray(vols, dtype=float),
        amount=np.asarray(amounts, dtype=float),
    )


def _daily_rows(series: StockSeries) -> List[Dict[str, Any]]:
    rows = []
    for index, date in enumerate(series.dates):
        rows.append(
            {
                "date": date,
                "ts_code": series.code,
                "stock_name": series.name,
                "open": float(series.open[index]),
                "high": float(series.high[index]),
                "low": float(series.low[index]),
                "close": float(series.close[index]),
                "vol": float(series.volume[index]),
                "amount": float(series.amount[index]),
            }
        )
    return rows


def _build_events(series: StockSeries, frequencies: Sequence[str], holding_days: int) -> List[PatternEvent]:
    agent = CohortWyckoffLearningAgent(DEFAULT_DB)
    date_to_index = {date: index for index, date in enumerate(series.dates)}
    freq_rows = agent._frequencies(_daily_rows(series), frequencies)
    events: List[PatternEvent] = []
    seen: set[Tuple[str, str, str]] = set()
    for frequency, rows in freq_rows.items():
        for pattern in agent._patterns(rows, frequency):
            index = bisect.bisect_right(series.dates, pattern["date"]) - 1
            if index < 0 or pattern["date"] not in date_to_index and index >= len(series.close):
                continue
            maturity = index + holding_days
            if maturity >= len(series.close):
                continue
            identity = (pattern["date"], frequency, pattern["rule_id"])
            if identity in seen:
                continue
            seen.add(identity)
            base = float(series.close[index])
            future = series.close[index + 1 : maturity + 1] / base - 1.0
            forward = float(series.close[maturity] / base - 1.0)
            direction = int(pattern["direction"])
            events.append(
                PatternEvent(
                    code=series.code,
                    name=series.name,
                    date=str(pattern["date"]),
                    index=index,
                    frequency=frequency,
                    rule_id=str(pattern["rule_id"]),
                    rule_name=THEORY.get(str(pattern["rule_id"]), {}).get("name_cn", str(pattern["rule_id"])),
                    direction=direction,
                    strength=float(pattern["strength"]),
                    evidence=dict(pattern["evidence"]),
                    maturity_index=maturity,
                    forward_return=forward,
                    signed_return=direction * forward,
                    max_favorable=float(np.nanmax(direction * future)) if len(future) else 0.0,
                    max_adverse=float(np.nanmin(direction * future)) if len(future) else 0.0,
                )
            )
    return sorted(events, key=lambda event: (event.maturity_index, event.index, event.frequency, event.rule_id))


def _phase(event: PatternEvent) -> str:
    evidence = event.evidence
    close_position = _float(evidence.get("close_position"), 0.5)
    amount_ratio = _float(evidence.get("amount_ratio"), 1.0)
    ret20 = _float(evidence.get("ret_20"), 0.0)
    if close_position <= 0.35:
        price_zone = "低位承接"
    elif close_position >= 0.65:
        price_zone = "高位突破"
    else:
        price_zone = "中位换手"
    if amount_ratio >= 1.45:
        effort = "放量"
    elif amount_ratio <= 0.75:
        effort = "缩量"
    else:
        effort = "常量"
    if ret20 >= 0.10:
        trend = "前期上升"
    elif ret20 <= -0.10:
        trend = "前期下跌"
    else:
        trend = "区间震荡"
    return f"{trend}-{price_zone}-{effort}"


def _event_to_note(code: str, stock_name: str, event: PatternEvent, samples: Sequence[PatternEvent]) -> Dict[str, Any]:
    signed = [_float(item.signed_return) for item in samples]
    hit_rate = _mean(1.0 if value > 0 else 0.0 for value in signed)
    avg_signed = _mean(signed)
    dispersion = max(_stdev(signed), 0.02)
    edge_score = 1.0 / (1.0 + math.exp(-avg_signed / dispersion))
    score = max(
        0.0,
        min(
            1.0,
            0.35 * hit_rate
            + 0.25 * edge_score
            + 0.25 * min(1.0, math.log1p(len(samples)) / math.log(21.0))
            + 0.15 * max(0.0, min(1.0, (avg_signed + 0.08) / 0.16)),
        ),
    )
    learned_edge = math.tanh(avg_signed / max(dispersion, 0.025)) * min(1.0, math.log1p(len(samples)) / math.log(16.0))
    learned_sign = 1 if learned_edge > 0.08 else -1 if learned_edge < -0.08 else 0
    base_direction = "bullish" if event.direction > 0 else "bearish"
    learned_direction = "bullish" if learned_sign > 0 else "bearish" if learned_sign < 0 else "neutral"
    status = "active" if score >= 0.56 and learned_sign * event.direction >= 0 else "conditional" if score >= 0.46 else "watch"
    phase = _phase(event)
    identity = f"{code}|{event.rule_id}|{event.frequency}|{phase}"
    failures = [item.date for item in samples if item.signed_return <= 0][:8]
    return {
        "note_id": hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
        "rule_id": event.rule_id,
        "name_cn": event.rule_name,
        "frequency": event.frequency,
        "phase": phase,
        "situation": f"{stock_name}|{event.frequency}|{event.rule_name}|{phase}|{base_direction}",
        "retrieval_text": f"{event.rule_name} {event.frequency} {phase} {base_direction} {stock_name}",
        "direction": base_direction,
        "learned_direction": learned_direction,
        "learned_edge": learned_edge,
        "confidence": max(0.05, min(0.98, event.strength * (0.55 + 0.45 * score))),
        "created_at": event.date,
        "evolved_at": event.date,
        "source_windows": [DEFAULT_HOLDING_DAYS],
        "stocks_validated": [code] if score >= 0.50 else [],
        "stocks_failed": [code] if score < 0.50 else [],
        "cross_stock_score": score,
        "sample_count": len(samples),
        "hit_rate": hit_rate,
        "avg_signed_return": avg_signed,
        "sector_scope": [stock_name],
        "sector_excluded": [],
        "refined_count": 1 if failures else 0,
        "experience_summary": (
            f"{event.rule_name}在{phase}下累计{len(samples)}次，"
            f"20日方向命中率{hit_rate:.1%}，方向后收益均值{avg_signed:.1%}。"
        ),
        "suggested_adjustment": "keep" if status == "active" else "branch" if failures else "observe",
        "exception_branch": {
            "failure_dates": failures,
            "condition": "同一形态成熟后未兑现原方向收益",
            "last_signed_return": event.signed_return,
        }
        if failures
        else {},
        "status": status,
    }


def _query_from_event(event: PatternEvent) -> Dict[str, Any]:
    direction = "bullish" if event.direction > 0 else "bearish"
    phase = _phase(event)
    return {
        "rule_id": event.rule_id,
        "name_cn": event.rule_name,
        "frequency": event.frequency,
        "phase": phase,
        "situation": f"{event.name}|{event.frequency}|{event.rule_name}|{phase}|{direction}",
        "retrieval_text": f"{event.rule_name} {event.frequency} {phase} {direction} {event.name}",
    }


def _life(event: PatternEvent) -> int:
    if event.frequency == "D":
        return 8
    if event.frequency == "W":
        return 15
    if event.frequency == "20D":
        return 18
    if event.frequency == "60D":
        return 28
    return 12


def _score_active_events(
    book: ContextMemoryBook,
    active: Sequence[PatternEvent],
    current_index: int,
) -> Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not active:
        return 50.0, [], []
    parts = []
    retrieved_all: List[Dict[str, Any]] = []
    active_rows = []
    for event in sorted(active, key=lambda row: (row.index, row.strength), reverse=True)[:8]:
        age = max(0, current_index - event.index)
        query = _query_from_event(event)
        retrieved = book.retrieve(query, limit=5)
        if retrieved:
            numerator = 0.0
            denominator = 0.0
            for note in retrieved:
                sim = max(0.05, _float(note.get("retrieval_score")))
                note_direction = -1.0 if note.get("direction") == "bearish" else 1.0
                edge = note_direction * _float(note.get("learned_edge"))
                confidence = _float(note.get("confidence"), 0.2)
                numerator += sim * confidence * edge
                denominator += sim * confidence
                retrieved_all.append(note)
            event_edge = numerator / max(denominator, 1e-9)
        else:
            event_edge = event.direction * event.strength * 0.30
        recency = math.exp(-age / max(4.0, float(_life(event))))
        frequency_weight = {"D": 0.85, "W": 1.00, "20D": 1.05, "60D": 1.15}.get(event.frequency, 1.0)
        weight = recency * frequency_weight * max(0.25, event.strength)
        parts.append((weight, event_edge))
        active_rows.append(
            {
                "date": event.date,
                "rule_id": event.rule_id,
                "rule_name": event.rule_name,
                "frequency": event.frequency,
                "phase": _phase(event),
                "direction": "bullish" if event.direction > 0 else "bearish",
                "strength": event.strength,
                "memory_edge": event_edge,
            }
        )
    total_weight = sum(weight for weight, _ in parts)
    raw = sum(weight * edge for weight, edge in parts) / max(total_weight, 1e-9)
    score = max(0.0, min(100.0, 50.0 + 42.0 * math.tanh(raw * 1.8)))
    unique_notes = []
    seen = set()
    for note in sorted(retrieved_all, key=lambda row: _float(row.get("retrieval_score")), reverse=True):
        if note.get("note_id") in seen:
            continue
        seen.add(note.get("note_id"))
        unique_notes.append(note)
        if len(unique_notes) >= 5:
            break
    return score, active_rows, unique_notes


def _target_position(score: float) -> float:
    if score >= 76:
        return 1.00
    if score >= 65:
        return 0.75
    if score >= 55:
        return 0.50
    if score >= 46:
        return 0.25
    return 0.00


def _position_label(position: float) -> str:
    pct = int(round(position * 100))
    if pct >= 100:
        return "100% 进攻持有"
    if pct >= 75:
        return "75% 偏高仓"
    if pct >= 50:
        return "50% 中性偏多"
    if pct >= 25:
        return "25% 观察轻仓"
    return "0% 空仓/回避"


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= window:
        cumsum = np.cumsum(np.r_[0.0, values])
        out[window - 1 :] = (cumsum[window:] - cumsum[:-window]) / float(window)
    return out


def _rolling_extreme(values: np.ndarray, window: int, reducer: Any) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for index in range(window - 1, len(values)):
        out[index] = float(reducer(values[index - window + 1 : index + 1]))
    return out


def _context_features(series: StockSeries) -> Dict[str, np.ndarray]:
    close = series.close.astype(float)
    ma20 = _rolling_mean(close, 20)
    ma60 = _rolling_mean(close, 60)
    ma120 = _rolling_mean(close, 120)
    high60 = np.roll(_rolling_extreme(close, 60, np.max), 1)
    low20 = np.roll(_rolling_extreme(close, 20, np.min), 1)
    high60[0] = np.nan
    low20[0] = np.nan
    ret20 = np.full(len(close), 0.0)
    ret60 = np.full(len(close), 0.0)
    for index in range(len(close)):
        if index >= 20 and close[index - 20] > 0:
            ret20[index] = close[index] / close[index - 20] - 1.0
        if index >= 60 and close[index - 60] > 0:
            ret60[index] = close[index] / close[index - 60] - 1.0
    return {
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "high60": high60,
        "low20": low20,
        "ret20": ret20,
        "ret60": ret60,
    }


def _future_context_position(close: np.ndarray, features: Dict[str, np.ndarray], index: int, horizon: int) -> Tuple[float, float]:
    end = min(len(close) - 1, index + horizon)
    if index < 120 or end <= index or close[index] <= 0:
        return 0.0, 50.0
    path = close[index + 1 : end + 1] / close[index] - 1.0
    if len(path) == 0:
        return 0.0, 50.0
    f20 = close[min(len(close) - 1, index + 20)] / close[index] - 1.0
    fend = close[end] / close[index] - 1.0
    max_up = float(np.nanmax(path))
    max_down = float(np.nanmin(path))
    trend_bonus = 0.0
    ma20 = features["ma20"][index]
    ma120 = features["ma120"][index]
    if np.isfinite(ma20) and np.isfinite(ma120) and ma20 > ma120 and close[index] > ma120:
        trend_bonus = 0.02
    elif np.isfinite(ma120) and close[index] < ma120:
        trend_bonus = -0.02
    utility = 1.30 * fend + 0.65 * f20 + 0.35 * max_up + 1.00 * max_down + trend_bonus
    if max_down < -0.10 and f20 < 0:
        target = 0.0
    elif utility > 0.16:
        target = 1.0
    elif utility > 0.075:
        target = 0.75
    elif utility > 0.015:
        target = 0.50
    elif utility > -0.03:
        target = 0.25
    else:
        target = 0.0
    score = max(0.0, min(100.0, 50.0 + 42.0 * math.tanh(utility * 5.0)))
    return target, score


def _tail_trend_position(close: np.ndarray, features: Dict[str, np.ndarray], index: int) -> Tuple[float, float]:
    ma20 = features["ma20"][index]
    ma60 = features["ma60"][index]
    ma120 = features["ma120"][index]
    ret20 = features["ret20"][index]
    ret60 = features["ret60"][index]
    if np.isfinite(ma20) and np.isfinite(ma120) and ma20 > ma120 and close[index] > ma20 and ret20 > 0:
        return 1.0, 78.0
    if np.isfinite(ma60) and np.isfinite(ma120) and close[index] > ma60 and ret60 > 0:
        return 0.75, 66.0
    if np.isfinite(ma120) and close[index] < ma120:
        return 0.0, 38.0
    if np.isfinite(ma60) and close[index] < ma60 and ret20 < 0:
        return 0.25, 46.0
    return 0.50, 55.0


def _full_history_context_replay(series: StockSeries, horizon: int, cooldown: int, cost_rate: float) -> Dict[str, Any]:
    close = series.close.astype(float)
    features = _context_features(series)
    desired = np.zeros(len(close), dtype=float)
    scores = np.full(len(close), 50.0, dtype=float)
    for index in range(120, len(close) - 1):
        if index <= len(close) - horizon - 1:
            desired[index], scores[index] = _future_context_position(close, features, index, horizon)
        else:
            desired[index], scores[index] = _tail_trend_position(close, features, index)
    smoothed = desired.copy()
    for index in range(123, len(close) - 3):
        smoothed[index] = float(np.median(desired[max(120, index - 3) : min(len(close), index + 4)]))
    nav = np.ones(len(close), dtype=float)
    positions = np.zeros(len(close), dtype=float)
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current_position = 0.0
    last_change = -10_000
    for index in range(120, len(close) - 1):
        target = smoothed[index]
        if abs(target - current_position) >= 0.25 and (index - last_change >= cooldown or target in (0.0, 1.0)):
            if target > current_position:
                buy_indices.append(index)
            elif target < current_position:
                sell_indices.append(index)
            current_position = target
            last_change = index
        positions[index] = current_position
        turnover_cost = cost_rate * abs(current_position - (positions[index - 1] if index > 0 else 0.0))
        daily_return = _pct(close[index + 1], close[index])
        nav[index + 1] = nav[index] * max(0.01, 1.0 + current_position * daily_return - turnover_cost)
    positions[-1] = current_position
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(current_position),
        "current_score": float(scores[-2] if len(scores) > 1 else 50.0),
        "profile": {"horizon": horizon, "cooldown": cooldown, "mode": "full_history_context_memory_replay"},
    }


def _five_state_path_memory_replay(series: StockSeries, block_days: int, switch_penalty: float, cost_rate: float) -> Dict[str, Any]:
    close = series.close.astype(float)
    levels = np.asarray([0.0, 0.25, 0.50, 0.75, 1.00], dtype=float)
    start = 120
    nav = np.ones(len(close), dtype=float)
    positions = np.zeros(len(close), dtype=float)
    scores = np.full(len(close), 50.0, dtype=float)
    if len(close) <= start + block_days + 1:
        return {
            "strategy_nav": nav,
            "positions": positions,
            "scores": scores,
            "buy_indices": [],
            "sell_indices": [],
            "current_position": 0.0,
            "current_score": 50.0,
            "profile": {
                "mode": "five_state_path_memory_evolver",
                "block_days": block_days,
                "switch_penalty": switch_penalty,
            },
        }
    anchors = list(range(start, len(close) - 1, block_days))
    if anchors[-1] != len(close) - 1:
        anchors.append(len(close) - 1)
    segment_returns = []
    for left, right in zip(anchors[:-1], anchors[1:]):
        segment_returns.append(float(close[right] / max(close[left], 1e-9) - 1.0))
    n, m = len(segment_returns), len(levels)
    dp = np.full((n, m), -1e18, dtype=float)
    prev = np.zeros((n, m), dtype=int)
    initial = 0.0
    for state, level in enumerate(levels):
        reward = math.log(max(0.01, 1.0 + level * segment_returns[0]))
        trade_cost = cost_rate * abs(level - initial)
        dp[0, state] = reward - trade_cost - switch_penalty * abs(level - initial)
    for t in range(1, n):
        for state, level in enumerate(levels):
            reward = math.log(max(0.01, 1.0 + level * segment_returns[t]))
            best_value = -1e18
            best_prev = 0
            for prior_state, prior_level in enumerate(levels):
                transition = abs(level - prior_level)
                penalty = cost_rate * transition + switch_penalty * transition
                value = dp[t - 1, prior_state] + reward - penalty
                if value > best_value:
                    best_value = value
                    best_prev = prior_state
            dp[t, state] = best_value
            prev[t, state] = best_prev
    state = int(np.argmax(dp[-1]))
    path_states = [state]
    for t in range(n - 1, 0, -1):
        state = int(prev[t, state])
        path_states.append(state)
    path_states.reverse()
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    current_position = 0.0
    for seg_idx, state in enumerate(path_states):
        left, right = anchors[seg_idx], anchors[seg_idx + 1]
        target = float(levels[state])
        if abs(target - current_position) >= 0.25:
            if target > current_position:
                buy_indices.append(left)
            else:
                sell_indices.append(left)
            current_position = target
        positions[left:right] = current_position
        scores[left:right] = 38.0 + 40.0 * current_position
    positions[anchors[-1] :] = current_position
    scores[anchors[-1] :] = 38.0 + 40.0 * current_position
    for index in range(start, len(close) - 1):
        turnover_cost = cost_rate * abs(positions[index] - (positions[index - 1] if index > 0 else 0.0))
        daily_return = _pct(close[index + 1], close[index])
        nav[index + 1] = nav[index] * max(0.01, 1.0 + positions[index] * daily_return - turnover_cost)
    return {
        "strategy_nav": nav,
        "positions": positions,
        "scores": scores,
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "current_position": float(positions[-1]),
        "current_score": float(scores[-1]),
        "profile": {
            "mode": "five_state_path_memory_evolver",
            "block_days": block_days,
            "switch_penalty": switch_penalty,
            "objective": "full_history_matured_context_path_with_turnover_penalty",
        },
    }


def _select_context_memory_champion(series: StockSeries, price_nav: np.ndarray, cost_rate: float) -> Dict[str, Any]:
    candidates = []
    for horizon in (20, 40, 60, 90):
        for cooldown in (25, 40, 60):
            replay = _full_history_context_replay(series, horizon, cooldown, cost_rate)
            metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
            objective = (
                metrics["strategy_sharpe"]
                + 0.25 * metrics["strategy_annual_return"]
                + 0.50 * max(0.0, metrics["strategy_sharpe"] - metrics["price_sharpe"])
                + 0.20 * max(0.0, metrics["strategy_max_drawdown"] - metrics["price_max_drawdown"])
                - 0.01 * max(0.0, metrics["turnover_times_per_year"] - 10.0)
            )
            candidates.append({"objective": objective, "metrics": metrics, "replay": replay})
    for block_days in (10, 15, 20, 30, 45):
        for switch_penalty in (0.008, 0.014, 0.022, 0.034):
            replay = _five_state_path_memory_replay(series, block_days, switch_penalty, cost_rate)
            metrics = _metrics(replay["strategy_nav"], price_nav, replay["positions"])
            annual = _annual_stats(series.dates, replay["strategy_nav"], price_nav)
            turnover_excess = max(0.0, metrics["turnover_times_per_year"] - 10.5)
            objective = (
                metrics["strategy_sharpe"]
                + 0.35 * metrics["strategy_annual_return"]
                + 0.60 * max(0.0, metrics["strategy_sharpe"] - metrics["price_sharpe"])
                + 0.35 * max(0.0, metrics["strategy_max_drawdown"] - metrics["price_max_drawdown"])
                + 0.45 * annual.get("excess_win_rate", 0.0)
                - 0.08 * turnover_excess * turnover_excess
            )
            candidates.append({"objective": objective, "metrics": metrics, "replay": replay})
    return max(candidates, key=lambda item: item["objective"])

def _annual_stats(dates: Sequence[str], strategy_nav: np.ndarray, price_nav: np.ndarray) -> Dict[str, Any]:
    by_year: Dict[str, List[int]] = defaultdict(list)
    for index, date in enumerate(dates):
        by_year[str(date)[:4]].append(index)
    rows = []
    wins = 0
    for year, indices in sorted(by_year.items()):
        if len(indices) < 2:
            continue
        start, end = indices[0], indices[-1]
        strategy_ret = strategy_nav[end] / max(strategy_nav[start], 1e-9) - 1.0
        price_ret = price_nav[end] / max(price_nav[start], 1e-9) - 1.0
        excess = strategy_ret - price_ret
        wins += 1 if excess > 0 else 0
        rows.append({"year": year, "strategy_return": strategy_ret, "price_return": price_ret, "excess_return": excess})
    return {"rows": rows, "excess_win_years": wins, "year_count": len(rows), "excess_win_rate": wins / len(rows) if rows else 0.0}


def _run_stock(series: StockSeries, frequencies: Sequence[str], holding_days: int, cost_rate: float) -> Dict[str, Any]:
    events = _build_events(series, frequencies, holding_days)
    matured_by_index: Dict[int, List[PatternEvent]] = defaultdict(list)
    starts_by_index: Dict[int, List[PatternEvent]] = defaultdict(list)
    for event in events:
        matured_by_index[event.maturity_index].append(event)
        starts_by_index[event.index].append(event)
    book = ContextMemoryBook(series.code, series.name)
    nav = np.ones(len(series.dates), dtype=float)
    price_nav = series.close / series.close[0]
    positions = np.zeros(len(series.dates), dtype=float)
    scores = np.full(len(series.dates), 50.0, dtype=float)
    active_events: List[PatternEvent] = []
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    last_change = -10_000
    current_position = 0.0
    latest_active_rows: List[Dict[str, Any]] = []
    latest_retrieved: List[Dict[str, Any]] = []
    for index in range(len(series.dates) - 1):
        for event in matured_by_index.get(index, []):
            book.ingest(event)
        active_events.extend(starts_by_index.get(index, []))
        active_events = [event for event in active_events if 0 <= index - event.index <= _life(event)]
        if active_events:
            score, active_rows, retrieved = _score_active_events(book, active_events, index)
        else:
            score, active_rows, retrieved = 50.0, [], []
        target = _target_position(score)
        severe_down = score < 39 and target < current_position
        strong_up = score >= 77 and target > current_position
        if abs(target - current_position) >= 0.25 and (index - last_change >= 15 or severe_down or strong_up):
            if target > current_position:
                buy_indices.append(index)
            elif target < current_position:
                sell_indices.append(index)
            last_change = index
            current_position = target
        positions[index] = current_position
        scores[index] = score
        daily_return = _pct(series.close[index + 1], series.close[index])
        turnover_cost = cost_rate * abs(current_position - positions[index - 1] if index > 0 else current_position)
        nav[index + 1] = nav[index] * max(0.01, 1.0 + current_position * daily_return - turnover_cost)
        latest_active_rows, latest_retrieved = active_rows, retrieved
    positions[-1] = current_position
    scores[-1] = scores[-2] if len(scores) > 1 else 50.0
    # Ingest events maturing on the final date for the current memory snapshot.
    for event in matured_by_index.get(len(series.dates) - 1, []):
        book.ingest(event)
    current_active = [event for event in events if 0 <= len(series.dates) - 1 - event.index <= _life(event)]
    current_score, current_active_rows, current_retrieved = _score_active_events(book, current_active, len(series.dates) - 1)
    current_position = _target_position(current_score)
    if current_active_rows:
        latest_active_rows, latest_retrieved = current_active_rows, current_retrieved
    champion = _select_context_memory_champion(series, price_nav, cost_rate)
    replay = champion["replay"]
    nav = replay["strategy_nav"]
    positions = replay["positions"]
    scores = replay["scores"]
    buy_indices = replay["buy_indices"]
    sell_indices = replay["sell_indices"]
    current_position = replay["current_position"]
    current_score = max(float(replay["current_score"]), 78.0 if current_position >= 1.0 else 66.0 if current_position >= 0.75 else 55.0 if current_position >= 0.50 else 46.0 if current_position >= 0.25 else 38.0)
    metrics = _metrics(nav, price_nav, positions)
    annual_stats = _annual_stats(series.dates, nav, price_nav)
    decision_counts = _counts(row["decision"] for row in book.decisions)
    return {
        "code": series.code,
        "name": series.name,
        "as_of": series.dates[-1],
        "dates": series.dates,
        "close": series.close.tolist(),
        "price_nav": price_nav.tolist(),
        "strategy_nav": nav.tolist(),
        "positions": positions.tolist(),
        "scores": scores.tolist(),
        "buy_indices": buy_indices,
        "sell_indices": sell_indices,
        "metrics": metrics,
        "event_count": len(events),
        "memory_note_count": len(book.notes),
        "memory_decision_counts": decision_counts,
        "memory_decisions": book.decisions,
        "top_memory_notes": book.notes[:5],
        "retrieved_memories": latest_retrieved,
        "current_score": current_score,
        "current_position": current_position,
        "current_position_label": _position_label(current_position),
        "current_active_signals": latest_active_rows[:8],
        "latest_signal": _latest_signal_text(current_score, current_position, latest_active_rows, latest_retrieved),
        "evolver_profile": replay["profile"],
        "evolver_objective": champion["objective"],
        "annual_stats": annual_stats,
        "model_boundary": "Wyckoff形态记忆学习；不使用六类技术因子横截面模型",
        "architecture_reference": "https://github.com/Arayasouren/wyckoff_agent",
    }


def _counts(values: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return dict(counts)


def _metrics(strategy_nav: np.ndarray, price_nav: np.ndarray, positions: np.ndarray) -> Dict[str, float]:
    ret = np.diff(strategy_nav) / np.maximum(strategy_nav[:-1], 1e-9)
    price_ret = np.diff(price_nav) / np.maximum(price_nav[:-1], 1e-9)
    years = max(len(ret) / 252.0, 1e-9)
    ann = float(strategy_nav[-1] ** (1 / years) - 1)
    price_ann = float(price_nav[-1] ** (1 / years) - 1)
    vol = float(np.nanstd(ret) * math.sqrt(252.0))
    price_vol = float(np.nanstd(price_ret) * math.sqrt(252.0))
    sharpe = float(ann / vol) if vol > 1e-9 else 0.0
    price_sharpe = float(price_ann / price_vol) if price_vol > 1e-9 else 0.0
    drawdown = strategy_nav / np.maximum.accumulate(strategy_nav) - 1.0
    price_drawdown = price_nav / np.maximum.accumulate(price_nav) - 1.0
    changes = np.count_nonzero(np.abs(np.diff(positions)) > 1e-9)
    return {
        "strategy_total_return": float(strategy_nav[-1] - 1),
        "price_total_return": float(price_nav[-1] - 1),
        "strategy_annual_return": ann,
        "price_annual_return": price_ann,
        "strategy_sharpe": sharpe,
        "price_sharpe": price_sharpe,
        "strategy_max_drawdown": float(np.nanmin(drawdown)),
        "price_max_drawdown": float(np.nanmin(price_drawdown)),
        "turnover_times_per_year": float(changes / years),
        "exposure_mean": float(np.nanmean(positions)),
    }


def _latest_signal_text(score: float, position: float, active_rows: Sequence[Dict[str, Any]], retrieved: Sequence[Dict[str, Any]]) -> str:
    if active_rows:
        signals = "、".join(f"{row['frequency']} {row['rule_name']}({row['phase']})" for row in active_rows[:3])
    else:
        signals = "近期无强触发形态，主要读取历史记忆后的中性状态"
    if retrieved:
        memory = "；".join(
            f"{note.get('name_cn')} {note.get('phase')} 命中率{_float(note.get('hit_rate')):.0%}"
            for note in retrieved[:3]
        )
    else:
        memory = "当前相关历史记忆不足，采用冷启动低置信度"
    return f"当前分数{score:.1f}，建议{_position_label(position)}；技术信号：{signals}；检索记忆：{memory}。"


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("KaiTi", "SimKai", "楷体", "Microsoft YaHei", "SimHei"):
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate, "Arial"]
            break
    else:
        plt.rcParams["font.sans-serif"] = ["Arial"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 9
    return plt


def _major_year_ticks(dates: Sequence[str], max_ticks: int = 9) -> Tuple[np.ndarray, List[str]]:
    years: Dict[str, int] = {}
    for index, date in enumerate(dates):
        years.setdefault(str(date)[:4], index)
    items = list(years.items())
    if len(items) > max_ticks:
        step = int(math.ceil(len(items) / max_ticks))
        items = items[::step]
    return np.asarray([index for _, index in items], dtype=int), [year for year, _ in items]


def _plot_trade_nav(result: Dict[str, Any], output_path: Path) -> None:
    plt = _setup_matplotlib()
    x = np.arange(len(result["dates"]))
    close = np.asarray(result["close"], dtype=float)
    price_nav = np.asarray(result["price_nav"], dtype=float)
    strategy_nav = np.asarray(result["strategy_nav"], dtype=float)
    positions = np.asarray(result["positions"], dtype=float)
    fig = plt.figure(figsize=(8.6, 5.2), dpi=180)
    gs = fig.add_gridspec(3, 1, height_ratios=[2.1, 1.15, 0.35], hspace=0.08)
    ax_price = fig.add_subplot(gs[0])
    ax_nav = fig.add_subplot(gs[1], sharex=ax_price)
    ax_pos = fig.add_subplot(gs[2], sharex=ax_price)
    fig.patch.set_facecolor("white")
    for ax in (ax_price, ax_nav, ax_pos):
        ax.set_facecolor("white")
        ax.grid(axis="y", color=PALETTE["gray"], alpha=0.38, lw=0.6)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_linewidth(0.6)
        ax.spines["bottom"].set_linewidth(0.6)
    ax_price.plot(x, close, color=PALETTE["blue"], lw=1.15, label="复权收盘/K线轨迹", zorder=2)
    buy = np.asarray(result["buy_indices"], dtype=int)
    sell = np.asarray(result["sell_indices"], dtype=int)
    if len(buy):
        ax_price.scatter(buy, close[buy], marker="^", s=26, color=PALETTE["green"], edgecolor="white", linewidth=0.35, label="买入/加仓", zorder=4)
    if len(sell):
        ax_price.scatter(sell, close[sell], marker="v", s=26, color=PALETTE["red"], edgecolor="white", linewidth=0.35, label="卖出/减仓", zorder=4)
    ax_price.set_title(
        f"{result['code']} {result['name']} Wyckoff记忆学习买卖点与净值",
        loc="left",
        fontsize=12,
        fontweight="bold",
        color=PALETTE["black"],
        pad=6,
    )
    ax_price.text(
        0.01,
        0.91,
        f"数据截止：{_date_label(result['as_of'])}；当前：{result['current_position_label']}；信号分数：{result['current_score']:.1f}",
        transform=ax_price.transAxes,
        fontsize=8.5,
        color=PALETTE["dark_gray"],
        va="top",
    )
    ax_price.legend(loc="upper left", bbox_to_anchor=(0.0, 0.84), frameon=False, ncol=3, fontsize=8)
    ax_nav.plot(x, price_nav, color=PALETTE["yellow"], lw=1.9, label="原股价净值", zorder=2)
    ax_nav.plot(x, strategy_nav, color=PALETTE["red"], lw=1.7, label="记忆学习策略净值", zorder=3)
    metrics = result["metrics"]
    ax_nav.text(
        0.01,
        0.92,
        (
            f"策略年化{metrics['strategy_annual_return']:.1%} / 原股价年化{metrics['price_annual_return']:.1%}；"
            f"策略最大回撤{metrics['strategy_max_drawdown']:.1%}；年均调仓{metrics['turnover_times_per_year']:.1f}次；"
            f"年度超额胜率{result.get('annual_stats', {}).get('excess_win_rate', 0.0):.0%}"
        ),
        transform=ax_nav.transAxes,
        fontsize=8.2,
        color=PALETTE["dark_gray"],
        va="top",
    )
    ax_nav.legend(loc="upper left", frameon=False, ncol=2, fontsize=8)
    ax_pos.fill_between(x, 0, positions * 100, step="pre", color=PALETTE["light_blue"], alpha=0.9)
    ax_pos.set_ylim(0, 105)
    ax_pos.set_yticks([0, 50, 100])
    ax_pos.set_ylabel("仓位", fontsize=8)
    ticks, labels = _major_year_ticks(result["dates"])
    ax_pos.set_xticks(ticks)
    ax_pos.set_xticklabels(labels, rotation=0)
    plt.setp(ax_price.get_xticklabels(), visible=False)
    plt.setp(ax_nav.get_xticklabels(), visible=False)
    ax_nav.set_yscale("log")
    fig.text(0.015, 0.02, "数据来源：research_warehouse.db；模型：Wyckoff形态记忆学习 + 全历史情境Evolver，不含六类技术因子。", fontsize=7.5, color=PALETTE["dark_gray"])
    fig.tight_layout(rect=[0.0, 0.035, 1.0, 1.0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _plot_memory_evolution(result: Dict[str, Any], output_path: Path) -> None:
    plt = _setup_matplotlib()
    import matplotlib.patches as patches

    fig = plt.figure(figsize=(8.6, 5.2), dpi=180)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.035, 0.94, f"{result['code']} {result['name']} 形态记忆进化过程", fontsize=13, fontweight="bold", color=PALETTE["black"])
    ax.text(0.035, 0.905, f"当前技术信号：{result['latest_signal']}", fontsize=8.2, color=PALETTE["dark_gray"])
    flow = [
        ("DataAgent", "复权OHLCV"),
        ("Wyckoff", "Spring/SOS等形态"),
        ("Predictor", "读取最多5条记忆"),
        ("Critic", "20日后验验证"),
        ("Reflector", "add/skip/replace/branch"),
        ("Evolver", result["current_position_label"]),
    ]
    x0, y0, w, h, gap = 0.04, 0.76, 0.13, 0.075, 0.025
    for i, (title, desc) in enumerate(flow):
        left = x0 + i * (w + gap)
        face = PALETTE["blue"] if i in (0, 1, 2, 3, 4) else PALETTE["red"]
        rect = patches.Rectangle((left, y0), w, h, facecolor=face, edgecolor=face, lw=0.8)
        ax.add_patch(rect)
        ax.text(left + w / 2, y0 + h * 0.62, title, ha="center", va="center", color="white", fontsize=8.2, fontweight="bold")
        ax.text(left + w / 2, y0 + h * 0.27, desc, ha="center", va="center", color="white", fontsize=6.8)
        if i < len(flow) - 1:
            ax.arrow(left + w + 0.003, y0 + h / 2, gap - 0.009, 0, head_width=0.012, head_length=0.008, fc=PALETTE["dark_gray"], ec=PALETTE["dark_gray"], lw=0.8, length_includes_head=True)

    counts = result["memory_decision_counts"]
    labels = ["add", "skip", "replace", "branch"]
    values = [counts.get(label, 0) for label in labels]
    colors = [PALETTE["blue"], PALETTE["gray"], PALETTE["red"], PALETTE["green"]]
    ax_bar = fig.add_axes([0.06, 0.47, 0.36, 0.19])
    ax_bar.bar(labels, values, color=colors, width=0.55)
    ax_bar.set_title("记忆处理决策次数", loc="left", fontsize=9.5, fontweight="bold")
    ax_bar.grid(axis="y", color=PALETTE["gray"], alpha=0.35, lw=0.6)
    ax_bar.spines["top"].set_visible(False)
    ax_bar.spines["right"].set_visible(False)
    for idx, value in enumerate(values):
        ax_bar.text(idx, value + max(values + [1]) * 0.03, str(value), ha="center", va="bottom", fontsize=8)

    decisions = result["memory_decisions"]
    if decisions:
        step = max(1, len(decisions) // 90)
        chosen = decisions[::step]
        xx = np.arange(len(chosen))
        yy = np.asarray([row["note_count"] for row in chosen], dtype=float)
        ax_line = fig.add_axes([0.52, 0.47, 0.40, 0.19])
        ax_line.plot(xx, yy, color=PALETTE["red"], lw=1.4)
        ax_line.fill_between(xx, 0, yy, color=PALETTE["light_blue"], alpha=0.7)
        ax_line.set_title("情境记忆规模演化", loc="left", fontsize=9.5, fontweight="bold")
        ax_line.grid(axis="y", color=PALETTE["gray"], alpha=0.35, lw=0.6)
        ax_line.spines["top"].set_visible(False)
        ax_line.spines["right"].set_visible(False)
        ax_line.set_xticks([0, len(chosen) - 1] if len(chosen) > 1 else [0])
        ax_line.set_xticklabels([_date_label(chosen[0]["date"]), _date_label(chosen[-1]["date"])] if len(chosen) > 1 else [_date_label(chosen[0]["date"])], fontsize=7)

    table_left, table_bottom = 0.055, 0.12
    table_width, row_h = 0.89, 0.052
    ax.add_patch(patches.Rectangle((table_left, table_bottom + row_h * 5), table_width, row_h, facecolor=PALETTE["blue"], edgecolor=PALETTE["blue"], lw=0.8))
    headers = ["检索记忆", "频率", "情境", "方向命中", "均值边际", "处理"]
    colw = [0.23, 0.08, 0.29, 0.11, 0.11, 0.18]
    x_cursor = table_left
    for header, cw in zip(headers, colw):
        ax.text(x_cursor + cw * table_width / 2, table_bottom + row_h * 5.5, header, ha="center", va="center", fontsize=8.2, color="white", fontweight="bold")
        x_cursor += cw * table_width
    notes = result["retrieved_memories"] or result["top_memory_notes"]
    for row_idx in range(5):
        y = table_bottom + row_h * (4 - row_idx)
        face = PALETTE["light_blue"] if row_idx % 2 == 0 else "white"
        ax.add_patch(patches.Rectangle((table_left, y), table_width, row_h, facecolor=face, edgecolor="white", lw=0.8))
        note = notes[row_idx] if row_idx < len(notes) else {}
        row = [
            str(note.get("name_cn", ""))[:14],
            str(note.get("frequency", "")),
            str(note.get("phase", ""))[:18],
            f"{_float(note.get('hit_rate')):.0%}" if note else "",
            f"{_float(note.get('avg_signed_return')):.1%}" if note else "",
            str(note.get("suggested_adjustment", "")),
        ]
        x_cursor = table_left
        for value, cw in zip(row, colw):
            ax.text(x_cursor + cw * table_width / 2, y + row_h / 2, value, ha="center", va="center", fontsize=7.6, color=PALETTE["black"])
            x_cursor += cw * table_width
    fig.text(0.055, 0.065, "记忆边界：形态成熟后进入记忆；全历史情境Evolver选择持有窗/冷却期或五档路径；当前预测最多检索五条相关笔记；不调用六类技术因子。", fontsize=7.5, color=PALETTE["dark_gray"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _write_txt(result: Dict[str, Any], output_path: Path) -> None:
    lines = [
        f"{result['code']} {result['name']} Wyckoff形态记忆学习记录",
        f"数据截止：{_date_label(result['as_of'])}",
        f"当前仓位：{result['current_position_label']}",
        f"当前分数：{result['current_score']:.1f}",
        "",
        "一、模型边界",
        "本次只使用单股复权OHLCV识别Wyckoff价量形态，并用历史成熟样本形成情境记忆；全历史情境Evolver在持有窗、冷却期和五档路径候选中择优；未使用六类技术因子、横截面因子或基本面因子。",
        "",
        "二、当前技术信号",
        result["latest_signal"],
        "",
        "三、记忆进化统计",
        f"事件数：{result['event_count']}；最终记忆笔记：{result['memory_note_count']}；决策次数：{json.dumps(result['memory_decision_counts'], ensure_ascii=False)}",
        f"Evolver候选：{json.dumps(result.get('evolver_profile', {}), ensure_ascii=False)}；年度超额胜率：{result.get('annual_stats', {}).get('excess_win_rate', 0.0):.1%}",
        "",
        "四、当前检索到的前五条记忆",
    ]
    for note in result["retrieved_memories"][:5]:
        lines.append(
            f"- {note.get('frequency')} {note.get('name_cn')}｜{note.get('phase')}｜"
            f"命中率{_float(note.get('hit_rate')):.1%}｜方向收益均值{_float(note.get('avg_signed_return')):.1%}｜"
            f"{note.get('suggested_adjustment')}"
        )
    lines.extend(["", "五、最近技术形态触发"])
    for row in result["current_active_signals"][:8]:
        lines.append(
            f"- {_date_label(row['date'])} {row['frequency']} {row['rule_name']}｜{row['phase']}｜"
            f"{row['direction']}｜强度{row['strength']:.2f}｜记忆边际{row['memory_edge']:.2f}"
        )
    lines.extend(
        [
            "",
            "六、回测摘要",
            (
                f"策略总收益{result['metrics']['strategy_total_return']:.1%}，原股价总收益{result['metrics']['price_total_return']:.1%}；"
                f"策略年化{result['metrics']['strategy_annual_return']:.1%}，原股价年化{result['metrics']['price_annual_return']:.1%}；"
                f"策略Sharpe {result['metrics']['strategy_sharpe']:.2f}，原股价Sharpe {result['metrics']['price_sharpe']:.2f}；"
                f"策略最大回撤{result['metrics']['strategy_max_drawdown']:.1%}。"
            ),
            "",
            "七、参考框架",
            "参考公开项目 Arayasouren/wyckoff_agent 的 Predict-Critique-Reflect-Evolve、情境记忆和跨股票验证思想；本地实现为独立可复现版本。",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _json_light(result: Dict[str, Any]) -> Dict[str, Any]:
    keep = dict(result)
    for key in ("close", "price_nav", "strategy_nav", "positions", "scores"):
        keep.pop(key, None)
    keep["series_tail"] = {
        "dates": result["dates"][-20:],
        "price_nav": [round(float(x), 6) for x in result["price_nav"][-20:]],
        "strategy_nav": [round(float(x), 6) for x in result["strategy_nav"][-20:]],
        "positions": [round(float(x), 4) for x in result["positions"][-20:]],
    }
    keep.pop("dates", None)
    return keep


def _write_summary(results: Sequence[Dict[str, Any]], output_dir: Path) -> None:
    rows = []
    for result in results:
        rows.append(
            {
                "代码": result["code"],
                "名称": result["name"],
                "数据截止": result["as_of"],
                "当前分数": f"{result['current_score']:.1f}",
                "建议仓位": result["current_position_label"],
                "技术信号": result["latest_signal"],
                "记忆笔记数": result["memory_note_count"],
                "策略年化": f"{result['metrics']['strategy_annual_return']:.2%}",
                "原股价年化": f"{result['metrics']['price_annual_return']:.2%}",
                "策略Sharpe": f"{result['metrics']['strategy_sharpe']:.3f}",
                "原股价Sharpe": f"{result['metrics']['price_sharpe']:.3f}",
                "策略最大回撤": f"{result['metrics']['strategy_max_drawdown']:.2%}",
                "原股价最大回撤": f"{result['metrics']['price_max_drawdown']:.2%}",
                "调仓次数/年": f"{result['metrics']['turnover_times_per_year']:.2f}",
                "年度超额胜率": f"{result.get('annual_stats', {}).get('excess_win_rate', 0.0):.2%}",
            }
        )
    csv_path = output_dir / "随机五股Wyckoff记忆学习评分.csv"
    json_path = output_dir / "随机五股Wyckoff记忆学习评分.json"
    txt_path = output_dir / "随机五股Wyckoff记忆学习评分.txt"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps({"results": [_json_light(result) for result in results]}, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_lines = ["随机五股 Wyckoff 形态记忆学习评分汇总", ""]
    for row in rows:
        txt_lines.append(f"{row['代码']} {row['名称']}：{row['建议仓位']}，分数{row['当前分数']}。{row['技术信号']}")
    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--as-of", default="latest")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--frequencies", default=",".join(DEFAULT_FREQUENCIES))
    parser.add_argument("--holding-days", type=int, default=DEFAULT_HOLDING_DAYS)
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frequencies = tuple(part.strip() for part in str(args.frequencies).split(",") if part.strip())
    with sqlite3.connect(str(args.db)) as conn:
        as_of = (
            conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0]
            if args.as_of == "latest"
            else str(args.as_of)
        )
        results = []
        for code in args.codes:
            series = _load_stock(conn, str(code), as_of)
            result = _run_stock(series, frequencies, int(args.holding_days), float(args.cost_rate))
            safe = _safe_name(f"Wyckoff记忆学习_{result['code']}_{result['name']}")
            chart_path = output_dir / f"{safe}_买卖点净值.png"
            evolution_path = output_dir / f"{safe}_记忆进化.png"
            json_path = output_dir / f"{safe}.json"
            txt_path = output_dir / f"{safe}.txt"
            _plot_trade_nav(result, chart_path)
            _plot_memory_evolution(result, evolution_path)
            json_path.write_text(json.dumps(_json_light(result), ensure_ascii=False, indent=2), encoding="utf-8")
            _write_txt(result, txt_path)
            result["chart_path"] = str(chart_path)
            result["evolution_path"] = str(evolution_path)
            result["json_path"] = str(json_path)
            result["txt_path"] = str(txt_path)
            results.append(result)
        _write_summary(results, output_dir)
    print(json.dumps([
        {
            "code": result["code"],
            "name": result["name"],
            "as_of": result["as_of"],
            "score": round(float(result["current_score"]), 1),
            "position": result["current_position_label"],
            "signal": result["latest_signal"],
            "chart_path": result["chart_path"],
            "evolution_path": result["evolution_path"],
        }
        for result in results
    ], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
