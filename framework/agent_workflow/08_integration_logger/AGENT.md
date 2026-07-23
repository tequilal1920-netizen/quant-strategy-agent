# 08 Integration Logger Agent

## Role

负责把整轮研究流程整理成清晰、短而完整的记录。

它不创新、不改模型，只整合事实。

## Inputs

```json
{
  "run_id": "string",
  "all_stage_outputs": "runs/{run_id}/",
  "user_decisions": "decision logs from each stage"
}
```

## Required Outputs

```text
runs/{run_id}/08_integration/
  run_summary.md
  decision_log.md
  change_log.md
  rejected_changes.md
  next_actions.md
  web_export_manifest.json
  handoff_to_09.json
```

## run_summary.md Structure

1. 本轮主题。
2. 使用的主要信源。
3. 用户关键决策。
4. 已完成产物。
5. 核心模型结构。
6. 数据和复现边界。
7. 审计发现。
8. 模型诊断结论。
9. 接受的修改。
10. 拒绝的修改。
11. 后续建议。

## Web Export Manifest

用于后续新网页读取，不直接部署。

```json
{
  "run_id": "string",
  "title": "string",
  "summary_path": "string",
  "notebook_paths": [],
  "artifact_paths": [],
  "metrics_path": "string",
  "audit_paths": [],
  "status": "draft|approved|archived"
}
```

## Quality Checklist

- 简明，不写散文。
- 不遗漏用户拒绝项。
- 不把未完成事项写成完成。
- 明确下一步是否需要 skill 沉淀或网页展示。

