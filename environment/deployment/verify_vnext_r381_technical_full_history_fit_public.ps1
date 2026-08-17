param(
  [string]$AppRoot = "F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor\board\quant_strategy_agent_vnext",
  [string]$BaseUrl = "https://desktop-i22b489.tailf9d7ac.ts.net:10000/quant-agent-vnext",
  [string]$ExpectedVersion = "2026.08.17-technical-full-history-fit-vnext-r38.1",
  [string]$ExpectedGovernanceRelease = "2026.08.17-technical-full-history-fit-governed-r38.1"
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
if (-not $PrivateValues["QUANT_AGENT_USER"] -or -not $PrivateValues["QUANT_AGENT_PASSWORD"]) { throw "public_verification_credentials_missing" }

$Health = Invoke-RestMethod -Uri ($BaseUrl + "/healthz") -TimeoutSec 20
if ($Health.status -ne "ok" -or $Health.version -ne $ExpectedVersion) { throw "public_health_version_failed:$($Health.version)" }

$Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$LoginWatch = [Diagnostics.Stopwatch]::StartNew()
$Login = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl + "/login") -Method Post -Body @{
  username = $PrivateValues["QUANT_AGENT_USER"]
  password = $PrivateValues["QUANT_AGENT_PASSWORD"]
} -WebSession $Session -MaximumRedirection 5 -TimeoutSec 30
$LoginWatch.Stop()
if ($Login.StatusCode -ne 200 -or $Login.Content -notmatch "app\.js") { throw "public_login_failed" }

$Evidence = Invoke-RestMethod -Uri ($BaseUrl + "/api/research-evidence?route=technical%3Akline") -WebSession $Session -TimeoutSec 30
if (
  $Evidence.module -ne "kline" -or
  $Evidence.governance.selection_uses_test -or
  $Evidence.governance.full_history_sample_split_used -or
  $Evidence.governance.full_history_holdout_validation_claimed -or
  -not $Evidence.governance.full_history_candidate
) { throw "public_technical_evidence_governance_failed" }
$Rows = $Evidence.visuals.diagnostics.table.rows
if ($Rows.Count -lt 12) { throw "public_technical_rows_missing" }

$Governance = Invoke-RestMethod -Uri ($BaseUrl + "/api/model-governance") -WebSession $Session -TimeoutSec 30
$Kline = $Governance.models.kline_memory
$FullGuard = $Kline.robustness.full_history_fit_guard
if (
  $Governance.release -ne $ExpectedGovernanceRelease -or
  $Kline.gate -ne "research_diagnostic" -or
  $Kline.engine -notmatch "technical-signal-stack/1\.1" -or
  $FullGuard.sample_split_used -or
  $FullGuard.holdout_validation_claimed -or
  $FullGuard.current_position_count -lt 20 -or
  $FullGuard.full_history_metrics.sharpe -le 0
) { throw "public_model_governance_failed" }

[pscustomobject]@{
  status = "passed"
  version = $Health.version
  governance_release = $Governance.release
  login_ms = $LoginWatch.ElapsedMilliseconds
  full_history_candidate = $Evidence.governance.full_history_candidate
  full_history_signal_date = $Evidence.governance.full_history_current_signal_date
  full_history_sharpe = $FullGuard.full_history_metrics.sharpe
  full_history_turnover = $FullGuard.full_history_metrics.turnover
  current_position_count = $FullGuard.current_position_count
  credentials_output = $false
} | ConvertTo-Json -Depth 6 -Compress
