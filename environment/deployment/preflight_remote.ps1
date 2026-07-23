param(
  [Parameter(Mandatory = $true)]
  [string]$AppRoot,
  [Parameter(Mandatory = $true)]
  [string]$Database,
  [string]$Python = "",
  [string]$FactorPython = "",
  [int]$Port = 18072,
  [string]$ExpectedVersion = "2026.07.23-research-workspace-r16.3"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if (-not $Python) {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) { $Python = $PythonCommand.Source }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
  throw "Python runtime not found."
}
if (-not (Test-Path -LiteralPath $Database)) {
  throw "Research warehouse not found: $Database"
}

$env:QUANT_AGENT_PYTHON = $Python
$env:QUANT_AGENT_ENV_FILE = Join-Path $AppRoot "private\quant_agent.env"
$env:FACTOR_LAB_DB = $Database
if ($FactorPython) { $env:FACTOR_LAB_PYTHON = $FactorPython }
$env:HOST = "127.0.0.1"
$env:PORT = [string]$Port

$LogDir = Join-Path $AppRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$OutLog = Join-Path $LogDir ("preflight_{0}.out.log" -f $Port)
$ErrLog = Join-Path $LogDir ("preflight_{0}.err.log" -f $Port)
$Launcher = $null

try {
  $Launcher = Start-Process powershell.exe -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    (Join-Path $AppRoot "deploy\run_service.ps1")
  ) -WorkingDirectory $AppRoot -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru

  $Health = $null
  for ($Attempt = 0; $Attempt -lt 45; $Attempt += 1) {
    try {
      $Health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/healthz" -f $Port) -TimeoutSec 3
      if ($Health.status -eq "ok") { break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $Health -or $Health.version -ne $ExpectedVersion) {
    throw "Preflight health/version failed."
  }

  $PrivateEnv = @{}
  foreach ($RawLine in Get-Content -LiteralPath $env:QUANT_AGENT_ENV_FILE -Encoding utf8) {
    $Line = $RawLine.Trim()
    if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) { continue }
    $Parts = $Line.Split("=", 2)
    $PrivateEnv[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
  }
  if (-not $PrivateEnv["QUANT_AGENT_USER"] -or -not $PrivateEnv["QUANT_AGENT_PASSWORD"]) {
    throw "Private login variables are missing."
  }

  $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $Login = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/login" -f $Port) -Method Post -Body @{
    username = $PrivateEnv["QUANT_AGENT_USER"]
    password = $PrivateEnv["QUANT_AGENT_PASSWORD"]
  } -WebSession $Session -MaximumRedirection 5 -TimeoutSec 15
  if ($Login.StatusCode -ne 200 -or $Login.Content -notmatch "app\.js") {
    throw "Authenticated shell failed."
  }

  $Checks = @(
    @{ Name = "services"; Url = "/api/services" },
    @{ Name = "board"; Url = "/api/board/snapshot" },
    @{ Name = "allocation"; Url = "/api/allocation/snapshot" },
    @{ Name = "rotation"; Url = "/api/rotation/snapshot" },
    @{ Name = "factor_lab"; Url = "/api/factor-lab/health" }
  )
  $Results = @()
  foreach ($Check in $Checks) {
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}{1}" -f $Port, $Check.Url) -WebSession $Session -Headers @{ "Accept-Encoding" = "gzip" } -TimeoutSec 30
    $Watch.Stop()
    if ($Response.StatusCode -ne 200) {
      throw ("{0} returned HTTP {1}" -f $Check.Name, $Response.StatusCode)
    }
    $Results += [pscustomobject]@{
      Name = $Check.Name
      Status = $Response.StatusCode
      Milliseconds = $Watch.ElapsedMilliseconds
      Bytes = $Response.RawContentLength
      Encoding = $Response.Headers["Content-Encoding"]
    }
  }

  [pscustomobject]@{
    Status = "passed"
    Version = $Health.version
    LoginStatus = $Login.StatusCode
    Results = $Results
    StdErrBytes = (Get-Item -LiteralPath $ErrLog).Length
  } | ConvertTo-Json -Depth 6 -Compress
} finally {
  $Connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Connection) {
    Stop-Process -Id $Connection.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  if ($Launcher -and -not $Launcher.HasExited) {
    Stop-Process -Id $Launcher.Id -Force -ErrorAction SilentlyContinue
  }
}
