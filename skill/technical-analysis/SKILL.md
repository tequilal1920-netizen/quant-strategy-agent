---
name: technical-analysis
description: "用于运行、审计和维护技术分析；当任务涉及K线学习、单股或同类公司分析、形态记忆、情境进化、历史任务或技术配置策略时使用。"
---

# 技术分析

## 目标

把 K 线任务、同类公司学习、126 份形态记忆、情境检索和技术配置策略整合为可追溯的学习与决策流程。

## 输入

- 股票代码、名称或搜索条件，截止日和复权口径。
- 学习窗口、预测周期、同类公司范围、技术指标和风险约束。
- 可选的已有任务、情境记忆或形态文件。

## 输出

- 单股与同类公司的 K 线、量价、形态、基本面和事件诊断。
- 规则排名、同类公司、情境记忆、进化记录和历史任务状态。
- 技术配置策略、置信度、失效条件、风险提示和可复现输入。
- 受影响的形态参考文档和技能注册表更新。

## 工作流

1. 阅读 `references/module-map.md`、`model/kline_memory_learning/AGENT.md`、模型 README 和技能注册表。
2. 核对证券、交易日、复权、价格、成交量、财务和事件数据的可用时点。
3. 根据任务选择单股分析、同类公司学习或横截面研究，避免重复加载整个大库。
4. 先检索 `references/kline-patterns/` 中的相关形态，再运行正式模型入口。
5. 将新证据写入正式记忆和输出；保留来源、样本窗口、置信度和失效条件。
6. 检查规则排名、同类公司、情境记忆、进化记录、历史任务和技术策略页内控件。
7. 验证健康、历史、股票搜索和日期接口的缓存、并发、错误状态和跳转。

## 约束

- 不把技术形态描述成确定收益；必须给出失效条件和风险提示。
- 不使用截止日之后的价格、财务或事件信息。
- 126 份形态参考文档是正式知识，不得当作中间文件删除。
- 状态颜色固定为绿色正常、蓝色运行、红色异常。
- 中文楷体、英文和数字 Arial；遵守统一看板布局。

## 验证

```powershell
python -m py_compile model/kline_memory_learning/single_stock_analyzer.py model/kline_memory_learning/cohort_wyckoff_learning.py model/kline_memory_learning/cross_sectional_factor_study.py
python board/quant_strategy_agent/qa/test_canonical_app.py
```

浏览器必须验证“任务设置 / 学习记忆 / 历史记录”和“规则排名 / 同类公司 / 情境记忆 / 进化记录”的跳转与内容。