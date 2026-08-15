# 行业景气度模块地图

## 固定页面与代码入口

- 一级标题：行业景气度
- 二级页面：行业景气度、风格轮动、配置策略
- AI查询入口：`ai-models/industry-rotation/scripts/query.py`
- 只读运行时：`ai-models/industry-rotation/runtime/agent_runtime/core.py`
- 本地模型镜像：`ai-models/industry-rotation/source`
- 快照入口：`ai-models/industry-rotation/source/build_snapshot.py`
- 跟踪入口：`ai-models/industry-rotation/source/build_tracking.py`
- 季度风格箱：`ai-models/industry-rotation/source/style_box_rotation.py`
- 主模型引擎：`ai-models/industry-rotation/source/engine.py`
- 六维研究引擎：`ai-models/industry-rotation/source/six_dimension_model.py`

左侧一级、二级标题保持不变。高频景气、六维轮动和季度风格箱在三个既有页面内部呈现，不能改变导航层级。

## 查询动作

| 动作 | 输入 | 返回口径 |
| --- | --- | --- |
| `ranking` | `频率=高频` | 31行业专属景气排名 |
| `drivers` | `行业=<名称或代码>` | 单行业高频指标贡献、来源和可见日期 |
| `ranking` | `频率=月频或周频` | 当前生产冠军排序和绩效 |
| `dimensions` | `频率=月频或周频` | 六维研究挑战者排名、审计和PIT质量 |
| `dimensions` | `频率=月频或周频 行业=<名称或代码>` | 单行业六维分解 |
| `style` | `数量=<1至50>` | 季度3×4风格箱及迁移 |
| `backtest` | 无 | 生产冠军绩效、研究挑战者审计和晋级门禁 |

六维查询只接受 `research_ranking`。缺少该字段时返回可解释错误，不回退到生产冠军排名。

## 六维与53个原子因子

### 景气度：5项

`prosperity_level`景气水平、`prosperity_acceleration`景气加速度、`prosperity_consensus`景气口径共识、`prosperity_reliability`景气数据可靠性、`prosperity_agreement`景气模型一致度。

### 基本面：12项

`roe`净资产收益率、`roa`总资产收益率、`gross_margin`毛利率、`netprofit_margin`净利率、`assets_turn`资产周转率、`current_ratio`流动比率、`debt_to_assets`低资产负债率、`tr_yoy`营业收入增速、`netprofit_yoy`归母净利润增速、`op_yoy`营业利润增速、`revenue_positive_breadth`收入正增长扩散度、`profit_positive_breadth`利润正增长扩散度。

### 技术面：12项

`momentum_12_1`十二减一月相对动量、`momentum_6_1`六减一月相对动量、`momentum_3_1`三减一月相对动量、`momentum_1`一月相对动量、`risk_adjusted_momentum`风险调整动量、`path_efficiency_126`半年趋势效率、`path_efficiency_63`季度趋势效率、`distance_ma120`半年均线距离、`distance_ma60`季度均线距离、`breadth_20`二十日上涨扩散度、`breadth_60`六十日上涨扩散度、`short_reversal`短期反转。

### 估值：4项

`earnings_yield`盈利收益率、`book_yield`账面收益率、`sales_yield`销售收益率、`dividend_yield`股息率。

### 资金面：10项

`flow_total_5`五日总流量强度、`flow_total_20`二十日总流量强度、`flow_total_60`六十日总流量强度、`flow_large_5`五日大单结构残差、`flow_large_20`二十日大单结构残差、`flow_large_60`六十日大单结构残差、`flow_extra_20`二十日超大单结构残差、`flow_extra_60`六十日超大单结构残差、`flow_breadth_20`二十日净流入扩散度、`flow_persistence_20`二十日净流入持续度。大单结构逐日对同窗口总流量做含截距横截面残差化；超大单结构再对同窗口总流量和大单结构做二次正交。每次回归至少需要20个有效行业，先稳健缩尾；覆盖不足时保留缺失，禁止补零。

### 拥挤度：10项

`turnover_level`换手水平、`turnover_expansion`换手扩张、`volume_ratio`量比水平、`amount_concentration`成交集中度、`limit_up_heat`涨停热度、`short_momentum_heat`短期涨幅热度、`price_distance_heat`价格偏离热度、`volatility_expansion`波动扩张、`breadth_heat`上涨扩散热度、`low_dispersion_heat`低分歧热度。

前五维为收益信号。拥挤度经过非负连续映射后只扣分，不产生低拥挤奖励。`anti_crowding`是界面方向统一字段，不计入53项，也不是第七维。

## 当前候选结构

| 频率 | 冻结研究候选 | 五类收益维度权重 | 拥挤处理 | 组合 |
| --- | --- | --- | --- | --- |
| 月频 | `C27`质量趋势正交增强（当前选择） | C6锚定；基本面10%、技术面18%、资金面7%为叠加上限 | 拥挤度仅作风险诊断 | Top10等权，缓冲3名 |
| 周频 | `C29`冠军锚定在线增强（当前选择） | C6锚定；基本面4%、技术面20%、资金面10%为叠加上限 | 拥挤度仅作风险诊断 | Top10等权，缓冲3名 |

维度内部先按独立信息簇合成，防止同类窗口重复增加权重。实际候选、权重和组合规则必须以当前快照的 `candidate_audit`、`six_dimension.current_weights` 和 `target_policy` 为准。

## 治理边界

- 生产冠军：当前为 `C6_direct_month_smooth`，常规月周排名和生产绩效从 `selected_candidate` 读取。
- 研究挑战者：月频由训练与验证选择C27，周频选择C29，从 `research_selected_candidate` 读取。
- C27与C29均属于 `post-test diagnostic`。这些架构在2022年后测试区间已被观察后形成，只能研究跟踪；报告期门禁均为拒绝，不能替换生产冠军。
- 候选由训练集和验证集选择。测试集仅报告或否决晋级，不参与候选排序、模型选择或参数调整。
- 不得把验证期高夏普外推为测试期提升，不得承诺夏普达到1.5。

## PIT数据规则

1. 申万行业成分按信号日有效区间连接。重叠区间标准化后仍无法区分的同日冲突隔离，不允许重复归属。
2. 财务字段只在 `visible_date < signal_date` 时可用。缺少公告时刻时公告日当日不可用，同时要求 `end_date <= visible_date`。
3. 资金流金额从万元乘10转换为千元，并以资金流有值股票的千元成交额作为分母。
4. 股息率使用小数口径。
5. 月度标签采用信号后首个交易日收盘至下一执行日收盘的行业超额收益，标签不重叠。
6. 在线信息系数权重只能读取 `maturity < current_signal` 的成熟标签，且仅作为诊断监控，不替代预设生产候选。

## 快照契约

- 根节点 `six_dimension`：模型版本、因子标签、53项分布、PIT质量、诊断和治理规则。
- `industry.frequencies.<monthly|weekly>.six_dimension.research_ranking`：六维挑战者的当前排序及六维分解。
- `selected_candidate`：生产冠军。
- `research_selected_candidate`：研究挑战者。
- `candidate_audit`：训练、验证、测试仅报告及组合规则。
- `promotion_gate`：晋级状态和否决原因。

AI回答必须同时返回模型角色、数据截止、生产冠军、研究挑战者和测试使用规则。
