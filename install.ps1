$ErrorActionPreference = 'Stop'
$bundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installRoot = Join-Path $env:LOCALAPPDATA 'B300-STLink'
$binRoot = Join-Path $installRoot 'bin'
$cliSource = Join-Path $bundleRoot 'b300-stlink.exe'
$guiSource = Join-Path $bundleRoot 'b300-stlink-gui.exe'
if (-not (Test-Path -LiteralPath $cliSource -PathType Leaf) -and
    -not (Test-Path -LiteralPath $guiSource -PathType Leaf)) {
    throw 'Incomplete B300 native bundle: executable is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot '_internal') -PathType Container)) {
    throw 'Incomplete B300 Windows onedir bundle: _internal runtime is missing.'
}
if ([StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetFullPath($bundleRoot), [IO.Path]::GetFullPath($installRoot))) {
    throw 'Run b300-stlink self-update from a managed install; do not copy it over itself.'
}
New-Item -ItemType Directory -Force -Path $installRoot, $binRoot | Out-Null
Get-ChildItem -LiteralPath $bundleRoot -Force | Copy-Item -Destination $installRoot -Recurse -Force
@'
@echo off
"%~dp0..\b300-stlink.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $binRoot 'b300-stlink.cmd') -Encoding ASCII
if (Test-Path -LiteralPath (Join-Path $installRoot 'b300-stlink-gui.exe')) {
@'
@echo off
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
Write-Host 'Installed. Open a new terminal, then run: b300-stlink doctor, b300-stlink setup, or b300-stlink-gui'
