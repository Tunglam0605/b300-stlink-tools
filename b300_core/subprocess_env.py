"""Safe environment for external OS commands launched by frozen B300 runtimes."""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional


def external_command_env(
        environ: Optional[Mapping[str, str]] = None, *, frozen: Optional[bool] = None,
) -> dict[str, str]:
    """Restore the host dynamic-loader environment before launching system tools.

    PyInstaller adjusts ``LD_LIBRARY_PATH`` for the frozen process. Child system
    binaries such as ``systemctl`` must not inherit B300's bundled-library path,
    otherwise they can load incompatible libraries and report false failures.
    """
    env = dict(os.environ if environ is None else environ)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if os.name != "nt" and is_frozen:
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


__all__ = ["external_command_env"]
