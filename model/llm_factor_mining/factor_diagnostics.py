from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import sqlite3
import tempfile
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


UNIVERSES = ("ALL_A", "CSI800_ENH", "CSI2000_ENH")
FREQUENCIES = ("D", "W", "M", "Q")
PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4}
MIN_SPLIT_PERIODS = {
    "D": {"train": 126, "valid": 42, "test": 42},
    "W": {"train": 26, "valid": 8, "test": 8},
    "M": {"train": 12, "valid": 4, "test": 4},
    "Q": {"train": 6, "valid": 2, "test": 2},
}
TARGET_COUNTS = {"ALL_A": 80, "CSI800_ENH": 50, "CSI2000_ENH": 50}
UNIVERSE_CN = {"ALL_A": "全A优质域", "CSI800_ENH": "中证800", "CSI2000_ENH": "中证2000（发布前方法学代理）"}
FREQUENCY_CN = {"D": "日频", "W": "周频", "M": "月频", "Q": "季频"}
VERSION = "point-in-time-factor-diagnostics/1.2"


class UnsupportedDiagnosticOperator(RuntimeError):
    pass


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_factor_miner(path: Optional[Path] = None) -> Any:
    module_path = Path(path or Path(__file__).with_name("factor_miner.py")).resolve()
    return _load_module(module_path, "factor_miner_for_diagnostics")


def _frequency_dates(dates: Sequence[str]) -> Dict[str, List[str]]:
    weekly: Dict[Tuple[int, int], str] = {}
    monthly: Dict[str, str] = {}
    quarterly: Dict[str, str] = {}
    for value in dates:
        stamp = dt.datetime.strptime(value, "%Y%m%d").date()
        iso = stamp.isocalendar()
        weekly[(int(iso.year), int(iso.week))] = value
        monthly[value[:6]] = value
        quarterly[f"{value[:4]}Q{(int(value[4:6]) - 1) // 3 + 1}"] = value
    return {"D": list(dates), "W": list(weekly.values()), "M": list(monthly.values()), "Q": list(quarterly.values())}


def _split_boundaries(dates: Sequence[str]) -> Dict[str, Dict[str, str]]:
    if len(dates) < 5:
        raise RuntimeError("Diagnostic window is too short")
    train_index = max(0, min(len(dates) - 3, int(len(dates) * 0.60) - 1))
    valid_index = max(train_index + 1, min(len(dates) - 2, int(len(dates) * 0.80) - 1))
    return {
        "train": {"start": dates[0], "end": dates[train_index]},
        "valid": {"start": dates[train_index + 1], "end": dates[valid_index]},
        "test": {"start": dates[valid_index + 1], "end": dates[-1]},
    }


def _formal_split(result: Dict[str, Any]) -> Optional[Dict[str, Dict[str, str]]]:
    audit = result.get("split_audit") or {}
    output: Dict[str, Dict[str, str]] = {}
    for name in ("train", "valid", "test"):
        block = audit.get(name) or {}
        start, end = str(block.get("start") or ""), str(block.get("end") or "")
        if len(start) != 8 or len(end) != 8 or not start.isdigit() or not end.isdigit() or start > end:
            return None
        output[name] = {"start": start, "end": end}
    if not (output["train"]["end"] < output["valid"]["start"] <= output["valid"]["end"] < output["test"]["start"]):
        return None
    return output


def _warmup_start(first_signal_date: str) -> str:
    stamp = dt.datetime.strptime(first_signal_date, "%Y%m%d").date() - dt.timedelta(days=400)
    return stamp.strftime("%Y%m%d")


def _split_name(date: str, split: Dict[str, Dict[str, str]]) -> str:
    for name in ("train", "valid", "test"):
        block = split[name]
        if block["start"] <= date <= block["end"]:
            return name
    return "outside"


def _period_split(current_date: str, next_date: str, split: Dict[str, Dict[str, str]]) -> Optional[str]:
    current_split = _split_name(current_date, split)
    next_split = _split_name(next_date, split)
    if current_split not in {"train", "valid", "test"}:
        return None
    if next_split in {"train", "valid", "test"} and next_split != current_split:
        return None
    return current_split


def _node_key(node: Dict[str, Any]) -> str:
    raw = json.dumps(node, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class StreamingDslEvaluator:
    """Evaluate causal DSL nodes one signal cross-section at a time."""

    def __init__(self, miner: Any):
        self.miner = miner
        self.temporal_state: Dict[str, Dict[str, deque]] = defaultdict(dict)

    def _temporal(self, frame: pd.DataFrame, node: Dict[str, Any], child: pd.Series) -> pd.Series:
        operation = str(node.get("op"))
        window = int(node.get("window", 3))
        if window not in set(getattr(self.miner, "CAUSAL_OBSERVATION_WINDOWS", (1, 2, 3, 6, 12))):
            raise UnsupportedDiagnosticOperator(f"Unsupported causal window: {window}")
        state = self.temporal_state[_node_key(node)]
        output = pd.Series(np.nan, index=frame.index, dtype=float)
        min_periods = 1 if window == 1 else max(2, int(math.ceil(window * 0.60)))
        for index, code, value in zip(frame.index, frame["ts_code"].astype(str), pd.to_numeric(child, errors="coerce")):
            history = state.get(code)
            if history is None:
                history = deque(maxlen=window + 1)
                state[code] = history
            history.append(float(value) if pd.notna(value) else np.nan)
            values = np.asarray(history, dtype=float)
            if operation == "ts_delta":
                if len(values) > window and np.isfinite(values[-1]) and np.isfinite(values[-1 - window]):
                    output.at[index] = values[-1] - values[-1 - window]
            else:
                rolling = values[-window:]
                finite = rolling[np.isfinite(rolling)]
                if len(finite) < min_periods:
                    continue
                if operation == "ts_mean":
                    output.at[index] = float(np.mean(finite))
                elif operation == "ts_std":
                    output.at[index] = float(np.std(finite, ddof=0))
                elif operation == "ts_zscore":
                    std = float(np.std(finite, ddof=0))
                    if std > 1e-12 and np.isfinite(values[-1]):
                        output.at[index] = (values[-1] - float(np.mean(finite))) / std
                else:
                    raise UnsupportedDiagnosticOperator(f"Unsupported temporal operator: {operation}")
        return output

    def evaluate(self, frame: pd.DataFrame, node: Dict[str, Any]) -> pd.Series:
        if not isinstance(node, dict):
            raise UnsupportedDiagnosticOperator("DSL node must be an object")
        op = str(node.get("op", ""))
        if op == "feature":
            name = str(node.get("name", ""))
            if name not in frame.columns:
                return pd.Series(np.nan, index=frame.index, dtype=float)
            return pd.to_numeric(frame[name], errors="coerce")
        if op == "const":
            return pd.Series(float(node.get("value", 0.0)), index=frame.index)
        if op in {"ts_delta", "ts_mean", "ts_std", "ts_zscore"}:
            return self._temporal(frame, node, self.evaluate(frame, node.get("child", {})))
        if op == "rank":
            return self.miner.group_rank(frame, self.evaluate(frame, node.get("child", {})), ascending=bool(node.get("ascending", True)))
        if op == "industry_rank":
            return self.miner.industry_rank(frame, self.evaluate(frame, node.get("child", {})), ascending=bool(node.get("ascending", True)))
        if op == "neg":
            return -self.evaluate(frame, node.get("child", {}))
        if op == "add":
            children = list(node.get("children") or [])
            weights = list(node.get("weights") or [1.0] * len(children))
            output = pd.Series(0.0, index=frame.index)
            for child, weight in zip(children, weights):
                output = output + float(weight) * self.evaluate(frame, child).fillna(0.0)
            return output
        if op == "sub":
            return self.evaluate(frame, node.get("left", {})).fillna(0.0) - self.evaluate(frame, node.get("right", {})).fillna(0.0)
        if op == "mul":
            return self.evaluate(frame, node.get("left", {})).fillna(0.5) * self.evaluate(frame, node.get("right", {})).fillna(0.5)
        if op == "div":
            denominator = self.evaluate(frame, node.get("right", {})).replace(0.0, np.nan)
            return self.evaluate(frame, node.get("left", {})) / denominator
        if op == "zscore":
            child = self.evaluate(frame, node.get("child", {}))
            work = pd.DataFrame({"trade_date": frame["trade_date"], "value": child}, index=frame.index)
            return work.groupby("trade_date")["value"].transform(self.miner.zscore).fillna(0.0)
        if op == "clip01":
            return self.evaluate(frame, node.get("child", {})).clip(0.0, 1.0)
        if op == "soft_gate":
            gate = self.miner.group_rank(frame, self.evaluate(frame, node.get("gate", {}))).clip(0.0, 1.0)
            positive = self.evaluate(frame, node.get("if_true", {})).fillna(0.0)
            negative = self.evaluate(frame, node.get("if_false", {})).fillna(0.0)
            return gate * positive + (1.0 - gate) * negative
        if op == "graph_concept_residual":
            if not isinstance(node.get("child"), dict):
                raise UnsupportedDiagnosticOperator("Graph residual diagnostics require an explicit child program")
            child = self.evaluate(frame, node["child"])
            return self.miner.compute_graph_concept_residual_series(frame, child)
        if op == "style_residual":
            child = self.evaluate(frame, node.get("child", {}))
            residual = self.miner.compute_style_residual_series(frame, child, node.get("features", []))
            strength = max(0.0, min(1.0, _safe_float(node.get("strength"), 0.5)))
            return strength * residual + (1.0 - strength) * child
        raise UnsupportedDiagnosticOperator(f"Operator {op!r} requires a fitted batch backend and cannot be silently approximated")


def _quality_mask(frame: pd.DataFrame) -> pd.Series:
    amount = pd.to_numeric(frame.get("amount"), errors="coerce")
    circ_mv = pd.to_numeric(frame.get("circ_mv"), errors="coerce")
    amount_floor = float(amount.quantile(0.20)) if amount.notna().any() else 0.0
    market_value_floor = float(circ_mv.quantile(0.05)) if circ_mv.notna().any() else 0.0
    names = frame.get("stock_name", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()
    bad_name = names.str.contains(r"(?:\*?ST|退)", regex=True, na=False)
    return (
        ~bad_name
        & pd.to_numeric(frame.get("px120"), errors="coerce").gt(0)
        & pd.to_numeric(frame.get("px"), errors="coerce").gt(0)
        & amount.fillna(0.0).ge(amount_floor)
        & circ_mv.fillna(0.0).ge(market_value_floor)
        & pd.to_numeric(frame.get("vol"), errors="coerce").fillna(0.0).gt(0)
        & frame.get("suspend_timing", pd.Series(None, index=frame.index)).isna()
    )


def _fetch_execution_panel(conn: sqlite3.Connection, miner: Any, date: str, next_date: str) -> pd.DataFrame:
    members = miner.get_members(conn, "ALL_A", date)
    if not members:
        return pd.DataFrame()
    conn.execute("drop table if exists temp_factor_members")
    conn.execute("create temp table temp_factor_members(ts_code text primary key, weight real)")
    conn.executemany("insert or replace into temp_factor_members(ts_code, weight) values (?, ?)", members)
    d120 = miner.offset_trade_date(conn, date, 120)
    rows = conn.execute(
        """
        select o.ts_code, o.stock_name, o.qfq_close as px, o.vol, o.amount, o.suspend_timing,
               p120.qfq_close as px120,
               e.qfq_close * e.open / nullif(e.close, 0) as px_entry,
               v.circ_mv, ind.industry_name
        from temp_factor_members tm
        join stock_ohlcv_daily o on o.ts_code=tm.ts_code and o.trade_date=?
        left join stock_ohlcv_daily p120 on p120.ts_code=o.ts_code and p120.trade_date=?
        left join stock_ohlcv_daily e on e.ts_code=o.ts_code and e.trade_date=(
          select min(ex.trade_date)
          from stock_ohlcv_daily ex
          where ex.ts_code=o.ts_code and ex.trade_date>? and ex.trade_date<=?
            and ex.qfq_close>0 and ex.open>0 and ex.close>0 and ex.suspend_timing is null
            and not (ex.up_limit is not null and ex.open>=ex.up_limit*0.995 and ex.low>=ex.up_limit*0.995)
            and not (ex.down_limit is not null and ex.open<=ex.down_limit*1.005 and ex.high<=ex.down_limit*1.005)
        )
        left join stock_valuation_daily v on v.ts_code=o.ts_code and v.trade_date=o.trade_date
        left join sw_l1_industry_daily ind on ind.ts_code=o.ts_code
          and ind.start_date<=? and (ind.end_date is null or ind.end_date>=?)
        where o.qfq_close>0
        """,
        (date, d120, date, next_date, date, date),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=[
        "ts_code", "stock_name", "px", "vol", "amount", "suspend_timing",
        "px120", "px_entry", "circ_mv", "industry_name",
    ])
    for column in ("px", "vol", "amount", "px120", "px_entry", "circ_mv"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["industry_name"] = frame["industry_name"].fillna("UNCLASSIFIED")
    frame["execution_eligible"] = frame["px_entry"].gt(0)
    return frame


def _csi2000_proxy_rebalance_dates(dates: Sequence[str], official_start: str) -> List[str]:
    available = sorted(str(value) for value in dates if str(value) < official_start)
    output = [next((value for value in available if value >= "20131231"), "")]
    first_year = 2014
    last_year = int(official_start[:4]) if official_start and official_start[:4].isdigit() else int(available[-1][:4])
    for year in range(first_year, last_year + 1):
        for month in (6, 12):
            fridays = [dt.date(year, month, day) for day in range(1, 22) if dt.date(year, month, day).weekday() == 4]
            if len(fridays) < 2:
                continue
            effective = next((value for value in available if value > fridays[1].strftime("%Y%m%d")), "")
            if effective:
                output.append(effective)
    return sorted({value for value in output if value and value < official_start})


def _csi2000_proxy_snapshot(
    conn: sqlite3.Connection,
    miner: Any,
    date: str,
    previous: set,
) -> Tuple[set, Dict[str, Any]]:
    history_start = miner.offset_trade_date(conn, date, 252)
    rows = conn.execute(
        """
        select a.con_code, avg(h.amount) as average_amount, avg(v.total_mv) as average_total_mv,
               count(distinct h.trade_date) as observations, current.stock_name
        from index_constituent_period a
        join stock_ohlcv_daily current on current.ts_code=a.con_code and current.trade_date=a.trade_date
        join stock_ohlcv_daily h on h.ts_code=a.con_code and h.trade_date between ? and ?
        left join stock_valuation_daily v on v.ts_code=h.ts_code and v.trade_date=h.trade_date
        where a.universe='ALL_A' and a.trade_date=? and h.amount>0 and v.total_mv>0
        group by a.con_code, current.stock_name
        having count(distinct h.trade_date)>=120
        """,
        (history_start, date, date),
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["ts_code", "average_amount", "average_total_mv", "observations", "stock_name"])
    if frame.empty:
        return set(), {"date": date, "status": "empty"}
    names = frame["stock_name"].fillna("").astype(str).str.upper()
    frame = frame[~names.str.contains(r"(?:\*?ST|退)", regex=True, na=False)].copy()
    liquid_count = max(1, int(math.floor(len(frame) * 0.90)))
    liquid = frame.nlargest(liquid_count, "average_amount").sort_values("average_total_mv", ascending=False)
    csi800 = {str(code) for code, _ in miner.get_members(conn, "CSI800_ENH", date)}
    top1500 = set(liquid.head(1500)["ts_code"].astype(str))
    approximate_csi1000 = set(liquid[~liquid["ts_code"].astype(str).isin(csi800)].head(1000)["ts_code"].astype(str))
    excluded = csi800 | top1500 | approximate_csi1000
    candidates = liquid[~liquid["ts_code"].astype(str).isin(excluded)].copy()
    ranked = list(candidates["ts_code"].astype(str))
    rank = {code: index + 1 for index, code in enumerate(ranked)}
    selected = [
        code for code in ranked
        if (code in previous and rank[code] <= 2400) or (code not in previous and rank[code] <= 1600)
    ][:2000]
    selected_set = set(selected)
    if len(selected_set) < min(2000, len(ranked)):
        for code in ranked:
            selected_set.add(code)
            if len(selected_set) >= min(2000, len(ranked)):
                break
    return selected_set, {
        "date": date, "status": "ready", "history_start": history_start,
        "raw_sample": int(len(frame)), "liquid_sample": int(len(liquid)),
        "excluded_sample": int(len(excluded)), "candidate_sample": int(len(candidates)),
        "selected_sample": int(len(selected_set)), "previous_retained": int(len(selected_set & previous)),
    }


def _official_csi2000_timeline(conn: sqlite3.Connection) -> List[Tuple[str, frozenset]]:
    events: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for date, code, status in conn.execute(
        """
        select trade_date, con_code, status
        from index_constituent_period
        where universe='CSI2000_ENH' and source like 'wind:AIndexMembers%'
        order by trade_date, con_code
        """
    ):
        events[str(date)].append((str(code), str(status)))
    snapshots: Dict[str, set] = {}
    snapshot_dates = [str(row[0]) for row in conn.execute(
        """
        select trade_date from index_constituent_period
        where universe='CSI2000_ENH' and source='index_member_weight'
        group by trade_date having count(*)>=1600
        """
    )]
    for date in snapshot_dates:
        snapshots[date] = {
            str(row[0]) for row in conn.execute(
                "select con_code from index_constituent_period where universe='CSI2000_ENH' and source='index_member_weight' and trade_date=?",
                (date,),
            )
        }
    state: set = set()
    timeline: List[Tuple[str, frozenset]] = []
    for date in sorted(set(events) | set(snapshots)):
        for code, status in events.get(date, []):
            if status == "inactive":
                state.discard(code)
        for code, status in events.get(date, []):
            if status != "inactive":
                state.add(code)
        if date in snapshots:
            state = set(snapshots[date])
        if len(state) >= 1600:
            timeline.append((date, frozenset(state)))
    return timeline


def _build_csi2000_membership(
    conn: sqlite3.Connection,
    miner: Any,
    dates: Sequence[str],
) -> Tuple[Dict[str, frozenset], Dict[str, Any]]:
    official = _official_csi2000_timeline(conn)
    official_start = official[0][0] if official else "99999999"
    proxy: List[Tuple[str, frozenset]] = []
    proxy_audit: List[Dict[str, Any]] = []
    previous: set = set()
    for date in _csi2000_proxy_rebalance_dates(dates, official_start):
        previous, audit = _csi2000_proxy_snapshot(conn, miner, date, previous)
        proxy.append((date, frozenset(previous)))
        proxy_audit.append(audit)
    by_date: Dict[str, frozenset] = {}
    proxy_index = official_index = -1
    for date in dates:
        while proxy_index + 1 < len(proxy) and proxy[proxy_index + 1][0] <= date:
            proxy_index += 1
        while official_index + 1 < len(official) and official[official_index + 1][0] <= date:
            official_index += 1
        if date >= official_start and official_index >= 0:
            by_date[str(date)] = official[official_index][1]
        elif proxy_index >= 0:
            by_date[str(date)] = proxy[proxy_index][1]
        else:
            by_date[str(date)] = frozenset()
    launch_proxy = proxy[-1][1] if proxy else frozenset()
    launch_official = official[0][1] if official else frozenset()
    intersection = len(launch_proxy & launch_official)
    union = len(launch_proxy | launch_official)
    audit = {
        "mode": "prelaunch_methodology_proxy_then_official_point_in_time_members",
        "methodology": "CSI_2000_V1.1_trailing_252d_amount_and_total_mv_with_1600_2400_buffer",
        "methodology_url": "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/20231208180041-932000_Index_Methodology_cn.pdf",
        "base_date": "20131231", "official_start": official_start if official else None,
        "proxy_rebalances": proxy_audit,
        "official_event_snapshots": len(official),
        "official_member_count_min": min((len(state) for _, state in official), default=0),
        "official_member_count_max": max((len(state) for _, state in official), default=0),
        "launch_overlap_count": intersection,
        "launch_official_coverage": intersection / max(len(launch_official), 1),
        "launch_jaccard": intersection / max(union, 1),
        "prelaunch_results_are_proxy_not_official_index_history": True,
    }
    return by_date, audit


def _prepared_score(miner: Any, frame: pd.DataFrame, raw: pd.Series, neutralize: bool) -> pd.Series:
    values = pd.to_numeric(raw, errors="coerce").replace([np.inf, -np.inf], np.nan)
    low = values.quantile(0.01)
    high = values.quantile(0.99)
    values = values.clip(lower=low, upper=high).fillna(0.0)
    standardized = miner.zscore(values).fillna(0.0)
    if not neutralize:
        return standardized
    work = frame.copy()
    work["_diagnostic_z"] = standardized
    return miner.neutralize_size_industry(work, "_diagnostic_z").fillna(0.0)


def _trade_dates(conn: sqlite3.Connection, start: str, end: str) -> List[str]:
    return [str(row[0]) for row in conn.execute(
        "select distinct trade_date from stock_ohlcv_daily where trade_date between ? and ? order by trade_date",
        (start, end),
    )]


def _candidate_row(result: Dict[str, Any], factor: Optional[str]) -> Dict[str, Any]:
    rows = list(result.get("accepted_factors") or result.get("leaderboard") or [])
    if factor:
        for row in rows:
            if str(row.get("factor")) == str(factor):
                return row
    if not rows:
        raise RuntimeError("Result has no factor candidate")
    return rows[0]


def _empty_runtime(frequency_dates: Dict[str, List[str]], codes: Sequence[str]) -> Dict[str, Dict[str, np.ndarray]]:
    width = len(codes)
    output: Dict[str, Dict[str, np.ndarray]] = {}
    for frequency, dates in frequency_dates.items():
        shape = (len(dates), width)
        output[frequency] = {
            "score": np.full(shape, np.nan, dtype=np.float32),
            "rank": np.full(shape, np.nan, dtype=np.float32),
            "px": np.full(shape, np.nan, dtype=np.float32),
            "entry": np.full(shape, np.nan, dtype=np.float32),
            "entry_eligible": np.zeros(shape, dtype=np.bool_),
            "quality": np.zeros(shape, dtype=np.bool_),
            "industry": np.full(shape, -1, dtype=np.int16),
            **{f"member_{universe}": np.zeros(shape, dtype=np.bool_) for universe in UNIVERSES},
        }
    return output


def _max_drawdown(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    array = np.asarray(values, dtype=float)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], array)))
    path = np.concatenate(([1.0], array))
    return float(np.min(path / np.maximum(peaks, 1e-12) - 1.0))


def _metric_block(periods: Sequence[Dict[str, Any]], frequency: str) -> Dict[str, Any]:
    if not periods:
        return {"periods": 0, "rank_ic": 0.0, "icir": 0.0, "positive_ic_ratio": 0.0, "annual_return": 0.0,
                "excess_annual_return": 0.0, "long_short_annual_return": 0.0, "sharpe": 0.0,
                "long_short_sharpe": 0.0, "max_drawdown": 0.0, "turnover": 0.0, "coverage": 0.0,
                "group_returns": [], "monotonicity": 0.0}
    ppy = PERIODS_PER_YEAR[frequency]
    long_returns = np.asarray([_safe_float(row.get("long_return")) for row in periods], dtype=float)
    benchmark = np.asarray([_safe_float(row.get("benchmark_return")) for row in periods], dtype=float)
    long_short = np.asarray([_safe_float(row.get("long_short_return")) for row in periods], dtype=float)
    ics = np.asarray([_safe_float(row.get("rank_ic")) for row in periods], dtype=float)

    def annualized(values: np.ndarray) -> float:
        nav = float(np.prod(1.0 + np.maximum(values, -0.999999)))
        return nav ** (ppy / len(values)) - 1.0 if len(values) and nav > 0 else 0.0

    def sharpe(values: np.ndarray) -> float:
        std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
        return float(np.mean(values) / std * math.sqrt(ppy)) if std > 1e-12 else 0.0

    nav = np.cumprod(1.0 + np.maximum(long_returns, -0.999999))
    group_rows = [row["group_returns"] for row in periods if len(row.get("group_returns", [])) == 5]
    groups = np.mean(np.asarray(group_rows, dtype=float), axis=0).tolist() if group_rows else []
    monotonicity = float(pd.Series(groups).corr(pd.Series([1, 2, 3, 4, 5]), method="spearman")) if len(groups) == 5 else 0.0
    ic_std = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
    return {
        "periods": int(len(periods)), "rank_ic": float(np.mean(ics)),
        "icir": float(np.mean(ics) / ic_std * math.sqrt(ppy)) if ic_std > 1e-12 else 0.0,
        "positive_ic_ratio": float(np.mean(ics > 0)), "annual_return": annualized(long_returns),
        "excess_annual_return": annualized(long_returns - benchmark),
        "long_short_annual_return": annualized(long_short), "sharpe": sharpe(long_returns),
        "long_short_sharpe": sharpe(long_short), "max_drawdown": _max_drawdown(nav.tolist()),
        "turnover": float(np.mean([_safe_float(row.get("turnover")) for row in periods])),
        "coverage": float(np.mean([_safe_float(row.get("coverage")) for row in periods])),
        "group_returns": [float(value) for value in groups], "monotonicity": monotonicity,
    }


def _turnover(previous: Dict[int, float], current: Dict[int, float]) -> float:
    keys = set(previous) | set(current)
    stock_change = sum(abs(current.get(key, 0.0) - previous.get(key, 0.0)) for key in keys)
    previous_cash = max(0.0, 1.0 - sum(previous.values()))
    current_cash = max(0.0, 1.0 - sum(current.values()))
    return 0.5 * (stock_change + abs(current_cash - previous_cash))


def _weights(
    score: np.ndarray,
    mask: np.ndarray,
    entry_eligible: np.ndarray,
    industries: np.ndarray,
    target: int,
    previous: Dict[int, float],
    top: bool,
) -> Dict[int, float]:
    candidates = np.where(mask & np.isfinite(score))[0]
    locked = {
        int(code): float(weight)
        for code, weight in previous.items()
        if 0 <= int(code) < len(entry_eligible) and not bool(entry_eligible[int(code)])
    }
    locked_weight = min(1.0, sum(max(0.0, weight) for weight in locked.values()))
    if locked_weight > 0:
        locked = {code: max(0.0, weight) for code, weight in locked.items()}
    if not len(candidates):
        if locked_weight <= 0:
            return {}
        return dict(locked)

    ordered = candidates[np.argsort(score[candidates])]
    if top:
        ordered = ordered[::-1]
    target = min(target, max(1, int(round(len(candidates) * 0.20))))
    buffer_codes = set(ordered[: min(len(ordered), max(target, int(math.ceil(target * 1.5))))].tolist())
    industry_cap = max(3, int(math.ceil(target * 0.15)))
    selected: List[int] = list(locked)
    counts: Dict[int, int] = defaultdict(int)
    for code in selected:
        counts[int(industries[code])] += 1
    for code in list(previous) + ordered.tolist():
        if len(selected) >= target:
            break
        if code in selected or code not in buffer_codes or not entry_eligible[code]:
            continue
        industry = int(industries[code])
        if counts[industry] >= industry_cap:
            continue
        selected.append(code)
        counts[industry] += 1
        if len(selected) >= target:
            break

    tradable = [code for code in selected if code not in locked]
    if not selected:
        return {}
    if not tradable:
        return dict(locked)
    strength = score[tradable] if top else 1.0 - score[tradable]
    minimum, maximum = float(np.nanmin(strength)), float(np.nanmax(strength))
    normalized = (strength - minimum) / max(maximum - minimum, 1e-12)
    raw = 0.85 + 0.15 * normalized
    available_weight = max(0.0, 1.0 - locked_weight)
    weights = raw / max(float(np.sum(raw)), 1e-12) * available_weight
    cap = max(0.03, 1.80 / max(len(selected), 1))
    weights = np.minimum(weights, cap)
    if float(np.sum(weights)) > 0:
        weights = weights / float(np.sum(weights)) * available_weight
    output = dict(locked)
    output.update({int(code): float(weight) for code, weight in zip(tradable, weights)})
    return output


def _portfolio_widths(candidate: Dict[str, Any]) -> List[Dict[str, float]]:
    selection = ((candidate.get("metrics") or {}).get("portfolio_selection") or {})
    rows = []
    for row in list(selection.get("weights") or []):
        fraction = _safe_float(row.get("fraction"))
        weight = _safe_float(row.get("weight"))
        if 0.01 <= fraction <= 0.30 and weight > 0:
            rows.append({"fraction": fraction, "weight": weight})
    if not rows:
        fraction = _safe_float(selection.get("selected_fraction"))
        if 0.01 <= fraction <= 0.30:
            rows = [{"fraction": fraction, "weight": 1.0}]
    total = sum(row["weight"] for row in rows)
    return [{"fraction": row["fraction"], "weight": row["weight"] / total} for row in rows] if total > 0 else []


def _blend_weights(weight_maps: Sequence[Dict[int, float]], sleeves: Sequence[Dict[str, float]]) -> Dict[int, float]:
    output: Dict[int, float] = defaultdict(float)
    for weights, sleeve in zip(weight_maps, sleeves):
        sleeve_weight = _safe_float(sleeve.get("weight"))
        for code, weight in weights.items():
            output[int(code)] += sleeve_weight * float(weight)
    return dict(output)


def _weighted_return(returns: np.ndarray, weights: Dict[int, float]) -> float:
    return float(sum(weight * returns[code] for code, weight in weights.items() if np.isfinite(returns[code])))


def _annual(periods: Sequence[Dict[str, Any]], frequency: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in periods:
        grouped[str(row["signal_date"])[:4]].append(row)
    output = []
    for year, rows in sorted(grouped.items()):
        metrics = _metric_block(rows, frequency)
        output.append({"year": year, "rank_ic": metrics["rank_ic"], "excess_return": metrics["excess_annual_return"],
                       "long_short_return": metrics["long_short_annual_return"], "coverage": metrics["coverage"]})
    return output


def _training_orientation(
    dates: Sequence[str],
    arrays: Dict[str, np.ndarray],
    split: Dict[str, Dict[str, str]],
) -> float:
    score = arrays["score"].astype(float)
    train_ics: List[float] = []
    for index in range(len(dates) - 1):
        if _split_name(dates[index], split) != "train" or _split_name(dates[index + 1], split) != "train":
            continue
        start_price = np.where(arrays["entry_eligible"][index], arrays["entry"][index], arrays["px"][index])
        exit_price = np.where(arrays["entry_eligible"][index + 1], arrays["entry"][index + 1], arrays["px"][index + 1])
        returns = exit_price / start_price - 1.0
        mask = arrays["quality"][index] & arrays["member_ALL_A"][index] & arrays["entry_eligible"][index]
        mask &= np.isfinite(score[index]) & np.isfinite(returns)
        if int(mask.sum()) < 50:
            continue
        ic = pd.Series(score[index, mask]).rank().corr(pd.Series(returns[mask]).rank())
        if pd.notna(ic):
            train_ics.append(float(ic))
    return -1.0 if train_ics and float(np.mean(train_ics)) < 0 else 1.0


def _evaluate_frequency(
    frequency: str,
    dates: Sequence[str],
    arrays: Dict[str, np.ndarray],
    split: Dict[str, Dict[str, str]],
    orientation: float,
    portfolio_widths: Sequence[Dict[str, float]],
    cost_rate: float,
    names: Sequence[str],
    codes: Sequence[str],
    industry_names: Sequence[str],
) -> Tuple[Dict[str, Any], np.ndarray]:
    score = arrays["score"].astype(float)
    orientation = -1.0 if orientation < 0 else 1.0
    oriented = score * orientation
    ranks = np.full(oriented.shape, np.nan, dtype=np.float32)
    for index in range(len(dates)):
        valid = np.isfinite(oriented[index])
        if valid.any():
            ranks[index, valid] = pd.Series(oriented[index, valid]).rank(pct=True).to_numpy(dtype=np.float32)
    arrays["rank"][:] = ranks
    universe_results: Dict[str, Any] = {}
    for universe in UNIVERSES:
        periods: List[Dict[str, Any]] = []
        sleeves = list(portfolio_widths) or [{"fraction": 0.0, "weight": 1.0}]
        last_top_sleeves: List[Dict[int, float]] = [{} for _ in sleeves]
        last_bottom_sleeves: List[Dict[int, float]] = [{} for _ in sleeves]
        last_top: Dict[int, float] = {}
        last_bottom: Dict[int, float] = {}
        active_split: Optional[str] = None
        member_key = f"member_{universe}"
        for index in range(len(dates) - 1):
            current_split = _period_split(dates[index], dates[index + 1], split)
            if current_split is None:
                continue
            if current_split != active_split:
                last_top_sleeves = [{} for _ in sleeves]
                last_bottom_sleeves = [{} for _ in sleeves]
                last_top, last_bottom = {}, {}
                active_split = current_split
            start_price = np.where(arrays["entry_eligible"][index], arrays["entry"][index], arrays["px"][index])
            exit_price = np.where(arrays["entry_eligible"][index + 1], arrays["entry"][index + 1], arrays["px"][index + 1])
            returns = exit_price / start_price - 1.0
            sample = arrays[member_key][index] & arrays["quality"][index] & arrays["entry_eligible"][index]
            sample &= np.isfinite(ranks[index]) & np.isfinite(returns)
            eligible_count = int((arrays[member_key][index] & arrays["quality"][index]).sum())
            if int(sample.sum()) < 30:
                continue
            ic = pd.Series(ranks[index, sample]).rank().corr(pd.Series(returns[sample]).rank())
            rank_ic = float(ic) if pd.notna(ic) else 0.0
            group_returns: List[float] = []
            if int(sample.sum()) >= 50:
                values = pd.Series(ranks[index, sample])
                try:
                    groups = pd.qcut(values.rank(method="first"), 5, labels=False) + 1
                    grouped = pd.Series(returns[sample]).groupby(groups.to_numpy()).mean()
                    group_returns = [float(grouped.get(group, 0.0)) for group in range(1, 6)]
                except ValueError:
                    group_returns = []
            selection_mask = arrays[member_key][index] & arrays["quality"][index] & np.isfinite(ranks[index])
            selection_count = int(selection_mask.sum())
            next_top_sleeves: List[Dict[int, float]] = []
            next_bottom_sleeves: List[Dict[int, float]] = []
            for sleeve_index, sleeve in enumerate(sleeves):
                fraction = _safe_float(sleeve.get("fraction"))
                target = max(10, int(round(selection_count * fraction))) if fraction > 0 else TARGET_COUNTS[universe]
                next_top_sleeves.append(_weights(
                    ranks[index], selection_mask, arrays["entry_eligible"][index], arrays["industry"][index],
                    target, last_top_sleeves[sleeve_index], True,
                ))
                next_bottom_sleeves.append(_weights(
                    ranks[index], selection_mask, arrays["entry_eligible"][index], arrays["industry"][index],
                    target, last_bottom_sleeves[sleeve_index], False,
                ))
            top = _blend_weights(next_top_sleeves, sleeves)
            bottom = _blend_weights(next_bottom_sleeves, sleeves)
            top_turnover = _turnover(last_top, top)
            bottom_turnover = _turnover(last_bottom, bottom)
            last_top_sleeves, last_bottom_sleeves = next_top_sleeves, next_bottom_sleeves
            last_top, last_bottom = top, bottom
            long_return = _weighted_return(returns, top) - cost_rate * top_turnover
            bottom_return = _weighted_return(returns, bottom) - cost_rate * bottom_turnover
            benchmark = float(np.mean(returns[sample])) if sample.any() else 0.0
            periods.append({
                "signal_date": dates[index], "date": dates[index + 1], "split": current_split,
                "rank_ic": rank_ic, "coverage": float(sample.sum() / max(eligible_count, 1)),
                "long_return": long_return, "benchmark_return": benchmark,
                "excess_return": long_return - benchmark, "long_short_return": long_return - bottom_return,
                "turnover": top_turnover, "group_returns": group_returns, "sample_count": int(sample.sum()),
            })
        metrics = {name: _metric_block([row for row in periods if row["split"] == name], frequency) for name in ("train", "valid", "test")}
        metrics["full"] = _metric_block(periods, frequency)
        nav = benchmark_nav = long_short_nav = 1.0
        nav_peak = benchmark_peak = long_short_peak = 1.0
        curve = []
        for row in periods:
            nav *= 1.0 + max(_safe_float(row["long_return"]), -0.999999)
            benchmark_nav *= 1.0 + max(_safe_float(row["benchmark_return"]), -0.999999)
            long_short_nav *= 1.0 + max(_safe_float(row["long_short_return"]), -0.999999)
            nav_peak, benchmark_peak, long_short_peak = max(nav_peak, nav), max(benchmark_peak, benchmark_nav), max(long_short_peak, long_short_nav)
            curve.append({"date": row["date"], "long_nav": nav, "benchmark_nav": benchmark_nav, "long_short_nav": long_short_nav,
                          "long_drawdown": nav / nav_peak - 1.0, "benchmark_drawdown": benchmark_nav / benchmark_peak - 1.0,
                          "long_short_drawdown": long_short_nav / long_short_peak - 1.0, "split": row["split"]})
        latest_index = len(dates) - 1
        latest_mask = arrays[member_key][latest_index] & arrays["quality"][latest_index] & np.isfinite(ranks[latest_index])
        latest_codes = np.where(latest_mask)[0]
        ordered = latest_codes[np.argsort(ranks[latest_index, latest_codes])] if len(latest_codes) else np.asarray([], dtype=int)

        def ranking(indices: Iterable[int]) -> List[Dict[str, Any]]:
            output = []
            for rank_number, code_index in enumerate(indices, start=1):
                industry_index = int(arrays["industry"][latest_index, code_index])
                output.append({"rank": rank_number, "ts_code": str(codes[code_index]), "stock_name": str(names[code_index]),
                               "industry_name": str(industry_names[industry_index]) if 0 <= industry_index < len(industry_names) else "未分类",
                               "factor_score": float(ranks[latest_index, code_index]),
                               "entry_eligible": None})
            return output

        required_periods = MIN_SPLIT_PERIODS[frequency]
        checks = {
            "enough_periods": all(
                int(round(metrics[name]["periods"])) >= required_periods[name]
                for name in ("train", "valid", "test")
            ),
            "train_rank_ic": metrics["train"]["rank_ic"] >= 0.015,
            "valid_rank_ic": metrics["valid"]["rank_ic"] >= 0.015,
            "test_rank_ic": metrics["test"]["rank_ic"] >= 0.015,
            "valid_excess": metrics["valid"]["excess_annual_return"] > 0.0,
            "test_excess": metrics["test"]["excess_annual_return"] > 0.0,
            "valid_test_same_direction": metrics["valid"]["rank_ic"] * metrics["test"]["rank_ic"] > 0.0,
            "coverage": min(metrics["valid"]["coverage"], metrics["test"]["coverage"]) >= 0.75,
        }
        universe_results[universe] = {
            "universe": universe, "universe_cn": UNIVERSE_CN[universe], "frequency": frequency,
            "frequency_cn": FREQUENCY_CN[frequency], "latest_signal_date": dates[-1],
            "orientation": "flipped_by_monthly_diagnostic_train" if orientation < 0 else "kept_by_monthly_diagnostic_train",
            "metrics": metrics, "checks": checks, "passed": all(checks.values()), "curve": curve,
            "ic_series": [{"date": row["signal_date"], "rank_ic": row["rank_ic"], "split": row["split"]} for row in periods],
            "annual": _annual(periods, frequency), "top10": ranking(ordered[::-1][:10]), "bottom10": ranking(ordered[:10]),
            "diagnostics": {
                "cost_rate_per_turnover": cost_rate,
                "portfolio_widths": sleeves,
                "target_count_latest": int(round(
                    int(latest_mask.sum()) * sum(_safe_float(row.get("fraction")) * _safe_float(row.get("weight")) for row in sleeves)
                )) if portfolio_widths else TARGET_COUNTS[universe],
                "industry_position_cap": 0.15,
                "buffer_multiple": 1.5,
                "minimum_split_periods": required_periods,
                "test_usage": "report_only",
            },
        }
    return universe_results, ranks


def build_diagnostics(
    result_path: Path,
    runtime_path: Optional[Path] = None,
    factor: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    cost_rate: float = 0.0015,
    workers: int = 4,
    miner_path: Optional[Path] = None,
) -> Dict[str, Any]:
    result_path = Path(result_path).resolve()
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    candidate = _candidate_row(result, factor)
    dsl = candidate.get("dsl") or {}
    if not dsl:
        raise RuntimeError("Selected factor has no executable DSL")
    miner = load_factor_miner(miner_path)
    database = Path(str(result.get("database") or "")).resolve()
    if not database.exists():
        raise FileNotFoundError(database)
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=120)
    latest = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0]
    end = str(end or latest)
    formal_split = _formal_split(result)
    split_source = "formal_factor_result" if formal_split else "diagnostic_60_20_20"
    evaluation_start = formal_split["train"]["start"] if formal_split else str(start or "20120101")
    data_start = str(start or (_warmup_start(evaluation_start) if formal_split else evaluation_start))
    dates = _trade_dates(conn, data_start, end)
    if len(dates) < 260:
        raise RuntimeError(f"Diagnostics require at least 260 trade dates, got {len(dates)}")
    frequency_dates = _frequency_dates(dates)
    frequency_sets = {key: set(values) for key, values in frequency_dates.items()}
    frequency_index = {key: {value: index for index, value in enumerate(values)} for key, values in frequency_dates.items()}
    split = formal_split or _split_boundaries(dates)
    actual_data_start = dates[0]
    warmup_trade_dates = sum(1 for date in dates if date < evaluation_start)
    csi2000_members_by_date, csi2000_membership_audit = _build_csi2000_membership(conn, miner, dates)
    financial, financial_dates = miner.load_financial(conn)
    codes = [str(row[0]) for row in conn.execute(
        "select distinct con_code from index_constituent_period where universe='ALL_A' and trade_date between ? and ? order by con_code",
        (data_start, end),
    )]
    if not codes:
        raise RuntimeError(f"Diagnostics found no ALL_A members between {data_start} and {end}")
    conn.close()
    code_index = {code: index for index, code in enumerate(codes)}
    names = ["" for _ in codes]
    industry_names: List[str] = []
    industry_index: Dict[str, int] = {}
    arrays = _empty_runtime(frequency_dates, codes)
    monthly_evaluator = StreamingDslEvaluator(miner)
    latest_monthly_scores: Dict[str, float] = {}
    neutralize = "neutral" in str(((candidate.get("metrics") or {}).get("preprocess") or {}).get("variant", "neutral"))
    portfolio_widths = _portfolio_widths(candidate)
    portfolio_selected_fraction = sum(row["fraction"] * row["weight"] for row in portfolio_widths)
    thread_state = threading.local()

    def snapshot(plan: Tuple[int, str, str]) -> Tuple[int, str, pd.DataFrame, Dict[str, set]]:
        position, date, next_date = plan
        worker_conn = getattr(thread_state, "conn", None)
        if worker_conn is None:
            worker_conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=120)
            thread_state.conn = worker_conn
        if date in frequency_sets["M"]:
            frame = miner.fetch_panel(worker_conn, "ALL_A", date, next_date, financial, financial_dates)
            frame = miner.materialize_base_features(frame)
        else:
            frame = _fetch_execution_panel(worker_conn, miner, date, next_date)
        memberships = {"ALL_A": set(frame["ts_code"].astype(str)) if not frame.empty else set()}
        member_cache = getattr(thread_state, "member_cache", None)
        if member_cache is None:
            member_cache = {}
            thread_state.member_cache = member_cache
        for universe in ("CSI800_ENH",):
            member_date = miner.latest_member_date(worker_conn, universe, date)
            key = (universe, str(member_date or ""))
            if key not in member_cache:
                member_cache[key] = {
                    str(code) for code, _ in miner.get_members(worker_conn, universe, date)
                } if member_date else set()
            memberships[universe] = member_cache[key]
        memberships["CSI2000_ENH"] = set(csi2000_members_by_date.get(date, frozenset()))
        return position, date, frame, memberships

    plans = [
        (index, date, dates[index + 1] if index + 1 < len(dates) else date)
        for index, date in enumerate(dates)
    ]
    with ThreadPoolExecutor(max_workers=max(1, min(8, int(workers)))) as executor:
        for _position, date, frame, memberships in executor.map(snapshot, plans):
            if frame.empty:
                continue
            frame["quality_eligible"] = _quality_mask(frame)
            for universe in UNIVERSES:
                frame[f"member_{universe}"] = frame["ts_code"].astype(str).isin(memberships[universe])
            columns = np.fromiter((code_index.get(str(code), -1) for code in frame["ts_code"]), dtype=np.int32)
            valid = columns >= 0
            target_columns = columns[valid]
            source = frame.loc[valid]
            if date in frequency_sets["M"]:
                raw = monthly_evaluator.evaluate(frame, dsl)
                prepared = _prepared_score(miner, frame, raw, neutralize)
                latest_monthly_scores = {
                    str(code): float(value)
                    for code, value in zip(frame["ts_code"].astype(str), prepared)
                    if np.isfinite(value)
                }
            for frequency in FREQUENCIES:
                if date not in frequency_sets[frequency]:
                    continue
                row_index = frequency_index[frequency][date]
                propagated = source["ts_code"].astype(str).map(latest_monthly_scores)
                arrays[frequency]["score"][row_index, target_columns] = propagated.to_numpy(dtype=np.float32)
                arrays[frequency]["px"][row_index, target_columns] = pd.to_numeric(source["px"], errors="coerce").to_numpy(dtype=np.float32)
                arrays[frequency]["entry"][row_index, target_columns] = pd.to_numeric(source["px_entry"], errors="coerce").to_numpy(dtype=np.float32)
                arrays[frequency]["entry_eligible"][row_index, target_columns] = source["execution_eligible"].fillna(False).to_numpy(dtype=bool)
                arrays[frequency]["quality"][row_index, target_columns] = source["quality_eligible"].fillna(False).to_numpy(dtype=bool)
                for universe in UNIVERSES:
                    arrays[frequency][f"member_{universe}"][row_index, target_columns] = source[f"member_{universe}"].fillna(False).to_numpy(dtype=bool)
                encoded_industries = []
                for name in source["industry_name"].fillna("UNCLASSIFIED").astype(str):
                    if name not in industry_index:
                        industry_index[name] = len(industry_names)
                        industry_names.append(name)
                    encoded_industries.append(industry_index[name])
                arrays[frequency]["industry"][row_index, target_columns] = np.asarray(encoded_industries, dtype=np.int16)
                for column, name in zip(target_columns, source["stock_name"].fillna("").astype(str)):
                    if name:
                        names[int(column)] = name

    results: Dict[str, Dict[str, Any]] = {universe: {} for universe in UNIVERSES}
    matrix: List[Dict[str, Any]] = []
    orientation = _training_orientation(frequency_dates["M"], arrays["M"], split)
    for frequency in FREQUENCIES:
        frequency_results, _ranks = _evaluate_frequency(
            frequency, frequency_dates[frequency], arrays[frequency], split, orientation, portfolio_widths,
            cost_rate, names, codes, industry_names,
        )
        for universe, block in frequency_results.items():
            results[universe][frequency] = block
            metrics = block["metrics"]
            matrix.append({"universe": universe, "universe_cn": UNIVERSE_CN[universe], "frequency": frequency,
                           "frequency_cn": FREQUENCY_CN[frequency], "passed": block["passed"],
                           "train_rank_ic": metrics["train"]["rank_ic"], "valid_rank_ic": metrics["valid"]["rank_ic"],
                           "test_rank_ic": metrics["test"]["rank_ic"],
                           "test_excess_annual_return": metrics["test"]["excess_annual_return"],
                           "test_sharpe": metrics["test"]["sharpe"], "test_max_drawdown": metrics["test"]["max_drawdown"]})
    expected_blocks = {(universe, frequency) for universe in UNIVERSES for frequency in FREQUENCIES}
    actual_blocks = {(row["universe"], row["frequency"]) for row in matrix}
    if actual_blocks != expected_blocks:
        missing = sorted(expected_blocks - actual_blocks)
        extra = sorted(actual_blocks - expected_blocks)
        raise RuntimeError(f"Diagnostics matrix contract failed: missing={missing}, extra={extra}")
    for universe, frequency in sorted(expected_blocks):
        block = results.get(universe, {}).get(frequency) or {}
        metrics = block.get("metrics") or {}
        empty_splits = [
            name for name in ("train", "valid", "test")
            if _safe_float((metrics.get(name) or {}).get("periods")) <= 0
        ]
        if empty_splits:
            raise RuntimeError(f"Diagnostics split contract failed for {universe}/{frequency}: {empty_splits}")
        if len(block.get("top10") or []) != 10 or len(block.get("bottom10") or []) != 10:
            raise RuntimeError(f"Diagnostics ranking contract failed for {universe}/{frequency}")
        if not block.get("curve") or not block.get("ic_series"):
            raise RuntimeError(f"Diagnostics curve contract failed for {universe}/{frequency}")
    structural_contract = {
        "passed": True,
        "expected_matrix_rows": len(expected_blocks),
        "actual_matrix_rows": len(matrix),
        "stock_code_count": len(codes),
        "all_splits_nonempty": True,
        "all_rankings_complete": True,
        "all_core_curves_nonempty": True,
    }
    runtime_path = Path(runtime_path or result_path.with_name(f"factor_diagnostics_{candidate.get('factor')}.npz")).resolve()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_payload: Dict[str, Any] = {
        "codes": np.asarray(codes), "names_json": np.asarray([json.dumps(dict(zip(codes, names)), ensure_ascii=False)]),
        "industry_names_json": np.asarray([json.dumps(industry_names, ensure_ascii=False)]),
        "split_json": np.asarray([json.dumps(split, ensure_ascii=True)]),
    }
    for frequency in FREQUENCIES:
        runtime_payload[f"dates_{frequency}"] = np.asarray(frequency_dates[frequency])
        for key, value in arrays[frequency].items():
            runtime_payload[f"{key}_{frequency}"] = value
    with tempfile.NamedTemporaryFile(prefix=runtime_path.name, suffix=".tmp.npz", dir=runtime_path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        np.savez_compressed(temp_path, **runtime_payload)
        temp_path.replace(runtime_path)
    finally:
        temp_path.unlink(missing_ok=True)
    diagnostics = {
        "status": "ready", "version": VERSION, "factor": candidate.get("factor"),
        "chinese_name": candidate.get("chinese_name") or candidate.get("name"),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"), "start": evaluation_start, "end": end,
        "data_requested_start": data_start, "data_warmup_start": actual_data_start,
        "warmup_trade_dates_before_evaluation": warmup_trade_dates, "split_source": split_source,
        "split": split, "matrix": matrix, "results": results, "runtime_file": runtime_path.name,
        "stock_options": [{"ts_code": code, "stock_name": name} for code, name in zip(codes, names) if name],
        "structural_contract": structural_contract,
        "integrity": {
            "same_executable_dsl": True, "signal_uses_close_or_earlier": True,
            "execution_is_next_tradable_open": True, "financials_are_visible_date_asof": True,
            "membership_is_signal_date_asof": True, "orientation_uses_train_only": True,
            "boundary_crossing_labels_are_purged": True, "test_not_used_for_formula": True,
            "unsupported_operators_fail_closed": True,
            "monthly_dsl_state_forward_filled_across_rebalance_frequencies": True,
            "same_formal_factor_split": bool(formal_split),
            "daily_execution_panel_uses_reduced_exact_fields": True,
            "csi2000_prelaunch_proxy_is_explicitly_labeled": True,
            "structural_contract_passed_before_result_write": True,
        },
        "signal_semantics": "original_monthly_dsl_state_forward_filled_to_daily_weekly_monthly_quarterly_rebalance_dates",
        "orientation": "flipped_by_monthly_diagnostic_train" if orientation < 0 else "kept_by_monthly_diagnostic_train",
        "portfolio_widths": portfolio_widths,
        "portfolio_selected_fraction": portfolio_selected_fraction,
        "portfolio_width_selection": "frozen_nested_train_two_fold_ensemble_from_factor_model",
        "quality_filter": ["exclude_st_delisting", "minimum_120_trading_days", "top_80pct_amount",
                           "top_95pct_circulating_market_value", "exclude_suspended", "entry_limit_guard"],
        "universe_membership_audit": {"CSI2000_ENH": csi2000_membership_audit},
        "cost_rate_per_turnover": cost_rate,
    }
    for collection in (result.get("leaderboard") or [], result.get("accepted_factors") or []):
        for row in collection:
            if str(row.get("factor")) == str(candidate.get("factor")):
                row["stock_diagnostics"] = diagnostics
    result.setdefault("stock_diagnostics", {})[str(candidate.get("factor"))] = diagnostics
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    temp_result = result_path.with_suffix(result_path.suffix + ".tmp")
    temp_result.write_text(encoded, encoding="utf-8")
    temp_result.replace(result_path)
    return diagnostics


def _stock_metric(periods: Sequence[Dict[str, Any]], frequency: str) -> Dict[str, Any]:
    if not periods:
        return {"periods": 0, "total_return": 0.0, "annual_return": 0.0, "buy_hold_annual_return": 0.0,
                "excess_annual_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "average_turnover": 0.0, "trade_count": 0, "invested_ratio": 0.0}
    strategy = np.asarray([_safe_float(row["strategy_return"]) for row in periods], dtype=float)
    buy_hold = np.asarray([_safe_float(row["buy_hold_return"]) for row in periods], dtype=float)
    strategy_nav = np.cumprod(1.0 + np.maximum(strategy, -0.999999))
    buy_hold_nav = np.cumprod(1.0 + np.maximum(buy_hold, -0.999999))
    ppy = PERIODS_PER_YEAR[frequency]
    annual = float(strategy_nav[-1] ** (ppy / len(strategy_nav)) - 1.0) if strategy_nav[-1] > 0 else -1.0
    buy_annual = float(buy_hold_nav[-1] ** (ppy / len(buy_hold_nav)) - 1.0) if buy_hold_nav[-1] > 0 else -1.0
    std = float(np.std(strategy, ddof=1)) if len(strategy) > 1 else 0.0
    turnovers = np.asarray([_safe_float(row["turnover"]) for row in periods], dtype=float)
    positions = np.asarray([_safe_float(row["position"]) for row in periods], dtype=float)
    return {"periods": len(periods), "total_return": float(strategy_nav[-1] - 1.0), "annual_return": annual,
            "buy_hold_annual_return": buy_annual, "excess_annual_return": annual - buy_annual,
            "sharpe": float(np.mean(strategy) / std * math.sqrt(ppy)) if std > 1e-12 else 0.0,
            "max_drawdown": _max_drawdown(strategy_nav.tolist()), "average_turnover": float(np.mean(turnovers)),
            "trade_count": int(np.sum(turnovers > 0)), "invested_ratio": float(np.mean(positions))}


def stock_payload(runtime_path: Path, diagnostics: Dict[str, Any], code: str, universe: str, frequency: str) -> Dict[str, Any]:
    universe = universe if universe in UNIVERSES else "ALL_A"
    frequency = frequency if frequency in FREQUENCIES else "W"
    data = np.load(Path(runtime_path), allow_pickle=False)
    codes = data["codes"].astype(str)
    matches = np.where(codes == str(code))[0]
    if not len(matches):
        raise KeyError(f"Unknown stock code: {code}")
    column = int(matches[0])
    names = json.loads(str(data["names_json"][0]))
    dates = data[f"dates_{frequency}"].astype(str)
    scores = data[f"rank_{frequency}"][:, column].astype(float)
    prices = data[f"px_{frequency}"][:, column].astype(float)
    entries = data[f"entry_{frequency}"][:, column].astype(float)
    eligible = data[f"entry_eligible_{frequency}"][:, column].astype(bool)
    quality = data[f"quality_{frequency}"][:, column].astype(bool)
    member = data[f"member_{universe}_{frequency}"][:, column].astype(bool)
    valid = np.isfinite(scores) & np.isfinite(prices)
    series = [[str(date), float(score), float(price)] for date, score, price in zip(dates[valid], scores[valid], prices[valid])]
    split = diagnostics.get("split") or json.loads(str(data["split_json"][0]))
    approximate_sizes = {"ALL_A": 5000.0, "CSI800_ENH": 800.0, "CSI2000_ENH": 2000.0}
    default_fraction = TARGET_COUNTS[universe] / approximate_sizes[universe]
    selection_fraction = min(0.30, max(0.01, _safe_float(diagnostics.get("portfolio_selected_fraction"), default_fraction)))
    entry_threshold = 1.0 - selection_fraction
    exit_threshold = max(0.0, entry_threshold - min(0.15, selection_fraction * 0.5))
    position = 0.0
    active_split: Optional[str] = None
    strategy_nav = buy_nav = 1.0
    strategy_peak = buy_peak = 1.0
    curve: List[List[Any]] = []
    periods: List[Dict[str, Any]] = []
    actions: Dict[str, Tuple[float, str]] = {}
    cost_rate = _safe_float(diagnostics.get("cost_rate_per_turnover"), 0.0015)
    for index in range(len(dates) - 1):
        current_split = _period_split(str(dates[index]), str(dates[index + 1]), split)
        if current_split is None:
            continue
        if current_split != active_split:
            position = 0.0
            active_split = current_split
        start_price = entries[index] if eligible[index] else prices[index]
        if not (np.isfinite(scores[index]) and np.isfinite(start_price) and start_price > 0):
            continue
        desired = position
        valid_scope = bool(member[index] and quality[index])
        if valid_scope and position <= 0 and scores[index] >= entry_threshold:
            desired = 1.0
        elif (not valid_scope) or (position > 0 and scores[index] <= exit_threshold):
            desired = 0.0
        action = "持有" if position > 0 else "空仓"
        new_position = position
        if desired != position:
            if eligible[index]:
                new_position = desired
                action = "买入" if desired > position else "卖出"
            else:
                action = "交易受限"
        turnover = abs(new_position - position)
        position = new_position
        actions[str(dates[index])] = (position, action)
        exit_price = entries[index + 1] if eligible[index + 1] else prices[index + 1]
        if not np.isfinite(exit_price) or exit_price <= 0:
            continue
        raw_return = float(exit_price / start_price - 1.0)
        strategy_return = float(position * raw_return - turnover * cost_rate)
        periods.append({"date": str(dates[index + 1]), "signal_date": str(dates[index]), "split": current_split,
                        "strategy_return": strategy_return, "buy_hold_return": raw_return, "turnover": turnover,
                        "position": position, "score": scores[index], "action": action})
        strategy_nav *= 1.0 + max(strategy_return, -0.999999)
        buy_nav *= 1.0 + max(raw_return, -0.999999)
        strategy_peak, buy_peak = max(strategy_peak, strategy_nav), max(buy_peak, buy_nav)
        curve.append([str(dates[index + 1]), strategy_nav, buy_nav, strategy_nav / strategy_peak - 1.0,
                      buy_nav / buy_peak - 1.0, position, float(scores[index]), action, current_split])
    enriched = [row + [actions.get(str(row[0]), (0.0, "空仓"))[0], actions.get(str(row[0]), (0.0, "空仓"))[1]] for row in series]
    metrics = {name: _stock_metric(periods if name == "full" else [row for row in periods if row["split"] == name], frequency)
               for name in ("train", "valid", "test", "full")}
    return {"ts_code": str(code), "stock_name": names.get(str(code), ""), "universe": universe,
            "universe_cn": UNIVERSE_CN[universe], "frequency": frequency, "frequency_cn": FREQUENCY_CN[frequency],
            "series": enriched, "strategy_curve": curve, "strategy_metrics": metrics,
            "signal_policy": {"entry_threshold": entry_threshold, "exit_threshold": exit_threshold,
                              "cost_rate_per_turnover": cost_rate, "execution": "signal_close_then_next_tradable_open",
                              "membership_is_signal_date_asof": True, "boundary_crossing_labels_are_purged": True,
                              "split_positions_are_reset": True, "outside_split_signals_are_ignored": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--runtime")
    parser.add_argument("--factor")
    parser.add_argument("--start", default=os.environ.get("FACTOR_DIAGNOSTICS_START") or None)
    parser.add_argument("--end")
    parser.add_argument("--cost-rate", type=float, default=float(os.environ.get("FACTOR_DIAGNOSTICS_COST_RATE", "0.0015")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("FACTOR_DIAGNOSTICS_WORKERS", "4")))
    parser.add_argument("--miner")
    args = parser.parse_args()
    output = build_diagnostics(Path(args.result), Path(args.runtime) if args.runtime else None, args.factor, args.start, args.end,
                               args.cost_rate, args.workers, Path(args.miner) if args.miner else None)
    print(json.dumps({"status": output.get("status"), "version": output.get("version"), "factor": output.get("factor"),
                      "matrix_rows": len(output.get("matrix") or [])}, ensure_ascii=True))


if __name__ == "__main__":
    main()
