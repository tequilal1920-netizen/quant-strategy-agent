from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "research_evidence_backend.py"
SPEC = importlib.util.spec_from_file_location("research_evidence_backend_vnext", MODULE_PATH)
backend = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backend
SPEC.loader.exec_module(backend)


class ResearchEvidenceBackendTests(unittest.TestCase):
    def test_all_existing_model_routes_return_serializable_evidence(self):
        routes = [
            "allocation:strategy",
            "liquidity:retail",
            "rotation:industry",
            "factorlab:dashboard",
            "factorlab:strategy",
            "technical:learning",
            "portfolio:solve",
        ]
        for route in routes:
            with self.subTest(route=route):
                payload = backend.build(route)
                self.assertNotEqual(payload["status"], "not_applicable")
                self.assertEqual(len(payload["layers"]), 5)
                self.assertEqual(len(payload["mechanism"]["nodes"]), 6)
                visuals = payload["visuals"]
                self.assertEqual(
                    set(visuals),
                    {"descriptive", "history", "diagnostics", "strategy"},
                )
                for block in visuals.values():
                    self.assertTrue(block["table"]["rows"])
                    chart = block["chart"]
                    self.assertTrue(chart.get("traces") or chart.get("heatmap"))
                encoded = json.dumps(payload, ensure_ascii=False)
                self.assertLess(
                    len(encoded),
                    100_000,
                    "Evidence endpoint must stay compact enough for interactive use.",
                )

    def test_allocation_uses_full_vnext_champion_snapshot(self):
        payload = backend.build("allocation:strategy")
        self.assertEqual(payload["champion"]["id"], "B06")
        self.assertEqual(
            payload["governance"]["promotion_gate"]["status"], "conditional"
        )
        self.assertEqual(
            [row["split"] for row in payload["metrics"]],
            ["train", "validation", "test"],
        )

    def test_tracking_pages_do_not_invent_strategy_metrics(self):
        payload = backend.build("liquidity:foreign")
        self.assertEqual(payload["status"], "tracking_not_return_model")
        self.assertNotIn("metrics", payload)
        self.assertEqual(
            payload["governance"]["model_metric_policy"],
            "no_sharpe_for_tracking_pages",
        )

    def test_factor_shadow_is_explicitly_not_promotion_eligible(self):
        payload = backend.build("factorlab:dashboard")
        self.assertTrue(payload["candidate_diagnostics"])
        self.assertFalse(payload["shadow_challenger"]["promotion_eligible"])
        self.assertEqual(payload["governance"]["test_policy"], "report_only")

    def test_kline_remains_observe_only_until_cross_section_validation(self):
        payload = backend.build("technical:learning")
        self.assertEqual(payload["status"], "observe_only")
        self.assertEqual(payload["governance"]["status"], "observe_only")
        self.assertGreaterEqual(
            len(payload["descriptive"]["required_validation"]), 5
        )

    def test_report_references_are_direct_pdf_links(self):
        for route in ("allocation:cycle", "factorlab:dashboard", "technical:learning"):
            for reference in backend.build(route).get("references") or []:
                self.assertTrue(reference["url"].lower().endswith(".pdf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
