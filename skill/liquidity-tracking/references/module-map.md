# 资金面跟踪模块地图

- 一级标题：资金面跟踪
- 二级页面：散户、公募、私募、外资、ETF、一级市场、融资资金
- 本地模型：`model/liquidity_tracking`
- 正式入口：`model/liquidity_tracking/build_snapshot.py`
- Excel 读取：`model/liquidity_tracking/xlsx_reader.py`
- 正式快照：`board/quant_strategy_agent/data/liquidity_snapshot.json`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 模块元数据：`model/liquidity_tracking/MODULE.json`

旧“主页”汇总能力归入“散户”的概览页内区段；其他旧页面均一一映射到新的七个主体，详见 `framework/integration/ui_module_mapping.json`。