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
