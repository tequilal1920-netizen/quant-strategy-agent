param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8070
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath($Root)
$serviceName = "ResearchMarketBoardService"
$updateName = "ResearchMarketBoardDailyUpdate"
$newsName = "ResearchMarketBoardNewsUpdate"
$healthName = "ResearchMarketBoardHealth"
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

$serviceScript = Join-Path $Root "deploy\run_service.ps1"
$updateScript = Join-Path $Root "deploy\run_update.ps1"
$healthScript = Join-Path $Root "deploy\healthcheck.ps1"
$servicePython = Join-Path $Root ".venv\Scripts\python.exe"
foreach ($script in @($serviceScript, $updateScript, $healthScript)) {
    if (-not (Test-Path -LiteralPath $script)) { throw "Missing deployment script: $script" }
}
if (-not (Test-Path -LiteralPath $servicePython)) { throw "Missing project virtual-environment Python: $servicePython" }

$serviceArguments = "-m waitress --listen=127.0.0.1:$Port --threads=8 --connection-limit=100 --channel-timeout=120 --ident=ResearchMarketBoard app:app"
$serviceAction = New-ScheduledTaskAction -Execute $servicePython -Argument $serviceArguments -WorkingDirectory $Root
$serviceTrigger = New-ScheduledTaskTrigger -AtStartup
$serviceSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $serviceName -Action $serviceAction -Trigger $serviceTrigger -Principal $principal -Settings $serviceSettings -Description "Public research market board on localhost:$Port" -Force | Out-Null

$updateAction = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$updateScript`" -Root `"$Root`" -Mode Full -Force -MaxAttempts 2 -RetryDelaySeconds 45"
$updateTrigger = New-ScheduledTaskTrigger -Daily -At "18:35"
$updateSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $updateName -Action $updateAction -Trigger $updateTrigger -Principal $principal -Settings $updateSettings -Description "Daily free-data refresh for public research market board" -Force | Out-Null

$newsAction = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$updateScript`" -Root `"$Root`" -Mode News -Force -MaxAttempts 2 -RetryDelaySeconds 30"
$newsTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) -RepetitionInterval (New-TimeSpan -Minutes 60) -RepetitionDuration (New-TimeSpan -Days 3650)
$newsSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 25)
Register-ScheduledTask -TaskName $newsName -Action $newsAction -Trigger $newsTrigger -Principal $principal -Settings $newsSettings -Description "Hourly bounded public-news refresh; other modules are merged from the last valid snapshot" -Force | Out-Null

$healthAction = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$healthScript`" -Root `"$Root`" -Port $Port -ServiceTask `"$serviceName`" -RestartCooldownMinutes 15"
$healthTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$healthSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $healthName -Action $healthAction -Trigger $healthTrigger -Principal $principal -Settings $healthSettings -Description "Health check for public research market board only" -Force | Out-Null

Start-ScheduledTask -TaskName $serviceName
foreach ($taskName in @($serviceName, $updateName, $newsName, $healthName)) {
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
}
