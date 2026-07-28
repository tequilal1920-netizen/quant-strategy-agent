import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("run_v4_audit_feedback_optimize.py")
SPEC = importlib.util.spec_from_file_location("v4_audit", MODULE_PATH)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class StrictGateTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            create table v3_run_manifest (
              run_id text primary key,
              started_at text not null,
              ended_at text,
              status text not null
            );
            create table v3_backtest_audit (
              run_id text not null,
              universe text not null,
              model_name text not null,
              split_name text not null,
              year text not null,
              periods integer,
              annual_return real,
              sharpe real,
              issues_json text
            );
            """
        )

    def tearDown(self):
        self.con.close()

    def add_run(self, run_id, ended_at, status="ready"):
        self.con.execute(
            "insert into v3_run_manifest values (?,?,?,?)",
            (run_id, ended_at, ended_at, status),
        )

    def add_model(self, run_id, model, periods):
        for split, count in periods.items():
            self.con.execute(
                """
                insert into v3_backtest_audit
                values (?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    "CSI2000_ENH",
                    model,
                    split,
                    "all",
                    count,
                    0.30,
                    1.80,
                    '["passed_or_no_issue"]',
                ),
            )

    def test_latest_ready_run_is_used_without_cross_run_mixing(self):
        self.add_run("old", "2026-01-01 00:00:00")
        self.add_model(
            "old",
            "model_a",
            {"train": 108, "valid": 24, "test": 40, "full": 172},
        )
        self.add_run("new", "2026-02-01 00:00:00")
        self.add_model(
            "new",
            "model_a",
            {"train": 108, "test": 40, "full": 148},
        )
        row = audit.strict_gate_rows(self.con)[0]
        self.assertEqual(row["source_run_id"], "new")
        self.assertEqual(row["pass_flag"], 0)
        self.assertIn("valid", row["issue"])

    def test_complete_independent_four_split_evidence_can_pass(self):
        self.add_run("complete", "2026-02-01 00:00:00")
        self.add_model(
            "complete",
            "model_a",
            {"train": 108, "valid": 24, "test": 40, "full": 172},
        )
        row = audit.strict_gate_rows(self.con)[0]
        self.assertEqual(row["pass_flag"], 1)
        self.assertEqual(row["issue"], "pass")

    def test_full_equal_to_test_cannot_pass(self):
        self.add_run("short_history", "2026-02-01 00:00:00")
        self.add_model(
            "short_history",
            "model_a",
            {"train": 1, "valid": 1, "test": 40, "full": 40},
        )
        row = audit.strict_gate_rows(self.con)[0]
        self.assertEqual(row["pass_flag"], 0)
        self.assertIn("not independent", row["issue"])

    def test_running_manifest_is_not_formal_evidence(self):
        self.add_run("running", "2026-02-01 00:00:00", status="running")
        self.add_model(
            "running",
            "model_a",
            {"train": 108, "valid": 24, "test": 40, "full": 172},
        )
        self.assertEqual(audit.strict_gate_rows(self.con), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
