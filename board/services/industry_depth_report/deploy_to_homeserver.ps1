param(
  [string]$HostAlias = "homeserver",
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
)

$ErrorActionPreference = "Stop"
$Zip = Join-Path $ProjectRoot "output\deployment\industry_depth_report_public_8892.zip"
$RemoteZip = "C:/Users/admin/industry_depth_report_public_8892.zip"
$RemoteDeploy = "C:/Users/admin/remote_deploy_industry_depth_report_8892.ps1"
$RemoteDeployLocal = Join-Path $ProjectRoot "board\services\industry_depth_report\remote_deploy_industry_depth_report_8892.ps1"

Set-Location $ProjectRoot
if (Test-Path $Zip) {
  Remove-Item -LiteralPath $Zip -Force
}

$Temp = Join-Path $env:TEMP ("industry_depth_report_package_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

foreach ($dir in @(
  "model\data_dashboard\industry_depth_report",
  "framework\data_pipeline"
)) {
  $dest = Join-Path $Temp $dir
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
  Copy-Item -LiteralPath (Join-Path $ProjectRoot $dir) -Destination $dest -Recurse -Force
}

$WebSrc = Join-Path $ProjectRoot "board\services\industry_depth_report"
$WebDest = Join-Path $Temp "board\services\industry_depth_report"
New-Item -ItemType Directory -Force -Path $WebDest | Out-Null
Get-ChildItem -LiteralPath $WebSrc -File |
  Where-Object { $_.Name -match '\.(py|ps1|md)$' } |
  ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $WebDest -Force }

foreach ($relative in @(
  "board\services\industry_depth_report\runtime",
  "board\services\industry_depth_report\vendor",
  "board\services\industry_depth_report\logs",
  "output\industry_depth_report"
)) {
  $target = Join-Path $Temp $relative
  if (Test-Path $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
  }
}

Compress-Archive -Path (Join-Path $Temp "*") -DestinationPath $Zip -Force
Remove-Item -LiteralPath $Temp -Recurse -Force

scp $Zip "$HostAlias`:$RemoteZip"
scp $RemoteDeployLocal "$HostAlias`:$RemoteDeploy"
ssh $HostAlias "powershell -NoProfile -ExecutionPolicy Bypass -File `"$RemoteDeploy`""
