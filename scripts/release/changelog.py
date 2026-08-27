"""Extract deterministic GitHub Release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
from pathlib import Path

from b300_core.release_notes import extract_release_notes


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
        args.output.write_bytes((notes + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
