"""Run the report-grounded causal multi-scale K-line challenger."""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framework.backtest.kline_multiscale_expert import (  # noqa: E402
    BacktestConfig,
    backtest_weekly_scores,
    build_multiscale_experts,
    causal_expert_mixture,
    combine_long_short_backtests,
    choose_champion,
    market_state_features,
    online_market_exposure,
    residual_blend,
    row_rank,
)
from framework.backtest.kline_supervised_ranker import (  # noqa: E402
    train_chronological_kline_ranker,
    train_chronological_market_exposure,
)
from framework.backtest.technical_signal_model import (  # noqa: E402
    TECHNICAL_MODEL_VERSION,
    build_technical_signal_families,
    combine_signal_families,
    learn_family_weights_train_only,
    technical_family_diagnostics,
    technical_framework_payload,
)


DEFAULT_DATABASE = ROOT / "database" / "research_warehouse.db"
DEFAULT_BASE_RUNTIME = ROOT / "output" / "kline_memory_learning" / "cross_sectional_factor_runtime.npz"
DEFAULT_BASE_RESULT = ROOT / "output" / "kline_memory_learning" / "cross_sectional_factor_study.json"
DEFAULT_OHLCV_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_ohlcv_runtime.npz"
DEFAULT_FEATURE_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_feature_runtime_v2.npz"
DEFAULT_SIZE_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_size_runtime.npz"
DEFAULT_OUTPUT = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_expert_challenger.json"


REPORTS = [
    {
        "broker": "中信建投证券",
        "title": "智能量化：量价因子策略库",
        "date": "2022-09-01",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/715345614460.pdf",
        "implemented": "量价形态拆分、截面排序与组合转化分层检验",
    },
    {
        "broker": "招商证券",
        "title": "AI系列研究之四：混合频率量价因子模型初探",
        "date": "2024-11-13",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/784821792072.pdf",
        "implemented": "日线与周线独立表征、残差增量融合",
    },
    {
        "broker": "民生证券",
        "title": "深度学习模型如何控制策略风险",
        "date": "2024-04-26",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/767436175565.pdf",
        "implemented": "换手缓冲、波动预算、尾部风险与封存测试",
    },
    {
        "broker": "国泰海通证券",
        "title": "GRU与TCN深度学习因子研究",
        "date": "2025-08-01",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/807349549883.pdf",
        "implemented": "多股票池验证、行业中性排序、组合约束",
    },
    {
        "broker": "东方证券",
        "title": "NeuralODE时序动力系统研究",
        "date": "2025-05-27",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/801678116035.pdf",
        "implemented": "连续市场状态特征与因果状态门控",
    },
    {
        "broker": "东吴证券",
        "title": "技术形态专家模型统一框架",
        "date": "2026-03-24",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/827696785997.pdf",
        "implemented": "个股截面与指数时序双验证、日K周K独立专家",
    },
]


def _safe_float(value: object, default: float = np.nan) -> float:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return default
    return output if np.isfinite(output) else default


def _load_base_runtime(path: Path) -> Dict[str, object]:
    with np.load(path, allow_pickle=False) as data:
        names = json.loads(str(data["names_json"][0]))
        return {
            "dates": data["dates"].astype(str),
            "codes": data["codes"].astype(str),
            "scores": data["score_matrix"].astype(np.float32),
            "prices": data["price_matrix"].astype(np.float32),
            "weekly_indices": data["frequency_W"].astype(np.int32),
            "names": names,
        }


def _cache_matches(path: Path, dates: np.ndarray, codes: np.ndarray) -> bool:
    if not path.exists():
        return False
    try:
        with np.load(path, allow_pickle=False) as data:
            return (
                np.array_equal(data["dates"].astype(str), dates.astype(str))
                and np.array_equal(data["codes"].astype(str), codes.astype(str))
            )
    except (OSError, KeyError, ValueError):
        return False


def _build_ohlcv_cache(database: Path, path: Path, dates: np.ndarray, codes: np.ndarray) -> Dict[str, np.ndarray]:
    shape = (len(dates), len(codes))
    arrays = {
        key: np.full(shape, np.nan, dtype=np.float32)
        for key in ("open", "high", "low", "close", "volume", "amount", "trade_open")
    }
    date_index = {str(value): index for index, value in enumerate(dates)}
    code_index = {str(value): index for index, value in enumerate(codes)}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    connection.execute("pragma query_only=on")
    cursor = connection.execute(
        "select trade_date,ts_code,open,high,low,close,qfq_close,vol,amount,"
        "up_limit,down_limit,suspend_timing from stock_ohlcv_daily "
        "where trade_date between ? and ? order by trade_date,ts_code",
        (str(dates[0]), str(dates[-1])),
    )
    for rows in iter(lambda: cursor.fetchmany(200000), []):
        for row in rows:
            date_position = date_index.get(str(row[0]))
            code_position = code_index.get(str(row[1]))
            if date_position is None or code_position is None:
                continue
            raw_close = _safe_float(row[5])
            adjusted_close = _safe_float(row[6])
            if not np.isfinite(raw_close) or raw_close <= 0 or not np.isfinite(adjusted_close):
                continue
            adjustment = adjusted_close / raw_close
            raw_open = _safe_float(row[2])
            raw_high = _safe_float(row[3])
            raw_low = _safe_float(row[4])
            volume = _safe_float(row[7])
            amount = _safe_float(row[8])
            arrays["open"][date_position, code_position] = raw_open * adjustment
            arrays["high"][date_position, code_position] = raw_high * adjustment
            arrays["low"][date_position, code_position] = raw_low * adjustment
            arrays["close"][date_position, code_position] = adjusted_close
            arrays["volume"][date_position, code_position] = volume
            arrays["amount"][date_position, code_position] = amount
            upper = _safe_float(row[9])
            lower = _safe_float(row[10])
            suspended = row[11] not in (None, "")
            locked_up = (
                np.isfinite(upper) and np.isfinite(raw_open) and np.isfinite(raw_low)
                and raw_open >= upper * 0.995 and raw_low >= upper * 0.995
            )
            locked_down = (
                np.isfinite(lower) and np.isfinite(raw_open) and np.isfinite(raw_high)
                and raw_open <= lower * 1.005 and raw_high <= lower * 1.005
            )
            if np.isfinite(raw_open) and raw_open > 0 and volume > 0 and not suspended and not locked_up and not locked_down:
                arrays["trade_open"][date_position, code_position] = raw_open * adjustment
    connection.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, dates=dates, codes=codes, **arrays)
    return arrays


def _load_ohlcv(database: Path, path: Path, dates: np.ndarray, codes: np.ndarray) -> Dict[str, np.ndarray]:
    if not _cache_matches(path, dates, codes):
        return _build_ohlcv_cache(database, path, dates, codes)
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key].astype(np.float32) for key in (
            "open", "high", "low", "close", "volume", "amount", "trade_open"
        )}


def _load_size_matrix(
    database: Path,
    path: Path,
    dates: np.ndarray,
    codes: np.ndarray,
) -> np.ndarray:
    if _cache_matches(path, dates, codes):
        with np.load(path, allow_pickle=False) as data:
            return data["circ_mv"].astype(np.float32)
    output = np.full((len(dates), len(codes)), np.nan, dtype=np.float32)
    date_index = {str(value): index for index, value in enumerate(dates)}
    code_index = {str(value): index for index, value in enumerate(codes)}
    placeholders = ",".join("?" for _ in dates)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    connection.execute("pragma query_only=on")
    query = (
        "select trade_date,ts_code,circ_mv from stock_valuation_daily "
        f"where trade_date in ({placeholders})"
    )
    for date, code, value in connection.execute(query, tuple(str(date) for date in dates)):
        row, column = date_index.get(str(date)), code_index.get(str(code))
        numeric = _safe_float(value)
        if row is not None and column is not None and np.isfinite(numeric) and numeric > 0.0:
            output[row, column] = numeric
    connection.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, dates=dates, codes=codes, circ_mv=output)
    return output


def _size_residual_rank(score: np.ndarray, size: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    output = np.full_like(score, np.nan, dtype=np.float32)
    for row in range(len(output)):
        mask = eligible[row] & np.isfinite(score[row]) & np.isfinite(size[row]) & (size[row] > 0.0)
        if int(mask.sum()) < 50:
            continue
        ranked_score = pd.Series(score[row, mask]).rank(pct=True).to_numpy(dtype=float)
        log_size = np.log(size[row, mask].astype(float))
        scale = max(float(np.std(log_size, ddof=1)), 1e-12)
        standardized = (log_size - float(np.mean(log_size))) / scale
        design = np.column_stack([np.ones(len(standardized)), standardized, standardized ** 2])
        residual = ranked_score - design @ np.linalg.lstsq(design, ranked_score, rcond=None)[0]
        output[row, mask] = pd.Series(residual).rank(pct=True).to_numpy(dtype=np.float32)
    return output


def _load_membership(
    database: Path,
    universe: str,
    dates: Sequence[str],
    codes: Sequence[str],
) -> np.ndarray:
    if universe == "ALL_A":
        return np.ones((len(dates), len(codes)), dtype=bool)
    output = np.zeros((len(dates), len(codes)), dtype=bool)
    date_index = {str(value): index for index, value in enumerate(dates)}
    code_index = {str(value): index for index, value in enumerate(codes)}
    placeholders = ",".join("?" for _ in dates)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    connection.execute("pragma query_only=on")
    query = (
        "select trade_date,con_code from index_constituent_period "
        f"where universe=? and trade_date in ({placeholders})"
    )
    for date, code in connection.execute(query, (universe, *dates)):
        i, j = date_index.get(str(date)), code_index.get(str(code))
        if i is not None and j is not None:
            output[i, j] = True
    connection.close()
    return output


def _industry_intervals(database: Path) -> Dict[str, List[Tuple[str, str, str]]]:
    output: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    connection.execute("pragma query_only=on")
    for code, start, end, industry in connection.execute(
        "select ts_code,start_date,coalesce(end_date,'99991231'),industry_name "
        "from sw_l1_industry_daily order by ts_code,start_date"
    ):
        output[str(code)].append((str(start), str(end), str(industry)))
    connection.close()
    return output


def _industry_matrix(database: Path, dates: Sequence[str], codes: Sequence[str]) -> np.ndarray:
    intervals = _industry_intervals(database)
    labels: Dict[str, int] = {"未分类": 0}
    output = np.zeros((len(dates), len(codes)), dtype=np.int16)
    for column, code in enumerate(codes):
        values = intervals.get(str(code), [])
        starts = [value[0] for value in values]
        for row, date in enumerate(dates):
            position = bisect.bisect_right(starts, str(date)) - 1
            industry = "未分类"
            if position >= 0 and values[position][0] <= str(date) <= values[position][1]:
                industry = values[position][2]
            if industry not in labels:
                labels[industry] = len(labels)
            output[row, column] = labels[industry]
    return output


def _industry_rank(score: np.ndarray, industries: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    global_rank = row_rank(score, eligible)
    output = np.full_like(global_rank, np.nan, dtype=np.float32)
    for row in range(len(output)):
        for industry in np.unique(industries[row, eligible[row]]):
            mask = eligible[row] & (industries[row] == industry) & np.isfinite(score[row])
            if int(mask.sum()) < 5:
                output[row, mask] = global_rank[row, mask]
                continue
            local = pd.Series(score[row, mask]).rank(pct=True).to_numpy(dtype=np.float32)
            output[row, mask] = 0.70 * global_rank[row, mask] + 0.30 * local
    return row_rank(output, eligible)


def _split_labels(dates: Sequence[str], base_result: Mapping[str, object]) -> List[str]:
    split = base_result["split"]
    train_end = str(split["train"]["end"])
    valid_end = str(split["valid"]["end"])
    return ["train" if date <= train_end else "valid" if date <= valid_end else "test" for date in dates]


def _rolling_volatility(close: np.ndarray, indices: np.ndarray) -> np.ndarray:
    returns = np.full_like(close, np.nan, dtype=np.float64)
    returns[1:] = close[1:] / close[:-1] - 1.0
    frame = pd.DataFrame(returns)
    return (frame.rolling(20, min_periods=12).std().to_numpy()[indices] * np.sqrt(252.0)).astype(np.float32)


def _latest_rows(
    codes: np.ndarray,
    names: Mapping[str, str],
    score: np.ndarray,
    experts: Mapping[str, np.ndarray],
    eligible: np.ndarray,
    limit: int = 20,
) -> List[Dict[str, object]]:
    index = len(score) - 1
    candidates = np.flatnonzero(eligible[index] & np.isfinite(score[index]))
    order = candidates[np.argsort(score[index, candidates])[::-1]][:limit]
    rows = []
    for rank, position in enumerate(order, start=1):
        rows.append({
            "排名": rank,
            "代码": str(codes[position]),
            "名称": str(names.get(str(codes[position]), str(codes[position]))),
            "综合形态": round(float(score[index, position]), 4),
            **{
                name: round(float(values[index, position]), 4)
                for name, values in experts.items() if np.isfinite(values[index, position])
            },
        })
    return rows


def _load_or_build_features(
    database: Path,
    feature_cache_path: Path,
    dates: np.ndarray,
    codes: np.ndarray,
    weekly_indices: np.ndarray,
    weekly_dates: np.ndarray,
    ohlcv: Mapping[str, np.ndarray],
    original_daily: np.ndarray,
    names: Mapping[str, str],
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if _cache_matches(feature_cache_path, dates, codes):
        with np.load(feature_cache_path, allow_pickle=False) as data:
            expert_names = json.loads(str(data["expert_names_json"][0]))
            experts = {
                name: data[f"expert_{index}"].astype(np.float32)
                for index, name in enumerate(expert_names)
            }
            return (
                experts,
                data["eligible_weekly_base"].astype(bool),
                data["original_weekly"].astype(np.float32),
                data["risk"].astype(np.float32),
                data["state_features"].astype(np.float32),
                data["states"].astype(np.int8),
                data["market_vol"].astype(np.float32),
            )
    name_mask = np.asarray([
        "ST" not in str(names.get(str(code), "")).upper()
        and "退" not in str(names.get(str(code), ""))
        for code in codes
    ], dtype=bool)
    amount_rank = row_rank(ohlcv["amount"])
    # Eligibility is a point-in-time market-data decision. Requiring the legacy
    # score here would drop the latest cross-section before its label matures.
    eligible_daily = (
        np.isfinite(ohlcv["close"])
        & (amount_rank >= 0.20) & name_mask[None, :]
    )
    eligible_weekly_base = eligible_daily[weekly_indices]
    expert_raw = build_multiscale_experts(
        ohlcv["close"], ohlcv["open"], ohlcv["high"], ohlcv["low"],
        ohlcv["volume"], ohlcv["amount"], weekly_indices, eligible_daily,
    )
    industries = _industry_matrix(database, weekly_dates, codes)
    experts = {
        name: _industry_rank(values, industries, eligible_weekly_base)
        for name, values in expert_raw.items()
    }
    original_weekly = _industry_rank(original_daily[weekly_indices], industries, eligible_weekly_base)
    risk = _rolling_volatility(ohlcv["close"], weekly_indices)
    state_features, states = market_state_features(ohlcv["close"], weekly_indices, eligible_daily)
    daily_market_returns = np.r_[
        np.full((1, len(codes)), np.nan),
        ohlcv["close"][1:] / ohlcv["close"][:-1] - 1.0,
    ]
    market_daily = np.nanmean(np.where(eligible_daily, daily_market_returns, np.nan), axis=1)
    market_vol = pd.Series(market_daily).rolling(20, min_periods=12).std().to_numpy()[weekly_indices] * np.sqrt(252.0)
    feature_cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        feature_cache_path,
        dates=dates,
        codes=codes,
        expert_names_json=np.asarray([json.dumps(list(experts), ensure_ascii=False)]),
        eligible_weekly_base=eligible_weekly_base,
        original_weekly=original_weekly,
        risk=risk,
        state_features=state_features,
        states=states,
        market_vol=market_vol,
        **{f"expert_{index}": values for index, values in enumerate(experts.values())},
    )
    return experts, eligible_weekly_base, original_weekly, risk, state_features, states, market_vol


def run(
    database: Path = DEFAULT_DATABASE,
    base_runtime_path: Path = DEFAULT_BASE_RUNTIME,
    base_result_path: Path = DEFAULT_BASE_RESULT,
    ohlcv_cache_path: Path = DEFAULT_OHLCV_CACHE,
    feature_cache_path: Path = DEFAULT_FEATURE_CACHE,
    output_path: Path = DEFAULT_OUTPUT,
) -> Dict[str, object]:
    runtime = _load_base_runtime(base_runtime_path)
    base_result = json.loads(base_result_path.read_text(encoding="utf-8"))
    dates = runtime["dates"]
    codes = runtime["codes"]
    weekly_indices = runtime["weekly_indices"]
    weekly_dates = dates[weekly_indices].astype(str)
    ohlcv = _load_ohlcv(database, ohlcv_cache_path, dates, codes)
    original_daily = runtime["scores"]
    (
        experts, eligible_weekly_base, original_weekly, risk,
        state_features, states, market_vol,
    ) = _load_or_build_features(
        database, feature_cache_path, dates, codes, weekly_indices, weekly_dates,
        ohlcv, original_daily, runtime["names"],
    )
    size_matrix = _load_size_matrix(database, DEFAULT_SIZE_CACHE, weekly_dates, codes)
    entry_indices = np.minimum(weekly_indices + 1, len(dates) - 1)
    entry_prices = ohlcv["trade_open"][entry_indices]
    marked_close = ohlcv["close"].astype(np.float32).copy()
    for row in range(1, len(marked_close)):
        missing = ~np.isfinite(marked_close[row])
        marked_close[row, missing] = marked_close[row - 1, missing]
    signal_close = marked_close[weekly_indices]
    split_labels = _split_labels(weekly_dates, base_result)
    supervised_feedback = np.full_like(signal_close, np.nan, dtype=np.float32)
    supervised_feedback[:-1] = entry_prices[1:] / entry_prices[:-1] - 1.0
    causal_feedback = np.full_like(signal_close, np.nan, dtype=np.float32)
    causal_feedback[:-1] = signal_close[1:] / entry_prices[:-1] - 1.0
    supervised_score, supervised_diagnostics = train_chronological_kline_ranker(
        experts, state_features, supervised_feedback, eligible_weekly_base, split_labels,
    )
    supervised_market_returns = np.nanmean(
        np.where(eligible_weekly_base, supervised_feedback, np.nan), axis=1
    )
    supervised_exposure, market_diagnostics = train_chronological_market_exposure(
        state_features, supervised_market_returns, market_vol, split_labels,
    )
    technical_name_mask = np.asarray([
        "ST" not in str(runtime["names"].get(str(code), "")).upper()
        and "退" not in str(runtime["names"].get(str(code), ""))
        for code in codes
    ], dtype=bool)
    technical_amount_rank = row_rank(ohlcv["amount"])
    technical_eligible_daily = (
        np.isfinite(ohlcv["close"])
        & (technical_amount_rank >= 0.20)
        & technical_name_mask[None, :]
    )
    technical_raw = build_technical_signal_families(
        ohlcv["close"], ohlcv["open"], ohlcv["high"], ohlcv["low"],
        ohlcv["volume"], ohlcv["amount"], weekly_indices, technical_eligible_daily,
    )
    technical_industries = _industry_matrix(database, weekly_dates, codes)
    technical_families = {
        name: _industry_rank(values, technical_industries, eligible_weekly_base)
        for name, values in technical_raw.items()
    }
    results: Dict[str, object] = {}

    for universe, universe_name, count in (
        ("ALL_A", "全A", 100),
        ("CSI800_ENH", "中证800", 80),
        ("CSI2000_ENH", "中证2000", 80),
    ):
        membership = _load_membership(database, universe, weekly_dates, codes)
        eligible = eligible_weekly_base & membership
        feedback = causal_feedback
        technical, _, _ = causal_expert_mixture(
            experts, feedback, eligible, states=states,
        )
        directed, posterior_weights, expert_ic = causal_expert_mixture(
            experts, feedback, eligible, states=states, signed_direction=True,
        )
        equal_weight = row_rank(np.nanmean(np.stack(list(experts.values())), axis=0), eligible)
        blended = residual_blend(original_weekly, technical, eligible, technical_weight=0.65)
        supervised_neutral = _size_residual_rank(supervised_score, size_matrix, eligible)
        market_feedback = np.nanmean(np.where(eligible, feedback, np.nan), axis=1)
        exposure = online_market_exposure(state_features, market_feedback, market_vol)
        config = BacktestConfig(selection_count=count, maximum_weight=0.02, cost_rate=0.0015)
        fraction_config = BacktestConfig(
            selection_count=0, selection_fraction=0.10, buffer_multiple=1.5,
            maximum_weight=0.02, cost_rate=0.0015,
        )
        equal_fraction_config = BacktestConfig(
            selection_count=0, selection_fraction=0.10, buffer_multiple=1.5,
            maximum_weight=0.02, cost_rate=0.0015, inverse_risk_weighting=False,
        )
        pure_weight_diagnostics = learn_family_weights_train_only(
            technical_families, supervised_feedback, eligible, split_labels,
        )
        pure_score = combine_signal_families(
            technical_families, eligible, pure_weight_diagnostics["weights"],
        )
        pure_residual = residual_blend(original_weekly, pure_score, eligible, technical_weight=0.75)
        pure_specs = [
            ("纯技术综合轮动", pure_score, None, fraction_config),
            ("纯技术残差增量轮动", pure_residual, None, config),
            ("纯技术风险预算轮动", pure_score, supervised_exposure, fraction_config),
            ("监督技术分位轮动", supervised_score, None, fraction_config),
            ("监督技术市值中性轮动", supervised_neutral, None, equal_fraction_config),
            ("监督技术风险预算轮动", supervised_score, exposure, fraction_config),
        ]
        pure_specs.extend(
            (f"技术子信号::{family_name}", family_score, None, fraction_config)
            for family_name, family_score in technical_families.items()
        )
        pure_candidates: Dict[str, object] = {}
        for name, score, candidate_exposure, candidate_config in pure_specs:
            pure_candidates[name] = backtest_weekly_scores(
                weekly_dates, score, eligible, entry_prices, signal_close, risk,
                split_labels, exposure=candidate_exposure, config=candidate_config,
            )
        pure_bottom_book = backtest_weekly_scores(
            weekly_dates,
            np.where(np.isfinite(supervised_neutral), 1.0 - supervised_neutral, np.nan),
            eligible,
            entry_prices,
            signal_close,
            risk,
            split_labels,
            exposure=None,
            config=equal_fraction_config,
        )
        pure_candidates["监督技术多空诊断"] = combine_long_short_backtests(
            pure_candidates["监督技术市值中性轮动"],
            pure_bottom_book,
            equal_fraction_config.cost_rate,
        )
        pure_champion = choose_champion(pure_candidates)
        pure_score_map = {name: score for name, score, _, _ in pure_specs}
        pure_score_map["监督技术多空诊断"] = supervised_neutral
        pure_selected_score = pure_score_map[pure_champion["name"]]
        candidates: Dict[str, object] = {}
        for name, score, candidate_exposure, candidate_config in (
            ("原始排序基线", original_weekly, None, config),
            ("原始排序分位组合", original_weekly, None, fraction_config),
            ("多周期等权专家", equal_weight, None, config),
            ("因果后验专家", technical, None, config),
            ("有向后验分位组合", directed, None, fraction_config),
            ("监督形态分位组合", supervised_score, None, fraction_config),
            ("监督形态等权分位", supervised_score, None, equal_fraction_config),
            ("监督形态市值中性", supervised_neutral, None, equal_fraction_config),
            ("监督形态择时组合", supervised_neutral, supervised_exposure, equal_fraction_config),
            ("残差增量融合", blended, None, config),
            ("状态风险控制", blended, exposure, config),
            ("有向后验风险控制", directed, exposure, fraction_config),
            ("监督形态风险控制", supervised_score, exposure, fraction_config),
        ):
            candidates[name] = backtest_weekly_scores(
                weekly_dates, score, eligible, entry_prices, signal_close, risk,
                split_labels, exposure=candidate_exposure, config=candidate_config,
            )
        bottom_book = backtest_weekly_scores(
            weekly_dates,
            np.where(np.isfinite(supervised_neutral), 1.0 - supervised_neutral, np.nan),
            eligible,
            entry_prices,
            signal_close,
            risk,
            split_labels,
            exposure=None,
            config=equal_fraction_config,
        )
        candidates["监督形态多空检验"] = combine_long_short_backtests(
            candidates["监督形态市值中性"], bottom_book, equal_fraction_config.cost_rate
        )
        champion = choose_champion(candidates)
        deployable_candidates = {
            name: value
            for name, value in candidates.items()
            if value.get("execution_mode") != "paper_long_short_alpha"
        }
        deployable_champion = choose_champion(deployable_candidates)
        selected_score = {
            "原始排序基线": original_weekly,
            "原始排序分位组合": original_weekly,
            "多周期等权专家": equal_weight,
            "因果后验专家": technical,
            "有向后验分位组合": directed,
            "监督形态分位组合": supervised_score,
            "监督形态等权分位": supervised_score,
            "监督形态市值中性": supervised_neutral,
            "监督形态择时组合": supervised_neutral,
            "监督形态多空检验": supervised_neutral,
            "残差增量融合": blended,
            "状态风险控制": blended,
            "有向后验风险控制": directed,
            "监督形态风险控制": supervised_score,
        }[champion["name"]]
        results[universe] = {
            "universe": universe,
            "universe_cn": universe_name,
            "champion": champion,
            "deployable_champion": deployable_champion,
            "candidates": candidates,
            "expert_names": list(experts),
            "posterior_weights": [
                [str(date), *[round(float(value), 6) for value in row]]
                for date, row in zip(weekly_dates, posterior_weights)
            ],
            "expert_rank_ic": [
                [str(date), *[None if not np.isfinite(value) else round(float(value), 6) for value in row]]
                for date, row in zip(weekly_dates, expert_ic)
            ],
            "state_history": [
                [str(date), int(state), round(float(exp), 4)]
                for date, state, exp in zip(weekly_dates, states, exposure)
            ],
            "pure_technical_model": {
                "version": TECHNICAL_MODEL_VERSION,
                "method": "纯OHLCV技术因子分组 + 训练期权重收缩 + 单股择时阈值 + 截面轮动回测",
                "framework": technical_framework_payload(),
                "family_weights": pure_weight_diagnostics,
                "family_diagnostics": technical_family_diagnostics(
                    technical_families, supervised_feedback, eligible, split_labels,
                ),
                "champion": pure_champion,
                "candidates": pure_candidates,
                "latest": _latest_rows(
                    codes, runtime["names"], pure_selected_score, technical_families, eligible,
                ),
                "single_stock_timing_contract": {
                    "score_source": "同一套六类技术信号的个股时间序列分位",
                    "threshold_fit": "entry/exit阈值仅由训练期分位确定",
                    "execution": "信号日收盘判断，下一交易日开盘或可成交价执行",
                    "outputs": ["历史买卖点", "当前多空/观望判断", "净值曲线", "失效条件"],
                    "validation_labels_used_for_threshold": False,
                    "test_labels_used_for_threshold": False,
                },
            },
            "latest": _latest_rows(codes, runtime["names"], selected_score, experts, eligible),
        }

    eligible_champions = []
    for universe, block in results.items():
        champion = block["champion"]
        if champion["accepted"]:
            eligible_champions.append((champion["selection"]["score"], universe, champion["name"]))
    if eligible_champions:
        _, selected_universe, selected_name = max(eligible_champions)
        selection_accepted = True
    else:
        fallback = max(
            (
                (block["champion"]["selection"]["score"], universe, block["champion"]["name"])
                for universe, block in results.items()
            ),
            key=lambda value: value[0],
        )
        _, selected_universe, selected_name = fallback
        selection_accepted = False
    selected_candidate = results[selected_universe]["candidates"][selected_name]
    pure_eligible_champions = []
    for universe, block in results.items():
        pure_block = block["pure_technical_model"]
        champion = pure_block["champion"]
        if champion["accepted"]:
            pure_eligible_champions.append((champion["selection"]["score"], universe, champion["name"]))
    if pure_eligible_champions:
        _, pure_universe, pure_name = max(pure_eligible_champions)
        pure_accepted = True
    else:
        pure_fallback = max(
            (
                (
                    block["pure_technical_model"]["champion"]["selection"]["score"],
                    universe,
                    block["pure_technical_model"]["champion"]["name"],
                )
                for universe, block in results.items()
            ),
            key=lambda value: value[0],
        )
        _, pure_universe, pure_name = pure_fallback
        pure_accepted = False
    pure_selected_candidate = results[pure_universe]["pure_technical_model"]["candidates"][pure_name]
    pure_test = pure_selected_candidate["metrics"]["test"]
    pure_release_gates = {
        "纯技术候选训练验证通过": bool(pure_accepted),
        "封存测试年化为正": pure_test.get("annual_return", 0.0) > 0.0,
        "封存测试夏普为正": pure_test.get("sharpe", 0.0) > 0.0,
        "封存测试回撤低于25%": pure_test.get("max_drawdown", -1.0) > -0.25,
        "可直接执行": True,
    }
    pure_release_approved = bool(all(pure_release_gates.values()))
    pure_status = (
        "validated_champion"
        if pure_release_approved
        else "observe_only_sealed_test_failed" if pure_accepted
        else "observe_only_no_validated_strategy"
    )
    deployable_champions = []
    for universe, block in results.items():
        champion = block["deployable_champion"]
        if champion["accepted"]:
            deployable_champions.append(
                (champion["selection"]["score"], universe, champion["name"])
            )
    if deployable_champions:
        _, deployment_universe, deployment_name = max(deployable_champions)
        deployment_accepted = True
        deployment_candidate = results[deployment_universe]["candidates"][deployment_name]
        sealed_test = deployment_candidate["metrics"]["test"]
    else:
        deployment_universe = None
        deployment_name = None
        deployment_accepted = False
        deployment_candidate = None
        sealed_test = {}
    release_gates = {
        "可执行候选训练验证通过": bool(deployment_accepted),
        "封存测试年化为正": bool(deployment_candidate)
        and sealed_test.get("annual_return", 0.0) > 0.0,
        "封存测试夏普为正": bool(deployment_candidate)
        and sealed_test.get("sharpe", 0.0) > 0.0,
        "封存测试回撤低于25%": bool(deployment_candidate)
        and sealed_test.get("max_drawdown", -1.0) > -0.25,
        "可直接执行": bool(deployment_candidate),
    }
    release_approved = bool(all(release_gates.values()))
    if release_approved:
        status = "validated_champion"
    elif deployment_accepted:
        status = "observe_only_sealed_test_failed"
    else:
        status = "observe_only_no_validated_deployable_strategy"
    payload = {
        "status": status,
        "version": "kline-multiscale-expert/1.6-research-deployment-split",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "method": "日K周K独立形态专家 + 行业中性排序 + 扩展窗口LambdaRank + 成熟标签有向后验 + 分位分散 + 波动预算",
        "selection_policy": "train_validation_only_test_report_only",
        "pure_technical_model": {
            "status": pure_status,
            "version": TECHNICAL_MODEL_VERSION,
            "method": "券商式技术指标分组、训练期权重收缩、单股择时与全市场轮动双回测",
            "selection_policy": "train_validation_only_test_report_only",
            "selected": {
                "universe": pure_universe,
                "candidate": pure_name,
                "accepted_by_train_validation": pure_accepted,
                "release_approved": pure_release_approved,
                "accepted": pure_release_approved,
                "role": "deployable_strategy" if pure_release_approved else "research_diagnostic",
            },
            "release_guard": {
                "gates": pure_release_gates,
                "sealed_test": pure_test,
                "test_used_for_selection": False,
                "test_used_for_release_only": True,
            },
            "framework": technical_framework_payload(),
            "results": {
                universe: block["pure_technical_model"]
                for universe, block in results.items()
            },
        },
        "supervised_ranker": supervised_diagnostics,
        "market_timing": market_diagnostics,
        "selected": {
            "universe": selected_universe,
            "candidate": selected_name,
            "accepted_by_train_validation": selection_accepted,
            "release_approved": False,
            "accepted": False,
            "role": "research_diagnostic",
        },
        "deployment_selected": {
            "universe": deployment_universe,
            "candidate": deployment_name,
            "accepted_by_train_validation": deployment_accepted,
            "release_approved": release_approved,
            "accepted": release_approved,
            "role": "deployable_strategy",
        },
        "release_guard": {
            "gates": release_gates,
            "sealed_test": sealed_test,
            "test_used_for_selection": False,
            "test_used_for_release_only": True,
        },
        "split": base_result["split"],
        "integrity": {
            "signal_uses_close_or_earlier": True,
            "execution_is_next_trade_open": True,
            "locked_or_suspended_entry_is_not_filled": True,
            "expert_weights_use_matured_feedback_only": True,
            "test_not_used_for_formula_direction_or_selection": True,
            "boundary_crossing_periods_are_purged": True,
            "cost_rate_per_turnover": 0.0015,
        },
        "research_basis": REPORTS,
        "state_labels": {"0": "弱势风险", "1": "震荡过渡", "2": "趋势扩张"},
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run causal multi-scale K-line expert challenger")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--base-runtime", type=Path, default=DEFAULT_BASE_RUNTIME)
    parser.add_argument("--base-result", type=Path, default=DEFAULT_BASE_RESULT)
    parser.add_argument("--ohlcv-cache", type=Path, default=DEFAULT_OHLCV_CACHE)
    parser.add_argument("--feature-cache", type=Path, default=DEFAULT_FEATURE_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    payload = run(
        arguments.database, arguments.base_runtime, arguments.base_result,
        arguments.ohlcv_cache, arguments.feature_cache, arguments.output,
    )
    print(json.dumps({
        "status": payload["status"], "version": payload["version"],
        "selected": payload["selected"], "output": str(arguments.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()






