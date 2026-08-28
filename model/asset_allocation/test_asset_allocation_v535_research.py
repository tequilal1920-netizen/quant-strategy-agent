from __future__ import annotations

import copy

import numpy as np
import pytest

import backtest_asset_allocation_v53_stack as base
from backtest_asset_allocation_v535_stack import candidate_grid_v535


def _candidate(identifier: str, test_sharpe: float) -> dict:
    train = {
        "months": 24,
        "annual_excess_return": 0.01,
        "information_ratio": 0.50,
        "sharpe_improvement": 0.05,
        "max_active_drawdown": -0.01,
        "average_turnover": 0.02,
        "sharpe": 0.70,
    }
    validation = {
        "months": 12,
        "annual_excess_return": 0.008,
        "information_ratio": 0.45,
        "sharpe_improvement": 0.04,
        "max_active_drawdown": -0.008,
        "average_turnover": 0.015,
        "sharpe": 0.65,
    }
    return {
        "spec": {"id": identifier, "model_version": "benchmark_relative"},
        "metrics": {
            "train": train,
            "validation": validation,
            "test": {"months": 18, "sharpe": test_sharpe},
        },
        "pretest_calendar_years": {
            year: dict(train if year != "2024" else validation)
            for year in ("2022", "2023", "2024")
        },
        "returns": [
            {"month": "202301", "sample": "train"},
            {"month": "202501", "sample": "test"},
        ],
    }


def test_candidate_grid_is_predeclared_and_bounded_at_24() -> None:
    grid = candidate_grid_v535()
    assert len(grid) == 24
    assert len({row["id"] for row in grid}) == 24
    assert sum(row["model_version"] == "benchmark_relative" for row in grid) == 18
    assert sum(row["model_version"] == "absolute_no_benchmark" for row in grid) == 6


def test_selector_payload_physically_removes_test() -> None:
    payload = base.selection_payload(_candidate("A", 99.0))
    assert set(payload["metrics"]) == {"train", "validation"}
    assert all(row["sample"] != "test" for row in payload["pretest_returns"])


def test_selector_rejects_test_metrics_and_is_test_counterfactual_invariant() -> None:
    first = _candidate("A", -99.0)
    second = _candidate("B", 99.0)
    with pytest.raises(ValueError, match="selector_received_test"):
        base.select_pretest([first, second], "benchmark_relative")
    stripped = [base.selection_payload(first), base.selection_payload(second)]
    selected_before, board_before = base.select_pretest(stripped, "benchmark_relative")
    changed = copy.deepcopy([first, second])
    changed[0]["metrics"]["test"]["sharpe"] = 1.0e9
    changed[1]["metrics"]["test"]["sharpe"] = -1.0e9
    selected_after, board_after = base.select_pretest(
        [base.selection_payload(row) for row in changed], "benchmark_relative"
    )
    assert selected_before == selected_after
    assert board_before == board_after


def test_policy_internal_order_is_equity_bond_gold_commodity() -> None:
    np.testing.assert_allclose(base.POLICY_WEIGHTS_V53, [0.60, 0.15, 0.10, 0.15])
    assert base.ASSETS == ("equity", "bond", "gold", "commodity")
