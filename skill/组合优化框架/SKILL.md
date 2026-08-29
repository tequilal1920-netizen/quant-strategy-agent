# 组合优化框架 Skill

## 触发

当用户询问优化求解器、宽基择时、指数增强、LLM约束、行业/风格约束、跟踪误差、权重和归因时使用。

## 模型入口

- AI 入口：`agent/ai-models/portfolio-optimization`
- 组合优化源码：`agent/model/portfolio_optimization`
- 指数增强源码：`agent/model/index_enhancement`
- 网页前端：`agent/board/quant_strategy_agent/static/js/portfolio_optimizer.js`
- 查询脚本：`agent/ai-models/portfolio-optimization/scripts/query.py`

## 二级页面

- 优化求解器：资产池、得分、约束编译、候选方案、求解状态和目标权重。
- 宽基择时：任意宽基的宏观、量价、情绪、估值四维攻防信号与仓位。
- 指数增强：指定指数的个股信号、行业约束、风格暴露、alpha/beta、跟踪误差和归因。

## 核心计算

1. LLM 只负责把自然语言约束编译为候选方程和解释，不能直接产出最终权重。
2. 最终权重由 HiGHS、Clarabel 或正式求解器生成，并保留可行性、约束残差、风险贡献和成本敏感性。
3. 宽基择时按宏观、量价、情绪、估值四维形成攻防分数，再映射为五档仓位。
4. 指数增强必须同时看个股 alpha、行业偏离、风格暴露、跟踪误差、换手、成本和回测门禁。
5. 测试集和报告期不能用于挑选求解参数；不可行和残差超限必须直接暴露。

## 工作流程

1. 优化求解器先读取资产池、得分、约束知识库和最新可认证 run。
2. LLM 只负责编译和解释约束草案，最终权重必须由 HiGHS/Clarabel 等求解器生成并认证。
3. 宽基择时按宏观、量价、情绪、估值四维构造攻防信号和五档仓位。
4. 指数增强需说明 beta、alpha、行业暴露、风格暴露、约束余量和回测门禁。
5. 修改资产配置或指数增强后必须复查组合优化三个二级页面没有被误覆盖，也没有回退到技术分析或旧图表。

## 查询与验收

```powershell
python ai-models/portfolio-optimization/scripts/query.py current 最小权重=0.001
python ai-models/portfolio-optimization/scripts/query.py solver
python -m unittest model.portfolio_optimization.test_portfolio_optimization_engine -v
```

网页验收检查候选切换、重复点击、约束解释、权重表、宽基择时、指数增强、缓存键、跳转和 LLM 中转站状态。
