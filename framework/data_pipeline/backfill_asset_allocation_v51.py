"""Code- and name-validated local ETF backfill for asset allocation v5.1.

The upstream subject database historically classified the same exchange ETF
under changing fund-type labels (for example 511010.SH as ``CEF``/``债券型``
and 518880.SH as ``商品型``).  The v5.0 tool correctly refused those rows when
it required the literal value ``ETF``.  This wrapper broadens admission only
through an explicit code-specific type whitelist and a security-name token;
it preserves every v5.0 transaction, hash, before-image and rollback control.

Dry-run remains the default.  No production write occurs without ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from typing import Any, Mapping, Sequence

import backfill_asset_allocation_v5 as base


SOURCE_ADMISSION_V51: dict[str, dict[str, Any]] = {
    "510300.SH": {"fund_types": ("ETF", "股票型"), "name_token": "沪深300"},
    "511010.SH": {"fund_types": ("ETF", "CEF", "债券型"), "name_token": "国债"},
    "518880.SH": {"fund_types": ("ETF", "商品型"), "name_token": "黄金"},
    "159980.SZ": {"fund_types": ("ETF", "商品型"), "name_token": "有色"},
    "159981.SZ": {"fund_types": ("ETF", "商品型"), "name_token": "能源化工"},
    "159985.SZ": {"fund_types": ("ETF", "商品型"), "name_token": "豆粕"},
}

_PATCH_LOCK = threading.RLock()


def _source_predicate(codes: Sequence[str]) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    parameters: list[Any] = [base.START_DATE, base.END_DATE]
    for code in codes:
        rule = SOURCE_ADMISSION_V51.get(code)
        if rule is None:
            raise base.BackfillError(f"v5.1 source admission is undefined for: {code}")
        types = tuple(rule["fund_types"])
        placeholders = ",".join("?" for _ in types)
        clauses.append(f"(ts_code = ? and fund_type in ({placeholders}))")
        parameters.extend((code, *types))
    return (
        "trade_date between ? and ? and (" + " or ".join(clauses) + ") "
        "and close is not null and cast(close as real) > 0",
        tuple(parameters),
    )


def _normalise_v51(row: Mapping[str, Any]) -> dict[str, Any]:
    trade_date = str(row["trade_date"]).strip()
    code = str(row["ts_code"]).strip().upper()
    rule = SOURCE_ADMISSION_V51.get(code)
    if rule is None:
        raise base.BackfillError(f"unexpected code in v5.1 source selection: {code}")
    fund_type = str(row["fund_type"] or "").strip()
    if fund_type not in rule["fund_types"]:
        raise base.BackfillError(f"unapproved fund_type for {code}: {fund_type}")
    fund_name = str(row["fund_name"] or "").strip()
    if str(rule["name_token"]) not in fund_name:
        raise base.BackfillError(
            f"security-name validation failed for {code}: expected token {rule['name_token']}"
        )
    key = (trade_date, code)
    if not base._DATE_RE.fullmatch(trade_date):
        raise base.BackfillError(f"invalid trade_date for source row: {trade_date!r}")
    output: dict[str, Any] = {
        "trade_date": trade_date,
        "ts_code": code,
        "fund_name": fund_name,
        "fund_type": "ETF",
    }
    for field in base.NUMERIC_COLUMNS:
        output[field] = base._optional_real(row[field], field, key)
    if output["close"] is None or output["close"] <= 0:
        raise base.BackfillError(f"close must be finite and positive for {code} on {trade_date}")
    return {column: output[column] for column in base.ROW_COLUMNS}


def _fetch_source_rows_v51(
    connection: sqlite3.Connection,
    codes: Sequence[str],
) -> list[dict[str, Any]]:
    where, parameters = _source_predicate(codes)
    rows = connection.execute(
        f"select {', '.join(base.ROW_COLUMNS)} from {base.SOURCE_TABLE} "
        f"where {where} order by trade_date, ts_code",
        parameters,
    ).fetchall()
    return [_normalise_v51(row) for row in rows]


def _source_duplicate_count_v51(
    connection: sqlite3.Connection,
    table: str,
    codes: Sequence[str],
) -> int:
    if table != base.SOURCE_TABLE:
        raise base.BackfillError(f"unexpected v5.1 source table: {table}")
    where, parameters = _source_predicate(codes)
    row = connection.execute(
        f"""
        select count(*) from (
          select trade_date, ts_code
          from {table}
          where {where}
          group by trade_date, ts_code
          having count(*) > 1
        )
        """,
        parameters,
    ).fetchone()
    return int(row[0])


@contextmanager
def _patched_source_admission() -> Any:
    with _PATCH_LOCK:
        original_fetch = base._fetch_source_rows
        original_duplicate = base._duplicate_count

        def duplicate(
            connection: sqlite3.Connection,
            table: str,
            codes: Sequence[str],
            source_predicate: bool = False,
        ) -> int:
            if source_predicate:
                return _source_duplicate_count_v51(connection, table, codes)
            return original_duplicate(connection, table, codes, source_predicate=False)

        base._fetch_source_rows = _fetch_source_rows_v51
        base._duplicate_count = duplicate
        try:
            yield
        finally:
            base._fetch_source_rows = original_fetch
            base._duplicate_count = original_duplicate


def inspect_backfill_v51(
    source_db: str | os.PathLike[str],
    target_db: str | os.PathLike[str],
    codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    selected = base._normalise_codes(codes)
    with _patched_source_admission():
        result = base.inspect_backfill(source_db, target_db, selected)
    result["source_admission_version"] = "5.1-code-type-name-validated"
    result["source_admission"] = {
        code: {
            "fund_types": list(SOURCE_ADMISSION_V51[code]["fund_types"]),
            "name_token": SOURCE_ADMISSION_V51[code]["name_token"],
        }
        for code in selected
    }
    return result


def apply_backfill_v51(
    source_db: str | os.PathLike[str],
    target_db: str | os.PathLike[str],
    codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    selected = base._normalise_codes(codes)
    with _patched_source_admission():
        result = base.apply_backfill(source_db, target_db, selected)
    result["source_admission_version"] = "5.1-code-type-name-validated"
    result["source_admission"] = {
        code: {
            "fund_types": list(SOURCE_ADMISSION_V51[code]["fund_types"]),
            "name_token": SOURCE_ADMISSION_V51[code]["name_token"],
        }
        for code in selected
    }
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", default=os.environ.get("SUBJECT_DB_PATH"))
    parser.add_argument("--target-db", default=os.environ.get("RESEARCH_WAREHOUSE_DB"))
    parser.add_argument("--codes", nargs="+", default=list(base.DEFAULT_CODES))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback-run")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.target_db:
            raise base.BackfillError("--target-db or RESEARCH_WAREHOUSE_DB is required")
        if args.rollback_run:
            result = base.rollback_run(args.target_db, args.rollback_run)
        else:
            if not args.source_db:
                raise base.BackfillError("--source-db or SUBJECT_DB_PATH is required")
            result = (
                apply_backfill_v51(args.source_db, args.target_db, args.codes)
                if args.apply
                else inspect_backfill_v51(args.source_db, args.target_db, args.codes)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (base.BackfillError, sqlite3.Error, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
