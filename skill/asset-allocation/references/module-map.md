# 资产配置模块地图

- 一级标题：资产配置
- 二级页面：周期跟踪、配置策略
- 本地模型：`model/asset_allocation`
- 正式入口：`model/asset_allocation/build_snapshot.py`
- 模型引擎：`model/asset_allocation/asset_allocation_engine.py`
- 正式快照：`board/quant_strategy_agent/data/asset_allocation_snapshot.json`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 模块元数据：`model/asset_allocation/MODULE.json`

旧的“主页”和“回测检验”不是独立导航项；其图表和结论分别归入“周期跟踪”或“配置策略”的页内控件。精确映射见 `framework/integration/ui_module_mapping.json`。