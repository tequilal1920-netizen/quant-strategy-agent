# 单行业深度报告数据契约

## 正式报告硬性规则

正式 Word 报告必须先通过 `IndustryDataQualityGate`。质量门未通过时，只生成：

- `payload.partial.json`
- `data_quality_gate.json`
- `missing_data_plan.md`

不得生成正式 Word。

## 章节与数据表

| 报告章节 | 必需数据 | 主表 | 最低要求 |
|---|---|---|---|
| 行业定义 | 申万一级行业成分 | `sw_l1_industry_daily` | 报告日成分不少于5只 |
| 需求验证 | 行业等权收益，成交额 | `stock_ohlcv_daily` | 最新日覆盖报告日，交易日不少于120个 |
| 定价验证 | PE，PB，PS，股息率，换手率 | `stock_valuation_daily` | 最新日覆盖报告日，核心字段非空 |
| 资金位置 | 净流入，20日净流入 | `stock_moneyflow_daily` | 不少于60个交易日 |
| 财务验证 | 收入，利润，ROE，杠杆，增速 | `financial_report_visible` | 最新样本覆盖不少于45% |
| 宏观背景 | PMI，PPI，M1，M2，社融 | `macro_monthly` | 至少具备PMI或货币指标 |
| 模型辅助 | 行业信号得分，目标权重 | `v3_industry_signal` | 目标行业有有效序列 |
| 研报证据 | 券商研报索引 | `broker_report_index` | 缺失为警告，不直接阻断 |

## 行业专属高频扩展

后续扩展时，应新增统一长表：

```sql
create table if not exists industry_hf_indicator (
  indicator_date text not null,
  industry_name text not null,
  indicator_group text not null,
  indicator_name text not null,
  indicator_value real,
  unit text,
  source text not null,
  source_url text,
  quality_flag text not null,
  updated_at text not null,
  primary key (indicator_date, industry_name, indicator_name, source)
);
```

建议分组：

- `demand`：销量，订单，客流，招标，出口，开工。
- `price`：产品价格，服务价格，利差，费率，批价。
- `supply`：产能，开工率，库存，资本开支。
- `profit`：价差，毛利率，ROE，现金流。
- `trade`：估值分位，成交额占比，换手率，基金持仓。

外部接口只允许按缺口清单取数，不允许无边界全量拉取。

