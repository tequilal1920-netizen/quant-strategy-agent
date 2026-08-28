# 行业风格框架 Skill

## 触发

当用户询问行业景气度、行业轮动、风格轮动、风格五因子、行业归因、月频持仓或行业信号时使用。

## 模型入口

- AI 入口：`agent/ai-models/industry-rotation`
- 模型源码：`agent/model/industry_rotation`
- 图表数据：`agent/board/quant_strategy_agent/data/rotation_snapshot.json`

## 工作流程

1. 行业景气度先看指标映射、方向、筛选检验、景气合成和回测。
2. 行业轮动看景气度、基本面、技术面、估值、资金面、拥挤度。
3. 风格轮动当前按五因子框架运行，不把行业专属景气度映射到风格域。
4. 所有候选必须遵守训练、验证、测试隔离，测试结果只报告。
