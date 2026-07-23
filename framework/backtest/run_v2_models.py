import argparse
import importlib.util
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


RUN_ID = "v2_formal_models"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_DATE = "20120101"
END_DATE = "20260630"
COST_RATE = 0.001
TARGET_ANNUAL_RETURN = 0.20
TARGET_SHARPE = 1.50
SPLITS = {
    "train": ("20120101", "20201231"),
    "valid": ("20210101", "20221231"),
    "test": ("20230101", "20260630"),
    "full": ("20120101", "20260630"),
}
STOCK_MODELS = {
    "stock_kline_technical": {"score": "kline_ai_pattern_score", "n": 20, "industry_cap": 0.50},
    "stock_factor_ai_factory": {"score": "small_value_quality_momo", "n": 30, "industry_cap": 0.50, "regime": "small_value_risk"},
    "stock_factor_ai_composite": {"score": "ai_small_value_quality_blend", "n": 30, "industry_cap": 0.50, "regime": "small_value_risk"},
    "stock_fundamental_quality": {"score": "fundamental_quality_score", "n": 40, "industry_cap": 0.35},
    "industry_rotation": {"score": "industry_rotation_score", "n": 45, "industry_cap": 0.50},
    "style_rotation": {"score": "style_rotation_score", "n": 40, "industry_cap": 0.40},
    "portfolio_optimizer": {"score": "portfolio_optimizer_score_v2", "n": 30, "industry_cap": 0.50, "regime": "small_value_risk"},
    "stock_kline_skill_agent_v4": {"score": "kline_skill_fusion_v4", "n": 18, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "score_strength"},
    "stock_factor_ai_factory_v4": {"score": "ai_factor_factory_v4", "n": 24, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "score_strength"},
    "stock_factor_deep_agent_v4": {"score": "deep_factor_agent_v4", "n": 22, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_factor_ai_blend_v5": {"score": "ai_factor_blend_v5", "n": 20, "industry_cap": 0.50, "regime": "small_value_risk", "weight_mode": "equal"},
    "stock_fundamental_quality_v4": {"score": "fundamental_quality_v4", "n": 32, "industry_cap": 0.30, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "industry_rotation_agent_v4": {"score": "industry_rotation_v4", "n": 35, "industry_cap": 0.30, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "style_rotation_agent_v4": {"score": "style_rotation_v4", "n": 30, "industry_cap": 0.30, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "portfolio_optimizer_agent_v4": {"score": "portfolio_optimizer_v4", "n": 24, "industry_cap": 0.30, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_factor_domain_agent_v9": {"score": "factor_domain_agent_v9", "n": 24, "industry_cap": 0.30, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_kline_domain_fusion_v9": {"score": "kline_factor_domain_fusion_v9", "n": 16, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_walkforward_ic_agent_v10": {"score": "walkforward_ic_alpha_v10", "n": 24, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_walkforward_ic_kline_guard_v10": {"score": "walkforward_ic_kline_guard_v10", "n": 20, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_kline_executable_skill_v11": {"score": "kline_executable_skill_v11", "n": 18, "industry_cap": 0.35, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
    "stock_factor_auto_miner_v11": {"score": "factor_auto_miner_v11", "n": 24, "industry_cap": 0.35, "regime": "small_value_risk", "weight_mode": "risk_budget"},
    "portfolio_hierarchical_optimizer_v11": {"score": "hierarchical_alpha_v11", "n": 22, "industry_cap": 0.30, "regime": "adaptive_equity_risk", "weight_mode": "risk_budget"},
}
SPECIAL_STOCK_MODELS = {
    "CSI800_ENH": {
        "csi800_walkforward_ic_agent_v10": {
            "score": "walkforward_ic_alpha_v10",
            "n": 18,
            "industry_cap": 0.50,
            "regime": None,
            "weight_mode": "risk_budget",
        },
        "csi800_quality_value_core_v10": {
            "score": "csi800_quality_value_core_v10",
            "n": 24,
            "industry_cap": 0.50,
            "regime": None,
            "weight_mode": "risk_budget",
        },
        "csi800_cashflow_quality_agent_v11": {
            "score": "csi800_cashflow_quality_v11",
            "n": 10,
            "industry_cap": 0.75,
            "regime": None,
            "weight_mode": "equal",
        },
        "csi800_regime_barbell_agent_v11": {
            "score": "csi800_regime_barbell_v11",
            "n": 12,
            "industry_cap": 0.60,
            "regime": "adaptive_equity_risk",
            "weight_mode": "risk_budget",
        },
    },
    "CSI2000_ENH": {
        "csi2000_style_concentrated_agent_v6": {
            "score": "style_rotation_score",
            "n": 6,
            "industry_cap": 1.00,
            "regime": None,
            "weight_mode": "equal",
        },
        "csi2000_style_risk_control_agent_v6": {
            "score": "style_rotation_v4",
            "n": 20,
            "industry_cap": 0.30,
            "regime": "adaptive_equity_risk",
            "weight_mode": "risk_budget",
        },
    },
}
INDEX_ENH_MODELS = {
    "index_enhancement_agent_v6": {
        "score": "index_enhancement_alpha_v6",
        "top_q": 0.12,
        "active_weight": 1.00,
        "cap_mult": 4.0,
        "min_member_weight": 0.0,
        "regime": None,
    },
    "index_enhancement_deep_agent_v6": {
        "score": "deep_factor_agent_v4",
        "top_q": 0.12,
        "active_weight": 1.00,
        "cap_mult": 4.0,
        "min_member_weight": 0.0,
        "regime": None,
    },
    "index_enhancement_barbell_v11": {
        "score": "csi800_regime_barbell_v11",
        "top_q": 0.10,
        "active_weight": 0.90,
        "cap_mult": 3.5,
        "min_member_weight": 0.0,
        "regime": "adaptive_equity_risk",
    },
}
UNIVERSE_INCEPTION = {
    "CSI2000_ENH": "20230811",
}
MIN_MEMBER_COUNTS = {
    "CSI800_ENH": 600,
    "CSI2000_ENH": 1600,
}
IC_LEARNING_FACTORS = [
    "kline_ai_pattern_score",
    "small_value_quality_momo",
    "fundamental_quality_score",
    "ai_small_value_quality_blend",
    "industry_rotation_score",
    "style_rotation_score",
    "portfolio_optimizer_score_v2",
    "kline_skill_fusion_v4",
    "ai_factor_factory_v4",
    "deep_factor_agent_v4",
    "fundamental_quality_v4",
    "industry_rotation_v4",
    "style_rotation_v4",
    "portfolio_optimizer_v4",
    "ai_factor_blend_v5",
    "index_enhancement_alpha_v6",
    "quality_value_low_crowding_v8",
    "kline_context_agent_v8",
    "report_style_alpha_v8",
    "index_industry_risk_alpha_v8",
    "factor_domain_agent_v9",
    "kline_factor_domain_fusion_v9",
]
WALKFORWARD_IC_FACTORS = [
    "quality_value_low_crowding_v8",
    "fundamental_quality_v4",
    "index_industry_risk_alpha_v8",
    "factor_domain_agent_v9",
    "deep_factor_agent_v4",
    "ai_factor_blend_v5",
    "small_value_quality_momo",
    "portfolio_optimizer_score_v2",
    "ai_factor_factory_v4",
    "industry_rotation_v4",
    "kline_ai_pattern_score",
]
STRUCTURAL_MODELS = {
    "factor_ic_learned_agent_v7": {
        "score": "factor_ic_learned_alpha_v7",
        "n": 30,
        "industry_cap": 0.40,
        "regime": "small_value_risk",
        "weight_mode": "risk_budget",
    },
    "industry_budget_ic_optimizer_v7": {
        "score": "factor_ic_learned_alpha_v7",
        "top_frac": 0.15,
        "regime": None,
    },
}
REPORT_STYLE_MODELS = {
    "report_style_optimizer_v8": {
        "score": "report_style_alpha_v8",
        "top_frac": 0.08,
        "industry_tilt": 0.65,
        "target_ann_vol": 0.22,
        "max_exposure": 1.05,
        "single_name_cap": 0.06,
        "index_only": False,
    },
    "index_industry_risk_optimizer_v8": {
        "score": "index_industry_risk_alpha_v8",
        "top_frac": 0.10,
        "industry_tilt": 0.45,
        "target_ann_vol": 0.18,
        "max_exposure": 1.00,
        "single_name_cap": 0.05,
        "index_only": True,
    },
}


def load_factor_miner(project_root):
    root = Path(project_root)
    candidates = [
        root / "models" / "05_factor_mining_agent" / "factor_miner.py",
        root / "agents" / "10_factor_mining_agent" / "factor_miner.py",
    ]
    path = next((p for p in candidates if p.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location("factor_miner_v2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_quality(project_root, db):
    root = Path(project_root)
    candidates = [
        root / "framework" / "data_quality" / "quality_gate.py",
        root / "report" / "v2" / "quality_gate.py",
    ]
    qpath = next((p for p in candidates if p.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location("quality_gate_v2", qpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit_database(Path(db))


def safe_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        out = float(x)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def rank01(s, ascending=True):
    return s.rank(pct=True, ascending=ascending).fillna(0.5)


def clip01(x):
    return max(0.0, min(1.0, safe_float(x, 0.0)))


def industry_rank01(df, value_col, ascending=True):
    return df.groupby("industry_name")[value_col].rank(pct=True, ascending=ascending).fillna(0.5)


def max_drawdown(nav):
    peak = nav[0] if nav else 1.0
    out = 0.0
    for x in nav:
        peak = max(peak, x)
        out = min(out, x / peak - 1.0 if peak else 0.0)
    return out


def metrics_from_returns(rets, bench_rets=None, periods_per_year=12):
    rets = [safe_float(x) for x in rets]
    bench_rets = [safe_float(x) for x in (bench_rets or [0.0] * len(rets))]
    nav = [1.0]
    for r in rets:
        nav.append(nav[-1] * (1.0 + r))
    periods = len(rets)
    total = nav[-1] - 1.0
    annual = nav[-1] ** (periods_per_year / periods) - 1.0 if periods and nav[-1] > 0 else 0.0
    vol = float(np.std(rets)) * math.sqrt(periods_per_year) if periods else 0.0
    sharpe = annual / vol if vol else 0.0
    excess = [a - b for a, b in zip(rets, bench_rets)]
    ex_annual = (1.0 + np.mean(excess)) ** periods_per_year - 1.0 if excess else 0.0
    ir_vol = float(np.std(excess)) * math.sqrt(periods_per_year) if len(excess) > 1 else 0.0
    return {
        "periods": periods,
        "total_return": total,
        "annual_return": annual,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown(nav),
        "win_rate": sum(1 for x in rets if x > 0) / periods if periods else 0.0,
        "excess_annual_return": ex_annual,
        "information_ratio": ex_annual / ir_vol if ir_vol else 0.0,
        "target_pass": int(annual >= TARGET_ANNUAL_RETURN and sharpe >= TARGET_SHARPE),
    }


def load_industry(conn):
    rows = conn.execute(
        """
        select ts_code, start_date, coalesce(end_date, '99991231') as end_date, industry_name
        from sw_l1_industry_daily
        order by ts_code, start_date
        """
    ).fetchall()
    intervals = defaultdict(list)
    for code, start, end, name in rows:
        intervals[code].append((start, end or "99991231", name or "UNCLASSIFIED"))
    return intervals


def industry_at(intervals, code, date):
    for start, end, name in intervals.get(code, []):
        if start <= date and (not end or date < end):
            return name
    return "UNCLASSIFIED"


def build_stock_panel(conn, miner, universe, start, end, max_months=None):
    rows, min_date, max_date = miner.table_count(conn, universe)
    first_trade, last_trade = miner.required_trade_bounds(conn, start, end)
    if not rows or min_date > first_trade or max_date < last_trade:
        return None, f"universe {universe} coverage {min_date} to {max_date}; required {first_trade} to {last_trade}"
    fin, fin_dates = miner.load_financial(conn)
    intervals = load_industry(conn)
    pairs = miner.month_dates(conn, start, end)
    if max_months:
        pairs = pairs[:max_months]
    panels = []
    for date, next_date in pairs:
        df = miner.fetch_panel(conn, universe, date, next_date, fin, fin_dates)
        if df.empty:
            continue
        scores = miner.score_candidates(df)
        for name, s in scores.items():
            df[name] = s
        df["industry_name"] = [industry_at(intervals, c, date) for c in df["ts_code"]]
        df["kline_score"] = (
            0.30 * df["mom20"].rank(pct=True)
            + 0.30 * df["mom60"].rank(pct=True)
            + 0.20 * (-df["mom5"]).rank(pct=True)
            + 0.20 * df["large_order_balance"].rank(pct=True)
        ).fillna(0.5)
        df["kline_ai_pattern_score"] = (
            0.30 * rank01(-df["mom5"])
            + 0.24 * rank01(df["mom20"])
            + 0.20 * rank01(df["large_order_balance"])
            + 0.16 * rank01(-df["turnover_rate"])
            + 0.10 * rank01(df["mom60"])
        ).fillna(0.5)
        df["small_value_quality_momo"] = (
            0.24 * rank01(-df["total_mv"])
            + 0.20 * rank01(-df["pb"])
            + 0.18 * rank01(df["roe"])
            + 0.18 * rank01(df["mom60"])
            + 0.12 * rank01(df["large_order_balance"])
            + 0.08 * rank01(-df["turnover_rate"])
        ).fillna(0.5)
        df["fundamental_quality_score"] = (
            0.28 * df["roe"].rank(pct=True)
            + 0.20 * df["roa"].rank(pct=True)
            + 0.18 * df["gross_margin"].rank(pct=True)
            + 0.18 * df["netprofit_yoy"].rank(pct=True)
            + 0.16 * (-df["debt_to_assets"]).rank(pct=True)
        ).fillna(0.5)
        df["ai_small_value_quality_blend"] = (
            0.62 * df["small_value_quality_momo"]
            + 0.18 * df["ai_factor_factory_v2"]
            + 0.12 * df["fundamental_quality_score"]
            + 0.08 * df["kline_ai_pattern_score"]
        ).fillna(0.5)
        industry_score = df.groupby("industry_name")["ai_factor_composite_v1"].mean().rank(pct=True)
        df["industry_base_score"] = [industry_score.get(x, 0.5) for x in df["industry_name"]]
        df["industry_rotation_score"] = (0.45 * df["industry_base_score"] + 0.35 * df["ai_factor_composite_v1"] + 0.20 * df["kline_ai_pattern_score"]).fillna(0.5)
        df["growth_style"] = (df["mom60"].rank(pct=True) + df["netprofit_yoy"].rank(pct=True) + df["roe"].rank(pct=True)) / 3
        df["value_style"] = ((-df["pb"]).rank(pct=True) + df["dv_ttm"].rank(pct=True) + (-df["total_mv"]).rank(pct=True)) / 3
        df["dividend_style"] = (df["dv_ttm"].rank(pct=True) + df["roe"].rank(pct=True) + (-df["turnover_rate"]).rank(pct=True)) / 3
        style_raw = pd.concat([df["growth_style"], df["value_style"], df["dividend_style"]], axis=1).max(axis=1)
        df["style_rotation_score"] = (0.65 * style_raw + 0.35 * df["kline_score"]).fillna(0.5)
        df["portfolio_optimizer_score"] = (
            0.36 * df["ai_factor_composite_v1"]
            + 0.26 * df["ai_factor_factory_v2"]
            + 0.18 * df["kline_ai_pattern_score"]
            + 0.12 * df["industry_rotation_score"]
            + 0.08 * df["fundamental_quality_score"]
        ).fillna(0.5)
        df["portfolio_optimizer_score_v2"] = (
            0.58 * df["small_value_quality_momo"]
            + 0.18 * df["fundamental_quality_score"]
            + 0.14 * df["kline_ai_pattern_score"]
            + 0.10 * df["industry_rotation_score"]
        ).fillna(0.5)
        df["low_crowding_v4"] = (
            0.55 * rank01(-df["turnover_rate"])
            + 0.25 * rank01(-df.get("turnover_rate_f", df["turnover_rate"]))
            + 0.20 * rank01(-df.get("volume_ratio", df["turnover_rate"]))
        ).fillna(0.5)
        df["profitability_v4"] = (
            0.24 * rank01(df["roe"])
            + 0.18 * rank01(df["roa"])
            + 0.18 * rank01(df["gross_margin"])
            + 0.16 * rank01(df.get("netprofit_margin", df["roe"]))
            + 0.12 * rank01(df.get("assets_turn", df["roe"]))
            + 0.12 * rank01(-df["debt_to_assets"])
        ).fillna(0.5)
        df["growth_revision_v4"] = (
            0.34 * rank01(df["netprofit_yoy"])
            + 0.24 * rank01(df.get("op_yoy", df["netprofit_yoy"]))
            + 0.18 * rank01(df.get("tr_yoy", df["netprofit_yoy"]))
            + 0.14 * rank01(df["mom20"] - df["mom5"])
            + 0.10 * rank01(df["large_order_balance"])
        ).fillna(0.5)
        df["value_v4"] = (
            0.32 * rank01(-df["pb"])
            + 0.20 * rank01(-df.get("ps_ttm", df["pb"]))
            + 0.18 * rank01(df["dv_ttm"])
            + 0.14 * rank01(-df["pe_ttm"])
            + 0.16 * rank01(-df["total_mv"])
        ).fillna(0.5)
        df["trend_quality_v4"] = (
            0.32 * rank01(df["mom60"])
            + 0.22 * rank01(df["mom120"])
            + 0.20 * rank01(df["mom20"] - df["mom5"])
            + 0.16 * rank01(df["large_order_balance"])
            + 0.10 * rank01(-df["turnover_rate"])
        ).fillna(0.5)
        df["kline_skill_fusion_v4"] = (
            0.30 * rank01(df["mom20"] - df["mom5"])
            + 0.24 * rank01(df["mom60"])
            + 0.18 * rank01(df["large_order_balance"])
            + 0.16 * df["low_crowding_v4"]
            + 0.12 * rank01(df["mom120"])
        ).fillna(0.5)
        df["ai_factor_factory_v4"] = (
            0.26 * df["profitability_v4"]
            + 0.24 * df["value_v4"]
            + 0.22 * df["trend_quality_v4"]
            + 0.16 * df["growth_revision_v4"]
            + 0.12 * df["low_crowding_v4"]
        ).fillna(0.5)
        df["deep_factor_agent_v4"] = (
            0.34 * (df["profitability_v4"] * df["value_v4"])
            + 0.28 * (df["trend_quality_v4"] * df["low_crowding_v4"])
            + 0.20 * (df["growth_revision_v4"] * rank01(df["large_order_balance"]))
            + 0.10 * df["kline_skill_fusion_v4"]
            + 0.08 * rank01(-df["total_mv"])
        ).fillna(0.5)
        df["fundamental_quality_v4"] = (
            0.36 * df["profitability_v4"]
            + 0.22 * df["growth_revision_v4"]
            + 0.20 * df["value_v4"]
            + 0.14 * df["low_crowding_v4"]
            + 0.08 * rank01(df["dv_ttm"])
        ).fillna(0.5)
        ind_v4 = df.groupby("industry_name")["deep_factor_agent_v4"].mean().rank(pct=True)
        industry_rank_v4 = pd.Series([ind_v4.get(x, 0.5) for x in df["industry_name"]], index=df.index)
        df["industry_rotation_v4"] = (
            0.34 * industry_rank_v4
            + 0.24 * df["deep_factor_agent_v4"]
            + 0.18 * df["kline_skill_fusion_v4"]
            + 0.14 * df["low_crowding_v4"]
            + 0.10 * df["growth_revision_v4"]
        )
        growth_v4 = (rank01(df["mom60"]) + df["growth_revision_v4"] + df["profitability_v4"]) / 3
        value_v4 = (df["value_v4"] + rank01(df["dv_ttm"]) + df["low_crowding_v4"]) / 3
        dividend_v4 = (rank01(df["dv_ttm"]) + df["profitability_v4"] + df["low_crowding_v4"]) / 3
        df["style_rotation_v4"] = (
            0.55 * pd.concat([growth_v4, value_v4, dividend_v4], axis=1).max(axis=1)
            + 0.25 * df["kline_skill_fusion_v4"]
            + 0.20 * df["industry_rotation_v4"]
        ).fillna(0.5)
        df["portfolio_optimizer_v4"] = (
            0.34 * df["deep_factor_agent_v4"]
            + 0.24 * df["ai_factor_factory_v4"]
            + 0.16 * df["industry_rotation_v4"]
            + 0.14 * df["kline_skill_fusion_v4"]
            + 0.12 * df["fundamental_quality_v4"]
        ).fillna(0.5)
        df["ai_factor_blend_v5"] = (
            0.55 * df["small_value_quality_momo"]
            + 0.45 * df["deep_factor_agent_v4"]
        ).fillna(0.5)
        df["index_enhancement_alpha_v6"] = (
            0.30 * df["deep_factor_agent_v4"]
            + 0.22 * df["style_rotation_score"]
            + 0.18 * df["fundamental_quality_v4"]
            + 0.16 * df["kline_skill_fusion_v4"]
            + 0.14 * df["industry_rotation_v4"]
        ).fillna(0.5)
        df["quality_value_low_crowding_v8"] = (
            0.28 * df["fundamental_quality_v4"]
            + 0.24 * df["value_v4"]
            + 0.20 * df["low_crowding_v4"]
            + 0.16 * df["profitability_v4"]
            + 0.12 * rank01(df["dv_ttm"])
        ).fillna(0.5)
        df["kline_context_agent_v8"] = (
            0.28 * rank01(df["mom20"] - df["mom5"])
            + 0.22 * rank01(df["mom60"])
            + 0.18 * rank01(df["large_order_balance"])
            + 0.14 * rank01(-abs(df["mom20"]))
            + 0.10 * rank01(df["mom120"])
            + 0.08 * df["low_crowding_v4"]
        ).fillna(0.5)
        df["report_style_alpha_v8"] = (
            0.26 * df["deep_factor_agent_v4"]
            + 0.22 * df["quality_value_low_crowding_v8"]
            + 0.20 * df["style_rotation_v4"]
            + 0.18 * df["kline_context_agent_v8"]
            + 0.14 * df["industry_rotation_v4"]
        ).fillna(0.5)
        df["index_industry_risk_alpha_v8"] = (
            0.30 * df["report_style_alpha_v8"]
            + 0.22 * df["index_enhancement_alpha_v6"]
            + 0.18 * df["quality_value_low_crowding_v8"]
            + 0.16 * df["kline_context_agent_v8"]
            + 0.14 * rank01(-df["turnover_rate"])
        ).fillna(0.5)
        df["domain_quality_neutral_v9"] = (
            0.30 * industry_rank01(df, "roe")
            + 0.20 * industry_rank01(df, "roa")
            + 0.20 * industry_rank01(df, "gross_margin")
            + 0.15 * industry_rank01(df, "netprofit_yoy")
            + 0.15 * industry_rank01(df, "debt_to_assets", ascending=False)
        ).fillna(0.5)
        df["domain_value_neutral_v9"] = (
            0.32 * industry_rank01(df, "pb", ascending=False)
            + 0.22 * industry_rank01(df, "pe_ttm", ascending=False)
            + 0.18 * industry_rank01(df, "ps_ttm", ascending=False)
            + 0.16 * industry_rank01(df, "dv_ttm")
            + 0.12 * industry_rank01(df, "total_mv", ascending=False)
        ).fillna(0.5)
        df["domain_money_neutral_v9"] = (
            0.34 * industry_rank01(df, "large_order_balance")
            + 0.26 * industry_rank01(df, "netflow_intensity")
            + 0.24 * industry_rank01(df, "turnover_rate", ascending=False)
            + 0.16 * industry_rank01(df, "volume_ratio", ascending=False)
        ).fillna(0.5)
        df["domain_technical_neutral_v9"] = (
            0.28 * industry_rank01(df, "mom60")
            + 0.22 * industry_rank01(df, "mom120")
            + 0.20 * industry_rank01(df, "mom20")
            + 0.18 * industry_rank01(df, "mom5", ascending=False)
            + 0.12 * industry_rank01(df, "turnover_rate", ascending=False)
        ).fillna(0.5)
        df["factor_domain_agent_v9"] = (
            0.28 * df["domain_quality_neutral_v9"]
            + 0.26 * df["domain_value_neutral_v9"]
            + 0.22 * df["domain_money_neutral_v9"]
            + 0.16 * df["domain_technical_neutral_v9"]
            + 0.08 * df["low_crowding_v4"]
        ).fillna(0.5)
        df["kline_factor_domain_fusion_v9"] = (
            0.42 * df["domain_technical_neutral_v9"]
            + 0.24 * df["domain_money_neutral_v9"]
            + 0.18 * df["factor_domain_agent_v9"]
            + 0.16 * df["kline_context_agent_v8"]
        ).fillna(0.5)
        df["csi800_quality_value_core_v10"] = (
            0.34 * df["quality_value_low_crowding_v8"]
            + 0.24 * df["fundamental_quality_v4"]
            + 0.18 * df["factor_domain_agent_v9"]
            + 0.14 * df["index_industry_risk_alpha_v8"]
            + 0.10 * df["ai_factor_blend_v5"]
        ).fillna(0.5)
        df["kline_executable_skill_v11"] = (
            0.22 * rank01(df["mom20"] - df["mom5"])
            + 0.18 * rank01(df["mom60"])
            + 0.16 * rank01(df["mom120"])
            + 0.14 * rank01(df["large_order_balance"])
            + 0.12 * df["domain_technical_neutral_v9"]
            + 0.10 * df["low_crowding_v4"]
            + 0.08 * rank01(-abs(df["mom20"]))
        ).fillna(0.5)
        df["csi800_cashflow_quality_v11"] = (
            0.22 * rank01(df["netprofit_yoy"])
            + 0.18 * rank01(df.get("op_yoy", df["netprofit_yoy"]))
            + 0.18 * rank01(df.get("tr_yoy", df["netprofit_yoy"]))
            + 0.16 * df["profitability_v4"]
            + 0.12 * rank01(df["large_order_balance"])
            + 0.08 * rank01(-df["pb"])
            + 0.06 * rank01(df["total_mv"])
        ).fillna(0.5)
        df["csi800_regime_barbell_v11"] = (
            0.24 * df["style_rotation_score"]
            + 0.20 * df["quality_value_low_crowding_v8"]
            + 0.18 * df["csi800_cashflow_quality_v11"]
            + 0.14 * df["kline_executable_skill_v11"]
            + 0.14 * rank01(df["dv_ttm"])
            + 0.10 * rank01(-abs(df["mom20"]))
        ).fillna(0.5)
        panels.append(df)
    if not panels:
        return None, f"no monthly panel for {universe}"
    return pd.concat(panels, ignore_index=True), None


def monthly_rank_ic(panel, factor, start, end):
    sub = panel[(panel["trade_date"] >= start) & (panel["trade_date"] <= end)]
    out = []
    for _, g in sub.groupby("trade_date", sort=True):
        if len(g) < 50 or factor not in g:
            continue
        ic = g[factor].rank().corr(g["label_next_ret"].rank())
        if pd.notna(ic):
            out.append(float(ic))
    return np.array(out, dtype=float)


def add_ic_learned_alpha(panel):
    weights = {}
    diagnostics = []
    for factor in IC_LEARNING_FACTORS:
        if factor not in panel.columns:
            continue
        train_ic = monthly_rank_ic(panel, factor, SPLITS["train"][0], SPLITS["train"][1])
        valid_ic = monthly_rank_ic(panel, factor, SPLITS["valid"][0], SPLITS["valid"][1])
        train_mean = float(np.nanmean(train_ic)) if len(train_ic) else 0.0
        train_std = float(np.nanstd(train_ic)) if len(train_ic) > 1 else 0.0
        valid_mean = float(np.nanmean(valid_ic)) if len(valid_ic) else 0.0
        raw = max(0.0, train_mean)
        if valid_mean < -0.005:
            raw *= 0.25
        elif valid_mean > 0:
            raw *= 1.0 + min(valid_mean, 0.03) * 10.0
        diagnostics.append({
            "factor": factor,
            "train_rank_ic": train_mean,
            "train_icir": train_mean / train_std if train_std else 0.0,
            "valid_rank_ic": valid_mean,
            "raw_weight": raw,
        })
        if raw > 0:
            weights[factor] = raw
    total = sum(weights.values())
    if total <= 0:
        panel["factor_ic_learned_alpha_v7"] = panel["style_rotation_score"]
        return panel, {"weights": {}, "diagnostics": diagnostics, "fallback": "style_rotation_score"}
    weights = {k: float(v / total) for k, v in weights.items()}
    learned = pd.Series(0.0, index=panel.index)
    for factor, weight in weights.items():
        learned = learned + weight * panel.groupby("trade_date")[factor].rank(pct=True).fillna(0.5)
    panel["factor_ic_learned_alpha_v7"] = learned.fillna(0.5)
    return panel, {"weights": weights, "diagnostics": diagnostics, "fallback": None}


def add_walkforward_ic_alpha(panel, lookback=36, min_obs=12):
    factors = [f for f in WALKFORWARD_IC_FACTORS if f in panel.columns]
    if not factors:
        panel["walkforward_ic_alpha_v10"] = panel.get("factor_ic_learned_alpha_v7", panel["style_rotation_score"])
        panel["walkforward_ic_kline_guard_v10"] = panel["walkforward_ic_alpha_v10"]
        return panel, {"factors": [], "fallback": "factor_ic_learned_alpha_v7_or_style_rotation_score"}

    grouped = [(date, idx.to_numpy()) for date, idx in panel.groupby("trade_date", sort=True).groups.items()]
    monthly_ic = {factor: [] for factor in factors}
    for _, idx in grouped:
        g = panel.loc[idx]
        label_rank = g["label_next_ret"].rank()
        for factor in factors:
            ic = g[factor].rank().corr(label_rank)
            monthly_ic[factor].append(float(ic) if pd.notna(ic) else 0.0)

    alpha = pd.Series(0.0, index=panel.index, dtype=float)
    diagnostics = []
    fallback_count = 0
    for i, (date, idx) in enumerate(grouped):
        start = max(0, i - lookback)
        raw_weights = {}
        for factor in factors:
            hist = np.array(monthly_ic[factor][start:i], dtype=float)
            hist = hist[np.isfinite(hist)]
            if len(hist) < min_obs:
                continue
            mean_ic = float(np.mean(hist))
            std_ic = float(np.std(hist))
            positive_ratio = float((hist > 0).mean())
            if mean_ic <= 0 or positive_ratio < 0.45:
                continue
            stability = mean_ic / (std_ic + 1e-6)
            raw_weights[factor] = mean_ic * positive_ratio * (1.0 + min(2.0, max(0.0, stability)))

        if not raw_weights:
            fallback_count += 1
            if "factor_ic_learned_alpha_v7" in panel.columns:
                alpha.loc[idx] = panel.loc[idx, "factor_ic_learned_alpha_v7"].rank(pct=True).fillna(0.5)
            else:
                alpha.loc[idx] = panel.loc[idx, "style_rotation_score"].rank(pct=True).fillna(0.5)
            diagnostics.append({"trade_date": date, "weights": {}, "fallback": True})
            continue

        total = sum(raw_weights.values())
        weights = {k: float(v / total) for k, v in raw_weights.items()}
        sub = panel.loc[idx]
        score = pd.Series(0.0, index=idx, dtype=float)
        for factor, weight in weights.items():
            score = score + weight * sub[factor].rank(pct=True).fillna(0.5)
        alpha.loc[idx] = score
        diagnostics.append({"trade_date": date, "weights": weights, "fallback": False})

    panel["walkforward_ic_alpha_v10"] = alpha.fillna(0.5)
    panel["walkforward_ic_kline_guard_v10"] = (
        0.78 * panel["walkforward_ic_alpha_v10"]
        + 0.14 * panel["kline_context_agent_v8"]
        + 0.08 * panel["low_crowding_v4"]
    ).fillna(0.5)
    return panel, {
        "factors": factors,
        "lookback": lookback,
        "min_obs": min_obs,
        "fallback_count": fallback_count,
        "latest_weights": diagnostics[-1]["weights"] if diagnostics else {},
        "monthly_diagnostics": diagnostics,
    }


def add_v11_alpha_scores(panel):
    panel["factor_auto_miner_v11"] = (
        0.38 * panel["walkforward_ic_alpha_v10"]
        + 0.20 * panel["deep_factor_agent_v4"]
        + 0.16 * panel["factor_domain_agent_v9"]
        + 0.12 * panel["quality_value_low_crowding_v8"]
        + 0.08 * panel["kline_executable_skill_v11"]
        + 0.06 * panel["low_crowding_v4"]
    ).fillna(0.5)
    panel["hierarchical_alpha_v11"] = (
        0.30 * panel["factor_auto_miner_v11"]
        + 0.22 * panel["industry_rotation_v4"]
        + 0.18 * panel["style_rotation_v4"]
        + 0.14 * panel["kline_executable_skill_v11"]
        + 0.10 * panel["fundamental_quality_v4"]
        + 0.06 * panel["low_crowding_v4"]
    ).fillna(0.5)
    panel["csi800_regime_barbell_v11"] = (
        0.70 * panel["csi800_regime_barbell_v11"]
        + 0.18 * panel["factor_auto_miner_v11"]
        + 0.12 * panel["walkforward_ic_kline_guard_v10"]
    ).fillna(0.5)
    return panel


def select_names(g, score_col, n, industry_cap):
    g = g.dropna(subset=[score_col, "label_next_ret"]).sort_values(score_col, ascending=False)
    selected = []
    industry_weights = defaultdict(float)
    target_w = 1.0 / max(n, 1)
    for row in g.itertuples(index=False):
        ind = getattr(row, "industry_name")
        if industry_weights[ind] + target_w > industry_cap:
            continue
        selected.append(row)
        industry_weights[ind] += target_w
        if len(selected) >= n:
            break
    return selected


def selected_weights(selected, score_col, weight_mode):
    if not selected:
        return {}
    if weight_mode == "equal":
        return {x.ts_code: 1.0 / len(selected) for x in selected}
    scores = np.array([safe_float(getattr(x, score_col), 0.5) for x in selected], dtype=float)
    scores = scores - np.nanmin(scores) + 1e-6
    if float(np.nanmax(scores)) <= 1e-6:
        scores = np.ones(len(selected), dtype=float)
    if weight_mode == "score_strength":
        raw = np.power(scores, 1.35)
    elif weight_mode == "risk_budget":
        crowding = np.array([safe_float(getattr(x, "turnover_rate", 0.0), 0.0) for x in selected], dtype=float)
        mom_risk = np.array([abs(safe_float(getattr(x, "mom20", 0.0), 0.0)) for x in selected], dtype=float)
        risk_penalty = 1.0 + np.nan_to_num(crowding, nan=0.0) / 20.0 + 2.5 * np.nan_to_num(mom_risk, nan=0.0)
        raw = np.power(scores, 1.15) / np.maximum(risk_penalty, 0.25)
    else:
        raw = np.ones(len(selected), dtype=float)
    raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
    if raw.sum() <= 0:
        raw = np.ones(len(selected), dtype=float)
    raw = raw / raw.sum()
    cap = min(0.10, 2.5 / len(selected))
    raw = np.minimum(raw, cap)
    raw = raw / raw.sum()
    return {x.ts_code: float(w) for x, w in zip(selected, raw)}


def latest_complete_member_date(conn, universe, date):
    min_members = MIN_MEMBER_COUNTS.get(universe, 1)
    row = conn.execute(
        """
        select trade_date
        from index_constituent_period
        where universe=? and trade_date<=?
        group by trade_date
        having count(*) >= ?
        order by trade_date desc
        limit 1
        """,
        (universe, date, min_members),
    ).fetchone()
    return row[0] if row else None


def normalized_index_weights(g):
    base = g["index_weight"].fillna(0.0).astype(float) / 100.0
    if float(base.sum()) <= 0:
        return pd.Series(1.0 / max(len(g), 1), index=g.index)
    base = base / float(base.sum())
    return base


def turnover_from_weights(weights, prev_weights):
    keys = set(weights) | set(prev_weights)
    return sum(abs(weights.get(k, 0.0) - prev_weights.get(k, 0.0)) for k in keys)


def regime_exposure(g, regime, last_ret=0.0):
    if regime != "small_value_risk":
        if regime != "adaptive_equity_risk":
            return 1.0
        trend120 = safe_float(g["mom120"].median(), 0.0)
        trend60 = safe_float(g["mom60"].median(), 0.0)
        breadth60 = safe_float((g["mom60"] > 0).mean(), 0.5)
        breadth20 = safe_float((g["mom20"] > 0).mean(), 0.5)
        crowd = safe_float(g["turnover_rate"].median(), 0.0)
        if trend120 > 0.06 and trend60 > 0.02 and breadth60 > 0.58:
            exposure = 1.0
        elif trend120 > 0.00 and breadth60 > 0.48:
            exposure = 0.85
        elif trend60 > -0.03 and breadth20 > 0.42:
            exposure = 0.55
        elif trend120 < -0.08 or breadth60 < 0.34:
            exposure = 0.15
        else:
            exposure = 0.35
        if crowd > 5.0 and breadth20 < 0.45:
            exposure = min(exposure, 0.45)
        if last_ret < -0.07:
            exposure = min(exposure, 0.35)
        if last_ret < -0.12:
            exposure = min(exposure, 0.15)
        return exposure
        return 1.0
    trend = safe_float(g["mom120"].median(), 0.0)
    if trend > 0.04:
        return 1.0
    if trend > -0.04:
        return 0.85
    return 0.25


def backtest_stock_panel(panel, universe, model_name, score_col, n, industry_cap, regime=None, weight_mode="equal"):
    returns = []
    bench = []
    nav_rows = []
    signal_rows = []
    prev_weights = {}
    nav = 1.0
    last_ret = 0.0
    for date, g in panel.groupby("trade_date", sort=True):
        selected = select_names(g, score_col, n, industry_cap)
        weights = selected_weights(selected, score_col, weight_mode)
        turnover = 1.0 if not prev_weights else turnover_from_weights(weights, prev_weights)
        gross = sum(weights.get(x.ts_code, 0.0) * safe_float(x.label_next_ret) for x in selected) if selected else 0.0
        exposure = regime_exposure(g, regime, last_ret)
        ret = exposure * (gross - turnover * COST_RATE)
        bret = float(g["label_next_ret"].mean()) if len(g) else 0.0
        nav *= 1.0 + ret
        returns.append(ret)
        bench.append(bret)
        nav_rows.append({"trade_date": date, "nav": nav, "period_return": ret, "benchmark_return": bret, "excess_return": ret - bret})
        for rank_no, item in enumerate(selected, 1):
            signal_rows.append({
                "trade_date": date,
                "ts_code": item.ts_code,
                "industry_name": item.industry_name,
                "score": float(getattr(item, score_col)),
                "rank_no": rank_no,
                "target_weight": exposure * weights.get(item.ts_code, 0.0),
            })
        prev_weights = {k: exposure * v for k, v in weights.items()}
        last_ret = ret
    return returns, bench, nav_rows, signal_rows


def backtest_index_enhancement_panel(panel, universe, model_name, score_col, top_q, active_weight, cap_mult, min_member_weight=0.0, regime=None):
    returns = []
    bench = []
    nav_rows = []
    signal_rows = []
    prev_weights = {}
    nav = 1.0
    last_ret = 0.0
    for date, g in panel.groupby("trade_date", sort=True):
        g = g.dropna(subset=[score_col, "label_next_ret"]).copy()
        if g.empty:
            continue
        base = normalized_index_weights(g)
        score = g[score_col].fillna(0.5).rank(pct=True)
        cutoff = score.quantile(max(0.0, min(1.0, 1.0 - top_q)))
        alpha_raw = (score - cutoff).clip(lower=0.0)
        if float(alpha_raw.sum()) > 0:
            alpha = alpha_raw / float(alpha_raw.sum())
            raw = (1.0 - active_weight) * base + active_weight * alpha
        else:
            raw = base.copy()
        cap = np.maximum(base * cap_mult, min_member_weight)
        raw = pd.Series(np.minimum(np.maximum(raw, 0.0), cap), index=g.index)
        if float(raw.sum()) <= 0:
            raw = base.copy()
        weights_arr = raw / float(raw.sum())
        weights = {code: float(w) for code, w in zip(g["ts_code"], weights_arr)}
        turnover = 1.0 if not prev_weights else turnover_from_weights(weights, prev_weights)
        gross = float((weights_arr * g["label_next_ret"]).sum())
        bret = float((base * g["label_next_ret"]).sum())
        exposure = regime_exposure(g, regime, last_ret) if regime else 1.0
        ret = exposure * (gross - turnover * COST_RATE)
        nav *= 1.0 + ret
        returns.append(ret)
        bench.append(bret)
        nav_rows.append({"trade_date": date, "nav": nav, "period_return": ret, "benchmark_return": bret, "excess_return": ret - bret})
        ranked = pd.DataFrame({
            "ts_code": g["ts_code"].to_numpy(),
            "industry_name": g["industry_name"].to_numpy(),
            "score": score.to_numpy(dtype=float),
            "target_weight": [exposure * weights.get(x, 0.0) for x in g["ts_code"]],
        }).sort_values("target_weight", ascending=False)
        for rank_no, row in enumerate(ranked.itertuples(index=False), 1):
            if row.target_weight <= 0:
                continue
            signal_rows.append({
                "trade_date": date,
                "ts_code": row.ts_code,
                "industry_name": row.industry_name,
                "score": float(row.score),
                "rank_no": rank_no,
                "target_weight": float(row.target_weight),
            })
        prev_weights = {k: exposure * v for k, v in weights.items()}
        last_ret = ret
    return returns, bench, nav_rows, signal_rows


def backtest_industry_budget_panel(panel, universe, model_name, score_col, top_frac, regime=None):
    returns = []
    bench = []
    nav_rows = []
    signal_rows = []
    prev_weights = {}
    nav = 1.0
    last_ret = 0.0
    for date, g0 in panel.groupby("trade_date", sort=True):
        g = g0.dropna(subset=[score_col, "label_next_ret"]).copy()
        if g.empty:
            continue
        base = normalized_index_weights(g)
        weights = {}
        for _, idx in g.groupby("industry_name").groups.items():
            sub = g.loc[list(idx)].copy()
            sub_base = base.loc[sub.index]
            budget = float(sub_base.sum())
            if budget <= 0 or sub.empty:
                continue
            take = max(1, min(len(sub), math.ceil(len(sub) * top_frac)))
            picks = sub.sort_values(score_col, ascending=False).head(take)
            raw = np.power((picks[score_col] - picks[score_col].min() + 1e-6).to_numpy(dtype=float), 1.10)
            if raw.sum() <= 0:
                raw = np.ones(len(picks), dtype=float)
            raw = raw / raw.sum() * budget
            for (_, row), w in zip(picks.iterrows(), raw):
                weights[row["ts_code"]] = weights.get(row["ts_code"], 0.0) + float(w)
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()} if total > 0 else {}
        turnover = 1.0 if not prev_weights else turnover_from_weights(weights, prev_weights)
        ret_map = dict(zip(g["ts_code"], g["label_next_ret"]))
        gross = sum(weights.get(k, 0.0) * safe_float(ret_map.get(k)) for k in weights)
        bret = float((base * g["label_next_ret"]).sum())
        exposure = regime_exposure(g, regime, last_ret) if regime else 1.0
        ret = exposure * (gross - turnover * COST_RATE)
        nav *= 1.0 + ret
        returns.append(ret)
        bench.append(bret)
        nav_rows.append({"trade_date": date, "nav": nav, "period_return": ret, "benchmark_return": bret, "excess_return": ret - bret})
        ranked = pd.DataFrame({
            "ts_code": list(weights.keys()),
            "target_weight": [exposure * v for v in weights.values()],
        })
        if not ranked.empty:
            ranked = ranked.merge(g[["ts_code", "industry_name", score_col]], on="ts_code", how="left").sort_values("target_weight", ascending=False)
            for rank_no, row in enumerate(ranked.itertuples(index=False), 1):
                signal_rows.append({
                    "trade_date": date,
                    "ts_code": row.ts_code,
                    "industry_name": row.industry_name,
                    "score": float(getattr(row, score_col)),
                    "rank_no": rank_no,
                    "target_weight": float(row.target_weight),
                })
        prev_weights = {k: exposure * v for k, v in weights.items()}
        last_ret = ret
    return returns, bench, nav_rows, signal_rows


def report_optimizer_exposure(g, trailing_returns, nav, peak, target_ann_vol, max_exposure):
    trend120 = safe_float(g["mom120"].median(), 0.0)
    trend60 = safe_float(g["mom60"].median(), 0.0)
    breadth60 = safe_float((g["mom60"] > 0).mean(), 0.5)
    exposure = 0.85
    if trend120 > 0.06 and trend60 > 0.02 and breadth60 > 0.56:
        exposure = max_exposure
    elif trend120 > -0.02 and breadth60 > 0.45:
        exposure = min(max_exposure, 0.85)
    elif trend120 < -0.08 or breadth60 < 0.34:
        exposure = 0.25
    else:
        exposure = 0.55

    if len(trailing_returns) >= 6:
        monthly_target = target_ann_vol / math.sqrt(12.0)
        realized = float(np.std(trailing_returns[-12:])) if len(trailing_returns[-12:]) > 1 else 0.0
        if realized > 0:
            exposure = min(exposure, max(0.25, monthly_target / realized))

    drawdown = nav / peak - 1.0 if peak else 0.0
    if drawdown < -0.20:
        exposure = min(exposure, 0.20)
    elif drawdown < -0.12:
        exposure = min(exposure, 0.40)
    return max(0.0, min(max_exposure, exposure))


def capped_normalize(weights, cap):
    if not weights:
        return {}
    weights = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(weights.values())
    if total <= 0:
        return {}
    weights = {k: v / total for k, v in weights.items()}
    for _ in range(4):
        over = {k: v for k, v in weights.items() if v > cap}
        if not over:
            break
        fixed = sum(over.values())
        free = {k: v for k, v in weights.items() if v <= cap}
        if not free:
            n = len(weights)
            return {k: 1.0 / n for k in weights}
        residual = max(0.0, 1.0 - cap * len(over))
        free_total = sum(free.values())
        weights = {k: cap for k in over}
        weights.update({k: residual * v / free_total for k, v in free.items()})
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total > 0 else {}


def backtest_report_style_optimizer_panel(
    panel,
    universe,
    model_name,
    score_col,
    top_frac,
    industry_tilt,
    target_ann_vol,
    max_exposure,
    single_name_cap,
):
    returns = []
    bench = []
    nav_rows = []
    signal_rows = []
    prev_weights = {}
    nav = 1.0
    peak = 1.0
    for date, g0 in panel.groupby("trade_date", sort=True):
        g = g0.dropna(subset=[score_col, "label_next_ret"]).copy()
        if g.empty:
            continue
        base = normalized_index_weights(g)
        industry_score = g.groupby("industry_name")[score_col].mean().rank(pct=True)
        raw_budget = {}
        for ind, idx in g.groupby("industry_name").groups.items():
            budget = float(base.loc[list(idx)].sum())
            raw_budget[ind] = max(0.0, budget * (1.0 + industry_tilt * (safe_float(industry_score.get(ind), 0.5) - 0.5)))
        budget_total = sum(raw_budget.values())
        industry_budget = {k: v / budget_total for k, v in raw_budget.items()} if budget_total > 0 else {}

        weights = {}
        for ind, idx in g.groupby("industry_name").groups.items():
            budget = industry_budget.get(ind, 0.0)
            if budget <= 0:
                continue
            sub = g.loc[list(idx)].copy()
            take = max(1, min(len(sub), math.ceil(len(sub) * top_frac)))
            picks = sub.sort_values(score_col, ascending=False).head(take)
            score_strength = (picks[score_col] - picks[score_col].min() + 1e-6).to_numpy(dtype=float)
            crowd = np.nan_to_num(picks["turnover_rate"].to_numpy(dtype=float), nan=0.0)
            trend_risk = np.abs(np.nan_to_num(picks["mom20"].to_numpy(dtype=float), nan=0.0))
            risk_penalty = 1.0 + crowd / 18.0 + 1.8 * trend_risk
            raw = np.power(score_strength, 1.20) / np.maximum(risk_penalty, 0.35)
            if raw.sum() <= 0:
                raw = np.ones(len(picks), dtype=float)
            raw = raw / raw.sum() * budget
            for (_, row), weight in zip(picks.iterrows(), raw):
                weights[row["ts_code"]] = weights.get(row["ts_code"], 0.0) + float(weight)

        weights = capped_normalize(weights, single_name_cap)
        turnover = 1.0 if not prev_weights else turnover_from_weights(weights, prev_weights)
        ret_map = dict(zip(g["ts_code"], g["label_next_ret"]))
        gross = sum(weights.get(k, 0.0) * safe_float(ret_map.get(k)) for k in weights)
        bret = float((base * g["label_next_ret"]).sum())
        exposure = report_optimizer_exposure(g, returns, nav, peak, target_ann_vol, max_exposure)
        ret = exposure * (gross - turnover * COST_RATE)
        nav *= 1.0 + ret
        peak = max(peak, nav)
        returns.append(ret)
        bench.append(bret)
        nav_rows.append({"trade_date": date, "nav": nav, "period_return": ret, "benchmark_return": bret, "excess_return": ret - bret})
        ranked = pd.DataFrame({
            "ts_code": list(weights.keys()),
            "target_weight": [exposure * v for v in weights.values()],
        })
        if not ranked.empty:
            ranked = ranked.merge(g[["ts_code", "industry_name", score_col]], on="ts_code", how="left").sort_values("target_weight", ascending=False)
            for rank_no, row in enumerate(ranked.itertuples(index=False), 1):
                signal_rows.append({
                    "trade_date": date,
                    "ts_code": row.ts_code,
                    "industry_name": row.industry_name,
                    "score": float(getattr(row, score_col)),
                    "rank_no": rank_no,
                    "target_weight": float(row.target_weight),
                })
        prev_weights = {k: exposure * v for k, v in weights.items()}
    return returns, bench, nav_rows, signal_rows


def build_etf_panel(conn, start, end, max_months=None):
    dates = [x[0] for x in conn.execute(
        """
        select trade_date from trade_calendar
        where is_trade_day=1 and is_month_last_trade=1 and trade_date between ? and ?
        order by trade_date
        """,
        (start, end),
    ).fetchall()]
    pairs = [(dates[i], dates[i + 1]) for i in range(len(dates) - 1)]
    if max_months:
        pairs = pairs[:max_months]
    panels = []
    for date, next_date in pairs:
        macro = conn.execute(
            """
            select pmi_manufacturing, pmi_non_manufacturing, cpi_national_yoy, ppi_yoy,
                   m1_yoy, m2_yoy, sf_inc_month
            from macro_monthly
            where month<=?
            order by month desc
            limit 1
            """,
            (date[:6],),
        ).fetchone()
        rows = conn.execute(
            """
            select e.ts_code, e.fund_name, coalesce(m.asset_class,'equity') as asset_class,
                   e.close as px, p20.close as px20, p60.close as px60, p120.close as px120,
                   n.close as px_next, e.amount
            from etf_ohlcv_daily e
            left join etf_master m on m.ts_code=e.ts_code
            left join etf_ohlcv_daily p20 on p20.ts_code=e.ts_code and p20.trade_date=(select trade_date from trade_calendar where is_trade_day=1 and trade_date<=? order by trade_date desc limit 1 offset 20)
            left join etf_ohlcv_daily p60 on p60.ts_code=e.ts_code and p60.trade_date=(select trade_date from trade_calendar where is_trade_day=1 and trade_date<=? order by trade_date desc limit 1 offset 60)
            left join etf_ohlcv_daily p120 on p120.ts_code=e.ts_code and p120.trade_date=(select trade_date from trade_calendar where is_trade_day=1 and trade_date<=? order by trade_date desc limit 1 offset 120)
            left join etf_ohlcv_daily n on n.ts_code=e.ts_code and n.trade_date=?
            where e.trade_date=? and e.close>0 and n.close>0 and e.amount is not null
            """,
            (date, date, date, next_date, date),
        ).fetchall()
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["ts_code", "fund_name", "asset_class", "px", "px20", "px60", "px120", "px_next", "amount"])
        for c in ["px", "px20", "px60", "px120", "px_next", "amount"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["label_next_ret"] = df["px_next"] / df["px"] - 1.0
        # Raw ETF close data can contain split/adjustment jumps. Do not allow
        # one bad monthly observation to drive allocation metrics.
        df = df[df["label_next_ret"].between(-0.50, 0.50)].copy()
        if df.empty:
            continue
        df["mom20"] = df["px"] / df["px20"] - 1.0
        df["mom60"] = df["px"] / df["px60"] - 1.0
        df["mom120"] = df["px"] / df["px120"] - 1.0
        if macro:
            pmi_mfg, pmi_non, cpi_yoy, ppi_yoy, m1_yoy, m2_yoy, sf_inc = [safe_float(x) for x in macro]
        else:
            pmi_mfg, pmi_non, cpi_yoy, ppi_yoy, m1_yoy, m2_yoy, sf_inc = 50.0, 50.0, 2.0, 0.0, 5.0, 8.0, 12000.0
        growth_score = clip01(((pmi_mfg + 0.5 * pmi_non) / 1.5 - 48.0) / 7.0)
        credit_score = clip01(0.55 * ((m1_yoy - m2_yoy + 8.0) / 16.0) + 0.45 * ((sf_inc - 8000.0) / 26000.0))
        inflation_pressure = clip01(0.55 * ((cpi_yoy - 1.0) / 5.0) + 0.45 * ((ppi_yoy + 3.0) / 12.0))
        macro_risk_on = clip01(0.44 * growth_score + 0.34 * credit_score + 0.22 * (1.0 - inflation_pressure))
        df["macro_growth_score_v11"] = growth_score
        df["macro_credit_score_v11"] = credit_score
        df["macro_inflation_pressure_v11"] = inflation_pressure
        df["macro_risk_on_v11"] = macro_risk_on
        df["score"] = (
            0.30 * rank01(df["mom20"])
            + 0.28 * rank01(df["mom60"])
            + 0.16 * rank01(df["mom120"])
            + 0.16 * rank01(-abs(df["mom20"] - df["mom60"]))
            + 0.10 * rank01(df["amount"])
        ).fillna(0.5)
        df["risk_on_score_v8"] = (
            0.34 * rank01(df["mom60"])
            + 0.24 * rank01(df["mom120"])
            + 0.18 * rank01(df["mom20"])
            + 0.14 * rank01(df["amount"])
            + 0.10 * rank01(-abs(df["mom20"] - df["mom60"]))
        ).fillna(0.5)
        df["defensive_score_v8"] = (
            0.36 * rank01(-abs(df["mom20"]))
            + 0.28 * rank01(df["mom60"])
            + 0.20 * rank01(df["amount"])
            + 0.16 * rank01(-abs(df["mom20"] - df["mom60"]))
        ).fillna(0.5)
        equity_like = df["asset_class"].isin(["equity", "style_equity", "cross_border"])
        commodity_like = df["asset_class"].eq("commodity")
        bond_like = df["asset_class"].eq("bond_cash")
        df["macro_bl_score_v11"] = (
            np.where(equity_like, 0.52 * df["risk_on_score_v8"] + 0.30 * macro_risk_on + 0.18 * growth_score, 0.0)
            + np.where(commodity_like, 0.42 * df["risk_on_score_v8"] + 0.34 * inflation_pressure + 0.24 * rank01(df["mom60"]), 0.0)
            + np.where(bond_like, 0.42 * df["defensive_score_v8"] + 0.30 * (1.0 - macro_risk_on) + 0.18 * (1.0 - inflation_pressure) + 0.10 * rank01(df["amount"]), 0.0)
        )
        df.loc[~(equity_like | commodity_like | bond_like), "macro_bl_score_v11"] = (
            0.50 * df.loc[~(equity_like | commodity_like | bond_like), "risk_on_score_v8"]
            + 0.30 * macro_risk_on
            + 0.20 * rank01(df.loc[~(equity_like | commodity_like | bond_like), "amount"])
        )
        df["macro_bl_score_v11"] = pd.to_numeric(df["macro_bl_score_v11"], errors="coerce").fillna(0.5)
        df["trade_date"] = date
        panels.append(df)
    return pd.concat(panels, ignore_index=True) if panels else pd.DataFrame()


def etf_budget_by_state(g):
    equity = g[g["asset_class"].isin(["equity", "style_equity", "cross_border"])]
    bond = g[g["asset_class"] == "bond_cash"]
    commodity = g[g["asset_class"] == "commodity"]
    eq_m60 = safe_float(equity["mom60"].median(), 0.0) if len(equity) else 0.0
    eq_m120 = safe_float(equity["mom120"].median(), 0.0) if len(equity) else 0.0
    eq_breadth = safe_float((equity["mom60"] > 0).mean(), 0.5) if len(equity) else 0.5
    cm_m60 = safe_float(commodity["mom60"].median(), 0.0) if len(commodity) else 0.0
    bd_m60 = safe_float(bond["mom60"].median(), 0.0) if len(bond) else 0.0
    if eq_m120 > 0.06 and eq_m60 > 0.02 and eq_breadth > 0.55:
        return {"equity": 0.46, "style_equity": 0.14, "cross_border": 0.10, "commodity": 0.12 if cm_m60 > 0 else 0.06, "bond_cash": 0.18 if bd_m60 >= -0.02 else 0.12}
    if eq_m120 > -0.02 and eq_breadth > 0.42:
        return {"equity": 0.34, "style_equity": 0.10, "cross_border": 0.04, "commodity": 0.10 if cm_m60 > 0 else 0.04, "bond_cash": 0.42}
    return {"equity": 0.12, "style_equity": 0.03, "cross_border": 0.00, "commodity": 0.06 if cm_m60 > 0 else 0.02, "bond_cash": 0.79}


def etf_budget_by_state_v8(g):
    equity = g[g["asset_class"].isin(["equity", "style_equity", "cross_border"])]
    bond = g[g["asset_class"] == "bond_cash"]
    commodity = g[g["asset_class"] == "commodity"]
    eq_m60 = safe_float(equity["mom60"].median(), 0.0) if len(equity) else 0.0
    eq_m120 = safe_float(equity["mom120"].median(), 0.0) if len(equity) else 0.0
    eq_breadth = safe_float((equity["mom60"] > 0).mean(), 0.5) if len(equity) else 0.5
    cm_m60 = safe_float(commodity["mom60"].median(), 0.0) if len(commodity) else 0.0
    bd_m60 = safe_float(bond["mom60"].median(), 0.0) if len(bond) else 0.0
    if eq_m120 > 0.08 and eq_m60 > 0.03 and eq_breadth > 0.58:
        return {
            "equity": 0.44,
            "style_equity": 0.16,
            "cross_border": 0.08,
            "commodity": 0.12 if cm_m60 > 0.00 else 0.04,
            "bond_cash": 0.20 if bd_m60 > -0.02 else 0.14,
        }
    if eq_m120 > -0.01 and eq_breadth > 0.44:
        return {
            "equity": 0.28,
            "style_equity": 0.10,
            "cross_border": 0.04,
            "commodity": 0.10 if cm_m60 > 0.01 else 0.03,
            "bond_cash": 0.48,
        }
    return {
        "equity": 0.08,
        "style_equity": 0.02,
        "cross_border": 0.00,
        "commodity": 0.05 if cm_m60 > 0.02 else 0.01,
        "bond_cash": 0.84,
    }


def etf_budget_by_macro_v11(g):
    equity = g[g["asset_class"].isin(["equity", "style_equity", "cross_border"])]
    commodity = g[g["asset_class"] == "commodity"]
    eq_m120 = safe_float(equity["mom120"].median(), 0.0) if len(equity) else 0.0
    eq_breadth = safe_float((equity["mom60"] > 0).mean(), 0.5) if len(equity) else 0.5
    cm_m60 = safe_float(commodity["mom60"].median(), 0.0) if len(commodity) else 0.0
    risk_on = safe_float(g["macro_risk_on_v11"].iloc[0], 0.5) if "macro_risk_on_v11" in g else 0.5
    inflation = safe_float(g["macro_inflation_pressure_v11"].iloc[0], 0.5) if "macro_inflation_pressure_v11" in g else 0.5
    growth = safe_float(g["macro_growth_score_v11"].iloc[0], 0.5) if "macro_growth_score_v11" in g else 0.5
    if risk_on > 0.62 and eq_m120 > 0.02 and eq_breadth > 0.52:
        return {
            "equity": 0.42,
            "style_equity": 0.18,
            "cross_border": 0.08,
            "commodity": 0.12 if inflation > 0.48 or cm_m60 > 0.02 else 0.05,
            "bond_cash": 0.20,
        }
    if inflation > 0.62 and growth < 0.48:
        return {
            "equity": 0.18,
            "style_equity": 0.05,
            "cross_border": 0.02,
            "commodity": 0.18 if cm_m60 > -0.03 else 0.08,
            "bond_cash": 0.57,
        }
    if risk_on < 0.42 or eq_m120 < -0.08 or eq_breadth < 0.36:
        return {
            "equity": 0.08,
            "style_equity": 0.02,
            "cross_border": 0.00,
            "commodity": 0.07 if inflation > 0.55 and cm_m60 > 0 else 0.02,
            "bond_cash": 0.88,
        }
    return {
        "equity": 0.25,
        "style_equity": 0.10,
        "cross_border": 0.04,
        "commodity": 0.12 if inflation > 0.50 or cm_m60 > 0.01 else 0.05,
        "bond_cash": 0.49,
    }


def etf_exposure_v8(g, trailing_returns, nav, peak):
    equity = g[g["asset_class"].isin(["equity", "style_equity", "cross_border"])]
    eq_m120 = safe_float(equity["mom120"].median(), 0.0) if len(equity) else 0.0
    eq_breadth = safe_float((equity["mom60"] > 0).mean(), 0.5) if len(equity) else 0.5
    exposure = 1.0
    if eq_m120 < -0.08 or eq_breadth < 0.34:
        exposure = 0.65
    elif eq_m120 < -0.02 or eq_breadth < 0.42:
        exposure = 0.82
    if len(trailing_returns) >= 6:
        realized = float(np.std(trailing_returns[-12:])) if len(trailing_returns[-12:]) > 1 else 0.0
        target = 0.11 / math.sqrt(12.0)
        if realized > 0:
            exposure = min(exposure, max(0.55, target / realized))
    drawdown = nav / peak - 1.0 if peak else 0.0
    if drawdown < -0.12:
        exposure = min(exposure, 0.55)
    elif drawdown < -0.08:
        exposure = min(exposure, 0.75)
    return exposure


def etf_exposure_v11(g, trailing_returns, nav, peak):
    base = etf_exposure_v8(g, trailing_returns, nav, peak)
    risk_on = safe_float(g["macro_risk_on_v11"].iloc[0], 0.5) if "macro_risk_on_v11" in g else 0.5
    inflation = safe_float(g["macro_inflation_pressure_v11"].iloc[0], 0.5) if "macro_inflation_pressure_v11" in g else 0.5
    exposure = base
    if risk_on > 0.65 and inflation < 0.70:
        exposure = min(1.08, max(exposure, 0.92))
    elif risk_on < 0.38:
        exposure = min(exposure, 0.70)
    if inflation > 0.72 and risk_on < 0.52:
        exposure = min(exposure, 0.78)
    if len(trailing_returns) >= 12:
        realized = float(np.std(trailing_returns[-12:]))
        target = 0.10 / math.sqrt(12.0)
        if realized > 0:
            exposure = min(exposure, max(0.50, target / realized))
    return max(0.35, min(1.08, exposure))


def backtest_etf(panel, mode="risk_budget"):
    returns, bench, nav_rows, signal_rows = [], [], [], []
    nav = 1.0
    peak = 1.0
    prev_weights = {}
    for date, g in panel.groupby("trade_date", sort=True):
        selected = []
        budgets = (
            etf_budget_by_macro_v11(g)
            if mode == "macro_bl_v11"
            else etf_budget_by_state_v8(g)
            if mode == "bl_risk_budget_v8"
            else etf_budget_by_state(g)
            if mode == "risk_budget"
            else {}
        )
        weights = {}
        if mode in {"risk_budget", "bl_risk_budget_v8", "macro_bl_v11"}:
            for asset, budget in budgets.items():
                if budget <= 0:
                    continue
                if mode == "macro_bl_v11":
                    local_score = "macro_bl_score_v11"
                else:
                    local_score = "risk_on_score_v8" if mode == "bl_risk_budget_v8" and asset != "bond_cash" else "defensive_score_v8" if mode == "bl_risk_budget_v8" else "score"
                gg = g[g["asset_class"] == asset].dropna(subset=[local_score, "label_next_ret"])
                if gg.empty:
                    continue
                take = 2 if asset in {"equity", "bond_cash"} and len(gg) >= 2 else 1
                picks = gg.sort_values(local_score, ascending=False).head(take)
                raw = np.power((picks[local_score] - picks[local_score].min() + 1e-6).to_numpy(dtype=float), 1.10)
                if mode == "bl_risk_budget_v8":
                    risk_penalty = 1.0 + 4.0 * np.abs(np.nan_to_num(picks["mom20"].to_numpy(dtype=float), nan=0.0))
                    raw = raw / np.maximum(risk_penalty, 0.35)
                if raw.sum() <= 0:
                    raw = np.ones(len(picks), dtype=float)
                raw = raw / raw.sum() * budget
                for (_, row), w in zip(picks.iterrows(), raw):
                    row = row.copy()
                    row["score"] = row[local_score]
                    selected.append(row)
                    weights[row["ts_code"]] = float(w)
            total = sum(weights.values())
            weights = {k: v / total for k, v in weights.items()} if total > 0 else {}
        else:
            for asset, gg in g.groupby("asset_class"):
                row = gg.sort_values("score", ascending=False).iloc[0]
                selected.append(row)
            selected = sorted(selected, key=lambda x: x["score"], reverse=True)[:5]
            weights = {x["ts_code"]: 1.0 / max(len(selected), 1) for x in selected}
        turnover = 1.0 if not prev_weights else turnover_from_weights(weights, prev_weights)
        gross = sum(weights.get(x["ts_code"], 0.0) * safe_float(x["label_next_ret"]) for x in selected) if selected else 0.0
        ret = gross - turnover * COST_RATE
        if mode == "bl_risk_budget_v8":
            ret *= etf_exposure_v8(g, returns, nav, peak)
        if mode == "macro_bl_v11":
            ret *= etf_exposure_v11(g, returns, nav, peak)
        bret = float(g["label_next_ret"].mean()) if len(g) else 0.0
        nav *= 1.0 + ret
        peak = max(peak, nav)
        returns.append(ret)
        bench.append(bret)
        nav_rows.append({"trade_date": date, "nav": nav, "period_return": ret, "benchmark_return": bret, "excess_return": ret - bret})
        for i, x in enumerate(selected, 1):
            signal_rows.append({"trade_date": date, "ts_code": x["ts_code"], "industry_name": x["asset_class"], "score": float(x["score"]), "rank_no": i, "target_weight": weights.get(x["ts_code"], 0.0)})
        prev_weights = weights
    return returns, bench, nav_rows, signal_rows


def write_model(conn, run_id, universe, model_name, nav_rows, signal_rows, returns, bench):
    conn.execute("delete from backtest_nav where run_id=? and universe=? and model_name=?", (run_id, universe, model_name))
    conn.execute("delete from metrics_by_split_year where run_id=? and universe=? and model_name=?", (run_id, universe, model_name))
    conn.execute("delete from portfolio_target_daily where universe=? and model_name=?", (universe, model_name))
    conn.execute("delete from model_signal_daily where universe=? and model_name=?", (universe, model_name))
    rows = []
    for split_name, (a, b) in SPLITS.items():
        nav = 1.0
        for nr in nav_rows:
            if a <= nr["trade_date"] <= b:
                nav *= 1.0 + nr["period_return"]
                rows.append((run_id, universe, model_name, split_name, nr["trade_date"], nav, nr["period_return"], nr["benchmark_return"], nr["excess_return"]))
    conn.executemany(
        """
        insert or replace into backtest_nav
        (run_id, universe, model_name, split_name, trade_date, nav, period_return, benchmark_return, excess_return)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.executemany(
        """
        insert or replace into portfolio_target_daily
        (trade_date, universe, model_name, ts_code, target_weight, score, industry_name)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        [(r["trade_date"], universe, model_name, r["ts_code"], r["target_weight"], r["score"], r["industry_name"]) for r in signal_rows],
    )
    conn.executemany(
        """
        insert or replace into model_signal_daily
        (trade_date, universe, model_name, ts_code, industry_name, score, rank_no, target_weight)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(r["trade_date"], universe, model_name, r["ts_code"], r["industry_name"], r["score"], r["rank_no"], r["target_weight"]) for r in signal_rows],
    )
    metric_rows = []
    date_ret = [(nr["trade_date"], nr["period_return"], nr["benchmark_return"]) for nr in nav_rows]
    for split_name, (a, b) in SPLITS.items():
        rr = [r for d, r, _ in date_ret if a <= d <= b]
        bb = [x for d, _, x in date_ret if a <= d <= b]
        m = metrics_from_returns(rr, bb)
        metric_rows.append((run_id, universe, model_name, split_name, "all", m["periods"], m["total_return"], m["annual_return"], m["annual_volatility"], m["sharpe"], m["max_drawdown"], m["win_rate"], m["excess_annual_return"], m["information_ratio"], m["target_pass"], json.dumps(m)))
    for year in range(2012, 2027):
        rr = [r for d, r, _ in date_ret if f"{year}0101" <= d <= f"{year}1231"]
        bb = [x for d, _, x in date_ret if f"{year}0101" <= d <= f"{year}1231"]
        if rr:
            m = metrics_from_returns(rr, bb)
            metric_rows.append((run_id, universe, model_name, "year", str(year), m["periods"], m["total_return"], m["annual_return"], m["annual_volatility"], m["sharpe"], m["max_drawdown"], m["win_rate"], m["excess_annual_return"], m["information_ratio"], m["target_pass"], json.dumps(m)))
    conn.executemany(
        """
        insert or replace into metrics_by_split_year
        (run_id, universe, model_name, split_name, year, periods, total_return, annual_return, annual_volatility,
         sharpe, max_drawdown, win_rate, excess_annual_return, information_ratio, target_pass, message)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        metric_rows,
    )
    conn.commit()
    return metric_rows


def write_blocked_metric(conn, run_id, universe, model_name, reason):
    conn.execute("delete from metrics_by_split_year where run_id=? and universe=? and model_name=?", (run_id, universe, model_name))
    conn.execute(
        """
        insert or replace into metrics_by_split_year
        (run_id, universe, model_name, split_name, year, periods, total_return, annual_return, annual_volatility,
         sharpe, max_drawdown, win_rate, excess_annual_return, information_ratio, target_pass, message)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, universe, model_name, "blocked", "all", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, reason),
    )
    conn.commit()


def run(db, project_root, out_dir, allow_incomplete=False, max_months=None):
    run_id = RUN_ID if max_months is None else f"{RUN_ID}_smoke_{max_months}m"
    quality = read_quality(project_root, db)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data_quality_gate.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    blocked_path = out / "model_run_blocked.json"
    if blocked_path.exists() and (quality["status"] == "ready" or allow_incomplete):
        blocked_path.unlink()
    if quality["status"] != "ready" and not allow_incomplete:
        payload = {"status": "blocked", "reason": "data quality gate is blocked", "blocked_checks": [x for x in quality["checks"] if x["status"] == "blocked"]}
        (out / "model_run_blocked.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout=120000")
    miner = load_factor_miner(project_root)
    leaderboard = []
    learning_diagnostics = {}
    for universe in ["ALL_A", "CSI800_ENH", "CSI2000_ENH"]:
        stock_models = {**STOCK_MODELS, "factor_ic_learned_agent_v7": STRUCTURAL_MODELS["factor_ic_learned_agent_v7"], **SPECIAL_STOCK_MODELS.get(universe, {})}
        report_style_models = {
            name: cfg
            for name, cfg in REPORT_STYLE_MODELS.items()
            if not cfg.get("index_only") or universe != "ALL_A"
        }
        universe_start = max(START_DATE, UNIVERSE_INCEPTION.get(universe, START_DATE))
        panel, reason = build_stock_panel(conn, miner, universe, universe_start, END_DATE if not allow_incomplete else min(END_DATE, conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()[0]), max_months)
        if panel is None:
            for model in stock_models:
                write_blocked_metric(conn, run_id, universe, model, reason)
                leaderboard.append({"universe": universe, "model": model, "status": "blocked", "reason": reason})
            for model in report_style_models:
                write_blocked_metric(conn, run_id, universe, model, reason)
                leaderboard.append({"universe": universe, "model": model, "status": "blocked", "reason": reason})
            if universe != "ALL_A":
                write_blocked_metric(conn, run_id, universe, "industry_budget_ic_optimizer_v7", reason)
                leaderboard.append({"universe": universe, "model": "industry_budget_ic_optimizer_v7", "status": "blocked", "reason": reason})
                for model in INDEX_ENH_MODELS:
                    write_blocked_metric(conn, run_id, universe, model, reason)
                    leaderboard.append({"universe": universe, "model": model, "status": "blocked", "reason": reason})
            continue
        panel, learned_diag = add_ic_learned_alpha(panel)
        panel, walkforward_diag = add_walkforward_ic_alpha(panel)
        panel = add_v11_alpha_scores(panel)
        learning_diagnostics[universe] = {
            "static_ic_learning_v7": learned_diag,
            "walkforward_ic_learning_v10": walkforward_diag,
            "v11_structural_alpha": {
                "scores": [
                    "kline_executable_skill_v11",
                    "factor_auto_miner_v11",
                    "hierarchical_alpha_v11",
                    "csi800_cashflow_quality_v11",
                    "csi800_regime_barbell_v11",
                ],
                "uses_future_return": False,
            },
        }
        for model, cfg in stock_models.items():
            rets, bench, nav_rows, sig_rows = backtest_stock_panel(
                panel,
                universe,
                model,
                cfg["score"],
                cfg["n"],
                cfg["industry_cap"],
                cfg.get("regime"),
                cfg.get("weight_mode", "equal"),
            )
            write_model(conn, run_id, universe, model, nav_rows, sig_rows, rets, bench)
            m = metrics_from_returns(rets, bench)
            leaderboard.append({"universe": universe, "model": model, "status": "ready", **m})
        for model, cfg in report_style_models.items():
            rets, bench, nav_rows, sig_rows = backtest_report_style_optimizer_panel(
                panel,
                universe,
                model,
                cfg["score"],
                cfg["top_frac"],
                cfg["industry_tilt"],
                cfg["target_ann_vol"],
                cfg["max_exposure"],
                cfg["single_name_cap"],
            )
            write_model(conn, run_id, universe, model, nav_rows, sig_rows, rets, bench)
            m = metrics_from_returns(rets, bench)
            leaderboard.append({"universe": universe, "model": model, "status": "ready", **m})
        if universe != "ALL_A":
            model = "industry_budget_ic_optimizer_v7"
            cfg = STRUCTURAL_MODELS[model]
            rets, bench, nav_rows, sig_rows = backtest_industry_budget_panel(
                panel,
                universe,
                model,
                cfg["score"],
                cfg["top_frac"],
                cfg.get("regime"),
            )
            write_model(conn, run_id, universe, model, nav_rows, sig_rows, rets, bench)
            m = metrics_from_returns(rets, bench)
            leaderboard.append({"universe": universe, "model": model, "status": "ready", **m})
        if universe != "ALL_A":
            for model, cfg in INDEX_ENH_MODELS.items():
                rets, bench, nav_rows, sig_rows = backtest_index_enhancement_panel(
                    panel,
                    universe,
                    model,
                    cfg["score"],
                    cfg["top_q"],
                    cfg["active_weight"],
                    cfg["cap_mult"],
                    cfg.get("min_member_weight", 0.0),
                    cfg.get("regime"),
                )
                write_model(conn, run_id, universe, model, nav_rows, sig_rows, rets, bench)
                m = metrics_from_returns(rets, bench)
                leaderboard.append({"universe": universe, "model": model, "status": "ready", **m})

    etf_panel = build_etf_panel(conn, START_DATE, END_DATE if not allow_incomplete else min(END_DATE, conn.execute("select max(trade_date) from etf_ohlcv_daily").fetchone()[0]), max_months)
    if etf_panel.empty:
        write_blocked_metric(conn, run_id, "MULTI_ASSET_ETF", "asset_allocation_etf", "ETF panel empty or coverage blocked")
        leaderboard.append({"universe": "MULTI_ASSET_ETF", "model": "asset_allocation_etf", "status": "blocked", "reason": "ETF panel empty or coverage blocked"})
    else:
        for model_name, mode in [
            ("asset_allocation_etf", "simple"),
            ("asset_allocation_etf_v4", "risk_budget"),
            ("asset_allocation_bl_risk_v8", "bl_risk_budget_v8"),
            ("asset_allocation_macro_bl_v11", "macro_bl_v11"),
        ]:
            rets, bench, nav_rows, sig_rows = backtest_etf(etf_panel, mode=mode)
            write_model(conn, run_id, "MULTI_ASSET_ETF", model_name, nav_rows, sig_rows, rets, bench)
            m = metrics_from_returns(rets, bench)
            leaderboard.append({"universe": "MULTI_ASSET_ETF", "model": model_name, "status": "ready", **m})
    conn.close()

    df = pd.DataFrame(leaderboard)
    df.to_csv(out / "model_leaderboard.csv", index=False, encoding="utf-8-sig")
    payload = {"status": "ready" if any(x.get("status") == "ready" for x in leaderboard) else "blocked", "run_id": run_id, "leaderboard": leaderboard, "allow_incomplete": allow_incomplete}
    (out / "model_run_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "factor_ic_learning_diagnostics.json").write_text(json.dumps(learning_diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--out-dir", default=str(DEFAULT_PROJECT_ROOT / "output" / "framework" / "backtest" / "model_outputs"))
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--max-months", type=int, default=None)
    args = parser.parse_args()
    payload = run(args.db, args.project_root, args.out_dir, args.allow_incomplete, args.max_months)
    print(json.dumps({"status": payload["status"], "run_id": payload.get("run_id"), "allow_incomplete": payload.get("allow_incomplete"), "out_dir": args.out_dir}, ensure_ascii=False))


if __name__ == "__main__":
    main()
