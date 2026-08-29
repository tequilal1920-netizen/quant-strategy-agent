param(
  [string]$Repository = "https://github.com/tequilal1920-netizen/quant-strategy-agent.git",
  [string]$Branch = "agent/industry-style-r16-6",
  [string]$InstallRoot = "F:\apps\quant_strategy_agent_github_runtime",
  [Parameter(Mandatory = $true)]
  [string]$SnapshotRoot,
  [string]$ResearchWarehouseDb = "",
  [string]$FactorStateDb = "",
  [string]$OutputRoot = "",
  [string]$Python = "",
  [int]$Port = 8091,
  [switch]$Serve,
  [switch]$Persistent,
  [string]$TaskName = "",
  [switch]$UseExisting,
  [string]$SourceCommit = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param([scriptblock]$Command)
  $PreviousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $Command
    $ExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $PreviousPreference
  }
  if ($ExitCode -ne 0) {
    throw "Command failed with exit code $ExitCode"
  }
}

function Resolve-PythonRuntime {
  param([string]$Requested)

  $Candidates = @()
  if ($Requested) { $Candidates += $Requested }
  if ($env:QUANT_AGENT_PYTHON) { $Candidates += $env:QUANT_AGENT_PYTHON }

  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) { $Candidates += $PythonCommand.Source }

  if ($env:LOCALAPPDATA) {
    $Candidates += Get-ChildItem `
      -Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe") `
      -File `
      -ErrorAction SilentlyContinue |
      Sort-Object FullName -Descending |
      ForEach-Object { $_.FullName }
  }

  foreach ($Candidate in $Candidates | Where-Object { $_ } | Select-Object -Unique) {
    if ($Candidate -like "*\WindowsApps\python.exe") { continue }
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
    & $Candidate --version *> $null
    if ($LASTEXITCODE -eq 0) {
      return (Resolve-Path -LiteralPath $Candidate).Path
    }
  }

  throw "A working Python runtime was not found. Pass -Python or set QUANT_AGENT_PYTHON."
}

function Quote-TaskArgument {
  param([string]$Value)
  return '"' + $Value.Replace('"', '\"') + '"'
}

if (-not (Test-Path -LiteralPath $SnapshotRoot -PathType Container)) {
  throw "SnapshotRoot does not exist: $SnapshotRoot"
}

if (Test-Path -LiteralPath (Join-Path $InstallRoot ".git")) {
  Push-Location $InstallRoot
  try {
    $dirty = git status --porcelain
    if ($dirty) {
      throw "InstallRoot contains uncommitted changes; refusing to overwrite it."
    }
    Invoke-Checked { git fetch origin $Branch }
    Invoke-Checked { git checkout $Branch }
    Invoke-Checked { git pull --ff-only origin $Branch }
  } finally {
    Pop-Location
  }
} elseif (Test-Path -LiteralPath $InstallRoot) {
  $RuntimeEntry = Join-Path $InstallRoot "agent_runtime\__main__.py"
  if (-not $UseExisting -or -not (Test-Path -LiteralPath $RuntimeEntry -PathType Leaf)) {
    throw "InstallRoot exists but is not a Git repository. Pass -UseExisting only for a verified GitHub archive deployment: $InstallRoot"
  }
} else {
  $parent = Split-Path -Parent $InstallRoot
  New-Item -ItemType Directory -Path $parent -Force | Out-Null
  Invoke-Checked { git clone --depth 1 --branch $Branch $Repository $InstallRoot }
}

$env:QUANT_AGENT_SNAPSHOT_ROOT = (Resolve-Path -LiteralPath $SnapshotRoot).Path
$env:PYTHONUTF8 = "1"
if ($ResearchWarehouseDb) {
  if (-not (Test-Path -LiteralPath $ResearchWarehouseDb -PathType Leaf)) {
    throw "ResearchWarehouseDb does not exist: $ResearchWarehouseDb"
  }
  $env:RESEARCH_WAREHOUSE_DB = (Resolve-Path -LiteralPath $ResearchWarehouseDb).Path
}
if ($FactorStateDb) {
  if (-not (Test-Path -LiteralPath $FactorStateDb -PathType Leaf)) {
    throw "FactorStateDb does not exist: $FactorStateDb"
  }
  $env:FACTOR_STATE_DB = (Resolve-Path -LiteralPath $FactorStateDb).Path
}
if ($OutputRoot) {
  if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
    throw "OutputRoot does not exist: $OutputRoot"
  }
  $env:QUANT_AGENT_OUTPUT_ROOT = (Resolve-Path -LiteralPath $OutputRoot).Path
}

Push-Location $InstallRoot
try {
  $PythonRuntime = Resolve-PythonRuntime $Python
  Invoke-Checked { & $PythonRuntime -m unittest agent_runtime.test_runtime -v }
  Invoke-Checked { & $PythonRuntime -m agent_runtime doctor --strict }
  Invoke-Checked { & $PythonRuntime -m agent_runtime query asset-allocation current "profile=balanced" --compact }
  Invoke-Checked { & $PythonRuntime -m agent_runtime query industry-rotation ranking "frequency=high_frequency" "limit=3" --compact }

  $processId = $null
  $registeredTask = $null
  if ($Serve) {
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
      throw "Port $Port is already in use."
    }

    $Runner = Join-Path $InstallRoot "environment\deployment\run_agent_runtime.ps1"
    $RunnerArguments = @(
      "-NoProfile",
      "-ExecutionPolicy", "Bypass",
      "-File", (Quote-TaskArgument $Runner),
      "-InstallRoot", (Quote-TaskArgument $InstallRoot),
      "-SnapshotRoot", (Quote-TaskArgument $env:QUANT_AGENT_SNAPSHOT_ROOT),
      "-Python", (Quote-TaskArgument $PythonRuntime),
      "-Port", "$Port"
    )
    if ($env:RESEARCH_WAREHOUSE_DB) {
      $RunnerArguments += @(
        "-ResearchWarehouseDb",
        (Quote-TaskArgument $env:RESEARCH_WAREHOUSE_DB)
      )
    }
    if ($env:FACTOR_STATE_DB) {
      $RunnerArguments += @(
        "-FactorStateDb",
        (Quote-TaskArgument $env:FACTOR_STATE_DB)
      )
    }
    if ($env:QUANT_AGENT_OUTPUT_ROOT) {
      $RunnerArguments += @(
        "-OutputRoot",
        (Quote-TaskArgument $env:QUANT_AGENT_OUTPUT_ROOT)
      )
    }

    if ($Persistent) {
      if (-not $TaskName) { $TaskName = "QuantStrategyAgentRuntime-$Port" }
      $TaskAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument ($RunnerArguments -join " ")
      $TaskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
      Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $TaskAction `
        -Trigger $TaskTrigger `
        -Description "Quant Strategy Agent local read-only runtime" `
        -RunLevel Limited `
        -Force | Out-Null
      Start-ScheduledTask -TaskName $TaskName
      $registeredTask = $TaskName
    } else {
      $process = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $RunnerArguments `
        -WorkingDirectory $InstallRoot `
        -WindowStyle Hidden `
        -PassThru
      $processId = $process.Id
    }

    $health = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
      try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        break
      } catch {
        Start-Sleep -Milliseconds 250
      }
    }
    if (-not $health) { throw "Runtime health check timed out." }
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 10
    if (-not $health.PSObject.Properties.Count) {
      throw "Runtime health check failed."
    }
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
      Select-Object -First 1
    $processId = $listener.OwningProcess
  }

  $ResolvedCommit = $SourceCommit
  if (Test-Path -LiteralPath (Join-Path $InstallRoot ".git")) {
    $ResolvedCommit = (git rev-parse HEAD)
  }
  if (-not $ResolvedCommit) { $ResolvedCommit = "verified-github-archive" }

  [ordered]@{
    status = "ok"
    repository = $Repository
    branch = $Branch
    commit = $ResolvedCommit
    install_root = $InstallRoot
    snapshot_root = $env:QUANT_AGENT_SNAPSHOT_ROOT
    research_database = [bool]$env:RESEARCH_WAREHOUSE_DB
    factor_database = [bool]$env:FACTOR_STATE_DB
    output_root = [bool]$env:QUANT_AGENT_OUTPUT_ROOT
    service_url = if ($Serve) { "http://127.0.0.1:$Port" } else { $null }
    process_id = $processId
    scheduled_task = $registeredTask
  } | ConvertTo-Json -Depth 4
} finally {
  Pop-Location
}
