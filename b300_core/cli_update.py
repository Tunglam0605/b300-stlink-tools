"""CLI-only signed updater construction and per-user cache selection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from .update_platform import CliUpdatePlatform, detect_cli_update_platform
from .update_public_key import MINISIGN_PUBLIC_KEY
from .updater import UpdateClient


@dataclass(frozen=True)
class CliUpdateRuntime:
    platform: CliUpdatePlatform
    client: UpdateClient


def build_cli_update_runtime(
        *, system: Optional[str] = None, machine: Optional[str] = None,
        public_key: str = MINISIGN_PUBLIC_KEY,
        open_url: Optional[Callable] = None) -> CliUpdateRuntime:
    """Build the existing verified updater with the CLI trust/platform identity."""
    selected = detect_cli_update_platform(system, machine)
    kwargs = {}
    if open_url is not None:
        kwargs["open_url"] = open_url
    client = UpdateClient(public_key, selected.value, **kwargs)
    return CliUpdateRuntime(selected, client)


def default_cli_update_cache(
        platform_name: str, *, environ: Optional[Mapping[str, str]] = None,
        home: Optional[Path] = None) -> Path:
    """Return the standard cache directory without creating or mutating it."""
    key = str(getattr(platform_name, "value", platform_name))
    selected_environ = os.environ if environ is None else environ
    selected_home = Path.home() if home is None else Path(home)
    if key == CliUpdatePlatform.WINDOWS_X64.value:
        local_app_data = selected_environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else selected_home / "AppData" / "Local"
        return root / "B300-STLink" / "updates"
    if key in {
            CliUpdatePlatform.LINUX_X64.value,
            CliUpdatePlatform.LINUX_ARM64.value,
    }:
        xdg_cache = selected_environ.get("XDG_CACHE_HOME")
        root = Path(xdg_cache) if xdg_cache else selected_home / ".cache"
        return root / "b300-stlink" / "updates"
    raise RuntimeError("Unsupported CLI update cache platform: %s" % key)
