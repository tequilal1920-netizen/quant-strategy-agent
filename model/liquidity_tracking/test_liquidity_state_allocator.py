from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from framework.backtest.liquidity_state_allocator import (
    AllocatorConfig,
    backtest_allocator,
    backtest_monthly_cash_overlay,
    build_exposure,
    forward_compound_return,
    selection_score,
    walkforward_hierarchical_evidence_model,
)


class LiquidityStateAllocatorTest(unittest.TestCase):
    def test_forward_target_starts_after_signal_date(self) -> None:
        dates = pd.date_range("2020-01-03", periods=8, freq="W-FRI")
        returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.01, -0.02, 0.01, 0.02], index=dates)
        target = forward_compound_return(returns, 2)
        expected = (1.0 + returns.iloc[1]) * (1.0 + returns.iloc[2]) - 1.0
        self.assertAlmostEqual(float(target.iloc[0]), float(expected), places=12)

    def test_backtest_applies_signal_to_next_week(self) -> None:
        dates = pd.date_range("2020-01-03", periods=30, freq="W-FRI")
        signal = pd.Series(0.0, index=dates)
        returns = pd.Series(np.linspace(-0.01, 0.02, len(dates)), index=dates)
        config = AllocatorConfig(
            name="test",
            label="test",
            exposure_floor=0.5,
            exposure_ceiling=0.5,
            target_volatility=10.0,
            cost_bps=0.0,
        )
        result = backtest_allocator(signal, returns, config)
        self.assertAlmostEqual(
            float(result.iloc[0]["strategy_return"]),
            0.5 * float(returns.iloc[1]),
            places=12,
        )

    def test_monthly_cash_overlay_uses_next_month_returns(self) -> None:
        dates = pd.date_range("2018-01-05", periods=180, freq="W-FRI")
        signal = pd.Series(0.0, index=dates)
        returns = pd.Series(0.002 * np.sin(np.arange(len(dates)) / 4.0), index=dates)
        months = dates.to_period("M").unique()
        cash_levels = pd.Series(100.0 * np.power(1.001, np.arange(len(months))), index=months)
        config = AllocatorConfig(
            name="monthly",
            label="monthly",
            exposure_floor=0.5,
            exposure_ceiling=0.5,
            target_volatility=10.0,
            cost_bps=0.0,
            rebalance_frequency="monthly",
            defensive_asset="511880.SH",
        )
        result = backtest_monthly_cash_overlay(signal, returns, cash_levels, config)
        first = result.iloc[0]
        self.assertAlmostEqual(float(first["equity_exposure"]), 0.5, places=12)
        self.assertAlmostEqual(
            float(first["strategy_return"]),
            0.5 * float(first["benchmark_return"])
            + 0.5 * float(first["defensive_return"]),
            places=12,
        )

    def test_selection_score_does_not_read_test(self) -> None:
        splits = {
            "train": {"sharpe": 0.5, "information_ratio": 0.1},
            "valid": {
                "sharpe": 0.4,
                "information_ratio": 0.2,
                "max_drawdown": -0.1,
                "annual_turnover": 1.0,
            },
            "test": {"sharpe": -10.0, "information_ratio": -10.0},
        }
        first = selection_score(splits)
        splits["test"] = {"sharpe": 100.0, "information_ratio": 100.0}
        self.assertEqual(first, selection_score(splits))

    def test_walkforward_labels_are_mature_before_refit(self) -> None:
        dates = pd.date_range("2012-01-06", periods=260, freq="W-FRI")
        values = np.sin(np.arange(len(dates)) / 8.0)
        feature_name = "demo::短期"
        features = pd.DataFrame({feature_name: values}, index=dates)
        target = pd.Series(np.roll(values, -4), index=dates)
        target.iloc[-4:] = np.nan
        registry = [
            {
                "feature": feature_name,
                "group": "杠杆",
                "label": "演示",
                "horizon": "短期",
            }
        ]
        result = walkforward_hierarchical_evidence_model(
            features,
            registry,
            target,
            target_horizon_weeks=4,
            feature_mode="fast",
            lookback_weeks=156,
            minimum_history_weeks=104,
            refit_weeks=13,
        )
        self.assertTrue(result["weight_history"])
        for row in result["weight_history"]:
            self.assertLess(row["last_matured_signal_date"], row["refit_date"])

    def test_exposure_is_continuous_and_bounded(self) -> None:
        dates = pd.date_range("2018-01-05", periods=80, freq="W-FRI")
        signal = pd.Series(np.linspace(-4.0, 4.0, len(dates)), index=dates)
        returns = pd.Series(0.002 * np.sin(np.arange(len(dates))), index=dates)
        config = AllocatorConfig(
            name="bounded",
            label="bounded",
            exposure_floor=0.0,
            exposure_ceiling=1.0,
            crowding_penalty=0.8,
        )
        exposure = build_exposure(signal, returns, config)
        self.assertTrue(np.isfinite(exposure).all())
        self.assertGreaterEqual(float(exposure.min()), 0.0)
        self.assertLessEqual(float(exposure.max()), 1.0)
        self.assertGreater(exposure.nunique(), 20)


if __name__ == "__main__":
    unittest.main()
