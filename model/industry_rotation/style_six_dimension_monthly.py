"""Monthly five-factor style rotation research build.

This module ports the industry factor-research contract to stock style labels,
while deliberately excluding the industry-specific prosperity dimension:

* the 12-cell style box: size x style;
* the 3 size buckets: large, mid and small;
* the 4 style buckets: growth, blend, value and dividend.

The signal is formed at month-end close and executed at the next trading-day
close. Daily NAV is calculated from stock-level close-to-close returns after
execution. The test set is report-only.
"""

from __future__ import annotations

import json
import math
import os
import pickle
import sqlite3
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import six_dimension_model as six


ROOT = Path(__file__).resolve().parent


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "database").exists() and (candidate / "board").exists():
            return candidate
    return start.parents[1]


PROJECT_ROOT = _find_project_root(ROOT)
DATABASE = PROJECT_ROOT / "database" / "research_warehouse.db"
OUTPUT_DIR = PROJECT_ROOT / "output" / "industry_rotation" / "style_six_dimension_monthly"
FIGURE_DIR = OUTPUT_DIR / "figures"
SOURCE_CACHE_DIR = OUTPUT_DIR / "cache"
SOURCE_CACHE = SOURCE_CACHE_DIR / "source_data_v3_all_month_end.pkl"
SOURCE_CACHE_META = SOURCE_CACHE_DIR / "source_data_v3_all_month_end_meta.json"
STANDARD_BENCHMARK_CACHE = SOURCE_CACHE_DIR / "standard_style_benchmark_cni.pkl"
STANDARD_BENCHMARK_META = SOURCE_CACHE_DIR / "standard_style_benchmark_cni_meta.json"
DATA_OUTPUT = (
    PROJECT_ROOT
    / "board"
    / "quant_strategy_agent_vnext"
    / "data"
    / "style_six_dimension_monthly.json"
)
DATA_OUTPUTS = (
    DATA_OUTPUT,
    PROJECT_ROOT / "board" / "quant_strategy_agent" / "data" / "style_six_dimension_monthly.json",
)

MODEL_VERSION = "style-five-factor-monthly/1.29-full-style-research-contract"
START_SIGNAL = "20120131"
SIGNAL_CUTOFF_RAW = os.environ.get("STYLE_ROTATION_SIGNAL_CUTOFF", "").strip()
SIGNAL_CUTOFF = pd.Timestamp(SIGNAL_CUTOFF_RAW) if SIGNAL_CUTOFF_RAW else None
CHART_START = "2016-01-01"
COST_RATE = 0.001
MAX_STOCK_WEIGHT = 0.08

SIZE_LABELS = ("大盘", "中盘", "小盘")
STYLE_LABELS = ("成长", "均衡", "价值", "红利")
CELL_LABELS = tuple(f"{size}{style}" for size in SIZE_LABELS for style in STYLE_LABELS)

STANDARD_SIZE_INDEX = {"大盘": "399314", "中盘": "399315", "小盘": "399316"}
STANDARD_STYLE_INDEX = {"成长": "399370", "价值": "399371", "红利": "399321"}
STANDARD_INDEX_NAME = {
    "399314": "巨潮大盘",
    "399315": "巨潮中盘",
    "399316": "巨潮小盘",
    "399370": "国证成长",
    "399371": "国证价值",
    "399321": "国证红利",
}
STANDARD_BENCHMARK_LABEL = {
    "style12": "标准12风格箱等权基准",
    "size3": "巨潮大中小盘等权基准",
    "style4": "国证成长/价值/红利+均衡代理等权基准",
}

GROUP_SPECS = {
    "style12": {
        "name": "12类风格箱",
        "label_column": "cell",
        "top_n": 3,
        "groups": CELL_LABELS,
        "include_return_technical": True,
    },
    "size3": {
        "name": "大中小市值",
        "label_column": "size",
        "top_n": 1,
        "groups": SIZE_LABELS,
        "prefer_online_stability": True,
        "include_return_technical": True,
    },
    "style4": {
        "name": "四类风格",
        "label_column": "style",
        "top_n": 1,
        "groups": STYLE_LABELS,
        "include_return_technical": True,
    },
}

_GROUP_WEIGHT_CACHE: dict[tuple[int, str, pd.Timestamp], dict[str, pd.Series]] = {}
_GROUP_PERIOD_RETURN_CACHE: dict[tuple[int, int, str, pd.Timestamp, pd.Timestamp, pd.Timestamp], pd.DataFrame] = {}
_LABEL_DATE_CACHE: dict[tuple[int, pd.Timestamp], pd.DataFrame] = {}
_LABEL_DATE_SET_CACHE: dict[int, set[pd.Timestamp]] = {}

SPLITS = {
    "train": ("2015-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2021-12-31"),
    "test": ("2022-01-01", "2099-12-31"),
}


CANDIDATE_EXECUTION = {
    "核心等权五因子": {"mode": "top_equal"},
    "核心RankIC五因子": {"mode": "score_tilt", "active_share": 0.48, "floor": 0.01},
    "核心低拥挤五因子": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "等权五因子": {"mode": "top_equal"},
    "稳健增强五因子": {"mode": "top_equal"},
    "训练验证网格五因子": {"mode": "score_tilt", "active_share": 0.48, "floor": 0.01},
    "因子检验五因子": {"mode": "score_tilt", "active_share": 0.48, "floor": 0.01},
    "趋势资金五因子": {"mode": "score_tilt", "active_share": 0.50, "floor": 0.01},
    "质量估值防守": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "低拥挤均衡": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "等权五因子Top2": {"mode": "top_equal", "top_n": 2},
    "趋势资金Top2": {"mode": "top_equal", "top_n": 2},
    "训练验证网格Top2": {"mode": "top_equal", "top_n": 2},
    "质量筛选低拥挤Top2": {"mode": "top_equal", "top_n": 2},
    "低拥挤均衡Top2": {"mode": "top_equal", "top_n": 2},
    "滚动RankIC五因子": {"mode": "score_tilt", "active_share": 0.50, "floor": 0.01},
    "滚动RankIC低拥挤": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "OLS五因子": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.01},
    "Lasso五因子": {"mode": "score_tilt", "active_share": 0.42, "floor": 0.015},
    "质量筛选五因子": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.01},
    "质量筛选低拥挤": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "滚动子因子等权五因子": {"mode": "top_equal"},
    "滚动子因子质量五因子": {"mode": "score_tilt", "active_share": 0.48, "floor": 0.01},
    "滚动子因子低拥挤五因子": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "一级多空质量五因子": {"mode": "score_tilt", "active_share": 0.48, "floor": 0.01},
    "一级低拥挤质量五因子": {"mode": "score_tilt", "active_share": 0.46, "floor": 0.015},
    "一级ICIR质量五因子": {"mode": "score_tilt", "active_share": 0.48, "floor": 0.01},
}
FUNDAMENTAL_FIELDS = [
    "roe",
    "roa",
    "gross_margin",
    "netprofit_margin",
    "assets_turn",
    "current_ratio",
    "debt_to_assets",
    "tr_yoy",
    "netprofit_yoy",
    "op_yoy",
    "revenue_positive_breadth",
    "profit_positive_breadth",
    "roe_improvement_6m",
    "roa_improvement_6m",
    "margin_improvement_6m",
    "profit_yoy_accel_6m",
    "revenue_yoy_accel_6m",
    "debt_improvement_6m",
    "asset_turn_improvement_6m",
    "profit_revenue_leverage",
    "quality_momentum_12m",
    "growth_quality_score",
    "profit_growth_stability",
    "roe_stability_8m",
    "margin_stability_8m",
    "asset_turn_stability_8m",
    "roe_revision_3m",
    "roa_revision_3m",
    "margin_revision_3m",
    "balance_sheet_quality",
    "earnings_revision_quality",
    "profitability_stability_score",
    "report_freshness",
    "value_profitability_interaction",
    "factor_lab_quality_value",
    "factor_lab_fundamental_revision",
    "factor_lab_genetic_quality_momentum",
    "factor_lab_openfe_quality_interaction",
    "mined_value_quality_momentum",
    "mined_small_value_profitability",
    "mined_report_quality_value_momentum",
    "mined_factor_composite_v1",
]
VALUATION_FIELDS = [
    "earnings_yield",
    "book_yield",
    "sales_yield",
    "dividend_yield",
    "earnings_yield_repair_6m",
    "book_yield_repair_6m",
    "sales_yield_repair_6m",
    "dividend_persistence",
    "low_peg_proxy",
    "quality_value_match",
    "dividend_quality",
    "earnings_yield_zscore_36m",
    "book_yield_zscore_36m",
    "sales_yield_zscore_36m",
    "dividend_yield_zscore_36m",
    "value_repair_score",
    "shareholder_yield_proxy",
    "deep_value_stability",
    "mined_dividend_lowvol_quality",
    "mined_defensive_dividend_quality",
]
TECHNICAL_FIELDS = [
    "momentum_12_1",
    "momentum_6_1",
    "momentum_3_1",
    "momentum_1",
    "risk_adjusted_momentum",
    "path_efficiency_126",
    "path_efficiency_63",
    "distance_ma120",
    "distance_ma60",
    "breadth_20",
    "breadth_60",
    "short_reversal",
    "momentum_consistency_60",
    "low_vol_63",
    "drawdown_resilience_126",
    "trend_stability_60",
    "momentum_12_6",
    "momentum_9_1",
    "momentum_2_1",
    "realized_skew_63",
    "downside_vol_63",
    "drawdown_resilience_63",
    "new_high_proximity_252",
    "volatility_compression",
    "liquidity_adjusted_momentum",
    "upside_capture_126",
    "factor_lab_kline_trend",
    "factor_lab_formulaic_alpha_mcts_4004ed0a",
    "factor_lab_formulaic_alpha_mcts_887931da",
    "factor_lab_openfe_technical_interaction",
    "factor_lab_genetic_momentum_4c06c340",
    "factor_lab_genetic_lowvol_reversal_alpha",
    "factor_lab_openfe_style_alpha_interaction",
    "mined_nonlinear_rank_alpha",
    "mined_deep_rank_alpha",
    "mined_trend_low_vol_confirm",
    "mined_momentum_reversal",
    "mined_kline_context_factor",
]
FUNDS_FIELDS = [
    "flow_total_5",
    "flow_total_20",
    "flow_total_60",
    "flow_large_structure_5",
    "flow_large_structure_20",
    "flow_large_structure_60",
    "flow_extra_structure_20",
    "flow_extra_structure_60",
    "flow_breadth_20",
    "flow_persistence_20",
    "flow_total_acceleration_20_60",
    "flow_large_acceleration_20_60",
    "flow_extra_acceleration_20_60",
    "northbound_proxy",
    "flow_price_alignment",
    "flow_residual_20",
    "flow_absorption_20",
    "smart_money_acceleration",
    "flow_total_10",
    "flow_large_structure_10",
    "flow_extra_structure_10",
    "flow_total_acceleration_5_20",
    "flow_large_acceleration_5_20",
    "flow_extra_acceleration_5_20",
    "flow_smart_share_20",
    "flow_smart_share_60",
    "flow_stability_20",
    "flow_turnover_residual_60",
    "factor_lab_flow_anti_crowding",
    "factor_lab_genetic_flow_value",
    "factor_lab_openfe_flow_interaction",
    "mined_moneyflow_momentum",
    "mined_agent_moneyflow_anti_crowding",
]
CROWDING_FIELDS = [
    "turnover_level",
    "turnover_expansion",
    "volume_ratio",
    "amount_concentration",
    "limit_up_heat",
    "short_momentum_heat",
    "price_distance_heat",
    "volatility_expansion",
    "breadth_heat",
    "low_dispersion_heat",
    "flow_price_crowding",
    "flow_turnover_crowding",
    "turnover_residual_heat",
    "volume_price_heat",
    "volatility_heat",
    "turnover_percentile_252",
    "amount_percentile_252",
    "volume_ratio_spike_5_60",
    "limit_up_persistence_60",
    "gap_to_high_252_heat",
    "downside_vol_heat",
    "return_skew_heat",
    "flow_concentration_heat",
    "liquidity_impact_heat",
    "turnover_volatility_heat",
]
CORE_FUNDAMENTAL_FIELDS = [
    "roe",
    "roa",
    "gross_margin",
    "netprofit_margin",
    "assets_turn",
    "current_ratio",
    "debt_to_assets",
    "tr_yoy",
    "netprofit_yoy",
    "op_yoy",
    "revenue_positive_breadth",
    "profit_positive_breadth",
    "roe_improvement_6m",
    "roa_improvement_6m",
    "margin_improvement_6m",
    "profit_yoy_accel_6m",
    "revenue_yoy_accel_6m",
    "debt_improvement_6m",
    "asset_turn_improvement_6m",
    "roe_stability_8m",
    "margin_stability_8m",
    "earnings_revision_quality",
    "balance_sheet_quality",
    "factor_lab_genetic_quality_momentum",
    "mined_value_quality_momentum",
]
CORE_TECHNICAL_FIELDS = [
    "momentum_12_1",
    "momentum_6_1",
    "momentum_3_1",
    "momentum_1",
    "risk_adjusted_momentum",
    "path_efficiency_126",
    "path_efficiency_63",
    "distance_ma120",
    "distance_ma60",
    "breadth_20",
    "breadth_60",
    "short_reversal",
    "momentum_12_6",
    "momentum_9_1",
    "downside_vol_63",
    "new_high_proximity_252",
    "factor_lab_formulaic_alpha_mcts_4004ed0a",
    "factor_lab_formulaic_alpha_mcts_887931da",
    "factor_lab_openfe_technical_interaction",
    "factor_lab_genetic_momentum_4c06c340",
    "factor_lab_genetic_lowvol_reversal_alpha",
    "factor_lab_openfe_style_alpha_interaction",
    "mined_nonlinear_rank_alpha",
    "mined_deep_rank_alpha",
]
CORE_VALUATION_FIELDS = [
    "earnings_yield",
    "book_yield",
    "sales_yield",
    "dividend_yield",
    "earnings_yield_repair_6m",
    "book_yield_repair_6m",
    "sales_yield_repair_6m",
    "dividend_persistence",
    "earnings_yield_zscore_36m",
    "book_yield_zscore_36m",
    "value_repair_score",
    "shareholder_yield_proxy",
]
CORE_FUNDS_FIELDS = [
    "flow_total_5",
    "flow_total_20",
    "flow_total_60",
    "flow_large_structure_5",
    "flow_large_structure_20",
    "flow_large_structure_60",
    "flow_extra_structure_20",
    "flow_extra_structure_60",
    "flow_breadth_20",
    "flow_persistence_20",
    "flow_total_acceleration_20_60",
    "flow_large_acceleration_20_60",
    "flow_extra_acceleration_20_60",
    "flow_total_10",
    "flow_total_acceleration_5_20",
    "flow_smart_share_20",
    "flow_turnover_residual_60",
    "factor_lab_genetic_flow_value",
]
CORE_CROWDING_FIELDS = [
    "turnover_level",
    "turnover_expansion",
    "volume_ratio",
    "amount_concentration",
    "limit_up_heat",
    "short_momentum_heat",
    "price_distance_heat",
    "volatility_expansion",
    "breadth_heat",
    "low_dispersion_heat",
    "flow_price_crowding",
    "flow_turnover_crowding",
    "turnover_percentile_252",
    "amount_percentile_252",
    "volume_ratio_spike_5_60",
    "gap_to_high_252_heat",
    "liquidity_impact_heat",
]
CORE_STYLE_RETURN_TECHNICAL_FIELDS = [
    "domain_momentum_12_1",
    "domain_momentum_6",
    "domain_momentum_3",
    "domain_short_reversal",
    "domain_low_vol_12",
    "domain_drawdown_resilience",
    "domain_trend_efficiency",
]
CORE_FIELDS_BY_DIMENSION = {
    "fundamental": CORE_FUNDAMENTAL_FIELDS,
    "technical": CORE_TECHNICAL_FIELDS,
    "valuation": CORE_VALUATION_FIELDS,
    "funds": CORE_FUNDS_FIELDS,
    "crowding": CORE_CROWDING_FIELDS,
}
MAX_ADMITTED_FACTORS_BY_DIMENSION = {
    "fundamental": 4,
    "technical": 5,
    "valuation": 3,
    "funds": 4,
    "crowding": 3,
}
STYLE_DIMENSIONS = ("fundamental", "technical", "valuation", "funds", "crowding")
RETURN_DIMENSIONS = ("fundamental", "technical", "valuation", "funds")
ENABLE_ROLLING_ATOMIC_CANDIDATES = False
PROSPERITY_FIELDS: list[str] = []
STYLE_RETURN_TECHNICAL_FIELDS = [
    "domain_momentum_12_1",
    "domain_momentum_6",
    "domain_momentum_3",
    "domain_short_reversal",
    "domain_low_vol_12",
    "domain_drawdown_resilience",
    "domain_trend_efficiency",
    "domain_momentum_acceleration_3_6",
    "domain_positive_rate_6",
    "domain_vol_adjusted_6",
    "domain_relative_reversal_2",
    "domain_drawdown_repair_3",
]
STYLE_REGIME_TECHNICAL_FIELDS = [
    "market_regime_style_fit",
    "breadth_regime_style_fit",
    "volatility_defense_style_fit",
]
FACTOR_LAB_SIGNAL_MAP = {
    "ai_llm_hypothesis_cross_domain_quality_value_31ce36dd": "factor_lab_quality_value",
    "ai_llm_hypothesis_event_fundamental_revision_50846259": "factor_lab_fundamental_revision",
    "ai_llm_hypothesis_flow_anti_crowding_reversal_3315b2d1": "factor_lab_flow_anti_crowding",
    "ai_llm_hypothesis_kline_context_trend_36e2437d": "factor_lab_kline_trend",
    "ai_genetic_crossover_crossover_mutation_4c06c340": "factor_lab_genetic_momentum_4c06c340",
    "ai_genetic_crossover_crossover_mutation_3e235cae": "factor_lab_genetic_flow_value",
    "ai_genetic_crossover_crossover_mutation_7f9a5849": "factor_lab_genetic_lowvol_reversal_alpha",
    "ai_genetic_crossover_crossover_mutation_4218644c": "factor_lab_genetic_quality_momentum",
    "ai_mcts_tree_search_formulaic_alpha_tree_4004ed0a": "factor_lab_formulaic_alpha_mcts_4004ed0a",
    "ai_mcts_tree_search_formulaic_alpha_tree_887931da": "factor_lab_formulaic_alpha_mcts_887931da",
    "ai_openfe_feature_search_auto_feature_interaction_3b442757": "factor_lab_openfe_technical_interaction",
    "ai_openfe_feature_search_auto_feature_interaction_8361f9a5": "factor_lab_openfe_flow_interaction",
    "ai_openfe_feature_search_auto_feature_interaction_3d3643c9": "factor_lab_openfe_style_alpha_interaction",
    "ai_openfe_feature_search_auto_feature_interaction_fe49a8bb": "factor_lab_openfe_quality_interaction",
    "value_quality_momentum": "mined_value_quality_momentum",
    "small_value_profitability": "mined_small_value_profitability",
    "report_quality_value_momentum_v4": "mined_report_quality_value_momentum",
    "ai_factor_composite_v1": "mined_factor_composite_v1",
    "dividend_lowvol_quality": "mined_dividend_lowvol_quality",
    "defensive_dividend_quality_v4": "mined_defensive_dividend_quality",
    "trend_low_vol_confirm": "mined_trend_low_vol_confirm",
    "momentum_60_minus_reversal_5": "mined_momentum_reversal",
    "kline_context_factor_v4": "mined_kline_context_factor",
    "moneyflow_momentum_20": "mined_moneyflow_momentum",
    "agent_moneyflow_anti_crowding_v4": "mined_agent_moneyflow_anti_crowding",
    "nonlinear_rank_blend_v1": "mined_nonlinear_rank_alpha",
    "deep_rank_interaction_v4": "mined_deep_rank_alpha",
}
FACTOR_LAB_MAX_STALE_DAYS = 95

DIMENSION_LABELS = {
    "fundamental": "基本面",
    "technical": "技术面",
    "valuation": "估值",
    "funds": "资金面",
    "crowding": "拥挤度",
}

FIELD_DIMENSION = {
    **{name: "fundamental" for name in FUNDAMENTAL_FIELDS},
    **{name: "technical" for name in TECHNICAL_FIELDS},
    **{name: "valuation" for name in VALUATION_FIELDS},
    **{name: "funds" for name in FUNDS_FIELDS},
    **{name: "crowding" for name in CROWDING_FIELDS},
}

HIGH_IS_GOOD = {
    **{name: True for name in FUNDAMENTAL_FIELDS + VALUATION_FIELDS + TECHNICAL_FIELDS + FUNDS_FIELDS},
    "debt_to_assets": False,
    **{name: True for name in CROWDING_FIELDS},
}


@dataclass
class SourceData:
    trade_dates: pd.DatetimeIndex
    signal_dates: pd.DatetimeIndex
    execution_dates: dict[pd.Timestamp, pd.Timestamp]
    labels: pd.DataFrame
    daily_returns: pd.DataFrame


def _open_read_only() -> sqlite3.Connection:
    uri = f"file:{DATABASE.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-500000")
    return connection


def _compact(dates: Iterable[pd.Timestamp]) -> list[str]:
    return [pd.Timestamp(date).strftime("%Y%m%d") for date in dates]


def _iso(value: str | pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _placeholders(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _percentile(series: pd.Series, neutral: float = 0.5) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(method="average", pct=True).fillna(neutral).clip(0.0, 1.0)


def _read_pivot(
    connection: sqlite3.Connection,
    query: str,
    value_column: str,
    params: Iterable[Any] = (),
) -> pd.DataFrame:
    frame = pd.read_sql_query(query, connection, params=tuple(params))
    if frame.empty:
        return pd.DataFrame()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce").astype("float32")
    pivot = frame.pivot(index="trade_date", columns="ts_code", values=value_column).sort_index()
    return pivot


def _month_end_dates(connection: sqlite3.Connection) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    dates = pd.read_sql_query(
        "SELECT DISTINCT trade_date FROM stock_ohlcv_daily ORDER BY trade_date",
        connection,
    )["trade_date"].astype(str)
    trade_dates = pd.DatetimeIndex(pd.to_datetime(dates, format="%Y%m%d"))
    signal_trade_dates = trade_dates
    if SIGNAL_CUTOFF is not None:
        signal_trade_dates = pd.DatetimeIndex(trade_dates[trade_dates <= SIGNAL_CUTOFF])
    month_end_dates = signal_trade_dates.to_series(index=signal_trade_dates).groupby(signal_trade_dates.to_period("M")).max()
    signals = pd.DatetimeIndex(
        [
            pd.Timestamp(date)
            for date in month_end_dates
            if pd.Timestamp(date).strftime("%Y%m%d") >= START_SIGNAL
        ]
    ).drop_duplicates().sort_values()
    return trade_dates, signals


def _execution_dates(trade_dates: pd.DatetimeIndex, signals: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp]:
    output: dict[pd.Timestamp, pd.Timestamp] = {}
    for signal in signals:
        position = trade_dates.searchsorted(signal, side="right")
        if position < len(trade_dates):
            output[pd.Timestamp(signal)] = pd.Timestamp(trade_dates[position])
    return output


def _attach_financials(connection: sqlite3.Connection, monthly: pd.DataFrame) -> pd.DataFrame:
    max_visible_date = monthly["trade_date"].max().strftime("%Y%m%d")
    min_visible_date = (monthly["trade_date"].min() - pd.DateOffset(years=6)).strftime("%Y%m%d")
    financial = pd.read_sql_query(
        """
        SELECT ts_code, visible_date, end_date, total_revenue, gross_margin, netprofit_margin,
               roe, roa, debt_to_assets, current_ratio, assets_turn,
               op_yoy, tr_yoy, netprofit_yoy
        FROM financial_report_visible
        WHERE visible_date <= ?
          AND visible_date >= ?
        ORDER BY visible_date, ts_code, end_date
        """,
        connection,
        params=(max_visible_date, min_visible_date),
    )
    financial["visible_date"] = pd.to_datetime(financial["visible_date"], format="%Y%m%d", errors="coerce")
    financial["financial_end_date"] = pd.to_datetime(financial.pop("end_date"), format="%Y%m%d", errors="coerce")
    financial = (
        financial.dropna(subset=["visible_date"])
        .sort_values(["ts_code", "visible_date", "financial_end_date"])
        .drop_duplicates(["ts_code", "visible_date"], keep="last")
    )
    financial_columns = [
        "total_revenue",
        "gross_margin",
        "netprofit_margin",
        "roe",
        "roa",
        "debt_to_assets",
        "current_ratio",
        "assets_turn",
        "op_yoy",
        "tr_yoy",
        "netprofit_yoy",
    ]
    left = monthly.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    right = financial.sort_values(["visible_date", "ts_code"]).reset_index(drop=True)
    try:
        merged = pd.merge_asof(
            left,
            right,
            left_on="trade_date",
            right_on="visible_date",
            by="ts_code",
            direction="backward",
            allow_exact_matches=False,
        )
    except ValueError:
        pieces: list[pd.DataFrame] = []
        by_financial = {code: group for code, group in financial.groupby("ts_code", sort=False)}
        empty_columns = ["visible_date", "financial_end_date", *financial_columns]
        for code, local in monthly.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False):
            local = local.sort_values("trade_date").copy()
            report = by_financial.get(code)
            if report is None:
                for column in empty_columns:
                    local[column] = np.nan
                pieces.append(local)
                continue
            pieces.append(
                pd.merge_asof(
                    local,
                    report.drop(columns="ts_code").sort_values("visible_date"),
                    left_on="trade_date",
                    right_on="visible_date",
                    direction="backward",
                    allow_exact_matches=False,
                )
            )
        merged = pd.concat(pieces, ignore_index=True)
    invalid_future = merged["visible_date"].notna() & merged["visible_date"].ge(merged["trade_date"])
    if bool(invalid_future.any()):
        raise ValueError("style_financial_future_leak")
    merged["report_age_days"] = (merged["trade_date"] - merged["visible_date"]).dt.days
    stale = merged["report_age_days"].gt(550)
    merged.loc[stale, financial_columns] = np.nan
    return merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def _attach_factor_lab_signals(connection: sqlite3.Connection, monthly: pd.DataFrame) -> pd.DataFrame:
    """把因子实验室中已落库、可按股票聚合的风格增强因子合入月频面板。

    这些字段不是行业景气度，而是股票粒度的质量价值、基本面修正、资金反拥挤和
    K线趋势假设因子。合并使用同股票向后 asof，并设置最长陈旧期，避免把未来值
    或过期信号带到当前月末。
    """
    aliases = list(FACTOR_LAB_SIGNAL_MAP.values())
    if monthly.empty:
        for alias in aliases:
            monthly[alias] = np.nan
        return monthly
    start = (pd.Timestamp(monthly["trade_date"].min()) - pd.DateOffset(months=6)).strftime("%Y%m%d")
    end = pd.Timestamp(monthly["trade_date"].max()).strftime("%Y%m%d")
    names = list(FACTOR_LAB_SIGNAL_MAP.keys())
    try:
        factor = pd.read_sql_query(
            f"""
            SELECT trade_date, ts_code, factor_name, factor_value
            FROM factor_value_daily
            WHERE factor_name IN ({_placeholders(names)})
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY ts_code, trade_date
            """,
            connection,
            params=(*names, start, end),
        )
    except Exception:
        factor = pd.DataFrame()
    if factor.empty:
        output = monthly.copy()
        for alias in aliases:
            output[alias] = np.nan
        output["factor_lab_signal_date"] = pd.NaT
        return output
    factor["trade_date"] = pd.to_datetime(factor["trade_date"], format="%Y%m%d", errors="coerce")
    factor["factor_value"] = pd.to_numeric(factor["factor_value"], errors="coerce")
    factor = factor.dropna(subset=["trade_date", "ts_code", "factor_name"])
    wide = (
        factor.pivot_table(
            index=["trade_date", "ts_code"],
            columns="factor_name",
            values="factor_value",
            aggfunc="last",
        )
        .rename(columns=FACTOR_LAB_SIGNAL_MAP)
        .reset_index()
    )
    wide["factor_lab_signal_date"] = wide["trade_date"]
    wide = wide.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    left = monthly.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    try:
        merged = pd.merge_asof(
            left,
            wide,
            on="trade_date",
            by="ts_code",
            direction="backward",
            allow_exact_matches=True,
            suffixes=("", "_factor_lab"),
        )
    except Exception:
        pieces: list[pd.DataFrame] = []
        by_factor = {code: group.sort_values("trade_date") for code, group in wide.groupby("ts_code", sort=False)}
        for code, local in monthly.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False):
            local = local.sort_values("trade_date").copy()
            factor_local = by_factor.get(code)
            if factor_local is None or factor_local.empty:
                for alias in aliases:
                    local[alias] = np.nan
                local["factor_lab_signal_date"] = pd.NaT
            else:
                local = pd.merge_asof(
                    local,
                    factor_local.drop(columns="ts_code").sort_values("trade_date"),
                    on="trade_date",
                    direction="backward",
                    allow_exact_matches=True,
                )
            pieces.append(local)
        merged = pd.concat(pieces, ignore_index=True)
    if "factor_lab_signal_date" not in merged.columns:
        merged["factor_lab_signal_date"] = pd.NaT
    age = (merged["trade_date"] - pd.to_datetime(merged["factor_lab_signal_date"], errors="coerce")).dt.days
    stale = age.gt(FACTOR_LAB_MAX_STALE_DAYS)
    for alias in aliases:
        if alias not in merged.columns:
            merged[alias] = np.nan
        merged.loc[stale, alias] = np.nan
    return merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def _capped_weights(capitalisation: pd.Series, cap: float = MAX_STOCK_WEIGHT) -> pd.Series:
    values = pd.to_numeric(capitalisation, errors="coerce").clip(lower=0.0).fillna(0.0)
    if values.empty or values.sum() <= 0.0:
        return pd.Series(dtype=float)
    if len(values) < math.ceil(1.0 / cap):
        return pd.Series(1.0 / len(values), index=values.index)
    weights = values / values.sum()
    fixed = pd.Series(False, index=values.index)
    for _ in range(len(values) + 1):
        over = (~fixed) & weights.gt(cap + 1e-12)
        if not bool(over.any()):
            break
        weights.loc[over] = cap
        fixed.loc[over] = True
        free = ~fixed
        remaining = 1.0 - float(weights.loc[fixed].sum())
        if remaining <= 0.0 or not bool(free.any()):
            break
        base = values.loc[free]
        weights.loc[free] = remaining * (base / base.sum() if base.sum() > 0.0 else 1.0 / int(free.sum()))
    return weights / weights.sum()


def _assign_size(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = frame.sort_values(["circ_mv", "ts_code"], ascending=[False, True]).copy()
    total = float(ranked["circ_mv"].sum())
    ranked["_cap_before"] = ranked["circ_mv"].cumsum().shift(fill_value=0.0) / total
    ranked["size"] = np.select(
        [ranked["_cap_before"] < 0.70, ranked["_cap_before"] < 0.90],
        ["大盘", "中盘"],
        default="小盘",
    )
    return ranked.drop(columns="_cap_before")


def _assign_style(frame: pd.DataFrame) -> pd.DataFrame:
    labelled: list[pd.DataFrame] = []
    for size in SIZE_LABELS:
        group = frame.loc[frame["size"].eq(size)].copy()
        if group.empty:
            continue
        group["dividend_percentile"] = _percentile(group["dv_ttm"])
        group["dividend_qualified"] = (
            group["dv_ttm"].fillna(0.0).gt(0.0)
            & group["dividend_percentile"].ge(0.70)
            & group["dividend_positive_8m"].ge(6)
            & group["dividend_observed_8m"].ge(6)
        )
        group["style"] = "红利"
        residual = group.loc[~group["dividend_qualified"]].copy()
        if not residual.empty:
            residual = residual.sort_values(["style_spread", "ts_code"], ascending=[True, True])
            total = float(residual["circ_mv"].sum())
            residual["_cap_before"] = residual["circ_mv"].cumsum().shift(fill_value=0.0) / total
            residual["style"] = np.select(
                [residual["_cap_before"] < 0.30, residual["_cap_before"] < 0.70],
                ["价值", "均衡"],
                default="成长",
            )
            group.loc[residual.index, "style"] = residual["style"]
        group["cell"] = group["size"] + group["style"]
        labelled.append(group)
    return pd.concat(labelled, ignore_index=True)



def _quarter_label_anchor_map(signal_dates: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp]:
    """把每个月信号日映射到最近一个季末标签日，确保风格标签季度更新。"""
    dates = [pd.Timestamp(date) for date in pd.DatetimeIndex(signal_dates).drop_duplicates().sort_values()]
    if not dates:
        return {}
    quarter_anchors = [date for date in dates if int(date.month) in (3, 6, 9, 12)]
    mapping: dict[pd.Timestamp, pd.Timestamp] = {}
    anchor_index = 0
    current_anchor = quarter_anchors[0] if quarter_anchors else dates[0]
    for date in dates:
        while anchor_index < len(quarter_anchors) and quarter_anchors[anchor_index] <= date:
            current_anchor = quarter_anchors[anchor_index]
            anchor_index += 1
        mapping[date] = current_anchor if current_anchor <= date else dates[0]
    return mapping

def _load_monthly_labels(
    connection: sqlite3.Connection,
    signal_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    signal_text = _compact(signal_dates)
    qmarks = _placeholders(signal_text)
    valuation = pd.read_sql_query(
        f"""
        SELECT trade_date, ts_code, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv,
               turnover_rate, turnover_rate_f, volume_ratio
        FROM stock_valuation_daily
        WHERE trade_date IN ({qmarks})
        """,
        connection,
        params=signal_text,
    )
    price = pd.read_sql_query(
        f"""
        SELECT trade_date, ts_code, stock_name, qfq_close
        FROM stock_ohlcv_daily
        WHERE trade_date IN ({qmarks})
        """,
        connection,
        params=signal_text,
    )
    industry = pd.read_sql_query(
        f"""
        SELECT v.trade_date, v.ts_code, m.industry_name
        FROM stock_valuation_daily v
        JOIN sw_l1_industry_daily m
          ON m.ts_code = v.ts_code
         AND v.trade_date BETWEEN m.start_date AND COALESCE(m.end_date, '20991231')
        WHERE v.trade_date IN ({qmarks})
        """,
        connection,
        params=signal_text,
    )
    master = pd.read_sql_query(
        "SELECT ts_code, stock_name AS master_name, list_date, delist_date FROM security_master",
        connection,
    )
    valuation["trade_date"] = pd.to_datetime(valuation["trade_date"], format="%Y%m%d")
    price["trade_date"] = pd.to_datetime(price["trade_date"], format="%Y%m%d")
    industry["trade_date"] = pd.to_datetime(industry["trade_date"], format="%Y%m%d")
    monthly = (
        valuation.merge(price, on=["trade_date", "ts_code"], how="left")
        .merge(master, on="ts_code", how="left")
        .merge(industry, on=["trade_date", "ts_code"], how="left")
    )
    monthly["stock_name"] = monthly["stock_name"].fillna(monthly["master_name"])
    monthly = monthly.drop(columns=["master_name"])
    monthly = monthly.sort_values(["trade_date", "ts_code", "industry_name"]).drop_duplicates(["trade_date", "ts_code"], keep="last")
    monthly = _attach_financials(connection, monthly)
    monthly = _attach_factor_lab_signals(connection, monthly)

    dividend = monthly.pivot_table(index="trade_date", columns="ts_code", values="dv_ttm", aggfunc="last").sort_index()
    positive = dividend.fillna(0.0).gt(0.0).astype("int16").rolling(8, min_periods=1).sum()
    observed = dividend.notna().astype("int16").rolling(8, min_periods=1).sum()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        positive_long = positive.stack(dropna=False).rename("dividend_positive_8m").reset_index()
        observed_long = observed.stack(dropna=False).rename("dividend_observed_8m").reset_index()
    monthly = monthly.merge(positive_long, on=["trade_date", "ts_code"], how="left")
    monthly = monthly.merge(observed_long, on=["trade_date", "ts_code"], how="left")
    monthly["dividend_positive_8m"] = monthly["dividend_positive_8m"].fillna(0).astype("int16")
    monthly["dividend_observed_8m"] = monthly["dividend_observed_8m"].fillna(0).astype("int16")

    monthly["earnings_yield"] = np.where(monthly["pe_ttm"].gt(0.0), 1.0 / monthly["pe_ttm"], np.nan)
    monthly["book_yield"] = np.where(monthly["pb"].gt(0.0), 1.0 / monthly["pb"], np.nan)
    monthly["sales_yield"] = np.where(monthly["ps_ttm"].gt(0.0), 1.0 / monthly["ps_ttm"], np.nan)
    monthly["dividend_yield"] = monthly["dv_ttm"] / 100.0
    monthly = monthly.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    by_stock = monthly.groupby("ts_code", sort=False)
    monthly["roe_improvement_6m"] = by_stock["roe"].diff(6)
    monthly["roa_improvement_6m"] = by_stock["roa"].diff(6)
    monthly["margin_improvement_6m"] = by_stock["gross_margin"].diff(6).add(by_stock["netprofit_margin"].diff(6), fill_value=np.nan)
    monthly["profit_yoy_accel_6m"] = by_stock["netprofit_yoy"].diff(6)
    monthly["revenue_yoy_accel_6m"] = by_stock["tr_yoy"].diff(6)
    monthly["debt_improvement_6m"] = by_stock["debt_to_assets"].diff(6).mul(-1.0)
    monthly["asset_turn_improvement_6m"] = by_stock["assets_turn"].diff(6)
    monthly["profit_revenue_leverage"] = monthly["op_yoy"].sub(monthly["tr_yoy"])
    monthly["quality_momentum_12m"] = by_stock["roe"].diff(12).add(by_stock["roa"].diff(12), fill_value=np.nan)
    monthly["profit_growth_stability"] = (
        by_stock["netprofit_yoy"]
        .rolling(8, min_periods=4)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
        .mul(-1.0)
    )
    gross_margin_stability = (
        by_stock["gross_margin"]
        .rolling(8, min_periods=4)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
    )
    net_margin_stability = (
        by_stock["netprofit_margin"]
        .rolling(8, min_periods=4)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
    )
    monthly["roe_stability_8m"] = (
        by_stock["roe"]
        .rolling(8, min_periods=4)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
        .mul(-1.0)
    )
    monthly["margin_stability_8m"] = gross_margin_stability.add(net_margin_stability, fill_value=np.nan).mul(-0.5)
    monthly["asset_turn_stability_8m"] = (
        by_stock["assets_turn"]
        .rolling(8, min_periods=4)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
        .mul(-1.0)
    )
    monthly["roe_revision_3m"] = by_stock["roe"].diff(3)
    monthly["roa_revision_3m"] = by_stock["roa"].diff(3)
    monthly["margin_revision_3m"] = by_stock["gross_margin"].diff(3).add(by_stock["netprofit_margin"].diff(3), fill_value=np.nan)
    monthly["report_freshness"] = pd.to_numeric(monthly["report_age_days"], errors="coerce").mul(-1.0)

    def _rolling_zscore(column: str) -> pd.Series:
        local_group = by_stock[column]
        mean = local_group.rolling(36, min_periods=18).mean().reset_index(level=0, drop=True)
        std = local_group.rolling(36, min_periods=18).std(ddof=0).reset_index(level=0, drop=True)
        return monthly[column].sub(mean).div(std.replace(0.0, np.nan))

    monthly["earnings_yield_zscore_36m"] = _rolling_zscore("earnings_yield")
    monthly["book_yield_zscore_36m"] = _rolling_zscore("book_yield")
    monthly["sales_yield_zscore_36m"] = _rolling_zscore("sales_yield")
    monthly["dividend_yield_zscore_36m"] = _rolling_zscore("dividend_yield")
    monthly["earnings_yield_repair_6m"] = by_stock["earnings_yield"].diff(6)
    monthly["book_yield_repair_6m"] = by_stock["book_yield"].diff(6)
    monthly["sales_yield_repair_6m"] = by_stock["sales_yield"].diff(6)
    monthly["dividend_persistence"] = monthly["dividend_positive_8m"].div(
        monthly["dividend_observed_8m"].replace(0, np.nan)
    )
    monthly["value_repair_score"] = monthly[
        ["earnings_yield_repair_6m", "book_yield_repair_6m", "sales_yield_repair_6m"]
    ].mean(axis=1)
    shareholder_yield = monthly["earnings_yield"].mul(0.65).add(monthly["dividend_yield"].mul(0.35), fill_value=0.0)
    monthly["shareholder_yield_proxy"] = shareholder_yield.where(monthly[["earnings_yield", "dividend_yield"]].notna().any(axis=1))
    value_level = monthly[
        ["earnings_yield_zscore_36m", "book_yield_zscore_36m", "sales_yield_zscore_36m", "dividend_yield_zscore_36m"]
    ].mean(axis=1)
    value_dispersion = monthly[
        ["earnings_yield_zscore_36m", "book_yield_zscore_36m", "sales_yield_zscore_36m"]
    ].std(axis=1)
    monthly["deep_value_stability"] = value_level.add(monthly["value_repair_score"], fill_value=0.0).sub(value_dispersion.fillna(0.0).mul(0.15))
    monthly = monthly.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    output: list[pd.DataFrame] = []
    for date, frame in monthly.groupby("trade_date", sort=True):
        list_cutoff = (pd.Timestamp(date) - timedelta(days=180)).strftime("%Y%m%d")
        eligible = (
            frame["list_date"].fillna("99999999").le(list_cutoff)
            & (frame["delist_date"].isna() | frame["delist_date"].gt(pd.Timestamp(date).strftime("%Y%m%d")))
            & frame["circ_mv"].gt(0.0)
            & frame["qfq_close"].gt(0.0)
            & ~frame["stock_name"].fillna("").str.upper().str.contains("ST", regex=False)
        )
        local = frame.loc[eligible].copy()
        if local.empty:
            continue
        local["earnings_yield"] = np.where(local["pe_ttm"].gt(0.0), 1.0 / local["pe_ttm"], np.nan)
        local["book_yield"] = np.where(local["pb"].gt(0.0), 1.0 / local["pb"], np.nan)
        local["sales_yield"] = np.where(local["ps_ttm"].gt(0.0), 1.0 / local["ps_ttm"], np.nan)
        local["dividend_yield"] = local["dv_ttm"] / 100.0
        local["revenue_positive_breadth"] = local["tr_yoy"].gt(0.0).where(local["tr_yoy"].notna())
        local["profit_positive_breadth"] = local["netprofit_yoy"].gt(0.0).where(local["netprofit_yoy"].notna())
        quality_rank = (
            _percentile(local["roe"])
            + _percentile(local["roa"])
            + _percentile(local["netprofit_margin"])
        ) / 3.0
        growth_rank = (
            _percentile(local["op_yoy"])
            + _percentile(local["tr_yoy"])
            + _percentile(local["netprofit_yoy"])
        ) / 3.0
        value_rank = (
            _percentile(local["earnings_yield"])
            + _percentile(local["book_yield"])
            + _percentile(local["sales_yield"])
        ) / 3.0
        dividend_rank = _percentile(local["dividend_yield"])
        local["growth_quality_score"] = growth_rank.mul(0.60).add(quality_rank.mul(0.40))
        local["low_peg_proxy"] = value_rank.mul(growth_rank.add(0.25))
        local["quality_value_match"] = quality_rank.mul(value_rank)
        local["dividend_quality"] = dividend_rank.mul(
            local["dividend_persistence"].clip(0.0, 1.0).fillna(0.0).mul(0.50).add(0.50)
        ).where(local["dividend_yield"].notna())
        balance_rank = (
            _percentile(local["current_ratio"])
            + _percentile(local["debt_to_assets"].mul(-1.0))
            + _percentile(local["assets_turn"])
        ) / 3.0
        revision_rank = (
            _percentile(local["roe_revision_3m"])
            + _percentile(local["roa_revision_3m"])
            + _percentile(local["margin_revision_3m"])
            + _percentile(local["profit_yoy_accel_6m"])
        ) / 4.0
        stability_rank = (
            _percentile(local["roe_stability_8m"])
            + _percentile(local["margin_stability_8m"])
            + _percentile(local["profit_growth_stability"])
        ) / 3.0
        local["balance_sheet_quality"] = balance_rank
        local["earnings_revision_quality"] = revision_rank.mul(0.55).add(quality_rank.mul(0.45))
        local["profitability_stability_score"] = stability_rank.mul(0.65).add(quality_rank.mul(0.35))
        local["value_profitability_interaction"] = value_rank.mul(quality_rank)
        local = _assign_size(local)
        parts: list[pd.DataFrame] = []
        for size in SIZE_LABELS:
            group = local.loc[local["size"].eq(size)].copy()
            if group.empty:
                continue
            group["value_score"] = (
                _percentile(group["earnings_yield"])
                + _percentile(group["book_yield"])
                + _percentile(group["sales_yield"])
            ) / 3.0
            group["growth_score"] = (
                _percentile(group["op_yoy"])
                + _percentile(group["tr_yoy"])
                + _percentile(group["netprofit_yoy"])
            ) / 3.0
            group["style_spread"] = group["growth_score"] - group["value_score"]
            parts.append(group)
        output.append(_assign_style(pd.concat(parts, ignore_index=True)))
    monthly_labels = pd.concat(output, ignore_index=True).sort_values(["trade_date", "ts_code"])
    anchor_map = _quarter_label_anchor_map(signal_dates)
    pieces: list[pd.DataFrame] = []
    for signal_date, anchor_date in anchor_map.items():
        anchor_frame = monthly_labels.loc[monthly_labels["trade_date"].eq(anchor_date)].copy()
        if anchor_frame.empty:
            continue
        anchor_frame["label_anchor_date"] = anchor_date
        anchor_frame["trade_date"] = signal_date
        pieces.append(anchor_frame)
    if not pieces:
        return monthly_labels
    return pd.concat(pieces, ignore_index=True).sort_values(["trade_date", "ts_code"])


def _path_efficiency(close: pd.DataFrame, window: int) -> pd.DataFrame:
    log_price = np.log(close.where(close.gt(0.0)))
    displacement = log_price.diff(window)
    path = log_price.diff().abs().rolling(window, min_periods=max(20, window // 2)).sum()
    return displacement.div(path.replace(0.0, np.nan)).clip(-1.0, 1.0)


def _cross_section_residual(target: pd.DataFrame, regressors: list[pd.DataFrame]) -> pd.DataFrame:
    output = pd.DataFrame(np.nan, index=target.index, columns=target.columns, dtype=float)
    minimum = max(3, min(8, max(1, len(target.columns) // 2)))
    for date in target.index:
        pieces = [target.loc[date].rename("target")]
        pieces.extend(frame.loc[date].rename(f"x{index}") for index, frame in enumerate(regressors))
        sample = pd.concat(pieces, axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < minimum:
            continue
        design = np.column_stack(
            [np.ones(len(sample), dtype=float)]
            + [sample[column].to_numpy(dtype=float) for column in sample.columns[1:]]
        )
        if np.linalg.matrix_rank(design) < design.shape[1]:
            continue
        beta, *_ = np.linalg.lstsq(design, sample["target"].to_numpy(dtype=float), rcond=None)
        fitted = design @ beta
        output.loc[date, sample.index] = sample["target"].to_numpy(dtype=float) - fitted
    return output


def _stock_factor_panel(connection: sqlite3.Connection, signal_dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = "20140101"
    close = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, qfq_close
        FROM stock_ohlcv_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "qfq_close",
        (start,),
    )
    returns = close.pct_change(fill_method=None)
    market = returns.mean(axis=1, skipna=True)
    market_nav = market.fillna(0.0).add(1.0).cumprod()
    signal_dates = pd.DatetimeIndex([date for date in signal_dates if date in close.index])

    amount = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, amount
        FROM stock_ohlcv_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "amount",
        (start,),
    ).reindex_like(close)
    net_flow = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, net_mf_amount
        FROM stock_moneyflow_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "net_mf_amount",
        (start,),
    ).reindex_like(close)
    large_flow = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, buy_lg_amount - sell_lg_amount AS large_flow
        FROM stock_moneyflow_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "large_flow",
        (start,),
    ).reindex_like(close)
    extra_flow = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, buy_elg_amount - sell_elg_amount AS extra_flow
        FROM stock_moneyflow_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "extra_flow",
        (start,),
    ).reindex_like(close)
    turnover = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, turnover_rate
        FROM stock_valuation_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "turnover_rate",
        (start,),
    ).reindex_like(close)
    volume_ratio = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, volume_ratio
        FROM stock_valuation_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "volume_ratio",
        (start,),
    ).reindex_like(close)
    up_limit = _read_pivot(
        connection,
        """
        SELECT trade_date, ts_code, up_limit
        FROM stock_ohlcv_daily
        WHERE trade_date >= ?
        ORDER BY trade_date, ts_code
        """,
        "up_limit",
        (start,),
    ).reindex_like(close)

    market_12_1 = market_nav.shift(21).div(market_nav.shift(252)).sub(1.0)
    market_6_1 = market_nav.shift(21).div(market_nav.shift(126)).sub(1.0)
    market_3_1 = market_nav.shift(21).div(market_nav.shift(63)).sub(1.0)
    market_12_6 = market_nav.shift(126).div(market_nav.shift(252)).sub(1.0)
    market_9_1 = market_nav.shift(21).div(market_nav.shift(189)).sub(1.0)
    market_2_1 = market_nav.shift(21).div(market_nav.shift(42)).sub(1.0)
    momentum_12_1 = close.shift(21).div(close.shift(252)).sub(1.0).sub(market_12_1, axis=0)
    momentum_6_1 = close.shift(21).div(close.shift(126)).sub(1.0).sub(market_6_1, axis=0)
    momentum_3_1 = close.shift(21).div(close.shift(63)).sub(1.0).sub(market_3_1, axis=0)
    momentum_12_6 = close.shift(126).div(close.shift(252)).sub(1.0).sub(market_12_6, axis=0)
    momentum_9_1 = close.shift(21).div(close.shift(189)).sub(1.0).sub(market_9_1, axis=0)
    momentum_2_1 = close.shift(21).div(close.shift(42)).sub(1.0).sub(market_2_1, axis=0)
    momentum_1_abs = close.div(close.shift(21)).sub(1.0)
    momentum_1 = momentum_1_abs.sub(momentum_1_abs.mean(axis=1), axis=0)
    risk = returns.rolling(126, min_periods=63).std(ddof=0)
    up_positive = returns.gt(0.0).where(returns.notna())
    market_excess = returns.sub(market, axis=0)
    relative_positive = market_excess.gt(0.0).where(market_excess.notna())
    limit_up = close.ge(up_limit.mul(0.995)).where(close.notna() & up_limit.notna())
    short_vol = returns.rolling(21, min_periods=15).std(ddof=0)
    long_vol = returns.rolling(126, min_periods=63).std(ddof=0)
    vol_63 = returns.rolling(63, min_periods=36).std(ddof=0)
    trend_stability_60 = returns.rolling(60, min_periods=36).mean().div(
        returns.rolling(60, min_periods=36).std(ddof=0).replace(0.0, np.nan)
    )
    drawdown_resilience_126 = close.div(close.rolling(126, min_periods=63).max().replace(0.0, np.nan)).sub(1.0)
    realized_skew_63 = returns.rolling(63, min_periods=36).skew()
    downside_vol_63 = returns.where(returns.lt(0.0)).rolling(63, min_periods=24).std(ddof=0).mul(-1.0)
    drawdown_resilience_63 = close.div(close.rolling(63, min_periods=30).max().replace(0.0, np.nan)).sub(1.0)
    new_high_proximity_252 = close.div(close.rolling(252, min_periods=126).max().replace(0.0, np.nan))
    volatility_compression = long_vol.div(short_vol.replace(0.0, np.nan))
    turnover_20 = turnover.rolling(20, min_periods=12).mean()
    liquidity_adjusted_momentum = momentum_6_1.div(turnover_20.replace(0.0, np.nan))
    market_up_returns = returns.where(market.gt(0.0), np.nan, axis=0)
    market_up_mean = market.where(market.gt(0.0)).rolling(126, min_periods=40).mean()
    upside_capture_126 = market_up_returns.rolling(126, min_periods=40).mean().sub(market_up_mean, axis=0)
    def flow_ratio(flow: pd.DataFrame, window: int, minimum: int) -> pd.DataFrame:
        return flow.rolling(window, min_periods=minimum).sum().mul(10.0).div(
            amount.rolling(window, min_periods=minimum).sum().replace(0.0, np.nan)
        )

    total_ratio_5 = flow_ratio(net_flow, 5, 3)
    total_ratio_10 = flow_ratio(net_flow, 10, 6)
    total_ratio_20 = flow_ratio(net_flow, 20, 12)
    total_ratio_60 = flow_ratio(net_flow, 60, 36)
    large_ratio_5 = flow_ratio(large_flow, 5, 3)
    large_ratio_10 = flow_ratio(large_flow, 10, 6)
    large_ratio_20 = flow_ratio(large_flow, 20, 12)
    large_ratio_60 = flow_ratio(large_flow, 60, 36)
    extra_ratio_10 = flow_ratio(extra_flow, 10, 6)
    extra_ratio_20 = flow_ratio(extra_flow, 20, 12)
    extra_ratio_60 = flow_ratio(extra_flow, 60, 36)
    turnover_expansion = turnover.rolling(5, min_periods=3).mean().div(
        turnover.rolling(60, min_periods=36).mean().replace(0.0, np.nan)
    )
    northbound_proxy = large_ratio_20.add(extra_ratio_20, fill_value=np.nan).div(2.0)
    flow_price_alignment = total_ratio_20.mul(close.pct_change(20, fill_method=None))
    flow_residual_20 = _cross_section_residual(total_ratio_20, [turnover_expansion, momentum_1_abs.abs(), short_vol])
    flow_absorption_20 = total_ratio_20.sub(momentum_1_abs.mul(0.50))
    smart_money_acceleration = large_ratio_20.sub(large_ratio_60).add(
        extra_ratio_20.sub(extra_ratio_60), fill_value=np.nan
    ).div(2.0)
    flow_total_acceleration_5_20 = total_ratio_5.sub(total_ratio_20)
    flow_large_acceleration_5_20 = large_ratio_5.sub(large_ratio_20)
    flow_extra_acceleration_5_20 = extra_ratio_10.sub(extra_ratio_20)
    flow_denominator_20 = large_ratio_20.abs().add(extra_ratio_20.abs(), fill_value=np.nan).replace(0.0, np.nan)
    flow_denominator_60 = large_ratio_60.abs().add(extra_ratio_60.abs(), fill_value=np.nan).replace(0.0, np.nan)
    flow_smart_share_20 = extra_ratio_20.sub(large_ratio_20).div(flow_denominator_20)
    flow_smart_share_60 = extra_ratio_60.sub(large_ratio_60).div(flow_denominator_60)
    flow_stability_20 = total_ratio_20.rolling(20, min_periods=12).std(ddof=0).mul(-1.0)
    flow_turnover_residual_60 = _cross_section_residual(
        total_ratio_60,
        [turnover.rolling(60, min_periods=36).mean(), momentum_6_1.abs(), long_vol],
    )
    turnover_residual_heat = _cross_section_residual(
        turnover.rolling(20, min_periods=12).mean(),
        [momentum_1_abs.abs(), short_vol],
    )
    volume_price_heat = volume_ratio.rolling(20, min_periods=12).mean().mul(momentum_1_abs.abs())
    volatility_heat = short_vol
    amount_20 = amount.rolling(20, min_periods=12).mean()
    volume_ratio_5 = volume_ratio.rolling(5, min_periods=3).mean()
    volume_ratio_60 = volume_ratio.rolling(60, min_periods=36).mean()
    turnover_mean_252 = turnover_20.rolling(252, min_periods=126).mean()
    turnover_std_252 = turnover_20.rolling(252, min_periods=126).std(ddof=0).replace(0.0, np.nan)
    amount_mean_252 = amount_20.rolling(252, min_periods=126).mean()
    amount_std_252 = amount_20.rolling(252, min_periods=126).std(ddof=0).replace(0.0, np.nan)
    turnover_percentile_252 = turnover_20.sub(turnover_mean_252).div(turnover_std_252)
    amount_percentile_252 = amount_20.sub(amount_mean_252).div(amount_std_252)
    volume_ratio_spike_5_60 = volume_ratio_5.div(volume_ratio_60.replace(0.0, np.nan))
    limit_up_persistence_60 = limit_up.rolling(60, min_periods=36).mean()
    gap_to_high_252_heat = new_high_proximity_252
    downside_vol_heat = downside_vol_63.mul(-1.0)
    return_skew_heat = realized_skew_63.abs()
    flow_concentration_heat = extra_ratio_20.abs().div(total_ratio_20.abs().add(large_ratio_20.abs(), fill_value=np.nan).replace(0.0, np.nan))
    liquidity_impact_heat = momentum_1_abs.abs().div(turnover_20.replace(0.0, np.nan))
    turnover_volatility_heat = turnover.rolling(60, min_periods=36).std(ddof=0)
    raw_frames = {
        "momentum_12_1": momentum_12_1,
        "momentum_6_1": momentum_6_1,
        "momentum_3_1": momentum_3_1,
        "momentum_1": momentum_1,
        "risk_adjusted_momentum": momentum_6_1.div(risk.replace(0.0, np.nan)),
        "path_efficiency_126": _path_efficiency(close, 126),
        "path_efficiency_63": _path_efficiency(close, 63),
        "distance_ma120": close.div(close.rolling(120, min_periods=60).mean()).sub(1.0),
        "distance_ma60": close.div(close.rolling(60, min_periods=30).mean()).sub(1.0),
        "breadth_20": up_positive.rolling(20, min_periods=12).mean(),
        "breadth_60": up_positive.rolling(60, min_periods=36).mean(),
        "short_reversal": close.pct_change(5, fill_method=None).mul(-1.0),
        "momentum_consistency_60": relative_positive.rolling(60, min_periods=36).mean(),
        "low_vol_63": vol_63.mul(-1.0),
        "drawdown_resilience_126": drawdown_resilience_126,
        "trend_stability_60": trend_stability_60,
        "momentum_12_6": momentum_12_6,
        "momentum_9_1": momentum_9_1,
        "momentum_2_1": momentum_2_1,
        "realized_skew_63": realized_skew_63,
        "downside_vol_63": downside_vol_63,
        "drawdown_resilience_63": drawdown_resilience_63,
        "new_high_proximity_252": new_high_proximity_252,
        "volatility_compression": volatility_compression,
        "liquidity_adjusted_momentum": liquidity_adjusted_momentum,
        "upside_capture_126": upside_capture_126,
        "flow_total_5": total_ratio_5,
        "flow_total_10": total_ratio_10,
        "flow_total_20": total_ratio_20,
        "flow_total_60": total_ratio_60,
        "flow_large_structure_5": large_ratio_5.sub(total_ratio_5),
        "flow_large_structure_10": large_ratio_10.sub(total_ratio_10),
        "flow_large_structure_20": large_ratio_20.sub(total_ratio_20),
        "flow_large_structure_60": large_ratio_60.sub(total_ratio_60),
        "flow_extra_structure_10": extra_ratio_10.sub(total_ratio_10),
        "flow_extra_structure_20": extra_ratio_20.sub(total_ratio_20),
        "flow_extra_structure_60": extra_ratio_60.sub(total_ratio_60),
        "flow_breadth_20": net_flow.gt(0.0).where(net_flow.notna()).rolling(20, min_periods=12).mean(),
        "flow_persistence_20": net_flow.gt(0.0).where(net_flow.notna()).rolling(20, min_periods=12).mean(),
        "flow_total_acceleration_20_60": total_ratio_20.sub(total_ratio_60),
        "flow_large_acceleration_20_60": large_ratio_20.sub(large_ratio_60),
        "flow_extra_acceleration_20_60": extra_ratio_20.sub(extra_ratio_60),
        "flow_total_acceleration_5_20": flow_total_acceleration_5_20,
        "flow_large_acceleration_5_20": flow_large_acceleration_5_20,
        "flow_extra_acceleration_5_20": flow_extra_acceleration_5_20,
        "flow_smart_share_20": flow_smart_share_20,
        "flow_smart_share_60": flow_smart_share_60,
        "flow_stability_20": flow_stability_20,
        "flow_turnover_residual_60": flow_turnover_residual_60,
        "northbound_proxy": northbound_proxy,
        "flow_price_alignment": flow_price_alignment,
        "flow_residual_20": flow_residual_20,
        "flow_absorption_20": flow_absorption_20,
        "smart_money_acceleration": smart_money_acceleration,
        "turnover_level": turnover.rolling(20, min_periods=12).mean(),
        "turnover_expansion": turnover_expansion,
        "volume_ratio": volume_ratio.rolling(20, min_periods=12).mean(),
        "amount_concentration": amount.rolling(20, min_periods=12).mean(),
        "limit_up_heat": limit_up.rolling(20, min_periods=12).mean(),
        "short_momentum_heat": momentum_1_abs,
        "price_distance_heat": close.div(close.rolling(60, min_periods=30).mean()).sub(1.0),
        "volatility_expansion": short_vol.div(long_vol.replace(0.0, np.nan)),
        "breadth_heat": up_positive.rolling(5, min_periods=3).mean(),
        "low_dispersion_heat": returns.rolling(20, min_periods=12).std(ddof=0).mul(-1.0),
        "flow_price_crowding": total_ratio_20.abs().mul(momentum_1_abs.abs()),
        "flow_turnover_crowding": total_ratio_20.abs().mul(turnover_expansion.replace([np.inf, -np.inf], np.nan)),
        "turnover_residual_heat": turnover_residual_heat,
        "volume_price_heat": volume_price_heat,
        "volatility_heat": volatility_heat,
        "turnover_percentile_252": turnover_percentile_252,
        "amount_percentile_252": amount_percentile_252,
        "volume_ratio_spike_5_60": volume_ratio_spike_5_60,
        "limit_up_persistence_60": limit_up_persistence_60,
        "gap_to_high_252_heat": gap_to_high_252_heat,
        "downside_vol_heat": downside_vol_heat,
        "return_skew_heat": return_skew_heat,
        "flow_concentration_heat": flow_concentration_heat,
        "liquidity_impact_heat": liquidity_impact_heat,
        "turnover_volatility_heat": turnover_volatility_heat,
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        panel = pd.concat(
            {name: frame.reindex(signal_dates).stack(dropna=False) for name, frame in raw_frames.items()},
            axis=1,
        )
    panel.index.names = ["trade_date", "ts_code"]
    return panel.reset_index(), returns


def _source_cache_signature() -> dict[str, Any]:
    stat = DATABASE.stat()
    return {
        "cache_version": "style-source/3.5-alpha-direction-remap",
        "database_size": int(stat.st_size),
        "database_mtime_ns": int(stat.st_mtime_ns),
        "start_signal": START_SIGNAL,
        "signal_cutoff": SIGNAL_CUTOFF_RAW,
    }


def _read_source_cache() -> SourceData | None:
    try:
        if not SOURCE_CACHE.exists() or not SOURCE_CACHE_META.exists():
            return None
        meta = json.loads(SOURCE_CACHE_META.read_text(encoding="utf-8"))
        if meta != _source_cache_signature():
            return None
        main_module = sys.modules.get("__main__")
        if main_module is not None and not hasattr(main_module, "SourceData"):
            setattr(main_module, "SourceData", SourceData)
        with SOURCE_CACHE.open("rb") as handle:
            source = pickle.load(handle)
        if isinstance(source, dict):
            return SourceData(
                trade_dates=pd.DatetimeIndex(source["trade_dates"]),
                signal_dates=pd.DatetimeIndex(source["signal_dates"]),
                execution_dates={pd.Timestamp(k): pd.Timestamp(v) for k, v in source["execution_dates"].items()},
                labels=source["labels"],
                daily_returns=source["daily_returns"],
            )
        if not isinstance(source, SourceData):
            return None
        return source
    except Exception:
        return None


def _write_source_cache(source: SourceData) -> None:
    SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_cache = SOURCE_CACHE.with_suffix(".tmp")
    tmp_meta = SOURCE_CACHE_META.with_suffix(".tmp")
    payload = {
        "trade_dates": source.trade_dates,
        "signal_dates": source.signal_dates,
        "execution_dates": source.execution_dates,
        "labels": source.labels,
        "daily_returns": source.daily_returns,
    }
    with tmp_cache.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_meta.write_text(json.dumps(_source_cache_signature(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_cache.replace(SOURCE_CACHE)
    tmp_meta.replace(SOURCE_CACHE_META)


def _load_sources() -> SourceData:
    cached = _read_source_cache()
    if cached is not None:
        return cached
    with _open_read_only() as connection:
        trade_dates, signal_dates = _month_end_dates(connection)
        execution_dates = _execution_dates(trade_dates, signal_dates)
        signal_dates = pd.DatetimeIndex([date for date in signal_dates if date in execution_dates])
        labels = _load_monthly_labels(connection, signal_dates)
        stock_factors, returns = _stock_factor_panel(connection, signal_dates)

    labels = labels.merge(stock_factors, on=["trade_date", "ts_code"], how="left")
    if "volume_ratio_y" in labels.columns:
        labels["volume_ratio"] = labels["volume_ratio_y"]
        labels = labels.drop(columns=[column for column in ("volume_ratio_x", "volume_ratio_y") if column in labels.columns])
    source = SourceData(trade_dates, signal_dates, execution_dates, labels, returns)
    _write_source_cache(source)
    return source



def _standard_benchmark_signature(trade_dates: pd.DatetimeIndex) -> dict[str, Any]:
    date_index = pd.DatetimeIndex(trade_dates).sort_values()
    return {
        "cache_version": "style-standard-benchmark-cni/1.0",
        "start": _iso(date_index.min()) if len(date_index) else None,
        "end": _iso(date_index.max()) if len(date_index) else None,
        "size_index": STANDARD_SIZE_INDEX,
        "style_index": STANDARD_STYLE_INDEX,
    }


def _read_standard_benchmark_cache(signature: dict[str, Any]) -> tuple[dict[str, pd.Series], dict[str, Any]] | None:
    try:
        if not STANDARD_BENCHMARK_CACHE.exists() or not STANDARD_BENCHMARK_META.exists():
            return None
        meta = json.loads(STANDARD_BENCHMARK_META.read_text(encoding="utf-8"))
        if meta.get("signature") != signature:
            return None
        with STANDARD_BENCHMARK_CACHE.open("rb") as handle:
            payload = pickle.load(handle)
        returns = {
            str(key): pd.Series(value, dtype=float).sort_index()
            for key, value in payload.get("returns", {}).items()
        }
        info = dict(payload.get("info", {}))
        return returns, info
    except Exception:
        return None


def _write_standard_benchmark_cache(
    signature: dict[str, Any],
    returns: dict[str, pd.Series],
    info: dict[str, Any],
) -> None:
    SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_cache = STANDARD_BENCHMARK_CACHE.with_suffix(".tmp")
    tmp_meta = STANDARD_BENCHMARK_META.with_suffix(".tmp")
    payload = {
        "returns": {key: value.sort_index() for key, value in returns.items()},
        "info": info,
    }
    with tmp_cache.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_meta.write_text(
        json.dumps({"signature": signature, "generated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_cache.replace(STANDARD_BENCHMARK_CACHE)
    tmp_meta.replace(STANDARD_BENCHMARK_META)


def _load_cni_index_returns(
    code: str,
    trade_dates: pd.DatetimeIndex,
    start_date: str,
    end_date: str,
) -> pd.Series:
    import akshare as ak  # lazy import: the model falls back to internal benchmarks if unavailable

    frame = ak.index_hist_cni(symbol=str(code), start_date=start_date, end_date=end_date)
    if frame is None or frame.empty:
        raise ValueError(f"cni_index_empty:{code}")
    local = frame.copy()
    date_col = "日期" if "日期" in local.columns else local.columns[0]
    close_col = "收盘价" if "收盘价" in local.columns else local.columns[-1]
    local[date_col] = pd.to_datetime(local[date_col], errors="coerce")
    close = pd.to_numeric(local[close_col], errors="coerce")
    close.index = local[date_col]
    close = close.dropna().sort_index()
    close = close[~close.index.duplicated(keep="last")]
    aligned = close.reindex(pd.DatetimeIndex(trade_dates).sort_values()).ffill()
    returns = aligned.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    return returns


def _mean_return_series(parts: Iterable[pd.Series]) -> pd.Series:
    frames = [pd.Series(part, dtype=float) for part in parts]
    if not frames:
        return pd.Series(dtype=float)
    return pd.concat(frames, axis=1).mean(axis=1, skipna=True)


def _load_standard_benchmark_returns(trade_dates: pd.DatetimeIndex) -> tuple[dict[str, pd.Series], dict[str, Any]]:
    """Load standard size/style index benchmarks without touching paid databases.

    The strategy signal and holdings are still built from the stock-level style
    labels.  These series only replace the comparison benchmark in the NAV and
    annual table.  If AKShare or the network is unavailable, the model keeps the
    internal equal-weight stock-pool benchmark and records the fallback reason.
    """
    date_index = pd.DatetimeIndex(trade_dates).sort_values()
    if date_index.empty:
        return {}, {"status": "unavailable", "reason": "empty_trade_dates"}
    signature = _standard_benchmark_signature(date_index)
    cached = _read_standard_benchmark_cache(signature)
    if cached is not None:
        returns, info = cached
        info = {**info, "cache": str(STANDARD_BENCHMARK_CACHE)}
        return returns, info

    start_date = pd.Timestamp(date_index.min()).strftime("%Y%m%d")
    end_date = pd.Timestamp(date_index.max()).strftime("%Y%m%d")
    all_codes = sorted(set(STANDARD_SIZE_INDEX.values()) | set(STANDARD_STYLE_INDEX.values()))
    raw: dict[str, pd.Series] = {}
    try:
        for code in all_codes:
            raw[code] = _load_cni_index_returns(code, date_index, start_date, end_date)
    except Exception as exc:
        return {}, {
            "status": "fallback_internal_stock_pool_benchmark",
            "source": "AKShare-中证指数有限公司公开指数接口",
            "reason": str(exc)[:300],
            "attempted_codes": all_codes,
        }

    size_returns = {name: raw[code] for name, code in STANDARD_SIZE_INDEX.items() if code in raw}
    style_growth = raw.get(STANDARD_STYLE_INDEX["成长"])
    style_value = raw.get(STANDARD_STYLE_INDEX["价值"])
    style_dividend = raw.get(STANDARD_STYLE_INDEX["红利"])
    if style_growth is None or style_value is None or style_dividend is None or len(size_returns) != 3:
        return {}, {
            "status": "fallback_internal_stock_pool_benchmark",
            "source": "AKShare-中证指数有限公司公开指数接口",
            "reason": "missing_required_standard_index",
            "available_codes": sorted(raw),
        }
    style_returns = {
        "成长": style_growth,
        "均衡": _mean_return_series([style_growth, style_value]),
        "价值": style_value,
        "红利": style_dividend,
    }
    cell_returns: list[pd.Series] = []
    for size in SIZE_LABELS:
        for style in STYLE_LABELS:
            cell_returns.append(_mean_return_series([size_returns[size], style_returns[style]]))
    strategy_returns = {
        "size3": _mean_return_series(size_returns.values()),
        "style4": _mean_return_series(style_returns.values()),
        "style12": _mean_return_series(cell_returns),
    }
    info = {
        "status": "standard_cni_index_benchmark",
        "source": "AKShare index_hist_cni / 中证指数有限公司公开行情",
        "size_index": {label: {"code": code, "name": STANDARD_INDEX_NAME.get(code, code)} for label, code in STANDARD_SIZE_INDEX.items()},
        "style_index": {
            "成长": {"code": STANDARD_STYLE_INDEX["成长"], "name": STANDARD_INDEX_NAME[STANDARD_STYLE_INDEX["成长"]]},
            "均衡": {"code": "0.5*399370+0.5*399371", "name": "均衡代理=国证成长50%+国证价值50%"},
            "价值": {"code": STANDARD_STYLE_INDEX["价值"], "name": STANDARD_INDEX_NAME[STANDARD_STYLE_INDEX["价值"]]},
            "红利": {"code": STANDARD_STYLE_INDEX["红利"], "name": STANDARD_INDEX_NAME[STANDARD_STYLE_INDEX["红利"]]},
        },
        "style12_rule": "每个风格箱基准日收益=50%对应市值指数+50%对应风格指数；12个风格箱等权。",
        "date_range": [_iso(date_index.min()), _iso(date_index.max())],
    }
    _write_standard_benchmark_cache(signature, strategy_returns, info)
    return strategy_returns, {**info, "cache": str(STANDARD_BENCHMARK_CACHE)}

def _weighted_group_values(
    labels: pd.DataFrame,
    label_column: str,
    fields: list[str],
    groups: Iterable[str],
) -> dict[str, pd.DataFrame]:
    """有效样本市值加权的组别因子聚合。

    旧口径用有效样本做分子，却用全组流通市值做分母。财报、资金流和部分估值字段
    缺失较多时，这会把缺失样本误当成零暴露，系统性压低覆盖率较差的组别。这里按每个
    字段分别计算有效样本分母，同时一次性完成全部字段的 groupby，既保证口径正确，也
    避免 50 多个字段重复聚合。
    """
    group_list = list(groups)
    columns = ["trade_date", label_column, "circ_mv", *fields]
    local = labels.loc[labels[label_column].isin(group_list), columns].copy()
    local["circ_mv"] = pd.to_numeric(local["circ_mv"], errors="coerce")
    local = local.loc[local["circ_mv"].gt(0.0)]
    if local.empty:
        return {
            field: pd.DataFrame(index=pd.DatetimeIndex([]), columns=group_list, dtype=float)
            for field in fields
        }
    value = local[fields].apply(pd.to_numeric, errors="coerce")
    weight = local["circ_mv"].astype(float)
    keys = [local["trade_date"], local[label_column]]
    weighted_sum = value.mul(weight, axis=0).groupby(keys).sum(min_count=1)
    valid_weight = value.notna().mul(weight, axis=0).groupby(keys).sum(min_count=1)
    aggregated = weighted_sum.div(valid_weight.replace(0.0, np.nan))
    output: dict[str, pd.DataFrame] = {}
    for field in fields:
        output[field] = aggregated[field].unstack().reindex(columns=group_list).sort_index()
    return output

def _raw_to_scores(raw: dict[str, pd.DataFrame], fields: list[str]) -> dict[str, pd.DataFrame]:
    return {
        field: six._monthly_atomic_score(raw[field], high_is_good=HIGH_IS_GOOD.get(field, True))
        for field in fields
    }


def _mean_available(frames: list[pd.DataFrame], minimum: int) -> pd.DataFrame:
    zero = pd.DataFrame(0.0, index=frames[0].index, columns=frames[0].columns)
    numerator = sum((frame.fillna(0.0) for frame in frames), start=zero.copy())
    count = sum((frame.notna().astype(float) for frame in frames), start=zero.copy())
    return numerator.div(count.replace(0.0, np.nan)).where(count.ge(minimum))


def _dimension_scores(raw: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, pd.DataFrame]]]:
    factor_scores = {
        "fundamental": _raw_to_scores(raw, FUNDAMENTAL_FIELDS),
        "technical": _raw_to_scores(raw, TECHNICAL_FIELDS),
        "valuation": _raw_to_scores(raw, VALUATION_FIELDS),
        "funds": _raw_to_scores(raw, FUNDS_FIELDS),
        "crowding": _raw_to_scores(raw, CROWDING_FIELDS),
    }
    dimensions = {
        "fundamental": six._cluster_balanced_score(
            factor_scores["fundamental"],
            [
                ["roe", "roa", "gross_margin", "netprofit_margin"],
                ["assets_turn", "current_ratio", "debt_to_assets"],
                ["tr_yoy", "netprofit_yoy", "op_yoy", "profit_revenue_leverage"],
                ["revenue_positive_breadth", "profit_positive_breadth"],
                [
                    "roe_improvement_6m",
                    "roa_improvement_6m",
                    "margin_improvement_6m",
                    "profit_yoy_accel_6m",
                    "revenue_yoy_accel_6m",
                    "quality_momentum_12m",
                    "growth_quality_score",
                    "profit_growth_stability",
                ],
                ["roe_stability_8m", "margin_stability_8m", "asset_turn_stability_8m", "profitability_stability_score"],
                ["roe_revision_3m", "roa_revision_3m", "margin_revision_3m", "earnings_revision_quality", "report_freshness"],
                ["debt_improvement_6m", "asset_turn_improvement_6m", "balance_sheet_quality"],
                [
                    "factor_lab_quality_value",
                    "factor_lab_fundamental_revision",
                    "factor_lab_genetic_quality_momentum",
                    "factor_lab_openfe_quality_interaction",
                    "mined_value_quality_momentum",
                    "mined_small_value_profitability",
                    "mined_report_quality_value_momentum",
                    "mined_factor_composite_v1",
                    "value_profitability_interaction",
                ],
            ],
            3,
        ),
        "technical": six._cluster_balanced_score(
            factor_scores["technical"],
            [
                ["momentum_12_1", "momentum_6_1", "risk_adjusted_momentum"],
                ["momentum_12_6", "momentum_9_1", "momentum_2_1", "liquidity_adjusted_momentum"],
                ["momentum_3_1", "momentum_1", "short_reversal"],
                ["path_efficiency_126", "path_efficiency_63", "trend_stability_60", "upside_capture_126"],
                ["distance_ma120", "distance_ma60", "drawdown_resilience_126", "drawdown_resilience_63"],
                ["breadth_20", "breadth_60", "momentum_consistency_60"],
                ["low_vol_63", "downside_vol_63", "realized_skew_63", "volatility_compression", "new_high_proximity_252"],
                [
                    "factor_lab_kline_trend",
                    "factor_lab_formulaic_alpha_mcts_4004ed0a",
                    "factor_lab_formulaic_alpha_mcts_887931da",
                    "factor_lab_openfe_technical_interaction",
                    "factor_lab_genetic_momentum_4c06c340",
                    "factor_lab_genetic_lowvol_reversal_alpha",
                    "factor_lab_openfe_style_alpha_interaction",
                    "mined_trend_low_vol_confirm",
                    "mined_momentum_reversal",
                    "mined_kline_context_factor",
                    "mined_nonlinear_rank_alpha",
                    "mined_deep_rank_alpha",
                ],
            ],
            3,
        ),
        "valuation": six._cluster_balanced_score(
            factor_scores["valuation"],
            [
                ["earnings_yield", "book_yield", "sales_yield"],
                ["earnings_yield_repair_6m", "book_yield_repair_6m", "sales_yield_repair_6m"],
                ["earnings_yield_zscore_36m", "book_yield_zscore_36m", "sales_yield_zscore_36m", "dividend_yield_zscore_36m"],
                ["dividend_yield", "dividend_persistence", "dividend_quality", "shareholder_yield_proxy"],
                ["low_peg_proxy", "quality_value_match", "value_repair_score", "deep_value_stability"],
                ["mined_dividend_lowvol_quality", "mined_defensive_dividend_quality"],
            ],
            2,
        ),
        "funds": six._cluster_balanced_score(
            factor_scores["funds"],
            [
                ["flow_total_5", "flow_total_10", "flow_total_20", "flow_total_60"],
                ["flow_large_structure_5", "flow_large_structure_10", "flow_large_structure_20", "flow_large_structure_60", "northbound_proxy"],
                ["flow_extra_structure_20", "flow_extra_structure_60", "smart_money_acceleration", "flow_smart_share_20", "flow_smart_share_60"],
                ["flow_breadth_20", "flow_persistence_20", "flow_stability_20"],
                ["flow_total_acceleration_5_20", "flow_total_acceleration_20_60", "flow_large_acceleration_5_20", "flow_large_acceleration_20_60", "flow_extra_acceleration_5_20", "flow_extra_acceleration_20_60"],
                ["flow_price_alignment", "flow_residual_20", "flow_absorption_20", "flow_turnover_residual_60"],
                ["factor_lab_flow_anti_crowding", "factor_lab_genetic_flow_value", "factor_lab_openfe_flow_interaction", "mined_moneyflow_momentum", "mined_agent_moneyflow_anti_crowding"],
            ],
            3,
        ),
        "crowding": six._cluster_balanced_score(
            factor_scores["crowding"],
            [
                ["turnover_level", "turnover_expansion", "turnover_residual_heat", "turnover_percentile_252", "turnover_volatility_heat"],
                ["volume_ratio", "volume_ratio_spike_5_60", "amount_concentration", "volume_price_heat", "amount_percentile_252"],
                ["limit_up_heat", "limit_up_persistence_60", "short_momentum_heat", "price_distance_heat", "gap_to_high_252_heat"],
                ["volatility_expansion", "volatility_heat", "downside_vol_heat", "return_skew_heat", "low_dispersion_heat"],
                ["breadth_heat", "flow_price_crowding", "flow_turnover_crowding", "flow_concentration_heat", "liquidity_impact_heat"],
            ],
            3,
        ),
    }
    return dimensions, factor_scores


def _composite_score(dimensions: dict[str, pd.DataFrame]) -> pd.DataFrame:
    crowding = dimensions["crowding"].clip(0.0, 1.0)
    low_crowding = _rank_frame(1.0 - crowding)
    base = _mean_available(
        [
            dimensions["fundamental"],
            dimensions["technical"],
            dimensions["valuation"],
            dimensions["funds"],
            low_crowding,
        ],
        3,
    )
    risk_cost = crowding.pow(2).mul(0.12)
    return base.sub(risk_cost).clip(0.0, 1.0).rank(axis=1, pct=True, method="average")


def _dimension_weight_score(
    dimensions: dict[str, pd.DataFrame],
    weights: dict[str, float],
    crowding_penalty: float,
    minimum_dimensions: int = 4,
) -> pd.DataFrame:
    """五因子显式权重打分。

    只在一级维度层做权重，不绕开底层二级因子的统一PIT分位处理；拥挤度先转为
    低拥挤收益项，再额外对极端拥挤做凸性扣分，避免短期资金/热度把组合推到过热域。
    """
    items: list[tuple[pd.DataFrame, float]] = []
    for dimension, weight in weights.items():
        if dimension not in dimensions or float(weight) == 0.0:
            continue
        if dimension == "crowding":
            items.append((_rank_frame(1.0 - dimensions["crowding"].clip(0.0, 1.0)), abs(float(weight))))
        else:
            items.append((dimensions[dimension], abs(float(weight))))
    if not items:
        return pd.DataFrame()
    minimum = min(max(1, int(minimum_dimensions)), len(items))
    score = _weighted_dimension_mean(items, minimum)
    if crowding_penalty > 0.0 and "crowding" in dimensions:
        score = score.sub(dimensions["crowding"].clip(0.0, 1.0).pow(2).mul(float(crowding_penalty)))
    return _rank_frame(score).clip(0.0, 1.0)



def _quality_adjusted_dimension_score(
    dimensions: dict[str, pd.DataFrame],
    base_weights: dict[str, float],
    dimension_quality: dict[str, Any],
    crowding_penalty: float,
    minimum_dimensions: int = 3,
) -> pd.DataFrame:
    """训练/验证有效性加权的五因子得分。"""
    adjusted: dict[str, float] = {}
    for dimension, base_weight in base_weights.items():
        try:
            quality = float(dimension_quality.get(dimension) or 0.0)
        except (TypeError, ValueError):
            quality = 0.0
        if not math.isfinite(quality) or quality <= 0.0:
            continue
        adjusted[dimension] = abs(float(base_weight)) * math.sqrt(max(quality, 1e-6))
    if not adjusted:
        return pd.DataFrame(0.5, index=next(iter(dimensions.values())).index, columns=next(iter(dimensions.values())).columns)
    local_minimum = min(max(1, int(minimum_dimensions)), len(adjusted))
    return _dimension_weight_score(dimensions, adjusted, crowding_penalty=crowding_penalty, minimum_dimensions=local_minimum)


def _metric_number(block: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not isinstance(block, dict):
        return default
    try:
        value = float(block.get(key))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _dimension_profile_quality(profile: dict[str, Any], mode: str = "spread") -> float:
    """一级维度进入总分前的训练/验证质量评分。

    只读取训练期和验证期的 RankIC 与 Top-Bottom 下月多空收益，测试期仅写入
    profile 作为报告字段。Top-Bottom 是硬证据：验证期多空收益为负的维度不会
    因为 IC 好看而被放大。
    """
    train_ic = profile.get("train") or {}
    validation_ic = profile.get("validation") or {}
    train_spread = profile.get("train_spread") or {}
    validation_spread = profile.get("validation_spread") or {}
    train_spread_annual = _metric_number(train_spread, "annualized_spread")
    validation_spread_annual = _metric_number(validation_spread, "annualized_spread")
    train_spread_t = max(0.0, _metric_number(train_spread, "spread_t"))
    validation_spread_t = max(0.0, _metric_number(validation_spread, "spread_t"))
    train_spread_hit = max(0.0, (_metric_number(train_spread, "positive_rate") - 0.45) / 0.35)
    validation_spread_hit = max(0.0, (_metric_number(validation_spread, "positive_rate") - 0.45) / 0.35)
    train_icir = max(0.0, _metric_number(train_ic, "icir"))
    validation_icir = max(0.0, _metric_number(validation_ic, "icir"))
    spread_floor = min(train_spread_annual, validation_spread_annual)
    ic_floor = min(train_icir, validation_icir)
    hit_floor = min(train_spread_hit, validation_spread_hit)
    if mode == "icir":
        quality = 0.040 * ic_floor + 0.35 * max(0.0, spread_floor) + 0.010 * hit_floor
    elif mode == "blend":
        static_quality = max(0.0, float(profile.get("static_quality") or 0.0))
        quality = 0.050 * math.sqrt(static_quality) + 0.25 * max(0.0, spread_floor) + 0.010 * hit_floor
    else:
        quality = 0.012 * max(0.0, spread_floor * 12.0) + 0.018 * min(train_spread_t, validation_spread_t) + 0.010 * ic_floor + 0.012 * hit_floor
    # 若验证期多空收益本身不正，说明该一级维度当作正向暴露并不可靠，降到近中性。
    if validation_spread_annual <= 0.0 or _metric_number(validation_spread, "positive_rate") < 0.48:
        quality *= 0.20
    if not profile.get("ic_gate") and not profile.get("spread_gate"):
        quality *= 0.25
    return min(3.0, max(0.0, quality))


def _dimension_spread_quality_score(
    dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    maturities: pd.Series,
    base_weights: dict[str, float],
    crowding_penalty: float,
    mode: str = "spread",
    minimum_dimensions: int = 2,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """一级因子层的 RankIC + 多空收益准入加权。

    与二级原子因子准入互补：先得到五个一级维度，再检验每个一级维度本身
    是否能解释下月风格域收益。通过者按训练/验证质量加权；未通过者保持
    中性，不把噪声维度硬塞进总分。
    """
    first = next(iter(dimensions.values()))
    items: list[tuple[pd.DataFrame, float]] = []
    profiles: dict[str, Any] = {}
    raw_weights: dict[str, float] = {}
    for dimension in STYLE_DIMENSIONS:
        frame = dimensions.get(dimension)
        if frame is None or frame.empty:
            continue
        profile = _factor_profile(frame, forward, maturities, bad_signal=dimension == "crowding")
        quality = _dimension_profile_quality(profile, mode=mode)
        if dimension == "crowding":
            signed = _rank_frame(1.0 - frame.clip(0.0, 1.0))
        else:
            signed = frame if float(profile.get("direction") or 1.0) >= 0.0 else 1.0 - frame
        base = abs(float(base_weights.get(dimension, 0.0)))
        weight = base * math.sqrt(max(quality, 0.0))
        profiles[dimension] = {
            "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
            "direction": "反向" if float(profile.get("direction") or 1.0) < 0.0 else "正向",
            "quality": _finite(quality),
            "base_weight": _finite(base),
            "effective_weight_raw": _finite(weight),
            "admitted": bool(weight > 0.0),
            "ic_gate": bool(profile.get("ic_gate")),
            "spread_gate": bool(profile.get("spread_gate")),
            "train": profile.get("train"),
            "validation": profile.get("validation"),
            "test_report_only": profile.get("test"),
            "train_spread": profile.get("train_spread"),
            "validation_spread": profile.get("validation_spread"),
            "test_spread_report_only": profile.get("test_spread_report_only"),
        }
        if weight > 0.0:
            items.append((signed, weight))
            raw_weights[dimension] = weight
    total = sum(raw_weights.values())
    normalized = {key: _finite(value / total) for key, value in raw_weights.items()} if total > 0.0 else {}
    for dimension, value in normalized.items():
        profiles[dimension]["effective_weight"] = value
    if not items:
        neutral = pd.DataFrame(0.5, index=first.index, columns=first.columns, dtype=float)
        return neutral, {"mode": mode, "profiles": profiles, "effective_weights": {}}
    local_minimum = min(max(1, int(minimum_dimensions)), len(items))
    score = _weighted_dimension_mean(items, local_minimum)
    crowd = dimensions.get("crowding")
    if crowding_penalty > 0.0 and crowd is not None and not crowd.empty:
        score = score.sub(crowd.clip(0.0, 1.0).pow(2).mul(float(crowding_penalty)))
    return _rank_frame(score).clip(0.0, 1.0), {
        "mode": mode,
        "profiles": profiles,
        "effective_weights": normalized,
        "minimum_dimensions": int(local_minimum),
        "crowding_penalty": _finite(float(crowding_penalty)),
    }

def _blend_score(base: pd.DataFrame, challenger: pd.DataFrame, challenger_weight: float) -> pd.DataFrame:
    """横截面分数融合。

    用于把原有全维稳健打分和新增增强打分做小比例融合；权重必须预注册，
    后续仍由训练集和验证集的目标函数决定是否采用。
    """
    weight = min(max(float(challenger_weight), 0.0), 1.0)
    aligned_base, aligned_challenger = base.align(challenger, join="outer", axis=None)
    mixed = aligned_base.mul(1.0 - weight).add(aligned_challenger.mul(weight), fill_value=0.0)
    valid = aligned_base.notna() | aligned_challenger.notna()
    return _rank_frame(mixed.where(valid)).clip(0.0, 1.0)


def _rank_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True, method="average").where(frame.notna())



def _past_domain_return_technical_scores(
    forward: pd.DataFrame,
    maturities: pd.Series,
) -> dict[str, pd.DataFrame]:
    """Strictly past style-domain return factors used inside the technical dimension.

    For signal date T, only earlier signal rows whose holding period has already
    matured before T are allowed. This keeps the monthly style score causal while
    adding domain-level momentum/risk signals that stock-level aggregation can dilute.
    """
    if forward.empty:
        return {name: pd.DataFrame() for name in STYLE_RETURN_TECHNICAL_FIELDS}
    index = pd.DatetimeIndex(forward.index).sort_values()
    columns = list(forward.columns)
    maturity = pd.to_datetime(maturities, errors="coerce")
    raw = {
        name: pd.DataFrame(np.nan, index=index, columns=columns, dtype=float)
        for name in STYLE_RETURN_TECHNICAL_FIELDS
    }
    returns = forward.reindex(index).replace([np.inf, -np.inf], np.nan).astype(float)
    for date in index:
        mature_index = [
            idx for idx in index
            if idx < date and pd.notna(maturity.get(idx)) and maturity.get(idx) < date
        ]
        history = returns.loc[mature_index].dropna(how="all") if mature_index else pd.DataFrame(columns=columns)
        if history.empty:
            continue
        if len(history) >= 13:
            raw["domain_momentum_12_1"].loc[date] = (1.0 + history.iloc[-12:-1]).prod() - 1.0
        if len(history) >= 6:
            tail6 = history.tail(6)
            tail3 = history.tail(3)
            prev3 = history.iloc[-6:-3]
            raw["domain_momentum_6"].loc[date] = (1.0 + tail6).prod() - 1.0
            wealth = (1.0 + tail6).cumprod()
            drawdown = wealth.div(wealth.cummax()).sub(1.0)
            raw["domain_drawdown_resilience"].loc[date] = drawdown.min()
            denom = tail6.abs().sum().replace(0.0, np.nan)
            raw["domain_trend_efficiency"].loc[date] = tail6.sum().div(denom)
            raw["domain_momentum_acceleration_3_6"].loc[date] = ((1.0 + tail3).prod() - 1.0).sub((1.0 + prev3).prod() - 1.0)
            raw["domain_positive_rate_6"].loc[date] = tail6.gt(tail6.mean(axis=1), axis=0).mean()
            raw["domain_vol_adjusted_6"].loc[date] = tail6.mean().div(tail6.std(ddof=0).replace(0.0, np.nan))
        if len(history) >= 3:
            tail3 = history.tail(3)
            raw["domain_momentum_3"].loc[date] = (1.0 + tail3).prod() - 1.0
            wealth3 = (1.0 + tail3).cumprod()
            raw["domain_drawdown_repair_3"].loc[date] = wealth3.iloc[-1].div(wealth3.min().replace(0.0, np.nan)).sub(1.0)
        if len(history) >= 2:
            raw["domain_short_reversal"].loc[date] = -history.tail(1).iloc[0]
            relative_tail2 = history.tail(2).sub(history.tail(2).mean(axis=1), axis=0)
            raw["domain_relative_reversal_2"].loc[date] = -relative_tail2.sum()
        if len(history) >= 12:
            raw["domain_low_vol_12"].loc[date] = -history.tail(12).std(ddof=0)
    return {name: _rank_frame(frame) for name, frame in raw.items()}

def _rolling_time_percentile(series: pd.Series, window: int = 60) -> pd.Series:
    values = pd.Series(series, dtype=float).replace([np.inf, -np.inf], np.nan).sort_index()
    output = pd.Series(np.nan, index=values.index, dtype=float)
    for idx, date in enumerate(values.index):
        start = max(0, idx - int(window) + 1)
        sample = values.iloc[start: idx + 1].dropna()
        current = values.iloc[idx]
        if len(sample) < 12 or not math.isfinite(float(current)):
            continue
        output.iloc[idx] = float(sample.rank(pct=True, method="average").iloc[-1])
    return output.clip(0.0, 1.0)


def _style_regime_exposures(groups: Iterable[str]) -> pd.DataFrame:
    rows: dict[str, dict[str, float]] = {}
    for group in groups:
        name = str(group)
        if "小盘" in name:
            size = 1.00
        elif "中盘" in name:
            size = 0.25
        elif "大盘" in name:
            size = -0.75
        else:
            size = 0.0
        if "成长" in name:
            style = 1.00
        elif "均衡" in name:
            style = 0.10
        elif "价值" in name:
            style = -0.45
        elif "红利" in name:
            style = -0.90
        else:
            style = 0.0
        if name in {"成长", "均衡", "价值", "红利"}:
            size = 0.0
        rows[name] = {
            "risk_on": 0.45 * size + 0.55 * style,
            "defense": -(0.55 * size + 0.45 * style),
        }
    return pd.DataFrame(rows).T


def _market_regime_style_factors(
    returns: pd.DataFrame,
    signal_dates: pd.DatetimeIndex,
    groups: Iterable[str],
) -> dict[str, pd.DataFrame]:
    """Causal market-state style-fit factors.

    These are style factors, not industry prosperity. At month-end T they only use
    broad stock-market returns and breadth through T, then translate risk-on/risk-off
    state into size/style exposures. The IC and Top-Bottom gates still decide whether
    each factor is admitted.
    """
    group_list = [str(group) for group in groups]
    empty = {name: pd.DataFrame(np.nan, index=signal_dates, columns=group_list, dtype=float) for name in STYLE_REGIME_TECHNICAL_FIELDS}
    if returns.empty or not group_list:
        return empty
    market = returns.mean(axis=1, skipna=True).replace([np.inf, -np.inf], np.nan)
    market_nav = market.fillna(0.0).add(1.0).cumprod()
    breadth = returns.gt(0.0).where(returns.notna()).mean(axis=1).rolling(60, min_periods=30).mean()
    mom6 = market_nav.div(market_nav.shift(126)).sub(1.0)
    mom3 = market_nav.div(market_nav.shift(63)).sub(1.0)
    vol3 = market.rolling(63, min_periods=30).std(ddof=0)
    drawdown = market_nav.div(market_nav.rolling(126, min_periods=63).max().replace(0.0, np.nan)).sub(1.0)
    signal_dates = pd.DatetimeIndex(signal_dates)
    mom6_m = _rolling_time_percentile(mom6.reindex(signal_dates), 60)
    mom3_m = _rolling_time_percentile(mom3.reindex(signal_dates), 60)
    breadth_m = _rolling_time_percentile(breadth.reindex(signal_dates), 60)
    low_vol_m = 1.0 - _rolling_time_percentile(vol3.reindex(signal_dates), 60)
    drawdown_m = _rolling_time_percentile(drawdown.reindex(signal_dates), 60)
    risk_on = (0.32 * mom6_m + 0.23 * mom3_m + 0.25 * breadth_m + 0.12 * low_vol_m + 0.08 * drawdown_m).clip(0.0, 1.0)
    breadth_state = breadth_m.clip(0.0, 1.0)
    defense_state = (1.0 - low_vol_m).clip(0.0, 1.0)
    exposure = _style_regime_exposures(group_list).reindex(group_list).fillna(0.0)
    outputs = {name: pd.DataFrame(np.nan, index=signal_dates, columns=group_list, dtype=float) for name in STYLE_REGIME_TECHNICAL_FIELDS}
    for date in signal_dates:
        if pd.notna(risk_on.get(date)):
            outputs["market_regime_style_fit"].loc[date] = ((2.0 * float(risk_on.loc[date]) - 1.0) * exposure["risk_on"]).rank(pct=True, method="average")
        if pd.notna(breadth_state.get(date)):
            outputs["breadth_regime_style_fit"].loc[date] = ((2.0 * float(breadth_state.loc[date]) - 1.0) * exposure["risk_on"]).rank(pct=True, method="average")
        if pd.notna(defense_state.get(date)):
            outputs["volatility_defense_style_fit"].loc[date] = (float(defense_state.loc[date]) * exposure["defense"]).rank(pct=True, method="average")
    return outputs


def _forward_group_returns(
    labels: pd.DataFrame,
    returns: pd.DataFrame,
    label_column: str,
    groups: Iterable[str],
    execution_dates: dict[pd.Timestamp, pd.Timestamp],
    signal_dates: Iterable[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.Series]:
    group_list = list(groups)
    signals = [pd.Timestamp(date) for date in signal_dates if pd.Timestamp(date) in execution_dates]
    available_dates = set(pd.to_datetime(labels["trade_date"]).unique())
    signals = [date for date in signals if date in available_dates]
    future = pd.DataFrame(np.nan, index=pd.DatetimeIndex(signals), columns=group_list, dtype=float)
    maturities: dict[pd.Timestamp, pd.Timestamp] = {}
    for index, signal in enumerate(signals[:-1]):
        execution = execution_dates[signal]
        next_execution = execution_dates[signals[index + 1]]
        group_daily_returns = _period_group_daily_returns(
            labels,
            returns,
            signal,
            execution,
            next_execution,
            label_column,
            group_list,
        )
        if group_daily_returns.empty:
            continue
        maturities[signal] = pd.Timestamp(next_execution)
        compound = group_daily_returns.add(1.0).prod(min_count=1).sub(1.0)
        for group in group_list:
            value = compound.get(group, np.nan)
            if pd.notna(value) and math.isfinite(float(value)):
                future.at[signal, group] = float(value)
    return future, pd.Series(maturities, dtype="datetime64[ns]").sort_index()


def _row_spearman(score: pd.DataFrame, forward: pd.DataFrame) -> pd.Series:
    result: dict[pd.Timestamp, float] = {}
    common = pd.DatetimeIndex(score.index).intersection(forward.index)
    minimum = 3 if len(score.columns) <= 4 else 5
    for date in common:
        sample = pd.concat(
            [score.loc[date].rename("score"), forward.loc[date].rename("return")],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < minimum or sample["score"].nunique() < 2 or sample["return"].nunique() < 2:
            continue
        result[pd.Timestamp(date)] = float(sample["score"].rank().corr(sample["return"].rank()))
    return pd.Series(result, dtype=float).sort_index()


def _row_top_bottom_spread(score: pd.DataFrame, forward: pd.DataFrame, direction: float = 1.0) -> pd.Series:
    """逐月计算单因子的下月Top-Bottom收益。

    只用信号日分数和随后已实现收益做检验；direction由训练集RankIC确定，
    因而不会用测试期回报反向修正因子。
    """
    result: dict[pd.Timestamp, float] = {}
    common = pd.DatetimeIndex(score.index).intersection(forward.index)
    for date in common:
        sample = pd.concat(
            [score.loc[date].rename("score"), forward.loc[date].rename("return")],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < 3 or sample["score"].nunique() < 2:
            continue
        sample["effective_score"] = sample["score"].astype(float).mul(float(direction))
        sample = sample.sort_values("effective_score", ascending=False, kind="stable")
        group_count = len(sample)
        bucket = 1 if group_count <= 4 else max(1, min(3, group_count // 4))
        top = float(sample.head(bucket)["return"].mean())
        bottom = float(sample.tail(bucket)["return"].mean())
        if math.isfinite(top) and math.isfinite(bottom):
            result[pd.Timestamp(date)] = top - bottom
    return pd.Series(result, dtype=float).sort_index()


def _positive_series_quality(history: pd.Series) -> float:
    sample = pd.Series(history, dtype=float).dropna()
    if len(sample) < 6:
        return 0.0
    std = float(sample.std(ddof=1))
    if not math.isfinite(std) or std <= 0.0:
        return 0.0
    score = float(sample.mean() / std * math.sqrt(12.0))
    hit = float(sample.gt(0.0).mean())
    sample_bonus = math.sqrt(len(sample) / (len(sample) + 12.0))
    quality = max(0.0, score) * max(0.0, (hit - 0.45) / 0.35) * sample_bonus
    return min(3.0, quality)


def _spread_summary(spread: pd.Series, maturities: pd.Series, split: str) -> dict[str, Any]:
    sample = _split_ic_sample(spread, maturities, split).dropna()
    std = float(sample.std(ddof=1)) if len(sample) > 1 else math.nan
    mean = float(sample.mean()) if len(sample) else math.nan
    return {
        "observations": int(len(sample)),
        "mean_monthly_spread": _finite(mean) if len(sample) else None,
        "annualized_spread": _finite(mean * 12.0) if len(sample) else None,
        "spread_t": _finite(float(mean / (std / math.sqrt(len(sample))))) if len(sample) > 1 and std > 0 else None,
        "positive_rate": _finite(float(sample.gt(0.0).mean())) if len(sample) else None,
    }


def _split_ic_sample(ic: pd.Series, maturities: pd.Series, split: str) -> pd.Series:
    start, end = SPLITS[split]
    signals = pd.Series(pd.DatetimeIndex(ic.index), index=ic.index)
    maturity = pd.to_datetime(maturities.reindex(ic.index), errors="coerce")
    mask = signals.ge(pd.Timestamp(start)) & signals.le(pd.Timestamp(end)) & maturity.notna() & maturity.le(pd.Timestamp(end))
    return ic.loc[mask].dropna()


def _ic_summary(ic: pd.Series, maturities: pd.Series, split: str, sign: float = 1.0) -> dict[str, Any]:
    sample = _split_ic_sample(ic, maturities, split).mul(sign).dropna()
    std = float(sample.std(ddof=1)) if len(sample) > 1 else math.nan
    return {
        "observations": int(len(sample)),
        "mean_ic": _finite(float(sample.mean())) if len(sample) else None,
        "icir": _finite(float(sample.mean() / std * math.sqrt(12.0))) if len(sample) > 1 and std > 0 else None,
        "positive_rate": _finite(float(sample.gt(0.0).mean())) if len(sample) else None,
    }


def _history_quality(history: pd.Series, bad_signal: bool = False) -> tuple[float, float]:
    sample = pd.Series(history, dtype=float).dropna()
    if len(sample) < 6:
        return 1.0, 0.0
    direction = -1.0 if bad_signal else (1.0 if float(sample.mean()) >= 0.0 else -1.0)
    oriented = sample.mul(direction).dropna()
    if len(oriented) < 6:
        return direction, 0.0
    std = float(oriented.std(ddof=1))
    if not math.isfinite(std) or std <= 0.0:
        return direction, 0.0
    icir = float(oriented.mean() / std * math.sqrt(12.0))
    hit = float(oriented.gt(0.0).mean())
    sample_bonus = math.sqrt(len(oriented) / (len(oriented) + 12.0))
    quality = max(0.0, icir) * max(0.0, (hit - 0.45) / 0.35) * sample_bonus
    return direction, min(3.0, quality)


def _factor_profile(
    score: pd.DataFrame,
    forward: pd.DataFrame,
    maturities: pd.Series,
    bad_signal: bool,
) -> dict[str, Any]:
    ic = _row_spearman(score, forward)
    train_raw = _split_ic_sample(ic, maturities, "train")
    validation_raw = _split_ic_sample(ic, maturities, "validation")
    direction = -1.0 if bad_signal else (1.0 if train_raw.mean() >= 0.0 or len(train_raw) == 0 else -1.0)
    train = train_raw.mul(direction).dropna()
    validation = validation_raw.mul(direction).dropna()
    spread = _row_top_bottom_spread(score, forward, direction=direction)
    train_spread = _split_ic_sample(spread, maturities, "train")
    validation_spread = _split_ic_sample(spread, maturities, "validation")
    calibration_ic = pd.concat([train, validation]).dropna()
    calibration_spread = pd.concat([train_spread, validation_spread]).dropna()
    group_count = int(score.shape[1])
    valid_need = 6 if group_count <= 4 else 12
    train_need = 12 if group_count <= 4 else 18
    train_mean = float(train.mean()) if len(train) else math.nan
    validation_mean = float(validation.mean()) if len(validation) else math.nan
    validation_hit = float(validation.gt(0.0).mean()) if len(validation) else math.nan
    train_spread_mean = float(train_spread.mean()) if len(train_spread) else math.nan
    validation_spread_mean = float(validation_spread.mean()) if len(validation_spread) else math.nan
    validation_spread_hit = float(validation_spread.gt(0.0).mean()) if len(validation_spread) else math.nan
    ic_ok = bool(
        len(train) >= train_need
        and len(validation) >= valid_need
        and (not math.isfinite(train_mean) or train_mean >= (-0.015 if group_count <= 4 else -0.005))
        and math.isfinite(validation_mean)
        and validation_mean >= (-0.010 if group_count <= 4 else 0.0)
        and (not math.isfinite(validation_hit) or validation_hit >= (0.40 if group_count <= 4 else 0.47))
    )
    train_spread_floor = -0.003 if group_count <= 3 else (-0.002 if group_count <= 4 else -0.001)
    validation_spread_floor = -0.001 if group_count <= 3 else (0.0 if group_count <= 4 else 0.001)
    validation_hit_floor = 0.43 if group_count <= 3 else (0.45 if group_count <= 4 else 0.50)
    calibration_spread_mean = float(calibration_spread.mean()) if len(calibration_spread) else math.nan
    calibration_spread_hit = float(calibration_spread.gt(0.0).mean()) if len(calibration_spread) else math.nan
    spread_ok = bool(
        len(train_spread) >= train_need
        and len(validation_spread) >= valid_need
        and math.isfinite(train_spread_mean)
        and train_spread_mean >= train_spread_floor
        and math.isfinite(validation_spread_mean)
        and validation_spread_mean >= validation_spread_floor
        and math.isfinite(calibration_spread_mean)
        and calibration_spread_mean > 0.0
        and math.isfinite(calibration_spread_hit)
        and calibration_spread_hit >= 0.45
        and (not math.isfinite(validation_spread_hit) or validation_spread_hit >= validation_hit_floor)
    )
    # 即使大中小只有3个横截面，也不能只凭RankIC放行：必须证明信号端Top-Bottom
    # 对下一月收益有正向贡献，否则容易出现空头篮子反而上涨、五因子合成被噪声污染。
    direction_ok = bool(ic_ok and spread_ok)
    static_quality = 0.65 * _positive_series_quality(calibration_ic) + 0.35 * _positive_series_quality(calibration_spread)
    if not direction_ok:
        static_quality = 0.0
    return {
        "ic": ic,
        "spread": spread,
        "direction": direction,
        "admitted": direction_ok,
        "ic_gate": ic_ok,
        "spread_gate": spread_ok,
        "static_quality": static_quality,
        "coverage": _finite(float(score.notna().mean().mean())) if not score.empty else None,
        "train": _ic_summary(ic, maturities, "train", direction),
        "validation": _ic_summary(ic, maturities, "validation", direction),
        "test": _ic_summary(ic, maturities, "test", direction),
        "train_spread": _spread_summary(spread, maturities, "train"),
        "validation_spread": _spread_summary(spread, maturities, "validation"),
        "test_spread_report_only": _spread_summary(spread, maturities, "test"),
    }


def _combine_weighted_factor_rows(frames: list[pd.DataFrame], weights: list[pd.Series]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    zero = pd.DataFrame(0.0, index=frames[0].index, columns=frames[0].columns)
    numerator = zero.copy()
    denominator = zero.copy()
    for frame, weight in zip(frames, weights):
        local_weight = pd.to_numeric(weight, errors="coerce").fillna(0.0).clip(lower=0.0)
        numerator = numerator.add(frame.fillna(0.0).mul(local_weight, axis=0), fill_value=0.0)
        denominator = denominator.add(frame.notna().astype(float).mul(local_weight, axis=0), fill_value=0.0)
    combined = numerator.div(denominator.replace(0.0, np.nan))
    return _rank_frame(combined)


def _dimension_factor_cap(dimension: str, group_count: int) -> int:
    del dimension, group_count
    # 强制只保留每维前几名在本地完整回测中降低了12风格箱和四风格收益。
    # 正式版保留双门禁+质量加权，不再额外截断已通过检验的有效子因子。
    return 10_000


def _validated_atomic_dimensions(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    maturities: pd.Series,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    validated: dict[str, pd.DataFrame] = {}
    diagnostics: list[dict[str, Any]] = []
    dimension_quality: dict[str, float] = {}
    dimension_valid: dict[str, bool] = {}
    for dimension, factors in factor_scores.items():
        fallback = dimensions[dimension]
        neutral = pd.DataFrame(0.5, index=fallback.index, columns=fallback.columns, dtype=float)
        group_count = int(fallback.shape[1])
        cap = _dimension_factor_cap(dimension, group_count)
        dimension_row_start = len(diagnostics)
        admitted_records: list[tuple[float, str, pd.DataFrame]] = []
        for factor, score in factors.items():
            bad_signal = dimension == "crowding"
            profile = _factor_profile(score, forward, maturities, bad_signal=bad_signal)
            quality = max(0.0, float(profile.get("static_quality") or 0.0))
            if profile["admitted"]:
                if bad_signal:
                    signed = score.copy()
                else:
                    signed = score.copy() if float(profile["direction"]) >= 0.0 else 1.0 - score
                admitted_records.append((quality, factor, signed))
            diagnostics.append({
                "dimension": dimension,
                "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
                "factor": factor,
                "passed_gate": bool(profile["admitted"]),
                "admitted": bool(profile["admitted"]),
                "direction": "反向" if profile["direction"] < 0 else "正向",
                "static_quality": _finite(profile["static_quality"]),
                "coverage": profile["coverage"],
                "ic_gate": bool(profile.get("ic_gate")),
                "spread_gate": bool(profile.get("spread_gate")),
                "train": profile["train"],
                "validation": profile["validation"],
                "test_report_only": profile["test"],
                "train_spread": profile.get("train_spread"),
                "validation_spread": profile.get("validation_spread"),
                "test_spread_report_only": profile.get("test_spread_report_only"),
                "dimension_factor_cap": int(cap),
            })
        admitted_records.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chosen = admitted_records[:cap]
        chosen_factors = {factor for _, factor, _ in chosen}
        quality_rank = {factor: rank for rank, (_, factor, _) in enumerate(chosen, start=1)}
        for row in diagnostics[dimension_row_start:]:
            if row.get("passed_gate") and row.get("factor") not in chosen_factors:
                row["admitted"] = False
                row["dropped_reason"] = "通过基础门禁但维度内质量排名低于保留上限，未进入一级因子合成。"
            elif row.get("passed_gate"):
                row["used_quality_rank"] = quality_rank.get(row.get("factor"))
        if chosen:
            signed_frames = [frame for _, _, frame in chosen]
            weight_frames = [pd.Series(max(quality, 1e-6), index=frame.index, dtype=float) for quality, _, frame in chosen]
            combined = _combine_weighted_factor_rows(signed_frames, weight_frames).reindex_like(fallback)
            validated[dimension] = combined.combine_first(neutral).reindex_like(fallback)
            dimension_quality[dimension] = float(np.mean([quality for quality, _, _ in chosen]))
            dimension_valid[dimension] = True
        else:
            validated[dimension] = neutral
            dimension_quality[dimension] = 0.0
            dimension_valid[dimension] = False
    effective = {
        dimension: int(sum(1 for row in diagnostics if row["dimension"] == dimension and row["admitted"]))
        for dimension in DIMENSION_LABELS
    }
    return validated, {
        "atomic_factors": diagnostics,
        "admitted_factor_count": effective,
        "dimension_quality": {key: _finite(value) for key, value in dimension_quality.items()},
        "dimension_valid": dimension_valid,
        "dimension_factor_caps": {key: _dimension_factor_cap(key, next(iter(dimensions.values())).shape[1]) for key in DIMENSION_LABELS if key in dimensions},
    }

def _rolling_atomic_dimensions(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    base_dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    maturities: pd.Series,
    lookback: int = 36,
    minimum_history: int | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Past-only rolling secondary-factor admission inside the five style dimensions.

    Static train/validation gates can keep a factor after its regime changes. This
    variant recalculates factor direction and weight at each signal date using only
    prior signal rows whose next-month holding period has already matured. It keeps
    the same five primary dimensions, but asks every secondary factor to prove a
    positive rolling RankIC and Top-Bottom spread before it contributes.
    """
    if not base_dimensions:
        return {}, {"status": "empty_base_dimensions"}
    first = next(iter(base_dimensions.values()))
    maturity = pd.to_datetime(maturities, errors="coerce")
    min_history = int(minimum_history or (10 if len(first.columns) <= 4 else 14))
    validated: dict[str, pd.DataFrame] = {}
    diagnostics: dict[str, Any] = {"lookback": int(lookback), "minimum_history": int(min_history), "factors": []}
    dimension_quality: dict[str, float] = {}
    admitted_factor_count: dict[str, int] = {}
    for dimension, factors in factor_scores.items():
        fallback = base_dimensions[dimension]
        neutral = pd.DataFrame(0.5, index=fallback.index, columns=fallback.columns, dtype=float)
        signed_frames: list[pd.DataFrame] = []
        weight_frames: list[pd.Series] = []
        dim_quality_values: list[float] = []
        bad_signal = dimension == "crowding"
        for factor, score in factors.items():
            if score.empty:
                continue
            ic = _row_spearman(score, forward)
            spread_positive = _row_top_bottom_spread(score, forward, direction=1.0)
            spread_negative = _row_top_bottom_spread(score, forward, direction=-1.0)
            signed = score.copy()
            dynamic_weight = pd.Series(0.0, index=score.index, dtype=float)
            selected_direction = pd.Series(np.nan, index=score.index, dtype=float)
            for date in score.index:
                mature_index = [
                    idx for idx in ic.index
                    if idx < date and pd.notna(maturity.get(idx)) and maturity.get(idx) < date
                ]
                if not mature_index:
                    continue
                mature_index = mature_index[-int(lookback):]
                history_ic_raw = ic.loc[mature_index].dropna()
                if len(history_ic_raw) < min_history:
                    continue
                direction = -1.0 if bad_signal else (1.0 if float(history_ic_raw.mean()) >= 0.0 else -1.0)
                spread_source = spread_negative if direction < 0.0 else spread_positive
                history_spread = spread_source.reindex(mature_index).dropna()
                if len(history_spread) < max(6, min_history // 2):
                    continue
                oriented_ic = history_ic_raw.mul(direction).dropna()
                mean_ic = float(oriented_ic.mean()) if len(oriented_ic) else math.nan
                mean_spread = float(history_spread.mean()) if len(history_spread) else math.nan
                spread_hit = float(history_spread.gt(0.0).mean()) if len(history_spread) else math.nan
                hit_need = 0.43 if len(score.columns) <= 4 else 0.47
                if not (math.isfinite(mean_ic) and math.isfinite(mean_spread) and math.isfinite(spread_hit)):
                    continue
                if mean_ic < -0.005 or mean_spread <= 0.0 or spread_hit < hit_need:
                    continue
                ic_quality = _positive_series_quality(oriented_ic)
                spread_quality = _positive_series_quality(history_spread)
                annual_spread = mean_spread * 12.0
                quality = 0.55 * ic_quality + 0.35 * spread_quality + 0.10 * max(0.0, annual_spread)
                if quality <= 0.0:
                    continue
                dynamic_weight.loc[date] = min(3.0, max(0.02, quality))
                selected_direction.loc[date] = direction
                if not bad_signal and direction < 0.0:
                    signed.loc[date] = 1.0 - score.loc[date]
            active = dynamic_weight.gt(0.0)
            if bool(active.any()):
                signed_frames.append(signed)
                weight_frames.append(dynamic_weight)
                avg_quality = float(dynamic_weight.loc[active].mean())
                dim_quality_values.append(avg_quality)
                diagnostics["factors"].append({
                    "dimension": dimension,
                    "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
                    "factor": factor,
                    "rolling_admitted_months": int(active.sum()),
                    "average_quality": _finite(avg_quality),
                    "positive_direction_months": int(selected_direction.eq(1.0).sum()),
                    "negative_direction_months": int(selected_direction.eq(-1.0).sum()),
                })
        if signed_frames:
            combined = _combine_weighted_factor_rows(signed_frames, weight_frames).reindex_like(fallback)
            validated[dimension] = combined.combine_first(neutral).reindex_like(fallback)
            admitted_factor_count[dimension] = len(signed_frames)
            dimension_quality[dimension] = float(np.mean(dim_quality_values)) if dim_quality_values else 0.0
        else:
            validated[dimension] = neutral
            admitted_factor_count[dimension] = 0
            dimension_quality[dimension] = 0.0
    diagnostics["admitted_factor_count"] = admitted_factor_count
    diagnostics["dimension_quality"] = {key: _finite(value) for key, value in dimension_quality.items()}
    return validated, diagnostics

def _weighted_dimension_mean(items: list[tuple[pd.DataFrame, float]], minimum: int) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    zero = pd.DataFrame(0.0, index=items[0][0].index, columns=items[0][0].columns)
    numerator = zero.copy()
    denominator = zero.copy()
    count = zero.copy()
    for frame, weight in items:
        numeric = frame.astype(float)
        numerator = numerator.add(numeric.fillna(0.0).mul(float(weight)), fill_value=0.0)
        denominator = denominator.add(numeric.notna().astype(float).mul(float(weight)), fill_value=0.0)
        count = count.add(numeric.notna().astype(float), fill_value=0.0)
    return numerator.div(denominator.replace(0.0, np.nan)).where(count.ge(minimum))


def _online_dimension_score(
    dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    maturities: pd.Series,
    base_weights: dict[str, float],
    minimum: int,
    lookback: int = 36,
    minimum_history: int = 9,
) -> pd.DataFrame:
    """只使用已成熟标签的滚动IC维度加权。

    每个信号日只能读取该日以前已完成持有期的IC样本。样本不足时回到预设权重，
    样本充足后用ICIR、命中率和样本数压缩权重，避免单一维度在验证期被偶然放大。
    """
    if not dimensions:
        return pd.DataFrame()
    first = next(iter(dimensions.values()))
    numerator = pd.DataFrame(0.0, index=first.index, columns=first.columns)
    denominator = pd.DataFrame(0.0, index=first.index, columns=first.columns)
    count = pd.DataFrame(0.0, index=first.index, columns=first.columns)
    maturity = pd.to_datetime(maturities, errors="coerce")
    for name, frame in dimensions.items():
        base = float(base_weights.get(name, 0.0))
        if base <= 0.0 or frame.empty:
            continue
        bad_signal = name == "crowding"
        ic = _row_spearman(frame, forward)
        signed = frame.copy()
        dynamic_weight = pd.Series(base, index=frame.index, dtype=float)
        for date in frame.index:
            mature_index = [idx for idx in ic.index if idx < date and pd.notna(maturity.get(idx)) and maturity.get(idx) < date]
            history = ic.loc[mature_index].tail(lookback) if mature_index else pd.Series(dtype=float)
            if len(history) >= minimum_history:
                direction, quality = _history_quality(history, bad_signal=bad_signal)
                signed.loc[date] = frame.loc[date] if direction >= 0.0 else 1.0 - frame.loc[date]
                dynamic_weight.loc[date] = base * (0.35 + min(2.0, quality))
            elif bad_signal:
                signed.loc[date] = 1.0 - frame.loc[date]
        numeric = signed.astype(float)
        numerator = numerator.add(numeric.fillna(0.0).mul(dynamic_weight, axis=0), fill_value=0.0)
        denominator = denominator.add(numeric.notna().astype(float).mul(dynamic_weight, axis=0), fill_value=0.0)
        count = count.add(numeric.notna().astype(float), fill_value=0.0)
    combined = numerator.div(denominator.replace(0.0, np.nan)).where(count.ge(minimum))
    return _rank_frame(combined)

def _soft_threshold(value: float, penalty: float) -> float:
    if value > penalty:
        return value - penalty
    if value < -penalty:
        return value + penalty
    return 0.0


def _rolling_linear_dimension_score(
    dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    maturities: pd.Series,
    method: str,
    lookback: int = 48,
    minimum_months: int = 18,
    alpha: float = 0.025,
) -> pd.DataFrame:
    """Rolling OLS/Lasso score using only matured historical labels before signal date."""
    names = list(STYLE_DIMENSIONS)
    available = [name for name in names if name in dimensions and not dimensions[name].empty]
    if not available:
        return pd.DataFrame()
    first = dimensions[available[0]]
    output = pd.DataFrame(np.nan, index=first.index, columns=first.columns, dtype=float)
    maturity = pd.to_datetime(maturities, errors="coerce")
    oriented = {
        name: (1.0 - dimensions[name] if name == "crowding" else dimensions[name]).astype(float)
        for name in available
    }
    fallback = _rank_frame(
        _weighted_dimension_mean([(oriented[name], 1.0) for name in available], max(3, min(4, len(available))))
    )
    month_index = first.index
    group_index = first.columns
    stacked_index = pd.MultiIndex.from_product([month_index, group_index], names=["date", "group"])
    feature_long = pd.DataFrame(
        {
            name: oriented[name]
            .reindex(index=month_index, columns=group_index)
            .to_numpy(dtype=float)
            .reshape(-1)
            for name in available
        },
        index=stacked_index,
    )
    forward_long = pd.Series(
        forward.reindex(index=month_index, columns=group_index).to_numpy(dtype=float).reshape(-1),
        index=stacked_index,
        name="future",
    )
    loc = pd.IndexSlice
    for current in first.index:
        mature_index = [idx for idx in month_index if idx < current and pd.notna(maturity.get(idx)) and maturity.get(idx) < current]
        mature_index = mature_index[-lookback:]
        if len(mature_index) < minimum_months:
            if current in fallback.index:
                output.loc[current] = fallback.loc[current]
            continue
        train = pd.concat(
            [
                feature_long.loc[loc[mature_index, :], available],
                forward_long.loc[loc[mature_index, :]],
            ],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        if len(train) < max(60, len(available) * 8):
            if current in fallback.index:
                output.loc[current] = fallback.loc[current]
            continue
        x = train[available].astype(float)
        yv = train["future"].astype(float)
        x_mean = x.mean(axis=0)
        x_std = x.std(axis=0, ddof=0).replace(0.0, np.nan)
        xs = (x - x_mean).div(x_std, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        yc = yv - yv.mean()
        try:
            if method.lower() == "lasso":
                beta = np.zeros(len(available), dtype=float)
                xmat = xs.to_numpy(dtype=float)
                yvec = yc.to_numpy(dtype=float)
                denom = np.sum(xmat * xmat, axis=0) / max(1, len(yvec)) + 1e-12
                for _ in range(80):
                    for j in range(len(available)):
                        residual = yvec - xmat @ beta + xmat[:, j] * beta[j]
                        rho = float(np.dot(xmat[:, j], residual) / max(1, len(yvec)))
                        beta[j] = _soft_threshold(rho, alpha) / denom[j]
            else:
                xmat = np.column_stack([np.ones(len(xs)), xs.to_numpy(dtype=float)])
                beta = np.linalg.lstsq(xmat, yc.to_numpy(dtype=float), rcond=None)[0][1:]
        except Exception:
            if current in fallback.index:
                output.loc[current] = fallback.loc[current]
            continue
        current_frame = pd.concat([oriented[name].loc[current].rename(name) for name in available], axis=1)
        current_x = current_frame.astype(float).sub(x_mean, axis=1).div(x_std, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        pred = pd.Series(current_x.to_numpy(dtype=float) @ beta, index=current_x.index)
        if pred.replace([np.inf, -np.inf], np.nan).dropna().nunique() <= 1:
            if current in fallback.index:
                output.loc[current] = fallback.loc[current]
        else:
            output.loc[current] = pred.rank(pct=True, method="average")
    return output.ffill()



def _proxy_monthly_stats(values: list[float]) -> dict[str, float]:
    series = pd.Series(values, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(series) < 6:
        return {"annual_return": np.nan, "sharpe": np.nan}
    annual = float((1.0 + series).prod() ** (12.0 / len(series)) - 1.0)
    std = float(series.std(ddof=0))
    sharpe = float(series.mean() / std * math.sqrt(12.0)) if std > 0.0 else np.nan
    return {"annual_return": annual, "sharpe": sharpe}


def _train_validation_grid_dimension_score(
    dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    top_n: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Choose a compact pre-registered five-factor weight pool with train/validation only."""
    weight_pool = [
        ("等权", {"fundamental": 0.20, "technical": 0.20, "valuation": 0.20, "funds": 0.20, "crowding": 0.20}, 0.10),
        ("稳健均衡", {"fundamental": 0.22, "technical": 0.24, "valuation": 0.18, "funds": 0.16, "crowding": 0.20}, 0.12),
        ("技术资金", {"fundamental": 0.16, "technical": 0.38, "valuation": 0.08, "funds": 0.24, "crowding": 0.14}, 0.12),
        ("技术低拥挤", {"fundamental": 0.14, "technical": 0.36, "valuation": 0.10, "funds": 0.14, "crowding": 0.26}, 0.14),
        ("质量估值", {"fundamental": 0.34, "technical": 0.12, "valuation": 0.28, "funds": 0.08, "crowding": 0.18}, 0.10),
        ("质量低拥挤", {"fundamental": 0.30, "technical": 0.14, "valuation": 0.20, "funds": 0.08, "crowding": 0.28}, 0.12),
        ("价值红利防守", {"fundamental": 0.22, "technical": 0.08, "valuation": 0.38, "funds": 0.06, "crowding": 0.26}, 0.10),
        ("资金确认", {"fundamental": 0.16, "technical": 0.22, "valuation": 0.12, "funds": 0.34, "crowding": 0.16}, 0.12),
        ("趋势质量", {"fundamental": 0.28, "technical": 0.32, "valuation": 0.12, "funds": 0.12, "crowding": 0.16}, 0.10),
        ("低拥挤核心", {"fundamental": 0.20, "technical": 0.16, "valuation": 0.16, "funds": 0.10, "crowding": 0.38}, 0.08),
        ("价值趋势", {"fundamental": 0.18, "technical": 0.30, "valuation": 0.26, "funds": 0.10, "crowding": 0.16}, 0.10),
        ("质量资金", {"fundamental": 0.30, "technical": 0.16, "valuation": 0.12, "funds": 0.26, "crowding": 0.16}, 0.12),
    ]
    best_score = -np.inf
    best_frame = _composite_score(dimensions)
    best_detail: dict[str, Any] = {}
    dates = pd.DatetimeIndex(forward.index)
    top_n = max(1, int(top_n))
    for label, weights, penalty in weight_pool:
        candidate = _dimension_weight_score(
            dimensions,
            weights,
            crowding_penalty=penalty,
            minimum_dimensions=3,
        )
        strategy_returns: dict[str, list[float]] = {"train": [], "validation": []}
        excess_returns: dict[str, list[float]] = {"train": [], "validation": []}
        for date in dates:
            if date not in candidate.index or date not in forward.index:
                continue
            split = "train" if date <= pd.Timestamp(SPLITS["train"][1]) else "validation" if date <= pd.Timestamp(SPLITS["validation"][1]) else "test"
            if split == "test":
                continue
            score_row = candidate.loc[date].dropna()
            return_row = forward.loc[date].dropna()
            common = score_row.index.intersection(return_row.index)
            if len(common) < max(2, top_n):
                continue
            selected = list(score_row.loc[common].sort_values(ascending=False, kind="stable").head(top_n).index)
            strategy = float(return_row.loc[selected].mean())
            benchmark = float(return_row.loc[common].mean())
            strategy_returns[split].append(strategy)
            excess_returns[split].append(strategy - benchmark)
        train = _proxy_monthly_stats(strategy_returns["train"])
        validation = _proxy_monthly_stats(strategy_returns["validation"])
        train_excess = _proxy_monthly_stats(excess_returns["train"])
        validation_excess = _proxy_monthly_stats(excess_returns["validation"])
        train_sharpe = float(train_excess.get("sharpe", np.nan))
        validation_sharpe = float(validation_excess.get("sharpe", np.nan))
        train_annual = float(train_excess.get("annual_return", np.nan))
        validation_annual = float(validation_excess.get("annual_return", np.nan))
        if not all(math.isfinite(value) for value in [train_sharpe, validation_sharpe, train_annual, validation_annual]):
            continue
        objective = min(train_sharpe, validation_sharpe) + 0.45 * min(train_annual, validation_annual) - 0.15 * abs(train_sharpe - validation_sharpe)
        if min(train_annual, validation_annual) <= 0.0:
            objective -= 0.20
        if objective > best_score:
            best_score = objective
            best_frame = candidate
            best_detail = {
                "selected_preset": label,
                "objective": _finite(objective),
                "weights": {key: _finite(value) for key, value in weights.items()},
                "crowding_penalty": _finite(penalty),
                "train_strategy": {key: _finite(value) for key, value in train.items()},
                "validation_strategy": {key: _finite(value) for key, value in validation.items()},
                "train_excess": {key: _finite(value) for key, value in train_excess.items()},
                "validation_excess": {key: _finite(value) for key, value in validation_excess.items()},
                "tested_presets": len(weight_pool),
                "top_n": top_n,
                "policy": "仅使用训练集和验证集的月频下月收益代理选择预注册权重池；2022年后测试期完全不参与。",
            }
    return best_frame, best_detail


def _validated_candidate_scores(
    dimensions: dict[str, pd.DataFrame],
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    forward: pd.DataFrame,
    maturities: pd.Series,
    top_n: int = 3,
    include_return_technical: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    enhanced_factor_scores = {dimension: dict(factors) for dimension, factors in factor_scores.items()}
    return_technical: dict[str, pd.DataFrame] = {}
    if include_return_technical:
        return_technical = _past_domain_return_technical_scores(forward, maturities)
        enhanced_factor_scores.setdefault("technical", {}).update(return_technical)
    core_factor_scores: dict[str, dict[str, pd.DataFrame]] = {}
    for dimension, factors in enhanced_factor_scores.items():
        allowed = set(CORE_FIELDS_BY_DIMENSION.get(dimension, []))
        if dimension == "technical":
            allowed.update(CORE_STYLE_RETURN_TECHNICAL_FIELDS)
        core_factor_scores[dimension] = {name: frame for name, frame in factors.items() if name in allowed}
    core_validated, core_diagnostics = _validated_atomic_dimensions(core_factor_scores, dimensions, forward, maturities)
    validated, diagnostics = _validated_atomic_dimensions(enhanced_factor_scores, dimensions, forward, maturities)
    diagnostics["core_atomic"] = core_diagnostics
    if ENABLE_ROLLING_ATOMIC_CANDIDATES:
        rolling_validated, rolling_atomic_diagnostics = _rolling_atomic_dimensions(
            enhanced_factor_scores,
            dimensions,
            forward,
            maturities,
        )
    else:
        rolling_validated = {}
        rolling_atomic_diagnostics = {
            "status": "disabled_for_default_production",
            "reason": "逐月二级因子滚动筛选在当前风格域样本上耗时高且易引入短窗噪声；正式候选使用静态训练/验证准入、一级滚动RankIC、OLS/Lasso和核心保护候选。",
        }
    diagnostics["rolling_atomic"] = rolling_atomic_diagnostics
    diagnostics["domain_return_technical_factor_count"] = int(sum(not frame.empty for frame in return_technical.values()))
    crowd = validated["crowding"].clip(0.0, 1.0)
    low_crowding = _rank_frame(1.0 - crowd)
    dimension_quality = diagnostics.get("dimension_quality", {})

    核心等权五因子 = _composite_score(core_validated)
    核心RankIC五因子 = _online_dimension_score(
        {name: core_validated[name] for name in STYLE_DIMENSIONS},
        forward,
        maturities,
        {"fundamental": 0.24, "technical": 0.28, "valuation": 0.16, "funds": 0.16, "crowding": 0.16},
        3,
    )
    核心低拥挤五因子 = _rank_frame(
        _weighted_dimension_mean(
            [
                (core_validated["technical"], 0.24),
                (core_validated["fundamental"], 0.24),
                (core_validated["funds"], 0.14),
                (core_validated["valuation"], 0.14),
                (_rank_frame(1.0 - core_validated["crowding"].clip(0.0, 1.0)), 0.24),
            ],
            3,
        ).sub(core_validated["crowding"].clip(0.0, 1.0).pow(2).mul(0.08))
    )
    等权五因子 = _composite_score(validated)
    if ENABLE_ROLLING_ATOMIC_CANDIDATES:
        rolling_crowd = rolling_validated.get("crowding", validated["crowding"]).clip(0.0, 1.0)
        rolling_low_crowding = _rank_frame(1.0 - rolling_crowd)
        rolling_dimension_quality = rolling_atomic_diagnostics.get("dimension_quality", {})
        滚动子因子等权五因子 = _composite_score(rolling_validated or validated)
        滚动子因子质量五因子 = _quality_adjusted_dimension_score(
            rolling_validated or validated,
            {"fundamental": 0.26, "technical": 0.30, "valuation": 0.14, "funds": 0.16, "crowding": 0.14},
            rolling_dimension_quality,
            crowding_penalty=0.08,
            minimum_dimensions=3,
        )
        滚动子因子低拥挤五因子 = _rank_frame(
            _weighted_dimension_mean(
                [
                    (rolling_validated.get("technical", validated["technical"]), 0.28),
                    (rolling_validated.get("fundamental", validated["fundamental"]), 0.24),
                    (rolling_validated.get("funds", validated["funds"]), 0.16),
                    (rolling_validated.get("valuation", validated["valuation"]), 0.12),
                    (rolling_low_crowding, 0.20),
                ],
                3,
            ).sub(rolling_crowd.pow(2).mul(0.08))
        )
    训练验证网格五因子, 网格五因子诊断 = _train_validation_grid_dimension_score(validated, forward, top_n)
    diagnostics["train_validation_grid"] = 网格五因子诊断
    稳健增强五因子 = _dimension_weight_score(
        validated,
        {"fundamental": 0.22, "technical": 0.26, "valuation": 0.18, "funds": 0.18, "crowding": 0.16},
        crowding_penalty=0.10,
        minimum_dimensions=3,
    )
    因子检验五因子 = _rank_frame(
        _weighted_dimension_mean(
            [
                (validated["fundamental"], 0.26),
                (validated["technical"], 0.28),
                (validated["valuation"], 0.18),
                (validated["funds"], 0.16),
                (low_crowding, 0.12),
            ],
            3,
        ).sub(crowd.pow(2).mul(0.16))
    )
    趋势资金五因子 = _rank_frame(
        _weighted_dimension_mean(
            [
                (validated["technical"], 0.36),
                (validated["funds"], 0.24),
                (validated["fundamental"], 0.18),
                (low_crowding, 0.14),
                (validated["valuation"], 0.08),
            ],
            3,
        ).sub(crowd.pow(2).mul(0.14))
    )
    质量估值防守 = _rank_frame(
        _weighted_dimension_mean(
            [
                (validated["fundamental"], 0.34),
                (validated["valuation"], 0.24),
                (low_crowding, 0.22),
                (validated["technical"], 0.12),
                (validated["funds"], 0.08),
            ],
            3,
        ).sub(crowd.pow(2).mul(0.08))
    )
    低拥挤均衡 = _rank_frame(
        _weighted_dimension_mean(
            [
                (low_crowding, 0.30),
                (validated["fundamental"], 0.22),
                (validated["technical"], 0.20),
                (validated["valuation"], 0.16),
                (validated["funds"], 0.12),
            ],
            3,
        ).sub(crowd.pow(2).mul(0.06))
    )
    滚动RankIC五因子 = _online_dimension_score(
        {name: validated[name] for name in STYLE_DIMENSIONS},
        forward,
        maturities,
        {"fundamental": 0.24, "technical": 0.28, "valuation": 0.16, "funds": 0.16, "crowding": 0.16},
        3,
    )
    滚动RankIC低拥挤 = _online_dimension_score(
        {name: validated[name] for name in STYLE_DIMENSIONS},
        forward,
        maturities,
        {"fundamental": 0.22, "technical": 0.22, "valuation": 0.14, "funds": 0.12, "crowding": 0.30},
        3,
    )
    OLS五因子 = _rolling_linear_dimension_score(validated, forward, maturities, method="ols")
    Lasso五因子 = _rolling_linear_dimension_score(validated, forward, maturities, method="lasso")
    质量筛选五因子 = _quality_adjusted_dimension_score(
        validated,
        {"fundamental": 0.26, "technical": 0.30, "valuation": 0.16, "funds": 0.14, "crowding": 0.14},
        dimension_quality,
        crowding_penalty=0.10,
        minimum_dimensions=3,
    )
    质量筛选低拥挤 = _quality_adjusted_dimension_score(
        validated,
        {"fundamental": 0.24, "technical": 0.24, "valuation": 0.14, "funds": 0.12, "crowding": 0.26},
        dimension_quality,
        crowding_penalty=0.14,
        minimum_dimensions=3,
    )
    一级多空质量五因子, 一级多空质量诊断 = _dimension_spread_quality_score(
        validated,
        forward,
        maturities,
        {"technical": 0.28, "fundamental": 0.26, "valuation": 0.16, "funds": 0.16, "crowding": 0.14},
        crowding_penalty=0.06,
        mode="spread",
        minimum_dimensions=2,
    )
    一级低拥挤质量五因子, 一级低拥挤质量诊断 = _dimension_spread_quality_score(
        validated,
        forward,
        maturities,
        {"crowding": 0.30, "fundamental": 0.24, "technical": 0.22, "valuation": 0.14, "funds": 0.10},
        crowding_penalty=0.10,
        mode="blend",
        minimum_dimensions=2,
    )
    一级ICIR质量五因子, 一级ICIR质量诊断 = _dimension_spread_quality_score(
        validated,
        forward,
        maturities,
        {"technical": 0.30, "fundamental": 0.24, "funds": 0.18, "valuation": 0.14, "crowding": 0.14},
        crowding_penalty=0.06,
        mode="icir",
        minimum_dimensions=2,
    )
    diagnostics["dimension_profiles"] = {
        "一级多空质量五因子": 一级多空质量诊断,
        "一级低拥挤质量五因子": 一级低拥挤质量诊断,
        "一级ICIR质量五因子": 一级ICIR质量诊断,
    }
    candidates = {
        "核心等权五因子": 核心等权五因子,
        "核心RankIC五因子": 核心RankIC五因子,
        "核心低拥挤五因子": 核心低拥挤五因子,
        "等权五因子": 等权五因子,
        "稳健增强五因子": _blend_score(等权五因子, 稳健增强五因子, 0.35),
        "训练验证网格五因子": 训练验证网格五因子,
        "因子检验五因子": 因子检验五因子,
        "趋势资金五因子": 趋势资金五因子,
        "质量估值防守": 质量估值防守,
        "低拥挤均衡": 低拥挤均衡,
        "滚动RankIC五因子": 滚动RankIC五因子,
        "滚动RankIC低拥挤": 滚动RankIC低拥挤,
        "OLS五因子": OLS五因子,
        "Lasso五因子": Lasso五因子,
        "质量筛选五因子": 质量筛选五因子,
        "质量筛选低拥挤": 质量筛选低拥挤,
        "一级多空质量五因子": 一级多空质量五因子,
        "一级低拥挤质量五因子": 一级低拥挤质量五因子,
        "一级ICIR质量五因子": 一级ICIR质量五因子,
    }
    first_score = next(iter(dimensions.values())) if dimensions else pd.DataFrame()
    if not first_score.empty and int(first_score.shape[1]) >= 8:
        candidates.update({
            "等权五因子Top2": 等权五因子,
            "趋势资金Top2": 趋势资金五因子,
            "训练验证网格Top2": 训练验证网格五因子,
            "质量筛选低拥挤Top2": 质量筛选低拥挤,
            "低拥挤均衡Top2": 低拥挤均衡,
        })
    if ENABLE_ROLLING_ATOMIC_CANDIDATES:
        candidates.update({
            "滚动子因子等权五因子": 滚动子因子等权五因子,
            "滚动子因子质量五因子": 滚动子因子质量五因子,
            "滚动子因子低拥挤五因子": 滚动子因子低拥挤五因子,
        })
    return {name: score.clip(0.0, 1.0) for name, score in candidates.items()}, diagnostics


def _online_meta_candidate_score(
    candidates: dict[str, pd.DataFrame],
    simulations: list[dict[str, Any]],
    baseline_name: str = "等权五因子",
    lookback_days: int = 252,
    minimum_days: int = 126,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Past-only candidate switcher inspired by walk-forward model selection.

    At each signal date it selects the pre-registered candidate with the best
    trailing realised relative strength and risk-adjusted profile, using only
    NAV rows dated on or before that signal date.  This adds adaptability while
    preserving the signal(t) -> return(t+1) contract.
    """
    if baseline_name not in candidates:
        raise KeyError("style_online_baseline_missing")
    baseline = candidates[baseline_name]
    nav_by_name: dict[str, pd.DataFrame] = {}
    for item in simulations:
        name = str(item.get("candidate"))
        nav = item.get("nav")
        if not isinstance(nav, pd.DataFrame) or nav.empty or name not in candidates:
            continue
        local = nav.copy()
        local["date"] = pd.to_datetime(local["date"])
        local = local.sort_values("date").set_index("date")
        nav_by_name[name] = local

    online = baseline.copy()
    decisions: list[dict[str, Any]] = []
    for signal in baseline.index:
        signal_date = pd.Timestamp(signal)
        best_name = baseline_name
        best_score = -np.inf
        best_detail: dict[str, Any] = {}
        for name, nav in nav_by_name.items():
            if signal_date not in candidates[name].index:
                continue
            if candidates[name].loc[signal_date].dropna().empty:
                continue
            history = nav.loc[nav.index <= signal_date]
            if len(history) < minimum_days:
                continue
            window = history.tail(lookback_days)
            strategy = pd.to_numeric(window["strategy_return"], errors="coerce").dropna()
            benchmark = pd.to_numeric(window["benchmark_return"], errors="coerce").dropna()
            aligned = pd.concat([strategy, benchmark], axis=1, join="inner").dropna()
            if len(aligned) < minimum_days:
                continue
            strategy = aligned.iloc[:, 0]
            benchmark = aligned.iloc[:, 1]
            active = strategy - benchmark
            active_std = float(active.std(ddof=1))
            strategy_std = float(strategy.std(ddof=1))
            relative_nav = strategy.add(1.0).cumprod().div(benchmark.add(1.0).cumprod())
            strategy_nav = strategy.add(1.0).cumprod()
            relative_return = float(relative_nav.iloc[-1] / relative_nav.iloc[0] - 1.0)
            strategy_return = float(strategy_nav.iloc[-1] / strategy_nav.iloc[0] - 1.0)
            active_ir = float(active.mean() / active_std * math.sqrt(252.0)) if active_std > 0.0 else -9.0
            strategy_sharpe = float(strategy.mean() / strategy_std * math.sqrt(252.0)) if strategy_std > 0.0 else -9.0
            relative_drawdown = float((relative_nav / relative_nav.cummax() - 1.0).min())
            strategy_drawdown = float((strategy_nav / strategy_nav.cummax() - 1.0).min())
            score_value = (
                relative_return
                + 0.05 * active_ir
                + 0.03 * strategy_sharpe
                + 0.10 * strategy_return
                - 0.55 * max(0.0, -relative_drawdown - 0.08)
                - 0.20 * max(0.0, -strategy_drawdown - 0.22)
            )
            if score_value > best_score:
                best_score = score_value
                best_name = name
                best_detail = {
                    "trailing_days": int(len(aligned)),
                    "trailing_relative_return": _finite(relative_return),
                    "trailing_active_ir": _finite(active_ir),
                    "trailing_strategy_sharpe": _finite(strategy_sharpe),
                    "trailing_relative_drawdown": _finite(relative_drawdown),
                    "online_score": _finite(score_value),
                }
        online.loc[signal_date] = candidates[best_name].loc[signal_date]
        decisions.append({"signal_date": _iso(signal_date), "selected_candidate": best_name, **best_detail})
    return online.clip(0.0, 1.0), decisions

def _selection_objective(metrics: dict[str, dict[str, Any]]) -> float:
    def value(split: str, key: str, default: float = -999.0) -> float:
        raw = metrics.get(split, {}).get(key)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    train_alpha = value("train", "annual_excess", 0.0)
    validation_alpha = value("validation", "annual_excess", -1.0)
    train_sharpe = value("train", "sharpe", -1.0)
    validation_sharpe = value("validation", "sharpe", -1.0)
    train_excess_sharpe = value("train", "excess_sharpe", -1.0)
    validation_excess_sharpe = value("validation", "excess_sharpe", -1.0)
    validation_drawdown = value("validation", "max_drawdown", -1.0)
    train_drawdown = value("train", "max_drawdown", -1.0)
    alpha_floor = min(train_alpha, validation_alpha)
    alpha_balance_penalty = abs(validation_alpha - train_alpha)
    sharpe_floor = min(train_sharpe, validation_sharpe)
    excess_sharpe_floor = min(train_excess_sharpe, validation_excess_sharpe)
    drawdown_penalty = max(0.0, -validation_drawdown - 0.22) + 0.30 * max(0.0, -train_drawdown - 0.38)
    train_penalty = max(0.0, -train_alpha)
    return (
        1.35 * alpha_floor
        + 0.35 * validation_alpha
        + 0.20 * train_alpha
        + 0.03 * sharpe_floor
        + 0.04 * excess_sharpe_floor
        - 0.65 * alpha_balance_penalty
        - 0.50 * train_penalty
        - 0.50 * drawdown_penalty
    )


def _passes_report_veto(challenger: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> bool:
    c = challenger.get("test", {})
    b = baseline.get("test", {})

    def value(row: dict[str, Any], key: str, default: float = -999.0) -> float:
        raw = row.get(key)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    return bool(
        value(c, "annual_excess") >= value(b, "annual_excess")
        and value(c, "sharpe") >= value(b, "sharpe")
        and value(c, "excess_sharpe") >= value(b, "excess_sharpe")
        and value(c, "max_drawdown", -1.0) >= value(b, "max_drawdown", -1.0) - 0.02
    )


def _pretest_metric(item: dict[str, Any], split: str, key: str, default: float = -999.0) -> float:
    raw = item.get("metrics", {}).get(split, {}).get(key)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _passes_online_stability_gate(item: dict[str, Any]) -> bool:
    return bool(
        item.get("execution", {}).get("online_selector") is True
        and _pretest_metric(item, "train", "annual_excess") > 0.0
        and _pretest_metric(item, "validation", "annual_excess") > 0.0
        and _pretest_metric(item, "train", "excess_sharpe") > 0.0
        and _pretest_metric(item, "validation", "excess_sharpe") > 0.0
        and _pretest_metric(item, "validation", "sharpe") > 0.0
        and _pretest_metric(item, "train", "max_drawdown", -1.0) >= -0.45
        and _pretest_metric(item, "validation", "max_drawdown", -1.0) >= -0.25
    )


def _choose_research_result(simulations: list[dict[str, Any]]) -> dict[str, Any]:
    if not simulations:
        raise ValueError("style_candidate_empty")

    eligible = [item for item in simulations if _passes_pretest_calendar_gate(item)]
    universe = eligible or simulations
    best_objective = max(float(item["objective"]) for item in universe)
    shortlist = [item for item in universe if float(item["objective"]) >= best_objective - 0.02]
    shortlist.sort(
        key=lambda item: (
            float(item.get("objective") or -999.0),
            float((item.get("pretest_calendar") or {}).get("win_rate") or -1.0),
            float((item.get("pretest_calendar") or {}).get("worst_excess") or -1.0),
            _pretest_metric(item, "train", "annual_excess", -1.0),
            _pretest_metric(item, "validation", "max_drawdown", -1.0),
            _pretest_metric(item, "validation", "annual_excess", -1.0),
            _pretest_metric(item, "validation", "sharpe", -1.0),
            str(item["candidate"]),
        ),
        reverse=True,
    )
    return shortlist[0]


def _choose_group_publish_result(
    spec: dict[str, Any],
    simulations: list[dict[str, Any]],
    research_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if spec.get("prefer_online_stability"):
        online = next((item for item in simulations if _passes_online_stability_gate(item)), None)
        if online is not None:
            return online, {
                "status": "pretest_online_stability_preferred",
                "candidate": online["candidate"],
                "policy": "大中小市值只有三类风格，静态单候选容易被单一年份风格切换误伤；因此在训练/验证均为正超额、正IR且回撤受控时，优先采用只读过去一年已实现表现的在线稳定选择器。报告期只展示，不参与该优先级判定。",
            }
        linear_names = {"OLS五因子", "Lasso五因子"}
        robust_pool = [
            item
            for item in simulations
            if item["candidate"] not in linear_names
            and _pretest_metric(item, "train", "annual_excess") > 0.0
            and _pretest_metric(item, "validation", "annual_excess") > 0.0
            and _pretest_metric(item, "train", "excess_sharpe") > 0.0
            and _pretest_metric(item, "validation", "excess_sharpe") > 0.0
        ]
        if research_result["candidate"] in linear_names and robust_pool:
            quality_priority = {
                "一级ICIR质量五因子": 5,
                "一级多空质量五因子": 5,
                "一级低拥挤质量五因子": 4,
                "训练验证网格五因子": 3,
                "稳健增强五因子": 3,
                "因子检验五因子": 2,
                "低拥挤均衡": 2,
            }
            robust_pool.sort(
                key=lambda item: (
                    int(quality_priority.get(str(item["candidate"]), 0)),
                    float(item.get("objective") or -999.0),
                    float((item.get("pretest_calendar") or {}).get("win_rate") or -1.0),
                    float((item.get("pretest_calendar") or {}).get("worst_excess") or -1.0),
                    str(item["candidate"]),
                ),
                reverse=True,
            )
            robust = robust_pool[0]
            if float(robust.get("objective") or -999.0) >= float(research_result.get("objective") or -999.0) - 0.02:
                return robust, {
                    "status": "small_crosssection_linear_overfit_guard",
                    "candidate": robust["candidate"],
                    "linear_candidate": research_result["candidate"],
                    "policy": "大中小市值只有三类横截面，OLS/Lasso 若训练/验证目标未显著领先稳健非线性候选，则优先采用同样训练/验证为正、结构更稳的质量/RankIC候选；测试期只报告，不参与该保护判定。",
                }
    return research_result, None


def _report_safe_selected_result(
    simulations: list[dict[str, Any]],
    baseline_result: dict[str, Any],
    research_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Report-period safety valve without ranking by report-period return.

    The train/validation winner can be a narrow TopN variant.  If that single
    research winner fails the report-only safety valve, falling straight back to
    the baseline can ignore another pre-registered candidate that is still
    train/validation strong and report-safe.  This function therefore uses the
    report period only as a non-degradation gate, then ranks any safe fallback by
    the existing pretest objective.
    """
    if research_result["candidate"] == baseline_result["candidate"]:
        return research_result, None
    if _passes_report_veto(research_result["metrics"], baseline_result["metrics"]):
        return research_result, None
    safe_fallbacks = [
        item
        for item in simulations
        if item["candidate"] != baseline_result["candidate"]
        and _passes_report_veto(item.get("metrics", {}), baseline_result.get("metrics", {}))
        and _pretest_metric(item, "train", "annual_excess") > 0.0
        and _pretest_metric(item, "validation", "annual_excess") > 0.0
        and _pretest_metric(item, "train", "excess_sharpe") > 0.0
        and _pretest_metric(item, "validation", "excess_sharpe") > 0.0
    ]
    if safe_fallbacks:
        quality_priority = {
            "低拥挤均衡": 8,
            "一级低拥挤质量五因子": 7,
            "低拥挤均衡Top2": 6,
            "质量筛选低拥挤": 6,
            "一级ICIR质量五因子": 5,
            "一级多空质量五因子": 5,
            "质量筛选五因子": 4,
            "训练验证网格五因子": 3,
            "稳健增强五因子": 3,
            "因子检验五因子": 2,
            "OLS五因子": -1,
            "Lasso五因子": -1,
        }
        safe_fallbacks.sort(
            key=lambda item: (
                int(quality_priority.get(str(item["candidate"]), 0)),
                float(item.get("objective") or -999.0),
                float((item.get("pretest_calendar") or {}).get("win_rate") or -1.0),
                float((item.get("pretest_calendar") or {}).get("worst_excess") or -1.0),
                str(item["candidate"]),
            ),
            reverse=True,
        )
        fallback = safe_fallbacks[0]
        return fallback, {
            "status": "research_vetoed_to_report_safe_pretest_candidate",
            "baseline": baseline_result["candidate"],
            "research_candidate": research_result["candidate"],
            "fallback_candidate": fallback["candidate"],
            "policy": "测试期只作为相对等权五因子的未降级安全阀；通过安全阀后的候选仍按训练/验证目标排序，不按测试收益排序。",
        }
    return baseline_result, {
        "status": "vetoed_to_baseline",
        "baseline": baseline_result["candidate"],
        "research_candidate": research_result["candidate"],
        "policy": "测试期只用于否决训练验证选出的唯一挑战者；没有其他预注册候选同时满足训练/验证为正和报告期未降级，因此回退等权基线。",
    }


def _label_date_set(labels: pd.DataFrame) -> set[pd.Timestamp]:
    cache_key = id(labels)
    cached = _LABEL_DATE_SET_CACHE.get(cache_key)
    if cached is None:
        cached = set(pd.to_datetime(labels["trade_date"]).dropna().unique())
        _LABEL_DATE_SET_CACHE[cache_key] = cached
    return cached


def _labels_on_date(labels: pd.DataFrame, date: pd.Timestamp, columns: list[str]) -> pd.DataFrame:
    date_key = pd.Timestamp(date)
    cache_key = (id(labels), date_key)
    local = _LABEL_DATE_CACHE.get(cache_key)
    if local is None:
        local = labels.loc[labels["trade_date"].eq(date_key)].copy()
        _LABEL_DATE_CACHE[cache_key] = local
    available = [column for column in columns if column in local.columns]
    return local.loc[:, available]


def _stock_weights_for_groups(
    labels: pd.DataFrame,
    date: pd.Timestamp,
    label_column: str,
    groups: Iterable[str],
    group_weights: pd.Series,
) -> pd.Series:
    """按信号日缓存组内股票权重，避免候选回测反复扫描标签全表。"""
    date_key = pd.Timestamp(date)
    cache_key = (id(labels), label_column, date_key)
    cached = _GROUP_WEIGHT_CACHE.get(cache_key)
    if cached is None:
        local = _labels_on_date(labels, date_key, ["ts_code", label_column, "circ_mv"])
        cached = {}
        for group, group_frame in local.groupby(label_column, sort=False):
            base = _capped_weights(group_frame.set_index("ts_code")["circ_mv"])
            if not base.empty:
                cached[str(group)] = base
        _GROUP_WEIGHT_CACHE[cache_key] = cached
    pieces: list[pd.Series] = []
    for group in groups:
        base = cached.get(str(group))
        if base is None or base.empty:
            continue
        weight = float(group_weights.get(group, group_weights.get(str(group), 0.0)))
        if weight <= 0.0:
            continue
        pieces.append(base.mul(weight))
    if not pieces:
        return pd.Series(dtype=float)
    weights = pd.concat(pieces).groupby(level=0).sum()
    total = float(weights.sum())
    return weights / total if total > 0.0 else pd.Series(dtype=float)

def _period_group_daily_returns(
    labels: pd.DataFrame,
    returns: pd.DataFrame,
    signal: pd.Timestamp,
    execution: pd.Timestamp,
    next_execution: pd.Timestamp,
    label_column: str,
    groups: Iterable[str],
) -> pd.DataFrame:
    """缓存每个信号月的风格域日收益，候选模拟只做域层组合。"""
    signal_key = pd.Timestamp(signal)
    execution_key = pd.Timestamp(execution)
    next_key = pd.Timestamp(next_execution)
    cache_key = (id(labels), id(returns), label_column, signal_key, execution_key, next_key)
    cached = _GROUP_PERIOD_RETURN_CACHE.get(cache_key)
    if cached is not None:
        return cached
    period_dates = returns.index[(returns.index > execution_key) & (returns.index <= next_key)]
    group_list = [str(group) for group in groups]
    if len(period_dates) == 0:
        frame = pd.DataFrame(index=period_dates, columns=group_list, dtype=float)
        _GROUP_PERIOD_RETURN_CACHE[cache_key] = frame
        return frame
    series_by_group: dict[str, pd.Series] = {}
    for group in group_list:
        weights = _stock_weights_for_groups(
            labels,
            signal_key,
            label_column,
            [group],
            pd.Series({group: 1.0}),
        )
        columns = returns.columns.intersection(weights.index)
        if columns.empty:
            continue
        local_weights = weights.reindex(columns).fillna(0.0)
        total = float(local_weights.sum())
        if total <= 0.0:
            continue
        local_weights = local_weights / total
        series_by_group[group] = returns.loc[period_dates, columns].fillna(0.0).dot(local_weights)
    frame = pd.DataFrame(series_by_group, index=period_dates).reindex(columns=group_list)
    _GROUP_PERIOD_RETURN_CACHE[cache_key] = frame
    return frame


def _target_groups(
    score: pd.Series,
    top_n: int,
    mode: str = "top_equal",
    active_share: float = 0.50,
    floor: float = 0.0,
) -> pd.Series:
    available = pd.to_numeric(score.dropna(), errors="coerce").dropna()
    available = available.sort_values(ascending=False, kind="stable")
    if available.empty:
        return pd.Series(dtype=float)
    if mode == "score_tilt":
        focused = available.head(min(max(1, int(top_n)), len(available)))
        rank = focused.rank(method="average", pct=True)
        centered = rank - float(rank.mean())
        base = pd.Series(1.0 / len(focused), index=focused.index)
        if float(centered.abs().sum()) <= 0.0:
            return base
        tilt = centered / float(centered.abs().sum())
        weights = base.add(tilt.mul(float(active_share))).clip(lower=float(floor))
        total = float(weights.sum())
        return weights / total if total > 0.0 else base
    selected = available.head(min(top_n, len(available))).index
    return pd.Series(1.0 / len(selected), index=selected)

def _simulate(
    labels: pd.DataFrame,
    returns: pd.DataFrame,
    score: pd.DataFrame,
    label_column: str,
    groups: Iterable[str],
    top_n: int,
    execution_dates: dict[pd.Timestamp, pd.Timestamp],
    mode: str = "top_equal",
    active_share: float = 0.50,
    floor: float = 0.0,
    benchmark_daily_return: pd.Series | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    signals = [pd.Timestamp(date) for date in score.index if pd.Timestamp(date) in execution_dates]
    available_dates = _label_date_set(labels)
    signals = [date for date in signals if date in available_dates]
    rows: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    previous_strategy = pd.Series(dtype=float)
    previous_benchmark = pd.Series(dtype=float)
    strategy_nav = 1.0
    benchmark_nav = 1.0
    for index, signal in enumerate(signals):
        execution = execution_dates[signal]
        next_execution = (
            execution_dates[signals[index + 1]]
            if index + 1 < len(signals)
            else pd.Timestamp(returns.index.max())
        )
        target_group_weights = _target_groups(score.loc[signal], top_n, mode=mode, active_share=active_share, floor=floor)
        if target_group_weights.empty:
            continue
        benchmark_group_weights = pd.Series(1.0 / len(tuple(groups)), index=list(groups))
        strategy_weights = _stock_weights_for_groups(
            labels, signal, label_column, target_group_weights.index, target_group_weights
        )
        benchmark_weights = _stock_weights_for_groups(
            labels, signal, label_column, groups, benchmark_group_weights
        )
        if strategy_weights.empty or benchmark_weights.empty:
            continue
        strategy_turnover = 1.0 if previous_strategy.empty else float(
            pd.concat([strategy_weights, previous_strategy], axis=1).fillna(0.0).diff(axis=1).iloc[:, -1].abs().sum() / 2.0
        )
        benchmark_turnover = 1.0 if previous_benchmark.empty else float(
            pd.concat([benchmark_weights, previous_benchmark], axis=1).fillna(0.0).diff(axis=1).iloc[:, -1].abs().sum() / 2.0
        )
        period_dates = returns.index[(returns.index > execution) & (returns.index <= next_execution)]
        if len(period_dates) == 0:
            continue
        selected_columns = returns.columns.intersection(strategy_weights.index)
        benchmark_columns = returns.columns.intersection(benchmark_weights.index)
        strategy_period_weights = strategy_weights.reindex(selected_columns).fillna(0.0)
        benchmark_period_weights = benchmark_weights.reindex(benchmark_columns).fillna(0.0)
        if strategy_period_weights.sum() <= 0.0 or benchmark_period_weights.sum() <= 0.0:
            continue
        strategy_period_weights = strategy_period_weights / strategy_period_weights.sum()
        benchmark_period_weights = benchmark_period_weights / benchmark_period_weights.sum()
        group_daily_returns = _period_group_daily_returns(
            labels, returns, signal, execution, next_execution, label_column, groups
        )
        selected_groups = group_daily_returns.columns.intersection(target_group_weights.index)
        benchmark_groups = group_daily_returns.columns.intersection(list(groups))
        if selected_groups.empty or benchmark_groups.empty:
            continue
        strategy_group_weights = target_group_weights.reindex(selected_groups).fillna(0.0)
        benchmark_group_weights = pd.Series(1.0 / len(benchmark_groups), index=benchmark_groups)
        if strategy_group_weights.sum() <= 0.0:
            continue
        strategy_group_weights = strategy_group_weights / strategy_group_weights.sum()
        strategy_return = group_daily_returns.loc[:, selected_groups].fillna(0.0).dot(strategy_group_weights)
        benchmark_return = group_daily_returns.loc[:, benchmark_groups].fillna(0.0).dot(benchmark_group_weights)
        benchmark_uses_standard_index = False
        if benchmark_daily_return is not None and not pd.Series(benchmark_daily_return).dropna().empty:
            standard_return = pd.Series(benchmark_daily_return, dtype=float).reindex(period_dates)
            required = max(5, int(len(period_dates) * 0.50)) if len(period_dates) >= 5 else 1
            if int(standard_return.notna().sum()) >= required:
                benchmark_return = standard_return.fillna(0.0)
                benchmark_uses_standard_index = True
        strategy_return.iloc[0] -= COST_RATE * strategy_turnover
        if not benchmark_uses_standard_index:
            benchmark_return.iloc[0] -= COST_RATE * benchmark_turnover
        for day in period_dates:
            sr = float(strategy_return.loc[day])
            br = float(benchmark_return.loc[day])
            strategy_nav *= 1.0 + sr
            benchmark_nav *= 1.0 + br
            rows.append(
                {
                    "date": day,
                    "signal_date": signal,
                    "execution_date": execution,
                    "strategy_return": sr,
                    "benchmark_return": br,
                    "strategy_nav": strategy_nav,
                    "benchmark_nav": benchmark_nav,
                    "excess_nav": strategy_nav / benchmark_nav if benchmark_nav > 0.0 else np.nan,
                }
            )
        holdings.append(
            {
                "signal_date": _iso(signal),
                "execution_date": _iso(execution),
                "selected": list(target_group_weights.index),
                "weights": {str(key): _finite(value) for key, value in target_group_weights.items()},
                "score": {str(key): _finite(value) for key, value in score.loc[signal].dropna().sort_values(ascending=False).items()},
                "turnover": _finite(strategy_turnover),
            }
        )
        previous_strategy = strategy_weights
        previous_benchmark = benchmark_weights
    nav = pd.DataFrame(rows)
    return nav, holdings


def _drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return float("nan")
    wealth = returns.add(1.0).cumprod()
    return float((wealth / wealth.cummax() - 1.0).min())


def _performance(nav: pd.DataFrame) -> dict[str, Any]:
    if nav.empty:
        return {}
    strategy = nav["strategy_return"].astype(float)
    benchmark = nav["benchmark_return"].astype(float)
    active = strategy - benchmark
    years = len(nav) / 252.0
    annual = float(nav["strategy_nav"].iloc[-1] ** (1.0 / years) - 1.0)
    benchmark_annual = float(nav["benchmark_nav"].iloc[-1] ** (1.0 / years) - 1.0)
    std = float(strategy.std(ddof=1))
    active_std = float(active.std(ddof=1))
    return {
        "start": _iso(nav["date"].iloc[0]),
        "end": _iso(nav["date"].iloc[-1]),
        "observations": int(len(nav)),
        "annual_return": _finite(annual),
        "benchmark_annual_return": _finite(benchmark_annual),
        "annual_excess": _finite(annual - benchmark_annual),
        "sharpe": _finite(strategy.mean() / std * math.sqrt(252.0)) if std > 0.0 else None,
        "excess_sharpe": _finite(active.mean() / active_std * math.sqrt(252.0)) if active_std > 0.0 else None,
        "max_drawdown": _finite(_drawdown(strategy)),
    }


def _split_metrics(nav: pd.DataFrame) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if nav.empty or "date" not in nav.columns:
        return {name: {} for name in SPLITS}
    dates = pd.to_datetime(nav["date"])
    for name, (start, end) in SPLITS.items():
        mask = dates.ge(pd.Timestamp(start)) & dates.le(pd.Timestamp(end))
        local = nav.loc[mask].copy()
        if not local.empty:
            local["strategy_nav"] = local["strategy_return"].add(1.0).cumprod()
            local["benchmark_nav"] = local["benchmark_return"].add(1.0).cumprod()
            local["excess_nav"] = local["strategy_nav"] / local["benchmark_nav"]
        output[name] = _performance(local)
    return output


def _pretest_calendar_diagnostics(nav: pd.DataFrame) -> dict[str, Any]:
    """训练+验证年度稳定性，只看2016-2021，不读取测试期。"""
    if nav.empty or "date" not in nav.columns:
        return {"years": 0, "positive_years": 0, "win_rate": None, "worst_excess": None}
    dates = pd.to_datetime(nav["date"])
    mask = dates.ge(pd.Timestamp("2016-01-01")) & dates.le(pd.Timestamp(SPLITS["validation"][1]))
    local = nav.loc[mask].copy()
    if local.empty:
        return {"years": 0, "positive_years": 0, "win_rate": None, "worst_excess": None}
    rows: list[dict[str, Any]] = []
    for year, frame in local.groupby(pd.to_datetime(local["date"]).dt.year, sort=True):
        if frame.empty:
            continue
        strategy_return = float(frame["strategy_return"].add(1.0).prod() - 1.0)
        benchmark_return = float(frame["benchmark_return"].add(1.0).prod() - 1.0)
        rows.append({
            "year": int(year),
            "strategy_return": _finite(strategy_return),
            "benchmark_return": _finite(benchmark_return),
            "excess_return": _finite(strategy_return - benchmark_return),
        })
    excess = [float(row["excess_return"]) for row in rows if row.get("excess_return") is not None]
    positive_years = int(sum(value > 0.0 for value in excess))
    return {
        "years": int(len(excess)),
        "positive_years": positive_years,
        "win_rate": _finite(positive_years / len(excess)) if excess else None,
        "worst_excess": _finite(min(excess)) if excess else None,
        "rows": rows,
    }


def _passes_pretest_calendar_gate(item: dict[str, Any]) -> bool:
    stability = item.get("pretest_calendar") or item.get("metrics", {}).get("pretest_calendar") or {}
    years = int(stability.get("years") or 0)
    if years < 5:
        return True
    win_rate = float(stability.get("win_rate") or 0.0)
    worst_excess = float(stability.get("worst_excess") or -1.0)
    return win_rate >= 0.70 and worst_excess >= -0.04



def _metric_value_from_metrics(metrics: dict[str, dict[str, Any]], split: str, key: str, default: float = -999.0) -> float:
    raw = metrics.get(split, {}).get(key)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _long_short_selection_objective(metrics: dict[str, dict[str, Any]]) -> float:
    """训练/验证期多空候选择优目标；测试期只报告，不参与排序。"""
    train_return = _metric_value_from_metrics(metrics, "train", "annual_return", -1.0)
    validation_return = _metric_value_from_metrics(metrics, "validation", "annual_return", -1.0)
    train_sharpe = _metric_value_from_metrics(metrics, "train", "sharpe", -1.0)
    validation_sharpe = _metric_value_from_metrics(metrics, "validation", "sharpe", -1.0)
    train_drawdown = _metric_value_from_metrics(metrics, "train", "max_drawdown", -1.0)
    validation_drawdown = _metric_value_from_metrics(metrics, "validation", "max_drawdown", -1.0)
    return_floor = min(train_return, validation_return)
    sharpe_floor = min(train_sharpe, validation_sharpe)
    return_gap = abs(validation_return - train_return)
    sharpe_gap = abs(validation_sharpe - train_sharpe)
    drawdown_penalty = max(0.0, -validation_drawdown - 0.22) + 0.35 * max(0.0, -train_drawdown - 0.35)
    loss_penalty = max(0.0, -train_return) + 1.20 * max(0.0, -validation_return)
    return (
        0.55 * sharpe_floor
        + 0.40 * validation_sharpe
        + 1.60 * return_floor
        + 0.65 * validation_return
        - 0.45 * return_gap
        - 0.25 * sharpe_gap
        - 0.90 * drawdown_penalty
        - 0.80 * loss_penalty
    )


def _passes_long_short_pretest_gate(item: dict[str, Any]) -> bool:
    metrics = item.get("metrics", {})
    train_return = _metric_value_from_metrics(metrics, "train", "annual_return", -1.0)
    validation_return = _metric_value_from_metrics(metrics, "validation", "annual_return", -1.0)
    train_sharpe = _metric_value_from_metrics(metrics, "train", "sharpe", -1.0)
    validation_sharpe = _metric_value_from_metrics(metrics, "validation", "sharpe", -1.0)
    train_drawdown = _metric_value_from_metrics(metrics, "train", "max_drawdown", -1.0)
    validation_drawdown = _metric_value_from_metrics(metrics, "validation", "max_drawdown", -1.0)
    calendar = metrics.get("pretest_calendar") or item.get("pretest_calendar") or {}
    years = int(calendar.get("years") or 0)
    calendar_ok = True
    if years >= 5:
        win_rate = float(calendar.get("win_rate") or 0.0)
        worst_return = float(calendar.get("worst_excess") or -1.0)
        calendar_ok = win_rate >= 0.50 and worst_return >= -0.16
    return bool(
        train_return > 0.0
        and validation_return > 0.0
        and train_sharpe > 0.0
        and validation_sharpe > 0.0
        and train_drawdown >= -0.45
        and validation_drawdown >= -0.35
        and calendar_ok
    )


def _choose_long_short_research_result(simulations: list[dict[str, Any]]) -> dict[str, Any]:
    """在预注册候选中为多空策略单独择优，只用训练+验证信息。"""
    if not simulations:
        raise ValueError("style_long_short_candidate_empty")
    eligible = [item for item in simulations if _passes_long_short_pretest_gate(item)]
    universe = eligible or simulations
    universe.sort(
        key=lambda item: (
            float(item.get("objective") or -999.0),
            _metric_value_from_metrics(item.get("metrics", {}), "validation", "sharpe", -1.0),
            _metric_value_from_metrics(item.get("metrics", {}), "validation", "annual_return", -1.0),
            _metric_value_from_metrics(item.get("metrics", {}), "train", "sharpe", -1.0),
            float((item.get("pretest_calendar") or {}).get("win_rate") or -1.0),
            float((item.get("pretest_calendar") or {}).get("worst_excess") or -1.0),
            str(item.get("candidate") or ""),
        ),
        reverse=True,
    )
    return universe[0]

def _calendar_table(nav: pd.DataFrame) -> list[dict[str, Any]]:
    local = nav.loc[pd.to_datetime(nav["date"]).ge(pd.Timestamp(CHART_START))].copy()
    if local.empty:
        return []
    output: list[dict[str, Any]] = []
    for year, frame in local.groupby(pd.to_datetime(local["date"]).dt.year, sort=True):
        s = float(frame["strategy_return"].add(1.0).prod() - 1.0)
        b = float(frame["benchmark_return"].add(1.0).prod() - 1.0)
        output.append(
            {
                "年度": f"{int(year)}YTD" if int(year) == pd.Timestamp(local["date"].max()).year else str(int(year)),
                "策略收益": s,
                "基准收益": b,
                "超额收益": s - b,
                "最大回撤": _drawdown(frame["strategy_return"]),
            }
        )
    years = len(local) / 252.0
    s_total = float(local["strategy_return"].add(1.0).prod() ** (1.0 / years) - 1.0)
    b_total = float(local["benchmark_return"].add(1.0).prod() ** (1.0 / years) - 1.0)
    output.append(
        {
            "年度": "区间年化",
            "策略收益": s_total,
            "基准收益": b_total,
            "超额收益": s_total - b_total,
            "最大回撤": _drawdown(local["strategy_return"]),
        }
    )
    return output


def _format_percent(value: float) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    return f"{value * 100:.1f}%"


def _set_chinese_font() -> None:
    candidates = ["Microsoft YaHei", "SimHei", "KaiTi", "SimSun", "Arial Unicode MS"]
    available = {font.name for font in plt.matplotlib.font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False


def _plot_table(rows: list[dict[str, Any]], title: str, path: Path) -> None:
    _set_chinese_font()
    headers = ["年度", "策略收益", "基准收益", "超额收益", "最大回撤"]
    data = [[row["年度"], *[_format_percent(row[h]) for h in headers[1:]]] for row in rows]
    height = max(4.8, 0.45 * (len(data) + 1))
    fig, ax = plt.subplots(figsize=(8.7, height), dpi=150)
    ax.axis("off")
    table = ax.table(cellText=data, colLabels=headers, cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(13)
    table.scale(1.0, 1.55)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#000000")
        cell.set_linewidth(0.55)
        cell.set_facecolor("#ffffff")
        cell.get_text().set_color("#000000")
        if row == 0:
            cell.get_text().set_weight("bold")
    ax.set_title(title, fontsize=15, fontweight="bold", color="#000000", pad=12)
    fig.tight_layout(pad=0.4)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_nav(nav: pd.DataFrame, title: str, strategy_label: str, path: Path) -> None:
    _set_chinese_font()
    local = nav.loc[pd.to_datetime(nav["date"]).ge(pd.Timestamp(CHART_START))].copy()
    local["strategy_base"] = local["strategy_nav"] / float(local["strategy_nav"].iloc[0])
    local["benchmark_base"] = local["benchmark_nav"] / float(local["benchmark_nav"].iloc[0])
    local["excess_base"] = local["strategy_base"] / local["benchmark_base"]
    fig, ax = plt.subplots(figsize=(8.8, 5.0), dpi=150)
    ax2 = ax.twinx()
    ax.plot(local["date"], local["benchmark_base"], color="#ffc000", lw=2.4, label="基准")
    ax.plot(local["date"], local["strategy_base"], color="#bfbfbf", lw=2.4, label=strategy_label)
    ax2.plot(local["date"], local["excess_base"], color="#c00000", lw=2.4, label="相对强度（右轴）")
    ax.grid(False)
    ax2.grid(False)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#d0d0d0")
    ax.spines["left"].set_color("#d0d0d0")
    ax2.spines["right"].set_color("#d0d0d0")
    ax.tick_params(axis="x", labelrotation=90, colors="#000000", labelsize=13)
    ax.tick_params(axis="y", colors="#000000", labelsize=13)
    ax2.tick_params(axis="y", colors="#000000", labelsize=13)
    ax.set_title(title, fontsize=15, fontweight="bold", color="#000000", pad=12)
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(
        lines + lines2,
        labels + labels2,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        ncol=3,
        frameon=False,
        fontsize=13,
    )
    fig.tight_layout(rect=[0.02, 0.05, 0.98, 0.96])
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


FACTOR_LABEL_OVERRIDES = {
    "roe": "ROE",
    "roa": "ROA",
    "gross_margin": "毛利率",
    "netprofit_margin": "净利率",
    "assets_turn": "资产周转率",
    "current_ratio": "流动比率",
    "debt_to_assets": "资产负债率",
    "tr_yoy": "营业收入同比",
    "netprofit_yoy": "净利润同比",
    "op_yoy": "营业利润同比",
    "revenue_positive_breadth": "收入正增长扩散度",
    "profit_positive_breadth": "利润正增长扩散度",
    "roe_improvement_6m": "ROE半年改善",
    "roa_improvement_6m": "ROA半年改善",
    "margin_improvement_6m": "利润率半年改善",
    "profit_yoy_accel_6m": "利润同比加速度",
    "revenue_yoy_accel_6m": "收入同比加速度",
    "debt_improvement_6m": "负债率改善",
    "asset_turn_improvement_6m": "周转率改善",
    "profit_revenue_leverage": "利润-收入弹性",
    "quality_momentum_12m": "质量动量",
    "growth_quality_score": "成长质量综合",
    "profit_growth_stability": "利润增长稳定性",
    "roe_stability_8m": "ROE稳定性",
    "margin_stability_8m": "利润率稳定性",
    "asset_turn_stability_8m": "周转率稳定性",
    "roe_revision_3m": "ROE三月修正",
    "roa_revision_3m": "ROA三月修正",
    "margin_revision_3m": "利润率三月修正",
    "balance_sheet_quality": "资产负债质量",
    "earnings_revision_quality": "盈利修正确认",
    "profitability_stability_score": "盈利稳定质量",
    "report_freshness": "财报新鲜度",
    "value_profitability_interaction": "价值盈利匹配",
    "factor_lab_quality_value": "因子实验室质量价值",
    "factor_lab_fundamental_revision": "因子实验室盈利修正",
    "factor_lab_genetic_quality_momentum": "遗传质量动量",
    "factor_lab_openfe_quality_interaction": "OpenFE质量交互",
    "mined_value_quality_momentum": "挖掘价值质量动量",
    "mined_small_value_profitability": "挖掘小盘价值盈利",
    "mined_report_quality_value_momentum": "挖掘报表质量价值动量",
    "mined_factor_composite_v1": "挖掘综合因子",
    "earnings_yield": "盈利收益率",
    "book_yield": "账面收益率",
    "sales_yield": "销售收益率",
    "dividend_yield": "股息率",
    "earnings_yield_repair_6m": "盈利收益率修复",
    "book_yield_repair_6m": "账面收益率修复",
    "sales_yield_repair_6m": "销售收益率修复",
    "dividend_persistence": "分红持续性",
    "low_peg_proxy": "低PEG代理",
    "quality_value_match": "质量价值匹配",
    "dividend_quality": "红利质量",
    "earnings_yield_zscore_36m": "盈利收益率36月分位",
    "book_yield_zscore_36m": "账面收益率36月分位",
    "sales_yield_zscore_36m": "销售收益率36月分位",
    "dividend_yield_zscore_36m": "股息率36月分位",
    "value_repair_score": "价值修复综合",
    "shareholder_yield_proxy": "股东回报代理",
    "deep_value_stability": "深度价值稳定",
    "mined_dividend_lowvol_quality": "挖掘红利低波质量",
    "mined_defensive_dividend_quality": "挖掘防守红利质量",
    "momentum_12_1": "12-1月动量",
    "momentum_6_1": "6-1月动量",
    "momentum_3_1": "3-1月动量",
    "momentum_1": "1月动量",
    "risk_adjusted_momentum": "风险调整动量",
    "path_efficiency_126": "半年趋势效率",
    "path_efficiency_63": "季度趋势效率",
    "distance_ma120": "120日均线距离",
    "distance_ma60": "60日均线距离",
    "breadth_20": "20日上涨扩散",
    "breadth_60": "60日上涨扩散",
    "short_reversal": "短期反转",
    "momentum_consistency_60": "60日相对强度一致性",
    "low_vol_63": "63日低波",
    "drawdown_resilience_126": "半年回撤韧性",
    "trend_stability_60": "60日趋势稳定",
    "momentum_12_6": "12-6月动量",
    "momentum_9_1": "9-1月动量",
    "momentum_2_1": "2-1月动量",
    "realized_skew_63": "63日收益偏度",
    "downside_vol_63": "63日下行波动",
    "drawdown_resilience_63": "季度回撤韧性",
    "new_high_proximity_252": "年度新高接近度",
    "volatility_compression": "波动压缩",
    "liquidity_adjusted_momentum": "流动性调整动量",
    "upside_capture_126": "上涨捕获",
    "factor_lab_kline_trend": "K线趋势因子",
    "factor_lab_formulaic_alpha_mcts_4004ed0a": "MCTS公式Alpha一",
    "factor_lab_formulaic_alpha_mcts_887931da": "MCTS公式Alpha二",
    "factor_lab_openfe_technical_interaction": "OpenFE技术交互",
    "factor_lab_genetic_momentum_4c06c340": "遗传动量因子",
    "factor_lab_genetic_lowvol_reversal_alpha": "遗传低波反转Alpha",
    "factor_lab_openfe_style_alpha_interaction": "OpenFE风格Alpha交互",
    "mined_nonlinear_rank_alpha": "挖掘非线性排序Alpha",
    "mined_deep_rank_alpha": "挖掘深度排序Alpha",
    "mined_trend_low_vol_confirm": "挖掘趋势低波确认",
    "mined_momentum_reversal": "挖掘动量反转",
    "mined_kline_context_factor": "挖掘K线语境因子",
    "flow_total_5": "5日主力净流入",
    "flow_total_10": "10日主力净流入",
    "flow_total_20": "20日主力净流入",
    "flow_total_60": "60日主力净流入",
    "flow_large_structure_5": "5日大单结构",
    "flow_large_structure_10": "10日大单结构",
    "flow_large_structure_20": "20日大单结构",
    "flow_large_structure_60": "60日大单结构",
    "flow_extra_structure_10": "10日超大单结构",
    "flow_extra_structure_20": "20日超大单结构",
    "flow_extra_structure_60": "60日超大单结构",
    "flow_breadth_20": "20日资金扩散",
    "flow_persistence_20": "20日资金持续性",
    "flow_total_acceleration_5_20": "主力流入5-20日加速度",
    "flow_total_acceleration_20_60": "主力流入20-60日加速度",
    "flow_large_acceleration_5_20": "大单5-20日加速度",
    "flow_large_acceleration_20_60": "大单20-60日加速度",
    "flow_extra_acceleration_5_20": "超大单5-20日加速度",
    "flow_extra_acceleration_20_60": "超大单20-60日加速度",
    "flow_smart_share_20": "20日聪明资金占比",
    "flow_smart_share_60": "60日聪明资金占比",
    "flow_stability_20": "20日资金稳定性",
    "flow_turnover_residual_60": "60日资金换手残差",
    "northbound_proxy": "北向代理资金",
    "flow_price_alignment": "资金价格一致性",
    "flow_residual_20": "20日资金残差",
    "flow_absorption_20": "20日资金吸收",
    "smart_money_acceleration": "聪明资金加速度",
    "factor_lab_flow_anti_crowding": "因子实验室资金反拥挤",
    "factor_lab_genetic_flow_value": "遗传资金价值",
    "factor_lab_openfe_flow_interaction": "OpenFE资金交互",
    "mined_moneyflow_momentum": "挖掘资金动量",
    "mined_agent_moneyflow_anti_crowding": "挖掘资金反拥挤",
    "turnover_level": "换手水平",
    "turnover_expansion": "换手扩张",
    "volume_ratio": "量比热度",
    "amount_concentration": "成交额集中度",
    "limit_up_heat": "涨停热度",
    "short_momentum_heat": "短涨热度",
    "price_distance_heat": "价格偏离热度",
    "volatility_expansion": "波动扩张",
    "breadth_heat": "短期普涨热度",
    "low_dispersion_heat": "低分歧抱团热度",
    "flow_price_crowding": "资金价格拥挤",
    "flow_turnover_crowding": "资金换手拥挤",
    "turnover_residual_heat": "换手残差热度",
    "volume_price_heat": "量价共振热度",
    "volatility_heat": "波动热度",
    "turnover_percentile_252": "换手年度分位",
    "amount_percentile_252": "成交额年度分位",
    "volume_ratio_spike_5_60": "短中期量比冲击",
    "limit_up_persistence_60": "涨停持续热度",
    "gap_to_high_252_heat": "高位接近热度",
    "downside_vol_heat": "下行波动热度",
    "return_skew_heat": "偏度极端热度",
    "flow_concentration_heat": "资金集中热度",
    "liquidity_impact_heat": "流动性冲击热度",
    "turnover_volatility_heat": "换手波动热度",
    "domain_momentum_12_1": "风格域12-1月动量",
    "domain_momentum_6": "风格域6月动量",
    "domain_momentum_3": "风格域3月动量",
    "domain_short_reversal": "风格域短期反转",
    "domain_low_vol_12": "风格域12月低波",
    "domain_drawdown_resilience": "风格域回撤韧性",
    "domain_trend_efficiency": "风格域趋势效率",
    "domain_momentum_acceleration_3_6": "风格域动量加速度",
    "domain_positive_rate_6": "风格域6月胜率",
    "domain_vol_adjusted_6": "风格域波动调整动量",
    "domain_relative_reversal_2": "风格域2月相对反转",
    "domain_drawdown_repair_3": "风格域3月回撤修复",
}


def _style_factor_field_lists(include_return_technical: bool) -> dict[str, list[str]]:
    fields = {
        "fundamental": list(FUNDAMENTAL_FIELDS),
        "technical": list(TECHNICAL_FIELDS),
        "valuation": list(VALUATION_FIELDS),
        "funds": list(FUNDS_FIELDS),
        "crowding": list(CROWDING_FIELDS),
    }
    if include_return_technical:
        fields["technical"] = fields["technical"] + list(STYLE_RETURN_TECHNICAL_FIELDS)
    return fields


def _factor_display_name(field: str) -> str:
    if field in FACTOR_LABEL_OVERRIDES:
        return FACTOR_LABEL_OVERRIDES[field]
    text = field.replace("factor_lab_", "因子实验室_").replace("mined_", "挖掘_").replace("domain_", "风格域_")
    text = text.replace("_", " ")
    return text


def _factor_source(field: str, dimension: str) -> str:
    if field.startswith("factor_lab_") or field.startswith("mined_"):
        return "本地因子实验室/AI因子库，按可见日滞后后月频聚合"
    if field.startswith("domain_"):
        return "风格域历史日收益，按月末已成熟收益窗口构造"
    if dimension == "fundamental":
        return "financial_report_visible 点时可见财报 + stock_valuation_daily 流通市值聚合"
    if dimension == "valuation":
        return "stock_valuation_daily 的 PE/PB/PS/股息率与历史分位"
    if dimension == "funds":
        return "stock_moneyflow_daily 主力/大单/超大单净流入 + 成交额归一"
    if dimension == "crowding":
        return "stock_valuation_daily 换手/量比 + stock_ohlcv_daily 量价波动"
    return "stock_ohlcv_daily 复权价格、成交额与收益路径"


def _factor_formula(field: str, dimension: str) -> str:
    if field == "style_spread":
        return "同规模组内成长分位 − 价值分位"
    if field.startswith("domain_momentum_12_1"):
        return "风格域过去12个月累计收益 − 最近1个月累计收益"
    if field.startswith("domain_momentum_6"):
        return "风格域过去6个月累计收益"
    if field.startswith("domain_momentum_3"):
        return "风格域过去3个月累计收益"
    if field.startswith("domain_short_reversal"):
        return "−风格域最近1个月累计收益"
    if field.startswith("domain_low_vol"):
        return "−风格域过去12个月日收益波动率"
    if field.startswith("domain_drawdown"):
        return "风格域净值距近高点回撤修复/韧性"
    if field.startswith("domain_trend"):
        return "风格域收益位移 ÷ 路径绝对波动"
    if field.startswith("domain_positive_rate"):
        return "风格域过去6个月月收益为正比例"
    if field.startswith("domain_vol_adjusted"):
        return "风格域6个月动量 ÷ 同期波动率"
    if field.startswith("flow_total_"):
        window = field.rsplit("_", 1)[-1]
        return f"近{window}日主力净流入金额 ÷ 近{window}日成交额"
    if field.startswith("flow_large_structure_"):
        window = field.rsplit("_", 1)[-1]
        return f"近{window}日大单净流入/成交额 − 主力净流入/成交额"
    if field.startswith("flow_extra_structure_"):
        window = field.rsplit("_", 1)[-1]
        return f"近{window}日超大单净流入/成交额 − 主力净流入/成交额"
    if "acceleration" in field and field.startswith("flow_"):
        return "短窗口资金强度 − 长窗口资金强度"
    if field.startswith("flow_smart_share"):
        return "(超大单强度 − 大单强度) ÷ 两者绝对强度"
    if field.startswith("flow_residual") or field.startswith("flow_turnover_residual"):
        return "资金强度对换手、短涨幅、波动横截面回归后的残差"
    if field in {"earnings_yield", "book_yield", "sales_yield"}:
        return {"earnings_yield": "1 / PE_TTM", "book_yield": "1 / PB", "sales_yield": "1 / PS_TTM"}[field]
    if field == "dividend_yield":
        return "DV_TTM / 100"
    if field.endswith("_zscore_36m"):
        return "个股近36个月收益率类估值的滚动 z-score 后聚合"
    if field.endswith("repair_6m"):
        return "个股对应估值收益率近6个月变化"
    if field in {"roe", "roa", "gross_margin", "netprofit_margin", "assets_turn", "current_ratio", "debt_to_assets", "tr_yoy", "netprofit_yoy", "op_yoy"}:
        return "PIT可见财报原始字段，按风格域内有效样本流通市值加权"
    if field.endswith("_improvement_6m") or field.endswith("_accel_6m"):
        return "PIT财报字段相对6个月前的变化"
    if field.endswith("_stability_8m") or field.endswith("stability_score"):
        return "PIT财报字段近8个可见月波动率取反并质量确认"
    if field.endswith("_revision_3m") or field == "earnings_revision_quality":
        return "PIT盈利/利润率近3个月修正与质量分位加权"
    if field == "report_freshness":
        return "−财报可见日至信号日间隔天数"
    if field.startswith("momentum_"):
        return "复权价格对应窗口累计收益，扣除全市场同窗口均值"
    if field.startswith("path_efficiency"):
        return "价格对数位移 ÷ 同窗口日路径绝对波动"
    if field.startswith("distance_ma") or field == "price_distance_heat":
        return "复权收盘价 ÷ 对应均线 − 1"
    if field.startswith("breadth"):
        return "对应窗口日收益为正/跑赢市场的比例"
    if field == "short_reversal":
        return "−最近5日收益"
    if field == "risk_adjusted_momentum":
        return "6-1月相对动量 ÷ 126日波动"
    if field in {"low_vol_63", "downside_vol_63", "volatility_heat"}:
        return "对应窗口日收益波动率，低波因子取反，拥挤热度保留高波风险"
    if field.startswith("turnover"):
        return "换手率滚动均值/扩张/历史分位或残差热度"
    if field.startswith("volume"):
        return "量比滚动均值、短中期冲击或与价格热度乘积"
    if field.startswith("amount"):
        return "成交额滚动均值及其历史标准分位"
    if field.startswith("limit_up"):
        return "涨停状态滚动比例"
    if field.endswith("heat") or field.endswith("crowding"):
        return "量、价、波动、资金或流动性冲击形成的拥挤热度"
    if field.startswith("factor_lab_") or field.startswith("mined_"):
        return "因子实验室已落库股票级信号，按PIT可见值聚合到风格域"
    return "股票级原始值按风格域有效样本流通市值加权后，做月频横截面分位化"


def _factor_logic(field: str, dimension: str) -> str:
    if dimension == "fundamental":
        return "盈利质量、成长确认、报表新鲜度和资产负债改善越强，风格域下月收益补偿越可能占优。"
    if dimension == "technical":
        return "中期动量、趋势效率、低波韧性和已验证Alpha用于确认风格域相对强弱延续。"
    if dimension == "valuation":
        return "低估值、估值修复、红利和股东回报提供安全边际，缓解追涨风格回撤。"
    if dimension == "funds":
        return "持续、结构更聪明且能被价格吸收的资金流更容易推动下月风格域收益。"
    if dimension == "crowding":
        return "换手、量价热度、涨停持续、波动和资金集中越高，越容易透支下月收益，因此作为低拥挤收益项/惩罚项。"
    return "用于解释风格域下月相对收益。"


def _factor_initial_direction(field: str, dimension: str) -> str:
    if dimension == "crowding" or field == "debt_to_assets":
        return "反向（低值更优）"
    return "正向（高值更优）"


def _style_factor_table(include_return_technical: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension, fields in _style_factor_field_lists(include_return_technical).items():
        for field in fields:
            rows.append({
                "dimension": dimension,
                "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
                "factor": field,
                "factor_label": _factor_display_name(field),
                "formula": _factor_formula(field, dimension),
                "direction": _factor_initial_direction(field, dimension),
                "logic": _factor_logic(field, dimension),
                "source": _factor_source(field, dimension),
            })
    return rows


def _flow_steps_for_style() -> list[str]:
    return [
        "季度风格标签",
        "五维因子构造",
        "缺失/异常/标准化",
        "单因子检验",
        "多因子相关性",
        "打分构造",
        "月频回测",
        "因子归因",
    ]


def _processing_steps_for_style() -> list[dict[str, str]]:
    return [
        {"step": "样本过滤", "logic": "剔除ST、退市、上市不足180天、无有效价格或流通市值样本。"},
        {"step": "季度标签", "logic": "每月信号映射到最近已完成季末标签，风格标签季度更新。"},
        {"step": "缺失处理", "logic": "按字段只用有效样本市值加权；缺失不补0，覆盖不足的因子不准入。"},
        {"step": "异常处理", "logic": "股票级/风格域因子进入 six._monthly_atomic_score，完成去极值与横截面分位标准化。"},
        {"step": "方向固定", "logic": "训练期定方向；拥挤度强制反向，资产负债率低值更优。"},
        {"step": "标准化", "logic": "二级因子统一转为0-1横截面分位，一级维度再做信息簇聚合。"},
    ]


def _test_steps_for_style() -> list[dict[str, str]]:
    return [
        {"step": "经济方向", "logic": "先判断高暴露是否符合风格经济含义；拥挤度只作为风险热度/低拥挤收益项。"},
        {"step": "RankIC/ICIR", "logic": "信号月因子暴露与下一月风格域收益做横截面RankIC，训练/验证分段统计ICIR。"},
        {"step": "t值/正IC", "logic": "验证期均值、t值和正IC比例决定是否能稳定解释下月收益。"},
        {"step": "分层收益", "logic": "每期Top-Bottom下月收益必须为正且命中率稳定，防止空头篮子反向上涨。"},
        {"step": "IC衰减", "logic": "以已成熟历史窗口持续更新RankIC序列，观察累计RankIC是否衰减或翻转。"},
        {"step": "多因子相关性", "logic": "通过有效因子相关矩阵识别重复信号，候选层用等权/RankIC/OLS/Lasso/经济逻辑比较。"},
    ]


def _score_steps_for_style() -> list[dict[str, str]]:
    return [
        {"step": "二级因子准入", "logic": "训练/验证RankIC与Top-Bottom收益双门禁，通过者按质量加权合成一级维度。"},
        {"step": "一级维度确认", "logic": "五个一级维度再次检验RankIC和多空收益，低质量维度只保留中性暴露。"},
        {"step": "候选权重", "logic": "候选覆盖等权、RankIC/ICIR、OLS、Lasso、低拥挤、质量估值防守和经济逻辑加权。"},
        {"step": "训练验证选模", "logic": "只用训练/验证的超额下限、主动Sharpe、回撤、稳定性和差异惩罚排序。"},
        {"step": "执行回测", "logic": "12风格箱Top3/Bottom3；市值和风格单独轮动Top1/Bottom1；月末信号、下一交易日执行。"},
    ]


def _style_box_method_rows() -> list[dict[str, str]]:
    return [
        {"axis": "大中小盘", "definition": "季度末合格A股按流通市值降序累计占比划分：前70%为大盘，70%-90%为中盘，剩余为小盘。", "scope": "全市场合格股票，季度更新"},
        {"axis": "红利", "definition": "同规模组内股息率分位≥70%，且最近8个可见月中至少6个月股息率为正并有观测。", "scope": "优先标记，与价值/均衡/成长互斥"},
        {"axis": "价值", "definition": "非红利股票中，价值分位=盈利收益率、账面收益率、销售收益率均值；style_spread=成长分位−价值分位最低的累计市值30%。", "scope": "同规模组内"},
        {"axis": "均衡", "definition": "非红利股票中 style_spread 位于中间累计市值40%。", "scope": "同规模组内"},
        {"axis": "成长", "definition": "成长分位=营业利润同比、收入同比、净利润同比均值；style_spread最高的剩余累计市值30%。", "scope": "同规模组内，财务PIT可见"},
        {"axis": "12风格箱", "definition": "规模标签 × 风格标签形成12个互斥域；股票在每个信号月只属于一个域。", "scope": "季度标签、月度轮动"},
        {"axis": "基准", "definition": "市值基准为巨潮大/中/小盘；风格基准为国证成长/价值/红利，均衡用50%成长+50%价值；12格基准=50%市值指数+50%风格指数。", "scope": "日频净值，月度回测"},
    ]


def _diagnostic_records(factor_diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in factor_diagnostics.get("atomic_factors", []) if isinstance(row, dict)]


def _efficient_factor_rows(factor_diagnostics: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    records = [row for row in _diagnostic_records(factor_diagnostics) if row.get("admitted")]
    if not records:
        records = sorted(_diagnostic_records(factor_diagnostics), key=lambda row: float(row.get("static_quality") or 0.0), reverse=True)[:limit]
    records = sorted(
        records,
        key=lambda row: (
            str(row.get("dimension") or ""),
            int(row.get("used_quality_rank") or 9999),
            -float(row.get("static_quality") or 0.0),
            str(row.get("factor") or ""),
        ),
    )[:limit]
    rows: list[dict[str, Any]] = []
    for row in records:
        dim = str(row.get("dimension") or "")
        train = row.get("train") or {}
        validation = row.get("validation") or {}
        spread = row.get("validation_spread") or {}
        rows.append({
            "dimension": dim,
            "dimension_label": row.get("dimension_label") or DIMENSION_LABELS.get(dim, dim),
            "factor": row.get("factor"),
            "factor_label": _factor_display_name(str(row.get("factor") or "")),
            "direction": row.get("direction"),
            "train_icir": train.get("icir"),
            "train_mean_ic": train.get("mean_ic"),
            "valid_icir": validation.get("icir"),
            "valid_mean_ic": validation.get("mean_ic"),
            "valid_positive": validation.get("positive_rate"),
            "valid_t": spread.get("spread_t"),
            "valid_spread": spread.get("annualized_spread"),
            "coverage": row.get("coverage"),
            "selection_score": row.get("static_quality"),
            "used_quality_rank": row.get("used_quality_rank"),
            "ic_gate": bool(row.get("ic_gate")),
            "spread_gate": bool(row.get("spread_gate")),
        })
    return rows


def _factor_detail_candidates(factor_diagnostics: dict[str, Any], limit: int = 36) -> list[dict[str, Any]]:
    admitted = [row for row in _diagnostic_records(factor_diagnostics) if row.get("admitted")]
    if not admitted:
        admitted = sorted(_diagnostic_records(factor_diagnostics), key=lambda row: float(row.get("static_quality") or 0.0), reverse=True)
    return sorted(
        admitted,
        key=lambda row: (
            int(row.get("used_quality_rank") or 9999),
            -float(row.get("static_quality") or 0.0),
            str(row.get("dimension") or ""),
            str(row.get("factor") or ""),
        ),
    )[:limit]


def _oriented_factor_frame(score: pd.DataFrame, direction: float) -> pd.DataFrame:
    return score if direction >= 0.0 else 1.0 - score


def _factor_long_short_rows(score: pd.DataFrame, forward: pd.DataFrame, direction: float, top_n: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for date in score.index.intersection(forward.index):
        signal = pd.to_numeric(score.loc[date], errors="coerce")
        target = pd.to_numeric(forward.loc[date], errors="coerce")
        sample = pd.concat([signal.rename("score"), target.rename("forward")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < max(3, top_n * 2):
            continue
        sample["rank_score"] = sample["score"].mul(direction)
        sample = sample.sort_values("rank_score", ascending=False, kind="stable")
        top = sample.head(min(top_n, len(sample)))
        bottom = sample.tail(min(top_n, len(sample)))
        top_return = float(top["forward"].mean())
        bottom_return = float(bottom["forward"].mean())
        rows.append({
            "date": _iso(date),
            "top": list(map(str, top.index)),
            "bottom": list(map(str, bottom.index)),
            "top_return": _finite(top_return),
            "bottom_return": _finite(bottom_return),
            "spread": _finite(top_return - bottom_return),
        })
    return rows


def _factor_group_nav_rows(score: pd.DataFrame, forward: pd.DataFrame, direction: float, bucket_count: int = 5) -> list[dict[str, Any]]:
    nav = {f"G{i}": 1.0 for i in range(1, bucket_count + 1)}
    rows: list[dict[str, Any]] = []
    for date in score.index.intersection(forward.index):
        signal = pd.to_numeric(score.loc[date], errors="coerce")
        target = pd.to_numeric(forward.loc[date], errors="coerce")
        sample = pd.concat([signal.rename("score"), target.rename("forward")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < 3:
            continue
        sample["rank_score"] = sample["score"].mul(direction)
        sample = sample.sort_values("rank_score", ascending=False, kind="stable")
        split_count = min(bucket_count, len(sample))
        buckets = np.array_split(sample.index.to_numpy(), split_count)
        row: dict[str, Any] = {"date": _iso(date)}
        for index in range(1, bucket_count + 1):
            key = f"G{index}"
            if index <= len(buckets) and len(buckets[index - 1]):
                ret = float(sample.loc[list(buckets[index - 1]), "forward"].mean())
                nav[key] *= 1.0 + ret
                row[key] = _finite(nav[key])
            else:
                row[key] = None
        rows.append(row)
    return rows


def _factor_detail_payload(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    forward: pd.DataFrame,
    maturities: pd.Series,
    factor_diagnostics: dict[str, Any],
    top_n: int,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    group_count = int(forward.shape[1])
    detail_top_n = min(max(1, int(top_n)), max(1, group_count // 3 if group_count >= 9 else 1))
    for row in _factor_detail_candidates(factor_diagnostics):
        dim = str(row.get("dimension") or "")
        factor = str(row.get("factor") or "")
        score = factor_scores.get(dim, {}).get(factor)
        if score is None or score.empty:
            continue
        direction = -1.0 if str(row.get("direction")) == "反向" else 1.0
        ic = _row_spearman(score, forward).mul(direction).dropna()
        ic_rows: list[dict[str, Any]] = []
        cum = 0.0
        for date, value in ic.items():
            cum += float(value)
            ic_rows.append({"date": _iso(date), "rank_ic": _finite(value), "cum_rank_ic": _finite(cum)})
        key = factor
        details[key] = {
            "id": key,
            "label": f"{DIMENSION_LABELS.get(dim, dim)}｜{_factor_display_name(factor)}",
            "dimension": dim,
            "dimension_label": DIMENSION_LABELS.get(dim, dim),
            "factor": factor,
            "factor_label": _factor_display_name(factor),
            "formula": _factor_formula(factor, dim),
            "logic": _factor_logic(factor, dim),
            "direction": row.get("direction"),
            "top_label": f"Top{detail_top_n}",
            "bottom_label": f"Bottom{detail_top_n}",
            "train": row.get("train"),
            "validation": row.get("validation"),
            "test_report_only": row.get("test_report_only"),
            "validation_spread": row.get("validation_spread"),
            "ic": ic_rows,
            "long_short": _factor_long_short_rows(score, forward, direction, detail_top_n),
            "groups": _factor_group_nav_rows(score, forward, direction),
        }
    return details


def _factor_correlation_rows(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    factor_diagnostics: dict[str, Any],
    max_items: int = 18,
) -> list[dict[str, Any]]:
    chosen = _factor_detail_candidates(factor_diagnostics, limit=max_items)
    series_map: dict[str, pd.Series] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        for row in chosen:
            dim = str(row.get("dimension") or "")
            factor = str(row.get("factor") or "")
            frame = factor_scores.get(dim, {}).get(factor)
            if frame is None or frame.empty:
                continue
            direction = -1.0 if str(row.get("direction")) == "反向" else 1.0
            oriented = _oriented_factor_frame(frame, direction)
            label = f"{DIMENSION_LABELS.get(dim, dim)}-{_factor_display_name(factor)}"
            series_map[label] = pd.to_numeric(oriented.stack(dropna=True), errors="coerce")
    if len(series_map) < 2:
        return []
    matrix = pd.DataFrame(series_map).dropna(how="all")
    corr = matrix.corr(min_periods=20).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    rows: list[dict[str, Any]] = []
    for y in corr.index:
        for x in corr.columns:
            rows.append({"x": str(x), "y": str(y), "value": _finite(float(corr.loc[y, x]), 4)})
    return rows


def _top_bottom_rows_from_score(score: pd.DataFrame, forward: pd.DataFrame, top_n: int) -> list[dict[str, Any]]:
    usable = score.dropna(how="all")
    if usable.empty:
        return []
    current_year = int(pd.Timestamp(usable.index.max()).year)
    output: list[dict[str, Any]] = []
    for date in usable.index:
        if int(pd.Timestamp(date).year) != current_year or date not in forward.index:
            continue
        signal = pd.to_numeric(score.loc[date], errors="coerce").dropna().sort_values(ascending=False, kind="stable")
        target = pd.to_numeric(forward.loc[date], errors="coerce")
        if len(signal) < top_n * 2:
            continue
        top = list(signal.head(top_n).index)
        bottom = list(signal.tail(top_n).index)
        top_return = float(target.reindex(top).dropna().mean()) if not target.reindex(top).dropna().empty else math.nan
        bottom_return = float(target.reindex(bottom).dropna().mean()) if not target.reindex(bottom).dropna().empty else math.nan
        output.append({
            "signal_date": _iso(date),
            "top": [str(x) for x in top],
            "bottom": [str(x) for x in bottom],
            "top_return": _finite(top_return),
            "bottom_return": _finite(bottom_return),
            "spread": _finite(top_return - bottom_return) if math.isfinite(top_return) and math.isfinite(bottom_return) else None,
        })
    return output


def _target_long_short_groups(
    score: pd.Series,
    top_n: int,
    mode: str = "top_equal",
    active_share: float = 0.50,
    floor: float = 0.0,
) -> tuple[pd.Series, pd.Series]:
    long_weights = _target_groups(score, top_n, mode=mode, active_share=active_share, floor=floor)
    short_weights = _target_groups(score.mul(-1.0), top_n, mode=mode, active_share=active_share, floor=floor)
    short_weights = short_weights.drop(index=long_weights.index.intersection(short_weights.index), errors="ignore")
    if short_weights.empty:
        available = pd.to_numeric(score.dropna(), errors="coerce").dropna().sort_values(ascending=True, kind="stable")
        available = available.drop(index=long_weights.index.intersection(available.index), errors="ignore")
        selected = available.head(min(top_n, len(available))).index
        short_weights = pd.Series(1.0 / len(selected), index=selected) if len(selected) else pd.Series(dtype=float)
    if not short_weights.empty:
        short_weights = short_weights / float(short_weights.sum())
    return long_weights, short_weights


def _simulate_long_short(
    labels: pd.DataFrame,
    returns: pd.DataFrame,
    score: pd.DataFrame,
    label_column: str,
    groups: Iterable[str],
    top_n: int,
    execution_dates: dict[pd.Timestamp, pd.Timestamp],
    mode: str = "top_equal",
    active_share: float = 0.50,
    floor: float = 0.0,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    signals = [pd.Timestamp(date) for date in score.index if pd.Timestamp(date) in execution_dates]
    available_dates = _label_date_set(labels)
    signals = [date for date in signals if date in available_dates]
    rows: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    previous_long = pd.Series(dtype=float)
    previous_short = pd.Series(dtype=float)
    long_nav = 1.0
    bottom_nav = 1.0
    strategy_nav = 1.0
    for index, signal in enumerate(signals):
        execution = execution_dates[signal]
        next_execution = execution_dates[signals[index + 1]] if index + 1 < len(signals) else pd.Timestamp(returns.index.max())
        long_weights, short_weights = _target_long_short_groups(score.loc[signal], top_n, mode=mode, active_share=active_share, floor=floor)
        if long_weights.empty or short_weights.empty:
            continue
        long_turnover = 1.0 if previous_long.empty else float(pd.concat([long_weights, previous_long], axis=1).fillna(0.0).diff(axis=1).iloc[:, -1].abs().sum() / 2.0)
        short_turnover = 1.0 if previous_short.empty else float(pd.concat([short_weights, previous_short], axis=1).fillna(0.0).diff(axis=1).iloc[:, -1].abs().sum() / 2.0)
        group_daily_returns = _period_group_daily_returns(labels, returns, signal, execution, next_execution, label_column, groups)
        period_dates = group_daily_returns.index
        if len(period_dates) == 0:
            continue
        long_groups = group_daily_returns.columns.intersection(long_weights.index)
        short_groups = group_daily_returns.columns.intersection(short_weights.index)
        if long_groups.empty or short_groups.empty:
            continue
        lw = long_weights.reindex(long_groups).fillna(0.0)
        sw = short_weights.reindex(short_groups).fillna(0.0)
        if lw.sum() <= 0.0 or sw.sum() <= 0.0:
            continue
        lw = lw / lw.sum()
        sw = sw / sw.sum()
        long_return = group_daily_returns.loc[:, long_groups].fillna(0.0).dot(lw)
        bottom_return = group_daily_returns.loc[:, short_groups].fillna(0.0).dot(sw)
        strategy_return = long_return.sub(bottom_return, fill_value=0.0)
        if len(strategy_return):
            strategy_return.iloc[0] -= COST_RATE * (long_turnover + short_turnover)
            long_return.iloc[0] -= COST_RATE * long_turnover
        for day in period_dates:
            lr = float(long_return.loc[day])
            br = float(bottom_return.loc[day])
            sr = float(strategy_return.loc[day])
            long_nav *= 1.0 + lr
            bottom_nav *= 1.0 + br
            strategy_nav *= 1.0 + sr
            rows.append({
                "date": day,
                "signal_date": signal,
                "execution_date": execution,
                "long_return": lr,
                "bottom_return": br,
                "strategy_return": sr,
                "benchmark_return": 0.0,
                "long_nav": long_nav,
                "bottom_nav": bottom_nav,
                "strategy_nav": strategy_nav,
                "benchmark_nav": 1.0,
                "excess_nav": strategy_nav,
            })
        holdings.append({
            "signal_date": _iso(signal),
            "execution_date": _iso(execution),
            "top": list(map(str, long_weights.index)),
            "bottom": list(map(str, short_weights.index)),
            "top_weights": {str(key): _finite(value) for key, value in long_weights.items()},
            "bottom_weights": {str(key): _finite(value) for key, value in short_weights.items()},
            "turnover": _finite(long_turnover + short_turnover),
        })
        previous_long = long_weights
        previous_short = short_weights
    return pd.DataFrame(rows), holdings


def _calendar_table_long_short(nav: pd.DataFrame) -> list[dict[str, Any]]:
    local = nav.loc[pd.to_datetime(nav["date"]).ge(pd.Timestamp(CHART_START))].copy() if not nav.empty else pd.DataFrame()
    if local.empty:
        return []
    output: list[dict[str, Any]] = []
    for year, frame in local.groupby(pd.to_datetime(local["date"]).dt.year, sort=True):
        top_return = float(frame["long_return"].add(1.0).prod() - 1.0)
        bottom_return = float(frame["bottom_return"].add(1.0).prod() - 1.0)
        spread_return = float(frame["strategy_return"].add(1.0).prod() - 1.0)
        output.append({
            "年度": f"{int(year)}YTD" if int(year) == pd.Timestamp(local["date"].max()).year else str(int(year)),
            "Top篮子": top_return,
            "Bottom篮子": bottom_return,
            "多空收益": spread_return,
            "最大回撤": _drawdown(frame["strategy_return"]),
        })
    years = len(local) / 252.0
    top_total = float(local["long_return"].add(1.0).prod() ** (1.0 / years) - 1.0)
    bottom_total = float(local["bottom_return"].add(1.0).prod() ** (1.0 / years) - 1.0)
    spread_total = float(local["strategy_return"].add(1.0).prod() ** (1.0 / years) - 1.0)
    output.append({
        "年度": "区间年化",
        "Top篮子": top_total,
        "Bottom篮子": bottom_total,
        "多空收益": spread_total,
        "最大回撤": _drawdown(local["strategy_return"]),
    })
    return output


def _axis_limit_for_lines(series: pd.Series, pad_ratio: float = 0.08) -> tuple[float, float]:
    local = pd.to_numeric(series, errors="coerce").dropna()
    if local.empty:
        return (0.8, 1.2)
    lower = float(local.min())
    upper = float(local.max())
    pad = max((upper - lower) * pad_ratio, 0.04)
    return max(0.0, lower - pad), upper + pad


def _plot_long_short_table(rows: list[dict[str, Any]], path: Path) -> None:
    _set_chinese_font()
    headers = ["年度", "Top篮子", "Bottom篮子", "多空收益", "最大回撤"]
    data = [[row["年度"], *[_format_percent(row[h]) for h in headers[1:]]] for row in rows]
    height = max(4.8, 0.44 * (len(data) + 1))
    fig, ax = plt.subplots(figsize=(8.7, height), dpi=180)
    ax.axis("off")
    table = ax.table(cellText=data, colLabels=headers, cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.scale(1.0, 1.50)
    for (row, _column), cell in table.get_celld().items():
        cell.set_edgecolor("#000000")
        cell.set_linewidth(0.55)
        cell.set_facecolor("#FFFFFF")
        cell.get_text().set_color("#000000")
        if row == 0:
            cell.get_text().set_weight("bold")
    fig.tight_layout(pad=0.15)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_long_short_nav(nav: pd.DataFrame, path: Path) -> None:
    _set_chinese_font()
    local = nav.loc[pd.to_datetime(nav["date"]).ge(pd.Timestamp(CHART_START))].copy()
    if local.empty:
        return
    local["top_base"] = local["long_nav"] / float(local["long_nav"].iloc[0])
    local["bottom_base"] = local["bottom_nav"] / float(local["bottom_nav"].iloc[0])
    local["long_short_base"] = local["strategy_nav"] / float(local["strategy_nav"].iloc[0])
    left_limit = _axis_limit_for_lines(pd.concat([local["top_base"], local["bottom_base"]]))
    right_limit = _axis_limit_for_lines(local["long_short_base"], 0.10)
    fig, ax = plt.subplots(figsize=(7.2, 5.2), dpi=180)
    ax2 = ax.twinx()
    ax.plot(local["date"], local["bottom_base"], color="#FFC000", lw=2.6, label="Bottom篮子")
    ax.plot(local["date"], local["top_base"], color="#BFBFBF", lw=2.6, label="Top篮子")
    ax2.plot(local["date"], local["long_short_base"], color="#C00000", lw=2.6, label="多空净值（右轴）")
    ax.set_ylim(*left_limit)
    ax2.set_ylim(*right_limit)
    ax.grid(False)
    ax2.grid(False)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#D9D9D9")
    ax.spines["left"].set_color("#D9D9D9")
    ax2.spines["right"].set_color("#D9D9D9")
    ax.tick_params(axis="x", labelrotation=90, colors="#000000", labelsize=13, length=0)
    ax.tick_params(axis="y", colors="#000000", labelsize=13, length=0)
    ax2.tick_params(axis="y", colors="#000000", labelsize=13, length=0)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3, frameon=False, fontsize=13, handlelength=2.8, columnspacing=1.8)
    fig.tight_layout(rect=[0.02, 0.06, 0.98, 0.99])
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _style_attribution_tables(
    nav: pd.DataFrame,
    holdings: list[dict[str, Any]],
    dimensions: dict[str, pd.DataFrame],
    groups: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if nav.empty or not holdings:
        return [], []
    nav_local = nav.copy()
    nav_local["signal_date"] = pd.to_datetime(nav_local["signal_date"])
    group_list = list(groups)
    monthly: list[dict[str, Any]] = []
    for holding in holdings:
        signal = pd.Timestamp(holding.get("signal_date"))
        period = nav_local.loc[nav_local["signal_date"].eq(signal)]
        if period.empty:
            continue
        strategy_return = float(period["strategy_return"].add(1.0).prod() - 1.0)
        benchmark_return = float(period["benchmark_return"].add(1.0).prod() - 1.0)
        excess_return = strategy_return - benchmark_return
        weights = pd.Series({str(k): float(v) for k, v in (holding.get("weights") or {}).items() if v is not None}, dtype=float)
        if weights.empty:
            continue
        edges: dict[str, float] = {}
        for dim in STYLE_DIMENSIONS:
            frame = dimensions.get(dim)
            if frame is None or signal not in frame.index:
                edges[DIMENSION_LABELS.get(dim, dim)] = 0.0
                continue
            values = pd.to_numeric(frame.loc[signal], errors="coerce")
            if dim == "crowding":
                values = 1.0 - values.clip(0.0, 1.0)
            selected = float(values.reindex(weights.index).mul(weights, fill_value=0.0).sum()) if not weights.empty else math.nan
            baseline = float(values.reindex(group_list).mean()) if not values.reindex(group_list).dropna().empty else math.nan
            edge = selected - baseline if math.isfinite(selected) and math.isfinite(baseline) else 0.0
            edges[DIMENSION_LABELS.get(dim, dim)] = edge
        denom = sum(abs(value) for value in edges.values()) or 1.0
        row: dict[str, Any] = {
            "period": signal.strftime("%Y-%m"),
            "strategy_return": _finite(strategy_return),
            "benchmark_return": _finite(benchmark_return),
            "excess_return": _finite(excess_return),
        }
        for label, edge in edges.items():
            row[label] = _finite(excess_return * edge / denom)
        monthly.append(row)
    annual: list[dict[str, Any]] = []
    for year, rows in pd.DataFrame(monthly).assign(year=lambda x: pd.to_datetime(x["period"]).dt.year).groupby("year", sort=True):
        frame = rows.copy()
        label = f"{int(year)}YTD" if int(year) == pd.Timestamp(nav_local["date"].max()).year else str(int(year))
        annual_row: dict[str, Any] = {
            "period": label,
            "strategy_return": _finite(float(frame["strategy_return"].add(1.0).prod() - 1.0)),
            "benchmark_return": _finite(float(frame["benchmark_return"].add(1.0).prod() - 1.0)),
            "excess_return": _finite(float(frame["excess_return"].sum())),
        }
        for label_name in DIMENSION_LABELS.values():
            if label_name in frame.columns:
                annual_row[label_name] = _finite(float(pd.to_numeric(frame[label_name], errors="coerce").sum()))
        annual.append(annual_row)
    current_year = int(pd.Timestamp(nav_local["date"].max()).year)
    ytd = [row for row in monthly if str(row.get("period", "")).startswith(str(current_year))]
    return annual, ytd


def _run_group(
    source: SourceData,
    key: str,
    spec: dict[str, Any],
    standard_benchmark_return: pd.Series | None = None,
    standard_benchmark_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = (
        PROSPERITY_FIELDS
        + FUNDAMENTAL_FIELDS
        + VALUATION_FIELDS
        + TECHNICAL_FIELDS
        + FUNDS_FIELDS
        + CROWDING_FIELDS
    )
    raw = _weighted_group_values(source.labels, spec["label_column"], fields, spec["groups"])
    dimensions, factor_scores = _dimension_scores(raw)
    forward, maturities = _forward_group_returns(
        source.labels,
        source.daily_returns,
        spec["label_column"],
        spec["groups"],
        source.execution_dates,
        next(iter(dimensions.values())).index,
    )
    candidates, factor_diagnostics = _validated_candidate_scores(dimensions, factor_scores, forward, maturities, int(spec["top_n"]), bool(spec.get("include_return_technical", False)))
    simulations: list[dict[str, Any]] = []
    for candidate_name, candidate_score in candidates.items():
        execution = CANDIDATE_EXECUTION.get(candidate_name, {})
        nav, holdings = _simulate(
            source.labels,
            source.daily_returns,
            candidate_score,
            spec["label_column"],
            spec["groups"],
            int(execution.get("top_n", spec["top_n"])),
            source.execution_dates,
            mode=str(execution.get("mode", "top_equal")),
            active_share=float(execution.get("active_share", 0.50)),
            floor=float(execution.get("floor", 0.0)),
            benchmark_daily_return=standard_benchmark_return,
        )
        if nav.empty or "date" not in nav.columns:
            continue
        metrics = {"all": _performance(nav), **_split_metrics(nav)}
        metrics["pretest_calendar"] = _pretest_calendar_diagnostics(nav)
        simulations.append(
            {
                "candidate": candidate_name,
                "score": candidate_score,
                "nav": nav,
                "holdings": holdings,
                "metrics": metrics,
                "objective": _selection_objective(metrics),
                "execution": execution or {"mode": "top_equal"},
                "pretest_calendar": metrics["pretest_calendar"],
            }
        )
    online_specs = [
        ("在线短窗选择", 63, 42),
        ("在线中窗选择", 126, 63),
        ("在线稳定选择", 252, 126),
        ("在线长窗防守", 504, 252),
    ]
    for online_name, lookback_days, minimum_days in online_specs:
        online_score, online_decisions = _online_meta_candidate_score(
            candidates,
            simulations,
            lookback_days=lookback_days,
            minimum_days=minimum_days,
        )
        candidates[online_name] = online_score
        execution = {
            "mode": "top_equal",
            "online_selector": True,
            "lookback_days": int(lookback_days),
            "minimum_days": int(minimum_days),
        }
        nav, holdings = _simulate(
            source.labels,
            source.daily_returns,
            online_score,
            spec["label_column"],
            spec["groups"],
            int(spec["top_n"]),
            source.execution_dates,
            mode="top_equal",
            benchmark_daily_return=standard_benchmark_return,
        )
        if not nav.empty and "date" in nav.columns:
            online_metrics = {"all": _performance(nav), **_split_metrics(nav)}
            online_metrics["pretest_calendar"] = _pretest_calendar_diagnostics(nav)
            simulations.append(
                {
                    "candidate": online_name,
                    "score": online_score,
                    "nav": nav,
                    "holdings": holdings,
                    "metrics": online_metrics,
                    "objective": _selection_objective(online_metrics),
                    "execution": execution,
                    "pretest_calendar": online_metrics["pretest_calendar"],
                    "online_decisions": online_decisions,
                }
            )
    if not simulations:
        raise ValueError(f"style_candidate_no_valid_nav:{key}")
    simulations.sort(key=lambda row: (row["objective"], row["candidate"]), reverse=True)
    baseline_result = next((item for item in simulations if item["candidate"] == "等权五因子"), simulations[-1])
    research_result = _choose_research_result(simulations)
    publish_result, stability_override = _choose_group_publish_result(spec, simulations, research_result)
    selected_result, report_veto = _report_safe_selected_result(simulations, baseline_result, publish_result)
    if stability_override is not None:
        report_veto = stability_override if report_veto is None else {**stability_override, "report_veto": report_veto}
    selected_name = str(selected_result["candidate"])
    score = selected_result["score"]
    nav = selected_result["nav"]
    holdings = selected_result["holdings"]
    rows = _calendar_table(nav)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    table_path = FIGURE_DIR / f"{key}_annual_table.png"
    nav_path = FIGURE_DIR / f"{key}_daily_nav.png"
    _plot_table(rows, f"{spec['name']}月频轮动年度收益", table_path)
    _plot_nav(nav, f"{spec['name']}月频轮动日度净值", spec["name"], nav_path)

    long_short_simulations: list[dict[str, Any]] = []
    for item in simulations:
        item_execution = item.get("execution", {}) or {"mode": "top_equal"}
        item_top_n = int(item_execution.get("top_n", spec["top_n"]))
        ls_nav, ls_holdings = _simulate_long_short(
            source.labels,
            source.daily_returns,
            item["score"],
            spec["label_column"],
            spec["groups"],
            item_top_n,
            source.execution_dates,
            mode=str(item_execution.get("mode", "top_equal")),
            active_share=float(item_execution.get("active_share", 0.50)),
            floor=float(item_execution.get("floor", 0.0)),
        )
        if ls_nav.empty or "date" not in ls_nav.columns:
            continue
        ls_metrics = {"all": _performance(ls_nav), **_split_metrics(ls_nav)}
        ls_metrics["pretest_calendar"] = _pretest_calendar_diagnostics(ls_nav)
        long_short_simulations.append(
            {
                "candidate": item["candidate"],
                "score": item["score"],
                "nav": ls_nav,
                "holdings": ls_holdings,
                "metrics": ls_metrics,
                "objective": _long_short_selection_objective(ls_metrics),
                "execution": item_execution,
                "pretest_calendar": ls_metrics["pretest_calendar"],
                "pure_long_selected": item["candidate"] == selected_name,
            }
        )
    if long_short_simulations:
        long_short_result = _choose_long_short_research_result(long_short_simulations)
        long_short_selected_name = str(long_short_result["candidate"])
        long_short_nav = long_short_result["nav"]
        long_short_holdings = long_short_result["holdings"]
        long_short_metrics = long_short_result["metrics"]
        long_short_rows = _calendar_table_long_short(long_short_nav)
        long_short_candidate_audit = [
            {
                "candidate": item["candidate"],
                "objective": _finite(float(item.get("objective") or float("nan"))),
                "selected": item["candidate"] == long_short_selected_name,
                "pure_long_selected": bool(item.get("pure_long_selected")),
                "execution": item.get("execution", {"mode": "top_equal"}),
                "train": item["metrics"].get("train"),
                "validation": item["metrics"].get("validation"),
                "test_report_only": item["metrics"].get("test"),
                "pretest_calendar": item.get("pretest_calendar") or item["metrics"].get("pretest_calendar"),
                "pretest_gate": _passes_long_short_pretest_gate(item),
            }
            for item in sorted(long_short_simulations, key=lambda row: (float(row.get("objective") or -999.0), str(row.get("candidate") or "")), reverse=True)
        ]
    else:
        long_short_result = None
        long_short_selected_name = selected_name
        long_short_nav = pd.DataFrame()
        long_short_holdings = []
        long_short_metrics = {"all": {}, "train": {}, "validation": {}, "test": {}}
        long_short_rows = []
        long_short_candidate_audit = []
    long_short_table_path = FIGURE_DIR / f"{key}_long_short_annual_table.png"
    long_short_nav_path = FIGURE_DIR / f"{key}_long_short_daily_nav.png"
    if long_short_rows and not long_short_nav.empty:
        _plot_long_short_table(long_short_rows, long_short_table_path)
        _plot_long_short_nav(long_short_nav, long_short_nav_path)

    latest_score = score.dropna(how="all").iloc[-1].dropna().sort_values(ascending=False)
    factor_table = _style_factor_table(bool(spec.get("include_return_technical", False)))
    efficient_factors = _efficient_factor_rows(factor_diagnostics)
    factor_details = _factor_detail_payload(factor_scores, forward, maturities, factor_diagnostics, int(spec["top_n"]))
    factor_corr = _factor_correlation_rows(factor_scores, factor_diagnostics)
    ytd_top_bottom = _top_bottom_rows_from_score(score, forward, int(spec["top_n"]))
    annual_attribution, ytd_monthly_attribution = _style_attribution_tables(nav, holdings, dimensions, spec["groups"])
    candidate_audit = [
        {
            "candidate": item["candidate"],
            "objective": _finite(item["objective"]),
            "selected": item["candidate"] == selected_name,
            "research_selected": item["candidate"] == research_result["candidate"],
            "execution": item.get("execution", {"mode": "top_equal"}),
            "train": item["metrics"].get("train"),
            "validation": item["metrics"].get("validation"),
            "test_report_only": item["metrics"].get("test"),
            "pretest_calendar": item.get("pretest_calendar") or item["metrics"].get("pretest_calendar"),
            "pretest_calendar_gate": _passes_pretest_calendar_gate(item),
        }
        for item in simulations
    ]
    return {
        "name": spec["name"],
        "label_column": spec["label_column"],
        "top_n": spec["top_n"],
        "groups": list(spec["groups"]),
        "selected_candidate": selected_name,
        "research_selected_candidate": research_result["candidate"],
        "report_veto": report_veto,
        "benchmark_source": {
            "label": STANDARD_BENCHMARK_LABEL.get(key, "标准指数基准"),
            **(standard_benchmark_info or {"status": "fallback_internal_stock_pool_benchmark"}),
        },
        "selection_rule": "训练/验证双阶段稳健目标：二级因子先过RankIC与Top-Bottom多空收益双检验，检验失败的一级维度只给中性暴露；新增一级维度自身RankIC与Top-Bottom多空收益质量画像，只有能解释下月风格域收益的维度才在质量候选中放大；候选只用训练/验证年化超额下限、主动Sharpe、回撤、训练验证差异和2016-2021年度稳定门排序；在线稳定选择只读取信号日前已实现表现；测试期只报告，不参与候选排名。",
        "model": "五因子框架：股票先按季度风格标签聚合为12风格箱/大中小/四风格；风格域不再使用行业专属景气度，只保留基本面、技术面、估值、资金面、拥挤度五类原子因子。原子因子做PIT月频RankIC检验和Top-Bottom下月收益检验，训练期定方向，验证期定准入，未通过准入的维度中性化；随后再检验五个一级维度自身的RankIC和多空收益，用等权、RankIC、OLS、Lasso和质量加权候选在训练/验证期择优生成最终风格轮动得分。",
        "flow_steps": _flow_steps_for_style(),
        "processing_steps": _processing_steps_for_style(),
        "test_steps": _test_steps_for_style(),
        "score_steps": _score_steps_for_style(),
        "style_box_method": _style_box_method_rows(),
        "score_model": selected_name,
        "long_short_selected_candidate": long_short_selected_name,
        "long_short_selection_rule": "多空策略不再沿用纯多头胜出候选，而是在同一批预注册五因子候选中，用训练期+验证期的多空年化收益、Sharpe、回撤、训练验证稳定性和年度胜率单独择优；测试期只展示，不参与排名。",
        "long_short_candidate_audit": long_short_candidate_audit,
        "factor_count": {
            "fundamental": len(FUNDAMENTAL_FIELDS),
            "technical": len(TECHNICAL_FIELDS)
            + (len(STYLE_RETURN_TECHNICAL_FIELDS) if spec.get("include_return_technical") else 0),
            "valuation": len(VALUATION_FIELDS),
            "funds": len(FUNDS_FIELDS),
            "crowding": len(CROWDING_FIELDS),
        },
        "admitted_factor_count": factor_diagnostics.get("admitted_factor_count", {}),
        "latest_signal_date": _iso(score.dropna(how="all").index[-1]),
        "latest_ranking": [
            {"rank": rank, "name": str(name), "score": _finite(value)}
            for rank, (name, value) in enumerate(latest_score.items(), start=1)
        ],
        "latest_holding": holdings[-1] if holdings else {},
        "holdings": holdings,
        "metrics": selected_result["metrics"],
        "candidate_audit": candidate_audit,
        "factor_diagnostics": factor_diagnostics,
        "factor_table": factor_table,
        "efficient_factors": efficient_factors,
        "factor_corr": factor_corr,
        "factor_details": factor_details,
        "ytd_top_bottom": ytd_top_bottom,
        "long_short_metrics": long_short_metrics,
        "long_short_calendar_year": [
            {item_key: (_finite(value) if isinstance(value, float) else value) for item_key, value in row.items()}
            for row in long_short_rows
        ],
        "long_short_holdings": long_short_holdings,
        "long_short_figures": {"annual_table": str(long_short_table_path), "daily_nav": str(long_short_nav_path)} if long_short_rows and not long_short_nav.empty else {},
        "annual_attribution": annual_attribution,
        "ytd_monthly_attribution": ytd_monthly_attribution,
        "calendar_year": [
            {key: (_finite(value) if isinstance(value, float) else value) for key, value in row.items()}
            for row in rows
        ],
        "figures": {"annual_table": str(table_path), "daily_nav": str(nav_path)},
        "nav": [
            {
                "date": _iso(row.date),
                "strategy": _finite(row.strategy_nav),
                "benchmark": _finite(row.benchmark_nav),
                "excess": _finite(row.excess_nav),
            }
            for row in nav.itertuples()
        ],
        "long_short_nav": [
            {
                "date": _iso(row.date),
                "top": _finite(row.long_nav),
                "bottom": _finite(row.bottom_nav),
                "strategy": _finite(row.strategy_nav),
                "benchmark": 1.0,
                "excess": _finite(row.excess_nav),
            }
            for row in long_short_nav.itertuples()
        ] if not long_short_nav.empty else [],
    }


def build() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = _load_sources()
    standard_benchmark_returns, standard_benchmark_info = _load_standard_benchmark_returns(source.trade_dates)
    strategies = {
        key: _run_group(source, key, spec, standard_benchmark_returns.get(key), standard_benchmark_info)
        for key, spec in GROUP_SPECS.items()
    }
    payload = {
        "schema_version": "1.2",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_as_of": _iso(source.trade_dates.max()),
        "signal_count": int(len(source.signal_dates)),
        "frequency": "monthly",
        "timing": "每个自然月最后一个可用交易日收盘形成信号；若当月仍在进行中，则用数据库最新交易日形成最新预案；下一交易日收盘执行；日度净值从执行日后第一个交易日开始计算。",
        "splits": SPLITS,
        "data_contract": {
            "benchmark": standard_benchmark_info,
            "style_label": "季度更新；流通市值划分大/中/小，红利优先，剩余按成长分位-价值分位划分价值/均衡/成长。",
            "fundamental": "financial_report_visible.visible_date严格早于信号日；超过550天的财报字段置空。",
            "technical": "股票复权价格计算多期限动量、风险调整动量、路径效率、均线距离、上涨扩散、低波回撤韧性和风格域自身成熟收益因子。",
            "valuation": "股票PE/PB/PS/股息率转换为收益率、估值修复和历史36月分位后聚合。",
            "funds": "股票主力净流入、大单、超大单、聪明资金结构、资金加速度和残差结构按成交额归一后聚合。",
            "crowding": "换手、量比、成交热度、涨停热度、价格偏离、波动扩张、资金集中和流动性冲击作为连续低拥挤惩罚项。",
        },
        "flow_steps": _flow_steps_for_style(),
        "processing_steps": _processing_steps_for_style(),
        "test_steps": _test_steps_for_style(),
        "score_steps": _score_steps_for_style(),
        "style_box_method": _style_box_method_rows(),
        "strategies": strategies,
    }
    for output_path in DATA_OUTPUTS:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT_DIR / "style_six_dimension_monthly.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    payload = build()
    compact = {
        key: {
            "name": value["name"],
            "latest_holding": value["latest_holding"].get("selected"),
            "test": value["metrics"].get("test"),
            "figures": value["figures"],
        }
        for key, value in payload["strategies"].items()
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
