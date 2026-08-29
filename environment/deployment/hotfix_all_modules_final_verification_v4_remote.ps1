param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [Parameter(Mandatory = $true)]
    [string]$OverlayZip,

    [string]$TaskName = "QuantStrategyAgent8096R340VisualOptimizer",
    [int]$Port = 8096,
    [string]$ExpectedVersion = "2026.08.28-all-modules-final-verification-v4"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Get-FullPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-UnderRoot([string]$Path, [string]$Root) {
    $full = Get-FullPath $Path
    $rootFull = Get-FullPath $Root
    if (-not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside release root: $full"
    }
    return $full
}

$release = Get-FullPath $ReleaseRoot
if (-not (Test-Path -LiteralPath $release -PathType Container)) {
    throw "Release root does not exist: $release"
}
if (-not $release.StartsWith("F:\apps\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release root must be under F:\apps\: $release"
}

$overlay = Get-FullPath $OverlayZip
if (-not (Test-Path -LiteralPath $overlay -PathType Leaf)) {
    throw "Overlay zip does not exist: $overlay"
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$workRoot = Join-Path $env:TEMP "quant_agent_overlay_$stamp"
$backupRoot = Join-Path "F:\apps\quant_strategy_agent\deployment_backups" "all_modules_final_verification_v4_$stamp"

New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
Expand-Archive -LiteralPath $overlay -DestinationPath $workRoot -Force

$files = @(Get-ChildItem -LiteralPath $workRoot -File -Recurse)
if ($files.Count -eq 0) {
    throw "Overlay zip is empty: $overlay"
}

$copied = @()
foreach ($file in $files) {
    $relative = $file.FullName.Substring($workRoot.Length).TrimStart("\", "/")
    if ([string]::IsNullOrWhiteSpace($relative)) {
        throw "Invalid empty relative path in overlay."
    }
    if ($relative -match '(^|[\\/])(\.git|__pycache__|.*\.egg-info)([\\/]|$)') {
        throw "Blocked generated/source-control path in overlay: $relative"
    }
    if ($relative -match '\.(db|sqlite|sqlite3|wal|shm|xlsx|xls|docx|pdf|zip|7z|rar)$') {
        throw "Blocked data/document/archive payload in overlay: $relative"
    }
    if ($relative -match '(^|[\\/])private([\\/]|$)' -or $relative -match '\.env$') {
        throw "Blocked credential path in overlay: $relative"
    }

    $target = Assert-UnderRoot (Join-Path $release $relative) $release
    $targetDir = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $backupTarget = Join-Path $backupRoot $relative
        $backupDir = Split-Path -Parent $backupTarget
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        Copy-Item -LiteralPath $target -Destination $backupTarget -Force
    }

    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    $copied += $relative
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $pythonFiles = @(
        "board\quant_strategy_agent\main.py",
        "board\quant_strategy_agent\app.py",
        "board\quant_strategy_agent\factor_lab_backend.py",
        "board\quant_strategy_agent\rotation_app.py",
        "board\quant_strategy_agent\kline_llm_backend.py"
    ) | Where-Object { Test-Path -LiteralPath (Join-Path $release $_) }
    if ($pythonFiles.Count -gt 0) {
        Push-Location $release
        & $python.Source -X utf8 -m py_compile @pythonFiles
        Pop-Location
    }
}

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $jsFiles = @(
        "board\quant_strategy_agent\static\js\app.js",
        "board\quant_strategy_agent\static\js\rotation_module.js",
        "board\quant_strategy_agent\static\ai_monitor\js\core.js",
        "board\quant_strategy_agent\static\ai_monitor\js\shell.js",
        "board\quant_strategy_agent\static\js\portfolio_optimizer.js"
    ) | Where-Object { Test-Path -LiteralPath (Join-Path $release $_) }
    foreach ($js in $jsFiles) {
        & $node.Source --check (Join-Path $release $js) | Out-Null
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    throw "Scheduled task not found: $TaskName"
}
try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
} catch {
    Write-Warning ("Task stop warning: " + $_.Exception.Message)
}
Start-ScheduledTask -TaskName $TaskName

$healthUrl = "http://127.0.0.1:$Port/quant-agent/healthz"
$health = $null
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 10
        if ($health.version -eq $ExpectedVersion) {
            break
        }
    } catch {
        $health = $null
    }
}

if (-not $health -or $health.version -ne $ExpectedVersion) {
    $actual = if ($health) { [string]$health.version } else { "<no response>" }
    throw "Health check version mismatch. expected=$ExpectedVersion actual=$actual"
}

[pscustomobject]@{
    status = "ok"
    release_root = $release
    backup_root = $backupRoot
    copied_count = $copied.Count
    version = $health.version
    copied = $copied
} | ConvertTo-Json -Depth 4
