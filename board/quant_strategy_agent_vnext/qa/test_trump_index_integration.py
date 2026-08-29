from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parents[1]
sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QUANT_AGENT_USER", "qa-user")
os.environ.setdefault("QUANT_AGENT_PASSWORD", "qa-password")
os.environ.setdefault("QUANT_AGENT_SECRET", "qa-secret-only")
os.environ.setdefault("FACTOR_LAB_DB", str(PROJECT_ROOT / "database" / "research_warehouse.db"))

main = importlib.import_module("main")


class TrumpIndexIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = main.app.test_client()
        response = self.client.post(
            "/login",
            data={
                "username": os.environ["QUANT_AGENT_USER"],
                "password": os.environ["QUANT_AGENT_PASSWORD"],
            },
        )
        self.assertIn(response.status_code, {302, 303})

    def test_navigation_and_full_renderer_contract(self) -> None:
        template = (APP_ROOT / "templates" / "index_rotation_factor_lab.html").read_text(encoding="utf-8")
        script = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        style = (APP_ROOT / "static" / "css" / "ui_unified.css").read_text(encoding="utf-8")
        self.assertIn('data-target="data:trump_index">川普指数', template)
        self.assertLess(
            template.index('data-target="data:ai_monitor"'),
            template.index('data-target="data:trump_index"'),
        )
        self.assertIn("async function workspaceTrumpIndex", script)
        self.assertIn("TACO事件复盘", script)
        self.assertIn("川普支持率六视图", script)
        self.assertIn("川普 Truths 全量跟踪", script)
        self.assertIn("/api/trump/truths", script)
        self.assertIn("section.id", script)
        self.assertIn("Trump index full research integration v3", style)
        self.assertIn(".trump-v3-taco", style)
        self.assertIn(".trump-v3-truth-list", style)
        self.assertIn("var(--ui-red)", style)
        self.assertIn("var(--ui-green)", style)
        self.assertIn("var(--ui-blue)", style)

    def test_authenticated_core_proxy_preserves_verified_payload(self) -> None:
        payload = {
            "status": "ok",
            "pressure": {
                "available": True,
                "value": 3.7,
                "change20d": 9.8,
                "audit": {"status": "warn", "passed": 6, "total": 8},
            },
            "tacoEvents": [{"id": "taco-1"}] * 27,
            "approval": {
                "series": [{}] * 554,
                "termSeries": [{}] * 4,
                "issues": [{}] * 26,
                "states": [{}] * 51,
                "demographics": [{}] * 18,
            },
        }
        with mock.patch.object(main.legacy, "proxy_json", return_value=payload) as proxy:
            response = self.client.get("/api/trump/core?refresh=1")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["pressure"]["value"], 3.7)
        self.assertEqual(len(body["tacoEvents"]), 27)
        self.assertEqual(len(body["approval"]["states"]), 51)
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[:2], ("trump", "/api/tracker"))
        self.assertEqual(proxy.call_args.kwargs["query"], {"scope": "core", "refresh": "1"})

    def test_authenticated_truths_proxy_requires_verified_archive(self) -> None:
        payload = {
            "status": "ok",
            "truths": [{"id": "1", "url": "https://truthsocial.com/@realDonaldTrump/1"}],
            "source": {"verifiedSource": True, "resultCount": 35004},
        }
        with mock.patch.object(main.legacy, "proxy_json", return_value=payload) as proxy:
            response = self.client.get("/api/trump/truths?category=trade&limit=20&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"]["resultCount"], 35004)
        query = proxy.call_args.kwargs["query"]
        self.assertEqual(query["scope"], "truths")
        self.assertEqual(query["category"], "trade")
        self.assertEqual(query["limit"], "20")

    def test_anonymous_proxies_are_rejected(self) -> None:
        anonymous = main.app.test_client()
        self.assertEqual(anonymous.get("/api/trump/core").status_code, 401)
        self.assertEqual(anonymous.get("/api/trump/truths").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
