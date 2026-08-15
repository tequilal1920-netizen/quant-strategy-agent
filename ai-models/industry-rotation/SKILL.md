---
name: industry-rotation
description: "查询和审计31个申万一级行业的高频景气、53因子六维行业轮动、月周配置、季度3×4风格箱及分样本回测治理；涉及行业排名、单行业驱动、六维分解、风格标签或行业配置时使用。"
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
2. 六维轮动：53个原子因子组成景气度、基本面、技术面、估值、资金面和拥挤度。前五维形成收益信号，拥挤度只作为非负风险扣分，不提供低拥挤收益奖励。
3. 季度风格箱：股票级大盘、中盘、小盘与成长、均衡、价值、红利形成12个互斥且穷尽的风格箱。

三套口径不得互相冒充。六维模型先在维度内按独立信息簇合成，避免同类窗口重复放大，再按候选预设权重融合。资金面保留10项：总流量强度3项、大单结构残差3项、超大单结构残差2项、流入扩散度1项、流入持续度1项；大单和超大单必须逐日正交，不能与已包含它们的总流量重复加权。

## 当前治理身份

- `C6_direct_month_smooth`是现有生产冠军。月周常规排名和生产绩效继续读取该冠军，除非正式快照中的 `selected_candidate` 发生经治理的变更。
- C6的248项方向参数冻结自R32冠军版本。21日远期IC使用日频成熟标签进行诊断，不再用约24个月频样本重估生产方向。
- 月频训练与验证当前选择 `C27_monthly_post_test_diagnostic_six_dimension_defensive_top10_buffered`，标签为质量趋势正交增强；周频选择 `C29_weekly_post_test_diagnostic_six_dimension_equal_top10_buffered`，标签为冠军锚定在线增强。
- C27与C29均是在2022年后测试区间已被观察后形成的架构，状态固定为 `post-test diagnostic`。它们只能用于研究和未来样本跟踪，报告期未通过冠军挑战门，不能替换C6。
- 候选排序和选模只使用训练集与验证集。测试集只报告或否决晋级，不参与调参、候选排序或事后筛选。不得宣称测试集表现提升，不得承诺夏普阈值。

回答时以快照中的 `selected_candidate`、`research_selected_candidate`、`candidate_audit` 和 `promotion_gate` 为准。必须同时说明生产冠军、研究挑战者、数据截止和测试仅报告口径。

## 53因子结构

- 景气度5项
- 基本面12项
- 技术面12项
- 估值4项
- 资金面10项
- 拥挤度10项

完整因子名称和候选权重见 `references/module-map.md`。查询返回的拥挤度是风险热度，数值越高表示扣分压力越大；`anti_crowding`仅供图形方向统一，不构成第七维。

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
