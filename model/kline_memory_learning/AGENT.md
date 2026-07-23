# 04 个股K线记忆学习Agent

## 定位

本模型替换旧版固定阈值K线规则引擎，定位为“单股票、全历史、多频率、逐K线、可回顾”的技术面学习Agent。它从本地数据库读取个股成立以来到指定日期的结构化行情，生成日线、周线、月线以及多个滚动窗口K线，逐根K线套入候选技术理论，记录成功与失败样本，在训练集、验证集、测试集和全样本上形成可解释的买卖点逻辑、回测结果和学习笔记。

## 本地数据源

默认数据库：

- `database/research_warehouse.db`

直接使用的表：

- `stock_ohlcv_daily`：交易日、股票代码、名称、开高低收、前收、涨跌幅、成交量、成交额、涨跌停价、停牌字段。
- `stock_valuation_daily`：换手率、量比、估值和市值字段，用于补足量价上下文。
- `stock_moneyflow_daily`：大单、超大单和净流入字段，用于构造订单流Delta代理变量。
- `kline_feature_daily`：可选写入表，仅在显式传入 `--write-db` 时写入学习后的规则摘要。

外部数据接口只作为缺数兜底，不在代码中保存任何账号、token 或 key。

## 运行入口

主程序：

- `model/kline_memory_learning/single_stock_analyzer.py`

常用命令：

```powershell
python model\kline_memory_learning\single_stock_analyzer.py --code 000001.SZ --as-of 20260630
```

仅做控制台验证、不落地产物：

```powershell
python model\kline_memory_learning\single_stock_analyzer.py --code 000001.SZ --as-of 20260630 --no-artifacts
```

显式要求GPT复核：

```powershell
$env:AI_ROUTER_API_KEY="..."
$env:AI_ROUTER_BASE_URL="https://ai.router.team/v1"
$env:KLINE_GPT_MODEL="gpt-5.5"
$env:KLINE_GPT_REASONING_EFFORT="xhigh"
python model\kline_memory_learning\single_stock_analyzer.py --code 000001.SZ --require-gpt
```

如果要把压缩后的全历史逐K线事件卡全部交给GPT复核，增加：

```powershell
--gpt-full-history
```

## 全流程（9.0）

1. `DataAgent` 读取上市以来到截止日的日线、估值和资金流，并把原始开高低按前复权收盘因子统一缩放；原始价格只保留给涨跌停和成交检查。
2. `FrequencyAgent` 生成日、周、月、3/5/10/20/60/120日滚动K线；`BarLabelAgent` 的5/10/20/60日标签始终映射到真实日线交易日。
3. `TheoryAgent` 以本地书籍中的单根、两根、三根、趋势、量价、订单流和多周期理论作为候选假设，不直接产生可交易规则。
4. `MemoryAgent` 按70%训练、15%验证、15%测试顺序切分；任何远期标签必须在自身集合内成熟，跨边界样本净化丢弃。
5. `CrossStockAnalogAgent` 使用最多96只市场样本的38维复权OHLCV路径生成共享候选；目标训练期使用带隔离期的时序样本外预测，验证和测试使用目标训练期末冻结模型。
6. `AdaptiveRegimeAgent` 用20/60/120日因果趋势信噪比形成状态观测；`BayesianFilteredRegimeAgent` 用Jeffreys-Dirichlet转移后验做因果隐藏状态过滤。后者只作为挑战者，不能因状态更平滑就自动晋级。
7. `ValidationGate` 用训练/验证方向、非重叠路径、尾部风险、稳定性和规则级DSR/PBO代理接受、降级或拒绝每条规则；代理指标不会冒充精确闭式PBO。
8. `SignalAgent` 只在训练集拟合组合阈值。在线记忆先由已成熟验证事件初始化；进入测试后，仅已成熟测试事件可影响更晚日期，测试结果不能反向选择规则、阈值、持有期或候选链。
9. 同一规则同日多频触发可进入“跨频相关证据池化”挑战者：训练/验证触发图估计有效独立证据数，相关调整二范数只控制仓位强弱，不取消原始触发；无实质增益即拒绝。
10. `StrategyMultipleTestingAudit` 对去重后的策略净值路径计算相关调整试验数、非正态修正的Deflated Sharpe概率和8区块70条组合路径的CSCV-PBO；该审计不使用测试集。
11. `ModelScopeGuard` 对共享规则、贝叶斯状态、证据池化等模型范围变化做训练/验证配对区块自助法。即使无退化，若没有配对区块实质增益也不能增加复杂度。
12. `HoldingWindowSelector` 要求5/10/20/60日任何挑战者都击败全局最优的无共享规则基线冠军；测试集不参与持有期排序或接受。
13. `GPTStudyAgent` 输出结构化补丁；`GPTPatchEngine` 将保留、废弃、条件拆分、状态限制、冷却期和趋势持有建议转为可执行补丁。最终补丁必须同时通过无退化门和配对区块实质增益门，无效果补丁回退到补丁前冠军。
14. `BacktestAgent` 在T日收盘产生信号、T+1前复权开盘代理成交，检查停牌、零成交、涨跌停、滑点、费用、冷却和年度信号预算；`ReviewAgent` 最后揭盲测试集并输出失败案例和接受/拒绝原因。

### 9.0 同类学习与自主进化增量

15. `CohortWyckoffLearningAgent` 只在目标股票训练集截止日上冻结行业、板块、流动性、波动率、趋势和上市时长标签，再从本地数据库选择最多10只同类公司；同类公司的远期收益也必须在该训练边界前成熟。
16. Wyckoff 结构层识别 Spring、Upthrust、SOS、SOW、LPS、LPSY、买卖高潮、吸筹与派发等价量结构；它们只作为新候选，不覆盖书籍规则，也不能绕过原有训练/验证门。
17. `CohortContextEvolver` 生成“严格跨股共识、个股与同类平衡、行业情境分支”三个预声明候选，使用目标训练/验证证据和训练边界内同类证据评分；测试集保持封存。
18. 情境记忆以单条结构化笔记保存，检索最多五条相关记录；新增笔记按 `add/skip/replace/branch` 处理语义冲突，单条分支最多精炼三次、单个情境档案最多120条，防止信息噪声和提示词膨胀。
19. 深度模式中的 `FullFiveStatePositionChallenger` 可以在0%、25%、50%、75%、100%五档仓位之间完整搜索，不再只能跟随旧仓位；但它仍须通过旧冠军对照、训练/验证无退化和配对区块实质增益门。失败时逐字段恢复旧规则、旧信号、旧仓位和旧净值曲线。
20. GPT只读取最多五条相关情境记忆并提交结构化补丁；本地规则引擎、同类证据和回测门决定是否接受，GPT不能读取测试标签或直接覆盖正式策略。

架构借鉴 [Arayasouren/wyckoff_agent](https://github.com/Arayasouren/wyckoff_agent) 公开的 Predict-Critique-Reflect-Evolve、情境记忆和跨股票验证思想；本模块为独立实现，不复制其闭源 Wyckoff 核心服务。
## 研究依据

- 浙商证券《基于技术分析的股票择时研究》：参数优化可能改善样本内却恶化样本外，因此本模型不以测试集回调参数。<https://www.stocke.com.cn/plat_files/upload/trans_file_upload/20150615/1573042253048.pdf>
- 渤海证券《技术形态选股策略研究》：K线形态必须与量、价、时、空和上下文结合，经典底部形态在A股并非普遍有效。<https://pdf.dfcfw.com/pdf/H3_AP201812271279802490_1.pdf>
- 广发证券《125个经典技术指标的量化检验》：趋势型市场中单一反转指标较弱，组合投票及降低调仓频率更稳健；本模型据此保留组合、成本和换手评估。<https://www.sdyanbao.com/detail/90684>
- Bailey与López de Prado的Deflated Sharpe研究：多次试验会抬高最优夏普，需要按试验数、偏度和峰度修正。<https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Bailey等人的回测过拟合概率研究：用组合对称交叉验证观察样本内赢家在样本外的排名翻转；本模型在策略层实现8区块CSCV审计。<https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
- Hamilton状态切换模型与Adams-MacKay在线变化点后验提供了“状态不应由单日硬标签决定”的方法依据；本模型使用因果Dirichlet转移过滤，但不声称实现了原论文的完整模型。<https://ideas.repec.org/a/ecm/emetrp/v57y1989i2p357-84.html> <https://arxiv.org/abs/0710.3742>
## 输出产物

默认输出到：

- `output/kline_memory_learning/<股票代码>_<截止日期>/`

文件：

- `learned_kline_result.json`：完整结构化结果。
- `learned_kline_notes.txt`：一行一个学习后的指标或K线逻辑，写清怎么走、怎么判断、证据和状态。
- `learned_rules.csv`：每条规则在训练、验证、测试和全样本上的表现。
- `trades.csv`：买卖点交易记录。
- `equity_curve.csv`：净值曲线。
- `kline_trade_marks.svg`：K线收盘价曲线和买卖点标记图。

## 约束

- 不保存任何API密钥、账号、密码或token。
- 不把GPT输出当作唯一真相，必须保留可复现的数据证据、样本切分和回测结果。
- 所有候选理论都必须经过历史事件卡、训练验证测试拆分和失败样本回顾。
- 默认不写数据库，只有显式 `--write-db` 才向 `kline_feature_daily` 写入规则摘要。
## 公网部署模式

`single_stock_analyzer.py` 支持 `--serve`，会启动一个无第三方依赖的HTTP服务：

- 公网入口由远程反向代理独立配置，模型服务仍绑定独立本地端口。
- 默认绑定 `127.0.0.1:8877`，适合再由远程电脑上的 Tailscale Funnel 或其他反向代理单独暴露。
- 页面必须先登录；GPT API 只在远程服务端环境变量或远程本地 secrets 文件中读取，不进入网页、URL或API响应。
- 网页中的个股和截止日期都从数据库接口下拉加载：`GET /api/stocks` 与 `GET /api/dates?code=<ts_code>`。
- 网页提交股票代码和截止日期后创建异步任务，任务完成后提供 `learned_kline_notes.txt`、`learned_rules.csv`、`learned_kline_result.json`、`trades.csv`、`equity_curve.csv`、`kline_trade_marks.svg` 下载。
- 服务模式强制 `write_db=False`，不会通过公网入口写数据库。
- 部署时必须使用独立端口和独立公网路由，不复用或停止既有监控端口。
