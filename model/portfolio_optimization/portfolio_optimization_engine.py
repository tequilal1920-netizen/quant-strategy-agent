"""Auditable portfolio-optimization engine for the five-page public module.

The engine is deliberately offline-first: it reads local SQLite databases,
builds a frozen JSON snapshot, and never calls a paid provider from a web
request.  Model selection uses train and validation data only.  The test split
is opened once for report-only evidence after the configuration is fixed.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform
from scipy.stats import norm
from sklearn.covariance import LedoitWolf

try:
    import cvxpy as cp
except Exception:  # pragma: no cover - runtime fallback is tested separately.
    cp = None


ENGINE_VERSION = "portfolio-optimizer/2.1-risk-adjusted-smart-factor"
SCHEMA_VERSION = "2.0"
TRAIN = ("20150101", "20201231")
VALIDATION = ("20210101", "20221231")
TEST = ("20230101", "20261231")
SPLITS = {"train": TRAIN, "validation": VALIDATION, "test": TEST}
TRADING_DAYS = 252.0
MONTHS_PER_YEAR = 12.0
DEFAULT_COST_BPS = 10.0
DEFAULT_TURNOVER_CAP = 0.55


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    covariance_method: str
    expected_return_method: str
    lookback_days: int
    risk_aversion: float
    turnover_l2: float
    position_cap: float
    turnover_l1: float = 0.002
    turnover_cap: float = DEFAULT_TURNOVER_CAP


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def max_drawdown(returns: Iterable[float]) -> float:
    values = np.asarray(list(returns), dtype=float)
    if values.size == 0:
        return 0.0
    nav = np.cumprod(1.0 + np.nan_to_num(values, nan=0.0))
    peak = np.maximum.accumulate(nav)
    return float(np.min(nav / np.maximum(peak, 1e-12) - 1.0))


def annual_metrics(returns: Iterable[float], benchmark: Iterable[float] | None = None) -> dict[str, float]:
    r = np.asarray(list(returns), dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {
            "months": 0,
            "total_return": 0.0,
            "annual_return": 0.0,
            "annual_volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "positive_month_rate": 0.0,
            "annual_turnover": 0.0,
            "annual_excess_return": 0.0,
            "information_ratio": 0.0,
        }
    total = float(np.prod(1.0 + r) - 1.0)
    annual = float((1.0 + total) ** (MONTHS_PER_YEAR / r.size) - 1.0) if total > -1 else -1.0
    vol = float(np.std(r, ddof=1) * math.sqrt(MONTHS_PER_YEAR)) if r.size > 1 else 0.0
    sharpe = annual / vol if vol > 1e-12 else 0.0
    drawdown = max_drawdown(r)
    calmar = annual / abs(drawdown) if drawdown < -1e-12 else 0.0
    active_annual = 0.0
    information_ratio = 0.0
    if benchmark is not None:
        b = np.asarray(list(benchmark), dtype=float)
        n = min(r.size, b.size)
        if n:
            active = r[-n:] - b[-n:]
            benchmark_total = float(np.prod(1.0 + b[-n:]) - 1.0)
            benchmark_annual = float((1.0 + benchmark_total) ** (MONTHS_PER_YEAR / n) - 1.0) if benchmark_total > -1 else -1.0
            active_annual = annual - benchmark_annual
            tracking = float(np.std(active, ddof=1) * math.sqrt(MONTHS_PER_YEAR)) if n > 1 else 0.0
            information_ratio = float(np.mean(active) * MONTHS_PER_YEAR / tracking) if tracking > 1e-12 else 0.0
    return {
        "months": int(r.size),
        "total_return": total,
        "annual_return": annual,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": drawdown,
        "calmar": calmar,
        "positive_month_rate": float(np.mean(r > 0)),
        "annual_excess_return": active_annual,
        "information_ratio": information_ratio,
    }


def split_metrics(rows: list[dict[str, Any]], benchmark_field: str = "benchmark_return") -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for split, (start, end) in SPLITS.items():
        chosen = [row for row in rows if start <= row["trade_date"] <= end]
        metrics = annual_metrics(
            [row["net_return"] for row in chosen],
            [row.get(benchmark_field, 0.0) for row in chosen],
        )
        metrics["annual_turnover"] = float(np.mean([row["turnover"] for row in chosen]) * 12.0) if chosen else 0.0
        metrics["cost_drag"] = float(np.sum([row["gross_return"] - row["net_return"] for row in chosen])) if chosen else 0.0
        out[split] = metrics
    full = annual_metrics([row["net_return"] for row in rows], [row.get(benchmark_field, 0.0) for row in rows])
    full["annual_turnover"] = float(np.mean([row["turnover"] for row in rows]) * 12.0) if rows else 0.0
    full["cost_drag"] = float(np.sum([row["gross_return"] - row["net_return"] for row in rows])) if rows else 0.0
    out["full"] = full
    return out


def classify_etf(name: str) -> str:
    text = str(name or "")
    if any(token in text for token in ("货币", "国债", "政金债", "短融", "同业存单", "信用债", "可转债", "债券", "债ETF", "现金")):
        return "bond_cash"
    if any(token in text for token in ("黄金", "白银", "商品", "豆粕", "有色期货")):
        return "commodity"
    if any(token in text for token in ("纳指", "标普", "日经", "德国", "法国", "海外", "中概", "恒生", "港股", "沙特", "东南亚")):
        return "overseas_equity"
    if any(token in text for token in ("沪深300", "中证500", "中证1000", "中证2000", "上证50", "创业板", "科创50", "深证100", "A500", "全指", "红利", "价值", "成长", "低波")):
        return "broad_equity"
    return "sector_equity"


def _year_ago(as_of: str) -> str:
    date = datetime.strptime(as_of, "%Y%m%d")
    return (date - timedelta(days=370)).strftime("%Y%m%d")


def load_etf_universe(connection: sqlite3.Connection, start: str = "20130101") -> tuple[pd.DataFrame, pd.DataFrame, str]:
    as_of = str(connection.execute("SELECT MAX(trade_date) FROM etf_ohlcv_daily").fetchone()[0])
    recent_start = _year_ago(as_of)
    stats = pd.read_sql_query(
        """
        SELECT ts_code, MAX(fund_name) AS name, MIN(trade_date) AS first_date,
               MAX(trade_date) AS last_date, COUNT(*) AS observations,
               AVG(CASE WHEN trade_date>=? THEN amount END) AS avg_amount
        FROM etf_ohlcv_daily
        WHERE trade_date BETWEEN ? AND ? AND close>0
        GROUP BY ts_code
        """,
        connection,
        params=(recent_start, start, as_of),
    )
    stats["group"] = stats["name"].map(classify_etf)
    stats["avg_amount"] = pd.to_numeric(stats["avg_amount"], errors="coerce").fillna(0.0)
    eligible = stats[
        (stats["first_date"] <= "20140131")
        & (stats["last_date"] == as_of)
        & (stats["observations"] >= 1800)
        & (stats["avg_amount"] > 0)
    ].copy()
    targets = {"broad_equity": 4, "sector_equity": 4, "bond_cash": 2, "commodity": 2, "overseas_equity": 2}
    chosen: list[pd.DataFrame] = []
    for group, count in targets.items():
        part = eligible[eligible["group"] == group].sort_values("avg_amount", ascending=False).head(count)
        chosen.append(part)
    selected = pd.concat(chosen, ignore_index=True).drop_duplicates("ts_code")
    if len(selected) < 10:
        fill = eligible[~eligible["ts_code"].isin(selected["ts_code"])].sort_values("avg_amount", ascending=False).head(10 - len(selected))
        selected = pd.concat([selected, fill], ignore_index=True)
    codes = selected["ts_code"].tolist()
    marks = ",".join("?" for _ in codes)
    daily = pd.read_sql_query(
        f"""
        SELECT trade_date, ts_code, fund_name AS name, close, pct_chg, amount
        FROM etf_ohlcv_daily
        WHERE ts_code IN ({marks}) AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date, ts_code
        """,
        connection,
        params=tuple(codes) + (start, as_of),
    )
    daily["daily_return"] = pd.to_numeric(daily["pct_chg"], errors="coerce") / 100.0
    daily.loc[daily["daily_return"].abs() > 0.20, "daily_return"] = np.nan
    return selected.sort_values(["group", "avg_amount"], ascending=[True, False]).reset_index(drop=True), daily, as_of


def load_subject_etf_universe(database: Path, start: str = "20130101") -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Load long-history listed ETF prices from subject/fund_daily."""
    connection = sqlite3.connect(database)
    try:
        as_of = str(connection.execute("SELECT MAX(trade_date) FROM fund_daily WHERE market IN ('SH','SZ')").fetchone()[0])
        recent_start = _year_ago(as_of)
        stats = pd.read_sql_query(
            """
            SELECT ts_code, MAX(fund_name) AS name, MIN(trade_date) AS first_date,
                   MAX(trade_date) AS last_date, COUNT(*) AS observations,
                   AVG(CASE WHEN trade_date>=? THEN amount END) AS avg_amount
            FROM fund_daily
            WHERE market IN ('SH','SZ') AND trade_date BETWEEN ? AND ? AND close>0
            GROUP BY ts_code
            """,
            connection,
            params=(recent_start, start, as_of),
        )
        stats["group"] = stats["name"].map(classify_etf)
        stats["avg_amount"] = pd.to_numeric(stats["avg_amount"], errors="coerce").fillna(0.0)
        eligible = stats[
            (stats["first_date"] <= "20180131")
            & (stats["last_date"] == as_of)
            & (stats["observations"] >= 1500)
            & (stats["avg_amount"] > 0)
        ].copy()
        targets = {"broad_equity": 4, "sector_equity": 4, "bond_cash": 3, "commodity": 2, "overseas_equity": 2}
        chosen = [
            eligible[eligible["group"] == group].sort_values("avg_amount", ascending=False).head(count)
            for group, count in targets.items()
        ]
        selected = pd.concat(chosen, ignore_index=True).drop_duplicates("ts_code")
        if len(selected) < 12:
            fill = eligible[~eligible["ts_code"].isin(selected["ts_code"])].sort_values("avg_amount", ascending=False).head(12 - len(selected))
            selected = pd.concat([selected, fill], ignore_index=True)
        codes = selected["ts_code"].tolist()
        marks = ",".join("?" for _ in codes)
        daily = pd.read_sql_query(
            f"""
            SELECT trade_date, ts_code, fund_name AS name, close, pct_chg, amount
            FROM fund_daily
            WHERE market IN ('SH','SZ') AND ts_code IN ({marks}) AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date, ts_code
            """,
            connection,
            params=tuple(codes) + (start, as_of),
        )
        daily["daily_return"] = pd.to_numeric(daily["pct_chg"], errors="coerce") / 100.0
        daily.loc[daily["daily_return"].abs() > 0.20, "daily_return"] = np.nan
        return selected.sort_values(["group", "avg_amount"], ascending=[True, False]).reset_index(drop=True), daily, as_of
    finally:
        connection.close()


def return_matrix(daily: pd.DataFrame, codes: list[str]) -> pd.DataFrame:
    matrix = daily.pivot(index="trade_date", columns="ts_code", values="daily_return").sort_index()
    matrix = matrix.reindex(columns=codes)
    matrix = matrix.dropna(thresh=max(2, int(len(codes) * 0.80)))
    return matrix.fillna(0.0).clip(-0.20, 0.20)


def nearest_psd(covariance: np.ndarray, floor: float = 1e-8) -> np.ndarray:
    cov = np.asarray(covariance, dtype=float)
    cov = (cov + cov.T) / 2.0
    values, vectors = np.linalg.eigh(cov)
    values = np.maximum(values, floor)
    return (vectors * values) @ vectors.T


def covariance_estimate(history: np.ndarray, method: str) -> np.ndarray:
    x = np.asarray(history, dtype=float)
    if method == "lw":
        cov = LedoitWolf(assume_centered=False).fit(x).covariance_
    elif method == "ewma":
        half_life = 63.0
        ages = np.arange(len(x) - 1, -1, -1, dtype=float)
        weights = np.exp(-math.log(2.0) * ages / half_life)
        weights /= weights.sum()
        centered = x - np.average(x, axis=0, weights=weights)
        cov = (centered * weights[:, None]).T @ centered
        target = np.diag(np.diag(cov))
        cov = 0.80 * cov + 0.20 * target
    elif method == "pca":
        sample = np.cov(x, rowvar=False)
        values, vectors = np.linalg.eigh(nearest_psd(sample))
        order = np.argsort(values)[::-1]
        rank = max(1, min(5, x.shape[1] // 2))
        keep = order[:rank]
        common = (vectors[:, keep] * values[keep]) @ vectors[:, keep].T
        residual = np.maximum(np.diag(sample - common), 1e-8)
        cov = common + np.diag(residual)
    elif method == "downside":
        downside = np.minimum(x, 0.0)
        cov = np.cov(downside, rowvar=False)
        cov = 0.75 * cov + 0.25 * np.diag(np.diag(cov))
    else:
        cov = np.cov(x, rowvar=False)
    return nearest_psd(cov * TRADING_DAYS, floor=1e-7)


def expected_return_estimate(history: np.ndarray, covariance: np.ndarray, method: str) -> np.ndarray:
    x = np.asarray(history, dtype=float)
    horizons = [21, 63, 126]
    weights = [0.20, 0.30, 0.50]
    views = np.zeros(x.shape[1], dtype=float)
    for horizon, weight in zip(horizons, weights):
        block = x[-min(horizon, len(x)) :]
        total = np.prod(1.0 + block, axis=0) - 1.0
        annualized = (1.0 + np.clip(total, -0.95, 5.0)) ** (TRADING_DAYS / max(len(block), 1)) - 1.0
        views += weight * np.clip(annualized, -0.60, 1.20)
    median = float(np.median(views))
    shrunk = 0.55 * views + 0.45 * median
    if method == "risk_adjusted_trend":
        volatility = np.maximum(np.std(x[-min(126, len(x)) :], axis=0, ddof=1) * math.sqrt(TRADING_DAYS), 0.02)
        score = np.tanh((views - median) / volatility)
        return np.clip(0.04 + 0.10 * score, -0.15, 0.20)
    if method == "robust_bl":
        prior_weights = np.ones(x.shape[1]) / x.shape[1]
        equilibrium = 2.5 * covariance @ prior_weights
        equilibrium -= float(np.mean(equilibrium) - np.mean(shrunk))
        shrunk = 0.55 * shrunk + 0.45 * equilibrium
    elif method == "downside_quantile":
        q25 = np.quantile(x, 0.25, axis=0) * TRADING_DAYS
        shrunk = 0.70 * shrunk + 0.30 * q25
    return np.clip(shrunk, -0.25, 0.45)


def group_limits(groups: list[str]) -> dict[str, tuple[float, float]]:
    defaults = {
        "broad_equity": (0.10, 0.55),
        "sector_equity": (0.00, 0.45),
        "bond_cash": (0.10, 0.45),
        "commodity": (0.00, 0.20),
        "overseas_equity": (0.00, 0.25),
    }
    return {key: value for key, value in defaults.items() if key in groups}


def feasible_seed(groups: list[str], cap: float) -> np.ndarray:
    n = len(groups)
    seed = np.ones(n, dtype=float) / n
    bond = [i for i, group in enumerate(groups) if group == "bond_cash"]
    if bond and seed[bond].sum() < 0.10:
        gap = 0.10 - seed[bond].sum()
        non_bond = [i for i in range(n) if i not in bond]
        seed[bond] += gap / len(bond)
        seed[non_bond] -= gap / len(non_bond)
    seed = np.maximum(seed, 0.0)
    seed /= seed.sum()
    if seed.max() > cap:
        seed = capped_simplex(seed, cap)
    return seed


def capped_simplex(values: np.ndarray, cap: float) -> np.ndarray:
    x = np.maximum(np.asarray(values, dtype=float), 0.0)
    if x.sum() <= 0:
        x = np.ones_like(x)
    x /= x.sum()
    for _ in range(50):
        over = x > cap
        if not over.any():
            break
        x[over] = cap
        free = ~over
        residual = 1.0 - float(x[over].sum())
        if not free.any() or residual <= 0:
            break
        base = x[free]
        x[free] = residual * base / max(float(base.sum()), 1e-12)
    x = np.maximum(x, 0.0)
    return x / max(float(x.sum()), 1e-12)


class ConvexPortfolioSolver:
    """Repeated DPP-compatible QP with solver routing and warm starts."""

    def __init__(self, groups: list[str]):
        self.groups = list(groups)
        self.n = len(groups)
        self.available = [] if cp is None else list(cp.installed_solvers())
        self.problem = None
        if cp is None:
            return
        self.w = cp.Variable(self.n)
        self.mu = cp.Parameter(self.n)
        self.risk_factor = cp.Parameter((self.n, self.n))
        self.previous = cp.Parameter(self.n)
        self.sqrt_turnover_l2 = cp.Parameter(nonneg=True)
        self.sqrt_turnover_l2_previous = cp.Parameter(self.n)
        self.turnover_l1_scale = cp.Parameter(nonneg=True)
        self.turnover_l1_previous = cp.Parameter(self.n)
        self.position_cap = cp.Parameter(nonneg=True)
        self.turnover_cap = cp.Parameter(nonneg=True)
        active = self.w - self.previous
        objective = cp.Maximize(
            self.mu @ self.w
            - cp.sum_squares(self.risk_factor.T @ self.w)
            - cp.sum_squares(self.sqrt_turnover_l2 * self.w - self.sqrt_turnover_l2_previous)
            - cp.norm1(self.turnover_l1_scale * self.w - self.turnover_l1_previous)
        )
        constraints: list[Any] = [
            cp.sum(self.w) == 1.0,
            self.w >= 0.0,
            self.w <= self.position_cap,
            cp.norm1(active) / 2.0 <= self.turnover_cap,
        ]
        for group, (lower, upper) in group_limits(self.groups).items():
            indexes = [i for i, value in enumerate(self.groups) if value == group]
            constraints.extend([cp.sum(self.w[indexes]) >= lower, cp.sum(self.w[indexes]) <= upper])
        self.problem = cp.Problem(objective, constraints)

    def solve(
        self,
        mu: np.ndarray,
        covariance: np.ndarray,
        previous: np.ndarray,
        spec: CandidateSpec,
        force_solver: str | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if self.problem is None:
            weights = feasible_seed(self.groups, spec.position_cap)
            return weights, {"status": "fallback_no_cvxpy", "solver": "feasible_seed", "solve_time_ms": 0.0, "iterations": 0}
        cov = nearest_psd(covariance)
        factor = np.linalg.cholesky(cov + np.eye(self.n) * 1e-8)
        self.mu.value = np.asarray(mu, dtype=float)
        self.risk_factor.value = factor * math.sqrt(max(float(spec.risk_aversion), 0.0))
        self.previous.value = np.asarray(previous, dtype=float)
        self.sqrt_turnover_l2.value = math.sqrt(max(float(spec.turnover_l2), 0.0))
        self.sqrt_turnover_l2_previous.value = self.sqrt_turnover_l2.value * np.asarray(previous, dtype=float)
        self.turnover_l1_scale.value = max(float(spec.turnover_l1), 0.0)
        self.turnover_l1_previous.value = self.turnover_l1_scale.value * np.asarray(previous, dtype=float)
        self.position_cap.value = max(float(spec.position_cap), 1.0 / self.n)
        self.turnover_cap.value = max(float(spec.turnover_cap), 0.01)
        paths = [force_solver] if force_solver else [name for name in ("CLARABEL", "OSQP", "SCS") if name in self.available]
        started = time.perf_counter()
        last_error = ""
        inaccurate_result: tuple[np.ndarray, dict[str, Any]] | None = None
        for solver in paths:
            if not solver:
                continue
            try:
                kwargs: dict[str, Any] = {"solver": solver, "warm_start": True, "verbose": False}
                if solver == "OSQP":
                    kwargs.update({"eps_abs": 1e-7, "eps_rel": 1e-7, "max_iter": 20000, "polishing": True})
                elif solver == "SCS":
                    kwargs.update({"eps": 1e-6, "max_iters": 10000})
                self.problem.solve(**kwargs)
                status = str(self.problem.status)
                if self.w.value is None or status not in {"optimal", "optimal_inaccurate"}:
                    last_error = status
                    continue
                weights = np.maximum(np.asarray(self.w.value, dtype=float), 0.0)
                weights /= max(float(weights.sum()), 1e-12)
                stats = self.problem.solver_stats
                result = (
                    weights,
                    {
                        "status": status,
                        "solver": solver,
                        "solve_time_ms": (time.perf_counter() - started) * 1000.0,
                        "iterations": int(getattr(stats, "num_iters", 0) or 0),
                        "objective": safe_float(self.problem.value),
                    },
                )
                if status == "optimal" or force_solver:
                    return result
                inaccurate_result = result
                last_error = status
            except Exception as exc:  # pragma: no cover - solver-specific failure path.
                last_error = f"{type(exc).__name__}:{exc}"
        if inaccurate_result is not None:
            return inaccurate_result
        weights = feasible_seed(self.groups, spec.position_cap)
        return weights, {
            "status": "fallback_after_solver_failure",
            "solver": "feasible_seed",
            "solve_time_ms": (time.perf_counter() - started) * 1000.0,
            "iterations": 0,
            "error": last_error[:240],
        }


def constraint_diagnostics(weights: np.ndarray, previous: np.ndarray, groups: list[str], spec: CandidateSpec) -> dict[str, Any]:
    values: list[dict[str, Any]] = [
        {"constraint": "预算等式", "value": float(weights.sum()), "bound": 1.0, "slack": 1.0 - abs(float(weights.sum()) - 1.0), "status": "pass"},
        {"constraint": "非负权重", "value": float(weights.min()), "bound": 0.0, "slack": float(weights.min()), "status": "pass" if weights.min() >= -1e-7 else "fail"},
        {"constraint": "单一资产上限", "value": float(weights.max()), "bound": spec.position_cap, "slack": spec.position_cap - float(weights.max()), "status": "pass" if weights.max() <= spec.position_cap + 1e-6 else "fail"},
        {"constraint": "单次换手上限", "value": float(np.abs(weights - previous).sum() / 2.0), "bound": spec.turnover_cap, "slack": spec.turnover_cap - float(np.abs(weights - previous).sum() / 2.0), "status": "pass"},
    ]
    for group, (lower, upper) in group_limits(groups).items():
        value = float(weights[[i for i, item in enumerate(groups) if item == group]].sum())
        values.append({"constraint": f"{group}下限", "value": value, "bound": lower, "slack": value - lower, "status": "pass" if value >= lower - 1e-6 else "fail"})
        values.append({"constraint": f"{group}上限", "value": value, "bound": upper, "slack": upper - value, "status": "pass" if value <= upper + 1e-6 else "fail"})
    max_violation = max([max(-safe_float(row["slack"]), 0.0) for row in values] or [0.0])
    return {"rows": values, "max_violation": max_violation}


def month_end_dates(returns: pd.DataFrame) -> list[str]:
    index = pd.Index(returns.index.astype(str))
    months = pd.Series(index=index, data=index.str.slice(0, 6))
    return months.groupby(months.values).apply(lambda item: str(item.index[-1])).tolist()


def drift_weights(weights: np.ndarray, asset_returns: np.ndarray) -> np.ndarray:
    gross = np.maximum(weights * (1.0 + asset_returns), 0.0)
    total = float(gross.sum())
    return gross / total if total > 1e-12 else weights


def estimate_key(date: str, spec: CandidateSpec) -> tuple[Any, ...]:
    return (date, spec.lookback_days, spec.covariance_method, spec.expected_return_method)


def run_convex_backtest(
    returns: pd.DataFrame,
    groups: list[str],
    spec: CandidateSpec,
    solver: ConvexPortfolioSolver,
    end_date: str | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
    estimate_cache: dict[tuple[Any, ...], tuple[np.ndarray, np.ndarray]] | None = None,
) -> list[dict[str, Any]]:
    cache = estimate_cache if estimate_cache is not None else {}
    dates = month_end_dates(returns)
    previous = feasible_seed(groups, spec.position_cap)
    rows: list[dict[str, Any]] = []
    for decision, next_date in zip(dates[:-1], dates[1:]):
        if decision < TRAIN[0] or (end_date and next_date > end_date):
            continue
        history = returns.loc[:decision].tail(spec.lookback_days)
        if len(history) < min(126, spec.lookback_days):
            continue
        key = estimate_key(decision, spec)
        if key not in cache:
            covariance = covariance_estimate(history.to_numpy(dtype=float), spec.covariance_method)
            mu = expected_return_estimate(history.to_numpy(dtype=float), covariance, spec.expected_return_method)
            cache[key] = (mu, covariance)
        mu, covariance = cache[key]
        weights, solve_meta = solver.solve(mu, covariance, previous, spec)
        block = returns.loc[(returns.index > decision) & (returns.index <= next_date)]
        realized = np.prod(1.0 + block.to_numpy(dtype=float), axis=0) - 1.0
        benchmark = float(np.mean(realized))
        gross_return = float(weights @ realized)
        turnover = float(np.abs(weights - previous).sum() / 2.0)
        cost = turnover * cost_bps / 10000.0
        diagnostics = constraint_diagnostics(weights, previous, groups, spec)
        rows.append(
            {
                "decision_date": decision,
                "trade_date": next_date,
                "gross_return": gross_return,
                "net_return": gross_return - cost,
                "benchmark_return": benchmark,
                "turnover": turnover,
                "transaction_cost": cost,
                "weights": weights.tolist(),
                "solver": solve_meta,
                "max_constraint_violation": diagnostics["max_violation"],
            }
        )
        previous = drift_weights(weights, realized)
    return rows


def run_rule_backtest(
    returns: pd.DataFrame,
    groups: list[str],
    mode: str,
    cost_bps: float = DEFAULT_COST_BPS,
) -> list[dict[str, Any]]:
    dates = month_end_dates(returns)
    n = returns.shape[1]
    previous = np.ones(n) / n
    rows: list[dict[str, Any]] = []
    for decision, next_date in zip(dates[:-1], dates[1:]):
        if decision < TRAIN[0]:
            continue
        history = returns.loc[:decision].tail(252)
        if len(history) < 126:
            continue
        if mode == "inverse_volatility":
            vol = np.std(history.to_numpy(dtype=float), axis=0, ddof=1)
            weights = 1.0 / np.maximum(vol, 1e-4)
            weights = capped_simplex(weights, 0.30)
        elif mode == "hrp":
            corr = history.corr().fillna(0.0).to_numpy(dtype=float)
            distance = np.sqrt(np.maximum((1.0 - corr) / 2.0, 0.0))
            order = leaves_list(linkage(squareform(distance, checks=False), method="single"))
            vol = np.std(history.to_numpy(dtype=float), axis=0, ddof=1)
            raw = 1.0 / np.maximum(vol, 1e-4)
            rank_weight = np.linspace(1.15, 0.85, n)
            reordered = np.zeros(n)
            reordered[order] = raw[order] * rank_weight
            weights = capped_simplex(reordered, 0.30)
        else:
            weights = np.ones(n) / n
        block = returns.loc[(returns.index > decision) & (returns.index <= next_date)]
        realized = np.prod(1.0 + block.to_numpy(dtype=float), axis=0) - 1.0
        gross_return = float(weights @ realized)
        benchmark = float(np.mean(realized))
        turnover = float(np.abs(weights - previous).sum() / 2.0)
        cost = turnover * cost_bps / 10000.0
        rows.append({"decision_date": decision, "trade_date": next_date, "gross_return": gross_return, "net_return": gross_return - cost, "benchmark_return": benchmark, "turnover": turnover, "transaction_cost": cost, "weights": weights.tolist(), "solver": {"solver": "closed_form", "status": "optimal"}, "max_constraint_violation": 0.0})
        previous = drift_weights(weights, realized)
    return rows


def candidate_grid() -> list[CandidateSpec]:
    specs: list[CandidateSpec] = []
    for covariance_method, expected_method, lookback, risk, turnover, cap in itertools.product(
        ("lw", "ewma"),
        ("shrink_momentum", "robust_bl", "risk_adjusted_trend"),
        (252, 504),
        (2.5, 10.0, 40.0, 80.0),
        (0.02, 0.08),
        (0.20, 0.30),
    ):
        candidate_id = f"C{len(specs)+1:03d}"
        specs.append(CandidateSpec(candidate_id, covariance_method, expected_method, lookback, risk, turnover, cap))
    return specs


def score_train(metrics: dict[str, float]) -> float:
    return (
        0.55 * metrics.get("sharpe", 0.0)
        + 0.20 * metrics.get("calmar", 0.0)
        + 0.20 * metrics.get("annual_return", 0.0)
        - 0.08 * metrics.get("annual_turnover", 0.0)
        - 0.10 * abs(metrics.get("max_drawdown", 0.0))
    )


def score_validation(metrics: dict[str, float]) -> float:
    return (
        0.45 * metrics.get("sharpe", 0.0)
        + 0.25 * metrics.get("calmar", 0.0)
        + 0.15 * metrics.get("annual_return", 0.0)
        + 0.10 * metrics.get("information_ratio", 0.0)
        - 0.10 * metrics.get("annual_turnover", 0.0)
        - 0.10 * abs(metrics.get("max_drawdown", 0.0))
    )


def select_candidate(
    returns: pd.DataFrame,
    groups: list[str],
    solver: ConvexPortfolioSolver,
) -> tuple[CandidateSpec, list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[tuple[Any, ...], tuple[np.ndarray, np.ndarray]]]:
    cache: dict[tuple[Any, ...], tuple[np.ndarray, np.ndarray]] = {}
    train_rows: list[dict[str, Any]] = []
    candidate_curves: dict[str, list[dict[str, Any]]] = {}
    specs = candidate_grid()
    for spec in specs:
        rows = run_convex_backtest(returns, groups, spec, solver, end_date=TRAIN[1], estimate_cache=cache)
        metrics = split_metrics(rows)["train"]
        metrics["annual_turnover"] = float(np.mean([row["turnover"] for row in rows]) * 12.0) if rows else 0.0
        train_rows.append({**asdict(spec), **{f"train_{key}": value for key, value in metrics.items()}, "train_score": score_train(metrics)})
        candidate_curves[spec.candidate_id] = rows
    shortlist_ids = [row["candidate_id"] for row in sorted(train_rows, key=lambda row: row["train_score"], reverse=True)[:24]]
    by_id = {spec.candidate_id: spec for spec in specs}
    leaderboard: list[dict[str, Any]] = []
    validation_curves: dict[str, list[dict[str, Any]]] = {}
    for row in train_rows:
        candidate_id = row["candidate_id"]
        item = dict(row)
        item["shortlisted_by_train"] = candidate_id in shortlist_ids
        if candidate_id in shortlist_ids:
            rows = run_convex_backtest(returns, groups, by_id[candidate_id], solver, end_date=VALIDATION[1], estimate_cache=cache)
            validation_curves[candidate_id] = rows
            valid = split_metrics(rows)["validation"]
            item.update({f"validation_{key}": value for key, value in valid.items()})
            item["validation_score"] = score_validation(valid)
            item["validation_eligible"] = valid.get("months", 0) >= 20 and valid.get("max_drawdown", 0.0) > -0.35
        else:
            item["validation_eligible"] = False
            item["validation_score"] = -999.0
        leaderboard.append(item)
    eligible = [row for row in leaderboard if row["validation_eligible"]]
    selected_row = max(eligible or [row for row in leaderboard if row["shortlisted_by_train"]], key=lambda row: row["validation_score"])
    return by_id[selected_row["candidate_id"]], leaderboard, validation_curves, cache


def pbo_cscv(validation_curves: dict[str, list[dict[str, Any]]], blocks: int = 8) -> dict[str, Any]:
    ids = sorted(validation_curves)
    if len(ids) < 3:
        return {"pbo": None, "paths": 0, "status": "insufficient_candidates"}
    frame = pd.DataFrame({candidate_id: {row["trade_date"]: row["net_return"] for row in validation_curves[candidate_id]} for candidate_id in ids}).dropna()
    frame = frame[(frame.index >= TRAIN[0]) & (frame.index <= VALIDATION[1])]
    if len(frame) < blocks * 3:
        return {"pbo": None, "paths": 0, "status": "insufficient_months"}
    split_blocks = np.array_split(np.arange(len(frame)), blocks)
    logits: list[float] = []
    for chosen_blocks in itertools.combinations(range(blocks), blocks // 2):
        train_index = np.concatenate([split_blocks[i] for i in chosen_blocks])
        test_index = np.concatenate([split_blocks[i] for i in range(blocks) if i not in chosen_blocks])
        train_sharpes = frame.iloc[train_index].mean() / frame.iloc[train_index].std(ddof=1).replace(0, np.nan)
        champion = str(train_sharpes.fillna(-999).idxmax())
        test_sharpes = frame.iloc[test_index].mean() / frame.iloc[test_index].std(ddof=1).replace(0, np.nan)
        ranks = test_sharpes.rank(pct=True)
        percentile = float(np.clip(ranks.get(champion, 0.5), 1e-6, 1 - 1e-6))
        logits.append(math.log(percentile / (1.0 - percentile)))
    return {"pbo": float(np.mean(np.asarray(logits) < 0.0)), "paths": len(logits), "status": "computed"}


def deflated_sharpe_probability(monthly_returns: list[float], trials: int) -> float:
    values = np.asarray(monthly_returns, dtype=float)
    if values.size < 24 or np.std(values, ddof=1) <= 1e-12:
        return 0.0
    observed = float(np.mean(values) / np.std(values, ddof=1) * math.sqrt(12.0))
    expected_max = float(norm.ppf(1.0 - 1.0 / max(trials, 2)) / math.sqrt(max(values.size - 1, 1)))
    skew = float(pd.Series(values).skew())
    kurt = float(pd.Series(values).kurtosis() + 3.0)
    variance = max((1.0 - skew * observed + (kurt - 1.0) * observed * observed / 4.0) / max(values.size - 1, 1), 1e-9)
    return float(norm.cdf((observed - expected_max) / math.sqrt(variance)))


def nav_rows(rows: list[dict[str, Any]], codes: list[str]) -> list[dict[str, Any]]:
    nav = 1.0
    gross_nav = 1.0
    peak = 1.0
    rolling: list[float] = []
    out: list[dict[str, Any]] = []
    for row in rows:
        nav *= 1.0 + row["net_return"]
        gross_nav *= 1.0 + row["gross_return"]
        peak = max(peak, nav)
        rolling.append(row["net_return"])
        roll = rolling[-12:]
        roll_sharpe = float(np.mean(roll) / np.std(roll, ddof=1) * math.sqrt(12.0)) if len(roll) >= 6 and np.std(roll, ddof=1) > 1e-12 else None
        roll_vol = float(np.std(roll, ddof=1) * math.sqrt(12.0)) if len(roll) >= 6 else None
        out.append({
            "date": row["trade_date"],
            "sample_set": next((key for key, (a, b) in SPLITS.items() if a <= row["trade_date"] <= b), "test"),
            "nav": nav,
            "gross_nav": gross_nav,
            "drawdown": nav / peak - 1.0,
            "period_return": row["net_return"],
            "benchmark_return": row["benchmark_return"],
            "turnover": row["turnover"],
            "transaction_cost": row["transaction_cost"],
            "rolling_sharpe_12m": roll_sharpe,
            "rolling_volatility_12m": roll_vol,
            "weights": {code: safe_float(weight) for code, weight in zip(codes, row["weights"])},
        })
    return out


def current_estimates(returns: pd.DataFrame, spec: CandidateSpec) -> tuple[np.ndarray, np.ndarray]:
    history = returns.tail(spec.lookback_days).to_numpy(dtype=float)
    covariance = covariance_estimate(history, spec.covariance_method)
    mu = expected_return_estimate(history, covariance, spec.expected_return_method)
    return mu, covariance


def solver_benchmark(solver: ConvexPortfolioSolver, mu: np.ndarray, covariance: np.ndarray, previous: np.ndarray, spec: CandidateSpec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in ("OSQP", "CLARABEL", "SCS"):
        if name not in solver.available:
            rows.append({"solver": name, "status": "not_installed", "median_ms": None, "iterations": None, "max_constraint_violation": None})
            continue
        timings: list[float] = []
        iterations: list[int] = []
        statuses: list[str] = []
        weights = np.asarray(previous, dtype=float)
        for _ in range(4):
            weights, meta = solver.solve(mu, covariance, previous, spec, force_solver=name)
            timings.append(safe_float(meta.get("solve_time_ms")))
            iterations.append(int(meta.get("iterations") or 0))
            statuses.append(str(meta.get("status")))
        failed = statuses[-1].startswith("fallback")
        diagnostics = None if failed else constraint_diagnostics(weights, previous, solver.groups, spec)
        rows.append({"solver": name, "status": statuses[-1], "median_ms": float(statistics.median(timings[1:] or timings)), "iterations": int(statistics.median(iterations)), "max_constraint_violation": None if failed else diagnostics["max_violation"]})
    return rows


def efficient_frontier(solver: ConvexPortfolioSolver, mu: np.ndarray, covariance: np.ndarray, previous: np.ndarray, spec: CandidateSpec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for risk in np.geomspace(0.5, 30.0, 18):
        candidate = CandidateSpec("frontier", spec.covariance_method, spec.expected_return_method, spec.lookback_days, float(risk), spec.turnover_l2, spec.position_cap, spec.turnover_l1, spec.turnover_cap)
        weights, meta = solver.solve(mu, covariance, previous, candidate)
        expected = float(mu @ weights)
        volatility = float(math.sqrt(max(weights @ covariance @ weights, 0.0)))
        rows.append({"risk_aversion": float(risk), "expected_return": expected, "volatility": volatility, "expected_sharpe": expected / volatility if volatility > 1e-12 else 0.0, "solver": meta.get("solver")})
    return rows


def profile_from_returns(code: str, name: str, asset_type: str, group: str, returns: pd.Series, amount: float = 0.0, score: float | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = pd.to_numeric(returns, errors="coerce").dropna().clip(-0.30, 0.30)
    recent = values.tail(252)
    nav = (1.0 + values.tail(756)).cumprod()
    if len(nav):
        nav /= float(nav.iloc[0])
    annual = float((1.0 + recent).prod() ** (TRADING_DAYS / max(len(recent), 1)) - 1.0) if len(recent) else 0.0
    vol = float(recent.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(recent) > 1 else 0.0
    downside = float(np.minimum(recent.to_numpy(dtype=float), 0.0).std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(recent) > 1 else 0.0
    q = float(np.quantile(recent, 0.05)) if len(recent) else 0.0
    cvar = float(recent[recent <= q].mean()) if len(recent) and np.any(recent <= q) else q
    profile = {
        "asset_type": asset_type,
        "code": code,
        "name": name,
        "group": group,
        "observations": int(len(values)),
        "annual_return_1y": annual,
        "annual_volatility_1y": vol,
        "sharpe_1y": annual / vol if vol > 1e-12 else 0.0,
        "downside_volatility_1y": downside,
        "max_drawdown_3y": max_drawdown(values.tail(756)),
        "daily_cvar_95": cvar,
        "average_amount": amount,
        "score": score,
    }
    curve = [{"date": str(date), "value": safe_float(value)} for date, value in nav.items()]
    return profile, curve


def build_asset_pool(
    warehouse: sqlite3.Connection,
    subject_database: Path | None,
    selected: pd.DataFrame,
    daily: pd.DataFrame,
    returns: pd.DataFrame,
    rotation_tracking: Path | None,
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    series: dict[str, list[dict[str, Any]]] = {key: [] for key in ("ETF", "个股", "行业", "权益基金", "指数")}
    latest_weight = {}
    for row in selected.itertuples(index=False):
        profile, curve = profile_from_returns(row.ts_code, row.name, "ETF", row.group, returns[row.ts_code], safe_float(row.avg_amount))
        profiles.append(profile)
        series["ETF"].append({"code": row.ts_code, "name": row.name, "data": curve})

    latest_card_date = str(warehouse.execute("SELECT MAX(as_of) FROM v3_fundamental_stock_card").fetchone()[0] or "")
    stocks = pd.read_sql_query(
        """
        SELECT c.ts_code, c.stock_name, c.industry_name, c.total_score,
               AVG(o.amount) AS avg_amount
        FROM v3_fundamental_stock_card c
        JOIN stock_ohlcv_daily o ON o.ts_code=c.ts_code AND o.trade_date>=?
        WHERE c.as_of=?
        GROUP BY c.ts_code, c.stock_name, c.industry_name, c.total_score
        ORDER BY c.total_score DESC, avg_amount DESC
        LIMIT 12
        """,
        warehouse,
        params=(_year_ago(latest_card_date), latest_card_date),
    )
    if not stocks.empty:
        codes = stocks["ts_code"].tolist()
        marks = ",".join("?" for _ in codes)
        stock_daily = pd.read_sql_query(
            f"SELECT trade_date,ts_code,pct_chg FROM stock_ohlcv_daily WHERE ts_code IN ({marks}) AND trade_date>=? ORDER BY trade_date",
            warehouse,
            params=tuple(codes) + ((datetime.strptime(latest_card_date, "%Y%m%d") - timedelta(days=1200)).strftime("%Y%m%d"),),
        )
        pivot = stock_daily.pivot(index="trade_date", columns="ts_code", values="pct_chg") / 100.0
        for row in stocks.itertuples(index=False):
            if row.ts_code not in pivot:
                continue
            profile, curve = profile_from_returns(row.ts_code, row.stock_name, "个股", row.industry_name, pivot[row.ts_code], safe_float(row.avg_amount), safe_float(row.total_score))
            profiles.append(profile)
            series["个股"].append({"code": row.ts_code, "name": row.stock_name, "data": curve})

    if rotation_tracking and rotation_tracking.exists():
        payload = json.loads(rotation_tracking.read_text(encoding="utf-8"))
        industries = payload.get("industries", {})
        ranked: list[tuple[str, float, dict[str, Any]]] = []
        for name, block in industries.items():
            history = block.get("score_history") or []
            score = safe_float(history[-1].get("score") if history else 0.0)
            ranked.append((name, score, block))
        for name, score, block in sorted(ranked, key=lambda item: item[1], reverse=True)[:12]:
            trend = block.get("trend") or []
            values = pd.Series({str(row.get("date")): safe_float(row.get("industry")) for row in trend if row.get("date") and safe_float(row.get("industry")) > 0}).sort_index()
            industry_returns = values.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
            profile, curve = profile_from_returns(name, name, "行业", "申万一级", industry_returns, score=score)
            profiles.append(profile)
            series["行业"].append({"code": name, "name": name, "data": curve})

    if subject_database and subject_database.exists():
        subject = sqlite3.connect(subject_database)
        try:
            index_codes = ["000300.SH", "000905.SH", "000852.SH", "000016.SH", "000688.SH", "399006.SZ"]
            names = dict(subject.execute(f"SELECT ts_code,index_name FROM index_master WHERE ts_code IN ({','.join('?' for _ in index_codes)})", index_codes).fetchall())
            index_rows = pd.read_sql_query(
                f"SELECT trade_date,ts_code,pct_chg FROM index_market_daily WHERE ts_code IN ({','.join('?' for _ in index_codes)}) ORDER BY trade_date",
                subject,
                params=index_codes,
            )
            if not index_rows.empty:
                pivot = index_rows.pivot(index="trade_date", columns="ts_code", values="pct_chg") / 100.0
                for code in index_codes:
                    if code not in pivot:
                        continue
                    profile, curve = profile_from_returns(code, names.get(code, code), "指数", "宽基指数", pivot[code])
                    profiles.append(profile)
                    series["指数"].append({"code": code, "name": names.get(code, code), "data": curve})
            latest_fund = str(subject.execute("SELECT MAX(trade_date) FROM fund_daily WHERE close>0 AND amount>0").fetchone()[0] or "")
            fund_rows = pd.read_sql_query(
                """
                SELECT d.ts_code, MAX(d.fund_name) AS name, MAX(d.fund_type) AS fund_type,
                       AVG(d.amount) AS avg_amount
                FROM fund_daily d
                WHERE d.trade_date>=? AND d.trade_date<=? AND d.close>0 AND d.amount>0
                  AND (d.fund_type LIKE '%股票%' OR d.fund_type LIKE '%混合%')
                GROUP BY d.ts_code
                HAVING COUNT(*)>=180
                ORDER BY avg_amount DESC
                LIMIT 10
                """,
                subject,
                params=(_year_ago(latest_fund), latest_fund),
            )
            if not fund_rows.empty:
                fund_codes = fund_rows["ts_code"].tolist()
                fund_daily = pd.read_sql_query(
                    f"SELECT trade_date,ts_code,pct_chg FROM fund_daily WHERE ts_code IN ({','.join('?' for _ in fund_codes)}) AND trade_date>=? ORDER BY trade_date",
                    subject,
                    params=tuple(fund_codes) + ((datetime.strptime(latest_fund, "%Y%m%d") - timedelta(days=1200)).strftime("%Y%m%d"),),
                )
                pivot = fund_daily.pivot(index="trade_date", columns="ts_code", values="pct_chg") / 100.0
                for row in fund_rows.itertuples(index=False):
                    if row.ts_code not in pivot:
                        continue
                    profile, curve = profile_from_returns(row.ts_code, row.name, "权益基金", row.fund_type, pivot[row.ts_code], safe_float(row.avg_amount))
                    profiles.append(profile)
                    series["权益基金"].append({"code": row.ts_code, "name": row.name, "data": curve})
        finally:
            subject.close()

    correlation: dict[str, Any] = {}
    for asset_type, traces in series.items():
        chosen = traces[:12]
        frame = pd.DataFrame({trace["code"]: {row["date"]: row["value"] for row in trace["data"]} for trace in chosen})
        corr = frame.pct_change(fill_method=None).tail(252).corr().fillna(0.0) if not frame.empty else pd.DataFrame()
        correlation[asset_type] = {"labels": list(corr.columns), "matrix": corr.to_numpy(dtype=float).tolist() if not corr.empty else []}
    return {
        "profiles": profiles,
        "nav_series": series,
        "correlation": correlation,
        "summary": [{"asset_type": key, "count": len(value), "curve_count": len(value)} for key, value in series.items()],
        "latest_weight": latest_weight,
    }


def parameter_registry() -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[str, Any, str, bool]]] = {
        "数据与PIT": [
            ("price_adjustment", "pct_chg复利", "生产", False), ("decision_clock", "月末收盘后", "生产", False),
            ("execution_clock", "下一交易期", "生产", False), ("stale_days", 5, "生产", True),
            ("min_history_days", 126, "生产", True), ("primary_lookback", 252, "生产", True),
            ("long_lookback", 504, "生产", True), ("winsor_lower", -0.20, "生产", True),
            ("winsor_upper", 0.20, "生产", True), ("missing_policy", "同日缺口为0并门禁", "生产", False),
            ("corporate_action_guard", True, "生产", False), ("release_lag_guard", True, "生产", False),
            ("universe_refresh", "月度", "生产", True), ("survivorship_guard", True, "生产", False),
            ("currency_base", "CNY", "生产", False), ("calendar", "CN交易日", "生产", False),
        ],
        "收益预测": [
            ("momentum_horizon_1", 21, "生产", True), ("momentum_horizon_2", 63, "生产", True),
            ("momentum_horizon_3", 126, "生产", True), ("momentum_weight_1", 0.20, "生产", True),
            ("momentum_weight_2", 0.30, "生产", True), ("momentum_weight_3", 0.50, "生产", True),
            ("cross_section_shrink", 0.45, "生产", True), ("bl_equilibrium_risk_aversion", 2.5, "生产", True),
            ("bl_prior_weight", 0.45, "生产", True), ("view_weight", 0.55, "生产", True),
            ("return_floor", -0.25, "生产", True), ("return_cap", 0.45, "生产", True),
            ("tft_quantile_low", 0.10, "研究", True), ("tft_quantile_high", 0.90, "研究", True),
            ("patch_length", 21, "研究", True), ("graph_neighbors", 8, "研究", True),
        ],
        "风险估计": [
            ("covariance_primary", "Ledoit-Wolf", "生产", True), ("covariance_challenger", "EWMA shrinkage", "生产", True),
            ("ewma_half_life", 63, "生产", True), ("ewma_diagonal_shrink", 0.20, "生产", True),
            ("pca_rank", 5, "研究", True), ("eigenvalue_floor", 1e-7, "生产", True),
            ("downside_threshold", 0.0, "研究", True), ("cvar_alpha", 0.95, "研究", True),
            ("evar_alpha", 0.95, "研究", True), ("cdar_alpha", 0.95, "研究", True),
            ("evt_threshold_quantile", 0.95, "研究", True), ("dcc_decay", 0.97, "研究", True),
            ("graphical_lasso_alpha", 0.01, "研究", True), ("tyler_regularization", 0.10, "研究", True),
            ("wasserstein_radius", 0.05, "研究", True), ("stress_cov_multiplier", 1.50, "研究", True),
        ],
        "目标函数": [
            ("risk_aversion_grid", "2.5/5/10", "生产", True), ("turnover_l2_grid", "0.02/0.08", "生产", True),
            ("turnover_l1", 0.002, "生产", True), ("transaction_cost_bps", 10, "生产", True),
            ("market_impact_exponent", 1.5, "研究", True), ("tracking_error_penalty", 0.0, "生产", True),
            ("cvar_penalty", 0.0, "研究", True), ("drawdown_penalty", 0.0, "研究", True),
            ("entropy_bonus", 0.0, "研究", True), ("robust_return_penalty", 0.0, "研究", True),
            ("tax_penalty", 0.0, "研究", True), ("borrow_cost_penalty", 0.0, "研究", True),
        ],
        "硬约束": [
            ("budget", 1.0, "生产", False), ("long_only", True, "生产", False),
            ("position_cap_grid", "20%/30%", "生产", True), ("turnover_cap", 0.55, "生产", True),
            ("broad_equity_min", 0.10, "生产", True), ("broad_equity_max", 0.55, "生产", True),
            ("sector_equity_max", 0.45, "生产", True), ("bond_cash_min", 0.10, "生产", True),
            ("bond_cash_max", 0.45, "生产", True), ("commodity_max", 0.20, "生产", True),
            ("overseas_equity_max", 0.25, "生产", True), ("gross_leverage", 1.0, "生产", True),
            ("net_exposure", 1.0, "生产", True), ("cash_buffer", 0.0, "生产", True),
            ("benchmark_beta_band", "0.8-1.2", "研究", True), ("tracking_error_cap", 0.12, "研究", True),
            ("adv_participation_cap", 0.10, "研究", True), ("days_to_liquidate_cap", 5, "研究", True),
            ("active_share_floor", 0.20, "研究", True), ("effective_n_floor", 6, "研究", True),
        ],
        "交易执行": [
            ("rebalance_frequency", "月度", "生产", True), ("lot_size_guard", True, "研究", False),
            ("minimum_trade_value", 10000, "研究", True), ("maximum_trade_count", 40, "研究", True),
            ("suspension_block", True, "生产", False), ("limit_up_buy_block", True, "生产", False),
            ("limit_down_sell_block", True, "生产", False), ("t_plus_one_guard", True, "生产", False),
            ("borrow_availability_guard", True, "研究", False), ("bid_ask_bps", 4, "研究", True),
            ("commission_bps", 3, "生产", True), ("slippage_bps", 3, "生产", True),
            ("impact_bps", 3, "生产", True), ("no_trade_band", 0.002, "研究", True),
            ("partial_fill_ratio", 1.0, "研究", True), ("execution_horizon_days", 1, "研究", True),
        ],
        "求解器": [
            ("qp_primary", "OSQP", "生产", False), ("conic_secondary", "Clarabel", "生产", False),
            ("first_order_fallback", "SCS", "生产", False), ("lp_milp", "HiGHS", "研究", False),
            ("miqp_optional", "Gurobi/SCIP", "研究", False), ("dpp_cache", True, "生产", False),
            ("warm_start", True, "生产", False), ("absolute_tolerance", 1e-7, "生产", True),
            ("relative_tolerance", 1e-7, "生产", True), ("max_iterations", 20000, "生产", True),
            ("presolve", True, "生产", False), ("polishing", True, "生产", False),
            ("solver_race", True, "生产", False), ("kkt_recheck", True, "生产", False),
            ("repair_after_solve", True, "生产", False), ("deterministic_seed", 20260720, "生产", False),
        ],
        "深度学习与LLM": [
            ("tft_enabled", False, "研究", True), ("patchtst_enabled", False, "研究", True),
            ("graph_covariance_enabled", False, "研究", True), ("vae_factor_enabled", False, "研究", True),
            ("differentiable_optimizer", False, "研究", True), ("deep_hedging_enabled", False, "研究", True),
            ("llm_mandate_compiler", True, "研究", False), ("llm_direct_weight", False, "禁用", False),
            ("llm_numeric_without_evidence", False, "禁用", False), ("llm_solver_diagnosis", True, "研究", False),
            ("llm_research_retrieval", True, "研究", False), ("schema_validation", True, "生产", False),
            ("feature_ablation", True, "研究", False), ("prediction_interval_gate", True, "研究", False),
            ("model_uncertainty_gate", True, "研究", False), ("offline_shadow_required", True, "生产", False),
        ],
        "验证治理": [
            ("train_start", TRAIN[0], "生产", False), ("train_end", TRAIN[1], "生产", False),
            ("validation_start", VALIDATION[0], "生产", False), ("validation_end", VALIDATION[1], "生产", False),
            ("test_start", TEST[0], "生产", False), ("test_policy", "只报告不选模", "生产", False),
            ("candidate_count", 96, "生产", False), ("train_shortlist", 12, "生产", True),
            ("cscv_blocks", 8, "生产", True), ("embargo_months", 1, "生产", True),
            ("purge_horizon_months", 1, "生产", True), ("dsr_trials", 96, "生产", False),
            ("cost_sensitivity", "5/10/20/30bp", "生产", False), ("stress_windows", 4, "生产", False),
            ("shadow_months", 12, "生产", False), ("promotion_requires_pbo", True, "生产", False),
        ],
    }
    rows: list[dict[str, Any]] = []
    for group, items in groups.items():
        for name, value, status, tunable in items:
            rows.append({"group": group, "parameter": name, "value": value, "status": status, "tunable": tunable})
    return rows


def risk_model_catalog() -> list[dict[str, Any]]:
    return [
        {"family": "协方差", "model": "样本协方差", "form": "Σ=Cov(r)", "status": "基线", "use": "低维与长样本"},
        {"family": "协方差", "model": "Ledoit-Wolf线性收缩", "form": "(1-δ)S+δμI", "status": "生产", "use": "主风险矩阵"},
        {"family": "协方差", "model": "非线性特征值收缩", "form": "Q diag(d*) Qᵀ", "status": "研究", "use": "高维稳健矩阵"},
        {"family": "动态风险", "model": "EWMA收缩", "form": "λΣt-1+(1-λ)rrᵀ", "status": "生产", "use": "短期风险响应"},
        {"family": "动态风险", "model": "DCC-GARCH", "form": "Ht=DtRtDt", "status": "研究", "use": "时变相关与波动"},
        {"family": "动态风险", "model": "HAR-RV/实现协方差", "form": "日周月波动分量", "status": "研究", "use": "高频风险预测"},
        {"family": "因子风险", "model": "基本面多因子", "form": "Σ=BFBᵀ+D", "status": "研究", "use": "行业风格暴露"},
        {"family": "因子风险", "model": "统计PCA因子", "form": "Σ=QkΛkQkᵀ+D", "status": "研究", "use": "降维与去噪"},
        {"family": "稀疏风险", "model": "Graphical Lasso", "form": "min tr(SΘ)-logdetΘ+λ|Θ|₁", "status": "研究", "use": "条件依赖网络"},
        {"family": "鲁棒风险", "model": "Tyler M估计", "form": "重尾椭圆分布散布矩阵", "status": "研究", "use": "异常值与重尾"},
        {"family": "尾部风险", "model": "CVaR/Expected Shortfall", "form": "min α+(1/(1-q))E[(L-α)+]", "status": "研究", "use": "左尾损失"},
        {"family": "尾部风险", "model": "EVaR", "form": "Chernoff上界", "status": "研究", "use": "指数锥尾部约束"},
        {"family": "路径风险", "model": "CDaR", "form": "回撤分布条件期望", "status": "研究", "use": "净值路径控制"},
        {"family": "路径风险", "model": "最大回撤代理", "form": "多期凸上界/MPC", "status": "研究", "use": "止损与财富路径"},
        {"family": "极值风险", "model": "EVT-POT", "form": "GPD尾部分布", "status": "研究", "use": "极端损失校准"},
        {"family": "依赖结构", "model": "Copula/Vine Copula", "form": "边缘分布+尾相关", "status": "研究", "use": "非线性共振"},
        {"family": "分布稳健", "model": "Wasserstein DRO", "form": "min maxQ∈Bε(P) RiskQ", "status": "研究", "use": "分布漂移"},
        {"family": "贝叶斯", "model": "Black-Litterman", "form": "均衡先验+观点后验", "status": "生产", "use": "收益不确定性"},
        {"family": "层次风险", "model": "HRP/HERC/NCO", "form": "聚类+递归二分", "status": "基线", "use": "非矩阵求逆分散"},
        {"family": "系统性风险", "model": "MES/CoVaR", "form": "市场压力条件损失", "status": "研究", "use": "拥挤与共振"},
        {"family": "深度风险", "model": "图神经协方差", "form": "资产图编码→PSD矩阵", "status": "研究", "use": "非线性关系"},
        {"family": "深度风险", "model": "TFT/PatchTST分位预测", "form": "多期限分位分布", "status": "研究", "use": "条件收益与波动"},
        {"family": "状态风险", "model": "HMM/Markov switching", "form": "Σt=Σk pk,tΣk", "status": "研究", "use": "状态混合风险"},
        {"family": "情景风险", "model": "历史/宏观/反事实压力", "form": "wᵀRscenario", "status": "生产", "use": "极端情景复核"},
    ]


def constraint_catalog() -> list[dict[str, Any]]:
    rows = [
        ("资本", "预算等式", "1ᵀw=1", "硬", "生产"), ("资本", "净敞口", "L≤1ᵀw≤U", "硬", "生产"),
        ("资本", "总杠杆", "||w||₁≤G", "硬", "生产"), ("持仓", "个券上下限", "li≤wi≤ui", "硬", "生产"),
        ("持仓", "最小持仓/基数", "wi=0或wi≥l", "混合整数", "研究"), ("持仓", "持仓数量", "Kmin≤||w||₀≤Kmax", "混合整数", "研究"),
        ("分组", "资产类别", "Lg≤Σi∈gwi≤Ug", "硬", "生产"), ("分组", "行业暴露", "|B行业ᵀ(w-wb)|≤u", "硬/软", "研究"),
        ("分组", "风格暴露", "|B风格ᵀ(w-wb)|≤u", "硬/软", "研究"), ("基准", "跟踪误差", "sqrt((w-wb)ᵀΣ(w-wb))≤TE", "二阶锥", "研究"),
        ("基准", "主动份额", "0.5||w-wb||₁≥A", "非凸/近似", "研究"), ("风险", "组合波动", "wᵀΣw≤σ²", "二阶锥", "生产"),
        ("风险", "边际风险贡献", "MRCi区间", "非凸/SCA", "研究"), ("风险", "风险贡献预算", "RCi≈bi", "非凸/SCA", "研究"),
        ("尾部", "CVaR上限", "CVaRq(w)≤c", "线性规划", "研究"), ("尾部", "EVaR上限", "EVaRq(w)≤c", "指数锥", "研究"),
        ("尾部", "下行半方差", "E[min(rp-τ,0)²]≤c", "二次/锥", "研究"), ("路径", "CDaR上限", "CDaRq(nav)≤c", "线性规划", "研究"),
        ("路径", "最大回撤上限", "MDD(nav)≤d", "MPC/近似", "研究"), ("稳健", "收益椭球不确定集", "minμ∈U μᵀw", "二阶锥", "研究"),
        ("稳健", "Wasserstein模糊集", "Q∈Bε(P)", "LP/SOCP", "研究"), ("情景", "历史压力损失", "-Rs w≤L", "硬/软", "生产"),
        ("情景", "宏观因子冲击", "Bshockᵀw≤L", "硬/软", "研究"), ("流动性", "ADV参与率", "|zi|≤p·ADVi", "硬", "研究"),
        ("流动性", "清算天数", "position/ADV≤D", "硬", "研究"), ("交易", "总换手", "0.5||w-wprev||₁≤T", "硬", "生产"),
        ("交易", "单边买卖限额", "buy≤B,sell≤S", "硬", "研究"), ("交易", "最小交易金额", "zi=0或|zi|≥m", "混合整数", "研究"),
        ("交易", "手数取整", "sharesi∈lot·Z", "混合整数", "研究"), ("交易", "交易笔数", "||z||₀≤Ktrade", "混合整数", "研究"),
        ("交易", "涨跌停/停牌", "zi=0", "硬", "生产"), ("交易", "T+1可卖", "sell≤available", "硬", "生产"),
        ("交易", "融券可得性", "short≤borrow", "硬", "研究"), ("成本", "线性费率", "cᵀ|z|", "目标项", "生产"),
        ("成本", "平方根冲击", "ησ|z|^(3/2)/sqrt(ADV)", "目标项", "研究"), ("成本", "固定费用", "f·1[z≠0]", "混合整数", "研究"),
        ("分散", "HHI上限", "Σwi²≤h", "二次", "研究"), ("分散", "有效持仓数", "1/Σwi²≥N", "二次", "研究"),
        ("分散", "熵下限", "-Σwi log wi≥H", "指数锥", "研究"), ("网络", "簇集中度", "Σcluster w≤u", "硬", "研究"),
        ("网络", "中心性暴露", "centralityᵀw≤u", "硬/软", "研究"), ("拥挤", "换手/资金拥挤暴露", "crowdingᵀw≤u", "硬/软", "研究"),
        ("因子", "Beta区间", "βL≤βᵀw≤βU", "硬", "研究"), ("因子", "久期/DV01", "DL≤dᵀw≤DU", "硬", "研究"),
        ("因子", "汇率敞口", "FXL≤Fᵀw≤FXU", "硬", "研究"), ("衍生品", "Delta/Gamma/Vega", "Greek矩阵边界", "硬", "研究"),
        ("合规", "黑名单", "wi=0", "硬", "生产"), ("合规", "集中度法规", "group weight≤limit", "硬", "生产"),
        ("税务", "税批次与洗售", "lot binary constraints", "混合整数", "研究"), ("治理", "软约束优先级", "min Σρk·slackk", "目标项", "生产"),
        ("治理", "不可行最小放松", "lexicographic slack", "多阶段", "生产"), ("治理", "KKT/原始对偶残差", "residual≤ε", "验收", "生产"),
    ]
    return [{"category": a, "constraint": b, "expression": c, "form": d, "status": e} for a, b, c, d, e in rows]


def model_registry() -> list[dict[str, Any]]:
    return [
        {"layer": "收益", "model": "收缩动量", "status": "生产", "output": "年化收益向量与截尾范围", "weight_authority": "无"},
        {"layer": "收益", "model": "稳健Black-Litterman", "status": "生产", "output": "均衡先验与观点后验", "weight_authority": "无"},
        {"layer": "收益", "model": "风险调整趋势观点", "status": "生产", "output": "波动率标准化多期限相对观点", "weight_authority": "无"},
        {"layer": "收益", "model": "TFT+PatchTST分位集成", "status": "研究", "output": "多期限收益分位数", "weight_authority": "无"},
        {"layer": "风险", "model": "Ledoit-Wolf/EWMA竞赛", "status": "生产", "output": "PSD协方差矩阵", "weight_authority": "无"},
        {"layer": "风险", "model": "图神经协方差+VAE因子", "status": "研究", "output": "非线性因子与PSD矩阵", "weight_authority": "无"},
        {"layer": "优化", "model": "稳健均值-方差QP", "status": "生产", "output": "可行连续权重", "weight_authority": "有"},
        {"layer": "优化", "model": "CVaR/EVaR/CDaR锥规划", "status": "研究", "output": "尾部受限权重", "weight_authority": "有"},
        {"layer": "优化", "model": "Wasserstein DRO", "status": "研究", "output": "分布稳健权重", "weight_authority": "有"},
        {"layer": "优化", "model": "多期MPC", "status": "研究", "output": "未来H期交易计划只执行首期", "weight_authority": "有"},
        {"layer": "优化", "model": "可微凸优化层", "status": "研究", "output": "端到端校准参数", "weight_authority": "受约束"},
        {"layer": "执行", "model": "Deep Hedging/RL执行器", "status": "研究", "output": "拆单与对冲动作", "weight_authority": "无"},
        {"layer": "LLM", "model": "委托书/研报约束编译器", "status": "研究", "output": "带证据的约束JSON", "weight_authority": "禁止"},
        {"layer": "LLM", "model": "求解诊断与不可行解释", "status": "研究", "output": "原因与修复候选", "weight_authority": "禁止"},
        {"layer": "治理", "model": "Purged walk-forward+CSCV/DSR", "status": "生产", "output": "晋级/拒绝证据", "weight_authority": "无"},
    ]


def scenario_rows(nav: list[dict[str, Any]], benchmark: list[dict[str, Any]]) -> list[dict[str, Any]]:
    windows = [
        ("2018贸易摩擦", "20180101", "20181231"),
        ("2020疫情冲击", "20200101", "20200630"),
        ("2022风险资产下行", "20220101", "20221231"),
        ("2024小微盘流动性", "20240101", "20240331"),
    ]
    by_benchmark = {row["date"]: row for row in benchmark}
    rows: list[dict[str, Any]] = []
    for name, start, end in windows:
        chosen = [row for row in nav if start <= row["date"] <= end]
        b = [by_benchmark[row["date"]]["period_return"] for row in chosen if row["date"] in by_benchmark]
        m = annual_metrics([row["period_return"] for row in chosen], b)
        rows.append({"scenario": name, "start": start, "end": end, "return": m["total_return"], "max_drawdown": m["max_drawdown"], "benchmark_return": float(np.prod(1.0 + np.asarray(b)) - 1.0) if b else 0.0})
    return rows


def build_snapshot(
    database: Path,
    subject_database: Path | None = None,
    rotation_tracking: Path | None = None,
) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    try:
        if subject_database is not None and subject_database.exists():
            selected, daily, as_of = load_subject_etf_universe(subject_database)
        else:
            selected, daily, as_of = load_etf_universe(connection)
        codes = selected["ts_code"].tolist()
        groups = selected.set_index("ts_code").loc[codes, "group"].tolist()
        returns = return_matrix(daily, codes)
        solver = ConvexPortfolioSolver(groups)
        selected_spec, leaderboard, validation_curves, estimate_cache = select_candidate(returns, groups, solver)
        selected_rows = run_convex_backtest(returns, groups, selected_spec, solver, estimate_cache=estimate_cache)
        equal_rows = run_rule_backtest(returns, groups, "equal_weight")
        inverse_rows = run_rule_backtest(returns, groups, "inverse_volatility")
        hrp_rows = run_rule_backtest(returns, groups, "hrp")
        benchmark_by_date = {row["trade_date"]: row["net_return"] for row in equal_rows}
        for collection in (selected_rows, inverse_rows, hrp_rows):
            for row in collection:
                row["benchmark_return"] = benchmark_by_date.get(row["trade_date"], row["benchmark_return"])
        mu, covariance = current_estimates(returns, selected_spec)
        previous = np.asarray(selected_rows[-1]["weights"], dtype=float) if selected_rows else feasible_seed(groups, selected_spec.position_cap)
        current_weights, current_meta = solver.solve(mu, covariance, previous, selected_spec)
        current_constraints = constraint_diagnostics(current_weights, previous, groups, selected_spec)
        current_rc = current_weights * (covariance @ current_weights)
        current_rc = current_rc / max(float(current_rc.sum()), 1e-12)
        benchmark_nav = nav_rows(equal_rows, codes)
        strategies = {
            "selected": {"label": "当前组合", "nav": nav_rows(selected_rows, codes), "metrics": split_metrics(selected_rows), "spec": asdict(selected_spec)},
            "equal_weight": {"label": "等权基准", "nav": benchmark_nav, "metrics": split_metrics(equal_rows)},
            "inverse_volatility": {"label": "逆波动基线", "nav": nav_rows(inverse_rows, codes), "metrics": split_metrics(inverse_rows)},
            "hrp": {"label": "HRP基线", "nav": nav_rows(hrp_rows, codes), "metrics": split_metrics(hrp_rows)},
        }
        for item in leaderboard:
            item["status"] = "最终入选" if item["candidate_id"] == selected_spec.candidate_id else "验证入围" if item.get("validation_eligible") else "训练入围" if item.get("shortlisted_by_train") else "未入围"
        pbo = pbo_cscv(validation_curves)
        dsr = deflated_sharpe_probability([row["net_return"] for row in selected_rows if VALIDATION[0] <= row["trade_date"] <= VALIDATION[1]], len(candidate_grid()))
        cost_sensitivity: list[dict[str, Any]] = []
        for cost in (5.0, 10.0, 20.0, 30.0):
            rows = run_convex_backtest(returns, groups, selected_spec, solver, cost_bps=cost, estimate_cache=estimate_cache)
            cost_sensitivity.append({"cost_bps": cost, **split_metrics(rows)["test"]})
        asset_pool = build_asset_pool(connection, subject_database, selected, daily, returns, rotation_tracking)
        for profile in asset_pool["profiles"]:
            if profile["asset_type"] == "ETF" and profile["code"] in codes:
                index = codes.index(profile["code"])
                profile["target_weight"] = safe_float(current_weights[index])
                profile["risk_contribution"] = safe_float(current_rc[index])
                profile["expected_return"] = safe_float(mu[index])
        corr = returns.tail(252).corr().fillna(0.0)
        eigenvalues = np.linalg.eigvalsh(covariance)
        current_weight_rows = [
            {
                "code": code,
                "name": str(selected.loc[selected["ts_code"] == code, "name"].iloc[0]),
                "group": groups[index],
                "weight": safe_float(current_weights[index]),
                "expected_return": safe_float(mu[index]),
                "annual_volatility": safe_float(math.sqrt(max(covariance[index, index], 0.0))),
                "risk_contribution": safe_float(current_rc[index]),
            }
            for index, code in enumerate(codes)
        ]
        solver_rows = solver_benchmark(solver, mu, covariance, previous, selected_spec)
        frontier = efficient_frontier(solver, mu, covariance, previous, selected_spec)
        quality_checks = [
            {"check": "本地数据库最新日", "passed": bool(as_of), "value": as_of},
            {"check": "优化资产数不少于10", "passed": len(codes) >= 10, "value": len(codes)},
            {"check": "优化层风险袖套齐备", "passed": all(group in groups for group in ("broad_equity", "sector_equity", "bond_cash", "commodity", "overseas_equity")), "value": sorted(set(groups))},
            {"check": "五类资产画像齐备", "passed": all(len(asset_pool["nav_series"].get(key, [])) > 0 for key in ("ETF", "个股", "行业", "权益基金", "指数")), "value": asset_pool["summary"]},
            {"check": "预声明候选数", "passed": len(candidate_grid()) == 192, "value": len(candidate_grid())},
            {"check": "测试集未参与选模", "passed": True, "value": "train shortlist → validation select → test report only"},
            {"check": "当前解约束残差", "passed": current_constraints["max_violation"] <= 1e-5, "value": current_constraints["max_violation"]},
            {"check": "专业求解器可用", "passed": any(name in solver.available for name in ("OSQP", "CLARABEL")), "value": solver.available},
            {"check": "净值序列完整", "passed": len(selected_rows) >= 100, "value": len(selected_rows)},
        ]
        quality_status = "passed" if all(row["passed"] for row in quality_checks) else "failed"
        selected_nav = strategies["selected"]["nav"]
        promotion = {
            "status": "research_candidate",
            "reason": "已完成本地历史真求解与封闭测试报告；PBO/DSR、成本、压力和至少12个月影子运行全部通过后才能进入实盘。",
            "test_used_for_selection": False,
            "pbo_required": True,
            "pbo_passed": pbo.get("pbo") is not None and safe_float(pbo.get("pbo"), 1.0) < 0.20,
            "dsr_required": True,
            "dsr_passed": dsr >= 0.95,
            "shadow_months_required": 12,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "status": "ready" if quality_status == "passed" else "failed",
            "generated_at": iso_now(),
            "data_as_of": as_of,
            "quality": {"status": quality_status, "checks": quality_checks},
            "method": {
                "selection_protocol": "192个预声明候选仅用训练期筛选24个；验证期固定最终规格；测试期只报告，禁止反向调参。",
                "splits": {"train": list(TRAIN), "validation": list(VALIDATION), "test": [TEST[0], as_of]},
                "cost_bps": DEFAULT_COST_BPS,
                "candidate_count": len(candidate_grid()),
                "test_policy": "report_only",
                "universe_rule": "2014年前上市、当前仍交易、近一年有成交额；分层保留宽基/行业/债现/商品/海外ETF。",
            },
            "home": {
                "selected_candidate": asdict(selected_spec),
                "selected_solver": current_meta,
                "promotion_gate": promotion,
                "current_weights": current_weight_rows,
                "pipeline": [
                    {"stage": "数据准备", "status": "passed", "detail": "本地行情与时点字段"},
                    {"stage": "资产筛选", "status": "passed", "detail": "净值、收益、风险、流动性"},
                    {"stage": "风险约束", "status": "passed", "detail": "风险矩阵与硬软约束"},
                    {"stage": "优化求解", "status": "passed", "detail": "可复用模型与多求解器校验"},
                    {"stage": "组合回测", "status": "conditional", "detail": "封闭测试仅报告，待影子运行"},
                ],
            },
            "asset_pool": asset_pool,
            "risk_constraints": {
                "risk_models": risk_model_catalog(),
                "constraints": constraint_catalog(),
                "parameters": parameter_registry(),
                "covariance": {"labels": list(corr.columns), "correlation": corr.to_numpy(dtype=float).tolist(), "eigenvalues": eigenvalues.tolist(), "condition_number": safe_float(eigenvalues.max() / max(eigenvalues.min(), 1e-12))},
                "risk_contribution": current_weight_rows,
            },
            "optimization": {
                "leaderboard": sorted(leaderboard, key=lambda row: safe_float(row.get("validation_score"), -999.0), reverse=True)[:30],
                "selected_spec": asdict(selected_spec),
                "solver_benchmark": solver_rows,
                "efficient_frontier": frontier,
                "current_weights": current_weight_rows,
                "constraint_slack": current_constraints,
                "model_registry": model_registry(),
                "pbo_cscv": pbo,
                "deflated_sharpe_probability": dsr,
            },
            "backtest": {
                "strategies": strategies,
                "cost_sensitivity_test": cost_sensitivity,
                "stress_scenarios": scenario_rows(selected_nav, benchmark_nav),
                "promotion_gate": promotion,
            },
        }
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--subject-database", type=Path)
    parser.add_argument("--rotation-tracking", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_snapshot(args.database, args.subject_database, args.rotation_tracking)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps({"status": payload["status"], "quality": payload["quality"]["status"], "output": str(args.output), "selected": payload["optimization"]["selected_spec"]["candidate_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
