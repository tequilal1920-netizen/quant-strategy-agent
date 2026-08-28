$ErrorActionPreference = "Stop"

$Server = Join-Path $PSScriptRoot "quant_agent_mcp"

Write-Host "安装量化 Agent MCP 依赖..."
Set-Location $Server
python -m pip install -e .

Write-Host "安装完成。启动命令："
Write-Host "python `"$Server\server.py`""
