from __future__ import annotations

import gzip
import importlib
import json
import os
import re
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parents[1]
sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QUANT_AGENT_USER", "qa-user")
os.environ.setdefault("QUANT_AGENT_PASSWORD", "qa-password")
os.environ.setdefault("QUANT_AGENT_SECRET", "qa-secret-only")
os.environ.setdefault("FACTOR_LAB_DB", str(PROJECT_ROOT / "database" / "research_warehouse.db"))

main = importlib.import_module("main")


class CanonicalAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = main.app.test_client()
        response = cls.client.post(
            "/login",
            data={
                "username": os.environ["QUANT_AGENT_USER"],
                "password": os.environ["QUANT_AGENT_PASSWORD"],
            },
        )
        assert response.status_code in {302, 303}

    @staticmethod
    def decoded(response) -> bytes:
        if response.headers.get("Content-Encoding") == "gzip":
            return gzip.decompress(response.data)
        return response.data

    def test_canonical_assets_only(self) -> None:
        response = self.client.get("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        html = self.decoded(response).decode("utf-8")
        for asset in (
            "ui_unified.css",
            "app.js",
            "ai_monitor/js/core.js",
            "ai_monitor/js/shell.js",
            "index_enhancement.js",
            "rotation_module.js",
            "factor_lab.js",
        ):
            self.assertIn(asset, html)
        for obsolete in ("factor_lab_v2", "rotation_module_v4", "index_enhancement_v2"):
            self.assertNotIn(obsolete, html)

    def test_static_assets_are_served_and_compressed(self) -> None:
        for path in (
            "/static/css/app.css",
            "/static/css/ui_unified.css",
            "/static/js/app.js",
            "/static/ai_monitor/css/native.css",
            "/static/ai_monitor/js/core.js",
            "/static/ai_monitor/js/features.js",
            "/static/ai_monitor/js/weights.js",

            "/static/ai_monitor/js/axis.js",
            "/static/ai_monitor/js/shell.js",
            "/static/js/index_enhancement.js",
            "/static/js/rotation_module.js",
            "/static/js/factor_lab.js",
            "/static/vendor/plotly.min.js",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept-Encoding": "gzip"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
                self.assertGreater(len(self.decoded(response)), 100)
                response.close()
    def test_snapshot_transport_contract(self) -> None:
        paths = (
            "/api/allocation/snapshot",
            "/api/liquidity/snapshot",
            "/api/index-enhancement/snapshot",
            "/api/portfolio/snapshot",
            "/api/rotation/snapshot",
            "/api/rotation/tracking",
            "/api/factor-lab/bootstrap",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept-Encoding": "gzip"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
                self.assertIn("max-age=300", response.headers.get("Cache-Control", ""))
                etag = response.headers.get("ETag")
                self.assertTrue(etag)
                conditional = self.client.get(
                    path,
                    headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
                )
                self.assertEqual(conditional.status_code, 304)
    def test_factor_lab_exposes_audited_champion_without_using_test_for_selection(self) -> None:
        response = self.client.get("/api/factor-lab/bootstrap")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        champion = payload["champion"]
        self.assertEqual(payload["engine_version"], "factor-lab/3.6.1-deep-anti-overfit")
        self.assertEqual(champion["active_engine_version"], payload["engine_version"])
        self.assertEqual(champion["status"], "ok")
        self.assertEqual(champion["selection_basis"], "train_and_validation_only")
        self.assertEqual(champion["test_usage"], "report_only")
        self.assertGreaterEqual(champion["candidate_count"], 1)
        self.assertEqual(champion["gate_summary"]["passed"], champion["gate_summary"]["total"])
        self.assertGreaterEqual(champion["gate_summary"]["total"], 1)
        self.assertTrue(champion["gate_summary"]["all_passed"])
        metrics = {row["split"]: row for row in champion["splits"]}
        self.assertEqual(set(metrics), {"train", "valid", "test"})
        self.assertTrue(all("sharpe" in row for row in metrics.values()))
        self.assertIn("max_drawdown", metrics["test"])
        turnover = next(row for row in champion["gates"] if row["gate"] == "turnover")
        self.assertTrue(turnover["passed"])
        js = (APP_ROOT / "static" / "js" / "factor_lab.js").read_text(encoding="utf-8")
        self.assertIn("function championHtml(champion)", js)
        self.assertIn("训练集与验证集选择，测试集仅作一次性报告", js)


    def test_workspace_controls_are_scoped_and_review_strip_is_absent(self) -> None:
        template = (APP_ROOT / "templates" / "index_rotation_factor_lab.html").read_text(encoding="utf-8")
        app_js = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        app_css = (APP_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        unified_css = (APP_ROOT / "static" / "css" / "ui_unified.css").read_text(encoding="utf-8")
        main_py = (APP_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('aria-label="当前板块功能目录"', template)
        self.assertIn('class="workspace-section-nav"', app_js)
        self.assertIn("S.workspace=S.workspace||{section:{}};", app_js)
        for removed in (
            "model-evidence-strip", "workspace-global-controls", "workspace-frequency",
            "workspace-risk", "workspace-asof", "workspace-refresh", "loadGovernance",
            "workspaceApplySharedParameters", "workspaceRenderEvidence",
        ):
            self.assertNotIn(removed, template + app_js + app_css + unified_css)
        self.assertNotIn("/api/model-governance", app_js)
        self.assertIn('@app.get("/api/model-governance")', main_py)

    def test_ai_monitor_tolerates_partial_level1_failures(self) -> None:
        core_js = (APP_ROOT / "static" / "ai_monitor" / "js" / "core.js").read_text(encoding="utf-8")
        shell_js = (APP_ROOT / "static" / "ai_monitor" / "js" / "shell.js").read_text(encoding="utf-8")
        self.assertIn("const pending = new Map();", core_js)
        self.assertIn("Promise.allSettled", core_js)
        self.assertIn("await new Promise((resolve) => setTimeout(resolve, 250));", core_js)
        self.assertIn("void groupTask;", core_js)
        self.assertIn("void level1Task;", core_js)
        self.assertIn('id="ai-monitor-status-dot" class="status-dot running"', shell_js)
        self.assertIn('id="ai-monitor-status-text"', shell_js)

    def test_ai_monitor_cache_matches_daily_update_frequency(self) -> None:
        main_py = (APP_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('if clean_path == "api/snapshot":', main_py)
        self.assertIn("ttl = 21_600", main_py)
        self.assertIn('elif clean_path == "api/dynamic-series":', main_py)
        self.assertIn("ttl = 900", main_py)
        self.assertIn("ttl = 1_800", main_py)

    def test_ai_monitor_is_native_shadow_ui(self) -> None:
        template = (APP_ROOT / "templates" / "index_rotation_factor_lab.html").read_text(encoding="utf-8")
        app_js = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        shell_js = (APP_ROOT / "static" / "ai_monitor" / "js" / "shell.js").read_text(encoding="utf-8")
        native_css = (APP_ROOT / "static" / "ai_monitor" / "css" / "native.css").read_text(encoding="utf-8")
        main_py = (APP_ROOT / "main.py").read_text(encoding="utf-8")
        for asset in ("core.js", "features.js", "weights.js", "boot.js", "axis.js", "shell.js"):
            self.assertIn(f"ai_monitor/js/{asset}", template)
        self.assertNotIn("<iframe", app_js)
        self.assertNotIn("/tech-diffusion/", app_js)
        self.assertIn("window.AIMonitorUI.mount", app_js)
        for section_id in ("overview", "industry-map", "industry-series", "stock-attribution"):
            self.assertIn(f'id="{section_id}"', shell_js)
        self.assertIn("font-size: 14px", native_css)
        self.assertIn("font-size: 11px", native_css)
        self.assertIn("Arial", native_css)
        self.assertIn('"KaiTi"', native_css)
        self.assertIn('@app.get("/api/ai-monitor/<path:upstream_path>")', main_py)
        self.assertIn('"ai_monitor_proxy"', main_py)
        anonymous = main.app.test_client()
        self.assertEqual(anonymous.get("/api/ai-monitor/api/snapshot").status_code, 401)
    def test_rotation_stock_labels_are_on_demand(self) -> None:
        snapshot_response = self.client.get(
            "/api/rotation/snapshot",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(snapshot_response.status_code, 200)
        snapshot_bytes = self.decoded(snapshot_response)
        self.assertLess(len(snapshot_bytes), 3_000_000)
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
        expected_total = int(snapshot["style"]["data_quality"]["latest_labelled_stock_count"])
        self.assertGreaterEqual(expected_total, 4000)
        self.assertNotIn("stock_labels", snapshot["style"])
        self.assertEqual(
            snapshot["style"]["stock_labels_endpoint"],
            "/api/rotation/style-labels",
        )

        labels_response = self.client.get(
            "/api/rotation/style-labels",
            query_string={"limit": 120},
        )
        self.assertEqual(labels_response.status_code, 200)
        labels = labels_response.get_json()
        self.assertEqual(labels["status"], "ok")
        self.assertEqual(labels["total"], expected_total)
        self.assertEqual(len(labels["rows"]), 120)
        self.assertEqual(len({row["code"] for row in labels["rows"]}), 120)

        first_cell = snapshot["style"]["cells"][0]["cell"]
        filtered_response = self.client.get(
            "/api/rotation/style-labels",
            query_string={"cell": first_cell, "limit": 5},
        )
        self.assertEqual(filtered_response.status_code, 200)
        filtered = filtered_response.get_json()
        self.assertLessEqual(len(filtered["rows"]), 5)
        self.assertTrue(all(row["cell"] == first_cell for row in filtered["rows"]))
        self.assertEqual(
            self.client.get(
                "/api/rotation/style-labels",
                query_string={"cell": "不存在的风格箱"},
            ).status_code,
            400,
        )
    def test_all_nav_targets_have_one_router(self) -> None:
        template = (APP_ROOT / "templates" / "index_rotation_factor_lab.html").read_text(encoding="utf-8")
        app_js = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        targets = re.findall(r'data-target="([^"]+)"', template)
        self.assertEqual(
            targets,
            [
                "data:market_monitor", "data:topic_tracking",
                "allocation:cycle", "allocation:strategy",
                "rotation:prosperity", "rotation:industry", "rotation:style",
                "factorlab:dashboard", "factorlab:mining", "factorlab:strategy",
                "technical:factors", "technical:learning",
                "portfolio:solve", "portfolio:timing", "portfolio:index",
            ],
        )
        for target, label in (
            ("rotation:prosperity", "行业景气度"),
            ("rotation:industry", "行业轮动"),
            ("rotation:style", "风格轮动"),
            ("technical:factors", "技术因子"),
            ("technical:learning", "K线学习"),
            ("portfolio:solve", "优化求解器"),
            ("portfolio:timing", "宽基择时"),
            ("portfolio:index", "指数增强"),
        ):
            self.assertIn(f'data-target="{target}">{label}', template)
        self.assertNotIn('data-target="rotation:home"', template)
        self.assertNotIn('data-target="rotation:backtest"', template)
        for legacy_prefix in ("index:", "factor:", "kline:"):
            self.assertFalse(any(target.startswith(legacy_prefix) for target in targets))
        for preserved_view in (
            "allocation:home", "allocation:backtest", "liquidity:home",
            "rotation:home", "rotation:backtest",
            "factor:home", "factor:expression", "factor:report", "factor:score", "factor:memory",
            "index:home", "index:universe", "index:alpha", "index:smartbeta",
            "index:timing", "index:risk", "index:tracking",
            "kline:home", "kline:learn", "kline:history", "kline:backtest",
            "portfolio:home", "portfolio:pool", "portfolio:timing", "portfolio:risk", "portfolio:backtest",
        ):
            self.assertIn(preserved_view, app_js)
        self.assertIn("WORKSPACE_CONFIG", app_js)
        self.assertIn("const loadingHost=$('view-root');", app_js)
        self.assertNotIn("const loadingHost=view-root;", app_js)
        self.assertNotIn("stopImmediatePropagation", app_js)
        self.assertIn("window.IndexEnhancement.render", app_js)
        self.assertIn("window.IndustryRotation.render", app_js)
        self.assertIn("window.FactorLaboratory.render", app_js)

    def test_factor_read_paths_are_parallel_and_cacheable(self) -> None:
        app_js = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        main_py = (APP_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("await Promise.all(tasks);", app_js)
        self.assertIn("api('/api/factor/history')", app_js)
        self.assertIn("refresh=1&ts=", app_js)
        self.assertIn("?live=1&ts=", app_js)
        self.assertIn("rows[0]&&rows[0].job_id", app_js)
        for endpoint in ("factor_status", "factor_history", "factor_history_detail", "kline_job"):
            self.assertIn(f'"{endpoint}"', main_py)

    def test_service_contract(self) -> None:
        response = self.client.get("/api/services", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        payload = json.loads(self.decoded(response).decode("utf-8"))
        self.assertEqual(
            set(payload["services"]),
            {
                "board", "kline", "factor", "ai_monitor", "trump", "allocation", "liquidity",
                "index_enhancement", "portfolio", "rotation", "factor_lab",
            },
        )

    def test_kline_llm_dashboard_keeps_model_dependencies(self) -> None:
        response = self.client.get("/api/kline-llm/dashboard")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["domains"]), 12)
        self.assertGreaterEqual(len(payload["rules"]), 400)
        self.assertGreaterEqual(len(payload["stock_universe"]), 5000)
        self.assertRegex(str(payload["as_of"]), r"^\d{8}$")



    def test_every_legacy_model_view_has_a_workspace_destination(self) -> None:
        mapping_path = PROJECT_ROOT / "framework" / "integration" / "ui_module_mapping.json"
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))["legacy_to_workspace"]
        expected = {
            "data:macro", "data:global_markets", "data:sw_industries",
            "data:commodities", "data:stock", "data:news_events",
            "allocation:home", "allocation:cycle", "allocation:strategy", "allocation:backtest",
            "portfolio:home", "portfolio:pool", "portfolio:timing", "portfolio:risk", "portfolio:solve", "portfolio:backtest",
            "index:home", "index:universe", "index:alpha", "index:smartbeta", "index:timing", "index:risk", "index:tracking",
            "rotation:home", "rotation:industry", "rotation:style", "rotation:allocation", "rotation:backtest",
            "liquidity:home", "liquidity:retail", "liquidity:public", "liquidity:etf",
            "liquidity:margin", "liquidity:primary", "liquidity:private", "liquidity:foreign",
            "kline:home", "kline:learn", "kline:backtest", "kline:history",
            "factorlab:home", "factorlab:dashboard", "factorlab:mining",
            "factorlab:testing", "factorlab:strategy", "factorlab:history",
            "factor:home", "factor:expression", "factor:report", "factor:score", "factor:memory",
        }
        self.assertEqual(set(mapping), expected)
        app_js = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        for legacy, destination in mapping.items():
            with self.subTest(legacy=legacy):
                self.assertIn(destination["target"], app_js)
                self.assertIn(destination["section"], app_js)
                self.assertIn(destination["renderer"].split("/")[1], app_js)

    def test_history_views_keep_only_the_governed_best_record(self) -> None:
        app_js = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        index_js = (
            APP_ROOT / "static" / "js" / "index_enhancement.js"
        ).read_text(encoding="utf-8")
        self.assertIn("function klineHistoryEvidence(row,detail)", app_js)
        self.assertIn("Math.min(trainSharpe,validSharpe)", app_js)
        self.assertIn("selection_basis:'train_validation_conservative_sharpe'", app_js)
        self.assertIn("selection_uses_test:false", app_js)
        self.assertIn("S.kline.history=best?[best.row]:[];", app_js)
        self.assertIn("best&&best.model?[best]:[]", index_js)
        self.assertIn("完整比较仍保留在上方图表", index_js)
if __name__ == "__main__":
    unittest.main(verbosity=2)
