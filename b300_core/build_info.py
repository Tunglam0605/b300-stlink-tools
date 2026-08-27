"""Resolve the immutable source commit embedded in a release build."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def build_commit() -> str:
    value = os.environ.get("B300_BUILD_COMMIT", "").strip().lower()
    if not value:
        if getattr(sys, "frozen", False):
            root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
            path = root / "BUILD-COMMIT.txt"
            if path.is_file():
                value = path.read_text(encoding="ascii").strip().lower()
        else:
            try:
                value = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=Path(__file__).resolve().parents[1],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    text=True,
                ).strip().lower()
            except (OSError, subprocess.SubprocessError):
                value = ""
    return value[:12] if COMMIT_RE.fullmatch(value) else "source"
