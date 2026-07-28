import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import engine


class IndustryPortfolioConstructionTests(unittest.TestCase):
    def setUp(self):
        self.columns = [f"industry_{index:02d}" for index in range(31)]
        self.index = pd.bdate_range("2020-01-02", "2020-04-30")
        rng = np.random.default_rng(31)
        self.close = pd.DataFrame(
            np.exp(np.cumsum(rng.normal(0.0002, 0.01, (len(self.index), 31)), axis=0)),
            index=self.index,
            columns=self.columns,
        )

    def test_capped_weights_are_fully_invested_and_respect_cap(self):
        raw = pd.Series(np.geomspace(1.0, 100.0, 10), index=self.columns[:10])
        weights = engine._capped_weights(raw, cap=0.15)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=12)
        self.assertLessEqual(float(weights.max()), 0.15 + 1e-12)
        self.assertTrue((weights > 0).all())

    def test_rank_buffer_reduces_rebalance_turnover(self):
        score = pd.DataFrame(index=self.index, columns=self.columns, dtype=float)
        for index, date in enumerate(self.index):
            month = date.month
            order = list(range(31))
            if month >= 2:
                order = list(range(10, 20)) + list(range(0, 10)) + list(range(20, 31))
            base = np.empty(31)
            base[order] = np.linspace(1.0, 0.0, len(self.columns))
            score.loc[date] = base
        plain = engine._targets(score, "monthly", buffer_size=0)
        buffered = engine._targets(score, "monthly", buffer_size=3)
        plain_values = list(plain.values())
        buffered_values = list(buffered.values())
        plain_turnover = float((plain_values[1] - plain_values[0]).abs().sum())
        buffered_turnover = float((buffered_values[1] - buffered_values[0]).abs().sum())
        self.assertLess(buffered_turnover, plain_turnover)

    def test_calendar_year_diagnostics_are_report_only_and_complete(self):
        index = pd.bdate_range("2020-12-28", "2021-01-08")
        simulation = pd.DataFrame(
            {
                "return": np.linspace(-0.01, 0.01, len(index)),
                "benchmark_return": np.linspace(-0.005, 0.005, len(index)),
                "turnover": np.zeros(len(index)),
            },
            index=index,
        )
        diagnostics = engine._calendar_year_metrics(simulation)
        self.assertEqual([row["year"] for row in diagnostics], [2020, 2021])
        self.assertTrue(all(row["status"] == "ok" for row in diagnostics))
        self.assertTrue(all("excess_sharpe" in row for row in diagnostics))

    def test_turnover_uses_initial_funding_then_one_way_rebalance(self):
        dates = pd.bdate_range("2020-01-02", periods=5)
        close = pd.DataFrame(100.0, index=dates, columns=self.columns)
        first = pd.Series(0.0, index=self.columns)
        first.iloc[:10] = 0.10
        second = pd.Series(0.0, index=self.columns)
        second.iloc[5:15] = 0.10
        targets = {
            dates[0]: first,
            dates[2]: second,
        }
        simulation, holdings = engine._simulate(close, targets, cost_rate=0.0)
        self.assertEqual(len(holdings), 2)
        self.assertAlmostEqual(holdings[0]["turnover"], 1.0, places=12)
        # Five names leave and five enter: half-L1 one-way turnover is 50%.
        self.assertAlmostEqual(holdings[1]["turnover"], 0.5, places=12)
        execution_turnover = simulation.loc[simulation["turnover"] > 0, "turnover"].tolist()
        self.assertEqual(len(execution_turnover), 2)
        self.assertAlmostEqual(execution_turnover[0], 1.0, places=12)
        self.assertAlmostEqual(execution_turnover[1], 0.5, places=12)

    def test_risk_weighted_targets_preserve_contract(self):
        score = pd.DataFrame(
            np.tile(np.linspace(0.0, 1.0, 31), (len(self.index), 1)),
            index=self.index,
            columns=self.columns,
        )
        targets = engine._targets(
            score,
            "monthly",
            close=self.close,
            buffer_size=3,
            risk_weighted=True,
        )
        self.assertTrue(targets)
        for target in targets.values():
            positive = target[target > 0]
            self.assertEqual(len(positive), 10)
            self.assertAlmostEqual(float(target.sum()), 1.0, places=10)
            self.assertLessEqual(float(target.max()), 0.15 + 1e-12)


    def test_risk_overlay_scales_investment_and_keeps_explicit_cash(self):
        score = pd.DataFrame(
            np.tile(np.linspace(0.0, 1.0, 31), (len(self.index), 1)),
            index=self.index,
            columns=self.columns,
        )
        zero_budget = pd.Series(0.0, index=self.index)
        with patch.object(
            engine, "_market_risk_budget", return_value=zero_budget
        ):
            targets = engine._targets(
                score,
                "monthly",
                close=self.close,
                risk_overlay=0.5,
            )
        self.assertTrue(targets)
        for target in targets.values():
            self.assertAlmostEqual(float(target.sum()), 0.5, places=12)
            self.assertEqual(int((target > 0).sum()), 10)

    def test_cash_notional_change_is_included_in_turnover(self):
        dates = pd.bdate_range("2020-01-02", periods=5)
        close = pd.DataFrame(100.0, index=dates, columns=self.columns)
        half = pd.Series(0.0, index=self.columns)
        half.iloc[:10] = 0.05
        full = pd.Series(0.0, index=self.columns)
        full.iloc[:10] = 0.10
        simulation, holdings = engine._simulate(
            close,
            {dates[0]: half, dates[2]: full},
            cost_rate=0.0,
        )
        self.assertAlmostEqual(holdings[0]["cash_weight"], 0.5, places=12)
        self.assertAlmostEqual(holdings[0]["turnover"], 0.5, places=12)
        self.assertAlmostEqual(holdings[1]["cash_weight"], 0.0, places=12)
        self.assertAlmostEqual(holdings[1]["turnover"], 0.5, places=12)
        self.assertAlmostEqual(
            float(simulation.loc[simulation["turnover"] > 0, "turnover"].sum()),
            1.0,
            places=12,
        )

    def test_sealed_test_can_only_veto_challenger_promotion(self):
        champion = {
            "test": {
                "annual_excess": 0.02,
                "excess_sharpe": 0.40,
                "max_drawdown": -0.20,
            }
        }
        dominated = {
            "test": {
                "annual_excess": 0.01,
                "excess_sharpe": 0.30,
                "max_drawdown": -0.25,
            }
        }
        dominant = {
            "test": {
                "annual_excess": 0.03,
                "excess_sharpe": 0.50,
                "max_drawdown": -0.18,
            }
        }
        rejected = engine._champion_challenger_promotion_gate(
            champion, dominated
        )
        passed = engine._champion_challenger_promotion_gate(
            champion, dominant
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse(all(rejected["checks"].values()))
        self.assertEqual(passed["status"], "passed")
        self.assertTrue(all(passed["checks"].values()))
        self.assertIn("never used to rank", passed["policy"])

    def test_common_evaluation_start_is_after_latest_first_execution(self):
        first = pd.DataFrame(
            {"return": 0.0},
            index=pd.bdate_range("2020-01-02", periods=8),
        )
        delayed = pd.DataFrame(
            {"return": 0.0},
            index=pd.bdate_range("2020-01-07", periods=5),
        )
        start = engine._common_evaluation_start([first, delayed])
        self.assertEqual(start, pd.Timestamp("2020-01-08"))
        self.assertGreater(start, first.index.min())
        self.assertGreater(start, delayed.index.min())

if __name__ == "__main__":
    unittest.main(verbosity=2)
