import importlib.util
import sqlite3
import json
import os
import sys
import unittest
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "factor_lab_backend.py"
SPEC = importlib.util.spec_from_file_location("factor_lab_backend_under_test", MODULE_PATH)
backend = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backend
SPEC.loader.exec_module(backend)

FACTOR_MODEL_PATH = Path(__file__).resolve().parents[3] / "model" / "factor_laboratory"
if str(FACTOR_MODEL_PATH) not in sys.path:
    sys.path.insert(0, str(FACTOR_MODEL_PATH))
from factor_catalog import build_factor_catalog


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
                "passed": 10,
                "total": 10,
                "all_passed": True,
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


    def test_gru_model_is_exposed_and_normalized(self):
        self.assertIn("gru", backend.MODEL_PRESETS)
        config = backend.normalized_config({
            "engine": "gru",
            "mode": "research",
            "gru_layers": 4,
            "sequence_length": 80,
            "epochs": 3,
        })
        self.assertEqual(config["engine"], "gru")
        self.assertEqual(config["recurrent_cell"], "gru")
        self.assertEqual(config["gru_layers"], 4)
        self.assertEqual(config["sequence_length"], 80)

    def test_strategy_config_enables_screened_factor_universe(self):
        config = backend.normalized_config({
            "engine": "strategy",
            "mode": "smoke",
            "factor_screen_top_n": 999,
            "factor_screen_min_coverage": 0.001,
        })
        self.assertEqual(config["factor_universe_mode"], "screened_full")
        self.assertEqual(config["factor_screen_top_n"], 240)
        self.assertEqual(config["factor_screen_min_coverage"], 0.01)
        self.assertEqual(config["external_factor_max_staleness_days"], 63)
        self.assertEqual(config["selection_turnover_budget"], 0.65)
        self.assertTrue(config["selection_prefer_best_development"])
        self.assertFalse(config["include_subject_parquet"])

    def test_catalog_payload_uses_dynamic_catalog_not_hardcoded_counts(self):
        original_state_db = backend.STATE_DB
        original_cache = backend.CATALOG_CACHE
        original_builder = backend.build_factor_catalog
        original_warehouse_path = backend.warehouse_path
        try:
            with tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                backend.STATE_DB = temp_root / "factor_lab_state.sqlite3"
                backend.CATALOG_CACHE = {"at": 0.0, "payload": None}
                backend.warehouse_path = lambda: temp_root / "missing_warehouse.sqlite3"
                backend.build_factor_catalog = lambda warehouse_path: {
                    "status": "ok",
                    "watermark": "2026-08-14",
                    "registered_factor_count": 343,
                    "explicit_factor_entry_count": 343,
                    "materialized_factor_count": 12,
                    "current_model_feature_count": 29,
                    "families": [
                        {"id": "technical", "label": "technical", "count": 26},
                        {"id": "money", "label": "money", "count": 22},
                        {"id": "fundamental", "label": "fundamental", "count": 32},
                        {"id": "valuation", "label": "valuation", "count": 18},
                        {"id": "macro", "label": "macro", "count": 19},
                        {"id": "llm_mined", "label": "llm", "count": 2},
                        {"id": "deep_mined", "label": "deep", "count": 3},
                        {"id": "warehouse_dynamic", "label": "warehouse", "count": 4},
                    ],
                    "factors": [
                        {"factor_name": "ret_20", "family_id": "technical", "factor_group": "technical"},
                        {"factor_name": "llm_cash_quality", "family_id": "llm_mined", "factor_group": "llm_mined"},
                    ],
                }
                backend.init_state()
                with backend.state_conn() as conn:
                    conn.execute(
                        """
                        insert into factor_lab_run(
                            run_id,user_name,engine,mode,status,progress,config_hash,config_json,created_at
                        ) values(?,?,?,?,?,?,?,?,?)
                        """,
                        ("run_gru", "tester", "gru", "smoke", "completed", 1.0, "hash", "{}", "2026-08-14T00:00:00+00:00"),
                    )
                payload = backend.catalog_payload(force=True)
                self.assertEqual(payload["registered_factor_count"], 343)
                self.assertEqual(payload["standard_factor_count"], 117)
                self.assertEqual(payload["discovered_factor_count"], 9)
                self.assertEqual(payload["completed_model_runs"]["gru"], 1)
                self.assertIn("gru", {item["id"] for item in payload["model_catalog"]})
        finally:
            backend.STATE_DB = original_state_db
            backend.CATALOG_CACHE = original_cache
            backend.build_factor_catalog = original_builder
            backend.warehouse_path = original_warehouse_path


    def test_build_factor_catalog_parses_subject_and_warehouse_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subject_main = root / "subject" / "main"
            (subject_main / "factor").mkdir(parents=True)
            (subject_main / "model").mkdir(parents=True)
            (subject_main / "factor" / "factors.py").write_text(
                "TECHNICAL_FACTORS = [{'name': 'ma20'}, {'name': 'rsi'}]\n"
                "MONEY_FACTORS = [{'name': 'north_flow'}]\n"
                "FUNDAMENTAL_FACTORS = [{'name': 'roe'}]\n"
                "VALUATION_FACTORS = [{'name': 'pe'}]\n"
                "MACRO_FACTORS = [{'name': 'credit'}]\n",
                encoding="utf-8",
            )
            (subject_main / "model" / "technical.py").write_text(
                "TECHNICAL_FACTOR_REGISTRY = ["
                "{'name': 'breakout_strength', 'category': 'trend'}"
                "]\n",
                encoding="utf-8",
            )
            (subject_main / "model" / "smartbeta.py").write_text(
                "SMARTBETA_SUBFACTOR_SPECS = ["
                "{'name': 'quality_profit', 'domain': 'quality'}"
                "]\n",
                encoding="utf-8",
            )
            orphan = subject_main / "factor" / "parquet" / "secking" / "secking_alpha.parquet.gzip"
            orphan.parent.mkdir(parents=True)
            orphan.write_text("placeholder", encoding="utf-8")
            warehouse = root / "warehouse.sqlite3"
            con = sqlite3.connect(warehouse)
            try:
                con.execute("create table factor_value_daily(factor_name text,factor_group text,source_agent text,trade_date text)")
                con.execute("insert into factor_value_daily values('llm_alpha_cash','llm','llm_agent','20260814')")
                con.execute("create table v3_factor_candidate_registry(factor_name text,family text,status text,created_at text)")
                con.execute("insert into v3_factor_candidate_registry values('deep_gru_alpha','deep','accepted','2026-08-14')")
                con.commit()
            finally:
                con.close()
            payload = build_factor_catalog(warehouse, subject_roots=[subject_main])
            names = {row["factor_name"] for row in payload["factors"]}
            self.assertTrue({"ma20", "breakout_strength", "quality_profit", "secking_alpha", "llm_alpha_cash", "deep_gru_alpha"}.issubset(names))
            families = {row["id"]: row["count"] for row in payload["families"]}
            self.assertGreaterEqual(families["technical"], 2)
            self.assertGreaterEqual(families["subject_strategy_technical"], 1)
            self.assertGreaterEqual(families["smartbeta"], 1)
            self.assertGreaterEqual(families["secking"], 1)
            self.assertGreaterEqual(families["llm_mined"], 1)
            self.assertGreaterEqual(families["deep_mined"], 1)
            self.assertGreaterEqual(payload["current_model_feature_count"], 29)


    def test_build_factor_catalog_uses_snapshot_when_external_roots_are_missing(self):
        original_snapshot = os.environ.get("FACTOR_LAB_CATALOG_SNAPSHOT")
        original_disable = os.environ.pop("FACTOR_LAB_DISABLE_CATALOG_SNAPSHOT", None)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                subject_main = root / "subject" / "main"
                (subject_main / "factor").mkdir(parents=True)
                (subject_main / "model").mkdir(parents=True)
                (subject_main / "factor" / "factors.py").write_text("TECHNICAL_FACTORS = [{'name': 'local_anchor'}]\n", encoding="utf-8")
                snapshot_path = root / "factor_catalog_snapshot.json"
                snapshot_path.write_text(
                    json.dumps({
                        "schema_version": "factor_catalog_snapshot/1.0",
                        "factors": [
                            {"factor_name": "snapshot_llm_alpha", "family_id": "llm_mined", "factor_group": "llm", "source_agent": "snapshot", "materialized": False}
                        ],
                    }),
                    encoding="utf-8",
                )
                os.environ["FACTOR_LAB_CATALOG_SNAPSHOT"] = str(snapshot_path)
                payload = build_factor_catalog(None, subject_roots=[subject_main])
                names = {row["factor_name"] for row in payload["factors"]}
                self.assertIn("snapshot_llm_alpha", names)
                self.assertIn("local_anchor", names)
        finally:
            if original_snapshot is None:
                os.environ.pop("FACTOR_LAB_CATALOG_SNAPSHOT", None)
            else:
                os.environ["FACTOR_LAB_CATALOG_SNAPSHOT"] = original_snapshot
            if original_disable is not None:
                os.environ["FACTOR_LAB_DISABLE_CATALOG_SNAPSHOT"] = original_disable


if __name__ == "__main__":
    unittest.main(verbosity=2)
