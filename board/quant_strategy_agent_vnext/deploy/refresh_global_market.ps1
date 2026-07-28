$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $PSScriptRoot
$Python = $env:QUANT_AGENT_PYTHON
if (-not $Python) { $Python = "F:\apps\research_market_board\.venv\Scripts\python.exe" }
$Output = Join-Path $AppRoot "data\global_market_snapshot.json"
$Candidate = Join-Path $AppRoot "data\global_market_snapshot.candidate.json"
$Log = Join-Path $PSScriptRoot "global_market_refresh.log"
$Mutex = [Threading.Mutex]::new($false, "Global\QuantGlobalMarketRefresh")
$Acquired = $false
try {
  $Acquired = $Mutex.WaitOne(0)
  if (-not $Acquired) { throw "refresh_already_running" }
  if (-not (Test-Path -LiteralPath $Python)) { throw "python_missing:$Python" }
  "[$((Get-Date).ToString('s'))] refresh started" | Add-Content -LiteralPath $Log -Encoding utf8
  Set-Location $AppRoot
  & $Python (Join-Path $PSScriptRoot "refresh_global_market.py") --output $Candidate 2>&1 |
    Add-Content -LiteralPath $Log -Encoding utf8
  if ($LASTEXITCODE -ne 0) { throw "builder_exit:$LASTEXITCODE" }
  $Payload = Get-Content -LiteralPath $Candidate -Raw -Encoding utf8 | ConvertFrom-Json
  if ($Payload.status -ne "ok" -or $Payload.rows.Count -ne 10 -or $Payload.series.Count -ne 10) {
    throw "candidate_quality_gate_failed"
  }
  Move-Item -LiteralPath $Candidate -Destination $Output -Force
  "[$((Get-Date).ToString('s'))] refresh passed; as_of=$($Payload.as_of); rows=$($Payload.rows.Count)" |
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