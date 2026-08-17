import numpy as np
import pytest

from framework.backtest.kline_supervised_ranker import (
    LGBMRanker,
    train_chronological_kline_ranker,
    train_chronological_market_exposure,
)


@pytest.mark.skipif(LGBMRanker is None, reason="lightgbm unavailable")
def test_supervised_ranker_does_not_use_validation_or_test_labels():
    rng = np.random.default_rng(11)
    periods, assets = 70, 130
    experts = {
        "趋势": rng.normal(size=(periods, assets)).astype(np.float32),
        "突破": rng.normal(size=(periods, assets)).astype(np.float32),
    }
    states = rng.normal(size=(periods, 5)).astype(np.float32)
    feedback = rng.normal(size=(periods, assets)).astype(np.float32)
    eligible = np.ones((periods, assets), dtype=bool)
    splits = ["train"] * 55 + ["valid"] * 8 + ["test"] * 7
    first, diagnostics = train_chronological_kline_ranker(
        experts, states, feedback, eligible, splits, seed=3
    )
    changed = feedback.copy()
    changed[55:] *= -1000.0
    second, _ = train_chronological_kline_ranker(
        experts, states, changed, eligible, splits, seed=3
    )
    np.testing.assert_allclose(first, second, equal_nan=True)
    assert diagnostics["validation_labels_used_for_fit"] is False
    assert diagnostics["test_labels_used_for_fit"] is False


def test_market_exposure_does_not_use_validation_or_test_labels():
    rng = np.random.default_rng(19)
    periods = 80
    state = rng.normal(size=(periods, 5))
    returns = rng.normal(scale=0.03, size=periods)
    volatility = np.full(periods, 0.18)
    splits = ["train"] * 60 + ["valid"] * 10 + ["test"] * 10
    first, diagnostics = train_chronological_market_exposure(
        state, returns, volatility, splits, minimum_history=20
    )
    changed = returns.copy()
    changed[60:] = 1000.0
    second, _ = train_chronological_market_exposure(
        state, changed, volatility, splits, minimum_history=20
    )
    np.testing.assert_allclose(first, second)
    assert diagnostics["validation_labels_used_for_fit"] is False
    assert diagnostics["test_labels_used_for_fit"] is False
