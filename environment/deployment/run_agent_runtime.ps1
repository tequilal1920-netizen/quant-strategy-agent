param(
  [string]$InstallRoot = "",
  [Parameter(Mandatory = $true)]
  [string]$SnapshotRoot,
  [string]$ResearchWarehouseDb = "",
  [string]$FactorStateDb = "",
  [string]$OutputRoot = "",
  [string]$Python = "",
  [int]$Port = 8091
)

$ErrorActionPreference = "Stop"

if (-not $InstallRoot) {
  $InstallRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

function Resolve-PythonRuntime {
  param([string]$Requested)

  $Candidates = @()
  if ($Requested) { $Candidates += $Requested }
  if ($env:QUANT_AGENT_PYTHON) { $Candidates += $env:QUANT_AGENT_PYTHON }

  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) { $Candidates += $PythonCommand.Source }

  if ($env:LOCALAPPDATA) {
    $Candidates += Get-ChildItem `
      -Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe") `
      -File `
      -ErrorAction SilentlyContinue |
      Sort-Object FullName -Descending |
      ForEach-Object { $_.FullName }
  }

  foreach ($Candidate in $Candidates | Where-Object { $_ } | Select-Object -Unique) {
    if ($Candidate -like "*\WindowsApps\python.exe") { continue }
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
    & $Candidate --version *> $null
    if ($LASTEXITCODE -eq 0) {
      return (Resolve-Path -LiteralPath $Candidate).Path
    }
  }

  throw "A working Python runtime was not found. Pass -Python or set QUANT_AGENT_PYTHON."
}

foreach ($RequiredDirectory in @($InstallRoot, $SnapshotRoot)) {
  if (-not (Test-Path -LiteralPath $RequiredDirectory -PathType Container)) {
    throw "Required directory does not exist: $RequiredDirectory"
  }
}

if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot "agent_runtime\__main__.py") -PathType Leaf)) {
  throw "Agent runtime is missing from InstallRoot: $InstallRoot"
}

$env:PYTHONUTF8 = "1"
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

$PythonRuntime = Resolve-PythonRuntime $Python
Set-Location -LiteralPath $InstallRoot
& $PythonRuntime -m agent_runtime serve --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) {
  throw "Agent runtime exited with code $LASTEXITCODE."
}
