from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.core import QueryError, catalog, query


class RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixtures = {
            "asset_allocation_snapshot.json": {
                "generated_at": "2026-07-28T00:00:00Z",
                "allocations": {
                    "default_profile": "balanced",
                    "current_cycle": {
                        "month": "202606",
                        "pring_phase_name": "共振下行",
                        "kitchin_state": "被动去库",
                        "juglar_state": "修复",
                        "kondratieff_state": "复苏",
                        "merrill_state": "复苏",
                    },
                    "profiles": {
                        "balanced": {
                            "weights": {"equity": 0.2, "bond": 0.5, "cash": 0.3},
                            "risk_contribution": {},
                            "metadata": {"current_rebalance_turnover": 0.1},
                        }
                    },
                },
                "optimization": {},
            },
            "rotation_snapshot.json": {
                "as_of": "2026-07-16",
                "generated_at": "2026-08-11T00:00:00Z",
                "high_frequency": {
                    "industries": [
                        {
                            "industry": "电子",
                            "rank": 1,
                            "score": 1,
                            "indicators": [
                                {
                                    "name": "半导体销量",
                                    "contribution": 0.4,
                                    "last_available_date": "2026-07-10",
                                }
                            ],
                        }
                    ]
                },
                "six_dimension": {
                    "model_version": "industry-rotation/5.3-champion-anchored-six-dimension",
                    "data_as_of": "2026-06-30",
                    "factor_count": {
                        "prosperity": 5,
                        "fundamental": 12,
                        "technical": 12,
                        "valuation": 4,
                        "funds": 10,
                        "crowding": 10,
                    },
                    "governance": {
                        "selection": "训练与验证",
                        "test": "仅报告或否决",
                        "promotion": "未通过冠军挑战门则保持研究状态",
                    },
                    "diagnostics": {
                        "horizon": "T+1执行收盘至下一执行收盘的行业超额收益",
                        "method": "不重叠标签，在线权重只读取成熟标签",
                    },
                    "data_quality": {
                        "status": "pass_with_quarantined_membership_conflicts",
                        "pit_membership_overlap": 0,
                    },
                },
                "industry": {
                    "frequencies": {
                        "monthly": {
                            "selected_candidate": "C6_direct_month_smooth",
                            "research_selected_candidate": (
                                "C27_monthly_post_test_diagnostic_"
                                "six_dimension_defensive_top10_buffered"
                            ),
                            "metrics": {"test": {"sharpe": 0.12}},
                            "promotion_gate": {
                                "status": "diagnostic_only",
                                "reason": "测试只否决，不能据此晋级",
                            },
                            "candidate_audit": [
                                {
                                    "candidate": (
                                        "C27_monthly_post_test_diagnostic_"
                                        "six_dimension_defensive_top10_buffered"
                                    ),
                                    "candidate_label": "月频质量趋势正交增强",
                                    "architecture": (
                                        "industry-rotation/5.3-champion-anchored-six-dimension"
                                    ),
                                    "train_sharpe": -1.6613927067075684,
                                    "train_excess_sharpe": 0.12603650689743032,
                                    "validation_sharpe": 1.2055691388243355,
                                    "validation_excess_sharpe": 0.8911732596810766,
                                    "objective": 0.7923964816924792,
                                    "report_only_test": {
                                        "sharpe": 0.02550023510169271,
                                        "excess_sharpe": -0.07247292735301254,
                                    },
                                }
                            ],
                            "six_dimension": {
                                "data_as_of": "2026-06-30",
                                "dimensions": [
                                    {"id": "prosperity", "label": "景气度", "role": "return_signal"},
                                    {"id": "fundamental", "label": "基本面", "role": "return_signal"},
                                    {"id": "technical", "label": "技术面", "role": "return_signal"},
                                    {"id": "valuation", "label": "估值", "role": "return_signal"},
                                    {"id": "funds", "label": "资金面", "role": "return_signal"},
                                    {"id": "crowding", "label": "拥挤度", "role": "risk_penalty"},
                                ],
                                "factor_count": {
                                    "prosperity": 5,
                                    "fundamental": 12,
                                    "technical": 12,
                                    "valuation": 4,
                                    "funds": 10,
                                    "crowding": 10,
                                },
                                "research_ranking": [
                                    {
                                        "rank": 1,
                                        "name": "电子",
                                        "code": "801080.SI",
                                        "score": 0.71,
                                        "selected": True,
                                        "weight": 0.1,
                                        "components": {
                                            "prosperity": 0.8,
                                            "fundamental": 0.4,
                                            "technical": 0.7,
                                            "valuation": 0.2,
                                            "funds": 0.6,
                                            "crowding": 0.3,
                                            "anti_crowding": 0.7,
                                        },
                                    },
                                    {
                                        "rank": 2,
                                        "name": "银行",
                                        "code": "801780.SI",
                                        "score": 0.55,
                                        "components": {
                                            "prosperity": 0.3,
                                            "fundamental": 0.8,
                                            "technical": 0.2,
                                            "valuation": 0.9,
                                            "funds": 0.4,
                                            "crowding": 0.1,
                                        },
                                    },
                                ],
                            },
                        },
                        "weekly": {
                            "selected_candidate": "C6_direct_month_smooth",
                            "research_selected_candidate": (
                                "C29_weekly_post_test_diagnostic_"
                                "six_dimension_equal_top10_buffered"
                            ),
                            "metrics": {"test": {"sharpe": -0.03}},
                            "promotion_gate": {"status": "diagnostic_only"},
                            "candidate_audit": [],
                            "six_dimension": {
                                "data_as_of": "2026-06-30",
                                "factor_count": {
                                    "prosperity": 5,
                                    "fundamental": 12,
                                    "technical": 12,
                                    "valuation": 4,
                                    "funds": 10,
                                    "crowding": 10,
                                },
                                "research_ranking": [],
                            },
                        },
                    }
                },
                "style": {"cells": []},
            },
            "liquidity_snapshot.json": {
                "generated_at": "2026-07-28",
                "pages": {
                    "home": {
                        "title": "资金总览",
                        "conclusion": "平稳",
                        "as_of": "2026-07-25",
                        "charts": [
                            {
                                "title": "ETF",
                                "traces": [
                                    {"name": "净流入", "x": ["2026-07-25"], "y": [3]}
                                ],
                            }
                        ],
                    }
                },
                "quality": {"status": "passed"},
            },
            "portfolio_optimization_snapshot.json": {
                "generated_at": "2026-07-28",
                "home": {
                    "selected_candidate": "C1",
                    "selected_solver": "QP",
                    "current_weights": [
                        {"code": "A", "weight": 0.6},
                        {"code": "B", "weight": 0.4},
                    ],
                    "promotion_gate": {"status": "research"},
                },
                "optimization": {},
                "backtest": {},
            },
            "index_enhancement_snapshot.json": {
                "generated_at": "2026-07-28",
                "champion_audit": {"CSI800_ENH": {}},
                "leaderboard": [],
            },
            "kline_cross_sectional_audit.json": {
                "status": "observe_only_no_validated_strategy",
                "candidate_count": 12,
                "eligible_count": 0,
            },
            "global_market_snapshot.json": {
                "as_of": "2026-07-28",
                "rows": [
                    {"market": "A", "ret_1d": 0.01},
                    {"market": "B", "ret_1d": -0.03},
                ],
            },
            "sina_news_snapshot.json": {"as_of": "2026-07-28", "rows": []},
        }
        for name, payload in fixtures.items():
            (self.root / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        self.env = patch.dict(
            os.environ, {"QUANT_AGENT_SNAPSHOT_ROOT": str(self.root)}
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_catalog_has_eight_primary_skills(self) -> None:
        self.assertEqual(len(catalog()["modules"]), 8)

    def test_asset_current(self) -> None:
        result = query("asset-allocation", "current", {"画像": "平衡"})
        self.assertEqual(result["结果"]["周期"]["基钦"], "被动去库")
        self.assertEqual(result["结果"]["资产权重"]["bond"], 0.5)

    def test_industry_ranking_and_driver(self) -> None:
        ranking = query("industry-rotation", "ranking", {"频率": "高频"})
        self.assertEqual(ranking["结果"]["排名"][0]["行业"], "电子")
        drivers = query(
            "industry-rotation", "drivers", {"行业": "电子", "数量": 3}
        )
        self.assertEqual(drivers["结果"]["驱动"][0]["指标"], "半导体销量")

    def test_industry_six_dimension_ranking(self) -> None:
        result = query(
            "industry-rotation", "dimensions", {"频率": "月频", "数量": 1}
        )
        body = result["结果"]
        self.assertEqual(result["数据截止"], "2026-06-30")
        self.assertEqual(body["生产冠军"], "C6_direct_month_smooth")
        self.assertEqual(
            body["模型版本"], "industry-rotation/5.3-champion-anchored-six-dimension"
        )
        self.assertTrue(body["研究挑战者"].startswith("C27_"))
        self.assertEqual(body["因子总数"], 53)
        self.assertEqual(body["排名"][0]["行业"], "电子")
        self.assertEqual(
            list(body["排名"][0]["六维分解"]),
            ["景气度", "基本面", "技术面", "估值", "资金面", "拥挤度"],
        )
        self.assertEqual(
            body["研究审计"]["测试仅报告"]["excess_sharpe"], -0.07247292735301254
        )
        self.assertEqual(body["晋级门禁"]["status"], "diagnostic_only")

    def test_industry_single_dimension_breakdown(self) -> None:
        result = query(
            "industry-rotation", "六维", {"频率": "月频", "行业": "801780.SI"}
        )
        row = result["结果"]["行业分解"]
        self.assertEqual(row["行业"], "银行")
        self.assertEqual(row["六维分解"]["估值"], 0.9)
        self.assertNotIn("低拥挤", row["六维分解"])

    def test_industry_dimensions_never_falls_back_to_production_ranking(self) -> None:
        path = self.root / "rotation_snapshot.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        monthly = payload["industry"]["frequencies"]["monthly"]
        monthly["six_dimension"]["research_ranking"] = []
        monthly["ranking"] = [{"rank": 1, "name": "电子", "score": 99}]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(QueryError, "不会用生产排名代替六维排名"):
            query("industry-rotation", "dimensions", {"频率": "月频"})

    def test_industry_backtest_separates_production_and_research(self) -> None:
        body = query("industry-rotation", "backtest", {})["结果"]["monthly"]
        self.assertEqual(body["绩效口径"], "生产冠军")
        self.assertEqual(body["绩效"]["test"]["sharpe"], 0.12)
        self.assertEqual(
            body["研究挑战者审计"]["测试仅报告"]["sharpe"], 0.02550023510169271
        )
        self.assertIn("不参与", body["测试使用"])

    def test_liquidity_latest_value(self) -> None:
        result = query("liquidity-tracking", "page", {"页面": "主页"})
        self.assertEqual(
            result["结果"]["图表"][0]["最新值"][0]["最新值"], 3.0
        )

    def test_portfolio_filters_dust(self) -> None:
        result = query(
            "portfolio-optimization", "current", {"最小权重": 0.5}
        )
        self.assertEqual(len(result["结果"]["权重"]), 1)

    def test_market_sorted_by_absolute_move(self) -> None:
        result = query("data-dashboard", "market", {"数量": 2})
        self.assertEqual(result["结果"]["市场"][0]["market"], "B")


if __name__ == "__main__":
    unittest.main()
