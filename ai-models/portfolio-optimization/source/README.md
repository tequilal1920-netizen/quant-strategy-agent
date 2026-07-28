# 组合优化

## 当前实现

- 价格历史：本地 `fund_daily`，上市 ETF，五类风险袖套。
- 候选协议：192 个预声明候选；训练期筛选 24 个，验证期固定最终方案，测试期只报告。
- 收益观点：收缩动量、稳健 Black-Litterman、风险调整趋势。
- 风险估计：Ledoit-Wolf 与 EWMA，均执行 PSD 修复。
- 求解：CVXPY DPP 复用计算图；Clarabel 优先，OSQP/SCS 顺序降级，SciPy SLSQP 提供同目标、同硬约束的独立后备路径；每次求解都要通过约束残差复核，禁止把等权可行种子伪装成正常优化结果。
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

## 收益损失归因

- `backtest.return_loss_attribution` 将测试期相对收益拆分为资产组主动配置贡献、交易成本和实现残差，并同时报告上涨捕获率与下跌捕获率。
- 资产组贡献按上期策略权重与基准权重之差乘以下期资产收益计算，避免将执行成本误判为信号失效。
- 分年相对收益用于定位持续性失效窗口。任何挑战模型必须重新通过训练期筛选和验证期稳健门禁，测试期改善不能用于晋级。
- 约束求解器、候选空间和前端数据结构保持不变；归因字段为向后兼容的新增审计输出。
