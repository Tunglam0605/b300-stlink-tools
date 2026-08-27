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
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


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


def fetch(url: str, output: Path) -> None:
    with urllib.request.urlopen(url) as source, output.open("wb") as destination:
        shutil.copyfileobj(source, destination)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release")
    parser.add_argument("--internal-distribution-approved", action="store_true")
    parser.add_argument("--cli-only", action="store_true",
                        help="Build the legacy CLI-only archive without the GUI executable.")
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
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum.read_text().split()[0].lower():
            raise RuntimeError("OpenOCD checksum mismatch.")
        unpack = temp / "openocd"
        if extension == ".zip":
            with zipfile.ZipFile(archive) as content: content.extractall(unpack)
        else:
            with tarfile.open(archive) as content: content.extractall(unpack)
        openocd_root = next(item for item in unpack.iterdir() if item.is_dir())
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user",
                               "-r", str(ROOT / "requirements-build.txt")])
        subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
                               "--name", "b300-stlink", "--distpath", str(args.output_dir),
                               str(ROOT / "b300_stlink.py")])
        gui_executable = "b300-stlink-gui.exe" if platform_name == "windows-x64" else "b300-stlink-gui"
        if not args.cli_only:
            subprocess.check_call([
                sys.executable, "-m", "PyInstaller", "--noconfirm",
                "--distpath", str(args.output_dir),
                "--workpath", str(ROOT / "build" / "b300-stlink-gui"),
                str(ROOT / "b300_gui.spec"),
            ])
        package_command = [
            sys.executable, str(ROOT / "package_internal.py"),
            "--executable", str(args.output_dir / executable),
            "--openocd-root", str(openocd_root), "--bootstrap", str(ROOT / installer),
            "--output", str(args.output_dir / ("b300-stlink-%s%s" % (platform_name, extension))),
            "--platform", platform_name, "--internal-distribution-approved",
            "--resource", str(ROOT / "LICENSE"),
            "--resource", str(ROOT / "packaging" / "linux" / "b300-stlink-gui.desktop"),
            "--resource", str(ROOT / "packaging" / "linux" / "b300-stlink-gui.svg"),
        ]
        if not args.cli_only:
            package_command.extend(["--gui-executable", str(args.output_dir / gui_executable)])
        subprocess.check_call(package_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
