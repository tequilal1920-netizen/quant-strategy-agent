"""Build the v4 industry-specific prosperity rotation release.

Hard constraints enforced by this builder:

* exactly 31 SW level-1 industries and exactly eight contracts per industry;
* no universal accounting, valuation, price-momentum or crowding fields;
* at least six independently named live business observations per industry;
* point-in-time availability dates are separate from observation dates;
* train/validation/test are disjoint and the test set never selects a model;
* Top10 long-only, equal weighted, compared with the same-day 31-industry
  equal-weight benchmark at monthly and weekly rebalance frequencies.

The CMB workbooks on G: are opened read-only.  Only the generated JSON inside
this project is written.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from catalog import (
    DIRECT_PATTERNS,
    EVENT_BLUEPRINTS,
    FORBIDDEN_PATTERNS,
    INDUSTRY_CODES,
)


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
DATA_DIR = PROJECT_ROOT / "board" / "quant_strategy_agent" / "data"
CACHE_DIR = PROJECT_ROOT / "output" / "industry_rotation" / "cache" / "market"
CMB_DATA_RAW = os.environ.get("INDUSTRY_ROTATION_SOURCE_XLSX", "").strip()
CMB_DATA = Path(CMB_DATA_RAW) if CMB_DATA_RAW else None
KEYWORD_AUDIT = PROJECT_ROOT / "output" / "industry_rotation" / "evidence" / "rotation_keyword_columns_v4.json"
WAREHOUSE = PROJECT_ROOT / "database" / "research_warehouse.db"
OLD_SNAPSHOT = DATA_DIR / "rotation_snapshot.json"
OUTPUT = DATA_DIR / "rotation_snapshot.json"

SPLITS = {
    "train": ("2015-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2021-12-31"),
    "test": ("2022-01-01", "2099-12-31"),
}
EVENT_INDUSTRIES = set(EVENT_BLUEPRINTS)
EXCLUDED_NEWS = (
    "营业收入", "营收", "利润", "净利润", "ROE", "ROA", "毛利率", "负债率",
    "主力资金", "涨停", "跌停", "股价", "换手率", "市盈率", "市净率",
)


@dataclass
class SeriesContract:
    industry: str
    name: str
    variable: str
    source: str
    source_spec: str
    frequency: str
    unit: str
    transform: str
    availability_rule: str
    raw: pd.Series
    source_kind: str
    observation_field: str
    status: str = "live"


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", text).strip("_")


def _concept(name: str) -> str:
    value = re.sub(r"(累计值|当期值|累计同比|当月同比|累计增长|当月值)", "", name)
    value = re.sub(r"\([^)]*\)", "", value)
    return re.sub(r"[_：:\s]", "", value)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_for(name: str, sheet: str) -> tuple[str, str, str, str]:
    if "主力连续" in name:
        return (
            "Wind商品期货日行情 / RQData主力连续",
            "Wind.CCOMMODITYFUTURESEODPRICES；RQData futures.get_dominant_price 交叉校验",
            "周",
            "交易所报价单位",
        )
    if "BDI" in name or "BCTI" in name or "BDTI" in name:
        return ("Baltic Exchange / AKShare", "AKShare macro_shipping_*；Wind 航运指数交叉校验", "周", "点")
    if "成品油调价" in name:
        return ("国家发改委 / AKShare", "AKShare energy_oil_hist；发改委调价公告", "周", "元/吨")
    if "汽车销量" in name:
        return ("盖世汽车产业库", "CMB已缓存盖世汽车月度车企/品牌销量；正式生产由 iFinD 汽车产业链复核", "月", "辆")
    if any(key in name for key in ("LPR", "Shibor")):
        return ("全国银行间同业拆借中心 / AKShare", "AKShare macro_china_lpr / rate_interbank；Wind 利率库复核", "月", "%")
    if any(key in name for key in ("M1", "M2", "人民币贷款")):
        return ("中国人民银行 / AKShare", "AKShare macro_china_money_supply / macro_china_new_financial_credit", "月", "亿元")
    if "融资余额" in name:
        return ("沪深交易所 / RQData", "RQData融资融券日表；交易所官方汇总交叉校验", "周", "亿元")
    if "原保险保费" in name:
        return ("国家金融监督管理总局 / AKShare", "AKShare macro_china_insurance；界面简称“原保险保费规模”", "月", "亿元")
    if "手机出货" in name:
        return ("中国信通院 / AKShare", "AKShare macro_china_mobile_number；信通院月报", "月", "万部")
    if "物流景气" in name or "建筑业景气" in name:
        return ("东方财富行业指标库", "Eastmoney RPT_INDUSTRY_INDEX；iFinD EDB 交叉校验", "月", "点")
    if "农产品价格指数" in name:
        return ("农业农村部 / AKShare", "AKShare macro_china_agricultural_product", "月", "点")
    if "用电量" in name:
        return ("国家能源局 / AKShare", "AKShare macro_china_society_electricity；Wind EDB 交叉校验", "月", "亿千瓦时")
    return (
        "国家统计局",
        "国家统计局工业产品/零售/运输月度库；CMB历史缓存；iFinD THS_EDB 或 Wind EDB 生产复核",
        "月" if sheet == "monthly" else "周",
        "原表单位",
    )


def _load_cmb_sheets() -> dict[str, pd.DataFrame]:
    if CMB_DATA is None or not CMB_DATA.is_file():
        raise FileNotFoundError("Set INDUSTRY_ROTATION_SOURCE_XLSX to the source workbook path.")
    frames: dict[str, pd.DataFrame] = {}
    workbook = pd.ExcelFile(CMB_DATA)
    for sheet in workbook.sheet_names:
        frame = pd.read_excel(CMB_DATA, sheet_name=sheet, index_col=0)
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        frame = frame.loc[frame.index.notna()]
        frames[str(sheet)] = frame
    return frames


def _select_direct_contracts(frames: dict[str, pd.DataFrame]) -> dict[str, list[SeriesContract]]:
    audit = _read_json(KEYWORD_AUDIT)
    selected: dict[str, list[SeriesContract]] = {industry: [] for industry in INDUSTRY_CODES}
    for industry, patterns in DIRECT_PATTERNS.items():
        rows = audit.get(industry, [])
        used_columns: set[str] = set()
        used_concepts: set[str] = set()
        for pattern in patterns:
            matches = [
                row for row in rows
                if pattern.lower() in str(row["column"]).lower()
                and str(row["last_date"]) >= "2025-07-01"
                and int(row["observations"]) >= 36
                and str(row["column"]) not in used_columns
                and _concept(str(row["column"])) not in used_concepts
                and not any(word.lower() in str(row["column"]).lower() for word in FORBIDDEN_PATTERNS)
                and "碳成交额" not in str(row["column"])
            ]
            if not matches:
                continue
            matches.sort(
                key=lambda row: (
                    "当期值" in str(row["column"]) or "当月值" in str(row["column"]),
                    str(row["last_date"]),
                    int(row["observations"]),
                ),
                reverse=True,
            )
            row = matches[0]
            sheet, column = str(row["sheet"]), str(row["column"])
            if sheet not in frames or column not in frames[sheet]:
                continue
            raw = pd.to_numeric(frames[sheet][column], errors="coerce").dropna().sort_index()
            source, source_spec, frequency, unit = _source_for(column, sheet)
            selected[industry].append(
                SeriesContract(
                    industry=industry,
                    name=re.sub(r"_.*?(?=产量|商品零售|房地产|$)", "_", column),
                    variable=f"cmb_{_slug(industry)}_{len(selected[industry]) + 1:02d}",
                    source=source,
                    source_spec=source_spec,
                    frequency=frequency,
                    unit=unit,
                    transform="原始业务量/价格经季节同比或滚动稳健标准化；不使用行业指数价格",
                    availability_rule="周频观察日后第1个交易日" if frequency == "周" else "统计期末+25个自然日（保守可用日）",
                    raw=raw,
                    source_kind="direct",
                    observation_field=column,
                )
            )
            used_columns.add(column)
            used_concepts.add(_concept(column))
    return selected


def _event_rows(industry: str, blueprints: list[tuple[str, list[str]]]) -> pd.DataFrame:
    keywords = sorted({word for _, words in blueprints for word in words})
    if not keywords:
        return pd.DataFrame(columns=["publish_date", "news_id", "headline"])
    uri = f"file:{WAREHOUSE.as_posix()}?mode=ro"
    clauses = " OR ".join("n.headline LIKE ?" for _ in keywords)
    exclusions = " AND ".join("n.headline NOT LIKE ?" for _ in EXCLUDED_NEWS)
    sql = f"""
        SELECT DISTINCT n.publish_date, n.news_id, n.headline
        FROM news_event_daily n
        JOIN sw_l1_industry_daily m
          ON n.subject_code = m.ts_code
         AND n.publish_date >= m.start_date
         AND n.publish_date <= COALESCE(m.end_date, '99991231')
        WHERE n.subject_type = 'stock'
          AND m.industry_name = ?
          AND n.publish_date BETWEEN '20120101' AND '20991231'
          AND ({clauses})
          AND {exclusions}
    """
    params: list[str] = [industry] + [f"%{word}%" for word in keywords] + [f"%{word}%" for word in EXCLUDED_NEWS]
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        frame = pd.read_sql_query(sql, connection, params=params)
    return frame


def _event_contracts(industry: str, blueprints: list[tuple[str, list[str]]]) -> list[SeriesContract]:
    rows = _event_rows(industry, blueprints)
    if not rows.empty:
        rows["date"] = pd.to_datetime(rows["publish_date"], format="%Y%m%d", errors="coerce")
        rows = rows.loc[rows["date"].notna()].copy()
    result: list[SeriesContract] = []
    for index, (label, keywords) in enumerate(blueprints, start=1):
        if rows.empty:
            raw = pd.Series(dtype=float)
        else:
            mask = rows["headline"].astype(str).apply(lambda value: any(word in value for word in keywords))
            daily = rows.loc[mask].groupby("date")["news_id"].nunique().astype(float)
            event_end = pd.to_datetime(rows["date"], errors="coerce").max().normalize()
            if pd.isna(event_end):
                raise RuntimeError("event stream has no valid observation date")
            full = pd.date_range("2012-01-01", event_end, freq="D")
            raw = daily.reindex(full, fill_value=0.0).resample("W-FRI").sum()
        status = "live" if len(raw) >= 156 and float(raw.sum()) > 0 else "metadata_only"
        result.append(
            SeriesContract(
                industry=industry,
                name=label,
                variable=f"event_{_slug(industry)}_{index:02d}",
                source="本地研究仓库公开新闻事件流 + 申万PIT行业映射",
                source_spec=f"news_event_daily JOIN sw_l1_industry_daily；标题关键词={','.join(keywords)}；排除财务与股价新闻",
                frequency="周",
                unit="条/周",
                transform="周事件数的13周均值与104周历史稳健标准化",
                availability_rule="新闻发布日期后第1个交易日；历史行业归属按start_date/end_date逐日连接",
                raw=raw,
                source_kind="event",
                observation_field="headline去重事件数",
                status=status,
            )
        )
    return result


def _build_contracts(frames: dict[str, pd.DataFrame]) -> dict[str, list[SeriesContract]]:
    contracts = _select_direct_contracts(frames)
    for industry in INDUSTRY_CODES:
        if len(contracts[industry]) < 8:
            events = _event_contracts(industry, EVENT_BLUEPRINTS.get(industry, []))
            events = sorted(
                events,
                key=lambda item: (item.status == "live" and len(item.raw) >= 36),
                reverse=True,
            )
            contracts[industry].extend(events[: max(0, 8 - len(contracts[industry]))])
        if len(contracts[industry]) != 8:
            raise RuntimeError(f"{industry}: expected 8 contracts, got {len(contracts[industry])}")
        live = sum(contract.status == "live" and len(contract.raw) >= 36 for contract in contracts[industry])
        if live < 6:
            raise RuntimeError(f"{industry}: only {live} live contracts")
        names = [contract.name for contract in contracts[industry]]
        if len(set(names)) != len(names):
            raise RuntimeError(f"{industry}: duplicate indicator names")
        for contract in contracts[industry]:
            text = f"{contract.name}|{contract.observation_field}"
            bad = [word for word in FORBIDDEN_PATTERNS if word.lower() in text.lower()]
            if bad:
                raise RuntimeError(f"{industry}/{contract.name}: forbidden fields {bad}")
    return contracts


def _robust_z(series: pd.Series, window: int, minimum: int) -> pd.Series:
    median = series.rolling(window, min_periods=minimum).median()
    mad = (series - median).abs().rolling(window, min_periods=minimum).median()
    scale = mad.replace(0, np.nan) * 1.4826
    return (series - median).div(scale).clip(-4, 4)


def _feature(contract: SeriesContract) -> pd.Series:
    raw = pd.to_numeric(contract.raw, errors="coerce").dropna().sort_index()
    if raw.empty:
        return raw
    if contract.source_kind == "event":
        smoothed = raw.rolling(13, min_periods=4).mean()
        return _robust_z(smoothed, 104, 52)
    if contract.frequency == "周":
        level = np.log(raw.where(raw > 0)).replace([np.inf, -np.inf], np.nan)
        return _robust_z(level, 156, 52)
    if any(key in contract.observation_field for key in ("LPR", "Shibor", "指数", "客座率")):
        return _robust_z(raw, 60, 36)
    seasonal = raw.pct_change(12, fill_method=None).replace([np.inf, -np.inf], np.nan)
    return _robust_z(seasonal, 60, 36)


def _available_index(contract: SeriesContract, index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if contract.frequency == "周":
        return pd.DatetimeIndex(index) + pd.offsets.BDay(1)
    return pd.DatetimeIndex(index) + pd.to_timedelta(25, unit="D") + pd.offsets.BDay(0)


def _load_closes() -> pd.DataFrame:
    pieces: list[pd.Series] = []
    for industry, code in INDUSTRY_CODES.items():
        frame = pd.read_csv(CACHE_DIR / f"sw_{code}.csv", parse_dates=["date"])
        series = pd.to_numeric(frame["close"], errors="coerce")
        series.index = pd.DatetimeIndex(frame["date"])
        pieces.append(series.rename(industry))
    close = pd.concat(pieces, axis=1).sort_index()
    return close.loc["2012-01-01":].dropna(how="all")


def _align_features(
    contracts: dict[str, list[SeriesContract]],
    trading_index: pd.DatetimeIndex,
) -> tuple[dict[str, dict[str, pd.Series]], dict[str, dict[str, float]]]:
    aligned: dict[str, dict[str, pd.Series]] = {}
    diagnostics: dict[str, dict[str, float]] = {}
    for industry, items in contracts.items():
        aligned[industry] = {}
        diagnostics[industry] = {}
        future = _load_industry_forward_return(industry, trading_index)
        for contract in items:
            feature = _feature(contract)
            feature.index = _available_index(contract, feature.index)
            daily = feature[~feature.index.duplicated(keep="last")].reindex(trading_index).ffill(limit=80 if contract.frequency == "月" else 25)
            aligned[industry][contract.variable] = daily
            sample = pd.concat([daily.rename("x"), future.rename("y")], axis=1).loc[SPLITS["train"][0] : SPLITS["train"][1]].dropna()
            ic = float(sample["x"].corr(sample["y"], method="spearman")) if len(sample) >= 120 else 0.0
            diagnostics[industry][contract.variable] = ic if math.isfinite(ic) else 0.0
    return aligned, diagnostics


_CLOSE_CACHE: pd.DataFrame | None = None


def _load_industry_forward_return(industry: str, index: pd.DatetimeIndex) -> pd.Series:
    global _CLOSE_CACHE
    if _CLOSE_CACHE is None:
        _CLOSE_CACHE = _load_closes()
    close = _CLOSE_CACHE[industry].reindex(index).ffill()
    return close.shift(-21).div(close).sub(1.0)


def _candidate_scores(
    contracts: dict[str, list[SeriesContract]],
    aligned: dict[str, dict[str, pd.Series]],
    diagnostics: dict[str, dict[str, float]],
    index: pd.DatetimeIndex,
) -> dict[str, pd.DataFrame]:
    outputs = {name: pd.DataFrame(index=index, columns=list(INDUSTRY_CODES), dtype=float) for name in ("C1_equal", "C2_reliability", "C3_train_ic")}
    for industry, items in contracts.items():
        frame = pd.DataFrame(aligned[industry])
        signs = {item.variable: (1.0 if diagnostics[industry].get(item.variable, 0.0) >= 0 else -1.0) for item in items}
        signed = frame.mul(pd.Series(signs), axis=1)
        weights_equal = pd.Series(1.0, index=frame.columns)
        weights_reliability = pd.Series({item.variable: 1.0 if item.source_kind == "direct" else 0.72 for item in items})
        weights_ic = pd.Series({
            item.variable: (0.35 + min(0.15, abs(diagnostics[industry].get(item.variable, 0.0))) * 6.0)
            * (1.0 if item.source_kind == "direct" else 0.75)
            for item in items
        })
        for name, weights in (
            ("C1_equal", weights_equal),
            ("C2_reliability", weights_reliability),
            ("C3_train_ic", weights_ic),
        ):
            numerator = signed.mul(weights, axis=1).sum(axis=1, min_count=4)
            denominator = signed.notna().mul(weights, axis=1).sum(axis=1).replace(0, np.nan)
            outputs[name][industry] = numerator.div(denominator)
    for name in outputs:
        outputs[name] = outputs[name].rank(axis=1, pct=True, method="average").where(outputs[name].notna())
    return outputs


def _signal_dates(index: pd.DatetimeIndex, frequency: str) -> list[pd.Timestamp]:
    labels = index.to_period("M") if frequency == "monthly" else index.to_period("W-FRI")
    values = pd.Series(index=index, data=index)
    return [pd.Timestamp(value) for value in values.groupby(labels).max().tolist()]


def _targets(score: pd.DataFrame, frequency: str) -> dict[pd.Timestamp, pd.Series]:
    targets: dict[pd.Timestamp, pd.Series] = {}
    for date in _signal_dates(score.index, frequency):
        row = score.loc[date].dropna()
        if len(row) < 25:
            continue
        chosen = row.nlargest(10).index
        target = pd.Series(0.0, index=score.columns)
        target.loc[chosen] = 0.1
        targets[date] = target
    return targets


def _simulate(close: pd.DataFrame, targets: dict[pd.Timestamp, pd.Series], cost_rate: float = 0.001) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    returns = close.pct_change(fill_method=None)
    columns = list(close.columns)
    execution: dict[pd.Timestamp, tuple[pd.Timestamp, pd.Series]] = {}
    for signal_date, target in targets.items():
        position = returns.index.searchsorted(signal_date, side="right")
        if position < len(returns.index):
            execution[pd.Timestamp(returns.index[position])] = (signal_date, target)
    weights = np.zeros(len(columns), dtype=float)
    benchmark_weights = np.zeros(len(columns), dtype=float)
    nav = benchmark_nav = 1.0
    previous_nav = previous_benchmark = 1.0
    started = False
    rows: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for date in returns.index:
        values = returns.loc[date].fillna(0.0).to_numpy(dtype=float)
        if started:
            portfolio_return = float(np.dot(weights, values))
            benchmark_return = float(np.dot(benchmark_weights, values))
            nav *= 1.0 + portfolio_return
            benchmark_nav *= 1.0 + benchmark_return
            if 1.0 + portfolio_return != 0:
                weights = weights * (1.0 + values) / (1.0 + portfolio_return)
            if 1.0 + benchmark_return != 0:
                benchmark_weights = benchmark_weights * (1.0 + values) / (1.0 + benchmark_return)
        turnover = 0.0
        if date in execution:
            signal_date, target = execution[date]
            target_values = target.reindex(columns).fillna(0.0).to_numpy(dtype=float)
            benchmark_target = np.full(len(columns), 1.0 / len(columns))
            turnover = float(np.abs(target_values - weights).sum())
            benchmark_turnover = float(np.abs(benchmark_target - benchmark_weights).sum())
            nav *= max(0.0, 1.0 - cost_rate * turnover)
            benchmark_nav *= max(0.0, 1.0 - cost_rate * benchmark_turnover)
            weights, benchmark_weights = target_values, benchmark_target
            started = True
            holdings.append({
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "execution_date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "names": [columns[i] for i, value in enumerate(weights) if value > 0],
                "weight": 0.1,
                "turnover": round(turnover, 6),
            })
        rows.append({
            "date": pd.Timestamp(date),
            "nav": nav,
            "benchmark_nav": benchmark_nav,
            "return": nav / previous_nav - 1.0,
            "benchmark_return": benchmark_nav / previous_benchmark - 1.0,
            "turnover": turnover,
        })
        previous_nav, previous_benchmark = nav, benchmark_nav
    frame = pd.DataFrame(rows).set_index("date")
    first = min(execution) if execution else frame.index.max()
    return frame.loc[first:], holdings


def _metrics(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    sample = frame.loc[start:end]
    if sample.empty:
        return {"status": "unavailable", "observations": 0}
    strategy = sample["return"].astype(float)
    benchmark = sample["benchmark_return"].astype(float)
    excess = strategy - benchmark
    def annual(series: pd.Series) -> float | None:
        if series.empty or (1 + series).le(0).any():
            return None
        return float((1 + series).prod() ** (252 / len(series)) - 1)
    def sharpe(series: pd.Series) -> float | None:
        std = float(series.std(ddof=1))
        return float(np.sqrt(252) * series.mean() / std) if std > 0 else None
    local_nav = (1 + strategy).cumprod()
    drawdown = local_nav.div(local_nav.cummax()).sub(1)
    ann, bench = annual(strategy), annual(benchmark)
    return {
        "status": "ok",
        "start": sample.index.min().strftime("%Y-%m-%d"),
        "end": sample.index.max().strftime("%Y-%m-%d"),
        "observations": int(len(sample)),
        "annual_return": ann,
        "benchmark_annual_return": bench,
        "annual_excess": (ann - bench) if ann is not None and bench is not None else None,
        "sharpe": sharpe(strategy),
        "excess_sharpe": sharpe(excess),
        "max_drawdown": float(drawdown.min()),
        "annual_turnover": float(sample["turnover"].mean() * 252),
    }


def _all_metrics(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result = {name: _metrics(frame, start, end) for name, (start, end) in SPLITS.items()}
    result["all"] = _metrics(frame, frame.index.min().strftime("%Y-%m-%d"), frame.index.max().strftime("%Y-%m-%d"))
    return result


def _frequency_payload(close: pd.DataFrame, scores: dict[str, pd.DataFrame], frequency: str) -> tuple[dict[str, Any], pd.DataFrame]:
    evaluated: list[tuple[float, str, pd.DataFrame, list[dict[str, Any]], dict[str, Any]]] = []
    audit: list[dict[str, Any]] = []
    for name, score in scores.items():
        simulation, holdings = _simulate(close, _targets(score, frequency))
        metrics = _all_metrics(simulation)
        validation = metrics["validation"].get("excess_sharpe")
        train = metrics["train"].get("excess_sharpe")
        objective = float(validation) if validation is not None else -999.0
        if train is None or train < -0.25:
            objective -= 1.0
        audit.append({"candidate": name, "train_excess_sharpe": train, "validation_excess_sharpe": validation, "objective": objective})
        evaluated.append((objective, name, simulation, holdings, metrics))
    evaluated.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _, selected, simulation, holdings, metrics = evaluated[0]
    score = scores[selected]
    latest_date = score.dropna(how="all").index.max()
    row = score.loc[latest_date].dropna().sort_values(ascending=False)
    ranking = [
        {"rank": rank, "code": INDUSTRY_CODES[name], "name": name, "score": round(float(value), 6), "selected": rank <= 10, "weight": 0.1 if rank <= 10 else 0.0, "components": {}}
        for rank, (name, value) in enumerate(row.items(), start=1)
    ]
    payload = {
        "frequency": frequency,
        "selected_candidate": selected,
        "selection_rule": "候选只使用训练集估计方向/权重，按验证集超额夏普选择；测试集冻结后一次性评估",
        "candidate_audit": audit,
        "metrics": metrics,
        "gate": {
            "status": "pass" if all((metrics[s].get("sharpe") or -999) > 0 for s in ("train", "validation", "test")) else "review",
            "policy": "真实结果原样披露；不以测试集反向调参，不承诺或伪造夏普。",
        },
        "nav": [
            {"date": date.strftime("%Y-%m-%d"), "strategy": round(float(value.nav), 6), "benchmark": round(float(value.benchmark_nav), 6), "excess": round(float(value.nav / value.benchmark_nav), 6)}
            for date, value in simulation.iterrows()
        ],
        "ranking": ranking,
        "holdings": holdings[-52:],
    }
    return payload, score


def _indicator_payload(
    contract: SeriesContract,
    feature: pd.Series,
    ic: float,
    latest_score_date: pd.Timestamp,
) -> dict[str, Any]:
    raw = pd.to_numeric(contract.raw, errors="coerce").dropna().sort_index()
    available_feature = feature.dropna()
    sign = 1 if ic >= 0 else -1
    chart = raw.tail(180 if contract.frequency == "周" else 120)
    last_observation = raw.index.max() if not raw.empty else None
    last_available = _available_index(contract, pd.DatetimeIndex([last_observation]))[0] if last_observation is not None else None
    value = float(available_feature.iloc[-1]) if not available_feature.empty else None
    return {
        "name": contract.name,
        "variable": contract.variable,
        "source": contract.source,
        "source_spec": contract.source_spec,
        "field": contract.observation_field,
        "frequency": contract.frequency,
        "unit": contract.unit,
        "transform": contract.transform,
        "availability_rule": contract.availability_rule,
        "status": contract.status,
        "model_eligible": contract.status == "live" and len(raw) >= 36,
        "model_note": "仅在available_date不晚于信号日时入模；缺失不补0，剩余可见字段重新归一化。",
        "direction": "训练集单调正向" if sign > 0 else "训练集单调反向",
        "train_spearman_ic": round(float(ic), 6),
        "first_date": raw.index.min().strftime("%Y-%m-%d") if not raw.empty else None,
        "last_date": last_observation.strftime("%Y-%m-%d") if last_observation is not None else None,
        "last_available_date": pd.Timestamp(last_available).strftime("%Y-%m-%d") if last_available is not None else None,
        "history_years": round((last_observation - raw.index.min()).days / 365.25, 1) if last_observation is not None and len(raw) else 0,
        "latest_feature": round(value, 6) if value is not None and math.isfinite(value) else None,
        "contribution": round(sign * value / 8.0, 6) if value is not None and math.isfinite(value) else None,
        "data": [{"date": pd.Timestamp(date).strftime("%Y-%m-%d"), "value": round(float(value), 6)} for date, value in chart.items() if pd.notna(value)],
    }


def _high_frequency_payload(
    contracts: dict[str, list[SeriesContract]],
    aligned: dict[str, dict[str, pd.Series]],
    diagnostics: dict[str, dict[str, float]],
    ranking: list[dict[str, Any]],
    latest_score_date: pd.Timestamp,
) -> dict[str, Any]:
    ranks = {row["name"]: row for row in ranking}
    industries: list[dict[str, Any]] = []
    for industry, items in contracts.items():
        indicators = [
            _indicator_payload(item, aligned[industry][item.variable], diagnostics[industry][item.variable], latest_score_date)
            for item in items
        ]
        live = sum(item["status"] == "live" and item["model_eligible"] for item in indicators)
        rank = ranks.get(industry, {})
        industries.append({
            "industry": industry,
            "code": INDUSTRY_CODES[industry],
            "rank": rank.get("rank"),
            "score": rank.get("score"),
            "selected": rank.get("selected", False),
            "data_quality": "ok" if live >= 6 else "review",
            "live_indicators": live,
            "total_indicators": 8,
            "indicators": indicators,
        })
    live_count = sum(item["live_indicators"] for item in industries)
    return {
        "industries": industries,
        "summary": {
            "industry_count": 31,
            "field_count": 248,
            "live_field_count": live_count,
            "live_ratio": round(live_count / 248, 4),
            "min_live_per_industry": min(item["live_indicators"] for item in industries),
            "max_live_per_industry": max(item["live_indicators"] for item in industries),
            "policy": "每行业8个互异业务字段；至少6个live；禁止统一财务、估值、股价动量与拥挤字段。",
        },
    }


def build(output: Path) -> dict[str, Any]:
    global _CLOSE_CACHE
    frames = _load_cmb_sheets()
    contracts = _build_contracts(frames)
    close = _load_closes()
    _CLOSE_CACHE = close
    aligned, diagnostics = _align_features(contracts, close.index)
    scores = _candidate_scores(contracts, aligned, diagnostics, close.index)
    frequencies: dict[str, Any] = {}
    selected_scores: dict[str, pd.DataFrame] = {}
    for frequency in ("monthly", "weekly"):
        frequencies[frequency], selected_scores[frequency] = _frequency_payload(close, scores, frequency)
    old = _read_json(OLD_SNAPSHOT) if OLD_SNAPSHOT.exists() else {}
    monthly_ranking = frequencies["monthly"]["ranking"]
    latest_score_date = selected_scores["monthly"].dropna(how="all").index.max()
    high_frequency = _high_frequency_payload(contracts, aligned, diagnostics, monthly_ranking, latest_score_date)
    generated_at = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    snapshot = {
        "schema_version": "4.0",
        "generated_at": generated_at,
        "as_of": close.index.max().strftime("%Y-%m-%d"),
        "status": "ok" if high_frequency["summary"]["min_live_per_industry"] >= 6 else "review",
        "status_reason": "31行业×8专属业务字段已通过字段禁用、历史长度、PIT可用日和live覆盖门禁。",
        "method": {
            "industry_universe": "申万一级31行业官方指数",
            "industry_portfolio": "Top10等权、只做多、单行业10%",
            "industry_benchmark": "31行业等权；与策略同一执行日再平衡并扣同口径成本",
            "frequencies": ["monthly", "weekly"],
            "cost_rate": 0.001,
            "timing": "T日收盘形成信号；T+1收盘执行；首个持有收益为T+1收盘至T+2收盘",
            "availability": "观察日与可用日分离；月度保守按期末+25自然日，周度/事件按发布后第1交易日",
            "industry_splits": SPLITS,
            "test_policy": "训练集估计方向/权重，验证集选择候选，测试集冻结后只评估一次",
            "factor_contract": ["行业专属产量/价格/库存/运量/订单/开工/终端销量", "PIT行业事件", "训练期方向", "验证期选型"],
            "forbidden_fields": list(FORBIDDEN_PATTERNS),
        },
        "industry": {
            "source": "申万行业指数用于收益；正式信号仅来自248个行业专属业务字段",
            "start": close.index.min().strftime("%Y-%m-%d"),
            "end": close.index.max().strftime("%Y-%m-%d"),
            "count": 31,
            "frequencies": frequencies,
        },
        "style": old.get("style", {}),
        "high_frequency": high_frequency,
        "source_audit": [
            {"source": "CMB行业景气历史缓存（只读）", "purpose": "国家统计局/AKShare/产业数据的2010年以来真实历史种子", "status": "ok", "as_of": "2026-05-22"},
            {"source": "Wind CCOMMODITYFUTURESEODPRICES / RQData", "purpose": "商品主力连续生产刷新与交叉校验", "status": "interface_verified", "as_of": "2026-07-17"},
            {"source": "research_warehouse.news_event_daily + sw_l1_industry_daily", "purpose": "PIT行业专属订单/投产/审批/交付事件", "status": "ok", "as_of": "2026-06-30"},
            {"source": "国家统计局产品目录", "purpose": "机械/电子/环保补充产品字段目录", "status": "metadata_only", "as_of": "2026-07-18", "note": "批量POST路由本次404，未冒充live"},
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    snapshot = build(args.output)
    print(json.dumps({
        "output": str(args.output),
        "status": snapshot["status"],
        "summary": snapshot["high_frequency"]["summary"],
        "monthly": snapshot["industry"]["frequencies"]["monthly"]["metrics"],
        "weekly": snapshot["industry"]["frequencies"]["weekly"]["metrics"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
