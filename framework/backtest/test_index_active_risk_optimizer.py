from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from framework.backtest.index_active_risk_optimizer import (
    ActiveRiskConfig,
    _annual_active_covariance,
    _causal_alpha_reliability,
    _tracking_error,
    add_causal_risk_features,
    optimize_weights,
)


def sample_frame() -> pd.DataFrame:
    rows = []
    for index in range(12):
        rows.append(
            {
                "trade_date": "20240131",
                "ts_code": f"{index:06d}.SZ",
                "industry_name": "A" if index < 6 else "B",
                "index_weight": 100.0 / 12.0,
                "score": index / 11.0,
                "total_mv": 100.0 + index * 50.0,
                "pb": 1.0 + index * 0.2,
                "roe": 0.05 + index * 0.01,
                "mom60": -0.10 + index * 0.02,
                "mom120": -0.15 + index * 0.025,
                "turnover_rate": 1.0 + index * 0.25,
                "active_risk_volatility": 0.18 + index * 0.005,
                "label_next_ret": -0.20 + index * 0.04,
            }
        )
    return pd.DataFrame(rows)


class ActiveRiskOptimizerTests(unittest.TestCase):
    def test_weights_preserve_benchmark_and_industry_budgets(self) -> None:
        frame = sample_frame()
        weights, diagnostics = optimize_weights(
            frame,
            "score",
            config=ActiveRiskConfig(turnover_penalty=0.0),
        )
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=10)
        for industry in ("A", "B"):
            codes = set(frame.loc[frame["industry_name"] == industry, "ts_code"])
            actual = sum(weight for code, weight in weights.items() if code in codes)
            expected = frame.loc[frame["industry_name"] == industry, "index_weight"].sum()
            expected /= frame["index_weight"].sum()
            self.assertAlmostEqual(actual, expected, places=9)
        self.assertLess(diagnostics["max_industry_deviation"], 1.0e-9)
        self.assertTrue(diagnostics["risk_history_is_causal"])

    def test_duplicate_security_rows_do_not_destroy_weight_mass(self) -> None:
        frame = sample_frame()
        duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        weights, diagnostics = optimize_weights(
            duplicated,
            "score",
            config=ActiveRiskConfig(turnover_penalty=0.0),
        )
        self.assertEqual(len(weights), frame["ts_code"].nunique())
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=10)
        self.assertLess(diagnostics["max_industry_deviation"], 1.0e-9)

    def test_causal_reliability_is_continuous_and_directional(self) -> None:
        config = ActiveRiskConfig(
            use_causal_alpha_reliability=True,
            reliability_lookback=12,
            reliability_prior_strength=6.0,
        )
        positive = _causal_alpha_reliability([0.04] * 12, config)
        negative = _causal_alpha_reliability([-0.04] * 12, config)
        weak = _causal_alpha_reliability([0.001, -0.001] * 6, config)
        self.assertGreater(positive, 0.0)
        self.assertLess(negative, 0.0)
        self.assertAlmostEqual(positive, -negative, places=10)
        self.assertLess(abs(weak), abs(positive))

    def test_next_return_never_changes_signal_date_weights(self) -> None:
        frame = sample_frame()
        changed = frame.copy()
        changed["label_next_ret"] = np.linspace(5.0, -5.0, len(changed))
        first, _ = optimize_weights(frame, "score")
        second, _ = optimize_weights(changed, "score")
        self.assertEqual(first, second)

    def test_trailing_volatility_uses_only_matured_labels(self) -> None:
        rows = []
        for index in range(18):
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": f"2023{index + 1:04d}",
                    "label_next_ret": index / 100.0,
                }
            )
        panel = pd.DataFrame(rows)
        changed = panel.copy()
        changed.loc[17, "label_next_ret"] = 99.0
        first = add_causal_risk_features(panel, lookback=12, min_periods=3)
        second = add_causal_risk_features(changed, lookback=12, min_periods=3)
        self.assertEqual(
            first.loc[17, "active_risk_volatility"],
            second.loc[17, "active_risk_volatility"],
        )

    def test_tracking_error_does_not_double_annualize_volatility(self) -> None:
        active = np.asarray([0.10, -0.10], dtype=float)
        annual_covariance = np.diag([0.20**2, 0.20**2])
        expected = float(np.sqrt(0.10**2 * 0.20**2 * 2.0))
        self.assertAlmostEqual(
            _tracking_error(active, annual_covariance), expected, places=12
        )

    def test_causal_covariance_is_psd_and_uses_only_matured_rows(self) -> None:
        matured = []
        for index in range(24):
            common = 0.01 * np.sin(index / 3.0)
            matured.append({
                "A": common + 0.002 * np.cos(index),
                "B": common - 0.002 * np.cos(index),
                "C": -0.5 * common,
            })
        config = ActiveRiskConfig(
            volatility_lookback=24,
            volatility_min_periods=12,
        )
        covariance, diagnostics = _annual_active_covariance(
            matured, ["A", "B", "C"], np.asarray([0.20, 0.20, 0.20]), config
        )
        self.assertEqual(diagnostics["method"], "ewma_newey_west_shrunk_psd")
        self.assertTrue(diagnostics["causal"])
        self.assertTrue(np.isfinite(covariance).all())
        self.assertGreaterEqual(float(np.linalg.eigvalsh(covariance).min()), -1.0e-12)
        self.assertGreater(covariance[0, 1], covariance[0, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
