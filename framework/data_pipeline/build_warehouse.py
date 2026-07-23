import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_DATE = "20120101"
END_DATE = "20260630"
SPLITS = {
    "train": ("20120101", "20201231", "parameter learning"),
    "valid": ("20210101", "20221231", "model selection"),
    "test": ("20230101", "20260630", "final out-of-sample evaluation"),
    "full": ("20120101", "20260630", "full-sample display only"),
}
INDEX_UNIVERSES = {
    "CSI800_ENH": "000906.SH",
    "CSI2000_ENH": "932000.CSI",
}


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def execute_script(conn, path):
    conn.executescript(Path(path).read_text(encoding="utf-8"))


def drop_load_indexes(conn):
    for name in [
        "idx_stock_ohlcv_code_date",
        "idx_stock_val_code_date",
        "idx_fin_visible_code_date",
        "idx_etf_code_date",
        "idx_index_constituent_lookup",
        "idx_news_event_date_source",
    ]:
        conn.execute(f"drop index if exists {name}")
    conn.commit()


def create_load_indexes(conn):
    conn.executescript(
        """
        create index if not exists idx_stock_ohlcv_code_date on stock_ohlcv_daily(ts_code, trade_date);
        create index if not exists idx_stock_val_code_date on stock_valuation_daily(ts_code, trade_date);
        create index if not exists idx_fin_visible_code_date on financial_report_visible(ts_code, visible_date);
        create index if not exists idx_etf_code_date on etf_ohlcv_daily(ts_code, trade_date);
        create index if not exists idx_index_constituent_lookup on index_constituent_period(universe, con_code, trade_date);
        create index if not exists idx_news_event_date_source on news_event_daily(publish_date, source_site);
        """
    )
    conn.commit()


def table_exists(conn, db_name, table):
    return conn.execute(
        f"select 1 from {db_name}.sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone() is not None


def cols(conn, db_name, table):
    return [row[1] for row in conn.execute(f'pragma {db_name}.table_info("{table}")').fetchall()]


def log(conn, run_id, step, status, message, started_at):
    conn.execute(
        """
        insert or replace into update_log(run_id, step, status, message, started_at, ended_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (run_id, step, status, message, started_at, now()),
    )
    conn.commit()


def manifest(conn, source_path, source_table, target_table, rows, min_date, max_date, frequency, status, message=""):
    conn.execute(
        """
        insert or replace into source_manifest
        (source_name, source_path, source_table, target_table, start_date, end_date, rows_loaded, min_date, max_date,
         frequency, update_mode, quota_policy, status, message, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "local_subject_sqlite",
            source_path,
            source_table,
            target_table,
            START_DATE,
            END_DATE,
            rows,
            min_date,
            max_date,
            frequency,
            "incremental_daily_supported",
            "local_db_first_no_paid_api_bulk_call",
            status,
            message,
            now(),
        ),
    )
    conn.commit()


def quality(conn, table, field, check_type, status, value=None, message=""):
    check_id = f"{table}:{field or '*'}:{check_type}"
    conn.execute(
        """
        insert or replace into data_quality_check
        (check_id, table_name, field_name, check_type, status, metric_value, message, checked_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (check_id, table, field, check_type, status, value, message, now()),
    )
    conn.commit()


def insert_splits(conn):
    for name, (start, end, purpose) in SPLITS.items():
        conn.execute(
            "insert or replace into split_definition(split_name, start_date, end_date, purpose) values (?, ?, ?, ?)",
            (name, start, end, purpose),
        )
    conn.commit()


def copy_table(conn, source_path, source_table, target_table, insert_sql, date_expr, frequency):
    started = now()
    conn.execute(f"delete from {target_table}")
    conn.execute(insert_sql)
    conn.commit()
    rows = conn.execute(f"select count(*) from {target_table}").fetchone()[0]
    min_date, max_date = conn.execute(f"select min({date_expr}), max({date_expr}) from {target_table}").fetchone()
    status = "ready" if rows else "blocked"
    manifest(conn, source_path, source_table, target_table, rows, min_date, max_date, frequency, status)
    log(conn, "warehouse_build", f"copy_{target_table}", status, f"{rows} rows loaded", started)


def classify_etf(name):
    text = name or ""
    if any(k in text for k in ["债", "国债", "政金", "信用", "短融", "货币", "现金", "城投"]):
        return "bond_cash"
    if any(k in text for k in ["黄金", "商品", "有色", "能源", "油气", "煤炭", "豆粕", "稀土"]):
        return "commodity"
    if any(k in text for k in ["纳指", "纳斯达克", "标普", "日经", "德国", "法国", "恒生", "港股", "香港", "QDII"]):
        return "cross_border"
    if any(k in text for k in ["红利", "价值", "成长", "低波", "质量", "央企"]):
        return "style_equity"
    return "equity"


def build_etf_master(conn):
    conn.execute("delete from etf_master")
    rows = conn.execute(
        """
        select ts_code, max(fund_name) as fund_name
        from etf_ohlcv_daily
        group by ts_code
        """
    ).fetchall()
    conn.executemany(
        "insert or replace into etf_master(ts_code, fund_name, asset_class, market, source) values (?, ?, ?, ?, ?)",
        [(r[0], r[1], classify_etf(r[1]), None, "derived_from_fund_name") for r in rows],
    )
    conn.commit()


def load_broker_reports(conn, project_root):
    conn.execute("delete from broker_report_index")
    index_path = Path(project_root) / "report" / "specs" / "recent_ai_quant_report_index.csv"
    rows = []
    if index_path.exists():
        with index_path.open("r", encoding="utf-8-sig", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), 1):
                title = row.get("title", "")
                tag = []
                for key, label in [
                    ("AI", "AI"),
                    ("Agent", "agent"),
                    ("因子", "factor"),
                    ("轮动", "rotation"),
                    ("ETF", "etf"),
                    ("资产配置", "allocation"),
                    ("机器学习", "ml"),
                    ("深度学习", "dl"),
                    ("OpenClaw", "agent"),
                ]:
                    if key in title:
                        tag.append(label)
                if tag:
                    rows.append(
                        (
                            f"recent_{i:05d}",
                            row.get("date", ""),
                            row.get("org", ""),
                            title,
                            row.get("researcher", ""),
                            row.get("link", ""),
                            ",".join(sorted(set(tag))),
                            "recent_ai_quant_report_index",
                        )
                    )
    conn.executemany(
        """
        insert or replace into broker_report_index
        (report_id, report_date, org, title, researcher, link, model_tag, source)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    quality(conn, "broker_report_index", "title", "evidence_count", "ready" if rows else "blocked", len(rows))


def build_universes(conn):
    started = now()
    conn.execute("delete from index_constituent_period")
    # ALL_A is a dynamic tradable universe derived from daily bars and security status.
    conn.execute(
        """
        insert into index_constituent_period(universe, index_code, trade_date, con_code, weight, source, status)
        select 'ALL_A', null, o.trade_date, o.ts_code, null, 'stock_ohlcv_daily_tradable', 'ready'
        from stock_ohlcv_daily o
        left join security_master m on o.ts_code = m.ts_code
        where o.trade_date between ? and ?
          and o.close is not null and o.close > 0
          and o.qfq_close is not null and o.qfq_close > 0
          and (o.stock_name is null or o.stock_name not like '%ST%')
          and (m.list_status is null or m.list_status = 'L')
          and (m.list_date is null or m.list_date <= o.trade_date)
          and (m.delist_date is null or m.delist_date = '' or m.delist_date > o.trade_date)
        """,
        (START_DATE, END_DATE),
    )
    for universe, index_code in INDEX_UNIVERSES.items():
        count = conn.execute(
            """
            select count(*), min(trade_date), max(trade_date)
            from src.index_member_weight
            where index_code = ? and trade_date between ? and ?
            """,
            (index_code, START_DATE, END_DATE),
        ).fetchone()
        rows, min_date, max_date = count
        if rows:
            conn.execute(
                """
                insert into index_constituent_period(universe, index_code, trade_date, con_code, weight, source, status)
                select ?, index_code, trade_date, con_code, weight, 'index_member_weight', 'ready'
                from src.index_member_weight
                where index_code = ? and trade_date between ? and ?
                """,
                (universe, index_code, START_DATE, END_DATE),
            )
            quality(conn, "index_constituent_period", universe, "coverage", "warning", rows, f"{min_date} to {max_date}; not full 2012-2026 if min_date > {START_DATE}")
        else:
            quality(conn, "index_constituent_period", universe, "coverage", "blocked", 0, f"missing exact dynamic constituents for {index_code}")
    conn.commit()
    total = conn.execute("select count(*) from index_constituent_period").fetchone()[0]
    log(conn, "warehouse_build", "build_universes", "ready", f"{total} membership rows", started)


def run_quality_checks(conn):
    required = {
        "stock_ohlcv_daily": ["trade_date", "ts_code", "qfq_close", "amount"],
        "stock_valuation_daily": ["pb", "pe_ttm", "total_mv", "turnover_rate"],
        "financial_report_visible": ["visible_date", "roe", "netprofit_yoy", "debt_to_assets"],
        "sw_l1_industry_daily": ["industry_name"],
        "etf_ohlcv_daily": ["close", "fund_type"],
        "macro_monthly": ["pmi_manufacturing", "cpi_national_yoy", "ppi_yoy", "m1_yoy", "m2_yoy"],
    }
    for table, fields in required.items():
        total = conn.execute(f"select count(*) from {table}").fetchone()[0]
        quality(conn, table, None, "row_count", "ready" if total else "blocked", total)
        for field in fields:
            if field not in cols(conn, "main", table):
                quality(conn, table, field, "field_exists", "blocked", None, "field missing")
                continue
            missing = conn.execute(f"select count(*) from {table} where {field} is null").fetchone()[0]
            ratio = missing / total if total else 1.0
            status = "ready" if ratio < 0.35 else "warning"
            quality(conn, table, field, "missing_ratio", status, ratio)


def build(source_db, out_db, project_root):
    source_db = str(Path(source_db))
    out_db = Path(out_db)
    out_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(out_db)
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma synchronous=off")
    conn.execute("pragma temp_store=memory")
    conn.execute("pragma cache_size=-800000")
    execute_script(conn, Path(project_root) / "framework" / "data_pipeline" / "schema_v2.sql")
    drop_load_indexes(conn)
    conn.execute("attach database ? as src", (source_db,))
    insert_splits(conn)

    if table_exists(conn, "src", "calendar_cn"):
        copy_table(
            conn,
            source_db,
            "calendar_cn",
            "trade_calendar",
            """
            insert into trade_calendar(trade_date, is_trade_day, is_month_last_trade, source)
            select calendar_date, is_trade_day, coalesce(is_month_last_trade, 0), source
            from src.calendar_cn
            where calendar_date between '20120101' and '20260630' and exchange in ('SSE', 'SZSE', 'CFFEX')
            group by calendar_date
            """,
            "trade_date",
            "daily",
        )
    else:
        conn.execute(
            """
            insert into trade_calendar(trade_date, is_trade_day, is_month_last_trade, source)
            select distinct trade_date, 1, 0, 'stock_ohlcv_daily'
            from src.stock_market_daily
            where trade_date between ? and ?
            """,
            (START_DATE, END_DATE),
        )
        conn.commit()

    copy_table(
        conn,
        source_db,
        "stock_master",
        "security_master",
        """
        insert into security_master
        select ts_code, symbol, stock_name, exchange, market, board_name, list_status, list_date, delist_date, is_hs, source
        from src.stock_master
        """,
        "coalesce(list_date, '')",
        "static",
    )

    copy_table(
        conn,
        source_db,
        "stock_market_daily",
        "stock_ohlcv_daily",
        """
        insert into stock_ohlcv_daily
        select trade_date, ts_code, stock_name, open, high, low, close, coalesce(qfq_close, close), pre_close,
               pct_chg, vol, amount, up_limit, down_limit, suspend_timing
        from src.stock_market_daily
        where trade_date between '20120101' and '20260630'
        """,
        "trade_date",
        "daily",
    )

    copy_table(
        conn,
        source_db,
        "stock_market_daily",
        "stock_valuation_daily",
        """
        insert into stock_valuation_daily
        select trade_date, ts_code, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv, turnover_rate, turnover_rate_f, volume_ratio
        from src.stock_market_daily
        where trade_date between '20120101' and '20260630'
        """,
        "trade_date",
        "daily",
    )

    copy_table(
        conn,
        source_db,
        "stock_market_daily",
        "stock_moneyflow_daily",
        """
        insert into stock_moneyflow_daily
        select trade_date, ts_code, net_mf_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount
        from src.stock_market_daily
        where trade_date between '20120101' and '20260630'
        """,
        "trade_date",
        "daily",
    )

    copy_table(
        conn,
        source_db,
        "stock_financial_report",
        "financial_report_visible",
        """
        insert into financial_report_visible
        select ts_code, visible_date, end_date, max(report_type),
               max(total_revenue), max(n_income_attr_p), max(gross_margin), max(netprofit_margin), max(roe), max(roa),
               max(debt_to_assets), max(current_ratio), max(assets_turn), max(op_yoy), max(tr_yoy), max(netprofit_yoy)
        from (
          select ts_code, coalesce(f_ann_date, ann_date, end_date) as visible_date, end_date, report_type,
                 total_revenue, n_income_attr_p, gross_margin, netprofit_margin, roe, roa, debt_to_assets,
                 current_ratio, assets_turn, op_yoy, tr_yoy, netprofit_yoy
          from src.stock_financial_report
          where coalesce(f_ann_date, ann_date, end_date) between '20100101' and '20260630'
            and coalesce(f_ann_date, ann_date, end_date) is not null
        )
        group by ts_code, visible_date, end_date
        """,
        "visible_date",
        "quarterly_visible",
    )

    copy_table(
        conn,
        source_db,
        "stock_industry_history",
        "sw_l1_industry_daily",
        """
        insert into sw_l1_industry_daily
        select ts_code, start_date, max(out_date), industry_code, max(industry_name), max(source)
        from (
          select ts_code, coalesce(in_date, '19000101') as start_date, out_date, l1_code as industry_code,
                 coalesce(l1_name, '未分组') as industry_name, source
          from src.stock_industry_history
          where industry_standard like 'SW%' or industry_standard like '%申万%' or industry_standard is null
        )
        group by ts_code, start_date, industry_code
        """,
        "start_date",
        "event_period",
    )

    copy_table(
        conn,
        source_db,
        "fund_daily",
        "etf_ohlcv_daily",
        """
        insert into etf_ohlcv_daily
        select trade_date, ts_code, fund_name, open, high, low, close, pct_chg, vol, amount, fund_type
        from src.fund_daily
        where trade_date between '20120101' and '20260630'
          and fund_type = 'ETF'
          and close is not null and close > 0
        """,
        "trade_date",
        "daily",
    )
    build_etf_master(conn)

    copy_table(
        conn,
        source_db,
        "macro_monthly_cn",
        "macro_monthly",
        """
        insert into macro_monthly
        select month, pmi_manufacturing, pmi_non_manufacturing, pmi_composite, cpi_national_yoy, ppi_yoy,
               m1_yoy, m2_yoy, sf_inc_month, sf_stock_endval, source
        from src.macro_monthly_cn
        where month between '201201' and '202606'
        """,
        "month",
        "monthly",
    )

    if table_exists(conn, "src", "news_stream"):
        copy_table(
            conn,
            source_db,
            "news_stream",
            "news_event_daily",
            """
            insert into news_event_daily
            select news_id, publish_date, headline, category, subject_type, subject_code, source_site, source_url,
                   case
                     when headline like '%AI%' or headline like '%人工智能%' or headline like '%Agent%' then 'AI'
                     when headline like '%政策%' then 'policy'
                     when headline like '%业绩%' then 'earnings'
                     when headline like '%并购%' then 'ma'
                     else 'general'
                   end as event_tag
            from src.news_stream
            where publish_date between '20120101' and '20260630'
            """,
            "publish_date",
            "event",
        )

    # LHB source table is optional in the current subject database.
    lhb_candidates = [t for t in ["lhb_daily", "stock_lhb_detail", "top_list"] if table_exists(conn, "src", t)]
    if not lhb_candidates:
        quality(conn, "lhb_daily", None, "source_exists", "blocked", 0, "No LHB table found in source DB; must add Tushare/Wind/CSMAR connector later.")
        manifest(conn, source_db, "missing_lhb_source", "lhb_daily", 0, None, None, "daily", "blocked", "No LHB source table found")

    load_broker_reports(conn, project_root)
    build_universes(conn)
    run_quality_checks(conn)
    create_load_indexes(conn)
    conn.execute("detach database src")
    conn.execute("pragma optimize")
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", default=os.environ.get("SOURCE_DB", ""), help="Source database path; may also be set via SOURCE_DB.")
    parser.add_argument("--out-db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    args = parser.parse_args()
    if not args.source_db:
        parser.error("--source-db is required or set SOURCE_DB.")
    build(args.source_db, args.out_db, args.project_root)
    print(json.dumps({"status": "ok", "database": args.out_db, "start": START_DATE, "end": END_DATE}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
