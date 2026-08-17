import numpy as np

from framework.backtest.kline_multiscale_expert import (
    causal_expert_mixture,
    choose_champion,
    selection_score,
)


def test_causal_mixture_is_invariant_to_future_feedback():
    rng = np.random.default_rng(7)
    periods, assets = 40, 120
    experts = {
        "趋势": rng.normal(size=(periods, assets)),
        "回撤": rng.normal(size=(periods, assets)),
    }
    feedback = rng.normal(scale=0.02, size=(periods, assets))
    eligible = np.ones((periods, assets), dtype=bool)
    first, weights_first, _ = causal_expert_mixture(experts, feedback, eligible)
    changed = feedback.copy()
    changed[31:] = rng.normal(loc=4.0, size=changed[31:].shape)
    second, weights_second, _ = causal_expert_mixture(experts, changed, eligible)
    np.testing.assert_allclose(first[:32], second[:32], equal_nan=True)
    np.testing.assert_allclose(weights_first[:32], weights_second[:32])


def _metrics(test_sharpe: float):
    block = {
        "periods": 40, "annual_return": 0.12, "excess_annual_return": 0.05,
        "sharpe": 0.8, "excess_sharpe": 0.6, "max_drawdown": -0.12,
        "turnover": 0.25, "rank_ic": 0.03, "win_rate": 0.55,
    }
    return {"train": dict(block), "valid": dict(block), "test": {**block, "sharpe": test_sharpe}}


def test_selection_score_never_uses_test_metrics():
    low = selection_score(_metrics(-9.0))
    high = selection_score(_metrics(9.0))
    assert low == high
    assert low["test_used"] is False


def test_champion_selection_ignores_test_ordering():
    weak = _metrics(99.0)
    weak["valid"]["excess_sharpe"] = 0.2
    strong = _metrics(-99.0)
    result = choose_champion({"验证较弱": {"metrics": weak}, "验证较强": {"metrics": strong}})
    assert result["name"] == "验证较强"
    assert result["test_used_for_selection"] is False
