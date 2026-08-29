# 研究主页模块地图

- 公开左侧入口：无
- 定位：跨模型汇总兼容入口；公网根地址默认进入“数据看板 / 市场监控 / 宏观”
- 本地模型：`ai-models/research-home/source`
- 统一服务入口：`board/quant_strategy_agent/main.py`
- 页面模板：`board/quant_strategy_agent/templates/index_rotation_factor_lab.html`
- 前端路由与渲染：`board/quant_strategy_agent/static/js/app.js`
- 样式契约：`board/quant_strategy_agent/static/css/app.css`
- 输入快照：`board/quant_strategy_agent/data/`
- 模块元数据：`ai-models/research-home/source/MODULE.json`

跨模型汇总按以下顺序消费模型结果：

1. `model/data_dashboard`
2. `model/asset_allocation`
3. `model/liquidity_tracking`
4. `model/industry_rotation`
5. `model/factor_laboratory` 与 `model/llm_factor_mining`
6. `model/kline_memory_learning`
7. `model/portfolio_optimization`

页面映射的机器可读真值为 `framework/integration/ui_module_mapping.json`。
