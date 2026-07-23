# 因子实验室

正式运行入口为 `worker.py`。其余文件是生产职责组件，不是历史版本：

- `core.py`：时点数据读取、基础模型与公共工具。
- `validated_ensemble.py`：仅使用训练/验证集选择的稳健集成。
- `stable_development.py`：开发折稳定性目标。
- `effective_dsr.py`：有效权重与 DSR 调整。
- `worker.py`：稳定 LSTM/RL 选择和命令行入口。

状态库存放在 `database/factor_lab_state.sqlite3`，任务产物存放在 `output/factor_laboratory/`。统一看板通过 `board/quant_strategy_agent/factor_lab_backend.py` 调用该入口。

```powershell
python -m py_compile model\factor_laboratory\*.py board\quant_strategy_agent\factor_lab_backend.py
```
