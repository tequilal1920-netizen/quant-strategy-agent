"""Final Factor Laboratory launcher with stable RL formula selection."""
from __future__ import annotations

import itertools

import effective_dsr as v4


VERSION = "factor-lab/3.6.1-deep-anti-overfit"


def _metric(metrics, name, default=0.0):
    try:
        value = float((metrics or {}).get(name, default) or default)
    except Exception:
        return float(default)
    return value if value == value else float(default)


def _anti_overfit_score(train_metrics, valid_metrics, config):
    base = v4.v3.stable_objective(train_metrics, valid_metrics)
    train_ic = _metric(train_metrics, "rank_ic")
    valid_ic = _metric(valid_metrics, "rank_ic")
    train_sharpe = _metric(train_metrics, "sharpe")
    valid_sharpe = _metric(valid_metrics, "sharpe")
    valid_turnover = _metric(valid_metrics, "turnover")
    valid_drawdown = abs(_metric(valid_metrics, "max_drawdown", _metric(valid_metrics, "drawdown")))
    ic_gap = max(0.0, train_ic - valid_ic)
    sharpe_gap = max(0.0, train_sharpe - valid_sharpe)
    turnover_excess = max(0.0, valid_turnover - float(config.get("max_valid_turnover", 0.80)))
    drawdown_excess = max(0.0, valid_drawdown - float(config.get("max_valid_drawdown", 0.30)))
    weak_signal_penalty = 0.0
    if valid_ic <= float(config.get("min_valid_rank_ic", 0.0)):
        weak_signal_penalty += 0.20
    if valid_sharpe <= float(config.get("min_valid_sharpe", 0.0)):
        weak_signal_penalty += 0.10
    return base - 2.2 * ic_gap - 0.08 * sharpe_gap - 0.45 * turnover_excess - 0.65 * drawdown_excess - weak_signal_penalty


def _anti_overfit_gate(train_metrics, valid_metrics, config):
    train_ic = _metric(train_metrics, "rank_ic")
    valid_ic = _metric(valid_metrics, "rank_ic")
    valid_sharpe = _metric(valid_metrics, "sharpe")
    valid_turnover = _metric(valid_metrics, "turnover")
    valid_drawdown = abs(_metric(valid_metrics, "max_drawdown", _metric(valid_metrics, "drawdown")))
    return (
        valid_ic > float(config.get("min_valid_rank_ic", 0.0))
        and valid_sharpe > float(config.get("min_valid_sharpe", 0.0))
        and max(0.0, train_ic - valid_ic) <= float(config.get("max_train_valid_ic_gap", 0.08))
        and valid_turnover <= float(config.get("max_valid_turnover", 0.80))
        and valid_drawdown <= float(config.get("max_valid_drawdown", 0.30))
    )


def run_rl_stable(panel, config, progress_path):
    transformer_error = None
    try:
        base_result = v4.v3.v2._base_run_rl(panel, config, progress_path)
    except RuntimeError as exc:
        if "PyTorch" not in str(exc):
            raise
        transformer_error = str(exc)
        base_result = {
            "engine": "rl_transformer",
            "engine_version": VERSION,
            "search": {"trial_count": 0},
            "candidates": [],
            "runtime_status": "degraded",
        }
    frames, target = v4.v3.v2._rl_frames(panel)
    formulas = [
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
        ["ret_20", "CS_RANK", "vol_20", "CS_RANK", "DIV"],
        ["large_flow", "CS_RANK", "turnover", "CS_RANK", "SUB"],
    ]
    for item in base_result.get("candidates") or []:
        formula = item.get("formula_postfix")
        if formula and formula not in formulas:
            formulas.append(formula)
    engine = v4.v3.v2.engine
    cost = float(config.get("cost_bps", 15))
    horizon = panel.horizons[0]
    evaluated = []
    for formula in formulas:
        scores = {}
        positive = {}
        negative = {}
        for split_name in ("train", "valid"):
            scores[split_name] = engine.evaluate_postfix(frames[split_name], formula)
            work = frames[split_name][["trade_date", "ts_code", target]].copy(); work["score"] = scores[split_name]
            positive[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
            work["score"] = -scores[split_name]
            negative[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
        positive_score = _anti_overfit_score(positive["train"], positive["valid"], config)
        negative_score = _anti_overfit_score(negative["train"], negative["valid"], config)
        direction = 1 if positive_score >= negative_score else -1
        chosen_metrics = positive if direction > 0 else negative
        anti_overfit_pass = _anti_overfit_gate(chosen_metrics["train"], chosen_metrics["valid"], config)
        evaluated.append({
            "formula_postfix": formula, "formula": " ".join(formula), "direction": direction,
            "selection_score": max(positive_score, negative_score), "metrics": chosen_metrics,
            "anti_overfit_pass": anti_overfit_pass,
            "train_valid_ic_gap": max(0.0, _metric(chosen_metrics["train"], "rank_ic") - _metric(chosen_metrics["valid"], "rank_ic")),
        })
    evaluated.sort(key=lambda x: x["selection_score"], reverse=True)
    gated_evaluated = [item for item in evaluated if item.get("anti_overfit_pass")]
    pool_source = gated_evaluated if gated_evaluated else evaluated
    pool = pool_source[: min(7, len(pool_source))]
    combinations = []
    for size in range(1, min(3, len(pool)) + 1):
        for combo in itertools.combinations(pool, size):
            weight_sets = [[1.0]] if size == 1 else ([[.25, .75], [.5, .5], [.75, .25]] if size == 2 else [[1 / 3] * 3])
            for weights in weight_sets:
                formula_set = [item["formula_postfix"] for item in combo]
                directions = [item["direction"] for item in combo]
                metrics = {}
                for split_name in ("train", "valid"):
                    score = v4.formula_scores(frames[split_name], formula_set, [w * d for w, d in zip(weights, directions)])
                    work = frames[split_name][["trade_date", "ts_code", target]].copy(); work["score"] = score
                    metrics[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
                combo_score = _anti_overfit_score(metrics["train"], metrics["valid"], config)
                combinations.append({
                    "formulas": formula_set, "directions": directions, "weights": weights,
                    "selection_score": combo_score,
                    "metrics": metrics,
                    "anti_overfit_pass": _anti_overfit_gate(metrics["train"], metrics["valid"], config),
                    "train_valid_ic_gap": max(0.0, _metric(metrics["train"], "rank_ic") - _metric(metrics["valid"], "rank_ic")),
                })
    combinations.sort(key=lambda x: x["selection_score"], reverse=True)
    gated_combinations = [item for item in combinations if item.get("anti_overfit_pass")]
    chosen_pool = gated_combinations if gated_combinations else combinations
    chosen = chosen_pool[0]
    metrics = {}
    for split_name in ("train", "valid", "test"):
        score = v4.formula_scores(frames[split_name], chosen["formulas"], [w * d for w, d in zip(chosen["weights"], chosen["directions"])])
        work = frames[split_name][["trade_date", "ts_code", target]].copy(); work["score"] = score
        metrics[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
    trials = len(evaluated) + len(combinations) + int((base_result.get("search") or {}).get("trial_count") or 0)
    base_result.update({
        "engine_version": VERSION,
        "architecture": {
            "name": "RL+Transformer",
            "components": ["Transformer", "PPO", "grammar_mask", "quality_diversity_archive", "stable_development_folds", "train_valid_gap_gate", "turnover_drawdown_penalty"] if transformer_error is None else ["grammar_formula_library", "stable_development_folds", "train_valid_gap_gate", "turnover_drawdown_penalty"],
            "transformer_runtime_status": "ok" if transformer_error is None else "blocked",
            "transformer_runtime_error": transformer_error,
            "execution_mode": "rl_transformer" if transformer_error is None else "curated_formula_fallback",
        },
        "metrics": metrics,
        "selection": {
            "name": "RL+Transformer", "formulas": chosen["formulas"], "directions": chosen["directions"],
            "weights": chosen["weights"], "valid_objective": chosen["selection_score"],
            "candidate_count": len(combinations),
            "gated_candidate_count": len(gated_combinations),
            "anti_overfit_policy": "valid_ic/sharpe positive + train_valid_gap/turnover/drawdown gates; test untouched",
        },
        "candidates": [
            {
                "name": f"候选 {index + 1}", "formula": item["formula"], "formula_postfix": item["formula_postfix"],
                "direction": item["direction"], "selection_score": item["selection_score"],
                "anti_overfit_pass": item.get("anti_overfit_pass", False),
                "train_valid_ic_gap": item.get("train_valid_ic_gap", 0),
                "train_rank_ic": item["metrics"]["train"].get("rank_ic", 0),
                "valid_rank_ic": item["metrics"]["valid"].get("rank_ic", 0),
                "valid_sharpe": item["metrics"]["valid"].get("sharpe", 0),
            }
            for index, item in enumerate(evaluated[:24])
        ],
        "diagnostics": v4.v3.v2._diagnostics(metrics, horizon, cost),
        "gates": v4.v3.v2.gate_results(metrics, trials),
        "test_used_for_search": False,
    })
    return v4.effective_dsr(base_result, panel, trials)


v4.v3.v2.ENGINE_VERSION = VERSION
v4.v3.v2.engine.ENGINE_VERSION = VERSION
v4.v3.v2.engine.run_rl_transformer = run_rl_stable


if __name__ == "__main__":
    raise SystemExit(v4.v3.v2.engine.main())
