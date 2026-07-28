param(
  [string]$Repository = "https://github.com/tequilal1920-netizen/quant-strategy-agent.git",
  [string]$Branch = "agent/industry-style-r16-6",
  [string]$InstallRoot = "F:\apps\quant_strategy_agent_github_runtime",
  [Parameter(Mandatory = $true)]
  [string]$SnapshotRoot,
  [string]$ResearchWarehouseDb = "",
  [string]$FactorStateDb = "",
  [string]$OutputRoot = "",
  [int]$Port = 8091,
  [switch]$Serve
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param([scriptblock]$Command)
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code $LASTEXITCODE"
  }
}

if (-not (Test-Path -LiteralPath $SnapshotRoot -PathType Container)) {
  throw "SnapshotRoot does not exist: $SnapshotRoot"
}

if (Test-Path -LiteralPath (Join-Path $InstallRoot ".git")) {
  Push-Location $InstallRoot
  try {
    $dirty = git status --porcelain
    if ($dirty) {
      throw "InstallRoot contains uncommitted changes; refusing to overwrite it."
    }
    Invoke-Checked { git fetch origin $Branch }
    Invoke-Checked { git checkout $Branch }
    Invoke-Checked { git pull --ff-only origin $Branch }
  } finally {
    Pop-Location
  }
} elseif (Test-Path -LiteralPath $InstallRoot) {
  throw "InstallRoot exists but is not a Git repository: $InstallRoot"
} else {
  $parent = Split-Path -Parent $InstallRoot
  New-Item -ItemType Directory -Path $parent -Force | Out-Null
  Invoke-Checked { git clone --depth 1 --branch $Branch $Repository $InstallRoot }
}

$env:QUANT_AGENT_SNAPSHOT_ROOT = (Resolve-Path -LiteralPath $SnapshotRoot).Path
if ($ResearchWarehouseDb) {
  if (-not (Test-Path -LiteralPath $ResearchWarehouseDb -PathType Leaf)) {
    throw "ResearchWarehouseDb does not exist: $ResearchWarehouseDb"
  }
  $env:RESEARCH_WAREHOUSE_DB = (Resolve-Path -LiteralPath $ResearchWarehouseDb).Path
}
if ($FactorStateDb) {
  if (-not (Test-Path -LiteralPath $FactorStateDb -PathType Leaf)) {
    throw "FactorStateDb does not exist: $FactorStateDb"
  }
  $env:FACTOR_STATE_DB = (Resolve-Path -LiteralPath $FactorStateDb).Path
}
if ($OutputRoot) {
  if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
    throw "OutputRoot does not exist: $OutputRoot"
  }
  $env:QUANT_AGENT_OUTPUT_ROOT = (Resolve-Path -LiteralPath $OutputRoot).Path
}

Push-Location $InstallRoot
try {
  Invoke-Checked { python -m unittest agent_runtime.test_runtime -v }
  Invoke-Checked { python -m agent_runtime doctor }
  Invoke-Checked { python -m agent_runtime query asset-allocation current "画像=平衡" --compact }
  Invoke-Checked { python -m agent_runtime query industry-rotation ranking "频率=高频" "数量=3" --compact }

  $processId = $null
  if ($Serve) {
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
      throw "Port $Port is already in use."
    }
    $process = Start-Process -FilePath "python" `
      -ArgumentList @("-m", "agent_runtime", "serve", "--host", "127.0.0.1", "--port", "$Port") `
      -WorkingDirectory $InstallRoot `
      -WindowStyle Hidden `
      -PassThru
    $processId = $process.Id
    Start-Sleep -Seconds 1
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 10
    if ($health."状态" -ne "正常") {
      throw "Runtime health check failed."
    }
  }

  [ordered]@{
    status = "ok"
    repository = $Repository
    branch = $Branch
    commit = (git rev-parse HEAD)
    install_root = $InstallRoot
    snapshot_root = $env:QUANT_AGENT_SNAPSHOT_ROOT
    research_database = [bool]$env:RESEARCH_WAREHOUSE_DB
    factor_database = [bool]$env:FACTOR_STATE_DB
    output_root = [bool]$env:QUANT_AGENT_OUTPUT_ROOT
    service_url = if ($Serve) { "http://127.0.0.1:$Port" } else { $null }
    process_id = $processId
  } | ConvertTo-Json -Depth 4
} finally {
  Pop-Location
}
