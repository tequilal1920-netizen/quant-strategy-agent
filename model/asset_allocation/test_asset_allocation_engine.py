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
    _promotion_gate_v4,
    _objective_champions_v5,
    _architecture_comparison_v5,
    _cash_hurdle_metrics_v5,
    _macro_factor_risk_audit_v5,
    _causal_portfolio_volatility_budget_v4,
    _posterior_specs_v4,
    _posterior_target_v4,
    _drifted_weight_v4,
    _execute_target_v4,
    _metrics,
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
        expected = 0.5 * float(np.abs(previous - drifted).sum())
        self.assertAlmostEqual(turnover, expected, places=12)

        _, limited_turnover, limited = _execute_target_v4(previous, drifted, expected, "balanced")
        self.assertTrue(limited)
        self.assertLessEqual(limited_turnover, expected / 2.0 + 1e-12)

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

    def test_sharpe_uses_arithmetic_mean_return_not_cagr(self) -> None:
        returns = np.asarray([0.02, -0.01, 0.03, -0.02] * 6, dtype=float)
        metrics = _metrics(returns)
        expected = float(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(12))
        legacy = metrics["annual_return"] / metrics["annual_volatility"]
        self.assertAlmostEqual(metrics["sharpe"], expected, places=12)
        self.assertNotAlmostEqual(metrics["sharpe"], legacy, places=6)
        self.assertAlmostEqual(metrics["total_return"], float(np.prod(1.0 + returns) - 1.0), places=12)

    def test_v4_promotion_requires_multiple_testing_adjusted_evidence(self) -> None:
        selected = {
            "train_active": {"annual_excess_return": 0.01},
            "validation": {"annual_return": 0.001},
            "validation_active": {
                "annual_excess_return": 0.01,
                "information_ratio": 0.3,
                "max_relative_drawdown": -0.02,
            },
        }
        audit = {
            "pbo_cscv": 0.30,
            "deflated_sharpe_probability": 0.56,
        }
        conditional = _promotion_gate_v4(selected, audit)
        self.assertEqual(conditional["status"], "conditional")
        self.assertIn(
            "deflated_sharpe_probability_at_least_95pct",
            conditional["failed"],
        )
        audit["deflated_sharpe_probability"] = 0.96
        passed = _promotion_gate_v4(selected, audit)
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(passed["failed"], [])

    def test_v4_portfolio_volatility_budget_is_causal_and_bounded(self) -> None:
        rng = np.random.default_rng(20260726)
        window = rng.normal(
            loc=[0.005, 0.002, 0.003, 0.0002],
            scale=[0.12, 0.04, 0.10, 0.002],
            size=(48, 4),
        )
        weight = np.asarray([0.55, 0.15, 0.20, 0.10])
        spec = {"portfolio_volatility_target": 0.08}
        adjusted, report = _causal_portfolio_volatility_budget_v4(
            weight, window, spec, "equity_preferred"
        )
        self.assertAlmostEqual(float(adjusted.sum()), 1.0, places=10)
        self.assertTrue(np.all(adjusted >= np.asarray([0.10, 0.05, 0.05, 0.05]) - 1e-12))
        self.assertTrue(np.all(adjusted <= np.asarray([0.70, 0.70, 0.60, 0.60]) + 1e-12))
        self.assertLess(report["risk_scale"], 1.0)
        self.assertLess(
            report["post_budget_forecast_volatility"],
            report["pre_budget_forecast_volatility"],
        )
        adjusted_again, _ = _causal_portfolio_volatility_budget_v4(
            weight, window.copy(), spec, "equity_preferred"
        )
        np.testing.assert_allclose(adjusted, adjusted_again)


    def test_cash_hurdle_metrics_do_not_reward_cash_beta(self) -> None:
        cash = np.asarray([0.002, 0.0015, 0.0022, 0.0018] * 6)
        portfolio = cash + np.asarray([0.001, -0.0005, 0.0012, -0.0002] * 6)
        report = _cash_hurdle_metrics_v5(portfolio, cash)
        self.assertGreater(report["annual_excess_return"], 0.0)
        self.assertGreater(report["cash_excess_sharpe"], 0.0)
        with self.assertRaisesRegex(ValueError, "cash_hurdle_length_mismatch"):
            _cash_hurdle_metrics_v5(portfolio[:-1], cash)

    def test_macro_factor_risk_audit_is_finite_and_complete(self) -> None:
        rng = np.random.default_rng(46)
        returns = rng.normal(0.002, [0.04, 0.015, 0.03, 0.002], size=(60, 4))
        cycles = [
            {
                "growth_score": np.sin(index / 7),
                "inflation_score": np.cos(index / 9),
                "liquidity_score": np.sin(index / 11),
                "credit_score": np.cos(index / 13),
            }
            for index in range(60)
        ]
        audit = _macro_factor_risk_audit_v5(returns, cycles, [0.35, 0.30, 0.20, 0.15])
        self.assertEqual(audit["status"], "ok")
        self.assertEqual([row["factor"] for row in audit["factors"]], ["增长", "通胀", "流动性", "信用"])
        self.assertTrue(all(np.isfinite(row["total_risk_share"]) for row in audit["factors"]))

    def test_architecture_comparison_requires_train_and_validation_evidence(self) -> None:
        def strategy(train_excess: float, validation_excess: float) -> dict:
            metrics = {
                split: {"annual_return": 0.02, "sharpe": 1.0}
                for split in ("train", "validation", "test")
            }
            active = {
                "train": {"annual_excess_return": train_excess},
                "validation": {"annual_excess_return": validation_excess},
                "test": {"annual_excess_return": 0.01},
            }
            cash = {
                split: {"cash_excess_sharpe": 0.5}
                for split in ("train", "validation", "test")
            }
            return {
                "metrics": {"average_annual_turnover": 0.2},
                "metrics_by_split": metrics,
                "active_metrics_by_split": active,
                "cash_hurdle_metrics_by_split": cash,
            }
        rows = _architecture_comparison_v5({"strategies": {
            "recommended": strategy(0.01, 0.01),
            "hrp": strategy(-0.01, 0.02),
        }})
        self.assertEqual(rows[0]["id"], "recommended")
        self.assertTrue(rows[0]["validation_gate"])
        self.assertTrue(rows[0]["cash_hurdle_gate"])
        self.assertTrue(rows[0]["evidence_gate"])
        self.assertFalse(rows[1]["train_gate"])

        weak_cash = strategy(0.01, 0.01)
        weak_cash["cash_hurdle_metrics_by_split"]["validation"]["cash_excess_sharpe"] = -0.1
        weak_row = _architecture_comparison_v5({"strategies": {"recommended": weak_cash}})[0]
        self.assertTrue(weak_row["validation_gate"])
        self.assertFalse(weak_row["cash_hurdle_gate"])
        self.assertFalse(weak_row["evidence_gate"])

    def test_objective_champion_uses_train_validation_floor_not_test(self) -> None:
        def strategy(train_sharpe: float, validation_sharpe: float, test_sharpe: float) -> dict:
            metrics = {
                "train": {"annual_return": 0.03, "sharpe": train_sharpe},
                "validation": {"annual_return": 0.03, "sharpe": validation_sharpe},
                "test": {"annual_return": 0.03, "sharpe": test_sharpe},
            }
            active = {
                split: {"annual_excess_return": 0.01}
                for split in ("train", "validation", "test")
            }
            cash = {
                split: {"cash_excess_sharpe": 0.2}
                for split in ("train", "validation", "test")
            }
            return {
                "metrics": {"average_annual_turnover": 0.2},
                "metrics_by_split": metrics,
                "active_metrics_by_split": active,
                "cash_hurdle_metrics_by_split": cash,
            }

        backtest = {
            "strategies": {
                "recommended": strategy(1.0, 0.1, 9.0),
                "hrp": strategy(0.8, 0.7, -4.0),
                "risk_parity": strategy(0.9, 0.6, 12.0),
            }
        }
        backtest["architecture_comparison"] = _architecture_comparison_v5(backtest)
        champions = _objective_champions_v5(backtest)
        stable = champions["stable_absolute"]
        self.assertEqual(stable["strategy"], "hrp")
        self.assertAlmostEqual(stable["conservative_sharpe"], 0.7)
        self.assertFalse(stable["selection_uses_test"])
        self.assertEqual(stable["test_sharpe_report_only"], -4.0)

    def test_price_merge_later_source_wins(self) -> None:
        first = {"equity": [{"date": "20260102", "close": 1.0}]}
        second = {"equity": [{"date": "20260102", "close": 1.1}, {"date": "20260105", "close": 1.2}]}
        merged = merge_price_series(first, second)
        self.assertEqual([row["close"] for row in merged["equity"]], [1.1, 1.2])


if __name__ == "__main__":
    unittest.main()
