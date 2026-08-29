# 因子实验室框架 Skill

## 触发

当用户询问因子数据库、因子看板、因子检验、因子归因、LLM因子挖掘、个股打分回测、指数增强或 SmartBeta 时使用。

## 模型入口

- AI 入口：`agent/ai-models/factor-laboratory`
- 因子看板：`agent/model/factor_laboratory`
- LLM 因子挖掘：`agent/model/llm_factor_mining`
- 指数增强：`agent/model/index_enhancement`
- 查询脚本：`agent/ai-models/factor-laboratory/scripts/query.py`

## 二级页面

因子实验室左侧二级标题必须为以下三个，顺序不变：

1. 因子看板
2. LLM因子挖掘
3. 模型层

旧名称只能作为内部历史文档出现，不能作为左侧二级标题回退。

## 核心计算

1. 因子看板读取正式因子目录、有效性检验、成熟标签、状态库和 champion manifest。
2. LLM因子挖掘只生成候选表达式、解释和研究假设，表达式必须进入受限 DSL，不执行任意代码。
3. 模型层覆盖 SmartBeta、风险模型、指数增强和多因子组合，最终权重必须由正式模型或求解器输出。
4. 因子验收包含可得性、去极值、标准化、中性化、覆盖率、RankIC、ICIR、分层收益、换手、回撤、PBO/DSR/多重检验。
5. 个股归因需要拆成因子贡献、行业暴露、风格暴露、资金面、技术状态和残差，不能只给定性评价。

## 工作流程

1. 先读取因子目录、champion manifest 和状态库。
2. 因子必须经过可得性、去极值、标准化、中性化、成熟标签、RankIC、ICIR、分层收益、换手和回撤检验。
3. LLM 只生成候选表达式、解释和研究假设，不能跳过回测治理。
4. 个股归因需要说明因子贡献、行业/风格暴露、资金和技术状态。
5. 每次资产配置或指数增强被修改后，都要复查这三个二级标题和指数增强图表是否被误回滚。

## 查询与验收

```powershell
python ai-models/factor-laboratory/scripts/query.py champion
python ai-models/factor-laboratory/scripts/query.py index 指数=CSI800_ENH 数量=10
python ai-models/factor-laboratory/scripts/query.py models
```

网页验收检查左侧标题、因子任务状态、历史任务、因子表达式、指数增强、个股信号、图表和表格样式。
