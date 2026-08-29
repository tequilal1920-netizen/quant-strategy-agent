# 技术分析框架 Skill

## 触发

当用户询问技术因子、K线学习、个股仓位历史决策点、当前技术信号、形态记忆或技术归因时使用。

## 模型入口

- AI 入口：`agent/ai-models/technical-analysis`
- 技术分析源码：`agent/model/technical_analysis`
- K线记忆源码：`agent/model/kline_memory_learning`
- 查询脚本：`agent/ai-models/technical-analysis/scripts/query.py`

## 二级页面

- 技术因子：技术因子轮动、横截面候选、行业/风格技术归因。
- K线学习：个股历史仓位决策点、当前信号、形态记忆、同类股票学习和五档仓位。

## 核心计算

1. 技术因子使用可时点化 OHLCV、趋势动量、突破确认、回撤反转、量价确认、波动质量和防守择时。
2. K线学习使用 Wyckoff 形态、相似记忆检索、批判验证、反思更新和仓位演化；网页端只读取冻结结果或明确的单股任务结果。
3. 全历史低频研究按用户要求可用全部成熟历史样本，但必须标记为全历史回溯研究，不得说成严格样本外生产。
4. 个股仓位输出必须带历史决策点、当前仓位、触发原因、失效条件、净值、回撤和换手成本。
5. 股票代码、复权口径、交易日、ST/退市过滤、成交额和样本长度是硬约束，不能用空数据生成 AI 结论。

## 工作流程

1. 先确认 OHLCV 数据截止日、股票池和可用缓存。
2. 技术因子侧看横截面因子、行业风格技术轮动和归因。
3. K线学习侧看历史决策点、当前形态、仓位建议和不确定性。
4. 如果上游 K线服务断开，先修复服务和缓存，不直接重训模型。
5. 需要单股图时优先使用正式 `run_wyckoff_memory_batch.py` 或远程 K线任务 API，不引用归档的旧 rulebook 迭代脚本。

## 查询与验收

```powershell
python ai-models/technical-analysis/scripts/query.py status
python ai-models/technical-analysis/scripts/query.py current 数量=20
python -X utf8 -m py_compile model/kline_memory_learning/run_wyckoff_memory_batch.py
```

网页验收检查技术因子、K线学习、个股搜索、任务提交、历史结果、图表非空、跳转速度和公网 K线代理。
