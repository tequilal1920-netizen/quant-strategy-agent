"""Production launcher for the stable Factor Laboratory worker."""
from __future__ import annotations

import math

import numpy as np

import stable_development as v3


VERSION = "factor-lab/2.2-stable-formula-weights"


def formula_scores(frame, formulas, weights):
    components = []
    for formula in formulas:
        score = v3.v2.engine.evaluate_postfix(frame, formula)
        rank = score.groupby(frame.trade_date).rank(pct=True).fillna(.5).to_numpy(float) - .5
        components.append(rank)
    weight_array = np.asarray(weights, dtype=float)
    denominator = max(float(np.abs(weight_array).sum()), 1e-12)
    return np.sum(np.stack(components) * weight_array[:, None], axis=0) / denominator


def effective_dsr(result, panel, trials: int | None = None):
    metrics = result.get("metrics") or {}
    test = metrics.get("test") or {}
    effective_observations = max(1, math.ceil(v3.v2.engine.finite(test.get("observations")) / max(1, panel.horizons[0])))
    trial_count = trials or int((result.get("selection") or {}).get("candidate_count") or 1)
    test.update(v3.v2.engine.deflated_sharpe_proxy(test.get("sharpe", 0), effective_observations, trial_count))
    test["effective_observations"] = effective_observations
    result["gates"] = v3.v2.gate_results(metrics, trial_count)
    result["engine_version"] = VERSION
    return result


_lstm = v3.v2.run_lstm_v2
_rl = v3.v2.run_rl_v2
_strategy = v3.v2.run_strategy_v2
_joint = v3.v2.run_joint_v2


def run_lstm(panel, config, progress_path):
    return effective_dsr(_lstm(panel, config, progress_path), panel)


def run_rl(panel, config, progress_path):
    result = _rl(panel, config, progress_path)
    trials = int((result.get("selection") or {}).get("candidate_count") or 1) + int((result.get("search") or {}).get("trial_count") or 0)
    return effective_dsr(result, panel, trials)


def run_strategy(panel, config, progress_path):
    return effective_dsr(_strategy(panel, config, progress_path), panel, 4)


def run_joint(panel, config, progress_path):
    result = _joint(panel, config, progress_path)
    return effective_dsr(result, panel, len(result.get("factors") or []))


v3.v2.ENGINE_VERSION = VERSION
v3.v2.engine.ENGINE_VERSION = VERSION
v3.v2._formula_scores = formula_scores
v3.v2.engine.run_lstm = run_lstm
v3.v2.engine.run_rl_transformer = run_rl
v3.v2.engine.run_strategy = run_strategy
v3.v2.engine.run_joint_test = run_joint


if __name__ == "__main__":
    raise SystemExit(v3.v2.engine.main())
