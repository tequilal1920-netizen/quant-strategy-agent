# 量化策略 Agent 使用约定

本仓库的正式 AI 入口是八个 `ai-models/<name>/` 统一文件夹。每个文件夹同时包含 `SKILL.md`、`PACKAGE.json`、查询脚本、一级模型源码和必要内部组件。收到研究问题后先选择对应文件夹，再调用其中的 `scripts/query.py`。旧 `skill/` 目录仅用于兼容。

## 路由

| 用户问题 | Skill |
| --- | --- |
| 跨模型简报与总配置 | `ai-models/research-home/` |
| 宏观、全球市场、行业、商品、个股与事件数据 | `ai-models/data-dashboard/` |
| 周期识别与大类资产权重 | `ai-models/asset-allocation/` |
| 散户、公募、ETF、融资、一级市场、私募与外资 | `ai-models/liquidity-tracking/` |
| 行业景气、行业驱动、月周轮动与风格箱 | `ai-models/industry-rotation/` |
| 因子、LLM挖掘、指数增强与SmartBeta | `ai-models/factor-laboratory/` |
| K线学习、形态记忆与单股技术任务 | `ai-models/technical-analysis/` |
| 已知标的、得分、约束与目标权重 | `ai-models/portfolio-optimization/` |

## 数据连接

优先读取远程机器上的正式数据，不把数据库或模型产物复制进公开仓库。

```powershell
$env:QUANT_AGENT_SNAPSHOT_ROOT = "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense\board\quant_strategy_agent_vnext\data"
$env:RESEARCH_WAREHOUSE_DB = "F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db"
$env:FACTOR_STATE_DB = "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense\database\factor_lab_state.sqlite3"
$env:QUANT_AGENT_OUTPUT_ROOT = "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense\output"
python -m agent_runtime doctor
```

路径以部署机器真实位置为准。`QUANT_AGENT_SNAPSHOT_ROOT` 是查询当前结论的必需项。数据库和输出目录仅在运行模型或读取明细时需要。

## 回答纪律

1. 先运行查询脚本，再根据返回的 `数据截止`、`生成时间`、`数据来源`和`结果`回答。
2. 训练集与验证集负责选模，测试集只报告。不得用测试结果继续调参。
3. 明确区分研究候选、观察状态和生产可交易状态。不得把高测试夏普直接描述为已通过治理。
4. 数据缺失、过期、来源受阻或远程接口失败时直接说明，不用近似值补齐。
5. 启动模型任务前确认用户确实要求运行或更新；只读询问不得改写数据库、快照或模型注册表。

## 统一入口

```powershell
python -m agent_runtime catalog
python -m agent_runtime query asset-allocation current 画像=平衡
python -m agent_runtime query industry-rotation drivers 行业=电子 数量=8
python -m agent_runtime serve --host 127.0.0.1 --port 8091
```

本机 HTTP 接口为 `GET /health`、`GET /v1/catalog` 和 `POST /v1/query`。绑定非本机地址前必须设置 `QUANT_AGENT_RUNTIME_TOKEN`。

远程统一模型服务通过下列环境变量访问，凭据不得写入提示词、日志或仓库：

```powershell
$env:QUANT_AGENT_BASE_URL = "http://127.0.0.1:8076/quant-agent-vnext"
$env:QUANT_AGENT_USER = "<运行时账号>"
$env:QUANT_AGENT_PASSWORD = "<运行时口令>"
python -m agent_runtime remote GET /api/services
```
