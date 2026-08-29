# 因子实验室

正式运行入口为 `worker.py`。其余文件是生产职责组件，不是历史版本：

- `core.py`：时点数据读取、基础模型与公共工具。
- `adaptive_icir.py`：对称正交化、成熟标签滚动 ICIR 与经验贝叶斯收缩。
- `validated_ensemble.py`：仅使用训练/验证集选择的稳健集成。
- `stable_development.py`：开发折稳定性目标。
- `effective_dsr.py`：有效权重与 DSR 调整。
- `worker.py`：稳定 LSTM/RL 选择和命令行入口。

状态库存放在 `database/factor_lab_state.sqlite3`，任务产物存放在 `output/factor_laboratory/`。统一看板通过 `board/quant_strategy_agent/factor_lab_backend.py` 调用该入口。

策略选型只使用训练期和验证期。输出中的 `selection_quality` 单独检查两段 RankIC 方向、验证命中率及正向 IC 备选，测试期只作报告，不参与晋级。

v2.8 增加固定秩集成候选：每个交易日分别计算原 OLS 与自适应 ICIR 的横截面秩，再按预声明的 50%/50% 权重合成并执行行业和市值正交化。该组合不按测试期调整权重；若未进入训练/验证的一标准误选择范围，仅保留为研究证据。

```powershell
python -m py_compile model\factor_laboratory\*.py board\quant_strategy_agent\factor_lab_backend.py
```
