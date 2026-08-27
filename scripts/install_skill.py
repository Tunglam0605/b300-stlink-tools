#!/usr/bin/env python3
"""Install the portable B300 Agent Skill into a chosen Agent Skills directory."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / ".agents/skills/b300-ota-stlink"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path,
                        help="Skills root, for example ~/.agents/skills.")
    parser.add_argument("--force", action="store_true",
                        help="Replace an existing b300-ota-stlink skill.")
    args = parser.parse_args(argv)
    target = args.destination.expanduser() / SOURCE.name
    if target.exists() and not args.force:
        print("Skill already exists: %s (use --force to replace it)" % target, file=sys.stderr)
        return 1
    args.destination.expanduser().mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target)
    print("Installed B300 skill: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
