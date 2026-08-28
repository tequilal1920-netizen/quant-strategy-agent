#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
因子实验室 v2 因子库蓝图构建脚本。

目标：
1. 按“宏观、基本面、技术面、估值、情绪、复合因子”的一二级分类补齐高质量因子定义；
2. 每个因子保留中文名称、经济含义、计算逻辑、数据来源、更新频率、质量门槛和 parquet 存储约定；
3. 只写可维护的本地定义/元数据文件，不写入任何账号、密钥、口令，也不伪造外部数据。

输出：
- 因子库v2_新增高质量因子清单.csv
- 因子库v2_分类覆盖汇总.csv
- 因子库v2_完整蓝图.json
- 因子库v2_定义元数据.parquet.gzip（若本机支持 parquet 引擎）
- README_因子库v2.md
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_library_v2"
CURRENT_TAXONOMY_JSON = (
    PROJECT_ROOT
    / "output"
    / "factor_laboratory"
    / "factor_taxonomy_cn"
    / "factor_taxonomy_cn.json"
)


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    因子中文名: str
    一级分类: str
    二级分类: str
    方向: str
    更新频率: str
    数据状态: str
    数据来源: str
    parquet相对路径: str
    经济含义: str
    计算逻辑: str
    标准化处理: str
    质量门槛: str
    参考依据: str
    是否新增: str = "是"


PRIMARY_SECONDARY = {
    "宏观": ["增长", "通胀", "利率", "信用", "汇率", "流动性"],
    "基本面": ["盈利", "成长", "增长", "债务", "现金流", "景气度"],
    "技术面": ["趋势动量", "突破确认", "回撤反转", "量价确认", "波动质量", "回撤择时"],
    "估值": ["规模", "红利", "质量"],
    "情绪": ["资金", "拥挤度", "成交额"],
    "复合因子": ["LLM表达", "遗传变异", "MCTS公式树", "OpenFE交互"],
}


PRIMARY_CODE = {
    "宏观": "macro",
    "基本面": "fund",
    "技术面": "tech",
    "估值": "value",
    "情绪": "sent",
    "复合因子": "combo",
}


SECONDARY_CODE = {
    "增长": "growth",
    "通胀": "inflation",
    "利率": "rate",
    "信用": "credit",
    "汇率": "fx",
    "流动性": "liquidity",
    "盈利": "profit",
    "成长": "earn_growth",
    "债务": "debt",
    "现金流": "cashflow",
    "景气度": "prosperity",
    "趋势动量": "momentum",
    "突破确认": "breakout",
    "回撤反转": "drawdown_reversal",
    "量价确认": "volume_price",
    "波动质量": "vol_quality",
    "回撤择时": "drawdown_timing",
    "规模": "size",
    "红利": "dividend",
    "质量": "quality",
    "资金": "fund_flow",
    "拥挤度": "crowding",
    "成交额": "amount",
    "LLM表达": "llm_expr",
    "遗传变异": "genetic",
    "MCTS公式树": "mcts_tree",
    "OpenFE交互": "openfe",
}


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _factor(
    primary: str,
    secondary: str,
    short_id: str,
    name_cn: str,
    direction: str,
    frequency: str,
    status: str,
    source: str,
    logic: str,
    formula: str,
    processing: str,
    quality_gate: str,
    reference: str,
) -> FactorDefinition:
    factor_id = (
        f"{PRIMARY_CODE[primary]}_{SECONDARY_CODE[secondary]}_{_slug(short_id)}"
    )
    rel_path = (
        f"parquet_v2/{PRIMARY_CODE[primary]}/{SECONDARY_CODE[secondary]}/"
        f"{factor_id}.parquet.gzip"
    )
    return FactorDefinition(
        factor_id=factor_id,
        因子中文名=name_cn,
        一级分类=primary,
        二级分类=secondary,
        方向=direction,
        更新频率=frequency,
        数据状态=status,
        数据来源=source,
        parquet相对路径=rel_path,
        经济含义=logic,
        计算逻辑=formula,
        标准化处理=processing,
        质量门槛=quality_gate,
        参考依据=reference,
    )


COMMON_PROCESSING = (
    "按公告/交易日可见性对齐；横截面去极值、行业和市值中性可选；缺失值分行业填补；"
    "最终保存为宽表 parquet：索引为交易日，列为股票代码，值为因子暴露。"
)
COMMON_GATE = (
    "覆盖率不低于60%，单期极端值占比不高于1%，滚动RankIC方向稳定；"
    "季度复核时若ICIR、分组单调性和多空回撤同时恶化则降权或剔除。"
)
REF_BASE = "A股多因子实务、Barra/CNE 风险风格体系、券商分域选股与因子择时报告、LLM/MCTS因子挖掘报告"


def macro_factors() -> Iterable[FactorDefinition]:
    p = "宏观"
    source_macro = "本地macro_monthly_cn、macro_quarterly_cn、macro_rate_cn、指数行情；外部源仅用于补齐尚缺字段"
    yield _factor(
        p,
        "增长",
        "pmi_expansion_beta_120d",
        "PMI扩张敏感度",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "经济从收缩转扩张时，订单、制造、周期链更敏感的股票通常提前反应盈利修复。",
        "先计算制造业PMI相对50的扩张强度及其三个月变化，再用股票近120个交易日收益对该宏观冲击做滚动敏感度估计。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "增长",
        "gdp_gap_up_capture",
        "GDP增长缺口上行捕获",
        "正向",
        "季度",
        "可本地物化",
        source_macro,
        "增长超预期阶段，高经营杠杆或顺周期公司对增长缺口更敏感。",
        "用实际GDP同比减去八季滚动中枢形成增长缺口，统计缺口上行季度内个股相对市场收益的平均捕获率。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "增长",
        "non_mfg_diffusion_exposure",
        "非制造业景气扩散暴露",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "服务消费与建筑链景气扩散会改变行业盈利边际，个股收益存在景气扩散敏感度差异。",
        "将非制造业PMI与综合PMI的同步扩散变化作为宏观脉冲，估计个股滚动相关与上行分位收益。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "增长",
        "growth_regime_relative_strength",
        "增长状态相对强弱",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "在增长上行状态中，相对强势且回撤受控的股票更可能是景气验证标的。",
        "以PMI、GDP、社融增速构造增长状态评分，再乘以个股相对行业六十日强弱并惩罚同期回撤。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "通胀",
        "cpi_surprise_beta",
        "CPI意外敏感度",
        "双向",
        "月度",
        "可本地物化",
        source_macro,
        "温和通胀利于定价权企业，恶性通胀压制估值；关键在个股对通胀意外的历史反应。",
        "CPI同比减十二月滚动预期中枢得到通胀意外，回归个股相对收益得到敏感度，并按通胀区间分段记录方向。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "通胀",
        "ppi_margin_squeeze_resilience",
        "PPI成本挤压韧性",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "上游成本上升时，毛利率稳定或股价不受挤压的公司往往具备议价权。",
        "用PPI同比上行阶段中个股毛利率变化和相对收益韧性合成，毛利率不降且收益强者得分更高。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "通胀",
        "cpi_ppi_scissor_pricing_power",
        "CPI-PPI剪刀差定价权",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "CPI强于PPI通常代表下游售价改善快于成本，利好消费和品牌定价权公司。",
        "构造CPI同比减PPI同比的剪刀差，叠加公司毛利率稳定性与行业相对收益，衡量通胀传导能力。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "通胀",
        "commodity_inflation_beta",
        "商品通胀贝塔",
        "双向",
        "日度/周度",
        "需外部权威源补齐",
        "Wind/米筐/同花顺商品指数或期货连续合约，本地期货表若已更新可直接接入",
        "原材料价格变化对上游资源和中下游制造的影响方向不同，需以股票历史收益验证暴露方向。",
        "对能源、金属、农产品篮子收益做主成分，估计个股对商品通胀主成分的滚动贝塔和上行捕获率。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "利率",
        "long_rate_change_beta",
        "长端利率变化敏感度",
        "反向",
        "日度/周度",
        "可本地物化",
        source_macro,
        "长端利率上行通常压缩高久期成长估值，低估值和金融链条反应不同。",
        "用十年期国债或同期限利率变动作为冲击，滚动估计个股相对收益敏感度，利率上行受损少者得分更高。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "利率",
        "term_spread_cycle_exposure",
        "期限利差周期暴露",
        "双向",
        "日度/周度",
        "可本地物化",
        source_macro,
        "期限利差反映增长预期与货币政策组合，银行、地产、成长板块敏感度差异明显。",
        "用长端利率减短端利率构造期限利差，估计个股对利差变化的分段贝塔，并按行业中性化。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "利率",
        "real_rate_pressure",
        "实际利率压力暴露",
        "反向",
        "月度",
        "可本地物化",
        source_macro,
        "实际利率越高，现金流远期化资产折现压力越大，盈利当期兑现公司相对占优。",
        "以长端利率减CPI同比形成实际利率压力，叠加个股估值久期代理和历史利率贝塔计算暴露。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "利率",
        "rate_down_duration_winner",
        "降息久期受益",
        "正向",
        "日度/周度",
        "可本地物化",
        source_macro,
        "利率下行阶段，高成长、高估值但基本面兑现不差的久期资产更容易修复。",
        "在利率下行窗口统计个股相对收益弹性，并用ROE和现金流质量过滤纯题材高久期。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "信用",
        "social_financing_impulse_beta",
        "社融脉冲敏感度",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "社融扩张改善信用环境，利好融资敏感和需求弹性行业，但劣质高杠杆公司需过滤。",
        "用社融存量同比变化和新增社融滚动斜率构造信用脉冲，回归个股相对收益得到敏感度，并惩罚高杠杆。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "信用",
        "m1_m2_credit_trend",
        "M1-M2信用活化趋势",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "M1相对M2改善代表企业活期资金和经营活跃度提升，通常有助风险偏好恢复。",
        "计算M1同比减M2同比的三个月变化，乘以个股顺周期贝塔与成交活跃改善，得到信用活化暴露。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "信用",
        "credit_tightening_resilience",
        "信用收缩韧性",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "信用收缩时，低债务、现金流充足、融资依赖低的公司抗风险更强。",
        "在社融脉冲下行窗口，综合相对收益、经营现金流/负债、短期偿债能力，越稳健得分越高。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "信用",
        "credit_spread_proxy_beta",
        "信用利差代理敏感度",
        "反向",
        "周度/月度",
        "需外部权威源补齐",
        "Wind/中债信用利差曲线；本地若新增债券利差表可直接切换",
        "信用利差走阔通常代表违约风险和融资成本上行，高负债企业承压。",
        "用AAA/AA产业债信用利差变化估计个股相对收益贝塔，并结合资产负债率做风险过滤。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "汇率",
        "rmb_depreciation_beta",
        "人民币贬值敏感度",
        "双向",
        "日度/周度",
        "需外部权威源补齐",
        "Wind/同花顺/米筐 USD-CNY、CFETS人民币指数",
        "汇率变化影响出口收入、进口成本和外资风险偏好，必须按行业和历史收益识别方向。",
        "对人民币汇率变化估计个股相对收益滚动贝塔，并按出口链、进口成本链、外资持仓敏感行业分层。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "汇率",
        "cfets_fx_pressure_exposure",
        "CFETS汇率压力暴露",
        "双向",
        "周度",
        "需外部权威源补齐",
        "Wind/同花顺 CFETS人民币汇率指数",
        "一篮子汇率比单一美元汇率更能反映贸易竞争力与外资风险偏好。",
        "用CFETS指数周度变化估计个股收益敏感度，并结合行业出口属性和毛利率稳定性校验。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "汇率",
        "fx_volatility_risk",
        "汇率波动风险暴露",
        "反向",
        "周度",
        "需外部权威源补齐",
        "Wind/同花顺/米筐汇率及波动率数据",
        "汇率波动扩大时，外资风险偏好下降、外币债务和进口成本不确定性上升。",
        "计算人民币汇率二十日波动率，并估计个股在汇率波动扩张期的相对收益损失。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "汇率",
        "fx_risk_on_capture",
        "汇率风险偏好捕获",
        "正向",
        "周度",
        "需外部权威源补齐",
        "Wind/同花顺/米筐汇率、北向资金或跨境资金数据",
        "人民币升值与外资流入共振时，外资偏好资产更易获得估值修复。",
        "识别人民币升值且跨境资金流入窗口，统计个股相对收益捕获率，并与外资持仓或大盘成长暴露交叉验证。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "流动性",
        "market_liquidity_beta",
        "市场流动性贝塔",
        "正向",
        "日度/周度",
        "可本地物化",
        source_macro,
        "全市场成交和风险偏好改善时，高弹性股票收益更强；流动性退潮时需规避拥挤高波动。",
        "用全市场成交额、换手率和上涨家数构造流动性因子，估计个股滚动敏感度并剔除极端拥挤。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "流动性",
        "money_growth_liquidity_gap",
        "货币流动性缺口暴露",
        "正向",
        "月度",
        "可本地物化",
        source_macro,
        "货币供给改善但实体融资未完全跟上时，股票估值可能先受益于流动性。",
        "用M2同比、M1同比和社融同比构造货币-信用缺口，并乘以个股估值久期和历史流动性贝塔。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "流动性",
        "rate_liquidity_composite",
        "利率流动性共振",
        "正向",
        "周度/月度",
        "可本地物化",
        source_macro,
        "利率下行且成交修复时，权益估值扩张概率更高；单一指标容易误判。",
        "将长端利率下行、M1-M2改善和市场成交扩张三个信号标准化求稳健均值，再映射到个股弹性。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "流动性",
        "liquidity_regime_up_capture",
        "流动性上行捕获率",
        "正向",
        "日度/周度",
        "可本地物化",
        source_macro,
        "流动性宽松时能够放大收益、收缩时回撤不失控的股票，更适合进入增强组合。",
        "在流动性状态评分上行的窗口计算个股相对市场收益均值，同时用下行窗口回撤作惩罚。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )


def fundamental_factors() -> Iterable[FactorDefinition]:
    p = "基本面"
    source = "本地financial_report_visible、stock_financial_report、行业映射、行情估值表"
    yield _factor(
        p,
        "盈利",
        "roe_stability_ttm",
        "ROE稳定盈利",
        "正向",
        "季度",
        "可本地物化",
        source,
        "持续稳定的ROE比单期高ROE更能代表商业模式和护城河，能降低财报噪声。",
        "使用最近八个可见报告期ROE均值减波动惩罚，且要求最近一期ROE不显著低于历史中枢。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "盈利",
        "gross_margin_improvement_quality",
        "毛利率改善质量",
        "正向",
        "季度",
        "可本地物化",
        source,
        "毛利率改善若伴随收入增长，通常代表产品力或成本效率改善，而非单纯费用压缩。",
        "最近四期毛利率同比变化与收入同比共同为正加分；若收入下滑而毛利率上升则降权。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "盈利",
        "roa_cash_confirmed",
        "现金确认ROA",
        "正向",
        "季度",
        "可本地物化",
        source,
        "资产回报如果能被经营现金流确认，盈利质量更高，财务操纵风险更低。",
        "ROA乘以经营现金流净额/归母净利润的稳健截尾值，现金流为负或利润现金不匹配时显著惩罚。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "盈利",
        "dupont_operating_efficiency",
        "杜邦经营效率",
        "正向",
        "季度",
        "可本地物化",
        source,
        "ROE来源中，周转率与净利率改善通常比单纯杠杆抬升更稳健。",
        "将净利率改善、总资产周转率改善、权益乘数变化拆解，奖励经营驱动ROE，惩罚杠杆驱动ROE。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "成长",
        "revenue_growth_acceleration",
        "收入成长加速度",
        "正向",
        "季度",
        "可本地物化",
        source,
        "成长股重在边际加速，收入增速从低位改善往往领先利润释放。",
        "最近一期收入同比减过去四期收入同比均值，并要求环比趋势不恶化，按行业内排序。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "成长",
        "profit_growth_acceleration",
        "利润成长加速度",
        "正向",
        "季度",
        "可本地物化",
        source,
        "利润增速加速代表经营拐点或景气兑现，但需过滤低基数假增长。",
        "归母净利润同比加速度减低基数惩罚；若利润绝对值过小或连续亏损则降权。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "成长",
        "growth_stability_score",
        "成长稳定度",
        "正向",
        "季度",
        "可本地物化",
        source,
        "长期稳定增长比单季暴冲更可持续，适合质量成长类组合。",
        "最近八期收入和利润同比的均值、胜率、波动率合成；均值高且波动低者得分高。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "成长",
        "growth_realization_rate",
        "成长兑现率",
        "正向",
        "季度",
        "需外部权威源补齐",
        "Wind/同花顺/CSMAR一致预期与公告实际值，本地预期表接入后可物化",
        "市场更奖励实际业绩超出预期而非单纯预期高的公司。",
        "实际收入或利润增速减公告前一致预期增速，滚动统计兑现率与兑现稳定性。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "增长",
        "asset_expansion_efficiency",
        "资产扩张效率",
        "正向",
        "季度",
        "可本地物化",
        source,
        "资产扩张只有转化为收入或利润增长才有价值，盲目扩张会稀释回报。",
        "营收增速减总资产增速，并结合ROA变化，资产扩张但回报下降者降权。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "增长",
        "operating_leverage_release",
        "经营杠杆释放",
        "正向",
        "季度",
        "可本地物化",
        source,
        "收入恢复时固定成本摊薄会带来利润弹性，是景气拐点中的重要信号。",
        "利润同比增速减收入同比增速，并要求毛利率或费用率不显著恶化，得到经营杠杆释放程度。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "增长",
        "cash_driven_growth",
        "现金驱动增长",
        "正向",
        "季度",
        "可本地物化",
        source,
        "收入利润增长如果被现金流同步确认，更可能是真实需求扩张。",
        "收入同比、利润同比、经营现金流同比三者稳健均值；任一维度显著背离则降低得分。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "增长",
        "revenue_quality_growth",
        "收入质量增长",
        "正向",
        "季度",
        "可本地物化",
        source,
        "高质量收入增长应避免应收账款和存货异常堆积。",
        "收入增长减应收账款增长和存货增长的异常占用惩罚，行业内标准化。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "债务",
        "leverage_improvement",
        "杠杆改善",
        "正向",
        "季度",
        "可本地物化",
        source,
        "资产负债率下降且盈利不恶化代表风险释放，尤其在信用收缩期有效。",
        "最近一期资产负债率同比下降幅度与ROE稳定性合成，盈利下滑式降杠杆不加分。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "债务",
        "short_term_solvency_buffer",
        "短期偿债缓冲",
        "正向",
        "季度",
        "可本地物化",
        source,
        "短期偿债能力强的公司在流动性收紧或盈利下行中抗风险更强。",
        "流动比率、速动比率、现金比率做稳健排序，叠加经营现金流为正约束。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "债务",
        "debt_to_cashflow_pressure",
        "债务现金流压力",
        "反向",
        "季度",
        "可本地物化",
        source,
        "债务规模相对于经营现金流过高，意味着再融资或偿债压力。",
        "总负债除以经营现金流净额的稳健值；现金流为负且负债高的公司风险得分最高。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "债务",
        "equity_multiplier_risk",
        "权益乘数风险",
        "反向",
        "季度",
        "可本地物化",
        source,
        "ROE若主要由高杠杆驱动，盈利质量和抗周期能力偏弱。",
        "总资产/归母权益作为权益乘数，结合ROA低位和资产负债率上行共同惩罚。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "现金流",
        "ocf_profit_match",
        "经营现金流利润匹配",
        "正向",
        "季度",
        "可本地物化",
        source,
        "利润能转化为现金说明回款和盈利质量较好。",
        "经营现金流净额/归母净利润，截尾后与利润为正约束共同打分。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "现金流",
        "free_cashflow_proxy_yield",
        "自由现金流代理收益率",
        "正向",
        "季度",
        "可本地物化",
        source,
        "自由现金流相对市值越高，公司自我造血和股东回报潜力越强。",
        "经营现金流净额减投资现金流流出代理自由现金流，再除以总市值并行业中性化。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "现金流",
        "working_capital_occupation",
        "营运资本占用改善",
        "正向",
        "季度",
        "可本地物化",
        source,
        "应收和存货占用下降说明回款、库存和渠道质量改善。",
        "应收账款加存货占收入的比例同比下降越多越好，同时剔除收入大幅下滑造成的被动改善。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "现金流",
        "ocf_stability",
        "经营现金流稳定度",
        "正向",
        "季度",
        "可本地物化",
        source,
        "经营现金流持续为正且波动较低，代表商业模式抗波动能力。",
        "最近八期经营现金流/资产的均值、胜率和波动率合成，现金流断崖式下滑降权。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )

    yield _factor(
        p,
        "景气度",
        "industry_profit_diffusion",
        "行业盈利扩散",
        "正向",
        "季度",
        "可本地物化",
        source,
        "同一行业内盈利改善公司占比上升，通常说明景气不是个别公司偶然现象。",
        "按行业统计ROE、利润增速、毛利率改善的公司占比，映射回行业成分股。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "景气度",
        "industry_revenue_diffusion",
        "行业收入扩散",
        "正向",
        "季度",
        "可本地物化",
        source,
        "收入扩散度能更早反映需求景气，适合周期和消费链。",
        "按行业统计收入同比为正且加速的公司比例，结合行业相对收益验证。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "景气度",
        "earnings_revision_breadth",
        "盈利预期上修广度",
        "正向",
        "周度/月度",
        "需外部权威源补齐",
        "Wind/同花顺/CSMAR一致预期、研报预测数据",
        "分析师盈利上修广度是景气度确认的重要信号，常用于行业和个股增强。",
        "统计近三十日一致预期EPS或净利润上修次数占比及上修幅度，行业内标准化。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )
    yield _factor(
        p,
        "景气度",
        "announcement_surprise_proxy",
        "财报超预期代理",
        "正向",
        "季度",
        "需外部权威源补齐",
        "本地公告实际值叠加外部一致预期；无预期时只保留定义不物化",
        "财报相对市场预期的偏离比绝对增速更能解释公告后收益。",
        "用公告实际利润减公告前一致预期利润，并结合公告后三日异常收益作稳健确认。",
        COMMON_PROCESSING,
        COMMON_GATE,
        REF_BASE,
    )


def technical_factors() -> Iterable[FactorDefinition]:
    p = "技术面"
    source = "本地stock_ohlcv_daily、stock_valuation_daily、stock_moneyflow_daily、现有行情parquet"
    yield _factor(p, "趋势动量", "twelve_minus_one_momentum", "12减1月动量", "正向", "日度", "可本地物化", source, "中期趋势延续是A股常见风格收益来源，剔除最近一月可降低短期反转噪声。", "过去十二个月累计收益减最近一个月收益，停牌和涨跌停不可交易日做可交易性过滤。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "趋势动量", "six_minus_one_momentum", "6减1月动量", "正向", "日度", "可本地物化", source, "六个月趋势更贴近季度调仓和景气交易节奏。", "过去六个月累计收益减最近一个月收益，按行业和市值中性后保留残差动量。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "趋势动量", "risk_adjusted_momentum", "风险调整动量", "正向", "日度", "可本地物化", source, "同样涨幅下，低波动、低回撤的动量质量更高。", "中期收益除以同期下行波动与最大回撤的加权惩罚，得到趋势质量分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "趋势动量", "residual_momentum", "行业市值残差动量", "正向", "日度", "可本地物化", source, "残差动量剔除了行业和市值共振，更接近个股alpha趋势。", "对个股收益回归市场、行业和市值收益，取残差收益的三至十二月累积值。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "突破确认", "high_breakout_60d", "六十日新高突破", "正向", "日度", "可本地物化", source, "有效突破代表资金愿意在高位继续定价，常用于趋势确认。", "收盘价距离六十日高点的分位，突破日若成交额放大且非一字涨停则加分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "突破确认", "volume_confirmed_breakout", "放量突破确认", "正向", "日度", "可本地物化", source, "价升量增的突破比缩量突破更可靠，能过滤假突破。", "价格突破二十/六十日高点时，成交额相对二十日均值放大且换手不过热者得分更高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "突破确认", "ma_alignment_strength", "均线多头排列强度", "正向", "日度", "可本地物化", source, "均线由短到长向上排列代表趋势结构稳定。", "五、二十、六十、一百二十日均线斜率和排列一致性打分，并惩罚乖离过高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "突破确认", "gap_followthrough_quality", "跳空延续质量", "正向", "日度", "可本地物化", source, "强消息驱动后的跳空若能延续而非回补，说明资金认可度较高。", "跳空上涨后五日收益、成交保持和回补幅度综合打分；涨停不可买入情形降低可执行分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "回撤反转", "short_reversal_after_drawdown", "回撤后短反转", "正向", "日度", "可本地物化", source, "过度下跌后的短期反转常来自流动性冲击修复，但需避免基本面恶化。", "二十日跌幅分位较低且近三日企稳，结合成交缩量和无重大下跌跳空得到反转分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "回撤反转", "low_rebound_60d", "六十日低位修复", "正向", "日度", "可本地物化", source, "从阶段低位放量修复可能对应悲观预期缓和。", "价格距离六十日低点的修复幅度、低位停留时间和成交确认合成，过度追高降权。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "回撤反转", "oversold_rebound_quality", "超跌反弹质量", "正向", "日度", "可本地物化", source, "超跌反弹只有在波动收敛和资金回流时更可持续。", "短期RSI或收益分位低位后反弹，叠加下行波动下降和资金净流入确认。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "回撤反转", "drawdown_stabilization", "回撤止跌稳定度", "正向", "日度", "可本地物化", source, "深回撤后如果价格波动收敛且低点抬高，反转成功率更高。", "近二十日最大回撤、低点抬升次数、波动率下降三者合成，继续创新低者惩罚。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "量价确认", "obv_strength", "能量潮强度", "正向", "日度", "可本地物化", source, "价格上涨若伴随成交能量累积，趋势确认度更高。", "根据涨跌日成交量累加能量潮，取其斜率和价格斜率的一致性。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "量价确认", "price_volume_corr", "价量相关确认", "正向", "日度", "可本地物化", source, "价量正相关说明上涨时有资金承接，下跌时成交萎缩则抛压较轻。", "二十至六十日收益率与成交额变化的滚动相关，结合趋势方向确定正负含义。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "量价确认", "flow_price_consistency", "资金价格一致性", "正向", "日度", "可本地物化", source, "资金净流入与价格上涨同步，比单独资金流更可靠。", "大单/超大单净流入强度与同期超额收益同向时加分，背离时降权。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "量价确认", "price_volume_divergence_repair", "价量背离修复", "正向", "日度", "可本地物化", source, "上涨缩量或下跌放量后的修复能识别真假趋势。", "识别价涨量缩、价跌量增背离，若后续五日成交与价格重新一致则给修复分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "波动质量", "downside_volatility_low", "低下行波动", "正向", "日度", "可本地物化", source, "相比总波动，下行波动更直接刻画持仓痛感和风险溢价。", "计算近六十日负收益样本波动率，取反向分数并与收益趋势合成。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "波动质量", "idiosyncratic_volatility_low", "低特质波动", "正向", "日度", "可本地物化", source, "高特质波动常对应噪声交易和彩票偏好，低特质波动更稳健。", "用市场和行业收益解释个股收益，残差波动率越低得分越高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "波动质量", "volatility_compression_break", "波动压缩突破", "正向", "日度", "可本地物化", source, "长期缩量低波后的放量突破常代表新趋势启动。", "近二十日波动处于一年低分位，同时价格向上突破中期均线且成交确认。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "波动质量", "volatility_skew_quality", "波动偏度质量", "正向", "日度", "可本地物化", source, "收益分布若右尾多、左尾少，持仓赔率更优。", "近六十日收益偏度、下行尾部损失和上涨日波动贡献合成。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "回撤择时", "max_drawdown_position", "最大回撤位置", "正向", "日度", "可本地物化", source, "最大回撤越接近历史尾部且后续修复越慢，趋势风险越高；已修复则风险下降。", "统计六十日最大回撤发生位置、当前距离高点和修复比例，形成择时风险分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "回撤择时", "drawdown_recovery_slope", "回撤修复斜率", "正向", "日度", "可本地物化", source, "回撤后修复越快，说明资金承接和基本面信心更强。",
        "最大回撤后至今的价格斜率除以下跌斜率，结合成交缩放过滤反抽。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "回撤择时", "trend_drawdown_stop", "趋势回撤止损质量", "正向", "日度", "可本地物化", source, "强趋势中的小幅有序回撤比无回撤冲高更可持续。", "在中期趋势为正的股票中，统计回撤深度、均线支撑和成交萎缩，回撤有序者得分高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "回撤择时", "high_level_pullback_quality", "高位回踩质量", "正向", "日度", "可本地物化", source, "突破后回踩不破关键位置且缩量，常是趋势二次上车点。", "价格处于一年高分位后回踩二十/六十日均线，若缩量、未破位、随后反包则加分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)


def valuation_factors() -> Iterable[FactorDefinition]:
    p = "估值"
    source = "本地stock_valuation_daily、stock_market_daily、财报可见表"
    yield _factor(p, "规模", "free_float_size_residual", "自由流通规模残差", "反向", "日度", "可本地物化", source, "小市值效应需要剔除行业差异和流动性陷阱，避免买入不可交易小票。", "取自由流通市值对行业和流动性回归后的残差，低残差且成交可承载者得分高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "规模", "small_liquid_quality", "小市值流动质量", "正向", "日度", "可本地物化", source, "小市值中有足够流动性和低冲击成本的标的更适合策略执行。", "小市值分数乘以成交额稳定性，并惩罚停牌、涨跌停和Amihud冲击成本。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "规模", "size_reversal_after_crowding", "拥挤后规模反转", "正向", "周度", "可本地物化", source, "大小盘风格拥挤到极端后常出现均值回归，需要和资金拥挤状态结合。", "计算小盘相对大盘收益分位与拥挤度，极端拥挤后反向配置得分更高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "红利", "dividend_yield_quality", "高股息质量", "正向", "日度/季度", "可本地物化", source, "高股息若伴随现金流和盈利稳定，防御属性更强；单纯高股息可能是价值陷阱。", "股息率与经营现金流稳定、ROE稳定合成，盈利恶化或派息不可持续者降权。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "红利", "dividend_stability", "分红稳定度", "正向", "年度/季度跟踪", "需外部权威源补齐",
        "Wind/同花顺/CSMAR分红实施明细；若本地分红表补齐可物化", "长期连续分红体现现金流纪律和股东回报意愿。", "统计过去三至五年是否连续分红、分红率波动和股息率分位，形成稳定度评分。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "红利", "payout_cash_coverage", "分红现金覆盖", "正向", "年度/季度跟踪", "需外部权威源补齐",
        "分红明细与本地经营现金流数据", "分红必须由现金流覆盖才可持续，否则高股息不可复制。", "现金分红总额除以经营现金流，适中且稳定者得分高，超过现金流过多者降权。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "质量", "quality_adjusted_ep", "质量调整盈利收益率", "正向", "日度/季度", "可本地物化", source, "低估值只有在盈利质量较好时才更可能获得重估。", "盈利收益率乘以ROE稳定、现金流匹配和杠杆稳健评分，过滤利润质量差的低PE。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "质量", "pb_roe_residual_value", "PB-ROE残差价值", "正向", "日度/季度", "可本地物化", source, "同等ROE下PB越低越有估值性价比，是质量价值常用框架。", "横截面用PB解释ROE或用ROE解释PB，取被低估的残差部分并行业中性化。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "质量", "value_trap_avoidance", "价值陷阱过滤", "正向", "日度/季度", "可本地物化", source, "低估值伴随盈利下滑、现金流恶化和高债务时常是价值陷阱。", "将低PE/PB/SP与盈利趋势、现金流和杠杆风险交叉，低估且基本面不恶化者得分高。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)


def sentiment_factors() -> Iterable[FactorDefinition]:
    p = "情绪"
    source = "本地stock_moneyflow_daily、stock_valuation_daily、stock_ohlcv_daily、新闻事件表"
    yield _factor(p, "资金", "large_order_persistence", "大单净流入持续性", "正向", "日度", "可本地物化", source, "持续的大单净流入比单日异动更可能代表机构资金行为。", "大单和超大单净流入占成交额的二十日均值、胜率和衰减权重合成。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "资金", "main_flow_acceleration", "主力资金加速度", "正向", "日度", "可本地物化", source, "资金流从边际改善到持续转正，常领先价格趋势确认。", "近五日净流入强度减近二十日均值，并要求价格未大幅透支。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "资金", "flow_reversal_filter", "资金反转过滤", "正向", "日度", "可本地物化", source, "价格下跌但资金开始回补时可能反转，价格上涨但资金流出则需警惕。", "比较短期价格收益与资金净流入方向，识别资金先行修复并过滤出货式上涨。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "拥挤度", "turnover_crowding_percentile", "换手拥挤分位", "反向", "日度", "可本地物化", source, "过高换手常代表交易拥挤和短线资金博弈，后续回撤风险上升。", "计算个股换手率相对自身一年历史分位和行业分位，极端高位作为拥挤惩罚。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "拥挤度", "volume_ratio_overheat", "量比过热风险", "反向", "日度", "可本地物化", source, "量比异常放大若没有趋势和资金确认，容易对应短期顶部或消息扰动。", "量比、成交额分位和当日振幅共同识别过热，并用后续价格确认校准方向。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "拥挤度", "limit_up_heat_decay", "涨停热度衰减", "反向", "日度", "可本地物化", source, "涨停过度拥挤后收益分布偏脆弱，尤其在板块热度衰减期。", "统计近十日涨停触及次数、封板质量和板块热度变化，热度衰减且拥挤高者降权。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)

    yield _factor(p, "成交额", "amount_shock_quality", "成交额冲击质量", "正向", "日度", "可本地物化", source, "成交额突然放大只有在价格结构健康时才是增量资金，否则可能是分歧出货。",
        "成交额相对二十日均值的冲击，叠加收益方向、振幅和资金净流入判断质量。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "成交额", "liquidity_improvement", "流动性改善", "正向", "日度", "可本地物化", source, "成交额稳步改善可降低交易冲击，也常伴随关注度提升。", "成交额二十日均值相对六十日均值改善，同时价格未大幅透支且波动不过热。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)
    yield _factor(p, "成交额", "amihud_liquidity_premium", "低冲击流动性溢价", "正向", "日度", "可本地物化", source, "同等收益下成交额越能承载交易，组合换仓成本越低。",
        "用绝对收益率除以成交额计算价格冲击，取反向分数并结合成交稳定性。", COMMON_PROCESSING, COMMON_GATE, REF_BASE)


def composite_factors() -> Iterable[FactorDefinition]:
    p = "复合因子"
    source = "本地基础因子宽表、LLM/MCTS/遗传规划/OpenFE表达式注册表；外部源只做候选解释和预期数据补齐"
    composite_gate = (
        "表达式复杂度受限；滚动训练外样本RankIC为正；分组单调性通过；"
        "置换检验和行业/市值中性检验通过后才进入候选池。"
    )
    yield _factor(p, "LLM表达", "llm_quality_value_momentum", "LLM质量价值动量表达", "正向", "季度生成/月度验证", "表达式待物化", source, "LLM把质量、估值和趋势组合成人能解释的低复杂度公式，避免黑箱堆叠。",
        "由LLM生成“盈利质量高、估值不贵、趋势未过热”的公式树，经语法检查、单因子检验、滚动外样本筛选后保留。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "LLM表达", "llm_flow_drawdown_repair", "LLM资金回撤修复表达", "正向", "季度生成/月度验证", "表达式待物化", source, "把资金先行、价格回撤和波动收敛合成，用于识别反转质量。",
        "LLM在资金、回撤、波动类原子因子上生成低阶表达式，并用回撤后收益和IC稳定性作为反馈修正。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "LLM表达", "llm_macro_style_switch", "LLM宏观风格切换表达", "双向", "月度", "表达式待物化", source, "将宏观状态映射到价值、成长、红利、动量等风格权重。",
        "LLM根据宏观状态标签生成风格门控公式，实证层用滚动外样本检验决定是否启用。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "LLM表达", "llm_expectation_revision_quality", "LLM预期修正质量表达", "正向", "周度/月度", "需外部权威源补齐", "一致预期、研报预测、公告实际值和基础因子表",
        "预期上修若被财务质量和价格确认支持，信号更强。",
        "LLM生成预期上修、兑现率、价格确认和估值约束的组合表达式；无预期表时只登记不物化。", COMMON_PROCESSING, composite_gate, REF_BASE)

    yield _factor(p, "遗传变异", "gp_value_profit_mutation", "遗传价值盈利变异", "正向", "季度生成/月度验证", "表达式待物化", source, "遗传规划通过交叉和变异寻找非线性但可解释的价值盈利组合。",
        "以EP、BP、ROE、现金流匹配为原子，进行交叉、变异、复杂度惩罚和外样本IC筛选。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "遗传变异", "gp_momentum_volatility_mutation", "遗传动量波动变异", "正向", "季度生成/月度验证", "表达式待物化", source, "动量因子经过波动和回撤质量修正后更稳定。",
        "以中期动量、下行波动、最大回撤、突破确认因子为原子，遗传搜索收益/风险比更优表达式。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "遗传变异", "gp_flow_crowding_mutation", "遗传资金拥挤变异", "正向", "季度生成/月度验证", "表达式待物化", source, "资金流入需要排除交易拥挤，遗传变异可寻找阈值型关系。",
        "以净流入、换手拥挤、量比、成交额冲击为原子，搜索资金确认与过热惩罚的非线性组合。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "遗传变异", "gp_macro_fundamental_mutation", "遗传宏观基本面变异", "双向", "季度生成/月度验证", "表达式待物化", source, "宏观状态对同一基本面因子的有效性有条件依赖。",
        "将宏观状态分数与盈利、成长、债务、现金流原子交叉，遗传搜索不同状态下的稳健组合。", COMMON_PROCESSING, composite_gate, REF_BASE)

    yield _factor(p, "MCTS公式树", "mcts_quality_growth_tree", "MCTS质量成长公式树", "正向", "季度生成/月度验证", "表达式待物化", source, "MCTS按收益反馈逐步扩展公式树，适合在大搜索空间里找到可解释组合。",
        "根节点为质量成长目标，动作包括加减乘除、排名、滚动均值、条件门控；奖励为外样本ICIR减复杂度惩罚。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "MCTS公式树", "mcts_reversal_timing_tree", "MCTS反转择时公式树", "正向", "季度生成/月度验证", "表达式待物化", source, "反转信号依赖市场状态和回撤结构，树搜索能自动寻找触发条件。",
        "在回撤、波动、成交、资金原子上扩展条件树，用分域外样本多空收益作为奖励。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "MCTS公式树", "mcts_macro_gate_tree", "MCTS宏观门控公式树", "双向", "月度/季度", "表达式待物化", source, "因子有效性随宏观环境变化，门控树用于决定不同状态下的因子权重。",
        "把增长、通胀、利率、信用、流动性状态作为条件节点，输出基础因子的动态权重。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "MCTS公式树", "mcts_execution_aware_tree", "MCTS执行友好公式树", "正向", "季度生成/月度验证", "表达式待物化", source, "高IC但高换手高冲击的因子无法转化为真实收益，公式树内置成本惩罚。",
        "奖励函数使用换手后多空收益、最大回撤、成交承载和持仓稳定性，搜索可交易表达式。", COMMON_PROCESSING, composite_gate, REF_BASE)

    yield _factor(p, "OpenFE交互", "openfe_value_quality_cross", "OpenFE价值质量交互", "正向", "季度生成/月度验证", "表达式待物化", source, "机器构造的二阶交互可发现人工低估的价值质量组合。",
        "对估值、ROE、现金流、杠杆原子做二阶交互，使用滚动外样本筛选和相关性去冗余。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "OpenFE交互", "openfe_momentum_liquidity_cross", "OpenFE动量流动性交互", "正向", "季度生成/月度验证", "表达式待物化", source, "趋势收益能否兑现受成交承载和拥挤状态影响。",
        "自动生成动量、成交额、换手、资金流之间的乘积、比值、条件排名交互，并以成本后收益筛选。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "OpenFE交互", "openfe_fundamental_macro_cross", "OpenFE基本面宏观交互", "双向", "月度/季度", "表达式待物化", source, "同一财务因子在不同宏观状态中的方向和强度不同。",
        "宏观状态分数与盈利、成长、债务、现金流因子自动交互，筛选跨状态稳健或状态专属信号。", COMMON_PROCESSING, composite_gate, REF_BASE)
    yield _factor(p, "OpenFE交互", "openfe_sentiment_reversal_cross", "OpenFE情绪反转交互", "正向", "季度生成/月度验证", "表达式待物化", source, "资金、拥挤和回撤反转之间存在强条件非线性。",
        "自动搜索资金回流、拥挤降温、回撤修复、波动收敛之间的交互表达式，保留低相关高胜率因子。", COMMON_PROCESSING, composite_gate, REF_BASE)


def build_new_definitions() -> list[FactorDefinition]:
    definitions: list[FactorDefinition] = []
    for factory in (
        macro_factors,
        fundamental_factors,
        technical_factors,
        valuation_factors,
        sentiment_factors,
        composite_factors,
    ):
        definitions.extend(factory())
    return definitions


def load_existing_factor_count() -> tuple[int, list[dict]]:
    if not CURRENT_TAXONOMY_JSON.exists():
        return 0, []
    try:
        data = json.loads(CURRENT_TAXONOMY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return 0, []
    if isinstance(data, dict):
        explicit_count = data.get("unique_factor_count")
        rows = data.get("unique_factors") or data.get("factor_entities") or data.get("factor_details") or data.get("rows") or []
    elif isinstance(data, list):
        explicit_count = None
        rows = data
    else:
        explicit_count = None
        rows = []
    names: set[str] = set()
    normalized_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_rows.append(row)
        name = row.get("factor_name_cn") or row.get("factor_name") or row.get("factor") or row.get("name")
        if name:
            names.add(str(name))
    if isinstance(explicit_count, int) and explicit_count > 0:
        return explicit_count, normalized_rows
    return len(names), normalized_rows

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: list[dict], existing_count: int) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    summary = (
        df.groupby(["一级分类", "二级分类", "数据状态"], dropna=False)
        .size()
        .rename("新增因子数")
        .reset_index()
        .sort_values(["一级分类", "二级分类", "数据状态"])
    )
    primary_summary = (
        df.groupby(["一级分类"], dropna=False)
        .size()
        .rename("新增因子数")
        .reset_index()
    )
    primary_summary["二级分类"] = "小计"
    primary_summary["数据状态"] = ""
    total_row = pd.DataFrame(
        [
            {
                "一级分类": "合计",
                "二级分类": f"上一版约{existing_count}个，新增后约{existing_count + len(df)}个",
                "数据状态": "",
                "新增因子数": len(df),
            }
        ]
    )
    return pd.concat([summary, primary_summary, total_row], ignore_index=True)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    definitions = build_new_definitions()
    rows = [asdict(item) for item in definitions]

    existing_count, existing_rows = load_existing_factor_count()
    summary = build_summary(rows, existing_count)

    write_csv(OUTPUT_DIR / "因子库v2_新增高质量因子清单.csv", rows)
    write_csv(OUTPUT_DIR / "因子库v2_分类覆盖汇总.csv", summary.to_dict("records"))

    blueprint = {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "说明": "本文件是因子实验室v2定义蓝图；不包含任何账号、密钥、口令；外部源因子只登记逻辑和接入位置，不伪造暴露值。",
        "现有因子估计数": existing_count,
        "本次新增因子数": len(rows),
        "新增后目标因子数": existing_count + len(rows),
        "分类体系": PRIMARY_SECONDARY,
        "parquet格式约定": {
            "文件后缀": ".parquet.gzip",
            "表结构": "宽表矩阵",
            "索引": "trade_date",
            "列": "ts_code",
            "值": "factor_exposure",
            "更新原则": "只追加或重算受影响日期；所有财报类因子按公告可见日防未来函数。",
        },
        "新增因子": rows,
        "现有因子样本行数": len(existing_rows),
    }
    (OUTPUT_DIR / "因子库v2_完整蓝图.json").write_text(
        json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    df = pd.DataFrame(rows)
    try:
        df.to_parquet(
            OUTPUT_DIR / "因子库v2_定义元数据.parquet.gzip",
            compression="gzip",
            index=False,
        )
    except Exception as exc:
        (OUTPUT_DIR / "parquet写入提示.txt").write_text(
            f"当前Python环境缺少可用parquet引擎，CSV和JSON已生成；错误：{exc}",
            encoding="utf-8",
        )

    readme = f"""# 因子实验室 v2 因子库升级说明

本次按用户指定的一二级分类补齐高质量因子定义，目标是从上一版约 {existing_count} 个因子扩展到约 {existing_count + len(rows)} 个因子。

## 核心原则

1. 不把简单原始字段直接当成因子，必须有经济含义、计算处理、方向和质量门槛。
2. 财报类因子按公告可见日对齐，避免未来函数。
3. 宏观、预期、汇率等数据若本地尚未具备，只登记可维护接入逻辑，不伪造暴露值。
4. 因子值文件沿用宽表 parquet：索引是交易日，列是股票代码，值是因子暴露。
5. 每个二级分类至少保留三到四个高质量候选，后续通过单因子检验、择时和分域进入模型。

## 输出文件

- `因子库v2_新增高质量因子清单.csv`
- `因子库v2_分类覆盖汇总.csv`
- `因子库v2_完整蓝图.json`
- `因子库v2_定义元数据.parquet.gzip`：若本机 parquet 引擎可用则生成。

## 后续物化规则

可本地物化因子从本地行情、估值、资金流、财报、宏观表生成；需外部权威源补齐的因子必须在数据接入层补齐真实字段后再生成暴露矩阵；复合因子必须经过表达式语法校验、滚动外样本RankIC、分组单调性、置换检验、行业/市值中性检验后才可进入候选池。
"""
    (OUTPUT_DIR / "README_因子库v2.md").write_text(readme, encoding="utf-8")

    print(json.dumps({
        "output_dir": str(OUTPUT_DIR),
        "existing_count": existing_count,
        "new_count": len(rows),
        "target_count": existing_count + len(rows),
        "status_counts": df["数据状态"].value_counts().to_dict(),
        "primary_counts": df["一级分类"].value_counts().to_dict(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
