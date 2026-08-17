Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ReleaseRoot = "F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor"
$TaskName = "QuantStrategyAgentVNext8099R352IndustryChampion"
$ZipName = "quant_strategy_agent_vnext_r381_technical_full_history_fit_20260817.zip"
$ExpectedZipHash = "3C001AEBCAF8923C5FECB107A05A70382099437D4EA243925AFC595BEE833E73"
$ExpectedVersion = "2026.08.17-technical-full-history-fit-vnext-r38.1"
$ExpectedGovernanceRelease = "2026.08.17-technical-full-history-fit-governed-r38.1"
$BackupRoot = Join-Path $ReleaseRoot "deployment_backups\technical_full_history_fit_r381_20260817"
$PythonExe = "D:\Download\Anaconda\python.exe"

$Files = @(
  "README.md",
  "ai-models\technical-analysis\SKILL.md",
  "ai-models\technical-analysis\PACKAGE.json",
  "ai-models\technical-analysis\runtime\agent_runtime\core.py",
  "ai-models\technical-analysis\runtime\agent_runtime\catalog.json",
  "ai-models\technical-analysis\source\README.md",
  "ai-models\technical-analysis\references\dual-model-sop.md",
  "board\quant_strategy_agent_vnext\main.py",
  "board\quant_strategy_agent_vnext\model_governance_backend.py",
  "board\quant_strategy_agent_vnext\research_evidence_backend.py",
  "board\quant_strategy_agent_vnext\kline_multiscale_visual_backend.py",
  "board\quant_strategy_agent_vnext\qa\test_kline_multiscale_evidence.py",
  "board\quant_strategy_agent_vnext\data\kline_multiscale_expert_challenger.json",
  "framework\backtest\technical_signal_model.py",
  "framework\backtest\test_technical_signal_model.py",
  "model\kline_memory_learning\run_multiscale_expert_challenger.py",
  "dist\technical_full_history_fit_r381_release_notes.md"
)

function Copy-TreeFile($SourceRoot, $TargetRoot, $RelPath) {
  $src = Join-Path $SourceRoot $RelPath
  $dst = Join-Path $TargetRoot $RelPath
  $parent = Split-Path -Parent $dst
  if (!(Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
  Copy-Item -LiteralPath $src -Destination $dst -Force
}

function Restore-Backup {
  foreach ($rel in $Files) {
    $bak = Join-Path $BackupRoot $rel
    $dst = Join-Path $ReleaseRoot $rel
    if (Test-Path -LiteralPath $bak) { Copy-TreeFile $BackupRoot $ReleaseRoot $rel }
  }
}

try {
  if (!(Test-Path -LiteralPath $ReleaseRoot)) { throw "release_root_missing:$ReleaseRoot" }
  $zip = Join-Path $ReleaseRoot $ZipName
  if (!(Test-Path -LiteralPath $zip)) { throw "zip_missing:$zip" }
  $actualHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToUpperInvariant()
  if ($actualHash -ne $ExpectedZipHash) { throw "zip_hash_mismatch:$actualHash" }

  if (Test-Path -LiteralPath $BackupRoot) { Remove-Item -LiteralPath $BackupRoot -Recurse -Force }
  New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
  foreach ($rel in $Files) {
    if (Test-Path -LiteralPath (Join-Path $ReleaseRoot $rel)) { Copy-TreeFile $ReleaseRoot $BackupRoot $rel }
  }

  $stage = Join-Path $ReleaseRoot "_technical_full_history_fit_r381_stage"
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
  New-Item -ItemType Directory -Path $stage -Force | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
  foreach ($rel in $Files) {
    if (!(Test-Path -LiteralPath (Join-Path $stage $rel))) { throw "package_file_missing:$rel" }
    Copy-TreeFile $stage $ReleaseRoot $rel
  }
  Remove-Item -LiteralPath $stage -Recurse -Force

  Push-Location $ReleaseRoot
  try {
    if (!(Test-Path -LiteralPath $PythonExe)) { throw "python_missing:$PythonExe" }
    & $PythonExe -m py_compile `
      .\ai-models\technical-analysis\runtime\agent_runtime\core.py `
      .\framework\backtest\technical_signal_model.py `
      .\model\kline_memory_learning\run_multiscale_expert_challenger.py `
      .\board\quant_strategy_agent_vnext\main.py `
      .\board\quant_strategy_agent_vnext\model_governance_backend.py `
      .\board\quant_strategy_agent_vnext\research_evidence_backend.py `
      .\board\quant_strategy_agent_vnext\kline_multiscale_visual_backend.py
    if ($LASTEXITCODE -ne 0) { throw "py_compile_failed:$LASTEXITCODE" }

    $checkScript = @"
import json
import sys
from pathlib import Path

sys.path.insert(0, 'board/quant_strategy_agent_vnext')
import research_evidence_backend as reb
import model_governance_backend as mgb

snapshot = json.loads(Path('board/quant_strategy_agent_vnext/data/kline_multiscale_expert_challenger.json').read_text(encoding='utf-8'))
full = snapshot['full_history_technical_model']
selected = full['selected']
assert selected['accepted_by_full_history_fit'] is True
assert full['release_guard']['sample_split_used'] is False
assert full['release_guard']['holdout_validation_claimed'] is False
assert len(full['current_positions']) >= 20
full_result = full['results'][selected['universe']]
full_candidate = full_result['candidates'][selected['candidate']]
metrics = full_candidate['metrics']['full']
assert metrics['periods'] >= 120
assert metrics['sharpe'] > 0
assert metrics['turnover'] < 0.30
assert full_result['champion']['selection']['minimum_periods'] >= 120

payload = reb.build('technical:kline')
assert payload['governance']['selection_uses_test'] is False
assert payload['governance']['full_history_sample_split_used'] is False
assert payload['governance']['full_history_holdout_validation_claimed'] is False
assert payload['governance']['full_history_candidate']
assert len(payload['visuals']['diagnostics']['table']['rows']) >= 12
assert len(payload['visuals']['descriptive']['table']['rows']) >= 10

governance = mgb.build_model_governance()
assert governance['release'] == '$ExpectedGovernanceRelease'
model = governance['models']['kline_memory']
assert 'technical-signal-stack/1.1' in model['engine']
full_guard = model['robustness']['full_history_fit_guard']
assert full_guard['sample_split_used'] is False
assert full_guard['holdout_validation_claimed'] is False
assert full_guard['current_position_count'] >= 20
assert full_guard['full_history_metrics']['sharpe'] > 0

print(json.dumps({
  'full_history_candidate': selected['candidate'],
  'full_history_sharpe': metrics['sharpe'],
  'full_history_turnover': metrics['turnover'],
  'full_history_periods': metrics['periods'],
  'governance_release': governance['release'],
}, ensure_ascii=False))
"@
    $checkPath = Join-Path (Get-Location) "_technical_full_history_fit_r381_check.py"
    [System.IO.File]::WriteAllText($checkPath, $checkScript, [System.Text.UTF8Encoding]::new($false))
    $checkRaw = & $PythonExe $checkPath
    $checkExit = $LASTEXITCODE
    Remove-Item -LiteralPath $checkPath -Force -ErrorAction SilentlyContinue
    if ($checkExit -ne 0) { throw "technical_contract_check_failed:$checkExit" }
  } finally { Pop-Location }

  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
  Start-Sleep -Seconds 2
  Start-ScheduledTask -TaskName $TaskName
  Start-Sleep -Seconds 8
  $task = Get-ScheduledTask -TaskName $TaskName
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:8099/quant-agent-vnext/healthz" -TimeoutSec 20
  if ($health.version -ne $ExpectedVersion) { throw "health_version_mismatch:$($health.version)" }

  [pscustomobject]@{
    status = "deployed"
    version = $health.version
    root = $ReleaseRoot
    task = $task.State.ToString()
    backup = $BackupRoot
    zip_hash = $actualHash
    snapshot_check = ($checkRaw | ConvertFrom-Json)
    files = $Files.Count
  } | ConvertTo-Json -Depth 8 -Compress
} catch {
  try { Restore-Backup } catch {}
  try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
  } catch {}
  throw
}
