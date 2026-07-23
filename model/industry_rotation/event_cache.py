"""Release wrapper with one-pass PIT event extraction and robust keywords."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

import engine as worker


ROBUST_EVENTS = {
    "农林牧渔": [("生猪供给事件", ["生猪"]), ("禽链补栏事件", ["鸡苗", "肉鸡"]), ("种业审定推广事件", ["种业", "种子"])],
    "电子": [("半导体扩产事件", ["半导体"]), ("集成电路订单事件", ["集成电路"]), ("消费电子新品事件", ["手机"]), ("面板稼动价格事件", ["面板"]), ("芯片供需事件", ["芯片"])],
    "家用电器": [("空调排产渠道事件", ["空调"]), ("冰箱冷链产品事件", ["冰箱"]), ("洗衣机产品事件", ["洗衣机"]), ("家电换新出口事件", ["家电"])],
    "轻工制造": [("家具订单事件", ["家具"]), ("造纸供需事件", ["造纸", "纸价"]), ("包装订单事件", ["包装"]), ("玩具出口事件", ["玩具"]), ("文具渠道事件", ["文具", "办公用品"])],
    "医药生物": [("新药审批事件", ["新药"]), ("医疗器械注册事件", ["医疗器械"]), ("药械集采事件", ["集采"]), ("疫苗批签发事件", ["疫苗"]), ("临床试验事件", ["临床试验"])],
    "综合": [("多元业务合同事件", ["合同"]), ("跨行业收购事件", ["收购"]), ("资产重组事件", ["重组"]), ("新业务落地事件", ["新业务"]), ("多元项目投产事件", ["项目"]), ("政府项目中标事件", ["中标"]), ("产业园运营事件", ["产业园"]), ("混合业务订单事件", ["订单"])],
    "建筑材料": [("水泥错峰供给事件", ["水泥"]), ("玻璃冷修复产事件", ["玻璃"])],
    "建筑装饰": [("工程项目中标事件", ["工程"]), ("城市更新订单事件", ["城市更新"]), ("装配式建筑事件", ["装配式"]), ("海外工程事件", ["海外项目", "境外项目"])],
    "电力设备": [("光伏招标排产事件", ["光伏"]), ("风电招标事件", ["风电"]), ("储能订单事件", ["储能"]), ("锂电扩产事件", ["锂电"]), ("电网设备中标事件", ["电网"])],
    "国防军工": [("军品合同事件", ["军品", "军工"]), ("航空装备交付事件", ["航空"]), ("卫星火箭发射事件", ["卫星", "火箭"]), ("舰船装备事件", ["舰船", "船舶"]), ("军工重组事件", ["重组"]), ("雷达导弹电子事件", ["雷达", "导弹"])],
    "计算机": [("算力服务器项目事件", ["算力", "服务器"]), ("政务信息化事件", ["信息化"]), ("信创安全订单事件", ["信创"]), ("人工智能项目事件", ["人工智能", "大模型"]), ("国产软件事件", ["软件"]), ("工业软件实施事件", ["工业软件"]), ("数据中心项目事件", ["数据中心"])],
    "传媒": [("影视项目事件", ["影视", "电影"]), ("游戏版号上线事件", ["游戏"]), ("广告投放订单事件", ["广告"])],
    "非银金融": [("券商承销业务事件", ["券商", "承销"]), ("权益基金发行事件", ["基金"]), ("保险产品赔付事件", ["保险"]), ("期货品种成交事件", ["期货"]), ("资管产品备案事件", ["资管"])],
    "煤炭": [("煤矿复产停产事件", ["煤矿"]), ("煤炭长协事件", ["煤炭"]), ("煤炭保供事件", ["保供"]), ("焦煤进口事件", ["焦煤"]), ("矿山安全检查事件", ["安全检查"])],
    "石油石化": [("炼化装置检修事件", ["炼化"]), ("油气增储上产事件", ["油气"])],
    "环保": [("污水处理项目事件", ["污水"]), ("垃圾焚烧运营事件", ["垃圾"]), ("烟气脱硫脱硝事件", ["脱硫", "脱硝"]), ("环保工程中标事件", ["环保"]), ("再生资源事件", ["再生资源"]), ("碳监测管理事件", ["碳"]), ("固废危废处置事件", ["固废", "危废"]), ("环境监测设备事件", ["环境监测"])],
    "美容护理": [("化妆品备案事件", ["化妆品"]), ("美妆新品事件", ["美妆"]), ("美妆直播电商事件", ["直播"]), ("美妆门店扩张事件", ["门店"]), ("医疗美容监管事件", ["医美", "医疗美容"]), ("护肤原料研发事件", ["护肤"])],
    "机械设备": [("工业机器人订单事件", ["机器人"]), ("机床订单交付事件", ["机床"]), ("挖掘机销量出口事件", ["挖掘机"]), ("电梯订单中标事件", ["电梯"]), ("设备更新改造事件", ["设备更新"]), ("专用设备出口事件", ["设备出口"])],
}


_CACHE: pd.DataFrame | None = None
CACHE_PATH = worker.CACHE_DIR.parent / "event_rows.pkl"


def _prefetch() -> pd.DataFrame:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if CACHE_PATH.exists():
        _CACHE = pd.read_pickle(CACHE_PATH)
        return _CACHE
    industries = list(ROBUST_EVENTS)
    keywords = sorted({word for rows in ROBUST_EVENTS.values() for _, words in rows for word in words})
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
    params = industries + [f"%{word}%" for word in keywords] + [f"%{word}%" for word in worker.EXCLUDED_NEWS]
    uri = f"file:{worker.WAREHOUSE.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        _CACHE = pd.read_sql_query(sql, connection, params=params)
    _CACHE.to_pickle(CACHE_PATH)
    return _CACHE


def _event_rows(industry: str, blueprints):
    frame = _prefetch()
    return frame.loc[frame["industry_name"] == industry, ["publish_date", "news_id", "headline"]].copy()


def main() -> int:
    worker.EVENT_BLUEPRINTS = ROBUST_EVENTS
    worker._event_rows = _event_rows
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
