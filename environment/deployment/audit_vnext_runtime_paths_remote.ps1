$ErrorActionPreference = "Stop"

$AppRoot = "F:\apps\quant_strategy_agent_research_r21_2_ai_cache\board\quant_strategy_agent"
$EnvPath = Join-Path $AppRoot "private\quant_agent.env"
$Values = @{}
foreach ($RawLine in Get-Content -LiteralPath $EnvPath -Encoding utf8) {
  $Line = $RawLine.Trim()
  if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) { continue }
  $Parts = $Line.Split("=", 2)
  $Values[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
}

$SafeNames = @(
  "QUANT_AGENT_PYTHON",
  "FACTOR_LAB_DB",
  "FACTOR_LAB_PYTHON",
  "FACTOR_LAB_ENGINE",
  "BOARD_BASE_URL",
  "KLINE_BASE_URL",
  "FACTOR_BASE_URL",
  "AI_MONITOR_BASE_URL"
)
$Rows = @()
foreach ($Name in $SafeNames) {
  $Value = $Values[$Name]
  $Rows += [pscustomobject]@{
    name = $Name
    value = $Value
    exists = if ($Value -and [IO.Path]::IsPathRooted($Value)) {
      Test-Path -LiteralPath $Value
    } else { $null }
  }
}

[ordered]@{
  status = "ok"
  env_path = $EnvPath
  safe_paths = $Rows
  credential_keys_present = [ordered]@{
    user = [bool]$Values["QUANT_AGENT_USER"]
    password = [bool]$Values["QUANT_AGENT_PASSWORD"]
    secret = [bool]$Values["QUANT_AGENT_SECRET"]
  }
} | ConvertTo-Json -Depth 5 -Compress
