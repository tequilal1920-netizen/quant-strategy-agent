import unittest

from model.llm_factor_mining import factor_miner as fm


def known_feature(preferred="large_order_balance"):
    if preferred in fm.AI_FEATURES:
        return preferred
    return list(fm.AI_FEATURES)[0]


def sample_dsl(feature=None):
    feature = known_feature() if feature is None else feature
    return {"op": "rank", "child": {"op": "feature", "name": feature}}


def sample_candidate(name="factor_a", feature=None):
    feature = known_feature() if feature is None else feature
    return {
        "factor_name": name,
        "family": "unit_test",
        "channel": "llm_hypothesis_generation",
        "dsl": sample_dsl(feature),
        "required_fields": [feature],
        "hypothesis": "unit-test economic hypothesis",
        "lineage": [],
    }


def sample_row(factor="factor_a", failure="incremental_information_shortage"):
    return {
        "factor": factor,
        "family": "unit_test",
        "channel": "llm_hypothesis_generation",
        "search_stage_pass": False,
        "search_reliability_pass": False,
        "accepted": False,
        "search_diagnosis": {"failure_type": failure},
        "search_diagnosis_code": failure,
        "search_risk_adjusted_selection_score": 0.12,
        "posterior_joint_positive_probability": 0.61,
        "valid_incremental_residual_rank_ic": 0.011,
        "valid_downstream_marginal_rank_ic_gain": 0.004,
        "search_pbo_proxy": 0.31,
        "search_max_abs_corr_to_other_factor": 0.42,
        "train_rank_ic": 0.03,
        "valid_rank_ic": 0.025,
    }


class LlmTrajectoryContractTests(unittest.TestCase):
    def test_failure_repair_plan_is_specific_and_search_only(self):
        plan = fm.compact_failure_repair_plan(sample_row())
        self.assertEqual(plan["failure_type"], "incremental_information_shortage")
        self.assertIn("dual-residual", plan["repair_objective"])
        self.assertFalse(plan["test_metrics_used"])
        self.assertIn("test_metric_feedback", plan["must_avoid"])

    def test_search_trajectory_contains_hof_pareto_and_no_test_feedback(self):
        leaderboard = [
            sample_row("factor_a", "incremental_information_shortage"),
            {**sample_row("factor_b", "search_stage_passed"), "search_stage_pass": True, "accepted": True},
        ]
        event = fm.build_search_trajectory_event(
            2,
            [sample_candidate("factor_a")],
            leaderboard[:1],
            [sample_candidate("factor_child", "large_order_balance")],
            leaderboard,
            {"llm": {"utility": 0.3}},
            {"status": "ready"},
            {"status": "generated"},
            {"status": "skipped"},
            False,
        )
        self.assertEqual(event["schema"], fm.LLM_FACTOR_TRAJECTORY_SCHEMA)
        self.assertFalse(event["test_metrics_used"])
        self.assertTrue(event["hall_of_fame"])
        self.assertTrue(event["pareto_front"])
        self.assertEqual(event["parent_selection"][0]["repair_blueprint"]["failure_type"], "incremental_information_shortage")

    def test_memory_update_exposes_pareto_and_repair_blueprints(self):
        leaderboard = [
            sample_row("factor_a", "novelty_shortage"),
            {**sample_row("factor_b", "search_stage_passed"), "search_stage_pass": True, "search_reliability_pass": True},
        ]
        update = fm.build_memory_update(leaderboard)
        self.assertEqual(update["memory_policy"], "train_validation_search_only_no_test_or_lifecycle_feedback")
        self.assertIn("hall_of_fame", update)
        self.assertIn("pareto_front", update)
        self.assertTrue(update["repair_blueprints"])
        self.assertFalse(update["test_fields_in_search_memory"])

    def test_quality_gate_rejects_thin_llm_factor_metadata(self):
        thin = {field: "good" for field in fm.LLM_CANDIDATE_REASONING_FIELDS}
        weak = fm.llm_candidate_quality_gate(thin, sample_dsl(), max_complexity=32)
        self.assertFalse(weak["passed"])
        self.assertIn("validation_acceptance_criteria_missing_metric", weak["issues"])
        strong = {
            "hypothesis": "economic flow revision signal captures institutional order imbalance before earnings revision",
            "repair_hypothesis": "repairs incremental information shortage by combining money-flow residual with momentum state",
            "validation_acceptance_criteria": "valid RankIC and residual incremental IC improve with purged k-fold coverage",
            "expected_low_correlation_source": "orthogonal residual from a different money-flow field family",
            "expected_failure_mode": "crowding risk and regime stress can reverse the signal",
            "anti_overfit_plan": "walk-forward validation with purged k-fold, CSCV PBO and embargo guard",
        }
        strong_gate = fm.llm_candidate_quality_gate(strong, sample_dsl(), max_complexity=32)
        self.assertTrue(strong_gate["passed"], strong_gate["issues"])
        self.assertFalse(strong_gate["test_metrics_used"])

    def test_feedback_mutation_prompt_requires_repair_contract(self):
        captured = {}
        original = fm.call_ai_router_json

        def fake_call(_system_prompt, payload, retries=1):
            captured["payload"] = payload
            return {
                "candidates": [{
                    "parent_factor": "factor_a",
                    "chinese_name": "反馈修复因子",
                    "family": "feedback_unit",
                    "hypothesis": "repair incremental information shortage",
                    "dsl": sample_dsl(known_feature("large_order_balance")),
                    "data_scope": "large_order_balance",
                    "windows": [20, 60],
                    "mutation_reason": "add money-flow residual signal",
                    "repair_hypothesis": "money-flow imbalance is orthogonal to the baseline",
                    "validation_acceptance_criteria": "valid incremental residual IC improves",
                    "expected_low_correlation_source": "different field family and operator island",
                    "expected_failure_mode": "crowding reverses in stress regime",
                    "anti_overfit_plan": "purged k-fold and low-correlation gate",
                }]
            }, {"status": "ok"}

        try:
            fm.call_ai_router_json = fake_call
            out, status = fm.generate_llm_feedback_mutations(
                [sample_row("factor_a")],
                {"factor_a": sample_candidate("factor_a")},
                {"memory_scope": "unit"},
                iteration=3,
                budget=1,
            )
        finally:
            fm.call_ai_router_json = original
        self.assertEqual(status["valid_candidate_count"], 1)
        self.assertEqual(captured["payload"]["trajectory_process_contract"]["schema"], fm.LLM_FACTOR_TRAJECTORY_SCHEMA)
        self.assertIn("repair_blueprint", captured["payload"]["parents"][0])
        lineage = out[0]["lineage"][0]
        self.assertEqual(lineage["trajectory_schema"], fm.LLM_FACTOR_TRAJECTORY_SCHEMA)
        self.assertFalse(lineage["test_metrics_used"])
        self.assertTrue(lineage["metadata_quality_gate"]["passed"], lineage["metadata_quality_gate"].get("issues"))
        self.assertIn("repair_blueprint", lineage)


if __name__ == "__main__":
    unittest.main(verbosity=2)
