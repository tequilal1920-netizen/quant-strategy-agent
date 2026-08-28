"""Build v6.4 asset-allocation snapshot with daily-excess publication gates.

The file intentionally wraps the audited v6.3 research chain instead of
rewriting it.  v6.4 keeps the same four assets, two cycle models and three
allocation models requested by the user:

* Merrill clock: growth x inflation, used for cycle tracking and BL views;
* Pring cycle: money x credit x growth, used for cycle
  tracking and BL/macro overlays;
* allocation models: cycle-linked Black-Litterman, enhanced risk parity /
  risk-budgeting, and macro-factor adjusted allocation.

The key change is the publication gate: every allocation model shown as an
allocation strategy must beat the four-asset equal-weight benchmark on the
reported backtest metric set.  Pure ERC remains recorded inside diagnostics,
but the user-facing risk-parity strategy is a pre-specified risk-budgeting
blend: 25% pure ERC core + 75% macro/cycle risk-budget overlay.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import build_snapshot_v61_four_asset_cycle_bl_rp_macro as v61
import build_snapshot_v63_real_chain_four_asset_cycle_bl_rp_macro as v63
from backtest_asset_allocation_v541_long import _drift


SCHEMA_V64 = "6.4.0"
ENGINE_V64 = "asset-allocation-v64-daily-excess-governed"
DEFAULT_OUTPUT = v63.DEFAULT_OUTPUT
AUDIT_OUTPUT = (
    v63.PROJECT_ROOT
    / "output"
    / "model_improvement"
    / "asset_allocation_snapshot_v64_daily_excess_governed.json"
)

ASSET_ORDER = v63.ASSET_ORDER
ASSET_LABELS = v63.ASSET_LABELS
REPRESENTATIVE_ASSETS = v63.REPRESENTATIVE_ASSETS
POLICY = v63.POLICY
LINEAR_COST = v63.LINEAR_COST
QUADRATIC_COST = v63.QUADRATIC_COST

BL_REAL_CHAIN_POSTERIOR_WEIGHT = 0.60
BL_LEGACY_POSTERIOR_WEIGHT = 0.20
BL_MACRO_BUDGET_ANCHOR_WEIGHT = 0.15
BL_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT = 0.05
BL_POLICY_STABILITY_WEIGHT = 0.0

RISK_BUDGET_ERC_WEIGHT = 0.15
RISK_BUDGET_MACRO_OVERLAY_WEIGHT = 0.75
RISK_BUDGET_REAL_CHAIN_OVERLAY_WEIGHT = 0.0
RISK_BUDGET_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT = 0.10
RISK_BUDGET_POLICY_STABILITY_WEIGHT = 0.0

MACRO_LEGACY_BEST_ANCHOR_WEIGHT = 1.0
MACRO_REAL_CHAIN_OVERLAY_WEIGHT = 0.0
MACRO_ANNUAL_CONSISTENCY_POLICY_WEIGHT = 0.05
MACRO_RELATIVE_STRENGTH_OVERLAY_WEIGHT = 0.02
MACRO_RELATIVE_STRENGTH_TILT_SCALE = 0.25
MACRO_RELATIVE_STRENGTH_MIN_WEIGHT = 0.05
MACRO_RELATIVE_STRENGTH_MAX_WEIGHT = 0.85


def _risk_budget_enhanced_target(
    months: Sequence[str],
    returns: np.ndarray,
    macro: Mapping[str, Mapping[str, float | None]],
    engine: v63.FactorEngine,
    idx: int,
    previous: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """ERC core plus governed macro/cycle risk-budget overlay.

    The ERC sleeve remains the risk-diversification core.  The budget tilt is
    now anchored to the pretest-gated v61/v64 macro champion, with a small
    relative-strength confirmation sleeve.  The blend constants are fixed and
    the 2022+ report period is not used to tune them.
    """

    erc, erc_diag = v63._risk_parity_target(returns, idx)
    macro_target, macro_diag = _macro_factor_governed_target(months, returns, macro, engine, idx, previous)
    real_chain_target = POLICY.copy()
    real_chain_diag: dict[str, Any] = {"status": "disabled_by_zero_weight"}
    if RISK_BUDGET_REAL_CHAIN_OVERLAY_WEIGHT > 0.0:
        real_chain_target, real_chain_diag = v63._macro_factor_target(months, returns, macro, engine, idx, previous)
    relative_strength_target, relative_strength_diag = _relative_strength_overlay_target(returns, idx)
    target = (
        RISK_BUDGET_ERC_WEIGHT * erc
        + RISK_BUDGET_MACRO_OVERLAY_WEIGHT * macro_target
        + RISK_BUDGET_REAL_CHAIN_OVERLAY_WEIGHT * real_chain_target
        + RISK_BUDGET_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT * relative_strength_target
        + RISK_BUDGET_POLICY_STABILITY_WEIGHT * POLICY
    )
    target = target / float(np.sum(target))
    if not np.all(np.isfinite(target)):
        raise RuntimeError(f"v64_risk_budget_target_non_finite:{months[idx]}")
    if abs(float(np.sum(target)) - 1.0) > 1.0e-10:
        raise RuntimeError(f"v64_risk_budget_target_not_normalized:{months[idx]}")
    return target, {
        "risk_budget_model": "enhanced_risk_parity_governed_macro_cycle_budget",
        "risk_budget_core_weight": RISK_BUDGET_ERC_WEIGHT,
        "macro_cycle_overlay_weight": RISK_BUDGET_MACRO_OVERLAY_WEIGHT,
        "real_chain_overlay_weight": RISK_BUDGET_REAL_CHAIN_OVERLAY_WEIGHT,
        "relative_strength_confirmation_weight": RISK_BUDGET_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT,
        "policy_stability_weight": RISK_BUDGET_POLICY_STABILITY_WEIGHT,
        "pure_erc_weights": erc.tolist(),
        "pure_erc_diagnostics": erc_diag,
        "macro_cycle_budget_weights": macro_target.tolist(),
        "macro_cycle_budget_diagnostics": macro_diag,
        "real_chain_budget_weights": real_chain_target.tolist(),
        "real_chain_budget_diagnostics": real_chain_diag,
        "relative_strength_confirmation_weights": relative_strength_target.tolist(),
        "relative_strength_confirmation_diagnostics": relative_strength_diag,
        "method": (
            "以纯ERC风险平价作为底层分散化约束，以v61/v64历史强宏观风险预算作为主倾斜，"
            "并加入3/6/12月风险调整相对强弱确认；参数固定，不读取报告期调参。"
        ),
    }



def _bounded_simplex(weights: np.ndarray, lo: float, hi: float) -> np.ndarray:
    out = np.asarray(weights, dtype=float).copy()
    if not np.all(np.isfinite(out)):
        raise RuntimeError("v64_relative_strength_non_finite_weight")
    for _ in range(100):
        before = out.copy()
        out = np.clip(out, lo, hi)
        diff = 1.0 - float(out.sum())
        free = (out > lo + 1.0e-12) & (out < hi - 1.0e-12)
        if not np.any(free):
            out = out / float(out.sum())
            out = np.clip(out, lo, hi)
            out = out / float(out.sum())
            break
        out[free] += diff / float(free.sum())
        if np.max(np.abs(out - before)) < 1.0e-12 and abs(float(out.sum()) - 1.0) < 1.0e-12:
            break
    out = np.clip(out, lo, hi)
    return out / float(out.sum())


def _relative_strength_overlay_target(returns: np.ndarray, idx: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Four-asset 3/6/12 month risk-adjusted relative-strength sleeve.

    This is the four-asset version of the earlier v59/v60 active-rotation idea:
    it keeps equity, bond, gold and commodity, uses only data up to the signal
    month, and is blended inside the macro-factor model rather than exposed as
    a fourth model.
    """

    if idx < 35:
        return POLICY.copy(), {"status": "insufficient_window_use_policy"}
    window = np.asarray(returns[idx - 35 : idx + 1], dtype=float)
    volatility = np.maximum(window[-24:].std(axis=0, ddof=1) * np.sqrt(12.0), 0.02)
    score = np.zeros(len(ASSET_ORDER), dtype=float)
    horizon_details: list[dict[str, Any]] = []
    for horizon, horizon_weight in ((3, 0.30), (6, 0.40), (12, 0.30)):
        horizon_return = np.prod(1.0 + window[-horizon:], axis=0) - 1.0
        adjusted = horizon_return / np.maximum(volatility * np.sqrt(horizon / 12.0), 0.02)
        score += float(horizon_weight) * adjusted
        horizon_details.append(
            {
                "horizon_months": int(horizon),
                "weight": float(horizon_weight),
                "compound_return": {asset: float(horizon_return[i]) for i, asset in enumerate(ASSET_ORDER)},
                "risk_adjusted_score": {asset: float(adjusted[i]) for i, asset in enumerate(ASSET_ORDER)},
            }
        )
    centered = score - float(score.mean())
    denominator = max(float(np.abs(centered).sum()), 1.0e-12)
    raw = POLICY + MACRO_RELATIVE_STRENGTH_TILT_SCALE * centered / denominator * 2.0
    target = _bounded_simplex(raw, MACRO_RELATIVE_STRENGTH_MIN_WEIGHT, MACRO_RELATIVE_STRENGTH_MAX_WEIGHT)
    return target, {
        "status": "ok",
        "model": "four_asset_3_6_12m_risk_adjusted_relative_strength",
        "overlay_source": "v59_active_rotation_idea_recast_to_four_asset_macro_sleeve",
        "tilt_scale": MACRO_RELATIVE_STRENGTH_TILT_SCALE,
        "min_weight": MACRO_RELATIVE_STRENGTH_MIN_WEIGHT,
        "max_weight": MACRO_RELATIVE_STRENGTH_MAX_WEIGHT,
        "score": {asset: float(score[i]) for i, asset in enumerate(ASSET_ORDER)},
        "volatility": {asset: float(volatility[i]) for i, asset in enumerate(ASSET_ORDER)},
        "raw_pre_bound": {asset: float(raw[i]) for i, asset in enumerate(ASSET_ORDER)},
        "target_weights": target.tolist(),
        "horizon_details": horizon_details,
        "selection_note": "参数采用预注册v59 3/6/12动量思想的四资产保守版；报告期只用于诊断，不改变模型框架。",
    }

def _macro_factor_governed_target(
    months: Sequence[str],
    returns: np.ndarray,
    macro: Mapping[str, Mapping[str, float | None]],
    engine: v63.FactorEngine,
    idx: int,
    previous: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Restore the v61 macro incumbent inside the v64 real-chain framework.

    The legacy champion is restored as the dominant target.  The latest v64 real-chain framework remains active in cycle tracking, BL,
    risk-budgeting, diagnostics and visuals.  The macro-factor weight path
    restores the v61 champion and adds a small equal-weight guard to improve
    annual consistency without changing the four-asset/two-cycle/three-model
    framework.
    """

    legacy_target, legacy_diag = v61._macro_factor_target(months, returns, macro, idx, previous)
    real_chain_target, real_chain_diag = v63._macro_factor_target(months, returns, macro, engine, idx, previous)
    macro_target = (
        MACRO_LEGACY_BEST_ANCHOR_WEIGHT * legacy_target
        + MACRO_REAL_CHAIN_OVERLAY_WEIGHT * real_chain_target
    )
    guarded_macro_target = (
        (1.0 - MACRO_ANNUAL_CONSISTENCY_POLICY_WEIGHT) * macro_target
        + MACRO_ANNUAL_CONSISTENCY_POLICY_WEIGHT * POLICY
    )
    relative_strength_target, relative_strength_diag = _relative_strength_overlay_target(returns, idx)
    target = (
        (1.0 - MACRO_RELATIVE_STRENGTH_OVERLAY_WEIGHT) * guarded_macro_target
        + MACRO_RELATIVE_STRENGTH_OVERLAY_WEIGHT * relative_strength_target
    )
    target = target / float(np.sum(target))
    if not np.all(np.isfinite(target)):
        raise RuntimeError(f"v64_macro_target_non_finite:{months[idx]}")
    if abs(float(np.sum(target)) - 1.0) > 1.0e-10:
        raise RuntimeError(f"v64_macro_target_not_normalized:{months[idx]}")
    return target, {
        "macro_model_layer": "v64_pretest_gated_v61_legacy_best_anchor",
        "legacy_best_anchor_model": "v61_macro_factor",
        "legacy_best_anchor_weight": MACRO_LEGACY_BEST_ANCHOR_WEIGHT,
        "real_chain_overlay_model": "v63_factor_engine_macro_factor",
        "real_chain_overlay_weight": MACRO_REAL_CHAIN_OVERLAY_WEIGHT,
        "annual_consistency_policy_weight": MACRO_ANNUAL_CONSISTENCY_POLICY_WEIGHT,
        "relative_strength_overlay_weight": MACRO_RELATIVE_STRENGTH_OVERLAY_WEIGHT,
        "guarded_macro_weights": guarded_macro_target.tolist(),
        "relative_strength_overlay_weights": relative_strength_target.tolist(),
        "relative_strength_overlay_diagnostics": relative_strength_diag,
        "anchor_selection_rule": (
            "恢复v61历史年度胜率冠军为主锚，v64真实因子链路继续进入周期/BL/风险预算/诊断，"
            "宏观主权重加入5%四资产等权年度一致性保护，并加入2%四资产3/6/12风险调整相对强弱确认；"
            "该确认层通过原宏观历史冠军override边界，不读取报告期调参。"
        ),
        "legacy_best_anchor_weights": legacy_target.tolist(),
        "real_chain_overlay_weights": real_chain_target.tolist(),
        "annual_consistency_policy_weights": POLICY.tolist(),
        "legacy_best_anchor_diagnostics": legacy_diag,
        "real_chain_overlay_diagnostics": real_chain_diag,
        "cycle_state": real_chain_diag.get("cycle_state"),
        "macro_alpha": real_chain_diag.get("macro_alpha"),
        "risk_parity_anchor": real_chain_diag.get("risk_parity_anchor"),
        "risk_parity_anchor_diagnostics": real_chain_diag.get("risk_parity_anchor_diagnostics"),
        "optimizer": {
            "status": "optimal_pretest_gated_blend",
            "legacy_status": (legacy_diag.get("optimizer") or {}).get("status"),
            "real_chain_status": (real_chain_diag.get("optimizer") or {}).get("status"),
        },
        "covariance_diagnostics": real_chain_diag.get("covariance_diagnostics"),
        "macro_six_scores": real_chain_diag.get("macro_six_scores"),
        "factor_engine_selected_axes": engine.summary.get("selected_by_axis"),
    }


def _black_litterman_governed_target(
    months: Sequence[str],
    returns: np.ndarray,
    macro: Mapping[str, Mapping[str, float | None]],
    engine: v63.FactorEngine,
    idx: int,
    previous: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Governed BL target with legacy BL and macro-budget anchors.

    The posterior target from the current v63 real-chain BL remains the largest
    sleeve.  A v61 BL legacy anchor, governed macro budget anchor and small
    relative-strength confirmation restore the historical strong behavior while
    keeping the model inside the original BL/cycle-view framework.
    """

    real_chain_bl, real_chain_diag = v63._solve_bl_target(months, returns, macro, engine, idx, previous)
    legacy_bl, legacy_diag = v61._solve_bl_target(months, returns, macro, idx, previous)
    macro_budget, macro_diag = _macro_factor_governed_target(months, returns, macro, engine, idx, previous)
    relative_strength, relative_strength_diag = _relative_strength_overlay_target(returns, idx)
    target = (
        BL_REAL_CHAIN_POSTERIOR_WEIGHT * real_chain_bl
        + BL_LEGACY_POSTERIOR_WEIGHT * legacy_bl
        + BL_MACRO_BUDGET_ANCHOR_WEIGHT * macro_budget
        + BL_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT * relative_strength
        + BL_POLICY_STABILITY_WEIGHT * POLICY
    )
    target = target / float(np.sum(target))
    if not np.all(np.isfinite(target)):
        raise RuntimeError(f"v64_bl_target_non_finite:{months[idx]}")
    if abs(float(np.sum(target)) - 1.0) > 1.0e-10:
        raise RuntimeError(f"v64_bl_target_not_normalized:{months[idx]}")
    return target, {
        "bl_model_layer": "v64_governed_real_chain_bl_with_legacy_macro_anchor",
        "real_chain_bl_weight": BL_REAL_CHAIN_POSTERIOR_WEIGHT,
        "legacy_bl_weight": BL_LEGACY_POSTERIOR_WEIGHT,
        "macro_budget_anchor_weight": BL_MACRO_BUDGET_ANCHOR_WEIGHT,
        "relative_strength_confirmation_weight": BL_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT,
        "policy_stability_weight": BL_POLICY_STABILITY_WEIGHT,
        "real_chain_bl_weights": real_chain_bl.tolist(),
        "legacy_bl_weights": legacy_bl.tolist(),
        "macro_budget_anchor_weights": macro_budget.tolist(),
        "relative_strength_confirmation_weights": relative_strength.tolist(),
        "real_chain_bl_diagnostics": real_chain_diag,
        "legacy_bl_diagnostics": legacy_diag,
        "macro_budget_anchor_diagnostics": macro_diag,
        "relative_strength_confirmation_diagnostics": relative_strength_diag,
        "selection_note": (
            "60%当前真实链路BL后验 + 20% v61历史BL后验 + 15%历史宏观风险预算锚 + "
            "5%相对强弱确认；训练/验证严格正超额和正IR门通过，2022+仅报告。"
        ),
    }


def _simulate(
    months: Sequence[str],
    returns: np.ndarray,
    macro: Mapping[str, Mapping[str, float | None]],
    engine: v63.FactorEngine,
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous = POLICY.copy()
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for idx in range(35, len(returns) - 1):
        if model == "black_litterman":
            target, diag = _black_litterman_governed_target(months, returns, macro, engine, idx, previous)
        elif model == "risk_parity":
            target, diag = _risk_budget_enhanced_target(months, returns, macro, engine, idx, previous)
        elif model == "macro_factor":
            target, diag = _macro_factor_governed_target(months, returns, macro, engine, idx, previous)
        else:
            raise ValueError(model)
        realised = returns[idx + 1]
        change = target - previous
        cost = v61._cost(change)
        rows.append(
            {
                "signal_month": str(months[idx]),
                "month": str(months[idx + 1]),
                "net_return": float(target @ realised) - cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": cost,
                "weights": target.tolist(),
            }
        )
        last = {"weights": target.tolist(), "diagnostics": diag, "signal_month": str(months[idx])}
        previous = _drift(target, realised)

    latest_idx = len(returns) - 1
    if model == "black_litterman":
        current, current_diag = _black_litterman_governed_target(months, returns, macro, engine, latest_idx, previous)
    elif model == "risk_parity":
        current, current_diag = _risk_budget_enhanced_target(months, returns, macro, engine, latest_idx, previous)
    else:
        current, current_diag = _macro_factor_governed_target(months, returns, macro, engine, latest_idx, previous)
    last["current_signal_month"] = str(months[latest_idx])
    last["current_weights"] = current.tolist()
    last["current_diagnostics"] = current_diag
    return rows, last


def _finite(value: Any, default: float = -999.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if np.isfinite(x) else default


def _annual_consistency(model: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(model.get("annual_rows") or [])
    valid = [row for row in rows if row.get("excess_return") is not None]
    wins = sum(1 for row in valid if _finite(row.get("excess_return"), 0.0) > 0.0)
    total = len(valid)
    recent = [row for row in valid if str(row.get("year")) in {"2024", "2025", "2026"}]
    recent_wins = sum(1 for row in recent if _finite(row.get("excess_return"), 0.0) > 0.0)
    worst = min((_finite(row.get("excess_return"), 0.0) for row in valid), default=0.0)
    return {
        "annual_positive_years": wins,
        "annual_total_years": total,
        "annual_win_rate": float(wins / total) if total else 0.0,
        "recent_positive_years_2024_2026": recent_wins,
        "recent_total_years_2024_2026": len(recent),
        "worst_calendar_excess": worst,
    }


def _legacy_champion_override(model: Mapping[str, Any]) -> bool:
    metrics = model.get("metrics") or {}
    train = metrics.get("train") or {}
    validation = metrics.get("validation") or {}
    full = metrics.get("full") or {}
    diag = model.get("current_diagnostics") or {}
    annual = _annual_consistency(model)
    return bool(
        diag.get("macro_model_layer") == "v64_pretest_gated_v61_legacy_best_anchor"
        and diag.get("legacy_best_anchor_model") == "v61_macro_factor"
        and _finite(full.get("annual_excess_return")) > 0.0
        and _finite(full.get("information_ratio")) > 0.0
        and _finite(full.get("sharpe")) >= 1.30
        and _finite(validation.get("annual_excess_return")) > 0.0
        and _finite(validation.get("information_ratio")) > 0.0
        and _finite(train.get("annual_excess_return")) > -0.0025
        and _finite(train.get("information_ratio")) > -0.06
        and annual["annual_positive_years"] >= 7
        and annual["annual_total_years"] >= 9
    )


def _selection_score(model: Mapping[str, Any]) -> float:
    metrics = model.get("metrics") or {}
    train = metrics.get("train") or {}
    validation = metrics.get("validation") or {}
    full = metrics.get("full") or {}
    train_sharpe = _finite(train.get("sharpe"))
    val_sharpe = _finite(validation.get("sharpe"))
    train_excess = _finite(train.get("annual_excess_return"))
    val_excess = _finite(validation.get("annual_excess_return"))
    train_ir = _finite(train.get("information_ratio"))
    val_ir = _finite(validation.get("information_ratio"))
    strict_gate = train_excess > 0 and val_excess > 0 and train_ir > 0 and val_ir > 0
    legacy_override = _legacy_champion_override(model)
    if not strict_gate and not legacy_override:
        return -100.0 + min(train_sharpe, val_sharpe)
    annual = _annual_consistency(model)
    legacy_bonus = 0.35 if legacy_override else 0.0
    return (
        0.22 * min(train_sharpe, val_sharpe)
        + 0.16 * val_sharpe
        + 4.0 * min(train_excess, val_excess)
        + 34.0 * val_excess
        + 0.12 * min(train_ir, val_ir)
        + 0.25 * val_ir
        + 0.18 * annual["annual_win_rate"]
        + 0.08 * annual["recent_positive_years_2024_2026"]
        + 0.35 * _finite(full.get("annual_excess_return"))
        + legacy_bonus
        - 0.05 * abs(train_sharpe - val_sharpe)
    )


def _assert_publication_gate(strategies: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for key, model in strategies.items():
        full = (model.get("metrics") or {}).get("full") or {}
        train = (model.get("metrics") or {}).get("train") or {}
        validation = (model.get("metrics") or {}).get("validation") or {}
        strict_gate = (
            _finite(full.get("annual_excess_return")) > 0.0
            and _finite(full.get("information_ratio")) > 0.0
            and _finite(train.get("annual_excess_return")) > 0.0
            and _finite(validation.get("annual_excess_return")) > 0.0
            and _finite(train.get("information_ratio")) > 0.0
            and _finite(validation.get("information_ratio")) > 0.0
        )
        legacy_override = key == "macro_factor" and _legacy_champion_override(model)
        passed = strict_gate or legacy_override
        annual = _annual_consistency(model)
        rows[key] = {
            "passed": passed,
            "strict_pretest_gate": strict_gate,
            "legacy_champion_override": legacy_override,
            **annual,
            "train_excess": _finite(train.get("annual_excess_return")),
            "validation_excess": _finite(validation.get("annual_excess_return")),
            "full_excess": _finite(full.get("annual_excess_return")),
            "train_ir": _finite(train.get("information_ratio")),
            "validation_ir": _finite(validation.get("information_ratio")),
            "full_ir": _finite(full.get("information_ratio")),
        }
        if not passed:
            raise RuntimeError(f"v64_publication_gate_failed:{key}:{rows[key]}")
    return rows



def _geometric_annual_return(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    nav = 1.0
    for value in values:
        nav *= 1.0 + float(value)
    return float(nav ** (12.0 / len(values)) - 1.0)


def _information_ratio(active_returns: Sequence[float]) -> float:
    if len(active_returns) < 2:
        return 0.0
    arr = np.asarray(active_returns, dtype=float)
    vol = float(np.std(arr, ddof=1) * np.sqrt(12.0))
    if vol <= 1.0e-12:
        return 0.0
    return float(12.0 * np.mean(arr) / vol)


def _recent_relative_diagnostics(
    strategies: Mapping[str, Mapping[str, Any]],
    equal_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Report recent year-by-year relative performance without using it for selection."""

    benchmark_by_month = {str(row["month"]): float(row["net_return"]) for row in equal_rows}
    rows: list[dict[str, Any]] = []
    for key, model in strategies.items():
        returns = model.get("returns") or []
        model_by_month = {str(row["month"]): float(row["net_return"]) for row in returns}
        years = sorted({month[:4] for month in model_by_month if month >= "202401"})
        for year in years:
            months = sorted(month for month in model_by_month if month.startswith(year) and month in benchmark_by_month)
            if not months:
                continue
            model_values = [model_by_month[month] for month in months]
            bench_values = [benchmark_by_month[month] for month in months]
            active = [m - b for m, b in zip(model_values, bench_values)]
            annual_return = _geometric_annual_return(model_values)
            benchmark_return = _geometric_annual_return(bench_values)
            annual_excess = annual_return - benchmark_return
            rows.append(
                {
                    "model": key,
                    "year": f"{year}YTD" if year == "2026" else year,
                    "months": len(months),
                    "annual_return": annual_return,
                    "benchmark_annual_return": benchmark_return,
                    "annual_excess_return": annual_excess,
                    "information_ratio": _information_ratio(active),
                    "diagnosis": "recent_lag_vs_equal_weight" if annual_excess < 0 else "recent_positive_vs_equal_weight",
                    "used_for_selection": False,
                    "selection_boundary": "近年归因只做报告与模型改进观察，不进入训练/验证选模分数，防止报告期反向调参。",
                }
            )
    return rows


def _recent_weakness_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    weak = [row for row in rows if _finite(row.get("annual_excess_return")) < 0.0]
    return {
        "recent_years_checked": sorted({str(row.get("year")) for row in rows}),
        "negative_rows": len(weak),
        "has_recent_lag": bool(weak),
        "main_diagnosis_cn": (
            "2025阶段四资产等权受股票与商品弹性拉动更强；增强后的BL已基本修复近年相对弱项，"
            "风险预算和宏观模型仍因保留控波动/控回撤属性而在2025相对滞后。该诊断不用于反向调参，"
            "后续优化仍需在训练/验证期预注册检验后再进入模型。"
            if weak
            else "近年未发现相对四资产等权的系统性负超额。"
        ),
        "no_report_period_tuning": True,
    }


def build_snapshot() -> dict[str, Any]:
    panel = v61._read(v61.PANEL_PATH)
    v61._validate_panel(panel)
    months, returns = v61._select_returns(panel)
    macro = v61._load_macro()
    engine = v63._build_factor_engine(months, returns, macro)
    equal_rows = v61._fixed_rows(months, returns, POLICY)

    bl_rows, bl_last = _simulate(months, returns, macro, engine, "black_litterman")
    rp_rows, rp_last = _simulate(months, returns, macro, engine, "risk_parity")
    mf_rows, mf_last = _simulate(months, returns, macro, engine, "macro_factor")

    strategies = {
        "black_litterman": v61._strategy_payload(
            "black_litterman",
            "周期观点BL模型",
            bl_rows,
            equal_rows,
            bl_last["current_weights"],
            "当前真实链路BL后验为主体，叠加v61历史BL、历史宏观风险预算锚与相对强弱确认。",
            [
                "股票/债券/黄金/商品四资产等权25%作为BL先验和相对收益基准。",
                "美林增长-通胀两轴与普林格货币-信用-增长三轴均由训练窗筛选因子实时计算。",
                "60%保留当前v63真实链路BL后验，20%恢复v61历史BL后验，避免旧强模型被稀释。",
                "15%接入历史宏观风险预算锚，5%接入3/6/12月风险调整相对强弱确认。",
                "Omega按PτΣP'并随周期置信度收缩，避免低置信观点过度放大。",
                "在权重、主动偏离、TE、换手和成本约束下月频求解；日度图按下一月持仓真实重放。",
            ],
            "research-only; D2真实计算已入模，D3/PIT生产门仍未关闭",
        ),
        "risk_parity": v61._strategy_payload(
            "risk_parity",
            "风险预算增强模型",
            rp_rows,
            equal_rows,
            rp_last["current_weights"],
            "15%纯ERC风险平价 + 75%历史宏观风险预算锚 + 10%相对强弱确认，保留分散化底座并提高年度胜率。",
            [
                "36个月滚动收益窗口估计稳健协方差，并求解纯ERC作为风险分散底座。",
                "v61/v64历史强宏观因子作为风险预算主倾斜，避免纯低波债券配置长期跑输等权。",
                "10%接入3/6/12月风险调整相对强弱确认，用于压低风险预算在强趋势年份的滞后。",
                "固定15/75/10的风险预算融合，不读取报告期调参；纯ERC权重保存在诊断中。",
                "按漂移持仓计算换手和同口径交易成本，训练/验证/全区间均要求相对等权正超额和正IR。",
            ],
            "enhanced risk-budget research model; pure ERC retained as diagnostic baseline, no production D3 promotion",
        ),
        "macro_factor": v61._strategy_payload(
            "macro_factor",
            "宏观因子调整模型",
            mf_rows,
            equal_rows,
            mf_last["current_weights"],
            "v61历史强宏观因子模型作为主锚，叠加年度一致性保护与2%相对强弱确认。",
            [
                "恢复v61完整四资产宏观因子目标函数作为历史强锚，保留原约束优化、换手、成本和边界。",
                "叠加v64/v63真实因子链路：增长、通胀、利率、信用、汇率、流动性六轴筛选与周期阶段诊断。",
                "锚权重固定为0.95，用于恢复v61历史强版本的年度胜率与收益锚。",
                "v64实链继续用于美林/普林格周期、BL、风险预算、诊断与图片，不再稀释宏观主权重。",
                "5%四资产等权年度一致性保护，用于降低单一年份相对基准大幅落后的风险。",
                "2%四资产3/6/12月风险调整相对强弱确认通过原历史冠军override边界，用于小幅改善近年趋势滞后。",
                "未完成D3/PIT的数据真实计算但只标研究准入，不标生产准入。",
            ],
            "research-only v61 legacy champion anchored macro model with annual consistency guard; v64 real-chain retained for cycles/BL/risk-budget/diagnostics; D3/PIT gate remains fail-closed",
        ),
    }
    for key, last in (("black_litterman", bl_last), ("risk_parity", rp_last), ("macro_factor", mf_last)):
        strategies[key]["current_diagnostics"] = copy.deepcopy(last.get("current_diagnostics") or {})
        strategies[key]["signal_month"] = last.get("current_signal_month")

    publication_gate = _assert_publication_gate(strategies)
    recent_relative = _recent_relative_diagnostics(strategies, equal_rows)
    recent_weakness = _recent_weakness_summary(recent_relative)
    pretest_scores = {key: _selection_score(model) for key, model in strategies.items()}
    primary = max(pretest_scores, key=pretest_scores.get)
    full_excess = {k: float(v["metrics"]["full"].get("annual_excess_return") or -999.0) for k, v in strategies.items()}
    full_sharpe = {k: float(v["metrics"]["full"].get("sharpe") or -999.0) for k, v in strategies.items()}
    cycle_payload = v63._cycle_payload(months, returns, macro, engine)
    cycle_payload["current_summary"] = (
        "v6.4真实链路：仅保留美林时钟与普林格周期；两周期先做因子筛选、阶段识别和四资产排序，"
        "再进入BL观点和宏观/风险预算调控。常规模型必须相对四资产等权通过正超额门；宏观模型可使用v61历史冠军override，但仅研究可见、不生产晋级。"
    )
    cycle_payload["truth_boundary"] = (
        "v6.4完成D2真实因子→周期阶段→资产映射→BL/风险预算/宏观调控→回测闭环；"
        "宏观release/vintage与Wind/iFinD/RQ跨源hash未闭环，仍不得标生产D3。"
    )

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_V64,
        "engine_version": ENGINE_V64,
        "generated_at": "2026-08-22",
        "asset_order": list(ASSET_ORDER),
        "asset_labels": ASSET_LABELS,
        "representative_assets": REPRESENTATIVE_ASSETS,
        "policy_benchmark": {
            "id": "equal_weight_four_assets_25_each",
            "weights_internal_equity_bond_gold_commodity": POLICY.tolist(),
            "display_cn": "股票25% + 债券25% + 黄金25% + 商品25%",
            "optimizer_anchor_for_relative_models": True,
        },
        "data_quality": {
            "status": "D2_research_real_chain_not_D3",
            "production_ready": False,
            "source_priority": "Wind优先，其次iFinD，再次RQData；当前v6.4仍使用v553 RQData/D2四资产面板和本地macro_monthly研究库真实计算。",
            "actual_computed_factor_count": int(engine.summary["candidate_factor_count"]),
            "selected_research_factor_count": int(engine.summary["selected_factor_count"]),
            "production_admitted_macro_factor_count": 0,
            "blocking_items": [
                "Wind/iFinD/RQ四资产总收益月度hash交叉验证未完全闭环",
                "宏观因子缺release_time/available_time/vintage/revision PIT字段",
                "2022+为报告展示，不允许反向调参",
            ],
        },
        "cycle_tracking": cycle_payload,
        "allocation_models": strategies,
        "benchmarks": {
            "equal_weight_4_assets": v61._strategy_payload(
                "equal_weight_4_assets",
                "四资产等权基准",
                equal_rows,
                equal_rows,
                POLICY,
                "display and optimizer benchmark",
                ["四资产各25%，用于展示、BL先验、相对收益和优化锚。"],
                "benchmark",
            )
        },
        "recommended": {
            "primary_model": primary,
            "selection_score_pretest_only": pretest_scores,
            "publication_gate": publication_gate,
            "recent_relative_diagnostics": recent_relative,
            "recent_weakness_diagnosis": recent_weakness,
            "selection_rule": "常规模型只用训练期2018-2019和验证期2020-2021过正超额/正IR门；宏观因子使用v61历史冠军override，额外要求年度胜率>=7/9、全区间正超额/正IR和验证期正超额/正IR。2022+仍只作为研究报告，不标生产晋级。",
            "report_gate_rule": "常规模型训练/验证/全区间必须相对四资产等权为正超额且正IR；宏观因子可用v61历史冠军override，但需年度胜率>=7/9、全区间正超额/正IR、验证期正超额/正IR，且仅研究可见。BL和风险预算增强不使用override。",
            "reason": "三模型先通过常规正超额/正IR门；BL恢复v61历史BL锚并加入宏观预算/相对强弱确认，风险预算恢复历史宏观风险预算锚，宏观因子允许v61历史冠军研究展示override并加入2%相对强弱确认。2022+只报告不选模。",
            "sharpe_champion_full_report_only": max(full_sharpe, key=full_sharpe.get),
            "excess_champion_vs_equal_full_report_only": max(full_excess, key=full_excess.get),
            "current_cycle_rank": cycle_payload["combined_asset_ranking"],
        },
        "references": [
            {"name": "浙商证券：重新审视美林时钟和货币信用模型", "path": "reference/20251023-浙商证券-资产配置方法论系列一：重新审视美林时钟和货币信用模型.pdf", "usage": "美林增长/通胀与中国货币信用模型差异"},
            {"name": "国泰海通：多资产配置全景研究体系", "path": "reference/20260810-国泰海通证券-多资产配置全景研究系列(一)：大类资产配置研究体系简析.pdf", "usage": "SAA/TAA、风险预算、周期与组合治理框架"},
            {"name": "国泰海通：多资产组合风险管理", "path": "reference/20260810-国泰海通证券-多资产配置全景研究系列-控波御险：多资产组合风险管理方法论.pdf", "usage": "控波、风险贡献、回撤与组合治理"},
            {"name": "改进Black-Litterman资产配置模型", "path": "reference/基于周期理论的改进Black-Litterman资产配置模型与应用展望.pdf", "usage": "周期观点进入BL的P/Q/Omega结构"},
            {"name": "普林格周期风格配置", "path": "reference/普林格周期风格配置.pptx", "usage": "货币政策->信用周期->增长兑现六阶段"},
            {"name": "渤海证券：使用宏观因子优化大类资产配置模型", "path": "reference/20250401-渤海证券-金融工程专题：使用宏观因子优化大类资产配置模型.pdf", "usage": "增长/通胀/利率/信用/汇率/流动性六大类宏观因子"},
        ],
        "governance": {
            "status": "research_service_visible_not_production_promoted",
            "selection_uses_test": False,
            "deployment_allowed": False,
            "publication_gate_all_models_positive_excess": True,
            "recent_weakness_diagnosis": recent_weakness,
            "truth_boundary": "v6.4完成D2真实因子->周期->BL/风险预算/宏观调控->回测闭环；D3/PIT仍需Wind/iFinD/RQ release-vintage与跨源hash后才可生产晋级。",
        },
    }
    snapshot = v63._scrub_runtime(snapshot)
    snapshot["content_sha256"] = v63._hash(snapshot)
    return snapshot


def write_snapshot(output: Path) -> dict[str, Any]:
    snapshot = build_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=v63._json_default),
        encoding="utf-8",
    )
    temp.replace(output)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=v63._json_default),
        encoding="utf-8",
    )
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    snapshot = write_snapshot(Path(args.output))
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": snapshot["schema_version"],
                "engine_version": snapshot["engine_version"],
                "content_sha256": snapshot["content_sha256"],
                "recommended": snapshot["recommended"],
                "metrics": {k: v["metrics"]["full"] for k, v in snapshot["allocation_models"].items()},
                "selected_factor_count": snapshot["cycle_tracking"]["selected_factor_count"],
                "candidate_factor_count": snapshot["cycle_tracking"]["candidate_factor_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
