# 06 多周期资产配置

本目录是资产配置的唯一模型实现位置；网页只消费离线研究快照，不在请求路由中重复拟合或选参。

## 文件职责

- `asset_allocation_engine.py`：点时宏观因子、普林格/基钦/朱格拉/康波/美林状态、多期限趋势后验、风险预算、全天候、HMM、HRP、稳健 Black-Litterman、因果滚动回测与质量门禁。
- `build_snapshot.py`：离线快照入口；读取本地研究仓库，并以免费低频接口补齐四只可交易ETF的连续复权总收益历史。
- `test_asset_allocation_engine.py`：周期定义、因果信号、数据合并、权重投影、风险预算、HMM、等权主动指标、v4候选网格、后验概率和漂移后换手测试。
- `README.md`：模型口径、日常SOP与维护边界。

## v4 生产口径

1. 普林格标准路径固定为 `100→110→111→011→001→000`；`101/010` 为冲突/过渡态，置信度折减，不新增伪周期。五类周期都输出逐因子轨迹、完整阶段定义和逐月历史总图。
2. 信号月只使用当月及以前数据，月末目标配置下一月收益。每次再平衡先用当月收益把上期目标漂移成实际持仓，再计算换手和10bp单边成本；等权基准使用完全相同的ETF、漂移规则、频率与成本。
3. 四资产代理固定为 `510300.SH` 权益、`511010.SH` 债券、`518880.SH` 商品、`511880.SH` 现金。债券、商品、现金使用Sina日线收盘与累计分红重建总收益；所有资产和公共月历必须逐月连续，任何断月、重复日期或异常收益都会阻断快照上线。
4. 权益偏好通过45%战略资本先验和更高权益风险预算表达，不再用35%硬下限伪造偏好；预先声明的10%危机下限允许模型在弱趋势、高相关或低扩散环境中真实降风险。
5. 推荐模型从48个预先声明规格中选择：1/3/6或1/3/6/12月风险调整趋势经Logistic映射为后验概率，再结合横截面相对强弱、波动、相关性、扩散度、逆波动稳定袖套、权益防守转移和带置信度的宏观周期倾斜，投影到画像约束可行域。
6. 选模采用非补偿式流程：训练期先过绝对收益、主动收益、分段稳定性、相对回撤与换手门槛；验证期再过绝对收益、年化超额、信息比率和相对回撤门槛并定型；测试期完全不参与排名，只作封印后的报告。
7. 回测同时报告全样本、训练、验证、测试、等权主动收益、信息比率、最大相对回撤、5/10/20/30bp成本敏感性、CSCV-PBO与去偏夏普概率。不能以提高测试收益为理由事后修改候选网格或测试边界。
8. 风险平价、全天候、HRP、宏观风险预算、稳健BL、HMM与周期风险预算保留为独立对照。康波只输出低置信度结构情景；LLM只生成带引用的解释报告，不能覆盖确定性权重。

## 日常 SOP

```powershell
python -m unittest discover -s .\model\asset_allocation -p 'test_*.py' -v
python .\model\asset_allocation\build_snapshot.py --database .\database\research_warehouse.db --output .\board\quant_strategy_agent\data\asset_allocation_snapshot.json
node --check .\board\quant_strategy_agent\static\js\app.js
```

只有在 `quality.status=passed`、全部单元测试和网页回归通过后，才允许原子替换生产快照。付费数据源只可通过服务端环境变量适配；严禁将账号、口令、token、许可证或模型API密钥写入仓库、快照、日志或浏览器响应。

## v4.2 审计口径

- 执行约束仍以目标权重相对漂移后持仓的 L1 变化计算。对外换手率按 `0.5 × L1` 报告，交易成本参数解释为作用于单边换手的双边费率。
- 候选门槛和换手惩罚继续使用等价 L1 换手，口径调整不会放松原有执行约束。
- 候选榜单披露训练期和验证期稳健得分的逐项贡献、最弱子期超额收益、单边换手和等价 L1 换手。测试集仍只作封印后报告。
- 研究输出应写入 `output/model_improvement/`。只有质量门禁、单元测试和回归测试全部通过后才可替换生产快照。

## v4.4 统计晋级口径

- 正式晋级必须同时通过训练和验证主动证据、CSCV-PBO及95%去偏夏普概率。候选权重和测试期报告可以保留，但测试期高夏普不能修复未通过的统计门。

## v5.2.2 refresh and runtime boundary

Use the formal builder for every refresh:

```powershell
python .\model\asset_allocation\build_snapshot_v522.py --database .\database\research_warehouse.db --output .\board\quant_strategy_agent_vnext\data\asset_allocation_snapshot.candidate.json
```

The refresh script validates the candidate before replacement. It requires schema `5.2.2`, service status `ready`, quality status `passed`, the explicit Sharpe-only authorization, recommended mode `benchmark_relative`, the exact 60/15/10/15 internal policy anchor, and an `equal_weight_25` object whose role is NAV-display-only. A failed candidate never replaces the current snapshot.

The runtime preserves legacy snapshots. For schema `5.2.2`, it reports the policy/display benchmark separation, service authorization separately from statistical warnings, all five cycle admission and missing-factor records, and `model_evidence_catalog` when supplied.

## v6.3 真实因子链路口径（2026-08-16）

v6.3 只保留用户指定的两个周期模型和三个配置模型：

1. 周期跟踪只保留「美林时钟」和「普林格周期」。美林使用增长、通胀两轴；普林格使用货币、信用、增长、市场确认四轴，并按六阶段输出资产偏好。每个轴从真实可计算的宏观和四资产市场序列中生成滚动 zscore、1/3/6/12 月变化、HP 滤波、傅里叶低频、分位数和 6 月斜率等候选特征，只用训练窗检验 IC、方向命中率和覆盖率后入模。
2. 资产只保留股票、债券、黄金、商品四类，基准为四资产等权 25%。当前研究面板来自 v553：权益 H00300、债券 H11006、黄金 AU9999、非贵金属商品自融资指数；该面板仍为 D2 研究级，不标生产 D3。
3. 配置模型只保留三类：
   - 周期观点 BL：美林和普林格合成四资产排序，生成股票-债券、黄金-债券、商品-债券三条相对观点，构造 Q/Omega 后进入 Black-Litterman 后验，再做 TE、主动偏离、换手和成本约束求解。
   - 风险平价：独立使用四资产稳健协方差求 ERC，作为低波高夏普风险均衡模型，不读取周期或宏观观点。
   - 宏观因子调整：增长、通胀、利率、信用、汇率、流动性六类筛选因子真实进入 alpha，并以风险平价为风险锚进行约束优化。
4. 推荐模型只用训练期 2018-2019 与验证期 2020-2021 的 Sharpe、超额、IR 和稳定性确定；2022 以后全部为 report-only，不允许反向调参。
5. 真实性边界：v6.3 已完成 D2 实算因子 → 周期阶段 → 资产映射 → BL/宏观调控 → 回测闭环；但宏观 release_time、available_time、vintage/revision、Wind/iFinD/RQ 跨源 hash 尚未闭环，production_admitted_macro_factor_count 必须保持 0。

日常构建：

```powershell
python .\model\asset_allocation\build_snapshot_v63_real_chain_four_asset_cycle_bl_rp_macro.py --output .\board\quant_strategy_agent_vnext\data\asset_allocation_snapshot.json
python -m pytest .\model\asset_allocation\test_asset_allocation_v63_real_chain.py -q
```
