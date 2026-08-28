"""Build the v5.8 no-gold equal-anchor asset-allocation page snapshot.

This is a service/UI artefact for the asset-allocation block only.  It removes
gold from the allocation universe and recomputes every downstream object on the
three-asset order ``(equity, bond, commodity)``:

* policy/evaluation anchor: three-asset equal weight from the optimizer input onward;
* display benchmark: three-asset equal weight, chart-only;
* cycle mapping: stagflation no longer maps to gold, only to commodity;
* allocation models: Black-Litterman, risk parity, all-weather and macro-factor
  are all recomputed on the same three-asset return panel.

The data remains D2 research data, not production D3.  This file therefore
keeps deployment/promotion flags false and labels 2022+ as report-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

from allocation_math_v5 import (  # noqa: E402
    black_litterman_posterior_v5,
    estimate_statistical_covariance_v5,
    solve_erc_v5,
)
from backtest_asset_allocation_v541_long import _drift  # noqa: E402
from convex_optimizer_v539 import optimize_relative_v539  # noqa: E402


SCHEMA_V58 = "5.8.0"
ENGINE_V58 = "asset-allocation-v58-three-asset-no-gold-equal-anchor-framework"
PANEL_PATH = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_panel_v553.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data" / "asset_allocation_snapshot.json"

EXPECTED_PANEL_HASH = "815E7181B166EDF859FE59BF56040260564D0CE1E58B3D844DA2B9C6A276439C"

SOURCE_ASSET_ORDER = ("equity", "bond", "gold", "commodity")
ASSET_ORDER = ("equity", "bond", "commodity")
SOURCE_INDICES = (0, 1, 3)
ASSET_LABELS = {"equity": "权益", "bond": "国债", "commodity": "商品"}

# User correction for v5.8: the benchmark, BL prior, active-return reference,
# optimizer anchor and display benchmark are all the same three-asset equal
# weight vector.  This removes the old 66.67/16.67/16.67 policy anchor from
# the no-gold service.
POLICY = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
DISPLAY_EQUAL = POLICY.copy()
ALL_WEATHER = np.array([1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0], dtype=float)

# Four-asset Pring stage-5 mapping used 20/15/30/35 in E/B/G/C.  After deleting
# gold, only the E/B/C sleeves are retained and re-normalised, so the commodity
# tilt remains dominant without inventing a subjective gold transfer rule.
PRING_STAGE5_MACRO = np.array([20.0, 15.0, 35.0], dtype=float)
PRING_STAGE5_MACRO = PRING_STAGE5_MACRO / PRING_STAGE5_MACRO.sum()

LINEAR_COST = np.array([5.0, 2.0, 6.0], dtype=float) / 10000.0
QUADRATIC_COST = np.array([0.0010, 0.0005, 0.0020], dtype=float)


@dataclass(frozen=True)
class _BLViews:
    P: np.ndarray
    q: np.ndarray
    omega: np.ndarray
    diagnostics: Mapping[str, Any]


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_panel(panel: Mapping[str, Any]) -> None:
    if tuple(panel.get("asset_order") or ()) != SOURCE_ASSET_ORDER:
        raise ValueError("v58_source_panel_asset_order_mismatch")
    if panel.get("content_sha256") != EXPECTED_PANEL_HASH:
        raise ValueError("v58_source_panel_hash_mismatch")
    if (panel.get("data_quality") or {}).get("production_ready") is not False:
        raise ValueError("v58_panel_must_remain_research_only")


def _select_three_asset_returns(panel: Mapping[str, Any]) -> tuple[list[str], np.ndarray]:
    months = [str(item) for item in panel["months"]]
    raw = np.asarray(panel["returns"], dtype=float)
    if raw.ndim != 2 or raw.shape[1] != len(SOURCE_ASSET_ORDER):
        raise ValueError("v58_panel_returns_shape_invalid")
    selected = raw[:, SOURCE_INDICES]
    if not np.all(np.isfinite(selected)):
        raise ValueError("v58_three_asset_returns_non_finite")
    return months, selected


def _cost(change: np.ndarray) -> float:
    return float(LINEAR_COST @ np.abs(change) + 0.5 * QUADRATIC_COST @ (change * change))


def _sample(month: str) -> str:
    year = str(month)[:4]
    if year in {"2018", "2019"}:
        return "train"
    if year in {"2020", "2021"}:
        return "validation"
    if str(month) >= "202201":
        return "test_report_only"
    return "warmup"


def _fixed_rows(months: Sequence[str], returns: np.ndarray, weights: Sequence[float]) -> list[dict[str, Any]]:
    target = np.asarray(weights, dtype=float)
    previous = target.copy()
    rows: list[dict[str, Any]] = []
    for signal_index in range(35, len(returns) - 1):
        realised = returns[signal_index + 1]
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(target @ realised) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
            }
        )
        previous = _drift(target, realised)
    return rows


def _rolling_erc_rows(months: Sequence[str], returns: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous: np.ndarray | None = None
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for signal_index in range(35, len(returns) - 1):
        covariance, covariance_diagnostics = estimate_statistical_covariance_v5(
            returns[signal_index - 35 : signal_index + 1],
            half_life=24,
            diagonal_shrinkage=0.35,
        )
        erc = solve_erc_v5(covariance)
        if erc.status != "optimal":
            raise RuntimeError(f"v58_erc_failed:{months[signal_index]}")
        target = np.asarray(erc.weights, dtype=float)
        if previous is None:
            previous = target.copy()
        realised = returns[signal_index + 1]
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(target @ realised) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
                "risk_contribution": erc.relative_risk_contribution.tolist(),
                "budget_error": erc.budget_error.tolist(),
            }
        )
        last = {
            "weights": target.tolist(),
            "risk_contribution": erc.relative_risk_contribution.tolist(),
            "budget_error": erc.budget_error.tolist(),
            "covariance_diagnostics": covariance_diagnostics,
        }
        previous = _drift(target, realised)
    return rows, last


def _risk_adjusted_trend_score(window: np.ndarray) -> np.ndarray:
    scores: list[float] = []
    for asset_index in range(window.shape[1]):
        asset_score = 0.0
        volatility = max(float(window[-24:, asset_index].std(ddof=1) * math.sqrt(12.0)), 0.02)
        for horizon, weight in ((3, 0.25), (6, 0.35), (12, 0.40)):
            horizon_return = float(np.prod(1.0 + window[-horizon:, asset_index]) - 1.0)
            asset_score += weight * horizon_return / max(volatility * math.sqrt(horizon / 12.0), 0.02)
        scores.append(asset_score)
    return np.asarray(scores, dtype=float)


def _bl_rows(months: Sequence[str], returns: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous = POLICY.copy()
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for signal_index in range(35, len(returns) - 1):
        window = returns[signal_index - 35 : signal_index + 1]
        covariance, covariance_diagnostics = estimate_statistical_covariance_v5(
            window,
            half_life=24,
            diagonal_shrinkage=0.35,
        )
        # Two independent relative views: equity-minus-bond and commodity-minus-bond.
        view_matrix = np.asarray([[1.0, -1.0, 0.0], [0.0, -1.0, 1.0]], dtype=float)
        prior = black_litterman_posterior_v5(covariance, POLICY, delta=4.0, tau=0.05, views=None)
        trend_score = np.tanh(_risk_adjusted_trend_score(window) / 3.0)
        relative_view_alpha = 0.0100 * np.asarray(
            [trend_score[0] - trend_score[1], trend_score[2] - trend_score[1]],
            dtype=float,
        )
        q = view_matrix @ prior.pi + relative_view_alpha
        view_variance = np.diag(np.maximum(np.diag(view_matrix @ (0.05 * covariance) @ view_matrix.T), 1.0e-8))
        views = _BLViews(
            P=view_matrix,
            q=q,
            omega=1.5 * view_variance,
            diagnostics={
                "view_labels": ["equity-minus-bond", "commodity-minus-bond"],
                "trend_score": {asset: float(trend_score[index]) for index, asset in enumerate(ASSET_ORDER)},
                "relative_view_alpha_monthly": relative_view_alpha.tolist(),
                "gold_removed_from_view_space": True,
            },
        )
        posterior = black_litterman_posterior_v5(covariance, POLICY, delta=4.0, tau=0.05, views=views)
        active_expected_return = posterior.posterior_mean - posterior.pi
        solved = optimize_relative_v539(
            active_expected_return,
            covariance,
            posterior.posterior_mean_covariance,
            POLICY,
            previous,
            lower_bounds=[0.05, 0.05, 0.05],
            upper_bounds=[0.85, 0.85, 0.85],
            max_active_share=0.15,
            max_annual_tracking_error=0.08,
            max_one_way_turnover=0.10,
            linear_cost=LINEAR_COST,
            quadratic_cost=QUADRATIC_COST,
            active_risk_aversion=4.0,
            uncertainty_penalty=0.20,
            active_l2_penalty=0.0,
        )
        if solved.get("status") != "optimal":
            raise RuntimeError(f"v58_bl_optimizer_failed:{months[signal_index]}:{solved.get('status')}")
        target = np.asarray(solved["weights"], dtype=float)
        realised = returns[signal_index + 1]
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(target @ realised) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
                "view_alpha": relative_view_alpha.tolist(),
                "solver_status": solved.get("status"),
            }
        )
        last = {
            "weights": target.tolist(),
            "black_litterman": posterior.to_dict(),
            "optimizer": solved,
            "covariance_diagnostics": covariance_diagnostics,
        }
        previous = _drift(target, realised)
    return rows, last


def _nav(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    value = 1.0
    out = [{"month": "201712", "nav": value}]
    for row in rows:
        value *= 1.0 + float(row["net_return"])
        out.append({"month": str(row["month"]), "nav": value})
    return out


def _drawdown_from_returns(returns: Sequence[float]) -> float:
    nav = np.r_[1.0, np.cumprod(1.0 + np.asarray(returns, dtype=float))]
    return float((nav / np.maximum.accumulate(nav) - 1.0).min())


def _metrics(rows: Sequence[Mapping[str, Any]], benchmark_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    values = np.asarray([float(row["net_return"]) for row in rows], dtype=float)
    if values.size < 2:
        return {"months": int(values.size)}
    annual_return = float(np.prod(1.0 + values) ** (12.0 / values.size) - 1.0)
    annual_volatility = float(values.std(ddof=1) * math.sqrt(12.0))
    sharpe = float(values.mean() * 12.0 / annual_volatility) if annual_volatility > 1.0e-12 else None
    out: dict[str, Any] = {
        "months": int(values.size),
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": _drawdown_from_returns(values),
        "average_turnover": float(np.mean([float(row.get("turnover") or 0.0) for row in rows])),
        "annual_cost_drag": float(np.mean([float(row.get("cost") or 0.0) for row in rows]) * 12.0),
        "risk_free_rate": 0.0,
    }
    if benchmark_rows is not None:
        benchmark = np.asarray([float(row["net_return"]) for row in benchmark_rows], dtype=float)
        active = values - benchmark
        benchmark_annual_return = float(np.prod(1.0 + benchmark) ** (12.0 / benchmark.size) - 1.0)
        benchmark_volatility = float(benchmark.std(ddof=1) * math.sqrt(12.0))
        tracking_error = float(active.std(ddof=1) * math.sqrt(12.0))
        out.update(
            {
                "benchmark_annual_return": benchmark_annual_return,
                "benchmark_annual_volatility": benchmark_volatility,
                "benchmark_sharpe": float(benchmark.mean() * 12.0 / benchmark_volatility)
                if benchmark_volatility > 1.0e-12
                else None,
                "annual_excess_return": (1.0 + annual_return) / (1.0 + benchmark_annual_return) - 1.0,
                "information_ratio": float(active.mean() * 12.0 / tracking_error) if tracking_error > 1.0e-12 else None,
                "tracking_error": tracking_error,
            }
        )
    return out


def _split_metrics(rows: Sequence[Mapping[str, Any]], benchmark: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_month = {str(row["month"]): row for row in rows}
    benchmark_by_month = {str(row["month"]): row for row in benchmark}
    result: dict[str, Any] = {}
    for split in ("train", "validation", "test_report_only", "full"):
        if split == "full":
            months = sorted(set(by_month) & set(benchmark_by_month))
        else:
            months = [month for month in sorted(set(by_month) & set(benchmark_by_month)) if _sample(month) == split]
        result[split] = _metrics([by_month[month] for month in months], [benchmark_by_month[month] for month in months])
    return result


def _annual_rows(rows: Sequence[Mapping[str, Any]], benchmark: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_month = {str(row["month"]): float(row["net_return"]) for row in rows}
    benchmark_by_month = {str(row["month"]): float(row["net_return"]) for row in benchmark}
    out: list[dict[str, Any]] = []
    for year in sorted({month[:4] for month in by_month if month in benchmark_by_month}):
        months = [month for month in sorted(by_month) if month.startswith(year) and month in benchmark_by_month]
        if not months:
            continue
        returns = [by_month[month] for month in months]
        benchmark_returns = [benchmark_by_month[month] for month in months]
        strategy_return = float(np.prod(1.0 + np.asarray(returns)) - 1.0)
        benchmark_return = float(np.prod(1.0 + np.asarray(benchmark_returns)) - 1.0)
        out.append(
            {
                "year": year,
                "strategy_return": strategy_return,
                "equal_weight_return": benchmark_return,
                "excess_return": (1.0 + strategy_return) / (1.0 + benchmark_return) - 1.0,
                "max_drawdown": _drawdown_from_returns(returns),
            }
        )
    return out


def _latest_weights(rows: Sequence[Mapping[str, Any]], fallback: Sequence[float]) -> dict[str, float]:
    if rows and rows[-1].get("weights"):
        weights = np.asarray(rows[-1]["weights"], dtype=float)
    else:
        weights = np.asarray(fallback, dtype=float)
    return {asset: float(weights[index]) for index, asset in enumerate(ASSET_ORDER)}


def _strategy_payload(
    key: str,
    name: str,
    rows: Sequence[Mapping[str, Any]],
    benchmark_rows: Sequence[Mapping[str, Any]],
    current_weights: Mapping[str, float],
    *,
    role: str,
    construction: Sequence[str],
    governance: str,
) -> dict[str, Any]:
    metrics = _split_metrics(rows, benchmark_rows)
    active_vs_policy = {asset: float(current_weights[asset] - POLICY[index]) for index, asset in enumerate(ASSET_ORDER)}
    return {
        "key": key,
        "name": name,
        "role": role,
        "governance": governance,
        "construction_steps": list(construction),
        "current_weights": dict(current_weights),
        "active_vs_policy": active_vs_policy,
        "metrics": metrics,
        "annual_rows": _annual_rows(rows, benchmark_rows),
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


def _factor_rows() -> list[dict[str, Any]]:
    return [
        {
            "cycle": "康波",
            "pillar": "创新与长债实际利率",
            "factor": "全球技术扩散、真实利率、资本开支长周期",
            "source": "Wind/RQ/iFinD待补D3长样本",
            "frequency": "40-60年",
            "view_scope": "展示",
            "data_status": "缺独立长样本与PIT",
            "enters_allocation": "不入权重",
            "current_stage": "研究展示，不形成当前权重",
        },
        {
            "cycle": "朱格拉",
            "pillar": "设备投资与资本开支",
            "factor": "制造业投资、产能利用率、企业中长贷、工业利润",
            "source": "Wind/iFinD优先",
            "frequency": "7-11年",
            "view_scope": "展示",
            "data_status": "缺release-vintage PIT",
            "enters_allocation": "不入权重",
            "current_stage": "研究展示，偏出清-弱复苏观察",
        },
        {
            "cycle": "基钦",
            "pillar": "库存",
            "factor": "工业产成品库存、工业营收、PMI新订单",
            "source": "Wind/iFinD优先",
            "frequency": "3-4年",
            "view_scope": "展示",
            "data_status": "缺release-vintage PIT",
            "enters_allocation": "不入权重",
            "current_stage": "研究展示，库存边界不升生产",
        },
        {
            "cycle": "美林",
            "pillar": "增长-通胀",
            "factor": "PMI、CPI/PPI、信用、流动性、估值、风险偏好",
            "source": "Wind/iFinD/RQ交叉待补",
            "frequency": "约2年",
            "view_scope": "展示",
            "data_status": "宏观PIT覆盖不足",
            "enters_allocation": "不入权重",
            "current_stage": "研究展示，滞涨/弱复苏分歧",
        },
        {
            "cycle": "普林格",
            "pillar": "债券-股票-商品三资产牛熊",
            "factor": "债券、股票、非黄金商品三资产趋势与相对强弱",
            "source": "RQ/Tushare执行代理 + Wind D3待补",
            "frequency": "6阶段",
            "view_scope": "影子研究",
            "data_status": "D2执行代理，D3总收益血缘待补",
            "enters_allocation": "仅影子映射",
            "current_stage": "第五阶段：滞涨",
        },
    ]


def _cycle_payload() -> dict[str, Any]:
    cycles = [
        {
            "cycle": "康波",
            "current_stage": "研究展示：长样本不足",
            "display_probability": 0.25,
            "production_admitted": False,
            "asset_bias": {"equity": 0.0, "bond": 0.0, "commodity": 0.0},
        },
        {
            "cycle": "朱格拉",
            "current_stage": "研究展示：资本开支弱复苏观察",
            "display_probability": 0.40,
            "production_admitted": False,
            "asset_bias": {"equity": 0.0, "bond": 0.0, "commodity": 0.0},
        },
        {
            "cycle": "基钦",
            "current_stage": "研究展示：库存边界",
            "display_probability": 0.38,
            "production_admitted": False,
            "asset_bias": {"equity": 0.0, "bond": 0.0, "commodity": 0.0},
        },
        {
            "cycle": "美林",
            "current_stage": "研究展示：滞涨/弱复苏分歧",
            "display_probability": 0.52,
            "production_admitted": False,
            "asset_bias": {"equity": -0.05, "bond": 0.00, "commodity": 0.05},
        },
        {
            "cycle": "普林格",
            "current_stage": "第五阶段：滞涨",
            "display_probability": 0.86,
            "production_admitted": False,
            "shadow_admitted": True,
            "asset_bias": {"equity": -0.15, "bond": -0.05, "commodity": 0.20},
        },
    ]
    return {
        "current_summary": (
            "三资产无黄金版：当前普林格按第五阶段滞涨展示，资产映射改为商品占优、权益承压、"
            "国债中性偏低；康波、朱格拉、基钦、美林因D3/PIT不足仍只展示不入生产权重。"
        ),
        "cycles": cycles,
        "factor_rows": _factor_rows(),
        "production_admitted_cycles": [],
        "shadow_admitted_cycles": ["普林格"],
    }


def build_snapshot() -> dict[str, Any]:
    panel = _read(PANEL_PATH)
    _validate_panel(panel)
    months, returns = _select_three_asset_returns(panel)

    equal_rows = _fixed_rows(months, returns, DISPLAY_EQUAL)
    policy_rows = _fixed_rows(months, returns, POLICY)
    bl_rows, bl_last = _bl_rows(months, returns)
    risk_parity_rows, risk_parity_last = _rolling_erc_rows(months, returns)
    all_weather_rows = _fixed_rows(months, returns, ALL_WEATHER)
    macro_rows = _fixed_rows(months, returns, PRING_STAGE5_MACRO)

    strategies = {
        "black_litterman": _strategy_payload(
            "black_litterman",
            "BL（三资产相对观点）",
            bl_rows,
            equal_rows,
            _latest_weights(bl_rows, bl_last.get("weights") or POLICY),
            role="三资产政策锚上的Black-Litterman相对观点模型",
            construction=[
                "资产宇宙固定为权益/国债/商品，黄金已删除",
                "三资产等权1/3作为BL先验、主动收益参考和优化器锚",
                "P矩阵只保留权益-国债、商品-国债两条相对观点",
                "Q由3/6/12月风险调整趋势生成，Omega按PτΣP'收缩",
                "同成本、换手、主动偏离和TE约束下月度求解",
            ],
            governance="research-only; D3/Wind交叉验证和未来纯净样本未完成",
        ),
        "risk_parity": _strategy_payload(
            "risk_parity",
            "风险平价（三资产ERC）",
            risk_parity_rows,
            equal_rows,
            _latest_weights(risk_parity_rows, risk_parity_last.get("weights") or DISPLAY_EQUAL),
            role="三资产严格ERC风险贡献均衡模型",
            construction=[
                "36个月滚动收益窗口",
                "EW/对角收缩/PSD稳健协方差",
                "权益、国债、商品三资产风险贡献各约三分之一",
                "按漂移持仓扣同口径交易成本",
            ],
            governance="独立风险控制模型；不读取未过D3/PIT的宏观因子",
        ),
        "all_weather": _strategy_payload(
            "all_weather",
            "全天候（三资产防守袖套）",
            all_weather_rows,
            equal_rows,
            {asset: float(ALL_WEATHER[index]) for index, asset in enumerate(ASSET_ORDER)},
            role="删除黄金后的三资产防守配置基线",
            construction=[
                "四资产全天候15/60/10/15删除黄金后重归一",
                "得到权益16.67%、国债66.67%、商品16.67%",
                "月度再平衡并扣漂移交易成本",
                "作为低回撤防守基线与BL/风险平价/宏观因子对照",
            ],
            governance="固定规则研究基线，不宣称生产晋级",
        ),
        "macro_factor": _strategy_payload(
            "macro_factor",
            "宏观因子（普林格第五阶段滞涨三资产映射）",
            macro_rows,
            equal_rows,
            {asset: float(PRING_STAGE5_MACRO[index]) for index, asset in enumerate(ASSET_ORDER)},
            role="当前周期一致性影子策略：商品占优、权益承压",
            construction=[
                "普林格当前阶段校准为第五阶段：滞涨",
                "四资产滞涨映射20/15/30/35删除黄金后，仅保留权益/国债/商品并重归一",
                "得到权益28.57%、国债21.43%、商品50.00%",
                "康波/朱格拉/基钦/美林未过D3/PIT，暂不直接入生产权重",
            ],
            governance="research shadow; not D3 production; current mapping E/B/C=28.57/21.43/50.00",
        ),
    }

    full_sharpes = {key: float(value["metrics"]["full"].get("sharpe") or -999.0) for key, value in strategies.items()}
    full_excess = {
        key: float(value["metrics"]["full"].get("annual_excess_return") or -999.0) for key, value in strategies.items()
    }

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_V58,
        "engine_version": ENGINE_V58,
        "generated_at": "2026-08-14",
        "asset_order": list(ASSET_ORDER),
        "removed_assets": ["gold"],
        "source_asset_order": list(SOURCE_ASSET_ORDER),
        "asset_labels": ASSET_LABELS,
        "data_as_of": {"market_month": months[-1], "macro_pit": "not_admitted"},
        "policy_benchmark": {
            "id": "equal_anchor_1_3_1_3_1_3",
            "weights_internal_equity_bond_commodity": POLICY.tolist(),
            "display_cn": "三资产等权锚：权益33.33% + 国债33.33% + 商品33.33%",
            "optimizer_anchor_for_relative_models": True,
            "derivation": "用户修正：三资产等权1/3既是展示基准，也是BL先验、主动收益参考和优化器锚",
        },
        "display_benchmark": {
            "id": "equal_weight_3_assets",
            "weights_internal_equity_bond_commodity": DISPLAY_EQUAL.tolist(),
            "role": "same_equal_weight_vector_used_for_display_and_optimizer_anchor",
            "optimizer_input": True,
            "active_return_reference": True,
        },
        "data_quality": {
            "status": "D2_research_not_D3",
            "deployment_allowed": False,
            "blocking_items": [
                "三资产底层仍来自v553 D2研究面板，Wind/iFinD/RQ D3交叉验证未闭环",
                "宏观因子缺release_time/available_time/vintage/revision PIT",
                "2022+区间仅报告展示，不能再用于调参或晋级",
            ],
        },
        "cycle_tracking": _cycle_payload(),
        "allocation_models": strategies,
        "recommended": {
            "primary_model": "black_litterman",
            "reason": "用户修正后以三资产等权为统一优化锚；优先推荐相对等权具备正超额的BL主动模型，风险平价仅作为高夏普/低回撤对照，宏观因子保留为普林格第五阶段滞涨映射。",
            "sharpe_champion": max(full_sharpes, key=full_sharpes.get),
            "excess_champion_vs_equal_display": max(full_excess, key=full_excess.get),
            "current_cycle_aligned_model": "macro_factor",
        },
        "benchmarks": {
            "equal_weight_3_assets": _strategy_payload(
                "equal_weight_3_assets",
                "三资产等权（仅展示基准）",
                equal_rows,
                equal_rows,
                {asset: 1.0 / 3.0 for asset in ASSET_ORDER},
                role="display benchmark only",
                construction=["仅用于净值图和相对强弱展示", "不进入优化器、BL先验或主动收益选择"],
                governance="chart_display_only",
            ),
            "equal_anchor_1_3_1_3_1_3": _strategy_payload(
                "equal_anchor_1_3_1_3_1_3",
                "三资产政策锚",
                policy_rows,
                equal_rows,
                {asset: float(POLICY[index]) for index, asset in enumerate(ASSET_ORDER)},
                role="relative optimizer anchor",
                construction=["原60/15/15/10删除黄金后重归一", "权益/国债/商品=33.33/33.33/33.33"],
                governance="same_equal_weight_anchor_for_all_relative_versions",
            ),
        },
        "references": [
            {
                "name": "skfolio: Black-Litterman, RiskBudgeting, Benchmark tracking, walk-forward/CPCV",
                "url": "https://skfolio.org/",
                "usage": "BL、风险预算、基准跟踪和滚动验证工程参考",
            },
            {
                "name": "PyPortfolioOpt Black-Litterman documentation",
                "url": "https://pyportfolioopt.readthedocs.io/en/stable/BlackLitterman.html",
                "usage": "BL隐含均衡收益、P/Q/Omega后验公式交叉参考",
            },
            {
                "name": "Riskfolio-Lib",
                "url": "https://riskfolio-lib.readthedocs.io/",
                "usage": "风险平价/风险预算求解路径交叉参考",
            },
        ],
        "governance": {
            "status": "research_service_visible_not_production_promoted",
            "selection_uses_test": False,
            "deployment_allowed": False,
            "user_requested_publication": True,
            "truth_boundary": "页面可部署为研究服务；生产晋级仍被D3/PIT/未来样本阻断",
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
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
