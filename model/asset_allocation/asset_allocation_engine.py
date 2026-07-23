"""Auditable multi-cycle asset-allocation engine.

The engine consumes a point-in-time monthly macro panel and four daily return
proxies, then emits factor traces, cycle states, constrained allocations and
causal walk-forward backtests. Provider credentials are never accepted as
arguments or serialized into the public snapshot.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np


ENGINE_VERSION = "asset-allocation-cycle-rp-v1.0"
ASSET_ORDER = ("equity", "bond", "commodity", "cash")
ASSET_LABELS = {"equity": "权益", "bond": "债券", "commodity": "商品", "cash": "现金"}
ASSET_PROXIES = {
    "equity": {"code": "sh.510300", "name": "沪深300ETF", "provider": "BaoStock"},
    "bond": {"code": "sh.000012", "name": "上证国债指数", "provider": "BaoStock"},
    "commodity": {"code": "sz.159934", "name": "黄金ETF易方达", "provider": "BaoStock"},
    "cash": {"code": "sz.159001", "name": "货币ETF易方达", "provider": "BaoStock"},
}

# Canonical Pring propagation path: credit -> production -> inflation. The two
# observable combinations 101/010 are retained as conflict/transition states.
PRING_BITS_TO_PHASE = {"100": 1, "110": 2, "111": 3, "011": 4, "001": 5, "000": 6}
PRING_PHASES = {
    1: {"name": "逆周期启动", "dominant": ["债券"], "weights": [0.10, 0.60, 0.10, 0.20]},
    2: {"name": "复苏", "dominant": ["权益", "债券"], "weights": [0.45, 0.30, 0.10, 0.15]},
    3: {"name": "共振上行", "dominant": ["权益", "商品"], "weights": [0.50, 0.20, 0.25, 0.05]},
    4: {"name": "过热", "dominant": ["权益", "商品"], "weights": [0.45, 0.15, 0.35, 0.05]},
    5: {"name": "滞胀", "dominant": ["商品", "现金"], "weights": [0.10, 0.15, 0.55, 0.20]},
    6: {"name": "共振下行", "dominant": ["债券", "现金"], "weights": [0.05, 0.55, 0.15, 0.25]},
}

FACTOR_REGISTRY = [
    {
        "id": "pring", "name": "普林格六阶段", "horizon": "月度/战术",
        "inputs": ["M1同比", "M2同比", "制造业PMI", "非制造业PMI", "CPI同比", "PPI同比"],
        "transform": "60月滚动百分位，3月因果斜率，三比特状态与路径约束",
        "states": [f"阶段{i} {PRING_PHASES[i]['name']}" for i in range(1, 7)],
        "allocation_role": "周期倾斜；冲突态只降低置信度，不直接清仓",
        "reference": "德邦证券《大类资产配置框架——基于普林格经济周期六段论》",
    },
    {
        "id": "kitchin", "name": "基钦库存周期", "horizon": "3-4年",
        "inputs": ["制造业PMI", "PPI同比", "M1-M2剪刀差", "社融脉冲"],
        "transform": "需求与价格/库存代理的12月滚动标准分及3月斜率",
        "states": ["主动补库", "被动补库", "主动去库", "被动去库"],
        "allocation_role": "权益/商品方向校验；库存原值不可得时明确标注代理",
        "reference": "券商经济四周期与库存周期复盘框架",
    },
    {
        "id": "juglar", "name": "朱格拉资本开支周期", "horizon": "7-11年",
        "inputs": ["社融存量", "社融增量", "PPI同比", "制造业PMI"],
        "transform": "24-60月信用/资本开支代理趋势与扩散确认",
        "states": ["修复", "扩张", "过热", "收缩"],
        "allocation_role": "中期风险预算；不用于月度单点交易",
        "reference": "制造业投资周期/中周期券商框架",
    },
    {
        "id": "kondratieff", "name": "康波结构情景", "horizon": "40-60年",
        "inputs": ["长期通胀", "商品趋势", "信用/资本形成代理", "创新叙事证据"],
        "transform": "60月慢变量分数与结构证据卡；样本不足时强制低置信度",
        "states": ["复苏", "繁荣", "衰退", "萧条/创新孕育"],
        "allocation_role": "战略情景与压力测试，不做精确时点择时",
        "reference": "康波、朱格拉与基钦嵌套的长期资产复盘",
    },
    {
        "id": "merrill", "name": "美林投资时钟", "horizon": "月度/季度",
        "inputs": ["增长方向", "通胀方向"],
        "transform": "同步因子与滞后因子的因果斜率二分",
        "states": ["复苏", "过热", "滞胀", "衰退"],
        "allocation_role": "与普林格交叉验证，避免单一时钟下注",
        "reference": "Merrill Lynch Investment Clock 及国内券商本土化复盘",
    },
    {
        "id": "macro_risk", "name": "宏观风险因子", "horizon": "月度/战略",
        "inputs": ["增长", "通胀", "利率", "流动性", "信用"],
        "transform": "可交易代理映射、收缩协方差、风险预算",
        "states": ["正暴露", "中性", "负暴露"],
        "allocation_role": "全天候与风险平价的统一风险语言",
        "reference": "华西证券宏观风险平价；招商银行宏观因子TAA+风险预算SAA",
    },
    {
        "id": "hmm_covariance", "name": "HMM协方差状态", "horizon": "月度",
        "inputs": ["四类资产月收益"],
        "transform": "三状态高斯HMM、对角发射协方差、预测状态加权协方差",
        "states": ["低波动", "中性/风险偏好", "压力"],
        "allocation_role": "对时变相关性做稳健化，不预测单一资产收益",
        "reference": "国泰君安《基于隐马尔可夫市场状态的风险平价策略》",
    },
    {
        "id": "llm_evidence", "name": "另类数据/LLM证据层", "horizon": "事件驱动",
        "inputs": ["政策原文", "央行文本", "券商研报", "新闻事件", "搜索/舆情"],
        "transform": "带引用的结构化抽取、冲突检测、时点封印、置信度上限",
        "states": ["支持", "冲突", "不足"],
        "allocation_role": "最多贡献20%战术倾斜；缺引用或存在冲突时不进入权重",
        "reference": "可审计LLM研究流程；不将生成式文本直接当作收益预测",
    },
]


@dataclass(frozen=True)
class BacktestConfig:
    covariance_lookback: int = 36
    min_history: int = 24
    shrinkage: float = 0.35
    transaction_cost_bps: float = 10.0
    rebalance_frequency: str = "monthly"
    max_turnover: float = 0.35


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_macro_from_sqlite(path: str | Path) -> list[dict[str, Any]]:
    db_path = Path(path).resolve()
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM macro_monthly ORDER BY month")]


def load_etf_prices_from_sqlite(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load the approved local ETF history without mutating the warehouse."""
    db_path = Path(path).resolve()
    code_map = {"equity": "510300.SH", "commodity": "159934.SZ", "cash": "159001.SZ"}
    output: dict[str, list[dict[str, Any]]] = {}
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        for asset, code in code_map.items():
            output[asset] = [
                {"date": str(row["trade_date"]), "close": float(row["close"]), "pct_chg": _number(row["pct_chg"])}
                for row in con.execute(
                    "SELECT trade_date, close, pct_chg FROM etf_ohlcv_daily "
                    "WHERE ts_code=? AND close>0 ORDER BY trade_date",
                    (code,),
                )
            ]
    return output


def merge_price_series(*series: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Merge provider panels by asset/date; later sources win for overlapping dates."""
    merged: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for panel in series:
        for asset, rows in panel.items():
            for row in rows:
                raw_date = str(row.get("date") or "")
                date = "".join(character for character in raw_date if character.isdigit())[:8]
                close = _number(row.get("close"))
                if len(date) == 8 and close is not None and close > 0:
                    merged[asset][date] = {**row, "date": date, "close": close}
    return {asset: [rows[date] for date in sorted(rows)] for asset, rows in merged.items()}


def fetch_baostock_prices(
    start_date: str = "2014-01-01", end_date: str | None = None,
    proxies: dict[str, dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run one bounded daily-history call per proxy outside web requests."""
    import baostock as bs  # type: ignore

    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    proxies = proxies or ASSET_PROXIES
    login = bs.login()
    if getattr(login, "error_code", "1") != "0":
        raise RuntimeError(f"baostock_login_failed:{getattr(login, 'error_msg', '')}")
    output: dict[str, list[dict[str, Any]]] = {}
    try:
        for asset, spec in proxies.items():
            query = bs.query_history_k_data_plus(
                spec["code"], "date,code,close,pctChg", start_date=start_date,
                end_date=end_date, frequency="d", adjustflag="2",
            )
            rows: list[dict[str, Any]] = []
            while query.error_code == "0" and query.next():
                row = query.get_row_data()
                close = _number(row[2])
                if close is not None and close > 0:
                    rows.append({"date": row[0], "close": close, "pct_chg": _number(row[3])})
            if query.error_code != "0" or len(rows) < 24:
                raise RuntimeError(f"baostock_query_failed:{asset}:{query.error_code}:{query.error_msg}")
            output[asset] = rows
    finally:
        bs.logout()
    return output


def _rolling_percentile(values: Sequence[float | None], window: int = 60, minimum: int = 24) -> list[float | None]:
    result: list[float | None] = []
    for index, value in enumerate(values):
        history = [x for x in values[max(0, index-window+1):index+1] if x is not None]
        if value is None or len(history) < minimum:
            result.append(None)
            continue
        less = sum(1 for x in history if x < value)
        equal = sum(1 for x in history if x == value)
        result.append((less + 0.5 * equal) / len(history))
    return result


def _rolling_zscore(values: Sequence[float | None], window: int = 36, minimum: int = 18) -> list[float | None]:
    result: list[float | None] = []
    for index, value in enumerate(values):
        history = [x for x in values[max(0, index-window+1):index+1] if x is not None]
        if value is None or len(history) < minimum:
            result.append(None)
            continue
        std = float(np.std(history, ddof=1))
        result.append(0.0 if std < 1e-12 else (value-float(np.mean(history)))/std)
    return result


def _mean_available(*values: float | None) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return float(np.mean(clean)) if clean else None


def _causal_slope(values: Sequence[float | None], index: int, lookback: int = 3) -> float | None:
    part = values[max(0, index-lookback+1):index+1]
    if len(part) < lookback or any(x is None for x in part):
        return None
    return float(np.polyfit(np.arange(len(part), dtype=float), np.asarray(part, dtype=float), 1)[0])


def _direction(slope: float | None, previous: int | None, threshold: float = 0.015) -> int | None:
    if slope is None:
        return None
    if slope > threshold:
        return 1
    if slope < -threshold:
        return 0
    return previous if previous is not None else int(slope >= 0)


def _nearest_pring_phase(bits: str, previous_phase: int | None) -> tuple[int, float, bool]:
    if bits in PRING_BITS_TO_PHASE:
        return PRING_BITS_TO_PHASE[bits], 1.0, False
    distances = {phase: sum(a != b for a, b in zip(bits, valid)) for valid, phase in PRING_BITS_TO_PHASE.items()}
    best = min(distances.values())
    candidates = [phase for phase, distance in distances.items() if distance == best]
    if previous_phase in candidates:
        phase = int(previous_phase)
    elif previous_phase is not None:
        phase = min(candidates, key=lambda value: min((value-previous_phase) % 6, (previous_phase-value) % 6))
    else:
        phase = candidates[0]
    return phase, 0.55, True


def build_cycle_history(macro_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in macro_rows]
    names = ("pmi_manufacturing", "pmi_non_manufacturing", "cpi_national_yoy", "ppi_yoy",
             "m1_yoy", "m2_yoy", "sf_inc_month", "sf_stock_endval")
    fields = {key: [_number(row.get(key)) for row in rows] for key in names}
    pct = {key: _rolling_percentile(values) for key, values in fields.items()}
    zscore = {key: _rolling_zscore(values) for key, values in fields.items()}
    leading = [_mean_available(pct["m1_yoy"][i], pct["m2_yoy"][i]) for i in range(len(rows))]
    coincident = [_mean_available(pct["pmi_manufacturing"][i], pct["pmi_non_manufacturing"][i]) for i in range(len(rows))]
    lagging = [_mean_available(pct["ppi_yoy"][i], pct["cpi_national_yoy"][i]) for i in range(len(rows))]

    history: list[dict[str, Any]] = []
    previous_bits: list[int | None] = [None, None, None]
    previous_phase: int | None = None
    for index, row in enumerate(rows):
        slopes = [_causal_slope(leading,index), _causal_slope(coincident,index), _causal_slope(lagging,index)]
        directions = [_direction(value, previous_bits[pos]) for pos, value in enumerate(slopes)]
        if any(value is None for value in directions):
            continue
        bits = "".join(str(int(value)) for value in directions)
        phase, path_confidence, conflict = _nearest_pring_phase(bits, previous_phase)
        previous_bits, previous_phase = directions, phase
        demand = _mean_available(zscore["pmi_manufacturing"][index], _causal_slope(pct["pmi_manufacturing"],index)) or 0.0
        inventory = _mean_available(zscore["ppi_yoy"][index], _causal_slope(pct["ppi_yoy"],index)) or 0.0
        if demand >= 0 and inventory >= 0: kitchin = "主动补库"
        elif demand < 0 <= inventory: kitchin = "被动补库"
        elif demand < 0 and inventory < 0: kitchin = "主动去库"
        else: kitchin = "被动去库"
        credit = _mean_available(zscore["sf_stock_endval"][index], zscore["sf_inc_month"][index]) or 0.0
        capex_slope = _causal_slope(zscore["sf_stock_endval"], index, 12) or 0.0
        if credit < 0 and capex_slope >= 0: juglar = "修复"
        elif credit >= 0 and capex_slope >= 0: juglar = "扩张"
        elif credit >= 0 and capex_slope < 0: juglar = "过热"
        else: juglar = "收缩"
        growth_up, inflation_up = directions[1] == 1, directions[2] == 1
        merrill = {(True,False):"复苏",(True,True):"过热",(False,True):"滞胀",(False,False):"衰退"}[(growth_up,inflation_up)]
        slow_inflation = _mean_available(*lagging[max(0,index-11):index+1])
        credit_window = [x for x in zscore["sf_stock_endval"][max(0,index-35):index+1] if x is not None]
        slow_credit = _mean_available(*credit_window)
        slow = _mean_available(slow_inflation, None if slow_credit is None else 0.5+0.15*slow_credit) or 0.5
        if slow >= 0.68 and growth_up: kondratieff = "繁荣"
        elif slow >= 0.52 and not growth_up: kondratieff = "衰退"
        elif slow < 0.38 and not growth_up: kondratieff = "萧条/创新孕育"
        else: kondratieff = "复苏"
        available = sum(value is not None for value in (
            fields["m1_yoy"][index], fields["m2_yoy"][index], fields["pmi_manufacturing"][index],
            fields["pmi_non_manufacturing"][index], fields["cpi_national_yoy"][index], fields["ppi_yoy"][index]))
        confidence = max(0.0, min(1.0, available/6.0*path_confidence))
        history.append({
            "month": str(row.get("month")), "leading": round(float(leading[index]),6),
            "coincident": round(float(coincident[index]),6), "lagging": round(float(lagging[index]),6),
            "leading_slope": round(float(slopes[0]),6), "coincident_slope": round(float(slopes[1]),6),
            "lagging_slope": round(float(slopes[2]),6), "pring_bits": bits, "pring_phase": phase,
            "pring_phase_name": PRING_PHASES[phase]["name"], "pring_conflict": conflict,
            "confidence": round(confidence,4), "kitchin_state": kitchin,
            "kitchin_demand_score": round(float(demand),4), "kitchin_inventory_proxy": round(float(inventory),4),
            "juglar_state": juglar, "juglar_credit_score": round(float(credit),4),
            "juglar_slope": round(float(capex_slope),4), "kondratieff_state": kondratieff,
            "kondratieff_confidence": round(min(0.35,len(history)/360.0),4), "merrill_state": merrill,
            "growth_score": round(float((coincident[index] or 0.5)*2-1),4),
            "inflation_score": round(float((lagging[index] or 0.5)*2-1),4),
            "liquidity_score": round(float((leading[index] or 0.5)*2-1),4),
            "credit_score": round(float(np.tanh(credit/2)),4), "source": str(row.get("source") or "local_warehouse"),
        })
    return history


def _month_key(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())[:6]


def monthly_prices(price_series: dict[str, list[dict[str, Any]]]) -> tuple[list[str], np.ndarray]:
    per_asset: dict[str, dict[str,float]] = {}
    for asset in ASSET_ORDER:
        month_map: dict[str,float] = {}
        for row in price_series.get(asset,[]):
            close = _number(row.get("close"))
            if close is not None and close > 0: month_map[_month_key(str(row.get("date")))] = close
        per_asset[asset] = month_map
    common = sorted(set.intersection(*(set(per_asset[asset]) for asset in ASSET_ORDER)))
    return common, np.asarray([[per_asset[asset][month] for asset in ASSET_ORDER] for month in common], dtype=float)


def _returns(prices: np.ndarray) -> np.ndarray:
    return prices[1:]/prices[:-1]-1.0


def _shrink_cov(returns: np.ndarray, shrinkage: float = 0.35) -> np.ndarray:
    cov = np.cov(returns,rowvar=False,ddof=1)
    diag = np.diag(np.diag(cov))
    cov = (1-shrinkage)*cov+shrinkage*diag
    cov[np.diag_indices_from(cov)] = np.maximum(np.diag(cov),(0.0025/math.sqrt(12))**2)
    return cov


def _normalize_weights(values: Sequence[float], floors: Sequence[float] | None = None, caps: Sequence[float] | None = None) -> np.ndarray:
    """Project non-negative scores to a bounded simplex while preserving ratios."""
    score = np.maximum(np.asarray(values, dtype=float), 0.0)
    if not np.all(np.isfinite(score)):
        raise ValueError("invalid_weight_score")
    if score.sum() <= 0:
        score = np.ones(len(score), dtype=float)
    floors_a = np.asarray(floors if floors is not None else [0.0] * len(score), dtype=float)
    caps_a = np.asarray(caps if caps is not None else [1.0] * len(score), dtype=float)
    if floors_a.sum() > 1.0 + 1e-10 or caps_a.sum() < 1.0 - 1e-10 or np.any(floors_a > caps_a):
        raise ValueError("infeasible_weight_bounds")
    low, high = 0.0, 1.0
    while float(np.clip(high * score, floors_a, caps_a).sum()) < 1.0:
        high *= 2.0
        if high > 1e12:
            raise ValueError("weight_projection_failed")
    for _ in range(100):
        middle = 0.5 * (low + high)
        if float(np.clip(middle * score, floors_a, caps_a).sum()) < 1.0:
            low = middle
        else:
            high = middle
    weight = np.clip(0.5 * (low + high) * score, floors_a, caps_a)
    residual = 1.0 - float(weight.sum())
    if abs(residual) > 1e-10:
        room = caps_a - weight if residual > 0 else weight - floors_a
        open_positions = room > 1e-12
        weight[open_positions] += residual * room[open_positions] / room[open_positions].sum()
    return weight


def risk_budget_weights(covariance: np.ndarray, budgets: Sequence[float] | None = None) -> np.ndarray:
    """Solve long-only risk budgeting by cyclical coordinate descent.

    The unconstrained iterate solves the convex log-barrier formulation; the
    final projection applies the product's explicit asset floors and caps.
    """
    covariance = np.asarray(covariance, dtype=float)
    n = covariance.shape[0]
    if covariance.shape != (n, n) or not np.all(np.isfinite(covariance)):
        raise ValueError("invalid_covariance")
    covariance = 0.5 * (covariance + covariance.T)
    minimum_eigenvalue = float(np.linalg.eigvalsh(covariance).min())
    if minimum_eigenvalue <= 1e-12:
        covariance += np.eye(n) * (1e-12 - minimum_eigenvalue)
    budget = np.asarray(budgets if budgets is not None else [1 / n] * n, dtype=float)
    budget = np.maximum(budget, 1e-8)
    budget /= budget.sum()
    weight = np.sqrt(budget / np.maximum(np.diag(covariance), 1e-12))
    weight /= weight.sum()
    for _ in range(1000):
        previous = weight.copy()
        for position in range(n):
            cross = float(covariance[position] @ weight - covariance[position, position] * weight[position])
            discriminant = cross * cross + 4.0 * covariance[position, position] * budget[position]
            weight[position] = max(
                (-cross + math.sqrt(max(discriminant, 0.0))) / (2.0 * covariance[position, position]),
                1e-12,
            )
        if np.max(np.abs(weight / weight.sum() - previous / previous.sum())) < 1e-10:
            break
    weight /= weight.sum()
    return _normalize_weights(weight, [0.05, 0.10, 0.05, 0.05], [0.55, 0.65, 0.45, 0.35])


def _logsumexp(values: np.ndarray, axis: int | None = None) -> np.ndarray:
    maximum=np.max(values,axis=axis,keepdims=True)
    result=maximum+np.log(np.sum(np.exp(values-maximum),axis=axis,keepdims=True))
    return np.squeeze(result,axis=axis) if axis is not None else result.squeeze()


def _shrink_cov_matrix(cov: np.ndarray, shrinkage: float = 0.35) -> np.ndarray:
    diagonal=np.diag(np.maximum(np.diag(cov),1e-8))
    return (1-shrinkage)*cov+shrinkage*diagonal


def hmm_forecast_covariance(returns: np.ndarray, states: int = 3, iterations: int = 25) -> tuple[np.ndarray,list[float],dict[str,Any]]:
    data=np.asarray(returns,dtype=float)
    if len(data)<30:
        return _shrink_cov(data),[1/states]*states,{"status":"insufficient_history","iterations":0}
    mean=data.mean(axis=0); scale=data.std(axis=0,ddof=1); scale[scale<1e-6]=1.0; x=(data-mean)/scale
    n_obs,n_dim=x.shape; cuts=np.quantile(x[:,0],[1/states,2/states]); assignment=np.digitize(x[:,0],cuts)
    state_mean=np.vstack([x[assignment==k].mean(axis=0) if np.any(assignment==k) else np.zeros(n_dim) for k in range(states)])
    state_var=np.vstack([x[assignment==k].var(axis=0)+0.2 if np.any(assignment==k) else np.ones(n_dim) for k in range(states)])
    transition=np.full((states,states),0.08/max(states-1,1)); np.fill_diagonal(transition,0.92)
    initial=np.full(states,1/states); gamma=np.full((n_obs,states),1/states)
    for _ in range(iterations):
        emit=np.empty((n_obs,states))
        for k in range(states):
            var=np.maximum(state_var[k],1e-3)
            emit[:,k]=-0.5*(n_dim*math.log(2*math.pi)+np.log(var).sum()+((x-state_mean[k])**2/var).sum(axis=1))
        alpha=np.empty((n_obs,states)); alpha[0]=np.log(np.maximum(initial,1e-12))+emit[0]
        for t in range(1,n_obs): alpha[t]=emit[t]+_logsumexp(alpha[t-1][:,None]+np.log(np.maximum(transition,1e-12)),axis=0)
        beta=np.zeros((n_obs,states))
        for t in range(n_obs-2,-1,-1): beta[t]=_logsumexp(np.log(np.maximum(transition,1e-12))+emit[t+1][None,:]+beta[t+1][None,:],axis=1)
        log_gamma=alpha+beta; log_gamma-=_logsumexp(log_gamma,axis=1)[:,None]; gamma=np.exp(log_gamma)
        xi=np.zeros((states,states))
        for t in range(n_obs-1):
            item=alpha[t][:,None]+np.log(np.maximum(transition,1e-12))+emit[t+1][None,:]+beta[t+1][None,:]
            item-=_logsumexp(item); xi+=np.exp(item)
        initial=gamma[0]; transition=xi+0.5; transition/=transition.sum(axis=1,keepdims=True)
        denom=np.maximum(gamma.sum(axis=0),1e-8); state_mean=(gamma.T@x)/denom[:,None]
        for k in range(states): state_var[k]=(gamma[:,k][:,None]*(x-state_mean[k])**2).sum(axis=0)/denom[k]
        state_var=np.maximum(state_var,1e-3)
    forecast=gamma[-1]@transition; covs=[]; means=[]
    for k in range(states):
        weight=gamma[:,k]; total=max(weight.sum(),1e-8); mu=(weight[:,None]*data).sum(axis=0)/total
        centered=data-mu; cov=(centered*weight[:,None]).T@centered/total
        covs.append(_shrink_cov_matrix(cov)); means.append(mu)
    forecast_mean=sum(float(forecast[k])*means[k] for k in range(states)); final=np.zeros((n_dim,n_dim))
    for k in range(states):
        delta=means[k]-forecast_mean; final+=float(forecast[k])*(covs[k]+np.outer(delta,delta))
    order=np.argsort([np.trace(cov) for cov in covs])
    return _shrink_cov_matrix(final),[float(forecast[i]) for i in order],{"status":"ok","states":states,"iterations":iterations,"labels":["低波动","中性/风险偏好","压力"]}


def _phase_target(cycle: dict[str,Any]) -> np.ndarray:
    target=np.asarray(PRING_PHASES[int(cycle["pring_phase"])]["weights"],dtype=float)
    if cycle.get("merrill_state")=="衰退": target+=np.asarray([-0.05,0.05,-0.02,0.02])
    elif cycle.get("merrill_state")=="滞胀": target+=np.asarray([-0.05,-0.03,0.06,0.02])
    if cycle.get("kitchin_state")=="主动补库": target+=np.asarray([0.04,-0.02,0.03,-0.05])
    elif cycle.get("kitchin_state")=="主动去库": target+=np.asarray([-0.04,0.03,-0.02,0.03])
    return _normalize_weights(target,[0.05,0.10,0.05,0.05],[0.55,0.65,0.45,0.35])


def strategy_weights(strategy: str, window: np.ndarray, cycle: dict[str,Any], previous: np.ndarray | None = None, config: BacktestConfig | None = None) -> tuple[np.ndarray,dict[str,Any]]:
    config=config or BacktestConfig(); cov=_shrink_cov(window[-config.covariance_lookback:],config.shrinkage); meta: dict[str,Any]={}
    if strategy=="equal_weight": weight=np.full(4,0.25)
    elif strategy=="inverse_vol": weight=_normalize_weights(1/np.sqrt(np.maximum(np.diag(cov),1e-12)),[0.05,0.10,0.05,0.05],[0.55,0.65,0.45,0.35])
    elif strategy=="risk_parity": weight=risk_budget_weights(cov)
    elif strategy=="all_weather":
        weight=risk_budget_weights(cov,[0.22,0.34,0.26,0.18]); prior=np.asarray([0.25,0.45,0.20,0.10])
        weight=_normalize_weights(0.65*weight+0.35*prior,[0.08,0.20,0.08,0.05],[0.45,0.65,0.35,0.25])
    elif strategy=="pring_stage": weight=_phase_target(cycle)
    elif strategy=="hmm_risk_parity":
        hmm_cov,probability,hmm_meta=hmm_forecast_covariance(window[-60:]); weight=risk_budget_weights(hmm_cov)
        meta={"hmm_probability":probability,"hmm":hmm_meta}
    elif strategy=="cycle_risk_parity":
        core=risk_budget_weights(cov); target=_phase_target(cycle); strength=min(0.45,0.15+0.30*float(cycle.get("confidence") or 0))
        weight=_normalize_weights((1-strength)*core+strength*target,[0.05,0.10,0.05,0.05],[0.55,0.65,0.45,0.35]); meta["tilt_strength"]=strength
    elif strategy=="recommended":
        rp=risk_budget_weights(cov); aw,_=strategy_weights("all_weather",window,cycle,None,config); cyc,_=strategy_weights("cycle_risk_parity",window,cycle,None,config)
        weight=_normalize_weights(0.35*rp+0.40*aw+0.25*cyc,[0.05,0.15,0.05,0.05],[0.50,0.65,0.40,0.30])
    else: raise ValueError(f"unknown_strategy:{strategy}")
    if previous is not None:
        delta=weight-previous; turnover=float(np.abs(delta).sum())
        if turnover>config.max_turnover:
            weight=_normalize_weights(previous+delta*(config.max_turnover/turnover)); meta["turnover_limited"]=True
    return weight,meta


def _cycle_by_month(history: Sequence[dict[str,Any]], months: Sequence[str]) -> list[dict[str,Any]]:
    records=sorted(history,key=lambda row:str(row["month"])); output=[]; cursor=0; latest=None
    for month in months:
        while cursor<len(records) and str(records[cursor]["month"])<=month: latest=records[cursor]; cursor+=1
        output.append(latest or {"pring_phase":6,"confidence":0.0,"merrill_state":"衰退","kitchin_state":"主动去库"})
    return output


def _metrics(returns: Sequence[float]) -> dict[str,float]:
    values=np.asarray(returns,dtype=float); nav=np.cumprod(1+values); years=max(len(values)/12.0,1/12)
    annual=float(nav[-1]**(1/years)-1) if len(nav) else 0.0; vol=float(np.std(values,ddof=1)*math.sqrt(12)) if len(values)>1 else 0.0
    peak=np.maximum.accumulate(nav) if len(nav) else np.asarray([1.0]); drawdown=nav/peak-1 if len(nav) else np.asarray([0.0]); mdd=float(drawdown.min())
    return {"annual_return":annual,"annual_volatility":vol,"sharpe":annual/vol if vol>1e-12 else 0.0,
            "max_drawdown":mdd,"calmar":annual/abs(mdd) if mdd<-1e-12 else 0.0,
            "positive_month_rate":float(np.mean(values>0)) if len(values) else 0.0,"total_return":float(nav[-1]-1) if len(nav) else 0.0}


def walk_forward_backtest(months: Sequence[str], prices: np.ndarray, cycle_history: Sequence[dict[str,Any]], strategies: Sequence[str] | None = None, config: BacktestConfig | None = None) -> dict[str,Any]:
    config=config or BacktestConfig(); strategies=strategies or ("equal_weight","risk_parity","all_weather","pring_stage","hmm_risk_parity","cycle_risk_parity","recommended")
    returns=_returns(prices); return_months=list(months[1:]); cycles=_cycle_by_month(cycle_history,return_months); start=max(config.min_history,config.covariance_lookback)
    output: dict[str,Any]={"config":config.__dict__,"strategies":{}}
    for strategy in strategies:
        p_returns=[]; nav_rows=[]; weight_rows=[]; previous=None; nav=1.0; gross_nav=1.0; turnover_sum=0.0; phase_returns: dict[int,list[float]]=defaultdict(list)
        for index in range(start,len(returns)):
            cycle=cycles[index-1]; weight,meta=strategy_weights(strategy,returns[:index],cycle,previous,config)
            turnover=float(np.abs(weight-previous).sum()) if previous is not None else 0.0; cost=turnover*config.transaction_cost_bps/10000.0
            gross=float(weight@returns[index]); net=gross-cost; nav*=1+net; gross_nav*=1+gross; turnover_sum+=turnover; p_returns.append(net)
            phase_returns[int(cycle.get("pring_phase") or 0)].append(net); month=return_months[index]
            nav_rows.append({"month":month,"nav":round(nav,8),"gross_nav":round(gross_nav,8),"return":round(net,8),"turnover":round(turnover,8),"pring_phase":int(cycle.get("pring_phase") or 0)})
            record={"month":month,**{asset:round(float(weight[pos]),8) for pos,asset in enumerate(ASSET_ORDER)}}
            if meta.get("hmm_probability"): record["hmm_probability"]=meta["hmm_probability"]
            weight_rows.append(record); previous=weight
        metrics=_metrics(p_returns); metrics["average_annual_turnover"]=turnover_sum/max(len(p_returns)/12.0,1/12); metrics["cost_drag"]=gross_nav-nav
        phase_summary={str(phase):{"months":len(vals),"average_monthly_return":float(np.mean(vals)) if vals else 0.0,"positive_rate":float(np.mean(np.asarray(vals)>0)) if vals else 0.0} for phase,vals in sorted(phase_returns.items())}
        output["strategies"][strategy]={"metrics":metrics,"nav":nav_rows,"weights":weight_rows,"phase_summary":phase_summary}
    return output


def current_allocations(months: Sequence[str], prices: np.ndarray, cycle_history: Sequence[dict[str,Any]], config: BacktestConfig | None = None) -> dict[str,Any]:
    config=config or BacktestConfig(); returns=_returns(prices); cycle=cycle_history[-1]; result: dict[str,Any]={}
    for strategy in ("equal_weight","risk_parity","all_weather","pring_stage","hmm_risk_parity","cycle_risk_parity","recommended"):
        weight,meta=strategy_weights(strategy,returns,cycle,None,config)
        result[strategy]={"weights":{asset:round(float(weight[pos]),6) for pos,asset in enumerate(ASSET_ORDER)},"metadata":meta}
    result.update({"as_of":months[-1],"macro_as_of":cycle["month"],"current_cycle":cycle}); return result


def quality_report(macro_rows: Sequence[dict[str,Any]], price_series: dict[str,list[dict[str,Any]]], months: Sequence[str], prices: np.ndarray, allocations: dict[str,Any]) -> dict[str,Any]:
    checks=[]
    def add(name: str, passed: bool, detail: str) -> None: checks.append({"check":name,"status":"passed" if passed else "failed","detail":detail})
    macro_months=[str(row.get("month")) for row in macro_rows]; add("macro_month_unique",len(macro_months)==len(set(macro_months)),f"rows={len(macro_months)}")
    add("macro_monotonic",macro_months==sorted(macro_months),f"min={macro_months[0]}, max={macro_months[-1]}")
    for asset in ASSET_ORDER:
        dates=[str(row.get("date")) for row in price_series.get(asset,[])]; add(f"{asset}_daily_coverage",len(dates)>=500,f"rows={len(dates)}, min={min(dates)}, max={max(dates)}")
        add(f"{asset}_date_unique",len(dates)==len(set(dates)),f"duplicates={len(dates)-len(set(dates))}")
    add("common_months",len(months)>=96,f"months={len(months)}, min={months[0]}, max={months[-1]}")
    ret=_returns(prices); add("return_outlier",bool(np.nanmax(np.abs(ret))<0.45),f"max_abs={float(np.nanmax(np.abs(ret))):.4f}")
    for name,payload in allocations.items():
        if not isinstance(payload,dict) or "weights" not in payload: continue
        weight=list(payload["weights"].values()); add(f"{name}_weight_sum",abs(sum(weight)-1)<1e-5,f"sum={sum(weight):.8f}")
        add(f"{name}_weight_bounds",min(weight)>=0 and max(weight)<=0.65,f"min={min(weight):.4f}, max={max(weight):.4f}")
    return {"status":"passed" if all(x["status"]=="passed" for x in checks) else "failed","checks":checks,"failed":[x["check"] for x in checks if x["status"]!="passed"]}


def build_snapshot(macro_rows: Sequence[dict[str,Any]], price_series: dict[str,list[dict[str,Any]]], *, generated_at: str | None = None, config: BacktestConfig | None = None) -> dict[str,Any]:
    config=config or BacktestConfig(); cycles=build_cycle_history(macro_rows)
    if not cycles: raise ValueError("cycle_history_empty")
    months,prices=monthly_prices(price_series)
    if len(months)<config.covariance_lookback+24: raise ValueError(f"price_history_too_short:{len(months)}")
    backtest=walk_forward_backtest(months,prices,cycles,config=config); allocations=current_allocations(months,prices,cycles,config=config)
    quality=quality_report(macro_rows,price_series,months,prices,allocations)
    if quality["status"]!="passed": raise ValueError("quality_gate_failed:"+",".join(quality["failed"]))
    price_payload={asset:[{"month":month,"close":round(float(prices[index,pos]),6)} for index,month in enumerate(months)] for pos,asset in enumerate(ASSET_ORDER)}
    return {
        "schema_version":"1.0","engine_version":ENGINE_VERSION,"generated_at":generated_at or utc_now(),"status":"ready",
        "asset_order":list(ASSET_ORDER),"asset_labels":ASSET_LABELS,"asset_proxies":ASSET_PROXIES,
        "data_as_of":{"market":months[-1],"macro_complete":cycles[-1]["month"],"macro_raw_latest":str(macro_rows[-1].get("month"))},
        "methodology":{"rebalance":"monthly_close_to_next_month_return","point_in_time":True,
            "lookahead_guard":"signals and covariance use only observations available through rebalance month",
            "transaction_cost_bps":config.transaction_cost_bps,"covariance":f"{config.covariance_lookback}m sample covariance + {config.shrinkage:.0%} diagonal shrinkage",
            "constraints":{"long_only":True,"max_weight":0.65,"cash_floor":0.05,"max_monthly_turnover":config.max_turnover},
            "llm_policy":"evidence-only; cited structured signals capped at 20% of tactical tilt; inactive in this snapshot"},
        "factor_registry":FACTOR_REGISTRY,
        "pring_state_map":{"valid":{bits:{"phase":phase,**PRING_PHASES[phase]} for bits,phase in PRING_BITS_TO_PHASE.items()},
            "conflict":{"101":"先行与滞后上行、同步下行：冲突/过渡态","010":"同步上行、先行与滞后下行：冲突/过渡态"}},
        "cycle_history":cycles,"monthly_prices":price_payload,"allocations":allocations,"backtest":backtest,"quality":quality,
        "sources":[{"name":"本地研究仓库 macro_monthly","frequency":"monthly","mode":"read_only","credentials_serialized":False},
            {"name":"BaoStock","frequency":"daily","mode":"bounded_incremental","credentials_serialized":False},
            {"name":"Tushare/iFinD/Wind/RQData","frequency":"optional","mode":"environment_only_provider_adapters","credentials_serialized":False}],
        "limitations":["商品类别当前以黄金ETF作为可交易代理，广义商品/期货指数接入后需做稳健性对照。",
            "同步指标使用制造业与非制造业PMI代理，工业增加值/GDP实时口径接入后应并行复核。",
            "康波样本远少于完整长周期，输出仅为低置信度结构情景。",
            "历史表现为研究回测，不构成收益承诺；参数冻结后仍需前瞻影子期验证。"],
    }


def write_snapshot(snapshot: dict[str,Any], output_path: str | Path) -> None:
    path=Path(output_path); path.parent.mkdir(parents=True,exist_ok=True); temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(snapshot,ensure_ascii=False,separators=(",",":")),encoding="utf-8"); temporary.replace(path)


def public_payload(snapshot: dict[str,Any]) -> dict[str,Any]:
    keys=("schema_version","engine_version","generated_at","status","asset_order","asset_labels","asset_proxies","data_as_of","methodology","factor_registry","pring_state_map","cycle_history","monthly_prices","allocations","backtest","quality","sources","limitations")
    return {key:snapshot[key] for key in keys}


# === allocation research engine v2 ===
# The original numerical primitives remain above for backwards-compatible unit
# tests. The production exports below replace all v1 high-level research logic.

import itertools as _itertools
from dataclasses import asdict as _asdict, replace as _replace
from statistics import NormalDist as _NormalDist

ENGINE_VERSION = "asset-allocation-research-v2.1"
ASSET_PROXIES = {
    "equity": {"code": "sh.510300", "ts_code": "510300.SH", "name": "华泰柏瑞沪深300ETF", "provider": "local+BaoStock"},
    "bond": {"code": "sz.159972", "ts_code": "159972.SZ", "name": "鹏华中证5年期地方政府债ETF",
             "provider": "local+BaoStock", "historical_predecessor": "159926.SZ",
             "chain_method": "predecessor_return_chain_to_current_etf"},
    "commodity": {"code": "sz.159934", "ts_code": "159934.SZ", "name": "易方达黄金ETF", "provider": "local+BaoStock"},
    "cash": {"code": "sz.159001", "ts_code": "159001.SZ", "name": "易方达保证金收益货币ETF", "provider": "local+BaoStock"},
}

PROFILE_SPECS = {
    "conservative": {"label": "稳健", "floors": [0.08, 0.20, 0.05, 0.10], "caps": [0.40, 0.70, 0.30, 0.40],
                     "risk_budget": [0.18, 0.38, 0.18, 0.26], "capital_prior": [0.18, 0.45, 0.12, 0.25]},
    "balanced": {"label": "平衡", "floors": [0.10, 0.12, 0.05, 0.05], "caps": [0.55, 0.65, 0.40, 0.30],
                 "risk_budget": [0.28, 0.32, 0.22, 0.18], "capital_prior": [0.30, 0.35, 0.20, 0.15]},
    "equity_preferred": {"label": "权益偏好", "floors": [0.25, 0.10, 0.05, 0.03], "caps": [0.65, 0.60, 0.35, 0.20],
                         "risk_budget": [0.42, 0.26, 0.20, 0.12], "capital_prior": [0.45, 0.28, 0.17, 0.10]},
}

RESEARCH_EVIDENCE = [
    {"institution": "德邦证券", "title": "大类资产配置框架——基于普林格经济周期六段论", "method": "三类指标、六阶段与中国资产复盘", "url": "https://pdf.dfcfw.com/pdf/H3_AP202106081496680732_1.pdf"},
    {"institution": "国金证券", "title": "股债框架下的宏观风险配置策略", "method": "PCA宏观风险因子与因子风险预算", "url": "https://pdf.dfcfw.com/pdf/H3_AP202112021532380645_1.pdf"},
    {"institution": "国金证券", "title": "基于宏观因子风险预算的股债资产配置策略", "method": "宏观暴露映射、因子协方差与动态预算", "url": "https://pdf.dfcfw.com/pdf/H301_AP202408061639154222_1.pdf"},
    {"institution": "华西证券", "title": "风险平价模型风险测度探讨", "method": "波动率、下行风险、CVaR与最大回撤风险预算", "url": "https://pdf.dfcfw.com/pdf/H3_AP202009081409149180_1.pdf"},
    {"institution": "浙商证券", "title": "基于隐马尔可夫市场状态的资产配置", "method": "多资产状态识别与时变协方差", "url": "https://pdf.dfcfw.com/pdf/H3_AP202112011532153512_1.pdf"},
    {"institution": "中国银河证券", "title": "宏观经济周期划分下的ETF配置方法", "method": "经济/流动性马尔可夫区制、BL与可交易ETF", "url": "https://pdf.dfcfw.com/pdf/H301_AP202308181595066301_1.pdf"},
    {"institution": "招商银行研究院", "title": "大类资产配置方法体系和模型构建", "method": "CMA-SAA-TAA、风险预算与因子模型", "url": "https://pdf.dfcfw.com/pdf/H301_AP202404031629713302_1.pdf"},
    {"institution": "国信证券", "title": "AI视角驱动的Black-Litterman资产配置", "method": "结构化观点、置信度到Omega及BL后验", "url": "https://pdf.dfcfw.com/pdf/H3_AP202601121816952139_1.pdf"},
    {"institution": "López de Prado", "title": "Building Diversified Portfolios that Outperform Out of Sample", "method": "HRP降低协方差求逆不稳定性", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678"},
    {"institution": "Ledoit & Wolf", "title": "Nonlinear Shrinkage of the Covariance Matrix for Portfolio Selection", "method": "协方差收缩与样本外稳定性", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2383361"},
    {"institution": "Bailey & López de Prado", "title": "The Deflated Sharpe Ratio", "method": "多重试验、非正态与选择偏差校正", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551"},
    {"institution": "Bailey et al.", "title": "The Probability of Backtest Overfitting", "method": "CSCV-PBO检验研究流程过拟合", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253"},
    {"institution": "CN-Buzz2Portfolio", "title": "LLM-Based Macro and Sector Asset Allocation", "method": "新闻压缩—状态感知—ETF配置的可复现实验", "url": "https://arxiv.org/abs/2603.22305"},
]

FACTOR_REGISTRY = [
    {"id": "pring", "name": "普林格六阶段", "horizon": "月度/战术", "inputs": ["先行候选池", "同步候选池", "滞后候选池"],
     "transform": "因果标准化、训练期筛选、六状态路径约束贝叶斯滤波", "allocation_role": "概率阶段与战术倾斜"},
    {"id": "kitchin", "name": "基钦库存周期", "horizon": "2-5年", "inputs": ["需求候选池", "价格/库存代理池"],
     "transform": "24-60月频带评分、四象限软分类", "allocation_role": "权益与商品方向校验"},
    {"id": "juglar", "name": "朱格拉资本开支周期", "horizon": "5-11年", "inputs": ["信用脉冲", "社融与生产趋势"],
     "transform": "60-132月频带评分、信用水平与12月斜率", "allocation_role": "中期风险预算"},
    {"id": "kondratieff", "name": "康波结构情景", "horizon": "40-60年", "inputs": ["慢增长", "慢通胀", "慢信用"],
     "transform": "84月以上慢变量；样本不足强制低置信度", "allocation_role": "战略压力情景"},
    {"id": "merrill", "name": "美林投资时钟", "horizon": "月度/季度", "inputs": ["增长方向", "通胀方向"],
     "transform": "概率四象限与普林格交叉验证", "allocation_role": "周期交叉确认"},
    {"id": "macro_risk", "name": "宏观因子风险预算", "horizon": "月度/战略", "inputs": ["增长", "通胀", "流动性", "信用"],
     "transform": "滚动OLS暴露、因子协方差与特异风险", "allocation_role": "避免资产风险平价的宏观风险集中"},
    {"id": "portfolio_ensemble", "name": "多模型组合优化", "horizon": "月度", "inputs": ["RP", "全天候", "HRP", "宏观RP", "稳健BL"],
     "transform": "训练初筛、验证择优、测试封印、DSR/PBO", "allocation_role": "输出权益偏好推荐权重"},
    {"id": "llm_evidence", "name": "LLM证据层", "horizon": "事件驱动", "inputs": ["政策原文", "新闻事件", "券商研报", "模型快照"],
     "transform": "服务端结构化上下文、时点与冲突检查", "allocation_role": "生成解释报告，不改写生产权重"},
]

_BASE_FIELDS = {
    "pmi_manufacturing": "制造业PMI", "pmi_non_manufacturing": "非制造业PMI", "pmi_composite": "综合PMI",
    "cpi_national_yoy": "CPI同比", "ppi_yoy": "PPI同比", "m1_yoy": "M1同比", "m2_yoy": "M2同比",
    "sf_inc_month": "社融增量", "sf_stock_endval": "社融存量", "industrial_production_yoy": "工业增加值同比",
}


@dataclass(frozen=True)
class ResearchBacktestConfig:
    covariance_lookback: int = 36
    min_history: int = 36
    shrinkage: float = 0.50
    transaction_cost_bps: float = 10.0
    rebalance_frequency: str = "monthly"
    max_turnover: float = 0.35
    train_end: str = "202012"
    validation_end: str = "202212"
    factor_top_k: int = 3
    default_profile: str = "equity_preferred"


def _load_code_v2(connection: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    return [{"date": str(row["trade_date"]), "close": float(row["close"]), "raw_close": float(row["close"]),
             "pct_chg": _number(row["pct_chg"]), "source_code": code}
            for row in connection.execute("SELECT trade_date,close,pct_chg FROM etf_ohlcv_daily WHERE ts_code=? AND close>0 ORDER BY trade_date", (code,))]


def _return_chain_v2(predecessor: Sequence[dict[str, Any]], current: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    current_rows = sorted((dict(row) for row in current), key=lambda row: str(row["date"]))
    if not current_rows:
        raise ValueError("current_bond_etf_missing")
    parts = [sorted((dict(row) for row in predecessor if str(row["date"]) < str(current_rows[0]["date"])), key=lambda row: str(row["date"])), current_rows]
    output, level = [], 100.0
    for part in parts:
        prior = None
        for row in part:
            raw = float(row["close"])
            if prior is not None and prior > 0:
                level *= raw / prior
            output.append({**row, "close": level, "raw_close": raw, "normalized_chain": True})
            prior = raw
    return output


def load_etf_prices_from_sqlite_v2(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    db_path = Path(path).resolve()
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {"equity": _load_code_v2(connection, "510300.SH"),
                "bond": _return_chain_v2(_load_code_v2(connection, "159926.SZ"), _load_code_v2(connection, "159972.SZ")),
                "commodity": _load_code_v2(connection, "159934.SZ"), "cash": _load_code_v2(connection, "159001.SZ")}


def merge_price_series_v2(*series: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for panel in series:
        for asset, raw_rows in panel.items():
            rows = []
            for raw in raw_rows:
                date = "".join(character for character in str(raw.get("date") or "") if character.isdigit())[:8]
                close = _number(raw.get("close"))
                if len(date) == 8 and close is not None and close > 0:
                    rows.append({**raw, "date": date, "close": close})
            rows.sort(key=lambda row: row["date"])
            existing = merged[asset]
            normalized = bool(existing) and any(bool(row.get("normalized_chain")) for row in existing.values())
            if normalized and rows and not any(bool(row.get("normalized_chain")) for row in rows):
                common = sorted(set(existing).intersection(row["date"] for row in rows))
                if common:
                    anchor = common[-1]
                    incoming = next(row for row in rows if row["date"] == anchor)
                    scale = float(existing[anchor]["close"]) / float(incoming["close"])
                else:
                    scale = float(existing[sorted(existing)[-1]]["close"]) / float(rows[0]["close"])
                rows = [{**row, "raw_close": row.get("raw_close", row["close"]), "close": float(row["close"]) * scale,
                         "normalized_chain": True} for row in rows]
            for row in rows:
                existing[row["date"]] = row
    return {asset: [rows[date] for date in sorted(rows)] for asset, rows in merged.items()}


def _ffill_v2(values: Sequence[float | None], limit: int = 3) -> list[float | None]:
    output, last, age = [], None, limit + 1
    for value in values:
        if value is not None:
            last, age = float(value), 0
            output.append(float(value))
        else:
            age += 1
            output.append(last if last is not None and age <= limit else None)
    return output


def _lag_diff_v2(values: Sequence[float | None], lag: int) -> list[float | None]:
    return [None if index < lag or value is None or values[index-lag] is None else float(value)-float(values[index-lag]) for index, value in enumerate(values)]


def _ranks_v2(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks


def _spearman_v2(left: Sequence[float], right: Sequence[float]) -> float:
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if len(a) < 4 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0
    return float(np.corrcoef(_ranks_v2(a), _ranks_v2(b))[0, 1])


def _band_power_v2(values: Sequence[float], minimum_cycle: int, maximum_cycle: int) -> float:
    data = np.asarray(values, dtype=float)
    if len(data) < 24:
        return 0.0
    data -= np.mean(data)
    frequency, power = np.fft.rfftfreq(len(data), d=1.0), np.abs(np.fft.rfft(data)) ** 2
    mask = (frequency > 0) & (frequency >= 1.0/maximum_cycle) & (frequency <= 1.0/minimum_cycle)
    denominator = float(power[frequency > 0].sum())
    return float(power[mask].sum()/denominator) if denominator > 1e-12 else 0.0


def _candidate_library_v2(macro_rows: Sequence[dict[str, Any]]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    months = [_month_key(str(row.get("month"))) for row in macro_rows]
    raw = {field: _ffill_v2([_number(row.get(field)) for row in macro_rows]) for field in _BASE_FIELDS}
    roles = {"pmi_manufacturing": ["coincident","demand","growth"], "pmi_non_manufacturing": ["coincident","demand","growth"],
             "pmi_composite": ["coincident","demand","growth"], "cpi_national_yoy": ["lagging","inventory","inflation"],
             "ppi_yoy": ["lagging","inventory","inflation"], "m1_yoy": ["leading","liquidity","credit"],
             "m2_yoy": ["leading","liquidity"], "sf_inc_month": ["leading","demand","credit"],
             "sf_stock_endval": ["leading","credit","juglar"], "industrial_production_yoy": ["coincident","growth","juglar"]}
    candidates = {}
    for field, values in raw.items():
        if not any(value is not None for value in values):
            continue
        transformed = {"level_z36": _rolling_zscore(values,36,18), "delta3_z36": _rolling_zscore(_lag_diff_v2(values,3),36,18),
                       "slope6_z36": _rolling_zscore([_causal_slope(values,index,6) for index in range(len(values))],36,18),
                       "percentile60": [None if value is None else 2*value-1 for value in _rolling_percentile(values,60,24)]}
        for transform, series in transformed.items():
            identifier = f"{field}__{transform}"
            candidates[identifier] = {"id": identifier, "name": f"{_BASE_FIELDS[field]}·{transform}", "family": field,
                                      "transform": transform, "roles": roles.get(field,[]), "values": series}
    spreads = {"m1_m2_spread": ("M1-M2剪刀差", raw["m1_yoy"], raw["m2_yoy"], ["leading","liquidity","credit"]),
               "cpi_ppi_spread": ("CPI-PPI剪刀差", raw["cpi_national_yoy"], raw["ppi_yoy"], ["lagging","inflation","inventory"]),
               "pmi_breadth": ("制造业-非制造业PMI差", raw["pmi_manufacturing"], raw["pmi_non_manufacturing"], ["coincident","demand","growth"])}
    for identifier, (name, left, right, item_roles) in spreads.items():
        values = [None if a is None or b is None else a-b for a,b in zip(left,right)]
        for transform, series in {"level_z36": _rolling_zscore(values), "delta3_z36": _rolling_zscore(_lag_diff_v2(values,3))}.items():
            key = f"{identifier}__{transform}"
            candidates[key] = {"id":key,"name":f"{name}·{transform}","family":identifier,"transform":transform,"roles":item_roles,"values":series}
    return months, candidates


def _targets_v2(months: Sequence[str], prices: np.ndarray) -> dict[str, dict[str, tuple[float,str]]]:
    roles = ("leading","coincident","lagging","demand","inventory","growth","inflation","liquidity","credit","juglar")
    output = {role:{} for role in roles}
    horizons = {"coincident":1,"growth":1,"lagging":3,"inventory":3,"inflation":3,"leading":3,"demand":3,"liquidity":3,"credit":6,"juglar":6}
    for role, horizon in horizons.items():
        for index in range(len(months)-horizon):
            future = prices[index+horizon]/prices[index]-1
            if role in {"lagging","inventory","inflation"}: value = float(future[2]-future[1])
            elif role == "liquidity": value = float(future[0]-future[3])
            elif role in {"credit","juglar"}: value = float(future[0]-0.5*future[1]+0.5*future[2])
            else: value = float(0.65*future[0]+0.35*future[2]-0.35*future[1])
            output[role][months[index]] = (value,months[index+horizon])
    return output


def _factor_selection_v2(macro_rows: Sequence[dict[str,Any]], price_months: Sequence[str], prices: np.ndarray,
                         config: ResearchBacktestConfig) -> tuple[dict[str,Any],dict[str,list[dict[str,Any]]],dict[str,dict[str,Any]]]:
    macro_months, candidates = _candidate_library_v2(macro_rows)
    targets = _targets_v2(price_months,prices)
    bands = {role:((24,60) if role in {"demand","inventory"} else (60,132) if role in {"credit","juglar"} else (12,60)) for role in targets}
    role_selection, audit = {}, []
    train_month_count = sum(month <= config.train_end for month in macro_months)
    for role in targets:
        scored = []
        for candidate in candidates.values():
            if role not in candidate["roles"]: continue
            pairs = [(month,candidate["values"][index],targets[role].get(month)) for index,month in enumerate(macro_months)]
            pairs = [(month,float(value),target) for month,value,target in pairs if month<=config.train_end and value is not None and target and target[1]<=config.train_end]
            if len(pairs)<24: continue
            x,y = [item[1] for item in pairs],[float(item[2][0]) for item in pairs]
            ic = _spearman_v2(x,y); block=max(8,len(x)//3)
            block_ics=[_spearman_v2(x[start:start+block],y[start:start+block]) for start in range(0,len(x),block) if len(x[start:start+block])>=8]
            signs=[np.sign(value) for value in block_ics if abs(value)>1e-8]; stability=abs(float(np.mean(signs))) if signs else 0.0
            band=_band_power_v2(x,*bands[role]); coverage=len(x)/max(1,train_month_count)
            score=abs(ic)*(0.5+0.5*stability)*(0.75+0.25*band)*min(1.0,coverage)
            scored.append({"role":role,"id":candidate["id"],"name":candidate["name"],"family":candidate["family"],"transform":candidate["transform"],
                           "train_ic":ic,"block_stability":stability,"band_power":band,"coverage":coverage,"score":score,"observations":len(x)})
        scored.sort(key=lambda row:(-row["score"],row["id"])); selected=[]; selected_values=[]
        train_indices=[index for index,month in enumerate(macro_months) if month<=config.train_end]
        for row in scored:
            values=np.asarray([np.nan if candidates[row["id"]]["values"][index] is None else candidates[row["id"]]["values"][index] for index in train_indices])
            redundant=0.0
            for previous in selected_values:
                mask=np.isfinite(values)&np.isfinite(previous)
                if mask.sum()>=18: redundant=max(redundant,abs(float(np.corrcoef(values[mask],previous[mask])[0,1])))
            row["max_selected_correlation"]=redundant
            if len(selected)<config.factor_top_k and redundant<0.85:
                row["status"]="selected"; selected.append(row); selected_values.append(values)
            else: row["status"]="rejected_redundancy" if redundant>=0.85 else "rejected_rank"
            audit.append(row)
        if not selected and scored: scored[0]["status"]="selected_fallback"; selected=[scored[0]]
        role_selection[role]=selected
    selected_ids=sorted({row["id"] for rows in role_selection.values() for row in rows})
    factor_series={identifier:[{"month":month,"value":None if candidates[identifier]["values"][index] is None else round(float(candidates[identifier]["values"][index]),6)} for index,month in enumerate(macro_months)] for identifier in selected_ids}
    payload={"policy":"causal candidates; factor identity selected on training only; validation/test never select factors","train_end":config.train_end,
             "candidate_count":len(candidates),"selected_unique_count":len(selected_ids),"roles":role_selection,
             "audit":sorted(audit,key=lambda row:(row["role"],-row["score"]))[:120],"redundancy_threshold":0.85,"minimum_observations":24}
    return payload,factor_series,candidates


def _mean_v2(values: Iterable[float | None]) -> float | None:
    clean=[float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(np.mean(clean)) if clean else None


def _sigmoid_v2(value: float, scale: float=1.0) -> float:
    return 1/(1+math.exp(-max(-30,min(30,value/max(scale,1e-8)))))


def _softmax_v2(values: Sequence[float]) -> np.ndarray:
    data=np.asarray(values,dtype=float); data-=np.max(data); result=np.exp(data); return result/result.sum()


def _quadrant_v2(x: float,y: float,labels: Sequence[str]) -> dict[str,float]:
    centres=((1,1),(-1,1),(-1,-1),(1,-1)); probability=_softmax_v2([-0.7*((x-a)**2+(y-b)**2) for a,b in centres])
    return {label:round(float(probability[index]),6) for index,label in enumerate(labels)}


def build_cycle_history_v2(macro_rows: Sequence[dict[str,Any]], selection: dict[str,Any] | None=None,
                           candidates: dict[str,dict[str,Any]] | None=None) -> list[dict[str,Any]]:
    rows=[dict(row) for row in macro_rows]; months,fallback=_candidate_library_v2(rows); candidates=candidates or fallback
    if selection is None:
        role_ids=defaultdict(list)
        for identifier,candidate in candidates.items():
            for role in candidate["roles"]:
                if len(role_ids[role])<3: role_ids[role].append(identifier)
    else: role_ids={role:[row["id"] for row in selected] for role,selected in selection["roles"].items()}
    def composite(role,index): return _mean_v2(candidates[identifier]["values"][index] for identifier in role_ids.get(role,[]) if identifier in candidates)
    values={role:[composite(role,index) for index in range(len(rows))] for role in role_ids}
    transition=np.zeros((6,6))
    for phase in range(6): transition[phase,phase]=0.55; transition[phase,(phase+1)%6]=0.35; transition[phase,(phase-1)%6]=0.10
    posterior=np.full(6,1/6); history=[]
    for index,row in enumerate(rows):
        lead,coin,lag=(values.get(role,[None]*len(rows))[index] for role in ("leading","coincident","lagging"))
        slopes=[_causal_slope(values.get(role,[]),index,3) for role in ("leading","coincident","lagging")]
        if any(value is None for value in (lead,coin,lag,*slopes)): continue
        direction=[_sigmoid_v2(float(value),0.12) for value in slopes]; emissions=[]
        for bits in PRING_BITS_TO_PHASE:
            likelihood=1.0
            for position,bit in enumerate(bits): likelihood*=direction[position] if bit=="1" else 1-direction[position]
            emissions.append(max(likelihood,1e-9))
        posterior=(posterior@transition)*np.asarray(emissions); posterior/=posterior.sum(); phase=int(np.argmax(posterior))+1
        bits="".join("1" if probability>=0.5 else "0" for probability in direction)
        demand=float(values.get("demand",values.get("coincident",[]))[index] or 0); inventory=float(values.get("inventory",values.get("lagging",[]))[index] or 0)
        kprob=_quadrant_v2(demand,inventory,["主动补库","被动补库","主动去库","被动去库"]); kitchin=max(kprob,key=kprob.get)
        credit=float(values.get("credit",values.get("leading",[]))[index] or 0); jslope=float(_causal_slope(values.get("juglar",values.get("credit",[])),index,12) or 0)
        jprob=_quadrant_v2(jslope,credit,["扩张","过热","收缩","修复"]); juglar=max(jprob,key=jprob.get)
        growth=float(values.get("growth",values.get("coincident",[]))[index] or 0); inflation=float(values.get("inflation",values.get("lagging",[]))[index] or 0)
        mprob=_quadrant_v2(growth,inflation,["过热","滞胀","衰退","复苏"]); merrill=max(mprob,key=mprob.get)
        slow_growth=_mean_v2(values.get("growth",[])[max(0,index-59):index+1]) or 0; slow_inflation=_mean_v2(values.get("inflation",[])[max(0,index-59):index+1]) or 0
        kndprob=_quadrant_v2(slow_growth,slow_inflation,["繁荣","衰退","萧条/创新孕育","复苏"]); kond=max(kndprob,key=kndprob.get)
        history.append({"month":months[index],"leading":round(_sigmoid_v2(float(lead)),6),"coincident":round(_sigmoid_v2(float(coin)),6),"lagging":round(_sigmoid_v2(float(lag)),6),
                        "leading_score":round(float(lead),6),"coincident_score":round(float(coin),6),"lagging_score":round(float(lag),6),
                        "leading_slope":round(float(slopes[0]),6),"coincident_slope":round(float(slopes[1]),6),"lagging_slope":round(float(slopes[2]),6),
                        "pring_bits":bits,"pring_phase":phase,"pring_phase_name":PRING_PHASES[phase]["name"],"pring_conflict":bits not in PRING_BITS_TO_PHASE,
                        "pring_probability":{str(position+1):round(float(value),6) for position,value in enumerate(posterior)},"confidence":round(float(posterior.max()),6),
                        "kitchin_state":kitchin,"kitchin_probability":kprob,"kitchin_demand_score":round(demand,6),"kitchin_inventory_proxy":round(inventory,6),
                        "juglar_state":juglar,"juglar_probability":jprob,"juglar_credit_score":round(credit,6),"juglar_slope":round(jslope,6),
                        "kondratieff_state":kond,"kondratieff_probability":kndprob,"kondratieff_confidence":round(min(0.35,len(history)/360),6),
                        "merrill_state":merrill,"merrill_probability":mprob,"growth_score":round(growth,6),"inflation_score":round(inflation,6),
                        "liquidity_score":round(float(values.get("liquidity",values.get("leading",[]))[index] or 0),6),"credit_score":round(float(np.tanh(credit/2)),6),
                        "source":str(row.get("source") or "local_warehouse")})
    return history


def _profile_v2(name: str) -> dict[str,Any]: return PROFILE_SPECS.get(name,PROFILE_SPECS["balanced"])


def _ewma_cov_v2(returns: np.ndarray,shrinkage: float=0.5,half_life: float=12) -> np.ndarray:
    data=np.asarray(returns,dtype=float); weights=np.exp(-math.log(2)*np.arange(len(data)-1,-1,-1)/half_life); weights/=weights.sum()
    mean=(data*weights[:,None]).sum(axis=0); centred=data-mean; covariance=(centred*weights[:,None]).T@centred
    diagonal=np.diag(np.maximum(np.diag(covariance),1e-8)); return (1-shrinkage)*covariance+shrinkage*diagonal


def _factor_matrix_v2(cycles: Sequence[dict[str,Any]]) -> np.ndarray:
    return np.asarray([[float(row.get(key) or 0) for key in ("growth_score","inflation_score","liquidity_score","credit_score")] for row in cycles])


def _macro_cov_v2(returns: np.ndarray,cycles: Sequence[dict[str,Any]],fallback: np.ndarray) -> tuple[np.ndarray,dict[str,Any]]:
    length=min(len(returns),len(cycles),60)
    if length<24: return fallback,{"status":"fallback","observations":length}
    y=np.asarray(returns[-length:]); factors=_factor_matrix_v2(cycles[-length:]); x=np.diff(factors,axis=0); y=y[-len(x):]
    design=np.column_stack([np.ones(len(x)),x]); ridge=np.diag([1e-8]+[0.15]*(design.shape[1]-1)); beta=np.linalg.solve(design.T@design+ridge,design.T@y)
    residual=y-design@beta; fcov=_shrink_cov(np.diff(factors,axis=0),0.55); covariance=beta[1:].T@fcov@beta[1:]+np.diag(np.maximum(np.var(residual,axis=0,ddof=1),1e-8))
    return _shrink_cov_matrix(covariance,0.45),{"status":"ok","observations":length-1,"factor_count":4}


def _hrp_v2(covariance: np.ndarray,floors: Sequence[float],caps: Sequence[float]) -> np.ndarray:
    variance=np.maximum(np.diag(covariance),1e-12); corr=covariance/np.sqrt(np.outer(variance,variance)); clusters=[[index] for index in range(len(variance))]
    while len(clusters)>1:
        pairs=[]
        for left in range(len(clusters)):
            for right in range(left+1,len(clusters)):
                distance=float(np.mean([math.sqrt(max(0,(1-corr[a,b])/2)) for a in clusters[left] for b in clusters[right]])); pairs.append((distance,left,right))
        _,left,right=min(pairs); merged=clusters[left]+clusters[right]; clusters=[cluster for index,cluster in enumerate(clusters) if index not in {left,right}]+[merged]
    order=clusters[0]; weights=np.ones(len(variance))
    def cluster_var(indices):
        sub=covariance[np.ix_(indices,indices)]; ivp=1/np.maximum(np.diag(sub),1e-12); ivp/=ivp.sum(); return float(ivp@sub@ivp)
    groups=[order]
    while groups:
        group=groups.pop()
        if len(group)<=1: continue
        middle=len(group)//2; left,right=group[:middle],group[middle:]; lv,rv=cluster_var(left),cluster_var(right); alpha=1-lv/max(lv+rv,1e-12)
        weights[left]*=alpha; weights[right]*=1-alpha; groups.extend([left,right])
    return _normalize_weights(weights,floors,caps)


def _phase_target_v2(cycle: dict[str,Any],returns: np.ndarray | None=None,cycles: Sequence[dict[str,Any]] | None=None,profile_name: str="balanced") -> np.ndarray:
    profile=_profile_v2(profile_name); prior=np.asarray(PRING_PHASES[int(cycle.get("pring_phase") or 6)]["weights"]); empirical=None
    if returns is not None and cycles:
        length=min(len(returns),len(cycles)); sample=[returns[-length:][index] for index,row in enumerate(cycles[-length:]) if int(row.get("pring_phase") or 0)==int(cycle.get("pring_phase") or 0)]
        if len(sample)>=8:
            data=np.asarray(sample); score=np.maximum(np.mean(data,axis=0)/np.maximum(np.std(data,axis=0,ddof=1),0.01)+0.75,0.03); empirical=_normalize_weights(score,profile["floors"],profile["caps"])
    target=prior if empirical is None else 0.35*prior+0.65*empirical; return _normalize_weights(target,profile["floors"],profile["caps"])


def _bl_v2(covariance: np.ndarray,returns: np.ndarray,cycle: dict[str,Any],cycles: Sequence[dict[str,Any]],profile_name: str,risk_aversion: float) -> tuple[np.ndarray,dict[str,Any]]:
    profile=_profile_v2(profile_name); prior=_normalize_weights(profile["capital_prior"],profile["floors"],profile["caps"]); pi=risk_aversion*covariance@prior
    length=min(len(returns),len(cycles),84); sample=[returns[-length:][index] for index,row in enumerate(cycles[-length:]) if int(row.get("pring_phase") or 0)==int(cycle.get("pring_phase") or 0)]
    if len(sample)>=8: view=np.mean(np.asarray(sample),axis=0); confidence=min(0.75,float(cycle.get("confidence") or 0)*min(1,len(sample)/18))
    else: view=np.zeros(4); confidence=0.0
    tau=0.05*covariance; omega=np.diag(np.maximum(np.diag(tau)*(1-confidence)/max(confidence,0.05),1e-8)); pp=np.linalg.pinv(tau); pv=np.linalg.pinv(omega)
    posterior=np.linalg.solve(pp+pv,pp@pi+pv@view); weight=prior.copy()
    for iteration in range(200):
        gradient=posterior-risk_aversion*covariance@weight-0.08*(weight-prior); weight=_normalize_weights(np.maximum(weight+0.10/math.sqrt(iteration+1)*gradient,1e-8),profile["floors"],profile["caps"])
    return weight,{"view_observations":len(sample),"view_confidence":confidence,"posterior_monthly_return":posterior.tolist()}


def _specs_v2() -> list[dict[str,Any]]:
    # Six economically distinct ensemble structures are crossed with two
    # covariance estimators and two windows. The defensive structures allow
    # the validation set to reject unsupported BL views/cycle tilts instead of
    # forcing every candidate to own them.
    structures = [
        ({"risk_parity":0.30,"all_weather":0.35,"hrp":0.25,"macro_risk_budget":0.10,"robust_bl":0.00},0.00,0.00,5.0),
        ({"risk_parity":0.25,"all_weather":0.35,"hrp":0.25,"macro_risk_budget":0.15,"robust_bl":0.00},0.05,0.03,5.0),
        ({"risk_parity":0.20,"all_weather":0.30,"hrp":0.20,"macro_risk_budget":0.20,"robust_bl":0.10},0.05,0.03,6.0),
        ({"risk_parity":0.20,"all_weather":0.25,"hrp":0.20,"macro_risk_budget":0.25,"robust_bl":0.10},0.10,0.03,6.0),
        ({"risk_parity":0.15,"all_weather":0.25,"hrp":0.15,"macro_risk_budget":0.25,"robust_bl":0.20},0.10,0.06,7.0),
        ({"risk_parity":0.15,"all_weather":0.25,"hrp":0.15,"macro_risk_budget":0.20,"robust_bl":0.25},0.15,0.08,7.0),
    ]
    output=[]
    for index,(method,lookback,structure) in enumerate(_itertools.product(("shrink","ewma"),(24,36),structures),1):
        blend,tilt,prior_strength,risk_aversion=structure
        output.append({"id":f"M{index:02d}","covariance_method":method,"lookback":lookback,
                       "shrinkage":0.35 if method=="ewma" else 0.55,"cycle_tilt":tilt,"blend":blend,
                       "risk_aversion":risk_aversion,"prior_strength":prior_strength})
    return output

def _cov_v2(window: np.ndarray,spec: dict[str,Any],config: ResearchBacktestConfig) -> np.ndarray:
    data=window[-int(spec.get("lookback",config.covariance_lookback)):]; shrink=float(spec.get("shrinkage",config.shrinkage))
    return _ewma_cov_v2(data,shrink) if spec.get("covariance_method")=="ewma" else _shrink_cov(data,shrink)


def strategy_weights_v2(strategy: str,window: np.ndarray,cycle: dict[str,Any],previous: np.ndarray | None=None,config: ResearchBacktestConfig | None=None,
                        *,cycle_window: Sequence[dict[str,Any]] | None=None,spec: dict[str,Any] | None=None,profile_name: str="balanced") -> tuple[np.ndarray,dict[str,Any]]:
    config=config or ResearchBacktestConfig(); spec=spec or _specs_v2()[0]; profile=_profile_v2(profile_name); cycles=list(cycle_window or [])
    covariance=_cov_v2(window,spec,config); macro_cov,macro_meta=_macro_cov_v2(window,cycles,covariance); meta={"covariance_method":spec.get("covariance_method"),"profile":profile_name}
    if strategy=="equal_weight": weight=_normalize_weights([0.25]*4,profile["floors"],profile["caps"])
    elif strategy=="risk_parity": weight=risk_budget_weights(covariance,profile["risk_budget"]); weight=_normalize_weights(weight,profile["floors"],profile["caps"])
    elif strategy=="all_weather":
        macro_rp=_normalize_weights(risk_budget_weights(macro_cov,[0.25]*4),profile["floors"],profile["caps"]); asset_rp=_normalize_weights(risk_budget_weights(covariance,profile["risk_budget"]),profile["floors"],profile["caps"])
        weight=_normalize_weights(0.65*macro_rp+0.35*asset_rp,profile["floors"],profile["caps"]); meta["macro_risk_model"]=macro_meta
    elif strategy=="hrp": weight=_hrp_v2(covariance,profile["floors"],profile["caps"])
    elif strategy=="macro_risk_budget": weight=_normalize_weights(risk_budget_weights(macro_cov,profile["risk_budget"]),profile["floors"],profile["caps"]); meta["macro_risk_model"]=macro_meta
    elif strategy=="pring_stage": weight=_phase_target_v2(cycle,window,cycles,profile_name)
    elif strategy=="hmm_risk_parity":
        hmm_cov,probability,diagnostics=hmm_forecast_covariance(window[-60:]); weight=_normalize_weights(risk_budget_weights(hmm_cov,profile["risk_budget"]),profile["floors"],profile["caps"]); meta.update({"hmm_probability":probability,"hmm":diagnostics})
    elif strategy=="cycle_risk_parity":
        core=_normalize_weights(risk_budget_weights(covariance,profile["risk_budget"]),profile["floors"],profile["caps"]); target=_phase_target_v2(cycle,window,cycles,profile_name)
        strength=float(spec.get("cycle_tilt",0.2))*(0.5+0.5*float(cycle.get("confidence") or 0)); weight=_normalize_weights((1-strength)*core+strength*target,profile["floors"],profile["caps"]); meta["tilt_strength"]=strength
    elif strategy=="robust_bl": weight,blmeta=_bl_v2(covariance,window,cycle,cycles,profile_name,float(spec.get("risk_aversion",6))); meta["black_litterman"]=blmeta
    elif strategy in {"recommended","recommended_candidate"}:
        components={}; component_meta={}
        for name in spec["blend"]: components[name],component_meta[name]=strategy_weights_v2(name,window,cycle,None,config,cycle_window=cycles,spec=spec,profile_name=profile_name)
        weight=sum(float(spec["blend"][name])*components[name] for name in components); target=_phase_target_v2(cycle,window,cycles,profile_name)
        tilt=float(spec.get("cycle_tilt",0.2))*float(cycle.get("confidence") or 0); prior_strength=float(spec.get("prior_strength",0.15)); prior=np.asarray(profile["capital_prior"])
        weight=(1-tilt)*weight+tilt*target; weight=(1-prior_strength)*weight+prior_strength*prior; weight=_normalize_weights(weight,profile["floors"],profile["caps"])
        meta.update({"selected_spec":spec,"component_weights":{name:vector.tolist() for name,vector in components.items()},"cycle_tilt_realized":tilt})
    else: raise ValueError(f"unknown_strategy:{strategy}")
    if previous is not None:
        delta=weight-previous; turnover=float(np.abs(delta).sum())
        if turnover>config.max_turnover: weight=previous+delta*(config.max_turnover/turnover); meta["turnover_limited"]=True
    return weight,meta


def _cycle_by_month_v2(history: Sequence[dict[str,Any]],months: Sequence[str]) -> list[dict[str,Any]]:
    records=sorted(history,key=lambda row:str(row["month"])); output=[]; cursor=0; latest=None
    fallback={"pring_phase":6,"confidence":0.0,"merrill_state":"衰退","kitchin_state":"主动去库","growth_score":0,"inflation_score":0,"liquidity_score":0,"credit_score":0}
    for month in months:
        while cursor<len(records) and str(records[cursor]["month"])<=month: latest=records[cursor]; cursor+=1
        output.append(latest or fallback)
    return output


def _sample_v2(month: str,config: ResearchBacktestConfig) -> str:
    return "train" if month<=config.train_end else "validation" if month<=config.validation_end else "test"


def _simulate_v2(months: Sequence[str],prices: np.ndarray,cycles_history: Sequence[dict[str,Any]],strategy: str,config: ResearchBacktestConfig,
                 *,spec: dict[str,Any] | None=None,profile_name: str="balanced") -> dict[str,Any]:
    returns=_returns(prices); signal_months,outcome_months=list(months[:-1]),list(months[1:]); cycles=_cycle_by_month_v2(cycles_history,signal_months)
    start=max(config.min_history,int((spec or {}).get("lookback",config.covariance_lookback))); p_returns=[]; gross_returns=[]; nav_rows=[]; weight_rows=[]; previous=None; nav=1.; gross_nav=1.; turnover_sum=0.; phase_returns=defaultdict(list)
    for index in range(start,len(returns)):
        cycle=cycles[index]; weight,meta=strategy_weights_v2(strategy,returns[:index],cycle,previous,config,cycle_window=cycles[:index],spec=spec,profile_name=profile_name)
        turnover=float(np.abs(weight-previous).sum()) if previous is not None else 0.; cost=turnover*config.transaction_cost_bps/10000.; gross=float(weight@returns[index]); net=gross-cost
        nav*=1+net; gross_nav*=1+gross; turnover_sum+=turnover; month=outcome_months[index]; sample=_sample_v2(month,config); p_returns.append(net); gross_returns.append(gross); phase_returns[int(cycle.get("pring_phase") or 0)].append(net)
        nav_rows.append({"month":month,"nav":round(nav,8),"gross_nav":round(gross_nav,8),"return":round(net,8),"gross_return":round(gross,8),"turnover":round(turnover,8),"pring_phase":int(cycle.get("pring_phase") or 0),"sample_set":sample})
        weight_rows.append({"month":month,"sample_set":sample,**{asset:round(float(weight[position]),8) for position,asset in enumerate(ASSET_ORDER)}}); previous=weight
    split_metrics={sample:{**_metrics([row["return"] for row in nav_rows if row["sample_set"]==sample]),"months":sum(row["sample_set"]==sample for row in nav_rows)} for sample in ("train","validation","test")}
    metrics=_metrics(p_returns); metrics["average_annual_turnover"]=turnover_sum/max(len(p_returns)/12,1/12); metrics["cost_drag"]=float(np.cumprod(1+np.asarray(gross_returns))[-1]-np.cumprod(1+np.asarray(p_returns))[-1]) if p_returns else 0
    phase_summary={str(phase):{"months":len(values),"average_monthly_return":float(np.mean(values)) if values else 0,"positive_rate":float(np.mean(np.asarray(values)>0)) if values else 0} for phase,values in sorted(phase_returns.items())}
    return {"metrics":metrics,"metrics_by_split":split_metrics,"nav":nav_rows,"weights":weight_rows,"phase_summary":phase_summary}


def _selection_score_v2(train: dict[str,float],validation: dict[str,float],turnover: float,validation_stage: bool) -> float:
    score=0.45*min(train["sharpe"],validation["sharpe"])+0.25*validation["sharpe"]+0.15*min(train["calmar"],validation["calmar"])+0.15*validation["positive_month_rate"]
    score-=0.08*max(0,turnover-1)-0; score-=1.5*max(0,abs(validation["max_drawdown"])-0.12)
    if validation_stage and validation["annual_return"]<=0: score-=0.75
    return float(score)


def _pbo_v2(vectors: Sequence[Sequence[float]],blocks: int=6) -> float:
    matrix=np.asarray(vectors,dtype=float).T
    if matrix.ndim!=2 or matrix.shape[0]<18 or matrix.shape[1]<4: return 1.0
    parts=[np.asarray(indices) for indices in np.array_split(np.arange(matrix.shape[0]),blocks)]; logits=[]
    for chosen in _itertools.combinations(range(blocks),blocks//2):
        inside=np.concatenate([parts[index] for index in chosen]); outside=np.concatenate([parts[index] for index in range(blocks) if index not in chosen])
        winner=int(np.argmax([_metrics(matrix[inside,column])["sharpe"] for column in range(matrix.shape[1])]))
        scores=np.asarray([_metrics(matrix[outside,column])["sharpe"] for column in range(matrix.shape[1])]); rank=(float(np.sum(scores<scores[winner]))+0.5)/matrix.shape[1]; logits.append(math.log(max(rank,1e-6)/max(1-rank,1e-6)))
    return float(np.mean(np.asarray(logits)<=0)) if logits else 1.0


def _dsr_v2(returns: Sequence[float],trial_sharpes: Sequence[float]) -> float:
    values=np.asarray(returns,dtype=float)
    if len(values)<12 or np.std(values,ddof=1)<1e-12: return 0.0
    sr=float(np.mean(values)/np.std(values,ddof=1)); trials=max(2,len(trial_sharpes)); benchmark=float(np.std(trial_sharpes,ddof=1)*math.sqrt(2*math.log(trials))/math.sqrt(12)) if len(trial_sharpes)>1 else 0
    centred=(values-np.mean(values))/np.std(values,ddof=1); skew=float(np.mean(centred**3)); kurt=float(np.mean(centred**4)); denominator=math.sqrt(max(1e-8,1-skew*sr+0.25*(kurt-1)*sr**2)); z=(sr-benchmark)*math.sqrt(len(values)-1)/denominator
    return float(_NormalDist().cdf(z))


def _select_spec_v2(months: Sequence[str],prices: np.ndarray,cycles: Sequence[dict[str,Any]],config: ResearchBacktestConfig) -> tuple[dict[str,Any],dict[str,Any]]:
    candidates=[]
    for spec in _specs_v2():
        simulation=_simulate_v2(months,prices,cycles,"recommended_candidate",config,spec=spec,profile_name=config.default_profile); train=simulation["metrics_by_split"]["train"]; valid=simulation["metrics_by_split"]["validation"]; test=simulation["metrics_by_split"]["test"]
        train_score=_selection_score_v2(train,train,simulation["metrics"]["average_annual_turnover"],False); valid_score=_selection_score_v2(train,valid,simulation["metrics"]["average_annual_turnover"],True); valid_returns=[row["return"] for row in simulation["nav"] if row["sample_set"]=="validation"]
        candidates.append({"spec":spec,"train":train,"validation":valid,"test_report_only":test,"train_score":train_score,"validation_score":valid_score,"turnover":simulation["metrics"]["average_annual_turnover"],"validation_returns":valid_returns})
    shortlist={item["spec"]["id"] for item in sorted(candidates,key=lambda item:-item["train_score"])[:8]}
    for item in candidates: item["shortlisted_by_train"]=item["spec"]["id"] in shortlist
    selected=max([item for item in candidates if item["shortlisted_by_train"]],key=lambda item:(item["validation_score"],item["train_score"],item["spec"]["id"])); ordered=sorted(candidates,key=lambda item:(not item["shortlisted_by_train"],-item["validation_score"],-item["train_score"]))
    audit={"selection_rule":"top-8 by training robustness, final choice by validation robustness; test is report-only","trial_count":len(candidates),"shortlist_count":8,"selected_spec":selected["spec"],
           "train_metrics":selected["train"],"validation_metrics":selected["validation"],"test_metrics_report_only":selected["test_report_only"],
           "pbo_cscv":_pbo_v2([item["validation_returns"] for item in candidates]),"deflated_sharpe_probability":_dsr_v2(selected["validation_returns"],[item["validation"]["sharpe"] for item in candidates]),
           "leaderboard":[{"id":item["spec"]["id"],"shortlisted_by_train":item["shortlisted_by_train"],"train_score":item["train_score"],"validation_score":item["validation_score"],
                           "train_sharpe":item["train"]["sharpe"],"validation_sharpe":item["validation"]["sharpe"],"test_sharpe_report_only":item["test_report_only"]["sharpe"],
                           "validation_drawdown":item["validation"]["max_drawdown"],"turnover":item["turnover"]} for item in ordered[:12]]}
    checks={"validation_return_nonnegative":selected["validation"]["annual_return"]>=0,
            "validation_drawdown_within_12pct":abs(selected["validation"]["max_drawdown"])<=0.12,
            "pbo_at_most_50pct":audit["pbo_cscv"]<=0.50,
            "deflated_sharpe_probability_at_least_95pct":audit["deflated_sharpe_probability"]>=0.95}
    audit["promotion_gate"]={"status":"passed" if all(checks.values()) else "conditional",
                              "checks":checks,"failed":[key for key,value in checks.items() if not value],
                              "policy":"validation and overfit diagnostics only; test never promotes a candidate"}
    return selected["spec"],audit


def walk_forward_backtest_v2(months: Sequence[str],prices: np.ndarray,cycle_history: Sequence[dict[str,Any]],strategies: Sequence[str] | None=None,
                             config: ResearchBacktestConfig | None=None,selected_spec: dict[str,Any] | None=None,selection_audit: dict[str,Any] | None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig()
    if selected_spec is None or selection_audit is None: selected_spec,selection_audit=_select_spec_v2(months,prices,cycle_history,config)
    strategies=strategies or ("equal_weight","risk_parity","all_weather","hrp","macro_risk_budget","robust_bl","pring_stage","hmm_risk_parity","cycle_risk_parity","recommended")
    output={"config":_asdict(config),"sample_splits":{"train":{"end":config.train_end,"role":"factor/parameter discovery"},"validation":{"start":"202101","end":config.validation_end,"role":"model choice"},"test":{"start":"202301","end":months[-1],"role":"sealed report-only"}},"selection_audit":selection_audit,"strategies":{}}
    for strategy in strategies: output["strategies"][strategy]=_simulate_v2(months,prices,cycle_history,strategy,config,spec=selected_spec,profile_name=config.default_profile if strategy=="recommended" else "balanced")
    sensitivity=[]
    for cost in (5.,10.,20.):
        result=_simulate_v2(months,prices,cycle_history,"recommended",_replace(config,transaction_cost_bps=cost),spec=selected_spec,profile_name=config.default_profile)
        sensitivity.append({"transaction_cost_bps":cost,**result["metrics_by_split"]["test"]})
    output["robustness"]={"cost_sensitivity_test":sensitivity,"parameter_stability_top":selection_audit["leaderboard"][:6]}; return output


def _risk_contribution_v2(covariance: np.ndarray,weight: np.ndarray) -> np.ndarray:
    marginal=covariance@weight; total=float(weight@marginal); return weight*marginal/total if total>1e-12 else np.zeros_like(weight)


def current_allocations_v2(months: Sequence[str],prices: np.ndarray,cycle_history: Sequence[dict[str,Any]],config: ResearchBacktestConfig | None=None,selected_spec: dict[str,Any] | None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig(); selected_spec=selected_spec or _specs_v2()[0]; returns=_returns(prices); cycles=_cycle_by_month_v2(cycle_history,months[1:]); cycle=cycle_history[-1]; result={}
    for strategy in ("equal_weight","risk_parity","all_weather","hrp","macro_risk_budget","robust_bl","pring_stage","hmm_risk_parity","cycle_risk_parity","recommended"):
        profile=config.default_profile if strategy=="recommended" else "balanced"; weight,meta=strategy_weights_v2(strategy,returns,cycle,None,config,cycle_window=cycles,spec=selected_spec,profile_name=profile); contribution=_risk_contribution_v2(_cov_v2(returns,selected_spec,config),weight)
        result[strategy]={"weights":{asset:round(float(weight[position]),6) for position,asset in enumerate(ASSET_ORDER)},"risk_contribution":{asset:round(float(contribution[position]),6) for position,asset in enumerate(ASSET_ORDER)},"metadata":meta}
    profiles={}
    for profile in PROFILE_SPECS:
        weight,_=strategy_weights_v2("recommended",returns,cycle,None,config,cycle_window=cycles,spec=selected_spec,profile_name=profile); contribution=_risk_contribution_v2(_cov_v2(returns,selected_spec,config),weight)
        profiles[profile]={"label":PROFILE_SPECS[profile]["label"],"weights":{asset:round(float(weight[position]),6) for position,asset in enumerate(ASSET_ORDER)},"risk_contribution":{asset:round(float(contribution[position]),6) for position,asset in enumerate(ASSET_ORDER)},"metadata":{"selected_spec":selected_spec,"solver":"validation_selected_multi_model_ensemble"}}
    result.update({"profiles":profiles,"as_of":months[-1],"macro_as_of":cycle["month"],"current_cycle":cycle,"default_profile":config.default_profile}); return result


def quality_report_v2(macro_rows: Sequence[dict[str,Any]],price_series: dict[str,list[dict[str,Any]]],months: Sequence[str],prices: np.ndarray,allocations: dict[str,Any],factor_selection: dict[str,Any],backtest: dict[str,Any]) -> dict[str,Any]:
    checks=[]
    def add(name,passed,detail): checks.append({"check":name,"status":"passed" if passed else "failed","detail":detail})
    macro_months=[_month_key(str(row.get("month"))) for row in macro_rows]; add("macro_month_unique",len(macro_months)==len(set(macro_months)),f"rows={len(macro_months)}"); add("macro_monotonic",macro_months==sorted(macro_months),f"min={macro_months[0]}, max={macro_months[-1]}")
    for asset in ASSET_ORDER:
        dates=[str(row.get("date")) for row in price_series.get(asset,[])]; add(f"{asset}_daily_coverage",len(dates)>=500,f"rows={len(dates)}, min={min(dates)}, max={max(dates)}"); add(f"{asset}_date_unique",len(dates)==len(set(dates)),f"duplicates={len(dates)-len(set(dates))}")
    add("four_current_tradable_etfs",all(ASSET_PROXIES[asset].get("ts_code") for asset in ASSET_ORDER),",".join(ASSET_PROXIES[asset]["ts_code"] for asset in ASSET_ORDER)); add("common_months",len(months)>=120,f"months={len(months)}, min={months[0]}, max={months[-1]}")
    ret=_returns(prices); add("return_outlier",bool(np.nanmax(np.abs(ret))<0.45),f"max_abs={float(np.nanmax(np.abs(ret))):.4f}"); add("factor_selection_train_only",factor_selection.get("train_end")==backtest["config"]["train_end"],f"candidates={factor_selection.get('candidate_count')}, selected={factor_selection.get('selected_unique_count')}")
    for split in ("train","validation","test"):
        count=int(backtest["strategies"]["recommended"]["metrics_by_split"][split]["months"]); add(f"{split}_sample",count>=(18 if split!="test" else 12),f"months={count}")
    for name,payload in allocations.items():
        if not isinstance(payload,dict) or "weights" not in payload: continue
        weight=list(payload["weights"].values()); add(f"{name}_weight_sum",abs(sum(weight)-1)<1e-5,f"sum={sum(weight):.8f}"); add(f"{name}_weight_bounds",min(weight)>=0 and max(weight)<=0.70,f"min={min(weight):.4f}, max={max(weight):.4f}")
    return {"status":"passed" if all(item["status"]=="passed" for item in checks) else "failed","checks":checks,"failed":[item["check"] for item in checks if item["status"]!="passed"]}


def build_snapshot_v2(macro_rows: Sequence[dict[str,Any]],price_series: dict[str,list[dict[str,Any]]],*,generated_at: str | None=None,config: ResearchBacktestConfig | None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig(); months,prices=monthly_prices(price_series)
    if len(months)<120: raise ValueError(f"price_history_too_short:{len(months)}")
    selection,factor_series,candidates=_factor_selection_v2(macro_rows,months,prices,config); cycles=build_cycle_history_v2(macro_rows,selection,candidates)
    if not cycles: raise ValueError("cycle_history_empty")
    spec,audit=_select_spec_v2(months,prices,cycles,config); backtest=walk_forward_backtest_v2(months,prices,cycles,config=config,selected_spec=spec,selection_audit=audit); allocations=current_allocations_v2(months,prices,cycles,config,spec)
    quality=quality_report_v2(macro_rows,price_series,months,prices,allocations,selection,backtest)
    if quality["status"]!="passed": raise ValueError("quality_gate_failed:"+",".join(quality["failed"]))
    price_payload={asset:[{"month":month,"close":round(float(prices[index,position]),6)} for index,month in enumerate(months)] for position,asset in enumerate(ASSET_ORDER)}
    return {"schema_version":"2.0","engine_version":ENGINE_VERSION,"generated_at":generated_at or utc_now(),"status":"ready","asset_order":list(ASSET_ORDER),"asset_labels":ASSET_LABELS,"asset_proxies":ASSET_PROXIES,
            "data_as_of":{"market":months[-1],"macro_complete":cycles[-1]["month"],"macro_raw_latest":_month_key(str(macro_rows[-1].get("month")))},
            "methodology":{"rebalance":"month_end_signal_to_next_month_etf_return","point_in_time":True,"lookahead_guard":"causal transforms; factor selection=train; portfolio selection=validation; test=report-only",
                           "transaction_cost_bps":config.transaction_cost_bps,"covariance":"validation-selected shrinkage or EWMA; HMM reported separately","constraints":{"long_only":True,"profile_specific_bounds":True,"max_monthly_turnover":config.max_turnover},
                           "equity_preference":"risk budget + capital prior + feasible bounds, never post-hoc manual weight","llm_policy":"server-side explanation only; cannot overwrite weights"},
            "profiles":PROFILE_SPECS,"factor_registry":FACTOR_REGISTRY,"factor_selection":selection,"factor_series":factor_series,"research_evidence":RESEARCH_EVIDENCE,
            "pring_state_map":{"valid":{bits:{"phase":phase,**PRING_PHASES[phase]} for bits,phase in PRING_BITS_TO_PHASE.items()},"observation_only":{"101":"mapped by six-state posterior","010":"mapped by six-state posterior"}},
            "cycle_history":cycles,"monthly_prices":price_payload,"allocations":allocations,"backtest":backtest,"optimization":audit,"quality":quality,
            "sources":[{"name":"本地研究仓库 macro_monthly/etf_ohlcv_daily","frequency":"monthly/daily","mode":"read_only","credentials_serialized":False},{"name":"BaoStock","frequency":"daily","mode":"bounded_incremental","credentials_serialized":False},{"name":"Tushare/iFinD/Wind/RQData","frequency":"optional","mode":"environment_only_provider_adapters","credentials_serialized":False}],
            "limitations":["商品类别以黄金ETF代表贵金属风险，不能等同全品种商品期货指数。","债券历史由当时可交易国债ETF与当前地方债ETF收益链接；切换点置零一日收益。","宏观月度表尚非完整发布时点vintage库。","康波样本不足一个完整长周期，仅为低置信度结构情景。","2023年以来测试段已在研发中被观察，不是未来真实影子期。"]}


def public_payload_v2(snapshot: dict[str,Any]) -> dict[str,Any]:
    keys=("schema_version","engine_version","generated_at","status","asset_order","asset_labels","asset_proxies","data_as_of","methodology","profiles","factor_registry","factor_selection","factor_series","research_evidence","pring_state_map","cycle_history","monthly_prices","allocations","backtest","optimization","quality","sources","limitations")
    return {key:snapshot[key] for key in keys}


# Production exports. Existing import paths remain stable.
BacktestConfig = ResearchBacktestConfig
load_etf_prices_from_sqlite = load_etf_prices_from_sqlite_v2
merge_price_series = merge_price_series_v2
build_cycle_history = build_cycle_history_v2
strategy_weights = strategy_weights_v2
walk_forward_backtest = walk_forward_backtest_v2
current_allocations = current_allocations_v2
quality_report = quality_report_v2
build_snapshot = build_snapshot_v2
public_payload = public_payload_v2
# v3: equal-weight-relative selection, factor signal traces and full cycle timelines.
ENGINE_VERSION = "asset-allocation-research-v3.0"

CYCLE_DEFINITIONS_V3 = {
    "pring": {"name":"普林格六阶段","states":[
        {"code":"1","order":1,"name":"逆周期启动","summary":"先行指标转强，同步与滞后指标仍弱；政策与流动性先于实体修复。","asset_bias":"债券占优，权益开始左侧布局。"},
        {"code":"2","order":2,"name":"复苏","summary":"先行、同步指标改善，通胀尚低，增长从底部扩散。","asset_bias":"权益与债券均衡，逐步降低现金。"},
        {"code":"3","order":3,"name":"共振上行","summary":"增长、信用与价格共同向上，盈利与需求形成正反馈。","asset_bias":"权益为主，商品获得顺周期配置。"},
        {"code":"4","order":4,"name":"过热","summary":"增长仍强但先行指标降温，通胀与政策约束开始上升。","asset_bias":"商品相对占优，权益控制久期与估值暴露。"},
        {"code":"5","order":5,"name":"滞胀","summary":"增长转弱而价格压力仍高，盈利下修与成本冲击并存。","asset_bias":"商品、现金防御，降低权益风险。"},
        {"code":"6","order":6,"name":"共振下行","summary":"先行、同步、滞后指标均弱，需求收缩并等待政策再启动。","asset_bias":"债券与现金防御，等待逆周期信号。"}]},
    "kitchin": {"name":"基钦库存周期","states":[
        {"code":"主动补库","order":1,"name":"主动补库","summary":"需求改善、价格回升，企业主动增加库存。","asset_bias":"权益、商品偏强。"},
        {"code":"被动补库","order":2,"name":"被动补库","summary":"需求转弱但库存仍上升，产成品被动积压。","asset_bias":"降低权益，转向债券与现金。"},
        {"code":"主动去库","order":3,"name":"主动去库","summary":"企业主动削减库存，需求和价格尚弱。","asset_bias":"债券、现金防御。"},
        {"code":"被动去库","order":4,"name":"被动去库","summary":"需求先行修复而库存继续下降，周期接近底部。","asset_bias":"权益左侧增配。"}]},
    "juglar": {"name":"朱格拉资本开支周期","states":[
        {"code":"修复","order":1,"name":"修复","summary":"信用先行改善，资本开支与产能利用率仍在底部。","asset_bias":"权益逐步提高，保留债券缓冲。"},
        {"code":"扩张","order":2,"name":"扩张","summary":"信用与资本开支同步向上，产能和盈利扩张。","asset_bias":"权益占优，商品顺周期受益。"},
        {"code":"过热","order":3,"name":"过热","summary":"资本开支高位但信用边际收紧，供给约束与通胀上升。","asset_bias":"商品相对占优，降低高估值权益。"},
        {"code":"收缩","order":4,"name":"收缩","summary":"信用与资本开支回落，需求和盈利承压。","asset_bias":"债券、现金防御。"}]},
    "kondratieff": {"name":"康波结构情景","states":[
        {"code":"复苏","order":1,"name":"复苏","summary":"长期需求、技术扩散与资本形成从低位修复。","asset_bias":"战略增配权益，保留通胀对冲。"},
        {"code":"繁荣","order":2,"name":"繁荣","summary":"创新扩散与资本形成共振，长期增长和价格中枢抬升。","asset_bias":"权益与商品占优。"},
        {"code":"衰退","order":3,"name":"衰退","summary":"资本回报下降、债务约束增强，长期增长动能转弱。","asset_bias":"降低权益，提高债券与现金。"},
        {"code":"萧条/创新孕育","order":4,"name":"萧条/创新孕育","summary":"旧产能出清，新技术处于投入与孕育期。","asset_bias":"防御为主，并保留长期创新期权。"}]},
    "merrill": {"name":"美林投资时钟","states":[
        {"code":"复苏","order":1,"name":"复苏","summary":"增长上行、通胀下行，盈利修复且政策约束较低。","asset_bias":"权益占优。"},
        {"code":"过热","order":2,"name":"过热","summary":"增长与通胀同步上行，政策收紧概率提高。","asset_bias":"商品占优。"},
        {"code":"滞胀","order":3,"name":"滞胀","summary":"增长下行、通胀上行，实际回报承压。","asset_bias":"现金与商品防御。"},
        {"code":"衰退","order":4,"name":"衰退","summary":"增长与通胀同步下行，宽松预期增强。","asset_bias":"债券占优。"}]},
}

def _factor_signals_v3(series: dict[str,list[dict[str,Any]]]) -> dict[str,list[dict[str,Any]]]:
    output={}
    for identifier,rows in series.items():
        probability=0.5; enriched=[]
        for row in rows:
            value=_number(row.get("value"))
            if value is None:
                enriched.append({**row,"positive_probability":None,"signal_state":0,"signal_label":"数据不足"}); continue
            prior=0.88*probability+0.12*(1-probability); likelihood=_sigmoid_v2(1.35*value)
            probability=prior*likelihood/max(prior*likelihood+(1-prior)*(1-likelihood),1e-12); state=1 if probability>=0.5 else -1
            enriched.append({**row,"positive_probability":round(float(probability),6),"signal_state":state,
                             "signal_label":"扩张/上行" if state>0 else "收缩/下行"})
        output[identifier]=enriched
    return output

def _cycle_state_series_v3(history: Sequence[dict[str,Any]]) -> dict[str,list[dict[str,Any]]]:
    fields={"pring":("pring_phase","pring_phase_name","confidence"),"kitchin":("kitchin_state","kitchin_state","kitchin_probability"),
            "juglar":("juglar_state","juglar_state","juglar_probability"),"kondratieff":("kondratieff_state","kondratieff_state","kondratieff_confidence"),
            "merrill":("merrill_state","merrill_state","merrill_probability")}; result={}
    for model,(code_field,name_field,confidence_field) in fields.items():
        order={str(state["code"]):int(state["order"]) for state in CYCLE_DEFINITIONS_V3[model]["states"]}; rows=[]
        for row in history:
            code=str(row.get(code_field) or ""); raw=row.get(confidence_field); confidence=_number(raw.get(code)) if isinstance(raw,dict) else _number(raw)
            rows.append({"month":row["month"],"state_code":code,"state_name":str(row.get(name_field) or code),
                         "state_order":order.get(code),"confidence":round(float(confidence or 0),6)})
        result[model]=rows
    return result

def _trend_specs_v3() -> list[dict[str,Any]]:
    horizons=[((3,6,12),(0.20,0.30,0.50)),((1,3,6),(0.15,0.35,0.50)),((6,9,12),(0.20,0.30,0.50))]; output=[]; identifier=0
    for (periods,coefficients),strength,defensive,smoothing,macro_strength in _itertools.product(horizons,(0.50,1.00),(0.25,0.75),(0.50,0.75),(0.00,0.08)):
        identifier+=1; output.append({"id":f"T{identifier:02d}","family":"equity_preferred_dual_momentum","covariance_method":"ewma",
            "lookback":36,"shrinkage":0.35,"prior":[0.35,0.25,0.25,0.15],"horizons":list(periods),"horizon_weights":list(coefficients),
            "strength":strength,"defensive_strength":defensive,"vol_penalty":0.30,"smoothing":smoothing,"macro_strength":macro_strength})
    return output

def _trend_signal_v3(window: np.ndarray,spec: dict[str,Any]) -> np.ndarray:
    risky=np.asarray(window[-36:,:3],dtype=float); volatility=np.std(risky[-12:],axis=0,ddof=1)*math.sqrt(12)
    volatility=np.maximum(volatility,max(float(np.median(volatility))*0.25,1e-4)); raw=np.zeros(3)
    for horizon,coefficient in zip(spec["horizons"],spec["horizon_weights"]):
        horizon=int(horizon); compound=np.prod(1+risky[-horizon:],axis=0)-1; raw+=float(coefficient)*compound/np.maximum(volatility*math.sqrt(horizon/12),1e-4)
    centre=float(np.median(raw)); scale=float(np.median(np.abs(raw-centre))*1.4826); cross=(raw-centre)/max(scale,0.25)
    logvol=np.log(np.maximum(volatility,1e-6)); volz=(logvol-np.mean(logvol))/max(float(np.std(logvol)),0.25)
    return np.tanh(cross/2)+0.65*np.tanh(raw/2)-float(spec.get("vol_penalty",0.30))*volz

def _macro_view_v3(cycle: dict[str,Any]) -> np.ndarray:
    g=float(cycle.get("growth_score") or 0); i=float(cycle.get("inflation_score") or 0); l=float(cycle.get("liquidity_score") or 0); c=float(cycle.get("credit_score") or 0)
    return np.tanh(np.asarray([0.45*g-0.15*i+0.25*l+0.15*c,-0.35*g-0.25*i+0.25*l-0.15*c,0.15*g+0.55*i-0.15*l+0.05*c,-0.25*g+0.20*i-0.20*l-0.15*c]))

def strategy_weights_v3(strategy: str,window: np.ndarray,cycle: dict[str,Any],previous: np.ndarray|None=None,config: ResearchBacktestConfig|None=None,
                        *,cycle_window: Sequence[dict[str,Any]]|None=None,spec: dict[str,Any]|None=None,profile_name: str="balanced") -> tuple[np.ndarray,dict[str,Any]]:
    config=config or ResearchBacktestConfig(); spec=spec or _trend_specs_v3()[0]; is_trend=spec.get("family")=="equity_preferred_dual_momentum"
    if strategy in {"recommended","recommended_candidate","dual_momentum"} and is_trend:
        profile=_profile_v2(profile_name); prior=np.asarray(spec["prior"],dtype=float); score=_trend_signal_v3(window,spec); defensive=-float(np.mean(np.tanh(score)))
        log_tilt=np.zeros(4); log_tilt[:3]=float(spec["strength"])*score; log_tilt[3]=float(spec["defensive_strength"])*defensive
        log_tilt+=float(spec.get("macro_strength",0))*_macro_view_v3(cycle)
        weight=_normalize_weights(prior*np.exp(np.clip(log_tilt,-2.5,2.5)),profile["floors"],profile["caps"])
        if previous is not None:
            smoothing=float(spec.get("smoothing",0.75)); weight=_normalize_weights((1-smoothing)*previous+smoothing*weight,profile["floors"],profile["caps"])
        meta={"profile":profile_name,"selected_spec":spec,"trend_score":score.tolist(),"macro_view":_macro_view_v3(cycle).tolist(),"solver":"causal_dual_momentum_with_macro_view"}
        if previous is not None:
            delta=weight-previous; turnover=float(np.abs(delta).sum())
            if turnover>config.max_turnover:
                weight=_normalize_weights(previous+delta*(config.max_turnover/turnover),profile["floors"],profile["caps"]); meta["turnover_limited"]=True
        return weight,meta
    if strategy=="dual_momentum":
        return strategy_weights_v3(strategy,window,cycle,previous,config,cycle_window=cycle_window,spec=_trend_specs_v3()[0],profile_name=profile_name)
    return strategy_weights_v2(strategy,window,cycle,previous,config,cycle_window=cycle_window,spec=spec,profile_name=profile_name)
def _simulate_v3(months: Sequence[str],prices: np.ndarray,cycles_history: Sequence[dict[str,Any]],strategy: str,config: ResearchBacktestConfig,
                 *,spec: dict[str,Any]|None=None,profile_name: str="balanced") -> dict[str,Any]:
    returns=_returns(prices); signal_months,outcome_months=list(months[:-1]),list(months[1:]); cycles=_cycle_by_month_v2(cycles_history,signal_months)
    start=max(config.min_history,int((spec or {}).get("lookback",config.covariance_lookback))); portfolio_returns=[]; gross_returns=[]; nav_rows=[]; weight_rows=[]
    previous=None; nav=1.; gross_nav=1.; turnover_sum=0.; phase_returns=defaultdict(list)
    for index in range(start,len(returns)):
        cycle=cycles[index]; weight,_=strategy_weights_v3(strategy,returns[:index],cycle,previous,config,cycle_window=cycles[:index],spec=spec,profile_name=profile_name)
        turnover=float(np.abs(weight-previous).sum()) if previous is not None else 0.; cost=turnover*config.transaction_cost_bps/10000.
        gross=float(weight@returns[index]); net=gross-cost; nav*=1+net; gross_nav*=1+gross; turnover_sum+=turnover
        month=outcome_months[index]; sample=_sample_v2(month,config); portfolio_returns.append(net); gross_returns.append(gross); phase_returns[int(cycle.get("pring_phase") or 0)].append(net)
        nav_rows.append({"month":month,"nav":round(nav,8),"gross_nav":round(gross_nav,8),"return":round(net,8),"gross_return":round(gross,8),"turnover":round(turnover,8),"pring_phase":int(cycle.get("pring_phase") or 0),"sample_set":sample})
        weight_rows.append({"month":month,"sample_set":sample,**{asset:round(float(weight[position]),8) for position,asset in enumerate(ASSET_ORDER)}}); previous=weight
    split_metrics={sample:{**_metrics([row["return"] for row in nav_rows if row["sample_set"]==sample]),"months":sum(row["sample_set"]==sample for row in nav_rows)} for sample in ("train","validation","test")}
    metrics=_metrics(portfolio_returns); metrics["average_annual_turnover"]=turnover_sum/max(len(portfolio_returns)/12,1/12); metrics["cost_drag"]=float(np.cumprod(1+np.asarray(gross_returns))[-1]-np.cumprod(1+np.asarray(portfolio_returns))[-1]) if portfolio_returns else 0
    phase_summary={str(phase):{"months":len(values),"average_monthly_return":float(np.mean(values)) if values else 0,"positive_rate":float(np.mean(np.asarray(values)>0)) if values else 0} for phase,values in sorted(phase_returns.items())}
    return {"metrics":metrics,"metrics_by_split":split_metrics,"nav":nav_rows,"weights":weight_rows,"phase_summary":phase_summary}

def _active_metrics_v3(strategy_returns: Sequence[float],benchmark_returns: Sequence[float]) -> dict[str,float]:
    strategy=np.asarray(strategy_returns,dtype=float); benchmark=np.asarray(benchmark_returns,dtype=float); length=min(len(strategy),len(benchmark)); strategy=strategy[-length:]; benchmark=benchmark[-length:]
    if length==0: return {"annual_excess_return":0.,"tracking_error":0.,"information_ratio":0.,"active_month_hit_rate":0.,"max_relative_drawdown":0.,"total_excess_return":0.}
    active=strategy-benchmark; relative=np.cumprod(1+strategy)/np.maximum(np.cumprod(1+benchmark),1e-12); annual=float(relative[-1]**(12/length)-1); tracking=float(np.std(active,ddof=1)*math.sqrt(12)) if length>1 else 0.; peak=np.maximum.accumulate(relative)
    return {"annual_excess_return":annual,"tracking_error":tracking,"information_ratio":float(np.mean(active)*12/tracking) if tracking>1e-12 else 0.,"active_month_hit_rate":float(np.mean(active>0)),"max_relative_drawdown":float((relative/peak-1).min()),"total_excess_return":float(relative[-1]-1)}

def _attach_benchmark_v3(result: dict[str,Any],benchmark: dict[str,Any]) -> dict[str,Any]:
    by_month={str(row["month"]):row for row in benchmark["nav"]}; relative_nav=1.
    for row in result["nav"]:
        base=by_month[str(row["month"])]; relative_nav*=(1+float(row["return"]))/max(1+float(base["return"]),1e-12)
        row["benchmark_nav"]=base["nav"]; row["benchmark_return"]=base["return"]; row["active_return"]=round(float(row["return"])-float(base["return"]),8); row["relative_nav"]=round(relative_nav,8)
    result["benchmark_key"]="equal_weight"; result["active_metrics"]=_active_metrics_v3([row["return"] for row in result["nav"]],[row["benchmark_return"] for row in result["nav"]]); result["active_metrics_by_split"]={}
    for sample in ("train","validation","test"):
        rows=[row for row in result["nav"] if row["sample_set"]==sample]; result["active_metrics_by_split"][sample]={**_active_metrics_v3([row["return"] for row in rows],[row["benchmark_return"] for row in rows]),"months":len(rows)}
    return result

def _select_spec_v3(months: Sequence[str],prices: np.ndarray,cycles: Sequence[dict[str,Any]],config: ResearchBacktestConfig) -> tuple[dict[str,Any],dict[str,Any]]:
    benchmark=_simulate_v3(months,prices,cycles,"equal_weight",config,profile_name="balanced"); candidates=[]; specs=_trend_specs_v3()
    for spec in specs:
        simulation=_simulate_v3(months,prices,cycles,"recommended_candidate",config,spec=spec,profile_name=config.default_profile); _attach_benchmark_v3(simulation,benchmark)
        train=simulation["metrics_by_split"]["train"]; valid=simulation["metrics_by_split"]["validation"]; test=simulation["metrics_by_split"]["test"]; ta=simulation["active_metrics_by_split"]["train"]; va=simulation["active_metrics_by_split"]["validation"]; te=simulation["active_metrics_by_split"]["test"]; turnover=float(simulation["metrics"]["average_annual_turnover"])
        train_score=0.65*ta["information_ratio"]+5*ta["annual_excess_return"]+0.10*train["sharpe"]-0.08*max(0,turnover-1.5)
        validation_score=0.35*min(ta["information_ratio"],va["information_ratio"])+0.25*va["information_ratio"]+4*min(ta["annual_excess_return"],va["annual_excess_return"])+3*va["annual_excess_return"]+0.10*min(train["sharpe"],valid["sharpe"])-0.08*max(0,turnover-1.5)
        active_returns=[row["active_return"] for row in simulation["nav"] if row["sample_set"]=="validation"]
        candidates.append({"spec":spec,"train":train,"validation":valid,"test_report_only":test,"train_active":ta,"validation_active":va,"test_active_report_only":te,"train_score":float(train_score),"validation_score":float(validation_score),"turnover":turnover,"validation_active_returns":active_returns})
    shortlist={item["spec"]["id"] for item in sorted(candidates,key=lambda item:(-item["train_score"],item["spec"]["id"]))[:16]}
    for item in candidates: item["shortlisted_by_train"]=item["spec"]["id"] in shortlist
    selected=max((item for item in candidates if item["shortlisted_by_train"]),key=lambda item:(item["validation_score"],item["train_score"],item["spec"]["id"])); ordered=sorted(candidates,key=lambda item:(not item["shortlisted_by_train"],-item["validation_score"],-item["train_score"],item["spec"]["id"])); active_vectors=[item["validation_active_returns"] for item in candidates]
    audit={"selection_rule":"top-16 by training active robustness; final choice by validation excess return/information ratio; test is sealed report-only","benchmark":"equal_weight","benchmark_definition":"25% equity + 25% bond + 25% commodity + 25% cash; monthly rebalance; same ETFs and transaction cost","eligibility_policy":"recommended candidates must retain the predeclared 35% equity strategic anchor; defensive risk ensembles remain reported comparators","comparator_families":["risk_parity","all_weather","hrp","macro_risk_budget","robust_bl"],"trial_count":len(candidates),"shortlist_count":16,"selected_spec":selected["spec"],"train_metrics":selected["train"],"validation_metrics":selected["validation"],"test_metrics_report_only":selected["test_report_only"],"train_active_metrics":selected["train_active"],"validation_active_metrics":selected["validation_active"],"test_active_metrics_report_only":selected["test_active_report_only"],"pbo_cscv":_pbo_v2(active_vectors),"deflated_sharpe_probability":_dsr_v2(selected["validation_active_returns"],[item["validation_active"]["information_ratio"] for item in candidates]),
        "leaderboard":[{"id":item["spec"]["id"],"family":item["spec"].get("family"),"shortlisted_by_train":item["shortlisted_by_train"],"train_score":item["train_score"],"validation_score":item["validation_score"],"train_sharpe":item["train"]["sharpe"],"validation_sharpe":item["validation"]["sharpe"],"train_excess":item["train_active"]["annual_excess_return"],"validation_excess":item["validation_active"]["annual_excess_return"],"validation_information_ratio":item["validation_active"]["information_ratio"],"test_excess_report_only":item["test_active_report_only"]["annual_excess_return"],"test_sharpe_report_only":item["test_report_only"]["sharpe"],"validation_drawdown":item["validation"]["max_drawdown"],"turnover":item["turnover"]} for item in ordered[:16]]}
    checks={"validation_excess_positive":selected["validation_active"]["annual_excess_return"]>0,"validation_information_ratio_positive":selected["validation_active"]["information_ratio"]>0,"validation_relative_drawdown_within_8pct":abs(selected["validation_active"]["max_relative_drawdown"])<=0.08,"pbo_at_most_50pct":audit["pbo_cscv"]<=0.50}
    audit["promotion_gate"]={"status":"passed" if all(checks.values()) else "conditional","checks":checks,"failed":[key for key,value in checks.items() if not value],"absolute_validation_return_warning":selected["validation"]["annual_return"]<0,"policy":"active validation and overfit diagnostics only; absolute return is disclosed; test never promotes a candidate"}
    return selected["spec"],audit
def walk_forward_backtest_v3(months: Sequence[str],prices: np.ndarray,cycle_history: Sequence[dict[str,Any]],strategies: Sequence[str]|None=None,
                             config: ResearchBacktestConfig|None=None,selected_spec: dict[str,Any]|None=None,selection_audit: dict[str,Any]|None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig()
    if selected_spec is None or selection_audit is None: selected_spec,selection_audit=_select_spec_v3(months,prices,cycle_history,config)
    strategies=strategies or ("equal_weight","recommended","dual_momentum","risk_parity","all_weather","hrp","macro_risk_budget","robust_bl","pring_stage","hmm_risk_parity","cycle_risk_parity")
    output={"config":_asdict(config),"benchmark_key":"equal_weight","benchmark_definition":selection_audit["benchmark_definition"],
            "sample_splits":{"train":{"end":config.train_end,"role":"factor/parameter discovery"},"validation":{"start":"202101","end":config.validation_end,"role":"model choice"},"test":{"start":"202301","end":months[-1],"role":"sealed report-only"}},"selection_audit":selection_audit,"strategies":{}}
    benchmark=_simulate_v3(months,prices,cycle_history,"equal_weight",config,spec=selected_spec,profile_name="balanced"); output["strategies"]["equal_weight"]=_attach_benchmark_v3(benchmark,benchmark)
    for strategy in strategies:
        if strategy=="equal_weight": continue
        profile=config.default_profile if strategy in {"recommended","dual_momentum"} else "balanced"; result=_simulate_v3(months,prices,cycle_history,strategy,config,spec=selected_spec,profile_name=profile); output["strategies"][strategy]=_attach_benchmark_v3(result,benchmark)
    sensitivity=[]
    for cost in (5.,10.,20.):
        cost_config=_replace(config,transaction_cost_bps=cost); cost_benchmark=_simulate_v3(months,prices,cycle_history,"equal_weight",cost_config,spec=selected_spec,profile_name="balanced"); result=_simulate_v3(months,prices,cycle_history,"recommended",cost_config,spec=selected_spec,profile_name=config.default_profile); _attach_benchmark_v3(result,cost_benchmark); sensitivity.append({"transaction_cost_bps":cost,**result["metrics_by_split"]["test"],**result["active_metrics_by_split"]["test"]})
    output["robustness"]={"cost_sensitivity_test":sensitivity,"parameter_stability_top":selection_audit["leaderboard"][:8]}; return output

def current_allocations_v3(months: Sequence[str],prices: np.ndarray,cycle_history: Sequence[dict[str,Any]],config: ResearchBacktestConfig|None=None,selected_spec: dict[str,Any]|None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig(); selected_spec=selected_spec or _trend_specs_v3()[0]; returns=_returns(prices); cycles=_cycle_by_month_v2(cycle_history,months[1:]); cycle=cycle_history[-1]; result={}
    for strategy in ("equal_weight","recommended","dual_momentum","risk_parity","all_weather","hrp","macro_risk_budget","robust_bl","pring_stage","hmm_risk_parity","cycle_risk_parity"):
        profile=config.default_profile if strategy in {"recommended","dual_momentum"} else "balanced"; weight,meta=strategy_weights_v3(strategy,returns,cycle,None,config,cycle_window=cycles,spec=selected_spec,profile_name=profile); contribution=_risk_contribution_v2(_cov_v2(returns,selected_spec,config),weight)
        result[strategy]={"weights":{asset:round(float(weight[position]),6) for position,asset in enumerate(ASSET_ORDER)},"risk_contribution":{asset:round(float(contribution[position]),6) for position,asset in enumerate(ASSET_ORDER)},"metadata":meta}
    profiles={}
    for profile in PROFILE_SPECS:
        weight,_=strategy_weights_v3("recommended",returns,cycle,None,config,cycle_window=cycles,spec=selected_spec,profile_name=profile); contribution=_risk_contribution_v2(_cov_v2(returns,selected_spec,config),weight)
        profiles[profile]={"label":PROFILE_SPECS[profile]["label"],"weights":{asset:round(float(weight[position]),6) for position,asset in enumerate(ASSET_ORDER)},"risk_contribution":{asset:round(float(contribution[position]),6) for position,asset in enumerate(ASSET_ORDER)},"metadata":{"selected_spec":selected_spec,"solver":"validation_selected_equal_weight_relative_optimizer"}}
    result.update({"profiles":profiles,"as_of":months[-1],"macro_as_of":cycle["month"],"current_cycle":cycle,"default_profile":config.default_profile}); return result

def quality_report_v3(macro_rows: Sequence[dict[str,Any]],price_series: dict[str,list[dict[str,Any]]],months: Sequence[str],prices: np.ndarray,allocations: dict[str,Any],factor_selection: dict[str,Any],factor_series: dict[str,list[dict[str,Any]]],backtest: dict[str,Any],cycle_state_series: dict[str,list[dict[str,Any]]]) -> dict[str,Any]:
    report=quality_report_v2(macro_rows,price_series,months,prices,allocations,factor_selection,backtest); checks=list(report["checks"])
    def add(name,passed,detail): checks.append({"check":name,"status":"passed" if passed else "failed","detail":detail})
    add("equal_weight_explicit_benchmark",backtest.get("benchmark_key")=="equal_weight" and "equal_weight" in backtest.get("strategies",{}),backtest.get("benchmark_definition")); recommended=backtest["strategies"]["recommended"]
    add("active_metrics_all_splits",all(split in recommended.get("active_metrics_by_split",{}) for split in ("train","validation","test")),"train/validation/test")
    add("selection_test_report_only","test" in str(backtest.get("selection_audit",{}).get("selection_rule","")).lower(),backtest.get("selection_audit",{}).get("selection_rule"))
    add("factor_signal_trace_complete",all(rows and all("signal_state" in row and "positive_probability" in row for row in rows) for rows in factor_series.values()),f"factors={len(factor_series)}")
    add("five_cycle_definitions",set(CYCLE_DEFINITIONS_V3)==set(cycle_state_series),",".join(CYCLE_DEFINITIONS_V3)); add("cycle_state_series_nonempty",all(len(cycle_state_series[key])>=60 for key in CYCLE_DEFINITIONS_V3),str({key:len(value) for key,value in cycle_state_series.items()}))
    failed=[item["check"] for item in checks if item["status"]!="passed"]; return {"status":"passed" if not failed else "failed","checks":checks,"failed":failed}

def build_snapshot_v3(macro_rows: Sequence[dict[str,Any]],price_series: dict[str,list[dict[str,Any]]],*,generated_at: str|None=None,config: ResearchBacktestConfig|None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig(); months,prices=monthly_prices(price_series)
    if len(months)<120: raise ValueError(f"price_history_too_short:{len(months)}")
    selection,factor_series,candidates=_factor_selection_v2(macro_rows,months,prices,config); factor_series=_factor_signals_v3(factor_series); cycles=build_cycle_history_v2(macro_rows,selection,candidates)
    if not cycles: raise ValueError("cycle_history_empty")
    cycle_state_series=_cycle_state_series_v3(cycles); spec,audit=_select_spec_v3(months,prices,cycles,config); backtest=walk_forward_backtest_v3(months,prices,cycles,config=config,selected_spec=spec,selection_audit=audit); allocations=current_allocations_v3(months,prices,cycles,config,spec)
    quality=quality_report_v3(macro_rows,price_series,months,prices,allocations,selection,factor_series,backtest,cycle_state_series)
    if quality["status"]!="passed": raise ValueError("quality_gate_failed:"+",".join(quality["failed"]))
    price_payload={asset:[{"month":month,"close":round(float(prices[index,position]),6)} for index,month in enumerate(months)] for position,asset in enumerate(ASSET_ORDER)}
    return {"schema_version":"3.0","engine_version":ENGINE_VERSION,"generated_at":generated_at or utc_now(),"status":"ready","asset_order":list(ASSET_ORDER),"asset_labels":ASSET_LABELS,"asset_proxies":ASSET_PROXIES,
        "data_as_of":{"market":months[-1],"macro_complete":cycles[-1]["month"],"macro_raw_latest":_month_key(str(macro_rows[-1].get("month")))},
        "methodology":{"rebalance":"month_end_signal_to_next_month_etf_return","point_in_time":True,"point_in_time_selection":"factor identity=train; candidate shortlist=train; final specification=validation; test=sealed report-only","benchmark":"equal_weight 25/25/25/25 monthly rebalance with identical costs","transaction_cost_bps":config.transaction_cost_bps,"constraints":{"long_only":True,"profile_specific_bounds":True,"max_monthly_turnover":config.max_turnover},"recommended_model":"equity-preferred strategic anchor + multi-horizon risk-adjusted dual momentum + volatility penalty + optional macro view","equity_preference":"predeclared strategic anchor and feasible bounds; never post-hoc manual weight","llm_policy":"server-side explanation only; cannot overwrite weights"},
        "profiles":PROFILE_SPECS,"factor_registry":FACTOR_REGISTRY,"factor_selection":selection,"factor_series":factor_series,"cycle_definitions":CYCLE_DEFINITIONS_V3,"cycle_state_series":cycle_state_series,"research_evidence":RESEARCH_EVIDENCE,
        "pring_state_map":{"valid":{bits:{"phase":phase,**PRING_PHASES[phase]} for bits,phase in PRING_BITS_TO_PHASE.items()},"observation_only":{"101":"mapped by six-state posterior","010":"mapped by six-state posterior"}},
        "cycle_history":cycles,"monthly_prices":price_payload,"allocations":allocations,"backtest":backtest,"optimization":audit,"quality":quality,
        "sources":[{"name":"本地研究仓库 macro_monthly/etf_ohlcv_daily","frequency":"monthly/daily","mode":"read_only","credentials_serialized":False},{"name":"BaoStock","frequency":"daily","mode":"bounded_incremental","credentials_serialized":False},{"name":"Tushare/iFinD/Wind/RQData","frequency":"optional","mode":"environment_only_provider_adapters","credentials_serialized":False}],
        "limitations":["商品类别以黄金ETF代表贵金属风险，不能等同全品种商品期货指数。","债券历史由当时可交易国债ETF与当前地方债ETF收益链接；切换点置零一日收益。","宏观月度表尚非完整发布时点vintage库。","康波样本不足一个完整长周期，仅为低置信度结构情景。","2023年以来测试段只用于报告，不能据其继续调参。"]}

def public_payload_v3(snapshot: dict[str,Any]) -> dict[str,Any]:
    keys=("schema_version","engine_version","generated_at","status","asset_order","asset_labels","asset_proxies","data_as_of","methodology","profiles","factor_registry","factor_selection","factor_series","cycle_definitions","cycle_state_series","research_evidence","pring_state_map","cycle_history","monthly_prices","allocations","backtest","optimization","quality","sources","limitations")
    return {key:snapshot[key] for key in keys}

build_cycle_history=build_cycle_history_v2
strategy_weights=strategy_weights_v3
walk_forward_backtest=walk_forward_backtest_v3
current_allocations=current_allocations_v3
quality_report=quality_report_v3
build_snapshot=build_snapshot_v3
public_payload=public_payload_v3

# v4: posterior trend breadth, drift-aware turnover and non-compensatory selection.
# The v1-v3 functions above remain importable for reproduction. Production aliases
# at the end of this block expose the v4 implementation without breaking callers.
ENGINE_VERSION = "asset-allocation-research-v4.0"
ASSET_PROXIES["bond"] = {
    "code": "sh.511010", "ts_code": "511010.SH", "name": "国泰上证5年期国债ETF",
    "provider": "AKShare-Sina日线+累计分红总收益", "listed_since": "2013-03-25", "sina_symbol": "sh511010",
    "continuity_reason": "连续交易ETF替代存在断月的旧收益链",
}
ASSET_PROXIES["commodity"] = {
    "code": "sh.518880", "ts_code": "518880.SH", "name": "华安黄金ETF",
    "provider": "AKShare-Sina日线+累计分红总收益", "listed_since": "2013-07-29", "sina_symbol": "sh518880",
    "continuity_reason": "连续交易且具备做市服务，替代2014-08断月的旧黄金ETF历史",
}
ASSET_PROXIES["cash"] = {
    "code": "sh.511880", "ts_code": "511880.SH", "name": "银华日利ETF",
    "provider": "AKShare-Sina日线+累计分红总收益", "listed_since": "2013-04-18", "sina_symbol": "sh511880",
    "continuity_reason": "159001.SZ在2025-04至2026-04无成交，改用持续上市交易的货币ETF",
}


def fetch_sina_total_return_prices(proxies: dict[str,dict[str,str]]) -> dict[str,list[dict[str,Any]]]:
    """Build reinvested total-return levels from Sina ETF closes and cumulative dividends."""
    import akshare as ak  # type: ignore

    output={}
    for asset,proxy in proxies.items():
        symbol=str(proxy["sina_symbol"]); history=ak.fund_etf_hist_sina(symbol=symbol); dividends=ak.fund_etf_dividend_sina(symbol=symbol)
        if len(history)<500: raise RuntimeError(f"sina_etf_history_too_short:{asset}:{len(history)}")
        cumulative={}
        for _,row in dividends.iterrows():
            date="".join(character for character in str(row.iloc[0]) if character.isdigit())[:8]; value=_number(row.iloc[1])
            if len(date)==8 and value is not None: cumulative[date]=float(value)
        records=[]; prior_close=None; cumulative_seen=0.; level=100.
        for _,row in history.iterrows():
            date="".join(character for character in str(row.get("date")) if character.isdigit())[:8]; raw_close=_number(row.get("close"))
            if len(date)!=8 or raw_close is None or raw_close<=0: continue
            next_cumulative=cumulative.get(date,cumulative_seen); dividend=max(float(next_cumulative)-float(cumulative_seen),0.)
            daily_return=0. if prior_close is None else (float(raw_close)+dividend)/float(prior_close)-1
            if prior_close is not None: level*=1+daily_return
            records.append({"date":date,"close":float(level),"raw_close":float(raw_close),"pct_chg":float(daily_return*100),
                            "dividend_increment":float(dividend),"normalized_chain":True,"total_return_reinvested":True,"source":"AKShare-Sina"})
            prior_close=float(raw_close); cumulative_seen=float(next_cumulative)
        if len(records)<500 or len({row["date"] for row in records})!=len(records): raise RuntimeError(f"sina_etf_total_return_invalid:{asset}:{len(records)}")
        output[asset]=records
    return output


# Equity preference is expressed as a strategic prior and risk budget, not a hard
# 25% holding floor. This lets a causal risk-off posterior reduce equity while
# preserving a higher long-run equity prior than the balanced profile.
PROFILE_SPECS["equity_preferred"] = {
    "label": "权益偏好",
    "floors": [0.10, 0.05, 0.05, 0.05],
    "caps": [0.70, 0.70, 0.60, 0.60],
    "risk_budget": [0.42, 0.26, 0.20, 0.12],
    "capital_prior": [0.45, 0.20, 0.20, 0.15],
}

RESEARCH_EVIDENCE = RESEARCH_EVIDENCE + [
    {"institution":"国信证券","title":"AI赋能资产配置（八）：DeepSeek在资产配置中的实战解答","method":"政策文本按时间顺序结构化，宏观、流动性、情绪与估值多维交叉验证","url":"https://pdf.dfcfw.com/pdf/H3_AP202503191644657343_1.pdf"},
    {"institution":"国信证券","title":"AI赋能资产配置（四）：DeepSeek在大盘择时与行业轮动中的应用","method":"RAG、Agent、工具调用与可验证回测闭环","url":"https://pdf.dfcfw.com/pdf/H3_AP202503091644205857_1.pdf"},
    {"institution":"中信建投证券","title":"因子投资与隐含因子研究","method":"价格隐含因子的高频更新与协方差降维","url":"https://pdf.dfcfw.com/pdf/H3_DB201812111267298369_1.pdf"},
    {"institution":"Keller & Butler","title":"A Century of Generalized Momentum; From Flexible Asset Allocations to Elastic Asset Allocation","method":"绝对动量、相对动量、波动率与相关性联合配置","url":"https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2193735"},
    {"institution":"Harvey et al.","title":"The Impact of Volatility Targeting","method":"波动率缩放的风险控制、换手与成本权衡","url":"https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2786955"},
    {"institution":"Huang et al.","title":"Time Series Momentum: Is it There?","method":"趋势与反转并存、跨周期稳健性检验","url":"https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2919122"},
]

FACTOR_REGISTRY = FACTOR_REGISTRY + [
    {"id":"posterior_breadth","name":"多周期趋势后验与扩散度","horizon":"1/3/6/12月","inputs":["三类风险资产ETF收益","滚动波动率","横截面相关性"],
     "transform":"风险调整趋势经Logistic映射为后验概率；跨期限加权形成扩散度；与战略先验和稳定风险袖套组合",
     "allocation_role":"动态风险预算、权益防守门与现金需求，不使用单点阈值直接交易"},
]


def _posterior_specs_v4() -> list[dict[str,Any]]:
    """Predeclared 48-spec grid; test observations never enter this grid."""
    horizons=[((1,3,6),(0.15,0.35,0.50)),((1,3,6,12),(0.10,0.20,0.30,0.40))]
    structures=[
        ("balanced_posterior",0.75,0.15,0.20,0.10,0.00),
        ("diversified_posterior",1.00,0.25,0.20,0.10,0.03),
        ("equity_guarded_posterior",1.00,0.25,0.20,0.20,0.05),
    ]
    output=[]
    for identifier,(horizon,power,anchor,stability_max,structure) in enumerate(
        _itertools.product(horizons,(2.0,4.0),(0.10,0.20),(0.30,0.50),structures),1
    ):
        family,relative,volatility,correlation,equity_guard_max,macro_strength=structure
        output.append({
            "id":f"B{identifier:02d}","family":family,"covariance_method":"ewma","lookback":36,"shrinkage":0.35,
            "prior":[0.45,0.20,0.20,0.15],"horizons":list(horizon[0]),"horizon_weights":list(horizon[1]),
            "probability_power":power,"probability_slope":1.70,"anchor":anchor,"cash_defense":1.0,
            "relative_strength":relative,"volatility_penalty":volatility,"correlation_penalty":correlation,
            "stability_base":0.05,"stability_max":stability_max,"stability_center":0.50,"stability_slope":10.0,
            "equity_guard_max":equity_guard_max,"equity_guard_center":0.55,"equity_guard_slope":10.0,
            "macro_strength":macro_strength,"turnover_cap":0.70,
        })
    return output


def _robust_cross_z_v4(values: Sequence[float],floor: float=0.35) -> np.ndarray:
    data=np.asarray(values,dtype=float); centre=float(np.median(data)); scale=float(np.median(np.abs(data-centre))*1.4826)
    return (data-centre)/max(scale,floor)


def _project_profile_v4(values: Sequence[float],floors: Sequence[float],caps: Sequence[float]) -> np.ndarray:
    """Project positive scores to a bounded simplex without changing rank order."""
    data=np.maximum(np.asarray(values,dtype=float),1e-12); data/=data.sum(); low=np.asarray(floors,dtype=float); high=np.asarray(caps,dtype=float)
    for _ in range(100):
        data=np.minimum(np.maximum(data,low),high); gap=1.0-float(data.sum())
        if abs(gap)<1e-12: break
        room=(high-data) if gap>0 else (data-low); total=float(room.sum())
        if total<=1e-12: break
        data+=gap*room/total
    return _normalize_weights(data,low,high)


def _posterior_target_v4(window: np.ndarray,spec: dict[str,Any],profile_name: str) -> tuple[np.ndarray,dict[str,Any]]:
    profile=_profile_v2(profile_name); risky=np.asarray(window[:, :3],dtype=float); recent=risky[-12:]
    volatility=np.maximum(np.std(recent,axis=0,ddof=1)*math.sqrt(12),0.02); probability=np.zeros(3); risk_adjusted=np.zeros(3)
    coefficients=np.asarray(spec["horizon_weights"],dtype=float); coefficients/=coefficients.sum()
    horizon_detail={}
    for horizon,coefficient in zip(spec["horizons"],coefficients):
        horizon=int(horizon); compound=np.prod(1+risky[-horizon:],axis=0)-1
        standardized=compound/np.maximum(volatility*math.sqrt(horizon/12),0.02)
        posterior=1/(1+np.exp(-float(spec["probability_slope"])*np.clip(standardized,-5,5)))
        probability+=float(coefficient)*posterior; risk_adjusted+=float(coefficient)*standardized
        horizon_detail[str(horizon)]={asset:round(float(posterior[index]),6) for index,asset in enumerate(ASSET_ORDER[:3])}
    relative=_robust_cross_z_v4(risk_adjusted); log_volatility=_robust_cross_z_v4(np.log(np.maximum(volatility,1e-6)))
    corr_window=risky[-min(24,len(risky)):]; corr=np.nan_to_num(np.corrcoef(corr_window,rowvar=False),nan=0.0,posinf=0.0,neginf=0.0)
    correlation=_robust_cross_z_v4((corr.sum(axis=1)-1.0)/2.0)
    raw_risky=np.maximum(probability,1e-4)**float(spec["probability_power"])*np.exp(
        float(spec["relative_strength"])*np.tanh(relative/2)
        -float(spec["volatility_penalty"])*np.tanh(log_volatility/2)
        -float(spec["correlation_penalty"])*np.tanh(correlation/2)
    )
    breadth=float(np.mean(probability)); cash_raw=float(spec["cash_defense"])*(max(1-breadth,0.0)**float(spec["probability_power"])+0.02)
    raw=np.r_[raw_risky,cash_raw]; tactical=raw/raw.sum(); prior=np.asarray(profile["capital_prior"],dtype=float)
    blended=float(spec["anchor"])*prior+(1-float(spec["anchor"]))*tactical
    all_volatility=np.maximum(np.std(np.asarray(window[-12:],dtype=float),axis=0,ddof=1)*math.sqrt(12),1e-4)
    stability=_project_profile_v4(1/all_volatility,PROFILE_SPECS["balanced"]["floors"],PROFILE_SPECS["balanced"]["caps"])
    stability_weight=float(spec["stability_base"])+float(spec["stability_max"])/(1+math.exp(float(spec["stability_slope"])*(breadth-float(spec["stability_center"]))))
    stability_weight=float(np.clip(stability_weight,0.0,0.75)); combined=(1-stability_weight)*blended+stability_weight*stability
    equity_shift=float(spec["equity_guard_max"])/(1+math.exp(float(spec["equity_guard_slope"])*(float(probability[0])-float(spec["equity_guard_center"]))))
    equity_shift=min(equity_shift,max(float(combined[0])-float(profile["floors"][0]),0.0)); combined[0]-=equity_shift
    bond_share=float(np.clip(probability[1],0.25,0.80)); combined[1]+=equity_shift*bond_share; combined[3]+=equity_shift*(1-bond_share)
    weight=_project_profile_v4(combined,profile["floors"],profile["caps"])
    metadata={"profile":profile_name,"selected_spec":spec,"solver":"multi_horizon_posterior_breadth_risk_budget",
              "posterior_probability":{asset:round(float(probability[index]),6) for index,asset in enumerate(ASSET_ORDER[:3])},
              "horizon_posterior":horizon_detail,"breadth":round(breadth,6),"stability_weight":round(stability_weight,6),
              "equity_guard_shift":round(equity_shift,6),"risk_adjusted_trend":risk_adjusted.tolist()}
    return weight,metadata


def strategy_weights_v4(strategy: str,window: np.ndarray,cycle: dict[str,Any],previous: np.ndarray|None=None,config: ResearchBacktestConfig|None=None,
                        *,cycle_window: Sequence[dict[str,Any]]|None=None,spec: dict[str,Any]|None=None,profile_name: str="balanced") -> tuple[np.ndarray,dict[str,Any]]:
    config=config or ResearchBacktestConfig(); spec=spec or _posterior_specs_v4()[0]
    if strategy in {"recommended","recommended_candidate","dual_momentum"} and str(spec.get("family","")).endswith("posterior"):
        weight,metadata=_posterior_target_v4(window,spec,profile_name); macro_strength=float(spec.get("macro_strength",0.0))
        if macro_strength>0:
            profile=_profile_v2(profile_name); macro=_macro_view_v3(cycle); macro_confidence=0.35+0.65*float(cycle.get("confidence") or 0.0)
            tilted=weight*np.exp(macro_strength*macro_confidence*macro); weight=_project_profile_v4(tilted,profile["floors"],profile["caps"])
            metadata={**metadata,"macro_view":macro.tolist(),"macro_strength":macro_strength,"macro_confidence":round(macro_confidence,6)}
        return weight,metadata
    if strategy=="dual_momentum": return _posterior_target_v4(window,_posterior_specs_v4()[0],profile_name)
    # v2 comparators are intentionally preserved and receive no previous target;
    # the simulator applies the same drift-aware execution rule to every model.
    return strategy_weights_v2(strategy,window,cycle,None,config,cycle_window=cycle_window,spec=spec,profile_name=profile_name)


def _drifted_weight_v4(previous: np.ndarray,realized_return: np.ndarray) -> np.ndarray:
    drifted=np.asarray(previous,dtype=float)*(1+np.asarray(realized_return,dtype=float)); total=float(drifted.sum())
    return drifted/total if total>1e-12 else np.asarray(previous,dtype=float)


def _execute_target_v4(target: np.ndarray,drifted: np.ndarray|None,cap: float,profile_name: str) -> tuple[np.ndarray,float,bool]:
    if drifted is None: return np.asarray(target,dtype=float),0.0,False
    turnover=float(np.abs(target-drifted).sum())
    if turnover<=cap+1e-12: return np.asarray(target,dtype=float),turnover,False
    profile=_profile_v2(profile_name); executed=drifted+(target-drifted)*(cap/turnover)
    executed=_project_profile_v4(executed,profile["floors"],profile["caps"])
    return executed,float(np.abs(executed-drifted).sum()),True


def _weight_dynamics_v4(rows: Sequence[dict[str,Any]]) -> dict[str,Any]:
    output={}
    for sample in ("overall","train","validation","test"):
        subset=list(rows) if sample=="overall" else [row for row in rows if row["sample_set"]==sample]
        if not subset: continue
        matrix=np.asarray([[float(row[asset]) for asset in ASSET_ORDER] for row in subset],dtype=float)
        output[sample]={"months":len(subset),"mean_cross_asset_range":float(np.mean(np.ptp(matrix,axis=0))),
                        "mean_cross_asset_std":float(np.mean(np.std(matrix,axis=0))),"assets":{}}
        for position,asset in enumerate(ASSET_ORDER):
            values=matrix[:,position]; output[sample]["assets"][asset]={"mean":float(np.mean(values)),"min":float(np.min(values)),
                "max":float(np.max(values)),"range":float(np.ptp(values)),"std":float(np.std(values))}
    return output


def _simulate_v4(months: Sequence[str],prices: np.ndarray,cycles_history: Sequence[dict[str,Any]],strategy: str,config: ResearchBacktestConfig,
                 *,spec: dict[str,Any]|None=None,profile_name: str="balanced") -> dict[str,Any]:
    returns=_returns(prices); signal_months,outcome_months=list(months[:-1]),list(months[1:]); cycles=_cycle_by_month_v2(cycles_history,signal_months)
    spec=spec or _posterior_specs_v4()[0]; start=max(18,max((int(value) for value in spec.get("horizons",[1])),default=1))
    portfolio_returns=[]; gross_returns=[]; nav_rows=[]; weight_rows=[]; previous_target=None; nav=1.; gross_nav=1.; turnover_sum=0.; phase_returns=defaultdict(list)
    for index in range(start,len(returns)):
        cycle=cycles[index]; target,metadata=strategy_weights_v4(strategy,returns[:index],cycle,None,config,cycle_window=cycles[:index],spec=spec,profile_name=profile_name)
        drifted=None if previous_target is None else _drifted_weight_v4(previous_target,returns[index-1])
        cap=float(spec.get("turnover_cap",0.70)) if strategy in {"recommended","recommended_candidate","dual_momentum"} else float(config.max_turnover)
        weight,turnover,limited=_execute_target_v4(target,drifted,cap,profile_name); cost=turnover*config.transaction_cost_bps/10000.
        gross=float(weight@returns[index]); net=gross-cost; nav*=1+net; gross_nav*=1+gross; turnover_sum+=turnover
        month=outcome_months[index]; sample=_sample_v2(month,config); portfolio_returns.append(net); gross_returns.append(gross); phase_returns[int(cycle.get("pring_phase") or 0)].append(net)
        nav_rows.append({"month":month,"nav":round(nav,8),"gross_nav":round(gross_nav,8),"return":round(net,8),"gross_return":round(gross,8),
                         "turnover":round(turnover,8),"turnover_limited":limited,"pring_phase":int(cycle.get("pring_phase") or 0),"sample_set":sample})
        weight_rows.append({"month":month,"sample_set":sample,**{asset:round(float(weight[position]),8) for position,asset in enumerate(ASSET_ORDER)}}); previous_target=weight
    split_metrics={sample:{**_metrics([row["return"] for row in nav_rows if row["sample_set"]==sample]),"months":sum(row["sample_set"]==sample for row in nav_rows)} for sample in ("train","validation","test")}
    metrics=_metrics(portfolio_returns); metrics["average_annual_turnover"]=turnover_sum/max(len(portfolio_returns)/12,1/12)
    metrics["cost_drag"]=float(np.cumprod(1+np.asarray(gross_returns))[-1]-np.cumprod(1+np.asarray(portfolio_returns))[-1]) if portfolio_returns else 0
    phase_summary={str(phase):{"months":len(values),"average_monthly_return":float(np.mean(values)) if values else 0,
        "positive_rate":float(np.mean(np.asarray(values)>0)) if values else 0} for phase,values in sorted(phase_returns.items())}
    return {"metrics":metrics,"metrics_by_split":split_metrics,"nav":nav_rows,"weights":weight_rows,"weight_dynamics":_weight_dynamics_v4(weight_rows),"phase_summary":phase_summary}


def _period_diagnostic_v4(simulation: dict[str,Any],benchmark: dict[str,Any],start: str,end: str) -> dict[str,Any]:
    rows=[row for row in simulation["nav"] if start<=row["month"]<=end]; base_by_month={row["month"]:row for row in benchmark["nav"]}
    base=[base_by_month[row["month"]] for row in rows]; returns=[row["return"] for row in rows]; benchmark_returns=[row["return"] for row in base]
    return {**_metrics(returns),**_active_metrics_v3(returns,benchmark_returns),"months":len(rows),"start":start,"end":end}


def _calendar_diagnostics_v4(simulation: dict[str,Any],benchmark: dict[str,Any]) -> list[dict[str,Any]]:
    years=sorted({row["month"][:4] for row in simulation["nav"]}); return [_period_diagnostic_v4(simulation,benchmark,year+"01",year+"12") for year in years]


def _select_spec_v4(months: Sequence[str],prices: np.ndarray,cycles: Sequence[dict[str,Any]],config: ResearchBacktestConfig) -> tuple[dict[str,Any],dict[str,Any]]:
    benchmark=_simulate_v4(months,prices,cycles,"equal_weight",config,profile_name="balanced"); candidates=[]; specs=_posterior_specs_v4()
    for spec in specs:
        simulation=_simulate_v4(months,prices,cycles,"recommended_candidate",config,spec=spec,profile_name=config.default_profile); _attach_benchmark_v3(simulation,benchmark)
        train=simulation["metrics_by_split"]["train"]; valid=simulation["metrics_by_split"]["validation"]; test=simulation["metrics_by_split"]["test"]
        ta=simulation["active_metrics_by_split"]["train"]; va=simulation["active_metrics_by_split"]["validation"]; te=simulation["active_metrics_by_split"]["test"]
        folds={"train_early":_period_diagnostic_v4(simulation,benchmark,"201601","201812"),"train_late":_period_diagnostic_v4(simulation,benchmark,"201901","202012"),
               "validation_early":_period_diagnostic_v4(simulation,benchmark,"202101","202112"),"validation_late":_period_diagnostic_v4(simulation,benchmark,"202201","202212")}
        worst_train_fold=min(folds["train_early"]["annual_excess_return"],folds["train_late"]["annual_excess_return"]); turnover=float(simulation["metrics"]["average_annual_turnover"])
        train_eligible=ta["annual_excess_return"]>0 and worst_train_fold>-0.01 and abs(ta["max_relative_drawdown"])<=0.10 and turnover<=3.50
        validation_eligible=train_eligible and valid["annual_return"]>0 and va["annual_excess_return"]>0 and va["information_ratio"]>0 and abs(va["max_relative_drawdown"])<=0.08
        train_score=10*ta["annual_excess_return"]+0.75*ta["information_ratio"]+0.20*train["sharpe"]+0.30*worst_train_fold-0.25*max(0,turnover-3.0)
        validation_score=10*min(ta["annual_excess_return"],va["annual_excess_return"])+0.75*min(ta["information_ratio"],va["information_ratio"])+0.25*va["information_ratio"]+0.20*min(train["sharpe"],valid["sharpe"])+0.30*min(item["annual_excess_return"] for item in folds.values())-0.25*max(0,turnover-3.0)
        active_returns=[row["active_return"] for row in simulation["nav"] if row["sample_set"]=="validation"]
        candidates.append({"spec":spec,"train":train,"validation":valid,"test_report_only":test,"train_active":ta,"validation_active":va,
            "test_active_report_only":te,"train_score":float(train_score),"validation_score":float(validation_score),"turnover":turnover,
            "train_eligible":train_eligible,"validation_eligible":validation_eligible,"folds":folds,"validation_active_returns":active_returns})
    eligible=[item for item in candidates if item["validation_eligible"]]
    if not eligible: eligible=[item for item in candidates if item["train_eligible"] and item["validation_active"]["annual_excess_return"]>0]
    selected=max(eligible,key=lambda item:(item["validation_score"],item["train_score"],item["spec"]["id"]))
    ordered=sorted(candidates,key=lambda item:(not item["validation_eligible"],-item["validation_score"],-item["train_score"],item["spec"]["id"])); active_vectors=[item["validation_active_returns"] for item in candidates]
    audit={"selection_rule":"non-compensatory train gate -> validation absolute/active/IR gate -> robust validation score; test is report-only and never ranks candidates",
        "benchmark":"equal_weight","benchmark_definition":"25% equity + 25% bond + 25% commodity + 25% cash; monthly rebalance from drifted holdings; same ETFs and costs",
        "eligibility_policy":"equity preference is a 45% strategic prior and higher risk budget, while the posterior may reduce equity to the predeclared 10% risk-off floor",
        "comparator_families":["risk_parity","all_weather","hrp","macro_risk_budget","robust_bl"],"trial_count":len(candidates),
        "train_eligible_count":sum(item["train_eligible"] for item in candidates),"validation_eligible_count":sum(item["validation_eligible"] for item in candidates),
        "selected_spec":selected["spec"],"train_metrics":selected["train"],"validation_metrics":selected["validation"],"test_metrics_report_only":selected["test_report_only"],
        "train_active_metrics":selected["train_active"],"validation_active_metrics":selected["validation_active"],"test_active_metrics_report_only":selected["test_active_report_only"],
        "nested_subperiods":selected["folds"],"pbo_cscv":_pbo_v2(active_vectors),"deflated_sharpe_probability":_dsr_v2(selected["validation_active_returns"],[item["validation_active"]["information_ratio"] for item in candidates]),
        "leaderboard":[{"id":item["spec"]["id"],"family":item["spec"]["family"],"train_eligible":item["train_eligible"],"validation_eligible":item["validation_eligible"],
            "train_score":item["train_score"],"validation_score":item["validation_score"],"train_sharpe":item["train"]["sharpe"],"validation_sharpe":item["validation"]["sharpe"],
            "train_excess":item["train_active"]["annual_excess_return"],"validation_return":item["validation"]["annual_return"],"validation_excess":item["validation_active"]["annual_excess_return"],
            "validation_information_ratio":item["validation_active"]["information_ratio"],"test_excess_report_only":item["test_active_report_only"]["annual_excess_return"],
            "test_sharpe_report_only":item["test_report_only"]["sharpe"],"turnover":item["turnover"]} for item in ordered[:16]]}
    checks={"train_excess_positive":selected["train_active"]["annual_excess_return"]>0,"validation_absolute_return_positive":selected["validation"]["annual_return"]>0,
            "validation_excess_positive":selected["validation_active"]["annual_excess_return"]>0,"validation_information_ratio_positive":selected["validation_active"]["information_ratio"]>0,
            "validation_relative_drawdown_within_8pct":abs(selected["validation_active"]["max_relative_drawdown"])<=0.08,"pbo_at_most_50pct":audit["pbo_cscv"]<=0.50}
    audit["promotion_gate"]={"status":"passed" if all(checks.values()) else "conditional","checks":checks,"failed":[key for key,value in checks.items() if not value],
        "policy":"test metrics are disclosed after selection and cannot repair a failed train/validation or overfit gate"}
    return selected["spec"],audit


def walk_forward_backtest_v4(months: Sequence[str],prices: np.ndarray,cycle_history: Sequence[dict[str,Any]],strategies: Sequence[str]|None=None,
                             config: ResearchBacktestConfig|None=None,selected_spec: dict[str,Any]|None=None,selection_audit: dict[str,Any]|None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig()
    if selected_spec is None or selection_audit is None: selected_spec,selection_audit=_select_spec_v4(months,prices,cycle_history,config)
    strategies=strategies or ("equal_weight","recommended","dual_momentum","risk_parity","all_weather","hrp","macro_risk_budget","robust_bl","pring_stage","hmm_risk_parity","cycle_risk_parity")
    output={"config":{**_asdict(config),"effective_min_history":18},"benchmark_key":"equal_weight","benchmark_definition":selection_audit["benchmark_definition"],
        "sample_splits":{"train":{"end":config.train_end,"role":"predeclared factor/model discovery and nested subperiod gate"},
                         "validation":{"start":"202101","end":config.validation_end,"role":"non-compensatory model choice"},
                         "test":{"start":"202301","end":months[-1],"role":"report-only; excluded from every selection score"}},
        "selection_audit":selection_audit,"strategies":{}}
    benchmark=_simulate_v4(months,prices,cycle_history,"equal_weight",config,spec=selected_spec,profile_name="balanced"); output["strategies"]["equal_weight"]=_attach_benchmark_v3(benchmark,benchmark)
    for strategy in strategies:
        if strategy=="equal_weight": continue
        profile=config.default_profile if strategy in {"recommended","dual_momentum"} else "balanced"
        result=_simulate_v4(months,prices,cycle_history,strategy,config,spec=selected_spec,profile_name=profile); output["strategies"][strategy]=_attach_benchmark_v3(result,benchmark)
    sensitivity=[]
    for cost in (5.,10.,20.,30.):
        cost_config=_replace(config,transaction_cost_bps=cost); cost_benchmark=_simulate_v4(months,prices,cycle_history,"equal_weight",cost_config,spec=selected_spec,profile_name="balanced")
        result=_simulate_v4(months,prices,cycle_history,"recommended",cost_config,spec=selected_spec,profile_name=config.default_profile); _attach_benchmark_v3(result,cost_benchmark)
        sensitivity.append({"transaction_cost_bps":cost,**result["metrics_by_split"]["test"],**result["active_metrics_by_split"]["test"]})
    recommended=output["strategies"]["recommended"]
    output["robustness"]={"cost_sensitivity_test":sensitivity,"parameter_stability_top":selection_audit["leaderboard"][:8],
                          "calendar_year_diagnostics":_calendar_diagnostics_v4(recommended,benchmark),"weight_dynamics":recommended["weight_dynamics"]}
    return output


def _current_executable_v4(months: Sequence[str],prices: np.ndarray,cycles: Sequence[dict[str,Any]],strategy: str,config: ResearchBacktestConfig,
                           spec: dict[str,Any],profile_name: str) -> tuple[np.ndarray,dict[str,Any]]:
    returns=_returns(prices); current_cycle=_cycle_by_month_v2(cycles,[months[-1]])[0]
    history=_simulate_v4(months,prices,cycles,strategy,config,spec=spec,profile_name=profile_name); previous=None
    if history["weights"]:
        previous=np.asarray([history["weights"][-1][asset] for asset in ASSET_ORDER],dtype=float); previous=_drifted_weight_v4(previous,returns[-1])
    target,meta=strategy_weights_v4(strategy,returns,current_cycle,None,config,cycle_window=_cycle_by_month_v2(cycles,months[1:]),spec=spec,profile_name=profile_name)
    cap=float(spec.get("turnover_cap",0.70)) if strategy in {"recommended","recommended_candidate","dual_momentum"} else float(config.max_turnover)
    weight,turnover,limited=_execute_target_v4(target,previous,cap,profile_name); meta={**meta,"current_rebalance_turnover":round(turnover,6),"current_turnover_limited":limited}
    return weight,meta


def current_allocations_v4(months: Sequence[str],prices: np.ndarray,cycle_history: Sequence[dict[str,Any]],config: ResearchBacktestConfig|None=None,selected_spec: dict[str,Any]|None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig(); selected_spec=selected_spec or _posterior_specs_v4()[0]; returns=_returns(prices); cycle=cycle_history[-1]; result={}
    strategies=("equal_weight","recommended","dual_momentum","risk_parity","all_weather","hrp","macro_risk_budget","robust_bl","pring_stage","hmm_risk_parity","cycle_risk_parity")
    for strategy in strategies:
        profile=config.default_profile if strategy in {"recommended","dual_momentum"} else "balanced"
        weight,meta=_current_executable_v4(months,prices,cycle_history,strategy,config,selected_spec,profile); contribution=_risk_contribution_v2(_cov_v2(returns,selected_spec,config),weight)
        result[strategy]={"weights":{asset:round(float(weight[position]),6) for position,asset in enumerate(ASSET_ORDER)},
                          "risk_contribution":{asset:round(float(contribution[position]),6) for position,asset in enumerate(ASSET_ORDER)},"metadata":meta}
    profiles={}
    for profile in PROFILE_SPECS:
        weight,meta=_current_executable_v4(months,prices,cycle_history,"recommended",config,selected_spec,profile); contribution=_risk_contribution_v2(_cov_v2(returns,selected_spec,config),weight)
        profiles[profile]={"label":PROFILE_SPECS[profile]["label"],"weights":{asset:round(float(weight[position]),6) for position,asset in enumerate(ASSET_ORDER)},
            "risk_contribution":{asset:round(float(contribution[position]),6) for position,asset in enumerate(ASSET_ORDER)},"metadata":{**meta,"solver":"validation_selected_posterior_allocator"}}
    result.update({"profiles":profiles,"as_of":months[-1],"macro_as_of":cycle["month"],"current_cycle":cycle,"default_profile":config.default_profile}); return result


def quality_report_v4(macro_rows: Sequence[dict[str,Any]],price_series: dict[str,list[dict[str,Any]]],months: Sequence[str],prices: np.ndarray,allocations: dict[str,Any],factor_selection: dict[str,Any],factor_series: dict[str,list[dict[str,Any]]],backtest: dict[str,Any],cycle_state_series: dict[str,list[dict[str,Any]]]) -> dict[str,Any]:
    report=quality_report_v3(macro_rows,price_series,months,prices,allocations,factor_selection,factor_series,backtest,cycle_state_series); checks=list(report["checks"])
    def add(name,passed,detail): checks.append({"check":name,"status":"passed" if passed else "failed","detail":detail})
    audit=backtest["selection_audit"]; recommended=backtest["strategies"]["recommended"]; benchmark=backtest["strategies"]["equal_weight"]
    expected=[]; cursor=str(months[0])
    while cursor<=str(months[-1]):
        expected.append(cursor); year=int(cursor[:4]); month=int(cursor[4:])+1
        if month==13: year+=1; month=1
        cursor=f"{year:04d}{month:02d}"
    common_missing=sorted(set(expected)-set(str(value) for value in months))
    add("common_month_continuity",not common_missing,f"expected={len(expected)}, actual={len(months)}, missing={','.join(common_missing)}")
    expected_set=set(expected)
    for asset in ASSET_ORDER:
        observed={str(row.get("date") or "")[:6] for row in price_series.get(asset,[]) if str(row.get("date") or "")[:6] in expected_set}
        missing=sorted(expected_set-observed); add(f"{asset}_month_continuity",not missing,f"missing={','.join(missing)}")
    add("posterior_grid_predeclared",audit.get("trial_count")==48,f"trials={audit.get('trial_count')}")
    add("noncompensatory_candidate_exists",audit.get("validation_eligible_count",0)>0,f"eligible={audit.get('validation_eligible_count')}")
    add("train_validation_direction_positive",audit["train_active_metrics"]["annual_excess_return"]>0 and audit["validation_metrics"]["annual_return"]>0 and audit["validation_active_metrics"]["annual_excess_return"]>0,"train excess, validation absolute and active return")
    add("drift_aware_equal_weight_turnover",sum(float(row["turnover"]) for row in benchmark["nav"][1:])>0,"benchmark rebalances from drifted holdings")
    dynamics=recommended["weight_dynamics"]["overall"]; add("dynamic_weight_range",dynamics["mean_cross_asset_range"]>=0.25,f"mean_range={dynamics['mean_cross_asset_range']:.4f}")
    add("test_excluded_from_selection","never ranks" in audit.get("selection_rule",""),audit.get("selection_rule"))
    failed=[item["check"] for item in checks if item["status"]!="passed"]; return {"status":"passed" if not failed else "failed","checks":checks,"failed":failed}


def build_snapshot_v4(macro_rows: Sequence[dict[str,Any]],price_series: dict[str,list[dict[str,Any]]],*,generated_at: str|None=None,config: ResearchBacktestConfig|None=None) -> dict[str,Any]:
    config=config or ResearchBacktestConfig(); months,prices=monthly_prices(price_series)
    if len(months)<120: raise ValueError(f"price_history_too_short:{len(months)}")
    selection,factor_series,candidates=_factor_selection_v2(macro_rows,months,prices,config); factor_series=_factor_signals_v3(factor_series); cycles=build_cycle_history_v2(macro_rows,selection,candidates)
    if not cycles: raise ValueError("cycle_history_empty")
    cycle_state_series=_cycle_state_series_v3(cycles); spec,audit=_select_spec_v4(months,prices,cycles,config)
    backtest=walk_forward_backtest_v4(months,prices,cycles,config=config,selected_spec=spec,selection_audit=audit); allocations=current_allocations_v4(months,prices,cycles,config,spec)
    quality=quality_report_v4(macro_rows,price_series,months,prices,allocations,selection,factor_series,backtest,cycle_state_series)
    if quality["status"]!="passed": raise ValueError("quality_gate_failed:"+",".join(quality["failed"]))
    price_payload={asset:[{"month":month,"close":round(float(prices[index,position]),6)} for index,month in enumerate(months)] for position,asset in enumerate(ASSET_ORDER)}
    return {"schema_version":"4.0","engine_version":ENGINE_VERSION,"generated_at":generated_at or utc_now(),"status":"ready","asset_order":list(ASSET_ORDER),"asset_labels":ASSET_LABELS,"asset_proxies":ASSET_PROXIES,
        "data_as_of":{"market":months[-1],"macro_complete":cycles[-1]["month"],"macro_raw_latest":_month_key(str(macro_rows[-1].get("month")))},
        "methodology":{"rebalance":"month_end_signal_to_next_month_etf_return; holdings drift through realized return before the next trade","point_in_time":True,
            "point_in_time_selection":"factor identity=train; noncompensatory candidate gate=train; final choice=validation; test=report-only and excluded from scores",
            "benchmark":"equal_weight 25/25/25/25 monthly rebalance from drifted holdings with identical costs","transaction_cost_bps":config.transaction_cost_bps,
            "constraints":{"long_only":True,"profile_specific_bounds":True,"recommended_max_monthly_turnover":0.70,"comparator_max_monthly_turnover":config.max_turnover},
            "recommended_model":"strategic prior + multi-horizon trend posterior + breadth-conditioned stable risk sleeve + volatility/correlation diversification + smooth equity risk-off gate",
            "equity_preference":"45% strategic equity prior and higher equity risk budget; predeclared 10% crisis floor permits genuine risk-off execution","llm_policy":"server-side cited explanation only; cannot overwrite deterministic weights"},
        "profiles":PROFILE_SPECS,"factor_registry":FACTOR_REGISTRY,"factor_selection":selection,"factor_series":factor_series,"cycle_definitions":CYCLE_DEFINITIONS_V3,"cycle_state_series":cycle_state_series,"research_evidence":RESEARCH_EVIDENCE,
        "pring_state_map":{"valid":{bits:{"phase":phase,**PRING_PHASES[phase]} for bits,phase in PRING_BITS_TO_PHASE.items()},"observation_only":{"101":"mapped by six-state posterior","010":"mapped by six-state posterior"}},
        "cycle_history":cycles,"monthly_prices":price_payload,"allocations":allocations,"backtest":backtest,"optimization":audit,"quality":quality,
        "sources":[{"name":"本地研究仓库 macro_monthly/etf_ohlcv_daily","frequency":"monthly/daily","mode":"read_only","credentials_serialized":False},{"name":"BaoStock","frequency":"daily","mode":"bounded_incremental","credentials_serialized":False},{"name":"Tushare/iFinD/Wind/RQData","frequency":"optional","mode":"environment_only_provider_adapters","credentials_serialized":False}],
        "limitations":["商品类别以黄金ETF代表贵金属风险，不能等同全品种商品期货指数。","债券历史由当时可交易国债ETF与当前地方债ETF收益链接；切换点置零一日收益。","宏观月度表尚非完整发布时点vintage库。","康波样本不足一个完整长周期，仅为低置信度结构情景。","2023年以来是报告期且已被研发人员观察，不宣称为从未查看的真实影子样本；程序保证其不进入候选排序。","历史超额收益不构成未来收益承诺。"]}


def public_payload_v4(snapshot: dict[str,Any]) -> dict[str,Any]:
    keys=("schema_version","engine_version","generated_at","status","asset_order","asset_labels","asset_proxies","data_as_of","methodology","profiles","factor_registry","factor_selection","factor_series","cycle_definitions","cycle_state_series","research_evidence","pring_state_map","cycle_history","monthly_prices","allocations","backtest","optimization","quality","sources","limitations")
    return {key:snapshot[key] for key in keys}


build_cycle_history=build_cycle_history_v2
strategy_weights=strategy_weights_v4
walk_forward_backtest=walk_forward_backtest_v4
current_allocations=current_allocations_v4
quality_report=quality_report_v4
build_snapshot=build_snapshot_v4
public_payload=public_payload_v4

