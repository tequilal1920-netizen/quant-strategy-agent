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

    def test_rotation_stock_labels_are_on_demand(self) -> None:
        snapshot_response = self.client.get(
            "/api/rotation/snapshot",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(snapshot_response.status_code, 200)
        snapshot_bytes = self.decoded(snapshot_response)
        self.assertLess(len(snapshot_bytes), 3_000_000)
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
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
        self.assertEqual(labels["total"], 5229)
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
                "home:overview",
                "data:macro", "data:global_markets", "data:sw_industries",
                "data:commodities", "data:stock", "data:news_events", "data:ai_monitor",
                "allocation:cycle", "allocation:strategy",
                "liquidity:retail", "liquidity:public", "liquidity:private",
                "liquidity:foreign", "liquidity:etf", "liquidity:primary", "liquidity:margin",
                "rotation:home", "rotation:industry", "rotation:style",
                "rotation:allocation", "rotation:backtest",
                "factorlab:dashboard", "factorlab:mining", "factorlab:strategy",
                "technical:learning", "technical:strategy",
                "portfolio:solve", "portfolio:strategy",
            ],
        )
        for label in (
            "01主页", "02行业景气度", "03风格轮动周期", "04配置策略", "05策略回测",
        ):
            self.assertIn(label, template)
        for legacy_prefix in ("index:", "factor:", "kline:"):
            self.assertFalse(any(target.startswith(legacy_prefix) for target in targets))
        for preserved_view in (
            "allocation:home", "allocation:backtest", "liquidity:home",
            "factor:home", "factor:expression", "factor:report", "factor:score", "factor:memory",
            "index:home", "index:universe", "index:alpha", "index:smartbeta",
            "index:risk", "index:tracking",
            "kline:home", "kline:learn", "kline:history", "kline:backtest",
            "portfolio:home", "portfolio:pool", "portfolio:risk", "portfolio:backtest",
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
        self.assertIn("latest.job_id", app_js)
        for endpoint in ("factor_status", "factor_history", "factor_history_detail", "kline_job"):
            self.assertIn(f'"{endpoint}"', main_py)

    def test_service_contract(self) -> None:
        response = self.client.get("/api/services", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        payload = json.loads(self.decoded(response).decode("utf-8"))
        self.assertEqual(
            set(payload["services"]),
            {
                "board", "kline", "factor", "allocation", "liquidity",
                "index_enhancement", "portfolio", "rotation", "factor_lab",
            },
        )



    def test_every_legacy_model_view_has_a_workspace_destination(self) -> None:
        mapping_path = PROJECT_ROOT / "framework" / "integration" / "ui_module_mapping.json"
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))["legacy_to_workspace"]
        expected = {
            "data:macro", "data:global_markets", "data:sw_industries",
            "data:commodities", "data:stock", "data:news_events",
            "allocation:home", "allocation:cycle", "allocation:strategy", "allocation:backtest",
            "portfolio:home", "portfolio:pool", "portfolio:risk", "portfolio:solve", "portfolio:backtest",
            "index:home", "index:universe", "index:alpha", "index:smartbeta", "index:risk", "index:tracking",
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
if __name__ == "__main__":
    unittest.main(verbosity=2)
