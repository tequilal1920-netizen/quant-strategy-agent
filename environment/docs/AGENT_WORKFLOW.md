# Agent Workflow

本文件定义所有研究主题的固定闸门流程。

## 总原则

每个 Agent 都是一个独立控件：

- 独立输入。
- 独立输出。
- 独立证据。
- 独立审计。
- 独立目录。
- 独立用户确认。

任何 Agent 不得直接把结果传给下一个 Agent，必须先输出 `review_packet`，等待用户确认。

## 标准流程

### 0. Run Gatekeeper

目的：初始化本轮研究任务。

输入：

- 用户主题。
- 本地数据源。
- 指定报告源。
- 允许的数据接口。
- 禁止事项。

输出：

- `runs/{run_id}/run_brief.md`
- `runs/{run_id}/source_whitelist.json`
- `runs/{run_id}/approval_state.json`

用户确认点：

- 研究边界是否正确。
- 信源白名单是否完整。
- 是否允许进入检索研究。

### 1. Research Report Agent

目的：先检索、学习、总结，而不是直接设计模型。

输入：

- `run_brief.md`
- `source_whitelist.json`
- 本地数据库字段概览。

输出：

- `research_report.md`
- `evidence_map.json`
- `open_questions.md`
- `source_coverage.md`

用户确认点：

- 是否遗漏重要报告。
- 是否需要继续检索。
- 是否需要改变研究范围。

### 2. Data Contract Agent

目的：确认“报告方法”和“本地数据”之间能否落地。

输入：

- `research_report.md`
- `evidence_map.json`
- 本地数据库结构。

输出：

- `data_availability_report.md`
- `data_contract.yml`
- `field_mapping.json`
- `data_risk_register.md`

用户确认点：

- 哪些字段用本地库。
- 哪些字段需要外部 API。
- 是否允许调用 iFind 或其他高额度源补缺。

### 3. Design Architect Agent

目的：生成完整模型大纲。

输入：

- 用户进一步方针。
- 研究报告。
- 数据契约。

输出：

- `design_outline.md`
- `strategy_dna.json`
- `model_blocks.json`
- `implementation_routes.md`

必须给出 1-3 条复刻路径：

- Path A: 严格研报复刻。
- Path B: 方法一致、数据适配复刻。
- Path C: 研报方法基础上的 Agentic 扩展。

用户确认点：

- 选择哪条路径。
- 删除哪些模型块。
- 加入哪些额外约束。

### 4. Spec Critic Agent

目的：把大纲变成可执行、可审计的 SOP。

输入：

- 已确认的设计大纲。
- 用户补充要求。
- 证据地图。

输出：

- `final_sop.md`
- `model_spec.yml`
- `notebook_build_spec.yml`
- `assumptions_pending_review.yml`
- `file_change_plan.md`

用户确认点：

- SOP 是否足够细。
- 参数是否允许使用。
- 文件修改计划是否批准。

### 5. Notebook Reproducer Agent

目的：严格按 SOP 写 notebook 并运行。

输入：

- `final_sop.md`
- `model_spec.yml`
- `notebook_build_spec.yml`
- 已批准的文件修改计划。

输出：

- `notebooks/{module}.ipynb`
- `notebook_run_manifest.json`
- `artifacts/`
- `blocking_issues.md`

用户确认点：

- notebook 是否符合预期。
- 是否允许进入审计。

### 6. Audit Repair Agent

目的：检查“是否按 SOP 做到”和“代码是否技术正确”。

输入：

- notebook。
- SOP。
- 运行结果。

输出：

- `spec_conformance_audit.md`
- `code_logic_audit.md`
- `repair_plan.md`
- `risk_flags.json`

用户确认点：

- 哪些修复接受。
- 哪些修复拒绝。
- 是否允许改 notebook。

### 7. Model Doctor Agent

目的：从模型效果穿透到数据、信号、权重、交易、归因层。

输入：

- 审计后 notebook。
- 回测结果。
- 归因结果。
- 研究证据。

输出：

- `model_diagnosis.md`
- `attribution_tree.json`
- `iteration_candidates.md`
- `experiment_plan.md`

用户确认点：

- 选择哪些迭代。
- 哪些只记录不修改。
- 是否允许进入下一版实验。

### 8. Integration Logger Agent

目的：形成最终交互记录与可继续执行的状态。

输入：

- 所有阶段输出。
- 用户确认记录。
- 被接受和拒绝的修改。

输出：

- `run_summary.md`
- `decision_log.md`
- `change_log.md`
- `next_actions.md`
- `web_export_manifest.json`

用户确认点：

- 是否封版。
- 是否沉淀为 skill。
- 是否推送到网页。

### 9. Skill Librarian Agent

目的：把已验证方法沉淀为可复用 skill。

输入：

- final SOP。
- notebook。
- 审计报告。
- 用户批准。

输出：

- `skill/{skill_name}/SKILL.md`
- `input_schema.json`
- `output_schema.json`
- `examples/`
- `tests/`
- `CHANGELOG.md`

用户确认点：

- 是否允许以后自动调用该 skill。

