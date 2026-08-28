#!/usr/bin/env python3
"""Stage Ubuntu AppImage/DEB layouts from a native B300 bundle directory."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from b300_version import __version__ as TOOL_VERSION

DESKTOP_SOURCE = ROOT / "packaging" / "linux" / "b300-stlink-gui.desktop"
ICON_SOURCE = ROOT / "branding" / "b300-stlink-icon.png"

B300_UDEV_RULE = (
    'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="374?", '
    'MODE="0660", GROUP="plugdev", TAG+="uaccess"\n'
)


def gui_output_names(architecture: str):
    if architecture == "x86_64":
        return "B300-STLink-GUI-Ubuntu-x64.AppImage", "b300-stlink-gui_amd64.deb"
    if architecture == "aarch64":
        return "B300-STLink-GUI-Ubuntu-arm64.AppImage", "b300-stlink-gui_arm64.deb"
    raise ValueError("Unsupported GUI architecture: %s" % architecture)


def write_text_lf(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def write_executable(path: Path, content: str) -> None:
    write_text_lf(path, content)
    path.chmod(path.stat().st_mode | 0o111)


def validate_bundle(bundle: Path) -> None:
    required = (
        bundle / "b300-stlink-gui",
        bundle / "vendor" / "openocd" / "bin" / "openocd",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("Native Linux bundle is incomplete: %s" % ", ".join(missing))


def ensure_runtime_executables(tool_root: Path) -> None:
    for relative in (
            Path("b300-stlink-gui"),
            Path("vendor") / "openocd" / "bin" / "openocd"):
        path = Path(tool_root) / relative
        if not path.is_file():
            raise ValueError("Linux runtime executable is missing: %s" % path)
        path.chmod(path.stat().st_mode | 0o111)


def stage_linux_appdir(bundle: Path, output: Path, architecture: str) -> Path:
    bundle = Path(bundle).resolve()
    validate_bundle(bundle)
    appdir = Path(output).resolve() / ("B300-STLink-%s.AppDir" % architecture)
    if appdir.exists():
        shutil.rmtree(appdir)
    tool_root = appdir / "usr" / "lib" / "b300-stlink"
    shutil.copytree(bundle, tool_root)
    ensure_runtime_executables(tool_root)

    write_executable(appdir / "AppRun", """#!/bin/sh
set -eu
appdir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export B300_APP_ROOT="$appdir/usr/lib/b300-stlink"
exec "$B300_APP_ROOT/b300-stlink-gui" "$@"
""")
    write_executable(appdir / "usr" / "bin" / "b300-stlink-gui", """#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../lib/b300-stlink" && pwd)
export B300_APP_ROOT="$root"
exec "$B300_APP_ROOT/b300-stlink-gui" "$@"
""")
    shutil.copy2(DESKTOP_SOURCE, appdir / "b300-stlink-gui.desktop")
    shutil.copy2(ICON_SOURCE, appdir / "b300-stlink-gui.png")
    desktop_dir = appdir / "usr" / "share" / "applications"
    icon_dir = appdir / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    desktop_dir.mkdir(parents=True)
    icon_dir.mkdir(parents=True)
    shutil.copy2(DESKTOP_SOURCE, desktop_dir / DESKTOP_SOURCE.name)
    shutil.copy2(ICON_SOURCE, icon_dir / "b300-stlink-gui.png")
    write_text_lf(
        appdir / "usr" / "share" / "b300-stlink" / "udev" / "49-b300-stlink.rules",
        B300_UDEV_RULE,
    )
    return appdir


def stage_deb_root(bundle: Path, output: Path, architecture: str, version: str) -> Path:
    bundle = Path(bundle).resolve()
    validate_bundle(bundle)
    debroot = Path(output).resolve() / ("b300-stlink-gui-deb-%s" % architecture)
    if debroot.exists():
        shutil.rmtree(debroot)
    tool_root = debroot / "opt" / "b300-stlink"
    shutil.copytree(bundle, tool_root)
    ensure_runtime_executables(tool_root)
    control = debroot / "DEBIAN" / "control"
    control.parent.mkdir(parents=True)
    write_text_lf(
        control,
        "Package: b300-stlink-gui\n"
        "Version: %s\n"
        "Architecture: %s\n"
        "Maintainer: TungLamAutomation\n"
        "Section: devel\n"
        "Priority: optional\n"
        "Depends: libdbus-1-3, libegl1, libgl1, libglib2.0-0, libx11-xcb1, "
        "libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, "
        "libxcb-render-util0, libxcb-shape0, libxcb-xkb1, libxkbcommon-x11-0\n"
        "Description: Safe B300 STM32F407 ST-Link provisioning GUI\n" %
        (version, architecture),
    )
    write_text_lf(
        debroot / "usr" / "lib" / "udev" / "rules.d" / "49-b300-stlink.rules",
        B300_UDEV_RULE,
    )
    write_executable(
        debroot / "DEBIAN" / "postinst",
        """#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=0483 || true
fi
exit 0
""",
    )
    write_executable(debroot / "usr" / "local" / "bin" / "b300-stlink-gui", """#!/bin/sh
set -eu
export B300_APP_ROOT=/opt/b300-stlink
exec "$B300_APP_ROOT/b300-stlink-gui" "$@"
""")
    desktop_dir = debroot / "usr" / "share" / "applications"
    icon_dir = debroot / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    desktop_dir.mkdir(parents=True)
    icon_dir.mkdir(parents=True)
    shutil.copy2(DESKTOP_SOURCE, desktop_dir / DESKTOP_SOURCE.name)
    shutil.copy2(ICON_SOURCE, icon_dir / "b300-stlink-gui.png")
    return debroot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--architecture", choices=("x86_64", "aarch64"), required=True)
    parser.add_argument("--version", default=TOOL_VERSION)
    parser.add_argument("--appimagetool", type=Path)
    parser.add_argument("--build-deb", action="store_true")
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    appdir = stage_linux_appdir(args.bundle_dir, args.output_dir, args.architecture)
    appimage_name, deb_name = gui_output_names(args.architecture)
    if args.appimagetool:
        if not args.appimagetool.is_file():
            parser.error("--appimagetool does not exist")
        environment = os.environ.copy()
        environment["ARCH"] = args.architecture
        appimage = args.output_dir / appimage_name
        subprocess.check_call([str(args.appimagetool), str(appdir), str(appimage)], env=environment)

    deb_arch = "amd64" if args.architecture == "x86_64" else "arm64"
    debroot = stage_deb_root(args.bundle_dir, args.output_dir, deb_arch, args.version)
    if args.build_deb:
        dpkg = shutil.which("dpkg-deb")
        if not dpkg:
            parser.error("dpkg-deb is required for --build-deb")
        subprocess.check_call([
            dpkg, "--build", "--root-owner-group", str(debroot),
            str(args.output_dir / deb_name),
        ])
    print("AppDir: %s" % appdir)
    print("DEB root: %s" % debroot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
