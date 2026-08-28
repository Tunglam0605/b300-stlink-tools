#!/usr/bin/env python3
"""Build a native self-contained B300 ST-Link release on its target OS."""
from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
from pathlib import Path

from b300_version import __version__ as TOOL_VERSION
from b300_core.offline_setup import (
    TRUSTED_OPENOCD_PACKAGES,
    extract_trusted_openocd_package,
)


ROOT = Path(__file__).resolve().parent
VERSION = "0.12.0-7"
BASE = "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v%s" % VERSION


def target_for(system: str, machine: str, python_platform: str):
    system, machine, python_platform = system.lower(), machine.lower(), python_platform.lower()
    if not machine:
        machine = {
            "win-amd64": "x86_64",
            "linux-x86_64": "x86_64",
            "linux-aarch64": "aarch64",
        }.get(python_platform, "")
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64", "win32-x64", ".zip", "install.ps1", "b300-stlink.exe"
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return "linux-x64", "linux-x64", ".tar.gz", "install.sh", "b300-stlink"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "linux-arm64", "linux-arm64", ".tar.gz", "install.sh", "b300-stlink"
    raise RuntimeError("Unsupported target: %s/%s" % (system, machine))


def target():
    return target_for(platform.system(), platform.machine(), sysconfig.get_platform())


def release_names(platform_name: str):
    if platform_name == "windows-x64":
        return (
            "B300-STLink-GUI-Windows-x64.zip",
            "B300-STLink-CLI-Windows-x64.zip",
        )
    if platform_name == "linux-x64":
        return (
            "B300-STLink-GUI-Linux-x64.tar.gz",
            "B300-STLink-CLI-Linux-x64.tar.gz",
        )
    if platform_name == "linux-arm64":
        return (
            "B300-STLink-GUI-Linux-arm64.tar.gz",
            "B300-STLink-CLI-Linux-arm64.tar.gz",
        )
    raise RuntimeError("Unsupported release platform: %s" % platform_name)


def gui_resources(platform_name: str):
    resources = [
        ROOT / "LICENSE",
        ROOT / "branding" / "b300-stlink-icon.png",
        ROOT / "branding" / "b300-stlink-icon.ico",
        ROOT / "branding" / "b300-stlink-wordmark.png",
    ]
    if platform_name.startswith("linux-"):
        resources.extend([
            ROOT / "packaging" / "linux" / "b300-stlink-gui.desktop",
            ROOT / "packaging" / "linux" / "b300-stlink-gui.svg",
        ])
    return resources


def runtime_resources(platform_name: str):
    """Immutable resources required by both frozen entry points."""
    del platform_name
    return [
        ROOT / "resources" / "firmware" / "b300_bootloader_f407ze_com3_v00050001.hex",
        ROOT / "resources" / "firmware" / "b300_bootloader_manifest.json",
    ]


def pyinstaller_data_argument(source: Path) -> str:
    separator = ";" if platform.system().lower() == "windows" else ":"
    return "%s%sresources/firmware" % (source, separator)


def fetch(url: str, output: Path) -> None:
    with urllib.request.urlopen(url) as source, output.open("wb") as destination:
        shutil.copyfileobj(source, destination)


def validate_trusted_package(platform_name: str, filename: str, digest: str) -> None:
    trusted_name, trusted_digest = TRUSTED_OPENOCD_PACKAGES[platform_name]
    if filename != trusted_name or digest.lower() != trusted_digest.lower():
        raise RuntimeError(
            "Downloaded OpenOCD package does not match the built-in trust anchor."
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release")
    parser.add_argument("--internal-distribution-approved", action="store_true")
    parser.add_argument("--flavor", choices=("all", "gui", "cli"), default="all")
    args = parser.parse_args(argv)
    if not args.internal_distribution_approved:
        parser.error("--internal-distribution-approved is required.")
    platform_name, xpack_name, extension, installer, executable = target()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b300-openocd-") as temp:
        temp = Path(temp)
        filename = "xpack-openocd-%s-%s%s" % (VERSION, xpack_name, extension)
        archive, checksum = temp / filename, temp / (filename + ".sha")
        fetch("%s/%s" % (BASE, filename), archive)
        fetch("%s/%s.sha" % (BASE, filename), checksum)
        verified_sha256 = checksum.read_text().split()[0].lower()
        if hashlib.sha256(archive.read_bytes()).hexdigest() != verified_sha256:
            raise RuntimeError("OpenOCD checksum mismatch.")
        validate_trusted_package(platform_name, filename, verified_sha256)
        openocd_root = temp / "openocd-runtime"
        extract_trusted_openocd_package(archive, openocd_root, platform_name)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user",
                               "-r", str(ROOT / "requirements-build.txt")])
        if args.flavor in {"all", "cli"}:
            command = [
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--onefile", "--name", "b300-stlink", "--distpath", str(args.output_dir),
                "--workpath", str(temp / "pyinstaller-cli"),
                "--icon", str(ROOT / "branding" / "b300-stlink-icon.ico"),
            ]
            for resource in runtime_resources(platform_name):
                command.extend(["--add-data", pyinstaller_data_argument(resource)])
            command.append(str(ROOT / "b300_stlink.py"))
            subprocess.check_call(command)
        gui_executable = "b300-stlink-gui.exe" if platform_name == "windows-x64" else "b300-stlink-gui"
        gui_application_root = None
        if args.flavor in {"all", "gui"}:
            gui_spec = (ROOT / "b300_gui_windows.spec"
                        if platform_name == "windows-x64" else ROOT / "b300_gui.spec")
            subprocess.check_call([
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--distpath", str(args.output_dir),
                "--workpath", str(temp / "pyinstaller-gui"),
                str(gui_spec),
            ])
            if platform_name == "windows-x64":
                gui_application_root = args.output_dir / "b300-stlink-gui"
                gui_executable = str(Path("b300-stlink-gui") / "b300-stlink-gui.exe")
        gui_name, cli_name = release_names(platform_name)

        def package(flavor_name, selected_executable, output_name, resources, application_root=None):
            command = [
                sys.executable, str(ROOT / "package_internal.py"),
                "--flavor", flavor_name,
                "--executable", str(args.output_dir / selected_executable),
                "--openocd-root", str(openocd_root),
                "--bootstrap", str(ROOT / installer),
                "--output", str(args.output_dir / output_name),
                "--platform", platform_name, "--internal-distribution-approved",
                "--version", TOOL_VERSION,
                "--openocd-archive", filename,
                "--openocd-sha256", verified_sha256,
                "--openocd-package", str(archive),
            ]
            if application_root is not None:
                command.extend(["--application-root", str(application_root)])
            for resource in resources:
                command.extend(["--resource", str(resource)])
            subprocess.check_call(command)

        if args.flavor in {"all", "cli"}:
            package("cli", executable, cli_name,
                    [ROOT / "LICENSE"] + runtime_resources(platform_name))
        if args.flavor in {"all", "gui"}:
            package(
                "gui", gui_executable, gui_name,
                gui_resources(platform_name) + runtime_resources(platform_name),
                application_root=gui_application_root,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
