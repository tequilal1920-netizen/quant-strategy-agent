# volume_price_30

## Skill ID

`volume_price_30`

## Family

volume_price

## Inputs

- `date`
- `code`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`
- `turnover`
- 复权因子、停牌、涨跌停和行业/指数背景

## Logic

方向：contextual  
观察窗口：30 根K线  
规则：量价配合、缩量整理、放量突破；观察窗口30根K线。

## Output

- `trigger`: 是否触发
- `direction`: bullish / bearish / neutral / contextual
- `confidence`: 0-1
- `evidence`: 触发价格、量能、趋势和失败条件
- `conflicts`: 与其他K线、趋势或风险规则的冲突项

## Failure Conditions

- 数据缺口、停牌、涨跌停导致形态不可交易
- 趋势背景与形态方向冲突
- 放量/缩量确认缺失
- 触发后回撤超过止损路径

## Source Basis

国内K线/量价技术分析书籍+券商AI技术分析框架

## Execution Status

registered; execution logic pending
