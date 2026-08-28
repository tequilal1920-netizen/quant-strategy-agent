# 数据看板框架 Skill

## 触发

当用户询问宏观、全球市场、行业、大宗商品、个股、新闻事件、资金面、AI监控、川普指数、内需股或数据看板自动更新时使用。

## 模型入口

- 网页源码：`agent/board/quant_strategy_agent`
- AI 入口：`agent/ai-models/data-dashboard`
- 数据看板源码：`agent/model/data_dashboard`
- 资金面模型：`agent/model/liquidity_tracking`
- 快照数据：`agent/board/quant_strategy_agent/data`

## 工作流程

1. 先查快照和 API 状态，确认数据截止日、生成时间和来源。
2. 再判断是否需要运行增量更新脚本；能用本地数据库解决时不要高频打外部授权接口。
3. 宏观月频、季频数据按官方发布节奏更新；日频行情、新闻、研报和资金数据按可得日度快照更新。
4. 回答时明确区分数据事实、模型推断、AI 观点和缺失风险。
