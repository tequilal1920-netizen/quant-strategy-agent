# 数据看板模块地图

- 一级标题：数据看板
- 二级页面：宏观、全球市场、行业、大宗商品、个股、新闻事件、AI监控
- 本地模型：`model/data_dashboard`
- 数据看板源码：`board/public_dashboard`
- 统一服务入口：`board/quant_strategy_agent/main.py`
- 统一前端：`board/quant_strategy_agent/static/js/app.js`
- 统一数据快照：`board/quant_strategy_agent/data/`
- AI 监控公网入口：`https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/`
- 模块元数据：`model/data_dashboard/MODULE.json`

旧的“一级行业”能力合并至“行业”；所有旧图表的精确目标和页内区段见 `framework/integration/ui_module_mapping.json`。