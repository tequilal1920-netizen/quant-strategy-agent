# 因子实验室框架 Skill

## 触发

当用户询问因子数据库、因子看板、因子检验、因子归因、LLM因子挖掘、个股打分回测、指数增强或 SmartBeta 时使用。

## 模型入口

- AI 入口：`agent/ai-models/factor-laboratory`
- 因子看板：`agent/model/factor_laboratory`
- LLM 因子挖掘：`agent/model/llm_factor_mining`
- 指数增强：`agent/model/index_enhancement`

## 工作流程

1. 先读取因子目录、champion manifest 和状态库。
2. 因子必须经过可得性、去极值、标准化、中性化、成熟标签、RankIC、ICIR、分层收益、换手和回撤检验。
3. LLM 只生成候选表达式、解释和研究假设，不能跳过回测治理。
4. 个股归因需要说明因子贡献、行业/风格暴露、资金和技术状态。
