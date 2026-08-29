import copy
import io
import urllib.error

import pytest

import mandate_compiler

from mandate_compiler import (
    AWAITING_CONFIRMATION,
    BLOCKED_LLM,
    BLOCKED_SCHEMA,
    BLOCKED_SOLVER_CAPABILITY,
    CONFIRMED,
    INFEASIBLE,
    compile_mandate,
    confirm_mandate,
    is_confirmation_valid,
    refresh_after_edit,
)


RAW_REQUEST = (
    "使用中证500（000905.SH）月频因子实验室得分 artifact.factor_lab.csi500.monthly，"
    "从500只成分股联合选择50只。单票权重0.5%到5%，行业主动偏离±2%，"
    "风格暴露±0.2z，年化跟踪误差不超过6%，单边换手不超过30%，"
    "交易参与率不超过10% ADV，并剔除000001.SZ。"
)


def _user_numeric(field, value, claim):
    return {"source_id": "user_supplied", "field": field, "value": value, "claim": claim}


def _constraint(
    constraint_id,
    constraint_type,
    metric,
    lower,
    upper,
    unit,
    dependencies,
    evidence,
    *,
    scope_extra=None,
):
    scope = {"metric": metric, "universe": "CSI500"}
    scope.update(scope_extra or {})
    return {
        "id": constraint_id,
        "type": constraint_type,
        "scope": scope,
        "lower": lower,
        "upper": upper,
        "unit": unit,
        "hard": True,
        "penalty": None,
        "priority": 1,
        "formula": f"deterministic formula for {constraint_id}",
        "data_dependencies": dependencies,
        "evidence": evidence,
    }


def valid_payload(mode="joint_cardinality"):
    objective = {
        "type": "benchmark_relative_alpha",
        "benchmark_id": "000905.SH",
        "score_artifact_id": "artifact.factor_lab.csi500.monthly",
        "rebalance_frequency": "monthly",
        "risk_model_id": "barra_like_pit_v1",
    }
    if mode == "fixed_candidate_set":
        objective["candidate_set_id"] = "candidate.csi500.top50.202608"
    return {
        "schema_version": "OptimizationMandate/v1",
        "mode": mode,
        "objective": objective,
        "constraints": [
            _constraint(
                "holding.cardinality.exact",
                "holding",
                "cardinality",
                50,
                50,
                "count",
                ["universe_membership", "alpha_scores"],
                [
                    _user_numeric("lower", 50, "用户要求选择50只"),
                    _user_numeric("upper", 50, "用户要求选择50只"),
                ],
            ),
            _constraint(
                "holding.security_weight",
                "holding",
                "security_weight",
                0.005,
                0.05,
                "weight_fraction",
                ["universe_membership", "benchmark_weights"],
                [
                    _user_numeric("lower", 0.005, "用户给出单票下限0.5%"),
                    _user_numeric("upper", 0.05, "用户给出单票上限5%"),
                ],
            ),
            _constraint(
                "industry.active_exposure.all",
                "industry",
                "active_exposure",
                -0.02,
                0.02,
                "weight_fraction",
                ["industry_classification_pit", "benchmark_weights"],
                [
                    _user_numeric("lower", -0.02, "用户给出行业主动偏离±2%"),
                    _user_numeric("upper", 0.02, "用户给出行业主动偏离±2%"),
                ],
                scope_extra={"group": "all", "benchmark_relative": True},
            ),
            _constraint(
                "style.active_exposure.all",
                "style",
                "active_exposure",
                -0.2,
                0.2,
                "zscore",
                ["style_exposure_matrix_pit", "benchmark_weights"],
                [
                    _user_numeric("lower", -0.2, "用户给出风格暴露±0.2z"),
                    _user_numeric("upper", 0.2, "用户给出风格暴露±0.2z"),
                ],
                scope_extra={"style": "all", "benchmark_relative": True},
            ),
            _constraint(
                "active_risk.tracking_error",
                "active_risk",
                "tracking_error",
                None,
                0.06,
                "annualized_fraction",
                ["benchmark_weights", "factor_covariance_pit", "specific_risk_pit"],
                [_user_numeric("upper", 0.06, "用户给出年化跟踪误差6%上限")],
                scope_extra={"benchmark_relative": True},
            ),
            _constraint(
                "trading.one_way_turnover",
                "trading",
                "one_way_turnover",
                None,
                0.30,
                "turnover_fraction",
                ["previous_weights", "tradeability_flags", "transaction_cost_model"],
                [_user_numeric("upper", 0.30, "用户给出单边换手30%上限")],
                scope_extra={"turnover_convention": "one_way"},
            ),
            _constraint(
                "liquidity.adv_participation",
                "liquidity",
                "adv_participation",
                None,
                0.10,
                "adv_fraction",
                ["point_in_time_adv", "portfolio_nav", "previous_weights", "prices_pit"],
                [_user_numeric("upper", 0.10, "用户给出10% ADV参与率上限")],
                scope_extra={"lookback": "20 trading days", "lag": "1 trading day"},
            ),
            _constraint(
                "list.blacklist",
                "list",
                "blacklist",
                None,
                None,
                "binary",
                ["security_master_pit", "tradeability_flags"],
                [{"source_id": "user_supplied", "claim": "用户要求剔除000001.SZ"}],
                scope_extra={"list_name": "blacklist", "security_set": ["000001.SZ"]},
            ),
        ],
        "retrieval_source_ids": [
            "broker.dfzq.2017.portfolio_optimization",
            "method.msci_barra.factor_risk",
        ],
        "assumptions": ["所有输入必须为调仓时点可得的PIT快照"],
    }


class StaticLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, *, system_prompt, user_payload):
        self.calls.append((system_prompt, user_payload))
        return copy.deepcopy(self.response)


def test_fake_llm_success_covers_all_constraint_families():
    client = StaticLLM(valid_payload())
    result = compile_mandate(RAW_REQUEST, llm_client=client, available_solvers=["GUROBI"])

    assert result["status"] == AWAITING_CONFIRMATION
    assert result["attempts"] == 1
    assert result["fallback_used"] is False
    assert result["weights_emitted"] is False
    assert len(result["draft_hash"]) == 64
    assert {item["type"] for item in result["mandate"]["constraints"]} == {
        "holding",
        "industry",
        "style",
        "active_risk",
        "trading",
        "liquidity",
        "list",
    }
    policy = result["solver_policy"]
    assert policy["capable_solvers"] == ["GUROBI"]
    assert policy["construction"] == "native_misocp_or_miqcp"
    assert policy["semantic_fallback_allowed"] is False
    assert policy["global_miqcp_optimality_claimed"] is False
    assert "target_weights" not in result["mandate"]


def test_joint_cardinality_accepts_highs_milp_plus_clarabel_socp():
    result = compile_mandate(
        RAW_REQUEST,
        llm_client=StaticLLM(valid_payload()),
        available_solvers=["SCIPY_HIGHS_MILP", "CLARABEL"],
    )

    assert result["status"] == AWAITING_CONFIRMATION
    policy = result["solver_policy"]
    assert policy["construction"] == (
        "highs_milp_linear_support_then_clarabel_full_socp_certification"
    )
    assert policy["hybrid_route_ready"] is True
    assert policy["hybrid_phase_i_solver"] == "SCIPY_HIGHS_MILP"
    assert policy["hybrid_phase_ii_solver"] == "CLARABEL"
    assert policy["candidate_set_must_be_frozen_before_solver"] is False
    assert policy["support_is_jointly_optimized_with_linear_mandates"] is True
    assert policy["milp_trial_weights_are_tradable"] is False
    assert policy["global_miqcp_optimality_claimed"] is False
    assert policy["semantic_fallback_allowed"] is False


def test_missing_api_key_blocks_without_local_fallback(monkeypatch):
    monkeypatch.delenv("AI_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_ROUTER_URL", raising=False)

    result = compile_mandate(RAW_REQUEST, available_solvers=["GUROBI"])

    assert result["status"] == BLOCKED_LLM
    assert result["attempts"] == 0
    assert result["fallback_used"] is False
    assert result["mandate"] is None


def test_router_http_error_is_redacted_and_not_retried(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_API_KEY", "test-key-never-sent")
    monkeypatch.setenv("AI_ROUTER_URL", "https://router.invalid/v1/chat/completions")
    seen_requests = []

    def fail_with_http_error(request, timeout):
        seen_requests.append((request, timeout))
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO("upstream body must remain redacted".encode("utf-8")),
        )

    monkeypatch.setattr(mandate_compiler.urllib.request, "urlopen", fail_with_http_error)

    result = compile_mandate(RAW_REQUEST, available_solvers=["GUROBI"])

    assert result["status"] == BLOCKED_LLM
    assert result["attempts"] == 1
    assert result["errors"] == ["AI Router call failed: HTTP 403"]
    assert "upstream body" not in str(result)
    assert len(seen_requests) == 1
    headers = {key.lower(): value for key, value in seen_requests[0][0].header_items()}
    assert headers["accept"] == "application/json"
    assert headers["user-agent"] == "Mozilla/5.0 QuantStrategyAgent-MandateCompiler/1.0"


def test_invalid_json_uses_initial_plus_two_repairs_then_blocks():
    client = StaticLLM("```json\n{}\n```")

    result = compile_mandate(RAW_REQUEST, llm_client=client, available_solvers=["GUROBI"])

    assert result["status"] == BLOCKED_LLM
    assert result["attempts"] == 3
    assert len(client.calls) == 3
    assert client.calls[1][1]["phase"] == "repair"
    assert result["fallback_used"] is False


def test_llm_cannot_change_requested_joint_cardinality_mode():
    payload = valid_payload(mode="fixed_candidate_set")
    result = compile_mandate(
        RAW_REQUEST,
        llm_client=StaticLLM(payload),
        mode="joint_cardinality",
        available_solvers=["SCIPY_HIGHS_MILP", "CLARABEL"],
    )

    assert result["status"] == BLOCKED_SCHEMA
    assert result["attempts"] == 3
    assert result["fallback_used"] is False
    assert any("must match requested_mode" in item for item in result["errors"])


def test_numeric_bound_without_value_matched_evidence_is_schema_block():
    payload = valid_payload()
    payload["constraints"][0]["evidence"] = [
        {"source_id": "broker.dfzq.2017.portfolio_optimization", "claim": "generic constraint family"}
    ]
    client = StaticLLM(payload)

    result = compile_mandate(RAW_REQUEST, llm_client=client, available_solvers=["GUROBI"])

    assert result["status"] == BLOCKED_SCHEMA
    assert result["attempts"] == 3
    assert any("value-matched evidence" in item for item in result["errors"])


def test_conflicting_hard_constraints_are_infeasible():
    payload = valid_payload()
    payload["constraints"].append(
        _constraint(
            "industry.active_exposure.conflict",
            "industry",
            "active_exposure",
            0.03,
            0.04,
            "weight_fraction",
            ["industry_classification_pit", "benchmark_weights"],
            [
                _user_numeric("lower", 0.03, "测试请求给出3%"),
                _user_numeric("upper", 0.04, "测试请求给出4%"),
            ],
            scope_extra={"group": "all", "benchmark_relative": True},
        )
    )
    raw_request = RAW_REQUEST + " 另要求同一行业主动偏离下限3%、上限4%。"

    result = compile_mandate(
        raw_request,
        llm_client=StaticLLM(payload),
        available_solvers=["GUROBI"],
    )

    assert result["status"] == INFEASIBLE
    assert any("empty intersection" in item for item in result["errors"])


def test_confirmation_hash_invalidates_after_any_constraint_edit():
    result = compile_mandate(
        RAW_REQUEST,
        llm_client=StaticLLM(valid_payload()),
        available_solvers=["GUROBI"],
    )
    confirmed = confirm_mandate(
        result,
        actor="portfolio_reviewer",
        expected_draft_hash=result["draft_hash"],
        confirmed_at="2026-08-11T00:00:00Z",
    )
    assert confirmed["status"] == CONFIRMED
    assert is_confirmation_valid(confirmed)

    confirmed["mandate"]["constraints"][1]["upper"] = 0.04
    assert not is_confirmation_valid(confirmed)
    refreshed = refresh_after_edit(confirmed)
    assert refreshed["status"] == AWAITING_CONFIRMATION
    assert refreshed["confirmation"] is None
    assert refreshed["draft_hash"] != result["draft_hash"]


def test_joint_cardinality_blocks_without_native_or_certified_hybrid():
    result = compile_mandate(
        RAW_REQUEST,
        llm_client=StaticLLM(valid_payload()),
        available_solvers=["CLARABEL", "SCS"],
    )

    assert result["status"] == BLOCKED_SOLVER_CAPABILITY
    assert result["solver_policy"]["hybrid_route_ready"] is False
    assert result["solver_policy"]["semantic_fallback_allowed"] is False
    assert "top-K" in result["solver_policy"]["prohibited_fallback"]
    assert "SCIPY_HIGHS_MILP" in result["errors"][0]
    assert "CLARABEL" in result["errors"][0]


def test_fixed_candidate_set_is_explicit_two_stage_not_mip():
    payload = valid_payload(mode="fixed_candidate_set")
    result = compile_mandate(
        RAW_REQUEST,
        llm_client=StaticLLM(payload),
        mode="fixed_candidate_set",
        available_solvers=["CLARABEL"],
    )

    assert result["status"] == AWAITING_CONFIRMATION
    policy = result["solver_policy"]
    assert policy["candidate_set_must_be_frozen_before_solver"] is True
    assert policy["is_mixed_integer_formulation"] is False
    assert policy["required_problem_class"] == "QP_or_SOCP"


def test_llm_direct_weight_output_is_rejected():
    payload = valid_payload()
    payload["target_weights"] = {"000002.SZ": 1.0}
    result = compile_mandate(
        RAW_REQUEST,
        llm_client=StaticLLM(payload),
        available_solvers=["GUROBI"],
    )

    assert result["status"] == BLOCKED_SCHEMA
    assert any("must not emit weights" in item for item in result["errors"])




def plan_options_payload():
    return {
        "schema_version": "OptimizationPlanOptions/v1",
        "options": [
            {
                "id": "baseline_constrained_optimizer",
                "name": "baseline_constrained_optimizer",
                "profile": "score_plus_active_risk",
                "summary": "在因子得分、行业风格中性、跟踪误差和换手之间做联合优化。",
                "objective_equation": "max sum_i alpha_i w_i - lambda_te (w-b)'Sigma(w-b) - lambda_to |w-w_prev|_1",
                "objective_terms": ["alpha_score", "active_risk", "turnover_cost"],
                "default_parameters": {
                    "holding": {"target_count": 50, "min_weight": 0.005, "max_weight": 0.05},
                    "risk": {"tracking_error_limit": 0.06, "style_abs_bound": 0.10},
                    "trading": {"turnover_limit": 1.0},
                },
                "added_constraints": [
                    {"name": "exact_cardinality", "type": "holding", "formula": "sum_i z_i = 50"},
                    {"name": "industry_active", "type": "industry", "formula": "|A_ind(w-b)| <= 0.02"},
                ],
                "constraint_equations": ["sum_i w_i = 1", "0.005 z_i <= w_i <= 0.05 z_i", "sqrt((w-b)'Sigma(w-b)) <= 0.06"],
                "solver_steps": ["HiGHS MILP selects binary support", "Clarabel certifies SOCP risk constraints"],
                "expected_tradeoff": "控制行业和风险后牺牲少量纯得分暴露，换取更稳健的主动收益。",
                "mandate_request": RAW_REQUEST + " 选择 baseline_constrained_optimizer，严格保留50只、行业风格、跟踪误差和换手约束。",
            },
            {
                "id": "turnover_controlled_optimizer",
                "name": "turnover_controlled_optimizer",
                "profile": "low_turnover",
                "summary": "在原约束基础上强化换手惩罚与成交约束。",
                "objective_equation": "max sum_i alpha_i w_i - lambda_te TE^2 - lambda_to turnover - lambda_cost cost",
                "objective_terms": ["alpha_score", "tracking_error", "turnover", "cost"],
                "default_parameters": {"trading": {"turnover_limit": 0.6, "cost_penalty": 2.0}},
                "added_constraints": [{"name": "turnover", "type": "trading", "formula": "0.5*|w-w_prev|_1 <= 0.6"}],
                "constraint_equations": ["0.5*sum_i |w_i-w_prev_i| <= 0.6"],
                "solver_steps": ["linearize turnover", "solve support", "certify continuous portfolio"],
                "expected_tradeoff": "降低调仓噪音，可能牺牲短期alpha追随速度。",
                "mandate_request": RAW_REQUEST + " 选择 turnover_controlled_optimizer，并把单期换手控制到60%。",
            },
        ],
    }


def test_generate_mandate_plan_options_uses_router_phase_and_returns_selectable_options():
    client = StaticLLM(plan_options_payload())

    result = mandate_compiler.generate_mandate_plan_options(
        RAW_REQUEST,
        llm_client=client,
        available_solvers=["SCIPY_HIGHS_MILP", "CLARABEL"],
    )

    assert result["status"] == mandate_compiler.AWAITING_PLAN_SELECTION
    assert result["schema_version"] == mandate_compiler.PLAN_SCHEMA_VERSION
    assert result["weights_emitted"] is False
    assert result["fallback_used"] is False
    assert len(result["options"]) == 2
    assert result["options"][0]["mandate_request"].startswith(RAW_REQUEST)
    assert client.calls[0][1]["phase"] == "plan_options"
    assert client.calls[0][1]["schema_contract"]["option_count"] == "1_to_3"
    assert client.calls[0][1]["solver_policy"]["mode"] == "joint_cardinality"


def test_generate_mandate_plan_options_blocks_direct_weight_fields():
    payload = plan_options_payload()
    payload["options"][0]["weights"] = {"000001.SZ": 1.0}

    result = mandate_compiler.generate_mandate_plan_options(
        RAW_REQUEST,
        llm_client=StaticLLM(payload),
        available_solvers=["SCIPY_HIGHS_MILP", "CLARABEL"],
    )

    assert result["status"] == BLOCKED_SCHEMA
    assert result["fallback_used"] is False
    assert result["options"] == []
    assert any("direct weight" in item for item in result["errors"])
