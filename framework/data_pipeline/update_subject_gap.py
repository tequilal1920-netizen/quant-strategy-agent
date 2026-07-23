import argparse
import importlib.util
import json
import os
import sqlite3
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_TABLES = [
    "stock_market_daily",
    "fund_daily",
    "index_market_daily",
    "index_member_weight",
    "macro_monthly_cn",
]


DATE_COLUMNS = {
    "calendar_cn": "calendar_date",
    "stock_market_daily": "trade_date",
    "fund_daily": "trade_date",
    "index_market_daily": "trade_date",
    "index_member_weight": "trade_date",
    "macro_monthly_cn": "month",
}


def load_subject_core(core_path):
    spec = importlib.util.spec_from_file_location("subject_database_core_locked", core_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load subject core from {core_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_exists(conn, table):
    return conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone() is not None


def summarize_table(conn, table, date_col, start_date, end_date):
    if not table_exists(conn, table):
        return {"table": table, "status": "missing"}
    row = conn.execute(
        f"select min({date_col}), max({date_col}), count(*) from {table}"
    ).fetchone()
    recent = conn.execute(
        f"select count(*) from {table} where {date_col} between ? and ?",
        (start_date, end_date),
    ).fetchone()[0]
    return {
        "table": table,
        "status": "ready",
        "min_date": row[0],
        "max_date": row[1],
        "rows": row[2],
        "rows_in_requested_window": recent,
    }


def summarize_index_weight(conn, start_date, end_date, index_codes):
    out = {}
    if not table_exists(conn, "index_member_weight"):
        return {code: {"status": "missing"} for code in index_codes}
    for code in index_codes:
        row = conn.execute(
            """
            select min(trade_date), max(trade_date), count(*), count(distinct con_code)
            from index_member_weight
            where index_code=?
            """,
            (code,),
        ).fetchone()
        recent = conn.execute(
            """
            select count(*), count(distinct con_code)
            from index_member_weight
            where index_code=? and trade_date between ? and ?
            """,
            (code, start_date, end_date),
        ).fetchone()
        out[code] = {
            "min_date": row[0],
            "max_date": row[1],
            "rows": row[2],
            "distinct_codes": row[3],
            "rows_in_requested_window": recent[0],
            "distinct_codes_in_requested_window": recent[1],
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject-dir", default=os.environ.get("SUBJECT_DATABASE_DIR", ""), help="Source project database directory; may also be set via SUBJECT_DATABASE_DIR.")
    parser.add_argument("--start-date", default="20260613")
    parser.add_argument("--end-date", default="20260630")
    parser.add_argument("--tables", nargs="*", default=DEFAULT_TABLES)
    parser.add_argument("--add-index-code", action="append", default=["932000.CSI"])
    parser.add_argument("--out", default=str(DEFAULT_PROJECT_ROOT / "output" / "framework" / "data_pipeline" / "subject_gap_update_result.json"))
    args = parser.parse_args()
    if not args.subject_dir:
        parser.error("--subject-dir is required or set SUBJECT_DATABASE_DIR.")

    subject_dir = Path(args.subject_dir)
    core_path = subject_dir / "core.py"
    db_path = subject_dir / "database.db"
    if not core_path.exists():
        raise FileNotFoundError(core_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    os.environ["DB_BUILD_MODE"] = "incremental"
    os.environ["DB_AUTO_RUN_PIPELINE"] = "0"

    core = load_subject_core(core_path)
    core.BUILD_MODE = "incremental"
    core.TODAY = args.end_date
    core.TODAY_DASH = f"{args.end_date[:4]}-{args.end_date[4:6]}-{args.end_date[6:]}"
    core.CALENDAR_END_DATE = args.end_date

    for code in args.add_index_code or []:
        if code not in core.INDEX_WEIGHT_CORE:
            core.INDEX_WEIGHT_CORE.append(code)
        if code not in core.INDEX_PRICE_CORE:
            core.INDEX_PRICE_CORE.append(code)

    results_df = core.run_selected_tables(args.tables)
    results = json.loads(results_df.to_json(orient="records", force_ascii=False))

    conn = sqlite3.connect(db_path)
    try:
        table_summary = {
            table: summarize_table(conn, table, DATE_COLUMNS[table], args.start_date, args.end_date)
            for table in DATE_COLUMNS
            if table in set(args.tables) or table in {"calendar_cn"}
        }
        index_summary = summarize_index_weight(
            conn,
            args.start_date,
            args.end_date,
            sorted(set(["000906.SH"] + list(args.add_index_code or []))),
        )
    finally:
        conn.close()

    output = {
        "status": "ok",
        "subject_db": str(db_path),
        "locked_end_date": args.end_date,
        "tables_requested": args.tables,
        "added_index_codes": args.add_index_code,
        "subject_results": results,
        "table_summary": table_summary,
        "index_weight_summary": index_summary,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
