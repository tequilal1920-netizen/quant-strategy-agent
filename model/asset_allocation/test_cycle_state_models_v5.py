"""Tests for governed v5 factor schema and explicit-duration cycle filters."""

from __future__ import annotations

import math
import unittest

import numpy as np

from cycle_factor_registry_v5 import validate_cycle_factor_registry_v5
from cycle_macro_models_v5 import (
    build_macro_cycle_probabilities_v5,
    build_pring_market_probabilities_v5,
    merge_cycle_history_v5,
)
from cycle_state_model_v5 import (
    DurationPriorV5,
    duration_hazard_v5,
    duration_pmf_v5,
    explicit_duration_filter_v5,
)


def _months(start_year: int, count: int) -> list[str]:
    return [f"{start_year + index // 12:04d}{index % 12 + 1:02d}" for index in range(count)]


def _macro_rows(count: int = 180) -> list[dict]:
    months = _months(2010, count)
    output: list[dict] = []
    for index, month in enumerate(months):
        slow = math.sin(index / 18.0)
        inventory = math.sin((index - 5) / 8.0)
        demand = math.sin(index / 8.0)
        output.append(
            {
                "month": month,
                "observation_period": month,
                "available_time": month,
                "vintage": f"v{month}",
                "_pit_verified": True,
                "source": "verified_test_fixture",
                "industrial_finished_goods_inventory_yoy": 5.0 + 2.0 * inventory,
                "industrial_revenue_yoy": 6.0 + 2.5 * demand,
                "pmi_new_orders": 50.0 + 2.0 * demand,
                "manufacturing_fai_yoy": 7.0 + 3.0 * slow,
                "enterprise_medium_long_loan_yoy": 9.0 + 2.5 * math.sin((index + 4) / 18.0),
                "capacity_utilization": 75.0 + 1.5 * math.sin((index - 8) / 18.0),
                "industrial_profit_yoy": 8.0 + 5.0 * math.sin((index - 3) / 18.0),
                "pmi_manufacturing": 50.0 + 2.0 * demand,
                "industrial_value_added_yoy": 5.5 + 2.0 * demand,
                "cpi_national_yoy": 2.0 + 1.2 * math.sin((index - 6) / 11.0),
                "ppi_yoy": 1.0 + 2.0 * math.sin((index - 4) / 11.0),
                "sf_stock_yoy": 10.0 + 1.8 * math.sin((index + 3) / 9.0),
                "m1_m2_spread": -2.0 + 1.0 * math.sin((index + 2) / 9.0),
                "m2_yoy": 9.0 + 1.2 * math.sin(index / 10.0),
                "dr007": 2.2 - 0.3 * math.sin(index / 10.0),
                "equity_risk_premium": 3.0 - 0.4 * math.sin(index / 8.0),
                "stock_bond_relative_momentum": 0.04 * math.sin((index + 1) / 8.0),
            }
        )
    return output


class DurationStateModelV5Tests(unittest.TestCase):
    def test_maximum_entropy_duration_prior_matches_support_and_mean(self) -> None:
        prior = DurationPriorV5(3, 7.0, 12)
        pmf = duration_pmf_v5(prior)
        support = np.arange(3, 13, dtype=float)
        self.assertAlmostEqual(float(pmf.sum()), 1.0, places=12)
        self.assertAlmostEqual(float(np.dot(pmf, support)), 7.0, places=9)
        hazard = duration_hazard_v5(prior)
        np.testing.assert_allclose(hazard[:2], np.zeros(2))
        self.assertAlmostEqual(float(hazard[-1]), 1.0, places=12)

    def test_duration_filter_is_causal_and_freezes_after_train_end(self) -> None:
        months = _months(2018, 72)
        likelihood = np.zeros((72, 3), dtype=float)
        likelihood[:30, 0] = 2.0
        likelihood[30:50, 1] = 2.0
        likelihood[50:, 2] = 2.0
        transition = np.asarray(((0, 0.9, 0.1), (0.1, 0, 0.9), (0.9, 0.1, 0)), dtype=float)
        priors = tuple(DurationPriorV5(2, 5.0, 10) for _ in range(3))
        first = explicit_duration_filter_v5(
            likelihood,
            transition,
            priors,
            months=months,
            train_end=months[35],
        )
        changed = likelihood.copy()
        changed[50:] = np.asarray((8.0, -8.0, -8.0))
        second = explicit_duration_filter_v5(
            changed,
            transition,
            priors,
            months=months,
            train_end=months[35],
        )
        np.testing.assert_allclose(first.state_probabilities[:50], second.state_probabilities[:50])
        np.testing.assert_allclose(first.exit_transition_history[35], first.final_exit_transition)
        self.assertEqual(first.learned_through, months[35])
        np.testing.assert_allclose(first.state_probabilities.sum(axis=1), np.ones(72), atol=1.0e-12)


class GovernedCycleModelV5Tests(unittest.TestCase):
    def test_factor_registry_requires_all_economic_pillars(self) -> None:
        audit = validate_cycle_factor_registry_v5()
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["required_pillars"]["juglar"], ["capacity", "credit", "investment", "profit"])
        self.assertEqual(
            audit["required_pillars"]["merrill"],
            ["credit", "growth", "inflation", "liquidity", "risk_appetite", "valuation"],
        )
        self.assertIn("never guesses", audit["provider_binding_policy"])

    def test_macro_cycles_are_pit_admitted_duration_aware_and_train_frozen(self) -> None:
        rows = _macro_rows()
        result = build_macro_cycle_probabilities_v5(rows, train_end="201912")
        latest = result[-1]
        for cycle in ("kitchin", "juglar", "merrill"):
            payload = latest[cycle]
            self.assertTrue(payload["eligible_for_views"], cycle)
            self.assertEqual(payload["data_status"], "D3")
            self.assertAlmostEqual(sum(payload["probabilities"].values()), 1.0, places=12)
            self.assertEqual(payload["duration_model"]["method"], "explicit_duration_hidden_semi_markov_forward_filter")
            np.testing.assert_allclose(result[119][cycle]["transition_matrix"], payload["transition_matrix"])
        self.assertEqual(
            set(latest["juglar"]["pillar_scores"]),
            {"investment", "credit", "capacity", "profit"},
        )
        self.assertEqual(
            set(latest["merrill"]["axis_scores"]),
            {"growth", "inflation", "credit", "liquidity", "valuation", "risk_appetite", "valuation_risk_appetite"},
        )
        self.assertFalse(latest["kondratieff"]["eligible_for_views"])
        self.assertEqual(latest["kondratieff"]["confidence"], 0.0)

    def test_missing_profit_or_pit_blocks_admission_without_proxy(self) -> None:
        rows = _macro_rows()
        for row in rows:
            row.pop("industrial_profit_yoy")
        rows[-1]["_pit_verified"] = False
        result = build_macro_cycle_probabilities_v5(rows, train_end="201912")
        latest = result[-1]
        self.assertFalse(latest["juglar"]["eligible_for_views"])
        self.assertIn("industrial_profit_growth", latest["juglar"]["factor_evidence"]["missing_required_factors"])
        for cycle in ("kitchin", "juglar", "merrill"):
            self.assertFalse(latest[cycle]["eligible_for_views"])
            self.assertEqual(latest[cycle]["factor_evidence"]["admission_reason"], "pit_or_vintage_not_verified")

    def test_future_macro_values_do_not_change_training_history(self) -> None:
        rows = _macro_rows()
        changed = [dict(row) for row in rows]
        for row in changed[132:]:
            row["pmi_manufacturing"] += 30.0
            row["industrial_finished_goods_inventory_yoy"] -= 30.0
            row["equity_risk_premium"] += 20.0
        first = build_macro_cycle_probabilities_v5(rows, train_end="201912")
        second = build_macro_cycle_probabilities_v5(changed, train_end="201912")
        for left, right in zip(first[:132], second[:132]):
            for cycle in ("kitchin", "juglar", "merrill"):
                self.assertEqual(left[cycle]["probabilities"], right[cycle]["probabilities"])

    def test_pring_excludes_gold_and_merge_exposes_diagnostics(self) -> None:
        rng = np.random.default_rng(20260811)
        months = _months(2016, 108)
        returns = rng.normal(0.003, [0.04, 0.015, 0.03, 0.035], size=(108, 4))
        first = build_pring_market_probabilities_v5(months, returns, train_end="202012")
        changed = returns.copy()
        changed[:, 2] = rng.normal(0.30, 0.40, size=108)
        second = build_pring_market_probabilities_v5(months, changed, train_end="202012")
        for left, right in zip(first, second):
            self.assertEqual(left["probabilities"], right["probabilities"])
            self.assertEqual(left["state"], right["state"])
        self.assertEqual(first[-1]["excluded_assets"], ["gold"])
        self.assertEqual(first[-1]["duration_model"]["learned_through"], "202012")

        macro = build_macro_cycle_probabilities_v5(_macro_rows(108), train_end="201912")
        merged = merge_cycle_history_v5(months, first, macro)
        self.assertEqual(len(merged), len(months))
        self.assertEqual(merged[-1]["cycle_diagnostics"]["factor_schema_version"], "5.1")
        self.assertNotIn("kondratieff", merged[-1]["cycle_diagnostics"]["admitted_cycles"])
        self.assertEqual(merged[-1]["cycle_eligibility"]["kondratieff"], False)


if __name__ == "__main__":
    unittest.main()
