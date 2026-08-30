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


class HardExitTextResult(unittest.TextTestResult):
    """Report the module result, then exit before Qt/PySide native teardown."""

    def stopTestRun(self) -> None:
        super().stopTestRun()
        self.printErrors()
        self.stream.writeln(self.separator2)
        self.stream.writeln("Ran %d tests" % self.testsRun)
        self.stream.writeln()
        if self.wasSuccessful():
            status = "OK"
            if self.skipped:
                status += " (skipped=%d)" % len(self.skipped)
            exit_code = 0
        else:
            parts = []
            if self.failures:
                parts.append("failures=%d" % len(self.failures))
            if self.errors:
                parts.append("errors=%d" % len(self.errors))
            if self.skipped:
                parts.append("skipped=%d" % len(self.skipped))
            status = "FAILED" + (" (%s)" % ", ".join(parts) if parts else "")
            exit_code = 1
        self.stream.writeln(status)
        try:
            self.stream.flush()
        except Exception:
            pass
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)


def run_and_exit(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Run one unittest module in an isolated process."
    )
    parser.add_argument("module", help="Dotted unittest module name, e.g. tests.test_gui_smoke")
    args = parser.parse_args(argv)

    suite = unittest.defaultTestLoader.loadTestsFromName(args.module)
    # Retain all nested test cases until HardExitTextResult.stopTestRun().
    pending = [suite]
    while pending:
        current = pending.pop()
        if isinstance(current, unittest.TestSuite):
            current._cleanup = False
            pending.extend(test for test in current if isinstance(test, unittest.TestSuite))

    unittest.TextTestRunner(verbosity=1, resultclass=HardExitTextResult).run(suite)
    raise RuntimeError("isolated unittest runner returned unexpectedly")


if __name__ == "__main__":
    run_and_exit()
