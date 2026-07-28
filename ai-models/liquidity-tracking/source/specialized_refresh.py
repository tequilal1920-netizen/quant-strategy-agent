"""Specialized Wind refreshers used by the liquidity source layer."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping


INDUSTRY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("农林牧渔", ("农林牧渔", "农业", "畜牧", "养殖", "种业")),
    ("煤炭", ("煤炭",)),
    ("石油石化", ("石油石化", "油气",)),
    ("基础化工", ("基础化工", "化工",)),
    ("钢铁", ("钢铁",)),
    ("有色金属", ("有色金属", "稀有金属", "稀土", "黄金")),
    ("建筑材料", ("建筑材料", "建材")),
    ("建筑装饰", ("建筑装饰", "建筑工程")),
    ("电力设备", ("电力设备", "新能源", "光伏", "电池")),
    ("机械设备", ("机械设备", "机械")),
    ("国防军工", ("国防军工", "军工")),
    ("汽车", ("汽车",)),
    ("家用电器", ("家用电器", "家电")),
    ("轻工制造", ("轻工制造",)),
    ("纺织服饰", ("纺织服饰", "纺织")),
    ("食品饮料", ("食品饮料", "白酒")),
    ("医药生物", ("医药生物", "医药", "医疗", "创新药")),
    ("公用事业", ("公用事业", "电力")),
    ("交通运输", ("交通运输", "物流")),
    ("房地产", ("房地产", "地产")),
    ("商贸零售", ("商贸零售", "零售")),
    ("社会服务", ("社会服务", "旅游", "酒店")),
    ("银行", ("银行",)),
    ("非银金融", ("非银金融", "证券", "保险")),
    ("计算机", ("计算机", "软件", "云计算")),
    ("传媒", ("传媒", "游戏")),
    ("通信", ("通信",)),
    ("电子", ("半导体", "芯片", "电子", "消费电子")),
    ("环保", ("环保",)),
    ("美容护理", ("美容护理",)),
)

BROAD_INDEX_KEYWORDS: tuple[str, ...] = (
    "沪深300",
    "中证500",
    "中证800",
    "中证1000",
    "中证2000",
    "中证A500",
    "上证50",
    "上证180",
    "上证综指",
    "深证成指",
    "创业板",
    "科创50",
    "红利",
    "央企",
    "国企",
)


def classify_etf_industry(index_name: str, fund_name: str) -> str | None:
    text = f"{index_name or ''} {fund_name or ''}"
    for label, keywords in INDUSTRY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return label
    return None


def is_broad_etf(index_name: str, fund_name: str) -> bool:
    if classify_etf_industry(index_name, fund_name):
        return False
    text = f"{index_name or ''} {fund_name or ''}"
    return any(keyword in text for keyword in BROAD_INDEX_KEYWORDS)


def refresh_etf(
    cache: Any,
    run_id: str,
    start: date,
    selected: set[str] | None,
    client_factory: Callable[[], Any],
    contracts: Mapping[str, Any],
    iso_date: Callable[[Any], str],
    finite_float: Callable[[Any], float],
) -> dict[str, Any]:
    target_ids = {
        "etf.net_share_all",
        "etf.net_share_sse",
        "etf.net_share_szse",
        "etf.flow_total",
        "etf.flow_broad",
        "etf.flow_other",
        "etf.flow_sector",
        "etf.flow_industry",
    }
    active = target_ids if selected is None else target_ids & selected
    if not active:
        return {"refreshed": [], "errors": {}}
    query_start_date = start - timedelta(days=45)
    query_end_date = date.today()

    def build_sql(chunk_start: date, chunk_end: date) -> str:
        return f"""
        SELECT F.S_INFO_WINDCODE AS fund_code,
               F.TRADE_DT AS observation_date,
               F.F_UNIT_FLOATSHARE AS float_share,
               N.F_NAV_UNIT AS nav_unit,
               N.F_NAV_ADJFACTOR AS nav_factor,
               C.S_INFO_NAME AS asset_class,
               D.F_INFO_NAME AS fund_name,
               X.index_name AS index_name
        FROM wande.dbo.CHINAMUTUALFUNDFLOATSHARE F
        JOIN wande.dbo.CHINAETFINVESTCLASS C
          ON F.S_INFO_WINDCODE=C.S_INFO_WINDCODE
        JOIN wande.dbo.CHINAMUTUALFUNDDESCRIPTION D
          ON F.S_INFO_WINDCODE=D.F_INFO_WINDCODE
        LEFT JOIN wande.dbo.CHINAMUTUALFUNDNAV N
          ON F.S_INFO_WINDCODE=N.F_INFO_WINDCODE
         AND F.TRADE_DT=N.PRICE_DATE
        OUTER APPLY (
            SELECT TOP 1 I.S_INFO_NAME AS index_name
            FROM wande.dbo.CHINAMUTUALFUNDTRACKINGINDEX T
            LEFT JOIN wande.dbo.AINDEXDESCRIPTION I
              ON T.S_INFO_INDEXWINDCODE=I.S_INFO_WINDCODE
            WHERE T.S_INFO_WINDCODE=F.S_INFO_WINDCODE
              AND T.ENTRY_DT<=F.TRADE_DT
              AND (T.REMOVE_DT IS NULL OR T.REMOVE_DT>=F.TRADE_DT)
            ORDER BY T.ENTRY_DT DESC
        ) X
        WHERE F.TRADE_DT>='{chunk_start.strftime("%Y%m%d")}'
          AND F.TRADE_DT<='{chunk_end.strftime("%Y%m%d")}'
          AND D.F_INFO_NAME NOT LIKE '%联接%'
        ORDER BY F.S_INFO_WINDCODE,F.TRADE_DT
        """
    try:
        rows: list[dict[str, Any]] = []
        chunk_start = query_start_date
        while chunk_start <= query_end_date:
            chunk_end = min(
                query_end_date,
                date(chunk_start.year, 12, 31),
            )
            rows.extend(
                client_factory().query(
                    build_sql(chunk_start, chunk_end)
                ).as_dicts()
            )
            chunk_start = chunk_end + timedelta(days=1)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["fund_code"])].append(row)
        series: dict[str, dict[str, float]] = {
            series_id: defaultdict(float)
            for series_id in target_ids
            if series_id not in {"etf.flow_sector", "etf.flow_industry"}
        }
        sector_by_week: dict[date, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        industry_by_week: dict[date, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        dates_by_week: dict[date, set[date]] = defaultdict(set)
        for code, fund_rows in grouped.items():
            fund_rows.sort(key=lambda row: str(row["observation_date"]))
            effective_shares = [finite_float(row["float_share"]) for row in fund_rows]
            factors = [finite_float(row["nav_factor"] or 1.0) for row in fund_rows]
            for index in range(1, len(fund_rows) - 1):
                prior_normalized = effective_shares[index - 1] / factors[index - 1]
                current_normalized = effective_shares[index] / factors[index]
                next_normalized = effective_shares[index + 1] / factors[index + 1]
                spike_threshold = max(abs(prior_normalized) * 0.5, 100000000.0)
                return_tolerance = max(abs(prior_normalized) * 0.02, 1000000.0)
                if (
                    abs(current_normalized - prior_normalized) > spike_threshold
                    and abs(next_normalized - prior_normalized) <= return_tolerance
                ):
                    effective_shares[index] = prior_normalized * factors[index]
            prior_share: float | None = None
            prior_factor: float | None = None
            for row, share, factor in zip(fund_rows, effective_shares, factors):
                when = datetime.strptime(
                    iso_date(row["observation_date"]), "%Y-%m-%d"
                ).date()
                if prior_share is None or prior_factor is None:
                    prior_share, prior_factor = share, factor
                    continue
                if factor <= 0 or prior_factor <= 0 or row["nav_unit"] is None:
                    prior_share, prior_factor = share, factor
                    continue
                share_change = share - prior_share * factor / prior_factor
                prior_share, prior_factor = share, factor
                if when < start:
                    continue
                nav = finite_float(row["nav_unit"])
                flow = share_change * nav / 100000000.0
                share_change_yi = share_change / 100000000.0
                when_key = when.isoformat()
                series["etf.net_share_all"][when_key] += share_change_yi
                if code.endswith(".SH"):
                    series["etf.net_share_sse"][when_key] += share_change_yi
                elif code.endswith(".SZ"):
                    series["etf.net_share_szse"][when_key] += share_change_yi
                series["etf.flow_total"][when_key] += flow
                broad = is_broad_etf(
                    str(row["index_name"] or ""), str(row["fund_name"] or "")
                )
                target = "etf.flow_broad" if broad else "etf.flow_other"
                series[target][when_key] += flow
                week_start = when - timedelta(days=when.weekday())
                dates_by_week[week_start].add(when)
                sector = str(row["asset_class"] or "其他ETF").strip()
                sector_by_week[week_start][sector] += flow
                industry = classify_etf_industry(
                    str(row["index_name"] or ""), str(row["fund_name"] or "")
                )
                if industry:
                    industry_by_week[week_start][industry] += flow
        refreshed: list[str] = []
        for series_id, values in series.items():
            if series_id not in active:
                continue
            item = contracts[series_id]
            cache.replace_series(
                series_id, values, item.preferred_provider, item.locator, run_id
            )
            refreshed.append(series_id)
        complete_weeks = [
            week_start
            for week_start, observed_dates in dates_by_week.items()
            if len(observed_dates) >= 3 and max(observed_dates).weekday() >= 4
        ]
        if not complete_weeks:
            raise ValueError("ETF refresh found no complete trading week")
        latest_week = max(complete_weeks)
        as_of = max(dates_by_week[latest_week]).isoformat()
        if "etf.flow_sector" in active:
            item = contracts["etf.flow_sector"]
            cache.replace_categories(
                item.series_id,
                as_of,
                sector_by_week[latest_week],
                item.preferred_provider,
                item.locator,
                run_id,
            )
            refreshed.append(item.series_id)
        if "etf.flow_industry" in active:
            item = contracts["etf.flow_industry"]
            cache.replace_categories(
                item.series_id,
                as_of,
                industry_by_week[latest_week],
                item.preferred_provider,
                item.locator,
                run_id,
            )
            refreshed.append(item.series_id)
        return {"refreshed": refreshed, "errors": {}}
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"etf": f"{type(error).__name__}: {error}"},
        }


def refresh_margin_industry(
    cache: Any,
    run_id: str,
    selected: set[str] | None,
    client_factory: Callable[[], Any],
    contracts: Mapping[str, Any],
    iso_date: Callable[[Any], str],
    finite_float: Callable[[Any], float],
) -> dict[str, Any]:
    series_id = "margin.industry_net_buy"
    if selected is not None and series_id not in selected:
        return {"refreshed": [], "errors": {}}
    client = client_factory()
    try:
        latest_result = client.query(
            "SELECT MAX(TRADE_DT) AS latest_date "
            "FROM wande.dbo.ASHAREMARGINTRADE"
        ).as_dicts()
        latest = datetime.strptime(
            iso_date(latest_result[0]["latest_date"]), "%Y-%m-%d"
        ).date()
        target_friday = (
            latest
            if latest.weekday() == 4
            else latest - timedelta(days=latest.weekday() + 3)
        )
        week_start = target_friday - timedelta(days=4)
        sql = f"""
            SELECT I.INDUSTRIESNAME AS category,
                   SUM(M.S_MARGIN_PURCHWITHBORROWMONEY
                       -M.S_MARGIN_REPAYMENTTOBROKER)/100000000.0 AS value
            FROM wande.dbo.ASHAREMARGINTRADE M
            JOIN wande.dbo.ASHARESWINDUSTRIESCLASS C
              ON M.S_INFO_WINDCODE=C.S_INFO_WINDCODE
             AND M.TRADE_DT>=C.ENTRY_DT
             AND (C.REMOVE_DT IS NULL OR M.TRADE_DT<=C.REMOVE_DT)
            JOIN wande.dbo.ASHAREINDUSTRIESCODE I
              ON I.INDUSTRIESCODE=
                 LEFT(C.SW_IND_CODE,4)+REPLICATE('0',12)
             AND I.LEVELNUM=2
             AND I.USED=1
            WHERE M.TRADE_DT>='{week_start.strftime("%Y%m%d")}'
              AND M.TRADE_DT<='{target_friday.strftime("%Y%m%d")}'
              AND M.S_MARGIN_PURCHWITHBORROWMONEY IS NOT NULL
              AND M.S_MARGIN_REPAYMENTTOBROKER IS NOT NULL
            GROUP BY I.INDUSTRIESNAME
            ORDER BY I.INDUSTRIESNAME
        """
        rows = client.query(sql).as_dicts()
        values = {
            str(row["category"]): finite_float(row["value"])
            for row in rows
            if row["category"] and row["value"] is not None
        }
        item = contracts[series_id]
        cache.replace_categories(
            series_id,
            target_friday.isoformat(),
            values,
            item.preferred_provider,
            item.locator,
            run_id,
        )
        return {"refreshed": [series_id], "errors": {}}
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"margin_industry": f"{type(error).__name__}: {error}"},
        }


def refresh_private_indices(
    cache: Any,
    run_id: str,
    start: date,
    selected: set[str] | None,
    client_factory: Callable[[], Any],
    contracts: Mapping[str, Any],
    finite_float: Callable[[Any], float],
) -> dict[str, Any]:
    target_ids = {
        "private.enhanced_300",
        "private.enhanced_500",
        "private.enhanced_1000",
        "private.neutral",
        "private.cta",
        "private.arbitrage",
    }
    active = target_ids if selected is None else target_ids & selected
    if not active:
        return {"refreshed": [], "errors": {}}
    completed_month_end = date.today().replace(day=1) - timedelta(days=1)
    if completed_month_end < start:
        return {
            "refreshed": [],
            "errors": {"private_indices": "no completed month in requested interval"},
        }
    previous_month_start = (
        date(start.year - 1, 12, 1)
        if start.month == 1
        else date(start.year, start.month - 1, 1)
    )
    query_start = previous_month_start.strftime("%Y%m%d")
    query_end = completed_month_end.strftime("%Y%m%d")
    sql = f"""
        WITH members AS (
            SELECT DISTINCT
                   CASE
                       WHEN D.F_INFO_NAME LIKE N'%沪深300%'
                           THEN 'private.enhanced_300'
                       WHEN D.F_INFO_NAME LIKE N'%中证500%'
                           THEN 'private.enhanced_500'
                       WHEN D.F_INFO_NAME LIKE N'%中证1000%'
                           THEN 'private.enhanced_1000'
                   END AS strategy,
                   D.F_INFO_WINDCODE AS code,
                   D.F_INFO_SETUPDATE AS entry_dt,
                   D.F_INFO_MATURITYDATE AS exit_dt
            FROM wande.dbo.CHINAHEDGEFUNDDESCRIPTION D
            WHERE (D.F_INFO_NAME LIKE N'%指数增强%'
                   OR D.F_INFO_NAME LIKE N'%指增%')
              AND (D.F_INFO_NAME LIKE N'%沪深300%'
                   OR D.F_INFO_NAME LIKE N'%中证500%'
                   OR D.F_INFO_NAME LIKE N'%中证1000%')
              AND D.F_INFO_NAME NOT LIKE N'%对冲%'
              AND D.F_INFO_NAME NOT LIKE N'%中性%'
              AND UPPER(D.F_INFO_NAME) NOT LIKE '%FOF%'
            UNION ALL
            SELECT DISTINCT
                   CASE
                       WHEN S.S_INFO_SECTOR LIKE '2001100200%'
                           THEN 'private.neutral'
                       WHEN S.S_INFO_SECTOR LIKE '2001100700%'
                           THEN 'private.cta'
                       WHEN S.S_INFO_SECTOR LIKE '2001100500%'
                           THEN 'private.arbitrage'
                   END AS strategy,
                   S.F_INFO_WINDCODE AS code,
                   S.S_INFO_SECTORENTRYDT AS entry_dt,
                   S.S_INFO_SECTOREXITDT AS exit_dt
            FROM wande.dbo.CHINAHEDGEFUNDSECTOR S
            WHERE S.S_INFO_SECTOR LIKE '2001100200%'
               OR S.S_INFO_SECTOR LIKE '2001100700%'
               OR S.S_INFO_SECTOR LIKE '2001100500%'
        ),
        nav_points AS (
            SELECT DISTINCT M.strategy,
                   M.code,
                   N.PRICE_DATE,
                   COALESCE(
                       NULLIF(N.F_NAV_DIVACCUMULATED,0),
                       NULLIF(N.F_NAV_ACCUMULATED,0),
                       CASE
                           WHEN N.F_NAV_UNIT>0 AND N.F_NAV_ADJFACTOR>0
                               THEN N.F_NAV_UNIT*N.F_NAV_ADJFACTOR
                       END
                   ) AS nav
            FROM members M
            JOIN wande.dbo.CHINAHEDGEFUNDNAV N
              ON N.F_INFO_WINDCODE=M.code
            WHERE N.PRICE_DATE>='{query_start}'
              AND N.PRICE_DATE<='{query_end}'
              AND (M.entry_dt IS NULL OR M.entry_dt=''
                   OR M.entry_dt='19000101' OR N.PRICE_DATE>=M.entry_dt)
              AND (M.exit_dt IS NULL OR M.exit_dt=''
                   OR N.PRICE_DATE<=M.exit_dt)
        ),
        ranked AS (
            SELECT strategy,
                   code,
                   LEFT(PRICE_DATE,6) AS month_key,
                   PRICE_DATE,
                   nav,
                   ROW_NUMBER() OVER (
                       PARTITION BY strategy,code,LEFT(PRICE_DATE,6)
                       ORDER BY PRICE_DATE DESC
                   ) AS row_number
            FROM nav_points
            WHERE nav>0
        ),
        monthly AS (
            SELECT strategy,code,month_key,PRICE_DATE,nav
            FROM ranked
            WHERE row_number=1
        ),
        lagged AS (
            SELECT strategy,
                   code,
                   month_key,
                   PRICE_DATE,
                   nav,
                   LAG(month_key) OVER (
                       PARTITION BY strategy,code ORDER BY month_key
                   ) AS previous_month,
                   LAG(nav) OVER (
                       PARTITION BY strategy,code ORDER BY month_key
                   ) AS previous_nav
            FROM monthly
        ),
        returns AS (
            SELECT strategy,
                   month_key,
                   PRICE_DATE,
                   nav/previous_nav-1.0 AS monthly_return
            FROM lagged
            WHERE previous_nav>0
              AND DATEDIFF(
                    month,
                    CONVERT(date,previous_month+'01',112),
                    CONVERT(date,month_key+'01',112)
                  )=1
        )
        SELECT strategy,
               month_key,
               AVG(monthly_return) AS mean_return,
               COUNT(*) AS sample_count
        FROM returns
        WHERE monthly_return>-0.90 AND monthly_return<3.0
        GROUP BY strategy,month_key
        HAVING COUNT(*)>=10
        ORDER BY strategy,month_key
    """
    try:
        rows = client_factory().query(sql).as_dicts()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["strategy"])].append(row)
        refreshed: list[str] = []
        quality: dict[str, Any] = {}
        for series_id in sorted(active):
            series_rows = sorted(grouped.get(series_id, []), key=lambda row: row["month_key"])
            contiguous_start = 0
            for index in range(1, len(series_rows)):
                previous = str(series_rows[index - 1]["month_key"])
                current = str(series_rows[index]["month_key"])
                previous_ordinal = int(previous[:4]) * 12 + int(previous[4:6])
                current_ordinal = int(current[:4]) * 12 + int(current[4:6])
                if current_ordinal - previous_ordinal != 1:
                    contiguous_start = index
            series_rows = series_rows[contiguous_start:]
            cumulative: list[tuple[str, float, int]] = []
            level = 1.0
            for row in series_rows:
                monthly_return = finite_float(row["mean_return"])
                level *= 1.0 + monthly_return
                month_text = str(row["month_key"])
                year, month = int(month_text[:4]), int(month_text[4:6])
                when = date(year, month, calendar.monthrange(year, month)[1])
                if when >= start:
                    cumulative.append(
                        (when.isoformat(), level, int(row["sample_count"]))
                    )
            if len(cumulative) < 2:
                raise ValueError(f"{series_id}: fewer than two aggregated months")
            base_level = cumulative[0][1]
            values = {
                when: raw_level / base_level
                for when, raw_level, _ in cumulative
            }
            item = contracts[series_id]
            cache.replace_series(
                series_id,
                values,
                item.preferred_provider,
                item.locator,
                run_id,
            )
            refreshed.append(series_id)
            quality[series_id] = {
                "observations": len(cumulative),
                "start": cumulative[0][0],
                "end": cumulative[-1][0],
                "latest_sample_count": cumulative[-1][2],
                "minimum_sample_count": 10,
                "individual_return_filter": "(-90%, 300%)",
                "contiguous_monthly_suffix": True,
            }
        return {"refreshed": refreshed, "errors": {}, "quality": quality}
    except Exception as error:
        return {
            "refreshed": [],
            "errors": {"private_indices": f"{type(error).__name__}: {error}"},
        }