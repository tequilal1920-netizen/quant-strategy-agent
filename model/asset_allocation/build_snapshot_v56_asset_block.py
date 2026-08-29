"""Build the v5.6 asset-allocation page snapshot.

The snapshot is a UI/service artefact for the asset-allocation block only.  It
uses the audited v553 four-asset D2 panel and v554/v557 research results, keeps
test/report-period performance labelled as report-only, and never upgrades data
quality to production D3.

Internal asset order is fixed as equity, bond, gold, commodity.  The user's
policy benchmark is therefore 60/15/10/15 in this internal order, while the
chart display benchmark remains four-asset equal weight and is explicitly not
an optimizer input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

from allocation_math_v5 import estimate_statistical_covariance_v5, solve_erc_v5
from backtest_asset_allocation_v541_long import (
    LINEAR_COST_BPS_V541,
    QUADRATIC_COST_V541,
    _drift,
)
from backtest_asset_allocation_v554_long import _simulate_v554, candidate_grid_v554


SCHEMA_V56 = "5.6.0"
ENGINE_V56 = "asset-allocation-v56-cycle-framework-four-models-research"
PANEL_PATH = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_panel_v553.json"
V554_PATH = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_v554_long_research.json"
V557_PATH = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_v557_legacy_direct_research.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data" / "asset_allocation_snapshot.json"

EXPECTED_PANEL_HASH = "815E7181B166EDF859FE59BF56040260564D0CE1E58B3D844DA2B9C6A276439C"
EXPECTED_V554_HASH = "1EFEFB9D98F18B4E6D4CB8B0051B897BED341B1E399B8D478577AB7200D0F376"
EXPECTED_V557_HASH = "B2CBCB5BA16CE9466016D64840F26F2DDD893CADD4AF3209E20E93C9056ABBA5"

ASSET_ORDER = ("equity", "bond", "gold", "commodity")
ASSET_LABELS = {
    "equity": "权益",
    "bond": "国债",
    "gold": "黄金",
    "commodity": "商品",
}
POLICY = np.array([0.60, 0.15, 0.10, 0.15], dtype=float)
DISPLAY_EQUAL = np.array([0.25, 0.25, 0.25, 0.25], dtype=float)
ALL_WEATHER = np.array([0.15, 0.60, 0.10, 0.15], dtype=float)
PRING_STAGE5_MACRO = np.array([0.20, 0.15, 0.30, 0.35], dtype=float)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_inputs(panel: Mapping[str, Any], v554: Mapping[str, Any], v557: Mapping[str, Any]) -> None:
    if tuple(panel.get("asset_order") or ()) != ASSET_ORDER:
        raise ValueError("v56_panel_asset_order_mismatch")
    if panel.get("content_sha256") != EXPECTED_PANEL_HASH:
        raise ValueError("v56_panel_hash_mismatch")
    if v554.get("content_sha256") != EXPECTED_V554_HASH:
        raise ValueError("v56_v554_hash_mismatch")
    if v557.get("content_sha256") != EXPECTED_V557_HASH:
        raise ValueError("v56_v557_hash_mismatch")
    if v554.get("selection_uses_test") is not False or v557.get("selection_uses_test") is not False:
        raise ValueError("v56_selection_must_not_use_test")


def _cost(change: np.ndarray) -> float:
    linear = np.asarray(LINEAR_COST_BPS_V541, dtype=float) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541, dtype=float)
    return float(linear @ np.abs(change) + 0.5 * quadratic @ (change * change))


def _fixed_rows(months: Sequence[str], returns: np.ndarray, weights: Sequence[float]) -> list[dict[str, Any]]:
    target = np.asarray(weights, dtype=float)
    previous = target.copy()
    rows: list[dict[str, Any]] = []
    for signal_index in range(35, len(returns) - 1):
        realized = returns[signal_index + 1]
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(target @ realized) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
            }
        )
        previous = _drift(target, realized)
    return rows


def _rolling_erc_rows(months: Sequence[str], returns: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous: np.ndarray | None = None
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for signal_index in range(35, len(returns) - 1):
        window = returns[signal_index - 35 : signal_index + 1]
        covariance, covariance_diagnostics = estimate_statistical_covariance_v5(
            window,
            half_life=24,
            diagonal_shrinkage=0.35,
        )
        erc = solve_erc_v5(covariance)
        if erc.status != "optimal":
            raise RuntimeError(f"v56_erc_failed:{months[signal_index]}")
        target = np.asarray(erc.weights, dtype=float)
        if previous is None:
            previous = target.copy()
        realized = returns[signal_index + 1]
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(target @ realized) - row_cost,
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
        previous = _drift(target, realized)
    return rows, last


def _bl_abs02_rows(panel: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = next(item for item in candidate_grid_v554() if item["id"] == "V554-ABS-02")
    result = _simulate_v554(panel, spec, allow_test=True)
    rows = [
        {
            "signal_month": str(row.get("signal_month") or ""),
            "month": str(row["month"]),
            "net_return": float(row["net_return"]),
            "turnover": float(row["turnover"]),
            "cost": float(row["cost"]),
        }
        for row in result["returns"]
    ]
    target = ((result.get("metrics") or {}).get("test") or {})
    return rows, {"raw_result_metrics_test": target}


def _sample(month: str) -> str:
    year = str(month)[:4]
    if year in {"2018", "2019"}:
        return "train"
    if year in {"2020", "2021"}:
        return "validation"
    if str(month) >= "202201":
        return "test_report_only"
    return "warmup"


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
        b_ann = float(np.prod(1.0 + benchmark) ** (12.0 / benchmark.size) - 1.0)
        b_vol = float(benchmark.std(ddof=1) * math.sqrt(12.0))
        tracking = float(active.std(ddof=1) * math.sqrt(12.0))
        out.update(
            {
                "benchmark_annual_return": b_ann,
                "benchmark_annual_volatility": b_vol,
                "benchmark_sharpe": float(benchmark.mean() * 12.0 / b_vol) if b_vol > 1.0e-12 else None,
                "annual_excess_return": (1.0 + annual_return) / (1.0 + b_ann) - 1.0,
                "information_ratio": float(active.mean() * 12.0 / tracking) if tracking > 1.0e-12 else None,
                "tracking_error": tracking,
            }
        )
    return out


def _split_metrics(rows: Sequence[Mapping[str, Any]], benchmark: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_month = {str(row["month"]): row for row in rows}
    b_by_month = {str(row["month"]): row for row in benchmark}
    result: dict[str, Any] = {}
    for split in ("train", "validation", "test_report_only", "full"):
        if split == "full":
            months = sorted(set(by_month) & set(b_by_month))
        else:
            months = [month for month in sorted(set(by_month) & set(b_by_month)) if _sample(month) == split]
        result[split] = _metrics([by_month[m] for m in months], [b_by_month[m] for m in months])
    return result


def _annual_rows(rows: Sequence[Mapping[str, Any]], benchmark: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_month = {str(row["month"]): float(row["net_return"]) for row in rows}
    b_by_month = {str(row["month"]): float(row["net_return"]) for row in benchmark}
    out: list[dict[str, Any]] = []
    for year in sorted({month[:4] for month in by_month if month in b_by_month}):
        months = [month for month in sorted(by_month) if month.startswith(year) and month in b_by_month]
        if not months:
            continue
        returns = [by_month[m] for m in months]
        b_returns = [b_by_month[m] for m in months]
        strategy_return = float(np.prod(1.0 + np.asarray(returns)) - 1.0)
        benchmark_return = float(np.prod(1.0 + np.asarray(b_returns)) - 1.0)
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
    policy_active = {asset: float(current_weights[asset] - POLICY[index]) for index, asset in enumerate(ASSET_ORDER)}
    return {
        "key": key,
        "name": name,
        "role": role,
        "governance": governance,
        "construction_steps": list(construction),
        "current_weights": dict(current_weights),
        "active_vs_policy": policy_active,
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
    raw = [
        ("康波", "创新/资本开支", "全球技术扩散、真实利率、资本开支长周期", "Wind/RQ/iFinD待补", "40-60年", "研究展示", "缺独立长样本", "不入权重", "新技术孕育/展示层"),
        ("朱格拉", "设备投资", "制造业投资、产能利用率、企业中长贷、工业利润", "Wind/iFinD优先", "7-11年", "研究展示", "缺release-vintage PIT", "不入权重", "出清-复苏观察"),
        ("基钦", "库存", "工业产成品库存、工业营收、PMI新订单", "Wind/iFinD优先", "3-4年", "研究展示", "缺release-vintage PIT", "不入权重", "去库/补库边界"),
        ("美林", "增长-通胀", "PMI、CPI/PPI、信用、流动性、估值、风险偏好", "Wind/iFinD/RQ交叉", "约2年", "研究展示", "宏观PIT覆盖为0", "不入权重", "滞涨/弱复苏分歧"),
        ("普林格", "三资产顺序", "债券、股票、商品牛熊与相对强弱", "RQ/Tushare执行代理+券商校准", "6阶段", "影子研究", "D3总收益血缘待补", "当前只作研究校准", "第五阶段：滞涨"),
    ]
    rows: list[dict[str, Any]] = []
    for cycle, pillar, factor, source, freq, scope, status, enters, stage in raw:
        rows.append(
            {
                "cycle": cycle,
                "pillar": pillar,
                "factor": factor,
                "source": source,
                "frequency": freq,
                "view_scope": scope,
                "data_status": status,
                "enters_allocation": enters,
                "current_stage": stage,
            }
        )
    return rows


def _cycle_payload() -> dict[str, Any]:
    cycles = [
        {
            "cycle": "康波",
            "current_stage": "研究展示：新技术孕育/长波样本不足",
            "display_probability": 0.25,
            "production_admitted": False,
            "asset_bias": {"equity": 0.0, "bond": 0.0, "gold": 0.0, "commodity": 0.0},
        },
        {
            "cycle": "朱格拉",
            "current_stage": "研究展示：出清-复苏观察",
            "display_probability": 0.40,
            "production_admitted": False,
            "asset_bias": {"equity": 0.0, "bond": 0.0, "gold": 0.0, "commodity": 0.0},
        },
        {
            "cycle": "基钦",
            "current_stage": "研究展示：库存边界",
            "display_probability": 0.38,
            "production_admitted": False,
            "asset_bias": {"equity": 0.0, "bond": 0.0, "gold": 0.0, "commodity": 0.0},
        },
        {
            "cycle": "美林",
            "current_stage": "研究展示：滞涨/弱复苏分歧",
            "display_probability": 0.52,
            "production_admitted": False,
            "asset_bias": {"equity": -0.05, "bond": 0.00, "gold": 0.02, "commodity": 0.03},
        },
        {
            "cycle": "普林格",
            "current_stage": "第五阶段：滞涨",
            "display_probability": 0.86,
            "production_admitted": False,
            "shadow_admitted": True,
            "asset_bias": {"equity": -0.15, "bond": -0.05, "gold": 0.10, "commodity": 0.10},
        },
    ]
    return {
        "current_summary": "普林格按用户指定与最新券商观点校准为第五阶段：滞涨；商品、黄金为当前周期相对占优，权益承压。其余周期因D3/PIT不足仅展示，不进入生产权重。",
        "cycles": cycles,
        "factor_rows": _factor_rows(),
        "production_admitted_cycles": [],
        "shadow_admitted_cycles": ["普林格"],
    }


def build_snapshot() -> dict[str, Any]:
    panel = _read(PANEL_PATH)
    v554 = _read(V554_PATH)
    v557 = _read(V557_PATH)
    _validate_inputs(panel, v554, v557)

    months = [str(item) for item in panel["months"]]
    returns = np.asarray(panel["returns"], dtype=float)
    equal_rows = _fixed_rows(months, returns, DISPLAY_EQUAL)
    policy_rows = _fixed_rows(months, returns, POLICY)
    bl_rows, _bl_last = _bl_abs02_rows(panel)
    risk_parity_rows, risk_parity_last = _rolling_erc_rows(months, returns)
    all_weather_rows = _fixed_rows(months, returns, ALL_WEATHER)
    macro_rows = _fixed_rows(months, returns, PRING_STAGE5_MACRO)

    bl_current = ((v554.get("test_reports_revealed_after_selection") or {}).get("absolute_no_benchmark") or {}).get("current_target") or {}
    bl_weights = bl_current.get("weights") or _latest_weights(bl_rows, [0.11293074667160354, 0.6278960324157017, 0.0500000721796437, 0.20917314873305098])

    strategies = {
        "black_litterman": _strategy_payload(
            "black_litterman",
            "BL（无基准高夏普）",
            bl_rows,
            equal_rows,
            {asset: float(bl_weights[asset]) for asset in ASSET_ORDER},
            role="Sharpe/回撤优秀的无基准研究候选",
            construction=[
                "政策/风险预算先验与稳健协方差",
                "Black-Litterman后验收益与完整Omega",
                "成本、换手、长仓和风险约束优化",
                "训练/验证选择，2022+仅报告",
            ],
            governance="v554 ABS-02; deployment_allowed=false; D3/Wind交叉验证未完成",
        ),
        "risk_parity": _strategy_payload(
            "risk_parity",
            "风险平价（严格ERC）",
            risk_parity_rows,
            equal_rows,
            _latest_weights(risk_parity_rows, risk_parity_last.get("weights") or DISPLAY_EQUAL),
            role="全样本Sharpe最高的独立风险控制模型",
            construction=[
                "36月滚动收益窗口",
                "EW/对角收缩/PSD稳健协方差",
                "Newton严格ERC，四资产风险贡献各25%",
                "按漂移持仓扣同口径交易成本",
            ],
            governance="独立诊断模型；不使用宏观PIT缺失因子",
        ),
        "all_weather": _strategy_payload(
            "all_weather",
            "全天候（固定防守袖套）",
            all_weather_rows,
            equal_rows,
            {asset: float(ALL_WEATHER[index]) for index, asset in enumerate(ASSET_ORDER)},
            role="低回撤防守型配置",
            construction=[
                "权益15%、国债60%、黄金10%、商品15%",
                "月度再平衡并按漂移持仓计成本",
                "不读测试期调参",
                "作为全天候基线与风险平价/BL对照",
            ],
            governance="固定规则，不作生产晋级声明",
        ),
        "macro_factor": _strategy_payload(
            "macro_factor",
            "宏观因子（普林格第五阶段滞涨映射）",
            macro_rows,
            equal_rows,
            {asset: float(PRING_STAGE5_MACRO[index]) for index, asset in enumerate(ASSET_ORDER)},
            role="当前周期最贴近券商滞涨判断、相对等权有正超额的影子策略",
            construction=[
                "普林格当前阶段校准为第五阶段：滞涨",
                "权益/国债降配，黄金/商品升配",
                "K/J/M/康波因PIT或样本不足不直接入权重",
                "后续D3宏观库完成后再替换为正式宏观因子BL观点",
            ],
            governance="research shadow; not D3 production; current mapping E/B/G/C=20/15/30/35",
        ),
    }

    full_sharpes = {key: float(value["metrics"]["full"].get("sharpe") or -999.0) for key, value in strategies.items()}
    full_excess = {key: float(value["metrics"]["full"].get("annual_excess_return") or -999.0) for key, value in strategies.items()}

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_V56,
        "engine_version": ENGINE_V56,
        "generated_at": "2026-08-14",
        "asset_order": list(ASSET_ORDER),
        "asset_labels": ASSET_LABELS,
        "data_as_of": {"market_month": months[-1], "macro_pit": "not_admitted"},
        "policy_benchmark": {
            "id": "policy_60_15_10_15",
            "weights_internal_equity_bond_gold_commodity": POLICY.tolist(),
            "display_cn": "权益60% + 国债15% + 商品15% + 黄金10%（内部顺序为权益/国债/黄金/商品）",
            "optimizer_anchor_for_relative_models": True,
        },
        "display_benchmark": {
            "id": "equal_weight_25",
            "weights_internal_equity_bond_gold_commodity": DISPLAY_EQUAL.tolist(),
            "role": "chart_display_only_not_optimizer_input",
            "optimizer_input": False,
            "active_return_reference": False,
        },
        "data_quality": {
            "status": "D2_research_not_D3",
            "deployment_allowed": False,
            "blocking_items": [
                "Wind/iFinD/RQ四资产D3交叉验证未闭环",
                "宏观因子缺release_time/available_time/vintage/revision PIT",
                "2022+区间已被观察，只能报告不能再用于调参",
            ],
        },
        "cycle_tracking": _cycle_payload(),
        "allocation_models": strategies,
        "recommended": {
            "primary_model": "risk_parity",
            "reason": "全样本Sharpe最高且回撤最低；若按当前普林格第五阶段滞涨观点看，macro_factor是周期一致性更强的影子方案。",
            "sharpe_champion": max(full_sharpes, key=full_sharpes.get),
            "excess_champion_vs_equal_display": max(full_excess, key=full_excess.get),
            "current_cycle_aligned_model": "macro_factor",
        },
        "benchmarks": {
            "equal_weight_25": _strategy_payload(
                "equal_weight_25",
                "四资产等权（仅展示基准）",
                equal_rows,
                equal_rows,
                {asset: 0.25 for asset in ASSET_ORDER},
                role="display benchmark only",
                construction=["仅用于净值图和相对强弱展示", "不进入优化器、BL先验或主动收益选择"],
                governance="chart_display_only",
            ),
            "policy_60_15_10_15": _strategy_payload(
                "policy_60_15_10_15",
                "政策基准",
                policy_rows,
                equal_rows,
                {asset: float(POLICY[index]) for index, asset in enumerate(ASSET_ORDER)},
                role="relative optimizer anchor",
                construction=["用户指定60/15/15/10基准", "内部顺序权益/国债/黄金/商品"],
                governance="optimizer_anchor_for_relative_versions",
            ),
        },
        "references": [
            {
                "name": "skfolio: Black-Litterman, RiskBudgeting, Benchmark tracking, walk-forward/CPCV",
                "url": "https://skfolio.org/",
                "usage": "统一BL、风险预算、基准跟踪和滚动验证的工程参考",
            },
            {
                "name": "PyPortfolioOpt Black-Litterman documentation",
                "url": "https://pyportfolioopt.readthedocs.io/en/stable/BlackLitterman.html",
                "usage": "BL隐含均衡收益、P/Q/Omega后验公式交叉参考",
            },
            {
                "name": "Riskfolio-Lib",
                "url": "https://riskfolio-lib.readthedocs.io/",
                "usage": "风险平价/风险预算求解口径交叉参考",
            },
            {
                "name": "中信建投资产因子框架（2026-06附近公开转引）",
                "url": "https://finance.sina.com.cn/wm/2026-06-03/doc-iniaarqw2591595.shtml",
                "usage": "当前普林格第五阶段/滞涨配置方向研究校准；正式数据仍待原文和D3数据落库",
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
