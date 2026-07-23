param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "",
    [ValidateSet("Full", "News")][string]$Mode = "Full",
    [switch]$Force,
    [ValidateRange(1, 3)][int]$MaxAttempts = 2,
    [ValidateRange(0, 300)][int]$RetryDelaySeconds = 45
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath($Root)
if (-not $Python) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe" }
}
if (-not (Test-Path -LiteralPath $Python)) { throw "Python executable not found" }
$pipeline = Join-Path $Root "pipeline.py"
$catalogSeed = Join-Path $Root "data\indicator_catalog.seed.json"
$dataDir = Join-Path $Root "data"
if (-not (Test-Path -LiteralPath $pipeline)) { throw "pipeline.py not found" }
if (-not (Test-Path -LiteralPath $catalogSeed)) { throw "indicator catalog seed not found" }

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$qualityLog = Join-Path $logDir "update_quality.jsonl"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$modeName = $Mode.ToLowerInvariant()
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:DASHBOARD_DATA_DIR = $dataDir
$env:DASHBOARD_CATALOG_PATH = $catalogSeed
$env:DASHBOARD_CACHE_TTL_SECONDS = "72000"
$env:DASHBOARD_ENABLE_TUSHARE = "0"
$env:DASHBOARD_ENABLE_IFIND = "0"
$env:DASHBOARD_ENABLE_WIND = "0"
Set-Location -LiteralPath $Root

$baseArgs = @($pipeline, "update", "--data-dir", $dataDir, "--catalog", $catalogSeed)
if ($Force) { $baseArgs += "--force" }
if ($Mode -eq "News") { $baseArgs += @("--modules", "news_events") }

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    $stdout = Join-Path $logDir "update_${stamp}_${modeName}_attempt${attempt}.out.log"
    $stderr = Join-Path $logDir "update_${stamp}_${modeName}_attempt${attempt}.err.log"
    $process = Start-Process -FilePath $Python -ArgumentList $baseArgs -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -Wait
    $updateExitCode = [int]$process.ExitCode
    $validateExitCode = -1
    if ($updateExitCode -eq 0) {
        & $Python $pipeline validate --data-dir $dataDir
        $validateExitCode = [int]$LASTEXITCODE
    }
    $passed = $updateExitCode -eq 0 -and $validateExitCode -eq 0
    $record = [ordered]@{
        timestamp = (Get-Date -Format o)
        mode = $modeName
        attempt = $attempt
        max_attempts = $MaxAttempts
        update_exit_code = $updateExitCode
        validate_exit_code = $validateExitCode
        status = if ($passed) { "passed" } else { "failed" }
    }
    Add-Content -LiteralPath $qualityLog -Encoding UTF8 -Value ($record | ConvertTo-Json -Compress)
    if ($passed) { exit 0 }
    if ($attempt -lt $MaxAttempts -and $RetryDelaySeconds -gt 0) {
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}
throw "Dashboard $modeName update failed after $MaxAttempts bounded attempts; inspect project logs"
