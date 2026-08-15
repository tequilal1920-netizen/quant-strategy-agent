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

- 六维模型使用53个原子因子：景气度5项、基本面12项、技术面12项、估值4项、资金面10项、拥挤度10项。
- 景气度、基本面、技术面、估值和资金面形成收益信号。拥挤度只作风险扣分。
- 资金面10项由总流量强度3项、大单结构残差3项、超大单结构残差2项、流入扩散度和流入持续度组成，避免嵌套订单口径重复加权。
- 当前生产冠军为C6。生产方向冻结自R32冠军参数，稀疏月频样本不再重新估计方向；21日远期IC仅作成熟标签诊断。
- 六维层以C6为锚，只允许训练与验证均有效且对冠军正交后仍有增量的信息进入研究叠加。估值与拥挤度当前不提供收益权重，拥挤度仅作风险诊断。
- 月频研究挑战者为C27质量趋势正交增强，周频研究挑战者为C29冠军锚定在线增强。两者均属于 `post-test diagnostic`，报告期未通过冠军挑战门，不替换C6。
- 训练和验证负责选模。2022年后的测试区间只报告或否决晋级，不参与调参。

详细因子清单、PIT规则和快照契约见 [module-map.md](references/module-map.md)，Agent执行纪律见 [SKILL.md](SKILL.md)。

## 验证

```powershell
Set-Location ai-models/industry-rotation/runtime
python -B -m unittest agent_runtime.test_runtime
python -B -m py_compile agent_runtime/core.py agent_runtime/cli.py agent_runtime/server.py
```
