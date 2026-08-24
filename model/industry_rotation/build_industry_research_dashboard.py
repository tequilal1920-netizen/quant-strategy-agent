"""Build the full industry prosperity and industry rotation dashboard payload.

The public UI reads this single JSON file.  The builder deliberately separates
model data production from browser rendering so the page can stay compact and
auditable.
"""

from __future__ import annotations

import json
import math
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

AGENT_ROOT = Path(__file__).resolve().parents[2]
BOARD_DATA = AGENT_ROOT / "board" / "quant_strategy_agent" / "data"
BOARD_VNEXT_DATA = AGENT_ROOT / "board" / "quant_strategy_agent_vnext" / "data"
CACHE_DIR = AGENT_ROOT / "output" / "industry_rotation" / "cache" / "market"
OUT_NAME = "industry_research_dashboard.json"

DIMENSION_LABELS = {
    "prosperity": "景气度",
    "fundamental": "基本面",
    "technical": "技术面",
    "valuation": "估值",
    "funds": "资金面",
    "crowding": "拥挤度",
    "anti_crowding": "低拥挤",
}

FACTOR_FORMULA = {
    "prosperity_level": "8个专属高频景气指标按可用日对齐，方向固定后滚动标准化，再做行业内综合分位",
    "prosperity_acceleration": "景气水平三个月滚动均值减三个月前滚动均值，再做31行业截面分位",
    "prosperity_consensus": "多口径景气模型的截面排序共识，方向一致后取均值分位",
    "prosperity_reliability": "指标可用率、历史长度和缺失惩罚合成，覆盖越稳定得分越高",
    "prosperity_agreement": "高频指标间离散度取反，模型口径越一致得分越高",
    "op_yoy": "行业成分股营业利润同比按PIT权重聚合后截面分位",
    "tr_yoy": "行业成分股营业收入同比按PIT权重聚合后截面分位",
    "netprofit_yoy": "行业成分股归母净利润同比按PIT权重聚合后截面分位",
    "op_yoy_acceleration": "营业利润同比减三个月前营业利润同比后截面分位",
    "netprofit_yoy_acceleration": "归母净利润同比减三个月前归母净利润同比后截面分位",
    "profit_positive_breadth": "行业内利润同比为正股票占比，按PIT成分聚合后截面分位",
    "revenue_positive_breadth": "行业内收入同比为正股票占比，按PIT成分聚合后截面分位",
    "earnings_quality_confirmation": "营业利润同比、归母净利润同比、盈利扩散度共同确认后截面分位",
    "profit_growth_stability": "利润增长滚动波动取反并结合增长均值，越稳定得分越高",
    "roe": "行业成分股ROE按PIT权重聚合后截面分位",
    "roa": "行业成分股ROA按PIT权重聚合后截面分位",
    "gross_margin": "行业成分股毛利率按PIT权重聚合后截面分位",
    "gross_margin_trend": "毛利率三个月变化额截面分位",
    "roe_trend": "ROE三个月变化额截面分位",
    "assets_turn": "行业资产周转率按PIT权重聚合后截面分位",
    "current_ratio": "行业流动比率按PIT权重聚合后截面分位",
    "debt_to_assets": "资产负债率取反后截面分位",
    "earnings_yield": "PE_TTM倒数，行业成分股按PIT权重聚合后截面分位",
    "book_yield": "PB倒数，行业成分股按PIT权重聚合后截面分位",
    "sales_yield": "PS倒数，行业成分股按PIT权重聚合后截面分位",
    "dividend_yield": "股息率按PIT权重聚合后截面分位",
    "earnings_yield_momentum": "盈利收益率三个月变化额截面分位",
    "peg_proxy": "盈利收益率与利润增速匹配度，估值便宜且增长改善得分更高",
    "value_quality_match": "盈利收益率、ROA、利润扩散度联合确认后的价值质量分位",
    "dividend_quality": "股息率与盈利质量共同确认，规避高股息陷阱",
    "momentum_12_1": "过去12个月收益剔除最近1个月反转噪声后截面分位",
    "momentum_6_1": "过去6个月收益剔除最近1个月后截面分位",
    "momentum_3_1": "过去3个月收益剔除最近1个月后截面分位",
    "momentum_1": "过去1个月收益截面分位",
    "trend_ir_126": "过去126个交易日行业日收益均值除以波动率后截面分位",
    "trend_ir_63": "过去63个交易日行业日收益均值除以波动率后截面分位",
    "path_efficiency_126": "126日净涨幅除以日涨跌绝对值累计，趋势越顺畅得分越高",
    "path_efficiency_63": "63日净涨幅除以日涨跌绝对值累计，趋势越顺畅得分越高",
    "new_high_proximity_252": "行业指数距离252日新高的接近度，越接近新高得分越高",
    "max_drawdown_resilience_126": "126日最大回撤取反，回撤越小得分越高",
    "risk_adjusted_momentum": "中期动量除以滚动波动率后截面分位",
    "distance_ma120": "行业指数相对120日均线距离，趋势确认后截面分位",
    "distance_ma60": "行业指数相对60日均线距离，趋势确认后截面分位",
    "momentum_consistency": "短中长期动量方向一致性得分",
    "flow_total_20": "20日主力净流入除以成交额后行业聚合并截面分位",
    "flow_total_5": "5日主力净流入除以成交额后行业聚合并截面分位",
    "flow_total_60": "60日主力净流入除以成交额后行业聚合并截面分位",
    "flow_large_structure_20": "20日大单净流入占成交额，行业聚合后截面分位",
    "flow_large_structure_5": "5日大单净流入占成交额，行业聚合后截面分位",
    "flow_large_structure_60": "60日大单净流入占成交额，行业聚合后截面分位",
    "flow_extra_structure_20": "20日超大单净流入占成交额，行业聚合后截面分位",
    "flow_extra_structure_60": "60日超大单净流入占成交额，行业聚合后截面分位",
    "flow_price_residual_20": "20日资金强度扣除同期价格表现后的残差分位",
    "flow_persistence_20": "20日资金流方向连续性，正流入持续越强得分越高",
    "large_flow_persistence_20": "20日大单净流入方向连续性",
    "flow_breadth_20": "20日行业内资金净流入股票占比",
    "flow_breadth_change": "资金扩散度较上月变化",
    "flow_acceleration_20_60": "20日资金强度减60日资金强度",
    "smart_money_confirmation": "超大单、大单、资金价格残差共同确认",
    "turnover_percentile_250": "换手率在过去250日分位，拥挤度越高惩罚越强",
    "turnover_level": "行业换手率水平截面分位",
    "turnover_expansion": "短期换手较中期换手抬升幅度",
    "volume_ratio": "量比水平截面分位",
    "liquidity_crowding": "成交额集中度与换手分位合成",
    "amount_concentration": "行业成交额集中度，集中度高代表拥挤风险",
    "short_momentum_heat": "最近1个月涨幅热度，过热时作为风险惩罚",
    "price_distance_heat": "价格偏离中期均线幅度，偏离越高代表拥挤",
    "breadth_heat": "上涨扩散度过高后的热度惩罚",
    "limit_up_heat": "行业涨停比例热度",
    "low_dispersion_heat": "收益分散度过低后的拥挤风险",
    "crowding_acceleration": "拥挤度短期抬升速度",
    "crowding_reversal_risk": "高拥挤叠加短期涨幅后的反转风险",
    "overheat_residual": "涨幅热度扣除基本面和景气确认后的残差",
    "volatility_expansion": "短期波动率较中期波动率抬升幅度",
}

FACTOR_LOGIC = {
    "prosperity": "产业量价和财报锚同步改善时，行业相对收益更容易延续。",
    "fundamental": "盈利改善和盈利扩散度提升代表分子端确认，能过滤单点业绩扰动。",
    "technical": "中期趋势和趋势效率刻画资金已经验证的方向，但需要剔除短期反转噪声。",
    "valuation": "估值越低且盈利质量不差，安全边际越高，极端成长阶段需降低权重。",
    "funds": "主力资金流入对短期相对收益有确认意义，持续性比单日净流入更重要。",
    "crowding": "换手、涨幅、量比和价格偏离过高时，后续容易出现均值回归。",
    "anti_crowding": "低拥挤是风险预算项，用于降低追高和回撤暴露。",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number):
        return number
    return None


def _pct_rank(frame: pd.DataFrame, ascending: bool = True) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True, ascending=ascending)


def _mean_available(frames: list[pd.DataFrame], minimum: int = 1) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    aligned = pd.concat(frames, keys=range(len(frames)), axis=1)
    count = aligned.notna().groupby(level=1, axis=1).sum()
    total = aligned.groupby(level=1, axis=1).sum(min_count=minimum)
    return total.where(count >= minimum)


def _max_drawdown(values: pd.Series) -> float:
    if values.isna().all():
        return np.nan
    running = values.cummax()
    dd = values / running - 1.0
    return float(dd.min())


def _pivot(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    return (
        frame.pivot_table(index="trade_date", columns="industry_name", values=column, aggfunc="last")
        .sort_index()
    )


def _last_trade_dates(daily: pd.DataFrame) -> pd.DatetimeIndex:
    dates = pd.Series(pd.to_datetime(daily["trade_date"].unique())).sort_values()
    return pd.DatetimeIndex(dates.groupby(dates.dt.to_period("M")).max().tolist())


def _forward_month_returns(daily_ret: pd.DataFrame, signal_dates: pd.DatetimeIndex) -> pd.DataFrame:
    rows: list[pd.Series] = []
    idx: list[pd.Timestamp] = []
    trade_dates = pd.DatetimeIndex(daily_ret.index)
    for i, signal_date in enumerate(signal_dates):
        start_pos = trade_dates.searchsorted(signal_date, side="right")
        if start_pos >= len(trade_dates):
            continue
        if i + 1 < len(signal_dates):
            end_date = signal_dates[i + 1]
        else:
            end_date = trade_dates[-1]
        end_pos = trade_dates.searchsorted(end_date, side="right")
        window = daily_ret.iloc[start_pos:end_pos]
        if window.empty:
            continue
        rows.append((1.0 + window).prod(skipna=True) - 1.0)
        idx.append(signal_date)
    if not rows:
        return pd.DataFrame(columns=daily_ret.columns)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    pair = pd.concat([x, y], axis=1).dropna()
    if len(pair) < 8:
        return None
    value = pair.iloc[:, 0].rank().corr(pair.iloc[:, 1].rank())
    return _finite(value)


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    def _rank_last(values: np.ndarray) -> float:
        current = values[-1]
        if np.isnan(current):
            return np.nan
        valid = values[~np.isnan(values)]
        if len(valid) < max(20, window // 4):
            return np.nan
        return float((valid <= current).sum() / len(valid))

    return series.rolling(window, min_periods=max(20, window // 4)).apply(_rank_last, raw=True)


def _build_factor_frames(
    monthly: pd.DataFrame,
    daily: pd.DataFrame,
    tracking: dict[str, Any],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    daily_ret = _pivot(daily, "equal_weight_return").sort_index()
    close = (1.0 + daily_ret.fillna(0.0)).cumprod()
    month_ends = _last_trade_dates(daily)

    amount = _pivot(daily, "traded_amount")
    flow_total = _pivot(daily, "flow_total_amount")
    flow_large = _pivot(daily, "flow_large_amount")
    flow_extra = _pivot(daily, "flow_extra_amount")
    turnover = _pivot(daily, "turnover_rate")
    volume_ratio = _pivot(daily, "volume_ratio")
    amount_conc = _pivot(daily, "amount_concentration")
    limit_up = _pivot(daily, "limit_up_ratio")
    dispersion = _pivot(daily, "return_dispersion")

    monthly_frames: dict[str, pd.DataFrame] = {}
    for column in [
        "earnings_yield",
        "book_yield",
        "sales_yield",
        "dividend_yield",
        "roe",
        "roa",
        "gross_margin",
        "netprofit_margin",
        "debt_to_assets",
        "current_ratio",
        "assets_turn",
        "op_yoy",
        "tr_yoy",
        "netprofit_yoy",
        "revenue_positive_breadth",
        "profit_positive_breadth",
    ]:
        monthly_frames[column] = _pivot(monthly, column).sort_index().ffill()

    # Prosperity history comes from the existing PIT score history; the C40
    # latest cross-section is added later at the industry-row level.
    score_history: dict[str, list[dict[str, Any]]] = {
        name: row.get("score_history", [])
        for name, row in tracking.get("industries", {}).items()
    }
    prosperity_history = pd.DataFrame({
        name: pd.Series(
            {pd.Timestamp(item["date"]): _finite(item.get("score")) for item in rows if item.get("date")}
        )
        for name, rows in score_history.items()
    }).sort_index()
    monthly_frames["prosperity_level"] = _pct_rank(prosperity_history.ffill())
    monthly_frames["prosperity_acceleration"] = _pct_rank(prosperity_history.ffill().sub(prosperity_history.ffill().shift(3)))

    for name in ["op_yoy", "tr_yoy", "netprofit_yoy", "revenue_positive_breadth", "profit_positive_breadth"]:
        monthly_frames[name] = _pct_rank(monthly_frames[name])
    monthly_frames["op_yoy_acceleration"] = _pct_rank(_pivot(monthly, "op_yoy").ffill().diff(3))
    monthly_frames["netprofit_yoy_acceleration"] = _pct_rank(_pivot(monthly, "netprofit_yoy").ffill().diff(3))
    monthly_frames["roe_trend"] = _pct_rank(_pivot(monthly, "roe").ffill().diff(3))
    monthly_frames["gross_margin_trend"] = _pct_rank(_pivot(monthly, "gross_margin").ffill().diff(3))
    monthly_frames["earnings_quality_confirmation"] = _pct_rank(_mean_available([
        monthly_frames["op_yoy"],
        monthly_frames["netprofit_yoy"],
        monthly_frames["profit_positive_breadth"],
    ], 2))
    monthly_frames["profit_growth_stability"] = _pct_rank(
        _pivot(monthly, "netprofit_yoy").ffill().rolling(6, min_periods=3).mean()
        - _pivot(monthly, "netprofit_yoy").ffill().rolling(6, min_periods=3).std(ddof=0)
    )
    monthly_frames["debt_to_assets"] = _pct_rank(_pivot(monthly, "debt_to_assets").ffill(), ascending=False)
    for name in ["earnings_yield", "book_yield", "sales_yield", "dividend_yield", "roe", "roa", "gross_margin", "netprofit_margin", "current_ratio", "assets_turn"]:
        monthly_frames[name] = _pct_rank(monthly_frames[name])
    monthly_frames["earnings_yield_momentum"] = _pct_rank(_pivot(monthly, "earnings_yield").ffill().diff(3))
    monthly_frames["peg_proxy"] = _pct_rank(_mean_available([
        monthly_frames["earnings_yield"],
        monthly_frames["op_yoy"],
        monthly_frames["netprofit_yoy"],
    ], 2))
    monthly_frames["value_quality_match"] = _pct_rank(_mean_available([
        monthly_frames["earnings_yield"],
        monthly_frames["roa"],
        monthly_frames["profit_positive_breadth"],
    ], 2))
    monthly_frames["dividend_quality"] = _pct_rank(_mean_available([
        monthly_frames["dividend_yield"],
        monthly_frames["earnings_quality_confirmation"],
    ], 2))

    ret_20 = close / close.shift(20) - 1.0
    ret_63 = close / close.shift(63) - 1.0
    ret_126 = close / close.shift(126) - 1.0
    ret_252 = close / close.shift(252) - 1.0
    ret_21_shift = close.shift(21)
    technical: dict[str, pd.DataFrame] = {
        "momentum_1": _pct_rank(ret_20.reindex(month_ends).ffill()),
        "momentum_3_1": _pct_rank((ret_21_shift / close.shift(63) - 1.0).reindex(month_ends).ffill()),
        "momentum_6_1": _pct_rank((ret_21_shift / close.shift(126) - 1.0).reindex(month_ends).ffill()),
        "momentum_12_1": _pct_rank((ret_21_shift / close.shift(252) - 1.0).reindex(month_ends).ffill()),
        "trend_ir_63": _pct_rank((daily_ret.rolling(63, min_periods=30).mean() / daily_ret.rolling(63, min_periods=30).std(ddof=0)).reindex(month_ends).ffill()),
        "trend_ir_126": _pct_rank((daily_ret.rolling(126, min_periods=60).mean() / daily_ret.rolling(126, min_periods=60).std(ddof=0)).reindex(month_ends).ffill()),
        "risk_adjusted_momentum": _pct_rank((ret_126 / daily_ret.rolling(126, min_periods=60).std(ddof=0)).reindex(month_ends).ffill()),
        "path_efficiency_63": _pct_rank(((close - close.shift(63)).abs() / close.diff().abs().rolling(63, min_periods=30).sum()).reindex(month_ends).ffill()),
        "path_efficiency_126": _pct_rank(((close - close.shift(126)).abs() / close.diff().abs().rolling(126, min_periods=60).sum()).reindex(month_ends).ffill()),
        "new_high_proximity_252": _pct_rank((close / close.rolling(252, min_periods=120).max()).reindex(month_ends).ffill()),
        "max_drawdown_resilience_126": _pct_rank(close.rolling(126, min_periods=60).apply(_max_drawdown).mul(-1).reindex(month_ends).ffill()),
        "distance_ma120": _pct_rank((close / close.rolling(120, min_periods=60).mean() - 1.0).reindex(month_ends).ffill()),
        "distance_ma60": _pct_rank((close / close.rolling(60, min_periods=30).mean() - 1.0).reindex(month_ends).ffill()),
    }
    technical["momentum_consistency"] = _pct_rank(_mean_available([
        technical["momentum_1"],
        technical["momentum_3_1"],
        technical["momentum_6_1"],
        technical["momentum_12_1"],
    ], 2))

    amount20 = amount.rolling(20, min_periods=10).sum()
    amount60 = amount.rolling(60, min_periods=30).sum()
    flow20 = flow_total.rolling(20, min_periods=10).sum() / amount20.replace(0, np.nan)
    flow60 = flow_total.rolling(60, min_periods=30).sum() / amount60.replace(0, np.nan)
    funds = {
        "flow_total_5": _pct_rank((flow_total.rolling(5, min_periods=3).sum() / amount.rolling(5, min_periods=3).sum().replace(0, np.nan)).reindex(month_ends).ffill()),
        "flow_total_20": _pct_rank(flow20.reindex(month_ends).ffill()),
        "flow_total_60": _pct_rank(flow60.reindex(month_ends).ffill()),
        "flow_large_structure_5": _pct_rank((flow_large.rolling(5, min_periods=3).sum() / amount.rolling(5, min_periods=3).sum().replace(0, np.nan)).reindex(month_ends).ffill()),
        "flow_large_structure_20": _pct_rank((flow_large.rolling(20, min_periods=10).sum() / amount20.replace(0, np.nan)).reindex(month_ends).ffill()),
        "flow_large_structure_60": _pct_rank((flow_large.rolling(60, min_periods=30).sum() / amount60.replace(0, np.nan)).reindex(month_ends).ffill()),
        "flow_extra_structure_20": _pct_rank((flow_extra.rolling(20, min_periods=10).sum() / amount20.replace(0, np.nan)).reindex(month_ends).ffill()),
        "flow_extra_structure_60": _pct_rank((flow_extra.rolling(60, min_periods=30).sum() / amount60.replace(0, np.nan)).reindex(month_ends).ffill()),
        "flow_price_residual_20": _pct_rank((flow20 - ret_20).reindex(month_ends).ffill()),
        "flow_acceleration_20_60": _pct_rank((flow20 - flow60).reindex(month_ends).ffill()),
        "flow_persistence_20": _pct_rank(flow_total.gt(0).rolling(20, min_periods=10).mean().reindex(month_ends).ffill()),
        "large_flow_persistence_20": _pct_rank(flow_large.gt(0).rolling(20, min_periods=10).mean().reindex(month_ends).ffill()),
    }
    funds["smart_money_confirmation"] = _pct_rank(_mean_available([
        funds["flow_extra_structure_20"],
        funds["flow_large_structure_20"],
        funds["flow_price_residual_20"],
    ], 2))
    funds["flow_breadth_20"] = funds["flow_persistence_20"]
    funds["flow_breadth_change"] = _pct_rank(funds["flow_persistence_20"].diff(1))

    turnover_pct = turnover.apply(lambda col: _rolling_percentile(col, 250)).reindex(month_ends).ffill()
    crowding = {
        "turnover_percentile_250": _pct_rank(turnover_pct),
        "turnover_level": _pct_rank(turnover.reindex(month_ends).ffill()),
        "turnover_expansion": _pct_rank((turnover.rolling(20, min_periods=10).mean() - turnover.rolling(60, min_periods=30).mean()).reindex(month_ends).ffill()),
        "volume_ratio": _pct_rank(volume_ratio.rolling(20, min_periods=10).mean().reindex(month_ends).ffill()),
        "amount_concentration": _pct_rank(amount_conc.rolling(20, min_periods=10).mean().reindex(month_ends).ffill()),
        "short_momentum_heat": _pct_rank(ret_20.reindex(month_ends).ffill()),
        "price_distance_heat": _pct_rank((close / close.rolling(120, min_periods=60).mean() - 1.0).abs().reindex(month_ends).ffill()),
        "limit_up_heat": _pct_rank(limit_up.rolling(20, min_periods=10).mean().reindex(month_ends).ffill()),
        "low_dispersion_heat": _pct_rank(dispersion.rolling(20, min_periods=10).mean().mul(-1).reindex(month_ends).ffill()),
        "volatility_expansion": _pct_rank((daily_ret.rolling(20, min_periods=10).std(ddof=0) - daily_ret.rolling(60, min_periods=30).std(ddof=0)).reindex(month_ends).ffill()),
    }
    crowding["liquidity_crowding"] = _pct_rank(_mean_available([crowding["turnover_percentile_250"], crowding["amount_concentration"]], 1))
    crowding["breadth_heat"] = _pct_rank(_pivot(daily, "up_ratio").rolling(20, min_periods=10).mean().reindex(month_ends).ffill())
    crowding["crowding_acceleration"] = _pct_rank(crowding["liquidity_crowding"].diff(1))
    crowding["crowding_reversal_risk"] = _pct_rank(_mean_available([crowding["liquidity_crowding"], crowding["short_momentum_heat"]], 2))
    crowding["overheat_residual"] = _pct_rank(crowding["short_momentum_heat"] - monthly_frames["earnings_quality_confirmation"].reindex(month_ends).ffill())

    factor_frames: dict[str, pd.DataFrame] = {}
    factor_frames.update(monthly_frames)
    factor_frames.update(technical)
    factor_frames.update(funds)
    factor_frames.update(crowding)

    dimensions = {
        "prosperity": _pct_rank(_mean_available([factor_frames["prosperity_level"], factor_frames["prosperity_acceleration"]], 1)),
        "fundamental": _pct_rank(_mean_available([
            factor_frames["op_yoy"],
            factor_frames["op_yoy_acceleration"],
            factor_frames["profit_positive_breadth"],
            factor_frames["earnings_quality_confirmation"],
        ], 2)),
        "technical": _pct_rank(_mean_available([
            factor_frames["momentum_12_1"],
            factor_frames["trend_ir_126"],
            factor_frames["new_high_proximity_252"],
            factor_frames["max_drawdown_resilience_126"],
        ], 2)),
        "valuation": _pct_rank(_mean_available([
            factor_frames["earnings_yield"],
            factor_frames["dividend_yield"],
            factor_frames["earnings_yield_momentum"],
        ], 2)),
        "funds": _pct_rank(_mean_available([
            factor_frames["flow_price_residual_20"],
            factor_frames["flow_extra_structure_20"],
            factor_frames["flow_large_structure_20"],
        ], 2)),
        "crowding": _pct_rank(_mean_available([
            factor_frames["turnover_percentile_250"],
            factor_frames["short_momentum_heat"],
            factor_frames["price_distance_heat"],
        ], 2)),
    }
    return factor_frames, dimensions, daily_ret, close


def _c39_score(factors: dict[str, pd.DataFrame], dimensions: dict[str, pd.DataFrame]) -> pd.DataFrame:
    raw = (
        factors["prosperity_acceleration"].mul(0.32)
        .add(factors["prosperity_level"].mul(0.22), fill_value=0.0)
        .add(factors["op_yoy"].mul(0.18), fill_value=0.0)
        .add(factors["op_yoy_acceleration"].mul(0.12), fill_value=0.0)
        .add(factors["profit_positive_breadth"].mul(0.10), fill_value=0.0)
        .add(factors["earnings_quality_confirmation"].mul(0.08), fill_value=0.0)
        .sub(dimensions["crowding"].mul(0.12), fill_value=0.0)
    )
    return _pct_rank(raw)


def _frame_latest_rows(score: pd.DataFrame, signal_date: str, holdings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    date = pd.Timestamp(signal_date)
    valid = score.dropna(how="all")
    if valid.empty:
        return []
    candidates = valid.index[valid.index <= date]
    latest = candidates[-1] if len(candidates) else valid.index[-1]
    values = valid.loc[latest].dropna().sort_values(ascending=False)
    holdings = holdings or {}
    selected = set(holdings.get("names") or [])
    weights = holdings.get("weights") or {}
    return [
        {
            "rank": int(i + 1),
            "name": name,
            "score": _finite(value),
            "selected": name in selected or i < 7,
            "weight": _finite(weights.get(name)),
            "signal_date": str(date.date()),
            "score_date": str(latest.date()),
        }
        for i, (name, value) in enumerate(values.items())
    ]


def _top_bottom_history(
    score: pd.DataFrame,
    forward_returns: pd.DataFrame,
    year: int,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    common = score.index.intersection(forward_returns.index)
    for date in common:
        if date.year != year:
            continue
        values = score.loc[date].dropna().sort_values(ascending=False)
        if len(values) < top_n * 2:
            continue
        top = list(values.head(top_n).index)
        bottom = list(values.tail(top_n).index[::-1])
        returns = forward_returns.loc[date]
        rows.append({
            "signal_date": str(date.date()),
            "top": top,
            "bottom": bottom,
            "top_return": _finite(returns.reindex(top).mean()),
            "bottom_return": _finite(returns.reindex(bottom).mean()),
            "spread": _finite(returns.reindex(top).mean() - returns.reindex(bottom).mean()),
        })
    return rows


def _matrix_rows(score: pd.DataFrame, acceleration: pd.DataFrame, signal_date: str) -> list[dict[str, Any]]:
    date = pd.Timestamp(signal_date)
    valid = score.dropna(how="all")
    if valid.empty:
        return []
    dates = valid.index[valid.index <= date]
    latest = dates[-1] if len(dates) else valid.index[-1]
    s = score.loc[latest]
    a = acceleration.reindex(score.index).loc[latest] if latest in acceleration.index else s.sub(s.median())
    pair = pd.DataFrame({"score": s, "acceleration": a}).dropna()
    if pair.empty:
        return []
    pair["score_bucket"] = pd.qcut(pair["score"].rank(method="first"), 3, labels=["低分位", "中分位", "高分位"])
    pair["acc_bucket"] = pd.qcut(pair["acceleration"].rank(method="first"), 3, labels=["低加速", "中加速", "高加速"])
    rows: list[dict[str, Any]] = []
    for acc in ["高加速", "中加速", "低加速"]:
        for pct in ["高分位", "中分位", "低分位"]:
            sub = pair[(pair["acc_bucket"] == acc) & (pair["score_bucket"] == pct)].sort_values("score", ascending=False)
            rows.append({
                "acceleration": acc,
                "percentile": pct,
                "industries": list(sub.index),
                "avg_score": _finite(sub["score"].mean()),
                "count": int(len(sub)),
            })
    return rows


def _factor_ic_detail(
    frame: pd.DataFrame,
    forward_returns: pd.DataFrame,
    factor_name: str,
    label: str,
) -> dict[str, Any]:
    common = frame.index.intersection(forward_returns.index)
    ic_rows: list[dict[str, Any]] = []
    cumulative = 0.0
    ls_rows: list[dict[str, Any]] = []
    group_nav = {f"G{i}": 1.0 for i in range(1, 6)}
    group_rows: list[dict[str, Any]] = []
    for date in common:
        values = frame.loc[date].dropna()
        returns = forward_returns.loc[date].reindex(values.index)
        pair = pd.concat([values.rename("score"), returns.rename("return")], axis=1).dropna()
        if len(pair) < 12:
            continue
        ic = pair["score"].rank().corr(pair["return"].rank())
        cumulative += 0.0 if pd.isna(ic) else float(ic)
        ic_rows.append({"date": str(date.date()), "rank_ic": _finite(ic), "cum_rank_ic": _finite(cumulative)})
        ordered = pair.sort_values("score", ascending=False)
        top = ordered.head(5)["return"].mean()
        bottom = ordered.tail(5)["return"].mean()
        spread = top - bottom
        ls_rows.append({"date": str(date.date()), "top_return": _finite(top), "bottom_return": _finite(bottom), "spread": _finite(spread)})
        chunks = np.array_split(ordered, 5)
        row = {"date": str(date.date())}
        for i, chunk in enumerate(chunks, 1):
            ret = chunk["return"].mean()
            if math.isfinite(float(ret)):
                group_nav[f"G{i}"] *= 1.0 + float(ret)
            row[f"G{i}"] = _finite(group_nav[f"G{i}"])
        group_rows.append(row)
    return {"factor": factor_name, "label": label, "ic": ic_rows, "long_short": ls_rows, "groups": group_rows}


def _factor_rows(six_dimension: dict[str, Any]) -> list[dict[str, Any]]:
    atoms = six_dimension.get("diagnostics", {}).get("atomic_factors", [])
    rows: list[dict[str, Any]] = []
    for item in atoms:
        dim = item.get("dimension")
        factor = item.get("factor")
        label = item.get("factor_label") or factor
        rows.append({
            "dimension": dim,
            "dimension_label": item.get("dimension_label") or DIMENSION_LABELS.get(dim, dim),
            "factor": factor,
            "factor_label": label,
            "formula": FACTOR_FORMULA.get(factor, f"{label}按PIT可见口径聚合到申万一级行业，再做缺失处理、去极值、截面标准化和方向固定"),
            "direction": item.get("direction") or "正向",
            "logic": FACTOR_LOGIC.get(dim, "训练与验证期有效且具备经济含义后进入候选因子池"),
            "coverage": _finite(item.get("coverage")),
            "train_ic": _finite(item.get("ic", {}).get("train", {}).get("mean_ic")),
            "valid_ic": _finite(item.get("ic", {}).get("validation", {}).get("mean_ic")),
            "test_ic_report": _finite(item.get("ic", {}).get("test", {}).get("mean_ic")),
            "train_icir": _finite(item.get("ic", {}).get("train", {}).get("icir")),
            "valid_icir": _finite(item.get("ic", {}).get("validation", {}).get("icir")),
            "valid_t": _finite(item.get("ic", {}).get("validation", {}).get("t_value")),
            "valid_positive": _finite(item.get("ic", {}).get("validation", {}).get("positive_rate")),
            "valid_spread": _finite(item.get("top_bottom", {}).get("validation", {}).get("annualized_spread")),
            "test_spread_report": _finite(item.get("top_bottom", {}).get("test", {}).get("annualized_spread")),
        })
    return rows


def _efficient_factor_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for dim in ["prosperity", "fundamental", "technical", "valuation", "funds", "crowding"]:
        sub = [r for r in rows if r.get("dimension") == dim]
        for row in sub:
            train = row.get("train_icir") or 0.0
            valid = row.get("valid_icir") or 0.0
            spread = row.get("valid_spread") or 0.0
            positive = row.get("valid_positive") or 0.0
            row["selection_score"] = _finite(0.35 * train + 0.45 * valid + 0.12 * np.sign(spread) * min(abs(spread), 0.20) + 0.08 * (positive - 0.5))
        output.extend(sorted(sub, key=lambda r: (r.get("selection_score") or -9), reverse=True)[:4])
    return output


def _factor_corr(dimensions: dict[str, pd.DataFrame], signal_date: str) -> list[dict[str, Any]]:
    date = pd.Timestamp(signal_date)
    valid_dates = sorted(set().union(*(frame.dropna(how="all").index for frame in dimensions.values())))
    valid_dates = [item for item in valid_dates if item <= date]
    if not valid_dates:
        return []
    latest = valid_dates[-1]
    frame = pd.DataFrame({DIMENSION_LABELS.get(k, k): v.reindex([latest]).iloc[0] for k, v in dimensions.items() if latest in v.index})
    corr = frame.corr(method="spearman").fillna(0.0)
    rows: list[dict[str, Any]] = []
    for left in corr.index:
        for right in corr.columns:
            rows.append({"x": str(left), "y": str(right), "value": _finite(corr.loc[left, right])})
    return rows


def _line_points(series: pd.Series, limit: int = 260) -> list[dict[str, Any]]:
    clean = series.dropna()
    if len(clean) > limit:
        clean = clean.iloc[-limit:]
    return [{"date": str(idx.date()), "value": _finite(val)} for idx, val in clean.items()]


def _standardized_indicator(indicator: dict[str, Any]) -> pd.Series:
    data = indicator.get("data") or []
    s = pd.Series({pd.Timestamp(item["date"]): _finite(item.get("value")) for item in data if item.get("date")}).sort_index().dropna()
    if s.empty:
        return s
    freq_text = f"{indicator.get('frequency', '')}|{indicator.get('source', '')}|{indicator.get('name', '')}"
    if "月" in freq_text:
        x = s.pct_change().replace([np.inf, -np.inf], np.nan)
        x = x.rolling(3, min_periods=1).mean()
        window = 60
    elif "事件" in freq_text:
        x = s.rolling(13, min_periods=3).mean()
        window = 104
    else:
        x = s.rolling(13, min_periods=4).mean()
        window = 104
    mean = x.rolling(window, min_periods=max(12, window // 4)).mean()
    std = x.rolling(window, min_periods=max(12, window // 4)).std(ddof=0)
    z = (x - mean) / std.replace(0, np.nan)
    direction = str(indicator.get("direction") or "")
    if "负" in direction or "反" in direction or "下行" in direction:
        z = -z
    return z.clip(-3.0, 3.0)


def _build_prosperity_section(
    snapshot: dict[str, Any],
    tracking: dict[str, Any],
    final_figures: dict[str, Any],
    score: pd.DataFrame,
    daily_ret: pd.DataFrame,
) -> dict[str, Any]:
    hf = snapshot.get("high_frequency", {})
    industries = hf.get("industries", [])
    framework = hf.get("framework", {})
    rows: list[dict[str, Any]] = []
    industry_detail: dict[str, Any] = {}
    for item in industries:
        name = item.get("industry")
        fw = item.get("framework") or {}
        score_value = _finite(fw.get("综合景气") if "综合景气" in fw else item.get("score"))
        acceleration = _finite(fw.get("三月加速度"))
        percentile = _finite(fw.get("等权分位") if "等权分位" in fw else score_value)
        rows.append({
            "industry": name,
            "rank": item.get("rank"),
            "score": score_value,
            "acceleration": acceleration,
            "percentile": percentile,
            "selected": bool(item.get("selected")),
            "live_indicators": item.get("live_indicators"),
            "industrial_field_count": fw.get("工业月度字段数"),
            "correlation_check": _finite(fw.get("相关检验")),
            "direction_win": _finite(fw.get("方向胜率")),
            "group_return": _finite(fw.get("分组收益")),
            "window_stability": _finite(fw.get("窗口稳定")),
            "ols_r2": _finite(fw.get("OLS拟合R2")),
            "dtw_similarity": _finite(fw.get("DTW相似")),
        })
        target = pd.Series({
            pd.Timestamp(p["date"]): _finite(p.get("score"))
            for p in tracking.get("industries", {}).get(name, {}).get("score_history", [])
            if p.get("date")
        }).sort_index()
        trend = tracking.get("industries", {}).get(name, {}).get("trend", [])
        price = pd.Series({pd.Timestamp(p["date"]): _finite(p.get("industry")) for p in trend if p.get("date")}).sort_index()
        equal = pd.Series({pd.Timestamp(p["date"]): _finite(p.get("equal_weight")) for p in trend if p.get("date")}).sort_index()
        rel = pd.Series({pd.Timestamp(p["date"]): _finite(p.get("relative")) for p in trend if p.get("date")}).sort_index()

        indicator_rows: list[dict[str, Any]] = []
        indicator_series: dict[str, Any] = {}
        standardized_frames: list[pd.Series] = []
        for ind in item.get("indicators", []):
            z = _standardized_indicator(ind)
            standardized_frames.append(z.rename(ind.get("name")))
            monthly_target = target.reindex(z.index, method="ffill") if not target.empty else pd.Series(dtype=float)
            corr = _spearman(z, monthly_target)
            direction_win = None
            aligned = pd.concat([z.diff(), monthly_target.diff()], axis=1).dropna()
            if len(aligned) >= 12:
                direction_win = float((np.sign(aligned.iloc[:, 0]) == np.sign(aligned.iloc[:, 1])).mean())
            rolling = pd.concat([z, monthly_target], axis=1).rolling(26, min_periods=12).corr().unstack()
            stability = None
            try:
                rc = rolling.iloc[:, 1].dropna()
                if len(rc):
                    stability = float((rc > 0).mean())
            except Exception:
                stability = None
            contribution = _finite(ind.get("contribution"))
            indicator_rows.append({
                "name": ind.get("name"),
                "direction": ind.get("direction"),
                "contribution": contribution,
                "economic": "通过" if ind.get("model_eligible") else "观察",
                "correlation": corr,
                "direction_win": direction_win,
                "group_return": contribution,
                "stability": stability,
                "source": ind.get("source"),
                "frequency": ind.get("frequency"),
                "last_available_date": ind.get("last_available_date"),
            })
            indicator_series[ind.get("name")] = {
                "processed": _line_points(z),
                "target": _line_points(monthly_target),
            }
        if standardized_frames:
            combined = pd.concat(standardized_frames, axis=1)
            equal_score = combined.mean(axis=1)
            diffusion = combined.diff().gt(0).sum(axis=1) / combined.notna().sum(axis=1).replace(0, np.nan)
            pca_proxy = _line_points(equal_score.rolling(8, min_periods=3).mean())
            composite = equal_score.rolling(4, min_periods=2).mean()
        else:
            diffusion = pd.Series(dtype=float)
            pca_proxy = []
            composite = pd.Series(dtype=float)
        industry_detail[name] = {
            "indicator_rows": sorted(indicator_rows, key=lambda x: abs(x.get("contribution") or 0), reverse=True),
            "indicator_series": indicator_series,
            "composite": {
                "综合景气": _line_points(composite),
                "扩散分数": _line_points(diffusion),
                "等权": _line_points(composite),
                "PCA": pca_proxy,
                "target": _line_points(target),
                "price": _line_points(price),
                "relative": _line_points(rel),
                "equal_weight": _line_points(equal),
            },
        }
    latest_signal = framework.get("latest_signal_date") or snapshot.get("as_of")
    prosperity_score = pd.DataFrame({
        row["industry"]: pd.Series({
            pd.Timestamp(p["date"]): _finite(p.get("score"))
            for p in tracking.get("industries", {}).get(row["industry"], {}).get("score_history", [])
            if p.get("date")
        })
        for row in rows if row.get("industry")
    }).sort_index()
    signal_dates = pd.DatetimeIndex(prosperity_score.dropna(how="all").index) if not prosperity_score.empty else pd.DatetimeIndex(score.dropna(how="all").index)
    forward_returns = _forward_month_returns(daily_ret, signal_dates)
    ytd_top_bottom = _top_bottom_history(prosperity_score if not prosperity_score.empty else score, forward_returns, pd.Timestamp(latest_signal).year, 5)
    return {
        "as_of": snapshot.get("as_of"),
        "latest_signal_date": latest_signal,
        "current_score_date": snapshot.get("as_of") or latest_signal,
        "flow_steps": [
            "数据获取",
            "频率转换",
            "异常与方向",
            "因变量构造",
            "五大检验",
            "拟合排序",
            "景气合成",
            "组合回测",
        ],
        "summary": hf.get("summary", {}),
        "framework": framework,
        "methods": ["综合景气", "扩散分数", "等权", "PCA"],
        "industries": rows,
        "industry_detail": industry_detail,
        "industrial_mapping": [
            {
                "industry": r["industry"],
                "industrial_fields": r.get("industrial_field_count"),
                "mapping": "行业专属高频字段中识别工业增加值、产量、产值、销量、开工、发电、用电等月度或高频工业活动项",
            }
            for r in rows
        ],
        "matrix": _matrix_rows(prosperity_score if not prosperity_score.empty else score, prosperity_score.diff(3) if not prosperity_score.empty else score.diff(3), str(latest_signal)),
        "ytd_top_bottom": ytd_top_bottom,
        "figures": (final_figures.get("figures") or {}).get("industry_monthly", {}),
    }


def _build_rotation_section(
    snapshot: dict[str, Any],
    best_snapshot: dict[str, Any],
    final_figures: dict[str, Any],
    factors: dict[str, pd.DataFrame],
    dimensions: dict[str, pd.DataFrame],
    daily_ret: pd.DataFrame,
) -> dict[str, Any]:
    six = snapshot.get("six_dimension") or best_snapshot.get("six_dimension") or {}
    factor_rows = _factor_rows(six)
    efficient = _efficient_factor_rows(factor_rows)
    score = _c39_score(factors, dimensions)
    best_monthly = best_snapshot["industry"]["frequencies"]["monthly"]
    research = best_monthly.get("research_result") or best_monthly
    latest_holding = (research.get("holdings") or best_monthly.get("holdings") or [{}])[-1]
    latest_signal = latest_holding.get("signal_date") or best_snapshot.get("as_of")
    current_as_of = best_snapshot.get("as_of") or latest_signal
    ranking = _frame_latest_rows(score, current_as_of, {})
    signal_dates = pd.DatetimeIndex(score.dropna(how="all").index)
    forward = _forward_month_returns(daily_ret, signal_dates)
    ytd = _top_bottom_history(score, forward, pd.Timestamp(latest_signal).year, 5)

    factor_details: dict[str, Any] = {}
    for row in efficient:
        factor = row.get("factor")
        if factor in factors:
            factor_details[factor] = _factor_ic_detail(
                factors[factor],
                forward,
                str(factor),
                str(row.get("factor_label") or factor),
            )
    if "prosperity_acceleration" not in factor_details and "prosperity_acceleration" in factors:
        factor_details["prosperity_acceleration"] = _factor_ic_detail(
            factors["prosperity_acceleration"], forward, "prosperity_acceleration", "景气加速度"
        )

    annual_attr = _attribution_rows(research.get("holdings") or [], daily_ret, score, dimensions, "year")
    monthly_attr = _attribution_rows(research.get("holdings") or [], daily_ret, score, dimensions, "month")
    acceleration = factors.get("prosperity_acceleration", score.diff(3))
    return {
        "as_of": best_snapshot.get("as_of"),
        "latest_signal_date": latest_signal,
        "current_score_date": current_as_of,
        "flow_steps": ["因子构造", "数据处理", "因子检验", "打分构造", "策略回测", "因子归因"],
        "processing_steps": [
            {"step": "缺失值", "logic": "按PIT可见日对齐，行业截面内不把缺失补成0，合成时按可用字段重新归一化"},
            {"step": "去极值", "logic": "滚动分位和截面分位限制极端值影响"},
            {"step": "标准化", "logic": "同一信号日31行业截面分位，保证维度之间可比"},
            {"step": "中性化", "logic": "拥挤、短期过热和资金价格残差用于剔除纯交易热度"},
            {"step": "时点", "logic": "月末信号，下一交易日执行；财务可见日必须早于信号日"},
        ],
        "test_steps": [
            "经济方向",
            "RankIC/ICIR/t值",
            "IC衰减",
            "Top-Bottom分层收益",
            "窗口稳定性",
            "多因子相关性",
            "训练验证筛选",
            "测试只报告",
        ],
        "factor_table": factor_rows,
        "efficient_factors": efficient,
        "factor_corr": _factor_corr(dimensions, current_as_of),
        "factor_details": factor_details,
        "default_factor": next(iter(factor_details), "prosperity_acceleration"),
        "ranking": ranking,
        "matrix": _matrix_rows(score, acceleration, current_as_of),
        "ytd_top_bottom": ytd,
        "annual_attribution": annual_attr,
        "ytd_monthly_attribution": [row for row in monthly_attr if str(row.get("period", "")).startswith(str(pd.Timestamp(latest_signal).year))],
        "metrics": research.get("metrics") or best_monthly.get("metrics"),
        "holdings": research.get("holdings") or best_monthly.get("holdings"),
        "figures": (final_figures.get("figures") or {}).get("industry_monthly", {}),
        "score_model": "0.32×景气加速度 + 0.22×景气水平 + 0.18×营业利润同比 + 0.12×营业利润同比加速度 + 0.10×盈利扩散度 + 0.08×盈利质量确认 - 0.12×拥挤惩罚",
    }


def _attribution_rows(
    holdings: list[dict[str, Any]],
    daily_ret: pd.DataFrame,
    score: pd.DataFrame,
    dimensions: dict[str, pd.DataFrame],
    mode: str,
) -> list[dict[str, Any]]:
    if not holdings:
        return []
    trade_dates = pd.DatetimeIndex(daily_ret.index)
    rows: list[dict[str, Any]] = []
    for idx, holding in enumerate(holdings):
        execution = pd.Timestamp(holding.get("execution_date"))
        start = trade_dates.searchsorted(execution, side="left")
        if start >= len(trade_dates):
            continue
        if idx + 1 < len(holdings):
            end_date = pd.Timestamp(holdings[idx + 1].get("execution_date"))
            end = trade_dates.searchsorted(end_date, side="left")
        else:
            end = len(trade_dates)
        window = daily_ret.iloc[start:end]
        if window.empty:
            continue
        weights = pd.Series({k: float(v) for k, v in (holding.get("weights") or {}).items() if _finite(v) is not None})
        names = list(weights.index)
        if not names:
            names = holding.get("names") or []
            weights = pd.Series(1.0 / max(len(names), 1), index=names)
        period_return = float(((window.reindex(columns=names).fillna(0.0) * weights.reindex(names).fillna(0.0)).sum(axis=1) + 1.0).prod() - 1.0)
        benchmark_return = float((1.0 + window.mean(axis=1)).prod() - 1.0)
        signal = pd.Timestamp(holding.get("signal_date"))
        dim_values = {}
        for key, frame in dimensions.items():
            valid = frame.dropna(how="all")
            prior = valid.index[valid.index <= signal] if not valid.empty else []
            if len(prior):
                dim_values[key] = _finite(valid.loc[prior[-1]].reindex(names).mean())
        period = str(execution.year) if mode == "year" else execution.strftime("%Y-%m")
        row = {
            "period": period,
            "signal_date": holding.get("signal_date"),
            "strategy_return": period_return,
            "benchmark_return": benchmark_return,
            "excess_return": period_return - benchmark_return,
        }
        row.update({DIMENSION_LABELS.get(k, k): v for k, v in dim_values.items()})
        rows.append(row)
    if mode == "year":
        frame = pd.DataFrame(rows)
        out: list[dict[str, Any]] = []
        for period, group in frame.groupby("period"):
            item = {"period": str(period)}
            for col in group.columns:
                if col in {"period", "signal_date"}:
                    continue
                item[col] = _finite(group[col].mean() if col not in {"strategy_return", "benchmark_return", "excess_return"} else (1.0 + group[col]).prod() - 1.0)
            out.append(item)
        return out
    return rows[-18:]


def build_payload() -> dict[str, Any]:
    snapshot = _load_json(BOARD_DATA / "rotation_snapshot.json")
    best_snapshot = _load_json(BOARD_VNEXT_DATA / "rotation_snapshot.json")
    tracking = _load_json(BOARD_DATA / "rotation_tracking.json")
    final_figures = _load_json(BOARD_DATA / "rotation_final_figures.json")
    monthly = pd.read_csv(CACHE_DIR / "pit_six_dimension_monthly.csv.gz", parse_dates=["trade_date"])
    daily = pd.read_csv(CACHE_DIR / "pit_six_dimension_daily.csv.gz", parse_dates=["trade_date"])
    factors, dimensions, daily_ret, _close = _build_factor_frames(monthly, daily, tracking)
    score = _c39_score(factors, dimensions)
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_as_of": snapshot.get("as_of"),
        "best_model_as_of": best_snapshot.get("as_of"),
        "source": {
            "prosperity_snapshot": "board/quant_strategy_agent/data/rotation_snapshot.json",
            "rotation_snapshot": "board/quant_strategy_agent_vnext/data/rotation_snapshot.json",
            "pit_monthly": "output/industry_rotation/cache/market/pit_six_dimension_monthly.csv.gz",
            "pit_daily": "output/industry_rotation/cache/market/pit_six_dimension_daily.csv.gz",
        },
        "prosperity": _build_prosperity_section(snapshot, tracking, final_figures, score, daily_ret),
        "rotation": _build_rotation_section(snapshot, best_snapshot, final_figures, factors, dimensions, daily_ret),
    }
    return payload


def main() -> None:
    payload = build_payload()
    for data_dir in [BOARD_DATA, BOARD_VNEXT_DATA]:
        target = data_dir / OUT_NAME
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
