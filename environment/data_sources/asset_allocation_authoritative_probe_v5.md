# 资产配置 v5 权威数据可得性审计（只读、低额度）

审计时间：2026-08-11（Asia/Shanghai）
审计范围：Wind → iFinD → RQData；同时核对本地 `research_warehouse.db` 与上游 `G:/subject/main/database/database.db`。
审计纪律：未把账号、密码、token、license 或连接串写入命令、文件、日志；未做批量付费取数；未改动数据库、模型或生产快照。

## 1. 结论

当前数据链路仍不满足 D3 生产准入，不能把 v5 标记为“数据完整”或切换生产。主要原因不是四类 ETF 都没有历史，而是：

1. Wind 当前进程没有安全注入的账号环境变量，无法在本次会话对四个目标研究序列做“精确代码 + 当前权限 + 末 5 行”复核；现有 Wind 样本是 2026-07-06 的历史证据，而且并非本项目四个目标代码。
2. iFinD SDK 在远程机器已安装，但当前进程没有安全凭证通道；用户在对话中提供的 API token 已按其自身日期信息过期，不能据此宣称当前 API 可用。官方文档只能证明函数存在，不能证明目标指标 ID 和账户权限。
3. 远程 RQData 3.4.1 已通过现有环境许可完成 `rqdatac.init()`；官方宏观因子目录也可读取。但在进一步执行明确代码元数据探针时，审批传输失败并被系统拒绝，因此本轮没有伪造五行样本，也没有继续绕过审批。
4. 本地上游 `fund_daily` 实际已有 `511010.SH` 和 `518880.SH` 的 2013—2026 历史；v5 看到“仅 31 行”是仓库 2026-07-06 后未重建造成的 ETL 陈旧，不是上游没有历史。先修复这处本地链路，可以立即把四 ETF 共同历史从 2 个月扩展到约 2020-01 至 2026-06，但仍只是执行代理，不等于权威研究基准和总收益口径已 D3。
5. 周期因子尚不全。当前本地宏观表只有 PMI、CPI、PPI、M1、M2、社融等 9 个主字段，且没有 `info_date/release_time/available_time/vintage/revision`；基钦所需真实库存与需求、朱格拉所需产能利用率/制造业投资/工业利润、以及中国美林所需 DR007、10 年国债收益率和估值风险偏好仍未形成可回放的 PIT 因子库。

因此当前应保持 `blocked/research_only`。可以做影子研究，但不能声称因子完备、数据 D3、夏普已由权威数据验证或生产可交易。

## 2. 数据源访问状态

| 优先级 | 数据源 | 本轮证据 | 状态 | 可用于生产吗 |
|---|---|---|---|---|
| 1 | Wind SQL | 本地连接器要求 `WIND_SQL_UID/WIND_SQL_PWD`；当前进程只发现变量名 `windir`，未发现安全注入的 Wind 认证变量 | 当前认证阻塞 | 否 |
| 1 | Wind SQL 历史证据 | 2026-07-06 的 3 行探针证明 `AINDEXMEMBERS`、`AINDEXCSI800WEIGHT`、`ASHARESTRANGETRADE` 当时可查，且有 `OPDATE/OPMODE` | 过期且非目标序列 | 否 |
| 1 | Wind EDB | 当前没有已登录终端/API 安全会话；现有项目状态也记载 EDB 未登录 | 阻塞 | 否 |
| 2 | iFinD | 远程 Anaconda 中 `iFinDPy` 已安装；官方 `THS_DS/THS_EDB/THS_DataStatistics` 文档存在；当前无安全运行时凭证 | D1 文档级 | 否 |
| 3 | RQData | 远程存在 `RQSDK_LICENSE/RQDATAC_CONF` 变量名，`rqdatac 3.4.1` 初始化成功；官方宏观目录 4,007 项可读 | D2 认证/目录，未完成目标五行样本 | 否 |
| 4 | 本地上游 SQLite | `fund_daily`、`future_market_daily`、三张宏观表可 mode=ro 读取 | D2 交叉核验 | 否；缺 PIT、版本与一手权威代码 |

远程主机只读状态：`DESKTOP-I22B489`，用户 `desktop-i22b489\admin`，console 会话运行中。远程环境只输出了相关变量名，没有输出任何值。

## 3. 四类资产的精确数据合同与当前可得性

### 3.1 权益

权威研究目标必须是人民币沪深 300 全收益指数，而不是价格指数。

- Wind 元数据：`wande.dbo.AINDEXDESCRIPTION`
  - 关键字段：`S_INFO_WINDCODE, S_INFO_NAME, INCOME_PROCESSING_METHOD, OPDATE, OPMODE`
  - 必须先筛选并确认 `INCOME_PROCESSING_METHOD=全收益指数`，不能把 `000300.SH` 价格指数冒充总收益。
- Wind 行情：`wande.dbo.AINDEXEODPRICES`
  - 关键字段：`S_INFO_WINDCODE, TRADE_DT, S_DQ_CLOSE`；手册还列有 OHLC、涨跌幅、成交量额。
- 当前精确 Wind 全收益代码：未完成当前权限探针，继续保持 `PENDING`，不猜测。
- RQData 交叉核验：`get_price('510300.XSHG', adjust_type='pre')` 与 `fund.get_nav('510300', fields=['acc_net_value','unit_net_value'])`；本轮仅确认 API 存在，未取样。
- 本地执行代理：`510300.SH`。
  - 上游 `fund_daily`：3,431 行，2012-05-04—2026-06-30；`close` 缺 8 行。
  - 仓库 `etf_ohlcv_daily`：3,412 行，2012-05-28—2026-06-30。
  - 上游累计/复权净值字段各缺 3,420 行，因此当前本地 `close` 不能直接宣称总收益。

状态：执行价格历史可用；权威全收益研究序列仍为 D1/D2，不是 D3。

### 3.2 国债

权威研究目标必须是中国政府债财富/总收益指数，收益率曲线不能替代资产收益序列。

- Wind 行情：`wande.dbo.CBONDINDEXEODCNBD`
  - 关键字段：`S_INFO_WINDCODE, TRADE_DT, S_DQ_CLOSE, AVGMVDURATION`。
  - 该手册没有给出能可靠区分“国债财富指数”的描述表；必须通过 Wind 代码浏览器或小样本查询确认代码、名称、口径和基期。
- 当前精确 Wind 国债财富代码：未完成当前权限探针，保持 `PENDING`。
- RQData 辅助：`get_yield_curve(..., tenor='10Y')` 官方说明为 2002 年至今中债国债收益率曲线；它只用于久期/实际利率因子，不能充当国债总收益。
- 本地执行代理：`511010.SH`（国泰上证 5 年期国债 ETF）。
  - 上游 `fund_daily`：3,229 行，2013-03-05—2026-06-30；`close` 缺 7 行。
  - 仓库 `etf_ohlcv_daily`：仅 31 行，2026-05-18—2026-06-30。
  - 上游累计/复权净值字段各缺 3,210 行，原始收盘价仍不等同于含分红总收益。

状态：存在明确 ETL 历史缺口；先补仓库，再用 Wind 财富指数/RQData 复权序列交叉核验。

### 3.3 人民币黄金

- Wind 基本资料：`wande.dbo.CGOLDSPOTDESCRIPTION`
  - 字段：`S_INFO_WINDCODE, S_INFO_CODE, S_INFO_NAME, S_INFO_EXCHMARKET, S_INFO_PUNIT`。
- Wind 行情：`wande.dbo.CGOLDSPOTEODPRICES`
  - 字段：`S_INFO_WINDCODE, TRADE_DT, S_DQ_OPEN/HIGH/LOW/CLOSE, S_DQ_AVGPRICE, S_DQ_VOLUME, S_DQ_AMOUNT, S_DQ_OI, DEL_AMT`。
- RQData 官方精确代码：`AU9999.SGEX`；`get_price` 可取上金所现货行情，`get_spot_benchmark_price` 可取早/午盘基准价。
- 本地执行代理：应统一为 `518880.SH`，避免研究序列继续写成另一只黄金 ETF。
  - 上游 `fund_daily`：3,146 行，2013-07-18—2026-06-30；`close` 缺 6 行。
  - 仓库 `etf_ohlcv_daily`：仅 31 行，2026-05-18—2026-06-30。
  - 兼容代理 `159934.SZ` 在仓库有 2,830 行（2013-12-16—2026-06-30），但不应因为仓库更完整而改变已声明的执行映射。

状态：RQData 代码已由官方文档确认；Wind 当前权限与五行样本未确认；仓库 518880 历史应补齐。

### 3.4 非黄金商品

不存在一个可以不审计就直接使用的“商品 ETF=全市场商品总收益”替代品。权威做法应从非黄金商品期货构建可复核指数。

- Wind 合约资料：`wande.dbo.CFUTURESCONTPRO`
  - 字段：`S_INFO_WINDCODE, S_INFO_CODE, S_INFO_NAME, S_INFO_LISTDATE, S_INFO_DELISTDATE, S_INFO_EXNAME, S_INFO_CEMULTIPLIER, S_SUB_TYPCODE, CONTRACT_ID`。
- Wind 主力/月合约映射：`wande.dbo.CFUTURESCONTRACTMAPPING`
  - 字段：`S_INFO_WINDCODE, FS_MAPPING_WINDCODE, STARTDATE, ENDDATE, CONTRACT_ID`。
- Wind 行情：`wande.dbo.CCOMMODITYFUTURESEODPRICES`
  - 字段：`S_INFO_WINDCODE, TRADE_DT, S_DQ_PRESETTLE, S_DQ_OPEN/HIGH/LOW/CLOSE, S_DQ_SETTLE, S_DQ_VOLUME, S_DQ_AMOUNT, S_DQ_OI, S_DQ_CHANGE, S_DQ_OICHANGE, FS_INFO_TYPE`。
- RQData 官方规则：主力连续为 `UnderlyingSymbol+88`、指数连续为 `UnderlyingSymbol+99`；生产研究仍应使用真实合约、T-1 换月信号和自融资收益，而不是直接把连续合约点位当总收益。
- 本地 25 个预声明非黄金品种均有真实合约日行情；覆盖如下：

| 品种 | 首日 | 末日 | 行数 | 合约数 | 结算价缺失 |
|---|---:|---:|---:|---:|---:|
| AL | 2001-01-02 | 2026-06-12 | 71,457 | 317 | 884 |
| BU | 2013-10-09 | 2026-06-12 | 40,933 | 164 | 0 |
| C | 2004-09-22 | 2026-06-12 | 31,632 | 135 | 0 |
| CF | 2004-06-01 | 2026-06-12 | 34,520 | 148 | 0 |
| CU | 2001-01-02 | 2026-06-12 | 72,122 | 317 | 404 |
| EB | 2019-09-26 | 2026-06-12 | 19,052 | 86 | 0 |
| EG | 2018-12-10 | 2026-06-12 | 21,437 | 96 | 0 |
| HC | 2014-03-21 | 2026-06-12 | 35,537 | 155 | 0 |
| I | 2013-10-18 | 2026-06-12 | 36,690 | 159 | 0 |
| J | 2011-04-15 | 2026-06-12 | 43,962 | 189 | 0 |
| JM | 2013-03-22 | 2026-06-12 | 38,414 | 167 | 0 |
| L | 2007-07-31 | 2026-06-12 | 54,951 | 236 | 0 |
| M | 2001-01-02 | 2026-06-12 | 47,183 | 203 | 0 |
| MA | 2014-06-17 | 2026-06-12 | 33,598 | 144 | 0 |
| NI | 2015-03-27 | 2026-06-12 | 32,574 | 143 | 0 |
| P | 2007-10-29 | 2026-06-12 | 54,240 | 233 | 0 |
| PP | 2014-02-28 | 2026-06-12 | 35,789 | 157 | 0 |
| RB | 2009-03-27 | 2026-06-12 | 49,869 | 213 | 0 |
| RU | 2001-01-02 | 2026-06-12 | 59,622 | 264 | 224 |
| SC | 2018-03-26 | 2026-06-12 | 39,590 | 114 | 0 |
| SR | 2006-01-06 | 2026-06-12 | 37,174 | 127 | 0 |
| TA | 2006-12-18 | 2026-06-12 | 56,428 | 244 | 0 |
| V | 2009-05-25 | 2026-06-12 | 49,570 | 213 | 0 |
| Y | 2006-01-09 | 2026-06-12 | 39,542 | 170 | 0 |
| ZN | 2007-03-26 | 2026-06-12 | 55,917 | 239 | 0 |

上述本地数据源为 Tushare，仅可作为 Wind/RQData 的交叉核验。`AU` 和 `AG` 数据虽存在，但必须在商品指数品种池、映射表、权重表和结果审计中明确排除；黄金权重恒为 0。

现有三只执行 ETF 上游覆盖：

| 代码 | 语义 | 行数 | 覆盖 |
|---|---|---:|---|
| `159980.SZ` | 有色金属期货 | 1,579 | 2019-10-24—2026-06-30 |
| `159981.SZ` | 能源化工期货 | 1,563 | 2019-12-13—2026-06-30 |
| `159985.SZ` | 豆粕期货 | 1,592 | 2019-09-24—2026-06-30 |

三 ETF 等权篮子只允许作为 2020 年后的执行代理；不能替代长期、含抵押品收益和换月损益的商品总收益指数。

## 4. 五类周期因子完整度

### 4.1 基钦周期：当前不完整

可精确获取的 RQData 因子目录项：

- `制造业采购经理指数PMI_生产`
- `制造业采购经理指数PMI_新订单`
- `制造业采购经理指数PMI_采购量`
- `制造业采购经理指数PMI_原材料库存`
- `制造业采购经理指数PMI_产成品库存`
- `制造业采购经理指数PMI_供应商配送时间`
- `社会融资规模_新增贷款(人民币)_当月值`

这些只能形成调查型库存/需求代理。真实“工业企业产成品存货同比、营业收入/利润、库存绝对量”没有出现在本轮 RQData 4,007 项目录检索和本地宏观表中；Wind/iFinD 的精确 EDB ID 也未验证。因此当前不能宣称基钦四象限已由真实库存链闭环。最低准入应为：真实库存同比 + PMI 库存 + PMI 新订单/生产 + 价格/利润，且每项保存 `info_date/vintage`。

### 4.2 朱格拉周期：当前不完整

可精确获取或已存在的候选：

- RQData：`固定资产投资完成额(不含农户):累计同比:月`
- RQData：`房地产开发投资总额:累计同比:月`
- RQData：`金融机构境内中长期贷款(人民币)_月末数`（模型中需做同比/趋势，不直接用水平）
- RQData：`工业企业景气指数:当期值:季`
- 本地：`macro_quarterly_cn.gdp_yoy/secondary_industry_yoy`

缺口：制造业投资、设备投资、工业产能利用率、工业利润、资本开工/竣工与企业中长贷流量尚未形成权威 PIT 面板。没有这些字段时，不应把朱格拉退化成单一固定资产投资同比。

### 4.3 中国版美林时钟：仅核心骨架可得

| 维度 | 可用精确接口/字段 | PIT 状态 |
|---|---|---|
| 增长 | PMI 生产/新订单/综合、固定资产投资、GDP | RQ `econ.get_factors` 返回 `info_date,start_date,end_date,value,rice_create_tm`；需实测修订 |
| 通胀 | `居民消费价格指数CPI_当月同比(上年同月=100)`、`工业品出厂价格指数PPI_当月同比_(上年同月=100)` | 同上 |
| 信用 | `社会融资规模_当月值`、新增人民币贷款；`econ.get_money_supply` 的 M1/M2 同比 | 货币供应 API 有 `info_date/effective_date` |
| 流动性/实际利率 | `get_yield_curve(10Y)`、`get_interbank_offered_rate(..., fields='1W')`、`econ.get_reserve_ratio` | 日频市场数据可因果对齐；本地没有 DR007 |
| 估值/风险偏好 | 权益估值、股债相对强弱、波动率/回撤、信用利差代理 | 尚未进入当前宏观 PIT 表 |

因此当前只能称“增长—通胀—信用骨架”，不能称完整的中国五维美林时钟。

### 4.4 普林格周期：价格输入可修复，但研究基准未 D3

普林格状态定义只允许使用债券、权益、非黄金商品三类市场；黄金不参与相位判定。

- 权益：本地 510300 执行价格完整，但权威全收益代码未确认。
- 债券：上游 511010 历史完整，仓库漏载；权威国债财富指数代码未确认。
- 商品：三 ETF 只从 2020 年开始；长历史真实期货可构建，但尚未固定品种池、T-1 换月、抵押品收益、成本和二源误差。

所以普林格概率状态引擎可以继续做 shadow，但不能作为生产 BL 观点的 D3 输入。

### 4.5 康波周期：只能展示

本地中国宏观月度数据从 2001 年开始，无法覆盖多个 40—60 年完整周期。即使补入 GDP、实际利率、商品和技术扩散代理，也只能作为叙事/历史展示层；其对 BL 观点、风险预算和最终权重的贡献必须保持 0。

## 5. 本地宏观表的可用性与 PIT 缺口

`G:/subject/main/database/database.db.macro_monthly_cn`：306 行，2001-01—2026-06；来源统一标记 `tushare+akshare`。

| 字段 | 非空数 | 首月 | 末月 | 空值数 |
|---|---:|---:|---:|---:|
| `pmi_manufacturing` | 222 | 2008-01 | 2026-06 | 84 |
| `pmi_non_manufacturing` | 222 | 2008-01 | 2026-06 | 84 |
| `pmi_composite` | 147 | 2014-04 | 2026-06 | 159 |
| `cpi_national_yoy` | 305 | 2001-01 | 2026-05 | 1 |
| `ppi_yoy` | 305 | 2001-01 | 2026-05 | 1 |
| `m1_yoy` | 305 | 2001-01 | 2026-05 | 1 |
| `m2_yoy` | 305 | 2001-01 | 2026-05 | 1 |
| `sf_inc_month` | 293 | 2002-01 | 2026-05 | 13 |
| `sf_stock_endval` | 142 | 2002-12 | 2026-05 | 164 |

表结构只有 `month,...,source,ingest_time,batch_id`，没有发布日期、可得时间、观察期、修订版次或历史 vintage；306 行的 `ingest_time` 全部为同一批次。它不能用于因果历史回放。`macro_rate_cn` 只有 Shibor（2018-05 起）和 LPR（2013-10 起），没有 DR007 和国债收益率曲线。

RQData 是当前最现实的 PIT 补充路径：

- `econ.get_factors`：返回 `factor, info_date, start_date, end_date, value, rice_create_tm`。
- `econ.get_money_supply`：返回 `info_date, effective_date, m0/m1/m2, m0/m1/m2_growth_yoy`。
- `econ.get_reserve_ratio`：返回 `info_date, effective_date, reserve_type, ratio_floor, ratio_ceiling`。
- `get_yield_curve`：官方说明为 2002 年至今中债国债曲线。

但 `rice_create_tm` 不自动等同于完整修订 vintage。正式入库时仍须按每次抓取时间追加保存原始响应并做修订差分，不能覆盖旧版本。

## 6. 必须按顺序执行的低额度补数 SOP

1. **先修本地 ETL，不调用付费接口。** 对 `511010.SH`、`518880.SH` 及五只已声明 ETF，从上游 `fund_daily` 以原子事务补入仓库；补前后记录行数、首尾日期、空值和哈希。仓库表还缺 `source/ingest_time/batch_id`，应补 lineage 字段或建立独立 source manifest。
2. **Wind 元数据先行。** 仅查表字段、目标候选代码、口径和权限；每张表 `TOP 5`。股票先用 `AINDEXDESCRIPTION.INCOME_PROCESSING_METHOD` 锁定全收益；债券必须由名称/口径锁定国债财富指数；黄金从 `CGOLDSPOTDESCRIPTION` 锁定 Au99.99；商品只查预声明品种和映射。
3. **Wind 行情五行探针。** 每个最终代码只取末 5 行，输出字段、首尾日期聚合、末 5 行、`OPDATE/OPMODE`、查询模板 SHA-256；不得先拉全史。
4. **iFinD 只做二源核验。** 先运行 `THS_DataStatistics()` 记录额度；在 SuperCommand 生成精确 `THS_DS/THS_EDB` 指标 ID 后，每序列最多 5 个观察；保存 `errorcode,dataVol,time,indicators`，再记录调用后额度。未生成 ID 前禁止猜 ID。
5. **RQData 补 PIT 宏观。** 先对上述白名单因子分别取最多 5 个发布观察，验证 `info_date<=回测可得日`、观察期和修订行为；通过后按月增量追加，不做一次性全库导出。
6. **三源一致性门禁。** 资产日收益在共同交易日比较；宏观比较同一观察期和同一 vintage。代码、币种、单位、复权/全收益口径、发布日期任一不一致即阻塞，不做静默填补。
7. **达到 D3 后才重跑模型。** D3 要求：当前权限成功、精确代码、末 5 行证据、历史覆盖、PIT/修订、二源误差、查询哈希、更新频率与失败告警全部齐全。

## 7. 本次证据与复核哈希

只读 SQL 模板 SHA-256：

- 上游 ETF 覆盖：`53f63a4a14ac41dfb2cca33fd295aa5a2f8f703dace08c43bdbc1ff7f7f9cb29`
- 仓库 ETF 覆盖：`57acc2664abcede3be9aed351d64d3614c17705c2ecada0f26c0a490892bd8bc`
- 宏观覆盖：`4b16763a7ff815fb31578f65c192e1f9388051adcb5ff481958f7774c4351cea`
- 商品期货池：`fe87e28a09fbd5e3d1f7b9e1f7211c51e5877b8b183733e23779b955ccd6198b`

历史 Wind 证据文件：

- `wind_sql_probe_result.json`：`D288CBCEC9D29B03D6D51411D18FCE2C84C4DB65C814FF46C8F9BD09334A8370`
- `wind_index_description_probe.json`：`B097940AA5144D1D52BDC297F15F4ADB2B360F810D5DF836E9CE576AEEA95CB7`
- `wind_major_event_probe.json`：`9E84493E1979B47A6FD148F1D714E4EB78699F59A9878B5719C0A21D9A5A2DC6`
- `asset_series_registry_v5.json`：`7F8FB6835A2A6A08A11B8D57621D880DABCFCC98C1F1D933211E9F21265182C1`

本地文档依据：

- `environment/data_sources/wind_quant_db_v445.txt`：A 股指数 4.91、期货映射 4.187、商品期货 4.193、黄金现货 4.194/4.195、中债指数 4.245。
- RQData 官方文档：`https://www.ricequant.com/doc/rqdata/python/`、`macro-economy`、`generic-api`、`spot-goods.html`。
- iFinD 官方文档：`https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html`。

## 8. 审计最终判定

- 四资产执行代理历史：**可修复/可做影子研究**。
- 四资产权威研究基准：**未 D3**。
- 基钦：**真实库存链缺失**。
- 朱格拉：**产能、制造业投资、利润链缺失**。
- 中国美林：**增长/通胀/信用骨架存在，流动性和估值风险偏好未闭环**。
- 普林格：**三市场定义正确，输入研究基准未 D3**。
- 康波：**仅展示、零权重**。
- 生产发布：**继续阻塞，不允许用短期回测或高夏普绕过数据门禁**。
