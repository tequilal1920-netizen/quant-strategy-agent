"""Refined semantic overlay for the user's six Chinese factor buckets.

This wraps build_factor_taxonomy_cn.py and corrects path-only ambiguities:
- size/market-cap factors are valuation/size, not macro, even if stored in a
  macro folder.
- cash-flow stability belongs to fundamentals, not sentiment, despite "flow".
- money flow, turnover, volume, liquidity, and crowding remain sentiment.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


BASE_PATH = Path(__file__).with_name("build_factor_taxonomy_cn.py")
spec = importlib.util.spec_from_file_location("build_factor_taxonomy_cn_base", BASE_PATH)
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(base)

_base_six_category = base.six_category
_base_subcategory = base.subcategory


def _norm(value: Any) -> str:
    return str(value or "").strip()


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

    if any(k in text for k in ("size", "market_cap", "log_mv", "市值", "规模")):
        return "估值"
    if any(k in text for k in ("cashflow", "cash_flow", "fund", "fundamental", "quality", "growth", "roe", "roa", "profit", "revenue", "margin", "leverage", "accrual", "基本面", "景气", "质量", "成长", "利润", "收入", "资产", "现金流")):
        return "基本面"
    if any(k in text for k in ("valuation", "value", "dividend", "yield", "shareholder", "payout", "book_to_price", "cash_to_mv", "to_mv", "ep", "bp", "sp", "ocfp", "估值", "股息", "红利", "盈利收益率")):
        return "估值"
    if any(k in text for k in ("money", "netmf", "amount", "volume", "turnover", "liquidity", "illiquidity", "amihud", "crowding", "retail", "成交", "资金", "换手", "量比", "拥挤", "流入", "大单", "主力", "热度", "放量", "涨停")):
        return "情绪"
    if name.startswith("macro_") or "宏观" in group:
        return "宏观"
    if any(k in text for k in ("tech", "momentum", "reversal", "return", "ret_", "breakout", "atr", "amplitude", "rsi", "ma_", "skew", "kurt", "lowvol", "volatility", "vol", "beta", "alpha", "trend", "price", "drawdown", "技术", "动量", "反转", "波动", "趋势", "均线", "振幅")):
        return "技术面"
    return _base_six_category(row)


def subcategory(row: dict[str, Any], category: str) -> str:
    name = _norm(row.get("factor_name")).lower()
    group = _norm(row.get("factor_group")).lower()
    if category == "估值" and any(k in f"{name} {group}" for k in ("size", "market_cap", "log_mv", "市值", "规模")):
        return "规模市值"
    if category == "基本面" and "cashflow" in f"{name} {group}":
        return "现金流稳定"
    if category == "宏观" and name.startswith("macro_"):
        return "宏观暴露"
    return _base_subcategory(row, category)


base.six_category = six_category
base.subcategory = subcategory


if __name__ == "__main__":
    raise SystemExit(base.main())
