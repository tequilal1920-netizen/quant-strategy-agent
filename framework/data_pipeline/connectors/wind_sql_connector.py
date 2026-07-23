import argparse
import calendar
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
import pyodbc


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
START_DATE = "20120101"
END_DATE = "20260630"
SERVER = os.environ.get("WIND_SQL_SERVER", "109.244.74.214,4108")
DRIVER = os.environ.get("WIND_SQL_DRIVER", "SQL Server")


def now_sql():
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")


def quote_openquery(sql):
    return sql.replace("'", "''")


def wind_conn():
    uid = os.environ.get("WIND_SQL_UID")
    pwd = os.environ.get("WIND_SQL_PWD")
    if not uid or not pwd:
        raise RuntimeError("WIND_SQL_UID and WIND_SQL_PWD must be set in the environment")
    return pyodbc.connect(f"DRIVER={DRIVER};SERVER={SERVER};UID={uid};PWD={pwd}")


def fetch_wind(sql):
    wrapped = f"SELECT * FROM OPENQUERY(WANDE, '{quote_openquery(sql)}')"
    conn = wind_conn()
    try:
        cur = conn.cursor()
        cur.execute(wrapped)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return pd.DataFrame([tuple(r) for r in rows], columns=columns)
    finally:
        conn.close()


def year_ranges(start=START_DATE, end=END_DATE):
    for year in range(int(start[:4]), int(end[:4]) + 1):
        a = max(start, f"{year}0101")
        b = min(end, f"{year}1231")
        yield year, a, b


def month_ranges(start=START_DATE, end=END_DATE):
    start_ts = pd.Timestamp(f"{start[:4]}-{start[4:6]}-{start[6:]}")
    end_ts = pd.Timestamp(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    cur = pd.Timestamp(year=start_ts.year, month=start_ts.month, day=1)
    while cur <= end_ts:
        last = calendar.monthrange(cur.year, cur.month)[1]
        a = max(start_ts, cur)
        b = min(end_ts, pd.Timestamp(year=cur.year, month=cur.month, day=last))
        yield f"{cur.year}{cur.month:02d}", a.strftime("%Y%m%d"), b.strftime("%Y%m%d")
        if cur.month == 12:
            cur = pd.Timestamp(year=cur.year + 1, month=1, day=1)
        else:
            cur = pd.Timestamp(year=cur.year, month=cur.month + 1, day=1)


def log(conn, step, status, message):
    conn.execute(
        """
        insert or replace into update_log(run_id, step, status, message, started_at, ended_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        ("wind_sql_connector", step, status, message, now_sql(), now_sql()),
    )
    conn.commit()


def manifest(conn, source_table, target_table, rows, min_date, max_date, status, message):
    conn.execute(
        """
        insert or replace into source_manifest
        (source_name, source_path, source_table, target_table, start_date, end_date, rows_loaded, min_date, max_date,
         frequency, update_mode, quota_policy, status, message, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "wind_sql_openquery",
            "env:WIND_SQL_UID/WIND_SQL_PWD",
            source_table,
            target_table,
            START_DATE,
            END_DATE,
            int(rows),
            min_date,
            max_date,
            "event_or_rebalance",
            "bounded_query",
            "exact_table_query_no_secret_storage",
            status,
            message,
            now_sql(),
        ),
    )
    conn.commit()


def insert_index_members(conn, universe, index_code, df, source):
    if df.empty:
        return {"rows": 0, "min_date": None, "max_date": None}
    lower = {c.lower(): c for c in df.columns}
    idx_col = lower.get("s_info_windcode") or lower.get("f_info_windcode")
    con_col = lower.get("s_con_windcode")
    in_col = lower.get("s_con_indate")
    out_col = lower.get("s_con_outdate")
    if not all([idx_col, con_col, in_col]):
        raise RuntimeError(f"Unexpected columns for index members: {list(df.columns)}")
    rows = []
    for r in df.to_dict("records"):
        con_code = str(r.get(con_col) or "").strip()
        in_date = str(r.get(in_col) or "").strip()
        out_date = str(r.get(out_col) or "").strip() if out_col else ""
        if not con_code or not in_date:
            continue
        start = max(in_date, START_DATE)
        end = min(out_date if out_date and out_date != "None" else END_DATE, END_DATE)
        rows.append((universe, index_code, start, con_code, None, source, "ready"))
        if end and end < END_DATE:
            rows.append((universe, index_code, end, con_code, None, source + ":out", "inactive"))
    conn.executemany(
        """
        insert or replace into index_constituent_period
        (universe, index_code, trade_date, con_code, weight, source, status)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    dates = [r[2] for r in rows]
    return {"rows": len(rows), "min_date": min(dates) if dates else None, "max_date": max(dates) if dates else None}


def fill_index_members(conn):
    configs = [
        ("CSI800_ENH", "000906.SH"),
        ("CSI2000_ENH", "932000.CSI"),
    ]
    result = {}
    for universe, index_code in configs:
        sql = f"""
        SELECT S_INFO_WINDCODE, S_CON_WINDCODE, S_CON_INDATE, S_CON_OUTDATE
        FROM wande.dbo.AINDEXMEMBERS
        WHERE S_INFO_WINDCODE='{index_code}'
          AND S_CON_INDATE <= '{END_DATE}'
          AND (S_CON_OUTDATE IS NULL OR S_CON_OUTDATE >= '{START_DATE}')
        """
        df = fetch_wind(sql)
        summary = insert_index_members(conn, universe, index_code, df, "wind:AIndexMembers")
        status = "ready" if summary["rows"] else "blocked"
        message = f"{index_code}; rows={summary['rows']}; {summary['min_date']} to {summary['max_date']}"
        manifest(conn, f"wande.dbo.AINDEXMEMBERS:{index_code}", "index_constituent_period", summary["rows"], summary["min_date"], summary["max_date"], status, message)
        log(conn, f"index_members_{universe}", status, message)
        result[universe] = summary
    return result


def fill_csi800_weight(conn):
    total_rows = 0
    min_date = None
    max_date = None
    for _, start, end in month_ranges():
        existing = conn.execute(
            """
            select count(*) from news_event_daily
            where news_id like 'wind_major_event:%'
              and publish_date between ? and ?
            """,
            (start, end),
        ).fetchone()[0]
        if existing:
            total_rows += int(existing)
            min_date = min(min_date or start, start)
            max_date = max(max_date or end, end)
            continue
        sql = f"""
        SELECT TRADE_DT, S_INFO_WINDCODE, S_CON_WINDCODE, WEIGHT
        FROM wande.dbo.AINDEXCSI800WEIGHT
        WHERE TRADE_DT BETWEEN '{start}' AND '{end}'
        """
        df = fetch_wind(sql)
        if df.empty:
            continue
        lower = {c.lower(): c for c in df.columns}
        rows = [
            (
                "CSI800_ENH",
                str(r.get(lower["s_info_windcode"]) or "000906.SH").strip(),
                str(r.get(lower["trade_dt"]) or "").strip(),
                str(r.get(lower["s_con_windcode"]) or "").strip(),
                None if pd.isna(r.get(lower["weight"])) else float(r.get(lower["weight"])),
                "wind:AIndexCSI800Weight",
                "ready",
            )
            for r in df.to_dict("records")
            if str(r.get(lower["trade_dt"]) or "").strip() and str(r.get(lower["s_con_windcode"]) or "").strip()
        ]
        if not rows:
            continue
        conn.executemany(
            """
            insert or replace into index_constituent_period
            (universe, index_code, trade_date, con_code, weight, source, status)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        dates = [r[2] for r in rows]
        total_rows += len(rows)
        min_date = min(min_date or min(dates), min(dates))
        max_date = max(max_date or max(dates), max(dates))
        log(conn, "csi800_weight_chunk", "ready", f"{start}-{end}; rows={len(rows)}")
    result = {"rows": total_rows, "min_date": min_date, "max_date": max_date}
    status = "ready" if result["rows"] else "blocked"
    manifest(conn, "wande.dbo.AINDEXCSI800WEIGHT", "index_constituent_period", result["rows"], result["min_date"], result["max_date"], status, "CSI800 daily/open weight table")
    log(conn, "csi800_weight", status, json.dumps(result, ensure_ascii=False))
    return result


def fill_lhb(conn):
    total_rows = 0
    min_date = None
    max_date = None
    name_map = dict(conn.execute("select ts_code, stock_name from security_master").fetchall())
    for _, start, end in month_ranges():
        existing = conn.execute(
            "select count(*) from lhb_daily where trade_date between ? and ?",
            (start, end),
        ).fetchone()[0]
        if existing:
            total_rows += int(existing)
            min_date = min(min_date or start, start)
            max_date = max(max_date or end, end)
            continue
        sql = f"""
        SELECT S_INFO_WINDCODE, S_STRANGE_BGDATE, S_STRANGE_ENDDATE, S_STRANGE_TRADERNAME,
               S_STRANGE_BUYAMOUNT, S_STRANGE_SELLAMOUNT, S_STRANGE_AMOUNT, S_VARIANT_TYPE
        FROM wande.dbo.ASHARESTRANGETRADE
        WHERE S_STRANGE_ENDDATE BETWEEN '{start}' AND '{end}'
        """
        df = fetch_wind(sql)
        if df.empty:
            continue
        lower = {c.lower(): c for c in df.columns}
        rows = []
        for r in df.to_dict("records"):
            code = str(r.get(lower["s_info_windcode"]) or "").strip()
            trade_date = str(r.get(lower["s_strange_enddate"]) or r.get(lower["s_strange_bgdate"]) or "").strip()
            if not code or not trade_date:
                continue
            buy = 0.0 if pd.isna(r.get(lower["s_strange_buyamount"])) else float(r.get(lower["s_strange_buyamount"]))
            sell = 0.0 if pd.isna(r.get(lower["s_strange_sellamount"])) else float(r.get(lower["s_strange_sellamount"]))
            amount = None if pd.isna(r.get(lower["s_strange_amount"])) else float(r.get(lower["s_strange_amount"]))
            variant = str(r.get(lower.get("s_variant_type", "")) or "").strip()
            trader = str(r.get(lower["s_strange_tradername"]) or "strange_trade").strip()
            reason = (variant + ":" + trader).strip(":")[:300]
            rows.append((trade_date, code, name_map.get(code), reason, buy, sell, buy - sell, amount, "wind:AShareStrangeTrade"))
        if not rows:
            continue
        conn.executemany(
            """
            insert or replace into lhb_daily
            (trade_date, ts_code, stock_name, reason, buy_amount, sell_amount, net_amount, institution_net_amount, source)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        dates = [r[0] for r in rows]
        total_rows += len(rows)
        min_date = min(min_date or min(dates), min(dates))
        max_date = max(max_date or max(dates), max(dates))
        log(conn, f"lhb_wind_chunk_{start[:6]}", "ready", f"{start}-{end}; rows={len(rows)}")
    result = {"rows": total_rows, "min_date": min_date, "max_date": max_date}
    status = "ready" if result["rows"] else "blocked"
    manifest(conn, "wande.dbo.ASHARESTRANGETRADE", "lhb_daily", result["rows"], result["min_date"], result["max_date"], status, "Wind strange-trade branch detail")
    log(conn, "lhb_wind", status, json.dumps(result, ensure_ascii=False))
    return result


def classify_event_tag(category_code, content):
    text = str(content or "")
    code = str(category_code or "").split(".")[0]
    if code.startswith("204006") or "业绩" in text:
        return "earnings"
    if code.startswith("204005") or "分红" in text:
        return "dividend"
    if code.startswith("204003") or code.startswith("204004") or "增发" in text or "配股" in text:
        return "refinancing"
    if code.startswith("204001") or "停牌" in text or "交易异动" in text:
        return "trading_event"
    if code.startswith("204009") or "诉讼" in text or "违规" in text:
        return "risk_event"
    if code.startswith("204014") or "并购" in text or "重组" in text:
        return "ma_restructuring"
    if "指数" in text:
        return "index_event"
    return "major_event"


def fill_major_events(conn):
    total_rows = 0
    min_date = None
    max_date = None
    completed = {
        row[0]: int(row[1])
        for row in conn.execute(
            """
            select substr(publish_date, 1, 6) as month, count(*)
            from news_event_daily
            where source_site='wind:AshareMajorEvent'
            group by substr(publish_date, 1, 6)
            """
        ).fetchall()
    }
    for _, start, end in month_ranges():
        existing = completed.get(start[:6], 0)
        if existing:
            total_rows += existing
            min_date = min(min_date or start, start)
            max_date = max(max_date or end, end)
            continue
        sql = f"""
        SELECT OBJECT_ID, S_INFO_WINDCODE, S_EVENT_CATEGORYCODE, S_EVENT_ANNCEDATE,
               S_EVENT_HAPDATE, S_EVENT_CONTENT
        FROM wande.dbo.ASHAREMAJOREVENT
        WHERE S_EVENT_ANNCEDATE BETWEEN '{start}' AND '{end}'
        """
        df = fetch_wind(sql)
        if df.empty:
            continue
        lower = {c.lower(): c for c in df.columns}
        rows = []
        for r in df.to_dict("records"):
            publish_date = str(r.get(lower["s_event_anncedate"]) or "").strip()
            event_id = str(r.get(lower["object_id"]) or "").strip()
            code = str(r.get(lower["s_info_windcode"]) or "").strip()
            category = r.get(lower["s_event_categorycode"])
            content = str(r.get(lower["s_event_content"]) or "").strip()
            if not publish_date or not event_id:
                continue
            rows.append(
                (
                    "wind_major_event:" + event_id,
                    publish_date,
                    content[:1200],
                    str(category).split(".")[0] if category is not None else None,
                    "stock",
                    code,
                    "wind:AshareMajorEvent",
                    None,
                    classify_event_tag(category, content),
                )
            )
        if not rows:
            continue
        conn.executemany(
            """
            insert or replace into news_event_daily
            (news_id, publish_date, headline, category, subject_type, subject_code, source_site, source_url, event_tag)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        dates = [r[1] for r in rows]
        total_rows += len(rows)
        min_date = min(min_date or min(dates), min(dates))
        max_date = max(max_date or max(dates), max(dates))
        log(conn, f"major_event_chunk_{start[:6]}", "ready", f"{start}-{end}; rows={len(rows)}")
    result = {"rows": total_rows, "min_date": min_date, "max_date": max_date}
    status = "ready" if result["rows"] else "blocked"
    manifest(conn, "wande.dbo.ASHAREMAJOREVENT", "news_event_daily", result["rows"], result["min_date"], result["max_date"], status, "Wind A-share major events")
    log(conn, "major_event_wind", status, json.dumps(result, ensure_ascii=False))
    return result


def probe():
    probes = {}
    for table in ["wande.dbo.AINDEXMEMBERS", "wande.dbo.AINDEXCSI800WEIGHT", "wande.dbo.ASHARESTRANGETRADE", "wande.dbo.ASHAREMAJOREVENT"]:
        try:
            df = fetch_wind(f"SELECT TOP 3 * FROM {table}")
            probes[table] = {"status": "ok", "rows": len(df), "columns": list(df.columns)}
        except Exception as exc:
            probes[table] = {"status": "error", "message": str(exc)}
    return probes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--mode", choices=["probe", "index_members", "csi800_weight", "lhb", "major_event", "all"], required=True)
    parser.add_argument("--out", default=str(DEFAULT_PROJECT_ROOT / "environment" / "data_sources" / "wind_sql_connector_result.json"))
    args = parser.parse_args()

    if args.mode == "probe":
        result = probe()
    else:
        conn = sqlite3.connect(args.db)
        try:
            result = {}
            if args.mode in {"index_members", "all"}:
                result["index_members"] = fill_index_members(conn)
            if args.mode in {"csi800_weight", "all"}:
                result["csi800_weight"] = fill_csi800_weight(conn)
            if args.mode in {"lhb", "all"}:
                result["lhb"] = fill_lhb(conn)
            if args.mode in {"major_event", "all"}:
                result["major_event"] = fill_major_events(conn)
        finally:
            conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "mode": args.mode, "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
