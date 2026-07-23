# 量化策略 Agent

本仓库保存当前生产源码、可复现配置、模型说明和标准化 Skill。数据库、运行输出、缓存、私密凭据、正式研究文档及历史备份不进入公开仓库。

## 目录结构

```text
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

八个一级板块与最终模型/Skill：

| 一级板块 | 模型目录 | Skill 目录 |
| --- | --- | --- |
| 主页 | `model/research_home` | `skill/research-home` |
| 数据看板 | `model/data_dashboard` | `skill/data-dashboard` |
| 资产配置 | `model/asset_allocation` | `skill/asset-allocation` |
| 资金面跟踪 | `model/liquidity_tracking` | `skill/liquidity-tracking` |
| 行业景气度 | `model/industry_rotation` | `skill/industry-rotation` |
| 因子实验室 | `model/factor_laboratory` | `skill/factor-laboratory` |
| 技术分析 | `model/technical_analysis` | `skill/technical-analysis` |
| 组合优化 | `model/portfolio_optimization` | `skill/portfolio-optimization` |

`model/index_enhancement`、`model/llm_factor_mining` 和 `model/kline_memory_learning` 是上述一级模型的内部组件，不再作为左侧一级板块。每个 `MODULE.json` 记录本地路径、Skill 路径、角色和永久 GitHub 地址。

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
- 当前生产版本：`2026.07.23-research-workspace-r16.3`
- K 线模型：`9.0-cohort-wyckoff-evolution`
- 部署与回滚脚本：`environment/deployment/`
- 公开仓库：https://github.com/tequilal1920-netizen/quant-strategy-agent

生产发布使用独立版本目录、SHA-256 校验、临时端口预检、计划任务原子切换和自动回滚。大型研究数据库始终原地引用，不进入发布包或 GitHub。
