"""Extract deterministic GitHub Release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .version_tools import parse_semver


HEADING_RE = re.compile(r"(?m)^## \[([^\]]+)\](?: - [^\r\n]+)?\s*$")


def extract_release_notes(text: str, version: str) -> str:
    parse_semver(version)
    headings = list(HEADING_RE.finditer(text))
    matches = [index for index, match in enumerate(headings) if match.group(1) == version]
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        notes = extract_release_notes(
            args.changelog.read_text(encoding="utf-8"), args.version
        )
    except ValueError as error:
        parser.error(str(error))
    if args.output is None:
        print(notes)
    else:
        args.output.write_text(notes + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
