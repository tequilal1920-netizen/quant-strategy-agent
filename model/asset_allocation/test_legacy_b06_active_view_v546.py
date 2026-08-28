from __future__ import annotations

import numpy as np

from legacy_b06_active_view_v546 import (
    ACTIVE_SCALE_MONTHLY_V546,
    legacy_b06_active_alpha_v546,
    legacy_b06_view_bundle_v546,
)


def history(seed: int = 546) -> np.ndarray:
    return np.random.default_rng(seed).normal(
        [.006, .003, .004, .005], [.04, .012, .03, .04], size=(36, 4)
    )


def test_alpha_is_zero_sum_bounded_and_cash_free():
    alpha, diagnostics = legacy_b06_active_alpha_v546(history())
    assert abs(float(alpha.sum())) <= 1.0e-12
    assert float(np.max(np.abs(alpha))) <= ACTIVE_SCALE_MONTHLY_V546 + 1.0e-12
    assert diagnostics["cash_semantics_removed"] is True
    assert diagnostics["selection_status"] == "not_a_new_blind_champion"


def test_equal_assets_create_zero_active_alpha_without_order_bias():
    common = np.random.default_rng(1).normal(.004, .02, size=36)
    alpha, _ = legacy_b06_active_alpha_v546(np.column_stack([common] * 4))
    # Only the fixed equity guard may distinguish equity; the three defensive
    # assets must remain exactly symmetric under identical data.
    assert alpha[1] == alpha[2] == alpha[3]
    assert abs(float(alpha.sum())) <= 1.0e-12


def test_asset_permutation_is_equivariant_except_fixed_equity_guard():
    data = history()
    alpha, _ = legacy_b06_active_alpha_v546(data)
    permutation = [0, 3, 2, 1]
    permuted, _ = legacy_b06_active_alpha_v546(data[:, permutation])
    assert np.allclose(permuted, alpha[permutation], atol=1.0e-12)


def test_bl_views_are_relative_and_finite():
    covariance = np.cov(history().T)
    prior = np.asarray([.01, .003, .004, .006])
    bundle = legacy_b06_view_bundle_v546(covariance, prior, history(), tau=.05)
    assert bundle.P.shape == (3, 4)
    assert bundle.q.shape == (3,)
    assert bundle.omega.shape == (3, 3)
    assert np.all(np.isfinite(bundle.q))
    assert np.linalg.eigvalsh(bundle.omega).min() > 0.0
