from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import build_asset_allocation_visual_pack_daily_local as daily_backend
import build_asset_allocation_visual_pack_v65_local as v65


REPO = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO / "output" / "model_improvement"
SNAPSHOT = DATA_ROOT / "asset_allocation_snapshot_v64_daily_excess_governed.json"
LEGACY_BOARD_SNAPSHOT = REPO / "board" / "quant_strategy_agent" / "data" / "asset_allocation_snapshot.json"
PANEL = DATA_ROOT / "asset_allocation_panel_v553.json"
FREEZE = DATA_ROOT / "asset_allocation_rqdata_v541_freeze.json"
BOARD_DATA_DIRS = [
    REPO / "board" / "quant_strategy_agent" / "data",
    REPO / "board" / "quant_strategy_agent_vnext" / "data",
]

ASSET_ORDER = ["equity", "bond", "gold", "commodity"]
ASSET_LABELS = {"equity": "股票", "bond": "债券", "gold": "黄金", "commodity": "商品"}
ASSET_COLORS = {
    "benchmark": "#FFC000",
    "strategy": "#BFBFBF",
    "relative": "#C00000",
    "equity": "#C00000",
    "bond": "#808080",
    "gold": "#FFC000",
    "commodity": "#2F75B5",
}
MERRILL_LABELS = {1: "复苏期", 2: "过热期", 3: "滞胀期", 4: "衰退期"}
PRING_LABELS = {
    1: "阶段I 信用修复",
    2: "阶段II 盈利扩张",
    3: "阶段III 繁荣",
    4: "阶段IV 信用压力",
    5: "阶段V 盈利下行",
    6: "阶段VI 衰退修复",
}
MODEL_LABELS = {
    "black_litterman": "BL模型",
    "macro_factor": "宏观因子模型",
    "risk_budget": "风险预算模型",
}
MODEL_SOURCE_KEYS = {
    "black_litterman": "black_litterman",
    "macro_factor": "macro_factor",
    "risk_budget": "risk_parity",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(number):
        return default
    return float(number)


def geom_return(values: pd.Series, periods: int = 252) -> float:
    vals = values.dropna().astype(float)
    if vals.empty:
        return 0.0
    return float(np.prod(1.0 + vals.to_numpy(dtype=float)) ** (periods / len(vals)) - 1.0)


def max_drawdown_from_nav(nav: pd.Series) -> float:
    vals = nav.dropna().astype(float)
    if vals.empty:
        return 0.0
    return float((vals / vals.cummax() - 1.0).min())


def nav_from_daily_returns(series: pd.Series) -> pd.Series:
    clean = series.dropna().astype(float)
    return pd.Series(np.cumprod(1.0 + clean.to_numpy(dtype=float)), index=clean.index)


def aligned_nav_frame(strategy: pd.Series, benchmark: pd.Series) -> pd.DataFrame:
    frame = pd.concat([strategy, benchmark], axis=1).dropna()
    frame.columns = ["strategy", "benchmark"]
    if frame.empty:
        return frame
    frame = frame / frame.iloc[0]
    frame["relative"] = frame["strategy"] / frame["benchmark"]
    return frame


def nav_records(strategy: pd.Series, benchmark: pd.Series) -> list[dict[str, Any]]:
    frame = aligned_nav_frame(strategy, benchmark)
    return [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "strategy": clean_float(row["strategy"]),
            "benchmark": clean_float(row["benchmark"]),
            "relative": clean_float(row["relative"]),
        }
        for idx, row in frame.iterrows()
    ]


def annual_rows(strategy: pd.Series, benchmark: pd.Series) -> list[dict[str, Any]]:
    frame = aligned_nav_frame(strategy, benchmark)
    rows: list[dict[str, Any]] = []
    if frame.empty:
        return rows
    for year, sub in frame.groupby(frame.index.year):
        if len(sub) < 2:
            continue
        sr = float(sub["strategy"].iloc[-1] / sub["strategy"].iloc[0] - 1.0)
        br = float(sub["benchmark"].iloc[-1] / sub["benchmark"].iloc[0] - 1.0)
        rows.append(
            {
                "year": f"{year}YTD" if year == frame.index[-1].year else str(year),
                "strategy_return": sr,
                "benchmark_return": br,
                "excess_return": sr - br,
                "max_drawdown": max_drawdown_from_nav(sub["strategy"]),
            }
        )
    years = max((frame.index[-1] - frame.index[0]).days / 365.25, 1e-9)
    sr_total = float(frame["strategy"].iloc[-1] / frame["strategy"].iloc[0] - 1.0)
    br_total = float(frame["benchmark"].iloc[-1] / frame["benchmark"].iloc[0] - 1.0)
    sr_ann = float((1.0 + sr_total) ** (1.0 / years) - 1.0)
    br_ann = float((1.0 + br_total) ** (1.0 / years) - 1.0)
    rows.append(
        {
            "year": "区间年化",
            "strategy_return": sr_ann,
            "benchmark_return": br_ann,
            "excess_return": sr_ann - br_ann,
            "max_drawdown": max_drawdown_from_nav(frame["strategy"]),
        }
    )
    return rows


def stage_return_rows(daily_rets: pd.DataFrame, cycles: pd.DataFrame, stage_col: str, labels: dict[int, str]) -> list[dict[str, Any]]:
    stage_by_month = {period.strftime("%Y%m"): int(value) for period, value in cycles[stage_col].dropna().items()}
    frame = daily_rets[ASSET_ORDER].copy()
    frame["stage"] = [stage_by_month.get(idx.strftime("%Y%m")) for idx in frame.index]
    rows: list[dict[str, Any]] = []
    for stage_id, label in labels.items():
        sub = frame[frame["stage"] == stage_id]
        row: dict[str, Any] = {"stage": label}
        for asset in ASSET_ORDER:
            row[asset] = geom_return(sub[asset]) if not sub.empty else None
        rows.append(row)
    return rows


def cycle_records(cycles: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for period, row in cycles.iterrows():
        merrill = int(row["美林阶段"])
        pring = int(row["普林格阶段"])
        records.append(
            {
                "month": period.strftime("%Y%m"),
                "date": period.to_timestamp(how="end").strftime("%Y-%m-%d"),
                "merrill_stage": merrill,
                "merrill_stage_label": MERRILL_LABELS.get(merrill, str(merrill)),
                "merrill_growth": clean_float(row.get("增长连续指标"), 0.0),
                "merrill_growth_direction": int(row.get("增长方向", 0)),
                "merrill_inflation": clean_float(row.get("通胀连续指标"), 0.0),
                "merrill_inflation_direction": int(row.get("通胀方向", 0)),
                "pring_stage": pring,
                "pring_stage_label": PRING_LABELS.get(pring, str(pring)),
                "pring_money": clean_float(row.get("货币连续指标"), 0.0),
                "pring_money_direction": int(row.get("货币方向", 0)),
                "pring_credit": clean_float(row.get("信用连续指标"), 0.0),
                "pring_credit_direction": int(row.get("信用方向", 0)),
                "pring_growth": clean_float(row.get("普林格增长连续指标"), 0.0),
                "pring_growth_direction": int(row.get("普林格增长方向", 0)),
            }
        )
    return records


def macro_records(factors: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rename = {
        "增长因子": "growth",
        "通胀因子": "inflation",
        "利率因子": "rate",
        "信用因子": "credit",
        "汇率因子": "fx",
        "流动性因子": "liquidity",
    }
    for period, row in factors.iterrows():
        rec = {"month": period.strftime("%Y%m"), "date": period.to_timestamp(how="end").strftime("%Y-%m-%d")}
        for cn, key in rename.items():
            rec[key] = clean_float(row.get(cn), 0.0)
        rows.append(rec)
    return rows


def weights_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for public_key, source_key in MODEL_SOURCE_KEYS.items():
        weights = ((snapshot.get("allocation_models") or {}).get(source_key) or {}).get("current_weights") or {}
        row = {"model": MODEL_LABELS[public_key]}
        for asset in ASSET_ORDER:
            row[asset] = clean_float(weights.get(asset), 0.0)
        rows.append(row)
    return rows


def risk_budget_decomposition(snapshot: dict[str, Any], legacy_snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    risk = ((snapshot.get("allocation_models") or {}).get("risk_parity") or {}).get("current_diagnostics") or {}
    diag = risk.get("macro_cycle_budget_diagnostics") or {}
    legacy_alloc = (legacy_snapshot or {}).get("allocations") or {}
    legacy_risk = legacy_alloc.get("risk_parity") or {}
    legacy_macro = legacy_alloc.get("macro_risk_budget") or {}
    legacy_budget = ((legacy_risk.get("metadata") or {}).get("risk_budget") or {})

    core_share = clean_float(diag.get("risk_budget_core_weight"), 0.15) or 0.15
    overlay_share = clean_float(diag.get("macro_cycle_overlay_weight"), 0.75) or 0.75
    trend_share = clean_float(diag.get("relative_strength_confirmation_weight"), 0.10) or 0.10

    final_weights = ((snapshot.get("allocation_models") or {}).get("risk_parity") or {}).get("current_weights") or legacy_risk.get("weights") or {}
    pure_weights = diag.get("pure_erc_weights") or legacy_budget.get("weights")
    macro_weights = diag.get("macro_cycle_budget_weights") or legacy_macro.get("weights") or ((snapshot.get("allocation_models") or {}).get("macro_factor") or {}).get("current_weights") or {}

    def to_asset_dict(values: Any) -> dict[str, float]:
        if isinstance(values, dict):
            return {asset: clean_float(values.get(asset), 0.0) for asset in ASSET_ORDER}
        vals = list(values or [])
        return {asset: clean_float(vals[i] if i < len(vals) else None, 0.0) for i, asset in enumerate(ASSET_ORDER)}

    final_map = to_asset_dict(final_weights)
    pure_map = to_asset_dict(pure_weights)
    macro_map = to_asset_dict(macro_weights)
    trend_values = diag.get("relative_strength_confirmation_weights")
    if trend_values:
        trend_map = to_asset_dict(trend_values)
    else:
        trend_map = {
            asset: clean_float((final_map[asset] - core_share * pure_map[asset] - overlay_share * macro_map[asset]) / trend_share, 0.0)
            for asset in ASSET_ORDER
        }

    def vector(name: str, share: float, source: str, values: dict[str, float]) -> dict[str, Any]:
        row = {"component": name, "weight_in_model": share, "source": source}
        for asset in ASSET_ORDER:
            row[asset] = clean_float(values.get(asset), 0.0)
        return row

    return [
        vector("底仓：纯风险预算", core_share, "协方差矩阵求解风险贡献均衡", pure_map),
        vector("周期映射：周期+宏观Alpha", overlay_share, "美林/普林格周期与宏观Alpha目标权重", macro_map),
        vector("趋势确认：动量+波动率", trend_share, "由最终权重反推的3/6/12月相对强弱确认项", trend_map),
        vector("最终风险预算模型", 1.0, "前三项合成后的月频目标权重", final_map),
    ]

def macro_factor_table() -> list[dict[str, Any]]:
    return [
        {"factor_group": "增长", "factor_name": "PMI", "meaning": "经济景气扩张/收缩核心代理", "data_process": "缺失、极值、标准化、变化率、HP周期、FFT低频", "alpha_map": "增长强偏股票/商品，增长弱偏债券"},
        {"factor_group": "通胀", "factor_name": "CPI、PPI", "meaning": "消费端和生产端价格压力", "data_process": "同比、环比变化、周期项、低频项", "alpha_map": "通胀强偏商品/黄金，低通胀更利好债券"},
        {"factor_group": "利率", "factor_name": "国债收益率、资金利率", "meaning": "贴现率、久期机会成本和货币环境", "data_process": "收益率变化、期限结构、滚动标准化", "alpha_map": "利率下行利多债券，上行压制久期资产"},
        {"factor_group": "信用", "factor_name": "社融、M1-M2", "meaning": "信用扩张与实体融资改善", "data_process": "同比、增量变化、滚动分位数", "alpha_map": "信用扩张偏股票/商品，收缩偏债券/黄金"},
        {"factor_group": "汇率", "factor_name": "外汇、黄金、商品确认", "meaning": "外部价格压力和避险需求", "data_process": "趋势、波动、风险调整确认", "alpha_map": "外部压力升高偏黄金，商品趋势确认偏商品"},
        {"factor_group": "流动性", "factor_name": "M1、M2", "meaning": "货币条件和交易环境", "data_process": "同比差、变化率、滚动标准化", "alpha_map": "流动性宽松提升风险资产预算"},
    ]


def optimizer_rows(kind: str) -> list[dict[str, Any]]:
    common = [
        {"param": "政策基准", "value": "四资产默认25%等权", "role": "优化中心和主动偏离约束锚"},
        {"param": "协方差矩阵", "value": "24月半衰期 + 35%对角收缩", "role": "提高近期风险敏感度，并降低相关性噪声"},
    ]
    if kind == "risk_budget":
        return [
            {"param": "协方差矩阵", "value": "24月半衰期 + 35%对角收缩", "role": "作为风险贡献求解输入"},
            {"param": "核心求解", "value": "最小化风险贡献偏离度", "role": "迭代+线搜索，用梯度和Hessian更新"},
            {"param": "预算增强", "value": "底仓 + 周期映射 + 趋势确认", "role": "在风险均衡底层上叠加周期与动量确认"},
        ]
    if kind == "macro_factor":
        return common + [{"param": "优化器", "value": "CVXPY + CLARABEL凸二次优化", "role": "最大化预期收益，惩罚风险和换手成本"}]
    return common + [{"param": "周期观点", "value": "50%美林 + 50%普林格", "role": "转为资产相对观点和置信概率"}, {"param": "优化器", "value": "CVXPY + CLARABEL凸二次优化", "role": "最大化后验收益，惩罚风险和换手成本"}]


def factor_test_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in (snapshot.get("cycle_tracking") or {}).get("factor_rows") or []:
        out.append(
            {
                "factor_group": row.get("pillar") or row.get("cycle") or "--",
                "factor_name": row.get("factor") or row.get("factor_id") or "--",
                "transform": row.get("transform") or "--",
                "coverage": clean_float(row.get("coverage_full"), 0.0),
                "production_admitted": "是" if row.get("production_admitted") else "否",
                "selected_axes": row.get("selected_axes") or "--",
            }
        )
    return out[:48]


def cycle_score_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    tracking = snapshot.get("cycle_tracking") or {}
    summary = tracking.get("current_summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    cycles = summary.get("cycles") if isinstance(summary.get("cycles"), dict) else summary
    if not isinstance(cycles, dict):
        cycles = {}
    rows: list[dict[str, Any]] = []
    for model, label in [("merrill", "美林时钟"), ("pring", "普林格周期")]:
        info = cycles.get(model) or {}
        scores = info.get("asset_scores") or {}
        rows.append(
            {
                "cycle_model": label,
                "stage": info.get("stage") or "--",
                "confidence": clean_float(info.get("confidence"), 0.0),
                "equity_score": clean_float(scores.get("equity"), 0.0),
                "bond_score": clean_float(scores.get("bond"), 0.0),
                "gold_score": clean_float(scores.get("gold"), 0.0),
                "commodity_score": clean_float(scores.get("commodity"), 0.0),
            }
        )
    combined = cycles.get("combined") or summary.get("combined") or {}
    scores = combined.get("asset_scores") if isinstance(combined, dict) else {}
    if not scores:
        scores = tracking.get("combined_scores") or {}
    rows.append(
        {
            "cycle_model": "综合周期",
            "stage": "50%美林 + 50%普林格",
            "confidence": None,
            "equity_score": clean_float(scores.get("equity"), 0.0),
            "bond_score": clean_float(scores.get("bond"), 0.0),
            "gold_score": clean_float(scores.get("gold"), 0.0),
            "commodity_score": clean_float(scores.get("commodity"), 0.0),
        }
    )
    return rows


def build_payload() -> dict[str, Any]:
    snapshot = load_json(SNAPSHOT)
    legacy_snapshot = load_json(LEGACY_BOARD_SNAPSHOT) if LEGACY_BOARD_SNAPSHOT.exists() else {}
    panel = load_json(PANEL)
    freeze = load_json(FREEZE)
    monthly_returns = v65.panel_monthly_returns(panel)
    daily_rets = daily_backend._daily_assets(panel, freeze)
    cycles = v65.cycle_history_frame(snapshot)
    synthetic = v65.synth_cycles(list(monthly_returns.index), monthly_returns)
    if cycles.empty:
        cycles = synthetic
    cycle_returns = monthly_returns.reindex(cycles.index).dropna(how="any")
    cycles = cycles.reindex(cycle_returns.index).combine_first(synthetic.reindex(cycle_returns.index)).ffill().bfill()
    macro_factors = v65.macro_proxy_frame(cycles, cycle_returns)

    merrill_weights = {
        1: np.array([0.55, 0.10, 0.10, 0.25]),
        2: np.array([0.25, 0.10, 0.15, 0.50]),
        3: np.array([0.10, 0.15, 0.45, 0.30]),
        4: np.array([0.10, 0.55, 0.25, 0.10]),
    }
    pring_weights = {
        1: np.array([0.30, 0.40, 0.20, 0.10]),
        2: np.array([0.45, 0.15, 0.10, 0.30]),
        3: np.array([0.25, 0.10, 0.20, 0.45]),
        4: np.array([0.10, 0.15, 0.45, 0.30]),
        5: np.array([0.10, 0.35, 0.40, 0.15]),
        6: np.array([0.20, 0.45, 0.25, 0.10]),
    }
    benchmark_returns = daily_rets[ASSET_ORDER].mean(axis=1)
    benchmark_nav = nav_from_daily_returns(benchmark_returns)
    merrill_nav = nav_from_daily_returns(v65.daily_cycle_strategy_returns(daily_rets, cycles, "美林阶段", merrill_weights))
    pring_nav = nav_from_daily_returns(v65.daily_cycle_strategy_returns(daily_rets, cycles, "普林格阶段", pring_weights))

    model_rows = daily_backend._monthly_model_rows()
    model_navs: dict[str, pd.Series] = {}
    for public_key, source_key in MODEL_SOURCE_KEYS.items():
        model_returns = daily_backend._daily_strategy_returns(daily_rets, model_rows[source_key]).dropna()
        model_navs[public_key] = nav_from_daily_returns(model_returns)

    navs = {
        "merrill_clock": ("美林时钟配置", merrill_nav),
        "pring_cycle": ("普林格周期配置", pring_nav),
        **{key: (label, model_navs[key]) for key, label in MODEL_LABELS.items()},
    }

    payload = {
        "schema_version": "asset-allocation-interactive-v66/1.0",
        "generated_at": pd.Timestamp.utcnow().replace(microsecond=0).isoformat(),
        "source_engine_version": snapshot.get("engine_version"),
        "source_snapshot": str(SNAPSHOT),
        "legacy_board_snapshot": str(LEGACY_BOARD_SNAPSHOT),
        "data_as_of": snapshot.get("data_as_of") or snapshot.get("generated_at"),
        "asset_order": ASSET_ORDER,
        "asset_labels": ASSET_LABELS,
        "asset_colors": ASSET_COLORS,
        "cycle_history": cycle_records(cycles),
        "macro_series": macro_records(macro_factors),
        "asset_correlation": {
            "labels": [ASSET_LABELS[a] for a in ASSET_ORDER],
            "matrix": daily_rets[ASSET_ORDER].corr().round(6).values.tolist(),
        },
        "asset_mapping": {
            "merrill": [
                {"stage": MERRILL_LABELS[k], "equity": float(w[0]), "bond": float(w[1]), "gold": float(w[2]), "commodity": float(w[3])}
                for k, w in merrill_weights.items()
            ],
            "pring": [
                {"stage": PRING_LABELS[k], "equity": float(w[0]), "bond": float(w[1]), "gold": float(w[2]), "commodity": float(w[3])}
                for k, w in pring_weights.items()
            ],
        },
        "stage_returns": {
            "merrill": stage_return_rows(daily_rets, cycles, "美林阶段", MERRILL_LABELS),
            "pring": stage_return_rows(daily_rets, cycles, "普林格阶段", PRING_LABELS),
        },
        "nav_series": {key: {"label": label, "series": nav_records(nav, benchmark_nav)} for key, (label, nav) in navs.items()},
        "annual_tables": {key: annual_rows(nav, benchmark_nav) for key, (_label, nav) in navs.items()},
        "model_current_weights": weights_rows(snapshot),
        "cycle_score_rows": cycle_score_rows(snapshot),
        "macro_factor_rows": macro_factor_table(),
        "factor_test_rows": factor_test_rows(snapshot),
        "optimizer_rows": {
            "black_litterman": optimizer_rows("black_litterman"),
            "macro_factor": optimizer_rows("macro_factor"),
            "risk_budget": optimizer_rows("risk_budget"),
        },
        "risk_budget_decomposition": risk_budget_decomposition(snapshot, legacy_snapshot),
        "model_comparison": [
            {"model": "BL模型", "logic": "美林/普林格周期观点转为P、Q、Omega，与均衡收益融合", "strength": "可解释、能承接周期判断", "boundary": "观点质量依赖周期因子PIT与样本外检验"},
            {"model": "宏观因子模型", "logic": "六类宏观因子检验后映射到资产Alpha，再进入凸优化", "strength": "能过滤宏观环境变化", "boundary": "宏观发布时点和修订必须受D3/PIT门控"},
            {"model": "风险预算模型", "logic": "风险贡献均衡底仓叠加周期预算和趋势确认", "strength": "波动更稳、解释更直接", "boundary": "强权益行情中可能阶段性落后"},
        ],
    }
    return payload


def main() -> None:
    payload = build_payload()
    for out_dir in BOARD_DATA_DIRS:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "asset_allocation_interactive_v66.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps({"status": "ok", "rows": len(payload["cycle_history"]), "outputs": [str(p / "asset_allocation_interactive_v66.json") for p in BOARD_DATA_DIRS]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
