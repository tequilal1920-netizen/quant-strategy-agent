param(
  [Parameter(Mandatory = $true)] [string]$FormalRoot,
  [Parameter(Mandatory = $true)] [string]$CandidateRoot,
  [string]$TaskName = "KlineAgentPublic8877",
  [int]$Port = 8877,
  [string]$ExpectedModel = "9.0-cohort-wyckoff-evolution"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$BaseUrl = "http://127.0.0.1:$Port"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupRoot = Join-Path (Join-Path $FormalRoot "deployment_backups") ("kline_cohort_r15_{0}" -f $Stamp)
$FormalAnalyzer = Join-Path $FormalRoot "single_stock_analyzer.py"
$FormalCohort = Join-Path $FormalRoot "cohort_wyckoff_learning.py"
$CandidateAnalyzer = Join-Path $CandidateRoot "single_stock_analyzer.py"
$CandidateCohort = Join-Path $CandidateRoot "cohort_wyckoff_learning.py"
$HadCohort = Test-Path -LiteralPath $FormalCohort
$CodeCopied = $false

function Wait-KlineHealth {
  param([string]$Expected = "", [int]$TimeoutSeconds = 90)
  $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    try {
      $Health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 4
      if ($Health.status -eq "ok" -and (-not $Expected -or $Health.model_version -eq $Expected)) { return $Health }
    } catch {}
    Start-Sleep -Milliseconds 700
  } while ((Get-Date) -lt $Deadline)
  throw "K-line health check timed out; expected model: $Expected"
}

function Stop-KlineService {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  for ($Attempt = 0; $Attempt -lt 40; $Attempt += 1) {
    $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $Connection) { return }
    Start-Sleep -Milliseconds 500
  }
  $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Connection) { Stop-Process -Id $Connection.OwningProcess -Force -ErrorAction Stop }
}

foreach ($RequiredPath in @($FormalAnalyzer, $CandidateAnalyzer, $CandidateCohort)) {
  if (-not (Test-Path -LiteralPath $RequiredPath)) { throw "Required deployment file is missing: $RequiredPath" }
}

$PreviousHealth = Wait-KlineHealth -TimeoutSeconds 15
try {
  Stop-KlineService
  New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
  Copy-Item -LiteralPath $FormalAnalyzer -Destination (Join-Path $BackupRoot "single_stock_analyzer.py") -Force
  if ($HadCohort) { Copy-Item -LiteralPath $FormalCohort -Destination (Join-Path $BackupRoot "cohort_wyckoff_learning.py") -Force }
  Copy-Item -LiteralPath $CandidateAnalyzer -Destination $FormalAnalyzer -Force
  Copy-Item -LiteralPath $CandidateCohort -Destination $FormalCohort -Force
  $CodeCopied = $true
  $AnalyzerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $CandidateAnalyzer).Hash
  $CohortHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $CandidateCohort).Hash
  if ($AnalyzerHash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $FormalAnalyzer).Hash) { throw "Analyzer source hash mismatch after deployment." }
  if ($CohortHash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $FormalCohort).Hash) { throw "Cohort source hash mismatch after deployment." }
  Start-ScheduledTask -TaskName $TaskName
  $Health = Wait-KlineHealth -Expected $ExpectedModel -TimeoutSeconds 120
  $Stocks = Invoke-RestMethod -Uri "$BaseUrl/api/stocks?limit=3&q=000001" -TimeoutSec 20
  $Dates = Invoke-RestMethod -Uri "$BaseUrl/api/dates?code=000001.SZ" -TimeoutSec 20
  [pscustomobject]@{
    status = "passed"; previous_model = $PreviousHealth.model_version; model_version = $Health.model_version
    gpt_configured = $Health.gpt_configured; db_exists = $Health.db_exists
    stock_count = @($Stocks.stocks).Count; date_count = @($Dates.dates).Count
    source_hash_match = $true; cohort_hash_match = $true; auth_database_untouched = $true; backup = $BackupRoot
  } | ConvertTo-Json -Compress
} catch {
  $OriginalFailure = $_.Exception.Message
  $RollbackFailure = $null
  $RollbackModel = $null
  try {
    Stop-KlineService
    if ($CodeCopied -and (Test-Path -LiteralPath (Join-Path $BackupRoot "single_stock_analyzer.py"))) {
      Copy-Item -LiteralPath (Join-Path $BackupRoot "single_stock_analyzer.py") -Destination $FormalAnalyzer -Force
      if ($HadCohort) { Copy-Item -LiteralPath (Join-Path $BackupRoot "cohort_wyckoff_learning.py") -Destination $FormalCohort -Force }
      else { Remove-Item -LiteralPath $FormalCohort -Force -ErrorAction SilentlyContinue }
    }
    Start-ScheduledTask -TaskName $TaskName
    $RollbackModel = (Wait-KlineHealth -TimeoutSeconds 120).model_version
  } catch { $RollbackFailure = $_.Exception.Message }
  if ($RollbackFailure) { throw "K-line deployment failed: $OriginalFailure; rollback also failed: $RollbackFailure" }
  throw "K-line deployment failed and rollback restored $RollbackModel`: $OriginalFailure"
}
