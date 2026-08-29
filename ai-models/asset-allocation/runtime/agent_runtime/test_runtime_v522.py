from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.core import QueryError, query


RESULT = "\u7ed3\u679c"


class AssetRuntimeV522Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cycles = {
            "kondratieff": {"state": "display_only", "confidence": 0.0},
            "juglar": {"state": "not_admitted", "confidence": 0.3},
            "kitchin": {"state": "not_admitted", "confidence": 0.25},
            "merrill": {"state": "not_admitted", "confidence": 0.37},
            "pring": {
                "state": "4",
                "state_name": "phase_4",
                "confidence": 0.60,
                "probabilities": {"4": 0.60},
            },
        }
        availability = {
            name: {
                "state": row["state"],
                "confidence": row["confidence"],
                "eligible_for_views": name == "pring",
                "data_status": (
                    "D3_upstream_total_return_registry"
                    if name == "pring"
                    else "pit_or_vintage_not_verified"
                ),
                "required_pillars": ["growth"],
                "present_pillars": [] if name != "pring" else ["market"],
                "missing_pillars": ["growth"] if name != "pring" else [],
                "missing_required_factors": (
                    ["verified_growth"] if name != "pring" else []
                ),
                "observed_fields": {},
                "admission_reason": (
                    None if name == "pring" else "pit_or_vintage_not_verified"
                ),
            }
            for name, row in cycles.items()
        }
        snapshot = {
            "schema_version": "5.2.2",
            "generated_at": "2026-08-11T00:00:00Z",
            "status": "ready",
            "asset_order": list(("equity", "bond", "gold", "commodity")),
            "benchmark": {
                "id": "strategic_60_15_15_10",
                "internal_asset_order": [
                    "equity",
                    "bond",
                    "gold",
                    "commodity",
                ],
                "weights": {
                    "equity": 0.60,
                    "bond": 0.15,
                    "gold": 0.10,
                    "commodity": 0.15,
                },
            },
            "allocations": {
                "recommended_mode": "benchmark_relative",
                "current_cycle": {"month": "202606", "cycles": cycles},
                "recommended": {
                    "weights": {
                        "equity": 0.5075,
                        "bond": 0.20,
                        "gold": 0.0925,
                        "commodity": 0.20,
                    },
                    "risk_contribution": {"equity": 0.7},
                    "metadata": {"current_rebalance_turnover": 0.08},
                },
                "benchmark_relative": {
                    "weights": {
                        "equity": 0.51,
                        "bond": 0.20,
                        "gold": 0.09,
                        "commodity": 0.20,
                    }
                },
                "absolute_no_benchmark": {
                    "weights": {
                        "equity": 0.10,
                        "bond": 0.75,
                        "gold": 0.05,
                        "commodity": 0.10,
                    }
                },
                "equal_weight_25": {
                    "weights": {
                        "equity": 0.25,
                        "bond": 0.25,
                        "gold": 0.25,
                        "commodity": 0.25,
                    }
                },
            },
            "cycle_factor_availability": {
                "factor_schema_version": "5.1",
                "cycles": availability,
                "admitted_cycles": ["pring"],
                "conflicts": [],
            },
            "quality": {
                "status": "passed",
                "promotion_gate": {
                    "status": "passed",
                    "gate_scope": "user_authorized_sharpe_mandate",
                },
                "statistical_evidence_gate": {
                    "status": "warning",
                    "failed_checks": ["D3", "PIT", "PSR"],
                },
                "statistical_evidence_by_version": {
                    "benchmark_relative": {"status": "warning"}
                },
            },
            "deployment_decision": {
                "status": "user_approved_sharpe_mandate",
                "deployable_dynamic_model": True,
                "executed_mode": "benchmark_relative",
                "authorization_basis": "explicit_user_approval_sharpe_only",
                "uses_retrospective_test": False,
            },
            "model_contract": {"selection_uses_test": False},
            "backtest": {
                "display_benchmarks": {
                    "equal_weight_25": {
                        "id": "equal_weight_25",
                        "weights": {
                            "equity": 0.25,
                            "bond": 0.25,
                            "gold": 0.25,
                            "commodity": 0.25,
                        },
                        "role": "nav_display_only_not_optimizer_input",
                        "optimizer_input": False,
                        "active_return_reference": False,
                    }
                },
                "comparison_policy": {
                    "primary_benchmark": "strategic_60_15_15_10",
                    "benchmark_relative_active_return": (
                        "benchmark_relative minus strategic_benchmark"
                    ),
                },
                "sample_splits": {"test": ["202401", "202606"]},
                "selection_audit": {"selection_uses_test": False},
                "strategies": {
                    "equal_weight_25": {
                        "id": "equal_weight_25",
                        "role": "nav_display_only_not_optimizer_input",
                        "optimizer_input": False,
                        "active_return_reference": False,
                        "weights": {
                            "equity": 0.25,
                            "bond": 0.25,
                            "gold": 0.25,
                            "commodity": 0.25,
                        },
                    },
                    "benchmark_relative": {
                        "metrics": {
                            "retrospective_test": {
                                "annual_return": 0.1342,
                                "annual_volatility": 0.0722,
                                "annual_excess_return": -0.0026,
                                "average_turnover": 0.04,
                                "calmar": 5.28,
                                "months": 30,
                                "positive_month_rate": 0.63,
                                "total_return": 0.36,
                                "sharpe": 1.79,
                            }
                        }
                    }
                },
            },
            "model_evidence_catalog": {
                "cycle_models": {name: {"id": name} for name in cycles},
                "allocation_models": {"black_litterman": {"id": "bl"}},
            },
        }
        (self.root / "asset_allocation_snapshot.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        self.env = patch.dict(
            os.environ, {"QUANT_AGENT_SNAPSHOT_ROOT": str(self.root)}
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_profiles_map_only_to_dynamic_allocations(self) -> None:
        expected = {
            "balanced": ("recommended", 0.5075),
            "equity_preferred": ("benchmark_relative", 0.51),
            "conservative": ("absolute_no_benchmark", 0.10),
            "recommended": ("recommended", 0.5075),
        }
        for profile, (target, equity) in expected.items():
            with self.subTest(profile=profile):
                result = query(
                    "asset-allocation", "current", {"profile": profile}
                )[RESULT]
                self.assertEqual(result["allocation_key"], target)
                self.assertEqual(result["weights"]["equity"], equity)
                self.assertNotEqual(result["allocation_key"], "equal_weight_25")

        with self.assertRaises(QueryError):
            query(
                "asset-allocation",
                "current",
                {"profile": "equal_weight_25"},
            )

    def test_policy_anchor_and_display_line_are_separate(self) -> None:
        result = query("asset-allocation", "current", {})[RESULT]
        contract = result["policy_and_display_benchmark"]
        self.assertEqual(
            contract["policy_anchor"]["weights_in_internal_order"],
            [0.60, 0.15, 0.10, 0.15],
        )
        display = contract["main_nav_display_benchmark"]
        self.assertEqual(display["id"], "equal_weight_25")
        self.assertFalse(display["optimizer_input"])
        self.assertFalse(display["active_return_reference"])

    def test_authorization_and_statistical_evidence_are_separate(self) -> None:
        result = query("asset-allocation", "backtest", {})[RESULT]
        governance = result[
            "service_authorization_and_statistical_evidence"
        ]
        self.assertEqual(
            governance["service_authorization"]["deployment_decision"]["status"],
            "user_approved_sharpe_mandate",
        )
        self.assertEqual(
            governance["service_authorization"]["authorization_gate"]["status"],
            "passed",
        )
        self.assertEqual(
            governance["statistical_evidence"]["gate"]["status"],
            "warning",
        )
        self.assertFalse(governance["selection_uses_retrospective_test"])
        self.assertEqual(
            result["backtest"]["metrics"]["benchmark_relative"][
                "retrospective_test"
            ]["sharpe"],
            1.79,
        )
        self.assertEqual(
            result["backtest"]["metrics"]["benchmark_relative"][
                "retrospective_test"
            ]["annual_volatility"],
            0.0722,
        )

    def test_cycle_reports_all_five_models_and_evidence_catalog(self) -> None:
        result = query("asset-allocation", "cycle", {})[RESULT]
        cycles = result["five_cycle_admission"]["cycles"]
        self.assertEqual(set(cycles), set(CYCLE_NAMES))
        self.assertEqual(
            cycles["juglar"]["missing_required_factors"],
            ["verified_growth"],
        )
        self.assertTrue(cycles["pring"]["admitted"])
        self.assertIn("allocation_models", result["model_evidence_catalog"])


CYCLE_NAMES = ("kondratieff", "juglar", "kitchin", "merrill", "pring")


if __name__ == "__main__":
    unittest.main()
