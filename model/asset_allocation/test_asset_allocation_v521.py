from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from asset_allocation_v521 import (
    ENGINE_VERSION_V521,
    STRATEGIC_HOLD_MODE_V521,
    apply_validation_governance_v521,
)


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "output" / "model_improvement" / "asset_allocation_snapshot_v52_candidate.json"


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class AssetAllocationV521GovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CANDIDATE.exists():
            raise unittest.SkipTest("frozen v5.2 candidate snapshot not built")
        cls.raw = json.loads(CANDIDATE.read_text(encoding="utf-8"))
        cls.governed = apply_validation_governance_v521(cls.raw)

    def test_policy_order_and_fallback(self):
        self.assertEqual(
            self.governed["benchmark"]["weights"],
            {"equity": 0.6, "bond": 0.15, "gold": 0.1, "commodity": 0.15},
        )
        self.assertEqual(
            self.governed["allocations"]["recommended_mode"],
            STRATEGIC_HOLD_MODE_V521,
        )
        self.assertEqual(
            self.governed["allocations"]["recommended"]["weights"],
            self.governed["allocations"]["strategic_benchmark"]["weights"],
        )

    def test_dynamic_weights_and_backtests_are_unchanged(self):
        for key in ("benchmark_relative", "absolute_no_benchmark"):
            self.assertEqual(
                digest(self.raw["allocations"][key]),
                digest(self.governed["allocations"][key]),
            )
            self.assertEqual(
                digest(self.raw["backtest"]["strategies"][key]),
                digest(self.governed["backtest"]["strategies"][key]),
            )

    def test_governance_does_not_read_test(self):
        changed = copy.deepcopy(self.raw)
        changed["backtest"]["strategies"]["benchmark_relative"]["metrics"]["test"]["sharpe"] = 999.0
        governed = apply_validation_governance_v521(changed)
        self.assertEqual(
            governed["allocations"]["recommended_mode"],
            self.governed["allocations"]["recommended_mode"],
        )
        self.assertFalse(governed["deployment_decision"]["uses_retrospective_test"])

    def test_strength_and_allocation_are_explicit(self):
        for mode in ("benchmark_relative", "absolute_no_benchmark"):
            rows = self.governed["asset_decisions"][mode]
            self.assertEqual(set(rows), {"equity", "bond", "gold", "commodity"})
            for row in rows.values():
                self.assertIn(row["strength_label_cn"], {"最强", "偏强", "偏弱", "最弱"})
                self.assertTrue(row["decision_summary_cn"])
                self.assertTrue(row["strength_is_not_weight_rank"])

    def test_contract_and_claim_are_machine_readable(self):
        self.assertEqual(self.governed["engine_version"], ENGINE_VERSION_V521)
        self.assertFalse(self.governed["model_contract"]["selection_uses_test"])
        self.assertFalse(self.governed["model_contract"]["performance_guarantee"])
        self.assertFalse(self.governed["performance_claim"]["validated_positive_excess"])
        self.assertFalse(self.governed["governance_correction_v521"]["dynamic_weights_changed"])


if __name__ == "__main__":
    unittest.main()
