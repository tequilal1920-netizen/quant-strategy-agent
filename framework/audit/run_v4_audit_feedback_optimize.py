"""Build the V4 audit-feedback-optimize control layer.

This script does not re-label weak backtests as successful.  It converts the
existing local-data, research-evidence and Model Doctor requirements into
durable warehouse tables so the next model iteration has a strict checklist.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


RUN_DEFAULT = "v4_audit_feedback_optimize_20260706"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_DATE = "2012-01-01"
END_DATE = "2026-06-30"
TRAIN_WINDOW = "2012-01-01..2020-12-31"
VALID_WINDOW = "2021-01-01..2022-12-31"
TEST_WINDOW = "2023-01-01..2026-06-30"
STRICT_TARGET = "test and full annual_return >= 20%, sharpe >= 1.5"


@dataclass(frozen=True)
class MethodCard:
    card_id: str
    module_name: str
    report_title: str
    org: str
    report_date: str
    evidence_level: str
    method_summary: str
    required_data: str
    expected_model_change: str
    current_gap: str
    adoption_status: str


@dataclass(frozen=True)
class GapItem:
    gap_id: str
    module_name: str
    severity: str
    gap_title: str
    current_state: str
    required_state: str
    repair_action: str
    acceptance_gate: str
    status: str
    owner_agent: str


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_slug(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "skill"


def execute_schema(con: sqlite3.Connection, project_root: Path) -> None:
    schema_path = project_root / "framework" / "audit" / "schema_v4.sql"
    con.executescript(schema_path.read_text(encoding="utf-8"))


def reset_run(con: sqlite3.Connection, run_id: str) -> None:
    tables = [
        "v4_run_manifest",
        "v4_method_card",
        "v4_gap_register",
        "v4_agent_review_cycle",
        "v4_model_upgrade_spec",
        "v4_strict_gate",
        "v4_kline_skill_doc_index",
        "v4_factor_agent_plan",
    ]
    for table in tables:
        con.execute(f"delete from {table} where run_id = ?", (run_id,))


def get_latest_run_id(con: sqlite3.Connection, table: str) -> str | None:
    row = con.execute(
        f"select run_id, count(*) as n from {table} group by run_id order by n desc limit 1"
    ).fetchone()
    return row[0] if row else None


def method_cards_from_sources(
    con: sqlite3.Connection, artifact_dir: Path
) -> list[MethodCard]:
    """Build method cards from local report index plus AgentConsole artifacts."""

    evidence_rows = con.execute(
        """
        select module_name, report_date, org, title, evidence_level, notes
        from v3_report_evidence
        order by module_name, adopted_flag desc, report_date desc
        """
    ).fetchall()
    by_module: dict[str, list[sqlite3.Row]] = {}
    for row in evidence_rows:
        by_module.setdefault(row["module_name"], []).append(row)

    strategy_map = load_json(artifact_dir / "strategy_reference_map.json", {})
    learning_map = load_json(artifact_dir / "book_and_report_learning_map.json", {})

    canonical = [
        MethodCard(
            card_id="weekly_evidence_graph",
            module_name="weekly_research_report_agent",
            report_title="券商周报、宏观跟踪、行业复盘、龙虎榜和事件库证据图谱",
            org="local_report_index + subject data contract",
            report_date="2024-2026",
            evidence_level="title_level_plus_local_contract",
            method_summary=(
                "将宏观、热点事件、行业数据、龙虎榜和个股深度拆成可追溯字段，"
                "每周只输出已入库证据、异常字段和需要补充的人工阅读清单。"
            ),
            required_data=(
                "交易日历、指数和个股行情、宏观月度/高频指标、行业分类、新闻/研报标题、"
                "龙虎榜、资金流、公告与财务披露日期。"
            ),
            expected_model_change="周报不直接优化收益，作为后续资产/行业/个股模型的证据输入和异常解释层。",
            current_gap="V3已有周报快照，但事件和龙虎榜深度解释仍偏薄，缺少全文研报证据和字段级血缘。",
            adoption_status="requires_full_text_and_field_lineage",
        ),
        MethodCard(
            card_id="asset_allocation_llm_bl",
            module_name="asset_allocation_etf",
            report_title="AI赋能资产配置、BL/风险预算/全天候ETF配置系列",
            org="国信/金融街/中银/华安等标题级证据",
            report_date="2025-2026",
            evidence_level="agentconsole_learning_artifact",
            method_summary=(
                "先做宏观周期与风险状态识别，再把股票、债券、商品和现金ETF放入风险预算；"
                "LLM只参与观点约束、风险解释和异常复核，不直接偷看未来收益。"
            ),
            required_data="A股宽基ETF、债券ETF、商品ETF、货币/现金代理、利率、信用利差、通胀、汇率、波动率和回撤。",
            expected_model_change="从简单动量预算升级为 regime + BL view + risk parity + drawdown control 的组合。",
            current_gap="V3 ETF袖套全样本收益达标但Sharpe不足，测试期也未过20%/1.5严格门槛。",
            adoption_status="large_change_required",
        ),
        MethodCard(
            card_id="style_industry_agent_rotation",
            module_name="style_industry_rotation",
            report_title="Agent赋能行业轮动、双周期行业跟踪、行业ETF轮动",
            org="国信/国金/金融街等标题级证据",
            report_date="2025-2026",
            evidence_level="agentconsole_learning_artifact",
            method_summary=(
                "申万一级行业按景气、资金、技术、拥挤、事件叙事和行业内Alpha六层打分；"
                "风格层覆盖成长、价值、红利及小盘/大盘，先定风格预算再定行业和ETF映射。"
            ),
            required_data="申万一级行业分类、行业指数、行业ETF、行业财务汇总、资金流、估值、成交拥挤、新闻研报证据。",
            expected_model_change="从行业聚合分数升级为多agent评分、置信度校准、行业内个股增强和ETF替代组合。",
            current_gap="V3有行业/风格信号，但缺少行业卡片、ETF映射审计和逐年归因。",
            adoption_status="medium_to_large_change_required",
        ),
        MethodCard(
            card_id="ai_kline_skillbook",
            module_name="stock_ai_kline_skill",
            report_title="GPT-Kline、技术分析大模型、K线书籍规则Skill化",
            org="华泰金工/国内技术分析教材",
            report_date="2024-2026",
            evidence_level="agentconsole_learning_artifact",
            method_summary=(
                "将K线形态、趋势、量价、波动、突破、止损和失败条件拆成独立skill文档；"
                "单股调用时先计算规则证据，再交给Codex URL做深度解释与结论复核。"
            ),
            required_data="前复权OHLCV、成交额、换手率、停牌/涨跌停、复权因子、行业和指数背景。",
            expected_model_change="把125个已注册规则变成可检索skillbook，并优先补齐可执行规则、冲突处理和多指标融合。",
            current_gap="V3注册125条K线skill，但仅部分逻辑可执行，单股深度分析需要更完整的依据树。",
            adoption_status="requires_skill_completion",
        ),
        MethodCard(
            card_id="ai_factor_factory_deep_mining",
            module_name="stock_factor_ai_factory",
            report_title="GPT因子工厂、GPT因子工厂2.0、大模型+强化学习因子挖掘",
            org="华泰金工/机器学习因子研究",
            report_date="2024-2026",
            evidence_level="agentconsole_learning_artifact",
            method_summary=(
                "大模型负责提出经济含义和表达式候选，程序负责点时检验、RankIC、分层收益、换手、"
                "相关性、衰减和样本外稳定性；未过门槛的表达式进入黑名单。"
            ),
            required_data="行情、财务、估值、资金流、分析师/研报、行业、风格暴露、可交易过滤和滞后披露时间。",
            expected_model_change="从9个候选因子升级为可配置路径输入、自动搜索、深度学习表征和停止规则。",
            current_gap="V3因子候选过少，DL/强化学习/自动挖掘循环尚未真正落地。",
            adoption_status="large_change_required",
        ),
        MethodCard(
            card_id="portfolio_optimizer_index_enhancement",
            module_name="portfolio_optimizer",
            report_title="PortfolioNet、组合优化、指数增强与交易成本约束",
            org="华泰金工/国金MCP投研Agent",
            report_date="2024-2026",
            evidence_level="agentconsole_learning_artifact",
            method_summary=(
                "把权益仓位、行业权重、个股Alpha、风格暴露、风险模型和交易成本统一到优化器；"
                "输出全A、中证800、中证2000三套指增组合，并做样本内外与逐年归因。"
            ),
            required_data="指数成分、行业/风格暴露、协方差、Alpha分数、停牌涨跌停、成本、基准权重和可交易约束。",
            expected_model_change="从分数排序升级为TE/行业/个股/换手/成本约束下的组合优化和贡献归因。",
            current_gap="V3全A接近目标，但CSI800/CSI2000和组合层Sharpe未达严格门槛。",
            adoption_status="large_change_required",
        ),
    ]

    # Attach exact local evidence titles when available. This keeps the cards tied
    # to the report index without pretending that title-level evidence is PDF proof.
    enriched: list[MethodCard] = []
    for card in canonical:
        rows = by_module.get(card.module_name, [])[:3]
        if rows:
            titles = "；".join(
                f"{r['org']}《{r['title']}》({r['report_date']})" for r in rows
            )
            report_title = f"{card.report_title} | 本地索引: {titles}"
        else:
            report_title = card.report_title
        enriched.append(
            MethodCard(
                card_id=card.card_id,
                module_name=card.module_name,
                report_title=report_title,
                org=card.org,
                report_date=card.report_date,
                evidence_level=card.evidence_level,
                method_summary=card.method_summary,
                required_data=card.required_data,
                expected_model_change=card.expected_model_change,
                current_gap=card.current_gap,
                adoption_status=card.adoption_status,
            )
        )

    # Record the old AgentConsole source map as explicit learning artifacts.
    for key, payload in strategy_map.items():
        if any(c.card_id == f"agentconsole_{key}" for c in enriched):
            continue
        reports = payload.get("broker_report_basis", [])
        impl = payload.get("implementation", [])
        enriched.append(
            MethodCard(
                card_id=f"agentconsole_{safe_slug(key)}",
                module_name=key,
                report_title="AgentConsole/1.0 strategy reference map",
                org="local_artifact",
                report_date="2026-07-06",
                evidence_level="local_strategy_learning_map",
                method_summary="；".join(str(x) for x in reports[:3]) or "local strategy map",
                required_data="由对应模块的数据契约约束，付费API仅作补缺校验。",
                expected_model_change="；".join(str(x) for x in impl[:3]) or "复用AgentConsole学习流程。",
                current_gap="原AgentConsole结果不是2012-2026严格样本内外正式验收结果，只能作为模型族来源。",
                adoption_status="source_family_only",
            )
        )

    for key, payload in learning_map.items():
        enriched.append(
            MethodCard(
                card_id=f"learning_{safe_slug(key)}",
                module_name=key,
                report_title="Book and report learning map",
                org="local_artifact",
                report_date="2026-07-06",
                evidence_level="local_book_report_map",
                method_summary="；".join(str(x) for x in payload.get("broker_reports", [])[:3]),
                required_data="；".join(str(x) for x in payload.get("learning_outputs", [])[:3]),
                expected_model_change="把书籍和研报条目转为可执行规则卡、字段卡、验收卡。",
                current_gap="需要逐条升级为可执行代码或可审计字段映射。",
                adoption_status="needs_execution_binding",
            )
        )
    return enriched


def gap_register_items() -> list[GapItem]:
    return [
        GapItem(
            "G001",
            "global",
            "critical",
            "全系统严格目标未完全通过",
            "当前仅部分模型在test+full同时满足20%年化和1.5 Sharpe；CSI800、CSI2000、ETF、K线、行业/风格等仍未整体通过。",
            "每个正式模块必须按2012-01-01至2026-06-30、train/valid/test/full、逐年审计。",
            "Model Doctor先定位失败层，再做大改实验；任何收益提升都必须保留约束、成本和样本外记录。",
            "v4_strict_gate中至少对应模型的test和full同时pass_flag=1，且逐年无单一年份异常依赖。",
            "open",
            "07_model_doctor",
        ),
        GapItem(
            "G002",
            "data_contract",
            "critical",
            "本地数据源字段血缘不够细",
            "V2/V3已经入库，但与subject原库、Wind补数和研报字段之间仍缺少完整字段级血缘表。",
            "所有字段必须说明来源表/API、频率、滞后、复权、缺失处理、异常阈值和用途。",
            "复用02_data_contract规范，生成字段血缘和预提取检查表；高额度API仅用于缺口校验。",
            "缺失率、日期范围、主键唯一性、未来函数检查全部通过。",
            "open",
            "02_data_contract",
        ),
        GapItem(
            "G003",
            "research_evidence",
            "high",
            "研报证据仍以标题级为主",
            "V3有56条报告证据，但多数为标题级或本地artifact，没有PDF正文参数抽取。",
            "每个子模型3-7篇券商金工深度报告，至少形成方法、字段、参数、风险四类证据。",
            "研报agent补全文/摘要读取，输出evidence map；未读全文不得写成确定参数。",
            "每个模型method card绑定不少于3条可追溯证据，证据等级清楚。",
            "open",
            "01_research_report",
        ),
        GapItem(
            "G004",
            "weekly_report",
            "medium",
            "周报链条缺少龙虎榜个股深度",
            "V3已有周报快照，但宏观、事件、行业、龙虎榜之间还未形成完整解释链。",
            "每周输出宏观状态、热点事件、行业复盘、龙虎榜个股深度和后续模型输入。",
            "新增龙虎榜/资金流/公告/行业证据拼接与异常个股解释模板。",
            "周报表中每期有四个板块，且每个板块有字段来源和可视化输出。",
            "open",
            "08_web_report_agent",
        ),
        GapItem(
            "G005",
            "asset_allocation_etf",
            "critical",
            "资产配置模型结构过浅",
            "V3资产配置ETF测试与全样本Sharpe均不足，当前风险状态与预算切换解释不够。",
            "股、债、商品、现金ETF必须有周期状态、BL观点、风险预算、回撤保护和成本审计。",
            "替换为regime + BL view + risk parity + momentum/carry filter + drawdown throttle。",
            "test/full年化和Sharpe过关，逐年归因显示不是靠单一风险资产暴露。",
            "open",
            "10_asset_allocator_agent",
        ),
        GapItem(
            "G006",
            "style_industry_rotation",
            "high",
            "行业和风格轮动未形成多agent卡片",
            "V3有信号表，但行业景气、资金、技术、拥挤、事件叙事和ETF映射仍偏合并。",
            "申万一级行业逐行业构造指标卡，风格成长/价值/红利有独立解释和ETF映射。",
            "拆分行业子agent，增加置信度校准、ETF替代、行业内个股增强和逐年归因。",
            "行业/风格模型在ALL_A、CSI800、CSI2000口径均有可解释的样本外稳定性。",
            "open",
            "11_industry_rotation_agent",
        ),
        GapItem(
            "G007",
            "stock_ai_kline_skill",
            "critical",
            "K线Skill库未完全可执行",
            "V3注册125条K线skill，但仅部分形态规则已经进入单股分析器可执行代码。",
            "上百个K线形态必须形成独立文档、规则、输入输出、失败条件和融合逻辑。",
            "生成skillbook索引，优先补齐TA-Lib形态、趋势量价确认和多指标冲突处理。",
            "任意个股+频率调用可输出最新形态、证据树、冲突项和Codex URL分析包。",
            "open",
            "09_kline_skill_agent",
        ),
        GapItem(
            "G008",
            "stock_factor_ai_factory",
            "critical",
            "因子挖掘Agent未形成长期自动搜索",
            "V3只有9个候选因子，尚未实现指定路径输入、DL表征、自动搜索和停止规则。",
            "因子Agent应读取指定特征路径，自动提出表达式、训练、验证、测试，直到过关或预算结束。",
            "新增候选生成、RankIC、分层、换手、相关性、衰减、黑名单、DL embedding和复核循环。",
            "新因子必须通过train/valid/test/full、逐年和相关性冗余检查。",
            "open",
            "10_factor_mining_agent",
        ),
        GapItem(
            "G009",
            "portfolio_optimizer",
            "critical",
            "组合优化与指增约束不足",
            "V3更多是分数排序，未充分落实全A、中证800、中证2000三类指增约束。",
            "需要权益仓位、行业权重、个股Alpha、风险模型、TE、成本、换手和持仓上限统一优化。",
            "引入指数增强优化器、风险暴露矩阵、交易成本和持仓约束，输出贡献归因。",
            "每个目标池均有样本内外、全样本、逐年和基准超额审计。",
            "open",
            "13_portfolio_optimizer_agent",
        ),
        GapItem(
            "G010",
            "web_runtime",
            "high",
            "网页缺少长任务与审查闭环",
            "V3公网只展示状态，不足以承载K线深度调用、因子挖掘长任务和Model Doctor复核。",
            "网页应有任务队列、进度、日志、产物下载、失败重跑和审查结论。",
            "在不影响现有公网端口的前提下新增端口，接入V4状态和长任务job表。",
            "公网/api/state展示V4缺口、严格门槛、任务状态和产物路径。",
            "open",
            "08_web_report_agent",
        ),
    ]


def strict_gate_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        select universe, model_name, split_name, annual_return, sharpe, issues_json
        from v3_backtest_audit
        where lower(year) = 'all' and split_name in ('test','full')
        """
    ).fetchall()
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["universe"], row["model_name"])
        item = pairs.setdefault(
            key,
            {
                "universe": row["universe"],
                "model_name": row["model_name"],
                "test_annual": None,
                "test_sharpe": None,
                "full_annual": None,
                "full_sharpe": None,
                "issues": [],
            },
        )
        if row["split_name"] == "test":
            item["test_annual"] = row["annual_return"]
            item["test_sharpe"] = row["sharpe"]
        elif row["split_name"] == "full":
            item["full_annual"] = row["annual_return"]
            item["full_sharpe"] = row["sharpe"]
        if row["issues_json"]:
            try:
                item["issues"].extend(json.loads(row["issues_json"]))
            except json.JSONDecodeError:
                item["issues"].append(row["issues_json"])

    out = []
    for item in pairs.values():
        pass_flag = int(
            item["test_annual"] is not None
            and item["test_sharpe"] is not None
            and item["full_annual"] is not None
            and item["full_sharpe"] is not None
            and item["test_annual"] >= 0.20
            and item["test_sharpe"] >= 1.50
            and item["full_annual"] >= 0.20
            and item["full_sharpe"] >= 1.50
        )
        issue_parts = []
        if item["test_annual"] is None or item["full_annual"] is None:
            issue_parts.append("missing test/full formal audit")
        else:
            if item["test_annual"] < 0.20:
                issue_parts.append("test annual < 20%")
            if item["test_sharpe"] < 1.50:
                issue_parts.append("test sharpe < 1.5")
            if item["full_annual"] < 0.20:
                issue_parts.append("full annual < 20%")
            if item["full_sharpe"] < 1.50:
                issue_parts.append("full sharpe < 1.5")
        if item["issues"]:
            issue_parts.append("formal issues: " + "; ".join(str(x) for x in item["issues"][:3]))
        item["pass_flag"] = pass_flag
        item["issue"] = "pass" if pass_flag else "；".join(issue_parts)
        out.append(item)
    return sorted(out, key=lambda x: (x["universe"], x["model_name"]))


def model_upgrade_specs(gate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for row in gate_rows:
        model = row["model_name"]
        universe = row["universe"]
        fail = row["issue"]
        if "asset" in model or "allocation" in model:
            block = "regime + BL view + risk parity + drawdown throttle"
            features = "macro regime, bond yield, credit spread, commodity trend, ETF momentum, volatility, drawdown"
            constraints = "asset sleeve max/min, volatility target, cash floor, turnover and drawdown stop"
        elif "industry" in model or "style" in model:
            block = "multi-agent industry/style scorecard with ETF mapping and confidence calibration"
            features = "SW L1 prosperity, flow, valuation, crowding, event narrative, ETF liquidity, in-industry alpha"
            constraints = "style budget, industry cap, ETF liquidity, turnover, benchmark exposure"
        elif "kline" in model:
            block = "executable K-line skillbook + multi-signal fusion + evidence tree"
            features = "125+ candlestick skills, trend/volume/volatility context, stop-loss path, conflict resolver"
            constraints = "single-stock liquidity, limit-up/down, industry cap, risk hard gate"
        elif "factor" in model:
            block = "AI factor factory with expression search, DL representation and full PIT validation"
            features = "price/volume, fundamentals, analyst/report, flow, industry, style neutralization, blacklists"
            constraints = "RankIC/ICIR/spread/turnover/correlation/decay gates, no future fields"
        elif "portfolio" in model:
            block = "index-enhancement optimizer with risk model, TE, cost and attribution"
            features = "alpha stack, risk exposures, covariance, benchmark weights, cost and capacity"
            constraints = "TE, industry/style neutrality, single-name cap, turnover/cost, rebalance calendar"
        else:
            block = "module-specific Model Doctor repair cycle"
            features = "data lineage, signal diagnostics, attribution, cost and sample stability"
            constraints = "full formal target gate and yearly audit"
        specs.append(
            {
                "model_name": model,
                "universe": universe,
                "current_test_annual": row["test_annual"],
                "current_test_sharpe": row["test_sharpe"],
                "current_full_annual": row["full_annual"],
                "current_full_sharpe": row["full_sharpe"],
                "failure_mode": fail,
                "upgrade_block": block,
                "new_features": features,
                "portfolio_constraints": constraints,
                "validation_rule": (
                    "2012-01-01..2026-06-30统一窗口；train/valid/test/full与逐年；"
                    "test和full同时annual>=20%、Sharpe>=1.5；保留成本、约束、数据血缘和异常记录。"
                ),
                "status": "needs_large_iteration" if row["pass_flag"] == 0 else "passed_keep_monitoring",
            }
        )
    return specs


def agent_review_cycles(gaps: list[GapItem], gate_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    strict_pass_count = sum(x["pass_flag"] for x in gate_rows)
    cycles: list[dict[str, str]] = []
    for gap in gaps:
        phases = [
            (
                "audit",
                gap.current_state,
                "进入Model Doctor诊断，不直接调参。",
                "检查数据、信号、模型、组合、执行六层证据。",
            ),
            (
                "feedback",
                gap.gap_title,
                "把缺口写成可执行实验，不写成口头建议。",
                "每个建议必须绑定数据字段、研报证据和验收门槛。",
            ),
            (
                "repair",
                gap.repair_action,
                "按大改/中改/小改分级实施，影响严格门槛的更改单独记录。",
                "代码、参数、输入数据和输出指标均可复现。",
            ),
            (
                "recheck",
                gap.acceptance_gate,
                "只接受样本外和全样本共同过关的结果。",
                "重新跑V2/V3/V4 formal audit并更新v4_strict_gate。",
            ),
            (
                "optimize",
                f"当前严格通过数={strict_pass_count}",
                "若仍未过关，定位贡献缺口后进行下一轮局部替换。",
                "不得降低约束、删成本或改变时间窗口来制造通过。",
            ),
        ]
        for phase, finding, decision, rule in phases:
            cycles.append(
                {
                    "cycle_id": f"{gap.gap_id}_{safe_slug(gap.module_name)}",
                    "phase": phase,
                    "agent_name": gap.owner_agent,
                    "finding": finding,
                    "repair_decision": decision,
                    "recheck_rule": rule,
                    "status": "open",
                    "created_at": now_iso(),
                }
            )
    return cycles


def executable_skill_ids() -> set[str]:
    return {
        "cdldoji",
        "cdlhammer",
        "cdlhangingman",
        "cdlinvertedhammer",
        "cdlshootingstar",
        "cdlmarubozu",
        "cdlengulfing",
        "cdlharami",
        "cdlharamicross",
        "cdlhighwave",
        "cdlmorningstar",
        "cdleveningstar",
        "cdlpiercing",
        "cdldarkcloudcover",
        "cdldragonflydoji",
        "cdlgravestonedoji",
        "cdllongleggeddoji",
        "cdlspinningtop",
        "cdlbelthold",
        "cdl3whitesoldiers",
        "cdl3blackcrows",
        "cdl3inside",
        "cdl3outside",
        "cdlabandonedbaby",
        "cdlupsidegap2crows",
        "cdlunique3river",
        "cdlladderbottom",
        "cdlcounterattack",
        "cdlthrusting",
        "cdlseparatinglines",
        "cdlkicking",
        "cdlrisefall3methods",
        "cdladvanceblock",
        "cdlstalledpattern",
        "cdl3linestrike",
        "cdlmatchinglow",
        "breakaway_gap_up",
        "breakaway_gap_down",
        "twenty_day_breakout_confirmed",
        "twenty_day_breakdown_confirmed",
        "volume_price_confirmation",
        "trend_pullback_reversal",
        "volatility_squeeze_breakout",
        "overheat_penalty",
        "stop_loss_path",
        "multi_signal_fusion",
    }


def generate_kline_skill_docs(
    con: sqlite3.Connection, run_id: str, project_root: Path
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        select skill_id, family, pattern_name, direction, lookback, logic, source_basis, implementation_status
        from v3_kline_skill_registry
        order by family, skill_id
        """
    ).fetchall()
    skill_dir = project_root / "skills" / "kline_patterns"
    skill_dir.mkdir(parents=True, exist_ok=True)
    executable = executable_skill_ids()
    index_rows: list[dict[str, Any]] = []
    for row in rows:
        sid = safe_slug(row["skill_id"])
        doc_path = skill_dir / f"{sid}.md"
        is_exec = int(sid in executable or row["implementation_status"] == "executable")
        missing = "" if is_exec else "需要补齐可执行规则、阈值、冲突处理和单元测试"
        text = f"""# {row['pattern_name']}

## Skill ID

`{sid}`

## Family

{row['family']}

## Inputs

- `date`
- `code`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`
- `turnover`
- 复权因子、停牌、涨跌停和行业/指数背景

## Logic

方向：{row['direction']}  
观察窗口：{row['lookback']} 根K线  
规则：{row['logic']}

## Output

- `trigger`: 是否触发
- `direction`: bullish / bearish / neutral / contextual
- `confidence`: 0-1
- `evidence`: 触发价格、量能、趋势和失败条件
- `conflicts`: 与其他K线、趋势或风险规则的冲突项

## Failure Conditions

- 数据缺口、停牌、涨跌停导致形态不可交易
- 趋势背景与形态方向冲突
- 放量/缩量确认缺失
- 触发后回撤超过止损路径

## Source Basis

{row['source_basis']}

## Execution Status

{('executable in current analyzer' if is_exec else 'registered; execution logic pending')}
"""
        doc_path.write_text(text, encoding="utf-8")
        index_rows.append(
            {
                "skill_id": sid,
                "family": row["family"],
                "pattern_name": row["pattern_name"],
                "doc_path": str(doc_path.relative_to(project_root)).replace("\\", "/"),
                "executable_flag": is_exec,
                "missing_logic": missing,
            }
        )

    fusion_doc = skill_dir / "multi_signal_fusion.md"
    fusion_doc.write_text(
        """# Multi Signal Fusion

## Skill ID

`multi_signal_fusion`

## Purpose

把单根K线、组合K线、趋势、量价、波动、突破、过热和止损信号合成一个可审计的技术面综合指标。

## Inputs

- 全部K线skill输出
- 20/60/120日趋势
- 成交量和换手确认
- 波动率收缩/扩张
- 停牌、涨跌停、流动性和行业背景

## Logic

1. 先过滤不可交易日期和异常价格。
2. 趋势skill决定基础方向，反转形态只能在趋势背景成立时提高置信度。
3. 量价确认作为乘数，缺失时降低权重。
4. 过热、破位和止损skill作为硬门槛。
5. 输出`technical_composite_score`、主要证据和冲突项。

## Output

- `technical_composite_score`
- `primary_signal`
- `supporting_signals`
- `risk_flags`
- `agent_prompt_package`

## Execution Status

executable design; analyzer binding required for full production use.
""",
        encoding="utf-8",
    )
    if not any(x["skill_id"] == "multi_signal_fusion" for x in index_rows):
        index_rows.append(
            {
                "skill_id": "multi_signal_fusion",
                "family": "fusion",
                "pattern_name": "多指标融合技术面综合信号",
                "doc_path": str(fusion_doc.relative_to(project_root)).replace("\\", "/"),
                "executable_flag": 1,
                "missing_logic": "",
            }
        )
    return index_rows


def factor_agent_plans() -> list[dict[str, str]]:
    return [
        {
            "plan_id": "F001",
            "factor_family": "classic_alpha_expression",
            "data_scope": "行情、量价、波动、反转、动量、换手、行业中性字段",
            "search_channel": "表达式模板 + GPT因子假设 + 历史失败黑名单",
            "validation_stack": "RankIC、ICIR、分层收益、多空、衰减、换手、相关性、逐年稳定性",
            "stop_rule": "valid/test/full均过门槛且与已入库因子相关性低于0.7；否则达到预算后停止。",
            "current_status": "needs_auto_loop_implementation",
        },
        {
            "plan_id": "F002",
            "factor_family": "fundamental_revision_quality",
            "data_scope": "财务、盈利质量、成长、估值、分析师/预期修正、披露日期",
            "search_channel": "财报字段组合 + 研报逻辑抽取 + 滞后可得性检查",
            "validation_stack": "点时披露、缺失覆盖、行业中性、RankIC、分组收益、换手和容量",
            "stop_rule": "样本外RankIC为正、top-bottom spread为正且交易成本后仍有效。",
            "current_status": "needs_field_lineage_and_pit_check",
        },
        {
            "plan_id": "F003",
            "factor_family": "deep_learning_representation",
            "data_scope": "过去N日OHLCV序列、财务低频特征、行业/风格暴露",
            "search_channel": "TCN/Transformer/TabNet式表征 + 因子蒸馏 + 传统因子融合",
            "validation_stack": "walk-forward训练、valid早停、test冻结、逐年稳定性、特征重要性和漂移",
            "stop_rule": "不允许用测试集调参；valid选择后一次性上test，失败写入实验库。",
            "current_status": "not_implemented_yet",
        },
        {
            "plan_id": "F004",
            "factor_family": "agent_proposed_custom_path",
            "data_scope": "用户指定任意本地特征路径，需通过字段契约和主键/日期检查",
            "search_channel": "路径扫描 -> 字段画像 -> 候选表达式 -> 自动回测 -> Model Doctor复核",
            "validation_stack": "主键唯一、日期范围、未来函数、缺失异常、IC、组合收益和约束审计",
            "stop_rule": "找到首个过关因子即冻结；若过关因子疑似过拟合则继续复核。",
            "current_status": "design_ready_needs_runner",
        },
    ]


def insert_rows(con: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"insert into {table} ({','.join(cols)}) values ({placeholders})"
    con.executemany(sql, [[row.get(c) for c in cols] for row in rows])


def render_status_md(summary: dict[str, Any], gate_rows: list[dict[str, Any]], gaps: list[GapItem]) -> str:
    lines = [
        "# V4 Audit Feedback Optimize Status",
        "",
        "## Scope",
        "",
        f"- Window: {START_DATE} to {END_DATE}",
        f"- Train: {TRAIN_WINDOW}",
        f"- Valid: {VALID_WINDOW}",
        f"- Test: {TEST_WINDOW}",
        f"- Strict target: {STRICT_TARGET}",
        "",
        "## Current Formal Gate",
        "",
        f"- Strict pass count: {summary['strict_pass_count']}",
        f"- Formal model-universe pairs checked: {len(gate_rows)}",
        "- Status: not final; repair iteration is still required.",
        "",
        "## Main Gaps",
        "",
    ]
    for gap in gaps:
        lines.append(f"- [{gap.severity}] {gap.gap_id} {gap.module_name}: {gap.gap_title}")
    lines += [
        "",
        "## Best Formal Rows",
        "",
    ]
    ranked = sorted(
        gate_rows,
        key=lambda x: (
            -999 if x["test_annual"] is None else x["test_annual"],
            -999 if x["test_sharpe"] is None else x["test_sharpe"],
        ),
        reverse=True,
    )
    for row in ranked[:10]:
        lines.append(
            "- {universe}/{model}: test annual={ta:.2%}, test Sharpe={ts:.3f}, "
            "full annual={fa:.2%}, full Sharpe={fs:.3f}, pass={pf}".format(
                universe=row["universe"],
                model=row["model_name"],
                ta=row["test_annual"] or 0.0,
                ts=row["test_sharpe"] or 0.0,
                fa=row["full_annual"] or 0.0,
                fs=row["full_sharpe"] or 0.0,
                pf=bool(row["pass_flag"]),
            )
        )
    lines += [
        "",
        "## Next Repair Order",
        "",
        "1. Field lineage and full data contract.",
        "2. Full-text or summary-level report evidence upgrade.",
        "3. K-line executable skill completion and fusion binding.",
        "4. Factor auto-mining and DL representation runner.",
        "5. Asset allocation BL/risk-budget replacement.",
        "6. Industry/style scorecards and ETF mapping.",
        "7. Index-enhancement optimizer and yearly attribution.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_PROJECT_ROOT / "database" / "research_warehouse.db"))
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--run-id", default=RUN_DEFAULT)
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    db_path = (project_root / args.db).resolve() if not Path(args.db).is_absolute() else Path(args.db)
    artifact_dir = project_root / "framework" / "reference_artifacts" / "agentconsole_v10_final"
    out_dir = project_root / "output" / "framework" / "audit" / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    execute_schema(con, project_root)
    reset_run(con, args.run_id)

    started = now_iso()
    con.execute(
        """
        insert into v4_run_manifest
        (run_id, started_at, status, start_date, end_date, train_window, valid_window, test_window, strict_target, message)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            args.run_id,
            started,
            "running",
            START_DATE,
            END_DATE,
            TRAIN_WINDOW,
            VALID_WINDOW,
            TEST_WINDOW,
            STRICT_TARGET,
            "Building audit-feedback-optimize control layer from V3 formal audit and AgentConsole artifacts.",
        ),
    )

    cards = method_cards_from_sources(con, artifact_dir)
    insert_rows(
        con,
        "v4_method_card",
        [
            {
                "run_id": args.run_id,
                "card_id": c.card_id,
                "module_name": c.module_name,
                "report_title": c.report_title,
                "org": c.org,
                "report_date": c.report_date,
                "evidence_level": c.evidence_level,
                "method_summary": c.method_summary,
                "required_data": c.required_data,
                "expected_model_change": c.expected_model_change,
                "current_gap": c.current_gap,
                "adoption_status": c.adoption_status,
            }
            for c in cards
        ],
    )

    gaps = gap_register_items()
    insert_rows(
        con,
        "v4_gap_register",
        [
            {
                "run_id": args.run_id,
                "gap_id": g.gap_id,
                "module_name": g.module_name,
                "severity": g.severity,
                "gap_title": g.gap_title,
                "current_state": g.current_state,
                "required_state": g.required_state,
                "repair_action": g.repair_action,
                "acceptance_gate": g.acceptance_gate,
                "status": g.status,
                "owner_agent": g.owner_agent,
            }
            for g in gaps
        ],
    )

    gate = strict_gate_rows(con)
    insert_rows(
        con,
        "v4_strict_gate",
        [
            {
                "run_id": args.run_id,
                "universe": x["universe"],
                "model_name": x["model_name"],
                "test_annual": x["test_annual"],
                "test_sharpe": x["test_sharpe"],
                "full_annual": x["full_annual"],
                "full_sharpe": x["full_sharpe"],
                "pass_flag": x["pass_flag"],
                "issue": x["issue"],
            }
            for x in gate
        ],
    )

    specs = model_upgrade_specs(gate)
    insert_rows(
        con,
        "v4_model_upgrade_spec",
        [{"run_id": args.run_id, **x} for x in specs],
    )

    cycles = agent_review_cycles(gaps, gate)
    insert_rows(
        con,
        "v4_agent_review_cycle",
        [{"run_id": args.run_id, **x} for x in cycles],
    )

    kline_index = generate_kline_skill_docs(con, args.run_id, project_root)
    insert_rows(
        con,
        "v4_kline_skill_doc_index",
        [{"run_id": args.run_id, **x} for x in kline_index],
    )

    factor_plans = factor_agent_plans()
    insert_rows(
        con,
        "v4_factor_agent_plan",
        [{"run_id": args.run_id, **x} for x in factor_plans],
    )

    strict_pass_count = sum(x["pass_flag"] for x in gate)
    summary = {
        "run_id": args.run_id,
        "started_at": started,
        "ended_at": now_iso(),
        "status": "needs_iteration",
        "database": str(db_path),
        "source_artifact_dir": str(artifact_dir),
        "window": {"start": START_DATE, "end": END_DATE},
        "splits": {"train": TRAIN_WINDOW, "valid": VALID_WINDOW, "test": TEST_WINDOW},
        "strict_target": STRICT_TARGET,
        "strict_pass_count": strict_pass_count,
        "method_cards": len(cards),
        "gaps": len(gaps),
        "agent_review_steps": len(cycles),
        "kline_skill_docs": len(kline_index),
        "kline_executable_docs": sum(x["executable_flag"] for x in kline_index),
        "factor_agent_plans": len(factor_plans),
        "formal_gate_rows": len(gate),
    }
    write_json(out_dir / "v4_audit_feedback_optimize_status.json", summary)
    (out_dir / "v4_audit_feedback_optimize_status.md").write_text(
        render_status_md(summary, gate, gaps),
        encoding="utf-8",
    )

    con.execute(
        """
        update v4_run_manifest
        set ended_at = ?, status = ?, message = ?
        where run_id = ?
        """,
        (
            summary["ended_at"],
            summary["status"],
            (
                f"V4 control layer built: {len(cards)} method cards, {len(gaps)} gaps, "
                f"{len(kline_index)} K-line skill docs, strict pass count {strict_pass_count}."
            ),
            args.run_id,
        ),
    )
    con.commit()
    con.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
