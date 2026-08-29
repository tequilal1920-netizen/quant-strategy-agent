"""Numerical contract tests for :mod:`allocation_math_v5`.

The assertions focus on model identities and invariants rather than snapshots:
PSD covariance construction, Euler risk decomposition, risk-budget accuracy,
Black--Litterman view algebra, and strict feasibility of the unified optimizer.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import numpy as np

try:
    from .allocation_math_v5 import (
        BlackLittermanResultV5,
        CovarianceBundleV5,
        black_litterman_posterior_v5,
        estimate_statistical_covariance_v5,
        fit_macro_factor_covariance_v5,
        idzorek_omega_v5,
        nearest_positive_semidefinite_v5,
        optimize_allocation_v5,
        portfolio_risk_contribution_v5,
        reverse_equilibrium_returns_v5,
        solve_constrained_risk_budget_v5,
        solve_erc_v5,
    )
except ImportError:  # Direct unittest discovery from this directory.
    from allocation_math_v5 import (
        BlackLittermanResultV5,
        CovarianceBundleV5,
        black_litterman_posterior_v5,
        estimate_statistical_covariance_v5,
        fit_macro_factor_covariance_v5,
        idzorek_omega_v5,
        nearest_positive_semidefinite_v5,
        optimize_allocation_v5,
        portfolio_risk_contribution_v5,
        reverse_equilibrium_returns_v5,
        solve_constrained_risk_budget_v5,
        solve_erc_v5,
    )


def _assert_psd(test_case: unittest.TestCase, matrix: np.ndarray, tolerance: float = 1.0e-11) -> None:
    test_case.assertTrue(np.allclose(matrix, matrix.T, atol=tolerance, rtol=0.0))
    test_case.assertGreaterEqual(float(np.linalg.eigvalsh(matrix).min()), -tolerance)


def _four_asset_covariance() -> np.ndarray:
    volatility = np.array([0.045, 0.020, 0.055, 0.035])
    correlation = np.array(
        [
            [1.00, -0.15, 0.35, 0.05],
            [-0.15, 1.00, -0.10, 0.10],
            [0.35, -0.10, 1.00, 0.25],
            [0.05, 0.10, 0.25, 1.00],
        ]
    )
    return correlation * np.outer(volatility, volatility)


def _bundle(covariance: np.ndarray | None = None) -> CovarianceBundleV5:
    matrix = _four_asset_covariance() if covariance is None else np.asarray(covariance, dtype=float)
    loadings = np.array(
        [
            [1.00, -0.10],
            [-0.30, 0.00],
            [0.50, 0.90],
            [0.00, 1.00],
        ]
    )
    return CovarianceBundleV5(
        covariance=matrix,
        factor_loadings=loadings,
        factor_covariance=np.diag([0.0010, 0.0007]),
        specific_covariance=np.diag(np.maximum(np.diag(matrix) * 0.40, 1.0e-8)),
        statistical_covariance=matrix.copy(),
        macro_blend_weight=0.50,
        factor_names=("growth", "inflation"),
        diagnostics={"fixture": np.array([1.0, 2.0])},
    )


def _posterior(
    covariance: np.ndarray,
    prior_weights: np.ndarray,
    mean: np.ndarray | None = None,
    mean_covariance: np.ndarray | None = None,
) -> BlackLittermanResultV5:
    expected = np.array([0.020, 0.002, 0.009, 0.005]) if mean is None else np.asarray(mean, dtype=float)
    uncertainty = np.eye(4) * 0.00010 if mean_covariance is None else np.asarray(mean_covariance, dtype=float)
    return BlackLittermanResultV5(
        prior_weights=prior_weights.copy(),
        pi=reverse_equilibrium_returns_v5(covariance, prior_weights, 2.5),
        delta=2.5,
        tau=0.05,
        P=np.zeros((0, 4)),
        q=np.zeros(0),
        omega=np.zeros((0, 0)),
        posterior_mean=expected,
        posterior_mean_covariance=uncertainty,
        predictive_covariance=covariance + uncertainty,
        diagnostics={"fixture": True},
    )


class CovarianceTests(unittest.TestCase):
    def test_nearest_psd_repairs_negative_eigenvalue(self) -> None:
        raw = np.array([[1.0, 1.2], [1.2, 1.0]])
        repaired, diagnostics = nearest_positive_semidefinite_v5(raw)
        _assert_psd(self, repaired)
        self.assertLess(diagnostics["minimum_eigenvalue_before"], 0.0)
        self.assertGreaterEqual(diagnostics["minimum_eigenvalue_after"], 0.0)

    def test_statistical_covariance_is_symmetric_psd_and_deterministic(self) -> None:
        rng = np.random.default_rng(517)
        returns = rng.normal(size=(160, 4)) @ np.array(
            [
                [0.03, 0.01, 0.00, 0.00],
                [0.00, 0.02, 0.01, 0.00],
                [0.01, 0.00, 0.04, 0.01],
                [0.00, 0.00, 0.01, 0.025],
            ]
        )
        first, diagnostics = estimate_statistical_covariance_v5(
            returns, half_life=36.0, diagonal_shrinkage=0.25
        )
        second, _ = estimate_statistical_covariance_v5(
            returns, half_life=36.0, diagonal_shrinkage=0.25
        )
        _assert_psd(self, first)
        np.testing.assert_allclose(first, second, atol=0.0, rtol=0.0)
        self.assertGreater(diagnostics["effective_observations"], 1.0)
        self.assertLessEqual(diagnostics["effective_observations"], len(returns))

    def test_macro_factor_covariance_obeys_documented_blend_identity(self) -> None:
        rng = np.random.default_rng(20260811)
        factors = rng.multivariate_normal(
            np.zeros(3),
            np.array(
                [
                    [0.0010, 0.0002, -0.0001],
                    [0.0002, 0.0007, 0.0001],
                    [-0.0001, 0.0001, 0.0005],
                ]
            ),
            size=240,
        )
        true_loadings = np.array(
            [
                [0.9, 0.1, -0.2],
                [-0.4, -0.1, 0.2],
                [0.3, 1.1, 0.3],
                [0.0, 0.5, 0.8],
            ]
        )
        residual = rng.normal(size=(240, 4)) * np.array([0.012, 0.007, 0.017, 0.010])
        returns = factors @ true_loadings.T + residual
        rho = 0.60
        bundle = fit_macro_factor_covariance_v5(
            returns,
            factors,
            macro_blend_weight=rho,
            factor_names=("growth", "inflation", "liquidity"),
            ridge_penalty=0.05,
            diagonal_shrinkage=0.20,
        )
        macro = (
            bundle.factor_loadings
            @ bundle.factor_covariance
            @ bundle.factor_loadings.T
            + bundle.specific_covariance
        )
        expected = rho * macro + (1.0 - rho) * bundle.statistical_covariance
        np.testing.assert_allclose(bundle.covariance, expected, atol=2.0e-11, rtol=2.0e-10)
        _assert_psd(self, bundle.covariance)
        self.assertEqual(bundle.factor_names, ("growth", "inflation", "liquidity"))
        self.assertEqual(bundle.diagnostics["status"], "ok")

    def test_macro_covariance_falls_back_when_history_is_insufficient(self) -> None:
        rng = np.random.default_rng(91)
        returns = rng.normal(0.0, 0.02, size=(20, 4))
        factors = rng.normal(0.0, 0.01, size=(20, 3))
        bundle = fit_macro_factor_covariance_v5(
            returns,
            factors,
            macro_blend_weight=0.75,
            min_observations=36,
        )
        self.assertEqual(bundle.macro_blend_weight, 0.0)
        np.testing.assert_allclose(bundle.covariance, bundle.statistical_covariance)
        self.assertEqual(bundle.diagnostics["status"], "fallback_statistical_covariance")


class RiskBudgetTests(unittest.TestCase):
    def test_euler_risk_contributions_sum_to_portfolio_volatility(self) -> None:
        covariance = _four_asset_covariance()
        weights = np.array([0.35, 0.30, 0.20, 0.15])
        volatility, contribution, relative = portfolio_risk_contribution_v5(covariance, weights)
        self.assertAlmostEqual(float(contribution.sum()), volatility, places=12)
        self.assertAlmostEqual(float(relative.sum()), 1.0, places=12)

    def test_strict_erc_matches_inverse_volatility_for_diagonal_covariance(self) -> None:
        volatility = np.array([0.10, 0.20, 0.30, 0.40])
        covariance = np.diag(volatility * volatility)
        result = solve_erc_v5(covariance)
        expected = (1.0 / volatility) / np.sum(1.0 / volatility)
        self.assertEqual(result.status, "optimal")
        np.testing.assert_allclose(result.weights, expected, atol=2.0e-8, rtol=0.0)
        np.testing.assert_allclose(result.relative_risk_contribution, 0.25, atol=2.0e-8, rtol=0.0)
        self.assertLess(float(np.max(np.abs(result.budget_error))), 2.0e-8)

    def test_erc_is_invariant_to_positive_covariance_scaling(self) -> None:
        covariance = _four_asset_covariance()
        base = solve_erc_v5(covariance)
        scaled = solve_erc_v5(37.0 * covariance)
        np.testing.assert_allclose(base.weights, scaled.weights, atol=2.0e-8, rtol=0.0)

    def test_unconstrained_custom_risk_budget_is_met(self) -> None:
        covariance = _four_asset_covariance()
        budget = np.array([0.40, 0.30, 0.20, 0.10])
        result = solve_constrained_risk_budget_v5(covariance, budget)
        self.assertEqual(result.status, "optimal")
        np.testing.assert_allclose(result.relative_risk_contribution, budget, atol=2.0e-7, rtol=0.0)

    def test_constrained_risk_budget_respects_active_weight_cap(self) -> None:
        volatility = np.array([0.10, 0.20, 0.30, 0.40])
        result = solve_constrained_risk_budget_v5(
            np.diag(volatility * volatility),
            np.full(4, 0.25),
            lower_bounds=[0.05, 0.05, 0.05, 0.05],
            upper_bounds=[0.35, 0.70, 0.70, 0.70],
            tolerance=1.0e-8,
        )
        self.assertIn(result.status, {"optimal", "approximate_constrained"})
        self.assertAlmostEqual(float(result.weights.sum()), 1.0, places=7)
        self.assertTrue(np.all(result.weights >= 0.05 - 1.0e-7))
        self.assertTrue(np.all(result.weights <= np.array([0.35, 0.70, 0.70, 0.70]) + 1.0e-7))
        self.assertAlmostEqual(float(result.weights[0]), 0.35, places=5)
        self.assertIn("upper_0", result.active_constraints)

    def test_constrained_risk_budget_respects_exact_turnover_cap(self) -> None:
        volatility = np.array([0.10, 0.20, 0.30, 0.40])
        previous = np.full(4, 0.25)
        cap = 0.05
        result = solve_constrained_risk_budget_v5(
            np.diag(volatility * volatility),
            np.full(4, 0.25),
            previous_weights=previous,
            turnover_cap=cap,
            tolerance=1.0e-8,
        )
        exact_turnover = 0.5 * float(np.abs(result.weights - previous).sum())
        self.assertIn(result.status, {"optimal", "approximate_constrained"})
        self.assertAlmostEqual(float(result.weights.sum()), 1.0, places=8)
        self.assertLessEqual(exact_turnover, cap + 1.0e-8)
        self.assertAlmostEqual(exact_turnover, cap, places=6)
        self.assertIn("turnover", result.active_constraints)


class BlackLittermanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.covariance = _four_asset_covariance()
        self.weights = np.array([0.30, 0.35, 0.20, 0.15])
        self.delta = 2.5
        self.tau = 0.05

    def test_no_view_posterior_equals_equilibrium_prior(self) -> None:
        result = black_litterman_posterior_v5(
            self.covariance,
            self.weights,
            delta=self.delta,
            tau=self.tau,
        )
        expected_pi = reverse_equilibrium_returns_v5(self.covariance, self.weights, self.delta)
        np.testing.assert_allclose(result.posterior_mean, expected_pi, atol=1.0e-14, rtol=0.0)
        np.testing.assert_allclose(
            result.posterior_mean_covariance,
            self.tau * self.covariance,
            atol=1.0e-14,
            rtol=1.0e-12,
        )
        _assert_psd(self, result.predictive_covariance)

    def test_neutral_view_does_not_move_posterior_mean(self) -> None:
        pi = reverse_equilibrium_returns_v5(self.covariance, self.weights, self.delta)
        P = np.array([[1.0, -1.0, 0.0, 0.0]])
        views = SimpleNamespace(
            P=P,
            q=P @ pi,
            omega=np.array([[0.00010]]),
            cycle_contributions={},
            forecast_error_covariance=np.array([[0.00010]]),
            diagnostics={"source": "unit-test"},
        )
        result = black_litterman_posterior_v5(
            self.covariance,
            self.weights,
            delta=self.delta,
            tau=self.tau,
            views=views,
        )
        np.testing.assert_allclose(result.posterior_mean, pi, atol=1.0e-13, rtol=0.0)
        self.assertEqual(result.diagnostics["views"]["source"]["source"], "unit-test")

    def test_view_row_rescaling_is_algebraically_invariant(self) -> None:
        P = np.array([[1.0, 0.0, -1.0, 0.0]])
        q = np.array([0.012])
        omega = np.array([[0.00008]])
        first = black_litterman_posterior_v5(
            self.covariance,
            self.weights,
            delta=self.delta,
            tau=self.tau,
            views=SimpleNamespace(P=P, q=q, omega=omega, diagnostics={}),
        )
        scale = 7.0
        second = black_litterman_posterior_v5(
            self.covariance,
            self.weights,
            delta=self.delta,
            tau=self.tau,
            views=SimpleNamespace(
                P=scale * P,
                q=scale * q,
                omega=(scale * scale) * omega,
                diagnostics={},
            ),
        )
        np.testing.assert_allclose(first.posterior_mean, second.posterior_mean, atol=2.0e-12, rtol=1.0e-10)
        np.testing.assert_allclose(
            first.posterior_mean_covariance,
            second.posterior_mean_covariance,
            atol=2.0e-12,
            rtol=1.0e-10,
        )

    def test_idzorek_uncertainty_decreases_with_confidence(self) -> None:
        P = np.array([[1.0, -1.0, 0.0, 0.0], [1.0, -1.0, 0.0, 0.0]])
        omega = idzorek_omega_v5(self.covariance, P, [0.35, 0.90], self.tau)
        self.assertGreater(float(omega[0, 0]), float(omega[1, 1]))
        self.assertEqual(float(omega[0, 1]), 0.0)

    def test_collinear_view_rows_are_rejected(self) -> None:
        views = SimpleNamespace(
            P=np.array([[1.0, -1.0, 0.0, 0.0], [2.0, -2.0, 0.0, 0.0]]),
            q=np.array([0.01, 0.02]),
            omega=np.diag([0.0001, 0.0002]),
            diagnostics={},
        )
        with self.assertRaisesRegex(ValueError, "linearly_independent"):
            black_litterman_posterior_v5(
                self.covariance,
                self.weights,
                delta=self.delta,
                tau=self.tau,
                views=views,
            )


class UnifiedOptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = _bundle()
        self.anchor = solve_erc_v5(self.bundle.covariance)
        self.previous = np.array([0.25, 0.25, 0.25, 0.25])
        self.posterior = _posterior(self.bundle.covariance, self.anchor.weights)

    def test_optimizer_satisfies_every_enabled_hard_constraint(self) -> None:
        constraints = {
            "lower_bounds": [0.10, 0.10, 0.10, 0.10],
            "upper_bounds": [0.55, 0.55, 0.55, 0.55],
            "max_turnover": 0.25,
            "annualization": 12.0,
            "max_annual_volatility": 0.20,
            "max_annual_tracking_error": 0.12,
            "factor_lower_bounds": [-0.50, -0.50],
            "factor_upper_bounds": [0.80, 0.80],
            "stress_returns": [
                [-0.18, 0.02, -0.12, -0.05],
                [-0.05, 0.01, -0.22, -0.08],
            ],
            "max_stress_loss": [0.15, 0.18],
            "linear_inequality_matrix": [[1.0, 0.0, 1.0, 0.0]],
            "linear_inequality_upper": [0.65],
        }
        result = optimize_allocation_v5(
            self.posterior,
            self.anchor,
            self.bundle,
            self.previous,
            constraints,
            {"linear": [0.0005] * 4, "quadratic": [0.002] * 4},
            {
                "risk_aversion": 2.0,
                "uncertainty_penalty": 0.25,
                "anchor_penalty": 0.10,
            },
        )
        self.assertNotEqual(result.status, "infeasible")
        self.assertAlmostEqual(float(result.weights.sum()), 1.0, places=8)
        self.assertLessEqual(float(result.constraint_slack["max_violation"]), 1.0e-7)
        self.assertLessEqual(result.turnover, 0.25 + 1.0e-7)
        self.assertTrue(np.all(result.constraint_slack["factor_lower_slack"] >= -1.0e-7))
        self.assertTrue(np.all(result.constraint_slack["factor_upper_slack"] >= -1.0e-7))
        self.assertTrue(np.all(result.constraint_slack["stress_slack"] >= -1.0e-7))
        self.assertTrue(np.all(result.constraint_slack["linear_inequality_slack"] >= -1.0e-7))
        self.assertFalse(result.diagnostics["hard_constraints_relaxed"])

    def test_transaction_cost_penalty_reduces_turnover(self) -> None:
        constraints = {
            "lower_bounds": [0.05, 0.05, 0.05, 0.05],
            "upper_bounds": [0.80, 0.80, 0.80, 0.80],
        }
        robust = {
            "risk_aversion": 0.25,
            "uncertainty_penalty": 0.0,
            "anchor_penalty": 0.0,
        }
        no_cost = optimize_allocation_v5(
            self.posterior,
            self.anchor,
            self.bundle,
            self.previous,
            constraints,
            {"linear": 0.0, "quadratic": 0.0},
            robust,
        )
        high_cost = optimize_allocation_v5(
            self.posterior,
            self.anchor,
            self.bundle,
            self.previous,
            constraints,
            {"linear": 0.10, "quadratic": 0.10},
            robust,
        )
        self.assertEqual(no_cost.status, "optimal")
        self.assertEqual(high_cost.status, "optimal")
        self.assertGreater(no_cost.turnover, 0.20)
        self.assertLess(high_cost.turnover, no_cost.turnover)

    def test_infeasible_hard_constraint_returns_explicit_failure_without_relaxation(self) -> None:
        covariance = np.diag([0.04, 0.04, 0.04, 0.04])
        bundle = _bundle(covariance)
        anchor = solve_erc_v5(covariance)
        posterior = _posterior(covariance, anchor.weights)
        result = optimize_allocation_v5(
            posterior,
            anchor,
            bundle,
            self.previous,
            {
                "lower_bounds": [0.0, 0.0, 0.0, 0.0],
                "upper_bounds": [1.0, 1.0, 1.0, 1.0],
                "annualization": 12.0,
                "max_annual_volatility": 0.05,
            },
            {"linear": 0.0, "quadratic": 0.0},
            {"risk_aversion": 1.0},
        )
        self.assertEqual(result.status, "infeasible")
        self.assertEqual(result.fallback_level, 3)
        self.assertTrue(np.all(np.isnan(result.weights)))
        self.assertEqual(result.diagnostics["reason"], "hard_constraints_infeasible_or_solver_failed")

    def test_result_payloads_are_json_serializable(self) -> None:
        result = optimize_allocation_v5(
            self.posterior,
            self.anchor,
            self.bundle,
            self.previous,
            {"lower_bounds": [0.05] * 4, "upper_bounds": [0.80] * 4},
            {"linear": 0.001, "quadratic": 0.001},
            {"risk_aversion": 1.0, "uncertainty_penalty": 0.10},
        )
        payload = {
            "covariance": self.bundle.to_dict(),
            "risk_budget": self.anchor.to_dict(),
            "posterior": self.posterior.to_dict(),
            "optimizer": result.to_dict(),
        }
        encoded = json.dumps(payload, allow_nan=False)
        self.assertIn("posterior_mean", encoded)
        self.assertIn("constraint_slack", encoded)


if __name__ == "__main__":
    unittest.main()
