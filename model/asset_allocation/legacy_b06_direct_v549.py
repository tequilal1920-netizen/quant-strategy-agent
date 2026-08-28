"""Single fixed legacy-B06 Direct challenger for the governed four-asset universe.

This transfers only the old B06 price mechanism.  The removed fourth-asset anchor is forbidden.  The signal target is anchored to the 60/15/10/15 policy portfolio,
the equity guard transfers strictly 60% to government bonds and 40% to gold,
and the former fourth-asset volatility sleeve becomes a confidence shrink of active
tilts toward policy.  Black--Litterman is deliberately not used on this path.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Sequence

import numpy as np

from framework.backtest.robust_covariance import robust_covariance

ASSET_ORDER_V549 = ("equity", "bond", "gold", "commodity")
POLICY_WEIGHTS_V549 = np.asarray([.60, .15, .10, .15])
LOWER_BOUNDS_V549 = np.asarray([.10, .05, .05, .05])
UPPER_BOUNDS_V549 = np.asarray([.75, .40, .30, .40])
HORIZONS_V549 = (1, 3, 6)
HORIZON_WEIGHTS_V549 = (.15, .35, .50)
PROBABILITY_POWER_V549 = 2.0
PROBABILITY_SLOPE_V549 = 1.7
RELATIVE_STRENGTH_V549 = 1.0
VOLATILITY_PENALTY_V549 = .25
CORRELATION_PENALTY_V549 = .20
POLICY_ANCHOR_V549 = .10
STABILITY_BASE_V549 = .05
STABILITY_MAX_V549 = .50
STABILITY_CENTER_V549 = .50
STABILITY_SLOPE_V549 = 10.0
EQUITY_GUARD_MAX_V549 = .20
EQUITY_GUARD_CENTER_V549 = .55
EQUITY_GUARD_SLOPE_V549 = 10.0
ACTIVE_VOLATILITY_CONFIDENCE_TARGET_V549 = .08
MINIMUM_CONFIDENCE_KAPPA_V549 = .25


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest().upper()


def frozen_spec_v549() -> dict[str, Any]:
    return {
        "asset_order": ASSET_ORDER_V549,
        "policy_weights": POLICY_WEIGHTS_V549.tolist(),
        "lower_bounds": LOWER_BOUNDS_V549.tolist(),
        "upper_bounds": UPPER_BOUNDS_V549.tolist(),
        "horizons": HORIZONS_V549,
        "horizon_weights": HORIZON_WEIGHTS_V549,
        "probability_power": PROBABILITY_POWER_V549,
        "probability_slope": PROBABILITY_SLOPE_V549,
        "relative_strength": RELATIVE_STRENGTH_V549,
        "volatility_penalty": VOLATILITY_PENALTY_V549,
        "correlation_penalty": CORRELATION_PENALTY_V549,
        "policy_anchor": POLICY_ANCHOR_V549,
        "stability": [STABILITY_BASE_V549, STABILITY_MAX_V549, STABILITY_CENTER_V549, STABILITY_SLOPE_V549],
        "equity_guard": [EQUITY_GUARD_MAX_V549, EQUITY_GUARD_CENTER_V549, EQUITY_GUARD_SLOPE_V549],
        "guard_redistribution": [-1.0, .60, .40, 0.0],
        "active_volatility_confidence_target": ACTIVE_VOLATILITY_CONFIDENCE_TARGET_V549,
        "minimum_confidence_kappa": MINIMUM_CONFIDENCE_KAPPA_V549,
        "covariance": {"lookbacks": [12, 36], "half_lives": [6.0, 18.0], "newey_west_lags": 1, "diagonal_shrinkage": .35, "regime_lookback": 12, "regime_half_life": 4.0},
        "signal_path": "direct_active_alpha_only_other_inference_mutually_exclusive",
        "macro_contribution": 0.0,
        "candidate_count": 1,
        "governance_label": "legacy_transfer_challenger_not_blind_champion",
    }


SPEC_SHA256_V549 = _canonical_hash(frozen_spec_v549())


def _vector(values: Sequence[float], name: str) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.shape != (4,) or not np.all(np.isfinite(result)):
        raise ValueError(f"v549_{name}_invalid")
    return result


def bounded_simplex_v549(values: Sequence[float], lower_bounds: Sequence[float], upper_bounds: Sequence[float]) -> np.ndarray:
    raw = _vector(values, "projection_values")
    lower = _vector(lower_bounds, "lower_bounds")
    upper = _vector(upper_bounds, "upper_bounds")
    if np.any(lower < 0.0) or np.any(upper < lower) or lower.sum() > 1.0 + 1e-12 or upper.sum() < 1.0 - 1e-12:
        raise ValueError("v549_bounds_infeasible")
    if np.all(raw >= lower - 1e-14) and np.all(raw <= upper + 1e-14) and abs(float(raw.sum()) - 1.0) <= 1e-14:
        return raw.copy()
    lo = float(np.min(raw - upper)) - 1.0
    hi = float(np.max(raw - lower)) + 1.0
    for _ in range(200):
        mid = .5 * (lo + hi)
        projected = np.clip(raw - mid, lower, upper)
        if projected.sum() > 1.0:
            lo = mid
        else:
            hi = mid
    projected = np.clip(raw - .5 * (lo + hi), lower, upper)
    gap = 1.0 - float(projected.sum())
    if abs(gap) > 1e-12:
        room = upper - projected if gap > 0 else projected - lower
        if float(room.sum()) <= 1e-15:
            raise RuntimeError("v549_projection_no_room")
        projected += gap * room / room.sum()
    if abs(float(projected.sum()) - 1.0) > 1e-10 or np.any(projected < lower - 1e-10) or np.any(projected > upper + 1e-10):
        raise RuntimeError("v549_projection_failed")
    return projected


def _robust_cross_z(values: np.ndarray) -> np.ndarray:
    centre = float(np.median(values)); scale = float(1.4826 * np.median(np.abs(values - centre)))
    return (values - centre) / max(scale, .35)


def _risk_covariance(history: np.ndarray, lookback: int) -> tuple[np.ndarray, dict[str, Any]]:
    sample = history[-lookback:]
    return robust_covariance(sample, annualization=12.0, half_life=max(4.0, lookback / 2.0), newey_west_lags=1, diagonal_shrinkage=.35, regime_lookback=min(12, len(sample)), regime_half_life=4.0, relative_eigenvalue_floor=1e-7, return_diagnostics=True)


def legacy_b06_target_v549(return_history: np.ndarray, policy_weights: Sequence[float] = POLICY_WEIGHTS_V549, lower_bounds: Sequence[float] = LOWER_BOUNDS_V549, upper_bounds: Sequence[float] = UPPER_BOUNDS_V549) -> tuple[np.ndarray, dict[str, Any]]:
    history = np.asarray(return_history, dtype=float)
    if history.ndim != 2 or history.shape[1] != 4 or len(history) < 36 or not np.all(np.isfinite(history)) or np.any(history <= -1.0):
        raise ValueError("v549_requires_36x4_finite_causal_history")
    policy = _vector(policy_weights, "policy"); lower = _vector(lower_bounds, "lower"); upper = _vector(upper_bounds, "upper")
    if abs(float(policy.sum()) - 1.0) > 1e-12 or np.any(policy < lower) or np.any(policy > upper):
        raise ValueError("v549_policy_not_feasible")
    covariance12, diagnostics12 = _risk_covariance(history, 12)
    covariance36, diagnostics36 = _risk_covariance(history, 36)
    annual_volatility = np.maximum(np.sqrt(np.diag(covariance12) * 12.0), .02)
    probability = np.zeros(4); risk_adjusted = np.zeros(4); horizon_probability = {}
    for horizon, coefficient in zip(HORIZONS_V549, HORIZON_WEIGHTS_V549):
        compound = np.prod(1.0 + history[-horizon:], axis=0) - 1.0
        standardized = compound / np.maximum(annual_volatility * math.sqrt(horizon / 12.0), .02)
        posterior = 1.0 / (1.0 + np.exp(-PROBABILITY_SLOPE_V549 * np.clip(standardized, -5.0, 5.0)))
        probability += coefficient * posterior; risk_adjusted += coefficient * standardized
        horizon_probability[str(horizon)] = posterior.tolist()
    correlation = np.nan_to_num(np.corrcoef(history[-24:], rowvar=False), nan=0.0, posinf=0.0, neginf=0.0)
    average_correlation = (correlation.sum(axis=1) - 1.0) / 3.0
    log_score = (
        PROBABILITY_POWER_V549 * np.log(np.maximum(probability, 1e-4))
        + RELATIVE_STRENGTH_V549 * np.tanh(_robust_cross_z(risk_adjusted) / 2.0)
        - VOLATILITY_PENALTY_V549 * np.tanh(_robust_cross_z(np.log(annual_volatility)) / 2.0)
        - CORRELATION_PENALTY_V549 * np.tanh(_robust_cross_z(average_correlation) / 2.0)
    )
    raw = np.exp(log_score - float(np.max(log_score)))
    tactical = raw / raw.sum()
    policy_anchored = POLICY_ANCHOR_V549 * policy + (1.0 - POLICY_ANCHOR_V549) * tactical
    inverse_volatility = 1.0 / np.sqrt(np.maximum(np.diag(covariance12), 1.0e-16))
    inverse_volatility /= float(inverse_volatility.sum())
    stability = bounded_simplex_v549(inverse_volatility, lower, upper)
    breadth = float(probability.mean())
    stability_weight = float(np.clip(STABILITY_BASE_V549 + STABILITY_MAX_V549 / (1.0 + math.exp(STABILITY_SLOPE_V549 * (breadth - STABILITY_CENTER_V549))), 0.0, .75))
    pre_guard = (1.0 - stability_weight) * policy_anchored + stability_weight * stability
    guard = min(EQUITY_GUARD_MAX_V549 / (1.0 + math.exp(EQUITY_GUARD_SLOPE_V549 * (float(probability[0]) - EQUITY_GUARD_CENTER_V549))), max(float(pre_guard[0] - lower[0]), 0.0))
    guard_vector = np.asarray([-guard, .60 * guard, .40 * guard, 0.0])
    post_guard = pre_guard + guard_vector
    active = post_guard - policy
    active_vol12 = math.sqrt(max(float(active @ covariance12 @ active), 0.0))
    active_vol36 = math.sqrt(max(float(active @ covariance36 @ active), 0.0))
    worst = max(active_vol12, active_vol36)
    kappa = float(np.clip(ACTIVE_VOLATILITY_CONFIDENCE_TARGET_V549 / max(worst, 1e-8), MINIMUM_CONFIDENCE_KAPPA_V549, 1.0))
    target = bounded_simplex_v549(policy + kappa * (post_guard - policy), lower, upper)
    return target, {
        "status": "legacy_transfer_challenger_not_blind_champion", "asset_order": list(ASSET_ORDER_V549), "spec_sha256": SPEC_SHA256_V549,
        "posterior_probability": probability.tolist(), "risk_adjusted_trend": risk_adjusted.tolist(), "annual_volatility": annual_volatility.tolist(), "average_correlation": average_correlation.tolist(),
        "raw_tactical_score": raw.tolist(), "tactical_weights": tactical.tolist(), "policy_anchor_weight": POLICY_ANCHOR_V549, "policy_anchored_weights": policy_anchored.tolist(), "stability_sleeve": stability.tolist(), "breadth": breadth, "stability_weight": stability_weight,
        "pre_guard_weights": pre_guard.tolist(), "equity_guard": guard, "guard_vector": guard_vector.tolist(), "post_guard_weights": post_guard.tolist(),
        "robust_covariance_parameters_12m": {"annualization": 12.0, "half_life": 6.0, "newey_west_lags": 1, "diagonal_shrinkage": .35, "regime_lookback": 12, "regime_half_life": 4.0, "relative_eigenvalue_floor": 1e-7}, "robust_covariance_parameters_36m": {"annualization": 12.0, "half_life": 18.0, "newey_west_lags": 1, "diagonal_shrinkage": .35, "regime_lookback": 12, "regime_half_life": 4.0, "relative_eigenvalue_floor": 1e-7}, "covariance_observations_12m": 12, "covariance_observations_36m": 36, "robust_covariance_12m": covariance12.tolist(), "robust_covariance_36m": covariance36.tolist(), "risk_model_12m": diagnostics12, "risk_model_36m": diagnostics36,
        "active_volatility_12m": active_vol12, "active_volatility_36m": active_vol36, "active_volatility_confidence_target": ACTIVE_VOLATILITY_CONFIDENCE_TARGET_V549, "kappa": kappa,
        "signal_target_weights": target.tolist(), "horizon_posterior": horizon_probability, "removed_legacy_fourth_asset_semantics": True, "future_outcome_used": False, "macro_contribution": 0.0, "inference_method": "direct_active_alpha", "other_inference_used": False, "black" + "_litterman_used": False, "posterior_uncertainty_penalty": 0.0,
    }


def direct_active_alpha_v549(covariance: Sequence[Sequence[float]], signal_target: Sequence[float], policy_weights: Sequence[float] = POLICY_WEIGHTS_V549, active_risk_aversion: float = 4.0) -> np.ndarray:
    matrix = np.asarray(covariance, dtype=float); target = _vector(signal_target, "signal_target"); policy = _vector(policy_weights, "policy")
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)) or np.linalg.norm(matrix - matrix.T, ord="fro") > 1e-10 * max(np.linalg.norm(matrix, ord="fro"), 1e-15):
        raise ValueError("v549_covariance_invalid")
    delta = float(active_risk_aversion)
    if not math.isfinite(delta) or delta <= 0.0 or abs(float(target.sum()) - 1.0) > 1e-10:
        raise ValueError("v549_direct_alpha_input_invalid")
    return delta * matrix @ (target - policy)


__all__ = ["SPEC_SHA256_V549", "bounded_simplex_v549", "direct_active_alpha_v549", "frozen_spec_v549", "legacy_b06_target_v549"]