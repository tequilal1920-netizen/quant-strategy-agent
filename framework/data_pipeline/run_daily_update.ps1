param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$SourceDb = $env:SOURCE_DB,
  [string]$WarehouseDb = "",
  [switch]$SkipWarehouseBuild,
  [switch]$AllowIncompleteModels,
  [int]$TushareMaxCalls = 0
)

$ErrorActionPreference = "Stop"
$Python = "python"
$WarehouseDb = if ($WarehouseDb) { $WarehouseDb } else { Join-Path $ProjectRoot "database\research_warehouse.db" }
$OutDir = Join-Path $ProjectRoot "output\framework\backtest\model_outputs_daily"
$QualityOutDir = Join-Path $ProjectRoot "output\framework\data_quality"

if (-not $SkipWarehouseBuild -and -not $SourceDb) {
  throw "SourceDb is required unless -SkipWarehouseBuild is used. Pass -SourceDb or set SOURCE_DB."
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $QualityOutDir -Force | Out-Null

Set-Location $ProjectRoot

if (-not $SkipWarehouseBuild) {
  & $Python "framework\data_pipeline\build_warehouse.py" --source-db $SourceDb --out-db $WarehouseDb --project-root $ProjectRoot
}

& $Python "framework\data_quality\quality_gate.py" --db $WarehouseDb --out (Join-Path $QualityOutDir "data_quality_gate.before_external.json")

if ($env:TUSHARE_TOKEN) {
  $maxArgs = @()
  if ($TushareMaxCalls -gt 0) {
    $maxArgs = @("--max-calls", "$TushareMaxCalls")
  }
  & $Python "framework\data_pipeline\connectors\tushare_connector.py" --db $WarehouseDb --mode market_gap --start 20120101 --end 20260630 --source-db $SourceDb @maxArgs
  & $Python "framework\data_pipeline\connectors\tushare_connector.py" --db $WarehouseDb --mode index_weight --start 20120101 --end 20260630 @maxArgs
  & $Python "framework\data_pipeline\connectors\tushare_connector.py" --db $WarehouseDb --mode lhb --start 20120101 --end 20260630 @maxArgs
} else {
  Write-Host "TUSHARE_TOKEN is not set; external gap-fill steps skipped."
}

& $Python "framework\data_quality\quality_gate.py" --db $WarehouseDb --out (Join-Path $QualityOutDir "data_quality_gate.json")

$modelArgs = @("--db", $WarehouseDb, "--project-root", $ProjectRoot, "--out-dir", $OutDir)
if ($AllowIncompleteModels) {
  $modelArgs += "--allow-incomplete"
}
& $Python "framework\backtest\run_v2_models.py" @modelArgs
