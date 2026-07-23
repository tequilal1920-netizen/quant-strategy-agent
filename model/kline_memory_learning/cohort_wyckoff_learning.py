from __future__ import annotations

import bisect
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


COHORT_MODES = ("hybrid", "industry", "style", "individual")
WYCKOFF_PREFIX = "KLINE_WYCKOFF_"
WYCKOFF_THEORY_ROWS: Tuple[Tuple[str, str, str, int, str, str], ...] = (
    ("KLINE_WYCKOFF_SPRING_BULL", "\u5a01\u79d1\u592b\u6625\u5929\u6d4b\u8bd5", "wyckoff", 1,
     "\u8dcc\u7834\u56e0\u679c\u652f\u6491\u540e\u5f53\u671f\u6536\u56de\uff0c\u4e0b\u65b9\u629b\u538b\u88ab\u627f\u63a5",
     "\u518d\u6b21\u6709\u6548\u8dcc\u7834\u6d4b\u8bd5\u4f4e\u70b9\u4e14\u653e\u91cf\u5ef6\u7eed"),
    ("KLINE_WYCKOFF_UPTHRUST_BEAR", "\u5a01\u79d1\u592b\u4e0a\u51b2\u56de\u843d", "wyckoff", -1,
     "\u7a81\u7834\u56e0\u679c\u963b\u529b\u540e\u5f53\u671f\u8dcc\u56de\uff0c\u4e0a\u65b9\u4f9b\u7ed9\u91cd\u65b0\u5360\u4f18",
     "\u653e\u91cf\u6536\u590d\u4e0a\u51b2\u9ad8\u70b9\u5e76\u6301\u7eed\u7ad9\u7a33"),
    ("KLINE_WYCKOFF_SOS_BULL", "\u5a01\u79d1\u592b\u5f3a\u52bf\u4fe1\u53f7", "wyckoff", 1,
     "\u5bbd\u5e45\u4e0a\u6da8\u7a81\u7834\u5386\u53f2\u963b\u529b\uff0c\u4ef7\u5dee\u4e0e\u6210\u4ea4\u52aa\u529b\u540c\u5411",
     "\u8dcc\u56de\u7a81\u7834\u533a\u4e14\u65e0\u6cd5\u5feb\u901f\u6536\u590d"),
    ("KLINE_WYCKOFF_SOW_BEAR", "\u5a01\u79d1\u592b\u5f31\u52bf\u4fe1\u53f7", "wyckoff", -1,
     "\u5bbd\u5e45\u4e0b\u8dcc\u8dcc\u7834\u5386\u53f2\u652f\u6491\uff0c\u4ef7\u5dee\u4e0e\u6210\u4ea4\u52aa\u529b\u540c\u5411",
     "\u6536\u590d\u8dcc\u7834\u533a\u4e14\u7ee7\u7eed\u5411\u4e0a\u6269\u5c55"),
    ("KLINE_WYCKOFF_LPS_BULL", "\u5a01\u79d1\u592b\u6700\u540e\u652f\u6491\u70b9", "wyckoff", 1,
     "\u5f3a\u52bf\u7a81\u7834\u540e\u7f29\u91cf\u56de\u6d4b\u4f46\u4e0d\u7834\u5173\u952e\u652f\u6491",
     "\u56de\u6d4b\u8dcc\u7834\u7ed3\u6784\u652f\u6491\u6216\u6210\u4ea4\u91cf\u9006\u52bf\u6269\u5f20"),
    ("KLINE_WYCKOFF_LPSY_BEAR", "\u5a01\u79d1\u592b\u6700\u540e\u4f9b\u7ed9\u70b9", "wyckoff", -1,
     "\u5f31\u52bf\u8dcc\u7834\u540e\u7f29\u91cf\u53cd\u62bd\u4f46\u65e0\u6cd5\u6536\u590d\u5173\u952e\u963b\u529b",
     "\u53cd\u62bd\u6536\u590d\u7ed3\u6784\u963b\u529b\u5e76\u653e\u91cf\u5ef6\u7eed"),
    ("KLINE_WYCKOFF_SELLING_CLIMAX_BULL", "\u5a01\u79d1\u592b\u5356\u51fa\u9ad8\u6f6e", "wyckoff", 1,
     "\u8fde\u7eed\u4e0b\u8dcc\u540e\u4ef7\u5dee\u548c\u6210\u4ea4\u52aa\u529b\u6781\u7aef\u6269\u5f20\uff0c\u6536\u76d8\u8131\u79bb\u6700\u4f4e\u70b9",
     "\u672a\u51fa\u73b0\u81ea\u52a8\u53cd\u5f39\u4e14\u7ee7\u7eed\u653e\u91cf\u521b\u65b0\u4f4e"),
    ("KLINE_WYCKOFF_BUYING_CLIMAX_BEAR", "\u5a01\u79d1\u592b\u4e70\u5165\u9ad8\u6f6e", "wyckoff", -1,
     "\u8fde\u7eed\u4e0a\u6da8\u540e\u4ef7\u5dee\u548c\u6210\u4ea4\u52aa\u529b\u6781\u7aef\u6269\u5f20\uff0c\u6536\u76d8\u8131\u79bb\u6700\u9ad8\u70b9",
     "\u7f29\u91cf\u6574\u7406\u540e\u7ee7\u7eed\u6709\u6548\u521b\u65b0\u9ad8"),
    ("KLINE_WYCKOFF_ACCUMULATION_BULL", "\u5a01\u79d1\u592b\u5438\u7b79\u7ed3\u6784", "wyckoff", 1,
     "\u4e0b\u8dcc\u540e\u8fdb\u5165\u6ce2\u52a8\u6536\u7f29\u533a\u95f4\uff0c\u652f\u6491\u6709\u6548\u4e14\u6536\u76d8\u4e2d\u5fc3\u4e0a\u79fb",
     "\u533a\u95f4\u4e0b\u6cbf\u653e\u91cf\u8dcc\u7834\u5e76\u6301\u7eed\u4e0b\u79fb"),
    ("KLINE_WYCKOFF_DISTRIBUTION_BEAR", "\u5a01\u79d1\u592b\u6d3e\u53d1\u7ed3\u6784", "wyckoff", -1,
     "\u4e0a\u6da8\u540e\u8fdb\u5165\u6ce2\u52a8\u6536\u7f29\u533a\u95f4\uff0c\u963b\u529b\u6709\u6548\u4e14\u6536\u76d8\u4e2d\u5fc3\u4e0b\u79fb",
     "\u533a\u95f4\u4e0a\u6cbf\u653e\u91cf\u7a81\u7834\u5e76\u6301\u7eed\u4e0a\u79fb"),
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _mean(values: Iterable[float]) -> float:
    rows = [_float(value) for value in values]
    return statistics.fmean(rows) if rows else 0.0


def _stdev(values: Sequence[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _pct(current: float, previous: float) -> float:
    return current / previous - 1.0 if previous > 0 else 0.0


def _quantile(values: Sequence[float], q: float) -> float:
    rows = sorted(_float(value) for value in values)
    if not rows:
        return 0.0
    position = max(0.0, min(1.0, q)) * (len(rows) - 1)
    low, high = int(math.floor(position)), int(math.ceil(position))
    weight = position - low
    return rows[low] * (1.0 - weight) + rows[high] * weight


def _value(row: Any, field: str) -> float:
    return _float(row.get(field) if isinstance(row, dict) else getattr(row, field, 0.0))


def _board(code: str) -> str:
    code = str(code).upper()
    stem = code.split(".", 1)[0]
    if stem.startswith(("300", "301")):
        return "chinext"
    if stem.startswith(("688", "689")):
        return "star"
    if code.endswith(".BJ") or stem.startswith(("4", "8")):
        return "beijing"
    return "sh_main" if code.endswith(".SH") else "sz_main"


def _style_stats(rows: Sequence[Any]) -> Dict[str, float]:
    closes = [(_value(row, "qfq_close") or _value(row, "close")) for row in rows]
    amounts = [_value(row, "amount") for row in rows]
    returns = [_pct(closes[index], closes[index - 1]) for index in range(1, len(closes))]
    trend_window = min(120, max(2, len(closes) - 1))
    return {
        "amount": _mean(amounts[-min(252, len(amounts)):]),
        "volatility": _stdev(returns[-min(60, len(returns)):]) * math.sqrt(252.0),
        "trend": _pct(closes[-1], closes[-1 - trend_window]) if len(closes) > trend_window else 0.0,
        "age": float(len(rows)),
    } if rows else {"amount": 0.0, "volatility": 0.0, "trend": 0.0, "age": 0.0}


def _style_labels(stats: Dict[str, float], breaks: Tuple[float, float]) -> Dict[str, str]:
    amount = stats["amount"]
    return {
        "liquidity": "low" if amount <= breaks[0] else "high" if amount >= breaks[1] else "mid",
        "volatility": "high" if stats["volatility"] >= 0.35 else "low" if stats["volatility"] <= 0.20 else "mid",
        "trend": "up" if stats["trend"] >= 0.12 else "down" if stats["trend"] <= -0.12 else "range",
    }


class CohortWyckoffLearningAgent:
    name = "CohortWyckoffLearningAgent"

    def __init__(self, db_path: Path, max_peers: int = 10):
        self.db_path = Path(db_path)
        self.max_peers = max(4, min(int(max_peers), 16))

    def enrich(
        self,
        daily: Sequence[Any],
        bars_by_freq: Dict[str, Sequence[Any]],
        events_by_freq: Dict[str, Sequence[Any]],
        split_by_date: Dict[str, str],
        theory_lookup: Any,
        cohort_mode: str = "hybrid",
        holding_days: int = 20,
    ) -> Dict[str, Any]:
        mode = cohort_mode if cohort_mode in COHORT_MODES else "hybrid"
        train_dates = [row.date for row in daily if split_by_date.get(row.date) == "train"]
        if not daily or not train_dates:
            return {"agent": self.name, "status": "insufficient_target_history", "mode": mode,
                    "peer_cards": [], "rule_evidence": {}}
        train_end = train_dates[-1]
        labels, peers, peer_series = self._select_peers(
            [row for row in daily if row.date <= train_end], train_end, mode
        )
        target_patterns: List[Dict[str, Any]] = []
        for frequency, bars in bars_by_freq.items():
            patterns = self._patterns(self._plain(bars), frequency)
            target_patterns.extend(patterns)
            event_map = {str(event.date): event for event in events_by_freq.get(frequency, [])}
            for pattern in patterns:
                event = event_map.get(pattern["date"])
                if event is None or any(item.get("rule_id") == pattern["rule_id"] for item in event.candidates):
                    continue
                theory = theory_lookup(pattern["rule_id"])
                event.candidates.append({
                    "rule_id": pattern["rule_id"], "name_cn": theory.get("name_cn", pattern["rule_id"]),
                    "frequency": frequency, "family": "wyckoff",
                    "direction_hypothesis": pattern["direction"], "target_horizon": holding_days,
                    "strength": pattern["strength"], "date": pattern["date"],
                    "hypothesis": theory.get("hypothesis", ""),
                    "applicable_conditions": theory.get("applicable_conditions", ""),
                    "invalidation": theory.get("invalidation", ""),
                    "source_role": "cohort_wyckoff_structure_challenger",
                    "source_detail": theory.get("source_detail", []),
                    "evidence": dict(pattern["evidence"]),
                })

        samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for peer in peers:
            rows = peer_series.get(peer["ts_code"], [])
            if len(rows) < 160:
                continue
            dates, closes = [row["date"] for row in rows], [row["close"] for row in rows]
            for frequency, freq_rows in self._frequencies(rows, tuple(events_by_freq)).items():
                for pattern in self._patterns(freq_rows, frequency):
                    index = bisect.bisect_right(dates, pattern["date"]) - 1
                    future = index + int(holding_days)
                    if index < 0 or future >= len(rows):
                        continue
                    forward = _pct(closes[future], closes[index])
                    samples[f"{pattern['rule_id']}|{frequency}"].append({
                        "ts_code": peer["ts_code"], "signed_return": pattern["direction"] * forward
                    })

        evidence: Dict[str, Dict[str, Any]] = {}
        for key, rows in samples.items():
            by_peer: Dict[str, List[float]] = defaultdict(list)
            for row in rows:
                by_peer[row["ts_code"]].append(_float(row["signed_return"]))
            peer_means = {code: _mean(values) for code, values in by_peer.items()}
            returns = [_float(row["signed_return"]) for row in rows]
            average, dispersion = _mean(returns), _stdev(returns)
            hit_rate = _mean(1.0 if value > 0 else 0.0 for value in returns)
            peer_ratio = _mean(1.0 if value > 0 else 0.0 for value in peer_means.values())
            edge_score = 1.0 / (1.0 + math.exp(-average / max(dispersion, 0.015)))
            score = max(0.0, min(1.0,
                0.20 * min(1.0, math.log1p(len(rows)) / math.log(41.0))
                + 0.20 * min(1.0, len(peer_means) / max(4.0, float(self.max_peers)))
                + 0.22 * hit_rate + 0.23 * peer_ratio + 0.15 * edge_score
            ))
            evidence[key] = {
                "peer_count": len(peer_means), "sample_count": len(rows),
                "avg_signed_return": average, "hit_rate": hit_rate,
                "positive_peer_ratio": peer_ratio, "dispersion": dispersion,
                "tail_signed_return": _quantile(returns, 0.10), "cross_stock_score": score,
                "validated_peers": sorted(code for code, value in peer_means.items() if value > 0),
                "failed_peers": sorted(code for code, value in peer_means.items() if value <= 0),
                "peer_mean_signed_returns": peer_means, "label_maturity_end": train_end,
            }
        for frequency, events in events_by_freq.items():
            for event in events:
                for candidate in event.candidates:
                    if str(candidate.get("rule_id", "")).startswith(WYCKOFF_PREFIX):
                        candidate.setdefault("evidence", {})["cohort_validation"] = evidence.get(
                            f"{candidate.get('rule_id')}|{frequency}", {}
                        )
        return {
            "agent": self.name, "status": "completed", "mode": mode,
            "target_code": daily[0].ts_code, "target_train_end": train_end,
            "stock_labels": labels, "cohort_id": labels["cohort_id"],
            "peer_cards": peers, "selected_peer_count": len(peers),
            "target_event_count": len(target_patterns),
            "target_event_counts": self._counts(pattern["rule_id"] for pattern in target_patterns),
            "rule_evidence": evidence,
            "learning_boundary": "all peer outcomes mature on or before the target training boundary",
            "test_usage": "target validation and test labels are never used for peer selection or peer evidence",
            "architecture_reference": "https://github.com/Arayasouren/wyckoff_agent",
            "implementation_note": "independent local implementation of public Predict-Critique-Reflect-Evolve and context-memory principles",
        }

    def _select_peers(
        self, target: Sequence[Any], train_end: str, mode: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
        target_code, target_stats = str(target[0].ts_code), _style_stats(target)
        recent_start = (dt.datetime.strptime(train_end, "%Y%m%d").date() - dt.timedelta(days=560)).strftime("%Y%m%d")
        industry_code = industry_name = ""
        industry_codes: set[str] = set()
        with sqlite3.connect(str(self.db_path)) as conn:
            try:
                row = conn.execute(
                    """select industry_code, industry_name from sw_l1_industry_daily
                       where ts_code=? and start_date<=?
                         and (coalesce(end_date,'')='' or end_date>=?)
                       order by start_date desc limit 1""",
                    (target_code, train_end, train_end),
                ).fetchone()
                if row:
                    industry_code, industry_name = str(row[0] or ""), str(row[1] or "")
                if industry_code:
                    industry_codes = {
                        str(row[0]) for row in conn.execute(
                            """select distinct ts_code from sw_l1_industry_daily
                               where industry_code=? and start_date<=?
                                 and (coalesce(end_date,'')='' or end_date>=?) and ts_code<>?""",
                            (industry_code, train_end, train_end, target_code),
                        ).fetchall()
                    }
            except sqlite3.DatabaseError:
                pass
            market = conn.execute(
                """select ts_code,max(coalesce(stock_name,'')),avg(amount),count(*),min(trade_date)
                   from stock_ohlcv_daily
                   where trade_date between ? and ? and qfq_close>0 and amount>0 and ts_code<>?
                   group by ts_code having count(*)>=160
                     and max(case when coalesce(stock_name,'') like '%ST%' then 1 else 0 end)=0
                   order by avg(amount) desc,ts_code limit 180""",
                (recent_start, train_end, target_code),
            ).fetchall()
            cards = {
                str(row[0]): {"ts_code": str(row[0]), "stock_name": str(row[1] or ""),
                              "avg_amount": _float(row[2]), "recent_rows": int(row[3] or 0),
                              "listing_date": str(row[4] or ""), "same_industry": str(row[0]) in industry_codes}
                for row in market
            }
            pool = list(dict.fromkeys(list(cards) + list(industry_codes)))[:240]
            if mode == "individual" or not pool:
                labels = self._labels(target_code, target_stats, (target_stats["amount"],) * 2,
                                      industry_code, industry_name, mode)
                return labels, [], {}
            placeholders = ",".join("?" for _ in pool)
            rows = conn.execute(
                f"""select trade_date,ts_code,qfq_close,amount from stock_ohlcv_daily
                    where ts_code in ({placeholders}) and trade_date between ? and ? and qfq_close>0
                    order by ts_code,trade_date""",
                tuple(pool) + (recent_start, train_end),
            ).fetchall()
        style_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for date, code, close, amount in rows:
            style_rows[str(code)].append({"date": str(date), "close": _float(close), "amount": _float(amount)})
        stats = {code: _style_stats(values) for code, values in style_rows.items() if len(values) >= 120}
        amounts = [item["amount"] for item in stats.values() if item["amount"] > 0]
        breaks = (_quantile(amounts, 0.33), _quantile(amounts, 0.67)) if amounts else (target_stats["amount"],) * 2
        labels = self._labels(target_code, target_stats, breaks, industry_code, industry_name, mode)

        def distance(code: str) -> float:
            item = stats[code]
            same_industry = code in industry_codes
            if mode == "industry" and not same_industry:
                return 1e6
            return (
                0.45 * abs(math.log1p(item["amount"]) - math.log1p(target_stats["amount"]))
                + 0.30 * abs(item["volatility"] - target_stats["volatility"]) / max(target_stats["volatility"], 0.12)
                + 0.25 * abs(item["trend"] - target_stats["trend"])
                + (0.0 if _board(code) == _board(target_code) else 0.35)
                + (0.0 if same_industry else 0.45 if mode == "hybrid" else 0.0)
            )

        ranked = sorted(stats, key=lambda code: (distance(code), code))
        selected = [code for code in ranked if distance(code) < 1e5][:self.max_peers]
        peer_cards = []
        for code in selected:
            item = stats[code]
            card = dict(cards.get(code) or {"ts_code": code, "stock_name": "", "avg_amount": item["amount"]})
            card.update({
                "industry_code": industry_code if code in industry_codes else "",
                "industry_name": industry_name if code in industry_codes else "",
                "board": _board(code), "same_industry": code in industry_codes,
                "distance": distance(code), "similarity": math.exp(-max(0.0, distance(code))),
                "style_labels": _style_labels(item, breaks),
                "volatility": item["volatility"], "trend_120": item["trend"],
            })
            peer_cards.append(card)
        if not selected:
            return labels, peer_cards, {}
        with sqlite3.connect(str(self.db_path)) as conn:
            placeholders = ",".join("?" for _ in selected)
            rows = conn.execute(
                f"""select trade_date,ts_code,coalesce(stock_name,''),open,high,low,close,qfq_close,vol,amount
                    from stock_ohlcv_daily where ts_code in ({placeholders})
                      and trade_date<=? and qfq_close>0 and close>0 order by ts_code,trade_date""",
                tuple(selected) + (train_end,),
            ).fetchall()
        series: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for date, code, name, open_, high, low, close, qfq_close, volume, amount in rows:
            raw_close, adjusted = _float(close), _float(qfq_close)
            factor = adjusted / raw_close if raw_close > 0 and adjusted > 0 else 1.0
            series[str(code)].append({
                "date": str(date), "ts_code": str(code), "stock_name": str(name or ""),
                "open": _float(open_) * factor, "high": _float(high) * factor,
                "low": _float(low) * factor, "close": adjusted,
                "vol": _float(volume), "amount": _float(amount),
            })
        return labels, peer_cards, dict(series)

    @staticmethod
    def _labels(code: str, stats: Dict[str, float], breaks: Tuple[float, float],
                industry_code: str, industry_name: str, mode: str) -> Dict[str, Any]:
        style = _style_labels(stats, breaks)
        cohort_id = industry_code or f"{_board(code)}-{style['liquidity']}-{style['volatility']}-{style['trend']}"
        return {
            "ts_code": code, "industry_code": industry_code, "industry_name": industry_name,
            "board": _board(code), "liquidity_style": style["liquidity"],
            "volatility_style": style["volatility"], "trend_style": style["trend"],
            "listing_history_bars": int(stats["age"]), "cohort_mode": mode, "cohort_id": cohort_id,
        }

    @staticmethod
    def _plain(rows: Sequence[Any]) -> List[Dict[str, Any]]:
        return [dict(row) if isinstance(row, dict) else {
            "date": str(row.date), "ts_code": str(row.ts_code),
            "stock_name": str(getattr(row, "stock_name", "")),
            "open": _float(row.open), "high": _float(row.high), "low": _float(row.low),
            "close": _float(row.qfq_close or row.close), "vol": _float(row.vol),
            "amount": _float(row.amount),
        } for row in rows]

    @staticmethod
    def _aggregate(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "date": rows[-1]["date"], "ts_code": rows[-1].get("ts_code", ""),
            "stock_name": rows[-1].get("stock_name", ""), "open": rows[0]["open"],
            "high": max(row["high"] for row in rows), "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"], "vol": sum(row.get("vol", 0.0) for row in rows),
            "amount": sum(row.get("amount", 0.0) for row in rows),
        }

    def _frequencies(self, daily: Sequence[Dict[str, Any]], frequencies: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        output: Dict[str, List[Dict[str, Any]]] = {}
        for frequency in frequencies:
            if frequency == "D":
                output[frequency] = list(daily)
            elif frequency in ("W", "M"):
                groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                order: List[str] = []
                for row in daily:
                    date = dt.datetime.strptime(row["date"], "%Y%m%d").date()
                    key = f"{date.isocalendar().year}W{date.isocalendar().week:02d}" if frequency == "W" else row["date"][:6]
                    if key not in groups:
                        order.append(key)
                    groups[key].append(row)
                output[frequency] = [self._aggregate(groups[key]) for key in order]
            elif frequency.endswith("D") and frequency[:-1].isdigit():
                window = int(frequency[:-1])
                output[frequency] = [self._aggregate(daily[index-window+1:index+1])
                                     for index in range(window - 1, len(daily))]
        return output

    def _patterns(self, bars: Sequence[Dict[str, Any]], frequency: str) -> List[Dict[str, Any]]:
        if len(bars) < 18:
            return []
        results: List[Dict[str, Any]] = []
        last_emit: Dict[str, int] = {}
        last_sos = last_sow = -10_000
        lookback = min(40 if frequency == "D" else 26 if frequency == "W" else 14,
                       max(12, len(bars) // 4))
        cooldown = 5 if frequency == "D" or frequency.endswith("D") else 2

        def emit(index: int, rule_id: str, direction: int, strength: float, evidence: Dict[str, Any]) -> None:
            if index - last_emit.get(rule_id, -10_000) < cooldown:
                return
            last_emit[rule_id] = index
            results.append({"date": bars[index]["date"], "rule_id": rule_id, "direction": direction,
                            "strength": max(0.05, min(0.98, strength)), "evidence": evidence})

        for index in range(lookback, len(bars)):
            row, history = bars[index], bars[index-lookback:index]
            recent = bars[max(0, index-20):index]
            close = _float(row["close"])
            current_range = max(_float(row["high"]) - _float(row["low"]), close * 1e-6)
            ranges = [max(_float(item["high"]) - _float(item["low"]), close * 1e-6) for item in recent]
            amounts = [_float(item.get("amount")) for item in recent]
            range_ratio = current_range / max(_quantile(ranges, 0.50), close * 0.002)
            amount_ratio = _float(row.get("amount")) / max(_quantile(amounts, 0.50), 1.0)
            close_position = (close - _float(row["low"])) / current_range
            support = min(_float(item["low"]) for item in history)
            resistance = max(_float(item["high"]) for item in history)
            prior_close = _float(bars[index-1]["close"])
            ret20 = _pct(prior_close, _float(bars[max(0, index-20)]["close"]))
            history_closes = [_float(item["close"]) for item in history]
            full_vol = _stdev([_pct(history_closes[i], history_closes[i-1]) for i in range(1, len(history_closes))])
            recent_closes = [_float(item["close"]) for item in recent]
            recent_vol = _stdev([_pct(recent_closes[i], recent_closes[i-1]) for i in range(1, len(recent_closes))])
            evidence = {
                "support": support, "resistance": resistance, "range_ratio": range_ratio,
                "amount_ratio": amount_ratio, "close_position": close_position,
                "ret_20": ret20, "frequency": frequency,
                "adaptive_context": "prior_range_and_rolling_effort_result_quantiles",
            }
            if row["low"] < support and close > support and close_position >= 0.55:
                emit(index, "KLINE_WYCKOFF_SPRING_BULL", 1,
                     0.46 + 0.13*min(range_ratio, 2.0) + 0.12*min(amount_ratio, 2.0), evidence)
            if row["high"] > resistance and close < resistance and close_position <= 0.45:
                emit(index, "KLINE_WYCKOFF_UPTHRUST_BEAR", -1,
                     0.46 + 0.13*min(range_ratio, 2.0) + 0.12*min(amount_ratio, 2.0), evidence)
            if close > resistance and close_position >= 0.62 and range_ratio >= 0.90:
                emit(index, "KLINE_WYCKOFF_SOS_BULL", 1,
                     0.43 + 0.15*min(range_ratio, 2.2) + 0.10*min(amount_ratio, 2.2), evidence)
                last_sos = index
            if close < support and close_position <= 0.38 and range_ratio >= 0.90:
                emit(index, "KLINE_WYCKOFF_SOW_BEAR", -1,
                     0.43 + 0.15*min(range_ratio, 2.2) + 0.10*min(amount_ratio, 2.2), evidence)
                last_sow = index
            if 3 <= index-last_sos <= max(8, lookback//2) and close >= support and amount_ratio <= 1.10 and close >= prior_close:
                emit(index, "KLINE_WYCKOFF_LPS_BULL", 1, 0.52 + 0.13*(1.10-min(amount_ratio, 1.10)), evidence)
            if 3 <= index-last_sow <= max(8, lookback//2) and close <= resistance and amount_ratio <= 1.10 and close <= prior_close:
                emit(index, "KLINE_WYCKOFF_LPSY_BEAR", -1, 0.52 + 0.13*(1.10-min(amount_ratio, 1.10)), evidence)
            range_extreme = current_range >= _quantile(ranges, 0.80)
            amount_extreme = _float(row.get("amount")) >= _quantile(amounts, 0.80)
            if ret20 < 0 and range_extreme and amount_extreme and close_position >= 0.45:
                emit(index, "KLINE_WYCKOFF_SELLING_CLIMAX_BULL", 1,
                     0.42 + 0.14*min(range_ratio, 2.2) + 0.12*min(amount_ratio, 2.2), evidence)
            if ret20 > 0 and range_extreme and amount_extreme and close_position <= 0.55:
                emit(index, "KLINE_WYCKOFF_BUYING_CLIMAX_BEAR", -1,
                     0.42 + 0.14*min(range_ratio, 2.2) + 0.12*min(amount_ratio, 2.2), evidence)
            third = max(4, lookback//3)
            center_shift = _pct(_mean(history_closes[-third:]), _mean(history_closes[:third]))
            if ret20 <= 0 and recent_vol <= max(full_vol, 1e-9) and center_shift > 0 and close_position >= 0.50:
                emit(index, "KLINE_WYCKOFF_ACCUMULATION_BULL", 1,
                     0.38 + 0.16*min(abs(center_shift)/max(full_vol, 0.01), 2.0), evidence)
            if ret20 >= 0 and recent_vol <= max(full_vol, 1e-9) and center_shift < 0 and close_position <= 0.50:
                emit(index, "KLINE_WYCKOFF_DISTRIBUTION_BEAR", -1,
                     0.38 + 0.16*min(abs(center_shift)/max(full_vol, 0.01), 2.0), evidence)
        return results

    @staticmethod
    def _counts(values: Iterable[str]) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for value in values:
            counts[value] += 1
        return dict(counts)


def apply_cohort_evolution(
    learned_rules: Sequence[Any],
    cohort_report: Dict[str, Any],
    primary_horizon: int,
    profile_root: Optional[Path] = None,
) -> Tuple[List[Any], Dict[str, Any], Dict[str, Any]]:
    rules = [copy.deepcopy(rule) for rule in learned_rules]
    evidence_map = cohort_report.get("rule_evidence", {}) if isinstance(cohort_report, dict) else {}
    profiles = (
        {"id": "candidate_a", "name_cn": "\u4e25\u683c\u8de8\u80a1\u5171\u8bc6", "peer_weight": 0.55, "complexity": 0.10},
        {"id": "candidate_b", "name_cn": "\u4e2a\u80a1\u4e0e\u540c\u7c7b\u5e73\u8861", "peer_weight": 0.38, "complexity": 0.05},
        {"id": "candidate_c", "name_cn": "\u884c\u4e1a\u60c5\u5883\u5206\u652f", "peer_weight": 0.28, "complexity": 0.08},
    )

    def value(rule: Any, evidence: Dict[str, Any], profile: Dict[str, Any]) -> Tuple[float, bool]:
        direction = 1 if int(getattr(rule, "direction", 0)) >= 0 else -1
        train, valid = getattr(rule, "train", {}) or {}, getattr(rule, "valid", {}) or {}
        train_edge = direction * _float(train.get(f"avg_fwd_{primary_horizon}"))
        valid_edge = direction * _float(valid.get(f"avg_fwd_{primary_horizon}"))
        target_value = 0.40*math.tanh(train_edge/0.04) + 0.60*math.tanh(valid_edge/0.04)
        peer_score = _float(evidence.get("cross_stock_score"))
        peer_edge = math.tanh(_float(evidence.get("avg_signed_return")) / max(_float(evidence.get("dispersion")), 0.02))
        peer_value = 0.55*(2.0*peer_score-1.0) + 0.45*peer_edge
        score = ((1.0-profile["peer_weight"])*target_value + profile["peer_weight"]*peer_value
                 + 0.12*min(1.0, math.log1p(_float(evidence.get("sample_count")))/math.log(31.0))
                 - profile["complexity"])
        if profile["id"] == "candidate_a":
            accepted = evidence.get("peer_count", 0) >= 3 and peer_score >= 0.58 and valid_edge > 0
        elif profile["id"] == "candidate_b":
            accepted = evidence.get("peer_count", 0) >= 2 and peer_score >= 0.48 and min(train_edge, valid_edge) > -0.01
        else:
            accepted = (evidence.get("peer_count", 0) >= 2 and peer_score >= 0.42
                        and evidence.get("positive_peer_ratio", 0.0) >= 0.50 and train_edge > 0)
        return score, bool(accepted)

    profile_rows = []
    for profile in profiles:
        selected, values = [], []
        for rule in rules:
            if not str(getattr(rule, "rule_id", "")).startswith(WYCKOFF_PREFIX):
                continue
            evidence = evidence_map.get(f"{rule.rule_id}|{rule.frequency}", {})
            score, accepted = value(rule, evidence, profile)
            if accepted:
                values.append(score)
                selected.append({"rule_id": rule.rule_id, "frequency": rule.frequency, "score": score})
        profile_rows.append({
            "candidate_id": profile["id"], "name_cn": profile["name_cn"],
            "objective": _mean(values) + 0.035*min(len(values), 6) if values else -1.0,
            "accepted_rule_count": len(selected), "selected_rules": selected,
            "selection_evidence": "train_valid_rule_utility_plus_training_boundary_peer_evidence",
            "test_usage": "not_used",
        })
    selected_profile = max(profile_rows, key=lambda row: (_float(row["objective"], -1.0), -len(row["selected_rules"])))
    selected_keys = {(item["rule_id"], item["frequency"]) for item in selected_profile["selected_rules"]}
    for rule in rules:
        if not str(getattr(rule, "rule_id", "")).startswith(WYCKOFF_PREFIX):
            continue
        evidence = evidence_map.get(f"{rule.rule_id}|{rule.frequency}", {})
        diagnostics = dict(getattr(rule, "learning_diagnostics", {}) or {})
        diagnostics.update({"cohort_validation": evidence, "cohort_profile": selected_profile["candidate_id"],
                            "cohort_accepted": (rule.rule_id, rule.frequency) in selected_keys})
        rule.learning_diagnostics = diagnostics
        if (rule.rule_id, rule.frequency) not in selected_keys:
            rule.status = "watch" if rule.status not in ("deprecated", "rejected") else rule.status
            rule.confidence = min(_float(rule.confidence), 0.24)
        else:
            peer_score = _float(evidence.get("cross_stock_score"), 0.5)
            rule.confidence = max(0.05, min(0.98, _float(rule.confidence)*(0.88+0.22*peer_score)))
            if selected_profile["candidate_id"] == "candidate_c" and rule.status == "active":
                rule.status = "conditional"
                rule.applicable_conditions = (
                    str(rule.applicable_conditions)
                    + "; \u4ec5\u5728\u884c\u4e1a/\u98ce\u683c\u60c5\u5883\u4e0e\u8de8\u80a1\u9a8c\u8bc1\u5206\u652f\u4e00\u81f4\u65f6\u542f\u7528"
                )
    notes = _context_notes(rules, cohort_report, primary_horizon)
    memory = _merge_profile(notes, cohort_report, profile_root)
    evolution = {
        "agent": "CohortContextEvolver",
        "method": "predict_critique_reflect_evolve_with_add_skip_replace_branch_entropy_control",
        "candidates": sorted(profile_rows, key=lambda row: _float(row["objective"]), reverse=True),
        "selected_candidate_id": selected_profile["candidate_id"],
        "selected_candidate_name_cn": selected_profile["name_cn"],
        "selected_rule_count": len(selected_keys),
        "selected_rule_keys": [f"{rule_id}|{frequency}" for rule_id, frequency in sorted(selected_keys)],
        "acceptance_boundary": "target train/valid plus peer labels ending at target train_end",
        "portfolio_acceptance": "pending existing full signal-chain paired bootstrap and no-degradation gates",
        "test_usage": "sealed_not_used",
    }
    return rules, evolution, memory


def _context_notes(rules: Sequence[Any], report: Dict[str, Any], horizon: int) -> List[Dict[str, Any]]:
    evidence_map, labels = report.get("rule_evidence", {}), report.get("stock_labels", {})
    notes = []
    for rule in rules:
        if not str(getattr(rule, "rule_id", "")).startswith(WYCKOFF_PREFIX):
            continue
        evidence = evidence_map.get(f"{rule.rule_id}|{rule.frequency}", {})
        if not evidence:
            continue
        direction = "bullish" if int(getattr(rule, "direction", 0)) > 0 else "bearish"
        failures = list(getattr(rule, "failure_dates", []) or [])[:8]
        identity = f"{rule.rule_id}|{rule.frequency}|{labels.get('cohort_id', '')}"
        notes.append({
            "note_id": hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
            "rule_id": rule.rule_id, "name_cn": getattr(rule, "name_cn", rule.rule_id),
            "frequency": rule.frequency,
            "situation": f"{labels.get('industry_name') or labels.get('cohort_id') or 'style'}|{rule.frequency}|{direction}",
            "retrieval_text": f"{getattr(rule, 'name_cn', rule.rule_id)} {rule.frequency} {direction} {labels.get('industry_name', '')}",
            "confidence": _float(getattr(rule, "confidence", 0.0)),
            "created_at": dt.datetime.now().date().isoformat(),
            "evolved_at": dt.datetime.now().date().isoformat(),
            "source_windows": [horizon],
            "stocks_validated": evidence.get("validated_peers", []),
            "stocks_failed": evidence.get("failed_peers", []),
            "cross_stock_score": _float(evidence.get("cross_stock_score")),
            "sector_scope": [labels.get("industry_name") or labels.get("cohort_id") or "all"],
            "sector_excluded": [], "refined_count": 1 if failures else 0,
            "experience_summary": str(getattr(rule, "note_text", "")),
            "suggested_adjustment": "keep" if getattr(rule, "status", "") in ("active", "conditional") else "observe",
            "exception_branch": {
                "failure_dates": failures, "condition": "validation failure or peer-direction disagreement",
                "valid_horizon_mean": (getattr(rule, "valid", {}) or {}).get(f"avg_fwd_{horizon}"),
            },
            "status": getattr(rule, "status", "watch"),
        })
    return sorted(notes, key=lambda note: (-_float(note["cross_stock_score"]), -_float(note["confidence"])))


def _tokens(note: Dict[str, Any]) -> set[str]:
    text = re.sub(r"\s+", "", f"{note.get('retrieval_text', '')}{note.get('situation', '')}".lower())
    ascii_tokens = set(re.findall(r"[a-z0-9_]+", text))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    return ascii_tokens | {cjk[index:index+2] for index in range(max(0, len(cjk)-1))}


def _similarity(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _merge_profile(
    incoming_notes: Sequence[Dict[str, Any]],
    report: Dict[str, Any],
    profile_root: Optional[Path],
) -> Dict[str, Any]:
    cohort_id = str(report.get("cohort_id") or report.get("target_code") or "default")
    path: Optional[Path] = None
    notes: List[Dict[str, Any]] = []
    if profile_root is not None:
        root = Path(profile_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / (hashlib.sha1(cohort_id.encode("utf-8")).hexdigest()[:20] + ".json")
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                notes = list(payload.get("notes", [])) if isinstance(payload, dict) else []
            except (OSError, json.JSONDecodeError):
                notes = []
    decisions = []
    for incoming in incoming_notes:
        ranked = sorted(((index, _similarity(incoming, note)) for index, note in enumerate(notes)),
                        key=lambda row: row[1], reverse=True)
        nearest_index, nearest_score = ranked[0] if ranked else (-1, 0.0)
        same = next((index for index, note in enumerate(notes)
                     if note.get("rule_id") == incoming.get("rule_id")
                     and note.get("frequency") == incoming.get("frequency")), -1)
        if same >= 0:
            old = notes[same]
            improved = _float(incoming["cross_stock_score"]) > _float(old.get("cross_stock_score")) + 0.015
            if improved or incoming.get("status") != old.get("status"):
                incoming = dict(incoming)
                incoming["created_at"] = old.get("created_at") or incoming.get("created_at")
                incoming["refined_count"] = min(3, int(old.get("refined_count", 0) or 0) + 1)
                notes[same], decision = incoming, "replace"
            elif incoming.get("exception_branch", {}).get("failure_dates") and int(old.get("refined_count", 0) or 0) < 3:
                updated = dict(old)
                updated.update({"exception_branch": incoming["exception_branch"],
                                "evolved_at": incoming["evolved_at"],
                                "refined_count": min(3, int(old.get("refined_count", 0) or 0) + 1)})
                notes[same], decision = updated, "branch"
            else:
                decision = "skip"
        elif nearest_index >= 0 and nearest_score >= 0.88:
            decision = "skip"
        else:
            notes.append(dict(incoming))
            decision = "add"
        decisions.append({"decision": decision, "note_id": incoming.get("note_id"),
                          "rule_id": incoming.get("rule_id"), "nearest_similarity": nearest_score})
    notes = sorted(notes, key=lambda note: (-_float(note.get("cross_stock_score")),
                                            -_float(note.get("confidence"))))[:120]
    if path is not None:
        payload = {"schema_version": 1, "cohort_id": cohort_id,
                   "updated_at": dt.datetime.now().isoformat(timespec="seconds"), "notes": notes}
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    return {
        "agent": "ContextMemoryEntropyManager", "cohort_id": cohort_id,
        "profile_path": str(path) if path is not None else None,
        "note_count": len(notes), "notes": notes, "top_retrieved_notes": notes[:5],
        "decisions": decisions,
        "decision_counts": CohortWyckoffLearningAgent._counts(item["decision"] for item in decisions),
        "retrieval_method": "rule-frequency exact match then deterministic semantic token similarity",
        "entropy_policy": "add_skip_replace_branch_with_max_three_refinements_and_120_note_cap",
        "test_usage": "sealed_not_used",
    }
