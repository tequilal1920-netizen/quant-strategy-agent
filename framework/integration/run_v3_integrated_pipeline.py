import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


START_DATE = "20120101"
END_DATE = "20260630"
TRAIN_WINDOW = "20120101-20201231"
VALID_WINDOW = "20210101-20221231"
TEST_WINDOW = "20230101-20260630"
TARGET_ANNUAL_RETURN = 0.20
TARGET_SHARPE = 1.50
V2_RUN_ID = "v2_formal_models"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]

CORE_TABLES = [
    "source_manifest",
    "trade_calendar",
    "security_master",
    "stock_ohlcv_daily",
    "stock_valuation_daily",
    "stock_moneyflow_daily",
    "financial_report_visible",
    "sw_l1_industry_daily",
    "index_constituent_period",
    "etf_master",
    "etf_ohlcv_daily",
    "macro_monthly",
    "news_event_daily",
    "lhb_daily",
    "broker_report_index",
    "factor_value_daily",
    "factor_test_result",
    "model_signal_daily",
    "portfolio_target_daily",
    "backtest_nav",
    "metrics_by_split_year",
]

MODULES = {
    "weekly_report": {
        "model": "weekly_research_agent",
        "keywords": ["周报", "复盘", "热点", "宏观", "龙虎榜", "事件", "AI周观察"],
        "component": "宏观-事件-行业-龙虎榜周报生成",
    },
    "asset_allocation": {
        "model": "asset_allocation_etf_v3",
        "keywords": ["资产配置", "全天候", "BL", "风险再平价", "投资时钟", "货币信用", "股债轮动"],
        "component": "宏观周期状态机、风险预算和 ETF 映射",
    },
    "style_rotation": {
        "model": "style_rotation_v3",
        "keywords": ["风格", "成长", "价值", "红利", "因子配置", "ETF"],
        "component": "成长/价值/红利风格指标卡和轮动权重",
    },
    "industry_rotation": {
        "model": "industry_rotation_v3",
        "keywords": ["行业轮动", "行业配置", "行业景气", "Agent赋能开发行业轮动策略", "ETF策略周报"],
        "component": "申万一级行业景气、资金、估值、技术、事件综合打分",
    },
    "kline_technical": {
        "model": "kline_skill_agent_v3",
        "keywords": ["技术分析", "K线", "GPT-Kline", "技术面", "AI技术分析", "趋势"],
        "component": "K线形态 skill 库和单股深度分析",
    },
    "factor_mining": {
        "model": "factor_factory_agent_v3",
        "keywords": ["因子", "深度学习因子", "机器学习", "Transformer", "量化投资", "Meta_Master"],
        "component": "人工因子、非线性表达式、深度学习因子候选检验",
    },
    "fundamental": {
        "model": "fundamental_research_agent_v3",
        "keywords": ["基本面", "财报", "ESG", "AI选股系统", "盈利", "质量"],
        "component": "财报可见日、盈利质量、估值和行业卡片",
    },
    "portfolio_optimizer": {
        "model": "portfolio_optimizer_v3",
        "keywords": ["组合", "有效前沿", "Portfolio", "对冲基金", "交易员", "风险", "优化"],
        "component": "资产-行业-个股分层组合、成本和风险审计",
    },
}


def now_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


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


def rank01(series, ascending=True):
    return series.rank(pct=True, ascending=ascending).fillna(0.5)


def md5_id(*parts):
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def connect(db):
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma busy_timeout=120000")
    return conn


def load_schema(conn, project_root):
    schema = (project_root / "framework" / "integration" / "schema_v3.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


def clear_run(conn, run_id):
    tables = [
        "v3_module_status",
        "v3_data_dictionary",
        "v3_table_quality",
        "v3_report_evidence",
        "v3_model_evidence_link",
        "v3_weekly_snapshot",
        "v3_macro_regime",
        "v3_asset_allocation_signal",
        "v3_style_signal",
        "v3_industry_signal",
        "v3_kline_skill_registry",
        "v3_kline_signal_audit",
        "v3_factor_candidate_registry",
        "v3_factor_validation",
        "v3_fundamental_stock_card",
        "v3_portfolio_layer_target",
        "v3_backtest_audit",
    ]
    for table in tables:
        conn.execute(f"delete from {table} where run_id=?", (run_id,))
    conn.execute("delete from v3_run_manifest where run_id=?", (run_id,))
    conn.commit()


def set_status(conn, run_id, module, status, rows=0, latest_date=None, coverage=None, target_pass=0, message="", artifact_path=""):
    conn.execute(
        """
        insert or replace into v3_module_status
        (run_id, module_name, status, coverage, latest_date, rows, target_pass, message, artifact_path, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, module, status, coverage, latest_date, rows, target_pass, message, artifact_path, now_text()),
    )


def date_col_for(table):
    if table in {"stock_ohlcv_daily", "stock_valuation_daily", "stock_moneyflow_daily", "etf_ohlcv_daily", "lhb_daily"}:
        return "trade_date"
    if table == "financial_report_visible":
        return "visible_date"
    if table == "index_constituent_period":
        return "trade_date"
    if table == "macro_monthly":
        return "month"
    if table == "news_event_daily":
        return "publish_date"
    if table in {"model_signal_daily", "portfolio_target_daily", "backtest_nav"}:
        return "trade_date"
    return None


def build_data_dictionary(conn, run_id):
    layer_map = {
        "source_manifest": "metadata",
        "trade_calendar": "clean",
        "security_master": "clean",
        "stock_ohlcv_daily": "clean",
        "stock_valuation_daily": "clean",
        "stock_moneyflow_daily": "clean",
        "financial_report_visible": "clean_point_in_time",
        "sw_l1_industry_daily": "clean_point_in_time",
        "index_constituent_period": "clean_point_in_time",
        "etf_master": "clean",
        "etf_ohlcv_daily": "clean",
        "macro_monthly": "clean",
        "news_event_daily": "event",
        "lhb_daily": "event",
        "broker_report_index": "evidence",
        "factor_value_daily": "feature",
        "factor_test_result": "model_validation",
        "model_signal_daily": "model_signal",
        "portfolio_target_daily": "portfolio",
        "backtest_nav": "backtest",
        "metrics_by_split_year": "backtest",
    }
    used_by = {
        "stock_ohlcv_daily": "weekly,kline,factor,style,industry,portfolio",
        "stock_valuation_daily": "factor,style,industry,fundamental,portfolio",
        "stock_moneyflow_daily": "weekly,industry,factor,lhb,portfolio",
        "financial_report_visible": "fundamental,factor,industry",
        "sw_l1_industry_daily": "weekly,industry,style,portfolio",
        "index_constituent_period": "universe,enhancement,benchmark",
        "etf_ohlcv_daily": "asset_allocation,style_etf,industry_etf",
        "macro_monthly": "weekly,macro_regime,asset_allocation",
        "news_event_daily": "weekly,event,fundamental,industry",
        "lhb_daily": "weekly,lhb_event,stock_deep_dive",
        "factor_test_result": "factor_mining,model_selection",
        "metrics_by_split_year": "audit,model_doctor,web",
    }
    rows = 0
    for table in CORE_TABLES:
        try:
            fields = conn.execute(f"pragma table_info({table})").fetchall()
        except sqlite3.Error:
            continue
        for _, name, typ, notnull, _, pk in fields:
            required = 1 if pk or notnull or name in {"trade_date", "ts_code", "month", "visible_date"} else 0
            rule = "non-null and point-in-time aligned" if required else "monitored for missing/extreme values"
            freq = "daily" if "daily" in table or table in {"trade_calendar"} else "monthly" if table == "macro_monthly" else "event"
            conn.execute(
                """
                insert or replace into v3_data_dictionary
                (run_id, table_name, field_name, field_type, data_layer, update_frequency, used_by,
                 required_flag, quality_rule, source_priority)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    table,
                    name,
                    typ,
                    layer_map.get(table, "derived"),
                    freq,
                    used_by.get(table, ""),
                    required,
                    rule,
                    "local_warehouse>Wind/Tushare/Akshare/CSMAR_if_needed",
                ),
            )
            rows += 1
    set_status(conn, run_id, "01_data_dictionary", "ready", rows=rows, message="字段级数据字典已生成，覆盖核心底稿表。")


def audit_core_tables(conn, run_id):
    rows = 0
    blockers = []
    for table in CORE_TABLES:
        dcol = date_col_for(table)
        try:
            if dcol:
                cnt, mn, mx = conn.execute(f"select count(*), min({dcol}), max({dcol}) from {table}").fetchone()
            else:
                cnt = conn.execute(f"select count(*) from {table}").fetchone()[0]
                mn, mx = None, None
        except sqlite3.Error as exc:
            cnt, mn, mx = 0, None, None
            blockers.append(f"{table}: {exc}")
        status = "ready"
        msg = ""
        if table in {"stock_ohlcv_daily", "etf_ohlcv_daily", "macro_monthly", "index_constituent_period"} and cnt == 0:
            status = "blocked"
            msg = "required core table is empty"
            blockers.append(table)
        conn.execute(
            """
            insert or replace into v3_table_quality
            (run_id, table_name, rows, min_date, max_date, missing_key_rows, status, message, checked_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, table, cnt, mn, mx, 0, status, msg, now_text()),
        )
        rows += 1
    set_status(
        conn,
        run_id,
        "02_quality_audit",
        "ready" if not blockers else "blocked",
        rows=rows,
        latest_date=END_DATE,
        target_pass=0 if blockers else 1,
        message="; ".join(blockers[:5]) if blockers else "核心表存在且已记录覆盖范围。",
    )


def classify_reports(row):
    title = row.get("title") or row.get("TITLE") or ""
    org = row.get("org") or row.get("ORG") or ""
    hay = f"{org} {title}"
    out = []
    for module, spec in MODULES.items():
        if any(k in hay for k in spec["keywords"]):
            out.append(module)
    if not out and "AI" in hay:
        out.append("weekly_report")
    return out


def ingest_report_evidence(conn, run_id, project_root):
    rows = []
    csv_path = project_root / "environment" / "docs" / "specs" / "recent_ai_quant_report_index.csv"
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                modules = classify_reports(row)
                if not modules:
                    continue
                rid = md5_id(row.get("date"), row.get("org"), row.get("title"), row.get("link"))
                for module in modules:
                    rows.append({
                        "report_id": rid,
                        "report_date": row.get("date"),
                        "org": row.get("org"),
                        "title": row.get("title") or "",
                        "researcher": row.get("researcher"),
                        "link": row.get("link"),
                        "module_name": module,
                        "method_tag": MODULES[module]["component"],
                        "evidence_level": "title_level_index",
                        "adopted_flag": 1,
                        "notes": "来自本地研报索引；后续若取得PDF原文，应升级为full_text_evidence。",
                    })
    try:
        for r in conn.execute("select report_id, report_date, org, title, researcher, link, model_tag from broker_report_index"):
            base = {"date": r[1], "org": r[2], "title": r[3] or "", "researcher": r[4], "link": r[5]}
            modules = classify_reports(base)
            if r[6]:
                modules.append(str(r[6]))
            for module in sorted(set(m for m in modules if m in MODULES)):
                rows.append({
                    "report_id": r[0] or md5_id(r[1], r[2], r[3]),
                    "report_date": r[1],
                    "org": r[2],
                    "title": r[3] or "",
                    "researcher": r[4],
                    "link": r[5],
                    "module_name": module,
                    "method_tag": MODULES[module]["component"],
                    "evidence_level": "warehouse_index",
                    "adopted_flag": 1,
                    "notes": "来自warehouse broker_report_index。",
                })
    except sqlite3.Error:
        pass

    by_module = defaultdict(list)
    for row in rows:
        by_module[row["module_name"]].append(row)

    kept = []
    for module in MODULES:
        candidates = sorted(by_module.get(module, []), key=lambda x: x.get("report_date") or "", reverse=True)
        # Keep 3-7 rows per module when the local evidence index supports it.
        kept.extend(candidates[:7])

    for row in kept:
        conn.execute(
            """
            insert or replace into v3_report_evidence
            (run_id, report_id, report_date, org, title, researcher, link, module_name,
             method_tag, evidence_level, adopted_flag, notes)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row["report_id"],
                row["report_date"],
                row["org"],
                row["title"],
                row["researcher"],
                row["link"],
                row["module_name"],
                row["method_tag"],
                row["evidence_level"],
                row["adopted_flag"],
                row["notes"],
            ),
        )
        conn.execute(
            """
            insert or replace into v3_model_evidence_link
            (run_id, model_name, module_name, report_id, adopted_component, limitation)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                MODULES[row["module_name"]]["model"],
                row["module_name"],
                row["report_id"],
                row["method_tag"],
                "当前自动化层只保存索引级证据；正式报告前应补PDF原文摘要和页码。",
            ),
        )
    short = [m for m in MODULES if len(by_module.get(m, [])) < 3]
    status = "needs_pdf" if short else "ready"
    set_status(
        conn,
        run_id,
        "03_report_evidence",
        status,
        rows=len(kept),
        coverage=(len(MODULES) - len(short)) / len(MODULES),
        message=("证据不足模块: " + ",".join(short)) if short else "每个模块已挂接3-7条研报/索引证据。",
    )


def load_trade_weeks(conn, limit_weeks):
    rows = conn.execute(
        """
        select trade_date from trade_calendar
        where is_trade_day=1 and trade_date between ? and ?
        order by trade_date
        """,
        (START_DATE, END_DATE),
    ).fetchall()
    grouped = defaultdict(list)
    for (d,) in rows:
        dd = dt.datetime.strptime(d, "%Y%m%d").date()
        y, w, _ = dd.isocalendar()
        grouped[(y, w)].append(d)
    weeks = [(min(v), max(v)) for _, v in sorted(grouped.items())]
    return weeks[-limit_weeks:]


def latest_macro(conn, date):
    row = conn.execute(
        "select * from macro_monthly where month<=? order by month desc limit 1",
        (date[:6],),
    ).fetchone()
    if not row:
        return {}
    cols = [x[1] for x in conn.execute("pragma table_info(macro_monthly)").fetchall()]
    return dict(zip(cols, row))


def build_weekly_snapshots(conn, run_id, limit_weeks=104):
    weeks = load_trade_weeks(conn, limit_weeks)
    rows_written = 0
    for first_date, week_end in weeks:
        macro = latest_macro(conn, week_end)
        event_rows = conn.execute(
            """
            select coalesce(category, event_tag, source_site, '未分类') as tag, count(*)
            from news_event_daily
            where publish_date between ? and ?
            group by tag order by count(*) desc limit 8
            """,
            (first_date, week_end),
        ).fetchall()
        lhb_rows = conn.execute(
            """
            select ts_code, max(stock_name), sum(coalesce(net_amount,0)) as net_amt,
                   sum(coalesce(institution_net_amount,0)) as inst_amt, count(*)
            from lhb_daily
            where trade_date between ? and ?
            group by ts_code
            order by abs(net_amt) desc limit 10
            """,
            (first_date, week_end),
        ).fetchall()
        industry_rows = conn.execute(
            """
            select i.industry_name,
                   avg(e.qfq_close / nullif(s.qfq_close, 0) - 1.0) as ret,
                   count(*) as n
            from stock_ohlcv_daily s
            join stock_ohlcv_daily e on e.ts_code=s.ts_code and e.trade_date=?
            left join sw_l1_industry_daily i
              on i.ts_code=s.ts_code and i.start_date<=?
             and (i.end_date is null or i.end_date>? )
            where s.trade_date=? and s.qfq_close>0 and e.qfq_close>0
            group by i.industry_name
            having n>=20
            order by ret desc limit 8
            """,
            (week_end, week_end, week_end, first_date),
        ).fetchall()
        conclusion = {
            "week_end": week_end,
            "growth_state_hint": "expansion" if safe_float(macro.get("pmi_manufacturing")) >= 50 else "neutral_or_contraction",
            "event_count": sum(x[1] for x in event_rows),
            "lhb_active_names": len(lhb_rows),
            "top_industry": industry_rows[0][0] if industry_rows else None,
        }
        conn.execute(
            """
            insert or replace into v3_weekly_snapshot
            (run_id, week_end, macro_json, event_json, industry_json, lhb_json, conclusion_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                week_end,
                jdump(macro),
                jdump([{"tag": r[0], "count": r[1]} for r in event_rows]),
                jdump([{"industry": r[0], "week_return": safe_float(r[1]), "n": r[2]} for r in industry_rows]),
                jdump([{"ts_code": r[0], "stock_name": r[1], "net_amount": safe_float(r[2]), "institution_net": safe_float(r[3]), "events": r[4]} for r in lhb_rows]),
                jdump(conclusion),
            ),
        )
        rows_written += 1
    latest = weeks[-1][1] if weeks else None
    set_status(conn, run_id, "04_weekly_report_agent", "ready", rows=rows_written, latest_date=latest, message="周报快照已覆盖宏观、事件、行业、龙虎榜四块。")


def budget_from_macro(row):
    pmi = safe_float(row.get("pmi_manufacturing"))
    comp = safe_float(row.get("pmi_composite"))
    cpi = safe_float(row.get("cpi_national_yoy"))
    ppi = safe_float(row.get("ppi_yoy"))
    m2 = safe_float(row.get("m2_yoy"))
    sf_stock = safe_float(row.get("sf_stock_endval"))
    growth = "expansion" if pmi >= 50.5 or comp >= 51 else "contraction" if pmi and pmi < 49.5 else "neutral"
    inflation = "reflation" if (cpi > 2.5 or ppi > 3.0) else "deflation_pressure" if (cpi and cpi < 0.5 and ppi < 0) else "normal"
    liquidity = "loose" if (m2 and m2 >= 8.5) or (sf_stock and sf_stock > 440) else "tight" if (m2 and m2 < 7.0) else "neutral"
    if growth == "expansion" and liquidity != "tight":
        budgets = (0.62, 0.18, 0.12, 0.08)
        risk = "risk_on"
    elif growth == "contraction" and liquidity == "loose":
        budgets = (0.42, 0.34, 0.08, 0.16)
        risk = "policy_put"
    elif inflation == "reflation" and growth != "contraction":
        budgets = (0.48, 0.16, 0.24, 0.12)
        risk = "reflation"
    else:
        budgets = (0.32, 0.42, 0.08, 0.18)
        risk = "risk_control"
    return growth, inflation, liquidity, risk, budgets


def build_macro_regime(conn, run_id):
    rows = conn.execute("select * from macro_monthly where month between '201201' and '202606' order by month").fetchall()
    cols = [x[1] for x in conn.execute("pragma table_info(macro_monthly)").fetchall()]
    count = 0
    for raw in rows:
        row = dict(zip(cols, raw))
        growth, inflation, liquidity, risk, budgets = budget_from_macro(row)
        conn.execute(
            """
            insert or replace into v3_macro_regime
            (run_id, month, growth_state, inflation_state, liquidity_state, risk_state,
             equity_budget, bond_budget, commodity_budget, cash_budget, evidence_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row["month"],
                growth,
                inflation,
                liquidity,
                risk,
                budgets[0],
                budgets[1],
                budgets[2],
                budgets[3],
                jdump({k: row.get(k) for k in ["pmi_manufacturing", "pmi_composite", "cpi_national_yoy", "ppi_yoy", "m2_yoy", "sf_stock_endval"]}),
            ),
        )
        count += 1
    latest = rows[-1][0] if rows else None
    set_status(conn, run_id, "05_macro_regime_agent", "ready", rows=count, latest_date=latest, message="宏观周期状态机已生成月度权益/债券/商品/现金预算。")


def infer_asset_class(name):
    n = name or ""
    if any(k in n for k in ["货币", "现金", "短融", "同业存单"]):
        return "cash"
    if any(k in n for k in ["债", "国开", "城投", "信用", "转债"]):
        return "bond_cash"
    if any(k in n for k in ["黄金", "商品", "豆粕", "能源化工", "有色商品"]):
        return "commodity"
    if any(k in n for k in ["恒生", "港股", "纳指", "标普", "德国", "日经", "海外"]):
        return "cross_border"
    if any(k in n for k in ["红利", "价值", "成长", "低波", "质量"]):
        return "style_equity"
    if any(k in n for k in ["沪深", "中证", "上证", "创业板", "科创", "深证", "A50", "宽基"]):
        return "equity"
    return "industry_equity"


def month_pairs(conn):
    dates = [r[0] for r in conn.execute(
        """
        select trade_date from trade_calendar
        where is_trade_day=1 and is_month_last_trade=1 and trade_date between ? and ?
        order by trade_date
        """,
        (START_DATE, END_DATE),
    ).fetchall()]
    return [(dates[i], dates[i + 1]) for i in range(len(dates) - 1)]


def perf_metrics(returns, periods_per_year=12):
    returns = [safe_float(x) for x in returns]
    if not returns:
        return {"periods": 0, "annual_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "target_pass": 0}
    nav = [1.0]
    for r in returns:
        nav.append(nav[-1] * (1 + r))
    periods = len(returns)
    annual = nav[-1] ** (periods_per_year / periods) - 1 if nav[-1] > 0 else -1.0
    vol = float(np.std(returns)) * math.sqrt(periods_per_year)
    peak = 1.0
    mdd = 0.0
    for x in nav:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    sharpe = annual / vol if vol else 0.0
    return {
        "periods": periods,
        "annual_return": annual,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "target_pass": int(annual >= TARGET_ANNUAL_RETURN and sharpe >= TARGET_SHARPE),
    }


def split_of(date):
    if date <= "20201231":
        return "train"
    if date <= "20221231":
        return "valid"
    return "test"


def build_asset_allocation(conn, run_id):
    rows = conn.execute(
        """
        select trade_date, ts_code, fund_name, close, amount
        from etf_ohlcv_daily
        where trade_date between ? and ?
        order by ts_code, trade_date
        """,
        (START_DATE, END_DATE),
    ).fetchall()
    if not rows:
        set_status(conn, run_id, "06_asset_allocator_agent", "blocked", message="etf_ohlcv_daily is empty")
        return
    df = pd.DataFrame(rows, columns=["trade_date", "ts_code", "fund_name", "close", "amount"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df = df.dropna(subset=["close"])
    names = df.groupby("ts_code")["fund_name"].last().to_dict()
    classes = {code: infer_asset_class(name) for code, name in names.items()}
    px = df.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    amt = df.pivot(index="trade_date", columns="ts_code", values="amount").sort_index()
    pairs = month_pairs(conn)
    last_weights = {}
    period_returns = []
    rows_written = 0
    for date, next_date in pairs:
        if date not in px.index or next_date not in px.index:
            continue
        loc = px.index.get_loc(date)
        if loc < 120:
            continue
        month = date[:6]
        regime = conn.execute(
            "select equity_budget, bond_budget, commodity_budget, cash_budget, risk_state from v3_macro_regime where run_id=? and month<=? order by month desc limit 1",
            (run_id, month),
        ).fetchone()
        if not regime:
            budgets = {"equity": 0.45, "style_equity": 0.10, "bond_cash": 0.25, "commodity": 0.10, "cash": 0.10, "cross_border": 0.0}
            risk_state = "neutral"
        else:
            eq, bd, cm, ca, risk_state = regime
            budgets = {"equity": eq * 0.70, "style_equity": eq * 0.20, "industry_equity": eq * 0.10, "bond_cash": bd, "commodity": cm, "cash": ca, "cross_border": 0.05 if risk_state == "risk_on" else 0.0}
        cur = px.loc[date]
        ret20 = cur / px.iloc[loc - 20] - 1
        ret60 = cur / px.iloc[loc - 60] - 1
        ret120 = cur / px.iloc[loc - 120] - 1
        vol60 = px.iloc[loc - 60: loc + 1].pct_change(fill_method=None).std()
        liq = np.log1p(amt.iloc[max(0, loc - 20): loc + 1].mean())
        score = 0.30 * rank01(ret20) + 0.30 * rank01(ret60) + 0.15 * rank01(ret120) + 0.15 * rank01(-vol60) + 0.10 * rank01(liq)
        candidates = pd.DataFrame({"score": score, "ret_next": px.loc[next_date] / cur - 1})
        candidates["asset_class"] = [classes.get(c, "industry_equity") for c in candidates.index]
        candidates["fund_name"] = [names.get(c, "") for c in candidates.index]
        selected = []
        for asset_class, budget in budgets.items():
            if budget <= 0.02:
                continue
            g = candidates[candidates["asset_class"] == asset_class].dropna(subset=["score", "ret_next"])
            if g.empty:
                continue
            row = g.sort_values(["score"], ascending=False).iloc[0]
            selected.append((row.name, asset_class, budget, row))
        total_budget = sum(x[2] for x in selected)
        if not selected or total_budget <= 0:
            continue
        weights = {code: budget / total_budget for code, _, budget, _ in selected}
        turnover = sum(abs(weights.get(k, 0) - last_weights.get(k, 0)) for k in set(weights) | set(last_weights))
        pret = sum(weights[code] * safe_float(row["ret_next"]) for code, _, _, row in selected) - 0.001 * turnover
        period_returns.append((date, pret))
        last_weights = weights
        for code, asset_class, _, row in selected:
            conn.execute(
                """
                insert or replace into v3_asset_allocation_signal
                (run_id, rebalance_date, etf_code, fund_name, asset_class, score, target_weight, reason_json)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    date,
                    code,
                    row["fund_name"],
                    asset_class,
                    safe_float(row["score"]),
                    weights[code],
                    jdump({"risk_state": risk_state, "budget": weights[code], "turnover": turnover}),
                ),
            )
            rows_written += 1
    for split in ["train", "valid", "test", "full"]:
        vals = [r for d, r in period_returns if split == "full" or split_of(d) == split]
        m = perf_metrics(vals)
        conn.execute(
            """
            insert or replace into v3_backtest_audit
            (run_id, universe, model_name, split_name, year, periods, annual_return, sharpe, max_drawdown,
             information_ratio, target_pass, issues_json)
            values (?, ?, ?, ?, 'all', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "MULTI_ASSET_ETF",
                "asset_allocation_etf_v3",
                split,
                m["periods"],
                m["annual_return"],
                m["sharpe"],
                m["max_drawdown"],
                None,
                m["target_pass"],
                jdump([] if m["target_pass"] else ["未达到20%年化/1.5 Sharpe硬门槛", "仍需引入更强宏观预测和波动率目标"]),
            ),
        )
    full = perf_metrics([r for _, r in period_returns])
    set_status(
        conn,
        run_id,
        "06_asset_allocator_agent",
        "ready" if rows_written else "blocked",
        rows=rows_written,
        latest_date=period_returns[-1][0] if period_returns else None,
        target_pass=full["target_pass"],
        message=f"ETF资产配置已改为宏观预算+多周期动量+低波+流动性。全样本年化{full['annual_return']:.2%}, Sharpe {full['sharpe']:.3f}。",
    )


def build_style_industry_targets(conn, run_id):
    rows = conn.execute(
        """
        select trade_date, universe, model_name, ts_code, industry_name, score, target_weight
        from model_signal_daily
        where model_name in ('industry_rotation','style_rotation')
        """,
    ).fetchall()
    industry_written = 0
    style_written = 0
    if rows:
        df = pd.DataFrame(rows, columns=["trade_date", "universe", "model_name", "ts_code", "industry_name", "score", "target_weight"])
        ind = df[df["model_name"] == "industry_rotation"].groupby(["trade_date", "universe", "industry_name"]).agg(score=("score", "mean"), target_weight=("target_weight", "sum"), names=("ts_code", "count")).reset_index()
        for r in ind.itertuples(index=False):
            conn.execute(
                """
                insert or replace into v3_industry_signal
                (run_id, rebalance_date, universe, industry_name, score, target_weight, reason_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, r.trade_date, r.universe, r.industry_name or "UNCLASSIFIED", safe_float(r.score), safe_float(r.target_weight), jdump({"selected_names": int(r.names), "source": "v2_industry_rotation_signals"})),
            )
            industry_written += 1
        style_df = df[df["model_name"] == "style_rotation"].copy()
        if not style_df.empty:
            for date, g0 in style_df.groupby("trade_date"):
                codes = g0["ts_code"].tolist()
                if not codes:
                    continue
                placeholders = ",".join("?" for _ in codes)
                val = pd.read_sql_query(
                    f"""
                    select ts_code, pb, dv_ttm, total_mv, turnover_rate
                    from stock_valuation_daily
                    where trade_date=? and ts_code in ({placeholders})
                    """,
                    conn,
                    params=[date] + codes,
                )
                merged = g0.merge(val, on="ts_code", how="left")
                by_uni = merged.groupby("universe")
                for universe, g in by_uni:
                    style_scores = {
                        "growth": safe_float(g["score"].mean()),
                        "value": safe_float(rank01(-pd.to_numeric(g["pb"], errors="coerce")).mean()),
                        "dividend": safe_float(rank01(pd.to_numeric(g["dv_ttm"], errors="coerce")).mean()),
                        "small_cap": safe_float(rank01(-pd.to_numeric(g["total_mv"], errors="coerce")).mean()),
                    }
                    total = sum(max(v, 0) for v in style_scores.values()) or 1.0
                    for style, score in style_scores.items():
                        conn.execute(
                            """
                            insert or replace into v3_style_signal
                            (run_id, rebalance_date, universe, style_name, score, target_weight, reason_json)
                            values (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (run_id, date, universe, style, score, max(score, 0) / total, jdump({"source": "style_rotation_selected_holdings", "names": len(g)})),
                        )
                        style_written += 1
    set_status(conn, run_id, "07_industry_rotation_agent", "ready" if industry_written else "needs_model", rows=industry_written, message="申万一级行业权重已从正式模型信号聚合。" if industry_written else "缺少行业轮动模型信号。")
    set_status(conn, run_id, "08_style_rotation_agent", "ready" if style_written else "needs_model", rows=style_written, message="风格权重已从风格模型持仓暴露聚合。" if style_written else "缺少风格轮动模型信号。")


def kline_skill_definitions():
    classic = [
        "CDL2CROWS", "CDL3BLACKCROWS", "CDL3INSIDE", "CDL3LINESTRIKE", "CDL3OUTSIDE", "CDL3STARSINSOUTH",
        "CDL3WHITESOLDIERS", "CDLABANDONEDBABY", "CDLADVANCEBLOCK", "CDLBELTHOLD", "CDLBREAKAWAY",
        "CDLCLOSINGMARUBOZU", "CDLCONCEALBABYSWALL", "CDLCOUNTERATTACK", "CDLDARKCLOUDCOVER", "CDLDOJI",
        "CDLDOJISTAR", "CDLDRAGONFLYDOJI", "CDLENGULFING", "CDLEVENINGDOJISTAR", "CDLEVENINGSTAR",
        "CDLGAPSIDESIDEWHITE", "CDLGRAVESTONEDOJI", "CDLHAMMER", "CDLHANGINGMAN", "CDLHARAMI",
        "CDLHARAMICROSS", "CDLHIGHWAVE", "CDLHIKKAKE", "CDLHIKKAKEMOD", "CDLHOMINGPIGEON",
        "CDLIDENTICAL3CROWS", "CDLINNECK", "CDLINVERTEDHAMMER", "CDLKICKING", "CDLKICKINGBYLENGTH",
        "CDLLADDERBOTTOM", "CDLLONGLEGGEDDOJI", "CDLLONGLINE", "CDLMARUBOZU", "CDLMATCHINGLOW",
        "CDLMATHOLD", "CDLMORNINGDOJISTAR", "CDLMORNINGSTAR", "CDLONNECK", "CDLPIERCING", "CDLRICKSHAWMAN",
        "CDLRISEFALL3METHODS", "CDLSEPARATINGLINES", "CDLSHOOTINGSTAR", "CDLSHORTLINE", "CDLSPINNINGTOP",
        "CDLSTALLEDPATTERN", "CDLSTICKSANDWICH", "CDLTAKURI", "CDLTASUKIGAP", "CDLTHRUSTING",
        "CDLTRISTAR", "CDLUNIQUE3RIVER", "CDLUPSIDEGAP2CROWS", "CDLXSIDEGAP3METHODS",
    ]
    defs = []
    for name in classic:
        direction = "contextual"
        if any(k in name for k in ["HAMMER", "MORNING", "PIERCING", "WHITESOLDIERS", "LADDERBOTTOM", "TAKURI"]):
            direction = "bullish"
        if any(k in name for k in ["BLACKCROWS", "EVENING", "DARKCLOUD", "HANGINGMAN", "SHOOTINGSTAR", "ADVANCEBLOCK"]):
            direction = "bearish"
        defs.append((name.lower(), "classic_candlestick", name, direction, 1 if "3" not in name else 3, f"识别{name}对应实体、影线、跳空和趋势上下文组合。", "K线技术分析书籍+TA-Lib命名体系", "logic_registered"))
    windows = [3, 5, 10, 20, 30, 60, 120, 250]
    families = [
        ("trend", "均线多头/空头排列、斜率和价格偏离"),
        ("breakout", "区间高低点突破和成交额确认"),
        ("reversal", "短期超跌/超涨后的影线和量能反转"),
        ("volume_price", "量价配合、缩量整理、放量突破"),
        ("risk", "跌破均线、跌破区间、涨跌停和波动扩张"),
        ("multi_frequency", "日周月多周期共振"),
        ("lhb_event", "龙虎榜事件后量价延续或衰竭"),
        ("fundamental_kline", "财报可见日附近技术确认"),
    ]
    for fam, desc in families:
        for w in windows:
            sid = f"{fam}_{w}"
            defs.append((sid, fam, f"{fam}_{w}", "contextual", w, f"{desc}；观察窗口{w}根K线。", "国内K线/量价技术分析书籍+券商AI技术分析框架", "logic_registered"))
    return defs


def build_kline_registry_and_audit(conn, run_id, project_root, audit_n=12):
    defs = kline_skill_definitions()
    for sid, fam, name, direction, lookback, logic, source, status in defs:
        conn.execute(
            """
            insert or replace into v3_kline_skill_registry
            (run_id, skill_id, family, pattern_name, direction, lookback, logic, source_basis, implementation_status, version)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'v3.0')
            """,
            (run_id, sid, fam, name, direction, lookback, logic, source, status),
        )
    analyzer_path = project_root / "models" / "04_kline_technical_agent" / "single_stock_analyzer.py"
    spec = importlib.util.spec_from_file_location("kline_agent_v3", analyzer_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    latest = conn.execute("select max(trade_date) from stock_ohlcv_daily where trade_date<=?", (END_DATE,)).fetchone()[0]
    codes = [r[0] for r in conn.execute(
        """
        select distinct ts_code from model_signal_daily
        where model_name='portfolio_optimizer' and trade_date=(select max(trade_date) from model_signal_daily)
        order by rank_no limit 30
        """,
    ).fetchall()]
    if not codes:
        codes = [r[0] for r in conn.execute("select ts_code from security_master where list_status='L' limit 20").fetchall()]
    audited = 0
    audit_codes = codes[: max(0, audit_n)]
    for code in audit_codes:
        try:
            payload = mod.analyze(str(project_root / "database" / "research_warehouse.db"), code, "D", latest, write_db=False)
        except Exception:
            continue
        summ = payload["summary"]
        pats = payload["triggered_patterns"]
        conn.execute(
            """
            insert or replace into v3_kline_signal_audit
            (run_id, trade_date, ts_code, frequency, bullish_score, bearish_score, neutral_score,
             pattern_count, feature_count, top_patterns_json, codex_package_json)
            values (?, ?, ?, 'D', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                payload["as_of"],
                code,
                safe_float(summ.get("bullish_strength")),
                safe_float(summ.get("bearish_strength")),
                1.0 if summ.get("conclusion") == "neutral" else 0.0,
                len(pats),
                payload["feature_count"],
                jdump(pats[:10]),
                jdump(payload.get("codex_url_package")),
            ),
        )
        audited += 1
    set_status(
        conn,
        run_id,
        "09_kline_skill_agent",
        "ready",
        rows=len(defs),
        latest_date=latest,
        coverage=audited / max(len(audit_codes), 1),
        message=f"K线skill库注册{len(defs)}条；当前批量审计{audited}/{len(audit_codes)}只，单股Agent可对任意股票继续深度运行。",
    )


def build_factor_registry(conn, run_id):
    rows = conn.execute(
        """
        select universe, factor_name, split_name, rank_ic, icir, group_spread, turnover, coverage, pass_flag, message
        from factor_test_result
        """
    ).fetchall()
    stats = defaultdict(dict)
    for r in rows:
        universe, factor, split, rank_ic, icir, spread, turnover, coverage, pass_flag, msg = r
        conn.execute(
            """
            insert or replace into v3_factor_validation
            (run_id, universe, factor_name, split_name, rank_ic, icir, group_spread, turnover, coverage, pass_flag, message)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, universe, factor, split, rank_ic, icir, spread, turnover, coverage, pass_flag, msg),
        )
        stats[factor][split] = int(pass_flag or 0)
    for factor, by_split in stats.items():
        status = "accepted" if by_split.get("train") and by_split.get("valid") and by_split.get("test") else "watchlist"
        conn.execute(
            """
            insert or replace into v3_factor_candidate_registry
            (run_id, factor_name, factor_group, expression, source_agent, train_pass, valid_pass, test_pass, full_pass, status, notes)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                factor,
                "ai_or_manual_rank_factor",
                factor,
                "10_factor_mining_agent",
                by_split.get("train", 0),
                by_split.get("valid", 0),
                by_split.get("test", 0),
                by_split.get("full", 0),
                status,
                "因子必须通过IC、分组收益、换手和样本外检查后才进入组合。",
            ),
        )
    set_status(conn, run_id, "10_factor_factory_agent", "ready" if rows else "needs_model", rows=len(stats), coverage=len(rows), message=f"因子候选{len(stats)}个，验证记录{len(rows)}条。")


def build_fundamental_cards(conn, run_id):
    latest = conn.execute("select max(trade_date) from stock_valuation_daily where trade_date<=?", (END_DATE,)).fetchone()[0]
    rows = conn.execute(
        """
        with latest_fin as (
          select f.*
          from financial_report_visible f
          join (
            select ts_code, max(visible_date) as visible_date
            from financial_report_visible
            where visible_date<=?
            group by ts_code
          ) m on m.ts_code=f.ts_code and m.visible_date=f.visible_date
        ),
        latest_ind as (
          select s.ts_code, max(s.industry_name) as industry_name
          from sw_l1_industry_daily s
          where s.start_date<=? and (s.end_date is null or s.end_date>?)
          group by s.ts_code
        )
        select v.ts_code, o.stock_name, latest_ind.industry_name, v.pb, v.pe_ttm, v.dv_ttm, v.total_mv,
               f.roe, f.roa, f.gross_margin, f.netprofit_yoy, f.debt_to_assets
        from stock_valuation_daily v
        join stock_ohlcv_daily o on o.ts_code=v.ts_code and o.trade_date=v.trade_date
        left join latest_fin f on f.ts_code=v.ts_code
        left join latest_ind on latest_ind.ts_code=v.ts_code
        where v.trade_date=? and v.total_mv is not null
        """,
        (latest, latest, latest, latest),
    ).fetchall()
    if not rows:
        set_status(conn, run_id, "11_fundamental_research_agent", "blocked", message="no latest valuation/financial rows")
        return
    df = pd.DataFrame(rows, columns=["ts_code", "stock_name", "industry_name", "pb", "pe_ttm", "dv_ttm", "total_mv", "roe", "roa", "gross_margin", "netprofit_yoy", "debt_to_assets"])
    for col in df.columns:
        if col not in {"ts_code", "stock_name", "industry_name"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["quality_score"] = (0.35 * rank01(df["roe"]) + 0.25 * rank01(df["roa"]) + 0.25 * rank01(df["gross_margin"]) + 0.15 * rank01(-df["debt_to_assets"]))
    df["growth_score"] = rank01(df["netprofit_yoy"])
    df["valuation_score"] = (0.50 * rank01(-df["pb"]) + 0.30 * rank01(df["dv_ttm"]) + 0.20 * rank01(-df["pe_ttm"]))
    df["leverage_score"] = rank01(-df["debt_to_assets"])
    df["total_score"] = 0.36 * df["quality_score"] + 0.24 * df["growth_score"] + 0.24 * df["valuation_score"] + 0.16 * df["leverage_score"]
    top = df.drop_duplicates("ts_code").sort_values("total_score", ascending=False).head(500)
    inserted = 0
    for r in top.itertuples(index=False):
        summary = {
            "quality": safe_float(r.quality_score),
            "growth": safe_float(r.growth_score),
            "valuation": safe_float(r.valuation_score),
            "leverage": safe_float(r.leverage_score),
            "visible_as_of": latest,
            "report_mode": "structured_card",
        }
        conn.execute(
            """
            insert or replace into v3_fundamental_stock_card
            (run_id, as_of, ts_code, stock_name, industry_name, quality_score, growth_score,
             valuation_score, leverage_score, total_score, summary_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, latest, r.ts_code, r.stock_name, r.industry_name, safe_float(r.quality_score), safe_float(r.growth_score), safe_float(r.valuation_score), safe_float(r.leverage_score), safe_float(r.total_score), jdump(summary)),
        )
        inserted += 1
    set_status(conn, run_id, "11_fundamental_research_agent", "ready", rows=inserted, latest_date=latest, message="基本面结构化卡片已基于可见财报日、估值和申万行业生成。")


def build_portfolio_and_backtest_audit(conn, run_id):
    target_rows = conn.execute(
        """
        select trade_date, universe, model_name, ts_code, industry_name, target_weight, score
        from portfolio_target_daily
        """,
    ).fetchall()
    written = 0
    for r in target_rows:
        conn.execute(
            """
            insert or replace into v3_portfolio_layer_target
            (run_id, rebalance_date, universe, layer_name, ts_code, industry_name, target_weight, score, source_model, reason_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, r[0], r[1], f"stock_alpha:{r[2]}", r[3], r[4], r[5], r[6], r[2], jdump({"source_run": V2_RUN_ID})),
        )
        written += 1
    metric_rows = conn.execute(
        """
        select universe, model_name, split_name, year, periods, annual_return, sharpe, max_drawdown,
               information_ratio, target_pass, message
        from metrics_by_split_year
        where run_id=?
        """,
        (V2_RUN_ID,),
    ).fetchall()
    pass_full = 0
    for r in metric_rows:
        issues = []
        if not r[9]:
            if safe_float(r[5]) < TARGET_ANNUAL_RETURN:
                issues.append("年化收益低于20%")
            if safe_float(r[6]) < TARGET_SHARPE:
                issues.append("Sharpe低于1.5")
            if safe_float(r[7]) < -0.20:
                issues.append("最大回撤偏大")
        if r[2] == "full" and r[3] == "all" and r[9]:
            pass_full += 1
        conn.execute(
            """
            insert or replace into v3_backtest_audit
            (run_id, universe, model_name, split_name, year, periods, annual_return, sharpe,
             max_drawdown, information_ratio, target_pass, issues_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], jdump(issues or [r[10] or "passed_or_no_issue"])),
        )
    set_status(
        conn,
        run_id,
        "12_portfolio_optimizer_and_backtest",
        "ready" if metric_rows else "needs_model",
        rows=written,
        target_pass=1 if pass_full else 0,
        message=f"组合目标{written}条；回测审计{len(metric_rows)}条；全样本达标模型{pass_full}个。未通过模型保留问题清单。",
    )


def write_outputs(conn, run_id, project_root, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    modules = [dict(zip([c[0] for c in conn.execute("select * from v3_module_status limit 0").description], r)) for r in conn.execute("select * from v3_module_status where run_id=? order by module_name", (run_id,))]
    evidence = [dict(r) for r in map(lambda row: {"module": row[0], "count": row[1]}, conn.execute("select module_name, count(*) from v3_report_evidence where run_id=? group by module_name", (run_id,)).fetchall())]
    audits = [
        {
            "universe": r[0],
            "model_name": r[1],
            "split": r[2],
            "year": r[3],
            "annual_return": r[4],
            "sharpe": r[5],
            "max_drawdown": r[6],
            "target_pass": r[7],
        }
        for r in conn.execute(
            """
            select universe, model_name, split_name, year, annual_return, sharpe, max_drawdown, target_pass
            from v3_backtest_audit
            where run_id=? and year='all'
            order by target_pass desc, sharpe desc
            """,
            (run_id,),
        ).fetchall()
    ]
    payload = {"run_id": run_id, "modules": modules, "evidence_counts": evidence, "backtest_audit": audits}
    (out_dir / "v3_integrated_status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# V3 Integrated Pipeline Status", "", f"Run: `{run_id}`", ""]
    lines.append("## Modules")
    for m in modules:
        lines.append(f"- {m['module_name']}: {m['status']} | rows={m['rows']} | pass={m['target_pass']} | {m['message']}")
    lines.append("")
    lines.append("## Backtest Gate")
    for a in audits[:30]:
        lines.append(f"- {a['universe']} / {a['model_name']} / {a['split']}: annual={safe_float(a['annual_return']):.2%}, sharpe={safe_float(a['sharpe']):.3f}, mdd={safe_float(a['max_drawdown']):.2%}, pass={a['target_pass']}")
    (out_dir / "v3_integrated_status.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    project_root = Path(args.project_root).resolve()
    db = Path(args.db).resolve()
    out_dir = Path(args.out_dir).resolve()
    conn = connect(db)
    load_schema(conn, project_root)
    clear_run(conn, args.run_id)
    conn.execute(
        """
        insert into v3_run_manifest
        (run_id, started_at, status, start_date, end_date, train_window, valid_window, test_window,
         target_annual_return, target_sharpe, message)
        values (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (args.run_id, now_text(), START_DATE, END_DATE, TRAIN_WINDOW, VALID_WINDOW, TEST_WINDOW, TARGET_ANNUAL_RETURN, TARGET_SHARPE, "v3 strict integration started"),
    )
    conn.commit()
    try:
        steps = [
            lambda: build_data_dictionary(conn, args.run_id),
            lambda: audit_core_tables(conn, args.run_id),
            lambda: ingest_report_evidence(conn, args.run_id, project_root),
            lambda: build_weekly_snapshots(conn, args.run_id, args.weekly_weeks),
            lambda: build_macro_regime(conn, args.run_id),
            lambda: build_asset_allocation(conn, args.run_id),
            lambda: build_style_industry_targets(conn, args.run_id),
            lambda: build_kline_registry_and_audit(conn, args.run_id, project_root, args.kline_audit_n),
            lambda: build_factor_registry(conn, args.run_id),
            lambda: build_fundamental_cards(conn, args.run_id),
            lambda: build_portfolio_and_backtest_audit(conn, args.run_id),
            lambda: write_outputs(conn, args.run_id, project_root, out_dir),
        ]
        for step in steps:
            step()
            conn.commit()
        conn.execute("update v3_run_manifest set status='ready', ended_at=?, message=? where run_id=?", (now_text(), "v3 strict integration completed; failed gates remain explicit", args.run_id))
        conn.commit()
    except Exception as exc:
        conn.execute("update v3_run_manifest set status='failed', ended_at=?, message=? where run_id=?", (now_text(), str(exc), args.run_id))
        conn.commit()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--out-dir", default=str(DEFAULT_PROJECT_ROOT / "output" / "framework" / "integration" / "outputs"))
    parser.add_argument("--run-id", default="v3_strict_integrated_20260706")
    parser.add_argument("--weekly-weeks", type=int, default=104)
    parser.add_argument("--kline-audit-n", type=int, default=12)
    args = parser.parse_args()
    run(args)
    print(json.dumps({"run_id": args.run_id, "status": "ready", "out_dir": args.out_dir}, ensure_ascii=False))


if __name__ == "__main__":
    main()
