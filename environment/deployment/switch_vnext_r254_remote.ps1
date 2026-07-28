$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Host443 = "desktop-i22b489.tailf9d7ac.ts.net:443"
$Host10000 = "desktop-i22b489.tailf9d7ac.ts.net:10000"
$PublicPath = "/quant-agent-vnext"
$Target = "http://127.0.0.1:8076/quant-agent-vnext"
$ExpectedVNext = "2026.07.27-five-panel-dense-vnext-r25.4"
$ExpectedCurrent = "2026.07.27-scoped-controls-ai-cache-r21.2"
$Before = (& tailscale funnel status --json) | ConvertFrom-Json
$CurrentBefore = $Before.Web.$Host443.Handlers."/quant-agent".Proxy
$RootBefore = $Before.Web.$Host10000.Handlers."/".Proxy
$AiBefore = $Before.Web.$Host10000.Handlers."/quant-ai".Proxy
$VNextBefore = $Before.Web.$Host10000.Handlers.$PublicPath.Proxy
$Changed = $false

try {
  & tailscale funnel `
    --bg `
    --https=10000 `
    --set-path=$PublicPath `
    --yes `
    $Target | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Funnel switch failed." }
  $Changed = $true
  Start-Sleep -Seconds 3

  $After = (& tailscale funnel status --json) | ConvertFrom-Json
  if (
    $After.Web.$Host443.Handlers."/quant-agent".Proxy -ne $CurrentBefore -or
    $After.Web.$Host10000.Handlers."/".Proxy -ne $RootBefore -or
    $After.Web.$Host10000.Handlers."/quant-ai".Proxy -ne $AiBefore -or
    $After.Web.$Host10000.Handlers.$PublicPath.Proxy -ne $Target
  ) {
    throw "Funnel isolation validation failed."
  }

  $Public = (
    "https://desktop-i22b489.tailf9d7ac.ts.net:10000" + $PublicPath
  )
  $Health = $null
  for ($Attempt = 0; $Attempt -lt 30; $Attempt += 1) {
    try {
      $Health = Invoke-RestMethod `
        -Uri ($Public + "/healthz") `
        -TimeoutSec 5
      if ($Health.version -eq $ExpectedVNext) { break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $Health -or $Health.version -ne $ExpectedVNext) {
    throw "Public R25.4 health failed."
  }

  $EnvPath = (
    "F:\apps\quant_strategy_agent_vnext_r25_4_five_panel_dense" +
    "\board\quant_strategy_agent_vnext\private\quant_agent.env"
  )
  $Values = @{}
  foreach ($Raw in Get-Content -LiteralPath $EnvPath -Encoding utf8) {
    $Line = $Raw.Trim()
    if (
      $Line -and
      -not $Line.StartsWith("#") -and
      $Line.Contains("=")
    ) {
      $Parts = $Line.Split("=", 2)
      $Values[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
    }
  }
  $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $Login = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri ($Public + "/login") `
    -Method Post `
    -Body @{
      username = $Values["QUANT_AGENT_USER"]
      password = $Values["QUANT_AGENT_PASSWORD"]
    } `
    -WebSession $Session `
    -MaximumRedirection 5 `
    -TimeoutSec 30
  if (
    $Login.StatusCode -ne 200 -or
    $Login.Content -notmatch "research_five_panel\.js" -or
    $Login.Content -match "research_analysis\.js"
  ) {
    throw "Public R25.4 login asset validation failed."
  }
  $Current = Invoke-RestMethod `
    -Uri "https://desktop-i22b489.tailf9d7ac.ts.net/quant-agent/healthz" `
    -TimeoutSec 20
  if ($Current.version -ne $ExpectedCurrent) {
    throw "Current public application changed."
  }

  [ordered]@{
    status = "switched"
    public_url = $Public + "/"
    vnext_version = $Health.version
    current_version = $Current.version
    vnext_proxy = $After.Web.$Host10000.Handlers.$PublicPath.Proxy
    current_proxy = $After.Web.$Host443.Handlers."/quant-agent".Proxy
    root_10000_unchanged = (
      $After.Web.$Host10000.Handlers."/".Proxy -eq $RootBefore
    )
    quant_ai_unchanged = (
      $After.Web.$Host10000.Handlers."/quant-ai".Proxy -eq $AiBefore
    )
    login_assets = "five-panel-only"
  } | ConvertTo-Json -Compress
} catch {
  if ($Changed -and $VNextBefore) {
    & tailscale funnel `
      --bg `
      --https=10000 `
      --set-path=$PublicPath `
      --yes `
      $VNextBefore | Out-Null
  }
  throw
}
