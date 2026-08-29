$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent $PSScriptRoot
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $AppRoot)

function Import-PrivateEnv([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  foreach ($RawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
    $Line = $RawLine.Trim()
    if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) { continue }
    $Parts = $Line.Split("=", 2)
    $Name = $Parts[0].Trim()
    $Value = $Parts[1].Trim().Trim('"').Trim("'")
    if ($Name -and -not [Environment]::GetEnvironmentVariable($Name, "Process")) {
      [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
  }
}

$EnvFile = $env:QUANT_AGENT_ENV_FILE
if (-not $EnvFile) { $EnvFile = Join-Path $AppRoot "private\quant_agent.env" }
Import-PrivateEnv $EnvFile

foreach ($Name in @("QUANT_AGENT_USER", "QUANT_AGENT_PASSWORD", "QUANT_AGENT_SECRET")) {
  if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
    throw "$Name must be configured in $EnvFile or the server environment."
  }
}

$Python = $env:QUANT_AGENT_PYTHON
if (-not $Python) {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) { $Python = $PythonCommand.Source }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
  throw "Python runtime not found. Configure QUANT_AGENT_PYTHON."
}
if (-not $env:RESEARCH_WAREHOUSE_DB) {
  if ($env:FACTOR_LAB_DB) {
    $env:RESEARCH_WAREHOUSE_DB = $env:FACTOR_LAB_DB
  } else {
    $env:RESEARCH_WAREHOUSE_DB = Join-Path $ProjectRoot "database\research_warehouse.db"
  }
}
if (-not $env:OPTIMIZER_STATE_DB) {
  $env:OPTIMIZER_STATE_DB = Join-Path $ProjectRoot "state\optimizer_state.db"
}
$OptimizerStateRoot = Split-Path -Parent $env:OPTIMIZER_STATE_DB
if (-not (Test-Path -LiteralPath $OptimizerStateRoot)) {
  New-Item -ItemType Directory -Path $OptimizerStateRoot -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $env:RESEARCH_WAREHOUSE_DB)) {
  throw "Optimizer research warehouse not found: $env:RESEARCH_WAREHOUSE_DB"
}

if (-not $env:HOST) { $env:HOST = "127.0.0.1" }
if (-not $env:PORT) { $env:PORT = "8071" }
if (-not $env:FACTOR_LAB_DB) { $env:FACTOR_LAB_DB = Join-Path $ProjectRoot "database\research_warehouse.db" }

& $Python -c "from scipy.optimize import milp; assert callable(milp)"
if ($LASTEXITCODE -ne 0) {
  throw "scipy.optimize.milp with the HiGHS backend is required for exact support selection."
}
& $Python -c "import cvxpy as cp; assert 'CLARABEL' in cp.installed_solvers()"
if ($LASTEXITCODE -ne 0) {
  throw "cvxpy with the CLARABEL solver is required for full SOCP certification."
}
if (-not $env:FACTOR_LAB_ENGINE) { $env:FACTOR_LAB_ENGINE = Join-Path $ProjectRoot "model\factor_laboratory\worker.py" }

if (-not (Test-Path -LiteralPath $env:FACTOR_LAB_DB)) { throw "Research warehouse not found: $env:FACTOR_LAB_DB" }
if (-not (Test-Path -LiteralPath $env:FACTOR_LAB_ENGINE)) { throw "Factor Laboratory engine not found: $env:FACTOR_LAB_ENGINE" }

Set-Location $AppRoot
& $Python -m waitress --host=$env:HOST --port=$env:PORT main:app
if ($LASTEXITCODE -ne 0) { throw "Quant Strategy Agent exited with code $LASTEXITCODE." }
