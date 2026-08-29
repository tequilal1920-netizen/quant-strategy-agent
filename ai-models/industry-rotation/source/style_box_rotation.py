"""Build the A-share 3×4 style-domain rotation snapshot.

The style box is an A-share extension inspired by Morningstar's stock-level
Style Box.  It is deliberately not represented as an official Morningstar
classification:

* Size is assigned by cumulative circulating market capitalisation:
  large 0-70%, mid 70-90%, small 90-100%.
* Every eligible stock is assigned to exactly one of Growth, Value, Blend or
  Dividend inside its size bucket.
* All accounting inputs are point-in-time: visible_date <= signal_date.
* The signal is formed at the quarter-end close and executed at the following
  trading-day close.  The test set is never used for candidate selection.

The script updates only the ``style`` section and style-related audit metadata
of an existing rotation snapshot; the 31-industry model is preserved byte-for-
byte at the Python object level.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


SIZE_LABELS = ("大盘", "中盘", "小盘")
STYLE_LABELS = ("成长", "均衡", "价值", "红利")
CELL_CODES = tuple(f"{size}{style}" for size in SIZE_LABELS for style in STYLE_LABELS)
TOP_N = 3
COST_RATE = 0.001
MAX_STOCK_WEIGHT = 0.08
TRAIN_END = "2018-12-31"
VALIDATION_START = "2019-01-01"
VALIDATION_END = "2021-12-31"
TEST_START = "2022-01-01"


def iso(value: str) -> str:
    text = str(value)
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def compact(value: str) -> str:
    return value.replace("-", "")


def finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def next_business_day(value: str) -> str:
    day = datetime.strptime(value, "%Y%m%d") + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.strftime("%Y%m%d")


def read_sql(connection: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> pd.DataFrame:
    return pd.read_sql_query(query, connection, params=tuple(params))


def placeholders(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def percentile(series: pd.Series, neutral: float = 0.5) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    ranked = numeric.rank(method="average", pct=True)
    return ranked.fillna(neutral).clip(0.0, 1.0)


def cross_zscore(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    mean = numeric.mean()
    std = numeric.std(ddof=0)
    if not math.isfinite(float(std or 0.0)) or float(std or 0.0) < 1e-12:
        return pd.Series(0.0, index=series.index)
    return ((numeric - mean) / std).replace([np.inf, -np.inf], 0.0).fillna(0.0)


def capped_weights(capitalisation: pd.Series, cap: float = MAX_STOCK_WEIGHT) -> pd.Series:
    """Return capitalisation weights with an 8% ceiling when mathematically feasible."""
    values = pd.to_numeric(capitalisation, errors="coerce").clip(lower=0.0).fillna(0.0)
    n = len(values)
    if n == 0:
        return values
    if n < math.ceil(1.0 / cap) or values.sum() <= 0:
        return pd.Series(1.0 / n, index=values.index)
    weights = values / values.sum()
    fixed = pd.Series(False, index=values.index)
    for _ in range(n + 1):
        over = (~fixed) & (weights > cap + 1e-12)
        if not over.any():
            break
        weights.loc[over] = cap
        fixed.loc[over] = True
        remaining = 1.0 - float(weights.loc[fixed].sum())
        free = ~fixed
        if not free.any() or remaining <= 0:
            break
        base = values.loc[free]
        weights.loc[free] = remaining * (base / base.sum() if base.sum() > 0 else 1.0 / free.sum())
    return weights / weights.sum()


def weighted_average(frame: pd.DataFrame, column: str) -> float | None:
    valid = frame[column].notna() & frame["circ_mv"].gt(0)
    if not valid.any():
        return None
    weights = capped_weights(frame.loc[valid, "circ_mv"])
    return finite((frame.loc[valid, column] * weights).sum())


def assign_size(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = frame.sort_values(["circ_mv", "ts_code"], ascending=[False, True]).copy()
    total = float(ranked["circ_mv"].sum())
    ranked["_cap_before"] = ranked["circ_mv"].cumsum().shift(fill_value=0.0) / total
    ranked["size"] = np.select(
        [ranked["_cap_before"] < 0.70, ranked["_cap_before"] < 0.90],
        ["大盘", "中盘"],
        default="小盘",
    )
    return ranked.drop(columns="_cap_before")


def assign_four_styles(frame: pd.DataFrame) -> pd.DataFrame:
    labelled: list[pd.DataFrame] = []
    for size in SIZE_LABELS:
        group = frame.loc[frame["size"].eq(size)].copy()
        if group.empty:
            continue
        group["dividend_percentile"] = percentile(group["dv_ttm"])
        group["dividend_qualified"] = (
            group["dv_ttm"].fillna(0).gt(0)
            & group["dividend_percentile"].ge(0.70)
            & group["dividend_positive_8q"].ge(6)
            & group["dividend_observed_8q"].ge(6)
        )
        group["style"] = "红利"
        residual = group.loc[~group["dividend_qualified"]].copy()
        if not residual.empty:
            residual = residual.sort_values(["style_spread", "ts_code"], ascending=[True, True])
            total = float(residual["circ_mv"].sum())
            residual["_cap_before"] = residual["circ_mv"].cumsum().shift(fill_value=0.0) / total
            residual["style"] = np.select(
                [residual["_cap_before"] < 0.30, residual["_cap_before"] < 0.70],
                ["价值", "均衡"],
                default="成长",
            )
            group.loc[residual.index, "style"] = residual["style"]
        group["cell"] = group["size"] + group["style"]
        labelled.append(group)
    return pd.concat(labelled, ignore_index=True)


def split_name(date_value: str) -> str:
    if date_value <= TRAIN_END:
        return "train"
    if VALIDATION_START <= date_value <= VALIDATION_END:
        return "validation"
    if date_value >= TEST_START:
        return "test"
    return "gap"


def drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return float("nan")
    wealth = (1.0 + returns).cumprod()
    return float((wealth / wealth.cummax() - 1.0).min())


def performance(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "status": "unavailable",
            "start": None,
            "end": None,
            "observations": 0,
            "annual_return": None,
            "benchmark_annual_return": None,
            "annual_excess": None,
            "sharpe": None,
            "excess_sharpe": None,
            "max_drawdown": None,
            "annual_turnover": None,
        }
    strategy = frame["strategy_return"].astype(float)
    benchmark = frame["benchmark_return"].astype(float)
    active = strategy - benchmark
    years = len(frame) / 4.0
    annual = float(np.prod(1.0 + strategy) ** (1.0 / years) - 1.0)
    benchmark_annual = float(np.prod(1.0 + benchmark) ** (1.0 / years) - 1.0)
    strategy_std = float(strategy.std(ddof=1))
    active_std = float(active.std(ddof=1))
    return {
        "status": "ok",
        "start": frame["execution_date"].iloc[0],
        "end": frame["end_date"].iloc[-1],
        "observations": int(len(frame)),
        "annual_return": finite(annual),
        "benchmark_annual_return": finite(benchmark_annual),
        "annual_excess": finite(annual - benchmark_annual),
        "sharpe": finite(strategy.mean() / strategy_std * math.sqrt(4)) if strategy_std > 0 else None,
        "excess_sharpe": finite(active.mean() / active_std * math.sqrt(4)) if active_std > 0 else None,
        "max_drawdown": finite(drawdown(strategy)),
        "annual_turnover": finite(frame["turnover"].mean() * 4),
    }


def candidate_scores(
    candidate: str,
    signal: str,
    completed_returns: pd.DataFrame,
    current_features: pd.DataFrame,
) -> pd.Series | None:
    history = completed_returns.loc[completed_returns.index < signal, list(CELL_CODES)]
    if len(history) < 4:
        return None
    momentum_4q = (1.0 + history.tail(4)).prod() - 1.0
    momentum_2q = (1.0 + history.tail(2)).prod() - 1.0
    z4 = cross_zscore(momentum_4q)
    z2 = cross_zscore(momentum_2q)
    volatility_4q = history.tail(4).std(ddof=0).replace(0.0, np.nan)
    loss_ratio_4q = history.tail(4).lt(0.0).mean()
    low_vol = cross_zscore(-volatility_4q)
    loss_control = cross_zscore(-loss_ratio_4q)
    aligned = current_features.set_index("cell").reindex(CELL_CODES)
    breadth = cross_zscore(aligned["growth_breadth"])
    carry = cross_zscore(aligned["carry_score"])
    if candidate in {"M4", "M4W"}:
        score = z4
    elif candidate == "M2":
        score = z2
    elif candidate in {"MIX", "MIXW"}:
        score = 0.50 * z2 + 0.50 * z4
    elif candidate == "PIT":
        score = 0.55 * (0.50 * z2 + 0.50 * z4) + 0.25 * breadth + 0.20 * carry
    elif candidate == "M4R":
        score = 0.52 * z4 + 0.23 * z2 + 0.15 * low_vol + 0.10 * loss_control
    elif candidate == "PITR":
        score = 0.45 * (0.50 * z2 + 0.50 * z4) + 0.20 * breadth + 0.15 * carry + 0.12 * low_vol + 0.08 * loss_control
    elif candidate == "BAL":
        score = 0.35 * z4 + 0.20 * z2 + 0.20 * carry + 0.15 * low_vol + 0.10 * loss_control
    else:
        raise ValueError(f"unknown candidate: {candidate}")
    return score.reindex(CELL_CODES).fillna(-99.0)


def style_box_weights(candidate: str, selected: list[str], score: pd.Series, history: pd.DataFrame) -> pd.Series:
    weights = pd.Series(0.0, index=CELL_CODES)
    if not selected:
        return weights
    if candidate not in {"M4W", "MIXW", "M4R", "PITR", "BAL"}:
        weights.loc[selected] = 1.0 / len(selected)
        return weights
    selected_score = pd.to_numeric(score.reindex(selected), errors="coerce")
    confidence = selected_score.sub(selected_score.min()).add(0.05).clip(lower=0.01)
    risk = history.tail(4).std(ddof=0).reindex(selected).replace(0.0, np.nan)
    fallback = float(risk.dropna().median()) if risk.notna().any() else 1.0
    raw = confidence.pow(0.5).div(risk.fillna(fallback).clip(lower=1e-6))
    raw = raw.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0.0)
    if raw.sum() <= 0.0:
        weights.loc[selected] = 1.0 / len(selected)
        return weights
    capped = raw / raw.sum()
    cap = 0.50
    for _ in range(len(capped) + 1):
        over = capped > cap + 1e-12
        if not over.any():
            break
        capped.loc[over] = cap
        remaining = 1.0 - float(capped.loc[over].sum())
        free = ~over
        if remaining <= 0.0 or not free.any():
            break
        base = raw.loc[free]
        capped.loc[free] = remaining * (base / base.sum() if base.sum() > 0.0 else 1.0 / int(free.sum()))
    weights.loc[selected] = capped / capped.sum()
    return weights


def run_candidate(
    candidate: str,
    period_returns: pd.DataFrame,
    features_by_signal: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    return_matrix = period_returns.pivot(index="signal_date", columns="cell", values="cell_return").reindex(columns=CELL_CODES)
    period_meta = period_returns[["signal_date", "execution_date", "end_date"]].drop_duplicates().set_index("signal_date")
    rows: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    previous = pd.Series(0.0, index=CELL_CODES)
    for signal in return_matrix.index:
        score = candidate_scores(candidate, signal, return_matrix, features_by_signal[signal])
        if score is None or return_matrix.loc[signal].isna().any():
            continue
        selected = list(score.sort_values(ascending=False, kind="stable").head(TOP_N).index)
        history = return_matrix.loc[return_matrix.index < signal, list(CELL_CODES)]
        weights = style_box_weights(candidate, selected, score, history)
        turnover = float((weights - previous).abs().sum() / 2.0)
        gross = float((weights * return_matrix.loc[signal]).sum())
        benchmark = float(return_matrix.loc[signal].mean())
        meta = period_meta.loc[signal]
        rows.append({
            "signal_date": iso(signal),
            "execution_date": iso(str(meta["execution_date"])),
            "end_date": iso(str(meta["end_date"])),
            "strategy_return": gross - COST_RATE * turnover,
            "benchmark_return": benchmark,
            "turnover": turnover,
            "split": split_name(iso(str(meta["execution_date"]))),
        })
        holdings.append({
            "signal_date": iso(signal),
            "execution_date": iso(str(meta["execution_date"])),
            "names": selected,
            "codes": selected,
            "weight": finite(float(weights.loc[selected].mean())),
            "weights": {cell: finite(float(weights.loc[cell])) for cell in selected},
            "turnover": finite(turnover),
        })
        previous = weights
    return pd.DataFrame(rows), holdings


@dataclass
class SourceFrames:
    signals: list[str]
    execution_dates: dict[str, str]
    master: pd.DataFrame
    valuations: pd.DataFrame
    prices: dict[str, pd.DataFrame]
    financials: pd.DataFrame
    data_as_of: str


def load_sources(database: Path) -> SourceFrames:
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        all_trade_dates = read_sql(
            connection,
            "SELECT DISTINCT trade_date FROM stock_ohlcv_daily ORDER BY trade_date",
        )["trade_date"].astype(str).tolist()
        quarter_groups: dict[tuple[int, int], str] = {}
        for day in all_trade_dates:
            year = int(day[:4])
            month = int(day[4:6])
            quarter = (month - 1) // 3 + 1
            quarter_groups[(year, quarter)] = day
        signals = [day for day in quarter_groups.values() if day >= "20120330"]
        signals.sort()
        next_date: dict[str, str] = {}
        position = {day: index for index, day in enumerate(all_trade_dates)}
        for signal in signals:
            index = position[signal]
            next_date[signal] = all_trade_dates[index + 1] if index + 1 < len(all_trade_dates) else next_business_day(signal)

        master = read_sql(connection, "SELECT * FROM security_master")
        qmarks = placeholders(signals)
        valuations = read_sql(
            connection,
            f"""
            SELECT trade_date, ts_code, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv
            FROM stock_valuation_daily
            WHERE trade_date IN ({qmarks})
            """,
            signals,
        )
        required_price_dates = sorted(set(signals) | {date for date in next_date.values() if date <= all_trade_dates[-1]})
        price_qmarks = placeholders(required_price_dates)
        price_frame = read_sql(
            connection,
            f"""
            SELECT trade_date, ts_code, stock_name, qfq_close
            FROM stock_ohlcv_daily
            WHERE trade_date IN ({price_qmarks})
            """,
            required_price_dates,
        )
        financials = read_sql(
            connection,
            """
            SELECT ts_code, visible_date, end_date, op_yoy, tr_yoy, netprofit_yoy
            FROM financial_report_visible
            ORDER BY visible_date, end_date
            """,
        )
    finally:
        connection.close()

    valuations["trade_date"] = valuations["trade_date"].astype(str)
    price_frame["trade_date"] = price_frame["trade_date"].astype(str)
    financials["visible_date"] = financials["visible_date"].astype(str)
    financials["end_date"] = financials["end_date"].astype(str)
    prices = {date: group.drop(columns="trade_date").set_index("ts_code") for date, group in price_frame.groupby("trade_date")}
    return SourceFrames(signals, next_date, master, valuations, prices, financials, all_trade_dates[-1])


def build_labels(source: SourceFrames) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    valuations = source.valuations.copy()
    dividend_pivot = valuations.pivot(index="ts_code", columns="trade_date", values="dv_ttm").reindex(columns=source.signals)
    positive = dividend_pivot.fillna(0).gt(0)
    observed = dividend_pivot.notna()
    master = source.master.set_index("ts_code")
    labels_by_signal: dict[str, pd.DataFrame] = {}
    features_by_signal: dict[str, pd.DataFrame] = {}

    for index, signal in enumerate(source.signals):
        frame = valuations.loc[valuations["trade_date"].eq(signal)].drop(columns="trade_date").copy()
        frame = frame.join(master, on="ts_code", how="inner", rsuffix="_master")
        signal_prices = source.prices.get(signal, pd.DataFrame())
        if not signal_prices.empty:
            frame = frame.join(signal_prices[["stock_name", "qfq_close"]], on="ts_code", how="left", rsuffix="_daily")
            frame["stock_name"] = frame["stock_name_daily"].fillna(frame["stock_name"])
            frame = frame.drop(columns=[column for column in ("stock_name_daily",) if column in frame])
        else:
            frame["qfq_close"] = np.nan
        list_cutoff = (datetime.strptime(signal, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
        eligible = (
            frame["list_date"].fillna("99999999").le(list_cutoff)
            & (frame["delist_date"].isna() | frame["delist_date"].gt(signal))
            & frame["circ_mv"].gt(0)
            & frame["qfq_close"].gt(0)
            & ~frame["stock_name"].fillna("").str.upper().str.contains("ST", regex=False)
        )
        frame = frame.loc[eligible].copy()

        start_index = max(0, index - 7)
        positive_count = positive.iloc[:, start_index:index + 1].sum(axis=1)
        observed_count = observed.iloc[:, start_index:index + 1].sum(axis=1)
        frame["dividend_positive_8q"] = frame["ts_code"].map(positive_count).fillna(0).astype(int)
        frame["dividend_observed_8q"] = frame["ts_code"].map(observed_count).fillna(0).astype(int)

        available = source.financials.loc[source.financials["visible_date"].le(signal)]
        latest = available.drop_duplicates("ts_code", keep="last").set_index("ts_code")
        frame = frame.join(latest[["visible_date", "end_date", "op_yoy", "tr_yoy", "netprofit_yoy"]], on="ts_code")

        frame["earnings_yield"] = np.where(frame["pe_ttm"].gt(0), 1.0 / frame["pe_ttm"], np.nan)
        frame["book_to_price"] = np.where(frame["pb"].gt(0), 1.0 / frame["pb"], np.nan)
        frame["sales_to_price"] = np.where(frame["ps_ttm"].gt(0), 1.0 / frame["ps_ttm"], np.nan)
        frame["value_factor_count"] = frame[["earnings_yield", "book_to_price", "sales_to_price"]].notna().sum(axis=1)
        frame["growth_factor_count"] = frame[["op_yoy", "tr_yoy", "netprofit_yoy"]].notna().sum(axis=1)

        frame = assign_size(frame)
        score_parts: list[pd.DataFrame] = []
        for size in SIZE_LABELS:
            group = frame.loc[frame["size"].eq(size)].copy()
            group["value_score"] = (
                percentile(group["earnings_yield"])
                + percentile(group["book_to_price"])
                + percentile(group["sales_to_price"])
            ) / 3.0
            group["growth_score"] = (
                percentile(group["op_yoy"])
                + percentile(group["tr_yoy"])
                + percentile(group["netprofit_yoy"])
            ) / 3.0
            group["style_spread"] = group["growth_score"] - group["value_score"]
            score_parts.append(group)
        frame = assign_four_styles(pd.concat(score_parts, ignore_index=True))

        frame["growth_breadth_stock"] = frame[["op_yoy", "tr_yoy", "netprofit_yoy"]].gt(0).sum(axis=1) / frame[
            ["op_yoy", "tr_yoy", "netprofit_yoy"]
        ].notna().sum(axis=1).replace(0, np.nan)
        frame["carry_stock"] = 0.65 * frame["value_score"] + 0.35 * frame["dividend_percentile"]
        labels_by_signal[signal] = frame

        feature_rows: list[dict[str, Any]] = []
        for cell in CELL_CODES:
            group = frame.loc[frame["cell"].eq(cell)]
            feature_rows.append({
                "cell": cell,
                "stock_count": int(len(group)),
                "cap": float(group["circ_mv"].sum()),
                "growth_breadth": weighted_average(group, "growth_breadth_stock"),
                "carry_score": weighted_average(group, "carry_stock"),
                "value_score": weighted_average(group, "value_score"),
                "growth_score": weighted_average(group, "growth_score"),
                "dividend_yield": weighted_average(group, "dv_ttm"),
            })
        features_by_signal[signal] = pd.DataFrame(feature_rows)
    return labels_by_signal, features_by_signal


def build_period_returns(
    source: SourceFrames,
    labels_by_signal: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, list[dict[str, Any]], float]:
    rows: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    coverage_values: list[float] = []
    for index, signal in enumerate(source.signals[:-1]):
        end_date = source.signals[index + 1]
        execution_date = source.execution_dates[signal]
        start_prices = source.prices.get(execution_date)
        end_prices = source.prices.get(end_date)
        if start_prices is None or end_prices is None:
            continue
        frame = labels_by_signal[signal].copy()
        frame = frame.join(start_prices[["qfq_close"]].rename(columns={"qfq_close": "start_close"}), on="ts_code")
        frame = frame.join(end_prices[["qfq_close"]].rename(columns={"qfq_close": "end_close"}), on="ts_code")
        frame["stock_return"] = frame["end_close"] / frame["start_close"] - 1.0
        for cell in CELL_CODES:
            group = frame.loc[frame["cell"].eq(cell)].copy()
            valid = group["stock_return"].replace([np.inf, -np.inf], np.nan).notna()
            base_weights = capped_weights(group["circ_mv"])
            coverage = float(base_weights.loc[valid].sum()) if len(group) else 0.0
            coverage_values.append(coverage)
            if not valid.any():
                cell_return = np.nan
            else:
                weights = base_weights.loc[valid]
                weights = weights / weights.sum()
                cell_return = float((weights * group.loc[valid, "stock_return"]).sum())
            rows.append({
                "signal_date": signal,
                "execution_date": execution_date,
                "end_date": end_date,
                "cell": cell,
                "cell_return": cell_return,
                "stock_count": int(len(group)),
                "return_stock_count": int(valid.sum()),
                "return_weight_coverage": coverage,
            })
            history.append({
                "signal_date": iso(signal),
                "execution_date": iso(execution_date),
                "end_date": iso(end_date),
                "cell": cell,
                "stock_count": int(len(group)),
                "return_stock_count": int(valid.sum()),
                "return_weight_coverage": finite(coverage),
                "return": finite(cell_return),
            })
    return pd.DataFrame(rows), history, float(np.mean(coverage_values))


def latest_cell_summary(frame: pd.DataFrame, total_cap: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cell in CELL_CODES:
        group = frame.loc[frame["cell"].eq(cell)].copy()
        weights = capped_weights(group["circ_mv"])
        top = group.assign(weight=weights).sort_values(["weight", "circ_mv"], ascending=False).head(5)
        rows.append({
            "cell": cell,
            "size": cell[:2],
            "style": cell[2:],
            "stock_count": int(len(group)),
            "cap_share": finite(group["circ_mv"].sum() / total_cap),
            "value_score": weighted_average(group, "value_score"),
            "growth_score": weighted_average(group, "growth_score"),
            "dividend_yield": weighted_average(group, "dv_ttm"),
            "top_holdings": [
                {
                    "code": row.ts_code,
                    "name": row.stock_name,
                    "weight": finite(row.weight),
                }
                for row in top.itertuples()
            ],
        })
    return rows


def stock_label_payload(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    ordered = frame.sort_values(["size", "style", "circ_mv"], ascending=[True, True, False])
    for row in ordered.itertuples():
        output.append({
            "code": row.ts_code,
            "name": row.stock_name,
            "size": row.size,
            "style": row.style,
            "cell": row.cell,
            "circ_mv_billion": finite(row.circ_mv / 10000.0, 4),
            "pe_ttm": finite(row.pe_ttm),
            "pb": finite(row.pb),
            "ps_ttm": finite(row.ps_ttm),
            "dividend_yield": finite(row.dv_ttm),
            "value_score": finite(row.value_score),
            "growth_score": finite(row.growth_score),
            "style_spread": finite(row.style_spread),
            "value_factor_count": int(row.value_factor_count),
            "growth_factor_count": int(row.growth_factor_count),
            "dividend_positive_8q": int(row.dividend_positive_8q),
            "financial_visible_date": iso(row.visible_date) if isinstance(row.visible_date, str) and len(row.visible_date) == 8 else None,
            "financial_period": iso(row.end_date) if isinstance(row.end_date, str) and len(row.end_date) == 8 else None,
        })
    return output


def migration_payload(previous: pd.DataFrame, current: pd.DataFrame) -> dict[str, Any]:
    joined = previous[["ts_code", "cell"]].merge(
        current[["ts_code", "cell"]],
        on="ts_code",
        suffixes=("_from", "_to"),
    )
    matrix = (
        joined.groupby(["cell_from", "cell_to"]).size().rename("count").reset_index()
        .sort_values(["count", "cell_from", "cell_to"], ascending=[False, True, True])
    )
    changed = matrix.loc[matrix["cell_from"].ne(matrix["cell_to"])]
    return {
        "common_stock_count": int(len(joined)),
        "changed_stock_count": int(joined["cell_from"].ne(joined["cell_to"]).sum()),
        "stability_rate": finite(joined["cell_from"].eq(joined["cell_to"]).mean()),
        "flows": [
            {"from": row.cell_from, "to": row.cell_to, "count": int(row.count)}
            for row in changed.head(24).itertuples()
        ],
        "matrix": [
            {"from": row.cell_from, "to": row.cell_to, "count": int(row.count)}
            for row in matrix.itertuples()
        ],
    }


def build_nav(backtest: pd.DataFrame) -> list[dict[str, Any]]:
    if backtest.empty:
        return []
    strategy = 1.0
    benchmark = 1.0
    output = [{
        "date": backtest["execution_date"].iloc[0],
        "strategy": 1.0,
        "benchmark": 1.0,
        "excess": 1.0,
    }]
    for row in backtest.itertuples():
        strategy *= 1.0 + row.strategy_return
        benchmark *= 1.0 + row.benchmark_return
        output.append({
            "date": row.end_date,
            "strategy": finite(strategy),
            "benchmark": finite(benchmark),
            "excess": finite(strategy / benchmark),
        })
    return output



def monthly_performance(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"status":"unavailable","start":None,"end":None,"observations":0,"annual_return":None,"benchmark_annual_return":None,"annual_excess":None,"sharpe":None,"excess_sharpe":None,"max_drawdown":None,"annual_turnover":None}
    strategy=frame["strategy_return"].astype(float); benchmark=frame["benchmark_return"].astype(float); active=strategy-benchmark
    years=len(frame)/12.0
    annual=float(np.prod(1.0+strategy)**(1.0/years)-1.0); bench_annual=float(np.prod(1.0+benchmark)**(1.0/years)-1.0)
    std=float(strategy.std(ddof=1)); active_std=float(active.std(ddof=1))
    return {"status":"ok","start":frame["execution_date"].iloc[0],"end":frame["end_date"].iloc[-1],"observations":int(len(frame)),"annual_return":finite(annual),"benchmark_annual_return":finite(bench_annual),"annual_excess":finite(annual-bench_annual),"sharpe":finite(strategy.mean()/std*math.sqrt(12)) if std>0 else None,"excess_sharpe":finite(active.mean()/active_std*math.sqrt(12)) if active_std>0 else None,"max_drawdown":finite(drawdown(strategy)),"annual_turnover":finite(frame["turnover"].mean()*12)}


def apply_monthly_style_overlay(style_payload: dict[str, Any], database: Path, source: SourceFrames) -> dict[str, Any]:
    """Replace the visible style strategy with monthly rotation on quarterly 3×4 labels.

    The original quarterly model is kept under ``legacy_quarterly``.  The new
    visible ``quarterly`` key is intentionally retained for front-end backward
    compatibility, but its internal ``frequency`` is monthly.
    """
    labels_by_signal, quarterly_features = build_labels(source)
    connection=sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        all_days=read_sql(connection,"SELECT DISTINCT trade_date FROM stock_ohlcv_daily ORDER BY trade_date")["trade_date"].astype(str).tolist()
        month_last={}
        for day in all_days:
            if day>="20120330":
                month_last[day[:6]]=day
        month_signals=[month_last[key] for key in sorted(month_last)]
        position={day:index for index,day in enumerate(all_days)}
        executions={signal:(all_days[position[signal]+1] if position[signal]+1<len(all_days) else next_business_day(signal)) for signal in month_signals}
        required=sorted(set(month_signals)|{day for day in executions.values() if day<=all_days[-1]})
        price_frame=read_sql(connection,f"SELECT trade_date,ts_code,stock_name,qfq_close,amount FROM stock_ohlcv_daily WHERE trade_date IN ({placeholders(required)})",required)
        valuation_frame=read_sql(connection,f"SELECT trade_date,ts_code,pe_ttm,pb,ps_ttm,dv_ttm,circ_mv,turnover_rate,turnover_rate_f,volume_ratio FROM stock_valuation_daily WHERE trade_date IN ({placeholders(month_signals)})",month_signals)
        moneyflow_frame=read_sql(connection,f"SELECT trade_date,ts_code,net_mf_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount FROM stock_moneyflow_daily WHERE trade_date IN ({placeholders(month_signals)})",month_signals)
    finally:
        connection.close()
    price_frame["trade_date"]=price_frame["trade_date"].astype(str); valuation_frame["trade_date"]=valuation_frame["trade_date"].astype(str); moneyflow_frame["trade_date"]=moneyflow_frame["trade_date"].astype(str)
    prices={day:group.drop(columns="trade_date").set_index("ts_code") for day,group in price_frame.groupby("trade_date")}
    valuations={day:group.drop(columns="trade_date").set_index("ts_code") for day,group in valuation_frame.groupby("trade_date")}
    moneyflows={day:group.drop(columns="trade_date").set_index("ts_code") for day,group in moneyflow_frame.groupby("trade_date")}
    quarter_map={}; q_index=0
    for signal in month_signals:
        while q_index+1<len(source.signals) and source.signals[q_index+1]<=signal:
            q_index+=1
        quarter_map[signal]=source.signals[q_index]

    rows=[]; coverage=[]
    for index,signal in enumerate(month_signals[:-1]):
        execution=executions[signal]; end_date=month_signals[index+1]
        start_prices=prices.get(execution); end_prices=prices.get(end_date)
        if start_prices is None or end_prices is None:
            continue
        frame=labels_by_signal[quarter_map[signal]][["ts_code","cell","circ_mv"]].copy()
        valuation=valuations.get(signal,pd.DataFrame())
        if not valuation.empty:
            frame=frame.set_index("ts_code").join(valuation[["circ_mv"]],how="left",rsuffix="_monthly").reset_index()
            frame["circ_mv"]=frame["circ_mv_monthly"].combine_first(frame["circ_mv"]); frame=frame.drop(columns="circ_mv_monthly")
        frame=frame.join(start_prices[["qfq_close"]].rename(columns={"qfq_close":"start_close"}),on="ts_code")
        frame=frame.join(end_prices[["qfq_close"]].rename(columns={"qfq_close":"end_close"}),on="ts_code")
        frame["stock_return"]=frame["end_close"]/frame["start_close"]-1.0
        for cell in CELL_CODES:
            group=frame.loc[frame["cell"].eq(cell)]
            valid=group["stock_return"].replace([np.inf,-np.inf],np.nan).notna()
            if valid.any():
                weights=capped_weights(group["circ_mv"]).loc[valid]; cov=float(weights.sum()); weights=weights/weights.sum(); cell_return=float((weights*group.loc[valid,"stock_return"]).sum())
            else:
                cov=0.0; cell_return=np.nan
            coverage.append(cov)
            rows.append({"signal_date":signal,"execution_date":execution,"end_date":end_date,"cell":cell,"cell_return":cell_return,"return_weight_coverage":cov})
    period_returns=pd.DataFrame(rows)
    return_matrix=period_returns.pivot(index="signal_date",columns="cell",values="cell_return").reindex(columns=CELL_CODES)
    meta=period_returns[["signal_date","execution_date","end_date"]].drop_duplicates().set_index("signal_date")

    price_pivot=price_frame.pivot(index="trade_date",columns="ts_code",values="qfq_close").reindex(month_signals).sort_index()
    monthly_return=price_pivot.pct_change(fill_method=None)
    stock_tech={"十二减一动量":price_pivot.shift(1)/price_pivot.shift(12)-1.0,"半年动量":price_pivot/price_pivot.shift(6)-1.0,"三月动量":price_pivot/price_pivot.shift(3)-1.0,"一月涨幅热度":price_pivot/price_pivot.shift(1)-1.0,"趋势效率":monthly_return.rolling(6).mean()/monthly_return.rolling(6).std(ddof=0),"回撤韧性":price_pivot/price_pivot.rolling(6).max()-1.0}
    previous_quarter={source.signals[index]:source.signals[index-1] for index in range(1,len(source.signals))}
    dim_cols={"基本面":["利润同比","收入同比","成长分","价值分"],"技术面":["十二减一动量","半年动量","三月动量","趋势效率","回撤韧性"],"估值面":["盈利收益率","账面收益率","销售收益率","股息率"],"资金面":["主力净流入","大单净流入","超大单净流入"],"拥挤度":["成交热度","量比热度","一月涨幅热度"]}
    def rank01(values: pd.Series) -> pd.Series:
        return pd.to_numeric(values,errors="coerce").rank(pct=True,method="average").fillna(0.5).clip(0.0,1.0)
    features_by_signal={}
    for signal in month_signals:
        quarter_signal=quarter_map[signal]
        frame=labels_by_signal[quarter_signal][["ts_code","cell","circ_mv","value_score","growth_score","dv_ttm","op_yoy","tr_yoy","netprofit_yoy"]].copy()
        prev_q=previous_quarter.get(quarter_signal)
        if prev_q:
            previous=labels_by_signal[prev_q][["ts_code","tr_yoy","netprofit_yoy"]].set_index("ts_code")
            frame=frame.set_index("ts_code").join(previous,how="left",rsuffix="_previous").reset_index()
            frame["netprofit_yoy_accel"]=pd.to_numeric(frame["netprofit_yoy"],errors="coerce")-pd.to_numeric(frame["netprofit_yoy_previous"],errors="coerce")
            frame["tr_yoy_accel"]=pd.to_numeric(frame["tr_yoy"],errors="coerce")-pd.to_numeric(frame["tr_yoy_previous"],errors="coerce")
        valuation=valuations.get(signal,pd.DataFrame())
        if not valuation.empty:
            frame=frame.set_index("ts_code").join(valuation,how="left",rsuffix="_monthly").reset_index()
            for col in ("circ_mv","pe_ttm","pb","ps_ttm","dv_ttm","turnover_rate","turnover_rate_f","volume_ratio"):
                mcol=f"{col}_monthly"
                if mcol in frame:
                    frame[col]=frame[mcol].combine_first(frame[col]) if col in frame else frame[mcol]
            frame=frame.drop(columns=[col for col in frame.columns if col.endswith("_monthly")],errors="ignore")
        for name,matrix in stock_tech.items():
            frame[name]=frame["ts_code"].map(matrix.loc[signal]) if signal in matrix.index else np.nan
        moneyflow=moneyflows.get(signal,pd.DataFrame())
        if not moneyflow.empty:
            frame=frame.set_index("ts_code").join(moneyflow,how="left").reset_index()
        for col in ("pe_ttm","pb","ps_ttm","dv_ttm","turnover_rate","turnover_rate_f","volume_ratio","amount","net_mf_amount","buy_lg_amount","sell_lg_amount","buy_elg_amount","sell_elg_amount","netprofit_yoy_accel","tr_yoy_accel"):
            if col not in frame: frame[col]=np.nan
        pe=pd.to_numeric(frame["pe_ttm"],errors="coerce"); pb=pd.to_numeric(frame["pb"],errors="coerce"); ps=pd.to_numeric(frame["ps_ttm"],errors="coerce"); amount=pd.to_numeric(frame["amount"],errors="coerce").replace(0.0,np.nan)
        atom={"景气加速度":frame["netprofit_yoy_accel"],"营收加速度":frame["tr_yoy_accel"],"盈利扩散":pd.to_numeric(frame["netprofit_yoy"],errors="coerce").gt(0.0).astype(float),"收入扩散":pd.to_numeric(frame["tr_yoy"],errors="coerce").gt(0.0).astype(float),"利润同比":frame["netprofit_yoy"],"收入同比":frame["tr_yoy"],"成长分":frame["growth_score"],"价值分":frame["value_score"],"盈利收益率":np.where(pe.gt(0.0),1.0/pe,np.nan),"账面收益率":np.where(pb.gt(0.0),1.0/pb,np.nan),"销售收益率":np.where(ps.gt(0.0),1.0/ps,np.nan),"股息率":frame["dv_ttm"],"主力净流入":pd.to_numeric(frame["net_mf_amount"],errors="coerce")/amount,"大单净流入":(pd.to_numeric(frame["buy_lg_amount"],errors="coerce")-pd.to_numeric(frame["sell_lg_amount"],errors="coerce"))/amount,"超大单净流入":(pd.to_numeric(frame["buy_elg_amount"],errors="coerce")-pd.to_numeric(frame["sell_elg_amount"],errors="coerce"))/amount,"成交热度":pd.to_numeric(frame["turnover_rate"],errors="coerce").combine_first(pd.to_numeric(frame["turnover_rate_f"],errors="coerce")),"量比热度":frame["volume_ratio"],"一月涨幅热度":frame["一月涨幅热度"]}
        for key,value in atom.items(): frame[key]=rank01(pd.Series(value,index=frame.index))
        feature_rows=[]
        for cell in CELL_CODES:
            group=frame.loc[frame["cell"].eq(cell)]
            row={"cell":cell,"stock_count":int(len(group)),"cap":float(group["circ_mv"].sum()) if len(group) else 0.0}
            if len(group):
                weights=capped_weights(group["circ_mv"])
                for dim,cols in dim_cols.items():
                    vals=[]
                    for col in cols:
                        x=pd.to_numeric(group[col],errors="coerce"); valid=x.notna()
                        if valid.any() and float(weights.loc[valid].sum())>0.0:
                            local=weights.loc[valid]/weights.loc[valid].sum(); vals.append(float((x.loc[valid]*local).sum()))
                    row[dim]=float(np.mean(vals)) if vals else 0.5
            else:
                for dim in dim_cols: row[dim]=0.5
            feature_rows.append(row)
        features=pd.DataFrame(feature_rows).set_index("cell").reindex(CELL_CODES)
        for dim in dim_cols:
            features[dim]=rank01(features[dim])
        features_by_signal[signal]=features

    candidates={
        "技术主锚":{"kind":"MOM","overlay":"none","top_n":2,"risk":True,"core":0.0,"definition":"12-1月风格域动量、半年动量与1月反转；Top2按得分置信度和12月波动风险加权"},
        "技术Top3":{"kind":"MOM","overlay":"none","top_n":3,"risk":True,"core":0.0,"definition":"同技术主锚，Top3分散化并按风险置信度加权"},
        "技术主锚低换手":{"kind":"MOM","overlay":"none","top_n":2,"risk":True,"core":0.7,"definition":"70%十二风格域等权核心 + 30%技术主锚Top2主动袖子"},
        "技术Top4低换手":{"kind":"MOM","overlay":"none","top_n":4,"risk":True,"core":0.5,"definition":"50%十二风格域等权核心 + 50%技术Top4主动袖子"},
        "五因子轻确认":{"kind":"MOM","overlay":"light","top_n":2,"risk":True,"core":0.0,"definition":"技术主锚82%，基本面/估值/资金确认与拥挤惩罚18%"},
        "五因子强确认":{"kind":"MOM","overlay":"strong","top_n":2,"risk":True,"core":0.0,"definition":"技术主锚70%，基本面/估值/资金确认与拥挤惩罚30%"},
        "风险韧性锚":{"kind":"RISK","overlay":"none","top_n":2,"risk":True,"core":0.0,"definition":"12-1月与半年动量，叠加12月低波和6月回撤韧性"},
        "风险五因子轻确认":{"kind":"RISK","overlay":"light","top_n":2,"risk":True,"core":0.0,"definition":"风险韧性锚82%，基本面/估值/资金确认与拥挤惩罚18%"},
        "五因子等权":{"kind":"FACTOR","overlay":"factor","top_n":3,"risk":False,"core":0.0,"factor_weights":{"基本面":0.22,"技术面":0.24,"估值面":0.18,"资金面":0.18,"拥挤度":-0.18},"definition":"五因子域暴露横截面标准化后等权近似合成，拥挤度反向，Top3等权"},
        "质量估值价值":{"kind":"FACTOR","overlay":"factor","top_n":3,"risk":False,"core":0.0,"factor_weights":{"基本面":0.28,"技术面":0.16,"估值面":0.28,"资金面":0.10,"拥挤度":-0.18},"definition":"偏盈利确认、价值红利和低拥挤，弱化短期技术与资金噪声，Top3等权"},
        "技术价值确认":{"kind":"HYBRID","overlay":"factor","top_n":2,"risk":True,"core":0.0,"base_weight":0.62,"factor_weights":{"基本面":0.12,"估值面":0.12,"资金面":0.06,"拥挤度":-0.08},"definition":"技术主锚62% + 基本面/估值/资金确认38%，拥挤反向，Top2风险加权"},
        "技术价值低换手":{"kind":"HYBRID","overlay":"factor","top_n":3,"risk":True,"core":0.45,"base_weight":0.58,"factor_weights":{"基本面":0.12,"估值面":0.12,"资金面":0.06,"拥挤度":-0.08},"definition":"45%十二风格域核心 + 55%技术价值确认Top3主动袖子，降低风格误判换手"},
    }
    def tech_score(signal: str, risk: bool=False) -> pd.Series | None:
        history=return_matrix.loc[return_matrix.index<signal,list(CELL_CODES)]
        if len(history)<12: return None
        mom6=(1.0+history.tail(6)).prod()-1.0; mom12=(1.0+history.iloc[-12:-1]).prod()-1.0; rev1=-history.tail(1).iloc[0]
        if risk:
            vol=history.tail(12).std(ddof=0); wealth=(1.0+history.tail(6)).cumprod(); resil=(wealth/wealth.cummax()-1.0).min()
            return 0.40*cross_zscore(mom12)+0.30*cross_zscore(mom6)+0.15*cross_zscore(-vol)+0.15*cross_zscore(-resil.abs())
        return 0.50*cross_zscore(mom12)+0.35*cross_zscore(mom6)+0.15*cross_zscore(rev1)
    def factor_score(signal: str, weights: dict[str, float]) -> pd.Series:
        feat=features_by_signal[signal]
        out=pd.Series(0.0,index=CELL_CODES,dtype=float)
        for dim,weight in weights.items():
            if dim in feat:
                out=out+float(weight)*cross_zscore(feat[dim])
        return out
    def score_for(candidate: dict[str, Any], signal: str) -> pd.Series | None:
        if candidate["kind"]=="FACTOR":
            return factor_score(signal,candidate.get("factor_weights",{}))
        base=tech_score(signal,candidate["kind"]=="RISK")
        if base is None: return None
        if candidate["kind"]=="HYBRID":
            return float(candidate.get("base_weight",0.6))*base+factor_score(signal,candidate.get("factor_weights",{}))
        if candidate["overlay"]=="none": return base
        feat=features_by_signal[signal]
        if candidate["overlay"]=="light":
            return 0.82*base+0.06*cross_zscore(feat["基本面"])+0.04*cross_zscore(feat["估值面"])+0.05*cross_zscore(feat["资金面"])-0.05*cross_zscore(feat["拥挤度"])
        return 0.70*base+0.09*cross_zscore(feat["基本面"])+0.07*cross_zscore(feat["估值面"])+0.08*cross_zscore(feat["资金面"])-0.08*cross_zscore(feat["拥挤度"])
    def weights_for(selected: list[str], score: pd.Series, history: pd.DataFrame, candidate: dict[str, Any]) -> pd.Series:
        weights=pd.Series(float(candidate["core"])/len(CELL_CODES),index=CELL_CODES); active=pd.Series(0.0,index=CELL_CODES)
        if candidate["risk"]:
            vol=history.tail(12).std(ddof=0).reindex(selected).replace(0.0,np.nan); fallback=float(vol.dropna().median()) if vol.notna().any() else 1.0
            conf=score.loc[selected].sub(score.loc[selected].min()).add(0.05).clip(lower=0.01).pow(0.5); raw=conf.div(vol.fillna(fallback).clip(lower=1e-6)).replace([np.inf,-np.inf],np.nan).fillna(0.0).clip(lower=0.0)
            active.loc[selected]=raw/raw.sum() if raw.sum()>0.0 else 1.0/len(selected)
        else:
            active.loc[selected]=1.0/len(selected)
        return weights+(1.0-float(candidate["core"]))*active
    def serialize_weights(weights: pd.Series) -> dict[str, float | None]:
        rounded={cell:finite(float(value)) for cell,value in weights.items() if abs(float(value))>1e-10}
        if rounded:
            total=sum(value for value in rounded.values() if value is not None)
            key=max(rounded,key=lambda cell: rounded[cell] if rounded[cell] is not None else -1.0)
            rounded[key]=finite(float(rounded[key] or 0.0)+1.0-total)
        return rounded
    def run_candidate_monthly(candidate: dict[str, Any]) -> tuple[pd.DataFrame,list[dict[str,Any]]]:
        rows=[]; holdings=[]; previous=pd.Series(0.0,index=CELL_CODES)
        for signal in return_matrix.index:
            score=score_for(candidate,signal)
            if score is None or return_matrix.loc[signal].isna().any(): continue
            selected=list(score.sort_values(ascending=False,kind="stable").head(int(candidate["top_n"])).index); history=return_matrix.loc[return_matrix.index<signal,list(CELL_CODES)]
            weights=weights_for(selected,score,history,candidate); turnover=float((weights-previous).abs().sum()/2.0); gross=float((weights*return_matrix.loc[signal]).sum()); benchmark=float(return_matrix.loc[signal].mean()); row=meta.loc[signal]
            rows.append({"signal_date":iso(signal),"execution_date":iso(str(row["execution_date"])),"end_date":iso(str(row["end_date"])),"strategy_return":gross-COST_RATE*turnover,"benchmark_return":benchmark,"turnover":turnover,"split":split_name(iso(str(row["execution_date"])))})
            holdings.append({"signal_date":iso(signal),"execution_date":iso(str(row["execution_date"])),"names":selected,"codes":selected,"weight":finite(float(weights.loc[selected].mean())),"weights":serialize_weights(weights),"active_names":selected,"turnover":finite(turnover)})
            previous=weights
        return pd.DataFrame(rows),holdings
    runs={name:run_candidate_monthly(candidate) for name,candidate in candidates.items()}
    audits=[]
    for name,(backtest,_) in runs.items():
        metrics={split:monthly_performance(backtest.loc[backtest["split"].eq(split)]) for split in ("train","validation","test")}
        lower=min(metrics["train"]["excess_sharpe"] or -999.0,metrics["validation"]["excess_sharpe"] or -999.0); ann_lower=min(metrics["train"]["annual_excess"] or -999.0,metrics["validation"]["annual_excess"] or -999.0)
        audits.append({"candidate":name,"definition":candidates[name]["definition"],"selection_objective":finite(lower),"train_excess_sharpe":metrics["train"]["excess_sharpe"],"validation_excess_sharpe":metrics["validation"]["excess_sharpe"],"validation_annual_excess":metrics["validation"]["annual_excess"],"validation_annual_turnover":metrics["validation"]["annual_turnover"],"test_excess_sharpe_report_only":metrics["test"]["excess_sharpe"],"metrics":metrics,"lower_bound_excess_sharpe":finite(lower),"lower_bound_annual_excess":finite(ann_lower)})
    eligible=[row for row in audits if (row["metrics"]["train"]["sharpe"] or -999.0)>0 and (row["metrics"]["validation"]["sharpe"] or -999.0)>0 and (row["metrics"]["train"]["annual_excess"] or -999.0)>0 and (row["metrics"]["validation"]["annual_excess"] or -999.0)>0]
    base_pool=eligible or audits
    best_lower=max((row["lower_bound_excess_sharpe"] or -999.0) for row in base_pool)
    one_standard_error=0.02
    robust_pool=[row for row in base_pool if (row["lower_bound_excess_sharpe"] or -999.0)>=best_lower-one_standard_error and (row["lower_bound_annual_excess"] or -999.0)>0]
    selected_row=min(robust_pool or base_pool,key=lambda row:(row["validation_annual_turnover"] or 999.0,-1.0*(row["lower_bound_excess_sharpe"] or -999.0),-1.0*(row["lower_bound_annual_excess"] or -999.0)))
    selected=selected_row["candidate"]
    for row in audits:
        row["selected"]=row["candidate"]==selected
        row["within_one_standard_error"]=bool((row["lower_bound_excess_sharpe"] or -999.0)>=best_lower-one_standard_error)
        row["one_standard_error"]=one_standard_error
    backtest,holdings=runs[selected]; metrics={split:monthly_performance(backtest.loc[backtest["split"].eq(split)]) for split in ("train","validation","test")}; metrics["all"]=monthly_performance(backtest)
    latest_signal=month_signals[-1]; candidate=candidates[selected]; latest_score=score_for(candidate,latest_signal)
    if latest_score is None: return style_payload
    latest_selected=list(latest_score.sort_values(ascending=False,kind="stable").head(int(candidate["top_n"])).index); latest_history=return_matrix.loc[return_matrix.index<latest_signal,list(CELL_CODES)]; latest_weights=weights_for(latest_selected,latest_score,latest_history,candidate)
    latest_label=labels_by_signal[quarter_map[latest_signal]]; summaries={row["cell"]:row for row in latest_cell_summary(latest_label,float(latest_label["circ_mv"].sum()))}; latest_features=features_by_signal[latest_signal]
    ranking=[]
    for rank,(cell,score) in enumerate(latest_score.sort_values(ascending=False,kind="stable").items(),start=1):
        summary=summaries[cell]
        ranking.append({"rank":rank,"code":cell,"name":cell,"size":summary["size"],"style":summary["style"],"score":finite(score),"selected":cell in set(latest_selected),"weight":finite(float(latest_weights.loc[cell])),"stock_count":summary["stock_count"],"cap_share":summary["cap_share"],"components":{"基本面":finite(latest_features.loc[cell,"基本面"]),"技术面":finite(latest_features.loc[cell,"技术面"]),"估值面":finite(latest_features.loc[cell,"估值面"]),"资金面":finite(latest_features.loc[cell,"资金面"]),"拥挤度":finite(latest_features.loc[cell,"拥挤度"])}})
    latest_execution=executions[latest_signal]; current={"signal_date":iso(latest_signal),"execution_date":iso(latest_execution),"names":latest_selected,"codes":latest_selected,"weight":finite(float(latest_weights.loc[latest_selected].mean())),"weights":serialize_weights(latest_weights),"active_names":latest_selected,"turnover":None,"status":"planned" if latest_execution>source.data_as_of else "executed"}
    checks={"train_sharpe_positive":bool((metrics["train"]["sharpe"] or -999.0)>0),"train_excess_positive":bool((metrics["train"]["annual_excess"] or -999.0)>0),"validation_sharpe_positive":bool((metrics["validation"]["sharpe"] or -999.0)>0),"validation_excess_positive":bool((metrics["validation"]["annual_excess"] or -999.0)>0),"test_sharpe_positive_report_only":bool((metrics["test"]["sharpe"] or -999.0)>0),"test_excess_positive_report_only":bool((metrics["test"]["annual_excess"] or -999.0)>0)}
    style_payload["frequencies"]["legacy_quarterly"]=style_payload["frequencies"]["quarterly"]
    style_payload["frequencies"]["quarterly"]={
        "frequency":"monthly",
        "model_type":"季度3×4风格域标签 + 月度五因子风格域轮动",
        "benchmark":"12风格域月度等权",
        "selected_candidate":selected,
        "selected_window":"季度3×4标签、月度12风格域五因子聚合、训练验证下界选型",
        "selected_weights":[finite(float(latest_weights.loc[cell])) for cell in latest_selected],
        "selection_rule":"训练集和验证集同时为正后，先按两段超额Sharpe下界筛入一标准误内候选，再选验证期换手最低且年化超额为正的稳健模型；2022年后测试集只报告。",
        "current_regime":" / ".join(current["names"]),
        "latest_signal_date":iso(latest_signal),
        "latest_execution_date":iso(latest_execution),
        "candidate_audit":audits,
        "metrics":metrics,
        "gate":{"status":"pass" if all(checks.values()) else "review","checks":checks,"policy":"训练和验证负责选模；测试期只报告，不因测试结果反向调整。"},
        "nav":build_nav(backtest),
        "ranking":ranking,
        "holdings":holdings+[current],
    }
    style_payload["latest_signal_date"]=iso(latest_signal); style_payload["latest_execution_date"]=iso(latest_execution); style_payload["frequency"]="monthly"; style_payload["model_type"]="季度3×4风格域标签 + 月度五因子风格域轮动"; style_payload["benchmark"]="12风格域月度等权"
    style_payload["data_quality"].update({"monthly_signal_count":int(len(month_signals)),"completed_return_months":int(period_returns["signal_date"].nunique()),"style_label_rebalance":"quarterly","style_rotation_rebalance":"monthly","monthly_factor_dimensions":["基本面","技术面","估值面","资金面","拥挤度"],"mean_monthly_return_weight_coverage":finite(float(np.mean(coverage)))})
    style_payload["factor_contract"]["signal"]="月末交易日收盘后；个股3×4风格标签按最近季度标签沿用"
    style_payload["factor_contract"]["execution"]="下一交易日收盘"
    style_payload["factor_contract"]["portfolio"]="季度3×4风格域内聚合股票级五因子；训练验证选择月度Top2风格域，按得分置信度和12月波动风险加权；12风格域月度等权为基准"
    return style_payload


def apply_six_dimension_style_overlay(style_payload: dict[str, Any], project_root: Path, source: SourceFrames) -> dict[str, Any]:
    """Use the strict five-factor style-domain research output as visible style strategy.

    The frontend still reads the historical compatibility key ``quarterly``.
    Internally the strategy is monthly: quarterly 3×4 stock labels, monthly
    style-domain factor scoring and next-trading-day execution.
    """
    result_path = project_root / "output" / "industry_rotation" / "style_six_dimension_monthly" / "style_six_dimension_monthly.json"
    if not result_path.exists():
        return style_payload
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return style_payload
    if str(payload.get("data_as_of") or "") != iso(source.data_as_of):
        return style_payload
    strategy = ((payload.get("strategies") or {}).get("style12") or {})
    latest = strategy.get("latest_holding") or {}
    selected = list(latest.get("selected") or [])
    if not strategy or not selected:
        return style_payload

    def normalised_weights(raw: dict[str, Any]) -> dict[str, float | None]:
        weights = {str(k): finite(float(v)) for k, v in (raw or {}).items() if v is not None}
        weights = {k: v for k, v in weights.items() if v is not None and abs(float(v)) > 1e-12}
        if weights:
            total = sum(float(v) for v in weights.values())
            key = max(weights, key=lambda item: float(weights[item] or 0.0))
            weights[key] = finite(float(weights[key] or 0.0) + 1.0 - total)
        return weights

    latest_weights = normalised_weights(latest.get("weights") or {})
    cell_map = {row.get("cell"): row for row in style_payload.get("cells", [])}
    score_map = dict(latest.get("score") or {})
    ranking = []
    for row in strategy.get("latest_ranking") or []:
        name = str(row.get("name") or "")
        summary = cell_map.get(name, {})
        ranking.append({
            "rank": int(row.get("rank") or len(ranking) + 1),
            "code": name,
            "name": name,
            "size": summary.get("size") or name[:2],
            "style": summary.get("style") or name[2:],
            "score": finite(row.get("score")),
            "selected": name in set(selected),
            "weight": latest_weights.get(name, 0.0),
            "stock_count": summary.get("stock_count"),
            "cap_share": summary.get("cap_share"),
            "components": {},
        })
    if not ranking and score_map:
        for rank, (name, score) in enumerate(sorted(score_map.items(), key=lambda kv: kv[1], reverse=True), start=1):
            summary = cell_map.get(name, {})
            ranking.append({"rank": rank, "code": name, "name": name, "size": summary.get("size") or name[:2], "style": summary.get("style") or name[2:], "score": finite(score), "selected": name in set(selected), "weight": latest_weights.get(name, 0.0), "stock_count": summary.get("stock_count"), "cap_share": summary.get("cap_share"), "components": {}})

    def split_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for split in ("train", "validation", "test", "all"):
            row = dict((metrics or {}).get(split) or {})
            row.setdefault("annual_turnover", None)
            out[split] = row
        return out

    holdings = []
    for item in strategy.get("holdings") or []:
        names = list(item.get("selected") or [])
        weights = normalised_weights(item.get("weights") or {})
        execution = iso(item.get("execution_date")) if item.get("execution_date") else None
        holdings.append({
            "signal_date": iso(item.get("signal_date")),
            "execution_date": execution,
            "names": names,
            "codes": names,
            "weight": finite(np.mean(list(weights.values()))) if weights else None,
            "weights": weights,
            "active_names": names,
            "turnover": finite(item.get("turnover")),
            "status": "planned" if execution and execution > iso(source.data_as_of) else "executed",
        })

    candidate_audit = []
    for item in strategy.get("candidate_audit") or []:
        metrics = {
            "train": dict(item.get("train") or {}),
            "validation": dict(item.get("validation") or {}),
            "test": dict(item.get("test_report_only") or {}),
        }
        metrics["all"] = {}
        candidate_audit.append({
            "candidate": item.get("candidate"),
            "definition": item.get("execution"),
            "selection_objective": finite(item.get("objective")),
            "selected": bool(item.get("selected")),
            "research_selected": bool(item.get("research_selected")),
            "metrics": metrics,
            "train_excess_sharpe": metrics["train"].get("excess_sharpe"),
            "validation_excess_sharpe": metrics["validation"].get("excess_sharpe"),
            "validation_annual_excess": metrics["validation"].get("annual_excess"),
            "test_excess_sharpe_report_only": metrics["test"].get("excess_sharpe"),
        })

    metrics = split_metrics(strategy.get("metrics") or {})
    benchmark_source = dict(strategy.get("benchmark_source") or {})
    benchmark_label = str(benchmark_source.get("label") or "标准指数基准")
    checks = {
        "train_excess_positive": bool((metrics["train"].get("annual_excess") or -999.0) > 0),
        "validation_excess_positive": bool((metrics["validation"].get("annual_excess") or -999.0) > 0),
        "test_excess_positive_report_only": bool((metrics["test"].get("annual_excess") or -999.0) > 0),
        "test_sharpe_positive_report_only": bool((metrics["test"].get("sharpe") or -999.0) > 0),
    }
    top_n = int(strategy.get("top_n") or len(selected))
    visible = {
        "frequency": "monthly",
        "model_type": "季度3×4风格域标签 + 月度五因子检验轮动",
        "benchmark": benchmark_label,
        "benchmark_source": benchmark_source,
        "top_n": top_n,
        "selected_candidate": strategy.get("selected_candidate") or "五因子框架",
        "research_selected_candidate": strategy.get("research_selected_candidate"),
        "selected_window": "季度3×4标签、月度五因子子因子检验、训练验证选权重与调仓数量",
        "selected_weights": list(latest_weights.values()),
        "selection_rule": (str(strategy.get("selection_rule") or "训练集和验证集筛选候选权重与调仓参数；测试期只报告。") + "；训练集/验证集选模，2022年后测试集只报告。"),
        "current_regime": " / ".join(selected),
        "latest_signal_date": iso(latest.get("signal_date")),
        "latest_execution_date": iso(latest.get("execution_date")),
        "candidate_audit": candidate_audit,
        "metrics": metrics,
        "gate": {"status": "pass" if all(checks.values()) else "review", "checks": checks, "policy": "训练和验证负责选模；测试期只报告，不因测试结果反向调整。"},
        "nav": strategy.get("nav") or [],
        "ranking": ranking,
        "holdings": holdings,
        "factor_diagnostics": strategy.get("factor_diagnostics"),
        "calendar_year": strategy.get("calendar_year"),
    }
    style_payload["frequencies"] = {"quarterly": visible}
    style_payload["latest_signal_date"] = visible["latest_signal_date"]
    style_payload["latest_execution_date"] = visible["latest_execution_date"]
    style_payload["frequency"] = "monthly"
    style_payload["model_type"] = visible["model_type"]
    style_payload["benchmark"] = visible["benchmark"]
    style_payload["data_quality"].update({
        "style_label_rebalance": "quarterly",
        "style_rotation_rebalance": "monthly",
        "five_factor_model_version": payload.get("model_version"),
        "monthly_signal_count": payload.get("signal_count"),
        "monthly_factor_dimensions": ["基本面", "技术面", "估值", "资金面", "拥挤度"],
        "admitted_factor_count": strategy.get("admitted_factor_count"),
    })
    style_payload["factor_contract"]["portfolio"] = f"季度3×4风格域标签；月度聚合股票级五因子；原子因子经RankIC/ICIR、胜率、Top-Bottom多空收益和稳定性检验后合成一级维度；训练验证在等权、检验权重、滚动IC与防守候选中选择；基准采用{benchmark_label}"
    return style_payload


def build_style_payload(source: SourceFrames) -> dict[str, Any]:
    labels_by_signal, features_by_signal = build_labels(source)
    period_returns, cell_history, mean_coverage = build_period_returns(source, labels_by_signal)
    candidates = ("M4", "M2", "MIX", "PIT", "M4W", "MIXW", "M4R", "PITR", "BAL")
    candidate_runs: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]] = {
        candidate: run_candidate(candidate, period_returns, features_by_signal)
        for candidate in candidates
    }
    candidate_audit: list[dict[str, Any]] = []
    for candidate, (backtest, _) in candidate_runs.items():
        split_metrics = {
            split: performance(backtest.loc[backtest["split"].eq(split)])
            for split in ("train", "validation", "test")
        }
        candidate_audit.append({
            "candidate": candidate,
            "definition": {
                "M4": "过去4个已完成季度的12风格箱横截面动量",
                "M2": "过去2个已完成季度的12风格箱横截面动量",
                "MIX": "2季度与4季度动量各50%",
                "PIT": "55%动量、25%可见日增长广度、20%价值/股息carry",
                "M4W": "四季动量选箱，Top3内部按得分置信度和近四季波动加权",
                "MIXW": "2/4季度动量选箱，Top3内部按得分置信度和近四季波动加权",
                "M4R": "四季动量叠加四季波动和亏损季度惩罚",
                "PITR": "PIT复合因子叠加四季波动和亏损季度惩罚",
                "BAL": "动量、价值股息carry、低波和亏损控制均衡融合",
            }[candidate],
            "selection_objective": split_metrics["validation"]["excess_sharpe"],
            "train_excess_sharpe": split_metrics["train"]["excess_sharpe"],
            "validation_excess_sharpe": split_metrics["validation"]["excess_sharpe"],
            "validation_annual_excess": split_metrics["validation"]["annual_excess"],
            "validation_annual_turnover": split_metrics["validation"]["annual_turnover"],
            "test_excess_sharpe_report_only": split_metrics["test"]["excess_sharpe"],
            "metrics": split_metrics,
        })
    best_validation = max(
        -999.0 if row["selection_objective"] is None else float(row["selection_objective"])
        for row in candidate_audit
    )
    validation_observations = max(
        int(row["metrics"]["validation"]["observations"])
        for row in candidate_audit
    )
    one_standard_error = math.sqrt(4.0 / validation_observations)
    robust_set = [
        row for row in candidate_audit
        if row["selection_objective"] is not None
        and float(row["selection_objective"]) >= best_validation - one_standard_error
        and (row["train_excess_sharpe"] or -999.0) > 0
        and (row["validation_annual_excess"] or -999.0) > 0
    ]
    selected_row = min(
        robust_set,
        key=lambda row: (
            999.0 if row["validation_annual_turnover"] is None else float(row["validation_annual_turnover"]),
            {"M4": 0, "M2": 1, "MIX": 2, "PIT": 3, "M4W": 4, "MIXW": 5, "M4R": 6, "PITR": 7, "BAL": 8}[row["candidate"]],
        ),
    )
    selected = selected_row["candidate"]
    for row in candidate_audit:
        row["within_one_standard_error"] = bool(row in robust_set)
        row["selected"] = row["candidate"] == selected
        row["one_standard_error"] = finite(one_standard_error)
    backtest, holdings = candidate_runs[selected]
    metrics = {
        split: performance(backtest.loc[backtest["split"].eq(split)])
        for split in ("train", "validation", "test")
    }
    metrics["all"] = performance(backtest)
    checks = {
        "train_sharpe_positive": bool((metrics["train"]["sharpe"] or -999) > 0),
        "train_excess_positive": bool((metrics["train"]["annual_excess"] or -999) > 0),
        "validation_sharpe_positive": bool((metrics["validation"]["sharpe"] or -999) > 0),
        "validation_excess_positive": bool((metrics["validation"]["annual_excess"] or -999) > 0),
        "test_sharpe_positive": bool((metrics["test"]["sharpe"] or -999) > 0),
        "test_excess_positive": bool((metrics["test"]["annual_excess"] or -999) > 0),
    }

    latest_signal = source.signals[-1]
    latest_execution = source.execution_dates[latest_signal]
    latest = labels_by_signal[latest_signal]
    latest_features = features_by_signal[latest_signal]
    return_matrix = period_returns.pivot(index="signal_date", columns="cell", values="cell_return").reindex(columns=CELL_CODES)
    latest_scores = candidate_scores(selected, latest_signal, return_matrix, latest_features)
    if latest_scores is None:
        raise RuntimeError("insufficient style-box history for latest ranking")
    latest_selected = list(latest_scores.sort_values(ascending=False, kind="stable").head(TOP_N).index)
    latest_history = return_matrix.loc[return_matrix.index < latest_signal, list(CELL_CODES)]
    latest_weights = style_box_weights(selected, latest_selected, latest_scores, latest_history)
    selected_cells = set(latest_selected)
    cell_summaries = {row["cell"]: row for row in latest_cell_summary(latest, float(latest["circ_mv"].sum()))}
    ranking: list[dict[str, Any]] = []
    for rank, (cell, score) in enumerate(latest_scores.sort_values(ascending=False, kind="stable").items(), start=1):
        summary = cell_summaries[cell]
        ranking.append({
            "rank": rank,
            "code": cell,
            "name": cell,
            "size": summary["size"],
            "style": summary["style"],
            "score": finite(score),
            "selected": cell in selected_cells,
            "weight": finite(float(latest_weights.loc[cell])) if cell in selected_cells else 0.0,
            "stock_count": summary["stock_count"],
            "cap_share": summary["cap_share"],
            "components": {
                "value_score": summary["value_score"],
                "growth_score": summary["growth_score"],
                "dividend_yield": summary["dividend_yield"],
            },
        })

    latest_codes = latest["ts_code"].tolist()
    unique_codes = len(set(latest_codes))
    current_holding = {
        "signal_date": iso(latest_signal),
        "execution_date": iso(latest_execution),
        "names": [row["name"] for row in ranking if row["selected"]],
        "codes": [row["code"] for row in ranking if row["selected"]],
        "weight": finite(float(latest_weights.loc[latest_selected].mean())),
        "weights": {cell: finite(float(latest_weights.loc[cell])) for cell in latest_selected},
        "turnover": None,
        "status": "planned" if latest_execution > source.data_as_of else "executed",
    }
    holdings_with_current = holdings + [current_holding]
    previous_signal = source.signals[-2]
    return {
        "source": (
            "research_warehouse.db：security_master、stock_valuation_daily、"
            "stock_ohlcv_daily、financial_report_visible（全程只读）"
        ),
        "start": iso(backtest["execution_date"].map(compact).min()) if not backtest.empty else None,
        "end": iso(source.signals[-1]),
        "count": 12,
        "benchmark": "12风格箱等权",
        "model_type": "季度个股PIT晨星启发3×4风格箱轮动",
        "frequency": "quarterly",
        "latest_signal_date": iso(latest_signal),
        "latest_execution_date": iso(latest_execution),
        "size_method": {
            "input": "季度末流通市值",
            "rule": "按流通市值降序累计：0–70%大盘、70–90%中盘、90–100%小盘",
            "rebalance": "每季度",
            "scope": "全体合格A股，上市满180日，排除ST/*ST及无有效价格/流通市值证券",
        },
        "style_method": {
            "scope": "在大盘/中盘/小盘内部独立比较",
            "value": "E/P、B/P、S/P三个横截面分位数等权；缺失项记中性0.5",
            "growth": "可见日营业利润、营业收入与归母净利润同比三个横截面分位数等权；仅作股票风格识别，不进入31行业景气字段",
            "dividend": "当前股息率位于同规模组前30%，且过去8个季度至少6次股息率为正；优先标记红利",
            "exclusive_assignment": "非红利股票按成长分−价值分排序，以流通市值累计30%/70%切为价值/均衡/成长",
            "disclaimer": "本模型为晨星方法启发的A股3×4扩展，并非Morningstar官方分类或复刻。",
        },
        "factor_contract": {
            "signal": "季度末交易日收盘后",
            "execution": "下一交易日收盘",
            "accounting_pit": "financial_report_visible.visible_date <= signal_date",
            "portfolio": "验证集选择候选；每季只做多Top3风格箱；基础候选等权，风险候选按得分置信度和近四季波动加权；单箱内部流通市值加权并尽可能限制单股8%",
            "cost": "风格箱层换手×10bp",
            "splits": {
                "train": ["2012-01-01", TRAIN_END],
                "validation": [VALIDATION_START, VALIDATION_END],
                "test": [TEST_START, "2099-12-31"],
            },
            "test_policy": "测试集只报告，绝不参与候选选择或阈值调整。",
        },
        "frequencies": {
            "quarterly": {
                "frequency": "quarterly",
                "selected_candidate": selected,
                "selected_window": "2Q/4Q/PIT及风险调整组合候选，验证集冻结择优",
                "selected_weights": [finite(float(latest_weights.loc[cell])) for cell in latest_selected],
                "selection_rule": (
                    "九个预注册候选使用2019–2021验证集；在最高超额夏普一倍标准误差内，"
                    "优先选择验证期换手最低的简单候选；2022年后仅报告"
                ),
                "current_regime": " / ".join(current_holding["names"]),
                "latest_signal_date": iso(latest_signal),
                "latest_execution_date": iso(latest_execution),
                "candidate_audit": candidate_audit,
                "metrics": metrics,
                "gate": {
                    "status": "pass" if all(checks.values()) else "review",
                    "checks": checks,
                    "policy": "只报告真实结果；验证集选型、测试集冻结，未通过项保持review。",
                },
                "nav": build_nav(backtest),
                "ranking": ranking,
                "holdings": holdings_with_current,
            }
        },
        "data_quality": {
            "latest_eligible_stock_count": int(len(latest)),
            "latest_labelled_stock_count": int(len(latest_codes)),
            "latest_unique_stock_count": int(unique_codes),
            "unclassified_stock_count": int(latest["cell"].isna().sum()),
            "duplicate_label_count": int(len(latest_codes) - unique_codes),
            "cell_count": int(latest["cell"].nunique()),
            "min_cell_stock_count": int(latest.groupby("cell").size().min()),
            "quarter_count": int(len(source.signals)),
            "completed_return_quarters": int(period_returns["signal_date"].nunique()),
            "mean_return_weight_coverage": finite(mean_coverage),
            "latest_visible_financial_coverage": finite(latest["visible_date"].notna().mean()),
            "latest_three_value_factor_coverage": finite(latest["value_factor_count"].eq(3).mean()),
            "latest_three_growth_factor_coverage": finite(latest["growth_factor_count"].eq(3).mean()),
        },
        "stock_labels": stock_label_payload(latest),
        "cells": list(cell_summaries.values()),
        "cell_history": cell_history,
        "migration": {
            "from_signal": iso(previous_signal),
            "to_signal": iso(latest_signal),
            **migration_payload(labels_by_signal[previous_signal], latest),
        },
        "sources": [
            {
                "name": "Morningstar Equity Style Box",
                "url": "https://advisor.morningstar.com/Enterprise/VTC/MorningstarEquityStyleBoxMethodology.pdf",
                "use": "股票级构件、规模累计市值70/20/10及价值/成长相对比较框架",
                "status": "methodology_reference",
            },
            {
                "name": "Morningstar Style Box methodology update",
                "url": "https://www.morningstar.com/whats-new/upcoming-update-to-the-morningstar-style-box-methodology",
                "use": "市值加权标准化与中性中心思想",
                "status": "methodology_reference",
            },
            {
                "name": "国证红利指数编制方案",
                "url": "https://www.cnindex.com.cn/docs/gz_399321.pdf",
                "use": "A股稳定分红筛选的本土化参考",
                "status": "methodology_reference",
            },
            {
                "name": "本地研究数据库",
                "tables": [
                    "security_master",
                    "stock_valuation_daily",
                    "stock_ohlcv_daily",
                    "financial_report_visible",
                ],
                "use": "股票范围、流通市值/估值/股息率、复权价格、PIT财务可见日",
                "status": "live",
                "as_of": iso(source.signals[-1]),
            },
        ],
    }


def update_snapshot(database: Path, snapshot: Path) -> dict[str, Any]:
    original = json.loads(snapshot.read_text(encoding="utf-8"))
    source = load_sources(database)
    style_payload = apply_monthly_style_overlay(build_style_payload(source), database, source)
    style_payload = apply_six_dimension_style_overlay(style_payload, Path(__file__).resolve().parents[2], source)
    snapshot_as_of = str(original.get("as_of") or "")
    for holding in style_payload.get("frequencies", {}).get("quarterly", {}).get("holdings", []):
        if holding.get("status") == "planned" and str(holding.get("execution_date") or "") <= snapshot_as_of:
            holding["status"] = "executed"
    original["style"] = style_payload
    original["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    original.setdefault("method", {})["style_universe"] = "全体合格A股季度个股标签"
    original["method"]["style_box"] = "大/中/小盘×成长/均衡/价值/红利，共12个互斥且穷尽的风格箱"
    original["method"]["style_frequency"] = "monthly_rotation_with_quarterly_labels"
    original["method"]["style_portfolio"] = "季度更新3×4风格域标签；月度聚合域内股票五因子，原子因子经RankIC与Top-Bottom多空收益检验后训练验证选择候选权重和调仓数量；基准采用标准市值/风格指数代理"
    original["method"]["style_timing"] = "月末收盘形成轮动信号；下一交易日收盘执行；风格域标签按最近季度标签沿用"
    original["method"]["style_splits"] = {
        "train": ["2012-01-01", TRAIN_END],
        "validation": [VALIDATION_START, VALIDATION_END],
        "test": [TEST_START, "2099-12-31"],
    }
    original["method"]["style_test_policy"] = "训练/验证选择预注册五因子候选；2022年后冻结只报告"
    original["source_audit"] = [
        row for row in original.get("source_audit", [])
        if row.get("purpose") not in {"季度个股3×4风格箱与轮动回测", "季度3×4风格域标签与月度六维轮动回测", "季度3×4风格域标签与月度五因子轮动回测"}
    ]
    original["source_audit"].append({
        "source": "research_warehouse.db（只读）+ Morningstar/国证官方方法文件 + AKShare中证指数公开行情",
        "purpose": "季度3×4风格域标签、月度五因子轮动回测与标准指数基准",
        "status": "ok",
        "as_of": original["style"]["end"],
        "note": "本模型为晨星启发的A股扩展；标签季度更新，轮动信号月度更新；风格轮动基准采用可复现的标准市值/风格指数组合，不使用测试期挑弱基准。",
    })
    temporary = snapshot.with_suffix(snapshot.suffix + ".tmp")
    temporary.write_text(json.dumps(original, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(snapshot)
    return original


def project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "database").is_dir() and (parent / "board").is_dir():
            return parent
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=root / "database" / "research_warehouse.db",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=root / "board" / "quant_strategy_agent" / "data" / "rotation_snapshot.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = update_snapshot(args.database, args.snapshot)
    style = payload["style"]
    quarterly = style["frequencies"]["quarterly"]
    print(json.dumps({
        "status": "ok",
        "signal": style["latest_signal_date"],
        "execution": style["latest_execution_date"],
        "stocks": style["data_quality"]["latest_labelled_stock_count"],
        "cells": style["data_quality"]["cell_count"],
        "selected_candidate": quarterly["selected_candidate"],
        "gate": quarterly["gate"]["status"],
        "test": quarterly["metrics"]["test"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
