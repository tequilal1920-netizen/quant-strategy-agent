"""Stable-development-fold launcher for Factor Laboratory v2.

This launcher upgrades LSTM candidate direction and blend selection from a
single validation score to a worst-fold train/validation objective.  It also
adds transparent economic factor anchors to the candidate set.  Test data is
still report-only.
"""
from __future__ import annotations

import itertools
from typing import Any

import numpy as np

import validated_ensemble as v2


VERSION = "factor-lab/2.1-stable-development-folds"


def stable_objective(train_metric: dict[str, Any], valid_metric: dict[str, Any]) -> float:
    train_score = v2._objective(train_metric)
    valid_score = v2._objective(valid_metric)
    decay = abs(v2.engine.finite(train_metric.get("rank_ic")) - v2.engine.finite(valid_metric.get("rank_ic")))
    return min(train_score, valid_score) + .35 * valid_score - 1.5 * decay


def orient(train_score, valid_score, test_score, train_frame, valid_frame, cost_bps: float, horizon: int):
    positive_train = v2._evaluate(train_frame, train_score, cost_bps, horizon)
    positive_valid = v2._evaluate(valid_frame, valid_score, cost_bps, horizon)
    negative_train = v2._evaluate(train_frame, -np.asarray(train_score), cost_bps, horizon)
    negative_valid = v2._evaluate(valid_frame, -np.asarray(valid_score), cost_bps, horizon)
    sign = 1.0 if stable_objective(positive_train, positive_valid) >= stable_objective(negative_train, negative_valid) else -1.0
    return sign * np.asarray(train_score), sign * np.asarray(valid_score), sign * np.asarray(test_score), sign


def fit_stable_ensemble(panel, config: dict[str, Any], neural_frames=None):
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    from sklearn.linear_model import ElasticNet, Ridge

    frames, feature_names = {}, []
    for split_name in ("train", "valid", "test"):
        frames[split_name], feature_names = v2._feature_frame(panel, split_name)
    x_train = frames["train"][feature_names].to_numpy(np.float32)
    y_train = frames["train"].target.to_numpy(float)
    max_samples = int(config.get("max_training_samples", 260000))
    if len(x_train) > max_samples:
        sample_index = np.linspace(0, len(x_train) - 1, max_samples).astype(int)
        x_fit, y_fit = x_train[sample_index], y_train[sample_index]
    else:
        x_fit, y_fit = x_train, y_train
    sample_weight = np.linspace(.55, 1.0, len(y_fit), dtype=np.float32)
    seed = int(config.get("seed", 20260720))
    models = {
        "Ridge": Ridge(alpha=12.0),
        "ElasticNet": ElasticNet(alpha=2e-4, l1_ratio=.18, max_iter=6000, random_state=seed),
        "ExtraTrees": ExtraTreesRegressor(
            n_estimators=160 if config.get("mode") == "smoke" else 360,
            max_depth=8, min_samples_leaf=max(18, len(y_fit) // 5000), max_features=.72,
            n_jobs=max(1, min(8, int(config.get("cpu_threads", 4)))), random_state=seed,
        ),
        "GradientBoosting": HistGradientBoostingRegressor(
            learning_rate=.045, max_iter=100 if config.get("mode") == "smoke" else 240,
            max_leaf_nodes=23, min_samples_leaf=max(24, len(y_fit) // 6000),
            l2_regularization=.15, early_stopping=True, validation_fraction=.16, random_state=seed,
        ),
    }
    predictions: dict[str, dict[str, np.ndarray]] = {}
    for name, model in models.items():
        model.fit(x_fit, y_fit, sample_weight=sample_weight)
        predictions[name] = {
            split_name: model.predict(frame[feature_names].to_numpy(np.float32))
            for split_name, frame in frames.items()
        }
    factor_candidates = {
        "Momentum": "f_momentum_residual", "LongMomentum": "f_long_momentum",
        "ShortReversal": "f_short_reversal", "Value": "f_value", "LowRisk": "f_low_risk",
        "Liquidity": "f_liquidity", "Flow": "f_flow", "BreakoutFlow": "f_breakout_flow",
        "PriceVolume": "f_price_volume", "ValueMomentum": "f_value_momentum",
        "RiskAdjustedMomentum": "f_risk_adjusted_momentum", "FlowReversal": "f_flow_reversal",
        "Crowding": "f_crowding",
    }
    for label, column in factor_candidates.items():
        predictions[label] = {split_name: frame[column].to_numpy(float) for split_name, frame in frames.items()}
    if neural_frames:
        merged_scores: dict[str, np.ndarray] = {}
        available = True
        for split_name, frame in frames.items():
            neural = neural_frames.get(split_name)
            if neural is None or neural.empty:
                available = False
                break
            merged = frame[["trade_date", "ts_code"]].merge(
                neural[["trade_date", "ts_code", "score"]], on=["trade_date", "ts_code"], how="left",
            )
            merged_scores[split_name] = merged.score.fillna(0.0).to_numpy(float)
        if available:
            predictions["LSTM"] = merged_scores

    cost = float(config.get("cost_bps", 15))
    horizon = panel.horizons[0]
    oriented, ledger = {}, []
    for name, values in predictions.items():
        tr, va, te, sign = orient(
            values["train"], values["valid"], values["test"],
            frames["train"], frames["valid"], cost, horizon,
        )
        oriented[name] = {"train": tr, "valid": va, "test": te}
        train_metric = v2._evaluate(frames["train"], tr, cost, horizon)
        valid_metric = v2._evaluate(frames["valid"], va, cost, horizon)
        ledger.append({
            "name": name, "direction": int(sign),
            "train": {k: value for k, value in train_metric.items() if k != "series"},
            "valid": {k: value for k, value in valid_metric.items() if k != "series"},
            "selection_score": stable_objective(train_metric, valid_metric),
        })
    ranked = sorted(ledger, key=lambda x: x["selection_score"], reverse=True)
    pool = [x["name"] for x in ranked[: min(6, len(ranked))]]
    candidate_specs = [(name, {name: 1.0}) for name in pool]
    for a, b in itertools.combinations(pool, 2):
        for weight in (.25, .5, .75):
            candidate_specs.append((f"{a}+{b}", {a: weight, b: 1 - weight}))
    if len(pool) >= 3:
        for combo in itertools.combinations(pool, 3):
            candidate_specs.append(("+".join(combo), {name: 1 / 3 for name in combo}))
    selections = []
    for label, weights in candidate_specs:
        train_score = sum(oriented[name]["train"] * weight for name, weight in weights.items())
        valid_score = sum(oriented[name]["valid"] * weight for name, weight in weights.items())
        train_metric = v2._evaluate(frames["train"], train_score, cost, horizon)
        valid_metric = v2._evaluate(frames["valid"], valid_score, cost, horizon)
        selections.append({
            "label": label, "weights": weights,
            "score": stable_objective(train_metric, valid_metric),
            "train": train_metric, "valid": valid_metric,
        })
    selections.sort(key=lambda x: x["score"], reverse=True)
    chosen = selections[0]
    metrics, chosen_scores = {}, {}
    for split_name in ("train", "valid", "test"):
        score = sum(oriented[name][split_name] * weight for name, weight in chosen["weights"].items())
        chosen_scores[split_name] = score
        metrics[split_name] = v2._evaluate(frames[split_name], score, cost, horizon)
    return {
        "frames": frames, "feature_names": feature_names, "metrics": metrics, "scores": chosen_scores,
        "selection": {
            "name": "LSTM", "components": chosen["weights"], "valid_objective": chosen["score"],
            "candidate_count": len(selections),
            "candidates": [
                {
                    "name": item["label"], "weights": item["weights"], "selection_score": item["score"],
                    "train_rank_ic": item["train"].get("rank_ic", 0), "valid_rank_ic": item["valid"].get("rank_ic", 0),
                    "valid_sharpe": item["valid"].get("sharpe", 0),
                }
                for item in selections[:20]
            ],
            "component_ledger": ranked,
        },
    }


v2.ENGINE_VERSION = VERSION
v2.engine.ENGINE_VERSION = VERSION
v2._fit_tabular_ensemble = fit_stable_ensemble


if __name__ == "__main__":
    raise SystemExit(v2.engine.main())
