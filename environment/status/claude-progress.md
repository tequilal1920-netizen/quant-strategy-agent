# 当前进度

更新时间：2026-07-23

## 已完成并验证

- `agent/` 按 `board/database/environment/framework/model/output/skill` 职责组织；`copy/previous_version_20260721` 作为唯一重组前源码备份，约 1.7MB，不含数据库。
- 左侧信息架构已收敛为 8 个一级板块、27 个二级页面；旧模型和全部可视化均通过 `framework/integration/ui_module_mapping.json` 映射到新板块，没有删除核心图表或子面板。
- 8 个一级板块均具备 `model/<module>/MODULE.json` 与标准 Skill（`SKILL.md`、`agents/openai.yaml`、`references/module-map.md`），全部通过官方 `quick_validate.py`。
- 因子增强、LLM 因子挖掘和 K 线记忆学习保留为一级模型内部组件；126 份 K 线模式文档已迁移到 `skill/technical-analysis/references/kline-patterns/`，迁移前后哈希一致。
- 主页已整合数据看板研报式日度点评和资产配置—资金面—行业轮动—个股选择—组合优化的日/周/月组合与权重输出。
- 每个页面顶部均有固定的更新频率、风险偏好、数据日期与板块内功能目录；页内功能可直接跳转和切换。
- UI 状态点含义固定为绿=正常、蓝=运行、红=异常；中文楷体、英文 Arial；可见 HTML 字体下限 14px，图表字体下限 11px；卡片、结论框和标题规范统一。
- 导航请求串行化，视图缓存按路由与参数隔离；K 线和因子状态/历史/详情预热，服务端分层缓存；gzip、ETag 和条件 304 生效。
- 生产凭据已迁移到用户指定的新管理员账号，历史内容与任务状态未改变；凭据只保存在远端私密环境文件，不进入仓库。
- 公网已切换到 `2026.07.23-research-workspace-r16.3`，K 线模型为 `9.0-cohort-wyckoff-evolution`；独立 r15 目录与计划任务 XML 备份可回滚。
- 远端临时端口预检通过；公网版本、登录、服务、数据看板、资产配置、行业轮动、因子、K 线会话/股票/日期/历史共 12 项 API 验收通过。
- 公网真实浏览器通过 27 个页面、51 个页内功能的真实点击回归；控制台错误 0、页面错误 0、横向溢出 0。按每个页面连同其全部页内功能计时，中位数约 475ms，P95 约 4.42s，最大约 5.13s；K 线学习全部子功能约 0.53s，因子挖掘全部 6 个子功能约 5.13s。
- AI 监控 iframe 返回 200、可见尺寸 1139×760、子 frame 正常加载；其内部保留独立登录状态。
- `test_canonical_app.py` 7/7、资产配置 16/16、组合优化、行业 31/248 合同、Python 编译、JavaScript 语法和全部部署 PowerShell 解析均通过。
- 14 份正式 Word/Excel 学习、SOP 和参考文件全部保留在 `G:\中信建投`；仅删除渲染缓存、候选 PDF、临时截图和生成/审计中间脚本。
- 11.9GB `research_warehouse.db` 及 WAL/SHM 未复制、未移动、未删除；发布包只继承 28KB 因子状态库并继续原地引用外部研究数据库。

## 当前发布证据

- 生产 URL：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`
- AI 监控 URL：`https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/`
- 生产版本：`2026.07.23-research-workspace-r16.3`
- 切换备份：`F:\apps\quant_strategy_agent\deployment_backups\research_workspace_r16_3_switch_20260723_161838`
- 重组前 Git 标签：`backup/ui-before-redesign-20260723`
- 公开仓库目标：`tequilal1920-netizen/quant-strategy-agent`

## GitHub 公开发布

- 公开仓库 `tequilal1920-netizen/quant-strategy-agent` 已建立默认 `main`，无历史根提交为 `cb7989cbd66325597e57ced2eeea46f1dc1bdcfc`。
- 远端验收为 389 个 blob；8 个主模型、8 个 Skill 及其 `SKILL.md`、`agents/openai.yaml`、`references/module-map.md` 全部存在，缺失项 0。
- 数据库、Office/PDF/ZIP、输出、`copy/`、真实凭据和 50MB 以上文件均未发布；禁传远端路径 0。`database/README.md` 仅为空数据库目录说明。
- GitHub CLI 浏览器授权账号为 `tequilal1920-netizen`；授权令牌未被读取、显示或写入项目文件。
- 本地与远端部署 ZIP、上传器日志和临时公开仓库已经删除；r15 回滚目录和 r16.3 正式发布目录保留。

## 剩余动作

- 本轮发布无剩余动作；后续新增源码必须重新执行敏感信息、禁传路径、模型/Skill 路径和公网功能回归。

## 约束

- 数据库、运行输出、缓存、真实凭据、`copy/` 和正式 Word/Excel 文档不得提交到公开 GitHub。
- 不删除任何核心模型、图表、正式研究文档或大型数据库。
- 未实际验证的功能不得标记为完成。
