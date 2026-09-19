#!/usr/bin/env python3
"""Fail CI when known repository-growth anti-patterns are introduced.

This gate is intentionally conservative: it blocks new version-layer GUI modules
and committed transient SDD reports while allowing only the explicitly documented
production versioned entry point during v0.24 consolidation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ALLOWED_VERSIONED_GUI = {
    # Production entry point pending canonical rename in a later consolidation slice.
    "main_window_v18.py",
}

VERSIONED_GUI_RE = re.compile(r".+_v\d+\.py$")


def _versioned_gui_violations() -> list[str]:
    gui_dir = ROOT / "b300_gui"
    violations: list[str] = []
    for path in sorted(gui_dir.glob("*.py")):
        if VERSIONED_GUI_RE.fullmatch(path.name) and path.name not in ALLOWED_VERSIONED_GUI:
            violations.append(
                f"{path.relative_to(ROOT)}: new version-layer GUI module; extend canonical modules instead"
            )
    return violations


def _transient_report_violations() -> list[str]:
    transient = ROOT / ".superpowers" / "sdd"
    if not transient.exists():
        return []

    violations: list[str] = []
    for path in sorted(p for p in transient.rglob("*") if p.is_file()):
        violations.append(
            f"{path.relative_to(ROOT)}: transient SDD report must stay out of Git"
        )
    return violations


def main() -> int:
    violations = _versioned_gui_violations() + _transient_report_violations()
    if violations:
        print("Repository hygiene check FAILED:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print(
        "Repository hygiene check PASS "
        f"(legacy GUI allowlist: {len(ALLOWED_VERSIONED_GUI)} files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
