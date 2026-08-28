# 组合优化框架 Skill

## 触发

当用户询问优化求解器、宽基择时、指数增强、LLM约束、行业/风格约束、跟踪误差、权重和归因时使用。

## 模型入口

- AI 入口：`agent/ai-models/portfolio-optimization`
- 组合优化源码：`agent/model/portfolio_optimization`
- 指数增强源码：`agent/model/index_enhancement`
- 网页前端：`agent/board/quant_strategy_agent/static/js/portfolio_optimizer.js`

## 工作流程

1. 优化求解器先读取资产池、得分、约束知识库和最新可认证 run。
2. LLM 只负责编译和解释约束草案，最终权重必须由 HiGHS/Clarabel 等求解器生成并认证。
3. 宽基择时按宏观、量价、情绪、估值四维构造攻防信号和五档仓位。
4. 指数增强需说明 beta、alpha、行业暴露、风格暴露、约束余量和回测门禁。
