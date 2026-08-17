---
name: technical-analysis
description: "用于运行、审计和维护技术分析双模型；当任务涉及 K 线学习、个股买卖点判断、同类股票模式学习、技术因子择时、横截面轮动、LLM 形态记忆或技术分析网页展示时使用。"
---
# 技术分析

## 对话入口

```powershell
python ai-models/technical-analysis/scripts/query.py status
python ai-models/technical-analysis/scripts/query.py patterns 关键词=breakout 数量=20
```

`status` 返回技术分析双模型的治理状态：模型一是纯技术信号栈，模型二是 LLM 记忆多周期模型。`patterns` 检索正式形态知识库。当前若返回观察或研究诊断状态，必须明确说明没有可直接发布交易的策略。

远程单股任务先查证券和交易日，再提交任务：

```powershell
python -m agent_runtime remote GET "/api/kline/stocks?q=000001&limit=20"
python -m agent_runtime remote GET "/api/kline/dates?code=000001.SZ"
python -m agent_runtime remote POST /api/kline/jobs --json '{"code":"000001.SZ","as_of":"latest","analysis_depth":"fast"}'
```

任务编号返回后，通过 `/api/kline/jobs/<任务编号>` 查询状态和结果。凭据只从环境变量读取。

## 当前模型边界

- 模型一：`technical-signal-stack/1.0-broker-style`。只使用本地 OHLCV 与可时点化的技术因子，先构建信号族，再训练期学习权重，输出个股择时和横截面轮动。
- 模型二：`kline-multiscale-expert/1.6`。使用 K 线形态专家、状态记忆、监督排序和多空诊断，学习历史 K 线模式与同类股票的相似技术结构。
- 封存测试只报告，不参与选模、调参或文案晋级。若封存测试失败，页面和回答都只能标记为研究诊断。

## 工作流

1. 阅读 `references/dual-model-sop.md`、`references/module-map.md`、`source/README.md` 和 K 线记忆组件说明。
2. 核对证券代码、复权口径、交易日、OHLCV、成交额、流动性过滤和 ST/退市过滤。
3. 模型一先生成技术信号族：趋势动量、突破确认、回撤反转、量价确认、波动质量、防守择时。
4. 只用训练集计算信号族 IC 权重和单股买卖阈值；验证集只负责候选筛选；封存测试只做一次报告。
5. 模型二读取形态专家和状态记忆，做股票大类/形态相似性学习，输出历史判断、当前判断、失败条件和净值回测。
6. 网页端读取冻结快照，不在 HTTP 请求中重新训练模型。
7. 任何新增因子、阈值或 LLM 记忆规则必须在进入封存测试前声明并留痕。

## 输出要求

- 个股：历史关键买卖点、当前买卖点倾向、触发逻辑、失效条件、净值曲线、最大回撤和换手成本。
- 组合：个股间技术评分、横截面轮动、训练/验证/测试分段绩效、候选失败原因和发布闸门。
- 说明：用中文解释流程，不承诺收益，不把训练集或测试集拟合结果说成未来确定性。

## 验证

```powershell
python -m pytest framework/backtest/test_technical_signal_model.py framework/backtest/test_kline_multiscale_expert.py framework/backtest/test_kline_supervised_ranker.py -q
python -m pytest board/quant_strategy_agent_vnext/qa/test_kline_multiscale_evidence.py -q
python -m py_compile ai-models/technical-analysis/runtime/agent_runtime/core.py
```
