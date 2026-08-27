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
if (Test-Path -LiteralPath (Join-Path $installRoot 'b300-stlink-gui.exe')) {
@'
@echo off
set "B300_OPENOCD=%~dp0..\vendor\openocd\bin\openocd.exe"
"%~dp0..\b300-stlink-gui.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $binRoot 'b300-stlink-gui.cmd') -Encoding ASCII
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
    $shortcutPath = Join-Path $startMenu 'B300 ST-Link Provisioning.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $installRoot 'b300-stlink-gui.exe'
    $shortcut.WorkingDirectory = $installRoot
    $shortcut.IconLocation = (Join-Path $installRoot 'b300-stlink-gui.exe') + ',0'
    $shortcut.Save()
}
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $binRoot)) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $binRoot), 'User')
}
Write-Host 'Installed. Open a new terminal, then run: b300-stlink doctor or b300-stlink-gui'
