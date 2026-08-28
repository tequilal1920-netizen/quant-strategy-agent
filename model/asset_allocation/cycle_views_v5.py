"""Point-in-time cycle probabilities and joint BL views for v5.

Pring is reconstructed from the bond/equity/ex-gold-commodity markets.  Gold
is intentionally absent from the phase filter.  The other four cycle families
are represented as probability distributions with explicit data-admission
flags.  Only admitted cycles enter the joint ridge view model; Kondratieff is
always display-only until several independent long waves are available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


ASSET_ORDER_V5 = ("equity", "bond", "gold", "commodity")
P_VIEWS_V5 = np.asarray(
    [
        [1.0, -1.0, 0.0, 0.0],  # equity - government bond
        [0.0, -1.0, 0.0, 1.0],  # ex-gold commodity - government bond
        [0.0, -1.0, 1.0, 0.0],  # gold - government bond
    ],
    dtype=float,
)

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

CYCLE_STATES_V5 = {
    "pring": tuple(str(index) for index in range(1, 7)),
    "kitchin": ("被动去库", "主动补库", "被动补库", "主动去库"),
    "juglar": ("修复期", "繁荣早期", "繁荣晚期", "出清期"),
    "merrill": ("再通胀/衰退", "复苏", "过热", "滞涨"),
    "kondratieff": ("回升", "繁荣", "衰退", "萧条"),
}


@dataclass
class ViewBundleV5:
    P: np.ndarray
    q: np.ndarray
    omega: np.ndarray
    cycle_contributions: dict[str, np.ndarray]
    forecast_error_covariance: np.ndarray
    diagnostics: dict[str, Any]


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


def _softmax(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    array -= float(np.max(array))
    result = np.exp(array)
    return result / float(np.sum(result))


def _causal_z(values: Sequence[float | None], window: int = 60, minimum: int = 24) -> list[float | None]:
    output: list[float | None] = []
    for index, value in enumerate(values):
        if value is None:
            output.append(None)
            continue
        history = [
            float(item)
            for item in values[max(0, index - window + 1): index + 1]
            if item is not None
        ]
        if len(history) < minimum:
            output.append(None)
            continue
        scale = float(np.std(history, ddof=1))
        output.append((float(value) - float(np.mean(history))) / max(scale, 1.0e-8))
    return output


def _lag_change(values: Sequence[float | None], lag: int) -> list[float | None]:
    return [
        None
        if index < lag or value is None or values[index - lag] is None
        else float(value) - float(values[index - lag])
        for index, value in enumerate(values)
    ]


def _mean_available(*values: float | None) -> float:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(np.mean(clean)) if clean else 0.0


def _quadrant_probabilities(
    horizontal: float,
    vertical: float,
    states: Sequence[str],
    centres: Sequence[tuple[float, float]],
) -> dict[str, float]:
    probability = _softmax(
        [-0.65 * ((horizontal - x) ** 2 + (vertical - y) ** 2) for x, y in centres]
    )
    return {state: float(probability[index]) for index, state in enumerate(states)}


def pring_transition_prior_v5() -> np.ndarray:
    """A transparent prior that permits persistence, progression and reversal."""

    matrix = np.full((6, 6), 0.01, dtype=float)
    for index in range(6):
        matrix[index, index] += 0.57
        matrix[index, (index + 1) % 6] += 0.29
        matrix[index, (index - 1) % 6] += 0.08
    return matrix / matrix.sum(axis=1, keepdims=True)


def build_pring_market_probabilities_v5(
    months: Sequence[str],
    asset_returns: np.ndarray,
    *,
    train_end: str,
    transition_prior: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Filter Pring phases from bond, equity and ex-gold commodity only."""

    returns = np.asarray(asset_returns, dtype=float)
    if returns.ndim != 2 or returns.shape[1] != 4 or len(returns) != len(months):
        raise ValueError("pring_requires_month_aligned_four_asset_returns")
    # Explicit column selection is a tested governance rule.  Gold column 2 is
    # never read by this function.
    market_indices = {"bond": 1, "equity": 0, "commodity": 3}
    transition = np.asarray(transition_prior if transition_prior is not None else pring_transition_prior_v5(), dtype=float)
    if transition.shape != (6, 6) or np.any(transition < 0):
        raise ValueError("invalid_pring_transition_prior")
    transition = transition / transition.sum(axis=1, keepdims=True)
    counts = 18.0 * transition
    posterior = np.full(6, 1.0 / 6.0)
    output: list[dict[str, Any]] = []
    for index, month in enumerate(months):
        prior_posterior = posterior.copy()
        bull: dict[str, float] = {}
        detail: dict[str, Any] = {}
        for market, column in market_indices.items():
            values = returns[:, column]
            lookback = values[max(0, index - 23): index + 1]
            annual_volatility = float(np.std(lookback, ddof=1) * math.sqrt(12.0)) if len(lookback) >= 6 else 0.15
            horizon_scores = []
            for horizon, coefficient in ((3, 0.20), (6, 0.35), (12, 0.45)):
                if index + 1 < horizon:
                    continue
                compound = float(np.prod(1.0 + values[index - horizon + 1: index + 1]) - 1.0)
                score = compound / max(annual_volatility * math.sqrt(horizon / 12.0), 0.02)
                horizon_scores.append((coefficient, score))
            if horizon_scores:
                denominator = sum(item[0] for item in horizon_scores)
                combined = sum(weight * score for weight, score in horizon_scores) / denominator
                probability = _sigmoid(1.35 * combined)
            else:
                combined = 0.0
                probability = 0.5
            bull[market] = probability
            detail[market] = {
                "bull_probability": probability,
                "risk_adjusted_multi_horizon_score": combined,
                "annualized_trailing_volatility": annual_volatility,
            }

        likelihood = np.ones(6, dtype=float)
        for phase, pattern in PRING_PATTERN_V5.items():
            for market_index, market in enumerate(("bond", "equity", "commodity")):
                probability = bull[market]
                likelihood[phase - 1] *= probability if pattern[market_index] else 1.0 - probability
        predicted = posterior @ transition
        posterior = np.maximum(predicted * np.maximum(likelihood, 1.0e-12), 1.0e-15)
        posterior /= posterior.sum()
        if str(month) <= train_end:
            counts += np.outer(prior_posterior, posterior)
            transition = counts / counts.sum(axis=1, keepdims=True)
        phase = int(np.argmax(posterior)) + 1
        output.append(
            {
                "month": str(month),
                "state": str(phase),
                "state_name": PRING_NAMES_V5[phase],
                "probabilities": {str(position + 1): float(value) for position, value in enumerate(posterior)},
                "confidence": float(np.max(posterior)),
                "market_probabilities": bull,
                "market_detail": detail,
                "transition_matrix": transition.tolist(),
                "eligible_for_views": True,
                "method": "public_auditable_pring_market_filter_bond_equity_ex_gold_commodity",
            }
        )
    return output


def build_macro_cycle_probabilities_v5(
    macro_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create causal macro-cycle probabilities and explicit admission flags."""

    rows = sorted((dict(row) for row in macro_rows), key=lambda row: _month(row.get("month")))
    if not rows:
        return []
    fields = {
        name: [_number(row.get(name)) for row in rows]
        for name in (
            "pmi_manufacturing",
            "pmi_composite",
            "cpi_national_yoy",
            "ppi_yoy",
            "m1_yoy",
            "m2_yoy",
            "sf_inc_month",
            "sf_stock_endval",
            "industrial_finished_goods_inventory",
            "industrial_revenue",
            "manufacturing_fai",
            "capacity_utilization",
            "enterprise_medium_long_loan",
        )
    }
    z = {name: _causal_z(values) for name, values in fields.items()}
    delta3_z = {name: _causal_z(_lag_change(values, 3), 36, 18) for name, values in fields.items()}
    credit_stock_growth = _causal_z(_lag_change(fields["sf_stock_endval"], 12), 60, 24)
    inventory_growth = _causal_z(_lag_change(fields["industrial_finished_goods_inventory"], 12), 60, 24)
    revenue_growth = _causal_z(_lag_change(fields["industrial_revenue"], 12), 60, 24)
    investment_growth = _causal_z(_lag_change(fields["manufacturing_fai"], 12), 60, 24)
    output: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        growth = _mean_available(
            z["pmi_manufacturing"][index],
            z["pmi_composite"][index],
            delta3_z["pmi_manufacturing"][index],
        )
        inflation = _mean_available(
            z["cpi_national_yoy"][index],
            z["ppi_yoy"][index],
            delta3_z["ppi_yoy"][index],
        )
        m1_m2 = None
        if fields["m1_yoy"][index] is not None and fields["m2_yoy"][index] is not None:
            m1_m2 = fields["m1_yoy"][index] - fields["m2_yoy"][index]
        credit = _mean_available(credit_stock_growth[index], m1_m2)
        liquidity = _mean_available(z["m2_yoy"][index], m1_m2)
        demand = _mean_available(growth, credit, revenue_growth[index])
        inventory = inventory_growth[index]
        inventory_proxy = inventory if inventory is not None else _mean_available(z["ppi_yoy"][index])

        kitchin_states = CYCLE_STATES_V5["kitchin"]
        kitchin_probability = _quadrant_probabilities(
            demand,
            float(inventory_proxy),
            kitchin_states,
            ((1.0, -1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0)),
        )
        kitchin_verified = inventory is not None and revenue_growth[index] is not None and bool(row.get("_pit_verified"))

        investment = investment_growth[index]
        capacity = z["capacity_utilization"][index]
        medium_loan = z["enterprise_medium_long_loan"][index]
        juglar_level = _mean_available(investment, capacity, medium_loan, credit)
        juglar_momentum = _mean_available(
            delta3_z["manufacturing_fai"][index],
            delta3_z["enterprise_medium_long_loan"][index],
        )
        juglar_probability = _quadrant_probabilities(
            juglar_momentum,
            juglar_level,
            CYCLE_STATES_V5["juglar"],
            ((1.0, -1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0)),
        )
        juglar_verified = all(value is not None for value in (investment, capacity, medium_loan)) and bool(row.get("_pit_verified"))

        merrill_probability = _quadrant_probabilities(
            growth,
            inflation,
            CYCLE_STATES_V5["merrill"],
            ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)),
        )
        merrill_verified = bool(row.get("_pit_verified")) and all(
            fields[name][index] is not None
            for name in ("pmi_manufacturing", "cpi_national_yoy", "ppi_yoy")
        )

        # The database cannot identify a 40-60 year wave.  The distribution is
        # retained for the page, but the view flag is hard false.
        kondratieff_probability = {
            state: 0.25 for state in CYCLE_STATES_V5["kondratieff"]
        }
        output.append(
            {
                "month": _month(row.get("month")),
                "growth_score": growth,
                "inflation_score": inflation,
                "credit_score": credit,
                "liquidity_score": liquidity,
                "kitchin": {
                    "state": max(kitchin_probability, key=kitchin_probability.get),
                    "probabilities": kitchin_probability,
                    "eligible_for_views": kitchin_verified,
                    "data_status": "D3" if kitchin_verified else "proxy_only_missing_real_inventory_or_pit",
                    "demand_score": demand,
                    "inventory_score": float(inventory_proxy),
                },
                "juglar": {
                    "state": max(juglar_probability, key=juglar_probability.get),
                    "probabilities": juglar_probability,
                    "eligible_for_views": juglar_verified,
                    "data_status": "D3" if juglar_verified else "missing_investment_capacity_or_pit",
                    "level_score": juglar_level,
                    "momentum_score": juglar_momentum,
                },
                "merrill": {
                    "state": max(merrill_probability, key=merrill_probability.get),
                    "probabilities": merrill_probability,
                    "eligible_for_views": merrill_verified,
                    "data_status": "D3" if merrill_verified else "macro_values_not_vintage_verified",
                },
                "kondratieff": {
                    "state": "研究展示",
                    "probabilities": kondratieff_probability,
                    "eligible_for_views": False,
                    "data_status": "display_only_insufficient_independent_cycles",
                },
                "source": str(row.get("source") or "local_warehouse"),
            }
        )
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
        macro = latest_macro or {
            "month": str(month),
            "growth_score": 0.0,
            "inflation_score": 0.0,
            "credit_score": 0.0,
            "liquidity_score": 0.0,
            "kitchin": {"state": "数据不足", "probabilities": {state: 0.25 for state in CYCLE_STATES_V5["kitchin"]}, "eligible_for_views": False},
            "juglar": {"state": "数据不足", "probabilities": {state: 0.25 for state in CYCLE_STATES_V5["juglar"]}, "eligible_for_views": False},
            "merrill": {"state": "数据不足", "probabilities": {state: 0.25 for state in CYCLE_STATES_V5["merrill"]}, "eligible_for_views": False},
            "kondratieff": {"state": "研究展示", "probabilities": {state: 0.25 for state in CYCLE_STATES_V5["kondratieff"]}, "eligible_for_views": False},
        }
        phase = int(pring["state"])
        cycles = {
            "pring": dict(pring),
            "kitchin": dict(macro["kitchin"]),
            "juglar": dict(macro["juglar"]),
            "merrill": dict(macro["merrill"]),
            "kondratieff": dict(macro["kondratieff"]),
        }
        output.append(
            {
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
                "cycle_eligibility": {
                    key: bool(value.get("eligible_for_views")) for key, value in cycles.items()
                },
                "source": str(macro.get("source") or "local_warehouse") + "+market_pring",
            }
        )
    return output


def cycle_probability_features_v5(
    cycle_history: Sequence[Mapping[str, Any]],
) -> tuple[list[str], np.ndarray, dict[str, slice]]:
    labels: list[str] = []
    slices: dict[str, slice] = {}
    columns: list[list[float]] = []
    cursor = 0
    for cycle in ("pring", "kitchin", "juglar", "merrill", "kondratieff"):
        states = CYCLE_STATES_V5[cycle]
        start = cursor
        for state in states[:-1]:
            labels.append(f"{cycle}:{state}")
            column: list[float] = []
            for row in cycle_history:
                payload = dict((row.get("cycles") or {}).get(cycle) or {})
                eligible = bool(payload.get("eligible_for_views")) and cycle != "kondratieff"
                probability = float((payload.get("probabilities") or {}).get(state, 0.0))
                column.append(probability if eligible else 0.0)
            columns.append(column)
            cursor += 1
        slices[cycle] = slice(start, cursor)
    matrix = np.asarray(columns, dtype=float).T if columns else np.zeros((len(cycle_history), 0))
    return labels, matrix, slices


def _ridge_fit(x: np.ndarray, y: np.ndarray, penalty: float) -> tuple[np.ndarray, np.ndarray]:
    intercept = np.mean(y, axis=0)
    coefficient = np.linalg.solve(
        x.T @ x + penalty * np.eye(x.shape[1]),
        x.T @ (y - intercept),
    )
    return intercept, coefficient


def fit_cycle_view_model_v5(
    asset_returns: np.ndarray,
    cycle_history: Sequence[Mapping[str, Any]],
    *,
    train_mask: Sequence[bool] | None = None,
    minimum_train: int = 24,
    ridge_grid: Sequence[float] = (0.1, 1.0, 10.0),
) -> dict[str, Any]:
    returns = np.asarray(asset_returns, dtype=float)
    if returns.ndim != 2 or returns.shape[1] != 4 or len(returns) != len(cycle_history):
        raise ValueError("cycle_view_requires_aligned_four_asset_returns")
    labels, raw_x, slices = cycle_probability_features_v5(cycle_history)
    x = raw_x[:-1]
    y = returns[1:] @ P_VIEWS_V5.T
    mask = np.ones(len(x), dtype=bool) if train_mask is None else np.asarray(train_mask, dtype=bool)[:-1]
    if len(mask) != len(x):
        raise ValueError("cycle_view_train_mask_length_mismatch")
    eligible = np.flatnonzero(mask)
    if len(eligible) < max(12, minimum_train):
        omega = np.cov(y.T) if len(y) > 3 else np.eye(3) * 1.0e-4
        omega = np.atleast_2d(omega) + np.eye(3) * 1.0e-8
        return {
            "status": "insufficient_history_no_cycle_views",
            "P": P_VIEWS_V5,
            "feature_names": labels,
            "feature_slices": slices,
            "feature_mean": np.zeros(raw_x.shape[1]),
            "intercept": np.zeros(3),
            "coefficient": np.zeros((raw_x.shape[1], 3)),
            "omega": omega,
            "selected_ridge": None,
            "oof_observations": 0,
        }

    train_x = x[eligible]
    train_y = y[eligible]
    feature_mean = np.mean(train_x, axis=0)
    centred = train_x - feature_mean
    scores: dict[float, float] = {}
    residuals_by_penalty: dict[float, list[np.ndarray]] = {}
    for penalty in ridge_grid:
        residuals: list[np.ndarray] = []
        for end in range(minimum_train, len(eligible)):
            prefix_x = centred[:end]
            prefix_y = train_y[:end]
            intercept, coefficient = _ridge_fit(prefix_x, prefix_y, float(penalty))
            prediction = intercept + centred[end] @ coefficient
            residuals.append(train_y[end] - prediction)
        if residuals:
            matrix = np.asarray(residuals)
            scores[float(penalty)] = float(np.mean(matrix ** 2))
            residuals_by_penalty[float(penalty)] = residuals
        else:
            scores[float(penalty)] = float("inf")
            residuals_by_penalty[float(penalty)] = []
    selected = min(scores, key=scores.get)
    intercept, coefficient = _ridge_fit(centred, train_y, selected)
    errors = residuals_by_penalty[selected]
    if len(errors) < 6:
        errors = list(train_y - (intercept + centred @ coefficient))
    error_matrix = np.asarray(errors, dtype=float)
    omega = np.cov(error_matrix.T, ddof=1) if len(error_matrix) > 1 else np.eye(3) * 1.0e-4
    omega = np.atleast_2d(omega)
    diagonal = np.diag(np.maximum(np.diag(omega), 1.0e-8))
    omega = 0.75 * omega + 0.25 * diagonal
    eigenvalues, eigenvectors = np.linalg.eigh((omega + omega.T) / 2.0)
    omega = eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0e-8)) @ eigenvectors.T
    return {
        "status": "ok",
        "P": P_VIEWS_V5,
        "feature_names": labels,
        "feature_slices": slices,
        "feature_mean": feature_mean,
        "intercept": intercept,
        "coefficient": coefficient,
        "omega": omega,
        "selected_ridge": selected,
        "ridge_scores": scores,
        "oof_observations": len(error_matrix),
        "policy": "joint cycle regression; ridge chosen by expanding training OOF MSE",
    }


def forecast_cycle_views_v5(
    fitted: Mapping[str, Any],
    prior_return: Sequence[float],
    current_cycle: Mapping[str, Any],
) -> ViewBundleV5:
    prior = np.asarray(prior_return, dtype=float)
    if prior.shape != (4,):
        raise ValueError("cycle_view_prior_must_have_four_assets")
    _, raw, slices = cycle_probability_features_v5([current_cycle])
    centred = raw[0] - np.asarray(fitted["feature_mean"], dtype=float)
    coefficient = np.asarray(fitted["coefficient"], dtype=float)
    intercept = np.asarray(fitted["intercept"], dtype=float)
    base_view = P_VIEWS_V5 @ prior
    contribution: dict[str, np.ndarray] = {}
    total = intercept.copy()
    for cycle, section in slices.items():
        value = centred[section] @ coefficient[section]
        if cycle == "kondratieff":
            value = np.zeros(3)
        contribution[cycle] = np.asarray(value, dtype=float)
        total += value
    q = base_view + total
    omega = np.asarray(fitted["omega"], dtype=float)
    diagnostics = {
        "status": str(fitted.get("status")),
        "selected_ridge": fitted.get("selected_ridge"),
        "oof_observations": int(fitted.get("oof_observations") or 0),
        "kondratieff_status": "display_only_insufficient_independent_cycles",
        "active_cycles": [
            cycle for cycle, value in contribution.items()
            if cycle != "kondratieff" and float(np.linalg.norm(value)) > 1.0e-12
        ],
    }
    return ViewBundleV5(
        P=P_VIEWS_V5.copy(),
        q=q,
        omega=omega,
        cycle_contributions=contribution,
        forecast_error_covariance=omega.copy(),
        diagnostics=diagnostics,
    )
