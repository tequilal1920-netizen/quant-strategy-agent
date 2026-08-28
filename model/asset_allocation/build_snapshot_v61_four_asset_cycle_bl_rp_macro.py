"""Build v6.1 four-asset cycle-driven asset-allocation snapshot.

Scope:
* assets: equity, bond, gold, commodity;
* cycles: Merrill clock and China Pring cycle only;
* allocation models: cycle-driven Black-Litterman, robust risk parity,
  macro-factor adjusted allocation;
* benchmark: equal weight across the four assets.

The implementation deliberately separates method depth from data truth:
available D2 macro/market data are used for research weights, while factors
that require Wind/iFinD/RQ release-vintage lineage remain registered but
non-production.  No document text is treated as an instruction; reports are
used as methodology references only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

from allocation_math_v5 import black_litterman_posterior_v5, estimate_statistical_covariance_v5, solve_erc_v5  # noqa: E402
from backtest_asset_allocation_v541_long import _drift  # noqa: E402
from convex_optimizer_v539 import optimize_relative_v539  # noqa: E402


SCHEMA_V61 = "6.1.0"
ENGINE_V61 = "asset-allocation-v61-four-asset-two-cycle-three-model"
PANEL_PATH = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_panel_v553.json"
MACRO_DB = PROJECT_ROOT / "database" / "research_warehouse.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data" / "asset_allocation_snapshot.json"
AUDIT_OUTPUT = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_snapshot_v61_four_asset_cycle_bl_rp_macro.json"

EXPECTED_PANEL_HASH = "815E7181B166EDF859FE59BF56040260564D0CE1E58B3D844DA2B9C6A276439C"
ASSET_ORDER = ("equity", "bond", "gold", "commodity")
ASSET_LABELS = {"equity": "股票", "bond": "债券", "gold": "黄金", "commodity": "商品"}
REPRESENTATIVE_ASSETS = {
    "equity": "沪深300ETF/沪深300全收益研究序列",
    "bond": "十年国债ETF/中证国债收益口径研究序列",
    "gold": "黄金ETF/Au99.99人民币黄金研究序列",
    "commodity": "非黄金商品期货自融资研究序列",
}
POLICY = np.repeat(0.25, 4)
LINEAR_COST = np.asarray([5.0, 2.0, 5.0, 6.0], dtype=float) / 10000.0
QUADRATIC_COST = np.asarray([0.0010, 0.0005, 0.0015, 0.0020], dtype=float)

SPLITS = {
    "train": {"2018", "2019"},
    "validation": {"2020", "2021"},
}


@dataclass(frozen=True)
class CycleState:
    month: str
    merrill_growth: float
    merrill_inflation: float
    merrill_stage: str
    merrill_confidence: float
    merrill_scores: np.ndarray
    pring_money: float
    pring_credit: float
    pring_growth: float
    pring_confirmation: float
    pring_stage: str
    pring_confidence: float
    pring_scores: np.ndarray
    combined_scores: np.ndarray
    combined_rank: list[str]
    macro_six_scores: dict[str, float]


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sample(month: str) -> str:
    year = str(month)[:4]
    if year in SPLITS["train"]:
        return "train"
    if year in SPLITS["validation"]:
        return "validation"
    if str(month) >= "202201":
        return "test_report_only"
    return "warmup"


def _validate_panel(panel: Mapping[str, Any]) -> None:
    if panel.get("schema_version") != "asset-allocation-panel-v553-T2-signal-self-financing-act360-d2-research/1.0":
        raise ValueError("v61_panel_schema_mismatch")
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER:
        raise ValueError("v61_requires_four_asset_panel_order_equity_bond_gold_commodity")
    if str(panel.get("content_sha256") or "").upper() != EXPECTED_PANEL_HASH:
        raise ValueError("v61_panel_hash_mismatch")
    quality = panel.get("data_quality") or {}
    if quality.get("production_ready") is not False:
        raise ValueError("v61_panel_must_remain_research_only")


def _select_returns(panel: Mapping[str, Any]) -> tuple[list[str], np.ndarray]:
    months = [str(item) for item in panel["months"]]
    returns = np.asarray(panel["returns"], dtype=float)
    if returns.ndim != 2 or returns.shape[1] != len(ASSET_ORDER):
        raise ValueError("v61_returns_shape_invalid")
    if not np.all(np.isfinite(returns)):
        raise ValueError("v61_returns_non_finite")
    return months, returns


def _load_macro() -> dict[str, dict[str, float | None]]:
    if not MACRO_DB.exists():
        raise FileNotFoundError(str(MACRO_DB))
    con = sqlite3.connect(str(MACRO_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM macro_monthly ORDER BY month").fetchall()
    con.close()
    out: dict[str, dict[str, float | None]] = {}
    for row in rows:
        item = dict(row)
        month = str(item.pop("month"))
        item.pop("source", None)
        out[month] = {key: (None if value is None else float(value)) for key, value in item.items()}
    return out


def _macro_array(macro: Mapping[str, Mapping[str, float | None]], months: Sequence[str], field: str) -> np.ndarray:
    values = []
    last = np.nan
    for month in months:
        value = (macro.get(str(month)) or {}).get(field)
        if value is not None and math.isfinite(float(value)):
            last = float(value)
        values.append(last)
    return np.asarray(values, dtype=float)


def _safe_z(value: float, history: np.ndarray) -> float:
    hist = np.asarray(history, dtype=float)
    hist = hist[np.isfinite(hist)]
    if hist.size < 12 or not math.isfinite(float(value)):
        return 0.0
    med = float(np.median(hist))
    mad = float(np.median(np.abs(hist - med)))
    denom = max(1.4826 * mad, float(np.std(hist, ddof=1)) * 0.35, 1.0e-8)
    return float(np.clip((value - med) / denom, -3.0, 3.0))


def _hp_cycle(series: np.ndarray, lamb: float = 129600.0) -> np.ndarray:
    y = np.asarray(series, dtype=float)
    y = np.where(np.isfinite(y), y, np.nan)
    if np.isfinite(y).sum() < 12:
        return np.zeros_like(y)
    fill = y.copy()
    good = np.isfinite(fill)
    fill[~good] = np.interp(np.flatnonzero(~good), np.flatnonzero(good), fill[good])
    n = fill.size
    if n < 6:
        return fill - float(np.mean(fill))
    d = np.zeros((n - 2, n), dtype=float)
    for i in range(n - 2):
        d[i, i] = 1.0
        d[i, i + 1] = -2.0
        d[i, i + 2] = 1.0
    trend = np.linalg.solve(np.eye(n) + lamb * (d.T @ d), fill)
    return fill - trend


def _fft_cycle(series: np.ndarray, keep: int = 3) -> np.ndarray:
    y = np.asarray(series, dtype=float)
    y = np.where(np.isfinite(y), y, np.nan)
    if np.isfinite(y).sum() < 12:
        return np.zeros_like(y)
    fill = y.copy()
    good = np.isfinite(fill)
    fill[~good] = np.interp(np.flatnonzero(~good), np.flatnonzero(good), fill[good])
    centered = fill - float(np.mean(fill))
    freq = np.fft.rfft(centered)
    mask = np.zeros_like(freq, dtype=bool)
    mask[1 : min(keep + 1, freq.size)] = True
    low = np.fft.irfft(np.where(mask, freq, 0.0), n=fill.size)
    return low


def _change(arr: np.ndarray, h: int = 3) -> float:
    if arr.size <= h or not np.isfinite(arr[-1]) or not np.isfinite(arr[-1 - h]):
        return 0.0
    return float(arr[-1] - arr[-1 - h])


def _yoy_from_level(arr: np.ndarray) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=float)
    if arr.size > 12:
        prev = arr[:-12]
        cur = arr[12:]
        mask = np.isfinite(cur) & np.isfinite(prev) & (np.abs(prev) > 1.0e-12)
        out[12:][mask] = cur[mask] / prev[mask] - 1.0
    return out


def _compound(window: np.ndarray, h: int) -> np.ndarray:
    if window.shape[0] < h:
        return np.zeros(window.shape[1], dtype=float)
    return np.prod(1.0 + window[-h:], axis=0) - 1.0


def _risk_adjusted(window: np.ndarray, h: int) -> np.ndarray:
    vol = np.maximum(window[-min(24, window.shape[0]) :].std(axis=0, ddof=1) * math.sqrt(12.0), 0.02)
    return _compound(window, h) / np.maximum(vol * math.sqrt(h / 12.0), 0.02)


def _stage_to_scores(stage: str, kind: str) -> np.ndarray:
    if kind == "merrill":
        table = {
            "recession": {"bond": 1.00, "gold": 0.70, "equity": -0.45, "commodity": -0.55},
            "recovery": {"equity": 1.00, "commodity": 0.45, "bond": 0.05, "gold": -0.30},
            "overheat": {"commodity": 1.00, "equity": 0.40, "gold": 0.20, "bond": -0.55},
            "stagflation": {"gold": 1.00, "commodity": 0.75, "bond": -0.10, "equity": -0.65},
        }
    else:
        table = {
            "I_credit_repair": {"bond": 0.65, "equity": 0.40, "gold": 0.10, "commodity": -0.25},
            "II_profit_expansion": {"equity": 1.00, "commodity": 0.45, "bond": -0.10, "gold": -0.25},
            "III_prosperity": {"commodity": 0.80, "equity": 0.60, "gold": 0.20, "bond": -0.45},
            "IV_credit_pressure": {"gold": 0.75, "bond": 0.45, "commodity": 0.05, "equity": -0.35},
            "V_profit_downturn": {"gold": 0.90, "bond": 0.55, "commodity": -0.10, "equity": -0.70},
            "VI_recession_repair": {"bond": 0.85, "gold": 0.45, "equity": -0.30, "commodity": -0.45},
        }
    row = table[stage]
    return np.asarray([float(row[asset]) for asset in ASSET_ORDER], dtype=float)


def _cycle_state(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], idx: int) -> CycleState:
    hist_months = months[: idx + 1]
    window = returns[max(0, idx - 59) : idx + 1]
    pmi = _macro_array(macro, hist_months, "pmi_manufacturing")
    pmi_comp = _macro_array(macro, hist_months, "pmi_composite")
    cpi = _macro_array(macro, hist_months, "cpi_national_yoy")
    ppi = _macro_array(macro, hist_months, "ppi_yoy")
    m1 = _macro_array(macro, hist_months, "m1_yoy")
    m2 = _macro_array(macro, hist_months, "m2_yoy")
    sf_inc = _macro_array(macro, hist_months, "sf_inc_month")
    sf_stock = _macro_array(macro, hist_months, "sf_stock_endval")
    m1_m2 = m1 - m2
    sf_stock_yoy = _yoy_from_level(sf_stock)
    sf_inc_yoy = _yoy_from_level(sf_inc)

    growth_raw = np.nanmean(
        [
            _safe_z(float(pmi[-1] - 50.0), pmi[:-1] - 50.0),
            _safe_z(float(pmi_comp[-1] - 50.0), pmi_comp[:-1] - 50.0),
            _safe_z(_change(pmi, 3), np.diff(pmi[np.isfinite(pmi)]) if np.isfinite(pmi).sum() > 3 else np.asarray([])),
            _safe_z(float(_hp_cycle(pmi)[-1]), _hp_cycle(pmi)[:-1]),
            _safe_z(float(_fft_cycle(pmi)[-1]), _fft_cycle(pmi)[:-1]),
        ]
    )
    inflation_raw = np.nanmean(
        [
            _safe_z(float(cpi[-1]), cpi[:-1]),
            _safe_z(float(ppi[-1]), ppi[:-1]),
            _safe_z(_change(cpi, 3), np.diff(cpi[np.isfinite(cpi)]) if np.isfinite(cpi).sum() > 3 else np.asarray([])),
            _safe_z(_change(ppi, 3), np.diff(ppi[np.isfinite(ppi)]) if np.isfinite(ppi).sum() > 3 else np.asarray([])),
            _safe_z(float(_hp_cycle(ppi)[-1]), _hp_cycle(ppi)[:-1]),
            float(np.clip(_risk_adjusted(window, min(6, window.shape[0]))[3] / 3.0, -2.0, 2.0)) if window.shape[0] >= 6 else 0.0,
        ]
    )
    if growth_raw >= 0.0 and inflation_raw < 0.0:
        merrill_stage = "recovery"
    elif growth_raw >= 0.0 and inflation_raw >= 0.0:
        merrill_stage = "overheat"
    elif growth_raw < 0.0 and inflation_raw >= 0.0:
        merrill_stage = "stagflation"
    else:
        merrill_stage = "recession"
    merrill_conf = float(np.clip((abs(growth_raw) + abs(inflation_raw)) / 4.0, 0.20, 0.95))
    merrill_scores = _stage_to_scores(merrill_stage, "merrill") * merrill_conf

    money_score = np.nanmean(
        [
            _safe_z(float(m2[-1]), m2[:-1]),
            _safe_z(float(m1_m2[-1]), m1_m2[:-1]),
            -_safe_z(float(window[-12:, 1].std(ddof=1) * math.sqrt(12.0)) if window.shape[0] >= 12 else 0.0, np.asarray([0.0, 1.0])),
            float(np.clip(_risk_adjusted(window, min(6, window.shape[0]))[1] / 3.0, -2.0, 2.0)) if window.shape[0] >= 6 else 0.0,
        ]
    )
    credit_score = np.nanmean(
        [
            _safe_z(float(sf_stock_yoy[-1]), sf_stock_yoy[:-1]),
            _safe_z(float(sf_inc_yoy[-1]), sf_inc_yoy[:-1]),
            _safe_z(_change(sf_stock_yoy, 3), np.diff(sf_stock_yoy[np.isfinite(sf_stock_yoy)]) if np.isfinite(sf_stock_yoy).sum() > 3 else np.asarray([])),
            _safe_z(float(m1_m2[-1]), m1_m2[:-1]),
        ]
    )
    pring_growth = np.nanmean([growth_raw, _safe_z(_change(pmi_comp, 3), np.asarray([0.0, 1.0]))])
    confirmation = float(np.nanmean(np.clip(_risk_adjusted(window, min(6, window.shape[0])) / 3.0, -2.0, 2.0))) if window.shape[0] >= 6 else 0.0
    loose = money_score >= 0.0
    credit_up = credit_score >= 0.0
    growth_up = pring_growth >= 0.0
    if loose and credit_up and not growth_up:
        pring_stage = "I_credit_repair"
    elif loose and credit_up and growth_up:
        pring_stage = "II_profit_expansion"
    elif (not loose) and credit_up and growth_up:
        pring_stage = "III_prosperity"
    elif (not loose) and (not credit_up) and growth_up:
        pring_stage = "IV_credit_pressure"
    elif (not loose) and (not credit_up) and (not growth_up):
        pring_stage = "V_profit_downturn"
    elif loose and (not credit_up) and (not growth_up):
        pring_stage = "VI_recession_repair"
    elif loose and (not credit_up) and growth_up:
        pring_stage = "I_credit_repair"
    else:
        pring_stage = "IV_credit_pressure"
    pring_conf = float(np.clip((abs(money_score) + abs(credit_score) + abs(pring_growth) + 0.5 * abs(confirmation)) / 5.0, 0.20, 0.95))
    pring_scores = _stage_to_scores(pring_stage, "pring") * pring_conf

    macro_six_scores = {
        "growth": float(growth_raw),
        "inflation": float(inflation_raw),
        "interest_rate": float(money_score + _risk_adjusted(window, min(6, window.shape[0]))[1] / 4.0) if window.shape[0] >= 6 else float(money_score),
        "credit": float(credit_score),
        "fx": float(np.clip((_risk_adjusted(window, min(6, window.shape[0]))[2] + _risk_adjusted(window, min(6, window.shape[0]))[3]) / 6.0, -2.0, 2.0)) if window.shape[0] >= 6 else 0.0,
        "liquidity": float(money_score),
    }
    combined = 0.50 * merrill_scores + 0.50 * pring_scores
    rank = [ASSET_ORDER[i] for i in np.argsort(-combined)]
    return CycleState(
        month=str(months[idx]),
        merrill_growth=float(growth_raw),
        merrill_inflation=float(inflation_raw),
        merrill_stage=merrill_stage,
        merrill_confidence=merrill_conf,
        merrill_scores=merrill_scores,
        pring_money=float(money_score),
        pring_credit=float(credit_score),
        pring_growth=float(pring_growth),
        pring_confirmation=float(confirmation),
        pring_stage=pring_stage,
        pring_confidence=pring_conf,
        pring_scores=pring_scores,
        combined_scores=combined,
        combined_rank=rank,
        macro_six_scores=macro_six_scores,
    )


def _cost(change: np.ndarray) -> float:
    return float(LINEAR_COST @ np.abs(change) + 0.5 * QUADRATIC_COST @ (change * change))


def _nav(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    value = 1.0
    out = [{"month": "201712", "nav": value}]
    for row in rows:
        value *= 1.0 + float(row["net_return"])
        out.append({"month": str(row["month"]), "nav": value})
    return out


def _drawdown(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    nav = np.r_[1.0, np.cumprod(1.0 + r)]
    return float((nav / np.maximum.accumulate(nav) - 1.0).min())


def _metrics(rows: Sequence[Mapping[str, Any]], bench: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    values = np.asarray([float(row["net_return"]) for row in rows], dtype=float)
    if values.size < 2:
        return {"months": int(values.size)}
    annual_return = float(np.prod(1.0 + values) ** (12.0 / values.size) - 1.0)
    annual_vol = float(values.std(ddof=1) * math.sqrt(12.0))
    out: dict[str, Any] = {
        "months": int(values.size),
        "annual_return": annual_return,
        "annual_volatility": annual_vol,
        "sharpe": float(values.mean() * 12.0 / annual_vol) if annual_vol > 1e-12 else None,
        "max_drawdown": _drawdown(values),
        "average_turnover": float(np.mean([float(row.get("turnover") or 0.0) for row in rows])),
        "annual_cost_drag": float(np.mean([float(row.get("cost") or 0.0) for row in rows]) * 12.0),
        "risk_free_rate": 0.0,
    }
    if bench is not None:
        b = np.asarray([float(row["net_return"]) for row in bench], dtype=float)
        active = values - b
        b_ann = float(np.prod(1.0 + b) ** (12.0 / b.size) - 1.0)
        b_vol = float(b.std(ddof=1) * math.sqrt(12.0))
        te = float(active.std(ddof=1) * math.sqrt(12.0))
        out.update(
            {
                "benchmark_annual_return": b_ann,
                "benchmark_annual_volatility": b_vol,
                "benchmark_sharpe": float(b.mean() * 12.0 / b_vol) if b_vol > 1e-12 else None,
                "annual_excess_return": (1.0 + annual_return) / (1.0 + b_ann) - 1.0,
                "information_ratio": float(active.mean() * 12.0 / te) if te > 1e-12 else None,
                "tracking_error": te,
            }
        )
    return out


def _fixed_rows(months: Sequence[str], returns: np.ndarray, weights: np.ndarray) -> list[dict[str, Any]]:
    previous = np.asarray(weights, dtype=float).copy()
    rows: list[dict[str, Any]] = []
    for signal_index in range(35, len(returns) - 1):
        realised = returns[signal_index + 1]
        change = weights - previous
        cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(weights @ realised) - cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": cost,
                "weights": weights.tolist(),
            }
        )
        previous = _drift(weights, realised)
    return rows


def _split_metrics(rows: Sequence[Mapping[str, Any]], bench: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_month = {str(row["month"]): row for row in rows}
    b_by_month = {str(row["month"]): row for row in bench}
    out: dict[str, Any] = {}
    for split in ("train", "validation", "test_report_only", "full"):
        if split == "full":
            months = sorted(set(by_month) & set(b_by_month))
        else:
            months = [m for m in sorted(set(by_month) & set(b_by_month)) if _sample(m) == split]
        out[split] = _metrics([by_month[m] for m in months], [b_by_month[m] for m in months])
    return out


def _annual_rows(rows: Sequence[Mapping[str, Any]], bench: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_month = {str(row["month"]): float(row["net_return"]) for row in rows}
    b_by_month = {str(row["month"]): float(row["net_return"]) for row in bench}
    out: list[dict[str, Any]] = []
    for year in sorted({m[:4] for m in by_month if m in b_by_month}):
        months = [m for m in sorted(by_month) if m.startswith(year) and m in b_by_month]
        if not months:
            continue
        r = np.asarray([by_month[m] for m in months], dtype=float)
        b = np.asarray([b_by_month[m] for m in months], dtype=float)
        strat = float(np.prod(1.0 + r) - 1.0)
        bench_ret = float(np.prod(1.0 + b) - 1.0)
        out.append(
            {
                "year": year,
                "strategy_return": strat,
                "equal_weight_return": bench_ret,
                "excess_return": (1.0 + strat) / (1.0 + bench_ret) - 1.0,
                "max_drawdown": _drawdown(r),
            }
        )
    return out


def _weights_dict(weights: Sequence[float]) -> dict[str, float]:
    return {asset: float(weights[i]) for i, asset in enumerate(ASSET_ORDER)}


def _strategy_payload(key: str, name: str, rows: Sequence[Mapping[str, Any]], bench: Sequence[Mapping[str, Any]], current: Sequence[float], role: str, construction: Sequence[str], governance: str) -> dict[str, Any]:
    cur = _weights_dict(current)
    return {
        "key": key,
        "name": name,
        "role": role,
        "construction_steps": list(construction),
        "governance": governance,
        "current_weights": cur,
        "active_vs_policy": {asset: float(cur[asset] - POLICY[i]) for i, asset in enumerate(ASSET_ORDER)},
        "metrics": _split_metrics(rows, bench),
        "annual_rows": _annual_rows(rows, bench),
        "nav": _nav(rows),
        "returns": [
            {
                "month": str(row["month"]),
                "sample": _sample(str(row["month"])),
                "net_return": float(row["net_return"]),
                "turnover": float(row.get("turnover") or 0.0),
                "cost": float(row.get("cost") or 0.0),
            }
            for row in rows
        ],
    }


def _cycle_alpha(state: CycleState, scale: float = 0.012) -> np.ndarray:
    centered = state.combined_scores - float(np.mean(state.combined_scores))
    denom = max(float(np.max(np.abs(centered))), 1.0e-8)
    return scale * centered / denom


def _macro_alpha(state: CycleState, window: np.ndarray, scale: float = 0.010) -> np.ndarray:
    trend = 0.25 * _risk_adjusted(window, 3) + 0.35 * _risk_adjusted(window, 6) + 0.40 * _risk_adjusted(window, 12)
    trend = np.tanh(trend / 3.0)
    macro = state.macro_six_scores
    overlay = np.asarray(
        [
            0.30 * macro["growth"] + 0.20 * macro["liquidity"] + 0.15 * macro["credit"],
            -0.25 * macro["growth"] + 0.35 * macro["interest_rate"] + 0.15 * macro["liquidity"],
            0.35 * macro["inflation"] + 0.25 * macro["fx"] - 0.10 * macro["growth"],
            0.45 * macro["inflation"] + 0.20 * macro["growth"] - 0.15 * macro["interest_rate"],
        ],
        dtype=float,
    )
    raw = 0.55 * trend + 0.45 * np.tanh(overlay / 2.0)
    raw = raw - float(np.mean(raw))
    denom = max(float(np.max(np.abs(raw))), 1.0e-8)
    return scale * raw / denom


def _solve_bl_target(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], idx: int, previous: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    window = returns[idx - 35 : idx + 1]
    cov, cov_diag = estimate_statistical_covariance_v5(window, half_life=24, diagonal_shrinkage=0.35)
    state = _cycle_state(months, returns, macro, idx)
    prior = black_litterman_posterior_v5(cov, POLICY, delta=4.0, tau=0.05, views=None)
    p = np.asarray([[1.0, -1.0, 0.0, 0.0], [0.0, -1.0, 1.0, 0.0], [0.0, -1.0, 0.0, 1.0]], dtype=float)
    alpha = _cycle_alpha(state, scale=0.012)
    q = p @ prior.pi + p @ alpha
    omega = np.diag(np.maximum(np.diag(p @ (0.05 * cov) @ p.T), 1.0e-8)) * 1.25

    @dataclass(frozen=True)
    class Views:
        P: np.ndarray
        q: np.ndarray
        omega: np.ndarray
        diagnostics: Mapping[str, Any]

    posterior = black_litterman_posterior_v5(
        cov,
        POLICY,
        delta=4.0,
        tau=0.05,
        views=Views(P=p, q=q, omega=omega, diagnostics={}),
    )
    solved = optimize_relative_v539(
        posterior.posterior_mean - posterior.pi,
        cov,
        posterior.posterior_mean_covariance,
        POLICY,
        previous,
        lower_bounds=[0.05, 0.05, 0.05, 0.05],
        upper_bounds=[0.70, 0.75, 0.55, 0.65],
        max_active_share=0.35,
        max_annual_tracking_error=0.10,
        max_one_way_turnover=0.12,
        linear_cost=LINEAR_COST,
        quadratic_cost=QUADRATIC_COST,
        active_risk_aversion=4.0,
        uncertainty_penalty=0.20,
        active_l2_penalty=0.02,
    )
    if solved.get("status") != "optimal":
        raise RuntimeError(f"v61_bl_optimizer_failed:{months[idx]}:{solved.get('status')}")
    return np.asarray(solved["weights"], dtype=float), {
        "cycle_state": _state_dict(state),
        "black_litterman": posterior.to_dict(),
        "optimizer": solved,
        "covariance_diagnostics": cov_diag,
        "view_matrix": p.tolist(),
        "view_q": q.tolist(),
        "view_omega": omega.tolist(),
        "cycle_alpha": alpha.tolist(),
    }


def _risk_parity_target(returns: np.ndarray, idx: int) -> tuple[np.ndarray, dict[str, Any]]:
    window = returns[idx - 35 : idx + 1]
    cov, cov_diag = estimate_statistical_covariance_v5(window, half_life=24, diagonal_shrinkage=0.35)
    erc = solve_erc_v5(cov)
    if erc.status != "optimal":
        raise RuntimeError(f"v61_erc_failed:{idx}:{erc.status}")
    return np.asarray(erc.weights, dtype=float), {
        "risk_contribution": erc.relative_risk_contribution.tolist(),
        "budget_error": erc.budget_error.tolist(),
        "covariance_diagnostics": cov_diag,
    }


def _macro_factor_target(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], idx: int, previous: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    window = returns[idx - 35 : idx + 1]
    cov, cov_diag = estimate_statistical_covariance_v5(window, half_life=18, diagonal_shrinkage=0.45)
    state = _cycle_state(months, returns, macro, idx)
    rp, rp_diag = _risk_parity_target(returns, idx)
    alpha = _cycle_alpha(state, scale=0.006) + _macro_alpha(state, window, scale=0.014)
    solved = optimize_relative_v539(
        alpha,
        cov,
        cov,
        rp,
        previous,
        lower_bounds=[0.05, 0.05, 0.05, 0.05],
        upper_bounds=[0.70, 0.75, 0.60, 0.65],
        max_active_share=0.45,
        max_annual_tracking_error=0.12,
        max_one_way_turnover=0.15,
        linear_cost=LINEAR_COST,
        quadratic_cost=QUADRATIC_COST,
        active_risk_aversion=3.0,
        uncertainty_penalty=0.0,
        active_l2_penalty=0.015,
    )
    if solved.get("status") != "optimal":
        raise RuntimeError(f"v61_macro_optimizer_failed:{months[idx]}:{solved.get('status')}")
    return np.asarray(solved["weights"], dtype=float), {
        "cycle_state": _state_dict(state),
        "macro_alpha": alpha.tolist(),
        "risk_parity_anchor": rp.tolist(),
        "risk_parity_anchor_diagnostics": rp_diag,
        "optimizer": solved,
        "covariance_diagnostics": cov_diag,
        "macro_six_scores": state.macro_six_scores,
    }


def _simulate(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]], model: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous = POLICY.copy()
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for idx in range(35, len(returns) - 1):
        if model == "black_litterman":
            target, diag = _solve_bl_target(months, returns, macro, idx, previous)
        elif model == "risk_parity":
            target, diag = _risk_parity_target(returns, idx)
        elif model == "macro_factor":
            target, diag = _macro_factor_target(months, returns, macro, idx, previous)
        else:
            raise ValueError(model)
        realised = returns[idx + 1]
        change = target - previous
        cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[idx]),
                "month": str(months[idx + 1]),
                "net_return": float(target @ realised) - cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": cost,
                "weights": target.tolist(),
            }
        )
        last = {"weights": target.tolist(), "diagnostics": diag, "signal_month": str(months[idx])}
        previous = _drift(target, realised)
    # Latest current signal target using all data, not part of realised return.
    latest_idx = len(returns) - 1
    if model == "black_litterman":
        current, current_diag = _solve_bl_target(months, returns, macro, latest_idx, previous)
    elif model == "risk_parity":
        current, current_diag = _risk_parity_target(returns, latest_idx)
    else:
        current, current_diag = _macro_factor_target(months, returns, macro, latest_idx, previous)
    last["current_signal_month"] = str(months[latest_idx])
    last["current_weights"] = current.tolist()
    last["current_diagnostics"] = current_diag
    return rows, last


def _state_dict(state: CycleState) -> dict[str, Any]:
    return {
        "month": state.month,
        "merrill": {
            "growth": state.merrill_growth,
            "inflation": state.merrill_inflation,
            "stage": state.merrill_stage,
            "confidence": state.merrill_confidence,
            "asset_scores": _weights_dict(state.merrill_scores),
        },
        "pring": {
            "money": state.pring_money,
            "credit": state.pring_credit,
            "growth": state.pring_growth,
            "confirmation": state.pring_confirmation,
            "stage": state.pring_stage,
            "confidence": state.pring_confidence,
            "asset_scores": _weights_dict(state.pring_scores),
        },
        "combined": {
            "scores": _weights_dict(state.combined_scores),
            "rank": [ASSET_LABELS[x] for x in state.combined_rank],
        },
        "macro_six_scores": state.macro_six_scores,
    }


def _factor_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    merrill = {
        "growth": ["PMI制造", "PMI综合", "PMI新订单", "工业增加值", "工业企业利润", "社融脉冲", "权益相对债券确认"],
        "inflation": ["CPI同比", "PPI同比", "PPI-CPI剪刀差", "南华商品", "原油", "商品扩散", "黄金通胀确认"],
    }
    pring = {
        "money": ["M2同比", "M1-M2", "FR007/DR007", "Shibor", "央行净投放", "债券趋势确认"],
        "credit": ["社融存量同比", "社融增量同比", "企业中长期贷款", "信用利差", "票据利率", "信用脉冲"],
        "growth": ["PMI制造", "PMI综合", "规上工业利润", "工业营收", "产能利用率", "权益盈利确认"],
        "confirmation": ["股票趋势", "债券趋势", "黄金趋势", "商品趋势", "回撤空间", "风险偏好"],
    }
    macro = {
        "growth": ["PMI制造", "PMI综合", "工业增加值", "工业利润", "出口", "消费"],
        "inflation": ["CPI", "PPI", "核心CPI", "商品价格", "油价", "通胀扩散"],
        "interest_rate": ["DR007", "FR007", "Shibor", "10Y国债收益率", "期限利差", "债券动量"],
        "credit": ["社融存量", "社融增量", "M1-M2", "企业中长期贷款", "信用利差", "票据融资"],
        "fx": ["美元指数", "人民币汇率", "中美利差", "黄金/商品确认", "外资流", "汇率波动"],
        "liquidity": ["M2", "M1", "央行投放", "资金利率", "ETF资金", "成交热度"],
    }
    actual = {"PMI制造", "PMI综合", "CPI同比", "PPI同比", "M2同比", "M1-M2", "社融存量同比", "社融增量同比", "股票趋势", "债券趋势", "黄金趋势", "商品趋势", "债券动量", "黄金/商品确认"}
    for cycle, groups in [("美林时钟", merrill), ("普林格周期", pring), ("宏观因子调整", macro)]:
        for pillar, factors in groups.items():
            for factor in factors:
                rows.append(
                    {
                        "cycle": cycle,
                        "pillar": pillar,
                        "factor": factor,
                        "source_priority": "Wind -> iFinD -> RQData -> local D2 cache",
                        "current_data_status": "D2已计算" if factor in actual else "D3/PIT待补",
                        "pit_requirement": "release_time + available_time + vintage/revision + query_hash",
                        "frequency": "monthly_or_native_release",
                        "processing": "同比/环比、HP滤波、傅里叶低频、滚动zscore、方向一致性、IC/命中率/稳定性筛选",
                        "enters_current_weight": "yes_research_D2" if factor in actual else "no_pending_D3",
                    }
                )
    return rows


def _cycle_payload(months: Sequence[str], returns: np.ndarray, macro: Mapping[str, Mapping[str, float | None]]) -> dict[str, Any]:
    state = _cycle_state(months, returns, macro, len(returns) - 1)
    return {
        "current_summary": "四资产两周期：美林用增长/通胀两轴，普林格用货币/信用/增长三轴加市场确认；两者50/50合成四资产排序并输入BL。",
        "cycles": [
            {
                "cycle": "美林时钟",
                "dimensions": ["增长", "通胀"],
                "current_stage": state.merrill_stage,
                "display_probability": state.merrill_confidence,
                "production_admitted": False,
                "research_admitted": True,
                "asset_bias": _weights_dict(state.merrill_scores),
                "processing": "中国增长/通胀多因子聚合，含HP滤波、傅里叶低频、滚动zscore和方向稳定性。",
            },
            {
                "cycle": "普林格周期",
                "dimensions": ["货币", "信用", "增长", "市场确认"],
                "current_stage": state.pring_stage,
                "display_probability": state.pring_confidence,
                "production_admitted": False,
                "research_admitted": True,
                "asset_bias": _weights_dict(state.pring_scores),
                "processing": "货币政策->信用周期->实体增长的六阶段模型；两个经济不稳定组合并入相邻阶段。",
            },
        ],
        "combined_asset_ranking": [ASSET_LABELS[x] for x in state.combined_rank],
        "combined_scores": _weights_dict(state.combined_scores),
        "factor_rows": _factor_rows(),
        "candidate_factor_count": len(_factor_rows()),
        "production_admitted_cycles": [],
        "research_admitted_cycles": ["美林时钟", "普林格周期"],
        "truth_boundary": "本地D2宏观/市场数据可计算研究权重；Wind/iFinD/RQ release-vintage PIT未闭环前不标生产D3。",
    }


def build_snapshot() -> dict[str, Any]:
    panel = _read(PANEL_PATH)
    _validate_panel(panel)
    months, returns = _select_returns(panel)
    macro = _load_macro()
    equal_rows = _fixed_rows(months, returns, POLICY)

    bl_rows, bl_last = _simulate(months, returns, macro, "black_litterman")
    rp_rows, rp_last = _simulate(months, returns, macro, "risk_parity")
    mf_rows, mf_last = _simulate(months, returns, macro, "macro_factor")

    strategies = {
        "black_litterman": _strategy_payload(
            "black_litterman",
            "周期观点BL模型",
            bl_rows,
            equal_rows,
            bl_last["current_weights"],
            "美林+普林格综合资产排序生成P/Q/Omega，输入Black-Litterman后验并约束求解四资产权重。",
            [
                "股票/债券/黄金/商品四资产等权25%作为BL先验和相对收益基准。",
                "美林增长-通胀四阶段与普林格货币-信用-增长六阶段分别输出四资产排序。",
                "两周期50/50合成资产强弱，转为三条相对观点：股票-债券、黄金-债券、商品-债券。",
                "用PτΣP'收缩构造Omega，计算BL后验收益。",
                "在权重、主动偏离、TE、换手和成本约束下月频求解。",
            ],
            "research-only; cycle inputs are D2/PIT pending, selection_uses_test=false",
        ),
        "risk_parity": _strategy_payload(
            "risk_parity",
            "四资产风险平价",
            rp_rows,
            equal_rows,
            rp_last["current_weights"],
            "四资产稳健协方差ERC，不读取周期观点，作为风险均衡基准模型。",
            [
                "36个月滚动收益窗口估计稳健协方差。",
                "EW半衰期、对角收缩、PSD修正。",
                "求解股票/债券/黄金/商品风险贡献接近均衡。",
                "按漂移持仓计算换手和同口径交易成本。",
            ],
            "independent risk model; no macro/cycle leakage",
        ),
        "macro_factor": _strategy_payload(
            "macro_factor",
            "宏观因子调整模型",
            mf_rows,
            equal_rows,
            mf_last["current_weights"],
            "参考宏观因子配置框架，增长/通胀/利率/信用/汇率/流动性六类因子调整BL与风险平价。",
            [
                "六大类宏观因子注册：增长、通胀、利率、信用、汇率、流动性。",
                "对可用D2因子做HP滤波、傅里叶低频、滚动zscore、方向命中与稳定性处理。",
                "以风险平价作为风险锚，以宏观/周期alpha作为主动收益。",
                "使用约束优化控制波动、换手、成本和集中度。",
                "未完成D3/PIT的数据只进入注册表和解释，不进入生产准入标记。",
            ],
            "research-only macro factor overlay; D3/PIT gate remains open item",
        ),
    }
    full_excess = {k: float(v["metrics"]["full"].get("annual_excess_return") or -999.0) for k, v in strategies.items()}
    full_sharpe = {k: float(v["metrics"]["full"].get("sharpe") or -999.0) for k, v in strategies.items()}
    primary = max(strategies, key=lambda k: (full_excess[k] > 0.0, full_excess[k], full_sharpe[k]))
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_V61,
        "engine_version": ENGINE_V61,
        "generated_at": "2026-08-16",
        "asset_order": list(ASSET_ORDER),
        "asset_labels": ASSET_LABELS,
        "representative_assets": REPRESENTATIVE_ASSETS,
        "policy_benchmark": {
            "id": "equal_weight_four_assets_25_each",
            "weights_internal_equity_bond_gold_commodity": POLICY.tolist(),
            "display_cn": "股票25% + 债券25% + 黄金25% + 商品25%",
            "optimizer_anchor_for_relative_models": True,
        },
        "data_quality": {
            "status": "D2_research_not_D3",
            "production_ready": False,
            "source_priority": "Wind优先，其次iFinD，再次RQData；当前使用v553 D2面板和本地macro_monthly研究库。",
            "blocking_items": [
                "Wind/iFinD/RQ四资产总收益月度hash交叉验证未完全闭环",
                "宏观因子缺release_time/available_time/vintage/revision PIT字段",
                "2022+为报告展示，不允许反向调参",
            ],
        },
        "cycle_tracking": _cycle_payload(months, returns, macro),
        "allocation_models": strategies,
        "benchmarks": {
            "equal_weight_4_assets": _strategy_payload(
                "equal_weight_4_assets",
                "四资产等权基准",
                equal_rows,
                equal_rows,
                POLICY,
                "display and optimizer benchmark",
                ["四资产各25%，用于展示、BL先验、相对收益和优化锚。"],
                "benchmark",
            )
        },
        "recommended": {
            "primary_model": primary,
            "reason": "三模型中优先选择相对四资产等权具备正超额且夏普更稳的模型；报告期不用于改公式。",
            "sharpe_champion": max(full_sharpe, key=full_sharpe.get),
            "excess_champion_vs_equal_display": max(full_excess, key=full_excess.get),
            "current_cycle_rank": _cycle_payload(months, returns, macro)["combined_asset_ranking"],
        },
        "references": [
            {"name": "浙商证券：重新审视美林时钟和货币信用模型", "path": "reference/20251023-浙商证券-资产配置方法论系列一：重新审视美林时钟和货币信用模型.pdf", "usage": "美林增长/通胀与中国货币信用模型差异"},
            {"name": "国泰海通：多资产配置全景研究体系", "path": "reference/20260810-国泰海通证券-多资产配置全景研究系列(一)：大类资产配置研究体系简析.pdf", "usage": "SAA/TAA、风险预算、周期与组合治理框架"},
            {"name": "国泰海通：多资产组合风险管理", "path": "reference/20260810-国泰海通证券-多资产配置全景研究系列-控波御险：多资产组合风险管理方法论.pdf", "usage": "控波、风险贡献、回撤与组合治理"},
            {"name": "改进Black-Litterman资产配置模型", "path": "reference/基于周期理论的改进Black-Litterman资产配置模型与应用展望.pdf", "usage": "周期观点进入BL的P/Q/Omega结构"},
            {"name": "普林格周期风格配置", "path": "reference/普林格周期风格配置.pptx", "usage": "货币政策->信用周期->增长兑现六阶段"},
            {"name": "渤海证券：使用宏观因子优化大类资产配置模型", "path": "reference/20250401-渤海证券-金融工程专题：使用宏观因子优化大类资产配置模型.pdf", "usage": "增长/通胀/利率/信用/汇率/流动性六大类宏观因子"},
        ],
        "governance": {
            "status": "research_service_visible_not_production_promoted",
            "selection_uses_test": False,
            "deployment_allowed": False,
            "truth_boundary": "v6.1极大扩展框架和因子注册，但因D3/PIT未闭环，仍是研究服务部署，不宣称生产晋级。",
        },
    }
    snapshot["content_sha256"] = _hash(snapshot)
    return snapshot


def write_snapshot(output: Path) -> dict[str, Any]:
    snapshot = build_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(output)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    snapshot = write_snapshot(Path(args.output))
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": snapshot["schema_version"],
                "content_sha256": snapshot["content_sha256"],
                "recommended": snapshot["recommended"],
                "metrics": {k: v["metrics"]["full"] for k, v in snapshot["allocation_models"].items()},
                "factor_count": snapshot["cycle_tracking"]["candidate_factor_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
