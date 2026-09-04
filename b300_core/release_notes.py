"""Read one version's release notes for source and packaged runtimes."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .versioning import SemVer


HEADING_RE = re.compile(r"(?m)^## \[([^\]]+)\](?: - [^\r\n]+)?\s*$")

# Keep a compact runtime fallback for releases whose full engineering notes live in
# docs/releases/<version>.md. This dictionary is Python code, so PyInstaller always
# bundles it; What's New does not depend on an external docs directory being present.
BUNDLED_RELEASE_NOTES = {
    "0.15.0": """Engineering Debug Workstation

- Mode-first LOCAL / GATEWAY / CLIENT entry with a compact debugger-oriented workspace.
- Structured Symbols, Source, Locals/Watch, Call Stack, Breakpoints, Registers, Live, Memory, Console, and Technical Log panes.
- Expandable GDB/MI variables for structs/arrays, HALT-only value editing with readback, Step Out, persistent BP/WP management, source navigation, and coherent HALT snapshots.
- Read-only HALT Memory view while Live Monitor remains zero-halt and read-only.

One-login Client SSH

- Client authenticates once inside B300; normal v0.15 GUI no longer opens a CMD/PowerShell SSH password window.
- One embedded SSH session is reused by GDB, Safe TCL, Interactive Debug, and Client Live Monitor.
- Password text is never stored in profile/log/command-line/status output; optional remembered credentials stay local and encrypted at rest.
- GDB/TCL forwards remain loopback-only.

Safety

- Flash/OTA/Bootloader/metadata safety policy is unchanged: no mass erase, no RDP changes, no normal writes to Bootloader Sector 0-2, no arbitrary debugger memory writes.
""".strip(),
    "0.15.1": """Unified remote Debug workflow

- Studio Debug is now the single place to choose LOCAL, GATEWAY, or CLIENT.
- The separate top-level SSH workflow is removed from production navigation.
- Gateway host preparation remains available from GATEWAY as an internal infrastructure page.
- CLIENT has one visible SSH login entry; Gateway/User/Password/Port are not duplicated elsewhere in the production flow.
- Legacy public-key authorization is hidden from the normal production workflow.

Clearer engineering roles

- LOCAL means ST-Link and Debug run on this workstation.
- GATEWAY means this workstation owns ST-Link/OpenOCD and serves remote Debug.
- CLIENT means this workstation debugs STM32 through the authenticated Gateway session.

Safety

- Flash, OTA, Bootloader, metadata, Option Bytes, protected flash boundaries, loopback-only GDB/TCL, and zero-halt Live Monitor behavior are unchanged.
""".strip(),
}


def extract_release_notes(text: str, version: str) -> str:
    SemVer.parse(version)
    headings = list(HEADING_RE.finditer(text))
    matches = [index for index, match in enumerate(headings)
               if match.group(1) == version]
    if not matches:
        raise ValueError("CHANGELOG release %s was not found." % version)
    if len(matches) != 1:
        raise ValueError("CHANGELOG release %s appears more than once." % version)
    index = matches[0]
    start = headings[index].end()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    notes = text[start:end].strip()
    if not notes:
        raise ValueError("CHANGELOG release %s is empty." % version)
    return notes


def bundled_changelog_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        root = Path(__file__).resolve().parents[1]
    return root / "CHANGELOG.md"


def current_release_notes(version: str) -> str:
    SemVer.parse(version)
    try:
        return extract_release_notes(
            bundled_changelog_path().read_text(encoding="utf-8"), version
        )
    except (OSError, ValueError):
        notes = BUNDLED_RELEASE_NOTES.get(version)
        if notes:
            return notes
        raise
