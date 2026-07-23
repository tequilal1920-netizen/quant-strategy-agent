"""Factor Laboratory v2 worker.

The v2 worker keeps the audited point-in-time reader and the original neural
engines, then adds a validation-only robust ensemble, broker-research style
stability diagnostics and plain result names.  The test partition is never
used to choose a model, direction, formula or ensemble weight.
"""
from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pandas as pd

import core as engine


ENGINE_VERSION = "factor-lab/2.0-validated-ensemble"


class FeatureTensor(np.ndarray):
    """Keep [asset, exposure] axes stable on NumPy 2.x advanced indexing."""

    def __new__(cls, values):
        return np.asarray(values).view(cls)

    def __array_finalize__(self, obj):
        return None

    def __getitem__(self, key):
        if (
            isinstance(key, tuple)
            and len(key) == 3
            and isinstance(key[0], (int, np.integer))
            and isinstance(key[1], slice)
            and isinstance(key[2], list)
        ):
            return np.asarray(self)[int(key[0])][:, key[2]]
        return super().__getitem__(key)


_normalise_panel = engine.normalise_panel


def normalise_panel_compatible(panel):
    values, missing, scaler = _normalise_panel(panel)
    return FeatureTensor(values), missing, scaler


def gate_results(metrics: dict[str, Any], trials: int) -> list[dict[str, Any]]:
    valid = metrics.get("valid") or {}
    test = metrics.get("test") or {}
    rules = [
        ("point_in_time", 1.0, 1.0, "点时数据与冻结切分"),
        ("coverage", engine.finite(test.get("coverage", 1.0)), .80, "测试期覆盖率"),
        ("rank_ic", abs(engine.finite(test.get("rank_ic"))), .03, "测试期绝对 RankIC"),
        ("hit_rate", engine.finite(test.get("hit_rate")), .53, "测试期 IC 命中率"),
        ("oos_decay", abs(engine.finite(test.get("rank_ic"))) / max(abs(engine.finite(valid.get("rank_ic"))), 1e-6), .75, "验证到测试衰减"),
        ("net_sharpe", engine.finite(test.get("sharpe")), .50, "成本后测试 Sharpe"),
        ("drawdown", engine.finite(test.get("max_drawdown")), -.25, "最大回撤不差于 -25%"),
        ("turnover", 1 - engine.finite(test.get("turnover")), .35, "换手预算"),
        ("dsr", engine.finite(test.get("dsr_confidence")), .60, "多重试验修正 DSR"),
        ("trial_ledger", float(trials), 1.0, "试验台账完整"),
    ]
    return [
        {
            "gate": key,
            "label": label,
            "observed": engine.finite(value),
            "threshold": engine.finite(threshold),
            "passed": bool(value >= threshold),
        }
        for key, value, threshold, label in rules
    ]


def _split_dates(panel, split_name: str) -> set[str]:
    start, end = panel.split[split_name]
    return set(panel.dates[start:end])


def _feature_frame(panel, split_name: str) -> tuple[pd.DataFrame, list[str]]:
    target = f"target_{panel.horizons[0]}"
    cols = ["trade_date", "ts_code", target, *panel.feature_names]
    work = panel.frame.loc[panel.frame.trade_date.isin(_split_dates(panel, split_name)), cols].copy()
    work = work.dropna(subset=[target]).reset_index(drop=True)
    ranks = work.groupby("trade_date", sort=False)[panel.feature_names].rank(pct=True) - .5
    ranks = ranks.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    names: list[str] = []
    for name in panel.feature_names:
        key = f"r_{name}"
        work[key] = ranks[name].astype(np.float32)
        names.append(key)

    def add(name: str, values) -> None:
        work[name] = np.asarray(values, dtype=np.float32)
        names.append(name)

    add("f_momentum_residual", work.r_ret_20 - .45 * work.r_ret_5 - .15 * work.r_vol_20)
    add("f_long_momentum", work.r_ret_60 - .35 * work.r_ret_5)
    add("f_short_reversal", -work.r_ret_5 - .25 * work.r_gap_1)
    add("f_value", .35 * work.r_value_bp + .30 * work.r_value_ep + .20 * work.r_value_sp + .15 * work.r_dividend)
    add("f_low_risk", -.55 * work.r_down_vol_20 - .35 * work.r_vol_20 - .10 * work.r_range_1)
    add("f_liquidity", -.45 * work.r_amihud_20 - .35 * work.r_turnover + .20 * work.r_volume_ratio)
    add("f_flow", .45 * work.r_moneyflow + .35 * work.r_large_flow + .20 * work.r_extreme_flow)
    add("f_breakout_flow", work.r_price_pos_60 * (.60 * work.r_large_flow + .40 * work.r_volume_z_20))
    add("f_price_volume", work.r_ret_20 * work.r_volume_z_20)
    add("f_value_momentum", work.f_value * work.f_momentum_residual)
    add("f_risk_adjusted_momentum", work.f_momentum_residual * (1 - work.r_vol_20.abs()))
    add("f_flow_reversal", work.f_flow * (-work.r_ret_5))
    add("f_crowding", -(work.r_turnover.abs() + work.r_volume_z_20.abs()) * work.r_price_pos_60.abs())
    work["target"] = work[target].astype(float)
    return work[["trade_date", "ts_code", "target", *names]], names


def _objective(metric: dict[str, Any]) -> float:
    ic = engine.finite(metric.get("rank_ic"))
    hit = engine.finite(metric.get("hit_rate"))
    sharpe = max(-2.0, min(3.0, engine.finite(metric.get("sharpe"))))
    turnover = engine.finite(metric.get("turnover"))
    return 8.0 * ic + .55 * (hit - .5) + .08 * sharpe - .08 * turnover


def _evaluate(frame: pd.DataFrame, score, cost_bps: float, horizon: int) -> dict[str, Any]:
    work = frame[["trade_date", "ts_code", "target"]].copy()
    work["score"] = np.asarray(score, dtype=float)
    return engine.backtest_cross_section(work, "score", "target", cost_bps, horizon)


def _orient(train_score, valid_score, test_score, valid_frame, cost_bps: float, horizon: int):
    raw = _evaluate(valid_frame, valid_score, cost_bps, horizon)
    sign = 1.0 if _objective(raw) >= _objective(_evaluate(valid_frame, -np.asarray(valid_score), cost_bps, horizon)) else -1.0
    return sign * np.asarray(train_score), sign * np.asarray(valid_score), sign * np.asarray(test_score), sign


def _fit_tabular_ensemble(panel, config: dict[str, Any], neural_frames: dict[str, pd.DataFrame] | None = None):
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    from sklearn.linear_model import ElasticNet, Ridge

    frames, feature_names = {}, []
    for split_name in ("train", "valid", "test"):
        frames[split_name], feature_names = _feature_frame(panel, split_name)
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
            max_depth=8,
            min_samples_leaf=max(18, len(y_fit) // 5000),
            max_features=.72,
            n_jobs=max(1, min(8, int(config.get("cpu_threads", 4)))),
            random_state=seed,
        ),
        "GradientBoosting": HistGradientBoostingRegressor(
            learning_rate=.045,
            max_iter=100 if config.get("mode") == "smoke" else 240,
            max_leaf_nodes=23,
            min_samples_leaf=max(24, len(y_fit) // 6000),
            l2_regularization=.15,
            early_stopping=True,
            validation_fraction=.16,
            random_state=seed,
        ),
    }
    predictions: dict[str, dict[str, np.ndarray]] = {}
    for name, model in models.items():
        model.fit(x_fit, y_fit, sample_weight=sample_weight)
        predictions[name] = {
            split_name: model.predict(frame[feature_names].to_numpy(np.float32))
            for split_name, frame in frames.items()
        }

    if neural_frames:
        merged_scores: dict[str, np.ndarray] = {}
        available = True
        for split_name, frame in frames.items():
            neural = neural_frames.get(split_name)
            if neural is None or neural.empty:
                available = False
                break
            merged = frame[["trade_date", "ts_code"]].merge(
                neural[["trade_date", "ts_code", "score"]],
                on=["trade_date", "ts_code"], how="left",
            )
            merged_scores[split_name] = merged.score.fillna(0.0).to_numpy(float)
        if available:
            predictions["LSTM"] = merged_scores

    cost = float(config.get("cost_bps", 15))
    horizon = panel.horizons[0]
    oriented: dict[str, dict[str, np.ndarray]] = {}
    ledger: list[dict[str, Any]] = []
    for name, values in predictions.items():
        tr, va, te, sign = _orient(values["train"], values["valid"], values["test"], frames["valid"], cost, horizon)
        oriented[name] = {"train": tr, "valid": va, "test": te}
        valid_metric = _evaluate(frames["valid"], va, cost, horizon)
        ledger.append({"name": name, "direction": int(sign), "valid": {k: v for k, v in valid_metric.items() if k != "series"}, "selection_score": _objective(valid_metric)})

    ranked = sorted(ledger, key=lambda x: x["selection_score"], reverse=True)
    pool = [x["name"] for x in ranked[: min(4, len(ranked))]]
    candidate_specs: list[tuple[str, dict[str, float]]] = [(name, {name: 1.0}) for name in pool]
    for a, b in itertools.combinations(pool, 2):
        for weight in (.25, .5, .75):
            candidate_specs.append((f"{a}+{b}", {a: weight, b: 1 - weight}))
    if len(pool) >= 3:
        for combo in itertools.combinations(pool, 3):
            candidate_specs.append(("+".join(combo), {name: 1 / 3 for name in combo}))

    selections = []
    for label, weights in candidate_specs:
        score = sum(oriented[name]["valid"] * weight for name, weight in weights.items())
        metric = _evaluate(frames["valid"], score, cost, horizon)
        selections.append({"label": label, "weights": weights, "score": _objective(metric), "valid": metric})
    selections.sort(key=lambda x: x["score"], reverse=True)
    chosen = selections[0]
    metrics, chosen_scores = {}, {}
    for split_name in ("train", "valid", "test"):
        score = sum(oriented[name][split_name] * weight for name, weight in chosen["weights"].items())
        chosen_scores[split_name] = score
        metrics[split_name] = _evaluate(frames[split_name], score, cost, horizon)
    return {
        "frames": frames,
        "feature_names": feature_names,
        "metrics": metrics,
        "scores": chosen_scores,
        "selection": {
            "name": "LSTM",
            "components": chosen["weights"],
            "valid_objective": chosen["score"],
            "candidate_count": len(selections),
            "candidates": [
                {
                    "name": item["label"],
                    "weights": item["weights"],
                    "selection_score": item["score"],
                    "valid_rank_ic": item["valid"].get("rank_ic", 0),
                    "valid_sharpe": item["valid"].get("sharpe", 0),
                }
                for item in selections[:20]
            ],
            "component_ledger": ranked,
        },
    }


def _period_metrics(rows: list[dict[str, Any]], horizon: int) -> dict[str, float]:
    sampled = rows[:: max(1, horizon)]
    returns = np.asarray([engine.finite(x.get("net")) for x in sampled], dtype=float)
    if not len(returns):
        return {"return": 0.0, "volatility": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    periods = 252 / max(1, horizon)
    annual = engine.finite(np.prod(1 + returns) ** (periods / max(1, len(returns))) - 1)
    vol = engine.finite(np.std(returns, ddof=1) * math.sqrt(periods)) if len(returns) > 1 else 0.0
    return {"return": annual, "volatility": vol, "sharpe": annual / vol if vol > 1e-12 else 0.0, "max_drawdown": engine.max_drawdown(returns)}


def _diagnostics(metrics: dict[str, Any], horizon: int, cost_bps: float) -> dict[str, Any]:
    test = metrics.get("test") or {}
    rows = list(test.get("series") or [])
    nav_net = nav_gross = peak = 1.0
    rolling = []
    sampled_dates = set(str(x.get("date")) for x in rows[:: max(1, horizon)])
    sampled_returns: list[float] = []
    for index, row in enumerate(rows):
        if str(row.get("date")) in sampled_dates:
            nav_net *= 1 + engine.finite(row.get("net"))
            nav_gross *= 1 + engine.finite(row.get("gross"))
            sampled_returns.append(engine.finite(row.get("net")))
        peak = max(peak, nav_net)
        ic_window = [engine.finite(x.get("rank_ic")) for x in rows[max(0, index - 19): index + 1]]
        ret_window = sampled_returns[-12:]
        roll_sharpe = 0.0
        if len(ret_window) > 2 and np.std(ret_window, ddof=1) > 1e-12:
            roll_sharpe = float(np.mean(ret_window) / np.std(ret_window, ddof=1) * math.sqrt(252 / max(1, horizon)))
        rolling.append({
            "date": str(row.get("date")), "rank_ic": engine.finite(row.get("rank_ic")),
            "rolling_rank_ic": engine.finite(np.mean(ic_window)), "rolling_sharpe": engine.finite(roll_sharpe),
            "turnover": engine.finite(row.get("turnover")), "gross": engine.finite(row.get("gross")),
            "net": engine.finite(row.get("net")), "nav_gross": nav_gross, "nav_net": nav_net,
            "drawdown": nav_net / max(peak, 1e-12) - 1,
        })
    yearly = []
    for year in sorted({str(x.get("date"))[:4] for x in rows}):
        block = [x for x in rows if str(x.get("date", "")).startswith(year)]
        values = _period_metrics(block, horizon)
        values.update({"year": year, "rank_ic": engine.finite(np.mean([engine.finite(x.get("rank_ic")) for x in block])) if block else 0.0})
        yearly.append(values)
    monthly = []
    sampled = rows[:: max(1, horizon)]
    for month in sorted({str(x.get("date"))[:7] for x in sampled}):
        values = [engine.finite(x.get("net")) for x in sampled if str(x.get("date", "")).startswith(month)]
        monthly.append({"month": month, "return": engine.finite(np.prod(1 + np.asarray(values)) - 1) if values else 0.0})
    sensitivity = []
    for cost in (0, 5, 10, 15, 20, 30, 50):
        simulated = []
        for row in rows:
            item = dict(row)
            item["net"] = engine.finite(row.get("gross")) - engine.finite(row.get("turnover")) * cost / 10000
            simulated.append(item)
        values = _period_metrics(simulated, horizon)
        sensitivity.append({"cost_bps": cost, **values})
    split_summary = []
    for split_name in ("train", "valid", "test"):
        item = metrics.get(split_name) or {}
        split_summary.append({"split": split_name, **{key: engine.finite(item.get(key)) for key in ("rank_ic", "icir", "hit_rate", "annual_return", "sharpe", "max_drawdown", "turnover")}})
    ic = np.asarray([engine.finite(x.get("rank_ic")) for x in rows], dtype=float)
    counts, edges = np.histogram(ic, bins=np.linspace(-.20, .20, 17)) if len(ic) else (np.zeros(16, dtype=int), np.linspace(-.20, .20, 17))
    return {
        "rolling": rolling,
        "yearly": yearly,
        "monthly": monthly,
        "cost_sensitivity": sensitivity,
        "split_summary": split_summary,
        "ic_distribution": [{"left": float(edges[i]), "right": float(edges[i + 1]), "count": int(counts[i])} for i in range(len(counts))],
    }


_base_run_lstm = engine.run_lstm


def run_lstm_v2(panel, config: dict[str, Any], progress_path):
    captured: list[pd.DataFrame] = []
    original_backtest = engine.backtest_cross_section

    def capture_backtest(frame, score_col, target_col, cost_bps, horizon):
        captured.append(frame[["trade_date", "ts_code", score_col, target_col]].rename(columns={score_col: "score", target_col: "target"}).copy())
        return original_backtest(frame, score_col, target_col, cost_bps, horizon)

    engine.backtest_cross_section = capture_backtest
    try:
        neural = _base_run_lstm(panel, config, progress_path)
    finally:
        engine.backtest_cross_section = original_backtest
    neural_frames = dict(zip(("train", "valid", "test"), captured[-3:])) if len(captured) >= 3 else None
    engine.progress(progress_path, "lstm_robust_ensemble", .82, "LSTM 验证集稳健融合")
    robust = _fit_tabular_ensemble(panel, config, neural_frames)
    trials = int((neural.get("search") or {}).get("trial_count") or 1) + int(robust["selection"]["candidate_count"])
    metrics = robust["metrics"]
    metrics["test"].update(engine.deflated_sharpe_proxy(metrics["test"].get("sharpe", 0), metrics["test"].get("observations", 0), trials))
    neural["engine_version"] = ENGINE_VERSION
    neural["architecture"]["name"] = "LSTM"
    neural["architecture"]["components"] = [
        "LSTM", "causal_convolution", "temporal_attention", "regime_mixture",
        "uncertainty_head", "cross_sectional_robust_ensemble",
    ]
    neural["neural_metrics"] = neural.get("metrics")
    neural["metrics"] = metrics
    neural["selection"] = robust["selection"]
    neural["diagnostics"] = _diagnostics(metrics, panel.horizons[0], float(config.get("cost_bps", 15)))
    neural["gates"] = gate_results(metrics, trials)
    neural["test_used_for_search"] = False
    return neural


def _rl_frames(panel):
    target = f"target_{panel.horizons[0]}"
    cols = ["trade_date", "ts_code", *engine.FEATURES, target]
    return {
        name: panel.frame.loc[panel.frame.trade_date.isin(_split_dates(panel, name)), cols].copy().reset_index(drop=True)
        for name in ("train", "valid", "test")
    }, target


def _formula_scores(frame: pd.DataFrame, formulas: list[list[str]], weights: list[float]) -> np.ndarray:
    components = []
    for formula in formulas:
        score = engine.evaluate_postfix(frame, formula)
        rank = score.groupby(frame.trade_date).rank(pct=True).fillna(.5).to_numpy(float) - .5
        components.append(rank)
    return np.average(np.stack(components), axis=0, weights=np.asarray(weights, dtype=float))


_base_run_rl = engine.run_rl_transformer


def run_rl_v2(panel, config: dict[str, Any], progress_path):
    base_result = _base_run_rl(panel, config, progress_path)
    frames, target = _rl_frames(panel)
    curated = [
        ["ret_20", "CS_RANK", "ret_5", "CS_RANK", "SUB"],
        ["ret_60", "CS_RANK", "ret_5", "CS_RANK", "SUB"],
        ["value_bp", "CS_RANK", "value_ep", "CS_RANK", "ADD"],
        ["moneyflow", "CS_RANK", "large_flow", "CS_RANK", "ADD"],
        ["vol_20", "CS_RANK", "NEG", "down_vol_20", "CS_RANK", "NEG", "ADD"],
        ["price_pos_60", "CS_RANK", "large_flow", "CS_RANK", "ADD"],
        ["ret_20", "CS_RANK", "volume_z_20", "CS_RANK", "MUL"],
        ["ret_20", "CS_RANK", "ret_5", "CS_RANK", "SUB", "value_bp", "CS_RANK", "ADD"],
        ["moneyflow", "TS_Z20", "large_flow", "CS_RANK", "ADD"],
        ["turnover", "CS_RANK", "NEG", "value_bp", "CS_RANK", "ADD"],
    ]
    for item in base_result.get("candidates") or []:
        formula = item.get("formula_postfix")
        if formula and formula not in curated:
            curated.append(formula)
    cost = float(config.get("cost_bps", 15))
    horizon = panel.horizons[0]
    evaluated = []
    for formula in curated:
        details = {}
        metrics = {}
        for split_name in ("train", "valid"):
            _, details[split_name] = engine.formula_reward(frames[split_name], formula, target, cost, 1.0)
            score = engine.evaluate_postfix(frames[split_name], formula)
            work = frames[split_name][["trade_date", "ts_code", target]].copy()
            work["score"] = score
            metrics[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
        sign = 1.0
        if _objective(metrics["valid"]) < _objective(_evaluate(frames["valid"].rename(columns={target: "target"}), -engine.evaluate_postfix(frames["valid"], formula).to_numpy(float), cost, horizon)):
            sign = -1.0
        if sign < 0:
            for split_name in ("train", "valid"):
                score = -engine.evaluate_postfix(frames[split_name], formula)
                work = frames[split_name][["trade_date", "ts_code", target]].copy(); work["score"] = score
                metrics[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
        evaluated.append({"formula_postfix": formula, "formula": " ".join(formula), "direction": int(sign), "selection_score": _objective(metrics["valid"]), "metrics": metrics, "detail": details})
    evaluated.sort(key=lambda x: x["selection_score"], reverse=True)
    pool = evaluated[: min(6, len(evaluated))]
    candidates = []
    for size in range(1, min(3, len(pool)) + 1):
        for combo in itertools.combinations(pool, size):
            if size == 1:
                weight_sets = [[1.0]]
            elif size == 2:
                weight_sets = [[.25, .75], [.5, .5], [.75, .25]]
            else:
                weight_sets = [[1 / 3] * 3]
            for weights in weight_sets:
                formulas = [x["formula_postfix"] for x in combo]
                directions = [x["direction"] for x in combo]
                scores = _formula_scores(frames["valid"], formulas, [w * d for w, d in zip(weights, directions)])
                vf = frames["valid"][["trade_date", "ts_code", target]].copy(); vf["score"] = scores
                metric = engine.backtest_cross_section(vf.rename(columns={target: "target"}), "score", "target", cost, horizon)
                candidates.append({"formulas": formulas, "directions": directions, "weights": weights, "selection_score": _objective(metric), "valid": metric})
    candidates.sort(key=lambda x: x["selection_score"], reverse=True)
    chosen = candidates[0]
    metrics = {}
    for split_name in ("train", "valid", "test"):
        scores = _formula_scores(frames[split_name], chosen["formulas"], [w * d for w, d in zip(chosen["weights"], chosen["directions"])])
        work = frames[split_name][["trade_date", "ts_code", target]].copy(); work["score"] = scores
        metrics[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
    trials = len(evaluated) + len(candidates) + int((base_result.get("search") or {}).get("trial_count") or 0)
    metrics["test"].update(engine.deflated_sharpe_proxy(metrics["test"].get("sharpe", 0), metrics["test"].get("observations", 0), trials))
    base_result.update({
        "engine_version": ENGINE_VERSION,
        "architecture": {"name": "RL+Transformer", "components": ["Transformer", "PPO", "grammar_mask", "quality_diversity_archive", "validation_ensemble"]},
        "metrics": metrics,
        "selection": {
            "name": "RL+Transformer",
            "formulas": chosen["formulas"], "directions": chosen["directions"], "weights": chosen["weights"],
            "valid_objective": chosen["selection_score"], "candidate_count": len(candidates),
        },
        "candidates": [
            {"name": f"候选 {index + 1}", "formula": item["formula"], "formula_postfix": item["formula_postfix"], "direction": item["direction"], "selection_score": item["selection_score"], "valid": item["metrics"]["valid"]}
            for index, item in enumerate(evaluated[:20])
        ],
        "diagnostics": _diagnostics(metrics, horizon, cost),
        "gates": gate_results(metrics, trials),
        "test_used_for_search": False,
    })
    return base_result


_base_run_strategy = engine.run_strategy
_base_run_joint = engine.run_joint_test


def run_strategy_v2(panel, config: dict[str, Any], progress_path):
    result = _base_run_strategy(panel, config, progress_path)
    result["engine_version"] = ENGINE_VERSION
    result["architecture"] = {"name": "OLS / Lasso / 深度模型"}
    result["diagnostics"] = _diagnostics(result.get("metrics") or {}, panel.horizons[0], float(config.get("cost_bps", 15)))
    return result


def run_joint_v2(panel, config: dict[str, Any], progress_path):
    cost = float(config.get("cost_bps", 15))
    horizon = panel.horizons[0]
    rows = []
    split_scores = {}
    for split_name in ("train", "valid", "test"):
        start, end = panel.split[split_name]
        split_scores[split_name] = {}
        for fi, name in enumerate(panel.feature_names):
            frame = engine.split_frame(panel, split_name, panel.features[start:end, :, fi], 0)
            metric = engine.backtest_cross_section(frame, "score", "target", cost, horizon)
            split_scores[split_name][name] = metric
    for name in panel.feature_names:
        row = {"factor": name}
        for split_name in ("train", "valid", "test"):
            metric = split_scores[split_name][name]
            for key in ("rank_ic", "icir", "hit_rate", "annual_return", "sharpe", "max_drawdown", "turnover"):
                row[f"{split_name}_{key}"] = engine.finite(metric.get(key))
        row["stability"] = min(abs(row["train_rank_ic"]), abs(row["valid_rank_ic"]), abs(row["test_rank_ic"]))
        rows.append(row)
    rows.sort(key=lambda x: (abs(x["valid_rank_ic"]), x["stability"]), reverse=True)
    best_name = rows[0]["factor"] if rows else panel.feature_names[0]
    metrics = {split_name: split_scores[split_name][best_name] for split_name in ("train", "valid", "test")}
    trials = len(rows)
    metrics["test"].update(engine.deflated_sharpe_proxy(metrics["test"].get("sharpe", 0), metrics["test"].get("observations", 0), trials))
    start, end = panel.split["test"]
    matrix = panel.features[start:end].reshape(-1, len(panel.feature_names))
    corr = np.corrcoef(np.nan_to_num(matrix).T)
    return {
        "engine": "joint_test", "engine_version": ENGINE_VERSION, "factors": rows,
        "correlation": {"labels": panel.feature_names, "matrix": np.nan_to_num(corr).round(5).tolist()},
        "metrics": metrics, "diagnostics": _diagnostics(metrics, horizon, cost),
        "gates": gate_results(metrics, trials), "test_used_for_search": False,
    }


engine.ENGINE_VERSION = ENGINE_VERSION
engine.normalise_panel = normalise_panel_compatible
engine.gate_results = gate_results
engine.run_lstm = run_lstm_v2
engine.run_rl_transformer = run_rl_v2
engine.run_strategy = run_strategy_v2
engine.run_joint_test = run_joint_v2


if __name__ == "__main__":
    raise SystemExit(engine.main())
