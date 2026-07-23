from __future__ import annotations

import argparse
import calendar
import copy
import csv
import gzip
import io
import json
import math
import statistics
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from xlsx_reader import XlsxReader, column_name, column_number


PALETTE = ["#c00000", "#ffc000", "#2f75b5", "#808080", "#ed7d31", "#7030a0", "#00b050", "#5b9bd5", "#a5a5a5", "#ff0000"]
START_FLOOR = date(2010, 1, 1)
TODAY = date.today()


SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "ifind_retail": {
        "label": "iFinD / Wind A股资金流向",
        "primary": "iFinD THS_DS/THS_DataPool 小单净额；Wind ASHAREMONEYFLOW 交叉核验",
        "fields": ["trade_dt", "value_diff_small_trader", "value_diff_small_trader_act"],
        "frequency": "日频，收盘后",
        "fallback": "Wind ASHAREMONEYFLOW；免费源仅用于方向核验，不替代金额口径",
        "quality": "同一交易日全A聚合；万元转亿元；不前向填充",
    },
    "wind_accounts": {
        "label": "Wind / iFinD EDB 投资者与开户",
        "primary": "Wind EDB 或 iFinD /api/v1/edb_service",
        "fields": ["参与交易投资者数量", "A股新增开户数"],
        "frequency": "日频/周频/月频，按原发布频率",
        "fallback": "交易所/中国结算公开发布",
        "quality": "月度序列只在月末对齐；禁止与日频直接前向填充",
    },
    "wind_public_fund": {
        "label": "Wind 公募基金数据库",
        "primary": "CHINAMUTUALFUNDDESCRIPTION / CHINAMUTUALFUNDSHARE / CHINAMUTUALFUNDASSETPORTFOLIO",
        "fields": ["f_info_setupdate", "f_issue_totalunit", "change_date", "fundshare_total", "f_prt_enddate", "f_prt_stocktonav"],
        "frequency": "日更，按周/月聚合",
        "fallback": "iFinD basic_data/date_sequence；RQData 基金基础资料与净值",
        "quality": "基金分类使用点时有效分类；份额亿份、资产元统一换算",
    },
    "ifind_fund_registry": {
        "label": "iFinD 产品报会与清算",
        "primary": "iFinD data_pool / edb_service，服务端配置专题报表及 EDB 指标 ID",
        "fields": ["新增产品报会数量", "基金清算数量", "清算规模"],
        "frequency": "周频/月频",
        "fallback": "Wind CHINAFUNDMAJOREVENT 与基金基本资料",
        "quality": "按公告日和事件类型去重；清算规模万元转亿元",
    },
    "wind_etf": {
        "label": "Wind ETF 份额/申赎/净值",
        "primary": "CHINAETFWEEKPCHREDM + CHINAMUTUALFUNDSHARE + CHINAMUTUALFUNDNAV",
        "fields": ["f_pchredm_enddate", "f_unit_pch", "f_unit_redm", "change_date", "fundshare_total", "f_nav_unit"],
        "frequency": "周频",
        "fallback": "iFinD ETF 专题报表；Tushare fund_share/fund_daily",
        "quality": "净申赎=申购-赎回；资金流=份额变动×复权单位净值；分类点时有效",
    },
    "wind_margin": {
        "label": "Wind 融资融券",
        "primary": "ASHAREMARGINTRADESUM / ASHAREMARGINTRADE / ASHAREMARGINSUBJECT",
        "fields": ["trade_dt", "s_marsum_tradingbalance", "s_marsum_purchwithborrowmoney", "s_marsum_repaymenttobroker", "s_margin_conversionrate"],
        "frequency": "日频，网页按周聚合",
        "fallback": "iFinD date_sequence；Tushare margin/margin_detail；交易所公开数据",
        "quality": "沪深交易所分别求和；融资净买入=买入额-偿还额；元转亿元",
    },
    "wind_primary": {
        "label": "Wind 一级市场发行",
        "primary": "ASHAREIPO / ASHARESEO / CBONDDESCRIPTION + CCBONDISSUANCE",
        "fields": ["s_ipo_subdate", "s_ipo_collection", "s_fellow_date", "s_fellow_collection", "b_issue_firstissue", "b_issue_amountact"],
        "frequency": "事件日，按周聚合",
        "fallback": "iFinD data_pool；交易所与证监会公告",
        "quality": "仅统计已实施；IPO万元、定增元、债券亿元统一为亿元",
    },
    "wind_private": {
        "label": "Wind 私募基金 / 华泰托管策略指数",
        "primary": "CHINAHEDGEFUNDDESCRIPTION / CHINAHEDGEFUNDNAV / CHINAHEDGEFUNDPERFORMANCE",
        "fields": ["f_info_setupdate", "f_info_status", "price_date", "f_nav_accumulated"],
        "frequency": "月频/周频/日频",
        "fallback": "中基协公开备案；私募排排网会员数据；托管样本指数",
        "quality": "存续口径按月末；策略指数等权且不外推缺失净值",
    },
    "wind_foreign": {
        "label": "Wind EDB / EPFR 外资配置",
        "primary": "Wind EDB、EPFR 授权数据与陆股通成交统计",
        "fields": ["主动配置流", "被动配置流", "A/H累计配置", "陆股通成交额", "外资仓位"],
        "frequency": "周频/月频",
        "fallback": "iFinD edb_service；交易所陆股通成交公开数据",
        "quality": "美元/人民币分轴；EPFR 周期不与交易日数据前向填充；披露规则变化显式标注",
    },
    "fred_cb": {
        "label": "FRED / 各央行资产负债表",
        "primary": "FRED CSV: WALCL, ECBASSETSW, JPNASSETS",
        "fields": ["WALCL", "ECBASSETSW", "JPNASSETS"],
        "frequency": "周频/月频，图中统一为月末最后观测",
        "fallback": "Federal Reserve H.4.1 / ECB WFS / BOJ Accounts",
        "quality": "不同币种仅展示2010=100指数，不直接相加",
    },
    "fred_us_liquidity": {
        "label": "FRED 美国美元流动性",
        "primary": "FRED CSV: WALCL, WDTGAL, RRPONTSYD",
        "fields": ["WALCL", "WDTGAL", "RRPONTSYD"],
        "frequency": "周频，周三交集",
        "fallback": "Fed H.4.1 与 New York Fed ON RRP",
        "quality": "单位统一十亿美元；净流动性=Fed总资产-TGA-ON RRP",
    },
    "fred_rates": {
        "label": "FRED 美元担保融资利率",
        "primary": "FRED CSV: SOFR, IORB, DFF",
        "fields": ["SOFR", "IORB", "DFF"],
        "frequency": "日频，共同有效交易日",
        "fallback": "New York Fed SOFR / Federal Reserve IORB 与 EFFR",
        "quality": "只取三者共同非空日期；百分比不做前向填充",
    },
    "cftc_tff": {
        "label": "CFTC Traders in Financial Futures",
        "primary": "CFTC 年度 fut_fin_txt_YYYY.zip",
        "fields": ["Open_Interest_All", "Asset_Mgr_Positions_Long/Short_All", "Lev_Money_Positions_Long/Short_All", "Dealer_Positions_Long/Short_All"],
        "frequency": "周频（周二持仓，周五发布）",
        "fallback": "CFTC Public Reporting Environment API",
        "quality": "精确匹配 E-MINI S&P 500；净头寸除以总持仓，避免合约规模漂移",
    },
}

SOURCE_REGISTRY.update({
    "ofr_rates": {
        "label": "OFR / 纽约联储参考利率",
        "primary": "OFR STFM fnyr 数据集（原始来源：Federal Reserve Bank of New York）",
        "fields": ["FNYR-SOFR-A", "FNYR-EFFR-A", "FNYR-OBFR-A"],
        "frequency": "日频，共同有效业务日",
        "fallback": "New York Fed Markets Data API",
        "quality": "三条利率仅取共同非空日期；单位均为百分比；不前向填充",
    },
    "ofr_repo": {
        "label": "OFR 美国回购市场",
        "primary": "OFR STFM repo 数据集",
        "fields": ["REPO-DVP_TV_TOT-F", "REPO-DVP_OV_TOT-F", "REPO-TRIV1_TV_TOT-F"],
        "frequency": "日频，Final vintage",
        "fallback": "OFR U.S. Repo Markets Data Release",
        "quality": "统一由美元换算为十亿美元；只取三序列共同有效日期；不填补披露缺口",
    },
    "ofr_mmf": {
        "label": "OFR 美国货币市场基金",
        "primary": "OFR STFM mmf 数据集",
        "fields": ["MMF-MMF_TOT-M", "MMF-MMF_T_TOT-M", "MMF-MMF_RP_TOT-M"],
        "frequency": "月频，完整序列修订口径",
        "fallback": "OFR U.S. Money Market Fund Data Release",
        "quality": "统一由美元换算为十亿美元；月末共同交集；不混入周频或日频值",
    },
})


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, "", "--", "."):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def extract_maps(reader: XlsxReader, sheet: str, date_col: str, fields: dict[str, str], start_row: int, end_row: int, scales: dict[str, float] | None = None) -> dict[str, dict[date, float]]:
    scales = scales or {}
    all_cols = [column_number(date_col)] + [column_number(col) for col in fields.values()]
    low, high = min(all_cols), max(all_cols)
    matrix = reader.read_rect(sheet, start_row, low, end_row, high)
    output = {key: {} for key in fields}
    for row in matrix:
        when = parse_date(row[column_number(date_col) - low])
        if when is None:
            continue
        for key, col in fields.items():
            value = finite(row[column_number(col) - low])
            if value is not None:
                output[key][when] = value * scales.get(key, 1.0)
    return output

def zero_fill_event_maps(reader: XlsxReader, sheet: str, date_col: str, fields: dict[str, str], start_row: int, end_row: int, scales: dict[str, float] | None = None) -> dict[str, dict[date, float]]:
    output = extract_maps(reader, sheet, date_col, fields, start_row, end_row, scales)
    date_rows = reader.read_range(sheet, f"{date_col}{start_row}:{date_col}{end_row}")
    dates = [when for row in date_rows if row and (when := parse_date(row[0])) is not None]
    for values in output.values():
        for when in dates:
            values.setdefault(when, 0.0)
    return output



def derived_map(source: dict[date, float], fn) -> dict[date, float]:
    result: dict[date, float] = {}
    for when, value in source.items():
        derived = finite(fn(value))
        if derived is not None:
            result[when] = derived
    return result


def rolling_mean(source: dict[date, float], window: int) -> dict[date, float]:
    items = sorted(source.items())
    return {when: statistics.fmean(value for _, value in items[index - window + 1 : index + 1]) for index, (when, _) in enumerate(items) if index + 1 >= window}


def year_over_year(source: dict[date, float]) -> dict[date, float]:
    by_month = {(when.year, when.month): value for when, value in source.items()}
    result: dict[date, float] = {}
    for when, value in source.items():
        prior = by_month.get((when.year - 1, when.month))
        if prior not in (None, 0):
            result[when] = (value / prior - 1) * 100
    return result


def nice_unit(raw: float) -> float:
    if not math.isfinite(raw) or raw <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw))
    fraction = raw / (10**exponent)
    choice = 1 if fraction <= 1 else 2 if fraction <= 2 else 2.5 if fraction <= 2.5 else 5 if fraction <= 5 else 10
    return choice * (10**exponent)


def nice_axis(values: Iterable[float], label_count: int = 12) -> dict[str, float]:
    clean = [float(value) for value in values if finite(value) is not None]
    if not clean:
        return {"min": 0, "max": 1, "dtick": 0.1}
    low, high = min(clean), max(clean)
    if low == high:
        padding = abs(low) * 0.1 or 1
        low, high = low - padding, high + padding
    unit = nice_unit((high - low) / max(1, label_count - 1))
    axis_low = math.floor(low / unit) * unit
    axis_high = math.ceil(high / unit) * unit
    if axis_low == axis_high:
        axis_high += unit
    return {"min": axis_low, "max": axis_high, "dtick": unit}


@dataclass
class TraceInput:
    name: str
    values: dict[date, float]
    chart_type: str = "line"
    axis: str = "left"
    color: str = PALETTE[0]
    source_id: str = ""
    color_by_sign: bool = False
    dash: str = "solid"


def longest_block(dates: list[date], frequency: str) -> list[date]:
    if not dates:
        return []
    blocks: list[list[date]] = []
    start = 0

    def is_gap(previous: date, current: date) -> bool:
        if frequency == "monthly":
            return (current.year - previous.year) * 12 + current.month - previous.month > 1
        limit = {"daily": 11, "weekly": 14}.get(frequency, 400)
        return (current - previous).days > limit

    for index in range(1, len(dates)):
        if is_gap(dates[index - 1], dates[index]):
            blocks.append(dates[start:index])
            start = index
    blocks.append(dates[start:])
    latest = blocks[-1]
    minimum_current = 8
    return latest if len(latest) >= minimum_current else max(blocks, key=lambda block: (len(block), block[-1]))


def time_chart(chart_id: str, title: str, subtitle: str, frequency: str, unit_left: str, traces: list[TraceInput], reference: str, unit_right: str | None = None, note: str = "") -> dict[str, Any]:
    common = set.intersection(*(set(trace.values) for trace in traces)) if traces else set()
    common = {when for when in common if when >= START_FLOOR}
    ordered = longest_block(sorted(common), frequency)
    if len(ordered) < 2:
        raise ValueError(f"{chart_id}: fewer than two common observations")
    rendered = []
    left_values: list[float] = []
    right_values: list[float] = []
    for trace in traces:
        values = [trace.values[when] for when in ordered]
        (right_values if trace.axis == "right" else left_values).extend(values)
        rendered.append({
            "name": trace.name,
            "type": trace.chart_type,
            "axis": trace.axis,
            "color": trace.color,
            "color_by_sign": trace.color_by_sign,
            "dash": trace.dash,
            "source_id": trace.source_id,
            "x": [when.isoformat() for when in ordered],
            "y": [round(value, 8) for value in values],
        })
    span_months = (ordered[-1].year - ordered[0].year) * 12 + ordered[-1].month - ordered[0].month
    gaps = [(ordered[i] - ordered[i - 1]).days for i in range(1, len(ordered))]
    axes: dict[str, Any] = {"left": {"title": unit_left, **nice_axis(left_values)}}
    if right_values:
        axes["right"] = {"title": unit_right or "", **nice_axis(right_values)}
    return {
        "id": chart_id,
        "kind": "time",
        "title": title,
        "subtitle": subtitle,
        "frequency": frequency,
        "reference": reference,
        "note": note,
        "x_tick": {"dtick": "M6" if span_months >= 24 else "M1", "format": "%Y-%m"},
        "axes": axes,
        "traces": rendered,
        "quality": {
            "status": "passed",
            "common_observations": len(ordered),
            "start": ordered[0].isoformat(),
            "end": ordered[-1].isoformat(),
            "largest_gap_days": max(gaps) if gaps else 0,
            "missing_after_intersection": 0,
            "duplicate_dates": 0,
            "monotonic": True,
        },
    }


def category_chart(chart_id: str, title: str, subtitle: str, categories: list[str], series: list[dict[str, Any]], unit: str, reference: str, note: str = "") -> dict[str, Any]:
    if len(set(categories)) != len(categories) or not categories:
        raise ValueError(f"{chart_id}: invalid or duplicate categories")
    all_values: list[float] = []
    traces = []
    for index, spec in enumerate(series):
        values = [finite(value) for value in spec["values"]]
        if any(value is None for value in values) or len(values) != len(categories):
            raise ValueError(f"{chart_id}: category/value mismatch")
        clean = [float(value) for value in values if value is not None]
        all_values.extend(clean)
        traces.append({"name": spec["name"], "type": spec.get("type", "bar"), "axis": "left", "color": spec.get("color", PALETTE[index]), "color_by_sign": spec.get("color_by_sign", False), "source_id": spec["source_id"], "x": categories, "y": clean})
    return {
        "id": chart_id,
        "kind": "category",
        "title": title,
        "subtitle": subtitle,
        "reference": reference,
        "note": note,
        "axes": {"left": {"title": unit, **nice_axis(all_values)}},
        "traces": traces,
        "quality": {"status": "passed", "categories": len(categories), "missing": 0, "duplicate_categories": 0},
    }


def cross_section(reader: XlsxReader, sheet: str, category_range: str, value_range: str) -> tuple[list[str], list[float]]:
    categories = [row[0] for row in reader.read_range(sheet, category_range)]
    values = [row[0] for row in reader.read_range(sheet, value_range)]
    pairs = [(str(category).strip(), finite(value)) for category, value in zip(categories, values) if category not in (None, "") and finite(value) is not None]
    return [item[0] for item in pairs], [float(item[1]) for item in pairs]


def fred_series(series_id: str, cache_root: Path) -> dict[date, float]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    cache_dir = cache_root / "fred"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{series_id}.csv"
    text = ""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "QuantStrategyAgent-Liquidity/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response:
                text = response.read().decode("utf-8-sig")
            if text.strip():
                cache_path.write_text(text, encoding="utf-8")
                break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if not text and cache_path.exists():
        text = cache_path.read_text(encoding="utf-8-sig")
    if not text:
        raise RuntimeError(f"FRED download failed for {series_id}: {last_error}")
    output: dict[date, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        when = parse_date(row.get("observation_date") or row.get("DATE"))
        value = finite(row.get(series_id) or row.get("VALUE"))
        if when and value is not None:
            output[when] = value
    if not output:
        raise ValueError(f"FRED returned no data for {series_id}")
    return output

def cached_json(url: str, cache_path: Path) -> dict[str, Any]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "QuantStrategyAgent-Liquidity/1.0", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            text = raw.decode("utf-8-sig")
            payload = json.loads(text)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if cache_path.exists():
        cache_age_days = (TODAY - datetime.fromtimestamp(cache_path.stat().st_mtime).date()).days
        if cache_age_days <= 14:
            return json.loads(cache_path.read_text(encoding="utf-8"))
    raise RuntimeError(f"official JSON download failed and no fresh cache is available: {url}; {last_error}")


def ofr_dataset(dataset: str, cache_root: Path) -> dict[str, Any]:
    return cached_json(f"https://data.financialresearch.gov/v1/series/dataset?dataset={dataset}", cache_root / "ofr" / f"{dataset}.json")


def ofr_series(payload: dict[str, Any], series_id: str, scale: float = 1.0) -> dict[date, float]:
    try:
        aggregation = payload["timeseries"][series_id]["timeseries"]["aggregation"]
    except KeyError as exc:
        raise ValueError(f"OFR series not found: {series_id}") from exc
    output: dict[date, float] = {}
    for item in aggregation:
        if not isinstance(item, list) or len(item) < 2:
            continue
        when, value = parse_date(item[0]), finite(item[1])
        if when and value is not None:
            output[when] = float(value) / scale
    if not output:
        raise ValueError(f"OFR returned no observations for {series_id}")
    return output



def month_last(source: dict[date, float]) -> dict[date, float]:
    latest: dict[tuple[int, int], tuple[date, float]] = {}
    for when, value in source.items():
        key = (when.year, when.month)
        if key not in latest or when > latest[key][0]:
            latest[key] = (when, value)
    return {date(year, month, calendar.monthrange(year, month)[1]): value for (year, month), (_, value) in latest.items()}


def normalize_common(series: dict[str, dict[date, float]]) -> dict[str, dict[date, float]]:
    common = sorted(set.intersection(*(set(values) for values in series.values())))
    common = [when for when in common if when >= START_FLOOR]
    if not common:
        raise ValueError("no common dates to normalize")
    base = common[0]
    return {name: {when: values[when] / values[base] * 100 for when in common} for name, values in series.items()}


def cftc_sp500(cache_dir: Path, start_year: int = 2010) -> dict[str, dict[date, float]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = {"asset": {}, "leveraged": {}, "dealer": {}}
    targets = {
        "E-MINI S&P 500 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
        "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    }
    for year in range(start_year, TODAY.year + 1):
        path = cache_dir / f"fut_fin_txt_{year}.zip"
        if not path.exists():
            url = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 QuantStrategyAgent-Liquidity/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                path.write_bytes(response.read())
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(archive.namelist()[0]).decode("latin1")
        for row in csv.DictReader(io.StringIO(raw)):
            if row.get("Market_and_Exchange_Names", "").strip() not in targets:
                continue
            when = parse_date(row.get("Report_Date_as_YYYY-MM-DD"))
            open_interest = finite(row.get("Open_Interest_All"))
            if when is None or not open_interest:
                continue
            for key, prefix in (("asset", "Asset_Mgr"), ("leveraged", "Lev_Money"), ("dealer", "Dealer")):
                long_value = finite(row.get(f"{prefix}_Positions_Long_All"))
                short_value = finite(row.get(f"{prefix}_Positions_Short_All"))
                if long_value is not None and short_value is not None:
                    output[key][when] = (long_value - short_value) / open_interest * 100
    if min(len(values) for values in output.values()) < 100:
        raise ValueError("CFTC S&P 500 history is incomplete")
    return output


def page(title: str, subtitle: str, conclusion: str, charts: list[dict[str, Any]]) -> dict[str, Any]:
    end_dates = [chart.get("quality", {}).get("end") for chart in charts if chart.get("quality", {}).get("end")]
    return {"title": title, "subtitle": subtitle, "conclusion": conclusion, "as_of": max(end_dates) if end_dates else TODAY.isoformat(), "charts": charts}


def build(args: argparse.Namespace) -> dict[str, Any]:
    with XlsxReader(args.huatai_main) as main, XlsxReader(args.huatai_retail_fund) as enhanced, XlsxReader(args.private_custody) as private:
        retail_flow = extract_maps(enhanced, "散户资金", "P", {"net": "Q", "ma7": "T"}, 5, 1002)
        retail_cum = extract_maps(enhanced, "散户资金", "P", {"since_202311": "U", "since_202403": "V"}, 353, 1002)
        retail_activity = extract_maps(enhanced, "散户资金", "X", {"investors": "Y"}, 5, 806)
        retail_accounts = extract_maps(enhanced, "散户资金", "AA", {"accounts": "AB"}, 5, 1002)
        account_ma3 = rolling_mean(retail_accounts["accounts"], 3)
        account_yoy = year_over_year(retail_accounts["accounts"])
        retail_charts = [
            time_chart("retail-flow", "散户小单净流入与7日均线", "红涨绿跌；仅保留净额与均线共同有效交易日", "daily", "亿元", [TraceInput("散户净买入", retail_flow["net"], "bar", color=PALETTE[0], source_id="ifind_retail", color_by_sign=True), TraceInput("7日均线", retail_flow["ma7"], color=PALETTE[2], source_id="ifind_retail")], "华泰《散户-基金》散户资金 P/Q/T"),
            time_chart("retail-cumulative", "散户累计净买入", "两套起始日口径严格取共同日期，不拼接错位", "daily", "亿元", [TraceInput("自2023-11-01", retail_cum["since_202311"], color=PALETTE[0], source_id="ifind_retail"), TraceInput("自2024-03-01", retail_cum["since_202403"], color=PALETTE[2], source_id="ifind_retail")], "华泰《散户-基金》散户资金 P/U/V"),
            time_chart("retail-accounts", "A股新增开户与趋势", "月度新增开户、3月均线与同比；发布月末对齐", "monthly", "万户", [TraceInput("新增开户", retail_accounts["accounts"], "bar", color=PALETTE[0], source_id="wind_accounts"), TraceInput("3月均线", account_ma3, color=PALETTE[2], source_id="wind_accounts"), TraceInput("同比", account_yoy, axis="right", color=PALETTE[3], source_id="wind_accounts")], "华泰《散户-基金》散户资金 AA/AB；同比与均线为看板派生", "%"),
            time_chart("retail-participation", "参与交易投资者与散户资金", "投资者数量与小单7日均线取共同交易日", "daily", "投资者数量", [TraceInput("参与交易投资者", retail_activity["investors"], color=PALETTE[0], source_id="wind_accounts"), TraceInput("小单7日均线", retail_flow["ma7"], axis="right", color=PALETTE[2], source_id="ifind_retail")], "华泰《散户-基金》散户资金 X/Y 与 P/T", "亿元"),
        ]

        public_new = extract_maps(enhanced, "公募基金", "P", {"new": "Q", "detrended": "R", "hma4": "S"}, 3, 3729)
        public_filings = zero_fill_event_maps(enhanced, "公募基金", "T", {"stock": "U", "mixed": "V"}, 3, 3729)
        filing_total = {when: public_filings["stock"][when] + public_filings["mixed"][when] for when in set(public_filings["stock"]) & set(public_filings["mixed"])}
        public_position = extract_maps(enhanced, "公募基金", "AC", {"stock": "AD", "mixed": "AE"}, 3, 3729, {"stock": 100, "mixed": 100})
        public_liq = zero_fill_event_maps(enhanced, "公募基金", "AG", {"count": "AH", "size": "AI"}, 3, 3729, {"size": 0.0001})
        public_charts = [
            time_chart("public-new", "新成立偏股基金份额", "原值、去趋势值与4周HMA共同交集", "weekly", "亿份", [TraceInput("新成立份额", public_new["new"], "bar", color=PALETTE[0], source_id="wind_public_fund"), TraceInput("去趋势", public_new["detrended"], color=PALETTE[2], source_id="wind_public_fund"), TraceInput("4周HMA", public_new["hma4"], color=PALETTE[3], source_id="wind_public_fund")], "华泰《散户-基金》公募基金 P/Q/R/S"),
            time_chart("public-filings", "新增产品报会", "股票型、混合型与两者合计", "weekly", "只", [TraceInput("股票型", public_filings["stock"], "bar", color=PALETTE[0], source_id="ifind_fund_registry"), TraceInput("混合型", public_filings["mixed"], "bar", color=PALETTE[2], source_id="ifind_fund_registry"), TraceInput("合计", filing_total, color=PALETTE[3], source_id="ifind_fund_registry")], "华泰《散户-基金》公募基金 T/U/V"),
            time_chart("public-position", "主动偏股基金股票仓位", "普通股票型与偏股混合型，统一百分比", "daily", "%", [TraceInput("普通股票型", public_position["stock"], color=PALETTE[0], source_id="wind_public_fund"), TraceInput("偏股混合型", public_position["mixed"], color=PALETTE[2], source_id="wind_public_fund")], "华泰《散户-基金》公募基金 AC/AD/AE"),
            time_chart("public-liquidation", "基金清算数量与规模", "月度事件数与清算规模双轴", "monthly", "只", [TraceInput("清算数量", public_liq["count"], "bar", color=PALETTE[0], source_id="ifind_fund_registry"), TraceInput("清算规模", public_liq["size"], axis="right", color=PALETTE[2], source_id="ifind_fund_registry")], "华泰《散户-基金》公募基金 AG/AH/AI", "亿元"),
        ]

        etf_share = extract_maps(main, "ETF", "G", {"all": "H", "sse": "I", "szse": "J"}, 3, 638)
        etf_flow = extract_maps(main, "ETF", "L", {"total": "M", "broad": "N", "other": "O"}, 3, 638)
        big_cat, big_val = cross_section(main, "ETF", "R3:R9", "S3:S9")
        ind_cat, ind_val = cross_section(main, "ETF", "R12:R38", "S12:S38")
        etf_charts = [
            time_chart("etf-share", "ETF净申购赎回份额", "全市场、上交所与深交所周度净份额", "weekly", "亿份", [TraceInput("全市场", etf_share["all"], color=PALETTE[0], source_id="wind_etf"), TraceInput("上交所", etf_share["sse"], "bar", color=PALETTE[2], source_id="wind_etf"), TraceInput("深交所", etf_share["szse"], "bar", color=PALETTE[3], source_id="wind_etf")], "华泰资金面数据库 ETF G/H/I/J"),
            time_chart("etf-flow", "ETF资金净流入", "总资金流、宽基ETF与其他ETF", "weekly", "亿元", [TraceInput("总资金流", etf_flow["total"], color=PALETTE[0], source_id="wind_etf"), TraceInput("宽基ETF", etf_flow["broad"], "bar", color=PALETTE[2], source_id="wind_etf"), TraceInput("其他ETF", etf_flow["other"], "bar", color=PALETTE[3], source_id="wind_etf")], "华泰资金面数据库 ETF L/M/N/O"),
            category_chart("etf-sector", "ETF大类板块流入", "最近完整周；红色为净流入、绿色为净流出", big_cat, [{"name": "最近一周", "values": big_val, "source_id": "wind_etf", "color_by_sign": True}], "亿元", "华泰资金面数据库 ETF R3:S9"),
            category_chart("etf-industry", "ETF行业流入", "申万行业口径；按原表顺序展示", ind_cat, [{"name": "最近一周", "values": ind_val, "source_id": "wind_etf", "color_by_sign": True}], "亿元", "华泰资金面数据库 ETF R12:S38"),
        ]

        margin_week = extract_maps(enhanced, "融资资金", "P", {"net": "Q", "balance": "R", "activity": "S", "ma7": "T"}, 3, 1147)
        collateral = extract_maps(enhanced, "融资资金", "W", {"ratio": "X", "cash": "Y", "securities": "Z", "sec_cash": "AA"}, 3, 1147)
        common_collateral = set(collateral["cash"]) & set(collateral["securities"])
        cash_share = {when: collateral["cash"][when] / (collateral["cash"][when] + collateral["securities"][when]) * 100 for when in common_collateral if collateral["cash"][when] + collateral["securities"][when] > 0}
        sec_share = {when: 100 - value for when, value in cash_share.items()}
        margin_cat, margin_val = cross_section(enhanced, "融资资金", "AE3:AE32", "AF3:AF32")
        average_margin = statistics.fmean(margin_val)
        margin_charts = [
            time_chart("margin-flow", "融资净买入与7日均线", "周度净买入与平滑趋势", "weekly", "亿元", [TraceInput("融资净买入", margin_week["net"], "bar", color=PALETTE[0], source_id="wind_margin", color_by_sign=True), TraceInput("7日均线", margin_week["ma7"], color=PALETTE[2], source_id="wind_margin")], "华泰《散户-基金》融资资金 P/Q/T"),
            time_chart("margin-balance", "融资余额与交易活跃度", "余额和融资买入占成交额双轴", "weekly", "亿元", [TraceInput("融资余额", margin_week["balance"], color=PALETTE[0], source_id="wind_margin"), TraceInput("融资活跃度", margin_week["activity"], axis="right", color=PALETTE[2], source_id="wind_margin")], "华泰《散户-基金》融资资金 P/R/S", "%"),
            time_chart("margin-collateral", "平均担保比例与证券/现金比", "同一周末口径双轴", "weekly", "平均担保比例（%）", [TraceInput("平均担保比例", collateral["ratio"], color=PALETTE[0], source_id="wind_margin"), TraceInput("证券市值/现金", collateral["sec_cash"], axis="right", color=PALETTE[2], source_id="wind_margin")], "华泰《散户-基金》融资资金 W/X/AA", "%"),
            time_chart("margin-collateral-share", "融资担保物结构", "现金与证券市值占两者合计比例", "weekly", "%", [TraceInput("现金占比", cash_share, color=PALETTE[0], source_id="wind_margin"), TraceInput("证券占比", sec_share, color=PALETTE[2], source_id="wind_margin")], "华泰《散户-基金》融资资金 W/Y/Z；看板派生"),
            category_chart("margin-industry", "行业融资净买入", "最近完整周与行业均值", margin_cat, [{"name": "行业净买入", "values": margin_val, "source_id": "wind_margin", "color_by_sign": True}, {"name": "行业均值", "values": [average_margin] * len(margin_cat), "source_id": "wind_margin", "type": "line", "color": PALETTE[3]}], "亿元", "华泰《散户-基金》融资资金 AE/AF"),
        ]

        primary = zero_fill_event_maps(main, "一级市场", "G", {"ipo_count": "H", "ipo_amt": "I", "seo_count": "J", "seo_amt": "K", "cb_count": "L", "cb_amt": "M"}, 3, 858)
        primary_total = {when: primary["ipo_amt"][when] + primary["seo_amt"][when] + primary["cb_amt"][when] for when in set(primary["ipo_amt"]) & set(primary["seo_amt"]) & set(primary["cb_amt"])}
        primary_charts = [
            time_chart("primary-ipo", "IPO融资", "募集资金与新增IPO数量双轴", "weekly", "亿元", [TraceInput("IPO募集资金", primary["ipo_amt"], "area", color=PALETTE[0], source_id="wind_primary"), TraceInput("IPO数量", primary["ipo_count"], axis="right", color=PALETTE[2], source_id="wind_primary")], "华泰资金面数据库 一级市场 G/H/I", "个"),
            time_chart("primary-seo", "定增融资", "募集资金与实施项目数量双轴", "weekly", "亿元", [TraceInput("定增募集资金", primary["seo_amt"], "area", color=PALETTE[0], source_id="wind_primary"), TraceInput("定增数量", primary["seo_count"], axis="right", color=PALETTE[2], source_id="wind_primary")], "华泰资金面数据库 一级市场 G/J/K", "个"),
            time_chart("primary-cb", "可转债融资", "募集资金与发行数量双轴", "weekly", "亿元", [TraceInput("可转债募集资金", primary["cb_amt"], "area", color=PALETTE[0], source_id="wind_primary"), TraceInput("可转债数量", primary["cb_count"], axis="right", color=PALETTE[2], source_id="wind_primary")], "华泰资金面数据库 一级市场 G/L/M", "个"),
            time_chart("primary-structure", "一级市场融资结构", "IPO、定增与可转债周度募集资金", "weekly", "亿元", [TraceInput("IPO", primary["ipo_amt"], "bar", color=PALETTE[0], source_id="wind_primary"), TraceInput("定增", primary["seo_amt"], "bar", color=PALETTE[2], source_id="wind_primary"), TraceInput("可转债", primary["cb_amt"], "bar", color=PALETTE[3], source_id="wind_primary")], "华泰资金面数据库 一级市场 G/I/K/M", note="总融资为三项之和；图中保留三项以便审计。"),
        ]

        private_month = extract_maps(main, "私募基金", "G", {"position": "H", "aum": "J"}, 3, 414)
        enhanced_nav: dict[str, dict[date, float]] = {}
        enhanced_drawdown: dict[str, dict[date, float]] = {}
        for key, sheet in (("300", "300增强-制图"), ("500", "500增强-制图"), ("1000", "1000增强-制图")):
            values = extract_maps(private, sheet, "A", {"nav": "C", "drawdown": "D"}, 2, 1000, {"drawdown": 100})
            enhanced_nav[key] = values["nav"]
            enhanced_drawdown[key] = values["drawdown"]
        alt_nav: dict[str, dict[date, float]] = {}
        for key, sheet in (("neutral", "中性-制图"), ("cta", "CTA-制图"), ("arb", "套利-制图")):
            alt_nav[key] = extract_maps(private, sheet, "A", {"nav": "C"}, 2, 1000)["nav"]
        private_charts = [
            time_chart("private-position-aum", "股票多头仓位与私募规模", "月度股票多头平均仓位与存续规模双轴", "monthly", "%", [TraceInput("股票多头平均仓位", private_month["position"], color=PALETTE[0], source_id="wind_private"), TraceInput("私募产品规模", private_month["aum"], axis="right", color=PALETTE[2], source_id="wind_private")], "华泰资金面数据库 私募基金 G/H/J", "亿元"),
            time_chart("private-drawdown", "私募指增策略最大回撤", "华泰托管等权样本：300/500/1000增强", "daily", "%", [TraceInput("300增强", enhanced_drawdown["300"], color=PALETTE[0], source_id="wind_private"), TraceInput("500增强", enhanced_drawdown["500"], color=PALETTE[2], source_id="wind_private"), TraceInput("1000增强", enhanced_drawdown["1000"], color=PALETTE[3], source_id="wind_private")], "华泰金工量化私募跟踪 300/500/1000增强-制图 A/D"),
            time_chart("private-enhanced", "私募指增策略指数", "华泰托管等权样本：300/500/1000增强", "daily", "净值", [TraceInput("300增强", enhanced_nav["300"], color=PALETTE[0], source_id="wind_private"), TraceInput("500增强", enhanced_nav["500"], color=PALETTE[2], source_id="wind_private"), TraceInput("1000增强", enhanced_nav["1000"], color=PALETTE[3], source_id="wind_private")], "华泰金工量化私募跟踪 300/500/1000增强-制图 A/C"),
            time_chart("private-alternative", "私募另类策略指数", "华泰托管等权样本：市场中性/CTA/套利", "daily", "净值", [TraceInput("市场中性", alt_nav["neutral"], color=PALETTE[0], source_id="wind_private"), TraceInput("CTA", alt_nav["cta"], color=PALETTE[2], source_id="wind_private"), TraceInput("套利", alt_nav["arb"], color=PALETTE[3], source_id="wind_private")], "华泰金工量化私募跟踪 中性/CTA/套利-制图 A/C"),
        ]

        foreign_flow = extract_maps(main, "外资-总体-A股", "G", {"total": "H", "active": "I", "passive": "J", "a": "K", "h": "L"}, 3, 7405)
        northbound = extract_maps(main, "外资-总体-A股", "Q", {"turnover": "R", "sse": "S"}, 4, 501)
        matrix = main.read_range("外资仓位", "B1:EJ7")
        position_dates = [parse_date(value) for value in matrix[0]]
        position_rows = {"asia": matrix[1], "global": matrix[3], "em": matrix[4]}
        position_maps = {key: {when: value for when, raw in zip(position_dates, row) if when and (value := finite(raw)) is not None} for key, row in position_rows.items()}
        foreign_charts = [
            time_chart("foreign-flow", "外资配置A股流量", "配置型外资总量、主动与被动分解", "weekly", "亿元", [TraceInput("配置型外资", foreign_flow["total"], color=PALETTE[0], source_id="wind_foreign"), TraceInput("主动配置", foreign_flow["active"], "bar", color=PALETTE[2], source_id="wind_foreign"), TraceInput("被动配置", foreign_flow["passive"], "bar", color=PALETTE[3], source_id="wind_foreign")], "华泰资金面数据库 外资-总体-A股 G/H/I/J"),
            time_chart("foreign-ah", "外资累计配置A股与H股", "EPFR周度累计配置，统一百万美元", "weekly", "百万美元", [TraceInput("A股", foreign_flow["a"], color=PALETTE[0], source_id="wind_foreign"), TraceInput("H股", foreign_flow["h"], color=PALETTE[2], source_id="wind_foreign")], "华泰资金面数据库 外资-总体-A股 G/K/L"),
            time_chart("foreign-turnover", "陆股通成交与上证指数", "陆股通日均成交额与上证综指双轴", "weekly", "亿元", [TraceInput("陆股通日均成交", northbound["turnover"], color=PALETTE[0], source_id="wind_foreign"), TraceInput("上证综指", northbound["sse"], axis="right", color=PALETTE[2], source_id="wind_foreign")], "华泰资金面数据库 外资-总体-A股 Q/R/S", "点", note="2024年后北向净买入披露变化，使用可持续披露的成交额而非伪造净流入。"),
            time_chart("foreign-position", "全球基金A股配置仓位", "亚洲除日本、全球新兴市场与全球被动资金", "monthly", "配置比例", [TraceInput("亚洲除日本主动", position_maps["asia"], color=PALETTE[0], source_id="wind_foreign"), TraceInput("全球新兴市场主动", position_maps["em"], color=PALETTE[2], source_id="wind_foreign"), TraceInput("全球被动", position_maps["global"], axis="right", color=PALETTE[3], source_id="wind_foreign")], "华泰资金面数据库 外资仓位 A:EJ", "配置比例"),
        ]

    cache_root = Path(args.cache_dir)
    fnyr = ofr_dataset("fnyr", cache_root)
    repo = ofr_dataset("repo", cache_root)
    mmf = ofr_dataset("mmf", cache_root)
    sofr = ofr_series(fnyr, "FNYR-SOFR-A")
    effr = ofr_series(fnyr, "FNYR-EFFR-A")
    obfr = ofr_series(fnyr, "FNYR-OBFR-A")
    dvp_transaction = ofr_series(repo, "REPO-DVP_TV_TOT-F", 1_000_000_000)
    dvp_outstanding = ofr_series(repo, "REPO-DVP_OV_TOT-F", 1_000_000_000)
    triparty_transaction = ofr_series(repo, "REPO-TRIV1_TV_TOT-F", 1_000_000_000)
    mmf_total = ofr_series(mmf, "MMF-MMF_TOT-M", 1_000_000_000)
    mmf_treasury = ofr_series(mmf, "MMF-MMF_T_TOT-M", 1_000_000_000)
    mmf_repo = ofr_series(mmf, "MMF-MMF_RP_TOT-M", 1_000_000_000)
    cftc = cftc_sp500(Path(args.cache_dir))
    global_charts = [
        time_chart("global-rates", "美元隔夜融资利率", "担保、联邦基金与银行隔夜融资利率共同有效日", "daily", "%", [TraceInput("SOFR", sofr, color=PALETTE[0], source_id="ofr_rates"), TraceInput("EFFR", effr, color=PALETTE[2], source_id="ofr_rates"), TraceInput("OBFR", obfr, color=PALETTE[3], source_id="ofr_rates")], "OFR STFM fnyr: FNYR-SOFR/EFFR/OBFR-A"),
        time_chart("global-repo", "美国回购市场规模", "DVP成交、DVP未到期与三方回购成交共同有效日", "daily", "十亿美元", [TraceInput("DVP成交额", dvp_transaction, color=PALETTE[0], source_id="ofr_repo"), TraceInput("DVP未到期余额", dvp_outstanding, color=PALETTE[2], source_id="ofr_repo"), TraceInput("三方回购成交额", triparty_transaction, color=PALETTE[3], source_id="ofr_repo")], "OFR STFM repo: Final vintage"),
        time_chart("global-mmf", "美国货币市场基金资产", "总资产、美国国债与回购协议月末余额", "monthly", "十亿美元", [TraceInput("总资产", mmf_total, color=PALETTE[0], source_id="ofr_mmf"), TraceInput("美国国债", mmf_treasury, color=PALETTE[2], source_id="ofr_mmf"), TraceInput("回购协议", mmf_repo, color=PALETTE[3], source_id="ofr_mmf")], "OFR STFM mmf: monthly complete-series vintage"),
        time_chart("global-position", "标普500期货机构净仓位", "净头寸占总持仓：资产管理人、杠杆基金、交易商", "weekly", "% OI", [TraceInput("资产管理人", cftc["asset"], color=PALETTE[0], source_id="cftc_tff"), TraceInput("杠杆基金", cftc["leveraged"], color=PALETTE[2], source_id="cftc_tff"), TraceInput("交易商", cftc["dealer"], color=PALETTE[3], source_id="cftc_tff")], "CFTC TFF E-MINI S&P 500"),
    ]

    domestic_home = []
    for source, new_id, new_title in ((retail_charts[0], "home-retail", "散户资金动量"), (public_charts[0], "home-public", "公募新发热度"), (etf_charts[1], "home-etf", "ETF资金需求"), (margin_charts[0], "home-margin", "融资资金动量")):
        chart = copy.deepcopy(source)
        chart["id"] = new_id
        chart["title"] = new_title
        domestic_home.append(chart)

    pages = {
        "home": page("资金面跟踪主页", "A股七类资金与全球流动性统一监测", "所有图表均使用共同有效日期交集；长期序列半年刻度，短期序列月度刻度；双轴只用于单位或量级不兼容的指标。", domestic_home + global_charts),
        "retail": page("散户资金", "小单流、开户与投资者参与度", "散户页覆盖交易行为、累计流量、开户和参与度四个维度。", retail_charts),
        "public": page("公募基金", "新发、报会、仓位与清算", "公募页区分增量份额、产品供给、存量仓位和退出压力。", public_charts),
        "etf": page("ETF资金", "份额申赎、资金流与结构分解", "ETF页同时展示交易所净份额、资金流和板块/行业结构。", etf_charts),
        "margin": page("融资资金", "净买入、余额、活跃度与担保结构", "融资页使用五张图，余额与活跃度、担保比例和行业结构分别核查。", margin_charts),
        "primary": page("一级市场", "IPO、定增与可转债融资供给", "一级市场按实施周统计金额与项目数，所有金额统一为亿元。", primary_charts),
        "private": page("私募基金", "仓位、净流入与策略指数", "私募页将全市场规模/仓位与托管样本策略净值分开，避免样本口径混淆。", private_charts),
        "foreign": page("外资资金", "配置流、A/H分配、陆股通成交与仓位", "外资页不把披露中断的北向净买入伪装成连续序列，改用持续披露的成交额并明确EPFR口径。", foreign_charts),
    }

    checks: list[dict[str, Any]] = []
    chart_count = 0
    for page_id, payload in pages.items():
        charts = payload["charts"]
        chart_count += len(charts)
        if page_id != "home":
            checks.append({"check": f"{page_id}_chart_count", "status": "passed" if 4 <= len(charts) <= 8 else "failed", "actual": len(charts), "expected": "4-8"})
        for chart in charts:
            sources = {trace["source_id"] for trace in chart["traces"]}
            checks.append({"check": f"{chart['id']}_source_registry", "status": "passed" if sources and sources <= set(SOURCE_REGISTRY) else "failed", "actual": sorted(sources)})
            if chart["kind"] == "time":
                x_sets = {tuple(trace["x"]) for trace in chart["traces"]}
                ticks_ok = chart["x_tick"]["dtick"] in {"M1", "M6"}
                checks.append({"check": f"{chart['id']}_date_alignment", "status": "passed" if len(x_sets) == 1 and ticks_ok else "failed", "observations": chart["quality"]["common_observations"], "tick": chart["x_tick"]["dtick"]})
    used_sources = {trace["source_id"] for payload in pages.values() for chart in payload["charts"] for trace in chart["traces"]}
    published_sources = {key: value for key, value in SOURCE_REGISTRY.items() if key in used_sources}
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    latest = max(payload["as_of"] for payload in pages.values())
    snapshot = {
        "status": "ready" if status == "passed" else "failed",
        "schema_version": "liquidity-dashboard-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_as_of": {"latest": latest, "huatai_reference": "2026-06-29", "global_public": pages["home"]["as_of"]},
        "style_contract": {"palette": PALETTE, "font_latin": "Arial", "font_cjk": "KaiTi", "font_size_px": 12, "line_width_px": 1.75, "legend": "bottom", "background": "#ffffff", "gridlines": False, "axis_labels": 12},
        "source_registry": published_sources,
        "pages": pages,
        "quality": {"status": status, "chart_count": chart_count, "checks_passed": sum(check["status"] == "passed" for check in checks), "checks_total": len(checks), "checks": checks},
        "limitations": ["华泰工作簿仅作为历史种子与图形口径参考；生产更新按 source_registry 的 Wind/iFinD/OFR/CFTC 映射执行。", "免费数据源不替代付费源的资金金额口径，仅用于可用性或方向核验。", "涉及EPFR、托管私募样本的序列受授权范围约束，公网仅发布聚合结果。"],
    }
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the validated liquidity dashboard snapshot.")
    parser.add_argument("--huatai-main", required=True, type=Path)
    parser.add_argument("--huatai-retail-fund", required=True, type=Path)
    parser.add_argument("--private-custody", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshot = build(args)
    if snapshot["quality"]["status"] != "passed":
        raise SystemExit("liquidity snapshot quality gate failed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    candidate = args.output.with_suffix(args.output.suffix + ".candidate")
    candidate.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    candidate.replace(args.output)
    print(json.dumps({"status": snapshot["status"], "charts": snapshot["quality"]["chart_count"], "checks": snapshot["quality"]["checks_total"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
