"""Build the seven domestic liquidity pages exclusively from the source cache."""

from __future__ import annotations

import calendar
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from data_sources import CONTRACT_BY_ID, DataQualityError, LiquidityCache


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


class CacheReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        audit = LiquidityCache(path).audit()
        if audit["status"] != "passed":
            missing = ", ".join(audit["missing_required_series"])
            raise DataQualityError(
                "liquidity cache is incomplete; release blocked. Missing: " + missing
            )

    def series(self, series_id: str) -> dict[date, float]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT observation_date,value
                FROM observations
                WHERE series_id=?
                ORDER BY observation_date
                """,
                (series_id,),
            ).fetchall()
        output = {_parse_date(when): float(value) for when, value in rows}
        if len(output) < 2:
            raise DataQualityError(f"{series_id}: fewer than two cached observations")
        return output

    def categories(self, series_id: str) -> tuple[list[str], list[float], date]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT category,value,as_of_date
                FROM category_observations
                WHERE series_id=?
                ORDER BY category
                """,
                (series_id,),
            ).fetchall()
        if not rows:
            raise DataQualityError(f"{series_id}: no cached categories")
        dates = {_parse_date(row[2]) for row in rows}
        if len(dates) != 1:
            raise DataQualityError(f"{series_id}: category as-of dates do not match")
        return (
            [str(row[0]) for row in rows],
            [float(row[1]) for row in rows],
            dates.pop(),
        )


def _week_end(when: date) -> date:
    return min(when + timedelta(days=4 - when.weekday()), date.today())


def _month_end(when: date) -> date:
    period_end = date(when.year, when.month, calendar.monthrange(when.year, when.month)[1])
    return min(period_end, date.today())


def _resample(
    source: Mapping[date, float],
    frequency: str,
    method: str,
    *,
    fill_empty: bool = False,
) -> dict[date, float]:
    groups: dict[date, list[tuple[date, float]]] = defaultdict(list)
    for when, value in sorted(source.items()):
        key = _week_end(when) if frequency == "weekly" else _month_end(when)
        groups[key].append((when, float(value)))
    result: dict[date, float] = {}
    for key, values in groups.items():
        if method == "sum":
            result[key] = sum(value for _, value in values)
        elif method == "mean":
            result[key] = statistics.fmean(value for _, value in values)
        elif method == "last":
            result[key] = max(values, key=lambda item: item[0])[1]
        else:
            raise ValueError(method)
    if fill_empty and result:
        cursor, end = min(result), max(result)
        while cursor <= end:
            result.setdefault(cursor, 0.0)
            if frequency == "weekly":
                cursor += timedelta(days=7)
            else:
                year = cursor.year + (1 if cursor.month == 12 else 0)
                month = 1 if cursor.month == 12 else cursor.month + 1
                cursor = date(year, month, calendar.monthrange(year, month)[1])
    return dict(sorted(result.items()))


def _rolling_mean(
    source: Mapping[date, float], window: int, *, centered: bool = False
) -> dict[date, float]:
    items = sorted(source.items())
    output: dict[date, float] = {}
    if centered:
        half = window // 2
        for index in range(half, len(items) - half):
            sample = items[index - half : index + half + 1]
            output[items[index][0]] = statistics.fmean(value for _, value in sample)
        return output
    for index in range(window - 1, len(items)):
        sample = items[index - window + 1 : index + 1]
        output[items[index][0]] = statistics.fmean(value for _, value in sample)
    return output


def _year_over_year(source: Mapping[date, float]) -> dict[date, float]:
    by_month = {(when.year, when.month): value for when, value in source.items()}
    output: dict[date, float] = {}
    for when, value in source.items():
        prior = by_month.get((when.year - 1, when.month))
        if prior not in (None, 0):
            output[when] = (value / prior - 1) * 100
    return output


def _cumulative(source: Mapping[date, float], start: date) -> dict[date, float]:
    total = 0.0
    output: dict[date, float] = {}
    for when, value in sorted(source.items()):
        if when < start:
            continue
        total += value
        output[when] = total
    return output


def _detrended_24(source: Mapping[date, float]) -> dict[date, float]:
    items = sorted(source.items())
    output: dict[date, float] = {}
    for index in range(23, len(items)):
        mean = statistics.fmean(value for _, value in items[index - 23 : index + 1])
        output[items[index][0]] = items[index][1] - mean
    return output


def _hma_reference(source: Mapping[date, float]) -> dict[date, float]:
    """Replicate the reference workbook's 3/6-period HMA formula exactly."""

    items = sorted(source.items())
    inner: dict[int, float] = {}
    for index in range(5, len(items)):
        mean3 = statistics.fmean(value for _, value in items[index - 2 : index + 1])
        mean6 = statistics.fmean(value for _, value in items[index - 5 : index + 1])
        inner[index] = 2 * mean3 - mean6
    output: dict[date, float] = {}
    for index in range(6, len(items)):
        output[items[index][0]] = (2 * inner[index] + inner[index - 1]) / 3
    return output


def _drawdown(source: Mapping[date, float]) -> dict[date, float]:
    running_peak = float("-inf")
    output: dict[date, float] = {}
    for when, value in sorted(source.items()):
        running_peak = max(running_peak, value)
        if running_peak > 0:
            output[when] = (value / running_peak - 1) * 100
    return output


def _ratio(
    numerator: Mapping[date, float],
    denominator: Mapping[date, float],
    scale: float = 1.0,
) -> dict[date, float]:
    output: dict[date, float] = {}
    for when in set(numerator) & set(denominator):
        if denominator[when] != 0:
            output[when] = numerator[when] / denominator[when] * scale
    return output


def _source_registry() -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for series_id, item in CONTRACT_BY_ID.items():
        output[series_id] = {
            "label": item.preferred_provider,
            "primary": item.locator,
            "fields": list(item.fields),
            "frequency": item.native_frequency,
            "fallback": "；".join(item.exact_fallbacks) or "无严格等价后备源",
            "quality": item.transform,
            "release_rule": item.release_rule,
            "availability": item.availability,
        }
    return output


def _reference(*series_ids: str) -> str:
    parts: list[str] = []
    for series_id in series_ids:
        item = CONTRACT_BY_ID[series_id]
        parts.append(f"{item.preferred_provider} {item.locator}")
    return "；".join(dict.fromkeys(parts))


def build_domestic(
    cache_path: Path,
    time_chart: Callable[..., dict[str, Any]],
    category_chart: Callable[..., dict[str, Any]],
    trace_input: type,
    palette: list[str],
) -> dict[str, Any]:
    store = CacheReader(cache_path)

    retail_net = store.series("retail.small_net")
    retail_ma7 = _rolling_mean(retail_net, 7, centered=True)
    accounts = store.series("retail.new_accounts")
    account_ma3 = _rolling_mean(accounts, 3)
    account_yoy = _year_over_year(accounts)
    investors = store.series("retail.participating_investors")
    retail_charts = [
        time_chart(
            "retail-flow",
            "散户小单净流入与7日均线",
            "红涨绿跌；仅保留净额与均线共同有效交易日",
            "daily",
            "亿元",
            [
                trace_input("散户净买入", retail_net, "bar", color=palette[0], source_id="retail.small_net", color_by_sign=True),
                trace_input("7日均线", retail_ma7, color=palette[2], source_id="retail.small_net"),
            ],
            _reference("retail.small_net"),
        ),
        time_chart(
            "retail-cumulative",
            "散户累计净买入",
            "两套起始日口径严格取共同日期，不拼接错位",
            "daily",
            "亿元",
            [
                trace_input("自2023-11-01", _cumulative(retail_net, date(2023, 11, 1)), color=palette[0], source_id="retail.small_net"),
                trace_input("自2024-03-01", _cumulative(retail_net, date(2024, 3, 1)), color=palette[2], source_id="retail.small_net"),
            ],
            _reference("retail.small_net"),
        ),
        time_chart(
            "retail-accounts",
            "A股新增开户与趋势",
            "月度新增开户、3月均线与同比；发布月末对齐",
            "monthly",
            "万户",
            [
                trace_input("新增开户", accounts, "bar", color=palette[0], source_id="retail.new_accounts"),
                trace_input("3月均线", account_ma3, color=palette[2], source_id="retail.new_accounts"),
                trace_input("同比", account_yoy, axis="right", color=palette[3], source_id="retail.new_accounts"),
            ],
            _reference("retail.new_accounts"),
            "%",
        ),
        time_chart(
            "retail-participation",
            "参与交易投资者与散户资金",
            "投资者数量与小单7日均线取共同发布日",
            "daily",
            "万人",
            [
                trace_input("参与交易投资者", investors, color=palette[0], source_id="retail.participating_investors"),
                trace_input("小单7日均线", retail_ma7, axis="right", color=palette[2], source_id="retail.small_net"),
            ],
            _reference("retail.participating_investors", "retail.small_net"),
            "亿元",
        ),
    ]

    public_new = _resample(store.series("public.new_equity_shares"), "weekly", "sum", fill_empty=True)
    public_detrended = _detrended_24(public_new)
    public_hma = _hma_reference(public_new)
    filing_stock = _resample(store.series("public.filings_stock"), "weekly", "sum", fill_empty=True)
    filing_mixed = _resample(store.series("public.filings_mixed"), "weekly", "sum", fill_empty=True)
    filing_total = {
        when: filing_stock[when] + filing_mixed[when]
        for when in set(filing_stock) & set(filing_mixed)
    }
    position_stock = store.series("public.position_stock")
    position_mixed = store.series("public.position_mixed")
    liquidation_count = _resample(store.series("public.liquidation_count"), "monthly", "sum", fill_empty=True)
    liquidation_scale = _resample(store.series("public.liquidation_scale"), "monthly", "sum", fill_empty=True)
    public_charts = [
        time_chart(
            "public-new",
            "新成立偏股基金份额",
            "原值、24周去趋势值与参考公式4周HMA共同交集",
            "weekly",
            "亿份",
            [
                trace_input("新成立份额", public_new, "bar", color=palette[0], source_id="public.new_equity_shares"),
                trace_input("去趋势", public_detrended, color=palette[2], source_id="public.new_equity_shares"),
                trace_input("4周HMA", public_hma, color=palette[3], source_id="public.new_equity_shares"),
            ],
            _reference("public.new_equity_shares"),
        ),
        time_chart(
            "public-filings",
            "新增产品报会",
            "股票型、混合型与两者合计",
            "weekly",
            "只",
            [
                trace_input("股票型", filing_stock, "bar", color=palette[0], source_id="public.filings_stock"),
                trace_input("混合型", filing_mixed, "bar", color=palette[2], source_id="public.filings_mixed"),
                trace_input("合计", filing_total, color=palette[3], source_id="public.filings_stock"),
            ],
            _reference("public.filings_stock", "public.filings_mixed"),
        ),
        time_chart(
            "public-position",
            "主动偏股基金股票仓位",
            "普通股票型与偏股混合型，统一百分比",
            "daily",
            "%",
            [
                trace_input("普通股票型", position_stock, color=palette[0], source_id="public.position_stock"),
                trace_input("偏股混合型", position_mixed, color=palette[2], source_id="public.position_mixed"),
            ],
            _reference("public.position_stock", "public.position_mixed"),
        ),
        time_chart(
            "public-liquidation",
            "基金清算数量与规模",
            "月度事件数与清算规模双轴",
            "monthly",
            "只",
            [
                trace_input("清算数量", liquidation_count, "bar", color=palette[0], source_id="public.liquidation_count"),
                trace_input("清算规模", liquidation_scale, axis="right", color=palette[2], source_id="public.liquidation_scale"),
            ],
            _reference("public.liquidation_count", "public.liquidation_scale"),
            "亿元",
        ),
    ]

    etf_all = _resample(store.series("etf.net_share_all"), "weekly", "sum")
    etf_sse = _resample(store.series("etf.net_share_sse"), "weekly", "sum")
    etf_szse = _resample(store.series("etf.net_share_szse"), "weekly", "sum")
    etf_total = _resample(store.series("etf.flow_total"), "weekly", "sum")
    etf_broad = _resample(store.series("etf.flow_broad"), "weekly", "sum")
    etf_other = _resample(store.series("etf.flow_other"), "weekly", "sum")
    sector_cat, sector_val, sector_as_of = store.categories("etf.flow_sector")
    industry_cat, industry_val, industry_as_of = store.categories("etf.flow_industry")
    etf_charts = [
        time_chart(
            "etf-share",
            "ETF净申购赎回份额",
            "全市场、上交所与深交所周度净份额",
            "weekly",
            "亿份",
            [
                trace_input("全市场", etf_all, color=palette[0], source_id="etf.net_share_all"),
                trace_input("上交所", etf_sse, "bar", color=palette[2], source_id="etf.net_share_sse"),
                trace_input("深交所", etf_szse, "bar", color=palette[3], source_id="etf.net_share_szse"),
            ],
            _reference("etf.net_share_all", "etf.net_share_sse", "etf.net_share_szse"),
        ),
        time_chart(
            "etf-flow",
            "ETF资金净流入",
            "总资金流、宽基ETF与其他ETF",
            "weekly",
            "亿元",
            [
                trace_input("总资金流", etf_total, color=palette[0], source_id="etf.flow_total"),
                trace_input("宽基ETF", etf_broad, "bar", color=palette[2], source_id="etf.flow_broad"),
                trace_input("其他ETF", etf_other, "bar", color=palette[3], source_id="etf.flow_other"),
            ],
            _reference("etf.flow_total", "etf.flow_broad", "etf.flow_other"),
        ),
        category_chart(
            "etf-sector",
            "ETF大类板块流入",
            f"最近完整周（截至{sector_as_of.isoformat()}）；红色为净流入、绿色为净流出",
            sector_cat,
            [{"name": "最近一周", "values": sector_val, "source_id": "etf.flow_sector", "color_by_sign": True}],
            "亿元",
            _reference("etf.flow_sector"),
        ),
        category_chart(
            "etf-industry",
            "ETF行业流入",
            f"申万一级行业口径（截至{industry_as_of.isoformat()}）",
            industry_cat,
            [{"name": "最近一周", "values": industry_val, "source_id": "etf.flow_industry", "color_by_sign": True}],
            "亿元",
            _reference("etf.flow_industry"),
        ),
    ]

    margin_net = _resample(store.series("margin.net_buy"), "weekly", "sum")
    margin_ma = _rolling_mean(margin_net, 3, centered=True)
    margin_balance = _resample(store.series("margin.balance"), "weekly", "last")
    margin_activity = _resample(store.series("margin.activity"), "weekly", "mean")
    guarantee = _resample(store.series("margin.guarantee_ratio"), "monthly", "last")
    cash = _resample(store.series("margin.collateral_cash"), "monthly", "last")
    securities = _resample(store.series("margin.collateral_securities"), "monthly", "last")
    sec_cash = _ratio(securities, cash)
    cash_share = {
        when: cash[when] / (cash[when] + securities[when]) * 100
        for when in set(cash) & set(securities)
        if cash[when] + securities[when] > 0
    }
    sec_share = {when: 100 - value for when, value in cash_share.items()}
    margin_cat, margin_val, margin_as_of = store.categories("margin.industry_net_buy")
    average_margin = statistics.fmean(margin_val)
    margin_charts = [
        time_chart(
            "margin-flow",
            "融资净买入与7日均线",
            "周度净买入与参考工作簿三期居中趋势",
            "weekly",
            "亿元",
            [
                trace_input("融资净买入", margin_net, "bar", color=palette[0], source_id="margin.net_buy", color_by_sign=True),
                trace_input("7日均线", margin_ma, color=palette[2], source_id="margin.net_buy"),
            ],
            _reference("margin.net_buy"),
        ),
        time_chart(
            "margin-balance",
            "融资余额与交易活跃度",
            "余额和融资买入占成交额双轴",
            "weekly",
            "亿元",
            [
                trace_input("融资余额", margin_balance, color=palette[0], source_id="margin.balance"),
                trace_input("融资活跃度", margin_activity, axis="right", color=palette[2], source_id="margin.activity"),
            ],
            _reference("margin.balance", "margin.activity"),
            "%",
        ),
        time_chart(
            "margin-collateral",
            "平均担保比例与证券/现金比",
            "同一月末口径双轴",
            "monthly",
            "平均担保比例（%）",
            [
                trace_input("平均担保比例", guarantee, color=palette[0], source_id="margin.guarantee_ratio"),
                trace_input("证券市值/现金", sec_cash, axis="right", color=palette[2], source_id="margin.collateral_securities"),
            ],
            _reference("margin.guarantee_ratio", "margin.collateral_cash", "margin.collateral_securities"),
            "倍",
        ),
        time_chart(
            "margin-collateral-share",
            "融资担保物结构",
            "现金与证券市值占两者合计比例",
            "monthly",
            "%",
            [
                trace_input("现金占比", cash_share, color=palette[0], source_id="margin.collateral_cash"),
                trace_input("证券占比", sec_share, color=palette[2], source_id="margin.collateral_securities"),
            ],
            _reference("margin.collateral_cash", "margin.collateral_securities"),
        ),
        category_chart(
            "margin-industry",
            "行业融资净买入",
            f"最近完整周（截至{margin_as_of.isoformat()}）与行业均值",
            margin_cat,
            [
                {"name": "行业净买入", "values": margin_val, "source_id": "margin.industry_net_buy", "color_by_sign": True},
                {"name": "行业均值", "values": [average_margin] * len(margin_cat), "source_id": "margin.industry_net_buy", "type": "line", "color": palette[3]},
            ],
            "亿元",
            _reference("margin.industry_net_buy"),
        ),
    ]

    ipo_amount = _resample(store.series("primary.ipo_amount"), "weekly", "sum", fill_empty=True)
    ipo_count = _resample(store.series("primary.ipo_count"), "weekly", "sum", fill_empty=True)
    seo_amount = _resample(store.series("primary.seo_amount"), "weekly", "sum", fill_empty=True)
    seo_count = _resample(store.series("primary.seo_count"), "weekly", "sum", fill_empty=True)
    cb_amount = _resample(store.series("primary.cb_amount"), "weekly", "sum", fill_empty=True)
    cb_count = _resample(store.series("primary.cb_count"), "weekly", "sum", fill_empty=True)
    primary_charts = [
        time_chart(
            "primary-ipo",
            "IPO融资",
            "募集资金与新增IPO数量双轴",
            "weekly",
            "亿元",
            [
                trace_input("IPO募集资金", ipo_amount, "area", color=palette[0], source_id="primary.ipo_amount"),
                trace_input("IPO数量", ipo_count, axis="right", color=palette[2], source_id="primary.ipo_count"),
            ],
            _reference("primary.ipo_amount", "primary.ipo_count"),
            "只",
        ),
        time_chart(
            "primary-seo",
            "定增融资",
            "现金募集资金与实施项目数量双轴",
            "weekly",
            "亿元",
            [
                trace_input("定增募集资金", seo_amount, "area", color=palette[0], source_id="primary.seo_amount"),
                trace_input("定增数量", seo_count, axis="right", color=palette[2], source_id="primary.seo_count"),
            ],
            _reference("primary.seo_amount", "primary.seo_count"),
            "只",
        ),
        time_chart(
            "primary-cb",
            "可转债融资",
            "募集资金与发行数量双轴",
            "weekly",
            "亿元",
            [
                trace_input("可转债募集资金", cb_amount, "area", color=palette[0], source_id="primary.cb_amount"),
                trace_input("可转债数量", cb_count, axis="right", color=palette[2], source_id="primary.cb_count"),
            ],
            _reference("primary.cb_amount", "primary.cb_count"),
            "只",
        ),
        time_chart(
            "primary-structure",
            "一级市场融资结构",
            "IPO、定增与可转债周度募集资金",
            "weekly",
            "亿元",
            [
                trace_input("IPO", ipo_amount, "bar", color=palette[0], source_id="primary.ipo_amount"),
                trace_input("定增", seo_amount, "bar", color=palette[2], source_id="primary.seo_amount"),
                trace_input("可转债", cb_amount, "bar", color=palette[3], source_id="primary.cb_amount"),
            ],
            _reference("primary.ipo_amount", "primary.seo_amount", "primary.cb_amount"),
            note="总融资为三项之和；图中保留三项以便审计。",
        ),
    ]

    private_position = _resample(store.series("private.stock_long_position"), "monthly", "last")
    private_aum = store.series("private.aum")
    p300 = store.series("private.enhanced_300")
    p500 = store.series("private.enhanced_500")
    p1000 = store.series("private.enhanced_1000")
    neutral = store.series("private.neutral")
    cta = store.series("private.cta")
    arbitrage = store.series("private.arbitrage")
    private_charts = [
        time_chart(
            "private-position-aum",
            "股票多头仓位与私募规模",
            "月度股票多头平均仓位与存续规模双轴",
            "monthly",
            "%",
            [
                trace_input("股票多头平均仓位", private_position, color=palette[0], source_id="private.stock_long_position"),
                trace_input("私募产品规模", private_aum, axis="right", color=palette[2], source_id="private.aum"),
            ],
            _reference("private.stock_long_position", "private.aum"),
            "亿元",
        ),
        time_chart(
            "private-drawdown",
            "私募指增策略最大回撤",
            "Wind私募净值等权样本：300/500/1000增强",
            "monthly",
            "%",
            [
                trace_input("300增强", _drawdown(p300), color=palette[0], source_id="private.enhanced_300"),
                trace_input("500增强", _drawdown(p500), color=palette[2], source_id="private.enhanced_500"),
                trace_input("1000增强", _drawdown(p1000), color=palette[3], source_id="private.enhanced_1000"),
            ],
            _reference("private.enhanced_300", "private.enhanced_500", "private.enhanced_1000"),
        ),
        time_chart(
            "private-enhanced",
            "私募指增策略指数",
            "Wind私募净值等权样本：300/500/1000增强",
            "monthly",
            "净值",
            [
                trace_input("300增强", p300, color=palette[0], source_id="private.enhanced_300"),
                trace_input("500增强", p500, color=palette[2], source_id="private.enhanced_500"),
                trace_input("1000增强", p1000, color=palette[3], source_id="private.enhanced_1000"),
            ],
            _reference("private.enhanced_300", "private.enhanced_500", "private.enhanced_1000"),
        ),
        time_chart(
            "private-alternative",
            "私募另类策略指数",
            "Wind私募净值等权样本：市场中性/CTA/套利",
            "monthly",
            "净值",
            [
                trace_input("市场中性", neutral, color=palette[0], source_id="private.neutral"),
                trace_input("CTA", cta, color=palette[2], source_id="private.cta"),
                trace_input("套利", arbitrage, color=palette[3], source_id="private.arbitrage"),
            ],
            _reference("private.neutral", "private.cta", "private.arbitrage"),
        ),
    ]

    foreign_total = store.series("foreign.flow_total")
    foreign_active = store.series("foreign.flow_active")
    foreign_passive = store.series("foreign.flow_passive")
    foreign_a = store.series("foreign.cumulative_a")
    foreign_h = store.series("foreign.cumulative_h")
    northbound = _resample(store.series("foreign.northbound_turnover"), "weekly", "mean")
    sse = _resample(store.series("foreign.sse_index"), "weekly", "last")
    asia = store.series("foreign.position_asia_ex_japan")
    em = store.series("foreign.position_em_active")
    global_passive = store.series("foreign.position_global_passive")
    foreign_charts = [
        time_chart(
            "foreign-flow",
            "外资配置A股流量",
            "配置型外资总量、主动与被动分解",
            "weekly",
            "亿元",
            [
                trace_input("配置型外资", foreign_total, color=palette[0], source_id="foreign.flow_total"),
                trace_input("主动配置", foreign_active, "bar", color=palette[2], source_id="foreign.flow_active"),
                trace_input("被动配置", foreign_passive, "bar", color=palette[3], source_id="foreign.flow_passive"),
            ],
            _reference("foreign.flow_total", "foreign.flow_active", "foreign.flow_passive"),
        ),
        time_chart(
            "foreign-ah",
            "外资累计配置A股与H股",
            "EPFR周度累计配置，统一百万美元",
            "weekly",
            "百万美元",
            [
                trace_input("A股", foreign_a, color=palette[0], source_id="foreign.cumulative_a"),
                trace_input("H股", foreign_h, color=palette[2], source_id="foreign.cumulative_h"),
            ],
            _reference("foreign.cumulative_a", "foreign.cumulative_h"),
        ),
        time_chart(
            "foreign-turnover",
            "陆股通成交与上证指数",
            "陆股通日均成交额与上证综指双轴",
            "weekly",
            "亿元",
            [
                trace_input("陆股通日均成交", northbound, color=palette[0], source_id="foreign.northbound_turnover"),
                trace_input("上证综指", sse, axis="right", color=palette[2], source_id="foreign.sse_index"),
            ],
            _reference("foreign.northbound_turnover", "foreign.sse_index"),
            "点",
            note="2024年后北向净买入披露变化，使用持续披露的成交额而非伪造净流入。",
        ),
        time_chart(
            "foreign-position",
            "全球基金A股配置仓位",
            "亚洲除日本、全球新兴市场与全球被动资金",
            "monthly",
            "配置比例",
            [
                trace_input("亚洲除日本主动", asia, color=palette[0], source_id="foreign.position_asia_ex_japan"),
                trace_input("全球新兴市场主动", em, color=palette[2], source_id="foreign.position_em_active"),
                trace_input("全球被动", global_passive, axis="right", color=palette[3], source_id="foreign.position_global_passive"),
            ],
            _reference("foreign.position_asia_ex_japan", "foreign.position_em_active", "foreign.position_global_passive"),
            "配置比例",
        ),
    ]

    return {
        "retail_charts": retail_charts,
        "public_charts": public_charts,
        "etf_charts": etf_charts,
        "margin_charts": margin_charts,
        "primary_charts": primary_charts,
        "private_charts": private_charts,
        "foreign_charts": foreign_charts,
        "source_registry": _source_registry(),
    }
