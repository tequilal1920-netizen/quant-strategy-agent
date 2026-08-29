# 技术分析双模型 r38 发布说明

## 版本

- vNext 页面版本：`2026.08.17-technical-dual-model-vnext-r38.0`
- 模型治理版本：`2026.08.17-technical-dual-model-governed-r38.0`
- 模型一：`technical-signal-stack/1.0-broker-style`
- 模型二：`kline-multiscale-expert/1.6-research-deployment-split`

## 本次变更

1. 新增纯技术信号栈：趋势动量、突破确认、回撤反转、量价确认、波动质量、防守择时六类信号。
2. 技术分析回测新增两类输出：个股择时阈值与横截面打分轮动。
3. 多周期 K 线模型旁路新增 `pure_technical_model` 冻结快照。
4. 技术分析页面诊断图表同时展示模型一和模型二的训练、验证、封存测试结果。
5. 模型治理页面新增纯技术模型发布闸门，并明确封存测试未通过时不能发布交易策略。
6. AI 模型包 `status` 查询新增 `model_1_pure_technical` 与 `model_2_llm_memory`。
7. 技术分析包文档统一为中文，并新增 `references/dual-model-sop.md`。

## 当前结论

两条链路训练与验证阶段存在有效研究候选，但封存测试夏普为负，当前仅允许作为研究诊断展示，不允许标记为可部署交易策略。

## 本地验证

```powershell
python -m pytest framework/backtest/test_technical_signal_model.py framework/backtest/test_kline_multiscale_expert.py framework/backtest/test_kline_supervised_ranker.py board/quant_strategy_agent_vnext/qa/test_kline_multiscale_evidence.py -q
```

结果：`11 passed`。
