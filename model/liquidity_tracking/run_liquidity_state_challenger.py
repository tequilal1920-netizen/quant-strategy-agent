"""Run the exact-series liquidity-state challenger in database read-only mode."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from framework.backtest.liquidity_state_allocator import (  # noqa: E402
    AllocatorConfig,
    EXCLUDED_CONTRACT_SERIES,
    SERIES_SPECS,
    backtest_allocator,
    backtest_monthly_cash_overlay,
    build_causal_feature_panel,
    compact_backtest,
    conditional_return_table,
    cost_sensitivity,
    cost_sensitivity_monthly_cash,
    deflated_sharpe_confidence,
    fit_hierarchical_evidence_model,
    forward_compound_return,
    forward_downside_safety,
    metrics,
    residualize_market_response,
    selection_score,
    split_metrics,
    walkforward_hierarchical_evidence_model,
)


START_DATE = "2012-01-01"
END_DATE = "2026-06-30"
CASH_SNAPSHOT = (
    PROJECT_ROOT
    / "output"
    / "model_improvement"
    / "asset_macro_risk_audit_v46_20260801.json"
)


SPLITS = {
    "train": ("2012-01-01", "2020-12-31"),
    "valid": ("2021-01-01", "2022-12-31"),
    "test": ("2023-01-01", "2026-06-30"),
    "full": ("2012-01-01", "2026-06-30"),
}


CANDIDATES: tuple[AllocatorConfig, ...] = (
    AllocatorConfig(
        name="liquidity_fast_posterior_v1",
        label="短期资金后验",
        feature_mode="fast",
        target_horizon_weeks=4,
        exposure_floor=0.10,
        exposure_bias=0.55,
        exposure_slope=0.95,
        target_volatility=0.18,
    ),
    AllocatorConfig(
        name="liquidity_balanced_posterior_v2",
        label="多期限资金后验",
        feature_mode="balanced",
        target_horizon_weeks=4,
        exposure_floor=0.10,
        exposure_bias=0.65,
        exposure_slope=0.90,
        target_volatility=0.18,
    ),
    AllocatorConfig(
        name="liquidity_slow_posterior_v3",
        label="中期资金后验",
        feature_mode="slow",
        target_horizon_weeks=13,
        exposure_floor=0.15,
        exposure_bias=0.75,
        exposure_slope=0.75,
        target_volatility=0.18,
    ),
    AllocatorConfig(
        name="liquidity_drawdown_budget_v4",
        label="资金状态风险预算",
        feature_mode="balanced",
        target_horizon_weeks=13,
        exposure_floor=0.05,
        exposure_bias=0.45,
        exposure_slope=1.05,
        target_volatility=0.16,
    ),
    AllocatorConfig(
        name="liquidity_endogeneity_crowding_v5",
        label="\u8d44\u91d1\u5185\u751f\u6027\u4e0e\u62e5\u6324\u4fee\u6b63",
        feature_mode="balanced",
        target_horizon_weeks=4,
        exposure_floor=0.00,
        exposure_bias=0.15,
        exposure_slope=1.25,
        target_volatility=0.16,
        market_residualization=True,
        crowding_penalty=0.90,
        crowding_center=0.80,
    ),
    AllocatorConfig(
        name="liquidity_downside_safety_v6",
        label="\u8d44\u91d1\u9762\u4e0b\u884c\u5b89\u5168\u540e\u9a8c",
        feature_mode="balanced",
        target_horizon_weeks=13,
        target_objective="downside_safety",
        exposure_floor=0.00,
        exposure_bias=0.30,
        exposure_slope=1.10,
        target_volatility=0.15,
        lookback_weeks=260,
        minimum_history_weeks=156,
    ),
    AllocatorConfig(
        name="liquidity_monthly_investable_cash_v9",
        label="月末资金后验与货币ETF",
        feature_mode="balanced",
        target_horizon_weeks=13,
        exposure_floor=0.05,
        exposure_bias=0.45,
        exposure_slope=1.05,
        target_volatility=0.16,
        rebalance_frequency="monthly",
        defensive_asset="511880.SH",
    ),
)


REPORT_BASIS = [
    {
        "title": "中国银河证券：结合价格动量和拥挤度的两融ETF交易策略探索",
        "url": "https://bigdata-s3.wmcloud.com/researchreport/2023-05/b21b963f27cfc886ec8759b2b4d7df6b.pdf",
        "application": "采用月度ETF配置和真实现金管理收益口径，避免把未配置权益部分错误记为零收益。",
    },
    {
        "title": "开源证券：权益择时的多策略框架，从宏观驱动到微观验证",
        "url": "https://www.sdyanbao.com/detail/938712",
        "application": "把宏观流动性、跨境资金、融资融券和大小单资金分层建模，再以动态证据权重合成；本模型只复现可由现有精确序列支持的部分。",
    },
    {
        "title": "开源证券：宏观择时，多维度结合下的新视角",
        "url": "https://bigdata-s3.wmcloud.com/researchreport/2022-02/b41b557d1a38f38ebdd1ec2d4de24f7f.pdf",
        "application": "借鉴多维信号交叉验证和组合层风险预算，不以单一流动性指标决定仓位。",
    },
    {
        "title": "开源证券：新型因子，资金流动力学与散户羊群效应",
        "url": "https://bigdata-s3.wmcloud.com/researchreport/2022-06/e55eef41a43f49f1d9e85aa0df5d4c86.pdf",
        "application": "资金流使用变化与相关结构，不直接把同步净流入解释成未来收益；散户信号方向由训练期证据确定。",
    },
    {
        "title": "开源证券：大小单资金流 alpha 探究 2.0，变量精筛与高频测算",
        "url": "https://asset.quant-wiki.com/pdf/20221218-%E5%BC%80%E6%BA%90%E8%AF%81%E5%88%B8-%E5%B8%82%E5%9C%BA%E5%BE%AE%E8%A7%82%E7%BB%93%E6%9E%84%E7%A0%94%E7%A9%B6%E7%B3%BB%E5%88%97%EF%BC%8818%EF%BC%89%EF%BC%9A%E5%A4%A7%E5%B0%8F%E5%8D%95%E8%B5%84%E9%87%91%E6%B5%81alpha%E6%8E%A2%E7%A9%B62.0%EF%BC%8C%E5%8F%98%E9%87%8F%E7%B2%BE%E7%AD%9B%E4%B8%8E%E9%AB%98%E9%A2%91%E6%B5%8B%E7%AE%97.pdf",
        "application": "采用稳健尺度、分阶段方向一致性和连续收缩权重，弱化样本偶然性。",
    },
    {
        "title": "海通证券：25 年能否迎来流动性牛市",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202412301641463922_1.pdf",
        "application": "把长期资金、ETF、杠杆、散户和公募拆开复盘，区分存量仓位、边际流量与供给压力。",
    },
    {
        "title": "广发证券：多因子 GFTD 体系与基金仓位、ETF 资金跟踪",
        "url": "https://bigdata-s3.wmcloud.com/researchreport/2023-06/b3c7429b14e7536b5f9b07aca4ede5e8.pdf",
        "application": "公募仓位和 ETF 份额按真实类别与规模聚合，模型只使用下一期可见值。",
    },
]


def _read_cash_total_return() -> tuple[pd.Series, dict[str, Any]]:
    snapshot = json.loads(CASH_SNAPSHOT.read_text(encoding="utf-8"))
    proxy = (snapshot.get("asset_proxies") or {}).get("cash") or {}
    if proxy.get("ts_code") != "511880.SH":
        raise RuntimeError("cash_proxy_must_be_511880.SH")
    rows = ((snapshot.get("monthly_prices") or {}).get("cash") or [])
    levels = pd.Series(
        [float(row["close"]) for row in rows],
        index=pd.PeriodIndex([str(row["month"]) for row in rows], freq="M"),
        name="银华日利ETF",
    ).sort_index()
    if len(levels) < 100 or levels.index.has_duplicates or not levels.index.is_monotonic_increasing:
        raise RuntimeError("cash_total_return_history_invalid")
    return levels, {
        **proxy,
        "snapshot": str(CASH_SNAPSHOT.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "observations": int(len(levels)),
        "first_month": str(levels.index.min()),
        "last_month": str(levels.index.max()),
    }


def _read_exact_data(connection: sqlite3.Connection) -> tuple[pd.DataFrame, pd.Series]:
    identifiers = [spec.series_id for spec in SERIES_SPECS]
    placeholders = ",".join("?" for _ in identifiers)
    raw = pd.read_sql_query(
        f"""
        select series_id, observation_date, value, provider, locator, retrieved_at
        from observations
        where series_id in ({placeholders})
          and observation_date <= ?
        order by observation_date, series_id
        """,
        connection,
        params=[*identifiers, END_DATE],
    )
    benchmark = pd.read_sql_query(
        """
        select observation_date, value
        from observations
        where series_id='foreign.sse_index' and observation_date <= ?
        order by observation_date
        """,
        connection,
        params=[END_DATE],
    )
    benchmark["date"] = pd.to_datetime(benchmark["observation_date"])
    weekly_close = benchmark.set_index("date")["value"].astype(float).resample("W-FRI").last()
    weekly_returns = weekly_close.pct_change(fill_method=None).loc[START_DATE:END_DATE]
    weekly_returns.name = "上证综指"
    return raw, weekly_returns


def _contract_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    contracts = pd.read_sql_query(
        "select series_id, contract_json from series_contracts order by series_id",
        connection,
    )
    available = set(
        pd.read_sql_query("select distinct series_id from observations", connection)["series_id"]
    )
    exact_inputs = [spec.series_id for spec in SERIES_SPECS if spec.series_id in available]
    unavailable = []
    for row in contracts.itertuples(index=False):
        contract = json.loads(row.contract_json)
        if contract.get("availability") != "ready":
            unavailable.append(
                {
                    "series_id": row.series_id,
                    "label": contract.get("display_name"),
                    "provider": contract.get("preferred_provider"),
                    "reason": contract.get("unavailable_reason"),
                }
            )
    return {
        "database_series_count": len(available),
        "exact_model_input_count": len(exact_inputs),
        "exact_model_inputs": exact_inputs,
        "contract_excluded_count": len(EXCLUDED_CONTRACT_SERIES),
        "contract_excluded": list(EXCLUDED_CONTRACT_SERIES),
        "unavailable_contracts": unavailable,
        "production_snapshot_used_for_model": False,
        "read_only": True,
    }


def _fit_mask(index: pd.Index, horizon: int) -> pd.Series:
    start = pd.Timestamp(SPLITS["train"][0])
    end = pd.Timestamp(SPLITS["train"][1]) - pd.Timedelta(weeks=horizon)
    return pd.Series((index >= start) & (index <= end), index=index)


def _split_mask(index: pd.Index, names: tuple[str, ...]) -> pd.Series:
    mask = pd.Series(False, index=index)
    for name in names:
        start, end = SPLITS[name]
        mask |= (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    return mask


def _yearly_metrics(
    backtest: pd.DataFrame,
    periods_per_year: float = 52.0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, subset in backtest.groupby(backtest.index.year):
        rows.append(
            {
                "year": int(year),
                **metrics(
                    subset["strategy_return"],
                    subset["benchmark_return"],
                    periods_per_year=periods_per_year,
                ),
                "average_exposure": float(subset["equity_exposure"].mean()),
            }
        )
    return rows


def _compact_diagnostics(model: dict[str, Any]) -> dict[str, Any]:
    feature_rows = []
    for feature, diagnostic in model["feature_diagnostics"].items():
        feature_rows.append({"feature": feature, **diagnostic})
    feature_rows.sort(key=lambda row: abs(float(row.get("weight") or 0.0)), reverse=True)
    group_rows = [
        {"group": group, **diagnostic}
        for group, diagnostic in model["group_diagnostics"].items()
    ]
    group_rows.sort(key=lambda row: abs(float(row.get("weight") or 0.0)), reverse=True)
    return {
        "feature_weights": feature_rows,
        "group_weights": group_rows,
        "group_members": model["group_members"],
        "calibration": model["calibration"],
    }


def _latest_group_state(model: dict[str, Any]) -> list[dict[str, Any]]:
    groups: pd.DataFrame = model["group_scores"]
    latest = groups.dropna(how="all").iloc[-1]
    rows = []
    for group, value in latest.items():
        diagnostic = model["group_diagnostics"].get(group) or {}
        weight = float(diagnostic.get("weight") or 0.0)
        rows.append(
            {
                "group": group,
                "state": float(value),
                "posterior_weight": weight,
                "contribution": float(value) * weight,
                "direction_confidence": float(
                    diagnostic.get("posterior_direction_confidence") or 0.5
                ),
            }
        )
    rows.sort(key=lambda row: abs(row["contribution"]), reverse=True)
    return rows


def run(database: Path, output_dir: Path) -> dict[str, Any]:
    before = database.stat()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=120)
    connection.execute("pragma query_only=on")
    try:
        raw, weekly_returns = _read_exact_data(connection)
        audit = _contract_audit(connection)
    finally:
        connection.close()
    cash_levels, cash_audit = _read_cash_total_return()
    comparison_start = cash_levels.index.min().to_timestamp("M")
    after = database.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("liquidity database changed during read-only research run")

    features, registry = build_causal_feature_panel(raw)
    features = features.reindex(weekly_returns.index)
    residual_features = residualize_market_response(features, weekly_returns)
    evaluations: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    for config in CANDIDATES:
        if config.target_objective == "downside_safety":
            target = forward_downside_safety(weekly_returns, config.target_horizon_weeks)
        else:
            target = forward_compound_return(weekly_returns, config.target_horizon_weeks)
        fit_mask = _fit_mask(features.index, config.target_horizon_weeks)
        candidate_features = residual_features if config.market_residualization else features
        if config.weighting_mode == "rolling_posterior":
            model = walkforward_hierarchical_evidence_model(
                candidate_features,
                registry,
                target,
                config.target_horizon_weeks,
                config.feature_mode,
                config.lookback_weeks,
                config.minimum_history_weeks,
                config.refit_weeks,
            )
        else:
            model = fit_hierarchical_evidence_model(
                candidate_features,
                registry,
                target,
                fit_mask,
                config.feature_mode,
            )
        if config.rebalance_frequency == "monthly":
            backtest = backtest_monthly_cash_overlay(
                model["signal"], weekly_returns, cash_levels, config
            )
            periods_per_year = 12.0
        else:
            backtest = backtest_allocator(model["signal"], weekly_returns, config)
            periods_per_year = 52.0
        backtest = backtest.loc[comparison_start:]
        by_split = split_metrics(
            backtest,
            SPLITS,
            periods_per_year=periods_per_year,
        )
        score = selection_score(by_split)
        evaluation = {
            "model": config.name,
            "label": config.label,
            "selection_score": score,
            "selection_uses_test": False,
            "promotion_eligible": False,
            "research_status": "post_test_diagnostic_candidate",
            "split_metrics": by_split,
            "config": asdict(config),
        }
        evaluations.append(evaluation)
        details[config.name] = {
            "evaluation": evaluation,
            "model_diagnostics": _compact_diagnostics(model),
            "latest_group_state": _latest_group_state(model),
            "latest_signal": float(model["signal"].dropna().iloc[-1]),
            "backtest": backtest,
            "signal": model["signal"],
            "yearly_metrics": _yearly_metrics(backtest, periods_per_year),
            "periods_per_year": periods_per_year,
        }

    evaluations.sort(key=lambda row: float(row["selection_score"]), reverse=True)
    selected_name = str(evaluations[0]["model"])
    selected_config = next(config for config in CANDIDATES if config.name == selected_name)
    selected = details[selected_name]
    backtest = selected.pop("backtest")
    signal = selected.pop("signal")
    periods_per_year = float(selected.pop("periods_per_year"))
    train_valid_mask = _split_mask(backtest.index, ("train", "valid"))
    test_mask = _split_mask(backtest.index, ("test",))
    selected.update(
        {
            "conditional_returns_train_validation": conditional_return_table(
                backtest, train_valid_mask
            ),
            "conditional_returns_test_report_only": conditional_return_table(backtest, test_mask),
            "cost_sensitivity": (
                cost_sensitivity_monthly_cash(
                    signal, weekly_returns, cash_levels, selected_config
                )
                if selected_config.rebalance_frequency == "monthly"
                else cost_sensitivity(signal, weekly_returns, selected_config)
            ),
            "deflated_sharpe_train_validation": deflated_sharpe_confidence(
                backtest.loc[train_valid_mask, "strategy_return"],
                len(CANDIDATES),
                periods_per_year=periods_per_year,
            ),
            "nav": compact_backtest(backtest),
        }
    )

    payload = {
        "status": "ready",
        "engine_version": "liquidity-state/1.1-investable-cash-monthly",
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "data_as_of": str(raw["observation_date"].max()),
        "benchmark": "上证综指",
        "defensive_asset": "银华日利ETF 511880.SH累计分红总收益",
        "selected_candidate": selected_name,
        "selection_rule": {
            "uses": ["train", "valid"],
            "excludes": ["test"],
            "train": SPLITS["train"],
            "validation": SPLITS["valid"],
            "test_report_only": SPLITS["test"],
            "candidate_count": len(CANDIDATES),
            "common_comparison_start": comparison_start.strftime("%Y-%m-%d"),
            "frequency_policy": "周频资金后验，月末执行；频率差异按各自年化口径比较",
            "promotion_eligible": False,
            "reason": "模型在已观察测试期之后开发，测试结果仅用于诊断，不得据此晋级生产。",
        },
        "data_audit": {
            **audit,
            "database_size_before": before.st_size,
            "database_size_after": after.st_size,
            "database_mtime_ns_before": before.st_mtime_ns,
            "database_mtime_ns_after": after.st_mtime_ns,
            "feature_count": len(features.columns),
            "feature_registry": registry,
            "investable_cash_proxy": cash_audit,
            "investable_cash_snapshot_used": True,
        },
        "root_cause_audit": [
            {
                "cause": "防御仓位收益口径缺失",
                "evidence": "旧回测把非权益仓位收益固定为零，低估真实现金管理收益并放大周频调仓成本。",
                "repair": "沿用原资金后验，在月末执行；剩余仓位配置已验证的511880.SH累计分红总收益。",
            },
            {
                "cause": "旧页面把监测完整度等同于模型完整度",
                "evidence": "37张图只检查来源和日期交集，没有训练、验证、封存测试或收益归因。",
                "repair": "保留生产监测，新增独立资金状态模型和训练验证测试证据。",
            },
            {
                "cause": "旧快照含正式数据库不存在的授权序列",
                "evidence": "两条散户账户序列和八条EPFR序列在合同中标记为运行授权缺失。",
                "repair": "收益模型只读正式SQLite精确序列，缺失合同保持缺失。",
            },
            {
                "cause": "同步资金流无法直接解释未来收益",
                "evidence": "净流入同时包含趋势追随、抄底、政策托底和被动申赎，方向随资金类型和阶段变化。",
                "repair": "先构造短中期创新，再用训练期分阶段秩相关形成连续后验权重。",
            },
            {
                "cause": "不同频率直接前向填充会造成发布日期泄漏",
                "evidence": "日频流量、月末仓位和事件融资的真实可见时间不同。",
                "repair": "每条序列先施加业务日发布滞后，再周频聚合，信号只作用于下一周。",
            },
            {
                "cause": "单一信号容易在资金主导者切换时失效",
                "evidence": "ETF托底、融资追涨、公募仓位和一级供给分别对应不同资金机制。",
                "repair": "先在七类资金内部收缩，再在资金类别之间做二层后验合成和波动风险预算。",
            },
        ],
        "research_basis": REPORT_BASIS,
        "candidate_evaluations": evaluations,
        "selected": selected,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "liquidity_state_challenger.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "database" / "liquidity_tracking.sqlite3",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "model_improvement" / "liquidity_tracking",
    )
    args = parser.parse_args()
    payload = run(args.database, args.output_dir)
    selected = payload["selected"]["evaluation"]
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_candidate": payload["selected_candidate"],
                "split_metrics": selected["split_metrics"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
