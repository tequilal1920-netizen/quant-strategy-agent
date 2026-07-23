# 研究主页模型

本目录是网页一级标题“主页”的聚合模型边界。它不复制七个下游模型的算法，而是按固定顺序读取各模型正式快照，生成研报式综合点评和日度、周度、月度推荐组合。

## 输入

- `board/quant_strategy_agent/data/` 中的正式快照
- 各组件 `MODULE.json`、截止日、生成时间和质量状态
- 用户选择的观察周期、基准与风险约束

## 输出

- 跨模型综合点评与风险提示
- 日度、周度、月度组合权重
- 可追溯到具体快照和模型组件的来源信息

正式页面入口位于 `board/quant_strategy_agent/main.py`，渲染逻辑位于 `board/quant_strategy_agent/static/js/app.js`，Agent 工作流位于 `skill/research-home/`。