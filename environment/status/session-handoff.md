# 会话交接

当前生产版本为 `2026.07.23-research-workspace-r16.3`，K 线模型为 `9.0-cohort-wyckoff-evolution`。公网统一入口、账号迁移、27 个二级页面、51 个页内功能、8 个一级 Skill 和文件清理均已完成并验证。GitHub CLI 浏览器授权已成功完成，当前正在把无历史、已审查的源码快照发布到公开仓库 `tequilal1920-netizen/quant-strategy-agent`。

## 已验证状态

- 公网：https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/
- AI 监控：https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/
- 生产切换备份：`F:\apps\quant_strategy_agent\deployment_backups\research_workspace_r16_3_switch_20260723_161838`
- 重组前 Git 标签：`backup/ui-before-redesign-20260723`
- 浏览器：27/27 页面、51/51 页内功能；控制台错误 0、页面错误 0、溢出 0。
- 回归：统一入口 7/7、资产配置 16/16、组合优化 5/5、行业合同 31 行业/248 字段、全量 Python 编译、3 个 JavaScript 文件和 7 个 PowerShell 脚本解析通过。
- Skill：8/8 通过官方 `quick_validate.py`。
- 生产凭据已按用户要求迁移，现有内容和任务状态不变；私密值未写入仓库。

## 文件约束

- 11.9GB `database/research_warehouse.db` 及 WAL/SHM 只能原地保留，不复制、不移动、不提交。
- `copy/previous_version_20260721` 是唯一重组前源码副本，约 1.7MB，被 Git 忽略。
- `G:\中信建投` 根目录 14 份正式 Word/Excel 文档全部保留，不提交公开仓库。
- `agent/` 内 5 份 UI/SOP/框架 Word 继续原地保留，但已从 Git 跟踪中移除并由 `*.docx` 规则忽略，不上传公开仓库。
- 数据库、输出、缓存、私密环境和部署 ZIP 不得提交。
- 本地与远端部署 ZIP、上传器日志及临时发布仓库均已删除；r15/r16.3 正式发布和回滚目录保留。

## 下一步

1. 使用已验证的 GitHub CLI 登录发布无历史源码快照，并验证公开仓库文件结构。
2. 只发布已通过检查的当前源码快照：389 个文件、37.26MiB、父提交 0，不含 Git 历史、Office/PDF、数据库、输出、`copy/`、ZIP 或真实凭据。
3. 公开访问仓库与 8 个主模型/Skill 路径验证成功后，才把全部 `MODULE.json` 的 `github_status` 从 `ready_to_publish` 更新为 `published`，并同步更新状态文件。
