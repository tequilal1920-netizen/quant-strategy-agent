"""Causal symmetric-orthogonal factor timing with empirical-Bayes ICIR shrinkage."""

from __future__ import annotations

from typing import Any

import numpy as np


def _rankdata(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    unique, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    del unique
    if np.any(counts > 1):
        sums = np.bincount(inverse, weights=ranks)
        ranks = sums[inverse] / counts[inverse]
    return ranks


def _safe_corr(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    mask = np.isfinite(left) & np.isfinite(right)
    if int(mask.sum()) < 3:
        return 0.0
    left = left[mask]
    right = right[mask]
    left = left - float(left.mean())
    right = right - float(right.mean())
    denominator = float(np.sqrt(np.dot(left, left) * np.dot(right, right)))
    return float(np.dot(left, right) / denominator) if denominator > 1e-12 else 0.0


def symmetric_orthogonalize(
    values: np.ndarray,
    active_mask: np.ndarray,
) -> np.ndarray:
    """Löwdin symmetric orthogonalization for one cross-section."""

    values = np.asarray(values, dtype=float)
    active_mask = np.asarray(active_mask, dtype=bool)
    output = np.full_like(values, np.nan, dtype=float)
    if values.ndim != 2 or int(active_mask.sum()) < max(12, values.shape[1] + 2):
        return output
    matrix = np.nan_to_num(values[active_mask], nan=0.0, posinf=0.0, neginf=0.0)
    matrix -= np.mean(matrix, axis=0, keepdims=True)
    scale = np.std(matrix, axis=0, ddof=0)
    usable = scale > 1e-10
    if not np.any(usable):
        return output
    standardized = np.zeros_like(matrix)
    standardized[:, usable] = matrix[:, usable] / scale[usable]
    gram = standardized.T @ standardized / max(len(standardized), 1)
    eigenvalues, eigenvectors = np.linalg.eigh((gram + gram.T) * 0.5)
    floor = max(float(np.max(eigenvalues)) * 1e-6, 1e-8)
    inverse_sqrt = eigenvectors @ np.diag(
        1.0 / np.sqrt(np.maximum(eigenvalues, floor))
    ) @ eigenvectors.T
    orthogonal = standardized @ inverse_sqrt
    orthogonal_scale = np.std(orthogonal, axis=0, ddof=0)
    stable = orthogonal_scale > 1e-10
    orthogonal[:, stable] /= orthogonal_scale[stable]
    orthogonal[:, ~stable] = 0.0
    output[active_mask] = orthogonal
    return output


def causal_rolling_icir_scores(
    ranked_features: np.ndarray,
    forward_targets: np.ndarray,
    valid: np.ndarray,
    horizon: int,
    *,
    lookback_periods: int,
    min_periods: int = 12,
    prior_strength: float = 6.0,
    prior_scale: float = 0.05,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build scores using only factor IC observations matured by each signal date."""

    ranked_features = np.asarray(ranked_features, dtype=float)
    forward_targets = np.asarray(forward_targets, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    if ranked_features.ndim != 3:
        raise ValueError("ranked_features_must_be_date_asset_feature")
    if ranked_features.shape[:2] != forward_targets.shape or valid.shape != forward_targets.shape:
        raise ValueError("adaptive_icir_shape_mismatch")
    horizon = max(1, int(horizon))
    lookback_periods = max(int(min_periods), int(lookback_periods))
    date_count, asset_count, feature_count = ranked_features.shape
    scores = np.full((date_count, asset_count), np.nan, dtype=np.float32)
    pending: dict[int, np.ndarray] = {}
    ic_history: list[np.ndarray] = []
    last_weights = np.zeros(feature_count, dtype=float)
    active_dates = 0
    active_factor_counts: list[int] = []
    first_active_index: int | None = None
    last_sample_count = 0

    for date_index in range(date_count):
        current = symmetric_orthogonalize(
            ranked_features[date_index],
            valid[date_index],
        )
        matured_index = date_index - horizon - 1
        matured = pending.pop(matured_index, None)
        if matured is not None:
            target_mask = valid[matured_index] & np.isfinite(
                forward_targets[matured_index]
            )
            if int(target_mask.sum()) >= 30:
                target_rank = _rankdata(forward_targets[matured_index, target_mask])
                ic_history.append(np.asarray([
                    _safe_corr(
                        _rankdata(matured[target_mask, feature_index]),
                        target_rank,
                    )
                    for feature_index in range(feature_count)
                ], dtype=float))
        pending[date_index] = current

        sampled = np.asarray(
            ic_history[::-horizon][:lookback_periods],
            dtype=float,
        )
        last_sample_count = len(sampled)
        if len(sampled) < min_periods:
            continue
        sample_mean = np.mean(sampled, axis=0)
        sample_variance = (
            np.var(sampled, axis=0, ddof=1)
            if len(sampled) > 1
            else np.zeros(feature_count)
        )
        posterior_mean = sample_mean * len(sampled) / (
            len(sampled) + prior_strength
        )
        posterior_standard_error = np.sqrt(
            sample_variance / max(len(sampled), 1)
            + prior_scale * prior_scale / (len(sampled) + prior_strength)
        )
        evidence = np.clip(
            posterior_mean / np.maximum(posterior_standard_error, 1e-8),
            -3.0,
            3.0,
        )
        weight_norm = float(np.sum(np.abs(evidence)))
        if weight_norm <= 1e-12:
            continue
        weights = evidence / weight_norm
        active_mask = valid[date_index] & np.all(np.isfinite(current), axis=1)
        if int(active_mask.sum()) < 30:
            continue
        scores[date_index, active_mask] = (
            current[active_mask] @ weights
        ).astype(np.float32)
        last_weights = weights
        active_dates += 1
        active_factor_counts.append(int(np.sum(np.abs(weights) >= 0.02)))
        if first_active_index is None:
            first_active_index = date_index

    return scores, {
        "method": (
            "lowdin_symmetric_orthogonalization_then_lagged_nonoverlapping_"
            "empirical_bayes_icir_weights"
        ),
        "test_usage": "never_used_for_weight_calibration_or_candidate_selection",
        "horizon": horizon,
        "lookback_periods": lookback_periods,
        "min_periods": min_periods,
        "prior_strength": prior_strength,
        "prior_scale": prior_scale,
        "active_dates": active_dates,
        "first_active_index": first_active_index,
        "last_history_sample_count": last_sample_count,
        "mean_active_factor_count": (
            float(np.mean(active_factor_counts)) if active_factor_counts else 0.0
        ),
        "last_weights": last_weights.tolist(),
    }
