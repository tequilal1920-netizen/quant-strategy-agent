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
                self.assertEqual(len(payload["layers"]), 4)
                self.assertNotIn("mechanism", payload)
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
        self.assertNotIn("id", payload["champion"])
        self.assertEqual(payload["champion"]["family"], "equity_guarded_posterior")
        self.assertEqual(
            payload["governance"]["promotion_gate"]["status"], "conditional"
        )
        self.assertEqual(
            [(row["model"], row["split"]) for row in payload["metrics"]],
            [
                ("战略偏好", "train"), ("战略偏好", "validation"), ("战略偏好", "test"),
                ("稳健绝对", "train"), ("稳健绝对", "validation"), ("稳健绝对", "test"),
            ],
        )
        self.assertFalse(payload["governance"]["objective_champions"]["stable_absolute"]["selection_uses_test"])
        self.assertTrue(
            all("cash_excess_sharpe" in row for row in payload["metrics"])
        )
        self.assertTrue(payload["architecture_comparison"])
        self.assertEqual(payload["architecture_comparison"][0]["id"], "recommended")
        self.assertIn("evidence_gate", payload["architecture_comparison"][0])
        self.assertEqual(
            payload["governance"]["architecture_policy"]["status"],
            "diagnostic_only",
        )
        macro = payload["macro_factor_risk_audit"]
        self.assertEqual(macro["status"], "ok")
        self.assertEqual(
            [row["factor"] for row in macro["factors"]],
            ["增长", "通胀", "流动性", "信用"],
        )
        visual_rows = payload["visuals"]["diagnostics"]["table"]["rows"]
        self.assertTrue(visual_rows)
        self.assertTrue(all("_" not in str(row["candidate"]) for row in visual_rows))

    def test_liquidity_strategy_metrics_keep_tracking_and_return_evidence_separate(self):
        payload = backend.build("liquidity:foreign")
        self.assertEqual(payload["status"], "\u7814\u7a76\u8bca\u65ad")
        self.assertTrue(payload["metrics"])
        self.assertEqual(payload["governance"]["status"], "research_diagnostic")
        self.assertTrue(payload["governance"]["exact_series_only"])
        self.assertFalse(payload["governance"]["selection_uses_test"])
        self.assertFalse(payload["governance"]["promotion_eligible"])
        self.assertGreater(payload["governance"]["validation_sharpe"], 0)

    def test_factor_shadow_is_explicitly_not_promotion_eligible(self):
        payload = backend.build("factorlab:dashboard")
        self.assertTrue(payload["candidate_diagnostics"])
        self.assertFalse(payload["shadow_challenger"]["promotion_eligible"])
        self.assertEqual(payload["governance"]["test_policy"], "report_only")

    def test_factor_evidence_uses_selected_champion_and_chinese_labels(self):
        payload = backend.build("factorlab:dashboard")
        self.assertEqual(
            payload["champion"]["candidate"],
            "自适应ICIR中性组合 · 连续排序、可靠性调仓与波动预算",
        )
        self.assertEqual(payload["champion"]["model"], "自适应ICIR中性组合")
        self.assertNotIn("::", json.dumps(payload["champion"], ensure_ascii=False))
        for row in payload["candidate_diagnostics"]:
            self.assertNotIn("::", row["candidate"])
            self.assertNotIn("_", row["model"])
        factor_rows = payload["visuals"]["descriptive"]["table"]["rows"]
        self.assertTrue(factor_rows)
        self.assertTrue(
            all("_" not in str(row["factor"]) for row in factor_rows)
        )

    def test_rotation_exposes_chinese_production_and_research_candidates(self):
        payload = backend.build("rotation:industry")
        monthly = payload["models"][0]
        self.assertEqual(monthly["model"], "月频行业轮动")
        self.assertNotIn("_", str(monthly["candidate"]))
        self.assertNotIn("_", str(monthly["research_candidate"]))
        rows = payload["visuals"]["diagnostics"]["table"]["rows"]
        self.assertTrue(rows)
        self.assertTrue(all("_" not in str(row["candidate"]) for row in rows))
        for key in (
            "train_absolute_sharpe", "validation_absolute_sharpe",
            "train_excess_sharpe", "validation_excess_sharpe",
            "report_excess_sharpe",
        ):
            self.assertIn(key, rows[0])

    def test_rotation_visuals_bind_real_six_dimension_ranking_nav_and_holdings(self):
        payload = backend.build("rotation:industry")
        visuals = payload["visuals"]
        heatmap = visuals["descriptive"]["chart"]["heatmap"]
        self.assertEqual(
            heatmap["x"],
            ["景气度", "基本面", "技术面", "估值", "资金面", "低拥挤度"],
        )
        self.assertEqual(len(heatmap["y"]), 31)
        self.assertEqual(len(heatmap["z"]), 31)
        self.assertTrue(all(len(row) == 6 for row in heatmap["z"]))

        snapshot = json.loads(
            (MODULE_PATH.parent / "data" / "rotation_snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        frequencies = snapshot["industry"]["frequencies"]
        monthly = frequencies["monthly"]
        six = monthly["six_dimension"]
        self.assertEqual(len(six["research_ranking"]), 31)
        result = monthly.get("research_result") or six["research_result"]
        self.assertTrue(result["nav"])
        research_dates = [row["date"] for row in result["nav"]]
        self.assertEqual(research_dates, sorted(set(research_dates)))
        self.assertGreaterEqual(
            research_dates[0], monthly["common_evaluation_start"]
        )
        self.assertIsInstance(result["holdings"], list)
        latest = result["holdings"][-1]
        self.assertTrue(latest["weights"])

        production_traces = {
            trace["name"]: trace
            for trace in visuals["history"]["chart"]["traces"]
        }
        research_traces = {
            trace["name"]: trace
            for trace in visuals["history"]["secondary_charts"][1]["traces"]
        }
        for frequency, label in (("monthly", "月频"), ("weekly", "周频")):
            model = frequencies[frequency]
            research = (
                model.get("research_result")
                or (model.get("six_dimension") or {}).get("research_result")
            )
            self.assertTrue(research["nav"])
            self.assertEqual(
                production_traces[f"{label}C6生产冠军"]["y"][-1],
                model["nav"][-1]["strategy"],
            )
            self.assertEqual(
                research_traces[f"{label}六维策略"]["y"][-1],
                research["nav"][-1]["strategy"],
            )

        production_latest = monthly["holdings"][-1]
        rows = {
            row["industry"]: row
            for row in visuals["strategy"]["table"]["rows"]
        }
        self.assertEqual(set(rows), {row["name"] for row in monthly["ranking"]})
        for industry, weight in production_latest["weights"].items():
            self.assertEqual(rows[industry]["selected"], "入选")
            self.assertAlmostEqual(rows[industry]["weight"], weight)
        for industry in set(rows) - set(production_latest["weights"]):
            self.assertEqual(rows[industry]["selected"], "未入选")
        self.assertEqual(
            [trace["name"] for trace in visuals["strategy"]["chart"]["traces"]],
            ["C6生产得分", "生产持仓权重"],
        )
        self.assertEqual(
            [trace["name"] for trace in visuals["strategy"]["secondary_charts"][0]["traces"]],
            ["六维综合分", "最新持仓权重"],
        )

    def test_rotation_visuals_never_relabels_production_as_six_dimension(self):
        data = {
            "industry": {
                "frequencies": {
                    "monthly": {
                        "ranking": [
                            {
                                "rank": 1,
                                "name": "生产行业",
                                "score": 0.25,
                                "components": {"prosperity": 0.1},
                            }
                        ],
                        "nav": [
                            {"date": "2026-01-31", "strategy": 1.0, "benchmark": 1.0}
                        ],
                        "research_result": {
                            "nav": [
                                {"date": "2026-01-31", "strategy": 9.0, "benchmark": 8.0}
                            ],
                            "holdings": [
                                {
                                    "names": ["生产行业"],
                                    "weights": {"生产行业": 0.35},
                                }
                            ],
                        },
                    },
                    "weekly": {
                        "nav": [
                            {"date": "2026-01-31", "strategy": 1.0, "benchmark": 1.0}
                        ],
                        "research_result": {
                            "nav": [
                                {"date": "2026-01-31", "strategy": 7.0, "benchmark": 6.0}
                            ]
                        },
                    },
                }
            }
        }
        visuals = backend.rotation_visuals(data, [])
        self.assertEqual(visuals["descriptive"]["chart"]["heatmap"]["y"], [])
        production = visuals["history"]["chart"]["traces"]
        self.assertEqual(production[0]["name"], "月频C6生产冠军")
        self.assertEqual(production[0]["y"], [1.0])
        self.assertEqual(production[2]["name"], "周频C6生产冠军")
        self.assertEqual(production[2]["y"], [1.0])
        research = visuals["history"]["secondary_charts"][1]["traces"]
        self.assertEqual(research[0]["name"], "月频六维策略")
        self.assertEqual(research[0]["y"], [9.0])
        self.assertEqual(research[2]["name"], "周频六维策略")
        self.assertEqual(research[2]["y"], [7.0])
        self.assertEqual(
            visuals["strategy"]["table"]["rows"][0]["industry"],
            "生产行业",
        )
        self.assertEqual(
            visuals["strategy"]["chart"]["traces"][0]["name"],
            "C6生产得分",
        )

    def test_rotation_subpages_use_distinct_graph_first_evidence(self):
        industry = backend.build("rotation:industry")["visuals"]
        style = backend.build("rotation:style")["visuals"]
        allocation = backend.build("rotation:allocation")["visuals"]
        self.assertEqual(
            industry["descriptive"]["chart"]["title"],
            "31行业六维条件色评分",
        )
        self.assertEqual(
            style["descriptive"]["chart"]["title"],
            "十二风格箱条件色矩阵",
        )
        self.assertEqual(
            allocation["descriptive"]["chart"]["title"],
            "月频、周频与风格配置权重",
        )
        for visuals in (industry, style, allocation):
            for block in visuals.values():
                self.assertEqual(block["display"], "charts_only")
                self.assertTrue(block.get("secondary_charts"))
                self.assertTrue((block.get("table") or {}).get("rows"))

    def test_portfolio_optimizer_uses_graphs_for_solver_effectiveness(self):
        payload = backend.build("portfolio:solve")
        visuals = payload["visuals"]
        self.assertEqual(payload["status"], "post_test_diagnostic_candidate")
        self.assertEqual(visuals["diagnostics"]["display"], "charts_only")
        titles = [
            chart["title"]
            for chart in visuals["diagnostics"]["secondary_charts"]
        ]
        self.assertEqual(
            titles,
            ["求解速度、迭代与约束精度", "约束边界紧度"],
        )
        self.assertEqual(
            visuals["history"]["secondary_charts"][0]["title"],
            "同口径历史回撤",
        )
        self.assertEqual(
            visuals["history"]["secondary_charts"][1]["title"],
            "训练、验证、测试表现",
        )
        self.assertEqual(
            [chart["title"] for chart in visuals["strategy"]["secondary_charts"]],
            ["交易成本冲击", "历史压力情景"],
        )
        self.assertTrue(visuals["descriptive"]["chart"].get("heatmap"))
        self.assertTrue((visuals["diagnostics"]["table"] or {}).get("rows"))
    def test_kline_remains_research_only_after_sealed_test_failure(self):
        payload = backend.build("technical:learning")
        self.assertEqual(payload["status"], "研究诊断")
        self.assertEqual(payload["governance"]["status"], "observe_only")
        self.assertEqual(payload["descriptive"]["candidate_count"], 14)
        self.assertFalse(payload["governance"]["selection_uses_test"])
        self.assertFalse(payload["governance"]["release_approved"])

    def test_report_references_are_direct_pdf_links(self):
        for route in ("allocation:cycle", "factorlab:dashboard", "technical:learning"):
            for reference in backend.build(route).get("references") or []:
                self.assertTrue(reference["url"].lower().endswith(".pdf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
