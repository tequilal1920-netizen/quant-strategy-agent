import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np

MODULE_PATH = Path(__file__).with_name("portfolio_optimization_engine.py")
SPEC = importlib.util.spec_from_file_location("portfolio_optimization_engine", MODULE_PATH)
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)


class PortfolioOptimizationEngineTests(unittest.TestCase):
    def setUp(self):
        self.groups = ["broad_equity", "sector_equity", "bond_cash", "commodity", "overseas_equity"] * 3

    def test_candidate_grid_is_predeclared_and_unique(self):
        candidates = engine.candidate_grid()
        self.assertEqual(len(candidates), 192)
        self.assertEqual(len({row.candidate_id for row in candidates}), 192)
        self.assertEqual(sum(row.expected_return_method == "risk_adjusted_trend" for row in candidates), 64)

    def test_train_selection_prefers_balanced_absolute_and_active_quality(self):
        def row(candidate_id, absolute, active):
            return {
                "candidate_id": candidate_id,
                "train_annual_return": absolute,
                "train_sharpe": absolute,
                "train_calmar": absolute,
                "train_max_drawdown": -0.10 + absolute * 0.01,
                "train_annual_excess_return": active,
                "train_information_ratio": active,
                "train_annual_turnover": 0.50,
                "train_cost_drag": 0.001,
            }

        rows = [
            row("absolute_only", 3.0, -1.0),
            row("balanced", 2.0, 2.0),
            row("active_only", 1.0, 3.0),
        ]
        engine._attach_train_selection_scores(rows)
        scores = {item["candidate_id"]: item["train_selection_score"] for item in rows}
        self.assertGreater(scores["balanced"], scores["absolute_only"])
        self.assertGreater(scores["balanced"], scores["active_only"])

    def test_covariance_is_positive_semidefinite(self):
        rng = np.random.default_rng(7)
        history = rng.normal(0.0002, 0.01, size=(504, 15))
        for method in ("lw", "ewma", "pca", "downside"):
            covariance = engine.covariance_estimate(history, method)
            self.assertGreaterEqual(float(np.linalg.eigvalsh(covariance).min()), -1e-10)

    def test_risk_adjusted_trend_is_finite_and_bounded(self):
        rng = np.random.default_rng(17)
        history = rng.normal(0.0001, 0.012, size=(504, 15))
        covariance = engine.covariance_estimate(history, "ewma")
        forecast = engine.expected_return_estimate(history, covariance, "risk_adjusted_trend")
        self.assertTrue(np.isfinite(forecast).all())
        self.assertLessEqual(float(forecast.max()), 0.20 + 1e-12)
        self.assertGreaterEqual(float(forecast.min()), -0.15 - 1e-12)

    @unittest.skipIf(engine.cp is None, "cvxpy is not installed")
    def test_dpp_solver_and_constraints(self):
        solver = engine.ConvexPortfolioSolver(self.groups)
        self.assertTrue(solver.problem.is_dpp())
        config = engine.CandidateSpec("test", "ewma", "risk_adjusted_trend", 504, 80.0, 0.08, 0.20)
        previous = np.ones(15) / 15
        covariance = np.eye(15) * 0.04
        mu = np.linspace(0.01, 0.12, 15)
        weights, metadata = solver.solve(mu, covariance, previous, config)
        diagnostics = engine.constraint_diagnostics(weights, previous, self.groups, config)
        self.assertEqual(metadata["status"], "optimal")
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)
        self.assertLessEqual(diagnostics["max_violation"], 1e-6)

    def test_invalid_cvxpy_route_uses_constrained_scipy_fallback(self):
        solver = engine.ConvexPortfolioSolver(self.groups)
        config = engine.CandidateSpec("fallback", "ewma", "risk_adjusted_trend", 504, 80.0, 0.08, 0.20)
        previous = np.ones(15) / 15
        covariance = np.eye(15) * 0.04
        mu = np.linspace(0.01, 0.12, 15)
        weights, metadata = solver.solve(
            mu,
            covariance,
            previous,
            config,
            force_solver="INTENTIONALLY_INVALID_SOLVER",
        )
        diagnostics = engine.constraint_diagnostics(weights, previous, self.groups, config)
        self.assertEqual(metadata["status"], "optimal")
        self.assertEqual(metadata["solver"], "SCIPY_SLSQP")
        self.assertLessEqual(diagnostics["max_violation"], 1e-6)
        self.assertGreater(float(weights[-1]), float(weights[0]))

    def test_return_loss_attribution_reconciles_active_return(self):
        rows = [{
            "trade_date": "20230131", "net_return": 0.015, "benchmark_return": 0.010,
            "gross_return": 0.016, "transaction_cost": 0.001,
            "weights": [1.0 / 15.0] * 15,
            "asset_returns": [0.01] * 15,
        }]
        result = engine.return_loss_attribution(rows, [str(index) for index in range(15)], self.groups)
        self.assertEqual(result["splits"]["test"]["months"], 1)
        self.assertAlmostEqual(result["splits"]["test"]["net_active_return_sum"], 0.005, places=12)
        self.assertAlmostEqual(result["splits"]["test"]["implementation_residual"], 0.005, places=12)

    def test_active_metrics_use_geometric_annual_returns(self):
        returns = [0.02, -0.01, 0.01, 0.03] * 6
        benchmark = [0.01, -0.005, 0.005, 0.015] * 6
        result = engine.annual_metrics(returns, benchmark)
        strategy_annual = (np.prod(1 + np.asarray(returns)) ** (12 / len(returns))) - 1
        benchmark_annual = (np.prod(1 + np.asarray(benchmark)) ** (12 / len(benchmark))) - 1
        self.assertAlmostEqual(result["annual_excess_return"], strategy_annual - benchmark_annual, places=12)

    def test_active_metrics_drop_nonfinite_strategy_and_benchmark_as_pairs(self):
        returns = [0.01, np.nan, 0.03]
        benchmark = [0.005, 0.50, 0.01]
        result = engine.annual_metrics(returns, benchmark)
        aligned_returns = np.asarray([0.01, 0.03])
        aligned_benchmark = np.asarray([0.005, 0.01])
        strategy_annual = np.prod(1 + aligned_returns) ** (12 / 2) - 1
        benchmark_annual = np.prod(1 + aligned_benchmark) ** (12 / 2) - 1
        self.assertEqual(result["months"], 2)
        self.assertAlmostEqual(
            result["annual_excess_return"],
            strategy_annual - benchmark_annual,
            places=12,
        )

    def test_parameter_registry_matches_predeclared_search_and_solver_order(self):
        registry = {
            (row["group"], row["parameter"]): row["value"]
            for row in engine.parameter_registry()
        }
        self.assertEqual(registry[("验证治理", "candidate_count")], 192)
        self.assertEqual(registry[("验证治理", "dsr_trials")], 192)
        self.assertEqual(registry[("求解器", "conic_primary")], "Clarabel")
        self.assertEqual(
            registry[("求解器", "constrained_fallback")],
            "SciPy SLSQP",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
