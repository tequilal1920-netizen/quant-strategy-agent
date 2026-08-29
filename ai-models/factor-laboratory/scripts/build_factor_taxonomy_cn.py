"""Build the Chinese six-dimension factor taxonomy requested by the user.

The source of truth is the read-only factor_catalog module plus the local
research_warehouse database.  This script writes user-facing CSV/JSON outputs:

- unique_factor_catalog_cn.csv: one row per unique factor name.
- factor_entity_locations_cn.csv: one row per factor/source entity.
- model_29_factors_cn.csv: the current production core-29 training panel.
- taxonomy_summary_cn.csv: category/sub-category counts.
- factor_taxonomy_cn.json: machine-readable equivalent.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACTOR_LAB_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = FACTOR_LAB_ROOT / "source"
WAREHOUSE_PATH = PROJECT_ROOT / "database" / "research_warehouse.db"
OUTPUT_DIR = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_taxonomy_cn"

sys.path.insert(0, str(SOURCE_ROOT))

import factor_catalog  # noqa: E402


PRIMARY_PRIORITY = {
    "core_model": 0,
    "technical": 1,
    "money": 2,
    "fundamental": 3,
    "valuation": 4,
    "macro": 5,
    "subject_strategy_technical": 6,
    "smartbeta": 7,
    "deep_mined": 8,
    "llm_mined": 9,
    "warehouse_dynamic": 10,
    "secking": 11,
    "subject_parquet": 12,
    "discovered": 13,
}

SIX_CATEGORY_ORDER = ["宏观", "基本面", "技术面", "估值", "情绪", "复合因子"]

CORE_CN = {
    "ret_1": "一日收益率",
    "ret_5": "五日收益率",
    "ret_20": "二十日收益率",
    "ret_60": "六十日收益率",
    "gap_1": "一日跳空幅度",
    "range_1": "一日振幅",
    "price_pos_60": "六十日价格位置",
    "value_ep": "盈利收益率",
    "value_bp": "账面市值比",
    "value_sp": "销售市值比",
    "dividend": "股息率",
    "log_mv": "对数市值",
    "quality_roe": "净资产收益率",
    "quality_roa": "总资产收益率",
    "quality_gross_margin": "毛利率",
    "quality_asset_turn": "资产周转率",
    "quality_low_leverage": "低杠杆质量",
    "growth_revenue": "营业收入增长",
    "growth_net_profit": "净利润增长",
    "growth_operating_profit": "营业利润增长",
    "turnover": "换手率",
    "volume_ratio": "量比",
    "volume_z_20": "二十日成交量标准分",
    "amihud_20": "二十日Amihud非流动性",
    "moneyflow": "资金流强度",
    "large_flow": "大单资金流",
    "extreme_flow": "极端资金冲击",
    "vol_20": "二十日波动率",
    "down_vol_20": "二十日下行波动率",
}

SPECIAL_CN = {
    "ai_factor_composite_v1": "AI复合因子一号",
    "ai_factor_factory_v2": "AI因子工厂二号",
    "agent_moneyflow_anti_crowding_v4": "资金反拥挤代理因子四号",
    "defensive_dividend_quality_v4": "防御红利质量因子四号",
    "dividend_lowvol_quality": "红利低波质量复合因子",
    "momentum_60_minus_reversal_5": "六十日动量减五日反转",
    "moneyflow_momentum_20": "二十日资金流动量",
    "nonlinear_rank_blend_v1": "非线性排名融合因子一号",
    "small_value_profitability": "小市值价值盈利因子",
    "trend_low_vol_confirm": "趋势低波确认因子",
    "deep_rank_interaction_v4": "深度排名交互因子四号",
}

TOKEN_CN = {
    "tech": "技术",
    "technical": "技术",
    "momentum": "动量",
    "return": "收益",
    "reversal": "反转",
    "breakout": "突破",
    "amplitude": "振幅",
    "atr": "真实波幅",
    "rsi": "相对强弱",
    "ma": "均线",
    "cross": "交叉",
    "intraday": "日内",
    "strength": "强度",
    "skew": "偏度",
    "kurt": "峰度",
    "vol": "波动",
    "volatility": "波动率",
    "downside": "下行",
    "drawdown": "回撤",
    "lowvol": "低波",
    "beta": "Beta",
    "alpha": "Alpha",
    "money": "资金",
    "flow": "流",
    "netmf": "净流入",
    "large": "大单",
    "retail": "散户",
    "amount": "成交额",
    "volume": "成交量",
    "turnover": "换手",
    "liquidity": "流动性",
    "illiquidity": "非流动性",
    "share": "占比",
    "dispersion": "分散度",
    "price": "价格",
    "corr": "相关",
    "zscore": "标准分",
    "fund": "基本面",
    "fundamental": "基本面",
    "quality": "质量",
    "growth": "成长",
    "gross": "毛利",
    "margin": "利润率",
    "profitability": "盈利能力",
    "profit": "利润",
    "revenue": "收入",
    "asset": "资产",
    "assets": "资产",
    "turn": "周转",
    "leverage": "杠杆",
    "cash": "现金",
    "cashflow": "现金流",
    "accrual": "应计",
    "roe": "ROE",
    "roa": "ROA",
    "value": "价值",
    "valuation": "估值",
    "book": "账面",
    "bps": "每股净资产",
    "ep": "盈利收益率",
    "bp": "账面市值比",
    "sp": "销售市值比",
    "ocfp": "经营现金流收益率",
    "fcff": "自由现金流",
    "dividend": "股息",
    "yield": "收益率",
    "shareholder": "股东回报",
    "payout": "派息",
    "market": "市场",
    "cap": "市值",
    "capacity": "容量",
    "macro": "宏观",
    "300": "沪深300",
    "852": "中证1000",
    "905": "中证500",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def chinese_name(name: str) -> str:
    if name in CORE_CN:
        return CORE_CN[name]
    if name in SPECIAL_CN:
        return SPECIAL_CN[name]
    if has_chinese(name):
        return name.replace("_", " - ")
    lower = name.lower()
    if lower.startswith("ai_genetic_crossover_crossover_mutation"):
        return "AI遗传交叉变异因子"
    if lower.startswith("ai_mcts_tree_search_formulaic_alpha_tree"):
        return "AI-MCTS公式树Alpha因子"
    if lower.startswith("ai_openfe_feature_search_auto_feature_interaction"):
        return "AI-OpenFE自动交互因子"
    if lower.startswith("ai_deep_representation_autoencoder_light_embedding"):
        return "深度自编码轻量表征因子"
    if lower.startswith("ai_failure_memory_mutation_autoencoder_light_embedding"):
        return "失败记忆变异自编码表征因子"
    if lower.startswith("ai_failure_memory_mutation_event_fundamental_revision"):
        return "失败记忆变异事件基本面修正因子"
    if lower.startswith("ai_llm_hypothesis_cross_domain_quality_value"):
        return "LLM假设跨域质量价值因子"
    if lower.startswith("ai_llm_hypothesis_event_fundamental_revision"):
        return "LLM假设事件基本面修正因子"
    if lower.startswith("ai_llm_hypothesis_flow_anti_crowding_reversal"):
        return "LLM假设资金反拥挤反转因子"
    if lower.startswith("ai_llm_hypothesis_kline_context_trend"):
        return "LLM假设K线情境趋势因子"
    if re.fullmatch(r"technical\d+", lower):
        return f"Secking技术因子{lower.replace('technical', '')}"
    if re.fullmatch(r"fundamental\d+", lower):
        return f"Secking基本面因子{lower.replace('fundamental', '')}"
    parts = [p for p in re.split(r"[_\-\s]+", name) if p]
    cn_parts = [TOKEN_CN.get(p.lower(), p) for p in parts]
    return "".join(cn_parts)


def six_category(row: dict[str, Any]) -> str:
    name = _norm(row.get("factor_name")).lower()
    family = _norm(row.get("family_id")).lower()
    group = _norm(row.get("factor_group"))
    group_l = group.lower()
    text = f"{name} {family} {group_l}"

    if family in {"llm_mined", "deep_mined"}:
        return "复合因子"
    if "行业轮动_一级维度" in group:
        return "复合因子"
    if "macro" in text or "宏观" in group:
        return "宏观"
    if group_l.startswith("core/"):
        return {
            "core/price": "技术面",
            "core/risk": "技术面",
            "core/liquidity": "情绪",
            "core/flow": "情绪",
            "core/valuation": "估值",
            "core/quality": "基本面",
            "core/growth": "基本面",
        }.get(group_l, "复合因子")
    if "行业轮动_资金面" in group or "行业轮动_拥挤度" in group:
        return "情绪"
    if "行业轮动_估值" in group:
        return "估值"
    if "行业轮动_基本面" in group or "行业轮动_景气度" in group:
        return "基本面"
    if "行业轮动_技术面" in group:
        return "技术面"

    sentiment_keys = (
        "money", "flow", "amount", "volume", "turnover", "liquidity", "illiquidity",
        "amihud", "crowding", "netmf", "retail", "成交", "资金", "换手", "量比",
        "拥挤", "流入", "大单", "主力", "热度", "放量", "涨停",
    )
    valuation_keys = (
        "valuation", "value", "dividend", "yield", "shareholder", "payout",
        "market_cap", "log_mv", "book_to_price", "cash_to_mv", "to_mv", "ep",
        "bp", "sp", "ocfp", "估值", "股息", "红利", "市值", "盈利收益率",
    )
    fundamental_keys = (
        "fund", "fundamental", "quality", "growth", "cashflow", "roe", "roa",
        "profit", "revenue", "margin", "leverage", "asset", "assets", "accrual",
        "基本面", "景气", "质量", "成长", "利润", "收入", "资产", "现金流",
    )
    technical_keys = (
        "tech", "momentum", "reversal", "return", "ret_", "breakout", "atr",
        "amplitude", "rsi", "ma_", "skew", "kurt", "lowvol", "volatility",
        "vol", "beta", "alpha", "trend", "price", "drawdown", "技术", "动量",
        "反转", "波动", "趋势", "均线", "振幅",
    )
    if any(k in text for k in sentiment_keys):
        return "情绪"
    if any(k in text for k in valuation_keys):
        return "估值"
    if any(k in text for k in fundamental_keys):
        return "基本面"
    if any(k in text for k in technical_keys):
        return "技术面"
    return "复合因子"


def subcategory(row: dict[str, Any], category: str) -> str:
    group = _norm(row.get("factor_group"))
    group_l = group.lower()
    family = _norm(row.get("family_id")).lower()
    name = _norm(row.get("factor_name")).lower()
    core_map = {
        "core/price": "价格动量与反转",
        "core/risk": "波动风险",
        "core/liquidity": "成交流动性",
        "core/flow": "资金流",
        "core/valuation": "估值规模红利",
        "core/quality": "盈利质量",
        "core/growth": "成长",
    }
    if group_l in core_map:
        return core_map[group_l]
    if group.startswith("行业轮动_"):
        return group.replace("行业轮动_", "行业轮动-")
    smartbeta_map = {
        "low_volatility": "低波低Beta",
        "momentum": "动量",
        "quality": "质量",
        "cashflow_stability": "现金流稳定",
        "size_capacity": "规模容量",
        "value": "价值",
        "shareholder_yield": "股东回报",
    }
    if family == "smartbeta" and group_l in smartbeta_map:
        return smartbeta_map[group_l]
    llm_map = {
        "mined_expression": "LLM表达式",
        "crossover_mutation": "遗传交叉变异",
        "formulaic_alpha_tree": "MCTS公式树",
        "auto_feature_interaction": "OpenFE自动交互",
        "autoencoder_light_embedding": "自编码/失败记忆表征",
        "event_fundamental_revision": "事件基本面修正",
        "cross_domain_quality_value": "跨域质量价值",
        "flow_anti_crowding_reversal": "资金反拥挤反转",
        "kline_context_trend": "K线情境趋势",
    }
    if family in {"llm_mined", "deep_mined"} and group_l in llm_map:
        return llm_map[group_l]
    if group_l in {"technical", "trend", "momentum", "reversal"}:
        return "技术量价"
    if group_l in {"money", "money_flow", "liquidity"}:
        return "成交资金面"
    if group_l == "risk" or "vol" in name:
        return "波动风险"
    if group_l == "fundamental":
        return "财务基本面"
    if group_l == "valuation":
        return "估值"
    if group_l == "macro":
        return "宏观暴露"
    if group_l == "secking":
        return "外部精选"
    return category


def entity_type_and_location(row: dict[str, Any]) -> tuple[str, str]:
    source = _norm(row.get("source_agent"))
    path = _norm(row.get("path"))
    if source == "factor_laboratory_core":
        return "源码核心定义", str(SOURCE_ROOT / "core.py") + "::FEATURES/DOMAINS"
    if path:
        return "物料文件/源码定义", path
    if "candidate_status" in row:
        return "SQLite候选注册", str(WAREHOUSE_PATH) + "::v3_factor_candidate_registry"
    if row.get("materialized"):
        return "SQLite暴露数据", str(WAREHOUSE_PATH) + "::factor_value_daily"
    return "未物化目录项", source or "unknown"


def primary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    for row in rows:
        name = _norm(row.get("factor_name"))
        family = _norm(row.get("family_id"))
        score = (
            PRIMARY_PRIORITY.get(family, 99),
            not bool(row.get("materialized")),
            _norm(row.get("source_agent")),
            _norm(row.get("factor_group")),
        )
        if name not in best or score < best[name][0]:
            best[name] = (score, row)
    return [item[1] for item in best.values()]


def enrich(row: dict[str, Any], *, unique: bool) -> dict[str, Any]:
    category = six_category(row)
    subcat = subcategory(row, category)
    entity_type, location = entity_type_and_location(row)
    name = _norm(row.get("factor_name"))
    return {
        "因子名称中文": chinese_name(name),
        "因子名称英文/原名": name,
        "一级分类": category,
        "二级分类": subcat,
        "原始家族": _norm(row.get("family_label") or row.get("family_id")),
        "原始分组": _norm(row.get("factor_group")),
        "来源": _norm(row.get("source_agent")),
        "是否已物化": "是" if row.get("materialized") else "否",
        "是否当前正式入模": "是" if row.get("eligible_for_model") else "否",
        "实体类型": entity_type,
        "实体位置": location,
        "暴露记录数": row.get("value_count") if row.get("value_count") is not None else "",
        "最后日期": _norm(row.get("last_date")),
        "目录口径": "唯一因子名" if unique else "来源实体",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    catalog = factor_catalog.build_factor_catalog(WAREHOUSE_PATH)
    raw_rows = list(catalog["factors"])
    unique_source_rows = primary_rows(raw_rows)
    unique_rows = [enrich(row, unique=True) for row in unique_source_rows]
    entity_rows = [enrich(row, unique=False) for row in raw_rows]

    order = {name: idx for idx, name in enumerate(SIX_CATEGORY_ORDER)}
    unique_rows.sort(key=lambda r: (order.get(r["一级分类"], 99), r["二级分类"], r["因子名称中文"], r["因子名称英文/原名"]))
    entity_rows.sort(key=lambda r: (r["因子名称英文/原名"], r["来源"], r["实体位置"]))

    model29 = [row for row in unique_rows if row["是否当前正式入模"] == "是"]
    model29.sort(key=lambda r: (order.get(r["一级分类"], 99), r["二级分类"], r["因子名称中文"]))

    summary_counter: Counter[tuple[str, str]] = Counter((r["一级分类"], r["二级分类"]) for r in unique_rows)
    summary_rows = [
        {"一级分类": cat, "二级分类": sub, "因子数": count}
        for (cat, sub), count in sorted(
            summary_counter.items(),
            key=lambda kv: (order.get(kv[0][0], 99), kv[0][1]),
        )
    ]
    category_counter = Counter(r["一级分类"] for r in unique_rows)
    category_rows = [
        {"一级分类": cat, "因子数": category_counter.get(cat, 0)}
        for cat in SIX_CATEGORY_ORDER
    ]
    model_category_counter = Counter(r["一级分类"] for r in model29)
    model_category_rows = [
        {"一级分类": cat, "正式入模因子数": model_category_counter.get(cat, 0)}
        for cat in SIX_CATEGORY_ORDER
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "1_全量唯一因子六分类表.csv", unique_rows)
    write_csv(OUTPUT_DIR / "2_因子实体位置表.csv", entity_rows)
    write_csv(OUTPUT_DIR / "3_正式入模29因子表.csv", model29)
    write_csv(OUTPUT_DIR / "4_一级分类汇总表.csv", category_rows)
    write_csv(OUTPUT_DIR / "5_二级分类汇总表.csv", summary_rows)
    write_csv(OUTPUT_DIR / "6_正式入模分类汇总表.csv", model_category_rows)

    payload = {
        "status": "ok",
        "source_catalog": {
            "registered_factor_count": catalog["registered_factor_count"],
            "explicit_factor_entry_count": catalog["explicit_factor_entry_count"],
            "materialized_factor_count": catalog["materialized_factor_count"],
            "current_model_feature_count": catalog["current_model_feature_count"],
            "warehouse_path": str(WAREHOUSE_PATH),
        },
        "six_category_order": SIX_CATEGORY_ORDER,
        "unique_factor_count": len(unique_rows),
        "entity_entry_count": len(entity_rows),
        "model_29_count": len(model29),
        "category_summary": category_rows,
        "model_29_category_summary": model_category_rows,
        "unique_factors": unique_rows,
        "factor_entities": entity_rows,
        "model_29_factors": model29,
    }
    (OUTPUT_DIR / "factor_taxonomy_cn.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output_dir": str(OUTPUT_DIR),
        "unique_factor_count": len(unique_rows),
        "entity_entry_count": len(entity_rows),
        "model_29_count": len(model29),
        "category_summary": category_rows,
        "model_29_category_summary": model_category_rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
