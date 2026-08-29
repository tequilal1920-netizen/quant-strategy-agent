# 资产配置框架 Skill

## 触发

当用户询问美林时钟、普林格周期、BL模型、宏观因子模型、风险预算模型、四资产权重、周期状态或资产配置回测时使用。

## 模型入口

- AI 入口：`agent/ai-models/asset-allocation`
- 模型源码：`agent/model/asset_allocation`
- 网页数据：`agent/board/quant_strategy_agent/data/asset_allocation_snapshot.json`
- 数据链路：`agent/framework/data_pipeline/backfill_asset_allocation_v51.py`
- 查询脚本：`agent/ai-models/asset-allocation/scripts/query.py`

## 二级页面

- 周期跟踪：美林、普林格、基钦、朱格拉、康波等周期状态和证据。
- 资产配置：四资产权重、风险贡献、资产组约束、主动/稳健目标、换手、回测和压力情景。

## 核心计算

1. 周期识别先按各宏观指标的可见日与频率整理状态，不用未来修订值。
2. 权重生成使用周期状态、宏观因子、BL/风险预算/约束优化等多模型结果，最终只展示治理认可的正式入口。
3. 训练集用于学习参数，验证集用于定型和候选选择，测试期只报告，不允许事后调参。
4. 资产权重必须满足权重和、上下界、风险贡献、换手和成本约束，不能用等权线替代正式组合。
5. 日度可更新字段来自本地数据库或授权接口快照，月季频字段按自然发布时间刷新。

## 工作流程

1. 读取当前快照，确认周期状态、资产权重、数据截止和生成时间。
2. 若更新模型，先更新底层资产、宏观因子和协方差，再生成交互式 payload。
3. 美林和普林格负责周期识别；BL、宏观因子、风险预算负责权重生成与回测。
4. 测试期只报告，不能根据已观察测试继续调参或宣称无条件生产有效。
5. 修改资产配置时必须同步检查数据看板、行业风格、因子实验室、技术分析和组合优化入口是否被回滚。

## 查询与验收

```powershell
python ai-models/asset-allocation/scripts/query.py current 画像=平衡
python ai-models/asset-allocation/scripts/query.py cycle
python -m py_compile model/asset_allocation/asset_allocation_engine.py
```

网页验收以 `/api/asset-allocation/interactive`、配置页控件、图表数量、表格样式、跳转性能和公网 `/healthz` 为准。
