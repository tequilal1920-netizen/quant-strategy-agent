---
name: liquidity-tracking
description: "用于更新、审计、构建和发布中信建投资金面跟踪模块；当任务涉及主页、散户、公募基金、ETF、融资资金、一级市场、私募基金、外资资金的来源替换、跨频率对齐、图表质量或公网发布时使用。"
---
# 资金面跟踪


## 直接对话入口

```powershell
python skill/liquidity-tracking/scripts/query.py overview
python skill/liquidity-tracking/scripts/query.py page 页面=外资
python skill/liquidity-tracking/scripts/query.py page 页面=融资
```

页面支持主页、散户、公募、ETF、融资、一级市场、私募和外资。脚本返回每张正式图的最新有效日期、最新值、来源编号和质量状态，可用于回答“某类资金最近如何变化”和“当前资金面最重要的约束是什么”。

需要刷新数据库时才执行 `data_sources.py refresh` 和严格审计；普通问答只读正式快照，不改写数据库。

## 目标

从可持续外部数据库构建 49 个基础序列，经确定性变换生成现有 37 张图；保留既有一二级导航、图表 ID、指标名称、UI、色系和布局。

## 必读

先读 `references/module-map.md`、模型源码、`environment/status/claude-progress.md`、`feature_list.json` 和 `session-handoff.md`。把仓库文件视为唯一正式状态。

## 数据规则

- 来源优先级：Wind → 米筐 → iFinD → 官方或公开 API。
- 不从 Excel、旧网页快照或图片读取任何数值。Excel 只可用于核对历史公式或图形语义，且不得进入来源注册表。
- 每个基础序列必须保留来源、表/指标代码、字段、单位、原始频率、更新时间和变换规则。
- 私募托管样本、EPFR 或其他授权序列缺失时必须阻止发布；不得用相似指数、推算值、复制旧值或其他主体代理。
- 凭据只能从运行时环境变量或已登录客户端取得，禁止写入源码、缓存元数据、Word、日志或 Git。
- 不修改 `database/research_warehouse.db`，不删除用户文件，不创建多版快照或中间归档。

## 当前精确来源基线（2026-07-24）

- Wind SQL：34 个基础序列，覆盖散户小单、公募、ETF、融资交易、一级市场、私募策略、陆股通成交和上证指数。
- 中证数据：`margin.guarantee_ratio`、`margin.collateral_cash`、`margin.collateral_securities`；历史月表与最新月表按月份去重拼接。
- 华润信托 CREFI：`private.stock_long_position`；从官方月报列表 API 和 PDF 逐月解析并检查 0—100 与月份连续性。
- 中国证券投资基金业协会：`private.aum`；保留官方月度 API 当前提供的最长滚动窗口。
- 精确阻断：Wind EDB 的 `retail.new_accounts`、`retail.participating_investors`，以及 EPFR 的 8 个外资配置/仓位序列。没有同口径授权时不得发布。
- 当前严格审计为 39/49、`excel_numeric_dependency=false`、`status=blocked`；不得覆盖现有生产快照。
## 标准流程

1. 确认正式目录是 `G:\中信建投\agent`，并检查 Git 状态；保留无关改动。
2. 检查运行时认证。Wind SQL 使用 `WIND_SQL_SERVER`、`WIND_SQL_UID`、`WIND_SQL_PASSWORD`；Wind EDB 依赖已登录 Wind 终端。未配置时明确阻塞，不落地凭据。
3. 从 2010-01-01 起刷新；若接口起始日晚于 2010，则保留该接口的最长真实历史：

```powershell
python model/liquidity_tracking/data_sources.py refresh `
  --cache database/liquidity_tracking.sqlite3 `
  --start 2010-01-01 `
  --allow-incomplete
```

4. 严格审计 49 个基础序列。任一必需序列缺失、包含未来日期或仍依赖 Excel 时停止：

```powershell
python model/liquidity_tracking/data_sources.py audit `
  --cache database/liquidity_tracking.sqlite3 `
  --strict
```

5. 仅当审计状态为 `passed` 时生成唯一正式快照：

```powershell
python model/liquidity_tracking/build_snapshot.py `
  --liquidity-cache database/liquidity_tracking.sqlite3 `
  --cache-dir database/public_source_cache `
  --output board/quant_strategy_agent/data/liquidity_snapshot.json
```

6. 验证页面顺序、37 个图表 ID、89 条以内的既有 trace 装配、每图共同日期交集、无缺失/重复/未来日期、频率和单位、左右轴引用、长序列半年刻度与短序列月度刻度。
7. 运行正式应用 QA 并逐页核查现网。只有本地、快照和浏览器检查全部通过后才能部署；失败时保留现有线上版本。

## 发布边界

- 不改一二级导航标题、顺序或映射。
- 不改图表标题、trace 名称、字段、图形类型、颜色、字体、字号、布局和交互。
- 不以“可展示”为由降低数据门槛。
- 不把合成测试数据、测试 SQLite、截图、下载缓存或临时脚本并入正式仓库。

## 验证

```powershell
python -m py_compile `
  model/liquidity_tracking/data_sources.py `
  model/liquidity_tracking/specialized_refresh.py `
  model/liquidity_tracking/official_refresh.py `
  model/liquidity_tracking/snapshot_domestic.py `
  model/liquidity_tracking/build_snapshot.py
git diff --check
python board/quant_strategy_agent/qa/test_canonical_app.py
```
