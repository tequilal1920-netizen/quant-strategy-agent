param(
  [Parameter(Mandatory = $true)]
  [string]$NewAppRoot,
  [Parameter(Mandatory = $true)]
  [string]$Python,
  [Parameter(Mandatory = $true)]
  [string]$Database,
  [Parameter(Mandatory = $true)]
  [string]$FactorPython,
  [string]$TaskName = "QuantStrategyAgent8071",
  [int]$Port = 8071,
  [string]$ExpectedVersion = "2026.07.23-research-workspace-r16.3"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$NewAppRoot = [IO.Path]::GetFullPath($NewAppRoot)
$AppsRoot = [IO.Path]::GetFullPath("F:\apps")
if (-not $NewAppRoot.StartsWith($AppsRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "New application root escaped the apps directory."
}

$Main = Join-Path $NewAppRoot "main.py"
$StartScript = Join-Path $NewAppRoot "deploy\run_service.ps1"
$Worker = [IO.Path]::GetFullPath((Join-Path $NewAppRoot "..\..\model\factor_laboratory\worker.py"))
$PrivateEnv = Join-Path $NewAppRoot "private\quant_agent.env"
foreach ($Required in @($Main, $StartScript, $Worker, $PrivateEnv, $Python, $Database, $FactorPython)) {
  if (-not (Test-Path -LiteralPath $Required)) { throw "Required deployment file is missing: $Required" }
}

function Set-PrivateEnvValue([string]$Path, [string]$Name, [string]$Value) {
  $Lines = [Collections.Generic.List[string]]::new()
  $Found = $false
  foreach ($RawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
    if ($RawLine -match ("^\s*" + [regex]::Escape($Name) + "\s*=")) {
      $Lines.Add($Name + "=" + $Value)
      $Found = $true
    } else {
      $Lines.Add($RawLine)
    }
  }
  if (-not $Found) { $Lines.Add($Name + "=" + $Value) }
  [IO.File]::WriteAllLines($Path, $Lines, (New-Object Text.UTF8Encoding($false)))
}

Set-PrivateEnvValue $PrivateEnv "QUANT_AGENT_PYTHON" $Python
Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_DB" $Database
Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_PYTHON" $FactorPython
Set-PrivateEnvValue $PrivateEnv "HOST" "127.0.0.1"
Set-PrivateEnvValue $PrivateEnv "PORT" ([string]$Port)

$Task = Get-ScheduledTask -TaskName $TaskName
$OriginalActions = $Task.Actions
$BackupRoot = [IO.Path]::GetFullPath((
  "F:\apps\quant_strategy_agent\deployment_backups\research_workspace_r16_3_switch_{0}" -f
  (Get-Date -Format "yyyyMMdd_HHmmss")
))
if (-not $BackupRoot.StartsWith($AppsRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Backup path escaped the apps directory."
}
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
Export-ScheduledTask -TaskName $TaskName | Out-File -LiteralPath (
  Join-Path $BackupRoot ($TaskName + ".xml")
) -Encoding unicode

$Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $StartScript
$NewAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $NewAppRoot
$Changed = $false

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

  Set-ScheduledTask -TaskName $TaskName -Action $NewAction | Out-Null
  $Changed = $true
  Start-ScheduledTask -TaskName $TaskName

  $Health = $null
  for ($Attempt = 0; $Attempt -lt 45; $Attempt += 1) {
    try {
      $Health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/healthz" -f $Port) -TimeoutSec 3
      if ($Health.version -eq $ExpectedVersion) { break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $Health -or $Health.version -ne $ExpectedVersion) {
    throw "Canonical application did not return the expected version."
  }

  $PrivateValues = @{}
  foreach ($RawLine in Get-Content -LiteralPath $PrivateEnv -Encoding utf8) {
    $Line = $RawLine.Trim()
    if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) { continue }
    $Parts = $Line.Split("=", 2)
    $PrivateValues[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
  }
  $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $Login = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/login" -f $Port) -Method Post -Body @{
    username = $PrivateValues["QUANT_AGENT_USER"]
    password = $PrivateValues["QUANT_AGENT_PASSWORD"]
  } -WebSession $Session -MaximumRedirection 5 -TimeoutSec 15
  if ($Login.StatusCode -ne 200 -or $Login.Content -notmatch "app\.js") {
    throw "Canonical login validation failed."
  }

  $Watch = [Diagnostics.Stopwatch]::StartNew()
  $Board = Invoke-WebRequest -UseBasicParsing -Uri (
    "http://127.0.0.1:{0}/api/board/snapshot" -f $Port
  ) -WebSession $Session -Headers @{ "Accept-Encoding" = "gzip" } -TimeoutSec 30
  $Watch.Stop()
  if ($Board.StatusCode -ne 200 -or $Board.RawContentLength -ge 500000) {
    throw "Canonical board metadata proxy validation failed."
  }
  $Services = Invoke-WebRequest -UseBasicParsing -Uri (
    "http://127.0.0.1:{0}/api/services" -f $Port
  ) -WebSession $Session -TimeoutSec 30
  if ($Services.StatusCode -ne 200) { throw "Canonical service health validation failed." }

  $Current = Get-NetTCPConnection -State Listen -LocalPort $Port | Select-Object -First 1
  $Process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $Current.OwningProcess)
  [pscustomobject]@{
    Status = "switched"
    Version = $Health.version
    Task = $TaskName
    Port = $Port
    ProcessId = $Current.OwningProcess
    CommandLine = $Process.CommandLine
    BoardMilliseconds = $Watch.ElapsedMilliseconds
    BoardBytes = $Board.RawContentLength
    BackupRoot = $BackupRoot
  } | ConvertTo-Json -Compress
} catch {
  if ($Changed) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Set-ScheduledTask -TaskName $TaskName -Action $OriginalActions | Out-Null
    Start-ScheduledTask -TaskName $TaskName
  }
  throw
}
