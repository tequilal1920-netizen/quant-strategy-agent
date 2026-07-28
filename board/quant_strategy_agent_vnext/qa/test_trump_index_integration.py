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

    def test_navigation_and_compact_renderer_contract(self) -> None:
        template = (APP_ROOT / "templates" / "index_rotation_factor_lab.html").read_text(encoding="utf-8")
        script = (APP_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        style = (APP_ROOT / "static" / "css" / "ui_unified.css").read_text(encoding="utf-8")
        self.assertIn('data-target="data:trump_index">川普指数', template)
        self.assertLess(
            template.index('data-target="data:ai_monitor"'),
            template.index('data-target="data:trump_index"'),
        )
        self.assertIn("'data:trump_index'", script)
        self.assertIn("async function workspaceTrumpIndex", script)
        self.assertIn("kind:'trump-index'", script)
        self.assertNotIn("<table", script[script.index("async function workspaceTrumpIndex"):script.index("function workspaceTopBy")])
        self.assertIn(".trump-index-dashboard", style)
        self.assertIn("var(--ui-red)", style)
        self.assertIn("var(--ui-green)", style)
        self.assertIn("var(--ui-blue)", style)

    def test_authenticated_proxy_preserves_verified_score(self) -> None:
        payload = {
            "status": "ok",
            "asOf": "2026-07-27",
            "pressure": {
                "available": True,
                "value": 3.9,
                "change20d": 10.0,
                "audit": {"status": "warn", "passed": 6, "total": 8},
            },
        }
        with mock.patch.object(main.legacy, "proxy_json", return_value=payload) as proxy:
            response = self.client.get("/api/trump/core?refresh=1")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["pressure"]["value"], 3.9)
        self.assertEqual(body["pressure"]["change20d"], 10.0)
        self.assertEqual(body["pressure"]["audit"]["passed"], 6)
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[:2], ("trump", "/api/tracker"))
        self.assertEqual(proxy.call_args.kwargs["query"], {"scope": "core", "refresh": "1"})

    def test_anonymous_proxy_is_rejected(self) -> None:
        anonymous = main.app.test_client()
        self.assertEqual(anonymous.get("/api/trump/core").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
