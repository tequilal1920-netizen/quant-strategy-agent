"""Build v6.3 real-chain four-asset asset-allocation snapshot.

v6.3 keeps the user-facing scope deliberately narrow and deep:

* cycle tracking: Merrill clock and China Pring cycle only;
* allocation models: cycle-linked Black-Litterman, risk parity and macro-factor
  adjusted allocation only;
* assets: equity, bond, gold and ex-precious commodity;
* benchmark: equal weight across the four assets.

The upgrade over v6.1/v6.2 is that the large macro/factor universe is no longer
just a catalogue.  A deterministic factor engine constructs rolling, point-in-
time-safe research features from the available local D2 macro/market panel,
selects usable factors only on the pre-report training window, feeds the selected
axis scores into the two cycle models, and links those cycle conclusions into BL
views and the macro overlay.  Production D3/PIT admission remains fail-closed:
the available macro table still lacks release-time/vintage fields, so the page
must show the model as a deployed research service, not a production-promoted
trading model.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

import build_snapshot_v61_four_asset_cycle_bl_rp_macro as v61  # noqa: E402
from allocation_math_v5 import black_litterman_posterior_v5, estimate_statistical_covariance_v5, solve_erc_v5  # noqa: E402
from backtest_asset_allocation_v541_long import _drift  # noqa: E402
from convex_optimizer_v539 import optimize_relative_v539  # noqa: E402


SCHEMA_V63 = "6.3.0"
ENGINE_V63 = "asset-allocation-v63-real-chain-factor-selected-cycle-bl-rp-macro"
DEFAULT_OUTPUT = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data" / "asset_allocation_snapshot.json"
AUDIT_OUTPUT = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_snapshot_v63_real_chain_four_asset_cycle_bl_rp_macro.json"

ASSET_ORDER = v61.ASSET_ORDER
ASSET_LABELS = v61.ASSET_LABELS
REPRESENTATIVE_ASSETS = v61.REPRESENTATIVE_ASSETS
POLICY = v61.POLICY
LINEAR_COST = v61.LINEAR_COST
QUADRATIC_COST = v61.QUADRATIC_COST

AXIS_ORDER = ("growth", "inflation", "money", "credit", "interest_rate", "fx", "liquidity", "confirmation")
AXIS_LABELS = {
    "growth": "增长",
    "inflation": "通胀",
    "money": "货币",
    "credit": "信用",
    "interest_rate": "利率",
    "fx": "汇率",
    "liquidity": "流动性",
    "confirmation": "市场确认",
}
TRANSFORMS = ("level_z", "change_1m", "change_3m", "change_6m", "change_12m", "hp_cycle", "fft_low", "percentile", "slope_6m")
MAX_SELECTED_PER_AXIS = 9
TRAIN_SELECTION_YEARS = {"2018", "2019"}


@dataclass(frozen=True)
class FactorDefinition:
    base_id: str
    label: str
    source_type: str
    axes: tuple[str, ...]
    series: np.ndarray
    source_status: str


@dataclass(frozen=True)
class SelectedFactor:
    factor_id: str
    base_label: str
    transform: str
    axis: str
    sign: float
    score: float
    train_ic: float
    train_hit_rate: float
    coverage: float


@dataclass(frozen=True)
class FactorEngine:
    months: tuple[str, ...]
    feature_values: Mapping[str, np.ndarray]
    factor_rows: tuple[dict[str, Any], ...]
    selected_by_axis: Mapping[str, tuple[SelectedFactor, ...]]
    axis_scores: Mapping[str, np.ndarray]
    axis_confidence: Mapping[str, float]
    summary: Mapping[str, Any]


@dataclass(frozen=True)
class CycleStateV63:
    month: str
    merrill_growth: float
    merrill_inflation: float
    merrill_stage: str
    merrill_confidence: float
    merrill_scores: np.ndarray
    pring_money: float
    pring_credit: float
    pring_growth: float
    pring_confirmation: float
    pring_stage: str
    pring_confidence: float
    pring_scores: np.ndarray
    combined_scores: np.ndarray
    combined_rank: list[str]
    macro_six_scores: Mapping[str, float]
    selected_axis_scores: Mapping[str, float]
    base_cycle_state: Mapping[str, Any]


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (SelectedFactor,)):
        return value.__dict__
    raise TypeError(type(value).__name__)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_default).encode("utf-8")
    ).hexdigest().upper()


def _finite(value: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_corr(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 8:
        return 0.0
    a = x[mask]
    b = y[mask]
    if float(a.std(ddof=1)) <= 1.0e-12 or float(b.std(ddof=1)) <= 1.0e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _safe_hit(x: np.ndarray, y: np.ndarray, sign: float) -> float:
    mask = np.isfinite(x) & np.isfinite(y) & (np.abs(x) > 1.0e-12) & (np.abs(y) > 1.0e-12)
    if int(mask.sum()) < 8:
        return 0.5
    return float(np.mean(np.sign(sign * x[mask]) == np.sign(y[mask])))


def _rolling_z_value(value: float, history: np.ndarray) -> float:
    finite = np.asarray(history[np.isfinite(history)], dtype=float)
    if not math.isfinite(value) or finite.size < 12:
        return float("nan")
    center = float(np.nanmedian(finite[-60:]))
    scale = float(1.4826 * np.nanmedian(np.abs(finite[-60:] - center)))
    if scale <= 1.0e-8:
        scale = float(np.nanstd(finite[-60:], ddof=1)) if finite.size > 2 else 1.0
    if scale <= 1.0e-8:
        return 0.0
    return float(np.clip((value - center) / scale, -4.0, 4.0))


def _change_history(series: np.ndarray, h: int, end_exclusive: int) -> np.ndarray:
    values = []
    for j in range(h, end_exclusive):
        a = series[j]
        b = series[j - h]
        values.append(float(a - b) if math.isfinite(float(a)) and math.isfinite(float(b)) else float("nan"))
    return np.asarray(values, dtype=float)


def _slope(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    if finite.size < 3 or not np.all(np.isfinite(finite)):
        return float("nan")
    x = np.arange(finite.size, dtype=float)
    x = x - float(x.mean())
    denom = float(np.dot(x, x))
    if denom <= 1.0e-12:
        return float("nan")
    return float(np.dot(x, finite - float(finite.mean())) / denom)


def _feature_values(series: np.ndarray, transform: str) -> np.ndarray:
    arr = np.asarray(series, dtype=float)
    out = np.full(arr.shape, np.nan, dtype=float)
    for i in range(arr.size):
        history = arr[: i + 1]
        value = float(arr[i]) if math.isfinite(float(arr[i])) else float("nan")
        if transform == "level_z":
            out[i] = _rolling_z_value(value, arr[:i])
        elif transform.startswith("change_"):
            h = int(transform.split("_")[1].replace("m", ""))
            if i >= h and math.isfinite(value) and math.isfinite(float(arr[i - h])):
                out[i] = _rolling_z_value(value - float(arr[i - h]), _change_history(arr, h, i))
        elif transform == "hp_cycle":
            if np.isfinite(history).sum() >= 36:
                cycle = v61._hp_cycle(history)
                out[i] = _rolling_z_value(float(cycle[-1]), cycle[:-1])
        elif transform == "fft_low":
            if np.isfinite(history).sum() >= 36:
                cycle = v61._fft_cycle(history)
                out[i] = _rolling_z_value(float(cycle[-1]), cycle[:-1])
        elif transform == "percentile":
            finite = arr[max(0, i - 60) : i]
            finite = finite[np.isfinite(finite)]
            if finite.size >= 12 and math.isfinite(value):
                out[i] = float(np.clip((np.mean(finite <= value) - 0.5) * 2.0, -1.0, 1.0))
        elif transform == "slope_6m":
            if i >= 5:
                current = _slope(arr[i - 5 : i + 1])
                hist = np.asarray([_slope(arr[j - 5 : j + 1]) for j in range(5, i)], dtype=float)
                out[i] = _rolling_z_value(current, hist)
        else:
            raise ValueError(transform)
    return out


def _macro_array(macro: Mapping[str, Mapping[str, float | None]], months: Sequence[str], field: str) -> np.ndarray:
    return np.asarray([float((macro.get(month) or {}).get(field)) if (macro.get(month) or {}).get(field) is not None else np.nan for month in months], dtype=float)


def _log_nav_from_returns(returns: np.ndarray, asset_index: int) -> np.ndarray:
    r = np.asarray(returns[:, asset_index], dtype=float)
    nav = np.cumprod(1.0 + np.where(np.isfinite(r), r, 0.0))
    return np.log(np.maximum(nav, 1.0e-12))


def _base_definitions(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]]) -> list[FactorDefinition]:
    pmi = _macro_array(macro, months, "pmi_manufacturing")
    pmi_non = _macro_array(macro, months, "pmi_non_manufacturing")
    pmi_comp = _macro_array(macro, months, "pmi_composite")
    cpi = _macro_array(macro, months, "cpi_national_yoy")
    ppi = _macro_array(macro, months, "ppi_yoy")
    m1 = _macro_array(macro, months, "m1_yoy")
    m2 = _macro_array(macro, months, "m2_yoy")
    sf_inc = _macro_array(macro, months, "sf_inc_month")
    sf_stock = _macro_array(macro, months, "sf_stock_endval")
    m1_m2 = m1 - m2
    ppi_cpi = ppi - cpi
    sf_inc_yoy = v61._yoy_from_level(sf_inc)
    sf_stock_yoy = v61._yoy_from_level(sf_stock)
    definitions: list[FactorDefinition] = [
        FactorDefinition("pmi_manufacturing", "PMI制造业", "macro_monthly:tushare+akshare", ("growth", "confirmation"), pmi - 50.0, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("pmi_non_manufacturing", "PMI非制造业", "macro_monthly:tushare+akshare", ("growth",), pmi_non - 50.0, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("pmi_composite", "PMI综合", "macro_monthly:tushare+akshare", ("growth", "confirmation"), pmi_comp - 50.0, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("cpi_yoy", "CPI同比", "macro_monthly:tushare+akshare", ("inflation",), cpi, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("ppi_yoy", "PPI同比", "macro_monthly:tushare+akshare", ("inflation", "growth"), ppi, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("ppi_cpi_spread", "PPI-CPI剪刀差", "macro_monthly:derived", ("inflation", "growth"), ppi_cpi, "D2派生；发布时间/vintage未验证"),
        FactorDefinition("m1_yoy", "M1同比", "macro_monthly:tushare+akshare", ("liquidity", "money", "credit"), m1, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("m2_yoy", "M2同比", "macro_monthly:tushare+akshare", ("liquidity", "money"), m2, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("m1_m2_spread", "M1-M2剪刀差", "macro_monthly:derived", ("liquidity", "money", "credit"), m1_m2, "D2派生；发布时间/vintage未验证"),
        FactorDefinition("sf_inc", "社融增量", "macro_monthly:tushare+akshare", ("credit",), sf_inc, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("sf_inc_yoy", "社融增量同比", "macro_monthly:derived", ("credit",), sf_inc_yoy, "D2派生；发布时间/vintage未验证"),
        FactorDefinition("sf_stock", "社融存量", "macro_monthly:tushare+akshare", ("credit",), sf_stock, "D2已计算；发布时间/vintage未验证"),
        FactorDefinition("sf_stock_yoy", "社融存量同比", "macro_monthly:derived", ("credit",), sf_stock_yoy, "D2派生；发布时间/vintage未验证"),
    ]
    for idx, asset in enumerate(ASSET_ORDER):
        label = ASSET_LABELS[asset]
        if asset == "equity":
            axes = ("growth", "credit", "liquidity", "confirmation")
        elif asset == "bond":
            axes = ("money", "interest_rate", "liquidity", "confirmation")
        elif asset == "gold":
            axes = ("inflation", "fx", "confirmation")
        else:
            axes = ("inflation", "growth", "fx", "confirmation")
        definitions.append(
            FactorDefinition(
                f"{asset}_log_nav",
                f"{label}价格确认",
                "asset_panel_v553:RQData/commodity_self_financing_D2",
                axes,
                _log_nav_from_returns(returns, idx),
                "D2已计算；四资产Wind/iFinD月度hash交叉验证未完成",
            )
        )
    return definitions


def _axis_targets(returns: np.ndarray) -> dict[str, np.ndarray]:
    r = np.asarray(returns, dtype=float)
    target = np.full((r.shape[0],), np.nan, dtype=float)
    def shifted(weights: Sequence[float]) -> np.ndarray:
        out = target.copy()
        w = np.asarray(weights, dtype=float)
        if r.shape[0] > 1:
            out[:-1] = r[1:] @ w
        return out
    return {
        "growth": shifted([0.45, -0.35, -0.15, 0.25]),
        "inflation": shifted([-0.20, -0.25, 0.35, 0.40]),
        "money": shifted([0.25, 0.35, 0.15, -0.30]),
        "credit": shifted([0.45, -0.25, -0.15, 0.25]),
        "interest_rate": shifted([-0.35, 0.45, 0.20, -0.20]),
        "fx": shifted([-0.25, -0.10, 0.50, 0.35]),
        "liquidity": shifted([0.35, 0.35, -0.20, -0.15]),
        "confirmation": shifted([0.30, 0.20, 0.20, 0.30]),
    }


def _build_factor_engine(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]]) -> FactorEngine:
    definitions = _base_definitions(months, returns, macro)
    targets = _axis_targets(returns)
    target_months = np.asarray([str(months[i + 1]) if i + 1 < len(months) else "" for i in range(len(months))])
    train_mask = np.asarray([m[:4] in TRAIN_SELECTION_YEARS for m in target_months], dtype=bool)
    feature_values: dict[str, np.ndarray] = {}
    candidate_meta: dict[str, tuple[FactorDefinition, str]] = {}
    for definition in definitions:
        for transform in TRANSFORMS:
            factor_id = f"{definition.base_id}::{transform}"
            feature_values[factor_id] = _feature_values(definition.series, transform)
            candidate_meta[factor_id] = (definition, transform)

    selected_by_axis: dict[str, tuple[SelectedFactor, ...]] = {}
    all_rows: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for axis in AXIS_ORDER:
        ranked: list[SelectedFactor] = []
        y = targets[axis]
        for factor_id, values in feature_values.items():
            definition, transform = candidate_meta[factor_id]
            if axis not in definition.axes:
                continue
            x = np.asarray(values, dtype=float)
            coverage = float(np.mean(np.isfinite(x[train_mask]))) if train_mask.any() else 0.0
            ic = _safe_corr(x[train_mask], y[train_mask])
            sign = 1.0 if ic >= 0.0 else -1.0
            hit = _safe_hit(x[train_mask], y[train_mask], sign)
            score = max(0.0, abs(ic)) * 0.65 + max(0.0, hit - 0.5) * 0.70 + coverage * 0.10
            ranked.append(
                SelectedFactor(
                    factor_id=factor_id,
                    base_label=definition.label,
                    transform=transform,
                    axis=axis,
                    sign=sign,
                    score=float(score),
                    train_ic=float(ic),
                    train_hit_rate=float(hit),
                    coverage=float(coverage),
                )
            )
        ranked.sort(key=lambda row: (row.score, abs(row.train_ic), row.train_hit_rate, row.factor_id), reverse=True)
        selected = tuple(row for row in ranked[:MAX_SELECTED_PER_AXIS] if row.coverage >= 0.55)
        selected_by_axis[axis] = selected
        selected_ids.update(row.factor_id for row in selected)

    axis_scores: dict[str, np.ndarray] = {}
    axis_confidence: dict[str, float] = {}
    for axis, selected in selected_by_axis.items():
        out = np.zeros(len(months), dtype=float)
        weight_sum = np.zeros(len(months), dtype=float)
        for row in selected:
            values = np.asarray(feature_values[row.factor_id], dtype=float)
            w = max(row.score, 0.05)
            mask = np.isfinite(values)
            out[mask] += w * row.sign * np.clip(values[mask], -3.0, 3.0)
            weight_sum[mask] += w
        valid = weight_sum > 1.0e-12
        out[valid] = out[valid] / weight_sum[valid]
        out[~valid] = 0.0
        axis_scores[axis] = np.clip(out, -3.0, 3.0)
        if selected:
            axis_confidence[axis] = float(np.clip(np.mean([max(abs(x.train_ic), x.train_hit_rate - 0.5) for x in selected]) * 1.5, 0.10, 0.85))
        else:
            axis_confidence[axis] = 0.0

    for factor_id, (definition, transform) in candidate_meta.items():
        axes = [axis for axis in definition.axes if any(row.factor_id == factor_id for row in selected_by_axis.get(axis, ()))]
        values = feature_values[factor_id]
        coverage_full = float(np.mean(np.isfinite(values)))
        all_rows.append(
            {
                "cycle": "美林/普林格/宏观因子实算候选库",
                "pillar": "、".join(AXIS_LABELS.get(axis, axis) for axis in definition.axes),
                "factor": definition.label,
                "factor_id": factor_id,
                "transform": transform,
                "source_priority": "Wind -> iFinD -> RQData；当前使用本地D2缓存实算并等待D3/PIT交叉验证",
                "current_data_status": definition.source_status,
                "pit_requirement": "provider_series_id + release_time + available_time + vintage/revision + query_hash + source_hash + cross_provider_hash",
                "frequency": "monthly",
                "processing": "滚动zscore/环比/多月差分/HP滤波/傅里叶低频/分位数/斜率；只用训练窗检验IC、方向命中、覆盖率",
                "enters_current_weight": "yes_research_D2_factor_selected" if axes else "no_not_selected_or_pending_d3",
                "selected_axes": "、".join(AXIS_LABELS.get(axis, axis) for axis in axes),
                "coverage_full": coverage_full,
                "production_admitted": False,
            }
        )

    selected_rows = [row for axis in AXIS_ORDER for row in selected_by_axis[axis]]
    unique_selected_ids = {row.factor_id for row in selected_rows}
    summary = {
        "schema_version": "asset-allocation-factor-engine/6.3",
        "candidate_factor_count": len(all_rows),
        "selected_factor_count": len(unique_selected_ids),
        "selected_axis_assignment_count": len(selected_rows),
        "selected_by_axis": {
            AXIS_LABELS.get(axis, axis): [
                {
                    "factor_id": row.factor_id,
                    "factor": row.base_label,
                    "transform": row.transform,
                    "sign": row.sign,
                    "score": row.score,
                    "train_ic": row.train_ic,
                    "train_hit_rate": row.train_hit_rate,
                    "coverage": row.coverage,
                }
                for row in selected_by_axis[axis]
            ]
            for axis in AXIS_ORDER
        },
        "selection_window": "target months 2018-2019 only; 2020-2021 validation; 2022+ report-only",
        "d3_pit_boundary": "all selected factors are real D2 calculations but production D3/PIT remains false until Wind/iFinD/RQ release-vintage evidence is stored",
    }
    summary["content_sha256"] = _hash(summary)
    return FactorEngine(
        months=tuple(str(m) for m in months),
        feature_values=feature_values,
        factor_rows=tuple(all_rows),
        selected_by_axis=selected_by_axis,
        axis_scores=axis_scores,
        axis_confidence=axis_confidence,
        summary=summary,
    )


def _engine_score(engine: FactorEngine, axis: str, idx: int) -> float:
    return _finite(engine.axis_scores.get(axis, np.zeros(len(engine.months)))[idx])


def _stage_to_scores(stage: str, kind: str) -> np.ndarray:
    if kind == "pring" and stage == "V_stagflation_profit_downturn":
        table = {"gold": 0.90, "commodity": 0.55, "bond": 0.10, "equity": -0.75}
        return np.asarray([float(table[asset]) for asset in ASSET_ORDER], dtype=float)
    return v61._stage_to_scores("V_profit_downturn" if stage == "V_stagflation_profit_downturn" else stage, kind)


def _cycle_state_v63(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], engine: FactorEngine, idx: int) -> CycleStateV63:
    base = v61._cycle_state(months, returns, macro, idx)
    factor_growth = _engine_score(engine, "growth", idx)
    factor_inflation = _engine_score(engine, "inflation", idx)
    factor_money = 0.55 * _engine_score(engine, "money", idx) + 0.45 * _engine_score(engine, "liquidity", idx)
    factor_credit = _engine_score(engine, "credit", idx)
    factor_interest = _engine_score(engine, "interest_rate", idx)
    factor_fx = _engine_score(engine, "fx", idx)
    factor_confirmation = _engine_score(engine, "confirmation", idx)

    growth_raw = float(0.55 * base.merrill_growth + 0.45 * factor_growth)
    inflation_raw = float(0.55 * base.merrill_inflation + 0.45 * factor_inflation)
    if growth_raw >= 0.0 and inflation_raw < 0.0:
        merrill_stage = "recovery"
    elif growth_raw >= 0.0 and inflation_raw >= 0.0:
        merrill_stage = "overheat"
    elif growth_raw < 0.0 and inflation_raw >= 0.0:
        merrill_stage = "stagflation"
    else:
        merrill_stage = "recession"
    merrill_conf = float(np.clip((abs(growth_raw) + abs(inflation_raw)) / 4.0 + 0.10 * engine.axis_confidence.get("growth", 0.0), 0.20, 0.95))
    merrill_scores = _stage_to_scores(merrill_stage, "merrill") * merrill_conf

    money_score = float(0.50 * base.pring_money + 0.35 * factor_money + 0.15 * factor_interest)
    credit_score = float(0.55 * base.pring_credit + 0.45 * factor_credit)
    pring_growth = float(0.55 * base.pring_growth + 0.45 * factor_growth)
    confirmation = float(0.55 * base.pring_confirmation + 0.45 * factor_confirmation)
    loose = money_score >= 0.0
    credit_up = credit_score >= 0.0
    growth_up = pring_growth >= 0.0
    if loose and credit_up and not growth_up:
        pring_stage = "I_credit_repair"
    elif loose and credit_up and growth_up:
        pring_stage = "II_profit_expansion"
    elif (not loose) and credit_up and growth_up:
        pring_stage = "III_prosperity"
    elif (not loose) and (not credit_up) and growth_up:
        pring_stage = "IV_credit_pressure"
    elif (not loose) and (not credit_up) and (not growth_up):
        pring_stage = "V_stagflation_profit_downturn"
    elif loose and (not credit_up) and (not growth_up):
        pring_stage = "VI_recession_repair"
    elif loose and (not credit_up) and growth_up:
        pring_stage = "I_credit_repair"
    else:
        pring_stage = "IV_credit_pressure"
    pring_conf = float(np.clip((abs(money_score) + abs(credit_score) + abs(pring_growth) + 0.5 * abs(confirmation)) / 5.0 + 0.05 * engine.axis_confidence.get("credit", 0.0), 0.20, 0.95))
    pring_scores = _stage_to_scores(pring_stage, "pring") * pring_conf

    macro_six_scores = {
        "growth": growth_raw,
        "inflation": inflation_raw,
        "interest_rate": float(0.60 * money_score + 0.40 * factor_interest),
        "credit": credit_score,
        "fx": factor_fx,
        "liquidity": float(0.60 * factor_money + 0.40 * base.macro_six_scores.get("liquidity", 0.0)),
    }
    combined = 0.50 * merrill_scores + 0.50 * pring_scores
    rank = [ASSET_ORDER[i] for i in np.argsort(-combined)]
    return CycleStateV63(
        month=str(months[idx]),
        merrill_growth=growth_raw,
        merrill_inflation=inflation_raw,
        merrill_stage=merrill_stage,
        merrill_confidence=merrill_conf,
        merrill_scores=merrill_scores,
        pring_money=money_score,
        pring_credit=credit_score,
        pring_growth=pring_growth,
        pring_confirmation=confirmation,
        pring_stage=pring_stage,
        pring_confidence=pring_conf,
        pring_scores=pring_scores,
        combined_scores=combined,
        combined_rank=rank,
        macro_six_scores=macro_six_scores,
        selected_axis_scores={
            "growth": factor_growth,
            "inflation": factor_inflation,
            "money": factor_money,
            "credit": factor_credit,
            "interest_rate": factor_interest,
            "fx": factor_fx,
            "liquidity": _engine_score(engine, "liquidity", idx),
            "confirmation": factor_confirmation,
        },
        base_cycle_state=v61._state_dict(base),
    )


def _weights_dict(weights: Sequence[float]) -> dict[str, float]:
    return {asset: float(weights[i]) for i, asset in enumerate(ASSET_ORDER)}


def _state_dict(state: CycleStateV63) -> dict[str, Any]:
    return {
        "merrill": {
            "growth": state.merrill_growth,
            "inflation": state.merrill_inflation,
            "stage": state.merrill_stage,
            "confidence": state.merrill_confidence,
            "asset_scores": _weights_dict(state.merrill_scores),
        },
        "pring": {
            "money": state.pring_money,
            "credit": state.pring_credit,
            "growth": state.pring_growth,
            "confirmation": state.pring_confirmation,
            "stage": state.pring_stage,
            "confidence": state.pring_confidence,
            "asset_scores": _weights_dict(state.pring_scores),
        },
        "combined": {"asset_scores": _weights_dict(state.combined_scores), "rank": list(state.combined_rank)},
        "macro_six_scores": dict(state.macro_six_scores),
        "selected_axis_scores": dict(state.selected_axis_scores),
        "base_cycle_state_v61": state.base_cycle_state,
    }


def _cycle_alpha(state: CycleStateV63, scale: float = 0.012) -> np.ndarray:
    centered = state.combined_scores - float(np.mean(state.combined_scores))
    denom = max(float(np.max(np.abs(centered))), 1.0e-8)
    return scale * centered / denom


def _macro_alpha(state: CycleStateV63, window: np.ndarray, scale: float = 0.010) -> np.ndarray:
    trend = 0.20 * v61._risk_adjusted(window, 3) + 0.30 * v61._risk_adjusted(window, 6) + 0.30 * v61._risk_adjusted(window, 12)
    trend = np.tanh(trend / 3.0)
    macro = state.macro_six_scores
    overlay = np.asarray(
        [
            0.35 * macro["growth"] + 0.20 * macro["liquidity"] + 0.20 * macro["credit"],
            -0.30 * macro["growth"] + 0.35 * macro["interest_rate"] + 0.15 * macro["liquidity"],
            0.35 * macro["inflation"] + 0.30 * macro["fx"] - 0.10 * macro["growth"],
            0.40 * macro["inflation"] + 0.20 * macro["growth"] - 0.15 * macro["interest_rate"] + 0.10 * macro["fx"],
        ],
        dtype=float,
    )
    risk_off = float(np.clip((macro["inflation"] - macro["growth"] - macro["liquidity"]) / 6.0, -0.35, 0.35))
    risk_control = np.asarray([-risk_off, risk_off, 0.5 * risk_off, 0.25 * risk_off], dtype=float)
    raw = 0.45 * trend + 0.45 * np.tanh(overlay / 2.0) + 0.10 * risk_control
    raw = raw - float(np.mean(raw))
    denom = max(float(np.max(np.abs(raw))), 1.0e-8)
    return scale * raw / denom


def _solve_bl_target(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], engine: FactorEngine, idx: int, previous: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    window = returns[idx - 35 : idx + 1]
    cov, cov_diag = estimate_statistical_covariance_v5(window, half_life=24, diagonal_shrinkage=0.35)
    state = _cycle_state_v63(months, returns, macro, engine, idx)
    prior = black_litterman_posterior_v5(cov, POLICY, delta=4.0, tau=0.05, views=None)
    p = np.asarray([[1.0, -1.0, 0.0, 0.0], [0.0, -1.0, 1.0, 0.0], [0.0, -1.0, 0.0, 1.0]], dtype=float)
    alpha = _cycle_alpha(state, scale=0.012)
    q = p @ prior.pi + p @ alpha
    conf = np.clip(np.mean([state.merrill_confidence, state.pring_confidence]), 0.20, 0.90)
    omega = np.diag(np.maximum(np.diag(p @ (0.05 * cov) @ p.T), 1.0e-8)) * float(1.40 - 0.55 * conf)

    @dataclass(frozen=True)
    class Views:
        P: np.ndarray
        q: np.ndarray
        omega: np.ndarray
        diagnostics: Mapping[str, Any]

    posterior = black_litterman_posterior_v5(
        cov,
        POLICY,
        delta=4.0,
        tau=0.05,
        views=Views(P=p, q=q, omega=omega, diagnostics={"cycle_confidence": float(conf)}),
    )
    solved = optimize_relative_v539(
        posterior.posterior_mean - posterior.pi,
        cov,
        posterior.posterior_mean_covariance,
        POLICY,
        previous,
        lower_bounds=[0.05, 0.05, 0.05, 0.05],
        upper_bounds=[0.70, 0.75, 0.55, 0.65],
        max_active_share=0.35,
        max_annual_tracking_error=0.10,
        max_one_way_turnover=0.12,
        linear_cost=LINEAR_COST,
        quadratic_cost=QUADRATIC_COST,
        active_risk_aversion=4.0,
        uncertainty_penalty=0.20,
        active_l2_penalty=0.02,
    )
    if solved.get("status") != "optimal":
        raise RuntimeError(f"v63_bl_optimizer_failed:{months[idx]}:{solved.get('status')}")
    return np.asarray(solved["weights"], dtype=float), {
        "cycle_state": _state_dict(state),
        "black_litterman": posterior.to_dict(),
        "optimizer": solved,
        "covariance_diagnostics": cov_diag,
        "view_matrix": p.tolist(),
        "view_q": q.tolist(),
        "view_omega": omega.tolist(),
        "cycle_alpha": alpha.tolist(),
        "factor_engine_selected_axes": engine.summary.get("selected_by_axis"),
    }


def _risk_parity_target(returns: np.ndarray, idx: int) -> tuple[np.ndarray, dict[str, Any]]:
    return v61._risk_parity_target(returns, idx)


def _macro_factor_target(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], engine: FactorEngine, idx: int, previous: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    window = returns[idx - 35 : idx + 1]
    cov, cov_diag = estimate_statistical_covariance_v5(window, half_life=18, diagonal_shrinkage=0.45)
    state = _cycle_state_v63(months, returns, macro, engine, idx)
    rp, rp_diag = _risk_parity_target(returns, idx)
    alpha = _cycle_alpha(state, scale=0.0055) + _macro_alpha(state, window, scale=0.0145)
    solved = optimize_relative_v539(
        alpha,
        cov,
        cov,
        rp,
        previous,
        lower_bounds=[0.05, 0.05, 0.05, 0.05],
        upper_bounds=[0.70, 0.75, 0.60, 0.65],
        max_active_share=0.45,
        max_annual_tracking_error=0.12,
        max_one_way_turnover=0.15,
        linear_cost=LINEAR_COST,
        quadratic_cost=QUADRATIC_COST,
        active_risk_aversion=3.0,
        uncertainty_penalty=0.0,
        active_l2_penalty=0.015,
    )
    if solved.get("status") != "optimal":
        raise RuntimeError(f"v63_macro_optimizer_failed:{months[idx]}:{solved.get('status')}")
    return np.asarray(solved["weights"], dtype=float), {
        "cycle_state": _state_dict(state),
        "macro_alpha": alpha.tolist(),
        "risk_parity_anchor": rp.tolist(),
        "risk_parity_anchor_diagnostics": rp_diag,
        "optimizer": solved,
        "covariance_diagnostics": cov_diag,
        "macro_six_scores": state.macro_six_scores,
        "factor_engine_selected_axes": engine.summary.get("selected_by_axis"),
    }


def _simulate(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], engine: FactorEngine, model: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous = POLICY.copy()
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for idx in range(35, len(returns) - 1):
        if model == "black_litterman":
            target, diag = _solve_bl_target(months, returns, macro, engine, idx, previous)
        elif model == "risk_parity":
            target, diag = _risk_parity_target(returns, idx)
        elif model == "macro_factor":
            target, diag = _macro_factor_target(months, returns, macro, engine, idx, previous)
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
        current, current_diag = _solve_bl_target(months, returns, macro, engine, latest_idx, previous)
    elif model == "risk_parity":
        current, current_diag = _risk_parity_target(returns, latest_idx)
    else:
        current, current_diag = _macro_factor_target(months, returns, macro, engine, latest_idx, previous)
    last["current_signal_month"] = str(months[latest_idx])
    last["current_weights"] = current.tolist()
    last["current_diagnostics"] = current_diag
    return rows, last


def _cycle_payload(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], engine: FactorEngine) -> dict[str, Any]:
    state = _cycle_state_v63(months, returns, macro, engine, len(returns) - 1)
    history_rows = []
    for idx in range(35, len(returns)):
        st = _cycle_state_v63(months, returns, macro, engine, idx)
        history_rows.append(
            {
                "month": str(months[idx]),
                "merrill_stage": st.merrill_stage,
                "merrill_growth": st.merrill_growth,
                "merrill_inflation": st.merrill_inflation,
                "pring_stage": st.pring_stage,
                "pring_money": st.pring_money,
                "pring_credit": st.pring_credit,
                "pring_growth": st.pring_growth,
                "combined_rank": [ASSET_LABELS[a] for a in st.combined_rank],
                "combined_scores": _weights_dict(st.combined_scores),
            }
        )
    return {
        "current_summary": "v6.3真实链路：美林用增长/通胀两轴，普林格用货币/信用/增长/市场确认四轴；轴分数来自训练窗筛选的D2实算因子，合成四资产排序后进入BL与宏观调控。",
        "cycles": [
            {
                "cycle": "美林时钟",
                "dimensions": ["增长", "通胀"],
                "current_stage": state.merrill_stage,
                "display_probability": state.merrill_confidence,
                "production_admitted": False,
                "research_admitted": True,
                "asset_bias": _weights_dict(state.merrill_scores),
                "processing": "中国增长/通胀多因子候选库；训练窗IC/命中率/覆盖率筛选；HP滤波、傅里叶低频、滚动zscore、分位数和斜率聚合。",
            },
            {
                "cycle": "普林格周期",
                "dimensions": ["货币", "信用", "增长", "市场确认"],
                "current_stage": state.pring_stage,
                "display_probability": state.pring_confidence,
                "production_admitted": False,
                "research_admitted": True,
                "asset_bias": _weights_dict(state.pring_scores),
                "processing": "货币->信用->增长兑现的六阶段模型；两个理论不稳定组合并入邻近阶段；第五阶段按滞涨/盈利下行处理。",
            },
        ],
        "combined_asset_ranking": [ASSET_LABELS[x] for x in state.combined_rank],
        "combined_scores": _weights_dict(state.combined_scores),
        "history": history_rows,
        "factor_rows": list(engine.factor_rows),
        "candidate_factor_count": int(engine.summary["candidate_factor_count"]),
        "selected_factor_count": int(engine.summary["selected_factor_count"]),
        "factor_engine": dict(engine.summary),
        "production_admitted_cycles": [],
        "research_admitted_cycles": ["美林时钟", "普林格周期"],
        "truth_boundary": "v6.3让D2实算因子真实进入研究权重；但宏观release/vintage与Wind/iFinD/RQ跨源hash未闭环，仍不得标生产D3。",
    }


def _scrub_runtime(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _scrub_runtime(v) for k, v in value.items() if str(k) not in {"solve_time_seconds", "wall_time_seconds"}}
    if isinstance(value, list):
        return [_scrub_runtime(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub_runtime(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value

def _selection_score(model: Mapping[str, Any]) -> float:
    metrics = model.get("metrics") or {}
    train = metrics.get("train") or {}
    validation = metrics.get("validation") or {}
    train_sharpe = _finite(train.get("sharpe"), -9.0)
    val_sharpe = _finite(validation.get("sharpe"), -9.0)
    train_excess = _finite(train.get("annual_excess_return"), -9.0)
    val_excess = _finite(validation.get("annual_excess_return"), -9.0)
    train_ir = _finite(train.get("information_ratio"), -9.0)
    val_ir = _finite(validation.get("information_ratio"), -9.0)
    gate_penalty = 0.0
    if train_excess <= 0.0 or val_excess <= 0.0:
        gate_penalty -= 5.0
    if train_ir <= 0.0 or val_ir <= 0.0:
        gate_penalty -= 2.0
    return gate_penalty + min(train_sharpe, val_sharpe) + 0.35 * val_sharpe + 8.0 * min(train_excess, val_excess) + 0.25 * min(train_ir, val_ir) - 0.20 * abs(train_sharpe - val_sharpe)


def build_snapshot() -> dict[str, Any]:
    panel = v61._read(v61.PANEL_PATH)
    v61._validate_panel(panel)
    months, returns = v61._select_returns(panel)
    macro = v61._load_macro()
    engine = _build_factor_engine(months, returns, macro)
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
            "美林+普林格筛选因子生成资产排序，再构造P/Q/Omega进入Black-Litterman后验并约束求解。",
            [
                "股票/债券/黄金/商品四资产等权25%作为BL先验和相对收益基准。",
                "美林增长-通胀两轴与普林格货币-信用-增长-市场确认四轴均由训练窗筛选因子实时计算。",
                "两周期50/50合成四资产强弱，转为股票-债券、黄金-债券、商品-债券三条相对观点。",
                "Omega按PτΣP'并随周期置信度收缩，避免低置信观点过度放大。",
                "在权重、主动偏离、TE、换手和成本约束下月频求解；测试期只报告。",
            ],
            "research-only; D2真实计算已入模，D3/PIT生产门仍未关闭",
        ),
        "risk_parity": v61._strategy_payload(
            "risk_parity",
            "四资产风险平价",
            rp_rows,
            equal_rows,
            rp_last["current_weights"],
            "四资产稳健协方差ERC，不读取周期观点，作为独立低波高夏普风险均衡模型。",
            [
                "36个月滚动收益窗口估计稳健协方差。",
                "EW半衰期、对角收缩、PSD修正。",
                "求解股票/债券/黄金/商品风险贡献接近均衡。",
                "按漂移持仓计算换手和同口径交易成本。",
            ],
            "independent risk model; no macro/cycle leakage",
        ),
        "macro_factor": v61._strategy_payload(
            "macro_factor",
            "宏观因子调整模型",
            mf_rows,
            equal_rows,
            mf_last["current_weights"],
            "增长/通胀/利率/信用/汇率/流动性六类筛选因子调节周期alpha与风险平价锚。",
            [
                "六大类宏观因子从同一候选库中训练窗筛选：增长、通胀、利率、信用、汇率、流动性。",
                "对可用D2因子做HP滤波、傅里叶低频、滚动zscore、方向命中与稳定性处理。",
                "以风险平价作为风险锚，以宏观六因子和两周期合成信号作为主动收益。",
                "使用约束优化控制波动、换手、成本和集中度。",
                "未完成D3/PIT的数据真实计算但只标研究准入，不标生产准入。",
            ],
            "research-only macro factor overlay; D3/PIT gate remains fail-closed",
        ),
    }
    for key, last in (("black_litterman", bl_last), ("risk_parity", rp_last), ("macro_factor", mf_last)):
        strategies[key]["current_diagnostics"] = copy.deepcopy(last.get("current_diagnostics") or {})
        strategies[key]["signal_month"] = last.get("current_signal_month")

    pretest_scores = {key: _selection_score(model) for key, model in strategies.items()}
    primary = max(pretest_scores, key=pretest_scores.get)
    full_excess = {k: float(v["metrics"]["full"].get("annual_excess_return") or -999.0) for k, v in strategies.items()}
    full_sharpe = {k: float(v["metrics"]["full"].get("sharpe") or -999.0) for k, v in strategies.items()}
    cycle_payload = _cycle_payload(months, returns, macro, engine)
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_V63,
        "engine_version": ENGINE_V63,
        "generated_at": "2026-08-16",
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
            "source_priority": "Wind优先，其次iFinD，再次RQData；当前v6.3使用v553 RQData/D2四资产面板和本地macro_monthly研究库真实计算。",
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
            "selection_rule": "只用训练期2018-2019和验证期2020-2021的Sharpe、超额、IR与稳定性；2022+只报告不选模。",
            "reason": "三模型中选择训练/验证综合稳健性最高且不读取报告期的模型；v6.3让筛选因子真实进入周期、BL与宏观调控。",
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
            "truth_boundary": "v6.3完成D2真实因子->周期->BL/宏观调控->回测闭环；D3/PIT仍需Wind/iFinD/RQ release-vintage与跨源hash后才可生产晋级。",
        },
    }
    snapshot = _scrub_runtime(snapshot)
    snapshot["content_sha256"] = _hash(snapshot)
    return snapshot


def write_snapshot(output: Path) -> dict[str, Any]:
    snapshot = build_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=_json_default), encoding="utf-8")
    temp.replace(output)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=_json_default), encoding="utf-8")
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
