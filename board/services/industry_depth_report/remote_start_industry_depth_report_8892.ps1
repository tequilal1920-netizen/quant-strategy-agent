$ErrorActionPreference = "Stop"

$Root = "F:\apps\industry_depth_report_public_8892"
$App = Join-Path $Root "board\services\industry_depth_report\app.py"
$Port = 8892
$LogDir = Join-Path $Root "output\logs\industry_depth_report"
$OutLog = Join-Path $LogDir "server_8892.remote.out.log"
$ErrLog = Join-Path $LogDir "server_8892.remote.err.log"
$RuntimeSite = Join-Path $Root "runtime\python_site"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
trap {
  "Fatal at $(Get-Date -Format s): $($_.Exception.Message)" | Out-File -FilePath $ErrLog -Append -Encoding utf8
  if ($_.ScriptStackTrace) {
    $_.ScriptStackTrace | Out-File -FilePath $ErrLog -Append -Encoding utf8
  }
  exit 1
}
"Remote start entered at $(Get-Date -Format s), user=$([Environment]::UserName)" | Out-File -FilePath $OutLog -Append -Encoding utf8
if (Test-Path $RuntimeSite) {
  if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RuntimeSite;$env:PYTHONPATH"
  } else {
    $env:PYTHONPATH = $RuntimeSite
  }
  "Runtime Python site enabled: $RuntimeSite" | Out-File -FilePath $OutLog -Append -Encoding utf8
}

$pythonCandidates = @(
  "C:\ProgramData\anaconda3\python.exe",
  "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
)

function Resolve-PythonCandidate($candidate) {
  if ($candidate -eq "python") {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
      return $cmd.Source
    }
    return $null
  }
  if (Test-Path $candidate) {
    return $candidate
  }
  return $null
}

function Test-PythonModules($pythonPath, [string[]]$modules) {
  $code = "import importlib.util, sys; missing=[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]; print(','.join(missing)); sys.exit(1 if missing else 0)"
  & $pythonPath -c $code @modules 1>$null 2>$null
  return ($LASTEXITCODE -eq 0)
}

$requiredModules = @("flask", "pandas", "numpy", "matplotlib", "requests", "docx")
$requiredPackages = @("flask", "pandas", "numpy", "matplotlib", "requests", "python-docx")

$Python = $null
$FallbackPython = $null
foreach ($candidate in $pythonCandidates) {
  $candidatePath = Resolve-PythonCandidate $candidate
  if (-not $candidatePath) {
    "Python candidate not found: $candidate" | Out-File -FilePath $OutLog -Append -Encoding utf8
    continue
  }
  "Python candidate found: $candidatePath" | Out-File -FilePath $OutLog -Append -Encoding utf8
  if (-not $FallbackPython) {
    $FallbackPython = $candidatePath
  }
  if (Test-PythonModules $candidatePath $requiredModules) {
    "Python candidate has required modules: $candidatePath" | Out-File -FilePath $OutLog -Append -Encoding utf8
    $Python = $candidatePath
    break
  } else {
    "Python candidate missing required modules: $candidatePath" | Out-File -FilePath $OutLog -Append -Encoding utf8
  }
}

if (-not $Python) {
  if (-not $FallbackPython) {
    throw "No Python runtime found for IndustryDepthReport."
  }
  $Python = $FallbackPython
  New-Item -ItemType Directory -Force -Path $RuntimeSite | Out-Null
  if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RuntimeSite;$env:PYTHONPATH"
  } else {
    $env:PYTHONPATH = $RuntimeSite
  }
  "Required Python modules missing; attempting one-time target install with $Python into $RuntimeSite" | Out-File -FilePath $OutLog -Append -Encoding utf8
  & $Python -m pip install --upgrade --target $RuntimeSite @requiredPackages 1>> $OutLog 2>> $ErrLog
  if (-not (Test-PythonModules $Python $requiredModules)) {
    throw "Python dependencies are unavailable after install attempt. Refuse to start."
  }
}
if (-not (Test-Path $App)) {
  throw "IndustryDepthReport app not found at $App"
}

$dbCandidates = @(
  "F:\data\industry_depth_report\research_warehouse.db",
  "F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db",
  "F:\data\agent_console_private\research_warehouse.db",
  (Join-Path $Root "database\research_warehouse.db")
)

$WarehouseDb = $null
foreach ($candidate in $dbCandidates) {
  if (Test-Path $candidate) {
    $WarehouseDb = $candidate
    break
  }
}

if (-not $WarehouseDb) {
  $SourceDb = "F:\data\agent_console_private\database.db"
  $BuildScript = Join-Path $Root "framework\data_pipeline\build_warehouse.py"
  $TargetDb = "F:\data\industry_depth_report\research_warehouse.db"
  if ((Test-Path $SourceDb) -and (Test-Path $BuildScript)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $TargetDb) | Out-Null
    "Building formal research warehouse from $SourceDb at $(Get-Date -Format s)" | Out-File -FilePath $OutLog -Append -Encoding utf8
    & $Python $BuildScript --source-db $SourceDb --out-db $TargetDb --project-root $Root 1>> $OutLog 2>> $ErrLog
    if (Test-Path $TargetDb) {
      $WarehouseDb = $TargetDb
      "Warehouse build finished at $(Get-Date -Format s): $WarehouseDb" | Out-File -FilePath $OutLog -Append -Encoding utf8
    }
  }
}

if (-not $WarehouseDb) {
  throw "No research_warehouse.db found. Refuse to start instead of generating a degraded report."
}

$UserName = [Environment]::GetEnvironmentVariable("INDUSTRY_REPORT_USER", "Machine")
if (-not $UserName) {
  $UserName = [Environment]::GetEnvironmentVariable("INDUSTRY_REPORT_USER", "User")
}
if (-not $UserName) {
  $UserName = [Environment]::GetEnvironmentVariable("INDUSTRY_REPORT_USER", "Process")
}
if (-not $UserName) {
  throw "INDUSTRY_REPORT_USER is not configured on the server."
}

$Password = [Environment]::GetEnvironmentVariable("INDUSTRY_REPORT_PASSWORD", "Machine")
if (-not $Password) {
  $Password = [Environment]::GetEnvironmentVariable("INDUSTRY_REPORT_PASSWORD", "User")
}
if (-not $Password) {
  $Password = [Environment]::GetEnvironmentVariable("INDUSTRY_REPORT_PASSWORD", "Process")
}
if (-not $Password) {
  throw "INDUSTRY_REPORT_PASSWORD is not configured on the server. Refuse to expose a public site without authentication."
}

$AiKey = [Environment]::GetEnvironmentVariable("AI_ROUTER_API_KEY", "Machine")
if (-not $AiKey) {
  $AiKey = [Environment]::GetEnvironmentVariable("AI_ROUTER_API_KEY", "User")
}
if (-not $AiKey) {
  $AiKey = [Environment]::GetEnvironmentVariable("AI_ROUTER_API_KEY", "Process")
}

$DocxPython = $null
foreach ($candidate in $pythonCandidates) {
  $candidatePath = $candidate
  if ($candidatePath -eq "python") {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
      $candidatePath = $cmd.Source
    } else {
      continue
    }
  }
  if (-not (Test-Path $candidatePath)) {
    continue
  }
  & $candidatePath -c "import docx" 1>$null 2>$null
  if ($LASTEXITCODE -eq 0) {
    $DocxPython = $candidatePath
    break
  }
}

if (-not $DocxPython) {
  "python-docx not found; attempting one-time user install with $Python" | Out-File -FilePath $OutLog -Append -Encoding utf8
  & $Python -m pip install --user python-docx 1>> $OutLog 2>> $ErrLog
  & $Python -c "import docx" 1>$null 2>$null
  if ($LASTEXITCODE -eq 0) {
    $DocxPython = $Python
  }
}
if (-not $DocxPython) {
  throw "python-docx is unavailable. Refuse to start because Word report generation cannot be verified."
}

"Supervisor started at $(Get-Date -Format s), python=$Python, docx_python=$DocxPython, db=$WarehouseDb, port=$Port" | Out-File -FilePath $OutLog -Append -Encoding utf8

while ($true) {
  $env:INDUSTRY_REPORT_PROJECT_ROOT = $Root
  $env:INDUSTRY_REPORT_DB = $WarehouseDb
  $env:INDUSTRY_REPORT_HOST = "127.0.0.1"
  $env:INDUSTRY_REPORT_PORT = [string]$Port
  $env:INDUSTRY_REPORT_USER = $UserName
  $env:INDUSTRY_REPORT_PASSWORD = $Password
  $env:INDUSTRY_DOCX_PYTHON = $DocxPython
  if ($AiKey) {
    $env:AI_ROUTER_API_KEY = $AiKey
    $env:AI_ROUTER_BASE_URL = "https://ai.router.team"
    $env:AI_ROUTER_MODEL = "gpt-5.5"
    $env:AI_ROUTER_REASONING_EFFORT = "xhigh"
  }

  "Launching IndustryDepthReport app at $(Get-Date -Format s)" | Out-File -FilePath $OutLog -Append -Encoding utf8
  $ChildOut = Join-Path $LogDir "flask_child.out.log"
  $ChildErr = Join-Path $LogDir "flask_child.err.log"
  $exitCode = $null
  Push-Location $Root
  $oldErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $Python $App 1>> $ChildOut 2>> $ChildErr
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldErrorActionPreference
    Pop-Location
  }
  "App exited at $(Get-Date -Format s), exit=$exitCode. Restarting in 5 seconds." | Out-File -FilePath $ErrLog -Append -Encoding utf8
  Start-Sleep -Seconds 5
}
