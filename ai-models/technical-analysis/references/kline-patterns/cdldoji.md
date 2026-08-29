# CDLDOJI

## Skill ID

`cdldoji`

## Family

classic_candlestick

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
观察窗口：1 根K线  
规则：识别CDLDOJI对应实体、影线、跳空和趋势上下文组合。

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

K线技术分析书籍+TA-Lib命名体系

## Execution Status

executable in current analyzer
