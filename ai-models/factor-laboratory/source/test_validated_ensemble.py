from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import validated_ensemble as ensemble
import effective_dsr
import core
from adaptive_icir import causal_rolling_icir_scores


class ValidatedEnsembleMetricsTests(unittest.TestCase):
    def test_period_sharpe_uses_arithmetic_mean_not_cagr(self) -> None:
        period_returns = np.asarray([0.02, -0.01, 0.03, -0.02] * 6, dtype=float)
        rows = [{"net": float(value)} for value in period_returns]
        metrics = ensemble._period_metrics(rows, horizon=1)
        expected = float(np.mean(period_returns) / np.std(period_returns, ddof=1) * math.sqrt(252.0))
        legacy = metrics["return"] / metrics["volatility"]
        self.assertAlmostEqual(metrics["sharpe"], expected, places=12)
        self.assertNotAlmostEqual(metrics["sharpe"], legacy, places=6)

    def test_cross_section_cost_uses_previous_rebalance_not_previous_day(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        for date_index in range(6):
            for asset_index, asset in enumerate(assets):
                score = float(asset_index)
                if date_index >= 2:
                    score = -score
                target = 0.02 if score >= 36 else (-0.02 if score <= 3 else 0.0)
                rows.append({
                    "trade_date": f"2024-01-{date_index + 2:02d}",
                    "ts_code": asset,
                    "score": score,
                    "target": target,
                })
        result = core.backtest_cross_section(
            pd.DataFrame(rows), "score", "target", cost_bps=10, horizon=2
        )
        rebalances = [row for row in result["series"] if row["is_rebalance"]]
        self.assertEqual(result["observations"], 3)
        self.assertEqual(len(rebalances), 3)
        self.assertEqual(rebalances[0]["turnover"], 2.0)
        self.assertEqual(rebalances[1]["turnover"], 2.0)
        self.assertEqual(rebalances[2]["turnover"], 0.0)
        self.assertAlmostEqual(rebalances[0]["net"], rebalances[0]["gross"] - 0.002)

    def test_period_metrics_respects_explicit_rebalance_rows(self) -> None:
        rows = [
            {"net": 0.01, "is_rebalance": True},
            {"net": 0.99, "is_rebalance": False},
            {"net": -0.01, "is_rebalance": True},
        ]
        metrics = ensemble._period_metrics(rows, horizon=2)
        self.assertAlmostEqual(metrics["return"], (1.01 * 0.99) ** (126 / 2) - 1)

    def test_causal_volatility_budget_uses_only_prior_rebalances(self) -> None:
        policy = {
            "target_volatility": 0.10,
            "minimum_history": 4,
            "fast_window": 4,
            "slow_window": 8,
        }
        prior = [0.04, -0.04, 0.05, -0.05]
        before = core.causal_volatility_scale(prior, 252.0, 1.0, policy)
        after_large_current_return = core.causal_volatility_scale(
            prior, 252.0, 1.0, policy
        )
        self.assertAlmostEqual(before, after_large_current_return)
        self.assertLess(before, 1.0)
        self.assertGreaterEqual(before, policy.get("minimum_scale", 0.25))

    def test_risk_budget_turnover_includes_notional_change(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        spreads = [0.04, -0.04, 0.05, -0.05, 0.04, -0.04, 0.05, -0.05]
        for date_index, spread in enumerate(spreads):
            for asset_index, asset in enumerate(assets):
                score = float(asset_index)
                target = spread if asset_index >= 36 else (-spread if asset_index <= 3 else 0.0)
                rows.append({
                    "trade_date": f"2024-01-{date_index + 2:02d}",
                    "ts_code": asset,
                    "score": score,
                    "target": target,
                })
        result = core.backtest_cross_section(
            pd.DataFrame(rows),
            "score",
            "target",
            cost_bps=10,
            horizon=1,
            risk_budget={
                "target_volatility": 0.10,
                "minimum_history": 4,
                "fast_window": 4,
                "slow_window": 8,
            },
        )
        rebalances = [row for row in result["series"] if row["is_rebalance"]]
        self.assertEqual(rebalances[0]["turnover"], 2.0)
        self.assertLess(rebalances[-1]["risk_scale"], 1.0)
        self.assertGreater(rebalances[5]["turnover"], 0.0)
        self.assertLess(result["average_risk_scale"], 1.0)

    def test_forward_return_enters_risk_budget_only_after_it_is_observable(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        for date_index in range(17):
            for asset_index, asset in enumerate(assets):
                score = float(asset_index)
                spread = 0.20 if date_index == 0 else 0.01
                target = (
                    spread if asset_index >= 36
                    else (-spread if asset_index <= 3 else 0.0)
                )
                rows.append({
                    "trade_date": f"2024-04-{date_index + 1:02d}",
                    "ts_code": asset,
                    "score": score,
                    "target": target,
                })
        result = core.backtest_cross_section(
            pd.DataFrame(rows),
            "score",
            "target",
            cost_bps=0,
            horizon=5,
            risk_budget={
                "target_volatility": 0.10,
                "minimum_history": 1,
                "fast_window": 2,
                "slow_window": 2,
            },
        )
        rebalances = [row for row in result["series"] if row["is_rebalance"]]
        self.assertEqual(len(rebalances), 4)
        self.assertAlmostEqual(rebalances[0]["risk_scale"], 1.0)
        self.assertAlmostEqual(rebalances[1]["risk_scale"], 1.0)
        self.assertAlmostEqual(rebalances[2]["risk_scale"], 1.0)
        self.assertLess(rebalances[3]["risk_scale"], 1.0)
        self.assertEqual(result["risk_return_observation_lag"], 6)

    def test_cost_aware_convex_sleeve_reduces_trade_without_changing_notional(self) -> None:
        desired = {"A": 0.7, "B": 0.2, "C": 0.1}
        previous = {"A": 0.5, "B": 0.3, "C": 0.2}
        optimized = core.cost_aware_sleeve_weights(
            desired,
            previous,
            cost_bps=15,
            rank_return_slope=0.01,
            raw_rank_sum=1.0,
        )
        desired_turnover = core._sleeve_turnover(desired, previous)
        optimized_turnover = core._sleeve_turnover(optimized, previous)
        self.assertAlmostEqual(sum(optimized.values()), 1.0, places=12)
        self.assertLess(optimized_turnover, desired_turnover)
        zero_cost = core.cost_aware_sleeve_weights(
            desired, previous, cost_bps=0,
            rank_return_slope=0.01, raw_rank_sum=1.0,
        )
        self.assertEqual(zero_cost, desired)

    def test_cost_aware_execution_forces_exit_from_untradable_names(self) -> None:
        rows = []
        first_assets = [f"S{i:02d}" for i in range(40)]
        second_assets = [f"S{i:02d}" for i in range(39)] + ["S40"]
        for date_index, assets in enumerate((first_assets, second_assets)):
            for asset_index, asset in enumerate(assets):
                rows.append({
                    "trade_date": f"2024-05-{date_index + 1:02d}",
                    "ts_code": asset,
                    "score": float(asset_index),
                    "target": (asset_index - 19.5) / 1000.0,
                })
        result = core.backtest_cross_section(
            pd.DataFrame(rows),
            "score",
            "target",
            cost_bps=15,
            horizon=1,
            portfolio_construction="continuous_rank",
            transaction_cost_optimized=True,
            rank_return_slope=0.01,
        )
        rebalances = [row for row in result["series"] if row["is_rebalance"]]
        self.assertEqual(len(rebalances), 2)
        self.assertGreater(rebalances[1]["turnover"], 0.0)
        self.assertTrue(np.isfinite(rebalances[1]["net"]))

    def test_adaptive_cost_slope_waits_for_matured_forward_returns(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        for date_index in range(17):
            for asset_index, asset in enumerate(assets):
                centered = (asset_index + 1) / 40.0 - 0.5
                multiplier = 0.08 if date_index == 0 else 0.01
                rows.append({
                    "trade_date": f"2024-06-{date_index + 1:02d}",
                    "ts_code": asset,
                    "score": float(asset_index),
                    "target": multiplier * centered,
                })
        result = core.backtest_cross_section(
            pd.DataFrame(rows),
            "score",
            "target",
            cost_bps=15,
            horizon=5,
            portfolio_construction="continuous_rank",
            transaction_cost_optimized=True,
            rank_return_slope=0.01,
            adaptive_rank_return_slope=True,
        )
        rebalances = [row for row in result["series"] if row["is_rebalance"]]
        self.assertEqual(len(rebalances), 4)
        self.assertAlmostEqual(
            rebalances[0]["cost_aware_rank_slope"], 0.01
        )
        self.assertAlmostEqual(
            rebalances[1]["cost_aware_rank_slope"], 0.01
        )
        self.assertGreater(
            rebalances[2]["cost_aware_rank_slope"], 0.01
        )
        self.assertGreater(
            result["average_cost_aware_rank_slope"], 0.01
        )

    def test_inverse_volatility_rank_execution_reallocates_risk_causally(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        for date_index in range(4):
            for asset_index, asset in enumerate(assets):
                score = float(asset_index)
                is_low_risk_winner = asset_index >= 30
                target = (
                    0.02 if is_low_risk_winner
                    else (-0.01 if asset_index <= 9 else 0.0)
                )
                rows.append({
                    "trade_date": f"2024-07-{date_index + 1:02d}",
                    "ts_code": asset,
                    "score": score,
                    "target": target,
                    "vol_20": 0.005 if is_low_risk_winner else 0.02,
                })
        frame = pd.DataFrame(rows)
        standard = core.backtest_cross_section(
            frame,
            "score",
            "target",
            cost_bps=0,
            horizon=1,
            portfolio_construction="continuous_rank",
        )
        risk_weighted = core.backtest_cross_section(
            frame,
            "score",
            "target",
            cost_bps=0,
            horizon=1,
            portfolio_construction="continuous_rank",
            asset_risk_weighted=True,
            risk_col="vol_20",
        )
        self.assertGreater(
            risk_weighted["annual_return"], standard["annual_return"]
        )
        self.assertTrue(risk_weighted["asset_risk_weighted"])
        self.assertGreater(
            risk_weighted["average_long_effective_names"], 1.0
        )

    def test_split_may_start_before_its_first_rebalance(self) -> None:
        rows = []
        dates = [f"2024-08-{index + 1:02d}" for index in range(8)]
        assets = [f"S{i:02d}" for i in range(40)]
        for date in dates:
            for asset_index, asset in enumerate(assets):
                rows.append({
                    "trade_date": date,
                    "ts_code": asset,
                    "score": float(asset_index),
                    "target": (asset_index - 19.5) / 1000.0,
                    "vol_20": 0.01 + asset_index / 10000.0,
                })
        original_positions = dict(core._TRADING_DATE_POSITION)
        try:
            core._TRADING_DATE_POSITION = {
                date: index + 3 for index, date in enumerate(dates)
            }
            result = core.backtest_cross_section(
                pd.DataFrame(rows),
                "score",
                "target",
                cost_bps=15,
                horizon=5,
                portfolio_construction="continuous_rank",
                asset_risk_weighted=True,
                risk_col="vol_20",
            )
        finally:
            core._TRADING_DATE_POSITION = original_positions
        self.assertFalse(result["series"][0]["is_rebalance"])
        self.assertEqual(result["series"][0]["long_effective_names"], 0.0)
        self.assertGreater(result["observations"], 0)

    def test_gate_reports_actual_turnover_with_upper_bound(self) -> None:
        gates = ensemble.gate_results(
            {"valid": {"rank_ic": 0.04}, "test": {"turnover": 0.70}},
            trials=1,
        )
        turnover = next(item for item in gates if item["gate"] == "turnover")
        self.assertEqual(turnover["comparison"], "le")
        self.assertAlmostEqual(turnover["observed"], 0.70)
        self.assertAlmostEqual(turnover["threshold"], 0.65)
        self.assertFalse(turnover["passed"])

    def test_core_drawdown_gate_uses_signed_drawdown(self) -> None:
        gates = core.gate_results({"test": {"max_drawdown": -0.30}}, trials=1)
        drawdown = next(item for item in gates if item["gate"] == "drawdown")
        self.assertAlmostEqual(drawdown["observed"], -0.30)
        self.assertAlmostEqual(drawdown["threshold"], -0.25)
        self.assertFalse(drawdown["passed"])

    def test_rank_buffer_reduces_boundary_churn(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        for date_index in range(6):
            for asset_index, asset in enumerate(assets):
                score = float(asset_index)
                if date_index % 2 and asset in {"S35", "S36"}:
                    score = 36.1 if asset == "S35" else 34.9
                target = 0.01 if asset_index >= 35 else (-0.01 if asset_index <= 4 else 0.0)
                rows.append({"trade_date": f"2024-02-{date_index + 1:02d}", "ts_code": asset, "score": score, "target": target})
        frame = pd.DataFrame(rows)
        plain = core.backtest_cross_section(frame, "score", "target", 10, 1)
        buffered = core.backtest_cross_section(frame, "score", "target", 10, 1, selection_buffer=0.5)
        self.assertLess(buffered["turnover"], plain["turnover"])

    def test_strategy_trial_ledger_counts_every_execution_candidate(self) -> None:
        candidates = {
            f"model_{model_index}::policy_{policy_index}": {}
            for model_index in range(9)
            for policy_index in range(4)
        }
        result = {
            "models": {"execution_candidates": candidates},
            "selection": {"candidate_count": 4},
        }
        self.assertEqual(effective_dsr.strategy_trial_count(result), 36)

    def test_continuous_rank_execution_converts_monotonic_ic_with_less_churn(self) -> None:
        rows = []
        assets = [f"S{i:02d}" for i in range(40)]
        for date_index in range(6):
            for asset_index, asset in enumerate(assets):
                score = float(asset_index)
                if date_index % 2 and asset in {"S35", "S36"}:
                    score = 36.1 if asset == "S35" else 34.9
                target = (asset_index - 19.5) / 1000.0
                rows.append({
                    "trade_date": f"2024-03-{date_index + 1:02d}",
                    "ts_code": asset,
                    "score": score,
                    "target": target,
                })
        frame = pd.DataFrame(rows)
        hard = core.backtest_cross_section(
            frame, "score", "target", 10, 1
        )
        continuous = core.backtest_cross_section(
            frame,
            "score",
            "target",
            10,
            1,
            portfolio_construction="continuous_rank",
        )
        self.assertGreater(continuous["annual_return"], 0.0)
        self.assertLess(continuous["turnover"], hard["turnover"])
        self.assertEqual(
            continuous["portfolio_construction"], "continuous_rank"
        )

    def test_score_neutralization_removes_industry_and_size_exposure(self) -> None:
        rows = []
        for date in ("20240102", "20240103"):
            for index in range(40):
                industry = "A" if index < 20 else "B"
                size = 8.0 + index / 10.0
                score = 2.0 * size + (5.0 if industry == "B" else -5.0)
                rows.append({
                    "trade_date": date,
                    "ts_code": f"S{index:02d}",
                    "industry_name": industry,
                    "log_mv": size,
                    "score": score,
                })
        frame = pd.DataFrame(rows)
        residual = core.neutralize_cross_sectional_scores(frame)
        frame["residual"] = residual
        for _, group in frame.groupby("trade_date"):
            centered_size = group["log_mv"] - group["log_mv"].mean()
            size_covariance = float(np.mean(group["residual"] * centered_size))
            self.assertLess(abs(size_covariance), 1e-10)
            means = group.groupby("industry_name")["residual"].mean()
            self.assertTrue(np.all(np.abs(means.to_numpy()) < 1e-8))

    def test_adaptive_icir_is_causal_and_recovers_persistent_factor(self) -> None:
        rng = np.random.default_rng(20260726)
        date_count = 90
        asset_count = 48
        feature_count = 4
        features = rng.normal(
            size=(date_count, asset_count, feature_count)
        )
        targets = (
            0.04 * features[:, :, 0]
            + rng.normal(scale=0.01, size=(date_count, asset_count))
        )
        valid = np.ones((date_count, asset_count), dtype=bool)
        scores_a, report = causal_rolling_icir_scores(
            features,
            targets,
            valid,
            horizon=5,
            lookback_periods=12,
            min_periods=6,
        )
        changed_features = features.copy()
        changed_targets = targets.copy()
        changed_features[60:] = rng.normal(
            loc=100.0,
            scale=20.0,
            size=changed_features[60:].shape,
        )
        changed_targets[60:] *= -100.0
        scores_b, _ = causal_rolling_icir_scores(
            changed_features,
            changed_targets,
            valid,
            horizon=5,
            lookback_periods=12,
            min_periods=6,
        )
        np.testing.assert_allclose(
            scores_a[:60], scores_b[:60], equal_nan=True
        )
        active = np.isfinite(scores_a[55])
        self.assertGreater(int(active.sum()), 30)
        correlation = np.corrcoef(scores_a[55, active], features[55, active, 0])[0, 1]
        self.assertGreater(correlation, 0.50)
        self.assertEqual(report["test_usage"], "never_used_for_weight_calibration_or_candidate_selection")

    def test_fixed_rank_ensemble_is_scale_invariant_and_date_local(self) -> None:
        dates = np.array(
            ["20200101"] * 4 + ["20200102"] * 4
        )
        first = np.array([1.0, 2.0, 3.0, 4.0, 8.0, 7.0, 6.0, 5.0])
        second = np.array([4.0, 1.0, 3.0, 2.0, 5.0, 6.0, 8.0, 7.0])
        baseline = core.cross_sectional_rank_ensemble(
            [first, second], dates
        )
        rescaled = core.cross_sectional_rank_ensemble(
            [first * 1000.0 + 7.0, second * 0.01 - 5.0],
            dates,
        )
        np.testing.assert_allclose(baseline, rescaled)
        changed = second.copy()
        changed[4:] = changed[4:][::-1]
        perturbed = core.cross_sectional_rank_ensemble(
            [first, changed], dates
        )
        np.testing.assert_allclose(baseline[:4], perturbed[:4])


if __name__ == "__main__":
    unittest.main(verbosity=2)
