# Notebook Standard

Notebook 是每个策略复刻的最终可执行载体。

## 格式

参考：

```text
environment/templates/notebook_outline.md
```

首个 Markdown 单元使用紧凑 HTML 风格：

```html
<div style="font-size: 11px; line-height: 1.5; margin: 0;">
<div><strong>标题</strong></div>
<div>用途说明。</div>
</div>
```

## 代码要求

- 代码单元少而清楚。
- 长逻辑应放在函数里。
- 路径集中定义。
- 参数集中定义。
- 所有随机过程固定 seed。
- 所有外部接口必须缓存。
- 所有结果必须可重复生成。

## 输出要求

必须保留：

- 环境检查输出。
- 数据样本检查输出。
- 核心参数表。
- 关键结果表。
- 图表。
- 回测摘要。
- 归因摘要。
- 审计辅助表。

## 文件输出

```text
artifacts/
  data_snapshot.json
  metrics.csv
  equity_curve.csv
  holdings.parquet
  attribution.csv
  figures/
```

## 失败处理

如果 notebook 无法跑通：

- 保留已执行输出。
- 写 `blocking_issues.md`。
- 不伪造结果。
- 不进入审计通过状态。

