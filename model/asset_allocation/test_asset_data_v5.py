"""Tests for the v5 four-asset data contract."""

from __future__ import annotations

import unittest
from dataclasses import replace

from asset_data_v5 import (
    ASSET_ORDER_V5,
    COMMODITY_EXECUTION_CODES_V5,
    build_execution_commodity_basket_v5,
    default_asset_registry_v5,
    reconcile_provider_series_v5,
    validate_asset_registry_v5,
)


class AssetDataV5Tests(unittest.TestCase):
    def test_asset_order_has_gold_and_no_cash(self) -> None:
        self.assertEqual(ASSET_ORDER_V5, ("equity", "bond", "gold", "commodity"))
        self.assertNotIn("cash", ASSET_ORDER_V5)

    def test_default_registry_is_truthfully_not_production_ready(self) -> None:
        registry = default_asset_registry_v5()
        research = validate_asset_registry_v5(registry, require_production=False)
        production = validate_asset_registry_v5(registry, require_production=True)
        self.assertEqual(research["status"], "passed")
        self.assertEqual(production["status"], "failed")
        self.assertTrue(any("production_verification_missing" in row for row in production["errors"]))
        self.assertTrue(registry["commodity"].excludes_gold)
        self.assertEqual(registry["commodity"].gold_weight, 0.0)
        self.assertNotEqual(registry["gold"].research_series_id, registry["commodity"].research_series_id)

    def test_commodity_rejects_equity_proxy_or_gold_overlap(self) -> None:
        registry = default_asset_registry_v5()
        registry["commodity"] = replace(
            registry["commodity"],
            research_series_id="510170.SH",
            execution_code="518880.SH",
        )
        audit = validate_asset_registry_v5(registry, require_production=False)
        self.assertIn("invalid_commodity_proxy_or_gold_overlap", audit["errors"])

    def test_execution_basket_uses_only_three_ex_gold_futures_etfs(self) -> None:
        dates = [f"202001{day:02d}" for day in range(1, 26)]
        components = {}
        for index, code in enumerate(COMMODITY_EXECUTION_CODES_V5):
            components[code] = [
                {"date": date, "close": 100.0 + index + position}
                for position, date in enumerate(dates)
            ]
        rows = build_execution_commodity_basket_v5(components)
        self.assertEqual(len(rows), len(dates))
        self.assertTrue(all(row["excludes_gold"] for row in rows))
        self.assertTrue(all(row["gold_weight"] == 0.0 for row in rows))
        self.assertTrue(all(row["research_only_proxy"] for row in rows))

    def test_provider_cross_check_does_not_silently_accept_breach(self) -> None:
        primary = [
            {"date": "20200101", "close": 100.0},
            {"date": "20200102", "close": 101.0},
        ]
        same = [
            {"date": "20200101", "close": 200.0},
            {"date": "20200102", "close": 202.0},
        ]
        _, passed = reconcile_provider_series_v5(primary, [same], return_tolerance=1.0e-8)
        self.assertEqual(passed["status"], "passed")
        conflict = [
            {"date": "20200101", "close": 200.0},
            {"date": "20200102", "close": 210.0},
        ]
        _, failed = reconcile_provider_series_v5(primary, [conflict], return_tolerance=0.001)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(failed["breaches"]), 1)


if __name__ == "__main__":
    unittest.main()
