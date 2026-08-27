"""Update the B300 source version without Git side effects."""

from __future__ import annotations

import argparse
from pathlib import Path

from .version_tools import replace_source_version


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", metavar="MAJOR.MINOR.PATCH")
    parser.add_argument(
        "--source", type=Path,
        default=Path(__file__).resolve().parents[2] / "b300_version.py",
    )
    args = parser.parse_args(argv)
    try:
        replace_source_version(args.source, args.version)
    except ValueError as error:
        parser.error(str(error))
    print("Updated %s to %s" % (args.source, args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
