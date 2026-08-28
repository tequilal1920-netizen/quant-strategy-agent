# GitHub Skill

## 触发

当任务涉及提交、推送、发布、PR、版本同步、回滚、远端仓库或“上传到 GitHub”时使用。

## 当前仓库

活动仓库位于 `G:\中信建投\agent`，远端为 `https://github.com/tequilal1920-netizen/quant-strategy-agent.git`。

## 提交纪律

1. 先运行 `git status --short --branch`，区分已跟踪改动、未跟踪文件、数据库、缓存和私密文件。
2. 提交前运行秘密扫描和大文件扫描；数据库、Office SOP、缓存、私密环境文件和授权数据不得进入 GitHub。
3. 只提交与本次版本整理和功能修复有关的文件，不用 `git add .`。
4. 推送前确认公网健康检查和核心 API 仍正常。

## 本项目上传范围

- 可以上传：正式源码、公开模型包、公开 Skill、MCP 骨架、工作区结构说明、无凭据的测试与部署维护脚本。
- 不上传：`database/*.db`、缓存、Office 文档、PDF、Excel 原始数据、授权平台导出、账号、token、cookie、refresh token、私密环境文件。
- 若必须保留本地中间文件，移入 `reference/历史归档` 或被 `.gitignore` 覆盖的本地目录，并在提交说明中写明未上传原因。

## 标准命令

```powershell
git status --short --branch
$scanPatterns = Get-Content $env:QUANT_AGENT_SECRET_SCAN_PATTERNS
$stagedFiles = git diff --cached --name-only
foreach ($pattern in $scanPatterns) { rg -n --fixed-strings $pattern $stagedFiles }
git diff --check
git commit -m "<中文或英文摘要>"
git -c http.proxy=http://127.0.0.1:7897 push origin agent/industry-style-r16-6
```

推送后用 `git ls-remote origin agent/industry-style-r16-6` 核对远端 HEAD。若代理或环境变量 token 导致 403/超时，优先修正本机 Git 配置，不把凭据写入仓库。
