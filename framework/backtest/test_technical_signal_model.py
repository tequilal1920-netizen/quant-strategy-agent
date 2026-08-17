import numpy as np

from framework.backtest.technical_signal_model import (
    build_technical_signal_families,
    calibrate_single_asset_timing,
    combine_signal_families,
    learn_family_weights_full_history,
    learn_family_weights_train_only,
    technical_family_diagnostics,
    technical_family_diagnostics_full_history,
)


def _sample_ohlcv(periods=90, assets=80):
    rng = np.random.default_rng(20260817)
    close = 20.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.018, size=(periods, assets)), axis=0))
    open_price = close * np.exp(rng.normal(0.0, 0.004, size=close.shape))
    high = np.maximum(open_price, close) * (1.0 + rng.random(close.shape) * 0.018)
    low = np.minimum(open_price, close) * (1.0 - rng.random(close.shape) * 0.018)
    volume = rng.lognormal(13.0, 0.7, size=close.shape)
    amount = volume * close
    eligible = np.ones_like(close, dtype=bool)
    return close, open_price, high, low, volume, amount, eligible


def test_technical_families_are_causal_with_respect_to_future_rows():
    close, open_price, high, low, volume, amount, eligible = _sample_ohlcv()
    signal_indices = np.arange(24, 86, 5)
    first = build_technical_signal_families(
        close, open_price, high, low, volume, amount, signal_indices, eligible
    )

    changed = [array.copy() for array in (close, open_price, high, low, volume, amount)]
    for array in changed:
        array[70:] *= 3.0
    second = build_technical_signal_families(*changed, signal_indices, eligible)

    stable_rows = np.flatnonzero(signal_indices < 70)
    assert set(first) == {"趋势动量", "突破确认", "回撤反转", "量价确认", "波动质量", "防守择时"}
    for name in first:
        np.testing.assert_allclose(first[name][stable_rows], second[name][stable_rows], equal_nan=True)


def test_family_weights_ignore_validation_and_test_labels():
    rng = np.random.default_rng(17)
    periods, assets = 48, 70
    families = {
        "趋势动量": rng.random((periods, assets)),
        "突破确认": rng.random((periods, assets)),
        "回撤反转": rng.random((periods, assets)),
    }
    eligible = np.ones((periods, assets), dtype=bool)
    forward = rng.normal(0.0, 0.02, size=(periods, assets))
    split = ["train"] * 24 + ["valid"] * 12 + ["test"] * 12

    base = learn_family_weights_train_only(families, forward, eligible, split)
    changed = forward.copy()
    changed[24:] = rng.normal(1.0, 2.0, size=changed[24:].shape)
    mutated = learn_family_weights_train_only(families, changed, eligible, split)

    assert base["validation_labels_used_for_fit"] is False
    assert base["test_labels_used_for_fit"] is False
    assert base["weights"] == mutated["weights"]


def test_combined_signal_and_diagnostics_use_all_families():
    rng = np.random.default_rng(21)
    periods, assets = 36, 55
    families = {
        "趋势动量": rng.random((periods, assets)),
        "突破确认": rng.random((periods, assets)),
        "波动质量": rng.random((periods, assets)),
    }
    eligible = np.ones((periods, assets), dtype=bool)
    forward = rng.normal(0.0, 0.02, size=(periods, assets))
    split = ["train"] * 18 + ["valid"] * 9 + ["test"] * 9
    weights = learn_family_weights_train_only(families, forward, eligible, split)["weights"]
    combined = combine_signal_families(families, eligible, weights)
    rows = technical_family_diagnostics(families, forward, eligible, split)

    assert combined.shape == (periods, assets)
    assert np.nanmin(combined) >= 0.0
    assert np.nanmax(combined) <= 1.0
    assert [row["family"] for row in rows] == list(families)
    assert all("test_rank_ic" in row for row in rows)


def test_full_history_weights_use_all_matured_labels_without_sample_split():
    rng = np.random.default_rng(38)
    periods, assets = 40, 64
    families = {
        "趋势动量": rng.random((periods, assets)),
        "突破确认": rng.random((periods, assets)),
        "回撤反转": rng.random((periods, assets)),
    }
    eligible = np.ones((periods, assets), dtype=bool)
    forward = rng.normal(0.0, 0.02, size=(periods, assets))

    fitted = learn_family_weights_full_history(families, forward, eligible)
    diagnostics = technical_family_diagnostics_full_history(families, forward, eligible)

    assert fitted["sample_split_used"] is False
    assert fitted["holdout_validation_claimed"] is False
    assert fitted["all_matured_history_used_for_fit"] is True
    assert abs(sum(fitted["weights"].values()) - 1.0) < 1e-9
    assert [row["family"] for row in diagnostics] == list(families)
    assert all(row["full_periods"] > 0 for row in diagnostics)


def test_single_asset_timing_calibration_ignores_test_returns():
    rng = np.random.default_rng(19)
    score = rng.random(90)
    realized = np.where(score > 0.62, 0.01, -0.002) + rng.normal(0.0, 0.01, size=90)
    split = ["train"] * 45 + ["valid"] * 20 + ["test"] * 25

    base = calibrate_single_asset_timing(score, realized, split)
    changed = realized.copy()
    changed[65:] = rng.normal(0.25, 0.10, size=len(changed[65:]))
    mutated = calibrate_single_asset_timing(score, changed, split)

    assert base["test_labels_used_for_fit"] is False
    assert base["entry_threshold"] == mutated["entry_threshold"]
    assert base["exit_threshold"] == mutated["exit_threshold"]
