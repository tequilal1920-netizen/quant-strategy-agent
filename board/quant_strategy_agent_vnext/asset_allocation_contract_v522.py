"""Fail-closed contracts shared by the formal schema-5.2.2 backends."""
from __future__ import annotations

import math
from typing import Any, Mapping


DISPLAY_STRATEGY_ID = "equal_weight_25"
DISPLAY_STRATEGY_ROLE = "nav_display_only_not_optimizer_input"
ASSET_ORDER = ("equity", "bond", "gold", "commodity")
TARGET_WEIGHT = 0.25
_TOLERANCE = 1e-12


def _fail(reason: str) -> None:
    raise ValueError(f"v522_equal_weight_contract_invalid:{reason}")


def _finite(value: Any, path: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        _fail(f"{path}_must_be_finite")
    if not math.isfinite(result):
        _fail(f"{path}_must_be_finite")
    return result


def _quarter(value: Any, path: str) -> float:
    result = _finite(value, path)
    if not math.isclose(result, TARGET_WEIGHT, rel_tol=0.0, abs_tol=_TOLERANCE):
        _fail(f"{path}_must_equal_0.25")
    return result


def equal_weight_display_benchmark_v522(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and derive the formal display-benchmark record.

    The function deliberately reads the real strategy object.  No fallback or
    hard-coded substitute is returned when the governed display series is
    missing or malformed.
    """

    backtest = snapshot.get("backtest")
    if not isinstance(backtest, Mapping):
        _fail("backtest_must_be_an_object")
    strategies = backtest.get("strategies")
    if not isinstance(strategies, Mapping):
        _fail("backtest.strategies_must_be_an_object")
    strategy = strategies.get(DISPLAY_STRATEGY_ID)
    if not isinstance(strategy, Mapping):
        _fail("backtest.strategies.equal_weight_25_must_be_an_object")

    if strategy.get("id") != DISPLAY_STRATEGY_ID:
        _fail("id_must_equal_equal_weight_25")
    if strategy.get("role") != DISPLAY_STRATEGY_ROLE:
        _fail("role_must_equal_nav_display_only_not_optimizer_input")
    if strategy.get("optimizer_input") is not False:
        _fail("optimizer_input_must_be_false")
    if strategy.get("active_return_reference") is not False:
        _fail("active_return_reference_must_be_false")

    current_weights = strategy.get("current_weights")
    if not isinstance(current_weights, (list, tuple)) or len(current_weights) != 4:
        _fail("current_weights_must_have_four_items")
    derived_weights = {
        asset: _quarter(current_weights[index], f"current_weights.{asset}")
        for index, asset in enumerate(ASSET_ORDER)
    }

    monthly_weights = strategy.get("weights")
    if not isinstance(monthly_weights, list) or not monthly_weights:
        _fail("weights_must_be_a_non_empty_monthly_list")
    for index, row in enumerate(monthly_weights):
        if not isinstance(row, Mapping):
            _fail(f"weights.{index}_must_be_an_object")
        if row.get("month") in (None, ""):
            _fail(f"weights.{index}.month_is_required")
        for asset in ASSET_ORDER:
            _quarter(row.get(asset), f"weights.{index}.{asset}")

    nav = strategy.get("nav")
    if not isinstance(nav, list) or not nav:
        _fail("nav_must_be_a_non_empty_list")
    for index, row in enumerate(nav):
        if not isinstance(row, Mapping):
            _fail(f"nav.{index}_must_be_an_object")
        if row.get("month") in (None, ""):
            _fail(f"nav.{index}.month_is_required")
        _finite(row.get("nav"), f"nav.{index}.nav")

    returns = strategy.get("returns")
    if not isinstance(returns, list) or not returns:
        _fail("returns_must_be_a_non_empty_list")
    for index, row in enumerate(returns):
        if not isinstance(row, Mapping):
            _fail(f"returns.{index}_must_be_an_object")
        linear = _finite(row.get("linear_cost"), f"returns.{index}.linear_cost")
        quadratic = _finite(
            row.get("quadratic_cost"), f"returns.{index}.quadratic_cost"
        )
        total = _finite(row.get("cost"), f"returns.{index}.cost")
        if not math.isclose(
            linear + quadratic, total, rel_tol=0.0, abs_tol=_TOLERANCE
        ):
            _fail(
                f"returns.{index}.cost_must_equal_linear_cost_plus_quadratic_cost"
            )

    return {
        "id": str(strategy["id"]),
        "strategy_key": DISPLAY_STRATEGY_ID,
        "weights": derived_weights,
        "role": str(strategy["role"]),
        "optimizer_input": False,
        "active_return_reference": False,
        "nav_observations": len(nav),
        "return_observations": len(returns),
    }
