# 数据仓库

本目录保存本机运行需要的 SQLite/DB 文件。数据库体积大且可能包含授权数据，不进入公开 GitHub；公开仓库只保留 schema、增量脚本和结构说明。

## 当前正式数据库

- `research_warehouse.db`
- `optimizer_state.db`
- `liquidity_tracking.sqlite3`
- `factor_lab_state.sqlite3`
- `factor_mining_users.sqlite3`
- `data_dictionary.xlsx`

统一数据窗口：2012-01-01 至 2026-06-30。训练集、验证集、测试集和全样本切分由 `framework/backtest` 与 `framework/integration` 中的脚本统一读取。

## 文件用途

- `research_warehouse.db`：研究仓库，承载行情、因子、回测、正式模型证据等本地核心数据。
- `optimizer_state.db`：组合优化器状态、约束、候选和认证结果。
- `liquidity_tracking.sqlite3`：资金面跟踪状态库。
- `factor_lab_state.sqlite3`：因子实验室看板、检验和模型状态。
- `factor_mining_users.sqlite3`：LLM 因子挖掘任务和用户状态。
- `data_dictionary.xlsx`：结构级数据字典，只含库文件、表字段和更新脚本索引，不含业务数据行。

## 更新入口

增量更新脚本统一放在 `framework/data_pipeline`，外层中文入口为 `G:\中信建投\数据库\数据更新脚本`。数据接口封装位于 `framework/data_pipeline/connectors`，包括 Wind SQL、Tushare、BaoStock、RQData 等本地安全封装。任何凭据都必须来自环境变量或私有配置，不能写入源码、README、日志或 GitHub。
