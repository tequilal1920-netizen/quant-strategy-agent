# 量化 Agent MCP

本目录是可上传 GitHub 的公开 MCP 接入层，用于让一个全新的 AI 通过仓库链接学习并调用本项目的正式模型框架。它只暴露模型目录、公式文档、查询入口和公开状态，不包含数据库、缓存、账号、token、cookie 或授权数据。

## 一键安装

在新电脑上执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\mcp\install.ps1
```

如果还没有 clone 仓库，可直接指定公开仓库和分支：

```powershell
powershell -ExecutionPolicy Bypass -File .\mcp\install.ps1 -RepoUrl https://github.com/tequilal1920-netizen/quant-strategy-agent.git -Branch agent/industry-style-r16-6
```

脚本会安装 `mcp/quant_agent_mcp`，并输出 Codex/Claude Desktop 可用的 MCP 配置示例。

## MCP 工具

- `list_modules`：列出六大模型、二级页面、源码入口和查询示例。
- `learning_path`：返回新 AI 的必读顺序，包括每个模型的 `SKILL.md`、`README.md`、`PACKAGE.json` 和 `references/module-map.md`。
- `module_status`：读取单个模型包的 package 与 Skill 摘要。
- `read_model_doc`：按白名单读取模型文档，支持 `skill`、`readme`、`package`、`module-map`、`source-readme`。
- `search_model_text`：在单个模型公开源码和文档中搜索关键词，便于追溯公式、治理门槛、数据口径和调用入口。
- `query_model`：调用 `ai-models/<module>/scripts/query.py` 的只读查询入口。
- `public_health`：检查公网 `/healthz`，确认部署版本和服务状态。

## 覆盖范围

MCP 的目录学习范围覆盖：

- 数据看板：宏观、全球市场、行业、大宗商品、个股、新闻事件、专题跟踪、资金面、AI监控、川普指数、内需股。
- 资产配置：美林、普林格、周期跟踪、多模型资产配置、风险贡献、换手和回测治理。
- 行业风格：行业景气度、行业轮动、风格轮动、六维归因和月频信号。
- 因子实验室：因子看板、LLM因子挖掘、模型层、因子检验、归因、个股信号、指数增强。
- 技术分析：技术因子轮动、K线学习、个股历史仓位决策点、当前信号和形态记忆。
- 组合优化：自适应优化求解器、宽基择时、指数增强、LLM约束编译、行业/风格/风险约束和归因。

## 数据与安全边界

新电脑 clone 仓库只能学习公开模型公式、框架说明、调用接口和脱敏状态。真实数据库、授权接口、额度账号、Office SOP 和生产缓存仍在本机或部署机私有目录中，通过环境变量或本地连接接入。任何 MCP 工具都不得输出或写入私密凭据。
