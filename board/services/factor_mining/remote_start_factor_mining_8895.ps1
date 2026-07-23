$ErrorActionPreference = "Stop"

$Root = "F:\apps\factor_mining_public_8895"
$App = Join-Path $Root "app.py"
$ModelScript = Join-Path $Root "factor_miner.py"
$PrivateEnv = "F:\apps\factor_mining_private\factor_mining_public.env"
$Port = 8895
$LogDir = Join-Path $Root "logs"
$OutLog = Join-Path $LogDir "server_8895.remote.out.log"
$ErrLog = Join-Path $LogDir "server_8895.remote.err.log"
$WarehouseDb = "F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$pythonCandidates = @(
  "C:\ProgramData\anaconda3\python.exe",
  "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe",
  "python"
)

$Python = $null
foreach ($candidate in $pythonCandidates) {
  if ($candidate -eq "python") {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
      $Python = $cmd.Source
      break
    }
  } elseif (Test-Path $candidate) {
    $Python = $candidate
    break
  }
}

if (-not $Python) { throw "No Python runtime found for FactorMining app." }
if (-not (Test-Path $App)) { throw "FactorMining app not found at $App" }
if (-not (Test-Path $ModelScript)) { throw "FactorMining model script not found at $ModelScript" }
if (-not (Test-Path $WarehouseDb)) { throw "Research warehouse not found at $WarehouseDb" }
if (-not (Test-Path $PrivateEnv)) { throw "Private env file not found at $PrivateEnv" }

"Supervisor started at $(Get-Date -Format s), python=$Python, port=$Port" | Out-File -FilePath $OutLog -Append -Encoding utf8

while ($true) {
  $env:FACTOR_APP_PORT = [string]$Port
  $env:FACTOR_APP_DB = $WarehouseDb
  $env:FACTOR_MINING_SCRIPT = $ModelScript
  $env:FACTOR_MINING_ENV_FILE = $PrivateEnv
  $env:FACTOR_PUBLIC_MAX_MONTHS = "180"
  $env:FACTOR_PUBLIC_MAX_BUDGET = "8"
  $env:FACTOR_PUBLIC_MAX_ITERATIONS = "8"
  $env:FACTOR_PUBLIC_MAX_CANDIDATES = "20"
  $env:FACTOR_JOB_TIMEOUT_SECONDS = "5400"
  $env:FACTOR_ALLOW_PUBLIC_FULL_RUN = "1"
  $env:FACTOR_REQUIRE_GPT = "1"
  $env:FACTOR_PANEL_CACHE_DIR = "F:\apps\factor_mining_private\panel_cache"

  $UserSite = "C:\Users\admin\AppData\Roaming\Python\Python312\site-packages"
  $PythonPathParts = @($Root)
  if (Test-Path $UserSite) { $PythonPathParts += $UserSite }
  if ($env:PYTHONPATH) { $PythonPathParts += $env:PYTHONPATH }
  $env:PYTHONPATH = ($PythonPathParts -join ";")

  "Launching FactorMining app with Waitress at $(Get-Date -Format s)" | Out-File -FilePath $OutLog -Append -Encoding utf8
  $ChildOut = Join-Path $LogDir "waitress_child.out.log"
  $ChildErr = Join-Path $LogDir "waitress_child.err.log"
  $Listen = "127.0.0.1:$Port"
  $proc = Start-Process -FilePath $Python -ArgumentList @("-m", "waitress", "--listen=$Listen", "--threads=4", "app:app") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $ChildOut -RedirectStandardError $ChildErr -PassThru
  "FactorMining child pid=$($proc.Id)" | Out-File -FilePath $OutLog -Append -Encoding utf8
  Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
  "App exited at $(Get-Date -Format s). Restarting in 5 seconds." | Out-File -FilePath $ErrLog -Append -Encoding utf8
  Start-Sleep -Seconds 5
}
