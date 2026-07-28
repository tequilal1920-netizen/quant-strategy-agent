from __future__ import annotations

import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from single_stock_analyzer import (
    NoDegradationGuard,
    POSITION_LEVELS,
    PriceBar,
    StrategyMultipleTestingAudit,
    _strategy_evidence_status,
    build_causal_multihorizon_momentum_targets,
)


def _candidate(name: str, family: str, phase: float) -> dict:
    current = date(2020, 1, 1)
    nav = 1.0
    benchmark = 1.0
    rows = []
    for index in range(241):
        if index:
            strategy_return = 0.0003 + 0.0015 * math.sin(index / 11.0 + phase)
            benchmark_return = 0.0002 + 0.0010 * math.sin(index / 13.0)
            nav *= 1.0 + strategy_return
            benchmark *= 1.0 + benchmark_return
        rows.append(
            {
                "date": current.isoformat(),
                "split": "valid",
                "nav": nav,
                "buy_hold_nav": benchmark,
            }
        )
        current += timedelta(days=1)
    spec = {
        "name": name,
        "final_selection_eligible": True,
        "observe_only": False,
    }
    spec[family] = True
    return {
        "spec": spec,
        "backtest": {
            "nav": rows,
            "metrics": {
                "train": {
                    "annual_return": 0.08 + phase * 0.001,
                    "sharpe": 0.8 + phase * 0.01,
                    "max_drawdown": -0.10,
                }
            },
        },
        "selection": {},
    }


class StrategyMultipleTestingAuditTests(unittest.TestCase):
    def test_no_degradation_guard_rejects_cash_only_noop(self) -> None:
        cash_backtest = {
            "metrics": {
                split: {
                    "total_return": 0.0,
                    "annual_return": 0.0,
                    "sharpe": 0.0,
                    "max_drawdown": 0.0,
                    "buy_hold_return": 0.1,
                    "periods": 252,
                    "signal_trigger_count": 0,
                    "avg_position": 0.0,
                }
                for split in ("train", "valid")
            },
            "nav": [],
            "trades": [],
        }
        decision = NoDegradationGuard().decide(cash_backtest, cash_backtest)
        self.assertFalse(decision["accepted_final"])
        self.assertFalse(decision["train_has_active_path"])
        self.assertFalse(decision["validation_has_active_path"])
        self.assertIn(
            "candidate_has_no_active_validation_path",
            decision["penalties"],
        )
        evidence = _strategy_evidence_status(
            decision,
            {"name": "验证失败观察保护", "observe_only": True},
            "验证失败观察保护",
        )
        self.assertFalse(evidence["validated_strategy"])
        self.assertEqual(
            evidence["status"],
            "observe_only_no_validated_strategy",
        )

    def test_nested_validation_preselection_is_active(self) -> None:
        candidates = [
            _candidate("baseline_a", "predeclared_baseline", 0.0),
            _candidate("baseline_b", "predeclared_baseline", 0.7),
            _candidate("trend_a", "sparse_trend_participation", 1.4),
            _candidate("trend_b", "sparse_trend_participation", 2.1),
        ]
        report = StrategyMultipleTestingAudit().apply(candidates)
        nested = report["nested_validation_preselection"]
        self.assertEqual(nested["status"], "completed")
        self.assertEqual(nested["architecture_family_count"], 2)
        self.assertEqual(nested["shortlisted_candidate_count"], 2)
        self.assertEqual(report["test_usage"], "not_used")

    def test_dual_momentum_is_causal_and_uses_five_level_risk_budget(self) -> None:
        current = date(2018, 1, 1)
        bars = []
        split_by_date = {}
        benchmark_a = {}
        benchmark_b = {}
        price = 10.0
        for index in range(1050):
            volatility = 0.002 if index < 900 else 0.025
            price *= math.exp(0.0004 + volatility * math.sin(index / 5.0))
            date_text = current.isoformat()
            bars.append(
                PriceBar(
                    date=date_text,
                    ts_code="000001.SZ",
                    close=price,
                    qfq_close=price,
                    raw_close=price,
                )
            )
            split_by_date[date_text] = (
                "train"
                if index < 700
                else ("valid" if index < 900 else "test")
            )
            base_context = {
                "benchmark_return_20": 0.002,
                "benchmark_return_60": 0.004,
                "benchmark_return_120": 0.006,
            }
            benchmark_a[date_text] = dict(base_context)
            benchmark_b[date_text] = (
                dict(base_context)
                if index < 900
                else {
                    "benchmark_return_20": 0.50,
                    "benchmark_return_60": 0.75,
                    "benchmark_return_120": 1.00,
                }
            )
            current += timedelta(days=1)
        targets_a, scores_a, report = build_causal_multihorizon_momentum_targets(
            bars, split_by_date, 20, benchmark_a, True, True
        )
        targets_b, _, _ = build_causal_multihorizon_momentum_targets(
            bars, split_by_date, 20, benchmark_b, True, True
        )
        pre_test_dates = [bar.date for bar in bars[:900]]
        self.assertEqual(
            [targets_a[date_text] for date_text in pre_test_dates],
            [targets_b[date_text] for date_text in pre_test_dates],
        )
        self.assertTrue(report["volatility_budget_enabled"])
        self.assertEqual(report["relative_strength_horizons"], [20, 60, 120])
        self.assertTrue(set(targets_a.values()).issubset(set(POSITION_LEVELS)))
        self.assertTrue(
            any(
                float(row.get("causal_volatility_budget", 1.0)) < 1.0
                for row in scores_a.values()
                if row.get("decision_date")
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
