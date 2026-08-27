"""Strict release version parsing and source-file updates."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Tuple


SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
VERSION_ASSIGNMENT_RE = re.compile(
    r'(?m)^__version__\s*=\s*["\']([^"\']+)["\']\s*$'
)


def parse_semver(value: str) -> Tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError("Version must use canonical MAJOR.MINOR.PATCH SemVer: %r" % value)
    return tuple(int(part) for part in match.groups())


def read_source_version(path: Path) -> str:
    text = Path(path).read_text(encoding="utf-8")
    matches = VERSION_ASSIGNMENT_RE.findall(text)
    if len(matches) != 1:
        raise ValueError("Version source must contain exactly one __version__ assignment.")
    version = matches[0]
    parse_semver(version)
    return version


def replace_source_version(path: Path, version: str) -> None:
    parse_semver(version)
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    matches = list(VERSION_ASSIGNMENT_RE.finditer(text))
    if len(matches) != 1:
        raise ValueError("Version source must contain exactly one __version__ assignment.")
    match = matches[0]
    replacement = '__version__ = "%s"' % version
    updated = text[:match.start()] + replacement + text[match.end():]
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", delete=False,
            dir=str(source.parent), prefix=source.name + ".", suffix=".tmp") as handle:
        handle.write(updated)
        temporary = Path(handle.name)
    try:
        os.replace(str(temporary), str(source))
    finally:
        if temporary.exists():
            temporary.unlink()
