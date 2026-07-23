import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_DATE = "20120101"
END_DATE = "20260630"
REQUIRED_TABLES = {
    "trade_calendar": {"date_col": "trade_date", "min_rows": 3000},
    "security_master": {"date_col": None, "min_rows": 4000},
    "stock_ohlcv_daily": {"date_col": "trade_date", "min_rows": 10_000_000},
    "stock_valuation_daily": {"date_col": "trade_date", "min_rows": 10_000_000},
    "stock_moneyflow_daily": {"date_col": "trade_date", "min_rows": 10_000_000},
    "financial_report_visible": {"date_col": "visible_date", "min_rows": 100_000},
    "sw_l1_industry_daily": {"date_col": "start_date", "min_rows": 4_000},
    "index_constituent_period": {"date_col": "trade_date", "min_rows": 10_000_000},
    "etf_master": {"date_col": None, "min_rows": 100},
    "etf_ohlcv_daily": {"date_col": "trade_date", "min_rows": 200_000},
    "macro_monthly": {"date_col": "month", "min_rows": 160},
    "broker_report_index": {"date_col": "report_date", "min_rows": 30},
}
REQUIRED_UNIVERSES = ["ALL_A", "CSI800_ENH", "CSI2000_ENH"]
UNIVERSE_INCEPTION = {
    "CSI2000_ENH": "20230811",
}


def query_one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def table_exists(conn, table):
    return query_one(
        conn,
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ) is not None


def status_item(name, status, message, **extra):
    item = {"name": name, "status": status, "message": message}
    item.update(extra)
    return item


def required_trade_bounds(conn):
    row = conn.execute(
        """
        select min(trade_date), max(trade_date)
        from trade_calendar
        where is_trade_day=1 and trade_date between ? and ?
        """,
        (START_DATE, END_DATE),
    ).fetchone()
    return row[0] or START_DATE, row[1] or END_DATE


def audit_database(db_path):
    conn = sqlite3.connect(db_path)
    checks = []
    first_trade, last_trade = required_trade_bounds(conn)

    for table, spec in REQUIRED_TABLES.items():
        if not table_exists(conn, table):
            checks.append(status_item(table, "blocked", "required table missing"))
            continue
        rows = query_one(conn, f"select count(*) from {table}")[0]
        status = "ready" if rows >= spec["min_rows"] else "blocked"
        msg = f"{rows} rows; threshold {spec['min_rows']}"
        extra = {"rows": rows}
        date_col = spec["date_col"]
        if date_col:
            min_date, max_date = query_one(conn, f"select min({date_col}), max({date_col}) from {table}")
            extra.update({"min_date": min_date, "max_date": max_date})
            if table == "financial_report_visible":
                stale_cutoff = "20260101"
                if min_date is None or min_date > first_trade or max_date is None or max_date < stale_cutoff:
                    status = "blocked"
                    msg += f"; event coverage {min_date} to {max_date}, required start <= {first_trade} and recent visible_date >= {stale_cutoff}"
                else:
                    msg += "; financial reports are point-in-time event data, not daily observations"
            elif table not in {"macro_monthly", "broker_report_index", "sw_l1_industry_daily"}:
                if min_date is None or min_date > first_trade or max_date is None or max_date < last_trade:
                    status = "blocked"
                    msg += f"; coverage {min_date} to {max_date}, required first/last trade {first_trade} to {last_trade}"
            elif table == "macro_monthly":
                if min_date is None or min_date > "201201" or max_date is None or max_date < "202606":
                    status = "blocked"
                    msg += f"; monthly coverage {min_date} to {max_date}, required 201201 to 202606"
        checks.append(status_item(table, status, msg, **extra))

    for universe in REQUIRED_UNIVERSES:
        rows, min_date, max_date, n_codes = query_one(
            conn,
            """
            select count(*), min(trade_date), max(trade_date), count(distinct con_code)
            from index_constituent_period
            where universe=?
            """,
            (universe,),
        )
        status = "ready"
        msg = f"{rows} rows; {n_codes} names; {min_date} to {max_date}"
        expected_start = max(first_trade, UNIVERSE_INCEPTION.get(universe, first_trade))
        if not rows:
            status = "blocked"
            msg = "missing exact dynamic constituents"
        elif min_date is None or min_date > expected_start or max_date is None or max_date < last_trade:
            status = "blocked"
            msg += f"; coverage does not span required universe window {expected_start} to {last_trade}"
        elif universe in UNIVERSE_INCEPTION:
            msg += f"; accepted from official index start {UNIVERSE_INCEPTION[universe]}"
        checks.append(status_item(f"universe:{universe}", status, msg, rows=rows, min_date=min_date, max_date=max_date, distinct_codes=n_codes))

    lhb_rows, lhb_min, lhb_max = query_one(
        conn,
        "select count(*), min(trade_date), max(trade_date) from lhb_daily",
    )
    lhb_status = "ready" if lhb_rows and lhb_min <= first_trade and lhb_max >= last_trade else "blocked"
    checks.append(status_item(
        "lhb_daily:coverage",
        lhb_status,
        f"{lhb_rows} rows; {lhb_min} to {lhb_max}; required for weekly hot-stock deep dive",
        rows=lhb_rows,
        min_date=lhb_min,
        max_date=lhb_max,
    ))

    news_rows, news_min, news_max = query_one(
        conn,
        "select count(*), min(publish_date), max(publish_date) from news_event_daily",
    )
    news_status = "ready" if news_rows and news_min <= START_DATE and news_max >= END_DATE else "blocked"
    checks.append(status_item(
        "news_event_daily:coverage",
        news_status,
        f"{news_rows} rows; {news_min} to {news_max}; required for weekly hot-event review",
        rows=news_rows,
        min_date=news_min,
        max_date=news_max,
    ))

    blocked = [x for x in checks if x["status"] == "blocked"]
    warning = [x for x in checks if x["status"] == "warning"]
    summary = {
        "db_path": str(db_path),
        "required_start": START_DATE,
        "required_end": END_DATE,
        "status": "blocked" if blocked else "ready",
        "blocked_count": len(blocked),
        "warning_count": len(warning),
        "checks": checks,
    }
    conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--out", default=str(DEFAULT_PROJECT_ROOT / "framework" / "data_quality" / "data_quality_gate.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    summary = audit_database(Path(args.db))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": summary["status"],
        "blocked_count": summary["blocked_count"],
        "warning_count": summary["warning_count"],
        "out": str(out),
    }, ensure_ascii=False))
    if args.strict and summary["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
