# Multi Signal Fusion

## Skill ID

`multi_signal_fusion`

## Purpose

把单根K线、组合K线、趋势、量价、波动、突破、过热和止损信号合成一个可审计的技术面综合指标。

## Inputs

- 全部K线skill输出
- 20/60/120日趋势
- 成交量和换手确认
- 波动率收缩/扩张
- 停牌、涨跌停、流动性和行业背景

## Logic

1. 先过滤不可交易日期和异常价格。
2. 趋势skill决定基础方向，反转形态只能在趋势背景成立时提高置信度。
3. 量价确认作为乘数，缺失时降低权重。
4. 过热、破位和止损skill作为硬门槛。
5. 输出`technical_composite_score`、主要证据和冲突项。

## Output

- `technical_composite_score`
- `primary_signal`
- `supporting_signals`
- `risk_flags`
- `agent_prompt_package`

## Execution Status

executable design; analyzer binding required for full production use.
