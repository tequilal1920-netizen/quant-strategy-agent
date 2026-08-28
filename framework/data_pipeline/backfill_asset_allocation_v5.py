"""Governed local ETF backfill for the asset-allocation v5 research model.

The command is deliberately narrow:

* read ``fund_daily`` from a local subject database;
* select a fixed, explicit ETF universe and the 2012-01-01..2026-06-30 window;
* upsert only those rows into ``etf_ohlcv_daily``;
* record before images, coverage and deterministic hashes so a run can be
  rolled back safely.

Dry-run is the default.  A write requires ``--apply``.  No credentials are
accepted or serialized by this module.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SOURCE_TABLE = "fund_daily"
TARGET_TABLE = "etf_ohlcv_daily"
MANIFEST_TABLE = "source_manifest"
AUDIT_RUN_TABLE = "asset_allocation_backfill_run_v5"
AUDIT_ROW_TABLE = "asset_allocation_backfill_row_v5"
SOURCE_NAME = "local_subject_sqlite"
START_DATE = "20120101"
END_DATE = "20260630"
DEFAULT_CODES = (
    "510300.SH",
    "511010.SH",
    "518880.SH",
    "159980.SZ",
    "159981.SZ",
    "159985.SZ",
)
ROW_COLUMNS = (
    "trade_date",
    "ts_code",
    "fund_name",
    "open",
    "high",
    "low",
    "close",
    "pct_chg",
    "vol",
    "amount",
    "fund_type",
)
NUMERIC_COLUMNS = ("open", "high", "low", "close", "pct_chg", "vol", "amount")
MANIFEST_COLUMNS = (
    "source_name",
    "source_path",
    "source_table",
    "target_table",
    "start_date",
    "end_date",
    "rows_loaded",
    "min_date",
    "max_date",
    "frequency",
    "update_mode",
    "quota_policy",
    "status",
    "message",
    "updated_at",
)
_DATE_RE = re.compile(r"^[0-9]{8}$")


class BackfillError(RuntimeError):
    """Raised when a governance or data-quality condition blocks a run."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _new_run_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"asset-allocation-v5-{stamp}-{uuid.uuid4().hex[:10]}"


def _resolve_existing_file(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise BackfillError(f"{label} does not exist or is not a file: {path}")
    return path


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma query_only = on")
    connection.execute("pragma busy_timeout = 5000")
    return connection


def _connect_write(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    connection.execute("pragma busy_timeout = 5000")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone() is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'pragma table_info("{table}")')}


def _require_columns(
    connection: sqlite3.Connection,
    table: str,
    required: Iterable[str],
) -> None:
    if not _table_exists(connection, table):
        raise BackfillError(f"required table is missing: {table}")
    missing = sorted(set(required) - _table_columns(connection, table))
    if missing:
        raise BackfillError(f"{table} is missing required columns: {', '.join(missing)}")


def _normalise_codes(values: Sequence[str] | None) -> tuple[str, ...]:
    raw = values or DEFAULT_CODES
    flattened: list[str] = []
    for value in raw:
        flattened.extend(part.strip().upper() for part in str(value).split(","))
    codes = tuple(dict.fromkeys(code for code in flattened if code))
    if not codes:
        raise BackfillError("at least one ETF code is required")
    invalid = [code for code in codes if not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code)]
    if invalid:
        raise BackfillError(f"invalid ETF code(s): {', '.join(invalid)}")
    return codes


def _optional_real(value: Any, field: str, key: tuple[str, str]) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BackfillError(f"non-numeric {field} for {key[1]} on {key[0]}") from exc
    if not math.isfinite(number):
        raise BackfillError(f"non-finite {field} for {key[1]} on {key[0]}")
    return number


def _normalise_source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    trade_date = str(row["trade_date"]).strip()
    ts_code = str(row["ts_code"]).strip().upper()
    key = (trade_date, ts_code)
    if not _DATE_RE.fullmatch(trade_date):
        raise BackfillError(f"invalid trade_date for source row: {trade_date!r}")
    if str(row["fund_type"]).strip() != "ETF":
        raise BackfillError(f"non-ETF row escaped the source predicate: {ts_code} {trade_date}")
    result: dict[str, Any] = {
        "trade_date": trade_date,
        "ts_code": ts_code,
        "fund_name": None if row["fund_name"] is None else str(row["fund_name"]),
        "fund_type": "ETF",
    }
    for field in NUMERIC_COLUMNS:
        result[field] = _optional_real(row[field], field, key)
    if result["close"] is None or result["close"] <= 0:
        raise BackfillError(f"close must be finite and positive for {ts_code} on {trade_date}")
    return {column: result[column] for column in ROW_COLUMNS}


def _row_dict(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return {column: row[column] for column in ROW_COLUMNS}


def _canonical_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BackfillError("cannot hash a non-finite database value")
        return format(value, ".17g")
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return value


def _canonical_row(row: Mapping[str, Any]) -> list[Any]:
    return [_canonical_value(row.get(column)) for column in ROW_COLUMNS]


def _row_json(row: Mapping[str, Any]) -> str:
    payload = {column: _canonical_value(row.get(column)) for column in ROW_COLUMNS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_hash(row: Mapping[str, Any]) -> str:
    payload = json.dumps(_canonical_row(row), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rows_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: (str(row["trade_date"]), str(row["ts_code"])))
    payload = json.dumps(
        [_canonical_row(row) for row in ordered],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coverage(rows: Sequence[Mapping[str, Any]], codes: Sequence[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        dates = sorted(str(row["trade_date"]) for row in rows if row["ts_code"] == code)
        result[code] = {
            "rows": len(dates),
            "min_date": dates[0] if dates else None,
            "max_date": dates[-1] if dates else None,
        }
    return result


def _scope_where(codes: Sequence[str]) -> tuple[str, tuple[Any, ...]]:
    placeholders = ",".join("?" for _ in codes)
    return (
        f"trade_date between ? and ? and ts_code in ({placeholders})",
        (START_DATE, END_DATE, *codes),
    )


def _fetch_scope_rows(
    connection: sqlite3.Connection,
    table: str,
    codes: Sequence[str],
) -> list[dict[str, Any]]:
    where, params = _scope_where(codes)
    columns = ", ".join(ROW_COLUMNS)
    rows = connection.execute(
        f"select {columns} from {table} where {where} order by trade_date, ts_code",
        params,
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _duplicate_count(
    connection: sqlite3.Connection,
    table: str,
    codes: Sequence[str],
    source_predicate: bool = False,
) -> int:
    where, params = _scope_where(codes)
    if source_predicate:
        where += " and fund_type = 'ETF' and close is not null and cast(close as real) > 0"
    row = connection.execute(
        f"""
        select count(*)
        from (
          select trade_date, ts_code
          from {table}
          where {where}
          group by trade_date, ts_code
          having count(*) > 1
        )
        """,
        params,
    ).fetchone()
    return int(row[0])


def _fetch_source_rows(
    connection: sqlite3.Connection,
    codes: Sequence[str],
) -> list[dict[str, Any]]:
    where, params = _scope_where(codes)
    columns = ", ".join(ROW_COLUMNS)
    rows = connection.execute(
        f"""
        select {columns}
        from {SOURCE_TABLE}
        where {where}
          and fund_type = 'ETF'
          and close is not null
          and cast(close as real) > 0
        order by trade_date, ts_code
        """,
        params,
    ).fetchall()
    return [_normalise_source_row(row) for row in rows]


def _rows_by_key(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["trade_date"]), str(row["ts_code"])): dict(row)
        for row in rows
    }


def _merge_projected_rows(
    pre_rows: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged = _rows_by_key(pre_rows)
    merged.update(_rows_by_key(source_rows))
    return [merged[key] for key in sorted(merged)]


def _plan_from_connections(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    codes: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    _require_columns(source, SOURCE_TABLE, ROW_COLUMNS)
    _require_columns(target, TARGET_TABLE, ROW_COLUMNS)
    source_duplicates = _duplicate_count(source, SOURCE_TABLE, codes, source_predicate=True)
    target_duplicates = _duplicate_count(target, TARGET_TABLE, codes)
    if source_duplicates:
        raise BackfillError(f"source duplicate-key groups in governed scope: {source_duplicates}")
    if target_duplicates:
        raise BackfillError(f"target duplicate-key groups in governed scope: {target_duplicates}")

    source_rows = _fetch_source_rows(source, codes)
    pre_rows = _fetch_scope_rows(target, TARGET_TABLE, codes)
    source_by_key = _rows_by_key(source_rows)
    pre_by_key = _rows_by_key(pre_rows)
    inserted = 0
    updated = 0
    unchanged = 0
    for key, row in source_by_key.items():
        before = pre_by_key.get(key)
        if before is None:
            inserted += 1
        elif _row_hash(before) == _row_hash(row):
            unchanged += 1
        else:
            updated += 1
    projected = _merge_projected_rows(pre_rows, source_rows)
    source_coverage = _coverage(source_rows, codes)
    missing_codes = [code for code in codes if source_coverage[code]["rows"] == 0]
    plan = {
        "status": "dry_run",
        "write_performed": False,
        "apply_required": True,
        "source_table": SOURCE_TABLE,
        "target_table": TARGET_TABLE,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "codes": list(codes),
        "source_rows": len(source_rows),
        "target_rows_before": len(pre_rows),
        "projected_target_rows_after": len(projected),
        "inserted_rows": inserted,
        "updated_rows": updated,
        "unchanged_rows": unchanged,
        "missing_source_codes": missing_codes,
        "eligible_for_apply": not missing_codes,
        "duplicate_key_checks": {"source": source_duplicates, "target": target_duplicates},
        "source_coverage": source_coverage,
        "pre_coverage": _coverage(pre_rows, codes),
        "projected_post_coverage": _coverage(projected, codes),
        "source_hash": _rows_hash(source_rows),
        "pre_hash": _rows_hash(pre_rows),
        "projected_post_hash": _rows_hash(projected),
    }
    return plan, source_rows, pre_rows


def inspect_backfill(
    source_db: str | os.PathLike[str],
    target_db: str | os.PathLike[str],
    codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a mutation-free plan for the governed backfill."""

    source_path = _resolve_existing_file(source_db, "source database")
    target_path = _resolve_existing_file(target_db, "target database")
    selected_codes = _normalise_codes(codes)
    with closing(_connect_read_only(source_path)) as source, closing(_connect_read_only(target_path)) as target:
        plan, _, _ = _plan_from_connections(source, target, selected_codes)
    return plan


def _create_audit_tables(connection: sqlite3.Connection) -> None:
    # ``executescript`` issues an implicit commit in Python's sqlite3 module.
    # Individual DDL statements keep all writes in the caller's transaction.
    connection.execute(
        f"""
        create table if not exists {AUDIT_RUN_TABLE} (
          run_id text primary key,
          status text not null,
          source_path text not null,
          source_table text not null,
          target_table text not null,
          start_date text not null,
          end_date text not null,
          codes_json text not null,
          source_rows integer not null,
          inserted_rows integer not null,
          updated_rows integer not null,
          unchanged_rows integer not null,
          source_hash text not null,
          pre_hash text not null,
          post_hash text not null,
          pre_coverage_json text not null,
          post_coverage_json text not null,
          manifest_source_name text not null,
          source_manifest_before_json text,
          source_manifest_after_json text not null,
          created_at text not null,
          committed_at text not null,
          rolled_back_at text,
          rollback_hash text,
          message text
        )
        """
    )
    connection.execute(
        f"""
        create table if not exists {AUDIT_ROW_TABLE} (
          run_id text not null,
          trade_date text not null,
          ts_code text not null,
          before_exists integer not null check (before_exists in (0, 1)),
          before_row_json text,
          after_row_hash text not null,
          primary key (run_id, trade_date, ts_code),
          foreign key (run_id) references {AUDIT_RUN_TABLE}(run_id)
        )
        """
    )


def _upsert_target_rows(
    connection: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    placeholders = ", ".join("?" for _ in ROW_COLUMNS)
    updates = ", ".join(
        f"{column} = excluded.{column}" for column in ROW_COLUMNS if column not in {"trade_date", "ts_code"}
    )
    connection.executemany(
        f"""
        insert into {TARGET_TABLE} ({', '.join(ROW_COLUMNS)})
        values ({placeholders})
        on conflict(trade_date, ts_code) do update set {updates}
        """,
        [tuple(row[column] for column in ROW_COLUMNS) for row in rows],
    )


def _manifest_row(connection: sqlite3.Connection) -> dict[str, Any] | None:
    _require_columns(connection, MANIFEST_TABLE, MANIFEST_COLUMNS)
    row = connection.execute(
        f"""
        select {', '.join(MANIFEST_COLUMNS)}
        from {MANIFEST_TABLE}
        where source_name = ? and source_table = ? and target_table = ?
        """,
        (SOURCE_NAME, SOURCE_TABLE, TARGET_TABLE),
    ).fetchone()
    return None if row is None else {column: row[column] for column in MANIFEST_COLUMNS}


def _manifest_json(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return None
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _upsert_manifest(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    placeholders = ", ".join("?" for _ in MANIFEST_COLUMNS)
    updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in MANIFEST_COLUMNS
        if column not in {"source_name", "source_table", "target_table"}
    )
    connection.execute(
        f"""
        insert into {MANIFEST_TABLE} ({', '.join(MANIFEST_COLUMNS)})
        values ({placeholders})
        on conflict(source_name, source_table, target_table) do update set {updates}
        """,
        tuple(row[column] for column in MANIFEST_COLUMNS),
    )


def apply_backfill(
    source_db: str | os.PathLike[str],
    target_db: str | os.PathLike[str],
    codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Apply one governed backfill in a single target-database transaction."""

    source_path = _resolve_existing_file(source_db, "source database")
    target_path = _resolve_existing_file(target_db, "target database")
    selected_codes = _normalise_codes(codes)

    with closing(_connect_read_only(source_path)) as source:
        _require_columns(source, SOURCE_TABLE, ROW_COLUMNS)
        source_duplicates = _duplicate_count(source, SOURCE_TABLE, selected_codes, source_predicate=True)
        if source_duplicates:
            raise BackfillError(f"source duplicate-key groups in governed scope: {source_duplicates}")
        source_rows = _fetch_source_rows(source, selected_codes)

    run_id = _new_run_id()
    connection = _connect_write(target_path)
    try:
        connection.execute("begin immediate")
        _require_columns(connection, TARGET_TABLE, ROW_COLUMNS)
        _require_columns(connection, MANIFEST_TABLE, MANIFEST_COLUMNS)
        target_duplicates = _duplicate_count(connection, TARGET_TABLE, selected_codes)
        if target_duplicates:
            raise BackfillError(f"target duplicate-key groups in governed scope: {target_duplicates}")

        pre_rows = _fetch_scope_rows(connection, TARGET_TABLE, selected_codes)
        source_by_key = _rows_by_key(source_rows)
        pre_by_key = _rows_by_key(pre_rows)
        source_coverage = _coverage(source_rows, selected_codes)
        missing_codes = [code for code in selected_codes if source_coverage[code]["rows"] == 0]
        if missing_codes:
            raise BackfillError(
                "apply blocked because source coverage is empty for: " + ", ".join(missing_codes)
            )

        inserted = sum(key not in pre_by_key for key in source_by_key)
        updated = sum(
            key in pre_by_key and _row_hash(pre_by_key[key]) != _row_hash(row)
            for key, row in source_by_key.items()
        )
        unchanged = len(source_rows) - inserted - updated
        projected = _merge_projected_rows(pre_rows, source_rows)
        projected_hash = _rows_hash(projected)
        pre_hash = _rows_hash(pre_rows)
        pre_coverage = _coverage(pre_rows, selected_codes)

        manifest_before = _manifest_row(connection)
        committed_at = _utc_now()
        manifest_after: dict[str, Any] = {
            "source_name": SOURCE_NAME,
            "source_path": str(source_path),
            "source_table": SOURCE_TABLE,
            "target_table": TARGET_TABLE,
            "start_date": START_DATE,
            "end_date": END_DATE,
            "rows_loaded": len(source_rows),
            "min_date": min(row["trade_date"] for row in source_rows),
            "max_date": max(row["trade_date"] for row in source_rows),
            "frequency": "daily",
            "update_mode": "targeted_upsert_asset_allocation_v5",
            "quota_policy": "local_db_first_no_paid_api_call",
            "status": "ready",
            "message": json.dumps(
                {
                    "run_id": run_id,
                    "codes": list(selected_codes),
                    "source_hash": _rows_hash(source_rows),
                    "pre_hash": pre_hash,
                    "post_hash": projected_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "updated_at": committed_at,
        }

        _create_audit_tables(connection)
        _upsert_target_rows(connection, source_rows)
        post_rows = _fetch_scope_rows(connection, TARGET_TABLE, selected_codes)
        post_hash = _rows_hash(post_rows)
        if post_hash != projected_hash:
            raise BackfillError("post-write hash does not match the dry-run projection")
        post_coverage = _coverage(post_rows, selected_codes)

        connection.execute(
            f"""
            insert into {AUDIT_RUN_TABLE}
            (run_id, status, source_path, source_table, target_table, start_date, end_date, codes_json,
             source_rows, inserted_rows, updated_rows, unchanged_rows, source_hash, pre_hash, post_hash,
             pre_coverage_json, post_coverage_json, manifest_source_name, source_manifest_before_json,
             source_manifest_after_json, created_at, committed_at, message)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "committed",
                str(source_path),
                SOURCE_TABLE,
                TARGET_TABLE,
                START_DATE,
                END_DATE,
                json.dumps(list(selected_codes), separators=(",", ":")),
                len(source_rows),
                inserted,
                updated,
                unchanged,
                _rows_hash(source_rows),
                pre_hash,
                post_hash,
                json.dumps(pre_coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                json.dumps(post_coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                SOURCE_NAME,
                _manifest_json(manifest_before),
                _manifest_json(manifest_after),
                committed_at,
                committed_at,
                "targeted local ETF backfill; reversible before images retained",
            ),
        )
        post_by_key = _rows_by_key(post_rows)
        connection.executemany(
            f"""
            insert into {AUDIT_ROW_TABLE}
            (run_id, trade_date, ts_code, before_exists, before_row_json, after_row_hash)
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    key[0],
                    key[1],
                    1 if key in pre_by_key else 0,
                    _row_json(pre_by_key[key]) if key in pre_by_key else None,
                    _row_hash(post_by_key[key]),
                )
                for key in sorted(source_by_key)
            ],
        )
        _upsert_manifest(connection, manifest_after)
        connection.execute("commit")
    except Exception:
        if connection.in_transaction:
            connection.execute("rollback")
        raise
    finally:
        connection.close()

    return {
        "status": "committed",
        "write_performed": True,
        "run_id": run_id,
        "source_table": SOURCE_TABLE,
        "target_table": TARGET_TABLE,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "codes": list(selected_codes),
        "source_rows": len(source_rows),
        "inserted_rows": inserted,
        "updated_rows": updated,
        "unchanged_rows": unchanged,
        "source_coverage": source_coverage,
        "pre_coverage": pre_coverage,
        "post_coverage": post_coverage,
        "source_hash": _rows_hash(source_rows),
        "pre_hash": pre_hash,
        "post_hash": post_hash,
        "rollback_command": f"--target-db <warehouse.db> --rollback-run {run_id}",
    }


def _decode_before_row(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if set(payload) != set(ROW_COLUMNS):
        raise BackfillError("audit before image has an invalid column set")
    result = {column: payload[column] for column in ROW_COLUMNS}
    for column in NUMERIC_COLUMNS:
        if result[column] is not None:
            result[column] = float(result[column])
    return result


def rollback_run(
    target_db: str | os.PathLike[str],
    run_id: str,
) -> dict[str, Any]:
    """Safely restore the exact target/manifest state captured before ``run_id``."""

    target_path = _resolve_existing_file(target_db, "target database")
    connection = _connect_write(target_path)
    try:
        connection.execute("begin immediate")
        _require_columns(connection, TARGET_TABLE, ROW_COLUMNS)
        _require_columns(connection, MANIFEST_TABLE, MANIFEST_COLUMNS)
        if not _table_exists(connection, AUDIT_RUN_TABLE) or not _table_exists(connection, AUDIT_ROW_TABLE):
            raise BackfillError("asset-allocation v5 backfill audit tables are missing")
        run = connection.execute(
            f"select * from {AUDIT_RUN_TABLE} where run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise BackfillError(f"unknown backfill run: {run_id}")
        if run["status"] == "rolled_back":
            connection.execute("rollback")
            return {
                "status": "already_rolled_back",
                "write_performed": False,
                "run_id": run_id,
                "rollback_hash": run["rollback_hash"],
            }
        if run["status"] != "committed":
            raise BackfillError(f"run is not rollback-eligible: {run['status']}")
        if run["source_table"] != SOURCE_TABLE or run["target_table"] != TARGET_TABLE:
            raise BackfillError("audit run does not target the governed source/target pair")
        if run["start_date"] != START_DATE or run["end_date"] != END_DATE:
            raise BackfillError("audit run date scope does not match this utility")

        codes = _normalise_codes(json.loads(run["codes_json"]))
        if _duplicate_count(connection, TARGET_TABLE, codes):
            raise BackfillError("target duplicate keys block a safe rollback")
        current_rows = _fetch_scope_rows(connection, TARGET_TABLE, codes)
        current_hash = _rows_hash(current_rows)
        if current_hash != run["post_hash"]:
            raise BackfillError(
                "target scope changed after the run; refusing to overwrite later data during rollback"
            )

        manifest_current = _manifest_row(connection)
        if _manifest_json(manifest_current) != run["source_manifest_after_json"]:
            raise BackfillError(
                "source_manifest changed after the run; refusing to overwrite later metadata"
            )

        audit_rows = connection.execute(
            f"select * from {AUDIT_ROW_TABLE} where run_id = ? order by trade_date, ts_code",
            (run_id,),
        ).fetchall()
        if len(audit_rows) != int(run["source_rows"]):
            raise BackfillError("audit row count does not match the committed run")
        current_by_key = _rows_by_key(current_rows)
        for audit in audit_rows:
            key = (audit["trade_date"], audit["ts_code"])
            current = current_by_key.get(key)
            if current is None or _row_hash(current) != audit["after_row_hash"]:
                raise BackfillError(f"target row changed after the run: {key[1]} {key[0]}")

        rows_to_restore = [
            _decode_before_row(audit["before_row_json"])
            for audit in audit_rows
            if int(audit["before_exists"]) == 1
        ]
        if rows_to_restore:
            _upsert_target_rows(connection, rows_to_restore)
        connection.executemany(
            f"delete from {TARGET_TABLE} where trade_date = ? and ts_code = ?",
            [
                (audit["trade_date"], audit["ts_code"])
                for audit in audit_rows
                if int(audit["before_exists"]) == 0
            ],
        )

        manifest_before_json = run["source_manifest_before_json"]
        if manifest_before_json is None:
            connection.execute(
                f"""
                delete from {MANIFEST_TABLE}
                where source_name = ? and source_table = ? and target_table = ?
                """,
                (SOURCE_NAME, SOURCE_TABLE, TARGET_TABLE),
            )
        else:
            _upsert_manifest(connection, json.loads(manifest_before_json))

        restored_rows = _fetch_scope_rows(connection, TARGET_TABLE, codes)
        restored_hash = _rows_hash(restored_rows)
        if restored_hash != run["pre_hash"]:
            raise BackfillError("rollback verification hash does not match the captured pre-run hash")
        rolled_back_at = _utc_now()
        connection.execute(
            f"""
            update {AUDIT_RUN_TABLE}
            set status = 'rolled_back', rolled_back_at = ?, rollback_hash = ?
            where run_id = ? and status = 'committed'
            """,
            (rolled_back_at, restored_hash, run_id),
        )
        connection.execute("commit")
    except Exception:
        if connection.in_transaction:
            connection.execute("rollback")
        raise
    finally:
        connection.close()

    return {
        "status": "rolled_back",
        "write_performed": True,
        "run_id": run_id,
        "restored_rows": len(audit_rows),
        "rollback_hash": restored_hash,
        "pre_coverage": json.loads(run["pre_coverage_json"]),
        "post_coverage_before_rollback": json.loads(run["post_coverage_json"]),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply the governed local ETF backfill for asset allocation v5."
    )
    parser.add_argument(
        "--source-db",
        default=os.environ.get("SUBJECT_DB_PATH"),
        help="Local subject SQLite database (or env SUBJECT_DB_PATH). Required except for rollback.",
    )
    parser.add_argument(
        "--target-db",
        default=os.environ.get("RESEARCH_WAREHOUSE_DB"),
        help="Research warehouse SQLite database (or env RESEARCH_WAREHOUSE_DB).",
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        default=list(DEFAULT_CODES),
        help="Explicit ETF codes; defaults to the governed six-code source universe.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Execute the targeted upsert. Without this flag the command is read-only.",
    )
    mode.add_argument(
        "--rollback-run",
        metavar="RUN_ID",
        help="Restore a committed run after verifying that no later data changed.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if not args.target_db:
            raise BackfillError("--target-db or RESEARCH_WAREHOUSE_DB is required")
        if args.rollback_run:
            result = rollback_run(args.target_db, args.rollback_run)
        else:
            if not args.source_db:
                raise BackfillError("--source-db or SUBJECT_DB_PATH is required")
            if args.apply:
                result = apply_backfill(args.source_db, args.target_db, args.codes)
            else:
                result = inspect_backfill(args.source_db, args.target_db, args.codes)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (BackfillError, sqlite3.Error, OSError, ValueError) as exc:
        print(
            json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
