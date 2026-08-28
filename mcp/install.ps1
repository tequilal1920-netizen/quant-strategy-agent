param(
    [string]$RepoUrl = "https://github.com/tequilal1920-netizen/quant-strategy-agent.git",
    [string]$Branch = "agent/industry-style-r16-6",
    [string]$InstallDir = "$env:USERPROFILE\quant-strategy-agent"
)

$ErrorActionPreference = "Stop"

$ScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$Server = Join-Path $ScriptRoot "quant_agent_mcp"

if (-not (Test-Path -LiteralPath $Server)) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git_not_found: install Git first, or run mcp\install.ps1 inside an existing clone."
    }
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        Write-Host "Local MCP source not found. Cloning public repository..."
        git clone --branch $Branch --single-branch $RepoUrl $InstallDir
    }
    $Server = Join-Path $InstallDir "mcp\quant_agent_mcp"
}

if (-not (Test-Path -LiteralPath $Server)) {
    throw "mcp_server_dir_missing: $Server"
}

Write-Host "Installing quant-agent MCP dependencies..."
python -m pip install -e $Server

Write-Host "Install complete. MCP command:"
Write-Host "python `"$Server\server.py`""
Write-Host ""
Write-Host "Codex/Claude Desktop config example:"
$Config = [ordered]@{
    mcpServers = [ordered]@{
        "quant-agent" = [ordered]@{
            command = "python"
            args = @("$Server\server.py")
        }
    }
}
Write-Host ($Config | ConvertTo-Json -Compress -Depth 5)
