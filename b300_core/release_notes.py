"""Read one version's notes from the project changelog."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .versioning import SemVer


HEADING_RE = re.compile(r"(?m)^## \[([^\]]+)\](?: - [^\r\n]+)?\s*$")


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
    return extract_release_notes(
        bundled_changelog_path().read_text(encoding="utf-8"), version
    )
