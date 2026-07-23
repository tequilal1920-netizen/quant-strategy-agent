# Replication Standard

本文件定义策略复刻的等级和验收标准。

## 复刻等级

### Level 1: Evidence Replication

目标：复刻报告的文字逻辑。

要求：

- 报告关键方法完整抽取。
- 数据、模型、参数、回测规则分别标注是否披露。
- 不写代码。

适用：

- 初次研究。
- 报告太多，需要筛选方向。

### Level 2: Method Replication

目标：复刻报告的方法链条。

要求：

- 模型结构一致。
- 数据字段尽量一致。
- 未披露参数进入待审批假设。
- 可运行 notebook。

适用：

- 报告未提供完整代码。
- 原始数据源不可完全取得。

### Level 3: Result Approximation

目标：在可得数据上接近报告结果。

要求：

- 输出净值、指标、归因。
- 对比报告披露结果。
- 解释差异来源。

适用：

- 报告披露回测结果但未披露全部细节。

### Level 4: Productionized Extension

目标：把复刻策略改造成可持续运行模块。

要求：

- 数据更新、缓存、日志、审计、网页展示。
- 策略结果可定时刷新。
- 必须保留原始复刻版本，不覆盖。

适用：

- 用户确认模型值得长期维护。

## 不可伪装原则

以下情况必须写明：

- 报告未披露参数。
- 使用了替代数据源。
- 使用了替代资产代码。
- 使用了不同模型。
- 回测区间不同。
- 无法调用原始 LLM 或原始 API。
- 结果与报告不同。

## 验收最低标准

一个复刻任务至少要有：

- `research_report.md`
- `evidence_map.json`
- `data_contract.yml`
- `final_sop.md`
- `notebook.ipynb`
- `spec_conformance_audit.md`
- `code_logic_audit.md`
- `model_diagnosis.md`
- `run_summary.md`

