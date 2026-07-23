# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SourceStatus:
    name: str
    configured: bool
    role: str
    env_keys: list[str]
    quota_policy: str
    note: str


class DataSourceRegistry:
    """Credential-safe registry for local and external data sources.

    The registry never stores secrets. It only checks whether required
    environment variables exist and describes which connector should fill a
    missing data contract.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def statuses(self) -> list[SourceStatus]:
        return [
            SourceStatus(
                name="local_research_warehouse",
                configured=(self.project_root / "database" / "research_warehouse.db").exists(),
                role="主数据仓库，覆盖行业成分，行情，估值，资金流，财报，宏观，研报索引",
                env_keys=["AI_QUANT_WAREHOUSE_DB"],
                quota_policy="本地只读，无额度消耗",
                note="正式报告优先使用该库",
            ),
            SourceStatus(
                name="tushare",
                configured=bool(os.environ.get("TUSHARE_TOKEN")),
                role="补充日行情，财务，指数，行业分类，部分宏观指标",
                env_keys=["TUSHARE_TOKEN"],
                quota_policy="低频缺口补数，禁止全市场高频重复拉取",
                note="token 只允许放在环境变量或私有 env 文件",
            ),
            SourceStatus(
                name="baostock",
                configured=True,
                role="补充免费A股日行情和基础财务指标",
                env_keys=[],
                quota_policy="仅用于低频补缺和交叉验证",
                note="无需密钥，网络不可用时标记为不可取",
            ),
            SourceStatus(
                name="akshare",
                configured=True,
                role="补充公开宏观，行业高频，商品和价格指标",
                env_keys=[],
                quota_policy="仅按行业指标白名单拉取",
                note="公开接口稳定性需质量门复核",
            ),
            SourceStatus(
                name="wind_sql",
                configured=bool(os.environ.get("WIND_SQL_UID") and os.environ.get("WIND_SQL_PWD")),
                role="补充Wind行业，指数，商品，库存，盈利预测等权威字段",
                env_keys=["WIND_SQL_UID", "WIND_SQL_PWD"],
                quota_policy="只取合约化SQL白名单字段，禁止无边界扫描",
                note="账号密码不得写入仓库",
            ),
            SourceStatus(
                name="ifind_quantapi",
                configured=bool(os.environ.get("IFIND_ACCESS_TOKEN") or os.environ.get("IFIND_REFRESH_TOKEN")),
                role="补充iFind行业高频，财务，预测和专题指标",
                env_keys=["IFIND_ACCESS_TOKEN", "IFIND_REFRESH_TOKEN"],
                quota_policy="额度敏感，默认禁用批量全量拉取",
                note="仅在本地私有配置开启时使用",
            ),
            SourceStatus(
                name="ai_router",
                configured=bool(os.environ.get("AI_ROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")),
                role="基于结构化摘要生成专业段落",
                env_keys=["AI_ROUTER_API_KEY", "AI_ROUTER_BASE_URL", "AI_ROUTER_MODEL", "AI_ROUTER_REASONING_EFFORT"],
                quota_policy="不处理原始凭据，不生成未给出的数字",
                note="AI不可替代数据质量门",
            ),
        ]

    def fill_plan(self, missing_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        plan = []
        for item in missing_items:
            dataset = item.get("dataset", "")
            if dataset in {"macro_monthly", "industry_hf_indicator"}:
                candidates = ["akshare", "wind_sql", "ifind_quantapi"]
            elif dataset in {"stock_ohlcv_daily", "stock_valuation_daily", "financial_report_visible"}:
                candidates = ["tushare", "baostock", "wind_sql", "ifind_quantapi"]
            elif dataset in {"stock_moneyflow_daily", "broker_report_index"}:
                candidates = ["wind_sql", "ifind_quantapi", "akshare"]
            else:
                candidates = ["wind_sql", "ifind_quantapi"]
            plan.append({
                "dataset": dataset,
                "issue": item.get("issue", ""),
                "required_action": item.get("required_action", "补齐数据后重新运行质量门"),
                "candidate_sources": candidates,
                "secret_policy": "凭据只能写入环境变量或私有env文件，禁止落入代码，日志，Word和payload",
            })
        return plan

