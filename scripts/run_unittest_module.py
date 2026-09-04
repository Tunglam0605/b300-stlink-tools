"""Run one unittest module and exit before native GUI runtime teardown."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class HardExitTextResult(unittest.TextTestResult):
    """Report the module result, then exit before Qt/PySide native teardown."""

    def stopTestRun(self) -> None:
        # Record the unittest verdict before any Qt/PySide finalization can crash
        # the hosted process. CI may accept a non-zero native teardown exit only
        # when this sentinel proves every unittest assertion already passed.
        successful = self.wasSuccessful()
        exit_code = 0 if successful else 1
        result_file = os.environ.get("B300_UNITTEST_RESULT_FILE", "").strip()
        if result_file:
            path = Path(result_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PASS\n" if successful else "FAIL\n", encoding="ascii")

        self.printErrors()
        self.stream.writeln(self.separator2)
        self.stream.writeln("Ran %d tests" % self.testsRun)
        self.stream.writeln()
        if successful:
            status = "OK"
            if self.skipped:
                status += " (skipped=%d)" % len(self.skipped)
        else:
            parts = []
            if self.failures:
                parts.append("failures=%d" % len(self.failures))
            if self.errors:
                parts.append("errors=%d" % len(self.errors))
            if self.skipped:
                parts.append("skipped=%d" % len(self.skipped))
            status = "FAILED" + (" (%s)" % ", ".join(parts) if parts else "")
        self.stream.writeln(status)
        try:
            self.stream.flush()
        except Exception:
            pass
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)


def iter_test_cases(suite: unittest.TestSuite):
    """Yield leaf test cases without retaining an enclosing Qt test suite."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_test_cases(item)
        else:
            yield item


def write_split_verdict(successful: bool) -> None:
    """Record one aggregate result instead of leaking an earlier child verdict."""
    result_file = os.environ.get("B300_UNITTEST_RESULT_FILE", "").strip()
    if not result_file:
        return
    path = Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PASS\n" if successful else "FAIL\n", encoding="ascii")


def _read_verdict(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def run_split_cases(module: str, *, case_timeout: int | None = None) -> int:
    """Run every test case in a new interpreter to bound native Qt state.

    Every child writes its unittest verdict before native Qt teardown. This matters
    on Windows hosted runners where a process can occasionally return a native
    non-zero code after all Python assertions have already passed. A child is only
    accepted in that situation when its private PASS sentinel exists; a missing or
    FAIL sentinel always fails the split suite.
    """
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    cases = tuple(iter_test_cases(suite))
    if not cases:
        print("No tests found in %s" % module, file=sys.stderr)
        write_split_verdict(False)
        return 1

    base_env = dict(os.environ)
    base_env.pop("B300_UNITTEST_RESULT_FILE", None)
    with tempfile.TemporaryDirectory(prefix="b300-unittest-cases-") as temp_root:
        result_root = Path(temp_root)
        for index, case in enumerate(cases):
            print("=== %s ===" % case.id(), flush=True)
            child_result = result_root / ("case-%04d.txt" % index)
            child_env = dict(base_env)
            child_env["B300_UNITTEST_RESULT_FILE"] = str(child_result)
            try:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), case.id()],
                    cwd=ROOT,
                    env=child_env,
                    timeout=case_timeout,
                )
            except subprocess.TimeoutExpired:
                print(
                    "Timed out after %ss: %s" % (case_timeout, case.id()),
                    file=sys.stderr,
                )
                write_split_verdict(False)
                return 124

            verdict = _read_verdict(child_result)
            if result.returncode and verdict == "PASS":
                print(
                    "WARNING: %s passed assertions but native Qt teardown returned %s; "
                    "accepting verified child PASS sentinel." % (case.id(), result.returncode),
                    file=sys.stderr,
                    flush=True,
                )
                continue
            if result.returncode:
                write_split_verdict(False)
                return result.returncode
            if verdict != "PASS":
                print(
                    "Child exited without a verified PASS sentinel: %s" % case.id(),
                    file=sys.stderr,
                )
                write_split_verdict(False)
                return 1

    write_split_verdict(True)
    return 0


def run_and_exit(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one unittest module in an isolated process."
    )
    parser.add_argument(
        "--split-cases",
        action="store_true",
        help="run each discovered test case in its own child interpreter",
    )
    parser.add_argument(
        "--case-timeout",
        type=int,
        help="maximum seconds for each child test case (requires --split-cases)",
    )
    parser.add_argument("module", help="Dotted unittest module name, e.g. tests.test_gui_smoke")
    args = parser.parse_args(argv)

    if args.case_timeout is not None and args.case_timeout <= 0:
        parser.error("--case-timeout must be positive")
    if args.case_timeout is not None and not args.split_cases:
        parser.error("--case-timeout requires --split-cases")
    if args.split_cases:
        return run_split_cases(args.module, case_timeout=args.case_timeout)

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
    sys.exit(run_and_exit())
