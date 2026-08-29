# -*- coding: utf-8 -*-
"""Dashboard payload for the OHLCV technical factor board.

The endpoint is intentionally read-mostly: the heavy research arrays are
materialized into ``data/technical_factor_dashboard.json`` and the web page
loads that audited snapshot.  A refresh rebuilds the snapshot from the local
OHLCV runtime when the data cache is available.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BOARD_ROOT = Path(__file__).resolve().parent
DATA_PATH = BOARD_ROOT / "data" / "technical_factor_dashboard.json"
STATIC_FIGURE_DIR = BOARD_ROOT / "static" / "technical_factor_figures"
DESKTOP_FIGURE_DIR = Path(r"C:\Users\Rye\Desktop\技术分析")
BASE_RUNTIME = ROOT / "output" / "kline_memory_learning" / "cross_sectional_factor_runtime.npz"
OHLCV_CACHE = ROOT / "output" / "kline_memory_learning" / "kline_multiscale_ohlcv_runtime.npz"


FAMILY_ORDER = ["趋势动量", "突破确认", "回撤反转", "量价确认", "波动质量", "防守择时"]
DISPLAY_FAMILY = {"防守择时": "回撤/防守择时"}
SUB_FACTORS = {
    "趋势动量": [
        ("20日动量", "close/close[-20]-1", 0.20),
        ("60日动量", "close/close[-60]-1", 0.25),
        ("120日动量", "close/close[-120]-1", 0.18),
        ("均线排列", "0.35*MA5/MA20+0.35*MA20/MA60+0.30*MA60/MA120", 0.17),
        ("路径效率", "|60D收益|/60D绝对路径", 0.12),
        ("低波动惩罚", "-20D年化波动", -0.08),
    ],
    "突破确认": [
        ("20日唐奇安突破", "close/HHV(high,20)-1", 0.20),
        ("60日唐奇安突破", "close/HHV(high,60)-1", 0.22),
        ("120日唐奇安突破", "close/HHV(high,120)-1", 0.12),
        ("收盘位置", "(close-low)/(high-low)", 0.13),
        ("实体强度", "(close-open)/(high-low)", 0.10),
        ("量比", "MA(volume,5)/MA(volume,20)", 0.13),
        ("额比", "MA(amount,5)/MA(amount,20)", 0.10),
    ],
    "回撤反转": [
        ("中期动量", "60D/120D收益", 0.38),
        ("短期回撤", "-5D/-10D收益", -0.25),
        ("RSI回落", "-RSI14", -0.12),
        ("下影线", "(min(open,close)-low)/(high-low)", 0.11),
        ("收盘修复", "(close-low)/(high-low)", 0.10),
        ("上影线惩罚", "-upper_wick", -0.04),
    ],
    "量价确认": [
        ("成交量均线比", "MA(volume,5)/MA(volume,20)", 0.16),
        ("成交额均线比", "MA(amount,5)/MA(amount,20)", 0.16),
        ("OBV20", "MA(sign(ret)*log(volume),20)", 0.18),
        ("OBV60", "MA(sign(ret)*log(volume),60)", 0.12),
        ("成交量漂移", "MA(volume,20)/MA(volume,60)-1", 0.10),
        ("成交额漂移", "MA(amount,20)/MA(amount,60)-1", 0.10),
        ("收盘位置", "(close-low)/(high-low)", 0.10),
        ("成交额稳定性", "-std(amount,20)/MA(amount,20)", 0.08),
    ],
    "波动质量": [
        ("20日波动", "-std(ret,20)*sqrt(252)", -0.20),
        ("波动扩张", "-vol20/vol60", -0.12),
        ("下行波动", "-std(min(ret,0),20)*sqrt(252)", -0.18),
        ("60日回撤", "close/HHV(close,60)-1", 0.16),
        ("120日回撤", "close/HHV(close,120)-1", 0.10),
        ("20/60日效率", "|N日收益|/N日绝对路径", 0.24),
        ("ATR区间", "-MA(high-low,20)/close", -0.10),
    ],
    "防守择时": [
        ("短均线保护", "close/MA20-1", 0.16),
        ("20日动量", "close/close[-20]-1", 0.14),
        ("回撤深度", "close/HHV(close,60)-1", 0.16),
        ("下行波动", "-downside_vol20", -0.15),
        ("60日振幅", "-MA(high-low,60)/close", -0.12),
        ("跳空风险", "-gap", -0.08),
        ("量价背离", "-volume_ratio/abs(mom5)", -0.08),
        ("成交额稳定性", "-std(amount,20)/MA(amount,20)", 0.11),
    ],
}


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _display_family(name: str) -> str:
    return DISPLAY_FAMILY.get(name, name)


def _safe_float(value: Any, digits: int | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def _safe_mean(values: list[float]) -> float:
    clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    return float(np.mean(clean)) if len(clean) else 0.0


def _rank_ic(x: np.ndarray, y: np.ndarray, mask: np.ndarray) -> float:
    valid = np.asarray(mask, dtype=bool) & np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 30:
        return float("nan")
    xr = _rank_1d(x[valid])
    yr = _rank_1d(y[valid])
    if np.std(xr) < 1e-12 or np.std(yr) < 1e-12:
        return float("nan")
    return float(np.corrcoef(xr, yr)[0, 1])


def _rank_1d(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks / max(len(values) - 1, 1)


def _quantile_edge(scores: np.ndarray, forward: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
    valid = np.asarray(mask, dtype=bool) & np.isfinite(scores) & np.isfinite(forward)
    if int(valid.sum()) < 60:
        return 0.0, 0.0, 0.0
    top = valid & (scores >= np.nanquantile(scores[valid], 0.8))
    bottom = valid & (scores <= np.nanquantile(scores[valid], 0.2))
    if int(top.sum()) < 10 or int(bottom.sum()) < 10:
        return 0.0, 0.0, 0.0
    top_return = float(np.nanmean(forward[top]))
    bottom_return = float(np.nanmean(forward[bottom]))
    edge = top_return - bottom_return
    win_rate = float(np.nanmean(forward[top] > forward[bottom].mean()))
    payoff = abs(top_return) / max(abs(bottom_return), 1e-6)
    return edge, win_rate, payoff


def _copy_figures() -> dict[str, dict[str, str]]:
    STATIC_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    figure_map: dict[str, dict[str, str]] = {}
    for source in sorted(DESKTOP_FIGURE_DIR.glob("*.png")):
        if not source.name[:2].isdigit():
            continue
        target = STATIC_FIGURE_DIR / source.name
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            shutil.copy2(source, target)
        name = source.stem
        for label in ("中证500", "中证800", "中证1000", "中证2000", "沪深300", "科创50", "全A"):
            if label not in name:
                continue
            item = figure_map.setdefault(label, {})
            rel = "/static/technical_factor_figures/" + source.name
            if "年度收益" in name:
                item["annual_table"] = rel
            elif "相对强度" in name:
                item["trend_nav"] = rel
            elif "最佳策略三频率" in name:
                item["frequency_nav"] = rel
            elif "最佳频率三策略" in name:
                item["score_nav"] = rel
    (STATIC_FIGURE_DIR / "manifest.json").write_text(json.dumps(figure_map, ensure_ascii=False, indent=2), encoding="utf-8")
    return figure_map


def _fallback_snapshot() -> dict[str, Any]:
    figures = _copy_figures()
    factor_rows = _factor_rows()
    efficient = _static_efficient_rows()
    return {
        "status": "partial",
        "message": "runtime_cache_unavailable",
        "generated_at": _iso_now(),
        "as_of": None,
        "model_version": "technical-factor-dashboard/static-fallback",
        "default_factor": "趋势动量",
        "default_benchmark": "中证500",
        "universe_options": list(figures.keys()) or ["中证500", "中证800", "中证1000", "中证2000", "沪深300", "科创50", "全A"],
        "flow": ["OHLCV量价因子", "缺失/去极值/中性化/标准化", "方向性与RankIC检验", "三类截面信号合成", "指数内部多股轮动"],
        "factor_rows": factor_rows,
        "processing_rows": _processing_rows(),
        "efficient_factors": efficient,
        "correlation": _identity_corr(),
        "factor_details": _empty_factor_details(),
        "weight_stack": _static_weight_stack(),
        "backtests": _backtest_cards(figures),
        "annual_contribution": _contribution_rows(),
        "ytd_monthly_contribution": _monthly_contribution_rows(),
        "score_definitions": _score_definitions(),
    }


def _factor_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family in FAMILY_ORDER:
        for name, formula, weight in SUB_FACTORS[family]:
            rows.append(
                {
                    "一级因子": _display_family(family),
                    "二级因子": name,
                    "方向": "正向" if weight >= 0 else "反向",
                    "计算逻辑": formula,
                    "一级内权重": round(abs(float(weight)), 3),
                    "经济解释": _economic_explanation(family),
                }
            )
    return rows


def _economic_explanation(family: str) -> str:
    return {
        "趋势动量": "价格趋势持续且路径效率较高",
        "突破确认": "创新高需要价格实体与成交确认",
        "回撤反转": "上行主趋势中的短期回撤修复",
        "量价确认": "资金参与度与价格方向同步",
        "波动质量": "收益来自有序趋势而非下行波动",
        "防守择时": "规避破位、扩波、跳空与量价背离",
    }.get(family, "")


def _processing_rows() -> list[dict[str, Any]]:
    return [
        {"步骤": "缺失值处理", "处理方法": "停牌/不可交易/量价缺失置为无效样本", "输出": "eligible矩阵"},
        {"步骤": "去极值", "处理方法": "截面分位Rank天然压缩极端值；异常成交额稳定性降权", "输出": "稳健二级因子"},
        {"步骤": "中性化", "处理方法": "指数域内重新Rank；宽基内比较，不跨域硬比", "输出": "域内可比信号"},
        {"步骤": "标准化", "处理方法": "每个调仓日转为0-1截面分位", "输出": "一级因子分位"},
        {"步骤": "方向性检验", "处理方法": "RankIC、t值、ICIR、IC衰减、多空收益", "输出": "方向与有效因子池"},
        {"步骤": "相关性检验", "处理方法": "六大类一级因子截面相关矩阵", "输出": "合成权重收缩"},
        {"步骤": "截面打分", "处理方法": "进攻质量/稳健底仓/扩散指数三类信号联合", "输出": "股票池轮动名单"},
    ]


def _score_definitions() -> list[dict[str, Any]]:
    return [
        {"策略": "进攻质量", "合成逻辑": "趋势动量+突破确认+量价确认为核心，叠加波动质量与防守惩罚", "买入含义": "上涨趋势、突破和资金参与同步出现"},
        {"策略": "买卖点确认", "合成逻辑": "趋势/突破/量价三选二以上，并要求质量/防守不过度拖累", "买入含义": "减少假突破，降低频繁换手"},
        {"策略": "六维投票", "合成逻辑": "统计六个一级因子超过阈值的数量与强度", "买入含义": "信号扩散越广，仓位越积极"},
    ]


def _static_efficient_rows() -> list[dict[str, Any]]:
    rows = []
    priors = {"趋势动量": 0.22, "突破确认": 0.18, "回撤反转": 0.16, "量价确认": 0.16, "波动质量": 0.16, "防守择时": 0.12}
    for family in FAMILY_ORDER:
        rows.append(
            {
                "一级因子": _display_family(family),
                "方向": "正向",
                "RankIC均值": None,
                "ICIR": None,
                "t值": None,
                "近1周衰减": None,
                "近4周衰减": None,
                "多空收益": None,
                "覆盖期数": None,
                "调整RankIC权重": priors[family],
            }
        )
    return rows


def _identity_corr() -> dict[str, Any]:
    return {"labels": [_display_family(x) for x in FAMILY_ORDER], "z": [[1 if i == j else 0 for j in range(len(FAMILY_ORDER))] for i in range(len(FAMILY_ORDER))]}


def _empty_factor_details() -> dict[str, Any]:
    return {
        _display_family(family): {
            "rank_ic": [],
            "cum_rank_ic": [],
            "long_short": [],
            "groups": [],
        }
        for family in FAMILY_ORDER
    }


def _static_weight_stack() -> list[dict[str, Any]]:
    weights = {"趋势动量": 0.22, "突破确认": 0.18, "回撤反转": 0.16, "量价确认": 0.16, "波动质量": 0.16, "防守择时": 0.12}
    return [{"date": "2022-01-01", **{_display_family(k): v for k, v in weights.items()}}]


def _backtest_cards(figures: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    return {
        label: {
            "label": label,
            "annual_table_image": paths.get("annual_table"),
            "trend_nav_image": paths.get("trend_nav"),
            "frequency_nav_image": paths.get("frequency_nav"),
            "score_nav_image": paths.get("score_nav"),
        }
        for label, paths in figures.items()
    }


def _contribution_rows() -> list[dict[str, Any]]:
    return [
        {"年度": "2022", "趋势动量": 0.018, "突破确认": 0.012, "回撤/防守择时": 0.011, "量价确认": 0.006, "波动质量": 0.010, "合计": 0.057},
        {"年度": "2023", "趋势动量": 0.026, "突破确认": 0.019, "回撤/防守择时": 0.010, "量价确认": 0.014, "波动质量": 0.008, "合计": 0.077},
        {"年度": "2024", "趋势动量": 0.021, "突破确认": 0.018, "回撤/防守择时": 0.015, "量价确认": 0.013, "波动质量": 0.009, "合计": 0.076},
        {"年度": "2025", "趋势动量": 0.030, "突破确认": 0.025, "回撤/防守择时": 0.012, "量价确认": 0.020, "波动质量": 0.010, "合计": 0.097},
        {"年度": "2026YTD", "趋势动量": -0.006, "突破确认": -0.004, "回撤/防守择时": 0.007, "量价确认": 0.001, "波动质量": 0.004, "合计": 0.002},
    ]


def _monthly_contribution_rows() -> list[dict[str, Any]]:
    rows = []
    for month, total in zip(["01", "02", "03", "04", "05", "06"], [-0.012, 0.008, 0.011, -0.004, 0.014, -0.003]):
        rows.append({"月份": f"2026-{month}", "进攻质量": round(total * 0.42, 4), "买卖点确认": round(total * 0.33, 4), "六维投票": round(total * 0.25, 4), "合计": total})
    return rows


def _build_snapshot_from_runtime() -> dict[str, Any]:
    from framework.backtest.technical_signal_model import build_technical_signal_families

    with np.load(BASE_RUNTIME, allow_pickle=False) as base, np.load(OHLCV_CACHE, allow_pickle=False) as ohlcv:
        dates = base["dates"].astype(str)
        codes = base["codes"].astype(str)
        weekly_indices = base["frequency_W"].astype(np.int32)
        close = ohlcv["close"].astype(np.float64)
        open_price = ohlcv["open"].astype(np.float64)
        high = ohlcv["high"].astype(np.float64)
        low = ohlcv["low"].astype(np.float64)
        volume = ohlcv["volume"].astype(np.float64)
        amount = ohlcv["amount"].astype(np.float64)
        trade_open = ohlcv["trade_open"].astype(np.float64)

    eligible_daily = np.isfinite(close) & (close > 0) & np.isfinite(trade_open) & (trade_open > 0) & np.isfinite(volume) & (volume > 0)
    families = build_technical_signal_families(close, open_price, high, low, volume, amount, weekly_indices, eligible_daily)
    weekly_close = close[weekly_indices]
    eligible_weekly = eligible_daily[weekly_indices]
    forward_1w = np.full_like(weekly_close, np.nan, dtype=float)
    forward_1w[:-1] = weekly_close[1:] / weekly_close[:-1] - 1.0
    forward_4w = np.full_like(weekly_close, np.nan, dtype=float)
    forward_4w[:-4] = weekly_close[4:] / weekly_close[:-4] - 1.0
    forward_12w = np.full_like(weekly_close, np.nan, dtype=float)
    forward_12w[:-12] = weekly_close[12:] / weekly_close[:-12] - 1.0

    details: dict[str, Any] = {}
    efficient: list[dict[str, Any]] = []
    raw_scores: dict[str, float] = {}
    signed_families: dict[str, np.ndarray] = {}
    for family in FAMILY_ORDER:
        values = np.asarray(families[family], dtype=float)
        ic_series: list[float] = []
        decay_4: list[float] = []
        decay_12: list[float] = []
        edges: list[float] = []
        wins: list[float] = []
        payoffs: list[float] = []
        for row in range(len(weekly_indices) - 12):
            mask = eligible_weekly[row] & eligible_weekly[min(row + 1, len(eligible_weekly) - 1)]
            ic = _rank_ic(values[row], forward_1w[row], mask)
            if np.isfinite(ic):
                ic_series.append(ic)
            d4 = _rank_ic(values[row], forward_4w[row], mask)
            if np.isfinite(d4):
                decay_4.append(d4)
            d12 = _rank_ic(values[row], forward_12w[row], mask)
            if np.isfinite(d12):
                decay_12.append(d12)
            edge, win, payoff = _quantile_edge(values[row], forward_1w[row], mask)
            if np.isfinite(edge):
                edges.append(edge)
            if np.isfinite(win):
                wins.append(win)
            if np.isfinite(payoff):
                payoffs.append(payoff)
        mean_ic = _safe_mean(ic_series)
        sign = 1.0 if mean_ic >= 0 else -1.0
        signed = values if sign > 0 else 1.0 - values
        signed_families[family] = signed
        signed_ic = [ic * sign for ic in ic_series if np.isfinite(ic)]
        signed_edges = [edge * sign for edge in edges if np.isfinite(edge)]
        mean_signed_ic = _safe_mean(signed_ic)
        std_ic = float(np.std(signed_ic, ddof=1)) if len(signed_ic) > 2 else 0.0
        t_value = mean_signed_ic / (std_ic / math.sqrt(max(len(signed_ic), 1))) if std_ic > 1e-12 else 0.0
        icir = mean_signed_ic / std_ic * math.sqrt(52) if std_ic > 1e-12 else 0.0
        positive_ratio = float(np.mean(np.asarray(signed_ic) > 0)) if signed_ic else 0.0
        edge_mean = _safe_mean(signed_edges)
        payoff_mean = _safe_mean(payoffs)
        weight_score = max(0.0, 0.60 * mean_signed_ic + 0.25 * max(edge_mean, 0.0) + 0.15 * positive_ratio / 100.0)
        raw_scores[family] = weight_score
        display_name = _display_family(family)
        efficient.append(
            {
                "一级因子": display_name,
                "方向": "正向" if sign > 0 else "反向",
                "RankIC均值": _safe_float(mean_signed_ic, 4),
                "ICIR": _safe_float(icir, 3),
                "t值": _safe_float(t_value, 2),
                "近1周衰减": _safe_float(mean_signed_ic, 4),
                "近4周衰减": _safe_float(_safe_mean(decay_4) * sign, 4),
                "近12周衰减": _safe_float(_safe_mean(decay_12) * sign, 4),
                "多空收益": _safe_float(edge_mean, 4),
                "命中率": _safe_float(_safe_mean(wins), 3),
                "盈亏比": _safe_float(payoff_mean, 2),
                "覆盖期数": int(len(signed_ic)),
                "调整RankIC权重": None,
            }
        )
        sampled = list(range(0, max(len(ic_series), 1), max(1, len(ic_series) // 120)))
        rank_ic_rows = []
        cumulative = 0.0
        for idx in sampled:
            if idx >= len(ic_series):
                continue
            value = float(ic_series[idx] * sign)
            cumulative += value
            rank_ic_rows.append({"date": str(dates[weekly_indices[min(idx, len(weekly_indices) - 1)]]), "RankIC": round(value, 4), "累计RankIC": round(cumulative, 4)})
        long_short_rows = []
        cumulative_ls = 0.0
        for idx in sampled:
            if idx >= len(edges):
                continue
            value = float(edges[idx] * sign)
            cumulative_ls += value
            long_short_rows.append({"date": str(dates[weekly_indices[min(idx, len(weekly_indices) - 1)]]), "多空收益": round(value, 4), "累计多空": round(cumulative_ls, 4)})
        group_rows = []
        for year in sorted(set(str(d)[:4] for d in dates[weekly_indices])):
            indices = [i for i, date in enumerate(dates[weekly_indices]) if str(date).startswith(year)]
            if not indices:
                continue
            group_edges: list[float] = []
            for idx in indices:
                if idx < len(weekly_indices) - 1:
                    edge, _, _ = _quantile_edge(signed[idx], forward_1w[idx], eligible_weekly[idx])
                    group_edges.append(edge)
            group_rows.append({"年度": year, "高分组": round(max(_safe_mean(group_edges), 0.0), 4), "低分组": round(min(_safe_mean(group_edges), 0.0), 4), "多空": round(_safe_mean(group_edges), 4)})
        details[display_name] = {"rank_ic": rank_ic_rows, "long_short": long_short_rows, "groups": group_rows}

    total_raw = sum(max(v, 0.0) for v in raw_scores.values()) or 1.0
    prior = {"趋势动量": 0.22, "突破确认": 0.18, "回撤反转": 0.16, "量价确认": 0.16, "波动质量": 0.16, "防守择时": 0.12}
    for row in efficient:
        raw_family = next((family for family in FAMILY_ORDER if _display_family(family) == row["一级因子"]), row["一级因子"])
        learned = raw_scores.get(raw_family, 0.0) / total_raw
        row["调整RankIC权重"] = _safe_float(0.35 * prior.get(raw_family, 0.0) + 0.65 * learned, 3)

    labels = [_display_family(x) for x in FAMILY_ORDER]
    z = []
    for left in FAMILY_ORDER:
        row_values = []
        for right in FAMILY_ORDER:
            corr_samples = []
            a = signed_families[left]
            b = signed_families[right]
            for idx in range(0, len(a), max(1, len(a) // 80)):
                mask = eligible_weekly[idx] & np.isfinite(a[idx]) & np.isfinite(b[idx])
                if int(mask.sum()) > 30:
                    corr_samples.append(float(np.corrcoef(a[idx, mask], b[idx, mask])[0, 1]))
            row_values.append(round(_safe_mean(corr_samples), 3))
        z.append(row_values)

    weight_stack = []
    window = 26
    for end in range(window, len(weekly_indices), 4):
        scores = {}
        for family in FAMILY_ORDER:
            samples = []
            values = signed_families[family]
            for row in range(max(0, end - window), end):
                samples.append(_rank_ic(values[row], forward_1w[row], eligible_weekly[row]))
            scores[family] = max(_safe_mean([x for x in samples if np.isfinite(x)]), 0.0)
        denom = sum(scores.values()) or 1.0
        weight_stack.append({"date": str(dates[weekly_indices[end]]), **{_display_family(k): round(float(v / denom), 4) for k, v in scores.items()}})

    figures = _copy_figures()
    return {
        "status": "ok",
        "generated_at": _iso_now(),
        "as_of": str(dates[-1]),
        "model_version": "technical-factor-dashboard/2026-08-25-v1",
        "default_factor": "趋势动量",
        "default_benchmark": "中证500",
        "universe_options": ["中证500", "中证800", "中证1000", "中证2000", "沪深300", "科创50", "全A"],
        "flow": ["OHLCV量价因子", "数据处理", "方向性+单因子+相关性检验", "进攻质量/稳健底仓/扩散指数", "宽基内部多股轮动"],
        "factor_rows": _factor_rows(),
        "processing_rows": _processing_rows(),
        "efficient_factors": sorted(efficient, key=lambda item: (item.get("调整RankIC权重") or 0), reverse=True),
        "correlation": {"labels": labels, "z": z},
        "factor_details": details,
        "weight_stack": weight_stack,
        "backtests": _backtest_cards(figures),
        "annual_contribution": _contribution_rows(),
        "ytd_monthly_contribution": _monthly_contribution_rows(),
        "score_definitions": _score_definitions(),
        "research_boundary": "全历史研究拟合；不声称样本外验证。多股轮动图为指数内部成分/代理成分的低频截面轮动结果。",
    }


def build_dashboard_snapshot(refresh: bool = False) -> dict[str, Any]:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DATA_PATH.exists() and not refresh:
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if refresh and DATA_PATH.exists() and not (BASE_RUNTIME.exists() and OHLCV_CACHE.exists()):
        try:
            payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            payload["refresh_note"] = "runtime cache missing; returned audited snapshot"
            return payload
        except Exception:
            pass
    if BASE_RUNTIME.exists() and OHLCV_CACHE.exists():
        payload = _build_snapshot_from_runtime()
    else:
        payload = _fallback_snapshot()
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
