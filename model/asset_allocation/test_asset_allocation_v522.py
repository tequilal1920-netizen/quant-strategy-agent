from __future__ import annotations

import copy

import numpy as np
import pytest

import asset_allocation_v522 as engine


def test_full_transaction_cost_includes_quadratic_impact() -> None:
    config = engine.ResearchConfigV522()
    change = np.asarray([0.10, -0.05, -0.02, -0.03])
    linear, quadratic, total = engine.transaction_cost_v522(change, config)
    expected_linear = float(
        (np.asarray(config.transaction_cost_bps) / 10000.0) @ np.abs(change)
    )
    expected_quadratic = float(
        0.5 * np.asarray(config.quadratic_cost) @ (change * change)
    )
    assert linear == expected_linear
    assert quadratic == expected_quadratic
    assert total == expected_linear + expected_quadratic
    assert quadratic > 0.0


def test_retrospective_test_cannot_change_promotion() -> None:
    config = engine.ResearchConfigV522(
        minimum_validation_returns=1,
        future_paper_holdout_certified=False,
    )
    selected = {
        "metrics": {
            "validation": {
                "months": 12,
                "sharpe": 2.0,
                "skewness": 0.0,
                "excess_kurtosis": 0.0,
                "annual_excess_return": 0.02,
                "information_ratio": 0.8,
            },
            "test": {
                "months": 24,
                "sharpe": -100.0,
                "annual_return": -0.5,
                "annual_excess_return": -0.5,
                "information_ratio": -10.0,
            },
        }
    }
    selection = {"eligible_count": 1}
    benchmark = {"metrics": {"test": {"sharpe": 1.0}}}
    registry = {"production_ready": True}
    macro = {"pit_verified_fraction": 1.0}
    first = engine.promotion_gate_v522(
        selected, selection, benchmark, registry, macro, "benchmark_relative", config
    )
    changed = copy.deepcopy(selected)
    changed["metrics"]["test"].update(
        {
            "sharpe": 100.0,
            "annual_return": 10.0,
            "annual_excess_return": 10.0,
            "information_ratio": 100.0,
        }
    )
    second = engine.promotion_gate_v522(
        changed, selection, benchmark, registry, macro, "benchmark_relative", config
    )
    assert first["status"] == second["status"] == "blocked"
    assert first["checks"] == second["checks"]
    assert first["probabilistic_sharpe_ratio_validation"] == second["probabilistic_sharpe_ratio_validation"]
    assert first["retrospective_test_enters_checks"] is False
    assert first["manual_holdout_certification_accepted"] is False
    assert first["future_holdout_validation"]["status"] == "not_implemented_fail_closed"
    assert "future_pristine_paper_holdout" not in first["checks"]
    assert "future_pristine_paper_holdout" not in first["failed"]
    statistical_first = first["statistical_evidence"]
    statistical_second = second["statistical_evidence"]
    assert statistical_first["status"] == statistical_second["status"] == "warning"
    assert statistical_first["checks"] == statistical_second["checks"]
    assert "future_pristine_paper_holdout" in statistical_first["failed"]
    assert statistical_first["retrospective_test_enters_checks"] is False
    assert statistical_first["retrospective_test_is_report_only"] is True
    assert (
        statistical_first["retrospective_test_summary"]
        != statistical_second["retrospective_test_summary"]
    )


def test_user_authorized_service_pass_can_coexist_with_statistical_warning() -> None:
    config = engine.ResearchConfigV522(
        minimum_train_returns=1,
        minimum_validation_returns=1,
        future_paper_holdout_certified=False,
    )
    selected = {
        "metrics": {
            "train": {"months": 12, "sharpe": 1.5},
            "validation": {
                "months": 12,
                "sharpe": 2.0,
                "skewness": 0.0,
                "excess_kurtosis": 0.0,
                "annual_excess_return": 0.02,
                "information_ratio": 0.8,
            },
            "test": {
                "months": 24,
                "sharpe": -100.0,
                "annual_return": -0.5,
                "annual_excess_return": -0.5,
                "information_ratio": -10.0,
            },
        }
    }
    selection = {"eligible_count": 1, "selection_uses_test": False}
    benchmark = {
        "metrics": {
            "train": {"months": 12, "sharpe": 1.0},
            "validation": {"months": 12, "sharpe": 1.0},
            "test": {"sharpe": 1.0},
        }
    }
    result = engine.promotion_gate_v522(
        selected,
        selection,
        benchmark,
        {"production_ready": True},
        {"pit_verified_fraction": 1.0},
        "benchmark_relative",
        config,
    )

    assert result["status"] == "passed"
    assert result["failed"] == []
    assert all(result["checks"].values())
    assert "future_pristine_paper_holdout" not in result["checks"]
    assert result["retrospective_test_enters_checks"] is False
    assert result["retrospective_test_is_report_only"] is True
    statistical = result["statistical_evidence"]
    assert statistical["status"] == "warning"
    assert "future_pristine_paper_holdout" in statistical["failed"]
    assert statistical["effect_on_user_authorized_deployment"] == "warning_only"


def test_manual_future_holdout_certification_is_forbidden() -> None:
    config = engine.ResearchConfigV522(
        future_paper_holdout_certified=True,
        future_paper_holdout_id="arbitrary-string-must-not-be-trusted",
    )
    with pytest.raises(
        ValueError,
        match="manual_future_holdout_certification_forbidden",
    ):
        config.validate()


def test_equal_strength_is_reported_as_a_tie() -> None:
    payload = {
        "rows": {
            "equity": {"composite_strength": 1.0, "signal_probability": 0.8},
            "bond": {"composite_strength": -0.5, "signal_probability": 0.2},
            "gold": {"composite_strength": -0.5, "signal_probability": 0.2},
            "commodity": {"composite_strength": 0.2, "signal_probability": 0.6},
        }
    }
    result = engine.apply_tie_policy_v522(payload)
    assert result["strongest_assets"] == ["equity"]
    assert result["weakest_asset"] is None
    assert result["weakest_assets"] == ["bond", "gold"]
    assert result["rows"]["bond"]["strength_rank"] == 3
    assert result["rows"]["gold"]["strength_rank"] == 3
    assert result["rows"]["bond"]["strength_label_cn"] == "并列最弱"
    assert result["rows"]["gold"]["signal_probability_calibrated"] is False


def test_absolute_candidate_selection_ignores_test() -> None:
    config = engine.ResearchConfigV522(
        minimum_train_returns=1, minimum_validation_returns=1
    )
    base = {
        "train": {"months": 12, "annual_return": 0.05, "sharpe": 0.8},
        "validation": {
            "months": 12,
            "annual_return": 0.06,
            "sharpe": 0.9,
            "average_turnover": 0.01,
        },
        "test": {"months": 12, "sharpe": -100.0},
    }
    first = {"spec": {"id": "A"}, "metrics": copy.deepcopy(base)}
    second = {"spec": {"id": "B"}, "metrics": copy.deepcopy(base)}
    second["metrics"]["train"]["sharpe"] = 1.1
    second["metrics"]["validation"]["sharpe"] = 1.2
    selected, audit = engine._raw._select_candidate_v52(
        [first, second], "absolute_no_benchmark", config
    )
    assert selected["spec"]["id"] == "B"
    first["metrics"]["test"]["sharpe"] = 10000.0
    second["metrics"]["test"]["sharpe"] = -10000.0
    selected_again, _ = engine._raw._select_candidate_v52(
        [first, second], "absolute_no_benchmark", config
    )
    assert selected_again["spec"]["id"] == "B"
    assert audit["selection_uses_test"] is False
