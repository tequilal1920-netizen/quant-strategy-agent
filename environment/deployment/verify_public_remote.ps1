param(
  [Parameter(Mandatory = $true)]
  [string]$AppRoot,
  [Parameter(Mandatory = $true)]
  [string]$BaseUrl,
  [string]$ExpectedVersion = "2026.07.23-research-workspace-r16.3",
  [string]$ExpectedKlineModel = "9.0-cohort-wyckoff-evolution"
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
  throw "Public verification credentials are missing from the private env."
}

$Health = Invoke-RestMethod -Uri ($BaseUrl + "/healthz") -TimeoutSec 20
if ($Health.status -ne "ok" -or $Health.version -ne $ExpectedVersion) {
  throw "Public health/version validation failed."
}

$Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$LoginWatch = [Diagnostics.Stopwatch]::StartNew()
$Login = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl + "/login") -Method Post -Body @{
  username = $PrivateValues["QUANT_AGENT_USER"]
  password = $PrivateValues["QUANT_AGENT_PASSWORD"]
} -WebSession $Session -MaximumRedirection 5 -TimeoutSec 30
$LoginWatch.Stop()
if ($Login.StatusCode -ne 200 -or $Login.Content -notmatch "app\.js") {
  throw "Public login validation failed."
}
foreach ($Obsolete in @("factor_lab_v2", "rotation_module_v4", "index_enhancement_v2")) {
  if ($Login.Content -match $Obsolete) { throw "Obsolete asset reference remains: $Obsolete" }
}
$ExpectedNavigationLabels = @(
  "5Li76aG1",
  "5pWw5o2u55yL5p2/",
  "6LWE5Lqn6YWN572u",
  "6LWE6YeR6Z2i6Lef6Liq",
  "6KGM5Lia5pmv5rCU5bqm",
  "5Zug5a2Q5a6e6aqM5a6k",
  "5oqA5pyv5YiG5p6Q",
  "57uE5ZCI5LyY5YyW"
) | ForEach-Object {
  [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_))
}
foreach ($ExpectedNavigation in $ExpectedNavigationLabels) {
  if ($Login.Content -notmatch [regex]::Escape($ExpectedNavigation)) {
    throw "Expected navigation group is missing: $ExpectedNavigation"
  }
}

$Checks = @(
  @{ Name = "services"; Url = "/api/services"; MaxBytes = 10000 },
  @{ Name = "board"; Url = "/api/board/snapshot"; MaxBytes = 500000 },
  @{ Name = "allocation"; Url = "/api/allocation/snapshot"; MaxBytes = 2000000 },
  @{ Name = "rotation"; Url = "/api/rotation/snapshot"; MaxBytes = 3000000 },
  @{ Name = "factor_lab"; Url = "/api/factor-lab/health"; MaxBytes = 10000 },
  @{ Name = "kline_health"; Url = "/api/kline/health"; MaxBytes = 10000 },
  @{ Name = "kline_session"; Url = "/api/kline/session"; MaxBytes = 10000 },
  @{ Name = "kline_stocks"; Url = "/api/kline/stocks?limit=3&q=000001"; MaxBytes = 20000 },
  @{ Name = "kline_dates"; Url = "/api/kline/dates?code=000001.SZ"; MaxBytes = 200000 },
  @{ Name = "kline_history"; Url = "/api/kline/history?limit=3"; MaxBytes = 500000 },
  @{ Name = "factor_status"; Url = "/api/factor/status"; MaxBytes = 20000 },
  @{ Name = "factor_history"; Url = "/api/factor/history"; MaxBytes = 500000 }
)
$Results = @()
foreach ($Check in $Checks) {
  $Watch = [Diagnostics.Stopwatch]::StartNew()
  $Response = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl + $Check.Url) -WebSession $Session -Headers @{
    "Accept-Encoding" = "gzip"
  } -TimeoutSec 45
  $Watch.Stop()
  if ($Response.StatusCode -ne 200 -or $Response.RawContentLength -ge $Check.MaxBytes) {
    throw ("Public check failed: {0}" -f $Check.Name)
  }
  $Results += [pscustomobject]@{
    Name = $Check.Name
    Status = $Response.StatusCode
    Milliseconds = $Watch.ElapsedMilliseconds
    Bytes = $Response.RawContentLength
    Encoding = $Response.Headers["Content-Encoding"]
  }
}

$KlineHealthPayload = Invoke-RestMethod -Uri ($BaseUrl + "/api/kline/health") -WebSession $Session -TimeoutSec 20
$KlineSessionPayload = Invoke-RestMethod -Uri ($BaseUrl + "/api/kline/session") -WebSession $Session -TimeoutSec 20
if ($KlineHealthPayload.model_version -ne $ExpectedKlineModel) {
  throw "Public K-line model version validation failed."
}
if (-not $KlineSessionPayload.authenticated) {
  throw "Public K-line authenticated session validation failed."
}

[pscustomobject]@{
  Status = "passed"
  Version = $Health.version
  KlineModel = $KlineHealthPayload.model_version
  KlineAuthenticated = $KlineSessionPayload.authenticated
  LoginMilliseconds = $LoginWatch.ElapsedMilliseconds
  Checks = $Results
  CredentialsOutput = $false
} | ConvertTo-Json -Depth 6 -Compress
