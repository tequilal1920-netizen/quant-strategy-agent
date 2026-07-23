$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$env:INDUSTRY_REPORT_PROJECT_ROOT = $Root
$env:INDUSTRY_REPORT_DB = Join-Path $Root "database\research_warehouse.db"
$env:INDUSTRY_REPORT_OUTPUT_ROOT = Join-Path $Root "output\industry_depth_report"
$env:INDUSTRY_REPORT_HOST = "127.0.0.1"
$env:INDUSTRY_REPORT_PORT = "8892"
$Python = if ($env:INDUSTRY_REPORT_PYTHON) { $env:INDUSTRY_REPORT_PYTHON } else { (Get-Command python -ErrorAction Stop).Source }
& $Python (Join-Path $PSScriptRoot "app.py")

