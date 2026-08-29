# 数据看板框架 Skill

## 触发

当用户询问宏观、全球市场、行业、大宗商品、个股、新闻事件、资金面、AI监控、川普指数、内需股或数据看板自动更新时使用。

## 模型入口

- 网页源码：`agent/board/quant_strategy_agent`
- AI 入口：`agent/ai-models/data-dashboard`
- 数据看板源码：`agent/model/data_dashboard`
- 资金面模型：`agent/model/liquidity_tracking`
- 快照数据：`agent/board/quant_strategy_agent/data`
- MCP 目录：`agent/mcp/model_catalog.json`
- 数据更新：`agent/framework/data_pipeline`

## 覆盖页面

- 市场监控：宏观、全球市场、行业、大宗商品、个股、新闻事件。
- 专题跟踪：资金面、AI监控、川普指数、内需股。
- 个股页：股票池搜索、行情、公告新闻、最新研报、AI分析和深度报告。
- AI监控：一级行业、三级行业、时序指标、时间窗口、平滑、可靠样本、权重合成、扩散指数/扩散分数/一级行业对标。

## 核心数据逻辑

1. 宏观月频、季频指标按官方发布时间和数据库可得日更新，不伪造成日频。
2. 行情、行业、大宗商品、个股、资金、新闻和研报按交易日或数据源可得日增量更新。
3. 资金面专题读取本地资金状态库与快照，字段必须保留来源、单位、截止日、较前值和质量状态。
4. AI监控按股票级特征聚合到行业：先计算个股收益广度、贡献扩散、预期修正、调研热度、资金扩散，再用行业映射汇总到三级和一级行业。
5. 川普指数和内需股只链接最初始数据源，不能暴露内部镜像或中转页面。

## 工作流程

1. 先查快照和 API 状态，确认数据截止日、生成时间和来源。
2. 再判断是否需要运行增量更新脚本；能用本地数据库解决时不要高频打外部授权接口。
3. 宏观月频、季频数据按官方发布节奏更新；日频行情、新闻、研报和资金数据按可得日度快照更新。
4. 回答时明确区分数据事实、模型推断、AI 观点和缺失风险。
5. 涉及网页修复时同时核验主站与 `board/quant_strategy_agent_vnext`，避免资产配置或组合优化回滚其他板块。
6. 修改 UI 后检查表格、色阶条、图例、横向溢出、跳转、缓存和空数据口径。

## 查询与验收

```powershell
python ai-models/data-dashboard/scripts/query.py overview
python ai-models/data-dashboard/scripts/query.py market 数量=10
python board/quant_strategy_agent/qa/test_canonical_app.py
```

验收时至少检查 `/api/board/snapshot`、`/api/stock/universe`、`/api/board/stock/<code>`、`/api/stock/news/<code>`、`/api/stock/reports/<code>`、`/api/liquidity/snapshot`、`/api/trump/core` 和公网 `/healthz`。
