# 技术分析模型

本目录是网页一级标题“技术分析”的聚合模型边界。现有 K 线学习算法继续保留在 `model/kline_memory_learning/`，本目录负责声明其与“K线学习、配置策略”两个二级页面的统一输入输出契约。

## 输入

- 股票、截止日、复权口径、学习窗口和预测周期
- 同类公司范围、形态记忆、技术指标与风险约束

## 输出

- 单股与同类公司诊断
- 规则排名、情境记忆和进化记录
- 技术配置策略、置信度、失效条件和风险提示

正式知识文档位于 `skill/technical-analysis/references/kline-patterns/`，Agent 工作流位于 `skill/technical-analysis/`。