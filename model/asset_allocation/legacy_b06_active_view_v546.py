"""Fixed legacy-B06 mechanism transfer for the new four-asset universe.

This is a single preregistered challenger, not a new blind champion.  It keeps
the old champion's 1/3/6-month logistic posterior, stability sleeve, equity
guard, cross-sectional volatility/correlation penalties and fixed B06
coefficients.  The old fourth-asset cash meaning is deliberately removed:
all four assets are equity, government bonds, RMB gold and ex-PM commodities.

The output is a zero-sum active alpha around 60/15/10/15 and is solved only by
the native constrained active optimizer.  There is no post-solve scaling.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from asset_allocation_v536_stack import average_rank_score_v536
from cycle_views_v5 import P_VIEWS_V5, ViewBundleV5


ASSET_ORDER_V546 = ("equity", "bond", "gold", "commodity")
HORIZONS_V546 = (1, 3, 6)
HORIZON_WEIGHTS_V546 = (0.15, 0.35, 0.50)
PROBABILITY_POWER_V546 = 2.0
PROBABILITY_SLOPE_V546 = 1.70
RELATIVE_STRENGTH_V546 = 1.0
VOLATILITY_PENALTY_V546 = 0.25
CORRELATION_PENALTY_V546 = 0.20
STABILITY_BASE_V546 = 0.05
STABILITY_MAX_V546 = 0.50
STABILITY_CENTER_V546 = 0.50
STABILITY_SLOPE_V546 = 10.0
EQUITY_GUARD_MAX_V546 = 0.20
EQUITY_GUARD_CENTER_V546 = 0.55
EQUITY_GUARD_SLOPE_V546 = 10.0
ACTIVE_SCALE_MONTHLY_V546 = 0.0025


def _robust_cross_z(values: np.ndarray, floor: float = 0.35) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    centre = float(np.median(data))
    scale = float(np.median(np.abs(data - centre)) * 1.4826)
    return (data - centre) / max(scale, floor)


def legacy_b06_active_alpha_v546(return_history: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    history = np.asarray(return_history, dtype=float)
    if history.ndim != 2 or history.shape[1] != 4 or len(history) < 12 or not np.all(np.isfinite(history)):
        raise ValueError("v546_legacy_view_requires_12x4_finite_history")
    recent = history[-12:]
    volatility = np.maximum(np.std(recent, axis=0, ddof=1) * np.sqrt(12.0), 0.02)
    probability = np.zeros(4)
    risk_adjusted = np.zeros(4)
    horizon_detail = {}
    for horizon, coefficient in zip(HORIZONS_V546, HORIZON_WEIGHTS_V546):
        compound = np.prod(1.0 + history[-horizon:], axis=0) - 1.0
        standardized = compound / np.maximum(volatility * np.sqrt(horizon / 12.0), 0.02)
        posterior = 1.0 / (1.0 + np.exp(-PROBABILITY_SLOPE_V546 * np.clip(standardized, -5.0, 5.0)))
        probability += coefficient * posterior
        risk_adjusted += coefficient * standardized
        horizon_detail[str(horizon)] = posterior.tolist()
    relative = _robust_cross_z(risk_adjusted)
    log_volatility = _robust_cross_z(np.log(np.maximum(volatility, 1.0e-6)))
    correlation = np.nan_to_num(np.corrcoef(history[-min(24, len(history)) :], rowvar=False), nan=0.0)
    correlation_penalty = _robust_cross_z((correlation.sum(axis=1) - 1.0) / 3.0)
    tactical_score = np.maximum(probability, 1.0e-4) ** PROBABILITY_POWER_V546 * np.exp(
        RELATIVE_STRENGTH_V546 * np.tanh(relative / 2.0)
        - VOLATILITY_PENALTY_V546 * np.tanh(log_volatility / 2.0)
        - CORRELATION_PENALTY_V546 * np.tanh(correlation_penalty / 2.0)
    )
    tactical_rank = average_rank_score_v536(np.log(np.maximum(tactical_score, 1.0e-12)))
    inverse_volatility_rank = average_rank_score_v536(1.0 / volatility)
    breadth = float(np.mean(probability))
    stability_weight = STABILITY_BASE_V546 + STABILITY_MAX_V546 / (
        1.0 + math.exp(STABILITY_SLOPE_V546 * (breadth - STABILITY_CENTER_V546))
    )
    stability_weight = float(np.clip(stability_weight, 0.0, 0.75))
    combined_rank = (1.0 - stability_weight) * tactical_rank + stability_weight * inverse_volatility_rank
    equity_guard = EQUITY_GUARD_MAX_V546 / (
        1.0 + math.exp(EQUITY_GUARD_SLOPE_V546 * (float(probability[0]) - EQUITY_GUARD_CENTER_V546))
    )
    # No cash sleeve exists.  The equity guard becomes a zero-sum penalty to
    # equity, redistributed to bond/gold/commodity using their non-negative
    # defensive posterior gap and inverse-volatility evidence.
    defensive = np.maximum(0.55 - probability[1:], 0.0) + 0.25 / volatility[1:]
    defensive /= float(defensive.sum())
    guard_vector = np.r_[-equity_guard, equity_guard * defensive]
    combined_rank += guard_vector
    combined_rank -= float(combined_rank.mean())
    maximum = float(np.max(np.abs(combined_rank)))
    if maximum > 1.0:
        combined_rank /= maximum
    alpha = ACTIVE_SCALE_MONTHLY_V546 * combined_rank
    if abs(float(alpha.sum())) > 1.0e-12:
        raise AssertionError("v546_active_alpha_not_zero_sum")
    return alpha, {
        "status": "legacy_B06_fixed_mechanism_transfer_challenger",
        "selection_status": "not_a_new_blind_champion",
        "asset_order": list(ASSET_ORDER_V546),
        "posterior_probability": probability.tolist(),
        "risk_adjusted_trend": risk_adjusted.tolist(),
        "annual_volatility": volatility.tolist(),
        "cross_asset_correlation_penalty": correlation_penalty.tolist(),
        "tactical_rank": tactical_rank.tolist(),
        "inverse_volatility_rank": inverse_volatility_rank.tolist(),
        "breadth": breadth,
        "stability_weight": stability_weight,
        "equity_guard": equity_guard,
        "active_alpha": alpha.tolist(),
        "horizon_posterior": horizon_detail,
        "cash_semantics_removed": True,
        "test_or_validation_used": False,
    }


def legacy_b06_view_bundle_v546(
    covariance: np.ndarray,
    prior: np.ndarray,
    return_history: np.ndarray,
    *,
    tau: float,
) -> ViewBundleV5:
    alpha, diagnostics = legacy_b06_active_alpha_v546(return_history)
    covariance = np.asarray(covariance, dtype=float)
    prior = np.asarray(prior, dtype=float)
    q = P_VIEWS_V5 @ prior + P_VIEWS_V5 @ alpha
    raw = P_VIEWS_V5 @ (float(tau) * covariance) @ P_VIEWS_V5.T
    diagonal = np.diag(np.maximum(np.diag(raw), 1.0e-8))
    omega = raw + diagonal
    return ViewBundleV5(
        P=P_VIEWS_V5.copy(),
        q=q,
        omega=omega,
        cycle_contributions={"legacy_B06_active_alpha": P_VIEWS_V5 @ alpha},
        forecast_error_covariance=omega.copy(),
        diagnostics={
            **diagnostics,
            "omega_policy": "tau_P_sigma_PT_plus_diagonal_model_risk_floor",
        },
    )


__all__ = [
    "legacy_b06_active_alpha_v546",
    "legacy_b06_view_bundle_v546",
]
