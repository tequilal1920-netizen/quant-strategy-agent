# 六模块研究数据看板

生产型、只读、日更的公网研究看板，覆盖：宏观、全球市场、申万一级行业、大宗商品、个股、新闻事件。

## 数据原则

- `data/indicator_catalog.seed.json` 固化 367 项字段定义；运行时发布为 `data/indicator_catalog.json`。
- `pipeline.py` 每日只调用免费公开接口；Tushare、iFind、Wind 仅声明环境变量开关，默认关闭且不会序列化任何配置值。
- 禁止随机数、零值补洞和静默换源。缺数显示 `unavailable`，源失败时仅回退到上次通过验证的快照并标记 `stale`。
- 当前 AKShare 版本中字段错位的申万实时/周频/成分接口被显式禁用；免费层只发布实测成功的 `index_hist_sw` 代表行业历史，并标记 `partial`。
- A 股行情按东财 → 新浪 → 腾讯逐级回退并逐行记录实际来源；“手/股/元/%”按接口文档归一，动态 PE 不冒充 PE-TTM，前复权价与原始价分栏。
- AKShare `macro_china_hgjck` 的海关金额原始量级按 `/1e5` 归一为“亿美元”；金额字段不沿用同比字段尺度，并以独立贸易差额端点做方向和量级交叉校验，允许统计口径与修订差异。
- 快照采用临时文件 + `os.replace` 原子发布，20 小时 TTL，最多保留 30 份历史快照。
- `modules[*].series` 是交互图表的唯一观测序列合同；每条序列保留稳定ID、目录ID、频率、单位、来源、水位、状态与原始日期值，不补零、不前向填充。

## 本地运行

```powershell
python pipeline.py update --data-dir data --catalog data\indicator_catalog.seed.json --force
python pipeline.py validate --data-dir data
python -m waitress --listen=127.0.0.1:8070 --threads=8 app:app
```

只读接口：

- `/livez`：仅检查进程和两个JSON文件可读性；数据陈旧或覆盖不足不会触发服务重启
- `/healthz`：严格数据质量检查（367行目录、模块非stale/failed、快照时效、日频模块水位和核心live覆盖）
- `/api/v1/snapshot`
- `/api/v1/catalog`
- `/api/v1/coverage`：总量与六模块的live/stale/unavailable/metadata_only覆盖率
- `/api/v1/series?ids=...&module=...&start=YYYY-MM-DD&end=YYYY-MM-DD&frequency=raw|daily|weekly|monthly|quarterly&transform=raw|difference|pct_change|mom|yoy|rebased|zscore&benchmark=...`：只读筛选、降频与变换；不访问上游
- `/api/v1/stock/<code>`

`/api/v1/series`限制查询串、序列数、日期跨度和单序列点数；未知参数、重复参数及非法ID均返回明确的400/414，不做静默截断。

## Notebook

`notebooks/update_dashboard.ipynb` 保持 7 个 Markdown + 7 个 Code 单元格、无嵌入输出和隐藏状态。生产计划任务直接调用 `pipeline.py`；notebook 用于人工复核和可重复执行。

## 远端部署

目标目录：`F:\apps\research_market_board`

公网地址：<https://desktop-i22b489.tailf9d7ac.ts.net:10007/>

独立资源：

- Waitress：`127.0.0.1:8070`
- Tailscale Funnel：HTTPS `10007`
- 服务任务：`ResearchMarketBoardService`
- 日更任务：`ResearchMarketBoardDailyUpdate`（每日 18:35）
- 新闻任务：`ResearchMarketBoardNewsUpdate`（每60分钟，仅刷新news_events并与上一有效快照合并）
- 健康检查：`ResearchMarketBoardHealth`（每5分钟；仅进程不可达时重启本服务，数据质量失败只记录日志）

日更和新闻任务均最多尝试2次；任务以SYSTEM运行、`MultipleInstances=IgnoreNew`，日志仅写项目目录且不含认证信息。月/季频指标每天检查发布状态，但未到发布日期时不会伪装为新的日频观测。

部署脚本位于 `deploy/`。现有 Subject、量化平台、443/8443/10000-10006 路由均不在本项目变更范围内。

## 验证

```powershell
python verify_release.py
python -m pytest -q tests
node --check static\js\dashboard.js
node --check static\js\plotly-loader.js
```
