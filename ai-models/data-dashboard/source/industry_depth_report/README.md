# 单行业深度报告 Agent

本 agent 面向申万一级行业深度研究。输入行业名称、报告日期和回看年限，输出一份包含真实数据、图表和审慎结论的 Word 报告。

## 设计边界

- 只做单一行业深度报告，不做行业比较，不做个股推荐。
- 数据计算优先使用 `database/research_warehouse.db`。
- 外部 API 和 AI 网关只通过环境变量读取密钥，不在代码、日志、Word 或 JSON 中写入凭据。
- AI 只允许基于结构化数据摘要写解释段落，不允许自行生成未给出的数值。
- 图表遵守 `board/quant_strategy_agent/static/css/ui_unified.css` 的固定色系和字体规范。

## 命令行示例

```powershell
Set-Location "<project-root>"
python model\data_dashboard\industry_depth_report\industry_depth_agent.py --industry 有色金属 --as-of 2026-06-12
```

输出目录默认位于：

```text
output/industry_depth_report/
```

