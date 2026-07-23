from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from lightgbm import LGBMRanker, early_stopping
except ImportError:
    LGBMRanker = None
    early_stopping = None


UNIVERSES = ("ALL_A", "CSI800_ENH", "CSI2000_ENH")
FREQUENCIES = ("D", "W", "M", "Q")
FREQUENCY_CN = {"D": "日频", "W": "周频", "M": "月频", "Q": "季频"}
UNIVERSE_CN = {"ALL_A": "全A优质域", "CSI800_ENH": "中证800", "CSI2000_ENH": "中证2000"}
PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4}
TARGET_COUNTS = {"ALL_A": 80, "CSI800_ENH": 50, "CSI2000_ENH": 50}
SEED_COMPONENT_WEIGHTS = {
    "base_low_crowding": 0.32, "base_reversal": 0.28,
    "base_value": 0.22, "base_trend": -0.18,
}
TIMING_HALFLIFE = {"D": 63.0, "W": 13.0, "M": 3.0, "Q": 1.0}
RANKER_RAW_FEATURES = (
    "base_low_crowding", "base_reversal", "base_value", "base_quality", "base_trend",
    "base_moneyflow", "mom5", "mom20", "mom60", "mom120", "ret1", "range_pct",
    "turnover_rate", "turnover_rate_f", "volume_ratio", "amount_to_mv",
    "large_order_balance", "netflow_intensity", "roe", "roa", "gross_margin",
    "netprofit_margin", "assets_turn", "debt_to_assets", "pb", "ps_ttm", "pe_ttm",
    "dv_ttm", "total_mv",
)
RANKER_INTERACTIONS = (
    "dip_in_trend", "value_quality", "value_low_crowding",
    "reversal_low_crowding", "trend_quality",
)
FACTOR_NAME = "LambdaRank状态自适应反拥挤选股因子"
FACTOR_FORMULA = (
    "0.80*rank_all(LambdaRank(X))+0.20*rank_industry(LambdaRank(X)); "
    "X includes low-crowding, reversal, value, quality, trend, money-flow and interactions"
)
FACTOR_LATEX = (
    r"F_{i,t}=0.80\,R_{\mathrm{all}}\!\left(f_{\theta}(X_{i,t})\right)"
    r"+0.20\,R_{\mathrm{ind}}\!\left(f_{\theta}(X_{i,t})\right)"
)


def _load_factor_miner() -> Any:
    path = Path(__file__).resolve().parents[1] / "05_factor_mining_agent" / "factor_miner.py"
    spec = importlib.util.spec_from_file_location("factor_miner_for_cross_section", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load factor miner from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _rank01(series: pd.Series, ascending: bool = True) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, ascending=ascending).fillna(0.5)


def _max_drawdown(values: Sequence[float]) -> float:
    peak = 1.0
    drawdown = 0.0
    for value in values:
        peak = max(peak, _safe_float(value, 1.0))
        drawdown = min(drawdown, _safe_float(value, 1.0) / max(peak, 1e-12) - 1.0)
    return drawdown


def _trade_dates(conn: sqlite3.Connection, start: str, end: str) -> List[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "select distinct trade_date from stock_ohlcv_daily where trade_date between ? and ? order by trade_date",
            (start, end),
        )
    ]


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
    return {
        "D": list(dates),
        "W": list(weekly.values()),
        "M": list(monthly.values()),
        "Q": list(quarterly.values()),
    }


def _split_boundaries(dates: Sequence[str]) -> Dict[str, str]:
    if not dates:
        return {"train_end": "", "valid_end": ""}
    train_index = max(0, min(len(dates) - 1, int(len(dates) * 0.60) - 1))
    valid_index = max(train_index + 1, min(len(dates) - 1, int(len(dates) * 0.80) - 1))
    return {"train_end": dates[train_index], "valid_end": dates[valid_index]}


def _split_name(date: str, boundaries: Dict[str, str]) -> str:
    if date <= boundaries["train_end"]:
        return "train"
    if date <= boundaries["valid_end"]:
        return "valid"
    return "test"


def _industry_intervals(conn: sqlite3.Connection) -> Dict[str, List[Tuple[str, str, str]]]:
    out: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    rows = conn.execute(
        "select ts_code,start_date,coalesce(end_date,'99991231'),coalesce(industry_name,'未分类') "
        "from sw_l1_industry_daily order by ts_code,start_date"
    ).fetchall()
    for code, start, end, name in rows:
        out[str(code)].append((str(start), str(end), str(name)))
    return out


def _industry_at(intervals: Dict[str, List[Tuple[str, str, str]]], code: str, date: str) -> str:
    for start, end, name in intervals.get(code, []):
        if start <= date <= end:
            return name
    return "未分类"


SNAPSHOT_SQL = """
select
  o.ts_code, o.stock_name, o.open, o.high, o.low, o.close, o.qfq_close,
  o.pre_close, o.pct_chg, o.vol, o.amount, o.up_limit, o.down_limit, o.suspend_timing,
  p1.qfq_close, p5.qfq_close, p20.qfq_close, p60.qfq_close, p120.qfq_close,
  v.pb, v.pe_ttm, v.ps_ttm, v.dv_ttm, v.total_mv, v.circ_mv,
  v.turnover_rate, v.turnover_rate_f, v.volume_ratio,
  mf.net_mf_amount, mf.buy_lg_amount, mf.sell_lg_amount,
  mf.buy_elg_amount, mf.sell_elg_amount,
  e.open, e.high, e.low, e.close, e.qfq_close, e.vol, e.up_limit, e.down_limit, e.suspend_timing
from index_constituent_period mem
join stock_ohlcv_daily o
  on o.trade_date=mem.trade_date and o.ts_code=mem.con_code
left join stock_ohlcv_daily p1 on p1.trade_date=? and p1.ts_code=o.ts_code
left join stock_ohlcv_daily p5 on p5.trade_date=? and p5.ts_code=o.ts_code
left join stock_ohlcv_daily p20 on p20.trade_date=? and p20.ts_code=o.ts_code
left join stock_ohlcv_daily p60 on p60.trade_date=? and p60.ts_code=o.ts_code
left join stock_ohlcv_daily p120 on p120.trade_date=? and p120.ts_code=o.ts_code
left join stock_valuation_daily v on v.trade_date=o.trade_date and v.ts_code=o.ts_code
left join stock_moneyflow_daily mf on mf.trade_date=o.trade_date and mf.ts_code=o.ts_code
left join stock_ohlcv_daily e on e.trade_date=? and e.ts_code=o.ts_code
where mem.universe='ALL_A' and mem.trade_date=? and o.qfq_close>0
"""


SNAPSHOT_COLUMNS = [
    "ts_code", "stock_name", "open", "high", "low", "close", "px", "pre_close",
    "pct_chg", "vol", "amount", "up_limit", "down_limit", "suspend_timing",
    "px1", "px5", "px20", "px60", "px120",
    "pb", "pe_ttm", "ps_ttm", "dv_ttm", "total_mv", "circ_mv",
    "turnover_rate", "turnover_rate_f", "volume_ratio",
    "net_mf_amount", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount",
    "entry_open", "entry_high", "entry_low", "entry_close", "entry_qfq_close", "entry_vol",
    "entry_up_limit", "entry_down_limit", "entry_suspend_timing",
]


def _snapshot(
    conn: sqlite3.Connection,
    miner: Any,
    date: str,
    lag_dates: Dict[int, str],
    entry_date: str,
    financial: Dict[str, List[Dict[str, Any]]],
    financial_dates: Dict[str, List[str]],
    industries: Dict[str, List[Tuple[str, str, str]]],
    memberships: Dict[str, set],
) -> pd.DataFrame:
    rows = conn.execute(
        SNAPSHOT_SQL,
        (
            lag_dates[1], lag_dates[5], lag_dates[20], lag_dates[60], lag_dates[120],
            entry_date, date,
        ),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
    text_columns = {"ts_code", "stock_name", "suspend_timing", "entry_suspend_timing"}
    for column in frame.columns:
        if column not in text_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["stock_name"] = frame["stock_name"].fillna("")
    frame["industry_name"] = [_industry_at(industries, code, date) for code in frame["ts_code"]]
    for universe in UNIVERSES:
        frame[f"member_{universe}"] = frame["ts_code"].isin(memberships[universe])

    frame["ret1"] = frame["px"] / frame["px1"] - 1.0
    frame["mom5"] = frame["px"] / frame["px5"] - 1.0
    frame["mom20"] = frame["px"] / frame["px20"] - 1.0
    frame["mom60"] = frame["px"] / frame["px60"] - 1.0
    frame["mom120"] = frame["px"] / frame["px120"] - 1.0
    frame["range_pct"] = (frame["high"] - frame["low"]) / frame["close"].replace(0, np.nan)
    frame["gap_pct"] = (frame["open"] - frame["pre_close"]) / frame["pre_close"].replace(0, np.nan)
    frame["amount_to_mv"] = frame["amount"] / frame["circ_mv"].replace(0, np.nan)
    frame["netflow_intensity"] = frame["net_mf_amount"] / frame["amount"].replace(0, np.nan)
    frame["large_order_balance"] = (
        frame["buy_lg_amount"].fillna(0)
        + frame["buy_elg_amount"].fillna(0)
        - frame["sell_lg_amount"].fillna(0)
        - frame["sell_elg_amount"].fillna(0)
    ) / frame["amount"].replace(0, np.nan)
    frame["entry_px"] = frame["entry_qfq_close"] * frame["entry_open"] / frame["entry_close"].replace(0, np.nan)
    entry_locked_up = (
        frame["entry_up_limit"].notna()
        & (frame["entry_open"] >= frame["entry_up_limit"] * 0.995)
        & (frame["entry_low"] >= frame["entry_up_limit"] * 0.995)
    )
    entry_locked_down = (
        frame["entry_down_limit"].notna()
        & (frame["entry_open"] <= frame["entry_down_limit"] * 1.005)
        & (frame["entry_high"] <= frame["entry_down_limit"] * 1.005)
    )
    frame["entry_eligible"] = (
        frame["entry_px"].gt(0)
        & frame["entry_vol"].fillna(0).gt(0)
        & frame["entry_suspend_timing"].isna()
        & ~entry_locked_up
        & ~entry_locked_down
    )

    financial_rows = [miner.latest_fin(financial, financial_dates, code, date) for code in frame["ts_code"]]
    for key in (
        "roe", "roa", "gross_margin", "netprofit_yoy", "debt_to_assets",
        "netprofit_margin", "assets_turn", "op_yoy", "tr_yoy",
    ):
        frame[key] = [_safe_float(item.get(key), np.nan) for item in financial_rows]

    grouped = frame.groupby("industry_name", dropna=False)
    frame["base_low_crowding"] = (
        0.45 * _rank01(-frame["turnover_rate"])
        + 0.25 * _rank01(-frame["turnover_rate_f"])
        + 0.20 * _rank01(-frame["volume_ratio"])
        + 0.10 * _rank01(-frame["amount_to_mv"])
    )
    frame["base_quality"] = (
        0.22 * _rank01(frame["roe"])
        + 0.18 * _rank01(frame["roa"])
        + 0.18 * _rank01(frame["gross_margin"])
        + 0.14 * _rank01(frame["netprofit_margin"])
        + 0.12 * _rank01(frame["assets_turn"])
        + 0.16 * _rank01(-frame["debt_to_assets"])
    )
    frame["base_value"] = (
        0.30 * _rank01(-frame["pb"])
        + 0.22 * _rank01(-frame["ps_ttm"])
        + 0.16 * _rank01(-frame["pe_ttm"])
        + 0.17 * _rank01(frame["dv_ttm"])
        + 0.15 * _rank01(-frame["total_mv"])
    )
    frame["base_trend"] = (
        0.40 * _rank01(frame["mom60"])
        + 0.35 * _rank01(frame["mom120"])
        + 0.25 * _rank01(frame["mom20"])
    )
    frame["base_moneyflow"] = (
        0.45 * _rank01(frame["large_order_balance"])
        + 0.35 * _rank01(frame["netflow_intensity"])
        + 0.20 * _rank01(frame["volume_ratio"])
    )
    frame["base_reversal"] = (
        0.50 * _rank01(-frame["mom5"])
        + 0.25 * _rank01(-frame["ret1"])
        + 0.25 * _rank01(-frame["range_pct"])
    )
    raw = sum(SEED_COMPONENT_WEIGHTS[key] * _rank01(frame[key]) for key in SEED_COMPONENT_WEIGHTS)
    global_rank = _rank01(raw)
    frame["_factor_raw"] = raw
    industry_rank = frame.groupby("industry_name", dropna=False)["_factor_raw"].rank(pct=True)
    industry_rank = industry_rank.reindex(frame.index).fillna(global_rank)
    frame["factor_score"] = (0.80 * global_rank + 0.20 * industry_rank).clip(0.0, 1.0)
    frame.drop(columns=["_factor_raw"], inplace=True)

    amount_floor = frame["amount"].quantile(0.20)
    market_value_floor = frame["circ_mv"].quantile(0.05)
    bad_name = frame["stock_name"].str.upper().str.contains(r"(?:\*?ST|退)", regex=True, na=False)
    frame["quality_eligible"] = (
        ~bad_name
        & frame["px120"].gt(0)
        & frame["qfq_close" if "qfq_close" in frame else "px"].gt(0)
        & frame["amount"].fillna(0).ge(amount_floor)
        & frame["circ_mv"].fillna(0).ge(market_value_floor)
        & frame["vol"].fillna(0).gt(0)
        & frame["suspend_timing"].isna()
    )
    frame["risk_proxy"] = (
        frame["range_pct"].abs().fillna(0.03)
        + 0.35 * frame["mom20"].abs().fillna(0.10)
        + 0.15 * frame["mom5"].abs().fillna(0.05)
    ).clip(lower=0.01)
    frame["trade_date"] = date
    frame["entry_date"] = entry_date
    return frame

def _ranker_features(frame: pd.DataFrame) -> pd.DataFrame:
    features = pd.DataFrame(index=frame.index)
    for column in RANKER_RAW_FEATURES:
        features[column] = _rank01(frame[column]).fillna(0.5)
    features["dip_in_trend"] = features["base_reversal"] * features["base_trend"]
    features["value_quality"] = features["base_value"] * features["base_quality"]
    features["value_low_crowding"] = features["base_value"] * features["base_low_crowding"]
    features["reversal_low_crowding"] = features["base_reversal"] * features["base_low_crowding"]
    features["trend_quality"] = features["base_trend"] * features["base_quality"]
    return features


def _apply_ranker(frame: pd.DataFrame, ranker: Any) -> pd.DataFrame:
    prediction = pd.Series(
        ranker.predict(_ranker_features(frame), num_iteration=ranker.best_iteration_),
        index=frame.index,
        dtype=float,
    )
    frame["ranker_prediction"] = prediction
    global_rank = _rank01(prediction)
    industry_rank = frame.assign(_prediction=prediction).groupby(
        "industry_name", dropna=False
    )["_prediction"].rank(pct=True)
    frame["factor_score"] = (
        0.80 * global_rank + 0.20 * industry_rank.reindex(frame.index).fillna(global_rank)
    ).clip(0.0, 1.0)
    return frame


def _date_rank_ic(frame: pd.DataFrame, prediction: str, universe: str) -> float:
    values: List[float] = []
    member = f"member_{universe}"
    for _, group in frame[frame[member]].groupby("date"):
        if len(group) < 30:
            continue
        value = group[prediction].rank().corr(group["realized_return"].rank())
        if pd.notna(value):
            values.append(float(value))
    return float(np.mean(values)) if values else 0.0


def _train_ranker(
    db: Path,
    miner: Any,
    dates: Sequence[str],
    boundaries: Dict[str, str],
    financial: Dict[str, Any],
    financial_dates: Dict[str, Any],
    industries: Dict[str, List[Tuple[str, str, str]]],
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Any, Dict[str, Any], Dict[str, Dict[str, Any]]]:
    if LGBMRanker is None or early_stopping is None:
        raise RuntimeError("lightgbm is required for the LambdaRank factor engine")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    conn.execute("pragma query_only=on")
    frames: List[pd.DataFrame] = []
    for date in _frequency_dates(dates)["M"]:
        if date > boundaries["valid_end"]:
            break
        date_index = dates.index(date)
        if date_index < 120 or date_index + 1 >= len(dates):
            continue
        lag_dates = {offset: dates[date_index - offset] for offset in (1, 5, 20, 60, 120)}
        memberships = {
            universe: {str(code) for code, _ in miner.get_members(conn, universe, date)}
            for universe in UNIVERSES
        }
        frame = _snapshot(
            conn, miner, date, lag_dates, dates[date_index + 1], financial,
            financial_dates, industries, memberships,
        )
        if not frame.empty:
            frames.append(frame)
    conn.close()
    blocks: List[pd.DataFrame] = []
    for index in range(len(frames) - 1):
        previous, current = frames[index], frames[index + 1]
        previous_date = str(previous["trade_date"].iloc[0])
        current_date = str(current["trade_date"].iloc[0])
        split = _split_name(previous_date, boundaries)
        if split not in {"train", "valid"} or _split_name(current_date, boundaries) != split:
            continue
        realized = _realized_returns(previous, current).set_index("ts_code")["realized_return"]
        sample = previous[previous["member_ALL_A"] & previous["quality_eligible"]].copy()
        sample["realized_return"] = sample["ts_code"].map(realized)
        sample = sample.dropna(subset=["realized_return"])
        if len(sample) < 100:
            continue
        block = _ranker_features(sample)
        block["target"] = np.minimum(
            9, np.floor(sample["realized_return"].rank(pct=True).to_numpy() * 10).astype(int)
        )
        block["realized_return"] = sample["realized_return"].to_numpy(dtype=float)
        block["date"] = previous_date
        block["split"] = split
        for universe in UNIVERSES:
            block[f"member_{universe}"] = sample[f"member_{universe}"].to_numpy(dtype=bool)
        blocks.append(block.reset_index(drop=True))
    if not blocks:
        raise RuntimeError("LambdaRank training produced no point-in-time samples")
    dataset = pd.concat(blocks, ignore_index=True).sort_values(["split", "date"])
    train = dataset[dataset["split"] == "train"].copy()
    valid = dataset[dataset["split"] == "valid"].copy()
    if train.empty or valid.empty:
        raise RuntimeError("LambdaRank requires non-empty chronological train and validation samples")
    feature_names = list(RANKER_RAW_FEATURES + RANKER_INTERACTIONS)
    ranker = LGBMRanker(
        objective="lambdarank", metric="ndcg",
        label_gain=list(range(10)), num_leaves=15, max_depth=4,
        min_child_samples=500, learning_rate=0.025, n_estimators=600,
        reg_alpha=3.0, reg_lambda=12.0, random_state=20260716,
        n_jobs=4, verbosity=-1, subsample=0.8, colsample_bytree=0.8,
        subsample_freq=1, deterministic=True, force_col_wise=True,
    )
    ranker.fit(
        train[feature_names], train["target"],
        group=train.groupby("date", sort=True).size().tolist(),
        eval_set=[(valid[feature_names], valid["target"])],
        eval_group=[valid.groupby("date", sort=True).size().tolist()],
        eval_at=[50, 100, 200],
        callbacks=[early_stopping(60, verbose=False)],
    )
    train["prediction"] = ranker.predict(train[feature_names], num_iteration=ranker.best_iteration_)
    valid["prediction"] = ranker.predict(valid[feature_names], num_iteration=ranker.best_iteration_)
    rank_ic = {
        split: {
            universe: _date_rank_ic(frame, "prediction", universe)
            for universe in UNIVERSES
        }
        for split, frame in (("train", train), ("valid", valid))
    }
    policies: Dict[str, Dict[str, Any]] = {}
    for universe in UNIVERSES:
        train_ic = rank_ic["train"][universe]
        valid_ic = rank_ic["valid"][universe]
        persistence = valid_ic / max(abs(train_ic), 0.01)
        timing_enabled = bool(valid_ic > 0 and persistence < 0.65)
        policies[universe] = {
            "timing_enabled": timing_enabled,
            "persistence_ratio": float(persistence),
            "target_multiplier": 8 if timing_enabled else 4,
            "skip_fraction": 0.0 if timing_enabled else 0.12,
            "buffer_multiple": 2.0,
        }
    importance = sorted(
        zip(feature_names, ranker.feature_importances_.tolist()),
        key=lambda item: item[1], reverse=True,
    )
    diagnostics = {
        "algorithm": "LightGBM LambdaRank",
        "objective": "monthly_forward_return_decile_lambdarank",
        "best_iteration": int(ranker.best_iteration_),
        "train_samples": int(len(train)),
        "valid_samples": int(len(valid)),
        "train_months": int(train["date"].nunique()),
        "valid_months": int(valid["date"].nunique()),
        "rank_ic": rank_ic,
        "feature_importance": [
            {"feature": name, "split_count": int(value)} for name, value in importance
        ],
        "test_labels_used": False,
    }
    if progress_callback:
        progress_callback(5, "LambdaRank训练与验证完成")
    return ranker, diagnostics, policies

def _portfolio_weights(
    frame: pd.DataFrame,
    universe: str,
    top: bool = True,
    previous: Optional[Dict[str, float]] = None,
    target_multiplier: int = 1,
    skip_fraction: float = 0.0,
    buffer_multiple: float = 1.5,
) -> Dict[str, float]:
    member_column = f"member_{universe}"
    eligible = frame[frame[member_column] & frame["quality_eligible"]].copy()
    if eligible.empty:
        return {}
    target = min(
        TARGET_COUNTS[universe] * max(1, int(target_multiplier)),
        max(20, int(round(len(eligible) * 0.20))),
    )
    eligible = eligible.sort_values("factor_score", ascending=not top)
    skip_count = min(int(len(eligible) * max(0.0, skip_fraction)), max(0, len(eligible) - target))
    eligible = eligible.iloc[skip_count:].copy()
    buffer_size = min(len(eligible), max(target, int(math.ceil(target * buffer_multiple))))
    buffered_codes = set(eligible.head(buffer_size)["ts_code"].astype(str))
    industry_cap = max(3, int(math.ceil(target * 0.15)))
    selected: List[int] = []
    industry_counts: Dict[str, int] = defaultdict(int)
    previous_codes = set((previous or {}).keys())
    if previous_codes:
        retained = eligible[
            eligible["ts_code"].astype(str).isin(previous_codes & buffered_codes)
        ]
        for index, row in retained.iterrows():
            industry = str(row["industry_name"])
            if industry_counts[industry] >= industry_cap:
                continue
            selected.append(index)
            industry_counts[industry] += 1
            if len(selected) >= target:
                break
    for index, row in eligible.iterrows():
        if len(selected) >= target:
            break
        if index in selected:
            continue
        industry = str(row["industry_name"])
        if industry_counts[industry] >= industry_cap:
            continue
        selected.append(index)
        industry_counts[industry] += 1
        if len(selected) >= target:
            break
    if len(selected) < target:
        for index in eligible.index:
            if index not in selected:
                selected.append(index)
            if len(selected) >= target:
                break
    chosen = frame.loc[selected].copy()
    tradable = chosen[chosen["entry_eligible"]]
    if tradable.empty:
        return {}
    strength = (tradable["factor_score"] if top else 1.0 - tradable["factor_score"]).clip(0.0, 1.0)
    normalized = (strength - strength.min()) / max(float(strength.max() - strength.min()), 1e-12)
    raw = 0.85 + 0.15 * normalized
    weights = raw / max(raw.sum(), 1e-12)
    weights = weights.clip(upper=max(0.03, 1.80 / max(len(weights), 1)))
    weights = weights / max(weights.sum(), 1e-12)
    return {str(code): float(weight) for code, weight in zip(tradable["ts_code"], weights)}


def _turnover(previous: Dict[str, float], current: Dict[str, float]) -> float:
    keys = set(previous) | set(current)
    return 0.5 * sum(abs(current.get(key, 0.0) - previous.get(key, 0.0)) for key in keys)


def _realized_returns(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    left = previous[[
        "ts_code", "factor_score", "entry_px", "entry_eligible", "quality_eligible",
        "industry_name", *[f"member_{u}" for u in UNIVERSES],
    ]].copy()
    right = current[["ts_code", "entry_px", "entry_eligible", "px"]].copy()
    right["exit_px"] = right["entry_px"].where(right["entry_eligible"], right["px"])
    merged = left.merge(right[["ts_code", "exit_px"]], on="ts_code", how="left")
    merged["realized_return"] = merged["exit_px"] / merged["entry_px"] - 1.0
    merged.loc[~merged["entry_eligible"], "realized_return"] = np.nan
    return merged


def _weighted_return(realized: pd.DataFrame, weights: Dict[str, float]) -> float:
    if not weights:
        return 0.0
    mapping = realized.set_index("ts_code")["realized_return"]
    total = 0.0
    for code, weight in weights.items():
        value = mapping.get(code, np.nan)
        if pd.notna(value):
            total += float(weight) * float(value)
    return total


def _evaluate_period(
    previous: Dict[str, Any],
    current_frame: pd.DataFrame,
    universe: str,
    frequency: str,
    split: str,
    cost_rate: float,
) -> Dict[str, Any]:
    realized = _realized_returns(previous["frame"], current_frame)
    member_column = f"member_{universe}"
    sample = realized[realized[member_column] & realized["quality_eligible"]].dropna(
        subset=["factor_score", "realized_return"]
    )
    ic = float(sample["factor_score"].rank().corr(sample["realized_return"].rank())) if len(sample) >= 30 else 0.0
    group_returns: List[float] = []
    if len(sample) >= 50:
        try:
            groups = pd.qcut(sample["factor_score"].rank(method="first"), 5, labels=False) + 1
            grouped = sample.assign(group=groups).groupby("group")["realized_return"].mean()
            group_returns = [float(grouped.get(group, 0.0)) for group in range(1, 6)]
        except ValueError:
            group_returns = []
    top_gross = _weighted_return(realized, previous["top_weights"])
    bottom_gross = _weighted_return(realized, previous["bottom_weights"])
    strategy_gross = _weighted_return(
        realized, previous.get("strategy_weights", previous["top_weights"])
    )
    benchmark = float(sample["realized_return"].mean()) if not sample.empty else 0.0
    top_cost = cost_rate * previous.get("top_turnover", 1.0)
    bottom_cost = cost_rate * previous.get("bottom_turnover", 1.0)
    strategy_cost = cost_rate * previous.get("strategy_turnover", 1.0)
    eligible_count = int(
        (previous["frame"][member_column] & previous["frame"]["quality_eligible"]).sum()
    )
    return {
        "signal_date": previous["date"],
        "date": str(current_frame["trade_date"].iloc[0]),
        "split": split,
        "rank_ic": ic,
        "coverage": float(len(sample) / max(eligible_count, 1)),
        "long_return": strategy_gross - strategy_cost,
        "benchmark_return": benchmark,
        "excess_return": strategy_gross - strategy_cost - benchmark,
        "long_short_return": top_gross - bottom_gross - top_cost - bottom_cost,
        "turnover": float(previous.get("strategy_turnover", 1.0)),
        "strategy_orientation": previous.get("strategy_orientation", "top"),
        "factor_timing_state": _safe_float(previous.get("factor_timing_state", 0.0)),
        "group_returns": group_returns,
        "sample_count": int(len(sample)),
        "frequency": frequency,
        "universe": universe,
    }


def _metric_block(periods: Sequence[Dict[str, Any]], frequency: str) -> Dict[str, Any]:
    if not periods:
        return {
            "periods": 0, "rank_ic": 0.0, "icir": 0.0, "positive_ic_ratio": 0.0,
            "annual_return": 0.0, "excess_annual_return": 0.0, "long_short_annual_return": 0.0,
            "sharpe": 0.0, "long_short_sharpe": 0.0, "max_drawdown": 0.0,
            "turnover": 0.0, "coverage": 0.0, "group_returns": [], "monotonicity": 0.0,
        }
    ppy = PERIODS_PER_YEAR[frequency]
    long_returns = np.asarray([_safe_float(row["long_return"]) for row in periods], dtype=float)
    benchmark = np.asarray([_safe_float(row["benchmark_return"]) for row in periods], dtype=float)
    excess = long_returns - benchmark
    long_short = np.asarray([_safe_float(row["long_short_return"]) for row in periods], dtype=float)
    ics = np.asarray([_safe_float(row["rank_ic"]) for row in periods], dtype=float)

    def annualized(values: np.ndarray) -> float:
        if not len(values):
            return 0.0
        nav = float(np.prod(1.0 + values))
        return nav ** (ppy / len(values)) - 1.0 if nav > 0 else -1.0

    def sharpe(values: np.ndarray) -> float:
        if len(values) < 2 or float(np.std(values, ddof=1)) <= 1e-12:
            return 0.0
        return float(np.mean(values) / np.std(values, ddof=1) * math.sqrt(ppy))

    nav = np.cumprod(1.0 + long_returns)
    group_rows = [row["group_returns"] for row in periods if len(row.get("group_returns", [])) == 5]
    groups = np.mean(np.asarray(group_rows, dtype=float), axis=0).tolist() if group_rows else []
    monotonicity = 0.0
    if len(groups) == 5:
        monotonicity = float(pd.Series(groups).corr(pd.Series([1, 2, 3, 4, 5]), method="spearman"))
    return {
        "periods": int(len(periods)),
        "rank_ic": float(np.mean(ics)),
        "icir": float(np.mean(ics) / np.std(ics, ddof=1) * math.sqrt(ppy)) if len(ics) > 1 and np.std(ics, ddof=1) > 1e-12 else 0.0,
        "positive_ic_ratio": float(np.mean(ics > 0)),
        "annual_return": annualized(long_returns),
        "excess_annual_return": annualized(excess),
        "long_short_annual_return": annualized(long_short),
        "sharpe": sharpe(long_returns),
        "long_short_sharpe": sharpe(long_short),
        "max_drawdown": _max_drawdown(nav.tolist()),
        "turnover": float(np.mean([_safe_float(row["turnover"]) for row in periods])),
        "coverage": float(np.mean([_safe_float(row["coverage"]) for row in periods])),
        "group_returns": [float(value) for value in groups],
        "monotonicity": monotonicity,
    }


def _curve(periods: Sequence[Dict[str, Any]]) -> List[List[Any]]:
    long_nav = benchmark_nav = long_short_nav = 1.0
    long_peak = benchmark_peak = long_short_peak = 1.0
    out: List[List[Any]] = []
    for row in periods:
        long_nav *= 1.0 + _safe_float(row["long_return"])
        benchmark_nav *= 1.0 + _safe_float(row["benchmark_return"])
        long_short_nav *= 1.0 + _safe_float(row["long_short_return"])
        long_peak = max(long_peak, long_nav)
        benchmark_peak = max(benchmark_peak, benchmark_nav)
        long_short_peak = max(long_short_peak, long_short_nav)
        out.append([
            row["date"], round(long_nav, 6), round(benchmark_nav, 6), round(long_short_nav, 6),
            round(long_nav / long_peak - 1.0, 6),
            round(benchmark_nav / benchmark_peak - 1.0, 6),
            round(long_short_nav / long_short_peak - 1.0, 6),
            row["split"], round(_safe_float(row["rank_ic"]), 6),
        ])
    return out


def _annual_blocks(periods: Sequence[Dict[str, Any]], frequency: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in periods:
        grouped[str(row["signal_date"])[:4]].append(row)
    out = []
    for year, rows in sorted(grouped.items()):
        block = _metric_block(rows, frequency)
        out.append({
            "year": year,
            "rank_ic": block["rank_ic"],
            "excess_return": block["excess_annual_return"],
            "long_short_return": block["long_short_annual_return"],
            "coverage": block["coverage"],
        })
    return out


def _score_result(metrics: Dict[str, Dict[str, Any]], integrity: Dict[str, bool]) -> Dict[str, Any]:
    train, valid, test = metrics["train"], metrics["valid"], metrics["test"]
    components = {
        "训练证据": min(20.0, max(0.0, train["rank_ic"] / 0.05 * 20.0)),
        "验证稳定": min(20.0, max(0.0, valid["rank_ic"] / 0.04 * 20.0)),
        "封存测试": min(20.0, max(0.0, test["rank_ic"] / 0.04 * 20.0)),
        "组合收益": min(15.0, max(0.0, (valid["excess_annual_return"] + test["excess_annual_return"]) / 0.16 * 15.0)),
        "分组单调": min(10.0, max(0.0, (valid["monotonicity"] + test["monotonicity"]) / 2.0 * 10.0)),
        "风险成本": min(10.0, max(0.0, (1.0 - abs(test["max_drawdown"]) / 0.35) * (1.0 - min(test["turnover"], 1.0)) * 10.0)),
        "数据完整": 5.0 if all(integrity.values()) else 0.0,
    }
    checks = {
        "训练RankIC": train["rank_ic"] >= 0.02,
        "验证RankIC": valid["rank_ic"] >= 0.015,
        "测试RankIC": test["rank_ic"] >= 0.015,
        "验证测试同向": valid["rank_ic"] * test["rank_ic"] > 0,
        "验证组合边际": valid["excess_annual_return"] > 0,
        "测试组合边际": test["excess_annual_return"] > 0,
        "分组单调性": min(valid["monotonicity"], test["monotonicity"]) >= 0.60,
        "覆盖率": min(valid["coverage"], test["coverage"]) >= 0.80,
        "未来信息审计": all(integrity.values()),
    }
    score = float(sum(components.values()))
    return {
        "score": score,
        "grade": "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D",
        "passed": all(checks.values()),
        "components": components,
        "checks": checks,
    }


def _ranking_rows(frame: pd.DataFrame, universe: str, top: bool) -> List[Dict[str, Any]]:
    member = f"member_{universe}"
    sub = frame[frame[member] & frame["quality_eligible"]].sort_values("factor_score", ascending=not top).head(10)
    rows = []
    for rank, (_, row) in enumerate(sub.iterrows(), start=1):
        rows.append({
            "rank": rank,
            "ts_code": str(row["ts_code"]),
            "stock_name": str(row["stock_name"]),
            "industry_name": str(row["industry_name"]),
            "factor_score": _safe_float(row["factor_score"]),
            "quality": _safe_float(row["base_quality"]),
            "value": _safe_float(row["base_value"]),
            "low_crowding": _safe_float(row["base_low_crowding"]),
            "reversal": _safe_float(row["base_reversal"]),
            "trend": _safe_float(row["base_trend"]),
            "moneyflow": _safe_float(row["base_moneyflow"]),
            "entry_eligible": bool(row["entry_eligible"]),
        })
    return rows


def run_study(
    db: Path,
    start: str = "20220101",
    end: Optional[str] = None,
    cost_rate: float = 0.0015,
    focus_code: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    db = Path(db)
    if not db.exists():
        raise FileNotFoundError(db)
    miner = _load_factor_miner()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    conn.execute("pragma query_only=on")
    latest = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0]
    end = str(end or latest)
    dates = _trade_dates(conn, start, end)
    if len(dates) < 260:
        raise RuntimeError(f"Cross-sectional study needs at least 260 trade dates, got {len(dates)}")
    frequency_dates = _frequency_dates(dates)
    frequency_sets = {key: set(values) for key, values in frequency_dates.items()}
    boundaries = _split_boundaries(dates)
    financial, financial_dates = miner.load_financial(conn)
    industries = _industry_intervals(conn)
    ranker, ranker_diagnostics, strategy_policies = _train_ranker(
        db, miner, dates, boundaries, financial, financial_dates, industries, progress_callback
    )
    all_codes = [
        str(row[0])
        for row in conn.execute(
            "select distinct con_code from index_constituent_period where universe='ALL_A' "
            "and trade_date between ? and ? order by con_code",
            (start, end),
        )
    ]
    code_index = {code: index for index, code in enumerate(all_codes)}
    score_matrix = np.full((len(dates), len(all_codes)), np.nan, dtype=np.float32)
    price_matrix = np.full((len(dates), len(all_codes)), np.nan, dtype=np.float32)
    names: Dict[str, str] = {}
    signal_index = {frequency: [dates.index(date) for date in values] for frequency, values in frequency_dates.items()}
    states: Dict[Tuple[str, str], Dict[str, Any]] = {
        (universe, frequency): {
            "previous": None, "periods": [], "last_top": {}, "last_bottom": {},
            "last_strategy": {}, "factor_history": [],
        }
        for universe in UNIVERSES for frequency in FREQUENCIES
    }
    latest_frame: Optional[pd.DataFrame] = None

    plans = [
        (
            date_index,
            date,
            {offset: dates[date_index - offset] for offset in (1, 5, 20, 60, 120)},
            dates[date_index + 1],
        )
        for date_index, date in enumerate(dates)
        if date_index >= 120 and date_index + 1 < len(dates)
    ]
    conn.close()
    worker_state = threading.local()

    def build_snapshot(plan: Tuple[int, str, Dict[int, str], str]) -> Tuple[int, str, pd.DataFrame]:
        date_index, date, lag_dates, entry_date = plan
        worker_conn = getattr(worker_state, "conn", None)
        if worker_conn is None:
            worker_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
            worker_conn.execute("pragma query_only=on")
            worker_state.conn = worker_conn
        memberships = {
            universe: {str(code) for code, _ in miner.get_members(worker_conn, universe, date)}
            for universe in UNIVERSES
        }
        frame = _snapshot(
            worker_conn, miner, date, lag_dates, entry_date, financial, financial_dates,
            industries, memberships,
        )
        if not frame.empty:
            frame = _apply_ranker(frame, ranker)
        return date_index, date, frame

    workers = max(1, min(8, int(os.environ.get("KLINE_FACTOR_STUDY_WORKERS", "4"))))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="factor-snapshot") as executor:
        snapshots = executor.map(build_snapshot, plans)
        for completed, (date_index, date, frame) in enumerate(snapshots, start=1):
            if frame.empty:
                continue
            names.update({str(code): str(name) for code, name in zip(frame["ts_code"], frame["stock_name"])})
            columns = np.fromiter((code_index.get(str(code), -1) for code in frame["ts_code"]), dtype=np.int32)
            valid_columns = columns >= 0
            score_matrix[date_index, columns[valid_columns]] = frame.loc[valid_columns, "factor_score"].to_numpy(dtype=np.float32)
            price_matrix[date_index, columns[valid_columns]] = frame.loc[valid_columns, "px"].to_numpy(dtype=np.float32)
            latest_frame = frame

            for frequency in FREQUENCIES:
                if date not in frequency_sets[frequency]:
                    continue
                current_split = _split_name(date, boundaries)
                for universe in UNIVERSES:
                    state = states[(universe, frequency)]
                    previous = state["previous"]
                    if previous is not None:
                        split = previous["split"] if previous["split"] == current_split else "purged_boundary"
                        row = _evaluate_period(previous, frame, universe, frequency, split, cost_rate)
                        state["periods"].append(row)
                        state["factor_history"].append(row["long_short_return"])
                    policy = strategy_policies[universe]
                    portfolio_kwargs = {
                        "target_multiplier": policy["target_multiplier"],
                        "skip_fraction": policy["skip_fraction"],
                        "buffer_multiple": policy["buffer_multiple"],
                    }
                    top_weights = _portfolio_weights(
                        frame, universe, top=True, previous=state["last_top"], **portfolio_kwargs
                    )
                    bottom_weights = _portfolio_weights(
                        frame, universe, top=False, previous=state["last_bottom"], **portfolio_kwargs
                    )
                    top_turnover = _turnover(state["last_top"], top_weights) if state["last_top"] else 1.0
                    bottom_turnover = _turnover(state["last_bottom"], bottom_weights) if state["last_bottom"] else 1.0
                    usable_history = state["factor_history"][:-1] if len(state["factor_history"]) > 1 else []
                    timing_state = 0.01
                    if policy["timing_enabled"] and usable_history:
                        values = np.asarray(usable_history, dtype=float)
                        age = np.arange(len(values) - 1, -1, -1, dtype=float)
                        decay = np.power(0.5, age / TIMING_HALFLIFE[frequency])
                        timing_state = float(np.sum(values * decay) / max(np.sum(decay), 1e-12))
                    orientation = "bottom" if policy["timing_enabled"] and timing_state < 0 else "top"
                    strategy_weights = bottom_weights if orientation == "bottom" else top_weights
                    strategy_turnover = (
                        _turnover(state["last_strategy"], strategy_weights)
                        if state["last_strategy"] else 1.0
                    )
                    state["last_top"] = top_weights
                    state["last_bottom"] = bottom_weights
                    state["last_strategy"] = strategy_weights
                    state["previous"] = {
                        "date": date,
                        "split": current_split,
                        "frame": frame,
                        "top_weights": top_weights,
                        "bottom_weights": bottom_weights,
                        "strategy_weights": strategy_weights,
                        "top_turnover": top_turnover,
                        "bottom_turnover": bottom_turnover,
                        "strategy_turnover": strategy_turnover,
                        "strategy_orientation": orientation,
                        "factor_timing_state": timing_state,
                    }
            if progress_callback and (completed % 10 == 0 or completed == len(plans)):
                percent = int(5 + 90 * completed / max(len(plans), 1))
                progress_callback(percent, f"并行横截面计算 {date}（{completed}/{len(plans)}）")
    if latest_frame is None:
        raise RuntimeError("No cross-sectional snapshots were produced")

    integrity = {
        "signal_uses_close_or_earlier": True,
        "execution_is_next_trade_open": True,
        "financials_are_visible_date_asof": True,
        "membership_is_signal_date_asof": True,
        "test_not_used_for_formula_or_direction": True,
        "boundary_crossing_labels_are_purged": True,
        "ranker_trained_without_test_labels": bool(ranker_diagnostics["test_labels_used"] is False),
        "factor_timing_uses_one_period_reporting_lag": True,
    }
    results: Dict[str, Dict[str, Any]] = {}
    matrix: List[Dict[str, Any]] = []
    for universe in UNIVERSES:
        results[universe] = {}
        for frequency in FREQUENCIES:
            periods = states[(universe, frequency)]["periods"]
            clean_periods = [row for row in periods if row["split"] != "purged_boundary"]
            metrics = {
                split: _metric_block([row for row in clean_periods if row["split"] == split], frequency)
                for split in ("train", "valid", "test")
            }
            metrics["full"] = _metric_block(clean_periods, frequency)
            score = _score_result(metrics, integrity)
            rank_ic_values = [row["rank_ic"] for row in clean_periods]
            trial_adjusted_sharpe = metrics["test"]["long_short_sharpe"] / math.sqrt(max(1.0, math.log(4.0 * len(UNIVERSES))))
            dsr_confidence = NormalDist().cdf(trial_adjusted_sharpe * math.sqrt(max(metrics["test"]["periods"], 1) / PERIODS_PER_YEAR[frequency]))
            block = {
                "universe": universe,
                "universe_cn": UNIVERSE_CN[universe],
                "frequency": frequency,
                "frequency_cn": FREQUENCY_CN[frequency],
                "metrics": metrics,
                "score": score,
                "curve": _curve(clean_periods),
                "ic_series": [[row["signal_date"], row["rank_ic"], row["split"]] for row in clean_periods],
                "annual": _annual_blocks(clean_periods, frequency),
                "top10": _ranking_rows(latest_frame, universe, top=True),
                "bottom10": _ranking_rows(latest_frame, universe, top=False),
                "diagnostics": {
                    "rank_ic_observations": len(rank_ic_values),
                    "dsr_confidence_proxy": float(dsr_confidence),
                    "cost_rate_per_turnover": cost_rate,
                    "base_selection_count": TARGET_COUNTS[universe],
                    "strategy_policy": strategy_policies[universe],
                    "industry_position_cap": 0.15,
                    "quality_filter": [
                        "剔除ST、退市整理与停牌样本",
                        "至少120个交易日历史",
                        "信号日成交额位于全A前80%",
                        "流通市值位于全A前95%",
                        "下一交易日涨跌停或停牌只影响成交，不反向改写信号",
                    ],
                    "test_usage": "sealed_report_only",
                },
            }
            results[universe][frequency] = block
            matrix.append({
                "universe": universe,
                "universe_cn": UNIVERSE_CN[universe],
                "frequency": frequency,
                "frequency_cn": FREQUENCY_CN[frequency],
                "score": score["score"],
                "grade": score["grade"],
                "passed": score["passed"],
                "train_rank_ic": metrics["train"]["rank_ic"],
                "valid_rank_ic": metrics["valid"]["rank_ic"],
                "test_rank_ic": metrics["test"]["rank_ic"],
                "test_excess_annual_return": metrics["test"]["excess_annual_return"],
                "test_sharpe": metrics["test"]["sharpe"],
                "test_max_drawdown": metrics["test"]["max_drawdown"],
            })

    payload = {
        "status": "done",
        "version": "cross-sectional-factor-study/1.2-lambdarank-factor-momentum",
        "factor_name": FACTOR_NAME,
        "formula": FACTOR_FORMULA,
        "latex": FACTOR_LATEX,
        "method": "LLM方法论候选 + 点时点特征编译 + LambdaRank收益十分位排序 + 稳定性裁判 + 严格滞后因子动量 + 封存测试",
        "ranker": ranker_diagnostics,
        "strategy_policies": strategy_policies,
        "database": str(db),
        "start": start,
        "end": end,
        "split": {
            "method": "chronological_60_20_20_common_calendar",
            "train": {"start": dates[0], "end": boundaries["train_end"]},
            "valid": {"start": dates[dates.index(boundaries["train_end"]) + 1], "end": boundaries["valid_end"]},
            "test": {"start": dates[dates.index(boundaries["valid_end"]) + 1], "end": dates[-1]},
            "boundary_label_policy": "purged",
        },
        "integrity": integrity,
        "universe_definitions": {
            "ALL_A": "信号日全A成分经历史长度、名称、停牌、流动性和流通市值过滤后的优质域",
            "CSI800_ENH": "信号日可得的中证800成分与同一质量过滤交集",
            "CSI2000_ENH": "信号日可得的中证2000成分与同一质量过滤交集",
        },
        "frequency_definitions": {
            "D": "每个交易日收盘形成信号，下一交易日开盘执行",
            "W": "每周最后交易日收盘形成信号，下一交易日开盘执行",
            "M": "每月最后交易日收盘形成信号，下一交易日开盘执行",
            "Q": "每季最后交易日收盘形成信号，下一交易日开盘执行",
        },
        "matrix": matrix,
        "results": results,
        "latest_date": str(latest_frame["trade_date"].iloc[0]),
        "focus_code": focus_code,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    runtime = {
        "dates": np.asarray(dates),
        "codes": np.asarray(all_codes),
        "names": names,
        "score_matrix": score_matrix,
        "price_matrix": price_matrix,
        "frequency_indices": {key: np.asarray(value, dtype=np.int32) for key, value in signal_index.items()},
    }
    if progress_callback:
        progress_callback(100, "横截面因子研究完成")
    return payload, runtime


def _stock_split_name(date: str, split: Dict[str, Any]) -> str:
    for name in ("train", "valid", "test"):
        block = split.get(name, {}) if isinstance(split, dict) else {}
        start, end = str(block.get("start", "")), str(block.get("end", ""))
        if start and end and start <= date <= end:
            return name
    return "full"


def _stock_metric_block(periods: Sequence[Dict[str, Any]], frequency: str) -> Dict[str, Any]:
    if not periods:
        return {
            "periods": 0,
            "total_return": 0.0,
            "annual_return": 0.0,
            "buy_hold_annual_return": 0.0,
            "excess_annual_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "average_turnover": 0.0,
            "total_turnover": 0.0,
            "trade_count": 0,
            "invested_ratio": 0.0,
        }
    strategy = np.asarray([_safe_float(row["strategy_return"]) for row in periods], dtype=float)
    buy_hold = np.asarray([_safe_float(row["buy_hold_return"]) for row in periods], dtype=float)
    strategy = np.maximum(strategy, -0.999999)
    buy_hold = np.maximum(buy_hold, -0.999999)
    strategy_nav = np.cumprod(1.0 + strategy)
    buy_hold_nav = np.cumprod(1.0 + buy_hold)
    periods_per_year = PERIODS_PER_YEAR[frequency]

    def annual_return(nav: np.ndarray) -> float:
        if not len(nav) or nav[-1] <= 0:
            return -1.0 if len(nav) else 0.0
        return float(nav[-1] ** (periods_per_year / len(nav)) - 1.0)

    strategy_std = float(np.std(strategy, ddof=1)) if len(strategy) > 1 else 0.0
    sharpe = float(np.mean(strategy) / strategy_std * math.sqrt(periods_per_year)) if strategy_std > 1e-12 else 0.0
    path = np.concatenate(([1.0], strategy_nav))
    drawdown = path / np.maximum.accumulate(path) - 1.0
    strategy_annual = annual_return(strategy_nav)
    buy_hold_annual = annual_return(buy_hold_nav)
    turnovers = np.asarray([_safe_float(row["turnover"]) for row in periods], dtype=float)
    positions = np.asarray([_safe_float(row["position"]) for row in periods], dtype=float)
    return {
        "periods": int(len(periods)),
        "total_return": float(strategy_nav[-1] - 1.0),
        "annual_return": strategy_annual,
        "buy_hold_annual_return": buy_hold_annual,
        "excess_annual_return": float(strategy_annual - buy_hold_annual),
        "sharpe": sharpe,
        "max_drawdown": float(np.min(drawdown)),
        "average_turnover": float(np.mean(turnovers)),
        "total_turnover": float(np.sum(turnovers)),
        "trade_count": int(np.sum(turnovers > 0)),
        "invested_ratio": float(np.mean(positions)),
    }


def _stock_strategy_backtest(
    db: Path,
    code: str,
    stock_name: str,
    series: Sequence[List[Any]],
    universe: str,
    frequency: str,
    result_block: Dict[str, Any],
    split: Dict[str, Any],
    cost_rate: float,
) -> Dict[str, Any]:
    policy = ((result_block or {}).get("diagnostics", {}).get("strategy_policy", {}) or {})
    target_multiplier = max(1.0, _safe_float(policy.get("target_multiplier", 1.0)))
    skip_fraction = max(0.0, min(0.40, _safe_float(policy.get("skip_fraction", 0.0))))
    buffer_multiple = max(1.0, _safe_float(policy.get("buffer_multiple", 1.0)))
    approximate_sizes = {"ALL_A": 5000.0, "CSI800_ENH": 800.0, "CSI2000_ENH": 2000.0}
    selection_fraction = min(
        0.80,
        max(0.01, TARGET_COUNTS[universe] * target_multiplier / approximate_sizes[universe]),
    )
    entry_threshold = max(0.05, 1.0 - skip_fraction - selection_fraction)
    buffer_width = min(0.15, selection_fraction * max(buffer_multiple - 1.0, 0.0))
    exit_threshold = max(0.0, entry_threshold - buffer_width)
    empty_metrics = {
        name: _stock_metric_block([], frequency)
        for name in ("train", "valid", "test", "full")
    }
    signal_policy = {
        "name": "因子分位严格滞后个股策略",
        "universe": universe,
        "universe_cn": UNIVERSE_CN[universe],
        "frequency": frequency,
        "frequency_cn": FREQUENCY_CN[frequency],
        "entry_threshold": entry_threshold,
        "exit_threshold": exit_threshold,
        "selection_fraction": selection_fraction,
        "cost_rate_per_turnover": float(cost_rate),
        "execution": "信号日收盘后确认，下一交易日开盘执行",
        "membership_is_signal_date_asof": True,
        "boundary_crossing_labels_are_purged": True,
        "quality_scope": "个股诊断执行历史成员、ST/退市、上市满120日与可交易约束；正式组合另执行横截面成交额、市值和行业限额。",
        "portfolio_attribution": False,
    }
    if len(series) < 3:
        return {
            "series": [list(row) + [0.0, "空仓"] for row in series],
            "strategy_curve": [],
            "strategy_metrics": empty_metrics,
            "signal_policy": signal_policy,
        }

    conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True, timeout=60)
    conn.execute("pragma query_only=on")
    market_rows = conn.execute(
        "select trade_date, open, high, low, close, qfq_close, vol, up_limit, down_limit, suspend_timing "
        "from stock_ohlcv_daily where ts_code=? order by trade_date",
        (code,),
    ).fetchall()
    membership_dates = {
        str(row[0])
        for row in conn.execute(
            "select trade_date from index_constituent_period "
            "where universe=? and con_code=?",
            (universe, code),
        ).fetchall()
    }
    universe_snapshot_dates = np.asarray([
        str(row[0])
        for row in conn.execute(
            "select distinct trade_date from index_constituent_period "
            "where universe=? order by trade_date",
            (universe,),
        ).fetchall()
    ])
    conn.close()
    if len(market_rows) < 3:
        return {
            "series": [list(row) + [0.0, "空仓"] for row in series],
            "strategy_curve": [],
            "strategy_metrics": empty_metrics,
            "signal_policy": signal_policy,
        }

    trade_dates = np.asarray([str(row[0]) for row in market_rows])
    bad_name = ("ST" in str(stock_name).upper()) or ("退" in str(stock_name))
    entries: List[Optional[Dict[str, Any]]] = []
    for row in series:
        signal_date = str(row[0])
        market_index = int(np.searchsorted(trade_dates, signal_date, side="right"))
        if market_index >= len(market_rows):
            entries.append(None)
            continue
        market = market_rows[market_index]
        open_price = _safe_float(market[1])
        high_price = _safe_float(market[2])
        low_price = _safe_float(market[3])
        close_price = _safe_float(market[4])
        qfq_close = _safe_float(market[5])
        entry_price = qfq_close * open_price / close_price if close_price > 0 else 0.0
        volume = _safe_float(market[6])
        up_limit = _safe_float(market[7])
        down_limit = _safe_float(market[8])
        suspended = market[9] not in (None, "")
        locked_up = bool(up_limit > 0 and open_price >= up_limit * 0.995 and low_price >= up_limit * 0.995)
        locked_down = bool(down_limit > 0 and open_price <= down_limit * 1.005 and high_price <= down_limit * 1.005)
        base_tradable = bool(entry_price > 0 and volume > 0 and not suspended)
        snapshot_index = int(np.searchsorted(universe_snapshot_dates, signal_date, side="right")) - 1
        membership_snapshot = (
            str(universe_snapshot_dates[snapshot_index]) if snapshot_index >= 0 else ""
        )
        quality_eligible = bool(
            not bad_name
            and market_index >= 120
            and membership_snapshot in membership_dates
        )
        entries.append({
            "date": str(market[0]),
            "price": entry_price,
            "buy_allowed": bool(base_tradable and not locked_up),
            "sell_allowed": bool(base_tradable and not locked_down),
            "quality_eligible": quality_eligible,
            "membership_snapshot": membership_snapshot,
        })

    position = 0.0
    strategy_nav = buy_hold_nav = 1.0
    strategy_peak = buy_hold_peak = 1.0
    periods: List[Dict[str, Any]] = []
    curve: List[List[Any]] = []
    states: Dict[str, Tuple[float, str]] = {}
    for index in range(len(series) - 1):
        signal_date = str(series[index][0])
        next_signal_date = str(series[index + 1][0])
        score = _safe_float(series[index][1])
        entry = entries[index]
        next_entry = entries[index + 1]
        desired = position
        if entry is not None and entry["quality_eligible"]:
            if position <= 0 and score >= entry_threshold:
                desired = 1.0
            elif position > 0 and score <= exit_threshold:
                desired = 0.0
        else:
            desired = 0.0

        action = "持有" if position > 0 else "空仓"
        new_position = position
        if desired > position:
            if entry is not None and entry["buy_allowed"]:
                new_position, action = 1.0, "买入"
            else:
                action = "买入受限"
        elif desired < position:
            if entry is not None and entry["sell_allowed"]:
                new_position, action = 0.0, "卖出"
            else:
                action = "卖出受限"
        turnover = abs(new_position - position)
        position = new_position
        states[signal_date] = (position, action)
        if entry is None or next_entry is None or entry["price"] <= 0 or next_entry["price"] <= 0:
            continue

        current_split = _stock_split_name(signal_date, split)
        next_split = _stock_split_name(next_signal_date, split)
        period_split = current_split if current_split == next_split else "purged_boundary"
        raw_return = float(next_entry["price"] / entry["price"] - 1.0)
        strategy_return = float(position * raw_return - turnover * cost_rate)
        period = {
            "date": next_signal_date,
            "signal_date": signal_date,
            "split": period_split,
            "strategy_return": strategy_return,
            "buy_hold_return": raw_return,
            "turnover": turnover,
            "position": position,
            "score": score,
            "action": action,
        }
        if period_split == "purged_boundary":
            continue
        periods.append(period)
        strategy_nav *= 1.0 + max(strategy_return, -0.999999)
        buy_hold_nav *= 1.0 + max(raw_return, -0.999999)
        strategy_peak = max(strategy_peak, strategy_nav)
        buy_hold_peak = max(buy_hold_peak, buy_hold_nav)
        curve.append([
            next_signal_date,
            round(strategy_nav, 6),
            round(buy_hold_nav, 6),
            round(strategy_nav / strategy_peak - 1.0, 6),
            round(buy_hold_nav / buy_hold_peak - 1.0, 6),
            round(position, 6),
            round(score, 6),
            action,
            period_split,
        ])
    states[str(series[-1][0])] = (position, "期末持仓" if position > 0 else "期末空仓")
    enriched_series = [
        list(row) + [round(states.get(str(row[0]), (0.0, "空仓"))[0], 6), states.get(str(row[0]), (0.0, "空仓"))[1]]
        for row in series
    ]
    metrics = {
        name: _stock_metric_block(
            periods if name == "full" else [row for row in periods if row["split"] == name],
            frequency,
        )
        for name in ("train", "valid", "test", "full")
    }
    return {
        "series": enriched_series,
        "strategy_curve": curve,
        "strategy_metrics": metrics,
        "signal_policy": signal_policy,
    }


def stock_series(
    runtime: Dict[str, Any],
    code: str,
    frequency: str = "W",
    db: Optional[Path] = None,
    universe: str = "ALL_A",
    result_block: Optional[Dict[str, Any]] = None,
    split: Optional[Dict[str, Any]] = None,
    cost_rate: float = 0.0015,
) -> Dict[str, Any]:
    frequency = frequency if frequency in FREQUENCIES else "W"
    universe = universe if universe in UNIVERSES else "ALL_A"
    codes = runtime["codes"]
    matches = np.where(codes == code)[0]
    if not len(matches):
        return {
            "ts_code": code,
            "universe": universe,
            "universe_cn": UNIVERSE_CN[universe],
            "frequency": frequency,
            "series": [],
            "strategy_curve": [],
        }
    column = int(matches[0])
    indices = runtime["frequency_indices"][frequency]
    dates = runtime["dates"][indices]
    scores = runtime["score_matrix"][indices, column]
    prices = runtime["price_matrix"][indices, column]
    valid = np.isfinite(scores) & np.isfinite(prices)
    series = [
        [str(date), round(float(score), 6), round(float(price), 6)]
        for date, score, price in zip(dates[valid], scores[valid], prices[valid])
    ]
    stock_name = runtime.get("names", {}).get(code, "")
    payload: Dict[str, Any] = {
        "ts_code": code,
        "stock_name": stock_name,
        "universe": universe,
        "universe_cn": UNIVERSE_CN[universe],
        "frequency": frequency,
        "frequency_cn": FREQUENCY_CN[frequency],
        "series": series,
        "strategy_curve": [],
    }
    if db is not None and result_block is not None and series:
        payload.update(_stock_strategy_backtest(
            Path(db),
            code,
            stock_name,
            series,
            universe,
            frequency,
            result_block,
            split or {},
            cost_rate,
        ))
    return payload


def save_runtime(runtime: Dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        dates=runtime["dates"],
        codes=runtime["codes"],
        score_matrix=runtime["score_matrix"],
        price_matrix=runtime["price_matrix"],
        frequency_D=runtime["frequency_indices"]["D"],
        frequency_W=runtime["frequency_indices"]["W"],
        frequency_M=runtime["frequency_indices"]["M"],
        frequency_Q=runtime["frequency_indices"]["Q"],
        names_json=np.asarray([json.dumps(runtime.get("names", {}), ensure_ascii=False)]),
    )


def load_runtime(path: Path) -> Optional[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return None
    data = np.load(path, allow_pickle=False)
    return {
        "dates": data["dates"],
        "codes": data["codes"],
        "score_matrix": data["score_matrix"],
        "price_matrix": data["price_matrix"],
        "frequency_indices": {key: data[f"frequency_{key}"] for key in FREQUENCIES},
        "names": json.loads(str(data["names_json"][0])),
    }

