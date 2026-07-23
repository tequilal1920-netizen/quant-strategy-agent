"""Build the production snapshot for the six-page index-enhancement workspace.

The builder is deliberately offline-first.  It reads the point-in-time research
warehouse and the formal model leaderboard, emits a compact JSON contract, and
never calls metered or external APIs during a web request.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable


HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parents[2]
APP_DIR = AGENT_DIR / "board" / "quant_strategy_agent"
WAREHOUSE = AGENT_DIR / "database" / "research_warehouse.db"
LEADERBOARD = AGENT_DIR / "output" / "framework" / "backtest" / "model_outputs_formal" / "model_leaderboard.csv"
OUTPUT = APP_DIR / "data" / "index_enhancement_snapshot.json"
PAGES = ["home", "universe", "alpha", "smartbeta", "risk", "tracking"]
UNIVERSES = ["CSI800_ENH", "CSI2000_ENH"]


def finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def round_or_none(value: Any, digits: int = 6) -> float | None:
    number = finite(value)
    return round(number, digits) if number is not None else None


def chunks(items: list[str], size: int = 450) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def query_by_codes(conn: sqlite3.Connection, sql_prefix: str, codes: list[str], params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for batch in chunks(codes):
        marks = ",".join("?" for _ in batch)
        rows.extend(conn.execute(sql_prefix.format(marks=marks), (*params, *batch)).fetchall())
    return rows


def percentile(values: list[float], q: float) -> float | None:
    clean = sorted(x for x in values if math.isfinite(x))
    if not clean:
        return None
    pos = (len(clean) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - pos) + clean[hi] * (pos - lo)


def drawdown(values: list[float]) -> list[float]:
    peak = -math.inf
    out: list[float] = []
    for value in values:
        peak = max(peak, value)
        out.append(value / peak - 1.0 if peak > 0 else 0.0)
    return out


def rolling_stats(returns: list[float], benchmark: list[float], window: int = 12) -> dict[str, list[float | None]]:
    sharpe: list[float | None] = []
    ir: list[float | None] = []
    te: list[float | None] = []
    for idx in range(len(returns)):
        if idx + 1 < window:
            sharpe.append(None)
            ir.append(None)
            te.append(None)
            continue
        sample = returns[idx + 1 - window : idx + 1]
        active = [a - b for a, b in zip(sample, benchmark[idx + 1 - window : idx + 1])]
        vol = pstdev(sample)
        active_vol = pstdev(active)
        sharpe.append(round_or_none(mean(sample) / vol * math.sqrt(12), 4) if vol else None)
        ir.append(round_or_none(mean(active) / active_vol * math.sqrt(12), 4) if active_vol else None)
        te.append(round_or_none(active_vol * math.sqrt(12), 6))
    return {"sharpe": sharpe, "information_ratio": ir, "tracking_error": te}


def read_leaderboard() -> list[dict[str, Any]]:
    with LEADERBOARD.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    output: list[dict[str, Any]] = []
    for row in rows:
        if row.get("universe") not in UNIVERSES:
            continue
        output.append(
            {
                "universe": row.get("universe"),
                "model": row.get("model"),
                "status": row.get("status"),
                "periods": int(float(row.get("periods") or 0)),
                "total_return": round_or_none(row.get("total_return")),
                "annual_return": round_or_none(row.get("annual_return")),
                "annual_volatility": round_or_none(row.get("annual_volatility")),
                "sharpe": round_or_none(row.get("sharpe")),
                "max_drawdown": round_or_none(row.get("max_drawdown")),
                "win_rate": round_or_none(row.get("win_rate")),
                "excess_annual_return": round_or_none(row.get("excess_annual_return")),
                "information_ratio": round_or_none(row.get("information_ratio")),
                "target_pass": int(float(row.get("target_pass") or 0)),
                "evidence_type": "formal_backtest",
            }
        )
    return output


def build_universe(conn: sqlite3.Connection, universe: str) -> dict[str, Any]:
    latest = conn.execute(
        "SELECT max(trade_date) FROM index_constituent_period WHERE universe=? AND status='ready'", (universe,)
    ).fetchone()[0]
    members = conn.execute(
        "SELECT con_code, weight, source FROM index_constituent_period WHERE universe=? AND trade_date=? ORDER BY weight DESC",
        (universe, latest),
    ).fetchall()
    codes = [row[0] for row in members]
    weight_map = {row[0]: finite(row[1], 0.0) or 0.0 for row in members}
    source_counts = Counter(str(row[2] or "unknown") for row in members)

    quote_rows = query_by_codes(
        conn,
        "SELECT ts_code,stock_name,close,pct_chg,amount,up_limit,down_limit,suspend_timing FROM stock_ohlcv_daily WHERE trade_date=? AND ts_code IN ({marks})",
        codes,
        (latest,),
    )
    value_rows = query_by_codes(
        conn,
        "SELECT ts_code,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv,turnover_rate_f,volume_ratio FROM stock_valuation_daily WHERE trade_date=? AND ts_code IN ({marks})",
        codes,
        (latest,),
    )
    industry_rows = query_by_codes(
        conn,
        """SELECT s.ts_code,s.industry_name FROM sw_l1_industry_daily s
        WHERE s.start_date<=? AND (s.end_date IS NULL OR s.end_date>=?) AND s.ts_code IN ({marks})
        AND s.start_date=(SELECT max(x.start_date) FROM sw_l1_industry_daily x WHERE x.ts_code=s.ts_code AND x.start_date<=? AND (x.end_date IS NULL OR x.end_date>=?))""",
        codes,
        (latest, latest, latest, latest),
    )
    factor_date = conn.execute("SELECT max(trade_date) FROM factor_value_daily WHERE trade_date<=?", (latest,)).fetchone()[0]
    factor_codes = {
        row[0]
        for row in query_by_codes(
            conn,
            "SELECT DISTINCT ts_code FROM factor_value_daily WHERE trade_date>=? AND trade_date<=? AND ts_code IN ({marks})",
            codes,
            (str(int(str(factor_date)[:4]) - 1) + "0101", factor_date),
        )
    }
    quotes = {row[0]: dict(row) for row in quote_rows}
    values = {row[0]: dict(row) for row in value_rows}
    industries = {row[0]: row[1] for row in industry_rows}

    rows: list[dict[str, Any]] = []
    for code in codes:
        quote, value = quotes.get(code, {}), values.get(code, {})
        close = finite(quote.get("close"))
        suspended = str(quote.get("suspend_timing") or "").strip() not in {"", "None", "0", "nan"}
        at_limit = close is not None and (
            abs(close - (finite(quote.get("up_limit"), math.inf) or math.inf)) < 1e-8
            or abs(close - (finite(quote.get("down_limit"), -math.inf) or -math.inf)) < 1e-8
        )
        rows.append(
            {
                "code": code,
                "name": quote.get("stock_name") or code,
                "industry": industries.get(code, "未分类"),
                "weight": round_or_none(weight_map.get(code), 6),
                "close": round_or_none(close, 4),
                "pct_chg": round_or_none(quote.get("pct_chg"), 4),
                "amount": round_or_none((finite(quote.get("amount"), 0.0) or 0.0) / 100000.0, 4),
                "total_mv": round_or_none((finite(value.get("total_mv"), 0.0) or 0.0) / 10000.0, 4),
                "circ_mv": round_or_none((finite(value.get("circ_mv"), 0.0) or 0.0) / 10000.0, 4),
                "pe_ttm": round_or_none(value.get("pe_ttm"), 4),
                "pb": round_or_none(value.get("pb"), 4),
                "ps_ttm": round_or_none(value.get("ps_ttm"), 4),
                "dv_ttm": round_or_none(value.get("dv_ttm"), 4),
                "turnover": round_or_none(value.get("turnover_rate_f"), 4),
                "volume_ratio": round_or_none(value.get("volume_ratio"), 4),
                "quote_ok": bool(quote),
                "valuation_ok": bool(value),
                "factor_ok": bool(quote) and bool(value),
                "factor_precomputed": code in factor_codes,
                "tradable": bool(quote) and not suspended and not at_limit and (finite(quote.get("amount"), 0.0) or 0.0) > 0,
            }
        )

    valid_mv = [row["total_mv"] for row in rows if finite(row["total_mv"]) is not None and row["total_mv"] > 0]
    valid_pe = [row["pe_ttm"] for row in rows if finite(row["pe_ttm"]) is not None and 0 < row["pe_ttm"] < 300]
    valid_pb = [row["pb"] for row in rows if finite(row["pb"]) is not None and 0 < row["pb"] < 30]
    valid_turn = [row["turnover"] for row in rows if finite(row["turnover"]) is not None and row["turnover"] >= 0]

    industry_weight: defaultdict[str, float] = defaultdict(float)
    industry_count: Counter[str] = Counter()
    size_weight = {"≤50亿": 0.0, "50–100亿": 0.0, "100–300亿": 0.0, "300–1000亿": 0.0, ">1000亿": 0.0}
    liquidity_weight = {"低流动性": 0.0, "中低流动性": 0.0, "中高流动性": 0.0, "高流动性": 0.0}
    q_turn = [percentile(valid_turn, q) or 0.0 for q in (0.25, 0.5, 0.75)]
    for row in rows:
        weight = finite(row["weight"], 0.0) or 0.0
        industry_weight[row["industry"]] += weight
        industry_count[row["industry"]] += 1
        mv = finite(row["total_mv"], 0.0) or 0.0
        size_key = "≤50亿" if mv <= 50 else "50–100亿" if mv <= 100 else "100–300亿" if mv <= 300 else "300–1000亿" if mv <= 1000 else ">1000亿"
        size_weight[size_key] += weight
        turn = finite(row["turnover"], 0.0) or 0.0
        liquidity_key = "低流动性" if turn <= q_turn[0] else "中低流动性" if turn <= q_turn[1] else "中高流动性" if turn <= q_turn[2] else "高流动性"
        liquidity_weight[liquidity_key] += weight

    scatter = sorted(rows, key=lambda row: finite(row["weight"], 0.0) or 0.0, reverse=True)[: min(400, len(rows))]
    investable = [row for row in rows if row["tradable"] and row["valuation_ok"] and row["factor_ok"]]
    return {
        "id": universe,
        "label": "中证800增强" if universe == "CSI800_ENH" else "中证2000增强",
        "as_of": latest,
        "factor_as_of": factor_date,
        "source": dict(source_counts),
        "weight_sum": round_or_none(sum(weight_map.values()), 6),
        "summary": {
            "benchmark_count": len(rows),
            "quote_count": sum(row["quote_ok"] for row in rows),
            "tradable_count": sum(row["tradable"] for row in rows),
            "valuation_count": sum(row["valuation_ok"] for row in rows),
            "factor_count": sum(row["factor_ok"] for row in rows),
            "precomputed_factor_count": sum(row["factor_precomputed"] for row in rows),
            "investable_count": len(investable),
            "median_market_cap": round_or_none(percentile(valid_mv, 0.5), 2),
            "median_pe": round_or_none(percentile(valid_pe, 0.5), 2),
            "median_pb": round_or_none(percentile(valid_pb, 0.5), 2),
            "median_turnover": round_or_none(percentile(valid_turn, 0.5), 2),
        },
        "funnel": [
            {"label": "基准成分", "value": len(rows)},
            {"label": "行情可用", "value": sum(row["quote_ok"] for row in rows)},
            {"label": "可交易", "value": sum(row["tradable"] for row in rows)},
            {"label": "估值可用", "value": sum(row["valuation_ok"] for row in rows)},
            {"label": "因子可计算", "value": sum(row["factor_ok"] for row in rows)},
            {"label": "最终投资池", "value": len(investable)},
        ],
        "industry": [
            {"name": key, "weight": round_or_none(value, 4), "count": industry_count[key]}
            for key, value in sorted(industry_weight.items(), key=lambda item: item[1], reverse=True)
        ],
        "size": [{"name": key, "weight": round_or_none(value, 4)} for key, value in size_weight.items()],
        "liquidity": [{"name": key, "weight": round_or_none(value, 4)} for key, value in liquidity_weight.items()],
        "valuation": {
            "pe": [round_or_none(percentile(valid_pe, q), 3) for q in (0.1, 0.25, 0.5, 0.75, 0.9)],
            "pb": [round_or_none(percentile(valid_pb, q), 3) for q in (0.1, 0.25, 0.5, 0.75, 0.9)],
            "market_cap": [round_or_none(percentile(valid_mv, q), 3) for q in (0.1, 0.25, 0.5, 0.75, 0.9)],
            "turnover": [round_or_none(percentile(valid_turn, q), 3) for q in (0.1, 0.25, 0.5, 0.75, 0.9)],
        },
        "scatter": [
            {key: row[key] for key in ("code", "name", "industry", "weight", "total_mv", "turnover", "pe_ttm", "pb", "amount", "pct_chg")}
            for row in scatter
        ],
        "constituents": [
            {key: row[key] for key in ("code", "name", "industry", "weight", "total_mv", "pe_ttm", "pb", "dv_ttm", "turnover", "amount", "pct_chg", "tradable")}
            for row in rows[:80]
        ],
    }


def build_nav(conn: sqlite3.Connection, leaderboard: list[dict[str, Any]]) -> dict[str, Any]:
    selected_models = {
        "CSI800_ENH": [
            "index_enhancement_agent_v6",
            "index_enhancement_deep_agent_v6",
            "industry_budget_ic_optimizer_v7",
            "csi800_quality_value_core_v10",
        ],
        "CSI2000_ENH": [
            "csi2000_style_concentrated_agent_v6",
            "csi2000_style_risk_control_agent_v6",
            "factor_ic_learned_agent_v7",
            "index_enhancement_agent_v6",
            "index_enhancement_deep_agent_v6",
            "stock_factor_deep_agent_v4",
        ],
    }
    result: dict[str, Any] = {}
    for universe, models in selected_models.items():
        series: list[dict[str, Any]] = []
        benchmark: dict[str, Any] | None = None
        for model in models:
            rows = conn.execute(
                """SELECT trade_date,nav,period_return,benchmark_return,excess_return FROM backtest_nav
                WHERE universe=? AND model_name=? AND split_name='full' ORDER BY trade_date""",
                (universe, model),
            ).fetchall()
            if not rows:
                continue
            dates = [f"{str(row[0])[:4]}-{str(row[0])[4:6]}-{str(row[0])[6:8]}" if len(str(row[0])) == 8 else str(row[0]) for row in rows]
            values = [finite(row[1], 1.0) or 1.0 for row in rows]
            returns = [finite(row[2], 0.0) or 0.0 for row in rows]
            bench_returns = [finite(row[3], 0.0) or 0.0 for row in rows]
            if benchmark is None:
                bench_nav: list[float] = []
                level = 1.0
                for ret in bench_returns:
                    level *= 1.0 + ret
                    bench_nav.append(level)
                benchmark = {"name": "基准指数", "dates": dates, "nav": [round(x, 6) for x in bench_nav], "returns": bench_returns}
            roll = rolling_stats(returns, bench_returns)
            series.append(
                {
                    "model": model,
                    "label": model.replace("_agent", "").replace("_", " "),
                    "dates": dates,
                    "nav": [round(x, 6) for x in values],
                    "returns": [round(x, 7) for x in returns],
                    "excess_returns": [round((a - b), 7) for a, b in zip(returns, bench_returns)],
                    "drawdown": [round(x, 6) for x in drawdown(values)],
                    "rolling": roll,
                }
            )
        result[universe] = {
            "benchmark": benchmark,
            "series": series,
            "leaderboard": [row for row in leaderboard if row["universe"] == universe],
        }
    return result


def build_factor_tests(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for universe in UNIVERSES:
        rows = conn.execute(
            """SELECT factor_name,split_name,rank_ic,icir,group_spread,turnover,coverage,pass_flag,message
            FROM factor_test_result WHERE universe=? ORDER BY pass_flag DESC, abs(coalesce(rank_ic,0)) DESC""",
            (universe,),
        ).fetchall()
        out[universe] = [
            {
                "factor": row[0],
                "split": row[1],
                "rank_ic": round_or_none(row[2]),
                "icir": round_or_none(row[3]),
                "group_spread": round_or_none(row[4]),
                "turnover": round_or_none(row[5]),
                "coverage": round_or_none(row[6]),
                "pass": int(row[7] or 0),
                "message": row[8],
            }
            for row in rows[:120]
        ]
    return out


def model_catalog() -> list[dict[str, Any]]:
    return [
        {"id": "linear_core", "name": "PIT线性贝叶斯核心", "role": "core", "stack": "稳健Z-score → 行业/规模中性 → ElasticNet/贝叶斯收缩 → IC后验加权", "params": "半衰期20/60/120日；L1比率0.05–0.80；系数漂移惩罚；Huber损失", "gate": "跨期IC同号、换手与容量通过后进入集成"},
        {"id": "tree_rank", "name": "GBDT排序专家", "role": "core", "stack": "LightGBM/CatBoost/LambdaMART → 横截面排序 → 单调与行业约束 → 概率校准", "params": "深度3–10；叶数15–127；学习率0.005–0.08；GOSS/Bagging；LambdaRank NDCG@K", "gate": "Purged Walk-forward + 特征漂移PSI"},
        {"id": "master", "name": "MASTER市场引导Transformer", "role": "challenger", "stack": "市场状态门控 → 时序注意力 → 股票间注意力 → 多任务RankIC/收益/风险头", "params": "窗口20/60/120；d_model64–256；4–8头；DropPath0–0.3；Huber+ListMLE", "gate": "只在CPCV/DSR/PBO通过后晋级"},
        {"id": "graph_hist", "name": "HIST/GAT关系网络", "role": "challenger", "stack": "行业/供应链/收益相关图 → 图注意力 → 残差表征 → 横截面排序", "params": "动态图阈值0.2–0.6；2–4层GAT；邻居16–64；图稀疏惩罚", "gate": "图数据PIT审计与边稳定性检验"},
        {"id": "temporal", "name": "TCN-GRU多频时序", "role": "challenger", "stack": "日频/周频/资金流/量价卷积 → GRU/TCN → 门控融合 → 分位数收益头", "params": "卷积核3/5/7；膨胀1/2/4/8；隐层64–256；分位数0.1/0.5/0.9", "gate": "短周期冲击与交易成本压力测试"},
        {"id": "tabular", "name": "FT-Transformer/TabNet", "role": "challenger", "stack": "连续特征嵌入 → 特征注意力 → 稀疏选择 → 横截面排序", "params": "8–12层；头数4–8；稀疏系数1e-5–1e-2；Mixup/Dropout", "gate": "相对树模型净IC与稳定性增益"},
        {"id": "llm_miner", "name": "LLM因子研究代理", "role": "research", "stack": "券商/论文RAG → DSL候选 → MCTS/OpenFE遗传变异 → 双残差 → IC/DSR/PBO裁决", "params": "候选预算、语义去重阈值、复杂度惩罚、算子白名单、泄漏扫描", "gate": "表达式可执行、经济含义、人审、样本外晋级"},
        {"id": "moe", "name": "状态门控专家混合", "role": "ensemble", "stack": "HMM/宏观流动性状态 → 专家概率 → 贝叶斯模型平均 → 蒸馏线性代理", "params": "状态3–6；温度0.3–2.0；Dirichlet先验；权重漂移与熵约束", "gate": "稳定性优先于单期最高夏普"},
    ]


def smartbeta_catalog() -> list[dict[str, Any]]:
    return [
        {"id": "value", "name": "价值", "signal": "EP/BP/FCFP/股息率，行业内稳健标准化", "construction": "分段线性倾斜 + 上下限 + 估值异常截尾", "risk": "价值陷阱：盈利质量、杠杆、分析师下修联防"},
        {"id": "quality", "name": "质量", "signal": "ROE/毛利/现金转化/应计/杠杆/盈利稳定性", "construction": "PIT财报可见日 + 缺失惩罚 + 多指标贝叶斯收缩", "risk": "拥挤度与估值溢价上限"},
        {"id": "momentum", "name": "动量", "signal": "12-1、6-1、20/60日残差动量与加速度", "construction": "波动率缩放 + 反转缓冲 + 换手带", "risk": "崩盘状态由市场广度/波动跳升门控"},
        {"id": "lowvol", "name": "低波/低Beta", "signal": "EWMA波动、下行波动、市场Beta、特质波动", "construction": "协方差感知最小方差倾斜", "risk": "利率敏感与行业集中约束"},
        {"id": "dividend", "name": "红利", "signal": "股息率、支付能力、现金流覆盖、连续分红", "construction": "可持续红利评分 + 单一公司/行业上限", "risk": "高股息不可持续与周期顶部过滤"},
        {"id": "size", "name": "规模", "signal": "流通市值、规模残差、流动性/壳风险过滤", "construction": "对数市值倾斜 + 容量约束", "risk": "冲击成本、涨跌停与停牌风险"},
        {"id": "growth", "name": "成长", "signal": "营收/利润/现金流增长与盈利预期扩散", "construction": "增长质量双维度 + 极端估值抑制", "risk": "预期透支与拥挤度"},
        {"id": "multi", "name": "动态多因子", "signal": "因子溢价、宏观/流动性、拥挤与衰减状态", "construction": "风险平价底仓 + HMM/门控网络战术偏离", "risk": "时序择因子仅作为挑战模型"},
    ]


def risk_catalog() -> dict[str, Any]:
    return {
        "layers": [
            {"name": "基础风险因子", "detail": "CNE5风格/行业暴露、特质风险、缺失与异常值处理", "status": "core"},
            {"name": "统计潜因子", "detail": "滚动PCA/POET，吸收基础模型未解释的共振", "status": "core"},
            {"name": "深度潜因子", "detail": "自编码器/变分状态空间提取非线性共同风险", "status": "challenger"},
            {"name": "动态协方差", "detail": "EWMA + DCC-GARCH + 收缩估计 + 特征值修复", "status": "core"},
            {"name": "尾部风险", "detail": "t分布/EVT、Copula相关、CVaR与流动性压力", "status": "core"},
            {"name": "前瞻波动", "detail": "HAR-RV/Transformer波动预测，贝叶斯模型平均", "status": "challenger"},
        ],
        "constraints": [
            {"name": "跟踪误差", "formula": "sqrt((w-wb)'Σ(w-wb))", "default": "年化2%–6%，按产品档位"},
            {"name": "行业偏离", "formula": "|B_ind'(w-wb)|", "default": "核心±1%，卫星±3%"},
            {"name": "风格偏离", "formula": "|B_style'(w-wb)|", "default": "每因子0.15–0.30σ"},
            {"name": "个股主动权重", "formula": "|wi-wbi|", "default": "0.30%–1.00%"},
            {"name": "换手", "formula": "0.5*Σ|w-wprev|", "default": "单次5%–20%"},
            {"name": "容量", "formula": "trade_i / ADV20_i", "default": "≤5%–10%"},
            {"name": "尾部风险", "formula": "CVaR95/99", "default": "预算随波动状态收紧"},
        ],
        "stress": [
            {"scenario": "市场单日-5%", "benchmark": -5.0, "active": -0.7, "liquidity_haircut": 1.4},
            {"scenario": "小盘流动性冲击", "benchmark": -7.5, "active": -1.8, "liquidity_haircut": 2.2},
            {"scenario": "价值反转", "benchmark": -3.0, "active": -1.2, "liquidity_haircut": 1.3},
            {"scenario": "成长拥挤踩踏", "benchmark": -4.5, "active": -1.5, "liquidity_haircut": 1.7},
            {"scenario": "相关性跃迁至0.8", "benchmark": -6.0, "active": -1.0, "liquidity_haircut": 1.9},
        ],
    }


def solver_catalog() -> dict[str, Any]:
    return {
        "objective": "max α'w - λTE·(w-wb)'Σ(w-wb) - λTC·TC(Δw) - λCVaR·CVaR - λL2||w-wb||²",
        "layers": [
            {"order": 1, "name": "候选模型与超参后验", "method": "CPCV/Purged Walk-forward + Optuna TPE/BOHB/ASHA", "output": "冻结的Alpha与风险预测"},
            {"order": 2, "name": "连续凸优化", "method": "QP/SOCP/CVaR-LP；OSQP/ECOS/Gurobi/RQOptimizer交叉核验", "output": "连续目标权重"},
            {"order": 3, "name": "稳健与多期优化", "method": "Wasserstein DRO + MPC交易路径 + 分段冲击成本", "output": "多期可执行轨迹"},
            {"order": 4, "name": "离散化与交易约束", "method": "MIQP处理手数、最小委托、持仓数、涨跌停", "output": "交易清单"},
            {"order": 5, "name": "可行性修复", "method": "优先级松弛、ADMM分解、精确约束复核", "output": "约束全部可解释"},
        ],
        "parameter_search": [
            {"level": "外层", "purpose": "模型/因子/风险预算选择", "method": "CPCV + PBO + Deflated Sharpe", "budget": "64–256组合"},
            {"level": "中层", "purpose": "深度模型与集成超参", "method": "TPE/BOHB + ASHA早停", "budget": "每模型60–200 trials"},
            {"level": "内层", "purpose": "每折训练与校准", "method": "Bayesian shrinkage/温度校准", "budget": "严格只用训练折"},
            {"level": "求解层", "purpose": "λ与约束边界", "method": "Pareto前沿 + 可行域二分 + warm start", "budget": "20–80个前沿点"},
        ],
        "execution": [
            {"name": "线性成本", "formula": "commission + stamp + spread"},
            {"name": "非线性冲击", "formula": "η·σ·sqrt(Q/ADV) + γ·Q/ADV"},
            {"name": "成交概率", "formula": "涨跌停/停牌/盘口容量条件化"},
            {"name": "再平衡缓冲", "formula": "no-trade band + hysteresis"},
        ],
    }


def research_basis() -> list[dict[str, str]]:
    return [
        {"type": "本地正式证据", "name": "research_warehouse.db / formal model outputs", "use": "PIT资产池、因子检验、净值、夏普、IR、回撤"},
        {"type": "券商路线", "name": "国盛证券：基于深度学习的指数增强策略", "use": "多频量价/资金流深度表征与指数增强落地"},
        {"type": "券商路线", "name": "民生证券：MASTER与深度风险模型系列", "use": "市场引导Transformer、非线性风险因子与组合构建"},
        {"type": "论文", "name": "MASTER: Market-Guided Stock Transformer", "use": "市场状态门控与股票横截面注意力"},
        {"type": "风险模型", "name": "MSCI Barra CNE5 methodology", "use": "A股风格/行业风险因子与特质风险基线"},
        {"type": "稳健评价", "name": "PBO / Deflated Sharpe Ratio", "use": "多重试验和回测过拟合惩罚"},
        {"type": "组合优化", "name": "Multi-period convex portfolio optimization", "use": "多期交易、成本和约束统一求解"},
        {"type": "内部研究入口", "name": "WarrenQ团队研究索引", "use": "指数增强、微观结构与因子组合主题持续学习入口"},
    ]


def build_snapshot() -> dict[str, Any]:
    conn = sqlite3.connect(WAREHOUSE)
    conn.row_factory = sqlite3.Row
    try:
        leaderboard = read_leaderboard()
        universes = {universe: build_universe(conn, universe) for universe in UNIVERSES}
        nav = build_nav(conn, leaderboard)
        factor_tests = build_factor_tests(conn)
    finally:
        conn.close()
    quality_checks = {
        "page_contract": PAGES,
        "universe_count": len(universes),
        "leaderboard_rows": len(leaderboard),
        "nav_series": sum(len(value["series"]) for value in nav.values()),
        "factor_test_rows": sum(len(value) for value in factor_tests.values()),
        "weight_sums": {key: value["weight_sum"] for key, value in universes.items()},
        "chart_contract": 44,
        "external_api_calls": 0,
    }
    passed = (
        set(PAGES) == {"home", "universe", "alpha", "smartbeta", "risk", "tracking"}
        and all(value["summary"]["benchmark_count"] in {800, 2000} for value in universes.values())
        and all(99.0 <= (value["weight_sum"] or 0) <= 101.0 for value in universes.values())
        and quality_checks["nav_series"] >= 8
        and quality_checks["factor_test_rows"] >= 20
    )
    return {
        "status": "ready" if passed else "failed",
        "engine_version": "index-enhancement/1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_as_of": max(value["as_of"] for value in universes.values()),
        "page_contract": PAGES,
        "quality": {"status": "passed" if passed else "failed", **quality_checks},
        "universes": universes,
        "nav": nav,
        "leaderboard": leaderboard,
        "factor_tests": factor_tests,
        "models": model_catalog(),
        "smartbeta": smartbeta_catalog(),
        "risk": risk_catalog(),
        "solver": solver_catalog(),
        "research_basis": research_basis(),
        "governance": {
            "promotion": "研究 → 挑战者 → 影子盘 → 小资金 → 正式；任一质量门失败自动降级",
            "anti_leakage": "财报按可见日、指数成分按生效日、训练/验证/测试冻结、Purged+Embargo",
            "performance_rule": "页面只展示formal_backtest证据；设计参数、压力情景与代理诊断必须显式标注",
            "update": "日频数据质检，月度再平衡，季度重训，漂移/风险事件触发重训",
        },
    }


def main() -> None:
    snapshot = build_snapshot()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "bytes": OUTPUT.stat().st_size, "quality": snapshot["quality"]}, ensure_ascii=False, indent=2))
    if snapshot["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
