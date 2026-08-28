# 行业风格框架 Skill

## 触发

当用户询问行业景气度、行业轮动、风格轮动、风格五因子、行业归因、月频持仓或行业信号时使用。

## 模型入口

- AI 入口：`agent/ai-models/industry-rotation`
- 模型源码：`agent/model/industry_rotation`
- 图表数据：`agent/board/quant_strategy_agent/data/rotation_snapshot.json`
- 查询脚本：`agent/ai-models/industry-rotation/scripts/query.py`

## 二级页面

- 行业景气度：31个申万一级行业专属高频景气指标、排名、驱动归因。
- 行业轮动：景气度、基本面、技术面、估值、资金面、拥挤度六维候选与月/周频信号。
- 风格轮动：市值、成长/均衡/价值/红利等风格箱与月频信号。

## 核心计算

1. 高频景气与六维轮动是两套口径，不能互相替代。
2. 六维候选先在维度内筛选有效因子，再做组合权重；拥挤度作为风险扣分而不是收益维度。
3. 行业成分、财务可见日和资金流单位必须按 PIT 口径处理，避免未来函数。
4. 月频和周频候选只用训练/验证选择，测试期用于报告和否决，不参与调参。
5. 风格轮动只使用风格域可解释指标，不能把行业专属景气度直接映射成风格信号。

## 工作流程

1. 行业景气度先看指标映射、方向、筛选检验、景气合成和回测。
2. 行业轮动看景气度、基本面、技术面、估值、资金面、拥挤度。
3. 风格轮动当前按五因子框架运行，不把行业专属景气度映射到风格域。
4. 所有候选必须遵守训练、验证、测试隔离，测试结果只报告。
5. 回答单行业问题时输出数据截止、生产冠军、研究展示候选、六维得分、有效因子和风险扣分。

## 查询与验收

```powershell
python ai-models/industry-rotation/scripts/query.py ranking 频率=高频 数量=10
python ai-models/industry-rotation/scripts/query.py dimensions 频率=月频 行业=电子
python ai-models/industry-rotation/scripts/query.py backtest
```

网页验收检查行业景气度、行业轮动、风格轮动三页、图例、双图并排、日期轴、色阶表格和导航跳转。
