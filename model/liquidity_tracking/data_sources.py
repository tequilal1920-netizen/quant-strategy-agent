"""Authoritative data-source layer for the liquidity dashboard.

The module has three deliberately strict rules:

1. No spreadsheet value is an admissible observation source.
2. A fallback may be used only when it is semantically equivalent to the
   requested field.  A proxy never silently replaces a licensed series.
3. Secrets come from environment variables and are never stored in SQLite,
   snapshots, logs, source contracts, or command output.

The dashboard builder reads only the SQLite cache produced here.  Paid-source
series that are unavailable fail the release gate instead of being backfilled
from an old workbook.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 2
DEFAULT_START = date(2010, 1, 1)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SourceConfigurationError(RuntimeError):
    """Raised when a provider is selected without the required runtime config."""


class SourceUnavailableError(RuntimeError):
    """Raised when an exact source cannot be reached or is not licensed."""


class DataQualityError(RuntimeError):
    """Raised when a provider response violates the series contract."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]


@dataclass(frozen=True)
class SeriesContract:
    series_id: str
    page: str
    display_name: str
    unit: str
    native_frequency: str
    preferred_provider: str
    locator: str
    fields: tuple[str, ...]
    release_rule: str
    transform: str
    exact_fallbacks: tuple[str, ...] = ()
    required: bool = True
    availability: str = "ready"
    unavailable_reason: str = ""


def contract(
    series_id: str,
    page: str,
    display_name: str,
    unit: str,
    native_frequency: str,
    preferred_provider: str,
    locator: str,
    fields: Sequence[str],
    release_rule: str,
    transform: str,
    *,
    exact_fallbacks: Sequence[str] = (),
    availability: str = "ready",
    unavailable_reason: str = "",
) -> SeriesContract:
    return SeriesContract(
        series_id=series_id,
        page=page,
        display_name=display_name,
        unit=unit,
        native_frequency=native_frequency,
        preferred_provider=preferred_provider,
        locator=locator,
        fields=tuple(fields),
        release_rule=release_rule,
        transform=transform,
        exact_fallbacks=tuple(exact_fallbacks),
        availability=availability,
        unavailable_reason=unavailable_reason,
    )


# Every externally sourced field behind the existing 37 charts is registered
# here.  Rolling means, cumulative sums, HMA, year-on-year rates and drawdowns
# are deterministic builder-side derivatives and therefore do not need a
# second external source contract.
CONTRACTS: tuple[SeriesContract, ...] = (
    contract(
        "retail.small_net",
        "retail",
        "散户净买入",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.ASHAREMONEYFLOW",
        ("TRADE_DT", "VALUE_DIFF_SMALL_TRADER"),
        "交易日收盘后",
        "全A逐日求和；原字段万元除以10000；不填充非交易日",
        exact_fallbacks=("iFinD THS_DS/THS_DataPool 同口径小单净额",),
    ),
    contract(
        "retail.participating_investors",
        "retail",
        "参与交易投资者",
        "万人",
        "daily",
        "Wind EDB",
        "R7708385",
        ("中国:参与交易的投资者数量",),
        "按原始发布日",
        "原值除以10000；不向交易日扩展",
        exact_fallbacks=("iFinD EDB S004085260",),
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind 终端未登录且 iFinD EDB 月度额度已耗尽",
    ),
    contract(
        "retail.new_accounts",
        "retail",
        "新增开户",
        "万户",
        "monthly",
        "Wind EDB",
        "M0010401",
        ("A股账户新增开户数",),
        "月度发布值",
        "保持原发布月，不以累计账户差分冒充",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind 终端未登录；SQL 表仅有累计账户数，不是严格等价字段",
    ),
    contract(
        "public.new_equity_shares",
        "public",
        "新成立偏股型基金份额",
        "亿份",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDISSUE",
        ("F_INFO_SETUPDATE", "F_INFO_INVESTYPE", "F_ISSUE_SHARES"),
        "基金成立日",
        "股票型与混合型成立份额按周求和",
        exact_fallbacks=("Wind EDB M0060433",),
    ),
    contract(
        "public.filings_stock",
        "public",
        "新增股票型产品报会",
        "只",
        "daily",
        "Wind SQL",
        "wande.dbo.CFUNDADMPERMITSCHEDULE",
        ("OBJECT_ID", "TYPE_CODE", "TYPE_NAME", "APPLY_PICKUP_DATE"),
        "申请材料接收日",
        "TYPE_CODE=267005001；OBJECT_ID去重；仅明确股票型名称",
    ),
    contract(
        "public.filings_mixed",
        "public",
        "新增混合型产品报会",
        "只",
        "daily",
        "Wind SQL",
        "wande.dbo.CFUNDADMPERMITSCHEDULE",
        ("OBJECT_ID", "TYPE_CODE", "TYPE_NAME", "APPLY_PICKUP_DATE"),
        "申请材料接收日",
        "TYPE_CODE=267005001；OBJECT_ID去重；仅明确混合型名称",
    ),
    contract(
        "public.position_stock",
        "public",
        "普通股票型基金仓位",
        "%",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDPOSESTIMATION + CHINAMUTUALFUNDSECTOR",
        ("S_INFO_WINDCODE", "F_EST_DATE", "F_EST_POSITION", "S_INFO_SECTOR"),
        "交易日估算",
        "Wind细分类2001010101000000；逐日等权均值；0-1原值乘100",
    ),
    contract(
        "public.position_mixed",
        "public",
        "偏股混合型基金仓位",
        "%",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDPOSESTIMATION + CHINAMUTUALFUNDSECTOR",
        ("S_INFO_WINDCODE", "F_EST_DATE", "F_EST_POSITION", "S_INFO_SECTOR"),
        "交易日估算",
        "Wind细分类2001010201000000；逐日等权均值；0-1原值乘100",
    ),
    contract(
        "public.liquidation_count",
        "public",
        "基金清算数量",
        "只",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAFUNDMAJOREVENT",
        ("OBJECT_ID", "S_INFO_WINDCODE", "S_EVENT_CATEGORYCODE", "S_EVENT_HAPDATE"),
        "清算/到期事件发生日",
        "事件代码204030018或204030023；OBJECT_ID去重；按月求和",
    ),
    contract(
        "public.liquidation_scale",
        "public",
        "基金清算规模",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAFUNDMAJOREVENT + CHINAMUTUALFUNDNAV",
        ("S_EVENT_HAPDATE", "PRICE_DATE", "F_PRT_NETASSET"),
        "清算事件日",
        "取事件日前最后披露净资产；原字段元除以1e8；按月求和",
    ),
    contract(
        "etf.net_share_all",
        "etf",
        "全市场ETF净申购赎回份额",
        "亿份",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDDESCRIPTION",
        ("TRADE_DT", "F_UNIT_FLOATSHARE", "F_INFO_EXCHMARKET", "F_INFO_NAME"),
        "交易日",
        "仅ETF且排除联接基金；逐基金份额一阶差分后按周求和",
    ),
    contract(
        "etf.net_share_sse",
        "etf",
        "上交所ETF净申购赎回份额",
        "亿份",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDDESCRIPTION",
        ("TRADE_DT", "F_UNIT_FLOATSHARE", "F_INFO_EXCHMARKET", "F_INFO_NAME"),
        "交易日",
        "仅上海ETF；逐基金份额一阶差分后按周求和",
    ),
    contract(
        "etf.net_share_szse",
        "etf",
        "深交所ETF净申购赎回份额",
        "亿份",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDDESCRIPTION",
        ("TRADE_DT", "F_UNIT_FLOATSHARE", "F_INFO_EXCHMARKET", "F_INFO_NAME"),
        "交易日",
        "仅深圳ETF；逐基金份额一阶差分后按周求和",
    ),
    contract(
        "etf.flow_total",
        "etf",
        "ETF总资金流",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDNAV",
        ("TRADE_DT", "F_UNIT_FLOATSHARE", "PRICE_DATE", "F_NAV_ADJUSTED"),
        "交易日",
        "逐基金份额变化乘当日复权单位净值；按周求和",
    ),
    contract(
        "etf.flow_broad",
        "etf",
        "宽基ETF资金流",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAETFINVESTCLASS + CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDNAV",
        ("S_INFO_SECTOR", "TRADE_DT", "F_UNIT_FLOATSHARE", "F_NAV_ADJUSTED"),
        "交易日",
        "Wind ETF投资分类中宽基组；逐基金计算后按周求和",
    ),
    contract(
        "etf.flow_other",
        "etf",
        "其他ETF资金流",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.CHINAETFINVESTCLASS + CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDNAV",
        ("S_INFO_SECTOR", "TRADE_DT", "F_UNIT_FLOATSHARE", "F_NAV_ADJUSTED"),
        "交易日",
        "总资金流减宽基ETF资金流",
    ),
    contract(
        "etf.flow_sector",
        "etf",
        "ETF大类板块流入",
        "亿元",
        "category_weekly",
        "Wind SQL",
        "wande.dbo.CHINAETFINVESTCLASS + CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDNAV",
        ("S_INFO_SECTOR", "S_INFO_NAME", "TRADE_DT", "F_UNIT_FLOATSHARE", "F_NAV_ADJUSTED"),
        "最近完整周",
        "逐基金资金流按Wind ETF投资大类汇总",
    ),
    contract(
        "etf.flow_industry",
        "etf",
        "ETF行业流入",
        "亿元",
        "category_weekly",
        "Wind SQL",
        "wande.dbo.CHINAETFINVESTCLASS + CHINAMUTUALFUNDFLOATSHARE + CHINAMUTUALFUNDNAV",
        ("S_INFO_SECTOR", "S_INFO_NAME", "TRADE_DT", "F_UNIT_FLOATSHARE", "F_NAV_ADJUSTED"),
        "最近完整周",
        "逐基金资金流按申万一级行业映射汇总",
    ),
    contract(
        "margin.net_buy",
        "margin",
        "融资净买入",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.ASHAREMARGINTRADESUM",
        ("TRADE_DT", "S_MARSUM_EXCHMARKET", "S_MARSUM_PURCHWITHBORROWMONEY", "S_MARSUM_REPAYMENTTOBROKER"),
        "交易日",
        "SSE+SZSE+BSE；(融资买入-融资偿还)/1e8；按周求和",
    ),
    contract(
        "margin.balance",
        "margin",
        "融资余额",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.ASHAREMARGINTRADESUM",
        ("TRADE_DT", "S_MARSUM_EXCHMARKET", "S_MARSUM_TRADINGBALANCE"),
        "交易日",
        "SSE+SZSE+BSE；元除以1e8；周末最后值",
    ),
    contract(
        "margin.activity",
        "margin",
        "融资活跃度",
        "%",
        "daily",
        "Wind SQL",
        "wande.dbo.ASHAREMARGINTRADESUM",
        ("TRADE_DT", "S_MARSUM_PURCHWITHBORROWMONEY", "S_MARSUM_TURNOVER_AMOUNT"),
        "交易日",
        "融资买入额/(成交额万元×10000)×100；周均",
    ),
    contract(
        "margin.guarantee_ratio",
        "margin",
        "市场平均担保比例",
        "%",
        "monthly",
        "中证数据有限责任公司",
        "融资融券月度历史数据 + 月度统计",
        ("时间", "全市场平均担保比例（%）"),
        "月度发布",
        "历史表与最新月报按月份去重拼接；保持月末原值",
        exact_fallbacks=("Wind EDB M0076000",),
    ),
    contract(
        "margin.collateral_cash",
        "margin",
        "融资融券担保物现金",
        "亿元",
        "monthly",
        "中证数据有限责任公司",
        "融资融券月度历史数据 + 月度统计",
        ("时间", "担保资金"),
        "月度发布",
        "使用全市场担保资金月末值；不以融资余额推算",
        exact_fallbacks=("Wind EDB M0075996",),
    ),
    contract(
        "margin.collateral_securities",
        "margin",
        "融资融券担保物证券市值",
        "亿元",
        "monthly",
        "中证数据有限责任公司",
        "融资融券月度历史数据 + 月度统计",
        ("时间", "可充抵保证金证券市值:小计"),
        "月度发布",
        "股票、债券、基金及其他可充抵证券市值合计月末值",
        exact_fallbacks=("Wind EDB M0075997",),
    ),
    contract(
        "margin.industry_net_buy",
        "margin",
        "行业融资净买入",
        "亿元",
        "category_weekly",
        "Wind SQL",
        "wande.dbo.ASHAREMARGINTRADE + ASHARESWINDUSTRIESCLASS + ASHAREINDUSTRIESCODE",
        ("TRADE_DT", "S_INFO_WINDCODE", "S_MARGIN_PURCHWITHBORROWMONEY", "S_MARGIN_REPAYMENTTOBROKER", "SW_IND_CODE"),
        "最近完整周",
        "个股融资买入减偿还后按申万一级行业汇总",
    ),
    contract(
        "primary.ipo_amount",
        "primary",
        "IPO募集资金",
        "亿元",
        "event",
        "Wind SQL",
        "wande.dbo.ASHAREIPO",
        ("S_INFO_WINDCODE", "S_IPO_LISTDATE", "S_IPO_COLLECTION", "IS_FAILURE"),
        "上市日",
        "仅成功发行；募集资金万元除以10000；按周求和",
    ),
    contract(
        "primary.ipo_count",
        "primary",
        "IPO数量",
        "只",
        "event",
        "Wind SQL",
        "wande.dbo.ASHAREIPO",
        ("S_INFO_WINDCODE", "S_IPO_LISTDATE", "IS_FAILURE"),
        "上市日",
        "代码去重；按周计数",
    ),
    contract(
        "primary.seo_amount",
        "primary",
        "定增募集资金",
        "亿元",
        "event",
        "Wind SQL",
        "wande.dbo.ASHARESEO",
        ("S_INFO_WINDCODE", "S_FELLOW_DATE", "CURRENCY_SUBSCRIPTION_AMOUNT", "S_FELLOW_ISSUETYPE", "S_FELLOW_PROGRESS"),
        "实施日",
        "类型439006000且进度3；仅现金认购金额元除以1e8；按周求和",
    ),
    contract(
        "primary.seo_count",
        "primary",
        "定增数量",
        "只",
        "event",
        "Wind SQL",
        "wande.dbo.ASHARESEO",
        ("S_INFO_WINDCODE", "S_FELLOW_DATE", "S_FELLOW_ISSUETYPE", "S_FELLOW_PROGRESS"),
        "实施日",
        "项目代码去重；按周计数",
    ),
    contract(
        "primary.cb_amount",
        "primary",
        "可转债募集资金",
        "亿元",
        "event",
        "Wind SQL",
        "wande.dbo.CCBONDISSUANCE",
        ("S_INFO_WINDCODE", "CB_INFO_LISTEDDATE", "CB_LIST_ISSUESIZE", "IS_CONVERTIBLE_BONDS"),
        "发行/上市日",
        "仅可转换债；发行规模万元除以10000；按周求和",
    ),
    contract(
        "primary.cb_count",
        "primary",
        "可转债数量",
        "只",
        "event",
        "Wind SQL",
        "wande.dbo.CCBONDISSUANCE",
        ("S_INFO_WINDCODE", "CB_INFO_LISTEDDATE", "IS_CONVERTIBLE_BONDS"),
        "发行/上市日",
        "债券代码去重；按周计数",
    ),
    contract(
        "private.stock_long_position",
        "private",
        "股票多头平均仓位",
        "%",
        "monthly",
        "华润信托 CREFI",
        "官方月报列表 API + 月报 PDF",
        ("报告月份", "CREFI指数成分基金平均股票仓位"),
        "每月初十个工作日内发布",
        "按报告月份标准化到月末；逐份PDF解析并检查0-100及月份连续性",
        exact_fallbacks=("私募排排网股票类私募仓位指数",),
    ),
    contract(
        "private.aum",
        "private",
        "私募产品规模",
        "亿元",
        "monthly",
        "中国证券投资基金业协会",
        "私募基金行业数据/月度数据",
        ("月份", "私募证券投资基金管理规模"),
        "协会月度发布",
        "使用协会存续私募证券投资基金规模；不与全口径私募规模混用",
        exact_fallbacks=("Wind EDB同口径协会序列",),
    ),
    contract(
        "private.enhanced_300",
        "private",
        "300增强策略指数",
        "净值",
        "monthly",
        "Wind SQL",
        "CHINAHEDGEFUNDDESCRIPTION + CHINAHEDGEFUNDNAV",
        ("F_INFO_NAME", "PRICE_DATE", "F_NAV_DIVACCUMULATED"),
        "完整月末",
        "沪深300指增产品月末复权净值收益等权；连续月份；样本不少于10只；首值归一为1",
        exact_fallbacks=("华泰托管300增强样本",),
    ),
    contract(
        "private.enhanced_500",
        "private",
        "500增强策略指数",
        "净值",
        "monthly",
        "Wind SQL",
        "CHINAHEDGEFUNDDESCRIPTION + CHINAHEDGEFUNDNAV",
        ("F_INFO_NAME", "PRICE_DATE", "F_NAV_DIVACCUMULATED"),
        "完整月末",
        "中证500指增产品月末复权净值收益等权；连续月份；样本不少于10只；首值归一为1",
        exact_fallbacks=("华泰托管500增强样本",),
    ),
    contract(
        "private.enhanced_1000",
        "private",
        "1000增强策略指数",
        "净值",
        "monthly",
        "Wind SQL",
        "CHINAHEDGEFUNDDESCRIPTION + CHINAHEDGEFUNDNAV",
        ("F_INFO_NAME", "PRICE_DATE", "F_NAV_DIVACCUMULATED"),
        "完整月末",
        "中证1000指增产品月末复权净值收益等权；连续月份；样本不少于10只；首值归一为1",
        exact_fallbacks=("华泰托管1000增强样本",),
    ),
    contract(
        "private.neutral",
        "private",
        "市场中性策略指数",
        "净值",
        "monthly",
        "Wind SQL",
        "CHINAHEDGEFUNDSECTOR + CHINAHEDGEFUNDNAV",
        ("S_INFO_SECTOR=2001100200*", "PRICE_DATE", "F_NAV_DIVACCUMULATED"),
        "完整月末",
        "按分类生效区间筛选市场中性产品，月末复权净值收益等权；样本不少于10只",
        exact_fallbacks=("华泰托管市场中性样本",),
    ),
    contract(
        "private.cta",
        "private",
        "CTA策略指数",
        "净值",
        "monthly",
        "Wind SQL",
        "CHINAHEDGEFUNDSECTOR + CHINAHEDGEFUNDNAV",
        ("S_INFO_SECTOR=2001100700*", "PRICE_DATE", "F_NAV_DIVACCUMULATED"),
        "完整月末",
        "按分类生效区间筛选管理期货产品，月末复权净值收益等权；样本不少于10只",
        exact_fallbacks=("华泰托管CTA样本",),
    ),
    contract(
        "private.arbitrage",
        "private",
        "套利策略指数",
        "净值",
        "monthly",
        "Wind SQL",
        "CHINAHEDGEFUNDSECTOR + CHINAHEDGEFUNDNAV",
        ("S_INFO_SECTOR=2001100500*", "PRICE_DATE", "F_NAV_DIVACCUMULATED"),
        "完整月末",
        "按分类生效区间筛选套利产品，月末复权净值收益等权；样本不少于10只",
        exact_fallbacks=("华泰托管套利样本",),
    ),
    contract(
        "foreign.flow_total",
        "foreign",
        "配置型外资",
        "亿元",
        "weekly",
        "EPFR授权数据",
        "配置型外资/A股流量",
        ("周末", "配置型外资总量"),
        "EPFR周度发布",
        "币种按原模型汇率换算；不得用陆股通成交替代配置流",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.flow_active",
        "foreign",
        "主动配置",
        "亿元",
        "weekly",
        "EPFR授权数据",
        "配置型外资/A股主动流量",
        ("周末", "主动配置流"),
        "EPFR周度发布",
        "保持EPFR基金样本口径",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.flow_passive",
        "foreign",
        "被动配置",
        "亿元",
        "weekly",
        "EPFR授权数据",
        "配置型外资/A股被动流量",
        ("周末", "被动配置流"),
        "EPFR周度发布",
        "保持EPFR基金样本口径",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.cumulative_a",
        "foreign",
        "A股累计配置",
        "百万美元",
        "weekly",
        "EPFR授权数据",
        "A/H累计配置/A股",
        ("周末", "A股累计配置"),
        "EPFR周度发布",
        "以既定基期累计；不得拼接北向成交",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.cumulative_h",
        "foreign",
        "H股累计配置",
        "百万美元",
        "weekly",
        "EPFR授权数据",
        "A/H累计配置/H股",
        ("周末", "H股累计配置"),
        "EPFR周度发布",
        "以既定基期累计",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.northbound_turnover",
        "foreign",
        "陆股通日均成交",
        "亿元",
        "daily",
        "Wind SQL",
        "wande.dbo.SHSCDAILYSTATISTICS",
        ("TRADE_DT", "S_INFO_EXCHMARKET", "ITEM_CODE", "VALUE", "UNIT"),
        "交易日",
        "MHS、ITEM_CODE=293002008、单位亿元；按周平均",
        exact_fallbacks=("Wind EDB M0329530",),
    ),
    contract(
        "foreign.sse_index",
        "foreign",
        "上证指数",
        "点",
        "daily",
        "Wind SQL",
        "wande.dbo.AINDEXEODPRICES",
        ("S_INFO_WINDCODE", "TRADE_DT", "S_DQ_CLOSE"),
        "交易日收盘",
        "000001.SH；周末最后值",
        exact_fallbacks=("Wind EDB M0020188",),
    ),
    contract(
        "foreign.position_asia_ex_japan",
        "foreign",
        "亚洲除日本主动基金A股配置仓位",
        "配置比例",
        "monthly",
        "EPFR授权数据",
        "全球基金A股配置/亚洲除日本主动",
        ("月份", "A股配置比例"),
        "EPFR月度发布",
        "保持基金样本与权重口径",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.position_em_active",
        "foreign",
        "全球新兴市场主动基金A股配置仓位",
        "配置比例",
        "monthly",
        "EPFR授权数据",
        "全球基金A股配置/新兴市场主动",
        ("月份", "A股配置比例"),
        "EPFR月度发布",
        "保持基金样本与权重口径",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
    contract(
        "foreign.position_global_passive",
        "foreign",
        "全球被动基金A股配置仓位",
        "配置比例",
        "monthly",
        "EPFR授权数据",
        "全球基金A股配置/全球被动",
        ("月份", "A股配置比例"),
        "EPFR月度发布",
        "保持基金样本与权重口径",
        availability="licensed_runtime_required",
        unavailable_reason="当前 Wind SQL 授权库无EPFR表，且未提供EPFR程序化授权",
    ),
)

CONTRACT_BY_ID = {item.series_id: item for item in CONTRACTS}
if len(CONTRACT_BY_ID) != len(CONTRACTS):
    raise RuntimeError("duplicate series contract")


def iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%Y%m":
                return parsed.replace(day=1).date().isoformat()
            return parsed.date().isoformat()
        except ValueError:
            continue
    raise DataQualityError(f"invalid observation date: {text!r}")


def finite_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise DataQualityError("non-finite observation")
    return number


class WindOpenQueryClient:
    """Read-only Wind SQL mirror access through the configured linked server."""

    def __init__(
        self,
        server: str | None = None,
        uid: str | None = None,
        password: str | None = None,
        driver: str | None = None,
        linked_server: str = "WANDE",
        timeout: int = 60,
    ) -> None:
        self.server = server or os.environ.get("WIND_SQL_SERVER", "")
        self.uid = uid or os.environ.get("WIND_SQL_UID", "")
        self.password = password or os.environ.get("WIND_SQL_PASSWORD", "")
        self.driver = driver or os.environ.get("WIND_SQL_DRIVER", "SQL Server")
        self.linked_server = linked_server
        self.timeout = timeout
        if not all((self.server, self.uid, self.password)):
            raise SourceConfigurationError(
                "WIND_SQL_SERVER, WIND_SQL_UID and WIND_SQL_PASSWORD are required"
            )

    @staticmethod
    def _validate_read_only(sql: str) -> str:
        normalized = " ".join(sql.strip().split())
        lowered = normalized.lower()
        if not (lowered.startswith("select ") or lowered.startswith("with ")):
            raise ValueError("Wind query must be a SELECT or read-only CTE statement")
        if lowered.startswith("with ") and " select " not in f" {lowered} ":
            raise ValueError("Wind CTE query must terminate in SELECT")
        padded = f" {normalized.lower()} "
        blocked = (
            " insert ",
            " update ",
            " delete ",
            " merge ",
            " drop ",
            " alter ",
            " truncate ",
            " exec ",
            " execute ",
        )
        if any(token in padded for token in blocked):
            raise ValueError("Wind query contains a blocked write keyword")
        return normalized

    def query(self, remote_sql: str) -> QueryResult:
        import pyodbc

        remote_sql = self._validate_read_only(remote_sql)
        escaped = remote_sql.replace("'", "''")
        wrapper = f"SELECT * FROM OPENQUERY({self.linked_server}, '{escaped}')"
        connection_string = (
            f"DRIVER={{{self.driver}}};SERVER={self.server};"
            f"UID={self.uid};PWD={self.password};"
            "APP=QuantStrategyAgent-Liquidity;TrustServerCertificate=yes"
        )
        last_error: Exception | None = None
        for attempt in range(3):
            connection = None
            try:
                connection = pyodbc.connect(
                    connection_string, timeout=self.timeout
                )
                cursor = connection.cursor()
                cursor.execute(wrapper)
                columns = tuple(
                    column[0].lower() for column in cursor.description
                )
                rows = tuple(
                    tuple(value for value in row) for row in cursor.fetchall()
                )
                return QueryResult(columns=columns, rows=rows)
            except pyodbc.Error as error:
                last_error = error
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
            finally:
                if connection is not None:
                    connection.close()
        assert last_error is not None
        raise last_error


class WindEDBClient:
    """Optional Wind terminal EDB reader; it never performs an interactive login."""

    def __init__(self) -> None:
        try:
            from WindPy import w
        except ImportError as error:
            raise SourceUnavailableError("WindPy is not installed") from error
        status = w.start()
        code = getattr(status, "ErrorCode", status)
        if code not in (0, None):
            raise SourceUnavailableError(f"Wind terminal is unavailable (error {code})")
        self.w = w

    def series(self, indicator_id: str, start: date, end: date) -> dict[str, float]:
        response = self.w.edb(indicator_id, start.isoformat(), end.isoformat(), "")
        if getattr(response, "ErrorCode", -1) != 0:
            raise SourceUnavailableError(
                f"Wind EDB {indicator_id} failed (error {response.ErrorCode})"
            )
        times = list(getattr(response, "Times", []) or [])
        rows = list(getattr(response, "Data", []) or [])
        values = rows[0] if rows else []
        output: dict[str, float] = {}
        for when, raw in zip(times, values):
            if raw is None:
                continue
            output[iso_date(when)] = finite_float(raw)
        if not output:
            raise DataQualityError(f"Wind EDB {indicator_id} returned no observations")
        return output


class IFindEDBClient:
    """Optional iFinD EDB reader used only for exact, registered fallbacks."""

    def __init__(self) -> None:
        username = os.environ.get("IFIND_USERNAME", "")
        password = os.environ.get("IFIND_PASSWORD", "")
        if not username or not password:
            raise SourceConfigurationError("IFIND_USERNAME and IFIND_PASSWORD are required")
        try:
            from iFinDPy import THS_EDB, THS_iFinDLogin
        except ImportError as error:
            raise SourceUnavailableError("iFinDPy is not installed") from error
        login_code = THS_iFinDLogin(username, password)
        if login_code != 0:
            raise SourceUnavailableError(f"iFinD login failed (error {login_code})")
        self._edb = THS_EDB

    def series(self, indicator_id: str, start: date, end: date) -> dict[str, float]:
        response = self._edb(
            indicator_id,
            "",
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
        if getattr(response, "errorcode", -1) != 0:
            raise SourceUnavailableError(
                f"iFinD EDB {indicator_id} failed (error {response.errorcode})"
            )
        payload = json.loads(response.data) if isinstance(response.data, str) else response.data
        output: dict[str, float] = {}
        for table in payload.get("tables", []):
            for row in table.get("table", []):
                when = row.get("time") or row.get("date")
                raw = row.get("value")
                if when is not None and raw is not None:
                    output[iso_date(when)] = finite_float(raw)
        if not output:
            raise DataQualityError(f"iFinD EDB {indicator_id} returned no observations")
        return output


class LiquidityCache:
    """Versioned SQLite cache with atomic per-series replacement and provenance."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS series_contracts (
                    series_id TEXT PRIMARY KEY,
                    contract_json TEXT NOT NULL,
                    contract_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    series_id TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    value REAL NOT NULL,
                    provider TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    PRIMARY KEY (series_id, observation_date)
                );
                CREATE TABLE IF NOT EXISTS category_observations (
                    series_id TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    category TEXT NOT NULL,
                    value REAL NOT NULL,
                    provider TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    PRIMARY KEY (series_id, as_of_date, category)
                );
                CREATE TABLE IF NOT EXISTS refresh_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            for item in CONTRACTS:
                payload = json.dumps(asdict(item), ensure_ascii=False, sort_keys=True)
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                connection.execute(
                    """
                    INSERT INTO series_contracts(series_id,contract_json,contract_hash,updated_at)
                    VALUES(?,?,?,?)
                    ON CONFLICT(series_id) DO UPDATE SET
                        contract_json=excluded.contract_json,
                        contract_hash=excluded.contract_hash,
                        updated_at=excluded.updated_at
                    """,
                    (item.series_id, payload, digest, now),
                )

    def start_run(self) -> str:
        self.initialize()
        run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO refresh_runs(run_id,started_at,status,details_json) VALUES(?,?,?,?)",
                (
                    run_id,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    "running",
                    "{}",
                ),
            )
        return run_id

    def finish_run(self, run_id: str, status: str, details: Mapping[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE refresh_runs
                SET finished_at=?, status=?, details_json=?
                WHERE run_id=?
                """,
                (
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    status,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )

    def replace_series(
        self,
        series_id: str,
        values: Mapping[str, float],
        provider: str,
        locator: str,
        run_id: str,
    ) -> None:
        if series_id not in CONTRACT_BY_ID:
            raise KeyError(series_id)
        normalized = sorted((iso_date(when), finite_float(raw)) for when, raw in values.items())
        if len(normalized) < 2:
            raise DataQualityError(f"{series_id}: fewer than two observations")
        dates = [when for when, _ in normalized]
        if len(dates) != len(set(dates)):
            raise DataQualityError(f"{series_id}: duplicate dates")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM observations WHERE series_id=?", (series_id,))
            connection.executemany(
                """
                INSERT INTO observations(
                    series_id,observation_date,value,provider,locator,retrieved_at,run_id
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [
                    (series_id, when, value, provider, locator, now, run_id)
                    for when, value in normalized
                ],
            )

    def replace_categories(
        self,
        series_id: str,
        as_of_date: str,
        values: Mapping[str, float],
        provider: str,
        locator: str,
        run_id: str,
    ) -> None:
        if series_id not in CONTRACT_BY_ID:
            raise KeyError(series_id)
        as_of = iso_date(as_of_date)
        normalized = sorted(
            (str(category).strip(), finite_float(raw))
            for category, raw in values.items()
            if str(category).strip()
        )
        if not normalized:
            raise DataQualityError(f"{series_id}: no category observations")
        if len({category for category, _ in normalized}) != len(normalized):
            raise DataQualityError(f"{series_id}: duplicate categories")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM category_observations WHERE series_id=?", (series_id,)
            )
            connection.executemany(
                """
                INSERT INTO category_observations(
                    series_id,as_of_date,category,value,provider,locator,retrieved_at,run_id
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (series_id, as_of, category, value, provider, locator, now, run_id)
                    for category, value in normalized
                ],
            )

    def audit(self) -> dict[str, Any]:
        self.initialize()
        today = date.today().isoformat()
        with self.connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT c.series_id,
                       COUNT(o.observation_date) AS observations,
                       MIN(o.observation_date) AS start_date,
                       MAX(o.observation_date) AS end_date,
                       COUNT(DISTINCT o.provider) AS provider_count,
                       SUM(CASE WHEN o.observation_date > ? THEN 1 ELSE 0 END)
                           AS future_observations
                FROM series_contracts c
                LEFT JOIN observations o ON o.series_id=c.series_id
                GROUP BY c.series_id
                """,
                (today,),
            ).fetchall()
            category_rows = connection.execute(
                """
                SELECT series_id,
                       COUNT(*) AS observations,
                       MAX(as_of_date) AS end_date,
                       SUM(CASE WHEN as_of_date > ? THEN 1 ELSE 0 END)
                           AS future_observations
                FROM category_observations GROUP BY series_id
                """,
                (today,),
            ).fetchall()
        category_by_id = {row["series_id"]: dict(row) for row in category_rows}
        series: list[dict[str, Any]] = []
        missing: list[str] = []
        invalid_future: list[str] = []
        for row in rows:
            item = CONTRACT_BY_ID[row["series_id"]]
            count = int(row["observations"])
            end_date = row["end_date"]
            future_count = int(row["future_observations"] or 0)
            if item.native_frequency.startswith("category"):
                category = category_by_id.get(item.series_id, {})
                count = int(category.get("observations", 0))
                end_date = category.get("end_date")
                future_count = int(category.get("future_observations", 0) or 0)
            has_data = count >= 2 or (
                item.native_frequency.startswith("category") and count >= 1
            )
            if future_count:
                status = "invalid_future_date"
                invalid_future.append(item.series_id)
            elif has_data:
                status = "ready"
            else:
                status = item.availability if item.availability != "ready" else "missing"
            if item.required and status != "ready":
                missing.append(item.series_id)
            series.append(
                {
                    "series_id": item.series_id,
                    "page": item.page,
                    "status": status,
                    "observations": count,
                    "start": row["start_date"],
                    "end": end_date,
                    "future_observations": future_count,
                    "preferred_provider": item.preferred_provider,
                    "locator": item.locator,
                    "reason": item.unavailable_reason if status != "ready" else "",
                }
            )
        return {
            "status": "passed" if not missing else "blocked",
            "schema_version": SCHEMA_VERSION,
            "required_series": len(CONTRACTS),
            "ready_series": len(CONTRACTS) - len(missing),
            "missing_required_series": missing,
            "future_dated_series": invalid_future,
            "excel_numeric_dependency": False,
            "series": series,
        }


def result_series(
    result: QueryResult,
    mappings: Mapping[str, str],
    date_column: str = "observation_date",
) -> dict[str, dict[str, float]]:
    output = {series_id: {} for series_id in mappings}
    for row in result.as_dicts():
        when = iso_date(row[date_column])
        for series_id, value_column in mappings.items():
            raw = row.get(value_column)
            if raw is not None:
                output[series_id][when] = finite_float(raw)
    return output


def wind_sql_queries(start: date) -> tuple[tuple[str, str, Mapping[str, str]], ...]:
    start_key = start.strftime("%Y%m%d")
    end_key = date.today().strftime("%Y%m%d")
    return (
        (
            "retail",
            f"""
            SELECT TRADE_DT AS observation_date,
                   SUM(VALUE_DIFF_SMALL_TRADER) / 10000.0 AS small_net
            FROM wande.dbo.ASHAREMONEYFLOW
            WHERE TRADE_DT >= '{start_key}'
              AND TRADE_DT <= '{end_key}'
            GROUP BY TRADE_DT
            ORDER BY TRADE_DT
            """,
            {"retail.small_net": "small_net"},
        ),
        (
            "public_new",
            f"""
            SELECT F_INFO_SETUPDATE AS observation_date,
                   SUM(F_ISSUE_SHARES) AS new_equity_shares
            FROM wande.dbo.CHINAMUTUALFUNDISSUE
            WHERE F_INFO_SETUPDATE >= '{start_key}'
              AND F_INFO_SETUPDATE <= '{end_key}'
              AND F_INFO_INVESTYPE IN (1,3)
              AND F_ISSUE_SHARES IS NOT NULL
            GROUP BY F_INFO_SETUPDATE
            ORDER BY F_INFO_SETUPDATE
            """,
            {"public.new_equity_shares": "new_equity_shares"},
        ),
        (
            "public_filings",
            f"""
            SELECT APPLY_PICKUP_DATE AS observation_date,
                   SUM(CASE WHEN TYPE_NAME LIKE '%股票型%' THEN 1 ELSE 0 END) AS stock_count,
                   SUM(CASE WHEN TYPE_NAME LIKE '%混合型%' THEN 1 ELSE 0 END) AS mixed_count
            FROM (
                SELECT DISTINCT OBJECT_ID, APPLY_PICKUP_DATE, TYPE_NAME
                FROM wande.dbo.CFUNDADMPERMITSCHEDULE
                WHERE TYPE_CODE='267005001'
                  AND APPLY_PICKUP_DATE >= '{start_key}'
                  AND APPLY_PICKUP_DATE <= '{end_key}'
                  AND APPLY_PICKUP_DATE IS NOT NULL
            ) X
            GROUP BY APPLY_PICKUP_DATE
            ORDER BY APPLY_PICKUP_DATE
            """,
            {
                "public.filings_stock": "stock_count",
                "public.filings_mixed": "mixed_count",
            },
        ),
        (
            "public_position",
            f"""
            SELECT P.F_EST_DATE AS observation_date,
                   AVG(CASE WHEN S.S_INFO_SECTOR='2001010101000000'
                            THEN P.F_EST_POSITION*100.0 END) AS stock_position,
                   AVG(CASE WHEN S.S_INFO_SECTOR='2001010201000000'
                            THEN P.F_EST_POSITION*100.0 END) AS mixed_position
            FROM wande.dbo.CHINAMUTUALFUNDPOSESTIMATION P
            JOIN wande.dbo.CHINAMUTUALFUNDSECTOR S
              ON P.S_INFO_WINDCODE=S.F_INFO_WINDCODE
             AND P.F_EST_DATE>=S.S_INFO_SECTORENTRYDT
             AND (S.S_INFO_SECTOREXITDT IS NULL
                  OR P.F_EST_DATE<=S.S_INFO_SECTOREXITDT)
             AND S.S_INFO_SECTOR IN
                 ('2001010101000000','2001010201000000')
            WHERE P.F_EST_DATE >= '{start_key}'
              AND P.F_EST_DATE <= '{end_key}'
              AND P.F_EST_POSITION IS NOT NULL
            GROUP BY P.F_EST_DATE
            ORDER BY P.F_EST_DATE
            """,
            {
                "public.position_stock": "stock_position",
                "public.position_mixed": "mixed_position",
            },
        ),
        (
            "public_liquidation",
            f"""
            SELECT E.S_EVENT_HAPDATE AS observation_date,
                   COUNT(DISTINCT E.OBJECT_ID) AS liquidation_count,
                   SUM(N.F_PRT_NETASSET) / 100000000.0 AS liquidation_scale
            FROM wande.dbo.CHINAFUNDMAJOREVENT E
            OUTER APPLY (
                SELECT TOP 1 F_PRT_NETASSET
                FROM wande.dbo.CHINAMUTUALFUNDNAV N0
                WHERE N0.F_INFO_WINDCODE=E.S_INFO_WINDCODE
                  AND N0.PRICE_DATE<=E.S_EVENT_HAPDATE
                  AND N0.F_PRT_NETASSET IS NOT NULL
                ORDER BY N0.PRICE_DATE DESC
            ) N
            WHERE E.S_EVENT_HAPDATE >= '{start_key}'
              AND E.S_EVENT_HAPDATE <= '{end_key}'
              AND E.S_EVENT_CATEGORYCODE IN ('204030018','204030023')
            GROUP BY E.S_EVENT_HAPDATE
            ORDER BY E.S_EVENT_HAPDATE
            """,
            {
                "public.liquidation_count": "liquidation_count",
                "public.liquidation_scale": "liquidation_scale",
            },
        ),
        (
            "margin",
            f"""
            SELECT TRADE_DT AS observation_date,
                   (SUM(S_MARSUM_PURCHWITHBORROWMONEY)
                    -SUM(S_MARSUM_REPAYMENTTOBROKER))/100000000.0 AS net_buy,
                   SUM(S_MARSUM_TRADINGBALANCE)/100000000.0 AS balance,
                   CASE WHEN SUM(S_MARSUM_TURNOVER_AMOUNT)>0
                        THEN SUM(S_MARSUM_PURCHWITHBORROWMONEY)
                             /(SUM(S_MARSUM_TURNOVER_AMOUNT)*10000.0)*100
                        END AS activity
            FROM wande.dbo.ASHAREMARGINTRADESUM
            WHERE TRADE_DT >= '{start_key}'
              AND TRADE_DT <= '{end_key}'
              AND S_MARSUM_EXCHMARKET IN ('SSE','SZSE','BSE')
            GROUP BY TRADE_DT
            ORDER BY TRADE_DT
            """,
            {
                "margin.net_buy": "net_buy",
                "margin.balance": "balance",
                "margin.activity": "activity",
            },
        ),
        (
            "primary_ipo",
            f"""
            SELECT S_IPO_LISTDATE AS observation_date,
                   SUM(S_IPO_COLLECTION)/10000.0 AS ipo_amount,
                   COUNT(DISTINCT S_INFO_WINDCODE) AS ipo_count
            FROM wande.dbo.ASHAREIPO
            WHERE S_IPO_LISTDATE >= '{start_key}'
              AND S_IPO_LISTDATE <= '{end_key}'
              AND (IS_FAILURE IS NULL OR IS_FAILURE=0)
              AND S_IPO_COLLECTION IS NOT NULL
            GROUP BY S_IPO_LISTDATE
            ORDER BY S_IPO_LISTDATE
            """,
            {
                "primary.ipo_amount": "ipo_amount",
                "primary.ipo_count": "ipo_count",
            },
        ),
        (
            "primary_seo",
            f"""
            SELECT S_FELLOW_DATE AS observation_date,
                   SUM(CURRENCY_SUBSCRIPTION_AMOUNT)/100000000.0 AS seo_amount,
                   COUNT(DISTINCT S_INFO_WINDCODE) AS seo_count
            FROM wande.dbo.ASHARESEO
            WHERE S_FELLOW_DATE >= '{start_key}'
              AND S_FELLOW_DATE <= '{end_key}'
              AND CURRENCY_SUBSCRIPTION_AMOUNT>0
              AND S_FELLOW_ISSUETYPE='439006000'
              AND S_FELLOW_PROGRESS=3
            GROUP BY S_FELLOW_DATE
            ORDER BY S_FELLOW_DATE
            """,
            {
                "primary.seo_amount": "seo_amount",
                "primary.seo_count": "seo_count",
            },
        ),
        (
            "primary_cb",
            f"""
            SELECT CB_INFO_LISTEDDATE AS observation_date,
                   SUM(CB_LIST_ISSUESIZE)/10000.0 AS cb_amount,
                   COUNT(DISTINCT S_INFO_WINDCODE) AS cb_count
            FROM wande.dbo.CCBONDISSUANCE
            WHERE CB_INFO_LISTEDDATE >= '{start_key}'
              AND CB_INFO_LISTEDDATE <= '{end_key}'
              AND CB_LIST_ISSUESIZE IS NOT NULL
              AND IS_CONVERTIBLE_BONDS=1
            GROUP BY CB_INFO_LISTEDDATE
            ORDER BY CB_INFO_LISTEDDATE
            """,
            {
                "primary.cb_amount": "cb_amount",
                "primary.cb_count": "cb_count",
            },
        ),
        (
            "foreign_turnover",
            f"""
            SELECT TRADE_DT AS observation_date,
                   MAX(CASE WHEN S_INFO_EXCHMARKET='MHS'
                                 AND ITEM_CODE='293002008'
                            THEN VALUE END) AS northbound_turnover
            FROM wande.dbo.SHSCDAILYSTATISTICS
            WHERE TRADE_DT >= '{start_key}'
              AND TRADE_DT <= '{end_key}'
              AND S_INFO_EXCHMARKET='MHS'
              AND ITEM_CODE='293002008'
            GROUP BY TRADE_DT
            ORDER BY TRADE_DT
            """,
            {"foreign.northbound_turnover": "northbound_turnover"},
        ),
        (
            "foreign_sse",
            f"""
            SELECT TRADE_DT AS observation_date,
                   S_DQ_CLOSE AS sse_index
            FROM wande.dbo.AINDEXEODPRICES
            WHERE S_INFO_WINDCODE='000001.SH'
              AND TRADE_DT >= '{start_key}'
            ORDER BY TRADE_DT
            """,
            {"foreign.sse_index": "sse_index"},
        ),
    )


def refresh_wind_sql(
    cache: LiquidityCache,
    run_id: str,
    start: date,
    selected: set[str] | None = None,
) -> dict[str, Any]:
    queries = wind_sql_queries(start)
    wind_series = {
        series_id
        for _, _, mappings in queries
        for series_id in mappings
    }
    if selected is not None and not (selected & wind_series):
        return {"refreshed": [], "errors": {}}
    try:
        client = WindOpenQueryClient()
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"wind_sql": f"{type(error).__name__}: {error}"},
        }
    refreshed: list[str] = []
    errors: dict[str, str] = {}
    for query_name, sql, mappings in queries:
        active = {
            series_id: column
            for series_id, column in mappings.items()
            if selected is None or series_id in selected
        }
        if not active:
            continue
        try:
            result = client.query(sql)
            series = result_series(result, active)
            for series_id, values in series.items():
                contract_item = CONTRACT_BY_ID[series_id]
                cache.replace_series(
                    series_id,
                    values,
                    contract_item.preferred_provider,
                    contract_item.locator,
                    run_id,
                )
                refreshed.append(series_id)
        except Exception as error:
            errors[query_name] = f"{type(error).__name__}: {error}"
    return {"refreshed": refreshed, "errors": errors}


def refresh_edb(
    cache: LiquidityCache,
    run_id: str,
    start: date,
    end: date,
    selected: set[str] | None = None,
) -> dict[str, Any]:
    mappings = {
        "retail.participating_investors": ("R7708385", 0.0001),
        "retail.new_accounts": ("M0010401", 1.0),
    }
    active = {
        series_id: spec
        for series_id, spec in mappings.items()
        if selected is None or series_id in selected
    }
    if not active:
        return {"refreshed": [], "errors": {}}
    refreshed: list[str] = []
    errors: dict[str, str] = {}
    wind_error: str | None = None
    try:
        client = WindEDBClient()
    except Exception as error:
        client = None
        wind_error = f"{type(error).__name__}: {error}"
        errors["wind_edb"] = wind_error
    for series_id, (indicator_id, scale) in active.items():
        try:
            if client is None:
                raise SourceUnavailableError(wind_error or "Wind EDB is unavailable")
            values = {
                when: value * scale
                for when, value in client.series(indicator_id, start, end).items()
            }
            item = CONTRACT_BY_ID[series_id]
            cache.replace_series(
                series_id,
                values,
                item.preferred_provider,
                indicator_id,
                run_id,
            )
            refreshed.append(series_id)
        except Exception as error:
            errors[series_id] = f"{type(error).__name__}: {error}"
    if "retail.participating_investors" in active and "retail.participating_investors" not in refreshed:
        try:
            ifind_client = IFindEDBClient()
            values = {
                when: value * 0.0001
                for when, value in ifind_client.series("S004085260", start, end).items()
            }
            item = CONTRACT_BY_ID["retail.participating_investors"]
            cache.replace_series(
                "retail.participating_investors",
                values,
                "iFinD EDB",
                "S004085260",
                run_id,
            )
            refreshed.append("retail.participating_investors")
            errors.pop("retail.participating_investors", None)
        except Exception as error:
            errors["retail.participating_investors.ifind"] = f"{type(error).__name__}: {error}"
    return {"refreshed": refreshed, "errors": errors}


def parse_selected(raw: Sequence[str] | None) -> set[str] | None:
    if not raw:
        return None
    selected = set(raw)
    unknown = selected - set(CONTRACT_BY_ID)
    if unknown:
        raise ValueError(f"unknown series ids: {sorted(unknown)}")
    return selected


def command_contracts(args: argparse.Namespace) -> int:
    payload = [asdict(item) for item in CONTRACTS]
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def command_refresh(args: argparse.Namespace) -> int:
    cache = LiquidityCache(args.cache)
    run_id = cache.start_run()
    selected = parse_selected(args.series)

    def guarded(name: str, fn: Any) -> dict[str, Any]:
        try:
            return fn()
        except Exception as exc:
            if not args.allow_incomplete:
                raise
            return {
                "refreshed": [],
                "errors": {"error": f"{type(exc).__name__}: {exc}"},
            }

    details: dict[str, Any] = {
        "wind_sql": guarded(
            "wind_sql", lambda: refresh_wind_sql(cache, run_id, args.start, selected)
        ),
    }
    from specialized_refresh import (
        refresh_etf,
        refresh_margin_industry,
        refresh_private_indices,
    )

    details["wind_etf"] = guarded(
        "wind_etf",
        lambda: refresh_etf(
            cache,
            run_id,
            args.start,
            selected,
            WindOpenQueryClient,
            CONTRACT_BY_ID,
            iso_date,
            finite_float,
        ),
    )
    details["wind_margin_industry"] = guarded(
        "wind_margin_industry",
        lambda: refresh_margin_industry(
            cache,
            run_id,
            selected,
            WindOpenQueryClient,
            CONTRACT_BY_ID,
            iso_date,
            finite_float,
        ),
    )
    details["wind_private_indices"] = guarded(
        "wind_private_indices",
        lambda: refresh_private_indices(
            cache,
            run_id,
            args.start,
            selected,
            WindOpenQueryClient,
            CONTRACT_BY_ID,
            finite_float,
        ),
    )
    from official_refresh import (
        refresh_amac_private_aum,
        refresh_crefi_position,
        refresh_csdata_margin_monthly,
    )

    details["amac_private"] = guarded(
        "amac_private",
        lambda: refresh_amac_private_aum(
            cache,
            run_id,
            selected,
            CONTRACT_BY_ID,
            finite_float,
        ),
    )
    details["csdata_margin"] = guarded(
        "csdata_margin",
        lambda: refresh_csdata_margin_monthly(
            cache,
            run_id,
            args.start,
            selected,
            CONTRACT_BY_ID,
            finite_float,
        ),
    )
    details["crefi_position"] = guarded(
        "crefi_position",
        lambda: refresh_crefi_position(
            cache,
            run_id,
            args.start,
            selected,
            CONTRACT_BY_ID,
        ),
    )
    if not args.skip_edb:
        details["wind_edb"] = guarded(
            "wind_edb", lambda: refresh_edb(cache, run_id, args.start, args.end, selected)
        )
    audit = cache.audit()
    details["audit"] = {
        "status": audit["status"],
        "ready_series": audit["ready_series"],
        "required_series": audit["required_series"],
        "missing_required_series": audit["missing_required_series"],
    }
    status = "passed" if audit["status"] == "passed" else "blocked"
    cache.finish_run(run_id, status, details)
    print(json.dumps({"run_id": run_id, "status": status, **details}, ensure_ascii=False, indent=2))
    return 0 if status == "passed" or args.allow_incomplete else 2

def command_audit(args: argparse.Namespace) -> int:
    payload = LiquidityCache(args.cache).audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" or not args.strict else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh and audit the liquidity dashboard source cache."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    contracts_parser = subparsers.add_parser("contracts")
    contracts_parser.add_argument("--output", type=Path)
    contracts_parser.set_defaults(func=command_contracts)

    refresh_parser = subparsers.add_parser("refresh")
    refresh_parser.add_argument("--cache", required=True, type=Path)
    refresh_parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    refresh_parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    refresh_parser.add_argument("--series", nargs="*")
    refresh_parser.add_argument("--skip-edb", action="store_true")
    refresh_parser.add_argument("--allow-incomplete", action="store_true")
    refresh_parser.set_defaults(func=command_refresh)

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--cache", required=True, type=Path)
    audit_parser.add_argument("--strict", action="store_true")
    audit_parser.set_defaults(func=command_audit)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
