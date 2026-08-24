"""V4.9 research release with causal prosperity acceleration diagnostics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import engine as worker
import event_overrides as release4
import six_dimension_model as six


_original_feature = worker._feature
_original_candidate_scores = worker._candidate_scores
_original_frequency_payload = worker._frequency_payload
_original_build = worker.build

PROSPERITY_FRAMEWORK_CANDIDATE = (
    "C40_monthly_post_test_diagnostic_six_dimension_full_prosperity_"
    "framework_top7_risk_weighted_buffered"
)
_PROSPERITY_FRAMEWORK_STATE: dict[str, object] = {}


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


def _weighted_available(frames: list[tuple[pd.DataFrame, float]]) -> pd.DataFrame:
    numerator = None
    denominator = None
    for frame, weight in frames:
        current = frame.astype(float)
        add_num = current.fillna(0.0).mul(weight)
        add_den = current.notna().astype(float).mul(weight)
        if numerator is None:
            numerator, denominator = add_num, add_den
        else:
            numerator = numerator.add(add_num, fill_value=0.0)
            denominator = denominator.add(add_den, fill_value=0.0)
    if numerator is None or denominator is None:
        return pd.DataFrame()
    return numerator.div(denominator.where(denominator.gt(0.0)))


def _contract_weight(item) -> float:
    if item.source_kind == "event":
        return 0.25
    if item.frequency == "周":
        return 0.80
    return 1.00


def _industrial_activity_item(item) -> bool:
    text = "|".join(str(part) for part in (
        item.name, item.source, item.source_spec, item.observation_field, item.frequency
    ))
    keywords = (
        "国家统计局", "工业", "产量", "产值", "销量", "开工", "发电", "用电",
        "运价", "价格指数", "投资", "库存", "商品零售", "景气", "PMI", "物流",
        "施工", "销售面积",
    )
    return item.source_kind == "direct" and item.frequency in {"月", "周"} and any(
        key in text for key in keywords
    )


def _signed_component(contracts, aligned, index, predicate=None, minimum_count: int = 4):
    output = pd.DataFrame(index=index, columns=list(worker.INDUSTRY_CODES), dtype=float)
    coverage: dict[str, int] = {}
    for industry, items in contracts.items():
        frame = pd.DataFrame(aligned[industry]).reindex(index)
        numerator = pd.Series(0.0, index=index)
        denominator = pd.Series(0.0, index=index)
        available = pd.Series(0, index=index, dtype=int)
        used = 0
        for item in items:
            if predicate is not None and not predicate(item):
                continue
            value = pd.to_numeric(frame[item.variable], errors="coerce")
            if value.dropna().empty:
                continue
            signed = value.mul(worker._champion_sign(industry, item.variable))
            weight = _contract_weight(item)
            numerator = numerator.add(signed.mul(weight).fillna(0.0), fill_value=0.0)
            denominator = denominator.add(signed.notna().astype(float).mul(weight), fill_value=0.0)
            available = available.add(signed.notna().astype(int), fill_value=0)
            used += 1
        coverage[industry] = used
        output[industry] = numerator.div(denominator.replace(0.0, np.nan)).where(available.ge(minimum_count))
    return output, coverage

def _diffusion_component(contracts, aligned, index, window: int = 21):
    output = pd.DataFrame(index=index, columns=list(worker.INDUSTRY_CODES), dtype=float)
    coverage: dict[str, int] = {}
    for industry, items in contracts.items():
        frame = pd.DataFrame(aligned[industry]).reindex(index)
        changes = []
        for item in items:
            value = pd.to_numeric(frame[item.variable], errors="coerce")
            if value.dropna().empty:
                continue
            changes.append(value.mul(worker._champion_sign(industry, item.variable)).diff(window))
        if not changes:
            coverage[industry] = 0
            continue
        change_frame = pd.concat(changes, axis=1)
        observed = change_frame.notna().sum(axis=1)
        coverage[industry] = int(change_frame.shape[1])
        output[industry] = change_frame.gt(0.0).sum(axis=1).div(observed.replace(0, np.nan)).where(observed.ge(4))
    return output, coverage


def _pca_nowcasting_rank(components: list[pd.DataFrame]) -> pd.DataFrame:
    index = components[0].index
    columns = components[0].columns
    output = pd.DataFrame(index=index, columns=columns, dtype=float)
    signal_dates = worker._signal_dates(index, "monthly")
    for date in signal_dates:
        if date not in index:
            continue
        current = pd.concat([frame.loc[date] for frame in components], axis=1).dropna(how="all")
        if current.shape[0] < 12 or current.shape[1] < 3:
            continue
        filled = current.fillna(current.mean(axis=0))
        std = filled.std(axis=0, ddof=0).replace(0.0, np.nan)
        standardized = filled.sub(filled.mean(axis=0), axis=1).div(std, axis=1)
        standardized = standardized.dropna(axis=1, how="all").fillna(0.0)
        if standardized.shape[1] < 3:
            continue
        try:
            _, _, vt = np.linalg.svd(standardized.to_numpy(), full_matrices=False)
        except np.linalg.LinAlgError:
            continue
        loadings = vt[0]
        score = standardized.to_numpy().dot(loadings)
        if np.nansum(loadings) < 0:
            score = -score
        output.loc[date, standardized.index] = pd.Series(score, index=standardized.index).rank(pct=True, method="average")
    return output.ffill(limit=45)

def _dtw_similarity(left: pd.Series, right: pd.Series) -> float | None:
    pair = pd.concat([left, right], axis=1).dropna().tail(96)
    if pair.shape[0] < 24:
        return None
    x = pair.iloc[:, 0].to_numpy(dtype=float)
    y = pair.iloc[:, 1].to_numpy(dtype=float)
    sx, sy = np.nanstd(x), np.nanstd(y)
    if sx == 0 or sy == 0 or not np.isfinite(sx) or not np.isfinite(sy):
        return None
    x = (x - np.nanmean(x)) / sx
    y = (y - np.nanmean(y)) / sy
    dp = np.full((len(x) + 1, len(y) + 1), np.inf)
    dp[0, 0] = 0.0
    for i in range(1, len(x) + 1):
        for j in range(max(1, i - 9), min(len(y), i + 9) + 1):
            dp[i, j] = abs(x[i - 1] - y[j - 1]) + min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])
    distance = dp[len(x), len(y)] / max(len(x), len(y))
    return None if not np.isfinite(distance) else float(1.0 / (1.0 + distance))


def _framework_validation(contracts, aligned, anchor: pd.DataFrame, component: pd.DataFrame, index):
    monthly_dates = pd.DatetimeIndex(worker._signal_dates(index, "monthly"))
    train_start, _ = worker.SPLITS["train"]
    _, valid_end = worker.SPLITS["validation"]
    dates = monthly_dates[(monthly_dates >= pd.Timestamp(train_start)) & (monthly_dates <= pd.Timestamp(valid_end))]
    summary: dict[str, dict[str, float | int]] = {}
    for industry, items in contracts.items():
        x = component[industry].reindex(dates)
        y = anchor[industry].reindex(dates)
        pair = pd.concat([x, y], axis=1).dropna()
        count = 0
        raw_frame = pd.DataFrame(aligned[industry]).reindex(dates)
        for item in items:
            value = pd.to_numeric(raw_frame[item.variable], errors="coerce")
            if value.notna().sum() >= 24:
                count += 1
        corr = np.nan
        hit = np.nan
        spread = np.nan
        stable = np.nan
        if pair.shape[0] >= 24 and pair.iloc[:, 0].nunique(dropna=True) >= 3 and pair.iloc[:, 1].nunique(dropna=True) >= 3:
            corr_value = pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman")
            corr = float(corr_value) if pd.notna(corr_value) else np.nan
            diff_pair = pair.diff().dropna()
            if diff_pair.shape[0] >= 12:
                hit = float(diff_pair.iloc[:, 0].mul(diff_pair.iloc[:, 1]).gt(0.0).mean())
            rank = pair.iloc[:, 0].rank(pct=True)
            high = pair.iloc[:, 1].where(rank.ge(0.70)).mean()
            low = pair.iloc[:, 1].where(rank.le(0.30)).mean()
            if pd.notna(high) and pd.notna(low):
                spread = float(high - low)
            rolling = pair.iloc[:, 0].rolling(24, min_periods=18).corr(pair.iloc[:, 1])
            if rolling.notna().sum() >= 12:
                stable = float(rolling.gt(0.0).mean())
        summary[industry] = {
            "有效指标数": int(count),
            "相关检验": corr,
            "方向胜率": hit,
            "分组检验": spread,
            "滚动稳定": stable,
            "OLS拟合R2": float(max(corr, 0.0) ** 2) if np.isfinite(corr) else np.nan,
            "DTW相似": _dtw_similarity(x.reindex(dates), y),
        }
    return summary

def _framework_workbook_snapshot() -> dict[str, dict[str, object]]:
    source = Path(str(worker.CMB_DATA))
    payload: dict[str, dict[str, object]] = {}
    for file, sheet, keys in (
        (source.parent.parent / "景气度-高频.xlsx", "Sheet4", ("月度锚来源", "锚点分位数", "扩散指数分位数", "等权分位分位数", "PCA-Nowcasting分位数")),
        (source.parent / "result.xlsx", "RMSE总表", ("拟合相关", "RMSE", "模型样本数", "最新模型", "景气度分位数")),
    ):
        if not file.exists():
            continue
        try:
            frame = pd.read_excel(file, sheet_name=sheet)
        except (OSError, ValueError):
            continue
        if "申万一级行业" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            industry = str(row.get("申万一级行业", "")).strip()
            if industry not in worker.INDUSTRY_CODES:
                continue
            payload.setdefault(industry, {})
            for key in keys:
                value = row.get(key)
                if pd.isna(value):
                    continue
                payload[industry][f"底稿{key}"] = float(value) if isinstance(value, (int, float)) else str(value)
    return payload


def _build_prosperity_framework_candidate(contracts, aligned, outputs, close, index):
    global _PROSPERITY_FRAMEWORK_STATE
    columns = list(worker.INDUSTRY_CODES)
    business = _rank(outputs["C7_consensus"].rolling(21, min_periods=8).mean())
    equal_raw, equal_coverage = _signed_component(contracts, aligned, index, minimum_count=4)
    equal_percentile = _rank(equal_raw.rolling(21, min_periods=8).mean())
    industrial_raw, industrial_coverage = _signed_component(contracts, aligned, index, predicate=_industrial_activity_item, minimum_count=1)
    industrial_anchor = _rank(industrial_raw.rolling(21, min_periods=5).mean())
    diffusion_raw, diffusion_coverage = _diffusion_component(contracts, aligned, index)
    diffusion = _rank(diffusion_raw.rolling(5, min_periods=3).mean())
    state = six.get_state()
    fundamental = None if state is None else state.dimensions.get("monthly", {}).get("fundamental")
    prosperity = None if state is None else state.dimensions.get("monthly", {}).get("prosperity")
    fundamental = business if fundamental is None else fundamental.reindex(index).reindex(columns=columns)
    prosperity = business if prosperity is None else prosperity.reindex(index).reindex(columns=columns)
    anchor = _weighted_available([(_rank(fundamental), 0.45), (industrial_anchor, 0.35), (_rank(prosperity), 0.20)])
    acceleration = _rank(anchor.rolling(63, min_periods=21).mean().sub(anchor.shift(63).rolling(63, min_periods=21).mean()))
    nowcasting = _pca_nowcasting_rank([_rank(fundamental), industrial_anchor, diffusion, equal_percentile, business])
    price = _price_rotation_scores(close, index)
    enhanced = _enhanced_momentum_scores(close, index)
    confirmation = _mean_available([price["monthly"], enhanced["regime_adjusted_rank"]])
    low_crowding = _rank(1.0 - price["crowding_percentile"])
    raw = _weighted_available([
        (anchor, 0.28), (diffusion, 0.18), (equal_percentile, 0.14),
        (nowcasting, 0.16), (acceleration, 0.10), (_rank(confirmation), 0.10),
        (low_crowding, 0.04),
    ])
    candidate = _cross_sectional_residual_rank(_rank(raw), price["crowding_percentile"])
    validation = _framework_validation(contracts, aligned, anchor, candidate, index)
    workbook = _framework_workbook_snapshot()
    latest_frame = candidate.dropna(how="all")
    latest_date = None if latest_frame.empty else latest_frame.index[-1]
    components = {
        "财报锚": _rank(fundamental), "工业月度锚": industrial_anchor, "高频扩散": diffusion,
        "等权分位": equal_percentile, "PCA-Nowcasting": nowcasting, "三月加速度": acceleration,
        "价格确认": _rank(confirmation), "低拥挤": low_crowding, "综合景气": candidate,
    }
    rows: dict[str, dict[str, object]] = {}
    if latest_date is not None:
        for industry in columns:
            row: dict[str, object] = {
                "综合得分": None if pd.isna(candidate.loc[latest_date, industry]) else float(candidate.loc[latest_date, industry]),
                "直接字段数": int(equal_coverage.get(industry, 0)),
                "工业月度字段数": int(industrial_coverage.get(industry, 0)),
                "扩散字段数": int(diffusion_coverage.get(industry, 0)),
            }
            for name, frame in components.items():
                value = frame.loc[latest_date, industry]
                row[name] = None if pd.isna(value) else float(value)
            row.update(workbook.get(industry, {}))
            row.update(validation.get(industry, {}))
            rows[industry] = row
    _PROSPERITY_FRAMEWORK_STATE = {
        "candidate": PROSPERITY_FRAMEWORK_CANDIDATE,
        "latest_signal_date": None if latest_date is None else str(latest_date.date()),
        "framework": "财报锚+工业月度锚+高频扩散+等权分位+PCA-Nowcasting+三月加速度+价格确认+拥挤度残差",
        "component_names": list(components),
        "validation_window": {"训练开始": str(worker.SPLITS["train"][0]), "验证结束": str(worker.SPLITS["validation"][1]), "测试集用途": "只报告不参与筛选"},
        "summary": {"覆盖行业数": len(rows), "直接字段总数": int(sum(equal_coverage.values())), "工业月度字段总数": int(sum(industrial_coverage.values())), "扩散字段总数": int(sum(diffusion_coverage.values())), "底稿行业数": len(workbook)},
        "industry_rows": rows,
    }
    return candidate


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    return value

def _enrich_snapshot_with_prosperity_framework(snapshot: dict) -> dict:
    if not _PROSPERITY_FRAMEWORK_STATE:
        return snapshot
    high_frequency = snapshot.setdefault("high_frequency", {})
    rows = _PROSPERITY_FRAMEWORK_STATE.get("industry_rows", {})
    if isinstance(rows, dict):
        for item in high_frequency.get("industries", []):
            industry = item.get("industry")
            if industry in rows:
                item["framework"] = _json_safe(rows[industry])
    high_frequency["framework"] = _json_safe({key: value for key, value in _PROSPERITY_FRAMEWORK_STATE.items() if key != "industry_rows"})
    summary = high_frequency.setdefault("summary", {})
    summary["framework_status"] = "enhanced"
    summary["framework_model"] = "财报锚+工业月度锚+高频扩散+分位+Nowcasting+五大检验诊断"
    snapshot.setdefault("method", {})["prosperity_framework"] = high_frequency["framework"]
    return snapshot

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
        outputs[PROSPERITY_FRAMEWORK_CANDIDATE] = _build_prosperity_framework_candidate(
            contracts, aligned, outputs, close, index
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
    snapshot = _enrich_snapshot_with_prosperity_framework(snapshot)
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
        "C35_monthly_post_test_diagnostic_six_dimension_online_factor_stack_top5_risk_weighted_buffered_cash25": "月频在线因子栈风险加权前五",
        "C36_weekly_post_test_diagnostic_six_dimension_online_factor_stack_top5_risk_weighted_buffered_cash25": "周频在线因子栈风险加权前五",
        "C39_monthly_post_test_diagnostic_six_dimension_prosperity_earnings_top7_risk_weighted_buffered": "月频景气盈利确认前七",
        "C41_monthly_post_test_diagnostic_secondary_factor_cluster_top5_risk_weighted_buffered_cash25": "月频二级因子簇精选前五",
        "C42_monthly_post_test_diagnostic_layered_return_regime_gate_top5_risk_weighted_buffered_cash25": "月频分层收益风险环境门控前五",
        "C43_monthly_post_test_diagnostic_layered_return_stable_gate_top5_risk_weighted_buffered_cash35": "月频分层收益稳健门控前五",
        PROSPERITY_FRAMEWORK_CANDIDATE: "月频完整景气框架诊断前七",
    })


def main() -> int:
    configure()
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
