"""Causal timing overlay for the CSI 500 enhanced-index optimizer.

The module deliberately uses only point-in-time fields that already exist in
the CSI 500 monthly optimizer panel:

* valuation and dividend fields for the left-side valuation-regression block;
* momentum, breadth, volume and money-flow fields for the right-side block;
* local monthly macro observations, lagged by one month, for dynamic macro;
* cross-sectional style/factor ranks for the alpha re-weighting overlay.

It does not write files, fetch external data or read sealed future returns for
parameter selection.  The output is an auditable map keyed by signal_date.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TimingOverlayConfig:
    """Pre-declared overlay controls, all causal and non-levered."""

    enabled: bool = False
    min_history_periods: int = 18
    valuation_lookback_periods: int = 84
    sentiment_lookback_periods: int = 36
    macro_lookback_months: int = 60
    left_weight: float = 0.45
    right_weight: float = 0.55
    alpha_overlay_enabled: bool = True
    max_alpha_overlay_weight: float = 0.30
    min_alpha_base_weight: float = 0.70
    risk_budget_floor: float = 0.40
    risk_budget_ceiling: float = 1.00
    min_tracking_error_multiplier: float = 1.00
    min_tracking_error_absolute: float = 0.06
    min_active_weight_multiplier: float = 1.00
    min_industry_multiplier: float = 1.00
    min_style_multiplier: float = 1.00
    min_turnover_multiplier: float = 1.00
    score_target_low_regime_multiplier: float = 0.65
    score_target_high_regime_multiplier: float = 1.15
    active_risk_low_regime_multiplier: float = 1.60
    active_risk_high_regime_multiplier: float = 0.90
    beta_momentum_tilt_enabled: bool = False
    right_beta_tilt_strength: float = 0.12
    right_momentum_tilt_strength: float = 0.09
    left_defensive_beta_tilt_strength: float = 0.12
    left_defensive_momentum_tilt_strength: float = 0.08
    max_timing_tilt_abs: float = 0.14
    risk_on_activation_threshold: float = 0.60
    risk_off_activation_threshold: float = 0.42


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clip01(value: Any, default: float = 0.5) -> float:
    number = _finite(value)
    if number is None:
        return default
    return max(0.0, min(1.0, number))


def _date_text(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) < 8:
        raise ValueError(f"invalid_date:{value!r}")
    return text[:8]


def _previous_month(signal_date: str) -> str:
    stamp = pd.Timestamp(_date_text(signal_date))
    prior = stamp - pd.offsets.MonthEnd(1)
    return prior.strftime("%Y%m")


def _percentile(series: Sequence[Any], value: Any, *, high_good: bool = True) -> float | None:
    current = _finite(value)
    if current is None:
        return None
    values = np.asarray([_finite(item) for item in series], dtype=object)
    clean = np.asarray([float(item) for item in values if item is not None], dtype=float)
    if clean.size < 3:
        return None
    pct = float((np.sum(clean <= current) - 0.5 * np.sum(clean == current)) / clean.size)
    pct = max(0.0, min(1.0, pct))
    return pct if high_good else 1.0 - pct


def _mean_present(values: Sequence[Any], default: float = 0.5) -> float:
    clean = [_finite(item) for item in values]
    finite = [float(item) for item in clean if item is not None]
    if not finite:
        return default
    return max(0.0, min(1.0, float(np.mean(finite))))


def _normalise_weights(values: pd.Series) -> pd.Series:
    raw = pd.to_numeric(values, errors="coerce").fillna(0.0).astype(float)
    if raw.sum() > 2.0:
        raw = raw / 100.0
    raw = raw.clip(lower=0.0)
    total = float(raw.sum())
    if total <= 0.0:
        return pd.Series(np.ones(len(raw)) / max(len(raw), 1), index=raw.index)
    return raw / total


def _weighted_mean(frame: pd.DataFrame, column: str, weights: pd.Series) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").astype(float)
    mask = values.notna() & np.isfinite(values)
    if not bool(mask.any()):
        return None
    aligned = weights.reindex(frame.index).fillna(0.0).astype(float)
    denom = float(aligned[mask].sum())
    if denom <= 0.0:
        return float(values[mask].mean())
    return float((values[mask] * aligned[mask]).sum() / denom)


def _weighted_positive_share(frame: pd.DataFrame, column: str, weights: pd.Series) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").astype(float)
    mask = values.notna() & np.isfinite(values)
    if not bool(mask.any()):
        return None
    aligned = weights.reindex(frame.index).fillna(0.0).astype(float)
    denom = float(aligned[mask].sum())
    if denom <= 0.0:
        return float(np.mean(values[mask] > 0.0))
    return float(aligned[mask & (values > 0.0)].sum() / denom)


def _weighted_earnings_yield(frame: pd.DataFrame, weights: pd.Series) -> float | None:
    if "pe_ttm" not in frame.columns:
        return None
    pe = pd.to_numeric(frame["pe_ttm"], errors="coerce").astype(float)
    mask = pe.notna() & np.isfinite(pe) & (pe > 0.0)
    if not bool(mask.any()):
        return None
    aligned = weights.reindex(frame.index).fillna(0.0).astype(float)
    denom = float(aligned[mask].sum())
    if denom <= 0.0:
        return float((1.0 / pe[mask]).mean())
    return float(((1.0 / pe[mask]) * aligned[mask]).sum() / denom)


def _macro_score(macro: pd.DataFrame | None, signal_date: str, config: TimingOverlayConfig) -> dict[str, Any]:
    if macro is None or macro.empty:
        return {
            "score": 0.5,
            "available": False,
            "reason": "macro_monthly_unavailable",
        }
    frame = macro.copy()
    if "month" not in frame.columns:
        return {
            "score": 0.5,
            "available": False,
            "reason": "macro_month_column_missing",
        }
    frame["month"] = frame["month"].astype(str).str.replace("-", "", regex=False).str[:6]
    frame = frame[frame["month"] <= _previous_month(signal_date)].sort_values("month")
    if frame.empty:
        return {
            "score": 0.5,
            "available": False,
            "reason": "macro_no_lagged_observation",
        }
    tail = frame.tail(int(config.macro_lookback_months)).copy()
    latest = tail.iloc[-1]

    pmi = _finite(latest.get("pmi_manufacturing"))
    pmi_delta = None
    if len(tail) >= 4 and pmi is not None:
        prev = _finite(tail.iloc[-4].get("pmi_manufacturing"))
        if prev is not None:
            pmi_delta = pmi - prev
    pmi_score = _mean_present([
        1.0 if pmi is not None and pmi >= 50.0 else 0.35 if pmi is not None and pmi >= 49.0 else 0.0,
        1.0 if pmi_delta is not None and pmi_delta > 0.0 else 0.0 if pmi_delta is not None else None,
    ])

    def yoy(column: str, periods: int = 12) -> float | None:
        if column not in tail.columns or len(tail) <= periods:
            return None
        now = _finite(tail.iloc[-1].get(column))
        old = _finite(tail.iloc[-periods - 1].get(column))
        if now is None or old is None or abs(old) <= 1.0e-12:
            return None
        return now / old - 1.0

    credit_values = []
    for column in ("sf_inc_month", "m2_yoy", "m1_yoy"):
        value = yoy(column) if column == "sf_inc_month" else _finite(latest.get(column))
        pct = _percentile(tail[column].tolist(), value, high_good=True) if column in tail else None
        credit_values.append(pct)
    credit_score = _mean_present(credit_values)

    cpi = _finite(latest.get("cpi_national_yoy"))
    ppi = _finite(latest.get("ppi_yoy"))
    ppi_cpi_gap = None if ppi is None or cpi is None else ppi - cpi
    inflation_score = _mean_present([
        _percentile(tail["cpi_national_yoy"].tolist(), cpi, high_good=False)
        if "cpi_national_yoy" in tail else None,
        _percentile(
            [
                (_finite(row.get("ppi_yoy")) or 0.0) - (_finite(row.get("cpi_national_yoy")) or 0.0)
                for _, row in tail.iterrows()
                if _finite(row.get("ppi_yoy")) is not None
                and _finite(row.get("cpi_national_yoy")) is not None
            ],
            ppi_cpi_gap,
            high_good=True,
        ),
    ])
    score = _mean_present([pmi_score, credit_score, inflation_score])
    return {
        "score": score,
        "available": True,
        "lagged_month": str(latest.get("month")),
        "pmi_score": pmi_score,
        "credit_score": credit_score,
        "inflation_score": inflation_score,
        "policy": "month<=signal_month_minus_one",
    }


def build_timing_overlay(
    panel: pd.DataFrame,
    *,
    macro_monthly: pd.DataFrame | None = None,
    config: TimingOverlayConfig | None = None,
) -> dict[str, Any]:
    """Build causal monthly timing scores keyed by signal_date."""

    config = config or TimingOverlayConfig()
    if not isinstance(panel, pd.DataFrame) or panel.empty:
        return {
            "status": "blocked",
            "reason": "panel_empty",
            "by_signal_date": {},
            "periods": [],
        }
    required = {"signal_date", "ts_code", "benchmark_weight"}
    missing = sorted(required - set(panel.columns))
    if missing:
        return {
            "status": "blocked",
            "reason": "missing_columns:" + ",".join(missing),
            "by_signal_date": {},
            "periods": [],
        }
    dates = sorted({_date_text(value) for value in panel["signal_date"].unique()})
    summary_rows: list[dict[str, Any]] = []
    raw_history: list[dict[str, Any]] = []
    for signal_date in dates:
        group = panel[panel["signal_date"].map(_date_text) == signal_date].copy()
        weights = _normalise_weights(group["benchmark_weight"])
        raw = {
            "signal_date": signal_date,
            "asset_count": int(group["ts_code"].nunique()),
            "earnings_yield": _weighted_earnings_yield(group, weights),
            "pb": _weighted_mean(group, "pb", weights),
            "dividend_yield": _weighted_mean(group, "dv_ttm", weights),
            "mom20": _weighted_mean(group, "mom20", weights),
            "mom60": _weighted_mean(group, "mom60", weights),
            "mom120": _weighted_mean(group, "mom120", weights),
            "mom252": _weighted_mean(group, "mom252", weights),
            "breadth20": _weighted_positive_share(group, "mom20", weights),
            "breadth60": _weighted_positive_share(group, "mom60", weights),
            "turnover_rate": _weighted_mean(group, "turnover_rate", weights),
            "volume_ratio": _weighted_mean(group, "volume_ratio", weights),
            "netflow_intensity": _weighted_mean(group, "netflow_intensity", weights),
            "large_order_balance": _weighted_mean(group, "large_order_balance", weights),
        }
        raw_history.append(raw)
        hist = pd.DataFrame(raw_history).tail(int(config.valuation_lookback_periods))
        sentiment_hist = pd.DataFrame(raw_history).tail(int(config.sentiment_lookback_periods))

        valuation_score = _mean_present([
            _percentile(hist["earnings_yield"].tolist(), raw["earnings_yield"], high_good=True),
            _percentile(hist["pb"].tolist(), raw["pb"], high_good=False),
            _percentile(hist["dividend_yield"].tolist(), raw["dividend_yield"], high_good=True),
        ])
        valuation_ready = len(hist) >= int(config.min_history_periods)

        trend_score = _mean_present([
            1.0 if (_finite(raw["mom20"]) or 0.0) > 0.0 else 0.0,
            1.0 if (_finite(raw["mom60"]) or 0.0) > 0.0 else 0.0,
            1.0 if (_finite(raw["mom120"]) or 0.0) > 0.0 else 0.0,
            _clip01(raw["breadth20"]),
            _clip01(raw["breadth60"]),
            _percentile(hist["mom252"].tolist(), raw["mom252"], high_good=True),
        ])

        sentiment_linear = _mean_present([
            _percentile(
                sentiment_hist["netflow_intensity"].tolist(),
                raw["netflow_intensity"],
                high_good=True,
            ),
            _percentile(
                sentiment_hist["large_order_balance"].tolist(),
                raw["large_order_balance"],
                high_good=True,
            ),
            _percentile(
                sentiment_hist["turnover_rate"].tolist(),
                raw["turnover_rate"],
                high_good=True,
            ),
            _percentile(
                sentiment_hist["volume_ratio"].tolist(),
                raw["volume_ratio"],
                high_good=True,
            ),
        ])
        if sentiment_linear <= 0.10:
            nonlinear_sentiment = 0.75
            sentiment_regime = "extreme_weak_reversal"
        elif sentiment_linear <= 0.30:
            nonlinear_sentiment = 0.55
            sentiment_regime = "weak_repair"
        elif sentiment_linear <= 0.80:
            nonlinear_sentiment = sentiment_linear
            sentiment_regime = "trend_following"
        elif sentiment_linear <= 0.975:
            nonlinear_sentiment = 0.60
            sentiment_regime = "hot_but_not_extreme"
        else:
            nonlinear_sentiment = 0.25
            sentiment_regime = "extreme_crowding_cooldown"

        macro = _macro_score(macro_monthly, signal_date, config)
        dynamic_macro = _clip01(macro.get("score"))
        left_score = _mean_present([valuation_score, dynamic_macro])
        right_score = _mean_present([trend_score, nonlinear_sentiment])
        composite = _clip01(
            float(config.left_weight) * left_score
            + float(config.right_weight) * right_score
        )
        if composite >= 0.75:
            timing_position = 1.0
        elif composite > 0.50:
            timing_position = 0.75
        elif abs(composite - 0.50) <= 1.0e-12:
            timing_position = 0.50
        elif composite > 0.25:
            timing_position = 0.25
        else:
            timing_position = 0.0
        budget = max(
            float(config.risk_budget_floor),
            min(float(config.risk_budget_ceiling), composite),
        )
        row = {
            "signal_date": signal_date,
            "asset_count": raw["asset_count"],
            "left_score": left_score,
            "right_score": right_score,
            "composite_score": composite,
            "timing_position": timing_position,
            "risk_budget_multiplier": budget,
            "active_side": "right" if right_score >= left_score else "left",
            "valuation_regression": {
                "score": valuation_score,
                "available": bool(valuation_ready),
                "earnings_yield": raw["earnings_yield"],
                "pb": raw["pb"],
                "dividend_yield": raw["dividend_yield"],
            },
            "price_volume_trend": {
                "score": trend_score,
                "mom20": raw["mom20"],
                "mom60": raw["mom60"],
                "mom120": raw["mom120"],
                "mom252": raw["mom252"],
                "breadth20": raw["breadth20"],
                "breadth60": raw["breadth60"],
            },
            "nonlinear_sentiment": {
                "score": nonlinear_sentiment,
                "linear_percentile": sentiment_linear,
                "regime": sentiment_regime,
                "netflow_intensity": raw["netflow_intensity"],
                "large_order_balance": raw["large_order_balance"],
                "turnover_rate": raw["turnover_rate"],
                "volume_ratio": raw["volume_ratio"],
            },
            "dynamic_macro": macro,
            "policy": {
                "position_mapping": ">=0.75:100%;>0.50:75%;=0.50:50%;>0.25:25%;else:0%",
                "report_lineage": "left_valuation_regression+dynamic_macro;right_price_volume_trend+nonlinear_sentiment",
                "future_returns_used": False,
            },
        }
        summary_rows.append(row)
    return {
        "status": "ready",
        "config": asdict(config),
        "periods": summary_rows,
        "by_signal_date": {row["signal_date"]: row for row in summary_rows},
        "audit": {
            "source": "csi500_monthly_optimizer_panel+macro_monthly_lagged",
            "signal_count": len(summary_rows),
            "min_signal_date": summary_rows[0]["signal_date"] if summary_rows else None,
            "max_signal_date": summary_rows[-1]["signal_date"] if summary_rows else None,
            "selection_uses_test_metrics": False,
            "future_returns_used": False,
        },
    }


def _unit_rank(series: pd.Series, *, high_good: bool = True) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").astype(float)
    ranked = values.rank(method="average", pct=True)
    if not high_good:
        ranked = 1.0 - ranked
    return ranked.fillna(0.5).clip(0.0, 1.0)


def _rank_average(frame: pd.DataFrame, columns: Sequence[str], *, high_good: bool = True) -> pd.Series:
    available = [column for column in columns if column in frame.columns]
    if not available:
        return pd.Series(np.full(len(frame), 0.5), index=frame.index)
    ranked = [_unit_rank(frame[column], high_good=high_good) for column in available]
    return pd.concat(ranked, axis=1).mean(axis=1).fillna(0.5).clip(0.0, 1.0)


def _neutralize_unit_score(
    frame: pd.DataFrame,
    score: pd.Series,
    style_columns: Sequence[str],
) -> tuple[pd.Series, dict[str, Any]]:
    weights = _normalise_weights(frame["benchmark_weight"])
    y = pd.to_numeric(score, errors="coerce").fillna(0.5).astype(float).to_numpy()
    blocks = [np.ones((len(frame), 1), dtype=float)]
    factor_names = ["intercept"]
    if "industry" in frame.columns:
        dummies = pd.get_dummies(frame["industry"].astype(str), dtype=float)
        if len(dummies.columns) > 1:
            blocks.append(dummies.iloc[:, 1:].to_numpy(dtype=float))
            factor_names.extend([f"industry:{name}" for name in dummies.columns[1:]])
    for column in style_columns:
        if column not in frame.columns:
            continue
        raw = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).astype(float)
        median = float(raw.median())
        mad = float(np.median(np.abs(raw - median)))
        scale = 1.4826 * mad if mad > 1.0e-8 else 1.0
        blocks.append(((raw - median) / scale).clip(-5.0, 5.0).to_numpy()[:, None])
        factor_names.append(str(column))
    x = np.column_stack(blocks)
    sqrt_w = np.sqrt(weights.to_numpy(dtype=float))[:, None]
    xw = x * sqrt_w
    yw = y * sqrt_w[:, 0]
    try:
        beta = np.linalg.lstsq(xw, yw, rcond=None)[0]
        residual = y - x @ beta
    except np.linalg.LinAlgError:
        residual = y - float(weights @ pd.Series(y, index=weights.index))
    centered = residual - float(weights.to_numpy(dtype=float) @ residual)
    dispersion = math.sqrt(float(weights.to_numpy(dtype=float) @ np.square(centered)))
    if dispersion <= 1.0e-12:
        centered = y - float(weights.to_numpy(dtype=float) @ y)
        dispersion = math.sqrt(float(weights.to_numpy(dtype=float) @ np.square(centered)))
    if dispersion <= 1.0e-12:
        output = pd.Series(np.zeros(len(frame)), index=frame.index)
    else:
        output = pd.Series(centered / dispersion, index=frame.index)
    audit = {
        "neutralization": "benchmark_weighted_industry_style_residual",
        "factor_count": len(factor_names),
        "style_columns": list(style_columns),
        "weighted_mean_after": float(weights.to_numpy(dtype=float) @ output.to_numpy(dtype=float)),
    }
    return output, audit


def apply_alpha_overlay(
    frame: pd.DataFrame,
    *,
    timing_row: Mapping[str, Any] | None,
    style_columns: Sequence[str],
    config: TimingOverlayConfig | None = None,
) -> tuple[pd.Series, dict[str, Any]]:
    """Return an adjusted alpha score and audit payload."""

    config = config or TimingOverlayConfig()
    original = pd.to_numeric(frame["alpha_score"], errors="coerce").astype(float)
    if (
        not config.enabled
        or timing_row is None
        or original.isna().any()
    ):
        return original, {
            "enabled": False,
            "reason": "disabled_or_unavailable",
            "future_returns_used": False,
        }
    left = _clip01(timing_row.get("left_score"))
    right = _clip01(timing_row.get("right_score"))
    composite = _clip01(timing_row.get("composite_score"))
    active_side = "right" if right >= left else "left"

    left_score = _rank_average(
        frame,
        (
            "quality_value_low_crowding_v8",
            "fundamental_quality_v4",
            "value_v4",
            "dividend_lowvol_quality",
            "low_crowding_v4",
        ),
        high_good=True,
    )
    right_score = _rank_average(
        frame,
        (
            "trend_quality_v4",
            "moneyflow_momentum_20",
            "kline_ai_pattern_score",
            "kline_score",
            "mom20",
            "mom60",
            "mom120",
        ),
        high_good=True,
    )
    if bool(config.alpha_overlay_enabled):
        overlay_unit = right_score if active_side == "right" else left_score
        original_unit = _unit_rank(original, high_good=True)
        confidence = abs(right - left) + max(0.0, composite - 0.5)
        overlay_weight = min(float(config.max_alpha_overlay_weight), max(0.0, 0.20 * confidence + 0.10))
        overlay_weight = min(overlay_weight, 1.0 - float(config.min_alpha_base_weight))
        blended_unit = (1.0 - overlay_weight) * original_unit + overlay_weight * overlay_unit
        adjusted, neutral_audit = _neutralize_unit_score(frame, blended_unit, style_columns)
    else:
        overlay_weight = 0.0
        adjusted = original.copy()
        neutral_audit = {
            "neutralization": "not_applied",
            "reason": "alpha_overlay_disabled_preserve_precomputed_score",
            "style_columns": list(style_columns),
            "future_returns_used": False,
        }
    tilt_audit: dict[str, Any] = {
        "enabled": False,
        "reason": "disabled_or_columns_unavailable",
        "future_returns_used": False,
    }
    if bool(config.beta_momentum_tilt_enabled):
        beta_column = "style_beta"
        momentum_candidates = (
            "style_momentum",
            "trend_quality_v4",
            "mom60",
            "mom120",
            "moneyflow_momentum_20",
        )
        momentum_column = next(
            (column for column in momentum_candidates if column in frame.columns),
            None,
        )
        if beta_column in frame.columns or momentum_column is not None:
            beta_rank = (
                _unit_rank(frame[beta_column], high_good=True) - 0.5
                if beta_column in frame.columns
                else pd.Series(np.zeros(len(frame)), index=frame.index)
            )
            momentum_rank = (
                _unit_rank(frame[momentum_column], high_good=True) - 0.5
                if momentum_column is not None
                else pd.Series(np.zeros(len(frame)), index=frame.index)
            )
            price_volume_trend = timing_row.get("price_volume_trend")
            if not isinstance(price_volume_trend, Mapping):
                price_volume_trend = {}
            trend_confirmation = _clip01(
                price_volume_trend.get("score"), default=right
            )
            right_edge = max(0.0, right - left)
            risk_on_signal = min(right, composite, trend_confirmation)
            risk_on_threshold = float(config.risk_on_activation_threshold)
            if right_edge <= 0.02:
                risk_on = 0.0
            else:
                risk_on = max(0.0, risk_on_signal - risk_on_threshold)
                risk_on /= max(1.0e-12, 1.0 - risk_on_threshold)
                risk_on *= 1.0 + right_edge
            risk_off_threshold = float(config.risk_off_activation_threshold)
            risk_off = max(0.0, risk_off_threshold - composite)
            risk_off /= max(1.0e-12, risk_off_threshold)
            risk_off *= 1.0 + max(0.0, left - right)
            beta_tilt = (
                float(config.right_beta_tilt_strength) * risk_on
                - float(config.left_defensive_beta_tilt_strength) * risk_off
            )
            momentum_tilt = (
                float(config.right_momentum_tilt_strength) * risk_on
                - float(config.left_defensive_momentum_tilt_strength) * risk_off
            )
            tilt_unit = beta_tilt * beta_rank + momentum_tilt * momentum_rank
            tilt_unit = tilt_unit.clip(
                -float(config.max_timing_tilt_abs),
                float(config.max_timing_tilt_abs),
            )
            if float(tilt_unit.abs().max()) > 1.0e-12:
                adjusted = adjusted + tilt_unit.astype(float)
                weights = _normalise_weights(frame["benchmark_weight"])
                adjusted = adjusted - float(weights @ adjusted)
                tilt_audit = {
                    "enabled": True,
                    "mode": "right_beta_momentum_when_trend_confirmed_left_defensive_when_risk_off",
                    "active_side": active_side,
                    "risk_on_intensity": float(risk_on),
                    "risk_off_intensity": float(risk_off),
                    "risk_on_signal": float(risk_on_signal),
                    "trend_confirmation": float(trend_confirmation),
                    "right_minus_left": float(right - left),
                    "beta_column": beta_column if beta_column in frame.columns else None,
                    "momentum_column": momentum_column,
                    "beta_tilt_coefficient": float(beta_tilt),
                    "momentum_tilt_coefficient": float(momentum_tilt),
                    "max_abs_tilt": float(tilt_unit.abs().max()),
                    "benchmark_weighted_mean_after": float(weights @ adjusted),
                    "future_returns_used": False,
                }
    return adjusted, {
        "enabled": True,
        "active_side": active_side,
        "left_score": left,
        "right_score": right,
        "composite_score": composite,
        "overlay_weight": float(overlay_weight),
        "base_alpha_weight": float(1.0 - overlay_weight),
        "left_columns": [
            "quality_value_low_crowding_v8",
            "fundamental_quality_v4",
            "value_v4",
            "dividend_lowvol_quality",
            "low_crowding_v4",
        ],
        "right_columns": [
            "trend_quality_v4",
            "moneyflow_momentum_20",
            "kline_ai_pattern_score",
            "kline_score",
            "mom20",
            "mom60",
            "mom120",
        ],
        "neutralization": neutral_audit,
        "beta_momentum_tilt": tilt_audit,
        "future_returns_used": False,
    }


def apply_timing_budget_to_optimizer_config(
    optimizer_config: Any,
    *,
    timing_row: Mapping[str, Any] | None,
    config: TimingOverlayConfig | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Tighten risk budget according to timing score without relaxing user caps."""

    config = config or TimingOverlayConfig()
    if not config.enabled or timing_row is None:
        return optimizer_config, {
            "enabled": False,
            "reason": "disabled_or_unavailable",
            "future_returns_used": False,
        }
    budget = _clip01(timing_row.get("risk_budget_multiplier"), default=1.0)
    budget = max(float(config.risk_budget_floor), min(float(config.risk_budget_ceiling), budget))
    te_multiplier = max(float(config.min_tracking_error_multiplier), budget)
    active_multiplier = max(float(config.min_active_weight_multiplier), budget)
    industry_multiplier = max(float(config.min_industry_multiplier), budget)
    style_multiplier = max(float(config.min_style_multiplier), budget)
    turnover_multiplier = max(float(config.min_turnover_multiplier), budget)

    target_tracking_error = min(
        float(optimizer_config.target_tracking_error),
        max(
            float(config.min_tracking_error_absolute),
            float(optimizer_config.target_tracking_error) * te_multiplier,
        ),
    )
    max_active_weight = float(optimizer_config.max_active_weight) * active_multiplier
    industry_deviation = optimizer_config.industry_deviation
    if isinstance(industry_deviation, Mapping):
        industry_deviation = {
            str(key): float(value) * industry_multiplier
            for key, value in industry_deviation.items()
        }
    else:
        industry_deviation = float(industry_deviation) * industry_multiplier

    def scale_bounds(bounds: Mapping[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for key, value in bounds.items():
            low, high = float(value[0]), float(value[1])
            out[str(key)] = (low * style_multiplier, high * style_multiplier)
        return out

    default_style_bounds = (
        float(optimizer_config.default_style_bounds[0]) * style_multiplier,
        float(optimizer_config.default_style_bounds[1]) * style_multiplier,
    )
    score_multiplier = (
        float(config.score_target_low_regime_multiplier)
        + (
            float(config.score_target_high_regime_multiplier)
            - float(config.score_target_low_regime_multiplier)
        )
        * budget
    )
    active_risk_multiplier = (
        float(config.active_risk_low_regime_multiplier)
        + (
            float(config.active_risk_high_regime_multiplier)
            - float(config.active_risk_low_regime_multiplier)
        )
        * budget
    )
    updated = replace(
        optimizer_config,
        target_tracking_error=target_tracking_error,
        max_active_weight=max_active_weight,
        industry_deviation=industry_deviation,
        style_bounds=scale_bounds(dict(optimizer_config.style_bounds)),
        default_style_bounds=default_style_bounds,
        one_way_turnover_limit=float(optimizer_config.one_way_turnover_limit) * turnover_multiplier,
        score_target_penalty=float(optimizer_config.score_target_penalty) * score_multiplier,
        active_risk_penalty=float(optimizer_config.active_risk_penalty) * active_risk_multiplier,
    )
    return updated, {
        "enabled": True,
        "budget": budget,
        "target_tracking_error": {
            "base": float(optimizer_config.target_tracking_error),
            "applied": float(updated.target_tracking_error),
        },
        "max_active_weight": {
            "base": float(optimizer_config.max_active_weight),
            "applied": float(updated.max_active_weight),
        },
        "industry_multiplier": industry_multiplier,
        "style_multiplier": style_multiplier,
        "turnover_multiplier": turnover_multiplier,
        "score_target_multiplier": score_multiplier,
        "active_risk_multiplier": active_risk_multiplier,
        "hard_caps_relaxed": False,
        "future_returns_used": False,
    }


__all__ = [
    "TimingOverlayConfig",
    "apply_alpha_overlay",
    "apply_timing_budget_to_optimizer_config",
    "build_timing_overlay",
]
