from __future__ import annotations

import copy
import unittest

from model.kline_memory_learning.cross_sectional_factor_study import (
    _selection_score_result,
)


def metric(
    *,
    periods: int,
    rank_ic: float,
    excess: float,
    sharpe: float = 1.0,
    drawdown: float = -0.10,
    turnover: float = 0.25,
    monotonicity: float = 0.80,
    coverage: float = 0.95,
) -> dict:
    return {
        "periods": periods,
        "rank_ic": rank_ic,
        "excess_annual_return": excess,
        "sharpe": sharpe,
        "max_drawdown": drawdown,
        "turnover": turnover,
        "monotonicity": monotonicity,
        "coverage": coverage,
    }


class CrossSectionalPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.integrity = {
            "signal_uses_close_or_earlier": True,
            "execution_is_next_trade_open": True,
            "financials_are_visible_date_asof": True,
            "membership_is_signal_date_asof": True,
            "test_not_used_for_formula_or_direction": True,
            "boundary_crossing_labels_are_purged": True,
            "ranker_trained_without_test_labels": True,
            "factor_timing_uses_one_period_reporting_lag": True,
        }
        self.metrics = {
            "train": metric(periods=30, rank_ic=0.04, excess=0.06),
            "valid": metric(periods=10, rank_ic=0.03, excess=0.05),
            "test": metric(periods=10, rank_ic=0.02, excess=0.02),
        }

    def test_test_metrics_cannot_change_selection_score_or_pass(self) -> None:
        first = _selection_score_result(self.metrics, self.integrity, "M")
        changed = copy.deepcopy(self.metrics)
        changed["test"] = metric(
            periods=10,
            rank_ic=-0.50,
            excess=-0.90,
            sharpe=-8.0,
            drawdown=-0.95,
            turnover=1.0,
            monotonicity=-1.0,
            coverage=0.0,
        )
        second = _selection_score_result(changed, self.integrity, "M")
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(first["passed"], second["passed"])
        self.assertFalse(first["selection_uses_test"])

    def test_quarterly_two_or_three_period_result_cannot_pass(self) -> None:
        sparse = copy.deepcopy(self.metrics)
        sparse["valid"]["periods"] = 3
        result = _selection_score_result(sparse, self.integrity, "Q")
        self.assertFalse(result["checks"]["validation_periods"])
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
