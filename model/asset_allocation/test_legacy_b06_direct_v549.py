from __future__ import annotations

import inspect

import numpy as np
import pytest

import legacy_b06_direct_v549 as direct
from framework.backtest.robust_covariance import robust_covariance
from legacy_b06_direct_v549 import (
    bounded_simplex_v549,
    direct_active_alpha_v549,
    legacy_b06_target_v549,
)


POLICY = np.asarray([0.60, 0.15, 0.10, 0.15], dtype=float)
LOWER = np.asarray([0.10, 0.05, 0.05, 0.05], dtype=float)
UPPER = np.asarray([0.75, 0.40, 0.30, 0.40], dtype=float)


def _history(seed: int = 549) -> np.ndarray:
    """Deterministic, non-exchangeable monthly four-asset history."""

    rng = np.random.default_rng(seed)
    time = np.arange(36, dtype=float)
    deterministic = np.column_stack(
        [
            0.008 + 0.020 * np.sin(time / 3.3),
            0.003 + 0.005 * np.cos(time / 4.7),
            0.005 + 0.013 * np.sin(time / 5.1 + 0.8),
            0.004 + 0.026 * np.cos(time / 3.8 + 0.3),
        ]
    )
    noise = rng.normal(0.0, [0.004, 0.001, 0.003, 0.006], size=(36, 4))
    return deterministic + noise


def _expected_box_projection(
    values: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    """Independent Euclidean projection on {sum(w)=1, lower<=w<=upper}."""

    left = float(np.min(values - upper)) - 1.0
    right = float(np.max(values - lower)) + 1.0
    for _ in range(200):
        middle = (left + right) / 2.0
        projected = np.clip(values - middle, lower, upper)
        if float(projected.sum()) > 1.0:
            left = middle
        else:
            right = middle
    return np.clip(values - (left + right) / 2.0, lower, upper)


def _target(history: np.ndarray) -> tuple[np.ndarray, dict]:
    return legacy_b06_target_v549(
        history,
        policy_weights=POLICY,
        lower_bounds=LOWER,
        upper_bounds=UPPER,
    )


def test_bounded_simplex_is_the_exact_box_constrained_projection():
    values = np.asarray([1.25, -0.40, 0.32, 0.91])
    actual = bounded_simplex_v549(values, LOWER, UPPER)
    expected = _expected_box_projection(values, LOWER, UPPER)

    assert np.allclose(actual, expected, atol=1.0e-12)
    assert abs(float(actual.sum()) - 1.0) <= 1.0e-12
    assert np.all(actual >= LOWER - 1.0e-12)
    assert np.all(actual <= UPPER + 1.0e-12)
    assert np.allclose(
        bounded_simplex_v549(actual, LOWER, UPPER), actual, atol=1.0e-12
    )

    with pytest.raises(ValueError):
        bounded_simplex_v549(values, np.full(4, 0.30), UPPER)


def test_tactical_uses_raw_positive_score_normalization_not_ranks():
    _, diagnostics = _target(_history())
    raw = np.asarray(diagnostics["raw_tactical_score"], dtype=float)
    tactical = np.asarray(diagnostics["tactical_weights"], dtype=float)

    assert raw.shape == tactical.shape == (4,)
    assert np.all(np.isfinite(raw)) and np.all(raw > 0.0)
    assert np.allclose(tactical, raw / float(raw.sum()), atol=1.0e-12)

    ranks = np.empty(4, dtype=float)
    ranks[np.argsort(raw)] = np.arange(1.0, 5.0)
    assert not np.allclose(tactical, ranks / float(ranks.sum()), atol=1.0e-6)


def test_policy_anchor_is_exactly_ten_percent_policy_ninety_percent_tactical():
    _, diagnostics = _target(_history())
    tactical = np.asarray(diagnostics["tactical_weights"], dtype=float)
    anchored = np.asarray(diagnostics["policy_anchored_weights"], dtype=float)

    assert np.allclose(anchored, 0.10 * POLICY + 0.90 * tactical, atol=1.0e-12)
    assert abs(float(anchored.sum()) - 1.0) <= 1.0e-12
    assert diagnostics["policy_anchor_weight"] == pytest.approx(0.10)


def test_stability_sleeve_is_bounded_inverse_volatility_and_exactly_blended():
    _, diagnostics = _target(_history())
    covariance_12 = np.asarray(diagnostics["robust_covariance_12m"], dtype=float)
    sigma = np.sqrt(np.maximum(np.diag(covariance_12), 1.0e-16))
    inverse_volatility = 1.0 / sigma
    inverse_volatility /= float(inverse_volatility.sum())
    expected_sleeve = bounded_simplex_v549(inverse_volatility, LOWER, UPPER)

    sleeve = np.asarray(diagnostics["stability_sleeve"], dtype=float)
    anchored = np.asarray(diagnostics["policy_anchored_weights"], dtype=float)
    pre_guard = np.asarray(diagnostics["pre_guard_weights"], dtype=float)
    stability_weight = float(diagnostics["stability_weight"])

    assert np.allclose(sleeve, expected_sleeve, atol=1.0e-12)
    assert 0.0 <= stability_weight <= 0.75
    assert np.allclose(
        pre_guard,
        (1.0 - stability_weight) * anchored + stability_weight * sleeve,
        atol=1.0e-12,
    )


def test_equity_guard_is_strict_sixty_forty_and_never_funds_commodity():
    history = _history()
    history[-6:, 0] = np.linspace(-0.0075, -0.0025, 6)
    _, diagnostics = _target(history)

    before = np.asarray(diagnostics["pre_guard_weights"], dtype=float)
    after = np.asarray(diagnostics["post_guard_weights"], dtype=float)
    guard = np.asarray(diagnostics["guard_vector"], dtype=float)
    amount = float(diagnostics["equity_guard"])

    assert amount > 0.0
    assert amount <= before[0] - LOWER[0] + 1.0e-12
    assert np.allclose(guard, [-amount, 0.60 * amount, 0.40 * amount, 0.0])
    assert np.allclose(after, before + guard, atol=1.0e-12)
    assert after[0] >= LOWER[0] - 1.0e-12
    assert after[3] == pytest.approx(before[3], abs=1.0e-15)
    assert abs(float(guard.sum())) <= 1.0e-12

    extreme = _history()
    extreme[-6:, 0] = np.asarray([-0.09, -0.08, -0.07, -0.06, -0.05, -0.04])
    _, extreme_diagnostics = _target(extreme)
    assert extreme_diagnostics["pre_guard_weights"][0] < LOWER[0]
    assert extreme_diagnostics["equity_guard"] == pytest.approx(0.0)


def test_robust_covariances_are_causal_12_and_36_month_estimates_and_set_kappa():
    history = _history()
    target, diagnostics = _target(history)
    parameters_12 = dict(diagnostics["robust_covariance_parameters_12m"])
    parameters_36 = dict(diagnostics["robust_covariance_parameters_36m"])
    covariance_12 = np.asarray(diagnostics["robust_covariance_12m"], dtype=float)
    covariance_36 = np.asarray(diagnostics["robust_covariance_36m"], dtype=float)

    assert parameters_12["half_life"] == pytest.approx(6.0)
    assert parameters_36["half_life"] == pytest.approx(18.0)
    expected_12 = robust_covariance(history[-12:], **parameters_12)
    expected_36 = robust_covariance(history[-36:], **parameters_36)
    assert np.allclose(covariance_12, expected_12, rtol=1.0e-12, atol=1.0e-14)
    assert np.allclose(covariance_36, expected_36, rtol=1.0e-12, atol=1.0e-14)
    assert diagnostics["covariance_observations_12m"] == 12
    assert diagnostics["covariance_observations_36m"] == 36

    post_guard = np.asarray(diagnostics["post_guard_weights"], dtype=float)
    active = post_guard - POLICY
    volatility_12 = float(np.sqrt(max(active @ covariance_12 @ active, 0.0)))
    volatility_36 = float(np.sqrt(max(active @ covariance_36 @ active, 0.0)))
    expected_kappa = float(
        np.clip(0.08 / max(volatility_12, volatility_36, 1.0e-12), 0.25, 1.0)
    )
    assert diagnostics["active_volatility_12m"] == pytest.approx(volatility_12)
    assert diagnostics["active_volatility_36m"] == pytest.approx(volatility_36)
    assert diagnostics["kappa"] == pytest.approx(expected_kappa)
    assert 0.25 <= float(diagnostics["kappa"]) <= 1.0
    expected_target = bounded_simplex_v549(
        POLICY + expected_kappa * active, LOWER, UPPER
    )
    assert np.allclose(target, expected_target, atol=1.0e-12)
    assert np.allclose(
        target, np.asarray(diagnostics["signal_target_weights"]), atol=1.0e-12
    )

    # Editing observations outside the trailing 12 months cannot alter the
    # causal 12-month covariance, but must remain visible to the 36-month leg.
    changed = history.copy()
    changed[0] += np.asarray([0.20, -0.08, 0.12, -0.15])
    _, changed_diagnostics = _target(changed)
    assert np.allclose(
        changed_diagnostics["robust_covariance_12m"], covariance_12, atol=1.0e-14
    )
    assert not np.allclose(
        changed_diagnostics["robust_covariance_36m"], covariance_36, atol=1.0e-10
    )


def test_direct_alpha_is_exact_delta_sigma_target_minus_policy():
    covariance = np.asarray(
        [
            [0.040, 0.002, 0.004, 0.009],
            [0.002, 0.006, 0.001, 0.001],
            [0.004, 0.001, 0.025, 0.003],
            [0.009, 0.001, 0.003, 0.050],
        ]
    )
    signal_target = np.asarray([0.52, 0.21, 0.13, 0.14])
    delta = 3.25
    alpha = direct_active_alpha_v549(
        covariance,
        signal_target,
        POLICY,
        active_risk_aversion=delta,
    )
    expected = delta * covariance @ (signal_target - POLICY)

    assert np.asarray(alpha).shape == (4,)
    assert np.allclose(alpha, expected, rtol=1.0e-13, atol=1.0e-15)
    # Direct expected active returns need not be zero-sum; imposing that old
    # v546 restriction would change the optimizer's native objective.
    assert abs(float(np.asarray(alpha).sum())) > 1.0e-8


def test_source_is_cash_free_and_direct_alpha_is_mutually_exclusive_with_bl():
    source = inspect.getsource(direct).lower()

    assert "cash" not in source
    assert "black_litterman" not in source
    assert "view_bundle" not in source
    assert "p_views" not in source

    _, diagnostics = _target(_history())
    assert diagnostics["inference_method"] == "direct_active_alpha"
    assert diagnostics["black_litterman_used"] is False
    assert diagnostics["posterior_uncertainty_penalty"] == pytest.approx(0.0)
    assert diagnostics["asset_order"] == ["equity", "bond", "gold", "commodity"]
