import importlib.util
import sqlite3
import json
import sys
import unittest
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "factor_lab_backend.py"
SPEC = importlib.util.spec_from_file_location("factor_lab_backend_under_test", MODULE_PATH)
backend = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backend
SPEC.loader.exec_module(backend)


class FactorCatalogEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            """
            create table factor_test_result (
              run_id text,
              universe text,
              factor_name text,
              split_name text,
              rank_ic real,
              icir real,
              group_spread real,
              turnover real,
              coverage real,
              pass_flag integer,
              message text
            )
            """
        )

    def tearDown(self):
        self.con.close()

    def add(self, run_id, universe, factor, split, rank_ic, icir, coverage, passed):
        self.con.execute(
            """
            insert into factor_test_result
            values (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                universe,
                factor,
                split,
                rank_ic,
                icir,
                0.0,
                0.0,
                coverage,
                passed,
                "",
            ),
        )

    def test_champion_manifest_rejects_test_selected_payload(self):
        original = backend.CHAMPION_MANIFEST
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "champion.json"
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": "factor-champion/1.0",
                            "engine_version": "factor-lab/test",
                            "selected_candidate": "bad",
                            "selection_basis": "test",
                            "test_usage": "selected",
                            "candidate_count": 1,
                            "splits": [
                                {"split": "train"},
                                {"split": "valid"},
                                {"split": "test"},
                            ],
                            "gates": [{"gate": "bad", "passed": True}],
                            "candidate_diagnostics": [],
                        }
                    ),
                    encoding="utf-8",
                )
                backend.CHAMPION_MANIFEST = path
                self.assertEqual(backend.champion_payload()["status"], "unavailable")
        finally:
            backend.CHAMPION_MANIFEST = original

    def test_champion_manifest_reports_gate_summary(self):
        original = backend.CHAMPION_MANIFEST
        try:
            backend.CHAMPION_MANIFEST = (
                Path(__file__).resolve().parents[3]
                / "model"
                / "factor_laboratory"
                / "champion_manifest.json"
            )
            result = backend.champion_payload()
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["gate_summary"], {
                "passed": 9,
                "total": 10,
                "all_passed": False,
            })
        finally:
            backend.CHAMPION_MANIFEST = original

    def test_record_is_coherent_and_keeps_rank_ic_sign(self):
        self.add("run_202501", "ALL_A", "factor_a", "full", 0.05, 0.7, 0.80, 1)
        self.add("run_202601", "ALL_A", "factor_a", "full", -0.03, -0.4, 0.70, 0)
        self.add("run_202701", "CSI2000_ENH", "factor_a", "full", 0.30, 4.0, 1.00, 1)
        result = backend.latest_factor_evaluations(self.con)["factor_a"]
        self.assertEqual(result["evaluation_run_id"], "run_202601")
        self.assertEqual(result["evaluation_universe"], "ALL_A")
        self.assertEqual(result["evaluation_split"], "full")
        self.assertEqual(result["rank_ic"], -0.03)
        self.assertEqual(result["icir"], -0.4)
        self.assertEqual(result["coverage"], 0.70)
        self.assertEqual(result["pass_flag"], 0)

    def test_full_formal_record_is_preferred_within_run(self):
        self.add("run_202601", "ALL_A", "factor_b", "test", 0.04, 0.5, 0.9, None)
        self.add("run_202601", "ALL_A", "factor_b", "full", 0.02, 0.3, 0.95, 1)
        result = backend.latest_factor_evaluations(self.con)["factor_b"]
        self.assertEqual(result["evaluation_split"], "full")
        self.assertEqual(result["rank_ic"], 0.02)
        self.assertEqual(result["pass_flag"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
