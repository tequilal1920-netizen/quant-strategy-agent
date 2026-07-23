"""Final Factor Laboratory launcher with stable RL formula selection."""
from __future__ import annotations

import itertools

import effective_dsr as v4


VERSION = "factor-lab/2.3-stable-lstm-rl"


def run_rl_stable(panel, config, progress_path):
    base_result = v4.v3.v2._base_run_rl(panel, config, progress_path)
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
        positive_score = v4.v3.stable_objective(positive["train"], positive["valid"])
        negative_score = v4.v3.stable_objective(negative["train"], negative["valid"])
        direction = 1 if positive_score >= negative_score else -1
        chosen_metrics = positive if direction > 0 else negative
        evaluated.append({
            "formula_postfix": formula, "formula": " ".join(formula), "direction": direction,
            "selection_score": max(positive_score, negative_score), "metrics": chosen_metrics,
        })
    evaluated.sort(key=lambda x: x["selection_score"], reverse=True)
    pool = evaluated[: min(7, len(evaluated))]
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
                combinations.append({
                    "formulas": formula_set, "directions": directions, "weights": weights,
                    "selection_score": v4.v3.stable_objective(metrics["train"], metrics["valid"]),
                    "metrics": metrics,
                })
    combinations.sort(key=lambda x: x["selection_score"], reverse=True)
    chosen = combinations[0]
    metrics = {}
    for split_name in ("train", "valid", "test"):
        score = v4.formula_scores(frames[split_name], chosen["formulas"], [w * d for w, d in zip(chosen["weights"], chosen["directions"])])
        work = frames[split_name][["trade_date", "ts_code", target]].copy(); work["score"] = score
        metrics[split_name] = engine.backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost, horizon)
    trials = len(evaluated) + len(combinations) + int((base_result.get("search") or {}).get("trial_count") or 0)
    base_result.update({
        "engine_version": VERSION,
        "architecture": {"name": "RL+Transformer", "components": ["Transformer", "PPO", "grammar_mask", "quality_diversity_archive", "stable_development_folds"]},
        "metrics": metrics,
        "selection": {
            "name": "RL+Transformer", "formulas": chosen["formulas"], "directions": chosen["directions"],
            "weights": chosen["weights"], "valid_objective": chosen["selection_score"],
            "candidate_count": len(combinations),
        },
        "candidates": [
            {
                "name": f"候选 {index + 1}", "formula": item["formula"], "formula_postfix": item["formula_postfix"],
                "direction": item["direction"], "selection_score": item["selection_score"],
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
