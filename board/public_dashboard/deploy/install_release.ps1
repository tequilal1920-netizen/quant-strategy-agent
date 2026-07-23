param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8070,
    [string]$SystemPython = "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath($Root)
if (-not (Test-Path -LiteralPath (Join-Path $Root "verify_release.py"))) { throw "Invalid release root" }
if (-not (Test-Path -LiteralPath $SystemPython)) { throw "System Python not found" }
Set-Location -LiteralPath $Root

& $SystemPython (Join-Path $Root "verify_release.py")
if ($LASTEXITCODE -ne 0) { throw "Release validation failed" }

$venv = Join-Path $Root ".venv"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    & $SystemPython -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
& $python -m pip install --disable-pip-version-check --ignore-installed -r (Join-Path $Root "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }

& (Join-Path $Root "deploy\run_update.ps1") -Root $Root -Python $python -Force
if ($LASTEXITCODE -ne 0) { throw "Initial data update failed" }

& $python -m ipykernel install --user --name research-market-board --display-name "Research Market Board"
if ($LASTEXITCODE -ne 0) { throw "Notebook kernel installation failed" }
& $python (Join-Path $Root "deploy\execute_notebook.py") --root $Root --kernel research-market-board
if ($LASTEXITCODE -ne 0) { throw "Notebook execution failed" }

& (Join-Path $Root "deploy\install_tasks.ps1") -Root $Root -Port $Port
if ($LASTEXITCODE -ne 0) { throw "Scheduled task installation failed" }

$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 3
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 8
        if ($response.StatusCode -eq 200) { break }
    } catch {}
} while ((Get-Date) -lt $deadline)
if (-not $response -or $response.StatusCode -ne 200) { throw "Local service health check timed out" }
& $python (Join-Path $Root "deploy\verify_http.py") --base "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) { throw "Full local HTTP release gate failed" }
$response.Content