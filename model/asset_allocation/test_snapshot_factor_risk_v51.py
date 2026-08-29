from __future__ import annotations

import copy
import unittest

import numpy as np

from snapshot_factor_risk_v51 import (
    attach_factor_risk_audit_v51,
    macro_factor_risk_decomposition_v51,
)


class MacroFactorRiskDecompositionV51Test(unittest.TestCase):
    def test_euler_components_reconcile_to_portfolio_variance(self) -> None:
        loadings = np.array([[1.0, 0.2], [0.1, 0.8]])
        factor_covariance = np.array([[0.04, 0.01], [0.01, 0.03]])
        specific = np.diag([0.01, 0.02])
        statistical = np.array([[0.05, 0.01], [0.01, 0.04]])
        rho = 0.6
        covariance = rho * (
            loadings @ factor_covariance @ loadings.T + specific
        ) + (1.0 - rho) * statistical
        result = macro_factor_risk_decomposition_v51(
            {"a": 0.4, "b": 0.6},
            ["a", "b"],
            {
                "covariance": covariance.tolist(),
                "factor_loadings": loadings.tolist(),
                "factor_covariance": factor_covariance.tolist(),
                "specific_covariance": specific.tolist(),
                "statistical_covariance": statistical.tolist(),
                "macro_blend_weight": rho,
                "factor_names": ["growth", "inflation"],
            },
        )
        self.assertEqual(result["status"], "active")
        self.assertAlmostEqual(result["relative_contribution_sum"], 1.0, places=10)
        self.assertAlmostEqual(
            result["portfolio_variance"], result["component_variance_sum"], places=12
        )

    def test_zero_blend_reports_zero_macro_risk_without_invention(self) -> None:
        covariance = np.array([[0.04, 0.01], [0.01, 0.03]])
        result = macro_factor_risk_decomposition_v51(
            {"a": 0.5, "b": 0.5},
            ["a", "b"],
            {
                "covariance": covariance.tolist(),
                "factor_loadings": [[0.0, 0.0], [0.0, 0.0]],
                "factor_covariance": np.eye(2).tolist(),
                "specific_covariance": np.eye(2).tolist(),
                "statistical_covariance": covariance.tolist(),
                "macro_blend_weight": 0.0,
                "factor_names": ["growth", "inflation"],
            },
        )
        self.assertEqual(result["status"], "inactive_by_pit_gate")
        factor_rows = result["rows"][:2]
        self.assertTrue(all(row["risk_contribution"] == 0.0 for row in factor_rows))
        statistical = next(
            row for row in result["rows"] if row["factor"] == "statistical_covariance_risk"
        )
        self.assertAlmostEqual(statistical["risk_contribution"], 1.0, places=12)

    def test_attachment_does_not_change_weights_or_backtest(self) -> None:
        covariance = np.array([[0.04, 0.01], [0.01, 0.03]])
        source = {
            "asset_order": ["a", "b"],
            "allocations": {
                "recommended": {
                    "weights": {"a": 0.5, "b": 0.5},
                    "metadata": {
                        "covariance": {
                            "covariance": covariance.tolist(),
                            "factor_loadings": [[0.0], [0.0]],
                            "factor_covariance": [[1.0]],
                            "specific_covariance": np.eye(2).tolist(),
                            "statistical_covariance": covariance.tolist(),
                            "macro_blend_weight": 0.0,
                            "factor_names": ["growth"],
                        }
                    },
                }
            },
            "backtest": {"sealed_test": {"sharpe": 1.2}},
            "model_hash": "old",
        }
        original = copy.deepcopy(source)
        result = attach_factor_risk_audit_v51(source)
        self.assertEqual(
            result["allocations"]["recommended"]["weights"],
            original["allocations"]["recommended"]["weights"],
        )
        self.assertEqual(result["backtest"], original["backtest"])
        self.assertIn(
            "factor_risk_contribution",
            result["allocations"]["recommended"]["metadata"],
        )


if __name__ == "__main__":
    unittest.main()
