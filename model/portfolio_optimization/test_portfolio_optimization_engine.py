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

    def test_active_metrics_use_geometric_annual_returns(self):
        returns = [0.02, -0.01, 0.01, 0.03] * 6
        benchmark = [0.01, -0.005, 0.005, 0.015] * 6
        result = engine.annual_metrics(returns, benchmark)
        strategy_annual = (np.prod(1 + np.asarray(returns)) ** (12 / len(returns))) - 1
        benchmark_annual = (np.prod(1 + np.asarray(benchmark)) ** (12 / len(benchmark))) - 1
        self.assertAlmostEqual(result["annual_excess_return"], strategy_annual - benchmark_annual, places=12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
