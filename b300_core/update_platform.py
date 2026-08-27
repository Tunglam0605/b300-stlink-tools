"""Normalize executable environment to the signed update platform keys."""

from __future__ import annotations

import os
import platform
from enum import Enum
from pathlib import Path
from typing import Optional


class UpdatePlatform(str, Enum):
    WINDOWS_X64 = "windows-x64"
    LINUX_X64_APPIMAGE = "linux-x64-appimage"
    LINUX_X64_DEB = "linux-x64-deb"
    LINUX_ARM64_APPIMAGE = "linux-arm64-appimage"
    LINUX_ARM64_DEB = "linux-arm64-deb"


def detect_update_platform(
        executable: Path, system: Optional[str] = None,
        machine: Optional[str] = None) -> UpdatePlatform:
    selected_system = (system or platform.system()).lower()
    selected_machine = (machine or platform.machine()).lower()
    if selected_system == "windows" and selected_machine in {"amd64", "x86_64"}:
        return UpdatePlatform.WINDOWS_X64
    if selected_system == "linux":
        is_appimage = (
            str(executable).lower().endswith(".appimage") or bool(os.environ.get("APPIMAGE"))
        )
        if selected_machine in {"amd64", "x86_64"}:
            return (
                UpdatePlatform.LINUX_X64_APPIMAGE if is_appimage
                else UpdatePlatform.LINUX_X64_DEB
            )
        if selected_machine in {"arm64", "aarch64"}:
            return (
                UpdatePlatform.LINUX_ARM64_APPIMAGE if is_appimage
                else UpdatePlatform.LINUX_ARM64_DEB
            )
    raise RuntimeError(
        "Unsupported update platform: %s/%s" % (selected_system, selected_machine)
    )
