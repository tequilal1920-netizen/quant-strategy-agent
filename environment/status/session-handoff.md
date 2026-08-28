# 会话交接

当前生产版本仍为 `2026.07.23-research-workspace-r16.3`，K 线模型为 `9.0-cohort-wyckoff-evolution`。公网统一入口、账号迁移、27 个二级页面、51 个页内功能、8 个一级 Skill、文件清理和既有 GitHub 公开发布均保持原状态。资金面数据源改造代码已在本地正式仓库完成，39/49 个精确序列已落入正式 SQLite；因剩余 10 个授权序列不可取，尚未生成新快照、提交 GitHub 或部署。

## 已验证状态

- 公网：https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/
- AI 监控：https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/
- 生产切换备份：`F:\apps\quant_strategy_agent\deployment_backups\research_workspace_r16_3_switch_20260723_161838`
- 重组前 Git 标签：`backup/ui-before-redesign-20260723`
- 既有浏览器基线：27/27 页面、51/51 页内功能；控制台错误 0、页面错误 0、溢出 0。
- 资金面源码：`model/liquidity_tracking` 中 5 个新管线模块编译通过，`git diff --check` 通过，无 Excel 数值依赖或凭据字面量。
- 资金面结构：37 图、34 张时间图、3 张分类图、89 条 trace、31,140 个隔离绘图值通过日期、缺失、重复、坐标轴、来源和刻度验证。
- 资金面 Skill：现有 `skill/liquidity-tracking` 已改为数据库工作流并通过官方 `quick_validate.py`。
- 资金面 Word：`G:\中信建投\agent\output\资金面跟踪.docx` 已通过 10 页逐页渲染、15 张表格几何、敏感信息和结构审计。

## 资金面阻塞

- 49 个基础契约中 39 个真实序列已刷新并落库，10 个仍缺失；严格门禁状态为 `blocked`。
- Wind EDB：仅剩 `retail.new_accounts`、`retail.participating_investors`；3 个融资担保字段已由中证数据官方月度文件补齐。
- 私募已补齐：`private.stock_long_position` 来自华润信托 CREFI 官方月报；三条指数增强、市场中性、CTA、套利来自 Wind 私募净值等权样本。
- EPFR：A/H 累计配置、主动/被动/合计流量、三类全球基金 A 股仓位，共 8 条。
- 当前 Wind 终端未登录，iFinD EDB 额度耗尽，浏览器会员会话控制工具不可用；米筐不提供同口径授权数据。禁止用相似指标替代。
- 真实缓存构建已验证会在写快照前失败；生产快照、公网版本和导航/UI 均未改。

## 2026-07-25 本地模型升级

- 统一绩效统计已接入资产配置、组合优化、因子实验室、LLM 因子和框架回测；夏普不再使用 CAGR/波动率，重叠 IC 使用 HAC。
- V4 审计禁止跨 V3 运行和 test/full 复用；因子目录使用同一正式评估记录并保留 RankIC 正负号。
- 资产配置 B06 修正后测试年化 11.00%、夏普 1.271、回撤 -9.88%；组合优化 C188 测试年化 12.22%、夏普 2.438、回撤 -1.94%，多目标筛选的 PBO 为 12.86%。
- 行业月频保持原 C6；周频测试年化 -3.15%→-2.95%、超额夏普 -0.436→-0.394、回撤 -38.60%→-38.05%、换手 11.585→11.539。复杂景气/趋势/拥挤候选因训练期失败未晋升。
- K 线嵌套验证已从硬编码关闭改为按正式候选数启用；000001.SZ 实跑完成，验证失败时保持观察保护，测试期不参与选择。
- 不确定性 BL 挑战者未改善选择且增加多重试验负担，已从代码撤回。
- 47 项测试、行业合同、Python 编译与差异检查通过。隔离证据在 `output/model_improvement/`；未覆盖生产快照，未改左侧标题，未部署。

## 下一步

1. 取得可调用的 Wind EDB 登录态以及 EPFR 程序化授权；私募与融资担保不再需要额外代理源。
2. 从 2010-01-01 或接口最早日期刷新 `database/liquidity_tracking.sqlite3`，运行严格审计直至 49/49。
3. 生成唯一正式快照并完成 37 图本地与公网逐页 QA。
4. 49/49 后同步更新 Word 中的阻塞状态并重新执行渲染检查。
5. 敏感信息扫描通过后再提交/推送 GitHub，保存新发布版本并部署。

## 文件约束

- 11.9GB `database/research_warehouse.db` 及 WAL/SHM 只能原地保留，不复制、不移动、不提交。
- `copy/previous_version_20260721` 是唯一重组前源码副本，约 1.7MB，被 Git 忽略。
- `G:\中信建投` 根目录正式 Word/Excel 文档全部保留，不提交公开仓库。
- 数据库、输出、缓存、私密环境、测试 SQLite、合成快照和部署 ZIP 不得提交。

## 2026-07-26 模型升级交接


## 2026-07-26 R18 因子实验室生产交接

- 当前生产版本：`2026.07.26-factor-lab-causal-champion-r18.0`
- 当前生产目录：`F:\apps\quant_strategy_agent_research_r18_factor_lab_causal_champion`
- 公网入口：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`
- 回滚备份：`F:\apps\quant_strategy_agent\deployment_backups\research_workspace_r16_3_switch_20260726_172050`
- 因子引擎：`factor-lab/3.2-inverse-volatility-rank-execution`
- 冻结冠军：`adaptive_icir_12m_neutral::continuous_rank_volatility_budget`
- 训练、验证、测试报告期 Sharpe：`0.464 / 1.126 / 2.466`
- 测试报告期：RankIC `0.0498`，年化收益 `27.45%`，最大回撤 `-2.82%`，换手 `0.764`
- 晋升状态：`research_only`。9/10 门禁通过；换手上限 0.65 未通过。测试期只报告，不参与权重、候选或执行策略选择。
- 本轮关键修复：前瞻收益成熟队列；交易成本感知凸优化执行；离池清仓换手；因果自适应成本斜率；逆波动率连续排序诊断；39 候选试验台账。
- UI 已在原有“因子实验室 > 配置策略 > 结果”中展示冠军、选择纪律、三段指标、候选拒绝原因和门禁。一级与二级标题未改。
- 验证：84 项 Python 测试、4 项 JavaScript 语法检查、真实浏览器登录点击、0 控制台错误、远端隔离预检、回滚保护切换和公网 13 接口均通过。
- 正式证据：`output/model_improvement/factor_strategy_inverse_vol_v32_20260726.json`；截图：`output/playwright/factor_strategy_champion_r18.png`。
- 下一步：停止读取当前测试区间继续改造。等待未来影子期或新的预声明横截面后，优先复核能否在验证 Sharpe 不下降的条件下把换手降至 0.65 以下。
- 资产配置 v4.2 已统一单边换手和成本解释，保留原 L1 执行上限并新增逐项评分审计。当前本地研究候选 B12：测试年化 10.95%、夏普 1.345、回撤 -8.57%、超额 2.28%、IR 0.541。
- B12 的 PBO=50%、Deflated Sharpe 概率=63.51%，不满足强统计晋级标准。隔离结果为 `output/model_improvement/asset_selection_audit_final_20260726.json`，不得直接覆盖生产快照。

## 2026-07-26 R20.2 生产交接

- 当前生产版本：`2026.07.26-active-risk-shadow-r20.2`
- 当前生产目录：`F:\apps\quant_strategy_agent_research_r20_2_best_history`
- 公网入口：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`
- 回滚备份：`F:\apps\quant_strategy_agent\deployment_backups\active_risk_shadow_r20_switch_20260726_210615`
- K线主UI只显示训练与验证选出的唯一最优任务 `000333.SZ_20260712_121414_8a2339ab`；测试期不参与选择，原始9条任务仍保留在底层审计记录。
- 指数增强历史表只显示治理冠军，完整模型比较图保留。一级标题、二级页面和工作区布局未改。
- 验证：18项量化主站QA、JavaScript语法、18072隔离预检、公网14接口、真实浏览器零错误全部通过。

- 组合优化 C188 选型与净值未变，新增资产组主动配置、交易成本和实现残差归因。隔离结果为 `output/model_improvement/portfolio_attribution_final_20260726.json`。
- 行业月频和周频均保留 C6，单边换手修复后月频测试年化 0.43%、超额夏普 0.337；周频测试年化 -2.38%、超额夏普 -0.281。周频仍不可晋级。
- 行业正式复现命令必须运行 `model/industry_rotation/build_snapshot.py` 并设置 `INDUSTRY_ROTATION_SOURCE_XLSX`。直接运行 `engine.py` 会因专属字段不足失败，这是预期门禁。
- 结构性挑战模型只有在训练和验证稳健目标胜出时才可保留。本轮组合风险状态、行业缓冲持仓、双时钟周频模型均已撤回，测试期改善未用于选型。
- 117 项回归通过，28 个 Python 文件编译通过，差异检查通过。没有前端文件、一二级标题、生产快照或公网版本变更。

## 下一步

1. 使用新增独立样本或 vintage 数据复核资产配置 B12 的 PBO/DSR，不得用现有测试期继续筛选。
2. 为行业周频补充独立高频经营数据，再重新声明候选族并从训练期开始评估。
3. 组合优化优先研究验证期可识别的上涨捕获机制；任何权益增配规则必须在测试期之外确定。

## 2026-07-26 v4.3 模型升级交接

- 代码版本：资产配置 `v4.3-causal-volatility-budget`；因子实验室 `v2.5-causal-risk-budget-domain`；行业轮动 `v4.3-walkforward-risk-budget`；K 线 `v9.2-dual-momentum-volatility-budget`；组合优化 `v2.3-return-loss-attribution`。
- 资产配置 B06 测试年化 11.78%、夏普 1.522、回撤 -6.32%、超额 3.05%、IR 0.717。验证期绝对收益接近零，且测试期并非全新独立样本，只能作为本地诊断。证据：`output/model_improvement/asset_volatility_budget_20260726.json`。
- 因子 OLS 加因果波动预算测试年化 23.91%、夏普 1.171、回撤 -7.99%。测试 RankIC、命中率、衰减和换手门槛失败。证据：`output/model_improvement/factor_strategy_domain_risk_v25_20260726.json`。
- LSTM 已真实运行 3352 秒，验证夏普 0.467，测试夏普 -2.090、回撤 -70.46%，不晋级。证据：`output/model_improvement/factor_lstm_fallback_20260726.json`。
- 行业新增景气稳定中心和风险预算候选均未晋级，继续使用 C6；月频测试夏普 0.120，周频 -0.025。证据：`output/model_improvement/industry_walkforward_risk_20260726.json`。
- K 线相对强弱波动预算候选在 000001.SZ 验证夏普 -0.123，正式门槛拒绝。证据：`output/model_improvement/kline_dual_momentum_000001/learned_kline_result.json`。
- 组合优化 C188 测试夏普 2.438，但超额为负且 DSR 未通过，仍为研究候选。证据：`output/model_improvement/portfolio_attribution_final_20260726.json`。
- 60 项目标回归、15 文件 AST、差异检查和前端不变性检查通过。`templates/`、`static/`、生产快照与公网版本均未改变。

## 下一步

1. 使用新 vintage 或未来影子区间对资产配置 B06 做一次完全独立复核。
2. 因子实验室先处理负 RankIC 和换手预算，不得根据当前测试期提升域 Ridge 或 LSTM。
3. 行业补充高频经营 vintage；K 线扩大预声明股票池后重新做嵌套验证、DSR 和 CPCV。

## 2026-07-26 v4.4 模型升级交接

## 2026-08-04 R29.9 交接

- 当前独立 vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.08.02-kline-multiscale-vnext-r29.9`；任务 `QuantStrategyAgentVNext8090R299`；端口 8090；目录 `F:\apps\quant_strategy_agent_vnext_r29_9_kline_multiscale`。
- 主公网 `/quant-agent/` 仍为 `2026.07.29-trump-research-v3-r27.0`。R29.8 的任务、8089 监听和发布目录仍存在，当前仅作为回滚版本；不要误报已删除，也不要停止 8090。
- K线研究模型为“监督形态多空检验”。训练、验证、封存测试夏普为 `1.595 / 1.720 / -1.971`；测试未参与选择，发布门关闭，页面状态为“研究诊断”。
- 核心源码：`framework/backtest/kline_multiscale_expert.py`、`framework/backtest/kline_supervised_ranker.py`；运行器：`model/kline_memory_learning/run_multiscale_expert_challenger.py`；冻结证据：`output/kline_memory_learning/kline_multiscale_expert_challenger.json`。
- K线UI保持原导航，五层证据由 `board/quant_strategy_agent_vnext/kline_multiscale_visual_backend.py` 和 `research_evidence_backend.py` 提供。三档浏览器无横向溢出、拉条、错误日期或控制台消息。
- 验证：K线单测 `5/5`，vNext `50/50`；公网证据为 5 层、4 可视化块，治理版本 `2026.08.02-kline-multiscale-r29.9`。发布包 SHA-256 为 `AFA84190AD1CE45BD25E1C84354A31A0E78E6DEC9D9995D6005BE752D492C7B8`。
- 下一步不能继续读取已观察封存测试调参。只允许新增未来影子样本或新的未观察验证截面，并先预声明模型与门禁。

- 资产配置代码为 `v4.4-dsr-promotion-guard`。B06 的测试夏普 1.522 不变，但 DSR 概率仅 56.18%，新统计门会返回 `conditional`，不得覆盖生产快照。

## 2026-08-04 R30.0 交接

- 当前独立 vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.08.04-dual-objective-clean-ui-vnext-r30.0`；治理版本 `2026.08.04-dual-objective-clean-ui-r30.0`；任务 `QuantStrategyAgentVNext8091R300`；端口 8091；目录 `F:\apps\quant_strategy_agent_vnext_r30_0_dual_objective_clean_ui`。
- 主公网 `/quant-agent/` 仍为 `2026.07.29-trump-research-v3-r27.0`，发布前后未变化。R30.0 登录态公网验收已通过。
- 所有实际模型页面已从五块改为四块：数据与截面、历史与实时、模型与预测、策略与归因；原理图从活动脚本及证据接口移除。一级、二级导航标题未改。
- 资产配置双目标：战略偏好维持原主动收益门禁；稳健绝对选择 HRP，训练/验证/测试只报告夏普 `1.318/0.712/1.900`，测试不参与选择。被拒绝 PCA 后验 HRP 挑战者未保留。

## 2026-08-11 R33.0 handoff

- Live main public: `https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`; version `2026.08.11-csi500-constrained-optimizer-r33.0`; root `F:\apps\quant_strategy_agent_r33_0_csi500_optimizer`; task `QuantStrategyAgent8094R330`; port 8094.
- Rollback is preserved: port 8071 is still listening and reports `2026.07.29-trump-research-v3-r27.0`. Repoint only the existing `/quant-agent` Funnel path if rollback is required.
- Latest audited run: `run-20260811131658-c5c3b23bc7`; completed/AUDITED; 50 positive weights; weight sum 1; `SCIPY_HIGHS_MILP` certified support followed by `CLARABEL` optimal/certified; max violation approximately `4.14e-13`; no fallback.
- Formal performance publication is blocked by `invalid_incomplete_requested_window`. Do not advertise the diagnostic segment as stable alpha or production Sharpe. Four-strategy formal metrics must remain null until a complete PIT window passes.
- The external LLM was not called. Keep LLM use explicitly user-initiated and do not transmit holdings, scores, constraints or credentials without explicit authorization. Local schema validation is the verified path.
- Final SOP: `G:\????\????.docx`; 14 pages; SHA-256 `131031D1CA5D451ECB531D336B8A5A0B75A860ABC9DA2A7204B36BB584727C5D`.
- Validation baseline: 94 Python tests, JavaScript syntax checks, static navigation checks, authenticated bootstrap and candidate run audit. In-app browser transport was unavailable during final switch; public reachability was independently confirmed by a 302 redirect to the protected login route.
- 历史治理：K线和指数已有最优单条逻辑；主因子页只取一条治理冠军；因子实验室每模型族最多一条完成记录，新远程环境当前为 0 条，没有迁移旧失败历史。
- 回归证据：资产 `23/23`，vNext `50/50`，跨模型 `101/101`。浏览器 1920/1440/1100 无横向溢出、无原理图、无 range 控件。
- 正式包：`dist/quant_strategy_agent_vnext_r30_0_dual_objective_clean_ui_20260804.zip`；86 项；SHA-256 `C59FDCA1059A4718CE7638BEF36EAEC9D9A36805FAC85609CCC142E69CD7D921`。
- 阻塞：远程旧版清理被安全审查单独拒绝。用户必须明确回复“批准清理远程旧版”，才可执行 `cleanup_superseded_vnext_r300_remote.ps1`。目前不要声称 R29.9/R29.8 已删除。
- 清理脚本只允许处理 `QuantStrategyAgentVNext8090R299`、`QuantStrategyAgentVNext8089R298`、8090/8089、两个明确旧目录及远程 Temp 中本次部署文件；R30.0、主公网、8086 不得修改。
- 因子 v2.6 的 PIT 财务、行业市值正交化和缓冲候选均经训练验证选模；原 OLS 冠军保持，测试夏普 1.171。行业价格路径 Top5 候选训练期方向失败，K 线空仓回退不再误报为通过。
- 组合优化 C188 继续为研究候选。高绝对夏普来自低波动，测试主动收益和 DSR 未通过，禁止依据已观察测试期提高权益权重。
- 统一回归 67 项和 4 个 JavaScript 语法检查通过；前端模板与静态资源无差异，未部署。
- 下一次有效提升必须来自新增 vintage、未来影子样本或预声明横截面，不得继续使用当前测试期选参。

## 2026-07-26 v4.5 模型升级交接

- 因子实验室为 `factor-lab/2.7-adaptive-orthogonal-icir`。对称正交化和因果滚动 ICIR 候选只使用成熟标签与非重叠 IC 观测，测试期从未参与权重或候选选择。
- 训练和验证仍选中原 OLS 快慢波动预算。训练、验证和测试夏普为 2.410、0.767 和 1.171；验证 RankIC 为 -0.0438，验证 IC 命中率为 42.7%，晋级状态为 `conditional`。
- 自适应 ICIR 全仓候选训练、验证和测试夏普为 0.735、0.536 和 2.097，训练与验证 RankIC 均为正。因验证夏普未进入一标准误范围，测试高收益不得用于晋级。
- 资产经验置信度候选未进入训练验证前 16 名，且令 DSR 概率降至 38.77%。候选代码和专用运行入口已撤回，隔离输出保留为否定性证据。
- 68 项模型、框架和页面契约测试通过；4 个核心 JavaScript 入口语法通过；未改模板、静态资源、一级或二级标题，未写生产快照，未部署。

## 下一步

1. 对因子正向 IC 候选采用新预声明横截面或未来影子期复核，不得再读当前测试期进行组合或调参。
2. 资产 B06 继续等待独立 vintage；行业与 K 线继续等待新增高频经营数据和横截面样本。

## 2026-07-26 v4.6 模型升级交接

- 行业正式复现源已恢复为 `G:\招银理财\行业景气0507\main\data.xlsx`，`industry_champion_guard_v45_20260726.json` 验证 31×8=248 个 live 专属业务字段。
- 月频研究挑战者 C19 只由训练和验证选出；封存测试相对 C6 的年化超额、超额夏普和最大回撤三项均失败，因此 v4.5 发布门保留 C6。周频训练验证仍直接选择 C6。
- 行业因果专家 ICIR 与困境反转候选均未通过训练验证并已撤回实现。后续不得根据当前测试期继续改造拥挤阈值、TopN 或风险预算。
- K 线 v9.2 已修复最终冠军复用分支的空仓误验收。000001.SZ 的正式结果为 `observe_only_no_validated_strategy`，空仓可保留为安全状态但不得标记为收益改善。
- K 线相对强弱波动预算候选训练、验证、测试夏普为 0.363、-0.123、0.363；验证期拒绝。下一次研究必须先声明横截面股票池和新封存期。
- 70 项单元测试、行业 31×248 合同、4 项 JavaScript 语法及差异检查通过；模板与静态资源无差异，生产快照未覆盖，公网未部署。

## 下一步

1. 获取新的行业经营 vintage 或未来影子样本后再复核 C19，不再读取当前测试期改参数。
2. 为 K 线预声明多股票横截面及独立封存期，完成组合级而非单股级的嵌套验证与多重检验。
3. 因子和资产候选继续等待独立样本，不以测试期高夏普触发自动晋级。

## 2026-07-26 v4.7 模型升级交接

- 行业引擎版本为 `industry-rotation/4.7-common-window-report-momentum`。月频和周频候选现在分别从 2017-10-10 与 2017-02-06 的共同起点比较，禁止再使用候选各自不同的历史起点。
- 月频训练验证唯一挑战者为 C18，封存测试否决后继续使用 C6。C22 研报增强动量训练/验证超额夏普仅 0.201/0.312，报告期 0.190 绝对夏普和 0.389 超额夏普不构成晋级依据。
- 因子引擎版本为 `factor-lab/2.8-fixed-rank-ensemble`。固定 50%/50% 横截面秩集成的全仓训练/验证/测试夏普为 1.604/0.406/2.198；缓冲风险预算版本为 1.645/0.271/2.354。验证不足，正式选择仍为 OLS 快慢波动预算 2.410/0.767/1.171。
- 证据文件为 `output/model_improvement/industry_report_momentum_v47_20260726.json` 与 `output/model_improvement/factor_strategy_fixed_rank_v28_20260726.json`。不得使用已经观察的测试结果继续调整 TopN、拥挤阈值、集成权重或风险预算。
- 73 项 Python 回归、4 项 JavaScript 语法和差异检查通过。前端、一级和二级标题、生产快照及公网版本未改，未部署。

## 下一步

1. 因子固定秩集成与行业 C22 等待未来影子期或新横截面复核；当前测试高夏普仅作为预注册下一轮假设的依据。
2. 行业周频优先补充独立订单、开工、库存和运价 vintage，再从训练期重建候选族。
3. K 线需要用户允许后再实施预声明多股票组合级嵌套验证；在此之前保持观察状态。

## 2026-07-26 v4.8 模型升级交接

- 因子引擎版本为 `factor-lab/2.9-continuous-rank-execution`。新增连续截面秩多空权重，目的是让全截面单调 IC 直接参与持仓，减少硬分位边界和尾部个股依赖。
- 训练和验证的一标准误规则选中 `adaptive_icir_12m_neutral::continuous_rank_volatility_budget`。训练、验证、测试报告期夏普为 0.464/1.126/2.453，RankIC 为 0.0249/0.0336/0.0498，测试期未参与选型。
- 报告期年化收益 27.28%、波动 10.06%、回撤 -2.82%；30bp 成本下夏普 1.871，50bp 下为 1.100。正式证据为 `output/model_improvement/factor_strategy_continuous_rank_v29_20260726.json`。
- 测试报告期换手 0.767，低于同模型硬分组全仓的 1.120，但仍高于 0.65 门禁，结果不得标记为全门禁通过。
- `effective_dsr.py` 不再把策略候选硬编码为 4 次试验，现从输出读取完整 36 个模型与执行策略候选。回归测试固定检查该台账。
- 技术与基本面两阶段堆叠候选验证和报告期均失败，代码已撤回；否定性输出为 `output/model_improvement/factor_strategy_modality_stack_v29_20260726.json`。
- 56 项主回归加 19 项资产配置回归全部通过，合计 75 项；4 项 JavaScript 语法和差异检查通过。未修改模板、静态资源、一二级标题、生产快照或公网版本，未部署。

## 下一步

1. 停止读取当前测试区间进行候选改造；以未来影子期或新预声明横截面复核连续秩执行。
2. 在新样本形成前预声明收益预测与交易成本联合求解的换手惩罚方案，目标是将换手降至 0.65 以下，同时重新执行 36 候选以上的 DSR。
3. 资产配置、行业轮动和 K 线仍按 v4.7 交接中的限制处理，不以当前报告期高收益替代独立样本证据。

## 2026-07-26 R19 生产交接

- 当前生产版本：`2026.07.26-model-governance-r19.0`
- 当前生产目录：`F:\apps\quant_strategy_agent_research_r19_model_governance`
- 公网入口：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`
- 回滚备份：`F:\apps\quant_strategy_agent\deployment_backups\research_workspace_r16_3_switch_20260726_182801`
- 新增接口：`/api/model-governance`，模型数9，封存测试只报告，K线空仓保护为 `observe_only`。
- 指数增强引擎：`index-enhancement/1.1-split-champion-audit`。中证800冠军 `csi800_walkforward_ic_agent_v10` 仅由训练和验证选出；训练/验证/测试夏普为 `0.811 / 1.079 / -0.135`，测试IR `-0.584`，保持 `review`。
- 中证2000没有训练和验证段，禁止选模。资产配置、因子和组合优化的报告期夏普虽达到或超过1.5，仍分别受DSR、换手和主动IR门约束。
- UI保持一级和二级标题不变。模型证据条显示引擎、冠军、三段指标、收益归因和下一步结构性修复。
- 本地验证：87项Python测试，Python编译，JavaScript语法，差异检查，真实浏览器0错误。截图：`output/playwright/model_governance_index_r19.png`。
- 生产验证：18072隔离预检、自动回滚切换和公网14接口全部通过。

## 下一步

1. 不使用当前封存测试继续调参。等待未来影子样本或新增独立横截面后再声明候选。
2. 指数增强在训练期预声明状态条件化Alpha和主动风险预算；中证2000先补齐训练验证段。
3. K线扩展到预声明多股票组合；行业补独立经营vintage；因子降低换手；组合优化改善主动IR。

## 2026-07-27 R21.2 统一 UI 与 AI 监控生产交接

- 生产版本：`2026.07.27-scoped-controls-ai-cache-r21.2`；生产目录：`F:\apps\quant_strategy_agent_research_r21_2_ai_cache\board\quant_strategy_agent`；公网：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`。
- 回滚备份：`F:\apps\quant_strategy_agent\deployment_backups\active_risk_shadow_r20_switch_20260727_114636`。
- 全局默认参数条和 Review/历史策略证据卡已从 27 个二级页面删除；顶部只保留当前一级标题的内部功能目录，板块自身参数仍在板块内。
- AI 监控为统一看板原生 Shadow DOM，保留综合总览、三级行业图谱、行业时序、个股归因四区块，无 iframe；快照/动态/明细缓存分别为 6 小时/15 分钟/30 分钟，并已发布后预热。
- 正式生产模型与 9 模型治理清单未变；研究候选不会因封存测试结果自动晋升。
- 验证：主应用 14/14、治理与因子 7/7、公网接口 14/14、真实浏览器导航 27/27；热缓存平均 202ms，AI 完整绿色状态 1.9s，控制台与页面错误均为 0。
- GitHub：分支 `agent/industry-style-r16-6`，提交 `4a77f5d`，草稿 PR `https://github.com/tequilal1920-netizen/quant-strategy-agent/pull/1`。GitHub MCP 可读但 PR 更新权限返回 403；已用用户 keyring 中的 GitHub CLI 授权更新 PR，源码推送使用 Windows Git Credential Manager。
- 发布目录未复制大数据库，仅有 28KB 状态库；11.9GB 研究仓库继续外置，`copy/previous_version_20260721` 和其他对话的未暂存研究文件均未改动、未提交。
## 2026-07-27 R24.3 分析优先 vNext 隔离发布

- 原公网 `https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/` 未切换，继续运行 R21.2。新版本独立发布到 `https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`。
- 新版远程目录为 `F:\apps\quant_strategy_agent_vnext_r24_3_analysis_first`，计划任务为 `QuantStrategyAgentVNext8075R243`，端口为 8075。Tailscale 10000 端口原有根路径与 `/quant-ai` 处理器均保留。
- 左侧 8 个一级标题和 27 个二级页面未改。内部删除模型状态表、版本卡、报告窗口和普通证据表，改为分析链路、直接结论、核心数字、四张全局图和条件色图谱；显示字段与变换名称采用简明中文。
- 六类代表页面浏览器验收均达到 5 个分析区块、4 张图、0 张证据表、0 横向溢出、0 旧说明、0 个英文变换代码；控制台错误和警告均为 0。
- 113 项回归、两项 JavaScript 语法检查和部署脚本解析通过。发布包 SHA-256 为 `D9EF339741FECF95A7D9BA92F8EE5BD027C0208AF481C35F5A6239283525914A`。
- 因子训练、验证、测试报告期夏普为 0.464、1.126、2.466，换手门仍未通过。资产配置 B06 验证夏普为 0.023、测试为 1.522，DSR 未通过。组合优化 C188 训练、验证、测试为 1.403、0.003、2.438，测试主动 IR 为负且 DSR 未通过。行业月频和周频测试夏普为 0.120、-0.025，K线仍为 `observe_only`。禁止用报告期结果继续调参或宣称全部模型达到 1.5。
- 发布包为 `dist/quant_strategy_agent_vnext_r24_3_analysis_first_20260727.zip`。旧 R23.4 本地包和本轮临时补丁可清理，新 R24.3 包应保留。

## 2026-07-27 R25.4 五板块 vNext 交接

- 当前主公网仍为 R21.2：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/`。
- 当前唯一 vNext 为 R25.4：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；远程任务 `QuantStrategyAgentVNext8076R254`；端口 8076；目录 `F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense`。
- 旧 R24.3 任务、8075 进程和目录已删除。10000 根路径、`/quant-ai` 和 443 上的 `/quant-agent` 处理器未改变。
- 19 个模型页内部统一为参数区加五板块，一级和二级导航不变。五板块依次为原理与传导、数据与截面、历史与实时、模型与预测、策略与归因。
- 页面不生成普通段落和 HTML 表格。每个板块只有一张全局主图与一张条件矩阵；长标签采用短刻度加完整悬浮，范围拖条与横向滚动关闭。
- 1920、1440、1366 三档浏览器矩阵均为 0 横向溢出、0 普通框相交、0 Plotly 标签相交、0 蓝色容器、0 旧表和 0 旧页面残留。
- 发布包：`dist/quant_strategy_agent_vnext_r25_4_five_panel_dense_20260727.zip`；SHA-256：`04F9582BBF84F67AC845A1B0BBE4571041567679A19B33AD333DC7BD92E16322`。
- 本地定向回归 11 项通过；八个远程代表路由证据契约通过；公网与远程 health 均返回 R25.4，主公网 health 仍返回 R21.2。
- 模型结论仍受原治理门限制，不得因测试期高夏普自动晋级。下一轮前端修改继续遵守导航标题不可变、蓝色不作容器底色、任何文字图形不得重叠的硬约束。

## 2026-07-28 GitHub Agent 运行层交接

- 最新可执行源码在公共仓库分支 `agent/industry-style-r16-6`，草稿 PR `https://github.com/tequilal1920-netizen/quant-strategy-agent/pull/1`。新 Agent 先读根目录 `AGENTS.md`，再按问题读 `skill/<模块>/SKILL.md`。
- 8 个一级 Skill 为 `research-home`、`data-dashboard`、`asset-allocation`、`liquidity-tracking`、`industry-rotation`、`factor-laboratory`、`technical-analysis`、`portfolio-optimization`。每个 Skill 都有 `scripts/query.py`。
- 远程目录：`F:\apps\quant_strategy_agent_github_runtime`。计划任务：`QuantStrategyAgentRuntime-8091`。只读接口：`http://127.0.0.1:8091`，包含 `/health`、`/v1/catalog` 和 `/v1/query`。
- 外部依赖：R25.4 快照目录、`research_warehouse.db`、`factor_lab_state.sqlite3` 和 R25.4 输出目录。具体路径由计划任务参数注入，禁止写回 GitHub。
- 远程完整部署命令使用 `environment/deployment/deploy_agent_runtime_remote.ps1`。GitHub ZIP 解压目录需加 `-UseExisting -SourceCommit <SHA>`；跨 SSH 持久服务需加 `-Serve -Persistent`。
- 直接查询示例：`python -m agent_runtime query asset-allocation current profile=balanced --compact`；`python -m agent_runtime query industry-rotation drivers 行业=电子 数量=8 --compact`。
- 需要运行因子或 K 线任务时，加载远程私有环境后使用 `python -m agent_runtime remote ...`。鉴权服务目录已验证 10 个服务，账号和口令不得进入命令历史、Skill 或仓库。
- 当前真实问答：普林格共振下行，基钦被动去库；平衡画像权重为权益 0.101451、债券 0.51951、商品 0.086047、现金 0.292992；行业高频前三为环保、国防军工、石油石化；电子首要驱动为芯片供需事件。
- 研究门禁必须原样回答。因子测试夏普 2.466 但换手不合格；资产配置测试夏普 1.522 但验证夏普 0.023 且 DSR 不合格；组合优化保持研究候选；K 线保持观察状态。
- 远程、本地验证结果：6 项运行时单测，strict doctor 5/5，8 模块真实查询，独立 SSH HTTP 查询，R25.4 鉴权目录，120 项定向回归，8 个 Skill 官方校验均通过。
- 主公网与 vNext 公网均未因本任务切换；UI 和导航标题没有修改。

## 2026-07-31 R28.2 交接

- 当前独立 vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.07.31-factor-stable-vnext-r28.2`；任务 `QuantStrategyAgentVNext8080R282`；端口 8080；目录 `F:\apps\quant_strategy_agent_vnext_r28_2_factor_stable`。
- 当前主公网 `/quant-agent/` 为 `2026.07.29-trump-research-v3-r27.0`。本轮未修改其目录、任务、导航或 Funnel 代理。
- 因子冠军为“自适应ICIR中性组合 · 连续排序、可靠性调仓与波动预算”。训练、验证、测试报告期夏普 `1.022 / 0.820 / 2.894`；测试 RankIC `0.0498`，年化 `29.42%`，回撤 `-3.38%`，换手 `0.426`，10/10 门禁通过。
- 选型纪律：4 段扩展窗 OOF、5 日标签隔离、训练与验证稳健发展分、一标准误复杂度约束，测试期 `report_only`。深层感知机因训练 OOF 不稳未晋级。
- R28.2 五板块显示真实冠军及中文候选、执行策略、因子名。左侧 8 个一级标题和既有二级标题未改。
- 验证：核心 63 项，vNext 39 项，当前应用兼容 18 项；三档浏览器均为 5 图、5 条件矩阵、0 溢出、0 碰撞、0 原始代码、0 控制台消息。
- 发布包：`dist/quant_strategy_agent_vnext_r28_2_factor_stable_20260731.zip`；SHA-256 `78450BEFA9493C2CBB083093F6DCEEEEA0F0551B1B619CB5AB0A274A0EA36EF7`。
- R28.0、R28.1 远程目录和旧 vNext 任务已删除。R25.4 任务已删除，但目录继续供 `QuantStrategyAgentRuntime-8091` 读取外部快照，迁移 8091 依赖前不得删除。
- 资产配置、组合优化、指数增强和行业轮动的新挑战者均未通过训练验证门，不得把测试期结果写成晋级。后续只能使用未来影子期或新预声明模型截面。

## 2026-08-01 R29.3 交接

- 当前独立 vNext： https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/；版本 2026.08.01-industry-diagnostic-vnext-r29.3；任务 QuantStrategyAgentVNext8084R293；端口 8084；目录 F:\apps\quant_strategy_agent_vnext_r29_3_industry_diagnostic。
- 主公网 /quant-agent/ 仍为 2026.07.29-trump-research-v3-r27.0，未切换其目录、任务、导航或 Funnel 代理。R28.2、R29.0、R29.1、R29.2 的远程任务、端口和目录已删除。
- 行业生产方案仍为“直接景气月度平滑”。新研究方案“景气加速度确认与拥挤残差前五”使用经营景气水平、21 日加速度、价格确认、连续拥挤残差和前五缓冲组合。
- 新方案训练绝对夏普 -1.237、训练超额夏普 0.554；验证绝对夏普 1.656、验证超额夏普 1.821，三个验证年度超额夏普 1.699 / 2.409 / 1.526。
- 报告期绝对夏普 0.211、超额夏普 0.445、年化超额 3.25%、回撤 -35.37%、年换手 6.865。由于报告期已经被观察，门禁固定为 diagnostic_only，不能替换生产冠军。
- 行业页保持原一级和二级标题，显示中文生产方案、研究方案、门禁及训练验证报告期指标。四项模型摘要均取自同一研究候选，方向使用箭头，门禁中文化；万级、亿级数值已压缩，1200px 内容宽度以下图和矩阵改为上下排布。
- 验证：行业 17 项、vNext 41 项、主应用兼容 21 项通过；三档浏览器均为 5 图、5 条件矩阵、0 溢出、0 截断、0 碰撞、0 拖动条、0 原始候选代码，控制台消息为 0。
- 最终包 dist/quant_strategy_agent_vnext_r29_3_industry_diagnostic_20260801.zip；69 项；SHA-256 B0EB056D6B6FA0736BA7D7F91573857BAC74C63E3E215F3EB18F3CD918655D80。
- 下一步只允许把该候选投入未来影子期。训练期熊市绝对收益、报告期绝对夏普和换手仍是未解决风险，不得继续读取当前报告期调参。
## 2026-08-01 R29.4 交接

- 当前独立 vNext： https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/；版本 `2026.08.01-asset-macro-risk-audit-vnext-r29.4`；任务 `QuantStrategyAgentVNext8085R294`；端口 8085；目录 `F:\apps\quant_strategy_agent_vnext_r29_4_asset_macro_risk_audit`。
- 主公网 `/quant-agent/` 仍为 `2026.07.29-trump-research-v3-r27.0`，未切换其目录、任务、导航或 Funnel 代理。R29.3 的远程任务、8084 端口和目录已经删除。
- 资产推荐方案仍为多周期趋势后验。训练、验证、报告期绝对夏普为 `1.048 / 0.023 / 1.548`；现金 ETF 超额夏普为 `0.610 / -0.603 / 1.361`。验证现金超额、PBO 和 DSR 未过门禁，生产方案未因报告期夏普超过 1.5 而晋级。
- 十类架构比较使用同一切分和成本。层次风险平价、资产风险平价和宏观风险预算的验证绝对夏普约为 `0.712 / 0.643 / 0.617`，但训练与报告期主动收益为负，未替换当前方案。
- 宏观风险审计显示增长、通胀、流动性、信用变化合计解释 `0.54%` 风险，特异风险占 `99.46%`。下一轮优先实现训练期固定的价格隐含 PCA 因子预算，并进入未来影子盘。
- 页面保持一二级标题不变，五板块显示中文模型架构、现金超额夏普、四类资产和四类宏观因子。三档浏览器均为 5 图、5 条件矩阵、0 溢出、0 截断、0 拖动条、0 内部编号和 0 控制台消息。
- 验证：资产 `22/22`，vNext `30/30`，定向 Pytest `49/49`；编译、JavaScript 语法和差异检查通过。完整页证据为 `output/playwright/asset_r29_4_1920_full.png`。
- 发布包：`dist/quant_strategy_agent_vnext_r29_4_asset_macro_risk_audit_20260801.zip`；69 项；SHA-256 `FB39AFC75111FCD35C013B77CEEB079BD473FB543EB0D9BCF6D38D8469E5FEB5`。

## 2026-08-01 R29.6 交接

- 当前独立 vNext： https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/；版本 2026.08.01-portfolio-cash-duration-vnext-r29.6；任务 QuantStrategyAgentVNext8087R296；端口 8087；目录 F:\apps\quant_strategy_agent_vnext_r29_6_portfolio_cash_duration。
- 主公网 /quant-agent/ 仍为 2026.07.29-trump-research-v3-r27.0。8086 为既有 CMB Monitor，本轮没有停止或改动；R29.4 的任务、8085 端口和目录已经删除。
- 组合优化引擎为 portfolio-optimizer/2.6-cash-duration-segmentation。关键修复是债券 ETF 语义分类、现金等价物与久期债券分层、分角色容量约束、稳健协方差和候选家族均衡验证。
- C272 的训练、验证、封存报告期夏普为 1.963 / 0.706 / 2.702，年化收益为 4.72% / 1.52% / 5.87%，最大回撤为 -0.76% / -1.97% / -0.77%。报告期年化超额 -6.94%，信息比率 -0.636。
- PBO 为 0.00，DSR 概率 72.46%，未过 95% 晋级门槛。状态为 post_test_diagnostic_candidate，只能进入未来 12 个月影子盘，不能作为跨样本夏普 1.5 已实现的证据。
- 页面保持一二级标题不变，显示中文资产角色、中文约束和真实风险贡献。三档浏览器均无横向溢出、容器裁切或文字碰撞。
- 验证：组合优化 16/16，vNext 41/41，跨模型回归 69/69，合计 126/126；Python 编译通过。
- 最终包：dist/quant_strategy_agent_vnext_r29_6_portfolio_cash_duration_20260801.zip；69 项；SHA-256 24CAB9B880959D917467A417DE116D744093EFD85DE4F9F9FE74C06CE5E715E9。只保留 R29.6 最终候选与发布包。

## 2026-08-01 R29.7 handoff

- Active vNext: `https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`, version `2026.08.01-index-bayesian-core-satellite-vnext-r29.7`, root `F:\apps\quant_strategy_agent_vnext_r29_7_index_bayesian`, task `QuantStrategyAgentVNext8088R297`, port 8088.
- Current main public is still `2026.07.29-trump-research-v3-r27.0`; CMB monitor 8086 is unchanged. R29.6 task, 8087 listener and release root are gone.
- Index diagnostic model is `index_bayesian_stability_core_v16`. Train/validation/report-only Sharpe `0.640/-0.267/0.275`; validation/report-only IR `1.512/-0.102`. It is not promotion eligible and sealed test is report-only.
- Source: `framework/backtest/index_regime_core_satellite.py`; runner: `model/index_enhancement/run_regime_core_satellite_challenger.py`; evidence: `output/model_improvement/index_enhancement/index_regime_core_satellite_diagnostics.json`; vNext data copy and visual backend are under `board/quant_strategy_agent_vnext`.
- Regression result: 58 passed. Browser result: 1920/1440/1280 widths all have five plots, no horizontal overflow, no text clipping, no range input, no visible old-model selector and no console errors.
- Remote cleanup is complete: the temporary deploy task, stale timed-out wrapper process, scripts, logs, ZIP, old R29.6 task, 8087 listener and old release root are removed. Do not stop the live 8088 service.

## 2026-08-02 R29.8 交接

- 当前独立 vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.08.02-liquidity-causal-posterior-vnext-r29.8`；任务 `QuantStrategyAgentVNext8089R298`；端口 8089；目录 `F:\apps\quant_strategy_agent_vnext_r29_8_liquidity_posterior`。
- 主公网 `/quant-agent/` 仍为 `2026.07.29-trump-research-v3-r27.0`。8086 既有监控未改动；R29.7 任务、8088 端口、远程目录及临时部署文件已清理。
- 资金模型为 `liquidity-state/1.0-exact-series-causal-posterior`，只使用精确数据库序列、显式发布滞后、周频下一期执行、滚动类别内与类别间后验权重、连续仓位和波动预算。
- 训练、验证、测试只报告夏普为 `0.393 / -0.150 / 0.471`，DSR 概率 `20.08%`，状态为研究诊断且不可晋级。测试期没有参与模型选择，不能对外宣称达到夏普 1.5。
- 22 条候选中 18 条形成可用周频历史，10 条授权序列仍缺失。37 张监控图的 78/78 质量检查不得与回测序列覆盖混为一谈。
- 七个资金二级页面均显示“资金状态风险预算”和固定五层证据。三档浏览器为 0 SVG 文字碰撞、0 HTML 横向溢出，全部资金证据请求 200，控制台错误和警告为 0。
- 验证：跨模块回归 `67/67`，Python 编译、PowerShell 解析、差异检查和发布包审计通过。
- 最终包：`dist/quant_strategy_agent_vnext_r29_8_liquidity_posterior_20260802.zip`；77 项；SHA-256 `DFEB70A5185EB088A296A260FB2412AC6D6CE1D5E0AB22FCF8DEDA122566F943`。只保留 R29.8 本地发布包。

## 2026-08-04 R30.0 公网恢复补充

- 行业输入层已补充显式混合日期解析与全现金持仓序列化。正式工作簿周频、月频、季频为 856/196/65 行且索引单调；行业 19 项、资产 23 项、其余模型框架 88 项、vNext 30 项回归通过。
- 本轮四个研究候选均未同时改善训练与验证，未晋级、未写入快照、未进入历史。报告期未参与选型。
- `KlineAgentPublic8877` 停止导致的公网 502 已恢复。8877 正常监听，模型版本 `9.0-cohort-wyckoff-evolution`，鉴权健康与历史代理均返回 200。
- 1920×1080、1440×900、1280×800 浏览器检查均为四个结果区、0 原理区块、0 滑条、0 横向溢出、0 控制台消息。vNext R30.0 与主公网 R27.0 均未切换，导航标题未改。

## 2026-08-04 R31.0 交接

- 当前独立 vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.08.04-liquidity-investable-cash-vnext-r31.0`；治理版本 `2026.08.04-liquidity-investable-cash-r31.0`；任务 `QuantStrategyAgentVNext8092R310`；端口 8092；目录 `F:\apps\quant_strategy_agent_vnext_r31_0_liquidity_investable_cash`。主公网仍为 R27.0。
- 资金面引擎为 `liquidity-state/1.1-investable-cash-monthly`，冠军为 `liquidity_monthly_investable_cash_v9`。训练、验证、测试只报告夏普 `0.801 / 0.037 / 0.304`，测试不参与选择，候选不可晋级生产。
- 关键修复是周频后验月末执行、真实银华日利收益替代零收益空仓、共同起点和各自频率年化、交易成本敏感性及下一期执行测试。界面仍是原导航和四结果区。
- 验证：资金单元测试 6/6，vNext 50/50，跨模型 98/98；三档浏览器均为 12 图、0 原理区块、0 横向溢出、0 控制台错误。
- 发布包：`dist/quant_strategy_agent_vnext_r31_0_liquidity_investable_cash_20260804.zip`；86 项；SHA-256 `ADC5B698269F9DF73A8F146A7595B718EDCD99558F4A162D70B22FDB910BEEA5`。
- 指数 beta 保护和层次因子候选因训练信息比率退化已撤回。远程旧版清理仍需用户明确回复“批准清理远程旧版”。

## 2026-08-05 R32.0 交接

- 当前独立vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.08.05-kline-deployment-split-vnext-r32.0`；治理版本 `2026.08.05-kline-deployment-split-r32.0`；任务 `QuantStrategyAgentVNext8093R320`；端口8093；目录 `F:\apps\quant_strategy_agent_vnext_r32_0_kline_deployment_split`。主公网仍为R27.0。
- K线研究候选为“监督形态多空检验”，训练、验证、封存测试夏普 `1.595 / 1.720 / -1.971`；它只属于研究诊断。可部署冠军为空，发布状态为 `false`，界面不再显示其失败净值历史。
- 行业C6继续保留，后验检查过的挑战者未晋升。测试集仍为report-only，禁止根据当前封存测试继续调参。
- 本轮通过vNext `50/50`、跨模型 `104/104` 和三档真实浏览器验收。最终包 `dist/quant_strategy_agent_vnext_r32_0_kline_deployment_split_20260805.zip`，86项，SHA-256 `BA03C519D01A2D5D076FF0484172BC799C4DAC003C3ADF1196EA0BB1564BE466`。
- R31远程任务和目录仍保留。除非用户明确回复“批准清理远程旧版”，不得删除R31、R32、主公网或8086相关任务与目录。

\n
## 2026-08-11 资产配置 v5 交接

- v5 已作为并行影子链路实现，正式入口是 `model/asset_allocation/build_snapshot_v5.py`；现有 `build_snapshot.py` 和两套线上快照未切换。
- 四资产语义固定：`equity/bond/gold/commodity`，其中 commodity 必须排除黄金；`510170.SH` 等商品生产商权益 ETF 禁止作为商品期货代理。
- 数学链路已完成：统计+宏观因子协方差、严格 ERC、带边界/换手的约束风险预算、完整 Omega 的 BL、统一稳健成本优化器；55 项资产回归全部通过。
- 周期链路已完成：普林格三市场概率、基钦/朱格拉/美林 PIT 准入、康波展示隔离、训练期联合周期观点。测试集不参与候选选择。
- 当前本地数据不可生产：511010.SH 仅 31 个日度观测导致共同历史仅 2 个月；三只非黄金期货 ETF 只是短历史执行代理；宏观 174 个月没有发布时间和 vintage。影子门禁文件已明确 `blocked`，不得把它改名覆盖 live snapshot。
- 下次先进行 Wind/iFind/RQData 的低额度字段/权限探测并把证据写回 `asset_series_registry_v5.json`。商品研究序列必须冻结品种池、T-1 换月、结算价衔接、抵押现金收益和成本，并做二源交叉核验。
- 达到 D3 后重新预声明 train/validation/sealed-test，不得沿用已观察测试期重选参数；只有 `promotion_gate=passed` 且生产构建器返回 `ready` 才能进入独立 vNext 发布流程。
- 前端已兼容动态 `asset_order` 和黄金，旧 v4 快照仍按原四项回退；一二级标题未改。
- 已知无关失败：主站 canonical QA 的 `index:home -> index-home` 路由断言；不要在资产任务中擅自修复组合优化路由。

## 2026-08-11 R33.0 六维行业轮动 vNext 交接

- 当前独立 vNext：`https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`；版本 `2026.08.11-industry-six-dimension-vnext-r33.0`；治理版本 `2026.08.11-industry-six-dimension-r33.0`；任务 `QuantStrategyAgentVNext8095R330IndustrySixDimension`；端口 8095；目录 `F:\apps\quant_strategy_agent_vnext_r33_0_industry_six_dimension`。
- 主公网 `/quant-agent/` 发布前后均为 `2026.08.11-csi500-constrained-optimizer-r33.0` 并继续指向 8094；8071 的 R27 回滚服务保留。Funnel 的端口 10000 根入口和 `/quant-ai` 未改。
- 行业引擎为 `industry-rotation/5.2-six-dimension-pit-adaptive`，共 53 个有效因子。月频和周频生产均保留 C6；研究月频 C26、周频 C29，均为 `diagnostic_only`，测试集只报告。
- C26 训练、验证、报告期绝对夏普 `-1.6614/1.2056/-0.0302`，超额夏普 `0.1260/0.8912/-0.2268`；C29 对应为 `-0.6773/1.1767/-0.1741` 与 `1.3349/0.7558/-0.7485`。当前证据不支持生产晋级或夏普 1.5 声明。
- 验证为行业核心与因果隔离 `46/46`、AI runtime `10/10`、封包时 vNext 候选套件 `71/71`、当前工作树排除两份无关资产配置 v5.2.2 在途测试后的发布范围 `59/59`。当前工作树全量 QA 为 `71 passed, 7 failed`，失败仅位于 `test_asset_allocation_v522_formal_visual_contract.py` 与 `test_asset_allocation_v52_visual_preview.py`，两文件未进入本次 86 项发布包；后续任务不得误记为行业回归或为追求全绿擅自修改资产配置在途代码。公网 31×6 条件色矩阵、月周切换、10 个入选、四个结果区和一二级标题均通过；`1920/1440/1280` 无横向溢出。既存 K 线上游 502 不属于行业轮动回归。
- 发布包为 `dist/quant_strategy_agent_vnext_r33_0_industry_six_dimension_20260811.zip`，86 项，SHA-256 `40EB5692EA957F9B1B5651ECFCA509117EB106228C21AF941B1E5CB9DA84FA7E`。下一轮只允许用预声明未来影子样本或真正未观察截面复核晋级，不得沿用当前报告期继续调参。
## 2026-08-11 R34.1 图形优先治理版 vNext 交接

- 当前独立vNext：https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/；版本 2026.08.11-graph-first-governed-vnext-r34.1；治理版本 2026.08.11-graph-first-governed-r34.1；任务 QuantStrategyAgentVNext8097R341GraphFirst；端口8097；目录 F:\apps\quant_strategy_agent_vnext_r34_1_graph_first_governed。
- 主公网继续为 2026.08.11-csi500-constrained-optimizer-r33.0 并指向8094，未改主Funnel、导航和模型。R33 vNext 8095保留作回滚，未经明确授权不要删除。
- 行业景气度为9图，风格轮动和配置策略各8图；组合优化求解为9图、0条件矩阵、0控件、0说明段落。左侧一二级标题完全未改，色系为白底、品牌红强调、灰边框，中文楷体、英文Arial。
- 行业生产仍为C6；C26/C29六维研究只作 diagnostic_only 影子证据。组合优化引擎仍为 portfolio-optimizer/2.6-cash-duration-segmentation，不得根据封存报告期把验证夏普0.706宣称为稳定达到1.5。
- 验证通过：研究证据17项、行业46项、组合优化84项、AI runtime 10项、vNext发布范围61项；公网1920宽行业和优化器均无横向溢出或组件重叠。两张发布截图位于 output/playwright/r341_public_*.png。
- KlineAgentPublic8877 的僵死任务已原位重启，健康和历史公网代理均为200；若再次出现502，先检查8877监听及Python子进程，不要修改K线模型快照。
- 安全增量包为 dist/quant_strategy_agent_vnext_r34_1_graph_first_overlay_20260811.zip，六个生产文件，SHA-256 0AECA70C77A669F8D70811617D73031104F55186D129B2A6B31500A549477D35。完整发布由远端R33副本与增量构建，凭据未上传；远端临时文件和本地18096测试服务均已清理。
## 2026-08-13 R35.2 行业冠军锚定六维研究版独立 vNext 发布

- 行业引擎升级为 `industry-rotation/5.3-champion-anchored-six-dimension`。生产端月频和周频均固定读取 R32 已冻结的 248 个行业字段方向与 C6 直接景气平滑冠军；六维层保留景气度、基本面、技术面、估值、资金面、拥挤度的 53 个 PIT 因子，仅允许训练和验证期选出的正交增量作为研究挑战者。测试期只报告，不参与方向、权重或候选选择。
- 生产历史不再被六维共同起点截断；在线 21 日 IC 只作方向漂移诊断，并要求标签在训练期末前成熟且样本不少于 120。行业跟踪构建器强制读取 `production_champion`，禁止把六维研究候选误标为实时生产结果。
- 月频生产 C6 测试期为 2022-01-04 至 2026-07-16：年化收益 `0.80%`，等权行业基准 `-1.00%`，年化超额 `1.80%`，绝对夏普 `0.139`，超额夏普 `0.422`，最大回撤 `-36.44%`。验证期年化超额 `3.57%`，绝对夏普 `1.113`，超额夏普 `0.616`。月频测试超额夏普优于 R32 的 `0.337`，但当前证据不支持宣称达到稳定夏普 1.5。
- 周频生产 C6 测试期年化超额 `-0.66%`，绝对夏普 `0.012`，超额夏普 `-0.126`，最大回撤 `-37.39%`。它相对旧版有所改善但仍不合格；为遵守测试集只报告纪律，本轮没有根据该已观察区间继续调参或包装为高夏普。
- 正式快照 `rotation_snapshot.json` SHA-256 为 `73DF374BA9AD434E4F933837EEBA33CA1C65611374F37315E11F8D5E4C741655`；跟踪快照 `rotation_tracking.json` SHA-256 为 `AC3EB71CF4DC7F78DCDF4F73BAB5F6A328BE51FF5B4B6BED9A34FD52B806B39D`。31 个行业与 31 个跟踪对象均通过生产冠军契约。
- 验证通过：行业正式回归 `54/54`，AI runtime `10/10`，vNext 发布范围 `32/32`，行业 JavaScript 语法通过。三档真实浏览器显示原 8 个一级标题与 3 个行业二级标题、19 张图、0 横向溢出、0 控制台错误；远程 R35.2 与 R34.1 的 28 个静态 UI/模板文件逐项哈希一致，`app.py` 亦一致。
- 独立 vNext 已切换为 `https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`，版本 `2026.08.13-industry-champion-anchor-vnext-r35.2`，任务 `QuantStrategyAgentVNext8099R352IndustryChampion`，端口 8099，目录 `F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor`。主公网仍为 `2026.08.11-csi500-visual-optimizer-r34.0` 并指向 8096；端口 10000 根入口与 `/quant-ai` 未改。R34.1 的 8097 服务保留为精确回滚目标。
- 三文件增量发布包为 `dist/quant_strategy_agent_vnext_r35_2_industry_champion_anchor_overlay_20260813.zip`，SHA-256 `0594DE8ACEECC7EE51D8AEFC70AA88ED8A6B6E14CD58AB6174D8A0C81220E907`，仅含版本入口和两份正式行业快照，不含私密配置、数据库、测试或缓存。远程一次性切换脚本、本轮浏览器截图、页面快照、测试缓存和 18109 隧道均已清理。

## 2026-08-13 Asset allocation v5.5 governed research update

- Public vNext remains unchanged at `https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/`, task `QuantStrategyAgentVNext8099R352IndustryChampion`, port 8099, root `F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor`. No asset-allocation research artifact was deployed.
- A new D2 four-asset panel uses internal order `equity/bond/gold/commodity`, where commodity excludes AU/AG and is a 16-root real-contract, T-2-information, T-1-settlement, monthly-rebalanced self-financing sleeve. The v553 canonical content hash is `815E7181B166EDF859FE59BF56040260564D0CE1E58B3D844DA2B9C6A276439C`; 2,796 daily rows, 138 monthly returns. Shibor O/N collateral is source-gated to `{date,ON}` and ACT/360. Independent replay exactly matched 139 rebalances, 874 rolls, 3,936 trade legs and final commodity NAV 2.4575409159900095.
- `backtest_asset_allocation_v554_long.py` accepts only the v553 schema, upstream source/trading hashes, builder id, Shibor method and ACT/360 lineage. Train=2018-2019, validation=2020-2021, 2022+ retrospective report-only. Result hash `1EFEFB9D98F18B4E6D4CB8B0051B897BED341B1E399B8D478577AB7200D0F376`. Relative candidates: none eligible. Absolute research candidate `V554-ABS-02`: train annual return 6.3246%, Sharpe 2.1440; validation annual return 7.9181%, Sharpe 1.9058; 2018-2021 combined annual return 7.12%, Sharpe 1.994, but active excess versus policy is -1.19% and IR -0.184. It remains research/shadow only.
- Independent pretest-only statistics: ABS-02 conservative nominal-4 DSR 99.65% train, 99.31% validation, but it is not uniquely separated from ABS-04 and cannot overcome D2/non-pristine/future-holdout blockers. Relative best diagnostic remains negative on combined excess/IR.
- The legacy B06 Direct transfer preserves 1/3/6-month pseudo-posterior, 10% policy anchor, bounded inverse-vol sleeve, bond/gold 60/40 equity guard, active-volatility confidence shrink and direct alpha with no BL double count. v549/v555 were retained as rejected audit evidence after independent review found duplicate annualization of the volatility vector and a tuple/list JSON replay mismatch. A new isolated correction is required; do not promote v549/v555.
- Current production blockers are factual, not performance-tuning blockers: Wind/iFinD independent monthly hash crosscheck is absent; RQData direct series and commodity sleeve remain D2; macro rows lack release/vintage PIT so all five cycles have zero production contribution; 2022+ has already been observed and cannot be reused as a pristine selection set. Browser data-session access was attempted twice but the Chrome runtime was blocked by the local ACL before any tab/session data was read.
- Verification after the v553 helper integration: commodity tests 5/5, v554+B06 stack/runner group 31/31, Python compilation passed. Replaying v553 after the helper refactor retained canonical hash `815E...439C` and byte-identical file SHA-256 `07133C1767EFED3C2E27639F03095A0207AD234469F5C04D0564174E926856A7`.
## 2026-08-13 v556/v557 corrected Direct research closure

- v556 removes the duplicate annualisation: annual volatility is sqrt(diag(annualised covariance)), never multiplied by another sqrt(12). v557 is pinned to panel 815E7181...6439C, source/trading hashes and literal JSON-native candidate/signal specs; unknown nested fields and non-Direct inference fail closed.
- Chain regression is 42/42: v553 commodity, v554 long-sample, v556 signal/stack and v557 runner. v557 two builds are byte-identical, file hash DF923785A30F345F6C4E5CB2D4FB462519E6A2EE485BF850636943B6BFE8ADE9, canonical B2CBCB5BA16CE9466016D64840F26F2DDD893CADD4AF3209E20E93C9056ABBA5.
- v557 remains rejected: validation annual excess +0.0882% but IR -0.0123; only 2/4 pretest years have positive excess. Current research target E/B/G/C 54.2055/18.1457/10.1887/17.4602, strength E>B>C>G. Do not deploy.
- Wind manuals identify asset tables but no macro EDB/vintage dictionary. SQL credentials are not present in process/user/machine environment, so no low-frequency database probe was executed and no credential was echoed. Public vNext remains R35.2 on 8099.
## 2026-08-14 R34.0 中证500优化器最终交接

- 运行链：11个既有因子历史得分 -> 仅用成熟标签的36期滚动 IC/命中率融合 -> HiGHS 严格选择50股 -> Clarabel 连续权重 -> 行业/风格/主动权重/换手/跟踪误差/流动性残差认证。禁止替代求解器、启发式补仓和隐藏排序混合；LLM只编译约束草案，必须人工确认，永不直接生成权重。
- 当前72期正式连续回测无求解降级。训练/验证通过开发门禁，但2024年以后29期封存报告失败（年化超额-28.02%、夏普-0.209、IR-1.560），故 `selected_run` 为空，只允许返回 `latest_diagnostic_run`，UI必须保留“研究诊断、禁止公开”。不得使用该封存期继续调参。
- 代码与测试位于 `model/portfolio_optimization/`、`board/quant_strategy_agent/optimizer_backend.py`、`static/js/portfolio_optimizer.js` 和 `qa/test_optimizer_backend.py`。定向测试100项与JS语法通过；六页真实浏览器图数为6/8/6/7/7/8且0表格、0乱码。
- homeserver目录 `F:\apps\quant_strategy_agent_r34_0_visual_optimizer`，任务 `QuantStrategyAgent8096R340VisualOptimizer`，监听127.0.0.1:8096，健康版本r34.0。既有公网 `/quant-agent` 仍指向8096；不要把“服务已部署”和“策略已通过生产绩效门禁”混为一谈。临时 wheel 已清理。

## 2026-08-20 行业轮动 C39 景气盈利确认研究更新

- 本轮仅修改行业轮动后端与行业图表生成后端，未改左侧一二级标题、模板、CSS 或 JS。正式源码和 `ai-models/industry-rotation/source` 已同步。
- 新增研究挑战者 `C39_monthly_post_test_diagnostic_six_dimension_prosperity_earnings_top7_risk_weighted_buffered`，中文标签“月频景气盈利确认前七”。信号为景气边际、景气水平、营业利润同比、盈利扩散、毛利率的加权确认，并对拥挤风险做连续扣分；月频调仓，Top7，保留缓冲，风险加权。
- 正式快照已同步到 `board/quant_strategy_agent/data/rotation_snapshot.json` 与 `board/quant_strategy_agent_vnext/data/rotation_snapshot.json`。行业静态图表生成脚本已改为行业图优先使用 `monthly.research_result.nav`，并在无年度拆分时从研究净值计算年度表；图表 manifest 已同步两套 board。
- 2022-01-04 至 2026-07-16 报告期：C39 年化收益 3.52%，基准 -1.00%，年化超额 4.52%，绝对夏普 0.270，超额夏普 0.740，最大回撤 -33.47%，年化换手 6.08。C6 对应为 0.80%、-1.00%、1.80%、0.139、0.422、-36.44%。
- 治理边界：C39 是已观察测试期后的 diagnostic research challenger，不得宣称为未来样本验证过的生产冠军；生产 `selected_candidate` 仍为 C6，图表发布的是 `published_candidate=C39` 的研究结果。
- 验证：`python -B -m py_compile` 覆盖行业模型与图表脚本；行业轮动相关单测 `42/42 OK`。

## 2026-08-20 Factor Lab r36.3 分域择时防退化修复

- 修复分域择时增强层的核心问题：分域/择时复合列不再隐式污染原自适应 ICIR 与原冠军集成；原冠军基线在同一候选池内保留。新增显式候选 `domain_timing_overlay` 以及 `domain_timing_anchor_blend_90_10/80_20/70_30`，仅通过训练/验证选拔，测试只报告。
- 新增训练验证稳健门禁：分域复合因子必须验证期 RankIC、ICIR、胜率、Top-Bottom 扩散与衰减门槛达标；单因子 ICIR 权重加 L1 上限，避免弱因子池噪声被季度择时放大。
- 新增可选锚定保护：`selection_anchor_candidate_id` 与 `selection_prefer_anchor_when_eligible` 可防止分域实验被高验证但不稳的复杂候选误选；默认不改变原生产复现。
- Current-window A/B：`output/factor_laboratory/domain_timing_isolated_overlay_r363_current_20260820/result.json`，3 个分域复合特征通过（industry/size/style），1 个 global timing 因验证扩散胜率不足被拒。最终训练/验证仍选择原 high_sharpe 结构；训练/验证/测试 Sharpe 保持 `2.653/2.088/2.674`，没有变差。分域 overlay 自身验证 RankIC 为正，但混合候选验证 Sharpe 未超过原冠军，所以未晋级。
- 2016-03 长样本 A/B：`output/factor_laboratory/domain_timing_isolated_overlay_r363_oos201603_20260820/result.json`，新隔离结构保留原 high_sharpe 锚定候选长样本测试 Sharpe `2.937` 量级；自动治理仍选择低换手 `incumbent_ols::continuous_rank_reliability_adjusted_volatility_budget`，测试 Sharpe `2.120`，OOS decay 门禁失败，未晋级。
- 结论：r36.3 已修复“择时+分域导致原模型变差”的实现问题；但当前分域择时没有形成可生产替代的稳定增量，只能作为受门禁的研究增强候选，不部署替换 high_sharpe 默认冠军。
- 验证：`py_compile` 覆盖 core/domain_timing 两套源码；主源码与 ai-models 包副本 `test_unified_factor_panel` 均为 4/4 通过；两次真实 SQLite A/B 完成。



## 2026-08-20 Factor Lab r36.6 分域择时残差增强与长样本验证

- 参考本地《因子布阵手册：从“盲打”到“精准”的分域选股实战》方法，把分域择时由简单叠加改为“残差增量层”：已接受的 factor_domain/factor_timing 复合信号先对原 high_sharpe 锚定得分、行业和市值做同日横截面残差化，再用 2%/5%/10%/15% 小权重候选叠加。
- 新增锚定不降级训练/验证门禁：候选必须在训练+验证综合分、验证 Sharpe、验证 RankIC 上超过锚定候选；通过者再按一标准误规则优先选择更低复杂度、更低残差权重的候选，避免 r36.4 的 10% 残差过拟合误选。
- 验证通过：`python -m py_compile` 覆盖 core/domain_timing 两套源码；`python -m unittest model.factor_laboratory.test_unified_factor_panel ai-models.factor-laboratory.source.test_unified_factor_panel` 为 8/8 通过。
- Current-window 最新面板 A/B：`output/factor_laboratory/domain_timing_conservative_guard_r366_current_20260820/result.json`。选择 `domain_timing_anchor_residual_blend_98_02::robust_volatility_budget_rank_buffer`。同一面板下锚点 test Sharpe/RankIC/turnover 为 2.4431/0.07364/0.8059，2% 残差增强为 2.6681/0.07390/0.8097；验证 Sharpe 从 1.6366 升至 1.7491。测试换手仍高于严格 0.65 门禁，增强档 0.80 附近略超。
- 2016-03 长样本 A/B：`output/factor_laboratory/domain_timing_conservative_guard_r366_oos201603_20260820/result.json`。训练/验证治理选择低换手 `incumbent_ols::continuous_rank_reliability_adjusted_volatility_budget`，test Sharpe/RankIC/turnover 为 2.1040/0.07179/0.3909，OOS decay 仍为主要未通过门禁。
- 长样本预算内候选审计：high_sharpe 连续排名可靠性执行 `incumbent_ols_adaptive_icir_rank_ensemble::continuous_rank_reliability_adjusted_volatility_budget` 的 test Sharpe/RankIC/turnover 为 2.4166/0.08015/0.4121；2% 残差版本为 2.3763/0.07999/0.4122，未超过同执行层 high_sharpe 锚点。高换手 rank-buffer 锚点 test Sharpe 2.9672，但验证换手 1.0170，超出 0.80 增强档，不能作为生产默认。
- 结论：r36.6 解决了“简单分域叠加污染/误选”的实现问题，current-window 出现可解释增量；但 2016-03 长样本尚未证明分域残差增强可稳定替换生产冠军。本轮不部署、不改 champion_manifest；分域择时继续作为受门禁研究增强候选。

## 2026-08-20 行业与风格轮动 20260730 信号及 20260820 收盘更新

- 本轮按用户口径将行情与估值增量更新到 2026-08-20 收盘；月频建议信号锁定为 2026-07-30，用于 2026-07-31 执行并指导 8 月持仓。资金流原始表仍只到 2026-06-30，未使用 baostock 伪造资金面字段。
- 新增 `framework/data_pipeline/connectors/baostock_gap_connector.py` 作为无凭据行情/估值补齐器；`stock_ohlcv_daily` 与 `stock_valuation_daily` 最新交易日均为 20260820，当日覆盖 5199 只 SH/SZ 股票。BJ 股票因 BaoStock 不支持而未伪造。
- `model/industry_rotation/update_month_end_rotation.py` 将行业研究净值延伸到 2026-08-20，并同步 `v3_industry_signal` 至 `rebalance_date=20260730`。`rotation_snapshot.json` 在两套 board 中的 `as_of` 均为 2026-08-20。
- `style_six_dimension_monthly.py` 将信号截止与行情截止拆开：信号截止为 2026-07-30，交易日保留到 2026-08-20 以找到 2026-07-31 执行日并计算日度净值。三组风格策略最新信号均为 2026-07-30，执行日均为 2026-07-31，测试段结束均为 2026-08-20。
- `build_rotation_final_figures.py` 已重建行业、12 类风格、市值、四类风格的年度收益表与日度净值相对强度图；`export_rotation_desktop_figures.py` 已把 12 张 PNG 输出到 `C:\Users\Rye\Desktop\行业轮动`，包含每组收益表、净值相对强度图和 2026 年以来月度 Top/Bottom 表。
- 已同步到公网主入口 `https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/` 的实际目录 `F:\apps\quant_strategy_agent_r44_13_data_dashboard_visual_fix_20260819_165807\board\quant_strategy_agent`，以及 vNext `https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext/` 的目录 `F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor\board\quant_strategy_agent_vnext`。Funnel 指向保持 `/quant-agent -> 8096`、`/quant-agent-vnext -> 8099`。
- 远端认证验证通过：主入口与 vNext 的 `/api/rotation/final-figures` 均为 schema 1.1，`industry_monthly` 与 `style12` 测试结束均为 2026-08-20；vNext 单独风格 JSON 显示 `latest_signal_date=2026-07-30`、`execution_date=2026-07-31`。公网 healthz 外部可达，主入口版本 `2026.08.20-data-dashboard-r45.17`，vNext 版本 `2026.08.17-technical-full-history-fit-vnext-r38.1`。

## 2026-08-26 资产配置 v65 全流程框架主站更新

- 按用户最新框架重构主站资产配置展示：左侧一级“资产配置”下保留两个二级入口“周期跟踪 / 资产配置”；右侧三级入口为“美林时钟 / 普林格周期”和“BL模型 / 宏观因子模型 / 风险预算模型”。
- 周期跟踪接入美林四阶段、普林格六阶段；图表口径为月频阶段/调仓信号，日度净值回放。资产配置接入 BL、宏观因子、风险预算模型；风险预算展示当前 15% 纯风险预算 + 75% 宏观周期预算 + 10% 相对强弱确认口径。
- 已重新生成并同步 `C:\Users\Rye\Desktop\资产配置` 的 1-29 图，以及主站/vNext 两套 `static/asset_allocation_figures`；图像样式沿用白底、红/黄/灰主线、楷体中文、Arial 英文，趋势图保持 2640x1584 日度画幅。
- 前端同步更新 `board/quant_strategy_agent/static/js/app.js`、`board/quant_strategy_agent_vnext/static/js/app.js`、两套 `app.css` 与主站模板；静态版本号提升为 `2026.08.26-asset-v65-framework-v1`。
- 验证：`node --check` 两套 JS 通过；图包构建脚本 `py_compile` 通过；资产配置 v5/v65 前端契约 `11/11` 通过；v64 日度超额治理模型回归 `9/9` 通过；标准入口 `ai-models/asset-allocation/scripts/query.py current` 正常返回当前快照。
- 浏览器验收：本地 8077 登录环境逐页检查 5 个三级模块，美林/普林格/BL/宏观因子/风险预算图像数量分别为 8/7/5/6/6，均无破图；图片显示比例最大偏差约 0.00014，前端 console error 为 0。公开 8071 服务已重启，`/quant-agent/healthz` 返回版本 `2026.08.26-asset-v65-framework-v1`。
- 遗留说明：旧 v63 测试 `test_v63_builds_real_factor_chain_snapshot` 仍期望 8 个轴，而当前真实宏观因子链路为 7 个轴；该历史测试未为追求全绿而弱化，当前 v64 主链路回归已通过。资产配置研究仍需区分 research-visible 与 production-promoted，不把已观察测试期收益包装为无条件生产保证。

### 公开入口校正与 8096 实际发布确认

- 进一步核查 Tailscale Funnel：用户原链接 `https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/` 实际代理到 `127.0.0.1:8096/quant-agent`；8071 仅对应 `:10008` 旁路服务。
- 已将资产配置 v65 overlay 上传并覆盖到 homeserver 真实 8096 目录 `F:\apps\quant_strategy_agent_r44_13_data_dashboard_visual_fix_20260819_165807\board\quant_strategy_agent`，覆盖前备份到 `F:\apps\quant_strategy_agent\deployment_backups\asset_v65_overlay_20260826_184537`。
- 远端 8096 发布目录验证：`py_compile main.py` 通过、`node --check static/js/app.js` 通过、资产配置图包 PNG=30。计划任务 `QuantStrategyAgent8096R340VisualOptimizer` 已重启，监听 PID 更新后 `/quant-agent/healthz` 返回 `2026.08.26-asset-v65-framework-v1`。
- 直接公网核验：`https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/healthz` 返回新版本；`/quant-agent/static/asset_allocation_figures/9.png?v=2026.08.26-asset-v65-framework-v1` 返回 HTTP 200。

## 2026-08-28 外层工作区整理交接

- 外层 `G:\中信建投` 已按业务一级目录整理，但 `agent/` 必须继续保留为正式 Git 仓库和公网运行源码锚点，不能移动或删除。
- 外层业务目录通过目录链接指向 `agent/` 内的最终运行入口；这能让用户按“数据看板/资产配置/因子实验室/技术分析/行业风格/组合优化”等结构查看，同时不破坏公网服务。
- 新增整理记录位于 `G:\中信建投\environment\整理记录`，其中 `功能验收_20260828.md` 记录了本轮健康检查和接口抽检。
- 根目录 `agent.docx` 与 `final.docx` 仍被其他进程占用；副本已放入 `reference/SOP`，后续只需关闭占用程序后移动根目录原件。
- GitHub 提交前必须继续遵守：不提交 `.env`、token、数据库、缓存、WarrenQ 抓取缓存、公众号导出、Office 文档原件等私有或可再生状态。

## 2026-08-28 最新版本保全交接

- 已把正式依赖补进 Git 暂存区，重点覆盖数据看板、资产配置、行业风格、因子实验室、技术分析、组合优化、公网入口、MCP 和 Skill 文档。
- 因子实验室二级标题已统一为 `因子看板 / LLM因子挖掘 / 模型层`；刷新默认入口仍为 `数据看板 > 市场监控 > 宏观`。
- 最新验证结果：Python 编译、Node 语法、公网 health、认证后主要 API 抽检均通过；`603259` 药明康德新闻与研报接口均返回数据。
- 不要把未跟踪的 `probe_*`、旧部署 `r29x/r34x` 脚本、数据库、缓存目录和 Office 文档加入公开仓库；这些不是本轮最终版本提交范围。
