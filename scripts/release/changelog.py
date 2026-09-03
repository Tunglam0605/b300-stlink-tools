"""Extract deterministic GitHub Release notes for one B300 version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from b300_core.release_notes import extract_release_notes


def _release_notes(version: str, changelog: Path) -> str:
    """Read CHANGELOG first, then an exact per-version release note file.

    The fallback keeps historical CHANGELOG extraction intact while allowing a large
    engineering release to carry a focused, immutable note file at
    ``docs/releases/<version>.md``. A different version can never accidentally reuse
    those notes.
    """
    try:
        return extract_release_notes(changelog.read_text(encoding="utf-8"), version)
    except ValueError as changelog_error:
        dedicated = Path("docs") / "releases" / (version + ".md")
        if not dedicated.is_file():
            raise changelog_error
        notes = dedicated.read_text(encoding="utf-8").strip()
        if not notes:
            raise ValueError("release notes file is empty: %s" % dedicated)
        return notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        notes = _release_notes(args.version, args.changelog)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.output is None:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(notes)
    else:
        args.output.write_bytes((notes + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
