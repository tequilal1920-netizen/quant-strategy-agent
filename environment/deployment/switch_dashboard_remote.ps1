param(
  [Parameter(Mandatory = $true)]
  [string]$Root,
  [Parameter(Mandatory = $true)]
  [string]$ExpectedSha256,
  [string]$CandidateName = "app_r14_candidate.py",
  [string]$TaskName = "ResearchMarketBoardService",
  [int]$Port = 8070
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = [IO.Path]::GetFullPath($Root)
$Candidate = [IO.Path]::GetFullPath((Join-Path $Root $CandidateName))
$Active = [IO.Path]::GetFullPath((Join-Path $Root "app.py"))
$BackupRoot = [IO.Path]::GetFullPath((Join-Path $Root (
  "deployment_backups\metadata_r14_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
)))

foreach ($Path in @($Candidate, $Active, $BackupRoot)) {
  if (-not $Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Deployment path escaped dashboard root: $Path"
  }
}
if (-not (Test-Path -LiteralPath $Candidate)) { throw "Candidate app is missing." }
if (-not (Test-Path -LiteralPath $Active)) { throw "Active app is missing." }

$CandidateHash = (Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash.ToLowerInvariant()
if ($CandidateHash -ne $ExpectedSha256.ToLowerInvariant()) {
  throw "Candidate SHA-256 mismatch."
}

New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
Copy-Item -LiteralPath $Active -Destination (Join-Path $BackupRoot "app.py") -Force
Export-ScheduledTask -TaskName $TaskName | Out-File -LiteralPath (
  Join-Path $BackupRoot ($TaskName + ".xml")
) -Encoding unicode

$Switched = $false
try {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  for ($Attempt = 0; $Attempt -lt 30; $Attempt += 1) {
    if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Seconds 1
  }
  $Remaining = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($Remaining) {
    Stop-Process -Id $Remaining.OwningProcess -Force
    Start-Sleep -Seconds 1
  }

  Copy-Item -LiteralPath $Candidate -Destination $Active -Force
  Move-Item -LiteralPath $Candidate -Destination (Join-Path $BackupRoot $CandidateName)
  $Switched = $true
  Start-ScheduledTask -TaskName $TaskName

  $Health = $null
  for ($Attempt = 0; $Attempt -lt 45; $Attempt += 1) {
    try {
      $Health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/healthz" -f $Port) -TimeoutSec 3
      if ($Health.status) { break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $Health) { throw "Dashboard did not return health after switching." }

  $Metadata = Invoke-WebRequest -UseBasicParsing -Uri (
    "http://127.0.0.1:{0}/api/v1/snapshot?view=metadata" -f $Port
  ) -TimeoutSec 30
  $MetadataPayload = $Metadata.Content | ConvertFrom-Json
  if ($Metadata.StatusCode -ne 200 -or $Metadata.RawContentLength -ge 500000) {
    throw "Metadata response contract failed after switching."
  }
  if ($MetadataPayload.modules.macro.series[0].PSObject.Properties.Name -contains "data") {
    throw "Metadata response still contains chart point arrays."
  }

  [pscustomobject]@{
    Status = "switched"
    Task = $TaskName
    Port = $Port
    CandidateSha256 = $CandidateHash
    BackupRoot = $BackupRoot
    HealthStatus = $Health.status
    MetadataBytes = $Metadata.RawContentLength
  } | ConvertTo-Json -Compress
} catch {
  if ($Switched) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath (Join-Path $BackupRoot "app.py") -Destination $Active -Force
    Start-ScheduledTask -TaskName $TaskName
  }
  throw
}
