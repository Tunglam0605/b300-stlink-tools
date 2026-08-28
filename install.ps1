$ErrorActionPreference = 'Stop'

function Get-NormalizedPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ([StringComparer]::OrdinalIgnoreCase.Equals($full, $root)) {
        return $root
    }
    return $full.TrimEnd([char[]]@(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ))
}

function Test-PathWithin([string]$Path, [string]$Base) {
    $candidate = Get-NormalizedPath $Path
    $parent = Get-NormalizedPath $Base
    if ([StringComparer]::OrdinalIgnoreCase.Equals($candidate, $parent)) {
        return $true
    }
    return $candidate.StartsWith(
        $parent + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-NoReparsePoint([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Managed install path contains an unsafe reparse point: $Path"
        }
    }
}

$bundleRoot = Get-NormalizedPath (Split-Path -Parent $MyInvocation.MyCommand.Path)
$userProfile = [Environment]::GetFolderPath('UserProfile')
if ([String]::IsNullOrWhiteSpace($userProfile) -or
    -not [IO.Path]::IsPathRooted($userProfile)) {
    throw 'Managed install requires an absolute per-user UserProfile.'
}
$userProfile = Get-NormalizedPath $userProfile
$userProfileRoot = [IO.Path]::GetPathRoot($userProfile)
if ([StringComparer]::OrdinalIgnoreCase.Equals($userProfile, $userProfileRoot)) {
    throw 'Managed install refuses a filesystem root as UserProfile.'
}
if ([String]::IsNullOrWhiteSpace($env:LOCALAPPDATA) -or
    -not [IO.Path]::IsPathRooted($env:LOCALAPPDATA)) {
    throw 'Managed install requires an absolute per-user LOCALAPPDATA.'
}
$localAppData = Get-NormalizedPath $env:LOCALAPPDATA
if (-not (Test-PathWithin $localAppData $userProfile) -or
    [StringComparer]::OrdinalIgnoreCase.Equals($localAppData, $userProfile)) {
    throw 'Managed install requires LOCALAPPDATA beneath the per-user UserProfile.'
}
Assert-NoReparsePoint $localAppData
$installRoot = Get-NormalizedPath (Join-Path $localAppData 'B300-STLink')
$binRoot = Join-Path $installRoot 'bin'
if ((Test-PathWithin $bundleRoot $installRoot) -or
    (Test-PathWithin $installRoot $bundleRoot)) {
    throw 'Run b300-stlink self-update from a managed install; source and destination overlap.'
}
Assert-NoReparsePoint $installRoot
Assert-NoReparsePoint $binRoot
Assert-NoReparsePoint (Join-Path $binRoot 'b300-stlink.cmd')
$cliSource = Join-Path $bundleRoot 'b300-stlink.exe'
$guiSource = Join-Path $bundleRoot 'b300-stlink-gui.exe'
if (-not (Test-Path -LiteralPath $cliSource -PathType Leaf) -and
    -not (Test-Path -LiteralPath $guiSource -PathType Leaf)) {
    throw 'Incomplete B300 native bundle: executable is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot '_internal') -PathType Container)) {
    throw 'Incomplete B300 Windows onedir bundle: _internal runtime is missing.'
}
New-Item -ItemType Directory -Force -Path $installRoot, $binRoot | Out-Null
Get-ChildItem -LiteralPath $bundleRoot -Force | Copy-Item -Destination $installRoot -Recurse -Force
@'
@echo off
"%~dp0..\b300-stlink.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $binRoot 'b300-stlink.cmd') -Encoding ASCII
if (Test-Path -LiteralPath (Join-Path $installRoot 'b300-stlink-gui.exe')) {
    Assert-NoReparsePoint (Join-Path $binRoot 'b300-stlink-gui.cmd')
@'
@echo off
"%~dp0..\b300-stlink-gui.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $binRoot 'b300-stlink-gui.cmd') -Encoding ASCII
    if ([String]::IsNullOrWhiteSpace($env:APPDATA) -or
        -not [IO.Path]::IsPathRooted($env:APPDATA) -or
        -not (Test-PathWithin $env:APPDATA $userProfile)) {
        throw 'GUI shortcut requires APPDATA beneath the per-user UserProfile.'
    }
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
