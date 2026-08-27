$ErrorActionPreference = 'Stop'
$bundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installRoot = Join-Path $env:LOCALAPPDATA 'B300-STLink'
$binRoot = Join-Path $installRoot 'bin'
New-Item -ItemType Directory -Force -Path $installRoot, $binRoot | Out-Null
Get-ChildItem -LiteralPath $bundleRoot -Force | Copy-Item -Destination $installRoot -Recurse -Force
@'
@echo off
set "B300_OPENOCD=%~dp0..\vendor\openocd\bin\openocd.exe"
"%~dp0..\b300-stlink.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $binRoot 'b300-stlink.cmd') -Encoding ASCII
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $binRoot)) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $binRoot), 'User')
}
Write-Host 'Installed. Open a new terminal, then run: b300-stlink doctor'
