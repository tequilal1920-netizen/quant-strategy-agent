"""Unified factor exposure loading and purged rolling screening.

This module deliberately stays read-only: it can merge materialized factor
exposures from the research warehouse into the core date-stock panel, then
select a compact factor universe using only train/validation dates supplied by
the caller.  Test dates are never read for selection.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    from domain_factor_timing import add_domain_timed_model_features, build_domain_factor_timing_report
except ImportError:  # pragma: no cover - package import path
    from model.factor_laboratory.domain_factor_timing import add_domain_timed_model_features, build_domain_factor_timing_report

CORE_29_MODE = "core_29"
SCREENED_FULL_MODE = "screened_full"
WAREHOUSE_SCREENED_MODE = "warehouse_screened"


@dataclass(frozen=True)
class FactorScreenSelection:
    selected_features: list[str]
    diagnostics: dict[str, Any]


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


def normalized_factor_universe_mode(config: dict[str, Any]) -> str:
    raw = str(config.get("factor_universe_mode") or "").strip().lower()
    if raw in {"full", "all", "unified", "screened", "screened_full"}:
        return SCREENED_FULL_MODE
    if raw in {"warehouse", "warehouse_screened", "factor_value_daily"}:
        return WAREHOUSE_SCREENED_MODE
    if bool(config.get("use_unified_factor_panel")):
        return SCREENED_FULL_MODE
    return CORE_29_MODE


def _sql_placeholders(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def warehouse_factor_statistics(
    database_path: str | Path,
    *,
    start_date: str,
    end_date: str,
    max_candidates: int,
    assets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return materialized factor coverage stats from factor_value_daily."""

    path = Path(database_path)
    if not path.exists():
        return []
    conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "select count(*) from sqlite_master where type='table' and name='factor_value_daily'"
        ).fetchone()[0]
        if not exists:
            return []
        columns = {row[1] for row in conn.execute("pragma table_info(factor_value_daily)").fetchall()}
        required = {"trade_date", "ts_code", "factor_name", "factor_value"}
        if not required.issubset(columns):
            return []
        params: list[Any] = [str(start_date), str(end_date)]
        asset_filter = ""
        if assets:
            asset_filter = f" and ts_code in ({_sql_placeholders(assets)})"
            params.extend(assets)
        params.append(int(max_candidates))
        rows = conn.execute(
            f"""
            select factor_name,
                   count(*) as value_count,
                   count(distinct trade_date) as date_count,
                   count(distinct ts_code) as asset_count,
                   min(trade_date) as first_date,
                   max(trade_date) as last_date
            from factor_value_daily
            where trade_date between ? and ?
              and factor_value is not null
              {asset_filter}
            group by factor_name
            order by value_count desc, factor_name asc
            limit ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_warehouse_factor_matrix(
    database_path: str | Path,
    *,
    start_date: str,
    end_date: str,
    assets: list[str],
    factor_names: list[str],
) -> pd.DataFrame:
    """Load selected factor_value_daily exposures as a wide date-stock frame."""

    path = Path(database_path)
    if not path.exists() or not factor_names or not assets:
        return pd.DataFrame(columns=["trade_date", "ts_code"])
    chunks: list[pd.DataFrame] = []
    max_sql_params = 900
    asset_placeholders = _sql_placeholders(assets)
    factor_chunk_size = max(1, max_sql_params - len(assets) - 2)
    conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=60)
    try:
        for start in range(0, len(factor_names), factor_chunk_size):
            names = factor_names[start:start + factor_chunk_size]
            factor_placeholders = _sql_placeholders(names)
            sql = f"""
                select trade_date, ts_code, factor_name, factor_value
                from factor_value_daily
                where trade_date between ? and ?
                  and ts_code in ({asset_placeholders})
                  and factor_name in ({factor_placeholders})
                  and factor_value is not null
            """
            params = [str(start_date), str(end_date), *assets, *names]
            long = pd.read_sql_query(sql, conn, params=params)
            if long.empty:
                continue
            wide = (
                long.pivot_table(
                    index=["trade_date", "ts_code"],
                    columns="factor_name",
                    values="factor_value",
                    aggfunc="last",
                )
                .reset_index()
            )
            wide.columns.name = None
            chunks.append(wide)
    finally:
        conn.close()
    if not chunks:
        return pd.DataFrame(columns=["trade_date", "ts_code"])
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged.merge(chunk, how="outer", on=["trade_date", "ts_code"])
    merged["trade_date"] = merged["trade_date"].astype(str)
    merged["ts_code"] = merged["ts_code"].astype(str)
    return merged


def _selection_date_window(
    date_order: list[str],
    split: dict[str, tuple[int, int]],
    *,
    lookback_days: int,
    as_of_index: int | None = None,
) -> tuple[list[str], dict[str, Any]]:
    train_start, train_end = split["train"]
    _, valid_end = split["valid"]
    end_index = min(valid_end, len(date_order))
    if as_of_index is not None:
        end_index = min(end_index, max(train_start + 1, int(as_of_index)))
    start_index = max(train_start, end_index - max(20, int(lookback_days)))
    return date_order[start_index:end_index], {
        "selection_scope": "train_plus_validation_only",
        "selection_start_date": date_order[start_index] if start_index < len(date_order) else "",
        "selection_end_date": date_order[end_index - 1] if end_index > start_index else "",
        "selection_start_index": int(start_index),
        "selection_end_index_exclusive": int(end_index),
        "train_end_index": int(train_end),
        "valid_end_index_exclusive": int(valid_end),
    }


def _daily_rank_ic(
    frame: pd.DataFrame,
    *,
    factor: str,
    target_col: str,
    dates: set[str],
    min_assets_per_date: int,
) -> list[float]:
    columns = ["trade_date", factor, target_col]
    if "model_eligible" in frame.columns:
        columns.append("model_eligible")
    work = frame.loc[frame["trade_date"].isin(dates), columns].copy()
    if "model_eligible" in work.columns:
        work = work.loc[work["model_eligible"].astype(bool)]
    work[factor] = pd.to_numeric(work[factor], errors="coerce")
    work[target_col] = pd.to_numeric(work[target_col], errors="coerce")
    work = work.replace([np.inf, -np.inf], np.nan).dropna(subset=[factor, target_col])
    if work.empty:
        return []
    ics: list[float] = []
    for _, group in work.groupby("trade_date", sort=False):
        if len(group) < min_assets_per_date:
            continue
        x = group[factor].rank(pct=True, method="average")
        y = group[target_col].rank(pct=True, method="average")
        corr = x.corr(y)
        if corr is not None and math.isfinite(float(corr)):
            ics.append(float(corr))
    return ics


def _factor_metrics(
    frame: pd.DataFrame,
    *,
    factor: str,
    target_col: str,
    dates: set[str],
    min_assets_per_date: int,
) -> dict[str, Any]:
    base = frame.loc[frame["trade_date"].isin(dates), ["trade_date", target_col]].copy()
    if "model_eligible" in frame.columns:
        base["model_eligible"] = frame.loc[base.index, "model_eligible"].astype(bool)
        base = base.loc[base["model_eligible"]]
    base[target_col] = pd.to_numeric(base[target_col], errors="coerce")
    base = base.replace([np.inf, -np.inf], np.nan).dropna(subset=[target_col])
    eligible_observations = int(len(base))
    factor_values = pd.to_numeric(frame.loc[base.index, factor], errors="coerce") if eligible_observations else pd.Series(dtype=float)
    coverage = float(factor_values.replace([np.inf, -np.inf], np.nan).notna().mean()) if eligible_observations else 0.0
    ics = _daily_rank_ic(
        frame,
        factor=factor,
        target_col=target_col,
        dates=dates,
        min_assets_per_date=min_assets_per_date,
    )
    if not ics:
        return {
            "factor_name": factor,
            "coverage": coverage,
            "daily_ic_count": 0,
            "mean_rank_ic": 0.0,
            "icir": 0.0,
            "hit_rate": 0.0,
            "selection_score": 0.0,
            "eligible_observations": eligible_observations,
        }
    values = np.asarray(ics, dtype=float)
    mean_ic = float(np.mean(values))
    std_ic = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    icir = mean_ic / std_ic * math.sqrt(len(values)) if std_ic > 1e-12 else 0.0
    sign = 1.0 if mean_ic >= 0 else -1.0
    hit_rate = float(np.mean(np.sign(values) == sign))
    stability = abs(hit_rate - 0.5) * 2.0
    coverage_weight = min(1.0, coverage / 0.80)
    score = abs(mean_ic) * math.sqrt(len(values)) * coverage_weight * (0.75 + 0.25 * stability)
    return {
        "factor_name": factor,
        "coverage": coverage,
        "daily_ic_count": int(len(values)),
        "mean_rank_ic": mean_ic,
        "icir": icir,
        "hit_rate": hit_rate,
        "selection_score": float(score),
        "eligible_observations": eligible_observations,
        "direction": 1 if mean_ic >= 0 else -1,
    }


def _redundancy_filter(
    frame: pd.DataFrame,
    *,
    ordered_metrics: list[dict[str, Any]],
    target_count: int,
    dates: set[str],
    max_pair_corr: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    selected: list[str] = []
    rejected: list[dict[str, Any]] = []
    scope = frame.loc[frame["trade_date"].isin(dates)]
    for metric in ordered_metrics:
        name = str(metric["factor_name"])
        if len(selected) >= target_count:
            break
        too_close_to = None
        for incumbent in selected:
            pair = scope[[name, incumbent]].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if len(pair) < 30:
                continue
            corr = pair[name].rank(pct=True).corr(pair[incumbent].rank(pct=True))
            if corr is not None and math.isfinite(float(corr)) and abs(float(corr)) >= max_pair_corr:
                too_close_to = {"factor_name": incumbent, "rank_corr": float(corr)}
                break
        if too_close_to:
            rejected.append({"factor_name": name, "reason": "redundant", "matched": too_close_to})
            continue
        selected.append(name)
    return selected, rejected


def screen_factor_frame(
    frame: pd.DataFrame,
    *,
    candidate_features: list[str],
    target_col: str,
    date_order: list[str],
    split: dict[str, tuple[int, int]],
    top_n: int,
    lookback_days: int,
    rebalance_days: int,
    min_coverage: float,
    min_dates: int,
    min_assets_per_date: int,
    max_pair_corr: float,
) -> FactorScreenSelection:
    """Select factors using only the caller-provided train/validation window."""

    final_dates, window = _selection_date_window(date_order, split, lookback_days=lookback_days)
    date_set = set(final_dates)
    metrics: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for factor in candidate_features:
        if factor not in frame.columns:
            excluded.append({"factor_name": factor, "reason": "missing_column"})
            continue
        item = _factor_metrics(
            frame,
            factor=factor,
            target_col=target_col,
            dates=date_set,
            min_assets_per_date=min_assets_per_date,
        )
        if item["coverage"] < min_coverage:
            item["reason"] = "low_coverage"
            excluded.append(item)
            continue
        if item["daily_ic_count"] < min_dates:
            item["reason"] = "insufficient_ic_dates"
            excluded.append(item)
            continue
        metrics.append(item)
    ordered = sorted(
        metrics,
        key=lambda row: (
            _finite(row.get("selection_score")),
            abs(_finite(row.get("icir"))),
            abs(_finite(row.get("mean_rank_ic"))),
            _finite(row.get("coverage")),
        ),
        reverse=True,
    )
    selected, redundant = _redundancy_filter(
        frame,
        ordered_metrics=ordered,
        target_count=max(0, int(top_n)),
        dates=date_set,
        max_pair_corr=max_pair_corr,
    )
    quarterly: list[dict[str, Any]] = []
    train_end = split["train"][1]
    valid_end = split["valid"][1]
    checkpoints = list(range(max(train_end, split["train"][0] + lookback_days), valid_end, max(1, rebalance_days)))
    if valid_end not in checkpoints:
        checkpoints.append(valid_end)
    for index in checkpoints[-8:]:
        q_dates, q_window = _selection_date_window(date_order, split, lookback_days=lookback_days, as_of_index=index)
        q_set = set(q_dates)
        q_metrics = []
        for factor in candidate_features:
            if factor not in frame.columns:
                continue
            item = _factor_metrics(
                frame,
                factor=factor,
                target_col=target_col,
                dates=q_set,
                min_assets_per_date=min_assets_per_date,
            )
            if item["coverage"] >= min_coverage and item["daily_ic_count"] >= min_dates:
                q_metrics.append(item)
        q_ordered = sorted(q_metrics, key=lambda row: _finite(row.get("selection_score")), reverse=True)
        q_selected, _ = _redundancy_filter(
            frame,
            ordered_metrics=q_ordered,
            target_count=max(0, int(top_n)),
            dates=q_set,
            max_pair_corr=max_pair_corr,
        )
        quarterly.append({
            "as_of_date": q_window.get("selection_end_date", ""),
            "selected_count": len(q_selected),
            "top_factors": q_selected[:10],
        })
    diagnostics = {
        **window,
        "candidate_count": len(candidate_features),
        "eligible_metric_count": len(metrics),
        "selected_count": len(selected),
        "selected_features": selected,
        "top_metrics": ordered[: min(25, len(ordered))],
        "excluded_count": len(excluded),
        "excluded_sample": excluded[: min(25, len(excluded))],
        "redundant_rejections": redundant[: min(25, len(redundant))],
        "quarterly_pools": quarterly,
        "screening_policy": {
            "top_n": int(top_n),
            "lookback_days": int(lookback_days),
            "rebalance_days": int(rebalance_days),
            "min_coverage": float(min_coverage),
            "min_dates": int(min_dates),
            "min_assets_per_date": int(min_assets_per_date),
            "max_pair_corr": float(max_pair_corr),
            "test_usage": "excluded_from_factor_screening",
        },
    }
    return FactorScreenSelection(selected, diagnostics)


def extend_with_screened_factors(
    frame: pd.DataFrame,
    *,
    database_path: str | Path,
    base_features: list[str],
    target_col: str,
    date_order: list[str],
    assets: list[str],
    split: dict[str, tuple[int, int]],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    """Merge and screen materialized external factors for the strategy panel."""

    mode = normalized_factor_universe_mode(config)
    if mode == CORE_29_MODE:
        return frame, list(base_features), {
            "mode": CORE_29_MODE,
            "enabled": False,
            "feature_count": len(base_features),
            "test_usage": "not_applicable",
        }
    max_candidates = _int_config(config, "max_factor_candidates", 180, 1, 600)
    stats = warehouse_factor_statistics(
        database_path,
        start_date=date_order[0],
        end_date=date_order[-1],
        max_candidates=max_candidates,
        assets=assets,
    )
    base_set = set(base_features)
    candidate_names = [
        str(row["factor_name"])
        for row in stats
        if str(row.get("factor_name") or "") and str(row.get("factor_name")) not in base_set
    ]
    explicit = config.get("factor_candidate_names")
    if isinstance(explicit, list) and explicit:
        explicit_set = {str(item) for item in explicit if str(item)}
        candidate_names = [name for name in candidate_names if name in explicit_set]
    if not candidate_names:
        return frame, list(base_features), {
            "mode": mode,
            "enabled": True,
            "warehouse_candidate_count": 0,
            "merged_factor_count": 0,
            "selected_count": 0,
            "feature_count": len(base_features),
            "message": "no_materialized_external_factors_available",
            "test_usage": "excluded_from_factor_screening",
        }
    wide = load_warehouse_factor_matrix(
        database_path,
        start_date=date_order[0],
        end_date=date_order[-1],
        assets=assets,
        factor_names=candidate_names,
    )
    if wide.empty:
        return frame, list(base_features), {
            "mode": mode,
            "enabled": True,
            "warehouse_candidate_count": len(candidate_names),
            "merged_factor_count": 0,
            "selected_count": 0,
            "feature_count": len(base_features),
            "message": "external_factor_values_not_aligned_to_panel_assets",
            "test_usage": "excluded_from_factor_screening",
        }
    merged = frame.merge(wide, how="left", on=["trade_date", "ts_code"], validate="many_to_one")
    merged_features = [name for name in candidate_names if name in merged.columns]
    max_staleness_days = _int_config(config, "external_factor_max_staleness_days", 63, 0, 252)
    if merged_features and max_staleness_days > 0:
        merged = merged.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        merged[merged_features] = (
            merged.groupby("ts_code", sort=False)[merged_features]
            .ffill(limit=max_staleness_days)
        )
    safe_external_limit = max(0, int(len(assets) * 0.60) - len(base_features))
    requested_top_n = _int_config(config, "factor_screen_top_n", 60, 0, 240)
    top_n = min(requested_top_n, safe_external_limit)
    screen = screen_factor_frame(
        merged,
        candidate_features=merged_features,
        target_col=target_col,
        date_order=date_order,
        split=split,
        top_n=top_n,
        lookback_days=_int_config(config, "factor_screen_lookback_days", 252, 40, 756),
        rebalance_days=_int_config(config, "factor_screen_rebalance_days", 63, 20, 252),
        min_coverage=_float_config(config, "factor_screen_min_coverage", 0.35, 0.01, 0.99),
        min_dates=_int_config(config, "factor_screen_min_dates", 20, 5, 252),
        min_assets_per_date=_int_config(config, "factor_screen_min_assets_per_date", 30, 5, 500),
        max_pair_corr=_float_config(config, "factor_screen_max_pair_corr", 0.92, 0.50, 0.999),
    )
    selected = screen.selected_features
    domain_timing = build_domain_factor_timing_report(
        merged,
        candidate_features=merged_features,
        selected_features=selected,
        base_features=base_features,
        target_col=target_col,
        date_order=date_order,
        split=split,
        config=config,
    )
    merged, domain_timed_features, domain_timed_report = add_domain_timed_model_features(
        merged,
        candidate_features=merged_features,
        selected_features=selected,
        base_features=base_features,
        target_col=target_col,
        date_order=date_order,
        split=split,
        config=config,
    )
    if isinstance(domain_timing, dict):
        domain_timing["model_features"] = domain_timed_report
    final_features = [*base_features, *selected, *domain_timed_features]
    return merged, final_features, {
        "mode": mode,
        "enabled": True,
        "warehouse_candidate_count": len(candidate_names),
        "merged_factor_count": len(merged_features),
        "selected_count": len(selected),
        "feature_count": len(final_features),
        "base_feature_count": len(base_features),
        "requested_top_n": requested_top_n,
        "asset_safe_top_n": safe_external_limit,
        "selected_external_features": selected,
        "selected_domain_timed_features": domain_timed_features,
        "screen": screen.diagnostics,
        "domain_factor_timing": domain_timing,
        "source_table": "factor_value_daily",
        "external_factor_fill_policy": "per_stock_forward_fill_from_last_observed_exposure",
        "external_factor_max_staleness_days": max_staleness_days,
        "include_subject_parquet": bool(config.get("include_subject_parquet", False)),
        "subject_parquet_policy": "cataloged_only_unless_explicit_selected_loader_is_enabled",
        "test_usage": "excluded_from_factor_screening",
    }
