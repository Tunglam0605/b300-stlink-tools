"""Validate a release tag against the source version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .version_tools import parse_semver, read_source_version


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-tag", required=True, metavar="vMAJOR.MINOR.PATCH")
    parser.add_argument(
        "--source", type=Path,
        default=Path(__file__).resolve().parents[2] / "b300_version.py",
    )
    args = parser.parse_args(argv)
    if not args.check_tag.startswith("v"):
        parser.error("release tag must start with v")
    tag_version = args.check_tag[1:]
    try:
        parse_semver(tag_version)
        source_version = read_source_version(args.source)
    except ValueError as error:
        parser.error(str(error))
    if tag_version != source_version:
        parser.error(
            "release tag version %s does not match source version %s" %
            (tag_version, source_version)
        )
    print(source_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
