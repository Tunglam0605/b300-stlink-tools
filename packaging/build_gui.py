#!/usr/bin/env python3
"""Stage Ubuntu AppImage/DEB layouts from a native B300 bundle directory."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SOURCE = ROOT / "packaging" / "linux" / "b300-stlink-gui.desktop"
ICON_SOURCE = ROOT / "packaging" / "linux" / "b300-stlink-gui.svg"


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | 0o111)


def validate_bundle(bundle: Path) -> None:
    required = (
        bundle / "b300-stlink",
        bundle / "b300-stlink-gui",
        bundle / "vendor" / "openocd" / "bin" / "openocd",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("Native Linux bundle is incomplete: %s" % ", ".join(missing))


def stage_linux_appdir(bundle: Path, output: Path, architecture: str) -> Path:
    bundle = Path(bundle).resolve()
    validate_bundle(bundle)
    appdir = Path(output).resolve() / ("B300-STLink-%s.AppDir" % architecture)
    if appdir.exists():
        shutil.rmtree(appdir)
    tool_root = appdir / "usr" / "lib" / "b300-stlink"
    shutil.copytree(bundle, tool_root)

    write_executable(appdir / "AppRun", """#!/bin/sh
set -eu
appdir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export B300_OPENOCD="$appdir/usr/lib/b300-stlink/vendor/openocd/bin/openocd"
exec "$appdir/usr/lib/b300-stlink/b300-stlink-gui" "$@"
""")
    write_executable(appdir / "usr" / "bin" / "b300-stlink-gui", """#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../lib/b300-stlink" && pwd)
export B300_OPENOCD="$root/vendor/openocd/bin/openocd"
exec "$root/b300-stlink-gui" "$@"
""")
    write_executable(appdir / "usr" / "bin" / "b300-stlink", """#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../lib/b300-stlink" && pwd)
export B300_OPENOCD="$root/vendor/openocd/bin/openocd"
exec "$root/b300-stlink" "$@"
""")
    shutil.copy2(DESKTOP_SOURCE, appdir / "b300-stlink-gui.desktop")
    shutil.copy2(ICON_SOURCE, appdir / "b300-stlink-gui.svg")
    desktop_dir = appdir / "usr" / "share" / "applications"
    icon_dir = appdir / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    desktop_dir.mkdir(parents=True)
    icon_dir.mkdir(parents=True)
    shutil.copy2(DESKTOP_SOURCE, desktop_dir / DESKTOP_SOURCE.name)
    shutil.copy2(ICON_SOURCE, icon_dir / ICON_SOURCE.name)
    return appdir


def stage_deb_root(bundle: Path, output: Path, architecture: str, version: str) -> Path:
    bundle = Path(bundle).resolve()
    validate_bundle(bundle)
    debroot = Path(output).resolve() / ("b300-stlink-gui-deb-%s" % architecture)
    if debroot.exists():
        shutil.rmtree(debroot)
    tool_root = debroot / "opt" / "b300-stlink"
    shutil.copytree(bundle, tool_root)
    control = debroot / "DEBIAN" / "control"
    control.parent.mkdir(parents=True)
    control.write_text(
        "Package: b300-stlink-gui\n"
        "Version: %s\n"
        "Architecture: %s\n"
        "Maintainer: TungLamAutomation\n"
        "Section: devel\n"
        "Priority: optional\n"
        "Description: Safe B300 STM32F407 ST-Link provisioning GUI and CLI\n" %
        (version, architecture),
        encoding="utf-8",
        newline="\n",
    )
    write_executable(debroot / "usr" / "local" / "bin" / "b300-stlink-gui", """#!/bin/sh
set -eu
export B300_OPENOCD="/opt/b300-stlink/vendor/openocd/bin/openocd"
exec /opt/b300-stlink/b300-stlink-gui "$@"
""")
    write_executable(debroot / "usr" / "local" / "bin" / "b300-stlink", """#!/bin/sh
set -eu
export B300_OPENOCD="/opt/b300-stlink/vendor/openocd/bin/openocd"
exec /opt/b300-stlink/b300-stlink "$@"
""")
    desktop_dir = debroot / "usr" / "share" / "applications"
    icon_dir = debroot / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    desktop_dir.mkdir(parents=True)
    icon_dir.mkdir(parents=True)
    shutil.copy2(DESKTOP_SOURCE, desktop_dir / DESKTOP_SOURCE.name)
    shutil.copy2(ICON_SOURCE, icon_dir / ICON_SOURCE.name)
    return debroot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--architecture", choices=("x86_64", "aarch64"), required=True)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--appimagetool", type=Path)
    parser.add_argument("--build-deb", action="store_true")
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    appdir = stage_linux_appdir(args.bundle_dir, args.output_dir, args.architecture)
    if args.appimagetool:
        if not args.appimagetool.is_file():
            parser.error("--appimagetool does not exist")
        environment = os.environ.copy()
        environment["ARCH"] = args.architecture
        appimage = args.output_dir / ("B300-STLink-GUI-%s.AppImage" % args.architecture)
        subprocess.check_call([str(args.appimagetool), str(appdir), str(appimage)], env=environment)

    deb_arch = "amd64" if args.architecture == "x86_64" else "arm64"
    debroot = stage_deb_root(args.bundle_dir, args.output_dir, deb_arch, args.version)
    if args.build_deb:
        dpkg = shutil.which("dpkg-deb")
        if not dpkg:
            parser.error("dpkg-deb is required for --build-deb")
        subprocess.check_call([
            dpkg, "--build", "--root-owner-group", str(debroot),
            str(args.output_dir / ("b300-stlink-gui_%s_%s.deb" % (args.version, deb_arch))),
        ])
    print("AppDir: %s" % appdir)
    print("DEB root: %s" % debroot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
