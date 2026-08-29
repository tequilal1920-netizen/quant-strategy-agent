# 技术分析模块地图

## 页面与运行时

- 网页栏目：`board/quant_strategy_agent_vnext` 的“技术分析”页面。
- 冻结快照：`board/quant_strategy_agent_vnext/data/kline_multiscale_expert_challenger.json`。
- 页面证据后端：`board/quant_strategy_agent_vnext/research_evidence_backend.py`。
- K 线图表后端：`board/quant_strategy_agent_vnext/kline_multiscale_visual_backend.py`。
- 模型治理后端：`board/quant_strategy_agent_vnext/model_governance_backend.py`。
- AI 包运行时：`ai-models/technical-analysis/runtime/agent_runtime/core.py`。

## 模型一：纯技术信号栈

- 技术信号族：`framework/backtest/technical_signal_model.py`。
- 单元测试：`framework/backtest/test_technical_signal_model.py`。
- 上游行情特征：`framework/backtest/kline_multiscale_expert.py`。
- 回测接入：`model/kline_memory_learning/run_multiscale_expert_challenger.py` 中的 `pure_technical_model` 输出。

## 模型二：LLM 记忆多周期

- 多周期专家：`framework/backtest/kline_multiscale_expert.py`。
- 监督排序：`framework/backtest/kline_supervised_ranker.py`。
- 训练与冻结快照：`model/kline_memory_learning/run_multiscale_expert_challenger.py`。
- 旧单股/同类学习组件：`ai-models/technical-analysis/components/kline_memory_learning`。
- 形态知识库：`ai-models/technical-analysis/references/kline-patterns`。

## 验证入口

- 模型一单测：`python -m pytest framework/backtest/test_technical_signal_model.py -q`。
- 多周期模型单测：`python -m pytest framework/backtest/test_kline_multiscale_expert.py framework/backtest/test_kline_supervised_ranker.py -q`。
- 网页证据测试：`python -m pytest board/quant_strategy_agent_vnext/qa/test_kline_multiscale_evidence.py -q`。
- 运行时编译：`python -m py_compile ai-models/technical-analysis/runtime/agent_runtime/core.py`。
