# 行业景气度模块地图

- 一级标题：行业景气度
- 二级页面：行业景气度、风格轮动、配置策略
- 本地模型：`model/industry_rotation`
- 快照入口：`model/industry_rotation/build_snapshot.py`
- 跟踪入口：`model/industry_rotation/build_tracking.py`
- 模型引擎：`model/industry_rotation/engine.py`
- 事件覆盖：`model/industry_rotation/event_overrides.py`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 模块元数据：`model/industry_rotation/MODULE.json`

原行业轮动的主页、状态、行业池、动量趋势、景气质量、事件催化、轮动信号、行业详情、配置策略和回测结果全部保留为三个新页面中的页内区段。精确映射见 `framework/integration/ui_module_mapping.json`。