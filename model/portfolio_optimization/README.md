# 组合优化

## 当前实现

- 价格历史：本地 `fund_daily`，上市 ETF，五类风险袖套。
- 候选协议：192 个预声明候选；训练期筛选 24 个，验证期固定最终方案，测试期只报告。
- 收益观点：收缩动量、稳健 Black-Litterman、风险调整趋势。
- 风险估计：Ledoit-Wolf 与 EWMA，均执行 PSD 修复。
- 求解：CVXPY DPP 复用计算图；Clarabel 优先，OSQP/SCS 对拍；硬约束残差必须不高于 `1e-5`。
- 审计：等权、逆波动、HRP 基线，交易成本、压力情景、CSCV-PBO、Deflated Sharpe 与研究转实盘门禁。
- LLM 与深度模型只能生成观点、约束草案或诊断，不能绕过求解器直接产生生产权重。

## 运行

```powershell
python portfolio_optimization_engine.py `
  --database ..\..\database\research_warehouse.db `
  --subject-database $env:SUBJECT_DATABASE `
  --rotation-tracking ..\..\board\quant_strategy_agent\data\rotation_tracking.json `
  --output ..\..\board\quant_strategy_agent\data\portfolio_optimization_snapshot.json
```

## 验证

```powershell
C:\ProgramData\anaconda3\python.exe test_portfolio_optimization_engine.py
```

网页只读取通过质量门禁的冻结快照，不在 HTTP 请求中访问数据库或付费 API。
