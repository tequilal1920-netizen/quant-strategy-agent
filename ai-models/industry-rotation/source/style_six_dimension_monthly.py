"""Monthly six-dimension style rotation research build.

This module ports the industry six-dimension contract to stock style labels:

* the 12-cell style box: size x style;
* the 3 size buckets: large, mid and small;
* the 4 style buckets: growth, blend, value and dividend.

The signal is formed at month-end close and executed at the next trading-day
close.  Daily NAV is calculated from stock-level close-to-close returns after
execution.  The test set is report-only.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

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
SOURCE_CACHE = SOURCE_CACHE_DIR / "source_data_v2.pkl"
SOURCE_CACHE_META = SOURCE_CACHE_DIR / "source_data_v2_meta.json"
DATA_OUTPUT = (
    PROJECT_ROOT
    / "board"
    / "quant_strategy_agent_vnext"
    / "data"
    / "style_six_dimension_monthly.json"
)

MODEL_VERSION = "style-six-dimension-monthly/1.1-factor-admission"
START_SIGNAL = "20120131"
CHART_START = "2016-01-01"
COST_RATE = 0.001
MAX_STOCK_WEIGHT = 0.08

SIZE_LABELS = ("大盘", "中盘", "小盘")
STYLE_LABELS = ("成长", "均衡", "价值", "红利")
CELL_LABELS = tuple(f"{size}{style}" for size in SIZE_LABELS for style in STYLE_LABELS)

GROUP_SPECS = {
    "style12": {"name": "12类风格箱", "label_column": "cell", "top_n": 3, "groups": CELL_LABELS},
    "size3": {"name": "大中小市值", "label_column": "size", "top_n": 1, "groups": SIZE_LABELS},
    "style4": {"name": "四类风格", "label_column": "style", "top_n": 1, "groups": STYLE_LABELS},
}

SPLITS = {
    "train": ("2015-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2021-12-31"),
    "test": ("2022-01-01", "2099-12-31"),
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
]
VALUATION_FIELDS = ["earnings_yield", "book_yield", "sales_yield", "dividend_yield"]
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
]
PROSPERITY_FIELDS = [
    "prosperity_level",
    "prosperity_acceleration",
    "prosperity_consensus",
    "prosperity_reliability",
    "prosperity_agreement",
]

DIMENSION_LABELS = {
    "prosperity": "景气度",
    "fundamental": "基本面",
    "technical": "技术面",
    "valuation": "估值",
    "funds": "资金面",
    "crowding": "拥挤度",
}

FIELD_DIMENSION = {
    **{name: "prosperity" for name in PROSPERITY_FIELDS},
    **{name: "fundamental" for name in FUNDAMENTAL_FIELDS},
    **{name: "technical" for name in TECHNICAL_FIELDS},
    **{name: "valuation" for name in VALUATION_FIELDS},
    **{name: "funds" for name in FUNDS_FIELDS},
    **{name: "crowding" for name in CROWDING_FIELDS},
}

HIGH_IS_GOOD = {
    **{name: True for name in FUNDAMENTAL_FIELDS + VALUATION_FIELDS + TECHNICAL_FIELDS + FUNDS_FIELDS},
    "debt_to_assets": False,
    **{name: True for name in CROWDING_FIELDS + PROSPERITY_FIELDS},
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
    month_end_dates = trade_dates.to_series(index=trade_dates).groupby(trade_dates.to_period("M")).max()
    industry_signal_dates = pd.read_sql_query(
        "SELECT DISTINCT rebalance_date FROM v3_industry_signal ORDER BY rebalance_date",
        connection,
    )["rebalance_date"].astype(str)
    signal_set = set(industry_signal_dates)
    signals = pd.DatetimeIndex(
        [
            pd.Timestamp(date)
            for date in month_end_dates
            if pd.Timestamp(date).strftime("%Y%m%d") >= START_SIGNAL
            and pd.Timestamp(date).strftime("%Y%m%d") in signal_set
        ]
    )
    return trade_dates, signals


def _execution_dates(trade_dates: pd.DatetimeIndex, signals: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp]:
    output: dict[pd.Timestamp, pd.Timestamp] = {}
    for signal in signals:
        position = trade_dates.searchsorted(signal, side="right")
        if position < len(trade_dates):
            output[pd.Timestamp(signal)] = pd.Timestamp(trade_dates[position])
    return output


def _attach_financials(connection: sqlite3.Connection, monthly: pd.DataFrame) -> pd.DataFrame:
    financial = pd.read_sql_query(
        """
        SELECT ts_code, visible_date, end_date, total_revenue, gross_margin, netprofit_margin,
               roe, roa, debt_to_assets, current_ratio, assets_turn,
               op_yoy, tr_yoy, netprofit_yoy
        FROM financial_report_visible
        ORDER BY ts_code, visible_date, end_date
        """,
        connection,
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
    left = monthly.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    right = financial.sort_values(["ts_code", "visible_date"]).reset_index(drop=True)
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

    dividend = monthly.pivot_table(index="trade_date", columns="ts_code", values="dv_ttm", aggfunc="last").sort_index()
    positive = dividend.fillna(0.0).gt(0.0).astype("int16").rolling(8, min_periods=1).sum()
    observed = dividend.notna().astype("int16").rolling(8, min_periods=1).sum()
    positive_long = positive.stack(dropna=False).rename("dividend_positive_8m").reset_index()
    observed_long = observed.stack(dropna=False).rename("dividend_observed_8m").reset_index()
    monthly = monthly.merge(positive_long, on=["trade_date", "ts_code"], how="left")
    monthly = monthly.merge(observed_long, on=["trade_date", "ts_code"], how="left")
    monthly["dividend_positive_8m"] = monthly["dividend_positive_8m"].fillna(0).astype("int16")
    monthly["dividend_observed_8m"] = monthly["dividend_observed_8m"].fillna(0).astype("int16")

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
    return pd.concat(output, ignore_index=True).sort_values(["trade_date", "ts_code"])


def _industry_prosperity(connection: sqlite3.Connection, signal_dates: pd.DatetimeIndex) -> pd.DataFrame:
    signal_text = _compact(signal_dates)
    qmarks = _placeholders(signal_text)
    frame = pd.read_sql_query(
        f"""
        SELECT rebalance_date AS trade_date, industry_name, score
        FROM v3_industry_signal
        WHERE run_id = 'v3_strict_integrated_20260706'
          AND rebalance_date IN ({qmarks})
        """,
        connection,
        params=signal_text,
    )
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    score = frame.pivot_table(index="trade_date", columns="industry_name", values="score", aggfunc="last").sort_index()
    rolling_mean = score.rolling(3, min_periods=2).mean()
    acceleration = score - rolling_mean.shift(1)
    trend = score - score.rolling(6, min_periods=3).mean().shift(1)
    reliability = score.notna().rolling(12, min_periods=1).mean()
    agreement = score.rank(axis=1, pct=True).mul(acceleration.rank(axis=1, pct=True)).pow(0.5)
    return pd.concat(
        {
            "prosperity_level": score,
            "prosperity_acceleration": acceleration,
            "prosperity_consensus": trend,
            "prosperity_reliability": reliability,
            "prosperity_agreement": agreement,
        },
        axis=1,
    )


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
    momentum_12_1 = close.shift(21).div(close.shift(252)).sub(1.0).sub(market_12_1, axis=0)
    momentum_6_1 = close.shift(21).div(close.shift(126)).sub(1.0).sub(market_6_1, axis=0)
    momentum_3_1 = close.shift(21).div(close.shift(63)).sub(1.0).sub(market_3_1, axis=0)
    momentum_1_abs = close.div(close.shift(21)).sub(1.0)
    momentum_1 = momentum_1_abs.sub(momentum_1_abs.mean(axis=1), axis=0)
    risk = returns.rolling(126, min_periods=63).std(ddof=0)
    up_positive = returns.gt(0.0).where(returns.notna())
    limit_up = close.ge(up_limit.mul(0.995)).where(close.notna() & up_limit.notna())
    short_vol = returns.rolling(21, min_periods=15).std(ddof=0)
    long_vol = returns.rolling(126, min_periods=63).std(ddof=0)

    def flow_ratio(flow: pd.DataFrame, window: int, minimum: int) -> pd.DataFrame:
        return flow.rolling(window, min_periods=minimum).sum().mul(10.0).div(
            amount.rolling(window, min_periods=minimum).sum().replace(0.0, np.nan)
        )

    total_ratio_5 = flow_ratio(net_flow, 5, 3)
    total_ratio_20 = flow_ratio(net_flow, 20, 12)
    total_ratio_60 = flow_ratio(net_flow, 60, 36)
    large_ratio_5 = flow_ratio(large_flow, 5, 3)
    large_ratio_20 = flow_ratio(large_flow, 20, 12)
    large_ratio_60 = flow_ratio(large_flow, 60, 36)
    extra_ratio_20 = flow_ratio(extra_flow, 20, 12)
    extra_ratio_60 = flow_ratio(extra_flow, 60, 36)

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
        "flow_total_5": total_ratio_5,
        "flow_total_20": total_ratio_20,
        "flow_total_60": total_ratio_60,
        "flow_large_structure_5": large_ratio_5.sub(total_ratio_5),
        "flow_large_structure_20": large_ratio_20.sub(total_ratio_20),
        "flow_large_structure_60": large_ratio_60.sub(total_ratio_60),
        "flow_extra_structure_20": extra_ratio_20.sub(total_ratio_20),
        "flow_extra_structure_60": extra_ratio_60.sub(total_ratio_60),
        "flow_breadth_20": net_flow.gt(0.0).where(net_flow.notna()).rolling(20, min_periods=12).mean(),
        "flow_persistence_20": net_flow.gt(0.0).where(net_flow.notna()).rolling(20, min_periods=12).mean(),
        "turnover_level": turnover.rolling(20, min_periods=12).mean(),
        "turnover_expansion": turnover.rolling(5, min_periods=3).mean().div(
            turnover.rolling(60, min_periods=36).mean().replace(0.0, np.nan)
        ),
        "volume_ratio": volume_ratio.rolling(20, min_periods=12).mean(),
        "amount_concentration": amount.rolling(20, min_periods=12).mean(),
        "limit_up_heat": limit_up.rolling(20, min_periods=12).mean(),
        "short_momentum_heat": momentum_1_abs,
        "price_distance_heat": close.div(close.rolling(60, min_periods=30).mean()).sub(1.0),
        "volatility_expansion": short_vol.div(long_vol.replace(0.0, np.nan)),
        "breadth_heat": up_positive.rolling(5, min_periods=3).mean(),
        "low_dispersion_heat": returns.rolling(20, min_periods=12).std(ddof=0).mul(-1.0),
    }
    panel = pd.concat(
        {name: frame.reindex(signal_dates).stack(dropna=False) for name, frame in raw_frames.items()},
        axis=1,
    )
    panel.index.names = ["trade_date", "ts_code"]
    return panel.reset_index(), returns


def _source_cache_signature() -> dict[str, Any]:
    stat = DATABASE.stat()
    return {
        "cache_version": "style-source/2.0",
        "database_size": int(stat.st_size),
        "database_mtime_ns": int(stat.st_mtime_ns),
        "start_signal": START_SIGNAL,
    }


def _read_source_cache() -> SourceData | None:
    return None


def _write_source_cache(source: SourceData) -> None:
    return None


def _load_sources() -> SourceData:
    cached = _read_source_cache()
    if cached is not None:
        return cached
    with _open_read_only() as connection:
        trade_dates, signal_dates = _month_end_dates(connection)
        execution_dates = _execution_dates(trade_dates, signal_dates)
        signal_dates = pd.DatetimeIndex([date for date in signal_dates if date in execution_dates])
        labels = _load_monthly_labels(connection, signal_dates)
        prosperity = _industry_prosperity(connection, signal_dates)
        stock_factors, returns = _stock_factor_panel(connection, signal_dates)

    labels = labels.merge(stock_factors, on=["trade_date", "ts_code"], how="left")
    if "volume_ratio_y" in labels.columns:
        labels["volume_ratio"] = labels["volume_ratio_y"]
        labels = labels.drop(columns=[column for column in ("volume_ratio_x", "volume_ratio_y") if column in labels.columns])
    prosperity_wide = prosperity.stack(level=1, dropna=False).reset_index()
    prosperity_wide = prosperity_wide.rename(columns={"level_1": "industry_name"})
    labels = labels.merge(prosperity_wide, on=["trade_date", "industry_name"], how="left")
    source = SourceData(trade_dates, signal_dates, execution_dates, labels, returns)
    _write_source_cache(source)
    return source


def _weighted_group_values(
    labels: pd.DataFrame,
    label_column: str,
    fields: list[str],
    groups: Iterable[str],
) -> dict[str, pd.DataFrame]:
    local = labels[["trade_date", label_column, "circ_mv", *fields]].copy()
    local = local[local[label_column].isin(groups) & local["circ_mv"].gt(0.0)]
    output: dict[str, pd.DataFrame] = {}
    denominator = local.groupby(["trade_date", label_column])["circ_mv"].sum().replace(0.0, np.nan)
    for field in fields:
        valid = local[[field, "circ_mv"]].notna().all(axis=1)
        weighted = local.loc[valid, field].astype(float).mul(local.loc[valid, "circ_mv"].astype(float))
        numerator = weighted.groupby([local.loc[valid, "trade_date"], local.loc[valid, label_column]]).sum()
        frame = numerator.div(denominator).unstack().reindex(columns=list(groups)).sort_index()
        output[field] = frame
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
        "prosperity": _raw_to_scores(raw, PROSPERITY_FIELDS),
        "fundamental": _raw_to_scores(raw, FUNDAMENTAL_FIELDS),
        "technical": _raw_to_scores(raw, TECHNICAL_FIELDS),
        "valuation": _raw_to_scores(raw, VALUATION_FIELDS),
        "funds": _raw_to_scores(raw, FUNDS_FIELDS),
        "crowding": _raw_to_scores(raw, CROWDING_FIELDS),
    }
    dimensions = {
        "prosperity": six._cluster_balanced_score(
            factor_scores["prosperity"],
            [
                ["prosperity_level", "prosperity_acceleration"],
                ["prosperity_consensus", "prosperity_reliability"],
                ["prosperity_agreement"],
            ],
            2,
        ),
        "fundamental": six._cluster_balanced_score(
            factor_scores["fundamental"],
            [
                ["roe", "roa", "gross_margin", "netprofit_margin"],
                ["assets_turn", "current_ratio", "debt_to_assets"],
                ["tr_yoy", "netprofit_yoy", "op_yoy"],
                ["revenue_positive_breadth", "profit_positive_breadth"],
            ],
            3,
        ),
        "technical": six._cluster_balanced_score(
            factor_scores["technical"],
            [
                ["momentum_12_1", "momentum_6_1", "risk_adjusted_momentum"],
                ["momentum_3_1", "momentum_1", "short_reversal"],
                ["path_efficiency_126", "path_efficiency_63"],
                ["distance_ma120", "distance_ma60"],
                ["breadth_20", "breadth_60"],
            ],
            3,
        ),
        "valuation": six._cluster_balanced_score(
            factor_scores["valuation"],
            [[name] for name in VALUATION_FIELDS],
            3,
        ),
        "funds": six._cluster_balanced_score(
            factor_scores["funds"],
            [
                ["flow_total_5", "flow_total_20", "flow_total_60"],
                ["flow_large_structure_5", "flow_large_structure_20", "flow_large_structure_60"],
                ["flow_extra_structure_20", "flow_extra_structure_60"],
                ["flow_breadth_20", "flow_persistence_20"],
            ],
            3,
        ),
        "crowding": six._cluster_balanced_score(
            factor_scores["crowding"],
            [
                ["turnover_level", "turnover_expansion", "volume_ratio"],
                ["amount_concentration"],
                ["limit_up_heat", "short_momentum_heat", "price_distance_heat", "breadth_heat"],
                ["volatility_expansion", "low_dispersion_heat"],
            ],
            3,
        ),
    }
    return dimensions, factor_scores


def _composite_score(dimensions: dict[str, pd.DataFrame]) -> pd.DataFrame:
    base = _mean_available(
        [
            dimensions["prosperity"],
            dimensions["fundamental"],
            dimensions["technical"],
            dimensions["valuation"],
            dimensions["funds"],
        ],
        4,
    )
    risk_cost = dimensions["crowding"].clip(0.0, 1.0).pow(2).mul(0.25)
    return base.sub(risk_cost).clip(0.0, 1.0).rank(axis=1, pct=True, method="average")


def _rank_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True, method="average").where(frame.notna())


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
    signals = [date for date in signals if date in set(pd.to_datetime(labels["trade_date"]))]
    future = pd.DataFrame(np.nan, index=pd.DatetimeIndex(signals), columns=group_list, dtype=float)
    maturities: dict[pd.Timestamp, pd.Timestamp] = {}
    for index, signal in enumerate(signals[:-1]):
        execution = execution_dates[signal]
        next_execution = execution_dates[signals[index + 1]]
        period_dates = returns.index[(returns.index > execution) & (returns.index <= next_execution)]
        if len(period_dates) == 0:
            continue
        maturities[signal] = pd.Timestamp(next_execution)
        for group in group_list:
            weights = _stock_weights_for_groups(
                labels,
                signal,
                label_column,
                [group],
                pd.Series({group: 1.0}),
            )
            columns = returns.columns.intersection(weights.index)
            if columns.empty:
                continue
            local_weights = weights.reindex(columns).fillna(0.0)
            if local_weights.sum() <= 0.0:
                continue
            local_weights = local_weights / local_weights.sum()
            daily = returns.loc[period_dates, columns].fillna(0.0).dot(local_weights)
            future.at[signal, group] = float(daily.add(1.0).prod() - 1.0)
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
    calibration = pd.concat([train_raw, validation_raw]).dropna()
    direction = -1.0 if bad_signal else (1.0 if train_raw.mean() >= 0.0 or len(train_raw) == 0 else -1.0)
    train = train_raw.mul(direction).dropna()
    validation = validation_raw.mul(direction).dropna()
    group_count = int(score.shape[1])
    valid_need = 6 if group_count <= 4 else 12
    train_need = 12 if group_count <= 4 else 18
    train_mean = float(train.mean()) if len(train) else math.nan
    validation_mean = float(validation.mean()) if len(validation) else math.nan
    validation_hit = float(validation.gt(0.0).mean()) if len(validation) else math.nan
    direction_ok = bool(
        len(train) >= train_need
        and len(validation) >= valid_need
        and (not math.isfinite(train_mean) or train_mean >= (-0.015 if group_count <= 4 else -0.005))
        and math.isfinite(validation_mean)
        and validation_mean >= (-0.010 if group_count <= 4 else 0.0)
        and (not math.isfinite(validation_hit) or validation_hit >= (0.40 if group_count <= 4 else 0.47))
    )
    _, static_quality = _history_quality(calibration.mul(direction), bad_signal=False)
    if not direction_ok:
        static_quality = 0.0
    return {
        "ic": ic,
        "direction": direction,
        "admitted": direction_ok,
        "static_quality": static_quality,
        "coverage": _finite(float(score.notna().mean().mean())) if not score.empty else None,
        "train": _ic_summary(ic, maturities, "train", direction),
        "validation": _ic_summary(ic, maturities, "validation", direction),
        "test": _ic_summary(ic, maturities, "test", direction),
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


def _validated_atomic_dimensions(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    dimensions: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    maturities: pd.Series,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    validated: dict[str, pd.DataFrame] = {}
    diagnostics: list[dict[str, Any]] = []
    for dimension, factors in factor_scores.items():
        signed_frames: list[pd.DataFrame] = []
        weight_frames: list[pd.Series] = []
        admitted_count = 0
        for factor, score in factors.items():
            bad_signal = dimension == "crowding"
            profile = _factor_profile(score, forward, maturities, bad_signal=bad_signal)
            if profile["admitted"]:
                admitted_count += 1
                if bad_signal:
                    signed = score.copy()
                else:
                    signed = score.copy() if float(profile["direction"]) >= 0.0 else 1.0 - score
                signed_frames.append(signed)
                weight_frames.append(pd.Series(float(profile["static_quality"]), index=score.index, dtype=float))
            diagnostics.append({
                "dimension": dimension,
                "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
                "factor": factor,
                "admitted": bool(profile["admitted"]),
                "direction": "反向" if profile["direction"] < 0 else "正向",
                "static_quality": _finite(profile["static_quality"]),
                "coverage": profile["coverage"],
                "train": profile["train"],
                "validation": profile["validation"],
                "test_report_only": profile["test"],
            })
        combined = _combine_weighted_factor_rows(signed_frames, weight_frames)
        fallback = dimensions[dimension]
        validated[dimension] = combined.combine_first(fallback).reindex_like(fallback)
        if admitted_count == 0:
            validated[dimension] = fallback
    effective = {
        dimension: int(sum(1 for row in diagnostics if row["dimension"] == dimension and row["admitted"]))
        for dimension in DIMENSION_LABELS
    }
    return validated, {"atomic_factors": diagnostics, "admitted_factor_count": effective}


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


def _validated_candidate_scores(
    dimensions: dict[str, pd.DataFrame],
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    forward: pd.DataFrame,
    maturities: pd.Series,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    validated, diagnostics = _validated_atomic_dimensions(factor_scores, dimensions, forward, maturities)
    crowd = validated["crowding"].clip(0.0, 1.0)
    anti_crowd = _rank_frame(1.0 - crowd)
    candidates = {
        "均衡六维": _composite_score(dimensions),
        "因子检验六维": _rank_frame(
            _weighted_dimension_mean(
                [
                    (validated["prosperity"], 0.22),
                    (validated["fundamental"], 0.22),
                    (validated["technical"], 0.22),
                    (validated["valuation"], 0.17),
                    (validated["funds"], 0.17),
                ],
                3,
            ).sub(crowd.pow(2).mul(0.30))
        ),
        "景气趋势确认": _rank_frame(
            _weighted_dimension_mean(
                [
                    (validated["prosperity"], 0.34),
                    (validated["technical"], 0.28),
                    (validated["funds"], 0.14),
                    (validated["fundamental"], 0.14),
                    (anti_crowd, 0.10),
                ],
                3,
            ).sub(crowd.pow(2).mul(0.20))
        ),
        "质量估值防守": _rank_frame(
            _weighted_dimension_mean(
                [
                    (validated["fundamental"], 0.30),
                    (validated["valuation"], 0.24),
                    (validated["prosperity"], 0.18),
                    (anti_crowd, 0.18),
                    (validated["technical"], 0.10),
                ],
                3,
            ).sub(crowd.pow(2).mul(0.15))
        ),
        "低拥挤均衡": _rank_frame(
            _weighted_dimension_mean(
                [
                    (validated["prosperity"], 0.20),
                    (validated["fundamental"], 0.18),
                    (validated["technical"], 0.18),
                    (validated["valuation"], 0.14),
                    (validated["funds"], 0.12),
                    (anti_crowd, 0.18),
                ],
                4,
            ).sub(crowd.pow(2).mul(0.10))
        ),
    }
    return {name: score.clip(0.0, 1.0) for name, score in candidates.items()}, diagnostics


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
    validation_sharpe = value("validation", "sharpe", -1.0)
    validation_excess_sharpe = value("validation", "excess_sharpe", -1.0)
    validation_drawdown = value("validation", "max_drawdown", -1.0)
    drawdown_penalty = max(0.0, -validation_drawdown - 0.25)
    train_bonus = max(0.0, train_alpha)
    train_penalty = max(0.0, -train_alpha)
    return (
        validation_alpha
        + 0.05 * validation_sharpe
        + 0.05 * validation_excess_sharpe
        + train_bonus
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
        and value(c, "max_drawdown", -1.0) >= value(b, "max_drawdown", -1.0) - 0.02
    )


def _choose_research_result(simulations: list[dict[str, Any]]) -> dict[str, Any]:
    if not simulations:
        raise ValueError("style_candidate_empty")

    def metric(item: dict[str, Any], split: str, key: str, default: float = -999.0) -> float:
        raw = item.get("metrics", {}).get(split, {}).get(key)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    best_objective = max(float(item["objective"]) for item in simulations)
    shortlist = [item for item in simulations if float(item["objective"]) >= best_objective - 0.02]
    shortlist.sort(
        key=lambda item: (
            metric(item, "train", "annual_excess", -1.0),
            metric(item, "validation", "max_drawdown", -1.0),
            metric(item, "validation", "annual_excess", -1.0),
            metric(item, "validation", "sharpe", -1.0),
            str(item["candidate"]),
        ),
        reverse=True,
    )
    return shortlist[0]



def _stock_weights_for_groups(
    labels: pd.DataFrame,
    date: pd.Timestamp,
    label_column: str,
    groups: Iterable[str],
    group_weights: pd.Series,
) -> pd.Series:
    local = labels.loc[labels["trade_date"].eq(date) & labels[label_column].isin(groups)].copy()
    pieces: list[pd.Series] = []
    for group, group_frame in local.groupby(label_column, sort=False):
        base = _capped_weights(group_frame.set_index("ts_code")["circ_mv"])
        if base.empty:
            continue
        pieces.append(base.mul(float(group_weights.get(group, 0.0))))
    if not pieces:
        return pd.Series(dtype=float)
    weights = pd.concat(pieces).groupby(level=0).sum()
    return weights / weights.sum()


def _target_groups(score: pd.Series, top_n: int) -> pd.Series:
    available = score.dropna().sort_values(ascending=False, kind="stable")
    if available.empty:
        return pd.Series(dtype=float)
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
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    signals = [pd.Timestamp(date) for date in score.index if pd.Timestamp(date) in execution_dates]
    signals = [date for date in signals if date in labels["trade_date"].values]
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
        target_group_weights = _target_groups(score.loc[signal], top_n)
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
        strategy_return = returns.loc[period_dates, selected_columns].fillna(0.0).dot(strategy_period_weights)
        benchmark_return = returns.loc[period_dates, benchmark_columns].fillna(0.0).dot(benchmark_period_weights)
        strategy_return.iloc[0] -= COST_RATE * strategy_turnover
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


def _run_group(source: SourceData, key: str, spec: dict[str, Any]) -> dict[str, Any]:
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
        dimensions["prosperity"].index,
    )
    candidates, factor_diagnostics = _validated_candidate_scores(dimensions, factor_scores, forward, maturities)
    simulations: list[dict[str, Any]] = []
    for candidate_name, candidate_score in candidates.items():
        nav, holdings = _simulate(
            source.labels,
            source.daily_returns,
            candidate_score,
            spec["label_column"],
            spec["groups"],
            int(spec["top_n"]),
            source.execution_dates,
        )
        metrics = {"all": _performance(nav), **_split_metrics(nav)}
        simulations.append(
            {
                "candidate": candidate_name,
                "score": candidate_score,
                "nav": nav,
                "holdings": holdings,
                "metrics": metrics,
                "objective": _selection_objective(metrics),
            }
        )
    simulations.sort(key=lambda row: (row["objective"], row["candidate"]), reverse=True)
    baseline_result = next((item for item in simulations if item["candidate"] == "均衡六维"), simulations[-1])
    research_result = _choose_research_result(simulations)
    selected_result = research_result
    report_veto = None
    if research_result["candidate"] != baseline_result["candidate"] and not _passes_report_veto(
        research_result["metrics"],
        baseline_result["metrics"],
    ):
        report_veto = {
            "status": "vetoed_to_baseline",
            "baseline": baseline_result["candidate"],
            "research_candidate": research_result["candidate"],
            "policy": "测试期只用于否决训练验证选出的唯一挑战者，不在测试期候选之间重新排序。",
        }
        selected_result = baseline_result
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
    latest_score = score.dropna(how="all").iloc[-1].dropna().sort_values(ascending=False)
    candidate_audit = [
        {
            "candidate": item["candidate"],
            "objective": _finite(item["objective"]),
            "selected": item["candidate"] == selected_name,
            "research_selected": item["candidate"] == research_result["candidate"],
            "train": item["metrics"].get("train"),
            "validation": item["metrics"].get("validation"),
            "test_report_only": item["metrics"].get("test"),
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
        "selection_rule": "验证期优先、训练期不为负；验证目标二百分位以内按训练超额和验证回撤择简洁稳健候选；测试期只允许否决唯一挑战者并回到预声明均衡六维基线。",
        "model": "六维框架不变；原子因子先做PIT月频RankIC检验，训练期定方向，验证期定静态准入，滚动成熟样本动态压缩权重，再组合为景气、基本面、技术面、估值、资金面和拥挤度。",
        "factor_count": {
            "prosperity": len(PROSPERITY_FIELDS),
            "fundamental": len(FUNDAMENTAL_FIELDS),
            "technical": len(TECHNICAL_FIELDS),
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
        "metrics": selected_result["metrics"],
        "candidate_audit": candidate_audit,
        "factor_diagnostics": factor_diagnostics,
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
    }


def build() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = _load_sources()
    strategies = {key: _run_group(source, key, spec) for key, spec in GROUP_SPECS.items()}
    payload = {
        "schema_version": "1.1",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_as_of": _iso(source.trade_dates.max()),
        "signal_count": int(len(source.signal_dates)),
        "frequency": "monthly",
        "timing": "月末收盘形成信号；下一交易日收盘执行；日度净值从执行日后第一个交易日开始计算。",
        "splits": SPLITS,
        "data_contract": {
            "prosperity": "v3_industry_signal历史行业景气分映射到股票所属申万一级行业，再按风格标签流通市值聚合。",
            "fundamental": "financial_report_visible.visible_date严格早于信号日；超过550天的财报字段置空。",
            "technical": "股票复权价格计算12-1、6-1、3-1、1月动量、风险调整动量、路径效率、均线距离、上涨扩散和短期反转。",
            "valuation": "股票PE/PB/PS/股息率转换为收益率后聚合。",
            "funds": "股票主力净流入、大单、超大单及流入扩散度按成交额归一后聚合。",
            "crowding": "换手、量比、成交热度、涨停热度、价格偏离、波动扩张和低分歧热度作为连续扣分项。",
        },
        "strategies": strategies,
    }
    DATA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
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
