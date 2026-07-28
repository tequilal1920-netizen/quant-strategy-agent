# 量化策略 Agent


## 新 Agent 直接使用

新 Agent 读取仓库根目录 `AGENTS.md`，再把对应的 `ai-models/<模型>/` 文件夹作为完整模型入口。每个文件夹同时包含 Skill、查询脚本、独立查询运行层、模型源码、必要内部组件和机器可读依赖清单；旧 `skill/` 目录继续保留兼容。

```powershell
git clone --branch agent/industry-style-r16-6 https://github.com/tequilal1920-netizen/quant-strategy-agent.git
Set-Location quant-strategy-agent
python -m agent_runtime catalog
```

远程机器把已有数据目录作为外部只读依赖接入，不把数据库、缓存或授权数据提交到 GitHub：

```powershell
$env:QUANT_AGENT_SNAPSHOT_ROOT = "<R25.4部署目录>\board\quant_strategy_agent_vnext\data"
$env:RESEARCH_WAREHOUSE_DB = "<研究数据库路径>"
$env:FACTOR_STATE_DB = "<因子状态库路径>"
$env:QUANT_AGENT_OUTPUT_ROOT = "<模型输出目录>"
python -m agent_runtime doctor
```

典型问答对应的真实命令：

```powershell
python ai-models/asset-allocation/scripts/query.py current 画像=平衡
python ai-models/industry-rotation/scripts/query.py ranking 频率=高频 数量=10
python ai-models/industry-rotation/scripts/query.py drivers 行业=电子 数量=8
python ai-models/liquidity-tracking/scripts/query.py page 页面=外资
python ai-models/factor-laboratory/scripts/query.py champion
python ai-models/technical-analysis/scripts/query.py status
python ai-models/portfolio-optimization/scripts/query.py current 最小权重=0.001
python ai-models/research-home/scripts/query.py overview
```

每次输出都包含 `数据截止`、`生成时间`、`数据来源` 和 `结果`。训练与验证负责选模，测试仅报告；研究候选、观察状态和生产可交易状态保持区分。

## 八个统一 AI 模型文件夹

| 一级标题 | 统一文件夹 | 一句话应用 |
| --- | --- | --- |
| 主页 | [`research-home`](ai-models/research-home/) | 汇总七个研究模块的时点结论、治理状态、风险提示和当前组合。 |
| 数据看板 | [`data-dashboard`](ai-models/data-dashboard/) | 查询宏观、全球市场、行业、商品、个股和事件快照及数据质量。 |
| 资产配置 | [`asset-allocation`](ai-models/asset-allocation/) | 识别多类宏观周期并输出分画像资产权重和风险贡献。 |
| 资金面跟踪 | [`liquidity-tracking`](ai-models/liquidity-tracking/) | 查询七类资金主体的最新值、方向、来源和质量状态。 |
| 行业景气度 | [`industry-rotation`](ai-models/industry-rotation/) | 查询行业景气排名、高频驱动、月周轮动和季度风格箱。 |
| 因子实验室 | [`factor-laboratory`](ai-models/factor-laboratory/) | 查询因子冠军、指数增强、SmartBeta、三段绩效和治理门禁。 |
| 技术分析 | [`technical-analysis`](ai-models/technical-analysis/) | 查询K线治理和形态知识，并转接远程单股学习任务。 |
| 组合优化 | [`portfolio-optimization`](ai-models/portfolio-optimization/) | 查询求解器、权重、风险贡献、约束余量、压力和回测门禁。 |

## 本机接口与远程模型

只读运行层不依赖第三方包：

```powershell
python -m agent_runtime serve --host 127.0.0.1 --port 8091
```

接口：

- `GET /health`
- `GET /v1/catalog`
- `POST /v1/query`

查询示例：

```json
{
  "skill": "industry-rotation",
  "operation": "drivers",
  "params": {"行业": "电子", "数量": 8}
}
```

需要运行K线或因子任务时，使用已部署统一服务。账号和口令只从环境变量读取：

```powershell
$env:QUANT_AGENT_BASE_URL = "http://127.0.0.1:8076/quant-agent-vnext"
$env:QUANT_AGENT_USER = "<运行时账号>"
$env:QUANT_AGENT_PASSWORD = "<运行时口令>"
python -m agent_runtime remote GET /api/services
python -m agent_runtime remote GET "/api/kline/stocks?q=000001&limit=20"
python -m agent_runtime remote GET /api/factor/status
```

一键远程克隆、接入外部数据并验收：

```powershell
powershell -ExecutionPolicy Bypass -File environment/deployment/deploy_agent_runtime_remote.ps1 `
  -SnapshotRoot "<R25.4部署目录>\board\quant_strategy_agent_vnext\data" `
  -ResearchWarehouseDb "<研究数据库路径>" `
  -FactorStateDb "<因子状态库路径>" `
  -OutputRoot "<模型输出目录>" `
  -Serve `
  -Persistent
```

远程主机无法直接连接 GitHub 时，先下载该分支的官方 ZIP 并解压到固定目录，再复用同一脚本：

```powershell
powershell -ExecutionPolicy Bypass -File environment/deployment/deploy_agent_runtime_remote.ps1 `
  -InstallRoot "<GitHub归档解压目录>" `
  -SnapshotRoot "<R25.4部署目录>\board\quant_strategy_agent_vnext\data" `
  -UseExisting -SourceCommit "<GitHub提交SHA>" -Serve -Persistent
```

本仓库保存当前生产源码、可复现配置、模型说明和标准化 Skill。数据库、运行输出、缓存、私密凭据、正式研究文档及历史备份不进入公开仓库。

## 目录结构

```text
ai-models/    八个可直接交给新 AI 的统一模型文件夹
board/        统一数据看板、服务端与前端资源
copy/         重组前单一备份（本地保留，Git 忽略）
database/     本地数据库与说明（数据库文件不提交）
environment/ 配置、部署、文档、依赖与项目状态
framework/   集成映射、数据、回测、质量门与审计框架
model/       模型源码及 MODULE.json
output/      模型运行结果与验证证据（Git 忽略）
skill/       八个一级板块对应的本地 Codex Skill
```

正式 SOP、学习材料、参考研报和基本信息表保留在 `G:\中信建投` 根目录；中间渲染文件和临时生成脚本不保留。`copy/previous_version_20260721` 是唯一重组前源码备份，不包含数据库或参考文档。

## 最新信息架构

- 主页
- 数据看板：宏观、全球市场、行业、大宗商品、个股、新闻事件、AI监控
- 资产配置：周期跟踪、配置策略
- 资金面跟踪：散户、公募、私募、外资、ETF、一级市场、融资资金
- 行业景气度：行业景气度、风格轮动、配置策略
- 因子实验室：因子看板、因子挖掘、配置策略
- 技术分析：K线学习、配置策略
- 组合优化：优化求解、配置策略

八个一级板块与统一 AI 文件夹：

| 一级板块 | 统一 AI 文件夹 | 旧兼容目录 |
| --- | --- | --- |
| 主页 | `ai-models/research-home` | `model/research_home`、`skill/research-home` |
| 数据看板 | `ai-models/data-dashboard` | `model/data_dashboard`、`skill/data-dashboard` |
| 资产配置 | `ai-models/asset-allocation` | `model/asset_allocation`、`skill/asset-allocation` |
| 资金面跟踪 | `ai-models/liquidity-tracking` | `model/liquidity_tracking`、`skill/liquidity-tracking` |
| 行业景气度 | `ai-models/industry-rotation` | `model/industry_rotation`、`skill/industry-rotation` |
| 因子实验室 | `ai-models/factor-laboratory` | `model/factor_laboratory`、`skill/factor-laboratory` |
| 技术分析 | `ai-models/technical-analysis` | `model/technical_analysis`、`skill/technical-analysis` |
| 组合优化 | `ai-models/portfolio-optimization` | `model/portfolio_optimization`、`skill/portfolio-optimization` |

`ai-models/factor-laboratory/components/` 已包含指数增强与 LLM 因子挖掘源码；`ai-models/technical-analysis/components/` 已包含 K 线学习源码。每个 `PACKAGE.json` 记录查询动作、外部数据库、共享运行层、部署端点、文件数量和永久 GitHub 文件夹地址。

## 统一看板

正式入口为 `board/quant_strategy_agent/main.py`。页面切换只读取已生成快照，不在跳转时重复运行模型；传输层支持 gzip、ETag、条件 304 和分层缓存。K 线、因子历史与任务详情采用服务端缓存并在显式刷新或运行状态查询时正确旁路。

必要环境变量：

- `QUANT_AGENT_USER`
- `QUANT_AGENT_PASSWORD`
- `QUANT_AGENT_SECRET`

可选上游地址：`BOARD_BASE_URL`、`KLINE_BASE_URL`、`FACTOR_BASE_URL`。生产凭据只保存在 `private/quant_agent.env`，不得写入源码或公开文档。

```powershell
python -m pip install -r board\quant_strategy_agent\requirements.txt
$env:QUANT_AGENT_USER = "local-user"
$env:QUANT_AGENT_PASSWORD = "local-password"
$env:QUANT_AGENT_SECRET = "replace-with-random-secret"
Set-Location board\quant_strategy_agent
python -m waitress --host=127.0.0.1 --port=8071 main:app
```

生产也可执行 `board/quant_strategy_agent/deploy/run_service.ps1`。

## UI 与状态语义

- 绿色：数据更新无误且服务正常。
- 蓝色：任务、刷新或页面加载正在运行。
- 红色：数据加载、质量检查或服务存在问题。
- 中文字体统一为楷体族，英文字体统一为 Arial；HTML 可见文字不小于 14px，图表文字不小于 11px。
- 一级、二级标题、结论框、卡片、图表和顶部固定控件由统一样式约束。

## 验证

```powershell
python board\quant_strategy_agent\qa\test_canonical_app.py
python model\asset_allocation\test_asset_allocation_engine.py
python model\portfolio_optimization\test_portfolio_optimization_engine.py
python model\industry_rotation\test_contract.py
node --check board\quant_strategy_agent\static\js\app.js
```

八个 Skill 使用官方 `quick_validate.py` 校验。真实公网浏览器验收覆盖 27 个二级页面和 51 个页内功能，检查真实点击、激活状态、控制台错误、页面错误、最小字体与横向溢出。

## 公网部署

- 统一看板：https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/
- AI 监控：https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/
- 原公网版本：`2026.07.27-scoped-controls-ai-cache-r21.2`
- vNext 入口：https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/
- vNext 版本：`2026.07.27-five-panel-dense-vnext-r25.4`
- K 线模型：`9.0-cohort-wyckoff-evolution`
- 部署与回滚脚本：`environment/deployment/`
- 公开仓库：https://github.com/tequilal1920-netizen/quant-strategy-agent

生产发布使用独立版本目录、SHA-256 校验、临时端口预检、计划任务原子切换和自动回滚。大型研究数据库始终原地引用，不进入发布包或 GitHub。
