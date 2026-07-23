import argparse
import bisect
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
import time
import warnings
from collections import defaultdict
from itertools import combinations, product
from pathlib import Path
from statistics import NormalDist
from urllib import error as urlerror
from urllib import request as urlrequest

import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning


warnings.simplefilter("ignore", PerformanceWarning)


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_AGENT = "05_factor_mining_agent"
DEFAULT_MODEL_NAME = os.environ.get("FACTOR_MINING_LLM_MODEL", "gpt-5.5")
DEFAULT_REASONING_EFFORT = os.environ.get("FACTOR_MINING_REASONING_EFFORT", "xhigh")
VENDOR_FINANCE_API = DEFAULT_PROJECT_ROOT / "environment" / "vendor" / "finance_api"
if VENDOR_FINANCE_API.exists() and str(VENDOR_FINANCE_API) not in sys.path:
    sys.path.insert(0, str(VENDOR_FINANCE_API))

SPLITS = {
    "train": ("20120101", "20201231"),
    "valid": ("20210101", "20221231"),
    "test": ("20230101", "20260630"),
    "full": ("20120101", "20260630"),
}
LABEL_HORIZON_PERIODS = 1
MIN_MEMBER_COUNTS = {
    "CSI800_ENH": 600,
    "CSI2000_ENH": 1600,
}
DEFAULT_GATES = {
    "train_rank_ic_min": 0.025,
    "valid_rank_ic_min": 0.02,
    "test_rank_ic_min": 0.02,
    "train_group_spread_min": 0.003,
    "valid_group_spread_min": 0.002,
    "coverage_min": 0.65,
    "max_redundancy": 0.82,
    "novelty_max_corr": 0.65,
    "min_group_monotonicity": 0.75,
    "max_turnover": 0.85,
    "min_backtest_sharpe": 0.60,
    "long_only_excess_return_min": 0.03,
    "long_only_information_ratio_min": 0.35,
    "long_only_max_drawdown_min": -0.30,
    "market_neutral_rank_ic_min": 0.02,
    "market_neutral_sharpe_min": 0.60,
    "market_neutral_annual_return_min": 0.06,
    "market_neutral_max_drawdown_min": -0.18,
    "market_neutral_win_rate_min": 0.52,
    "max_pbo_proxy": 0.50,
    "cscv_overfit_posterior_trigger": 0.80,
    "purged_oos_rank_percentile_min": 0.50,
    "purged_absolute_positive_ratio_min": 0.80,
    "risk_adjusted_selection_score_min": 0.0,
    "deflated_sharpe_confidence_min": 0.60,
}

FLOW_STEPS = [
    "抽取 AI 因子挖掘方法卡",
    "构建数据空间、算子空间、约束空间",
    "LLM 生成候选假设",
    "编译成可执行因子程序",
    "静态审计",
    "批量计算因子值",
    "快速初筛",
    "完整单因子检验",
    "组合回测",
    "裁判打分",
    "失败归因",
    "智能变异",
    "更新记忆库",
    "下一轮搜索",
    "直到找到合格因子",
]

DEEP_METHOD_CARDS = [
    {
        "method": "HIST_graph_hidden_concept",
        "source": "HIST / concept-oriented graph stock forecasting",
        "implementation": "industry graph + hidden concept cluster shared-information residual factor",
    },
    {
        "method": "MASTER_market_guided_transformer",
        "source": "MASTER / market-guided stock transformer",
        "implementation": "market-regime router + feature attention mixture factor",
    },
    {
        "method": "RVRAE_dynamic_factor_autoencoder",
        "source": "RVRAE / recurrent variational autoencoder dynamic factor model",
        "implementation": "variational bottleneck latent score and reconstruction-residual stability factor",
    },
    {
        "method": "Temporal_relational_ranking",
        "source": "temporal relational ranking for stock prediction",
        "implementation": "industry and hidden-relation rank propagation before single-factor evaluation",
    },
    {
        "method": "TabNet_sparse_feature_gate",
        "source": "TabNet-style attentive tabular representation learning",
        "implementation": "train-period IC guided sparse feature masks with nonlinear gated interaction factor",
    },
    {
        "method": "TCN_sequence_alpha_encoder",
        "source": "temporal convolutional network for sequential stock representation",
        "implementation": "multi-horizon momentum and reversal convolutional filters routed by train-period stability",
    },
    {
        "method": "Contrastive_regime_representation",
        "source": "contrastive representation learning for regime-aware stock ranking",
        "implementation": "separate bull/bear and high/low-volatility cross-sectional embeddings, then preserve regime-stable rank signal",
    },
]

DATA_SPACE = {
    "price_volume": ["ret1", "mom5", "mom20", "mom60", "mom120", "mom252", "range_pct", "gap_pct", "amount_to_mv"],
    "valuation": ["pb", "pe_ttm", "ps_ttm", "dv_ttm", "total_mv", "circ_mv"],
    "fundamental_visible": ["roe", "roa", "gross_margin", "netprofit_yoy", "debt_to_assets", "netprofit_margin", "current_ratio", "assets_turn", "op_yoy", "tr_yoy"],
    "moneyflow": ["netflow_intensity", "large_order_balance", "net_mf_amount", "buy_lg_amount", "sell_lg_amount"],
    "event_text_proxy": ["event_attention", "event_risk", "base_event_attention", "base_event_risk"],
    "cross_section": ["industry_name", "total_mv", "turnover_rate", "turnover_rate_f", "volume_ratio"],
}

OPERATOR_SPACE = {
    "cross_section": ["rank", "industry_rank", "zscore", "winsorize", "neutralize_size_industry"],
    "formula": ["add", "sub", "mul", "div", "neg", "clip01", "ts_delta", "ts_mean", "ts_std", "ts_zscore", "soft_gate"],
    "auto_feature": ["rank_multiply", "main_effect_plus_interaction", "regime_gate", "graph_residual"],
    "deep_model": ["deep_supervised_ranker", "deep_transformer_attention", "deep_vae_bottleneck", "graph_concept_residual", "regime_mixture_expert", "deep_tabnet_sparse_gate", "deep_temporal_convolution", "deep_contrastive_regime", "deep_svd"],
    "search": ["llm_hypothesis_generation", "llm_feedback_mutation", "mcts_tree_seed", "mcts_feedback_tree_search", "genetic_crossover", "openfe_feature_search", "rl_bandit_policy", "window_structure_search"],
}

CONSTRAINT_SPACE = {
    "leakage_guard": ["visible_date <= trade_date", "label_next_ret forbidden in features", "train-only supervised fitting", "one_period_label_embargo", "strict_post_train_oos_validation"],
    "anti_overfit": ["sealed_test_search_policy", "train_valid_test_split", "walk_forward", "purged_kfold", "cscv_pbo", "deflated_sharpe", "alpha_lifecycle_diagnostics", "complexity_penalty", "redundancy_penalty", "sample_decay_penalty"],
    "tradability": ["coverage_min", "turnover_max", "transaction_cost", "limit_suspend_proxy", "liquidity_proxy"],
    "novelty": ["spearman_redundancy", "ast_similarity", "dual_residual_incremental_ic", "downstream_synergy", "quality_diversity_islands", "failure_memory_mutation"],
}


def safe_float(x, default=np.nan):
    try:
        if x is None or x == "":
            return default
        out = float(x)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def clean_name(text, max_len=72):
    out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(text).lower())
    out = "_".join(x for x in out.split("_") if x)
    return out[:max_len] if len(out) > max_len else out


def stable_id(prefix, payload, n=10):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:n]}"


def env_present(name):
    return bool(os.environ.get(name))


def package_available(name):
    try:
        import importlib.util

        return bool(importlib.util.find_spec(name))
    except Exception:
        return False


def max_drawdown(nav):
    peak = nav[0] if nav else 1.0
    out = 0.0
    for value in nav:
        peak = max(peak, value)
        out = min(out, value / peak - 1.0 if peak else 0.0)
    return out


def nav_drawdown_series(rets):
    nav = []
    drawdown = []
    value = 1.0
    peak = 1.0
    for ret in [safe_float(x, 0.0) for x in rets]:
        value *= 1.0 + ret
        peak = max(peak, value)
        nav.append(value)
        drawdown.append(value / peak - 1.0 if peak else 0.0)
    return nav, drawdown


def metrics_from_returns(rets, bench_rets=None, periods_per_year=12):
    rets = [safe_float(x, 0.0) for x in rets]
    periods_per_year = max(1.0, safe_float(periods_per_year, 12.0))
    bench_rets = [safe_float(x, 0.0) for x in (bench_rets or [0.0] * len(rets))]
    nav = [1.0]
    for ret in rets:
        nav.append(nav[-1] * (1.0 + ret))
    periods = len(rets)
    total = nav[-1] - 1.0
    annual = nav[-1] ** (periods_per_year / periods) - 1.0 if periods and nav[-1] > 0 else 0.0
    vol = float(np.std(rets)) * math.sqrt(periods_per_year) if periods else 0.0
    sharpe = annual / vol if vol else 0.0
    excess = [a - b for a, b in zip(rets, bench_rets)]
    ex_annual = (1.0 + float(np.mean(excess))) ** periods_per_year - 1.0 if excess else 0.0
    ir_vol = float(np.std(excess)) * math.sqrt(periods_per_year) if len(excess) > 1 else 0.0
    return {
        "periods": periods,
        "periods_per_year": float(periods_per_year),
        "total_return": total,
        "annual_return": annual,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown(nav),
        "win_rate": sum(1 for x in rets if x > 0) / periods if periods else 0.0,
        "excess_annual_return": ex_annual,
        "information_ratio": ex_annual / ir_vol if ir_vol else 0.0,
    }


def first_metric(obj, *keys, default=0.0):
    if not isinstance(obj, dict):
        return default
    for key in keys:
        value = obj.get(key)
        if value is not None:
            return safe_float(value, default)
    return default


def newey_west_tstat(values, lags=3):
    vals = np.array([safe_float(x, np.nan) for x in values], dtype=float)
    vals = vals[np.isfinite(vals)]
    n = len(vals)
    if n < 3:
        return 0.0
    demeaned = vals - float(np.mean(vals))
    max_lag = min(int(lags), n - 1)
    gamma0 = float(np.dot(demeaned, demeaned) / n)
    var = gamma0
    for lag in range(1, max_lag + 1):
        gamma = float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
        var += 2.0 * (1.0 - lag / (max_lag + 1.0)) * gamma
    if var <= 0:
        return 0.0
    se = math.sqrt(var / n)
    return float(np.mean(vals) / se) if se else 0.0


def build_backtest_curve(bt):
    if bt.empty:
        return []
    long_nav, long_dd = nav_drawdown_series(bt["long_return"].tolist())
    long_short_nav, long_short_dd = nav_drawdown_series(bt["long_short_return"].tolist())
    benchmark_nav, benchmark_dd = nav_drawdown_series(bt["benchmark_return"].tolist())
    curve = []
    for i, row in enumerate(bt.to_dict("records")):
        curve.append({
            "date": str(row.get("trade_date", "")),
            "long_nav": float(long_nav[i]),
            "benchmark_nav": float(benchmark_nav[i]),
            "long_short_nav": float(long_short_nav[i]),
            "long_drawdown": float(long_dd[i]),
            "benchmark_drawdown": float(benchmark_dd[i]),
            "long_short_drawdown": float(long_short_dd[i]),
            "long_return": safe_float(row.get("long_return", 0.0), 0.0),
            "benchmark_return": safe_float(row.get("benchmark_return", 0.0), 0.0),
            "long_short_return": safe_float(row.get("long_short_return", 0.0), 0.0),
            "turnover": safe_float(row.get("turnover", 0.0), 0.0),
            "long_turnover": safe_float(row.get("long_turnover", 0.0), 0.0),
            "short_turnover": safe_float(row.get("short_turnover", 0.0), 0.0),
        })
    return curve


def rank01(s, ascending=True):
    vals = pd.to_numeric(s, errors="coerce")
    if vals.notna().sum() <= 1:
        return pd.Series(0.5, index=s.index)
    return vals.rank(pct=True, ascending=ascending).fillna(0.5)


def zscore(s):
    vals = pd.to_numeric(s, errors="coerce")
    std = vals.std(ddof=0)
    if not std or not np.isfinite(std):
        return pd.Series(0.0, index=s.index)
    return ((vals - vals.mean()) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def winsorize_by_date(panel, col, lower=0.01, upper=0.99):
    def _clip(vals):
        vals = pd.to_numeric(vals, errors="coerce")
        if vals.notna().sum() < 10:
            return vals
        lo, hi = vals.quantile(lower), vals.quantile(upper)
        return vals.clip(lo, hi)

    return panel.groupby("trade_date")[col].transform(_clip)


def normalize_ts_code(value):
    text = str(value).strip().upper()
    if text.endswith(".XSHG"):
        return text.replace(".XSHG", ".SH")
    if text.endswith(".XSHE"):
        return text.replace(".XSHE", ".SZ")
    return text


def normalize_trade_date(value):
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    text = text.replace("-", "").replace("/", "").replace(".", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else digits


def table_count(conn, universe):
    return conn.execute(
        "select count(*), min(trade_date), max(trade_date) from index_constituent_period where universe=?",
        (universe,),
    ).fetchone()


def required_trade_bounds(conn, start, end):
    row = conn.execute(
        """
        select min(trade_date), max(trade_date)
        from trade_calendar
        where is_trade_day=1 and trade_date between ? and ?
        """,
        (start, end),
    ).fetchone()
    return row[0] or start, row[1] or end


def month_dates(conn, start, end):
    rows = conn.execute(
        """
        select trade_date from trade_calendar
        where is_trade_day=1 and is_month_last_trade=1 and trade_date between ? and ?
        order by trade_date
        """,
        (start, end),
    ).fetchall()
    dates = [r[0] for r in rows]
    return [(dates[i], dates[i + 1]) for i in range(len(dates) - 1)]


def select_month_pairs(pairs, max_months=None):
    if not max_months or max_months >= len(pairs):
        return pairs
    max_months = max(3, int(max_months))
    selected = []
    used = set()
    split_names = ["train", "valid", "test"]
    quota = max(1, max_months // len(split_names))
    remainder = max_months - quota * len(split_names)
    quotas = {name: quota + (1 if i < remainder else 0) for i, name in enumerate(split_names)}
    for split_name in split_names:
        a, b = SPLITS[split_name]
        bucket = [p for p in pairs if a <= p[0] <= b]
        if not bucket:
            continue
        take = min(len(bucket), quotas[split_name])
        if take >= len(bucket):
            chosen = bucket
        else:
            idxs = np.linspace(0, len(bucket) - 1, take).round().astype(int).tolist()
            chosen = [bucket[i] for i in sorted(set(idxs))]
        for p in chosen:
            if p[0] not in used:
                selected.append(p)
                used.add(p[0])
    if len(selected) < max_months:
        remaining = [p for p in pairs if p[0] not in used]
        if remaining:
            idxs = np.linspace(0, len(remaining) - 1, max_months - len(selected)).round().astype(int).tolist()
            for i in sorted(set(idxs)):
                p = remaining[i]
                if p[0] not in used:
                    selected.append(p)
                    used.add(p[0])
    return sorted(selected, key=lambda x: x[0])[:max_months]


def offset_trade_date(conn, date, offset):
    row = conn.execute(
        """
        select trade_date from trade_calendar
        where is_trade_day=1 and trade_date<=?
        order by trade_date desc
        limit 1 offset ?
        """,
        (date, int(offset)),
    ).fetchone()
    return row[0] if row else date


def latest_member_date(conn, universe, date):
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


def get_members(conn, universe, date):
    member_date = date if universe == "ALL_A" else latest_member_date(conn, universe, date)
    if not member_date:
        return []
    return conn.execute(
        "select con_code, coalesce(weight, 0) from index_constituent_period where universe=? and trade_date=?",
        (universe, member_date),
    ).fetchall()


def load_financial(conn):
    data = defaultdict(list)
    rows = conn.execute(
        """
        select ts_code, visible_date, roe, roa, gross_margin, netprofit_yoy,
               debt_to_assets, netprofit_margin, current_ratio, assets_turn,
               op_yoy, tr_yoy, total_revenue, n_income_attr_p
        from financial_report_visible
        where visible_date is not null
        order by ts_code, visible_date
        """
    ).fetchall()
    for r in rows:
        data[r[0]].append({
            "visible_date": r[1],
            "roe": safe_float(r[2]),
            "roa": safe_float(r[3]),
            "gross_margin": safe_float(r[4]),
            "netprofit_yoy": safe_float(r[5]),
            "debt_to_assets": safe_float(r[6]),
            "netprofit_margin": safe_float(r[7]),
            "current_ratio": safe_float(r[8]),
            "assets_turn": safe_float(r[9]),
            "op_yoy": safe_float(r[10]),
            "tr_yoy": safe_float(r[11]),
            "total_revenue": safe_float(r[12]),
            "n_income_attr_p": safe_float(r[13]),
        })
    dates = {k: [x["visible_date"] for x in v] for k, v in data.items()}
    return data, dates


def latest_fin(fin, fin_dates, code, date):
    dates = fin_dates.get(code, [])
    pos = bisect.bisect_right(dates, date) - 1
    if pos < 0:
        return {}
    return fin[code][pos]


def load_event_features(conn, date, lookback=60):
    start = offset_trade_date(conn, date, lookback)
    rows = conn.execute(
        """
        select subject_code,
               count(*) as event_count,
               sum(case when event_tag='earnings' then 1 else 0 end) as earnings_count,
               sum(case when event_tag='risk_event' then 1 else 0 end) as risk_count,
               sum(case when event_tag in ('major_event','trading_event') then 1 else 0 end) as trading_count,
               sum(case when event_tag='AI' then 1 else 0 end) as ai_count
        from news_event_daily
        where subject_type='stock'
          and publish_date between ? and ?
        group by subject_code
        """,
        (start, date),
    ).fetchall()
    out = {}
    for code, cnt, earn, risk, trading, ai in rows:
        out[normalize_ts_code(code)] = {
            "event_count_60": safe_float(cnt, 0.0),
            "earnings_event_count_60": safe_float(earn, 0.0),
            "risk_event_count_60": safe_float(risk, 0.0),
            "trading_event_count_60": safe_float(trading, 0.0),
            "ai_event_count_60": safe_float(ai, 0.0),
        }
    return out


def load_kline_feature_summary(conn, date):
    rows = conn.execute(
        """
        select ts_code,
               avg(feature_value) as kline_feature_mean,
               count(*) as kline_feature_count,
               sum(case when signal_direction='bullish' then 1 when signal_direction='bearish' then -1 else 0 end) as kline_signal_balance
        from kline_feature_daily
        where trade_date=?
        group by ts_code
        """,
        (date,),
    ).fetchall()
    return {
        normalize_ts_code(code): {
            "kline_feature_mean": safe_float(mean),
            "kline_feature_count": safe_float(cnt, 0.0),
            "kline_signal_balance": safe_float(balance, 0.0),
        }
        for code, mean, cnt, balance in rows
    }


def fetch_panel(conn, universe, date, next_date, fin, fin_dates):
    members = get_members(conn, universe, date)
    if not members:
        return pd.DataFrame()
    conn.execute("drop table if exists temp_factor_members")
    conn.execute("create temp table temp_factor_members(ts_code text primary key, weight real)")
    conn.executemany("insert or replace into temp_factor_members(ts_code, weight) values (?, ?)", members)
    d1 = offset_trade_date(conn, date, 1)
    d5 = offset_trade_date(conn, date, 5)
    d20 = offset_trade_date(conn, date, 20)
    d60 = offset_trade_date(conn, date, 60)
    d120 = offset_trade_date(conn, date, 120)
    d252 = offset_trade_date(conn, date, 252)
    rows = conn.execute(
        """
        select
          o.ts_code, o.stock_name, o.open, o.high, o.low, o.close, o.qfq_close as px,
          o.pre_close, o.pct_chg, o.vol, o.amount, o.up_limit, o.down_limit, o.suspend_timing,
          p1.qfq_close as px1, p5.qfq_close as px5, p20.qfq_close as px20,
          p60.qfq_close as px60, p120.qfq_close as px120, p252.qfq_close as px252,
          e.trade_date as entry_trade_date,
          e.qfq_close * e.open / nullif(e.close, 0) as px_entry,
          n.trade_date as exit_trade_date,
          n.qfq_close as px_next,
          v.pb, v.pe_ttm, v.ps_ttm, v.dv_ttm, v.total_mv, v.circ_mv,
          v.turnover_rate, v.turnover_rate_f, v.volume_ratio,
          m.net_mf_amount, m.buy_lg_amount, m.sell_lg_amount, m.buy_elg_amount, m.sell_elg_amount,
          ind.industry_name,
          tm.weight
        from temp_factor_members tm
        join stock_ohlcv_daily o on o.ts_code=tm.ts_code and o.trade_date=?
        left join stock_ohlcv_daily p1 on p1.ts_code=o.ts_code and p1.trade_date=?
        left join stock_ohlcv_daily p5 on p5.ts_code=o.ts_code and p5.trade_date=?
        left join stock_ohlcv_daily p20 on p20.ts_code=o.ts_code and p20.trade_date=?
        left join stock_ohlcv_daily p60 on p60.ts_code=o.ts_code and p60.trade_date=?
        left join stock_ohlcv_daily p120 on p120.ts_code=o.ts_code and p120.trade_date=?
        left join stock_ohlcv_daily p252 on p252.ts_code=o.ts_code and p252.trade_date=?
        left join stock_ohlcv_daily e on e.ts_code=o.ts_code and e.trade_date=(
          select min(ex.trade_date)
          from stock_ohlcv_daily ex
          where ex.ts_code=o.ts_code
            and ex.trade_date>?
            and ex.trade_date<=?
            and ex.qfq_close>0
            and ex.open>0
            and ex.close>0
            and ex.suspend_timing is null
            and not (ex.up_limit is not null and ex.open>=ex.up_limit*0.995 and ex.low>=ex.up_limit*0.995)
            and not (ex.down_limit is not null and ex.open<=ex.down_limit*1.005 and ex.high<=ex.down_limit*1.005)
        )
        left join stock_ohlcv_daily n on n.ts_code=o.ts_code and n.trade_date=(
          select max(nx.trade_date)
          from stock_ohlcv_daily nx
          where nx.ts_code=o.ts_code
            and nx.trade_date>?
            and nx.trade_date<=?
            and nx.qfq_close>0
        )
        left join stock_valuation_daily v on v.ts_code=o.ts_code and v.trade_date=o.trade_date
        left join stock_moneyflow_daily m on m.ts_code=o.ts_code and m.trade_date=o.trade_date
        left join sw_l1_industry_daily ind
          on ind.ts_code=o.ts_code
         and ind.start_date<=?
         and (ind.end_date is null or ind.end_date>=?)
        where o.qfq_close>0
        """,
        (date, d1, d5, d20, d60, d120, d252, date, next_date, date, next_date, date, date),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "ts_code", "stock_name", "open", "high", "low", "close", "px", "pre_close",
        "pct_chg", "vol", "amount", "up_limit", "down_limit", "suspend_timing",
        "px1", "px5", "px20", "px60", "px120", "px252",
        "entry_trade_date", "px_entry", "exit_trade_date", "px_next",
        "pb", "pe_ttm", "ps_ttm", "dv_ttm", "total_mv", "circ_mv",
        "turnover_rate", "turnover_rate_f", "volume_ratio",
        "net_mf_amount", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount",
        "industry_name", "index_weight",
    ])
    for col in df.columns:
        if col not in {"ts_code", "stock_name", "suspend_timing", "industry_name", "entry_trade_date", "exit_trade_date"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["industry_name"] = df["industry_name"].fillna("UNCLASSIFIED")
    df["label_close_to_close_ret"] = df["px_next"] / df["px"] - 1.0
    df["label_next_ret"] = df["px_next"] / df["px_entry"] - 1.0
    signal_date = pd.to_datetime(str(date), format="%Y%m%d", errors="coerce")
    expected_exit = pd.to_datetime(str(next_date), format="%Y%m%d", errors="coerce")
    entry_dates = pd.to_datetime(df["entry_trade_date"], format="%Y%m%d", errors="coerce")
    exit_dates = pd.to_datetime(df["exit_trade_date"], format="%Y%m%d", errors="coerce")
    df["execution_delay_days"] = (entry_dates - signal_date).dt.days
    df["exit_staleness_days"] = (expected_exit - exit_dates).dt.days
    df["execution_eligible"] = (df["px_entry"] > 0) & (df["px_next"] > 0)
    df["ret1"] = df["px"] / df["px1"] - 1.0
    df["mom5"] = df["px"] / df["px5"] - 1.0
    df["mom20"] = df["px"] / df["px20"] - 1.0
    df["mom60"] = df["px"] / df["px60"] - 1.0
    df["mom120"] = df["px"] / df["px120"] - 1.0
    df["mom252"] = df["px"] / df["px252"] - 1.0
    df["range_pct"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["gap_pct"] = (df["open"] - df["pre_close"]) / df["pre_close"].replace(0, np.nan)
    df["amount_to_mv"] = df["amount"] / df["circ_mv"].replace(0, np.nan)
    df["is_suspended"] = df["suspend_timing"].notna().astype(int)
    df["limit_pressure"] = 0.0
    df.loc[df["up_limit"].notna() & (df["close"] >= df["up_limit"] * 0.995), "limit_pressure"] = 1.0
    df.loc[df["down_limit"].notna() & (df["close"] <= df["down_limit"] * 1.005), "limit_pressure"] = -1.0
    df["netflow_intensity"] = df["net_mf_amount"] / df["amount"].replace(0, np.nan)
    df["large_order_balance"] = (
        df["buy_lg_amount"] + df["buy_elg_amount"] - df["sell_lg_amount"] - df["sell_elg_amount"]
    ) / df["amount"].replace(0, np.nan)
    fin_rows = [latest_fin(fin, fin_dates, code, date) for code in df["ts_code"]]
    for key in [
        "roe", "roa", "gross_margin", "netprofit_yoy", "debt_to_assets",
        "netprofit_margin", "current_ratio", "assets_turn", "op_yoy", "tr_yoy",
        "total_revenue", "n_income_attr_p",
    ]:
        df[key] = [safe_float(x.get(key)) for x in fin_rows]
    event_map = load_event_features(conn, date, 60)
    kline_map = load_kline_feature_summary(conn, date)
    for col in [
        "event_count_60", "earnings_event_count_60", "risk_event_count_60",
        "trading_event_count_60", "ai_event_count_60", "kline_feature_mean",
        "kline_feature_count", "kline_signal_balance",
    ]:
        df[col] = 0.0
    for idx, code in df["ts_code"].items():
        for col, val in event_map.get(code, {}).items():
            df.at[idx, col] = val
        for col, val in kline_map.get(code, {}).items():
            df.at[idx, col] = val
    df["trade_date"] = date
    return df


def materialize_base_features(panel):
    df = panel
    df["base_low_crowding"] = (
        0.45 * df.groupby("trade_date")["turnover_rate"].transform(lambda s: rank01(-s))
        + 0.25 * df.groupby("trade_date")["turnover_rate_f"].transform(lambda s: rank01(-s))
        + 0.20 * df.groupby("trade_date")["volume_ratio"].transform(lambda s: rank01(-s))
        + 0.10 * df.groupby("trade_date")["amount_to_mv"].transform(lambda s: rank01(-s))
    ).fillna(0.5)
    df["base_quality"] = (
        0.22 * df.groupby("trade_date")["roe"].transform(rank01)
        + 0.18 * df.groupby("trade_date")["roa"].transform(rank01)
        + 0.18 * df.groupby("trade_date")["gross_margin"].transform(rank01)
        + 0.14 * df.groupby("trade_date")["netprofit_margin"].transform(rank01)
        + 0.12 * df.groupby("trade_date")["assets_turn"].transform(rank01)
        + 0.16 * df.groupby("trade_date")["debt_to_assets"].transform(lambda s: rank01(-s))
    ).fillna(0.5)
    df["base_growth"] = (
        0.35 * df.groupby("trade_date")["netprofit_yoy"].transform(rank01)
        + 0.25 * df.groupby("trade_date")["op_yoy"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["tr_yoy"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["mom20"].transform(rank01)
    ).fillna(0.5)
    df["base_value"] = (
        0.30 * df.groupby("trade_date")["pb"].transform(lambda s: rank01(-s))
        + 0.22 * df.groupby("trade_date")["ps_ttm"].transform(lambda s: rank01(-s))
        + 0.16 * df.groupby("trade_date")["pe_ttm"].transform(lambda s: rank01(-s))
        + 0.17 * df.groupby("trade_date")["dv_ttm"].transform(rank01)
        + 0.15 * df.groupby("trade_date")["total_mv"].transform(lambda s: rank01(-s))
    ).fillna(0.5)
    df["base_trend"] = (
        0.28 * df.groupby("trade_date")["mom60"].transform(rank01)
        + 0.24 * df.groupby("trade_date")["mom120"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["mom20"].transform(rank01)
        + 0.16 * df.groupby("trade_date")["mom5"].transform(lambda s: rank01(-s))
        + 0.12 * df.groupby("trade_date")["range_pct"].transform(lambda s: rank01(-s))
    ).fillna(0.5)
    df["base_moneyflow"] = (
        0.45 * df.groupby("trade_date")["large_order_balance"].transform(rank01)
        + 0.35 * df.groupby("trade_date")["netflow_intensity"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["volume_ratio"].transform(rank01)
    ).fillna(0.5)
    df["base_reversal"] = (
        0.50 * df.groupby("trade_date")["mom5"].transform(lambda s: rank01(-s))
        + 0.25 * df.groupby("trade_date")["ret1"].transform(lambda s: rank01(-s))
        + 0.25 * df.groupby("trade_date")["range_pct"].transform(lambda s: rank01(-s))
    ).fillna(0.5)
    df["base_event_attention"] = (
        0.34 * df.groupby("trade_date")["event_count_60"].transform(rank01)
        + 0.26 * df.groupby("trade_date")["earnings_event_count_60"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["trading_event_count_60"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["ai_event_count_60"].transform(rank01)
    ).fillna(0.5)
    df["base_event_risk"] = df.groupby("trade_date")["risk_event_count_60"].transform(rank01).fillna(0.5)
    df["base_kline_context"] = (
        0.35 * df.groupby("trade_date")["mom20"].transform(rank01)
        + 0.25 * df.groupby("trade_date")["mom5"].transform(lambda s: rank01(-s))
        + 0.20 * df.groupby("trade_date")["kline_signal_balance"].transform(rank01)
        + 0.20 * df.groupby("trade_date")["kline_feature_count"].transform(rank01)
    ).fillna(0.5)
    df["base_size"] = df.groupby("trade_date")["total_mv"].transform(lambda s: rank01(-s)).fillna(0.5)
    return df


BASE_FEATURES = [
    "base_low_crowding", "base_quality", "base_growth", "base_value", "base_trend",
    "base_moneyflow", "base_reversal", "base_event_attention", "base_event_risk",
    "base_kline_context", "base_size",
]

RAW_AI_FEATURES = [
    "ret1", "mom5", "mom20", "mom60", "mom120", "mom252", "range_pct", "gap_pct", "amount_to_mv",
    "pb", "pe_ttm", "ps_ttm", "dv_ttm", "total_mv", "circ_mv",
    "turnover_rate", "turnover_rate_f", "volume_ratio",
    "roe", "roa", "gross_margin", "netprofit_yoy", "debt_to_assets", "netprofit_margin",
    "current_ratio", "assets_turn", "op_yoy", "tr_yoy",
    "netflow_intensity", "large_order_balance",
    "event_count_60", "earnings_event_count_60", "risk_event_count_60",
    "trading_event_count_60", "ai_event_count_60",
    "kline_feature_mean", "kline_feature_count", "kline_signal_balance",
]
AI_FEATURES = BASE_FEATURES + RAW_AI_FEATURES
CAUSAL_OBSERVATION_WINDOWS = (1, 2, 3, 6, 12)

AI_FEATURE_SEMANTICS = {
    "base_features": "audited cross-sectional composites built only from signal-date-visible inputs",
    "price_path": ["ret1", "mom5", "mom20", "mom60", "mom120", "mom252", "range_pct", "gap_pct"],
    "valuation_size": ["pb", "pe_ttm", "ps_ttm", "dv_ttm", "total_mv", "circ_mv"],
    "liquidity_crowding": ["amount_to_mv", "turnover_rate", "turnover_rate_f", "volume_ratio"],
    "point_in_time_fundamental": [
        "roe", "roa", "gross_margin", "netprofit_yoy", "debt_to_assets", "netprofit_margin",
        "current_ratio", "assets_turn", "op_yoy", "tr_yoy",
    ],
    "moneyflow": ["netflow_intensity", "large_order_balance"],
    "event_and_kline": [
        "event_count_60", "earnings_event_count_60", "risk_event_count_60",
        "trading_event_count_60", "ai_event_count_60", "kline_feature_mean",
        "kline_feature_count", "kline_signal_balance",
    ],
    "temporal_window_unit": "past signal observations; full-window production runs are contiguous monthly observations",
}

def group_rank(panel, series, ascending=True):
    tmp = pd.DataFrame({"trade_date": panel["trade_date"], "value": series}, index=panel.index)
    return tmp.groupby("trade_date")["value"].transform(lambda s: rank01(s, ascending=ascending))


def industry_rank(panel, series, ascending=True):
    tmp = pd.DataFrame({
        "trade_date": panel["trade_date"],
        "industry_name": panel["industry_name"].fillna("UNCLASSIFIED"),
        "value": series,
    }, index=panel.index)
    return tmp.groupby(["trade_date", "industry_name"])["value"].transform(lambda s: rank01(s, ascending=ascending)).fillna(0.5)


def causal_stock_transform(panel, values, operation, window):
    """Apply a past-only transform along each stock's signal-date observations."""
    window = int(window)
    if window not in CAUSAL_OBSERVATION_WINDOWS:
        raise ValueError(f"Unsupported causal observation window: {window}")
    frame = pd.DataFrame({
        "ts_code": panel["ts_code"].astype(str),
        "trade_date": panel["trade_date"].astype(str),
        "value": pd.to_numeric(values, errors="coerce"),
    }, index=panel.index).sort_values(["ts_code", "trade_date"])
    grouped = frame.groupby("ts_code", sort=False)["value"]
    min_periods = 1 if window == 1 else max(2, int(math.ceil(window * 0.60)))
    if operation == "ts_delta":
        transformed = grouped.diff(window)
    elif operation == "ts_mean":
        transformed = grouped.transform(lambda series: series.rolling(window, min_periods=min_periods).mean())
    elif operation == "ts_std":
        transformed = grouped.transform(lambda series: series.rolling(window, min_periods=min_periods).std(ddof=0))
    elif operation == "ts_zscore":
        rolling_mean = grouped.transform(lambda series: series.rolling(window, min_periods=min_periods).mean())
        rolling_std = grouped.transform(lambda series: series.rolling(window, min_periods=min_periods).std(ddof=0))
        transformed = (frame["value"] - rolling_mean) / rolling_std.replace(0.0, np.nan)
    else:
        raise ValueError(f"Unknown causal stock transform: {operation}")
    return pd.Series(transformed, index=frame.index).reindex(panel.index)

def eval_dsl(panel, node):
    op = node.get("op")
    if op == "deep_svd":
        return compute_deep_svd(panel, node.get("features", []))
    if op == "deep_supervised_ranker":
        return cached_deep_supervised_ranker(panel, node.get("features", []), hidden=int(node.get("hidden", 8)))
    if op == "deep_transformer_attention":
        return compute_transformer_attention_factor(panel, node.get("features", []))
    if op == "deep_vae_bottleneck":
        return compute_vae_bottleneck_factor(panel, node.get("features", []))
    if op == "graph_concept_residual":
        if isinstance(node.get("child"), dict):
            return compute_graph_concept_residual_series(panel, eval_dsl(panel, node["child"]))
        return compute_graph_concept_residual(panel, node.get("features", []))
    if op == "regime_mixture_expert":
        return compute_regime_mixture_factor(panel, node.get("features", []))
    if op == "deep_tabnet_sparse_gate":
        return compute_tabnet_sparse_gate_factor(panel, node.get("features", []))
    if op == "deep_temporal_convolution":
        return compute_temporal_convolution_factor(panel, node.get("features", []))
    if op == "deep_contrastive_regime":
        return compute_contrastive_regime_factor(panel, node.get("features", []))
    if op == "style_residual":
        child = eval_dsl(panel, node["child"])
        residual = compute_style_residual_series(panel, child, node.get("features", []))
        strength = max(0.0, min(1.0, safe_float(node.get("strength"), 0.5)))
        return strength * residual + (1.0 - strength) * child
    if op == "feature":
        col = node["name"]
        return pd.to_numeric(panel[col], errors="coerce") if col in panel.columns else pd.Series(np.nan, index=panel.index)
    if op == "const":
        return pd.Series(float(node.get("value", 0.0)), index=panel.index)
    if op in {"ts_delta", "ts_mean", "ts_std", "ts_zscore"}:
        child = eval_dsl(panel, node["child"])
        return causal_stock_transform(panel, child, op, int(node.get("window", 3)))
    if op == "soft_gate":
        gate = group_rank(panel, eval_dsl(panel, node["gate"]), ascending=True).clip(0.0, 1.0)
        if_true = eval_dsl(panel, node["if_true"]).fillna(0.0)
        if_false = eval_dsl(panel, node["if_false"]).fillna(0.0)
        return gate * if_true + (1.0 - gate) * if_false
    if op == "rank":
        return group_rank(panel, eval_dsl(panel, node["child"]), ascending=bool(node.get("ascending", True)))
    if op == "industry_rank":
        return industry_rank(panel, eval_dsl(panel, node["child"]), ascending=bool(node.get("ascending", True)))
    if op == "neg":
        return -eval_dsl(panel, node["child"])
    if op == "add":
        children = node.get("children", [])
        weights = node.get("weights") or [1.0] * len(children)
        out = pd.Series(0.0, index=panel.index)
        for child, weight in zip(children, weights):
            out = out + float(weight) * eval_dsl(panel, child).fillna(0.0)
        return out
    if op == "sub":
        return eval_dsl(panel, node["left"]).fillna(0.0) - eval_dsl(panel, node["right"]).fillna(0.0)
    if op == "mul":
        return eval_dsl(panel, node["left"]).fillna(0.5) * eval_dsl(panel, node["right"]).fillna(0.5)
    if op == "div":
        den = eval_dsl(panel, node["right"]).replace(0, np.nan)
        return eval_dsl(panel, node["left"]) / den
    if op == "zscore":
        child = eval_dsl(panel, node["child"])
        tmp = pd.DataFrame({"trade_date": panel["trade_date"], "value": child}, index=panel.index)
        return tmp.groupby("trade_date")["value"].transform(zscore)
    if op == "clip01":
        return eval_dsl(panel, node["child"]).clip(0.0, 1.0)
    raise ValueError(f"Unknown DSL op: {op}")


def feature_node(name):
    return {"op": "feature", "name": name}


def rank_node(name):
    return {"op": "rank", "child": feature_node(name)}


def add_node(items):
    return {
        "op": "add",
        "children": [x[0] for x in items],
        "weights": [x[1] for x in items],
    }


def dsl_to_latex(node):
    if not isinstance(node, dict):
        return "0"
    op = node.get("op")
    if op == "feature":
        name = str(node.get("name", "x")).replace("_", "\\_")
        return f"\\mathrm{{{name}}}"
    if op == "const":
        return f"{float(node.get('value', 0.0)):.3g}"
    if op in {"ts_delta", "ts_mean", "ts_std", "ts_zscore"}:
        label = {"ts_delta": r"\Delta", "ts_mean": r"\operatorname{TSMean}", "ts_std": r"\operatorname{TSStd}", "ts_zscore": r"\operatorname{TSZScore}"}[op]
        return f"{label}_{{{int(node.get('window', 3))}}}\\left({dsl_to_latex(node.get('child', {}))}\\right)"
    if op == "soft_gate":
        gate = dsl_to_latex(node.get("gate", {}))
        positive = dsl_to_latex(node.get("if_true", {}))
        negative = dsl_to_latex(node.get("if_false", {}))
        return f"\\operatorname{{softgate}}\\left({gate};{positive},{negative}\\right)"
    if op == "rank":
        return f"\\operatorname{{rank}}\\left({dsl_to_latex(node.get('child', {}))}\\right)"
    if op == "industry_rank":
        return f"\\operatorname{{rank}}_{{industry}}\\left({dsl_to_latex(node.get('child', {}))}\\right)"
    if op == "zscore":
        return f"\\operatorname{{zscore}}\\left({dsl_to_latex(node.get('child', {}))}\\right)"
    if op == "clip01":
        return f"\\operatorname{{clip}}_{{0,1}}\\left({dsl_to_latex(node.get('child', {}))}\\right)"
    if op == "neg":
        return f"-{dsl_to_latex(node.get('child', {}))}"
    if op == "sub":
        return f"{dsl_to_latex(node.get('left', {}))}-{dsl_to_latex(node.get('right', {}))}"
    if op == "mul":
        return f"{dsl_to_latex(node.get('left', {}))}\\times {dsl_to_latex(node.get('right', {}))}"
    if op == "div":
        return f"\\frac{{{dsl_to_latex(node.get('left', {}))}}}{{{dsl_to_latex(node.get('right', {}))}}}"
    if op == "add":
        children = node.get("children", [])
        weights = node.get("weights") or [1.0] * len(children)
        terms = [f"{float(w):.2f}\\cdot {dsl_to_latex(c)}" for c, w in zip(children, weights)]
        return " + ".join(terms) if terms else "0"
    if op == "style_residual":
        inner = dsl_to_latex(node.get("child", {}))
        features = ",".join(str(item).replace("_", "\\_") for item in node.get("features", []))
        strength = safe_float(node.get("strength"), 0.5)
        return f"\\operatorname{{StyleResidual}}_{{{strength:.2g};{features}}}\\left({inner}\\right)"
    if op == "deep_svd":
        return "\\operatorname{SVDLatent}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "deep_supervised_ranker":
        return "\\operatorname{MLPRanker}_{train}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "deep_transformer_attention":
        return "\\operatorname{ICAttentionStyle}_{train}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "deep_vae_bottleneck":
        return "\\operatorname{NonlinearBottleneck}_{train}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "graph_concept_residual":
        inner = dsl_to_latex(node["child"]) if isinstance(node.get("child"), dict) else ",".join(node.get("features", []))
        return "\\operatorname{GraphConceptResidual}\\left(" + inner + "\\right)"
    if op == "regime_mixture_expert":
        return "\\operatorname{RegimeMixture}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "deep_tabnet_sparse_gate":
        return "\\operatorname{SparseGateStyle}_{train}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "deep_temporal_convolution":
        return "\\operatorname{CausalConvStyle}_{multi\\ horizon}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    if op == "deep_contrastive_regime":
        return "\\operatorname{RegimeContrastStyle}_{train}\\left(" + ",".join(node.get("features", [])) + "\\right)"
    return f"\\operatorname{{{str(op)}}}(\\cdot)"


def describe_dsl(node):
    op = node.get("op") if isinstance(node, dict) else ""
    descriptions = {
        "deep_svd": "对多个基础特征做横截面标准化后提取主成分，捕捉共同潜在风格暴露。",
        "deep_supervised_ranker": "仅在训练期学习非线性隐藏层权重，再把同一映射应用到验证期和测试期。",
        "deep_transformer_attention": "用训练期 IC 稳定性形成注意力权重，对不同特征域动态加权。",
        "deep_vae_bottleneck": "用非线性瓶颈表示和重构残差构造稳健 latent 因子。",
        "graph_concept_residual": "把个股自身信息与行业图、隐概念簇共享信息结合，再取残差部分。",
        "regime_mixture_expert": "按市场趋势/波动状态路由到不同专家组合，避免单一结构失效。",
        "deep_tabnet_sparse_gate": "用训练期 IC 稳定性学习稀疏特征门控，只保留少数可解释高贡献特征，再做非线性交互。",
        "deep_temporal_convolution": "把多窗口趋势、反转和资金流视为时序通道，用固定可审计卷积核提取短中长周期组合信号。",
        "deep_contrastive_regime": "按市场状态构造对比式表征，奖励跨状态一致的截面排序，惩罚只在单一状态有效的表达式。",
        "style_residual": "逐期对候选因子的趋势、反转、拥挤或波动风格暴露做岭回归残差化，并按训练验证选定的强度软收缩，避免硬性全剔除。",
    }
    if op in descriptions:
        return descriptions[op]
    if op in {"ts_delta", "ts_mean", "ts_std", "ts_zscore"}:
        return "按个股和信号日期排序，只使用当前及过去观测构造变化、均值、波动或时序标准分，不读取未来数据。"
    if op == "soft_gate":
        return "用当期可见状态变量形成连续门控，在两条经济逻辑分支之间平滑路由，避免硬阈值切换。"
    if op == "add":
        return "对多个子因子按权重加权组合，并在后续做去极值、标准化和行业市值中性化。"
    if op in {"rank", "industry_rank", "zscore", "clip01"}:
        return "先计算子表达式，再做截面排名、行业内排名或标准化稳健处理。"
    return "由 DSL 表达式编译为可执行因子程序，并进入统一审计、检验和回测。"


DSL_IMPLEMENTATION_BACKENDS = {
    "deep_supervised_ranker": "sklearn_mlp_date_balanced_ensemble_with_explicit_numpy_fallback",
    "deep_transformer_attention": "train_ic_stability_softmax_router_attention_inspired",
    "deep_vae_bottleneck": "train_svd_nonlinear_bottleneck_not_variational_neural_network",
    "graph_concept_residual": "cross_sectional_industry_and_proxy_concept_residual",
    "regime_mixture_expert": "causal_market_state_rule_router",
    "deep_tabnet_sparse_gate": "train_ic_sparse_gate_tabnet_inspired",
    "deep_temporal_convolution": "fixed_causal_multi_horizon_convolution_tcn_inspired",
    "deep_contrastive_regime": "train_state_ic_router_contrastive_inspired",
    "deep_svd": "per_date_cross_sectional_svd",
    "style_residual": "per_date_ridge_style_exposure_soft_residual",
}
TARGET_FITTED_DSL_OPS = {
    "deep_supervised_ranker",
    "deep_transformer_attention",
    "deep_tabnet_sparse_gate",
    "deep_contrastive_regime",
}
TRAIN_FEATURE_FITTED_DSL_OPS = {"deep_vae_bottleneck"}


def collect_dsl_ops(node):
    if not isinstance(node, dict):
        return set()
    ops = {str(node.get("op", ""))}
    for key in ("child", "left", "right", "gate", "if_true", "if_false"):
        ops.update(collect_dsl_ops(node.get(key)))
    for child in node.get("children", []) or []:
        ops.update(collect_dsl_ops(child))
    return {op for op in ops if op}


def dsl_implementation_audit(dsl):
    ops = collect_dsl_ops(dsl)
    deep_ops = sorted(op for op in ops if op in DSL_IMPLEMENTATION_BACKENDS)
    return {
        "dsl_ops": sorted(ops),
        "deep_ops": deep_ops,
        "operator_backends": {op: DSL_IMPLEMENTATION_BACKENDS[op] for op in deep_ops},
        "neural_network_trained": "deep_supervised_ranker" in ops,
        "architecture_inspired_operators": [op for op in deep_ops if op != "deep_supervised_ranker"],
        "target_fitted": bool(ops & TARGET_FITTED_DSL_OPS),
        "train_feature_fitted": bool(ops & TRAIN_FEATURE_FITTED_DSL_OPS),
        "orientation_target_fitted": True,
        "fit_policy": "train_only_with_one_period_label_embargo",
        "validation_policy": "frozen_model_strict_post_train_oos",
        "label_embargo_periods": LABEL_HORIZON_PERIODS,
    }


def feature_economic_domain(feature):
    feature = str(feature)
    base_map = {
        "base_low_crowding": "liquidity_crowding",
        "base_quality": "fundamental",
        "base_growth": "fundamental",
        "base_value": "valuation",
        "base_trend": "price_path",
        "base_moneyflow": "moneyflow",
        "base_reversal": "price_path",
        "base_event_attention": "event",
        "base_event_risk": "event",
        "base_kline_context": "price_path",
        "base_size": "valuation",
    }
    if feature in base_map:
        return base_map[feature]
    for domain, fields in AI_FEATURE_SEMANTICS.items():
        if isinstance(fields, list) and feature in fields:
            return domain
    return "other"


def candidate_static_island(candidate):
    dsl = candidate.get("dsl") or {}
    ops = collect_dsl_ops(dsl)
    fields = candidate.get("required_fields") or extract_features(dsl)
    domains = sorted(set(feature_economic_domain(field) for field in fields))
    if ops & {"ts_delta", "ts_mean", "ts_std", "ts_zscore"}:
        architecture = "causal_temporal"
    elif "soft_gate" in ops or "regime_mixture_expert" in ops:
        architecture = "state_conditioned"
    elif any(op.startswith("deep_") for op in ops):
        architecture = "deep_representation"
    elif ops & {"graph_concept_residual", "style_residual"}:
        architecture = "graph_residual"
    elif ops & {"mul", "div"}:
        architecture = "nonlinear_symbolic"
    else:
        architecture = "linear_rank_symbolic"
    complexity = int(candidate.get("complexity", dsl_complexity(dsl)) or 0)
    complexity_band = "compact" if complexity <= 8 else "moderate" if complexity <= 16 else "complex"
    domain_label = "+".join(domains[:2]) if domains else "other"
    return f"{domain_label}|{architecture}|{complexity_band}"

def make_candidate(chinese_name, channel, family, dsl, hypothesis, data_scope, windows=None, neutralize=True, lineage=None):
    payload = {
        "chinese_name": chinese_name,
        "channel": channel,
        "family": family,
        "dsl": dsl,
        "hypothesis": hypothesis,
        "data_scope": data_scope,
        "windows": windows or [],
        "neutralize": bool(neutralize),
        "implementation_audit": dsl_implementation_audit(dsl),
    }
    name = stable_id(clean_name(f"ai_{channel}_{family}", 60), payload, 8)
    payload.update({
        "factor_name": name,
        "complexity": dsl_complexity(dsl),
        "lineage": lineage or [],
        "required_fields": sorted(set(extract_features(dsl))),
        "expected_direction": "higher_is_better",
        "latex_formula": dsl_to_latex(dsl),
        "construction": describe_dsl(dsl),
    })
    payload["quality_diversity_island"] = candidate_static_island(payload)
    return payload


def ai_router_endpoint():
    base = os.environ.get("AI_ROUTER_BASE_URL", "https://ai.router.team").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def ai_router_key():
    return os.environ.get("AI_ROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")


def parse_ai_json_content(content):
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_ai_router_json(system_prompt, user_payload, timeout=None, retries=None):
    api_key = os.environ.get("AI_ROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = ai_router_endpoint()
    model = os.environ.get("AI_ROUTER_MODEL", "gpt-5.5")
    effort = os.environ.get("AI_ROUTER_REASONING_EFFORT", "xhigh")
    timeout = int(os.environ.get("FACTOR_LLM_TIMEOUT_SECONDS", str(timeout or 180)))
    retries = int(retries if retries is not None else os.environ.get("FACTOR_LLM_RETRIES", "3"))
    if not api_key:
        return None, {"status": "disabled", "reason": "missing_api_key"}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": float(os.environ.get("AI_ROUTER_TEMPERATURE", "0.35")),
        "reasoning_effort": effort,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 AI-Factor-Mining-Agent/1.0",
    }
    last_status = {"status": "error", "reason": "not_called"}
    attempt_history = []
    request_started = time.time()

    for attempt in range(1, retries + 1):
        attempt_started = time.time()
        req = urlrequest.Request(base_url, data=body, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = parse_ai_json_content(content)
            attempt_history.append({
                "attempt": attempt,
                "status": "ready",
                "elapsed_seconds": round(time.time() - attempt_started, 2),
            })
            return parsed, {
                "status": "ready",
                "model": model,
                "reasoning_effort": effort,
                "attempt": attempt,
                "timeout_seconds": timeout,
                "retry_limit": retries,
                "payload_bytes": len(body),
                "elapsed_seconds": round(time.time() - request_started, 2),
                "attempt_history": attempt_history,
            }
        except Exception as exc:
            reason = str(exc)
            http_code = None
            retry_after = None
            if isinstance(exc, urlerror.HTTPError):
                http_code = int(exc.code)
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                    if detail:
                        reason = f"HTTP {exc.code}: {detail}"
                        try:
                            detail_payload = json.loads(detail)
                            if detail_payload.get("retry_after") is not None:
                                retry_after = int(detail_payload["retry_after"])
                        except (TypeError, ValueError, json.JSONDecodeError):
                            pass
                except Exception:
                    reason = str(exc)
                if retry_after is None:
                    header_value = exc.headers.get("Retry-After") if exc.headers else None
                    if header_value:
                        try:
                            retry_after = int(float(header_value))
                        except (TypeError, ValueError):
                            retry_after = None
                if http_code == 524 and retry_after is None:
                    retry_after = 120
            attempt_record = {
                "attempt": attempt,
                "status": "error",
                "http_code": http_code,
                "elapsed_seconds": round(time.time() - attempt_started, 2),
            }
            attempt_history.append(attempt_record)
            last_status = {
                "status": "error",
                "reason": reason[:800],
                "model": model,
                "reasoning_effort": effort,
                "attempt": attempt,
                "timeout_seconds": timeout,
                "retry_limit": retries,
                "payload_bytes": len(body),
                "elapsed_seconds": round(time.time() - request_started, 2),
                "attempt_history": attempt_history,
            }
            if attempt < retries:
                delay = max(1, int(retry_after or min(30, 2 ** attempt)))
                attempt_record["retry_delay_seconds"] = delay
                time.sleep(delay)
    last_status["elapsed_seconds"] = round(time.time() - request_started, 2)
    return None, last_status

ALLOWED_AI_DSL_OPS = {
    "feature", "const", "rank", "industry_rank", "zscore", "clip01", "neg", "add", "sub", "mul", "div",
    "ts_delta", "ts_mean", "ts_std", "ts_zscore", "soft_gate",
    "deep_svd", "deep_supervised_ranker", "deep_transformer_attention", "deep_vae_bottleneck",
    "graph_concept_residual", "regime_mixture_expert", "deep_tabnet_sparse_gate",
    "deep_temporal_convolution", "deep_contrastive_regime", "style_residual",
}


LLM_DIVERSITY_FOCI = [
    "raw point-in-time fundamentals and valuation with economically justified nonlinear confirmation",
    "causal path-state operators over price, liquidity and crowding without hard regime thresholds",
    "train-only deep representation with regime robustness and explicit interpretability",
    "event attention, event risk, money-flow confirmation and graph residualization",
    "multi-horizon trend, reversal, liquidity and crowding interactions with cost awareness",
    "underexplored cross-domain structures with low residual correlation to the current baseline",
]


def sanitize_observation_window(value):
    value = int(max(1, safe_float(value, 3)))
    return min(CAUSAL_OBSERVATION_WINDOWS, key=lambda allowed: abs(allowed - value))


def sanitize_ai_dsl(node, depth=0):
    if not isinstance(node, dict) or depth > 12:
        return None
    op = str(node.get("op", ""))
    if op not in ALLOWED_AI_DSL_OPS:
        return None
    if op == "feature":
        name = str(node.get("name", ""))
        return feature_node(name) if name in AI_FEATURES else None
    if op == "const":
        return {"op": "const", "value": max(-3.0, min(3.0, safe_float(node.get("value", 0.0), 0.0)))}
    if op in {"ts_delta", "ts_mean", "ts_std", "ts_zscore"}:
        child = sanitize_ai_dsl(node.get("child"), depth + 1)
        if not child:
            return None
        return {"op": op, "child": child, "window": sanitize_observation_window(node.get("window", 3))}
    if op == "soft_gate":
        gate = sanitize_ai_dsl(node.get("gate"), depth + 1)
        if_true = sanitize_ai_dsl(node.get("if_true"), depth + 1)
        if_false = sanitize_ai_dsl(node.get("if_false"), depth + 1)
        if not gate or not if_true or not if_false:
            return None
        return {"op": op, "gate": gate, "if_true": if_true, "if_false": if_false}
    if op == "style_residual":
        child = sanitize_ai_dsl(node.get("child"), depth + 1)
        features = [str(x) for x in node.get("features", []) if str(x) in AI_FEATURES]
        features = list(dict.fromkeys(features))[:6]
        if not child or not features:
            return None
        return {
            "op": op,
            "child": child,
            "features": features,
            "strength": max(0.0, min(1.0, safe_float(node.get("strength"), 0.5))),
        }
    if op == "graph_concept_residual":
        child = sanitize_ai_dsl(node.get("child"), depth + 1)
        if child:
            return {"op": op, "child": child}
        features = [str(x) for x in node.get("features", []) if str(x) in AI_FEATURES]
        return {"op": op, "features": features[:8]} if len(features) >= 2 else None
    if op in {
        "deep_svd", "deep_supervised_ranker", "deep_transformer_attention", "deep_vae_bottleneck",
        "regime_mixture_expert", "deep_tabnet_sparse_gate", "deep_temporal_convolution",
        "deep_contrastive_regime",
    }:
        features = [str(x) for x in node.get("features", []) if str(x) in AI_FEATURES]
        if len(features) < 2:
            return None
        out = {"op": op, "features": features[:8]}
        if op == "deep_supervised_ranker":
            out["hidden"] = int(max(2, min(16, safe_float(node.get("hidden", 8), 8))))
        return out
    if op in {"rank", "industry_rank", "zscore", "clip01", "neg"}:
        child = sanitize_ai_dsl(node.get("child"), depth + 1)
        return {"op": op, "child": child} if child else None
    if op == "add":
        children, weights = [], []
        raw_children = node.get("children", []) if isinstance(node.get("children"), list) else []
        raw_weights = node.get("weights")
        if not isinstance(raw_weights, list):
            raw_weights = [1.0] * len(raw_children)
        for child, weight in zip(raw_children[:6], raw_weights[:6]):
            clean = sanitize_ai_dsl(child, depth + 1)
            w = abs(safe_float(weight, 0.0))
            if clean and w > 0:
                children.append(clean)
                weights.append(w)
        if not children:
            return None
        total = sum(weights) or 1.0
        return {"op": "add", "children": children, "weights": [w / total for w in weights]}
    if op in {"sub", "mul", "div"}:
        raw_children = node.get("children") if isinstance(node.get("children"), list) else []
        if node.get("left") is not None or node.get("right") is not None:
            raw_children = [node.get("left"), node.get("right")]
        children = [sanitize_ai_dsl(child, depth + 1) for child in raw_children[:6]]
        children = [child for child in children if child]
        if len(children) < 2:
            return None
        if op in {"sub", "div"} and len(children) != 2:
            return None
        out = {"op": op, "left": children[0], "right": children[1]}
        for child in children[2:]:
            out = {"op": op, "left": out, "right": child}
        return out
    return None

def dsl_from_weight_dict(weights, risk_penalty=None):
    if not isinstance(weights, dict):
        return None
    if not isinstance(risk_penalty, dict):
        risk_penalty = {}
    items = []
    total = 0.0
    for feature, weight in weights.items():
        feature = str(feature)
        if feature not in AI_FEATURES:
            continue
        w = abs(safe_float(weight, 0.0))
        if w <= 0:
            continue
        items.append((rank_node(feature), w))
        total += w
    for feature, weight in risk_penalty.items():
        feature = str(feature)
        if feature not in AI_FEATURES:
            continue
        w = abs(safe_float(weight, 0.0))
        if w <= 0:
            continue
        items.append(({"op": "neg", "child": rank_node(feature)}, w))
        total += w
    if not items:
        return None
    return add_node([(node, weight / total if total else weight) for node, weight in items])

def generate_llm_api_candidate_batch(method_cards, memory, budget, diversity_slot=0, avoid_candidates=None, research_directive=None):
    budget = max(1, int(budget))
    user_payload = {
        "task": "generate_methodology_driven_factor_candidates",
        "budget": budget,
        "diversity_slot": int(diversity_slot) + 1,
        "diversity_focus": LLM_DIVERSITY_FOCI[int(diversity_slot) % len(LLM_DIVERSITY_FOCI)],
        "existing_candidates_to_avoid": avoid_candidates or [],
        "data_space": AI_FEATURES,
        "feature_semantics": AI_FEATURE_SEMANTICS,
        "research_directive": research_directive or "independent methodology-driven discovery",
        "allowed_dsl_ops": sorted(ALLOWED_AI_DSL_OPS),
        "dsl_grammar": {
            "feature": {"op": "feature", "name": "one data_space field"},
            "unary": {"op": "rank|industry_rank|zscore|clip01|neg|graph_concept_residual", "child": "one DSL node"},
            "causal_temporal": {"op": "ts_delta|ts_mean|ts_std|ts_zscore", "child": "one DSL node", "window": [1, 2, 3, 6, 12]},
            "soft_gate": {"op": "soft_gate", "gate": "state DSL", "if_true": "DSL branch", "if_false": "DSL branch"},
            "weighted_add": {"op": "add", "children": "2-6 DSL nodes", "weights": "same-length positive number array"},
            "binary": {"op": "sub|mul|div", "left": "DSL node", "right": "DSL node"},
            "deep_or_graph_features": {"op": "one deep operator or graph_concept_residual", "features": "2-8 data_space fields"},
        },
        "method_cards": method_cards[:8],
        "memory": memory,
        "constraints": {
            "no_future_return_or_label": True,
            "train_valid_test_protocol": SPLITS,
            "strict_nested_dsl_required": True,
            "candidate_count_must_equal_budget": True,
            "top_level_weight_vectors_forbidden": True,
            "anti_overfit_required": ["walk_forward", "purged_kfold", "novelty", "transaction_cost", "dual_residual_incrementality", "regime_posterior"],
            "information_truncation": "method cards are methodological priors only; never reproduce reported factor formulas or performance",
        },
        "required_json_schema": {
            "candidates": [{
                "chinese_name": "string",
                "family": "string",
                "hypothesis": "economic rationale and expected failure mode",
                "data_scope": "fields used",
                "dsl": {
                    "op": "add",
                    "children": [{"op": "rank", "child": {"op": "feature", "name": "base_quality"}}],
                    "weights": [1.0],
                },
                "windows": [20, 60, 120],
                "anti_overfit_plan": "how it should survive sample-out checks",
            }]
        },
    }
    system_prompt = (
        "You are an institutional A-share AI factor mining agent. Create auditable factor hypotheses, not report replication. "
        "Use only the provided point-in-time feature space and allowed DSL operators. Never use future returns, labels, test metrics, or hidden target leakage. "
        "Prefer economically motivated nonlinear interactions, regime-aware representations, and robust anti-overfit plans. "
        "Return exactly the requested number of candidates as strict JSON. Each candidate must contain one nested DSL object; "
        "do not emit top-level weights or risk_penalty fields."
    )
    parsed, status = call_ai_router_json(system_prompt, user_payload, retries=2)
    if not parsed or not isinstance(parsed, dict):
        return [], status
    if not isinstance(status, dict):
        status = {"status": "error", "reason": "missing_api_status"}

    out = []
    seen_programs = set()
    invalid_reasons = []
    raw_candidates = parsed.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raw_candidates = []
        invalid_reasons.append("candidates_not_list")
    for idx, item in enumerate(raw_candidates[:budget]):
        if not isinstance(item, dict):
            invalid_reasons.append(f"candidate_{idx + 1}_not_object")
            continue
        dsl = sanitize_ai_dsl(item.get("dsl"))
        adapter = "strict_dsl"
        if not dsl:
            dsl = dsl_from_weight_dict(item.get("weights"), item.get("risk_penalty"))
            adapter = "legacy_weight_dict" if dsl else None
        if not dsl:
            invalid_reasons.append(f"candidate_{idx + 1}_invalid_dsl")
            continue
        candidate = make_candidate(
            chinese_name=item.get("chinese_name", "GPT factor hypothesis"),
            channel="llm_hypothesis_generation",
            family=clean_name(item.get("family", "gpt_factor_candidate"), 48),
            dsl=dsl,
            hypothesis=item.get("hypothesis", "LLM generated factor hypothesis with anti-overfit validation."),
            data_scope=item.get("data_scope", "AI generated DSL factor"),
            windows=item.get("windows", [20, 60, 120]),
            neutralize=True,
            lineage=[{
                "model": DEFAULT_MODEL_NAME,
                "reasoning_effort": DEFAULT_REASONING_EFFORT,
                "api_status": status,
                "api_output_adapter": adapter,
                "anti_overfit_plan": item.get(
                    "anti_overfit_plan",
                    "walk-forward + purged k-fold + dual-residual incrementality + regime posterior",
                ),
                "generation_mode": "methodology_only_no_report_factor_replication",
                "information_truncation_policy": "no test metrics, no reported factor formula or reported performance",
                "test_metrics_used": False,
                "research_directive": research_directive,
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }],
        )
        fingerprint = candidate_program_fingerprint(candidate)
        if fingerprint in seen_programs:
            invalid_reasons.append(f"candidate_{idx + 1}_duplicate_program")
            continue
        seen_programs.add(fingerprint)
        out.append(candidate)

    status.update({
        "requested_candidate_count": budget,
        "raw_candidate_count": len(raw_candidates),
        "valid_candidate_count": len(out),
        "invalid_or_duplicate_candidate_count": len(invalid_reasons),
        "validation_issues": invalid_reasons[:12],
    })
    return out, status

def generate_llm_api_candidates(method_cards, memory, budget, research_directive=None):
    budget = max(1, int(budget))
    candidates = []
    batch_statuses = []
    seen_programs = set()
    for slot in range(budget):
        avoid = [{
            "family": item.get("family"),
            "required_fields": item.get("required_fields"),
            "dsl_ops": sorted(collect_dsl_ops(item.get("dsl", {}))),
            "hypothesis_summary": str(item.get("hypothesis", ""))[:240],
        } for item in candidates]
        batch, status = generate_llm_api_candidate_batch(
            method_cards,
            memory,
            1,
            diversity_slot=slot,
            avoid_candidates=avoid,
            research_directive=research_directive,
        )
        status = dict(status or {})
        status["diversity_slot"] = slot + 1
        accepted_in_slot = 0
        for candidate in batch:
            fingerprint = candidate_program_fingerprint(candidate)
            if fingerprint in seen_programs:
                continue
            seen_programs.add(fingerprint)
            candidates.append(candidate)
            accepted_in_slot += 1
        status["wrapper_accepted_count"] = accepted_in_slot
        batch_statuses.append(status)

    aggregate = {
        "status": "ready" if candidates else "error",
        "model": DEFAULT_MODEL_NAME,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "generation_mode": "sequential_independent_single_candidate_xhigh",
        "requested_candidate_count": budget,
        "valid_candidate_count": len(candidates),
        "failed_or_duplicate_slot_count": max(0, budget - len(candidates)),
        "batch_statuses": batch_statuses,
    }
    for candidate in candidates:
        for event in candidate.get("lineage") or []:
            event["generation_audit"] = aggregate
    return candidates, aggregate

def extract_features(node):
    if not isinstance(node, dict):
        return []
    if node.get("op") == "feature":
        return [node["name"]]
    out = list(node.get("features") or []) if isinstance(node.get("features"), list) else []
    for key in ["child", "children", "left", "right", "gate", "if_true", "if_false"]:
        value = node.get(key)
        if isinstance(value, list):
            for child in value:
                out.extend(extract_features(child))
        elif isinstance(value, dict):
            out.extend(extract_features(value))
    return out


def dsl_complexity(node):
    if not isinstance(node, dict):
        return 0
    score = 1
    if isinstance(node.get("features"), list):
        score += max(0, len(node.get("features", [])) - 1)
    for key in ["child", "left", "right", "gate", "if_true", "if_false"]:
        if isinstance(node.get(key), dict):
            score += dsl_complexity(node[key])
    for child in node.get("children", []) or []:
        score += dsl_complexity(child)
    return score


def dsl_token_set(node):
    if not isinstance(node, dict):
        return set()
    tokens = {f"op:{node.get('op')}"}
    if str(node.get("op", "")).startswith("ts_"):
        tokens.add(f"window:{int(node.get('window', 3))}")
    if node.get("op") == "feature":
        tokens.add(f"feature:{node.get('name')}")
    for feature in node.get("features", []) or []:
        tokens.add(f"feature:{feature}")
    for key in ["child", "left", "right", "gate", "if_true", "if_false"]:
        if isinstance(node.get(key), dict):
            tokens |= dsl_token_set(node[key])
    for child in node.get("children", []) or []:
        tokens |= dsl_token_set(child)
    return tokens


def dsl_similarity(a, b):
    left = dsl_token_set(a)
    right = dsl_token_set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def collect_dsl_subexpressions(node):
    if not isinstance(node, dict):
        return []
    out = []
    complexity = dsl_complexity(node)
    if 2 <= complexity <= 10:
        out.append(node)
    for key in ("child", "left", "right", "gate", "if_true", "if_false"):
        out.extend(collect_dsl_subexpressions(node.get(key)))
    for child in node.get("children", []) or []:
        out.extend(collect_dsl_subexpressions(child))
    return out


def build_subexpression_memory(patterns, limit=16):
    motifs = {}
    for pattern in patterns or []:
        dsl = pattern.get("dsl") if isinstance(pattern, dict) else {}
        for subtree in collect_dsl_subexpressions(dsl):
            key = json.dumps(subtree, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            entry = motifs.setdefault(key, {
                "dsl": subtree,
                "count": 0,
                "families": set(),
                "channels": set(),
                "selection_scores": [],
            })
            entry["count"] += 1
            if pattern.get("family"):
                entry["families"].add(str(pattern["family"]))
            if pattern.get("channel"):
                entry["channels"].add(str(pattern["channel"]))
            score = safe_float(pattern.get("risk_adjusted_selection_score", pattern.get("selection_score")), np.nan)
            if np.isfinite(score):
                entry["selection_scores"].append(float(score))
    rows = []
    for entry in motifs.values():
        rows.append({
            "dsl": entry["dsl"],
            "count": entry["count"],
            "families": sorted(entry["families"])[:5],
            "channels": sorted(entry["channels"])[:5],
            "mean_train_valid_selection_score": (
                float(np.mean(entry["selection_scores"])) if entry["selection_scores"] else 0.0
            ),
            "dsl_ops": sorted(collect_dsl_ops(entry["dsl"])),
            "required_fields": sorted(set(extract_features(entry["dsl"]))),
            "test_metrics_used": False,
        })
    rows.sort(
        key=lambda row: (
            row["count"],
            row["mean_train_valid_selection_score"],
            -dsl_complexity(row["dsl"]),
        ),
        reverse=True,
    )
    return rows[: int(limit)]

def annotate_ast_similarity(candidates):
    seen = []
    for cand in candidates:
        sims = [dsl_similarity(cand.get("dsl", {}), prev.get("dsl", {})) for prev in seen]
        cand["max_ast_similarity_prior"] = float(max(sims)) if sims else 0.0
        seen.append(cand)
    return candidates


def load_methodology_cards(conn):
    cards = []
    for row in conn.execute(
        """
        select card_id, module_name, report_title, org, report_date, method_summary,
               required_data, expected_model_change, current_gap, adoption_status
        from v4_method_card
        order by card_id
        """
    ).fetchall():
        cards.append({
            "card_id": row[0],
            "module_name": row[1],
            "report_title": row[2],
            "org": row[3],
            "report_date": row[4],
            "method_summary": row[5],
            "required_data": row[6],
            "expected_model_change": row[7],
            "current_gap": row[8],
            "adoption_status": row[9],
            "source": "v4_method_card",
        })
    for row in conn.execute(
        """
        select plan_id, factor_family, data_scope, search_channel, validation_stack, stop_rule, current_status
        from v4_factor_agent_plan
        order by plan_id
        """
    ).fetchall():
        cards.append({
            "card_id": row[0],
            "module_name": "factor_mining_methodology",
            "report_title": row[1],
            "org": "local_v4_factor_agent_plan",
            "report_date": "2026",
            "method_summary": f"search_channel={row[3]}; validation_stack={row[4]}; stop_rule={row[5]}",
            "required_data": row[2],
            "expected_model_change": "drive systematic AI factor mining, not report factor replication",
            "current_gap": row[6],
            "adoption_status": "active_methodology_seed",
            "source": "v4_factor_agent_plan",
        })
    if not cards:
        cards = [
            {
                "card_id": "alpha_gpt_method",
                "module_name": "factor_mining_methodology",
                "report_title": "Alpha-GPT / GPT Factor Factory methodology",
                "org": "paper_and_broker_method",
                "report_date": "2023-2026",
                "method_summary": "LLM proposes calculable hypotheses; local evaluator scores; failure memory mutates the next search.",
                "required_data": "price, valuation, fundamentals, moneyflow, event text, industry",
                "expected_model_change": "systematic alpha mining loop",
                "current_gap": "local fallback card",
                "adoption_status": "active_methodology_seed",
                "source": "built_in",
            }
        ]
    return cards


def audit_external_data_sources(conn, db_path):
    latest = {}
    for table, date_col in [
        ("stock_ohlcv_daily", "trade_date"),
        ("stock_valuation_daily", "trade_date"),
        ("stock_moneyflow_daily", "trade_date"),
        ("financial_report_visible", "visible_date"),
        ("news_event_daily", "publish_date"),
        ("trade_calendar", "trade_date"),
    ]:
        try:
            row = conn.execute(f"select min({date_col}), max({date_col}) from {table}").fetchone()
            latest[table] = {"min_date": row[0], "max_date": row[1]}
        except Exception as exc:
            latest[table] = {"error": str(exc)}
    return {
        "primary_database": {
            "path": str(db_path),
            "status": "used",
            "coverage": latest,
        },
        "public_python_packages": {
            "akshare": package_available("akshare"),
            "baostock": package_available("baostock"),
            "tushare": package_available("tushare"),
            "requests": package_available("requests"),
            "pyodbc": package_available("pyodbc"),
        },
        "api_secret_presence": {
            "ai_router": env_present("AI_ROUTER_API_KEY") or env_present("OPENAI_API_KEY"),
            "tushare": env_present("TUSHARE_TOKEN"),
            "ifind": env_present("IFIND_ACCESS_TOKEN") and env_present("IFIND_REFRESH_TOKEN"),
            "wind_sql": env_present("WIND_SQL_UID") and env_present("WIND_SQL_PWD"),
        },
        "quota_policy": {
            "ifind": "disabled unless FACTOR_MINING_ENABLE_PAID_PROBES=1; use only sampled metadata checks",
            "wind_sql": "disabled unless FACTOR_MINING_ENABLE_PAID_PROBES=1; never bulk extract",
            "csmar_cnki_ppm": "browser-only research source through SHH remote machine; no passwords stored",
        },
        "remote_browser_policy": {
            "skill_path": "skill/llm-factor-mining",
            "host_alias": "homeserver",
            "rule": "GUI/web login workflows run on the remote Windows session, not the local browser.",
        },
    }


def load_agent_memory(conn, universe, limit=80):
    rows = conn.execute(
        """
        select factor_name,
               max(case when split_name='train' then rank_ic end) as train_ic,
               max(case when split_name='valid' then rank_ic end) as valid_ic,
               max(case when split_name='valid' then coverage end) as valid_coverage
        from factor_test_result
        where universe=? and split_name in ('train', 'valid')
        group by factor_name
        order by coalesce(valid_ic, -99) desc
        limit ?
        """,
        (universe, limit),
    ).fetchall()

    registry_rows = conn.execute(
        """
        select factor_name, factor_group, expression, status, notes
        from v3_factor_candidate_registry
        where source_agent=?
        order by rowid desc
        limit ?
        """,
        (SOURCE_AGENT, limit),
    ).fetchall()
    registry = []
    failure_counts = defaultdict(int)
    seen = set()
    for factor_name, family, expression, _final_status, notes_text in registry_rows:
        key = (factor_name, family)
        if key in seen:
            continue
        seen.add(key)
        try:
            notes = json.loads(notes_text or "{}")
        except (TypeError, json.JSONDecodeError):
            notes = {}
        try:
            dsl = json.loads(expression or "{}")
        except (TypeError, json.JSONDecodeError):
            dsl = {}
        research = notes.get("research_memory") if isinstance(notes.get("research_memory"), dict) else notes
        diagnosis = research.get("search_diagnosis") if isinstance(research.get("search_diagnosis"), dict) else {}
        failure = diagnosis.get("failure_type") or research.get("search_diagnosis_code") or "unknown"
        stage_pass = bool(research.get("search_stage_pass", failure == "search_stage_passed"))
        reliability_pass = bool(research.get("search_reliability_pass", stage_pass))
        search_pass = stage_pass and reliability_pass
        if not search_pass:
            failure_counts[str(failure)] += 1
        registry.append({
            "factor_name": factor_name,
            "family": family,
            "dsl": dsl,
            "status": "search_passed" if search_pass else "search_rejected",
            "channel": notes.get("channel"),
            "selection_score": safe_float(research.get("selection_score"), 0.0),
            "risk_adjusted_selection_score": safe_float(
                research.get("search_risk_adjusted_selection_score", research.get("risk_adjusted_selection_score")),
                0.0,
            ),
            "pbo_proxy": safe_float(research.get("search_pbo_proxy"), 0.5),
            "search_max_abs_corr": safe_float(research.get("search_max_abs_corr"), 1.0),
            "failure_type": str(failure),
        })

    accepted_patterns = [x for x in registry if x.get("status") == "search_passed"][:20]
    failed_patterns = [x for x in registry if x.get("status") != "search_passed"][:40]
    return {
        "memory_scope": "train_validation_search_only",
        "test_fields_loaded": False,
        "best_prior_factors": [
            {
                "factor_name": row[0],
                "train_rank_ic": safe_float(row[1], 0.0),
                "valid_rank_ic": safe_float(row[2], 0.0),
                "valid_coverage": safe_float(row[3], 0.0),
                "research_pass": bool(
                    safe_float(row[1], 0.0) >= DEFAULT_GATES["train_rank_ic_min"]
                    and safe_float(row[2], 0.0) >= DEFAULT_GATES["valid_rank_ic_min"]
                ),
            }
            for row in rows
        ],
        "accepted_patterns": accepted_patterns,
        "failed_patterns": failed_patterns,
        "successful_subexpression_motifs": build_subexpression_memory(accepted_patterns),
        "failed_subexpression_motifs": build_subexpression_memory(failed_patterns),
        "failed_factor_names": [
            row[0] for row in rows if safe_float(row[2], 0.0) < DEFAULT_GATES["valid_rank_ic_min"]
        ],
        "failure_counts": dict(failure_counts),
        "memory_records": len(registry),
    }

def ensure_isolated_memory_schema(conn):
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        create table if not exists factor_agent_search_memory (
            id integer primary key autoincrement,
            memory_scope text not null,
            universe text not null,
            run_id text not null,
            created_at text not null,
            factor_name text not null,
            family text,
            channel text,
            dsl_json text not null,
            train_rank_ic real,
            valid_rank_ic real,
            valid_coverage real,
            search_stage_pass integer not null,
            search_reliability_pass integer not null,
            search_diagnosis_code text,
            selection_score real,
            risk_adjusted_selection_score real,
            search_pbo_proxy real,
            search_max_abs_corr real,
            complexity integer,
            unique(memory_scope, universe, run_id, factor_name)
        )
    """)
    conn.execute("""
        create index if not exists idx_factor_agent_memory_scope
        on factor_agent_search_memory(memory_scope, universe, id desc)
    """)


def load_isolated_agent_memory(db_path, universe, memory_scope, limit=80):
    scope = str(memory_scope or "default")[:64]
    audit = {
        "configured": bool(db_path),
        "database": str(db_path) if db_path else None,
        "memory_scope": scope,
        "policy": "train_validation_search_only_no_test_fields",
    }
    if not db_path:
        audit.update({"status": "disabled", "records_loaded": 0})
        return None, audit
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        ensure_isolated_memory_schema(conn)
        rows = conn.execute(
            """
            select factor_name, family, channel, dsl_json,
                   train_rank_ic, valid_rank_ic, valid_coverage,
                   search_stage_pass, search_reliability_pass,
                   search_diagnosis_code, selection_score,
                   risk_adjusted_selection_score, search_pbo_proxy,
                   search_max_abs_corr, complexity
            from factor_agent_search_memory
            where memory_scope=? and universe=?
            order by id desc
            limit ?
            """,
            (scope, universe, int(limit)),
        ).fetchall()
        conn.close()
    except Exception as exc:
        audit.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500], "records_loaded": 0})
        return None, audit

    registry = []
    failure_counts = defaultdict(int)
    seen = set()
    for row in rows:
        factor_name, family, channel, dsl_text = row[:4]
        if factor_name in seen:
            continue
        seen.add(factor_name)
        try:
            dsl = json.loads(dsl_text or "{}")
        except (TypeError, json.JSONDecodeError):
            dsl = {}
        stage_pass = bool(row[7])
        reliability_pass = bool(row[8])
        search_pass = stage_pass and reliability_pass
        failure = str(row[9] or ("search_stage_passed" if search_pass else "unknown"))
        if not search_pass:
            failure_counts[failure] += 1
        registry.append({
            "factor_name": factor_name,
            "family": family,
            "channel": channel,
            "dsl": dsl,
            "status": "search_passed" if search_pass else "search_rejected",
            "train_rank_ic": safe_float(row[4], 0.0),
            "valid_rank_ic": safe_float(row[5], 0.0),
            "valid_coverage": safe_float(row[6], 0.0),
            "selection_score": safe_float(row[10], 0.0),
            "risk_adjusted_selection_score": safe_float(row[11], 0.0),
            "pbo_proxy": safe_float(row[12], 0.5),
            "search_max_abs_corr": safe_float(row[13], 1.0),
            "complexity": int(safe_float(row[14], 0.0)),
            "failure_type": failure,
        })
    accepted_patterns = [item for item in registry if item["status"] == "search_passed"][:20]
    failed_patterns = [item for item in registry if item["status"] != "search_passed"][:40]
    memory = {
        "memory_scope": f"isolated_user_train_validation_search_only:{scope}",
        "test_fields_loaded": False,
        "best_prior_factors": [{
            "factor_name": item["factor_name"],
            "train_rank_ic": item["train_rank_ic"],
            "valid_rank_ic": item["valid_rank_ic"],
            "valid_coverage": item["valid_coverage"],
            "research_pass": item["status"] == "search_passed",
        } for item in registry[:40]],
        "accepted_patterns": accepted_patterns,
        "failed_patterns": failed_patterns,
        "successful_subexpression_motifs": build_subexpression_memory(accepted_patterns),
        "failed_subexpression_motifs": build_subexpression_memory(failed_patterns),
        "failed_factor_names": [item["factor_name"] for item in failed_patterns],
        "failure_counts": dict(failure_counts),
        "memory_records": len(registry),
    }
    audit.update({"status": "ready", "records_loaded": len(registry), "test_fields_loaded": False})
    return memory, audit


def persist_isolated_agent_memory(db_path, memory_scope, universe, run_id, candidates, leaderboard, retention=500):
    scope = str(memory_scope or "default")[:64]
    audit = {
        "configured": bool(db_path),
        "database": str(db_path) if db_path else None,
        "memory_scope": scope,
        "policy": "train_validation_search_only_no_test_fields",
    }
    if not db_path:
        audit.update({"status": "disabled", "records_written": 0})
        return audit
    candidate_map = {candidate.get("factor_name"): candidate for candidate in candidates}
    records = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for row in leaderboard:
        factor = row.get("factor")
        candidate = candidate_map.get(factor, {})
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        valid_metrics = metrics.get("valid") if isinstance(metrics.get("valid"), dict) else {}
        records.append((
            scope,
            universe,
            run_id,
            now,
            factor,
            row.get("family"),
            row.get("channel"),
            json.dumps(candidate.get("dsl") or row.get("dsl") or {}, ensure_ascii=True, sort_keys=True),
            safe_float(row.get("train_rank_ic"), 0.0),
            safe_float(row.get("valid_rank_ic"), 0.0),
            safe_float(valid_metrics.get("coverage"), 0.0),
            int(bool(row.get("search_stage_pass"))),
            int(bool(row.get("search_reliability_pass"))),
            str(row.get("search_diagnosis_code") or "unknown"),
            safe_float(row.get("selection_score"), 0.0),
            safe_float(row.get("search_risk_adjusted_selection_score"), 0.0),
            safe_float(row.get("search_pbo_proxy"), 0.5),
            safe_float(row.get("search_max_abs_corr_to_other_factor"), 1.0),
            int(safe_float(row.get("complexity"), 0.0)),
        ))
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        ensure_isolated_memory_schema(conn)
        if records:
            conn.executemany(
                """
                insert or replace into factor_agent_search_memory
                (memory_scope, universe, run_id, created_at, factor_name, family, channel, dsl_json,
                 train_rank_ic, valid_rank_ic, valid_coverage, search_stage_pass,
                 search_reliability_pass, search_diagnosis_code, selection_score,
                 risk_adjusted_selection_score, search_pbo_proxy, search_max_abs_corr, complexity)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
        conn.execute(
            """
            delete from factor_agent_search_memory
            where memory_scope=? and universe=? and id not in (
                select id from factor_agent_search_memory
                where memory_scope=? and universe=?
                order by id desc limit ?
            )
            """,
            (scope, universe, scope, universe, int(retention)),
        )
        conn.commit()
        total = conn.execute(
            "select count(*) from factor_agent_search_memory where memory_scope=? and universe=?",
            (scope, universe),
        ).fetchone()[0]
        conn.close()
        audit.update({"status": "ready", "records_written": len(records), "records_retained": int(total), "test_fields_written": False})
    except Exception as exc:
        audit.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500], "records_written": 0})
    return audit

def generate_llm_hypotheses(method_cards, memory, budget):
    api_candidates, api_status = generate_llm_api_candidates(method_cards, memory, budget)
    if api_candidates:
        return api_candidates
    if os.environ.get("FACTOR_REQUIRE_GPT", "0") == "1":
        raise RuntimeError(f"GPT required but unavailable: {api_status}")
    # This is the local structured fallback for the later official API adapter:
    # it consumes method cards and emits strict JSON-like candidates.
    templates = [
        (
            "质量价值低拥挤动量交互因子",
            "cross_domain_quality_value",
            add_node([
                (rank_node("base_quality"), 0.24),
                (rank_node("base_value"), 0.22),
                (rank_node("base_low_crowding"), 0.20),
                (rank_node("base_trend"), 0.18),
                (rank_node("base_moneyflow"), 0.16),
            ]),
            "质量、估值、拥挤度、趋势和资金流同时确认时，截面预期收益更稳定。",
            "valuation + point-in-time fundamentals + moneyflow + price",
        ),
        (
            "事件确认的基本面改善因子",
            "event_fundamental_revision",
            add_node([
                (rank_node("base_growth"), 0.28),
                (rank_node("base_quality"), 0.24),
                (rank_node("base_event_attention"), 0.18),
                (rank_node("base_value"), 0.16),
                ({"op": "neg", "child": rank_node("base_event_risk")}, 0.14),
            ]),
            "事件和财报可见信息确认基本面改善，但同时惩罚风险事件堆积。",
            "visible financial report + event text index + valuation",
        ),
        (
            "资金流反拥挤反转因子",
            "flow_anti_crowding_reversal",
            add_node([
                (rank_node("base_moneyflow"), 0.30),
                (rank_node("base_low_crowding"), 0.25),
                (rank_node("base_reversal"), 0.20),
                (rank_node("base_trend"), 0.15),
                (rank_node("base_value"), 0.10),
            ]),
            "短期反转、低拥挤和大单流入共振时，资金行为更可能提供增量 alpha。",
            "moneyflow + turnover + price windows",
        ),
        (
            "K线语境保护的趋势质量因子",
            "kline_context_trend",
            add_node([
                (rank_node("base_kline_context"), 0.26),
                (rank_node("base_trend"), 0.24),
                (rank_node("base_quality"), 0.18),
                (rank_node("base_low_crowding"), 0.18),
                (rank_node("base_moneyflow"), 0.14),
            ]),
            "趋势因子容易追高，加入K线语境和拥挤度保护后降低失效风险。",
            "kline_feature_daily + price + turnover + moneyflow",
        ),
    ]
    out = []
    used_cards = [c["card_id"] for c in method_cards[:8]]
    for tpl in templates[:budget]:
        cand = make_candidate(
            chinese_name=tpl[0],
            channel="llm_hypothesis",
            family=tpl[1],
            dsl=tpl[2],
            hypothesis=tpl[3],
            data_scope=tpl[4],
            windows=[5, 20, 60, 120],
            neutralize=True,
            lineage=[{
                "method_cards": used_cards,
                "model": DEFAULT_MODEL_NAME,
                "reasoning_effort": DEFAULT_REASONING_EFFORT,
                "mode": "local_structured_fallback",
                "api_status": api_status,
            }],
        )
        out.append(cand)
    return out


def generate_raw_causal_grammar_candidates(budget):
    templates = [
        (
            "盈利能力变化与估值确认路径因子",
            "profitability_revision_value_path",
            {"op": "industry_rank", "child": add_node([
                ({"op": "ts_delta", "child": rank_node("roe"), "window": 3}, 0.28),
                ({"op": "ts_delta", "child": rank_node("gross_margin"), "window": 3}, 0.20),
                ({"op": "neg", "child": rank_node("pb")}, 0.20),
                (rank_node("assets_turn"), 0.16),
                (rank_node("base_low_crowding"), 0.16),
            ])},
            "盈利能力与毛利率的可见信息变化若得到估值和资产效率确认，可能包含静态质量基线之外的修正信息。",
        ),
        (
            "资金流持续性反拥挤路径因子",
            "flow_persistence_anti_crowding_path",
            {"op": "industry_rank", "child": add_node([
                ({"op": "ts_mean", "child": rank_node("large_order_balance"), "window": 3}, 0.32),
                ({"op": "ts_delta", "child": rank_node("netflow_intensity"), "window": 2}, 0.22),
                (rank_node("base_low_crowding"), 0.22),
                ({"op": "neg", "child": {"op": "ts_std", "child": rank_node("turnover_rate"), "window": 6}}, 0.14),
                ({"op": "neg", "child": rank_node("risk_event_count_60")}, 0.10),
            ])},
            "持续而非单点的资金流入，在低拥挤和低换手波动条件下更可能转化为可交易增量收益。",
        ),
        (
            "波动状态条件化趋势成熟度因子",
            "volatility_conditioned_trend_maturity",
            {"op": "soft_gate",
             "gate": rank_node("range_pct"),
             "if_true": add_node([
                 (rank_node("base_quality"), 0.42),
                 (rank_node("base_low_crowding"), 0.36),
                 ({"op": "neg", "child": rank_node("risk_event_count_60")}, 0.22),
             ]),
             "if_false": add_node([
                 ({"op": "ts_zscore", "child": rank_node("mom60"), "window": 6}, 0.38),
                 ({"op": "ts_mean", "child": rank_node("large_order_balance"), "window": 3}, 0.24),
                 (rank_node("roe"), 0.20),
                 ({"op": "neg", "child": rank_node("pb")}, 0.18),
             ])},
            "高波动时依赖防御质量，低波动时依赖趋势成熟度和资金流持续性，用连续门控避免硬状态切换。",
        ),
        (
            "事件关注变化与基本面确认因子",
            "event_change_fundamental_confirmation",
            {"op": "industry_rank", "child": add_node([
                ({"op": "ts_delta", "child": rank_node("earnings_event_count_60"), "window": 2}, 0.22),
                ({"op": "ts_delta", "child": rank_node("risk_event_count_60"), "window": 2}, -0.18),
                (rank_node("netprofit_yoy"), 0.22),
                (rank_node("roe"), 0.18),
                ({"op": "neg", "child": rank_node("turnover_rate")}, 0.12),
                ({"op": "neg", "child": rank_node("pb")}, 0.08),
            ])},
            "事件变化只有在点时可见基本面与低拥挤确认时才保留，风险事件变化作为反向分支。",
        ),
        (
            "营运效率修正与价值残差因子",
            "operating_efficiency_value_residual",
            {"op": "graph_concept_residual", "child": {"op": "industry_rank", "child": add_node([
                ({"op": "ts_delta", "child": rank_node("assets_turn"), "window": 3}, 0.26),
                ({"op": "ts_delta", "child": rank_node("netprofit_margin"), "window": 3}, 0.24),
                ({"op": "neg", "child": rank_node("ps_ttm")}, 0.20),
                ({"op": "neg", "child": rank_node("debt_to_assets")}, 0.16),
                (rank_node("base_low_crowding"), 0.14),
            ])}},
            "提取营运效率和利润率修正中无法被行业、隐概念和静态价值质量基线解释的残差。",
        ),
        (
            "估值偏离与交易拥挤纠偏因子",
            "valuation_deviation_crowding_correction",
            {"op": "soft_gate",
             "gate": {"op": "ts_zscore", "child": rank_node("turnover_rate"), "window": 6},
             "if_true": add_node([
                 ({"op": "neg", "child": rank_node("pb")}, 0.35),
                 ({"op": "neg", "child": rank_node("ps_ttm")}, 0.25),
                 (rank_node("base_low_crowding"), 0.25),
                 (rank_node("roe"), 0.15),
             ]),
             "if_false": add_node([
                 ({"op": "ts_zscore", "child": {"op": "neg", "child": rank_node("pb")}, "window": 6}, 0.32),
                 (rank_node("dv_ttm"), 0.24),
                 (rank_node("gross_margin"), 0.22),
                 (rank_node("large_order_balance"), 0.22),
             ])},
            "把估值绝对水平与个股自身估值偏离分开，并用交易拥挤连续调节两条价值逻辑的权重。",
        ),
    ]
    out = []
    for index, (name, family, dsl, hypothesis) in enumerate(templates[: max(0, int(budget))], 1):
        out.append(make_candidate(
            chinese_name=name,
            channel="raw_causal_grammar_seed",
            family=family,
            dsl=dsl,
            hypothesis=hypothesis,
            data_scope=", ".join(sorted(set(extract_features(dsl)))),
            windows=[1, 2, 3, 6, 12],
            neutralize=True,
            lineage=[{
                "generation_mode": "methodology_grammar_seed",
                "economic_hypothesis_required": True,
                "test_metrics_used": False,
                "intended_objective": "dual_residual_incrementality_and_downstream_synergy",
                "seed_index": index,
            }],
        ))
    return out

def generate_nested_orthogonal_complement_seed_candidates(budget=1):
    """Revalidate the strongest prior train-nested complement with full trial accounting."""
    if int(budget or 0) <= 0:
        return []
    dsl = {
        "op": "graph_concept_residual",
        "child": {
            "op": "industry_rank",
            "child": add_node([
                ({"op": "ts_delta", "child": rank_node("op_yoy"), "window": 2}, 0.46),
                ({"op": "ts_mean", "child": rank_node("large_order_balance"), "window": 3}, 0.22),
                ({"op": "neg", "child": rank_node("pb")}, 0.16),
                (rank_node("base_low_crowding"), 0.10),
                ({"op": "neg", "child": rank_node("base_event_risk")}, 0.06),
            ]),
        },
    }
    candidate = make_candidate(
        chinese_name="训练期嵌套正交补充种子因子",
        channel="nested_orthogonal_complement_seed",
        family="train_nested_raw_orthogonal_complement",
        dsl=dsl,
        hypothesis=(
            "仅用训练期早晚隔离折选择原始经营变化路径，再与资金流持续性、"
            "低拥挤、低估值和低事件风险形成不含父代公式的正交补充。"
        ),
        data_scope=", ".join(sorted(set(extract_features(dsl)))),
        windows=[2, 3],
        neutralize=True,
        lineage=[{
            "generation_mode": "revalidated_prior_nested_train_orthogonal_complement",
            "prior_hypothesis": "v25_nested_train_orthogonal_complement",
            "mcts_action": "downstream_orthogonal_complement_expansion",
            "expanded_feature": "op_yoy",
            "expanded_window": 2,
            "feature_selection_audit": {
                "mode": "historical_embargoed_nested_train_only_revalidation",
                "internal_hypotheses_evaluated": 27,
                "historical_development_trials_included": True,
                "selection_split": "embargoed_nested_train_only",
                "validation_metrics_used": False,
                "test_metrics_used": False,
            },
            "test_metrics_used": False,
        }],
    )
    candidate["construction"] = (
        "训练期嵌套选择得到两期营业收入同比变化路径；再加入三期大单资金流、"
        "低估值、低拥挤和低事件风险，执行行业内排序与隐概念残差化。"
        "该表达式不包含父代公式；历史开发试验按27次计入DSR，测试集不参与重新选择。"
    )
    return [candidate]


def generate_mcts_candidates(memory, budget):
    features = [
        "base_quality", "base_value", "base_trend", "base_moneyflow",
        "base_low_crowding", "base_growth", "base_reversal", "base_event_attention",
    ]
    priors = {f["factor_name"]: f for f in memory.get("best_prior_factors", [])}
    rng = random.Random(20260707 + len(priors))
    out = []
    for depth in range(1, budget + 1):
        a, b, c = rng.sample(features, 3)
        if depth % 3 == 0:
            tree = {"op": "mul", "left": rank_node(a), "right": {"op": "sub", "left": rank_node(b), "right": rank_node(c)}}
        elif depth % 3 == 1:
            tree = add_node([(rank_node(a), 0.45), ({"op": "mul", "left": rank_node(b), "right": rank_node(c)}, 0.35), (rank_node("base_low_crowding"), 0.20)])
        else:
            tree = {"op": "industry_rank", "child": add_node([(rank_node(a), 0.50), (rank_node(b), 0.30), ({"op": "neg", "child": rank_node(c)}, 0.20)])}
        out.append(make_candidate(
            chinese_name=f"MCTS表达式树第{depth}层候选",
            channel="mcts_tree_seed",
            family="formulaic_alpha_tree",
            dsl=tree,
            hypothesis="Create diverse grammar-tree seeds; later rounds apply feedback-driven UCB expansion using train-validation evidence only.",
            data_scope="formulaic price/valuation/fundamental/moneyflow/event search space",
            windows=[5, 20, 60, 120, 252],
            neutralize=True,
            lineage=[{"mcts_depth": depth, "mcts_policy": "diverse_grammar_seed_before_feedback_ucb", "prior_factor_count": len(priors), "test_metrics_used": False}],
        ))
    return out


def monthly_rank_ic_values_for_series(panel, values):
    frame = pd.DataFrame({
        "trade_date": panel["trade_date"].astype(str),
        "value": pd.to_numeric(values, errors="coerce"),
        "label": pd.to_numeric(panel["label_next_ret"], errors="coerce"),
    }, index=panel.index).dropna()
    monthly = []
    for _, group in frame.groupby("trade_date"):
        if len(group) < 50:
            continue
        value_rank = group["value"].rank()
        label_rank = group["label"].rank()
        if value_rank.nunique(dropna=True) < 2 or label_rank.nunique(dropna=True) < 2:
            continue
        ic = value_rank.corr(label_rank)
        if pd.notna(ic):
            monthly.append(float(ic))
    return monthly


def mean_monthly_rank_ic_for_series(panel, values):
    monthly = monthly_rank_ic_values_for_series(panel, values)
    return float(np.mean(monthly)) if monthly else 0.0


def generate_openfe_candidates(panel, budget):
    if budget <= 0:
        return []
    train = split_panel(panel, "train")
    valid = split_panel(panel, "valid")
    available = [feature for feature in BASE_FEATURES if feature in panel.columns]
    proposals = []
    for left, right in combinations(available, 2):
        train_interaction = pd.to_numeric(train[left], errors="coerce").fillna(0.5) * pd.to_numeric(train[right], errors="coerce").fillna(0.5)
        valid_interaction = pd.to_numeric(valid[left], errors="coerce").fillna(0.5) * pd.to_numeric(valid[right], errors="coerce").fillna(0.5)
        train_ic = mean_monthly_rank_ic_for_series(train, train_interaction)
        valid_ic = mean_monthly_rank_ic_for_series(valid, valid_interaction)
        sign_consistent = train_ic * valid_ic >= 0
        robust_ic = min(abs(train_ic), abs(valid_ic))
        score = robust_ic - 0.45 * abs(abs(train_ic) - abs(valid_ic)) - (0.08 if not sign_consistent else 0.0)
        direction = 1.0 if (train_ic + valid_ic) >= 0 else -1.0
        proposals.append({
            "left": left,
            "right": right,
            "train_ic": train_ic,
            "valid_ic": valid_ic,
            "sign_consistent": sign_consistent,
            "proposal_score": float(score),
            "direction": direction,
        })
    proposals.sort(
        key=lambda item: (
            item["sign_consistent"],
            item["proposal_score"],
            min(abs(item["train_ic"]), abs(item["valid_ic"])),
            item["left"],
            item["right"],
        ),
        reverse=True,
    )
    selected = []
    feature_use = defaultdict(int)
    remaining = list(proposals)
    while remaining and len(selected) < budget:
        remaining.sort(
            key=lambda item: (
                item["proposal_score"]
                + 0.015 / (1.0 + feature_use[item["left"]])
                + 0.015 / (1.0 + feature_use[item["right"]]),
                item["sign_consistent"],
            ),
            reverse=True,
        )
        choice = remaining.pop(0)
        selected.append(choice)
        feature_use[choice["left"]] += 1
        feature_use[choice["right"]] += 1

    out = []
    for index, proposal in enumerate(selected, 1):
        left, right = proposal["left"], proposal["right"]
        interaction = {"op": "mul", "left": rank_node(left), "right": rank_node(right)}
        if proposal["direction"] < 0:
            interaction = {"op": "neg", "child": interaction}
        tree = add_node([
            (interaction, 0.60),
            (rank_node(left), 0.20),
            (rank_node(right), 0.20),
        ])
        out.append(make_candidate(
            chinese_name=f"OpenFE auto interaction factor {index}",
            channel="openfe_feature_search",
            family="auto_feature_interaction",
            dsl=tree,
            hypothesis="Enumerate cross-domain nonlinear interactions on embargoed train/validation samples and retain robust, sign-consistent, diverse proposals.",
            data_scope=f"{left} x {right}",
            windows=[20, 60],
            neutralize=True,
            lineage=[{
                "openfe_operator": "train_valid_enumerated_rank_interaction",
                "left": left,
                "right": right,
                "train_rank_ic": proposal["train_ic"],
                "valid_rank_ic": proposal["valid_ic"],
                "sign_consistent": proposal["sign_consistent"],
                "proposal_score": proposal["proposal_score"],
                "test_metrics_used": False,
            }],
        ))
    return out

def generate_genetic_candidates(seed_candidates, budget):
    out = []
    seeds = seed_candidates[: max(2, min(len(seed_candidates), 8))]
    for i in range(max(0, min(budget, len(seeds) - 1))):
        p1, p2 = seeds[i], seeds[-(i + 1)]
        tree = add_node([
            (p1["dsl"], 0.55),
            (p2["dsl"], 0.35),
            (rank_node("base_low_crowding"), 0.10),
        ])
        out.append(make_candidate(
            chinese_name=f"遗传交叉因子{i + 1}",
            channel="genetic_crossover",
            family="crossover_mutation",
            dsl=tree,
            hypothesis="把两条不同有效逻辑做交叉，并用低拥挤项约束交易拥挤风险。",
            data_scope="crossed candidate programs",
            windows=sorted(set((p1.get("windows") or []) + (p2.get("windows") or []))),
            neutralize=True,
            lineage=[{"parents": [p1["factor_name"], p2["factor_name"]], "mutation": "low_crowding_guard"}],
        ))
    return out


def generate_bandit_policy_candidates(memory, budget):
    priors = memory.get("accepted_patterns", []) if isinstance(memory, dict) else []
    failed = memory.get("failed_patterns", []) if isinstance(memory, dict) else []
    arms = [
        ("质量估值低拥挤Bandit因子", "quality_value_low_crowding_bandit", [("base_quality", 0.30), ("base_value", 0.28), ("base_low_crowding", 0.22), ("base_trend", 0.20)]),
        ("资金流动量防御Bandit因子", "flow_trend_defensive_bandit", [("base_moneyflow", 0.34), ("base_trend", 0.24), ("base_low_crowding", 0.24), ("base_event_risk", -0.18)]),
        ("成长质量状态Bandit因子", "growth_quality_regime_bandit", [("base_growth", 0.30), ("base_quality", 0.28), ("base_trend", 0.22), ("base_reversal", 0.20)]),
        ("小市值价值质量Bandit因子", "size_value_quality_bandit", [("base_size", 0.26), ("base_value", 0.28), ("base_quality", 0.26), ("base_low_crowding", 0.20)]),
        ("事件关注低风险Bandit因子", "event_attention_low_risk_bandit", [("base_event_attention", 0.30), ("base_quality", 0.24), ("base_low_crowding", 0.24), ("base_event_risk", -0.22)]),
    ]
    scored = []
    total_memory = max(1, len(priors) + len(failed))
    for name, family, weights in arms:
        successes = [p for p in priors if p.get("family") == family]
        failures = [p for p in failed if p.get("family") == family]
        alpha = 1.0 + len(successes)
        beta = 1.0 + len(failures)
        posterior_mean = alpha / (alpha + beta)
        uncertainty = math.sqrt(math.log(total_memory + 2.0) / (len(successes) + len(failures) + 1.0))
        quality = float(np.mean([
            safe_float(p.get("risk_adjusted_selection_score", p.get("selection_score")), 0.0)
            for p in successes
        ])) if successes else 0.0
        pbo_penalty = float(np.mean([safe_float(p.get("pbo_proxy"), 0.5) for p in successes])) if successes else 0.5
        domains = len({x[0].replace("base_", "").split("_")[0] for x in weights})
        diversity = 0.03 * domains
        utility = posterior_mean + 0.20 * uncertainty + 0.12 * quality - 0.08 * pbo_penalty + diversity
        scored.append((utility, name, family, weights, alpha, beta))
    scored.sort(key=lambda x: (x[0], x[2]), reverse=True)
    out = []
    for i, (utility, name, family, weights, alpha, beta) in enumerate(scored[:budget], 1):
        dsl = add_node([(rank_node(f), w) if w >= 0 else ({"op": "neg", "child": rank_node(f)}, abs(w)) for f, w in weights])
        out.append(make_candidate(
            chinese_name=f"{name}{i}",
            channel="rl_bandit_policy",
            family=family,
            dsl=dsl,
            hypothesis="使用历史验收、失败归因和不确定性构成后验效用，在利用稳健模式与探索未充分测试模式之间动态平衡。",
            data_scope=", ".join(f for f, _ in weights),
            windows=[20, 60, 120],
            neutralize=True,
            lineage=[{
                "policy": "beta_posterior_ucb_memory",
                "posterior_alpha": alpha,
                "posterior_beta": beta,
                "posterior_utility": utility,
                "accepted_prior_count": len(priors),
                "failed_prior_count": len(failed),
            }],
        ))
    return out


def generate_window_structure_candidates(panel, budget):
    if budget <= 0:
        return []
    train = split_panel(panel, "train")
    valid = split_panel(panel, "valid")
    long_windows = [("mom20", 20), ("mom60", 60), ("mom120", 120), ("mom252", 252)]
    short_windows = [("ret1", 1), ("mom5", 5), ("mom20", 20)]
    anchors = [
        ("base_quality", "base_low_crowding"),
        ("base_value", "base_quality"),
        ("base_moneyflow", "base_low_crowding"),
        ("base_growth", "base_quality"),
    ]
    proposals = []
    for (long_feature, long_window), (short_feature, short_window), (anchor_a, anchor_b) in product(long_windows, short_windows, anchors):
        if any(feature not in panel.columns for feature in [long_feature, short_feature, anchor_a, anchor_b]):
            continue
        dsl = add_node([
            (rank_node(long_feature), 0.45),
            ({"op": "neg", "child": rank_node(short_feature)}, 0.20),
            (rank_node(anchor_a), 0.20),
            (rank_node(anchor_b), 0.15),
        ])
        train_ic = mean_monthly_rank_ic_for_series(train, eval_dsl(train, dsl))
        valid_ic = mean_monthly_rank_ic_for_series(valid, eval_dsl(valid, dsl))
        sign_consistent = train_ic * valid_ic >= 0
        robust_ic = min(abs(train_ic), abs(valid_ic))
        decay = abs(abs(train_ic) - abs(valid_ic))
        score = robust_ic - 0.45 * decay - (0.08 if not sign_consistent else 0.0)
        proposals.append({
            "dsl": dsl,
            "long_feature": long_feature,
            "long_window": long_window,
            "short_feature": short_feature,
            "short_window": short_window,
            "anchor_a": anchor_a,
            "anchor_b": anchor_b,
            "train_ic": train_ic,
            "valid_ic": valid_ic,
            "sign_consistent": sign_consistent,
            "proposal_score": float(score),
        })
    proposals.sort(
        key=lambda item: (
            item["sign_consistent"],
            item["proposal_score"],
            min(abs(item["train_ic"]), abs(item["valid_ic"])),
            -item["long_window"],
        ),
        reverse=True,
    )
    selected = []
    used_window_pairs = set()
    used_anchors = set()
    for proposal in proposals:
        window_pair = (proposal["long_window"], proposal["short_window"])
        anchors_key = (proposal["anchor_a"], proposal["anchor_b"])
        if window_pair in used_window_pairs and anchors_key in used_anchors:
            continue
        selected.append(proposal)
        used_window_pairs.add(window_pair)
        used_anchors.add(anchors_key)
        if len(selected) >= budget:
            break

    out = []
    for index, proposal in enumerate(selected, 1):
        out.append(make_candidate(
            chinese_name=f"\u7a97\u53e3\u7ed3\u6784\u7a33\u5065\u641c\u7d22\u56e0\u5b50{index}",
            channel="window_structure_search",
            family=f"window_{proposal['long_window']}_{proposal['short_window']}_{proposal['anchor_a'].replace('base_', '')}",
            dsl=proposal["dsl"],
            hypothesis="Search real momentum/reversal horizons on embargoed train and validation samples, then retain sign-consistent structures with low decay and diverse economic anchors.",
            data_scope=", ".join([proposal["long_feature"], proposal["short_feature"], proposal["anchor_a"], proposal["anchor_b"]]),
            windows=[proposal["short_window"], proposal["long_window"]],
            neutralize=True,
            lineage=[{
                "optimizer": "embargoed_train_valid_exhaustive_window_structure_search",
                "long_window": proposal["long_window"],
                "short_window": proposal["short_window"],
                "train_rank_ic": proposal["train_ic"],
                "valid_rank_ic": proposal["valid_ic"],
                "sign_consistent": proposal["sign_consistent"],
                "proposal_score": proposal["proposal_score"],
                "test_metrics_used": False,
            }],
        ))
    return out

def generate_deep_representation_candidates(budget):
    out = []
    specs = [
        (
            "训练期监督深度Ranker因子",
            "deep_supervised_ranker",
            "supervised_nonlinear_ranker",
            ["base_quality", "base_value", "base_growth", "base_low_crowding", "base_trend", "base_moneyflow"],
            "用训练期下一期收益训练浅层非线性 ranker，再固定权重应用到验证和测试样本。",
            {"hidden": 10},
        ),
        (
            "Transformer注意力稳定IC因子",
            "deep_transformer_attention",
            "market_guided_attention",
            ["base_quality", "base_value", "base_growth", "base_trend", "base_moneyflow", "base_event_attention", "base_low_crowding"],
            "借鉴 Market-Guided Transformer，用训练期 IC 稳定性形成特征注意力，避免纯手工等权。",
            {},
        ),
        (
            "VAE瓶颈潜变量因子",
            "deep_vae_bottleneck",
            "vae_dynamic_latent",
            ["base_quality", "base_value", "base_growth", "base_moneyflow", "base_low_crowding", "base_reversal", "base_event_risk"],
            "用训练期特征结构学习非线性瓶颈表示，并用重构残差惩罚不稳定暴露。",
            {},
        ),
        (
            "HIST行业隐概念残差因子",
            "graph_concept_residual",
            "graph_hidden_concept",
            ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding"],
            "把个股自身信息、行业共享信息和隐概念簇共享信息分解，保留更有区分度的残差。",
            {},
        ),
        (
            "市场状态路由多专家因子",
            "regime_mixture_expert",
            "temporal_routing_expert",
            ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding", "base_reversal", "base_event_attention"],
            "按市场趋势和波动状态在价值质量、趋势资金、低拥挤防御三个专家间路由。",
            {},
        ),
        (
            "TabNet稀疏门控非线性因子",
            "deep_tabnet_sparse_gate",
            "tabnet_sparse_attention",
            ["base_quality", "base_value", "base_growth", "base_trend", "base_moneyflow", "base_low_crowding", "base_event_attention"],
            "借鉴 TabNet 的稀疏注意力思想，用训练期 IC 稳定性形成特征 mask，再学习少数高贡献变量的非线性交互。",
            {},
        ),
        (
            "TCN多窗口时序卷积因子",
            "deep_temporal_convolution",
            "tcn_multi_horizon_encoder",
            ["base_trend", "base_reversal", "base_moneyflow", "base_low_crowding", "base_kline_context", "base_event_attention"],
            "借鉴时序卷积网络，把短中长窗口的趋势、反转、K线和资金流通道组合成可审计的多尺度时序表示。",
            {},
        ),
        (
            "对比式市场状态稳健因子",
            "deep_contrastive_regime",
            "contrastive_regime_embedding",
            ["base_quality", "base_value", "base_growth", "base_trend", "base_moneyflow", "base_low_crowding", "base_reversal"],
            "借鉴对比学习，把上涨/下跌、高波动/低波动状态看作正负视角，保留跨状态排序一致而非单状态偶然有效的信号。",
            {},
        ),
        (
            "深度表示SVD因子",
            "deep_svd",
            "autoencoder_light_embedding",
            ["base_quality", "base_value", "base_growth", "base_low_crowding", "base_trend"],
            "用横截面标准化后的多域特征提取第一主成分，作为轻量可审计表示学习基线。",
            {},
        ),
    ]
    priority_order = [0, 6, 3, 4, 1, 5, 7, 2, 8]
    ordered_specs = [specs[i] for i in priority_order if i < len(specs)]
    for i, (name, op, family, features, hypothesis, extra) in enumerate(ordered_specs[:budget], 1):
        dsl = {"op": op, "features": features}
        dsl.update(extra)
        out.append(make_candidate(
            chinese_name=f"{name}{i}",
            channel="deep_representation",
            family=family,
            dsl=dsl,
            hypothesis=hypothesis,
            data_scope=", ".join(features),
            windows=[20, 60, 120],
            neutralize=True,
            lineage=[{"model": op, "features": features, "method_cards": DEEP_METHOD_CARDS}],
        ))
    return out


def generate_residual_monotonic_candidates(budget):
    specs = [
        (
            "\u56fe\u6b8b\u5dee\u4f4e\u62e5\u6324\u5355\u8c03\u4fee\u590d\u56e0\u5b50",
            "graph_residual_low_crowding_monotonic",
            ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding", "base_reversal"],
            add_node([
                ({"op": "rank", "child": {"op": "graph_concept_residual", "features": ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding", "base_reversal"]}}, 0.50),
                (rank_node("base_low_crowding"), 0.22),
                (rank_node("base_quality"), 0.18),
                ({"op": "neg", "child": rank_node("base_event_risk")}, 0.10),
            ]),
            "Residualize shared industry/concept exposure, then add low-crowding and quality anchors before industry ranking.",
        ),
        (
            "\u884c\u4e1a\u5185\u8d28\u91cf\u4f30\u503c\u6b63\u4ea4\u56e0\u5b50",
            "industry_quality_value_orthogonal",
            ["base_quality", "base_value", "base_growth", "base_size", "base_low_crowding"],
            add_node([
                ({"op": "mul", "left": rank_node("base_quality"), "right": rank_node("base_value")}, 0.42),
                ({"op": "sub", "left": rank_node("base_growth"), "right": rank_node("base_size")}, 0.24),
                (rank_node("base_low_crowding"), 0.22),
                ({"op": "graph_concept_residual", "features": ["base_quality", "base_value", "base_growth", "base_low_crowding"]}, 0.12),
            ]),
            "Use nonlinear quality-value interaction plus size-adjusted growth to reduce overlap with plain style factors.",
        ),
        (
            "\u8d44\u91d1\u53cd\u8f6c\u9632\u62e5\u6324\u6b8b\u5dee\u56e0\u5b50",
            "flow_reversal_anti_crowding_residual",
            ["base_moneyflow", "base_reversal", "base_low_crowding", "base_event_attention", "base_event_risk"],
            add_node([
                ({"op": "graph_concept_residual", "features": ["base_moneyflow", "base_reversal", "base_low_crowding", "base_event_attention"]}, 0.44),
                ({"op": "mul", "left": rank_node("base_moneyflow"), "right": rank_node("base_reversal")}, 0.24),
                (rank_node("base_low_crowding"), 0.22),
                ({"op": "neg", "child": rank_node("base_event_risk")}, 0.10),
            ]),
            "Combine moneyflow reversal with graph residualization and crowding control, then test as a single factor.",
        ),
    ]
    out = []
    for idx, (name, family, fields, core, hypothesis) in enumerate(specs[:budget], 1):
        dsl = {"op": "industry_rank", "child": {"op": "zscore", "child": core}}
        out.append(make_candidate(
            chinese_name=f"{name}{idx}",
            channel="residual_monotonic_repair",
            family=family,
            dsl=dsl,
            hypothesis=hypothesis,
            data_scope=", ".join(fields),
            windows=[20, 60, 120],
            neutralize=True,
            lineage=[{"repair_target": "novelty_and_monotonicity", "features": fields}],
        ))
    return out


def mutate_candidate(candidate, diagnosis, idx):
    dsl = candidate["dsl"]
    failure = diagnosis.get("failure_type") if isinstance(diagnosis, dict) else str(diagnosis)
    if failure in {"incremental_information_shortage"}:
        raw_residual = {"op": "graph_concept_residual", "features": [
            "roe", "gross_margin", "pb", "turnover_rate", "large_order_balance", "mom60",
        ]}
        dsl = {"op": "industry_rank", "child": add_node([
            ({"op": "rank", "child": raw_residual}, 0.52),
            ({"op": "ts_delta", "child": rank_node("large_order_balance"), "window": 2}, 0.18),
            ({"op": "neg", "child": rank_node("pb")}, 0.16),
            (rank_node("base_low_crowding"), 0.14),
        ])}
        mutation = "dual_residual_raw_domain_repair"
    elif failure in {"synergy_contribution_shortage"}:
        dsl = {"op": "soft_gate",
            "gate": rank_node("range_pct"),
            "if_true": add_node([(rank_node("base_low_crowding"), 0.55), (rank_node("base_quality"), 0.45)]),
            "if_false": add_node([(dsl, 0.58), (rank_node("base_moneyflow"), 0.24), (rank_node("base_value"), 0.18)]),
        }
        mutation = "downstream_synergy_soft_gate_recombination"
    elif failure in {"regime_concentration"}:
        dsl = {"op": "soft_gate",
            "gate": rank_node("turnover_rate"),
            "if_true": add_node([(rank_node("base_quality"), 0.44), (rank_node("base_low_crowding"), 0.36), ({"op": "neg", "child": rank_node("base_event_risk")}, 0.20)]),
            "if_false": add_node([({"op": "ts_mean", "child": dsl, "window": 3}, 0.54), (rank_node("base_trend"), 0.26), (rank_node("base_moneyflow"), 0.20)]),
        }
        mutation = "state_breadth_soft_gate_repair"
    elif failure in {"economic_realization_shortage"}:
        dsl = add_node([
            ({"op": "ts_mean", "child": dsl, "window": 3}, 0.58),
            (rank_node("base_low_crowding"), 0.20),
            (rank_node("base_quality"), 0.14),
            ({"op": "neg", "child": rank_node("base_event_risk")}, 0.08),
        ])
        mutation = "causal_smoothing_cost_realization_repair"
    elif failure in {"posterior_train_signal_uncertain", "posterior_validation_signal_uncertain"}:
        dsl = {"op": "industry_rank", "child": add_node([
            ({"op": "ts_zscore", "child": rank_node("roe"), "window": 6}, 0.24),
            ({"op": "neg", "child": rank_node("pb")}, 0.20),
            ({"op": "ts_delta", "child": rank_node("large_order_balance"), "window": 2}, 0.20),
            (rank_node("base_low_crowding"), 0.18),
            (rank_node("base_trend"), 0.18),
        ])}
        mutation = "fresh_raw_causal_signal_rebuild"
    elif failure in {"too_much_turnover", "high_turnover"}:
        dsl = add_node([(dsl, 0.70), (rank_node("base_low_crowding"), 0.20), (rank_node("base_quality"), 0.10)])
        mutation = "add_low_crowding_turnover_guard"
    elif failure in {"sample_out_decay", "walk_forward_instability"}:
        dsl = add_node([(dsl, 0.60), (rank_node("base_quality"), 0.20), (rank_node("base_value"), 0.20)])
        mutation = "add_fundamental_anchor_and_decay_control"
    elif failure in {"purged_kfold_instability", "purged_kfold_unstable", "search_purged_oos_rank_migration"}:
        dsl = {"op": "deep_contrastive_regime", "features": sorted(set(candidate.get("required_fields", []) + ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding", "base_reversal"]))[:8]}
        mutation = "contrastive_regime_repair_after_purged_kfold_decay"
    elif failure in {"search_pbo_overfit_risk", "search_cscv_overfit_risk"}:
        fields = sorted(set(candidate.get("required_fields", []) + ["base_quality", "base_value", "base_low_crowding", "base_moneyflow"]))[:6]
        dsl = {"op": "industry_rank", "child": add_node([
            ({"op": "graph_concept_residual", "features": fields}, 0.46),
            (rank_node("base_quality"), 0.22),
            (rank_node("base_value"), 0.18),
            (rank_node("base_low_crowding"), 0.14),
        ])}
        mutation = "simplify_and_residualize_after_search_overfit"
    elif failure in {"search_risk_adjusted_score_negative"}:
        dsl = add_node([
            ({"op": "industry_rank", "child": dsl}, 0.58),
            (rank_node("base_quality"), 0.18),
            (rank_node("base_low_crowding"), 0.14),
            (rank_node("base_moneyflow"), 0.10),
        ])
        mutation = "risk_adjusted_industry_rank_repair"
    elif failure in {"event_risk", "weak_market_neutral"}:
        dsl = add_node([(dsl, 0.76), ({"op": "neg", "child": rank_node("base_event_risk")}, 0.14), (rank_node("base_low_crowding"), 0.10)])
        mutation = "subtract_event_risk_and_crowding"
    elif failure in {"novelty_shortage", "redundant_factor"}:
        fields = sorted(set((candidate.get("required_fields") or ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding"]) + ["base_low_crowding", "base_event_risk"]))[:7]
        residual = {"op": "graph_concept_residual", "features": fields[:6]}
        dsl = {"op": "industry_rank", "child": {"op": "zscore", "child": add_node([
            ({"op": "rank", "child": residual}, 0.52),
            (rank_node("base_low_crowding"), 0.20),
            (rank_node("base_quality"), 0.16),
            ({"op": "neg", "child": rank_node("base_event_risk")}, 0.12),
        ])}}
        mutation = "graph_residual_industry_rank_to_reduce_redundancy"
    elif failure in {"bad_monotonicity", "non_monotonic_groups"}:
        dsl = {"op": "industry_rank", "child": {"op": "rank", "child": add_node([(dsl, 0.62), (rank_node("base_quality"), 0.16), (rank_node("base_low_crowding"), 0.14), ({"op": "neg", "child": rank_node("base_event_risk")}, 0.08)])}}
        mutation = "ranked_industry_monotonicity_repair_with_crowding_guard"
    elif failure in {"weak_valid_signal"}:
        dsl = {"op": "deep_contrastive_regime", "features": sorted(set(candidate.get("required_fields", []) + ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding", "base_reversal"]))[:8]}
        mutation = "contrastive_regime_generalization_repair_after_weak_validation"
    elif failure in {"weak_test_signal", "weak_test_ic", "weak_group_spread", "below_composite_reward", "search_stage_passed"}:
        dsl = {"op": "regime_mixture_expert", "features": sorted(set(candidate.get("required_fields", []) + ["base_quality", "base_value", "base_trend", "base_moneyflow", "base_low_crowding"]))[:8]}
        mutation = "route_by_market_regime_after_weak_oos_signal"
    elif failure in {"low_coverage", "coverage_shortage", "static_audit_blocked"}:
        dsl = add_node([(rank_node("base_quality"), 0.30), (rank_node("base_value"), 0.24), (rank_node("base_trend"), 0.22), (rank_node("base_moneyflow"), 0.14), (rank_node("base_low_crowding"), 0.10)])
        mutation = "fallback_to_high_coverage_core_domains"
    else:
        dsl = add_node([(dsl, 0.72), (rank_node("base_moneyflow"), 0.16), (rank_node("base_reversal"), 0.12)])
        mutation = "add_flow_reversal_probe"
    mutation_labels = {
        "dual_residual_raw_domain_repair": "双残差原始域增量修复因子",
        "downstream_synergy_soft_gate_recombination": "组合边际贡献软门控重组因子",
        "state_breadth_soft_gate_repair": "跨状态广度软门控修复因子",
        "causal_smoothing_cost_realization_repair": "因果平滑收益兑现修复因子",
        "fresh_raw_causal_signal_rebuild": "原始字段因果信号重建因子",
        "add_low_crowding_turnover_guard": "低拥挤换手约束变异因子",
        "add_fundamental_anchor_and_decay_control": "基本面锚定衰减修复因子",
        "contrastive_regime_repair_after_purged_kfold_decay": "对比状态稳健修复因子",
        "simplify_and_residualize_after_search_overfit": "降复杂度图残差修复因子",
        "risk_adjusted_industry_rank_repair": "风险调整行业内排序修复因子",
        "subtract_event_risk_and_crowding": "事件风险与拥挤惩罚变异因子",
        "graph_residual_industry_rank_to_reduce_redundancy": "图残差行业中性去冗余因子",
        "ranked_industry_monotonicity_repair_with_crowding_guard": "行业内单调性修复因子",
        "contrastive_regime_generalization_repair_after_weak_validation": "对比状态验证泛化修复因子",
        "route_by_market_regime_after_weak_oos_signal": "市场状态混合专家变异因子",
        "fallback_to_high_coverage_core_domains": "高覆盖核心域修复因子",
        "add_flow_reversal_probe": "资金流反转探针变异因子",
    }
    mutation_label = mutation_labels.get(mutation, "定向失败归因变异因子")
    return make_candidate(
        chinese_name=f"{mutation_label}{idx}",
        channel="failure_memory_mutation",
        family=clean_name(mutation, 48),
        dsl=dsl,
        hypothesis=f"Directed mutation after failure diagnosis {failure}: {mutation}.",
        data_scope=", ".join(extract_features(dsl)),
        windows=candidate.get("windows", []),
        neutralize=True,
        lineage=candidate.get("lineage", []) + [{"parent": candidate["factor_name"], "mutation": mutation, "diagnosis": diagnosis}],
    )


def crossover_search_parents(left, right, idx, left_evidence=None, right_evidence=None):
    def robust_parent_strength(row):
        row = row or {}
        train_ic = max(0.0, safe_float(row.get("train_rank_ic"), 0.0))
        valid_ic = max(0.0, safe_float(row.get("valid_rank_ic"), 0.0))
        pbo = min(1.0, max(0.0, safe_float(row.get("search_pbo_proxy"), 0.5)))
        base = min(train_ic, valid_ic) + 0.25 * 0.5 * (train_ic + valid_ic)
        return max(0.001, base * (1.0 - 0.35 * pbo))

    left_strength = robust_parent_strength(left_evidence)
    right_strength = robust_parent_strength(right_evidence)
    share = left_strength / max(left_strength + right_strength, 1e-12)
    left_weight = max(0.25, min(0.60, 0.80 * share))
    right_weight = 0.80 - left_weight
    dsl = add_node([
        (left["dsl"], left_weight),
        (right["dsl"], right_weight),
        (rank_node("base_low_crowding"), 0.12),
        ({"op": "neg", "child": rank_node("base_event_risk")}, 0.08),
    ])
    left_name = left.get("chinese_name") or left.get("factor_name")
    right_name = right.get("chinese_name") or right.get("factor_name")
    return make_candidate(
        chinese_name=f"{left_name}\u4e0e{right_name}\u534f\u540c\u4ea4\u53c9\u56e0\u5b50{idx}",
        channel="pareto_parent_crossover",
        family="pareto_synergy_crossover",
        dsl=dsl,
        hypothesis="Combine two train-validation Pareto parents with stability-weighted coefficients while retaining crowding and event-risk guards.",
        data_scope=f"{left.get('data_scope', '')} x {right.get('data_scope', '')}",
        windows=sorted(set((left.get("windows") or []) + (right.get("windows") or []))),
        neutralize=True,
        lineage=[{
            "parents": [left.get("factor_name"), right.get("factor_name")],
            "parent_policy": "train_valid_pareto_frontier",
            "joint_objective": "robust_tail_return_plus_novelty_minus_turnover",
            "left_weight": left_weight,
            "right_weight": right_weight,
            "left_strength": left_strength,
            "right_strength": right_strength,
        }],
    )


def _embargoed_synergy_research_folds(panel, boundary_embargo=2):
    folds = []
    for split_name in ("train", "valid"):
        split = split_panel(panel, split_name)
        dates = sorted(str(value) for value in split["trade_date"].dropna().unique())
        if not dates:
            continue
        midpoint = len(dates) // 2
        early_dates = dates[: max(1, midpoint - boundary_embargo // 2)]
        late_dates = dates[min(len(dates), midpoint + int(math.ceil(boundary_embargo / 2.0))):]
        parts = []
        if len(early_dates) >= 2:
            parts.append(("early", early_dates))
        if len(late_dates) >= 2:
            parts.append(("late", late_dates))
        if len(parts) < 2:
            parts = [("full", dates)]
        for label, fold_dates in parts:
            index = split[split["trade_date"].astype(str).isin(set(fold_dates))].index
            if len(index):
                folds.append({
                    "name": f"{split_name}_{label}",
                    "split": split_name,
                    "dates": list(fold_dates),
                    "index": index,
                })
    return folds


def _synergy_weight_vectors(size):
    if size < 2:
        return []
    vectors = set()

    def add(values):
        values = np.asarray(values, dtype=float)
        values[np.abs(values) < 1e-12] = 0.0
        total = float(values.sum())
        if total <= 0 or int((values > 0).sum()) < 2:
            return
        vectors.add(tuple(float(round(value / total, 6)) for value in values))

    add([1.0 / size] * size)
    for primary in range(size):
        rest = 0.50 / max(1, size - 1)
        add([0.50 if idx == primary else rest for idx in range(size)])
    for left, right in combinations(range(size), 2):
        for left_weight in (0.35, 0.50, 0.65):
            values = [0.0] * size
            values[left] = left_weight
            values[right] = 1.0 - left_weight
            add(values)
    if size >= 3:
        for chosen in combinations(range(size), 3):
            for weights in ((0.50, 0.30, 0.20), (0.30, 0.50, 0.20), (0.20, 0.30, 0.50)):
                values = [0.0] * size
                for idx, weight in zip(chosen, weights):
                    values[idx] = weight
                add(values)
    return [list(values) for values in sorted(vectors)]


def _pareto_records(records, objective_field):
    def dominates(left, right):
        a = left[objective_field]
        b = right[objective_field]
        return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))

    return [
        record for record in records
        if not any(dominates(other, record) for other in records if other is not record)
    ]


def _prepare_synergy_search_series(panel, raw_values):
    work = panel[[
        "trade_date", "ts_code", "total_mv", "industry_name", "label_next_ret",
    ]].copy()
    work["__synergy_raw"] = pd.to_numeric(raw_values, errors="coerce")
    work["__synergy_raw"] = winsorize_by_date(work, "__synergy_raw").fillna(0.0)
    work["__synergy_z"] = work.groupby("trade_date")["__synergy_raw"].transform(zscore).fillna(0.0)
    work["__synergy_prepared"] = neutralize_size_industry(work, "__synergy_z").fillna(0.0)
    direction_ic = train_direction_rank_ic(work, "__synergy_prepared")
    orientation = -1.0 if direction_ic < 0 else 1.0
    return orientation * work["__synergy_prepared"], orientation, direction_ic


def _synergy_fold_evidence(panel, values, folds, include_backtest=False):
    rows = []
    for fold in folds:
        sub = panel.loc[fold["index"]]
        fold_values = pd.to_numeric(values.loc[fold["index"]], errors="coerce")
        monthly_ic = monthly_rank_ic_values_for_series(sub, fold_values)
        posterior = normal_posterior_evidence(monthly_ic)
        row = {
            "fold": fold["name"],
            "split": fold["split"],
            "periods": len(monthly_ic),
            "rank_ic": float(np.mean(monthly_ic)) if monthly_ic else 0.0,
            "posterior_mean": safe_float(posterior.get("posterior_mean"), 0.0),
            "posterior_std": safe_float(posterior.get("posterior_std"), 0.04),
            "lower_90": safe_float(posterior.get("lower_90"), -0.10),
            "positive_probability": safe_float(posterior.get("positive_probability"), 0.5),
        }
        if include_backtest:
            frame = sub[["trade_date", "ts_code", "label_next_ret"]].copy()
            frame["__synergy_factor"] = fold_values
            backtest = top_bottom_backtest(
                frame.dropna(),
                "__synergy_factor",
                cost_rate=0.001,
                top_frac=0.20,
            )
            long_short = backtest.get("long_short", {}) if isinstance(backtest, dict) else {}
            row.update({
                "annual_return": safe_float(long_short.get("annual_return"), 0.0),
                "sharpe": safe_float(long_short.get("sharpe"), 0.0),
                "max_drawdown": safe_float(long_short.get("max_drawdown"), 0.0),
                "turnover": safe_float(backtest.get("avg_turnover"), 0.0),
                "backtest_curve": backtest.get("curve", []),
            })
        rows.append(row)
    return rows


def build_multiobjective_synergy_candidate(parent_rows, evaluated, panel, iteration):
    """Build one auditable factor ensemble from train/validation Pareto parents."""
    eligible = []
    for row in parent_rows:
        factor = row.get("factor")
        candidate = evaluated.get(factor)
        raw_col = f"{factor}__raw"
        if not factor or candidate is None or factor not in panel.columns or raw_col not in panel.columns:
            continue
        eligible.append({
            "factor": factor,
            "row": row,
            "candidate": candidate,
            "strength": float(np.tanh(row_search_score(row))),
        })
    if len(eligible) < 2:
        return None

    eligible.sort(
        key=lambda item: (
            item["strength"],
            safe_float(item["row"].get("valid_rank_ic"), -999.0),
            -safe_float(item["row"].get("complexity"), 999.0),
        ),
        reverse=True,
    )
    eligible = eligible[:5]
    research = search_panel(panel)

    pair_corr = {}
    for left, right in combinations(eligible, 2):
        left_values = panel[left["factor"]]
        right_values = panel[right["factor"]]
        monthly = []
        frame = pd.DataFrame({
            "trade_date": research["trade_date"].astype(str),
            "left": pd.to_numeric(left_values.loc[research.index], errors="coerce"),
            "right": pd.to_numeric(right_values.loc[research.index], errors="coerce"),
        }, index=research.index).dropna()
        for _, group in frame.groupby("trade_date"):
            if len(group) < 50:
                continue
            left_rank = group["left"].rank()
            right_rank = group["right"].rank()
            if left_rank.nunique(dropna=True) < 2 or right_rank.nunique(dropna=True) < 2:
                continue
            corr = left_rank.corr(right_rank)
            if pd.notna(corr):
                monthly.append(abs(float(corr)))
        pair_corr[tuple(sorted((left["factor"], right["factor"])))] = float(np.mean(monthly)) if monthly else 1.0

    parent_set_trials = []
    maximum_size = min(3, len(eligible))
    for size in range(2, maximum_size + 1):
        for members in combinations(eligible, size):
            correlations = [
                pair_corr.get(tuple(sorted((left["factor"], right["factor"]))), 1.0)
                for left, right in combinations(members, 2)
            ]
            strengths = [member["strength"] for member in members]
            families = {member["candidate"].get("family") for member in members}
            islands = {member["row"].get("quality_diversity_island") for member in members}
            correlation = float(np.mean(correlations)) if correlations else 1.0
            robust_strength = min(strengths) + 0.35 * float(np.median(strengths))
            diversity = len(families) + len(islands)
            score = robust_strength + 0.25 * (1.0 - correlation) + 0.025 * diversity - 0.015 * size
            parent_set_trials.append({
                "members": list(members),
                "mean_abs_monthly_rank_corr": correlation,
                "robust_parent_strength": robust_strength,
                "diversity_count": diversity,
                "set_score": score,
            })
    if not parent_set_trials:
        return None
    parent_set_trials.sort(
        key=lambda item: (
            item["set_score"],
            -item["mean_abs_monthly_rank_corr"],
            item["diversity_count"],
        ),
        reverse=True,
    )
    selected_members = parent_set_trials[0]["members"]

    child_nodes = []
    child_values = []
    for member in selected_members:
        candidate = member["candidate"]
        factor = member["factor"]
        node = {"op": "rank", "child": candidate["dsl"]}
        values = group_rank(panel, panel[f"{factor}__raw"], ascending=True)
        if candidate.get("orientation") == "flipped_by_train_rank_ic":
            node = {"op": "neg", "child": node}
            values = -values
        child_nodes.append(node)
        child_values.append(pd.to_numeric(values, errors="coerce").fillna(0.0))

    folds = _embargoed_synergy_research_folds(panel, boundary_embargo=2)
    if len(folds) < 2:
        return None
    weight_vectors = _synergy_weight_vectors(len(selected_members))
    stage_one = []
    for weights in weight_vectors:
        blend = pd.Series(0.0, index=panel.index)
        for weight, values in zip(weights, child_values):
            blend = blend + float(weight) * values
        fold_rows = _synergy_fold_evidence(panel, blend, folds, include_backtest=False)
        means = [row["posterior_mean"] for row in fold_rows]
        lowers = [row["lower_90"] for row in fold_rows]
        probabilities = [max(1e-6, row["positive_probability"]) for row in fold_rows]
        hhi = float(sum(weight * weight for weight in weights))
        dispersion = float(np.std(means)) if means else 1.0
        probability_geomean = float(math.exp(np.mean(np.log(probabilities)))) if probabilities else 0.0
        objectives = (
            min(lowers or [-1.0]),
            min(means or [-1.0]),
            probability_geomean,
            float(np.median(means or [-1.0])),
            -dispersion,
            -hhi,
        )
        stage_one.append({
            "weights": weights,
            "folds": fold_rows,
            "hhi": hhi,
            "dispersion": dispersion,
            "probability_geomean": probability_geomean,
            "objectives": objectives,
        })
    if not stage_one:
        return None
    stage_one_frontier = _pareto_records(stage_one, "objectives")
    stage_one_frontier.sort(
        key=lambda item: (
            min(item["objectives"]),
            item["objectives"][0],
            item["probability_geomean"],
            -item["hhi"],
        ),
        reverse=True,
    )
    risk_overlays = [
        {"name": "none", "features": [], "strength": 0.0, "smooth_window": None},
        {"name": "trend_reversal_soft_residual", "features": ["base_trend", "base_reversal"], "strength": 0.35, "smooth_window": None},
        {"name": "trend_crowding_soft_residual", "features": ["base_trend", "base_low_crowding", "turnover_rate"], "strength": 0.45, "smooth_window": None},
        {"name": "multi_style_soft_residual", "features": ["base_trend", "base_reversal", "base_value", "range_pct"], "strength": 0.35, "smooth_window": None},
        {"name": "trend_crowding_residual_smooth", "features": ["base_trend", "base_low_crowding", "turnover_rate"], "strength": 0.35, "smooth_window": 2},
    ]
    exact_trials = []
    for proposal in stage_one_frontier[: min(8, len(stage_one_frontier))]:
        raw_blend = pd.Series(0.0, index=panel.index)
        for weight, values in zip(proposal["weights"], child_values):
            raw_blend = raw_blend + float(weight) * values
        for overlay in risk_overlays:
            controlled = raw_blend
            if overlay["features"]:
                residual = compute_style_residual_series(panel, controlled, overlay["features"])
                controlled = overlay["strength"] * residual + (1.0 - overlay["strength"]) * controlled
            if overlay["smooth_window"]:
                controlled = causal_stock_transform(panel, controlled, "ts_mean", int(overlay["smooth_window"]))
            prepared, orientation, orientation_train_ic = _prepare_synergy_search_series(panel, controlled)
            fold_rows_with_curves = _synergy_fold_evidence(
                panel, prepared, folds, include_backtest=True,
            )
            search_backtest_curve = sorted(
                [
                    point
                    for fold_row in fold_rows_with_curves
                    for point in (fold_row.get("backtest_curve") or [])
                ],
                key=lambda point: str(point.get("date", "")),
            )
            fold_rows = [
                {key: value for key, value in fold_row.items() if key != "backtest_curve"}
                for fold_row in fold_rows_with_curves
            ]
            lowers = [row["lower_90"] for row in fold_rows]
            probabilities = [max(1e-6, row["positive_probability"]) for row in fold_rows]
            sharpes = [row["sharpe"] for row in fold_rows]
            annual_returns = [row["annual_return"] for row in fold_rows]
            drawdowns = [row["max_drawdown"] for row in fold_rows]
            turnovers = [row["turnover"] for row in fold_rows]
            objectives = (
                min(lowers or [-1.0]),
                float(math.exp(np.mean(np.log(probabilities)))) if probabilities else 0.0,
                min(sharpes or [-10.0]),
                float(np.median(sharpes or [-10.0])),
                float(np.median(annual_returns or [-1.0])),
                min(drawdowns or [-1.0]),
                -float(np.mean(turnovers or [1.0])),
                -proposal["hhi"],
            )
            exact_trials.append({
                **proposal,
                "risk_overlay": dict(overlay),
                "prepared_orientation": orientation,
                "orientation_train_rank_ic": orientation_train_ic,
                "exact_folds": fold_rows,
                "search_backtest_curve": search_backtest_curve,
                "exact_objectives": objectives,
            })
    if not exact_trials:
        return None

    internal_cscv_rows = [
        {
            "factor": f"synergy_exact_trial_{index}",
            "curve": item.get("search_backtest_curve") or [],
        }
        for index, item in enumerate(exact_trials, 1)
    ]
    internal_cscv_audit = annotate_cscv_pbo(
        internal_cscv_rows,
        "curve",
        prefix="synergy_",
    )
    global_overfit_count = int(round(
        safe_float(internal_cscv_audit.get("pbo"), 0.5)
        * int(internal_cscv_audit.get("splits") or 0)
    ))
    global_posterior = beta_binomial_overfit_posterior(
        global_overfit_count,
        int(internal_cscv_audit.get("splits") or 0),
    )
    max_selected_count = max(
        [int(row.get("synergy_cscv_selected_count") or 0) for row in internal_cscv_rows] or [0]
    )
    for item, cscv_row in zip(exact_trials, internal_cscv_rows):
        selected_count = int(cscv_row.get("synergy_cscv_selected_count") or 0)
        candidate_posterior_mean = cscv_row.get("synergy_cscv_pbo_posterior_mean")
        candidate_probability = cscv_row.get("synergy_cscv_probability_overfit_above_half")
        effective_pbo = (
            safe_float(candidate_posterior_mean, 0.5)
            if selected_count > 0
            else safe_float(global_posterior.get("posterior_mean"), 0.5)
        )
        effective_probability = (
            safe_float(candidate_probability, 0.5)
            if selected_count > 0
            else safe_float(global_posterior.get("probability_overfit_above_half"), 0.5)
        )
        effective_oos_percentile = safe_float(
            cscv_row.get("synergy_cscv_oos_rank_percentile_when_selected"),
            safe_float(internal_cscv_audit.get("mean_oos_rank_percentile"), 0.5),
        )
        selection_support = (
            selected_count / max(1, max_selected_count)
            if max_selected_count > 0
            else 0.0
        )
        item["internal_cscv"] = {
            "candidate_evidence_available": selected_count > 0,
            "selected_count": selected_count,
            "empirical_pbo_when_selected": cscv_row.get("synergy_cscv_pbo_when_selected"),
            "posterior_mean_pbo": (
                safe_float(candidate_posterior_mean, 0.5)
                if selected_count > 0
                else None
            ),
            "probability_pbo_above_half": (
                safe_float(candidate_probability, 0.5)
                if selected_count > 0
                else None
            ),
            "fallback_global_posterior_mean_pbo": safe_float(
                global_posterior.get("posterior_mean"), 0.5,
            ),
            "fallback_global_probability_pbo_above_half": safe_float(
                global_posterior.get("probability_overfit_above_half"), 0.5,
            ),
            "mean_oos_rank_percentile_when_selected": (
                effective_oos_percentile if selected_count > 0 else None
            ),
            "fallback_global_mean_oos_rank_percentile": safe_float(
                internal_cscv_audit.get("mean_oos_rank_percentile"), 0.5,
            ),
            "relative_selection_support": selection_support,
            "test_metrics_used": False,
        }
        item["exact_objectives"] = tuple(item["exact_objectives"]) + (
            1.0 - effective_pbo,
            effective_oos_percentile,
            selection_support,
        )

    exact_frontier = _pareto_records(exact_trials, "exact_objectives")
    dimensions = len(exact_frontier[0]["exact_objectives"])
    for item in exact_frontier:
        percentiles = []
        for dimension in range(dimensions):
            values = [record["exact_objectives"][dimension] for record in exact_frontier]
            percentiles.append(max(0.02, percentile_rank(item["exact_objectives"][dimension], values)))
        item["objective_percentiles"] = percentiles
        item["weakest_objective_percentile"] = min(percentiles)
        item["noncompensatory_rank_score"] = float(math.exp(np.mean(np.log(percentiles))))
    exact_frontier.sort(
        key=lambda item: (
            int(bool((item.get("internal_cscv") or {}).get("candidate_evidence_available"))),
            -safe_float((item.get("internal_cscv") or {}).get("posterior_mean_pbo"), 1.0),
            safe_float((item.get("internal_cscv") or {}).get("mean_oos_rank_percentile_when_selected"), 0.0),
            safe_float((item.get("internal_cscv") or {}).get("relative_selection_support"), 0.0),
            item["weakest_objective_percentile"],
            item["noncompensatory_rank_score"],
            item["exact_objectives"][0],
            item["exact_objectives"][2],
        ),
        reverse=True,
    )
    selected = exact_frontier[0]
    dsl = add_node(list(zip(child_nodes, selected["weights"])))
    selected_overlay = selected.get("risk_overlay") or {"name": "none", "features": [], "strength": 0.0, "smooth_window": None}
    if selected_overlay.get("features"):
        dsl = {
            "op": "style_residual",
            "child": dsl,
            "features": list(selected_overlay["features"]),
            "strength": float(selected_overlay["strength"]),
        }
    if selected_overlay.get("smooth_window"):
        dsl = {"op": "ts_mean", "child": dsl, "window": int(selected_overlay["smooth_window"])}
    parent_names = [
        member["candidate"].get("chinese_name") or member["factor"]
        for member in selected_members
    ]
    weight_map = {
        member["factor"]: float(weight)
        for member, weight in zip(selected_members, selected["weights"])
    }
    internal_trials = len(parent_set_trials) + len(stage_one) + len(exact_trials)
    lineage_event = {
        "synergy_parent_factors": [member["factor"] for member in selected_members],
        "synergy_parent_names": parent_names,
        "synergy_weights": weight_map,
        "selected_risk_overlay": selected_overlay,
        "parent_set_mean_abs_monthly_rank_corr": parent_set_trials[0]["mean_abs_monthly_rank_corr"],
        "selection_policy": "nsga2_style_pareto_then_candidate_cscv_evidence_then_weakest_rank_geometric_aggregation",
        "search_objectives": [
            "fold_posterior_lower_90",
            "fold_positive_probability",
            "cost_after_long_short_sharpe",
            "cost_after_long_short_annual_return",
            "max_drawdown",
            "turnover",
            "weight_diversification",
            "cscv_posterior_survival",
            "cscv_oos_rank_percentile",
            "cscv_selection_support",
        ],
        "selected_exact_folds": selected["exact_folds"],
        "selected_objective_percentiles": selected["objective_percentiles"],
        "selected_weakest_objective_percentile": selected["weakest_objective_percentile"],
        "selected_noncompensatory_rank_score": selected["noncompensatory_rank_score"],
        "selected_internal_cscv": selected.get("internal_cscv", {}),
        "feature_selection_audit": {
            "mode": "nested_train_validation_multiobjective_synergy_ensemble",
            "internal_hypotheses_evaluated": internal_trials,
            "parent_set_trials": len(parent_set_trials),
            "weight_trials": len(stage_one),
            "exact_cost_backtest_trials": len(exact_trials),
            "risk_control_variants": len(risk_overlays),
            "stage_one_pareto_frontier": len(stage_one_frontier),
            "exact_pareto_frontier": len(exact_frontier),
            "internal_cscv": {
                **internal_cscv_audit,
                "global_beta_binomial_posterior": global_posterior,
                "selection_scope": "embargoed_train_validation_exact_trials_only",
                "test_metrics_used": False,
            },
            "nested_fold_date_counts": {
                fold["name"]: len(fold["dates"]) for fold in folds
            },
            "boundary_embargo_observations": 2,
            "selection_split": "embargoed_train_and_validation_only",
            "validation_metrics_used": True,
            "test_metrics_used": False,
        },
        "methodology_references": [
            "Huatai multi-objective fundamental factor mining NSGA-II",
            "Dongwu LLM-GP island evolution and low-correlation screening",
            "Xinda DeepSeek factor enhancement plus Lasso-style synthesis",
            "Minsheng Meta_RiskControl style exposure penalty and incremental adaptation",
            "AlphaGen synergistic formulaic alpha collection",
        ],
        "test_metrics_used": False,
    }
    candidate = make_candidate(
        chinese_name=f"多目标协同合成因子{iteration}",
        channel="multiobjective_synergy_ensemble",
        family="nested_pareto_synergy_ensemble",
        dsl=dsl,
        hypothesis="在训练和验证的隔离折上联合优化后验RankIC、成本后多空收益、回撤、换手与低相关性，把多个互补逻辑合成为一个可执行因子。",
        data_scope=", ".join(sorted(set(
            feature
            for member in selected_members
            for feature in member["candidate"].get("required_fields", [])
        ))),
        windows=sorted(set(
            window
            for member in selected_members
            for window in (member["candidate"].get("windows") or [])
        )),
        neutralize=True,
        lineage=[lineage_event],
    )
    candidate["data_scope"] = ", ".join(candidate.get("required_fields") or [])
    if selected_overlay.get("smooth_window"):
        candidate["windows"] = sorted(set(
            (candidate.get("windows") or []) + [int(selected_overlay["smooth_window"])]
        ))
    component_text = "；".join(
        f"{name}×{weight:.3g}"
        for name, weight in zip(parent_names, selected["weights"])
    )
    candidate["construction"] = (
        f"先将父代子表达式分别做截面排序，再按训练/验证多目标帕累托搜索得到的非负权重合成：{component_text}。"
        f"风险控制采用 {selected_overlay.get('name')}，软残差强度 {safe_float(selected_overlay.get('strength'), 0.0):.2g}，"
        f"因果平滑窗口 {selected_overlay.get('smooth_window') or 0}。"
        "随后统一执行去极值、标准化、行业与市值中性化；测试集不参与父代、权重、风格收缩或方向选择。"
    )
    candidate["ensemble_components"] = [
        {
            "factor": member["factor"],
            "chinese_name": name,
            "weight": float(weight),
        }
        for member, name, weight in zip(selected_members, parent_names, selected["weights"])
    ]
    return candidate

def compile_candidate(panel, candidate):
    if candidate["dsl"].get("op") == "deep_svd":
        return compute_deep_svd(panel, candidate["dsl"].get("features", []))
    return eval_dsl(panel, candidate["dsl"])


def compute_deep_svd(panel, features):
    out = pd.Series(np.nan, index=panel.index)
    features = [f for f in features if f in panel.columns]
    if len(features) < 2:
        return pd.Series(0.0, index=panel.index)
    for _, idx in panel.groupby("trade_date").groups.items():
        g = panel.loc[idx, features].apply(pd.to_numeric, errors="coerce").fillna(0.5)
        if len(g) < 50:
            out.loc[idx] = 0.0
            continue
        x = (g - g.mean()) / g.std(ddof=0).replace(0, np.nan)
        x = x.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
        try:
            _, _, vt = np.linalg.svd(x, full_matrices=False)
            score = x @ vt[0]
            out.loc[idx] = pd.Series(score, index=idx).rank(pct=True)
        except np.linalg.LinAlgError:
            out.loc[idx] = 0.5
    return out.fillna(0.5)


def standardized_feature_frame(panel, features):
    features = [f for f in features if f in panel.columns]
    if not features:
        return pd.DataFrame(index=panel.index)
    x = panel[features].apply(pd.to_numeric, errors="coerce")
    out = pd.DataFrame(index=panel.index)
    for col in features:
        out[col] = panel.groupby("trade_date")[col].transform(zscore).fillna(0.0)
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def rank_output_by_date(panel, values):
    tmp = pd.DataFrame({"trade_date": panel["trade_date"], "value": values}, index=panel.index)
    return tmp.groupby("trade_date")["value"].transform(lambda s: rank01(s)).fillna(0.5)


def deterministic_projection(features, hidden, salt=0):
    raw = hashlib.sha1(("|".join(features) + f"|seed={salt}").encode("utf-8")).hexdigest()
    seed = int(raw[:8], 16)
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 1.0 / max(len(features), 1), size=(len(features), hidden))


def train_ridge_beta(design, target, ridge=2.5, sample_weight=None):
    if design.size == 0 or target.size == 0:
        return None
    x = np.asarray(design, dtype=float)
    y = np.asarray(target, dtype=float)
    weights = np.ones(len(y), dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    valid = np.isfinite(y) & np.isfinite(x).all(axis=1) & np.isfinite(weights) & (weights > 0)
    x, y, weights = x[valid], y[valid], weights[valid]
    if len(y) < max(200, x.shape[1] * 8):
        return None
    weights = weights / max(float(np.mean(weights)), 1e-12)
    root_weight = np.sqrt(weights)
    x_weighted = x * root_weight[:, None]
    y_weighted = y * root_weight
    xtx = x_weighted.T @ x_weighted
    penalty = np.eye(xtx.shape[0]) * ridge
    penalty[0, 0] = 0.0
    rhs = x_weighted.T @ y_weighted
    try:
        return np.linalg.solve(xtx + penalty, rhs)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(xtx + penalty, rhs, rcond=None)[0]


def record_runtime_operator_backend(panel, operator, backend, **details):
    runtime = panel.attrs.setdefault("runtime_operator_backends", {})
    runtime[str(operator)] = {
        "backend": str(backend),
        **{key: value for key, value in details.items() if value is not None},
    }


_DEEP_OPERATOR_VALUE_CACHE = {}


def cached_deep_supervised_ranker(panel, features, hidden=8, ensemble_seeds=3):
    cache = _DEEP_OPERATOR_VALUE_CACHE.setdefault(id(panel), {})
    key = json.dumps({
        "op": "deep_supervised_ranker",
        "features": list(features),
        "hidden": int(hidden),
        "ensemble_seeds": int(ensemble_seeds),
        "train_split": SPLITS["train"],
        "label_embargo": LABEL_HORIZON_PERIODS,
    }, sort_keys=True, ensure_ascii=True)
    if key in cache:
        entry = cache[key]
        out = pd.Series(np.asarray(entry["values"], dtype=float).copy(), index=panel.index)
        out.attrs.update(dict(entry.get("attrs") or {}))
        backend = out.attrs.get("implementation_backend")
        if backend:
            record_runtime_operator_backend(
                panel,
                "deep_supervised_ranker",
                backend,
                ensemble_members=out.attrs.get("ensemble_members"),
                training_rows=out.attrs.get("training_rows"),
                fallback_reason=out.attrs.get("fallback_reason"),
                cache_hit=True,
            )
        return out
    out = compute_deep_supervised_ranker(
        panel,
        features,
        hidden=hidden,
        ensemble_seeds=ensemble_seeds,
    )
    cache[key] = {
        "values": out.to_numpy(dtype=float).copy(),
        "attrs": dict(out.attrs),
    }
    return out


def compute_deep_supervised_ranker(panel, features, hidden=8, ensemble_seeds=3):
    x = standardized_feature_frame(panel, features)
    if x.shape[1] < 2:
        out = pd.Series(0.5, index=panel.index)
        out.attrs["implementation_backend"] = "insufficient_features_constant"
        record_runtime_operator_backend(panel, "deep_supervised_ranker", "insufficient_features_constant")
        return out
    x_np = x.to_numpy(dtype=float)
    train = split_panel(panel, "train")
    train_mask = panel.index.isin(train.index)
    ranked_target = panel.groupby("trade_date")["label_next_ret"].transform(
        lambda values: values.rank(pct=True) - 0.5
    )
    y = ranked_target.loc[train_mask].to_numpy(dtype=float)
    train_dates = panel.loc[train_mask, "trade_date"]
    date_counts = train_dates.value_counts()
    sample_weight = train_dates.map(lambda date: 1.0 / max(int(date_counts.get(date, 1)), 1)).to_numpy(dtype=float)

    train_frame = panel.loc[train_mask, ["trade_date", "ts_code"]].copy()
    train_frame["_sample_key"] = pd.util.hash_pandas_object(
        train_frame[["trade_date", "ts_code"]].astype(str), index=False
    ).to_numpy(dtype=np.uint64)
    sampled_index = (
        train_frame.sort_values(["trade_date", "_sample_key"])
        .groupby("trade_date", sort=False)
        .head(320)
        .index
    )
    sampled_positions = panel.index.get_indexer(sampled_index)
    sampled_target = ranked_target.loc[sampled_index].to_numpy(dtype=float)
    valid_sample = (
        (sampled_positions >= 0)
        & np.isfinite(sampled_target)
        & np.isfinite(x_np[np.maximum(sampled_positions, 0)]).all(axis=1)
    )
    sampled_positions = sampled_positions[valid_sample]
    sampled_target = sampled_target[valid_sample]
    neural_predictions = []
    neural_error = None
    if len(sampled_positions) >= 500:
        try:
            from sklearn.neural_network import MLPRegressor

            y_sample = sampled_target
            for seed in range(max(1, int(ensemble_seeds))):
                model = MLPRegressor(
                    hidden_layer_sizes=(max(8, int(hidden)), max(4, int(hidden) // 2)),
                    activation="tanh",
                    solver="adam",
                    alpha=0.005,
                    batch_size=min(512, max(64, len(sampled_positions) // 20)),
                    learning_rate_init=0.002,
                    max_iter=80,
                    early_stopping=True,
                    validation_fraction=0.15,
                    n_iter_no_change=8,
                    random_state=seed,
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model.fit(x_np[sampled_positions], y_sample)
                neural_predictions.append(model.predict(x_np))
        except Exception as exc:
            neural_error = f"{type(exc).__name__}: {exc}"[:500]
            neural_predictions = []

    if neural_predictions:
        score = np.median(np.column_stack(neural_predictions), axis=1)
        out = rank_output_by_date(panel, pd.Series(score, index=panel.index))
        out.attrs["implementation_backend"] = "sklearn_mlp_train_only_date_balanced_ensemble"
        out.attrs["ensemble_members"] = len(neural_predictions)
        out.attrs["training_rows"] = int(len(sampled_positions))
        record_runtime_operator_backend(
            panel,
            "deep_supervised_ranker",
            "sklearn_mlp_train_only_date_balanced_ensemble",
            ensemble_members=len(neural_predictions),
            training_rows=int(len(sampled_positions)),
        )
        return out

    fallback_predictions = []
    for seed in range(max(1, int(ensemble_seeds))):
        proj = deterministic_projection(list(x.columns), max(2, hidden), salt=seed)
        hidden_np = np.tanh(x_np @ proj)
        design = np.column_stack([np.ones(len(panel)), x_np, hidden_np, x_np * hidden_np[:, :1]])
        beta = train_ridge_beta(design[train_mask], y, sample_weight=sample_weight)
        if beta is not None:
            fallback_predictions.append(design @ beta)
    if fallback_predictions:
        score = np.median(np.column_stack(fallback_predictions), axis=1)
        out = rank_output_by_date(panel, pd.Series(score, index=panel.index))
        out.attrs["implementation_backend"] = "numpy_random_feature_ridge_fallback"
        out.attrs["ensemble_members"] = len(fallback_predictions)
        out.attrs["fallback_reason"] = neural_error or "insufficient_valid_neural_training_rows"
        record_runtime_operator_backend(
            panel,
            "deep_supervised_ranker",
            "numpy_random_feature_ridge_fallback",
            ensemble_members=len(fallback_predictions),
            training_rows=int(len(sampled_positions)),
            fallback_reason=out.attrs["fallback_reason"],
        )
        return out
    out = rank_output_by_date(panel, x.mean(axis=1))
    out.attrs["implementation_backend"] = "equal_weight_fallback"
    out.attrs["fallback_reason"] = neural_error or "all_supervised_backends_failed"
    record_runtime_operator_backend(
        panel,
        "deep_supervised_ranker",
        "equal_weight_fallback",
        training_rows=int(len(sampled_positions)),
        fallback_reason=out.attrs["fallback_reason"],
    )
    return out


def train_feature_ic_weights(panel, features):
    weights = []
    train = split_panel(panel, "train")
    for feature in features:
        vals = []
        for _, g in train.groupby("trade_date"):
            if feature not in g or len(g) < 50:
                continue
            ic = pd.to_numeric(g[feature], errors="coerce").rank().corr(g["label_next_ret"].rank())
            if pd.notna(ic):
                vals.append(float(ic))
        mean_ic = float(np.nanmean(vals)) if vals else 0.0
        stability = abs(mean_ic) / (float(np.nanstd(vals)) + 0.01) if vals else 0.0
        weights.append((feature, mean_ic, stability))
    return weights


def compute_transformer_attention_factor(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.shape[1] < 2:
        return pd.Series(0.5, index=panel.index)
    stats = train_feature_ic_weights(panel, list(x.columns))
    logits = np.array([abs(ic) * 12.0 + min(stab, 5.0) for _, ic, stab in stats], dtype=float)
    logits = logits - np.nanmax(logits) if len(logits) else logits
    attn = np.exp(logits)
    attn = attn / attn.sum() if attn.sum() else np.ones(len(stats)) / max(len(stats), 1)
    signs = np.array([1.0 if ic >= 0 else -1.0 for _, ic, _ in stats])
    score = x.to_numpy(dtype=float) @ (attn * signs)
    return rank_output_by_date(panel, pd.Series(score, index=panel.index))


def compute_vae_bottleneck_factor(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.shape[1] < 2:
        return pd.Series(0.5, index=panel.index)
    train = split_panel(panel, "train")
    train_mask = panel.index.isin(train.index)
    train_x = x.to_numpy(dtype=float)[train_mask]
    if len(train_x) < 200:
        return rank_output_by_date(panel, x.mean(axis=1))
    try:
        _, _, vt = np.linalg.svd(train_x, full_matrices=False)
        w1 = vt[0]
        w2 = vt[1] if len(vt) > 1 else vt[0]
        latent = np.tanh(x.to_numpy(dtype=float) @ w1)
        recon = np.outer(latent, w1)
        residual = np.linalg.norm(x.to_numpy(dtype=float) - recon, axis=1)
        score = latent - 0.15 * zscore(pd.Series(residual, index=panel.index)).to_numpy(dtype=float)
        score += 0.25 * np.tanh(x.to_numpy(dtype=float) @ w2)
        return rank_output_by_date(panel, pd.Series(score, index=panel.index))
    except np.linalg.LinAlgError:
        return rank_output_by_date(panel, x.mean(axis=1))


def graph_concept_residual_from_base(panel, base):
    base = pd.to_numeric(base, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    tmp = pd.DataFrame({
        "trade_date": panel["trade_date"],
        "industry_name": panel["industry_name"].fillna("UNCLASSIFIED"),
        "base": base,
        "mv_rank": panel.groupby("trade_date")["total_mv"].transform(lambda s: rank01(s)),
    }, index=panel.index)
    tmp["concept"] = tmp.groupby("trade_date")["mv_rank"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop") if len(s) >= 50 else 0
    )
    industry_shared = tmp.groupby(["trade_date", "industry_name"])["base"].transform("mean")
    concept_shared = tmp.groupby(["trade_date", "concept"])["base"].transform("mean")
    score = 0.55 * base + 0.25 * (base - industry_shared) + 0.20 * (base - concept_shared)
    return rank_output_by_date(panel, score)


def compute_graph_concept_residual(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.empty:
        return pd.Series(0.5, index=panel.index)
    return graph_concept_residual_from_base(panel, x.mean(axis=1))


def compute_graph_concept_residual_series(panel, raw_series):
    raw = pd.to_numeric(raw_series, errors="coerce")
    base = pd.DataFrame({"trade_date": panel["trade_date"], "raw": raw}, index=panel.index)
    standardized = base.groupby("trade_date")["raw"].transform(zscore).fillna(0.0)
    return graph_concept_residual_from_base(panel, standardized)

def compute_regime_mixture_factor(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.empty:
        return pd.Series(0.5, index=panel.index)
    feature_map = {f: x[f] for f in x.columns}
    value_quality = np.mean([feature_map[f] for f in x.columns if f in {"base_quality", "base_value", "base_growth"}] or [x.mean(axis=1)], axis=0)
    trend_flow = np.mean([feature_map[f] for f in x.columns if f in {"base_trend", "base_moneyflow", "base_event_attention"}] or [x.mean(axis=1)], axis=0)
    defensive = np.mean([feature_map[f] for f in x.columns if f in {"base_low_crowding", "base_reversal"}] or [x.mean(axis=1)], axis=0)
    regime = pd.DataFrame({
        "trade_date": panel["trade_date"],
        "market_trend": panel.groupby("trade_date")["mom20"].transform("median").fillna(0.0),
        "market_range": panel.groupby("trade_date")["range_pct"].transform("median").fillna(0.0),
    }, index=panel.index)
    trend_gate = 1.0 / (1.0 + np.exp(-20.0 * regime["market_trend"].clip(-0.2, 0.2)))
    risk_gate = 1.0 / (1.0 + np.exp(35.0 * (regime["market_range"].clip(0, 0.2) - 0.05)))
    score = trend_gate * trend_flow + (1.0 - trend_gate) * value_quality
    score = risk_gate * score + (1.0 - risk_gate) * defensive
    return rank_output_by_date(panel, pd.Series(score, index=panel.index))


def compute_tabnet_sparse_gate_factor(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.shape[1] < 2:
        return pd.Series(0.5, index=panel.index)
    stats = train_feature_ic_weights(panel, list(x.columns))
    raw = np.array([abs(ic) * (1.0 + min(stab, 4.0)) for _, ic, stab in stats], dtype=float)
    if not np.isfinite(raw).any() or raw.sum() <= 0:
        raw = np.ones(len(stats), dtype=float)
    keep = max(2, min(4, len(raw)))
    order = np.argsort(raw)[::-1]
    mask = np.zeros(len(raw), dtype=float)
    mask[order[:keep]] = raw[order[:keep]]
    mask = mask / mask.sum() if mask.sum() else np.ones(len(raw)) / len(raw)
    signs = np.array([1.0 if ic >= 0 else -1.0 for _, ic, _ in stats])
    x_np = x.to_numpy(dtype=float)
    gated = x_np * mask * signs
    main = gated.sum(axis=1)
    interaction = np.zeros(len(panel), dtype=float)
    for a in range(keep):
        for b in range(a + 1, keep):
            ia, ib = order[a], order[b]
            interaction += np.tanh(x_np[:, ia] * x_np[:, ib]) * mask[ia] * mask[ib] * signs[ia] * signs[ib]
    score = main + 1.8 * interaction
    return rank_output_by_date(panel, pd.Series(score, index=panel.index))


def compute_temporal_convolution_factor(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.empty:
        return pd.Series(0.5, index=panel.index)
    available = set(x.columns)
    trend = x["base_trend"] if "base_trend" in available else x.mean(axis=1)
    reversal = x["base_reversal"] if "base_reversal" in available else -trend
    flow = x["base_moneyflow"] if "base_moneyflow" in available else x.mean(axis=1)
    kline = x["base_kline_context"] if "base_kline_context" in available else trend
    defensive = x["base_low_crowding"] if "base_low_crowding" in available else x.mean(axis=1)
    event = x["base_event_attention"] if "base_event_attention" in available else flow
    tmp = pd.DataFrame({
        "ts_code": panel["ts_code"],
        "trade_date": panel["trade_date"],
        "trend": trend,
        "reversal": reversal,
        "flow": flow,
        "kline": kline,
        "defensive": defensive,
        "event": event,
    }, index=panel.index).sort_values(["ts_code", "trade_date"])
    g = tmp.groupby("ts_code", sort=False)
    conv_fast = 0.50 * tmp["reversal"] + 0.30 * g["flow"].transform(lambda s: s.rolling(2, min_periods=1).mean()) + 0.20 * tmp["kline"]
    conv_mid = 0.35 * g["trend"].transform(lambda s: s.rolling(3, min_periods=1).mean()) + 0.30 * g["flow"].transform(lambda s: s.rolling(3, min_periods=1).mean()) + 0.20 * tmp["defensive"] + 0.15 * tmp["event"]
    conv_slow = 0.45 * g["trend"].transform(lambda s: s.rolling(6, min_periods=1).mean()) + 0.35 * tmp["defensive"] + 0.20 * g["event"].transform(lambda s: s.rolling(4, min_periods=1).mean())
    score_sorted = 0.30 * conv_fast + 0.45 * conv_mid + 0.25 * conv_slow
    score = pd.Series(index=tmp.index, data=score_sorted).reindex(panel.index).fillna(0.0)
    return rank_output_by_date(panel, score)


def compute_contrastive_regime_factor(panel, features):
    x = standardized_feature_frame(panel, features)
    if x.shape[1] < 2:
        return pd.Series(0.5, index=panel.index)

    daily = panel.groupby("trade_date").agg(
        market_trend=("mom20", "median"),
        market_range=("range_pct", "median"),
    ).sort_index()
    train_dates = set(split_panel(panel, "train")["trade_date"].astype(str).unique())
    train_daily = daily[daily.index.astype(str).isin(train_dates)]
    if train_daily.empty:
        train_daily = daily.iloc[: max(1, len(daily) // 2)]
    trend_cut = safe_float(train_daily["market_trend"].median(), 0.0)
    range_cut = safe_float(train_daily["market_range"].median(), 0.0)
    daily["regime"] = np.where(daily["market_trend"] >= trend_cut, "up", "down")
    daily["regime"] = daily["regime"] + np.where(daily["market_range"] >= range_cut, "_high_vol", "_low_vol")
    regime = panel["trade_date"].map(daily["regime"]).fillna("down_high_vol")

    train_mask = panel["trade_date"].astype(str).isin(train_dates)
    features = list(x.columns)
    global_stats = train_feature_ic_weights(panel, features)

    def normalized_weights(stats):
        raw = np.array([safe_float(ic, 0.0) * (1.0 + min(safe_float(stability, 0.0), 4.0)) for _, ic, stability in stats], dtype=float)
        denom = np.abs(raw).sum()
        if denom <= 1e-12:
            return np.ones(len(features), dtype=float) / max(1, len(features))
        return raw / denom

    weight_map = {"global": normalized_weights(global_stats)}
    states = ["up_low_vol", "up_high_vol", "down_low_vol", "down_high_vol"]
    for state in states:
        state_panel = panel[train_mask & (regime == state)]
        if state_panel["trade_date"].nunique() < 6:
            weight_map[state] = weight_map["global"]
            continue
        weight_map[state] = normalized_weights(train_feature_ic_weights(state_panel, features))

    x_np = x.to_numpy(dtype=float)
    all_scores = np.column_stack([x_np @ weight_map[state] for state in states])
    state_index = {state: i for i, state in enumerate(states)}
    selected_idx = regime.map(state_index).fillna(0).astype(int).to_numpy()
    selected = all_scores[np.arange(len(panel)), selected_idx]
    invariant = all_scores.mean(axis=1)
    disagreement = all_scores.std(axis=1)
    confidence = 1.0 / (1.0 + disagreement)
    score = (0.62 * selected + 0.38 * invariant) * confidence
    return rank_output_by_date(panel, pd.Series(score, index=panel.index))


def compute_style_residual_series(panel, values, features, ridge=0.05):
    features = [feature for feature in dict.fromkeys(features or []) if feature in panel.columns]
    if not features:
        return pd.to_numeric(values, errors="coerce")
    out = pd.Series(np.nan, index=panel.index)
    numeric_values = pd.to_numeric(values, errors="coerce")
    for _, index in panel.groupby("trade_date").groups.items():
        y = numeric_values.loc[index]
        x = panel.loc[index, features].apply(pd.to_numeric, errors="coerce")
        valid = y.notna() & x.notna().mean(axis=1).ge(0.70)
        if int(valid.sum()) < max(50, len(features) * 8):
            out.loc[index] = zscore(y)
            continue
        xv = x.loc[valid].copy()
        xv = (xv - xv.median()) / xv.std(ddof=0).replace(0.0, np.nan)
        xv = xv.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        matrix = np.column_stack([np.ones(len(xv)), xv.to_numpy(dtype=float)])
        penalty = np.eye(matrix.shape[1], dtype=float) * float(ridge)
        penalty[0, 0] = 0.0
        try:
            beta = np.linalg.solve(
                matrix.T @ matrix + penalty,
                matrix.T @ y.loc[valid].to_numpy(dtype=float),
            )
            residual = y.loc[valid].to_numpy(dtype=float) - matrix @ beta
            out.loc[xv.index] = zscore(pd.Series(residual, index=xv.index))
        except np.linalg.LinAlgError:
            out.loc[index] = zscore(y)
    return out.fillna(0.0)

def neutralize_size_industry(panel, raw_col):
    out = pd.Series(np.nan, index=panel.index)
    for _, idx in panel.groupby("trade_date").groups.items():
        g = panel.loc[idx]
        y = pd.to_numeric(g[raw_col], errors="coerce")
        valid = y.notna() & g["total_mv"].notna()
        if valid.sum() < 80:
            out.loc[idx] = zscore(y)
            continue
        sub = g.loc[valid, ["total_mv", "industry_name"]].copy()
        x = pd.DataFrame({"log_mv": np.log(pd.to_numeric(sub["total_mv"], errors="coerce").clip(lower=1.0))}, index=sub.index)
        dummies = pd.get_dummies(sub["industry_name"].fillna("UNCLASSIFIED"), prefix="ind", dtype=float)
        x = pd.concat([pd.Series(1.0, index=sub.index, name="const"), x, dummies], axis=1)
        try:
            beta, *_ = np.linalg.lstsq(x.to_numpy(dtype=float), y.loc[valid].to_numpy(dtype=float), rcond=None)
            resid = y.loc[valid] - x.to_numpy(dtype=float) @ beta
            out.loc[valid[valid].index] = zscore(pd.Series(resid, index=valid[valid].index))
        except Exception:
            out.loc[idx] = zscore(y)
    return out.fillna(0.0)



def train_direction_rank_ic(panel, factor_col):
    sub = split_panel(panel, "train") if "trade_date" in panel.columns else panel
    vals = []
    for _, g in sub[["trade_date", factor_col, "label_next_ret"]].dropna().groupby("trade_date"):
        if len(g) < 50:
            continue
        ic = g[factor_col].rank().corr(g["label_next_ret"].rank())
        if pd.notna(ic):
            vals.append(float(ic))
    return float(np.nanmean(vals)) if vals else 0.0


def prepare_factor(panel, candidate):
    raw_col = f"{candidate['factor_name']}__raw"
    panel.attrs["runtime_operator_backends"] = {}
    compiled = compile_candidate(panel, candidate)
    implementation_audit = dsl_implementation_audit(candidate.get("dsl", {}))
    runtime_operator_backends = dict(panel.attrs.pop("runtime_operator_backends", {}))
    runtime_backend = getattr(compiled, "attrs", {}).get("implementation_backend")
    if runtime_backend:
        implementation_audit["runtime_backend"] = runtime_backend
        implementation_audit["ensemble_members"] = getattr(compiled, "attrs", {}).get("ensemble_members")
        implementation_audit["training_rows"] = getattr(compiled, "attrs", {}).get("training_rows")
        implementation_audit["fallback_reason"] = getattr(compiled, "attrs", {}).get("fallback_reason")
    if runtime_operator_backends:
        implementation_audit["runtime_operator_backends"] = runtime_operator_backends
    mlp_runtime = runtime_operator_backends.get("deep_supervised_ranker", {})
    if "deep_supervised_ranker" in collect_dsl_ops(candidate.get("dsl", {})):
        implementation_audit["neural_network_trained"] = str(mlp_runtime.get("backend", runtime_backend)).startswith("sklearn_mlp_")
    candidate["implementation_audit"] = implementation_audit
    panel[raw_col] = compiled
    panel[raw_col] = winsorize_by_date(panel, raw_col).fillna(0.0)
    z_col = f"{candidate['factor_name']}__z"
    panel[z_col] = panel.groupby("trade_date")[raw_col].transform(zscore).fillna(0.0)
    if candidate.get("neutralize", True):
        prepared = neutralize_size_industry(panel, z_col)
        variant = "winsor_zscore_train_direction_size_industry_neutral"
    else:
        prepared = panel[z_col]
        variant = "winsor_zscore_train_direction_locked"
    prep_col = f"{candidate['factor_name']}__prepared"
    panel[prep_col] = prepared.fillna(0.0)
    direction_ic = train_direction_rank_ic(panel, prep_col)
    orientation = -1.0 if direction_ic < 0 else 1.0
    panel[candidate["factor_name"]] = orientation * panel[prep_col]
    candidate["orientation"] = "flipped_by_train_rank_ic" if orientation < 0 else "kept_by_train_rank_ic"
    candidate["orientation_train_rank_ic"] = direction_ic
    return raw_col, variant


def static_audit(panel, candidate):
    issues = []
    dsl_ops = collect_dsl_ops(candidate.get("dsl", {}))
    temporal_ops = sorted(dsl_ops & {"ts_delta", "ts_mean", "ts_std", "ts_zscore"})
    temporal_audit = {
        "operators": temporal_ops,
        "observation_unit": "ordered_signal_dates_within_each_stock",
        "past_only": True,
        "mode": "not_used",
    }
    if temporal_ops:
        dates = pd.to_datetime(pd.Series(sorted(panel["trade_date"].astype(str).unique())), errors="coerce").dropna()
        gaps = dates.diff().dt.days.dropna()
        maximum_gap = safe_float(gaps.max(), 0.0)
        median_gap = safe_float(gaps.median(), 0.0)
        contiguous_monthly = bool(len(dates) >= 12 and maximum_gap <= 62)
        temporal_audit.update({
            "mode": "contiguous_monthly" if contiguous_monthly else "sparse_research_observation_lag",
            "dates": int(len(dates)),
            "median_gap_days": median_gap,
            "maximum_gap_days": maximum_gap,
            "production_eligible": contiguous_monthly,
        })
        if not contiguous_monthly:
            issues.append({
                "severity": "warning",
                "code": "sparse_temporal_observation_probe",
                "message": "Temporal windows count sampled signal observations in this probe; production requires a contiguous monthly panel.",
            })
    for field in candidate.get("required_fields", []):
        if field not in panel.columns:
            issues.append({"severity": "blocked", "code": "missing_field", "field": field})
        else:
            cov = pd.to_numeric(panel[field], errors="coerce").notna().mean()
            if cov < 0.20:
                issues.append({"severity": "warning", "code": "low_field_coverage", "field": field, "coverage": float(cov)})
    if candidate.get("complexity", 0) > 28:
        issues.append({"severity": "warning", "code": "high_complexity", "complexity": candidate["complexity"]})
    if "label_next_ret" in candidate.get("required_fields", []):
        issues.append({"severity": "blocked", "code": "label_leakage"})
    blocked = any(x["severity"] == "blocked" for x in issues)
    return {"passed": not blocked, "issues": issues, "causal_temporal_audit": temporal_audit}


def drop_tail_signal_dates(panel, periods=LABEL_HORIZON_PERIODS):
    if panel.empty or periods <= 0 or "trade_date" not in panel:
        return panel
    dates = sorted(str(value) for value in panel["trade_date"].dropna().unique())
    if len(dates) <= periods:
        return panel.iloc[0:0]
    return panel[panel["trade_date"].astype(str).isin(set(dates[:-periods]))]


def split_panel(panel, split_name):
    a, b = SPLITS[split_name]
    sub = panel[(panel["trade_date"] >= a) & (panel["trade_date"] <= b)]
    if split_name in {"train", "valid"}:
        sub = drop_tail_signal_dates(sub, LABEL_HORIZON_PERIODS)
    return sub


def search_panel(panel):
    """Train+validation research sample, embargoed before the sealed test."""
    sub = panel[
        (panel["trade_date"] >= SPLITS["train"][0])
        & (panel["trade_date"] <= SPLITS["valid"][1])
    ]
    return drop_tail_signal_dates(sub, LABEL_HORIZON_PERIODS)


_RESEARCH_EVIDENCE_CACHE = {}


def normal_posterior_evidence(values, prior_mean=0.0, prior_scale=0.04):
    values = np.asarray([safe_float(value, np.nan) for value in (values or [])], dtype=float)
    values = values[np.isfinite(values)]
    count = int(len(values))
    if count == 0:
        return {
            "observations": 0,
            "sample_mean": 0.0,
            "posterior_mean": float(prior_mean),
            "posterior_std": float(prior_scale),
            "positive_probability": 0.5,
            "lower_90": float(prior_mean - 1.2815515655446004 * prior_scale),
            "method": "normal_empirical_bayes_with_weak_zero_prior",
        }
    sample_mean = float(np.mean(values))
    if count > 1:
        sample_var = float(np.var(values, ddof=1))
    else:
        sample_var = float(prior_scale ** 2)
    sample_var = max(sample_var, float((prior_scale * 0.35) ** 2))
    sampling_var = sample_var / max(1, count)
    prior_var = float(prior_scale ** 2)
    posterior_var = 1.0 / (1.0 / prior_var + 1.0 / sampling_var)
    posterior_mean = posterior_var * (float(prior_mean) / prior_var + sample_mean / sampling_var)
    posterior_std = math.sqrt(max(posterior_var, 1e-12))
    positive_probability = NormalDist().cdf(posterior_mean / posterior_std)
    return {
        "observations": count,
        "sample_mean": sample_mean,
        "sample_std": float(math.sqrt(sample_var)),
        "posterior_mean": float(posterior_mean),
        "posterior_std": float(posterior_std),
        "positive_probability": float(np.clip(positive_probability, 0.0, 1.0)),
        "lower_90": float(posterior_mean - 1.2815515655446004 * posterior_std),
        "method": "normal_empirical_bayes_with_weak_zero_prior",
    }


def rank_center_by_date(panel, values):
    frame = pd.DataFrame({
        "trade_date": panel["trade_date"].astype(str),
        "value": pd.to_numeric(values, errors="coerce"),
    }, index=panel.index)
    return frame.groupby("trade_date")["value"].transform(lambda series: series.rank(pct=True) - 0.5)


def residualize_cross_section(panel, target, control):
    frame = pd.DataFrame({
        "trade_date": panel["trade_date"].astype(str),
        "target": pd.to_numeric(target, errors="coerce"),
        "control": pd.to_numeric(control, errors="coerce"),
    }, index=panel.index)
    out = pd.Series(np.nan, index=panel.index, dtype=float)
    for _, idx in frame.groupby("trade_date", sort=False).groups.items():
        group = frame.loc[idx]
        valid = group["target"].notna() & group["control"].notna()
        if valid.sum() < 30:
            out.loc[idx] = group["target"]
            continue
        y = group.loc[valid, "target"]
        x = group.loc[valid, "control"]
        y = y - y.mean()
        x = x - x.mean()
        variance = float(np.dot(x, x))
        beta = float(np.dot(x, y) / variance) if variance > 1e-12 else 0.0
        out.loc[y.index] = y - beta * x
    return out


def monthly_pair_evidence(panel, left, right):
    frame = pd.DataFrame({
        "trade_date": panel["trade_date"].astype(str),
        "left": pd.to_numeric(left, errors="coerce"),
        "right": pd.to_numeric(right, errors="coerce"),
    }, index=panel.index).dropna()
    rows = []
    for date, group in frame.groupby("trade_date", sort=True):
        if len(group) < 50:
            continue
        value = group["left"].rank().corr(group["right"].rank())
        if pd.notna(value):
            rows.append({"date": str(date), "rank_ic": float(value)})
    posterior = normal_posterior_evidence([row["rank_ic"] for row in rows])
    return {
        "rank_ic": float(np.mean([row["rank_ic"] for row in rows])) if rows else 0.0,
        "positive_rate": sum(row["rank_ic"] > 0 for row in rows) / len(rows) if rows else 0.0,
        "periods": len(rows),
        "series": rows,
        "posterior": posterior,
    }


def baseline_research_state(panel):
    cache = _RESEARCH_EVIDENCE_CACHE.setdefault(id(panel), {})
    if "baseline" in cache:
        stored = cache["baseline"]
        return {
            **stored["audit"],
            "baseline_rank": pd.Series(stored["baseline_rank"], index=panel.index, dtype=float),
            "target_rank": pd.Series(stored["target_rank"], index=panel.index, dtype=float),
            "target_residual": pd.Series(stored["target_residual"], index=panel.index, dtype=float),
            "sample_positions": np.asarray(stored["sample_positions"], dtype=int),
        }

    features = [feature for feature in BASE_FEATURES if feature in panel.columns]
    x = standardized_feature_frame(panel, features)
    target_rank = rank_center_by_date(panel, panel["label_next_ret"])
    train_index = split_panel(panel, "train").index
    sample_frame = deterministic_cross_section_sample(
        panel.loc[train_index, ["trade_date", "ts_code"]],
        sample_per_date=260,
    )
    sample_positions = panel.index.get_indexer(sample_frame.index)
    sample_positions = sample_positions[sample_positions >= 0]
    design = np.column_stack([np.ones(len(panel)), x.to_numpy(dtype=float)])
    beta = train_ridge_beta(
        design[sample_positions],
        target_rank.iloc[sample_positions].to_numpy(dtype=float),
        ridge=12.0,
    )
    if beta is None:
        prediction = x.mean(axis=1).to_numpy(dtype=float) if not x.empty else np.zeros(len(panel), dtype=float)
        backend = "equal_weight_cross_sectional_baseline"
        beta_values = []
    else:
        prediction = design @ beta
        backend = "train_only_date_balanced_ridge_baseline"
        beta_values = [float(value) for value in beta]
    baseline_rank = rank_center_by_date(panel, pd.Series(prediction, index=panel.index))
    target_residual = residualize_cross_section(panel, target_rank, baseline_rank)
    audit = {
        "features": features,
        "backend": backend,
        "training_rows": int(len(sample_positions)),
        "fit_split": "embargoed_train_only",
        "frozen_on_validation_and_test": True,
        "target": "cross_sectional_rank_of_next_executable_return",
        "coefficients": beta_values,
    }
    cache["baseline"] = {
        "audit": audit,
        "baseline_rank": baseline_rank.to_numpy(dtype=float),
        "target_rank": target_rank.to_numpy(dtype=float),
        "target_residual": target_residual.to_numpy(dtype=float),
        "sample_positions": sample_positions.tolist(),
    }
    return {
        **audit,
        "baseline_rank": baseline_rank,
        "target_rank": target_rank,
        "target_residual": target_residual,
        "sample_positions": sample_positions,
    }


def incremental_factor_evidence(panel, factor_name):
    baseline = baseline_research_state(panel)
    baseline_rank = baseline["baseline_rank"]
    target_rank = baseline["target_rank"]
    target_residual = baseline["target_residual"]
    candidate_rank = rank_center_by_date(panel, panel[factor_name])
    candidate_residual = residualize_cross_section(panel, candidate_rank, baseline_rank)

    design = np.column_stack([
        np.ones(len(panel)),
        baseline_rank.to_numpy(dtype=float),
        candidate_residual.to_numpy(dtype=float),
    ])
    sample_positions = np.asarray(baseline["sample_positions"], dtype=int)
    beta = train_ridge_beta(
        design[sample_positions],
        target_rank.iloc[sample_positions].to_numpy(dtype=float),
        ridge=8.0,
    )
    if beta is None:
        combined_prediction = baseline_rank.fillna(0.0) + 0.20 * candidate_residual.fillna(0.0)
        combination_backend = "conservative_residual_addition_fallback"
        beta_values = []
    else:
        combined_prediction = pd.Series(design @ beta, index=panel.index)
        combination_backend = "train_only_ridge_baseline_plus_candidate_residual"
        beta_values = [float(value) for value in beta]

    result = {
        "method": "dual_residual_incremental_ic_and_frozen_downstream_synergy",
        "baseline_audit": {
            key: value for key, value in baseline.items()
            if key not in {"baseline_rank", "target_rank", "target_residual", "sample_positions"}
        },
        "combination_audit": {
            "backend": combination_backend,
            "fit_split": "embargoed_train_only",
            "frozen_on_validation_and_test": True,
            "coefficients": beta_values,
            "candidate_residualized_against": "frozen_baseline_prediction_cross_section",
            "return_residualized_against": "frozen_baseline_prediction_cross_section",
        },
    }
    split_frames = {
        "train": split_panel(panel, "train"),
        "valid": split_panel(panel, "valid"),
        "test": split_panel(panel, "test"),
        "search": search_panel(panel),
        "full": split_panel(panel, "full"),
    }
    for split_name, sub in split_frames.items():
        idx = sub.index
        residual = monthly_pair_evidence(sub, candidate_residual.loc[idx], target_residual.loc[idx])
        baseline_metric = monthly_pair_evidence(sub, baseline_rank.loc[idx], target_rank.loc[idx])
        combined_metric = monthly_pair_evidence(sub, combined_prediction.loc[idx], target_rank.loc[idx])
        baseline_by_date = {row["date"]: row["rank_ic"] for row in baseline_metric["series"]}
        marginal_series = [
            {
                "date": row["date"],
                "rank_ic_gain": float(row["rank_ic"] - baseline_by_date[row["date"]]),
            }
            for row in combined_metric["series"]
            if row["date"] in baseline_by_date
        ]
        marginal_posterior = normal_posterior_evidence([row["rank_ic_gain"] for row in marginal_series], prior_scale=0.02)
        result[split_name] = {
            "residual_rank_ic": residual["rank_ic"],
            "residual_positive_rate": residual["positive_rate"],
            "residual_periods": residual["periods"],
            "residual_ic_series": residual["series"],
            "residual_posterior": residual["posterior"],
            "baseline_rank_ic": baseline_metric["rank_ic"],
            "combined_rank_ic": combined_metric["rank_ic"],
            "marginal_rank_ic_gain": combined_metric["rank_ic"] - baseline_metric["rank_ic"],
            "marginal_ic_series": marginal_series,
            "marginal_posterior": marginal_posterior,
        }
    return result


def market_state_definition(panel):
    cache = _RESEARCH_EVIDENCE_CACHE.setdefault(id(panel), {})
    if "market_state" in cache:
        return cache["market_state"]
    daily = panel.groupby("trade_date").agg(
        trend=("mom20", "median"),
        volatility=("range_pct", "median"),
        dispersion=("mom20", "std"),
        crowding=("turnover_rate", "median"),
    ).sort_index()
    train_dates = set(split_panel(panel, "train")["trade_date"].astype(str).unique())
    train = daily[daily.index.astype(str).isin(train_dates)]
    if train.empty:
        train = daily.iloc[: max(1, len(daily) // 2)]

    def robust_parameters(series):
        median = safe_float(series.median(), 0.0)
        mad = safe_float((series - median).abs().median(), 0.0)
        return median, max(1e-9, 1.4826 * mad)

    trend_cut = safe_float(train["trend"].median(), 0.0)
    risk_components = []
    risk_parameters = {}
    for column in ["volatility", "dispersion", "crowding"]:
        center, scale = robust_parameters(train[column])
        risk_parameters[column] = {"center": center, "scale": scale}
        risk_components.append((pd.to_numeric(daily[column], errors="coerce") - center) / scale)
    risk_score = pd.concat(risk_components, axis=1).mean(axis=1)
    risk_cut = safe_float(risk_score.loc[train.index].median(), 0.0)
    trend_state = np.where(pd.to_numeric(daily["trend"], errors="coerce") >= trend_cut, "up", "down")
    risk_state = np.where(risk_score >= risk_cut, "high_risk", "low_risk")
    states = pd.Series(
        [f"{trend}_{risk}" for trend, risk in zip(trend_state, risk_state)],
        index=daily.index.astype(str),
        dtype="object",
    )
    result = {
        "date_to_state": states.to_dict(),
        "states": sorted(states.dropna().unique().tolist()),
        "audit": {
            "fit_split": "embargoed_train_only",
            "future_labels_used": False,
            "state_variables": ["signal_date_market_trend", "volatility", "dispersion", "crowding"],
            "trend_cut": trend_cut,
            "risk_cut": risk_cut,
            "risk_parameters": risk_parameters,
            "routing_role": "conditioning_and_robustness_evaluation_not_standalone_alpha",
        },
    }
    cache["market_state"] = result
    return result


def regime_robustness_evidence(panel, metrics_by_split):
    state_definition = market_state_definition(panel)
    date_to_state = state_definition["date_to_state"]
    output = {
        "method": "train_defined_market_state_empirical_bayes_robustness",
        "state_audit": state_definition["audit"],
    }
    for split_name in ["train", "valid", "test", "search", "full"]:
        metric = metrics_by_split.get(split_name, {})
        buckets = defaultdict(list)
        for row in metric.get("ic_series", []) if isinstance(metric, dict) else []:
            state = date_to_state.get(str(row.get("date")))
            value = safe_float(row.get("rank_ic"), np.nan)
            if state and np.isfinite(value):
                buckets[state].append(float(value))
        states = []
        for state in state_definition["states"]:
            posterior = normal_posterior_evidence(buckets.get(state, []))
            states.append({"state": state, **posterior})
        available = [row for row in states if row["observations"] > 0]
        positive_probabilities = [row["positive_probability"] for row in available]
        lower_bounds = [row["lower_90"] for row in available]
        state_log_odds = np.asarray([
            math.log(max(1e-4, min(1.0 - 1e-4, probability)) / (1.0 - max(1e-4, min(1.0 - 1e-4, probability))))
            for probability in positive_probabilities
        ], dtype=float)
        if len(state_log_odds):
            risk_sensitive_state_log_odds = float(
                -math.log(max(1e-12, float(np.mean(np.exp(np.clip(-state_log_odds, -30.0, 30.0))))))
            )
            risk_sensitive_state_probability = float(
                1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, risk_sensitive_state_log_odds))))
            )
        else:
            risk_sensitive_state_log_odds = 0.0
            risk_sensitive_state_probability = 0.5
        absolute_means = np.asarray([abs(row["posterior_mean"]) for row in available], dtype=float)
        concentration = (
            float(np.dot(absolute_means, absolute_means) / max(float(absolute_means.sum() ** 2), 1e-12))
            if len(absolute_means)
            else 1.0
        )
        output[split_name] = {
            "states": states,
            "state_count": len(available),
            "posterior_positive_breadth": float(np.mean(positive_probabilities)) if positive_probabilities else 0.5,
            "risk_sensitive_positive_probability": risk_sensitive_state_probability,
            "risk_sensitive_state_log_odds": risk_sensitive_state_log_odds,
            "worst_state_lower_90": float(min(lower_bounds)) if lower_bounds else -0.10,
            "state_signal_concentration": concentration,
        }
    return output


def posterior_search_evidence(metrics_by_split, complexity=0):
    train = metrics_by_split.get("train", {})
    valid = metrics_by_split.get("valid", {})
    incremental = metrics_by_split.get("incremental_evidence", {})
    regime = metrics_by_split.get("regime_evidence", {})
    train_posterior = normal_posterior_evidence([row.get("rank_ic") for row in train.get("ic_series", [])])
    valid_posterior = normal_posterior_evidence([row.get("rank_ic") for row in valid.get("ic_series", [])])
    residual_posterior = (incremental.get("valid") or {}).get("residual_posterior") or normal_posterior_evidence([])
    synergy_posterior = (incremental.get("valid") or {}).get("marginal_posterior") or normal_posterior_evidence([], prior_scale=0.02)
    valid_curve = (((valid.get("backtest") or {}).get("curve")) or [])
    return_posterior = normal_posterior_evidence(
        [point.get("long_short_return") for point in valid_curve],
        prior_scale=0.025,
    )
    regime_valid = regime.get("valid") or {}
    regime_probability = safe_float(regime_valid.get("risk_sensitive_positive_probability", regime_valid.get("posterior_positive_breadth")), 0.5)
    probabilities = {
        "train_signal": train_posterior["positive_probability"],
        "validation_signal": valid_posterior["positive_probability"],
        "incremental_residual": residual_posterior["positive_probability"],
        "downstream_synergy": synergy_posterior["positive_probability"],
        "economic_realization": return_posterior["positive_probability"],
        "regime_breadth": regime_probability,
    }
    clipped = {key: float(np.clip(value, 1e-4, 1.0 - 1e-4)) for key, value in probabilities.items()}
    log_odds_components = {key: math.log(value / (1.0 - value)) for key, value in clipped.items()}
    log_posterior_odds = float(np.mean(list(log_odds_components.values())))
    log_odds_values = np.asarray(list(log_odds_components.values()), dtype=float)
    risk_sensitive_log_odds = float(
        -math.log(max(1e-12, float(np.mean(np.exp(np.clip(-log_odds_values, -30.0, 30.0))))))
    )
    minimum_coverage = min(safe_float(train.get("coverage"), 0.0), safe_float(valid.get("coverage"), 0.0))
    coverage_log_prior = math.log(max(0.05, min(1.0, minimum_coverage)))
    complexity_log_prior = math.log1p(max(0.0, safe_float(complexity, 0.0) - 10.0)) / 3.0
    utility = risk_sensitive_log_odds + coverage_log_prior - complexity_log_prior
    return {
        "method": "entropic_soft_min_posterior_odds_with_coverage_and_complexity_priors",
        "component_probabilities": probabilities,
        "component_log_odds": log_odds_components,
        "joint_positive_probability": float(math.exp(np.mean([math.log(value) for value in clipped.values()]))),
        "log_posterior_odds": log_posterior_odds,
        "risk_sensitive_log_odds": risk_sensitive_log_odds,
        "weakest_component": min(log_odds_components, key=log_odds_components.get),
        "coverage_log_prior": coverage_log_prior,
        "complexity_log_prior": -complexity_log_prior,
        "utility": float(utility),
        "posterior_pass": bool(utility > 0.0),
        "failure_pressures": {key: 1.0 - value for key, value in probabilities.items()},
        "train_posterior": train_posterior,
        "valid_posterior": valid_posterior,
        "valid_return_posterior": return_posterior,
        "valid_residual_posterior": residual_posterior,
        "valid_synergy_posterior": synergy_posterior,
        "valid_regime": regime_valid,
        "test_metrics_used": False,
    }

def posterior_final_reporting_evidence(metrics_by_split):
    search_evidence = metrics_by_split.get("posterior_search_evidence") or {}
    test = metrics_by_split.get("test", {})
    incremental = metrics_by_split.get("incremental_evidence", {})
    test_incremental = incremental.get("test") or {}
    test_signal = normal_posterior_evidence([row.get("rank_ic") for row in test.get("ic_series", [])])
    residual = test_incremental.get("residual_posterior") or normal_posterior_evidence([])
    synergy = test_incremental.get("marginal_posterior") or normal_posterior_evidence([], prior_scale=0.02)
    test_curve = (((test.get("backtest") or {}).get("curve")) or [])
    realization = normal_posterior_evidence(
        [point.get("long_short_return") for point in test_curve],
        prior_scale=0.025,
    )
    probabilities = {
        "sealed_search_evidence": safe_float(search_evidence.get("joint_positive_probability"), 0.5),
        "test_signal": test_signal["positive_probability"],
        "test_incremental_residual": residual["positive_probability"],
        "test_downstream_synergy": synergy["positive_probability"],
        "test_economic_realization": realization["positive_probability"],
    }
    clipped = {key: float(np.clip(value, 1e-4, 1.0 - 1e-4)) for key, value in probabilities.items()}
    log_odds = {key: math.log(value / (1.0 - value)) for key, value in clipped.items()}
    log_odds_values = np.asarray(list(log_odds.values()), dtype=float)
    risk_sensitive_log_odds = float(
        -math.log(max(1e-12, float(np.mean(np.exp(np.clip(-log_odds_values, -30.0, 30.0))))))
    )
    return {
        "method": "sealed_test_entropic_soft_min_posterior_reporting_only",
        "component_probabilities": probabilities,
        "component_log_odds": log_odds,
        "joint_positive_probability": float(math.exp(np.mean([math.log(value) for value in clipped.values()]))),
        "mean_log_posterior_odds": float(np.mean(list(log_odds.values()))),
        "risk_sensitive_log_odds": risk_sensitive_log_odds,
        "weakest_component": min(log_odds, key=log_odds.get),
        "utility": risk_sensitive_log_odds,
        "test_signal_posterior": test_signal,
        "test_residual_posterior": residual,
        "test_synergy_posterior": synergy,
        "test_realization_posterior": realization,
        "used_for_parent_selection": False,
        "test_metrics_used": True,
    }

def group_return_profile(g, factor_name, q=5):
    if len(g) < q * 10:
        return None
    ranks = g[factor_name].rank(method="first")
    groups = pd.qcut(ranks, q, labels=False, duplicates="drop")
    tmp = g.assign(_group=groups)
    vals = tmp.groupby("_group")["label_next_ret"].mean().to_dict()
    if len(vals) < q:
        return None
    ordered = [float(vals.get(i, np.nan)) for i in range(q)]
    return ordered


def infer_periods_per_year(dates, default=12.0):
    parsed = pd.to_datetime(pd.Series(list(dates), dtype="object"), errors="coerce").dropna()
    if len(parsed) < 2:
        return float(default)
    span_years = max((parsed.max() - parsed.min()).days / 365.25, 1.0 / float(default))
    observed = len(parsed) / span_years
    return float(max(1.0, min(float(default), observed)))


def single_width_top_bottom_backtest(df, factor_name, cost_rate=0.001, top_frac=0.20):
    rows = []
    prev_top = None
    prev_short = None
    for date, g in df.dropna(subset=[factor_name, "label_next_ret"]).groupby("trade_date"):
        if len(g) < 50:
            continue
        n = max(int(len(g) * top_frac), 1)
        top = g.nlargest(n, factor_name)
        bot = g.nsmallest(n, factor_name)
        top_set = set(top["ts_code"])
        bot_set = set(bot["ts_code"])
        long_turnover = 0.0
        short_turnover = 0.0
        if prev_top is not None:
            long_turnover = 1.0 - len(top_set & prev_top) / max(len(prev_top), 1)
        if prev_short is not None:
            short_turnover = 1.0 - len(bot_set & prev_short) / max(len(prev_short), 1)
        turnover = 0.5 * (long_turnover + short_turnover) if prev_top is not None else 0.0
        top_ret = float(top["label_next_ret"].mean())
        bot_ret = float(bot["label_next_ret"].mean())
        bench = float(g["label_next_ret"].mean())
        rows.append({
            "trade_date": date,
            "long_return": top_ret - cost_rate * long_turnover,
            "long_short_return": top_ret - bot_ret - cost_rate * (long_turnover + short_turnover),
            "benchmark_return": bench,
            "turnover": turnover,
            "long_turnover": long_turnover,
            "short_turnover": short_turnover,
        })
        prev_top = top_set
        prev_short = bot_set
    if not rows:
        return {
            "long_only": {},
            "long_short": {},
            "avg_turnover": 0.0,
            "curve": [],
        }
    bt = pd.DataFrame(rows)
    periods_per_year = infer_periods_per_year(bt["trade_date"].tolist())
    return {
        "long_only": metrics_from_returns(
            bt["long_return"].tolist(),
            bt["benchmark_return"].tolist(),
            periods_per_year=periods_per_year,
        ),
        "long_short": metrics_from_returns(
            bt["long_short_return"].tolist(),
            periods_per_year=periods_per_year,
        ),
        "periods_per_year": periods_per_year,
        "avg_turnover": float(bt["turnover"].mean()),
        "curve": build_backtest_curve(bt),
    }



def _backtest_payload_from_rows(rows):
    if not rows:
        return {
            "long_only": {},
            "long_short": {},
            "avg_turnover": 0.0,
            "curve": [],
        }
    bt = pd.DataFrame(rows)
    periods_per_year = infer_periods_per_year(bt["trade_date"].tolist())
    return {
        "long_only": metrics_from_returns(
            bt["long_return"].tolist(),
            bt["benchmark_return"].tolist(),
            periods_per_year=periods_per_year,
        ),
        "long_short": metrics_from_returns(
            bt["long_short_return"].tolist(),
            periods_per_year=periods_per_year,
        ),
        "periods_per_year": periods_per_year,
        "avg_turnover": float(bt["turnover"].mean()),
        "curve": build_backtest_curve(bt),
    }


def top_bottom_backtest(df, factor_name, cost_rate=0.001, top_frac=0.20):
    if not isinstance(top_frac, dict):
        return single_width_top_bottom_backtest(
            df, factor_name, cost_rate=cost_rate, top_frac=float(top_frac)
        )
    components = []
    for item in top_frac.get("weights") or []:
        fraction = safe_float(item.get("fraction"), np.nan)
        weight = safe_float(item.get("weight"), 0.0)
        if np.isfinite(fraction) and 0 < fraction < 0.5 and weight > 0:
            components.append((float(fraction), float(weight)))
    total_weight = sum(weight for _, weight in components)
    if total_weight <= 0:
        return single_width_top_bottom_backtest(
            df, factor_name, cost_rate=cost_rate, top_frac=0.20
        )
    components = [(fraction, weight / total_weight) for fraction, weight in components]
    states = [
        {
            "fraction": fraction,
            "weight": weight,
            "prev_top": None,
            "prev_short": None,
            "rows": [],
        }
        for fraction, weight in components
    ]
    usable = df.dropna(subset=[factor_name, "label_next_ret"])
    for date, group in usable.groupby("trade_date"):
        if len(group) < 50:
            continue
        benchmark_return = float(group["label_next_ret"].mean())
        for state in states:
            count = max(int(len(group) * state["fraction"]), 1)
            top = group.nlargest(count, factor_name)
            bottom = group.nsmallest(count, factor_name)
            top_set = set(top["ts_code"])
            bottom_set = set(bottom["ts_code"])
            long_turnover = 0.0
            short_turnover = 0.0
            if state["prev_top"] is not None:
                long_turnover = 1.0 - len(top_set & state["prev_top"]) / max(
                    len(state["prev_top"]), 1
                )
            if state["prev_short"] is not None:
                short_turnover = 1.0 - len(bottom_set & state["prev_short"]) / max(
                    len(state["prev_short"]), 1
                )
            turnover = (
                0.5 * (long_turnover + short_turnover)
                if state["prev_top"] is not None
                else 0.0
            )
            top_return = float(top["label_next_ret"].mean())
            bottom_return = float(bottom["label_next_ret"].mean())
            state["rows"].append({
                "trade_date": date,
                "long_return": top_return - cost_rate * long_turnover,
                "long_short_return": (
                    top_return
                    - bottom_return
                    - cost_rate * (long_turnover + short_turnover)
                ),
                "benchmark_return": benchmark_return,
                "turnover": turnover,
                "long_turnover": long_turnover,
                "short_turnover": short_turnover,
            })
            state["prev_top"] = top_set
            state["prev_short"] = bottom_set

    component_results = []
    combined = None
    for state in states:
        result = _backtest_payload_from_rows(state["rows"])
        component_results.append({
            "fraction": state["fraction"],
            "weight": state["weight"],
            "long_only": result.get("long_only", {}),
            "long_short": result.get("long_short", {}),
            "avg_turnover": result.get("avg_turnover", 0.0),
        })
        curve = pd.DataFrame(result.get("curve") or [])
        if curve.empty:
            continue
        values = curve[[
            "date", "long_return", "long_short_return", "benchmark_return",
            "turnover", "long_turnover", "short_turnover",
        ]].copy()
        for column in [
            "long_return", "long_short_return", "turnover",
            "long_turnover", "short_turnover",
        ]:
            values[column] = (
                pd.to_numeric(values[column], errors="coerce").fillna(0.0)
                * state["weight"]
            )
        if combined is None:
            combined = values
        else:
            merged = combined.merge(values, on="date", how="inner", suffixes=("", "_new"))
            for column in [
                "long_return", "long_short_return", "turnover",
                "long_turnover", "short_turnover",
            ]:
                merged[column] = merged[column] + merged.pop(f"{column}_new")
            merged = merged.drop(columns=["benchmark_return_new"], errors="ignore")
            combined = merged
    if combined is None or combined.empty:
        return {
            "long_only": {},
            "long_short": {},
            "avg_turnover": 0.0,
            "curve": [],
            "portfolio_widths": component_results,
        }
    combined = combined.rename(columns={"date": "trade_date"}).sort_values("trade_date")
    periods_per_year = infer_periods_per_year(combined["trade_date"].tolist())
    return {
        "long_only": metrics_from_returns(
            combined["long_return"].tolist(),
            combined["benchmark_return"].tolist(),
            periods_per_year=periods_per_year,
        ),
        "long_short": metrics_from_returns(
            combined["long_short_return"].tolist(),
            periods_per_year=periods_per_year,
        ),
        "periods_per_year": periods_per_year,
        "avg_turnover": float(combined["turnover"].mean()),
        "curve": build_backtest_curve(combined),
        "portfolio_widths": component_results,
        "portfolio_width_policy": "entropy_regularized_embargoed_train_ensemble",
    }


def annual_summary_from_ic_rows(ic_rows, backtest_curve=None):
    buckets = defaultdict(list)
    for row in ic_rows:
        year = str(row.get("date", ""))[:4]
        if year:
            buckets[year].append(row)
    curve_buckets = defaultdict(list)
    for point in backtest_curve or []:
        year = str(point.get("date", ""))[:4]
        if year:
            curve_buckets[year].append(point)
    out = []
    for year in sorted(buckets):
        rows = buckets[year]
        ics = [safe_float(r.get("rank_ic"), np.nan) for r in rows]
        spreads = [safe_float(r.get("group_spread"), np.nan) for r in rows]
        coverages = [safe_float(r.get("coverage"), np.nan) for r in rows]
        curve_rows = curve_buckets.get(year, [])

        def finite_values(values):
            return [float(value) for value in values if np.isfinite(value)]

        finite_ics = finite_values(ics)
        finite_spreads = finite_values(spreads)
        finite_coverages = finite_values(coverages)

        def compounded_return(key):
            values = finite_values([safe_float(point.get(key), np.nan) for point in curve_rows])
            return float(np.prod(1.0 + np.asarray(values, dtype=float)) - 1.0) if values else None

        out.append({
            "year": year,
            "rank_ic": float(np.mean(finite_ics)) if finite_ics else 0.0,
            "positive_ic_rate": sum(1 for x in finite_ics if x > 0) / len(finite_ics) if finite_ics else 0.0,
            "group_spread": float(np.mean(finite_spreads)) if finite_spreads else 0.0,
            "coverage": float(np.mean(finite_coverages)) if finite_coverages else 0.0,
            "periods": len(rows),
            "long_return": compounded_return("long_return"),
            "benchmark_return": compounded_return("benchmark_return"),
            "long_short_return": compounded_return("long_short_return"),
        })
    return out

def evaluate_factor(panel, factor_name, top_frac=0.20):
    if isinstance(top_frac, dict):
        weights = top_frac.get("weights") or []
        representative_fraction = sum(
            safe_float(item.get("fraction"), 0.0) * safe_float(item.get("weight"), 0.0)
            for item in weights
        ) or 0.20
    else:
        representative_fraction = float(top_frac)
    df = panel[["trade_date", "ts_code", factor_name, "label_next_ret"]].dropna()
    if df.empty:
        return {
            "rank_ic": 0.0, "icir": 0.0, "group_spread": 0.0, "turnover": 0.0,
            "coverage": 0.0, "group_monotonicity": 0.0, "ic_win_rate": 0.0,
            "rank_ic_t": 0.0, "rank_ic_t_newey_west": 0.0, "group_returns": [],
            "ic_series": [], "annual_summary": [], "backtest": {},
        }
    panel_counts = panel.groupby("trade_date").size().to_dict()
    ics, spreads, turnovers, group_profiles, ic_rows = [], [], [], [], []
    prev_top = None
    coverage_rows = 0
    total_rows = 0
    for date, g in df.groupby("trade_date"):
        day_total = int(panel_counts.get(date, len(g)))
        total_rows += day_total
        coverage_rows += len(g)
        if len(g) < 50:
            continue
        ic = g[factor_name].rank().corr(g["label_next_ret"].rank())
        q = max(int(len(g) * representative_fraction), 1)
        top = g.nlargest(q, factor_name)
        bot = g.nsmallest(q, factor_name)
        spread = float(top["label_next_ret"].mean() - bot["label_next_ret"].mean())
        if pd.notna(ic):
            ics.append(float(ic))
        spreads.append(spread)
        top_set = set(top["ts_code"])
        turnover = 0.0
        if prev_top is not None:
            turnover = 1.0 - len(top_set & prev_top) / max(len(top_set | prev_top), 1)
            turnovers.append(turnover)
        prev_top = top_set
        profile = group_return_profile(g, factor_name, 5)
        if profile:
            group_profiles.append(profile)
        ic_rows.append({
            "date": str(date),
            "rank_ic": safe_float(ic, 0.0),
            "group_spread": spread,
            "top_return": float(top["label_next_ret"].mean()),
            "bottom_return": float(bot["label_next_ret"].mean()),
            "coverage": len(g) / max(day_total, 1),
            "turnover": safe_float(turnover, 0.0),
        })
    rank_ic = float(np.nanmean(ics)) if ics else 0.0
    ic_std = float(np.nanstd(ics)) if len(ics) > 1 else 0.0
    avg_profile = np.nanmean(np.array(group_profiles), axis=0).tolist() if group_profiles else []
    monotonic = 0.0
    if len(avg_profile) >= 5:
        monotonic = float(np.mean(np.diff(avg_profile) >= 0))
    bt = top_bottom_backtest(
        panel[["trade_date", "ts_code", factor_name, "label_next_ret"]].dropna(),
        factor_name,
        top_frac=top_frac,
    )
    return {
        "portfolio_fraction": float(representative_fraction),
        "portfolio_widths": top_frac.get("weights", []) if isinstance(top_frac, dict) else [{"fraction": float(top_frac), "weight": 1.0}],
        "rank_ic": rank_ic,
        "icir": rank_ic / ic_std if ic_std else 0.0,
        "rank_ic_t": rank_ic / (ic_std / math.sqrt(len(ics))) if ic_std and len(ics) > 1 else 0.0,
        "rank_ic_t_newey_west": newey_west_tstat(ics, lags=3),
        "ic_win_rate": sum(1 for x in ics if x > 0) / len(ics) if ics else 0.0,
        "group_spread": float(np.nanmean(spreads)) if spreads else 0.0,
        "turnover": float(np.nanmean(turnovers)) if turnovers else 0.0,
        "coverage": coverage_rows / total_rows if total_rows else 0.0,
        "group_monotonicity": monotonic,
        "group_returns": [float(x) for x in avg_profile],
        "ic_decay": {"next_rebalance": rank_ic},
        "ic_series": ic_rows,
        "annual_summary": annual_summary_from_ic_rows(ic_rows, bt.get("curve", [])),
        "backtest": bt,
    }


def annual_metrics_from_full_evaluation(panel, factor_name, full_metric):
    ic_rows = list(full_metric.get("ic_series") or [])
    curve = list((full_metric.get("backtest") or {}).get("curve") or [])
    rows_by_year = defaultdict(list)
    curve_by_year = defaultdict(list)
    for row in ic_rows:
        year = str(row.get("date", ""))[:4]
        if year:
            rows_by_year[year].append(row)
    for point in curve:
        year = str(point.get("date", ""))[:4]
        if year:
            curve_by_year[year].append(point)

    output = {}
    for year in [str(value) for value in range(2012, 2027)]:
        rows = rows_by_year.get(year, [])
        year_curve = curve_by_year.get(year, [])
        values = [safe_float(row.get("rank_ic"), np.nan) for row in rows]
        values = [value for value in values if np.isfinite(value)]
        spreads = [safe_float(row.get("group_spread"), np.nan) for row in rows]
        spreads = [value for value in spreads if np.isfinite(value)]
        turnovers = [safe_float(row.get("turnover"), np.nan) for row in rows]
        turnovers = [value for value in turnovers if np.isfinite(value)]
        year_panel = panel[panel["trade_date"].astype(str).str.startswith(year)]
        valid_rows = year_panel[[factor_name, "label_next_ret"]].dropna()
        coverage = len(valid_rows) / max(len(year_panel), 1)
        backtest = {}
        if year_curve:
            periods_per_year = infer_periods_per_year(
                [point.get("date") for point in year_curve]
            )
            backtest = {
                "long_only": metrics_from_returns(
                    [safe_float(point.get("long_return"), 0.0) for point in year_curve],
                    [safe_float(point.get("benchmark_return"), 0.0) for point in year_curve],
                    periods_per_year=periods_per_year,
                ),
                "long_short": metrics_from_returns(
                    [safe_float(point.get("long_short_return"), 0.0) for point in year_curve],
                    periods_per_year=periods_per_year,
                ),
                "periods_per_year": periods_per_year,
                "avg_turnover": float(np.mean(turnovers)) if turnovers else 0.0,
                "curve": year_curve,
            }
        rank_ic = float(np.mean(values)) if values else 0.0
        ic_std = float(np.std(values)) if len(values) > 1 else 0.0
        output[f"year:{year}"] = {
            "rank_ic": rank_ic,
            "icir": rank_ic / ic_std if ic_std else 0.0,
            "rank_ic_t": (
                rank_ic / (ic_std / math.sqrt(len(values)))
                if ic_std and len(values) > 1
                else 0.0
            ),
            "rank_ic_t_newey_west": newey_west_tstat(values, lags=3),
            "ic_win_rate": (
                sum(value > 0 for value in values) / len(values)
                if values
                else 0.0
            ),
            "group_spread": float(np.mean(spreads)) if spreads else 0.0,
            "turnover": float(np.mean(turnovers)) if turnovers else 0.0,
            "coverage": float(coverage),
            "ic_series": rows,
            "annual_summary": [
                row for row in full_metric.get("annual_summary", [])
                if str(row.get("year")) == year
            ],
            "backtest": backtest,
            "derived_from_full_window_monthly_evidence": True,
        }
    return output

def quick_screen_factor(panel, factor_name):
    df = panel[["trade_date", "ts_code", factor_name, "label_next_ret"]].dropna()
    if df.empty:
        return {"passed": False, "coverage": 0.0, "rough_rank_ic": 0.0, "extreme_ratio": 1.0, "stability": 0.0}
    coverage = len(df) / max(len(panel), 1)
    ics = []
    date_stats = []
    for _, g in df.groupby("trade_date"):
        if len(g) < 50:
            continue
        vals = pd.to_numeric(g[factor_name], errors="coerce")
        ic = vals.rank().corr(g["label_next_ret"].rank())
        if pd.notna(ic):
            ics.append(float(ic))
        date_stats.append((float(vals.mean()), float(vals.std(ddof=0) or 0.0)))
    values = pd.to_numeric(df[factor_name], errors="coerce")
    extreme_ratio = float((values.abs() > values.abs().quantile(0.995)).mean()) if values.notna().sum() else 1.0
    rough_ic = float(np.nanmean(ics)) if ics else 0.0
    stat_std = float(np.nanstd([x[0] for x in date_stats]) + np.nanstd([x[1] for x in date_stats])) if date_stats else 9.0
    stability = 1.0 / (1.0 + stat_std)
    return {
        "passed": bool(coverage >= 0.50 and extreme_ratio <= 0.02 and stability >= 0.25),
        "coverage": coverage,
        "rough_rank_ic": rough_ic,
        "ic_win_rate": sum(1 for x in ics if x > 0) / len(ics) if ics else 0.0,
        "extreme_ratio": extreme_ratio,
        "stability": stability,
        "periods": len(ics),
    }


def walk_forward_metrics(panel, factor_name, train_years=5, top_frac=0.20):
    out = []
    years = sorted(set(str(x)[:4] for x in panel["trade_date"].dropna().unique()))
    for y in years:
        year = int(y)
        train_start = f"{year - train_years}0101"
        train_end = f"{year - 1}1231"
        test_start = f"{year}0101"
        test_end = f"{year}1231"
        if train_start < SPLITS["train"][0] or test_end > SPLITS["full"][1]:
            continue
        train = panel[(panel["trade_date"] >= train_start) & (panel["trade_date"] <= train_end)]
        test = panel[(panel["trade_date"] >= test_start) & (panel["trade_date"] <= test_end)]
        if train.empty or test.empty:
            continue
        tr = evaluate_factor(train, factor_name, top_frac=top_frac)
        te = evaluate_factor(test, factor_name, top_frac=top_frac)
        out.append({
            "test_year": str(year),
            "train_rank_ic": tr.get("rank_ic", 0.0),
            "train_ic": tr.get("rank_ic", 0.0),
            "test_rank_ic": te.get("rank_ic", 0.0),
            "test_ic": te.get("rank_ic", 0.0),
            "test_group_spread": te.get("group_spread", 0.0),
            "test_coverage": te.get("coverage", 0.0),
            "decay": tr.get("rank_ic", 0.0) - te.get("rank_ic", 0.0),
        })
    if not out:
        return {"windows": [], "mean_test_rank_ic": 0.0, "mean_test_ic": 0.0, "positive_rate": 0.0, "positive_test_ic_ratio": 0.0, "mean_decay": 0.0}
    return {
        "windows": out,
        "mean_test_rank_ic": float(np.nanmean([x["test_rank_ic"] for x in out])),
        "mean_test_ic": float(np.nanmean([x["test_rank_ic"] for x in out])),
        "positive_rate": sum(1 for x in out if x["test_rank_ic"] > 0) / len(out),
        "positive_test_ic_ratio": sum(1 for x in out if x["test_rank_ic"] > 0) / len(out),
        "mean_decay": float(np.nanmean([x["decay"] for x in out])),
    }


def purged_kfold_metrics(panel, factor_name, n_splits=5, purge_periods=1, top_frac=0.20):
    dates = sorted(str(x) for x in panel["trade_date"].dropna().unique())
    if len(dates) < max(4, n_splits):
        return {"folds": [], "mean_test_rank_ic": 0.0, "mean_test_ic": 0.0, "positive_rate": 0.0, "positive_test_ic_ratio": 0.0, "mean_decay": 0.0, "purge_periods": purge_periods}
    n_splits = max(2, min(int(n_splits), len(dates)))
    fold_size = int(math.ceil(len(dates) / n_splits))
    folds = []
    for fold in range(n_splits):
        start_idx = fold * fold_size
        end_idx = min(len(dates), (fold + 1) * fold_size)
        if start_idx >= end_idx:
            continue
        test_dates = set(dates[start_idx:end_idx])
        purge_start = max(0, start_idx - purge_periods)
        purge_end = min(len(dates), end_idx + purge_periods)
        train_dates = set(dates[:purge_start] + dates[purge_end:])
        if len(train_dates) < 2 or len(test_dates) < 1:
            continue
        train = panel[panel["trade_date"].isin(train_dates)]
        test = panel[panel["trade_date"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        tr = evaluate_factor(train, factor_name, top_frac=top_frac)
        te = evaluate_factor(test, factor_name, top_frac=top_frac)
        folds.append({
            "fold": fold + 1,
            "train_periods": len(train_dates),
            "test_periods": len(test_dates),
            "purge_periods": purge_periods,
            "test_start": min(test_dates),
            "test_end": max(test_dates),
            "train_rank_ic": tr.get("rank_ic", 0.0),
            "train_ic": tr.get("rank_ic", 0.0),
            "test_rank_ic": te.get("rank_ic", 0.0),
            "test_ic": te.get("rank_ic", 0.0),
            "test_group_spread": te.get("group_spread", 0.0),
            "test_coverage": te.get("coverage", 0.0),
            "decay": tr.get("rank_ic", 0.0) - te.get("rank_ic", 0.0),
        })
    if not folds:
        return {"folds": [], "mean_test_rank_ic": 0.0, "mean_test_ic": 0.0, "positive_rate": 0.0, "positive_test_ic_ratio": 0.0, "mean_decay": 0.0, "purge_periods": purge_periods}
    return {
        "folds": folds,
        "mean_test_rank_ic": float(np.nanmean([x["test_rank_ic"] for x in folds])),
        "mean_test_ic": float(np.nanmean([x["test_rank_ic"] for x in folds])),
        "positive_rate": sum(1 for x in folds if x["test_rank_ic"] > 0) / len(folds),
        "positive_test_ic_ratio": sum(1 for x in folds if x["test_rank_ic"] > 0) / len(folds),
        "mean_decay": float(np.nanmean([x["decay"] for x in folds])),
        "purge_periods": purge_periods,
    }


def frozen_oos_walk_forward_metrics(panel, factor_name, top_frac=0.20):
    train = split_panel(panel, "train")
    oos = panel[panel["trade_date"].astype(str) > SPLITS["train"][1]]
    if train.empty or oos.empty:
        return {
            "windows": [],
            "mean_test_rank_ic": 0.0,
            "mean_test_ic": 0.0,
            "positive_rate": 0.0,
            "positive_test_ic_ratio": 0.0,
            "mean_decay": 0.0,
            "protocol": "frozen_train_model_strict_post_train_years",
            "label_embargo_periods": LABEL_HORIZON_PERIODS,
        }
    train_metrics = evaluate_factor(train, factor_name, top_frac=top_frac)
    out = []
    years = sorted(set(str(value)[:4] for value in oos["trade_date"].dropna().unique()))
    for year in years:
        test = oos[oos["trade_date"].astype(str).str.startswith(year)]
        if test.empty:
            continue
        test_metrics = evaluate_factor(test, factor_name, top_frac=top_frac)
        out.append({
            "test_year": year,
            "train_rank_ic": train_metrics.get("rank_ic", 0.0),
            "train_ic": train_metrics.get("rank_ic", 0.0),
            "test_rank_ic": test_metrics.get("rank_ic", 0.0),
            "test_ic": test_metrics.get("rank_ic", 0.0),
            "test_group_spread": test_metrics.get("group_spread", 0.0),
            "test_coverage": test_metrics.get("coverage", 0.0),
            "decay": train_metrics.get("rank_ic", 0.0) - test_metrics.get("rank_ic", 0.0),
        })
    if not out:
        return {
            "windows": [],
            "mean_test_rank_ic": 0.0,
            "mean_test_ic": 0.0,
            "positive_rate": 0.0,
            "positive_test_ic_ratio": 0.0,
            "mean_decay": 0.0,
            "protocol": "frozen_train_model_strict_post_train_years",
            "label_embargo_periods": LABEL_HORIZON_PERIODS,
        }
    return {
        "windows": out,
        "mean_test_rank_ic": float(np.nanmean([row["test_rank_ic"] for row in out])),
        "mean_test_ic": float(np.nanmean([row["test_rank_ic"] for row in out])),
        "positive_rate": sum(row["test_rank_ic"] > 0 for row in out) / len(out),
        "positive_test_ic_ratio": sum(row["test_rank_ic"] > 0 for row in out) / len(out),
        "mean_decay": float(np.nanmean([row["decay"] for row in out])),
        "protocol": "frozen_train_model_strict_post_train_years",
        "label_embargo_periods": LABEL_HORIZON_PERIODS,
        "fit_end": str(train["trade_date"].max()),
        "oos_start": str(oos["trade_date"].min()),
        "oos_end": str(oos["trade_date"].max()),
    }


def frozen_oos_block_metrics(panel, factor_name, n_splits=5, top_frac=0.20):
    train = split_panel(panel, "train")
    oos = panel[panel["trade_date"].astype(str) > SPLITS["train"][1]]
    dates = sorted(str(value) for value in oos["trade_date"].dropna().unique())
    empty = {
        "folds": [],
        "mean_test_rank_ic": 0.0,
        "mean_test_ic": 0.0,
        "positive_rate": 0.0,
        "positive_test_ic_ratio": 0.0,
        "mean_decay": 0.0,
        "purge_periods": LABEL_HORIZON_PERIODS,
        "protocol": "frozen_train_model_strict_post_train_blocks",
        "label_embargo_periods": LABEL_HORIZON_PERIODS,
    }
    if train.empty or len(dates) < 2:
        return empty
    train_metrics = evaluate_factor(train, factor_name, top_frac=top_frac)
    blocks = [list(block) for block in np.array_split(np.array(dates, dtype=object), min(max(2, int(n_splits)), len(dates))) if len(block)]
    folds = []
    for fold, block_dates in enumerate(blocks, 1):
        test = oos[oos["trade_date"].astype(str).isin(set(block_dates))]
        if test.empty:
            continue
        test_metrics = evaluate_factor(test, factor_name, top_frac=top_frac)
        folds.append({
            "fold": fold,
            "train_periods": int(train["trade_date"].nunique()),
            "test_periods": len(block_dates),
            "purge_periods": LABEL_HORIZON_PERIODS,
            "test_start": min(block_dates),
            "test_end": max(block_dates),
            "train_rank_ic": train_metrics.get("rank_ic", 0.0),
            "train_ic": train_metrics.get("rank_ic", 0.0),
            "test_rank_ic": test_metrics.get("rank_ic", 0.0),
            "test_ic": test_metrics.get("rank_ic", 0.0),
            "test_group_spread": test_metrics.get("group_spread", 0.0),
            "test_coverage": test_metrics.get("coverage", 0.0),
            "decay": train_metrics.get("rank_ic", 0.0) - test_metrics.get("rank_ic", 0.0),
        })
    if not folds:
        return empty
    return {
        "folds": folds,
        "mean_test_rank_ic": float(np.nanmean([row["test_rank_ic"] for row in folds])),
        "mean_test_ic": float(np.nanmean([row["test_rank_ic"] for row in folds])),
        "positive_rate": sum(row["test_rank_ic"] > 0 for row in folds) / len(folds),
        "positive_test_ic_ratio": sum(row["test_rank_ic"] > 0 for row in folds) / len(folds),
        "mean_decay": float(np.nanmean([row["decay"] for row in folds])),
        "purge_periods": LABEL_HORIZON_PERIODS,
        "protocol": "frozen_train_model_strict_post_train_blocks",
        "label_embargo_periods": LABEL_HORIZON_PERIODS,
        "fit_end": str(train["trade_date"].max()),
        "oos_start": min(dates),
        "oos_end": max(dates),
    }


def attribution_slices(panel, factor_name):
    df = panel[["trade_date", "ts_code", factor_name, "label_next_ret", "industry_name", "total_mv", "turnover_rate"]].dropna(subset=[factor_name, "label_next_ret"])
    if df.empty:
        return {"industry": [], "size": [], "liquidity": []}
    industries = []
    for ind, g in df.groupby("industry_name"):
        if len(g) < 300:
            continue
        ic = g[factor_name].rank().corr(g["label_next_ret"].rank())
        industries.append({"bucket": str(ind), "rank_ic": safe_float(ic, 0.0), "rows": int(len(g))})
    industries = sorted(industries, key=lambda x: abs(x["rank_ic"]), reverse=True)[:10]
    tmp = df.copy()
    tmp["size_bucket"] = tmp.groupby("trade_date")["total_mv"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop") if len(s) >= 50 else 0)
    tmp["liq_bucket"] = tmp.groupby("trade_date")["turnover_rate"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop") if len(s) >= 50 else 0)
    def bucket_stats(col):
        rows = []
        for b, g in tmp.groupby(col):
            if len(g) < 200:
                continue
            ic = g[factor_name].rank().corr(g["label_next_ret"].rank())
            rows.append({"bucket": int(b) + 1 if pd.notna(b) else 0, "rank_ic": safe_float(ic, 0.0), "rows": int(len(g))})
        return rows
    return {"industry": industries, "size": bucket_stats("size_bucket"), "liquidity": bucket_stats("liq_bucket")}


def pass_flag(metrics_by_split, gates=None):
    gates = gates or DEFAULT_GATES
    tr = metrics_by_split.get("train", {})
    va = metrics_by_split.get("valid", {})
    return int(
        tr.get("rank_ic", 0) >= gates["train_rank_ic_min"]
        and tr.get("group_spread", 0) >= gates["train_group_spread_min"]
        and tr.get("coverage", 0) >= gates["coverage_min"]
        and va.get("rank_ic", 0) >= gates["valid_rank_ic_min"]
        and va.get("group_spread", 0) >= gates["valid_group_spread_min"]
    )

def backtest_blocks(metrics_by_split):
    test = metrics_by_split.get("test", {}) if isinstance(metrics_by_split, dict) else {}
    bt = test.get("backtest", {}) if isinstance(test.get("backtest", {}), dict) else {}
    return bt.get("long_only", {}) or {}, bt.get("long_short", {}) or {}


def signal_quality_gate(metrics_by_split, gates=DEFAULT_GATES):
    train = metrics_by_split.get("train", {})
    valid = metrics_by_split.get("valid", {})
    test = metrics_by_split.get("test", {})
    quick = metrics_by_split.get("quick_screen", metrics_by_split.get("quick", {}))
    walk = metrics_by_split.get("walk_forward", {})
    purged = metrics_by_split.get("purged_kfold", {})
    train_coverage = safe_float(train.get("coverage", test.get("coverage", 0.0)))
    return bool(
        safe_float(train.get("rank_ic")) >= gates.get("train_rank_ic_min", 0.025)
        and safe_float(valid.get("rank_ic")) >= gates.get("valid_rank_ic_min", 0.02)
        and safe_float(test.get("rank_ic")) >= gates.get("test_rank_ic_min", 0.02)
        and safe_float(train.get("group_spread")) >= gates.get("train_group_spread_min", 0.003)
        and safe_float(valid.get("group_spread")) >= gates.get("valid_group_spread_min", 0.002)
        and train_coverage >= gates.get("coverage_min", 0.65)
        and safe_float(test.get("coverage")) >= gates.get("coverage_min", 0.65)
        and quick.get("passed", False)
        and safe_float(valid.get("group_monotonicity")) >= gates.get("min_group_monotonicity", 0.75)
        and safe_float(first_metric(walk, "positive_test_ic_ratio", "positive_rate")) >= 0.50
        and safe_float(first_metric(purged, "positive_test_ic_ratio", "positive_rate")) >= 0.50
        and safe_float(train.get("rank_ic")) * safe_float(valid.get("rank_ic")) >= 0
    )


def novelty_gate(max_abs_corr, gates=DEFAULT_GATES):
    return safe_float(max_abs_corr) <= gates.get("novelty_max_corr", gates.get("max_redundancy", 0.82))


def turnover_gate(metrics_by_split, gates=DEFAULT_GATES):
    _lo, ls = backtest_blocks(metrics_by_split)
    test = metrics_by_split.get("test", {})
    bt = test.get("backtest", {}) if isinstance(test.get("backtest", {}), dict) else {}
    avg_turnover = safe_float(bt.get("avg_turnover"))
    if avg_turnover <= 0:
        avg_turnover = safe_float(test.get("turnover"))
    return avg_turnover <= gates.get("max_turnover", 0.85)


def long_only_gate(metrics_by_split, gates=DEFAULT_GATES):
    lo, _ls = backtest_blocks(metrics_by_split)
    return bool(
        safe_float(lo.get("excess_annual_return")) >= gates.get("long_only_excess_return_min", 0.03)
        and safe_float(lo.get("information_ratio")) >= gates.get("long_only_information_ratio_min", 0.35)
        and safe_float(lo.get("max_drawdown")) >= gates.get("long_only_max_drawdown_min", -0.30)
    )


def market_neutral_gate(metrics_by_split, gates=DEFAULT_GATES):
    _lo, ls = backtest_blocks(metrics_by_split)
    test = metrics_by_split.get("test", {})
    return bool(
        safe_float(test.get("rank_ic")) >= gates.get("market_neutral_rank_ic_min", gates.get("test_rank_ic_min", 0.02))
        and safe_float(ls.get("annual_return")) >= gates.get("market_neutral_annual_return_min", 0.06)
        and safe_float(ls.get("sharpe")) >= gates.get("market_neutral_sharpe_min", 0.60)
        and safe_float(ls.get("max_drawdown")) >= gates.get("market_neutral_max_drawdown_min", -0.18)
        and safe_float(ls.get("win_rate")) >= gates.get("market_neutral_win_rate_min", 0.52)
    )


def acceptance_type(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    if not signal_quality_gate(metrics_by_split, gates):
        return "rejected"
    if not novelty_gate(max_abs_corr, gates):
        return "rejected"
    if not turnover_gate(metrics_by_split, gates):
        return "rejected"
    long_pass = long_only_gate(metrics_by_split, gates)
    neutral_pass = market_neutral_gate(metrics_by_split, gates)
    if long_pass and neutral_pass:
        return "both"
    if neutral_pass:
        return "market_neutral"
    if long_pass:
        return "long_only"
    return "rejected"


def acceptance_label_cn(kind):
    return {
        "both": "市场中性与多头增强均通过",
        "market_neutral": "市场中性通过",
        "long_only": "多头增强通过",
        "rejected": "未通过",
    }.get(kind, "未通过")

def deterministic_cross_section_sample(panel, sample_per_date=300):
    if panel.empty or sample_per_date <= 0:
        return panel.iloc[0:0]
    ordered = panel.sort_values(["trade_date", "ts_code"]).copy()
    position = ordered.groupby("trade_date").cumcount().astype(float)
    group_size = ordered.groupby("trade_date")["trade_date"].transform("size").astype(float)
    target_size = np.minimum(group_size, float(sample_per_date))
    bucket = np.floor(position * target_size / group_size.clip(lower=1.0)).astype(int)
    keys = pd.DataFrame({"trade_date": ordered["trade_date"].astype(str), "bucket": bucket}, index=ordered.index)
    return ordered.loc[~keys.duplicated(["trade_date", "bucket"])]


def declared_synergy_parent_map(evaluated):
    """Return explicitly declared ensemble parents without inferring hidden lineage."""
    out = {}
    for factor, candidate in (evaluated or {}).items():
        parents = []
        for event in candidate.get("lineage") or []:
            if not isinstance(event, dict):
                continue
            values = event.get("synergy_parent_factors") or []
            if isinstance(values, str):
                values = [values]
            for parent in values:
                parent = str(parent or "").strip()
                if parent and parent != factor and parent not in parents:
                    parents.append(parent)
        if parents:
            out[factor] = parents
    return out


def factor_redundancy(panel, factor_names, sample_per_date=300, exclusions_by_factor=None):
    cols = [f for f in factor_names if f in panel.columns]
    if len(cols) <= 1:
        return {f: 0.0 for f in cols}
    sample = deterministic_cross_section_sample(panel, sample_per_date=sample_per_date)
    corr = sample[cols].apply(pd.to_numeric, errors="coerce").corr(method="spearman").abs()
    out = {}
    for factor in cols:
        exclusions = set((exclusions_by_factor or {}).get(factor) or [])
        vals = corr[factor].drop(labels=[factor, *sorted(exclusions)], errors="ignore").dropna()
        out[factor] = float(vals.max()) if len(vals) else 0.0
    return out


def declared_parent_redundancy(panel, factor_names, parent_map, sample_per_date=300):
    """Disclose ensemble-to-parent correlation; this diagnostic never drives acceptance."""
    cols = [f for f in factor_names if f in panel.columns]
    out = {f: 0.0 for f in cols}
    if len(cols) <= 1 or not parent_map:
        return out
    corr = factor_corr_matrix(panel, cols, sample_per_date=sample_per_date)
    for factor in cols:
        parents = [p for p in parent_map.get(factor, []) if p in corr.columns and p != factor]
        if factor not in corr.index or not parents:
            continue
        vals = corr.loc[factor, parents].dropna()
        out[factor] = float(vals.max()) if len(vals) else 0.0
    return out

def factor_corr_matrix(panel, factor_names, sample_per_date=300):
    cols = [f for f in factor_names if f in panel.columns]
    if len(cols) <= 1:
        return pd.DataFrame(np.eye(len(cols)), index=cols, columns=cols)
    sample = deterministic_cross_section_sample(panel, sample_per_date=sample_per_date)
    return sample[cols].apply(pd.to_numeric, errors="coerce").corr(method="spearman").abs()


def frontier_redundancy(
    panel,
    ordered_factors,
    eligible_factors,
    sample_per_date=300,
    max_corr=0.65,
    exclusions_by_factor=None,
):
    ordered_factors = [f for f in ordered_factors if f in panel.columns]
    eligible_factors = set(eligible_factors or [])
    corr = factor_corr_matrix(panel, ordered_factors, sample_per_date=sample_per_date)
    kept = []
    out = {}
    for factor in ordered_factors:
        if kept and factor in corr.index:
            exclusions = set((exclusions_by_factor or {}).get(factor) or [])
            comparison_factors = [
                k for k in kept if k in corr.columns and k not in exclusions
            ]
            vals = corr.loc[factor, comparison_factors].dropna() if comparison_factors else pd.Series(dtype=float)
            out[factor] = float(vals.max()) if len(vals) else 0.0
        else:
            out[factor] = 0.0
        # Only independent, search-eligible candidates become frontier
        # representatives. A rejected duplicate must not contaminate every
        # candidate that follows it in the ordered search frontier.
        if factor in eligible_factors and out[factor] <= max_corr:
            kept.append(factor)
    return out


def deflated_sharpe_proxy(sharpe, periods, trials, returns=None, periods_per_year=12):
    sharpe = safe_float(sharpe, 0.0)
    periods = max(1.0, safe_float(periods, 1.0))
    trials = max(1.0, safe_float(trials, 1.0))
    values = np.asarray([safe_float(x, np.nan) for x in (returns or [])], dtype=float)
    values = values[np.isfinite(values)]

    if len(values) >= 4 and float(np.std(values, ddof=1)) > 0:
        period_sharpe = float(np.mean(values) / np.std(values, ddof=1))
        skew = float(pd.Series(values).skew())
        kurtosis = float(pd.Series(values).kurt()) + 3.0
        se_null = 1.0 / math.sqrt(max(1.0, len(values) - 1.0))
        if trials > 1.0:
            normal = NormalDist()
            euler_gamma = 0.5772156649015329
            q1 = normal.inv_cdf(max(1e-9, min(1.0 - 1e-9, 1.0 - 1.0 / trials)))
            q2 = normal.inv_cdf(max(1e-9, min(1.0 - 1e-9, 1.0 - 1.0 / (trials * math.e))))
            expected_max_period_sharpe = se_null * ((1.0 - euler_gamma) * q1 + euler_gamma * q2)
        else:
            expected_max_period_sharpe = 0.0
        variance = (
            1.0
            - skew * period_sharpe
            + ((kurtosis - 1.0) / 4.0) * period_sharpe * period_sharpe
        ) / max(1.0, len(values) - 1.0)
        se = math.sqrt(max(1e-12, variance))
        z = (period_sharpe - expected_max_period_sharpe) / se
        confidence = NormalDist().cdf(z)
        adjusted = (period_sharpe - expected_max_period_sharpe) * math.sqrt(periods_per_year)
        expected_max_noise = expected_max_period_sharpe * math.sqrt(periods_per_year)
        return {
            "raw_sharpe": sharpe,
            "periods": int(len(values)),
            "trials": float(trials),
            "return_skew": skew,
            "return_kurtosis": kurtosis,
            "expected_max_noise": float(expected_max_noise),
            "deflated_sharpe_proxy": float(adjusted),
            "deflated_sharpe_confidence": float(max(0.0, min(1.0, confidence))),
            "method": "probabilistic_sharpe_with_nonnormality_and_effective_trials",
        }

    se = math.sqrt(max(1e-9, (1.0 + 0.5 * sharpe * sharpe) / periods))
    expected_max_noise = math.sqrt(2.0 * math.log(trials)) * se if trials > 1 else 0.0
    adjusted = sharpe - expected_max_noise
    z = adjusted / se if se else 0.0
    confidence = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return {
        "raw_sharpe": sharpe,
        "periods": int(periods),
        "trials": float(trials),
        "expected_max_noise": float(expected_max_noise),
        "deflated_sharpe_proxy": float(adjusted),
        "deflated_sharpe_confidence": float(max(0.0, min(1.0, confidence))),
        "method": "small_sample_fallback",
    }


def effective_trial_count_from_curves(leaderboard):
    series = {}
    for row in leaderboard:
        curve = row.get("backtest_curve") or []
        vals = {}
        for point in curve:
            date = str(point.get("date", ""))
            if date:
                vals[date] = safe_float(point.get("long_short_return"), 0.0)
        if vals:
            series[row.get("factor")] = pd.Series(vals, dtype=float)
    if len(series) <= 1:
        return float(max(1, len(leaderboard)))
    df = pd.DataFrame(series).sort_index().fillna(0.0)
    if df.shape[1] <= 1:
        return float(max(1, len(leaderboard)))
    corr = df.corr().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    np.fill_diagonal(corr.values, 1.0)
    try:
        eig = np.linalg.eigvalsh(corr.to_numpy(dtype=float))
    except np.linalg.LinAlgError:
        return float(max(1, len(leaderboard)))
    eig = np.clip(eig, 0.0, None)
    denom = float(np.dot(eig, eig))
    if denom <= 0:
        return 1.0
    eff = float((eig.sum() ** 2) / denom)
    return float(max(1.0, min(float(len(leaderboard)), eff)))


def percentile_rank(value, values):
    vals = [safe_float(x, np.nan) for x in values]
    vals = [x for x in vals if np.isfinite(x)]
    value = safe_float(value, np.nan)
    if not vals or not np.isfinite(value):
        return 0.5
    if len(vals) == 1:
        return 0.5
    below = sum(1 for x in vals if x < value)
    equal = sum(1 for x in vals if x == value)
    return float((below + 0.5 * equal) / len(vals))


def beta_binomial_overfit_posterior(overfit_count, selected_count):
    """Weak-prior posterior for the probability that CSCV OOS rank falls below median."""
    selected = max(0, int(selected_count or 0))
    overfit = min(selected, max(0, int(overfit_count or 0)))
    alpha = overfit + 1
    beta = selected - overfit + 1
    trials = alpha + beta - 1
    probability_above_half = sum(
        math.comb(trials, j) for j in range(alpha)
    ) * (0.5 ** trials)
    return {
        "available": selected > 0,
        "selected_count": selected,
        "overfit_count": overfit,
        "posterior_mean": alpha / (alpha + beta),
        "probability_overfit_above_half": float(probability_above_half),
        "prior": "Beta(1,1)",
    }


def annotate_cscv_pbo(leaderboard, curve_field, prefix="", n_blocks=6):
    series = {}
    for row in leaderboard:
        values = {}
        for point in row.get(curve_field) or []:
            date = str(point.get("date", ""))
            if date:
                values[date] = safe_float(point.get("long_short_return"), np.nan)
        if values:
            series[row.get("factor")] = pd.Series(values, dtype=float)

    result = {
        "available": False,
        "curve_field": curve_field,
        "periods": 0,
        "candidates": len(series),
        "blocks": 0,
        "splits": 0,
        "pbo": 0.5,
        "mean_oos_rank_percentile": 0.5,
    }
    if len(series) < 2:
        for row in leaderboard:
            row[f"{prefix}cscv_available"] = False
            row[f"{prefix}cscv_pbo"] = 0.5
        return result

    matrix = pd.DataFrame(series).sort_index().dropna(axis=0, how="any")
    result["periods"] = int(len(matrix))
    if len(matrix) < 12:
        for row in leaderboard:
            row[f"{prefix}cscv_available"] = False
            row[f"{prefix}cscv_pbo"] = 0.5
        return result

    blocks = min(int(n_blocks), max(4, len(matrix) // 2))
    if blocks % 2:
        blocks -= 1
    if blocks < 4:
        return result
    block_indices = [np.asarray(x, dtype=int) for x in np.array_split(np.arange(len(matrix)), blocks) if len(x)]
    if len(block_indices) % 2:
        block_indices = block_indices[:-1]
    half = len(block_indices) // 2
    columns = list(matrix.columns)
    picked = defaultdict(lambda: {"selected": 0, "overfit": 0, "oos": []})
    oos_percentiles = []

    def strategy_scores(frame):
        mu = frame.mean(axis=0)
        sd = frame.std(axis=0, ddof=1).replace(0.0, np.nan)
        return (mu / sd * math.sqrt(12.0)).replace([np.inf, -np.inf], np.nan).fillna(-999.0)

    split_count = 0
    for train_blocks in combinations(range(len(block_indices)), half):
        train_set = set(train_blocks)
        test_blocks = [i for i in range(len(block_indices)) if i not in train_set]
        train_idx = np.concatenate([block_indices[i] for i in train_blocks])
        test_idx = np.concatenate([block_indices[i] for i in test_blocks])
        train_scores = strategy_scores(matrix.iloc[train_idx])
        test_scores = strategy_scores(matrix.iloc[test_idx])
        if train_scores.empty or test_scores.empty:
            continue
        selected_factor = str(train_scores.idxmax())
        selected_oos = safe_float(test_scores.get(selected_factor), -999.0)
        oos_pct = percentile_rank(selected_oos, test_scores.tolist())
        bucket = picked[selected_factor]
        bucket["selected"] += 1
        bucket["oos"].append(oos_pct)
        if oos_pct < 0.50:
            bucket["overfit"] += 1
        oos_percentiles.append(oos_pct)
        split_count += 1

    if not split_count:
        return result
    global_pbo = float(np.mean([x < 0.50 for x in oos_percentiles]))
    global_overfit_count = int(sum(x < 0.50 for x in oos_percentiles))
    global_posterior = beta_binomial_overfit_posterior(global_overfit_count, split_count)
    result.update({
        "available": True,
        "blocks": len(block_indices),
        "splits": split_count,
        "pbo": global_pbo,
        "mean_oos_rank_percentile": float(np.mean(oos_percentiles)),
        "overfit_count": global_overfit_count,
        "pbo_posterior_mean": global_posterior["posterior_mean"],
        "probability_overfit_above_half": global_posterior["probability_overfit_above_half"],
        "posterior_prior": global_posterior["prior"],
    })
    for row in leaderboard:
        bucket = picked.get(row.get("factor"), {})
        selected = int(bucket.get("selected", 0))
        overfit_count = int(bucket.get("overfit", 0))
        candidate_pbo = float(overfit_count / selected) if selected else None
        candidate_oos = float(np.mean(bucket.get("oos"))) if selected else None
        candidate_posterior = beta_binomial_overfit_posterior(overfit_count, selected)
        effective_pbo = candidate_pbo if candidate_pbo is not None else global_pbo
        effective_oos = candidate_oos if candidate_oos is not None else result["mean_oos_rank_percentile"]
        row[f"{prefix}cscv_available"] = True
        row[f"{prefix}cscv_pbo"] = global_pbo
        row[f"{prefix}cscv_selected_count"] = selected
        row[f"{prefix}cscv_overfit_count"] = overfit_count
        row[f"{prefix}cscv_pbo_when_selected"] = candidate_pbo
        row[f"{prefix}cscv_oos_rank_percentile_when_selected"] = candidate_oos
        row[f"{prefix}cscv_pbo_posterior_mean"] = candidate_posterior["posterior_mean"]
        row[f"{prefix}cscv_probability_overfit_above_half"] = candidate_posterior["probability_overfit_above_half"]
        row[f"{prefix}cscv_posterior_prior"] = candidate_posterior["prior"]
        row[f"{prefix}cscv_candidate_evidence_available"] = selected > 0
        if prefix == "search_":
            row["search_risk_adjusted_selection_score"] = (
                safe_float(row.get("search_risk_adjusted_selection_score"), 0.0)
                - 0.10 * effective_pbo
                - 0.05 * max(0.0, 0.5 - effective_oos)
            )
    return result


def annotate_pbo_proxy(leaderboard, metric_field="purged_kfold", prefix=""):
    def field(name):
        return f"{prefix}{name}"

    fold_ids = sorted({
        fold.get("fold")
        for row in leaderboard
        for fold in ((row.get(metric_field) or {}).get("folds") or [])
        if fold.get("fold") is not None
    })
    if not fold_ids:
        for row in leaderboard:
            row[field("pbo_proxy")] = 0.5
            row[field("purged_is_rank_percentile_mean")] = 0.5
            row[field("purged_oos_rank_percentile_mean")] = 0.5
            row[field("purged_train_test_decay_mean")] = 0.0
            row[field("risk_adjusted_selection_score")] = row.get("selection_score", 0.0)
        return leaderboard

    by_factor = {}
    for row in leaderboard:
        folds = (row.get(metric_field) or {}).get("folds") or []
        by_factor[row.get("factor")] = {fold.get("fold"): fold for fold in folds}

    accum = {row.get("factor"): {"is": [], "oos": [], "decay": [], "selected": 0, "overfit": 0} for row in leaderboard}
    for fold_id in fold_ids:
        train_vals = []
        test_vals = []
        rows = []
        for row in leaderboard:
            fold = by_factor.get(row.get("factor"), {}).get(fold_id)
            if not isinstance(fold, dict):
                continue
            train_ic = safe_float(fold.get("train_rank_ic", fold.get("train_ic")), np.nan)
            test_ic = safe_float(fold.get("test_rank_ic", fold.get("test_ic")), np.nan)
            if not np.isfinite(train_ic) or not np.isfinite(test_ic):
                continue
            train_vals.append(train_ic)
            test_vals.append(test_ic)
            rows.append((row.get("factor"), train_ic, test_ic))
        for factor, train_ic, test_ic in rows:
            train_pct = percentile_rank(train_ic, train_vals)
            test_pct = percentile_rank(test_ic, test_vals)
            bucket = accum[factor]
            bucket["is"].append(train_pct)
            bucket["oos"].append(test_pct)
            bucket["decay"].append(train_ic - test_ic)
            if train_pct >= 0.50:
                bucket["selected"] += 1
                if test_pct < 0.50:
                    bucket["overfit"] += 1

    for row in leaderboard:
        bucket = accum.get(row.get("factor"), {})
        is_mean = float(np.nanmean(bucket.get("is") or [0.5]))
        oos_mean = float(np.nanmean(bucket.get("oos") or [0.5]))
        decay_mean = float(np.nanmean(bucket.get("decay") or [0.0]))
        selected = int(bucket.get("selected") or 0)
        if selected:
            pbo = float(bucket.get("overfit", 0) / selected)
        else:
            pbo = float(max(0.0, 0.5 - oos_mean) * 2.0)
        risk_adjusted = safe_float(row.get("selection_score"), 0.0) - 0.20 * pbo - 0.12 * max(0.0, decay_mean) + 0.08 * (oos_mean - 0.5)
        row[field("pbo_proxy")] = pbo
        row[field("purged_is_rank_percentile_mean")] = is_mean
        row[field("purged_oos_rank_percentile_mean")] = oos_mean
        row[field("purged_train_test_decay_mean")] = decay_mean
        row[field("risk_adjusted_selection_score")] = float(risk_adjusted)
    return leaderboard


def annotate_multiple_testing_risk(leaderboard):
    def lineage_internal_trials(row):
        trials = 1
        for event in row.get("lineage") or []:
            if not isinstance(event, dict):
                continue
            audit = event.get("feature_selection_audit") or {}
            attempted = max(1, int(safe_float(audit.get("internal_hypotheses_evaluated"), 1)))
            trials += attempted - 1
        return max(1, trials)

    curve_raw_trials = max(1, len(leaderboard))
    curve_effective_trials = effective_trial_count_from_curves(leaderboard)
    internal_by_factor = {
        row.get("factor"): lineage_internal_trials(row)
        for row in leaderboard
    }
    internal_extra_trials = sum(max(0, trials - 1) for trials in internal_by_factor.values())
    raw_trials = float(curve_raw_trials + internal_extra_trials)
    effective_trials = float(curve_effective_trials + internal_extra_trials)

    for row in leaderboard:
        metrics = row.get("metrics", {}) if isinstance(row.get("metrics"), dict) else {}
        test = metrics.get("test", {}) if isinstance(metrics.get("test"), dict) else {}
        bt = test.get("backtest", {}) if isinstance(test.get("backtest"), dict) else {}
        long_only = bt.get("long_only", {}) if isinstance(bt.get("long_only"), dict) else {}
        long_short = bt.get("long_short", {}) if isinstance(bt.get("long_short"), dict) else {}
        curve = row.get("backtest_curve") or []
        long_returns = [point.get("long_return") for point in curve]
        long_short_returns = [point.get("long_short_return") for point in curve]
        lo_adj = deflated_sharpe_proxy(
            long_only.get("sharpe"),
            long_only.get("periods"),
            effective_trials,
            returns=long_returns,
            periods_per_year=long_only.get("periods_per_year", 12),
        )
        ls_adj = deflated_sharpe_proxy(
            long_short.get("sharpe"),
            long_short.get("periods"),
            effective_trials,
            returns=long_short_returns,
            periods_per_year=long_short.get("periods_per_year", 12),
        )
        long_only.update({k: v for k, v in lo_adj.items() if k not in {"raw_sharpe", "periods", "trials"}})
        long_short.update({k: v for k, v in ls_adj.items() if k not in {"raw_sharpe", "periods", "trials"}})
        row["test_long_deflated_sharpe"] = lo_adj["deflated_sharpe_proxy"]
        row["test_long_deflated_sharpe_confidence"] = lo_adj["deflated_sharpe_confidence"]
        row["test_long_short_deflated_sharpe"] = ls_adj["deflated_sharpe_proxy"]
        row["test_long_short_deflated_sharpe_confidence"] = ls_adj["deflated_sharpe_confidence"]
        row["multiple_testing_trials"] = raw_trials
        row["effective_multiple_testing_trials"] = effective_trials
        row["curve_effective_multiple_testing_trials"] = curve_effective_trials
        row["candidate_internal_search_trials"] = internal_by_factor.get(row.get("factor"), 1)
        row["pool_internal_extra_trials"] = internal_extra_trials
        row["internal_trial_accounting"] = "conservative_additive_upper_bound_without_stored_variant_curves"
    return leaderboard
def annotate_lifecycle_diagnostics(leaderboard):
    for row in leaderboard:
        curve = row.get("backtest_curve") or []
        ic_series = row.get("ic_series") or []
        full_returns = np.asarray([
            safe_float(point.get("long_short_return"), np.nan) for point in curve
        ], dtype=float)
        full_returns = full_returns[np.isfinite(full_returns)]
        full_ic = np.asarray([
            safe_float(point.get("rank_ic"), np.nan) for point in ic_series
        ], dtype=float)
        full_ic = full_ic[np.isfinite(full_ic)]

        def window_summary(horizon):
            recent_curve = curve[-horizon:]
            recent_ic_rows = ic_series[-horizon:]
            returns = [safe_float(point.get("long_short_return"), 0.0) for point in recent_curve]
            ic_values = [safe_float(point.get("rank_ic"), 0.0) for point in recent_ic_rows]
            dates = [point.get("date") for point in recent_curve]
            ppy = infer_periods_per_year(dates) if dates else 12.0
            metrics = metrics_from_returns(returns, periods_per_year=ppy)
            return {
                "periods": len(returns),
                "periods_per_year": ppy,
                "start": str(dates[0]) if dates else None,
                "end": str(dates[-1]) if dates else None,
                "total_return": metrics.get("total_return", 0.0),
                "annual_return": metrics.get("annual_return", 0.0),
                "sharpe": metrics.get("sharpe", 0.0),
                "win_rate": metrics.get("win_rate", 0.0),
                "rank_ic": float(np.mean(ic_values)) if ic_values else 0.0,
                "positive_ic_rate": float(np.mean([x > 0 for x in ic_values])) if ic_values else 0.0,
            }

        def empirical_bayes_recent_state(values, recent_horizon=6, baseline_horizon=24):
            values = np.asarray(values, dtype=float)
            values = values[np.isfinite(values)]
            recent = values[-recent_horizon:]
            baseline = values[:-recent_horizon][-baseline_horizon:]
            if len(recent) < 3 or len(baseline) < 6:
                return {
                    "available": False,
                    "recent_periods": len(recent),
                    "baseline_periods": len(baseline),
                }

            def robust_variance(sample):
                if len(sample) < 2:
                    return 0.0
                low, high = np.quantile(sample, [0.10, 0.90])
                return float(np.var(np.clip(sample, low, high), ddof=1))

            baseline_mean = float(np.mean(baseline))
            recent_mean = float(np.mean(recent))
            pooled_variance = max(robust_variance(np.concatenate([baseline, recent])), 1e-10)
            prior_strength = min(2.0, math.sqrt(len(baseline)) / 2.0)
            posterior_mean = float(
                (recent.sum() + prior_strength * baseline_mean) / (len(recent) + prior_strength)
            )
            posterior_se = math.sqrt(pooled_variance / (len(recent) + prior_strength))
            positive_probability = NormalDist().cdf(posterior_mean / posterior_se)
            shift_se = math.sqrt(max(
                robust_variance(baseline) / len(baseline)
                + robust_variance(recent) / len(recent),
                1e-10,
            ))
            deterioration_probability = NormalDist().cdf((baseline_mean - recent_mean) / shift_se)
            return {
                "available": True,
                "recent_periods": len(recent),
                "baseline_periods": len(baseline),
                "baseline_mean": baseline_mean,
                "recent_mean": recent_mean,
                "posterior_mean": posterior_mean,
                "posterior_standard_error": posterior_se,
                "positive_probability": float(positive_probability),
                "deterioration_probability": float(deterioration_probability),
                "standardized_mean_shift": float((recent_mean - baseline_mean) / shift_se),
                "prior_strength": float(prior_strength),
                "variance_estimator": "10_90_winsorized_empirical_variance",
            }

        recent6 = window_summary(6)
        recent12 = window_summary(12)
        test_ic = safe_float(row.get("test_rank_ic"), 0.0)
        ic_survival = recent12["rank_ic"] / test_ic if abs(test_ic) > 1e-12 else 0.0
        recent6_ic_survival = recent6["rank_ic"] / test_ic if abs(test_ic) > 1e-12 else 0.0
        return_state = empirical_bayes_recent_state(full_returns)
        ic_state = empirical_bayes_recent_state(full_ic)
        if return_state.get("available") and ic_state.get("available"):
            joint_deterioration = math.sqrt(
                return_state["deterioration_probability"] * ic_state["deterioration_probability"]
            )
            deployment_confidence = (
                return_state["positive_probability"] ** 0.35
                * ic_state["positive_probability"] ** 0.45
                * max(1e-6, 1.0 - joint_deterioration) ** 0.20
            )
        else:
            joint_deterioration = 0.5
            deployment_confidence = 0.0

        if len(full_returns) >= 6:
            recent = full_returns[-min(12, len(full_returns)):]
            prior_strength = min(12.0, max(3.0, len(full_returns) / 4.0))
            prior_mean = float(np.mean(full_returns))
            posterior_mean = float((recent.sum() + prior_strength * prior_mean) / (len(recent) + prior_strength))
            scale = float(np.std(full_returns, ddof=1)) if len(full_returns) > 1 else 0.0
            posterior_se = scale / math.sqrt(max(1.0, len(recent) + prior_strength))
            posterior_positive = NormalDist().cdf(posterior_mean / posterior_se) if posterior_se > 0 else float(posterior_mean > 0)
        else:
            posterior_mean = 0.0
            posterior_positive = 0.5

        if recent12["periods"] < 6 or not return_state.get("available") or not ic_state.get("available"):
            state = "insufficient_recent_sample"
        elif joint_deterioration >= 0.90:
            if return_state["positive_probability"] < 0.25 and ic_state["positive_probability"] < 0.50:
                state = "structural_decay"
            elif return_state["positive_probability"] < 0.50:
                state = "tail_realization_watch"
            else:
                state = "regime_transition_watch"
        elif return_state["positive_probability"] < 0.50 and ic_state["positive_probability"] >= 0.50:
            state = "tail_realization_watch"
        elif ic_state["positive_probability"] < 0.50:
            state = "signal_decay_watch"
        elif recent12["total_return"] < 0 and recent12["rank_ic"] > 0:
            state = "tail_realization_watch"
        elif recent12["rank_ic"] <= 0:
            state = "signal_decay_watch"
        else:
            state = "healthy"

        test_backtest = (((row.get("metrics") or {}).get("test") or {}).get("backtest") or {})
        test_long_short = test_backtest.get("long_short") or {}
        test_periods = int(safe_float(test_long_short.get("periods"), len(curve)))
        test_periods_per_year = safe_float(
            test_long_short.get("periods_per_year"),
            infer_periods_per_year([point.get("date") for point in curve]),
        )
        production_window_complete = test_periods >= 24 and test_periods_per_year >= 10.0
        observation_mode = "contiguous_monthly" if production_window_complete else "sparse_research_probe"
        posterior_research_ready = bool(row.get("posterior_search_pass", False))
        production_ready = state == "healthy" and production_window_complete and posterior_research_ready

        recent6["observation_basis"] = "last_6_monthly_periods" if production_window_complete else "last_6_sampled_observations"
        recent12["observation_basis"] = "last_12_monthly_periods" if production_window_complete else "last_12_sampled_observations"
        row["lifecycle_state"] = state
        row["lifecycle_observation_mode"] = observation_mode
        row["lifecycle_production_window_complete"] = production_window_complete
        row["lifecycle_posterior_research_ready"] = posterior_research_ready
        row["lifecycle_production_ready"] = production_ready
        row["lifecycle_production_ready_reason"] = (
            "healthy_contiguous_monthly_test_window"
            if production_ready
            else "non_monthly_or_short_probe_window"
            if not production_window_complete
            else "train_validation_posterior_evidence_not_ready"
            if not posterior_research_ready
            else f"lifecycle_state_{state}"
        )
        row["lifecycle_recent_6m"] = recent6
        row["lifecycle_recent_12m"] = recent12
        row["lifecycle_ic_survival_ratio"] = float(ic_survival)
        row["lifecycle_recent_6m_ic_survival_ratio"] = float(recent6_ic_survival)
        row["lifecycle_posterior_mean_return"] = posterior_mean
        row["lifecycle_posterior_positive_probability"] = float(posterior_positive)
        row["lifecycle_return_state_diagnostics"] = return_state
        row["lifecycle_ic_state_diagnostics"] = ic_state
        row["lifecycle_joint_deterioration_probability"] = float(joint_deterioration)
        row["lifecycle_deployment_confidence"] = float(deployment_confidence)
        row["lifecycle_recommended_weight_multiplier"] = float(np.clip(deployment_confidence, 0.0, 1.0))
        row["lifecycle_state_method"] = "empirical_bayes_recent6_vs_prior24_joint_return_ic_change_detection"
    return leaderboard

def purged_rank_migration_evidence(row, prefix="", gates=DEFAULT_GATES):
    """Treat relative IC rank as material only when absolute complement evidence also fails."""
    rank_key = f"{prefix}purged_oos_rank_percentile_mean"
    positive_key = f"{prefix}purged_positive_ratio"
    relative_rank = safe_float(row.get(rank_key), 0.5)
    positive_ratio = safe_float(row.get(positive_key), 0.0)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    posterior = metrics.get("posterior_search_evidence") or {}
    probabilities = posterior.get("component_probabilities") or {}
    residual_probability = safe_float(probabilities.get("incremental_residual"), 0.5)
    synergy_probability = safe_float(probabilities.get("downstream_synergy"), 0.5)
    residual_ic = safe_float(row.get("valid_incremental_residual_rank_ic"), 0.0)
    marginal_gain = safe_float(row.get("valid_downstream_marginal_rank_ic_gain"), 0.0)
    absolute_survival = bool(
        positive_ratio >= gates.get("purged_absolute_positive_ratio_min", 0.80)
        and bool(row.get("posterior_search_pass", False))
        and residual_probability > 0.50
        and synergy_probability > 0.50
        and residual_ic > 0.0
        and marginal_gain > 0.0
    )
    relative_rank_pass = (
        relative_rank >= gates.get("purged_oos_rank_percentile_min", 0.50)
    )
    material_failure = not relative_rank_pass and not absolute_survival
    return {
        "scope": "search_train_validation" if prefix == "search_" else "final_frozen_oos",
        "relative_oos_rank_percentile": relative_rank,
        "relative_rank_pass": relative_rank_pass,
        "absolute_positive_fold_ratio": positive_ratio,
        "posterior_search_pass": bool(row.get("posterior_search_pass", False)),
        "incremental_residual_probability": residual_probability,
        "downstream_synergy_probability": synergy_probability,
        "valid_incremental_residual_rank_ic": residual_ic,
        "valid_downstream_marginal_rank_ic_gain": marginal_gain,
        "absolute_complement_survival_pass": absolute_survival,
        "material_failure": material_failure,
        "decision": (
            "relative_rank_passed"
            if relative_rank_pass
            else "penalty_only_absolute_complement_survived"
            if absolute_survival
            else "material_relative_and_absolute_migration_failure"
        ),
    }


def reliability_failure_type(row, gates=DEFAULT_GATES):
    if not row.get("accepted"):
        return ""
    if not bool(row.get("search_reliability_pass", False)):
        return (
            row.get("search_reliability_failure_type")
            or row.get("search_diagnosis_code")
            or "search_stage_not_qualified"
        )
    if row.get("lifecycle_state") == "structural_decay":
        return "recent_alpha_structural_decay"
    if safe_float(row.get("pbo_proxy"), 0.0) > gates.get("max_pbo_proxy", 0.50):
        return "pbo_overfit_risk"
    migration_evidence = (
        row.get("purged_rank_migration_evidence")
        or purged_rank_migration_evidence(row, "", gates)
    )
    if migration_evidence.get("material_failure"):
        return "purged_oos_rank_migration"
    if safe_float(row.get("risk_adjusted_selection_score"), -999.0) < gates.get("risk_adjusted_selection_score_min", 0.0):
        return "weak_risk_adjusted_selection"
    if row.get("accepted_type") in {"both", "market_neutral"}:
        if safe_float(row.get("test_long_short_deflated_sharpe_confidence"), 0.0) < gates.get("deflated_sharpe_confidence_min", 0.60):
            return "deflated_sharpe_fragile"
    return ""


def apply_reliability_acceptance(leaderboard, gates=DEFAULT_GATES):
    for row in leaderboard:
        row["pre_reliability_accepted"] = bool(row.get("accepted"))
        row["pre_reliability_accepted_type"] = row.get("accepted_type")
        row["purged_rank_migration_evidence"] = purged_rank_migration_evidence(
            row, "", gates,
        )
        failure = reliability_failure_type(row, gates)
        row["reliability_failure_type"] = failure
        row["reliability_pass"] = not bool(failure)
        if failure:
            row["accepted"] = False
            row["status"] = "rejected"
            row["accepted_type"] = "rejected"
            row["accepted_type_cn"] = acceptance_label_cn("rejected")
            row["diagnosis"] = {"failure_type": failure}
            row["diagnosis_code"] = failure
            row["diagnosis_cn"] = diagnosis_cn(row["diagnosis"])
    return leaderboard


def apply_search_reliability_diagnosis(leaderboard, gates=DEFAULT_GATES):
    for row in leaderboard:
        failure = ""
        stage_pass = row.get("search_diagnosis_code") == "search_stage_passed"
        migration_evidence = purged_rank_migration_evidence(row, "search_", gates)
        row["search_purged_rank_migration_evidence"] = migration_evidence
        if stage_pass:
            cscv_selected = int(row.get("search_cscv_selected_count") or 0)
            cscv_posterior_risk = safe_float(
                row.get("search_cscv_probability_overfit_above_half"),
                0.5,
            )
            if (
                cscv_selected >= 2
                and cscv_posterior_risk >= gates.get("cscv_overfit_posterior_trigger", 0.80)
            ):
                failure = "search_cscv_overfit_risk"
            elif safe_float(row.get("search_pbo_proxy"), 0.0) > gates.get("max_pbo_proxy", 0.50):
                failure = "search_pbo_overfit_risk"
            elif migration_evidence.get("material_failure"):
                failure = "search_purged_oos_rank_migration"
            elif safe_float(row.get("search_risk_adjusted_selection_score"), -999.0) < gates.get("risk_adjusted_selection_score_min", 0.0):
                failure = "search_risk_adjusted_score_negative"
        row["search_stage_pass"] = stage_pass
        row["search_reliability_failure_type"] = failure
        row["search_statistical_reliability_pass"] = not bool(failure)
        row["search_reliability_pass"] = stage_pass and not bool(failure)
        if failure:
            row["search_diagnosis"] = {"failure_type": failure}
            row["search_diagnosis_code"] = failure
            row["search_diagnosis_cn"] = diagnosis_cn(row["search_diagnosis"])
            candidate = {"family": row.get("family")}
            row["mutation_plan"] = mutation_plan_from_diagnosis(candidate, failure)
    return leaderboard


def build_frontier_leaderboard(panel, evaluated, metrics, audits, gates):
    factor_cols = [f for f in evaluated if f in panel.columns]
    research = search_panel(panel)
    declared_parents = declared_synergy_parent_map(evaluated)
    final_global_redundancy = factor_redundancy(panel, factor_cols)
    search_global_redundancy = factor_redundancy(research, factor_cols)
    final_non_parent_redundancy = factor_redundancy(
        panel, factor_cols, exclusions_by_factor=declared_parents,
    )
    search_non_parent_redundancy = factor_redundancy(
        research, factor_cols, exclusions_by_factor=declared_parents,
    )
    final_parent_redundancy = declared_parent_redundancy(panel, factor_cols, declared_parents)
    search_parent_redundancy = declared_parent_redundancy(research, factor_cols, declared_parents)

    preliminary = []
    for factor, cand in evaluated.items():
        m = metrics.get(factor, {})
        preliminary.append(build_leaderboard_row(
            factor, cand, m, 0.0, audits.get(factor, {"passed": False}), gates,
            search_max_abs_corr=0.0,
        ))
    # Reliability must be known before choosing a representative for each
    # correlated alpha cluster. Otherwise an overfit candidate can occupy the
    # frontier and force every reliable neighbour to fail the novelty gate.
    annotate_pbo_proxy(preliminary, metric_field="purged_kfold_search", prefix="search_")
    annotate_cscv_pbo(preliminary, "search_backtest_curve", prefix="search_")
    apply_search_reliability_diagnosis(preliminary, gates)
    preliminary.sort(key=leaderboard_search_key, reverse=True)
    eligible = [
        row["factor"]
        for row in preliminary
        if row.get("search_diagnosis_code") == "search_stage_passed"
        and row.get("search_reliability_pass", False)
    ]
    novelty_limit = gates.get("novelty_max_corr", gates.get("max_redundancy", 0.82))
    search_frontier = frontier_redundancy(
        research,
        [row["factor"] for row in preliminary],
        eligible,
        max_corr=novelty_limit,
        exclusions_by_factor=declared_parents,
    )
    final_frontier = frontier_redundancy(
        panel,
        [row["factor"] for row in preliminary],
        eligible,
        max_corr=novelty_limit,
        exclusions_by_factor=declared_parents,
    )

    leaderboard = []
    for factor, cand in evaluated.items():
        m = metrics.get(factor, {})
        row = build_leaderboard_row(
            factor,
            cand,
            m,
            final_frontier.get(factor, 0.0),
            audits.get(factor, {"passed": False}),
            gates,
            search_max_abs_corr=search_frontier.get(factor, 0.0),
        )
        row["frontier_max_abs_corr"] = safe_float(final_frontier.get(factor, 0.0))
        row["search_frontier_max_abs_corr"] = safe_float(search_frontier.get(factor, 0.0))
        row["global_max_abs_corr_to_other_factor"] = safe_float(final_global_redundancy.get(factor, 0.0))
        row["search_global_max_abs_corr_to_other_factor"] = safe_float(search_global_redundancy.get(factor, 0.0))
        row["declared_parent_factors"] = list(declared_parents.get(factor, []))
        row["lineage_novelty_exemption_applied"] = bool(declared_parents.get(factor))
        row["max_abs_corr_to_declared_parents"] = safe_float(final_parent_redundancy.get(factor, 0.0))
        row["search_max_abs_corr_to_declared_parents"] = safe_float(search_parent_redundancy.get(factor, 0.0))
        row["global_max_abs_corr_including_declared_parents"] = safe_float(final_global_redundancy.get(factor, 0.0))
        row["search_global_max_abs_corr_including_declared_parents"] = safe_float(search_global_redundancy.get(factor, 0.0))
        row["global_max_abs_corr_excluding_declared_parents"] = safe_float(final_non_parent_redundancy.get(factor, 0.0))
        row["search_global_max_abs_corr_excluding_declared_parents"] = safe_float(search_non_parent_redundancy.get(factor, 0.0))
        row["novelty_evaluation_scope"] = (
            "reliable_frontier_excluding_declared_synergy_parents"
            if declared_parents.get(factor)
            else "reliable_frontier_all_other_candidates"
        )
        row["search_frontier_eligible"] = factor in eligible
        row["search_frontier_representative"] = bool(
            factor in eligible and safe_float(search_frontier.get(factor), 1.0) <= novelty_limit
        )
        row["final_frontier_representative"] = bool(
            factor in eligible and safe_float(final_frontier.get(factor), 1.0) <= novelty_limit
        )
        leaderboard.append(row)

    annotate_pbo_proxy(leaderboard, metric_field="purged_kfold_search", prefix="search_")
    annotate_cscv_pbo(leaderboard, "search_backtest_curve", prefix="search_")
    apply_search_reliability_diagnosis(leaderboard, gates)
    annotate_pbo_proxy(leaderboard)
    annotate_cscv_pbo(leaderboard, "backtest_curve", prefix="test_")
    annotate_multiple_testing_risk(leaderboard)
    annotate_lifecycle_diagnostics(leaderboard)
    apply_reliability_acceptance(leaderboard, gates)
    leaderboard.sort(key=leaderboard_final_key, reverse=True)
    return leaderboard


def reward_score(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    """Final reporting rank; sealed-test evidence is never reused by the search loop."""
    evidence = metrics_by_split.get("posterior_final_evidence") or posterior_final_reporting_evidence(metrics_by_split)
    correlation = float(np.clip(safe_float(max_abs_corr, 0.0), 0.0, 0.999))
    novelty_log_prior = math.log(max(0.05, 1.0 - correlation * correlation))
    _long_only, long_short = backtest_blocks(metrics_by_split)
    drawdown = abs(min(0.0, safe_float(long_short.get("max_drawdown"), 0.0)))
    drawdown_log_prior = math.log(max(0.05, 1.0 - min(0.95, drawdown)))
    return float(safe_float(evidence.get("utility"), -999.0) + novelty_log_prior + drawdown_log_prior)

def reward_breakdown(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    evidence = metrics_by_split.get("posterior_final_evidence") or posterior_final_reporting_evidence(metrics_by_split)
    probabilities = evidence.get("component_probabilities") or {}
    correlation = float(np.clip(safe_float(max_abs_corr, 0.0), 0.0, 0.999))
    novelty_log_prior = math.log(max(0.05, 1.0 - correlation * correlation))
    _long_only, long_short = backtest_blocks(metrics_by_split)
    drawdown = abs(min(0.0, safe_float(long_short.get("max_drawdown"), 0.0)))
    drawdown_log_prior = math.log(max(0.05, 1.0 - min(0.95, drawdown)))
    return {
        "sealed_search_evidence_probability": safe_float(probabilities.get("sealed_search_evidence"), 0.5),
        "test_signal_probability": safe_float(probabilities.get("test_signal"), 0.5),
        "test_incremental_residual_probability": safe_float(probabilities.get("test_incremental_residual"), 0.5),
        "test_downstream_synergy_probability": safe_float(probabilities.get("test_downstream_synergy"), 0.5),
        "test_economic_realization_probability": safe_float(probabilities.get("test_economic_realization"), 0.5),
        "joint_positive_probability": safe_float(evidence.get("joint_positive_probability"), 0.0),
        "posterior_log_odds_utility": safe_float(evidence.get("utility"), -999.0),
        "novelty_log_prior": novelty_log_prior,
        "drawdown_log_prior": drawdown_log_prior,
    }

def split_backtest_blocks(metrics_by_split, split):
    block = metrics_by_split.get(split, {}) if isinstance(metrics_by_split, dict) else {}
    bt = block.get("backtest", {}) if isinstance(block.get("backtest", {}), dict) else {}
    return bt.get("long_only", {}) or {}, bt.get("long_short", {}) or {}


def robust_ratio(value, cap=3.0):
    value = safe_float(value, 0.0)
    return max(-cap, min(cap, value))


def backtest_reliability(block, min_periods=12):
    periods = max(0.0, safe_float(block.get("periods"), 0.0)) if isinstance(block, dict) else 0.0
    if periods <= 0:
        return 0.0
    return min(1.0, math.sqrt(periods / max(1.0, float(min_periods))))


def selection_score(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    """Train/validation-only posterior-odds utility used to choose search parents."""
    evidence = metrics_by_split.get("posterior_search_evidence") or posterior_search_evidence(metrics_by_split)
    correlation = float(np.clip(safe_float(max_abs_corr, 0.0), 0.0, 0.999))
    novelty_log_prior = math.log(max(0.05, 1.0 - correlation * correlation))
    return float(safe_float(evidence.get("utility"), -999.0) + novelty_log_prior)

def selection_breakdown(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    evidence = metrics_by_split.get("posterior_search_evidence") or posterior_search_evidence(metrics_by_split)
    probabilities = evidence.get("component_probabilities") or {}
    correlation = float(np.clip(safe_float(max_abs_corr, 0.0), 0.0, 0.999))
    novelty_log_prior = math.log(max(0.05, 1.0 - correlation * correlation))
    return {
        "train_signal_probability": safe_float(probabilities.get("train_signal"), 0.5),
        "validation_signal_probability": safe_float(probabilities.get("validation_signal"), 0.5),
        "incremental_residual_probability": safe_float(probabilities.get("incremental_residual"), 0.5),
        "downstream_synergy_probability": safe_float(probabilities.get("downstream_synergy"), 0.5),
        "economic_realization_probability": safe_float(probabilities.get("economic_realization"), 0.5),
        "regime_breadth_probability": safe_float(probabilities.get("regime_breadth"), 0.5),
        "joint_positive_probability": safe_float(evidence.get("joint_positive_probability"), 0.0),
        "posterior_log_odds": safe_float(evidence.get("log_posterior_odds"), -999.0),
        "coverage_log_prior": safe_float(evidence.get("coverage_log_prior"), 0.0),
        "complexity_log_prior": safe_float(evidence.get("complexity_log_prior"), 0.0),
        "novelty_log_prior": novelty_log_prior,
        "posterior_utility": safe_float(evidence.get("utility"), -999.0),
    }

def diagnose_search_failure(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    train = metrics_by_split.get("train", {})
    valid = metrics_by_split.get("valid", {})
    evidence = metrics_by_split.get("posterior_search_evidence") or posterior_search_evidence(metrics_by_split)
    valid_ls = split_backtest_blocks(metrics_by_split, "valid")[1]
    minimum_coverage = min(safe_float(train.get("coverage"), 0.0), safe_float(valid.get("coverage"), 0.0))
    if minimum_coverage < 0.20:
        return "low_coverage"

    search_utility = selection_score(metrics_by_split, max_abs_corr, gates)
    turnover = safe_float(valid_ls.get("avg_turnover", valid.get("turnover", 0.0)), 0.0)
    if bool(evidence.get("posterior_pass")) and search_utility > 0.0 and turnover < 1.0:
        return "search_stage_passed"

    pressures = dict(evidence.get("failure_pressures") or {})
    if bool(evidence.get("posterior_pass")):
        probabilities = evidence.get("component_probabilities") or {}
        economic_probability = safe_float(probabilities.get("economic_realization"), 0.5)
        incremental_probability = safe_float(probabilities.get("incremental_residual"), 0.5)
        pressures["novelty"] = (
            float(np.clip(safe_float(max_abs_corr, 0.0), 0.0, 1.0) ** 2)
            * max(0.10, 1.0 - incremental_probability)
        )
        pressures["coverage"] = 1.0 - float(np.clip(minimum_coverage, 0.0, 1.0))
        pressures["turnover"] = (
            float(np.clip(turnover, 0.0, 1.0))
            * max(0.10, 1.0 - economic_probability)
        )
    dominant = max(pressures, key=pressures.get) if pressures else "validation_signal"
    mapping = {
        "train_signal": "posterior_train_signal_uncertain",
        "validation_signal": "posterior_validation_signal_uncertain",
        "incremental_residual": "incremental_information_shortage",
        "downstream_synergy": "synergy_contribution_shortage",
        "economic_realization": "economic_realization_shortage",
        "regime_breadth": "regime_concentration",
        "novelty": "novelty_shortage",
        "coverage": "low_coverage",
        "turnover": "too_much_turnover",
    }
    return mapping.get(dominant, "posterior_validation_signal_uncertain")

def row_search_score(row):
    return safe_float(
        row.get("search_risk_adjusted_selection_score", row.get("selection_score")),
        -999.0,
    )


def acceptance_priority(row):
    return {
        "both": 3,
        "market_neutral": 2,
        "long_only": 1,
        "rejected": 0,
    }.get(str(row.get("accepted_type", "rejected")), 0)


def leaderboard_search_key(row):
    return (
        int(row.get("search_diagnosis_code") == "search_stage_passed"),
        int(bool(row.get("search_reliability_pass", True))),
        row_search_score(row),
        safe_float(row.get("valid_rank_ic"), -999.0),
        safe_float(row.get("search_walk_positive_ratio"), 0.0),
        safe_float(row.get("search_purged_positive_ratio"), 0.0),
        -safe_float(row.get("search_pbo_proxy"), 1.0),
        -safe_float(row.get("search_max_abs_corr_to_other_factor"), 999.0),
    )


def leaderboard_final_key(row):
    lifecycle_priority = {
        "healthy": 3,
        "regime_transition_watch": 2,
        "tail_realization_watch": 2,
        "signal_decay_watch": 1,
        "insufficient_recent_sample": 1,
        "structural_decay": 0,
    }.get(str(row.get("lifecycle_state")), 0)
    return (
        int(bool(row.get("accepted"))),
        lifecycle_priority,
        acceptance_priority(row),
        row_search_score(row),
        safe_float(row.get("reward"), -999.0),
        safe_float(row.get("valid_rank_ic"), -999.0),
        -safe_float(row.get("pbo_proxy"), 1.0),
        -safe_float(row.get("max_abs_corr_to_other_factor"), 999.0),
    )


def diagnosis_cn(diagnosis):
    failure = diagnosis.get("failure_type") if isinstance(diagnosis, dict) else None
    mapping = {
        "both": "市场中性与多头增强检验均通过。",
        "market_neutral": "市场中性组合检验通过，但多头增强仍需复核。",
        "long_only": "多头增强检验通过，但市场中性收益仍需复核。",
        "static_audit_blocked": "静态审计未通过，可能存在字段缺失、复杂度异常或潜在泄漏。",
        "coverage_shortage": "覆盖率不足，无法在足够多股票上稳定计算。",
        "low_coverage": "覆盖率不足，样本横截面不够完整。",
        "redundant_factor": "与候选池其他因子相关性过高，新颖性不足。",
        "novelty_shortage": "与已有候选或记忆库因子过于相似，需要提高表达式新颖性。",
        "sample_out_decay": "训练期有效但测试期衰减明显，存在过拟合风险。",
        "non_monotonic_groups": "分组收益不单调，高分组没有稳定优于低分组。",
        "bad_monotonicity": "验证期分组单调性不足，需要检查方向和分组结构。",
        "high_turnover": "换手率过高，交易成本后稳定性不足。",
        "too_much_turnover": "换手率超过约束，信号需要平滑或加入低换手惩罚。",
        "weak_train_signal": "训练期 RankIC 不达标，原始信号较弱。",
        "weak_valid_signal": "验证期 RankIC 不达标，训练到验证迁移不足。",
        "posterior_train_signal_uncertain": "训练期月度 IC 的后验胜率不足，信号均值相对不确定性没有形成优势。",
        "posterior_validation_signal_uncertain": "验证期月度 IC 后验赔率不足，训练期逻辑尚未稳定迁移到严格样本外。",
        "incremental_information_shortage": "相对训练期冻结基线做因子与收益双残差后，验证期增量信息不足。",
        "synergy_contribution_shortage": "加入候选后，冻结组合模型在验证期的边际 RankIC 没有稳定改善。",
        "economic_realization_shortage": "验证期多空收益兑现的后验胜率弱于预测信号，需要处理成本、尾部或持仓承接。",
        "regime_concentration": "信号集中于少数训练期定义的市场状态，最弱状态后验与状态广度不足。",
        "weak_test_signal": "测试期 RankIC 不达标，样本外预测力不足。",
        "weak_market_neutral": "市场中性多空组合收益、夏普或回撤未达标。",
        "weak_long_only_excess": "多头增强超额收益或信息比率未达标。",
        "weak_group_spread": "验证期高低分组收益差不足。",
        "walk_forward_instability": "滚动样本外检验不稳定，正 IC 窗口占比不足。",
        "purged_kfold_instability": "隔离 K 折检验不稳定，剔除相邻调仓期后仍有样本外衰减。",
        "unstable_quick_screen": "快速初筛未通过，覆盖率、极端值比例或分布稳定性不达标。",
        "quick_screen_failed": "快速初筛未通过，覆盖率、极端值比例或分布稳定性不达标。",
        "market_neutral_only_alpha": "市场中性收益尚可，但多头增强承接不足，可能依赖对冲结构。",
        "event_risk": "全样本 RankIC 为负或事件风险暴露较大。",
        "below_composite_reward": "综合评分不足，未同时满足 IC、收益、稳定性和新颖性。",
        "search_pbo_overfit_risk": "训练与验证搜索空间的 PBO 偏高，样本内领先结构向互补样本迁移不足。",
        "search_cscv_overfit_risk": "组合对称交叉验证显示该候选被选中时容易在互补样本中掉队。",
        "search_purged_oos_rank_migration": "训练与验证期的隔离样本排名迁移不足，候选优势依赖局部窗口。",
        "search_risk_adjusted_score_negative": "扣除搜索过拟合、衰减和不确定性后，训练验证期净证据为负。",
        "pbo_overfit_risk": "最终可靠性审计显示回测过拟合概率偏高。",
        "purged_oos_rank_migration": "最终隔离样本中的候选相对排名迁移不足。",
        "weak_risk_adjusted_selection": "扣除搜索风险后的选择得分不足。",
        "deflated_sharpe_fragile": "考虑多重试验后的夏普置信度不足。",
        "recent_alpha_structural_decay": "\u6700\u8fd1\u6536\u76ca\u3001IC\u5b58\u6d3b\u7387\u4e0e\u540e\u9a8c\u6b63\u6536\u76ca\u6982\u7387\u540c\u65f6\u6076\u5316\uff0c\u5224\u5b9a\u4e3a\u7ed3\u6784\u6027\u8870\u51cf\u3002",
        "failed_unknown": "未通过综合裁判门槛，失败来源需要继续归因。",
    }
    return mapping.get(failure, "未通过综合裁判门槛，需要继续变异搜索。")


def compact_metrics(metrics_by_split):
    out = {}
    for split in ["train", "valid", "test", "full"]:
        m = metrics_by_split.get(split, {})
        bt = m.get("backtest", {}) if isinstance(m.get("backtest"), dict) else {}
        out[split] = {
            "rank_ic": m.get("rank_ic", 0.0),
            "icir": m.get("icir", 0.0),
            "rank_ic_t": m.get("rank_ic_t", 0.0),
            "rank_ic_t_newey_west": m.get("rank_ic_t_newey_west", 0.0),
            "ic_win_rate": m.get("ic_win_rate", 0.0),
            "group_spread": m.get("group_spread", 0.0),
            "turnover": m.get("turnover", 0.0),
            "coverage": m.get("coverage", 0.0),
            "group_monotonicity": m.get("group_monotonicity", 0.0),
            "group_returns": m.get("group_returns", []),
            "long_only": bt.get("long_only", {}),
            "long_short": bt.get("long_short", {}),
            "backtest_curve": bt.get("curve", []),
            "ic_series": m.get("ic_series", []),
            "annual_summary": m.get("annual_summary", []),
        }
    annual = {}
    for key, value in metrics_by_split.items():
        if isinstance(key, str) and key.startswith("year:") and isinstance(value, dict):
            annual[key.split(":", 1)[1]] = {
                "rank_ic": value.get("rank_ic", 0.0),
                "group_spread": value.get("group_spread", 0.0),
                "coverage": value.get("coverage", 0.0),
            }
    out["annual"] = annual
    out["quick_screen"] = metrics_by_split.get("quick_screen", {})
    out["walk_forward"] = metrics_by_split.get("walk_forward", {})
    out["purged_kfold"] = metrics_by_split.get("purged_kfold", {})
    out["incremental_evidence"] = metrics_by_split.get("incremental_evidence", {})
    out["regime_evidence"] = metrics_by_split.get("regime_evidence", {})
    out["posterior_search_evidence"] = metrics_by_split.get("posterior_search_evidence", {})
    out["posterior_final_evidence"] = metrics_by_split.get("posterior_final_evidence", {})
    out["attribution"] = metrics_by_split.get("attribution", {})
    return out


def accepted_flag(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    return acceptance_type(metrics_by_split, max_abs_corr, gates) != "rejected"


def diagnose_failure(metrics_by_split, max_abs_corr, gates=DEFAULT_GATES):
    train = metrics_by_split.get("train", {})
    valid = metrics_by_split.get("valid", {})
    test = metrics_by_split.get("test", {})
    walk = metrics_by_split.get("walk_forward", {})
    purged = metrics_by_split.get("purged_kfold", {})
    lo, ls = backtest_blocks(metrics_by_split)
    quick = metrics_by_split.get("quick_screen", metrics_by_split.get("quick", {}))

    if accepted_flag(metrics_by_split, max_abs_corr, gates):
        return acceptance_type(metrics_by_split, max_abs_corr, gates)
    if safe_float(train.get("rank_ic")) < gates.get("train_rank_ic_min", 0.025):
        return "weak_train_signal"
    if safe_float(valid.get("rank_ic")) < gates.get("valid_rank_ic_min", 0.02):
        return "weak_valid_signal"
    if safe_float(test.get("rank_ic")) < gates.get("test_rank_ic_min", 0.02):
        return "weak_test_signal"
    if safe_float(test.get("coverage")) < gates.get("coverage_min", 0.65):
        return "low_coverage"
    if not quick.get("passed", False):
        return "unstable_quick_screen"
    if safe_float(valid.get("group_monotonicity")) < gates.get("min_group_monotonicity", 0.75):
        return "bad_monotonicity"
    if safe_float(first_metric(walk, "positive_test_ic_ratio", "positive_rate")) < 0.50:
        return "walk_forward_instability"
    if safe_float(first_metric(purged, "positive_test_ic_ratio", "positive_rate")) < 0.50:
        return "purged_kfold_instability"
    if not turnover_gate(metrics_by_split, gates):
        return "too_much_turnover"
    if safe_float(max_abs_corr) > gates.get("novelty_max_corr", gates.get("max_redundancy", 0.82)):
        return "novelty_shortage"
    if market_neutral_gate(metrics_by_split, gates) and not long_only_gate(metrics_by_split, gates):
        return "market_neutral_only_alpha"
    if not market_neutral_gate(metrics_by_split, gates):
        return "weak_market_neutral"
    if not long_only_gate(metrics_by_split, gates):
        return "weak_long_only_excess"
    return "failed_unknown"


def mutation_plan_from_diagnosis(cand, diagnosis):
    base = [
        "保留原始经济假设，只改造表达式结构，避免因为一次失败就丢弃可解释信号。",
        "优先做有约束的定向变异：窗口、平滑、去极值、行业市值中性化、低拥挤惩罚。",
    ]
    mapping = {
        "novelty_shortage": ["替换一个高相关基础因子，或对旧因子做正交残差化。", "加入不同经济域交互，降低与记忆库的相关性。"],
        "weak_train_signal": ["回到方法卡重新生成原始假设，避免在弱信号上继续堆复杂度。", "降低非线性层数，先确认基础方向是否有效。"],
        "weak_valid_signal": ["加入训练到验证的时间衰减约束。", "减少只在训练期有效的窗口和阈值。"],
        "posterior_train_signal_uncertain": ["停止围绕弱父代微调，回到原始时点字段重建可解释假设。", "使用月度后验分布而不是单个均值选择结构。"],
        "posterior_validation_signal_uncertain": ["保留训练期方向锁定，改造原始字段、因果窗口和行业内排序结构。", "优先提升验证期后验赔率而不是放宽门槛。"],
        "incremental_information_shortage": ["对训练期冻结基线做双残差，搜索仍能解释剩余收益的原始字段和路径状态。", "减少对既有 base 组合的重复表达。"],
        "synergy_contribution_shortage": ["以候选加入冻结组合后的验证期边际贡献为目标重组。", "从不同经济域或分岛选择互补父代，而不是叠加高相关强因子。"],
        "economic_realization_shortage": ["用因果平滑、低拥挤与事件风险惩罚改善成本后兑现。", "核对 IC 有效但多空收益弱是否来自尾部、换手或分组承接。"],
        "regime_concentration": ["使用训练期定义的趋势与风险状态做连续软门控。", "优化最弱状态后验和状态覆盖，而不是只提高平均 IC。"],
        "weak_test_signal": ["测试集只用于最终失败归因，不把测试结果反馈进下一轮搜索。", "回到训练验证证据重新生成假设。"],
        "weak_market_neutral": ["强化 Top-Bottom 收益目标，同时惩罚回撤和低胜率。", "检查行业、市值和 beta 暴露，避免收益来自公共风险。"],
        "weak_long_only_excess": ["保留市场中性信号，同时派生多头增强版本。", "加入质量、流动性或低拥挤过滤，提高多头可持有性。"],
        "too_much_turnover": ["加入 5-20 期移动平均或指数衰减。", "提高低换手约束，惩罚短周期翻转过强的表达式。"],
        "low_coverage": ["减少字段依赖或加入缺失值兜底。", "删除过窄条件过滤，提高可计算股票覆盖率。"],
        "bad_monotonicity": ["检查方向符号，必要时对因子取反。", "先做 rank 或 zscore，再做行业市值中性化。"],
        "walk_forward_instability": ["只保留多个滚动窗口反复有效的子表达式。", "缩短记忆窗口并增加时间衰减。"],
        "purged_kfold_instability": ["扩大隔离窗口后重新评估。", "删除疑似事件泄漏或调仓日泄漏输入项。"],
        "search_pbo_overfit_risk": ["降低表达式自由度并对公共成分做残差化。", "只保留在训练验证互补样本中重复有效的子结构。"],
        "search_cscv_overfit_risk": ["采用互补分块下的稳定子表达式重组。", "减少依赖单一市场阶段的窗口和交互。"],
        "search_purged_oos_rank_migration": ["改用训练期定义的市场状态对比表示。", "在不同状态间共享稳定权重并抑制状态特有噪声。"],
        "search_risk_adjusted_score_negative": ["回退到低复杂度行业内排序结构。", "增加质量与低拥挤锚点后重新进入搜索。"],
        "unstable_quick_screen": ["加入 winsorize、rank 或 zscore 稳定分布。", "降低极端非线性算子权重，优先保证横截面可排序。"],
        "market_neutral_only_alpha": ["保留市场中性版本，同时单独生成多头增强分支。", "检查 beta、行业、市值暴露，避免收益只来自对冲结构。"],
    }
    return base + mapping.get(diagnosis, ["回到方法卡重新生成相邻假设，并降低与失败候选的表达式相似度。"])


def build_leaderboard_row(
    factor,
    cand,
    metrics_by_split,
    max_abs_corr,
    audit=None,
    gates=DEFAULT_GATES,
    search_max_abs_corr=None,
):
    te = metrics_by_split.get("test", {})
    va = metrics_by_split.get("valid", {})
    tr = metrics_by_split.get("train", {})
    research = metrics_by_split.get("search", {})
    quick = metrics_by_split.get("quick_screen", {})
    search_quick = metrics_by_split.get("quick_screen_search", quick)
    walk = metrics_by_split.get("walk_forward", {})
    search_walk = metrics_by_split.get("walk_forward_search", {})
    purged = metrics_by_split.get("purged_kfold", {})
    search_purged = metrics_by_split.get("purged_kfold_search", {})
    bt = te.get("backtest", {}) if isinstance(te.get("backtest", {}), dict) else {}
    search_bt = research.get("backtest", {}) if isinstance(research.get("backtest", {}), dict) else {}
    incremental = metrics_by_split.get("incremental_evidence", {}) or {}
    regime = metrics_by_split.get("regime_evidence", {}) or {}
    posterior_evidence = metrics_by_split.get("posterior_search_evidence", {}) or {}
    posterior_final_evidence = metrics_by_split.get("posterior_final_evidence", {}) or {}
    lo = bt.get("long_only", {}) or {}
    ls = bt.get("long_short", {}) or {}
    gates = gates or DEFAULT_GATES
    search_corr = max_abs_corr if search_max_abs_corr is None else search_max_abs_corr
    accepted_kind = acceptance_type(metrics_by_split, max_abs_corr, gates)
    is_accepted = accepted_kind != "rejected"
    diag_code = diagnose_failure(metrics_by_split, max_abs_corr, gates)
    search_diag_code = diagnose_search_failure(metrics_by_split, search_corr, gates)
    diag = {"failure_type": diag_code}
    search_diag = {"failure_type": search_diag_code}
    score = reward_score(metrics_by_split, max_abs_corr, gates)
    score_parts = reward_breakdown(metrics_by_split, max_abs_corr, gates)
    search_score = selection_score(metrics_by_split, search_corr, gates)
    search_parts = selection_breakdown(metrics_by_split, search_corr, gates)
    cname = cand.get("chinese_name") or cand.get("name")
    fields = cand.get("data_fields") or cand.get("required_fields") or []
    return {
        "factor": factor,
        "name": cand.get("name"),
        "family": cand.get("family"),
        "chinese_name": cname,
        "hypothesis": cand.get("hypothesis"),
        "construction": cand.get("construction") or cand.get("hypothesis"),
        "formula": cand.get("formula"),
        "latex_formula": cand.get("latex_formula"),
        "channel": cand.get("channel"),
        "data_fields": fields,
        "data_scope": " x ".join(fields) if isinstance(fields, list) else str(fields),
        "complexity": cand.get("complexity"),
        "quality_diversity_island": cand.get("quality_diversity_island") or candidate_static_island(cand),
        "implementation_audit": cand.get("implementation_audit") or dsl_implementation_audit(cand.get("dsl", {})),
        "static_audit": audit or {},
        "lineage": cand.get("lineage") or [],
        "dsl": cand.get("dsl") or {},
        "portfolio_fraction": safe_float((metrics_by_split.get("portfolio_selection") or {}).get("selected_fraction"), 0.20),
        "portfolio_selection": metrics_by_split.get("portfolio_selection", {}),
        "status": "accepted" if is_accepted else "rejected",
        "accepted": is_accepted,
        "accepted_type": accepted_kind,
        "accepted_type_cn": acceptance_label_cn(accepted_kind),
        "signal_pass": signal_quality_gate(metrics_by_split, gates),
        "long_only_pass": long_only_gate(metrics_by_split, gates),
        "market_neutral_pass": market_neutral_gate(metrics_by_split, gates),
        "novelty_pass": novelty_gate(max_abs_corr, gates),
        "search_novelty_pass": novelty_gate(search_corr, gates),
        "turnover_pass": turnover_gate(metrics_by_split, gates),
        "train_rank_ic": safe_float(tr.get("rank_ic")),
        "valid_rank_ic": safe_float(va.get("rank_ic")),
        "test_rank_ic": safe_float(te.get("rank_ic")),
        "posterior_search_pass": bool(posterior_evidence.get("posterior_pass", False)),
        "posterior_joint_positive_probability": safe_float(posterior_evidence.get("joint_positive_probability"), 0.0),
        "posterior_search_utility": safe_float(posterior_evidence.get("utility"), -999.0),
        "posterior_final_joint_positive_probability": safe_float(posterior_final_evidence.get("joint_positive_probability"), 0.0),
        "posterior_final_reporting_utility": safe_float(posterior_final_evidence.get("utility"), -999.0),
        "valid_incremental_residual_rank_ic": safe_float((incremental.get("valid") or {}).get("residual_rank_ic"), 0.0),
        "test_incremental_residual_rank_ic": safe_float((incremental.get("test") or {}).get("residual_rank_ic"), 0.0),
        "valid_downstream_marginal_rank_ic_gain": safe_float((incremental.get("valid") or {}).get("marginal_rank_ic_gain"), 0.0),
        "test_downstream_marginal_rank_ic_gain": safe_float((incremental.get("test") or {}).get("marginal_rank_ic_gain"), 0.0),
        "valid_regime_positive_breadth": safe_float((regime.get("valid") or {}).get("posterior_positive_breadth"), 0.5),
        "valid_worst_regime_lower_90": safe_float((regime.get("valid") or {}).get("worst_state_lower_90"), -0.10),
        "valid_regime_signal_concentration": safe_float((regime.get("valid") or {}).get("state_signal_concentration"), 1.0),
        "test_group_spread": safe_float(te.get("group_spread")),
        "test_rank_ic_t_newey_west": safe_float(te.get("rank_ic_t_newey_west")),
        "valid_monotonicity": safe_float(va.get("group_monotonicity")),
        "test_coverage": safe_float(te.get("coverage")),
        "test_turnover": safe_float(bt.get("avg_turnover", te.get("turnover"))),
        "test_long_sharpe": safe_float(lo.get("sharpe")),
        "test_long_annual_return": safe_float(lo.get("annual_return")),
        "test_long_max_drawdown": safe_float(lo.get("max_drawdown")),
        "test_long_excess_annual_return": safe_float(lo.get("excess_annual_return")),
        "test_long_information_ratio": safe_float(lo.get("information_ratio")),
        "test_long_short_sharpe": safe_float(ls.get("sharpe")),
        "test_long_short_annual_return": safe_float(ls.get("annual_return")),
        "test_long_short_max_drawdown": safe_float(ls.get("max_drawdown")),
        "test_long_short_win_rate": safe_float(ls.get("win_rate")),
        "redundancy_max_abs_corr": safe_float(max_abs_corr),
        "max_abs_corr_to_other_factor": safe_float(max_abs_corr),
        "search_max_abs_corr_to_other_factor": safe_float(search_corr),
        "quick_passed": bool(quick.get("passed", False)),
        "search_quick_passed": bool(search_quick.get("passed", False)),
        "walk_positive_ratio": safe_float(first_metric(walk, "positive_test_ic_ratio", "positive_rate")),
        "purged_positive_ratio": safe_float(first_metric(purged, "positive_test_ic_ratio", "positive_rate")),
        "search_walk_positive_ratio": safe_float(first_metric(search_walk, "positive_test_ic_ratio", "positive_rate")),
        "search_purged_positive_ratio": safe_float(first_metric(search_purged, "positive_test_ic_ratio", "positive_rate")),
        "reward_score": score,
        "reward": score,
        "reward_breakdown": score_parts,
        "score_breakdown": score_parts,
        "selection_score": search_score,
        "search_score": search_score,
        "selection_breakdown": search_parts,
        "search_breakdown": search_parts,
        "diagnosis": diag,
        "diagnosis_code": diag_code,
        "diagnosis_cn": diagnosis_cn(diag),
        "search_diagnosis": search_diag,
        "search_diagnosis_code": search_diag_code,
        "search_diagnosis_cn": diagnosis_cn(search_diag),
        "mutation_plan": mutation_plan_from_diagnosis(cand, search_diag_code),
        "metrics": metrics_by_split,
        "backtest_curve": bt.get("curve", []),
        "search_backtest_curve": search_bt.get("curve", []),
        "group_returns": te.get("group_returns", {}),
        "ic_series": te.get("ic_series", []),
        "annual_summary": te.get("annual_summary", []),
        "quick_screen": quick,
        "quick_screen_search": search_quick,
        "walk_forward": walk,
        "walk_forward_search": search_walk,
        "purged_kfold": purged,
        "purged_kfold_search": search_purged,
        "incremental_evidence": incremental,
        "regime_evidence": regime,
        "posterior_search_evidence": posterior_evidence,
        "posterior_final_evidence": posterior_final_evidence,
        "evaluation_error": metrics_by_split.get("evaluation_error"),
        "evaluation_failed": bool(metrics_by_split.get("evaluation_error")),
    }


def build_memory_update(leaderboard):
    search_passed = [
        row for row in leaderboard
        if row.get("search_stage_pass") and row.get("search_reliability_pass")
    ]
    search_rejected = [row for row in leaderboard if row not in search_passed]
    search_failures = defaultdict(int)
    final_reporting_failures = defaultdict(int)
    for row in search_rejected:
        search_failures[(row.get("search_diagnosis") or {}).get("failure_type", "unknown")] += 1
    for row in leaderboard:
        final_reporting_failures[(row.get("diagnosis") or {}).get("failure_type", "unknown")] += 1

    useful_ops = [{
        "factor": row.get("factor"),
        "family": row.get("family"),
        "channel": row.get("channel"),
        "dsl": row.get("dsl") or {},
        "quality_diversity_island": row.get("quality_diversity_island"),
        "selection_score": row.get("selection_score"),
        "risk_adjusted_selection_score": row.get("search_risk_adjusted_selection_score"),
        "posterior_joint_positive_probability": row.get("posterior_joint_positive_probability"),
        "valid_incremental_residual_rank_ic": row.get("valid_incremental_residual_rank_ic"),
        "valid_downstream_marginal_rank_ic_gain": row.get("valid_downstream_marginal_rank_ic_gain"),
        "valid_regime_positive_breadth": row.get("valid_regime_positive_breadth"),
        "search_pbo_proxy": row.get("search_pbo_proxy"),
        "test_metrics_used": False,
    } for row in sorted(search_passed, key=leaderboard_search_key, reverse=True)[:10]]
    discarded = [{
        "factor": row.get("factor"),
        "family": row.get("family"),
        "channel": row.get("channel"),
        "dsl": row.get("dsl") or {},
        "quality_diversity_island": row.get("quality_diversity_island"),
        "failure_type": (row.get("search_diagnosis") or {}).get("failure_type"),
        "search_score": row.get("search_risk_adjusted_selection_score"),
        "posterior_joint_positive_probability": row.get("posterior_joint_positive_probability"),
        "valid_incremental_residual_rank_ic": row.get("valid_incremental_residual_rank_ic"),
        "valid_downstream_marginal_rank_ic_gain": row.get("valid_downstream_marginal_rank_ic_gain"),
        "search_pbo_proxy": row.get("search_pbo_proxy"),
        "search_max_abs_corr": row.get("search_max_abs_corr_to_other_factor"),
        "ast_similarity": row.get("max_ast_similarity_prior"),
        "test_metrics_used": False,
    } for row in sorted(search_rejected, key=leaderboard_search_key, reverse=True)[:20]]

    next_actions = []
    if search_failures.get("incremental_information_shortage", 0):
        next_actions.append("扩大原始时点字段、因果路径和图残差搜索，直接优化冻结基线后的双残差增量信息。")
    if search_failures.get("synergy_contribution_shortage", 0):
        next_actions.append("按质量多样性分岛重组互补候选，以验证期组合边际贡献而非单因子均值选父代。")
    if search_failures.get("regime_concentration", 0):
        next_actions.append("用训练期定义的状态后验优化最弱状态和状态广度，避免平均 IC 掩盖单状态依赖。")
    if search_failures.get("posterior_validation_signal_uncertain", 0):
        next_actions.append("注入全新 GPT 经济假设并减少弱父代的局部参数变异。")
    if any(search_failures.get(code, 0) for code in ["search_pbo_overfit_risk", "search_cscv_overfit_risk"]):
        next_actions.append("保留互补样本中重复领先的结构，并用 DSR、PBO 与 CSCV 抑制多重试验膨胀。")
    if search_failures.get("novelty_shortage", 0):
        next_actions.append("优先探索未覆盖经济域和算子分岛，并对公共基线成分做正交残差化。")
    if not next_actions:
        next_actions.append("继续交替执行诊断变异与全新 GPT 假设注入，保持测试集封存。")
    return {
        "memory_policy": "train_validation_search_only_no_test_or_lifecycle_feedback",
        "accepted_patterns": useful_ops,
        "discarded_patterns": discarded,
        "successful_subexpression_motifs": build_subexpression_memory(useful_ops),
        "failed_subexpression_motifs": build_subexpression_memory(discarded),
        "search_failure_counts": dict(search_failures),
        "final_reporting_failure_counts_not_persisted": dict(final_reporting_failures),
        "channel_controller": build_search_channel_controller(leaderboard),
        "next_search_actions": next_actions,
        "test_fields_in_search_memory": False,
    }

def build_search_channel_controller(leaderboard):
    groups = defaultdict(list)
    for row in leaderboard:
        groups[str(row.get("channel") or "other")].append(row)
    total = max(1, len(leaderboard))
    controller = {}
    for channel, rows in groups.items():
        passes = sum(
            1 for row in rows
            if row.get("search_diagnosis_code") == "search_stage_passed"
            and row.get("search_reliability_pass", True)
        )
        alpha = 1.0 + passes
        beta = 1.0 + len(rows) - passes
        posterior = alpha / (alpha + beta)
        scores = [row_search_score(row) for row in rows]
        pbo = [safe_float(row.get("search_pbo_proxy"), 0.5) for row in rows]
        novelty = [
            max(0.0, 1.0 - safe_float(row.get("search_max_abs_corr_to_other_factor"), 1.0))
            for row in rows
        ]
        exploration = math.sqrt(math.log(total + 2.0) / (len(rows) + 1.0))
        mean_score = float(np.mean(scores)) if scores else -1.0
        mean_pbo = float(np.mean(pbo)) if pbo else 0.5
        mean_novelty = float(np.mean(novelty)) if novelty else 0.0
        utility = mean_score + 0.20 * posterior + 0.12 * exploration + 0.08 * mean_novelty - 0.10 * mean_pbo
        controller[channel] = {
            "evaluated": len(rows),
            "search_passes": passes,
            "posterior_alpha": alpha,
            "posterior_beta": beta,
            "posterior_success": posterior,
            "mean_search_score": mean_score,
            "mean_search_pbo": mean_pbo,
            "mean_novelty": mean_novelty,
            "exploration_bonus": exploration,
            "utility": float(utility),
        }
    return dict(sorted(controller.items(), key=lambda kv: kv[1]["utility"], reverse=True))


def quality_diversity_research_directive(leaderboard):
    island_counts = defaultdict(int)
    failure_counts = defaultdict(int)
    for row in leaderboard:
        island_counts[str(row.get("quality_diversity_island") or "unknown")] += 1
        failure_counts[str(row.get("search_diagnosis_code") or "unknown")] += 1
    sparse_islands = [
        island for island, _count in sorted(island_counts.items(), key=lambda item: (item[1], item[0]))[:4]
    ]
    dominant_failures = [
        failure for failure, _count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))[:4]
        if failure != "search_stage_passed"
    ]
    return (
        "Generate a genuinely fresh economic hypothesis rather than a parameter mutation. "
        f"Underexplored islands: {sparse_islands or ['raw-field causal temporal or state-conditioned']}. "
        f"Dominant train-validation failures to avoid: {dominant_failures or ['none yet']}. "
        "Prefer raw point-in-time fields, causal temporal operators or soft state gates when economically justified. "
        "The candidate must seek positive dual-residual incremental IC and downstream marginal contribution "
        "against the frozen baseline while remaining robust across train-defined market states."
    )


def generate_llm_fresh_hypothesis(method_cards, memory, leaderboard, all_candidates, iteration, budget=1):
    if budget <= 0:
        return [], {"status": "skipped", "reason": "no_budget"}
    directive = quality_diversity_research_directive(leaderboard)
    avoid = [{
        "family": candidate.get("family"),
        "quality_diversity_island": candidate.get("quality_diversity_island") or candidate_static_island(candidate),
        "required_fields": candidate.get("required_fields"),
        "dsl_ops": sorted(collect_dsl_ops(candidate.get("dsl", {}))),
    } for candidate in all_candidates[-20:]]
    raw, status = generate_llm_api_candidate_batch(
        method_cards,
        memory,
        budget,
        diversity_slot=int(iteration) + len(LLM_DIVERSITY_FOCI),
        avoid_candidates=avoid,
        research_directive=directive,
    )
    out = []
    for candidate in raw:
        lineage = list(candidate.get("lineage") or []) + [{
            "generation_mode": "periodic_fresh_hypothesis_injection",
            "iteration": int(iteration),
            "research_directive": directive,
            "test_metrics_used": False,
        }]
        out.append(make_candidate(
            chinese_name=candidate.get("chinese_name", "GPT全新假设注入因子"),
            channel="llm_fresh_hypothesis_injection",
            family=candidate.get("family", "gpt_fresh_hypothesis"),
            dsl=candidate.get("dsl", {}),
            hypothesis=candidate.get("hypothesis", directive),
            data_scope=candidate.get("data_scope", ", ".join(candidate.get("required_fields") or [])),
            windows=candidate.get("windows", []),
            neutralize=True,
            lineage=lineage,
        ))
    status = dict(status or {})
    status.update({
        "generation_mode": "periodic_fresh_hypothesis_injection",
        "research_directive": directive,
        "valid_candidate_count": len(out),
    })
    return out, status

def generate_llm_feedback_mutations(parent_rows, evaluated, memory, iteration, budget=1):
    if budget <= 0 or not parent_rows:
        return [], {"status": "skipped", "reason": "no_budget_or_parent"}
    parent_payload = []
    for row in parent_rows[: min(4, len(parent_rows))]:
        factor = row.get("factor")
        candidate = evaluated.get(factor)
        if not candidate:
            continue
        parent_payload.append({
            "factor": factor,
            "family": row.get("family"),
            "dsl": candidate.get("dsl"),
            "required_fields": candidate.get("required_fields"),
            "complexity": row.get("complexity"),
            "train_rank_ic": row.get("train_rank_ic"),
            "valid_rank_ic": row.get("valid_rank_ic"),
            "search_diagnosis": row.get("search_diagnosis"),
            "search_pbo_proxy": row.get("search_pbo_proxy"),
            "search_cscv_pbo_when_selected": row.get("search_cscv_pbo_when_selected"),
            "search_purged_oos_rank_percentile_mean": row.get("search_purged_oos_rank_percentile_mean"),
            "search_max_abs_corr": row.get("search_max_abs_corr_to_other_factor"),
            "search_risk_adjusted_score": row.get("search_risk_adjusted_selection_score"),
            "quality_diversity_island": row.get("quality_diversity_island"),
            "valid_incremental_residual_rank_ic": row.get("valid_incremental_residual_rank_ic"),
            "valid_downstream_marginal_rank_ic_gain": row.get("valid_downstream_marginal_rank_ic_gain"),
            "valid_regime_positive_breadth": row.get("valid_regime_positive_breadth"),
            "valid_worst_regime_lower_90": row.get("valid_worst_regime_lower_90"),
            "posterior_joint_positive_probability": row.get("posterior_joint_positive_probability"),
            "posterior_search_utility": row.get("posterior_search_utility"),
        })
    if not parent_payload:
        return [], {"status": "skipped", "reason": "no_valid_parent_payload"}
    payload = {
        "task": "diagnosis_conditioned_factor_program_mutation",
        "iteration": int(iteration),
        "budget": int(budget),
        "parents": parent_payload,
        "data_space": AI_FEATURES,
        "feature_semantics": AI_FEATURE_SEMANTICS,
        "research_directive": "repair the diagnosed train-validation failure without using test evidence",
        "allowed_dsl_ops": sorted(ALLOWED_AI_DSL_OPS),
        "dsl_grammar": {
            "feature": {"op": "feature", "name": "one data_space field"},
            "unary": {"op": "rank|industry_rank|zscore|clip01|neg|graph_concept_residual", "child": "one DSL node"},
            "causal_temporal": {"op": "ts_delta|ts_mean|ts_std|ts_zscore", "child": "one DSL node", "window": [1, 2, 3, 6, 12]},
            "soft_gate": {"op": "soft_gate", "gate": "state DSL", "if_true": "DSL branch", "if_false": "DSL branch"},
            "weighted_add": {"op": "add", "children": "2-6 DSL nodes", "weights": "same-length positive number array"},
            "binary": {"op": "sub|mul|div", "left": "DSL node", "right": "DSL node"},
        },
        "search_memory_scope": memory.get("memory_scope", "train_validation_search_only"),
        "search_failure_counts": memory.get("failure_counts", {}),
        "constraints": {
            "use_test_metrics": False,
            "use_final_acceptance": False,
            "preserve_economic_rationale": True,
            "must_change_program_semantics": True,
            "max_dsl_complexity": 28,
            "one_period_label_embargo": True,
            "strict_post_train_oos_validation": True,
        },
        "required_json_schema": {
            "candidates": [{
                "parent_factor": "one factor id from parents",
                "chinese_name": "string",
                "family": "string",
                "hypothesis": "why this mutation addresses the observed search failure",
                "dsl": {"op": "add", "children": [], "weights": []},
                "data_scope": "fields used",
                "windows": [20, 60, 120],
                "mutation_reason": "specific diagnosis-to-program change",
                "anti_overfit_plan": "train-validation-only rationale",
            }]
        },
    }
    system_prompt = (
        "You are the feedback mutation component of an institutional A-share factor-mining agent. "
        "Use only the supplied train/validation diagnostics and search-memory fields. Test metrics do not exist for this task. "
        "Produce a semantically changed, auditable DSL program that directly addresses the diagnosed failure without merely changing a scalar threshold. "
        "Follow the supplied DSL grammar and return strict JSON only."
    )
    parsed, status = call_ai_router_json(system_prompt, payload, retries=1)
    status = dict(status or {})
    status["stage"] = "diagnosis_conditioned_feedback_mutation"
    if not parsed or not isinstance(parsed, dict):
        status.update({"valid_candidate_count": 0, "validation_issues": ["missing_or_invalid_json"]})
        return [], status

    parent_map = {item["factor"]: item for item in parent_payload}
    out = []
    validation_issues = []
    raw_candidates = parsed.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raw_candidates = []
        validation_issues.append("candidates_not_list")
    for idx, item in enumerate(raw_candidates[:budget]):
        if not isinstance(item, dict):
            validation_issues.append(f"candidate_{idx + 1}_not_object")
            continue
        parent_factor = str(item.get("parent_factor") or parent_payload[0]["factor"])
        if parent_factor not in parent_map:
            parent_factor = parent_payload[0]["factor"]
        dsl = sanitize_ai_dsl(item.get("dsl"))
        if not dsl:
            validation_issues.append(f"candidate_{idx + 1}_invalid_dsl")
            continue
        if dsl_complexity(dsl) > 28:
            validation_issues.append(f"candidate_{idx + 1}_complexity_above_28")
            continue
        parent = evaluated[parent_factor]
        out.append(make_candidate(
            chinese_name=item.get("chinese_name") or f"GPT feedback mutation {iteration}",
            channel="llm_feedback_mutation",
            family=clean_name(item.get("family") or f"feedback_{parent.get('family', 'factor')}", 48),
            dsl=dsl,
            hypothesis=item.get("hypothesis") or "GPT diagnosis-conditioned train-validation feedback mutation.",
            data_scope=item.get("data_scope") or ", ".join(extract_features(dsl)),
            windows=item.get("windows") or parent.get("windows") or [20, 60, 120],
            neutralize=True,
            lineage=[{
                "parent_factor": parent_factor,
                "feedback_iteration": int(iteration),
                "model": DEFAULT_MODEL_NAME,
                "reasoning_effort": DEFAULT_REASONING_EFFORT,
                "api_status": status,
                "mutation_reason": item.get("mutation_reason"),
                "anti_overfit_plan": item.get("anti_overfit_plan"),
                "evidence_scope": "embargoed_train_validation_search_only",
                "test_metrics_used": False,
            }],
        ))
    status.update({
        "requested_candidate_count": int(budget),
        "raw_candidate_count": len(raw_candidates),
        "valid_candidate_count": len(out),
        "validation_issues": validation_issues,
        "failure_is_nonfatal_after_required_initial_gpt_preflight": True,
    })
    return out, status

def build_mcts_feedback_expansion(search_ranked, evaluated, iteration, panel=None):
    """Expand one posterior-MCTS node using train/validation evidence only.

    Tree statistics are reconstructed from immutable candidate lineage on every
    round. Every evaluated descendant is back-propagated to all ancestors, so
    the search rewards mechanisms that keep producing robust children instead
    of repeatedly selecting a single lucky formula.
    """
    rows = [
        row for row in search_ranked
        if row.get("factor") in evaluated and not row.get("evaluation_failed")
    ]
    if not rows:
        return None
    rows_by_factor = {str(row["factor"]): row for row in rows}

    parent_of = {}
    children = defaultdict(list)
    for factor, candidate in evaluated.items():
        for event in reversed(candidate.get("lineage") or []):
            parent = event.get("mcts_parent") if isinstance(event, dict) else None
            if parent and str(parent) in evaluated:
                parent_of[str(factor)] = str(parent)
                children[str(parent)].append(str(factor))
                break

    def node_path(factor):
        path = [str(factor)]
        seen = {str(factor)}
        while path[-1] in parent_of and parent_of[path[-1]] not in seen:
            path.append(parent_of[path[-1]])
            seen.add(path[-1])
        return path

    def bounded_reward(row):
        evidence = row.get("posterior_search_evidence") or {}
        utility = safe_float(evidence.get("utility"), row_search_score(row))
        return float(math.tanh(np.clip(utility, -30.0, 30.0) / 3.0))

    reward_paths = defaultdict(list)
    own_rewards = {}
    for factor, row in rows_by_factor.items():
        reward = bounded_reward(row)
        own_rewards[factor] = reward
        for ancestor in node_path(factor):
            reward_paths[ancestor].append(reward)

    all_rewards = np.asarray(list(own_rewards.values()), dtype=float)
    reward_dispersion = float(np.std(all_rewards)) if len(all_rewards) > 1 else 0.0
    exploration_constant = float(np.clip(0.35 + reward_dispersion, 0.35, 1.25))
    total_visits = max(1, sum(len(values) for values in reward_paths.values()))

    scored = []
    for row in rows:
        factor = str(row["factor"])
        subtree_rewards = reward_paths.get(factor) or [own_rewards.get(factor, -1.0)]
        visit_count = max(1, len(subtree_rewards))
        q_value = float(np.mean(subtree_rewards))
        exploration_bonus = exploration_constant * math.sqrt(
            math.log(total_visits + 1.0) / visit_count
        )
        allowed_children = max(1, int(math.ceil(1.5 * math.sqrt(visit_count))))
        child_count = len(set(children.get(factor, [])))
        progressively_open = child_count < allowed_children
        uct = q_value + exploration_bonus
        scored.append((progressively_open, uct, q_value, -visit_count, factor, row, allowed_children, child_count))
    open_nodes = [item for item in scored if item[0]] or scored
    open_nodes.sort(reverse=True, key=lambda item: (item[1], item[2], item[3], item[4]))
    _, uct, q_value, neg_visits, factor, parent_row, allowed_children, child_count = open_nodes[0]

    parent = evaluated[factor]
    used_features = set(parent.get("required_fields") or [])
    seed = int(hashlib.sha1(f"{factor}|{iteration}".encode("utf-8")).hexdigest()[:8], 16)
    failure = str((parent_row.get("search_diagnosis") or {}).get("failure_type", "search_stage_passed"))
    weakest = str((parent_row.get("posterior_search_evidence") or {}).get("weakest_component", ""))

    if weakest == "downstream_synergy" or failure == "synergy_contribution_shortage":
        feature_space = [
            "large_order_balance", "netflow_intensity", "netprofit_yoy", "earnings_event_count_60",
            "dv_ttm", "gross_margin", "assets_turn", "netprofit_margin", "debt_to_assets", "op_yoy",
        ]
    elif weakest == "incremental_residual" or failure == "incremental_information_shortage":
        feature_space = [
            "assets_turn", "netprofit_margin", "gross_margin", "netprofit_yoy", "large_order_balance",
            "netflow_intensity", "earnings_event_count_60", "risk_event_count_60", "dv_ttm", "debt_to_assets",
        ]
    else:
        feature_space = list(dict.fromkeys(list(RAW_AI_FEATURES) + list(BASE_FEATURES)))
    available = [feature for feature in feature_space if feature not in used_features]
    if not available:
        available = feature_space
    feature = available[seed % len(available)]
    window = (2, 3, 6)[seed % 3]
    feature_orientation = 1.0
    feature_selection_audit = {
        "mode": "deterministic_semantic_pool",
        "candidate_fields": len(available),
        "test_metrics_used": False,
    }

    if (
        panel is not None
        and factor in panel.columns
        and "label_next_ret" in panel.columns
        and weakest in {"incremental_residual", "downstream_synergy"}
    ):
        try:
            train_panel = split_panel(panel, "train")
            train_index = train_panel.index
            train_dates = sorted(train_panel["trade_date"].astype(str).unique())
            if len(train_dates) < 12:
                raise ValueError("insufficient_train_dates_for_nested_parameter_selection")
            midpoint = len(train_dates) // 2
            early_dates = set(train_dates[: max(1, midpoint - 1)])
            late_dates = set(train_dates[min(len(train_dates), midpoint + 1):])
            early_index = train_panel[train_panel["trade_date"].astype(str).isin(early_dates)].index
            late_index = train_panel[train_panel["trade_date"].astype(str).isin(late_dates)].index
            if len(early_index) == 0 or len(late_index) == 0:
                raise ValueError("empty_nested_train_fold")

            baseline = baseline_research_state(panel)
            parent_rank = rank_center_by_date(panel, panel[factor])
            target_residual = baseline["target_residual"]
            evidence_cache = {}

            def oriented_fold_stats(evidence, orientation):
                posterior = evidence.get("posterior") or {}
                mean = safe_float(posterior.get("posterior_mean"), 0.0)
                std = max(1e-9, safe_float(posterior.get("posterior_std"), 0.04))
                probability = safe_float(posterior.get("positive_probability"), 0.5)
                if orientation < 0:
                    probability = 1.0 - probability
                return {
                    "rank_ic": orientation * safe_float(evidence.get("rank_ic"), 0.0),
                    "posterior_mean": orientation * mean,
                    "lower_90": orientation * mean - 1.2815515655446004 * std,
                    "positive_probability": probability,
                    "periods": int(evidence.get("periods") or 0),
                }

            def train_feature_evidence(candidate_feature, candidate_window):
                key = (candidate_feature, int(candidate_window))
                if key in evidence_cache:
                    return evidence_cache[key]
                temporal = causal_stock_transform(
                    panel,
                    rank_center_by_date(panel, panel[candidate_feature]),
                    "ts_delta",
                    int(candidate_window),
                )
                candidate_rank = rank_center_by_date(panel, temporal)
                incremental_path = residualize_cross_section(panel, candidate_rank, parent_rank)
                full_evidence = monthly_pair_evidence(
                    panel.loc[train_index],
                    incremental_path.loc[train_index],
                    target_residual.loc[train_index],
                )
                early_evidence = monthly_pair_evidence(
                    panel.loc[early_index],
                    incremental_path.loc[early_index],
                    target_residual.loc[early_index],
                )
                early_mean = safe_float((early_evidence.get("posterior") or {}).get("posterior_mean"), 0.0)
                orientation = 1.0 if early_mean >= 0 else -1.0
                late_evidence = monthly_pair_evidence(
                    panel.loc[late_index],
                    incremental_path.loc[late_index],
                    target_residual.loc[late_index],
                )
                full_stats = oriented_fold_stats(full_evidence, orientation)
                early_stats = oriented_fold_stats(early_evidence, orientation)
                late_stats = oriented_fold_stats(late_evidence, orientation)
                result = {
                    "feature": candidate_feature,
                    "window": int(candidate_window),
                    "orientation": orientation,
                    "oriented_lower_90": min(early_stats["lower_90"], late_stats["lower_90"]),
                    "oriented_probability": math.sqrt(max(0.0, early_stats["positive_probability"] * late_stats["positive_probability"])),
                    "oriented_rank_ic": min(early_stats["rank_ic"], late_stats["rank_ic"]),
                    "full_train": full_stats,
                    "early_train": early_stats,
                    "late_train": late_stats,
                }
                evidence_cache[key] = result
                return result

            field_trials = [train_feature_evidence(item, 3) for item in available[:8]]
            best_field = max(
                field_trials,
                key=lambda item: (item["oriented_lower_90"], item["oriented_rank_ic"], item["oriented_probability"]),
            )
            window_trials = [train_feature_evidence(best_field["feature"], item) for item in (2, 3, 6)]
            selected = max(
                window_trials,
                key=lambda item: (item["oriented_lower_90"], item["oriented_rank_ic"], item["oriented_probability"]),
            )
            feature = selected["feature"]
            window = selected["window"]
            feature_orientation = selected["orientation"]
            feature_selection_audit = {
                "mode": "nested_train_parent_residual_posterior_selection",
                "candidate_fields": len(field_trials),
                "candidate_windows": len(window_trials),
                "internal_hypotheses_evaluated": len(evidence_cache),
                "selected_train_oriented_rank_ic": selected["full_train"]["rank_ic"],
                "selected_train_oriented_lower_90": selected["oriented_lower_90"],
                "selected_train_oriented_probability": selected["oriented_probability"],
                "selected_early_train": selected["early_train"],
                "selected_late_train": selected["late_train"],
                "nested_fold_date_counts": {"early": len(early_dates), "embargo": 2, "late": len(late_dates)},
                "selection_split": "embargoed_nested_train_only",
                "validation_metrics_used": False,
                "test_metrics_used": False,
            }
        except Exception as exc:
            feature_selection_audit = {
                "mode": "deterministic_semantic_pool_after_train_selector_error",
                "error_type": type(exc).__name__,
                "candidate_fields": len(available),
                "test_metrics_used": False,
            }

    temporal_feature_node = {"op": "ts_delta", "child": rank_node(feature), "window": window}
    if feature_orientation < 0:
        temporal_feature_node = {"op": "neg", "child": temporal_feature_node}

    if weakest == "incremental_residual" or failure == "incremental_information_shortage":
        dsl = {
            "op": "graph_concept_residual",
            "child": {"op": "industry_rank", "child": add_node([
                (parent["dsl"], 0.44),
                (temporal_feature_node, 0.34),
                (rank_node("base_low_crowding"), 0.22),
            ])},
        }
        action = "dual_residual_raw_temporal_expansion"
    elif weakest == "downstream_synergy" or failure == "synergy_contribution_shortage":
        if feature in {"large_order_balance", "netflow_intensity"}:
            supporting_path = {"op": "ts_delta", "child": rank_node("gross_margin"), "window": 3}
        else:
            supporting_path = {"op": "ts_mean", "child": rank_node("large_order_balance"), "window": 3}
        dsl = {
            "op": "graph_concept_residual",
            "child": {"op": "industry_rank", "child": add_node([
                (temporal_feature_node, 0.46),
                (supporting_path, 0.22),
                ({"op": "neg", "child": rank_node("pb")}, 0.16),
                (rank_node("base_low_crowding"), 0.10),
                ({"op": "neg", "child": rank_node("base_event_risk")}, 0.06),
            ])},
        }
        action = "downstream_orthogonal_complement_expansion"
    elif weakest == "regime_breadth" or failure == "regime_concentration":
        dsl = {
            "op": "soft_gate",
            "gate": {"op": "ts_zscore", "child": rank_node("turnover_rate"), "window": 6},
            "if_true": add_node([
                (parent["dsl"], 0.56),
                (rank_node("base_quality"), 0.24),
                (rank_node("base_low_crowding"), 0.20),
            ]),
            "if_false": add_node([
                (parent["dsl"], 0.52),
                ({"op": "ts_mean", "child": rank_node("large_order_balance"), "window": 3}, 0.28),
                ({"op": "neg", "child": rank_node("pb")}, 0.20),
            ]),
        }
        action = "continuous_regime_breadth_expansion"
    elif weakest == "economic_realization" or failure == "economic_realization_shortage":
        dsl = {"op": "industry_rank", "child": add_node([
            ({"op": "ts_mean", "child": parent["dsl"], "window": 3}, 0.58),
            ({"op": "ts_mean", "child": rank_node("large_order_balance"), "window": 3}, 0.20),
            (rank_node("base_low_crowding"), 0.14),
            ({"op": "neg", "child": rank_node("risk_event_count_60")}, 0.08),
        ])}
        action = "turnover_aware_realization_expansion"
    elif weakest in {"train_signal", "validation_signal"} or failure in {
        "posterior_train_signal_uncertain", "posterior_validation_signal_uncertain",
    }:
        dsl = {"op": "industry_rank", "child": add_node([
            ({"op": "ts_mean", "child": parent["dsl"], "window": 2}, 0.62),
            (rank_node("base_quality"), 0.20),
            (rank_node("base_low_crowding"), 0.18),
        ])}
        action = "validation_stability_smoothing_expansion"
    elif failure in {"bad_monotonicity", "non_monotonic_groups"}:
        dsl = {"op": "industry_rank", "child": add_node([
            (parent["dsl"], 0.58),
            (rank_node(feature), 0.18),
            (rank_node("base_quality"), 0.14),
            (rank_node("base_low_crowding"), 0.10),
        ])}
        action = "industry_rank_monotonic_expansion"
    elif failure in {"novelty_shortage", "redundant_factor"}:
        dsl = add_node([
            ({"op": "mul", "left": parent["dsl"], "right": rank_node(feature)}, 0.62),
            (rank_node("base_low_crowding"), 0.22),
            ({"op": "neg", "child": rank_node("base_event_risk")}, 0.16),
        ])
        action = "cross_domain_nonlinear_novelty_expansion"
    elif failure in {"search_pbo_overfit_risk", "search_cscv_overfit_risk", "search_purged_oos_rank_migration"}:
        fields = sorted(set(list(used_features) + [feature, "base_quality", "base_low_crowding"]))[:6]
        dsl = {"op": "industry_rank", "child": add_node([
            ({"op": "graph_concept_residual", "features": fields}, 0.52),
            (rank_node("base_quality"), 0.26),
            (rank_node("base_low_crowding"), 0.22),
        ])}
        action = "low_complexity_residual_expansion"
    else:
        dsl = add_node([
            (parent["dsl"], 0.62),
            ({"op": "mul", "left": rank_node(feature), "right": rank_node("base_low_crowding")}, 0.20),
            (rank_node("base_quality"), 0.10),
            ({"op": "neg", "child": rank_node("base_event_risk")}, 0.08),
        ])
        action = "posterior_uct_cross_domain_expansion"

    path = list(reversed(node_path(factor)))
    return make_candidate(
        chinese_name=f"后验树搜索因子{iteration}",
        channel="mcts_feedback_tree_search",
        family=f"mcts_feedback_{parent.get('family', 'factor')}",
        dsl=dsl,
        hypothesis="Use lineage-backpropagated train-validation posterior MCTS with progressive widening; expand the weakest evidence dimension without consulting sealed test metrics.",
        data_scope=f"{parent.get('data_scope', '')}, {feature}",
        windows=sorted(set((parent.get("windows") or []) + [window])),
        neutralize=True,
        lineage=[{
            "mcts_parent": factor,
            "mcts_iteration": int(iteration),
            "mcts_depth": len(path),
            "mcts_path": path,
            "mcts_subtree_visits": int(-neg_visits),
            "mcts_subtree_q_value": float(q_value),
            "mcts_uct": float(uct),
            "mcts_exploration_constant": exploration_constant,
            "mcts_progressive_width": int(allowed_children),
            "mcts_existing_children": int(child_count),
            "mcts_action": action,
            "expanded_feature": feature,
            "expanded_window": int(window),
            "expanded_feature_orientation": float(feature_orientation),
            "feature_selection_audit": feature_selection_audit,
            "parent_search_failure": failure,
            "parent_weakest_posterior_component": weakest,
            "backpropagation_scope": "entire_mcts_lineage",
            "evidence_scope": "embargoed_train_validation_only",
            "test_metrics_used": False,
        }],
    )

def select_search_parents(search_ranked, limit):
    rows = [row for row in search_ranked if row.get("factor")]
    if not rows or limit <= 0:
        return []

    def objectives(row):
        return (
            row_search_score(row),
            safe_float(row.get("valid_rank_ic"), -999.0),
            safe_float(row.get("search_purged_positive_ratio"), 0.0),
            safe_float(row.get("valid_incremental_residual_rank_ic"), -999.0),
            safe_float(row.get("valid_downstream_marginal_rank_ic_gain"), -999.0),
            safe_float(row.get("valid_regime_positive_breadth"), 0.0),
            max(0.0, 1.0 - safe_float(row.get("search_max_abs_corr_to_other_factor"), 1.0)),
            -safe_float(row.get("complexity"), 999.0),
        )

    def dominates(left, right):
        a, b = objectives(left), objectives(right)
        return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))

    frontier = [
        row for row in rows
        if not any(dominates(other, row) for other in rows if other is not row)
    ]
    frontier.sort(key=leaderboard_search_key, reverse=True)
    remainder = [row for row in rows if row not in frontier]
    remainder.sort(key=leaderboard_search_key, reverse=True)

    selected = []
    seen_families = set()
    seen_channels = set()
    seen_islands = set()
    for pool in (frontier, remainder):
        for row in pool:
            family = row.get("family")
            channel = row.get("channel")
            island = row.get("quality_diversity_island")
            if len(selected) < limit and (
                island not in seen_islands
                or family not in seen_families
                or channel not in seen_channels
            ):
                selected.append(row)
                seen_families.add(family)
                seen_channels.add(channel)
                seen_islands.add(island)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for row in frontier + remainder:
            if row not in selected:
                selected.append(row)
            if len(selected) >= limit:
                break
    return selected[:limit]


def round_candidate_budget(max_candidates, evaluated_count, iteration, iterations):
    remaining = max(0, int(max_candidates) - int(evaluated_count))
    rounds_left = max(1, int(iterations) - int(iteration))
    if remaining <= 0:
        return 0
    if iteration == 0 and rounds_left > 1:
        # Challenge the revalidated champion against one fresh GPT hypothesis first.
        return min(remaining, 2)
    return min(remaining, max(1, int(math.ceil(remaining / rounds_left))))


def candidate_program_fingerprint(candidate):
    return json.dumps(candidate.get("dsl", {}), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def adaptive_candidate_batch(all_candidates, evaluated, pending_mutations, budget, controller):
    if budget <= 0:
        return []
    evaluated_names = set(evaluated)
    evaluated_candidates = [
        candidate for candidate in all_candidates
        if candidate.get("factor_name") in evaluated_names
    ]
    evaluated_programs = {candidate_program_fingerprint(candidate) for candidate in evaluated_candidates}
    evaluated_families = {candidate.get("family") for candidate in evaluated_candidates}
    evaluated_islands = {
        candidate.get("quality_diversity_island") or candidate_static_island(candidate)
        for candidate in evaluated_candidates
    }

    pending = []
    seen_programs = set(evaluated_programs)
    for candidate in pending_mutations:
        fingerprint = candidate_program_fingerprint(candidate)
        if candidate["factor_name"] not in evaluated_names and fingerprint not in seen_programs:
            pending.append(candidate)
            seen_programs.add(fingerprint)

    base = []
    for candidate in all_candidates:
        fingerprint = candidate_program_fingerprint(candidate)
        if (
            candidate["factor_name"] not in evaluated_names
            and candidate.get("channel") != "failure_memory_mutation"
            and fingerprint not in evaluated_programs
        ):
            base.append(candidate)

    if not controller and not pending:
        return base[:budget]

    mutation_budget = min(len(pending), max(1, int(math.ceil(budget * 0.50)))) if pending else 0
    chosen = pending[:mutation_budget]
    chosen_names = {c["factor_name"] for c in chosen}
    chosen_programs = {candidate_program_fingerprint(c) for c in chosen}

    channel_buckets = defaultdict(list)
    for candidate in base:
        fingerprint = candidate_program_fingerprint(candidate)
        if candidate["factor_name"] not in chosen_names and fingerprint not in chosen_programs:
            channel_buckets[candidate.get("channel", "other")].append(candidate)

    channel_targets = {
        "deep_representation": 0.22,
        "raw_causal_grammar_seed": 0.16,
        "nested_orthogonal_complement_seed": 0.14,
        "llm_feedback_mutation": 0.12,
        "residual_monotonic_repair": 0.15,
        "llm_hypothesis_generation": 0.10,
        "llm_fresh_hypothesis_injection": 0.12,
        "llm_hypothesis": 0.10,
        "mcts_tree_seed": 0.06,
        "mcts_feedback_tree_search": 0.08,
        "openfe_feature_search": 0.08,
        "genetic_crossover": 0.08,
        "rl_bandit_policy": 0.05,
        "window_structure_search": 0.04,
        "multiobjective_synergy_ensemble": 0.18,
        "pareto_parent_crossover": 0.06,
    }
    channel_counts = defaultdict(int)
    base_evaluated = 0
    for candidate in evaluated_candidates:
        if candidate.get("channel") != "failure_memory_mutation":
            channel_counts[candidate.get("channel", "other")] += 1
            base_evaluated += 1

    def channel_priority(channel):
        target = channel_targets.get(channel, 0.05)
        deficit = target * max(1, base_evaluated + 1) - channel_counts.get(channel, 0)
        unseen_channel = 0.25 if channel_counts.get(channel, 0) == 0 else 0.0
        posterior = safe_float((controller.get(channel) or {}).get("utility"), 0.0)
        unseen_family = 0.12 if any(c.get("family") not in evaluated_families for c in channel_buckets.get(channel, [])) else 0.0
        return deficit + unseen_channel + unseen_family + 0.08 * max(-1.0, min(1.0, posterior))

    for channel, bucket in channel_buckets.items():
        bucket.sort(
            key=lambda candidate: (
                int((candidate.get("quality_diversity_island") or candidate_static_island(candidate)) not in evaluated_islands),
                int(candidate.get("family") not in evaluated_families),
                -safe_float(candidate.get("max_ast_similarity_prior"), 0.0),
                -safe_float(candidate.get("complexity"), 0.0),
            ),
            reverse=True,
        )
    ordered_channels = sorted(channel_buckets, key=lambda ch: (channel_priority(ch), ch), reverse=True)

    while len(chosen) < budget and any(channel_buckets.values()):
        progressed = False
        for channel in ordered_channels:
            bucket = channel_buckets.get(channel)
            if bucket:
                candidate = bucket.pop(0)
                fingerprint = candidate_program_fingerprint(candidate)
                if fingerprint in chosen_programs:
                    continue
                chosen.append(candidate)
                chosen_programs.add(fingerprint)
                evaluated_families.add(candidate.get("family"))
                progressed = True
                if len(chosen) >= budget:
                    break
        if not progressed:
            break

    if len(chosen) < budget:
        for candidate in pending[mutation_budget:]:
            fingerprint = candidate_program_fingerprint(candidate)
            if candidate["factor_name"] not in chosen_names and fingerprint not in chosen_programs:
                chosen.append(candidate)
                chosen_names.add(candidate["factor_name"])
                chosen_programs.add(fingerprint)
            if len(chosen) >= budget:
                break
    return chosen[:budget]


def diversify_candidate_order(candidates):
    priority = [
        "llm_hypothesis_generation",
        "llm_hypothesis",
        "nested_orthogonal_complement_seed",
        "raw_causal_grammar_seed",
        "llm_feedback_mutation",
        "deep_representation",
        "deep_representation",
        "mcts_tree_seed",
        "mcts_feedback_tree_search",
        "residual_monotonic_repair",
        "openfe_feature_search",
        "genetic_crossover",
        "rl_bandit_policy",
        "window_structure_search",
        "multiobjective_synergy_ensemble",
        "pareto_parent_crossover",
        "failure_memory_mutation",
    ]
    buckets = defaultdict(list)
    for cand in candidates:
        buckets[cand.get("channel", "other")].append(cand)
    ordered = []
    while any(buckets.values()):
        progressed = False
        for channel in priority + sorted(k for k in buckets if k not in priority):
            if buckets.get(channel):
                ordered.append(buckets[channel].pop(0))
                progressed = True
        if not progressed:
            break
    return ordered


def build_candidate_pool(panel, method_cards, memory, budget_per_channel=5, llm_candidates=None):
    candidates = []
    if llm_candidates is None:
        llm_candidates = generate_llm_hypotheses(method_cards, memory, min(4, budget_per_channel))
    deep_candidates = generate_deep_representation_candidates(min(9, max(6, budget_per_channel + 3)))
    raw_causal_candidates = generate_raw_causal_grammar_candidates(min(6, max(4, budget_per_channel + 1)))
    orthogonal_seed_candidates = generate_nested_orthogonal_complement_seed_candidates(1)
    mcts_candidates = generate_mcts_candidates(memory, budget_per_channel)
    openfe_candidates = generate_openfe_candidates(panel, budget_per_channel)
    bandit_candidates = generate_bandit_policy_candidates(memory, min(4, budget_per_channel))
    window_candidates = generate_window_structure_candidates(panel, min(4, budget_per_channel))
    residual_candidates = generate_residual_monotonic_candidates(min(3, budget_per_channel))
    seed_candidates = llm_candidates + orthogonal_seed_candidates + raw_causal_candidates + deep_candidates + mcts_candidates + residual_candidates + openfe_candidates + bandit_candidates + window_candidates
    genetic_candidates = generate_genetic_candidates(seed_candidates, budget_per_channel)
    candidates.extend(llm_candidates)
    candidates.extend(orthogonal_seed_candidates)
    candidates.extend(raw_causal_candidates)
    candidates.extend(deep_candidates)
    candidates.extend(mcts_candidates)
    candidates.extend(residual_candidates)
    candidates.extend(openfe_candidates)
    candidates.extend(genetic_candidates)
    candidates.extend(bandit_candidates)
    candidates.extend(window_candidates)
    dedup = {}
    for cand in candidates:
        dedup[cand["factor_name"]] = cand
    return annotate_ast_similarity(diversify_candidate_order(list(dedup.values())))


def score_candidates(df):
    # Compatibility entry used by framework/backtest/run_v2_models.py.
    tmp = materialize_base_features(df.copy())
    out = {}
    out["momentum_60_minus_reversal_5"] = tmp["base_trend"] + tmp["base_reversal"] + tmp["base_low_crowding"]
    out["trend_low_vol_confirm"] = 0.45 * tmp["base_trend"] + 0.35 * tmp["base_low_crowding"] + 0.20 * tmp["base_moneyflow"]
    out["moneyflow_momentum_20"] = 0.45 * tmp["base_moneyflow"] + 0.35 * tmp["base_trend"] + 0.20 * tmp["base_reversal"]
    out["value_quality_momentum"] = 0.35 * tmp["base_value"] + 0.35 * tmp["base_quality"] + 0.30 * tmp["base_trend"]
    out["dividend_lowvol_quality"] = 0.45 * rank01(tmp.get("dv_ttm", pd.Series(np.nan, index=tmp.index))) + 0.35 * tmp["base_quality"] + 0.20 * tmp["base_low_crowding"]
    out["small_value_profitability"] = 0.35 * tmp["base_size"] + 0.35 * tmp["base_value"] + 0.30 * tmp["base_quality"]
    out["ai_factor_composite_v1"] = 0.25 * tmp["base_trend"] + 0.20 * tmp["base_value"] + 0.20 * tmp["base_quality"] + 0.20 * tmp["base_moneyflow"] + 0.15 * tmp["base_low_crowding"]
    out["ai_factor_factory_v2"] = 0.22 * tmp["base_trend"] + 0.20 * tmp["base_growth"] + 0.20 * tmp["base_value"] + 0.20 * tmp["base_moneyflow"] + 0.18 * tmp["base_size"]
    out["nonlinear_rank_blend_v1"] = tmp["base_quality"] * tmp["base_value"] + tmp["base_trend"] * tmp["base_moneyflow"] - 0.25 * (1 - tmp["base_low_crowding"])
    out["report_quality_value_momentum_v4"] = 0.26 * tmp["base_quality"] + 0.24 * tmp["base_value"] + 0.22 * tmp["base_trend"] + 0.16 * tmp["base_growth"] + 0.12 * tmp["base_low_crowding"]
    out["agent_moneyflow_anti_crowding_v4"] = 0.30 * tmp["base_moneyflow"] + 0.25 * tmp["base_low_crowding"] + 0.20 * tmp["base_reversal"] + 0.15 * tmp["base_trend"] + 0.10 * tmp["base_event_attention"]
    out["deep_rank_interaction_v4"] = 0.35 * (tmp["base_quality"] * tmp["base_value"]) + 0.30 * (tmp["base_trend"] * tmp["base_low_crowding"]) + 0.20 * (tmp["base_growth"] * tmp["base_moneyflow"]) + 0.15 * tmp["base_size"]
    out["defensive_dividend_quality_v4"] = 0.30 * rank01(tmp.get("dv_ttm", pd.Series(np.nan, index=tmp.index))) + 0.28 * tmp["base_quality"] + 0.22 * tmp["base_low_crowding"] + 0.20 * tmp["base_value"]
    out["kline_context_factor_v4"] = 0.32 * tmp["base_kline_context"] + 0.26 * tmp["base_trend"] + 0.18 * tmp["base_moneyflow"] + 0.14 * tmp["base_low_crowding"] + 0.10 * tmp["base_reversal"]
    out["factor_auto_miner_v12"] = 0.28 * tmp["base_quality"] + 0.22 * tmp["base_value"] + 0.18 * tmp["base_moneyflow"] + 0.16 * tmp["base_event_attention"] + 0.16 * tmp["base_low_crowding"] - 0.12 * tmp["base_event_risk"]
    return out


def iter_external_feature_files(path, max_files=80):
    if path.is_dir():
        files = sorted(x for x in path.rglob("*") if x.suffix.lower() in {".csv", ".parquet", ".gzip"})
    elif path.suffix.lower() in {".csv", ".parquet", ".gzip"}:
        files = [path]
    else:
        raise ValueError("feature_path must be a CSV/parquet file or directory")
    usable = []
    for file in files:
        lower = file.name.lower()
        if lower.endswith(".csv") or lower.endswith(".parquet") or lower.endswith(".parquet.gzip"):
            usable.append(file)
        if len(usable) >= max_files:
            break
    return usable


def external_col_name(file, raw_col):
    stem = file.name
    for suffix in [".parquet.gzip", ".parquet", ".csv", ".gzip"]:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in f"{stem}_{raw_col}")
    return f"external_{clean}".lower()


def load_long_external_feature(file, trade_dates, ts_codes):
    df = pd.read_csv(file) if file.suffix.lower() == ".csv" else pd.read_parquet(file)
    lower_cols = {str(c).lower(): c for c in df.columns}
    if "trade_date" not in lower_cols or "ts_code" not in lower_cols:
        return pd.DataFrame(), []
    df = df.rename(columns={lower_cols["trade_date"]: "trade_date", lower_cols["ts_code"]: "ts_code"})
    df["trade_date"] = df["trade_date"].map(normalize_trade_date)
    df["ts_code"] = df["ts_code"].map(normalize_ts_code)
    if trade_dates is not None:
        df = df[df["trade_date"].isin(trade_dates)]
    if ts_codes is not None:
        df = df[df["ts_code"].isin(ts_codes)]
    value_cols = []
    for col in list(df.columns):
        if col in {"trade_date", "ts_code"}:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        if vals.notna().mean() < 0.20:
            continue
        new_col = external_col_name(file, col)
        df[new_col] = vals
        value_cols.append(new_col)
    if not value_cols:
        return pd.DataFrame(), []
    return df[["trade_date", "ts_code"] + value_cols], value_cols


def load_wide_external_feature(file, trade_dates, ts_codes):
    df = pd.read_parquet(file)
    if df.empty:
        return pd.DataFrame(), []
    df = pd.DataFrame(df)
    df.index = [normalize_trade_date(x) for x in df.index]
    if trade_dates is not None:
        df = df.loc[[x for x in df.index if x in trade_dates]]
    if df.empty:
        return pd.DataFrame(), []
    df.columns = [normalize_ts_code(x) for x in df.columns]
    if ts_codes is not None:
        keep = [c for c in df.columns if c in ts_codes]
        if not keep:
            return pd.DataFrame(), []
        df = df[keep]
    df = df.apply(pd.to_numeric, errors="coerce")
    if df.notna().mean().mean() < 0.20:
        return pd.DataFrame(), []
    col = external_col_name(file, "matrix")
    long = df.stack(dropna=False).reset_index()
    long.columns = ["trade_date", "ts_code", col]
    long = long.dropna(subset=[col])
    if long.empty:
        return pd.DataFrame(), []
    return long, [col]


def load_external_features(feature_path, trade_dates=None, ts_codes=None, max_files=80):
    if not feature_path:
        return pd.DataFrame(), []
    path = Path(feature_path)
    if not path.exists():
        raise FileNotFoundError(path)
    files = iter_external_feature_files(path, max_files=max_files)
    trade_dates = set(str(x) for x in trade_dates) if trade_dates is not None else None
    ts_codes = set(normalize_ts_code(x) for x in ts_codes) if ts_codes is not None else None
    frames = []
    for file in files:
        try:
            df, value_cols = load_long_external_feature(file, trade_dates, ts_codes)
            if df.empty and file.suffix.lower() != ".csv":
                df, value_cols = load_wide_external_feature(file, trade_dates, ts_codes)
        except Exception:
            continue
        if value_cols:
            frames.append(df[["trade_date", "ts_code"] + value_cols])
    if not frames:
        return pd.DataFrame(), []
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on=["trade_date", "ts_code"], how="outer")
    value_cols = [c for c in merged.columns if c not in {"trade_date", "ts_code"}]
    return merged, value_cols


def merge_external_features(panel, feature_path, max_files=80):
    external, value_cols = load_external_features(
        feature_path,
        trade_dates=panel["trade_date"].unique(),
        ts_codes=panel["ts_code"].unique(),
        max_files=max_files,
    )
    if external.empty:
        return panel, []
    panel = panel.merge(external, on=["trade_date", "ts_code"], how="left")
    usable = []
    for col in value_cols:
        if col not in panel.columns:
            continue
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
        if panel[col].notna().mean() < 0.20:
            continue
        rank_col = f"{col}_rank"
        panel[rank_col] = panel.groupby("trade_date")[col].rank(pct=True).fillna(0.5)
        usable.append(rank_col)
    return panel, usable


PANEL_CACHE_SCHEMA = "v27_point_in_time_panel_cache_1"


def panel_source_fingerprint(db, universe, start, end, max_months):
    path = Path(db).resolve()
    stat = path.stat()
    payload = {
        "schema": PANEL_CACHE_SCHEMA,
        "database": str(path),
        "database_size": int(stat.st_size),
        "database_mtime_ns": int(stat.st_mtime_ns),
        "universe": str(universe),
        "start": str(start),
        "end": str(end),
        "max_months": int(max_months) if max_months else None,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return payload, hashlib.sha256(encoded).hexdigest()


def build_panel_with_cache(conn, db, universe, start, end, max_months=None):
    cache_root = str(os.environ.get("FACTOR_PANEL_CACHE_DIR", "") or "").strip()
    source, fingerprint = panel_source_fingerprint(
        db, universe, start, end, max_months
    )
    audit = {
        "schema": PANEL_CACHE_SCHEMA,
        "enabled": bool(cache_root),
        "status": "disabled",
        "source_fingerprint": fingerprint,
        "database_size": source["database_size"],
        "database_mtime_ns": source["database_mtime_ns"],
        "universe": str(universe),
        "max_months": source["max_months"],
    }
    if not cache_root:
        panel = build_panel(conn, universe, start, end, max_months=max_months)
        audit.update({
            "rows": int(len(panel)),
            "months": int(panel["trade_date"].nunique()) if not panel.empty else 0,
        })
        return panel, audit

    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    query_payload = {
        key: source[key]
        for key in ["schema", "universe", "start", "end", "max_months"]
    }
    query_key = hashlib.sha256(
        json.dumps(query_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    cache_path = root / f"panel_{clean_name(universe, 20)}_{query_key}.pkl"
    read_started = time.perf_counter()
    if cache_path.exists():
        try:
            cached = pd.read_pickle(cache_path)
            if (
                isinstance(cached, dict)
                and cached.get("schema") == PANEL_CACHE_SCHEMA
                and cached.get("source_fingerprint") == fingerprint
                and isinstance(cached.get("panel"), pd.DataFrame)
            ):
                panel = cached["panel"]
                required = {"trade_date", "ts_code", "label_next_ret"}
                if required.issubset(panel.columns):
                    audit.update({
                        "status": "hit",
                        "read_seconds": round(time.perf_counter() - read_started, 4),
                        "rows": int(len(panel)),
                        "months": int(panel["trade_date"].nunique()) if not panel.empty else 0,
                    })
                    return panel, audit
            audit["status"] = "stale"
        except Exception as exc:
            audit.update({
                "status": "read_error",
                "read_error_type": type(exc).__name__,
            })

    panel = build_panel(conn, universe, start, end, max_months=max_months)
    write_started = time.perf_counter()
    temp_path = cache_path.with_suffix(f".{os.getpid()}.tmp")
    try:
        pd.to_pickle(
            {
                "schema": PANEL_CACHE_SCHEMA,
                "source_fingerprint": fingerprint,
                "panel": panel,
            },
            temp_path,
            protocol=5,
        )
        os.replace(temp_path, cache_path)
        audit.update({
            "status": "miss_written",
            "write_seconds": round(time.perf_counter() - write_started, 4),
            "cache_bytes": int(cache_path.stat().st_size),
        })
    except Exception as exc:
        audit.update({
            "status": "write_error",
            "write_error_type": type(exc).__name__,
        })
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
    audit.update({
        "rows": int(len(panel)),
        "months": int(panel["trade_date"].nunique()) if not panel.empty else 0,
    })
    return panel, audit

def build_panel(conn, universe, start, end, max_months=None):
    fin, fin_dates = load_financial(conn)
    pairs = month_dates(conn, start, end)
    if max_months:
        pairs = select_month_pairs(pairs, max_months)
    panels = []
    for date, next_date in pairs:
        df = fetch_panel(conn, universe, date, next_date, fin, fin_dates)
        if not df.empty:
            panels.append(df)
    if not panels:
        return pd.DataFrame()
    panel = pd.concat(panels, ignore_index=True)
    return materialize_base_features(panel)


def execution_audit(panel):
    delay = pd.to_numeric(panel.get("execution_delay_days"), errors="coerce")
    staleness = pd.to_numeric(panel.get("exit_staleness_days"), errors="coerce")
    executable = pd.to_numeric(panel.get("label_next_ret"), errors="coerce")
    close_to_close = pd.to_numeric(panel.get("label_close_to_close_ret"), errors="coerce")
    comparable = executable.notna() & close_to_close.notna()
    return {
        "policy": "signal_close_then_first_tradable_open_to_last_available_close_before_next_rebalance",
        "one_price_limit_and_suspension_entry_guard": True,
        "rows": int(len(panel)),
        "executable_label_coverage": float(executable.notna().mean()) if len(panel) else 0.0,
        "execution_delay_days_mean": safe_float(delay.mean(), 0.0),
        "execution_delay_days_p95": safe_float(delay.quantile(0.95), 0.0),
        "exit_staleness_days_mean": safe_float(staleness.mean(), 0.0),
        "exit_staleness_days_p95": safe_float(staleness.quantile(0.95), 0.0),
        "mean_return_change_vs_same_close": safe_float(
            (executable[comparable] - close_to_close[comparable]).mean(), 0.0
        ) if comparable.any() else 0.0,
    }


def split_audit(panel):
    out = {}
    for split_name in ["train", "valid", "test", "full"]:
        sub = split_panel(panel, split_name)
        months = sorted(str(x) for x in sub["trade_date"].dropna().unique()) if not sub.empty and "trade_date" in sub else []
        out[split_name] = {
            "rows": int(len(sub)),
            "months": int(len(months)),
            "start": min(months) if months else None,
            "end": max(months) if months else None,
            "stock_count_mean": float(sub.groupby("trade_date")["ts_code"].nunique().mean()) if not sub.empty and "ts_code" in sub else 0.0,
            "label_coverage": float(pd.to_numeric(sub.get("label_next_ret"), errors="coerce").notna().mean()) if not sub.empty else 0.0,
            "execution_delay_days_mean": safe_float(pd.to_numeric(sub.get("execution_delay_days"), errors="coerce").mean(), 0.0) if not sub.empty else 0.0,
            "exit_staleness_days_mean": safe_float(pd.to_numeric(sub.get("exit_staleness_days"), errors="coerce").mean(), 0.0) if not sub.empty else 0.0,
            "label_tail_embargo_periods": LABEL_HORIZON_PERIODS if split_name in {"train", "valid"} else 0,
        }
    return out


def select_nested_portfolio_fraction(panel, factor_name, fractions=(0.10, 0.15, 0.20)):
    train = split_panel(panel, "train")
    dates = sorted(str(x) for x in train["trade_date"].dropna().unique())
    audit = []
    if len(dates) < 6:
        return {"selected_fraction": 0.20, "folds": 0, "candidates": audit, "policy": "default_insufficient_train_periods"}

    date_folds = [list(x) for x in np.array_split(np.array(dates, dtype=object), 2) if len(x)]
    for fraction in fractions:
        fold_rows = []
        for fold_id, fold_dates in enumerate(date_folds, 1):
            sub = train[train["trade_date"].isin(set(fold_dates))]
            bt = top_bottom_backtest(
                sub[["trade_date", "ts_code", factor_name, "label_next_ret"]].dropna(),
                factor_name,
                top_frac=float(fraction),
            )
            ls = bt.get("long_short", {}) if isinstance(bt, dict) else {}
            fold_rows.append({
                "fold": fold_id,
                "periods": ls.get("periods", 0),
                "annual_return": safe_float(ls.get("annual_return"), 0.0),
                "sharpe": safe_float(ls.get("sharpe"), 0.0),
                "max_drawdown": safe_float(ls.get("max_drawdown"), 0.0),
                "turnover": safe_float(bt.get("avg_turnover"), 0.0),
            })
        sharpes = [x["sharpe"] for x in fold_rows]
        returns = [x["annual_return"] for x in fold_rows]
        turnovers = [x["turnover"] for x in fold_rows]
        concentration_penalty = max(0.0, 0.20 - float(fraction)) * 0.20
        score = (
            0.55 * float(np.median(sharpes or [0.0]))
            + 0.25 * float(min(sharpes or [0.0]))
            + 0.20 * float(np.median(returns or [0.0]))
            - 0.08 * float(np.mean(turnovers or [0.0]))
            - concentration_penalty
        )
        audit.append({
            "fraction": float(fraction),
            "robust_train_score": float(score),
            "folds": fold_rows,
        })

    audit.sort(key=lambda x: (x["robust_train_score"], x["fraction"]), reverse=True)
    if not audit:
        weights = [{"fraction": 0.20, "weight": 1.0}]
    else:
        scores = np.asarray([item["robust_train_score"] for item in audit], dtype=float)
        temperature = max(0.15, float(np.std(scores)))
        logits = (scores - float(np.max(scores))) / temperature
        softmax = np.exp(np.clip(logits, -30.0, 0.0))
        softmax = softmax / max(float(softmax.sum()), 1e-12)
        regularized = 0.65 * softmax + 0.35 / len(audit)
        weights = [
            {"fraction": item["fraction"], "weight": float(weight)}
            for item, weight in zip(audit, regularized)
        ]
        for item, weight in zip(audit, regularized):
            item["ensemble_weight"] = float(weight)
    selected_fraction = float(sum(item["fraction"] * item["weight"] for item in weights))
    return {
        "selected_fraction": selected_fraction,
        "weights": weights,
        "folds": len(date_folds),
        "candidates": audit,
        "policy": "nested_train_two_fold_entropy_regularized_tail_width_ensemble",
    }


def evaluate_candidate(panel, candidate, audit):
    if not audit["passed"]:
        empty = {
            s: evaluate_factor(
                pd.DataFrame(columns=["trade_date", "ts_code", candidate["factor_name"], "label_next_ret"]),
                candidate["factor_name"],
            )
            for s in SPLITS
        }
        empty["search"] = empty["valid"].copy()
        empty["portfolio_selection"] = {"selected_fraction": 0.20, "policy": "static_audit_blocked"}
        empty["quick_screen_search"] = {}
        empty["quick_screen"] = {}
        empty["walk_forward_search"] = {}
        empty["walk_forward"] = {}
        empty["purged_kfold_search"] = {}
        empty["purged_kfold"] = {}
        empty["incremental_evidence"] = {}
        empty["regime_evidence"] = {}
        empty["posterior_search_evidence"] = {"posterior_pass": False, "utility": -999.0}
        empty["posterior_final_evidence"] = {"utility": -999.0, "used_for_parent_selection": False}
        return empty

    raw_col, variant = prepare_factor(panel, candidate)
    research = search_panel(panel)
    portfolio_selection = select_nested_portfolio_fraction(panel, candidate["factor_name"])
    top_frac = {"weights": portfolio_selection.get("weights")} if portfolio_selection.get("weights") else safe_float(portfolio_selection.get("selected_fraction"), 0.20)
    metrics = {
        "portfolio_selection": portfolio_selection,
        "search": evaluate_factor(research, candidate["factor_name"], top_frac=top_frac),
        "quick_screen_search": quick_screen_factor(research, candidate["factor_name"]),
        "quick_screen": quick_screen_factor(panel, candidate["factor_name"]),
    }
    for split_name in SPLITS:
        sub = split_panel(panel, split_name)
        metrics[split_name] = evaluate_factor(sub, candidate["factor_name"], top_frac=top_frac)
    metrics.update(annual_metrics_from_full_evaluation(
        panel, candidate["factor_name"], metrics["full"]
    ))
    metrics["incremental_evidence"] = incremental_factor_evidence(panel, candidate["factor_name"])
    metrics["regime_evidence"] = regime_robustness_evidence(panel, metrics)
    metrics["posterior_search_evidence"] = posterior_search_evidence(
        metrics,
        complexity=candidate.get("complexity", 0),
    )
    metrics["posterior_final_evidence"] = posterior_final_reporting_evidence(metrics)
    raw_panel = panel[["trade_date", "ts_code", "label_next_ret", raw_col]].rename(columns={raw_col: candidate["factor_name"]})
    metrics["raw_full"] = evaluate_factor(raw_panel, candidate["factor_name"], top_frac=top_frac)
    metrics["walk_forward_search"] = frozen_oos_walk_forward_metrics(research, candidate["factor_name"], top_frac=top_frac)
    metrics["walk_forward"] = frozen_oos_walk_forward_metrics(panel, candidate["factor_name"], top_frac=top_frac)
    metrics["purged_kfold_search"] = frozen_oos_block_metrics(research, candidate["factor_name"], top_frac=top_frac)
    metrics["purged_kfold"] = frozen_oos_block_metrics(panel, candidate["factor_name"], top_frac=top_frac)
    metrics["attribution"] = attribution_slices(panel, candidate["factor_name"])
    metrics["preprocess"] = {"variant": variant, "raw_col": raw_col}
    return metrics


def persist_results(conn, run_id, universe, panel, candidates, metrics, leaderboard):
    conn.execute("delete from factor_test_result where run_id=? and universe=?", (run_id, universe))
    conn.execute("delete from v3_factor_validation where run_id=? and universe=?", (run_id, universe))
    conn.execute("delete from v3_factor_candidate_registry where run_id=?", (run_id,))
    conn.execute("delete from factor_value_daily where source_agent=?", (SOURCE_AGENT,))
    rows = []
    validation_rows = []
    candidate_rows = []
    for cand in candidates:
        factor = cand["factor_name"]
        if factor not in metrics:
            continue
        for split_name, m in metrics[factor].items():
            if not isinstance(m, dict) or split_name in {"preprocess", "raw_full"}:
                continue
            pass_value = pass_flag(metrics[factor]) if split_name == "full" else None
            row = (
                run_id, universe, factor, split_name,
                m.get("rank_ic", 0.0), m.get("icir", 0.0), m.get("group_spread", 0.0),
                m.get("turnover", 0.0), m.get("coverage", 0.0), pass_value,
                json.dumps({"candidate": cand, "metrics": m}, ensure_ascii=False),
            )
            rows.append(row)
            validation_rows.append(row)
        lb = next((x for x in leaderboard if x["factor"] == factor), {})
        candidate_rows.append((
            run_id,
            factor,
            cand["family"],
            json.dumps(cand["dsl"], ensure_ascii=False),
            SOURCE_AGENT,
            int(metrics[factor].get("train", {}).get("rank_ic", 0.0) >= DEFAULT_GATES["train_rank_ic_min"]),
            int(metrics[factor].get("valid", {}).get("rank_ic", 0.0) >= DEFAULT_GATES["valid_rank_ic_min"]),
            int(metrics[factor].get("test", {}).get("rank_ic", 0.0) >= DEFAULT_GATES["test_rank_ic_min"]),
            int(metrics[factor].get("full", {}).get("coverage", 0.0) >= DEFAULT_GATES["coverage_min"]),
            "accepted" if lb.get("accepted") else "rejected",
            json.dumps({
                "chinese_name": cand["chinese_name"],
                "channel": cand["channel"],
                "hypothesis": cand["hypothesis"],
                "implementation_audit": cand.get("implementation_audit"),
                "research_memory": {
                    "selection_score": lb.get("selection_score"),
                    "search_risk_adjusted_selection_score": lb.get("search_risk_adjusted_selection_score"),
                    "search_pbo_proxy": lb.get("search_pbo_proxy"),
                    "search_cscv_pbo_when_selected": lb.get("search_cscv_pbo_when_selected"),
                    "search_max_abs_corr": lb.get("search_max_abs_corr_to_other_factor"),
                    "search_diagnosis": lb.get("search_diagnosis"),
                    "search_stage_pass": lb.get("search_stage_pass"),
                    "search_reliability_pass": lb.get("search_reliability_pass"),
                },
                "final_audit": {
                    "reward": lb.get("reward"),
                    "pbo_proxy": lb.get("pbo_proxy"),
                    "diagnosis": lb.get("diagnosis"),
                    "lifecycle_state": lb.get("lifecycle_state"),
                    "lifecycle_production_ready": lb.get("lifecycle_production_ready"),
                },
            }, ensure_ascii=False),
        ))
    if rows:
        conn.executemany(
            """
            insert or replace into factor_test_result
            (run_id, universe, factor_name, split_name, rank_ic, icir, group_spread, turnover, coverage, pass_flag, message)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.executemany(
            """
            insert or replace into v3_factor_validation
            (run_id, universe, factor_name, split_name, rank_ic, icir, group_spread, turnover, coverage, pass_flag, message)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            validation_rows,
        )
    if candidate_rows:
        conn.executemany(
            """
            insert or replace into v3_factor_candidate_registry
            (run_id, factor_name, factor_group, expression, source_agent, train_pass, valid_pass, test_pass, full_pass, status, notes)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            candidate_rows,
        )
    value_rows = []
    sample = panel.sort_values(["trade_date", "ts_code"]).groupby("trade_date").head(300)
    for cand in candidates:
        factor = cand["factor_name"]
        if factor not in sample.columns:
            continue
        for r in sample[["trade_date", "ts_code", factor]].dropna().itertuples(index=False):
            value_rows.append((r.trade_date, r.ts_code, factor, float(getattr(r, factor)), cand["family"], SOURCE_AGENT))
    if value_rows:
        conn.executemany(
            """
            insert or replace into factor_value_daily
            (trade_date, ts_code, factor_name, factor_value, factor_group, source_agent)
            values (?, ?, ?, ?, ?, ?)
            """,
            value_rows,
        )
    conn.commit()


def mine(
    db,
    universe,
    start,
    end,
    out_dir,
    stop_on_pass=False,
    max_months=None,
    feature_path=None,
    feature_limit=80,
    max_redundancy=0.82,
    min_test_rank_ic=0.02,
    iterations=2,
    budget_per_channel=5,
    persist=True,
    target_accepted=1,
    max_candidates=20,
    memory_db=None,
    memory_scope="default",
):
    mine_started = time.perf_counter()
    gates = dict(DEFAULT_GATES)
    gates["max_redundancy"] = max_redundancy
    gates["test_rank_ic_min"] = min_test_rank_ic
    conn = sqlite3.connect(db)
    rows, min_date, max_date = table_count(conn, universe)
    first_trade, last_trade = required_trade_bounds(conn, start, end)
    if not rows or min_date > first_trade or max_date < last_trade:
        payload = {
            "status": "blocked",
            "reason": f"universe {universe} coverage {min_date} to {max_date}; required first/last trade {first_trade} to {last_trade}",
            "universe": universe,
            "database": str(db),
        }
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        Path(out_dir, f"factor_mining_{universe}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        conn.close()
        return payload
    method_cards = load_methodology_cards(conn)
    warehouse_memory = load_agent_memory(conn, universe)
    isolated_memory, memory_load_audit = load_isolated_agent_memory(memory_db, universe, memory_scope)
    memory = isolated_memory if isolated_memory is not None else warehouse_memory
    data_source_audit = audit_external_data_sources(conn, db)
    llm_started = time.perf_counter()
    initial_llm_budget = min(4, max(1, int(budget_per_channel)))
    if stop_on_pass:
        initial_llm_budget = min(initial_llm_budget, max(1, min(2, int(target_accepted))))
    llm_candidates = generate_llm_hypotheses(method_cards, memory, initial_llm_budget)
    llm_generation_seconds = time.perf_counter() - llm_started
    initial_llm_generation_audit = {"status": "not_recorded"}
    for llm_candidate in llm_candidates:
        for lineage_event in llm_candidate.get("lineage") or []:
            generation_audit = lineage_event.get("generation_audit") if isinstance(lineage_event, dict) else None
            if isinstance(generation_audit, dict):
                initial_llm_generation_audit = generation_audit
                break
        if initial_llm_generation_audit.get("status") != "not_recorded":
            break
    panel_started = time.perf_counter()
    panel, panel_cache_audit = build_panel_with_cache(
        conn, db, universe, start, end, max_months=max_months
    )
    if panel.empty:
        raise RuntimeError(f"No factor panel built for {universe}")
    panel, external_factor_names = merge_external_features(panel, feature_path, max_files=feature_limit)
    panel_build_seconds = time.perf_counter() - panel_started
    _DEEP_OPERATOR_VALUE_CACHE.clear()
    _RESEARCH_EVIDENCE_CACHE.clear()
    for col in external_factor_names:
        panel[f"base_external_{clean_name(col, 40)}"] = panel[col]
    run_id = f"factor_mining_agent_v27_{universe}_{start}_{end}"
    if max_months:
        run_id += f"_m{max_months}"
    candidate_pool_started = time.perf_counter()
    deferred_full_pool = bool(stop_on_pass and int(max_candidates) >= 2)
    full_pool_expanded = False
    expanded_pool_build_seconds = 0.0
    if deferred_full_pool:
        challenge_candidates = (
            list(llm_candidates)
            + generate_nested_orthogonal_complement_seed_candidates(1)
        )
        challenge_by_name = {
            candidate["factor_name"]: candidate for candidate in challenge_candidates
        }
        all_candidates = annotate_ast_similarity(
            diversify_candidate_order(list(challenge_by_name.values()))
        )
    else:
        all_candidates = build_candidate_pool(
            panel,
            method_cards,
            memory,
            budget_per_channel=budget_per_channel,
            llm_candidates=llm_candidates,
        )
    initial_pool_build_seconds = time.perf_counter() - candidate_pool_started
    evaluated = {}
    metrics = {}
    audits = {}
    iteration_log = []
    candidate_runtime_audit = []
    accepted = []
    pending_mutations = []
    search_controller = {}
    leaderboard = []
    production_streaks = {}
    stop_reason = "iteration_limit_reached"

    for it in range(max(1, iterations)):
        remaining_budget = max(0, int(max_candidates) - len(evaluated))
        if remaining_budget <= 0:
            stop_reason = "candidate_limit_reached"
            break
        batch_budget = round_candidate_budget(max_candidates, len(evaluated), it, max(1, iterations))
        current = adaptive_candidate_batch(
            all_candidates,
            evaluated,
            pending_mutations,
            batch_budget,
            search_controller,
        )
        selected_names = {c["factor_name"] for c in current}
        pending_mutations = [c for c in pending_mutations if c["factor_name"] not in selected_names]
        if not current:
            stop_reason = "candidate_pool_exhausted"
            break

        for cand in current:
            candidate_started = time.perf_counter()
            audit = static_audit(panel, cand)
            audits[cand["factor_name"]] = audit
            try:
                metrics[cand["factor_name"]] = evaluate_candidate(panel, cand, audit)
            except Exception as exc:
                audits[cand["factor_name"]] = {
                    "passed": False,
                    "issues": [{
                        "severity": "blocked",
                        "code": "compile_or_eval_error",
                        "message": str(exc),
                    }],
                }
                metrics[cand["factor_name"]] = {
                    "evaluation_error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                }
            evaluated[cand["factor_name"]] = cand
            candidate_runtime_audit.append({
                "factor": cand["factor_name"],
                "channel": cand.get("channel"),
                "iteration": it + 1,
                "elapsed_seconds": round(time.perf_counter() - candidate_started, 4),
                "evaluation_failed": bool(metrics[cand["factor_name"]].get("evaluation_error")),
            })

        leaderboard = build_frontier_leaderboard(panel, evaluated, metrics, audits, gates)
        search_ranked = sorted(leaderboard, key=leaderboard_search_key, reverse=True)
        search_controller = build_search_channel_controller(leaderboard)
        accepted = [x for x in leaderboard if x["accepted"]]
        production_ready = [
            x for x in accepted if x.get("lifecycle_production_ready", False)
        ]
        current_production_factors = {row["factor"] for row in production_ready}
        production_streaks = {
            factor: (production_streaks.get(factor, 0) + 1 if factor in current_production_factors else 0)
            for factor in set(production_streaks) | current_production_factors
        }
        confirmed_production = [
            row for row in production_ready if production_streaks.get(row["factor"], 0) >= 2
        ]
        strict_target_rows = [
            row for row in accepted
            if row.get("search_reliability_pass", False)
            and row.get("reliability_pass", False)
        ]
        strict_target_reached = (
            stop_on_pass
            and len(strict_target_rows) >= max(1, int(target_accepted))
        )

        parent_rows = []
        generated_mutations = []
        llm_feedback_audit = {"status": "skipped", "reason": "no_next_round"}
        llm_search_mode = "skipped"
        mcts_expansion_audit = {"status": "skipped", "reason": "no_next_round"}
        synergy_expansion_audit = {"status": "skipped", "reason": "no_next_round"}
        can_continue = (
            it + 1 < max(1, iterations)
            and len(evaluated) < int(max_candidates)
            and not strict_target_reached
        )
        if can_continue:
            if deferred_full_pool:
                expanded_pool_started = time.perf_counter()
                all_candidates = build_candidate_pool(
                    panel,
                    method_cards,
                    memory,
                    budget_per_channel=budget_per_channel,
                    llm_candidates=llm_candidates,
                )
                expanded_pool_build_seconds += time.perf_counter() - expanded_pool_started
                deferred_full_pool = False
                full_pool_expanded = True
            next_budget = round_candidate_budget(max_candidates, len(evaluated), it + 1, max(1, iterations))
            parent_rows = select_search_parents(
                search_ranked,
                max(1, min(next_budget, budget_per_channel, 5)),
            )
            known_names = {c["factor_name"] for c in all_candidates}
            known_programs = {candidate_program_fingerprint(c) for c in all_candidates}
            if len(parent_rows) >= 2:
                try:
                    synergy_candidate = build_multiobjective_synergy_candidate(
                        parent_rows,
                        evaluated,
                        panel,
                        it + 1,
                    )
                    if synergy_candidate is not None:
                        synergy_lineage = (synergy_candidate.get("lineage") or [{}])[-1]
                        synergy_expansion_audit = {
                            "status": "generated",
                            "factor": synergy_candidate.get("factor_name"),
                            "parents": synergy_lineage.get("synergy_parent_factors", []),
                            "weights": synergy_lineage.get("synergy_weights", {}),
                            "feature_selection_audit": synergy_lineage.get("feature_selection_audit", {}),
                            "test_metrics_used": False,
                        }
                        synergy_fingerprint = candidate_program_fingerprint(synergy_candidate)
                        if (
                            synergy_candidate["factor_name"] not in known_names
                            and synergy_fingerprint not in known_programs
                        ):
                            generated_mutations.append(synergy_candidate)
                            all_candidates.append(synergy_candidate)
                            known_names.add(synergy_candidate["factor_name"])
                            known_programs.add(synergy_fingerprint)
                    else:
                        synergy_expansion_audit = {
                            "status": "skipped",
                            "reason": "insufficient_eligible_complementary_parents",
                        }
                except Exception as exc:
                    synergy_expansion_audit = {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                        "test_metrics_used": False,
                    }
            if it % 2 == 0:
                llm_search_mode = "diagnosis_conditioned_feedback_mutation"
                llm_feedback, llm_feedback_audit = generate_llm_feedback_mutations(
                    parent_rows,
                    evaluated,
                    memory,
                    it + 1,
                    budget=min(1, next_budget),
                )
            else:
                llm_search_mode = "periodic_fresh_hypothesis_injection"
                llm_feedback, llm_feedback_audit = generate_llm_fresh_hypothesis(
                    method_cards,
                    memory,
                    leaderboard,
                    all_candidates,
                    it + 1,
                    budget=min(1, next_budget),
                )
            for feedback_candidate in llm_feedback:
                feedback_fingerprint = candidate_program_fingerprint(feedback_candidate)
                if feedback_candidate["factor_name"] not in known_names and feedback_fingerprint not in known_programs:
                    generated_mutations.append(feedback_candidate)
                    all_candidates.append(feedback_candidate)
                    known_names.add(feedback_candidate["factor_name"])
                    known_programs.add(feedback_fingerprint)
            mcts_expansion = build_mcts_feedback_expansion(search_ranked, evaluated, it + 1, panel=panel)
            if mcts_expansion is not None:
                mcts_lineage = (mcts_expansion.get("lineage") or [{}])[-1]
                mcts_expansion_audit = {
                    "status": "generated",
                    "factor": mcts_expansion.get("factor_name"),
                    **mcts_lineage,
                }
                mcts_fingerprint = candidate_program_fingerprint(mcts_expansion)
                if mcts_expansion["factor_name"] not in known_names and mcts_fingerprint not in known_programs:
                    generated_mutations.append(mcts_expansion)
                    all_candidates.append(mcts_expansion)
                    known_names.add(mcts_expansion["factor_name"])
                    known_programs.add(mcts_fingerprint)
            if len(parent_rows) >= 2:
                left = evaluated[parent_rows[0]["factor"]]
                right = evaluated[parent_rows[1]["factor"]]
                crossed = crossover_search_parents(
                    left,
                    right,
                    it + 1,
                    left_evidence=parent_rows[0],
                    right_evidence=parent_rows[1],
                )
                crossed_fingerprint = candidate_program_fingerprint(crossed)
                if crossed["factor_name"] not in known_names and crossed_fingerprint not in known_programs:
                    generated_mutations.append(crossed)
                    all_candidates.append(crossed)
                    known_names.add(crossed["factor_name"])
                    known_programs.add(crossed_fingerprint)
            for idx, row in enumerate(parent_rows, 1):
                parent = evaluated[row["factor"]]
                mutated = mutate_candidate(
                    parent,
                    row.get("search_diagnosis") or {"failure_type": "search_stage_passed"},
                    idx + it * 10,
                )
                fingerprint = candidate_program_fingerprint(mutated)
                if mutated["factor_name"] not in known_names and fingerprint not in known_programs:
                    generated_mutations.append(mutated)
                    all_candidates.append(mutated)
                    known_names.add(mutated["factor_name"])
                    known_programs.add(fingerprint)
            pending_mutations.extend(generated_mutations)

        iteration_log.append({
            "iteration": it + 1,
            "round_budget": batch_budget,
            "evaluated_now": len(current),
            "evaluated_total": len(evaluated),
            "evaluated_channels": [c.get("channel") for c in current],
            "accepted_total": len(accepted),
            "production_ready_total": len(production_ready),
            "production_confirmed_total": len(confirmed_production),
            "strict_target_reached": strict_target_reached,
            "strict_target_factor_count": len(strict_target_rows),
            "production_confirmation_streaks": dict(production_streaks),
            "search_parent_factors": [row.get("factor") for row in parent_rows],
            "generated_mutations": [c.get("factor_name") for c in generated_mutations],
            "llm_feedback_audit": llm_feedback_audit,
            "llm_search_mode": llm_search_mode,
            "mcts_expansion_audit": mcts_expansion_audit,
            "synergy_expansion_audit": synergy_expansion_audit,
            "quality_diversity_islands_evaluated": len(set(
                row.get("quality_diversity_island") for row in leaderboard
            )),
            "channel_controller": search_controller,
            "best": search_ranked[:5],
        })

        if strict_target_reached:
            stop_reason = "strict_research_target_reached"
            break
        if len(evaluated) >= int(max_candidates):
            stop_reason = "candidate_limit_reached"
            break

    leaderboard = build_frontier_leaderboard(panel, evaluated, metrics, audits, gates)
    for idx, row in enumerate(leaderboard, 1):
        row["index_label"] = f"因子{idx}"
    candidates = [evaluated[x["factor"]] for x in leaderboard]
    if persist:
        persist_results(conn, run_id, universe, panel, candidates, metrics, leaderboard)
    memory_write_audit = persist_isolated_agent_memory(
        memory_db, memory_scope, universe, run_id, candidates, leaderboard
    )
    conn.close()
    accepted = [x for x in leaderboard if x["accepted"]]
    production_ready = [
        x for x in accepted if x.get("lifecycle_production_ready", False)
    ]
    confirmed_production = [
        row for row in production_ready if production_streaks.get(row["factor"], 0) >= 2
    ]
    memory_update = build_memory_update(leaderboard)
    reliability_head = leaderboard[0] if leaderboard else {}
    payload = {
        "status": "ready",
        "model_version": "v27_strict_champion_challenge",
        "run_id": run_id,
        "universe": universe,
        "database": str(db),
        "rows": int(len(panel)),
        "months": int(panel["trade_date"].nunique()),
        "methodology_cards_used": len(method_cards),
        "initial_llm_candidate_count": len(llm_candidates),
        "initial_llm_generation_audit": initial_llm_generation_audit,
        "search_strategy": {
            "mode": "strict_champion_challenge_then_diagnosis_conditioned_expansion",
            "initial_challenge_size": min(2, int(max_candidates)),
            "initial_llm_budget": int(initial_llm_budget),
            "strict_gates_unchanged": True,
            "failure_expands_until_candidate_or_iteration_limit": True,
            "deferred_full_pool": True,
            "full_pool_expanded": bool(full_pool_expanded),
        },
        "runtime_audit": {
            "llm_generation_seconds": round(llm_generation_seconds, 4),
            "panel_build_seconds": round(panel_build_seconds, 4),
            "panel_cache": panel_cache_audit,
            "initial_pool_build_seconds": round(initial_pool_build_seconds, 4),
            "expanded_pool_build_seconds": round(expanded_pool_build_seconds, 4),
            "candidate_evaluations": candidate_runtime_audit,
            "candidate_evaluation_seconds": round(sum(
                item["elapsed_seconds"] for item in candidate_runtime_audit
            ), 4),
            "total_seconds": round(time.perf_counter() - mine_started, 4),
        },
        "memory_store_audit": {"load": memory_load_audit, "write": memory_write_audit},
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "production_ready_count": len(production_ready),
        "production_confirmed_count": len(confirmed_production),
        "target_accepted": int(target_accepted),
        "max_candidates": int(max_candidates),
        "stop_reason": stop_reason,
        "persisted_to_database": bool(persist),
        "external_feature_path": str(feature_path) if feature_path else None,
        "external_factor_count": len(external_factor_names),
        "data_source_audit": data_source_audit,
        "panel_cache_audit": panel_cache_audit,
        "execution_audit": execution_audit(panel),
        "split_audit": split_audit(panel),
        "search_channels": sorted(set(x["channel"] for x in candidates)),
        "flow_steps": FLOW_STEPS,
        "deep_method_cards": DEEP_METHOD_CARDS,
        "data_space": DATA_SPACE,
        "operator_space": OPERATOR_SPACE,
        "constraint_space": CONSTRAINT_SPACE,
        "memory_prior": {
            "scope": memory.get("memory_scope"),
            "test_fields_loaded": memory.get("test_fields_loaded", False),
            "records_loaded": int(memory.get("memory_records", 0)),
            "accepted_patterns_loaded": len(memory.get("accepted_patterns", [])),
            "failed_patterns_loaded": len(memory.get("failed_patterns", [])),
            "successful_subexpression_motifs_loaded": len(memory.get("successful_subexpression_motifs", [])),
            "failed_subexpression_motifs_loaded": len(memory.get("failed_subexpression_motifs", [])),
            "failure_counts": memory.get("failure_counts", {}),
        },
        "memory_update": memory_update,
        "search_controller": search_controller,
        "validation_policy": {
            "search_sample": "train_plus_validation_with_one_period_test_boundary_embargo",
            "final_test_role": "final_acceptance_lifecycle_and_reporting_only",
            "parent_selection_uses_test": False,
            "mutation_uses_test": False,
            "train_and_validation_label_embargo_periods": LABEL_HORIZON_PERIODS,
            "quick_screen_search_scope": "embargoed_train_plus_validation",
            "walk_forward_search_scope": "frozen_train_model_strict_post_train_validation_years",
            "purged_kfold_search_scope": "frozen_train_model_strict_post_train_validation_blocks",
            "portfolio_fraction_selection_scope": "embargoed_train_two_fold_entropy_regularized_multi_width_ensemble",
            "factor_ensemble_selection_scope": "embargoed_train_validation_four_fold_multiobjective_pareto_no_test",
            "factor_ensemble_objectives": ["posterior_rank_ic", "cost_after_long_short_return", "sharpe", "drawdown", "turnover", "low_correlation", "weight_diversification"],
            "factor_ensemble_selection_rule": "pareto_frontier_then_weakest_objective_percentile_and_geometric_rank",
            "execution_label": "first_tradable_open_after_signal_to_last_available_close_before_next_rebalance",
            "transaction_cost_policy": "long_side_cost_for_long_only_and_both_side_cost_for_long_short",
            "early_stop_confirmation": "strict_research_acceptance_target_after_champion_gpt_challenge; no gate relaxation",
            "lifecycle_state_method": "empirical_bayes_recent6_vs_prior24_joint_return_ic_change_detection",
            "final_multiple_testing_controls": ["candidate_curve_effective_trials", "internal_variant_trials", "deflated_sharpe", "PBO_proxy", "CSCV_PBO"],
            "incrementality_method": "train_frozen_baseline_dual_residual_rank_ic",
            "synergy_method": "train_frozen_baseline_plus_candidate_marginal_rank_ic",
            "regime_method": "train_defined_trend_risk_states_with_empirical_bayes_state_posteriors",
            "project_level_test_reuse_risk": "2023-2026 test history has been observed during framework development and is not a pristine prospective holdout",
            "prospective_shadow_period_required_before_live_capital": True,
            "search_utility": "entropic_soft_min_posterior_odds_with_coverage_complexity_and_novelty_priors",
            "tree_search_policy": "lineage_backpropagated_posterior_mcts_with_adaptive_uct_and_progressive_widening",
        },
        "search_reliability_audit": {
            "search_pbo_proxy_best": reliability_head.get("search_pbo_proxy"),
            "search_cscv_available": reliability_head.get("search_cscv_available"),
            "search_cscv_pbo": reliability_head.get("search_cscv_pbo"),
            "test_cscv_available": reliability_head.get("test_cscv_available"),
            "test_cscv_pbo": reliability_head.get("test_cscv_pbo"),
            "effective_multiple_testing_trials": reliability_head.get("effective_multiple_testing_trials"),
        },
        "search_objective": {
            "goal": "找到至少一个在训练验证后验、双残差增量、组合边际贡献、跨状态稳健和最终回测中均成立的原创因子",
            "priority": "先确认独立增量与组合贡献，再追求跨状态稳定、成本后收益和低冗余；测试只做最终报告",
            "stop_rule": "stop only after at least 12 or 60 percent of candidate budget and the same lifecycle-healthy factor remains accepted for two consecutive search rounds",
        },
        "applied_methodology": [
            "broker_and_paper_method_card_learning",
            "initial_llm_hypothesis_generation",
            "alternating_llm_diagnosis_mutation_and_fresh_hypothesis_injection",
            "raw_point_in_time_causal_grammar_seed_channel",
            "quality_diversity_method_islands",
            "posterior_mcts_lineage_backpropagation_and_progressive_widening",
            "train_frozen_baseline_dual_residual_incremental_ic",
            "train_frozen_downstream_combination_marginal_contribution",
            "train_defined_regime_risk_sensitive_posterior",
            "entropic_soft_min_noncompensatory_judge",
            "grammar_tree_seed",
            "train_validation_ucb_feedback_tree_search",
            "train_validation_enumerated_openfe_interactions",
            "real_momentum_reversal_window_structure_search",
            "nested_train_validation_multiobjective_synergy_ensemble",
            "revalidated_nested_train_orthogonal_complement_seed_with_historical_trial_accounting",
            "nsga2_style_pareto_factor_set_selection",
            "cost_aware_noncompensatory_ensemble_weight_search",
            "embargoed_train_validation_cscv_beta_binomial_synergy_selection",
            "pareto_parent_selection_and_synergy_crossover",
            "genetic_crossover",
            "search_memory_beta_ucb_bandit",
            "sklearn_mlp_date_balanced_train_only_ensemble",
            "attention_inspired_train_ic_router",
            "nonlinear_bottleneck_inspired_operator",
            "graph_hidden_concept_residual_operator",
            "causal_regime_mixture_operator",
            "tabnet_inspired_sparse_gate_operator",
            "tcn_inspired_fixed_causal_convolution_operator",
            "contrastive_inspired_regime_router",
            "one_period_label_boundary_embargo",
            "strict_frozen_model_post_train_oos_years_and_blocks",
            "cscv_probability_of_backtest_overfitting",
            "effective_trials_and_deflated_sharpe",
            "sealed_test_and_cross_run_memory_isolation",
            "first_tradable_open_execution_label",
            "long_and_short_side_transaction_costs",
            "winsor_zscore_size_industry_neutralization",
            "nested_embargoed_train_entropy_regularized_tail_width_ensemble",
            "correlation_cluster_frontier_representative",
            "alpha_lifecycle_recent_decay_diagnostics",
            "two_round_sequential_early_stop_confirmation",
            "failure_attribution_and_directed_mutation",
        ],
        "gates": gates,
        "iteration_log": iteration_log,
        "leaderboard": leaderboard,
        "accepted_factors": accepted,
        "production_ready_factors": production_ready,
        "production_confirmed_factors": confirmed_production,
        "llm_adapter": {
            "model": DEFAULT_MODEL_NAME,
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "endpoint": ai_router_endpoint(),
            "api_key_configured": bool(ai_router_key()),
            "mode": "required_api_no_fallback" if os.environ.get("FACTOR_REQUIRE_GPT", "0") == "1" else "api_if_configured_else_explicit_test_fallback",
            "feedback_mutation_enabled": True,
            "initial_generation_failure_blocks_formal_run": os.environ.get("FACTOR_REQUIRE_GPT", "0") == "1",
            "feedback_failure_blocks_formal_run": False,
            "feedback_failure_policy": "strict_reject_and_audit_then_continue_other_search_channels",
            "secret_policy": "keys are read from server environment only and are never written to browser or result outputs",
        },
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"factor_mining_{universe}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(leaderboard).to_csv(out / f"factor_leaderboard_{universe}.csv", index=False, encoding="utf-8-sig")
    method_path = out / f"methodology_cards_{universe}.json"
    method_path.write_text(json.dumps(method_cards, ensure_ascii=False, indent=2), encoding="utf-8")
    _DEEP_OPERATOR_VALUE_CACHE.pop(id(panel), None)
    _RESEARCH_EVIDENCE_CACHE.pop(id(panel), None)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--universe", default="ALL_A")
    parser.add_argument("--start", default="20120101")
    parser.add_argument("--end", default="20260630")
    parser.add_argument("--out-dir", default=str(DEFAULT_PROJECT_ROOT / "output" / "llm_factor_mining"))
    parser.add_argument("--stop-on-pass", action="store_true")
    parser.add_argument("--max-months", type=int, default=None)
    parser.add_argument("--feature-path", default=None)
    parser.add_argument("--feature-limit", type=int, default=80)
    parser.add_argument("--max-redundancy", type=float, default=0.82)
    parser.add_argument("--min-test-rank-ic", type=float, default=0.02)
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--budget-per-channel", type=int, default=5)
    parser.add_argument("--target-accepted", type=int, default=1)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--memory-db", default=None)
    parser.add_argument("--memory-scope", default="default")
    args = parser.parse_args()
    payload = mine(
        args.db,
        args.universe,
        args.start,
        args.end,
        args.out_dir,
        args.stop_on_pass,
        args.max_months,
        args.feature_path,
        args.feature_limit,
        args.max_redundancy,
        args.min_test_rank_ic,
        args.iterations,
        args.budget_per_channel,
        not args.no_persist,
        args.target_accepted,
        args.max_candidates,
        args.memory_db,
        args.memory_scope,
    )
    print(json.dumps({
        k: payload.get(k)
        for k in ["status", "run_id", "universe", "rows", "months", "candidate_count", "accepted_count"]
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
