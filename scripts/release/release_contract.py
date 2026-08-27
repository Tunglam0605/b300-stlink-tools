"""Canonical B300 GitHub Release asset contract."""

EXPECTED_PACKAGE_ASSETS = (
    "B300-STLink-GUI-Windows-x64.exe",
    "B300-STLink-GUI-Windows-x64.zip",
    "B300-STLink-GUI-Ubuntu-x64.AppImage",
    "b300-stlink-gui_amd64.deb",
    "B300-STLink-GUI-Ubuntu-arm64.AppImage",
    "b300-stlink-gui_arm64.deb",
    "B300-STLink-CLI-Windows-x64.zip",
    "B300-STLink-CLI-Linux-x64.tar.gz",
    "B300-STLink-CLI-Linux-arm64.tar.gz",
)

METADATA_ASSETS = (
    "SHA256SUMS.txt",
    "release-manifest.json",
    "release-manifest.json.minisig",
    "latest.json",
    "latest.json.minisig",
)

UPDATE_PLATFORM_FILES = {
    "windows-x64": "B300-STLink-GUI-Windows-x64.exe",
    "linux-x64-appimage": "B300-STLink-GUI-Ubuntu-x64.AppImage",
    "linux-x64-deb": "b300-stlink-gui_amd64.deb",
    "linux-arm64-appimage": "B300-STLink-GUI-Ubuntu-arm64.AppImage",
    "linux-arm64-deb": "b300-stlink-gui_arm64.deb",
}
