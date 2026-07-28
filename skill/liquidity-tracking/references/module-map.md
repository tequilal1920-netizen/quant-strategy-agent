# 资金面跟踪模块地图

- 一级标题：资金面跟踪
- 现有页面 ID：`home`、`retail`、`public`、`etf`、`margin`、`primary`、`private`、`foreign`
- 页面图数：主页 8；散户 4；公募 4；ETF 4；融资 5；一级市场 4；私募 4；外资 4；共 37
- 本地模型：`model/liquidity_tracking`
- 数据契约与刷新：`model/liquidity_tracking/data_sources.py`
- ETF 与行业刷新：`model/liquidity_tracking/specialized_refresh.py`
- 官方来源刷新：`model/liquidity_tracking/official_refresh.py`
- 国内图表构建：`model/liquidity_tracking/snapshot_domestic.py`
- 正式快照入口：`model/liquidity_tracking/build_snapshot.py`
- 正式数据库缓存：`database/liquidity_tracking.sqlite3`- 正式 Word：`output/资金面跟踪.docx`（运行输出，不提交公开仓库）
- 当前来源状态：39/49 已落库；34 个 Wind SQL、3 个中证数据、1 个华润信托 CREFI、1 个基金业协会；剩余 2 个 Wind EDB 与 8 个 EPFR 精确字段阻断发布
- 正式快照：`board/quant_strategy_agent/data/liquidity_snapshot.json`
- 页面渲染：`board/quant_strategy_agent/static/js/app.js`
- 模块元数据：`model/liquidity_tracking/MODULE.json`

`xlsx_reader.py` 是旧实现遗留文件，不属于正式数值链路。不得用它读取旧 Excel 数值、补缺或发布。现有导航映射以 `framework/integration/ui_module_mapping.json` 为准，数据源维护不得修改该文件。
