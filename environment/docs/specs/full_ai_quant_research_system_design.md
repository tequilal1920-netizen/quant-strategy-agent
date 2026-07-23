# Full AI Quant Research System Design

更新时间：2026-07-03

## 0. 目标

构建一个可以完成课题、可以逐步部署成网页应用的 AI 投研系统：

```text
权威研报证据库
  -> 周报生产
  -> 周期划分与大类资产配置
  -> 风格与行业轮动
  -> 行业内个股增强
  -> 组合优化与回测
  -> 实时跟踪与复盘
  -> Agent / Skill 持续进化
  -> 网页应用展示与远程执行
```

项目定位：

- 不是普通多因子模型。
- 不是传统美林时钟拼盘。
- 不是让大模型直接给买卖建议。
- 是一个以券商金工报告为证据、以大模型 Agent 为投研引擎、以量化回测为审计约束的端到端系统。

## 1. 证据原则

### 1.1 研报证据

已在 `report/specs/recent_ai_quant_report_index.csv` 生成 2024-01-01 至 2026-07-03 的标题级研报索引，共 302 条。后续需要精读 PDF 或平台原文后，才能把具体参数写入 SOP。

优先精读方向：

1. 国信证券 AI 赋能资产配置系列：AI 投资时钟、TradingAgents、Transformer 到 Agent、Deep Research、Agent 行业轮动、技术分析。
2. 华泰金工 GPT 因子工厂、GPT 因子工厂 2.0、GPT-Kline、GPT 如海、PortfolioNet、大模型 + 强化学习因子挖掘。
3. 国金证券大模型投研、MCP 研报复现、OpenClaw、投资大师智能体、双周期风格行业轮动。
4. 民生/海通/广发/招商等深度学习因子、机器学习选股、模型动物园。
5. 金融街/中银/华安等 ETF 资产配置、全天候、BL、行业配置和因子配置。

### 1.2 数据证据

数据源按优先级：

1. 本地 `G:\subject`，后续只读扫描。
2. Tushare / AKShare / BaoStock 小样本验证。
3. iFind / CSMAR / PPMData 只做关键缺口补齐，严控额度。
4. WarrenQ / CNKI / CSMAR 网页只用于研报、学术和数据说明检索。

### 1.3 AI 证据

大模型输出不能直接作为结论，必须落到：

- 结构化 signal。
- evidence id。
- confidence。
- backtest result。
- audit result。
- failure mode。

## 2. 总体架构

```text
report/
  source_registry.yml
  data_registry.yml
  agent_registry.yml
  skill_registry.yml
  specs/
  agents/
  adapters/
  artifacts/
  web/
  remote/
```

核心流水线：

```text
Source Librarian
  -> Research RAG Agent
  -> Weekly Report Agent
  -> Macro Regime Agent
  -> Asset Allocator Agent
  -> Style Rotation Agent
  -> Industry Rotation Agent
  -> LHB Event Agent
  -> Technical Skill Agent
  -> Factor Factory Agent
  -> Fundamental Research Agent
  -> Stock Selector Agent
  -> Portfolio Optimizer Agent
  -> Backtest Auditor Agent
  -> Model Doctor Agent
  -> Web Publisher Agent
```

## 3. 模块 1：周报系统

### 3.1 目标

每周生成一份可被模型消费的投研周报，不只是文字报告。

输出：

- `weekly_report.md`
- `weekly_report.html`
- `macro_state.json`
- `event_graph.json`
- `industry_weekly_panel.parquet`
- `lhb_deep_dive.json`
- `weekly_signal_bundle.json`

### 3.2 宏观数据跟踪

输入：

- 经济增长：PMI、工业增加值、社零、固定资产投资、出口。
- 通胀：CPI、PPI、商品价格、猪肉、原油。
- 信用：社融、M2、信贷、票据利率、信用利差。
- 流动性：DR007、R007、国债收益率、央行操作。
- 海外：美元指数、美债、联储预期、全球股债商品。

AI 用法：

- LLM 读取央行、统计局、会议、政策文本。
- 抽取政策立场：宽松、紧缩、产业支持、风险防控。
- 生成宏观叙事摘要，并映射到资产影响。

量化输出：

```json
{
  "growth_score": 0.35,
  "inflation_score": -0.10,
  "credit_score": 0.62,
  "liquidity_score": 0.58,
  "policy_stance": "pro_growth",
  "risk_appetite": 0.41,
  "evidence_ids": ["macro_20260703_001"]
}
```

验证：

- 宏观状态对权益、债券、商品后续 1/4/12 周收益的解释力。
- 状态切换是否提前于市场波动。
- LLM 政策立场与人工标签一致率。

### 3.3 热点事件跟踪

输入：

- 新闻、政策、产业事件、海外事件、公司重大公告、研报标题。

AI 用法：

- RAG 检索事件上下文。
- LLM 抽取事件实体、方向、影响链、时间窗口和置信度。
- 构建 Event-to-Asset Graph。

输出：

```json
{
  "event": "AI算力资本开支上修",
  "affected_assets": ["A股权益", "有色", "电力设备", "通信"],
  "affected_industries": ["通信", "电子", "电力设备", "公用事业"],
  "direction": "positive",
  "horizon": "4-12 weeks",
  "confidence": 0.76
}
```

创新指标：

- Narrative Momentum：主题文本 embedding 的热度、扩散速度、跨行业传播。
- Event Similarity Return：相似历史事件后的 1/5/20 日行业超额。
- Evidence Robustness：事件证据来源数量、权威等级、是否互相印证。

### 3.4 行业数据复盘

每个申万一级行业建立指标卡。

指标维度：

- 景气：价格、销量、库存、开工率、产能利用率。
- 盈利：毛利代理、盈利预测上修、财报 surprise。
- 估值：PE、PB、ERP、历史分位。
- 资金：ETF 份额、北向、两融、成交占比、机构调研。
- 拥挤：换手率、波动、成交集中度、研报覆盖热度。
- 技术：趋势、突破、量价、波动收缩。
- 叙事：政策、AI、出口、涨价、国产替代、产业链催化。

AI 用法：

- 行业 Agent 读取行业新闻和研报，解释分数变化。
- Judge Agent 检查解释是否和数据方向一致。

输出：

- 行业周报卡片。
- 行业得分矩阵。
- 行业配置候选。

### 3.5 龙虎榜个股深度

目标：

判断龙虎榜事件是否具备后续持续性，而不是追逐短线传闻。

指标：

- 上榜原因。
- 机构席位、沪深股通、营业部集中度。
- 净买入、连续上榜、买卖分歧。
- 所属主题和事件催化。
- 同类事件后验收益。

AI 用法：

- LLM 汇总公司公告、新闻、互动易、研报摘要，解释上榜原因。
- 输出风险标签：短炒、一日游、基本面支持、趋势强化、监管风险。

## 4. 模块 2：周期划分与大类资产配置

### 4.1 目标

输出未来 1-4 周或 1-3 月的：

- 权益仓位。
- 债券仓位。
- 商品/黄金仓位。
- 现金仓位。
- 可跟踪 ETF 权重。

### 4.2 周期划分

不采用单一美林时钟。使用五模型集成：

1. Macro Regime：增长、通胀、信用、流动性。
2. Market Regime：趋势、波动、相关性、尾部风险。
3. Policy Regime：LLM 政策立场和政策强度。
4. Narrative Regime：市场主线和叙事扩散。
5. Change Point：结构突变检测。

输出：

```json
{
  "regime": "liquidity_easing_growth_repair",
  "equity_budget": 0.65,
  "bond_budget": 0.20,
  "commodity_budget": 0.10,
  "cash_budget": 0.05,
  "confidence": 0.72
}
```

### 4.3 资产配置模型

模型集：

- HRP / Risk Parity：稳健 baseline。
- Trend + Vol Target：控制回撤。
- Black-Litterman：融合 AI 观点。
- Regime-Conditional Allocation：不同周期下使用不同风险预算。
- LLM View Generator：从周报和事件图谱生成资产观点。

LLM-BL 流程：

1. LLM 生成观点：权益多/中性/空，债券多/空，黄金多/空。
2. Evidence Auditor 检查观点证据。
3. Calibrator 把观点转成 `P/Q/Omega`。
4. BL 生成后验收益。
5. Optimizer 输出 ETF 权重。
6. Risk Overlay 加波动率目标和现金保护。

ETF 初始池：

- 权益宽基：沪深300、中证500、中证1000、创业板、科创50。
- 债券：国债、政金债、信用债、短债、货币。
- 商品：黄金、有色、能源、农产品类 ETF。
- 跨境：恒生科技、纳指、标普、日经，待数据和账户可得性确认。

## 5. 模块 3：风格与行业轮动

### 5.1 风格轮动

风格：

- 成长
- 价值
- 红利
- 质量
- 小盘
- 大盘
- 动量
- 低波

指标：

| 风格 | 核心指标 |
| --- | --- |
| 成长 | 盈利上修、收入增速、研发强度、产业叙事、估值容忍度 |
| 价值 | 低估值、ROE 修复、现金流、股债性价比 |
| 红利 | 股息率、现金流稳定、利率下行、防御需求 |
| 小盘 | 流动性、风险偏好、并购重组、成交扩散 |
| 质量 | ROE、现金流、负债率、盈利稳定 |
| 低波 | 波动、回撤、防御行业占比 |

AI 用法：

- 读取市场周报和政策事件，判断风格叙事是否扩散。
- 解释风格切换原因，并给出失效条件。

### 5.2 行业轮动

行业总分：

```text
Industry Score =
  macro_beta_score
  + prosperity_score
  + earnings_revision_score
  + valuation_score
  + flow_score
  + technical_score
  + narrative_momentum_score
  + event_catalyst_score
  - crowding_penalty
```

AI 创新：

- Industry Agent Debate：每个行业由宏观、景气、资金、技术、事件五个 Agent 辩论。
- Knowledge Graph：行业间产业链关系、利率敏感、出口敏感、AI 映射关系。
- Narrative Half-life：主题热度衰减速度，避免追高。
- Rotation Stability：将换手成本和信号持续性放进优化。

输出：

- 申万一级行业权重。
- 行业 ETF 映射。
- 行业配置解释。
- 行业轮动回测。

## 6. 模块 4：个股模型

### 6.1 技术面 Skill

目标：

把 K 线技术分析变成可调用、可测试、可版本化 skill，而不是主观画线。

Skill 列表：

- 趋势突破。
- 均线结构。
- 量价背离。
- 波动收缩。
- 缺口分析。
- 支撑阻力。
- K 线形态。
- 止损止盈。

每个 Skill 必须包含：

- 规则来源。
- 输入 OHLCV 字段。
- 参数。
- 输出 signal/confidence/invalid_condition。
- 反例。
- 测试样例。

多模态扩展：

- 生成 K 线图片。
- 视觉模型识别形态。
- 结构化规则复核。
- 两者冲突时交给 Technical Judge。

### 6.2 因子挖掘

因子体系：

1. 人工因子库：估值、质量、成长、动量、反转、波动、流动性。
2. AutoFE：自动构造滞后、滚动、交叉、标准化因子。
3. 深度学习因子：Transformer、TCN、TabNet、GNN、MLP-Mixer。
4. LLM Factor Factory：大模型从研报/财报/产业逻辑提出因子假设和表达式。
5. RL/Program Search：表达式搜索生成候选因子。

上线门槛：

- 样本外 RankIC。
- 分组收益单调。
- 换手和交易成本可接受。
- 与已有因子相关性低。
- 行业/市值/风格暴露可控。
- 衰减周期清楚。
- 无未来函数。

### 6.3 基本面深度报告

输入：

- 财报。
- 公告。
- 盈利预测。
- 新闻。
- 行业数据。
- 研报摘要。
- 机构调研。
- 龙虎榜和事件。

AI 输出：

```text
业务拆解
收入/利润驱动
行业景气
竞争格局
财务质量
盈利预测情景
估值比较
催化剂
风险
量化标签
是否进入候选池
```

关键要求：

- 每段结论绑定 evidence id。
- 最终必须回写结构化标签，服务于选股模型。

## 7. 模块 5：组合优化与回测

### 7.1 权重合成

输入：

- 大类资产权重。
- 权益仓位。
- 风格权重。
- 行业权重。
- 行业内股票 alpha。
- 风险模型。
- 交易成本。

目标函数：

```text
maximize expected_alpha
  - lambda_risk * risk
  - lambda_turnover * turnover
  - lambda_cost * transaction_cost
  - lambda_crowding * crowding
```

约束：

- 权益总仓位不超过资产配置预算。
- 行业权重不偏离行业模型过多。
- 个股上限。
- 流动性上限。
- ST、停牌、涨跌停过滤。
- Beta、波动、回撤和风格暴露约束。

### 7.2 回测

分层回测：

1. 大类 ETF 配置。
2. 风格 ETF 轮动。
3. 行业 ETF 轮动。
4. 行业内选股。
5. 技术面 overlay。
6. 全组合。

指标：

- 年化收益。
- 波动。
- Sharpe。
- Calmar。
- 最大回撤。
- 换手率。
- 交易成本。
- 信息比率。
- 跟踪误差。
- 分市场状态表现。
- 样本外表现。

消融实验：

```text
Full System
- AI narrative
- macro regime
- industry prosperity
- factor alpha
- technical skill
- fundamental tags
- optimizer
```

归因：

```text
组合收益 =
  大类资产配置贡献
  + 权益择时贡献
  + 风格轮动贡献
  + 行业轮动贡献
  + 个股选择贡献
  + 技术面 overlay
  - 交易成本
  + 残差
```

## 8. 模块 6：复盘与实时跟踪

### 8.1 日度跟踪

输出：

- 当前组合收益。
- 当日归因。
- 风险暴露。
- 行业/风格偏离。
- 异常事件。
- 需要人工确认的事项。

### 8.2 周度复盘

输出：

- 预测 vs 实际。
- 哪些 Agent 判断正确。
- 哪些信号失效。
- 是否需要更新 skill。
- 下周配置建议。

### 8.3 Model Doctor

失败归因：

- 数据延迟。
- 文本误读。
- 信号衰减。
- 市场状态切换。
- 交易成本过高。
- 拥挤度过高。
- 行业映射错误。
- 个股黑天鹅。

输出：

- `model_diagnosis.md`
- `failure_tree.json`
- `next_experiment_plan.yml`

## 9. Agent 与 Skill 管理

### 9.1 Agent 规范

每个 Agent 一个文件夹：

```text
AGENT.md
input_schema.json
output_schema.json
api_contract.md
evidence_policy.md
tests.md
outputs/
```

Agent 状态：

```text
draft -> tested -> validated -> production -> deprecated
```

### 9.2 Skill 规范

Skill 不是 prompt 片段，而是可测试的能力模块。

```text
SKILL.md
input_schema.json
output_schema.json
examples/
tests/
CHANGELOG.md
evidence.md
```

更新流程：

1. 发现失败模式。
2. 提出 skill 更新假设。
3. 绑定研报/数据/回测证据。
4. 修改 skill。
5. 运行测试。
6. 样本外验证。
7. 用户确认后升版。

## 10. 网页应用

### 10.1 页面

1. Evidence：研报证据和数据源。
2. Weekly：宏观、事件、行业、龙虎榜。
3. Regime：周期和权益仓位。
4. Allocation：大类 ETF 配置。
5. Rotation：风格和行业轮动。
6. Stock：技术、因子、基本面。
7. Portfolio：组合权重和调仓。
8. Backtest：净值、归因、消融。
9. Agents：Agent 状态。
10. Skills：Skill 版本。

### 10.2 AI 运行模式

三种模式：

| 模式 | 说明 | 是否需要 OpenAI API key |
| --- | --- | --- |
| Local Demo | 验证网页链路 | 否 |
| Codex Bridge | 当前 Codex 会话处理网页队列 | 否，但需要 Codex 在线参与 |
| API-backed | 网页后台自动调用模型 | 是，或需要远程 Codex 可程序化运行环境 |

结论：

- 当前账号可以支持我在 Codex 中构建、分析、生成和半自动处理队列。
- 完全无人值守 AI 网页后端，不能只依赖 ChatGPT 网页登录态，需要可调用的后端模型运行时。

### 10.3 SHH 远程部署

原则：

- 不覆盖 SHH。
- 新站点放在 `report/web/quant_ai_console`。
- 远程脚本放在 `report/remote`。
- 长任务在远程执行，网页只读 artifacts。

## 11. 课题写作结构

论文/课题可写成：

1. 引言：AI Agent 在量化投研中的价值。
2. 文献与券商研报综述。
3. 系统架构：数据、证据、Agent、Skill、网页。
4. 周报到资产配置模型。
5. 风格行业轮动模型。
6. 个股增强模型。
7. 组合优化和回测。
8. 实证结果与归因。
9. 消融实验。
10. 风险与局限。
11. 结论与后续扩展。

## 12. 最小可行实施顺序

MVP 1：网页和 Agent 框架。

- 完成当前 Quant AI Console。
- 创建 Agent registry。
- 创建 evidence registry。
- 完成研报索引。

MVP 2：ETF 大类配置。

- 数据字段确认。
- ETF 池确认。
- 周期识别 baseline。
- LLM-BL 观点模板。
- 回测。

MVP 3：行业轮动。

- 申万一级行业指标卡。
- 行业 ETF 映射。
- Narrative Momentum。
- 行业轮动回测。

MVP 4：个股增强。

- 技术 skill 最小集。
- 因子库最小集。
- 基本面报告 Agent。
- 行业内选股回测。

MVP 5：全组合。

- 组合优化。
- 归因。
- 实时跟踪。
- 网页 dashboard。

## 13. 下一步

建议下一步正式生成：

- `report/agent_registry.yml`
- `report/source_registry.yml`
- `report/data_registry.yml`
- `report/skill_registry.yml`
- `report/agents/*`

并只做小样本数据验证，不调用 iFind 全量额度。
