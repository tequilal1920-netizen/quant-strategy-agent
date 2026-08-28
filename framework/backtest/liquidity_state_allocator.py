"""Causal liquidity-state research allocator.

The engine is deliberately independent from the production liquidity snapshot.
It consumes only exact series from ``database/liquidity_tracking.sqlite3`` and
never writes to that database.  Every feature is timestamped by an explicit
availability lag, standardised with trailing information, and applied to the
following week's return.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, exp, isfinite, log, sqrt
from typing import Any, Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    group: str
    kind: str
    release_lag_business_days: int
    label: str


@dataclass(frozen=True)
class AllocatorConfig:
    name: str
    label: str
    feature_mode: str = "balanced"
    target_horizon_weeks: int = 4
    exposure_floor: float = 0.10
    exposure_ceiling: float = 1.00
    exposure_bias: float = 0.65
    exposure_slope: float = 0.90
    target_volatility: float = 0.18
    cost_bps: float = 10.0
    weighting_mode: str = "rolling_posterior"
    lookback_weeks: int = 260
    minimum_history_weeks: int = 156
    refit_weeks: int = 13
    market_residualization: bool = False
    crowding_penalty: float = 0.0
    crowding_center: float = 1.0
    target_objective: str = "forward_return"
    rebalance_frequency: str = "weekly"
    defensive_asset: str = "zero_cash"


SERIES_SPECS: tuple[SeriesSpec, ...] = (
    SeriesSpec("retail.small_net", "散户", "flow", 1, "散户小单净买入"),
    SeriesSpec("etf.flow_total", "ETF", "flow", 1, "ETF总资金流"),
    SeriesSpec("etf.flow_other", "ETF", "flow", 1, "其他ETF资金流"),
    SeriesSpec("etf.net_share_all", "ETF", "flow", 1, "ETF净申购份额"),
    SeriesSpec("margin.net_buy", "杠杆", "flow", 1, "融资净买入"),
    SeriesSpec("margin.activity", "杠杆", "level", 1, "融资活跃度"),
    SeriesSpec("margin.balance", "杠杆", "level", 1, "融资余额"),
    SeriesSpec("margin.guarantee_ratio", "杠杆", "level", 15, "平均担保比例"),
    SeriesSpec("margin.collateral_cash", "杠杆", "level", 15, "担保物现金"),
    SeriesSpec("margin.collateral_securities", "杠杆", "level", 15, "担保物证券"),
    SeriesSpec("public.new_equity_shares", "公募", "flow", 1, "新成立偏股基金份额"),
    SeriesSpec("public.filings_stock", "公募", "flow", 1, "股票型基金报会"),
    SeriesSpec("public.filings_mixed", "公募", "flow", 1, "混合型基金报会"),
    SeriesSpec("public.position_stock", "公募", "level", 1, "普通股票型基金仓位"),
    SeriesSpec("public.position_mixed", "公募", "level", 1, "偏股混合型基金仓位"),
    SeriesSpec("public.liquidation_count", "公募", "flow", 1, "基金清算数量"),
    SeriesSpec("public.liquidation_scale", "公募", "flow", 1, "基金清算规模"),
    SeriesSpec("primary.ipo_amount", "一级供给", "flow", 1, "IPO募集资金"),
    SeriesSpec("primary.seo_amount", "一级供给", "flow", 1, "定增募集资金"),
    SeriesSpec("primary.cb_amount", "一级供给", "flow", 1, "可转债募集资金"),
    SeriesSpec("private.stock_long_position", "私募", "level", 15, "私募股票多头仓位"),
    SeriesSpec("foreign.northbound_turnover", "外资", "level", 1, "陆股通成交"),
)


EXCLUDED_CONTRACT_SERIES: tuple[str, ...] = (
    "retail.new_accounts",
    "retail.participating_investors",
    "foreign.flow_total",
    "foreign.flow_active",
    "foreign.flow_passive",
    "foreign.cumulative_a",
    "foreign.cumulative_h",
    "foreign.position_asia_ex_japan",
    "foreign.position_em_active",
    "foreign.position_global_passive",
)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _robust_z(series: pd.Series, lookback: int = 104, min_periods: int = 26) -> pd.Series:
    history = series.shift(1)
    median = history.rolling(lookback, min_periods=min_periods).median()
    mad = history.rolling(lookback, min_periods=min_periods).apply(
        lambda values: float(np.median(np.abs(values - np.median(values)))),
        raw=True,
    )
    scale = 1.4826 * mad
    fallback = history.rolling(lookback, min_periods=min_periods).std(ddof=1)
    scale = scale.where(scale > 1.0e-12, fallback)
    zscore = (series - median) / scale.replace(0.0, np.nan)
    return zscore.clip(-4.0, 4.0)


def _weekly_series(raw: pd.DataFrame, spec: SeriesSpec) -> pd.Series:
    frame = raw.loc[raw["series_id"] == spec.series_id, ["observation_date", "value"]].copy()
    if frame.empty:
        return pd.Series(dtype=float, name=spec.series_id)
    frame["available_date"] = pd.to_datetime(frame["observation_date"]) + pd.offsets.BDay(
        spec.release_lag_business_days
    )
    values = frame.set_index("available_date")["value"].astype(float).sort_index()
    if spec.kind == "flow":
        weekly = values.resample("W-FRI").sum(min_count=1)
    else:
        weekly = values.resample("W-FRI").last()
    weekly.name = spec.series_id
    return weekly


def build_causal_feature_panel(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    feature_columns: dict[str, pd.Series] = {}
    registry: list[dict[str, Any]] = []
    for spec in SERIES_SPECS:
        weekly = _weekly_series(raw, spec)
        if weekly.empty:
            continue
        if spec.kind == "flow":
            short = weekly.ewm(span=4, adjust=False, min_periods=4).mean()
            medium = weekly.rolling(13, min_periods=7).sum()
        else:
            short = weekly.diff(4)
            medium = weekly.diff(13)
        candidates = {
            f"{spec.series_id}::短期": _robust_z(short),
            f"{spec.series_id}::中期": _robust_z(medium),
        }
        for name, values in candidates.items():
            feature_columns[name] = values
            registry.append(
                {
                    **asdict(spec),
                    "feature": name,
                    "horizon": "短期" if name.endswith("短期") else "中期",
                    "first_available": (
                        values.dropna().index.min().strftime("%Y-%m-%d")
                        if not values.dropna().empty
                        else None
                    ),
                    "last_available": (
                        values.dropna().index.max().strftime("%Y-%m-%d")
                        if not values.dropna().empty
                        else None
                    ),
                    "observations": int(values.notna().sum()),
                }
            )
    panel = pd.DataFrame(feature_columns).sort_index()
    return panel, registry

def residualize_market_response(
    features: pd.DataFrame,
    weekly_returns: pd.Series,
    lookback: int = 104,
    min_periods: int = 52,
) -> pd.DataFrame:
    """Remove the causal component explained by already-observed market returns."""
    market_impulse = _robust_z(weekly_returns.rolling(4, min_periods=2).sum())
    market_history = market_impulse.shift(1)
    denominator = market_history.rolling(lookback, min_periods=min_periods).var()
    output: dict[str, pd.Series] = {}
    for column in features.columns:
        feature_history = features[column].shift(1)
        covariance = feature_history.rolling(lookback, min_periods=min_periods).cov(market_history)
        beta = covariance / denominator.replace(0.0, np.nan)
        output[column] = features[column] - beta * market_impulse
    return pd.DataFrame(output, index=features.index)



def forward_compound_return(weekly_returns: pd.Series, horizon: int) -> pd.Series:
    gross = (1.0 + weekly_returns).rolling(horizon, min_periods=horizon).apply(np.prod, raw=True)
    return gross.shift(-horizon) - 1.0

def forward_downside_safety(weekly_returns: pd.Series, horizon: int) -> pd.Series:
    """Future path minimum; larger values denote a safer equity path."""
    values = weekly_returns.to_numpy(dtype=float)
    output = np.full(len(values), np.nan, dtype=float)
    for position in range(len(values)):
        future = values[position + 1 : position + horizon + 1]
        if len(future) < horizon or np.isnan(future).any():
            continue
        cumulative = np.cumprod(1.0 + future) - 1.0
        output[position] = float(np.min(cumulative))
    return pd.Series(output, index=weekly_returns.index, name="downside_safety")



def _spearman(feature: pd.Series, target: pd.Series) -> tuple[float, int]:
    paired = pd.concat([feature, target], axis=1).dropna()
    if len(paired) < 16 or paired.iloc[:, 0].nunique() < 3 or paired.iloc[:, 1].nunique() < 3:
        return 0.0, len(paired)
    return _safe_number(paired.iloc[:, 0].corr(paired.iloc[:, 1], method="spearman")), len(paired)


def _posterior_weight(
    feature: pd.Series,
    target: pd.Series,
    fit_mask: pd.Series,
    folds: int = 4,
) -> dict[str, Any]:
    paired = pd.concat([feature.rename("feature"), target.rename("target")], axis=1)
    paired = paired.loc[fit_mask.reindex(paired.index, fill_value=False)].dropna()
    if len(paired) < 64:
        return {
            "weight": 0.0,
            "observations": len(paired),
            "correlations": [],
            "posterior_direction_confidence": 0.5,
        }
    fold_rows = [row for row in np.array_split(paired, folds) if len(row) >= 12]
    correlations = [_spearman(row["feature"], row["target"])[0] for row in fold_rows]
    center = float(np.median(correlations)) if correlations else 0.0
    if abs(center) < 1.0e-12:
        direction = 0.0
    else:
        direction = 1.0 if center > 0.0 else -1.0
    agreeing = sum(1 for value in correlations if value * direction > 0.0)
    posterior = (agreeing + 1.0) / (len(correlations) + 2.0) if correlations else 0.5
    reliability = max(0.0, 2.0 * posterior - 1.0)
    magnitude = float(np.median(np.abs(correlations))) if correlations else 0.0
    coverage = len(paired) / (len(paired) + 52.0)
    weight = direction * magnitude * reliability * coverage
    return {
        "weight": float(weight),
        "observations": len(paired),
        "correlations": [float(value) for value in correlations],
        "median_correlation": center,
        "posterior_direction_confidence": float(posterior),
        "coverage_shrinkage": float(coverage),
    }


def _feature_subset(panel: pd.DataFrame, mode: str) -> list[str]:
    if mode == "fast":
        return [column for column in panel if column.endswith("短期")]
    if mode == "slow":
        return [column for column in panel if column.endswith("中期")]
    return list(panel.columns)


def fit_hierarchical_evidence_model(
    features: pd.DataFrame,
    registry: list[dict[str, Any]],
    target: pd.Series,
    fit_mask: pd.Series,
    feature_mode: str,
) -> dict[str, Any]:
    selected = _feature_subset(features, feature_mode)
    registry_by_feature = {row["feature"]: row for row in registry}
    feature_diagnostics: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[str]] = {}
    for feature in selected:
        diagnostic = _posterior_weight(features[feature], target, fit_mask)
        feature_diagnostics[feature] = diagnostic
        group = str(registry_by_feature[feature]["group"])
        grouped.setdefault(group, []).append(feature)

    group_scores: dict[str, pd.Series] = {}
    group_members: dict[str, list[dict[str, Any]]] = {}
    for group, columns in grouped.items():
        weights = np.array([feature_diagnostics[column]["weight"] for column in columns], dtype=float)
        denominator = float(np.abs(weights).sum())
        if denominator <= 1.0e-12:
            group_scores[group] = pd.Series(0.0, index=features.index)
        else:
            group_scores[group] = (
                features[columns].fillna(0.0).mul(weights, axis=1).sum(axis=1) / denominator
            )
        group_members[group] = [
            {
                "feature": column,
                "label": registry_by_feature[column]["label"],
                "horizon": registry_by_feature[column]["horizon"],
                **feature_diagnostics[column],
            }
            for column in columns
        ]

    groups = pd.DataFrame(group_scores, index=features.index)
    group_diagnostics = {
        group: _posterior_weight(groups[group], target, fit_mask)
        for group in groups.columns
    }
    group_weights = np.array(
        [group_diagnostics[group]["weight"] for group in groups.columns], dtype=float
    )
    denominator = float(np.abs(group_weights).sum())
    if denominator <= 1.0e-12:
        composite = pd.Series(0.0, index=groups.index, name="资金状态")
    else:
        composite = groups.fillna(0.0).mul(group_weights, axis=1).sum(axis=1) / denominator

    fit_values = composite.loc[fit_mask.reindex(composite.index, fill_value=False)].dropna()
    center = float(fit_values.median()) if not fit_values.empty else 0.0
    mad = float(np.median(np.abs(fit_values - center))) if not fit_values.empty else 0.0
    scale = 1.4826 * mad
    if scale <= 1.0e-12:
        scale = float(fit_values.std(ddof=1)) if len(fit_values) > 1 else 1.0
    calibrated = ((composite - center) / max(scale, 1.0e-12)).clip(-4.0, 4.0)
    return {
        "signal": calibrated,
        "group_scores": groups,
        "feature_diagnostics": feature_diagnostics,
        "group_diagnostics": group_diagnostics,
        "group_members": group_members,
        "calibration": {"center": center, "scale": scale},
    }


def walkforward_hierarchical_evidence_model(
    features: pd.DataFrame,
    registry: list[dict[str, Any]],
    target: pd.Series,
    target_horizon_weeks: int,
    feature_mode: str,
    lookback_weeks: int = 260,
    minimum_history_weeks: int = 156,
    refit_weeks: int = 13,
) -> dict[str, Any]:
    """Refit evidence weights using only labels matured before each decision date."""

    index = features.index
    signal = pd.Series(np.nan, index=index, name="liquidity_state")
    groups = sorted({str(row["group"]) for row in registry})
    group_scores = pd.DataFrame(np.nan, index=index, columns=groups)
    history: list[dict[str, Any]] = []
    latest: dict[str, Any] | None = None
    first_refit = minimum_history_weeks + target_horizon_weeks
    for start_position in range(first_refit, len(index), refit_weeks):
        refit_date = index[start_position]
        matured_position = start_position - target_horizon_weeks
        fit_start = max(0, matured_position - lookback_weeks + 1)
        fit_mask = pd.Series(False, index=index)
        fit_mask.iloc[fit_start : matured_position + 1] = True
        fitted = fit_hierarchical_evidence_model(
            features,
            registry,
            target,
            fit_mask,
            feature_mode,
        )
        end_position = min(start_position + refit_weeks, len(index))
        signal.iloc[start_position:end_position] = fitted["signal"].iloc[
            start_position:end_position
        ]
        fitted_groups: pd.DataFrame = fitted["group_scores"]
        for group in fitted_groups.columns:
            group_scores.loc[
                index[start_position:end_position], group
            ] = fitted_groups[group].iloc[start_position:end_position]
        history.append(
            {
                "refit_date": refit_date.strftime("%Y-%m-%d"),
                "fit_start": index[fit_start].strftime("%Y-%m-%d"),
                "last_matured_signal_date": index[matured_position].strftime("%Y-%m-%d"),
                "group_weights": {
                    group: float(diagnostic.get("weight") or 0.0)
                    for group, diagnostic in fitted["group_diagnostics"].items()
                },
            }
        )
        latest = fitted
    if latest is None:
        fallback_mask = pd.Series(True, index=index)
        latest = fit_hierarchical_evidence_model(
            features,
            registry,
            target,
            fallback_mask,
            feature_mode,
        )
    latest = {**latest, "signal": signal, "group_scores": group_scores}
    latest["weight_history"] = history
    latest["walkforward_policy"] = {
        "lookback_weeks": lookback_weeks,
        "minimum_history_weeks": minimum_history_weeks,
        "refit_weeks": refit_weeks,
        "target_horizon_weeks": target_horizon_weeks,
        "label_maturity_rule": "refit_date_minus_target_horizon",
    }
    return latest


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1.0 + exp_value)

def _risk_score(value: float, config: AllocatorConfig) -> float:
    adjusted = float(value)
    if config.crowding_penalty > 0.0:
        distance = adjusted - config.crowding_center
        if distance > 30.0:
            saturation = distance
        else:
            saturation = log(1.0 + exp(distance))
        adjusted -= config.crowding_penalty * saturation**2
    return config.exposure_bias + config.exposure_slope * adjusted



def build_exposure(
    signal: pd.Series,
    weekly_returns: pd.Series,
    config: AllocatorConfig,
) -> pd.Series:
    risk_score = signal.fillna(0.0).map(lambda value: _risk_score(float(value), config))
    raw = risk_score.map(
        lambda value: config.exposure_floor
        + (config.exposure_ceiling - config.exposure_floor)
        * _sigmoid(float(value))
    )
    trailing_volatility = weekly_returns.rolling(26, min_periods=13).std(ddof=1).shift(1) * sqrt(52.0)
    volatility_budget = (config.target_volatility / trailing_volatility).clip(upper=1.0).fillna(1.0)
    return (raw * volatility_budget).clip(config.exposure_floor, config.exposure_ceiling)


def _max_drawdown(returns: pd.Series) -> float:
    nav = (1.0 + returns.fillna(0.0)).cumprod()
    if nav.empty:
        return 0.0
    return float((nav / nav.cummax() - 1.0).min())


def metrics(
    returns: pd.Series,
    benchmark: pd.Series | None = None,
    periods_per_year: float = 52.0,
) -> dict[str, Any]:
    values = returns.dropna().astype(float)
    annualization = float(periods_per_year)
    if values.empty:
        return {
            "periods": 0,
            "annual_return": 0.0,
            "annual_volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
        }
    annual_volatility = (
        float(values.std(ddof=1) * sqrt(annualization)) if len(values) > 1 else 0.0
    )
    annual_return = float(
        (1.0 + values).prod() ** (annualization / len(values)) - 1.0
    )
    sharpe = (
        float(values.mean() / values.std(ddof=1) * sqrt(annualization))
        if values.std(ddof=1) > 0
        else 0.0
    )
    output: dict[str, Any] = {
        "periods": len(values),
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown(values),
        "win_rate": float((values > 0.0).mean()),
    }
    if benchmark is not None:
        aligned = pd.concat(
            [values.rename("strategy"), benchmark.rename("benchmark")], axis=1
        ).dropna()
        excess = aligned["strategy"] - aligned["benchmark"]
        output.update(
            {
                "benchmark_annual_return": float(
                    (1.0 + aligned["benchmark"]).prod()
                    ** (annualization / len(aligned))
                    - 1.0
                )
                if len(aligned)
                else 0.0,
                "benchmark_sharpe": float(
                    aligned["benchmark"].mean()
                    / aligned["benchmark"].std(ddof=1)
                    * sqrt(annualization)
                )
                if len(aligned) > 1 and aligned["benchmark"].std(ddof=1) > 0
                else 0.0,
                "information_ratio": float(
                    excess.mean() / excess.std(ddof=1) * sqrt(annualization)
                )
                if len(excess) > 1 and excess.std(ddof=1) > 0
                else 0.0,
                "annual_excess_return": float(excess.mean() * annualization)
                if len(excess)
                else 0.0,
            }
        )
    return output


def backtest_allocator(
    signal: pd.Series,
    weekly_returns: pd.Series,
    config: AllocatorConfig,
) -> pd.DataFrame:
    exposure = build_exposure(signal, weekly_returns, config)
    next_return = weekly_returns.shift(-1)
    turnover = exposure.diff().abs().fillna(0.0)
    cost = turnover * config.cost_bps / 10000.0
    strategy_return = exposure * next_return - cost
    frame = pd.DataFrame(
        {
            "signal": signal,
            "equity_exposure": exposure,
            "turnover": turnover,
            "transaction_cost": cost,
            "benchmark_return": next_return,
            "strategy_return": strategy_return,
        }
    ).dropna(subset=["benchmark_return"])
    frame["strategy_nav"] = (1.0 + frame["strategy_return"]).cumprod()
    frame["benchmark_nav"] = (1.0 + frame["benchmark_return"]).cumprod()
    frame["drawdown"] = frame["strategy_nav"] / frame["strategy_nav"].cummax() - 1.0
    return frame


def backtest_monthly_cash_overlay(
    signal: pd.Series,
    weekly_returns: pd.Series,
    cash_total_return_levels: pd.Series,
    config: AllocatorConfig,
) -> pd.DataFrame:
    """Execute the weekly liquidity posterior at month-end against cash ETF carry."""
    monthly_signal = signal.groupby(signal.index.to_period("M")).last()
    monthly_exposure = build_exposure(signal, weekly_returns, config).groupby(
        signal.index.to_period("M")
    ).last()
    equity_level = (1.0 + weekly_returns.fillna(0.0)).cumprod()
    equity_return = equity_level.groupby(equity_level.index.to_period("M")).last().pct_change()
    cash_level = cash_total_return_levels.astype(float).copy()
    if not isinstance(cash_level.index, pd.PeriodIndex):
        cash_level.index = pd.to_datetime(cash_level.index).to_period("M")
    cash_return = cash_level.groupby(cash_level.index).last().pct_change()
    frame = pd.concat(
        [
            monthly_signal.rename("signal"),
            monthly_exposure.rename("equity_exposure"),
            equity_return.shift(-1).rename("benchmark_return"),
            cash_return.shift(-1).rename("defensive_return"),
        ],
        axis=1,
    ).dropna(subset=["benchmark_return", "defensive_return", "equity_exposure"])
    frame["turnover"] = frame["equity_exposure"].diff().abs().fillna(0.0)
    frame["transaction_cost"] = frame["turnover"] * config.cost_bps / 10000.0
    frame["strategy_return"] = (
        frame["equity_exposure"] * frame["benchmark_return"]
        + (1.0 - frame["equity_exposure"]) * frame["defensive_return"]
        - frame["transaction_cost"]
    )
    frame.index = frame.index.to_timestamp("M")
    frame["strategy_nav"] = (1.0 + frame["strategy_return"]).cumprod()
    frame["benchmark_nav"] = (1.0 + frame["benchmark_return"]).cumprod()
    frame["drawdown"] = frame["strategy_nav"] / frame["strategy_nav"].cummax() - 1.0
    return frame


def split_metrics(
    backtest: pd.DataFrame,
    splits: dict[str, tuple[str, str]],
    periods_per_year: float = 52.0,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for name, (start, end) in splits.items():
        subset = backtest.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        output[name] = metrics(
            subset["strategy_return"],
            subset["benchmark_return"],
            periods_per_year=periods_per_year,
        )
        output[name]["average_exposure"] = (
            float(subset["equity_exposure"].mean()) if len(subset) else 0.0
        )
        output[name]["annual_turnover"] = (
            float(subset["turnover"].mean() * periods_per_year) if len(subset) else 0.0
        )
    return output


def selection_score(split_rows: dict[str, dict[str, Any]]) -> float:
    train = split_rows["train"]
    valid = split_rows["valid"]
    train_sharpe = _safe_number(train.get("sharpe"))
    valid_sharpe = _safe_number(valid.get("sharpe"))
    train_ir = _safe_number(train.get("information_ratio"))
    valid_ir = _safe_number(valid.get("information_ratio"))
    valid_drawdown = abs(_safe_number(valid.get("max_drawdown")))
    turnover = _safe_number(valid.get("annual_turnover"))
    return float(
        0.22 * train_sharpe
        + 0.38 * valid_sharpe
        + 0.12 * min(train_sharpe, valid_sharpe)
        + 0.10 * train_ir
        + 0.18 * valid_ir
        - 0.20 * valid_drawdown
        - 0.01 * turnover
    )


def deflated_sharpe_confidence(
    returns: pd.Series,
    trials: int,
    periods_per_year: float = 52.0,
) -> dict[str, float]:
    values = returns.dropna().astype(float)
    if len(values) < 24 or values.std(ddof=1) <= 0.0:
        return {"adjusted_sharpe": 0.0, "confidence": 0.0}
    weekly_sharpe = float(values.mean() / values.std(ddof=1))
    annual_sharpe = weekly_sharpe * sqrt(periods_per_year)
    null_weekly = sqrt(max(0.0, 2.0 * log(max(trials, 1))) / len(values))
    skew = float(values.skew())
    kurtosis = float(values.kurtosis() + 3.0)
    denominator = sqrt(
        max(
            1.0e-12,
            1.0 - skew * weekly_sharpe + (kurtosis - 1.0) * weekly_sharpe**2 / 4.0,
        )
    )
    statistic = (weekly_sharpe - null_weekly) * sqrt(len(values) - 1.0) / denominator
    return {
        "adjusted_sharpe": float(
            (weekly_sharpe - null_weekly) * sqrt(periods_per_year)
        ),
        "confidence": float(_normal_cdf(statistic)),
        "observed_sharpe": annual_sharpe,
        "trial_count": float(trials),
    }


def conditional_return_table(backtest: pd.DataFrame, mask: pd.Series) -> list[dict[str, Any]]:
    subset = backtest.loc[mask.reindex(backtest.index, fill_value=False)].dropna(subset=["signal"])
    if len(subset) < 50:
        return []
    ranks = subset["signal"].rank(method="first")
    buckets = pd.qcut(ranks, 5, labels=False, duplicates="drop")
    rows: list[dict[str, Any]] = []
    for bucket in sorted(buckets.dropna().unique()):
        sample = subset.loc[buckets == bucket]
        rows.append(
            {
                "bucket": int(bucket) + 1,
                "observations": len(sample),
                "average_next_week_return": float(sample["benchmark_return"].mean()),
                "average_next_period_return": float(sample["benchmark_return"].mean()),
                "positive_rate": float((sample["benchmark_return"] > 0.0).mean()),
                "average_exposure": float(sample["equity_exposure"].mean()),
            }
        )
    return rows


def compact_backtest(backtest: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for date, row in backtest.iterrows():
        rows.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "signal": _safe_number(row["signal"]),
                "equity_exposure": _safe_number(row["equity_exposure"]),
                "turnover": _safe_number(row["turnover"]),
                "strategy_return": _safe_number(row["strategy_return"]),
                "benchmark_return": _safe_number(row["benchmark_return"]),
                "strategy_nav": _safe_number(row["strategy_nav"]),
                "benchmark_nav": _safe_number(row["benchmark_nav"]),
                "drawdown": _safe_number(row["drawdown"]),
            }
        )
    return rows


def cost_sensitivity(
    signal: pd.Series,
    weekly_returns: pd.Series,
    base: AllocatorConfig,
    costs: Iterable[float] = (5.0, 10.0, 15.0, 20.0, 30.0),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cost in costs:
        config = AllocatorConfig(**{**asdict(base), "cost_bps": float(cost)})
        backtest = backtest_allocator(signal, weekly_returns, config)
        rows.append({"cost_bps": float(cost), **metrics(backtest["strategy_return"], backtest["benchmark_return"])})
    return rows


def cost_sensitivity_monthly_cash(
    signal: pd.Series,
    weekly_returns: pd.Series,
    cash_total_return_levels: pd.Series,
    base: AllocatorConfig,
    costs: Iterable[float] = (5.0, 10.0, 15.0, 20.0, 30.0),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cost in costs:
        config = AllocatorConfig(**{**asdict(base), "cost_bps": float(cost)})
        backtest = backtest_monthly_cash_overlay(
            signal, weekly_returns, cash_total_return_levels, config
        )
        rows.append(
            {
                "cost_bps": float(cost),
                **metrics(
                    backtest["strategy_return"],
                    backtest["benchmark_return"],
                    periods_per_year=12.0,
                ),
            }
        )
    return rows
