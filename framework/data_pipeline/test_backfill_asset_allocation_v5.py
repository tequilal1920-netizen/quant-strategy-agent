import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import backfill_asset_allocation_v5 as backfill


ROW_DDL = """
(
  trade_date text not null,
  ts_code text not null,
  fund_name text,
  open real,
  high real,
  low real,
  close real,
  pct_chg real,
  vol real,
  amount real,
  fund_type text
)
"""

MANIFEST_DDL = """
create table source_manifest (
  source_name text not null,
  source_path text not null,
  source_table text not null,
  target_table text not null,
  start_date text not null,
  end_date text not null,
  rows_loaded integer not null default 0,
  min_date text,
  max_date text,
  frequency text not null,
  update_mode text not null,
  quota_policy text not null,
  status text not null,
  message text,
  updated_at text not null,
  primary key (source_name, source_table, target_table)
)
"""


def row(
    trade_date,
    ts_code,
    close,
    *,
    fund_type="ETF",
    fund_name=None,
    scale=1.0,
):
    fund_name = fund_name or f"ETF-{ts_code}"
    return (
        trade_date,
        ts_code,
        fund_name,
        close * 0.99 * scale,
        close * 1.01 * scale,
        close * 0.98 * scale,
        close,
        0.25,
        1000.0,
        5000.0,
        fund_type,
    )


def fetch_all(connection, table):
    order_by = (
        "trade_date, ts_code, rowid"
        if table == "etf_ohlcv_daily"
        else "source_name, source_table, target_table"
    )
    return connection.execute(f"select * from {table} order by {order_by}").fetchall()


@contextlib.contextmanager
def db_connection(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class BackfillAssetAllocationV5Test(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.source_path = root / "subject.db"
        self.target_path = root / "warehouse.db"
        self._create_source()
        self._create_target()

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_source(self, *, primary_key=True):
        with db_connection(self.source_path) as connection:
            ddl = ROW_DDL.rstrip().rstrip(")")
            if primary_key:
                ddl += ", primary key (trade_date, ts_code))"
            else:
                ddl += ")"
            connection.execute(f"create table fund_daily {ddl}")
            source_rows = [
                row(f"2020010{index}", code, 10.0 + index)
                for index, code in enumerate(backfill.DEFAULT_CODES, start=1)
            ]
            connection.executemany(
                "insert into fund_daily values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                source_rows,
            )
            # These rows must never enter the governed selection.
            connection.executemany(
                "insert into fund_daily values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    row("20200201", backfill.DEFAULT_CODES[0], 99.0, fund_type="LOF"),
                    row("20200202", backfill.DEFAULT_CODES[1], 0.0),
                    row("20111230", backfill.DEFAULT_CODES[2], 88.0),
                    row("20200204", "588000.SH", 77.0),
                ],
            )

    def _create_target(self, *, primary_key=True):
        with db_connection(self.target_path) as connection:
            ddl = ROW_DDL.rstrip().rstrip(")")
            if primary_key:
                ddl += ", primary key (trade_date, ts_code))"
            else:
                ddl += ")"
            connection.execute(f"create table etf_ohlcv_daily {ddl}")
            connection.execute(MANIFEST_DDL)
            source_rows = [
                row("20200101", backfill.DEFAULT_CODES[0], 1.0, fund_name="old-overwritten"),
                row("20200102", backfill.DEFAULT_CODES[1], 12.0),
                row("20190103", backfill.DEFAULT_CODES[0], 6.0, fund_name="old-preserved"),
                row("20200109", "588000.SH", 9.0, fund_name="unrelated-preserved"),
            ]
            connection.executemany(
                "insert into etf_ohlcv_daily values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                source_rows,
            )
            connection.execute(
                """
                insert into source_manifest values
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backfill.SOURCE_NAME,
                    "old/source.db",
                    backfill.SOURCE_TABLE,
                    backfill.TARGET_TABLE,
                    "20150101",
                    "20251231",
                    2,
                    "20150105",
                    "20251230",
                    "daily",
                    "old_mode",
                    "old_quota",
                    "ready",
                    "old manifest",
                    "2025-12-31T00:00:00+00:00",
                ),
            )

    def _target_snapshot(self):
        with db_connection(self.target_path) as connection:
            return fetch_all(connection, "etf_ohlcv_daily")

    def _manifest_snapshot(self):
        with db_connection(self.target_path) as connection:
            return fetch_all(connection, "source_manifest")

    def test_default_cli_is_read_only_and_reports_projection(self):
        target_before = self._target_snapshot()
        manifest_before = self._manifest_snapshot()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = backfill.main(
                [
                    "--source-db",
                    str(self.source_path),
                    "--target-db",
                    str(self.target_path),
                ]
            )
        result = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["write_performed"])
        self.assertTrue(result["apply_required"])
        self.assertTrue(result["eligible_for_apply"])
        self.assertEqual(result["source_rows"], 6)
        self.assertEqual(result["inserted_rows"], 4)
        self.assertEqual(result["updated_rows"], 1)
        self.assertEqual(result["unchanged_rows"], 1)
        self.assertEqual(result["duplicate_key_checks"], {"source": 0, "target": 0})
        self.assertNotEqual(result["pre_hash"], result["projected_post_hash"])
        self.assertEqual(self._target_snapshot(), target_before)
        self.assertEqual(self._manifest_snapshot(), manifest_before)
        with db_connection(self.target_path) as connection:
            self.assertFalse(backfill._table_exists(connection, backfill.AUDIT_RUN_TABLE))

    def test_apply_is_targeted_audited_and_rollback_restores_exact_state(self):
        target_before = self._target_snapshot()
        manifest_before = self._manifest_snapshot()
        applied = backfill.apply_backfill(self.source_path, self.target_path)

        self.assertEqual(applied["status"], "committed")
        self.assertEqual(applied["inserted_rows"], 4)
        self.assertEqual(applied["updated_rows"], 1)
        self.assertEqual(applied["unchanged_rows"], 1)
        self.assertNotEqual(applied["pre_hash"], applied["post_hash"])
        with db_connection(self.target_path) as connection:
            connection.row_factory = sqlite3.Row
            invalid = connection.execute(
                """
                select count(*) from etf_ohlcv_daily
                where (trade_date = '20200201' and ts_code = ?)
                   or (trade_date = '20200202' and ts_code = ?)
                   or trade_date < ?
                """,
                (backfill.DEFAULT_CODES[0], backfill.DEFAULT_CODES[1], backfill.START_DATE),
            ).fetchone()[0]
            self.assertEqual(invalid, 0)
            unrelated = connection.execute(
                "select fund_name from etf_ohlcv_daily where ts_code = '588000.SH'"
            ).fetchone()[0]
            preserved = connection.execute(
                "select fund_name from etf_ohlcv_daily where ts_code = ? and trade_date = '20190103'",
                (backfill.DEFAULT_CODES[0],),
            ).fetchone()[0]
            self.assertEqual(unrelated, "unrelated-preserved")
            self.assertEqual(preserved, "old-preserved")
            run = connection.execute(
                f"select * from {backfill.AUDIT_RUN_TABLE} where run_id = ?",
                (applied["run_id"],),
            ).fetchone()
            audit_count = connection.execute(
                f"select count(*) from {backfill.AUDIT_ROW_TABLE} where run_id = ?",
                (applied["run_id"],),
            ).fetchone()[0]
            manifest = connection.execute(
                """
                select * from source_manifest
                where source_name = ? and source_table = ? and target_table = ?
                """,
                (backfill.SOURCE_NAME, backfill.SOURCE_TABLE, backfill.TARGET_TABLE),
            ).fetchone()
            self.assertEqual(run["status"], "committed")
            self.assertEqual(run["pre_hash"], applied["pre_hash"])
            self.assertEqual(run["post_hash"], applied["post_hash"])
            self.assertEqual(audit_count, 6)
            self.assertEqual(manifest["rows_loaded"], 6)
            self.assertEqual(manifest["update_mode"], "targeted_upsert_asset_allocation_v5")
            self.assertIn(applied["run_id"], manifest["message"])

        rolled_back = backfill.rollback_run(self.target_path, applied["run_id"])
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual(rolled_back["rollback_hash"], applied["pre_hash"])
        self.assertEqual(self._target_snapshot(), target_before)
        self.assertEqual(self._manifest_snapshot(), manifest_before)
        with db_connection(self.target_path) as connection:
            run = connection.execute(
                f"select status, rollback_hash from {backfill.AUDIT_RUN_TABLE} where run_id = ?",
                (applied["run_id"],),
            ).fetchone()
            self.assertEqual(run, ("rolled_back", applied["pre_hash"]))

        second = backfill.rollback_run(self.target_path, applied["run_id"])
        self.assertEqual(second["status"], "already_rolled_back")
        self.assertFalse(second["write_performed"])

    def test_missing_default_code_blocks_apply_without_mutation(self):
        with db_connection(self.source_path) as connection:
            connection.execute(
                "delete from fund_daily where ts_code = ?",
                (backfill.DEFAULT_CODES[-1],),
            )
        before = self._target_snapshot()
        plan = backfill.inspect_backfill(self.source_path, self.target_path)
        self.assertFalse(plan["eligible_for_apply"])
        self.assertEqual(plan["missing_source_codes"], [backfill.DEFAULT_CODES[-1]])
        with self.assertRaisesRegex(backfill.BackfillError, "source coverage is empty"):
            backfill.apply_backfill(self.source_path, self.target_path)
        self.assertEqual(self._target_snapshot(), before)
        with db_connection(self.target_path) as connection:
            self.assertFalse(backfill._table_exists(connection, backfill.AUDIT_RUN_TABLE))

    def test_duplicate_source_keys_block_before_any_write(self):
        self.source_path.unlink()
        self._create_source(primary_key=False)
        with db_connection(self.source_path) as connection:
            connection.execute(
                "insert into fund_daily values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row("20200101", backfill.DEFAULT_CODES[0], 33.0),
            )
        before = self._target_snapshot()
        with self.assertRaisesRegex(backfill.BackfillError, "source duplicate-key"):
            backfill.apply_backfill(self.source_path, self.target_path)
        self.assertEqual(self._target_snapshot(), before)

    def test_duplicate_target_keys_block_before_any_write(self):
        self.target_path.unlink()
        self._create_target(primary_key=False)
        with db_connection(self.target_path) as connection:
            connection.execute(
                "insert into etf_ohlcv_daily values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row("20200101", backfill.DEFAULT_CODES[0], 44.0),
            )
        before = self._target_snapshot()
        with self.assertRaisesRegex(backfill.BackfillError, "target duplicate-key"):
            backfill.apply_backfill(self.source_path, self.target_path)
        self.assertEqual(self._target_snapshot(), before)

    def test_failure_after_target_upsert_rolls_back_entire_transaction(self):
        target_before = self._target_snapshot()
        manifest_before = self._manifest_snapshot()
        with mock.patch.object(backfill, "_upsert_manifest", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                backfill.apply_backfill(self.source_path, self.target_path)
        self.assertEqual(self._target_snapshot(), target_before)
        self.assertEqual(self._manifest_snapshot(), manifest_before)
        with db_connection(self.target_path) as connection:
            self.assertFalse(backfill._table_exists(connection, backfill.AUDIT_RUN_TABLE))
            self.assertFalse(backfill._table_exists(connection, backfill.AUDIT_ROW_TABLE))

    def test_rollback_refuses_to_clobber_a_later_target_change(self):
        applied = backfill.apply_backfill(self.source_path, self.target_path)
        with db_connection(self.target_path) as connection:
            connection.execute(
                """
                update etf_ohlcv_daily set close = close + 1
                where trade_date = '20200101' and ts_code = ?
                """,
                (backfill.DEFAULT_CODES[0],),
            )
        drifted = self._target_snapshot()
        with self.assertRaisesRegex(backfill.BackfillError, "scope changed after the run"):
            backfill.rollback_run(self.target_path, applied["run_id"])
        self.assertEqual(self._target_snapshot(), drifted)
        with db_connection(self.target_path) as connection:
            status = connection.execute(
                f"select status from {backfill.AUDIT_RUN_TABLE} where run_id = ?",
                (applied["run_id"],),
            ).fetchone()[0]
            self.assertEqual(status, "committed")


if __name__ == "__main__":
    unittest.main()
