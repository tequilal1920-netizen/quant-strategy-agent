---
name: factor-laboratory
description: "用于运行、审计和维护因子实验室；当任务涉及因子看板、因子挖掘、LLM因子表达式、严格检验、综合打分、指数增强或配置策略时使用。"
---

# 因子实验室

## 目标

统一管理隔离因子实验、LLM 因子挖掘和指数增强配置，让假设、表达式、检验、评分、变异关系和最终策略可复现。

## 输入

- 研究截止日、股票池、基准、因子表达式或自然语言假设。
- 训练/验证/测试区间、交易成本、行业中性和风险约束。
- 可选的因子族、挖掘预算、评分门槛和配置策略。

## 输出

- 因子看板、有效性诊断、相关性和稳定性证据。
- 可执行 DSL 表达式、检验结果、综合评分和历史记忆。
- 指数增强或多因子配置策略的目标权重、跟踪风险和回测证据。
- 所有异步任务的状态、日志位置和产物路径。

## 工作流

1. 阅读 `references/module-map.md`、三个组件模型的 README/AGENT 和当前状态文件。
2. 把自然语言假设转换为受限、可执行的因子 DSL；禁止执行任意代码。
3. 固定股票池、时间点、训练/验证/测试隔离和 embargo，再启动实验。
4. 通过独立 worker 运行，限制并发；产物写入 `output/`，状态写入正式状态库。
5. 检查覆盖率、IC、分层收益、换手、成本、稳定性、泄漏和多重试验偏差。
6. 只有通过门槛的因子才能进入配置策略；测试集仅报告一次。
7. 遍历因子看板、因子挖掘和配置策略及其全部页内控件，确认历史记忆和旧指数增强图表仍可访问。

## 约束

- 密钥只从环境变量读取，公共请求不得改写正式因子注册表。
- 不降低 embargo、断言、评分门槛或测试隔离以让结果通过。
- 状态颜色固定为绿色正常、蓝色运行、红色异常。
- 中文楷体、英文和数字 Arial；遵守统一看板布局。

## 验证

```powershell
python -m py_compile model/factor_laboratory/worker.py model/llm_factor_mining/factor_miner.py board/quant_strategy_agent/factor_lab_backend.py board/services/factor_mining/app.py
python board/quant_strategy_agent/qa/test_canonical_app.py
```