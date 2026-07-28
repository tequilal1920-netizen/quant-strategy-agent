$ErrorActionPreference = "Stop"

$CurrentRelease = "F:\apps\quant_strategy_agent_research_r21_2_ai_cache"
$AppRoot = Join-Path $CurrentRelease "board\quant_strategy_agent"
$Task = Get-ScheduledTask -TaskName "QuantStrategyAgent8071" -ErrorAction SilentlyContinue
$Ports = @()
foreach ($Port in 8070..8076) {
  $Listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
  $Ports += [pscustomobject]@{
    port = $Port
    listening = [bool]$Listener
    process_id = if ($Listener) { $Listener.OwningProcess } else { $null }
  }
}

$Python = Get-Command "D:\Download\Anaconda\python.exe" -ErrorAction SilentlyContinue
$Payload = [ordered]@{
  status = "ok"
  host = $env:COMPUTERNAME
  app_directories = @(
    Get-ChildItem -LiteralPath "F:\apps" -ErrorAction Stop |
      Where-Object { $_.PSIsContainer } |
      Select-Object -ExpandProperty Name
  )
  current_release = $CurrentRelease
  current_release_exists = Test-Path -LiteralPath $CurrentRelease
  current_app_exists = Test-Path -LiteralPath (Join-Path $AppRoot "main.py")
  current_private_env_exists = Test-Path -LiteralPath (Join-Path $AppRoot "private\quant_agent.env")
  current_database_exists = Test-Path -LiteralPath (Join-Path $CurrentRelease "database\research_warehouse.db")
  current_factor_worker_exists = Test-Path -LiteralPath (Join-Path $CurrentRelease "model\factor_laboratory\worker.py")
  python = if ($Python) { $Python.Source } else { $null }
  task = if ($Task) {
    [ordered]@{
      name = $Task.TaskName
      state = [string]$Task.State
      action = [string]$Task.Actions.Execute
      arguments = [string]$Task.Actions.Arguments
      working_directory = [string]$Task.Actions.WorkingDirectory
    }
  } else { $null }
  ports = $Ports
}

$Payload | ConvertTo-Json -Depth 6 -Compress
