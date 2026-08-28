# 资产配置框架 Skill

## 触发

当用户询问美林时钟、普林格周期、BL模型、宏观因子模型、风险预算模型、四资产权重、周期状态或资产配置回测时使用。

## 模型入口

- AI 入口：`agent/ai-models/asset-allocation`
- 模型源码：`agent/model/asset_allocation`
- 网页数据：`agent/board/quant_strategy_agent/data/asset_allocation_snapshot.json`

## 工作流程

1. 读取当前快照，确认周期状态、资产权重、数据截止和生成时间。
2. 若更新模型，先更新底层资产、宏观因子和协方差，再生成交互式 payload。
3. 美林和普林格负责周期识别；BL、宏观因子、风险预算负责权重生成与回测。
4. 测试期只报告，不能根据已观察测试继续调参或宣称无条件生产有效。
