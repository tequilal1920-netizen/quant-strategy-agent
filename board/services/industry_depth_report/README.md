# 申万一级行业深度报告网页

本网页调用 `model/data_dashboard/industry_depth_report`，输入行业和报告日期，生成严格质量门通过后的 Word 报告。

## 本地启动

```powershell
Set-Location "<project-root>"
.\board\services\industry_depth_report\start_local.ps1
```

访问：

```text
http://127.0.0.1:8892
```

## 公网安全变量

公网部署前建议配置：

```powershell
$env:INDUSTRY_REPORT_USER="admin"
$env:INDUSTRY_REPORT_PASSWORD="<private-password>"
```

AI网关只从环境变量读取：

```powershell
$env:AI_ROUTER_API_KEY="<private-key>"
$env:AI_ROUTER_BASE_URL="https://ai.router.team"
$env:AI_ROUTER_MODEL="gpt-5.5"
$env:AI_ROUTER_REASONING_EFFORT="xhigh"
```

这些变量不能写入仓库文件。

## 远程部署

本工具使用独立目录、独立本地端口和独立 Funnel 端口：

```text
远程目录：F:\apps\industry_depth_report_public_8892
远程本地服务：http://127.0.0.1:8892
公网入口：https://desktop-i22b489.tailf9d7ac.ts.net:10004/
计划任务：IndustryDepthReportPublic8892
```

部署前必须在远程服务器配置 `INDUSTRY_REPORT_PASSWORD`。用户名和口令都必须通过服务器环境变量配置，脚本不提供源码默认值。若服务器找不到正式 `research_warehouse.db`、找不到 Word 生成依赖、或没有公网认证口令，服务会拒绝启动。

```powershell
Set-Location "<project-root>"
.\board\services\industry_depth_report\deploy_to_homeserver.ps1
```

数据库不打包进公网应用。远程启动脚本会按顺序查找：

```text
F:\data\industry_depth_report\research_warehouse.db
F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db
F:\data\agent_console_private\research_warehouse.db
F:\apps\industry_depth_report_public_8892\database\research_warehouse.db
```

如果上述正式仓库不存在，但 `F:\data\agent_console_private\database.db` 存在，远程启动脚本会调用随包部署的 `framework\data_pipeline\build_warehouse.py` 构建正式仓库，并优先写入 `F:\data\industry_depth_report\research_warehouse.db`。构建失败时仍然拒绝启动。

已验证的公网入口：

```text
https://desktop-i22b489.tailf9d7ac.ts.net:10004/
```

缺少关键数据时，模型只输出 `data_quality_gate.json`、`missing_data_plan.md` 和 `payload.partial.json`，不会生成正式 Word 报告。
