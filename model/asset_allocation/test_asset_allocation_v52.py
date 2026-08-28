from __future__ import annotations

import math
from unittest.mock import patch

import numpy as np

import asset_allocation_v52 as engine
from asset_data_v5 import ASSET_ORDER_V5


def _month_add(year: int, month: int, offset: int) -> tuple[int, int]:
    ordinal = year * 12 + month - 1 + offset
    return ordinal // 12, ordinal % 12 + 1


def _synthetic_panel(count: int = 96):
    rng = np.random.default_rng(520811)
    prices = np.full(4, 100.0)
    panel = {asset: [] for asset in ASSET_ORDER_V5}
    macro = []
    for index in range(count):
        year, month = _month_add(2017, 1, index)
        key = f"{year:04d}{month:02d}"
        if index:
            common = 0.006 + 0.010 * math.sin(index / 9.0)
            shock = rng.normal(0.0, [0.030, 0.010, 0.022, 0.027])
            drift = np.asarray(
                [common, 0.003 - 0.20 * common, 0.004 - 0.08 * common, 0.003 + 0.18 * common]
            )
            prices *= 1.0 + drift + shock
        for position, asset in enumerate(ASSET_ORDER_V5):
            panel[asset].append(
                {"date": key + "28", "close": float(prices[position])}
            )
        release_year, release_month = _month_add(year, month, 1)
        macro.append(
            {
                "month": key,
                "observation_period": key,
                "available_time": f"{release_year:04d}{release_month:02d}15",
                "vintage": "first_release",
                "_pit_verified": True,
                "pmi_manufacturing": 50.0 + 2.0 * math.sin(index / 6.0),
                "pmi_composite": 50.5 + 1.5 * math.sin(index / 6.0),
                "cpi_national_yoy": 2.0 + 0.8 * math.cos(index / 8.0),
                "ppi_yoy": 1.0 + 2.0 * math.cos(index / 7.0),
                "m1_yoy": 7.0 + 2.5 * math.sin(index / 5.0),
                "m2_yoy": 8.0 + math.sin(index / 8.0),
                "sf_stock_endval": 100.0 * (1.0 + index / 120.0),
                "industrial_finished_goods_inventory_yoy": 5.0 + math.cos(index / 8.0),
                "industrial_revenue_yoy": 7.0 + math.sin(index / 8.0),
                "manufacturing_fai_yoy": 6.0 + math.sin(index / 10.0),
                "enterprise_medium_long_loan_yoy": 8.0 + math.cos(index / 10.0),
                "capacity_utilization": 75.0 + math.sin(index / 12.0),
                "industrial_profit_yoy": 7.0 + math.cos(index / 9.0),
                "equity_risk_premium": 3.0 + math.sin(index / 6.0),
                "stock_bond_relative_momentum": math.sin(index / 4.0),
                "source": "synthetic_test_only",
            }
        )
    return panel, macro


def _one_spec(mode: str) -> list[dict[str, object]]:
    return [
        {
            "id": "V52-REL-T01" if mode == "benchmark_relative" else "V52-ABS-T01",
            "model_version": mode,
            "half_life": 18.0,
            "diagonal_shrinkage": 0.35,
            "macro_blend_weight": 0.25,
            "risk_aversion": 4.0,
            "tau": 0.05,
            "uncertainty_penalty": 0.40,
            "anchor_penalty": 1.25,
        }
    ]


def test_policy_benchmark_uses_internal_equity_bond_gold_commodity_order() -> None:
    config = engine.ResearchConfigV52()
    config.validate()
    assert tuple(ASSET_ORDER_V5) == ("equity", "bond", "gold", "commodity")
    assert config.policy_benchmark_weights == (0.60, 0.15, 0.10, 0.15)
    assert config.policy_benchmark_weights != (0.60, 0.15, 0.15, 0.10)


def test_strategic_benchmark_is_not_equal_weight_and_uses_same_cost_path() -> None:
    config = engine.ResearchConfigV52(lookback_months=12)
    months = [f"{2019 + index // 12:04d}{index % 12 + 1:02d}" for index in range(30)]
    returns = np.tile(np.asarray([0.01, 0.002, 0.004, 0.006]), (30, 1))
    result = engine.strategic_benchmark_backtest_v52(months, returns, config)
    np.testing.assert_allclose(result["current_weights"], [0.60, 0.15, 0.10, 0.15])
    assert result["role"] == "primary_policy_benchmark_not_equal_weight"
    assert result["metrics"]["test"]["months"] >= 0
    assert result["returns"][0]["cost"] == 0.0


def test_policy_candidate_selection_never_reads_test_metrics() -> None:
    config = engine.ResearchConfigV52(
        minimum_train_returns=1, minimum_validation_returns=1
    )
    common = {
        "train": {
            "months": 12,
            "annual_return": 0.08,
            "sharpe": 0.8,
            "annual_excess_return": 0.02,
            "information_ratio": 0.5,
            "average_turnover": 0.03,
        },
        "validation": {
            "months": 12,
            "annual_return": 0.07,
            "sharpe": 0.7,
            "annual_excess_return": 0.01,
            "information_ratio": 0.4,
            "average_turnover": 0.03,
        },
        "test": {"months": 12, "sharpe": -50.0},
    }
    first = {"spec": {"id": "A"}, "metrics": common}
    second_metrics = {key: dict(value) for key, value in common.items()}
    second_metrics["validation"].update(
        {"sharpe": 0.9, "annual_excess_return": 0.03, "information_ratio": 0.8}
    )
    second = {"spec": {"id": "B"}, "metrics": second_metrics}
    selected, audit = engine._select_candidate_v52(
        [first, second], "benchmark_relative", config
    )
    assert selected["spec"]["id"] == "B"
    common["test"]["sharpe"] = 1000.0
    second_metrics["test"]["sharpe"] = -1000.0
    selected_again, _ = engine._select_candidate_v52(
        [first, second], "benchmark_relative", config
    )
    assert selected_again["spec"]["id"] == "B"
    assert audit["selection_uses_test"] is False


def test_end_to_end_v52_has_two_versions_policy_constraints_and_strength() -> None:
    panel, macro = _synthetic_panel(96)
    config = engine.ResearchConfigV52(
        train_end="202012",
        validation_end="202212",
        lookback_months=18,
        minimum_cycle_train=18,
        minimum_train_returns=12,
        minimum_validation_returns=6,
        minimum_test_returns=6,
        production_mode=False,
    )
    with patch.object(
        engine,
        "candidate_grid_v52",
        side_effect=lambda mode=None: _one_spec(str(mode)),
    ):
        snapshot = engine.build_snapshot_v52(
            macro,
            panel,
            config=config,
            generated_at="2026-08-11T00:00:00Z",
        )
    assert snapshot["schema_version"] == "5.2"
    assert snapshot["status"] == "research_only"
    assert snapshot["benchmark"]["weights"] == {
        "equity": 0.60,
        "bond": 0.15,
        "gold": 0.10,
        "commodity": 0.15,
    }
    assert snapshot["benchmark"]["equal_weight_is_primary_benchmark"] is False
    for key in (
        "strategic_benchmark",
        "benchmark_relative",
        "absolute_no_benchmark",
        "recommended",
    ):
        assert key in snapshot["allocations"]
        assert abs(sum(snapshot["allocations"][key]["weights"].values()) - 1.0) < 1.0e-8
    relative = snapshot["allocations"]["benchmark_relative"]
    policy = relative["metadata"]["policy_constraint_audit"]
    assert policy["active_share"] <= config.policy_max_active_share + 1.0e-8
    assert policy["annual_tracking_error"] <= config.policy_max_annual_tracking_error + 1.0e-8
    assert abs(sum(policy["active_weight"])) < 1.0e-8
    absolute = snapshot["allocations"]["absolute_no_benchmark"]
    assert absolute["metadata"]["policy_benchmark_used_in_model"] is False
    assert absolute["metadata"]["policy_benchmark"] is None
    decisions = snapshot["asset_decisions"]
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        assert set(decisions[mode]) == set(ASSET_ORDER_V5)
        labels = {row["strength_label_cn"] for row in decisions[mode].values()}
        assert labels == {"最强", "偏强", "偏弱", "最弱"}
        assert all(len(row["input_signals"]) >= 6 for row in decisions[mode].values())
    assert all(
        row["benchmark_weight"] is None
        for row in decisions["absolute_no_benchmark"].values()
    )
    assert "equal_weight" not in snapshot["backtest"]["strategies"]
    assert snapshot["backtest"]["selection_audit"]["selection_uses_test"] is False
    assert snapshot["v52_governance"]["absolute_model_reads_policy_benchmark"] is False
    assert snapshot["v52_governance"]["historical_test_pristine"] is False
    assert len(snapshot["model_hash"]) == 64


def test_v52_production_default_registry_remains_blocked() -> None:
    panel, macro = _synthetic_panel(30)
    config = engine.ResearchConfigV52(production_mode=True)
    try:
        engine.build_snapshot_v52(macro, panel, config=config)
    except ValueError as error:
        assert "v52_production_data_gate_failed" in str(error)
    else:  # pragma: no cover
        raise AssertionError("default D1/D2 registry must not enter production")
