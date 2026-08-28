"""Governed PIT macro/market cycle filters used by ``cycle_views_v5``.

The public functions in this module are drop-in replacements for the legacy
v5 builders.  They add canonical factor evidence, strict admission and an
explicit-duration hidden semi-Markov filter while preserving every field the
allocation orchestrator already consumes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from cycle_factor_registry_v5 import (
    CYCLE_FACTOR_REGISTRY_V5,
    CycleFactorSpecV5,
    validate_cycle_factor_registry_v5,
)
from cycle_state_model_v5 import DurationPriorV5, explicit_duration_filter_v5


CYCLE_STATES_V5 = {
    "pring": tuple(str(index) for index in range(1, 7)),
    "kitchin": ("被动去库", "主动补库", "被动补库", "主动去库"),
    "juglar": ("修复期", "繁荣早期", "繁荣晚期", "出清期"),
    "merrill": ("再通胀/衰退", "复苏", "过热", "滞涨"),
    "kondratieff": ("回升", "繁荣", "衰退", "萧条"),
}
PRING_PATTERN_V5 = {
    1: (1, 0, 0),
    2: (1, 1, 0),
    3: (1, 1, 1),
    4: (0, 1, 1),
    5: (0, 0, 1),
    6: (0, 0, 0),
}
PRING_NAMES_V5 = {
    1: "衰退期（债牛）",
    2: "复苏前期（债股牛）",
    3: "复苏期（债股商牛）",
    4: "过热期（股商牛）",
    5: "滞涨期（商品牛）",
    6: "衰退前期（三类熊）",
}
FACTOR_SCHEMA_VERSION_V5 = "5.1"

PRING_DURATION_PRIORS_V5 = tuple(DurationPriorV5(2, 4.0, 10) for _ in range(6))
KITCHIN_DURATION_PRIORS_V5 = tuple(DurationPriorV5(3, 9.0, 18) for _ in range(4))
JUGLAR_DURATION_PRIORS_V5 = (
    DurationPriorV5(12, 21.0, 36),
    DurationPriorV5(18, 33.0, 48),
    DurationPriorV5(18, 30.0, 48),
    DurationPriorV5(12, 24.0, 42),
)
MERRILL_DURATION_PRIORS_V5 = tuple(DurationPriorV5(3, 6.0, 15) for _ in range(4))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _month(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:6]


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def _mean_available(values: Sequence[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(np.mean(clean)) if clean else None


def _change(values: Sequence[float | None], lag: int, *, percent: bool = False) -> list[float | None]:
    output: list[float | None] = []
    for position, value in enumerate(values):
        previous = values[position - lag] if position >= lag else None
        if value is None or previous is None:
            output.append(None)
        elif percent and abs(float(previous)) > 1.0e-12:
            output.append(100.0 * (float(value) / float(previous) - 1.0))
        else:
            output.append(float(value) - float(previous))
    return output


def _causal_robust_z(
    values: Sequence[float | None],
    months: Sequence[str],
    *,
    train_end: str | None,
    minimum: int,
    window: int = 60,
) -> list[float | None]:
    """One-step-behind robust scaling, frozen after an explicit train end."""

    calibration: list[float] = []
    output: list[float | None] = []
    for value, month in zip(values, months):
        history = calibration[-window:]
        if value is None or len(history) < minimum:
            output.append(None)
        else:
            median = float(np.median(history))
            mad = float(np.median(np.abs(np.asarray(history, dtype=float) - median)))
            scale = 1.4826 * mad
            if scale < 1.0e-8:
                scale = float(np.std(history, ddof=1)) if len(history) > 1 else 0.0
            score = (float(value) - median) / max(scale, 1.0e-8)
            output.append(float(np.clip(score, -4.0, 4.0)))
        if value is not None and (train_end is None or str(month) <= str(train_end)):
            calibration.append(float(value))
    return output


def _select_field(
    rows: Sequence[Mapping[str, Any]],
    specification: CycleFactorSpecV5,
    train_end: str | None,
) -> str | None:
    training_rows = [row for row in rows if train_end is None or _month(row.get("month")) <= str(train_end)]
    for field in specification.accepted_fields:
        if any(_number(row.get(field)) is not None for row in training_rows):
            return field
    return None


def _factor_series(
    rows: Sequence[Mapping[str, Any]],
    months: Sequence[str],
    specification: CycleFactorSpecV5,
    *,
    train_end: str | None,
) -> dict[str, Any]:
    field = _select_field(rows, specification, train_end)
    raw = [_number(row.get(field)) if field else None for row in rows]
    if specification.factor_key == "m1_m2_spread" and field is None:
        field = "derived:m1_yoy-m2_yoy"
        raw = [
            None
            if _number(row.get("m1_yoy")) is None or _number(row.get("m2_yoy")) is None
            else float(row["m1_yoy"]) - float(row["m2_yoy"])
            for row in rows
        ]

    transformed = list(raw)
    if specification.transform == "yoy_or_level_yoy" and field and not field.endswith("_yoy"):
        transformed = _change(raw, 12, percent=True)
    elif specification.transform == "inverse_level":
        transformed = [None if value is None else -float(value) for value in raw]
    elif specification.transform == "momentum_3m" and field and "momentum" not in field:
        transformed = _change(raw, 3)

    # A low equity valuation percentile is supportive; ERP is already signed
    # in the economically supportive direction.
    if specification.factor_key == "equity_valuation_support" and field == "equity_valuation_percentile":
        transformed = [None if value is None else -float(value) for value in transformed]
    transformed = [
        None if value is None else float(value) * float(specification.direction)
        for value in transformed
    ]
    score = _causal_robust_z(
        transformed,
        months,
        train_end=train_end,
        minimum=max(12, min(specification.minimum_history_months, 36)),
    )
    momentum = _causal_robust_z(
        _change(transformed, 3),
        months,
        train_end=train_end,
        minimum=12,
        window=36,
    )
    return {
        "field": field,
        "raw": raw,
        "transformed": transformed,
        "score": score,
        "momentum": momentum,
        "transform": specification.transform,
    }


def _cycle_exit_transition(states: int) -> np.ndarray:
    matrix = np.full((states, states), 0.02 / max(states - 2, 1), dtype=float)
    np.fill_diagonal(matrix, 0.0)
    for position in range(states):
        matrix[position, (position + 1) % states] = 0.86
        matrix[position, (position - 1) % states] = 0.12
    return matrix / matrix.sum(axis=1, keepdims=True)


def _emission_from_centres(
    features: Sequence[Sequence[float | None]],
    centres: np.ndarray,
    weights: Sequence[float],
) -> np.ndarray:
    centre_matrix = np.asarray(centres, dtype=float)
    weight = np.asarray(weights, dtype=float)
    if centre_matrix.ndim != 2 or centre_matrix.shape[1] != len(weight):
        raise ValueError("cycle_emission_centre_dimension_mismatch")
    output = np.zeros((len(features), centre_matrix.shape[0]), dtype=float)
    for position, row in enumerate(features):
        values = np.asarray([np.nan if value is None else float(value) for value in row], dtype=float)
        available = np.isfinite(values)
        if not bool(np.any(available)):
            continue
        active_weight = weight[available]
        active_weight = active_weight / float(np.sum(active_weight))
        difference = centre_matrix[:, available] - values[available][None, :]
        output[position] = -0.75 * np.sum((difference ** 2) * active_weight[None, :], axis=1)
    return output


def _duration_payload(
    filtered: Any,
    priors: Sequence[DurationPriorV5],
    states: Sequence[str],
    position: int,
    selected: int,
) -> dict[str, Any]:
    prior = priors[selected]
    return {
        "method": filtered.diagnostics["method"],
        "expected_elapsed_months": float(filtered.expected_elapsed_months[position, selected]),
        "expected_elapsed_by_state": {
            state: float(filtered.expected_elapsed_months[position, state_index])
            for state_index, state in enumerate(states)
        },
        "minimum_months": prior.minimum_months,
        "expected_months": prior.expected_months,
        "maximum_months": prior.maximum_months,
        "train_end": filtered.diagnostics["train_end"],
        "learned_through": filtered.learned_through,
    }


def _factor_evidence(
    cycle: str,
    position: int,
    row: Mapping[str, Any],
    factor_data: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    specifications = [item for item in CYCLE_FACTOR_REGISTRY_V5 if item.cycle == cycle]
    required = [item for item in specifications if item.required_for_admission]
    missing_factors = [
        item.factor_key for item in required if factor_data[item.factor_key]["score"][position] is None
    ]
    present_pillars = sorted({
        item.pillar
        for item in specifications
        if factor_data[item.factor_key]["score"][position] is not None
    })
    required_pillars = sorted({item.pillar for item in required})
    observed_fields = {
        item.factor_key: factor_data[item.factor_key]["field"]
        for item in specifications
        if factor_data[item.factor_key]["score"][position] is not None
    }
    pit_verified = bool(row.get("_pit_verified"))
    admitted = pit_verified and not missing_factors
    if not pit_verified:
        reason = "pit_or_vintage_not_verified"
    elif missing_factors:
        reason = "missing_required_factors:" + ",".join(missing_factors)
    else:
        reason = "all_required_pillars_and_pit_verified"
    return {
        "factor_schema_version": FACTOR_SCHEMA_VERSION_V5,
        "required_pillars": required_pillars,
        "present_pillars": present_pillars,
        "missing_pillars": sorted(set(required_pillars) - set(present_pillars)),
        "missing_required_factors": missing_factors,
        "observed_fields": observed_fields,
        "pit_verified": pit_verified,
        "eligible_for_views": admitted,
        "admission_reason": reason,
        "observation_period": _month(row.get("observation_period") or row.get("month")),
        "available_time": _month(row.get("available_time") or row.get("release_time") or row.get("month")),
        "vintage": row.get("vintage"),
        "source": str(row.get("source") or "local_warehouse"),
    }


def pring_transition_prior_v5() -> np.ndarray:
    matrix = np.full((6, 6), 0.01, dtype=float)
    for position in range(6):
        matrix[position, (position + 1) % 6] += 0.80
        matrix[position, (position - 1) % 6] += 0.14
    np.fill_diagonal(matrix, 0.0)
    return matrix / matrix.sum(axis=1, keepdims=True)


def build_pring_market_probabilities_v5(
    months: Sequence[str],
    asset_returns: np.ndarray,
    *,
    train_end: str,
    transition_prior: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Six-stage explicit-duration Pring filter; gold is never an input."""

    returns = np.asarray(asset_returns, dtype=float)
    if returns.ndim != 2 or returns.shape[1] != 4 or len(returns) != len(months):
        raise ValueError("pring_requires_month_aligned_four_asset_returns")
    transition = np.asarray(
        transition_prior if transition_prior is not None else pring_transition_prior_v5(),
        dtype=float,
    )
    if transition.shape != (6, 6) or np.any(transition < 0.0):
        raise ValueError("invalid_pring_transition_prior")
    np.fill_diagonal(transition, 0.0)
    if np.any(transition.sum(axis=1) <= 0.0):
        raise ValueError("invalid_pring_exit_transition")
    transition /= transition.sum(axis=1, keepdims=True)

    # This explicit indexing is a governance control: column 2 (gold) is not
    # touched by any score, likelihood or transition update.
    market_indices = {"bond": 1, "equity": 0, "commodity": 3}
    market_history: list[dict[str, Any]] = []
    log_likelihood = np.zeros((len(months), 6), dtype=float)
    for position, month in enumerate(months):
        bull: dict[str, float] = {}
        detail: dict[str, Any] = {}
        for market, column in market_indices.items():
            values = returns[:, column]
            lookback = values[max(0, position - 23): position + 1]
            volatility = float(np.std(lookback, ddof=1) * math.sqrt(12.0)) if len(lookback) >= 6 else 0.15
            horizon_scores: list[tuple[float, float]] = []
            for horizon, coefficient in ((3, 0.20), (6, 0.35), (12, 0.45)):
                if position + 1 < horizon:
                    continue
                compound = float(np.prod(1.0 + values[position - horizon + 1: position + 1]) - 1.0)
                score = compound / max(volatility * math.sqrt(horizon / 12.0), 0.02)
                horizon_scores.append((coefficient, score))
            if horizon_scores:
                total_weight = sum(weight for weight, _ in horizon_scores)
                combined = sum(weight * score for weight, score in horizon_scores) / total_weight
                probability = _sigmoid(1.35 * combined)
            else:
                combined = 0.0
                probability = 0.5
            bull[market] = probability
            detail[market] = {
                "bull_probability": probability,
                "risk_adjusted_multi_horizon_score": combined,
                "annualized_trailing_volatility": volatility,
                "history_months": position + 1,
            }
        for phase, pattern in PRING_PATTERN_V5.items():
            value = 0.0
            for market_position, market in enumerate(("bond", "equity", "commodity")):
                probability = float(np.clip(bull[market], 1.0e-8, 1.0 - 1.0e-8))
                value += math.log(probability if pattern[market_position] else 1.0 - probability)
            log_likelihood[position, phase - 1] = value
        market_history.append({"month": str(month), "bull": bull, "detail": detail})

    filtered = explicit_duration_filter_v5(
        log_likelihood,
        transition,
        PRING_DURATION_PRIORS_V5,
        months=[str(month) for month in months],
        train_end=str(train_end),
        transition_prior_strength=36.0,
    )
    output: list[dict[str, Any]] = []
    for position, market in enumerate(market_history):
        probability = filtered.state_probabilities[position]
        selected = int(np.argmax(probability))
        eligible = position + 1 >= 12
        output.append({
            "month": market["month"],
            "state": str(selected + 1),
            "state_name": PRING_NAMES_V5[selected + 1],
            "probabilities": {str(index + 1): float(value) for index, value in enumerate(probability)},
            "confidence": float(np.max(probability)),
            "market_probabilities": market["bull"],
            "market_detail": market["detail"],
            "transition_matrix": filtered.exit_transition_history[position].tolist(),
            "duration_model": _duration_payload(filtered, PRING_DURATION_PRIORS_V5, CYCLE_STATES_V5["pring"], position, selected),
            "eligible_for_views": eligible,
            "data_status": "D3_upstream_total_return_registry" if eligible else "insufficient_market_history",
            "method": "explicit_duration_pring_filter_bond_equity_ex_gold_commodity",
            "input_assets": ["bond", "equity", "commodity"],
            "excluded_assets": ["gold"],
        })
    return output


def build_macro_cycle_probabilities_v5(
    macro_rows: Sequence[Mapping[str, Any]],
    *,
    train_end: str | None = None,
) -> list[dict[str, Any]]:
    """Build Kitchin/Juglar/Merrill probabilities from governed PIT factors."""

    validate_cycle_factor_registry_v5()
    rows = sorted((dict(row) for row in macro_rows), key=lambda row: _month(row.get("month")))
    if not rows:
        return []
    months = [_month(row.get("month")) for row in rows]
    factor_data = {
        specification.factor_key: _factor_series(
            rows, months, specification, train_end=train_end
        )
        for specification in CYCLE_FACTOR_REGISTRY_V5
    }
    by_cycle: dict[str, list[CycleFactorSpecV5]] = defaultdict(list)
    for specification in CYCLE_FACTOR_REGISTRY_V5:
        by_cycle[specification.cycle].append(specification)

    def factor_score(key: str, position: int, kind: str = "score") -> float | None:
        return factor_data[key][kind][position]

    kitchin_features: list[list[float | None]] = []
    juglar_features: list[list[float | None]] = []
    merrill_features: list[list[float | None]] = []
    pillar_history: list[dict[str, dict[str, float | None]]] = []
    evidence_history: list[dict[str, dict[str, Any]]] = []
    for position, row in enumerate(rows):
        pillars: dict[str, dict[str, float | None]] = {}
        for cycle in ("kitchin", "juglar", "merrill"):
            cycle_pillars: dict[str, float | None] = {}
            for pillar in sorted({item.pillar for item in by_cycle[cycle]}):
                cycle_pillars[pillar] = _mean_available([
                    factor_score(item.factor_key, position)
                    for item in by_cycle[cycle]
                    if item.pillar == pillar
                ])
            pillars[cycle] = cycle_pillars

        inventory = pillars["kitchin"].get("inventory")
        demand = pillars["kitchin"].get("demand")
        inventory_momentum = _mean_available([
            factor_score(item.factor_key, position, "momentum")
            for item in by_cycle["kitchin"] if item.pillar == "inventory"
        ])
        demand_momentum = _mean_available([
            factor_score(item.factor_key, position, "momentum")
            for item in by_cycle["kitchin"] if item.pillar == "demand"
        ])
        kitchin_features.append([
            _mean_available([demand, demand_momentum]),
            _mean_available([inventory, inventory_momentum]),
        ])

        juglar_level = _mean_available(list(pillars["juglar"].values()))
        juglar_momentum = _mean_available([
            factor_score(item.factor_key, position, "momentum")
            for item in by_cycle["juglar"]
        ])
        juglar_features.append([juglar_momentum, juglar_level])

        merrill_features.append([
            pillars["merrill"].get("growth"),
            pillars["merrill"].get("inflation"),
            pillars["merrill"].get("credit"),
            pillars["merrill"].get("liquidity"),
            pillars["merrill"].get("valuation"),
            pillars["merrill"].get("risk_appetite"),
        ])
        pillar_history.append(pillars)
        evidence_history.append({
            cycle: _factor_evidence(cycle, position, row, factor_data)
            for cycle in ("kitchin", "juglar", "merrill")
        })

    kitchin_emission = _emission_from_centres(
        kitchin_features,
        np.asarray(((1, -1), (1, 1), (-1, 1), (-1, -1)), dtype=float),
        (0.55, 0.45),
    )
    juglar_emission = _emission_from_centres(
        juglar_features,
        np.asarray(((1, -1), (1, 1), (-1, 1), (-1, -1)), dtype=float),
        (0.45, 0.55),
    )
    merrill_emission = _emission_from_centres(
        merrill_features,
        np.asarray(
            (
                (-1.0, -1.0, -0.2, 0.6, 0.4, -0.3),
                (1.0, -1.0, 0.8, 0.7, 0.6, 0.7),
                (1.0, 1.0, 0.4, -0.2, -0.2, 0.5),
                (-1.0, 1.0, -0.7, -0.6, -0.4, -0.7),
            ),
            dtype=float,
        ),
        (0.24, 0.22, 0.16, 0.14, 0.12, 0.12),
    )
    filters = {
        "kitchin": explicit_duration_filter_v5(
            kitchin_emission, _cycle_exit_transition(4), KITCHIN_DURATION_PRIORS_V5,
            months=months, train_end=train_end, transition_prior_strength=30.0,
        ),
        "juglar": explicit_duration_filter_v5(
            juglar_emission, _cycle_exit_transition(4), JUGLAR_DURATION_PRIORS_V5,
            months=months, train_end=train_end, transition_prior_strength=48.0,
        ),
        "merrill": explicit_duration_filter_v5(
            merrill_emission, _cycle_exit_transition(4), MERRILL_DURATION_PRIORS_V5,
            months=months, train_end=train_end, transition_prior_strength=30.0,
        ),
    }
    priors = {
        "kitchin": KITCHIN_DURATION_PRIORS_V5,
        "juglar": JUGLAR_DURATION_PRIORS_V5,
        "merrill": MERRILL_DURATION_PRIORS_V5,
    }

    output: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        cycle_payload: dict[str, dict[str, Any]] = {}
        for cycle in ("kitchin", "juglar", "merrill"):
            states = CYCLE_STATES_V5[cycle]
            probability_array = filters[cycle].state_probabilities[position]
            selected = int(np.argmax(probability_array))
            evidence = evidence_history[position][cycle]
            cycle_payload[cycle] = {
                "state": states[selected],
                "probabilities": {
                    state: float(probability_array[state_index])
                    for state_index, state in enumerate(states)
                },
                "confidence": float(np.max(probability_array)),
                "eligible_for_views": bool(evidence["eligible_for_views"]),
                "data_status": "D3" if evidence["eligible_for_views"] else evidence["admission_reason"],
                "factor_evidence": evidence,
                "duration_model": _duration_payload(filters[cycle], priors[cycle], states, position, selected),
                "transition_matrix": filters[cycle].exit_transition_history[position].tolist(),
            }

        kitchin_axis = {"demand": kitchin_features[position][0], "inventory": kitchin_features[position][1]}
        cycle_payload["kitchin"]["axis_scores"] = kitchin_axis
        # Legacy field names remain available for the current page/orchestrator.
        cycle_payload["kitchin"]["demand_score"] = kitchin_axis["demand"]
        cycle_payload["kitchin"]["inventory_score"] = kitchin_axis["inventory"]

        juglar_pillars = pillar_history[position]["juglar"]
        juglar_axis = {"momentum": juglar_features[position][0], "level": juglar_features[position][1]}
        cycle_payload["juglar"]["pillar_scores"] = juglar_pillars
        cycle_payload["juglar"]["axis_scores"] = juglar_axis
        cycle_payload["juglar"]["level_score"] = juglar_axis["level"]
        cycle_payload["juglar"]["momentum_score"] = juglar_axis["momentum"]

        merrill_pillars = pillar_history[position]["merrill"]
        cycle_payload["merrill"]["pillar_scores"] = merrill_pillars
        cycle_payload["merrill"]["axis_scores"] = {
            "growth": merrill_features[position][0],
            "inflation": merrill_features[position][1],
            "credit": merrill_features[position][2],
            "liquidity": merrill_features[position][3],
            "valuation": merrill_features[position][4],
            "risk_appetite": merrill_features[position][5],
            "valuation_risk_appetite": _mean_available(merrill_features[position][4:6]),
        }

        cycle_payload["kondratieff"] = {
            "state": "研究展示",
            "probabilities": {state: 0.25 for state in CYCLE_STATES_V5["kondratieff"]},
            "confidence": 0.0,
            "eligible_for_views": False,
            "data_status": "display_only_insufficient_independent_cycles",
            "factor_evidence": {
                "factor_schema_version": FACTOR_SCHEMA_VERSION_V5,
                "eligible_for_views": False,
                "admission_reason": "40-60年周期缺少多个独立完整样本，禁止参数化与映射",
            },
            "duration_model": None,
        }
        merrill_axis = cycle_payload["merrill"]["axis_scores"]
        output.append({
            "month": months[position],
            "growth_score": float(merrill_axis["growth"] or 0.0),
            "inflation_score": float(merrill_axis["inflation"] or 0.0),
            "credit_score": float(merrill_axis["credit"] or 0.0),
            "liquidity_score": float(merrill_axis["liquidity"] or 0.0),
            **cycle_payload,
            "factor_schema_version": FACTOR_SCHEMA_VERSION_V5,
            "source": str(row.get("source") or "local_warehouse"),
        })
    return output


def _directional_score(cycle: str, payload: Mapping[str, Any]) -> float:
    probability = payload.get("probabilities") or {}
    weights = {
        "pring": {"1": -0.4, "2": 0.4, "3": 1.0, "4": 0.6, "5": -0.3, "6": -1.0},
        "kitchin": {"被动去库": 0.7, "主动补库": 1.0, "被动补库": -0.7, "主动去库": -1.0},
        "juglar": {"修复期": 0.4, "繁荣早期": 1.0, "繁荣晚期": 0.3, "出清期": -1.0},
        "merrill": {"再通胀/衰退": -0.8, "复苏": 1.0, "过热": 0.5, "滞涨": -1.0},
    }[cycle]
    return float(sum(float(probability.get(state, 0.0)) * value for state, value in weights.items()))


def _cycle_conflicts(cycles: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    admitted = [
        cycle for cycle in ("pring", "kitchin", "juglar", "merrill")
        if bool((cycles.get(cycle) or {}).get("eligible_for_views"))
    ]
    score = {cycle: _directional_score(cycle, cycles[cycle]) for cycle in admitted}
    output: list[dict[str, Any]] = []
    for left_index, left in enumerate(admitted):
        for right in admitted[left_index + 1:]:
            if score[left] * score[right] < -0.16:
                output.append({
                    "left": left,
                    "right": right,
                    "left_direction_score": score[left],
                    "right_direction_score": score[right],
                    "reason": "probability_weighted_growth_direction_conflict",
                })
    return output


def merge_cycle_history_v5(
    months: Sequence[str],
    pring_rows: Sequence[Mapping[str, Any]],
    macro_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pring_by_month = {str(row["month"]): dict(row) for row in pring_rows}
    macro_sorted = sorted((dict(row) for row in macro_rows), key=lambda row: str(row["month"]))
    macro_cursor = 0
    latest_macro: dict[str, Any] | None = None
    output: list[dict[str, Any]] = []
    for month in months:
        while macro_cursor < len(macro_sorted) and str(macro_sorted[macro_cursor]["month"]) <= str(month):
            latest_macro = macro_sorted[macro_cursor]
            macro_cursor += 1
        pring = pring_by_month.get(str(month))
        if pring is None:
            continue
        macro = latest_macro
        if macro is None:
            macro = {
                "month": str(month),
                "growth_score": 0.0,
                "inflation_score": 0.0,
                "credit_score": 0.0,
                "liquidity_score": 0.0,
                **{
                    cycle: {
                        "state": "数据不足",
                        "probabilities": {state: 1.0 / len(CYCLE_STATES_V5[cycle]) for state in CYCLE_STATES_V5[cycle]},
                        "eligible_for_views": False,
                        "data_status": "no_pit_macro_observation_available",
                    }
                    for cycle in ("kitchin", "juglar", "merrill")
                },
                "kondratieff": {
                    "state": "研究展示",
                    "probabilities": {state: 0.25 for state in CYCLE_STATES_V5["kondratieff"]},
                    "eligible_for_views": False,
                    "data_status": "display_only_insufficient_independent_cycles",
                },
            }
        phase = int(pring["state"])
        cycles = {
            "pring": dict(pring),
            "kitchin": dict(macro["kitchin"]),
            "juglar": dict(macro["juglar"]),
            "merrill": dict(macro["merrill"]),
            "kondratieff": dict(macro["kondratieff"]),
        }
        admitted = [cycle for cycle, payload in cycles.items() if bool(payload.get("eligible_for_views"))]
        conflicts = _cycle_conflicts(cycles)
        output.append({
            "month": str(month),
            "cycles": cycles,
            "pring_phase": phase,
            "pring_phase_name": PRING_NAMES_V5[phase],
            "pring_probability": dict(pring["probabilities"]),
            "confidence": float(pring["confidence"]),
            "pring_market_probability": dict(pring["market_probabilities"]),
            "kitchin_state": cycles["kitchin"]["state"],
            "kitchin_probability": cycles["kitchin"]["probabilities"],
            "juglar_state": cycles["juglar"]["state"],
            "juglar_probability": cycles["juglar"]["probabilities"],
            "merrill_state": cycles["merrill"]["state"],
            "merrill_probability": cycles["merrill"]["probabilities"],
            "kondratieff_state": cycles["kondratieff"]["state"],
            "kondratieff_probability": cycles["kondratieff"]["probabilities"],
            "kondratieff_confidence": 0.0,
            "growth_score": float(macro.get("growth_score") or 0.0),
            "inflation_score": float(macro.get("inflation_score") or 0.0),
            "credit_score": float(macro.get("credit_score") or 0.0),
            "liquidity_score": float(macro.get("liquidity_score") or 0.0),
            "cycle_eligibility": {cycle: bool(payload.get("eligible_for_views")) for cycle, payload in cycles.items()},
            "cycle_diagnostics": {
                "factor_schema_version": FACTOR_SCHEMA_VERSION_V5,
                "admitted_cycles": admitted,
                "conflicts": conflicts,
                "conflict_count": len(conflicts),
                "macro_information_month": str(macro.get("month") or ""),
                "kondratieff_policy": "display_only_zero_allocation_contribution",
            },
            "source": str(macro.get("source") or "local_warehouse") + "+market_pring",
        })
    return output


__all__ = [
    "FACTOR_SCHEMA_VERSION_V5",
    "build_macro_cycle_probabilities_v5",
    "build_pring_market_probabilities_v5",
    "merge_cycle_history_v5",
    "pring_transition_prior_v5",
]
