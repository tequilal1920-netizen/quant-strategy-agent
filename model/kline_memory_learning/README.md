# 04 个股K线记忆学习Agent

当前正式版本：`6.5-split-isolated-confidence-gate`

主入口：`single_stock_analyzer.py`

这版不是旧的固定阈值K线打分器，而是面向单只股票全历史行情的记忆学习Agent。它使用复权一致的开高低收，按真实交易日生成5/10/20/60日标签并在切分边界净化；书本理论、市场共享OHLCV表示、状态过滤、跨频池化和GPT补丁都只是挑战者，必须经过训练期时序样本外预测、验证门控、策略级相关调整DSR与8区块CSCV-PBO审计、配对区块自助法和全局纯基线冠军保护，测试集只在最终揭盲时报告。

最小运行：

```powershell
python model\kline_memory_learning\single_stock_analyzer.py --code 000001.SZ --as-of 20260630
```

输出目录：

```text
output/kline_memory_learning/<股票代码>_<截止日期>/
```

6.5新增非重叠成熟路径现金置信学习：从已通过个股训练与验证筛选的7条多样化K线规则形成稀疏专家，并与两类现金防守专家竞争；每个持有窗口只结算一条互不重叠的独立路径，训练、验证、测试分别初始化和更新。该候选只有在训练与验证收益和夏普均为正、且正式多重检验审计通过时，才可在普通候选全部被拒绝后进入救援；测试结果仍不参与选择。
核心产物：

- `learned_kline_notes.txt`：一行一个学习后的K线指标或组合逻辑。
- `learned_rules.csv`：每条规则在训练、验证、测试、全样本上的表现。
- `learned_kline_result.json`：完整Agent运行结果。
- `trades.csv`、`equity_curve.csv`、`kline_trade_marks.svg`：回测交易、净值和买卖点图。

GPT复核通过环境变量启用，不在仓库保存密钥：

```powershell
$env:AI_ROUTER_API_KEY="..."
$env:AI_ROUTER_BASE_URL="https://ai.router.team/v1"
$env:KLINE_GPT_MODEL="gpt-5.5"
$env:KLINE_GPT_REASONING_EFFORT="xhigh"
python model\kline_memory_learning\single_stock_analyzer.py --code 000001.SZ --require-gpt
```

逐K线全历史GPT复核：

```powershell
python model\kline_memory_learning\single_stock_analyzer.py --code 000001.SZ --require-gpt --gpt-full-history
```

公网服务模式：

```powershell
python model\kline_memory_learning\single_stock_analyzer.py `
  --serve `
  --host 127.0.0.1 `
  --port 8877 `
  --db F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db `
  --service-output-root F:\apps\kline_agent_public_8877\outputs `
  --service-max-workers 1
```

公网入口由远程反向代理独立配置。服务页面必须先登录；只开放网页、股票列表、交易日列表、任务提交、任务状态、历史记录和结果文件下载，不开放任意命令执行，且强制 `write_db=False`。GPT API 只从远程服务端环境变量读取，不在网页、API响应或仓库文件中展示。
