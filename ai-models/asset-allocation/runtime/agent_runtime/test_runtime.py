from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.core import catalog, query


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
                "industry": {"frequencies": {}},
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
