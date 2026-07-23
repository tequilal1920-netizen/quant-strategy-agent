param(
  [Parameter(Mandatory = $true)]
  [string]$Root,
  [Parameter(Mandatory = $true)]
  [string]$Python,
  [int]$Port = 18070,
  [string]$Module = "app_r14_candidate"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if (-not (Test-Path -LiteralPath $Python)) {
  throw "Python runtime not found: $Python"
}

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$OutLog = Join-Path $LogDir ("preflight_dashboard_{0}.out.log" -f $Port)
$ErrLog = Join-Path $LogDir ("preflight_dashboard_{0}.err.log" -f $Port)
$Process = $null

try {
  $Process = Start-Process $Python -ArgumentList @(
    "-m",
    "waitress",
    ("--listen=127.0.0.1:{0}" -f $Port),
    "--threads=8",
    "--connection-limit=100",
    "--channel-timeout=120",
    ("{0}:app" -f $Module)
  ) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru

  $Health = $null
  for ($Attempt = 0; $Attempt -lt 30; $Attempt += 1) {
    try {
      $Health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/healthz" -f $Port) -TimeoutSec 3
      if ($Health.status) { break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $Health) { throw "Dashboard health check failed." }

  $Base = "http://127.0.0.1:{0}" -f $Port
  $Watch = [Diagnostics.Stopwatch]::StartNew()
  $Metadata = Invoke-WebRequest -UseBasicParsing -Uri ($Base + "/api/v1/snapshot?view=metadata") -Headers @{ "Accept-Encoding" = "gzip" } -TimeoutSec 30
  $Watch.Stop()
  $MetadataMilliseconds = $Watch.ElapsedMilliseconds
  $MetadataPayload = $Metadata.Content | ConvertFrom-Json

  $Watch.Restart()
  $Full = Invoke-WebRequest -UseBasicParsing -Uri ($Base + "/api/v1/snapshot") -Headers @{ "Accept-Encoding" = "gzip" } -TimeoutSec 30
  $Watch.Stop()
  $FullMilliseconds = $Watch.ElapsedMilliseconds

  if ($Metadata.StatusCode -ne 200 -or $Full.StatusCode -ne 200) {
    throw "Snapshot endpoint returned a non-200 status."
  }
  if ($MetadataPayload.modules.macro.series[0].PSObject.Properties.Name -contains "data") {
    throw "Metadata view still contains series data."
  }
  if ($Metadata.RawContentLength -ge $Full.RawContentLength) {
    throw "Metadata response is not smaller than the full snapshot."
  }

  [pscustomobject]@{
    Status = "passed"
    HealthStatus = $Health.status
    MetadataMilliseconds = $MetadataMilliseconds
    MetadataBytes = $Metadata.RawContentLength
    MetadataEncoding = $Metadata.Headers["Content-Encoding"]
    FullMilliseconds = $FullMilliseconds
    FullBytes = $Full.RawContentLength
    FullEncoding = $Full.Headers["Content-Encoding"]
    ReductionRatio = [math]::Round(1 - ($Metadata.RawContentLength / [double]$Full.RawContentLength), 6)
    StdErrBytes = (Get-Item -LiteralPath $ErrLog).Length
  } | ConvertTo-Json -Compress
} finally {
  $Connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Connection) {
    Stop-Process -Id $Connection.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  if ($Process -and -not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
  }
}
