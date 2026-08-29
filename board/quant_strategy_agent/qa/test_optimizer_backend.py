from __future__ import annotations

import copy
import sqlite3
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from flask import Flask


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parents[1]
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

import optimizer_backend  # noqa: E402


RAW_REQUEST = (
    "使用中证500（000905.SH）500只成分股的月频历史得分，联合选择50只；"
    "单票权重下限0.5%、上限5%。"
)


def _evidence(field: str, value: float | int) -> dict:
    return {
        "source_id": "user_supplied",
        "field": field,
        "value": value,
        "claim": f"用户明确给出{field}={value}",
    }


def _constraint(
    constraint_id: str,
    metric: str,
    lower: float | int,
    upper: float | int,
    unit: str,
) -> dict:
    return {
        "id": constraint_id,
        "type": "holding",
        "scope": {"metric": metric, "universe": "CSI500"},
        "lower": lower,
        "upper": upper,
        "unit": unit,
        "hard": True,
        "penalty": None,
        "priority": 1,
        "formula": f"严格执行 {constraint_id}",
        "data_dependencies": ["universe_membership", "benchmark_weights"],
        "evidence": [_evidence("lower", lower), _evidence("upper", upper)],
    }


def valid_llm_payload() -> dict:
    return {
        "schema_version": "OptimizationMandate/v1",
        "mode": "joint_cardinality",
        "objective": {
            "type": "benchmark_relative_alpha",
            "benchmark_id": "000905.SH",
            "score_artifact_id": "artifact.factor_lab.csi500.monthly",
            "rebalance_frequency": "monthly",
            "risk_model_id": "barra_like_pit_v1",
        },
        "constraints": [
            _constraint("holding.cardinality.exact", "cardinality", 50, 50, "count"),
            _constraint("holding.security_weight", "security_weight", 0.005, 0.05, "weight_fraction"),
        ],
        "retrieval_source_ids": [],
        "assumptions": ["所有输入均为调仓时点可得的PIT数据"],
    }


def make_warehouse(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE index_constituent_period (
              universe TEXT, index_code TEXT, trade_date TEXT, con_code TEXT,
              weight REAL, source TEXT, status TEXT
            );
            CREATE TABLE optimizer_factor_score_period (
              score_run_id TEXT, score_name TEXT, signal_date TEXT,
              ts_code TEXT
            );
            """
        )
        connection.executemany(
            """INSERT INTO index_constituent_period
               VALUES ('CSI500_ENH','000905.SH','20260630',?,0.2,'qa','ready')""",
            [(f"{index:06d}.SZ",) for index in range(1, 501)],
        )
        connection.execute(
            """INSERT INTO index_constituent_period VALUES
               ('OTHER_UNIVERSE','000905.SH','20260630',
                '999999.SZ',100.0,'qa','ready')"""
        )
        connection.executemany(
            """INSERT INTO optimizer_factor_score_period
               VALUES ('qa-score','causal_rolling_icir_neutral_score',
                       '20260630',?)""",
            [(f"{index:06d}.SZ",) for index in range(1, 501)],
        )
    return path


class StaticLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self, *, system_prompt, user_payload):
        assert "never" in system_prompt.lower() or "Never" in system_prompt
        assert user_payload["raw_request"] == RAW_REQUEST
        assert user_payload["requested_mode"] == "joint_cardinality"
        self.calls += 1
        return copy.deepcopy(self.payload)


def make_app(
    tmp_path: Path,
    *,
    llm_client=None,
    runner=None,
    available_solvers=None,
):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="optimizer-test")
    service = optimizer_backend.register_optimizer(
        app,
        state_db_path=tmp_path / "optimizer_state.db",
        llm_client=llm_client,
        runner=runner,
        available_solvers=(
            ["SCIPY_HIGHS_MILP", "CLARABEL"]
            if available_solvers is None
            else available_solvers
        ),
    )
    return app, service


def interpret(client) -> dict:
    response = client.post(
        "/api/optimizer/constraints/interpret",
        json={"raw_request": RAW_REQUEST, "mode": "joint_cardinality"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "AWAITING_CONFIRMATION"
    assert payload["weights_emitted"] is False
    return payload


def confirm(client, draft: dict) -> dict:
    response = client.post(
        "/api/optimizer/constraints/validate",
        json={
            "draft_id": draft["draft_id"],
            "expected_draft_hash": draft["draft_hash"],
            "confirm": True,
            "actor": "qa_reviewer",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "CONFIRMED"
    assert payload["confirmation_valid"] is True
    return payload


def wait_for(client, run_id: str, final: set[str], timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        response = client.get(f"/api/optimizer/runs/{run_id}?live=1")
        assert response.status_code == 200
        latest = response.get_json()
        if latest["status"] in final:
            return latest
        time.sleep(0.01)
    pytest.fail(f"run {run_id} did not finish; latest={latest}")


def assert_no_solution_fields(value) -> None:
    if isinstance(value, dict):
        forbidden = optimizer_backend.DIRECT_SOLUTION_FIELDS & {
            str(key).lower() for key in value
        }
        assert not forbidden
        for item in value.values():
            assert_no_solution_fields(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_solution_fields(item)


def test_bootstrap_exposes_grouped_strict_capabilities(monkeypatch, tmp_path):
    warehouse = make_warehouse(tmp_path / "warehouse.db")
    monkeypatch.setenv("RESEARCH_WAREHOUSE_DB", str(warehouse))
    app, service = make_app(tmp_path, llm_client=StaticLLM(valid_llm_payload()))
    response = app.test_client().get("/api/optimizer/bootstrap")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["defaults"]["universe"]["code"] == "000905.SH"
    assert payload["defaults"]["universe"]["holdings"] == 50
    assert payload["universes"][0]["name"] == "\u4e2d\u8bc1500"
    assert payload["defaults"]["holdings"]["min_weight"] == 0.002
    assert payload["defaults"]["holdings"]["max_weight"] == 0.05
    assert payload["defaults"]["active_risk"]["max_active_weight"] == 0.045
    assert payload["defaults"]["active_risk"]["tracking_error_limit"] == 0.09
    assert payload["defaults"]["trading"]["turnover_limit"] == 1.0
    assert payload["knowledge_base"]["version"] == "ConstraintKnowledgeBase/v1"
    assert all("?" not in item["label"] for item in payload["constraint_groups"])
    assert payload["data_ready"] is True
    assert payload["universes"][0]["constituent_count"] == 500
    assert payload["score_sources"][0]["constituent_count"] == 500
    assert payload["score_sources"][0]["as_of"] == "20260630"
    assert payload["defaults"]["backtest"]["start"] == "20190531"
    assert payload["defaults"]["objective"]["alpha_scale"] == 3.0
    assert payload["defaults"]["objective"]["risk_aversion"] == 0.35
    assert payload["defaults"]["objective"]["turnover_penalty"] == 0.18
    assert payload["defaults"]["industry"]["max_active_deviation"] == 0.03
    assert payload["defaults"]["style"]["max_abs_exposure"] == 0.14
    assert payload["defaults"]["backtest"]["end"] == "20260630"
    assert [item["id"] for item in payload["constraint_groups"]] == [
        "holding",
        "industry",
        "style",
        "active_risk",
        "trading",
        "liquidity",
        "list",
    ]
    assert payload["status"] == "ready"
    assert payload["default_mode"] == "joint_cardinality"
    assert payload["policies"]["optimizer_mode"] == "joint_cardinality"
    assert payload["policies"]["candidate_set_pre_frozen"] is False
    assert payload["policies"]["llm_emits_weights"] is False
    assert payload["policies"]["fallback_allowed"] is False
    joint = payload["capabilities"]["joint_cardinality"]
    assert joint["status"] == "READY"
    assert joint["hybrid_route_ready"] is True
    assert joint["hybrid_phase_i_solver"] == "SCIPY_HIGHS_MILP"
    assert joint["hybrid_phase_ii_solver"] == "CLARABEL"
    assert joint["candidate_set_must_be_frozen_before_solver"] is False
    assert joint["global_miqcp_optimality_claimed"] is False
    assert joint["fallback_allowed"] is False
    assert payload["capabilities"]["fixed_candidate_socp"]["status"] == "BLOCKED_SOLVER_CAPABILITY"
    assert Path(service.store.path).is_file()
    assert response.headers["Cache-Control"] == "no-store"


def test_bootstrap_prefers_latest_inserted_run_within_latest_signal_date(
    monkeypatch, tmp_path
):
    warehouse = make_warehouse(tmp_path / "warehouse.db")
    with sqlite3.connect(warehouse) as connection:
        connection.executemany(
            """INSERT INTO optimizer_factor_score_period
               VALUES ('zzz-older','older-score','20260630',?)""",
            [(f"{index:06d}.SZ",) for index in range(1, 501)],
        )
        connection.executemany(
            """INSERT INTO optimizer_factor_score_period
               VALUES ('aaa-newer','newer-score','20260630',?)""",
            [(f"{index:06d}.SZ",) for index in range(1, 501)],
        )
    monkeypatch.setenv("RESEARCH_WAREHOUSE_DB", str(warehouse))
    app, _ = make_app(tmp_path, llm_client=StaticLLM(valid_llm_payload()))

    payload = app.test_client().get("/api/optimizer/bootstrap").get_json()

    assert payload["score_sources"][0]["score_run_id"] == "aaa-newer"
    assert payload["score_sources"][0]["id"] == "newer-score"
    assert payload["score_sources"][0]["as_of"] == "20260630"
    assert payload["score_sources"][0]["constituent_count"] == 500


def test_discover_solvers_adds_scipy_highs_capability(monkeypatch, tmp_path):
    monkeypatch.setattr(
        optimizer_backend, "_scipy_highs_milp_available", lambda: True
    )
    _, service = make_app(tmp_path, available_solvers=["CLARABEL"])

    assert service._discover_solvers() == ["CLARABEL", "SCIPY_HIGHS_MILP"]


@pytest.mark.parametrize(
    ("available_solvers", "highs_available", "missing"),
    [
        (["CLARABEL"], False, "SCIPY_HIGHS_MILP"),
        ([], True, "CLARABEL"),
    ],
)
def test_bootstrap_blocks_when_either_runtime_solver_is_missing(
    monkeypatch,
    tmp_path,
    available_solvers,
    highs_available,
    missing,
):
    warehouse = make_warehouse(tmp_path / "warehouse.db")
    monkeypatch.setenv("RESEARCH_WAREHOUSE_DB", str(warehouse))
    monkeypatch.setattr(
        optimizer_backend,
        "_scipy_highs_milp_available",
        lambda: highs_available,
    )
    app, _ = make_app(
        tmp_path,
        available_solvers=available_solvers,
    )

    payload = app.test_client().get("/api/optimizer/bootstrap").get_json()

    assert payload["status"] == "BLOCKED_SOLVER"
    assert payload["data_ready"] is True
    assert missing in payload["block_reason"]
    assert payload["capabilities"]["joint_cardinality"]["status"] == "BLOCKED_SOLVER_CAPABILITY"
    assert payload["capabilities"]["joint_cardinality"]["fallback_allowed"] is False


def test_missing_llm_key_is_persisted_as_explicit_block(monkeypatch, tmp_path):
    for key in ("AI_ROUTER_API_KEY", "OPENAI_API_KEY", "AI_ROUTER_URL"):
        monkeypatch.delenv(key, raising=False)
    app, service = make_app(tmp_path)

    response = app.test_client().post(
        "/api/optimizer/constraints/interpret",
        json={"raw_request": RAW_REQUEST, "mode": "joint_cardinality"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "BLOCKED_LLM"
    assert payload["fallback_used"] is False
    assert payload["mandate"] is None
    persisted = service.store.get_draft(payload["draft_id"])
    assert persisted["status"] == "BLOCKED_LLM"
    assert [event["state"] for event in persisted["audit"]][:2] == [
        "DRAFT_RECEIVED",
        "RETRIEVAL_COMPLETE",
    ]


def test_hash_confirmation_and_edit_invalidation(tmp_path):
    app, _ = make_app(tmp_path, llm_client=StaticLLM(valid_llm_payload()))
    client = app.test_client()
    draft = interpret(client)
    confirmed = confirm(client, draft)

    edited = copy.deepcopy(confirmed["mandate"])
    edited["constraints"][0]["formula"] = "经用户修改后的严格等式"
    response = client.post(
        "/api/optimizer/constraints/validate",
        json={
            "draft_id": draft["draft_id"],
            "mandate": edited,
            "expected_draft_hash": draft["draft_hash"],
            "confirm": True,
            "actor": "qa_reviewer",
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["status"] == "AWAITING_CONFIRMATION"
    assert payload["edited"] is True
    assert payload["confirmation_valid"] is False
    assert payload["draft_hash"] != draft["draft_hash"]


def test_run_returns_202_then_audited_result_and_list_entry(tmp_path):
    def runner(*, run_request, mandate, cancel_requested):
        assert mandate["status"] == "CONFIRMED"
        assert cancel_requested() is False
        return {
            "status": "ready",
            "tradable": True,
            "weights": {"000001.SZ": 0.02, "000002.SZ": 0.02},
            "active_weights": {"000001.SZ": 0.01, "000002.SZ": -0.01},
            "solver": {"name": "CLARABEL", "status": "optimal", "certified": True},
            "fallback_used": False,
        }

    app, _ = make_app(
        tmp_path,
        llm_client=StaticLLM(valid_llm_payload()),
        runner=runner,
    )
    client = app.test_client()
    draft = interpret(client)
    confirmed = confirm(client, draft)
    response = client.post(
        "/api/optimizer/runs",
        json={
            "draft_id": draft["draft_id"],
            "draft_hash": confirmed["draft_hash"],
            "run_name": "中证500组合优化",
        },
    )

    assert response.status_code == 202
    created = response.get_json()
    assert created["status"] == "queued"
    assert created["internal_status"] == "QUEUED"
    completed = wait_for(client, created["run_id"], {"completed"})
    assert completed["result"]["solver"]["certified"] is True
    assert completed["result"]["weights"]["000001.SZ"] == 0.02
    states = [item["state"] for item in completed["audit"]]
    assert states[-3:] == ["SOLVING", "SOLVED", "AUDITED"]

    listed = client.get("/api/optimizer/runs?limit=10").get_json()["runs"]
    assert listed[0]["run_id"] == created["run_id"]
    assert listed[0]["status"] == "completed"
    assert listed[0]["internal_status"] == "AUDITED"


def test_blocked_runner_never_returns_weights_or_orders(tmp_path):
    def blocked_runner(*, run_request, mandate, cancel_requested):
        return {
            "status": "blocked",
            "tradable": False,
            "blocked_stage": "solver_availability",
            "reason": "certified_solver_clarabel_unavailable",
            "weights": {"forbidden": 1.0},
            "nested": {"target_weights": {"forbidden": 1.0}},
            "transactions": [{"code": "forbidden"}],
        }

    app, _ = make_app(
        tmp_path,
        llm_client=StaticLLM(valid_llm_payload()),
        runner=blocked_runner,
    )
    client = app.test_client()
    draft = interpret(client)
    confirmed = confirm(client, draft)
    created = client.post(
        "/api/optimizer/runs",
        json={"draft_id": draft["draft_id"], "draft_hash": confirmed["draft_hash"]},
    ).get_json()

    blocked = wait_for(client, created["run_id"], {"blocked"})
    assert blocked["internal_status"] == "BLOCKED_SOLVER"
    assert blocked["result"]["status"] == "BLOCKED_SOLVER"
    assert blocked["result"]["tradable"] is False
    assert blocked["result"]["fallback_used"] is False
    assert_no_solution_fields(blocked["result"])


def test_cancel_is_persistent_and_discards_late_runner_result(tmp_path):
    started = threading.Event()

    def slow_runner(*, run_request, mandate, cancel_requested):
        started.set()
        deadline = time.time() + 2.0
        while time.time() < deadline and not cancel_requested():
            time.sleep(0.005)
        return {
            "status": "ready",
            "tradable": True,
            "weights": {"must_not_be_persisted": 1.0},
        }

    app, _ = make_app(
        tmp_path,
        llm_client=StaticLLM(valid_llm_payload()),
        runner=slow_runner,
    )
    client = app.test_client()
    draft = interpret(client)
    confirmed = confirm(client, draft)
    created = client.post(
        "/api/optimizer/runs",
        json={"draft_id": draft["draft_id"], "draft_hash": confirmed["draft_hash"]},
    ).get_json()
    assert started.wait(timeout=1.0)

    cancel = client.post(f"/api/optimizer/runs/{created['run_id']}/cancel")
    assert cancel.status_code == 202
    assert cancel.get_json()["status"] == "cancelled"
    final = wait_for(client, created["run_id"], {"cancelled"})
    assert final["result"] is None
    assert final["cancel_requested"] is True


def ui_base_config() -> dict:
    return {
        "universe": {
            "code": "000905.SH",
            "holdings": 50,
            "rebalance_frequency": "monthly",
            "score_source": "causal_rolling_icir_neutral_score",
        },
        "objective": {
            "alpha_scale": 1.0,
            "risk_aversion": 8.0,
            "turnover_penalty": 1.0,
        },
        "holdings": {"min_weight": 0.005, "max_weight": 0.05},
        "industry": {"max_active_deviation": 0.02},
        "style": {
            "max_abs_exposure": 0.20,
            "size": 0.20,
            "value": 0.20,
            "momentum": 0.20,
            "liquidity": 0.20,
        },
        "active_risk": {
            "tracking_error_limit": 0.04,
            "max_active_weight": 0.01,
        },
        "trading": {
            "turnover_limit": 0.20,
            "transaction_cost_bps": 10.0,
        },
        "liquidity": {"max_adv_participation": 0.05},
        "lists": {"include": "", "exclude": ""},
    }


def test_structured_validation_id_is_tamper_evident(tmp_path):
    def runner(*, run_request, mandate, cancel_requested):
        return {
            "status": "ready",
            "tradable": True,
            "weights": {"000001.SZ": 1.0},
            "solver": {"name": "CLARABEL", "certified": True},
        }

    app, service = make_app(
        tmp_path,
        llm_client=StaticLLM(valid_llm_payload()),
        runner=runner,
    )
    client = app.test_client()
    interpreted = client.post(
        "/api/optimizer/constraints/interpret",
        json={
            "mode": "joint_cardinality",
            "instruction": RAW_REQUEST,
            "base_config": ui_base_config(),
            "universe": "000905.SH",
        },
    ).get_json()
    assert len(interpreted["constraints"]) == 2
    assert interpreted["constraints"][0]["category"] == "holdings"
    assert interpreted["mandate"]["mode"] == "joint_cardinality"
    assert "candidate_set_id" not in interpreted["mandate"]["objective"]

    validated = client.post(
        "/api/optimizer/constraints/validate",
        json={
            "mode": "joint_cardinality",
            "base_config": ui_base_config(),
            "constraints": interpreted["constraints"],
            "universe": "000905.SH",
            "score_source": "causal_rolling_icir_neutral_score",
        },
    )
    assert validated.status_code == 200
    draft_proof = validated.get_json()
    assert draft_proof["feasible"] is True, draft_proof
    assert draft_proof["status"] == "AWAITING_CONFIRMATION"
    assert draft_proof["validation_id"] is None
    assert draft_proof["confirmation_valid"] is False
    assert draft_proof["solver_policy"]["mode"] == "joint_cardinality"
    assert draft_proof["solver_policy"]["hybrid_route_ready"] is True
    assert draft_proof["solver_policy"]["candidate_set_must_be_frozen_before_solver"] is False
    assert draft_proof["solver_policy"]["semantic_fallback_allowed"] is False

    confirmed = client.post(
        "/api/optimizer/constraints/validate",
        json={
            "draft_id": draft_proof["draft_id"],
            "action": "confirm",
            "expected_draft_hash": draft_proof["draft_hash"],
            "actor": "qa_reviewer",
        },
    )
    assert confirmed.status_code == 200
    proof = confirmed.get_json()
    assert proof["status"] == "CONFIRMED"
    assert proof["confirmation_valid"] is True
    assert proof["validation_id"]
    assert service.store.find_draft_by_confirmation_hash(
        proof["validation_id"]
    )["draft_id"] == proof["draft_id"]

    tampered_config = copy.deepcopy(proof["normalized_config"])
    tampered_config["holdings"]["max_weight"] = 0.08
    rejected = client.post(
        "/api/optimizer/runs",
        json={
            "config": tampered_config,
            "constraints": proof["normalized_constraints"],
            "validation_id": proof["validation_id"],
            "universe": "000905.SH",
            "score_source": "causal_rolling_icir_neutral_score",
        },
    )
    assert rejected.status_code == 409
    assert rejected.get_json()["message"] == "validated_configuration_changed"

    accepted = client.post(
        "/api/optimizer/runs",
        json={
            "config": proof["normalized_config"],
            "constraints": proof["normalized_constraints"],
            "validation_id": proof["validation_id"],
            "universe": "000905.SH",
            "score_source": "causal_rolling_icir_neutral_score",
        },
    )
    assert accepted.status_code == 202
    assert accepted.get_json()["status"] == "queued"


def test_frontend_result_contract_has_four_real_series_and_audits():
    mandate = valid_llm_payload()
    result = {
        "status": "ready",
        "tradable_period_count": 1,
        "evaluated_period_count": 1,
        "carried_period_count": 0,
        "blocked_periods": 0,
        "rebalance_blocked_periods": 0,
        "requested_window_performance_status": (
            "valid_complete_requested_window"
        ),
        "curve_status": "formal_contiguous",
        "curves": [{
            "signal_date": "20260130",
            "benchmark_nav": 1.01,
            "direct_nav": 1.02,
            "same_support_nav": 1.025,
            "optimized_nav": 1.03,
        }],
        "metrics": {
            "performance_status": "valid_complete_requested_window",
            "requested_window_performance_status": (
                "valid_complete_requested_window"
            ),
            "formal_metrics_valid": True,
            "continuity": {
                "window_start": "20260130",
                "window_end": "20260130",
                "window_period_rows": 1,
                "requested_calendar_months": 1,
                "evaluated_periods": 1,
                "complete_return_periods": 1,
                "gap_periods": [],
                "missing_calendar_months": [],
                "all_periods_have_complete_benchmark_and_three_portfolios": True,
            },
            "diagnostic_contiguous_segments": [{
                "start": "20260130",
                "end": "20260130",
                "periods": 1,
                "diagnostic_only": True,
            }],
            "longest_contiguous_segment": {
                "start": "20260130",
                "end": "20260130",
                "periods": 1,
                "diagnostic_only": True,
            },
            "benchmark": {"annual_return": 0.10},
            "direct": {"annual_return": 0.12, "average_turnover": 0.25},
            "same_support_score_weighted": {
                "annual_return": 0.13,
                "average_turnover": 0.22,
            },
            "optimized": {
                "annual_return": 0.15,
                "information_ratio": 1.2,
                "tracking_error": 0.04,
                "average_turnover": 0.18,
            },
        },
        "periods": [{
            "signal_date": "20260130",
            "phase": "test_report_only",
            "optimizer_result": {
                "status": "ready",
                "tradable": True,
                "requested": {
                    "selection": {
                        "support_search": {
                            "phase_i_linear_solver": "SCIPY_HIGHS_MILP",
                            "selected_strategy": (
                                "highs_milp_joint_linear_support"
                            ),
                            "attempt_count": 2,
                            "attempt_limit": 64,
                            "no_good_cut_per_rejected_support": True,
                            "heuristic_support_fallback_used": False,
                            "attempts": [
                                {
                                    "status": "rejected_by_clarabel_socp",
                                    "linear_diagnostics": {
                                        "max_constraint_violation": 2e-9,
                                    },
                                },
                                {
                                    "status": "certified",
                                    "linear_diagnostics": {
                                        "max_constraint_violation": 1e-9,
                                    },
                                },
                            ],
                        },
                    },
                },
                "weights": {"000001.SZ": 0.03, "000002.SZ": 0.0},
                "active_weights": {"000001.SZ": 0.01, "000002.SZ": -0.01},
                "transactions": [{
                    "ts_code": "000001.SZ",
                    "previous_weight": 0.02,
                    "target_weight": 0.03,
                    "trade_weight": 0.01,
                    "side": "buy",
                }],
                "realized": {
                    "industry_active_exposure": {"??": 0.01},
                    "style_active_exposure": {"style_size": -0.02},
                },
                "slack": {"tracking_error": 0.01},
                "dual": {"tracking_error": 0.5},
                "solver": {
                    "name": "CLARABEL",
                    "status": "optimal",
                    "certified": True,
                    "objective": 1.5,
                    "max_constraint_violation": 1e-9,
                    "fallback_used": False,
                },
            },
        }],
        "fallback_used": False,
    }
    output = optimizer_backend.OptimizerBackendService._frontend_strategy_result(
        result, mandate
    )
    assert output["tradable"] is True
    assert set(output["strategies"]) == {
        "benchmark", "direct_score_top50",
        "same_support_score_weighted", "constrained_optimizer",
    }
    assert output["formal_metrics_valid"] is True
    assert output["series_scope"] == "formal_requested_window"
    assert output["strategies"]["same_support_score_weighted"]["nav"] == [{
        "date": "20260130", "nav": 1.025,
    }]
    assert output["metrics"]["constrained_optimizer"]["annual_excess_return"] == pytest.approx(0.05)
    assert output["weights"][0]["benchmark_weight"] == pytest.approx(0.02)
    assert len(output["exposures"]) == 2
    assert output["slack"]["tracking_error"] == 0.01
    assert output["dual"]["tracking_error"] == 0.5
    assert output["transactions"][0]["code"] == "000001.SZ"
    assert output["solver"]["max_violation"] == pytest.approx(1e-9)
    assert output["solver"]["phase_i"]["name"] == "SCIPY_HIGHS_MILP"
    assert output["solver"]["phase_i"]["attempt_count"] == 2
    assert output["solver"]["phase_i"]["no_good_cuts_applied"] == 1
    assert output["solver"]["phase_i"][
        "max_linear_constraint_violation"
    ] == pytest.approx(2e-9)
    assert output["solver"]["phase_ii"]["name"] == "CLARABEL"
    assert output["solver"]["phase_ii"]["certified"] is True

def test_frontend_result_contract_limits_invalid_window_to_diagnostic_segment():
    result = {
        "status": "partial",
        "tradable_period_count": 1,
        "evaluated_period_count": 2,
        "carried_period_count": 1,
        "blocked_periods": 1,
        "rebalance_blocked_periods": 1,
        "requested_window_performance_status": (
            "invalid_incomplete_requested_window"
        ),
        "curve_status": "diagnostic_non_contiguous",
        "curves": [
            {
                "signal_date": date,
                "benchmark_nav": benchmark,
                "direct_nav": direct,
                "same_support_nav": same_support,
                "optimized_nav": optimized,
            }
            for date, benchmark, direct, same_support, optimized in (
                ("20260130", 1.00, 1.00, 1.00, 1.00),
                ("20260227", 1.01, 1.02, 1.015, 1.025),
                ("20260331", 1.02, 1.03, 1.025, 1.04),
            )
        ],
        "metrics": {
            "performance_status": "invalid_incomplete_requested_window",
            "requested_window_performance_status": (
                "invalid_incomplete_requested_window"
            ),
            "formal_metrics_valid": False,
            "continuity": {
                "window_start": "20260130",
                "window_end": "20260331",
                "window_period_rows": 3,
                "requested_calendar_months": 3,
                "evaluated_periods": 2,
                "complete_return_periods": 2,
                "gap_periods": ["20260130"],
                "missing_calendar_months": [],
                "all_periods_have_complete_benchmark_and_three_portfolios": False,
            },
            "diagnostic_contiguous_segments": [{
                "start": "20260227",
                "end": "20260331",
                "periods": 2,
                "diagnostic_only": True,
            }],
            "longest_contiguous_segment": {
                "start": "20260227",
                "end": "20260331",
                "periods": 2,
                "diagnostic_only": True,
            },
            "benchmark": {"annual_return": 9.9, "sharpe": 9.9},
            "direct": {
                "annual_return": 9.9, "sharpe": 9.9,
                "information_ratio": 9.9, "average_turnover": 0.3,
            },
            "same_support_score_weighted": {
                "annual_return": 9.9, "sharpe": 9.9,
                "information_ratio": 9.9, "average_turnover": 0.2,
            },
            "optimized": {
                "annual_return": 9.9, "annual_volatility": 9.9,
                "sharpe": 9.9, "information_ratio": 9.9,
                "tracking_error": 9.9, "average_turnover": 0.1,
            },
        },
        "periods": [
            {
                "signal_date": "20260130",
                "phase": "validation",
                "status": "blocked",
                "reason": "score_period_missing",
                "evaluation_included": False,
            },
            {
                "signal_date": "20260227",
                "phase": "validation",
                "status": "carried",
                "reason": "factor_panel_incomplete",
                "rebalance_blocked": True,
                "carry_status": "held_and_marked_to_market",
                "evaluation_included": True,
            },
            {
                "signal_date": "20260331",
                "phase": "validation",
                "status": "ready",
                "optimizer_result": {
                    "status": "ready",
                    "tradable": True,
                    "weights": {"000001.SZ": 1.0},
                    "active_weights": {"000001.SZ": 0.0},
                    "solver": {
                        "name": "CLARABEL",
                        "status": "optimal",
                        "certified": True,
                        "max_constraint_violation": 1e-9,
                        "fallback_used": False,
                    },
                },
            },
        ],
        "fallback_used": False,
    }

    output = optimizer_backend.OptimizerBackendService._frontend_strategy_result(
        result, valid_llm_payload()
    )

    assert output["formal_metrics_valid"] is False
    assert output["requested_window_performance_status"] == (
        "invalid_incomplete_requested_window"
    )
    assert output["series_scope"] == (
        "diagnostic_longest_contiguous_segment"
    )
    for strategy in output["strategies"].values():
        assert [row["date"] for row in strategy["nav"]] == [
            "20260227", "20260331",
        ]
        assert strategy["metrics"]["annual_return"] is None
        assert strategy["metrics"]["sharpe"] is None
        assert strategy["metrics"]["information_ratio"] is None
    optimized = output["metrics"]["constrained_optimizer"]
    assert optimized["annual_volatility"] is None
    assert optimized["annual_excess_return"] is None
    assert optimized["tracking_error"] is None
    assert optimized["turnover"] == pytest.approx(0.1)
    audit = output["backtest_audit"]
    assert audit["longest_contiguous_segment"] == {
        "start": "20260227",
        "end": "20260331",
        "periods": 2,
        "diagnostic_only": True,
    }
    assert audit["carried_period_count"] == 1
    assert audit["rebalance_blocked_periods"] == 1
    assert len(audit["blocked_events"]) == 2
    assert audit["blocked_reason_counts"] == {
        "factor_panel_incomplete": 1,
        "score_period_missing": 1,
    }





def test_ui_control_contract_maps_cost_penalty_and_score_source():
    config = ui_base_config()
    config["objective"].update(
        cost_penalty=3.0, turnover_penalty=2.0
    )
    values, strategy, audit = (
        optimizer_backend.OptimizerBackendService._ui_optimizer_values(
            config, valid_llm_payload()
        )
    )

    assert values["transaction_cost_rate"] == pytest.approx(0.001)
    assert values["turnover_l1_penalty"] == pytest.approx(0.004)
    assert values["alpha_weight"] == pytest.approx(1.0)
    assert values["score_target_penalty"] == pytest.approx(3.0 / 1.8)
    assert strategy["transaction_cost_rate"] == pytest.approx(0.001)
    assert strategy["score_name"] == "causal_rolling_icir_neutral_score"
    assert audit["status"] == "validated"
    assert "objective.cost_penalty" in audit["applied"]


@pytest.mark.parametrize(
    ("section", "field", "value", "reason"),
    [
        ("style", "volatility", 0.10, "style.volatility"),
        ("industry", "max_total_active", 0.30, "industry.max_total_active"),
        ("trading", "no_trade_band", 0.001, "trading.no_trade_band"),
        ("liquidity", "min_daily_amount_million", 50.0, "liquidity.min_daily_amount_million"),
        ("lists", "freeze", "000001.SZ", "lists.freeze"),
        ("active_risk", "covariance_model", "hybrid", "active_risk.covariance_model"),
        ("liquidity", "exclude_suspended", False, "liquidity.exclude_suspended"),
        ("liquidity", "exclude_limit_locked", False, "liquidity.exclude_limit_locked"),
    ],
)
def test_ui_control_contract_blocks_unsupported_values(
    section, field, value, reason
):
    config = ui_base_config()
    config.setdefault(section, {})[field] = value
    with pytest.raises(ValueError, match=reason.replace(".", r"\.")):
        optimizer_backend.OptimizerBackendService._ui_optimizer_values(
            config, valid_llm_payload()
        )


def test_ui_control_contract_maps_beta_style_bound():
    config = ui_base_config()
    config["style"]["beta"] = 0.10
    values, strategy, audit = (
        optimizer_backend.OptimizerBackendService._ui_optimizer_values(
            config, valid_llm_payload()
        )
    )

    assert values["style_bounds"]["style_beta"] == pytest.approx((-0.10, 0.10))
    assert strategy["optimizer_style_columns"][-1] == "style_beta"
    assert audit["status"] == "validated"


def test_ui_control_contract_blocks_unavailable_llm_style_factor():
    mandate = valid_llm_payload()
    mandate["constraints"].append({
        "id": "style.beta",
        "type": "style",
        "scope": {"metric": "active_exposure", "style": "beta"},
        "lower": -0.10,
        "upper": 0.10,
        "unit": "exposure",
        "hard": True,
    })
    with pytest.raises(ValueError, match="style_factor_unavailable"):
        optimizer_backend.OptimizerBackendService._ui_optimizer_values(
            ui_base_config(), mandate
        )


def test_sealed_test_gate_vetoes_negative_oos_without_using_it_for_ranking():
    result = {
        "backtest_audit": {
            "metrics_by_split": {
                "test_report_only": {
                    "optimized": {
                        "periods": 29,
                        "annual_return": -0.058,
                        "sharpe": -0.21,
                        "information_ratio": -1.56,
                    },
                    "benchmark": {"annual_return": 0.222},
                },
            },
        },
    }
    passed, audit = optimizer_backend._sealed_test_publication_gate(result)

    assert passed is False
    assert audit["status"] == "production_vetoed"
    assert audit["role"] == "post_selection_production_veto_only"
    assert audit["used_for_candidate_ranking"] is False
    assert audit["periods"] == 29
    assert audit["annual_excess_return"] == pytest.approx(-0.28)
    assert audit["sharpe"] == pytest.approx(-0.21)
    assert audit["information_ratio"] == pytest.approx(-1.56)



def optimizer_plan_options_payload() -> dict:
    return {
        "schema_version": "OptimizationPlanOptions/v1",
        "options": [
            {
                "id": "baseline_constrained_optimizer",
                "name": "baseline_constrained_optimizer",
                "profile": "score_plus_active_risk",
                "summary": "打分、风险、换手、行业风格的联合优化方案。",
                "objective_equation": "max alpha'w - lambda_te (w-b)'Sigma(w-b) - lambda_to |w-w_prev|_1",
                "objective_terms": ["alpha_score", "tracking_error", "turnover"],
                "default_parameters": {
                    "holding": {"target_count": 50, "min_weight": 0.005, "max_weight": 0.05},
                    "risk": {"tracking_error_limit": 0.06, "industry_bound": 0.02},
                },
                "added_constraints": [
                    {"name": "exact_cardinality", "type": "holding", "formula": "sum_i z_i = 50"},
                    {"name": "tracking_error", "type": "active_risk", "formula": "sqrt((w-b)'Sigma(w-b)) <= 0.06"},
                ],
                "constraint_equations": ["sum_i w_i=1", "0.005z_i<=w_i<=0.05z_i", "TE<=0.06"],
                "solver_steps": ["HiGHS MILP", "Clarabel SOCP", "audit constraints"],
                "expected_tradeoff": "在保留得分暴露的同时压低跟踪误差和换手。",
                "mandate_request": RAW_REQUEST + " 选择 baseline_constrained_optimizer，保留50只、行业偏离、风格、TE和换手约束。",
            }
        ],
    }


class PlanThenMandateLLM:
    def __init__(self, plan_payload: dict, mandate_payload: dict) -> None:
        self.plan_payload = plan_payload
        self.mandate_payload = mandate_payload
        self.calls = []

    def __call__(self, *, system_prompt, user_payload):
        self.calls.append(copy.deepcopy(user_payload))
        if user_payload.get("phase") == "plan_options":
            return copy.deepcopy(self.plan_payload)
        return copy.deepcopy(self.mandate_payload)


def test_plan_options_api_returns_selectable_equation_flow(tmp_path):
    llm = PlanThenMandateLLM(optimizer_plan_options_payload(), valid_llm_payload())
    app, _ = make_app(tmp_path, llm_client=llm)
    client = app.test_client()

    response = client.post(
        "/api/optimizer/constraints/plans",
        json={
            "mode": "joint_cardinality",
            "instruction": RAW_REQUEST,
            "base_config": ui_base_config(),
            "universe": "000905.SH",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "AWAITING_PLAN_SELECTION"
    assert payload["planner"] == "ai_router_mandate_plan_options"
    assert payload["weights_emitted"] is False
    assert payload["fallback_used"] is False
    assert len(payload["options"]) == 1
    assert payload["options"][0]["objective_equation"].startswith("max alpha")
    assert payload["options"][0]["mandate_request"]
    assert llm.calls[0]["phase"] == "plan_options"
    assert_no_solution_fields(payload)


def test_selected_plan_can_be_compiled_by_existing_interpret_api(tmp_path):
    llm = PlanThenMandateLLM(optimizer_plan_options_payload(), valid_llm_payload())
    app, _ = make_app(tmp_path, llm_client=llm)
    client = app.test_client()
    plan_payload = client.post(
        "/api/optimizer/constraints/plans",
        json={"mode": "joint_cardinality", "instruction": RAW_REQUEST, "base_config": ui_base_config()},
    ).get_json()
    option = plan_payload["options"][0]

    interpreted = client.post(
        "/api/optimizer/constraints/interpret",
        json={
            "mode": "joint_cardinality",
            "instruction": option["mandate_request"],
            "selected_plan": option,
            "base_config": ui_base_config(),
            "universe": "000905.SH",
        },
    )

    assert interpreted.status_code == 200
    payload = interpreted.get_json()
    assert payload["status"] == "AWAITING_CONFIRMATION"
    assert payload["compiled_from_selected_plan"] is True
    assert len(payload["constraints"]) >= 8
    assert {item["_constraint_payload"]["type"] for item in payload["constraints"]} >= {
        "holding",
        "industry",
        "style",
        "active_risk",
        "trading",
        "liquidity",
    }
    assert len(llm.calls) == 1
    assert llm.calls[-1]["phase"] == "plan_options"


def test_selected_plan_parameter_updates_flow_into_mandate(tmp_path):
    llm = PlanThenMandateLLM(optimizer_plan_options_payload(), valid_llm_payload())
    app, _ = make_app(tmp_path, llm_client=llm)
    client = app.test_client()
    option = {
        "id": "plan_parameter_updates",
        "name": "plan_parameter_updates",
        "parameter_updates": {
            "target_count": 50,
            "min_weight": 0.004,
            "max_weight": 0.040,
            "max_active_weight": 0.025,
            "industry_bound": 0.015,
            "style_bound": 0.10,
            "tracking_error_limit": 0.055,
            "turnover_limit": 0.30,
            "max_adv_participation": 0.04,
            "whitelist": ["000001.SZ", "600000"],
            "blacklist": "000002.SZ,000003.SZ",
        },
    }

    interpreted = client.post(
        "/api/optimizer/constraints/interpret",
        json={
            "mode": "joint_cardinality",
            "instruction": RAW_REQUEST,
            "selected_plan": option,
            "base_config": ui_base_config(),
            "universe": "000905.SH",
        },
    )

    assert interpreted.status_code == 200
    payload = interpreted.get_json()
    assert payload["status"] == "AWAITING_CONFIRMATION"
    constraints = {
        item["_constraint_payload"]["id"]: item["_constraint_payload"]
        for item in payload["constraints"]
    }
    assert constraints["holding.security_weight"]["lower"] == pytest.approx(0.004)
    assert constraints["holding.security_weight"]["upper"] == pytest.approx(0.040)
    assert constraints["holding.active_security_weight"]["upper"] == pytest.approx(0.025)
    assert constraints["industry.active_exposure.all"]["upper"] == pytest.approx(0.015)
    for style_name in ("size", "value", "momentum", "liquidity"):
        assert constraints[f"style.active_exposure.{style_name}"]["upper"] == pytest.approx(0.10)
    assert constraints["active_risk.tracking_error"]["upper"] == pytest.approx(0.055)
    assert constraints["trading.one_way_turnover"]["upper"] == pytest.approx(0.30)
    assert constraints["liquidity.adv_participation"]["upper"] == pytest.approx(0.04)
    assert constraints["list.whitelist"]["scope"]["security_set"] == ["000001.SZ", "600000.SH"]
    assert constraints["list.blacklist"]["scope"]["security_set"] == ["000002.SZ", "000003.SZ"]
    assert len(llm.calls) == 0


def test_plan_options_api_blocks_llm_weight_fields(tmp_path):
    bad = optimizer_plan_options_payload()
    bad["options"][0]["target_weights"] = {"000001.SZ": 0.1}
    app, _ = make_app(tmp_path, llm_client=PlanThenMandateLLM(bad, valid_llm_payload()))

    response = app.test_client().post(
        "/api/optimizer/constraints/plans",
        json={"mode": "joint_cardinality", "instruction": RAW_REQUEST, "base_config": ui_base_config()},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "BLOCKED_SCHEMA"
    assert payload["fallback_used"] is False
    assert payload["options"] == []
    assert any("direct weight" in item for item in payload["errors"])
