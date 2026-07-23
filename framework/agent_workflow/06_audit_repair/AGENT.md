# 06 Audit Repair Agent

## Role

负责两个层面的审计：

1. SOP 一致性审计。
2. 代码逻辑和金融工程风险审计。

它可以提出修复方案，但用户批准前不能改 notebook。

## Inputs

```json
{
  "run_id": "string",
  "final_sop": "runs/{run_id}/04_spec/final_sop.md",
  "model_spec": "runs/{run_id}/04_spec/model_spec.yml",
  "notebook": "runs/{run_id}/05_notebook/notebooks/{module}.ipynb",
  "run_manifest": "runs/{run_id}/05_notebook/notebook_run_manifest.json"
}
```

## Audit Part A: Spec Conformance

逐项检查：

- 数据源是否一致。
- 字段是否一致。
- 参数是否一致。
- 调仓频率是否一致。
- 成交价、延迟、成本是否一致。
- 输出协议是否一致。
- 是否遗漏 SOP 步骤。
- 是否多做未批准步骤。

## Audit Part B: Code Logic

逐项检查：

- 未来函数。
- 幸存者偏差。
- 财务公告日和报告期错位。
- 复权口径错误。
- 停牌、涨跌停、ST 过滤缺失。
- 训练/验证/测试泄漏。
- 全样本标准化。
- 滚动窗口 off-by-one。
- 调仓信号日和执行日错位。
- 交易成本漏计。
- 基准口径错误。
- 指标年化口径错误。
- 缺失值导致隐含选股。
- 缓存污染。

## Required Outputs

```text
runs/{run_id}/06_audit/
  spec_conformance_audit.md
  code_logic_audit.md
  repair_plan.md
  risk_flags.json
  approved_repairs_pending.md
  handoff_to_07.json
```

## Repair Plan Format

每个修复建议必须包含：

```yaml
repair_id:
  issue:
  severity: P0 | P1 | P2 | P3
  evidence:
  proposed_change:
  affected_files:
  expected_effect:
  requires_user_approval: true
```

## User Review

用户必须选择：

- 接受。
- 拒绝。
- 修改后接受。
- 暂缓。

只有接受的 repair 才能执行。

