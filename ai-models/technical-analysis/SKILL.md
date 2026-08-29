---
name: technical-analysis
description: "用于运行、审计和维护技术分析三轨模型；当任务涉及 K 线学习、个股买卖点判断、同类股票模式学习、技术因子择时、横截面轮动、全历史低频拟合、LLM 形态记忆或技术分析网页展示时使用。"
---
# 技术分析

## 对话入口

```powershell
python ai-models/technical-analysis/scripts/query.py status
python ai-models/technical-analysis/scripts/query.py current 数量=20
python ai-models/technical-analysis/scripts/query.py patterns 关键词=breakout 数量=20
```

`status` 返回技术分析三轨模型治理状态；`current` 返回全历史低频技术模型的最新多股持仓候选；`patterns` 检索正式形态知识库。若返回研究或观察状态，必须明确说明不是未来收益承诺。

远程单股任务先查证券和交易日，再提交任务：

```powershell
python -m agent_runtime remote GET "/api/kline/stocks?q=000001&limit=20"
python -m agent_runtime remote GET "/api/kline/dates?code=000001.SZ"
python -m agent_runtime remote POST /api/kline/jobs --json '{"code":"000001.SZ","as_of":"latest","analysis_depth":"fast"}'
```

任务编号返回后，通过 `/api/kline/jobs/<任务编号>` 查询状态和结果。凭据只从环境变量读取。

## 当前模型边界

- 模型一：`technical-signal-stack/1.0-broker-style`。只使用本地 OHLCV 与可时点化技术因子，训练期学习权重，验证期筛选，封存测试只报告。
- 模型二：`kline-multiscale-expert/1.6`。使用 K 线形态专家、状态记忆、监督排序和多空诊断，学习历史 K 线模式与同类股票技术结构。
- 模型三：`technical-signal-stack/1.1-full-history-low-frequency`。按用户要求不再做训练/测试划分，全部成熟历史样本用于拟合和候选选择；四周或八周低频刷新，带最少 120 个有效周护栏，输出当前位置。

## 工作流

1. 阅读 `references/dual-model-sop.md`、`references/module-map.md` 和 `source/README.md`。
2. 核对证券代码、复权口径、交易日、OHLCV、成交额、流动性过滤和 ST/退市过滤。
3. 生成六类技术信号族：趋势动量、突破确认、回撤反转、量价确认、波动质量、防守择时。
4. 分段治理版只用训练集学习权重；全历史研究版用全部成熟历史拟合，但必须标记为全样本回溯研究。
5. 网页端读取冻结快照，不在 HTTP 请求中重新训练模型。
6. 短样本高夏普不采纳；当前护栏要求候选至少 120 个有效周。

## 输出要求

- 个股：历史关键买卖点、当前买卖点倾向、触发逻辑、失效条件、净值、回撤和换手成本。
- 组合：个股间技术评分、横截面轮动、全历史低频候选、当前位置、候选失败原因和发布边界。
- 说明：全中文解释流程，不承诺收益，不把全历史拟合说成未来确定性。

## 验证

```powershell
python -m pytest framework/backtest/test_technical_signal_model.py framework/backtest/test_kline_multiscale_expert.py framework/backtest/test_kline_supervised_ranker.py -q
python -m pytest board/quant_strategy_agent_vnext/qa/test_kline_multiscale_evidence.py -q
python -m py_compile ai-models/technical-analysis/runtime/agent_runtime/core.py
```
