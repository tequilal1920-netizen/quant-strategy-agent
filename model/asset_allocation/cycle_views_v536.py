"""Causal and fail-closed cycle-view model for asset allocation v5.3.6.

The legacy view model intentionally remains untouched as historical evidence.
This module fixes four release-blocking issues in a new versioned path:

* labels are target-month aligned (cycle state at ``t`` predicts return at
  ``t+1``), and every expanding fit receives only labels observable at the
  signal month;
* the regression target and forecast are both *absolute relative returns*, so
  the Black--Litterman prior is not added twice;
* insufficient samples emit no cycle view instead of a zero-valued strong
  opinion;
* all covariance estimates use eligible labels only.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from cycle_views_v5 import P_VIEWS_V5, cycle_probability_features_v5


def _valid_month(value: Any) -> str:
    month = str(value)
    if len(month) != 6 or not month.isdigit() or not 1 <= int(month[4:]) <= 12:
        raise ValueError("v536_invalid_yyyymm")
    return month


def _fit_ridge(x: np.ndarray, y: np.ndarray, penalty: float) -> tuple[np.ndarray, np.ndarray]:
    intercept = np.mean(y, axis=0)
    coefficient = np.linalg.solve(
        x.T @ x + float(penalty) * np.eye(x.shape[1]),
        x.T @ (y - intercept),
    )
    return intercept, coefficient


def fit_cycle_views_expanding_v536(
    asset_returns: np.ndarray,
    cycle_history: Sequence[Mapping[str, Any]],
    return_months: Sequence[str],
    *,
    signal_index: int,
    production_cycles: Sequence[str],
    minimum_train: int = 24,
    ridge_grid: Sequence[float] = (0.1, 1.0, 10.0),
) -> dict[str, Any]:
    """Fit only labels whose target month is no later than the signal month."""

    returns = np.asarray(asset_returns, dtype=float)
    months = tuple(_valid_month(value) for value in return_months)
    if returns.ndim != 2 or returns.shape[1] != 4:
        raise ValueError("v536_cycle_views_require_four_assets")
    if len(returns) != len(months) or len(returns) != len(cycle_history):
        raise ValueError("v536_cycle_view_inputs_misaligned")
    if tuple(sorted(months)) != months or len(set(months)) != len(months):
        raise ValueError("v536_cycle_view_months_not_unique_sorted")
    if not 1 <= int(signal_index) < len(months):
        raise ValueError("v536_signal_index_out_of_range")
    if any(str(row.get("month")) != month for row, month in zip(cycle_history, months)):
        raise ValueError("v536_cycle_history_month_alignment_failed")

    allowed = set(map(str, production_cycles))
    unknown = allowed.difference({"pring", "kitchin", "juglar", "merrill"})
    if unknown:
        raise ValueError("v536_unknown_production_cycle")
    labels, raw_features, slices = cycle_probability_features_v5(cycle_history)
    features = raw_features[:-1].copy()
    for cycle, section in slices.items():
        if cycle not in allowed:
            features[:, section] = 0.0
    labels_target = returns[1:] @ P_VIEWS_V5.T
    eligible = np.arange(signal_index, dtype=int)
    if len(allowed) == 0 or len(eligible) < max(12, int(minimum_train)):
        return {
            "status": "inactive_no_D3_cycle_or_insufficient_history",
            "P": P_VIEWS_V5.copy(),
            "feature_names": labels,
            "feature_slices": slices,
            "feature_mean": np.zeros(features.shape[1]),
            "intercept": np.zeros(3),
            "coefficient": np.zeros((features.shape[1], 3)),
            "omega": np.eye(3) * 1.0e6,
            "selected_ridge": None,
            "oof_observations": 0,
            "production_cycles": sorted(allowed),
            "last_admitted_target_month": months[signal_index],
            "emits_view": False,
            "selection_uses_test": False,
        }

    train_x = features[eligible]
    train_y = labels_target[eligible]
    residuals_by_penalty: dict[float, list[np.ndarray]] = {}
    scores: dict[float, float] = {}
    for penalty_raw in ridge_grid:
        penalty = float(penalty_raw)
        residuals: list[np.ndarray] = []
        for end in range(int(minimum_train), len(train_x)):
            prefix = train_x[:end]
            centre = np.mean(prefix, axis=0)
            intercept, coefficient = _fit_ridge(prefix - centre, train_y[:end], penalty)
            residuals.append(train_y[end] - (intercept + (train_x[end] - centre) @ coefficient))
        residuals_by_penalty[penalty] = residuals
        scores[penalty] = (
            float(np.mean(np.asarray(residuals) ** 2)) if residuals else float("inf")
        )
    selected = min(scores, key=scores.get)
    feature_mean = np.mean(train_x, axis=0)
    intercept, coefficient = _fit_ridge(train_x - feature_mean, train_y, selected)
    errors = residuals_by_penalty[selected]
    if len(errors) < 6:
        errors = list(train_y - (intercept + (train_x - feature_mean) @ coefficient))
    error_matrix = np.asarray(errors, dtype=float)
    omega = (
        np.cov(error_matrix.T, ddof=1)
        if len(error_matrix) > 1
        else np.eye(3) * 1.0e-4
    )
    omega = np.atleast_2d(omega)
    diagonal = np.diag(np.maximum(np.diag(omega), 1.0e-8))
    omega = 0.75 * omega + 0.25 * diagonal
    eigenvalues, eigenvectors = np.linalg.eigh((omega + omega.T) / 2.0)
    omega = eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0e-8)) @ eigenvectors.T
    return {
        "status": "ok",
        "P": P_VIEWS_V5.copy(),
        "feature_names": labels,
        "feature_slices": slices,
        "feature_mean": feature_mean,
        "intercept": intercept,
        "coefficient": coefficient,
        "omega": omega,
        "selected_ridge": selected,
        "ridge_scores": scores,
        "oof_observations": len(error_matrix),
        "production_cycles": sorted(allowed),
        "last_admitted_target_month": months[signal_index],
        "emits_view": True,
        "selection_uses_test": False,
        "label_semantics": "absolute_relative_return_P_times_r_t_plus_1",
    }


def forecast_cycle_views_v536(
    fitted: Mapping[str, Any], current_cycle: Mapping[str, Any]
) -> dict[str, Any]:
    """Forecast absolute relative returns; never add ``P @ pi`` here."""

    if not bool(fitted.get("emits_view")):
        return {
            "status": str(fitted.get("status")),
            "emits_view": False,
            "P": np.zeros((0, 4)),
            "q": np.zeros(0),
            "omega": np.zeros((0, 0)),
            "cycle_contributions": {},
            "selection_uses_test": False,
        }
    _, raw, slices = cycle_probability_features_v5([current_cycle])
    centred = raw[0] - np.asarray(fitted["feature_mean"], dtype=float)
    coefficient = np.asarray(fitted["coefficient"], dtype=float)
    intercept = np.asarray(fitted["intercept"], dtype=float)
    allowed = set(map(str, fitted.get("production_cycles") or ()))
    contribution: dict[str, np.ndarray] = {"intercept": intercept.copy()}
    q = intercept.copy()
    for cycle, section in slices.items():
        value = centred[section] @ coefficient[section]
        if cycle not in allowed:
            value = np.zeros(3)
        contribution[cycle] = np.asarray(value, dtype=float)
        q += value
    if not np.allclose(q, sum(contribution.values()), atol=1.0e-12):
        raise RuntimeError("v536_cycle_view_attribution_not_conserved")
    return {
        "status": "ok",
        "emits_view": True,
        "P": P_VIEWS_V5.copy(),
        "q": q,
        "omega": np.asarray(fitted["omega"], dtype=float),
        "cycle_contributions": contribution,
        "selection_uses_test": False,
        "q_semantics": "absolute_relative_return_not_prior_plus_alpha",
    }


__all__ = ["fit_cycle_views_expanding_v536", "forecast_cycle_views_v536"]
