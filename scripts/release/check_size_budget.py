#!/usr/bin/env python3
"""Fail release packaging when a base artifact exceeds its declared size budget."""
from __future__ import annotations

import argparse
from pathlib import Path

MIB = 1024 * 1024


def check_size(path: Path, max_mib: float) -> tuple[int, float]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError("Artifact does not exist: %s" % source)
    if max_mib <= 0:
        raise ValueError("Size budget must be positive.")
    size = source.stat().st_size
    actual_mib = size / MIB
    if actual_mib > max_mib:
        raise RuntimeError(
            "Artifact size regression: %s is %.2f MiB; budget is %.2f MiB." %
            (source.name, actual_mib, max_mib)
        )
    return size, actual_mib


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--max-mib", required=True, type=float)
    args = parser.parse_args(argv)
    _size, actual = check_size(args.artifact, args.max_mib)
    print("SIZE_OK %s %.2f MiB <= %.2f MiB" %
          (args.artifact.name, actual, args.max_mib))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
