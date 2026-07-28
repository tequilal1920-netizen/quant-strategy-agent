param(
  [string]$Branch = "agent/industry-style-r16-6",
  [string]$RepositoryUrl = "https://github.com/tequilal1920-netizen/quant-strategy-agent"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $Root ".git"))) {
  $Root = (Get-Location).Path
}
$Root = [IO.Path]::GetFullPath($Root)
$PackagesRoot = [IO.Path]::GetFullPath((Join-Path $Root "ai-models"))

$Mappings = @(
  [pscustomobject]@{ slug = "research-home"; source = "research_home"; components = @() },
  [pscustomobject]@{ slug = "data-dashboard"; source = "data_dashboard"; components = @() },
  [pscustomobject]@{ slug = "asset-allocation"; source = "asset_allocation"; components = @() },
  [pscustomobject]@{ slug = "liquidity-tracking"; source = "liquidity_tracking"; components = @() },
  [pscustomobject]@{ slug = "industry-rotation"; source = "industry_rotation"; components = @() },
  [pscustomobject]@{ slug = "factor-laboratory"; source = "factor_laboratory"; components = @("llm_factor_mining", "index_enhancement") },
  [pscustomobject]@{ slug = "technical-analysis"; source = "technical_analysis"; components = @("kline_memory_learning") },
  [pscustomobject]@{ slug = "portfolio-optimization"; source = "portfolio_optimization"; components = @() }
)

function Write-Utf8NoBom {
  param([string]$Path, [string]$Content)
  [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Copy-TrackedTree {
  param(
    [string]$RepositoryPath,
    [string]$Destination
  )
  $Tracked = @(git -C $Root ls-files -- $RepositoryPath)
  if ($LASTEXITCODE -ne 0) {
    throw "git ls-files failed for $RepositoryPath"
  }
  foreach ($TrackedPath in $Tracked) {
    $Relative = $TrackedPath.Substring($RepositoryPath.Length).TrimStart("/")
    $SourceFile = Join-Path $Root ($TrackedPath.Replace("/", "\"))
    $DestinationFile = Join-Path $Destination ($Relative.Replace("/", "\"))
    $DestinationDirectory = Split-Path -Parent $DestinationFile
    New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    Copy-Item -LiteralPath $SourceFile -Destination $DestinationFile -Force
  }
  return $Tracked
}

New-Item -ItemType Directory -Path $PackagesRoot -Force | Out-Null
$Catalog = Get-Content -Raw -Encoding UTF8 (Join-Path $Root "agent_runtime\catalog.json") | ConvertFrom-Json
$Summary = @()

foreach ($Mapping in $Mappings) {
  $Package = [IO.Path]::GetFullPath((Join-Path $PackagesRoot $Mapping.slug))
  if (-not $Package.StartsWith(
    $PackagesRoot + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
  )) {
    throw "Unsafe package path: $Package"
  }
  if (Test-Path -LiteralPath $Package) {
    Remove-Item -LiteralPath $Package -Recurse -Force
  }
  New-Item -ItemType Directory -Path $Package | Out-Null

  $SkillPath = "skill/$($Mapping.slug)"
  $SourcePath = "model/$($Mapping.source)"
  $SkillFiles = @(Copy-TrackedTree $SkillPath $Package)
  $SourceFiles = @(Copy-TrackedTree $SourcePath (Join-Path $Package "source"))
  $ComponentFiles = @()
  foreach ($Component in $Mapping.components) {
    $ComponentFiles += Copy-TrackedTree "model/$Component" (Join-Path $Package "components\$Component")
  }

  $TextFiles = @((Join-Path $Package "SKILL.md"))
  $TextFiles += Get-ChildItem -LiteralPath (Join-Path $Package "references") -File -Recurse -Filter "*.md" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName
  foreach ($TextFile in $TextFiles | Select-Object -Unique) {
    $Text = Get-Content -Raw -Encoding UTF8 $TextFile
    $Text = $Text.Replace("skill/$($Mapping.slug)", "ai-models/$($Mapping.slug)")
    $Text = $Text.Replace("model/$($Mapping.source)", "ai-models/$($Mapping.slug)/source")
    foreach ($Component in $Mapping.components) {
      $Text = $Text.Replace("model/$Component", "ai-models/$($Mapping.slug)/components/$Component")
    }
    Write-Utf8NoBom $TextFile $Text
  }

  $SourceManifestPath = Join-Path $Root "model\$($Mapping.source)\MODULE.json"
  $SourceManifest = Get-Content -Raw -Encoding UTF8 $SourceManifestPath | ConvertFrom-Json
  $CatalogModule = $Catalog.modules | Where-Object { $_.skill -eq $Mapping.slug } | Select-Object -First 1
  $FolderUrl = "$RepositoryUrl/tree/$Branch/ai-models/$($Mapping.slug)"

  $PackageManifest = [ordered]@{
    schema_version = "quant-agent-ai-package/1.0"
    title = $SourceManifest.nav_title
    slug = $Mapping.slug
    github_folder_url = $FolderUrl
    github_branch = $Branch
    skill_entry = "SKILL.md"
    query_entry = "scripts/query.py"
    model_source = "source"
    bundled_components = @($Mapping.components | ForEach-Object { "components/$_" })
    operations = @($CatalogModule.operations)
    question_examples = @($CatalogModule.questions)
    shared_runtime = "../../agent_runtime"
    shared_repository_dependencies = @(
      "../../board",
      "../../database",
      "../../framework",
      "../../environment"
    )
    required_environment = @(
      "QUANT_AGENT_SNAPSHOT_ROOT",
      "RESEARCH_WAREHOUSE_DB",
      "FACTOR_STATE_DB",
      "QUANT_AGENT_OUTPUT_ROOT"
    )
    remote_model_environment = @(
      "QUANT_AGENT_BASE_URL",
      "QUANT_AGENT_USER",
      "QUANT_AGENT_PASSWORD"
    )
    deployment = [ordered]@{
      remote_root = "F:\apps\quant_strategy_agent_github_runtime"
      local_service = "http://127.0.0.1:8091"
      authenticated_service = "http://127.0.0.1:8076/quant-agent-vnext"
    }
    inventory = [ordered]@{
      skill_files = $SkillFiles.Count
      source_files = $SourceFiles.Count
      component_files = $ComponentFiles.Count
      total_files = $SkillFiles.Count + $SourceFiles.Count + $ComponentFiles.Count + 1
    }
  }
  Write-Utf8NoBom (Join-Path $Package "PACKAGE.json") ($PackageManifest | ConvertTo-Json -Depth 10)

  $SourceManifest | Add-Member -NotePropertyName "skill_path" -NotePropertyValue "ai-models/$($Mapping.slug)/SKILL.md" -Force
  $SourceManifest | Add-Member -NotePropertyName "legacy_skill_path" -NotePropertyValue $SkillPath -Force
  $SourceManifest | Add-Member -NotePropertyName "ai_package_path" -NotePropertyValue "ai-models/$($Mapping.slug)" -Force
  $SourceManifest | Add-Member -NotePropertyName "query_entry" -NotePropertyValue "ai-models/$($Mapping.slug)/scripts/query.py" -Force
  $SourceManifest | Add-Member -NotePropertyName "shared_runtime" -NotePropertyValue "agent_runtime" -Force
  $SourceManifest | Add-Member -NotePropertyName "github_url" -NotePropertyValue $FolderUrl -Force
  $SourceManifest | Add-Member -NotePropertyName "github_ref" -NotePropertyValue $Branch -Force
  $SourceManifest | Add-Member -NotePropertyName "delivery" -NotePropertyValue "unified-ai-folder" -Force
  Write-Utf8NoBom $SourceManifestPath ($SourceManifest | ConvertTo-Json -Depth 10)

  $Summary += [pscustomobject]@{
    slug = $Mapping.slug
    package = $Package
    files = $PackageManifest.inventory.total_files
    url = $FolderUrl
  }
}

$Summary | ConvertTo-Json -Depth 5
