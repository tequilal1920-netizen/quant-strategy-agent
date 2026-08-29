# 组合优化模块地图

- 一级标题：组合优化
- 二级页面：优化求解器、宽基择时、指数增强
- 本地模型：`model/portfolio_optimization`
- 正式引擎：`model/portfolio_optimization/portfolio_optimization_engine.py`
- 正式快照：`board/quant_strategy_agent/data/portfolio_optimization_snapshot.json`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 模块元数据：`model/portfolio_optimization/MODULE.json`

原资产池、风险约束和优化求解归入“优化求解器”的页内控件；宽基择时、目标权重、候选切换、指数增强和组合回测分别归入“宽基择时”和“指数增强”。精确映射见 `framework/integration/ui_module_mapping.json`。
