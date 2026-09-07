#!/usr/bin/env python3
"""PyInstaller-safe B300 GUI entry point."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Optional


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _numeric_version(value: str) -> Optional[tuple[int, int, int]]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _bundle_version(root: Path) -> Optional[str]:
    try:
        values = {}
        for line in (root / "BUNDLE-METADATA.txt").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
        version = values.get("version", "")
        if values.get("platform") != "windows-x64" or values.get("flavor") != "gui":
            return None
        return version if _numeric_version(version) is not None else None
    except (OSError, UnicodeError):
        return None


def find_canonical_gui_redirect(
        executable: Path, *, local_app_data: Optional[Path] = None,
        temporary_root: Optional[Path] = None, frozen: Optional[bool] = None,
        current_version: Optional[str] = None,
        environment: Mapping[str, str] = os.environ) -> Optional[Path]:
    """Return a verified installed GUI when a frozen copy was opened from Temp."""
    is_frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
    current = Path(executable)
    temp = Path(temporary_root or tempfile.gettempdir())
    if not is_frozen or current.name.lower() != "b300-stlink-gui.exe" or not _inside(current, temp):
        return None
    app_data_value = local_app_data if local_app_data is not None else environment.get("LOCALAPPDATA")
    if not app_data_value:
        return None
    app_data = Path(app_data_value)
    canonical_root = app_data / "B300-STLink"
    canonical = canonical_root / "b300-stlink-gui.exe"
    installed_version = _bundle_version(canonical_root)
    if installed_version is None or not canonical.is_file():
        return None
    if current_version is None:
        from b300_version import __version__
        current_version = __version__
    selected_current = _numeric_version(current_version)
    selected_installed = _numeric_version(installed_version)
    if selected_current is None or selected_installed is None or selected_installed < selected_current:
        return None
    try:
        from b300_core.runtime_integrity import validate_runtime
        validate_runtime(canonical_root, installed_version)
    except (OSError, ValueError):
        return None
    return canonical


def _native_core_selftest() -> int:
    from b300_core.native_debug_core import NativeDebugCoreAdapter

    adapter = NativeDebugCoreAdapter(mode="on")
    result = adapter.decode_fixed_width(
        b"\x01\x00\x00\x00\x02\x00\x00\x00",
        channel=7,
        timestamp_ns=123,
        source_id=9,
    )
    values = [event.value for event in result.events]
    if adapter.backend != "native" or result.consumed != 8 or values != [1, 2]:
        raise RuntimeError("packaged native debug-core self-test failed")
    print("B300 NATIVE DEBUG CORE: OK · ABI v1")
    return 0


def main(argv=None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if selected and selected[0] == "--apply-verified-update":
        from b300_core.update_helper import main as update_helper_main
        return update_helper_main(selected[1:])
    if selected == ["--native-core-selftest"]:
        return _native_core_selftest()
    if "--smoke-test" not in selected:
        redirect = find_canonical_gui_redirect(Path(sys.executable))
        if redirect is not None:
            from b300_core.process_startup import child_process_kwargs
            subprocess.Popen(
                (str(redirect), *selected), shell=False,
                **child_process_kwargs("windows"),
            )
            return 0
    from b300_gui.__main__ import main as gui_main
    return gui_main(selected)


if __name__ == "__main__":
    raise SystemExit(main())
