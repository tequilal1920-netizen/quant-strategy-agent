"""Incrementally fill the local research warehouse with BaoStock daily data.

This connector is deliberately narrow:

* it never stores credentials;
* it fills only the missing daily dates after the current local maximum;
* it writes the same canonical tables used by the style and industry rotation
  engines: ``trade_calendar``, ``stock_ohlcv_daily`` and
  ``stock_valuation_daily``;
* fields not provided historically by BaoStock are either derived from real
  price history or left null, and the source manifest records that contract.

BaoStock does not currently recognise Beijing Stock Exchange symbols through
``query_history_k_data_plus``.  The connector therefore updates Shanghai and
Shenzhen symbols only and leaves existing BJ rows untouched rather than
fabricating data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"
FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,turn,"
    "pctChg,peTTM,pbMRQ,psTTM,isST"
)


def now_text() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact_date(value: str | pd.Timestamp | dt.date) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def dashed_date(value: str | pd.Timestamp | dt.date) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def tushare_to_baostock(code: str) -> str | None:
    if code.endswith(".SH"):
        return "sh." + code[:6]
    if code.endswith(".SZ"):
        return "sz." + code[:6]
    return None


def baostock_to_tushare(code: str) -> str | None:
    if code.startswith("sh."):
        return code[3:] + ".SH"
    if code.startswith("sz."):
        return code[3:] + ".SZ"
    return None


def ensure_metadata_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists source_manifest (
          source_name text,
          source_path text,
          source_table text,
          target_table text,
          start_date text,
          end_date text,
          rows_loaded integer,
          min_date text,
          max_date text,
          frequency text,
          update_mode text,
          quota_policy text,
          status text,
          message text,
          updated_at text,
          primary key (source_name, source_table, target_table)
        )
        """
    )
    conn.execute(
        """
        create table if not exists data_quality_check (
          check_id text primary key,
          table_name text,
          field_name text,
          check_type text,
          status text,
          metric_value real,
          message text,
          checked_at text
        )
        """
    )
    conn.commit()


def write_manifest(
    conn: sqlite3.Connection,
    source_table: str,
    target_table: str,
    rows: int,
    start: str,
    end: str,
    min_date: str | None,
    max_date: str | None,
    status: str,
    message: str,
) -> None:
    ensure_metadata_tables(conn)
    conn.execute(
        """
        insert or replace into source_manifest
        (source_name, source_path, source_table, target_table, start_date, end_date,
         rows_loaded, min_date, max_date, frequency, update_mode, quota_policy,
         status, message, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "baostock_public",
            "https://baostock.com",
            source_table,
            target_table,
            start,
            end,
            rows,
            min_date,
            max_date,
            "daily",
            "incremental_gap_fill",
            "public_api_small_batch_no_credentials",
            status,
            message,
            now_text(),
        ),
    )
    conn.commit()


def write_quality(
    conn: sqlite3.Connection,
    table: str,
    field: str,
    check_type: str,
    status: str,
    message: str,
    value: float | None = None,
) -> None:
    ensure_metadata_tables(conn)
    check_id = f"{table}:{field}:{check_type}"
    conn.execute(
        """
        insert or replace into data_quality_check
        (check_id, table_name, field_name, check_type, status, metric_value,
         message, checked_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (check_id, table, field, check_type, status, value, message, now_text()),
    )
    conn.commit()


def update_trade_calendar(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    import baostock as bs  # type: ignore

    start_dash = dashed_date(start)
    end_dash = dashed_date(end)
    rs = bs.query_trade_dates(start_date=start_dash, end_date=end_dash)
    rows: list[tuple[str, int]] = []
    trade_dates: list[str] = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        date_text = compact_date(row[0])
        is_open = int(row[1])
        rows.append((date_text, is_open))
        if is_open == 1:
            trade_dates.append(date_text)
    if rs.error_code != "0":
        raise RuntimeError(f"baostock_trade_calendar_failed:{rs.error_code}:{rs.error_msg}")
    conn.executemany(
        "insert or replace into trade_calendar (trade_date, is_trade_day) values (?, ?)",
        rows,
    )
    conn.commit()
    write_manifest(
        conn,
        "query_trade_dates",
        "trade_calendar",
        len(rows),
        start,
        end,
        min((row[0] for row in rows), default=None),
        max((row[0] for row in rows), default=None),
        "ready" if rows else "blocked",
        f"trade_days={len(trade_dates)}; calendar_rows={len(rows)}",
    )
    return trade_dates


def load_security_base(conn: sqlite3.Connection, base_date: str) -> pd.DataFrame:
    query = """
        select m.ts_code, m.stock_name, o.close as base_close,
               o.qfq_close as base_qfq_close,
               v.total_mv as base_total_mv, v.circ_mv as base_circ_mv,
               v.dv_ttm as base_dv_ttm
        from security_master m
        left join stock_ohlcv_daily o
          on o.ts_code = m.ts_code and o.trade_date = ?
        left join stock_valuation_daily v
          on v.ts_code = m.ts_code and v.trade_date = ?
        where (m.ts_code like '%.SH' or m.ts_code like '%.SZ')
    """
    frame = pd.read_sql_query(query, conn, params=(base_date, base_date))
    frame["bs_code"] = frame["ts_code"].map(tushare_to_baostock)
    for column in [
        "base_close",
        "base_qfq_close",
        "base_total_mv",
        "base_circ_mv",
        "base_dv_ttm",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.loc[frame["bs_code"].notna()].sort_values("ts_code").reset_index(drop=True)


def load_volume_history(conn: sqlite3.Connection, base_date: str) -> dict[str, list[float]]:
    start = (pd.Timestamp(base_date) - pd.Timedelta(days=20)).strftime("%Y%m%d")
    frame = pd.read_sql_query(
        """
        select ts_code, trade_date, vol
        from stock_ohlcv_daily
        where trade_date between ? and ?
          and (ts_code like '%.SH' or ts_code like '%.SZ')
        order by ts_code, trade_date
        """,
        conn,
        params=(start, base_date),
    )
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce")
    return {
        code: [float(v) for v in group["vol"].dropna().tail(5)]
        for code, group in frame.groupby("ts_code", sort=False)
    }


def query_one_stock(bs: Any, code: str, start: str, end: str) -> pd.DataFrame:
    rs = bs.query_history_k_data_plus(
        code,
        FIELDS,
        start_date=dashed_date(start),
        end_date=dashed_date(end),
        frequency="d",
        adjustflag="3",
    )
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=rs.fields)
    frame["ts_code"] = frame["code"].map(baostock_to_tushare)
    frame["trade_date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y%m%d")
    for column in [
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "turn",
        "pctChg",
        "peTTM",
        "pbMRQ",
        "psTTM",
        "isST",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["ts_code", "trade_date"]).sort_values("trade_date")


def fill_market_gap(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    pause: float,
    max_codes: int | None,
    progress_every: int,
    batch_size: int,
) -> dict[str, Any]:
    import baostock as bs  # type: ignore

    login = bs.login()
    if getattr(login, "error_code", "") != "0":
        raise RuntimeError(f"baostock_login_failed:{getattr(login, 'error_msg', '')}")

    current_max = conn.execute(
        """
        select max(trade_date)
        from (
          select trade_date, count(*) as n
          from stock_ohlcv_daily
          where ts_code like '%.SH' or ts_code like '%.SZ'
          group by trade_date
          having n >= 4000
        )
        """
    ).fetchone()[0]
    base_date = current_max or start
    if current_max and current_max >= end:
        return {
            "status": "ready",
            "message": f"stock_ohlcv_daily already covers {current_max}",
            "rows": 0,
            "codes": 0,
            "min_date": None,
            "max_date": None,
        }
    calendar_start = (
        (pd.Timestamp(current_max) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        if current_max
        else start
    )
    trade_dates = update_trade_calendar(conn, calendar_start, end)
    dates = [
        row[0]
        for row in conn.execute(
            """
            select trade_date from trade_calendar
            where is_trade_day=1 and trade_date>? and trade_date<=?
            order by trade_date
            """,
            (base_date, end),
        )
    ]
    if not dates:
        return {
            "status": "ready",
            "message": "no missing trade dates",
            "rows": 0,
            "codes": 0,
            "min_date": None,
            "max_date": None,
        }
    securities = load_security_base(conn, base_date)
    complete_codes = {
        row[0]
        for row in conn.execute(
            """
            select ts_code
            from stock_ohlcv_daily
            where trade_date between ? and ?
              and (ts_code like '%.SH' or ts_code like '%.SZ')
            group by ts_code
            having count(distinct trade_date) >= ?
            """,
            (dates[0], dates[-1], len(dates)),
        )
    }
    if complete_codes:
        securities = securities.loc[~securities["ts_code"].isin(complete_codes)].copy()
    if max_codes:
        securities = securities.head(max_codes).copy()
    volume_seed = load_volume_history(conn, base_date)
    base_by_code = securities.set_index("ts_code").to_dict("index")

    ohlcv_rows: list[tuple[Any, ...]] = []
    valuation_rows: list[tuple[Any, ...]] = []
    errors: list[dict[str, str]] = []
    processed = 0
    rows_loaded = 0
    min_date: str | None = None
    max_date: str | None = None

    def flush_batch() -> None:
        nonlocal ohlcv_rows, valuation_rows, rows_loaded
        if not ohlcv_rows and not valuation_rows:
            return
        conn.executemany(
            """
            insert or replace into stock_ohlcv_daily
            (trade_date, ts_code, stock_name, open, high, low, close, qfq_close,
             pre_close, pct_chg, vol, amount, up_limit, down_limit, suspend_timing)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ohlcv_rows,
        )
        conn.executemany(
            """
            insert or replace into stock_valuation_daily
            (trade_date, ts_code, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv,
             turnover_rate, turnover_rate_f, volume_ratio)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            valuation_rows,
        )
        conn.commit()
        rows_loaded += len(ohlcv_rows)
        ohlcv_rows = []
        valuation_rows = []

    try:
        for sec in securities.itertuples(index=False):
            processed += 1
            frame = query_one_stock(bs, sec.bs_code, dates[0], dates[-1])
            if frame.empty:
                errors.append({"ts_code": sec.ts_code, "reason": "empty_or_unsupported"})
                continue
            base = base_by_code.get(sec.ts_code, {})
            last_close = safe_float(base.get("base_close"))
            qfq = safe_float(base.get("base_qfq_close"))
            total_mv = safe_float(base.get("base_total_mv"))
            circ_mv = safe_float(base.get("base_circ_mv"))
            dv_ttm = safe_float(base.get("base_dv_ttm"))
            vol_window = list(volume_seed.get(sec.ts_code, []))
            for row in frame.itertuples(index=False):
                close = safe_float(row.close)
                pct_chg = safe_float(row.pctChg)
                if qfq is not None:
                    if pct_chg is not None:
                        qfq = qfq * (1.0 + pct_chg / 100.0)
                    elif close is not None and last_close and last_close > 0:
                        qfq = qfq * close / last_close
                ratio = close / last_close if close is not None and last_close and last_close > 0 else None
                if ratio is not None and total_mv is not None:
                    total_mv = total_mv * ratio
                if ratio is not None and circ_mv is not None:
                    circ_mv = circ_mv * ratio
                volume_hands = safe_float(row.volume)
                volume_hands = volume_hands / 100.0 if volume_hands is not None else None
                if volume_hands is not None:
                    recent = [v for v in vol_window[-5:] if v is not None and math.isfinite(v)]
                    volume_ratio = volume_hands / (sum(recent) / len(recent)) if recent and sum(recent) > 0 else None
                    vol_window.append(volume_hands)
                else:
                    volume_ratio = None
                amount_thousand = safe_float(row.amount)
                amount_thousand = amount_thousand / 1000.0 if amount_thousand is not None else None
                is_st = int(row.isST) if pd.notna(row.isST) else 0
                stock_name = sec.stock_name
                ohlcv_rows.append(
                    (
                        row.trade_date,
                        sec.ts_code,
                        stock_name,
                        safe_float(row.open),
                        safe_float(row.high),
                        safe_float(row.low),
                        close,
                        qfq,
                        safe_float(row.preclose),
                        pct_chg,
                        volume_hands,
                        amount_thousand,
                        None,
                        None,
                        "ST" if is_st else None,
                    )
                )
                valuation_rows.append(
                    (
                        row.trade_date,
                        sec.ts_code,
                        safe_float(row.peTTM),
                        safe_float(row.pbMRQ),
                        safe_float(row.psTTM),
                        dv_ttm,
                        total_mv,
                        circ_mv,
                        safe_float(row.turn),
                        safe_float(row.turn),
                        volume_ratio,
                    )
                )
                min_date = min(min_date or row.trade_date, row.trade_date)
                max_date = max(max_date or row.trade_date, row.trade_date)
                if close is not None:
                    last_close = close
            if batch_size > 0 and processed % batch_size == 0:
                flush_batch()
            if progress_every > 0 and processed % progress_every == 0:
                print(json.dumps({"processed": processed, "rows": rows_loaded + len(ohlcv_rows), "errors": len(errors), "skipped_complete": len(complete_codes)}, ensure_ascii=False), flush=True)
            if pause > 0:
                time.sleep(pause)
        flush_batch()
    finally:
        bs.logout()

    status = "ready" if rows_loaded else "blocked"
    message = (
        f"codes={processed}; rows={rows_loaded}; unsupported_or_empty={len(errors)}; skipped_complete={len(complete_codes)}; "
        f"dates={min_date}..{max_date}; base_date={base_date}; "
        "qfq_close compounded from BaoStock pctChg; total_mv/circ_mv price-rolled from base date; "
        "dv_ttm carried forward; volume_ratio derived from rolling volume; BJ symbols unsupported by BaoStock."
    )
    write_manifest(
        conn,
        "query_history_k_data_plus",
        "stock_ohlcv_daily/stock_valuation_daily",
        rows_loaded,
        dates[0],
        dates[-1],
        min_date,
        max_date,
        status,
        message,
    )
    for table in ("stock_ohlcv_daily", "stock_valuation_daily"):
        latest = conn.execute(f"select max(trade_date) from {table}").fetchone()[0]
        write_quality(
            conn,
            table,
            "trade_date",
            "coverage_end",
            "ready" if latest and latest >= dates[-1] else "blocked",
            f"max_date={latest}; required_end={dates[-1]}; source=baostock_public",
        )
    if errors:
        err_dir = DEFAULT_PROJECT_ROOT / "output" / "framework" / "data_pipeline"
        err_dir.mkdir(parents=True, exist_ok=True)
        (err_dir / "baostock_gap_errors.json").write_text(
            json.dumps(errors[:2000], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {
        "status": status,
        "rows": rows_loaded,
        "codes": processed,
        "unsupported_or_empty": len(errors),
        "min_date": min_date,
        "max_date": max_date,
        "calendar_trade_days": len(trade_dates),
        "message": message,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--start", default="20120101")
    parser.add_argument("--end", required=True)
    parser.add_argument("--pause", type=float, default=0.0)
    parser.add_argument("--max-codes", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=300)
    parser.add_argument("--out", default=str(DEFAULT_PROJECT_ROOT / "output" / "framework" / "data_pipeline" / "baostock_gap_result.json"))
    parser.add_argument("--no-output", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma busy_timeout=120000")
    try:
        result = fill_market_gap(
            conn,
            compact_date(args.start),
            compact_date(args.end),
            args.pause,
            args.max_codes,
            args.progress_every,
            args.batch_size,
        )
    finally:
        conn.close()
    if not args.no_output:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
