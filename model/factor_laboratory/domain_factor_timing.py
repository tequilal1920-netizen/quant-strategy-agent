"""Broker-style domain factor timing and construction audit.

The functions in this module are deliberately diagnostic-first.  They use only
caller-supplied train/validation windows and report domain-aware factor pools,
permutation/BH heterogeneity checks and quarterly timing ledgers.  They do not
promote a new production champion by themselves; the strategy selector still
has to pass the existing train/validation gates before any default can change.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DOMAIN_TIMING_VERSION = "r36.3-domain-factor-timing-robust-gate"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _domain_timing_cache_root(config: dict[str, Any]) -> Path | None:
    if not _bool_config(config, "enable_domain_timing_cache", True):
        return None
    raw = config.get("domain_timing_cache_dir") or "output/factor_laboratory/domain_timing_cache"
    try:
        root = Path(str(raw))
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        return None


def _database_fingerprint(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("database_path")
    if not raw:
        return {"path": ""}
    path = Path(str(raw))
    try:
        stat = path.stat()
        return {"path": str(path), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    except OSError:
        return {"path": str(path), "missing": True}


def _frame_fingerprint(frame: pd.DataFrame, date_order: list[str]) -> dict[str, Any]:
    return {
        "rows": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "date_count": int(len(date_order)),
        "first_date": str(date_order[0]) if date_order else "",
        "last_date": str(date_order[-1]) if date_order else "",
        "asset_count": int(frame["ts_code"].nunique()) if "ts_code" in frame.columns else 0,
    }


def _cache_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_json_cache(path: Path) -> dict[str, Any] | None:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached if isinstance(cached, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return


def _read_feature_cache(path: Path) -> dict[str, Any] | None:
    try:
        cached = pd.read_pickle(path)
        return cached if isinstance(cached, dict) else None
    except Exception:
        return None


def _write_feature_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        pd.to_pickle(payload, tmp)
        tmp.replace(path)
    except Exception:
        return


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _int_config(config: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _float_config(config: dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _bool_config(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _rank_pct_by_date(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return values.groupby(frame["trade_date"], sort=False).rank(pct=True, method="average")


def _mean_rank_score(frame: pd.DataFrame, signed_columns: list[tuple[str, float]]) -> pd.Series:
    ranked: list[pd.Series] = []
    for column, sign in signed_columns:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce") * float(sign)
        ranked.append(values.groupby(frame["trade_date"], sort=False).rank(pct=True, method="average"))
    if not ranked:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.concat(ranked, axis=1).mean(axis=1, skipna=True)


def _tertile_labels_by_date(score: pd.Series, dates: pd.Series, labels: tuple[str, str, str]) -> pd.Series:
    ranked = score.groupby(dates, sort=False).rank(pct=True, method="average")
    out = pd.Series(pd.NA, index=score.index, dtype="object")
    out.loc[ranked <= 0.30] = labels[0]
    out.loc[(ranked > 0.30) & (ranked < 0.70)] = labels[1]
    out.loc[ranked >= 0.70] = labels[2]
    return out


def _spearman(a: pd.Series, b: pd.Series) -> float:
    pair = pd.DataFrame({"a": a, "b": b}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 2:
        return 0.0
    ra = pair["a"].rank(method="average")
    rb = pair["b"].rank(method="average")
    corr = ra.corr(rb)
    return float(corr) if corr is not None and math.isfinite(float(corr)) else 0.0


def _safe_corr_rank(group: pd.DataFrame, factor: str, target_col: str) -> float | None:
    if len(group) < 2:
        return None
    x = pd.to_numeric(group[factor], errors="coerce").replace([np.inf, -np.inf], np.nan)
    y = pd.to_numeric(group[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    pair = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(pair) < 2:
        return None
    corr = pair["x"].rank(pct=True, method="average").corr(pair["y"].rank(pct=True, method="average"))
    return float(corr) if corr is not None and math.isfinite(float(corr)) else None


def _bh_adjust(p_values: list[float]) -> tuple[list[float], list[bool]]:
    m = len(p_values)
    if m == 0:
        return [], []
    order = sorted(range(m), key=lambda idx: p_values[idx])
    adjusted = [1.0] * m
    running = 1.0
    for rank_from_end, idx in enumerate(reversed(order), start=1):
        rank = m - rank_from_end + 1
        running = min(running, p_values[idx] * m / max(rank, 1))
        adjusted[idx] = min(1.0, running)
    passed = [value <= 0.05 for value in adjusted]
    return adjusted, passed


def construction_recommendations(factor_name: str) -> list[str]:
    name = factor_name.lower()
    notes: list[str] = []
    if name in {"ret_1", "ret_5", "ret_20", "ret_60", "range_1", "gap_1"}:
        notes.append("原始量价过于粗糙：建议改为行业/市值中性残差、跳过最近窗口的动量-反转组合或异常行情过滤版本")
    if name in {"value_ep", "value_bp", "value_sp", "dividend", "log_mv"}:
        notes.append("估值/规模原始口径偏粗：建议加入行业内分位、历史分位、质量交互和极端估值约束")
    if "flow" in name or "money" in name:
        notes.append("资金流单日口径易噪声：建议改为滚动持续性、成交额标准化、拥挤反转和大单/超大单一致性版本")
    if name in {"quality_roe", "quality_roa", "quality_gross_margin", "quality_asset_turn", "quality_low_leverage"}:
        notes.append("质量因子建议使用TTM/单季度变化/稳定性三口径，并以披露可见日做严格滞后")
    if name.startswith("growth_"):
        notes.append("成长因子建议加入增长加速度、盈利质量交叉和行业景气中性，避免只看单一同比")
    if name.startswith("ai_") or "llm" in name or "mcts" in name or "openfe" in name:
        notes.append("挖掘因子必须保留表达式、父代、变异路径、失败记忆和低相关审计，不能只保存最终数值")
    return notes


def audit_factor_construction_quality(
    frame: pd.DataFrame,
    *,
    candidate_features: list[str],
    target_col: str,
    dates: set[str],
    min_coverage: float,
    min_assets_per_date: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    scope = frame.loc[frame["trade_date"].isin(dates)].copy()
    if "model_eligible" in scope.columns:
        scope = scope.loc[scope["model_eligible"].astype(bool)]
    for factor in candidate_features:
        if factor not in scope.columns:
            rows.append({"factor_name": factor, "status": "missing_column", "severity": "block"})
            continue
        values = pd.to_numeric(scope[factor], errors="coerce").replace([np.inf, -np.inf], np.nan)
        coverage = float(values.notna().mean()) if len(values) else 0.0
        finite = values.dropna()
        flags: list[str] = []
        if coverage < min_coverage:
            flags.append("低覆盖")
        if len(finite) == 0:
            flags.append("无有效暴露")
        else:
            unique_ratio = float(finite.nunique(dropna=True) / max(len(finite), 1))
            if unique_ratio < 0.005:
                flags.append("有效取值过少")
            q01, q50, q99 = finite.quantile([0.01, 0.50, 0.99]).tolist()
            mad = float((finite - q50).abs().median())
            if mad > 0:
                outlier_ratio = float(((finite - q50).abs() / mad > 12).mean())
                if outlier_ratio > 0.02:
                    flags.append("极端值占比偏高")
            if abs(float(q99) - float(q01)) < 1e-12:
                flags.append("截面几乎常数")
        daily_dates = 0
        ic_values: list[float] = []
        if factor in scope.columns and target_col in scope.columns:
            for _, group in scope[["trade_date", factor, target_col]].dropna().groupby("trade_date", sort=False):
                if len(group) < min_assets_per_date:
                    continue
                corr = _safe_corr_rank(group, factor, target_col)
                if corr is not None:
                    daily_dates += 1
                    ic_values.append(corr)
        mean_ic = float(np.mean(ic_values)) if ic_values else 0.0
        icir = 0.0
        if len(ic_values) > 1:
            std = float(np.std(ic_values, ddof=1))
            if std > 1e-12:
                icir = mean_ic / std * math.sqrt(len(ic_values))
        if abs(mean_ic) < 0.003 and daily_dates >= 20:
            flags.append("训练验证期RankIC过弱")
        recommendations = construction_recommendations(factor)
        if recommendations and not flags:
            flags.append("构造可升级")
        severity = "pass"
        if any(flag in flags for flag in ["低覆盖", "无有效暴露", "有效取值过少", "截面几乎常数"]):
            severity = "block"
        elif flags:
            severity = "warn"
        rows.append({
            "factor_name": factor,
            "coverage": coverage,
            "daily_ic_count": daily_dates,
            "mean_rank_ic": mean_ic,
            "icir": icir,
            "flags": flags,
            "recommendations": recommendations,
            "severity": severity,
        })
    return {
        "status": "ready",
        "audited_count": len(rows),
        "block_count": sum(1 for row in rows if row.get("severity") == "block"),
        "warn_count": sum(1 for row in rows if row.get("severity") == "warn"),
        "pass_count": sum(1 for row in rows if row.get("severity") == "pass"),
        "worst_sample": sorted(rows, key=lambda row: (row.get("severity") != "block", row.get("severity") != "warn", abs(_finite(row.get("mean_rank_ic")))), reverse=False)[:25],
    }


def build_domain_labels(frame: pd.DataFrame, *, target_col: str) -> dict[str, pd.Series]:
    labels: dict[str, pd.Series] = {}
    if "industry_name" in frame.columns:
        industry = frame["industry_name"].astype("object").where(frame["industry_name"].notna(), "未知")
        labels["industry"] = industry
    if "log_mv" in frame.columns:
        labels["size"] = _tertile_labels_by_date(_rank_pct_by_date(frame, "log_mv"), frame["trade_date"], ("小市值", "中市值", "大市值"))
    value_score = _mean_rank_score(frame, [("value_ep", 1), ("value_bp", 1), ("value_sp", 1), ("dividend", 1)])
    growth_score = _mean_rank_score(frame, [("growth_revenue", 1), ("growth_operating_profit", 1), ("growth_net_profit", 1), ("ret_60", 1)])
    if value_score.notna().any() and growth_score.notna().any():
        labels["style"] = _tertile_labels_by_date(value_score - growth_score, frame["trade_date"], ("成长", "均衡", "价值"))
    ds_parts: list[pd.Series] = []
    peer_keys = [frame["trade_date"]]
    if "industry_name" in frame.columns:
        peer_keys.append(frame["industry_name"].astype("object").where(frame["industry_name"].notna(), "未知"))
    for column in ["vol_20", "turnover", "range_1"]:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        peer = values.groupby(peer_keys, sort=False).transform("median")
        ds = (values - peer).abs() / (values.abs() + peer.abs() + 1e-12)
        ds_parts.append(ds)
    if ds_parts:
        ds_score = pd.concat(ds_parts, axis=1).mean(axis=1, skipna=True)
        labels["behavior_ds"] = _tertile_labels_by_date(ds_score, frame["trade_date"], ("低偏离", "中偏离", "高偏离"))
    if target_col in frame.columns:
        rev_score = _mean_rank_score(frame, [("value_ep", 1), ("value_bp", 1), ("ret_20", -1), ("vol_20", -1), ("down_vol_20", -1)])
        mom_score = _mean_rank_score(frame, [("ret_60", 1), ("growth_revenue", 1), ("growth_operating_profit", 1), ("quality_roe", 1), ("quality_roa", 1)])
        target_rank = pd.to_numeric(frame[target_col], errors="coerce").groupby(frame["trade_date"], sort=False).rank(pct=True, method="average")
        rev_error = (rev_score - target_rank).abs()
        mom_error = (mom_score - target_rank).abs()
        rev_cut = rev_error.groupby(frame["trade_date"], sort=False).transform(lambda x: x.quantile(0.30))
        mom_cut = mom_error.groupby(frame["trade_date"], sort=False).transform(lambda x: x.quantile(0.30))
        supervised = pd.Series("均衡域", index=frame.index, dtype="object")
        supervised.loc[(rev_error <= rev_cut) & (mom_error > mom_cut)] = "均值回复域"
        supervised.loc[(mom_error <= mom_cut) & (rev_error > rev_cut)] = "趋势域"
        labels["supervised_pricing"] = supervised.where(rev_score.notna() & mom_score.notna() & target_rank.notna())
    return labels


def _domain_ic_table(
    frame: pd.DataFrame,
    *,
    factor: str,
    target_col: str,
    domain: pd.Series,
    dates: set[str],
    min_assets_per_domain: int,
) -> pd.DataFrame:
    work = pd.DataFrame({
        "trade_date": frame["trade_date"],
        "domain": domain,
        factor: frame[factor] if factor in frame.columns else np.nan,
        target_col: frame[target_col],
    })
    if "model_eligible" in frame.columns:
        work["model_eligible"] = frame["model_eligible"].astype(bool)
        work = work.loc[work["model_eligible"]]
    work = work.loc[work["trade_date"].isin(dates)].copy()
    work[factor] = pd.to_numeric(work[factor], errors="coerce").replace([np.inf, -np.inf], np.nan)
    work[target_col] = pd.to_numeric(work[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    work = work.dropna(subset=["domain", factor, target_col])
    if work.empty:
        return pd.DataFrame()
    group_keys = ["trade_date", "domain"]
    grouped = work.groupby(group_keys, sort=False)
    work["x_rank"] = grouped[factor].rank(method="average", pct=True)
    work["y_rank"] = grouped[target_col].rank(method="average", pct=True)
    work["xx"] = work["x_rank"] * work["x_rank"]
    work["yy"] = work["y_rank"] * work["y_rank"]
    work["xy"] = work["x_rank"] * work["y_rank"]
    stats = work.groupby(group_keys, sort=False).agg(
        n=("x_rank", "size"),
        sx=("x_rank", "sum"),
        sy=("y_rank", "sum"),
        sxx=("xx", "sum"),
        syy=("yy", "sum"),
        sxy=("xy", "sum"),
    ).reset_index()
    stats = stats.loc[stats["n"] >= int(min_assets_per_domain)].copy()
    if stats.empty:
        return pd.DataFrame()
    n = stats["n"].astype(float)
    numerator = n * stats["sxy"] - stats["sx"] * stats["sy"]
    denom_x = n * stats["sxx"] - stats["sx"] * stats["sx"]
    denom_y = n * stats["syy"] - stats["sy"] * stats["sy"]
    denominator = np.sqrt(denom_x.clip(lower=0.0) * denom_y.clip(lower=0.0))
    stats["rank_ic"] = numerator / denominator.replace(0.0, np.nan)
    stats = stats.replace([np.inf, -np.inf], np.nan).dropna(subset=["rank_ic"])
    if stats.empty:
        return pd.DataFrame()
    return stats.pivot_table(index="trade_date", columns="domain", values="rank_ic", aggfunc="mean")


def _permutation_p_value(matrix: pd.DataFrame, *, permutations: int, seed: int) -> tuple[float, float]:
    matrix = matrix.dropna(axis=1, how="all")
    if matrix.shape[1] < 2 or matrix.shape[0] < 2:
        return 0.0, 1.0
    observed = float(matrix.mean(axis=0, skipna=True).var(ddof=0))
    if observed <= 0 or permutations <= 0:
        return observed, 1.0
    rng = np.random.default_rng(seed)
    greater = 0
    rows = [row.dropna().to_numpy(dtype=float) for _, row in matrix.iterrows() if row.dropna().size >= 2]
    columns = list(matrix.columns)
    if not rows:
        return observed, 1.0
    for _ in range(permutations):
        bucket = {col: [] for col in columns}
        for values in rows:
            shuffled = rng.permutation(values)
            chosen_cols = rng.choice(columns, size=len(shuffled), replace=False) if len(shuffled) <= len(columns) else columns
            for col, value in zip(chosen_cols, shuffled):
                bucket[col].append(float(value))
        means = [np.mean(v) for v in bucket.values() if v]
        stat = float(np.var(means)) if len(means) >= 2 else 0.0
        if stat >= observed:
            greater += 1
    return observed, float((greater + 1) / (permutations + 1))


def domain_heterogeneity_tests(
    frame: pd.DataFrame,
    *,
    candidate_features: list[str],
    target_col: str,
    dates: set[str],
    domains: dict[str, pd.Series],
    min_assets_per_domain: int,
    min_dates: int,
    permutations: int,
    seed: int,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for domain_name, domain_series in domains.items():
        factor_rows: list[dict[str, Any]] = []
        for factor_index, factor in enumerate(candidate_features):
            if factor not in frame.columns:
                continue
            ic_matrix = _domain_ic_table(
                frame,
                factor=factor,
                target_col=target_col,
                domain=domain_series,
                dates=dates,
                min_assets_per_domain=min_assets_per_domain,
            )
            if ic_matrix.empty or len(ic_matrix) < min_dates or ic_matrix.shape[1] < 2:
                continue
            stat, p_value = _permutation_p_value(ic_matrix, permutations=permutations, seed=seed + factor_index * 17)
            means = ic_matrix.mean(axis=0, skipna=True).sort_values(ascending=False)
            split_at = max(1, len(ic_matrix) // 2)
            first = ic_matrix.iloc[:split_at].mean(axis=0, skipna=True)
            second = ic_matrix.iloc[split_at:].mean(axis=0, skipna=True)
            stability = _spearman(first, second)
            factor_rows.append({
                "factor_name": factor,
                "domain_count": int(ic_matrix.shape[1]),
                "ic_date_count": int(len(ic_matrix)),
                "heterogeneity_stat": stat,
                "p_value": p_value,
                "mean_rank_ic_by_domain": {str(k): float(v) for k, v in means.items() if math.isfinite(float(v))},
                "best_domain": str(means.index[0]) if len(means) else "",
                "worst_domain": str(means.index[-1]) if len(means) else "",
                "match_stability_spearman": stability,
            })
        q_values, passed = _bh_adjust([float(row["p_value"]) for row in factor_rows])
        for row, q_value, is_passed in zip(factor_rows, q_values, passed):
            row["bh_q_value"] = q_value
            row["bh_significant"] = bool(is_passed)
            row["trusted_for_domain_allocation"] = bool(is_passed and row.get("match_stability_spearman", 0.0) >= 0.20)
        significant = [row for row in factor_rows if row.get("bh_significant")]
        trusted = [row for row in factor_rows if row.get("trusted_for_domain_allocation")]
        factor_rows = sorted(
            factor_rows,
            key=lambda row: (
                bool(row.get("trusted_for_domain_allocation")),
                -float(row.get("bh_q_value", 1.0)),
                abs(_finite(row.get("heterogeneity_stat"))),
            ),
            reverse=True,
        )
        reports.append({
            "domain_scheme": domain_name,
            "tested_factor_count": len(factor_rows),
            "significant_factor_count": len(significant),
            "trusted_factor_count": len(trusted),
            "heterogeneity_coverage": float(len(significant) / len(factor_rows)) if factor_rows else 0.0,
            "trusted_coverage": float(len(trusted) / len(factor_rows)) if factor_rows else 0.0,
            "top_factors": factor_rows[:25],
        })
    return reports


def _selection_date_window(date_order: list[str], split: dict[str, tuple[int, int]], *, lookback_days: int, as_of_index: int | None = None) -> tuple[set[str], dict[str, Any]]:
    train_start, train_end = split["train"]
    valid_end = split["valid"][1]
    end_index = min(valid_end, len(date_order)) if as_of_index is None else min(valid_end, max(train_start + 1, int(as_of_index)))
    start_index = max(train_start, end_index - max(20, int(lookback_days)))
    return set(date_order[start_index:end_index]), {
        "selection_start_date": date_order[start_index] if start_index < len(date_order) else "",
        "selection_end_date": date_order[end_index - 1] if end_index > start_index else "",
        "selection_start_index": int(start_index),
        "selection_end_index_exclusive": int(end_index),
        "train_end_index": int(train_end),
        "valid_end_index_exclusive": int(valid_end),
    }


def _quarterly_factor_timing(
    frame: pd.DataFrame,
    *,
    candidate_features: list[str],
    target_col: str,
    date_order: list[str],
    split: dict[str, tuple[int, int]],
    lookback_days: int,
    rebalance_days: int,
    min_assets_per_date: int,
    max_factors: int,
) -> list[dict[str, Any]]:
    train_start, train_end = split["train"]
    valid_end = split["valid"][1]
    checkpoints = list(range(max(train_end, train_start + lookback_days), valid_end, max(1, rebalance_days)))
    if valid_end not in checkpoints:
        checkpoints.append(valid_end)
    rows: list[dict[str, Any]] = []
    for index in checkpoints[-12:]:
        dates, window = _selection_date_window(date_order, split, lookback_days=lookback_days, as_of_index=index)
        scored: list[dict[str, Any]] = []
        for factor in candidate_features:
            if factor not in frame.columns:
                continue
            ic_values: list[float] = []
            scope = frame.loc[frame["trade_date"].isin(dates)]
            if "model_eligible" in scope.columns:
                scope = scope.loc[scope["model_eligible"].astype(bool)]
            for _, group in scope[["trade_date", factor, target_col]].dropna().groupby("trade_date", sort=False):
                if len(group) < min_assets_per_date:
                    continue
                corr = _safe_corr_rank(group, factor, target_col)
                if corr is not None:
                    ic_values.append(corr)
            if len(ic_values) < 3:
                continue
            mean_ic = float(np.mean(ic_values))
            std = float(np.std(ic_values, ddof=1)) if len(ic_values) > 1 else 0.0
            icir = mean_ic / std * math.sqrt(len(ic_values)) if std > 1e-12 else 0.0
            timing_weight = max(0.0, icir)
            scored.append({"factor_name": factor, "mean_rank_ic": mean_ic, "icir": icir, "timing_weight_raw": timing_weight})
        scored = sorted(scored, key=lambda row: (row["timing_weight_raw"], abs(row["mean_rank_ic"])), reverse=True)[:max_factors]
        total = sum(row["timing_weight_raw"] for row in scored)
        for row in scored:
            row["timing_weight"] = float(row["timing_weight_raw"] / total) if total > 0 else 0.0
        rows.append({
            "as_of_date": window.get("selection_end_date", ""),
            "selected_count": len(scored),
            "top_factors": scored[:15],
            "policy": "季度滚动；过去窗口Rank ICIR负值归零后归一化；仅训练/验证窗口可见数据",
        })
    return rows


def build_domain_factor_timing_report(
    frame: pd.DataFrame,
    *,
    candidate_features: list[str],
    selected_features: list[str],
    base_features: list[str],
    target_col: str,
    date_order: list[str],
    split: dict[str, tuple[int, int]],
    config: dict[str, Any],
) -> dict[str, Any]:
    if not _bool_config(config, "enable_domain_factor_timing", True):
        return {"status": "disabled", "version": DOMAIN_TIMING_VERSION}
    if not candidate_features or target_col not in frame.columns:
        return {"status": "unavailable", "version": DOMAIN_TIMING_VERSION, "message": "missing_candidate_features_or_target"}
    candidate_limit = _int_config(config, "domain_timing_candidate_limit", 80, 1, 240)
    audit_limit = _int_config(config, "domain_timing_audit_limit", 140, 1, 400)
    lookback_days = _int_config(config, "domain_timing_lookback_days", 252 * 5, 60, 2520)
    rebalance_days = _int_config(config, "domain_timing_rebalance_days", 63, 20, 252)
    min_assets = _int_config(config, "domain_timing_min_assets_per_domain", 20, 3, 300)
    min_dates = _int_config(config, "domain_timing_min_dates", 24, 3, 252)
    permutations = _int_config(config, "domain_timing_permutations", 100, 0, 2000)
    seed = _int_config(config, "domain_timing_seed", 20260818, 1, 2_000_000_000)
    timing_max_factors = _int_config(config, "domain_timing_max_factors_per_quarter", 30, 1, 120)
    selection_dates, window = _selection_date_window(date_order, split, lookback_days=lookback_days)
    audit_features = list(dict.fromkeys([*selected_features, *base_features, *candidate_features[:audit_limit]]))
    domains = build_domain_labels(frame, target_col=target_col)
    test_features = list(dict.fromkeys([*selected_features, *candidate_features]))[:candidate_limit]
    cache_root = _domain_timing_cache_root(config)
    report_cache_path: Path | None = None
    report_cache_key = ""
    if cache_root is not None:
        report_cache_payload = {
            "kind": "domain_factor_timing_report",
            "version": DOMAIN_TIMING_VERSION,
            "frame": _frame_fingerprint(frame, date_order),
            "database": _database_fingerprint(config),
            "split": split,
            "target_col": target_col,
            "candidate_features": test_features,
            "audit_features": audit_features,
            "config": {
                key: config.get(key)
                for key in [
                    "domain_timing_candidate_limit", "domain_timing_audit_limit", "domain_timing_lookback_days",
                    "domain_timing_rebalance_days", "domain_timing_min_assets_per_domain", "domain_timing_min_dates",
                    "domain_timing_permutations", "domain_timing_seed", "domain_timing_max_factors_per_quarter",
                    "factor_screen_min_coverage", "factor_screen_min_assets_per_date",
                ]
            },
        }
        report_cache_key = _cache_key(report_cache_payload)
        report_cache_path = cache_root / f"domain_report_{report_cache_key}.json"
        cached_report = _read_json_cache(report_cache_path)
        if cached_report is not None:
            cached_report = dict(cached_report)
            cached_report["cache"] = {"status": "hit", "kind": "domain_report", "key": report_cache_key, "path": str(report_cache_path)}
            return cached_report
    construction = audit_factor_construction_quality(
        frame,
        candidate_features=audit_features,
        target_col=target_col,
        dates=selection_dates,
        min_coverage=_float_config(config, "factor_screen_min_coverage", 0.35, 0.01, 0.99),
        min_assets_per_date=_int_config(config, "factor_screen_min_assets_per_date", 30, 5, 500),
    )
    domain_tests = domain_heterogeneity_tests(
        frame,
        candidate_features=test_features,
        target_col=target_col,
        dates=selection_dates,
        domains=domains,
        min_assets_per_domain=min_assets,
        min_dates=min_dates,
        permutations=permutations,
        seed=seed,
    ) if domains else []
    quarterly = _quarterly_factor_timing(
        frame,
        candidate_features=list(dict.fromkeys([*selected_features, *candidate_features]))[:candidate_limit],
        target_col=target_col,
        date_order=date_order,
        split=split,
        lookback_days=max(60, min(lookback_days, 252 * 5)),
        rebalance_days=rebalance_days,
        min_assets_per_date=_int_config(config, "factor_screen_min_assets_per_date", 30, 5, 500),
        max_factors=timing_max_factors,
    )
    result = {
        "status": "ready",
        "version": DOMAIN_TIMING_VERSION,
        "methodology_reference": "量化专题报告：因子布阵手册：从盲打到精准的分域选股实战",
        "selection_scope": "train_plus_validation_only",
        "test_usage": "excluded_from_domain_factor_timing_and_heterogeneity_tests",
        "window": window,
        "domain_schemes": list(domains.keys()),
        "construction_audit": construction,
        "heterogeneity_tests": domain_tests,
        "quarterly_factor_timing": quarterly,
        "policy": {
            "permutation_test": "每个因子先计算域内RankIC矩阵，再置换每期域标签得到零分布",
            "bh_correction": "同一分域方式内对全部因子p值做BH校正，控制FDR=5%",
            "stability": "前后半窗口域均值IC排序Spearman，低稳定因子只做观察不做域配置重仓",
            "domain_training": "域内筛选和ICIR加权只使用训练+验证已成熟标签",
            "domain_combination": "保留域间Alpha水位，不强制域内二次标准化；域Alpha动量作为候选增强项",
            "champion_guard": "本报告不直接改冠军；新分域候选必须训练/验证优于旧冠军且通过门禁才允许替换",
        },
    }
    if report_cache_path is not None:
        result["cache"] = {"status": "miss_written", "kind": "domain_report", "key": report_cache_key, "path": str(report_cache_path)}
        _write_json_cache(report_cache_path, result)
    return result



def _normalize_and_cap_l1_weights(
    scored: list[tuple[str, float, float]],
    *,
    max_abs_weight: float,
) -> dict[str, float]:
    scale = sum(abs(weight) for _, weight, _ in scored)
    if scale <= 0:
        return {}
    weights = {factor: float(weight / scale) for factor, weight, _ in scored}
    if not (0.0 < max_abs_weight < 1.0):
        return weights
    for _ in range(8):
        oversized = {factor for factor, weight in weights.items() if abs(weight) > max_abs_weight}
        if not oversized:
            break
        locked = {factor: math.copysign(max_abs_weight, weights[factor]) for factor in oversized}
        unlocked = {factor: weight for factor, weight in weights.items() if factor not in oversized}
        remaining = max(0.0, 1.0 - sum(abs(weight) for weight in locked.values()))
        unlocked_abs = sum(abs(weight) for weight in unlocked.values())
        if remaining <= 0.0 or unlocked_abs <= 1e-12:
            weights = locked
            break
        weights = {
            **locked,
            **{factor: float(weight * remaining / unlocked_abs) for factor, weight in unlocked.items()},
        }
    final_scale = sum(abs(weight) for weight in weights.values())
    if final_scale <= 0:
        return {}
    return {factor: float(weight / final_scale) for factor, weight in weights.items()}


def _factor_coefficients(
    frame: pd.DataFrame,
    *,
    factor_names: list[str],
    target_col: str,
    dates: set[str],
    min_assets_per_date: int,
    max_factors: int,
    allow_negative_direction: bool,
    min_abs_mean_ic: float = 0.0,
    min_abs_icir: float = 0.0,
    max_abs_weight: float = 1.0,
) -> dict[str, float]:
    scored: list[tuple[str, float, float]] = []
    scope = frame.loc[frame["trade_date"].isin(dates)]
    if "model_eligible" in scope.columns:
        scope = scope.loc[scope["model_eligible"].astype(bool)]
    for factor in factor_names:
        if factor not in scope.columns:
            continue
        ics: list[float] = []
        for _, group in scope[["trade_date", factor, target_col]].dropna().groupby("trade_date", sort=False):
            if len(group) < min_assets_per_date:
                continue
            corr = _safe_corr_rank(group, factor, target_col)
            if corr is not None:
                ics.append(corr)
        if len(ics) < 3:
            continue
        mean_ic = float(np.mean(ics))
        std_ic = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
        icir = mean_ic / std_ic * math.sqrt(len(ics)) if std_ic > 1e-12 else 0.0
        if abs(mean_ic) < min_abs_mean_ic or abs(icir) < min_abs_icir:
            continue
        if allow_negative_direction:
            strength = abs(icir)
            direction = 1.0 if mean_ic >= 0 else -1.0
        else:
            strength = max(0.0, icir)
            direction = 1.0
        if strength <= 0:
            continue
        scored.append((factor, direction * strength, abs(mean_ic)))
    scored = sorted(scored, key=lambda item: (abs(item[1]), item[2]), reverse=True)[:max_factors]
    return _normalize_and_cap_l1_weights(scored, max_abs_weight=max_abs_weight)


def _normalized_exposures(frame: pd.DataFrame, factor_names: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for factor in factor_names:
        if factor not in frame.columns:
            continue
        values = pd.to_numeric(frame[factor], errors="coerce").replace([np.inf, -np.inf], np.nan)
        ranked = values.groupby(frame["trade_date"], sort=False).rank(pct=True, method="average") - 0.5
        out[factor] = ranked.fillna(0.0)
    return out


def _apply_coefficients(normalized: pd.DataFrame, rows: pd.Index, coeffs: dict[str, float]) -> pd.Series:
    if not coeffs:
        return pd.Series(np.nan, index=rows, dtype=float)
    score = pd.Series(0.0, index=rows, dtype=float)
    for factor, weight in coeffs.items():
        if factor in normalized.columns:
            score = score.add(normalized.loc[rows, factor].astype(float) * float(weight), fill_value=0.0)
    return score


def _zscore_map(values: dict[str, float]) -> dict[str, float]:
    finite = np.asarray([v for v in values.values() if math.isfinite(float(v))], dtype=float)
    if finite.size == 0:
        return {key: 0.0 for key in values}
    mean = float(finite.mean())
    std = float(finite.std(ddof=0))
    if std <= 1e-12:
        return {key: 0.0 for key in values}
    return {key: float((value - mean) / std) for key, value in values.items()}


def _domain_alpha_momentum(
    frame: pd.DataFrame,
    *,
    domain: pd.Series,
    target_col: str,
    dates: set[str],
    min_observations: int,
) -> dict[str, float]:
    work = pd.DataFrame({"domain": domain, "target": pd.to_numeric(frame[target_col], errors="coerce"), "trade_date": frame["trade_date"]})
    if "model_eligible" in frame.columns:
        work["model_eligible"] = frame["model_eligible"].astype(bool)
        work = work.loc[work["model_eligible"]]
    work = work.loc[work["trade_date"].isin(dates)].replace([np.inf, -np.inf], np.nan).dropna(subset=["domain", "target"])
    if work.empty:
        return {}
    market_daily = work.groupby("trade_date", sort=False)["target"].mean()
    values: dict[str, float] = {}
    for label, group in work.groupby("domain", sort=False):
        if len(group) < min_observations:
            continue
        daily = group.groupby("trade_date", sort=False)["target"].mean()
        excess = daily.sub(market_daily.reindex(daily.index), fill_value=0.0).dropna()
        if len(excess) < 3:
            continue
        std = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
        values[str(label)] = float(excess.mean() / std * math.sqrt(len(excess))) if std > 1e-12 else 0.0
    return _zscore_map(values)


def _composite_metrics(
    frame: pd.DataFrame,
    *,
    feature_name: str,
    target_col: str,
    dates: set[str],
    min_assets_per_date: int,
) -> dict[str, Any]:
    ics: list[float] = []
    scope = frame.loc[frame["trade_date"].isin(dates)]
    if "model_eligible" in scope.columns:
        scope = scope.loc[scope["model_eligible"].astype(bool)]
    coverage = float(pd.to_numeric(scope[feature_name], errors="coerce").replace([np.inf, -np.inf], np.nan).notna().mean()) if len(scope) else 0.0
    for _, group in scope[["trade_date", feature_name, target_col]].dropna().groupby("trade_date", sort=False):
        if len(group) < min_assets_per_date:
            continue
        corr = _safe_corr_rank(group, feature_name, target_col)
        if corr is not None:
            ics.append(corr)
    mean_ic = float(np.mean(ics)) if ics else 0.0
    std_ic = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
    icir = mean_ic / std_ic * math.sqrt(len(ics)) if std_ic > 1e-12 else 0.0
    return {
        "factor_name": feature_name,
        "coverage": coverage,
        "daily_ic_count": len(ics),
        "mean_rank_ic": mean_ic,
        "icir": icir,
        "hit_rate": float(np.mean(np.sign(ics) == (1 if mean_ic >= 0 else -1))) if ics else 0.0,
    }


def _composite_spread_metrics(
    frame: pd.DataFrame,
    *,
    feature_name: str,
    target_col: str,
    dates: set[str],
    min_assets_per_date: int,
) -> dict[str, Any]:
    spreads: list[float] = []
    scope = frame.loc[frame["trade_date"].isin(dates)]
    if "model_eligible" in scope.columns:
        scope = scope.loc[scope["model_eligible"].astype(bool)]
    for _, group in scope[["trade_date", feature_name, target_col]].dropna().groupby("trade_date", sort=False):
        if len(group) < min_assets_per_date:
            continue
        values = pd.to_numeric(group[feature_name], errors="coerce").replace([np.inf, -np.inf], np.nan)
        target = pd.to_numeric(group[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        pair = pd.DataFrame({"score": values, "target": target}).dropna()
        if len(pair) < min_assets_per_date:
            continue
        ranks = pair["score"].rank(pct=True, method="first")
        low = pair.loc[ranks <= 0.20, "target"]
        high = pair.loc[ranks >= 0.80, "target"]
        if len(low) < 3 or len(high) < 3:
            continue
        spreads.append(float(high.mean() - low.mean()))
    mean_spread = float(np.mean(spreads)) if spreads else 0.0
    std_spread = float(np.std(spreads, ddof=1)) if len(spreads) > 1 else 0.0
    return {
        "factor_name": feature_name,
        "spread_count": len(spreads),
        "mean_top_bottom_spread": mean_spread,
        "spread_ir": mean_spread / std_spread * math.sqrt(len(spreads)) if std_spread > 1e-12 else 0.0,
        "spread_hit_rate": float(np.mean(np.asarray(spreads) > 0.0)) if spreads else 0.0,
    }


def _supervised_model_feature_columns(frame: pd.DataFrame) -> list[str]:
    preferred = [
        "value_ep", "value_bp", "value_sp", "dividend", "log_mv",
        "ret_5", "ret_20", "ret_60", "vol_20", "down_vol_20", "turnover", "range_1", "amihud_20",
        "quality_roe", "quality_roa", "quality_gross_margin", "quality_asset_turn", "quality_low_leverage",
        "growth_revenue", "growth_operating_profit", "growth_net_profit",
        "moneyflow", "large_flow", "extreme_flow",
    ]
    return [col for col in preferred if col in frame.columns]


def _causal_supervised_pricing_domains(
    frame: pd.DataFrame,
    *,
    target_col: str,
    date_order: list[str],
    split: dict[str, tuple[int, int]],
    rebalance_days: int,
    lookback_days: int,
    embargo_days: int,
    sample_cap: int,
    seed: int,
) -> tuple[pd.Series | None, dict[str, Any]]:
    feature_cols = _supervised_model_feature_columns(frame)
    if len(feature_cols) < 6:
        return None, {"status": "unavailable", "reason": "insufficient_supervised_features", "feature_count": len(feature_cols)}
    try:
        from sklearn.tree import DecisionTreeClassifier
    except Exception as exc:  # pragma: no cover
        return None, {"status": "unavailable", "reason": "sklearn_unavailable", "message": str(exc)}
    oracle = build_domain_labels(frame, target_col=target_col).get("supervised_pricing")
    if oracle is None:
        return None, {"status": "unavailable", "reason": "oracle_training_labels_missing"}
    feature_frame = frame[feature_cols].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    feature_frame = feature_frame.groupby(frame["trade_date"], sort=False).transform(lambda x: x.fillna(x.median()))
    feature_frame = feature_frame.fillna(0.0)
    predicted = pd.Series(pd.NA, index=frame.index, dtype="object")
    train_start = split["train"][0]
    valid_end = split["valid"][1]
    checkpoints = list(range(max(train_start + lookback_days, split["train"][1]), valid_end + 1, max(1, rebalance_days)))
    if valid_end not in checkpoints:
        checkpoints.append(valid_end)
    rng = np.random.default_rng(seed)
    fits: list[dict[str, Any]] = []
    for pos, checkpoint in enumerate(checkpoints):
        train_end = max(train_start + 1, checkpoint - max(1, embargo_days))
        train_begin = max(train_start, train_end - max(60, lookback_days))
        train_dates = set(date_order[train_begin:train_end])
        next_checkpoint = checkpoints[pos + 1] if pos + 1 < len(checkpoints) else valid_end
        predict_dates = set(date_order[checkpoint:next_checkpoint])
        if not predict_dates:
            continue
        train_idx = frame.index[frame["trade_date"].isin(train_dates) & oracle.notna()]
        if "model_eligible" in frame.columns:
            train_idx = train_idx[frame.loc[train_idx, "model_eligible"].astype(bool)]
        if len(train_idx) < 200:
            continue
        if len(train_idx) > sample_cap:
            train_idx = pd.Index(rng.choice(train_idx.to_numpy(), size=sample_cap, replace=False))
        y = oracle.loc[train_idx].astype(str)
        if y.nunique() < 2:
            continue
        clf = DecisionTreeClassifier(max_depth=5, min_samples_leaf=max(20, min(250, len(train_idx) // 100)), class_weight="balanced", random_state=seed + pos)
        clf.fit(feature_frame.loc[train_idx, feature_cols], y)
        pred_idx = frame.index[frame["trade_date"].isin(predict_dates)]
        if len(pred_idx) == 0:
            continue
        predicted.loc[pred_idx] = clf.predict(feature_frame.loc[pred_idx, feature_cols])
        fits.append({
            "as_of_date": date_order[checkpoint - 1] if checkpoint > 0 else "",
            "train_start_date": date_order[train_begin] if train_begin < len(date_order) else "",
            "train_end_date": date_order[train_end - 1] if train_end > train_begin else "",
            "train_rows": int(len(train_idx)),
            "classes": sorted(map(str, y.unique().tolist())),
            "predict_date_count": len(predict_dates),
        })
    # Freeze the last validation-trained classifier label path into test dates by carrying the
    # last predicted domain model forward, not by retraining on test labels.
    if fits and valid_end < len(date_order):
        last_known = predicted.loc[frame["trade_date"].isin(set(date_order[max(train_start, valid_end - rebalance_days):valid_end]))]
        if last_known.notna().any():
            fallback_by_asset = predicted.groupby(frame["ts_code"], sort=False).ffill()
            test_idx = frame.index[frame["trade_date"].isin(set(date_order[valid_end:]))]
            predicted.loc[test_idx] = fallback_by_asset.loc[test_idx].where(fallback_by_asset.loc[test_idx].notna(), "均衡域")
    return predicted, {
        "status": "ready" if fits else "unavailable",
        "model": "causal_decision_tree_classifier",
        "feature_count": len(feature_cols),
        "feature_columns": feature_cols,
        "fit_count": len(fits),
        "fits": fits[-8:],
        "label_policy": "历史成熟收益构造训练标签，季度滚动预测下一季度；测试期不重训，只沿用验证末模型/标签路径",
    }


def add_domain_timed_model_features(
    frame: pd.DataFrame,
    *,
    candidate_features: list[str],
    selected_features: list[str],
    base_features: list[str],
    target_col: str,
    date_order: list[str],
    split: dict[str, tuple[int, int]],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    """Create causal domain/timing composite features and gate them by train+valid IC.

    The generated scores are challenger features.  They are estimated from only
    dates before the rebalance checkpoint with an embargo and are frozen through
    the test split.  A feature is appended to the model panel only when its
    train+validation diagnostic clears minimum IC/coverage/date thresholds.
    """

    if not _bool_config(config, "enable_domain_timed_model_features", True):
        return frame, [], {"status": "disabled", "version": DOMAIN_TIMING_VERSION}
    factor_names = list(dict.fromkeys([*selected_features, *candidate_features, *base_features]))
    factor_names = [name for name in factor_names if name in frame.columns and name != target_col]
    feature_limit_raw = config.get("domain_timing_model_feature_limit")
    if feature_limit_raw not in (None, ""):
        feature_limit = _int_config(config, "domain_timing_model_feature_limit", len(factor_names), 1, max(1, len(factor_names)))
        factor_names = factor_names[:feature_limit]
    if not factor_names or target_col not in frame.columns:
        return frame, [], {"status": "unavailable", "version": DOMAIN_TIMING_VERSION, "reason": "missing_factors_or_target"}
    lookback_days = _int_config(config, "domain_timing_lookback_days", 252 * 5, 60, 2520)
    rebalance_days = _int_config(config, "domain_timing_rebalance_days", 63, 20, 252)
    embargo_days = _int_config(config, "domain_timing_embargo_days", 21, 1, 252)
    max_factors = _int_config(config, "domain_timing_max_factors_per_quarter", 30, 1, 120)
    min_assets = _int_config(config, "factor_screen_min_assets_per_date", 30, 5, 500)
    min_domain_assets = _int_config(config, "domain_timing_min_assets_per_domain", 20, 3, 300)
    min_feature_dates = _int_config(config, "domain_timed_feature_min_dates", 20, 3, 252)
    min_feature_coverage = _float_config(config, "domain_timed_feature_min_coverage", 0.35, 0.01, 0.99)
    min_feature_ic = _float_config(config, "domain_timed_feature_min_rank_ic", 0.005, -0.05, 0.10)
    alpha_lambda = _float_config(config, "domain_alpha_momentum_lambda", 0.25, 0.0, 2.0)
    allow_negative_direction = _bool_config(config, "domain_timing_allow_negative_direction", True)
    min_abs_factor_ic = _float_config(config, "domain_timing_min_abs_factor_ic", 0.005, 0.0, 0.10)
    min_abs_factor_icir = _float_config(config, "domain_timing_min_abs_factor_icir", 0.20, 0.0, 20.0)
    max_abs_weight = _float_config(config, "domain_timing_max_abs_factor_weight", 0.12, 0.03, 1.0)
    min_valid_ic = _float_config(config, "domain_timed_feature_min_valid_rank_ic", max(0.0, min_feature_ic), -0.05, 0.10)
    min_valid_icir = _float_config(config, "domain_timed_feature_min_valid_icir", 0.25, -5.0, 20.0)
    min_valid_hit_rate = _float_config(config, "domain_timed_feature_min_valid_hit_rate", 0.52, 0.0, 1.0)
    min_valid_dates = _int_config(config, "domain_timed_feature_min_valid_dates", min_feature_dates, 3, 252)
    min_valid_spread = _float_config(config, "domain_timed_feature_min_valid_top_bottom_spread", 0.0, -0.10, 0.10)
    min_valid_spread_hit_rate = _float_config(config, "domain_timed_feature_min_valid_spread_hit_rate", 0.52, 0.0, 1.0)
    min_valid_to_train_ic_ratio = _float_config(config, "domain_timed_feature_min_valid_to_train_ic_ratio", 0.25, 0.0, 2.0)
    seed = _int_config(config, "domain_timing_seed", 20260818, 1, 2_000_000_000)
    cache_root = _domain_timing_cache_root(config)
    feature_cache_path: Path | None = None
    feature_cache_key = ""
    if cache_root is not None:
        feature_cache_payload = {
            "kind": "domain_timed_model_features",
            "version": DOMAIN_TIMING_VERSION,
            "frame": _frame_fingerprint(frame, date_order),
            "database": _database_fingerprint(config),
            "split": split,
            "target_col": target_col,
            "factor_names": factor_names,
            "config": {
                key: config.get(key)
                for key in [
                    "domain_timing_model_feature_limit", "domain_timing_lookback_days", "domain_timing_rebalance_days",
                    "domain_timing_embargo_days", "domain_timing_max_factors_per_quarter", "factor_screen_min_assets_per_date",
                    "domain_timing_min_assets_per_domain", "domain_timed_feature_min_dates", "domain_timed_feature_min_coverage",
                    "domain_timed_feature_min_rank_ic", "domain_alpha_momentum_lambda", "domain_timing_allow_negative_direction",
                    "domain_timing_seed", "enable_supervised_domain_classifier", "supervised_domain_sample_cap",
                    "domain_timing_min_abs_factor_ic", "domain_timing_min_abs_factor_icir", "domain_timing_max_abs_factor_weight",
                    "domain_timed_feature_min_valid_rank_ic", "domain_timed_feature_min_valid_icir", "domain_timed_feature_min_valid_hit_rate",
                    "domain_timed_feature_min_valid_dates", "domain_timed_feature_min_valid_top_bottom_spread",
                    "domain_timed_feature_min_valid_spread_hit_rate", "domain_timed_feature_min_valid_to_train_ic_ratio",
                    "domain_timed_feature_gate",
                ]
            },
        }
        feature_cache_key = _cache_key(feature_cache_payload)
        feature_cache_path = cache_root / f"domain_model_features_{feature_cache_key}.pkl"
        cached_feature_payload = _read_feature_cache(feature_cache_path)
        if cached_feature_payload is not None:
            cached_features = cached_feature_payload.get("features")
            cached_report = cached_feature_payload.get("report")
            if isinstance(cached_features, pd.DataFrame) and isinstance(cached_report, dict) and len(cached_features) == len(frame):
                output = frame.copy()
                for column in cached_features.columns:
                    output[column] = cached_features[column].to_numpy()
                cached_report = dict(cached_report)
                cached_report["cache"] = {"status": "hit", "kind": "domain_model_features", "key": feature_cache_key, "path": str(feature_cache_path)}
                return output, list(cached_report.get("accepted_features") or []), cached_report
    normalized = _normalized_exposures(frame, factor_names)
    output = frame.copy()
    train_start = split["train"][0]
    valid_end = split["valid"][1]
    checkpoints = list(range(max(train_start + max(60, min(lookback_days, 252)), split["train"][1]), valid_end + 1, max(1, rebalance_days)))
    if valid_end not in checkpoints:
        checkpoints.append(valid_end)
    if not checkpoints:
        return output, [], {"status": "unavailable", "version": DOMAIN_TIMING_VERSION, "reason": "no_rebalance_checkpoints"}
    scheme_labels = build_domain_labels(output, target_col=target_col)
    supervised_report: dict[str, Any] = {"status": "not_requested"}
    if _bool_config(config, "enable_supervised_domain_classifier", True):
        supervised, supervised_report = _causal_supervised_pricing_domains(
            output,
            target_col=target_col,
            date_order=date_order,
            split=split,
            rebalance_days=rebalance_days,
            lookback_days=max(60, min(lookback_days, 252 * 3)),
            embargo_days=embargo_days,
            sample_cap=_int_config(config, "supervised_domain_sample_cap", 30000, 1000, 300000),
            seed=seed,
        )
        if supervised is not None and supervised.notna().any():
            scheme_labels["supervised_pricing_predicted"] = supervised
    scheme_order = ["industry", "size", "style", "supervised_pricing_predicted"]
    scheme_order = [name for name in scheme_order if name in scheme_labels]
    feature_columns = ["factor_timing_global_icir_v1", *[f"factor_domain_{name}_timed_icir_v1" for name in scheme_order]]
    for column in feature_columns:
        output[column] = np.nan
    ledger: list[dict[str, Any]] = []
    for pos, checkpoint in enumerate(checkpoints):
        train_end = max(train_start + 1, checkpoint - embargo_days)
        train_begin = max(train_start, train_end - lookback_days)
        train_dates = set(date_order[train_begin:train_end])
        next_checkpoint = checkpoints[pos + 1] if pos + 1 < len(checkpoints) else len(date_order)
        if checkpoint >= valid_end:
            next_checkpoint = len(date_order)  # freeze validation-end weights through report-only test dates
        predict_dates = set(date_order[checkpoint:next_checkpoint])
        if not train_dates or not predict_dates:
            continue
        pred_rows = output.index[output["trade_date"].isin(predict_dates)]
        global_coeffs = _factor_coefficients(
            output,
            factor_names=factor_names,
            target_col=target_col,
            dates=train_dates,
            min_assets_per_date=min_assets,
            max_factors=max_factors,
            allow_negative_direction=allow_negative_direction,
            min_abs_mean_ic=min_abs_factor_ic,
            min_abs_icir=min_abs_factor_icir,
            max_abs_weight=max_abs_weight,
        )
        output.loc[pred_rows, "factor_timing_global_icir_v1"] = _apply_coefficients(normalized, pred_rows, global_coeffs)
        domain_summaries: dict[str, Any] = {}
        for scheme_name in scheme_order:
            labels = scheme_labels[scheme_name]
            feature_name = f"factor_domain_{scheme_name}_timed_icir_v1"
            domain_values = labels.loc[pred_rows].dropna().astype(str).unique().tolist()
            alpha_map = _domain_alpha_momentum(
                output,
                domain=labels,
                target_col=target_col,
                dates=train_dates,
                min_observations=max(50, min_domain_assets * 3),
            )
            domain_summaries[scheme_name] = {"domain_count": len(domain_values), "alpha_domains": len(alpha_map), "top_domains": []}
            for domain_value in domain_values:
                train_domain_dates = train_dates
                domain_train_mask = output["trade_date"].isin(train_domain_dates) & (labels.astype(str) == str(domain_value))
                if "model_eligible" in output.columns:
                    domain_train_mask = domain_train_mask & output["model_eligible"].astype(bool)
                date_counts = output.loc[domain_train_mask].groupby("trade_date", sort=False).size()
                usable_dates = set(date_counts[date_counts >= min_domain_assets].index.astype(str).tolist())
                coeffs = _factor_coefficients(
                    output.loc[domain_train_mask | output["trade_date"].isin(predict_dates)],
                    factor_names=factor_names,
                    target_col=target_col,
                    dates=usable_dates,
                    min_assets_per_date=min_domain_assets,
                    max_factors=max(3, max_factors // 2),
                    allow_negative_direction=allow_negative_direction,
                    min_abs_mean_ic=min_abs_factor_ic,
                    min_abs_icir=min_abs_factor_icir,
                    max_abs_weight=max_abs_weight,
                )
                if not coeffs:
                    coeffs = global_coeffs
                rows = pred_rows[labels.loc[pred_rows].astype(str) == str(domain_value)]
                if len(rows) == 0:
                    continue
                score = _apply_coefficients(normalized, rows, coeffs)
                if alpha_lambda and alpha_map:
                    score = score + alpha_lambda * float(alpha_map.get(str(domain_value), 0.0))
                output.loc[rows, feature_name] = score
                domain_summaries[scheme_name]["top_domains"].append({
                    "domain": str(domain_value),
                    "factor_count": len(coeffs),
                    "alpha_momentum_z": float(alpha_map.get(str(domain_value), 0.0)),
                    "top_factors": list(coeffs)[:8],
                })
        ledger.append({
            "as_of_date": date_order[checkpoint - 1] if checkpoint > 0 else "",
            "train_start_date": date_order[train_begin] if train_begin < len(date_order) else "",
            "train_end_date": date_order[train_end - 1] if train_end > train_begin else "",
            "predict_start_date": date_order[checkpoint] if checkpoint < len(date_order) else "",
            "predict_end_date": date_order[next_checkpoint - 1] if next_checkpoint > checkpoint and next_checkpoint - 1 < len(date_order) else "",
            "global_factor_count": len(global_coeffs),
            "global_top_factors": list(global_coeffs)[:10],
            "domain_summaries": domain_summaries,
            "test_period_frozen": bool(checkpoint >= valid_end),
        })
        if checkpoint >= valid_end:
            break
    selection_dates, window = _selection_date_window(date_order, split, lookback_days=max(60, min(lookback_days, 252 * 5)))
    train_gate_dates = set(date_order[split["train"][0]:split["train"][1]])
    valid_gate_dates = set(date_order[split["valid"][0]:split["valid"][1]])
    metrics = [
        _composite_metrics(output, feature_name=column, target_col=target_col, dates=selection_dates, min_assets_per_date=min_assets)
        for column in feature_columns
    ]
    train_metrics = [
        _composite_metrics(output, feature_name=column, target_col=target_col, dates=train_gate_dates, min_assets_per_date=min_assets)
        for column in feature_columns
    ]
    valid_metrics = [
        _composite_metrics(output, feature_name=column, target_col=target_col, dates=valid_gate_dates, min_assets_per_date=min_assets)
        for column in feature_columns
    ]
    valid_spreads = [
        _composite_spread_metrics(output, feature_name=column, target_col=target_col, dates=valid_gate_dates, min_assets_per_date=min_assets)
        for column in feature_columns
    ]
    train_by_name = {row["factor_name"]: row for row in train_metrics}
    valid_by_name = {row["factor_name"]: row for row in valid_metrics}
    spread_by_name = {row["factor_name"]: row for row in valid_spreads}
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    for row in metrics:
        name = row["factor_name"]
        train_row = train_by_name.get(name, {})
        valid_row = valid_by_name.get(name, {})
        spread_row = spread_by_name.get(name, {})
        reasons: list[str] = []
        if row["coverage"] < min_feature_coverage:
            reasons.append("coverage_below_gate")
        if row["daily_ic_count"] < min_feature_dates:
            reasons.append("insufficient_train_valid_ic_dates")
        if row["mean_rank_ic"] < min_feature_ic:
            reasons.append("train_valid_rank_ic_below_gate")
        if valid_row.get("daily_ic_count", 0) < min_valid_dates:
            reasons.append("insufficient_validation_ic_dates")
        if valid_row.get("mean_rank_ic", 0.0) < min_valid_ic:
            reasons.append("validation_rank_ic_below_gate")
        if valid_row.get("icir", 0.0) < min_valid_icir:
            reasons.append("validation_icir_below_gate")
        if valid_row.get("hit_rate", 0.0) < min_valid_hit_rate:
            reasons.append("validation_hit_rate_below_gate")
        if spread_row.get("spread_count", 0) < min_valid_dates:
            reasons.append("insufficient_validation_spread_dates")
        if spread_row.get("mean_top_bottom_spread", 0.0) < min_valid_spread:
            reasons.append("validation_top_bottom_spread_below_gate")
        if spread_row.get("spread_hit_rate", 0.0) < min_valid_spread_hit_rate:
            reasons.append("validation_spread_hit_rate_below_gate")
        train_ic = float(train_row.get("mean_rank_ic", 0.0))
        valid_ic = float(valid_row.get("mean_rank_ic", 0.0))
        if train_ic > max(min_feature_ic, 1e-12) and valid_ic < train_ic * min_valid_to_train_ic_ratio:
            reasons.append("validation_train_ic_decay_too_large")
        if reasons:
            rejected.append({
                "factor_name": name,
                "reasons": reasons,
                "train_valid": row,
                "train": train_row,
                "valid": valid_row,
                "valid_spread": spread_row,
            })
        else:
            accepted.append(name)
    if not _bool_config(config, "domain_timed_feature_gate", True):
        accepted = [row["factor_name"] for row in metrics if row["coverage"] > 0]
        rejected = []
    report = {
        "status": "ready",
        "version": DOMAIN_TIMING_VERSION,
        "created_feature_count": len(feature_columns),
        "accepted_feature_count": len(accepted),
        "accepted_features": accepted,
        "rejected_features": rejected,
        "metrics": metrics,
        "train_metrics": train_metrics,
        "valid_metrics": valid_metrics,
        "valid_spread_metrics": valid_spreads,
        "ledger": ledger[-8:],
        "supervised_domain_classifier": supervised_report,
        "window": window,
        "gate": {
            "min_rank_ic": min_feature_ic,
            "min_valid_rank_ic": min_valid_ic,
            "min_valid_icir": min_valid_icir,
            "min_valid_hit_rate": min_valid_hit_rate,
            "min_valid_dates": min_valid_dates,
            "min_valid_top_bottom_spread": min_valid_spread,
            "min_valid_spread_hit_rate": min_valid_spread_hit_rate,
            "min_valid_to_train_ic_ratio": min_valid_to_train_ic_ratio,
            "min_dates": min_feature_dates,
            "min_coverage": min_feature_coverage,
            "max_abs_factor_weight": max_abs_weight,
            "min_abs_factor_ic": min_abs_factor_ic,
            "min_abs_factor_icir": min_abs_factor_icir,
            "policy": "分域/择时复合因子必须训练验证整体有效、验证期IC/ICIR/胜率有效，且验证期Top-Bottom扩散为正后才进入模型；测试期权重冻结，不参与择时",
        },
        "test_usage": "report_only_scores_use_validation_end_frozen_weights",
    }
    if feature_cache_path is not None:
        report["cache"] = {"status": "miss_written", "kind": "domain_model_features", "key": feature_cache_key, "path": str(feature_cache_path)}
        _write_feature_cache(feature_cache_path, {"features": output[feature_columns].copy(), "report": report})
    return output, accepted, report
