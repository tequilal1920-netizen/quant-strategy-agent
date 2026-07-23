<div style="font-size: 11px; line-height: 1.5; margin: 0;">
<div><strong>{TITLE}</strong></div>
<div>{PURPOSE}</div>
<ul style="margin: 4px 0 0 18px; padding: 0;">
<li>数据优先读取本地数据库和缓存。</li>
<li>所有外部接口调用必须写入日志。</li>
<li>所有结果保留在 notebook 输出中。</li>
</ul>
</div>

## Notebook Cell Order

1. HTML 风格封面 Markdown。
2. 自动重载和环境设置。
3. 路径、依赖、配置检查。
4. 数据源和字段可用性检查。
5. 数据读取与缓存。
6. 数据清洗、滞后、缺失、异常处理。
7. 特征或信号构建。
8. 模型训练或规则计算。
9. 权重、持仓或交易动作生成。
10. 回测。
11. 归因。
12. 稳健性和敏感性检查。
13. 结果导出。
14. 运行摘要。

## Required Outputs

- `notebook_run_manifest.json`
- 回测净值表。
- 关键指标表。
- 归因表。
- 风险审计表。
- 使用的数据快照或缓存路径。

