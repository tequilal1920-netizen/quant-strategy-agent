from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

import numpy as np
import pandas as pd

SCRIPT = Path(__file__).resolve()
AGENT_ROOT = SCRIPT.parents[2]
EVENT_PATH = AGENT_ROOT / "output" / "kline_memory_learning" / "domain_memory_audit" / "wyckoff_events_with_domains_all_W-20D-60D_h20.pkl"
OUTPUT_DIR = AGENT_ROOT / "output" / "kline_memory_learning" / "domain_memory_evolver"
DESKTOP_DIR = Path(r"C:\Users\Rye\Desktop\技术分析")

TRAIN_END = "20181231"
VALID_START = "20190101"
VALID_END = "20211231"
TEST_START = "20220101"
HORIZON = 20

SCHEME_COLUMNS = {
    "全市场": "domain_global",
    "申万31行业": "domain_industry",
    "市值3种": "domain_size3",
    "风格4种": "domain_style4",
    "风格×市值12种": "domain_style_size12",
    "行业×市值": "domain_industry_size",
    "行业×风格": "domain_industry_style",
    "DS快变量3种": "domain_behavior_ds",
    "上市板块": "domain_board",
    "流动性3种": "domain_liquidity3",
}

STAGE_PRIOR = {
    "强趋势上行": 0.34,
    "稳态上行": 0.22,
    "下跌后修复": 0.15,
    "上升中回撤": 0.08,
    "横盘震荡": 0.00,
    "弱势下行": -0.20,
    "急跌风险": -0.36,
}

FREQ_PRIOR = {"W": 0.08, "20D": 0.03, "60D": 0.12}
RULE_BONUS = {
    "KLINE_WYCKOFF_SOS_BULL": 0.05,
    "KLINE_WYCKOFF_LPS_BULL": 0.09,
    "KLINE_WYCKOFF_ACCUMULATION_BULL": 0.08,
    "KLINE_WYCKOFF_SPRING_BULL": 0.08,
    "KLINE_WYCKOFF_SELLING_CLIMAX_BULL": 0.05,
    "KLINE_WYCKOFF_UPTHRUST_BEAR": -0.08,
    "KLINE_WYCKOFF_SOW_BEAR": -0.11,
    "KLINE_WYCKOFF_LPSY_BEAR": -0.08,
    "KLINE_WYCKOFF_DISTRIBUTION_BEAR": -0.09,
    "KLINE_WYCKOFF_BUYING_CLIMAX_BEAR": -0.07,
}


def split_frame(frame: pd.DataFrame, split: str) -> pd.DataFrame:
    if split == "train":
        return frame.loc[frame["split"].eq("train") & frame["future_date"].le(TRAIN_END)].copy()
    if split == "validation":
        return frame.loc[frame["split"].eq("validation") & frame["future_date"].le(VALID_END)].copy()
    if split == "test":
        return frame.loc[frame["split"].eq("test")].copy()
    raise ValueError(split)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def annualized(mean_horizon_return: float, horizon: int = HORIZON) -> float | None:
    if not math.isfinite(mean_horizon_return) or mean_horizon_return <= -0.95:
        return None
    return (1.0 + mean_horizon_return) ** (252.0 / horizon) - 1.0


def sharpe(series: pd.Series, horizon: int = HORIZON) -> float | None:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(values) < 3:
        return None
    std = float(values.std(ddof=1))
    if std <= 1e-12:
        return None
    return float(values.mean() / std * math.sqrt(252.0 / horizon))


def classify_stage(frame: pd.DataFrame) -> pd.Series:
    ret20 = pd.to_numeric(frame["ret20"], errors="coerce").fillna(0.0)
    ret60 = pd.to_numeric(frame["ret60"], errors="coerce").fillna(0.0)
    close_pos = pd.to_numeric(frame["close_position"], errors="coerce").fillna(0.5)
    vol20 = pd.to_numeric(frame["vol20"], errors="coerce").fillna(0.0)
    stage = pd.Series("横盘震荡", index=frame.index, dtype="object")
    stage.loc[(ret20 >= 0.05) & (ret60 >= 0.10)] = "强趋势上行"
    stage.loc[(ret20 >= 0.00) & (ret60 >= 0.00) & ~((ret20 >= 0.05) & (ret60 >= 0.10))] = "稳态上行"
    stage.loc[(ret20 >= 0.03) & (ret60 < 0.00)] = "下跌后修复"
    stage.loc[(ret20 < 0.00) & (ret60 > 0.03)] = "上升中回撤"
    stage.loc[(ret20 < -0.03) & (ret60 < -0.03)] = "弱势下行"
    stage.loc[(ret20 < -0.12) & (close_pos <= 0.45) & (vol20 >= 0.25)] = "急跌风险"
    return stage


def classify_confirmation(frame: pd.DataFrame) -> pd.Series:
    amount_ratio = pd.to_numeric(frame["amount_ratio"], errors="coerce").fillna(1.0)
    range_ratio = pd.to_numeric(frame["range_ratio"], errors="coerce").fillna(1.0)
    close_pos = pd.to_numeric(frame["close_position"], errors="coerce").fillna(0.5)
    direction = pd.to_numeric(frame["direction"], errors="coerce").fillna(0).astype(int)
    confirmed = ((direction > 0) & (close_pos >= 0.58) | (direction < 0) & (close_pos <= 0.42)) & (amount_ratio >= 0.95)
    exhaustion = (range_ratio >= 1.35) & (amount_ratio >= 1.35)
    low_effort = (amount_ratio <= 0.80) & (range_ratio <= 1.05)
    label = pd.Series("普通确认", index=frame.index, dtype="object")
    label.loc[confirmed] = "量价确认"
    label.loc[exhaustion] = "高波动高量"
    label.loc[low_effort] = "缩量低波"
    return label


def prepare_events(path: Path) -> pd.DataFrame:
    frame = pd.read_pickle(path)
    frame = frame.copy()
    frame["stage"] = classify_stage(frame)
    frame["confirmation"] = classify_confirmation(frame)
    frame["rule_bonus"] = frame["rule_id"].map(RULE_BONUS).fillna(0.0)
    ret20_num = pd.to_numeric(frame["ret20"], errors="coerce").fillna(0.0)
    ret60_num = pd.to_numeric(frame["ret60"], errors="coerce").fillna(0.0)
    market = frame.assign(_ret20=ret20_num, _ret60=ret60_num).groupby("date", sort=False).agg(
        market_ret20=("_ret20", "median"),
        market_ret60=("_ret60", "median"),
        breadth20=("_ret20", lambda item: float((item > 0).mean())),
        breadth60=("_ret60", lambda item: float((item > 0).mean())),
        panic_share=("_ret20", lambda item: float((item < -0.10).mean())),
    ).reset_index()
    frame = frame.merge(market, on="date", how="left")
    frame["market_state"] = "震荡平衡"
    frame.loc[(frame["market_ret20"] > 0.035) & (frame["market_ret60"] > 0.055) & (frame["breadth20"] > 0.56), "market_state"] = "普涨强势"
    frame.loc[(frame["market_ret20"] > 0.015) & (frame["breadth20"] > 0.52) & ~frame["market_state"].eq("普涨强势"), "market_state"] = "风险释放"
    frame.loc[(frame["market_ret20"] < -0.025) & (frame["market_ret60"] < -0.035) & (frame["breadth20"] < 0.46), "market_state"] = "风险收缩"
    frame.loc[(frame["panic_share"] > 0.22) & (frame["breadth20"] < 0.40), "market_state"] = "系统急跌"
    frame["market_prior"] = frame["market_state"].map({"普涨强势": 0.42, "风险释放": 0.22, "震荡平衡": 0.0, "风险收缩": -0.36, "系统急跌": -0.58}).fillna(0.0)
    frame["stage_prior"] = frame["stage"].map(STAGE_PRIOR).fillna(0.0)
    frame["freq_prior"] = frame["frequency"].map(FREQ_PRIOR).fillna(0.0)
    frame["strength"] = pd.to_numeric(frame["strength"], errors="coerce").fillna(0.5).clip(0.05, 0.98)
    frame["forward_return"] = pd.to_numeric(frame["forward_return"], errors="coerce").fillna(0.0).clip(-0.95, 3.0)
    frame["signed_return"] = pd.to_numeric(frame["signed_return"], errors="coerce").fillna(0.0).clip(-3.0, 3.0)
    for column in SCHEME_COLUMNS.values():
        frame[column] = frame[column].fillna("未分域").astype(str)
    return frame


def memory_table(frame: pd.DataFrame, keys: Sequence[str], prefix: str) -> pd.DataFrame:
    grouped = frame.groupby(list(keys), sort=False)
    out = grouped.agg(
        **{
            f"{prefix}_n": ("forward_return", "size"),
            f"{prefix}_avg_long": ("forward_return", "mean"),
            f"{prefix}_std_long": ("forward_return", "std"),
            f"{prefix}_hit_long": ("forward_return", lambda item: float((item > 0).mean())),
            f"{prefix}_avg_signed": ("signed_return", "mean"),
            f"{prefix}_tail10": ("forward_return", lambda item: float(np.nanquantile(item, 0.10))),
        }
    ).reset_index()
    for column in out.columns:
        if column.endswith(("avg_long", "std_long", "hit_long", "avg_signed", "tail10")):
            out[column] = pd.to_numeric(out[column], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def build_memory(train: pd.DataFrame, domain_col: str) -> Dict[str, pd.DataFrame]:
    local = train.copy()
    local["domain_value"] = local[domain_col].astype(str)
    return {
        "exact": memory_table(local, ["domain_value", "rule_id", "frequency", "stage", "confirmation"], "m1"),
        "stage": memory_table(local, ["domain_value", "rule_id", "frequency", "stage"], "m2"),
        "base": memory_table(local, ["domain_value", "rule_id", "frequency"], "m3"),
        "global_stage": memory_table(local.assign(domain_value="全市场"), ["domain_value", "rule_id", "frequency", "stage"], "m4"),
        "global_base": memory_table(local.assign(domain_value="全市场"), ["domain_value", "rule_id", "frequency"], "m5"),
    }


def attach_memory(events: pd.DataFrame, memory: Dict[str, pd.DataFrame], domain_col: str) -> pd.DataFrame:
    frame = events.copy()
    frame["domain_value"] = frame[domain_col].astype(str)
    frame = frame.merge(memory["exact"], on=["domain_value", "rule_id", "frequency", "stage", "confirmation"], how="left")
    frame = frame.merge(memory["stage"], on=["domain_value", "rule_id", "frequency", "stage"], how="left")
    frame = frame.merge(memory["base"], on=["domain_value", "rule_id", "frequency"], how="left")
    global_stage = memory["global_stage"].drop(columns=["domain_value"])
    global_base = memory["global_base"].drop(columns=["domain_value"])
    frame = frame.merge(global_stage, on=["rule_id", "frequency", "stage"], how="left")
    frame = frame.merge(global_base, on=["rule_id", "frequency"], how="left")
    return frame


def weighted_memory_score(frame: pd.DataFrame, profile: Dict[str, Any]) -> pd.Series:
    means, hits, signed, tails, weights = [], [], [], [], []
    level_weights = [profile["w_exact"], profile["w_stage"], profile["w_base"], profile["w_global_stage"], profile["w_global_base"]]
    for idx, prefix in enumerate(["m1", "m2", "m3", "m4", "m5"]):
        n = pd.to_numeric(frame.get(f"{prefix}_n"), errors="coerce").fillna(0.0)
        min_n = profile["min_exact"] if prefix == "m1" else profile["min_memory"]
        ok = n >= min_n
        conf = np.minimum(1.0, np.log1p(n) / math.log1p(profile["n_cap"])) * level_weights[idx]
        conf = conf.where(ok, 0.0)
        weights.append(conf)
        means.append(pd.to_numeric(frame.get(f"{prefix}_avg_long"), errors="coerce").fillna(0.0))
        hits.append(pd.to_numeric(frame.get(f"{prefix}_hit_long"), errors="coerce").fillna(0.5))
        signed.append(pd.to_numeric(frame.get(f"{prefix}_avg_signed"), errors="coerce").fillna(0.0))
        tails.append(pd.to_numeric(frame.get(f"{prefix}_tail10"), errors="coerce").fillna(0.0))
    weight_sum = sum(weights)
    weight_sum = weight_sum.replace(0.0, np.nan)
    avg_long = sum(w * m for w, m in zip(weights, means)) / weight_sum
    hit_long = sum(w * h for w, h in zip(weights, hits)) / weight_sum
    avg_signed = sum(w * s for w, s in zip(weights, signed)) / weight_sum
    tail10 = sum(w * t for w, t in zip(weights, tails)) / weight_sum
    avg_long = avg_long.fillna(profile["fallback_mean"])
    hit_long = hit_long.fillna(0.5)
    avg_signed = avg_signed.fillna(0.0)
    tail10 = tail10.fillna(0.0)

    stage_prior = pd.to_numeric(frame["stage_prior"], errors="coerce").fillna(0.0)
    market_prior = pd.to_numeric(frame["market_prior"], errors="coerce").fillna(0.0)
    freq_prior = pd.to_numeric(frame["freq_prior"], errors="coerce").fillna(0.0)
    rule_bonus = pd.to_numeric(frame["rule_bonus"], errors="coerce").fillna(0.0)
    strength = pd.to_numeric(frame["strength"], errors="coerce").fillna(0.5)
    ret20 = pd.to_numeric(frame["ret20"], errors="coerce").fillna(0.0)
    ret60 = pd.to_numeric(frame["ret60"], errors="coerce").fillna(0.0)
    close_pos = pd.to_numeric(frame["close_position"], errors="coerce").fillna(0.5)
    amount_ratio = pd.to_numeric(frame["amount_ratio"], errors="coerce").fillna(1.0)

    memory_component = np.tanh(avg_long / profile["return_scale"])
    hit_component = (hit_long - 0.5) * 2.0
    signed_component = np.tanh(avg_signed / profile["signed_scale"])
    tail_penalty = np.minimum(0.0, tail10 + profile["tail_guard"])
    trend_component = stage_prior + 0.45 * np.tanh(ret20 / 0.08) + 0.30 * np.tanh(ret60 / 0.16)
    quality_component = 0.12 * np.tanh((amount_ratio - 1.0) / 0.7) + 0.10 * (close_pos - 0.5)

    raw = (
        profile["a_memory"] * memory_component
        + profile["a_hit"] * hit_component
        + profile["a_signed"] * signed_component
        + profile["a_trend"] * trend_component
        + profile.get("a_market", 0.0) * market_prior
        + profile["a_rule"] * rule_bonus
        + profile["a_freq"] * freq_prior
        + profile["a_quality"] * quality_component
        + profile["a_strength"] * (strength - 0.5)
        + profile["a_tail"] * tail_penalty
    )
    return pd.Series(raw, index=frame.index).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def assign_positions(score: pd.Series, thresholds: Sequence[float]) -> pd.Series:
    t0, t25, t50, t75 = thresholds
    values = np.select(
        [score <= t0, score <= t25, score <= t50, score <= t75],
        [0.0, 0.25, 0.50, 0.75],
        default=1.0,
    )
    return pd.Series(values, index=score.index)


def apply_market_position_policy(scored: pd.DataFrame, position: pd.Series, thresholds: Sequence[float]) -> pd.Series:
    out = position.astype(float).copy()
    score = scored.get("_score_for_policy")
    if score is None:
        score = pd.Series(0.0, index=scored.index)
    t0, t25, t50, t75 = thresholds
    market = scored["market_state"].astype(str)
    stage = scored["stage"].astype(str)
    strong_stock = stage.isin(["强趋势上行", "稳态上行"])
    rebound_stock = stage.isin(["下跌后修复", "上升中回撤"])
    weak_stock = stage.isin(["弱势下行", "急跌风险"])

    # 市场急跌或风险收缩时，总闸先保护净值；只有个股自身仍是强趋势且评分很高，才允许半仓以上。
    crash = market.eq("系统急跌")
    risk_off = market.eq("风险收缩")
    out.loc[crash & ~strong_stock] = np.minimum(out.loc[crash & ~strong_stock], 0.25)
    out.loc[risk_off & weak_stock] = np.minimum(out.loc[risk_off & weak_stock], 0.25)
    out.loc[risk_off & ~strong_stock & ~weak_stock] = np.minimum(out.loc[risk_off & ~strong_stock & ~weak_stock], 0.50)

    # 普涨强势/风险释放时，不能因为单条记忆保守而长时间空仓；趋势确认股票给仓位下限。
    risk_on = market.isin(["普涨强势", "风险释放"])
    out.loc[risk_on & strong_stock & (score > t25)] = np.maximum(out.loc[risk_on & strong_stock & (score > t25)], 0.75)
    out.loc[risk_on & strong_stock & (score > t50)] = np.maximum(out.loc[risk_on & strong_stock & (score > t50)], 1.00)
    out.loc[market.eq("普涨强势") & rebound_stock & (score > t25)] = np.maximum(out.loc[market.eq("普涨强势") & rebound_stock & (score > t25)], 0.50)
    return out.clip(0.0, 1.0)
def evaluate_scored(frame: pd.DataFrame, position: pd.Series, cost_bp: float) -> Dict[str, Any]:
    local = frame[["date", "forward_return"]].copy()
    local["position"] = position.astype(float).clip(0.0, 1.0)
    cost = cost_bp / 10000.0
    local["strategy_event_return"] = local["position"] * local["forward_return"] - cost * local["position"]
    local["benchmark_event_return"] = local["forward_return"]
    by_date = local.groupby("date", sort=True).agg(
        strategy=("strategy_event_return", "mean"),
        benchmark=("benchmark_event_return", "mean"),
        position=("position", "mean"),
        event_count=("strategy_event_return", "size"),
    )
    active = by_date["strategy"] - by_date["benchmark"]
    annual = annualized(float(by_date["strategy"].mean()))
    bench = annualized(float(by_date["benchmark"].mean()))
    return {
        "events": int(len(local)),
        "signal_dates": int(len(by_date)),
        "annual_return": annual,
        "benchmark_annual_return": bench,
        "annual_excess": None if annual is None or bench is None else annual - bench,
        "sharpe": sharpe(by_date["strategy"]),
        "excess_sharpe": sharpe(active),
        "hit_rate": float((local["strategy_event_return"] > 0).mean()),
        "excess_hit_rate": float((local["strategy_event_return"] > local["benchmark_event_return"]).mean()),
        "avg_position": float(local["position"].mean()),
        "full_position_share": float(local["position"].ge(0.99).mean()),
        "low_position_share": float(local["position"].le(0.25).mean()),
    }


def objective(metric: Dict[str, Any], profile: Dict[str, Any]) -> float:
    sharpe_value = finite(metric.get("sharpe"), -99.0)
    excess = finite(metric.get("annual_excess"), -1.0)
    annual = finite(metric.get("annual_return"), -1.0)
    hit = finite(metric.get("hit_rate"), 0.0)
    avg_pos = finite(metric.get("avg_position"), 0.0)
    low = finite(metric.get("low_position_share"), 0.0)
    full = finite(metric.get("full_position_share"), 0.0)
    return (
        sharpe_value
        + profile["obj_excess"] * excess
        + profile["obj_annual"] * annual
        + 0.20 * (hit - 0.5)
        - profile["obj_low_penalty"] * max(0.0, low - profile["max_low_share"])
        - profile["obj_underinvest_penalty"] * max(0.0, profile["min_avg_position"] - avg_pos)
        + 0.05 * full
    )


def threshold_grid(scores: pd.Series) -> list[tuple[float, float, float, float]]:
    clean = scores.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return [(-0.4, -0.1, 0.1, 0.3)]
    quantile_sets = [
        (0.12, 0.28, 0.48, 0.68),
        (0.08, 0.22, 0.42, 0.62),
        (0.18, 0.34, 0.54, 0.74),
        (0.05, 0.18, 0.36, 0.58),
        (0.25, 0.45, 0.65, 0.82),
        (0.00, 0.15, 0.35, 0.60),
    ]
    out = []
    for qs in quantile_sets:
        vals = tuple(float(clean.quantile(q)) for q in qs)
        if vals == tuple(sorted(vals)):
            out.append(vals)
    # Absolute thresholds help when validation scores are very compressed.
    out.extend([
        (-0.40, -0.12, 0.08, 0.28),
        (-0.30, -0.05, 0.12, 0.32),
        (-0.20, 0.00, 0.18, 0.38),
        (-0.10, 0.08, 0.24, 0.44),
        (-0.55, -0.22, 0.02, 0.22),
        (-0.65, -0.35, -0.05, 0.12),
        (-0.50, -0.25, -0.02, 0.16),
        (-0.35, -0.18, 0.00, 0.18),
    ])
    dedup = []
    seen = set()
    for item in out:
        key = tuple(round(x, 6) for x in item)
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup


def profiles() -> list[Dict[str, Any]]:
    base = {
        "min_exact": 10,
        "min_memory": 25,
        "n_cap": 260,
        "return_scale": 0.055,
        "signed_scale": 0.045,
        "tail_guard": 0.08,
        "w_exact": 1.00,
        "w_stage": 0.72,
        "w_base": 0.48,
        "w_global_stage": 0.40,
        "w_global_base": 0.22,
        "a_memory": 0.62,
        "a_hit": 0.28,
        "a_signed": 0.16,
        "a_trend": 0.42,
        "a_market": 0.0,
        "a_rule": 0.35,
        "a_freq": 0.18,
        "a_quality": 0.16,
        "a_strength": 0.12,
        "a_tail": 1.20,
        "obj_excess": 1.40,
        "obj_annual": 0.25,
        "obj_low_penalty": 0.25,
        "obj_underinvest_penalty": 0.35,
        "max_low_share": 0.45,
        "min_avg_position": 0.48,
        "fallback_mean": 0.0,
    }
    variants = []
    for name, domain_col in [
        ("行业风格趋势Evolver", "domain_industry_style"),
        ("行业市值趋势Evolver", "domain_industry_size"),
        ("流动性趋势Evolver", "domain_liquidity3"),
        ("风格市值趋势Evolver", "domain_style_size12"),
        ("申万行业趋势Evolver", "domain_industry"),
        ("DS状态趋势Evolver", "domain_behavior_ds"),
    ]:
        item = dict(base)
        item["name"] = name
        item["domain_col"] = domain_col
        variants.append(item)
    aggressive = dict(base)
    aggressive.update({
        "name": "行业风格进攻Evolver",
        "domain_col": "domain_industry_style",
        "a_trend": 0.62,
        "a_market": 0.0,
        "a_rule": 0.45,
        "a_tail": 0.85,
        "obj_underinvest_penalty": 0.55,
        "min_avg_position": 0.58,
        "max_low_share": 0.35,
    })
    variants.append(aggressive)
    yield_first = dict(base)
    yield_first.update({
        "name": "行业风格收益优先Evolver",
        "domain_col": "domain_industry_style",
        "a_trend": 0.82,
        "a_rule": 0.38,
        "a_tail": 0.70,
        "obj_excess": 0.70,
        "obj_annual": 0.85,
        "obj_underinvest_penalty": 1.10,
        "obj_low_penalty": 0.55,
        "min_avg_position": 0.72,
        "max_low_share": 0.22,
    })
    variants.append(yield_first)
    liquidity_yield = dict(base)
    liquidity_yield.update({
        "name": "流动性收益优先Evolver",
        "domain_col": "domain_liquidity3",
        "a_trend": 0.78,
        "a_rule": 0.34,
        "a_tail": 0.72,
        "obj_excess": 0.65,
        "obj_annual": 0.90,
        "obj_underinvest_penalty": 1.00,
        "obj_low_penalty": 0.50,
        "min_avg_position": 0.72,
        "max_low_share": 0.22,
    })
    variants.append(liquidity_yield)
    defensive = dict(base)
    defensive.update({
        "name": "行业市值防守Evolver",
        "domain_col": "domain_industry_size",
        "a_tail": 1.65,
        "a_trend": 0.50,
        "a_market": 0.0,
        "obj_excess": 1.80,
        "max_low_share": 0.52,
        "min_avg_position": 0.42,
    })
    variants.append(defensive)
    return variants


def evaluate_profile(events: pd.DataFrame, profile: Dict[str, Any], cost_bp: float) -> Dict[str, Any]:
    train = split_frame(events, "train")
    valid = split_frame(events, "validation")
    test = split_frame(events, "test")
    domain_col = profile["domain_col"]
    train_memory = build_memory(train, domain_col)
    valid_scored = attach_memory(valid, train_memory, domain_col)
    valid_score = weighted_memory_score(valid_scored, profile)
    best = None
    valid_scored["_score_for_policy"] = valid_score
    for thresholds in threshold_grid(valid_score):
        pos = apply_market_position_policy(valid_scored, assign_positions(valid_score, thresholds), thresholds)
        metric = evaluate_scored(valid_scored, pos, cost_bp)
        obj = objective(metric, profile)
        if best is None or obj > best["objective"]:
            best = {"thresholds": thresholds, "validation": metric, "objective": obj}
    assert best is not None

    train_valid = pd.concat([train, valid], ignore_index=True)
    train_valid_memory = build_memory(train_valid, domain_col)
    train_scored = attach_memory(train, train_memory, domain_col)
    train_score = weighted_memory_score(train_scored, profile)
    train_scored["_score_for_policy"] = train_score
    train_metric = evaluate_scored(train_scored, apply_market_position_policy(train_scored, assign_positions(train_score, best["thresholds"]), best["thresholds"]), cost_bp)
    test_scored = attach_memory(test, train_valid_memory, domain_col)
    test_score = weighted_memory_score(test_scored, profile)
    test_position = assign_positions(test_score, best["thresholds"])
    test_metric = evaluate_scored(test_scored, test_position, cost_bp)
    return {
        "profile": profile["name"],
        "domain_col": domain_col,
        "domain_name": next((k for k, v in SCHEME_COLUMNS.items() if v == domain_col), domain_col),
        "thresholds": [round(float(x), 6) for x in best["thresholds"]],
        "objective": best["objective"],
        "train": train_metric,
        "validation": best["validation"],
        "test": test_metric,
    }


def flatten_result(result: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "profile": result["profile"],
        "domain_name": result["domain_name"],
        "domain_col": result["domain_col"],
        "thresholds": json.dumps(result["thresholds"], ensure_ascii=False),
        "objective": result["objective"],
    }
    for split in ("train", "validation", "test"):
        for key, value in result[split].items():
            row[f"{split}_{key}"] = value
    return row


def pct(value: Any) -> str:
    number = finite(value, float("nan"))
    return "" if not math.isfinite(number) else f"{number * 100:.2f}%"


def num(value: Any) -> str:
    number = finite(value, float("nan"))
    return "" if not math.isfinite(number) else f"{number:.3f}"


def write_outputs(results: list[Dict[str, Any]], output_dir: Path, desktop_dir: Path | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [flatten_result(item) for item in results]
    summary = pd.DataFrame(rows).sort_values(
        ["validation_sharpe", "validation_annual_excess", "test_sharpe"],
        ascending=False,
        na_position="last",
    )
    csv_path = output_dir / "wyckoff_domain_evolver_optimized_summary.csv"
    json_path = output_dir / "wyckoff_domain_evolver_optimized.json"
    txt_path = output_dir / "wyckoff_domain_evolver_optimized_conclusion.txt"
    summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    best_valid = summary.iloc[0]
    best_test = summary.sort_values(["test_sharpe", "test_annual_excess"], ascending=False, na_position="last").iloc[0]
    lines = [
        "Wyckoff/K线分域记忆Evolver优化结果",
        "",
        f"验证集最佳：{best_valid['profile']}（{best_valid['domain_name']}），验证Sharpe {num(best_valid['validation_sharpe'])}，验证年化 {pct(best_valid['validation_annual_return'])}，验证超额 {pct(best_valid['validation_annual_excess'])}，平均仓位 {pct(best_valid['validation_avg_position'])}。",
        f"测试集只报告最佳：{best_test['profile']}（{best_test['domain_name']}），测试Sharpe {num(best_test['test_sharpe'])}，测试年化 {pct(best_test['test_annual_return'])}，测试超额 {pct(best_test['test_annual_excess'])}，平均仓位 {pct(best_test['test_avg_position'])}。",
        "",
        "候选表：",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"- {row.profile}: 验证Sharpe {num(row.validation_sharpe)} / 年化{pct(row.validation_annual_return)} / 超额{pct(row.validation_annual_excess)} / 平均仓位{pct(row.validation_avg_position)}；"
            f"测试Sharpe {num(row.test_sharpe)} / 年化{pct(row.test_annual_return)} / 超额{pct(row.test_annual_excess)} / 平均仓位{pct(row.test_avg_position)}"
        )
    lines.extend(["", f"CSV：{csv_path}", f"JSON：{json_path}"])
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    if desktop_dir is not None:
        desktop_dir.mkdir(parents=True, exist_ok=True)
        summary.to_csv(desktop_dir / "Wyckoff分域记忆Evolver优化总表.csv", index=False, encoding="utf-8-sig")
        (desktop_dir / "Wyckoff分域记忆Evolver优化结论.txt").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize domain-aware Wyckoff memory Evolver on cached events.")
    parser.add_argument("--events", type=Path, default=EVENT_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--desktop-dir", type=Path, default=DESKTOP_DIR)
    parser.add_argument("--cost-bp", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print("[1/3] loading cached full-sample Wyckoff events", flush=True)
    events = prepare_events(args.events)
    print(f"[events] {len(events):,} events / {events['ts_code'].nunique():,} stocks", flush=True)
    print("[2/3] evaluating Evolver profiles", flush=True)
    results = []
    for profile in profiles():
        result = evaluate_profile(events, profile, args.cost_bp)
        results.append(result)
        print(
            f"[profile] {result['profile']}: validation Sharpe={num(result['validation']['sharpe'])}, "
            f"test Sharpe={num(result['test']['sharpe'])}, test excess={pct(result['test']['annual_excess'])}",
            flush=True,
        )
    print("[3/3] writing outputs", flush=True)
    write_outputs(results, args.output_dir, args.desktop_dir)
    best = max(results, key=lambda item: (finite(item['validation']['sharpe'], -99), finite(item['validation']['annual_excess'], -99)))
    print(f"[done] best validation profile={best['profile']} / validation Sharpe={num(best['validation']['sharpe'])}", flush=True)


if __name__ == "__main__":
    main()
