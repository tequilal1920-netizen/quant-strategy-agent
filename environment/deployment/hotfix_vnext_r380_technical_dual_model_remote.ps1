Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ReleaseRoot = "F:\apps\quant_strategy_agent_vnext_r35_2_industry_champion_anchor"
$TaskName = "QuantStrategyAgentVNext8099R352IndustryChampion"
$ZipName = "quant_strategy_agent_vnext_r38_technical_dual_model_20260817.zip"
$ExpectedZipHash = "1C8CAC8432457C199B8549F7886AF7952D290592705719811C39C9971CEBD995"
$ExpectedVersion = "2026.08.17-technical-dual-model-vnext-r38.0"
$ExpectedGovernanceRelease = "2026.08.17-technical-dual-model-governed-r38.0"
$BackupRoot = Join-Path $ReleaseRoot "deployment_backups\technical_dual_model_r38_20260817"
$PythonExe = "D:\Download\Anaconda\python.exe"

$Files = @(
  "ai-models\technical-analysis\SKILL.md",
  "ai-models\technical-analysis\PACKAGE.json",
  "ai-models\technical-analysis\runtime\agent_runtime\core.py",
  "ai-models\technical-analysis\source\README.md",
  "ai-models\technical-analysis\references\module-map.md",
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
  "dist\technical_dual_model_r38_release_notes.md"
)

function Copy-TreeFile($SourceRoot, $TargetRoot, $RelPath) {
  $src = Join-Path $SourceRoot $RelPath
  $dst = Join-Path $TargetRoot $RelPath
  $parent = Split-Path -Parent $dst
  if (!(Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  Copy-Item -LiteralPath $src -Destination $dst -Force
}

function Restore-Backup {
  foreach ($rel in $Files) {
    $bak = Join-Path $BackupRoot $rel
    $dst = Join-Path $ReleaseRoot $rel
    if (Test-Path -LiteralPath $bak) {
      $parent = Split-Path -Parent $dst
      if (!(Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
      }
      Copy-Item -LiteralPath $bak -Destination $dst -Force
    }
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
    $src = Join-Path $ReleaseRoot $rel
    if (Test-Path -LiteralPath $src) {
      Copy-TreeFile $ReleaseRoot $BackupRoot $rel
    }
  }

  $stage = Join-Path $ReleaseRoot "_technical_dual_model_r38_stage"
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
  New-Item -ItemType Directory -Path $stage -Force | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
  foreach ($rel in $Files) {
    if (!(Test-Path -LiteralPath (Join-Path $stage $rel))) {
      throw "package_file_missing:$rel"
    }
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
import os
import sys
from pathlib import Path

sys.path.insert(0, 'board/quant_strategy_agent_vnext')
import research_evidence_backend as reb
import model_governance_backend as mgb

snapshot = json.loads(Path('board/quant_strategy_agent_vnext/data/kline_multiscale_expert_challenger.json').read_text(encoding='utf-8'))
pure = snapshot['pure_technical_model']
assert pure['version'] == 'technical-signal-stack/1.0-broker-style'
assert pure['selected']['accepted_by_train_validation'] is True
assert not bool(pure.get('release_guard', {}).get('release_approved'))
assert snapshot['selected']['accepted_by_train_validation'] is True
assert snapshot['deployment_selected']['release_approved'] is False

payload = reb.build('technical:kline')
rows = payload['visuals']['diagnostics']['table']['rows']
assert len(rows) >= 8
assert rows[0]['sharpe'] > 1.5 and rows[1]['sharpe'] > 1.5 and rows[2]['sharpe'] < 0
assert rows[4]['sharpe'] > 1.5 and rows[5]['sharpe'] > 1.5 and rows[6]['sharpe'] < 0
assert payload['governance']['selection_uses_test'] is False
assert payload['governance']['pure_technical_selection_uses_test'] is False
assert payload['governance']['pure_technical_release_approved'] is False

governance = mgb.build_model_governance()
assert governance['release'] == '$ExpectedGovernanceRelease'
model = governance['models']['kline_memory']
assert 'technical-signal-stack/1.0' in model['engine']
assert 'kline-multiscale-expert/1.6' in model['engine']
assert model['gate'] == 'research_diagnostic'
assert model['robustness']['pure_technical_release_guard']['selection_uses_test'] is False
assert model['robustness']['pure_technical_release_guard']['release_approved'] is False
assert model['robustness']['pure_technical_release_guard']['sealed_test']['sharpe'] < 0

package = json.loads(Path('ai-models/technical-analysis/PACKAGE.json').read_text(encoding='utf-8'))
assert package['title'] == '\u6280\u672f\u5206\u6790'
assert 'framework/backtest/technical_signal_model.py' in package['shared_repository_dependencies']

print(json.dumps({
  'pure_status': pure['status'],
  'model_1_train_sharpe': rows[4]['sharpe'],
  'model_1_valid_sharpe': rows[5]['sharpe'],
  'model_1_test_sharpe': rows[6]['sharpe'],
  'governance_release': governance['release'],
}, ensure_ascii=False))
"@
    $checkPath = Join-Path (Get-Location) "_technical_dual_model_r38_check.py"
    [System.IO.File]::WriteAllText($checkPath, $checkScript, [System.Text.UTF8Encoding]::new($false))
    $checkRaw = & $PythonExe $checkPath
    $checkExit = $LASTEXITCODE
    Remove-Item -LiteralPath $checkPath -Force -ErrorAction SilentlyContinue
    if ($checkExit -ne 0) { throw "technical_contract_check_failed:$checkExit" }
  } finally {
    Pop-Location
  }

  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
  Start-Sleep -Seconds 2
  Start-ScheduledTask -TaskName $TaskName
  Start-Sleep -Seconds 8
  $task = Get-ScheduledTask -TaskName $TaskName
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:8099/quant-agent-vnext/healthz" -TimeoutSec 20
  if ($health.version -ne $ExpectedVersion) {
    throw "health_version_mismatch:$($health.version)"
  }

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
