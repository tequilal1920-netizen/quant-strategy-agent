"""Point-in-time six-dimension SW level-1 industry rotation model.

The module augments the existing industry-specific prosperity engine with five
independently sourced dimensions and a separate crowding-risk penalty:

* prosperity: the existing 31 x 8 business-indicator engine;
* fundamentals: financial statements whose visible date is not later than the
  signal date;
* technical: industry relative momentum, trend efficiency and stock breadth;
* valuation: positive earnings/book/sales yields and dividend yield;
* funds: stock money-flow aggregates divided by traded amount;
* crowding: turnover, volume, concentration, breadth heat and price risk.

All database access is read-only.  Formal reusable aggregates are cached below
``output/industry_rotation/cache/market`` together with a source manifest.
The test interval is reported only and is never used to choose a factor sign,
weight or candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


MODEL_VERSION = "industry-rotation/5.5-layered-return-regime-gated-six-dimension"
CACHE_VERSION = "six-dimension-inputs/1.2"
ROOT = Path(__file__).resolve().parent


def _resolve_project_root(root: Path) -> Path:
    for candidate in (root, *root.parents):
        if (candidate / "database" / "research_warehouse.db").exists() and (candidate / "board").exists():
            return candidate
    return root.parents[1]


PROJECT_ROOT = _resolve_project_root(ROOT)
WAREHOUSE = PROJECT_ROOT / "database" / "research_warehouse.db"
CACHE_DIR = PROJECT_ROOT / "output" / "industry_rotation" / "cache" / "market"
DAILY_CACHE = CACHE_DIR / "pit_six_dimension_daily.csv.gz"
MONTHLY_CACHE = CACHE_DIR / "pit_six_dimension_monthly.csv.gz"
MANIFEST = CACHE_DIR / "pit_six_dimension_manifest.json"

DIMENSION_LABELS = {
    "prosperity": "景气度",
    "fundamental": "基本面",
    "technical": "技术面",
    "valuation": "估值",
    "funds": "资金面",
    "crowding": "拥挤度",
    "anti_crowding": "低拥挤",
}

FACTOR_LABELS = {
    "prosperity_level": "景气水平",
    "prosperity_acceleration": "景气加速度",
    "prosperity_consensus": "景气口径共识",
    "prosperity_reliability": "景气数据可靠性",
    "prosperity_agreement": "景气模型一致度",
    "roe": "净资产收益率",
    "roa": "总资产收益率",
    "gross_margin": "毛利率",
    "netprofit_margin": "净利率",
    "assets_turn": "资产周转率",
    "current_ratio": "流动比率",
    "debt_to_assets": "低资产负债率",
    "tr_yoy": "营业收入增速",
    "netprofit_yoy": "归母净利润增速",
    "op_yoy": "营业利润增速",
    "revenue_positive_breadth": "收入正增长扩散度",
    "profit_positive_breadth": "利润正增长扩散度",
    "op_yoy_acceleration": "营业利润增速加速度",
    "netprofit_yoy_acceleration": "归母净利润增速加速度",
    "roe_trend": "净资产收益率改善",
    "gross_margin_trend": "毛利率改善",
    "earnings_quality_confirmation": "盈利质量确认",
    "profit_growth_stability": "利润增长稳定性",
    "earnings_yield": "盈利收益率",
    "book_yield": "账面收益率",
    "sales_yield": "销售收益率",
    "dividend_yield": "股息率",
    "peg_proxy": "低PEG代理",
    "earnings_yield_momentum": "盈利收益率改善",
    "value_quality_match": "估值质量匹配",
    "dividend_quality": "红利质量",
    "momentum_12_1": "十二减一月相对动量",
    "momentum_6_1": "六减一月相对动量",
    "momentum_3_1": "三减一月相对动量",
    "momentum_1": "一月相对动量",
    "risk_adjusted_momentum": "风险调整动量",
    "path_efficiency_126": "半年趋势效率",
    "path_efficiency_63": "季度趋势效率",
    "distance_ma120": "半年均线距离",
    "distance_ma60": "季度均线距离",
    "breadth_20": "二十日上涨扩散度",
    "breadth_60": "六十日上涨扩散度",
    "short_reversal": "短期反转",
    "trend_ir_126": "半年趋势信息比",
    "trend_ir_63": "季度趋势信息比",
    "max_drawdown_resilience_126": "半年回撤韧性",
    "new_high_proximity_252": "一年新高接近度",
    "momentum_consistency": "多周期动量一致性",
    "flow_total_5": "五日主力净流入",
    "flow_total_20": "二十日主力净流入",
    "flow_total_60": "六十日主力净流入",
    "flow_large_structure_5": "五日大单结构残差",
    "flow_large_structure_20": "二十日大单结构残差",
    "flow_large_structure_60": "六十日大单结构残差",
    "flow_extra_structure_20": "二十日超大单结构残差",
    "flow_extra_structure_60": "六十日超大单结构残差",
    "flow_breadth_20": "二十日净流入扩散度",
    "flow_persistence_20": "二十日净流入持续度",
    "flow_acceleration_20_60": "二十日相对六十日资金加速度",
    "large_flow_persistence_20": "二十日大单持续度",
    "flow_price_residual_20": "二十日资金价格残差",
    "flow_breadth_change": "资金扩散改善",
    "smart_money_confirmation": "聪明钱确认",
    "turnover_level": "换手水平",
    "turnover_expansion": "换手扩张",
    "volume_ratio": "量比水平",
    "amount_concentration": "成交集中度",
    "limit_up_heat": "涨停热度",
    "short_momentum_heat": "短期涨幅热度",
    "price_distance_heat": "价格偏离热度",
    "volatility_expansion": "波动扩张",
    "breadth_heat": "上涨扩散热度",
    "low_dispersion_heat": "低分歧热度",
    "crowding_acceleration": "拥挤加速度",
    "turnover_percentile_250": "一年换手分位",
    "overheat_residual": "涨幅过热残差",
    "liquidity_crowding": "成交放量拥挤",
    "crowding_reversal_risk": "拥挤反转风险",
}


@dataclass
class SixDimensionState:
    candidates: dict[str, pd.DataFrame]
    dimensions: dict[str, dict[str, pd.DataFrame]]
    factor_scores: dict[str, dict[str, pd.DataFrame]]
    current_weights: dict[str, dict[str, float]]
    diagnostics: dict[str, Any]
    data_quality: dict[str, Any]
    data_as_of: str
    factor_count: dict[str, int]


_STATE: SixDimensionState | None = None


def get_state() -> SixDimensionState | None:
    return _STATE


def _json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _database_signature() -> dict[str, Any]:
    stat = WAREHOUSE.stat()
    return {
        "path": str(WAREHOUSE),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _read_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_valid() -> bool:
    manifest = _read_manifest()
    return bool(
        manifest.get("cache_version") == CACHE_VERSION
        and manifest.get("database") == _database_signature()
        and DAILY_CACHE.exists()
        and MONTHLY_CACHE.exists()
        and manifest.get("files", {}).get("daily_sha256") == _hash(DAILY_CACHE)
        and manifest.get("files", {}).get("monthly_sha256") == _hash(MONTHLY_CACHE)
    )


def _open_read_only() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{WAREHOUSE.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-400000")
    return connection


def _date_text(value: str | int | pd.Timestamp) -> str:
    return pd.Timestamp(str(value)).strftime("%Y%m%d")


def _normalise_memberships(
    rows: pd.DataFrame,
    start: str,
    end: str,
    allowed_industries: set[str],
) -> pd.DataFrame:
    """Resolve overlapping source intervals with the latest effective row."""
    floor = pd.Timestamp(start)
    ceiling = pd.Timestamp(end)
    output: list[dict[str, str]] = []
    ambiguous_intervals = 0
    data = rows.copy()
    data["start"] = pd.to_datetime(data["start_date"], format="%Y%m%d", errors="coerce")
    data["end"] = pd.to_datetime(data["end_date"], format="%Y%m%d", errors="coerce").fillna(ceiling)
    data = data[
        data["start"].notna()
        & data["industry_name"].isin(allowed_industries)
        & data["start"].le(ceiling)
        & data["end"].ge(floor)
    ]
    for code, group in data.groupby("ts_code", sort=False):
        boundaries = {floor, ceiling + timedelta(days=1)}
        for row in group.itertuples():
            boundaries.add(max(floor, pd.Timestamp(row.start)))
            boundaries.add(min(ceiling + timedelta(days=1), pd.Timestamp(row.end) + timedelta(days=1)))
        points = sorted(boundaries)
        local: list[dict[str, Any]] = []
        for left, right in zip(points[:-1], points[1:]):
            if left >= right or left > ceiling:
                continue
            eligible = group[group["start"].le(left) & group["end"].ge(left)]
            if eligible.empty:
                continue
            latest_start = eligible["start"].max()
            latest = eligible[eligible["start"].eq(latest_start)]
            if int(latest["industry_name"].nunique()) != 1:
                ambiguous_intervals += 1
                continue
            chosen = latest.sort_values(["end"]).iloc[-1]
            interval = {
                "ts_code": str(code),
                "start_date": max(left, floor),
                "end_date": min(right - timedelta(days=1), ceiling),
                "industry_name": str(chosen["industry_name"]),
            }
            if (
                local
                and local[-1]["industry_name"] == interval["industry_name"]
                and local[-1]["end_date"] + timedelta(days=1) == interval["start_date"]
            ):
                local[-1]["end_date"] = interval["end_date"]
            else:
                local.append(interval)
        for interval in local:
            output.append(
                {
                    "ts_code": interval["ts_code"],
                    "start_date": interval["start_date"].strftime("%Y%m%d"),
                    "end_date": interval["end_date"].strftime("%Y%m%d"),
                    "industry_name": interval["industry_name"],
                }
            )
    result = pd.DataFrame(output)
    if result.empty:
        raise ValueError("six_dimension_membership_empty")
    ordered = result.sort_values(["ts_code", "start_date", "end_date"])
    previous = ordered.groupby("ts_code")["end_date"].shift()
    overlap = previous.notna() & ordered["start_date"].le(previous.fillna(""))
    if bool(overlap.any()):
        raise ValueError("six_dimension_membership_overlap_after_normalisation")
    result.attrs["ambiguous_intervals_excluded"] = int(ambiguous_intervals)
    return result


def _install_memberships(connection: sqlite3.Connection, rows: pd.DataFrame) -> None:
    connection.execute("PRAGMA query_only=OFF")
    connection.execute(
        "CREATE TEMP TABLE pit_sw_membership ("
        "ts_code TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, "
        "industry_name TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO pit_sw_membership VALUES (?, ?, ?, ?)",
        list(rows[["ts_code", "start_date", "end_date", "industry_name"]].itertuples(index=False, name=None)),
    )
    connection.execute(
        "CREATE INDEX temp.idx_pit_sw_membership ON pit_sw_membership(ts_code, start_date, end_date)"
    )
    connection.execute("PRAGMA query_only=ON")


def _build_daily_input(connection: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    query = """
        SELECT
            o.trade_date,
            m.industry_name,
            COUNT(*) AS member_count,
            AVG(CASE WHEN o.pct_chg IS NULL THEN NULL WHEN o.pct_chg > 0 THEN 1.0 ELSE 0.0 END) AS up_ratio,
            AVG(o.pct_chg) / 100.0 AS equal_weight_return,
            AVG((o.pct_chg / 100.0) * (o.pct_chg / 100.0)) AS return_square,
            SUM(o.amount) AS traded_amount,
            SUM(CASE WHEN f.net_mf_amount IS NULL THEN NULL ELSE o.amount END) AS flow_covered_amount,
            SUM(o.amount * o.amount) / NULLIF(SUM(o.amount) * SUM(o.amount), 0.0) AS amount_concentration,
            AVG(v.turnover_rate) AS turnover_rate,
            AVG(v.volume_ratio) AS volume_ratio,
            SUM(f.net_mf_amount) AS flow_total_amount,
            SUM(f.buy_lg_amount - f.sell_lg_amount) AS flow_large_amount,
            SUM(f.buy_elg_amount - f.sell_elg_amount) AS flow_extra_amount,
            AVG(CASE WHEN f.net_mf_amount IS NULL THEN NULL WHEN f.net_mf_amount > 0 THEN 1.0 ELSE 0.0 END) AS flow_positive_ratio,
            AVG(CASE WHEN f.net_mf_amount IS NULL THEN 0.0 ELSE 1.0 END) AS flow_coverage,
            AVG(CASE WHEN o.up_limit IS NULL OR o.close IS NULL THEN NULL WHEN o.close >= o.up_limit * 0.995 THEN 1.0 ELSE 0.0 END) AS limit_up_ratio
        FROM stock_ohlcv_daily o
        JOIN pit_sw_membership m
          ON m.ts_code = o.ts_code
         AND o.trade_date BETWEEN m.start_date AND m.end_date
        LEFT JOIN stock_valuation_daily v
          ON v.trade_date = o.trade_date AND v.ts_code = o.ts_code
        LEFT JOIN stock_moneyflow_daily f
          ON f.trade_date = o.trade_date AND f.ts_code = o.ts_code
        WHERE o.trade_date BETWEEN ? AND ?
        GROUP BY o.trade_date, m.industry_name
        ORDER BY o.trade_date, m.industry_name
    """
    daily = pd.read_sql_query(query, connection, params=(start, end))
    if daily.empty:
        raise ValueError("six_dimension_daily_input_empty")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    variance = daily["return_square"] - daily["equal_weight_return"].pow(2)
    daily["return_dispersion"] = np.sqrt(variance.clip(lower=0.0))
    return daily.drop(columns=["return_square"])


def _load_month_end_stocks(connection: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    query = """
        WITH month_dates AS (
            SELECT SUBSTR(trade_date, 1, 6) AS month, MAX(trade_date) AS trade_date
            FROM stock_valuation_daily
            WHERE trade_date BETWEEN ? AND ?
            GROUP BY SUBSTR(trade_date, 1, 6)
        )
        SELECT
            v.trade_date,
            v.ts_code,
            m.industry_name,
            v.pe_ttm,
            v.pb,
            v.ps_ttm,
            v.dv_ttm,
            v.circ_mv,
            v.turnover_rate,
            v.volume_ratio
        FROM month_dates d
        JOIN stock_valuation_daily v ON v.trade_date = d.trade_date
        JOIN pit_sw_membership m
          ON m.ts_code = v.ts_code
         AND v.trade_date BETWEEN m.start_date AND m.end_date
        ORDER BY v.trade_date, v.ts_code
    """
    frame = pd.read_sql_query(query, connection, params=(start, end))
    if frame.empty:
        raise ValueError("six_dimension_month_end_stock_input_empty")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    return frame


def _merge_visible_financials(connection: sqlite3.Connection, stocks: pd.DataFrame, end: str) -> pd.DataFrame:
    financial = pd.read_sql_query(
        """
        SELECT ts_code, visible_date, end_date, total_revenue, gross_margin, netprofit_margin,
               roe, roa, debt_to_assets, current_ratio, assets_turn,
               op_yoy, tr_yoy, netprofit_yoy
        FROM financial_report_visible
        WHERE visible_date <= ? AND end_date <= visible_date
        ORDER BY ts_code, visible_date, end_date
        """,
        connection,
        params=(end,),
    )
    financial["visible_date"] = pd.to_datetime(financial["visible_date"], format="%Y%m%d", errors="coerce")
    financial["financial_end_date"] = pd.to_datetime(financial.pop("end_date"), format="%Y%m%d", errors="coerce")
    financial = (
        financial.dropna(subset=["visible_date"])
        .sort_values(["ts_code", "visible_date", "financial_end_date"])
        .drop_duplicates(["ts_code", "visible_date"], keep="last")
    )
    left = stocks.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
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
        parts: list[pd.DataFrame] = []
        financial_by_code = {code: row for code, row in financial.groupby("ts_code", sort=False)}
        for code, local in stocks.groupby("ts_code", sort=False):
            report = financial_by_code.get(code)
            if report is None:
                parts.append(local.assign(visible_date=pd.NaT))
                continue
            parts.append(
                pd.merge_asof(
                    local.sort_values("trade_date"),
                    report.drop(columns="ts_code").sort_values("visible_date"),
                    left_on="trade_date",
                    right_on="visible_date",
                    direction="backward",
                    allow_exact_matches=False,
                )
            )
        merged = pd.concat(parts, ignore_index=True)
    invalid_future = merged["visible_date"].notna() & merged["visible_date"].gt(merged["trade_date"])
    if bool(invalid_future.any()):
        raise ValueError("six_dimension_financial_future_leak")
    merged["report_age_days"] = (merged["trade_date"] - merged["visible_date"]).dt.days
    stale = merged["report_age_days"].gt(550)
    financial_columns = [
        "total_revenue", "gross_margin", "netprofit_margin", "roe", "roa", "debt_to_assets",
        "current_ratio", "assets_turn", "op_yoy", "tr_yoy", "netprofit_yoy",
    ]
    merged.loc[stale, financial_columns] = np.nan
    return merged


def _plausibility_filter(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    gross_raw = pd.to_numeric(result["gross_margin"], errors="coerce")
    revenue = pd.to_numeric(result["total_revenue"], errors="coerce").replace(0.0, np.nan)
    gross_from_amount = gross_raw.div(revenue).mul(100.0)
    result["gross_margin"] = gross_raw.where(gross_raw.abs().le(200.0), gross_from_amount)
    ranges = {
        "pe_ttm": (1.0, 1000.0),
        "pb": (0.05, 100.0),
        "ps_ttm": (0.05, 100.0),
        "dv_ttm": (0.0, 30.0),
        "roe": (-100.0, 100.0),
        "roa": (-100.0, 100.0),
        "gross_margin": (-100.0, 100.0),
        "netprofit_margin": (-100.0, 100.0),
        "debt_to_assets": (0.0, 150.0),
        "current_ratio": (0.0, 20.0),
        "assets_turn": (0.0, 10.0),
        "op_yoy": (-300.0, 500.0),
        "tr_yoy": (-300.0, 500.0),
        "netprofit_yoy": (-300.0, 500.0),
    }
    for column, (lower, upper) in ranges.items():
        values = pd.to_numeric(result[column], errors="coerce")
        result[column] = values.where(values.between(lower, upper))
    result["earnings_yield"] = 1.0 / result["pe_ttm"]
    result["book_yield"] = 1.0 / result["pb"]
    result["sales_yield"] = 1.0 / result["ps_ttm"]
    # Tushare exposes dv_ttm in percentage points.  Store all yields as
    # decimals so factor audits and downstream consumers use one unit.
    result["dividend_yield"] = result["dv_ttm"] / 100.0
    result["revenue_positive"] = result["tr_yoy"].gt(0).where(result["tr_yoy"].notna())
    result["profit_positive"] = result["netprofit_yoy"].gt(0).where(result["netprofit_yoy"].notna())
    result["valuation_available"] = result[["earnings_yield", "book_yield", "sales_yield"]].notna().sum(axis=1).ge(2)
    result["financial_available"] = result[["roe", "roa", "tr_yoy", "netprofit_yoy"]].notna().sum(axis=1).ge(2)
    return result


def _build_monthly_input(connection: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    stocks = _load_month_end_stocks(connection, start, end)
    merged = _plausibility_filter(_merge_visible_financials(connection, stocks, end))
    group = merged.groupby(["trade_date", "industry_name"], sort=True)
    monthly = group.agg(
        member_count=("ts_code", "nunique"),
        earnings_yield=("earnings_yield", "median"),
        book_yield=("book_yield", "median"),
        sales_yield=("sales_yield", "median"),
        dividend_yield=("dividend_yield", "median"),
        roe=("roe", "median"),
        roa=("roa", "median"),
        gross_margin=("gross_margin", "median"),
        netprofit_margin=("netprofit_margin", "median"),
        debt_to_assets=("debt_to_assets", "median"),
        current_ratio=("current_ratio", "median"),
        assets_turn=("assets_turn", "median"),
        op_yoy=("op_yoy", "median"),
        tr_yoy=("tr_yoy", "median"),
        netprofit_yoy=("netprofit_yoy", "median"),
        revenue_positive_breadth=("revenue_positive", "mean"),
        profit_positive_breadth=("profit_positive", "mean"),
        valuation_coverage=("valuation_available", "mean"),
        financial_coverage=("financial_available", "mean"),
        report_age_days=("report_age_days", "median"),
    ).reset_index()
    if monthly.empty:
        raise ValueError("six_dimension_monthly_input_empty")
    return monthly


def _load_or_build_inputs(industries: Iterable[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if _cache_valid():
        daily = pd.read_csv(DAILY_CACHE, parse_dates=["trade_date"])
        monthly = pd.read_csv(MONTHLY_CACHE, parse_dates=["trade_date"])
        return daily, monthly, _read_manifest()

    connection = _open_read_only()
    try:
        bounds = connection.execute(
            "SELECT MIN(trade_date), MAX(trade_date) FROM stock_ohlcv_daily"
        ).fetchone()
        start = max(str(bounds[0]), "20120101")
        end = str(bounds[1])
        memberships = pd.read_sql_query(
            "SELECT ts_code, start_date, end_date, industry_name FROM sw_l1_industry_daily",
            connection,
        )
        normalised = _normalise_memberships(
            memberships,
            start,
            end,
            set(industries),
        )
        _install_memberships(connection, normalised)
        daily = _build_daily_input(connection, start, end)
        monthly = _build_monthly_input(connection, start, end)
    finally:
        connection.close()

    daily_counts = daily.groupby("trade_date")["industry_name"].nunique()
    monthly_counts = monthly.groupby("trade_date")["industry_name"].nunique()
    if int(daily_counts.min()) < 25 or int(monthly_counts.min()) < 25:
        raise ValueError("six_dimension_cross_section_coverage_below_25")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    daily_tmp = DAILY_CACHE.with_suffix(DAILY_CACHE.suffix + ".tmp")
    monthly_tmp = MONTHLY_CACHE.with_suffix(MONTHLY_CACHE.suffix + ".tmp")
    daily.to_csv(daily_tmp, index=False, compression="gzip", encoding="utf-8")
    monthly.to_csv(monthly_tmp, index=False, compression="gzip", encoding="utf-8")
    os.replace(daily_tmp, DAILY_CACHE)
    os.replace(monthly_tmp, MONTHLY_CACHE)
    manifest = {
        "cache_version": CACHE_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "database": _database_signature(),
        "pit_membership": {
            "source_rows": int(len(memberships)),
            "normalised_rows": int(len(normalised)),
            "ambiguous_intervals_excluded": int(normalised.attrs.get("ambiguous_intervals_excluded", 0)),
            "overlap_after_normalisation": 0,
            "rule": "信号日取start_date最近的唯一行业；同起始日多行业区间隔离；连续同业区间合并",
        },
        "daily": {
            "rows": int(len(daily)),
            "start": daily["trade_date"].min().strftime("%Y-%m-%d"),
            "end": daily["trade_date"].max().strftime("%Y-%m-%d"),
            "industry_count": int(daily["industry_name"].nunique()),
            "minimum_daily_industry_count": int(daily_counts.min()),
        },
        "monthly": {
            "rows": int(len(monthly)),
            "start": monthly["trade_date"].min().strftime("%Y-%m-%d"),
            "end": monthly["trade_date"].max().strftime("%Y-%m-%d"),
            "industry_count": int(monthly["industry_name"].nunique()),
            "minimum_monthly_industry_count": int(monthly_counts.min()),
        },
        "availability": {
            "daily": "T日收盘字段只用于T日信号，T+1收盘执行",
            "financial": "visible_date<signal_date后逐股取最新报告，超过550日视为缺失",
            "membership": "start_date<=signal_date<=end_date；同日起始多行业冲突区间隔离并审计",
            "moneyflow": "资金万元乘10后除以有资金记录股票的千元成交额",
            "dividend_yield": "dv_ttm百分数点除以100转为小数",
            "gross_margin": "绝对值超过200时按gross_margin/total_revenue*100从毛利额还原毛利率",
        },
        "files": {
            "daily": str(DAILY_CACHE),
            "monthly": str(MONTHLY_CACHE),
            "daily_sha256": _hash(DAILY_CACHE),
            "monthly_sha256": _hash(MONTHLY_CACHE),
        },
    }
    _json_write(MANIFEST, manifest)
    return daily, monthly, manifest


def _pivot(frame: pd.DataFrame, field: str, index: pd.DatetimeIndex, columns: list[str]) -> pd.DataFrame:
    values = frame.pivot(index="trade_date", columns="industry_name", values=field)
    return values.reindex(index=index, columns=columns).astype(float)


def _cross_section_rank(frame: pd.DataFrame, high_is_good: bool = True) -> pd.DataFrame:
    ranked = frame.rank(axis=1, pct=True, method="average", ascending=high_is_good)
    return ranked.where(frame.notna())


def _rolling_rank(frame: pd.DataFrame, window: int, minimum: int) -> pd.DataFrame:
    return frame.apply(lambda values: values.rolling(window, min_periods=minimum).rank(pct=True))


def _cross_section_mad_winsor(frame: pd.DataFrame, threshold: float = 3.5) -> pd.DataFrame:
    values = frame.replace([np.inf, -np.inf], np.nan).astype(float)
    median = values.median(axis=1, skipna=True)
    mad = values.sub(median, axis=0).abs().median(axis=1, skipna=True).mul(1.4826)
    lower = median.sub(mad.mul(threshold))
    upper = median.add(mad.mul(threshold))
    clipped = values.clip(lower=lower, upper=upper, axis=0)
    return clipped.where(values.notna())


def _atomic_score(
    raw: pd.DataFrame,
    high_is_good: bool = True,
    rolling_window: int = 1250,
    minimum: int = 252,
    time_series_weight: float = 0.35,
) -> pd.DataFrame:
    values = _cross_section_mad_winsor(raw)
    cross_section = _cross_section_rank(values, high_is_good=high_is_good)
    time_series = _rolling_rank(values, rolling_window, minimum)
    if not high_is_good:
        time_series = 1.0 - time_series
    score = cross_section.mul(1.0 - time_series_weight).add(time_series.mul(time_series_weight))
    return score.where(values.notna()).clip(0.0, 1.0)


def _monthly_atomic_score(raw: pd.DataFrame, high_is_good: bool = True) -> pd.DataFrame:
    return _atomic_score(
        raw,
        high_is_good=high_is_good,
        rolling_window=60,
        minimum=24,
        time_series_weight=0.45,
    )


def _mean_available(frames: list[pd.DataFrame], minimum: int) -> pd.DataFrame:
    zero = pd.DataFrame(0.0, index=frames[0].index, columns=frames[0].columns)
    numerator = sum((frame.fillna(0.0) for frame in frames), start=zero.copy())
    count = sum((frame.notna().astype(float) for frame in frames), start=zero.copy())
    return numerator.div(count.replace(0.0, np.nan)).where(count.ge(minimum))


def _cluster_balanced_score(
    factors: dict[str, pd.DataFrame],
    clusters: list[list[str]],
    minimum_clusters: int,
) -> pd.DataFrame:
    """Average information clusters so correlated windows do not multiply weight."""
    cluster_scores: list[pd.DataFrame] = []
    for members in clusters:
        available = [factors[name] for name in members if name in factors]
        if available:
            cluster_scores.append(_mean_available(available, 1))
    if len(cluster_scores) < minimum_clusters:
        raise ValueError("six_dimension_independent_cluster_shortfall")
    return _cross_section_rank(_mean_available(cluster_scores, minimum_clusters))


def _path_efficiency(close: pd.DataFrame, window: int) -> pd.DataFrame:
    log_price = np.log(close.where(close.gt(0)))
    displacement = log_price.diff(window)
    path = log_price.diff().abs().rolling(window, min_periods=max(20, window // 2)).sum()
    return displacement.div(path.replace(0.0, np.nan)).clip(-1.0, 1.0)


def _cross_section_residual(
    target: pd.DataFrame,
    regressors: list[pd.DataFrame],
    minimum: int = 20,
) -> pd.DataFrame:
    """Return same-day robust cross-sectional OLS residuals without filling gaps."""
    output = pd.DataFrame(np.nan, index=target.index, columns=target.columns, dtype=float)
    for date in target.index:
        pieces = [target.loc[date].rename("target")]
        pieces.extend(frame.loc[date].rename(f"x{number}") for number, frame in enumerate(regressors))
        sample = pd.concat(pieces, axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < minimum:
            continue
        robust = sample.copy()
        for column in robust.columns:
            median = float(robust[column].median())
            mad = float((robust[column] - median).abs().median())
            if math.isfinite(mad) and mad > 0.0:
                scale = 1.4826 * mad
                robust[column] = robust[column].clip(median - 4.0 * scale, median + 4.0 * scale)
        design = np.column_stack(
            [np.ones(len(robust), dtype=float)]
            + [robust[column].to_numpy(dtype=float) for column in robust.columns[1:]]
        )
        if np.linalg.matrix_rank(design) < design.shape[1]:
            continue
        response = robust["target"].to_numpy(dtype=float)
        beta, _, _, _ = np.linalg.lstsq(design, response, rcond=None)
        output.loc[date, robust.index] = response - design @ beta
    return output


def _prosperity_factors(existing: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    level = existing["C6_direct_month_smooth"]
    available = [
        existing[name]
        for name in ("C1_equal", "C2_reliability", "C3_train_ic", "C4_direct_dominant", "C6_direct_month_smooth", "C7_consensus")
        if name in existing
    ]
    dispersion = pd.concat(
        {str(i): frame.stack(dropna=False) for i, frame in enumerate(available)},
        axis=1,
    ).std(axis=1, ddof=0).unstack()
    return {
        "prosperity_level": _cross_section_rank(level),
        "prosperity_acceleration": _cross_section_rank(level.sub(level.shift(21))),
        "prosperity_consensus": _cross_section_rank(existing.get("C7_consensus", level)),
        "prosperity_reliability": _cross_section_rank(existing.get("C2_reliability", level)),
        "prosperity_agreement": _cross_section_rank(-dispersion),
    }


def _monthly_factor_scores(
    monthly: pd.DataFrame,
    close_index: pd.DatetimeIndex,
    columns: list[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    unique_dates = pd.DatetimeIndex(sorted(monthly["trade_date"].unique()))
    fundamental_direction = {
        "roe": True,
        "roa": True,
        "gross_margin": True,
        "netprofit_margin": True,
        "assets_turn": True,
        "current_ratio": True,
        "debt_to_assets": False,
        "tr_yoy": True,
        "netprofit_yoy": True,
        "op_yoy": True,
        "revenue_positive_breadth": True,
        "profit_positive_breadth": True,
    }
    valuation_direction = {
        "earnings_yield": True,
        "book_yield": True,
        "sales_yield": True,
        "dividend_yield": True,
    }
    groups: dict[str, dict[str, pd.DataFrame]] = {"fundamental": {}, "valuation": {}}
    raw_groups = {"fundamental": fundamental_direction, "valuation": valuation_direction}
    raw_values: dict[str, pd.DataFrame] = {}
    for dimension, specs in raw_groups.items():
        for field, direction in specs.items():
            raw = _pivot(monthly, field, unique_dates, columns)
            raw_values[field] = raw
            scored = _monthly_atomic_score(raw, high_is_good=direction)
            groups[dimension][field] = scored.reindex(close_index).ffill()
    coverage: dict[str, pd.DataFrame] = {}
    for field in ("financial_coverage", "valuation_coverage", "report_age_days", "member_count"):
        coverage[field] = _pivot(monthly, field, unique_dates, columns).reindex(close_index).ffill()
    financial_mask = coverage["financial_coverage"].ge(0.45)
    valuation_mask = coverage["valuation_coverage"].ge(0.45)
    groups["fundamental"] = {name: frame.where(financial_mask) for name, frame in groups["fundamental"].items()}
    groups["valuation"] = {name: frame.where(valuation_mask) for name, frame in groups["valuation"].items()}

    derived_fundamental = {
        "op_yoy_acceleration": raw_values["op_yoy"].sub(raw_values["op_yoy"].shift(3)),
        "netprofit_yoy_acceleration": raw_values["netprofit_yoy"].sub(raw_values["netprofit_yoy"].shift(3)),
        "roe_trend": raw_values["roe"].sub(raw_values["roe"].shift(3)),
        "gross_margin_trend": raw_values["gross_margin"].sub(raw_values["gross_margin"].shift(3)),
        "profit_growth_stability": raw_values["netprofit_yoy"].rolling(8, min_periods=4).std(ddof=0).mul(-1.0),
    }
    for name, raw in derived_fundamental.items():
        groups["fundamental"][name] = _monthly_atomic_score(raw).reindex(close_index).ffill().where(financial_mask)
    groups["fundamental"]["earnings_quality_confirmation"] = _cross_section_rank(
        _mean_available(
            [
                groups["fundamental"]["op_yoy"],
                groups["fundamental"]["netprofit_yoy"],
                groups["fundamental"]["profit_positive_breadth"],
                groups["fundamental"]["gross_margin"],
            ],
            2,
        )
    ).where(financial_mask)

    positive_growth = raw_values["netprofit_yoy"].clip(lower=0.0).div(100.0).add(0.01)
    derived_valuation = {
        "peg_proxy": raw_values["earnings_yield"].mul(positive_growth),
        "earnings_yield_momentum": raw_values["earnings_yield"].sub(raw_values["earnings_yield"].shift(3)),
    }
    for name, raw in derived_valuation.items():
        groups["valuation"][name] = _monthly_atomic_score(raw).reindex(close_index).ffill().where(valuation_mask & financial_mask)
    groups["valuation"]["value_quality_match"] = _cross_section_rank(
        _mean_available(
            [
                groups["valuation"]["earnings_yield"],
                groups["valuation"]["book_yield"],
                groups["fundamental"]["roe"],
                groups["fundamental"]["gross_margin"],
            ],
            3,
        )
    ).where(valuation_mask & financial_mask)
    groups["valuation"]["dividend_quality"] = _cross_section_rank(
        _mean_available(
            [
                groups["valuation"]["dividend_yield"],
                groups["fundamental"]["roe"],
                groups["fundamental"]["gross_margin"],
            ],
            2,
        )
    ).where(valuation_mask & financial_mask)
    return {**groups["fundamental"], **groups["valuation"]}, coverage


def _daily_factor_scores(
    daily: pd.DataFrame,
    close: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    index = close.index
    columns = list(close.columns)
    amount = _pivot(daily, "traded_amount", index, columns)
    flow_amount = _pivot(daily, "flow_covered_amount", index, columns)
    total_flow = _pivot(daily, "flow_total_amount", index, columns)
    large_flow = _pivot(daily, "flow_large_amount", index, columns)
    extra_flow = _pivot(daily, "flow_extra_amount", index, columns)
    flow_coverage = _pivot(daily, "flow_coverage", index, columns)
    flow_breadth = _pivot(daily, "flow_positive_ratio", index, columns)

    def flow_ratio(numerator: pd.DataFrame, window: int, minimum: int) -> pd.DataFrame:
        value = numerator.rolling(window, min_periods=minimum).sum().mul(10.0).div(
            flow_amount.rolling(window, min_periods=minimum).sum().replace(0.0, np.nan)
        )
        coverage = flow_coverage.rolling(window, min_periods=minimum).mean()
        return value.where(coverage.ge(0.50))

    coverage_20 = flow_coverage.rolling(20, min_periods=12).mean()
    total_ratio = {
        5: flow_ratio(total_flow, 5, 3),
        20: flow_ratio(total_flow, 20, 12),
        60: flow_ratio(total_flow, 60, 36),
    }
    large_ratio = {
        5: flow_ratio(large_flow, 5, 3),
        20: flow_ratio(large_flow, 20, 12),
        60: flow_ratio(large_flow, 60, 36),
    }
    extra_ratio = {
        20: flow_ratio(extra_flow, 20, 12),
        60: flow_ratio(extra_flow, 60, 36),
    }
    large_structure = {
        window: _cross_section_residual(large_ratio[window], [total_ratio[window]])
        for window in (5, 20, 60)
    }
    extra_structure = {
        window: _cross_section_residual(
            extra_ratio[window],
            [total_ratio[window], large_structure[window]],
        )
        for window in (20, 60)
    }
    funds_raw = {
        "flow_total_5": total_ratio[5],
        "flow_total_20": total_ratio[20],
        "flow_total_60": total_ratio[60],
        "flow_large_structure_5": large_structure[5],
        "flow_large_structure_20": large_structure[20],
        "flow_large_structure_60": large_structure[60],
        "flow_extra_structure_20": extra_structure[20],
        "flow_extra_structure_60": extra_structure[60],
        "flow_breadth_20": flow_breadth.rolling(20, min_periods=12).mean().where(coverage_20.ge(0.50)),
        "flow_persistence_20": total_flow.gt(0).where(total_flow.notna()).rolling(20, min_periods=12).mean().where(coverage_20.ge(0.50)),
        "flow_acceleration_20_60": total_ratio[20].sub(total_ratio[60]),
        "large_flow_persistence_20": large_flow.gt(0).where(large_flow.notna()).rolling(20, min_periods=12).mean().where(coverage_20.ge(0.50)),
        "flow_breadth_change": flow_breadth.rolling(20, min_periods=12).mean().sub(flow_breadth.rolling(60, min_periods=36).mean()).where(coverage_20.ge(0.50)),
    }
    funds = {name: _atomic_score(frame) for name, frame in funds_raw.items()}

    returns = close.pct_change(fill_method=None)
    market = returns.mean(axis=1, skipna=True)
    market_nav = market.fillna(0.0).add(1.0).cumprod()
    log_price = np.log(close.where(close.gt(0)))
    momentum_12_1 = close.shift(21).div(close.shift(252)).sub(1.0)
    momentum_6_1 = close.shift(21).div(close.shift(126)).sub(1.0)
    momentum_3_1 = close.shift(21).div(close.shift(63)).sub(1.0)
    momentum_1 = close.div(close.shift(21)).sub(1.0)
    market_12_1 = market_nav.shift(21).div(market_nav.shift(252)).sub(1.0)
    market_6_1 = market_nav.shift(21).div(market_nav.shift(126)).sub(1.0)
    market_3_1 = market_nav.shift(21).div(market_nav.shift(63)).sub(1.0)
    excess_12_1 = momentum_12_1.sub(market_12_1, axis=0)
    excess_6_1 = momentum_6_1.sub(market_6_1, axis=0)
    excess_3_1 = momentum_3_1.sub(market_3_1, axis=0)
    risk = returns.rolling(126, min_periods=63).std(ddof=0)
    excess_daily = returns.sub(market, axis=0)
    up_ratio = _pivot(daily, "up_ratio", index, columns)
    momentum_consistency = _mean_available(
        [
            excess_12_1.gt(0).where(excess_12_1.notna()).astype(float),
            excess_6_1.gt(0).where(excess_6_1.notna()).astype(float),
            excess_3_1.gt(0).where(excess_3_1.notna()).astype(float),
            momentum_1.gt(0).where(momentum_1.notna()).astype(float),
        ],
        3,
    )
    technical_raw = {
        "momentum_12_1": excess_12_1,
        "momentum_6_1": excess_6_1,
        "momentum_3_1": excess_3_1,
        "momentum_1": momentum_1.sub(momentum_1.mean(axis=1), axis=0),
        "risk_adjusted_momentum": excess_6_1.div(risk.replace(0.0, np.nan)),
        "path_efficiency_126": _path_efficiency(close, 126),
        "path_efficiency_63": _path_efficiency(close, 63),
        "distance_ma120": close.div(close.rolling(120, min_periods=60).mean()).sub(1.0),
        "distance_ma60": close.div(close.rolling(60, min_periods=30).mean()).sub(1.0),
        "breadth_20": up_ratio.rolling(20, min_periods=12).mean(),
        "breadth_60": up_ratio.rolling(60, min_periods=36).mean(),
        "short_reversal": close.pct_change(5, fill_method=None).mul(-1.0),
        "trend_ir_126": excess_daily.rolling(126, min_periods=63).mean().div(excess_daily.rolling(126, min_periods=63).std(ddof=0).replace(0.0, np.nan)),
        "trend_ir_63": excess_daily.rolling(63, min_periods=30).mean().div(excess_daily.rolling(63, min_periods=30).std(ddof=0).replace(0.0, np.nan)),
        "max_drawdown_resilience_126": close.div(close.rolling(126, min_periods=63).max()).sub(1.0),
        "new_high_proximity_252": close.div(close.rolling(252, min_periods=126).max()),
        "momentum_consistency": momentum_consistency,
    }
    technical = {name: _atomic_score(frame) for name, frame in technical_raw.items()}
    funds["flow_price_residual_20"] = _atomic_score(
        _cross_section_residual(total_ratio[20], [technical_raw["momentum_1"]])
    )
    funds["smart_money_confirmation"] = _cross_section_rank(
        _mean_available(
            [
                funds["flow_large_structure_20"],
                funds["flow_extra_structure_20"],
                technical["momentum_1"],
            ],
            2,
        )
    )

    turnover = _pivot(daily, "turnover_rate", index, columns)
    volume_ratio = _pivot(daily, "volume_ratio", index, columns)
    concentration = _pivot(daily, "amount_concentration", index, columns)
    limit_up = _pivot(daily, "limit_up_ratio", index, columns)
    dispersion = _pivot(daily, "return_dispersion", index, columns)
    short_vol = returns.rolling(21, min_periods=15).std(ddof=0)
    long_vol = returns.rolling(126, min_periods=63).std(ddof=0)
    turnover_level_raw = turnover.rolling(20, min_periods=12).mean()
    turnover_expansion_raw = turnover.rolling(5, min_periods=3).mean().div(turnover.rolling(60, min_periods=36).mean().replace(0.0, np.nan))
    price_distance_raw = close.div(close.rolling(60, min_periods=30).mean()).sub(1.0)
    volatility_expansion_raw = short_vol.div(long_vol.replace(0.0, np.nan))
    volume_ratio_raw = volume_ratio.rolling(20, min_periods=12).mean()
    limit_up_raw = limit_up.rolling(20, min_periods=12).mean()
    crowding_raw = {
        "turnover_level": turnover_level_raw,
        "turnover_expansion": turnover_expansion_raw,
        "volume_ratio": volume_ratio_raw,
        "amount_concentration": concentration.rolling(20, min_periods=12).mean(),
        "limit_up_heat": limit_up_raw,
        "short_momentum_heat": momentum_1,
        "price_distance_heat": price_distance_raw,
        "volatility_expansion": volatility_expansion_raw,
        "breadth_heat": up_ratio.rolling(5, min_periods=3).mean(),
        "low_dispersion_heat": dispersion.rolling(20, min_periods=12).mean().mul(-1.0),
        "crowding_acceleration": turnover_expansion_raw.add(volatility_expansion_raw, fill_value=np.nan),
        "turnover_percentile_250": _rolling_rank(turnover_level_raw, 250, 120),
        "overheat_residual": _cross_section_residual(momentum_1, [excess_12_1, excess_6_1]),
        "liquidity_crowding": amount.rolling(20, min_periods=12).mean().div(amount.rolling(252, min_periods=126).mean().replace(0.0, np.nan)),
        "crowding_reversal_risk": limit_up_raw.add(price_distance_raw.clip(lower=0.0), fill_value=np.nan).add(turnover_expansion_raw, fill_value=np.nan),
    }
    crowding = {name: _atomic_score(frame) for name, frame in crowding_raw.items()}
    return technical, funds, crowding


def _dimension_scores(
    prosperity: dict[str, pd.DataFrame],
    monthly_scores: dict[str, pd.DataFrame],
    technical: dict[str, pd.DataFrame],
    funds: dict[str, pd.DataFrame],
    crowding: dict[str, pd.DataFrame],
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, dict[str, pd.DataFrame]]]:
    fundamental_names = [
        "roe", "roa", "gross_margin", "netprofit_margin", "assets_turn", "current_ratio",
        "debt_to_assets", "tr_yoy", "netprofit_yoy", "op_yoy",
        "revenue_positive_breadth", "profit_positive_breadth",
        "op_yoy_acceleration", "netprofit_yoy_acceleration", "roe_trend",
        "gross_margin_trend", "earnings_quality_confirmation", "profit_growth_stability",
    ]
    valuation_names = [
        "earnings_yield", "book_yield", "sales_yield", "dividend_yield",
        "peg_proxy", "earnings_yield_momentum", "value_quality_match", "dividend_quality",
    ]
    factor_scores = {
        "prosperity": prosperity,
        "fundamental": {name: monthly_scores[name] for name in fundamental_names},
        "technical": technical,
        "valuation": {name: monthly_scores[name] for name in valuation_names},
        "funds": funds,
        "crowding": crowding,
    }
    prosperity_score = _cluster_balanced_score(
        prosperity,
        [
            ["prosperity_level", "prosperity_acceleration"],
            ["prosperity_consensus", "prosperity_reliability"],
            ["prosperity_agreement"],
        ],
        2,
    )
    fundamental_score = _cluster_balanced_score(
        factor_scores["fundamental"],
        [
            ["roe", "roa", "gross_margin", "netprofit_margin"],
            ["assets_turn", "current_ratio", "debt_to_assets"],
            ["tr_yoy", "netprofit_yoy", "op_yoy"],
            ["op_yoy_acceleration", "netprofit_yoy_acceleration", "roe_trend", "gross_margin_trend"],
            ["revenue_positive_breadth", "profit_positive_breadth", "earnings_quality_confirmation", "profit_growth_stability"],
        ],
        3,
    )
    valuation_score = _cluster_balanced_score(
        factor_scores["valuation"],
        [
            ["earnings_yield", "book_yield", "sales_yield"],
            ["dividend_yield", "dividend_quality"],
            ["peg_proxy", "value_quality_match"],
            ["earnings_yield_momentum"],
        ],
        3,
    )
    technical_score = _cluster_balanced_score(
        technical,
        [
            ["momentum_12_1", "momentum_6_1", "risk_adjusted_momentum", "momentum_consistency"],
            ["momentum_3_1", "momentum_1", "short_reversal"],
            ["path_efficiency_126", "path_efficiency_63", "trend_ir_126", "trend_ir_63"],
            ["distance_ma120", "distance_ma60", "max_drawdown_resilience_126", "new_high_proximity_252"],
            ["breadth_20", "breadth_60"],
        ],
        3,
    )
    weekly_technical_score = _cluster_balanced_score(
        technical,
        [
            ["momentum_3_1", "momentum_1", "short_reversal", "momentum_consistency"],
            ["path_efficiency_63", "trend_ir_63"],
            ["distance_ma60", "max_drawdown_resilience_126"],
            ["breadth_20"],
        ],
        3,
    )
    funds_score = _cluster_balanced_score(
        funds,
        [
            ["flow_total_5", "flow_total_20", "flow_total_60", "flow_acceleration_20_60"],
            ["flow_large_structure_5", "flow_large_structure_20", "flow_large_structure_60", "large_flow_persistence_20"],
            ["flow_extra_structure_20", "flow_extra_structure_60"],
            ["flow_breadth_20", "flow_persistence_20", "flow_breadth_change"],
            ["flow_price_residual_20", "smart_money_confirmation"],
        ],
        3,
    )
    weekly_funds_score = _cluster_balanced_score(
        funds,
        [
            ["flow_total_5", "flow_total_20", "flow_acceleration_20_60"],
            ["flow_large_structure_5", "flow_large_structure_20", "large_flow_persistence_20"],
            ["flow_extra_structure_20"],
            ["flow_breadth_20", "flow_persistence_20", "flow_breadth_change"],
            ["flow_price_residual_20", "smart_money_confirmation"],
        ],
        3,
    )
    crowding_score = _cluster_balanced_score(
        crowding,
        [
            ["turnover_level", "turnover_expansion", "turnover_percentile_250", "volume_ratio"],
            ["amount_concentration", "liquidity_crowding"],
            ["limit_up_heat", "short_momentum_heat", "price_distance_heat", "breadth_heat", "overheat_residual"],
            ["volatility_expansion", "low_dispersion_heat", "crowding_acceleration", "crowding_reversal_risk"],
        ],
        3,
    )
    monthly = {
        "prosperity": prosperity_score,
        "fundamental": fundamental_score,
        "technical": technical_score,
        "valuation": valuation_score,
        "funds": funds_score,
        "crowding": crowding_score,
    }
    weekly = {
        "prosperity": prosperity_score,
        "fundamental": fundamental_score,
        "technical": weekly_technical_score,
        "valuation": valuation_score,
        "funds": weekly_funds_score,
        "crowding": crowding_score,
    }
    return {"monthly": monthly, "weekly": weekly}, factor_scores


def _crowding_risk(crowding: pd.DataFrame) -> pd.DataFrame:
    """Map crowding to a non-negative continuous risk cost without a hard threshold."""
    values = crowding.clip(lower=0.0, upper=1.0)
    return values.pow(2).where(crowding.notna())


def _admitted_factor_score(
    factors: dict[str, pd.DataFrame],
    admitted: list[str],
    clusters: list[list[str]],
    minimum_clusters: int,
) -> pd.DataFrame:
    """Build one dimension only from factors that passed train/validation gates."""
    allowed = set(admitted)
    selected = {
        name: frame
        for name, frame in factors.items()
        if name in allowed
    }
    active_clusters = [
        [name for name in cluster if name in selected]
        for cluster in clusters
    ]
    active_clusters = [cluster for cluster in active_clusters if cluster]
    if len(active_clusters) < minimum_clusters:
        raise ValueError("six_dimension_admitted_cluster_shortfall")
    return _cluster_balanced_score(selected, active_clusters, minimum_clusters)


def _orthogonal_rank(signal: pd.DataFrame, anchor: pd.DataFrame) -> pd.DataFrame:
    """Remove the same-day cross-sectional champion exposure from an overlay."""
    residual = _cross_section_residual(signal, [anchor], minimum=20)
    return _cross_section_rank(residual)


def _champion_overlay_score(
    anchor: pd.DataFrame,
    overlays: dict[str, pd.DataFrame],
    weights: dict[str, float],
) -> pd.DataFrame:
    """Add only independent residual information to the frozen champion rank."""
    centered = _cross_section_rank(anchor).sub(0.5)
    total = centered.copy()
    for name, weight in weights.items():
        frame = overlays.get(name)
        if frame is None or float(weight) <= 0.0:
            continue
        total = total.add(
            _orthogonal_rank(frame, anchor).sub(0.5).mul(float(weight)),
            fill_value=np.nan,
        )
    return _cross_section_rank(total)


def _online_champion_overlay_score(
    anchor: pd.DataFrame,
    overlays: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    maximum_weights: dict[str, float],
    lookback: int,
    minimum_history: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Learn residual overlay weights using only labels matured before each signal."""
    calendar = pd.DatetimeIndex(sorted({pd.Timestamp(date) for date in signal_dates if date in close.index}))
    future, maturities = _non_overlapping_forward_excess(close, list(calendar))
    residuals = {name: _orthogonal_rank(frame, anchor) for name, frame in overlays.items()}
    ic = pd.DataFrame({
        name: _row_spearman(frame, future, list(calendar))
        for name, frame in residuals.items()
    }).reindex(calendar)
    weights = pd.DataFrame(0.0, index=calendar, columns=list(residuals), dtype=float)
    output = pd.DataFrame(np.nan, index=anchor.index, columns=anchor.columns, dtype=float)
    for current in calendar:
        eligible = maturities[maturities.lt(current)].index
        history = ic.loc[ic.index.intersection(eligible)].tail(int(lookback))
        for name in weights.columns:
            sample = history[name].dropna()
            if len(sample) < int(minimum_history):
                continue
            mean = float(sample.mean())
            std = float(sample.std(ddof=1)) if len(sample) > 1 else math.nan
            positive_rate = float(sample.gt(0.0).mean())
            if mean <= 0.0 or positive_rate <= 0.50 or not math.isfinite(std) or std <= 0.0:
                continue
            t_stat = mean / (std / math.sqrt(len(sample)))
            confidence = min(1.0, max(0.0, t_stat / 2.0))
            consistency = min(1.0, max(0.0, (positive_rate - 0.50) * 4.0))
            evidence = len(sample) / (len(sample) + float(minimum_history))
            weights.at[current, name] = float(maximum_weights.get(name, 0.0)) * confidence * consistency * evidence
        score = anchor.loc[current].sub(0.5)
        for name in weights.columns:
            score = score.add(
                residuals[name].loc[current].sub(0.5).mul(float(weights.at[current, name])),
                fill_value=np.nan,
            )
        output.loc[current] = score.rank(pct=True, method="average")
    return output.ffill(), weights

# Research challenger factor pool.  The production champion is still protected
# by the promotion gate; this pool is used to test whether the expanded
# secondary-factor library adds stable train/validation information before any
# model is allowed to replace the champion.
ADMITTED_FACTORS = {
    "fundamental": [
        "assets_turn", "netprofit_yoy", "op_yoy", "profit_positive_breadth",
        "op_yoy_acceleration", "netprofit_yoy_acceleration", "roe_trend",
        "gross_margin_trend", "earnings_quality_confirmation",
        "profit_growth_stability",
    ],
    "valuation": [
        "earnings_yield", "book_yield", "dividend_yield", "peg_proxy",
        "earnings_yield_momentum", "value_quality_match", "dividend_quality",
    ],
    "technical_monthly": [
        "momentum_12_1", "momentum_6_1", "risk_adjusted_momentum",
        "path_efficiency_126", "path_efficiency_63", "distance_ma120",
        "trend_ir_126", "trend_ir_63", "max_drawdown_resilience_126",
        "new_high_proximity_252", "momentum_consistency",
    ],
    "technical_weekly": [
        "momentum_3_1", "momentum_1", "path_efficiency_63", "distance_ma60",
        "trend_ir_63", "max_drawdown_resilience_126", "momentum_consistency",
    ],
    "funds_monthly": [
        "flow_large_structure_20", "flow_large_structure_60", "flow_extra_structure_20",
        "flow_acceleration_20_60", "large_flow_persistence_20",
        "flow_price_residual_20", "flow_breadth_change", "smart_money_confirmation",
    ],
    "funds_weekly": [
        "flow_large_structure_20", "flow_extra_structure_20",
        "flow_acceleration_20_60", "large_flow_persistence_20",
        "flow_price_residual_20", "smart_money_confirmation",
    ],
}


def _admitted_dimensions(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
) -> dict[str, dict[str, pd.DataFrame]]:
    fundamental = _admitted_factor_score(
        factor_scores["fundamental"],
        ADMITTED_FACTORS["fundamental"],
        [
            ["assets_turn"],
            ["netprofit_yoy", "op_yoy"],
            ["op_yoy_acceleration", "netprofit_yoy_acceleration", "roe_trend", "gross_margin_trend"],
            ["profit_positive_breadth", "earnings_quality_confirmation", "profit_growth_stability"],
        ],
        4,
    )
    valuation = _admitted_factor_score(
        factor_scores["valuation"],
        ADMITTED_FACTORS["valuation"],
        [
            ["earnings_yield", "book_yield"],
            ["dividend_yield", "dividend_quality"],
            ["peg_proxy", "value_quality_match"],
            ["earnings_yield_momentum"],
        ],
        3,
    )
    technical_monthly = _admitted_factor_score(
        factor_scores["technical"],
        ADMITTED_FACTORS["technical_monthly"],
        [
            ["momentum_12_1", "momentum_6_1", "risk_adjusted_momentum", "momentum_consistency"],
            ["path_efficiency_126", "path_efficiency_63", "trend_ir_126", "trend_ir_63"],
            ["distance_ma120", "max_drawdown_resilience_126", "new_high_proximity_252"],
        ],
        3,
    )
    technical_weekly = _admitted_factor_score(
        factor_scores["technical"],
        ADMITTED_FACTORS["technical_weekly"],
        [
            ["momentum_3_1", "momentum_1", "momentum_consistency"],
            ["path_efficiency_63", "trend_ir_63"],
            ["distance_ma60", "max_drawdown_resilience_126"],
        ],
        3,
    )
    funds_monthly = _admitted_factor_score(
        factor_scores["funds"],
        ADMITTED_FACTORS["funds_monthly"],
        [
            ["flow_large_structure_20", "flow_large_structure_60", "large_flow_persistence_20"],
            ["flow_extra_structure_20"],
            ["flow_acceleration_20_60", "flow_breadth_change"],
            ["flow_price_residual_20", "smart_money_confirmation"],
        ],
        3,
    )
    funds_weekly = _admitted_factor_score(
        factor_scores["funds"],
        ADMITTED_FACTORS["funds_weekly"],
        [
            ["flow_large_structure_20", "large_flow_persistence_20"],
            ["flow_extra_structure_20"],
            ["flow_acceleration_20_60"],
            ["flow_price_residual_20", "smart_money_confirmation"],
        ],
        3,
    )
    return {
        "monthly": {
            "fundamental": fundamental,
            "valuation": valuation,
            "technical": technical_monthly,
            "funds": funds_monthly,
        },
        "weekly": {
            "fundamental": fundamental,
            "valuation": valuation,
            "technical": technical_weekly,
            "funds": funds_weekly,
        },
    }

def _weighted_dimension_score(
    dimensions: dict[str, pd.DataFrame],
    weights: dict[str, float],
    crowding_penalty: float,
    consensus_weight: float = 0.0,
) -> pd.DataFrame:
    names = ["prosperity", "fundamental", "technical", "valuation", "funds"]
    zero = pd.DataFrame(0.0, index=dimensions[names[0]].index, columns=dimensions[names[0]].columns)
    numerator = sum((dimensions[name].fillna(0.0).mul(weights[name]) for name in names), start=zero.copy())
    denominator = sum((dimensions[name].notna().astype(float).mul(weights[name]) for name in names), start=zero.copy())
    average = numerator.div(denominator.replace(0.0, np.nan)).where(denominator.ge(0.70))
    if consensus_weight > 0:
        stacked = pd.concat({name: dimensions[name].stack(dropna=False) for name in names}, axis=1)
        minimum = stacked.min(axis=1, skipna=True).where(stacked.notna().sum(axis=1).ge(4)).unstack()
        average = average.mul(1.0 - consensus_weight).add(minimum.mul(consensus_weight))
    risk_cost = _crowding_risk(dimensions["crowding"]).mul(max(float(crowding_penalty), 0.0))
    return average.sub(risk_cost).clip(lower=0.0, upper=1.0).where(risk_cost.notna())


def _row_spearman(score: pd.DataFrame, future: pd.DataFrame, dates: list[pd.Timestamp]) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    for date in dates:
        if date not in score.index or date not in future.index:
            continue
        pair = pd.concat([score.loc[date], future.loc[date]], axis=1).dropna()
        if len(pair) >= 20:
            values[date] = float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman"))
    return pd.Series(values, dtype=float).sort_index()


def _row_top_bottom_spread(
    score: pd.DataFrame,
    future: pd.DataFrame,
    dates: list[pd.Timestamp],
    top_n: int = 5,
) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    for date in dates:
        if date not in score.index or date not in future.index:
            continue
        pair = pd.concat([score.loc[date], future.loc[date]], axis=1).dropna()
        if len(pair) < max(20, top_n * 2):
            continue
        pair.columns = ["score", "future"]
        ordered = pair.sort_values("score")
        bottom = ordered.head(top_n)["future"].mean()
        top = ordered.tail(top_n)["future"].mean()
        values[date] = float(top - bottom)
    return pd.Series(values, dtype=float).sort_index()


def _non_overlapping_forward_excess(
    close: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.Series]:
    """Return T+1 execution-to-next-T+1 execution excess returns and maturities."""
    calendar = pd.DatetimeIndex(sorted({pd.Timestamp(date) for date in signal_dates if date in close.index}))
    future = pd.DataFrame(np.nan, index=close.index, columns=close.columns, dtype=float)
    maturities: dict[pd.Timestamp, pd.Timestamp] = {}
    for signal, next_signal in zip(calendar[:-1], calendar[1:]):
        start_pos = int(close.index.searchsorted(signal, side="right"))
        end_pos = int(close.index.searchsorted(next_signal, side="right"))
        if start_pos >= len(close.index) or end_pos >= len(close.index):
            continue
        execution = close.index[start_pos]
        maturity = close.index[end_pos]
        returns = close.loc[maturity].div(close.loc[execution]).sub(1.0)
        future.loc[signal] = returns.sub(returns.mean(skipna=True))
        maturities[signal] = maturity
    return future, pd.Series(maturities, dtype="datetime64[ns]").sort_index()


def _capped_weights(values: pd.Series, cap: float = 0.30) -> pd.Series:
    """Project non-negative evidence weights onto a capped simplex."""
    raw = values.astype(float).clip(lower=0.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if float(raw.sum()) <= 0.0:
        raw[:] = 1.0
    result = pd.Series(0.0, index=raw.index, dtype=float)
    active = list(raw.index)
    remaining = 1.0
    while active:
        base = raw.loc[active]
        if float(base.sum()) <= 0.0:
            proposal = pd.Series(remaining / len(active), index=active)
        else:
            proposal = base.div(base.sum()).mul(remaining)
        offenders = proposal[proposal.gt(cap + 1e-12)].index.tolist()
        if not offenders:
            result.loc[active] = proposal
            break
        result.loc[offenders] = cap
        remaining -= cap * len(offenders)
        active = [name for name in active if name not in offenders]
    return result.div(result.sum())


def _online_ic_score(
    dimensions: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    names = ["prosperity", "fundamental", "technical", "valuation", "funds"]
    calendar = pd.DatetimeIndex(sorted({pd.Timestamp(date) for date in signal_dates if date in close.index}))
    future, maturities = _non_overlapping_forward_excess(close, list(calendar))
    ic = pd.DataFrame({name: _row_spearman(dimensions[name], future, list(calendar)) for name in names}).reindex(calendar)
    equal = pd.Series(1.0 / len(names), index=names, dtype=float)
    weights = pd.DataFrame(index=calendar, columns=names, dtype=float)
    for current in calendar:
        eligible = maturities[maturities.lt(current)].index
        history = ic.loc[ic.index.intersection(eligible)].tail(36)
        if len(history) < 12:
            weights.loc[current] = equal
            continue
        count = history.notna().sum()
        mean = history.mean()
        std = history.std(ddof=1).replace(0.0, np.nan)
        reliability = mean.div(std).clip(lower=0.0).fillna(0.0)
        evidence = count.div(count.add(24.0))
        empirical = reliability.mul(evidence)
        if float(empirical.sum()) <= 0.0:
            posterior = equal
        else:
            posterior = equal.mul(0.70).add(empirical.div(empirical.sum()).mul(0.30))
        weights.loc[current] = _capped_weights(posterior, cap=0.30)
    score = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for date, row in weights.iterrows():
        numerator = pd.Series(0.0, index=close.columns)
        denominator = pd.Series(0.0, index=close.columns)
        for name in names:
            values = dimensions[name].loc[date]
            numerator = numerator.add(values.fillna(0.0).mul(float(row[name])), fill_value=0.0)
            denominator = denominator.add(values.notna().astype(float).mul(float(row[name])), fill_value=0.0)
        average = numerator.div(denominator.replace(0.0, np.nan)).where(denominator.ge(0.70))
        risk_cost = _crowding_risk(dimensions["crowding"].loc[[date]]).iloc[0].mul(0.25)
        score.loc[date] = average.sub(risk_cost).clip(lower=0.0, upper=1.0).where(risk_cost.notna())
    return score.ffill(), weights


def _online_factor_stack_score(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    dimensions: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    frequency: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Causal online stack of atomic factors for next-period industry excess return.

    The prior six-dimension challengers kept the frozen C6 prosperity score as
    the centre of gravity.  This stack lets matured next-period labels decide
    which atomic factors still carry predictive power.  At each signal date,
    only labels whose execution-to-next-execution window has already matured
    are eligible, so the future return being predicted is not visible.
    """

    if frequency == "monthly":
        prosperity = ["prosperity_level", "prosperity_acceleration"]
        fundamental = [
            "op_yoy", "netprofit_yoy", "op_yoy_acceleration", "netprofit_yoy_acceleration",
            "profit_positive_breadth", "earnings_quality_confirmation",
            "gross_margin_trend", "profit_growth_stability",
        ]
        valuation = [
            "earnings_yield", "peg_proxy", "earnings_yield_momentum",
            "value_quality_match", "dividend_quality",
        ]
        technical = [
            "momentum_12_1", "momentum_6_1", "risk_adjusted_momentum",
            "path_efficiency_126", "trend_ir_126", "max_drawdown_resilience_126",
            "new_high_proximity_252", "momentum_consistency",
        ]
        funds = [
            "flow_large_structure_60", "flow_acceleration_20_60",
            "large_flow_persistence_20", "flow_price_residual_20",
            "smart_money_confirmation",
        ]
        lookback = 36
        minimum_history = 12
        recent_window = 9
        prior_weights = {
            "prosperity:prosperity_level": 0.12,
            "prosperity:prosperity_acceleration": 0.14,
            "fundamental:op_yoy": 0.10,
            "fundamental:netprofit_yoy": 0.05,
            "fundamental:op_yoy_acceleration": 0.07,
            "fundamental:netprofit_yoy_acceleration": 0.05,
            "fundamental:profit_positive_breadth": 0.08,
            "fundamental:earnings_quality_confirmation": 0.07,
            "fundamental:gross_margin_trend": 0.04,
            "fundamental:profit_growth_stability": 0.03,
            "valuation:earnings_yield": 0.03,
            "valuation:peg_proxy": 0.04,
            "valuation:earnings_yield_momentum": 0.03,
            "valuation:value_quality_match": 0.03,
            "technical:momentum_12_1": 0.16,
            "technical:risk_adjusted_momentum": 0.06,
            "technical:trend_ir_126": 0.05,
            "technical:max_drawdown_resilience_126": 0.04,
            "technical:momentum_consistency": 0.05,
            "funds:flow_large_structure_60": 0.03,
            "funds:flow_acceleration_20_60": 0.03,
            "funds:flow_price_residual_20": 0.03,
            "funds:smart_money_confirmation": 0.02,
        }
        dimension_cap = {
            "prosperity": 0.34,
            "fundamental": 0.42,
            "valuation": 0.14,
            "technical": 0.46,
            "funds": 0.14,
        }
        individual_cap = 0.20
        risk_penalty = 0.16
        shrink_to_prior = 0.62
    else:
        prosperity = ["prosperity_level", "prosperity_acceleration"]
        fundamental = [
            "op_yoy", "op_yoy_acceleration", "profit_positive_breadth",
            "earnings_quality_confirmation", "gross_margin_trend",
        ]
        valuation = ["earnings_yield", "peg_proxy", "value_quality_match"]
        technical = [
            "momentum_3_1", "momentum_1", "path_efficiency_63",
            "trend_ir_63", "distance_ma60", "momentum_consistency",
        ]
        funds = [
            "flow_total_20", "flow_large_structure_20", "flow_extra_structure_20",
            "flow_acceleration_20_60", "flow_price_residual_20", "smart_money_confirmation",
        ]
        lookback = 104
        minimum_history = 30
        recent_window = 13
        prior_weights = {
            "prosperity:prosperity_level": 0.08,
            "prosperity:prosperity_acceleration": 0.08,
            "fundamental:op_yoy": 0.10,
            "fundamental:op_yoy_acceleration": 0.08,
            "fundamental:profit_positive_breadth": 0.07,
            "fundamental:earnings_quality_confirmation": 0.05,
            "valuation:peg_proxy": 0.04,
            "technical:momentum_3_1": 0.14,
            "technical:momentum_1": 0.11,
            "technical:path_efficiency_63": 0.11,
            "technical:trend_ir_63": 0.07,
            "technical:momentum_consistency": 0.06,
            "funds:flow_total_20": 0.05,
            "funds:flow_large_structure_20": 0.05,
            "funds:flow_price_residual_20": 0.04,
            "funds:smart_money_confirmation": 0.04,
        }
        dimension_cap = {
            "prosperity": 0.26,
            "fundamental": 0.34,
            "valuation": 0.10,
            "technical": 0.48,
            "funds": 0.22,
        }
        individual_cap = 0.18
        risk_penalty = 0.22
        shrink_to_prior = 0.55

    selected: dict[str, pd.DataFrame] = {}
    for factor in prosperity:
        selected[f"prosperity:{factor}"] = factor_scores["prosperity"][factor]
    for factor in fundamental:
        selected[f"fundamental:{factor}"] = factor_scores["fundamental"][factor]
    for factor in valuation:
        selected[f"valuation:{factor}"] = factor_scores["valuation"][factor]
    for factor in technical:
        selected[f"technical:{factor}"] = factor_scores["technical"][factor]
    for factor in funds:
        selected[f"funds:{factor}"] = factor_scores["funds"][factor]

    columns = list(close.columns)
    calendar = pd.DatetimeIndex(
        sorted({pd.Timestamp(date) for date in signal_dates if date in close.index})
    )
    future, maturities = _non_overlapping_forward_excess(close, list(calendar))
    ic = pd.DataFrame(
        {name: _row_spearman(frame, future, list(calendar)) for name, frame in selected.items()}
    ).reindex(calendar)
    spread = pd.DataFrame(
        {name: _row_top_bottom_spread(frame, future, list(calendar), top_n=5) for name, frame in selected.items()}
    ).reindex(calendar)
    prior = pd.Series(prior_weights, dtype=float).reindex(selected).fillna(0.0)
    if float(prior.sum()) <= 0.0:
        prior[:] = 1.0
    prior = prior.div(prior.sum())

    def apply_caps(raw: pd.Series) -> pd.Series:
        weights = raw.astype(float).clip(lower=0.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if float(weights.sum()) <= 0.0:
            weights = prior.copy()
        weights = weights.div(weights.sum())
        weights = weights.clip(upper=individual_cap)
        for dimension, cap in dimension_cap.items():
            members = [name for name in weights.index if name.startswith(f"{dimension}:")]
            total = float(weights.loc[members].sum()) if members else 0.0
            if total > cap:
                weights.loc[members] = weights.loc[members].mul(cap / total)
        if float(weights.sum()) <= 0.0:
            weights = prior.copy()
        return weights.div(weights.sum())

    weights = pd.DataFrame(index=calendar, columns=list(selected), dtype=float)
    for current in calendar:
        eligible = maturities[maturities.lt(current)].index
        history = ic.loc[ic.index.intersection(eligible)].tail(lookback)
        spread_history = spread.loc[spread.index.intersection(eligible)].tail(lookback)
        if len(history) < minimum_history:
            weights.loc[current] = apply_caps(prior)
            continue
        sample_count = history.notna().sum()
        mean = history.mean()
        std = history.std(ddof=1).replace(0.0, np.nan)
        hit = history.gt(0.0).mean()
        recent = history.tail(recent_window).mean()
        reliability = mean.clip(lower=0.0).div(std.fillna(np.inf))
        persistence = hit.sub(0.50).clip(lower=0.0).mul(2.5)
        recent_abs = history.tail(recent_window).abs().mean().replace(0.0, np.nan)
        stability = recent.clip(lower=0.0).div(recent_abs).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(0.0, 1.0)
        evidence = sample_count.div(sample_count.add(float(minimum_history)))
        spread_count = spread_history.notna().sum()
        spread_mean = spread_history.mean()
        spread_std = spread_history.std(ddof=1).replace(0.0, np.nan)
        spread_hit = spread_history.gt(0.0).mean()
        spread_recent = spread_history.tail(max(3, min(recent_window, len(spread_history)))).mean()
        spread_reliability = spread_mean.clip(lower=0.0).div(spread_std.fillna(np.inf))
        spread_persistence = spread_hit.sub(0.50).clip(lower=0.0).mul(2.0)
        spread_evidence = spread_count.div(spread_count.add(float(minimum_history)))
        spread_signal = spread_reliability.mul(spread_persistence).mul(spread_evidence).where(spread_recent.ge(-0.002), 0.0)
        raw = reliability.mul(persistence).mul(stability).mul(evidence).mul(0.58).add(spread_signal.mul(0.42), fill_value=0.0)
        raw = raw.where(recent.gt(-0.005), 0.0)
        posterior = (
            prior.mul(shrink_to_prior).add(raw.div(raw.sum()).mul(1.0 - shrink_to_prior))
            if float(raw.sum()) > 0.0
            else prior.copy()
        )
        weights.loc[current] = apply_caps(posterior)

    score = pd.DataFrame(index=close.index, columns=columns, dtype=float)
    for date, row in weights.iterrows():
        numerator = pd.Series(0.0, index=columns)
        denominator = pd.Series(0.0, index=columns)
        for name, weight in row.dropna().items():
            frame = selected[name]
            if date not in frame.index:
                continue
            values = frame.loc[date]
            numerator = numerator.add(values.fillna(0.0).mul(float(weight)), fill_value=0.0)
            denominator = denominator.add(values.notna().astype(float).mul(float(weight)), fill_value=0.0)
        combined = numerator.div(denominator.replace(0.0, np.nan)).where(denominator.ge(0.70))
        risk_cost = _crowding_risk(dimensions["crowding"].loc[[date]]).iloc[0].mul(risk_penalty)
        daily_score = combined.sub(risk_cost).clip(lower=0.0, upper=1.0).where(risk_cost.notna())
        score.loc[date] = daily_score.rank(pct=True, method="average")
    return score.ffill(), weights


def _market_regime_strength(close: pd.DataFrame) -> pd.Series:
    """Past-only broad-market risk appetite used to blend offensive and defensive scores."""
    benchmark = close.mean(axis=1).sort_index()
    trend = benchmark.pct_change(126)
    short_trend = benchmark.pct_change(63)
    breadth = close.pct_change(63).gt(0.0).mean(axis=1)
    vol = benchmark.pct_change().rolling(63, min_periods=30).std()
    vol_rank = vol.rolling(756, min_periods=252).rank(pct=True)
    trend_rank = trend.rolling(756, min_periods=252).rank(pct=True)
    short_rank = short_trend.rolling(504, min_periods=168).rank(pct=True)
    raw = (
        trend_rank.mul(0.45)
        .add(short_rank.mul(0.25), fill_value=0.0)
        .add(breadth.rolling(20, min_periods=5).mean().mul(0.20), fill_value=0.0)
        .add((1.0 - vol_rank).mul(0.10), fill_value=0.0)
    )
    return raw.clip(0.10, 0.90).ffill()


def _regime_blend_score(
    close: pd.DataFrame,
    offensive: pd.DataFrame,
    defensive: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
) -> pd.DataFrame:
    """Blend two predeclared score books using only information visible at the signal date."""
    columns = list(offensive.columns)
    regime = _market_regime_strength(close).reindex(offensive.index).ffill()
    output = pd.DataFrame(index=offensive.index, columns=columns, dtype=float)
    calendar = pd.DatetimeIndex(sorted({pd.Timestamp(date) for date in signal_dates if date in offensive.index}))
    for date in calendar:
        weight = float(regime.get(date, np.nan))
        if not math.isfinite(weight):
            weight = 0.50
        output.loc[date] = offensive.loc[date].mul(weight).add(defensive.loc[date].mul(1.0 - weight), fill_value=np.nan)
    return _cross_section_rank(output.ffill())


def _split_ic_stats(
    ic: pd.Series,
    maturities: pd.Series,
    splits: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    maturity = pd.to_datetime(maturities.reindex(ic.index), errors="coerce")
    signals = pd.Series(pd.DatetimeIndex(ic.index), index=ic.index)
    for name, (start, end) in splits.items():
        start_date = pd.Timestamp(start)
        end_date = pd.Timestamp(end)
        signal_window = signals.ge(start_date) & signals.le(end_date)
        eligible = signal_window & maturity.notna() & maturity.le(end_date)
        sample = ic.loc[eligible].dropna()
        purged = int((signal_window & (~maturity.notna() | maturity.gt(end_date))).sum())
        std = float(sample.std(ddof=1)) if len(sample) > 1 else math.nan
        mean = float(sample.mean()) if len(sample) else math.nan
        t_value = mean / (std / math.sqrt(len(sample))) if len(sample) > 1 and std > 0 else math.nan
        output[name] = {
            "observations": int(len(sample)),
            "mean_ic": round(mean, 6) if len(sample) else None,
            "icir": round(float(np.sqrt(12.0) * mean / std), 6) if len(sample) > 1 and std > 0 else None,
            "t_value": round(float(t_value), 6) if math.isfinite(t_value) else None,
            "positive_rate": round(float(sample.gt(0).mean()), 6) if len(sample) else None,
            "latest_maturity": pd.Timestamp(maturity.loc[sample.index].max()).strftime("%Y-%m-%d") if len(sample) else None,
            "purged_boundary_labels": purged,
            "report_only": name == "test",
        }
    train_mean = output.get("train", {}).get("mean_ic")
    train_icir = output.get("train", {}).get("icir")
    for name, stats in output.items():
        if name == "train":
            continue
        mean = stats.get("mean_ic")
        icir = stats.get("icir")
        stats["ic_decay_vs_train"] = (
            round(float(mean) - float(train_mean), 6)
            if mean is not None and train_mean is not None
            else None
        )
        stats["icir_decay_ratio_vs_train"] = (
            round(float(icir) / float(train_icir), 6)
            if icir is not None and train_icir not in (None, 0)
            else None
        )
    return output


def _split_spread_stats(
    spread: pd.Series,
    maturities: pd.Series,
    splits: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    maturity = pd.to_datetime(maturities.reindex(spread.index), errors="coerce")
    signals = pd.Series(pd.DatetimeIndex(spread.index), index=spread.index)
    for name, (start, end) in splits.items():
        start_date = pd.Timestamp(start)
        end_date = pd.Timestamp(end)
        signal_window = signals.ge(start_date) & signals.le(end_date)
        eligible = signal_window & maturity.notna() & maturity.le(end_date)
        sample = spread.loc[eligible].dropna()
        purged = int((signal_window & (~maturity.notna() | maturity.gt(end_date))).sum())
        std = float(sample.std(ddof=1)) if len(sample) > 1 else math.nan
        mean = float(sample.mean()) if len(sample) else math.nan
        t_value = mean / (std / math.sqrt(len(sample))) if len(sample) > 1 and std > 0 else math.nan
        output[name] = {
            "observations": int(len(sample)),
            "mean_spread": round(mean, 6) if len(sample) else None,
            "annualized_spread": round(float(mean * 12.0), 6) if len(sample) else None,
            "t_value": round(float(t_value), 6) if math.isfinite(t_value) else None,
            "positive_rate": round(float(sample.gt(0).mean()), 6) if len(sample) else None,
            "latest_maturity": pd.Timestamp(maturity.loc[sample.index].max()).strftime("%Y-%m-%d") if len(sample) else None,
            "purged_boundary_labels": purged,
            "report_only": name == "test",
        }
    return output


def _factor_diagnostics(
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    dimensions: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    splits: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    future, maturities = _non_overlapping_forward_excess(close, signal_dates)
    atomic: list[dict[str, Any]] = []
    for dimension, rows in factor_scores.items():
        for factor, score in rows.items():
            ic = _row_spearman(score, future, signal_dates)
            coverage = score.loc[score.index.intersection(signal_dates)].notna().mean().mean()
            spread = _row_top_bottom_spread(score, future, signal_dates, top_n=5)
            atomic.append({
                "dimension": dimension,
                "dimension_label": DIMENSION_LABELS[dimension],
                "factor": factor,
                "factor_label": FACTOR_LABELS.get(factor, factor),
                "direction": "正向" if dimension != "crowding" else "越高越拥挤",
                "coverage": round(float(coverage), 6),
                "ic": _split_ic_stats(ic, maturities, splits),
                "top_bottom": _split_spread_stats(spread, maturities, splits),
            })
    dimension_rows: list[dict[str, Any]] = []
    for name, score in dimensions.items():
        ic = _row_spearman(score, future, signal_dates)
        spread = _row_top_bottom_spread(score, future, signal_dates, top_n=5)
        dimension_rows.append({
            "dimension": name,
            "label": DIMENSION_LABELS[name],
            "factor_count": len(factor_scores[name]),
            "ic": _split_ic_stats(ic, maturities, splits),
            "top_bottom": _split_spread_stats(spread, maturities, splits),
        })
    return {
        "horizon": "月末信号后首个交易日收盘至下月首个执行日收盘的行业超额收益",
        "method": "逐月31行业不重叠Spearman IC；在线权重只读取成熟标签；测试期仅报告",
        "label_count": int(len(maturities)),
        "atomic_factors": atomic,
        "dimensions": dimension_rows,
    }


def _persist_factor_library(
    industry_codes: dict[str, str],
    signal_dates: list[pd.Timestamp],
    factor_scores: dict[str, dict[str, pd.DataFrame]],
    dimensions: dict[str, pd.DataFrame],
    diagnostics: dict[str, Any],
    data_as_of: pd.Timestamp,
) -> dict[str, int]:
    calendar = pd.DatetimeIndex(
        sorted({pd.Timestamp(date) for date in signal_dates if pd.Timestamp(date) <= data_as_of})
    )
    names = list(industry_codes)
    rows: list[tuple[str, str, str, float, str, str]] = []

    def append_frame(frame: pd.DataFrame, factor_name: str, group_name: str) -> None:
        aligned = frame.reindex(calendar).reindex(columns=names)
        for (date, industry), value in aligned.stack(dropna=True).items():
            number = float(value)
            if not math.isfinite(number):
                continue
            rows.append((
                pd.Timestamp(date).strftime("%Y%m%d"),
                str(industry_codes.get(str(industry), str(industry))),
                factor_name,
                number,
                group_name,
                MODEL_VERSION,
            ))

    for dimension, factor_map in factor_scores.items():
        group_name = f"行业轮动_{DIMENSION_LABELS.get(dimension, dimension)}"
        for factor, frame in factor_map.items():
            label = FACTOR_LABELS.get(factor, factor)
            append_frame(frame, f"{group_name}_{label}", group_name)
    for dimension, frame in dimensions.items():
        label = DIMENSION_LABELS.get(dimension, dimension)
        append_frame(frame, f"行业轮动_一级维度_{label}", "行业轮动_一级维度")

    run_id = "industry_rotation_v5_4_secondary_factor_gated_SW31_" + data_as_of.strftime("%Y%m%d")
    test_rows: list[tuple[Any, ...]] = []
    for item in diagnostics.get("atomic_factors", []):
        factor_name = f"{item.get('dimension_label')}_{item.get('factor_label')}"
        coverage = item.get("coverage")
        spread_by_split = item.get("top_bottom") or {}
        for split, stats in (item.get("ic") or {}).items():
            spread_stats = spread_by_split.get(split, {})
            rank_ic = stats.get("mean_ic")
            icir = stats.get("icir")
            group_spread = spread_stats.get("mean_spread")
            observations = int(stats.get("observations") or 0)
            pass_flag = int(
                observations >= 12
                and rank_ic is not None
                and group_spread is not None
                and float(rank_ic) > 0.0
                and float(group_spread) > 0.0
            )
            message = json.dumps(
                {
                    "维度": item.get("dimension_label"),
                    "因子": item.get("factor_label"),
                    "方向": item.get("direction"),
                    "IC_t值": stats.get("t_value"),
                    "IC衰减": stats.get("ic_decay_vs_train"),
                    "分层年化价差": spread_stats.get("annualized_spread"),
                    "分层t值": spread_stats.get("t_value"),
                    "测试期只报告": bool(stats.get("report_only")),
                },
                ensure_ascii=False,
                allow_nan=False,
            )
            test_rows.append((
                run_id,
                "SW31_INDUSTRY",
                factor_name,
                split,
                rank_ic,
                icir,
                group_spread,
                None,
                coverage,
                pass_flag,
                message,
            ))

    connection = sqlite3.connect(WAREHOUSE)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.executemany(
            "INSERT OR REPLACE INTO factor_value_daily "
            "(trade_date, ts_code, factor_name, factor_value, factor_group, source_agent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            "DELETE FROM factor_test_result WHERE run_id = ? AND universe = ?",
            (run_id, "SW31_INDUSTRY"),
        )
        connection.executemany(
            "INSERT INTO factor_test_result "
            "(run_id, universe, factor_name, split_name, rank_ic, icir, group_spread, turnover, coverage, pass_flag, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            test_rows,
        )
        connection.commit()
    finally:
        connection.close()
    manifest = _read_manifest()
    if manifest:
        manifest["database"] = _database_signature()
        manifest.setdefault("factor_library", {})["latest_run_id"] = run_id
        manifest["factor_library"]["source_agent"] = MODEL_VERSION
        manifest["factor_library"]["factor_value_rows"] = len(rows)
        manifest["factor_library"]["factor_test_rows"] = len(test_rows)
        _json_write(MANIFEST, manifest)
    return {"factor_value_rows": len(rows), "factor_test_rows": len(test_rows), "run_id": run_id}


def build_candidates(
    existing: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    industry_codes: dict[str, str],
    splits: dict[str, tuple[str, str]],
) -> dict[str, pd.DataFrame]:
    global _STATE
    columns = list(close.columns)
    daily, monthly, manifest = _load_or_build_inputs(columns)
    data_as_of = min(daily["trade_date"].max(), monthly["trade_date"].max())
    prosperity = _prosperity_factors(existing)
    monthly_scores, coverage = _monthly_factor_scores(monthly, close.index, columns)
    technical, funds, crowding = _daily_factor_scores(daily, close)
    dimensions, factor_scores = _dimension_scores(prosperity, monthly_scores, technical, funds, crowding)
    for frequency in dimensions:
        for name in dimensions[frequency]:
            frame = dimensions[frequency][name].copy()
            frame.loc[frame.index > data_as_of, :] = np.nan
            dimensions[frequency][name] = frame

    # The frozen C6 remains the return anchor.  Overlay factors are screened on
    # train and validation only, then orthogonalised against C6 so the expanded
    # framework cannot silently overwrite the historical champion.
    admitted = _admitted_dimensions(factor_scores)
    anchor = existing["C6_direct_month_smooth"]
    monthly_primary = _champion_overlay_score(
        anchor,
        admitted["monthly"],
        {"fundamental": 0.10, "valuation": 0.04, "technical": 0.18, "funds": 0.07},
    )
    monthly_conservative = _champion_overlay_score(
        anchor,
        admitted["monthly"],
        {"fundamental": 0.08, "valuation": 0.03, "technical": 0.12, "funds": 0.05},
    )
    monthly_quality_trend = _champion_overlay_score(
        anchor,
        admitted["monthly"],
        {"fundamental": 0.14, "valuation": 0.06, "technical": 0.14, "funds": 0.04},
    )
    weekly_primary = _champion_overlay_score(
        anchor,
        admitted["weekly"],
        {"fundamental": 0.04, "valuation": 0.02, "technical": 0.20, "funds": 0.10},
    )
    weekly_conservative = _champion_overlay_score(
        anchor,
        admitted["weekly"],
        {"fundamental": 0.03, "valuation": 0.02, "technical": 0.14, "funds": 0.07},
    )
    monthly_dates = [date for date in close.index.to_series().groupby(close.index.to_period("M")).max().tolist() if date <= data_as_of]
    weekly_dates = [date for date in close.index.to_series().groupby(close.index.to_period("W-FRI")).max().tolist() if date <= data_as_of]
    monthly_online, monthly_overlay_weights = _online_champion_overlay_score(
        anchor,
        admitted["monthly"],
        close,
        monthly_dates,
        {"fundamental": 0.15, "valuation": 0.06, "technical": 0.20, "funds": 0.10},
        lookback=36,
        minimum_history=12,
    )
    weekly_online, weekly_overlay_weights = _online_champion_overlay_score(
        anchor,
        admitted["weekly"],
        close,
        weekly_dates,
        {"fundamental": 0.05, "valuation": 0.03, "technical": 0.25, "funds": 0.12},
        lookback=104,
        minimum_history=26,
    )
    monthly_factor_stack, monthly_factor_stack_weights = _online_factor_stack_score(
        factor_scores,
        dimensions["monthly"],
        close,
        monthly_dates,
        "monthly",
    )
    weekly_factor_stack, weekly_factor_stack_weights = _online_factor_stack_score(
        factor_scores,
        dimensions["weekly"],
        close,
        weekly_dates,
        "weekly",
    )
    monthly_prosperity_earnings = _cross_section_rank(
        factor_scores["prosperity"]["prosperity_acceleration"].mul(0.32)
        .add(factor_scores["prosperity"]["prosperity_level"].mul(0.22), fill_value=0.0)
        .add(factor_scores["fundamental"]["op_yoy"].mul(0.18), fill_value=0.0)
        .add(factor_scores["fundamental"]["op_yoy_acceleration"].mul(0.12), fill_value=0.0)
        .add(factor_scores["fundamental"]["profit_positive_breadth"].mul(0.10), fill_value=0.0)
        .add(factor_scores["fundamental"]["earnings_quality_confirmation"].mul(0.08), fill_value=0.0)
        .sub(_crowding_risk(dimensions["monthly"]["crowding"]).mul(0.12), fill_value=0.0)
    )
    monthly_secondary_cluster = _weighted_dimension_score(
        dimensions["monthly"],
        {
            "prosperity": 0.28,
            "fundamental": 0.26,
            "technical": 0.20,
            "valuation": 0.08,
            "funds": 0.18,
        },
        crowding_penalty=0.16,
        consensus_weight=0.08,
    )
    monthly_secondary_gated = _cross_section_rank(
        _mean_available(
            [monthly_factor_stack, monthly_secondary_cluster, monthly_prosperity_earnings],
            2,
        )
    )
    anti_crowding = _cross_section_rank(1.0 - dimensions["monthly"]["crowding"])
    monthly_regime_offensive = _cross_section_rank(
        _mean_available(
            [monthly_factor_stack, monthly_secondary_cluster, monthly_prosperity_earnings, monthly_online],
            2,
        )
    )
    monthly_regime_defensive = _cross_section_rank(
        _mean_available(
            [
                anchor,
                dimensions["monthly"]["fundamental"],
                dimensions["monthly"]["valuation"],
                factor_scores["fundamental"]["profit_growth_stability"],
                anti_crowding,
            ],
            3,
        ).sub(_crowding_risk(dimensions["monthly"]["crowding"]).mul(0.10), fill_value=0.0)
    )
    monthly_regime_gated = _regime_blend_score(
        close,
        monthly_regime_offensive,
        monthly_regime_defensive,
        monthly_dates,
    )
    monthly_regime_stable = _cross_section_rank(
        _mean_available([monthly_regime_gated, monthly_secondary_gated, anchor], 2)
    )
    # Each architecture is economically distinct and frozen before evaluation:
    # fixed balanced consensus, matured-label online IC, defensive balance,
    # weekly fast evidence and weekly equal evidence.  The sealed test never
    # ranks these post-test diagnostic candidates.
    candidates = {
        "C25_monthly_post_test_diagnostic_six_dimension_consensus_top10_buffered": monthly_primary,
        "C26_monthly_post_test_diagnostic_six_dimension_online_ic_top10_buffered": monthly_online,
        "C27_monthly_post_test_diagnostic_six_dimension_defensive_top10_buffered": monthly_quality_trend,
        "C28_weekly_post_test_diagnostic_six_dimension_fast_top10_buffered": weekly_primary,
        "C29_weekly_post_test_diagnostic_six_dimension_equal_top10_buffered": weekly_online,
        "C35_monthly_post_test_diagnostic_six_dimension_online_factor_stack_top5_risk_weighted_buffered_cash25": monthly_factor_stack,
        "C36_weekly_post_test_diagnostic_six_dimension_online_factor_stack_top5_risk_weighted_buffered_cash25": weekly_factor_stack,
        "C39_monthly_post_test_diagnostic_six_dimension_prosperity_earnings_top7_risk_weighted_buffered": monthly_prosperity_earnings,
        "C41_monthly_post_test_diagnostic_secondary_factor_cluster_top5_risk_weighted_buffered_cash25": monthly_secondary_gated,
        "C42_monthly_post_test_diagnostic_layered_return_regime_gate_top5_risk_weighted_buffered_cash25": monthly_regime_gated,
        "C43_monthly_post_test_diagnostic_layered_return_stable_gate_top5_risk_weighted_buffered_cash35": monthly_regime_stable,
    }
    for name, score in candidates.items():
        score = score.copy()
        score.loc[score.index > data_as_of, :] = np.nan
        candidates[name] = score

    diagnostics = _factor_diagnostics(
        factor_scores,
        dimensions["monthly"],
        close,
        monthly_dates,
        splits,
    )
    persistence: dict[str, int] | None = None
    if os.environ.get("INDUSTRY_ROTATION_WRITE_FACTOR_DB") == "1":
        persistence = _persist_factor_library(
            industry_codes,
            monthly_dates,
            factor_scores,
            dimensions["monthly"],
            diagnostics,
            data_as_of,
        )
    current_monthly_overlay = (
        monthly_overlay_weights.iloc[-1].to_dict()
        if not monthly_overlay_weights.empty
        else {name: 0.0 for name in admitted["monthly"]}
    )
    current_weekly_overlay = (
        weekly_overlay_weights.iloc[-1].to_dict()
        if not weekly_overlay_weights.empty
        else {name: 0.0 for name in admitted["weekly"]}
    )
    rounded_online = {key: round(float(value), 6) for key, value in current_monthly_overlay.items()}
    rounded_weekly_online = {key: round(float(value), 6) for key, value in current_weekly_overlay.items()}
    latest_monthly_stack = (
        monthly_factor_stack_weights.iloc[-1].dropna().sort_values(ascending=False).head(12).to_dict()
        if not monthly_factor_stack_weights.empty
        else {}
    )
    latest_weekly_stack = (
        weekly_factor_stack_weights.iloc[-1].dropna().sort_values(ascending=False).head(12).to_dict()
        if not weekly_factor_stack_weights.empty
        else {}
    )
    _STATE = SixDimensionState(
        candidates=candidates,
        dimensions=dimensions,
        factor_scores=factor_scores,
        current_weights={
            "monthly_champion_anchor": 1.0,
            "monthly_overlay": {"fundamental": 0.10, "valuation": 0.04, "technical": 0.18, "funds": 0.07, "crowding": 0.0},
            "monthly_online_ic": rounded_online,
            "weekly_overlay": {"fundamental": 0.04, "valuation": 0.02, "technical": 0.20, "funds": 0.10, "crowding": 0.0},
            "weekly_online_ic": rounded_weekly_online,
            "monthly_online_factor_stack": {key: round(float(value), 6) for key, value in latest_monthly_stack.items()},
            "weekly_online_factor_stack": {key: round(float(value), 6) for key, value in latest_weekly_stack.items()},
            "monthly_secondary_factor_cluster": {
                "prosperity": 0.28,
                "fundamental": 0.26,
                "technical": 0.20,
                "valuation": 0.08,
                "funds": 0.18,
                "crowding_penalty": 0.16,
                "consensus_floor": 0.08,
            },
            "monthly_regime_gate": {
                "offensive": "online_factor_stack + secondary_cluster + prosperity_earnings + champion_overlay",
                "defensive": "champion_anchor + fundamental + valuation + profit_growth_stability + anti_crowding",
                "blend_signal": "126d/63d broad-market trend, 63d breadth, 63d volatility percentile; all past-only",
            },
        },
        diagnostics=diagnostics,
        data_quality={
            "status": "pass_with_quarantined_membership_conflicts",
            "pit_membership_overlap": 0,
            "ambiguous_membership_intervals_excluded": int(manifest["pit_membership"]["ambiguous_intervals_excluded"]),
            "daily_minimum_industry_count": manifest["daily"]["minimum_daily_industry_count"],
            "monthly_minimum_industry_count": manifest["monthly"]["minimum_monthly_industry_count"],
            "latest_financial_coverage_median": round(float(coverage["financial_coverage"].loc[:data_as_of].iloc[-1].median()), 6),
            "latest_valuation_coverage_median": round(float(coverage["valuation_coverage"].loc[:data_as_of].iloc[-1].median()), 6),
            "financial_availability": "visible_date严格早于信号日；无公告时刻时公告日不可用",
            "prosperity_availability": "源工作簿无逐条发布时间；月频期末+25自然日、周频+1交易日为统一保守滞后，不冒充真实vintage",
            "moneyflow_unit": "万元乘10后除以同覆盖股票的千元成交额；大单与超大单先对总流量做同日横截面残差化",
            "dividend_yield_unit": "decimal",
            "gross_margin_unit": "percentage_point; raw amount values normalised by total_revenue",
            "cache_manifest": str(MANIFEST),
            "factor_library_persistence": persistence or {"status": "disabled"},
        },
        data_as_of=data_as_of.strftime("%Y-%m-%d"),
        factor_count={name: len(rows) for name, rows in factor_scores.items()},
    )
    return candidates


def _effective_factor_count() -> dict[str, int]:
    if _STATE is None:
        return {}
    output = {name: 0 for name in ("prosperity", "fundamental", "technical", "valuation", "funds", "crowding")}
    for row in _STATE.diagnostics.get("atomic_factors", []):
        coverage = float(row.get("coverage") or 0.0)
        observed = any(
            int((row.get("ic") or {}).get(split, {}).get("observations") or 0) > 0
            for split in ("train", "validation", "test")
        )
        dimension = str(row.get("dimension") or "")
        if coverage > 0.0 and observed and dimension in output:
            output[dimension] += 1
    return output


def _safe(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if math.isfinite(number) else None


def ranking_components(frequency: str, date: pd.Timestamp, industry: str) -> dict[str, float | None]:
    if _STATE is None:
        return {}
    dimensions = _STATE.dimensions[frequency]
    output = {name: _safe(frame.at[date, industry]) for name, frame in dimensions.items() if date in frame.index and industry in frame.columns}
    if output.get("crowding") is not None:
        output["anti_crowding"] = round(1.0 - float(output["crowding"]), 6)
    return output


def enrich_frequency_payload(payload: dict[str, Any], frequency: str) -> dict[str, Any]:
    if _STATE is None:
        return payload
    dimensions = _STATE.dimensions[frequency]
    latest = pd.Timestamp(_STATE.data_as_of)
    research_candidate = str(payload.get("research_selected_candidate") or "")
    research_ranking: list[dict[str, Any]] = []
    research_score = _STATE.candidates.get(research_candidate)
    if research_score is not None:
        available = research_score.loc[research_score.index <= latest].dropna(how="all")
        if not available.empty:
            research_date = pd.Timestamp(available.index.max())
            code_by_name = {
                str(row.get("name")): row.get("code")
                for row in payload.get("ranking", [])
            }
            ordered = research_score.loc[research_date].dropna().sort_values(ascending=False)
            research_ranking = [
                {
                    "rank": rank,
                    "code": code_by_name.get(str(industry)),
                    "name": str(industry),
                    "score": round(float(value), 6),
                    "components": ranking_components(frequency, research_date, str(industry)),
                }
                for rank, (industry, value) in enumerate(ordered.items(), start=1)
            ]
    all_candidate_count = len(payload.get("candidate_audit", []))
    candidate_search_count = sum(
        "six_dimension" in str(row.get("candidate") or "")
        for row in payload.get("candidate_audit", [])
    )
    visible_candidates = {
        str(payload.get("selected_candidate") or ""),
        research_candidate,
    }
    payload["candidate_audit"] = [
        row for row in payload.get("candidate_audit", [])
        if str(row.get("candidate") or "") in visible_candidates
    ]
    payload["six_dimension"] = {
        "status": (
            "active"
            if "six_dimension" in str(payload.get("selected_candidate"))
            else "post_test_diagnostic_research_challenger"
        ),
        "data_as_of": _STATE.data_as_of,
        "research_candidate": research_candidate or None,
        "research_candidate_label": payload.get("research_selected_candidate_label"),
        "research_ranking": research_ranking,
        "research_result": payload.get("research_result", {}),
        "candidate_search_count": candidate_search_count,
        "all_candidate_count": all_candidate_count,
        "dimensions": [
            {"id": name, "label": DIMENSION_LABELS[name], "role": "risk_penalty" if name == "crowding" else "return_signal"}
            for name in ("prosperity", "fundamental", "technical", "valuation", "funds", "crowding")
        ],
        "factor_count": _STATE.factor_count,
        "effective_factor_count": _effective_factor_count(),
        "current_weights": _STATE.current_weights,
        "data_quality": _STATE.data_quality,
    }
    for row in payload.get("candidate_audit", []):
        if "six_dimension" in str(row.get("candidate")):
            execution = "波动倒数风险均衡" if "risk_weighted" in str(row.get("candidate")) else "等权"
            row["definition"] = f"五类收益维度按独立信息簇融合，拥挤度非负连续扣分，Top10{execution}并保留3名缓冲"
            row["architecture"] = MODEL_VERSION
    return payload


def enrich_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if _STATE is None:
        return snapshot
    snapshot["engine_version"] = MODEL_VERSION
    snapshot["status_reason"] = "R32冠军方向、六维PIT因子、行业归属去重、公告可见日、T+1执行、成本后回测和测试期只报告门禁已运行。"
    method = snapshot.setdefault("method", {})
    method["industry_portfolio"] = "生产方案为R32方向冻结的C6直接景气月度平滑；六维综合分仅作为研究挑战者，训练与验证未胜出时不得覆盖冠军"
    method["factor_contract"] = [
        "景气度：31行业各8项专属产业字段",
        "基本面：visible_date严格早于信号日的财务质量与增长",
        "技术面：行业相对动量、风险调整趋势、路径效率与股票扩散度",
        "估值：正盈利收益率、账面收益率、销售收益率与股息率",
        "资金面：主力总流量强度、大单结构残差、超大单结构残差及流入扩散度",
        "拥挤度：换手、量比、成交集中、涨停热度、偏离与波动扩张的连续风险扣分",
    ]
    method["test_policy"] = "景气契约生产方向固定读取R32冠军参数；21交易日前瞻IC只作训练期方向漂移诊断，成熟样本不足时回退R32冠军方向；六维因子只读取当时可见数据；训练与验证选择唯一挑战者；2022年后测试集仅报告或否决，不参与调参"
    method["forbidden_fields"] = ["信号日之后数据", "公告日同日财报", "缺失值补0", "测试期调权", "LLM生成数值因子"]
    method["missing_policy"] = "缺失不补0；原子因子、维度和总分按当前可见项重新归一化；覆盖不足时该项缺失"
    method["llm_policy"] = "LLM只解释证据和数据缺口，不生成数值因子、不修改权重、不参与求解"
    snapshot["industry"]["source"] = "申万行业指数收益；景气工作簿与research_warehouse.db六维PIT信号"
    monthly_research = (
        snapshot["industry"]["frequencies"]["monthly"]
        .get("six_dimension", {})
        .get("research_ranking", [])
    )
    research_by_name = {row.get("name"): row for row in monthly_research}
    for row in snapshot.get("high_frequency", {}).get("industries", []):
        rank = research_by_name.get(row.get("industry"), {})
        row["six_dimension"] = {
            "as_of": _STATE.data_as_of,
            "research_rank": rank.get("rank"),
            "research_score": rank.get("score"),
            "components": rank.get("components", {}),
        }
    summary = snapshot.get("high_frequency", {}).get("summary", {})
    summary["policy"] = "每行业8项专属景气字段保持独立；六维综合分在行业配置层融合，不改写产业字段。"
    snapshot["six_dimension"] = {
        "model_version": MODEL_VERSION,
        "data_as_of": _STATE.data_as_of,
        "dimension_labels": DIMENSION_LABELS,
        "factor_labels": FACTOR_LABELS,
        "factor_count": _STATE.factor_count,
        "effective_factor_count": _effective_factor_count(),
        "data_quality": _STATE.data_quality,
        "current_weights": _STATE.current_weights,
        "diagnostics": _STATE.diagnostics,
        "governance": {
            "selection": "训练与验证",
            "test": "仅报告或否决",
            "promotion": "未通过原有冠军挑战门时保留为研究挑战者，不伪造夏普",
        },
    }
    snapshot.setdefault("source_audit", []).extend([
        {
            "source": "research_warehouse.db / sw_l1_industry_daily",
            "purpose": "股票至申万一级行业PIT归属；同日起始多行业冲突区间隔离并进入质量告警",
            "status": "ok",
            "as_of": _STATE.data_as_of,
        },
        {
            "source": "research_warehouse.db / stock_ohlcv_daily + stock_valuation_daily + stock_moneyflow_daily",
            "purpose": "技术、估值、资金与拥挤因子；资金单位及覆盖成交额一致",
            "status": "ok",
            "as_of": _STATE.data_as_of,
        },
        {
            "source": "research_warehouse.db / financial_report_visible",
            "purpose": "公告可见日基本面因子；无公告时刻时次交易日起可用",
            "status": "ok",
            "as_of": _STATE.data_as_of,
        },
    ])
    return snapshot


def component_history(frequency: str, dates: Iterable[pd.Timestamp], industry: str) -> dict[str, dict[str, float | None]]:
    if _STATE is None:
        return {}
    output: dict[str, dict[str, float | None]] = {}
    for date in dates:
        output[pd.Timestamp(date).strftime("%Y-%m-%d")] = ranking_components(frequency, pd.Timestamp(date), industry)
    return output

