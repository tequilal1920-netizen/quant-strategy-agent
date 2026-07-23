"""Deterministic unit tests for the asset-allocation engine."""

from __future__ import annotations

import unittest

import numpy as np

from asset_allocation_engine import (
    ASSET_PROXIES,
    CYCLE_DEFINITIONS_V3,
    _active_metrics_v3,
    _factor_signals_v3,
    _trend_specs_v3,
    _posterior_specs_v4,
    _posterior_target_v4,
    _drifted_weight_v4,
    _execute_target_v4,
    PROFILE_SPECS,
    PRING_BITS_TO_PHASE,
    _specs_v2,
    _normalize_weights,
    _shrink_cov,
    hmm_forecast_covariance,
    merge_price_series,
    risk_budget_weights,
)


class EngineTests(unittest.TestCase):
    def test_pring_has_six_canonical_and_two_conflict_states(self) -> None:
        self.assertEqual(set(PRING_BITS_TO_PHASE), {"100", "110", "111", "011", "001", "000"})
        self.assertNotIn("101", PRING_BITS_TO_PHASE)
        self.assertNotIn("010", PRING_BITS_TO_PHASE)

    def test_weight_normalizer_respects_simplex_and_caps(self) -> None:
        weights = _normalize_weights([0.9, 0.08, 0.01, 0.01], floors=[0, 0, 0, 0.05], caps=[0.65] * 4)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)
        self.assertGreaterEqual(float(weights.min()), 0.0)
        self.assertLessEqual(float(weights.max()), 0.6500001)
        self.assertGreaterEqual(float(weights[3]), 0.0499999)
        ratio = weights[1] / weights[2]
        self.assertGreater(float(ratio), 5.0)

    def test_risk_budget_is_long_only_and_finite(self) -> None:
        rng = np.random.default_rng(7)
        returns = rng.normal(0, [0.05, 0.015, 0.035, 0.002], size=(96, 4))
        covariance = _shrink_cov(returns)
        weights = risk_budget_weights(covariance)
        self.assertTrue(np.all(np.isfinite(weights)))
        self.assertTrue(np.all(weights >= 0))
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)

    def test_risk_budget_changes_with_covariance(self) -> None:
        first = risk_budget_weights(np.diag([0.04, 0.01, 0.0225, 0.0001]))
        second = risk_budget_weights(np.diag([0.01, 0.04, 0.0001, 0.0225]))
        self.assertGreater(float(np.max(np.abs(first - second))), 0.05)

    def test_hmm_covariance_is_symmetric_psd(self) -> None:
        rng = np.random.default_rng(19)
        returns = rng.normal(0, [0.04, 0.012, 0.03, 0.003], size=(120, 4))
        covariance, probabilities, diagnostics = hmm_forecast_covariance(returns, iterations=8)
        self.assertTrue(np.allclose(covariance, covariance.T, atol=1e-10))
        self.assertGreaterEqual(float(np.linalg.eigvalsh(covariance).min()), -1e-10)
        self.assertAlmostEqual(sum(probabilities), 1.0, places=6)
        self.assertEqual(diagnostics["states"], 3)

    def test_current_asset_proxies_are_four_distinct_tradeable_etfs(self) -> None:
        codes = [ASSET_PROXIES[asset]["ts_code"] for asset in ("equity", "bond", "commodity", "cash")]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(code.endswith((".SH", ".SZ")) for code in codes))
        self.assertEqual(ASSET_PROXIES["bond"].get("ts_code"), "511010.SH")
        self.assertEqual(ASSET_PROXIES["commodity"].get("ts_code"), "518880.SH")
        self.assertEqual(ASSET_PROXIES["cash"].get("ts_code"), "511880.SH")
        self.assertTrue(ASSET_PROXIES["bond"].get("sina_symbol"))
        self.assertTrue(ASSET_PROXIES["cash"].get("sina_symbol"))

    def test_equity_preferred_profile_has_explicit_capital_and_risk_preference(self) -> None:
        preferred = PROFILE_SPECS["equity_preferred"]
        balanced = PROFILE_SPECS["balanced"]
        self.assertAlmostEqual(preferred["floors"][0], 0.10)
        self.assertAlmostEqual(preferred["caps"][0], 0.70)
        self.assertGreater(preferred["risk_budget"][0], balanced["risk_budget"][0])
        self.assertGreater(preferred["capital_prior"][0], balanced["capital_prior"][0])
        self.assertGreaterEqual(preferred["capital_prior"][0], 0.45)

    def test_candidate_grid_crosses_structures_estimators_and_windows(self) -> None:
        specs = _specs_v2()
        self.assertEqual(len(specs), 24)
        self.assertEqual({row["covariance_method"] for row in specs}, {"shrink", "ewma"})
        self.assertEqual({row["lookback"] for row in specs}, {24, 36})
        self.assertGreaterEqual(len({tuple(row["blend"].items()) for row in specs}), 6)

    def test_active_metrics_use_equal_weight_relative_nav(self) -> None:
        benchmark = [0.01, -0.02, 0.015, 0.005] * 6
        strategy = [value + 0.001 + (0.0002 if index % 2 else -0.0002) for index, value in enumerate(benchmark)]
        metrics = _active_metrics_v3(strategy, benchmark)
        self.assertGreater(metrics["annual_excess_return"], 0)
        self.assertGreater(metrics["information_ratio"], 0)
        self.assertGreater(metrics["total_excess_return"], 0)

    def test_trend_candidates_keep_equity_preference_anchor(self) -> None:
        specs = _trend_specs_v3()
        self.assertEqual(len(specs), 48)
        self.assertTrue(all(row["family"] == "equity_preferred_dual_momentum" for row in specs))
        self.assertTrue(all(row["prior"] == [0.35, 0.25, 0.25, 0.15] for row in specs))
        self.assertEqual({tuple(row["horizons"]) for row in specs}, {(1, 3, 6), (3, 6, 12), (6, 9, 12)})

    def test_v4_grid_is_predeclared_and_structurally_diverse(self) -> None:
        specs = _posterior_specs_v4()
        self.assertEqual(len(specs), 48)
        self.assertEqual(
            {row["family"] for row in specs},
            {"balanced_posterior", "diversified_posterior", "equity_guarded_posterior"},
        )
        self.assertEqual({tuple(row["horizons"]) for row in specs}, {(1, 3, 6), (1, 3, 6, 12)})
        self.assertTrue(all(row["prior"] == [0.45, 0.20, 0.20, 0.15] for row in specs))
        self.assertEqual({row["macro_strength"] for row in specs}, {0.0, 0.03, 0.05})

    def test_v4_posterior_weights_and_probabilities_are_valid(self) -> None:
        rng = np.random.default_rng(31)
        returns = rng.normal(0.004, [0.05, 0.018, 0.04, 0.002], size=(48, 4))
        weights, metadata = _posterior_target_v4(returns, _posterior_specs_v4()[25], "equity_preferred")
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)
        self.assertGreaterEqual(float(weights.min()), 0.049999)
        self.assertLessEqual(float(weights.max()), 0.700001)
        probabilities = list(metadata["posterior_probability"].values())
        self.assertTrue(all(0.0 <= value <= 1.0 for value in probabilities))

    def test_v4_turnover_uses_drifted_holdings(self) -> None:
        previous = np.full(4, 0.25)
        drifted = _drifted_weight_v4(previous, np.asarray([0.10, -0.05, 0.20, 0.00]))
        executed, turnover, limited = _execute_target_v4(previous, drifted, 1.0, "balanced")
        self.assertGreater(turnover, 0.0)
        self.assertFalse(limited)
        self.assertAlmostEqual(float(executed.sum()), 1.0, places=8)

    def test_factor_signal_filter_is_causal(self) -> None:
        rows = [{"month": f"2020{month:02d}", "value": value} for month, value in enumerate([0.2, -0.1, 0.4, 0.7, -0.3, 0.1], 1)]
        full = _factor_signals_v3({"x": rows})["x"]
        prefix = _factor_signals_v3({"x": rows[:4]})["x"]
        self.assertEqual(full[:4], prefix)
        self.assertTrue(all(row["signal_state"] in {-1, 1} for row in full))

    def test_each_cycle_has_ordered_complete_state_definitions(self) -> None:
        self.assertEqual(set(CYCLE_DEFINITIONS_V3), {"pring", "kitchin", "juglar", "kondratieff", "merrill"})
        self.assertEqual(len(CYCLE_DEFINITIONS_V3["pring"]["states"]), 6)
        for payload in CYCLE_DEFINITIONS_V3.values():
            states = payload["states"]
            self.assertEqual([row["order"] for row in states], list(range(1, len(states) + 1)))
            self.assertTrue(all(row["summary"] and row["asset_bias"] for row in states))

    def test_price_merge_later_source_wins(self) -> None:
        first = {"equity": [{"date": "20260102", "close": 1.0}]}
        second = {"equity": [{"date": "20260102", "close": 1.1}, {"date": "20260105", "close": 1.2}]}
        merged = merge_price_series(first, second)
        self.assertEqual([row["close"] for row in merged["equity"]], [1.1, 1.2])


if __name__ == "__main__":
    unittest.main()
