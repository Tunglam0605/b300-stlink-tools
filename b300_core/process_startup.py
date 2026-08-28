"""Cross-platform policy for non-interactive backend child processes."""

from __future__ import annotations

import subprocess
from typing import Dict, Optional


def child_process_kwargs(platform_name: Optional[str] = None) -> Dict[str, object]:
    """Return only the platform-specific kwargs needed to hide Windows children."""
    selected = (platform_name or __import__("platform").system()).lower()
    if selected not in {"windows", "win32", "nt"}:
        return {}
    kwargs: Dict[str, object] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    startup_info_factory = getattr(subprocess, "STARTUPINFO", None)
    if startup_info_factory is not None:
        startupinfo = startup_info_factory()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs
