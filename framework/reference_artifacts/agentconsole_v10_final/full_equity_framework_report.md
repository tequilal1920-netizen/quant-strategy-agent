# 全权益研究框架执行报告

- 数据库：`database/research_warehouse.db`
- 股票池：`180`，交易日：`1317`，最新交易日：`20260612`
- ETF池：`160`
- 研报/新闻证据库记录：`418`

## 模型验收

- `stock_ai_kline_skill`：年化 40.28%，Sharpe 1.762，最大回撤 -7.24%，达标 `True`
- `stock_fundamental_quality`：年化 25.49%，Sharpe 1.679，最大回撤 -4.71%，达标 `True`
- `portfolio_agent_optimizer`：年化 25.02%，Sharpe 1.668，最大回撤 -10.13%，达标 `True`
- `style_agent_rotation`：年化 25.90%，Sharpe 1.659，最大回撤 -3.21%，达标 `True`
- `stock_factor_ai_composite`：年化 28.78%，Sharpe 1.647，最大回撤 -5.51%，达标 `True`
- `stock_factor_ai_factory`：年化 27.14%，Sharpe 1.635，最大回撤 -6.48%，达标 `True`
- `industry_agent_rotation`：年化 30.77%，Sharpe 1.632，最大回撤 -4.71%，达标 `True`
- `asset_allocation_etf`：年化 12.67%，Sharpe 1.135，最大回撤 -10.11%，达标 `False`

## 结论

本执行器不生成合成行情，不把未达标模型伪装为通过。未达标模型需要继续进入 Agent 诊断、风险预算、样本外和数据扩展迭代。