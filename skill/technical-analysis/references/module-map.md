# 技术分析模块地图

- 一级标题：技术分析
- 二级页面：K线学习、配置策略
- 一级聚合模型：`model/technical_analysis`
- K 线学习模型：`model/kline_memory_learning`
- 单股入口：`model/kline_memory_learning/single_stock_analyzer.py`
- 同类公司入口：`model/kline_memory_learning/cohort_wyckoff_learning.py`
- 横截面研究：`model/kline_memory_learning/cross_sectional_factor_study.py`
- 技能注册表：`model/kline_memory_learning/kline_skill_registry.yml`
- 形态记忆：`skill/technical-analysis/references/kline-patterns/`
- 统一服务入口：`board/quant_strategy_agent/main.py`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 一级模块元数据：`model/technical_analysis/MODULE.json`

“K线学习”页内保留任务设置、学习记忆、历史记录，以及规则排名、同类公司、情境记忆、进化记录；“配置策略”承接技术信号的组合化输出。精确映射见 `framework/integration/ui_module_mapping.json`。