$ErrorActionPreference = "Stop"

$NewRoot = [IO.Path]::GetFullPath(
  "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense"
)
$OldRoot = [IO.Path]::GetFullPath(
  "F:\apps\quant_strategy_agent_vnext_r24_3_analysis_first"
)
$AppsRoot = [IO.Path]::GetFullPath("F:\apps")
$NewTaskName = "QuantStrategyAgentVNext8076R254"
$OldTaskName = "QuantStrategyAgentVNext8075R243"
$ExpectedVersion = "2026.07.27-five-panel-dense-vnext-r25.4"
$ExpectedCurrentVersion = "2026.07.27-scoped-controls-ai-cache-r21.2"

foreach ($Root in @($NewRoot, $OldRoot)) {
  if (-not $Root.StartsWith($AppsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Release root escaped F:\apps."
  }
}
$Health = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8076/healthz" `
  -TimeoutSec 15
if ($Health.version -ne $ExpectedVersion) {
  throw "New vNext health is not the expected release."
}
$CurrentHealth = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8071/healthz" `
  -TimeoutSec 15
if ($CurrentHealth.version -ne $ExpectedCurrentVersion) {
  throw "Current /quant-agent baseline changed."
}
$NewTask = Get-ScheduledTask -TaskName $NewTaskName -ErrorAction Stop
if ([string]$NewTask.State -ne "Running") {
  throw "New vNext task is not running."
}
if (
  -not ([string]$NewTask.Actions.WorkingDirectory).StartsWith(
    $NewRoot,
    [StringComparison]::OrdinalIgnoreCase
  )
) {
  throw "New task does not point to the R25.4 release."
}
$Serve = (& tailscale funnel status --json) | ConvertFrom-Json
$Host443 = "desktop-i22b489.tailf9d7ac.ts.net:443"
$Host10000 = "desktop-i22b489.tailf9d7ac.ts.net:10000"
if (
  $Serve.Web.$Host443.Handlers."/quant-agent".Proxy -ne
    "http://127.0.0.1:8071/quant-agent"
) {
  throw "Current public proxy changed."
}
if (
  $Serve.Web.$Host10000.Handlers."/quant-agent-vnext".Proxy -ne
    "http://127.0.0.1:8076/quant-agent-vnext"
) {
  throw "New public vNext proxy is unexpected."
}

$OldTask = Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
if ($OldTask) {
  Stop-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask `
    -TaskName $OldTaskName `
    -Confirm:$false `
    -ErrorAction Stop
}
$OldProcesses = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine.IndexOf(
        $OldRoot,
        [StringComparison]::OrdinalIgnoreCase
      ) -ge 0
    }
)
foreach ($Process in $OldProcesses) {
  Stop-Process -Id $Process.ProcessId -Force -ErrorAction Stop
}
Start-Sleep -Seconds 2
$OldListener = Get-NetTCPConnection `
  -State Listen `
  -LocalPort 8075 `
  -ErrorAction SilentlyContinue |
  Select-Object -First 1
if ($OldListener) {
  $OldProcess = Get-CimInstance `
    Win32_Process `
    -Filter ("ProcessId=" + $OldListener.OwningProcess)
  $OldParent = Get-CimInstance `
    Win32_Process `
    -Filter ("ProcessId=" + $OldProcess.ParentProcessId)
  if (
    $OldProcess.Name -ne "python.exe" -or
    $OldProcess.CommandLine -notmatch "--port=8075\s+main:app" -or
    -not $OldParent.CommandLine -or
    $OldParent.CommandLine.IndexOf(
      $OldRoot,
      [StringComparison]::OrdinalIgnoreCase
    ) -lt 0
  ) {
    throw "Refusing to stop an unexpected process on port 8075."
  }
  Stop-Process -Id $OldProcess.ProcessId -Force -ErrorAction Stop
  Stop-Process `
    -Id $OldParent.ProcessId `
    -Force `
    -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  $OldListener = Get-NetTCPConnection `
    -State Listen `
    -LocalPort 8075 `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($OldListener) {
    throw "Old vNext port 8075 is still listening after guarded cleanup."
  }
}
if (Test-Path -LiteralPath $OldRoot) {
  Remove-Item -LiteralPath $OldRoot -Recurse -Force
}

$TempRoot = [IO.Path]::GetFullPath(
  [Environment]::GetFolderPath("LocalApplicationData") + "\Temp"
)
foreach ($Name in @(
  "quant_strategy_agent_vnext_r24_3_analysis_first_20260727.zip",
  "quant_strategy_agent_vnext_r25_4_five_panel_dense_20260727.zip",
  "deploy_vnext_r254_isolated_remote.ps1",
  "deploy_vnext_isolated_remote.ps1",
  "preflight_vnext_r254_remote.ps1",
  "switch_vnext_r254_remote.ps1",
  "cleanup_superseded_vnext_r254_remote.ps1"
)) {
  $Path = [IO.Path]::GetFullPath((Join-Path $TempRoot $Name))
  if (-not $Path.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Temporary cleanup path escaped the temporary directory."
  }
  if (Test-Path -LiteralPath $Path) {
    Remove-Item -LiteralPath $Path -Force
  }
}

$FinalHealth = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8076/healthz" `
  -TimeoutSec 15
$FinalCurrent = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8071/healthz" `
  -TimeoutSec 15
[ordered]@{
  status = "clean"
  current_version = $FinalCurrent.version
  vnext_version = $FinalHealth.version
  new_release_exists = Test-Path -LiteralPath $NewRoot
  old_release_exists = Test-Path -LiteralPath $OldRoot
  new_task_state = [string](
    Get-ScheduledTask -TaskName $NewTaskName -ErrorAction Stop
  ).State
  old_task_exists = [bool](
    Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
  )
  new_proxy = (
    ((& tailscale funnel status --json) | ConvertFrom-Json).
      Web.$Host10000.Handlers."/quant-agent-vnext".Proxy
  )
} | ConvertTo-Json -Depth 4 -Compress
