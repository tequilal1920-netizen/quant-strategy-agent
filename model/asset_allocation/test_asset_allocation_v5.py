from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np

import asset_allocation_v5 as engine
from asset_data_v5 import ASSET_ORDER_V5, default_asset_registry_v5


def _month_add(year: int, month: int, offset: int) -> tuple[int, int]:
    ordinal = year * 12 + month - 1 + offset
    return ordinal // 12, ordinal % 12 + 1


def _synthetic_panel(count: int = 96):
    rng = np.random.default_rng(240811)
    prices = np.full(4, 100.0)
    panel = {asset: [] for asset in ASSET_ORDER_V5}
    macro = []
    for index in range(count):
        year, month = _month_add(2017, 1, index)
        key = f"{year:04d}{month:02d}"
        if index:
            common = 0.006 + 0.012 * math.sin(index / 9.0)
            shock = rng.normal(0.0, [0.035, 0.012, 0.025, 0.030])
            drift = np.asarray([common, 0.003 - 0.25 * common, 0.004 - 0.10 * common, 0.003 + 0.20 * common])
            prices *= 1.0 + drift + shock
        for position, asset in enumerate(ASSET_ORDER_V5):
            panel[asset].append({"date": key + "28", "close": float(prices[position])})
        release_year, release_month = _month_add(year, month, 1)
        macro.append(
            {
                "month": key,
                "observation_period": key,
                "available_time": f"{release_year:04d}{release_month:02d}15",
                "vintage": "first_release",
                "_pit_verified": True,
                "pmi_manufacturing": 50.0 + 2.0 * math.sin(index / 6.0),
                "pmi_composite": 50.5 + 1.6 * math.sin(index / 6.0),
                "cpi_national_yoy": 2.0 + 0.8 * math.cos(index / 8.0),
                "ppi_yoy": 1.0 + 2.0 * math.cos(index / 7.0),
                "m1_yoy": 7.0 + 2.5 * math.sin(index / 5.0),
                "m2_yoy": 8.0 + 1.0 * math.sin(index / 8.0),
                "sf_stock_endval": 100.0 * (1.0 + index / 120.0),
                "industrial_finished_goods_inventory": 90.0 * (1.0 + index / 150.0),
                "industrial_revenue": 110.0 * (1.0 + index / 100.0),
                "manufacturing_fai": 95.0 * (1.0 + index / 110.0),
                "capacity_utilization": 75.0 + math.sin(index / 10.0),
                "enterprise_medium_long_loan": 80.0 * (1.0 + index / 90.0),
                "source": "synthetic_test_only",
            }
        )
    return panel, macro


def _one_spec():
    return [
        {
            "id": "V5-T01",
            "half_life": 18.0,
            "diagonal_shrinkage": 0.35,
            "macro_blend_weight": 0.25,
            "risk_aversion": 4.0,
            "tau": 0.05,
            "uncertainty_penalty": 0.40,
            "anchor_penalty": 1.25,
        }
    ]


class AssetAllocationV5Tests(unittest.TestCase):
    def test_monthly_panel_is_four_assets_without_cash(self):
        panel, _ = _synthetic_panel(30)
        months, matrix, audit = engine.monthly_prices_v5(panel)
        self.assertEqual(tuple(ASSET_ORDER_V5), ("equity", "bond", "gold", "commodity"))
        self.assertNotIn("cash", ASSET_ORDER_V5)
        self.assertEqual(matrix.shape, (30, 4))
        self.assertEqual(audit["common"]["months"], 30)
        self.assertEqual(months[0], "201701")

    def test_candidate_selection_never_reads_test_metrics(self):
        config = engine.ResearchConfigV5(minimum_train_returns=1, minimum_validation_returns=1)
        base = {
            "metrics": {
                "train": {"months": 12, "annual_return": 0.08, "sharpe": 0.8},
                "validation": {"months": 12, "annual_return": 0.07, "sharpe": 0.7, "average_turnover": 0.1},
                "test": {"months": 12, "annual_return": -0.5, "sharpe": -5.0},
            }
        }
        first = {**base, "spec": {"id": "A"}}
        second = {
            **base,
            "spec": {"id": "B"},
            "metrics": {
                **base["metrics"],
                "validation": {"months": 12, "annual_return": 0.09, "sharpe": 0.9, "average_turnover": 0.1},
                "test": {"months": 12, "annual_return": 5.0, "sharpe": 50.0},
            },
        }
        selected, audit = engine._select_candidate_v5([first, second], config)
        self.assertEqual(selected["spec"]["id"], "B")
        first["metrics"]["test"]["sharpe"] = 1000.0
        second["metrics"]["test"]["sharpe"] = -1000.0
        selected_again, _ = engine._select_candidate_v5([first, second], config)
        self.assertEqual(selected_again["spec"]["id"], "B")
        self.assertFalse(audit["selection_uses_test"])

    def test_kondratieff_never_changes_risk_budget(self):
        cycle = {
            "cycles": {
                "pring": {"eligible_for_views": False, "probabilities": {}},
                "merrill": {"eligible_for_views": False, "probabilities": {}},
                "kitchin": {"eligible_for_views": False, "probabilities": {}},
                "juglar": {"eligible_for_views": False, "probabilities": {}},
                "kondratieff": {"eligible_for_views": True, "probabilities": {"boom": 1.0}},
            }
        }
        budget, audit = engine.cycle_risk_budget_v5(cycle)
        np.testing.assert_allclose(budget, np.full(4, 0.25))
        self.assertEqual(audit["kondratieff_weight"], 0.0)

    def test_default_registry_cannot_enter_production(self):
        panel, macro = _synthetic_panel(30)
        config = engine.ResearchConfigV5(production_mode=True)
        with self.assertRaisesRegex(ValueError, "v5_production_data_gate_failed"):
            engine.build_snapshot_v5(macro, panel, registry=default_asset_registry_v5(), config=config)

    def test_end_to_end_shadow_snapshot_is_auditable(self):
        panel, macro = _synthetic_panel(96)
        config = engine.ResearchConfigV5(
            train_end="202012",
            validation_end="202212",
            lookback_months=18,
            minimum_cycle_train=18,
            minimum_train_returns=12,
            minimum_validation_returns=6,
            minimum_test_returns=6,
            production_mode=False,
        )
        with patch.object(engine, "candidate_grid_v5", return_value=_one_spec()):
            snapshot = engine.build_snapshot_v5(macro, panel, config=config, generated_at="2026-08-11T00:00:00Z")
        self.assertEqual(snapshot["schema_version"], "5.0")
        self.assertEqual(snapshot["status"], "research_only")
        self.assertEqual(snapshot["asset_order"], list(ASSET_ORDER_V5))
        weights = snapshot["allocations"]["recommended"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=7)
        for asset, lower, upper in zip(ASSET_ORDER_V5, config.lower_bounds, config.upper_bounds):
            self.assertGreaterEqual(weights[asset], lower - 1.0e-7)
            self.assertLessEqual(weights[asset], upper + 1.0e-7)
        self.assertFalse(snapshot["backtest"]["selection_audit"]["selection_uses_test"])
        self.assertEqual(snapshot["methodology"]["kondratieff_policy"], "display only; zero risk-budget and BL-view contribution")
        self.assertIn("black_litterman", snapshot["optimization"])
        self.assertIn("risk_budget", snapshot["optimization"])
        self.assertIn("optimizer", snapshot["optimization"])
        self.assertEqual(len(snapshot["model_hash"]), 64)


if __name__ == "__main__":
    unittest.main()
