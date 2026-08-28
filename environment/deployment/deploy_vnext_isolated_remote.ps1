param(
  [Parameter(Mandatory = $true)]
  [string]$ArchivePath,
  [string]$ReleaseRoot = "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense",
  [string]$CurrentRelease = "F:\apps\quant_strategy_agent_research_r21_2_ai_cache",
  [string]$TaskName = "QuantStrategyAgentVNext8076R254",
  [int]$Port = 8076,
  [int]$PublicPort = 10000,
  [string]$PublicPath = "/quant-agent-vnext",
  [string]$ExpectedVersion = "2026.07.27-five-panel-dense-vnext-r25.4",
  [string]$ExpectedGovernanceRelease = "2026.07.27-dense-evidence-solver-audit-r23.3",
  [string]$ExpectedCurrentVersion = "2026.07.27-scoped-controls-ai-cache-r21.2"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Set-PrivateEnvValue(
  [string]$Path,
  [string]$Name,
  [string]$Value
) {
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
  [IO.File]::WriteAllLines(
    $Path,
    $Lines,
    (New-Object Text.UTF8Encoding($false))
  )
}

function Read-PrivateEnv([string]$Path) {
  $Values = @{}
  foreach ($RawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
    $Line = $RawLine.Trim()
    if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) {
      continue
    }
    $Parts = $Line.Split("=", 2)
    $Values[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
  }
  return $Values
}

function Wait-Health([string]$Url, [string]$Version) {
  for ($Attempt = 0; $Attempt -lt 60; $Attempt += 1) {
    try {
      $Health = Invoke-RestMethod -Uri $Url -TimeoutSec 4
      if ($Health.status -eq "ok" -and $Health.version -eq $Version) {
        return $Health
      }
    } catch {}
    Start-Sleep -Seconds 1
  }
  throw "Health check did not reach the expected version: $Url"
}

$ArchivePath = [IO.Path]::GetFullPath($ArchivePath)
$ReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot)
$AppsRoot = [IO.Path]::GetFullPath("F:\apps")
if (-not $ReleaseRoot.StartsWith($AppsRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Release root escaped F:\apps."
}
if (-not (Test-Path -LiteralPath $ArchivePath)) {
  throw "Release archive not found: $ArchivePath"
}
if (Test-Path -LiteralPath $ReleaseRoot) {
  throw "Release root already exists: $ReleaseRoot"
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  throw "Scheduled task already exists: $TaskName"
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
  throw "Port is already occupied: $Port"
}

$CurrentApp = Join-Path $CurrentRelease "board\quant_strategy_agent"
$CurrentEnv = Join-Path $CurrentApp "private\quant_agent.env"
if (-not (Test-Path -LiteralPath $CurrentEnv)) {
  throw "Current private environment is unavailable."
}

$ServeBeforeRaw = & tailscale funnel status --json
$ServeBefore = $ServeBeforeRaw | ConvertFrom-Json
$ServeHost443 = "desktop-i22b489.tailf9d7ac.ts.net:443"
$ServeHostPublic = "desktop-i22b489.tailf9d7ac.ts.net:$PublicPort"
$CurrentProxyBefore = $ServeBefore.Web.$ServeHost443.Handlers."/quant-agent".Proxy
$PublicRootBefore = $ServeBefore.Web.$ServeHostPublic.Handlers."/".Proxy
$PublicQuantAiBefore = $ServeBefore.Web.$ServeHostPublic.Handlers."/quant-ai".Proxy
$PreviousVNextProxy = $ServeBefore.Web.$ServeHostPublic.Handlers.$PublicPath.Proxy
if ([string]::IsNullOrWhiteSpace($CurrentProxyBefore)) {
  throw "Current /quant-agent Funnel baseline is unavailable."
}
$CurrentProxyUri = [Uri]$CurrentProxyBefore
if (
  $CurrentProxyUri.Scheme -ne "http" -or
  $CurrentProxyUri.Host -ne "127.0.0.1" -or
  $CurrentProxyUri.AbsolutePath.TrimEnd("/") -ne "/quant-agent"
) {
  throw "Current /quant-agent Funnel baseline is unexpected."
}
$CurrentHealthUri = "{0}://{1}:{2}/healthz" -f `
  $CurrentProxyUri.Scheme, $CurrentProxyUri.Host, $CurrentProxyUri.Port
$CurrentHealthBefore = Invoke-RestMethod -Uri $CurrentHealthUri -TimeoutSec 15
if ($CurrentHealthBefore.version -ne $ExpectedCurrentVersion) {
  throw "Current /quant-agent version baseline changed before deployment."
}

$TaskCreated = $false
$FunnelAdded = $false
try {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath, $ReleaseRoot)

  $AppRoot = Join-Path $ReleaseRoot "board\quant_strategy_agent_vnext"
  $PrivateDir = Join-Path $AppRoot "private"
  $PrivateEnv = Join-Path $PrivateDir "quant_agent.env"
  $FactorEngine = Join-Path $ReleaseRoot "model\factor_laboratory\worker.py"
  $StateDb = Join-Path $ReleaseRoot "database\factor_lab_state.sqlite3"
  $RunRoot = Join-Path $ReleaseRoot "output\factor_laboratory\runs"
  $ChampionManifest = Join-Path $ReleaseRoot "model\factor_laboratory\champion_manifest.json"
  $Python = "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
  $Warehouse = "F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db"

  foreach ($Required in @(
    (Join-Path $AppRoot "main.py"),
    (Join-Path $AppRoot "deploy\run_service.ps1"),
    $FactorEngine,
    $Python,
    $Warehouse
  )) {
    if (-not (Test-Path -LiteralPath $Required)) {
      throw "Release dependency missing: $Required"
    }
  }
  New-Item -ItemType Directory -Path $PrivateDir -Force | Out-Null
  New-Item -ItemType Directory -Path (Split-Path -Parent $StateDb) -Force |
    Out-Null
  New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "logs") -Force |
    Out-Null
  Copy-Item -LiteralPath $CurrentEnv -Destination $PrivateEnv -Force

  Set-PrivateEnvValue $PrivateEnv "QUANT_AGENT_PYTHON" $Python
  Set-PrivateEnvValue $PrivateEnv "QUANT_AGENT_PREFIXES" $PublicPath
  Set-PrivateEnvValue $PrivateEnv "HOST" "127.0.0.1"
  Set-PrivateEnvValue $PrivateEnv "PORT" ([string]$Port)
  Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_DB" $Warehouse
  Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_PYTHON" $Python
  Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_ENGINE" $FactorEngine
  Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_STATE_DB" $StateDb
  Set-PrivateEnvValue $PrivateEnv "FACTOR_LAB_RUN_ROOT" $RunRoot
  Set-PrivateEnvValue `
    $PrivateEnv "FACTOR_LAB_CHAMPION_MANIFEST" $ChampionManifest

  $Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument (
      '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f
      (Join-Path $AppRoot "deploy\run_service.ps1")
    ) `
    -WorkingDirectory $AppRoot
  $Trigger = New-ScheduledTaskTrigger -AtStartup
  $Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
  $Principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Isolated governed Quant Strategy Agent vNext" |
    Out-Null
  $TaskCreated = $true
  Start-ScheduledTask -TaskName $TaskName

  $LocalBase = "http://127.0.0.1:$Port"
  $Health = Wait-Health ($LocalBase + "/healthz") $ExpectedVersion
  $Values = Read-PrivateEnv $PrivateEnv
  $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $Login = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri ($LocalBase + "/login") `
    -Method Post `
    -Body @{
      username = $Values["QUANT_AGENT_USER"]
      password = $Values["QUANT_AGENT_PASSWORD"]
    } `
    -WebSession $Session `
    -MaximumRedirection 5 `
    -TimeoutSec 20
  if (
    $Login.StatusCode -ne 200 -or
    $Login.Content -notmatch "research_five_panel\.js" -or
    $Login.Content -match "research_analysis\.js"
  ) {
    throw "Local vNext login validation failed."
  }

  $MountedBase = $LocalBase + $PublicPath
  $MountedSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $MountedLogin = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri ($MountedBase + "/login") `
    -Method Post `
    -Body @{
      username = $Values["QUANT_AGENT_USER"]
      password = $Values["QUANT_AGENT_PASSWORD"]
    } `
    -WebSession $MountedSession `
    -MaximumRedirection 5 `
    -TimeoutSec 20
  $MountedGovernance = Invoke-RestMethod `
    -Uri ($MountedBase + "/api/model-governance") `
    -WebSession $MountedSession `
    -TimeoutSec 30
  if (
    $MountedLogin.StatusCode -ne 200 -or
    $MountedLogin.Content -notmatch "research_five_panel\.js" -or
    $MountedLogin.Content -match "research_analysis\.js" -or
    $MountedGovernance.release -ne $ExpectedGovernanceRelease
  ) {
    throw "Local mounted-path vNext validation failed."
  }

  $Governance = Invoke-RestMethod `
    -Uri ($LocalBase + "/api/model-governance") `
    -WebSession $Session `
    -TimeoutSec 30
  if (
    $Governance.status -ne "ok" -or
    $Governance.release -ne $ExpectedGovernanceRelease -or
    $Governance.models.index_enhancement.engine -ne
      "index-enhancement/1.3-bayesian-core-satellite-audit" -or
    $Governance.models.index_enhancement.robustness.post_test_shadow.model -ne
      "index_bayesian_stability_core_v16" -or
    $Governance.models.liquidity_tracking.engine -ne
      "liquidity-state/1.1-investable-cash-monthly" -or
    $Governance.models.liquidity_tracking.champion -ne
      "liquidity_monthly_investable_cash_v9" -or
    $Governance.models.liquidity_tracking.robustness.selection_uses_test -ne $false -or
    $Governance.models.liquidity_tracking.robustness.promotion_eligible -ne $false -or
    $Governance.models.liquidity_tracking.robustness.effective_training_series -ne 18 -or
    $Governance.models.liquidity_tracking.robustness.excluded_contracts -ne 10 -or
    $Governance.models.portfolio_optimization.engine -ne
      "portfolio-optimizer/2.6-cash-duration-segmentation" -or
    $Governance.models.portfolio_optimization.robustness.quality_status -ne
      "passed" -or
    @($Governance.models.portfolio_optimization.robustness.solver_benchmark).Count -ne 4 -or
    $Governance.models.kline_memory.engine -ne
      "kline-multiscale-expert/1.6-research-deployment-split" -or
    $Governance.models.kline_memory.gate -ne "research_diagnostic" -or
    $Governance.models.kline_memory.robustness.multiscale_release_guard.selection_uses_test -ne $false -or
    $Governance.models.kline_memory.robustness.multiscale_release_guard.release_approved -ne $false -or
    $null -ne $Governance.models.kline_memory.robustness.multiscale_release_guard.deployment_candidate
  ) {
    throw "Local vNext model-governance validation failed."
  }

  $Routes = @(
    "allocation:strategy",
    "liquidity:retail",
    "rotation:industry",
    "factorlab:dashboard",
    "factorlab:strategy",
    "technical:learning",
    "portfolio:solve"
  )
  $EvidenceResults = @()
  foreach ($Route in $Routes) {
    $Encoded = [Uri]::EscapeDataString($Route)
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Evidence = Invoke-RestMethod `
      -Uri ($LocalBase + "/api/research-evidence?route=" + $Encoded) `
      -WebSession $Session `
      -TimeoutSec 30
    $Watch.Stop()
    if (
      $Evidence.status -eq "not_applicable" -or
      @($Evidence.layers).Count -ne 4 -or
      $null -ne $Evidence.mechanism -or
      @($Evidence.visuals.PSObject.Properties).Count -ne 4
    ) {
      throw "Research evidence contract failed for $Route"
    }
    $EvidenceResults += [pscustomobject]@{
      route = $Route
      milliseconds = $Watch.ElapsedMilliseconds
      layers = @($Evidence.layers).Count
      visual_blocks = @($Evidence.visuals.PSObject.Properties).Count
    }
  }

  $RotationSnapshot = Invoke-RestMethod `
    -Uri ($LocalBase + "/api/rotation/snapshot") `
    -WebSession $Session `
    -TimeoutSec 30
  $RotationTracking = Invoke-RestMethod `
    -Uri ($LocalBase + "/api/rotation/tracking") `
    -WebSession $Session `
    -TimeoutSec 30
  $RotationEvidence = Invoke-RestMethod `
    -Uri ($LocalBase + "/api/research-evidence?route=rotation%3Aindustry") `
    -WebSession $Session `
    -TimeoutSec 30
  $MonthlyRotation = $RotationSnapshot.industry.frequencies.monthly
  $WeeklyRotation = $RotationSnapshot.industry.frequencies.weekly
  $MonthlyResearch = @($MonthlyRotation.research_ranking)
  $WeeklyResearch = @($WeeklyRotation.research_ranking)
  $TrackingRows = @(
    $RotationTracking.industries.PSObject.Properties |
      ForEach-Object { $_.Value }
  )
  $EffectiveFactorTotal = (
    $RotationSnapshot.six_dimension.effective_factor_count.PSObject.Properties |
      Measure-Object -Property Value -Sum
  ).Sum
  $BadWeightProfiles = @()
  $SelectedLabel = ([char]0x5165).ToString() + ([char]0x9009).ToString()
  foreach ($Profile in $RotationSnapshot.six_dimension.current_weights.PSObject.Properties) {
    $WeightValues = @($Profile.Value.PSObject.Properties | ForEach-Object { [double]$_.Value })
    if (
      [math]::Abs((($WeightValues | Measure-Object -Sum).Sum) - 1.0) -gt 1e-9 -or
      ($WeightValues | Measure-Object -Minimum).Minimum -lt 0 -or
      ($WeightValues | Measure-Object -Maximum).Maximum -gt 0.300000001
    ) {
      $BadWeightProfiles += $Profile.Name
    }
  }
  if (
    $RotationSnapshot.six_dimension.model_version -ne
      "industry-rotation/5.2-six-dimension-pit-adaptive" -or
    [int]$EffectiveFactorTotal -ne 53 -or
    @($BadWeightProfiles).Count -ne 0 -or
    $MonthlyRotation.selected_candidate -ne "C6_direct_month_smooth" -or
    $WeeklyRotation.selected_candidate -ne "C6_direct_month_smooth" -or
    $MonthlyRotation.research_selected_candidate -ne
      "C26_monthly_post_test_diagnostic_six_dimension_online_ic_top10_buffered" -or
    $WeeklyRotation.research_selected_candidate -ne
      "C29_weekly_post_test_diagnostic_six_dimension_equal_top10_buffered" -or
    $MonthlyRotation.promotion_gate.status -ne "diagnostic_only" -or
    $WeeklyRotation.promotion_gate.status -ne "diagnostic_only" -or
    $MonthlyResearch.Count -ne 31 -or
    $WeeklyResearch.Count -ne 31 -or
    @($MonthlyResearch | Where-Object {
      @($_.components.PSObject.Properties).Count -ne 7
    }).Count -ne 0 -or
    @($WeeklyResearch | Where-Object {
      @($_.components.PSObject.Properties).Count -ne 7
    }).Count -ne 0 -or
    $RotationTracking.selected_candidate -ne
      "C26_monthly_post_test_diagnostic_six_dimension_online_ic_top10_buffered" -or
    $RotationTracking.production_candidate -ne "C6_direct_month_smooth" -or
    $TrackingRows.Count -ne 31 -or
    @($TrackingRows | Where-Object { $_.selected }).Count -ne 10 -or
    @($RotationEvidence.visuals.descriptive.chart.heatmap.x).Count -ne 6 -or
    @($RotationEvidence.visuals.descriptive.chart.heatmap.y).Count -ne 31 -or
    @($RotationEvidence.visuals.strategy.table.rows).Count -ne 31 -or
    @($RotationEvidence.visuals.strategy.table.rows |
      Where-Object { $_.selected -eq $SelectedLabel }).Count -ne 10
  ) {
    throw "Industry six-dimension release contract validation failed."
  }

  $Target = "http://127.0.0.1:$Port$PublicPath"
  & tailscale funnel `
    --bg `
    --https=$PublicPort `
    --set-path=$PublicPath `
    --yes `
    $Target | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Tailscale Funnel update failed." }
  $FunnelAdded = $true
  Start-Sleep -Seconds 3

  $ServeAfterRaw = & tailscale funnel status --json
  $ServeAfter = $ServeAfterRaw | ConvertFrom-Json
  if (
    $ServeAfter.Web.$ServeHost443.Handlers."/quant-agent".Proxy -ne
      $CurrentProxyBefore -or
    $ServeAfter.Web.$ServeHostPublic.Handlers."/".Proxy -ne
      $PublicRootBefore -or
    $ServeAfter.Web.$ServeHostPublic.Handlers."/quant-ai".Proxy -ne
      $PublicQuantAiBefore -or
    $ServeAfter.Web.$ServeHostPublic.Handlers.$PublicPath.Proxy -ne $Target
  ) {
    throw "Funnel handler isolation validation failed."
  }

  $PublicBase = "https://desktop-i22b489.tailf9d7ac.ts.net:$PublicPort$PublicPath"
  $PublicHealth = Wait-Health ($PublicBase + "/healthz") $ExpectedVersion
  $PublicReady = $false
  $PublicLoginStatus = 0
  $PublicHasFivePanel = $false
  $PublicHasLegacyAnalysis = $false
  $PublicFinalUri = ""
  $PublicLoginForm = $false
  $PublicContentLength = 0
  for ($Attempt = 0; $Attempt -lt 12; $Attempt += 1) {
    try {
      $PublicSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
      $PublicLogin = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri ($PublicBase + "/login") `
        -Method Post `
        -Body @{
          username = $Values["QUANT_AGENT_USER"]
          password = $Values["QUANT_AGENT_PASSWORD"]
        } `
        -WebSession $PublicSession `
        -MaximumRedirection 5 `
        -TimeoutSec 30
      $PublicLoginStatus = [int]$PublicLogin.StatusCode
      $PublicFinalUri = [string]$PublicLogin.BaseResponse.ResponseUri.AbsoluteUri
      $PublicContentLength = [int]$PublicLogin.Content.Length
      $PublicLoginForm = $PublicLogin.Content -match 'class="login-form"'
      $PublicHasFivePanel = $PublicLogin.Content -match "research_five_panel\.js"
      $PublicHasLegacyAnalysis = $PublicLogin.Content -match "research_analysis\.js"
      if (
        $PublicLoginStatus -eq 200 -and
        $PublicHasFivePanel -and
        -not $PublicHasLegacyAnalysis
      ) {
        $PublicGovernance = Invoke-RestMethod `
          -Uri ($PublicBase + "/api/model-governance") `
          -WebSession $PublicSession `
          -TimeoutSec 30
        if (
          $PublicGovernance.status -eq "ok" -and
          $PublicGovernance.release -eq $ExpectedGovernanceRelease
        ) {
          $PublicReady = $true
          break
        }
      }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $PublicReady) {
    throw (
      "Public vNext login validation failed after retries. " +
      "status=$PublicLoginStatus five_panel=$PublicHasFivePanel " +
      "legacy_analysis=$PublicHasLegacyAnalysis login_form=$PublicLoginForm " +
      "content_length=$PublicContentLength final_uri=$PublicFinalUri"
    )
  }

  $PublicRotationSnapshot = Invoke-RestMethod `
    -Uri ($PublicBase + "/api/rotation/snapshot") `
    -WebSession $PublicSession `
    -TimeoutSec 30
  $PublicRotationTracking = Invoke-RestMethod `
    -Uri ($PublicBase + "/api/rotation/tracking") `
    -WebSession $PublicSession `
    -TimeoutSec 30
  $PublicRotationEvidence = Invoke-RestMethod `
    -Uri ($PublicBase + "/api/research-evidence?route=rotation%3Aindustry") `
    -WebSession $PublicSession `
    -TimeoutSec 30
  if (
    $PublicRotationSnapshot.six_dimension.model_version -ne
      $RotationSnapshot.six_dimension.model_version -or
    $PublicRotationSnapshot.industry.frequencies.monthly.research_selected_candidate -ne
      $MonthlyRotation.research_selected_candidate -or
    $PublicRotationSnapshot.industry.frequencies.weekly.research_selected_candidate -ne
      $WeeklyRotation.research_selected_candidate -or
    @($PublicRotationSnapshot.industry.frequencies.monthly.research_ranking).Count -ne 31 -or
    @($PublicRotationSnapshot.industry.frequencies.weekly.research_ranking).Count -ne 31 -or
    $PublicRotationTracking.selected_candidate -ne $RotationTracking.selected_candidate -or
    @($PublicRotationTracking.industries.PSObject.Properties).Count -ne 31 -or
    @($PublicRotationEvidence.visuals.descriptive.chart.heatmap.x).Count -ne 6 -or
    @($PublicRotationEvidence.visuals.descriptive.chart.heatmap.y).Count -ne 31 -or
    @($PublicRotationEvidence.visuals.strategy.table.rows |
      Where-Object { $_.selected -eq $SelectedLabel }).Count -ne 10
  ) {
    throw "Public industry six-dimension contract validation failed."
  }
  $CurrentHealthAfter = Invoke-RestMethod `
    -Uri "https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/healthz" `
    -TimeoutSec 20
  if (
    $CurrentHealthAfter.version -ne $ExpectedCurrentVersion -or
    $CurrentHealthAfter.version -ne $CurrentHealthBefore.version
  ) {
    throw "Current public application changed during isolated deployment."
  }

  $Listener = Get-NetTCPConnection -State Listen -LocalPort $Port |
    Select-Object -First 1
  [ordered]@{
    status = "deployed"
    public_url = $PublicBase + "/"
    version = $PublicHealth.version
    governance_release = $Governance.release
    liquidity_engine = $Governance.models.liquidity_tracking.engine
    portfolio_engine = $Governance.models.portfolio_optimization.engine
    task = $TaskName
    task_state = [string](Get-ScheduledTask -TaskName $TaskName).State
    port = $Port
    process_id = $Listener.OwningProcess
    release_root = $ReleaseRoot
    evidence = $EvidenceResults
    current_public_version_before = $CurrentHealthBefore.version
    current_public_version_after = $CurrentHealthAfter.version
    current_funnel_proxy_unchanged = $true
    credentials_output = $false
  } | ConvertTo-Json -Depth 6 -Compress
} catch {
  if ($FunnelAdded) {
    if ($PreviousVNextProxy) {
      & tailscale funnel `
        --bg `
        --https=$PublicPort `
        --set-path=$PublicPath `
        --yes `
        $PreviousVNextProxy | Out-Null
    } else {
      & tailscale funnel `
        --https=$PublicPort `
        --set-path=$PublicPath `
        off `
        --yes | Out-Null
    }
  }
  if ($TaskCreated) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false `
      -ErrorAction SilentlyContinue
  }
  $Listener = Get-NetTCPConnection -State Listen -LocalPort $Port `
    -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Listener) {
    Stop-Process -Id $Listener.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
  }
  if (Test-Path -LiteralPath $ReleaseRoot) {
    throw "Failed release root remains after rollback. Original failure: $($_.Exception.Message)"
  }
  throw
}
