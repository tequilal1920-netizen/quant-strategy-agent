import argparse
import calendar
import datetime as dt
import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_URL = os.environ.get("TUSHARE_API_URL", "http://api.tushare.pro")
START_DATE = "20120101"
END_DATE = "20260630"


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def month_ranges(start, end):
    start_dt = dt.datetime.strptime(start, "%Y%m%d").date().replace(day=1)
    end_dt = dt.datetime.strptime(end, "%Y%m%d").date()
    cur = start_dt
    while cur <= end_dt:
        last_day = calendar.monthrange(cur.year, cur.month)[1]
        a = max(cur, dt.datetime.strptime(start, "%Y%m%d").date())
        b = min(cur.replace(day=last_day), end_dt)
        yield a.strftime("%Y%m%d"), b.strftime("%Y%m%d")
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


def log(conn, run_id, step, status, message):
    conn.execute(
        """
        insert or replace into update_log(run_id, step, status, message, started_at, ended_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (run_id, step, status, message, now(), now()),
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


def manifest(conn, source_table, target_table, rows, min_date, max_date, status, message=""):
    conn.execute(
        """
        insert or replace into source_manifest
        (source_name, source_path, source_table, target_table, start_date, end_date, rows_loaded, min_date, max_date,
         frequency, update_mode, quota_policy, status, message, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tushare_pro",
            "env:TUSHARE_TOKEN",
            source_table,
            target_table,
            START_DATE,
            END_DATE,
            rows,
            min_date,
            max_date,
            "daily_or_rebalance",
            "incremental_low_frequency",
            "env_token_small_batch_no_bulk_secret_storage",
            status,
            message,
            now(),
        ),
    )
    conn.commit()


def pro_request(api_name, params=None, fields=None, token=None, timeout=60):
    token = token or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set in the environment")
    payload = {
        "api_name": api_name,
        "token": token,
        "params": params or {},
        "fields": fields or "",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if out.get("code") not in (0, None):
        raise RuntimeError(f"Tushare {api_name} failed: code={out.get('code')} msg={out.get('msg')}")
    if "data" not in out or not out["data"]:
        return []
    names = out["data"].get("fields", [])
    rows = out["data"].get("items", [])
    return [dict(zip(names, row)) for row in rows]


def fill_index_weight(conn, index_code, universe, start, end, pause, max_calls):
    fields = "index_code,con_code,trade_date,weight"
    total = 0
    calls = 0
    min_date = None
    max_date = None
    for a, b in month_ranges(start, end):
        if max_calls is not None and calls >= max_calls:
            break
        rows = pro_request(
            "index_weight",
            {"index_code": index_code, "start_date": a, "end_date": b},
            fields,
        )
        calls += 1
        if rows:
            conn.executemany(
                """
                insert or replace into index_constituent_period
                (universe, index_code, trade_date, con_code, weight, source, status)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        universe,
                        row.get("index_code") or index_code,
                        row.get("trade_date"),
                        row.get("con_code"),
                        float(row.get("weight") or 0.0),
                        "tushare:index_weight",
                        "ready",
                    )
                    for row in rows
                    if row.get("trade_date") and row.get("con_code")
                ],
            )
            conn.commit()
            total += len(rows)
            dates = [row.get("trade_date") for row in rows if row.get("trade_date")]
            if dates:
                min_date = min(min_date or min(dates), min(dates))
                max_date = max(max_date or max(dates), max(dates))
        time.sleep(pause)
    status = "ready" if total else "blocked"
    message = f"{index_code} -> {universe}; calls={calls}; rows={total}"
    manifest(conn, f"index_weight:{index_code}", "index_constituent_period", total, min_date, max_date, status, message)
    quality(conn, "index_constituent_period", universe, "coverage", status, total, message)
    log(conn, "tushare_connector", f"index_weight_{universe}", status, message)
    return {"universe": universe, "index_code": index_code, "rows": total, "calls": calls, "min_date": min_date, "max_date": max_date}


def read_qfq_scale(source_db, last_date):
    if not source_db or not Path(source_db).exists():
        return {}
    src = sqlite3.connect(source_db)
    try:
        rows = src.execute(
            """
            select ts_code, close, adj_factor, qfq_close
            from stock_market_daily
            where trade_date=? and close>0 and adj_factor>0 and qfq_close>0
            """,
            (last_date,),
        ).fetchall()
        return {code: qfq / (close * adj) for code, close, adj, qfq in rows if close and adj}
    finally:
        src.close()


def fill_market_gap(conn, start, end, pause, max_calls, source_db=None):
    current_max = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0]
    if current_max and current_max >= end:
        return {"status": "ready", "message": f"stock_ohlcv_daily already covers {current_max}", "calls": 0, "rows": 0}
    start = max(start, current_max or start)
    dates = [
        x[0]
        for x in conn.execute(
            """
            select trade_date from trade_calendar
            where is_trade_day=1 and trade_date>? and trade_date<=?
            order by trade_date
            """,
            (start, end),
        ).fetchall()
    ]
    if not dates:
        return {"status": "ready", "message": "no trade dates to fill", "calls": 0, "rows": 0}

    scale = read_qfq_scale(source_db, current_max) if current_max else {}
    if not scale:
        quality(conn, "stock_ohlcv_daily", "qfq_close", "qfq_scale", "warning", None, "No local source adj_factor scale; qfq_close may need review.")

    daily_fields = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    basic_fields = "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv"
    adj_fields = "ts_code,trade_date,adj_factor"
    mf_fields = "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
    calls = 0
    rows_loaded = 0
    min_date = None
    max_date = None
    names = dict(conn.execute("select ts_code, stock_name from security_master").fetchall())
    for d in dates:
        if max_calls is not None and calls >= max_calls:
            break
        daily = pro_request("daily", {"trade_date": d}, daily_fields)
        calls += 1
        time.sleep(pause)
        if not daily:
            continue
        if max_calls is not None and calls >= max_calls:
            break
        basic = pro_request("daily_basic", {"trade_date": d}, basic_fields)
        calls += 1
        time.sleep(pause)
        if max_calls is not None and calls >= max_calls:
            break
        adj = pro_request("adj_factor", {"trade_date": d}, adj_fields)
        calls += 1
        time.sleep(pause)
        if max_calls is not None and calls >= max_calls:
            break
        money = pro_request("moneyflow", {"trade_date": d}, mf_fields)
        calls += 1
        time.sleep(pause)

        basic_map = {r.get("ts_code"): r for r in basic}
        adj_map = {r.get("ts_code"): float(r.get("adj_factor") or 0.0) for r in adj}
        money_map = {r.get("ts_code"): r for r in money}
        ohlcv_rows = []
        val_rows = []
        mf_rows = []
        all_a_rows = []
        for row in daily:
            code = row.get("ts_code")
            if not code:
                continue
            close = float(row.get("close") or 0.0)
            adj_factor = adj_map.get(code)
            qfq_close = close
            if adj_factor and scale.get(code):
                qfq_close = close * adj_factor * scale[code]
            ohlcv_rows.append((
                row.get("trade_date") or d,
                code,
                names.get(code),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                close,
                qfq_close,
                row.get("pre_close"),
                row.get("pct_chg"),
                row.get("vol"),
                row.get("amount"),
                None,
                None,
                None,
            ))
            b = basic_map.get(code, {})
            val_rows.append((
                d,
                code,
                b.get("pe_ttm"),
                b.get("pb"),
                b.get("ps_ttm"),
                b.get("dv_ttm"),
                b.get("total_mv"),
                b.get("circ_mv"),
                b.get("turnover_rate"),
                b.get("turnover_rate_f"),
                b.get("volume_ratio"),
            ))
            m = money_map.get(code, {})
            mf_rows.append((
                d,
                code,
                m.get("net_mf_amount"),
                m.get("buy_lg_amount"),
                m.get("sell_lg_amount"),
                m.get("buy_elg_amount"),
                m.get("sell_elg_amount"),
            ))
            if close > 0 and qfq_close > 0 and "ST" not in (names.get(code) or ""):
                all_a_rows.append(("ALL_A", None, d, code, None, "tushare:daily_gap", "ready"))
        conn.executemany(
            """
            insert or replace into stock_ohlcv_daily
            (trade_date, ts_code, stock_name, open, high, low, close, qfq_close, pre_close, pct_chg, vol, amount, up_limit, down_limit, suspend_timing)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ohlcv_rows,
        )
        conn.executemany(
            """
            insert or replace into stock_valuation_daily
            (trade_date, ts_code, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv, turnover_rate, turnover_rate_f, volume_ratio)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            val_rows,
        )
        conn.executemany(
            """
            insert or replace into stock_moneyflow_daily
            (trade_date, ts_code, net_mf_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            mf_rows,
        )
        conn.executemany(
            """
            insert or replace into index_constituent_period
            (universe, index_code, trade_date, con_code, weight, source, status)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            all_a_rows,
        )
        conn.commit()
        rows_loaded += len(ohlcv_rows)
        min_date = min(min_date or d, d)
        max_date = max(max_date or d, d)

    status = "ready" if rows_loaded else "blocked"
    message = f"calls={calls}; rows={rows_loaded}; {min_date} to {max_date}; qfq_scale_from={current_max}"
    manifest(conn, "daily+daily_basic+adj_factor+moneyflow", "stock_ohlcv_daily/stock_valuation_daily/stock_moneyflow_daily", rows_loaded, min_date, max_date, status, message)
    for table in ["stock_ohlcv_daily", "stock_valuation_daily", "stock_moneyflow_daily"]:
        max_loaded = conn.execute(f"select max(trade_date) from {table}").fetchone()[0]
        quality(conn, table, "trade_date", "coverage_end", "ready" if max_loaded >= end else "blocked", None, f"max_date={max_loaded}; required_end={end}")
    log(conn, "tushare_connector", "market_gap", status, message)
    return {"status": status, "calls": calls, "rows": rows_loaded, "min_date": min_date, "max_date": max_date}


def fill_lhb(conn, start, end, pause, max_calls):
    dates = [
        x[0]
        for x in conn.execute(
            """
            select trade_date from trade_calendar
            where is_trade_day=1 and trade_date between ? and ?
            order by trade_date
            """,
            (start, end),
        ).fetchall()
    ]
    fields = "trade_date,ts_code,name,close,pct_chg,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,reason"
    total = 0
    calls = 0
    min_date = None
    max_date = None
    for d in dates:
        if max_calls is not None and calls >= max_calls:
            break
        rows = pro_request("top_list", {"trade_date": d}, fields)
        calls += 1
        if rows:
            conn.executemany(
                """
                insert or replace into lhb_daily
                (trade_date, ts_code, stock_name, reason, buy_amount, sell_amount, net_amount, institution_net_amount, source)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.get("trade_date") or d,
                        row.get("ts_code"),
                        row.get("name"),
                        row.get("reason"),
                        float(row.get("l_buy") or 0.0),
                        float(row.get("l_sell") or 0.0),
                        float(row.get("net_amount") or 0.0),
                        None,
                        "tushare:top_list",
                    )
                    for row in rows
                    if row.get("ts_code")
                ],
            )
            conn.commit()
            total += len(rows)
            min_date = min(min_date or d, d)
            max_date = max(max_date or d, d)
        time.sleep(pause)
    status = "ready" if total else "blocked"
    message = f"calls={calls}; rows={total}; {min_date} to {max_date}"
    manifest(conn, "top_list", "lhb_daily", total, min_date, max_date, status, message)
    quality(conn, "lhb_daily", None, "coverage", status, total, message)
    log(conn, "tushare_connector", "lhb_daily", status, message)
    return {"rows": total, "calls": calls, "min_date": min_date, "max_date": max_date}


def probe(conn, start, end):
    samples = {}
    for api_name, params, fields in [
        ("daily", {"trade_date": end}, "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"),
        ("daily_basic", {"trade_date": end}, "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv"),
        ("adj_factor", {"trade_date": end}, "ts_code,trade_date,adj_factor"),
        ("moneyflow", {"trade_date": end}, "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"),
        ("index_weight", {"index_code": "000906.SH", "start_date": start, "end_date": end}, "index_code,con_code,trade_date,weight"),
        ("top_list", {"trade_date": end}, "trade_date,ts_code,name,l_buy,l_sell,net_amount,reason"),
    ]:
        try:
            rows = pro_request(api_name, params, fields)
            samples[api_name] = {"status": "ok", "rows": len(rows), "fields": list(rows[0].keys()) if rows else []}
        except Exception as exc:
            samples[api_name] = {"status": "error", "message": str(exc)}
    quality(conn, "source_manifest", "tushare", "probe", "ready" if any(v["status"] == "ok" for v in samples.values()) else "blocked", None, json.dumps(samples, ensure_ascii=False))
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--mode", choices=["probe", "index_weight", "lhb", "market_gap"], required=True)
    parser.add_argument("--start", default=START_DATE)
    parser.add_argument("--end", default=END_DATE)
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--source-db", default=os.environ.get("SOURCE_DB", ""), help="Required for market_gap; may also be set via SOURCE_DB.")
    parser.add_argument("--out", default=str(DEFAULT_PROJECT_ROOT / "output" / "framework" / "data_pipeline" / "tushare_connector_result.json"))
    args = parser.parse_args()
    if args.mode == "market_gap" and not args.source_db:
        parser.error("--source-db is required for market_gap or set SOURCE_DB.")

    conn = sqlite3.connect(args.db)
    result = {}
    if args.mode == "probe":
        result = probe(conn, args.start, args.end)
    elif args.mode == "index_weight":
        result["CSI800_ENH"] = fill_index_weight(conn, "000906.SH", "CSI800_ENH", args.start, args.end, args.pause, args.max_calls)
        result["CSI2000_ENH"] = fill_index_weight(conn, "932000.CSI", "CSI2000_ENH", args.start, args.end, args.pause, args.max_calls)
    elif args.mode == "lhb":
        result = fill_lhb(conn, args.start, args.end, args.pause, args.max_calls)
    elif args.mode == "market_gap":
        result = fill_market_gap(conn, args.start, args.end, args.pause, args.max_calls, args.source_db)
    conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "out": str(out), "result": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
