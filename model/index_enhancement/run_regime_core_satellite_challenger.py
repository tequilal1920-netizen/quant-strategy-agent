"""Evaluate causal Bayesian core-satellite CSI 800 enhancement candidates.

This runner is intentionally database read-only.  Candidate selection uses the
training and validation intervals only.  The already-observed test interval is
retained as report-only diagnostic evidence and cannot promote a model.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKTEST_PATH = PROJECT_ROOT / "framework" / "backtest" / "run_v2_models.py"
UNIVERSE = "CSI800_ENH"

from framework.backtest.index_regime_core_satellite import (  # noqa: E402
    BayesianAlphaConfig,
    CoreSatelliteConfig,
    add_bayesian_regime_alpha,
    backtest_core_satellite,
)


CANDIDATES: dict[str, dict[str, Any]] = {
    "index_bayesian_core_satellite_v15": {
        "mandate": "稳健核心卫星",
        "alpha": BayesianAlphaConfig(),
        "portfolio": CoreSatelliteConfig(
            target_tracking_error=0.030,
            max_active_weight=0.005,
            max_industry_deviation=0.008,
            turnover_penalty=7.0,
            covariance_shrinkage=0.65,
        ),
    },
    "index_bayesian_stability_core_v16": {
        "mandate": "慢变量稳定增强",
        "alpha": BayesianAlphaConfig(
            horizons=(12, 24, 48, 72),
            horizon_weights=(0.32, 0.30, 0.24, 0.14),
            prior_strength=10.0,
            covariance_lookback=48,
            covariance_shrinkage=0.65,
            factor_transition_penalty=4.0,
        ),
        "portfolio": CoreSatelliteConfig(
            target_tracking_error=0.035,
            max_active_weight=0.006,
            max_industry_deviation=0.010,
            turnover_penalty=6.0,
            covariance_shrinkage=0.60,
        ),
    },
    "index_bayesian_responsive_satellite_v17": {
        "mandate": "快慢因子响应增强",
        "alpha": BayesianAlphaConfig(
            horizons=(6, 12, 24, 36),
            horizon_weights=(0.42, 0.30, 0.18, 0.10),
            prior_strength=7.0,
            covariance_lookback=30,
            covariance_shrinkage=0.50,
            factor_transition_penalty=1.5,
        ),
        "portfolio": CoreSatelliteConfig(
            target_tracking_error=0.040,
            max_active_weight=0.007,
            max_industry_deviation=0.015,
            turnover_penalty=4.0,
            covariance_shrinkage=0.50,
        ),
    },
}


def _load_backtest_module() -> Any:
    spec = importlib.util.spec_from_file_location("formal_model_backtest", BACKTEST_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load formal backtest module: {BACKTEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _relative_max_drawdown(returns: list[float], benchmark: list[float]) -> float:
    relative_nav = 1.0
    peak = 1.0
    drawdown = 0.0
    for strategy_return, benchmark_return in zip(returns, benchmark):
        denominator = 1.0 + benchmark_return
        if denominator <= 0.0:
            continue
        relative_nav *= (1.0 + strategy_return) / denominator
        peak = max(peak, relative_nav)
        drawdown = min(drawdown, relative_nav / peak - 1.0)
    return drawdown


def _metric_block(
    formal: Any,
    returns: list[float],
    benchmark: list[float],
) -> dict[str, Any]:
    metrics = formal.metrics_from_returns(returns, benchmark)
    excess = np.asarray(returns, dtype=float) - np.asarray(benchmark, dtype=float)
    metrics.update(
        {
            "relative_max_drawdown": _relative_max_drawdown(returns, benchmark),
            "tracking_error": float(np.std(excess, ddof=1) * math.sqrt(12.0))
            if len(excess) > 1
            else 0.0,
            "active_win_rate": float(np.mean(excess > 0.0)) if len(excess) else 0.0,
            "average_monthly_excess": float(np.mean(excess)) if len(excess) else 0.0,
        }
    )
    return metrics


def _split_metrics(formal: Any, nav_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for split, (start, end) in formal.SPLITS.items():
        rows = [row for row in nav_rows if start <= str(row["trade_date"]) <= end]
        output[split] = _metric_block(
            formal,
            [float(row["period_return"]) for row in rows],
            [float(row["benchmark_return"]) for row in rows],
        )
    return output


def _yearly_metrics(formal: Any, nav_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    years = sorted({str(row["trade_date"])[:4] for row in nav_rows})
    output: list[dict[str, Any]] = []
    for year in years:
        rows = [row for row in nav_rows if str(row["trade_date"]).startswith(year)]
        metrics = _metric_block(
            formal,
            [float(row["period_return"]) for row in rows],
            [float(row["benchmark_return"]) for row in rows],
        )
        output.append({"year": year, **metrics})
    return output


def _selection_score(split_metrics: dict[str, dict[str, Any]], evidence: dict[str, Any]) -> float:
    train = split_metrics["train"]
    validation = split_metrics["valid"]
    train_ir = float(train.get("information_ratio") or 0.0)
    validation_ir = float(validation.get("information_ratio") or 0.0)
    train_excess = float(train.get("excess_annual_return") or 0.0)
    validation_excess = float(validation.get("excess_annual_return") or 0.0)
    transition_cost = float(evidence.get("average_one_way_turnover") or 0.0)
    relative_drawdown = abs(float(validation.get("relative_max_drawdown") or 0.0))
    return (
        0.25 * train_ir
        + 0.40 * validation_ir
        + 0.15 * min(train_ir, validation_ir)
        + 0.08 * 10.0 * train_excess
        + 0.12 * 10.0 * validation_excess
        - 0.10 * transition_cost
        - 0.15 * relative_drawdown
    )


def _cost_sensitivity(
    formal: Any,
    nav_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    benchmark = [float(row["benchmark_return"]) for row in nav_rows]
    for cost_bps in (5, 10, 15, 20, 30):
        rate = cost_bps / 10000.0
        returns = [
            float(row["gross_return"]) - float(row["two_way_turnover"]) * rate
            for row in nav_rows
        ]
        metrics = _metric_block(formal, returns, benchmark)
        output.append({"cost_bps": cost_bps, **metrics})
    return output


def _compact_monthly_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "trade_date": row.get("trade_date"),
            "alpha_confidence": row.get("alpha_confidence"),
            "active_share": row.get("active_share"),
            "estimated_tracking_error": row.get("estimated_tracking_error"),
            "one_way_turnover": row.get("one_way_turnover"),
            "max_industry_deviation": row.get("max_industry_deviation"),
            "max_style_exposure": row.get("max_style_exposure"),
            "gross_return": row.get("gross_return"),
            "benchmark_return": row.get("benchmark_return"),
            "net_return": row.get("net_return"),
            "excess_return": row.get("excess_return"),
            "transaction_cost": row.get("transaction_cost"),
        }
        for row in rows
    ]


def run(db: Path, out_dir: Path) -> dict[str, Any]:
    formal = _load_backtest_module()
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    connection.execute("pragma busy_timeout=120000")
    try:
        miner = formal.load_factor_miner(PROJECT_ROOT)
        panel, reason = formal.build_stock_panel(
            connection,
            miner,
            UNIVERSE,
            formal.START_DATE,
            formal.END_DATE,
        )
        if panel is None:
            raise RuntimeError(reason or "point-in-time panel is unavailable")
        panel, static_diagnostics = formal.add_ic_learned_alpha(panel)
        panel, walkforward_diagnostics = formal.add_walkforward_ic_alpha(panel)
        panel = formal.add_v11_alpha_scores(panel)
    finally:
        connection.close()

    evaluations: list[dict[str, Any]] = []
    detailed: dict[str, dict[str, Any]] = {}
    for model_name, specification in CANDIDATES.items():
        candidate_panel, alpha_diagnostics = add_bayesian_regime_alpha(
            panel,
            specification["alpha"],
            score_column=f"{model_name}_score",
            confidence_column=f"{model_name}_confidence",
        )
        returns, benchmark, nav_rows, signal_rows, evidence = backtest_core_satellite(
            candidate_panel,
            f"{model_name}_score",
            f"{model_name}_confidence",
            cost_rate=formal.COST_RATE,
            config=specification["portfolio"],
            safe_float=formal.safe_float,
        )
        split_metrics = _split_metrics(formal, nav_rows)
        selection_score = _selection_score(split_metrics, evidence)
        summary = {
            "model": model_name,
            "mandate": specification["mandate"],
            "selection_score": selection_score,
            "selection_uses_test": False,
            "research_status": "post_test_diagnostic_candidate",
            "promotion_eligible": False,
            "split_metrics": split_metrics,
            "average_tracking_error": evidence["average_tracking_error"],
            "average_one_way_turnover": evidence["average_one_way_turnover"],
            "average_active_share": evidence["average_active_share"],
            "average_alpha_confidence": evidence["average_alpha_confidence"],
            "max_industry_deviation": evidence["max_industry_deviation"],
            "max_style_exposure": evidence["max_style_exposure"],
        }
        evaluations.append(summary)
        detailed[model_name] = {
            "summary": summary,
            "alpha_diagnostics": alpha_diagnostics,
            "portfolio_evidence": {
                **evidence,
                "monthly_evidence": _compact_monthly_evidence(
                    evidence["monthly_evidence"]
                ),
            },
            "nav": nav_rows,
            "signal_count": len(signal_rows),
            "yearly_metrics": _yearly_metrics(formal, nav_rows),
        }

    evaluations.sort(key=lambda item: float(item["selection_score"]), reverse=True)
    selected_model = str(evaluations[0]["model"])
    selected = detailed[selected_model]
    selected["cost_sensitivity"] = _cost_sensitivity(formal, selected["nav"])
    payload = {
        "status": "ready",
        "engine_version": "index-enhancement/1.3-bayesian-core-satellite-audit",
        "data_as_of": str(panel["trade_date"].max()),
        "universe": UNIVERSE,
        "selected_candidate": selected_model,
        "selection_rule": {
            "uses": ["train", "valid"],
            "excludes": ["test"],
            "test_policy": "封存测试只报告，不参与候选排序或参数选择",
            "promotion_eligible": False,
            "reason": "该结构是在观察2023至2026年失效后提出，只能作为后验诊断候选",
        },
        "root_cause_audit": [
            {
                "cause": "基准口径错配",
                "evidence": "旧冠军采用成分股等权均值作为比较基准，未按指数权重复制中证800",
                "repair": "全程以信号日指数权重为核心仓位，收益与主动风险均相对同一基准计算",
            },
            {
                "cause": "基准贝塔缺失",
                "evidence": "旧模型集中持有少量股票，强势指数阶段出现系统性跟涨不足",
                "repair": "取消绝对仓位择时，组合始终满仓，主动视图只作为有限卫星偏离",
            },
            {
                "cause": "长窗口掩盖因子衰减",
                "evidence": "2023年高IC抬高长窗口均值，2025至2026年近期IC恶化未及时降低风险预算",
                "repair": "使用6至72个月多期限经验贝叶斯后验，证据转弱时连续收缩主动预算",
            },
            {
                "cause": "行业完全中性损失有效信息",
                "evidence": "旧主动风险优化器把每个行业权重强制还原至基准",
                "repair": "保留风格残差化，行业改为软偏离预算并纳入跟踪误差约束",
            },
        ],
        "research_basis": [
            {
                "title": "海通证券：风控模型还有必要吗",
                "url": "https://www.htsec.com/jfimg/colimg/upload/20230821/1692578595183004217.pdf",
                "application": "比较严格暴露控制与保留有效主动暴露的收益和跟踪误差权衡",
            },
            {
                "title": "华宝证券：主动暴露的得与失",
                "url": "https://pdf.dfcfw.com/pdf/H301_AP202211301580682867_1.pdf",
                "application": "把风格暴露收益和行业约束损失分开归因并采用动态风险预算",
            },
            {
                "title": "东北证券：可转债风险模型构建与应用",
                "url": "https://www.nesc.cn/timerfiles/upload/report/2024/01/29/15813745.pdf",
                "application": "借鉴EWMA、协方差收缩与风险模型叠加Alpha的完整链路",
            },
            {
                "title": "西南证券：智慧迭代Alpha模型，精准风控引领稳健收益",
                "url": "https://pdf.dfcfw.com/pdf/H301_AP202406201636704562_1.pdf",
                "application": "采用动态因子权重、低换手组合和跟踪误差约束的联合框架",
            },
        ],
        "candidate_evaluations": evaluations,
        "selected": selected,
        "legacy_alpha_diagnostics": {
            "static_ic": static_diagnostics,
            "walkforward_ic": {
                "lookback": walkforward_diagnostics.get("lookback"),
                "min_obs": walkforward_diagnostics.get("min_obs"),
                "fallback_count": walkforward_diagnostics.get("fallback_count"),
                "latest_weights": walkforward_diagnostics.get("latest_weights"),
            },
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "index_regime_core_satellite_diagnostics.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "database" / "research_warehouse.db",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "model_improvement" / "index_enhancement",
    )
    args = parser.parse_args()
    payload = run(args.db, args.out_dir)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_candidate": payload["selected_candidate"],
                "candidate_evaluations": payload["candidate_evaluations"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
