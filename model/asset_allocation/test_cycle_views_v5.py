"""Tests for v5 cycle filters and BL view construction."""

from __future__ import annotations

import unittest

import numpy as np

from cycle_views_v5 import (
    P_VIEWS_V5,
    build_pring_market_probabilities_v5,
    cycle_probability_features_v5,
    fit_cycle_view_model_v5,
    forecast_cycle_views_v5,
)


def _cycle_row(index: int) -> dict:
    pring = {str(i): 0.7 if i == (index % 6) + 1 else 0.06 for i in range(1, 7)}
    total = sum(pring.values())
    pring = {key: value / total for key, value in pring.items()}
    return {
        "month": f"{2018 + index // 12:04d}{index % 12 + 1:02d}",
        "cycles": {
            "pring": {"probabilities": pring, "eligible_for_views": True},
            "kitchin": {"probabilities": {"被动去库": 0.4, "主动补库": 0.3, "被动补库": 0.2, "主动去库": 0.1}, "eligible_for_views": True},
            "juglar": {"probabilities": {"修复期": 0.1, "繁荣早期": 0.2, "繁荣晚期": 0.3, "出清期": 0.4}, "eligible_for_views": True},
            "merrill": {"probabilities": {"再通胀/衰退": 0.25, "复苏": 0.35, "过热": 0.25, "滞涨": 0.15}, "eligible_for_views": True},
            "kondratieff": {"probabilities": {"回升": 0.8, "繁荣": 0.1, "衰退": 0.05, "萧条": 0.05}, "eligible_for_views": False},
        },
    }


class CycleViewsV5Tests(unittest.TestCase):
    def test_pring_uses_bond_equity_commodity_but_not_gold(self) -> None:
        rng = np.random.default_rng(20260811)
        months = [f"{2018 + index // 12:04d}{index % 12 + 1:02d}" for index in range(60)]
        returns = rng.normal(0.003, [0.04, 0.015, 0.03, 0.035], size=(60, 4))
        first = build_pring_market_probabilities_v5(months, returns, train_end="202012")
        changed = returns.copy()
        changed[:, 2] = rng.normal(0.20, 0.30, size=60)
        second = build_pring_market_probabilities_v5(months, changed, train_end="202012")
        for left, right in zip(first, second):
            self.assertEqual(left["probabilities"], right["probabilities"])
            self.assertEqual(left["state"], right["state"])
            self.assertAlmostEqual(sum(left["probabilities"].values()), 1.0, places=12)

    def test_probability_features_disable_kondratieff(self) -> None:
        history = [_cycle_row(index) for index in range(4)]
        names, matrix, slices = cycle_probability_features_v5(history)
        self.assertEqual(matrix.shape[0], 4)
        self.assertTrue(np.allclose(matrix[:, slices["kondratieff"]], 0.0))
        self.assertTrue(any(name.startswith("pring:") for name in names))

    def test_joint_view_model_has_psd_full_omega_and_zero_kondratieff(self) -> None:
        rng = np.random.default_rng(73)
        history = [_cycle_row(index) for index in range(72)]
        returns = rng.normal(0.003, [0.04, 0.015, 0.025, 0.035], size=(72, 4))
        fitted = fit_cycle_view_model_v5(
            returns,
            history,
            train_mask=[index < 60 for index in range(72)],
            minimum_train=24,
        )
        self.assertEqual(fitted["status"], "ok")
        omega = fitted["omega"]
        self.assertTrue(np.allclose(omega, omega.T, atol=1.0e-12))
        self.assertGreaterEqual(float(np.linalg.eigvalsh(omega).min()), -1.0e-12)
        bundle = forecast_cycle_views_v5(fitted, [0.01, 0.005, 0.007, 0.008], history[-1])
        self.assertEqual(bundle.P.shape, (3, 4))
        np.testing.assert_allclose(bundle.P, P_VIEWS_V5)
        np.testing.assert_allclose(bundle.cycle_contributions["kondratieff"], np.zeros(3))
        self.assertEqual(bundle.diagnostics["kondratieff_status"], "display_only_insufficient_independent_cycles")


if __name__ == "__main__":
    unittest.main()
