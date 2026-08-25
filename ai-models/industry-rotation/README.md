# 行业轮动 AI 模型包

本目录是新 Agent 查询行业景气、六维行业轮动和季度风格箱的统一入口。代码仓库只保存查询运行时、模型源码和契约，不保存大型研究数据库、缓存或运行凭据。

## 接入

```powershell
$env:QUANT_AGENT_SNAPSHOT_ROOT = "<部署目录>\board\quant_strategy_agent_vnext\data"
python ai-models/industry-rotation/scripts/query.py dimensions 频率=月频 数量=10
```

必要快照为 `rotation_snapshot.json`。六维查询还要求快照包含：

```text
industry.frequencies.monthly.six_dimension.research_ranking
industry.frequencies.weekly.six_dimension.research_ranking
```

字段缺失时运行时会要求刷新快照，不会用生产冠军排名替代研究挑战者排名。

## 常用问题

```powershell
# 最新高频景气排名
python ai-models/industry-rotation/scripts/query.py ranking 频率=高频 数量=10

# 单行业高频驱动
python ai-models/industry-rotation/scripts/query.py drivers 行业=电子 数量=8

# 月频六维研究排名
python ai-models/industry-rotation/scripts/query.py dimensions 频率=月频 数量=10

# 单行业六维分解
python ai-models/industry-rotation/scripts/query.py dimensions 频率=月频 行业=电子

# 生产冠军与研究挑战者的分样本治理
python ai-models/industry-rotation/scripts/query.py backtest
```

## 口径

- 高频景气、行业轮动和风格轮动是三套独立口径，不互相冒充。
- 旧 `rotation_snapshot.json` 的生产冠军仍保留为 C6，用于兼容旧月周常规查询和治理审计。
- 当前网页研究展示与最终两图读取 `industry_research_dashboard.json`，发布候选为 `C45_monthly_verified_quality_trend_crowding_top7_risk_weighted_buffered`。
- C45 的因子池来自六维候选表。候选原子因子先做缺失处理、去极值、截面标准化、方向固定和PIT约束，再在训练集与验证集检验 RankIC、ICIR、t值、方向胜率、Top-Bottom分层收益和窗口稳定性。
- 当前高效二级因子共24个。估值因子未同时通过训练和验证门槛，不进入主打分；资金面保留在检验表和解释层，未进入C45主权重；拥挤度只做风险扣分。
- C45 主打分为 `62%C39景气盈利主锚 + 30%技术趋势有效簇 + 8%基本面确认有效簇 - 5%拥挤风险扣分`，月末信号，下一交易日执行，Top7风险加权并保留3名缓冲。
- 训练和验证负责选模。2022年后的测试区间只报告或否决晋级，不参与调参。

详细因子清单、PIT规则和快照契约见 [module-map.md](references/module-map.md)，Agent执行纪律见 [SKILL.md](SKILL.md)。

## 验证

```powershell
Set-Location ai-models/industry-rotation/runtime
python -B -m unittest agent_runtime.test_runtime
python -B -m py_compile agent_runtime/core.py agent_runtime/cli.py agent_runtime/server.py
```
