"""Strict semantic versions used by the updater."""

from __future__ import annotations

import re
from dataclasses import dataclass


SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = SEMVER_RE.fullmatch(value)
        if match is None:
            raise ValueError("Version must use canonical MAJOR.MINOR.PATCH SemVer.")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return "%d.%d.%d" % (self.major, self.minor, self.patch)
