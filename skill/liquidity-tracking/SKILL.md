---
name: liquidity-tracking
description: "用于运行、审计和维护多来源资金面跟踪；当任务涉及散户、公募、私募、外资、ETF、一级市场、融资资金或跨频率资金流时使用。"
---

# 资金面跟踪

## 目标

把七类资金主体的不同频率、单位和滞后统一成带来源和可用日期的可审计快照。

## 输入

- 资金主体、截止日、频率、单位和市场范围。
- Excel、数据库或正式 API 快照及其来源元数据。
- 可选的平滑、比较基准和历史窗口。

## 输出

- 散户、公募、私募、外资、ETF、一级市场、融资资金的结论、图表和状态。
- 来源、频率、单位、可用日期、滞后、覆盖率和异常说明。
- 供主页、行业配置和组合优化消费的资金面约束。

## 工作流

1. 阅读 `references/module-map.md`、模型 README 和当前状态文件。
2. 按目标主体定位真实来源；读取 Excel 时统一使用 `xlsx_reader.py`。
3. 核对发布日期和可用时点，禁止跨频率盲目前向填充。
4. 更新唯一正式快照，并保留原始频率、单位、来源和滞后。
5. 检查七个页面的页内控件、图表、结论和状态点。
6. 验证缓存后重复访问速度，同时确保缓存键包含页面和参数。

## 约束

- 私募、外资或一级市场缺失时必须显示缺失与滞后，不得用其他主体替代。
- 状态颜色固定为绿色正常、蓝色运行、红色异常。
- 中文楷体、英文和数字 Arial；遵守统一看板布局。
- 不复制或删除 `database/` 中的大型数据库。

## 验证

```powershell
python -m py_compile model/liquidity_tracking/build_snapshot.py model/liquidity_tracking/xlsx_reader.py
python board/quant_strategy_agent/qa/test_canonical_app.py
```