# 02 中证2000风格集中增强

- 正式模型：`csi2000_style_concentrated_agent_v6`
- 目标标的：中证2000增强
- 主逻辑：小盘风格、行业轮动和强约束集中持仓组合。
- 运行入口：`framework/backtest/run_v2_models.py`
- 审计位置：数据库表 `v3_backtest_audit`、`v4_strict_gate`
