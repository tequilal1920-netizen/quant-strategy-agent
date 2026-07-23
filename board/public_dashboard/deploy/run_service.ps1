param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8070,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath($Root)
if (-not (Test-Path -LiteralPath (Join-Path $Root "app.py"))) {
    throw "app.py not found under $Root"
}
if (-not $Python) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe" }
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python executable not found"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -like "*$Root*" -and $process.CommandLine -match "waitress") {
        exit 0
    }
    throw "Port $Port is already owned by another process"
}

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:DASHBOARD_DATA_DIR = Join-Path $Root "data"
$env:DASHBOARD_BIND = "127.0.0.1"
$env:DASHBOARD_PORT = [string]$Port
Set-Location -LiteralPath $Root

$stdout = Join-Path $logDir "service.out.log"
$stderr = Join-Path $logDir "service.err.log"
$args = @(
    "-m", "waitress",
    "--listen=127.0.0.1:$Port",
    "--threads=8",
    "--connection-limit=100",
    "--channel-timeout=120",
    "--ident=ResearchMarketBoard",
    "app:app"
)
$process = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -Wait
exit $process.ExitCode