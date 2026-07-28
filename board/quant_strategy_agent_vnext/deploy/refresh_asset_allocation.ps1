$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent $PSScriptRoot
$ModelRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $AppRoot)) "model\asset_allocation"
$Database = $env:ASSET_ALLOCATION_DATABASE
if (-not $Database) {
  $Database = "F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db"
}
$Python = $env:QUANT_AGENT_PYTHON
if (-not $Python) {
  $Python = "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
}
$Output = Join-Path $AppRoot "data\asset_allocation_snapshot.json"
$Candidate = Join-Path $AppRoot "data\asset_allocation_snapshot.candidate.json"
$Log = Join-Path $PSScriptRoot "asset_allocation_refresh.log"
$Mutex = [Threading.Mutex]::new($false, "Global\QuantAssetAllocationRefresh")
$Acquired = $false

try {
  $Acquired = $Mutex.WaitOne(0)
  if (-not $Acquired) { throw "refresh_already_running" }
  if (-not (Test-Path -LiteralPath $Database)) { throw "warehouse_missing:$Database" }
  if (-not (Test-Path -LiteralPath $Python)) { throw "python_missing:$Python" }
  $StartedAt = (Get-Date).ToString("s")
  "[$StartedAt] refresh started" | Add-Content -LiteralPath $Log -Encoding utf8
  & $Python (Join-Path $ModelRoot "build_snapshot.py") --database $Database --output $Candidate 2>&1 |
    Add-Content -LiteralPath $Log -Encoding utf8
  if ($LASTEXITCODE -ne 0) { throw "builder_exit:$LASTEXITCODE" }
  $Payload = Get-Content -LiteralPath $Candidate -Raw -Encoding utf8 | ConvertFrom-Json
  if ($Payload.status -ne "ready" -or $Payload.quality.status -ne "passed") {
    throw "candidate_quality_gate_failed"
  }
  Move-Item -LiteralPath $Candidate -Destination $Output -Force
  "[$((Get-Date).ToString('s'))] refresh passed; market=$($Payload.data_as_of.market); macro=$($Payload.data_as_of.macro_complete)" |
    Add-Content -LiteralPath $Log -Encoding utf8
} catch {
  "[$((Get-Date).ToString('s'))] refresh failed: $($_.Exception.Message)" |
    Add-Content -LiteralPath $Log -Encoding utf8
  if (Test-Path -LiteralPath $Candidate) { Remove-Item -LiteralPath $Candidate -Force }
  throw
} finally {
  if ($Acquired) { $Mutex.ReleaseMutex() }
  $Mutex.Dispose()
}
