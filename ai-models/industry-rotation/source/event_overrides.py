"""Executable v4 release with cached core events and five small gap queries."""

from __future__ import annotations

import re
import sqlite3

import pandas as pd

import engine as worker
import event_cache as release


EVENTS = dict(release.ROBUST_EVENTS)
EVENTS.update({
    "纺织服饰": [("纺织服装出口订单事件", ["服装出口", "纺织订单"])],
    "公用事业": [("火电机组运营事件", ["火电"]), ("水电来水发电事件", ["水电"]), ("燃气供应负荷事件", ["燃气供应"]), ("水务供水运营事件", ["水务", "供水"])],
    "商贸零售": [("电商平台促销动销事件", ["电商", "促销"])],
    "社会服务": [("旅游客流恢复事件", ["旅游", "游客"]), ("酒店入住经营事件", ["酒店", "入住率"])],
    "汽车": [("新能源汽车交付事件", ["新能源汽车", "新能源车"]), ("汽车出口订单事件", ["汽车出口", "海外销量"])],
})
GAP_INDUSTRIES = {"纺织服饰", "公用事业", "商贸零售", "社会服务", "汽车"}


_original_select = worker._select_direct_contracts
_original_event_rows = worker._event_rows
_GAP_CACHE: pd.DataFrame | None = None
_GAP_CACHE_SIGNATURE: tuple[str, ...] | None = None


def _prefetch_gap_events() -> pd.DataFrame:
    """Read every dynamic gap industry in one PIT join without writing a cache."""
    global _GAP_CACHE, _GAP_CACHE_SIGNATURE
    industries = tuple(sorted(GAP_INDUSTRIES))
    if _GAP_CACHE is not None and _GAP_CACHE_SIGNATURE == industries:
        return _GAP_CACHE
    keywords = sorted({
        word
        for industry in industries
        for _, words in worker.EVENT_BLUEPRINTS.get(industry, [])
        for word in words
    })
    if not industries or not keywords:
        _GAP_CACHE = pd.DataFrame(columns=["industry_name", "publish_date", "news_id", "headline"])
        _GAP_CACHE_SIGNATURE = industries
        return _GAP_CACHE
    industry_marks = ",".join("?" for _ in industries)
    keyword_clause = " OR ".join("n.headline LIKE ?" for _ in keywords)
    exclusion_clause = " AND ".join("n.headline NOT LIKE ?" for _ in worker.EXCLUDED_NEWS)
    sql = f"""
        SELECT DISTINCT m.industry_name, n.publish_date, n.news_id, n.headline
        FROM news_event_daily n
        JOIN sw_l1_industry_daily m
          ON n.subject_code = m.ts_code
         AND n.publish_date >= m.start_date
         AND n.publish_date <= COALESCE(m.end_date, '99991231')
        WHERE n.subject_type = 'stock'
          AND m.industry_name IN ({industry_marks})
          AND n.publish_date BETWEEN '20120101' AND '20991231'
          AND ({keyword_clause})
          AND {exclusion_clause}
    """
    params = list(industries) + [f"%{word}%" for word in keywords] + [f"%{word}%" for word in worker.EXCLUDED_NEWS]
    uri = f"file:{worker.WAREHOUSE.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        _GAP_CACHE = pd.read_sql_query(sql, connection, params=params)
    _GAP_CACHE_SIGNATURE = industries
    return _GAP_CACHE


def _select(frames):
    selected = _original_select(frames)
    for items in selected.values():
        for item in items:
            item.name = re.sub(r"_+", " · ", item.observation_field.replace("原保险保费收入", "原保险保费规模"))
    return selected


def _event_rows(industry, blueprints):
    if industry in GAP_INDUSTRIES:
        frame = _prefetch_gap_events()
        return frame.loc[
            frame["industry_name"].eq(industry),
            ["publish_date", "news_id", "headline"],
        ].copy()
    return release._event_rows(industry, blueprints)


def main() -> int:
    worker.EVENT_BLUEPRINTS = EVENTS
    worker._event_rows = _event_rows
    worker._select_direct_contracts = _select
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
