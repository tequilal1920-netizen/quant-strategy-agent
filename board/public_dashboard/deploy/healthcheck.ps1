param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8070,
    [string]$ServiceTask = "ResearchMarketBoardService",
    [int]$RestartCooldownMinutes = 15
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath($Root)
$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir "health.jsonl"
$restartState = Join-Path $logDir "health_restart_state.json"
$liveUrl = "http://127.0.0.1:$Port/livez"
$healthUrl = "http://127.0.0.1:$Port/healthz"

function Write-HealthRecord {
    param([string]$Event, [hashtable]$Data = @{})
    $record = [ordered]@{ timestamp = (Get-Date -Format o); event = $Event }
    foreach ($key in $Data.Keys) { $record[$key] = $Data[$key] }
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ($record | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-DashboardEndpoint {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 12
        $payload = $null
        try { $payload = $response.Content | ConvertFrom-Json } catch { }
        return [pscustomobject]@{ Reachable = $true; HttpStatus = [int]$response.StatusCode; Payload = $payload }
    } catch {
        $response = $_.Exception.Response
        if ($null -ne $response) {
            $status = 0
            try { $status = [int]$response.StatusCode } catch { }
            $payload = $null
            try {
                if ($_.ErrorDetails.Message) { $payload = $_.ErrorDetails.Message | ConvertFrom-Json }
            } catch { }
            return [pscustomobject]@{ Reachable = $true; HttpStatus = $status; Payload = $payload }
        }
        return [pscustomobject]@{ Reachable = $false; HttpStatus = 0; Payload = $null }
    }
}

$live = Invoke-DashboardEndpoint -Url $liveUrl
if ($live.Reachable -and $live.HttpStatus -eq 200 -and $null -ne $live.Payload -and $live.Payload.status -eq "ok") {
    $quality = Invoke-DashboardEndpoint -Url $healthUrl
    if ($quality.Reachable -and $quality.HttpStatus -eq 200 -and $null -ne $quality.Payload -and $quality.Payload.status -eq "ok") {
        Write-HealthRecord -Event "healthy" -Data @{ catalog_rows = $quality.Payload.catalog_rows; generated_at = $quality.Payload.generated_at }
        exit 0
    }
    $state = if ($null -ne $quality.Payload) { $quality.Payload.data_state } else { "health_endpoint_invalid" }
    $failures = if ($null -ne $quality.Payload) { @($quality.Payload.failures) } else { @() }
    Write-HealthRecord -Event "quality_failed_no_restart" -Data @{ http_status = $quality.HttpStatus; data_state = $state; failures = $failures }
    exit 2
}

if ($live.Reachable) {
    $state = if ($null -ne $live.Payload) { $live.Payload.data_state } else { "live_endpoint_invalid" }
    Write-HealthRecord -Event "artifact_failed_no_restart" -Data @{ http_status = $live.HttpStatus; data_state = $state }
    exit 2
}

$nowUtc = [DateTime]::UtcNow
if (Test-Path -LiteralPath $restartState) {
    try {
        $saved = Get-Content -LiteralPath $restartState -Raw -Encoding UTF8 | ConvertFrom-Json
        $lastRestart = [DateTime]::Parse($saved.last_restart_utc).ToUniversalTime()
        if (($nowUtc - $lastRestart).TotalMinutes -lt $RestartCooldownMinutes) {
            Write-HealthRecord -Event "restart_suppressed_cooldown" -Data @{ cooldown_minutes = $RestartCooldownMinutes }
            exit 3
        }
    } catch {
        Write-HealthRecord -Event "restart_state_invalid" -Data @{}
    }
}

$task = Get-ScheduledTask -TaskName $ServiceTask -ErrorAction Stop
$scope = ($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join " "
if ($scope -notlike "*$Root*") {
    Write-HealthRecord -Event "restart_refused_wrong_task_scope" -Data @{ task = $ServiceTask }
    exit 4
}
Write-HealthRecord -Event "process_unreachable_restart" -Data @{ task = $ServiceTask }
if ($task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $ServiceTask
    Start-Sleep -Seconds 2
}
Start-ScheduledTask -TaskName $ServiceTask
@{ last_restart_utc = $nowUtc.ToString("o") } | ConvertTo-Json -Compress | Set-Content -LiteralPath $restartState -Encoding UTF8
Start-Sleep -Seconds 12
$recovered = Invoke-DashboardEndpoint -Url $liveUrl
if (-not ($recovered.Reachable -and $recovered.HttpStatus -eq 200 -and $null -ne $recovered.Payload -and $recovered.Payload.status -eq "ok")) {
    Write-HealthRecord -Event "restart_failed" -Data @{ task = $ServiceTask }
    exit 5
}
Write-HealthRecord -Event "restart_recovered" -Data @{ task = $ServiceTask }
exit 0
