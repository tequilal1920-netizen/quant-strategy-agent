# Source Policy

本项目的信源政策用于防止“模型幻觉式复刻”。

## 信源等级

### S 级：用户明确指定的原始材料

包括：

- 用户给出的 PDF、docx、ipynb、数据库、网页链接。
- 本地 `G:\subject` 项目。
- 用户指定的券商研报平台和高校数据库。

用途：

- 直接作为模型设计依据。
- 可进入证据地图。
- 可作为复刻参数来源。

### A 级：权威券商金工/资配报告

包括：

- 华泰金工 GPT 因子工厂、GPT-Kline、GPT 如海、PortfolioNet 等系列。
- 国信 AI 赋能资产配置系列。
- 国金大模型赋能投研、OpenClaw、MCP 研报复现、投资大师智能体等系列。
- 中信、中信建投、中金、招商、申万、海通、广发、兴业、长江等金工方法论报告。

用途：

- 作为策略结构和 Agent 工作流依据。
- 报告明确披露的参数可进入复刻 SOP。
- 报告未披露的细节不得伪装为报告原文。

### B 级：官方和稳定金融数据源

包括：

- Tushare。
- AKShare。
- BaoStock。
- iFind。
- CSMAR。
- PPMData。
- 交易所、指数公司、央行、统计局、基金公司官方数据。

用途：

- 作为行情、财务、宏观、指数、基金、行业数据来源。
- 必须记录接口、字段、时间戳、批次。
- 高额度或付费接口需先小样本验证再调用。

### C 级：工程参考项目

包括：

- Microsoft Qlib / RD-Agent。
- Model Context Protocol servers。
- RAGFlow。
- MinerU。
- OpenBB。
- TradingAgents。
- OpenClaw 类 Workspace/Skills/Cron 思路。

用途：

- 参考工程架构、Agent 工作流、RAG、MCP、自动化实验管理。
- 不直接作为金融结论来源。

### D 级：普通网页与媒体

用途：

- 只作补充检索、新闻背景或线索。
- 不可作为核心参数来源。

## 禁止事项

- 不允许把模型自己的猜测写成券商报告做法。
- 不允许用未来数据补齐历史回测。
- 不允许在代码、notebook、网页、日志中明文保存 token、账号或密码。
- 不允许绕过用户确认自动进入下一个 Agent。
- 不允许为了提升收益删除风险约束、交易成本、审计步骤。

## 未披露细节处理

如果报告未披露关键细节，必须进入：

```text
assumptions_pending_review.yml
```

每条假设必须包含：

- `assumption_id`
- `missing_detail`
- `proposed_value`
- `reason`
- `source_status`
- `requires_user_approval`

