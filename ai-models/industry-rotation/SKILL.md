---
name: industry-rotation
description: "查询和审计31个申万一级行业的高频景气、六维多因子行业轮动、月周配置、季度3×4风格箱、月频六维风格轮动及分样本回测治理；涉及行业排名、单行业驱动、六维分解、风格标签或行业配置时使用。"
---
# 行业景气度

## 直接查询

```powershell
python ai-models/industry-rotation/scripts/query.py ranking 频率=高频 数量=10
python ai-models/industry-rotation/scripts/query.py drivers 行业=电子 数量=8
python ai-models/industry-rotation/scripts/query.py dimensions 频率=月频 数量=10
python ai-models/industry-rotation/scripts/query.py dimensions 频率=月频 行业=电子
python ai-models/industry-rotation/scripts/query.py dimensions 频率=周频 数量=10
python ai-models/industry-rotation/scripts/query.py style 数量=12
python ai-models/industry-rotation/scripts/query.py backtest
```

问题路由：

- 哪个行业景气度最高：`ranking 频率=高频`
- 某行业的高频景气由什么驱动：`drivers 行业=<名称或代码>`
- 六维模型当前看好哪些行业：`dimensions 频率=月频|周频`
- 某行业的景气度、基本面、技术面、估值、资金面和拥挤度如何：`dimensions 频率=月频|周频 行业=<名称或代码>`
- 月周轮动的训练、验证、测试表现及晋级状态：`backtest`

`ranking 频率=月频|周频`读取生产冠军排序。`dimensions`只读取六维研究挑战者的 `research_ranking`。旧快照缺少该字段时应刷新快照，不得用生产排名代替六维排名，也不得在查询层重新拼分。

## 三套独立口径

1. 高频景气：31个申万一级行业各自的专属业务指标，用于最新景气排名和驱动归因。
2. 六维轮动：候选原子因子组成景气度、基本面、技术面、估值、资金面和拥挤度。当前研究展示先在训练和验证区间检验二级因子，再筛出24个高效因子；估值未通过门槛不进主打分，资金面保留在解释层，拥挤度只作为风险扣分。
3. 季度风格箱：股票级大盘、中盘、小盘与成长、均衡、价值、红利形成12个互斥且穷尽的风格箱。

三套口径不得互相冒充。六维模型先在维度内按独立信息簇合成，避免同类窗口重复放大，再按候选预设权重融合。资金面保留10项：总流量强度3项、大单结构残差3项、超大单结构残差2项、流入扩散度1项、流入持续度1项；大单和超大单必须逐日正交，不能与已包含它们的总流量重复加权。

## 当前治理身份

- `C6_direct_month_smooth`是旧 `rotation_snapshot.json` 的生产冠军。月周常规查询和生产绩效继续保留该冠军，除非正式快照中的 `selected_candidate` 发生经治理的变更。
- 当前网页研究展示和最终两图读取 `industry_research_dashboard.json`，发布候选为 `C45_monthly_verified_quality_trend_crowding_top7_risk_weighted_buffered`。
- C45 的主打分为 `62%C39景气盈利主锚 + 30%技术趋势有效簇 + 8%基本面确认有效簇 - 5%拥挤风险扣分`。月末信号，下一交易日执行，Top7风险加权并保留3名缓冲。
- 候选排序和选模只使用训练集与验证集。2022年后的测试集只报告或否决晋级，不参与调参、候选排序或事后筛选。不得承诺夏普阈值。

回答时以快照中的 `selected_candidate`、`research_selected_candidate`、`published_candidate`、`candidate_audit` 和 `promotion_gate` 为准。必须同时说明生产冠军、研究展示候选、数据截止和测试仅报告口径。

## 因子结构

- 景气度
- 基本面
- 技术面
- 估值
- 资金面
- 拥挤度

完整因子名称、候选权重和筛选结果以 `industry_research_dashboard.json` 的 `rotation.factor_table` 与 `rotation.efficient_factors` 为准。查询返回的拥挤度是风险热度，数值越高表示扣分压力越大；`anti_crowding`仅供图形方向统一，不构成第七维。

## PIT与标签约束

- 行业成分使用信号时点有效区间。重叠区间先标准化；无法区分的同日同优先级冲突必须隔离，不能重复计入。
- 财务数据必须满足 `visible_date < signal_date`。数据库没有公告时刻时，公告日当日不可使用。
- 财务查询同时限制 `end_date <= visible_date`，避免报表期晚于可见日。
- 资金流金额由万元乘10转为千元，再除以同一资金流覆盖股票的千元成交额。分母不能使用未覆盖股票成交额。
- 股息率统一使用小数口径。
- 月度标签为信号后首个交易日收盘至下一执行日收盘的行业超额收益。标签不重叠，在线权重只能读取在当前信号日前已经成熟的标签。
- 训练与验证负责选模；2022年后的测试区间保持仅报告或否决。

## 输出纪律

- `dimensions`输出六维研究排名或单行业六维分解、53因子分布、研究审计、晋级门禁和PIT质量。
- `backtest`中的 `绩效`明确标记为生产冠军口径；六维候选单列为 `研究挑战者审计`，其中测试数据标记为 `测试仅报告`。
- 数据缺失、快照过期、字段缺失或治理门禁未通过时直接说明，不用近似值补齐。
- 只读问题不得改写数据库、快照、候选注册表或模型状态。

## 验证

```powershell
Set-Location ai-models/industry-rotation/runtime
python -B -m unittest agent_runtime.test_runtime
python -B -m py_compile agent_runtime/core.py agent_runtime/cli.py agent_runtime/server.py
```
## 月频六维风格轮动

- `source/style_six_dimension_monthly.py` 将行业轮动六维框架映射到股票风格标签，形成12格风格箱、3类市值、4类风格三套月频策略。
- 六维为景气度、基本面、技术面、估值、资金面、拥挤度。前五维形成收益信号，拥挤度用于扣分和低拥挤确认。
- 因子进入候选前先做训练期和验证期 RankIC、ICIR、胜率、覆盖率和方向稳定性检验。测试集只报告和否决，不参与候选排序。
- 当前结果写入 `board/quant_strategy_agent_vnext/data/style_six_dimension_monthly.json`。最终展示图写入 `board/quant_strategy_agent_vnext/static/rotation_figures/`，网页只读取脱敏后的 `rotation_final_figures.json`。
- 复现命令：`python model\industry_rotation\style_six_dimension_monthly.py`，更新展示图：`python model\industry_rotation\build_rotation_final_figures.py`。
