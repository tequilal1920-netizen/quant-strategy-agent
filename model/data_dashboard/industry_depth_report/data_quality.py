# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from data_sources import DataSourceRegistry


@dataclass
class GateResult:
    passed: bool
    blockers: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    metrics: dict[str, Any]


class IndustryDataQualityGate:
    """Strict quality gate for formal single-industry reports."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.sources = DataSourceRegistry(project_root)

    def evaluate(self, payload: dict[str, Any]) -> GateResult:
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}

        members = payload.get("members", {})
        market = payload.get("market", {})
        fin = payload.get("financial", {})
        macro = payload.get("macro", {})
        signal = payload.get("signal", {})
        reports = payload.get("reports", [])
        as_of = str(payload.get("as_of", ""))
        start_date = str(payload.get("start_date", ""))

        member_count = int(members.get("count") or 0)
        metrics["member_count"] = member_count
        if member_count < 5:
            blockers.append(self.issue("sw_l1_industry_daily", "行业成分股少于5只", "检查申万一级行业映射和日期口径"))

        daily = pd.DataFrame(market.get("industry_daily", []))
        metrics["price_dates"] = int(daily["trade_date"].nunique()) if not daily.empty and "trade_date" in daily else 0
        metrics["price_latest"] = str(daily["trade_date"].max()) if not daily.empty and "trade_date" in daily else ""
        if daily.empty:
            blockers.append(self.issue("stock_ohlcv_daily", "行业行情序列为空", "从本地库或Tushare/BaoStock/Wind补齐日行情"))
        else:
            if metrics["price_latest"] < as_of:
                blockers.append(self.issue("stock_ohlcv_daily", f"行情最新日{metrics['price_latest']}早于报告日{as_of}", "先更新行情库或选择可覆盖的报告日"))
            if metrics["price_dates"] < 120:
                blockers.append(self.issue("stock_ohlcv_daily", "行情交易日少于120个", "至少补齐半年以上日行情"))

        val = pd.DataFrame(market.get("valuation_daily", []))
        metrics["valuation_dates"] = int(val["trade_date"].nunique()) if not val.empty and "trade_date" in val else 0
        metrics["valuation_latest"] = str(val["trade_date"].max()) if not val.empty and "trade_date" in val else ""
        if val.empty:
            blockers.append(self.issue("stock_valuation_daily", "估值序列为空", "补齐PE PB PS 股息率 换手率"))
        else:
            if metrics["valuation_latest"] < as_of:
                blockers.append(self.issue("stock_valuation_daily", f"估值最新日{metrics['valuation_latest']}早于报告日{as_of}", "先更新估值库"))
            last_val = val.tail(1).iloc[0]
            for col in ["median_pe", "median_pb", "median_turnover"]:
                if pd.isna(pd.to_numeric(pd.Series([last_val.get(col)]), errors="coerce").iloc[0]):
                    blockers.append(self.issue("stock_valuation_daily", f"{col} 最新值缺失", "补齐估值字段并重新聚合"))

        money = pd.DataFrame(market.get("moneyflow_daily", []))
        metrics["moneyflow_dates"] = int(money["trade_date"].nunique()) if not money.empty and "trade_date" in money else 0
        if money.empty or metrics["moneyflow_dates"] < 60:
            blockers.append(self.issue("stock_moneyflow_daily", "资金流序列不足60个交易日", "补齐行业成分股资金流"))

        metrics["financial_available"] = bool(fin.get("available"))
        metrics["financial_latest_end_date"] = str(fin.get("latest_end_date", ""))
        metrics["financial_sample"] = int(fin.get("latest_sample") or 0) if fin.get("available") else 0
        if not fin.get("available"):
            blockers.append(self.issue("financial_report_visible", "财报数据为空", "补齐可见财报口径"))
        else:
            if metrics["financial_sample"] < max(5, int(member_count * 0.45)):
                blockers.append(self.issue("financial_report_visible", "最新财报样本覆盖不足45%", "补齐行业成分股财报"))
            for key in ["median_roe", "median_debt_to_assets", "median_tr_yoy", "median_netprofit_yoy"]:
                if fin.get(key) is None:
                    blockers.append(self.issue("financial_report_visible", f"{key} 缺失", "补齐财务指标字段"))

        metrics["macro_available"] = bool(macro.get("available"))
        if not macro.get("available"):
            blockers.append(self.issue("macro_monthly", "宏观月度数据为空", "补齐PMI PPI CPI M1 M2 社融等字段"))
        else:
            if not macro.get("pmi_manufacturing") and not macro.get("m2_yoy"):
                blockers.append(self.issue("macro_monthly", "宏观核心字段不足", "至少需要PMI或货币社融指标"))

        metrics["signal_available"] = bool(signal.get("available"))
        if not signal.get("available"):
            blockers.append(self.issue("v3_industry_signal", "行业模型信号为空", "运行V3行业信号或提供替代模型信号"))

        metrics["report_evidence_count"] = len(reports)
        if len(reports) < 1:
            warnings.append(self.issue("broker_report_index", "未检索到行业相关研报索引", "补充行业研报证据可增强政策和产业链章节"))

        framework = payload.get("framework", {})
        if not framework.get("core") or not framework.get("demand") or not framework.get("supply") or not framework.get("profit"):
            blockers.append(self.issue("industry_framework", "行业类型框架缺失", "完善行业类型到研究框架映射"))

        return GateResult(passed=not blockers, blockers=blockers, warnings=warnings, metrics=metrics)

    def issue(self, dataset: str, issue: str, required_action: str) -> dict[str, Any]:
        return {"dataset": dataset, "issue": issue, "required_action": required_action}

    def write_outputs(self, result: GateResult, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        gate_path = output_dir / "data_quality_gate.json"
        missing_path = output_dir / "missing_data_plan.md"
        source_status = [s.__dict__ for s in self.sources.statuses()]
        fill_plan = self.sources.fill_plan(result.blockers)
        gate_payload = {
            "passed": result.passed,
            "blockers": result.blockers,
            "warnings": result.warnings,
            "metrics": result.metrics,
            "source_status": source_status,
            "fill_plan": fill_plan,
        }
        gate_path.write_text(json.dumps(gate_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# 数据缺口和补数计划", ""]
        lines.append(f"- 质量门状态：{'通过' if result.passed else '未通过'}")
        lines.append("")
        if result.blockers:
            lines.append("## 阻断项")
            for item in result.blockers:
                lines.append(f"- `{item['dataset']}`：{item['issue']}。处理：{item['required_action']}")
            lines.append("")
        if result.warnings:
            lines.append("## 警告项")
            for item in result.warnings:
                lines.append(f"- `{item['dataset']}`：{item['issue']}。处理：{item['required_action']}")
            lines.append("")
        lines.append("## 可用数据源")
        for s in source_status:
            lines.append(f"- {s['name']}：{'已配置' if s['configured'] else '未配置'}；用途：{s['role']}；额度：{s['quota_policy']}")
        lines.append("")
        lines.append("## 补数规则")
        lines.append("- 正式报告不允许用空字段生成结论。")
        lines.append("- 付费接口只按缺口清单取数，不做无边界全量拉取。")
        lines.append("- 凭据只能放入环境变量或私有 env 文件。")
        missing_path.write_text("\n".join(lines), encoding="utf-8")
        return gate_path, missing_path

