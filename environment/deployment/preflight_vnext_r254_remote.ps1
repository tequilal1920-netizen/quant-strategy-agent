$ErrorActionPreference = "Stop"

$NewRelease = "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense"
$OldRelease = "F:\apps\quant_strategy_agent_vnext_r24_3_analysis_first"
$NewTask = "QuantStrategyAgentVNext8076R254"
$OldTask = "QuantStrategyAgentVNext8075R243"
$CurrentHealth = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8071/healthz" `
  -TimeoutSec 15
$OldHealth = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8075/healthz" `
  -TimeoutSec 15
$NewListener = Get-NetTCPConnection `
  -State Listen `
  -LocalPort 8076 `
  -ErrorAction SilentlyContinue |
  Select-Object -First 1
$OldListener = Get-NetTCPConnection `
  -State Listen `
  -LocalPort 8075 `
  -ErrorAction SilentlyContinue |
  Select-Object -First 1
$Serve = (& tailscale funnel status --json) | ConvertFrom-Json
$Host443 = "desktop-i22b489.tailf9d7ac.ts.net:443"
$Host10000 = "desktop-i22b489.tailf9d7ac.ts.net:10000"

if ($CurrentHealth.version -ne "2026.07.27-scoped-controls-ai-cache-r21.2") {
  throw "Current /quant-agent baseline changed."
}
if ($OldHealth.version -ne "2026.07.27-analysis-first-visuals-vnext-r24.3") {
  throw "Old vNext baseline changed."
}
if (-not (Test-Path -LiteralPath $OldRelease)) {
  throw "Old vNext release is missing."
}
if (-not (Get-ScheduledTask -TaskName $OldTask -ErrorAction SilentlyContinue)) {
  throw "Old vNext scheduled task is missing."
}
if (-not $OldListener) {
  throw "Old vNext port 8075 is not listening."
}
if (Test-Path -LiteralPath $NewRelease) {
  throw "New vNext release root already exists."
}
if (Get-ScheduledTask -TaskName $NewTask -ErrorAction SilentlyContinue) {
  throw "New vNext scheduled task already exists."
}
if ($NewListener) {
  throw "New vNext port 8076 is already occupied."
}
if (
  $Serve.Web.$Host443.Handlers."/quant-agent".Proxy -ne
    "http://127.0.0.1:8071/quant-agent"
) {
  throw "Current /quant-agent Funnel proxy changed."
}
if (
  $Serve.Web.$Host10000.Handlers."/quant-agent-vnext".Proxy -ne
    "http://127.0.0.1:8075/quant-agent-vnext"
) {
  throw "Old vNext Funnel proxy changed."
}

[ordered]@{
  status = "ready"
  current_version = $CurrentHealth.version
  old_vnext_version = $OldHealth.version
  old_vnext_task = $OldTask
  old_vnext_port = 8075
  new_release_absent = -not (Test-Path -LiteralPath $NewRelease)
  new_task_absent = -not [bool](
    Get-ScheduledTask -TaskName $NewTask -ErrorAction SilentlyContinue
  )
  new_port_free = -not [bool]$NewListener
  current_proxy = $Serve.Web.$Host443.Handlers."/quant-agent".Proxy
  vnext_proxy = $Serve.Web.$Host10000.Handlers."/quant-agent-vnext".Proxy
} | ConvertTo-Json -Depth 4 -Compress
