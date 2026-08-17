"""Strictly chronological supervised ranker for multi-scale K-line experts."""

from __future__ import annotations

from typing import Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from lightgbm import LGBMRanker
except ImportError:  # pragma: no cover
    LGBMRanker = None


def _feature_names(expert_names: Sequence[str]) -> List[str]:
    names = [f"形态_{name}" for name in expert_names]
    names.extend(f"趋势状态_{name}" for name in expert_names)
    names.extend(f"风险状态_{name}" for name in expert_names)
    return names


def _features_at(
    expert_panel: np.ndarray,
    state_features: np.ndarray,
    date_index: int,
    asset_mask: np.ndarray,
) -> np.ndarray:
    base = expert_panel[date_index][:, asset_mask].T.astype(np.float32)
    centered = base - 0.5
    trend_state = float(state_features[date_index, 1] + state_features[date_index, 3])
    risk_state = float(-state_features[date_index, 4])
    return np.column_stack([base, centered * trend_state, centered * risk_state]).astype(np.float32)


def _target_deciles(realized: np.ndarray) -> np.ndarray:
    ranks = pd.Series(realized).rank(pct=True, method="average").to_numpy()
    return np.minimum(9, np.floor(np.clip(ranks, 0.0, 0.999999) * 10.0)).astype(np.int32)


def _fit_model(x: np.ndarray, y: np.ndarray, groups: Sequence[int], seed: int) -> object:
    if LGBMRanker is None:
        raise RuntimeError("lightgbm is required for the supervised K-line ranker")
    model = LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        label_gain=list(range(10)),
        num_leaves=15,
        max_depth=4,
        min_child_samples=800,
        learning_rate=0.03,
        n_estimators=220,
        reg_alpha=4.0,
        reg_lambda=16.0,
        subsample=0.80,
        colsample_bytree=0.80,
        subsample_freq=1,
        random_state=seed,
        n_jobs=4,
        verbosity=-1,
        deterministic=True,
        force_col_wise=True,
    )
    model.fit(x, y, group=list(groups))
    return model


def _dataset(
    expert_panel: np.ndarray,
    state_features: np.ndarray,
    feedback_returns: np.ndarray,
    eligible: np.ndarray,
    date_indices: Sequence[int],
) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    features: List[np.ndarray] = []
    targets: List[np.ndarray] = []
    groups: List[int] = []
    for date_index in date_indices:
        mask = eligible[date_index] & np.isfinite(feedback_returns[date_index])
        mask &= np.all(np.isfinite(expert_panel[date_index]), axis=0)
        if int(mask.sum()) < 100:
            continue
        features.append(_features_at(expert_panel, state_features, date_index, mask))
        targets.append(_target_deciles(feedback_returns[date_index, mask]))
        groups.append(int(mask.sum()))
    if not groups:
        return np.empty((0, expert_panel.shape[1] * 3), dtype=np.float32), np.empty(0, dtype=np.int32), []
    return np.vstack(features), np.concatenate(targets), groups


def _predict_dates(
    model: object,
    output: np.ndarray,
    expert_panel: np.ndarray,
    state_features: np.ndarray,
    eligible: np.ndarray,
    date_indices: Sequence[int],
) -> None:
    for date_index in date_indices:
        mask = eligible[date_index] & np.all(np.isfinite(expert_panel[date_index]), axis=0)
        if int(mask.sum()) < 30:
            continue
        prediction = model.predict(_features_at(expert_panel, state_features, date_index, mask))
        output[date_index, mask] = pd.Series(prediction).rank(pct=True).to_numpy(dtype=np.float32)


def train_chronological_kline_ranker(
    experts: Mapping[str, np.ndarray],
    state_features: np.ndarray,
    feedback_returns: np.ndarray,
    eligible: np.ndarray,
    split_labels: Sequence[str],
    seed: int = 20260802,
) -> Tuple[np.ndarray, Dict[str, object]]:
    """Create expanding-window OOF train scores and a train-only final model."""

    names = list(experts)
    panel = np.stack([np.asarray(experts[name], dtype=np.float32) for name in names], axis=1)
    periods, _, assets = panel.shape
    output = np.full((periods, assets), np.nan, dtype=np.float32)
    train_dates = [index for index, split in enumerate(split_labels) if split == "train"]
    report_dates = [index for index, split in enumerate(split_labels) if split in {"valid", "test"}]
    if len(train_dates) < 52:
        raise RuntimeError("supervised K-line ranker requires at least 52 training weeks")

    warmup = max(39, int(round(len(train_dates) * 0.40)))
    fold_edges = sorted(set([
        warmup,
        int(round(len(train_dates) * 0.60)),
        int(round(len(train_dates) * 0.80)),
        len(train_dates),
    ]))
    folds: List[Dict[str, object]] = []
    start = warmup
    for fold_number, end in enumerate(fold_edges[1:], start=1):
        fit_dates = train_dates[:start]
        predict_dates = train_dates[start:end]
        x, y, groups = _dataset(panel, state_features, feedback_returns, eligible, fit_dates)
        if len(groups) < 20 or not predict_dates:
            start = end
            continue
        model = _fit_model(x, y, groups, seed + fold_number)
        _predict_dates(model, output, panel, state_features, eligible, predict_dates)
        folds.append({
            "fold": fold_number,
            "fit_weeks": len(groups),
            "fit_samples": int(len(y)),
            "prediction_start_index": int(predict_dates[0]),
            "prediction_end_index": int(predict_dates[-1]),
        })
        start = end

    x_full, y_full, groups_full = _dataset(
        panel, state_features, feedback_returns, eligible, train_dates
    )
    final_model = _fit_model(x_full, y_full, groups_full, seed + 100)
    _predict_dates(final_model, output, panel, state_features, eligible, report_dates)
    importances = sorted(
        zip(_feature_names(names), final_model.feature_importances_.tolist()),
        key=lambda item: item[1],
        reverse=True,
    )
    diagnostics = {
        "algorithm": "LightGBM LambdaRank",
        "objective": "weekly_forward_return_decile",
        "feature_names": _feature_names(names),
        "folds": folds,
        "train_weeks": len(groups_full),
        "train_samples": int(len(y_full)),
        "feature_importance": [
            {"feature": name, "split_count": int(value)} for name, value in importances
        ],
        "train_scores_are_expanding_window_oof": True,
        "validation_labels_used_for_fit": False,
        "test_labels_used_for_fit": False,
        "test_labels_used_for_selection": False,
    }
    return output, diagnostics


def _market_design(state_features: np.ndarray) -> np.ndarray:
    state = np.asarray(state_features, dtype=np.float64)
    return np.column_stack([
        state,
        state ** 2,
        state[:, 0] * state[:, 1],
        state[:, 2] * state[:, 3],
        state[:, 1] * state[:, 4],
    ])


def _ridge_prediction(
    design: np.ndarray,
    target: np.ndarray,
    fit_indices: Sequence[int],
    predict_indices: Sequence[int],
    ridge: float,
) -> np.ndarray:
    output = np.full(len(predict_indices), np.nan, dtype=np.float64)
    fit_indices = np.asarray(fit_indices, dtype=np.int32)
    fit_indices = fit_indices[np.isfinite(target[fit_indices])]
    if len(fit_indices) < 20:
        return output
    x = design[fit_indices]
    y = target[fit_indices]
    mean = np.mean(x, axis=0)
    scale = np.std(x, axis=0, ddof=1)
    scale[~np.isfinite(scale) | (scale < 1e-8)] = 1.0
    standardized = (x - mean) / scale
    matrix = np.column_stack([np.ones(len(standardized)), standardized])
    penalty = np.eye(matrix.shape[1]) * ridge
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(matrix.T @ matrix + penalty, matrix.T @ y)
    predict = (design[np.asarray(predict_indices)] - mean) / scale
    return np.column_stack([np.ones(len(predict)), predict]) @ beta


def train_chronological_market_exposure(
    state_features: np.ndarray,
    market_returns: np.ndarray,
    market_volatility: np.ndarray,
    split_labels: Sequence[str],
    minimum_history: int = 39,
    ridge: float = 12.0,
    volatility_target: float = 0.14,
) -> Tuple[np.ndarray, Dict[str, object]]:
    """Fit an OOF train-only market model and map forecasts to long/cash exposure."""

    design = _market_design(state_features)
    target = np.asarray(market_returns, dtype=np.float64)
    volatility = np.asarray(market_volatility, dtype=np.float64)
    prediction = np.full(len(target), np.nan, dtype=np.float64)
    train_dates = [index for index, split in enumerate(split_labels) if split == "train"]
    for position, date_index in enumerate(train_dates):
        history = train_dates[:position]
        if len(history) < minimum_history:
            continue
        prediction[date_index] = _ridge_prediction(design, target, history, [date_index], ridge)[0]
    report_dates = [index for index, split in enumerate(split_labels) if split in {"valid", "test"}]
    prediction[report_dates] = _ridge_prediction(design, target, train_dates, report_dates, ridge)
    weekly_volatility = np.maximum(volatility / np.sqrt(52.0), 0.015)
    strength = np.nan_to_num(prediction / weekly_volatility, nan=0.0)
    directional = 0.5 + 0.5 * np.tanh(np.clip(strength, -4.0, 4.0))
    volatility_budget = np.minimum(1.0, volatility_target / np.maximum(volatility, 0.06))
    exposure = np.clip(directional * volatility_budget, 0.0, 1.0).astype(np.float32)
    diagnostics = {
        "algorithm": "expanding_ridge_state_model",
        "features": int(design.shape[1]),
        "minimum_history": int(minimum_history),
        "ridge": float(ridge),
        "volatility_target": float(volatility_target),
        "train_predictions_are_expanding_window_oof": True,
        "validation_labels_used_for_fit": False,
        "test_labels_used_for_fit": False,
        "prediction": [None if not np.isfinite(value) else round(float(value), 8) for value in prediction],
        "exposure": [round(float(value), 6) for value in exposure],
    }
    return exposure, diagnostics
