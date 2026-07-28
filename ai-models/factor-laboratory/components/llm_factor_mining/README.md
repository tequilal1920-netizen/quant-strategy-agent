# 05 因子挖掘 Agent

本模型已经替换为方法论驱动的 AI 因子挖掘 Agent。它不是复现研报里的单个因子，而是学习 AI 因子挖掘方法，系统性生成、搜索、检验、变异和沉淀新因子。

## 正式入口

```powershell
python model\llm_factor_mining\factor_miner.py --db database\research_warehouse.db --universe ALL_A --iterations 2 --budget-per-channel 5
```

兼容入口仍为 `factor_miner.py`，并保留 `load_financial()`、`fetch_panel()`、`score_candidates()` 等函数，供 `framework/backtest/run_v2_models.py` 动态导入。

## GPT/API 接入

Agent 支持 OpenAI-compatible / AI Router 接口，默认模型为 `gpt-5.5`，推理强度为 `xhigh`。密钥只从环境变量读取，不写入源码、配置、输出或日志。

可选环境变量：

- `AI_ROUTER_API_KEY`
- `AI_ROUTER_BASE_URL`
- `FACTOR_MINING_LLM_MODEL`
- `FACTOR_MINING_REASONING_EFFORT`
- `TUSHARE_TOKEN`
- `IFIND_ACCESS_TOKEN`
- `IFIND_REFRESH_TOKEN`
- `WIND_SQL_UID`
- `WIND_SQL_PWD`
- `FACTOR_MINING_ENABLE_PAID_PROBES`

如果没有配置 API key，LLM 生成器会自动降级为本地结构化候选生成，并在输出 JSON 的 `llm_adapter` 和 `data_source_audit` 中明确标记。

## 本地数据库

正式数据库：`database/research_warehouse.db`

实际使用表：

- `stock_ohlcv_daily`
- `stock_valuation_daily`
- `stock_moneyflow_daily`
- `financial_report_visible`
- `news_event_daily`
- `kline_feature_daily`
- `sw_l1_industry_daily`
- `index_constituent_period`
- `trade_calendar`
- `v4_method_card`
- `v4_factor_agent_plan`
- `factor_test_result`
- `factor_value_daily`
- `v3_factor_candidate_registry`
- `v3_factor_validation`

## 真实流程

1. 从 `v4_method_card` 和 `v4_factor_agent_plan` 学习方法论，不把研报因子当作复现目标。
2. 构建量价、估值、可见财报、资金流、新闻事件、K线语境、行业中性化数据空间。
3. 通过 LLM 假设生成、MCTS 表达式树搜索、遗传交叉、OpenFE 自动交互、深度表示 SVD、失败记忆变异生成候选。
4. 将候选编译成可执行 DSL 或轻量表示程序。
5. 做静态审计：字段存在性、覆盖率、复杂度和标签泄漏。
6. 对候选执行去极值、标准化、市值和行业中性化。
7. 做 RankIC、ICIR、t 值、胜率、分组收益、单调性、换手、覆盖率、逐年检验。
8. 做 Top 组多头和 Top-Bottom 多空组合回测，并扣除换手成本。
9. 按 reward 评分，惩罚冗余、换手、复杂度和样本内外落差。
10. 对失败候选归因并定向变异，直到找到通过门槛的因子或达到预算。

## 输出

- `output/llm_factor_mining/factor_mining_{universe}.json`
- `output/llm_factor_mining/factor_leaderboard_{universe}.csv`
- `output/llm_factor_mining/methodology_cards_{universe}.json`

同时写入：

- `factor_test_result`
- `factor_value_daily`
- `v3_factor_candidate_registry`
- `v3_factor_validation`
