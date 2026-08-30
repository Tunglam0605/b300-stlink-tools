"""Run one unittest module and exit before native GUI runtime teardown."""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one unittest module in an isolated process."
    )
    parser.add_argument("module", help="Dotted unittest module name, e.g. tests.test_gui_smoke")
    args = parser.parse_args(argv)

    suite = unittest.defaultTestLoader.loadTestsFromName(args.module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    exit_code = main()
    # PySide/Qt can fail during interpreter finalization after unittest already
    # reported OK on hosted runners. Flush useful output, then terminate this
    # intentionally isolated module process without running native destructors.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
