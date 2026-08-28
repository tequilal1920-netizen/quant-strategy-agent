# 量化 Agent MCP

本目录是可上传 GitHub 的公开 MCP 接入骨架。它不包含数据库、缓存、凭据或授权数据，只暴露模型目录、查询入口和公开状态。

## 启动

```powershell
Set-Location mcp\quant_agent_mcp
python -m pip install -e .
python server.py
```

新电脑 clone 仓库后，可通过 MCP 工具读取 `model_catalog.json`，再按本机私密环境变量接入外部数据库和生产快照。
