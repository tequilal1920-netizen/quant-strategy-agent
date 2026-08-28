# 工作区中文结构同步说明

外层 `G:\中信建投` 已整理为中文模块目录，活动 Git 仓库仍为本目录所在的 `agent/`。为了让 GitHub 也能保留这次整理的公开说明，本文件记录外层结构和正式代码映射。

| 外层目录 | Git 仓库正式入口 |
| --- | --- |
| `数据看板` | `board/quant_strategy_agent`、`ai-models/data-dashboard`、`model/data_dashboard`、`model/liquidity_tracking` |
| `资产配置` | `ai-models/asset-allocation`、`model/asset_allocation` |
| `行业风格` | `ai-models/industry-rotation`、`model/industry_rotation` |
| `因子实验室` | `ai-models/factor-laboratory`、`model/factor_laboratory`、`model/llm_factor_mining`、`model/index_enhancement` |
| `技术分析` | `ai-models/technical-analysis`、`model/technical_analysis`、`model/kline_memory_learning` |
| `组合优化` | `ai-models/portfolio-optimization`、`model/portfolio_optimization`、`model/index_enhancement` |
| `数据库` | `database`、`framework/data_pipeline` |
| `公网` | `board/quant_strategy_agent`、`environment/deployment`、`environment/status` |
| `skill` | `skill`、`ai-models/*/SKILL.md` |
| `mcp` | 外层 `mcp/` 保存本地 MCP 骨架；公开仓库只记录无凭据说明。 |

外层 `reference/SOP` 中的 Word/Excel 文档不提交公开仓库。大型数据库、缓存、私密环境变量和授权数据也不提交公开仓库。
