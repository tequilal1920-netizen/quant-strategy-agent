param(
  [Parameter(Mandatory = $true)]
  [string]$OldUsername,
  [Parameter(Mandatory = $true)]
  [string]$OldPassword,
  [Parameter(Mandatory = $true)]
  [string]$NewUsername,
  [Parameter(Mandatory = $true)]
  [string]$NewPassword,
  [string]$Python = "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$FactorDb = "F:\apps\factor_mining_public_8895\state\users_history.sqlite"
$KlineDb = "F:\apps\kline_agent_public_8877\outputs\kline_public_auth.sqlite3"
$FactorSecret = "F:\apps\factor_mining_public_8895\state\flask_session_secret.txt"
$EnvPaths = @(
  "F:\apps\quant_strategy_agent_canonical_r14\board\quant_strategy_agent\private\quant_agent.env",
  "F:\apps\quant_strategy_agent\private\quant_agent.env"
) | Where-Object { Test-Path -LiteralPath $_ }
$BackupRoot = "F:\apps\quant_strategy_agent\deployment_backups\credential_migration_{0}" -f (
  Get-Date -Format "yyyyMMdd_HHmmss"
)

foreach ($Required in @($Python, $FactorDb, $KlineDb)) {
  if (-not (Test-Path -LiteralPath $Required)) {
    throw "Required authentication migration dependency is missing: $Required"
  }
}
if ($EnvPaths.Count -eq 0) { throw "No quant-agent private environment file was found." }

New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
$EnvBackupMap = @{}
for ($Index = 0; $Index -lt $EnvPaths.Count; $Index += 1) {
  $Target = Join-Path $BackupRoot ("quant_agent_{0}.env" -f $Index)
  Copy-Item -LiteralPath $EnvPaths[$Index] -Destination $Target -Force
  $EnvBackupMap[$EnvPaths[$Index]] = $Target
}
$FactorSecretExisted = Test-Path -LiteralPath $FactorSecret
if ($FactorSecretExisted) {
  Copy-Item -LiteralPath $FactorSecret -Destination (
    Join-Path $BackupRoot "factor_session_secret.txt"
  ) -Force
}

function Stop-AuthServices {
  foreach ($TaskName in @("QuantStrategyAgent8071", "FactorMiningPublic8895", "KlineAgentPublic8877")) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  }
  foreach ($Port in @(8071, 8895, 8877)) {
    for ($Attempt = 0; $Attempt -lt 5; $Attempt += 1) {
      $Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
      if ($Listeners.Count -eq 0) { break }
      $Listeners | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
      }
      Start-Sleep -Milliseconds 300
    }
  }
  $ServiceRoots = @(
    "F:\apps\quant_strategy_agent_canonical_r14\board\quant_strategy_agent",
    "F:\apps\factor_mining_public_8895",
    "F:\apps\kline_agent_public_8877"
  )
  Get-CimInstance Win32_Process | Where-Object {
    $CommandLine = [string]$_.CommandLine
    $ServiceRoots | Where-Object { $CommandLine.Contains($_) }
  } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 1
}

function Start-ServiceTask([string]$TaskName, [int]$Port) {
  Start-ScheduledTask -TaskName $TaskName
  for ($Attempt = 0; $Attempt -lt 120; $Attempt += 1) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { return }
    Start-Sleep -Seconds 1
  }
  throw "Service did not listen: $TaskName"
}

function Set-PrivateEnvValue([string]$Path, [string]$Name, [string]$Value) {
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
  [IO.File]::WriteAllLines($Path, $Lines, (New-Object Text.UTF8Encoding($false)))
}

function New-RandomSecret {
  $Bytes = New-Object byte[] 48
  $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $Generator.GetBytes($Bytes) } finally { $Generator.Dispose() }
  return [Convert]::ToBase64String($Bytes)
}

function Test-JsonLogin(
  [int]$Port,
  [string]$Path,
  [string]$Username,
  [string]$Password
) {
  try {
    $Body = @{ username = $Username; password = $Password } | ConvertTo-Json -Compress
    $Response = Invoke-WebRequest -UseBasicParsing -Uri (
      "http://127.0.0.1:{0}{1}" -f $Port, $Path
    ) -Method Post -ContentType "application/json" -Body $Body -TimeoutSec 20
    return $Response.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Test-ShellLogin([string]$Username, [string]$Password) {
  try {
    $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8071/login" `
      -Method Post -Body @{ username = $Username; password = $Password } `
      -WebSession $Session -MaximumRedirection 5 -TimeoutSec 20
    return $Response.StatusCode -eq 200 -and $Response.Content -match "app\.js"
  } catch {
    return $false
  }
}

$PythonCode = @'
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from pathlib import Path

old_password = os.environ["AUTH_OLD_PASSWORD"]
new_username = os.environ["AUTH_NEW_USERNAME"]
new_password = os.environ["AUTH_NEW_PASSWORD"]
backup_root = Path(os.environ["AUTH_BACKUP_ROOT"])
factor_path = Path(os.environ["AUTH_FACTOR_DB"])
kline_path = Path(os.environ["AUTH_KLINE_DB"])

def backup_db(source, target):
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()

backup_db(factor_path, backup_root / "factor_auth_before.sqlite")
backup_db(kline_path, backup_root / "kline_auth_before.sqlite3")

factor = sqlite3.connect(factor_path, timeout=30)
try:
    factor.execute("begin immediate")
    users = factor.execute("select id,username,password_hash,salt from users").fetchall()
    if len(users) != 1:
        raise RuntimeError(f"factor user count changed: {len(users)}")
    user_id, _, old_digest, old_salt = users[0]
    salt_bytes = base64.b64decode(old_salt.encode("ascii"))
    expected = base64.b64encode(
        hashlib.pbkdf2_hmac("sha256", old_password.encode("utf-8"), salt_bytes, 200000)
    ).decode("ascii")
    if not hmac.compare_digest(expected, old_digest):
        raise RuntimeError("factor old password verification failed")
    if factor.execute(
        "select count(*) from users where username=? and id<>?", (new_username, user_id)
    ).fetchone()[0]:
        raise RuntimeError("factor target username already exists")
    factor_jobs_before = factor.execute(
        "select count(*) from job_history where user_id=?", (user_id,)
    ).fetchone()[0]
    factor_memory_before = factor.execute(
        "select count(*) from factor_agent_search_memory"
    ).fetchone()[0]
    new_salt_bytes = secrets.token_bytes(16)
    new_digest = base64.b64encode(
        hashlib.pbkdf2_hmac("sha256", new_password.encode("utf-8"), new_salt_bytes, 200000)
    ).decode("ascii")
    factor.execute(
        "update users set username=?,password_hash=?,salt=? where id=?",
        (new_username, new_digest, base64.b64encode(new_salt_bytes).decode("ascii"), user_id),
    )
    factor.commit()
    factor_jobs_after = factor.execute(
        "select count(*) from job_history where user_id=?", (user_id,)
    ).fetchone()[0]
    factor_memory_after = factor.execute(
        "select count(*) from factor_agent_search_memory"
    ).fetchone()[0]
finally:
    factor.close()

kline = sqlite3.connect(kline_path, timeout=30)
try:
    kline.execute("begin immediate")
    users = kline.execute("select username,salt,password_hash from users").fetchall()
    if len(users) != 1:
        raise RuntimeError(f"kline user count changed: {len(users)}")
    old_username, old_salt_hex, old_digest_hex = users[0]
    expected_hex = hashlib.pbkdf2_hmac(
        "sha256", old_password.encode("utf-8"), bytes.fromhex(old_salt_hex), 180000
    ).hex()
    if not hmac.compare_digest(expected_hex, old_digest_hex):
        raise RuntimeError("kline old password verification failed")
    if kline.execute(
        "select count(*) from users where username=? and username<>?",
        (new_username, old_username),
    ).fetchone()[0]:
        raise RuntimeError("kline target username already exists")
    kline_jobs_before = kline.execute(
        "select count(*) from job_history where username=?", (old_username,)
    ).fetchone()[0]
    sessions_before = kline.execute("select count(*) from sessions").fetchone()[0]
    new_salt_bytes = secrets.token_bytes(16)
    new_digest_hex = hashlib.pbkdf2_hmac(
        "sha256", new_password.encode("utf-8"), new_salt_bytes, 180000
    ).hex()
    kline.execute(
        "update job_history set username=? where username=?", (new_username, old_username)
    )
    kline.execute("delete from sessions")
    kline.execute(
        "update users set username=?,salt=?,password_hash=? where username=?",
        (new_username, new_salt_bytes.hex(), new_digest_hex, old_username),
    )
    kline.commit()
    kline_jobs_after = kline.execute(
        "select count(*) from job_history where username=?", (new_username,)
    ).fetchone()[0]
    sessions_after = kline.execute("select count(*) from sessions").fetchone()[0]
finally:
    kline.close()

result = {
    "factor_user_rows": 1,
    "factor_jobs_preserved": factor_jobs_before == factor_jobs_after,
    "factor_memory_preserved": factor_memory_before == factor_memory_after,
    "kline_user_rows": 1,
    "kline_jobs_preserved": kline_jobs_before == kline_jobs_after,
    "kline_sessions_revoked": sessions_before - sessions_after,
}
if not all(
    [
        result["factor_jobs_preserved"],
        result["factor_memory_preserved"],
        result["kline_jobs_preserved"],
    ]
):
    raise RuntimeError("content preservation assertion failed")
print(json.dumps(result, separators=(",", ":")))
'@

try {
  Stop-AuthServices
  $env:AUTH_OLD_PASSWORD = $OldPassword
  $env:AUTH_NEW_USERNAME = $NewUsername
  $env:AUTH_NEW_PASSWORD = $NewPassword
  $env:AUTH_BACKUP_ROOT = $BackupRoot
  $env:AUTH_FACTOR_DB = $FactorDb
  $env:AUTH_KLINE_DB = $KlineDb
  $PythonBytes = [Text.Encoding]::UTF8.GetBytes($PythonCode)
  $PythonBase64 = [Convert]::ToBase64String($PythonBytes)
  $MigrationJson = & $Python -c (
    "import base64;exec(base64.b64decode('{0}'))" -f $PythonBase64
  )
  if ($LASTEXITCODE -ne 0) { throw "Authentication database migration failed." }

  foreach ($EnvPath in $EnvPaths) {
    Set-PrivateEnvValue $EnvPath "QUANT_AGENT_USER" $NewUsername
    Set-PrivateEnvValue $EnvPath "QUANT_AGENT_PASSWORD" $NewPassword
    Set-PrivateEnvValue $EnvPath "QUANT_AGENT_SECRET" (New-RandomSecret)
  }
  [IO.File]::WriteAllText(
    $FactorSecret,
    (New-RandomSecret),
    (New-Object Text.UTF8Encoding($false))
  )

  Start-ServiceTask "FactorMiningPublic8895" 8895
  Start-ServiceTask "KlineAgentPublic8877" 8877
  Start-ServiceTask "QuantStrategyAgent8071" 8071

  $FactorOld = Test-JsonLogin 8895 "/api/auth/login" $OldUsername $OldPassword
  $FactorNew = Test-JsonLogin 8895 "/api/auth/login" $NewUsername $NewPassword
  $KlineOld = Test-JsonLogin 8877 "/api/login" $OldUsername $OldPassword
  $KlineNew = Test-JsonLogin 8877 "/api/login" $NewUsername $NewPassword
  $ShellOld = Test-ShellLogin $OldUsername $OldPassword
  $ShellNew = Test-ShellLogin $NewUsername $NewPassword
  if (
    -not $FactorNew -or $FactorOld -or -not $KlineNew -or $KlineOld -or
    -not $ShellNew -or $ShellOld
  ) {
    throw (
      "Post-migration login assertions failed: " +
      "factorNew=$FactorNew factorOld=$FactorOld " +
      "klineNew=$KlineNew klineOld=$KlineOld " +
      "shellNew=$ShellNew shellOld=$ShellOld"
    )
  }
  $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8071/healthz" -TimeoutSec 20
  [pscustomobject]@{
    Status = "migrated"
    BackupRoot = $BackupRoot
    DatabaseMigration = ($MigrationJson | ConvertFrom-Json)
    FactorNewLogin = $FactorNew
    FactorOldLoginRejected = (-not $FactorOld)
    KlineNewLogin = $KlineNew
    KlineOldLoginRejected = (-not $KlineOld)
    ShellNewLogin = $ShellNew
    ShellOldLoginRejected = (-not $ShellOld)
    Version = $Health.version
  } | ConvertTo-Json -Depth 6 -Compress
} catch {
  $OriginalError = $_.Exception.Message
  Stop-AuthServices
  foreach ($Path in @($FactorDb, $KlineDb)) {
    foreach ($Suffix in @("-wal", "-shm")) {
      $Sidecar = $Path + $Suffix
      if (Test-Path -LiteralPath $Sidecar) { Remove-Item -LiteralPath $Sidecar -Force }
    }
  }
  $FactorBackup = Join-Path $BackupRoot "factor_auth_before.sqlite"
  $KlineBackup = Join-Path $BackupRoot "kline_auth_before.sqlite3"
  if (Test-Path -LiteralPath $FactorBackup) {
    Copy-Item -LiteralPath $FactorBackup -Destination $FactorDb -Force
  }
  if (Test-Path -LiteralPath $KlineBackup) {
    Copy-Item -LiteralPath $KlineBackup -Destination $KlineDb -Force
  }
  foreach ($Entry in $EnvBackupMap.GetEnumerator()) {
    Copy-Item -LiteralPath $Entry.Value -Destination $Entry.Key -Force
  }
  if ($FactorSecretExisted) {
    Copy-Item -LiteralPath (Join-Path $BackupRoot "factor_session_secret.txt") `
      -Destination $FactorSecret -Force
  } elseif (Test-Path -LiteralPath $FactorSecret) {
    Remove-Item -LiteralPath $FactorSecret -Force
  }
  foreach ($TaskName in @("FactorMiningPublic8895", "KlineAgentPublic8877", "QuantStrategyAgent8071")) {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  }
  throw "Credential migration rolled back: $OriginalError"
}
