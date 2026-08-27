"""Resolve bundled and source-tree B300 branding assets."""

from __future__ import annotations

import sys
from pathlib import Path


def asset_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        root = Path(__file__).resolve().parents[1]
    return root / "branding" / name
