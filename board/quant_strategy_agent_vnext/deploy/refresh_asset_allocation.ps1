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
  & $Python (Join-Path $ModelRoot "build_snapshot_v522.py") --database $Database --output $Candidate 2>&1 |
    Add-Content -LiteralPath $Log -Encoding utf8
  if ($LASTEXITCODE -ne 0) { throw "builder_exit:$LASTEXITCODE" }
  & $Python (Join-Path $ModelRoot "verify_asset_allocation_v522.py") $Candidate *> $null
  if ($LASTEXITCODE -ne 0) { throw "verifier_exit:$LASTEXITCODE" }

  $Payload = Get-Content -LiteralPath $Candidate -Raw -Encoding utf8 | ConvertFrom-Json
  if ([string]$Payload.schema_version -ne "5.2.2") {
    throw "candidate_schema_not_v522"
  }
  if ($Payload.status -ne "ready") {
    throw "candidate_service_status_not_ready"
  }
  if ($Payload.quality.status -ne "passed") {
    throw "candidate_quality_contract_not_passed"
  }
  if ($Payload.public_snapshot_sanitization.status -ne "passed" -or
      [int]$Payload.public_snapshot_sanitization.local_absolute_path_count -ne 0) {
    throw "candidate_public_snapshot_sanitization_failed"
  }
  if ($Payload.deployment_decision.status -ne "user_approved_sharpe_mandate") {
    throw "candidate_authorization_status_invalid"
  }
  if ($Payload.deployment_decision.deployable_dynamic_model -ne $true) {
    throw "candidate_dynamic_model_not_authorized"
  }
  if ($Payload.deployment_decision.executed_mode -ne "benchmark_relative") {
    throw "candidate_executed_mode_invalid"
  }
  if ($Payload.deployment_decision.authorization_basis -ne "explicit_user_approval_sharpe_only") {
    throw "candidate_authorization_basis_invalid"
  }
  if ($Payload.allocations.recommended_mode -ne "benchmark_relative") {
    throw "candidate_recommended_mode_invalid"
  }

  $ExpectedOrder = @("equity", "bond", "gold", "commodity")
  $ExpectedPolicyWeights = @(0.60, 0.15, 0.10, 0.15)
  $ActualOrder = @($Payload.benchmark.internal_asset_order)
  if ($ActualOrder.Count -ne $ExpectedOrder.Count) {
    throw "candidate_policy_anchor_order_invalid"
  }
  for ($Index = 0; $Index -lt $ExpectedOrder.Count; $Index++) {
    if ($ActualOrder[$Index] -ne $ExpectedOrder[$Index]) {
      throw "candidate_policy_anchor_order_invalid"
    }
    $Asset = $ExpectedOrder[$Index]
    $ActualWeight = [double]$Payload.benchmark.weights.$Asset
    if ([Math]::Abs($ActualWeight - $ExpectedPolicyWeights[$Index]) -gt 1e-12) {
      throw "candidate_policy_anchor_weight_invalid:$Asset"
    }
  }

  $DisplayBenchmark = $Payload.backtest.strategies.equal_weight_25
  if ($null -eq $DisplayBenchmark -or $DisplayBenchmark.id -ne "equal_weight_25") {
    throw "candidate_equal_weight_display_benchmark_missing"
  }
  if ($DisplayBenchmark.role -ne "nav_display_only_not_optimizer_input" -or
      $DisplayBenchmark.optimizer_input -ne $false -or
      $DisplayBenchmark.active_return_reference -ne $false) {
    throw "candidate_equal_weight_scope_invalid"
  }
  $DisplayCurrentWeights = @($DisplayBenchmark.current_weights)
  if ($DisplayCurrentWeights.Count -ne $ExpectedOrder.Count) {
    throw "candidate_equal_weight_current_weights_invalid"
  }
  for ($Index = 0; $Index -lt $ExpectedOrder.Count; $Index++) {
    $Asset = $ExpectedOrder[$Index]
    if ([Math]::Abs([double]$DisplayCurrentWeights[$Index] - 0.25) -gt 1e-12) {
      throw "candidate_equal_weight_value_invalid:$Asset"
    }
  }
  $DisplayContract = $Payload.backtest.display_benchmarks.equal_weight_25
  if ($null -eq $DisplayContract -or $DisplayContract.id -ne "equal_weight_25") {
    throw "candidate_equal_weight_display_contract_missing"
  }
  foreach ($Asset in $ExpectedOrder) {
    if ([Math]::Abs([double]$DisplayContract.weights.$Asset - 0.25) -gt 1e-12) {
      throw "candidate_equal_weight_contract_value_invalid:$Asset"
    }
  }

  if (Test-Path -LiteralPath $Output) {
    [System.IO.File]::Replace($Candidate, $Output, $null)
  } else {
    Move-Item -LiteralPath $Candidate -Destination $Output
  }
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
