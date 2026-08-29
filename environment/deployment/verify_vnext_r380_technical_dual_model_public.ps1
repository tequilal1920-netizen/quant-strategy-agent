param(
  [string]$AppRoot = "F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor\board\quant_strategy_agent_vnext",
  [string]$BaseUrl = "https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext",
  [string]$ExpectedVersion = "2026.08.17-technical-dual-model-vnext-r38.0",
  [string]$ExpectedGovernanceRelease = "2026.08.17-technical-dual-model-governed-r38.0"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$BaseUrl = $BaseUrl.TrimEnd("/")
$PrivateEnvPath = Join-Path $AppRoot "private\quant_agent.env"

$PrivateValues = @{}
foreach ($RawLine in Get-Content -LiteralPath $PrivateEnvPath -Encoding utf8) {
  $Line = $RawLine.Trim()
  if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) { continue }
  $Parts = $Line.Split("=", 2)
  $PrivateValues[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
}
if (-not $PrivateValues["QUANT_AGENT_USER"] -or -not $PrivateValues["QUANT_AGENT_PASSWORD"]) {
  throw "public_verification_credentials_missing"
}

$Health = Invoke-RestMethod -Uri ($BaseUrl + "/healthz") -TimeoutSec 20
if ($Health.status -ne "ok" -or $Health.version -ne $ExpectedVersion) {
  throw "public_health_version_failed:$($Health.version)"
}

$Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$LoginWatch = [Diagnostics.Stopwatch]::StartNew()
$Login = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl + "/login") -Method Post -Body @{
  username = $PrivateValues["QUANT_AGENT_USER"]
  password = $PrivateValues["QUANT_AGENT_PASSWORD"]
} -WebSession $Session -MaximumRedirection 5 -TimeoutSec 30
$LoginWatch.Stop()
if ($Login.StatusCode -ne 200 -or $Login.Content -notmatch "app\.js") {
  throw "public_login_failed"
}

$Evidence = Invoke-RestMethod -Uri ($BaseUrl + "/api/research-evidence?route=technical%3Akline") -WebSession $Session -TimeoutSec 30
if (
  $Evidence.module -ne "kline" -or
  $Evidence.governance.selection_uses_test -or
  $Evidence.governance.pure_technical_selection_uses_test -or
  $Evidence.governance.release_approved -or
  $Evidence.governance.pure_technical_release_approved
) {
  throw "public_technical_evidence_governance_failed"
}
$Rows = $Evidence.visuals.diagnostics.table.rows
if ($Rows.Count -lt 8) { throw "public_technical_rows_missing" }
if ($Rows[0].sharpe -le 1.5 -or $Rows[1].sharpe -le 1.5 -or $Rows[2].sharpe -ge 0) {
  throw "public_model2_metrics_failed"
}
if ($Rows[4].sharpe -le 1.5 -or $Rows[5].sharpe -le 1.5 -or $Rows[6].sharpe -ge 0) {
  throw "public_model1_metrics_failed"
}

$Governance = Invoke-RestMethod -Uri ($BaseUrl + "/api/model-governance") -WebSession $Session -TimeoutSec 30
$Kline = $Governance.models.kline_memory
if (
  $Governance.release -ne $ExpectedGovernanceRelease -or
  $Kline.gate -ne "research_diagnostic" -or
  $Kline.engine -notmatch "technical-signal-stack/1\.0" -or
  $Kline.engine -notmatch "kline-multiscale-expert/1\.6" -or
  $Kline.robustness.pure_technical_release_guard.selection_uses_test -or
  $Kline.robustness.pure_technical_release_guard.release_approved -or
  $Kline.robustness.pure_technical_release_guard.sealed_test.sharpe -ge 0
) {
  throw "public_model_governance_failed"
}

[pscustomobject]@{
  status = "passed"
  version = $Health.version
  governance_release = $Governance.release
  login_ms = $LoginWatch.ElapsedMilliseconds
  model1_train_sharpe = $Rows[4].sharpe
  model1_valid_sharpe = $Rows[5].sharpe
  model1_test_sharpe = $Rows[6].sharpe
  model2_train_sharpe = $Rows[0].sharpe
  model2_valid_sharpe = $Rows[1].sharpe
  model2_test_sharpe = $Rows[2].sharpe
  credentials_output = $false
} | ConvertTo-Json -Depth 5 -Compress
