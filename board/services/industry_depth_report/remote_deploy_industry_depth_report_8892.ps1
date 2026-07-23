$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Target = "F:\apps\industry_depth_report_public_8892"
$Zip = Join-Path $env:USERPROFILE "industry_depth_report_public_8892.zip"
$TaskName = "IndustryDepthReportPublic8892"
$LocalPort = 8892
$PublicPort = 10004
$PersistentDataDir = "F:\data\industry_depth_report"
$PersistentDb = Join-Path $PersistentDataDir "research_warehouse.db"

if (-not (Test-Path $Zip)) {
  throw "Deployment zip not found: $Zip"
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

Get-CimInstance Win32_Process -Filter "name='powershell.exe'" |
  Where-Object { $_.CommandLine -like "*remote_start_industry_depth_report_8892.ps1*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Get-CimInstance Win32_Process -Filter "name='python.exe'" |
  Where-Object { $_.CommandLine -like "*industry_depth_report_public_8892*app.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 2

New-Item -ItemType Directory -Force -Path $PersistentDataDir | Out-Null
$TargetDb = Join-Path $Target "database\research_warehouse.db"
if ((Test-Path $TargetDb) -and (-not (Test-Path $PersistentDb))) {
  Move-Item -LiteralPath $TargetDb -Destination $PersistentDb
  Write-Output "Moved existing warehouse to persistent data path $PersistentDb"
}

if (Test-Path $Target) {
  $backup = $Target + ".backup_" + (Get-Date -Format "yyyyMMdd_HHmmss")
  Move-Item -LiteralPath $Target -Destination $backup
  Write-Output "Backed up existing target to $backup"
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $Target -Force

$StartScript = Join-Path $Target "board\services\industry_depth_report\remote_start_industry_depth_report_8892.ps1"
if (-not (Test-Path $StartScript)) {
  throw "Start script not found after extraction: $StartScript"
}

if ($existingTask) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "Industry depth report agent on local port $LocalPort and Funnel port $PublicPort" `
  -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

$deadline = (Get-Date).AddMinutes(45)
do {
  Start-Sleep -Seconds 5
  $listener = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue
  if ($listener) {
    break
  }
  $err = Join-Path $Target "output\logs\industry_depth_report\server_8892.remote.err.log"
  if (Test-Path $err) {
    Get-Content -LiteralPath $err -Tail 30
  }
} while ((Get-Date) -lt $deadline)

if (-not $listener) {
  throw "IndustryDepthReport did not start listening on local port $LocalPort before timeout."
}

$tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if (-not $tailscale) {
  $tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
}
if (-not $tailscale) {
  throw "tailscale command not found; local service is running but public Funnel was not configured."
}

& $tailscale.Source funnel --bg --yes --https $PublicPort "http://127.0.0.1:$LocalPort"

Write-Output "Deployment complete."
Write-Output "Local service: http://127.0.0.1:$LocalPort"
Write-Output "Public URL: https://desktop-i22b489.tailf9d7ac.ts.net:$PublicPort"
Write-Output "Task: $TaskName"
Write-Output "--- Tailscale Serve Status ---"
& $tailscale.Source serve status
