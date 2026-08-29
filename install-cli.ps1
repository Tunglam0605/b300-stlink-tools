param(
    [switch]$DownloadOnly
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'Tunglam0605/b300-stlink-tools'
$ManifestUrl = "https://github.com/$Repo/releases/latest/download/latest-cli.json"
$SignatureUrl = "https://github.com/$Repo/releases/latest/download/latest-cli.json.minisig"
$PublicKey = 'RWSjwseDEGd6o+Ykylwi3nmXPA7DYtOhvuXvHBQxf58Dej383Hd+5eYN'
$MinisignVersion = '0.12'
$MinisignUrl = "https://github.com/jedisct1/minisign/releases/download/$MinisignVersion/minisign-0.12-win64.zip"
$MinisignSha256 = '37b600344e20c19314b2e82813db2bfdcc408b77b876f7727889dbd46d539479'
$PlatformKey = 'windows-x64-cli'
$ExpectedFile = 'B300-STLink-CLI-Windows-x64.zip'

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'B300 CLI bootstrap supports native Windows x64 (AMD64) only.'
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Download-File([string]$Url, [string]$Path) {
    Write-Host "Downloading $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path
}

$temp = Join-Path ([IO.Path]::GetTempPath()) ('b300-cli-bootstrap-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $manifestPath = Join-Path $temp 'latest-cli.json'
    $signaturePath = Join-Path $temp 'latest-cli.json.minisig'
    Download-File $ManifestUrl $manifestPath
    Download-File $SignatureUrl $signaturePath

    $minisign = (Get-Command minisign.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    if ([String]::IsNullOrWhiteSpace($minisign)) {
        $archive = Join-Path $temp 'minisign-0.12-win64.zip'
        Download-File $MinisignUrl $archive
        $actual = Get-Sha256 $archive
        if ($actual -ne $MinisignSha256) {
            throw "Pinned minisign bootstrap SHA-256 mismatch: $actual"
        }
        $miniRoot = Join-Path $temp 'minisign'
        Expand-Archive -LiteralPath $archive -DestinationPath $miniRoot -Force
        $minisign = Join-Path $miniRoot 'minisign-win64\x86_64\minisign.exe'
        if (-not (Test-Path -LiteralPath $minisign -PathType Leaf)) {
            throw 'Pinned minisign executable is missing after extraction.'
        }
    }

    & $minisign -Vm $manifestPath -x $signaturePath -P $PublicKey
    if ($LASTEXITCODE -ne 0) {
        throw 'B300 latest-cli.json signature verification failed.'
    }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $asset = $manifest.platforms.$PlatformKey
    if ($null -eq $asset) {
        throw "Signed manifest does not contain platform $PlatformKey."
    }
    if ($asset.file -ne $ExpectedFile) {
        throw "Signed manifest selected unexpected file: $($asset.file)"
    }
    $expectedUrlPattern = '^https://github\.com/Tunglam0605/b300-stlink-tools/releases/download/v[0-9]+\.[0-9]+\.[0-9]+/B300-STLink-CLI-Windows-x64\.zip$'
    if ([string]$asset.url -notmatch $expectedUrlPattern) {
        throw "Signed manifest contains an unexpected immutable asset URL: $($asset.url)"
    }
    if ([string]$asset.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'Signed manifest contains an invalid SHA-256.'
    }

    $package = Join-Path $temp $ExpectedFile
    Download-File ([string]$asset.url) $package
    if ((Get-Sha256 $package) -ne [string]$asset.sha256) {
        throw 'Downloaded B300 CLI package SHA-256 mismatch.'
    }
    if ((Get-Item -LiteralPath $package).Length -ne [Int64]$asset.size) {
        throw 'Downloaded B300 CLI package size mismatch.'
    }
    Write-Host "Verified signed B300 CLI v$($manifest.version) package."

    if ($DownloadOnly) {
        $destination = Join-Path (Get-Location) $ExpectedFile
        Copy-Item -LiteralPath $package -Destination $destination -Force
        Write-Host "Verified package copied to: $destination"
        exit 0
    }

    $stage = Join-Path $temp 'bundle'
    Expand-Archive -LiteralPath $package -DestinationPath $stage -Force
    $installer = Join-Path $stage 'install.ps1'
    $runtime = Join-Path $stage '_internal'
    $exe = Join-Path $stage 'b300-stlink.exe'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf) -or
        -not (Test-Path -LiteralPath $runtime -PathType Container) -or
        -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw 'Verified package is incomplete after extraction.'
    }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) {
        throw "B300 managed installer failed with exit code $LASTEXITCODE."
    }

    $launcher = Join-Path $env:LOCALAPPDATA 'B300-STLink\bin\b300-stlink.cmd'
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw 'B300 CLI launcher was not created by the managed installer.'
    }
    & $launcher --version
    if ($LASTEXITCODE -ne 0) {
        throw 'Installed B300 CLI version check failed.'
    }
    Write-Host ''
    Write-Host 'Gateway preflight:'
    & $launcher gateway doctor
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'B300 CLI is installed; Gateway preflight still reports a host setup requirement (commonly SSH Server or ST-Link).'
    }
    Write-Host ''
    Write-Host 'Installed. Open a new terminal and run: b300-stlink gateway doctor'
    Write-Host 'When READY, start the headless Gateway with: b300-stlink debug'
}
finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
