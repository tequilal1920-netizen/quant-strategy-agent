# 因子实验室模块地图

- 一级标题：因子实验室
- 二级页面：因子看板、因子挖掘、配置策略
- 隔离实验模型：`model/factor_laboratory`
- LLM 挖掘模型：`model/llm_factor_mining`
- 指数增强模型：`model/index_enhancement`
- 因子 worker：`model/factor_laboratory/worker.py`
- LLM 挖掘入口：`model/llm_factor_mining/factor_miner.py`
- 指数增强入口：`model/index_enhancement/build_snapshot.py`
- 后端：`board/quant_strategy_agent/factor_lab_backend.py`
- 独立挖掘服务：`board/services/factor_mining`
- 产物：`output/factor_laboratory`、`output/llm_factor_mining`
- 状态库：`database/factor_lab_state.sqlite3`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 一级模块元数据：`model/factor_laboratory/MODULE.json`

页内控件必须保留实验挖掘、LLM挖掘、因子表达式、检验结果、综合打分、历史记忆，以及原指数增强的资产池、Alpha、SmartBeta、风险模型和组合跟踪。精确映射见 `framework/integration/ui_module_mapping.json`。