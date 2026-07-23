# 03 中证2000风格风险控制增强

- 正式模型：`csi2000_style_risk_control_agent_v6`
- 目标标的：中证2000增强
- 主逻辑：风格轮动叠加风险预算、行业上限和回撤控制。
- 运行入口：`framework/backtest/run_v2_models.py`
- 审计位置：数据库表 `v3_backtest_audit`、`v4_strict_gate`
