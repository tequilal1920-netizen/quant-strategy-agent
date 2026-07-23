# 策略学习过程

本框架按“书本与研报学习 -> 方法抽取 -> Agent 交叉审计 -> 真实回测 -> Skill 沉淀”执行。

## 学习链路
- source_ingestion：收集内地量化、技术分析、资产配置书本章节和券商金工深度报告。 输出 `source_cards / report_cards`。
- method_extraction：抽取策略 DNA：假设、字段、窗口、调仓、风控、交易成本、失败场景。 输出 `strategy_dna.json`。
- agent_debate：宏观、行业、技术、因子、基本面、组合 Agent 交叉审计信号方向。 输出 `agent_votes.json`。
- backtest_audit：真实数据库回测，检查未来函数、公告可得性、路径止损和样本不足。 输出 `model_leaderboard / target_gate_report`。
- skill_lifecycle：只把稳定、可解释、可复现且样本外待审的规则沉淀为 skill。 输出 `skill_registry / changelog`。

## 书本与研报映射
- `technical_skill_agent`：书本依据：丁鹏《量化投资：策略与技术》；丁鹏《量化投资：数据挖掘技术与实践》；国内证券投资技术分析教材中的趋势、形态、量价、止损和交易纪律章节。研报依据：华泰金工 GPT-Kline 系列；国信证券 AI 赋能资产配置系列中的技术分析和 Agent 化研究。
- `factor_factory_agent`：书本依据：丁鹏《量化投资：策略与技术》；国内多因子选股、统计套利、机器学习量化投资教材中的因子检验章节。研报依据：华泰金工 GPT 因子工厂、GPT 因子工厂 2.0；民生、海通、广发、招商等机器学习/深度学习因子研究。
- `industry_rotation_agent`：书本依据：国内行业比较、资产配置、金融工程实证研究教材中的景气、估值、资金和拥挤章节。研报依据：国信证券《AI赋能资产配置（三十八）：Agent赋能开发行业轮动策略》；国金证券行业轮动双周度跟踪；金融街证券行业轮动 ETF 策略周报。
- `asset_allocator_agent`：书本依据：国内资产配置、FOF、风险预算和组合管理教材中的股债商品现金配置章节。研报依据：金融街、中银、华安等 ETF 资产配置、全天候、BL 和策略主题 ETF 研究。
- `portfolio_optimizer_agent`：书本依据：国内组合管理、风险模型和量化投资教材中的优化、约束和交易成本章节。研报依据：华泰金工 PortfolioNet；国金证券大模型投研、MCP 研报复现和投研 Agent 协同框架。

## 模型依据
- `ai_factor_factory`：华泰金工：GPT 因子工厂、GPT 因子工厂 2.0、大模型 + 强化学习因子挖掘；民生/海通/广发/招商等机器学习和深度学习因子模型动物园。
- `ai_kline_skill`：华泰金工：GPT-Kline、技术分析大模型化；国信证券 AI 赋能资产配置系列：技术分析与 Agent 化信号审计。
- `agent_industry_rotation`：国信证券 2026-06-16《AI赋能资产配置（三十八）：Agent赋能开发行业轮动策略》；国金证券行业轮动双周度跟踪、金融街证券行业轮动ETF策略周报。
- `asset_allocation_etf`：金融街/中银/华安 ETF 资产配置、全天候、BL、行业配置和因子配置研究；金融街证券 2026-06-10《主题ETF系列研究报告：策略主题ETF：A股市场因子配置的精细化工具》。
- `portfolio_optimizer`：华泰金工 PortfolioNet、组合优化与风险预算框架；国金证券大模型投研、MCP 研报复现和投研 Agent 协同框架。