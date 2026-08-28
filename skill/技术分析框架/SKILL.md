# 技术分析框架 Skill

## 触发

当用户询问技术因子、K线学习、个股仓位历史决策点、当前技术信号、形态记忆或技术归因时使用。

## 模型入口

- AI 入口：`agent/ai-models/technical-analysis`
- 技术分析源码：`agent/model/technical_analysis`
- K线记忆源码：`agent/model/kline_memory_learning`

## 工作流程

1. 先确认 OHLCV 数据截止日、股票池和可用缓存。
2. 技术因子侧看横截面因子、行业风格技术轮动和归因。
3. K线学习侧看历史决策点、当前形态、仓位建议和不确定性。
4. 如果上游 K线服务断开，先修复服务和缓存，不直接重训模型。
