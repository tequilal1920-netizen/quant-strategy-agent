"""V4.9 research release with causal prosperity acceleration diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd

import engine as worker
import event_overrides as release4
import six_dimension_model as six


_original_feature = worker._feature
_original_candidate_scores = worker._candidate_scores
_original_frequency_payload = worker._frequency_payload
_original_build = worker.build


def _rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True, method="average").where(frame.notna())


def _mean_available(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame.astype(float) for frame in frames]
    numerator = valid[0].fillna(0.0)
    denominator = valid[0].notna().astype(float)
    for frame in valid[1:]:
        numerator = numerator.add(frame.fillna(0.0), fill_value=0.0)
        denominator = denominator.add(frame.notna().astype(float), fill_value=0.0)
    return numerator.div(denominator.replace(0.0, np.nan))

def _cross_sectional_residual_rank(
    signal: pd.DataFrame,
    nuisance: pd.DataFrame,
) -> pd.DataFrame:
    """Remove the current cross-section's linear nuisance exposure causally."""
    y = signal.astype(float)
    x = nuisance.reindex_like(y).astype(float)
    valid = y.notna() & x.notna()
    x_centered = x.sub(x.where(valid).mean(axis=1), axis=0).where(valid)
    y_centered = y.sub(y.where(valid).mean(axis=1), axis=0).where(valid)
    covariance = x_centered.mul(y_centered).sum(axis=1, min_count=12)
    variance = x_centered.pow(2).sum(axis=1, min_count=12).replace(0.0, np.nan)
    beta = covariance.div(variance)
    residual = y.sub(x_centered.mul(beta, axis=0)).where(valid)
    return _rank(residual)




def _directional_path_efficiency(close: pd.DataFrame, window: int) -> pd.DataFrame:
    log_price = np.log(close.where(close > 0))
    displacement = log_price.diff(window)
    path = (
        log_price.diff().abs().rolling(window, min_periods=max(20, window // 2)).sum()
    )
    return displacement.div(path.replace(0.0, np.nan)).clip(-1.0, 1.0)


def _price_rotation_scores(
    close: pd.DataFrame, index: pd.DatetimeIndex
) -> dict[str, pd.DataFrame]:
    aligned_close = close.reindex(index).ffill()
    returns = aligned_close.pct_change(fill_method=None)

    momentum_12_1 = aligned_close.shift(21).div(aligned_close.shift(252)).sub(1.0)
    momentum_6_1 = aligned_close.shift(21).div(aligned_close.shift(126)).sub(1.0)
    momentum_3_1 = aligned_close.shift(5).div(aligned_close.shift(63)).sub(1.0)
    momentum_1 = aligned_close.div(aligned_close.shift(21)).sub(1.0)

    risk_126 = returns.rolling(126, min_periods=63).std(ddof=0)
    risk_adjusted = momentum_6_1.div(risk_126.replace(0.0, np.nan))
    monthly = _mean_available([
        _rank(momentum_12_1.sub(momentum_12_1.mean(axis=1), axis=0)),
        _rank(momentum_6_1.sub(momentum_6_1.mean(axis=1), axis=0)),
        _rank(risk_adjusted),
        _rank(_directional_path_efficiency(aligned_close, 126)),
    ])
    weekly = _mean_available([
        _rank(momentum_1.sub(momentum_1.mean(axis=1), axis=0)),
        _rank(momentum_3_1.sub(momentum_3_1.mean(axis=1), axis=0)),
        _rank(momentum_1.div(returns.rolling(63, min_periods=30).std(ddof=0))),
        _rank(_directional_path_efficiency(aligned_close, 63)),
    ])

    moving_average = aligned_close.rolling(120, min_periods=60).mean()
    distance = aligned_close.div(moving_average).sub(1.0)
    volatility_expansion = returns.rolling(21, min_periods=15).std(ddof=0).div(
        returns.rolling(126, min_periods=63).std(ddof=0).replace(0.0, np.nan)
    )
    crowding_state = _mean_available([
        _rank(momentum_1),
        _rank(distance),
        _rank(volatility_expansion),
    ])
    crowding_percentile = crowding_state.apply(
        lambda series: series.rolling(1250, min_periods=252).rank(pct=True)
    )
    return {
        "monthly": _rank(monthly),
        "weekly": _rank(weekly),
        "crowding_percentile": crowding_percentile,
    }


def _enhanced_momentum_scores(
    close: pd.DataFrame,
    index: pd.DatetimeIndex,
) -> dict[str, pd.DataFrame | pd.Series]:
    """Causal broker-reproduction momentum with reversal and regime states."""
    aligned_close = close.reindex(index).ffill()
    daily_return = aligned_close.pct_change(fill_method=None)
    weekly_return = aligned_close.pct_change(5, fill_method=None)
    weekly_excess = weekly_return.sub(weekly_return.mean(axis=1), axis=0)

    # [t-11m, t-5m] weekly excess return divided by excess volatility.
    formation = weekly_excess.shift(105).rolling(
        126, min_periods=84
    )
    raw_momentum = formation.mean().div(
        formation.std(ddof=0).replace(0.0, np.nan)
    )
    short_excess = aligned_close.pct_change(
        21, fill_method=None
    ).sub(
        aligned_close.pct_change(21, fill_method=None).mean(axis=1),
        axis=0,
    )
    short_percentile = short_excess.apply(
        lambda series: series.rolling(
            252, min_periods=126
        ).rank(pct=True)
    )
    reversal_multiplier = pd.DataFrame(
        np.where(
            raw_momentum.ge(0.0),
            1.0 - short_percentile,
            short_percentile,
        ),
        index=index,
        columns=aligned_close.columns,
    )
    enhanced = raw_momentum.mul(reversal_multiplier)
    enhanced_rank = _rank(enhanced)

    market_return = daily_return.mean(axis=1, skipna=True).fillna(0.0)
    market_nav = (1.0 + market_return).cumprod()
    market_short = market_nav.pct_change(21, fill_method=None)
    market_long = market_nav.pct_change(252, fill_method=None)
    market_acceleration = market_nav.pct_change(
        63, fill_method=None
    ).sub(
        market_nav.shift(63).pct_change(63, fill_method=None)
    )
    breadth = aligned_close.gt(
        aligned_close.rolling(120, min_periods=60).mean()
    ).mean(axis=1)

    def ts_percentile(series: pd.Series) -> pd.Series:
        return series.rolling(1250, min_periods=252).rank(pct=True)

    persistence = pd.concat(
        [
            ts_percentile(market_short),
            ts_percentile(market_long),
            ts_percentile(market_acceleration),
            breadth,
        ],
        axis=1,
    ).mean(axis=1, skipna=True).clip(0.0, 1.0)
    regime_adjusted = enhanced_rank.mul(persistence, axis=0).add(
        (1.0 - enhanced_rank).mul(1.0 - persistence, axis=0)
    )
    return {
        "enhanced_rank": enhanced_rank,
        "regime_adjusted_rank": _rank(regime_adjusted),
        "market_persistence": persistence,
    }


def _walkforward_stability_center(
    contracts,
    aligned,
    index,
    close: pd.DataFrame,
    smoothing: int,
) -> pd.DataFrame:
    """Causal expanding direction with a stable center across lookback windows."""
    output = pd.DataFrame(index=index, columns=list(worker.INDUSTRY_CODES), dtype=float)
    forward_return = close.shift(-21).div(close).sub(1.0)
    for industry, items in contracts.items():
        frame = pd.DataFrame(aligned[industry]).reindex(index)
        target = forward_return[industry].reindex(index)
        window_scores = []
        for window in (504, 756, 1008):
            numerator = pd.Series(0.0, index=index)
            denominator = pd.Series(0.0, index=index)
            available = pd.Series(0, index=index, dtype=int)
            for item in items:
                value = pd.to_numeric(frame[item.variable], errors="coerce")
                correlation = value.rolling(
                    window,
                    min_periods=max(252, window // 2),
                ).corr(target).shift(21)
                direction = np.sign(correlation).replace(0, np.nan)
                reliability = correlation.abs().clip(0.0, 0.15).mul(5.0).add(0.25)
                source_weight = 1.0 if item.source_kind == "direct" else 0.20
                weight = reliability.mul(source_weight)
                signed = value.mul(direction)
                numerator = numerator.add(signed.mul(weight).fillna(0.0), fill_value=0.0)
                denominator = denominator.add(
                    weight.where(signed.notna(), 0.0).fillna(0.0),
                    fill_value=0.0,
                )
                available = available.add(signed.notna().astype(int), fill_value=0)
            composite = numerator.div(denominator.replace(0, np.nan))
            window_scores.append(composite.where(available >= 4))
        stable_center = pd.concat(window_scores, axis=1).median(axis=1, skipna=True)
        output[industry] = stable_center.rolling(
            smoothing,
            min_periods=max(3, smoothing // 3),
        ).mean()
    return _rank(output)


def _feature(contract):
    if contract.source_kind != "event":
        return _original_feature(contract)
    raw = pd.to_numeric(contract.raw, errors="coerce").dropna().sort_index()
    if raw.empty:
        return raw
    activity = np.log1p(raw.rolling(13, min_periods=4).sum())
    mean = activity.rolling(104, min_periods=52).mean()
    std = activity.rolling(104, min_periods=52).std(ddof=0).replace(0, np.nan)
    return activity.sub(mean).div(std).clip(-4, 4).fillna(0.0)


def _candidate_scores(contracts, aligned, diagnostics, index):
    outputs = _original_candidate_scores(contracts, aligned, diagnostics, index)
    direct_dominant = pd.DataFrame(index=index, columns=list(worker.INDUSTRY_CODES), dtype=float)
    for industry, items in contracts.items():
        frame = pd.DataFrame(aligned[industry])
        signs = pd.Series({item.variable: worker._champion_sign(industry, item.variable) for item in items})
        weights = pd.Series({item.variable: (1.0 if item.source_kind == "direct" else 0.20) for item in items})
        signed = frame.mul(signs, axis=1)
        numerator = signed.mul(weights, axis=1).sum(axis=1, min_count=4)
        denominator = signed.notna().mul(weights, axis=1).sum(axis=1).replace(0, np.nan)
        direct_dominant[industry] = numerator.div(denominator)
    outputs["C4_direct_dominant"] = direct_dominant.rank(axis=1, pct=True, method="average").where(direct_dominant.notna())
    outputs["C5_ic_quarter_smooth"] = outputs["C3_train_ic"].rolling(63, min_periods=20).mean().rank(axis=1, pct=True)
    outputs["C6_direct_month_smooth"] = outputs["C4_direct_dominant"].rolling(21, min_periods=8).mean().rank(axis=1, pct=True)
    consensus = outputs["C1_equal"].add(outputs["C3_train_ic"], fill_value=np.nan).add(outputs["C4_direct_dominant"], fill_value=np.nan)
    outputs["C7_consensus"] = consensus.div(3.0).rank(axis=1, pct=True)
    close = worker._CLOSE_CACHE
    if close is not None and not close.empty:
        outputs["C10_monthly_direct_smooth_risk_budget_cash25"] = outputs["C6_direct_month_smooth"].copy()
        outputs["C11_monthly_direct_smooth_risk_budget_cash50"] = outputs["C6_direct_month_smooth"].copy()
        outputs["C14_weekly_direct_smooth_risk_budget_cash25"] = outputs["C6_direct_month_smooth"].copy()
        outputs["C15_weekly_direct_smooth_risk_budget_cash50"] = outputs["C6_direct_month_smooth"].copy()
        price_signals = _price_rotation_scores(close, index)
        monthly_business = _rank(
            outputs["C7_consensus"].rolling(21, min_periods=8).mean()
        )
        weekly_business = _rank(
            outputs["C7_consensus"].rolling(5, min_periods=3).mean()
        )
        monthly_combined = _mean_available([monthly_business, price_signals["monthly"]])
        weekly_combined = _mean_available([weekly_business, price_signals["weekly"]])
        crowded = price_signals["crowding_percentile"].ge(0.90)
        enhanced = _enhanced_momentum_scores(close, index)
        low_crowding = 1.0 - price_signals["crowding_percentile"]
        prosperity_trend = np.sqrt(
            monthly_business.clip(lower=0.0).mul(
                enhanced["regime_adjusted_rank"].clip(lower=0.0)
            )
        )
        prosperity_low_crowding = np.sqrt(
            monthly_business.clip(lower=0.0).mul(
                low_crowding.clip(lower=0.0)
            )
        )
        report_composite = _mean_available(
            [prosperity_trend, prosperity_low_crowding]
        )

        outputs["C18_monthly_residual_path_top5"] = price_signals["monthly"]
        outputs["C19_monthly_business_price_crowding_top5"] = _rank(
            monthly_combined.mask(crowded)
        )
        outputs["C20_weekly_residual_path_top5"] = price_signals["weekly"]
        outputs["C21_weekly_business_price_crowding_top5"] = _rank(
            weekly_combined.mask(crowded)
        )
        outputs[
            "C22_monthly_report_enhanced_momentum_top5"
        ] = _rank(report_composite)
        # Prosperity level and marginal acceleration must agree with price
        # confirmation. Crowding is removed continuously rather than by a
        # hard threshold. The already-observed test interval makes this
        # architecture diagnostic-only.
        acceleration = _rank(
            monthly_business.rolling(21, min_periods=8).mean().sub(
                monthly_business.shift(21).rolling(21, min_periods=8).mean()
            )
        )
        prosperity = _mean_available([monthly_business, acceleration])
        confirmation = _mean_available(
            [price_signals["monthly"], enhanced["regime_adjusted_rank"]]
        )
        confirmed_prosperity = _mean_available(
            [_rank(prosperity), _rank(confirmation)]
        )
        outputs[
            "C23_monthly_post_test_diagnostic_acceleration_confirmed_"
            "crowding_residual_top5_buffered"
        ] = _cross_sectional_residual_rank(
            confirmed_prosperity, price_signals["crowding_percentile"]
        )
    outputs.update(
        six.build_candidates(
            outputs, close, worker.INDUSTRY_CODES, worker.SPLITS
        )
    )
    if close is not None and not close.empty:
        price_signals = _price_rotation_scores(close, index)
        enhanced = _enhanced_momentum_scores(close, index)
        aligned_close = close.reindex(index).ffill()
        returns = aligned_close.pct_change(fill_method=None)
        low_vol_63 = _rank(-returns.rolling(63, min_periods=30).std(ddof=0))
        low_vol_126 = _rank(-returns.rolling(126, min_periods=63).std(ddof=0))
        low_crowding = _rank(1.0 - price_signals["crowding_percentile"])
        monthly_business = _rank(
            outputs["C7_consensus"].rolling(21, min_periods=8).mean()
        )
        weekly_business = _rank(
            outputs["C7_consensus"].rolling(5, min_periods=3).mean()
        )
        state = six.get_state()

        def dimension_frame(
            frequency: str,
            name: str,
            fallback: pd.DataFrame,
        ) -> pd.DataFrame:
            if state is None:
                return fallback
            frame = state.dimensions.get(frequency, {}).get(name)
            if frame is None:
                return fallback
            return frame.reindex(index).reindex(columns=fallback.columns)

        monthly_prosperity = dimension_frame("monthly", "prosperity", monthly_business)
        monthly_fundamental = dimension_frame("monthly", "fundamental", monthly_business)
        monthly_technical = dimension_frame("monthly", "technical", price_signals["monthly"])
        monthly_valuation = dimension_frame("monthly", "valuation", monthly_business)
        monthly_funds = dimension_frame("monthly", "funds", monthly_business)
        monthly_crowding = dimension_frame("monthly", "crowding", 1.0 - low_crowding)
        monthly_anti_crowding = _rank(1.0 - monthly_crowding)
        monthly_trend = _mean_available([
            monthly_technical,
            price_signals["monthly"],
            enhanced["regime_adjusted_rank"],
        ])
        monthly_quality = _mean_available([
            monthly_fundamental,
            monthly_valuation,
            monthly_funds,
        ])
        monthly_canslim = _mean_available([
            monthly_prosperity,
            monthly_quality,
            monthly_trend,
            monthly_anti_crowding,
        ])
        monthly_super = _mean_available([
            monthly_canslim,
            low_vol_63,
            low_vol_126,
            low_crowding,
        ])
        monthly_defensive = _mean_available([
            monthly_prosperity,
            monthly_fundamental,
            monthly_valuation,
            monthly_anti_crowding,
            low_vol_126,
        ])
        outputs[
            "C30_monthly_six_dimension_canslim_top5_"
            "risk_weighted_buffered_cash25"
        ] = _rank(monthly_canslim)
        outputs[
            "C31_monthly_six_dimension_super_weight_top7_"
            "risk_weighted_buffered_cash25"
        ] = _rank(monthly_super)
        outputs[
            "C32_monthly_six_dimension_defensive_top5_"
            "risk_weighted_buffered_cash50"
        ] = _rank(monthly_defensive)

        weekly_prosperity = dimension_frame("weekly", "prosperity", weekly_business)
        weekly_technical = dimension_frame("weekly", "technical", price_signals["weekly"])
        weekly_funds = dimension_frame("weekly", "funds", weekly_business)
        weekly_crowding = dimension_frame("weekly", "crowding", 1.0 - low_crowding)
        weekly_anti_crowding = _rank(1.0 - weekly_crowding)
        weekly_fast = _mean_available([
            weekly_prosperity,
            weekly_technical,
            weekly_funds,
            price_signals["weekly"],
            weekly_anti_crowding,
            low_vol_63,
        ])
        weekly_defensive = _mean_available([
            weekly_business,
            weekly_funds,
            weekly_anti_crowding,
            low_vol_63,
            low_vol_126,
        ])
        outputs[
            "C33_weekly_six_dimension_fast_top5_"
            "risk_weighted_buffered_cash25"
        ] = _rank(weekly_fast)
        outputs[
            "C34_weekly_six_dimension_defensive_top5_"
            "risk_weighted_buffered_cash50"
        ] = _rank(weekly_defensive)
    return outputs


def _frequency_payload(close, scores, frequency):
    payload, score = _original_frequency_payload(close, scores, frequency)
    return six.enrich_frequency_payload(payload, frequency), score


def _build(output):
    preserved_style = {}
    if output.exists():
        try:
            preserved_style = worker._read_json(output).get("style", {})
        except (OSError, ValueError):
            preserved_style = {}
    snapshot = _original_build(output)
    if preserved_style:
        snapshot["style"] = preserved_style
    snapshot = six.enrich_snapshot(snapshot)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        worker.json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(output)
    return snapshot


def configure() -> None:
    unique_event_industries = {
        "农林牧渔", "基础化工", "钢铁", "交通运输", "建筑装饰",
        "商贸零售", "传媒", "通信", "石油石化", "纺织服饰",
        "轻工制造", "机械设备", "煤炭", "美容护理",
    }
    worker.EVENT_BLUEPRINTS = {
        **release4.EVENTS,
        **{name: worker.EVENT_BLUEPRINTS[name] for name in unique_event_industries},
    }
    release4.GAP_INDUSTRIES.update(unique_event_industries)
    worker._event_rows = release4._event_rows
    worker._select_direct_contracts = release4._select
    worker._feature = _feature
    worker._candidate_scores = _candidate_scores
    worker._frequency_payload = _frequency_payload
    worker.build = _build
    worker.CANDIDATE_LABELS.update({
        "C25_monthly_post_test_diagnostic_six_dimension_consensus_top10_buffered": "月频六维分层共识等权",
        "C26_monthly_post_test_diagnostic_six_dimension_online_ic_top10_buffered": "月频冠军锚定在线增强",
        "C27_monthly_post_test_diagnostic_six_dimension_defensive_top10_buffered": "月频质量趋势正交增强",
        "C28_weekly_post_test_diagnostic_six_dimension_fast_top10_buffered": "周频六维快变量等权",
        "C29_weekly_post_test_diagnostic_six_dimension_equal_top10_buffered": "周频冠军锚定在线增强",
        "C30_monthly_six_dimension_canslim_top5_risk_weighted_buffered_cash25": "月频六维CANSLIM风险加权前五",
        "C31_monthly_six_dimension_super_weight_top7_risk_weighted_buffered_cash25": "月频六维SUPER风险加权前七",
        "C32_monthly_six_dimension_defensive_top5_risk_weighted_buffered_cash50": "月频六维防守风险预算前五",
        "C33_weekly_six_dimension_fast_top5_risk_weighted_buffered_cash25": "周频六维快变风险加权前五",
        "C34_weekly_six_dimension_defensive_top5_risk_weighted_buffered_cash50": "周频六维防守风险预算前五",
    })


def main() -> int:
    configure()
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
